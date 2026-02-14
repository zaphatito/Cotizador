# src/ai/assistant/reports.py
from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass
from typing import Optional, Tuple

from ...db_path import resolve_db_path
from ...logging_setup import get_logger
from sqlModels.db import connect, ensure_schema

log = get_logger(__name__)

_CURRENCY_MAP = {
    "soles": "PEN",
    "sol": "PEN",
    "pen": "PEN",
    "usd": "USD",
    "dolar": "USD",
    "dólar": "USD",
    "dolares": "USD",
    "dólares": "USD",
    "pyg": "PYG",
    "guarani": "PYG",
    "guaranies": "PYG",
    "guaraníes": "PYG",
    "ves": "VES",
    "bolivar": "VES",
    "bolívar": "VES",
    "bolivares": "VES",
    "bolívares": "VES",
    "ars": "ARS",
    "brl": "BRL",
    "bob": "BOB",
}

@dataclass
class ReportSpec:
    kind: str = "sales_summary"       # top_products | sales_by_day | sales_by_payment | sales_by_status | top_clients | tiers_usage | low_stock | sales_summary
    metric: str = "qty"               # qty | revenue (para top_products)
    limit: int = 20
    date_from: Optional[str] = None   # ISO inclusive (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)
    date_to: Optional[str] = None     # ISO inclusive (YYYY-MM-DD)
    currency: Optional[str] = None
    country_code: Optional[str] = None
    stock_threshold: float = 0.0


def report_text_from_db(user_query: str) -> str:
    spec = parse_report_query(user_query or "")
    db_path = resolve_db_path()
    con = connect(db_path)
    ensure_schema(con)
    try:
        return run_report(con, spec)
    finally:
        con.close()


def parse_report_query(text: str) -> ReportSpec:
    q = (text or "").strip()
    ql = _clean_spaces(q.lower())

    spec = ReportSpec()

    # limit: "top 50", "top50", "20 más vendidos"
    m = re.search(r"\btop\s*(\d{1,3})\b", ql)
    if not m:
        m = re.search(r"\b(\d{1,3})\s+(m[aá]s\s+vendid|vendid|productos)\b", ql)
    if m:
        try:
            spec.limit = max(5, min(int(m.group(1)), 200))
        except Exception:
            pass

    # currency detection
    spec.currency = _detect_currency(ql)

    # date range (simple)
    df, dt = _detect_date_range(ql)
    spec.date_from, spec.date_to = df, dt

    # country_code "PE", "PY", "VE"
    mcc = re.search(r"\b(pe|py|ve)\b", ql)
    if mcc:
        spec.country_code = mcc.group(1).upper()

    # stock threshold: "stock bajo 5" / "stock <= 3"
    if re.search(r"\b(stock|inventario|agotad)\b", ql):
        spec.kind = "low_stock"
        mth = re.search(r"(?:stock\s*(?:bajo|<=|<)\s*|<=\s*|<\s*)(\d+(?:\.\d+)?)", ql)
        if mth:
            try:
                spec.stock_threshold = float(mth.group(1))
            except Exception:
                spec.stock_threshold = 0.0
        else:
            spec.stock_threshold = 0.0
        return spec

    # routing by keywords
    if re.search(r"(m[aá]s\s+vendid|top\s+productos|ranking\s+productos|productos\s+vendid|ventas?\s+por\s+producto)", ql):
        spec.kind = "top_products"
        spec.metric = "revenue" if re.search(r"(monto|total|ingreso|factur|recaud)", ql) else "qty"
        return spec

    if re.search(r"(ventas?\s+por\s+d[ií]a|por\s+d[ií]a|diari[oa]|historial\s+diario)", ql):
        spec.kind = "sales_by_day"
        return spec

    if re.search(r"(m[eé]todo\s+de\s+pago|por\s+pago|pagos?\s+por)", ql):
        spec.kind = "sales_by_payment"
        return spec

    if re.search(r"\b(estado|pagad|pendient|por\s+pagar|anulad)\b", ql):
        spec.kind = "sales_by_status"
        return spec

    if re.search(r"(top\s+clientes|clientes?\s+m[aá]s|ranking\s+clientes)", ql):
        spec.kind = "top_clients"
        return spec

    if re.search(r"(tier|precio_tier|oferta|minimo|m[aá]ximo|unitario)\b", ql):
        spec.kind = "tiers_usage"
        return spec

    # default
    spec.kind = "sales_summary"
    return spec


