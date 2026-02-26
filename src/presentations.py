import os
import re
import unicodedata
import pandas as pd

from .utils import nz

_HEADER_TRY_ORDER = [4, 0, 1, 2, 3, 5]


def _norm_txt(s: str) -> str:
    if s is None:
        return ""
    t = unicodedata.normalize("NFD", str(s))
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Mn")
    return t.strip().lower()


def pick_sheet_name(xls: pd.ExcelFile, desired_index: int, name_candidates: list[str]) -> str:
    sheets = list(xls.sheet_names or [])
    low = {str(s).strip().lower(): str(s) for s in sheets}

    for cand in name_candidates:
        c = str(cand).strip().lower()
        if c in low:
            return low[c]

    compact = [str(c).lower().replace(" ", "") for c in name_candidates]
    for s in sheets:
        ns = str(s).lower().replace(" ", "")
        if any(c in ns for c in compact):
            return str(s)

    if len(sheets) > desired_index:
        return str(sheets[desired_index])

    raise RuntimeError(f"Se esperaba la hoja {desired_index + 1} pero solo se encontraron: {sheets}")


def _find_col(cols_lower: dict[str, str], *cands: str) -> str | None:
    for cnd in cands:
        k = _norm_txt(cnd)
        if k in cols_lower:
            return cols_lower[k]

    for key, orig in cols_lower.items():
        for cnd in cands:
            if _norm_txt(cnd) in key:
                return orig

    return None


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
        cols_lower = {_norm_txt(c): c for c in df.columns}

        score = 0
        for tok in required_tokens:
            nt = _norm_txt(tok)
            if any(nt in c for c in cols_lower.keys()):
                score += 1

        if score > best_score:
            best_score = score
            best_df = df

        if score >= max(1, len(required_tokens) // 2):
            return df

    if best_df is not None:
        return best_df

    return pd.DataFrame()


def _norm_codigo_val(v) -> str:
    """
    Normaliza codigo preservando ceros a la izquierda.
    Numericos se rellenan a 4 digitos para mantener formato de presentacion.
    """
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""

    s = str(v).strip()
    if not s:
        return ""

    m = re.fullmatch(r"(\d+)\.0+", s)
    if m:
        s = m.group(1)

    if s.isdigit() and len(s) <= 4:
        s = s.zfill(4)

    return s.upper()


def read_sheet2_presentations(path_xlsx: str) -> pd.DataFrame:
    """
    Hoja 2 (Presentaciones):
    Codigo, Departamento, Genero, Nombre, Descripcion,
    Precio Maximo, Precio Minimo, Precio Oferta
    """
    xls = pd.ExcelFile(path_xlsx, engine="openpyxl")
    sheet2 = pick_sheet_name(
        xls,
        desired_index=1,
        name_candidates=[
            "Presentaciones",
            "Presentaciones Prod",
            "Hoja 2",
            "Hoja2",
            "Sheet2",
            "Sheet 2",
        ],
    )

    df = _read_sheet_with_header_fallback(
        xls,
        sheet2,
        required_tokens=["codigo", "departamento", "genero", "precio maximo"],
    )
    if df is None or df.empty:
        return pd.DataFrame()

    cols_lower = {_norm_txt(c): c for c in df.columns}

    col_codigo = _find_col(cols_lower, "codigo", "código", "cod")
    col_depto = _find_col(cols_lower, "departamento", "categoria", "categoría")
    col_genero = _find_col(cols_lower, "genero", "género")
    col_nombre = _find_col(cols_lower, "nombre")
    col_desc = _find_col(cols_lower, "descripcion", "descripción", "detalle")

    col_p_max = _find_col(cols_lower, "precio maximo", "precio máximo")
    col_p_min = _find_col(cols_lower, "precio minimo", "precio mínimo")
    col_p_oferta = _find_col(cols_lower, "precio oferta", "oferta")

    out_rows: list[dict] = []
    for _, row in df.iterrows():
        codigo = _norm_codigo_val(row.get(col_codigo, "") if col_codigo else "")
        nombre = str(row.get(col_nombre, "") if col_nombre else "").strip()
        departamento = str(row.get(col_depto, "") if col_depto else "").strip()
        genero = str(row.get(col_genero, "") if col_genero else "").strip()
        descripcion = str(row.get(col_desc, "") if col_desc else "").strip()

        if not codigo and not nombre:
            continue

        p_max = pd.to_numeric(row.get(col_p_max, 0) if col_p_max else 0, errors="coerce")
        p_min = pd.to_numeric(row.get(col_p_min, 0) if col_p_min else 0, errors="coerce")
        p_oferta = pd.to_numeric(row.get(col_p_oferta, 0) if col_p_oferta else 0, errors="coerce")

        out_rows.append(
            {
                "codigo": codigo,
                "departamento": departamento,
                "genero": genero,
                "nombre": nombre,
                "descripcion": descripcion,
                "p_max": float(p_max) if pd.notna(p_max) else 0.0,
                "p_min": float(p_min) if pd.notna(p_min) else 0.0,
                "p_oferta": float(p_oferta) if pd.notna(p_oferta) else 0.0,
            }
        )

    out = pd.DataFrame(out_rows)
    if out.empty:
        return out

    for col in ["codigo", "departamento", "genero", "nombre", "descripcion"]:
        out[col] = out[col].fillna("").astype(str).str.strip()

    for col in ["p_max", "p_min", "p_oferta"]:
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0.0)

    return out


