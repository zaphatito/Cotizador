# src/ai/assistant/parsing.py
from __future__ import annotations

import re
from typing import Optional, Any

from ...logging_setup import get_logger

log = get_logger(__name__)


# =====================================================
# Helpers existentes (los mantengo)
# =====================================================
def extract_choice_number(text: str) -> Optional[int]:
    t = (text or "").strip()
    if not t:
        return None
    m = re.search(r"\b([1-9])\b", t)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def extract_code_like(text: str) -> Optional[str]:
    t = (text or "").strip()
    if not t:
        return None
    m = re.search(r"\b([A-Za-z]{1,6}\d{1,8}|\d{3,8})\b", t)
    if not m:
        return None
    return str(m.group(1) or "").strip().upper() or None


def find_currency_in_text(text: str, currencies: list[str]) -> str:
    """
    Detecta moneda por:
    - códigos (PEN, USD, etc.)
    - sinónimos comunes: "Soles" => PEN (o SOL), "Dólares" => USD
    """
    t = (text or "").strip()
    if not t:
        return ""

    t_low = t.lower()
    curset = {str(c or "").strip().upper() for c in (currencies or []) if str(c or "").strip()}

    def _pick(*prefs: str) -> str:
        prefs_u = [str(p or "").strip().upper() for p in prefs if str(p or "").strip()]
        if not prefs_u:
            return ""
        if curset:
            for p in prefs_u:
                if p in curset:
                    return p
            return ""
        return prefs_u[0]

    for c in (currencies or []):
        cu = str(c or "").strip().upper()
        if not cu:
            continue
        if re.search(rf"\b{re.escape(cu)}\b", t, flags=re.I):
            return cu

    if ("soles" in t_low) or re.search(r"\bsol\b", t_low) or ("s/." in t_low) or ("s/" in t_low):
        return _pick("PEN", "SOL") or ("PEN" if not curset else "")

    if ("dólar" in t_low) or ("dolar" in t_low) or ("dólares" in t_low) or ("dolares" in t_low) or re.search(r"\busd\b", t_low) or ("$" in t_low):
        return _pick("USD") or ("USD" if not curset else "")

    return ""


# =====================================================
# NUEVO: intención + status (para plan_builder)
# =====================================================
_STATUS_MAP = [
    (re.compile(r"\bpor\s+pagar\b", re.I), "POR_PAGAR"),
    (re.compile(r"\bpendiente(?:s)?\b", re.I), "PENDIENTE"),
    (re.compile(r"\bpagad[ao]s?\b", re.I), "PAGADO"),
    (re.compile(r"\bsin\s+estado\b", re.I), ""),
    (re.compile(r"\btodos?\b", re.I), ""),  # “todas las cotizaciones” -> sin filtro
]


def pick_status_from_text(text: str) -> Optional[str]:
    """
    Devuelve el status normalizado (POR_PAGAR / PENDIENTE / PAGADO / "" / None).
    None = no se detectó nada.
    ""   = “sin filtro” o “sin estado”, según contexto (lo decide el caller).
    """
    s = (text or "").strip()
    if not s:
        return None
    for rx, val in _STATUS_MAP:
        if rx.search(s):
            return val
    return None


_CODE_QTY_RE = re.compile(r"\b([A-Za-z]{1,6}\d{1,8}|\d{3,8})\b\s*(?:x|×)\s*([0-9]+(?:[.,][0-9]+)?)", re.I)


def _looks_like_items(text: str) -> bool:
    s = (text or "")
    if _CODE_QTY_RE.search(s):
        return True
    # señales de item sin x: "CH1104 precio oferta"
    if re.search(r"\b([A-Za-z]{1,6}\d{1,8})\b", s, flags=re.I) and re.search(r"\b(precio|oferta|promo|minimo|mínimo|maximo|máximo)\b", s, flags=re.I):
        return True
    return False


