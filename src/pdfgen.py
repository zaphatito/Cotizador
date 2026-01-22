import os, datetime, re
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from .config import APP_COUNTRY, id_label_for_country, COUNTRY_CODE
from .paths  import COTIZACIONES_DIR, resolve_country_asset, resolve_template_path, resolve_font_asset, DATA_DIR
from .utils  import fmt_money_pdf, nz
from .pricing import cantidad_para_mostrar

# =====================================================
# LAYOUTS AJUSTABLES (sistema 960×1280; +X=DER, +Y=ABAJO)
# =====================================================

# --- Venezuela ---
LAYOUT_VE = {
    # Encabezado
    "DATE_PX": 735, "DATE_PY": 205,
    "QUOTE_SHOW": False,
    "QUOTE_PX": 735, "QUOTE_PY": 185,
    "CLIENT_RIGHT_PX": 900,
    "CLIENT_Ys": (310, 332, 354),

    # Tabla
    "HEADER_Y_PX": 430,
    "HEADER_TO_FIRST_ROW_GAP": 24,
    "COLS_PX": {"codigo": 80, "producto": 200, "cantidad": 505, "precio": 630, "subtotal": 745},
    "COLS_HEADER_ANCHOR_ADD": {"codigo": 0, "producto": 0, "cantidad": 30, "precio": 50, "subtotal": 40},
    "TABLE_SHIFT_X": 10, "TABLE_SHIFT_Y": 0,
    "ROW_LINE_H": 13,
    "BOTTOM_LIMIT_PY": 880,

    "CODE_TO_PRODUCT_GAP_PX": 8,

    # Totales
    "TOTALS_LABEL_TEXTS": ("TOTAL BRUTO:", "DESCUENTO:", "TOTAL FINAL:"),
    "TOTALS_LABEL_X_PXs": (700, 700, 700),
    "TOTALS_VALUE_X_PXs": (880, 880, 880),
    "TOTALS_Ys_PX": (950, 935, 920),
    "TOTALS_FONT_SIZES": (10, 10, 10),
    "TOTALS_COLOR_LABEL": colors.HexColor("#4f3b40"),
    "SHOW_LABELS": {"BRUTO": True, "DESC": True, "FINAL": True},

    # Observaciones
    "OBS_X_PX": 160,
    "OBS_START_Y_PX": None,
    "OBS_LINE_H": 12,
    "OBS_MAX_Y_LIMIT_PX": 1170,

    # Cuadro crema intermedio
    "TOTALS_BG": {
        "color_rgb_255": (252, 251, 249),
        "x_px": 470, "bottom_py": 865, "top_py": 1000,
        "width_px": None,
    },
}

# --- Perú / Paraguay ---
# Tabla PE/PY:
# CÓDIGO | PRODUCTO | CANTIDAD | PRECIO UNITARIO | SUBTOTAL SIN DESCUENTO | DESCUENTO | SUBTOTAL
# --- Perú / Paraguay ---
# --- Perú / Paraguay ---
LAYOUT_ALT = {
    # Encabezado
    "QUOTE_SHOW": True,
    "QUOTE_PX": 685, "QUOTE_PY": 182,
    "DATE_PX": 700, "DATE_PY": 243,
    "CLIENT_RIGHT_PX": 295,
    "CLIENT_LABEL_GAP_PX": 6,
    "CLIENT_VALUE_GAP_PX": 6,
    "CLIENT_Ys": (325, 347, 369),

    # Tabla
    "HEADER_Y_PX": 452,
    "HEADER_TO_FIRST_ROW_GAP": 32,
    "COLS_PX": {
        "codigo":    124,   # ✅ un poco más a la derecha (no se sale del cuadro)
        "producto":  185,

        # ✅ producto “más chico” (arranca el bloque numérico antes)
        "cantidad":  592,

        # ✅ un poquito más de espacio entre columnas numéricas (paso 72 aprox.)
        "precio":    664,
        "subsin":    736,
        "descuento": 808,
        "subtotal":  880,
    },
    "COLS_HEADER_ANCHOR_ADD": {
        "codigo": 0, "producto": 0,
        "cantidad": 0, "precio": 0, "subsin": 0, "descuento": 0, "subtotal": 0
    },

    "TABLE_SHIFT_X": 26, "TABLE_SHIFT_Y": 12,  # ✅ un toque a la derecha
    "ROW_LINE_H": 7,
    "BOTTOM_LIMIT_PY": 880,

    "CODE_TO_PRODUCT_GAP_PX": 5,

    "NUM_COL_GAP_PX": 0.75,  # (queda igual)
    "QTY_COL_W_PX": 50,

    # Totales
    "TOTALS_LABEL_TEXTS": ("TOTAL BRUTO:", "DESCUENTO:", "TOTAL:"),
    "TOTALS_LABEL_X_PXs": (700, 700, 700),
    "TOTALS_VALUE_X_PXs": (880, 880, 880),
    "TOTALS_Ys_PX": (908, 948, 980),
    "TOTALS_FONT_SIZES": (10, 10, 10),
    "TOTALS_COLOR_LABEL": colors.HexColor("#551f31"),
    "SHOW_LABELS": {"BRUTO": False, "DESC": True, "FINAL": False},

    # Observaciones
    "OBS_X_PX": 160,
    "OBS_START_Y_PX": 940,
    "OBS_LINE_H": 12,
    "OBS_MAX_Y_LIMIT_PX": 1350,

    # Cuadro crema
    "TOTALS_BG": {
        "color_rgb_255": (255, 255, 255),
        "x_px": 470, "bottom_py": 1000, "top_py": 880,
        "width_px": None,
    },
}



