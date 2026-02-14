# src/ai/assistant/resolvers.py
from __future__ import annotations

import difflib
import datetime
from typing import Optional

from sqlModels.db import connect, ensure_schema


def resolve_client_from_history(db_path: str, query: str) -> Optional[tuple[str, str, str]]:
    """
    Devuelve (cliente, cedula, telefono) desde histórico quotes si encuentra match.
    """
    q = (query or "").strip()
    if not q:
        return None

    con = connect(db_path)
    ensure_schema(con)
    try:
        like = f"%{q}%"
        rows = con.execute(
            """
            SELECT cliente, cedula, telefono
            FROM quotes
            WHERE deleted_at IS NULL
              AND (cliente LIKE ? OR cedula LIKE ? OR telefono LIKE ?)
            ORDER BY created_at DESC
            LIMIT 20
            """,
            (like, like, like),
        ).fetchall()
        if not rows:
            return None

        best = None
        best_s = -1.0
        for r in rows:
            name = str(r["cliente"] or "")
            s = difflib.SequenceMatcher(None, q.lower(), name.lower()).ratio()
            if s > best_s:
                best_s = s
                best = (str(r["cliente"] or ""), str(r["cedula"] or ""), str(r["telefono"] or ""))

        return best
    finally:
        con.close()


def resolve_product_candidates(window, query: str, limit: int = 6, allow_weak: bool = True) -> list[tuple[str, str, float, str]]:
    """
    Devuelve lista de candidatos: (codigo, nombre, score, kind)
    kind: "product" o "presentation"

    allow_weak: si no hay match fuerte, devuelve sugerencias por similitud (útil para “no match”).
    """
    q = (query or "").strip()
    if not q:
        return []

    q_u = q.upper()

    # Fuente A: ventana cotizador
    prod = getattr(window, "productos", None) or []
    pres = getattr(window, "presentaciones", None) or []

    # Fuente B: histórico (catalog_manager)
    if (not prod or not pres):
        cm = getattr(window, "catalog_manager", None)
        if cm is not None:
            try:
                dfp = getattr(cm, "df_productos", None)
                if dfp is not None and (not dfp.empty):
                    prod = dfp.to_dict("records")
            except Exception:
                pass
            try:
                dfpr = getattr(cm, "df_presentaciones", None)
                if dfpr is not None and (not dfpr.empty):
                    pres = dfpr.to_dict("records")
            except Exception:
                pass

    candidates: list[tuple[str, str, float, str]] = []

    # exact code (products)
    for p in prod:
        code = str(p.get("id") or "").strip().upper()
        if code and code == q_u:
            return [(code, str(p.get("nombre") or code), 1.0, "product")]

    # exact code (presentations)
    for p in pres:
        code = str(p.get("CODIGO_NORM") or "").strip().upper()
        if code and code == q_u:
            return [(code, str(p.get("NOMBRE") or code), 1.0, "presentation")]

    def score_text(a: str, b: str) -> float:
        return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()

    # fuzzy “normal”
    for p in prod:
        code = str(p.get("id") or "").strip().upper()
        name = str(p.get("nombre") or "").strip()
        blob = f"{code} {name} {p.get('categoria','')}"
        if q_u in blob.upper():
            candidates.append((code, name or code, 0.85, "product"))
        else:
            s = max(score_text(q, code), score_text(q, name))
            if s >= 0.55:
                candidates.append((code, name or code, 0.55 + 0.35 * s, "product"))

    for p in pres:
        code = str(p.get("CODIGO_NORM") or "").strip().upper()
        name = str(p.get("NOMBRE") or "").strip()
        blob = f"{code} {name} {p.get('DEPARTAMENTO','')}"
        if q_u in blob.upper():
            candidates.append((code, name or code, 0.85, "presentation"))
        else:
            s = max(score_text(q, code), score_text(q, name))
            if s >= 0.55:
                candidates.append((code, name or code, 0.55 + 0.35 * s, "presentation"))

    candidates.sort(key=lambda x: x[2], reverse=True)
    candidates = candidates[: int(limit)]

    # weak suggestions
    if not candidates and allow_weak:
        weak: list[tuple[str, str, float, str]] = []

        def add_weak(code: str, name: str, kind: str):
            s = max(score_text(q, code), score_text(q, name))
            if s <= 0:
                return
            weak.append((code, name or code, 0.20 + 0.55 * s, kind))

        for p in prod:
            code = str(p.get("id") or "").strip().upper()
            name = str(p.get("nombre") or "").strip()
            if code:
                add_weak(code, name, "product")

        for p in pres:
            code = str(p.get("CODIGO_NORM") or "").strip().upper()
            name = str(p.get("NOMBRE") or "").strip()
            if code:
                add_weak(code, name, "presentation")

        weak.sort(key=lambda x: x[2], reverse=True)
        candidates = weak[: int(limit)]

    return candidates


def month_range_from_today(today: datetime.date) -> tuple[str, str]:
    start = today.replace(day=1)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1, day=1)
    else:
        end = start.replace(month=start.month + 1, day=1)
    return (start.isoformat(), end.isoformat())
