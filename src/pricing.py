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
            # En PY (y otros no-PERU) qty = "unidades" (1 unidad = 50g)
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
# Factor para total por categoría / país
# =====================================================
def factor_total_por_categoria(cat: str) -> float:
    """
    Factor que SOLO afecta el cálculo de subtotal/total (no el precio unitario mostrado).

    - CATS (esencias/granel):
        * PERU: qty ya viene en otra unidad (tu lógica actual), NO aplica x50 aquí.
        * NO-PERU (ej. Paraguay): qty representa "unidades" de 50g => total = unit * qty * 50
    """
    cat_u = (cat or "").upper()
    if cat_u in CATS and APP_COUNTRY != "PERU":
        return 50.0
    return 1.0


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
    """
    Precio que se muestra en el listado (no depende de cantidades).

    - BOTELLAS: precio por unidad
    - CATS (granel): precio unitario BASE (NO x50 aquí)
    - PRESENTACION: usa PRECIO_PRESENT (precio de la presentación base, sin botella)
    - Resto: PVP / lista / unitario, incluyendo PRECIO_PRESENT como posible clave
    """
    cat = (prod.get("categoria") or "").upper()

    if cat == "BOTELLAS":
        return _first_price(prod, "precio_unidad", "precio_venta")

    if cat in CATS:
        # ✅ No multiplicar por 50 aquí (unitario debe mantenerse)
        return _first_price(prod, "precio_base_50g", "precio_unitario", "precio_venta")

    if cat == "PRESENTACION":
        return _first_price(
            prod,
            "PRECIO_PRESENT",
            "precio_unitario",
            "PRECIO",
            "precio_venta",
        )

    return _first_price(
        prod,
        "PRECIO_PRESENT",
        "precio_maximo",
        "pvp",
        "pvpr",
        "precio_lista",
        "PRECIO",
        "precio_unitario",
        "precio_venta",
    )


def precio_unitario_por_categoria(cat: str, prod: dict, qty_units: float) -> float:
    """
    Calcula el precio unitario aplicando reglas por categoría.
    ▶️ Sin tramos por cantidad.

    IMPORTANTE:
    - Para CATS (esencias/granel) el unitario NO se escala por país.
      El x50 se aplica en el TOTAL (ver factor_total_por_categoria).
    """
    cat_u = (cat or "").upper()

    if cat_u in CATS:
        # ✅ Unitario base (sin x50)
        return _first_price(prod, "precio_base_50g", "precio_unitario", "precio_venta")

    if cat_u == "BOTELLAS":
        return _first_price(prod, "precio_unidad", "precio_unitario", "precio_venta")

    if cat_u == "PRESENTACION":
        return _first_price(
            prod,
            "PRECIO_PRESENT",
            "precio_unitario",
            "PRECIO",
            "precio_venta",
        )

    return _first_price(
        prod,
        "PRECIO_PRESENT",
        "precio_unitario",
        "PRECIO",
        "precio_venta",
    )
