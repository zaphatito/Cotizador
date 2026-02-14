# src/ai/assistant/plan_builder.py
from __future__ import annotations

import re
import unicodedata

from typing import Any, Optional, Tuple

from ...currency import normalize_currency_code
from ...logging_setup import get_logger

from .intent import route_intent, pick_status_from_text
from .rules import (
    build_create_quote_args,
    extract_currency,
)

log = get_logger(__name__)

ACTIONS = (
    "create_quote", "list_quotes", "top_clients", "open_quote", "edit_quote",
    "chat", "reply", "product_prices", "report"
)


def _clamp_currency(cur: str, allowed: list[str]) -> str:
    c = normalize_currency_code(cur)
    if not c:
        return ""
    allow = [normalize_currency_code(x) for x in (allowed or []) if str(x or "").strip()]
    allow = [x for x in allow if x]
    if allow and c not in allow:
        return ""
    return c


_SMALLTALK_SET = {
    # ES
    "hola", "buenas", "buenos dias", "buenas tardes", "buenas noches",
    "que tal", "como estas", "cómo estás", "gracias",
    # EN
    "hello", "hi", "hey", "good morning", "good afternoon", "good evening", "thanks",
    # PT/IT/FR/DE (básico)
    "ola", "olá", "oi", "ciao", "bonjour", "salut", "merci", "hallo", "danke",
    # Otros comunes (puedes ampliar)
    "namaste", "shalom", "salam",
}


def _norm_smalltalk(text: str) -> str:
    t = unicodedata.normalize("NFKC", (text or "")).strip().casefold()
    t = re.sub(r"[\W_]+", " ", t, flags=re.UNICODE).strip()
    return t


def _is_smalltalk(text: str) -> bool:
    t = _norm_smalltalk(text)
    if not t:
        return False
    # si es corto y está en set (evita falsos positivos en frases largas)
    if len(t) > 30:
        return False
    return t in _SMALLTALK_SET


def _is_meaningful_create_args(args: dict) -> bool:
    items = args.get("items") if isinstance(args.get("items"), list) else []
    return bool(
        items
        or str(args.get("client_query") or "").strip()
        or str(args.get("client_doc") or "").strip()
        or str(args.get("client_phone") or "").strip()
        or str(args.get("payment_method") or "").strip()
        or str(args.get("currency") or "").strip()
    )


_PRICE_WORD_RE = re.compile(r"\b(precio|precios|cu[aá]nto|cuanto|costo|costos|valor|vale)\b", re.I)
_CODE_RE = re.compile(r"\b([A-Za-z]{1,6}\d{1,8}[A-Za-z0-9\-_\/]*)\b")
_REPORT_WORD_RE = re.compile(
    r"\b("
    r"reporte|reportes|informe|resumen|dashboard|estad[ií]stica|estadisticas|"
    r"kpi|indicadores|"
    r"ventas?|vendid[oa]s?|m[aá]s\s+vendid[oa]s?|"
    r"ranking|top"
    r")\b",
    re.I,
)

# ✅ señales de “crear cotización” (para NO confundir con consulta de precios)
_QUOTE_WORD_RE = re.compile(
    r"\b(cotizaci[oó]n|cotizacion|cotiza|cotizar|crear|crea|armar|arma|generar|genera)\b",
    re.I,
)
_QUOTE_CTX_RE = re.compile(
    r"\b(dni|ruc|dni\s*/\s*ruc|cliente|tlf|tel[eé]fono|pago|m[eé]todo\s+de\s+pago|yape|plin|soles|s\/)\b",
    re.I,
)

_OPEN_PDF_RE = re.compile(r"\b(pdf)\b", re.I)
_OPEN_QUOTE_RE = re.compile(r"\b(cotizaci[oó]n|cotizacion|panel|ventana)\b", re.I)


def _extract_first_code(text: str) -> str:
    m = _CODE_RE.search(text or "")
    return (m.group(1) or "").strip().upper() if m else ""


def _default_help_text() -> str:
    return "Dime qué hacer: crear cotización / listar cotizaciones / abrir número / editar ítems."