def read_sheet3_presentacion_prod(path_xlsx: str) -> pd.DataFrame:
    """
    Hoja 3 (PresentacionesProd):
    Cod Producto, Cod Presentacion, Departamento, Genero, Cantidad
    """
    xls = pd.ExcelFile(path_xlsx, engine="openpyxl")
    sheet3 = pick_sheet_name(
        xls,
        desired_index=2,
        name_candidates=[
            "PresentacionesProd",
            "Presentaciones Prod",
            "PresentacionProd",
            "Hoja 3",
            "Hoja3",
            "Sheet3",
            "Sheet 3",
        ],
    )

    df = _read_sheet_with_header_fallback(
        xls,
        sheet3,
        required_tokens=["cod producto", "cod presentacion", "cantidad"],
    )
    if df is None or df.empty:
        return pd.DataFrame()

    cols_lower = {_norm_txt(c): c for c in df.columns}

    col_cod_prod = _find_col(cols_lower, "cod producto", "codigo producto", "producto", "cod_prod")
    col_cod_pres = _find_col(cols_lower, "cod presentacion", "codigo presentacion", "presentacion", "cod_pres")
    col_depto = _find_col(cols_lower, "departamento", "categoria", "categoría")
    col_genero = _find_col(cols_lower, "genero", "género")
    col_cantidad = _find_col(cols_lower, "cantidad", "cant")

    out_rows: list[dict] = []
    for _, row in df.iterrows():
        cod_prod = str(row.get(col_cod_prod, "") if col_cod_prod else "").strip().upper()
        cod_pres = _norm_codigo_val(row.get(col_cod_pres, "") if col_cod_pres else "")
        departamento = str(row.get(col_depto, "") if col_depto else "").strip()
        genero = str(row.get(col_genero, "") if col_genero else "").strip()
        cant = pd.to_numeric(row.get(col_cantidad, 0) if col_cantidad else 0, errors="coerce")

        if not cod_prod and not cod_pres:
            continue

        out_rows.append(
            {
                "cod_producto": cod_prod,
                "cod_presentacion": cod_pres,
                "departamento": departamento,
                "genero": genero,
                "cantidad": float(cant) if pd.notna(cant) else 0.0,
            }
        )

    out = pd.DataFrame(out_rows)
    if out.empty:
        return out

    for col in ["cod_producto", "cod_presentacion", "departamento", "genero"]:
        out[col] = out[col].fillna("").astype(str).str.strip()

    out["cantidad"] = pd.to_numeric(out["cantidad"], errors="coerce").fillna(0.0)
    return out


