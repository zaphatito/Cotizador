from __future__ import annotations

import re
from typing import Any, Dict, List

from ...currency import normalize_currency_code

try:
    from ...currency import pick_currency_from_text  # type: ignore
except Exception:
    pick_currency_from_text = None


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------
def _clean_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _to_float(x: Any) -> float:
    s = str(x or "").strip().replace(",", ".")
    s = re.sub(r"[^\d\.\-]", "", s)
    try:
        return float(s)
    except Exception:
        return 0.0


def _qty_text_keep(s: Any) -> str:
    """
    Mantiene qty como TEXTO para no perder ceros:
      - "0.050" queda "0.050"
      - soporta coma decimal si no hay punto
    """
    x = str(s).strip() if s is not None else ""
    if not x:
        return "1"
    if "," in x and "." not in x:
        x = x.replace(",", ".")
    x = re.sub(r"[^0-9\.\-]", "", x)
    if not x or x in ("-", ".", "-."):
        return "1"
    if x.count(".") > 1:
        head, *rest = x.split(".")
        x = head + "." + "".join(rest)
    if x.count("-") > 1:
        x = x.replace("-", "")
    return x


def _fmt_qty(v: float, *, prefer_3dec_under_1: bool = True) -> str:
    """
    Para PERÚ (kilos decimales): si es <1, devuelve 3 decimales (0.050, 0.100).
    Para >=1, recorta ceros.
    """
    if prefer_3dec_under_1 and abs(v) < 1:
        return f"{v:.3f}"
    s = f"{v:.3f}"
    s = s.rstrip("0").rstrip(".")
    return s or "0"


def _qty_from_unit(country: str, num: float, unit: str) -> str:
    """
    Convierte entradas tipo "3kg" / "100g" a qty según país (CATS):
      - PERU: qty en kilos (100g => 0.100, 50g => 0.050, 3kg => 3)
      - PARAGUAY / VENEZUELA: múltiplos de 50g (50g => 1, 100g => 2, 3kg => 60)
    OJO: acá asumimos que cuando el usuario usa g/kg es porque está hablando de CATS.
    """
    c = (country or "").upper().strip()
    u = (unit or "").lower().strip()

    grams = num
    if u.startswith("k"):  # kg, kilo, kilos
        grams = num * 1000.0

    if c in ("PARAGUAY", "VENEZUELA"):
        units = grams / 50.0
        # normalmente será entero
        if abs(units - round(units)) < 1e-9:
            return str(int(round(units)))
        return _fmt_qty(units, prefer_3dec_under_1=False)

    # default: PERU / resto => kilos
    kg = grams / 1000.0
    return _fmt_qty(kg, prefer_3dec_under_1=True)


# ------------------------------------------------------------
# MONEDA
# ------------------------------------------------------------
def extract_currency(text: str, allowed_currencies: List[str] | None = None) -> str:
    allowed = [normalize_currency_code(x) for x in (allowed_currencies or []) if str(x or "").strip()]
    s = text or ""

    if pick_currency_from_text is not None:
        try:
            return normalize_currency_code(pick_currency_from_text(s, allowed=allowed))
        except Exception:
            pass

    t = s.lower()
    if "soles" in t or "s/" in t or "s/." in t or re.search(r"\bsol\b", t):
        return "PEN"
    if "dolar" in t or "dólar" in t or "usd" in t or "$" in t:
        return "USD"
    if "gs" in t or "guarani" in t or "guaraní" in t or "₲" in t:
        return "PYG"
    if "ves" in t or "bolivar" in t or "bolívar" in t or re.search(r"\bbs\b", t):
        return "VES"

    return ""


# ------------------------------------------------------------
# PAGO
# ------------------------------------------------------------
_CODE_RE = r"(?P<code>[A-Za-z]{1,6}\d{1,8})"
_CODE_ITEM_RE = re.compile(rf"\b{_CODE_RE}\b\s*(?:x|×)\s*(?P<qty>[0-9]+(?:[.,][0-9]+)?)", re.I)

_PAGO_ANCHOR_RE = re.compile(
    r"""
    (?:
        \bpago\s+con\b
      | \bpago\b\s*[:=]
      | \bm[eé]todo\s+de\s+pago\b\s*[:=]?
      | \bmetodo\s+de\s+pago\b\s*[:=]?
    )
    """,
    re.I | re.X,
)

_PY_EFECTIVO_RE = re.compile(r"\b(efectivo|cash|contado)\b", re.I)
_PY_TARJETA_RE = re.compile(r"\b(tarjeta|tdc|tdd|visa|mastercard|d[eé]bito|debito|cr[eé]dito|credito)\b", re.I)

# “Yape/Plin” con faltas comunes
_YAPE_RE = re.compile(r"\b(ya?p[e3]|yap3|yape)\b", re.I)
_PLIN_RE = re.compile(r"\b(plin|pl1n)\b", re.I)


