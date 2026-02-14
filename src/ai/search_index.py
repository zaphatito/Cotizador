# src/ai/search_index.py
from __future__ import annotations

import re
import sqlite3
import threading
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from rapidfuzz import fuzz, process


_FTS_PRODUCTS = "ai_products_fts"
_FTS_CLIENTS = "ai_clients_fts"


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

    return out[:18]


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
            codigo, nombre, categoria, genero, ml, fuente,
            bm25({_FTS_PRODUCTS}) AS score
        FROM {_FTS_PRODUCTS}
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


class LocalSearchIndex:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._fts_available: Optional[bool] = None
        self._fuzzy: Optional[_FuzzyCache] = None

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("PRAGMA journal_mode = WAL")
        con.execute("PRAGMA synchronous = NORMAL")
        con.execute("PRAGMA busy_timeout = 5000")
        return con

    def ensure_and_rebuild(self) -> None:
        with self._lock:
            con = self._connect()
            try:
                self._fts_available = ensure_ai_schema(con)
                rebuild_all(con)
                con.commit()
            finally:
                con.close()
            self._fuzzy = None

    def _ensure_fuzzy_cache(self) -> _FuzzyCache:
        with self._lock:
            if self._fuzzy is not None:
                return self._fuzzy

            con = self._connect()
            try:
                prows = con.execute(
                    """
                    SELECT id, COALESCE(nombre,''), COALESCE(categoria,''), COALESCE(genero,''), COALESCE(ml,'')
                    FROM products_current
                    """
                ).fetchall()

                products: List[Tuple[str, str]] = []
                for r in prows:
                    codigo = str(r["id"] or "")
                    nombre = str(r[1] or "")
                    categoria = str(r[2] or "")
                    genero = str(r[3] or "")
                    ml = str(r[4] or "")

                    base = _norm_query(" ".join([codigo, nombre, categoria, genero, ml]).strip())
                    ns = re.sub(r"\s+", "", base)
                    split1 = _split_alpha_digit(base)
                    split2 = _split_alpha_digit(ns)
                    wd = _words_to_digits(base)
                    txt = " ".join([base, ns, split1, split2, wd, re.sub(r"\s+", "", wd)]).strip()
                    products.append((codigo, txt))

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

                self._fuzzy = _FuzzyCache(products=products, clients=clients)
                return self._fuzzy
            finally:
                con.close()

    def search_products(self, q: str, limit: int = 15) -> List[Dict[str, Any]]:
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

            if self._fts_available:
                for qv in variants:
                    rows = _search_products_fts(con, qv, max(limit * 3, 25))
                    for r in rows or []:
                        code = str(r.get("codigo") or "").strip()
                        if not code or code in seen:
                            continue
                        seen.add(code)
                        out.append(r)
                        if len(out) >= limit:
                            return out[:limit]

            for qv in variants:
                rows = _search_products_like(con, qv, max(limit * 3, 25))
                for r in rows or []:
                    code = str(r.get("codigo") or "").strip()
                    if not code or code in seen:
                        continue
                    seen.add(code)
                    out.append(r)
                    if len(out) >= limit:
                        return out[:limit]

            if short:
                return out[:limit]

        finally:
            con.close()

        cache = self._ensure_fuzzy_cache()
        choices: Dict[str, str] = {codigo: texto for (codigo, texto) in cache.products}

        best: Dict[str, int] = {}
        for qv in variants:
            hits = process.extract(qv, choices, scorer=fuzz.WRatio, limit=max(limit * 10, 120))
            for _val, score, key in hits:
                k = str(key)
                s = int(score or 0)
                if s > best.get(k, 0):
                    best[k] = s

        scored = sorted(best.items(), key=lambda kv: kv[1], reverse=True)
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
                finally:
                    con2.close()
            except Exception:
                pass

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
