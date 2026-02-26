import os
import pandas as pd

from .utils import to_float, nz


_HEADER_TRY_ORDER = [4, 0, 1, 2, 3, 5]


def _norm_header(x) -> str:
    return str(x or "").strip().lower()


def _pick_sheet_name(xls: pd.ExcelFile, desired_index: int, name_candidates: list[str]) -> str:
    sheets = list(xls.sheet_names or [])
    low = {str(s).strip().lower(): str(s) for s in sheets}

    for cand in name_candidates:
        key = str(cand).strip().lower()
        if key in low:
            return low[key]

    compact_cands = [str(c).lower().replace(" ", "") for c in name_candidates]
    for s in sheets:
        ns = str(s).lower().replace(" ", "")
        if any(c in ns for c in compact_cands):
            return str(s)

    if len(sheets) > desired_index:
        return str(sheets[desired_index])

    raise RuntimeError(f"No se pudo ubicar hoja {desired_index + 1}. Hojas: {sheets}")


def _find_col(cols_lower: dict[str, str], *cands: str) -> str | None:
    for cnd in cands:
        key = _norm_header(cnd)
        if key in cols_lower:
            return cols_lower[key]

    for key, orig in cols_lower.items():
        for cnd in cands:
            if _norm_header(cnd) in key:
                return orig

    return None


def _parse_price_type_id(v, default: int = 1) -> int:
    try:
        if v is None:
            return int(default)

        if isinstance(v, (int, float)):
            iv = int(v)
            return iv if iv in (1, 2, 3) else int(default)

        s = str(v).strip().lower()
        if not s:
            return int(default)

        if s in ("1", "p_max", "max", "maximo", "unitario", "base", "lista"):
            return 1
        if s in ("2", "p_min", "min", "minimo"):
            return 2
        if s in ("3", "p_oferta", "oferta", "promo", "promocion"):
            return 3

        try:
            iv = int(float(s.replace(",", ".")))
            return iv if iv in (1, 2, 3) else int(default)
        except Exception:
            return int(default)
    except Exception:
        return int(default)


