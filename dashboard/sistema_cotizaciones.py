import os
import sys
import json
import datetime
import pandas as pd
import shutil
import re
import math

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors

# ===== PySide6 =====
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QMessageBox, QDialog, QGroupBox,
    QHeaderView, QAbstractItemView, QTableView, QCompleter, QTableWidget, QTableWidgetItem,
    QMenu, QSizePolicy
)
from PySide6.QtGui import QAction, QKeySequence, QShortcut, QDesktopServices, QIcon, QBrush
from PySide6.QtCore import (
    Qt, QAbstractTableModel, QModelIndex, QStringListModel,
    QTimer, QUrl, QStandardPaths
)

BASE_APP_TITLE = "Cotizador"

# =========================
# Rutas
# =========================
def resource_path(relative_path: str) -> str:
    if getattr(sys, "frozen", False):
        base_dir = os.path.dirname(sys.executable)
        cand_root = os.path.join(base_dir, relative_path)
        if os.path.exists(cand_root):
            return cand_root
        cand_internal = os.path.join(base_dir, "_internal", relative_path)
        if os.path.exists(cand_internal):
            return cand_internal
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            cand_meipass = os.path.join(meipass, relative_path)
            if os.path.exists(cand_meipass):
                return cand_meipass
        return cand_root
    else:
        return os.path.join(os.path.abspath("."), relative_path)

def user_docs_root() -> str:
    try:
        base = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation) or os.path.join(os.path.expanduser("~"), "Documents")
    except Exception:
        base = os.path.join(os.path.expanduser("~"), "Documents")
    root = os.path.join(base, "Cotizaciones")
    os.makedirs(root, exist_ok=True)
    return root

def user_docs_dir(subfolder: str) -> str:
    d = os.path.join(user_docs_root(), subfolder)
    os.makedirs(d, exist_ok=True)
    return d

APP_DATA_DIR = os.path.abspath(resource_path("data"))
DATA_DIR = user_docs_dir("data")                 # SIEMPRE vacío tras instalar (no copiamos auto)
COTIZACIONES_DIR = user_docs_dir("cotizaciones")
TEMPLATES_DIR = resource_path("templates")
CONFIG_DIR    = resource_path("config")

# =========================
# Config JSON (unificado)
# =========================
DEFAULT_CONFIG = {
    "country": "PARAGUAY",
    "listing_type": "AMBOS",
    "allow_no_stock": False,
}

def _load_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def load_app_config() -> dict:
    # ⬇️ Nombre y ruta definitivos
    cfg_path = os.path.join(CONFIG_DIR, "config.json")
    cfg = DEFAULT_CONFIG.copy()
    if os.path.exists(cfg_path):
        raw = _load_json(cfg_path)
        if isinstance(raw, dict):
            cfg["country"] = str(raw.get("country", cfg["country"])).strip().upper() or cfg["country"]
            lt = str(raw.get("listing_type", cfg["listing_type"])).strip().upper()
            cfg["listing_type"] = lt if lt in ("PRODUCTOS", "PRESENTACIONES", "AMBOS") else "AMBOS"
            cfg["allow_no_stock"] = bool(raw.get("allow_no_stock", cfg["allow_no_stock"]))
    return cfg

APP_CONFIG = load_app_config()
APP_COUNTRY = APP_CONFIG["country"]            # 'PARAGUAY' | 'PERU' | 'VENEZUELA'
APP_LISTING_TYPE = APP_CONFIG["listing_type"]  # 'PRODUCTOS' | 'PRESENTACIONES' | 'AMBOS'
ALLOW_NO_STOCK = APP_CONFIG["allow_no_stock"]  # True/False

LISTING_TYPES = ("PRODUCTOS", "PRESENTACIONES", "AMBOS")

def listing_allows_products() -> bool:
    return APP_LISTING_TYPE in ("PRODUCTOS", "AMBOS")

def listing_allows_presentations() -> bool:
    return APP_LISTING_TYPE in ("PRESENTACIONES", "AMBOS")

# =========================
# Moneda / País
# =========================
def currency_for_country(country: str) -> str:
    c = (country or "").upper()
    if c == "PERU":
        return "PEN"
    if c == "VENEZUELA":
        return "USD"
    return "PYG"

APP_CURRENCY = currency_for_country(APP_COUNTRY)

def _country_suffix(country: str) -> str:
    m = {"VENEZUELA": "VE", "PERU": "PE", "PARAGUAY": "PY"}
    return m.get((country or "").upper(), "PY")

COUNTRY_CODE = _country_suffix(APP_COUNTRY)

def id_label_for_country(country: str) -> str:
    c = (country or "").upper()
    if c == "PERU":
        return "DNI/RUC"
    if c == "VENEZUELA":
        return "CEDULA / RIF"
    return "CEDULA / RUC"

def templates_country_dir() -> str:
    return os.path.join(TEMPLATES_DIR, COUNTRY_CODE)

def resolve_country_asset(filename: str) -> str | None:
    cand1 = os.path.join(templates_country_dir(), filename)
    if os.path.exists(cand1):
        return cand1
    cand2 = os.path.join(TEMPLATES_DIR, filename)
    if os.path.exists(cand2):
        return cand2
    return None

def fmt_money_ui(n: float) -> str:
    n = nz(n, 0.0)
    if APP_CURRENCY == "PEN":
        return f"S/ {n:0.2f}"
    elif APP_CURRENCY == "USD":
        return f"$ {n:0.2f}"
    else:
        return f"₲{n:0.2f}"

def fmt_money_pdf(n: float) -> str:
    n = nz(n, 0.0)
    if APP_CURRENCY == "PEN":
        return f"S/. {n:0.2f}"
    elif APP_CURRENCY == "USD":
        return f"$ {n:0.2f}"
    else:
        return f"Gs. {n:0.2f}"

# =========================
# Utilidades
# =========================
def to_float(val, default=0.0) -> float:
    try:
        if val is None:
            return default
        if isinstance(val, str):
            txt = val.strip().replace(",", "").replace(" ", "")
            if not txt:
                return default
            f = float(txt)
        else:
            f = float(val)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except Exception:
        return default

def nz(x, default=0.0):
    try:
        f = float(x)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except Exception:
        return default

def _format_grams(g: float) -> str:
    if abs(g - round(g)) < 1e-9:
        return f"{int(round(g))} g"
    return f"{g:.1f} g"

# Categorías a granel
CATS = ["ESENCIA", "AROMATERAPIA", "ESENCIAS"]

def cantidad_para_mostrar(it: dict) -> str:
    cat = (it.get("categoria") or "").upper()
    qty = it.get("cantidad", 0)
    if cat in CATS:
        if APP_COUNTRY == "PERU":
            try:
                gramos = float(qty) * 1000.0
            except Exception:
                gramos = 0.0
            return _format_grams(gramos)
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
        base_val = 0.0
        for key in ("precio_venta", "precio_unitario", "precio_base_50g"):
            base_val = nz(prod.get(key))
            if base_val:
                break
        return base_val if APP_COUNTRY == "PERU" else base_val * 50.0
    return nz(prod.get("precio_unitario", prod.get("precio_venta")))

