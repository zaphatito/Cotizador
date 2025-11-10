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
    """
    Devuelve el primer valor numérico (>0) encontrado entre las llaves dadas.
    Útil para compatibilidad entre diferentes fuentes/formatos.
    """
    for k in keys:
        val = nz(prod.get(k))
        if val:
            return val
    return 0.0


def precio_base_para_listado(prod: dict) -> float:
    """
    Precio que se muestra en el listado (no depende de cantidades).
    """
    cat = (prod.get("categoria") or "").upper()

    if cat == "BOTELLAS":
        # Base para listar: precio por unidad (no oferta/min)
        return _first_price(prod, "precio_unidad", "precio_venta")

    if cat in CATS:
        # Granel: preferimos base de 50g si existe, luego unitario, luego venta
        base_val = _first_price(prod, "precio_base_50g", "precio_unitario", "precio_venta")
        return base_val if APP_COUNTRY == "PERU" else base_val * 50.0

    return _first_price(prod, "precio_unitario", "precio_venta")


def precio_unitario_por_categoria(cat: str, prod: dict, qty_units: float) -> float:
    """
    Calcula el precio unitario aplicando reglas por categoría.
    - Granel (CATS): usa base 50g (PE directo, VE/PY *50).
    - BOTELLAS: >=100 -> precio_minimo, >=12 -> precio_oferta, else -> precio_unidad.
      Compatibilidad con llaves antiguas (">12 unidades", ">100 unidades").
    - Otros: precio_unitario/venta.
    """
    cat_u = (cat or "").upper()

    # === Granel ===
    if cat_u in CATS:
        base_val = _first_price(prod, "precio_base_50g", "precio_unitario", "precio_venta")
        return base_val if APP_COUNTRY == "PERU" else base_val * 50.0

    # === Botellas ===
    if cat_u == "BOTELLAS":
        precio_unidad = _first_price(prod, "precio_unidad", "precio_venta")

        # Compatibilidad completa con distintos nombres de columnas
        precio_oferta = _first_price(
            prod,
            "precio_oferta",          # nuevo
            "precio_oferta_base",     # fallback desde inventarios
            ">12 unidades"            # histórico
        )
        precio_min = _first_price(
            prod,
            "precio_minimo",          # nuevo
            "precio_minimo_base",     # fallback desde inventarios
            ">100 unidades"           # histórico
        )

        # Regla por tramos (independiente de empresa/país)
        if qty_units >= 100 and precio_min:
            return precio_min
        if qty_units >= 12 and precio_oferta:
            return precio_oferta
        return precio_unidad

    # === Resto de categorías ===
    return _first_price(prod, "precio_unitario", "PRECIO", "precio_venta")


def reglas_cantidad(cat: str) -> tuple[float, float]:
    """
    Devuelve (mínimo, paso) para edición de cantidades.
    """
    cat_u = (cat or "").upper()
    if APP_COUNTRY == "PERU" and cat_u in CATS:
        return 0.001, 0.001
    return 1.0, 1.0