def _read_sheet_with_header_fallback(xls: pd.ExcelFile, sheet_name: str, required_tokens: list[str]) -> pd.DataFrame:
    best_df = None
    best_score = -1

    for h in _HEADER_TRY_ORDER:
        try:
            df = pd.read_excel(xls, sheet_name=sheet_name, header=h)
        except Exception:
            continue

        if df is None or df.empty:
            continue

        df = df.dropna(how="all")
        cols_lower = {_norm_header(c): c for c in df.columns}

        score = 0
        for tok in required_tokens:
            nt = _norm_header(tok)
            if any(nt in col for col in cols_lower.keys()):
                score += 1

        if score > best_score:
            best_score = score
            best_df = df

        if score >= max(1, len(required_tokens) // 2):
            return df

    if best_df is not None:
        return best_df

    return pd.DataFrame()


def _leer_inventario_xlsx(path: str, fuente: str) -> pd.DataFrame:
    """
    Hoja 1 (Inventario):
    N°, Codigo, Nombre, Departamento, Genero, Cantidad Disponible,
    Precio Maximo, Precio Minimo, Precio Oferta
    """
    xls = pd.ExcelFile(path, engine="openpyxl")
    sheet1 = _pick_sheet_name(
        xls,
        desired_index=0,
        name_candidates=["Inventario", "Hoja 1", "Hoja1", "Sheet1", "Sheet 1"],
    )

    df = _read_sheet_with_header_fallback(
        xls,
        sheet1,
        required_tokens=["codigo", "nombre", "departamento", "genero", "precio maximo"],
    )
    if df is None or df.empty:
        return pd.DataFrame()

    cols_lower = {_norm_header(c): c for c in df.columns}

    col_codigo = _find_col(cols_lower, "codigo", "código", "cod")
    col_nombre = _find_col(cols_lower, "nombre", "descripcion", "descripción")
    col_depto = _find_col(cols_lower, "departamento", "categoria", "categoría", "rubro")
    col_genero = _find_col(cols_lower, "genero", "género")
    col_cant = _find_col(cols_lower, "cantidad disponible", "cantidad", "stock", "existencia")

    col_p_max = _find_col(cols_lower, "precio maximo", "precio máximo")
    col_p_min = _find_col(cols_lower, "precio minimo", "precio mínimo")
    col_p_oferta = _find_col(cols_lower, "precio oferta", "oferta")
    col_precio_venta = _find_col(
        cols_lower,
        "precio venta",
        "tipo precio",
        "precio por defecto",
        "precio default",
        "p venta",
        "p_venta",
    )

    records: list[dict] = []

    for _, row in df.iterrows():
        codigo = str(row.get(col_codigo, "") if col_codigo else "").strip()
        nombre = str(row.get(col_nombre, "") if col_nombre else "").strip()

        if not codigo and not nombre:
            continue

        departamento = str(row.get(col_depto, "") if col_depto else "").strip()
        genero = str(row.get(col_genero, "") if col_genero else "").strip()

        cantidad = to_float(row.get(col_cant, 0) if col_cant else 0, 0.0)
        p_max = to_float(row.get(col_p_max, 0) if col_p_max else 0, 0.0)
        p_min = to_float(row.get(col_p_min, 0) if col_p_min else 0, 0.0)
        p_oferta = to_float(row.get(col_p_oferta, 0) if col_p_oferta else 0, 0.0)
        precio_venta_tipo = _parse_price_type_id(
            row.get(col_precio_venta, 1) if col_precio_venta else 1,
            1,
        )

        depto_up = (departamento or "").upper()

        records.append(
            {
                # Canonico (estructura excel)
                "CODIGO": codigo,
                "NOMBRE": nombre,
                "DEPARTAMENTO": departamento,
                "GENERO": genero,
                "CANTIDAD_DISPONIBLE": cantidad,
                "P_MAX": p_max,
                "P_MIN": p_min,
                "P_OFERTA": p_oferta,
                "PRECIO_VENTA": int(precio_venta_tipo),
                "__FUENTE": fuente,

                # Compatibilidad app actual
                "id": codigo if codigo else nombre,
                "nombre": nombre,
                "categoria": depto_up,
                "departamento_excel": departamento,
                "genero": genero,
                "cantidad_disponible": cantidad,
                "precio_venta": int(precio_venta_tipo),
                "p_max": p_max,
                "p_min": p_min,
                "p_oferta": p_oferta,
                "__fuente": fuente,
            }
        )

    return pd.DataFrame(records)


def cargar_excel_productos_desde_inventarios(data_dir: str) -> pd.DataFrame:
    rutas = [
        os.path.join(data_dir, "inventario_lcdp.xlsx"),
        os.path.join(data_dir, "inventario_ef.xlsx"),
    ]

    frames: list[pd.DataFrame] = []
    for ruta in rutas:
        if os.path.exists(ruta):
            frames.append(_leer_inventario_xlsx(ruta, os.path.basename(ruta)))

    if not frames:
        raise FileNotFoundError(
            "No se encontro ninguno de los archivos 'inventario_lcdp.xlsx' o 'inventario_ef.xlsx' en data/"
        )

    df = pd.concat(frames, ignore_index=True)

    # Mantiene columnas que esperan otras partes del sistema.
    compat_records: list[dict] = []
    for _, r in df.iterrows():
        base = {
            "id": r.get("id"),
            "nombre": r.get("nombre"),
            "categoria": r.get("categoria"),
            "genero": r.get("genero", ""),
            "cantidad_disponible": nz(r.get("cantidad_disponible"), 0.0),
            "precio_venta": int(nz(r.get("precio_venta"), 1) or 1),
            "codigo": r.get("CODIGO"),
            "departamento": r.get("DEPARTAMENTO"),
            "p_max": nz(r.get("P_MAX"), 0.0),
            "p_min": nz(r.get("P_MIN"), 0.0),
            "p_oferta": nz(r.get("P_OFERTA"), 0.0),
            "ml": "",
        }

        compat_records.append(base)

    return pd.DataFrame(compat_records)
