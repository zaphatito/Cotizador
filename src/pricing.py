# src/pricing.py
from .config import APP_COUNTRY, CATS
from .utils import nz, format_grams


# =====================================================
# Cantidad mostrada en el PDF / tabla
# =====================================================
def cantidad_para_mostrar(it: dict) -> str:
    cat = (it.get("categoria") or "").upper()
    qty = it.get("cantidad", 0)

    if cat in CATS:
        if APP_COUNTRY == "PERU":
            try:
                gramos = float(qty) * 1000.0
            except Exception:
                gramos = 0.0
            return format_grams(gramos)
        else:
            try:
                q_int = int(round(float(qty)))
            except Exception:
                q_int = int(qty) if isinstance(qty, int) else 0
            return f"{q_int * 50} g"

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
def factor_total_por_categoria(cat: str) -> float:
    """
    Factor que SOLO afecta el calculo de subtotal/total (no el precio unitario mostrado).

    - CATS (esencias/granel):
        * PERU: qty ya viene en otra unidad, NO aplica x50 aqui.
        * NO-PERU: qty representa unidades de 50g => total = unit * qty * 50
    """
    cat_u = (cat or "").upper()
    if cat_u in CATS and APP_COUNTRY != "PERU":
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