def norm_pres_code(c: str) -> tuple[str, bool, bool]:
    """
    Mantiene firma por compatibilidad con el resto del sistema.
    Regla nueva: no se marca requiere_botella desde Excel.
    """
    if not c:
        return "", False, False

    cu = str(c).strip().upper()
    if cu == "PZA":
        return cu, False, True

    return _norm_codigo_val(cu), False, False


def extract_ml_from_text(text: str) -> int:
    if not text:
        return 0

    m = re.search(r"(\d{2,4})\s*ml", str(text), re.I)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return 0

    m2 = re.search(r"(\d{2,4})", str(text))
    if m2:
        try:
            return int(m2.group(1))
        except Exception:
            return 0

    return 0


def ml_from_pres_code_norm(code: str) -> int:
    if not code:
        return 0

    s = str(code).strip().upper()
    m = re.fullmatch(r"0*([0-9]{2,4})", s)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return 0

    m2 = re.search(r"([0-9]{2,4})", s)
    if m2:
        try:
            return int(m2.group(1))
        except Exception:
            return 0

    return 0


def cargar_presentaciones(path_xlsx: str) -> pd.DataFrame:
    if not os.path.exists(path_xlsx):
        raise FileNotFoundError(f"No se encontro el archivo: {path_xlsx}")

    df2 = read_sheet2_presentations(path_xlsx)
    out = []

    for _, r in df2.iterrows():
        cod = str(r.get("codigo") or "").strip()
        if not cod:
            continue

        cod_norm, req_bot, ignorar = norm_pres_code(cod)
        if ignorar:
            continue

        p_max = nz(r.get("p_max"), 0.0)
        p_min = nz(r.get("p_min"), 0.0)
        p_oferta = nz(r.get("p_oferta"), 0.0)

        out.append(
            {
                "CODIGO": cod_norm,
                "CODIGO_NORM": cod_norm,
                "NOMBRE": str(r.get("nombre") or "").strip() or cod_norm,
                "DESCRIPCION": str(r.get("descripcion") or "").strip(),
                "DEPARTAMENTO": str(r.get("departamento") or "").strip().upper(),
                "GENERO": str(r.get("genero") or "").strip(),
                "P_MAX": float(p_max),
                "P_MIN": float(p_min),
                "P_OFERTA": float(p_oferta),
                "REQUIERE_BOTELLA": bool(req_bot),
            }
        )

    return pd.DataFrame(out)


def cargar_presentaciones_prod(path_xlsx: str) -> pd.DataFrame:
    if not os.path.exists(path_xlsx):
        raise FileNotFoundError(f"No se encontro el archivo: {path_xlsx}")

    df3 = read_sheet3_presentacion_prod(path_xlsx)
    out = []

    for _, r in df3.iterrows():
        cod_prod = str(r.get("cod_producto") or "").strip().upper()
        cod_pres = _norm_codigo_val(r.get("cod_presentacion"))
        if not cod_prod or not cod_pres:
            continue

        out.append(
            {
                "COD_PRODUCTO": cod_prod,
                "COD_PRESENTACION": cod_pres,
                "DEPARTAMENTO": str(r.get("departamento") or "").strip().upper(),
                "GENERO": str(r.get("genero") or "").strip(),
                "CANTIDAD": float(nz(r.get("cantidad"), 0.0)),
            }
        )

    return pd.DataFrame(out)


def map_pc_to_bottle_code(pc_id: str) -> str | None:
    if not pc_id:
        return None

    s = str(pc_id).strip().upper()
    if not s.startswith("PC") or len(s) < 3:
        return None

    return s[1:]
