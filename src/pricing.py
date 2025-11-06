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

def precio_base_para_listado(prod: dict) -> float:
    cat = (prod.get("categoria") or "").upper()
    if cat == "BOTELLAS":
        return nz(prod.get("precio_unidad", prod.get("precio_venta")))
    if cat in CATS:
        # Para insumos por granel preferimos un "base 50g" si existe,
        # luego precio_unitario y al final precio_venta.
        for key in ("precio_base_50g", "precio_unitario", "precio_venta"):
            base_val = nz(prod.get(key))
            if base_val:
                return base_val if APP_COUNTRY == "PERU" else base_val * 50.0
        return 0.0
    return nz(prod.get("precio_unitario", prod.get("precio_venta")))

def precio_unitario_por_categoria(cat: str, prod: dict, qty_units: float) -> float:
    cat_u = (cat or "").upper()

    if cat_u in CATS:
        # Precio por 50g en VE/PY (se multiplica 50), directo en PE
        for key in ("precio_base_50g", "precio_unitario", "precio_venta"):
            base_val = nz(prod.get(key))
            if base_val:
                return base_val if APP_COUNTRY == "PERU" else base_val * 50.0
        return 0.0

    if cat_u == "BOTELLAS":
        precio_unidad = nz(prod.get("precio_unidad", prod.get("precio_venta")))
        precio_oferta = nz(prod.get("precio_oferta", prod.get("precio_oferta_base")))
        precio_min    = nz(prod.get("precio_minimo", prod.get("precio_minimo_base")))
        if qty_units >= 100 and precio_min:
            return precio_min
        if qty_units >= 12 and precio_oferta:
            return precio_oferta
        return precio_unidad

    return nz(prod.get("precio_unitario", prod.get("PRECIO", prod.get("precio_venta"))))

def reglas_cantidad(cat: str) -> tuple[float, float]:
    cat_u = (cat or "").upper()
    if APP_COUNTRY == "PERU" and cat_u in CATS:
        return 0.001, 0.001
    return 1.0, 1.0
