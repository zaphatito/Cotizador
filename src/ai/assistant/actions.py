# src/ai/assistant/actions.py
from __future__ import annotations

import datetime
import json
import math
import os
import re
from typing import Any, Optional, Iterable

from sqlModels.db import connect, ensure_schema
from sqlModels.quotes_repo import STATUS_PENDIENTE

from ...paths import DATA_DIR
from ...config import APP_COUNTRY, CATS
from .resolvers import resolve_client_from_history, resolve_product_candidates, month_range_from_today
from .reports import report_text_from_db


YES_WORDS = {
    "si", "sí", "claro", "dale", "ok", "okay", "confirmo", "confirmar", "hazlo", "seguro", "deuna", "de una", "listo"
}
NO_WORDS = {"no", "cancela", "cancelar", "mejor no", "detente", "stop"}


def _clean_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def _upper_code(x: Any) -> str:
    return str(x or "").strip().upper()

def _get_ci(d: dict, key: str) -> Any:
    """dict get case-insensitive + respeta claves raras (>, espacios, etc.)"""
    if not isinstance(d, dict):
        return None
    if key in d:
        return d.get(key)
    lk = str(key).lower()
    for k in d.keys():
        if str(k).lower() == lk:
            return d.get(k)
    return None


def _first_nonzero(prod: dict, keys: list[str]) -> Optional[float]:
    for k in keys:
        v = _get_ci(prod, k)
        x = _to_float(v, 0.0)
        if x and x > 0:
            return float(x)
    return None


def product_prices_text(window, args: dict) -> str:
    code = _upper_code((args or {}).get("code") or (args or {}).get("query"))
    if not code:
        return "Dime el código. Ej: **precios del CH1104**."

    # usa tu resolver “oficial” (df/list) para obtener fila
    r_pr, r_p = _find_row_any(window, code)
    row = r_pr or r_p
    if not isinstance(row, dict):
        return f"No encontré el producto **{code}** en el catálogo cargado."

    # nombre
    nombre = ""
    for k in ("nombre", "NOMBRE", "descripcion", "DESCRIPCION", "name", "NAME"):
        v = _get_ci(row, k)
        if str(v or "").strip():
            nombre = str(v).strip()
            break

    # ✅ tus columnas EXACTAS
    unitario = _first_nonzero(row, ["precio_unidad", "precio_unitario", "precio_venta"])
    oferta = _first_nonzero(
        row,
        [
            "precio_oferta",
            "precio_oferta_base",
            "oferta",
            ">12 unidades",
            "precio_12",
            "precio_12_unidades",
            "mayor_12",
            "mayor12",
            "docena",
            "precio_mayorista",
        ],
    )
    minimo = _first_nonzero(
        row,
        [
            "precio_minimo",
            "precio_minimo_base",
            "minimo",
            ">100 unidades",
            "precio_100",
            "precio_100_unidades",
            "mayor_100",
            "ciento",
        ],
    )
    base_val = _first_nonzero(
        row,
        ["precio_unitario", "precio_unidad", "precio_base_50g", "precio_venta"],
    )

    # opcional: “máximo” usando tu lógica estricta (si aplica en tu sistema)
    pmax = float(lookup_price_for_code_and_mode_strict(window, code, "max") or 0.0)
    maximo = pmax if pmax > 0 else None

    if unitario is None and oferta is None and minimo is None and base_val is None and maximo is None:
        return f"No encontré precios cargados para **{code}**."

    def fmt(x: Optional[float]) -> str:
        if x is None:
            return "—"
        s = f"{float(x):,.4f}"
        return s.rstrip("0").rstrip(".")

    title = f"Precios de **{code}**"
    if nombre:
        title += f" — {nombre}"

    lines = [title]
    lines.append(f"• Unitario: {fmt(unitario)}")
    lines.append(f"• Oferta (>12): {fmt(oferta)}")
    lines.append(f"• Mínimo (>100): {fmt(minimo)}")
    lines.append(f"• Base: {fmt(base_val)}")
    lines.append(f"• Máximo: {fmt(maximo)}")

    return "\n".join(lines)


def _tokenize_simple(text: str) -> list[str]:
    t = _clean_spaces((text or "").lower())
    t = re.sub(r"[^0-9a-záéíóúñü]+", " ", t, flags=re.I)
    t = _clean_spaces(t)
    return t.split() if t else []


def _contains_phrase(text: str, phrase: str) -> bool:
    t = _clean_spaces((text or "").lower())
    p = _clean_spaces((phrase or "").lower())
    if not t or not p:
        return False
    return f" {p} " in f" {t} "


