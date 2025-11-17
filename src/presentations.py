# src/presentations.py
import os
import re
import unicodedata
import pandas as pd
from .utils import nz

def pick_sheet_name(xls: pd.ExcelFile, desired_index: int, name_candidates: list[str]) -> str:
    sheets = xls.sheet_names
    low = {s.lower(): s for s in sheets}
    for cand in name_candidates:
        key = cand.lower()
        if key in low:
            return low[key]
    # match tolerante a espacios
    for s in sheets:
        if any(c.lower().replace(" ", "") in s.lower().replace(" ", "") for c in name_candidates):
            return s
    if len(sheets) > desired_index:
        return sheets[desired_index]
    raise RuntimeError(f"Se esperaba la hoja {desired_index+1} pero solo se encontraron: {sheets}")

def _norm_txt(s: str) -> str:
    if s is None:
        return ""
    t = unicodedata.normalize("NFD", str(s))
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Mn")
    return t.strip().lower()

def _norm_codigo_val(v) -> str:
    """
    Normaliza el código de presentación:

    - Si viene como 3.0 -> "0003"
    - Si viene como 3    -> "0003"
    - Si viene como 100  -> "0100"
    - Si ya viene como "0003" o "0100" se respeta.
    - Sólo se aplica a códigos puramente numéricos.
    """
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = str(v).strip()
    if not s:
        return ""
    # quitar .0 si viene de float
    m = re.fullmatch(r"(\d+)\.0+", s)
    if m:
        s = m.group(1)
    # si es todo dígitos, rellenar a 4
    if s.isdigit():
        if len(s) <= 4:
            s = s.zfill(4)
    return s

def read_sheet2_presentations(path_xlsx: str) -> pd.DataFrame:
    """
    Lee Hoja 2 (o equivalente) y produce columnas estandarizadas:
    codigo, nombre, departamento, genero, p_venta
    - p_venta prioriza: Precio Máximo > Precio Presentación > Precio Venta > "Precio"
    """
    xls = pd.ExcelFile(path_xlsx, engine="openpyxl")
    sheet2 = pick_sheet_name(
        xls, desired_index=1,
        name_candidates=["Hoja 2", "Hoja2", "Productos", "Productos Base",
                         "Presentaciones", "Sheet2", "Sheet 2"]
    )
    df = pd.read_excel(xls, sheet_name=sheet2, header=4)
    if df is None or df.empty:
        return pd.DataFrame()

    # Renombrar columnas de texto (excepto precio; ese lo resolvemos por prioridad explícita)
    rename_fixed = {}
    for c in df.columns:
        cl = _norm_txt(c)
        if cl == "codigo":
            rename_fixed[c] = "codigo"
        elif cl == "nombre":
            rename_fixed[c] = "nombre"
        elif "departamento" in cl or "categoria" in cl:
            rename_fixed[c] = "departamento"
        elif "genero" in cl or "género" in str(c).lower():
            rename_fixed[c] = "genero"
    df = df.rename(columns=rename_fixed)
    df = df.loc[:, ~df.columns.duplicated(keep="first")]

    # Resolver columna de precio con prioridad
    norm_map = {_norm_txt(c): c for c in df.columns}
    price_priority = [
        "precio maximo", "precio máximo",
        "precio presentacion", "precio presentación",
        "precio venta", "p. venta", "p venta",
        "precio",
    ]
    price_col = None
    for key in price_priority:
        nk = _norm_txt(key)
        candidates = [nk, nk.replace(" ", ""), nk.replace(".", ""),
                      nk.replace(" ", "").replace(".", "")]
        found = None
        for cand in candidates:
            # búsqueda por inclusión (ej: "precio maximo" dentro de
            # "precio maximo s/ igv")
            for norm_name, orig in norm_map.items():
                if cand in norm_name.replace(" ", ""):
                    found = orig
                    break
            if found:
                break
        if found:
            price_col = found
            break

    # Helper para extraer series robustamente
    def _ser(colname: str) -> pd.Series:
        if colname not in df.columns:
            return pd.Series([None] * len(df), index=df.index)
        s = df[colname]
        if isinstance(s, pd.DataFrame):
            s = s.iloc[:, 0]
        return s

    # CODIGO: usar normalización especial para preservar ceros a la izquierda
    raw_cod = _ser("codigo")
    cod = raw_cod.map(_norm_codigo_val)

    nom = _ser("nombre").astype(str).str.strip()
    dep = _ser("departamento").astype(str).str.strip()
    gen = _ser("genero").astype(str).str.strip()

    if price_col and price_col in df.columns:
        pventa_src = _ser(price_col)
    else:
        # Si no hay ninguna, generamos 0.0
        pventa_src = pd.Series([0] * len(df), index=df.index)

    pventa_txt = (
        pventa_src.astype(str)
        .str.strip()
        .str.replace(",", "", regex=False)
        .str.replace(" ", "", regex=False)
    )
    pventa_num = pd.to_numeric(pventa_txt, errors="coerce").fillna(0.0)

    out = pd.DataFrame({
        "codigo": cod,
        "nombre": nom,
        "departamento": dep,
        "genero": gen,
        "p_venta": pventa_num,
    })

    # Normalizar pseudo-nulos
    for col in ["codigo", "nombre", "departamento", "genero"]:
        out[col] = (
            out[col]
            .replace({"None": "", "none": "", "nan": "", "NaN": "", "NAN": ""})
            .fillna("")
            .astype(str)
            .str.strip()
        )

    # Eliminar filas totalmente vacías (sin código, nombre, depto ni género)
    mask_all_empty = out[["codigo", "nombre", "departamento", "genero"]].apply(
        lambda s: s.str.len() == 0
    ).all(axis=1)
    out = out.loc[~mask_all_empty].reset_index(drop=True)
    return out

