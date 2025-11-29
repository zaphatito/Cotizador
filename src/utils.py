# src/utils.py
import math
from .config import APP_CURRENCY, get_currency_context


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
    Devuelve el código de la moneda actualmente activa en el contexto
    (PEN, USD, ARS, VES, BOB, PYG, etc.). Si algo falla, cae a APP_CURRENCY.
    """
    try:
        cur, _, _ = get_currency_context()
        if cur:
            return cur
    except Exception:
        pass
    return APP_CURRENCY


def _symbol_ui(cur: str) -> str:
    """
    Símbolo para la UI según código de moneda.
    """
    c = (cur or "").upper()

    # Perú
    if c == "PEN":
        return "S/"

    # Dólar
    if c == "USD":
        return "$"

    # Peso argentino
    if c == "ARS":
        return "AR$"  # si prefieres, se puede cambiar a "AR$"

    # Guaraní paraguayo
    if c in ("PYG", "GS"):
        return "₲"

    # Bolívar venezolano
    if c in ("VEF", "VES"):
        return "Bs."

    # Boliviano
    if c == "BOB":
        return "Bs"
    
    # Real brasileño
    if c == "BRL":
        return "R$"

    # Fallback genérico
    return c


def _symbol_pdf(cur: str) -> str:
    """
    Símbolo para el PDF según código de moneda.
    """
    c = (cur or "").upper()

    # Perú
    if c == "PEN":
        return "S/."

    # Dólar
    if c == "USD":
        return "$"

    # Peso argentino
    if c == "ARS":
        return "AR$"  # o "AR$" si quieres distinguirlo

    # Guaraní paraguayo
    if c in ("PYG", "GS"):
        return "Gs."

    # Bolívar venezolano
    if c in ("VEF", "VES"):
        return "Bs."

    # Boliviano
    if c == "BOB":
        return "Bs"
    
    # Real brasileño
    if c == "BRL":
        return "R$"

    # Fallback genérico
    return c


def fmt_money_ui(n: float) -> str:
    """
    Formato para la UI, usando la MONEDA ACTUAL (no fija a APP_CURRENCY).
    Ej: "S/ 123.45", "$ 10.00", "Bs. 50.00", "₲10000.00", etc.
    """
    n = nz(n, 0.0)
    cur = _current_currency_code()
    sym = _symbol_ui(cur)
    return f"{sym} {n:0.2f}"


def fmt_money_pdf(n: float) -> str:
    """
    Formato para PDF, también usando la MONEDA ACTUAL.
    (Los montos ya deben venir en la moneda actual si se usó convert_from_base).
    Ej: "S/. 123.45", "Bs. 50.00", "Gs. 10000.00", etc.
    """
    n = nz(n, 0.0)
    cur = _current_currency_code()
    sym = _symbol_pdf(cur)
    return f"{sym} {n:0.2f}"


def format_grams(g: float) -> str:
    if abs(g - round(g)) < 1e-9:
        return f"{int(round(g))} g"
    return f"{g:.1f} g"
