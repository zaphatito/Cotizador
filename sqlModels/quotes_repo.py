# sqlModels/quotes_repo.py
from __future__ import annotations

import sqlite3
from typing import Any, Optional


# =========================
# Estado (guardado en quotes.estado)
# =========================
STATUS_PAGADO = "PAGADO"
STATUS_POR_PAGAR = "POR_PAGAR"
STATUS_PENDIENTE = "PENDIENTE"
STATUS_NO_APLICA = "NO_APLICA"

ALL_STATUSES = {STATUS_PAGADO, STATUS_POR_PAGAR, STATUS_PENDIENTE, STATUS_NO_APLICA}

STATUS_LABELS = {
    STATUS_PAGADO: "Pagado",
    STATUS_POR_PAGAR: "Por pagar",
    STATUS_PENDIENTE: "Pendiente",
    STATUS_NO_APLICA: "No aplica",
}


def normalize_status(value: str | None) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    u = s.upper().replace(" ", "_")

    aliases = {
        "PAGO": STATUS_PAGADO,
        "PAGADO": STATUS_PAGADO,
        "PAID": STATUS_PAGADO,
        "POR_PAGAR": STATUS_POR_PAGAR,
        "PORPAGAR": STATUS_POR_PAGAR,
        "POR-PAGAR": STATUS_POR_PAGAR,
        "PENDIENTE": STATUS_PENDIENTE,
        "PENDING": STATUS_PENDIENTE,
        "NO_APLICA": STATUS_NO_APLICA,
        "NOAPLICA": STATUS_NO_APLICA,
        "INACTIVA": STATUS_NO_APLICA,
        "INACTIVO": STATUS_NO_APLICA,
        "INACTIVE": STATUS_NO_APLICA,
    }
    u = aliases.get(u, u)
    return u if u in ALL_STATUSES else None


def status_label(status: str | None) -> str:
    st = normalize_status(status)
    if not st:
        return ""
    return STATUS_LABELS.get(st, st)


def _has_column(con: sqlite3.Connection, table: str, col: str) -> bool:
    try:
        rows = con.execute(f"PRAGMA table_info({table})").fetchall()
        cols = {str(r["name"]).lower() for r in rows}
        return col.lower() in cols
    except Exception:
        return False


def insert_quote(
    con: sqlite3.Connection,
    *,
    country_code: str,
    quote_no: str,
    created_at: str,
    cliente: str,
    cedula: str,
    telefono: str,
    metodo_pago: str = "",
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

    has_mp = _has_column(con, "quotes", "metodo_pago")
    has_estado = _has_column(con, "quotes", "estado")

    cols: list[str] = [
        "country_code", "quote_no", "created_at",
        "cliente", "cedula", "telefono",
        "currency_shown", "tasa_shown",
        "subtotal_bruto_base", "descuento_total_base", "total_neto_base",
        "subtotal_bruto_shown", "descuento_total_shown", "total_neto_shown",
        "pdf_path",
    ]
    vals: list[Any] = [
        country_code, quote_no, created_at,
        cliente, cedula, telefono,
        currency_shown, tasa_shown,
        float(subtotal_bruto_base), float(descuento_total_base), float(total_neto_base),
        float(subtotal_bruto_shown), float(descuento_total_shown), float(total_neto_shown),
        pdf_path,
    ]

    if has_mp:
        insert_pos = cols.index("telefono") + 1
        cols.insert(insert_pos, "metodo_pago")
        vals.insert(insert_pos, str(metodo_pago or ""))

    if has_estado:
        # por defecto sin estado (''), para no colorear
        insert_pos = cols.index("telefono") + 1
        if has_mp:
            insert_pos += 1
        cols.insert(insert_pos, "estado")
        vals.insert(insert_pos, "")

    placeholders = ",".join(["?"] * len(cols))
    sql = f"INSERT INTO quotes({', '.join(cols)}) VALUES({placeholders})"

    cur = con.execute(sql, tuple(vals))
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


def update_quote_payment(con: sqlite3.Connection, quote_id: int, metodo_pago: str) -> None:
    """Actualiza metodo_pago en la cabecera de una cotización (si la columna existe)."""
    if not _has_column(con, "quotes", "metodo_pago"):
        raise RuntimeError("La columna 'metodo_pago' no existe en la tabla 'quotes'.")
    con.execute(
        "UPDATE quotes SET metodo_pago = ? WHERE id = ?",
        (str(metodo_pago or ""), int(quote_id)),
    )


def update_quote_status(con: sqlite3.Connection, quote_id: int, estado: str | None) -> None:
    """Actualiza estado en la cabecera (si la columna existe). None => ''. """
    if not _has_column(con, "quotes", "estado"):
        raise RuntimeError("La columna 'estado' no existe en la tabla 'quotes'.")
    st = normalize_status(estado) or ""
    con.execute(
        "UPDATE quotes SET estado = ? WHERE id = ?",
        (st, int(quote_id)),
    )


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

    has_mp = _has_column(con, "quotes", "metodo_pago")
    has_estado = _has_column(con, "quotes", "estado")

    # “Buscar por cualquier columna” (incluye estado/pago/moneda/total/items/pdf/fecha)
    if st:
        like = f"%{st}%"
        pago_w = "q.metodo_pago LIKE ?" if has_mp else "0"
        estado_w = "q.estado LIKE ? OR REPLACE(q.estado,'_',' ') LIKE ?" if has_estado else "0"

        where.append(
            f"""
            (
                q.created_at LIKE ?
                OR q.quote_no LIKE ?
                OR q.cliente LIKE ?
                OR q.cedula LIKE ?
                OR q.telefono LIKE ?
                OR {pago_w}
                OR {estado_w}
                OR q.currency_shown LIKE ?
                OR q.pdf_path LIKE ?
                OR CAST(q.total_neto_shown AS TEXT) LIKE ?
                OR CAST((SELECT COUNT(*) FROM quote_items qi2 WHERE qi2.quote_id = q.id) AS TEXT) LIKE ?
            )
            """
        )

        params.extend([like, like, like, like, like, like])  # created_at..telefono
        if has_mp:
            params.append(like)  # metodo_pago
        if has_estado:
            params.extend([like, like])  # estado + estado con espacios
        params.extend([like, like, like, like])  # currency, pdf, total, items_count

    if cp:
        where.append(
            """
            EXISTS (
                SELECT 1
                FROM quote_items qi
                WHERE qi.quote_id = q.id
                  AND (qi.codigo LIKE ? OR qi.producto LIKE ?)
            )
            """
        )
        likep = f"%{cp}%"
        params.extend([likep, likep])

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    total = con.execute(
        f"SELECT COUNT(*) AS n FROM quotes q {where_sql}",
        tuple(params),
    ).fetchone()["n"]

    pago_expr = "q.metodo_pago" if has_mp else "'' AS metodo_pago"
    estado_expr = "q.estado" if has_estado else "'' AS estado"

    rows = con.execute(
        f"""
        SELECT
            q.id,
            q.created_at,
            q.quote_no,
            q.cliente,
            q.cedula,
            q.telefono,
            {estado_expr},
            {pago_expr},
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
