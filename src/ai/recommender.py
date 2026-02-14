# src/ai/recommender.py
from __future__ import annotations

import sqlite3
import datetime as _dt
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Set

from ..config import ALLOW_NO_STOCK, listing_allows_products, listing_allows_presentations
from ..presentations import map_pc_to_bottle_code


@dataclass
class RecItem:
    codigo: str              # código a usar para agregar (CODIGO para presentaciones)
    kind: str                # "product" | "presentation" | "pc"
    nombre: str
    qty: float
    price_base: float        # unitario base (para override)
    score: float             # 0..1
    reason: str


def _norm_client(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


def _cutoff_date_iso(days: int) -> str:
    d = (_dt.datetime.utcnow() - _dt.timedelta(days=int(days))).date()
    return d.strftime("%Y-%m-%d")


class QuoteRecommender:
    """
    Recomendador por co-ocurrencia en quotes/quote_items.

    - Cliente exacto por (cliente+cedula+telefono) (normalizado).
    - Sin cliente completo: solo recomienda si hay 'seeds' (productos ya elegidos) y usa últimos 3 meses.
    - Umbral: P(cand|seed) >= 0.20 y soporte co-oc >= 2.   ✅
    """
    def __init__(self, db_path: str):
        self.db_path = str(db_path)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("PRAGMA journal_mode = WAL")
        con.execute("PRAGMA synchronous = NORMAL")
        con.execute("PRAGMA busy_timeout = 5000")
        return con

    def _catalog_lookup(self, con: sqlite3.Connection, code_u: str) -> Optional[Tuple[str, str, str]]:
        """
        Retorna (kind, codigo_add, nombre) o None si no existe en catálogo actual.

        Prioridad:
        - PC... (existe como product pero se trata como presentation)
        - presentations_current (devuelve codigo para agregar)
        - products_current
        """
        code_u = (code_u or "").strip().upper()
        if not code_u:
            return None

        # 1) PC... (se agregan con código tal cual)
        if code_u.startswith("PC"):
            r = con.execute(
                "SELECT id, COALESCE(nombre,'') AS nombre FROM products_current WHERE UPPER(id)=? LIMIT 1",
                (code_u,),
            ).fetchone()
            if r:
                return ("pc", str(r["id"]), str(r["nombre"] or ""))

        # 2) presentations_current: match por codigo_norm o codigo
        r = con.execute(
            """
            SELECT codigo_norm, COALESCE(codigo,'') AS codigo, COALESCE(nombre,'') AS nombre
            FROM presentations_current
            WHERE UPPER(codigo_norm)=? OR UPPER(codigo)=?
            LIMIT 1
            """,
            (code_u, code_u),
        ).fetchone()
        if r:
            codigo_add = str(r["codigo"] or r["codigo_norm"] or "").strip().upper()
            if codigo_add:
                return ("presentation", codigo_add, str(r["nombre"] or ""))

        # 3) products_current
        r = con.execute(
            "SELECT id, COALESCE(nombre,'') AS nombre FROM products_current WHERE UPPER(id)=? LIMIT 1",
            (code_u,),
        ).fetchone()
        if r:
            return ("product", str(r["id"]), str(r["nombre"] or ""))

        return None

    def _product_stock_ok(self, con: sqlite3.Connection, code_u: str) -> bool:
        if ALLOW_NO_STOCK:
            return True
        r = con.execute(
            "SELECT COALESCE(cantidad_disponible,0) AS s FROM products_current WHERE UPPER(id)=? LIMIT 1",
            (code_u,),
        ).fetchone()
        if not r:
            return False
        try:
            return float(r["s"] or 0.0) > 0.0
        except Exception:
            return False

    def _pc_bottle_stock_ok(self, con: sqlite3.Connection, pc_code_u: str) -> bool:
        if ALLOW_NO_STOCK:
            return True
        bot_code = (map_pc_to_bottle_code(pc_code_u) or "").strip().upper()
        if not bot_code:
            return True  # si no se puede inferir, no bloqueamos aquí
        r = con.execute(
            "SELECT COALESCE(cantidad_disponible,0) AS s FROM products_current WHERE UPPER(id)=? LIMIT 1",
            (bot_code,),
        ).fetchone()
        if not r:
            return False
        try:
            return float(r["s"] or 0.0) > 0.0
        except Exception:
            return False

    def _allowed_kind(self, kind: str) -> bool:
        # Respeta config por listado
        if kind in ("presentation", "pc"):
            return bool(listing_allows_presentations())
        return bool(listing_allows_products())

    def recommend(
        self,
        *,
        client_triplet: Optional[Tuple[str, str, str]],
        seeds: List[str],
        limit: int = 10,
        p_threshold: float = 0.20,   # ✅ default 20%
        min_support: int = 2,
    ) -> List[RecItem]:
        seeds_u: List[str] = [str(x or "").strip().upper() for x in (seeds or []) if str(x or "").strip()]
        seed_set: Set[str] = set(seeds_u)

        # Sin cliente completo: solo recomendamos si ya hay seeds
        if (not client_triplet or not all(client_triplet)) and not seeds_u:
            return []

        con = self._connect()
        try:
            # Scope
            params: List[object] = []
            where = ["q.deleted_at IS NULL"]

            # Cliente completo: TODO el historial
            if client_triplet and all(client_triplet):
                c, d, t = client_triplet
                where.append("LOWER(TRIM(q.cliente))=LOWER(TRIM(?))")
                where.append("LOWER(TRIM(q.cedula))=LOWER(TRIM(?))")
                where.append("LOWER(TRIM(q.telefono))=LOWER(TRIM(?))")
                params.extend([c, d, t])
                scope_label = "del cliente"
            else:
                # Global: últimos 3 meses
                cutoff = _cutoff_date_iso(92)
                where.append("substr(q.created_at,1,10) >= ?")
                params.append(cutoff)
                scope_label = "recientes"

            where_sql = " AND ".join(where)

            rows = con.execute(
                f"""
                SELECT
                    qi.quote_id,
                    UPPER(TRIM(COALESCE(qi.codigo,''))) AS codigo,
                    COALESCE(qi.cantidad,0) AS cantidad,
                    COALESCE(qi.precio_base,0) AS precio_base
                FROM quote_items qi
                JOIN quotes q ON q.id = qi.quote_id
                WHERE {where_sql}
                  AND TRIM(COALESCE(qi.codigo,'')) <> ''
                """,
                tuple(params),
            ).fetchall()

            # Agrupar por quote
            quote_codes: Dict[int, Set[str]] = {}
            quote_items: Dict[int, List[Tuple[str, float, float]]] = {}
            for r in rows:
                qid = int(r["quote_id"])
                code = str(r["codigo"] or "").strip().upper()
                if not code:
                    continue
                qty = float(r["cantidad"] or 0.0)
                pr = float(r["precio_base"] or 0.0)

                quote_codes.setdefault(qid, set()).add(code)
                quote_items.setdefault(qid, []).append((code, qty, pr))

            if not quote_codes:
                return []

            # Conteos
            total_quotes = len(quote_codes)
            count_code: Dict[str, int] = {}
            co_count: Dict[Tuple[str, str], int] = {}

            # para mediana en contexto (con seeds)
            ctx_qty: Dict[str, List[float]] = {}
            ctx_pr: Dict[str, List[float]] = {}

            for qid, codes in quote_codes.items():
                for c in codes:
                    count_code[c] = count_code.get(c, 0) + 1

                if seed_set:
                    present_seeds = [s for s in seed_set if s in codes]
                    if not present_seeds:
                        continue
                    for s in present_seeds:
                        for cand in codes:
                            if cand == s:
                                continue
                            co_count[(s, cand)] = co_count.get((s, cand), 0) + 1

                if seed_set:
                    present_seeds = [s for s in seed_set if s in codes]
                    if present_seeds:
                        items = quote_items.get(qid, [])
                        codes_in_q = set([x[0] for x in items])
                        for cand in (codes_in_q - set(present_seeds)):
                            for (cc, qty, pr) in items:
                                if cc == cand:
                                    if qty > 0:
                                        ctx_qty.setdefault(cand, []).append(float(qty))
                                    if pr > 0:
                                        ctx_pr.setdefault(cand, []).append(float(pr))

            candidates: Dict[str, Tuple[float, str, int]] = {}  # code -> (score, reason, support)

            if not seed_set:
                # frecuencia por cliente (independiente de #items por quote)
                for cand, cnt in count_code.items():
                    p = (cnt / total_quotes) if total_quotes > 0 else 0.0
                    if p >= float(p_threshold) and cnt >= int(min_support):
                        candidates[cand] = (
                            p,
                            f"El cliente compra este ítem en {cnt}/{total_quotes} cotizaciones (p={p:.0%}).",
                            cnt,
                        )
            else:
                for cand in list(count_code.keys()):
                    if cand in seed_set:
                        continue

                    best_p = 0.0
                    best_seed = ""
                    best_support = 0
                    hits = 0

                    for s in seed_set:
                        denom = count_code.get(s, 0)
                        if denom <= 0:
                            continue
                        sup = co_count.get((s, cand), 0)
                        p = (sup / denom) if denom > 0 else 0.0
                        if p >= float(p_threshold):
                            hits += 1
                        if p > best_p:
                            best_p = p
                            best_seed = s
                            best_support = sup

                    if best_p >= float(p_threshold) and best_support >= int(min_support):
                        score = min(1.0, best_p + (0.05 * max(0, hits - 1)))
                        candidates[cand] = (
                            score,
                            f"En {best_support}/{count_code.get(best_seed, 0)} cotizaciones {scope_label} "
                            f"cuando está '{best_seed}' también llevan '{cand}' (p={best_p:.0%}).",
                            best_support,
                        )

            if not candidates:
                if client_triplet and all(client_triplet) and seed_set:
                    return self.recommend(
                        client_triplet=None,
                        seeds=seeds_u,
                        limit=limit,
                        p_threshold=p_threshold,
                        min_support=min_support,
                    )
                return []

            ranked = sorted(
                [(code, sc, rsn, sup) for code, (sc, rsn, sup) in candidates.items()],
                key=lambda x: (float(x[1]), int(x[3])),
                reverse=True,
            )

            out: List[RecItem] = []
            for code, sc, rsn, sup in ranked:
                code_u = str(code).strip().upper()
                cat = self._catalog_lookup(con, code_u)
                if not cat:
                    continue

                kind, codigo_add, nombre = cat
                if not self._allowed_kind(kind):
                    continue

                if kind == "product":
                    if not self._product_stock_ok(con, code_u):
                        continue
                if kind == "pc":
                    if not self._pc_bottle_stock_ok(con, code_u):
                        continue

                qty_list = ctx_qty.get(code_u, [])
                pr_list = ctx_pr.get(code_u, [])

                if not qty_list or not pr_list:
                    qty_list, pr_list = self._fetch_recent_stats(con, where_sql, params, code_u)

                qty = self._median(qty_list) if qty_list else 1.0
                pr = self._median(pr_list) if pr_list else 0.0
                if pr <= 0:
                    continue

                out.append(
                    RecItem(
                        codigo=str(codigo_add).strip().upper(),
                        kind=kind,
                        nombre=str(nombre or "").strip(),
                        qty=float(qty),
                        price_base=float(pr),
                        score=float(sc),
                        reason=str(rsn),
                    )
                )
                if len(out) >= int(limit):
                    break

            return out[: int(limit)]
        finally:
            con.close()

    def _fetch_recent_stats(
        self,
        con: sqlite3.Connection,
        where_sql: str,
        params: List[object],
        code_u: str,
        limit: int = 200,
    ) -> Tuple[List[float], List[float]]:
        rr = con.execute(
            f"""
            SELECT
                COALESCE(qi.cantidad,0) AS cantidad,
                COALESCE(qi.precio_base,0) AS precio_base
            FROM quote_items qi
            JOIN quotes q ON q.id = qi.quote_id
            WHERE {where_sql}
              AND UPPER(TRIM(COALESCE(qi.codigo,''))) = ?
            ORDER BY q.created_at DESC
            LIMIT ?
            """,
            tuple(list(params) + [code_u, int(limit)]),
        ).fetchall()

        qtys: List[float] = []
        prs: List[float] = []
        for r in rr:
            try:
                q = float(r["cantidad"] or 0.0)
                if q > 0:
                    qtys.append(q)
            except Exception:
                pass
            try:
                p = float(r["precio_base"] or 0.0)
                if p > 0:
                    prs.append(p)
            except Exception:
                pass
        return qtys, prs

    @staticmethod
    def _median(xs: List[float]) -> float:
        xs2 = [float(x) for x in xs if x is not None]
        if not xs2:
            return 0.0
        xs2.sort()
        n = len(xs2)
        mid = n // 2
        if n % 2 == 1:
            return float(xs2[mid])
        return float((xs2[mid - 1] + xs2[mid]) / 2.0)