# =====================================================
# Utilidades
# =====================================================

def _x_img(W, px): return px / 960.0 * W
def _y_img(H, py): return (1 - py / 1280.0) * H

def _country() -> str:
    try:
        return (COUNTRY_CODE or "PY").strip().upper()
    except Exception:
        return "PY"

def _template_path_for_country(cc: str) -> str | None:
    return (
        resolve_country_asset(f"TEMPLATE_{cc}.jpg", cc)
        or resolve_country_asset(f"TEMPLATE_{cc}.png", cc)
        or resolve_country_asset(f"TEMPLATE_{cc}.jpeg", cc)
        or resolve_template_path(cc)
    )

def _register_lufga_if_available() -> tuple[str, str]:
    reg = resolve_font_asset("Lufga", "Lufga-Regular", exts=("otf","ttf"))
    bold = resolve_font_asset("Lufga", "Lufga-Bold", exts=("otf","ttf"))

    if not reg:
        alt = os.path.join(os.path.dirname(__file__), "assets", "fonts", "Lufga-Regular.otf")
        if os.path.exists(alt): reg = alt
    if not bold:
        altb = os.path.join(os.path.dirname(__file__), "assets", "fonts", "Lufga-Bold.otf")
        if os.path.exists(altb): bold = altb

    try:
        if reg:  pdfmetrics.registerFont(TTFont("Lufga", reg))
        if bold: pdfmetrics.registerFont(TTFont("Lufga-Bold", bold))
        if reg and bold: return "Lufga", "Lufga-Bold"
        if reg:          return "Lufga", "Helvetica-Bold"
    except Exception:
        pass
    return "Helvetica", "Helvetica-Bold"

def _next_quote_number(prefix: str) -> str:
    os.makedirs(DATA_DIR, exist_ok=True)
    seq_file = os.path.join(DATA_DIR, f"seq_{prefix}.txt")
    n = 0
    try:
        if os.path.exists(seq_file):
            with open(seq_file, "r", encoding="utf-8") as fh:
                n = int((fh.read() or "0").strip())
    except Exception:
        n = 0
    n += 1
    try:
        with open(seq_file, "w", encoding="utf-8") as fh:
            fh.write(str(n))
    except Exception:
        pass
    return f"{prefix}-{n:07d}"

def _draw_totals_bg_block(c: canvas.Canvas, W, H, L: dict):
    bg = L.get("TOTALS_BG", None)
    if not bg:
        return
    r, g, b = bg.get("color_rgb_255", (252, 251, 249))
    color = colors.Color(r/255.0, g/255.0, b/255.0)

    x_px      = bg.get("x_px", 470)
    bottom_py = bg.get("bottom_py", 865)
    top_py    = bg.get("top_py", 1000)
    width_px  = bg.get("width_px", None)

    X = lambda px: _x_img(W, px)
    Y = lambda py: _y_img(H, py)

    x = X(x_px)
    w = (W - x) if width_px in (None, "", 0) else X(width_px)
    y_bottom = Y(bottom_py)
    h = Y(top_py) - y_bottom

    c.setFillColor(color)
    c.rect(x, y_bottom, w, h, stroke=0, fill=1)