# =========================
# Carga desde inventarios (Hoja 1, header fila 5)
# =========================
def _leer_inventario_xlsx(path: str, fuente: str) -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name=0, header=4, engine="openpyxl")
    df = df.dropna(how="all")
    cols_lower = {str(c).strip().lower(): c for c in df.columns}

    def col(*cands):
        for cnd in cands:
            cnd_l = cnd.lower()
            if cnd_l in cols_lower:
                return cols_lower[cnd_l]
        for key, orig in cols_lower.items():
            for cnd in cands:
                if cnd.lower() in key:
                    return orig
        return None

    col_codigo = col("codigo", "código", "cod.", "id", "referencia")
    col_nombre = col("nombre", "descripcion", "descripción")
    col_depto = col("departamento", "categoria", "categoría", "rubro")
    col_genero = col("genero", "género")
    col_cant = col("cantidad disponible", "cantidad", "stock", "existencia")
    col_p_venta = col("precio venta", "p. venta", "precio", "precio unitario")
    col_p_oferta = col("precio oferta", "p. oferta", "oferta")
    col_p_min = col("precio minimo", "precio mínimo", "minimo", "mínimo")

    records = []
    for _, row in df.iterrows():
        codigo = str(row.get(col_codigo, "")).strip() if col_codigo else ""
        nombre = str(row.get(col_nombre, "")).strip() if col_nombre else ""
        if not codigo and not nombre:
            continue
        depto_raw = str(row.get(col_depto, "")).strip()
        genero_raw = str(row.get(col_genero, "")).strip()
        cant = int(to_float(row.get(col_cant, 0) if col_cant else 0, 0))
        p_venta = to_float(row.get(col_p_venta, 0) if col_p_venta else 0, 0.0)
        p_oferta = to_float(row.get(col_p_oferta, 0) if col_p_oferta else 0, 0.0)
        p_min = to_float(row.get(col_p_min, 0) if col_p_min else 0, 0.0)
        depto_up = (depto_raw or "").upper()
        records.append(
            {
                "id": codigo if codigo else nombre,
                "nombre": nombre,
                "categoria": depto_up,
                "departamento_excel": depto_raw,
                "genero": genero_raw,
                "cantidad_disponible": cant,
                "precio_venta": p_venta,
                "precio_oferta_base": p_oferta,
                "precio_minimo_base": p_min,
                "__fuente": fuente,
            }
        )
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
            "No se encontró ninguno de los archivos 'inventario_lcdp.xlsx' o 'inventario_ef.xlsx' en la carpeta data/"
        )
    df = pd.concat(frames, ignore_index=True)

    compat_records = []
    for _, r in df.iterrows():
        cat = (r["categoria"] or "").upper()
        base = {
            "id": r["id"],
            "nombre": r["nombre"],
            "categoria": r["categoria"],
            "genero": r.get("genero", ""),
            "cantidad_disponible": int(nz(r.get("cantidad_disponible"), 0)),
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

# =========================
# Hoja 2 (presentaciones) – header fila 5
# =========================
def _pick_sheet_name(xls: pd.ExcelFile, desired_index: int, name_candidates: list[str]) -> str:
    sheets = xls.sheet_names
    low = {s.lower(): s for s in sheets}
    for cand in name_candidates:
        key = cand.lower()
        if key in low:
            return low[key]
    for s in sheets:
        if any(c.lower().replace(" ", "") in s.lower().replace(" ", "") for c in name_candidates):
            return s
    if len(sheets) > desired_index:
        return sheets[desired_index]
    raise RuntimeError(f"Se esperaba la hoja {desired_index+1} pero solo se encontraron: {sheets}")

def _read_sheet2_presentations(path_xlsx: str) -> pd.DataFrame:
    xls = pd.ExcelFile(path_xlsx, engine="openpyxl")
    sheet2 = _pick_sheet_name(
        xls, desired_index=1,
        name_candidates=["Hoja 2", "Hoja2", "Productos", "Productos Base", "Presentaciones", "Sheet2", "Sheet 2"],
    )
    df = pd.read_excel(xls, sheet_name=sheet2, header=4)
    if df is None or df.empty:
        return pd.DataFrame()
    rename = {}
    for c in df.columns:
        cl = str(c).strip().lower()
        if cl == "codigo": rename[c] = "codigo"
        elif cl == "nombre": rename[c] = "nombre"
        elif "departamento" in cl or "categoria" in cl: rename[c] = "departamento"
        elif "genero" in cl or "género" in cl: rename[c] = "genero"
        elif "precio venta" in cl or "p. venta" in cl or "precio" in cl: rename[c] = "p_venta"
    df = df.rename(columns=rename)
    df = df.loc[:, ~df.columns.duplicated(keep="first")]
    def _ser(colname: str) -> pd.Series:
        if colname not in df.columns:
            return pd.Series([None] * len(df), index=df.index)
        s = df[colname]
        if isinstance(s, pd.DataFrame):
            s = s.iloc[:, 0]
        return s
    cod = _ser("codigo").astype(str).str.strip()
    nom = _ser("nombre").astype(str).str.strip()
    dep = _ser("departamento").astype(str).str.strip()
    gen = _ser("genero").astype(str).str.strip()
    pventa_txt = (
        _ser("p_venta").astype(str).str.strip().str.replace(",", "", regex=False).str.replace(" ", "", regex=False)
    )
    pventa_num = pd.to_numeric(pventa_txt, errors="coerce").fillna(0.0)
    out = pd.DataFrame({"codigo": cod, "nombre": nom, "departamento": dep, "genero": gen, "p_venta": pventa_num})
    out = out.replace({"codigo": {"None": ""}, "nombre": {"None": ""}}).dropna(how="all")
    mask_all_empty = (out[["codigo", "nombre", "departamento", "genero"]].apply(lambda s: s.str.len() == 0).all(axis=1))
    out = out.loc[~mask_all_empty].reset_index(drop=True)
    return out

def _norm_pres_code(c: str) -> tuple[str, bool, bool]:
    if not c:
        return "", False, False
    cu = c.strip().upper()
    if cu == "PZA": return cu, False, True
    if cu == "E100": return "0100", True, False
    if cu == "C240": return "C240", False, False
    if re.fullmatch(r"[0-9]{3,4}", cu): return cu, True, False
    return cu, False, False

def _extract_ml_from_text(text: str) -> int:
    if not text: return 0
    m = re.search(r"(\d{2,4})\s*ml", str(text), re.I)
    if m:
        try: return int(m.group(1))
        except Exception: return 0
    m2 = re.search(r"(\d{2,4})", str(text))
    if m2:
        try: return int(m2.group(1))
        except Exception: return 0
    return 0

def _ml_from_pres_code_norm(code: str) -> int:
    if not code: return 0
    s = str(code).strip().upper()
    m = re.fullmatch(r"0*([0-9]{2,4})", s)
    if m:
        try: return int(m.group(1))
        except Exception: return 0
    m2 = re.search(r"([0-9]{2,4})", s)
    if m2:
        try: return int(m2.group(1))
        except Exception: return 0
    return 0

def cargar_presentaciones(path_xlsx: str) -> pd.DataFrame:
    if not os.path.exists(path_xlsx):
        raise FileNotFoundError(f"No se encontró el archivo: {path_xlsx}")
    df2 = _read_sheet2_presentations(path_xlsx)
    out = []
    for _, r in df2.iterrows():
        cod = str(r.get("codigo") or "").strip()
        if not cod:
            continue
        cod_norm, req_bot, ignorar = _norm_pres_code(cod)
        if ignorar:
            continue
        out.append({
            "CODIGO": cod,
            "CODIGO_NORM": cod_norm,
            "NOMBRE": str(r.get("nombre") or "").strip() or cod_norm,
            "DEPARTAMENTO": str(r.get("departamento") or "").upper(),
            "GENERO": str(r.get("genero") or ""),
            "PRECIO_PRESENT": nz(r.get("p_venta"), 0.0),
            "REQUIERE_BOTELLA": bool(req_bot),
        })
    return pd.DataFrame(out)

# =========================
# Reglas de precio/cantidad
# =========================
def precio_unitario_por_categoria(cat: str, prod: dict, qty_units: float) -> float:
    cat_u = (cat or "").upper()
    if cat_u in CATS:
        base_val = 0.0
        for key in ("precio_venta", "precio_unitario", "precio_base_50g"):
            base_val = nz(prod.get(key))
            if base_val:
                break
        return base_val if APP_COUNTRY == "PERU" else base_val * 50.0
    if cat_u == "BOTELLAS":
        precio_unidad = nz(prod.get("precio_unidad", prod.get("precio_venta")))
        precio_oferta = nz(prod.get(">12 unidades", prod.get("precio_oferta_base")))
        precio_min    = nz(prod.get(">100 unidades", prod.get("precio_minimo_base")))
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

# =========================
# Template por país
# =========================
def resolve_template_path() -> str | None:
    for ext in ("jpg", "jpeg", "png"):
        cand = resolve_country_asset(f"template.{ext}")
        if cand:
            return cand
    for ext in ("jpg", "jpeg", "png"):
        p = os.path.join(TEMPLATES_DIR, f"template.{ext}")
        if os.path.exists(p):
            return p
    return None

# =========================
# PDF
# =========================
def _split_base_and_extra_from_name(name: str) -> tuple[str, str]:
    """Devuelve (base_name, extra_from_name) separando por '|' si existe."""
    if not name:
        return "", ""
    if "|" in name:
        left, right = name.split("|", 1)
        return left.strip(), right.strip()
    return name.strip(), ""

def generar_pdf(datos):
    cliente_raw = (datos.get("cliente","") or "").strip()
    cliente_slug = re.sub(r"[^A-Za-z0-9_-]+", "_", cliente_raw).strip("_")
    fecha_slug = datetime.datetime.now().strftime("%Y%m%d")
    nombre_archivo = os.path.join(COTIZACIONES_DIR, f"cotizacion_{cliente_slug}_{fecha_slug}.pdf")
    c = canvas.Canvas(nombre_archivo, pagesize=A4)
    c.setTitle(f"Cotización - {cliente_raw}")
    W, H = A4

    TEMPLATE_PATH = resolve_template_path()

    def x_img(px): return px / 960.0 * W
    def y_img(py): return (1 - py / 1280.0) * H

    def draw_template():
        if TEMPLATE_PATH and os.path.exists(TEMPLATE_PATH):
            c.drawImage(TEMPLATE_PATH, 0, 0, width=W, height=H)

    def draw_header_common():
        c.setFont("Helvetica", 10); c.setFillColor(colors.white)
        c.drawString(x_img(735), y_img(205), f"Fecha: {datos.get('fecha', datetime.datetime.now().strftime('%d/%m/%Y'))}")
        c.setFont("Helvetica", 10); c.setFillColor(colors.HexColor("#4f3b40"))
        cli_right = x_img(900)
        id_lbl = id_label_for_country(APP_COUNTRY)
        c.drawRightString(cli_right, y_img(310), f"Nombre/Empresa: {datos.get('cliente','')}")
        c.drawRightString(cli_right, y_img(332), f"{id_lbl}: {datos.get('cedula','')}")
        c.drawRightString(cli_right, y_img(354), f"Teléfono: {datos.get('telefono','')}")

    def draw_table_header():
        c.setFont("Helvetica-Bold", 9); c.setFillColor(colors.HexColor("#4f3b40"))
        c.drawString(col_codigo, header_y, "CÓDIGO")
        c.drawString(col_producto, header_y, "PRODUCTO")
        c.drawRightString(col_cantidad + 30, header_y, "CANTIDAD")
        c.drawRightString(col_precio + 50, header_y, "PRECIO UNITARIO")
        c.drawRightString(col_subtotal + 40, header_y, "SUBTOTAL")

    def wrap_text(text, max_width, font_name="Helvetica", font_size=9):
        words = text.split(" ")
        lines, current = [], ""
        for w in words:
            test = (current + " " + w).strip()
            if c.stringWidth(test, font_name, font_size) <= max_width:
                current = test
            else:
                if current: lines.append(current)
                current = w
        if current: lines.append(current)
        return lines

    all_items = datos["items"]
    total_bruto = round(sum(float(nz(i.get("total"))) for i in all_items), 2)

    # DESCUENTO: PRESENTACION con ml >= 30
    pres_validas = [i for i in all_items if (i.get("categoria") or "") == "PRESENTACION" and i.get("ml") and int(i["ml"]) >= 30]
    total_pres_bruto = round(sum(float(nz(i.get("total"))) for i in pres_validas), 2)
    cnt_pres = sum(int(nz(i.get("cantidad"), 0)) for i in pres_validas)

    desc_pct = 0
    if cnt_pres >= 20: desc_pct = 0.20
    elif cnt_pres >= 10: desc_pct = 0.15
    elif cnt_pres >= 5: desc_pct = 0.10
    elif cnt_pres >= 3: desc_pct = 0.05

    descuento_valor = round(total_pres_bruto * desc_pct, 2)
    total_final = round(total_bruto - descuento_valor, 2)

    TABLE_SHIFT_X = 10
    header_y = y_img(430)
    col_codigo = x_img(80) + TABLE_SHIFT_X
    col_producto = x_img(200) + TABLE_SHIFT_X
    col_cantidad = x_img(505) + TABLE_SHIFT_X
    col_precio = x_img(630) + TABLE_SHIFT_X
    col_subtotal = x_img(745) + TABLE_SHIFT_X
    top_row_y = header_y - 24
    bottom_limit = y_img(880)
    line_h = 13
    max_prod_width = (col_cantidad - 8) - col_producto

    tot_lbl_x = x_img(700)
    y_tot_1 = y_img(950); y_tot_2 = y_tot_1 - 15; y_tot_3 = y_tot_2 - 15
    val_x = x_img(880)

    bg_color = colors.Color(252/255.0, 251/255.0, 249/255.0)
    cover_x = x_img(470)
    cover_top = y_img(1000)
    cover_bottom = y_img(865)
    cover_w = W - cover_x
    cover_h = cover_top - cover_bottom
    obs_min_y = y_img(1170)

    idx = 0
    n_items = len(all_items)

    while idx < n_items:
        draw_template()
        draw_header_common()
        draw_table_header()

        row_y = top_row_y
        obs_lines = []

        while idx < n_items:
            it = all_items[idx]
            full_name = it["producto"]
            if it.get("fragancia"): full_name += f" ({it['fragancia']})"
            if it.get("observacion"): full_name += f" | {it['observacion']}"

            qty_txt = cantidad_para_mostrar(it)
            prod_lines = wrap_text(full_name, max_prod_width, "Helvetica", 9)
            n_lines = len(prod_lines)
            h_needed = n_lines * line_h + 2
            if row_y - h_needed < bottom_limit:
                break

            c.setFont("Helvetica", 9); c.setFillColor(colors.black)
            c.drawString(col_codigo, row_y, str(it["codigo"]))
            for lidx, line in enumerate(prod_lines):
                c.drawString(col_producto, row_y - lidx * line_h, line)
            c.drawRightString(col_cantidad + 30, row_y, qty_txt)
            c.drawRightString(col_precio + 50, row_y, fmt_money_pdf(float(nz(it.get("precio")))))
            c.drawRightString(col_subtotal + 40, row_y, fmt_money_pdf(float(nz(it.get("total")))))

            # --- Observaciones al pie: "- {codigo} {base_name}: {extra_from_name | observacion}"
            name_for_obs = it.get("producto", "")
            base_name, extra_from_name = _split_base_and_extra_from_name(name_for_obs)
            page_obs_text = (it.get("observacion") or "").strip()
            parts = []
            if extra_from_name:
                parts.append(extra_from_name)
            if page_obs_text:
                parts.append(page_obs_text)
            if parts:
                obs_lines.append(f"- {it['codigo']} {base_name}: " + " | ".join(parts))

            row_y -= h_needed
            idx += 1

        c.setFont("Helvetica", 9); c.setFillColor(colors.black)
        obs_x = x_img(160); obs_y = y_tot_1
        for line in obs_lines:
            c.drawString(obs_x, obs_y, line[:135]); obs_y -= 12
            if obs_y < obs_min_y: break

        is_last_page = (idx >= n_items)
        if is_last_page:
            c.setFont("Helvetica-Bold", 10); c.setFillColor(colors.HexColor("#4f3b40"))
            c.drawRightString(tot_lbl_x, y_tot_1, "TOTAL BRUTO:")
            c.drawRightString(tot_lbl_x, y_tot_2, "DESCUENTO:")
            c.drawRightString(tot_lbl_x, y_tot_3, "TOTAL FINAL:")
            c.setFont("Helvetica", 10); c.setFillColor(colors.black)
            c.drawRightString(val_x, y_tot_1, fmt_money_pdf(total_bruto))
            c.drawRightString(val_x, y_tot_2, f"- {fmt_money_pdf(descuento_valor)}")
            c.setFillColor(colors.HexColor("#4f3b40"))
            c.drawRightString(val_x, y_tot_3, fmt_money_pdf(total_final))
        else:
            c.setFillColor(bg_color)
            c.rect(cover_x, cover_bottom, cover_w, cover_h, stroke=0, fill=1)
            c.setFillColor(colors.black)
            c.showPage()

    c.save()
    return nombre_archivo

# =========================
# Diálogos y Modelo
# =========================
class SelectorTablaSimple(QDialog):
    def __init__(self, parent, titulo, filas, app_icon: QIcon = QIcon()):
        super().__init__(parent)
        self.setWindowTitle(titulo)
        self.resize(560, 420)
        if not app_icon.isNull(): self.setWindowIcon(app_icon)
        self.seleccion = None

        v = QVBoxLayout(self)
        self.entry_buscar = QLineEdit(); self.entry_buscar.setPlaceholderText("Filtrar…")
        v.addWidget(self.entry_buscar)

        self.tabla = QTableWidget(0, 4)
        self.tabla.setHorizontalHeaderLabels(["Código", "Nombre", "Departamento", "Género"])
        self.tabla.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tabla.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tabla.setSelectionBehavior(QAbstractItemView.SelectRows)
        v.addWidget(self.tabla)

        self._rows = filas[:]

        def pintar(rows):
            self.tabla.setRowCount(0)
            for r in rows:
                i = self.tabla.rowCount()
                self.tabla.insertRow(i)
                self.tabla.setItem(i, 0, QTableWidgetItem(str(r.get("codigo",""))))
                self.tabla.setItem(i, 1, QTableWidgetItem(str(r.get("nombre",""))))
                self.tabla.setItem(i, 2, QTableWidgetItem(str(r.get("categoria",""))))
                self.tabla.setItem(i, 3, QTableWidgetItem(str(r.get("genero",""))))
        self._pintar = pintar
        self._pintar(self._rows)

        def filtrar(txt):
            t = txt.lower().strip()
            if not t: return self._pintar(self._rows)
            filtrados = []
            for r in self._rows:
                if (t in str(r.get("codigo","")).lower()
                    or t in str(r.get("nombre","")).lower()
                    or t in str(r.get("categoria","")).lower()
                    or t in str(r.get("genero","")).lower()):
                    filtrados.append(r)
            self._pintar(filtrados)
        self.entry_buscar.textChanged.connect(filtrar)

        def doble_click(row, _col): self._guardar(row)
        self.tabla.cellDoubleClicked.connect(doble_click)

        btn = QPushButton("Seleccionar")
        btn.clicked.connect(lambda: self._guardar(self.tabla.currentRow()))
        v.addWidget(btn)

    def _guardar(self, row):
        if row < 0: return
        item = {
            "codigo": self.tabla.item(row, 0).text() if self.tabla.item(row, 0) else "",
            "nombre": self.tabla.item(row, 1).text() if self.tabla.item(row, 1) else "",
            "categoria": self.tabla.item(row, 2).text() if self.tabla.item(row, 2) else "",
            "genero": self.tabla.item(row, 3).text() if self.tabla.item(row, 3) else "",
        }
        self.seleccion = item
        self.accept()

# === NUEVO: Diálogo de producto personalizado/servicio ===
class CustomProductDialog(QDialog):
    def __init__(self, parent=None, app_icon: QIcon = QIcon()):
        super().__init__(parent)
        self.setWindowTitle("Agregar producto personalizado o servicio")
        self.resize(420, 260)
        if not app_icon.isNull(): self.setWindowIcon(app_icon)
        self.resultado = None

        form = QFormLayout(self)
        self.edCodigo = QLineEdit(); self.edCodigo.setPlaceholderText("Ej: SRV001 o PERS001")
        self.edNombre = QLineEdit(); self.edNombre.setPlaceholderText("Nombre del producto/servicio")
        self.edObs    = QLineEdit(); self.edObs.setPlaceholderText("Observación (opcional)")
        self.edPrecio = QLineEdit(); self.edPrecio.setPlaceholderText("Precio unitario"); self.edPrecio.setText("0.00")
        self.edCant   = QLineEdit(); self.edCant.setPlaceholderText("Cantidad"); self.edCant.setText("1")

        form.addRow("Código:", self.edCodigo)
        form.addRow("Nombre:", self.edNombre)
        form.addRow("Observación:", self.edObs)
        form.addRow("Precio:", self.edPrecio)
        form.addRow("Cantidad:", self.edCant)

        self.btnGuardar = QPushButton("Guardar")
        self.btnGuardar.clicked.connect(self._guardar)
        form.addRow(self.btnGuardar)

    def _guardar(self):
        codigo = self.edCodigo.text().strip()
        nombre = self.edNombre.text().strip()
        obs    = self.edObs.text().strip()
        precio = to_float(self.edPrecio.text(), 0.0)
        cant   = to_float(self.edCant.text(), 1.0)

        if not codigo:
            QMessageBox.warning(self, "Falta código", "Ingrese un código para el producto personalizado.")
            return
        if not nombre:
            QMessageBox.warning(self, "Falta nombre", "Ingrese un nombre para el producto personalizado.")
            return
        if precio < 0:
            QMessageBox.warning(self, "Precio inválido", "El precio no puede ser negativo.")
            return
        if cant <= 0:
            QMessageBox.warning(self, "Cantidad inválida", "La cantidad debe ser mayor que 0.")
            return

        # Cantidad entera por consistencia (no es granel)
        try:
            cant = int(round(float(cant)))
            if cant <= 0: cant = 1
        except Exception:
            cant = 1

        self.resultado = {
            "codigo": codigo,
            "nombre": nombre,
            "observacion": obs,
            "precio": float(precio),
            "cantidad": int(cant),
        }
        self.accept()

CAN_EDIT_UNIT_PRICE = (APP_COUNTRY == "PARAGUAY")

class ItemsModel(QAbstractTableModel):
    HEADERS = ["Código", "Producto", "Cantidad", "Precio Unitario", "Subtotal"]

    def __init__(self, items: list[dict]):
        super().__init__()
        self._items = items

    def rowCount(self, parent=QModelIndex()) -> int: return len(self._items)
    def columnCount(self, parent=QModelIndex()) -> int: return len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole: return None
        if orientation == Qt.Horizontal: return self.HEADERS[section]
        return str(section + 1)

    def flags(self, index):
        if not index.isValid(): return Qt.ItemIsEnabled
        base = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if index.column() == 2: return base | Qt.ItemIsEditable
        if index.column() == 3 and CAN_EDIT_UNIT_PRICE: return base | Qt.ItemIsEditable
        return base

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid(): return None
        it = self._items[index.row()]; col = index.column()

        if role == Qt.ForegroundRole and col == 2:
            try:
                cat_u = (it.get("categoria") or "").upper()
                disp = int(nz(it.get("stock_disponible"), 0))
                cant = float(nz(it.get("cantidad"), 0))
                mult = 50.0 if (APP_COUNTRY in ("VENEZUELA", "PARAGUAY") and cat_u in CATS) else 1.0
                if cant * mult > disp and disp >= 0:
                    return QBrush(Qt.red)
            except Exception:
                pass

        if role == Qt.ForegroundRole and col == 3:
            if it.get("precio_override") is not None:
                return QBrush(Qt.darkMagenta)

        if role == Qt.ToolTipRole and col == 3 and it.get("precio_override") is not None:
            return "Precio reescrito manualmente. Click derecho → 'Quitar reescritura de precio' para restaurar."

        if role == Qt.DisplayRole:
            if col == 0: return it["codigo"]
            elif col == 1:
                prod = it["producto"]
                if it.get("fragancia"): prod += f" ({it['fragancia']})"
                if it.get("observacion"): prod += f" | {it['observacion']}"
                return prod
            elif col == 2:
                cat = (it.get("categoria") or "").upper()
                if APP_COUNTRY == "PERU" and cat in CATS:
                    try: return f"{float(it.get('cantidad', 0.0)):.3f}"
                    except Exception: return "0.000"
                else:
                    try: return str(int(round(float(it.get('cantidad', 0)))))
                    except Exception: return "1"
            elif col == 3:
                base_text = fmt_money_ui(float(nz(it.get("precio"))))
                return f"{base_text} ✏️" if it.get("precio_override") is not None else base_text
            elif col == 4:
                return fmt_money_ui(float(nz(it.get("total"))))
        if role == Qt.EditRole:
            if col == 2:
                cat = (it.get("categoria") or "").upper()
                if APP_COUNTRY == "PERU" and (cat in CATS):
                    return f"{float(nz(it.get('cantidad'), 0.0)):.3f}"
                try: return str(int(round(float(nz(it.get("cantidad"), 0)))))
                except Exception: return "1"
            if col == 3 and CAN_EDIT_UNIT_PRICE:
                try: return f"{float(nz(it.get('precio'), 0.0)):.4f}"
                except Exception: return "0.0000"
        return None

    def setData(self, index, value, role=Qt.EditRole):
        if role != Qt.EditRole or not index.isValid(): return False
        row = index.row(); it = self._items[row]; col = index.column()

        if col == 2:
            cat = (it.get("categoria") or "").upper()
            min_u, step = reglas_cantidad(cat)
            txt = str(value).strip().lower().replace(",", ".")
            txt = re.sub(r"[^\d\.\-]", "", txt)
            try:
                if APP_COUNTRY == "PERU" and (cat in CATS):
                    new_qty = float(txt) if txt else float(min_u)
                    if new_qty < min_u: new_qty = min_u
                    new_qty = round(round(new_qty / step) * step, 3)
                else:
                    new_qty = int(float(txt)) if txt else int(min_u)
                    if new_qty < int(min_u): new_qty = int(min_u)
            except Exception:
                return False
            it["cantidad"] = new_qty
            override = it.get("precio_override", None)
            unit_price = float(override) if override is not None and override >= 0 else precio_unitario_por_categoria(cat, it.get("_prod", {}), float(new_qty))
            it["precio"] = float(unit_price)
            it["total"] = round(float(unit_price) * float(new_qty), 2)
            top = self.index(row, 0); bottom = self.index(row, self.columnCount() - 1)
            self.dataChanged.emit(top, bottom, [Qt.DisplayRole, Qt.EditRole]); return True

        if col == 3 and CAN_EDIT_UNIT_PRICE:
            txt = str(value).strip().replace(",", "").replace(" ", "")
            txt = re.sub(r"[^\d\.\-]", "", txt)
            try:
                new_price = float(txt) if txt else 0.0
                if new_price < 0: new_price = 0.0
            except Exception:
                return False
            it["precio_override"] = new_price
            it["precio"] = new_price
            qty = float(nz(it.get("cantidad"), 0.0))
            it["total"] = round(new_price * qty, 2)
            top = self.index(row, 0); bottom = self.index(row, self.columnCount() - 1)
            self.dataChanged.emit(top, bottom, [Qt.DisplayRole, Qt.EditRole]); return True
        return False

    def add_item(self, item: dict):
        if "precio_override" not in item: item["precio_override"] = None
        self.beginInsertRows(QModelIndex(), len(self._items), len(self._items))
        self._items.append(item)
        self.endInsertRows()

    def remove_rows(self, rows: list[int]):
        for r in sorted(set(rows), reverse=True):
            if 0 <= r < len(self._items):
                self.beginRemoveRows(QModelIndex(), r, r)
                self._items.pop(r)
                self.endRemoveRows()

# =========================
# Ventana principal
# =========================
def _map_pc_to_bottle_code(pc_id: str) -> str | None:
    if not pc_id: return None
    s = str(pc_id).strip().upper()
    if not s.startswith("PC") or len(s) < 3: return None
    return s[1:]  # quita solo el primer 'P'

class ListadoProductosDialog(QDialog):
    def __init__(self, parent, productos, presentaciones, on_select, app_icon: QIcon = QIcon()):
        super().__init__(parent)
        self.setWindowTitle("Listado de Productos")
        self.resize(720, 480)
        if not app_icon.isNull(): self.setWindowIcon(app_icon)
        self._on_select = on_select
        self.productos = productos
        self.presentaciones = presentaciones

        v = QVBoxLayout(self)
        self.entry_buscar = QLineEdit(); self.entry_buscar.setPlaceholderText("Filtrar por código, nombre, categoría, precio, stock o género…")
        v.addWidget(self.entry_buscar)

        self.tabla = QTableWidget(0, 6)
        self.tabla.setHorizontalHeaderLabels(["Código", "Nombre", "Categoría", "Precio", "Stock", "Tipo"])
        self.tabla.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tabla.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tabla.setSelectionBehavior(QAbstractItemView.SelectRows)
        v.addWidget(self.tabla)

        self._rows = []

        # Catálogo (respeta allow_no_stock)
        if listing_allows_products():
            for p in self.productos:
                stock = int(nz(p.get("cantidad_disponible"), 0))
                if stock <= 0 and not ALLOW_NO_STOCK:
                    continue
                precio = precio_base_para_listado(p)
                self._rows.append({
                    "codigo": p.get("id", ""),
                    "nombre": p.get("nombre", ""),
                    "categoria": p.get("categoria", ""),
                    "genero": p.get("genero", ""),
                    "precio": precio,
                    "stock": stock,
                    "tipo": "Catálogo"
                })

        # Presentaciones PC… (OTROS)
        if listing_allows_presentations():
            pcs = [p for p in self.productos if str(p.get("id","")).upper().startswith("PC") and (p.get("categoria","").upper() == "OTROS")]
            for pc in pcs:
                bot_code = _map_pc_to_bottle_code(pc.get("id", ""))
                bot = next((b for b in self.productos if str(b.get("id","")).upper() == (bot_code or "").upper() and (b.get("categoria","").upper() == "BOTELLAS")), None)
                bot_stock = int(nz(bot.get("cantidad_disponible"), 0)) if bot else 0
                if bot_stock <= 0 and not ALLOW_NO_STOCK:
                    continue
                stock_to_show = bot_stock if bot else int(nz(pc.get("cantidad_disponible"), 0))
                self._rows.append({
                    "codigo": pc.get("id", ""),
                    "nombre": f"Presentación (PC) - {pc.get('nombre','')}",
                    "categoria": "PRESENTACION",
                    "genero": pc.get("genero",""),
                    "precio": float(nz(pc.get("precio_unitario", pc.get("precio_venta")))),
                    "stock": stock_to_show,
                    "tipo": "Presentación"
                })

        self._pintar_tabla(self._rows)
        self.entry_buscar.textChanged.connect(self._filtrar)
        self.tabla.cellDoubleClicked.connect(self._doble_click)

    def _pintar_tabla(self, rows):
        self.tabla.setRowCount(0)
        for r in rows:
            i = self.tabla.rowCount()
            self.tabla.insertRow(i)
            self.tabla.setItem(i, 0, QTableWidgetItem(str(r["codigo"])))
            self.tabla.setItem(i, 1, QTableWidgetItem(str(r["nombre"])))
            self.tabla.setItem(i, 2, QTableWidgetItem(str(r["categoria"])))
            self.tabla.setItem(i, 3, QTableWidgetItem(fmt_money_ui(nz(r["precio"], 0.0))))
            self.tabla.setItem(i, 4, QTableWidgetItem(str(int(nz(r.get("stock", 0))))))
            self.tabla.setItem(i, 5, QTableWidgetItem(str(r["tipo"])))

    def _filtrar(self, txt):
        t = txt.lower().strip()
        if not t: return self._pintar_tabla(self._rows)
        filtrados = []
        for r in self._rows:
            if (t in str(r["codigo"]).lower()
                or t in str(r["nombre"]).lower()
                or t in str(r["categoria"]).lower()
                or t in str(r["tipo"]).lower()
                or t in str(r.get("genero","")).lower()
                or t in str(r["precio"]).lower()
                or t in str(r.get("stock","")).lower()):
                filtrados.append(r)
        self._pintar_tabla(filtrados)

    def _doble_click(self, row, _col):
        item_cod = self.tabla.item(row, 0)
        if not item_cod: return
        codigo = item_cod.text().strip()
        if self._on_select: self._on_select(codigo)

class SistemaCotizaciones(QMainWindow):
    def _update_title_with_client(self, text: str):
        name = (text or "").strip()
        self.setWindowTitle(f"{name} - {BASE_APP_TITLE}" if name else BASE_APP_TITLE)

    def __init__(self, df_productos: pd.DataFrame, df_presentaciones: pd.DataFrame, app_icon: QIcon):
        super().__init__()
        self.setWindowTitle(BASE_APP_TITLE)
        self.resize(980, 640)
        if not app_icon.isNull(): self.setWindowIcon(app_icon)

        self.productos = df_productos.to_dict("records")
        self.presentaciones = df_presentaciones.to_dict("records")
        self.items: list[dict] = []
        self._suppress_next_return = False
        self._ignore_completer = False
        self._shown_once = False
        self._app_icon = app_icon
        self._ctx_row = None

        self._botellas_pc = [p for p in self.productos if str(p.get("id","")).upper().startswith("PC") and (p.get("categoria","").upper() == "OTROS")]

        self._build_ui()
        self.entry_cliente.textChanged.connect(self._update_title_with_client)
        self._update_title_with_client(self.entry_cliente.text())
        self._build_completer()

    def _center_on_screen(self):
        scr = self.screen()
        if not scr: return
        geo = self.frameGeometry(); center = scr.availableGeometry().center(); geo.moveCenter(center)
        self.move(geo.topLeft())

    def showEvent(self, event):
        super().showEvent(event)
        if not self._shown_once:
            self._shown_once = True
            self._center_on_screen()

    # ==== abrir carpetas ====
    def abrir_carpeta_data(self):
        if not os.path.isdir(DATA_DIR):
            QMessageBox.warning(self, "Carpeta no encontrada", f"No se encontró la carpeta:\n{DATA_DIR}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(DATA_DIR)))

    def abrir_carpeta_cotizaciones(self):
        if not os.path.isdir(COTIZACIONES_DIR):
            QMessageBox.warning(self, "Carpeta no encontrada", f"No se encontró la carpeta:\n{COTIZACIONES_DIR}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(COTIZACIONES_DIR)))

    def _apply_btn_responsive(self, btn: QPushButton, min_w: int = 80, min_h: int = 28):
        sp = QSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        btn.setSizePolicy(sp)
        btn.setMinimumSize(min_w, min_h)

    def _build_ui(self):
        central = QWidget(); self.setCentralWidget(central)
        main = QVBoxLayout(central)

        grp_cli = QGroupBox("Datos del Cliente")
        form_cli = QFormLayout()
        self.entry_cliente = QLineEdit()
        self.entry_cedula = QLineEdit()
        self.entry_telefono = QLineEdit()
        self.lbl_doc = QLabel(id_label_for_country(APP_COUNTRY) + ":")
        form_cli.addRow("Nombre Completo:", self.entry_cliente)
        form_cli.addRow(self.lbl_doc, self.entry_cedula)
        form_cli.addRow("Teléfono:", self.entry_telefono)
        grp_cli.setLayout(form_cli)
        main.addWidget(grp_cli)

        htop = QHBoxLayout()
        btn_cambiar = QPushButton("Cambiar productos"); self._apply_btn_responsive(btn_cambiar); btn_cambiar.clicked.connect(self.abrir_carpeta_data)
        btn_cotizaciones = QPushButton("Cotizaciones"); self._apply_btn_responsive(btn_cotizaciones); btn_cotizaciones.clicked.connect(self.abrir_carpeta_cotizaciones)
        btn_manual = QPushButton("📘"); self._apply_btn_responsive(btn_manual, min_w=28, min_h=28); btn_manual.setToolTip("Abrir manual de usuario (PDF)"); btn_manual.clicked.connect(self.abrir_manual)
        # NUEVO botón personalizado a la izquierda del listado:
        btn_personalizado = QPushButton("Agregar producto personalizado o servicio")
        self._apply_btn_responsive(btn_personalizado, min_w=120); btn_personalizado.clicked.connect(self.agregar_producto_personalizado)
        btn_listado = QPushButton("Listado de productos"); self._apply_btn_responsive(btn_listado); btn_listado.clicked.connect(self.abrir_listado_productos)

        htop.addWidget(btn_cambiar)
        htop.addWidget(btn_cotizaciones)
        htop.addWidget(btn_manual)
        htop.addStretch(1)
        htop.addWidget(btn_personalizado)  # ⬅️ inserción aquí
        htop.addWidget(btn_listado)
        main.addLayout(htop)

        grp_bus = QGroupBox("Búsqueda de Productos")
        vbus = QVBoxLayout(); hbus = QHBoxLayout()
        self.entry_producto = QLineEdit(); self.entry_producto.setPlaceholderText("Código, nombre, categoría o tipo"); self.entry_producto.returnPressed.connect(self._on_return_pressed)
        hbus.addWidget(QLabel("Código o Nombre:")); hbus.addWidget(self.entry_producto)
        vbus.addLayout(hbus); grp_bus.setLayout(vbus); main.addWidget(grp_bus)

        grp_tab = QGroupBox("Productos Seleccionados")
        vtab = QVBoxLayout()
        self.table = QTableView()
        self.model = ItemsModel(self.items)
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)

        # Acciones (click derecho)
        self.act_edit = QAction("Editar observación…", self); self.act_edit.triggered.connect(self.editar_observacion)
        self.act_edit_price = QAction("Editar precio unitario…", self); self.act_edit_price.triggered.connect(self.editar_precio_unitario)
        self.act_clear_price = QAction("Quitar reescritura de precio", self); self.act_clear_price.triggered.connect(self.quitar_reescritura_precio)
        self.act_del = QAction("Eliminar", self); self.act_del.triggered.connect(self.eliminar_producto)

        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.mostrar_menu_tabla)
        self.table.doubleClicked.connect(self._double_click_tabla)
        QShortcut(QKeySequence.Delete, self.table, activated=self.eliminar_producto)

        vtab.addWidget(self.table)
        grp_tab.setLayout(vtab)
        main.addWidget(grp_tab)

        hact = QHBoxLayout()
        btn_prev = QPushButton("Previsualizar"); self._apply_btn_responsive(btn_prev); btn_prev.clicked.connect(self.previsualizar_datos)
        btn_gen = QPushButton("Generar Cotización"); self._apply_btn_responsive(btn_gen); btn_gen.clicked.connect(self.generar_cotizacion)
        btn_lim = QPushButton("Limpiar"); self._apply_btn_responsive(btn_lim); btn_lim.clicked.connect(self.limpiar_formulario)
        for w in (btn_prev, btn_gen, btn_lim): hact.addWidget(w)
        main.addLayout(hact)

    def _item_allows_observation_edit(self, item: dict) -> bool:
        cat = (item.get("categoria") or "").upper()
        return cat in ("BOTELLAS", "SERVICIO")

    def mostrar_menu_tabla(self, pos):
        index = self.table.indexAt(pos)
        if not index.isValid(): return
        row = index.row()
        if row < 0 or row >= len(self.items): return
        self._ctx_row = row
        self.table.selectRow(row)
        item = self.items[row]
        menu = QMenu(self)
        if CAN_EDIT_UNIT_PRICE:
            menu.addAction(self.act_edit_price)
            self.act_clear_price.setEnabled(item.get("precio_override") is not None)
            menu.addAction(self.act_clear_price)
        # Editar observación SOLO para BOTELLAS y SERVICIO
        if self._item_allows_observation_edit(item):
            if menu.actions(): menu.addSeparator()
            menu.addAction(self.act_edit)
        if menu.actions(): menu.addSeparator()
        menu.addAction(self.act_del)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _double_click_tabla(self, index: QModelIndex):
        if not index.isValid(): return
        col = index.column()
        if col in (0, 1):
            row = index.row()
            if row < 0 or row >= len(self.items): return
            item = self.items[row]
            # SOLO abrir diálogo si es BOTELLAS o SERVICIO
            if self._item_allows_observation_edit(item):
                self._abrir_dialogo_observacion(row, item)

    def _build_completer(self):
        sugerencias = []
        if listing_allows_products():
            for p in self.productos:
                cat = p.get("categoria", ""); gen = p.get("genero", "")
                sugerencias.append(f"{p['id']} - {p['nombre']} - {cat}" + (f" - {gen}" if gen else ""))
        if listing_allows_presentations():
            for pc in self._botellas_pc:
                sugerencias.append(f"{pc.get('id')} - Presentación (PC) - {pc.get('nombre','')}")
        self._sug_model = QStringListModel(sugerencias)
        self._completer = QCompleter(self._sug_model, self)
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchContains)
        self.entry_producto.setCompleter(self._completer)

        def add_from_completion(text: str):
            if self._ignore_completer:
                self._ignore_completer = False; return
            cod = str(text).split(" - ")[0].strip()
            self._suppress_next_return = True
            self._agregar_por_codigo(cod)
            QTimer.singleShot(0, self.entry_producto.clear)
            if self._completer.popup(): self._completer.popup().hide()
            self.entry_producto.setFocus()
        self._completer.activated[str].connect(add_from_completion)

    def _on_return_pressed(self):
        popup = self._completer.popup()
        if popup and popup.isVisible():
            idx = popup.currentIndex()
            if idx.isValid():
                text = idx.data()
                cod = str(text).split(" - ")[0].strip()
                self._ignore_completer = True
                self._suppress_next_return = True
                self._agregar_por_codigo(cod)
                QTimer.singleShot(0, self.entry_producto.clear)
                popup.hide()
                self.entry_producto.setFocus()
                return
        if self._suppress_next_return:
            self._suppress_next_return = False; return
        text = self.entry_producto.text().strip()
        if not text: return
        cod = text.split(" - ")[0].strip()
        self._agregar_por_codigo(cod)
        self.entry_producto.clear(); self.entry_producto.setFocus()

    # ===== Agregar producto personalizado =====
    def agregar_producto_personalizado(self):
        dlg = CustomProductDialog(self, app_icon=self._app_icon)
        if dlg.exec() != QDialog.Accepted or not dlg.resultado:
            return
        data = dlg.resultado
        unit_price = float(nz(data["precio"], 0.0))
        qty = int(nz(data["cantidad"], 1))
        item = {
            "_prod": {"precio_unitario": unit_price},  # para recalcular al cambiar cantidad
            "codigo": data["codigo"],
            "producto": data["nombre"],
            "categoria": "SERVICIO",                # ⬅️ ahora SERVICIO
            "cantidad": qty,
            "ml": "",
            "precio": unit_price,
            "total": round(unit_price * qty, 2),
            "observacion": data.get("observacion", ""),  # puede tener observación
            "stock_disponible": -1,  # -1 => sin chequeo/alerta de stock
            "precio_override": None,
        }
        self.model.add_item(item)

    # ===== Agregar por código (respeta allow_no_stock) =====
    def _agregar_por_codigo(self, cod: str):
        cod_u = (cod or "").strip().upper()

        # 1) Presentación estilo PC…
        if cod_u.startswith("PC"):
            if not listing_allows_presentations():
                QMessageBox.warning(self, "Restringido por configuración", "El tipo de listado actual no permite agregar Presentaciones.")
                return
            pc = next((p for p in self._botellas_pc if str(p.get("id","")).upper() == cod_u), None)
            if pc:
                bot_code = _map_pc_to_bottle_code(str(pc.get("id", "")))
                bot = next((b for b in self.productos if str(b.get("id","")).upper() == (bot_code or "").upper() and (b.get("categoria","").upper() == "BOTELLAS")), None)
                if bot is not None and int(nz(bot.get("cantidad_disponible"), 0)) <= 0 and not ALLOW_NO_STOCK:
                    QMessageBox.warning(self, "Sin botellas", "❌ No hay botellas disponibles para esta presentación.")
                    return
                self._selector_pc(pc)
                return

        # 2) Presentación de Hoja 2
        pres = next((p for p in self.presentaciones if str(p.get("CODIGO")).upper() == cod_u), None)
        if pres:
            if not listing_allows_presentations():
                QMessageBox.warning(self, "Restringido por configuración", "El tipo de listado actual no permite agregar Presentaciones.")
                return
            self._selector_presentacion(pres)
            return

        # 3) Producto de catálogo
        prod = next((p for p in self.productos if str(p["id"]).upper() == cod_u), None)
        if not prod:
            QMessageBox.warning(self, "Advertencia", "❌ Producto no encontrado"); return
        if not listing_allows_products():
            QMessageBox.warning(self, "Restringido por configuración", "El tipo de listado actual no permite agregar Productos.")
            return
        if int(nz(prod.get("cantidad_disponible"), 0)) <= 0 and not ALLOW_NO_STOCK:
            QMessageBox.warning(self, "Sin stock", "❌ Este producto no tiene stock disponible."); return

        cat = (prod.get("categoria") or "").upper()
        min_u, _ = reglas_cantidad(cat)
        qty_default = float(min_u)
        unit_price = precio_unitario_por_categoria(cat, prod, qty_default)
        item = {
            "_prod": prod,
            "codigo": prod["id"],
            "producto": prod["nombre"],
            "categoria": cat,
            "cantidad": qty_default,
            "ml": prod.get("ml", ""),
            "precio": float(unit_price),
            "total": round(float(unit_price) * qty_default, 2),
            "observacion": "",
            "stock_disponible": int(nz(prod.get("cantidad_disponible"), 0)),
            "precio_override": None,
        }
        self.model.add_item(item)

    def _selector_pc(self, pc: dict):
        mapped_code = _map_pc_to_bottle_code(str(pc.get("id","")))
        botella_ref = next((b for b in self.productos if mapped_code and str(b.get("id","")).upper() == mapped_code and b.get("categoria","").upper()=="BOTELLAS"), None)
        ml_botella = _extract_ml_from_text(botella_ref.get("nombre","")) if botella_ref else 0
        if ml_botella == 0: ml_botella = _extract_ml_from_text(pc.get("nombre",""))
        if ml_botella == 0:
            QMessageBox.warning(self, "PC sin ML", "No pude inferir los ml de la botella asociada a este PC.\nRenombra la botella con '... 30ml/50ml/100ml ...' o usa una presentación directa.")
            return

        pres_ml_matches = [pr for pr in self.presentaciones if _ml_from_pres_code_norm(pr.get("CODIGO_NORM") or pr.get("CODIGO")) == ml_botella]

        def base_has_match(p):
            dep_base = (p.get("categoria", "") or "").upper()
            gen_base = (p.get("genero", "") or "").strip().lower()
            for pr in pres_ml_matches:
                if (pr.get("DEPARTAMENTO", "") or "").upper() == dep_base:
                    pr_gen = (pr.get("GENERO", "") or "").strip().lower()
                    if not pr_gen or pr_gen == gen_base:
                        return True
            return False

        filas_base = [{
            "codigo": p.get("id",""),
            "nombre": p.get("nombre",""),
            "categoria": p.get("categoria",""),
            "genero": p.get("genero",""),
        } for p in self.productos if (ALLOW_NO_STOCK or int(nz(p.get("cantidad_disponible"), 0)) > 0) and base_has_match(p)]

        if not filas_base:
            QMessageBox.warning(self, "Sin bases", "No hay productos base compatibles para este PC.")
            return

        dlg_base = SelectorTablaSimple(self, "Seleccionar Producto Base", filas_base, self._app_icon)
        if dlg_base.exec() != QDialog.Accepted or not dlg_base.seleccion: return
        cod_base = dlg_base.seleccion["codigo"]
        base = next((p for p in self.productos if str(p.get("id")) == cod_base), None)
        if not base: return

        dep_base = (base.get("categoria","") or "").upper()
        gen_base = (base.get("genero","") or "").strip().lower()
        pres_candidates = []
        for pr in pres_ml_matches:
            if (pr.get("DEPARTAMENTO","") or "").upper() != dep_base: continue
            pr_gen = (pr.get("GENERO","") or "").strip().lower()
            if (not pr_gen) or (pr_gen == gen_base): pres_candidates.append(pr)
        if not pres_candidates:
            QMessageBox.warning(self, "Presentación no encontrada", f"No hay una presentación de {ml_botella} ml que coincida con '{dep_base}' y género '{gen_base or 'cualquiera'}'.")
            return

        pres_final = pres_candidates[0]
        precio_pres = float(nz(pres_final.get("PRECIO_PRESENT"), 0.0))
        precio_pc   = float(nz(pc.get("precio_unitario", pc.get("precio_venta")), 0.0))
        unit_price  = precio_pres + precio_pc

        nombre_pres = pres_final.get("NOMBRE") or pres_final.get("CODIGO_NORM") or pres_final.get("CODIGO")
        nombre_final = f"A LA MODE {base.get('nombre','')} {nombre_pres}".strip()
        codigo_final = f"{pc.get('id','')}{base.get('id','')}"
        ml = ml_botella

        stock_bot = int(nz(botella_ref.get("cantidad_disponible"), 0)) if botella_ref else 0
        stock_base = int(nz(base.get("cantidad_disponible"), 0))
        if botella_ref:
            stock_ref = min(stock_bot, stock_base) if stock_bot > 0 and stock_base > 0 else (stock_bot or stock_base)
        else:
            stock_ref = stock_base

        item = {
            "_prod": {"precio_unitario": unit_price},
            "codigo": codigo_final,
            "producto": nombre_final,
            "categoria": "PRESENTACION",
            "cantidad": 1.0,
            "ml": str(ml) if ml else "",
            "precio": float(unit_price),
            "total": round(float(unit_price) * 1.0, 2),
            "fragancia": base.get("nombre","") if dep_base in ("ESENCIA","ESENCIAS") else "",
            "observacion": "",
            "stock_disponible": int(stock_ref),
            "precio_override": None,
        }
        self.model.add_item(item)

    def _selector_presentacion(self, pres: dict):
        dep = (pres.get("DEPARTAMENTO") or "").upper()
        gen = (pres.get("GENERO") or "").strip().lower()
        base_candidates = [
            p for p in self.productos
            if (p.get("categoria","").upper() == dep)
            and ((not gen) or (str(p.get("genero","")).strip().lower() == gen))
            and (ALLOW_NO_STOCK or int(nz(p.get("cantidad_disponible"), 0)) > 0)
        ]
        if not base_candidates:
            QMessageBox.warning(self, "Sin coincidencias", f"No hay productos base para {dep} / {pres.get('GENERO','')}")
            return

        filas_base = [{"codigo": p.get("id",""), "nombre": p.get("nombre",""), "categoria": p.get("categoria",""), "genero": p.get("genero","")} for p in base_candidates]
        dlg_base = SelectorTablaSimple(self, "Seleccionar Producto Base", filas_base, self._app_icon)
        if dlg_base.exec() != QDialog.Accepted or not dlg_base.seleccion: return
        cod_base = dlg_base.seleccion["codigo"]
        base = next((p for p in base_candidates if str(p.get("id")) == cod_base), None)
        if not base: return

        botella = None
        if bool(pres.get("REQUIERE_BOTELLA", False)):
            ml_pres = _ml_from_pres_code_norm(pres.get("CODIGO_NORM") or pres.get("CODIGO") or "")
            bot_opts = []
            for b in self._botellas_pc:
                bot_code = _map_pc_to_bottle_code(str(b.get("id", "")))
                bot = next((bb for bb in self.productos if str(bb.get("id","")).upper() == (bot_code or "").upper() and (bb.get("categoria","").upper()=="BOTELLAS")), None)
                if not bot: continue
                if int(nz(bot.get("cantidad_disponible"), 0)) <= 0 and not ALLOW_NO_STOCK:
                    continue
                ml_b = _extract_ml_from_text(bot.get("nombre","")) or _extract_ml_from_text(b.get("nombre",""))
                if ml_b != ml_pres: continue
                bot_opts.append(b)
            if not bot_opts:
                QMessageBox.warning(self, "Sin botellas PC", "No hay botellas PC compatibles para esta presentación.")
                return
            botella = bot_opts[0]

        precio_pres = float(nz(pres.get("PRECIO_PRESENT"), 0.0))
        precio_bot = float(nz(botella.get("precio_unitario"), 0.0)) if botella else 0.0
        unit_price = precio_pres + precio_bot

        nombre_pres = pres.get("NOMBRE") or pres.get("CODIGO_NORM") or pres.get("CODIGO")
        nombre_final = f"A LA MODE {base.get('nombre','')} {nombre_pres}".strip()

        if botella:
            codigo_final = f"{botella.get('id','')}{base.get('id','')}"
            ml = _extract_ml_from_text(botella.get("nombre",""))
        else:
            codigo_final = f"{base.get('id','')}{pres.get('CODIGO_NORM') or pres.get('CODIGO')}"
            ml = _ml_from_pres_code_norm(pres.get('CODIGO_NORM') or pres.get('CODIGO') or "")

        stock_base = int(nz(base.get("cantidad_disponible"), 0))
        stock_ref = stock_base
        if botella:
            stock_bot = int(nz(next((bb for bb in self.productos if str(bb.get("id","")).upper() == _map_pc_to_bottle_code(str(botella.get("id",""))) and (bb.get("categoria","").upper()=="BOTELLAS")), {}).get("cantidad_disponible", 0)))
            if stock_base > 0 and stock_bot > 0:
                stock_ref = min(stock_base, stock_bot)
            elif stock_bot > 0:
                stock_ref = stock_bot

        item = {
            "_prod": {"precio_unitario": unit_price},
            "codigo": codigo_final,
            "producto": nombre_final,
            "categoria": "PRESENTACION",
            "cantidad": 1.0,
            "ml": str(ml) if ml else "",
            "precio": float(unit_price),
            "total": round(float(unit_price) * 1.0, 2),
            "fragancia": base.get("nombre","") if dep in ("ESENCIA","ESENCIAS") else "",
            "observacion": "",
            "stock_disponible": int(stock_ref),
            "precio_override": None,
        }
        self.model.add_item(item)

    def abrir_manual(self):
        ruta = resolve_country_asset("manual_usuario_sistema.pdf")
        if not ruta or not os.path.exists(ruta):
            QMessageBox.warning(self, "Manual no encontrado",
                "No se encontró 'manual_usuario_sistema.pdf' en 'templates/PAIS' ni en 'templates/'.\nCopia el manual y vuelve a intentar.")
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(ruta)))

    def abrir_listado_productos(self):
        dlg = ListadoProductosDialog(self, self.productos, self.presentaciones, self._agregar_por_codigo, app_icon=self._app_icon)
        main_geo = self.frameGeometry(); main_center = main_geo.center(); dlg_size = dlg.sizeHint()
        x = main_center.x(); y = main_center.y() - dlg_size.height()
        dlg.move(x, y); dlg.exec()

    def _abrir_dialogo_observacion(self, row: int, item: dict):
        dlg = QDialog(self); dlg.setWindowTitle("Editar Observación"); dlg.resize(320, 120)
        if not self._app_icon.isNull(): dlg.setWindowIcon(self._app_icon)
        v = QVBoxLayout(dlg); v.addWidget(QLabel("Ingrese observación (ej: Color ámbar):"))
        entry = QLineEdit(); entry.setText(item.get("observacion", "")); v.addWidget(entry)
        btn = QPushButton("Guardar")
        def _save():
            item["observacion"] = entry.text().strip()
            self.model.dataChanged.emit(self.model.index(row,0), self.model.index(row,self.model.columnCount()-1), [Qt.DisplayRole])
            dlg.accept()
        btn.clicked.connect(_save); v.addWidget(btn); dlg.exec()

    def editar_observacion(self):
        sel = self.table.selectionModel().selectedRows()
        if not sel: return
        row = sel[0].row()
        if row < 0 or row >= len(self.items): return
        item = self.items[row]
        if not self._item_allows_observation_edit(item):
            return
        self._abrir_dialogo_observacion(row, item)

    def editar_precio_unitario(self):
        if not CAN_EDIT_UNIT_PRICE: return
        row = self._ctx_row
        if row is None:
            sel = self.table.selectionModel().selectedRows()
            if not sel: return
            row = sel[0].row()
        if row < 0 or row >= len(self.items): return
        idx_price = self.model.index(row, 3)
        if not idx_price.isValid(): return
        self.table.setCurrentIndex(idx_price); self.table.edit(idx_price)

    def _recalc_price_from_rules(self, item: dict):
        cat = (item.get("categoria") or "").upper()
        qty = float(nz(item.get("cantidad"), 0.0))
        base_prod = item.get("_prod", {})
        unit_price = precio_unitario_por_categoria(cat, base_prod, qty)
        item["precio"] = float(unit_price)
        item["total"] = round(float(unit_price) * qty, 2)

    def quitar_reescritura_precio(self):
        if not CAN_EDIT_UNIT_PRICE: return
        sel = self.table.selectionModel().selectedRows()
        if not sel: return
        rows = [ix.row() for ix in sel if 0 <= ix.row() < len(self.items)]
        changed = False
        for r in rows:
            it = self.items[r]
            if it.get("precio_override") is not None:
                it["precio_override"] = None
                self._recalc_price_from_rules(it)
                changed = True
        if changed:
            top = self.model.index(min(rows), 0); bottom = self.model.index(max(rows), self.model.columnCount() - 1)
            self.model.dataChanged.emit(top, bottom, [Qt.DisplayRole, Qt.EditRole])

    def eliminar_producto(self):
        sel = self.table.selectionModel().selectedRows()
        if not sel: return
        rows = [ix.row() for ix in sel]
        self.model.remove_rows(rows)

    def previsualizar_datos(self):
        c = self.entry_cliente.text(); ci = self.entry_cedula.text(); t = self.entry_telefono.text(); items = self.items
        if not all([c, ci, t]):
            QMessageBox.warning(self, "Advertencia", "❌ Faltan datos del cliente"); return
        total_items = sum(nz(i.get("total")) for i in items) if items else 0.0
        if not items or total_items <= 0.0:
            QMessageBox.warning(self, "Advertencia", "❌ Faltan productos en la cotización"); return

        dlg = QDialog(self); dlg.setWindowTitle("Previsualización de Cotización"); dlg.resize(860, 520)
        if not self._app_icon.isNull(): self.setWindowIcon(self._app_icon); dlg.setWindowIcon(self._app_icon)
        v = QVBoxLayout(dlg)
        id_lbl = id_label_for_country(APP_COUNTRY)
        v.addWidget(QLabel(f"<b>Nombre:</b> {c}"))
        v.addWidget(QLabel(f"<b>{id_lbl}:</b> {ci}"))
        v.addWidget(QLabel(f"<b>Teléfono:</b> {t}"))

        tbl = QTableWidget(0, 5)
        tbl.setHorizontalHeaderLabels(["Código", "Producto", "Cantidad", "Precio", "Subtotal"])
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        tbl.setEditTriggers(QAbstractItemView.NoEditTriggers); tbl.setSelectionMode(QAbstractItemView.NoSelection)

        total_desc_base = 0.0; cnt_desc = 0; otros_total = 0.0
        for it in self.items:
            r = tbl.rowCount(); tbl.insertRow(r)
            prod = it["producto"]
            if it.get("fragancia"): prod += f" ({it['fragancia']})"
            if it.get("observacion"): prod += f" | {it['observacion']}"
            vals = [
                it["codigo"],
                prod,
                cantidad_para_mostrar(it),
                fmt_money_ui(float(nz(it.get("precio")))),
                fmt_money_ui(float(nz(it.get("total")))),
            ]
            for col, val in enumerate(vals):
                tbl.setItem(r, col, QTableWidgetItem(val))

            try:
                cat_u = (it.get("categoria") or "").upper()
                disp = int(nz(it.get("stock_disponible"), 0))
                cant = float(nz(it.get("cantidad"), 0))
                mult = 50.0 if (APP_COUNTRY in ("VENEZUELA", "PARAGUAY") and cat_u in CATS) else 1.0
                if cant * mult > disp and disp >= 0:
                    qty_item = tbl.item(r, 2)
                    if qty_item: qty_item.setForeground(QBrush(Qt.red))
            except Exception:
                pass

            if (it.get("categoria") == "PRESENTACION") and it.get("ml") and int(it["ml"]) >= 30:
                total_desc_base += float(nz(it.get("total"))); cnt_desc += int(nz(it.get("cantidad"), 0)) or 0
            else:
                otros_total += float(nz(it.get("total")))

        v.addWidget(tbl)

        desc = 0
        if cnt_desc >= 20: desc = 0.20
        elif cnt_desc >= 10: desc = 0.15
        elif cnt_desc >= 5: desc = 0.10
        elif cnt_desc >= 3: desc = 0.05

        tot_desc = round(total_desc_base * (1 - desc), 2)
        ahorro = round(total_desc_base - tot_desc, 2)
        total_general = round(tot_desc + otros_total, 2)

        v.addWidget(QLabel(f"<b>Total Presentaciones ≥30ml:</b> {fmt_money_ui(total_desc_base)}"))
        if desc > 0:
            v.addWidget(QLabel(f"<b>Descuento ({int(desc * 100)}%):</b> -{fmt_money_ui(ahorro)}"))
            v.addWidget(QLabel(f"<b>Presentaciones con descuento:</b> {fmt_money_ui(tot_desc)}"))
        v.addWidget(QLabel(f"<b>Insumos/Otros:</b> {fmt_money_ui(otros_total)}"))
        v.addWidget(QLabel(f"<b>Total General:</b> {fmt_money_ui(total_general)}"))

        btn = QPushButton("Cerrar"); btn.clicked.connect(dlg.accept); v.addWidget(btn); dlg.exec()

    def generar_cotizacion(self):
        c = self.entry_cliente.text(); ci = self.entry_cedula.text(); t = self.entry_telefono.text()
        if not all([c, ci, t]):
            QMessageBox.warning(self, "Advertencia", "❌ Faltan datos del cliente"); return
        total_items = sum(nz(i.get("total")) for i in self.items) if self.items else 0.0
        if not self.items or total_items <= 0:
            QMessageBox.warning(self, "Advertencia", "❌ Agrega al menos un producto a la cotización"); return
        datos = {"fecha": datetime.datetime.now().strftime("%d/%m/%Y"), "cliente": c, "cedula": ci, "telefono": t, "metodo_pago": "Transferencia", "items": self.items}
        try:
            ruta = generar_pdf(datos)
            QMessageBox.information(self, "PDF Generado", f"📄 Cotización generada:\n{ruta}")
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(COTIZACIONES_DIR)))
        except Exception as e:
            QMessageBox.critical(self, "Error al generar PDF", f"❌ No se pudo generar la cotización en:\n{COTIZACIONES_DIR}\n\nDetalle:\n{e}")

    def limpiar_formulario(self):
        self.entry_cliente.clear(); self.entry_cedula.clear(); self.entry_telefono.clear(); self.entry_producto.clear()
        self.model.remove_rows(list(range(len(self.items))))

