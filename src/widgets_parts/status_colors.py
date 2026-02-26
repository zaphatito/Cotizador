# src/widgets_parts/status_colors.py
from __future__ import annotations

from PySide6.QtGui import QColor

from sqlModels.db import connect, ensure_schema, tx
from sqlModels.quote_statuses_repo import (
    DEFAULT_QUOTE_STATUSES,
    get_quote_statuses_cached,
    replace_quote_statuses,
)
from sqlModels.quotes_repo import normalize_status
from ..db_path import resolve_db_path

DEFAULT_STATUS_BG_HEX: dict[str, str] = {
    str(r.get("code") or ""): str(r.get("color_hex") or "#5B708E")
    for r in DEFAULT_QUOTE_STATUSES
}

STATUS_COLOR_ORDER: tuple[str, ...] = tuple(DEFAULT_STATUS_BG_HEX.keys())

STATUS_BG_COLORS: dict[str, QColor] = {}
_STATUS_COLORS_LOADED = False


def _db_conn():
    con = connect(resolve_db_path())
    ensure_schema(con)
    return con


def _normalize_hex_color(value, *, fallback: str = "#5B708E") -> str:
    c = QColor(str(value or "").strip())
    if c.isValid():
        return c.name(QColor.HexRgb).upper()
    fb = QColor(str(fallback or "#5B708E").strip())
    if fb.isValid():
        return fb.name(QColor.HexRgb).upper()
    return "#5B708E"


def status_color_setting_key(status: str | None) -> str:
    _ = status
    return ""


def get_default_status_color_hex_map() -> dict[str, str]:
    return {str(k): _normalize_hex_color(v) for k, v in DEFAULT_STATUS_BG_HEX.items()}


def _rows_from_db(*, force_reload: bool = False) -> list[dict]:
    return get_quote_statuses_cached(
        db_path=resolve_db_path(),
        force_reload=bool(force_reload),
    )


def _load_runtime_from_rows(rows: list[dict]) -> None:
    STATUS_BG_COLORS.clear()
    for r in rows or []:
        code = str((r or {}).get("code") or "").strip()
        if not code:
            continue
        hx = _normalize_hex_color((r or {}).get("color_hex"), fallback=DEFAULT_STATUS_BG_HEX.get(code, "#5B708E"))
        STATUS_BG_COLORS[code] = QColor(hx)
    global _STATUS_COLORS_LOADED
    _STATUS_COLORS_LOADED = True


def apply_status_color_hex_map(overrides: dict[str, str] | None) -> None:
    rows = _rows_from_db(force_reload=True)
    if not rows:
        _load_runtime_from_rows([])
        return
    merged_rows: list[dict] = []
    for r in rows:
        code = str(r.get("code") or "").strip()
        if not code:
            continue
        hx = str((overrides or {}).get(code) or r.get("color_hex") or "").strip()
        merged_rows.append(
            {
                "code": code,
                "label": str(r.get("label") or "").strip(),
                "color_hex": _normalize_hex_color(hx, fallback=str(r.get("color_hex") or "#5B708E")),
            }
        )

    con = None
    try:
        con = _db_conn()
        with tx(con):
            replace_quote_statuses(con, merged_rows)
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass
    _load_runtime_from_rows(_rows_from_db(force_reload=True))


def reload_status_colors_from_db() -> None:
    _load_runtime_from_rows(_rows_from_db(force_reload=True))


def save_status_colors_to_db_settings(values: dict[str, str] | None) -> dict[str, str]:
    apply_status_color_hex_map(values or {})
    return get_current_status_color_hex_map()


def save_status_colors_to_local_settings(values: dict[str, str] | None) -> dict[str, str]:
    return save_status_colors_to_db_settings(values)


def _ensure_status_colors_loaded() -> None:
    if _STATUS_COLORS_LOADED:
        return
    reload_status_colors_from_db()


def get_current_status_color_hex_map() -> dict[str, str]:
    _ensure_status_colors_loaded()
    out: dict[str, str] = {}
    for code, c in STATUS_BG_COLORS.items():
        if isinstance(c, QColor) and c.isValid():
            out[str(code)] = c.name(QColor.HexRgb).upper()
    return out


def _srgb_to_linear(x: float) -> float:
    if x <= 0.04045:
        return x / 12.92
    return ((x + 0.055) / 1.055) ** 2.4


def _rel_luminance(c: QColor) -> float:
    r = _srgb_to_linear(c.redF())
    g = _srgb_to_linear(c.greenF())
    b = _srgb_to_linear(c.blueF())
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def best_text_color_for_bg(bg: QColor) -> QColor:
    try:
        lbg = _rel_luminance(bg)
        contrast_white = (1.0 + 0.05) / (lbg + 0.05)
        contrast_black = (lbg + 0.05) / (0.0 + 0.05)
        return QColor("#FFFFFF") if contrast_white >= contrast_black else QColor("#000000")
    except Exception:
        return QColor("#000000")


def bg_for_status(status: str | None) -> QColor | None:
    st = normalize_status(status)
    if not st:
        return None
    _ensure_status_colors_loaded()
    return STATUS_BG_COLORS.get(str(st))
