# src/app_window_parts/ticket_actions.py
from __future__ import annotations

import os
import math

from ..config import APP_COUNTRY, CATS, STORE_ID
from ..logging_setup import get_logger
from ..pricing import cantidad_para_mostrar
from ..quote_code import extract_quote_code_from_pdf_path, format_quote_display_no
from ..ticketgen import (
    DEFAULT_PRINTER_NAME,
    DEFAULT_TICKET_WIDTH,
    OBS_MAX_LEN,
    build_ticket_text,
    write_ticket_cmd_for_pdf,
)
from ..utils import nz

log = get_logger(__name__)


def _quote_code_from_pdf_path(pdf_path: str) -> str:
    base = os.path.splitext(os.path.basename(pdf_path))[0]
    if not base:
        return ""

    code = extract_quote_code_from_pdf_path(pdf_path)
    if code:
        return code

    if base.startswith("C-"):
        return base[2:].split("_", 1)[0].strip()
    return ""


def _normalize_country(country: str) -> str:
    c = str(country or "").strip().upper()
    if c in ("PE", "PERU"):
        return "PERU"
    if c in ("PY", "PARAGUAY"):
        return "PARAGUAY"
    if c in ("VE", "VENEZUELA"):
        return "VENEZUELA"
    return c


def _country_from_quote_code(code: str) -> str:
    c = str(code or "").strip().upper()
    if not c:
        return ""
    prefix = c.split("-", 1)[0].strip()
    return _normalize_country(prefix)


def _fmt_qty(x: float) -> str:
    try:
        if math.isfinite(x) and math.isclose(x, round(x), abs_tol=1e-9):
            return str(int(round(x)))
    except Exception:
        pass
    return f"{float(nz(x, 0.0)):.3f}".rstrip("0").rstrip(".")


def _peru_header_extra_lines(items_pdf: list[dict]) -> list[str]:
    total_botellas = 0.0
    total_esencias_g = 0.0

    for it in (items_pdf or []):
        try:
            cat = str(it.get("categoria") or "").strip().upper()
            qty = float(nz(it.get("cantidad"), 0.0))
            if cat == "BOTELLAS":
                total_botellas += qty
            if cat in CATS:
                # En Peru la cantidad de esencias se maneja en KG para preview;
                # en ticket la mostramos en gramos igual que la previsualizacion.
                total_esencias_g += (qty * 1000.0)
        except Exception:
            continue

    out: list[str] = []
    if total_botellas > 0:
        out.append(f"Total de Botellas: {_fmt_qty(total_botellas)}")
    if total_esencias_g > 0:
        out.append(f"Total de Esencias: {_fmt_qty(total_esencias_g)} g")
    return out


def _ticket_total_amount(items_pdf: list[dict]) -> float:
    total = 0.0
    for it in (items_pdf or []):
        try:
            total += float(nz(it.get("total"), nz(it.get("subtotal"), 0.0)))
        except Exception:
            continue
    return float(total)


def generar_ticket_para_cotizacion(
    pdf_path: str,
    items_pdf: list[dict],
    *,
    quote_code: str = "",
    country: str = "",
    cliente_nombre: str = "",
    printer_name: str = DEFAULT_PRINTER_NAME,
    width: int = DEFAULT_TICKET_WIDTH,
    top_mm: float = 0.0,
    bottom_mm: float = 10.0,
    cut_mode: str = "full_feed",
) -> dict[str, str]:
    """
    Genera el .cmd en <cotizaciones>/tickets/<base>.IMPRIMIR_TICKET.cmd
    """
    try:
        code = (quote_code or "").strip() or _quote_code_from_pdf_path(pdf_path)
        ticket_quote_no = format_quote_display_no(
            quote_code=code,
            store_id=STORE_ID,
            width=7,
        )
        country_norm = _normalize_country(country) or _country_from_quote_code(code) or _normalize_country(APP_COUNTRY)

        header_extra_lines: list[str] = []
        if country_norm == "PERU":
            header_extra_lines = _peru_header_extra_lines(items_pdf)

        total_general = _ticket_total_amount(items_pdf)
        total_general_text = f"{float(total_general):.2f}"

        ticket_text = build_ticket_text(
            items_pdf,
            quote_number=ticket_quote_no,
            cliente_nombre=cliente_nombre,
            width=width,
            qty_text_fn=cantidad_para_mostrar,
            obs_max_len=OBS_MAX_LEN,
            header_extra_lines=header_extra_lines,
            total_general_text=total_general_text,
        )
        if not ticket_text.strip():
            return {}

        out = write_ticket_cmd_for_pdf(
            pdf_path,
            ticket_text,
            width=width,
            printer_name=printer_name,
            top_mm=top_mm,
            bottom_mm=bottom_mm,
            cut_mode=cut_mode,
        )
        log.info("Ticket listo: %s", out.get("ticket_cmd"))
        return out

    except Exception:
        log.exception("Error generando ticket para cotizacion")
        return {}