def run_report(con, spec: ReportSpec) -> str:
    # normalize dates to bounds
    start_iso, end_excl_iso = _to_bounds(spec.date_from, spec.date_to)
    params_common = {
        "start": start_iso,
        "end": end_excl_iso,
        "currency": spec.currency,
        "cc": spec.country_code,
    }

    if spec.kind == "top_products":
        return _top_products(con, params_common, limit=spec.limit, metric=spec.metric)
    if spec.kind == "sales_by_day":
        return _sales_by_day(con, params_common)
    if spec.kind == "sales_by_payment":
        return _sales_by_payment(con, params_common, limit=spec.limit)
    if spec.kind == "sales_by_status":
        return _sales_by_status(con, params_common)
    if spec.kind == "top_clients":
        return _top_clients(con, params_common, limit=min(spec.limit, 50))
    if spec.kind == "tiers_usage":
        return _tiers_usage(con, params_common)
    if spec.kind == "low_stock":
        return _low_stock(con, threshold=spec.stock_threshold, limit=spec.limit)

    return _sales_summary(con, params_common)


def _base_where(params_common: dict) -> Tuple[str, list]:
    where = ["q.deleted_at IS NULL"]
    params: list = []

    if params_common.get("cc"):
        where.append("q.country_code = ?")
        params.append(params_common["cc"])

    if params_common.get("currency"):
        where.append("q.currency_shown = ?")
        params.append(params_common["currency"])

    if params_common.get("start"):
        where.append("q.created_at >= ?")
        params.append(params_common["start"])

    if params_common.get("end"):
        where.append("q.created_at < ?")
        params.append(params_common["end"])

    return " AND ".join(where), params


def _sales_summary(con, params_common: dict) -> str:
    where, params = _base_where(params_common)
    sql = f"""
    SELECT q.currency_shown AS moneda,
           COUNT(*) AS n,
           SUM(q.total_neto_shown) AS total
    FROM quotes q
    WHERE {where}
    GROUP BY q.currency_shown
    ORDER BY total DESC
    """
    rows = con.execute(sql, params).fetchall()
    if not rows:
        return "No hay datos para ese período."

    lines = ["Resumen de ventas:"]
    for r in rows:
        moneda = r[0] or ""
        n = int(r[1] or 0)
        total = float(r[2] or 0.0)
        lines.append(f"• {moneda}: {n} cotizaciones — {total:.2f}")
    return "\n".join(lines)


def _sales_by_day(con, params_common: dict) -> str:
    where, params = _base_where(params_common)
    sql = f"""
    SELECT substr(q.created_at, 1, 10) AS dia,
           COUNT(*) AS n,
           SUM(q.total_neto_shown) AS total
    FROM quotes q
    WHERE {where}
    GROUP BY dia
    ORDER BY dia ASC
    """
    rows = con.execute(sql, params).fetchall()
    if not rows:
        return "No hay datos para ese período."

    lines = ["Ventas por día:"]
    for dia, n, total in rows:
        lines.append(f"• {dia}: {int(n or 0)} — {float(total or 0.0):.2f}")
    return "\n".join(lines)


def _sales_by_payment(con, params_common: dict, *, limit: int = 30) -> str:
    where, params = _base_where(params_common)
    sql = f"""
    SELECT COALESCE(NULLIF(trim(q.metodo_pago), ''), '(vacío)') AS pago,
           COUNT(*) AS n,
           SUM(q.total_neto_shown) AS total
    FROM quotes q
    WHERE {where}
    GROUP BY pago
    ORDER BY total DESC
    LIMIT ?
    """
    rows = con.execute(sql, params + [int(limit)]).fetchall()
    if not rows:
        return "No hay datos para ese período."

    lines = ["Ventas por método de pago:"]
    for pago, n, total in rows:
        lines.append(f"• {pago}: {int(n or 0)} — {float(total or 0.0):.2f}")
    return "\n".join(lines)


def _sales_by_status(con, params_common: dict) -> str:
    where, params = _base_where(params_common)
    sql = f"""
    SELECT COALESCE(NULLIF(trim(q.estado), ''), '(vacío)') AS estado,
           COUNT(*) AS n,
           SUM(q.total_neto_shown) AS total
    FROM quotes q
    WHERE {where}
    GROUP BY estado
    ORDER BY n DESC
    """
    rows = con.execute(sql, params).fetchall()
    if not rows:
        return "No hay datos para ese período."

    lines = ["Ventas por estado:"]
    for estado, n, total in rows:
        lines.append(f"• {estado}: {int(n or 0)} — {float(total or 0.0):.2f}")
    return "\n".join(lines)


def _top_clients(con, params_common: dict, *, limit: int = 20) -> str:
    where, params = _base_where(params_common)
    sql = f"""
    SELECT q.cliente AS cliente,
           COUNT(*) AS n,
           SUM(q.total_neto_shown) AS total
    FROM quotes q
    WHERE {where}
    GROUP BY q.cliente
    ORDER BY total DESC
    LIMIT ?
    """
    rows = con.execute(sql, params + [int(limit)]).fetchall()
    if not rows:
        return "No hay datos para ese período."

    lines = ["Top clientes:"]
    for i, (cliente, n, total) in enumerate(rows, 1):
        lines.append(f"{i}) {cliente} — {int(n or 0)} cotiz. — {float(total or 0.0):.2f}")
    return "\n".join(lines)