def route_intent(text: str) -> str:
    """
    Heurística rápida SOLO para hint/fallback, no reemplaza al LLM.
    Retorna: create_quote | list_quotes | top_clients | open_quote | edit_quote | chat | ""
    """
    s = (text or "").strip()
    if not s:
        return ""

    low = s.lower()

    # charla/saludos (para no disparar “create_quote vacío”)
    if re.fullmatch(r"(hola+|buenas+|hey+|hello+|ok+|gracias+|thanks+|:?\)+|:?\(+)", low.strip()):
        return "chat"

    # abrir / última
    if re.search(r"\b(abr[ie]r|abre|open)\b", low):
        return "open_quote"
    if re.search(r"\b(ultima|última|anterior|previa|hist[oó]rico)\b", low) and re.search(r"\b(cotizaci[oó]n|cotizacion)\b", low):
        return "open_quote"

    # editar/modificar una vieja (UI)
    if re.search(r"\b(edita|editar|modifica|modificar|cambia|cambiar)\b", low) and re.search(r"\b(cotizaci[oó]n|cotizacion)\b", low):
        return "edit_quote"
    if re.search(r"\b(reemplaza|quita|elimina|borra|agrega|añade|anade|suma|pon|setea)\b", low) and re.search(r"\b([A-Za-z]{1,6}\d{1,8}|\d{3,8})\b", s):
        # muchas veces esto es edición de la cotización abierta/pending
        return "edit_quote"

    # reportes / ranking
    if re.search(r"\b(top|ranking|rank|mejores|m[aá]s\s+vendidos|clientes)\b", low) and re.search(r"\b(top|ranking|clientes)\b", low):
        return "top_clients"

    # listar
    if re.search(r"\b(lista|listar|mu[eé]strame|ver|mira|dame)\b", low) and re.search(r"\b(cotizaci[oó]n|cotizacion|cotizaciones)\b", low):
        return "list_quotes"
    if re.search(r"\b(cotizaciones)\b", low) and pick_status_from_text(s) is not None:
        return "list_quotes"

    # crear con items
    if _looks_like_items(s):
        return "create_quote"
    if re.search(r"\b(crea|crear|arma|armar|nueva|nuevo)\b", low) and re.search(r"\b(cotizaci[oó]n|cotizacion)\b", low):
        return "create_quote"

    # si no sabemos, que el LLM decida; en fallback mejor chat
    return ""


# =====================================================
# Fallback usando plan_builder (sin circular import)
# =====================================================
def fallback_parse_plan(text: str, ctx: dict, *, country: str, force_action: str = "") -> dict:
    """
    Fallback local: reusa plan_builder (reglas) para NO inventar create_quote vacío.
    OJO: import local para evitar circular (parsing <-> plan_builder).
    """
    try:
        from .plan_builder import build_plan  # 👈 import local evita circular

        plan, _, _ = build_plan(
            text,
            ctx=ctx,
            planner=None,
            today_iso="",
            country=country,
            force_action=force_action,
        )
        return plan
    except Exception as e:
        log.warning("fallback_parse_plan failed: %s", e)
        return {
            "action": "chat",
            "args": {"text": "No pude interpretar eso. Escribe por ejemplo: 'Crea cotización para Juan con CH2156 x2'."},
            "needs_confirmation": False,
            "explanation": "Fallback extremo.",
        }


# =====================================================
# Ediciones (las dejo igual que las tenías)
# =====================================================
def parse_client_payment_edits(text: str) -> dict:
    s = text or ""
    out: dict[str, str] = {}

    def grab(pattern: str, key: str):
        m = re.search(pattern, s, flags=re.I)
        if m:
            v = (m.group(1) or "").strip()
            if v:
                out[key] = v

    grab(r"(?:cliente|nombre)\s*:\s*([^;\n]+)", "cliente")
    grab(r"(?:doc|dni|ruc|cedula)\s*:\s*([^;\n]+)", "cedula")
    grab(r"(?:tel|telefono)\s*:\s*([^;\n]+)", "telefono")
    grab(r"(?:pago|metodo\s*de\s*pago)\s*:\s*([^;\n]+)", "payment_method")

    m = re.search(r"(?:cambia|pon|setea|poner)\s+(?:el\s+)?pago\s*(?:a|por)?\s*([^;\n]+)", s, flags=re.I)
    if m:
        v = (m.group(1) or "").strip()
        if v:
            out["payment_method"] = v

    return out