def _normalize_open_target_from_text(text: str) -> str:
    """
    target: 'pdf' | 'quote' | 'ask'
    - si el texto dice 'pdf' => pdf
    - si dice 'cotización/panel/ventana' => quote
    - si no, ask (ambiguo)
    """
    s = (text or "").strip()
    if not s:
        return "ask"
    if _OPEN_PDF_RE.search(s):
        return "pdf"
    if _OPEN_QUOTE_RE.search(s):
        return "quote"
    return "ask"


def _normalize_open_target_from_args(v: Any) -> str:
    t = str(v or "").strip().lower()
    if t in ("pdf", "quote", "ask"):
        return t
    if t in ("cotizacion", "cotización", "panel", "ventana"):
        return "quote"
    return ""


def _extract_open_args_rules(text: str) -> dict:
    s = (text or "").strip()
    out: dict[str, Any] = {}

    # ✅ decide target por texto (regla local)
    out["target"] = _normalize_open_target_from_text(s)

    m = re.search(r"\b(?:abrir|abre|open)\s+#?\s*(\d{4,10})\b", s, flags=re.I)
    if m:
        out["quote_no"] = str(m.group(1) or "").strip()
        out["which"] = "by_number"
        return out

    m2 = re.search(r"\b(?:ultima|última)\s+(?:cotizaci[oó]n|cotiza)\s+de\s+(.+?)\s*$", s, flags=re.I)
    if m2:
        out["client_query"] = str(m2.group(1) or "").strip()
        out["which"] = "last"
        return out

    m3 = re.search(r"\b(?:abrir|abre|open)\b.*\b(?:ultima|última)\b.*\bde\s+(.+?)(?:\s*$)", s, flags=re.I)
    if m3:
        out["client_query"] = str(m3.group(1) or "").strip()
        out["which"] = "last"
        return out

    out["which"] = "last"
    return out


def _safe_plan_from_llm(plan_llm: object) -> dict:
    return plan_llm if isinstance(plan_llm, dict) else {}


def _norm_query_key(q: str) -> str:
    return str(q or "").strip().upper()


def _qty_to_text(qty_raw: Any) -> str:
    """
    Mantiene qty como TEXTO (no float) para no perder ceros:
      - "0.050" queda "0.050"
      - soporta coma decimal si no hay punto
    """
    s = str(qty_raw).strip() if qty_raw is not None else ""
    if not s:
        return "1"
    if "," in s and "." not in s:
        s = s.replace(",", ".")
    s2 = re.sub(r"[^0-9\.\-]", "", s)
    if not s2 or s2 in ("-", ".", "-."):
        return "1"
    if s2.count(".") > 1:
        head, *rest = s2.split(".")
        s2 = head + "." + "".join(rest)
    if s2.count("-") > 1:
        s2 = s2.replace("-", "")
    return s2


def _normalize_price_mode(pmode_raw: Any) -> str:
    pm = str(pmode_raw or "").strip().lower()
    if not pm:
        return ""
    if pm in ("oferta", "promo", "promoción", "promocion", "sale", "offer"):
        return "oferta"
    if pm in ("min", "mín", "mínimo", "minimo", "minimum"):
        return "min"
    if pm in ("max", "máx", "máximo", "maximo", "maximum"):
        return "max"
    if pm in ("base", "normal", "lista", "unitario"):
        return "base"
    if pm in ("oferta", "min", "max", "base"):
        return pm
    return ""