def is_yes(text: str) -> bool:
    t_raw = (text or "").strip()
    if not t_raw:
        return False
    t = _clean_spaces(t_raw.lower())

    if t in {w.lower() for w in YES_WORDS}:
        return True

    for w in YES_WORDS:
        w2 = (w or "").strip().lower()
        if " " in w2 and _contains_phrase(t, w2):
            return True

    toks = set(_tokenize_simple(t))
    for w in YES_WORDS:
        w2 = (w or "").strip().lower()
        if " " not in w2 and w2 in toks:
            return True

    return False


def is_no(text: str) -> bool:
    t_raw = (text or "").strip()
    if not t_raw:
        return False
    t = _clean_spaces(t_raw.lower())

    if t in {w.lower() for w in NO_WORDS}:
        return True

    for w in NO_WORDS:
        w2 = (w or "").strip().lower()
        if " " in w2 and _contains_phrase(t, w2):
            return True

    toks = set(_tokenize_simple(t))
    for w in NO_WORDS:
        w2 = (w or "").strip().lower()
        if " " not in w2 and w2 in toks:
            return True

    return False


def _to_float(x, default: float = 0.0) -> float:
    """
    Parse robusto:
      - "1.234,56" -> 1234.56
      - "1,234.56" -> 1234.56
      - "S/ 12,50" -> 12.50
    """
    try:
        if x is None:
            return float(default)

        s = str(x).strip()
        if not s:
            return float(default)

        s = re.sub(r"[^\d\.,\-]", "", s)
        if not s or s in ("-", ".", ",", "-.", "-,"):
            return float(default)

        neg = s.startswith("-")
        s = s.replace("-", "")
        if not s:
            return float(default)

        if "." in s and "," in s:
            last_dot = s.rfind(".")
            last_comma = s.rfind(",")
            if last_comma > last_dot:
                s = s.replace(".", "")
                s = s.replace(",", ".")
            else:
                s = s.replace(",", "")
        else:
            if "," in s and "." not in s:
                s = s.replace(",", ".")

        if s.count(".") > 1:
            parts = s.split(".")
            s = "".join(parts[:-1]) + "." + parts[-1]

        if not s or s == ".":
            return float(default)

        v = float(s)
        if neg:
            v = -v

        if math.isnan(v) or math.isinf(v):
            return float(default)
        return float(v)
    except Exception:
        return float(default)


def _normalize_doc_only_number(doc_raw: str) -> str:
    s = _clean_spaces(doc_raw or "")
    if not s:
        return ""
    parts = [p.strip() for p in s.split("/") if p.strip()]
    out = []
    for p in parts:
        p2 = re.sub(
            r"^(dni/ruc|dni|ruc|cedula|c[eé]dula|rif|pasaporte|passport|doc(?:umento)?|nit|ci)\b\s*[:=]?\s*",
            "",
            p,
            flags=re.I,
        )
        p2 = p2.strip()
        p2 = re.sub(r"[^0-9A-Za-z\-.\/]", "", p2).strip()
        if p2:
            out.append(p2)
    return " / ".join(out) if out else ""


def _normalize_payment_method_text(mp: str) -> str:
    s = _clean_spaces(mp or "")
    if not s:
        return ""
    s = re.sub(r"\bcon\s*$", "", s, flags=re.I).strip()
    if re.search(r"\b(ya?p[e3]|yap3|yape)\b", s, flags=re.I):
        return "Yape"
    if re.search(r"\b(plin|pl1n)\b", s, flags=re.I):
        return "Plin"
    return s


def _parse_qty_peru_cats(value) -> float:
    s = str(value).strip()
    if not s:
        return 0.001

    if "." in s or "," in s:
        x = _to_float(s, 0.0)
        x = round(x, 3)
        return float(max(0.001, x))

    digits = re.sub(r"\D", "", s)
    if not digits:
        return 0.001

    try:
        x = int(digits) / 1000.0
    except Exception:
        x = 0.0

    x = round(x, 3)
    return float(max(0.001, x))