def _top_products(con, params_common: dict, *, limit: int = 20, metric: str = "qty") -> str:
    where, params = _base_where(params_common)
    order = "qty DESC" if metric == "qty" else "total DESC"

    sql = f"""
    SELECT COALESCE(NULLIF(trim(it.codigo), ''), '(sin código)') AS codigo,
           COALESCE(NULLIF(trim(it.producto), ''), NULLIF(trim(it.fragancia), ''), '') AS producto,
           SUM(it.cantidad) AS qty,
           SUM(it.total_shown) AS total
    FROM quote_items it
    JOIN quotes q ON q.id = it.quote_id
    WHERE {where}
    GROUP BY codigo
    ORDER BY {order}
    LIMIT ?
    """
    rows = con.execute(sql, params + [int(limit)]).fetchall()
    if not rows:
        return "No hay ítems para ese período."

    title = "Productos más vendidos (cantidad):" if metric == "qty" else "Productos más vendidos (monto):"
    lines = [title]
    for i, (codigo, producto, qty, total) in enumerate(rows, 1):
        lines.append(f"{i}) {codigo} — {producto} — {float(qty or 0.0):g} — {float(total or 0.0):.2f}")
    return "\n".join(lines)


def _tiers_usage(con, params_common: dict) -> str:
    where, params = _base_where(params_common)
    sql = f"""
    SELECT COALESCE(NULLIF(trim(it.precio_tier), ''), '(vacío)') AS tier,
           COUNT(*) AS n,
           SUM(it.cantidad) AS qty
    FROM quote_items it
    JOIN quotes q ON q.id = it.quote_id
    WHERE {where}
    GROUP BY tier
    ORDER BY n DESC
    """
    rows = con.execute(sql, params).fetchall()
    if not rows:
        return "No hay datos para ese período."

    lines = ["Uso de precio_tier:"]
    for tier, n, qty in rows:
        lines.append(f"• {tier}: {int(n or 0)} ítems — {float(qty or 0.0):g} unidades")
    return "\n".join(lines)


def _low_stock(con, *, threshold: float = 0.0, limit: int = 20) -> str:
    # Si threshold=0: muestra los que están <= 0 (agotados) por defecto
    thr = float(threshold)
    sql = """
    SELECT id, nombre, categoria, cantidad_disponible
    FROM products_current
    WHERE cantidad_disponible <= ?
    ORDER BY cantidad_disponible ASC
    LIMIT ?
    """
    rows = con.execute(sql, [thr, int(limit)]).fetchall()
    if not rows:
        return f"No hay productos con stock <= {thr:g}."

    lines = [f"Productos con stock <= {thr:g}:"]
    for i, (pid, nombre, cat, qty) in enumerate(rows, 1):
        lines.append(f"{i}) {pid} — {nombre} — {cat} — stock={float(qty or 0.0):g}")
    return "\n".join(lines)


def _clean_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _detect_currency(ql: str) -> Optional[str]:
    # tokens directos
    for tok in ["PEN", "USD", "PYG", "VES", "ARS", "BRL", "BOB"]:
        if re.search(rf"\b{tok.lower()}\b", ql):
            return tok
    # palabras
    for k, v in _CURRENCY_MAP.items():
        if re.search(rf"\b{re.escape(k)}\b", ql):
            return v
    return None


def _detect_date_range(ql: str) -> Tuple[Optional[str], Optional[str]]:
    today = _dt.date.today()

    # explícito: 2026-02-01 a 2026-02-10
    m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b.*\b(\d{4}-\d{2}-\d{2})\b", ql)
    if m:
        return m.group(1), m.group(2)

    if re.search(r"\bhoy\b", ql):
        d = today.isoformat()
        return d, d

    if re.search(r"\bayer\b", ql):
        d = (today - _dt.timedelta(days=1)).isoformat()
        return d, d

    if re.search(r"\b(semana|7\s*d[ií]as|ultimos?\s+7)\b", ql):
        return (today - _dt.timedelta(days=6)).isoformat(), today.isoformat()

    if re.search(r"\b(30\s*d[ií]as|ultimos?\s+30)\b", ql):
        return (today - _dt.timedelta(days=29)).isoformat(), today.isoformat()

    # por defecto: mes actual
    first = today.replace(day=1)
    # end inclusive = último día del mes
    if first.month == 12:
        next_month = _dt.date(first.year + 1, 1, 1)
    else:
        next_month = _dt.date(first.year, first.month + 1, 1)
    last = next_month - _dt.timedelta(days=1)
    return first.isoformat(), last.isoformat()


def _to_bounds(date_from: Optional[str], date_to: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if not date_from or not date_to:
        return None, None

    # start inclusive at 00:00:00
    start = f"{date_from} 00:00:00"

    # end exclusive = (date_to + 1 day) 00:00:00
    try:
        y, m, d = [int(x) for x in date_to.split("-")]
        end_date = _dt.date(y, m, d) + _dt.timedelta(days=1)
        end_excl = f"{end_date.isoformat()} 00:00:00"
    except Exception:
        end_excl = None
    return start, end_excl


    