def _coerce_price(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        if isinstance(x, (int, float)):
            return float(x)
        s = str(x).strip()
        if not s:
            return None
        s = s.replace(",", ".")
        s = re.sub(r"[^\d\.\-]", "", s)
        if not s:
            return None
        return float(s)
    except Exception:
        return None


def _strip_doc_label(v: str) -> str:
    """
    Si el LLM manda 'CEDULA 123' o 'DNI: 123', nos quedamos solo con el número.
    """
    s = (v or "").strip()
    if not s:
        return ""
    s2 = re.sub(
        r"^(dni\s*/\s*ruc|dni|ruc|cedula|c[eé]dula|rif|pasaporte|passport|doc(?:umento)?|nit|ci)\b\s*[:=]?\s*",
        "",
        s,
        flags=re.I,
    )
    return s2.strip()


def _merge_items_rules_over_llm(rule_items: list, llm_items: list) -> list[dict]:
    """
    Regla: items de reglas SON la base.
    LLM solo ENRIQUECE por query/código:
      - price / price_mode SOLO si el item de reglas no los trae
      - qty SOLO si reglas dejó "1" (o vacío) y LLM trae otra cosa
    Además:
      - agrega items extra del LLM que no estén en reglas (por si reglas no captó algo),
        pero sin duplicar.
    """
    r_items = rule_items if isinstance(rule_items, list) else []
    l_items = llm_items if isinstance(llm_items, list) else []

    llm_by_key: dict[str, dict] = {}
    for it in l_items:
        if not isinstance(it, dict):
            continue
        q = str(it.get("query") or it.get("code") or "").strip()
        if not q:
            continue
        k = _norm_query_key(q)
        if k and k not in llm_by_key:
            llm_by_key[k] = it

    merged: list[dict] = []
    seen = set()

    for rit in r_items:
        if not isinstance(rit, dict):
            continue

        q = str(rit.get("query") or rit.get("code") or "").strip()
        if not q:
            continue
        k = _norm_query_key(q)
        if not k:
            continue
        if k in seen:
            continue
        seen.add(k)

        out = dict(rit)
        out["qty"] = _qty_to_text(out.get("qty"))

        lit = llm_by_key.get(k)
        if isinstance(lit, dict):
            rq = str(out.get("qty") or "").strip()
            lq = _qty_to_text(lit.get("qty"))
            if (not rq or rq == "1") and lq and lq != "1":
                out["qty"] = lq

            if out.get("price") in (None, "", 0, 0.0):
                p = _coerce_price(lit.get("price"))
                if p is not None and p > 0:
                    out["price"] = float(p)

            if not str(out.get("price_mode") or "").strip():
                pm = _normalize_price_mode(lit.get("price_mode"))
                if pm:
                    out["price_mode"] = pm

        merged.append(out)

    for lit in l_items:
        if not isinstance(lit, dict):
            continue
        q = str(lit.get("query") or lit.get("code") or "").strip()
        if not q:
            continue
        k = _norm_query_key(q)
        if not k or k in seen:
            continue
        seen.add(k)

        o: dict[str, Any] = {"query": q, "qty": _qty_to_text(lit.get("qty"))}
        p = _coerce_price(lit.get("price"))
        if p is not None and p > 0:
            o["price"] = float(p)
        pm = _normalize_price_mode(lit.get("price_mode"))
        if pm:
            o["price_mode"] = pm
        merged.append(o)

    return merged


_ITEM_SPEC_RE = re.compile(
    r"""
    \b(?P<code>[A-Za-z]{1,6}\d{1,8}[A-Za-z0-9\-_\/]*)\b
    (?:
        \s*(?:x|×)\s*(?P<qty_x>\d+(?:[.,]\d+)?)
      | \s+(?P<qty_sp>\d+(?:[.,]\d+)?)
    )?
    (?:
        \s*(?:precio|p)\s*[:=]?\s*(?:de\s+)?
        (?P<pinfo>
            oferta|promo|promoci[oó]n|
            min|m[ií]n|minimo|m[ií]nimo|
            max|m[aá]x|maximo|m[aá]ximo|
            base|normal|lista|unitario|unit
          | \d+(?:[.,]\d+)?
        )
    )?
    """,
    flags=re.I | re.X,
)


def _extract_item_specs_from_text(text: str) -> dict[str, dict[str, Any]]:
    specs: dict[str, dict[str, Any]] = {}
    s = text or ""
    for m in _ITEM_SPEC_RE.finditer(s):
        code = str(m.group("code") or "").strip()
        if not code:
            continue
        k = _norm_query_key(code)
        if not k:
            continue

        qty = m.group("qty_x") or m.group("qty_sp") or ""
        qty = _qty_to_text(qty) if qty else ""

        pinfo = str(m.group("pinfo") or "").strip()
        pm = ""
        pnum: Optional[float] = None
        if pinfo:
            pnum = _coerce_price(pinfo)
            if pnum is None:
                pm = _normalize_price_mode(pinfo)

        spec = specs.get(k) or {}
        if qty:
            spec["qty"] = qty
        if pm:
            spec["price_mode"] = pm
        if pnum is not None and pnum > 0:
            spec["price"] = float(pnum)

        if spec:
            specs[k] = spec

    return specs


def _apply_specs_to_items(items: list[dict], specs: dict[str, dict[str, Any]]) -> list[dict]:
    out: list[dict] = []
    seen = set()

    for it in (items or []):
        if not isinstance(it, dict):
            continue
        q = str(it.get("query") or it.get("code") or "").strip()
        if not q:
            continue
        k = _norm_query_key(q)
        if not k:
            continue

        seen.add(k)
        spec = specs.get(k) or {}

        merged = dict(it)
        merged["qty"] = _qty_to_text(merged.get("qty"))

        if "qty" in spec and str(spec["qty"]).strip():
            merged["qty"] = _qty_to_text(spec["qty"])

        if "price_mode" in spec and str(spec["price_mode"]).strip():
            merged["price_mode"] = str(spec["price_mode"]).strip()

        if "price" in spec and spec["price"] is not None:
            merged["price"] = float(spec["price"])

        out.append(merged)

    for k, spec in (specs or {}).items():
        if k in seen:
            continue
        o: dict[str, Any] = {"query": k, "qty": _qty_to_text(spec.get("qty") or "1")}
        if spec.get("price_mode"):
            o["price_mode"] = str(spec["price_mode"]).strip()
        if spec.get("price") is not None:
            o["price"] = float(spec["price"])
        out.append(o)

    return out


def _is_quote_request(text: str, intent_hint: str) -> bool:
    s = (text or "").strip()
    if not s:
        return False

    if (intent_hint or "").strip() == "create_quote":
        return True

    if _QUOTE_WORD_RE.search(s):
        return True

    if _QUOTE_CTX_RE.search(s):
        return True

    if _ITEM_SPEC_RE.search(s):
        return True

    codes = _CODE_RE.findall(s)
    if len([c for c in codes if c]) >= 2:
        return True

    return False


def build_plan(
    user_text: str,
    *,
    ctx: dict,
    planner: Optional[Any],
    today_iso: str,
    country: str,
    force_action: str = "",
) -> Tuple[dict, bool, str]:
    text = user_text or ""
    currencies = [str(x or "").upper().strip() for x in (ctx.get("currencies") or []) if str(x or "").strip()]
    statuses_allowed = {str(x or "").strip().upper() for x in (ctx.get("statuses") or [])}
    statuses_allowed.add("")

    intent_hint = (force_action or route_intent(text) or "").strip()
    if _is_smalltalk(text):
        intent_hint = "chat"

    # ✅ FIX: NO convertir en product_prices cuando el usuario está pidiendo una cotización
    if (not _is_quote_request(text, intent_hint)) and _PRICE_WORD_RE.search(text):
        code = _extract_first_code(text)
        if code:
            return (
                {
                    "action": "product_prices",
                    "args": {"code": code},
                    "needs_confirmation": False,
                    "explanation": "Regla local: pregunta de precios => product_prices.",
                },
                False,
                "",
            )

    if _REPORT_WORD_RE.search(text):
        return (
            {
                "action": "report",
                "args": {"query": text.strip()},
                "needs_confirmation": False,
                "explanation": "Regla local: reporte => report.",
            },
            False,
            "",
        )

    used_fallback = False
    ollama_error = ""

    plan_llm: dict = {}
    if planner is not None:
        try:
            ctx2 = dict(ctx or {})
            if intent_hint:
                ctx2["intent_hint"] = intent_hint
            ctx2["country"] = (country or "").upper().strip()
            plan_llm = _safe_plan_from_llm(planner.plan(text, today_iso=today_iso, context=ctx2))  # type: ignore
        except Exception as e:
            used_fallback = True
            ollama_error = str(e).strip() or "planner_failed"
            plan_llm = {}
    else:
        used_fallback = True
        ollama_error = "no_planner"
        plan_llm = {}

    action = str(plan_llm.get("action") or "").strip()
    if action and action not in ACTIONS:
        used_fallback = True
        if not ollama_error:
            ollama_error = f"invalid_action:{action}"
        action = ""

    if not action:
        action = intent_hint if intent_hint in ACTIONS else "chat"

    if action == "reply":
        action = "chat"

    args = plan_llm.get("args") if isinstance(plan_llm.get("args"), dict) else {}

    if action == "create_quote":
        rule_args = build_create_quote_args(text, country=country, allowed_currencies=currencies)

        if not _is_meaningful_create_args({**(rule_args or {}), **(args or {})}):
            return (
                {
                    "action": "chat",
                    "args": {"text": _default_help_text()},
                    "needs_confirmation": False,
                    "explanation": "LLM (sin acción ejecutable) + ayuda.",
                },
                used_fallback,
                ollama_error,
            )

        merged = dict(rule_args or {})

        for k in ("client_query", "client_doc", "client_phone", "payment_method", "currency"):
            rv = str(merged.get(k) or "").strip()
            lv = str((args or {}).get(k) or "").strip()
            if not rv and lv:
                if k == "client_doc":
                    merged[k] = _strip_doc_label(lv)
                else:
                    merged[k] = lv

        for k, v in (args or {}).items():
            if k == "items":
                continue
            if k in ("client_query", "client_doc", "client_phone", "payment_method", "currency"):
                continue
            merged[k] = v

        rule_items = (rule_args or {}).get("items") if isinstance((rule_args or {}).get("items"), list) else []
        llm_items = (args or {}).get("items") if isinstance((args or {}).get("items"), list) else []
        merged_items = _merge_items_rules_over_llm(rule_items, llm_items)

        specs = _extract_item_specs_from_text(text)
        merged["items"] = _apply_specs_to_items(merged_items, specs)

        return (
            {
                "action": "create_quote",
                "args": merged,
                "needs_confirmation": True,
                "explanation": "LLM + reglas (hints).",
            },
            used_fallback,
            ollama_error,
        )

    if action == "list_quotes":
        out = dict(args or {})
        st = out.get("status", None)
        if st is None:
            st = pick_status_from_text(text)
        if st is not None:
            st_u = str(st).strip().upper()
            if st_u == "" or st_u in statuses_allowed:
                out["status"] = "" if st_u == "" else st_u
            else:
                out.pop("status", None)

        cur = _clamp_currency(out.get("currency", ""), currencies)
        if not cur:
            cur = _clamp_currency(extract_currency(text, allowed_currencies=currencies), currencies)
        if cur:
            out["currency"] = cur
        else:
            out.pop("currency", None)

        try:
            out["limit"] = int(out.get("limit") or 30)
        except Exception:
            out["limit"] = 30
        out["limit"] = max(1, min(int(out["limit"]), 200))

        return (
            {"action": "list_quotes", "args": out, "needs_confirmation": False, "explanation": "LLM + reglas (hints)."},
            used_fallback,
            ollama_error,
        )

    if action == "top_clients":
        out = dict(args or {})
        cur = _clamp_currency(out.get("currency", ""), currencies) or "USD"
        out["currency"] = cur
        try:
            out["limit"] = int(out.get("limit") or 10)
        except Exception:
            out["limit"] = 10
        out["limit"] = max(1, min(int(out["limit"]), 100))
        return (
            {"action": "top_clients", "args": out, "needs_confirmation": False, "explanation": "LLM + reglas (hints)."},
            used_fallback,
            ollama_error,
        )

    if action == "open_quote":
        out = dict(args or {})

        # ✅ target desde args si viene; si no, por reglas desde texto
        tgt = _normalize_open_target_from_args(out.get("target"))
        out["target"] = tgt if tgt else _normalize_open_target_from_text(text)

        if not out.get("quote_no") and not out.get("client_query"):
            out.update(_extract_open_args_rules(text))
            # _extract_open_args_rules también setea target, pero respetamos
            # el target ya decidido (si venía en args)
            if tgt:
                out["target"] = tgt

        if not out.get("which"):
            out["which"] = "last"

        return (
            {"action": "open_quote", "args": out, "needs_confirmation": False, "explanation": "LLM + reglas (hints)."},
            used_fallback,
            ollama_error,
        )

    if action == "edit_quote":
        out = dict(args or {})
        if not out.get("edits_text"):
            out["edits_text"] = text
        return (
            {"action": "edit_quote", "args": out, "needs_confirmation": False, "explanation": "LLM (edición en pantalla)."},
            used_fallback,
            ollama_error,
        )

    msg = str((args or {}).get("text") or "").strip() or _default_help_text()
    return (
        {"action": "chat", "args": {"text": msg}, "needs_confirmation": False, "explanation": "LLM (chat)."},
        used_fallback,
        ollama_error,
    )