def parse_item_edits(text: str) -> dict:
    """
    Parser de ediciones (sin confundir x0/x12 como códigos).
    """
    s = (text or "").strip()
    out = {"add": [], "remove": [], "set_price": [], "set_qty": [], "pct": [], "replace": []}
    if not s:
        return out

    for m in re.finditer(
        r"(?:reemplaza|cambia)\s+([A-Za-z]{1,6}\d{1,8}|\d{3,8})\s+por\s+([A-Za-z]{1,6}\d{1,8}|\d{3,8})",
        s, flags=re.I
    ):
        a = (m.group(1) or "").strip().upper()
        b = (m.group(2) or "").strip().upper()
        if a and b:
            out["replace"].append({"old": a, "new": b})

    for m in re.finditer(r"(?:quita|elimina|borra)\s+([A-Za-z]{1,6}\d{1,8}|\d{3,8})\b", s, flags=re.I):
        code = (m.group(1) or "").strip().upper()
        if code:
            out["remove"].append(code)

    for m in re.finditer(
        r"(?:cambia|pon|setea)\s+(?:el\s+)?precio\s+(?:de\s+)?([A-Za-z]{1,6}\d{1,8}|\d{3,8})\s*(?:a|=|:)?\s*([0-9]+(?:[.,][0-9]+)?)",
        s, flags=re.I
    ):
        code = (m.group(1) or "").strip().upper()
        price = (m.group(2) or "").strip()
        if code and price:
            out["set_price"].append({"code": code, "price": price})
    
    
    m = re.search(
        r"\b(?:cambia|cambiar)\s+([A-Za-z]{1,6}\d{1,8}|\d{3,8})\s*(?:a|=|:)\s*([0-9]+(?:[.,][0-9]+)?)\s*(?:g|gr|gramos?|ml|unid(?:ades)?|u)?\b",
        s, flags=re.I
    )
    if m:
        code = (m.group(1) or "").strip().upper()
        qty = (m.group(2) or "").strip()
        if code and qty:
            out["set_qty"].append({"code": code, "qty": qty})
            return out  # ya resolvimos esta intención
        

        
    for m in re.finditer(
        r"(?:cambia|pon|setea)\s+(?:la\s+)?(?:cantidad|qty)\s+(?:de\s+)?([A-Za-z]{1,6}\d{1,8}|\d{3,8})\s*(?:a|=|:)?\s*([0-9]+(?:[.,][0-9]+)?)",
        s, flags=re.I
    ):
        code = (m.group(1) or "").strip().upper()
        qty = (m.group(2) or "").strip()
        if code and qty:
            out["set_qty"].append({"code": code, "qty": qty})

    for m in re.finditer(
        r"(?:pon|setea|poner)\s+([A-Za-z]{1,6}\d{1,8}|\d{3,8})\s+en\s+([0-9]+(?:[.,][0-9]+)?)\s*(?:exacto|cantidad|qty)?",
        s, flags=re.I
    ):
        code = (m.group(1) or "").strip().upper()
        num = (m.group(2) or "").strip()
        tail = s[m.end(): m.end() + 20].lower()
        has_qty_hint = ("exacto" in tail) or ("cantidad" in tail) or ("qty" in tail) or ("exacto" in s.lower()) or ("cantidad" in s.lower())
        if code and num and has_qty_hint:
            out["set_qty"].append({"code": code, "qty": num})

    for m in re.finditer(
        r"(?:agrega|añade|anade|suma)\s+([0-9]+(?:[.,][0-9]+)?)\s+(?:m[aá]s\s+de\s+)?([A-Za-z]{1,6}\d{1,8}|\d{3,8})\b",
        s, flags=re.I
    ):
        qty = (m.group(1) or "").strip()
        code = (m.group(2) or "").strip().upper()
        if code and qty:
            out["add"].append({"code": code, "qty": qty, "price": None})

    for m in re.finditer(
        r"(?:rebaja|baja|descuenta)\s+([A-Za-z]{1,6}\d{1,8}|\d{3,8})\s+([0-9]{1,2}(?:[.,][0-9]+)?)\s*%",
        s, flags=re.I
    ):
        code = (m.group(1) or "").strip().upper()
        pct = (m.group(2) or "").strip()
        if code and pct:
            out["pct"].append({"code": code, "pct": pct, "op": "down"})

    for m in re.finditer(
        r"(?:aumenta|sube)\s+([A-Za-z]{1,6}\d{1,8}|\d{3,8})\s+([0-9]{1,2}(?:[.,][0-9]+)?)\s*%",
        s, flags=re.I
    ):
        code = (m.group(1) or "").strip().upper()
        pct = (m.group(2) or "").strip()
        if code and pct:
            out["pct"].append({"code": code, "pct": pct, "op": "up"})

    looks_items = bool(re.search(r"\b(x|×|precio|pu|p\.u\.|agrega|añade|anade|suma)\b", s, flags=re.I))
    if looks_items:
        for m in re.finditer(
            r"(?P<code>[A-Za-z]{1,6}\d{1,8}|\d{3,8})\s*(?:x|×)\s*(?P<qty>[0-9]+(?:[.,][0-9]+)?)",
            s, flags=re.I
        ):
            code = (m.group("code") or "").strip().upper()
            qty = (m.group("qty") or "").strip()
            if code and qty:
                out["add"].append({"code": code, "qty": qty, "price": None})

    out["remove"] = list(dict.fromkeys(out["remove"]))
    return out