# =========================
# Main
# =========================
def load_app_icon() -> QIcon:
    p = resolve_country_asset("logo_sistema.ico")
    if p: return QIcon(p)
    p2 = resource_path("logo_sistema.ico")
    return QIcon(p2) if os.path.exists(p2) else QIcon()

def set_win_app_id():
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(u"Cotizador.1")
        except Exception:
            pass

def main():
    set_win_app_id()
    app = QApplication(sys.argv)
    app_icon = load_app_icon()
    if not app_icon.isNull(): app.setWindowIcon(app_icon)

    # ⚠️ Data SIEMPRE vacía al instalar: solo aseguramos carpeta
    try: os.makedirs(DATA_DIR, exist_ok=True)
    except Exception: pass

    try:
        df_productos = cargar_excel_productos_desde_inventarios(DATA_DIR)
    except Exception as e:
        QMessageBox.critical(None, "Error", f"❌ Error al cargar inventarios:\n{e}")
        sys.exit(1)

    try:
        df_presentaciones = cargar_presentaciones(os.path.join(DATA_DIR, "inventario_lcdp.xlsx"))
    except Exception as e:
        QMessageBox.critical(None, "Error", f"❌ Error al cargar presentaciones (Hoja 2 de inventario_lcdp.xlsx):\n{e}")
        sys.exit(1)

    window = SistemaCotizaciones(df_productos, df_presentaciones, app_icon=app_icon)
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
