# sqlModels/quotes_repo.py
from __future__ import annotations

import sqlite3
from typing import Any


def insert_quote(
    con: sqlite3.Connection,
    *,
    country_code: str,
    quote_no: str,
    created_at: str,
    cliente: str,
    cedula: str,
    telefono: str,
    currency_shown: str,
    tasa_shown: float | None,
    subtotal_bruto_base: float,
    descuento_total_base: float,
    total_neto_base: float,
    subtotal_bruto_shown: float,
    descuento_total_shown: float,
    total_neto_shown: float,
    pdf_path: str,
    items_base: list[dict],
    items_shown: list[dict],
) -> int:
    """
    items_base: items tal como están en self.items (moneda base interna)
    items_shown: items_pdf tal como los construyes (ya convertidos para mostrar/PDF)
    Deben estar en el mismo orden / mismo largo.
    """
    if len(items_base) != len(items_shown):
        raise ValueError("items_base y items_shown deben tener el mismo tamaño")

    cur = con.execute(
        """
        INSERT INTO quotes(
            country_code, quote_no, created_at,
            cliente, cedula, telefono,
            currency_shown, tasa_shown,
            subtotal_bruto_base, descuento_total_base, total_neto_base,
            subtotal_bruto_shown, descuento_total_shown, total_neto_shown,
            pdf_path, deleted_at
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)
        """,
        (
            country_code, quote_no, created_at,
            cliente, cedula, telefono,
            currency_shown, tasa_shown,
            float(subtotal_bruto_base), float(descuento_total_base), float(total_neto_base),
            float(subtotal_bruto_shown), float(descuento_total_shown), float(total_neto_shown),
            pdf_path,
        ),
    )
    quote_id = int(cur.lastrowid)

    rows: list[tuple[Any, ...]] = []
    for b, s in zip(items_base, items_shown):
        rows.append((
            quote_id,
            str(b.get("codigo") or ""),
            str(b.get("producto") or ""),
            str(b.get("categoria") or ""),
            str(b.get("fragancia") or ""),
            str(b.get("observacion") or ""),
            float(b.get("cantidad") or 0.0),

            float(b.get("precio") or 0.0),
            float(b.get("subtotal_base") or 0.0),
            (b.get("descuento_mode") or None),
            float(b.get("descuento_pct") or 0.0),
            float(b.get("descuento_monto") or 0.0),
            float(b.get("total") or 0.0),
            (None if b.get("precio_override") is None else float(b.get("precio_override") or 0.0)),
            (b.get("precio_tier") or None),

            float(s.get("precio") or 0.0),
            float(s.get("subtotal") or 0.0),
            float(s.get("descuento") or 0.0),
            float(s.get("total") or 0.0),
        ))

    # 🔧 FIX: 19 columnas => 19 placeholders
    con.executemany(
        """
        INSERT INTO quote_items(
            quote_id,
            codigo, producto, categoria, fragancia, observacion,
            cantidad,

            precio_base, subtotal_base,
            descuento_mode, descuento_pct, descuento_monto_base,
            total_base,
            precio_override_base, precio_tier,

            precio_shown, subtotal_shown, descuento_monto_shown, total_shown
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        rows,
    )
    return quote_id


def soft_delete_quote(con: sqlite3.Connection, quote_id: int, deleted_at_iso: str) -> None:
    con.execute(
        "UPDATE quotes SET deleted_at = ? WHERE id = ?",
        (deleted_at_iso, int(quote_id)),
    )


def list_quotes(
    con: sqlite3.Connection,
    *,
    search_text: str = "",
    contains_product: str = "",
    include_deleted: bool = False,
    limit: int = 200,
    offset: int = 0,
) -> tuple[list[dict], int]:
    st = (search_text or "").strip()
    cp = (contains_product or "").strip()

    where = []
    params: list[Any] = []

    if not include_deleted:
        where.append("q.deleted_at IS NULL")

    if st:
        where.append("(q.quote_no LIKE ? OR q.cliente LIKE ? OR q.cedula LIKE ? OR q.telefono LIKE ?)")
        like = f"%{st}%"
        params.extend([like, like, like, like])

    if cp:
        where.append("""
            EXISTS (
                SELECT 1
                FROM quote_items qi
                WHERE qi.quote_id = q.id
                  AND (qi.codigo LIKE ? OR qi.producto LIKE ?)
            )
        """)
        likep = f"%{cp}%"
        params.extend([likep, likep])

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    total = con.execute(
        f"SELECT COUNT(*) AS n FROM quotes q {where_sql}",
        tuple(params),
    ).fetchone()["n"]

    rows = con.execute(
        f"""
        SELECT
            q.id,
            q.created_at,
            q.quote_no,
            q.cliente,
            q.cedula,
            q.telefono,
            q.total_neto_shown AS total_shown,
            q.currency_shown,
            q.pdf_path,
            q.deleted_at,
            (SELECT COUNT(*) FROM quote_items qi WHERE qi.quote_id = q.id) AS items_count
        FROM quotes q
        {where_sql}
        ORDER BY q.created_at DESC
        LIMIT ? OFFSET ?
        """,
        tuple(params + [int(limit), int(offset)]),
    ).fetchall()

    return [dict(r) for r in rows], int(total)


def get_quote_header(con: sqlite3.Connection, quote_id: int) -> dict:
    r = con.execute("SELECT * FROM quotes WHERE id = ?", (int(quote_id),)).fetchone()
    if not r:
        raise KeyError(f"Cotización no encontrada: {quote_id}")
    return dict(r)


def get_quote_items(con: sqlite3.Connection, quote_id: int) -> tuple[list[dict], list[dict]]:
    rows = con.execute(
        "SELECT * FROM quote_items WHERE quote_id = ? ORDER BY id ASC",
        (int(quote_id),),
    ).fetchall()

    base_items: list[dict] = []
    shown_items: list[dict] = []

    for r in rows:
        d = dict(r)

        base_items.append({
            "codigo": d.get("codigo", ""),
            "producto": d.get("producto", ""),
            "categoria": d.get("categoria", ""),
            "fragancia": d.get("fragancia", ""),
            "observacion": d.get("observacion", ""),
            "cantidad": d.get("cantidad", 0.0),

            "precio": d.get("precio_base", 0.0),
            "subtotal_base": d.get("subtotal_base", 0.0),

            "descuento_mode": d.get("descuento_mode") or None,
            "descuento_pct": d.get("descuento_pct", 0.0),
            "descuento_monto": d.get("descuento_monto_base", 0.0),
            "total": d.get("total_base", 0.0),

            "precio_override": d.get("precio_override_base", None),
            "precio_tier": d.get("precio_tier", None),
        })

        shown_items.append({
            "codigo": d.get("codigo", ""),
            "producto": d.get("producto", ""),
            "categoria": d.get("categoria", ""),
            "fragancia": d.get("fragancia", ""),
            "observacion": d.get("observacion", ""),
            "cantidad": d.get("cantidad", 0.0),

            "precio": d.get("precio_shown", 0.0),
            "subtotal": d.get("subtotal_shown", 0.0),
            "descuento": d.get("descuento_monto_shown", 0.0),
            "total": d.get("total_shown", 0.0),
        })

    return base_items, shown_items
