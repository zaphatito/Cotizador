# src/app_window_parts/ticket_actions.py
from __future__ import annotations

import os
import re

from ..logging_setup import get_logger
from ..pricing import cantidad_para_mostrar
from ..ticketgen import (
    DEFAULT_PRINTER_NAME,
    DEFAULT_TICKET_WIDTH,
    build_ticket_text,
    write_ticket_cmd_for_pdf,
)

log = get_logger(__name__)


def _quote_number_from_pdf_path(pdf_path: str) -> str:
    """
    pdfgen genera: C-<CC>-<0000001>_<cliente>.pdf
    Devuelve solo el correlativo numérico (0000001) si lo encuentra.
    """
    base = os.path.splitext(os.path.basename(pdf_path))[0]  # sin .pdf
    m = re.match(r"^C-[A-Z]{2}-(\d+)", base, flags=re.IGNORECASE)
    if m:
        return m.group(1)
    # fallback: todo lo que va tras "C-" hasta "_" (por si cambia formato)
    if base.startswith("C-"):
        part = base[2:].split("_", 1)[0]
        # si es CC-0000001, quedarnos con lo último
        if "-" in part:
            return part.split("-")[-1]
        return part
    return ""


def generar_ticket_para_cotizacion(
    pdf_path: str,
    items_pdf: list[dict],
    *,
    printer_name: str = DEFAULT_PRINTER_NAME,
    width: int = DEFAULT_TICKET_WIDTH,
    top_mm: float = 0.0,
    bottom_mm: float = 5.0,
    cut_mode: str = "full_save",
) -> dict[str, str]:
    """
    Genera el .cmd en <cotizaciones>/tickets/<base>.IMPRIMIR_TICKET.cmd
    """
    try:
        quote_number = _quote_number_from_pdf_path(pdf_path)

        ticket_text = build_ticket_text(
            items_pdf,
            quote_number=quote_number,
            width=width,
            qty_text_fn=cantidad_para_mostrar,
            obs_max_len=12,
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
