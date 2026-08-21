# src/pricing.py
import math

from .config import APP_COUNTRY
from .product_rules import (
    normalize_country,
    uses_gram_quantity,
)
from .country_rules import uses_peru_business_rules
from .utils import nz, format_grams


def quantity_in_grams(
    item: dict | str,
    qty: float | None = None,
    *,
    country: str | None = None,
) -> float:
    """Convert an item's internal quantity to grams when it is weight-based."""
    current_country = normalize_country(APP_COUNTRY if country is None else country)
    if not uses_gram_quantity(item, country=current_country):
        return 0.0
    if qty is None:
        if isinstance(item, dict):
            qty = item.get("cantidad", item.get("CANTIDAD", 0))
        else:
            qty = 0
    quantity = nz(qty, 0.0)
    if uses_peru_business_rules(current_country):
        return quantity * 1000.0
    if current_country in {"PARAGUAY", "VENEZUELA"}:
        return quantity * 50.0
    return 0.0


# =====================================================
# Cantidad mostrada en el PDF / tabla
# =====================================================
def cantidad_para_mostrar(it: dict, *, country: str | None = None) -> str:
    cat = (it.get("categoria") or "").upper()
    qty = it.get("cantidad", 0)
    current_country = normalize_country(APP_COUNTRY if country is None else country)

    if uses_gram_quantity(it, country=current_country):
        return format_grams(quantity_in_grams(it, country=current_country))

    if cat == "BOTELLAS":
        try:
            return str(int(round(float(qty))))
        except Exception:
            return "0"

    try:
        return str(int(round(float(qty))))
    except Exception:
        return str(qty)


# =====================================================
# Factor para total por categoria / pais
# =====================================================
def factor_total_por_categoria(
    cat: str,
    prod_or_item: dict | None = None,
    *,
    country: str | None = None,
) -> float:
    """
    Factor que SOLO afecta el calculo de subtotal/total (no el precio unitario mostrado).

    - CATS (esencias/granel):
        * PERU: qty ya viene en otra unidad, NO aplica x50 aqui.
        * NO-PERU: qty representa unidades de 50g => total = unit * qty * 50
        * Excepcion PY: FERO001/FIJ002 se comportan como unidades => NO aplica x50
    """
    rule_item = dict(prod_or_item or {})
    rule_item["categoria"] = cat
    current_country = normalize_country(APP_COUNTRY if country is None else country)
    if uses_gram_quantity(rule_item, country=current_country) and not uses_peru_business_rules(current_country):
        return 50.0
    return 1.0


def _first_nonzero(prod: dict, *keys: str) -> float:
    for k in keys:
        try:
            v = float(nz(prod.get(k), 0.0))
        except Exception:
            v = 0.0
        if v > 0:
            return float(v)
    return 0.0


def discount_percentage_decimals(country: str | None = None) -> int:
    country_code = str(country or "").strip().upper()
    if country_code in {"PE", "PERU", "PERÚ"}:
        return 0
    return 4


def round_discount_percentage(value, country: str | None = None) -> float:
    try:
        percentage = float(value)
    except (TypeError, ValueError):
        return 0.0

    if not math.isfinite(percentage):
        return 0.0

    decimals = discount_percentage_decimals(country)
    if decimals == 0:
        return float(math.floor(percentage + 0.5))
    return round(percentage, decimals)


def discount_from_amount(subtotal, amount, country: str | None = None) -> tuple[float, float]:
    subtotal_value = max(0.0, float(nz(subtotal, 0.0)))
    amount_value = max(0.0, min(float(nz(amount, 0.0)), subtotal_value))
    if subtotal_value <= 0:
        return 0.0, 0.0

    percentage = round_discount_percentage(
        amount_value / subtotal_value * 100.0,
        country,
    )
    percentage = max(0.0, min(percentage, 100.0))
    recalculated_amount = subtotal_value * percentage / 100.0
    return percentage, recalculated_amount


def normalize_price_id(value, default: int = 1) -> int:
    try:
        if isinstance(value, (int, float)):
            iv = int(value)
            return iv if iv in (1, 2, 3) else int(default)

        s = str(value or "").strip().lower()
        if not s:
            return int(default)

        if s in ("1", "p_max", "max", "maximo", "unitario", "base", "lista"):
            return 1
        if s in ("2", "p_min", "min", "minimo"):
            return 2
        if s in ("3", "p_oferta", "oferta", "promo", "promocion"):
            return 3

        iv = int(float(s.replace(",", ".")))
        return iv if iv in (1, 2, 3) else int(default)
    except Exception:
        return int(default)


def default_price_id_for_product(prod: dict) -> int:
    # Regla de negocio: el precio por defecto siempre es p_max.
    return 1


def price_for_price_id(prod: dict, price_id: int) -> float:
    if not isinstance(prod, dict):
        return 0.0
    pid = normalize_price_id(price_id, 1)
    p_max = _first_nonzero(prod, "p_max", "P_MAX")
    p_min = _first_nonzero(prod, "p_min", "P_MIN")
    p_oferta = _first_nonzero(prod, "p_oferta", "P_OFERTA")

    if pid == 2 and p_min > 0:
        return float(p_min)
    if pid == 3 and p_oferta > 0:
        return float(p_oferta)
    if p_max > 0:
        return float(p_max)
    if p_oferta > 0:
        return float(p_oferta)
    if p_min > 0:
        return float(p_min)
    return 0.0


def precio_base_para_listado(prod: dict) -> float:
    """
    Precio mostrado en listado:
    - Productos: p_max por defecto.
    - Presentaciones: p_max por defecto.
    """
    cat = (prod.get("categoria") or "").upper()
    if cat == "PRESENTACION":
        return price_for_price_id(prod, 1)
    return price_for_price_id(prod, default_price_id_for_product(prod))


def precio_unitario_por_categoria(cat: str, prod: dict, qty_units: float) -> float:
    """
    Devuelve el precio segun el tipo por defecto.
    Regla actual: siempre p_max por defecto.
    """
    cat_u = (cat or "").upper()
    if cat_u == "PRESENTACION":
        return price_for_price_id(prod, 1)
    return price_for_price_id(prod, default_price_id_for_product(prod))