def _cut_at_items(text: str) -> str:
    if not text:
        return ""
    m = _CODE_ITEM_RE.search(text)
    if not m:
        return text.strip()
    return text[: m.start()].strip()


def extract_payment_method(text: str, *, country: str) -> str:
    s = text or ""
    ctry = (country or "").upper().strip()

    pay_raw = ""

    m = _PAGO_ANCHOR_RE.search(s)
    if m:
        pay_raw = s[m.end():].strip()
        pay_raw = _cut_at_items(pay_raw)
        pay_raw = re.sub(r"^(con|por)\s+", "", pay_raw, flags=re.I).strip()
    else:
        # detección sin anchor (coloquial)
        if _YAPE_RE.search(s) or _PLIN_RE.search(s) or re.search(r"\b(transferencia|transfer|dep[oó]sito|deposito)\b", s, flags=re.I):
            pay_raw = _cut_at_items(s)
            pay_raw = _clean_spaces(pay_raw)

    pay_raw = _clean_spaces(pay_raw)

    # normaliza casos tipo: "Yape con" => "Yape"
    pay_raw = re.sub(r"\bcon\s*$", "", pay_raw, flags=re.I).strip()
    pay_raw = re.sub(r"\bcon\s+(productos?|items?)\b.*$", "", pay_raw, flags=re.I).strip()

    # Canonical: Yape / Plin
    if _YAPE_RE.search(pay_raw) or _YAPE_RE.search(s):
        return "Yape"
    if _PLIN_RE.search(pay_raw) or _PLIN_RE.search(s):
        return "Plin"

    if ctry == "PARAGUAY":
        if _PY_EFECTIVO_RE.search(s) or _PY_EFECTIVO_RE.search(pay_raw):
            return "Efectivo"
        if _PY_TARJETA_RE.search(s) or _PY_TARJETA_RE.search(pay_raw):
            return "Tarjeta"
        if pay_raw:
            return "Tarjeta"
        return ""

    return pay_raw


# ------------------------------------------------------------
# DOCUMENTO(S)
# ------------------------------------------------------------
_DOC_TYPE_RE = r"(dni\s*/\s*ruc|dni|ruc|cedula|c[eé]dula|rif|pasaporte|passport|doc(?:umento)?\s*\d*|doc\d+|nit|ci)"
_DOC_PAIR_RE = re.compile(
    rf"\b(?P<typ>{_DOC_TYPE_RE})\b\s*[:=]?\s*(?P<num>[0-9A-Za-z][0-9A-Za-z\-.\/]*)",
    re.I,
)
_DOC_GENERIC_RE = re.compile(r"\b(documento|doc)\b\s*[:=]?\s*([0-9A-Za-z][0-9A-Za-z\-.\/]*)", re.I)


def _clean_doc_num(num: str) -> str:
    n = _clean_spaces(num or "")
    # deja alfanum + separadores típicos, pero sin espacios
    n = n.replace(" ", "")
    return n


def extract_client_doc(text: str) -> str:
    """
    Devuelve SOLO el número del doc (sin "CEDULA"/"DNI"/etc).
    """
    s = text or ""
    found: List[str] = []

    for m in _DOC_PAIR_RE.finditer(s):
        num = _clean_doc_num(m.group("num") or "")
        if num:
            found.append(num)

    if not found:
        m2 = _DOC_GENERIC_RE.search(s)
        if m2:
            num = _clean_doc_num(m2.group(2) or "")
            if num:
                found.append(num)

    # dedupe conservando orden
    out: List[str] = []
    seen = set()
    for x in found:
        if x not in seen:
            out.append(x)
            seen.add(x)

    return " / ".join(out)


# ------------------------------------------------------------
# TELÉFONO
# ------------------------------------------------------------
# agrega numero/número/nro para casos reales
_TEL_RE = re.compile(
    r"\b(tel|tlf|telefono|tel[eé]fono|cel|celular|wsp|whatsapp|numero|n[uú]mero|nro|n°)\b\s*[:=]?\s*([0-9+\-\s]{6,25})",
    re.I,
)


def extract_client_phone(text: str) -> str:
    s = text or ""
    m = _TEL_RE.search(s)
    if not m:
        return ""
    tel = m.group(2) or ""
    tel = re.sub(r"\s+", "", tel)
    return tel


# ------------------------------------------------------------
# CLIENTE (nombre)
# ------------------------------------------------------------
_CLIENT_RE_1 = re.compile(
    r"(?:para\s+(?:el\s+)?cliente|cliente)\s+(.+?)(?=\s+(?:con|dni|ruc|doc|tlf|tel(?:efono)?|t[eé]lefono|pago|en)\b|,|;|$)",
    re.I,
)
_CLIENT_RE_2 = re.compile(
    r"\bpara\s+([A-Za-zÁÉÍÓÚÑáéíóúñ0-9 .'\-]+?)(?=\s+(?:con|dni|ruc|doc|tlf|tel(?:efono)?|t[eé]lefono|pago|en)\b|,|;|$)",
    re.I,
)