# ---------- wrapping ----------
def _wrap_words(c: canvas.Canvas, text: str, max_width: float, font_name: str, font_size: int):
    words = str(text).split(" ")
    lines, current = [], ""
    for w in words:
        test = (current + " " + w).strip()
        if c.stringWidth(test, font_name, font_size) <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
                current = w
            else:
                lines.append(w)
                current = ""
    if current:
        lines.append(current)
    return lines if lines else [""]

def _wrap_chars(c: canvas.Canvas, text: str, max_width: float, font_name: str, font_size: int):
    s = str(text or "")
    if c.stringWidth(s, font_name, font_size) <= max_width:
        return [s]
    out, cur = [], ""
    for ch in s:
        test = cur + ch
        if c.stringWidth(test, font_name, font_size) <= max_width:
            cur = test
        else:
            if cur:
                out.append(cur)
                cur = ch
            else:
                out.append(ch)
                cur = ""
    if cur:
        out.append(cur)
    return out if out else [""]

def _wrap_smart(c: canvas.Canvas, text: str, max_width: float, font_name: str, font_size: int):
    s = str(text or "")
    if c.stringWidth(s, font_name, font_size) <= max_width:
        return [s]
    lines = _wrap_words(c, s, max_width, font_name, font_size)
    fixed = []
    for ln in lines:
        if c.stringWidth(ln, font_name, font_size) <= max_width:
            fixed.append(ln)
        else:
            fixed.extend(_wrap_chars(c, ln, max_width, font_name, font_size))
    return fixed if fixed else [""]

def _wrap_code_with_hyphen(c: canvas.Canvas, code: str, max_width: float, font_name: str, font_size: int):
    s = str(code or "")
    out = []
    while s:
        last_fit = 0
        for i in range(1, len(s)+1):
            if c.stringWidth(s[:i], font_name, font_size) <= max_width:
                last_fit = i
            else:
                break
        if last_fit == 0:
            last_fit = 1
        if last_fit < len(s):
            out.append(s[:last_fit] + "-")
            s = s[last_fit:]
        else:
            out.append(s[:last_fit])
            s = ""
    return out

def _draw_right_multiline(c: canvas.Canvas, x_right: float, y_top: float, lines: list[str], line_h: float):
    for i, ln in enumerate(lines):
        c.drawRightString(x_right, y_top - i * line_h, ln)

# =====================================================
# Generación de PDF
# =====================================================

