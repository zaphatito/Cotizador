# src/pricing.py
from .config import APP_COUNTRY, CATS
from .utils import nz, format_grams

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


def _first_price(prod: dict, *keys: str) -> float:
    """Devuelve el primer valor numérico (>0) encontrado entre las llaves dadas."""
    for k in keys:
        try:
            val = float(nz(prod.get(k), 0.0))
        except Exception:
            val = 0.0
        if val > 0:
            return val
    return 0.0


def precio_base_para_listado(prod: dict) -> float:
    """Precio que se muestra en el listado (no depende de cantidades)."""
    cat = (prod.get("categoria") or "").upper()

    if cat == "BOTELLAS":
        # Mostrar precio por unidad (no oferta/mín)
        return _first_price(prod, "precio_unidad", "precio_venta")

    if cat in CATS:
        base_val = _first_price(prod, "precio_base_50g", "precio_unitario", "precio_venta")
        return base_val if APP_COUNTRY == "PERU" else base_val * 50.0

    # Resto categorías: PVP/lista si existe, si no unitario/venta
    return _first_price(prod, "precio_maximo", "pvp", "pvpr", "precio_lista", "PRECIO", "precio_unitario", "precio_venta")


def precio_unitario_por_categoria(cat: str, prod: dict, qty_units: float) -> float:
    """
    Calcula el precio unitario aplicando reglas por categoría.
    ▶️ Sin tramos por cantidad.
    """
    cat_u = (cat or "").upper()

    # === Granel ===
    if cat_u in CATS:
        base_val = _first_price(prod, "precio_base_50g", "precio_unitario", "precio_venta")
        return base_val if APP_COUNTRY == "PERU" else base_val * 50.0

    # === Botellas (por defecto 'unitario') ===
    if cat_u == "BOTELLAS":
        return _first_price(prod, "precio_unidad", "precio_unitario", "precio_venta")

    # === Resto de categorías ===
    return _first_price(prod, "precio_unitario", "PRECIO", "precio_venta")