def extract_client_name(text: str) -> str:
    s = text or ""
    m = _CLIENT_RE_1.search(s)
    if m:
        return _clean_spaces(m.group(1) or "")
    m = _CLIENT_RE_2.search(s)
    if m:
        return _clean_spaces(m.group(1) or "")
    return ""


# ------------------------------------------------------------
# ÍTEMS + price_mode
# ------------------------------------------------------------
_PRICE_TAIL_RE = re.compile(r"(?:precio|p\.?\s*u\.?|pu|unit(?:ario)?)\s*[:=]?\s*([0-9]+(?:[.,][0-9]+)?)", re.I)
_PRICE_MODE_TAIL_RE = re.compile(r"(?:precio\s+)?(oferta|promo|promoci[oó]n|mínimo|minimo|máximo|maximo)", re.I)

_QTY_UNIT_BEFORE_CODE_RE = re.compile(
    rf"\b(?P<num>[0-9]+(?:[.,][0-9]+)?)\s*(?P<unit>kg|kilos?|kilo|g|gr|grs|gramos?)\b\s*(?:de\s+)?\b{_CODE_RE}\b",
    re.I,
)

_QTY_BEFORE_CODE_RE = re.compile(
    rf"\b(?P<num>\d+)\s*(?:unid(?:ades)?|und(?:s)?|pzs?|piezas?)?\s*(?:de\s+)?\b{_CODE_RE}\b",
    re.I,
)


def _price_mode_from_text(s: str) -> str:
    t = (s or "").lower()
    if "oferta" in t or "promo" in t or "promoci" in t:
        return "oferta"
    if "mínimo" in t or "minimo" in t:
        return "min"
    if "máximo" in t or "maximo" in t:
        return "max"
    return ""


def extract_items(text: str, *, country: str) -> List[Dict[str, Any]]:
    """
    Soporta:
      - CH2156 x2 precio 10.5
      - CH1104 precio oferta
      - 15 de CH1104
      - 3kg de DD001
      - 100 g de CC169
    """
    s = text or ""
    items_map: Dict[str, Dict[str, Any]] = {}

    def _apply_price_tail(code: str, start_idx: int):
        tail = s[start_idx: start_idx + 120]
        pm = _PRICE_TAIL_RE.search(tail)
        mm = _PRICE_MODE_TAIL_RE.search(tail)

        price = (pm.group(1).strip() if pm else None)
        mode = (_price_mode_from_text(mm.group(0) or "") if mm else "")

        if code in items_map:
            if price is not None:
                items_map[code]["price"] = price
                items_map[code].pop("price_mode", None)
            elif mode and "price" not in items_map[code]:
                items_map[code]["price_mode"] = mode

    # 1) COD x QTY
    for m in _CODE_ITEM_RE.finditer(s):
        code = (m.group("code") or "").strip().upper()
        qty = _qty_text_keep(m.group("qty") or "1")
        if not code:
            continue

        items_map[code] = {"query": code, "qty": qty}
        _apply_price_tail(code, m.end())

    # 2) QTY + unidad + de + COD (kg/g)
    for m in _QTY_UNIT_BEFORE_CODE_RE.finditer(s):
        code = (m.group("code") or "").strip().upper()
        if not code:
            continue
        if code in items_map:
            continue

        num = _to_float(m.group("num"))
        unit = str(m.group("unit") or "")
        qty = _qty_from_unit(country, num, unit)

        items_map[code] = {"query": code, "qty": qty}
        _apply_price_tail(code, m.end())

    # 3) QTY de COD (unidades)
    for m in _QTY_BEFORE_CODE_RE.finditer(s):
        code = (m.group("code") or "").strip().upper()
        if not code:
            continue
        if code in items_map:
            continue

        qty = _qty_text_keep(m.group("num") or "1")
        items_map[code] = {"query": code, "qty": qty}
        _apply_price_tail(code, m.end())

    # 4) Bonus: "CH1104 precio oferta" sin xN
    for m in re.finditer(rf"\b{_CODE_RE}\b", s, flags=re.I):
        code = (m.group("code") or "").strip().upper()
        if not code or code in items_map:
            continue
        tail = s[m.end(): m.end() + 60]
        mm = _PRICE_MODE_TAIL_RE.search(tail)
        if mm:
            mode = _price_mode_from_text(mm.group(0) or "")
            if mode:
                items_map[code] = {"query": code, "qty": "1", "price_mode": mode}

    return list(items_map.values())


def build_create_quote_args(text: str, *, country: str, allowed_currencies: List[str] | None = None) -> Dict[str, Any]:
    return {
        "client_query": extract_client_name(text),
        "client_doc": extract_client_doc(text),
        "client_phone": extract_client_phone(text),
        "payment_method": extract_payment_method(text, country=country),
        "currency": extract_currency(text, allowed_currencies=allowed_currencies),
        "items": extract_items(text, country=country),
    }
