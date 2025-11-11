# src/dataio.py
import os, pandas as pd
from .utils import to_float, nz
from .config import CATS

def _leer_inventario_xlsx(path: str, fuente: str) -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name=0, header=4, engine="openpyxl")
    df = df.dropna(how="all")
    cols_lower = {str(c).strip().lower(): c for c in df.columns}

    def col(*cands):
        # Busca coincidencia exacta (normalizada a lower), luego por "contiene"
        for cnd in cands:
            cnd_l = cnd.lower()
            if cnd_l in cols_lower:
                return cols_lower[cnd_l]
        for key, orig in cols_lower.items():
            for cnd in cands:
                if cnd.lower() in key:
                    return orig
        return None

    # Encabezados típicos + los que nos diste
    col_codigo  = col("codigo", "código", "cod.", "id", "referencia")
    col_nombre  = col("nombre", "descripcion", "descripción")
    col_depto   = col("departamento", "categoria", "categoría", "rubro")
    col_genero  = col("genero", "género")
    col_cant    = col("cantidad disponible", "cantidad", "stock", "existencia")
    # Precio Máximo = precio_venta base
    col_p_venta = col("precio maximo", "precio máximo",
                      "precio venta", "p. venta", "precio", "precio unitario")
    col_p_oferta= col("precio oferta", "p. oferta", "oferta")
    col_p_min   = col("precio minimo", "precio mínimo", "minimo", "mínimo")

    records = []
    for _, row in df.iterrows():
        codigo = str(row.get(col_codigo, "")).strip() if col_codigo else ""
        nombre = str(row.get(col_nombre, "")).strip() if col_nombre else ""
        if not codigo and not nombre:
            continue

        depto_raw = str(row.get(col_depto, "")).strip()
        genero_raw = str(row.get(col_genero, "")).strip()

        # ⬅️ PRESERVAR DECIMALES: NO convertir a int
        cant = to_float(row.get(col_cant, 0) if col_cant else 0, 0.0)

        p_venta  = to_float(row.get(col_p_venta, 0)  if col_p_venta  else 0, 0.0)
        p_oferta = to_float(row.get(col_p_oferta, 0) if col_p_oferta else 0, 0.0)
        p_min    = to_float(row.get(col_p_min, 0)    if col_p_min    else 0, 0.0)

        depto_up = (depto_raw or "").upper()

        records.append({
            "id": codigo if codigo else nombre,
            "nombre": nombre,
            "categoria": depto_up,
            "departamento_excel": depto_raw,
            "genero": genero_raw,
            # ⬅️ Guardamos float para que llegue con decimales al Listado
            "cantidad_disponible": cant,
            "precio_venta": p_venta,                # = Precio Maximo
            "precio_oferta_base": p_oferta,         # = Precio Oferta
            "precio_minimo_base": p_min,            # = Precio Minimo
            "__fuente": fuente,
        })

    return pd.DataFrame(records)

def cargar_excel_productos_desde_inventarios(data_dir: str) -> pd.DataFrame:
    rutas = [
        os.path.join(data_dir, "inventario_lcdp.xlsx"),
        os.path.join(data_dir, "inventario_ef.xlsx"),
    ]
    frames = []
    for ruta in rutas:
        if os.path.exists(ruta):
            frames.append(_leer_inventario_xlsx(ruta, os.path.basename(ruta)))
    if not frames:
        raise FileNotFoundError(
            "No se encontró ninguno de los archivos 'inventario_lcdp.xlsx' o 'inventario_ef.xlsx' en data/"
        )
    df = pd.concat(frames, ignore_index=True)

    compat_records = []
    for _, r in df.iterrows():
        cat = (r["categoria"] or "").upper()

        # ⬅️ Mantener decimales en el catálogo también
        base = {
            "id": r["id"],
            "nombre": r["nombre"],
            "categoria": r["categoria"],
            "genero": r.get("genero", ""),
            "cantidad_disponible": nz(r.get("cantidad_disponible"), 0.0),  # float, no int()
        }

        if cat == "BOTELLAS":
            base["precio_unidad"]   = nz(r.get("precio_venta"))
            base[">12 unidades"]    = nz(r.get("precio_oferta_base", r.get("precio_venta")))
            base[">100 unidades"]   = nz(r.get("precio_minimo_base", r.get("precio_oferta_base", r.get("precio_venta"))))
            base["ml"] = ""
        elif cat in CATS:
            base["precio_base_50g"] = nz(r.get("precio_venta"))
            base["precio_gramo"]    = 0.0
            base["ml"] = ""
        else:
            base["precio_unitario"] = nz(r.get("precio_venta"))
            base["ml"] = ""

        compat_records.append(base)

    return pd.DataFrame(compat_records)
