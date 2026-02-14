# src/utils.py
from __future__ import annotations

import math
from .config import APP_CURRENCY, get_currency_context
from .currency import normalize_currency_code, symbol_ui, symbol_pdf


def to_float(val, default=0.0) -> float:
    try:
        if val is None:
            return default
        if isinstance(val, str):
            txt = val.strip().replace(",", "").replace(" ", "")
            if not txt:
                return default
            f = float(txt)
        else:
            f = float(val)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except Exception:
        return default


def nz(x, default=0.0):
    try:
        f = float(x)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except Exception:
        return default


def _current_currency_code() -> str:
    """
    Devuelve el código CANÓNICO de la moneda actualmente activa (PEN, USD, PYG...).
    Si algo falla, cae a APP_CURRENCY (también normalizado).
    """
    try:
        cur, _, _ = get_currency_context()
        cur = normalize_currency_code(cur or "")
        if cur:
            return cur
    except Exception:
        pass
    return normalize_currency_code(APP_CURRENCY or "")


def fmt_money_ui(n: float) -> str:
    """
    Formato para la UI usando la moneda actual (canónica).
    """
    n = nz(n, 0.0)
    cur = _current_currency_code()
    sym = symbol_ui(cur)
    return f"{sym} {n:0.2f}"


def fmt_money_pdf(n: float) -> str:
    """
    Formato para PDF usando la moneda actual (canónica).
    """
    n = nz(n, 0.0)
    cur = _current_currency_code()
    sym = symbol_pdf(cur)
    return f"{sym} {n:0.2f}"


def format_grams(g: float) -> str:
    if abs(g - round(g)) < 1e-9:
        return f"{int(round(g))} g"
    return f"{g:.1f} g"