def generar_pdf(datos: dict, fixed_quote_no: str | None = None, out_path: str | None = None) -> str:
    cc = _country()
    is_alt = cc in {"PE", "PY"}
    L = LAYOUT_ALT if is_alt else LAYOUT_VE

    TEXT_COLOR = colors.HexColor("#551f31") if is_alt else colors.black

    cliente_raw  = (datos.get("cliente","") or "").strip()
    cliente_slug = re.sub(r"[^A-Za-z0-9_-]+", "_", cliente_raw).strip("_")
    if fixed_quote_no:
        nro_cot2 = str(fixed_quote_no).strip()
        nro_cot = f"{cc}-{nro_cot2}"
    else:
        nro_cot = _next_quote_number(cc)
        nro_cot2 = nro_cot.rsplit("-", 1)[1]

    if not out_path:
        out_path = os.path.join(COTIZACIONES_DIR, f"C-{nro_cot}_{cliente_slug}.pdf")

    c = canvas.Canvas(out_path, pagesize=A4)
    c.setTitle(f"Cotización - {cliente_raw}")
    W, H = A4

    TEMPLATE_PATH = _template_path_for_country(cc)
    FONT_REG, FONT_BOLD = (_register_lufga_if_available() if is_alt else ("Helvetica", "Helvetica-Bold"))

    X = lambda px: _x_img(W, px)
    Y = lambda py: _y_img(H, py)

    def draw_background():
        if TEMPLATE_PATH and os.path.exists(TEMPLATE_PATH):
            c.drawImage(TEMPLATE_PATH, 0, 0, width=W, height=H)

    def _draw_label_colon_value(y_px: int, label: str, value: str):
        colon_x = X(L["CLIENT_RIGHT_PX"])
        pad_l   = X(L.get("CLIENT_LABEL_GAP_PX", 6))
        pad_r   = X(L.get("CLIENT_VALUE_GAP_PX", 6))
        y = Y(y_px)
        c.drawRightString(colon_x - pad_l, y, label)
        c.drawString(colon_x, y, ":")
        c.drawString(colon_x + pad_r, y, value)

    def draw_header():
        fecha_str = datetime.datetime.now().strftime("%d/%m/%Y")
        id_lbl    = id_label_for_country(APP_COUNTRY)

        c.setFont(FONT_REG, 10)
        c.setFillColor(TEXT_COLOR if is_alt else colors.white)
        c.drawString(X(L["DATE_PX"]), Y(L["DATE_PY"]), f"{fecha_str}")

        if L["QUOTE_SHOW"]:
            c.setFillColor(TEXT_COLOR)
            c.setFont(FONT_BOLD, 20)
            c.drawString(X(L["QUOTE_PX"]), Y(L["QUOTE_PY"]), f"{nro_cot2}")

        c.setFont(FONT_BOLD, 10)
        c.setFillColor(TEXT_COLOR if is_alt else colors.HexColor("#4f3b40"))
        y_cli, y_id, y_tel = L["CLIENT_Ys"]

        if is_alt:
            _draw_label_colon_value(y_cli, "Nombre/Empresa", (datos.get("cliente","") or ""))
            _draw_label_colon_value(y_id,  id_lbl,           (datos.get("cedula","") or ""))
            _draw_label_colon_value(y_tel, "Teléfono",       (datos.get("telefono","") or ""))
        else:
            cli_right = X(L["CLIENT_RIGHT_PX"])
            c.drawRightString(cli_right, Y(y_cli), f"Nombre/Empresa: {datos.get('cliente','')}")
            c.drawRightString(cli_right, Y(y_id),  f"{id_lbl}: {datos.get('cedula','')}")
            c.drawRightString(cli_right, Y(y_tel), f"Teléfono: {datos.get('telefono','')}")

    def _anchor_x(col_key: str, shift_x: float) -> float:
        base = L["COLS_PX"][col_key] + shift_x
        add  = L["COLS_HEADER_ANCHOR_ADD"].get(col_key, 0)
        return X(base + add)

    def draw_table_header(shift_x, shift_y):
        header_y = Y(L["HEADER_Y_PX"] + shift_y)
        c.setFillColor(TEXT_COLOR if is_alt else colors.HexColor("#4f3b40"))

        if is_alt:
            c.setFont(FONT_BOLD, 4)  # ✅ -2 vs 6
            col_codigo   = _anchor_x("codigo",   shift_x)
            col_producto = _anchor_x("producto", shift_x)
            c.drawString(col_codigo,   header_y, "CÓDIGO")
            c.drawString(col_producto, header_y, "PRODUCTO")

            ax_cant    = _anchor_x("cantidad",  shift_x)
            ax_precio  = _anchor_x("precio",    shift_x)
            ax_subsin  = _anchor_x("subsin",    shift_x)
            ax_desc    = _anchor_x("descuento", shift_x)
            ax_subtot  = _anchor_x("subtotal",  shift_x)

            lh = 6
            _draw_right_multiline(c, ax_cant,   header_y, ["CANTIDAD"], lh)
            _draw_right_multiline(c, ax_precio, header_y, ["PRECIO", "UNITARIO"], lh)
            _draw_right_multiline(c, ax_subsin, header_y, ["SUBTOTAL SIN", "DESCUENTO"], lh)
            _draw_right_multiline(c, ax_desc,   header_y, ["DESCUENTO"], lh)
            _draw_right_multiline(c, ax_subtot, header_y, ["SUBTOTAL"], lh)
        else:
            c.setFont(FONT_BOLD, 9)
            c.drawString(_anchor_x("codigo",   shift_x) - X(L["COLS_HEADER_ANCHOR_ADD"]["codigo"]),   header_y, "CÓDIGO")
            c.drawString(_anchor_x("producto", shift_x) - X(L["COLS_HEADER_ANCHOR_ADD"]["producto"]), header_y, "PRODUCTO")
            c.drawRightString(_anchor_x("cantidad", shift_x), header_y, "CANTIDAD")
            c.drawRightString(_anchor_x("precio",   shift_x), header_y, "PRECIO UNITARIO")
            c.drawRightString(_anchor_x("subtotal", shift_x), header_y, "SUBTOTAL")

        return header_y

    all_items = datos["items"]
    idx = 0
    n_items = len(all_items)

    while idx < n_items:
        draw_background()
        draw_header()

        shift_x, shift_y = L["TABLE_SHIFT_X"], L["TABLE_SHIFT_Y"]
        header_y = draw_table_header(shift_x, shift_y)

        col_codigo   = _anchor_x("codigo",   shift_x)
        col_producto = _anchor_x("producto", shift_x)

        ax_cantidad  = _anchor_x("cantidad", shift_x)
        ax_precio    = _anchor_x("precio",   shift_x)

        if is_alt:
            ax_subsin   = _anchor_x("subsin",   shift_x)
            ax_desc     = _anchor_x("descuento",shift_x)
            ax_subtotal = _anchor_x("subtotal", shift_x)
        else:
            ax_subtotal = _anchor_x("subtotal", shift_x)

        row_y = header_y - L["HEADER_TO_FIRST_ROW_GAP"]
        bottom_limit = Y(L["BOTTOM_LIMIT_PY"] + shift_y)
        line_h = L["ROW_LINE_H"]

        max_code_width = (col_producto - X(L["CODE_TO_PRODUCT_GAP_PX"])) - col_codigo

        if is_alt:
            gap   = X(L.get("NUM_COL_GAP_PX", 2))
            qty_w = X(L.get("QTY_COL_W_PX", 55))

            max_prod_width = (ax_cantidad - gap - qty_w) - col_producto

            max_qty_width      = qty_w
            max_precio_width   = (ax_precio   - ax_cantidad) - gap
            max_subsin_width   = (ax_subsin   - ax_precio)   - gap
            max_desc_width     = (ax_desc     - ax_subsin)   - gap
            max_subtotal_width = (ax_subtotal - ax_desc)     - gap
        else:
            max_prod_width = (ax_cantidad - X(8)) - col_producto

        page_obs_lines = []

        while idx < n_items:
            it = all_items[idx]

            full_name = it["producto"]
            if it.get("fragancia"):
                full_name += f" ({it['fragancia']})"
            if it.get("observacion"):
                full_name += f" | {it['observacion']}"

            body_fs = 5 if is_alt else 9  # ✅ -2 vs 7
            c.setFont(FONT_REG, body_fs)

            prod_lines = _wrap_smart(c, full_name, max_prod_width, FONT_REG, body_fs)
            code_lines = _wrap_code_with_hyphen(c, str(it["codigo"]), max_code_width, FONT_REG, body_fs)

            if is_alt:
                qty_txt      = cantidad_para_mostrar(it)
                precio_txt   = fmt_money_pdf(float(nz(it.get("precio"))))
                subsin_txt   = fmt_money_pdf(float(nz(it.get("subtotal"))))

                # ✅ descuento SIN "-"
                d = float(nz(it.get("descuento"), 0.0))
                d_txt = fmt_money_pdf(abs(d))

                subtotal_txt = fmt_money_pdf(float(nz(it.get("total"))))

                qty_lines      = _wrap_smart(c, qty_txt,      max_qty_width,      FONT_REG, body_fs)
                precio_lines   = _wrap_smart(c, precio_txt,   max_precio_width,   FONT_REG, body_fs)
                subsin_lines   = _wrap_smart(c, subsin_txt,   max_subsin_width,   FONT_REG, body_fs)
                desc_lines     = _wrap_smart(c, d_txt,        max_desc_width,     FONT_REG, body_fs)
                subtotal_lines = _wrap_smart(c, subtotal_txt, max_subtotal_width, FONT_REG, body_fs)

                n_lines = max(
                    len(prod_lines), len(code_lines),
                    len(qty_lines), len(precio_lines), len(subsin_lines), len(desc_lines), len(subtotal_lines)
                )
            else:
                n_lines = max(len(prod_lines), len(code_lines))

            h_needed = n_lines * line_h + 2
            if row_y - h_needed < bottom_limit:
                break

            c.setFillColor(TEXT_COLOR if is_alt else colors.black)

            for lidx, line in enumerate(code_lines):
                c.drawString(col_codigo, row_y - lidx * line_h, line)

            for lidx, line in enumerate(prod_lines):
                c.drawString(col_producto, row_y - lidx * line_h, line)

            if is_alt:
                for lidx, line in enumerate(qty_lines):
                    c.drawRightString(ax_cantidad, row_y - lidx * line_h, line)
                for lidx, line in enumerate(precio_lines):
                    c.drawRightString(ax_precio, row_y - lidx * line_h, line)
                for lidx, line in enumerate(subsin_lines):
                    c.drawRightString(ax_subsin, row_y - lidx * line_h, line)
                for lidx, line in enumerate(desc_lines):
                    c.drawRightString(ax_desc, row_y - lidx * line_h, line)
                for lidx, line in enumerate(subtotal_lines):
                    c.drawRightString(ax_subtotal, row_y - lidx * line_h, line)
            else:
                qty_txt = cantidad_para_mostrar(it)
                c.drawRightString(ax_cantidad, row_y, qty_txt)
                c.drawRightString(ax_precio,   row_y, fmt_money_pdf(float(nz(it.get("precio")))))
                c.drawRightString(ax_subtotal, row_y, fmt_money_pdf(float(nz(it.get("total")))))

            obs_txt = (it.get("observacion") or "").strip()
            if obs_txt:
                page_obs_lines.append(f"- {it['codigo']}: {obs_txt}")

            row_y -= h_needed
            idx += 1

        is_last_page = (idx >= n_items)
        if not is_last_page:
            _draw_totals_bg_block(c, W, H, L)

        # Observaciones
        c.setFont(FONT_REG, 9)
        c.setFillColor(TEXT_COLOR if is_alt else colors.black)
        obs_x = X(L["OBS_X_PX"])
        obs_y = Y(L["OBS_START_Y_PX"] if L["OBS_START_Y_PX"] is not None else L["TOTALS_Ys_PX"][0])
        obs_min_y = Y(L["OBS_MAX_Y_LIMIT_PX"])
        for line in page_obs_lines:
            c.drawString(obs_x, obs_y, line[:135])
            obs_y -= L["OBS_LINE_H"]
            if obs_y < obs_min_y:
                break

        if is_last_page:
            subtotal_bruto   = float(nz(datos.get("subtotal_bruto"), 0.0))
            descuento_total  = float(nz(datos.get("descuento_total"), 0.0))
            total_general    = float(nz(datos.get("total_general"), 0.0))

            if subtotal_bruto == 0 and descuento_total == 0 and total_general == 0:
                subtotal_bruto = round(
                    sum(float(nz(i.get("subtotal", nz(i.get("total"), 0.0)))) for i in all_items), 2
                )
                descuento_total = round(
                    sum(float(nz(i.get("descuento", 0.0))) for i in all_items), 2
                )
                total_general = round(subtotal_bruto - descuento_total, 2)

            values = (subtotal_bruto, descuento_total, total_general)
            shows  = (
                L["SHOW_LABELS"].get("BRUTO", True),
                L["SHOW_LABELS"].get("DESC",  True),
                L["SHOW_LABELS"].get("FINAL", True),
            )

            for i in range(3):
                y  = Y(L["TOTALS_Ys_PX"][i])
                lx = X(L["TOTALS_LABEL_X_PXs"][i])
                vx = X(L["TOTALS_VALUE_X_PXs"][i])

                if shows[i]:
                    c.setFont(FONT_BOLD, L["TOTALS_FONT_SIZES"][i])
                    c.setFillColor(L["TOTALS_COLOR_LABEL"])
                    c.drawRightString(lx, y, L["TOTALS_LABEL_TEXTS"][i])

                c.setFont(FONT_REG, L["TOTALS_FONT_SIZES"][i])
                c.setFillColor(TEXT_COLOR if is_alt else colors.black)

                val = values[i]
                # ✅ descuento SIN "-" para PE/PY (y también sin "-" en el total)
                if i == 1 and is_alt:
                    txt = fmt_money_pdf(abs(val))
                else:
                    txt = fmt_money_pdf(val if i != 1 else abs(val))
                    if i == 1 and val != 0:
                        txt = f"- {txt}"

                c.drawRightString(vx, y, txt)
        else:
            c.showPage()

    c.save()
    return out_path
