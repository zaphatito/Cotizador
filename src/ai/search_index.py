# src/ai/search_index.py
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import unicodedata
import zlib
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from rapidfuzz import fuzz, process


_FTS_PRODUCTS = "ai_products_fts"
_FTS_CLIENTS = "ai_clients_fts"
_SEARCH_CACHE_TABLE = "ai_search_cache"
_CACHE_KEY_FUZZY_PRODUCTS = "fuzzy_products_v2"


def _strip_accents(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def _norm_query(q: str) -> str:
    q = (q or "").strip().lower()
    q = _strip_accents(q)
    q = re.sub(r"[^0-9a-zA-Z]+", " ", q, flags=re.UNICODE)
    q = re.sub(r"\s+", " ", q).strip()
    return q


def _split_alpha_digit(s: str) -> str:
    return re.sub(r"(?<=\D)(?=\d)|(?<=\d)(?=\D)", " ", s)


_NUM_WORD_TO_DIGIT: Dict[str, str] = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10", "eleven": "11",
    "twelve": "12", "thirteen": "13", "fourteen": "14", "fifteen": "15", "sixteen": "16",
    "seventeen": "17", "eighteen": "18", "nineteen": "19", "twenty": "20",
    "cero": "0", "uno": "1", "un": "1", "dos": "2", "tres": "3", "cuatro": "4", "cinco": "5",
    "seis": "6", "siete": "7", "ocho": "8", "nueve": "9", "diez": "10", "once": "11",
    "doce": "12", "trece": "13", "catorce": "14", "quince": "15", "dieciseis": "16",
    "diecisiete": "17", "dieciocho": "18", "diecinueve": "19", "veinte": "20",
}

_DIGIT_TO_WORDS: Dict[str, List[str]] = {
    "0": ["zero", "cero"],
    "1": ["one", "uno"],
    "2": ["two", "dos"],
    "3": ["three", "tres"],
    "4": ["four", "cuatro"],
    "5": ["five", "cinco"],
    "6": ["six", "seis"],
    "7": ["seven", "siete"],
    "8": ["eight", "ocho"],
    "9": ["nine", "nueve"],
    "10": ["ten", "diez"],
    "11": ["eleven", "once"],
    "12": ["twelve", "doce"],
    "13": ["thirteen", "trece"],
    "14": ["fourteen", "catorce"],
    "15": ["fifteen", "quince"],
    "16": ["sixteen", "dieciseis"],
    "17": ["seventeen", "diecisiete"],
    "18": ["eighteen", "dieciocho"],
    "19": ["nineteen", "diecinueve"],
    "20": ["twenty", "veinte"],
}

_TOKEN_EQUIV: Dict[str, List[str]] = {
    "millon": ["million"],
    "millones": ["millions"],
    "million": ["millon"],
    "millions": ["millones"],
    "un": ["one", "uno"],
    "uno": ["one", "un"],
    "one": ["uno", "un"],
}


def _words_to_digits(s: str) -> str:
    toks = s.split()
    out = []
    for t in toks:
        out.append(_NUM_WORD_TO_DIGIT.get(t, t))
    return " ".join(out).strip()


def _digits_to_words_variants(s: str) -> List[str]:
    toks = s.split()
    idxs = [i for i, t in enumerate(toks) if t in _DIGIT_TO_WORDS]
    if not idxs:
        return []

    variants = []
    base = toks[:]
    for i in idxs:
        for w in _DIGIT_TO_WORDS[toks[i]][:2]:
            t2 = base[:]
            t2[i] = w
            variants.append(" ".join(t2).strip())
    return variants[:8]


def _token_equiv_variants(s: str) -> List[str]:
    toks = (s or "").split()
    if not toks:
        return []

    out: List[str] = []
    seen = set()
    for i, t in enumerate(toks):
        alts = _TOKEN_EQUIV.get(t, [])
        for alt in alts:
            t2 = toks[:]
            t2[i] = alt
            v = " ".join(t2).strip()
            if not v or v in seen:
                continue
            seen.add(v)
            out.append(v)
    return out[:12]


def _query_variants(q: str) -> List[str]:
    base = _norm_query(q)
    if not base:
        return []

    out: List[str] = []
    seen = set()

    def add(x: str):
        x = (x or "").strip()
        if not x or x in seen:
            return
        seen.add(x)
        out.append(x)

    add(base)

    ns = re.sub(r"\s+", "", base)
    add(ns)

    add(_split_alpha_digit(base))
    add(_split_alpha_digit(ns))

    wd = _words_to_digits(base)
    add(wd)
    add(re.sub(r"\s+", "", wd))
    add(_split_alpha_digit(wd))
    add(_split_alpha_digit(re.sub(r"\s+", "", wd)))

    for v in _digits_to_words_variants(base):
        add(v)
        add(re.sub(r"\s+", "", v))
        add(_split_alpha_digit(v))
        add(_split_alpha_digit(re.sub(r"\s+", "", v)))

    semantic_seeds: List[str] = [base, wd]
    semantic_seeds.extend(_digits_to_words_variants(base))
    for seed in semantic_seeds:
        for v in _token_equiv_variants(seed):
            add(v)
            add(re.sub(r"\s+", "", v))
            add(_split_alpha_digit(v))
            add(_split_alpha_digit(re.sub(r"\s+", "", v)))

    return out[:28]


