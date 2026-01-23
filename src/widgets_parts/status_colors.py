# src/widgets_parts/status_colors.py
from __future__ import annotations

from PySide6.QtGui import QColor

from sqlModels.quotes_repo import (
    STATUS_PAGADO,
    STATUS_POR_PAGAR,
    STATUS_PENDIENTE,
    STATUS_NO_APLICA,
    normalize_status,
)

# ✅ ÚNICA FUENTE DE COLORES (cámbialos aquí y se reflejan en TODO)
STATUS_BG_COLORS: dict[str, QColor] = {
    STATUS_PAGADO: QColor("#06863B"),     # verde vivo
    STATUS_POR_PAGAR: QColor("#ECD060"),  # amarillo vivo
    STATUS_PENDIENTE: QColor("#E67E22"),  # naranja vivo
    STATUS_NO_APLICA: QColor("#811307"),  # rojo vivo
}


def _srgb_to_linear(x: float) -> float:
    # x en [0..1]
    if x <= 0.04045:
        return x / 12.92
    return ((x + 0.055) / 1.055) ** 2.4


def _rel_luminance(c: QColor) -> float:
    # luminancia relativa (WCAG)
    r = _srgb_to_linear(c.redF())
    g = _srgb_to_linear(c.greenF())
    b = _srgb_to_linear(c.blueF())
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def best_text_color_for_bg(bg: QColor) -> QColor:
    """
    Elige negro/blanco según mejor contraste con el fondo (WCAG).
    """
    try:
        Lbg = _rel_luminance(bg)
        # contraste con blanco (L=1) y negro (L=0)
        contrast_white = (1.0 + 0.05) / (Lbg + 0.05)
        contrast_black = (Lbg + 0.05) / (0.0 + 0.05)
        return QColor("#FFFFFF") if contrast_white >= contrast_black else QColor("#000000")
    except Exception:
        return QColor("#000000")


def bg_for_status(status: str | None) -> QColor | None:
    st = normalize_status(status)
    if not st:
        return None
    return STATUS_BG_COLORS.get(st)