def _parse_qty_py_vz_cats(value) -> float:
    s = str(value).strip()
    if not s:
        return 1.0

    if "." in s or "," in s:
        n = _to_float(s, 1.0)
        return float(max(1, int(round(n))))

    digits = re.sub(r"\D", "", s)
    if not digits:
        return 1.0
    try:
        n = int(digits)
    except Exception:
        return 1.0

    if n >= 50 and (n % 50 == 0):
        return float(max(1, n // 50))

    return float(max(1, n))


def normalize_price(price_raw) -> float:
    return float(_to_float(price_raw, 0.0) or 0.0)


def _get_catalog_manager(window):
    return getattr(window, "_catalog_manager", None) or getattr(window, "catalog_manager", None)


def _get_catalog_dfs_and_lists(window):
    cm = _get_catalog_manager(window)

    dfp = getattr(cm, "df_productos", None) if cm is not None else getattr(window, "df_productos", None)
    dfpr = getattr(cm, "df_presentaciones", None) if cm is not None else getattr(window, "df_presentaciones", None)

    prods = getattr(window, "productos", None)
    pres = getattr(window, "presentaciones", None)

    prods_list = prods if isinstance(prods, list) else []
    pres_list = pres if isinstance(pres, list) else []

    return dfp, dfpr, prods_list, pres_list


def _find_row_by_code_df(df, code: str) -> Optional[dict]:
    if df is None or getattr(df, "empty", True):
        return None
    cu = (code or "").strip().upper()
    if not cu:
        return None
    try:
        recs = df.to_dict("records")
    except Exception:
        return None

    keys_try = ["CODIGO_NORM", "CODIGO", "codigo_norm", "codigo", "id", "ID"]
    for r in recs:
        for k in keys_try:
            v = str(r.get(k) or "").strip().upper()
            if v and v == cu:
                return r
    return None


def _find_row_by_code_list(recs: Iterable[dict], code: str) -> Optional[dict]:
    cu = (code or "").strip().upper()
    if not cu:
        return None
    keys_try = ["CODIGO_NORM", "CODIGO", "codigo_norm", "codigo", "id", "ID"]
    for r in (recs or []):
        if not isinstance(r, dict):
            continue
        for k in keys_try:
            v = str(r.get(k) or "").strip().upper()
            if v and v == cu:
                return r
    return None


def _find_row_any(window, code: str) -> tuple[Optional[dict], Optional[dict]]:
    dfp, dfpr, prods_list, pres_list = _get_catalog_dfs_and_lists(window)

    r_pr = _find_row_by_code_df(dfpr, code) or _find_row_by_code_list(pres_list, code)
    r_p = _find_row_by_code_df(dfp, code) or _find_row_by_code_list(prods_list, code)
    return r_pr, r_p


def is_cats_code(window, code: str) -> bool:
    cats = {str(x or "").strip().upper() for x in (CATS or [])}
    r_pr, r_p = _find_row_any(window, code)

    for r in (r_pr, r_p):
        if not r:
            continue
        cat = str(r.get("categoria") or r.get("CATEGORIA") or "").strip().upper()
        if cat and cat in cats:
            return True

    return False


def normalize_qty_for_code(window, code: str, kind: str, qty_raw) -> float:
    if is_cats_code(window, code):
        if APP_COUNTRY == "PERU":
            return float(_parse_qty_peru_cats(qty_raw))
        if APP_COUNTRY in ("PARAGUAY", "VENEZUELA"):
            return float(_parse_qty_py_vz_cats(qty_raw))
        q = _to_float(qty_raw, 0.001)
        q = round(q, 3)
        return float(max(0.001, q))

    q = _to_float(qty_raw, 1.0)
    q_int = int(round(q))
    return float(max(1, q_int))


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
    if pm in ("base", "normal", "lista", "unitario", "unit"):
        return "base"
    if pm in ("oferta", "min", "max", "base"):
        return pm
    return ""


def _price_mode_label(pmode: str) -> str:
    pm = _normalize_price_mode(pmode)
    if pm == "oferta":
        return "oferta"
    if pm == "min":
        return "mínimo"
    if pm == "max":
        return "máximo"
    if pm == "base":
        return "base"
    return (pmode or "").strip()


def _looks_like_sku(q: str) -> bool:
    """
    Detecta códigos tipo: CH1104, cc001, PC123, DD001, etc.
    (sin espacios)
    """
    s = (q or "").strip()
    if not s or " " in s or "\t" in s:
        return False
    return bool(re.fullmatch(r"[A-Za-z]{1,6}\d{1,8}[A-Za-z0-9\-_/]*", s))


def lookup_base_price_for_code(window, code: str) -> float:
    cols_base = [
        # estilo Excel
        "UNITARIO", "P_UNITARIO", "PRECIO_UNITARIO", "PRECIO", "PRECIO_BASE", "PRECIO_VENTA", "PVP", "P_VENTA",
        "precio", "precio_base", "precio_venta", "pvp", "p_venta", "price", "base_price",
        "PRICE", "BASE_PRICE",
        # estilo SQLite products_current
        "precio_unitario", "PRECIO_UNITARIO",
        "precio_unidad", "PRECIO_UNIDAD",
        "precio_base_50g", "PRECIO_BASE_50G",
        "precio_venta", "PRECIO_VENTA",
    ]

    r_pr, r_p = _find_row_any(window, code)

    for r in (r_pr, r_p):
        if not r:
            continue
        for c in cols_base:
            v = _to_float(r.get(c), 0.0)
            if v > 0:
                return float(v)

    return 0.0


def _lookup_price_from_row(row: dict, cols: list[str]) -> float:
    """
    IMPORTANTE: usar _get_ci para soportar:
      - mayúsculas/minúsculas
      - columnas con espacios o símbolos (ej: '>12 unidades')
    """
    for c in cols:
        v = _get_ci(row, c)
        x = _to_float(v, 0.0)
        if x > 0:
            return float(x)
    return 0.0


def _lookup_price_toggle_for_min(row: dict, cols: list[str]) -> float:
    return _lookup_price_from_row(row, cols)


def lookup_price_for_code_and_mode_strict(window, code: str, price_mode: str) -> float:
    pm = _normalize_price_mode(price_mode)

    # ✅ Incluye tus columnas “reales” (las mismas ideas que product_prices_text)
    cols_offer = [
        # “normales”
        "OFERTA", "P_OFERTA", "PRECIO_OFERTA", "PRECIO_PROMO", "PROMO", "PROMOCION",
        "PRECIO_12", "PRECIO_12_UNIDADES", "MAYOR_12", "MAYOR12", "DOCENA", "PRECIO_MAYORISTA",
        "P_DOCENA", "PRECIO_DOCENA", "P_12", "P12", "PRECIO12",
        "offer", "price_offer", "precio_oferta", "precio_12", "precio_12_unidades", "mayor_12", "mayor12", "docena",
        "precio_mayorista",
        "precio_oferta_base", "PRECIO_OFERTA_BASE",
        # ✅ “raras” (Excel)
        ">12 unidades", "> 12 unidades", ">=12 unidades", ">= 12 unidades",
    ]

    cols_min = [
        "MINIMO", "P_MINIMO", "PRECIO_MINIMO", "MIN", "MINIMUM",
        "PRECIO_100", "PRECIO_100_UNIDADES", "MAYOR_100", "CIENTO",
        "P_CIENTO", "PRECIO_CIENTO", "P_100", "P100", "PRECIO100",
        "min", "price_min", "precio_minimo", "precio_100", "precio_100_unidades", "mayor_100", "ciento",
        "precio_minimo_base", "PRECIO_MINIMO_BASE",
        # ✅ “raras” (Excel)
        ">100 unidades", "> 100 unidades", ">=100 unidades", ">= 100 unidades",
    ]

    # ✅ Max SOLO max (no metas unitario/base aquí, para que el fallback a base sea real y detectable)
    cols_max = [
        "MAXIMO", "P_MAXIMO", "PRECIO_MAXIMO", "MAX", "MAXIMUM",
        "max", "price_max", "precio_maximo",
        "precio_maximo_base", "PRECIO_MAXIMO_BASE",
    ]

    cols_base = [
        "UNITARIO", "P_UNITARIO", "PRECIO_UNITARIO", "PRECIO", "PRECIO_BASE", "PRECIO_VENTA", "PVP", "P_VENTA",
        "precio", "precio_base", "precio_venta", "pvp", "p_venta", "price", "base_price",
        "precio_unitario", "PRECIO_UNITARIO",
        "precio_unidad", "PRECIO_UNIDAD",
        "precio_base_50g", "PRECIO_BASE_50G",
        "precio_venta", "PRECIO_VENTA",
    ]

    r_pr, r_p = _find_row_any(window, code)
    rows = [r for r in (r_pr, r_p) if isinstance(r, dict)]  # presentación -> producto

    if pm == "oferta":
        for r in rows:
            v = _lookup_price_from_row(r, cols_offer)
            if v > 0:
                return float(v)
        return 0.0

    if pm == "min":
        for r in rows:
            v = _lookup_price_toggle_for_min(r, cols_min)
            if v > 0:
                return float(v)
        return 0.0

    if pm == "max":
        for r in rows:
            v = _lookup_price_from_row(r, cols_max)
            if v > 0:
                return float(v)
        return 0.0

    # base
    if pm == "base":
        for r in rows:
            v = _lookup_price_from_row(r, cols_base)
            if v > 0:
                return float(v)
        return 0.0

    return 0.0

def _resolve_price_for_item(
    window,
    *,
    code: str,
    requested_mode: str,
    price_raw: Any,
    default_mode_if_missing: str = "base",  # ✅ mejor default: base (no “max”)
) -> tuple[float, str, str, bool, bool]:
    """
    Devuelve:
      (price_value, effective_mode, requested_mode_norm, used_default, fell_back_to_base)
    """
    pnum = normalize_price(price_raw)
    req = _normalize_price_mode(requested_mode)
    if pnum > 0:
        return (float(pnum), "", req, False, False)

    used_default = False

    want = req
    if not want:
        want = _normalize_price_mode(default_mode_if_missing)
        used_default = True

    eff = want
    p = 0.0
    if eff:
        p = float(lookup_price_for_code_and_mode_strict(window, code, eff) or 0.0)

    if p > 0:
        return (p, eff, req, used_default, False)

    # fallback base si no se encontró el modo pedido/default
    base = float(lookup_price_for_code_and_mode_strict(window, code, "base") or 0.0)
    if base <= 0:
        base = float(lookup_base_price_for_code(window, code) or 0.0)

    if base > 0:
        return (base, "base", req if req else want, used_default, True)

    return (0.0, eff, req, used_default, True)

def export_catalog_for_assistant(catalog_manager) -> None:
    try:
        base = os.path.join(str(DATA_DIR), "assistant", "catalog")
        os.makedirs(base, exist_ok=True)

        dfp = getattr(catalog_manager, "df_productos", None)
        dfpr = getattr(catalog_manager, "df_presentaciones", None)

        p_path = os.path.join(base, "products.jsonl")
        with open(p_path, "w", encoding="utf-8") as f:
            if dfp is not None and (not dfp.empty):
                for r in dfp.to_dict("records"):
                    pid = str(r.get("id") or r.get("ID") or "").strip().upper()
                    if not pid:
                        continue
                    obj = {
                        "id": pid,
                        "nombre": str(r.get("nombre") or r.get("NOMBRE") or ""),
                        "categoria": str(r.get("categoria") or r.get("CATEGORIA") or ""),
                        "genero": str(r.get("genero") or r.get("GENERO") or ""),
                        "ml": str(r.get("ml") or r.get("ML") or ""),
                    }
                    f.write(json.dumps(obj, ensure_ascii=False) + "\n")

        pr_path = os.path.join(base, "presentations.jsonl")
        with open(pr_path, "w", encoding="utf-8") as f:
            if dfpr is not None and (not dfpr.empty):
                for r in dfpr.to_dict("records"):
                    cn = str(r.get("CODIGO_NORM") or r.get("codigo_norm") or "").strip().upper()
                    if not cn:
                        cn = str(r.get("CODIGO") or r.get("codigo") or "").strip().upper()
                    if not cn:
                        continue
                    obj = {
                        "codigo_norm": cn,
                        "codigo": str(r.get("CODIGO") or r.get("codigo") or ""),
                        "nombre": str(r.get("NOMBRE") or r.get("nombre") or ""),
                        "departamento": str(r.get("DEPARTAMENTO") or r.get("departamento") or ""),
                        "genero": str(r.get("GENERO") or r.get("genero") or ""),
                    }
                    f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    except Exception:
        return


def set_currency_on_window(window, currency: str) -> tuple[str, float]:
    from ...config import set_currency_context

    cur = (currency or "").upper().strip()
    base = str(getattr(window, "base_currency", "") or "").upper()

    rate = 1.0
    if cur and base and cur != base:
        rates = getattr(window, "_rates", {}) or {}
        rate = float(rates.get(cur) or 0.0) or 1.0

    set_currency_context(cur or base, float(rate))
    try:
        window._update_currency_label()
    except Exception:
        pass

    try:
        m = getattr(window, "model", None)
        if m and m.rowCount() > 0:
            top = m.index(0, 0)
            bottom = m.index(m.rowCount() - 1, m.columnCount() - 1)
            m.dataChanged.emit(top, bottom, [])
    except Exception:
        pass

    return (cur or base, float(rate))


def create_quote_preview(window, args: dict) -> tuple[str, dict]:
    client_q = str((args or {}).get("client_query") or "").strip()
    currency = str((args or {}).get("currency") or "").upper().strip()
    items = (args or {}).get("items") or []

    doc_in = _normalize_doc_only_number(str((args or {}).get("client_doc") or "").strip())
    tel_in = str((args or {}).get("client_phone") or "").strip()
    pay_in = _normalize_payment_method_text(str((args or {}).get("payment_method") or "").strip())

    resolved = {
        "currency": currency,
        "payment_method": pay_in,
        "client_query": client_q,
        "client": {"cliente": "", "cedula": "", "telefono": ""},
        "items": [],
        "unresolved": [],
        "raw_args": dict(args or {}),
        "warnings": [],
    }

    db_path = window._ai_db_path
    cli_trip = resolve_client_from_history(db_path, client_q) if client_q else None

    if cli_trip:
        resolved["client"]["cliente"] = str(cli_trip[0] or "")
        resolved["client"]["cedula"] = _normalize_doc_only_number(str(cli_trip[1] or ""))
        resolved["client"]["telefono"] = str(cli_trip[2] or "")
    else:
        resolved["client"]["cliente"] = client_q

    if doc_in:
        resolved["client"]["cedula"] = doc_in
    if tel_in:
        resolved["client"]["telefono"] = tel_in

    for it in items:
        q_raw = str((it or {}).get("query") or "").strip()
        qty_raw = (it or {}).get("qty", 1)
        price_raw = (it or {}).get("price", None)
        pmode_raw = (it or {}).get("price_mode", "")

        # ✅ normaliza SKU en minúscula
        q_for_resolve = q_raw.upper() if _looks_like_sku(q_raw) else q_raw

        # requested por ítem
        pmode_req = _normalize_price_mode(pmode_raw)

        cands = resolve_product_candidates(window, q_for_resolve, limit=6)
        if not cands:
            resolved["unresolved"].append({
                "query": q_raw,
                "reason": "no_match",
                "qty": qty_raw,
                "price": price_raw,
                "price_mode": pmode_req,
            })
            continue

        if len(cands) == 1 or (cands[0][2] - cands[1][2] >= 0.15 and cands[0][2] >= 0.70):
            code, name, score, kind = cands[0]
            qty = normalize_qty_for_code(window, code, kind, qty_raw)

            # ✅ precio por ítem:
            #   - si NO especifica nada => default max
            #   - si especifica offer/min/max => intenta ese, si falta => base
            price_val, pm_eff, pm_req_norm, used_default, fell_back = _resolve_price_for_item(
                window,
                code=str(code).upper(),
                requested_mode=pmode_req,
                price_raw=price_raw,
                default_mode_if_missing="max",
            )

            item_out = {
                "query": q_raw,
                "codigo": str(code).upper(),
                "nombre": name,
                "kind": kind,
                "qty": float(qty),
                "price": float(price_val),
                "price_mode": pm_req_norm,                 # lo que pidió el usuario (normalizado) o ""
                "price_mode_effective": pm_eff or "",      # lo que se aplicó realmente (max/base/...) o ""
                "price_mode_defaulted": bool(used_default),
                "price_mode_fallback": bool(fell_back),
                "confidence": float(score),
            }

            resolved["items"].append(item_out)
        else:
            resolved["unresolved"].append({
                "query": q_raw,
                "reason": "ambiguous",
                "qty": qty_raw,
                "price": price_raw,
                "price_mode": pmode_req,
                "candidates": [
                    {"codigo": c[0], "nombre": c[1], "score": float(c[2]), "kind": c[3]} for c in cands
                ],
            })

    cli = resolved["client"]
    lines = []
    lines.append("Voy a preparar esta cotización:")
    lines.append(f"• Cliente: {cli.get('cliente') or '—'}")
    if cli.get("cedula"):
        lines.append(f"• Doc: {cli.get('cedula')}")
    if cli.get("telefono"):
        lines.append(f"• Tel: {cli.get('telefono')}")
    if resolved.get("payment_method"):
        lines.append(f"• Pago: {resolved.get('payment_method')}")
    lines.append(f"• Moneda: {currency or '—'}")
    lines.append("• Ítems:")

    if resolved["items"]:
        for r in resolved["items"]:
            pr = float(r.get("price") or 0.0)
            pm_req = str(r.get("price_mode") or "").strip()
            pm_eff = str(r.get("price_mode_effective") or "").strip()
            fell_back = bool(r.get("price_mode_fallback"))

            extra = ""
            if pr > 0:
                # si aplicamos modo efectivo (max/base/offer/min)
                if pm_eff:
                    if pm_req and pm_req != pm_eff and fell_back:
                        extra = f" | precio {_price_mode_label(pm_req)} (no encontrado; se usó {_price_mode_label(pm_eff)}): {pr:g}"
                    else:
                        extra = f" | precio {_price_mode_label(pm_eff)}: {pr:g}"
                else:
                    extra = f" | precio unitario: {pr:g}"
            else:
                # no se pudo resolver precio (que el modelo lo calcule)
                # si no pidió modo explícito, asumimos max por defecto en explicación
                want = pm_req or "max"
                extra = f" | precio {_price_mode_label(want)} (no encontrado; se usará base)"

            lines.append(f"  - {r['codigo']} — {r['nombre']}  x {r['qty']}{extra}")
    else:
        lines.append("  - (ninguno resuelto aún)")

    if cli_trip:
        lines.append("• Nota: completé datos desde histórico (si no los sobreescribiste).")

    if resolved["unresolved"]:
        lines.append("\nNecesito aclarar esto antes de ejecutar:")
        for u in resolved["unresolved"]:
            if u.get("reason") == "ambiguous":
                n = len(u.get("candidates") or [])
                lines.append(f"• '{u['query']}' es ambiguo ({n} opciones).")
            else:
                lines.append(f"• No encontré match para: '{u['query']}'")

        lines.append("\nTe iré preguntando uno por uno con botones.")
        lines.append("También puedes escribir el número (1,2,3…) o el código exacto (SKU).")

    return ("\n".join(lines), resolved)


def execute_create_quote(window, resolved: dict) -> str:
    window.limpiar_formulario()

    cur = str(resolved.get("currency") or "").upper().strip()
    if cur:
        set_currency_on_window(window, cur)

    cli = (resolved.get("client") or {})
    try:
        if getattr(window, "entry_cliente", None) is not None:
            window.entry_cliente.setText(cli.get("cliente", "") or "")
        if getattr(window, "entry_cedula", None) is not None:
            window.entry_cedula.setText(_normalize_doc_only_number(cli.get("cedula", "") or ""))
        if getattr(window, "entry_telefono", None) is not None:
            window.entry_telefono.setText(cli.get("telefono", "") or "")
    except Exception:
        pass

    mp = _normalize_payment_method_text(str(resolved.get("payment_method") or "").strip())
    if mp:
        if APP_COUNTRY == "PARAGUAY":
            is_cash = mp.lower() == "efectivo"
            try:
                if hasattr(window, "_set_py_cash_mode"):
                    window._set_py_cash_mode(is_cash, assume_items_already=True)
            except Exception:
                pass
        elif APP_COUNTRY == "PERU":
            try:
                if getattr(window, "entry_metodo_pago", None) is not None:
                    window.entry_metodo_pago.setText(mp)
            except Exception:
                pass

    for r in (resolved.get("items") or []):
        code = str(r.get("codigo") or "").strip().upper()
        if not code:
            continue

        window._agregar_por_codigo(code)

        try:
            row = window._find_last_row_by_code(code)
        except Exception:
            row = None

        qty = r.get("qty", 1)
        try:
            qty_f = float(qty)
        except Exception:
            qty_f = 1.0

        # precio ya resuelto (puede ser 0 si no hubo match)
        price = r.get("price", None)
        try:
            price_f = float(price) if price is not None else 0.0
        except Exception:
            price_f = 0.0

        # si por alguna razón no vino precio (o vino 0), aplicamos default max por ítem
        if price_f <= 0:
            pm_req = _normalize_price_mode(r.get("price_mode", ""))
            pm_eff = _normalize_price_mode(r.get("price_mode_effective", ""))
            # si no hay efectivo, asumimos max por defecto
            want = pm_eff or pm_req or "max"
            p2 = lookup_price_for_code_and_mode_strict(window, code, want)
            if p2 <= 0:
                # fallback base
                p2 = lookup_price_for_code_and_mode_strict(window, code, "base") or lookup_base_price_for_code(window, code)
            if p2 > 0:
                price_f = float(p2)

        if row is not None:
            try:
                window._force_qty_price_on_row(row, qty_f, price_f)
            except Exception:
                pass

    return "Listo: dejé la cotización armada en pantalla. Si quieres, ahora puedes previsualizar o generar el PDF."


def _parse_date_ymd(s: str) -> Optional[datetime.date]:
    s = (s or "").strip()
    if not s:
        return None
    base = s[:10]
    try:
        return datetime.date.fromisoformat(base)
    except Exception:
        return None


def _to_iso_start_bound(date_str: str) -> str:
    s = _clean_spaces(date_str or "")
    if not s:
        return ""
    if "T" in s or ":" in s:
        return s
    d = _parse_date_ymd(s)
    return f"{d.isoformat()}T00:00:00" if d else f"{s}T00:00:00"


def _to_iso_end_exclusive_bound(date_from: str, date_to: str) -> str:
    s_to = _clean_spaces(date_to or "")
    if not s_to:
        return ""
    if "T" in s_to or ":" in s_to:
        return s_to

    d_from = _parse_date_ymd(_clean_spaces(date_from or ""))
    d_to = _parse_date_ymd(s_to)
    if d_to is None:
        return f"{s_to}T00:00:00"

    if d_from is not None and d_to.day == 1 and d_to > d_from:
        delta = (d_to - d_from).days
        if delta >= 27:
            return f"{d_to.isoformat()}T00:00:00"

    d_excl = d_to + datetime.timedelta(days=1)
    return f"{d_excl.isoformat()}T00:00:00"


def list_quotes_filtered(window, args: dict) -> str:
    st_raw = (args or {}).get("status", None)
    if st_raw is None:
        status: Optional[str] = None
    else:
        status = str(st_raw).strip().upper()

    currency = str((args or {}).get("currency") or "").strip().upper()
    date_from = str((args or {}).get("date_from") or "").strip()
    date_to = str((args or {}).get("date_to") or "").strip()
    limit = int((args or {}).get("limit") or 20)

    if status in ("PENDIENTE_APROBACION", "APROBACION", "PENDING_APPROVAL"):
        status = STATUS_PENDIENTE

    if not date_from and not date_to:
        today = datetime.date.today()
        date_from, date_to = month_range_from_today(today)

    df_iso = _to_iso_start_bound(date_from)
    dt_iso = _to_iso_end_exclusive_bound(date_from, date_to)

    db_path = window._ai_db_path
    con = connect(db_path)
    ensure_schema(con)
    try:
        where = ["deleted_at IS NULL"]
        params: list[Any] = []

        if status is not None:
            if status == "":
                where.append("(estado IS NULL OR estado = '')")
            else:
                where.append("estado = ?")
                params.append(status)

        if currency:
            where.append("currency_shown = ?")
            params.append(currency)
        if df_iso:
            where.append("created_at >= ?")
            params.append(df_iso)
        if dt_iso:
            where.append("created_at < ?")
            params.append(dt_iso)

        rows = con.execute(
            f"""
            SELECT id, created_at, quote_no, cliente, estado, total_neto_shown, currency_shown, pdf_path
            FROM quotes
            WHERE {" AND ".join(where)}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            tuple(params + [limit]),
        ).fetchall()

        if not rows:
            return "No encontré cotizaciones con esos filtros."

        lines = []
        lines.append("Encontré estas cotizaciones:")
        for r in rows:
            qn = str(r["quote_no"] or "")
            lines.append(
                f"• #{qn}  {r['cliente']}  {r['currency_shown']} {float(r['total_neto_shown'] or 0):.2f}  ({r['estado'] or ''})"
            )
        lines.append("\nSi quieres abrir una, dime por ejemplo: 'abrir 0000123'.")

        window._assistant_last_list = [dict(x) for x in rows]
        return "\n".join(lines)
    finally:
        con.close()


def top_clients(window, args: dict) -> str:
    currency = str((args or {}).get("currency") or "USD").strip().upper()
    date_from = str((args or {}).get("date_from") or "").strip()
    date_to = str((args or {}).get("date_to") or "").strip()
    limit = int((args or {}).get("limit") or 10)

    if not date_from and not date_to:
        today = datetime.date.today()
        date_from, date_to = month_range_from_today(today)

    df_iso = _to_iso_start_bound(date_from)
    dt_iso = _to_iso_end_exclusive_bound(date_from, date_to)

    db_path = window._ai_db_path
    con = connect(db_path)
    ensure_schema(con)
    try:
        where = ["deleted_at IS NULL", "currency_shown = ?"]
        params: list[Any] = [currency]

        if df_iso:
            where.append("created_at >= ?")
            params.append(df_iso)
        if dt_iso:
            where.append("created_at < ?")
            params.append(dt_iso)

        rows = con.execute(
            f"""
            SELECT cliente, SUM(total_neto_shown) AS total
            FROM quotes
            WHERE {" AND ".join(where)}
            GROUP BY cliente
            ORDER BY total DESC
            LIMIT ?
            """,
            tuple(params + [limit]),
        ).fetchall()

        if not rows:
            return f"No hay datos suficientes para ranking en {currency}."

        lines = [f"Top clientes por monto en {currency}:"]
        for i, r in enumerate(rows, start=1):
            lines.append(f"{i}) {r['cliente']} — {float(r['total'] or 0):.2f}")

        return "\n".join(lines)
    finally:
        con.close()


def report_text(w, args: dict) -> str:
    q = ""
    if isinstance(args, dict):
        q = str(args.get("query") or args.get("text") or args.get("user_query") or "").strip()
    if not q:
        return "Dime qué reporte quieres. Ej: 'productos más vendidos', 'ventas por día', 'ventas por método de pago', 'stock bajo 5'."
    return report_text_from_db(q)