def _fts_match_query(q: str) -> str:
    q = _norm_query(q)
    if not q:
        return ""

    parts = [p.strip() for p in re.split(r"[^\w]+", q, flags=re.UNICODE) if p.strip()]
    if not parts:
        return ""

    tokens = [f"{p}*" for p in parts if p]
    and_q = " AND ".join(tokens)

    if len(parts) == 1:
        return and_q

    concat = "".join(parts) + "*"
    return f"({and_q}) OR ({concat})"


def _has_fts5(con: sqlite3.Connection) -> bool:
    try:
        con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS __fts5_test USING fts5(x)")
        con.execute("DROP TABLE IF EXISTS __fts5_test")
        return True
    except sqlite3.OperationalError:
        return False
    except Exception:
        return False


def ensure_ai_schema(con: sqlite3.Connection) -> bool:
    if not _has_fts5(con):
        return False

    tokenize = "unicode61 remove_diacritics 2"

    con.execute(
        f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS {_FTS_PRODUCTS}
        USING fts5(
            codigo,
            nombre,
            categoria,
            genero,
            ml,
            fuente,
            tokenize='{tokenize}'
        )
        """
    )
    con.execute(
        f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS {_FTS_CLIENTS}
        USING fts5(
            cliente,
            cedula,
            telefono,
            tokenize='{tokenize}'
        )
        """
    )
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SEARCH_CACHE_TABLE} (
            key TEXT PRIMARY KEY,
            payload BLOB NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    return True


def _expand_name_for_index(nombre: str) -> str:
    raw = str(nombre or "").strip()
    base = _norm_query(raw)
    if not base:
        return raw

    tokens: List[str] = []
    seen = set()

    def add(x: str):
        x = (x or "").strip()
        if not x or x in seen:
            return
        seen.add(x)
        tokens.append(x)

    add(base)
    ns = re.sub(r"\s+", "", base)
    add(ns)
    add(_split_alpha_digit(base))
    add(_split_alpha_digit(ns))

    wd = _words_to_digits(base)
    add(wd)
    add(re.sub(r"\s+", "", wd))
    add(_split_alpha_digit(wd))
    add(_split_alpha_digit(re.sub(r"\s+", "", wd)))

    for v in _digits_to_words_variants(base):
        add(v)
        add(re.sub(r"\s+", "", v))

    for v in _token_equiv_variants(base):
        add(v)
        add(re.sub(r"\s+", "", v))

    for w in base.split():
        ww = re.sub(r"\s+", "", w)
        if len(ww) >= 6:
            if re.match(r"^[a-z]{2}\w+$", ww):
                add(ww[2:])
                add(_split_alpha_digit(ww[2:]))
            if re.match(r"^[a-z]{3}\w+$", ww):
                add(ww[3:])
                add(_split_alpha_digit(ww[3:]))

    extra = " ".join(tokens[:50])
    return f"{raw} {extra}".strip()


def rebuild_products_index(con: sqlite3.Connection) -> None:
    fts_ok = ensure_ai_schema(con)
    if not fts_ok:
        return

    con.execute(f"DELETE FROM {_FTS_PRODUCTS}")

    rows = con.execute(
        """
        SELECT
            COALESCE(id,'') AS codigo,
            COALESCE(nombre,'') AS nombre,
            COALESCE(categoria,'') AS categoria,
            COALESCE(genero,'') AS genero,
            COALESCE(ml,'') AS ml,
            COALESCE(fuente,'') AS fuente
        FROM products_current
        """
    ).fetchall()

    payload = []
    for r in rows:
        codigo = str(r["codigo"] or "")
        nombre = str(r["nombre"] or "")
        categoria = str(r["categoria"] or "")
        genero = str(r["genero"] or "")
        ml = str(r["ml"] or "")
        fuente = str(r["fuente"] or "")

        nombre_exp = _expand_name_for_index(nombre)
        payload.append((codigo, nombre_exp, categoria, genero, ml, fuente))

    con.executemany(
        f"""
        INSERT INTO {_FTS_PRODUCTS}(codigo, nombre, categoria, genero, ml, fuente)
        VALUES(?,?,?,?,?,?)
        """,
        payload,
    )


def rebuild_clients_index(con: sqlite3.Connection) -> None:
    fts_ok = ensure_ai_schema(con)
    if not fts_ok:
        return

    con.execute(f"DELETE FROM {_FTS_CLIENTS}")

    con.execute(
        f"""
        INSERT INTO {_FTS_CLIENTS}(cliente, cedula, telefono)
        SELECT
            COALESCE(t.cliente,''),
            COALESCE(t.cedula,''),
            COALESCE(t.telefono,'')
        FROM (
            SELECT
                cliente, cedula, telefono,
                MAX(created_at) AS max_created
            FROM quotes
            WHERE deleted_at IS NULL
              AND TRIM(COALESCE(cliente,'')) <> ''
            GROUP BY cliente, cedula, telefono
            ORDER BY max_created DESC
        ) t
        """
    )


def rebuild_all(con: sqlite3.Connection) -> None:
    rebuild_products_index(con)
    rebuild_clients_index(con)


def _search_products_fts(con: sqlite3.Connection, q: str, limit: int) -> List[Dict[str, Any]]:
    mq = _fts_match_query(q)
    if not mq:
        return []
    rows = con.execute(
        f"""
        SELECT
            f.codigo AS codigo,
            COALESCE(p.nombre, f.nombre) AS nombre,
            COALESCE(p.categoria, f.categoria) AS categoria,
            COALESCE(p.genero, f.genero) AS genero,
            COALESCE(p.ml, f.ml) AS ml,
            COALESCE(p.fuente, f.fuente) AS fuente,
            bm25({_FTS_PRODUCTS}) AS score
        FROM {_FTS_PRODUCTS} AS f
        LEFT JOIN products_current AS p
            ON UPPER(COALESCE(p.id, '')) = UPPER(COALESCE(f.codigo, ''))
        WHERE {_FTS_PRODUCTS} MATCH ?
        ORDER BY score
        LIMIT ?
        """,
        (mq, int(limit)),
    ).fetchall()
    return [dict(r) for r in rows]


def _search_clients_fts(con: sqlite3.Connection, q: str, limit: int) -> List[Dict[str, Any]]:
    mq = _fts_match_query(q)
    if not mq:
        return []
    rows = con.execute(
        f"""
        SELECT
            cliente, cedula, telefono,
            bm25({_FTS_CLIENTS}) AS score
        FROM {_FTS_CLIENTS}
        WHERE {_FTS_CLIENTS} MATCH ?
        ORDER BY score
        LIMIT ?
        """,
        (mq, int(limit)),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        # bm25: menor = mejor => rel = -score
        try:
            d["_rel"] = -float(d.get("score") or 0.0)
        except Exception:
            d["_rel"] = 0.0
        out.append(d)
    return out


def _search_products_like(con: sqlite3.Connection, q: str, limit: int) -> List[Dict[str, Any]]:
    qn = _norm_query(q)
    like = f"%{qn}%"
    q_compact = re.sub(r"\s+", "", qn).lower()
    like_compact = f"%{q_compact}%"

    rows = con.execute(
        """
        SELECT
            id AS codigo,
            COALESCE(nombre,'') AS nombre,
            COALESCE(categoria,'') AS categoria,
            COALESCE(genero,'') AS genero,
            COALESCE(ml,'') AS ml,
            COALESCE(fuente,'') AS fuente
        FROM products_current
        WHERE
            LOWER(id) LIKE ?
            OR LOWER(nombre) LIKE ?
            OR LOWER(categoria) LIKE ?
            OR REPLACE(LOWER(nombre), ' ', '') LIKE ?
        LIMIT ?
        """,
        (like, like, like, like_compact, int(limit)),
    ).fetchall()

    return [dict(r) for r in rows]


def _search_clients_like(con: sqlite3.Connection, q: str, limit: int) -> List[Dict[str, Any]]:
    qn = _norm_query(q)
    like = f"%{qn}%"
    like_ns = f"%{qn.replace(' ', '')}%"

    rows = con.execute(
        """
        SELECT DISTINCT
            COALESCE(cliente,'') AS cliente,
            COALESCE(cedula,'') AS cedula,
            COALESCE(telefono,'') AS telefono
        FROM quotes
        WHERE deleted_at IS NULL
          AND (
              LOWER(COALESCE(cliente,'')) LIKE ?
              OR LOWER(COALESCE(cedula,'')) LIKE ?
              OR LOWER(COALESCE(telefono,'')) LIKE ?
              OR REPLACE(LOWER(COALESCE(cliente,'')), ' ', '') LIKE ?
              OR REPLACE(LOWER(COALESCE(cedula,'')), ' ', '') LIKE ?
              OR REPLACE(LOWER(COALESCE(telefono,'')), ' ', '') LIKE ?
          )
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (like, like, like, like_ns, like_ns, like_ns, int(limit)),
    ).fetchall()

    out = []
    for r in rows:
        d = dict(r)
        d["score"] = 0.0
        d["_rel"] = 0.0
        out.append(d)
    return out


@dataclass
class _FuzzyCache:
    products: List[Tuple[str, str]]  # (codigo, texto_expandido)
    clients: List[Tuple[str, str, str]]  # (cliente, cedula, telefono)
    combos: List[Dict[str, Any]]  # rows sinteticas de codigo combinado
    presentations: List[Dict[str, Any]]  # rows de presentaciones (codigo directo)
    product_codes: set[str]
    choices: Dict[str, str]
    choices_text: Dict[str, str]
    combo_map: Dict[str, Dict[str, Any]]
    pres_map: Dict[str, Dict[str, Any]]
    pref_rows_all: List[Dict[str, Any]]
    pref_rows_by_1: Dict[str, List[Dict[str, Any]]]
    pref_rows_by_2: Dict[str, List[Dict[str, Any]]]


_GLOBAL_FUZZY_CACHE: Dict[str, _FuzzyCache] = {}
_GLOBAL_FUZZY_CACHE_LOCK = threading.Lock()


class LocalSearchIndex:
    def __init__(self, db_path: str):
        self.db_path = os.path.abspath(str(db_path))
        self._lock = threading.Lock()
        self._fts_available: Optional[bool] = None
        self._fuzzy: Optional[_FuzzyCache] = None
        self._prewarm_started = False

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("PRAGMA journal_mode = WAL")
        con.execute("PRAGMA synchronous = NORMAL")
        con.execute("PRAGMA busy_timeout = 5000")
        return con

    @staticmethod
    def _finalize_fuzzy_cache(
        *,
        products: List[Tuple[str, str]],
        clients: List[Tuple[str, str, str]],
        combos: List[Dict[str, Any]],
        presentations: List[Dict[str, Any]],
    ) -> _FuzzyCache:
        cache = _FuzzyCache(
            products=products,
            clients=clients,
            combos=combos,
            presentations=presentations,
            product_codes={str(codigo) for (codigo, _txt) in products},
            choices={},
            choices_text={},
            combo_map={},
            pres_map={},
            pref_rows_all=[],
            pref_rows_by_1={},
            pref_rows_by_2={},
        )
        cache.choices = {codigo: texto for (codigo, texto) in products}
        cache.choices_text = dict(cache.choices)

        for c in combos:
            code = str(c.get("codigo") or "").strip()
            txt = str(c.get("_text") or "").strip()
            if not code:
                continue
            cache.combo_map[code] = c
            if code not in cache.choices and txt:
                cache.choices[code] = txt

        for p in presentations:
            code = str(p.get("codigo") or "").strip()
            txt = str(p.get("_text") or "").strip()
            if not code:
                continue
            cache.pres_map[code] = p
            if code not in cache.choices_text and txt:
                cache.choices_text[code] = txt
            if code not in cache.choices and txt:
                cache.choices[code] = txt

        cache.pref_rows_all = list(combos or []) + list(presentations or [])
        for row in cache.pref_rows_all:
            code = str(row.get("codigo") or "").strip().upper()
            if not code:
                continue
            k1 = code[:1]
            k2 = code[:2]
            cache.pref_rows_by_1.setdefault(k1, []).append(row)
            if len(k2) == 2:
                cache.pref_rows_by_2.setdefault(k2, []).append(row)

        return cache

    def _load_persisted_products_cache(self, con: sqlite3.Connection) -> Optional[Dict[str, Any]]:
        try:
            row = con.execute(
                f"SELECT payload FROM {_SEARCH_CACHE_TABLE} WHERE key = ? LIMIT 1",
                (_CACHE_KEY_FUZZY_PRODUCTS,),
            ).fetchone()
            if not row:
                return None
            payload = row["payload"]
            if payload is None:
                return None
            if isinstance(payload, memoryview):
                payload = payload.tobytes()
            if not isinstance(payload, (bytes, bytearray)):
                return None
            raw = zlib.decompress(bytes(payload)).decode("utf-8", errors="strict")
            data = json.loads(raw)
            if not isinstance(data, dict):
                return None
            return data
        except Exception:
            return None

    def _save_persisted_products_cache(
        self,
        con: sqlite3.Connection,
        *,
        combos: List[Dict[str, Any]],
        presentations: List[Dict[str, Any]],
    ) -> None:
        try:
            data = {
                "combos": combos or [],
                "presentations": presentations or [],
            }
            raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            blob = zlib.compress(raw, level=6)
            con.execute(
                f"""
                INSERT INTO {_SEARCH_CACHE_TABLE}(key, payload, updated_at)
                VALUES(?, ?, datetime('now'))
                ON CONFLICT(key) DO UPDATE SET
                    payload=excluded.payload,
                    updated_at=excluded.updated_at
                """,
                (_CACHE_KEY_FUZZY_PRODUCTS, sqlite3.Binary(blob)),
            )
        except Exception:
            return

    def ensure_and_rebuild(self) -> None:
        with self._lock:
            con = self._connect()
            try:
                self._fts_available = ensure_ai_schema(con)
                rebuild_all(con)
                try:
                    con.execute(
                        f"DELETE FROM {_SEARCH_CACHE_TABLE} WHERE key = ?",
                        (_CACHE_KEY_FUZZY_PRODUCTS,),
                    )
                except Exception:
                    pass
                con.commit()
            finally:
                con.close()
            self._fuzzy = None
            self._prewarm_started = False
            with _GLOBAL_FUZZY_CACHE_LOCK:
                _GLOBAL_FUZZY_CACHE.pop(self.db_path, None)

        # Regenera cache persistente fuera del lock.
        try:
            self.prewarm()
        except Exception:
            pass

    def prewarm(self) -> None:
        try:
            if self._fts_available is None:
                con = self._connect()
                try:
                    self._fts_available = ensure_ai_schema(con)
                finally:
                    con.close()
            self._ensure_fuzzy_cache()
        except Exception:
            return

    def prewarm_async(self) -> None:
        with self._lock:
            if self._prewarm_started:
                return
            self._prewarm_started = True

        t = threading.Thread(target=self.prewarm, daemon=True)
        t.start()

    def _ensure_fuzzy_cache(self) -> _FuzzyCache:
        with self._lock:
            if self._fuzzy is not None:
                return self._fuzzy
            with _GLOBAL_FUZZY_CACHE_LOCK:
                global_cached = _GLOBAL_FUZZY_CACHE.get(self.db_path)
            if global_cached is not None:
                self._fuzzy = global_cached
                return self._fuzzy

            con = self._connect()
            try:
                crows = con.execute(
                    """
                    SELECT cliente, cedula, telefono
                    FROM quotes
                    WHERE deleted_at IS NULL
                      AND TRIM(COALESCE(cliente,'')) <> ''
                    GROUP BY cliente, cedula, telefono
                    ORDER BY MAX(created_at) DESC
                    """
                ).fetchall()

                clients: List[Tuple[str, str, str]] = []
                for r in crows:
                    clients.append((str(r["cliente"] or ""), str(r["cedula"] or ""), str(r["telefono"] or "")))

                essence_cats = {"ESENCIA", "ESENCIAS", "AROMATERAPIA"}
                prows = con.execute(
                    """
                    SELECT
                        UPPER(COALESCE(id, '')) AS codigo,
                        COALESCE(nombre, '') AS nombre,
                        UPPER(COALESCE(categoria, '')) AS categoria,
                        COALESCE(genero, '') AS genero,
                        COALESCE(ml, '') AS ml,
                        COALESCE(fuente, '') AS fuente
                    FROM products_current
                    """
                ).fetchall()

                products: List[Tuple[str, str]] = []
                product_meta: List[Dict[str, Any]] = []
                products_by_cat: Dict[str, List[Dict[str, Any]]] = {}
                products_by_cat_no_gen: Dict[str, List[Dict[str, Any]]] = {}
                products_by_cat_gen: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
                essence_products: List[Dict[str, Any]] = []
                essence_products_no_gen: List[Dict[str, Any]] = []
                essence_products_by_gen: Dict[str, List[Dict[str, Any]]] = {}
                for r in prows:
                    codigo = str(r["codigo"] or "")
                    nombre = str(r["nombre"] or "")
                    categoria = str(r["categoria"] or "")
                    genero = str(r["genero"] or "")
                    ml = str(r["ml"] or "")
                    fuente = str(r["fuente"] or "")

                    base = _norm_query(" ".join([codigo, nombre, categoria, genero, ml]).strip())
                    ns = re.sub(r"\s+", "", base)
                    split1 = _split_alpha_digit(base)
                    split2 = _split_alpha_digit(ns)
                    wd = _words_to_digits(base)
                    txt = " ".join([base, ns, split1, split2, wd, re.sub(r"\s+", "", wd)]).strip()
                    products.append((codigo, txt))
                    cat_u = str(categoria or "").strip().upper()
                    gen_l = str(genero or "").strip().lower()
                    pm = {
                        "codigo": codigo,
                        "nombre": nombre,
                        "categoria": categoria,
                        "genero": genero,
                        "fuente": fuente,
                        "_cat_u": cat_u,
                        "_gen_l": gen_l,
                    }
                    product_meta.append(pm)

                    if cat_u:
                        products_by_cat.setdefault(cat_u, []).append(pm)
                        if gen_l:
                            products_by_cat_gen.setdefault((cat_u, gen_l), []).append(pm)
                        else:
                            products_by_cat_no_gen.setdefault(cat_u, []).append(pm)

                    if cat_u in essence_cats:
                        essence_products.append(pm)
                        if gen_l:
                            essence_products_by_gen.setdefault(gen_l, []).append(pm)
                        else:
                            essence_products_no_gen.append(pm)

                persisted = self._load_persisted_products_cache(con)
                if persisted:
                    combos_raw = persisted.get("combos") or []
                    pres_raw = persisted.get("presentations") or []

                    combos: List[Dict[str, Any]] = []
                    for item in combos_raw:
                        if isinstance(item, dict):
                            combos.append(dict(item))

                    presentations: List[Dict[str, Any]] = []
                    for item in pres_raw:
                        if isinstance(item, dict):
                            presentations.append(dict(item))

                    if products or combos or presentations:
                        self._fuzzy = self._finalize_fuzzy_cache(
                            products=products,
                            clients=clients,
                            combos=combos,
                            presentations=presentations,
                        )
                        with _GLOBAL_FUZZY_CACHE_LOCK:
                            _GLOBAL_FUZZY_CACHE[self.db_path] = self._fuzzy
                        return self._fuzzy

                combo_rows = con.execute(
                    """
                    SELECT DISTINCT
                        UPPER(COALESCE(pp.cod_producto, '')) AS base_codigo,
                        UPPER(COALESCE(pr.codigo_norm, pr.codigo, pp.cod_presentacion, '')) AS pres_codigo,
                        COALESCE(p.nombre, '') AS base_nombre,
                        COALESCE(pr.nombre, '') AS pres_nombre,
                        COALESCE(p.categoria, '') AS base_categoria,
                        COALESCE(NULLIF(pr.genero, ''), pp.genero, p.genero, '') AS genero,
                        COALESCE(pr.departamento, '') AS departamento,
                        COALESCE(pr.fuente, p.fuente, '') AS fuente
                    FROM presentacion_prod_current pp
                    JOIN products_current p
                        ON UPPER(p.id) = UPPER(pp.cod_producto)
                    LEFT JOIN presentations_current pr
                        ON UPPER(pr.codigo_norm) = UPPER(pp.cod_presentacion)
                        OR UPPER(pr.codigo) = UPPER(pp.cod_presentacion)
                    WHERE TRIM(COALESCE(pp.cod_producto, '')) <> ''
                      AND TRIM(COALESCE(pp.cod_presentacion, '')) <> ''
                    """
                ).fetchall()

                combos: List[Dict[str, Any]] = []
                seen_combo = set()
                for r in combo_rows:
                    base_code = str(r["base_codigo"] or "").strip().upper()
                    pres_code = str(r["pres_codigo"] or "").strip().upper()
                    if not base_code or not pres_code:
                        continue

                    combo_code = f"{base_code}{pres_code}"
                    if combo_code in seen_combo:
                        continue
                    seen_combo.add(combo_code)

                    base_name = str(r["base_nombre"] or "").strip()
                    pres_name = str(r["pres_nombre"] or "").strip()
                    depto = str(r["departamento"] or "").strip().upper()
                    genero = str(r["genero"] or "").strip()
                    fuente = str(r["fuente"] or "").strip()
                    nombre = " ".join([x for x in [base_name, pres_name] if x]).strip() or combo_code

                    text = _norm_query(
                        " ".join(
                            [
                                combo_code,
                                base_code,
                                pres_code,
                                nombre,
                                base_name,
                                pres_name,
                                depto,
                                genero,
                            ]
                        ).strip()
                    )
                    text_ns = re.sub(r"\s+", "", text)

                    combos.append(
                        {
                            "codigo": combo_code,
                            "nombre": nombre,
                            "categoria": "PRESENTACION",
                            "genero": genero,
                            "ml": "",
                            "fuente": fuente,
                            "_text": text,
                            "_text_ns": text_ns,
                        }
                    )

                pres_rows = con.execute(
                    """
                    SELECT
                        UPPER(COALESCE(codigo_norm, '')) AS codigo_norm,
                        UPPER(COALESCE(codigo, '')) AS codigo,
                        COALESCE(nombre, '') AS nombre,
                        COALESCE(departamento, '') AS departamento,
                        COALESCE(genero, '') AS genero,
                        COALESCE(fuente, '') AS fuente
                    FROM presentations_current
                    WHERE TRIM(COALESCE(codigo_norm, codigo, '')) <> ''
                    """
                ).fetchall()

                presentations: List[Dict[str, Any]] = []
                seen_pres = set()
                for r in pres_rows:
                    nombre = str(r["nombre"] or "").strip()
                    depto = str(r["departamento"] or "").strip().upper()
                    genero = str(r["genero"] or "").strip()
                    genero_l = genero.lower()
                    fuente = str(r["fuente"] or "").strip()

                    codes = []
                    c1 = str(r["codigo_norm"] or "").strip().upper()
                    c2 = str(r["codigo"] or "").strip().upper()
                    if c1:
                        codes.append(c1)
                    if c2 and c2 != c1:
                        codes.append(c2)

                    for code in codes:
                        if code in seen_pres:
                            continue
                        seen_pres.add(code)

                        text = _norm_query(
                            " ".join(
                                [
                                    code,
                                    nombre,
                                    depto,
                                    genero,
                                    "presentacion",
                                ]
                            ).strip()
                        )
                        text_ns = re.sub(r"\s+", "", text)

                        presentations.append(
                            {
                                "codigo": code,
                                "nombre": nombre or code,
                                "categoria": "PRESENTACION",
                                "genero": genero,
                                "ml": "",
                                "fuente": fuente,
                                "_text": text,
                                "_text_ns": text_ns,
                            }
                        )

                    # Combos sintéticos: base(esencia)+presentación cuando
                    # el departamento de presentación no discrimina base.
                    dep_is_presentation = depto in {"", "PRESENTACION", "PRESENTACIONES"}
                    if dep_is_presentation:
                        candidates: List[Dict[str, Any]] = (
                            essence_products
                            if not genero_l
                            else (essence_products_by_gen.get(genero_l, []) + essence_products_no_gen)
                        )
                    else:
                        candidates = (
                            products_by_cat.get(depto, [])
                            if not genero_l
                            else (products_by_cat_gen.get((depto, genero_l), []) + products_by_cat_no_gen.get(depto, []))
                        )

                    for pm in candidates:
                        base_code = str(pm.get("codigo") or "").strip().upper()
                        base_name = str(pm.get("nombre") or "").strip()
                        base_cat = str(pm.get("_cat_u") or "").strip().upper()
                        base_gen = str(pm.get("_gen_l") or "").strip().lower()
                        if not base_code:
                            continue

                        if dep_is_presentation:
                            if base_cat not in essence_cats:
                                continue
                        else:
                            if base_cat != depto:
                                continue

                        if genero and base_gen and base_gen != genero_l:
                            continue

                        for pres_code in codes:
                            combo_code = f"{base_code}{pres_code}"
                            if combo_code in seen_combo:
                                continue
                            seen_combo.add(combo_code)

                            combo_name = " ".join(
                                [x for x in [base_name, (nombre or pres_code)] if x]
                            ).strip() or combo_code
                            combo_src = str(pm.get("fuente") or "").strip() or fuente

                            combo_text = _norm_query(
                                " ".join(
                                    [
                                        combo_code,
                                        base_code,
                                        pres_code,
                                        combo_name,
                                        base_name,
                                        nombre,
                                        depto,
                                        genero,
                                    ]
                                ).strip()
                            )
                            combo_text_ns = re.sub(r"\s+", "", combo_text)

                            combos.append(
                                {
                                    "codigo": combo_code,
                                    "nombre": combo_name,
                                    "categoria": "PRESENTACION",
                                    "genero": genero or str(pm.get("genero") or ""),
                                    "ml": "",
                                    "fuente": combo_src,
                                    "_text": combo_text,
                                    "_text_ns": combo_text_ns,
                                }
                            )

                self._fuzzy = self._finalize_fuzzy_cache(
                    products=products,
                    clients=clients,
                    combos=combos,
                    presentations=presentations,
                )
                self._save_persisted_products_cache(
                    con,
                    combos=combos,
                    presentations=presentations,
                )
                con.commit()
                with _GLOBAL_FUZZY_CACHE_LOCK:
                    _GLOBAL_FUZZY_CACHE[self.db_path] = self._fuzzy

                return self._fuzzy
            finally:
                con.close()

    def search_products(self, q: str, limit: int = 15) -> List[Dict[str, Any]]:
        qn = _norm_query(q)
        if len(qn) < 1:
            return []

        short = (len(qn) < 2)
        variants = [qn] if short else _query_variants(qn)
        pre_out: List[Dict[str, Any]] = []

        con = self._connect()
        try:
            if self._fts_available is None:
                self._fts_available = ensure_ai_schema(con)

            seen = set()
            out: List[Dict[str, Any]] = []

            cache = self._ensure_fuzzy_cache()
            qn_ns = re.sub(r"\s+", "", qn)

            if self._fts_available:
                for qv in variants:
                    if len(out) >= (limit * 2):
                        break
                    rows = _search_products_fts(con, qv, max(limit * 3, 25))
                    for r in rows or []:
                        code = str(r.get("codigo") or "").strip()
                        if not code or code in seen:
                            continue
                        seen.add(code)
                        out.append(r)
                        if len(out) >= (limit * 2):
                            break

            for qv in variants:
                if len(out) >= (limit * 2):
                    break
                rows = _search_products_like(con, qv, max(limit * 3, 25))
                for r in rows or []:
                    code = str(r.get("codigo") or "").strip()
                    if not code or code in seen:
                        continue
                    seen.add(code)
                    out.append(r)
                    if len(out) >= (limit * 2):
                        break

            def _score_cached_row(row: Dict[str, Any]) -> int:
                code = str(row.get("codigo") or "").strip().lower()
                text = str(row.get("_text") or "")
                text_ns = str(row.get("_text_ns") or "")
                if not code:
                    return 0
                if qn_ns and code == qn_ns:
                    return 300
                if qn_ns and code.startswith(qn_ns):
                    return 240
                if qn_ns and qn_ns in code:
                    return 190
                if qn and qn in text:
                    return 150
                if qn_ns and qn_ns in text_ns:
                    return 120
                return 0

            pref_scored: List[Tuple[int, Dict[str, Any]]] = []
            run_pref_scan = (" " not in qn) or any(ch.isdigit() for ch in qn_ns)
            if run_pref_scan:
                rows_pref = cache.pref_rows_all
                qcode = str(qn_ns or "").strip().upper()
                if len(qcode) >= 2:
                    rows_pref = cache.pref_rows_by_2.get(qcode[:2], rows_pref)
                elif len(qcode) == 1:
                    rows_pref = cache.pref_rows_by_1.get(qcode[:1], rows_pref)

                for row in (rows_pref or []):
                    s = _score_cached_row(row)
                    if s > 0:
                        pref_scored.append((s, row))

            pref_scored.sort(key=lambda x: x[0], reverse=True)
            for _score, row in pref_scored:
                code = str(row.get("codigo") or "").strip()
                if not code or code in seen:
                    continue
                seen.add(code)
                out.append(
                    {
                        "codigo": code,
                        "nombre": row.get("nombre", ""),
                        "categoria": row.get("categoria", "PRESENTACION"),
                        "genero": row.get("genero", ""),
                        "ml": row.get("ml", ""),
                        "fuente": row.get("fuente", ""),
                    }
                )
                if len(out) >= (limit * 2):
                    break

            if out:
                pre_out = out[:limit]
                # Para búsquedas tipo código, el merge final ya devolvería esto
                # cuando está completo; evitar fuzzy aquí no altera resultados.
                if (" " not in qn) and (len(pre_out) >= limit):
                    return pre_out
                if (" " in qn) and (len(pre_out) >= limit):
                    return pre_out

            if short:
                return pre_out

        finally:
            con.close()

        cache = self._ensure_fuzzy_cache()
        # Para texto libre (con espacios), excluir combos del fuzzy pesado.
        # Los combos se mantienen en búsquedas de código.
        has_digits = any(ch.isdigit() for ch in qn_ns)
        choices = cache.choices if (has_digits or (" " not in qn)) else cache.choices_text
        combo_map = cache.combo_map
        pres_map = cache.pres_map

        best: Dict[str, int] = {}
        for qv in variants:
            hits = process.extract(
                qv,
                choices,
                scorer=fuzz.WRatio,
                limit=max(limit * 6, 60),
                score_cutoff=45,
            )
            for _val, score, key in hits:
                k = str(key)
                s = int(score or 0)
                if s > best.get(k, 0):
                    best[k] = s

        product_codes = cache.product_codes
        scored = sorted(
            best.items(),
            key=lambda kv: (1 if str(kv[0]) in product_codes else 0, kv[1]),
            reverse=True,
        )
        picked_codes: List[str] = []
        res: List[Dict[str, Any]] = []
        for codigo, score in scored:
            if score < 45:
                continue
            picked_codes.append(str(codigo))
            res.append({"codigo": str(codigo), "nombre": "", "score": int(score)})
            if len(res) >= limit:
                break

        if picked_codes:
            try:
                con2 = self._connect()
                try:
                    ph = ",".join(["?"] * len(picked_codes))
                    rr = con2.execute(
                        f"""
                        SELECT
                            id AS codigo,
                            COALESCE(nombre,'') AS nombre,
                            COALESCE(categoria,'') AS categoria,
                            COALESCE(genero,'') AS genero,
                            COALESCE(ml,'') AS ml,
                            COALESCE(fuente,'') AS fuente
                        FROM products_current
                        WHERE id IN ({ph})
                        """,
                        tuple(picked_codes),
                    ).fetchall()
                    mp = {str(r["codigo"]): dict(r) for r in rr}
                    for r in res:
                        code = str(r.get("codigo") or "")
                        extra = mp.get(code)
                        if extra:
                            r.update({k: v for k, v in extra.items() if k != "codigo"})
                        elif code in combo_map:
                            c = combo_map[code]
                            r.update(
                                {
                                    "nombre": c.get("nombre", ""),
                                    "categoria": c.get("categoria", "PRESENTACION"),
                                    "genero": c.get("genero", ""),
                                    "ml": c.get("ml", ""),
                                    "fuente": c.get("fuente", ""),
                                }
                            )
                        elif code in pres_map:
                            p = pres_map[code]
                            r.update(
                                {
                                    "nombre": p.get("nombre", ""),
                                    "categoria": p.get("categoria", "PRESENTACION"),
                                    "genero": p.get("genero", ""),
                                    "ml": p.get("ml", ""),
                                    "fuente": p.get("fuente", ""),
                                }
                            )
                finally:
                    con2.close()
            except Exception:
                pass

        if pre_out:
            merged: List[Dict[str, Any]] = []
            seen_codes = set()

            def _push(rows: List[Dict[str, Any]]) -> None:
                for row in rows or []:
                    code = str(row.get("codigo") or "").strip()
                    if not code or code in seen_codes:
                        continue
                    seen_codes.add(code)
                    merged.append(row)
                    if len(merged) >= limit:
                        break

            if (" " in qn) and res:
                head = max(1, min(len(pre_out), limit // 2))
                _push(pre_out[:head])
                if len(merged) < limit:
                    _push(res)
                if len(merged) < limit:
                    _push(pre_out[head:])
            else:
                _push(pre_out)
                if len(merged) < limit:
                    _push(res)

            return merged[:limit]

        return res[:limit]

    # ✅ CLIENTES: ordenar por “más pidió” (más cotizaciones), no por items
    def search_clients(self, q: str, limit: int = 15) -> List[Dict[str, Any]]:
        qn = _norm_query(q)
        if len(qn) < 1:
            return []

        short = (len(qn) < 2)
        variants = [qn] if short else _query_variants(qn)

        con = self._connect()
        try:
            if self._fts_available is None:
                self._fts_available = ensure_ai_schema(con)

            seen = set()
            out: List[Dict[str, Any]] = []
            collect_limit = max(limit * 5, 60)

            if self._fts_available:
                for qv in variants:
                    rows = _search_clients_fts(con, qv, max(collect_limit, 60))
                    for r in rows or []:
                        key = (r.get("cliente"), r.get("cedula"), r.get("telefono"))
                        if key in seen:
                            continue
                        seen.add(key)
                        out.append(r)
                        if len(out) >= collect_limit:
                            break

            for qv in variants:
                rows = _search_clients_like(con, qv, max(collect_limit, 60))
                for r in rows or []:
                    key = (r.get("cliente"), r.get("cedula"), r.get("telefono"))
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append(r)
                    if len(out) >= collect_limit:
                        break

            if not out and not short:
                # FUZZY fallback
                cache = self._ensure_fuzzy_cache()

                choices: Dict[str, str] = {}
                for i, (cli, doc, tel) in enumerate(cache.clients):
                    base = _norm_query(f"{cli} {doc} {tel}".strip())
                    ns = base.replace(" ", "")
                    choices[str(i)] = f"{base} {ns}"

                best: Dict[str, int] = {}
                for qv in variants:
                    hits = process.extract(qv, choices, scorer=fuzz.WRatio, limit=max(limit * 10, 120))
                    for _val, score, key in hits:
                        k = str(key)
                        s = int(score or 0)
                        if s > best.get(k, 0):
                            best[k] = s

                scored = sorted(best.items(), key=lambda kv: kv[1], reverse=True)
                for key, score in scored:
                    if score < 48:
                        continue
                    i = int(key)
                    cli, doc, tel = cache.clients[i]
                    out.append({"cliente": cli, "cedula": doc, "telefono": tel, "score": score, "_rel": float(score)})
                    if len(out) >= collect_limit:
                        break

            # ✅ ranking final por cantidad de cotizaciones (no por items)
            if out:
                keys = []
                for r in out:
                    cli = str(r.get("cliente") or "").strip()
                    doc = str(r.get("cedula") or "").strip()
                    tel = str(r.get("telefono") or "").strip()
                    k = f"{cli.lower().strip()}|{doc.lower().strip()}|{tel.lower().strip()}"
                    keys.append(k)

                # query counts en 1 tiro
                expr = "LOWER(TRIM(COALESCE(cliente,''))) || '|' || LOWER(TRIM(COALESCE(cedula,''))) || '|' || LOWER(TRIM(COALESCE(telefono,'')))"
                ph = ",".join(["?"] * len(keys))
                rr = con.execute(
                    f"""
                    SELECT
                        {expr} AS k,
                        COUNT(*) AS cnt,
                        MAX(created_at) AS last_created
                    FROM quotes
                    WHERE deleted_at IS NULL
                      AND ({expr}) IN ({ph})
                    GROUP BY k
                    """,
                    tuple(keys),
                ).fetchall()

                mp = {str(r["k"]): (int(r["cnt"] or 0), str(r["last_created"] or "")) for r in rr}

                for r in out:
                    cli = str(r.get("cliente") or "").strip()
                    doc = str(r.get("cedula") or "").strip()
                    tel = str(r.get("telefono") or "").strip()
                    k = f"{cli.lower().strip()}|{doc.lower().strip()}|{tel.lower().strip()}"
                    cnt, lastc = mp.get(k, (0, ""))
                    r["usage_cnt"] = cnt
                    r["last_created"] = lastc
                    try:
                        r["_rel"] = float(r.get("_rel") or 0.0)
                    except Exception:
                        r["_rel"] = 0.0

                out.sort(
                    key=lambda r: (
                        int(r.get("usage_cnt") or 0),
                        float(r.get("_rel") or 0.0),
                        str(r.get("last_created") or ""),
                    ),
                    reverse=True,
                )

            # limpia campo interno
            res = []
            for r in out[:limit]:
                rr2 = dict(r)
                rr2.pop("_rel", None)
                res.append(rr2)
            return res

        finally:
            con.close()
