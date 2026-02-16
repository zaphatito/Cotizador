# src/app_window_parts/ticket_actions.py
from __future__ import annotations

import os

from ..logging_setup import get_logger
from ..pricing import cantidad_para_mostrar
from ..quote_code import extract_quote_code_from_pdf_path
from ..ticketgen import (
    DEFAULT_PRINTER_NAME,
    DEFAULT_TICKET_WIDTH,
    OBS_MAX_LEN,
    build_ticket_text,
    write_ticket_cmd_for_pdf,
)

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


def generar_ticket_para_cotizacion(
    pdf_path: str,
    items_pdf: list[dict],
    *,
    quote_code: str = "",
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

        ticket_text = build_ticket_text(
            items_pdf,
            quote_number=code,
            cliente_nombre=cliente_nombre,
            width=width,
            qty_text_fn=cantidad_para_mostrar,
            obs_max_len=OBS_MAX_LEN,
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
