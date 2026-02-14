# src/ai/assistant/plan_schema.py
from __future__ import annotations

from typing import Any, Dict, List

from ...currency import normalize_currency_code
from ...logging_setup import get_logger

log = get_logger(__name__)

ACTIONS = ("create_quote", "list_quotes", "top_clients")


def _to_str(x: Any) -> str:
    try:
        return str(x) if x is not None else ""
    except Exception:
        return ""


def _to_int(x: Any, default: int) -> int:
    try:
        return int(x)
    except Exception:
        return int(default)


def _clamp_currency(cur: str, allowed: list[str]) -> str:
    c = normalize_currency_code(cur)
    if not c:
        return ""
    allow = [normalize_currency_code(x) for x in (allowed or []) if str(x or "").strip()]
    allow = [x for x in allow if x]
    if allow and c not in allow:
        return ""
    return c


def _normalize_payment(country: str, payment_method: str) -> str:
    """
    - PY: SOLO "Efectivo" o "Tarjeta"
    - Otros: libre
    """
    c = (country or "").upper().strip()
    pm = (payment_method or "").strip()
    if not pm:
        return ""

    if c == "PARAGUAY":
        low = pm.lower()
        if "efectivo" in low or "cash" in low or "contado" in low:
            return "Efectivo"
        if (
            "tarjeta" in low
            or "visa" in low
            or "master" in low
            or "credito" in low
            or "crédito" in low
            or "debito" in low
            or "débito" in low
            or "tdc" in low
            or "tdd" in low
        ):
            return "Tarjeta"
        # si hay intención de pago pero no coincide => Tarjeta
        return "Tarjeta"

    return pm


def validate_and_clean_plan(plan: dict, *, ctx: dict, country: str) -> dict:
    """
    Asegura contrato y normaliza:
      - action válida
      - args dict
      - currency clamp (ctx.currencies)
      - payment normalize (country)
      - items list[dict] con {query, qty, price?}
      - list/top: limit int y status permitido
    """
    p = plan if isinstance(plan, dict) else {}
    action = _to_str(p.get("action")).strip()
    if action not in ACTIONS:
        action = "create_quote"

    args = p.get("args") if isinstance(p.get("args"), dict) else {}
    explanation = _to_str(p.get("explanation")).strip()
    needs_confirmation = bool(p.get("needs_confirmation", action == "create_quote"))

    currencies = [str(x or "").upper().strip() for x in (ctx.get("currencies") or []) if str(x or "").strip()]
    statuses_allowed = {str(x or "").strip().upper() for x in (ctx.get("statuses") or [])}
    statuses_allowed.add("")

    if action == "create_quote":
        out: Dict[str, Any] = {}

        out["client_query"] = _to_str(args.get("client_query")).strip()
        out["client_doc"] = _to_str(args.get("client_doc")).strip()
        out["client_phone"] = _to_str(args.get("client_phone")).strip()

        out["currency"] = _clamp_currency(_to_str(args.get("currency")).strip(), currencies)
        out["payment_method"] = _normalize_payment(country, _to_str(args.get("payment_method")).strip())

        # opcional: observation/nota
        if "observation" in args:
            out["observation"] = _to_str(args.get("observation")).strip()

        raw_items = args.get("items")
        items: List[Dict[str, Any]] = []
        if isinstance(raw_items, list):
            for it in raw_items:
                if not isinstance(it, dict):
                    continue
                q = _to_str(it.get("query")).strip()
                if not q:
                    continue
                qty = _to_str(it.get("qty")).strip() or "1"
                price = it.get("price", None)
                price_s = _to_str(price).strip() if price is not None else ""
                price_out = price_s if price_s != "" else None
                items.append({"query": q, "qty": qty, "price": price_out})

        out["items"] = items

        return {
            "action": "create_quote",
            "args": out,
            "needs_confirmation": True,
            "explanation": explanation,
        }

    if action == "list_quotes":
        out: Dict[str, Any] = {}
        limit = _to_int(args.get("limit", 30), 30)
        limit = max(1, min(limit, 200))
        out["limit"] = limit

        st = args.get("status", None)
        if st is not None:
            st2 = _to_str(st).strip().upper()
            if st2 == "" or st2 in statuses_allowed:
                out["status"] = "" if st2 == "" else st2

        cur = _clamp_currency(_to_str(args.get("currency")).strip(), currencies)
        if cur:
            out["currency"] = cur

        return {
            "action": "list_quotes",
            "args": out,
            "needs_confirmation": False,
            "explanation": explanation,
        }

    if action == "top_clients":
        out: Dict[str, Any] = {}
        limit = _to_int(args.get("limit", 10), 10)
        limit = max(1, min(limit, 200))
        out["limit"] = limit

        cur = _clamp_currency(_to_str(args.get("currency")).strip(), currencies) or "USD"
        out["currency"] = cur

        return {
            "action": "top_clients",
            "args": out,
            "needs_confirmation": False,
            "explanation": explanation,
        }

    # fallback seguro
    return {
        "action": "create_quote",
        "args": {"client_query": "", "client_doc": "", "client_phone": "", "payment_method": "", "currency": "", "items": []},
        "needs_confirmation": True,
        "explanation": explanation,
    }