def norm_pres_code(c: str) -> tuple[str, bool, bool]:
    """
    Devuelve (codigo_normalizado, requiere_botella, ignorar)

    Regla actual (según lo que comentaste):
      - Códigos que vienen de la hoja de presentaciones (0003, 0100, etc.)
        NO requieren botella → queremos vender la base sola.
      - Los PC... salen del inventario de productos (no pasan por aquí).

    - 'PZA' se ignora
    - 'E100' -> ('0100', requiere_botella=False)
    - 'C240' -> ('C240', requiere_botella=False)
    - 'XXX' numérico de 3-4 dígitos -> requiere_botella=False
    """
    if not c:
        return "", False, False
    cu = c.strip().upper()

    if cu == "PZA":
        return cu, False, True

    # Presentaciones especiales, también sin botella
    if cu == "E100":
        return "0100", False, False
    if cu == "C240":
        return "C240", False, False

    # Códigos numéricos de 3–4 dígitos: base sin botella
    if re.fullmatch(r"[0-9]{3,4}", cu):
        return cu, False, False

    # Cualquier otra cosa: la dejamos como está y no requiere botella
    return cu, False, False

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
            return int(m.group(1))
        except Exception:
            return 0
    return 0

def cargar_presentaciones(path_xlsx: str) -> pd.DataFrame:
    if not os.path.exists(path_xlsx):
        raise FileNotFoundError(f"No se encontró el archivo: {path_xlsx}")
    df2 = read_sheet2_presentations(path_xlsx)
    out = []
    for _, r in df2.iterrows():
        cod = str(r.get("codigo") or "").strip()
        if not cod:
            continue

        cod_norm, req_bot, ignorar = norm_pres_code(cod)
        if ignorar:
            continue

        out.append({
            "CODIGO": cod,                    # como viene en la hoja (ya normalizado a 4 dígitos)
            "CODIGO_NORM": cod_norm,          # mismo código, upper y sin rarezas
            "NOMBRE": str(r.get("nombre") or "").strip() or cod_norm,
            "DEPARTAMENTO": str(r.get("departamento") or "").upper(),
            "GENERO": str(r.get("genero") or ""),
            "PRECIO_PRESENT": nz(r.get("p_venta"), 0.0),  # precio SOLO de la presentación
            "REQUIERE_BOTELLA": bool(req_bot),           # ahora False para 0003, 0100, etc.
        })
    return pd.DataFrame(out)

def map_pc_to_bottle_code(pc_id: str) -> str | None:
    """
    PCs siguen viniendo del inventario de productos.
    Esta función sólo mapea 'PCxxxx' -> 'Cxxxx' (o lógica similar
    que uses después), para los combos que sí incluyen botella.
    """
    if not pc_id:
        return None
    s = str(pc_id).strip().upper()
    if not s.startswith("PC") or len(s) < 3:
        return None
    return s[1:]
