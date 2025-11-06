# src/utils.py
import math
from .config import APP_CURRENCY

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

def fmt_money_ui(n: float) -> str:
    n = nz(n, 0.0)
    if APP_CURRENCY == "PEN": return f"S/ {n:0.2f}"
    if APP_CURRENCY == "USD": return f"$ {n:0.2f}"
    return f"₲{n:0.2f}"

def fmt_money_pdf(n: float) -> str:
    n = nz(n, 0.0)
    if APP_CURRENCY == "PEN": return f"S/. {n:0.2f}"
    if APP_CURRENCY == "USD": return f"$ {n:0.2f}"
    return f"Gs. {n:0.2f}"

def format_grams(g: float) -> str:
    if abs(g - round(g)) < 1e-9:
        return f"{int(round(g))} g"
    return f"{g:.1f} g"
