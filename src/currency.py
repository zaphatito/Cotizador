# src/currency.py
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional


@dataclass(frozen=True)
class CurrencySpec:
    code: str           # canónico ISO (PEN, USD, PYG, ...)
    ui_symbol: str      # UI
    pdf_symbol: str     # PDF


# --- Canónicos ---
CURRENCIES: dict[str, CurrencySpec] = {
    "PEN": CurrencySpec("PEN", "S/", "S/."),
    "USD": CurrencySpec("USD", "$", "$"),
    "ARS": CurrencySpec("ARS", "AR$", "AR$"),
    "PYG": CurrencySpec("PYG", "₲", "Gs."),
    "VES": CurrencySpec("VES", "Bs.", "Bs."),
    "BOB": CurrencySpec("BOB", "Bs", "Bs"),
    "BRL": CurrencySpec("BRL", "R$", "R$"),
}

# --- Alias de código -> canónico ---
CODE_ALIASES: dict[str, str] = {
    "SOL": "PEN",
    "SOLES": "PEN",
    "S/": "PEN",
    "S/.": "PEN",
    "GS": "PYG",      # opcional (si lo usas)
    "VEF": "VES",
    "BS": "VES",
    "BS.": "VES",
}

# --- Detección por texto (lenguaje natural) ---
# Nota: orden importa (prioridad).
_TEXT_RULES: list[tuple[re.Pattern, str]] = [
    # PEN
    (re.compile(r"(?:^|[\s,;])(?:pen|sol(?:es)?|s\/\.?|s\/)(?:$|[\s,;])", re.I), "PEN"),
    # USD
    (re.compile(r"(?:^|[\s,;])(?:usd|d[oó]lar(?:es)?)(?:$|[\s,;])", re.I), "USD"),
    # PYG
    (re.compile(r"(?:^|[\s,;])(?:pyg|gs\.?|guaran[ií](?:es)?)(?:$|[\s,;])", re.I), "PYG"),
    (re.compile(r"₲"), "PYG"),
    # VES
    (re.compile(r"(?:^|[\s,;])(?:ves|v?ef|bol[ií]var(?:es)?|bs\.?)(?:$|[\s,;])", re.I), "VES"),
    # BRL
    (re.compile(r"(?:^|[\s,;])(?:brl|r\$|real(?:es)?(?:\s+brasileñ[oa]s?)?)(?:$|[\s,;])", re.I), "BRL"),
    # ARS (ojo: “pesos” es ambiguo; por eso lo amarro a “argentino” o ARS/AR$)
    (re.compile(r"(?:^|[\s,;])(?:ars|ar\$|peso(?:s)?\s+argentino(?:s)?)(?:$|[\s,;])", re.I), "ARS"),
    # BOB
    (re.compile(r"(?:^|[\s,;])(?:bob|boliviano(?:s)?)(?:$|[\s,;])", re.I), "BOB"),
]


def normalize_currency_code(code: str) -> str:
    """
    Normaliza un código o alias a canónico (ISO).
    Ej: SOL -> PEN, GS -> PYG, VEF -> VES.
    """
    c = (code or "").strip().upper()
    if not c:
        return ""
    c = CODE_ALIASES.get(c, c)
    return c


def pick_currency_from_text(text: str, allowed: Iterable[str] | None = None) -> str:
    """
    Detecta moneda por texto (Soles, Dólares, Gs, etc) y devuelve código CANÓNICO.
    Si allowed se pasa, solo devuelve una moneda que esté permitida.
    """
    t = (text or "").strip()
    if not t:
        return ""

    allowed_set = set()
    if allowed is not None:
        allowed_set = {normalize_currency_code(x) for x in allowed if str(x or "").strip()}

    # 1) si el usuario escribió un código (o alias) explícito
    #    ejemplo: "en PEN", "moneda: SOL"
    tokens = re.findall(r"\b[A-Za-z]{2,5}\b", t)
    for tok in tokens:
        cand = normalize_currency_code(tok)
        if cand and (not allowed_set or cand in allowed_set):
            if cand in CURRENCIES:
                return cand

    # 2) reglas por lenguaje natural
    for pat, cur in _TEXT_RULES:
        if pat.search(t):
            cur2 = normalize_currency_code(cur)
            if not allowed_set or cur2 in allowed_set:
                return cur2

    # 3) '$' suelto es ambiguo: solo lo tomo si NO hay allowed, o si USD es la única opción razonable
    if "$" in t:
        if not allowed_set:
            return "USD"
        if "USD" in allowed_set and "ARS" not in allowed_set:
            return "USD"

    return ""


def symbol_ui(code: str) -> str:
    c = normalize_currency_code(code)
    spec = CURRENCIES.get(c)
    return spec.ui_symbol if spec else (c or "")


def symbol_pdf(code: str) -> str:
    c = normalize_currency_code(code)
    spec = CURRENCIES.get(c)
    return spec.pdf_symbol if spec else (c or "")
