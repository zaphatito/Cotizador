# src/ai/assistant/intent.py
from __future__ import annotations

import re
from typing import Optional


def route_intent(text: str) -> str:
    """
    Router SIN LLM (guard / hint):
      - open_quote: abrir última / abrir número
      - list_quotes: mostrar/listar/ver cotizaciones
      - top_clients: ranking/top de clientes
      - create_quote: crear/preparar cotización o items (COD xN)
      - "" si no hay señales (saludo, charla, etc.)
    """
    t = (text or "").strip().lower()
    if not t:
        return ""

    # abrir cotización
    if re.search(r"\b(abrir|abre|open)\b", t) and re.search(r"\b(cotiza|cotizaci)\b", t):
        return "open_quote"
    if re.search(r"\b(ultima|última)\b", t) and re.search(r"\b(cotiza|cotizaci)\b", t):
        return "open_quote"
    if t.startswith("abrir "):
        return "open_quote"

    # top clientes
    if re.search(r"\b(top|ranking|mejores)\b", t) and re.search(r"\b(clientes?)\b", t):
        return "top_clients"
    if re.search(r"\b(clientes?)\b", t) and re.search(r"\b(m[aá]s|mayor(es)?|ranking|top)\b", t):
        return "top_clients"

    # listar cotizaciones
    if re.search(r"\b(muestra|mu[eé]strame|lista|listar|ver|dame|ens[eé]ñame)\b", t) and re.search(r"\b(cotiza|cotizaci)\b", t):
        return "list_quotes"
    if re.search(r"\b(cotiza|cotizaci)\b", t) and re.search(r"\b(por\s*pagar|pendient|pagad|reenviado(?:s)?|anulad|sin\s*estado)\b", t):
        return "list_quotes"

    # crear cotización
    if re.search(r"\b[A-Za-z]{1,6}\d{1,8}\b\s*(?:x|×)\s*[0-9]", t):
        return "create_quote"
    if re.search(r"\b(crea|crear|arm(a|ar)|prepara|cotiza|cotizaci[oó]n)\b", t):
        return "create_quote"

    return ""


def pick_status_from_text(text: str) -> Optional[str]:
    """
    Devuelve:
      - "POR_PAGAR"/"PENDIENTE"/"PAGADO"/"NO_APLICA"/"REENVIADO" si lo detecta
      - "" si el usuario pide explícitamente "sin estado"
      - None si NO se menciona estado
    """
    t = (text or "").lower()
    if not t:
        return None

    if "sin estado" in t:
        return ""

    if "por pagar" in t or "pendiente de pago" in t or "pendiente pago" in t:
        return "POR_PAGAR"
    if "pendiente" in t or "pendientes" in t:
        return "PENDIENTE"
    if "pagado" in t or "pagada" in t or "pagadas" in t or "pagados" in t:
        return "PAGADO"
    if "reenviado" in t or "reenviados" in t:
        return "REENVIADO"
    if "no aplica" in t:
        return "NO_APLICA"

    return None
