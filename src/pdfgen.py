import os, datetime, re
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from .config import APP_COUNTRY, id_label_for_country, COUNTRY_CODE
from .paths import COTIZACIONES_DIR, resolve_template_path, resolve_country_asset
from .utils import fmt_money_pdf, nz
from .pricing import cantidad_para_mostrar

# -------------------------------
# Helpers
# -------------------------------

def _split_base_and_extra_from_name(name: str) -> tuple[str, str]:
    """Devuelve (base_name, extra_from_name) separando por '|' si existe."""
    if not name:
        return "", ""
    if "|" in name:
        left, right = name.split("|", 1)
        return left.strip(), right.strip()
    return str(name).strip(), ""

def _register_lufga() -> tuple[str, str]:
    """
    Intenta registrar la fuente Lufga. 
    Devuelve (font_regular, font_bold) a usar.
    """
    # Busca en assets por país y en un directorio común.
    candidates_reg = [
        resolve_country_asset("Lufga-Regular.ttf", COUNTRY_CODE),
        resolve_country_asset("Lufga.ttf", COUNTRY_CODE),
        resolve_country_asset("Lufga-Regular.ttf", "COMMON"),
        os.path.join(os.path.dirname(__file__), "assets", "fonts", "Lufga-Regular.ttf"),
    ]
    candidates_bold = [
        resolve_country_asset("Lufga-Bold.ttf", COUNTRY_CODE),
        resolve_country_asset("Lufga Bold.ttf", COUNTRY_CODE),
        resolve_country_asset("Lufga-Bold.ttf", "COMMON"),
        os.path.join(os.path.dirname(__file__), "assets", "fonts", "Lufga-Bold.ttf"),
    ]

    reg = next((p for p in candidates_reg if p and os.path.exists(p)), None)
    bold = next((p for p in candidates_bold if p and os.path.exists(p)), None)

    try:
        if reg:
            pdfmetrics.registerFont(TTFont("Lufga", reg))
        if bold:
            pdfmetrics.registerFont(TTFont("Lufga-Bold", bold))
        if reg and bold:
            return "Lufga", "Lufga-Bold"
        if reg:
            return "Lufga", "Helvetica-Bold"
    except Exception:
        pass
    # Fallback
    return "Helvetica", "Helvetica-Bold"

def _next_quote_number(prefix: str) -> str:
    """
    Genera un número de cotización autoincremental por país.
    Guarda/lee en COTIZACIONES_DIR/seq_{prefix}.txt
    Retorna, por ejemplo: 'PE-000123'
    """
    os.makedirs(COTIZACIONES_DIR, exist_ok=True)
    seq_file = os.path.join(COTIZACIONES_DIR, f"seq_{prefix}.txt")
    current = 0
    try:
        if os.path.exists(seq_file):
            with open(seq_file, "r", encoding="utf-8") as fh:
                current = int((fh.read() or "0").strip())
    except Exception:
        current = 0
    current += 1
    try:
        with open(seq_file, "w", encoding="utf-8") as fh:
            fh.write(str(current))
    except Exception:
        # Si falla escritura, igual devolvemos el generado en memoria
        pass
    return f"{prefix}-{current:06d}"

# -------------------------------
# PDF principal
# -------------------------------

def generar_pdf(datos: dict) -> str:
    cliente_raw = (datos.get("cliente","") or "").strip()
    cliente_slug = re.sub(r"[^A-Za-z0-9_-]+", "_", cliente_raw).strip("_")

    # País con nuevo formato
    use_alt_format = (str(COUNTRY_CODE).upper() in {"PE", "PY"})

    # Número de cotización autoincremental (para PE/PY)
    nro_cotizacion = _next_quote_number(str(COUNTRY_CODE).upper()) if use_alt_format else None

    # Nombre de archivo
    if use_alt_format:
        nombre_archivo = os.path.join(COTIZACIONES_DIR, f"cotizacion_{cliente_slug}_{nro_cotizacion}.pdf")
    else:
        fecha_slug = datetime.datetime.now().strftime("%Y%m%d")
        nombre_archivo = os.path.join(COTIZACIONES_DIR, f"cotizacion_{cliente_slug}_{fecha_slug}.pdf")

    c = canvas.Canvas(nombre_archivo, pagesize=A4)
    c.setTitle(f"Cotización - {cliente_raw}")
    W, H = A4

    # Template por país
    if use_alt_format:
        TEMPLATE_PATH = (
            resolve_country_asset("template.jpg", COUNTRY_CODE)
            or resolve_country_asset("template.png", COUNTRY_CODE)
            or resolve_country_asset("template.jpeg", COUNTRY_CODE)
        )
    else:
        TEMPLATE_PATH = (
            resolve_country_asset(f"TEMPLATE_{COUNTRY_CODE}.jpg", COUNTRY_CODE)
            or resolve_country_asset(f"TEMPLATE_{COUNTRY_CODE}.png", COUNTRY_CODE)
            or resolve_country_asset(f"TEMPLATE_{COUNTRY_CODE}.jpeg", COUNTRY_CODE)
            or resolve_template_path(COUNTRY_CODE)
        )

    # Elección de fuentes
    FONT_REG, FONT_BOLD = (_register_lufga() if use_alt_format else ("Helvetica", "Helvetica-Bold"))

    def x_img(px): return px / 960.0 * W
    def y_img(py): return (1 - py / 1280.0) * H

    def draw_template():
        if TEMPLATE_PATH and os.path.exists(TEMPLATE_PATH):
            c.drawImage(TEMPLATE_PATH, 0, 0, width=W, height=H)

    def draw_header_common():
        # Todos los textos en negro para PE/PY (legibilidad).
        # Para VE conservamos el comportamiento original excepto color (lo ponemos negro igualmente).
        c.setFillColor(colors.black)

        if use_alt_format:
            # En el nuevo formato: mostramos N° de cotización en el lugar de la fecha
            c.setFont(FONT_BOLD, 11)
            c.drawString(x_img(735), y_img(205), f"COTIZACIÓN N°: {nro_cotizacion}")
        else:
            # Formato VE: seguimos mostrando Fecha (en negro)
            c.setFont(FONT_REG, 10)
            c.drawString(x_img(735), y_img(205), f"Fecha: {datos.get('fecha', datetime.datetime.now().strftime('%d/%m/%Y'))}")

        cli_right = x_img(900)
        id_lbl = id_label_for_country(APP_COUNTRY)

        c.setFont(FONT_REG, 10)
        c.drawRightString(cli_right, y_img(310), f"Nombre/Empresa: {datos.get('cliente','')}")
        c.drawRightString(cli_right, y_img(332), f"{id_lbl}: {datos.get('cedula','')}")
        c.drawRightString(cli_right, y_img(354), f"Teléfono: {datos.get('telefono','')}")

    def draw_table_header():
        c.setFont(FONT_BOLD, 10 if use_alt_format else 9)
        c.setFillColor(colors.black)
        c.drawString(col_codigo, header_y, "CÓDIGO")
        c.drawString(col_producto, header_y, "PRODUCTO")
        c.drawRightString(col_cantidad + 30, header_y, "CANTIDAD")
        c.drawRightString(col_precio + 50, header_y, "PRECIO UNITARIO")
        c.drawRightString(col_subtotal + 40, header_y, "SUBTOTAL")

    def wrap_text(text, max_width, font_name=FONT_REG, font_size=9):
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
        if current:
            lines.append(current)
        return lines

    all_items = datos["items"]
    total_bruto = round(sum(float(nz(i.get("total"))) for i in all_items), 2)

    # DESCUENTO: PRESENTACION con ml >= 30
    pres_validas = [i for i in all_items if (i.get("categoria") or "") == "PRESENTACION" and i.get("ml") and int(i["ml"]) >= 30]
    total_pres_bruto = round(sum(float(nz(i.get("total"))) for i in pres_validas), 2)
    cnt_pres = sum(int(nz(i.get("cantidad"), 0)) for i in pres_validas)

    desc_pct = 0
    if cnt_pres >= 20:
        desc_pct = 0.20
    elif cnt_pres >= 10:
        desc_pct = 0.15
    elif cnt_pres >= 5:
        desc_pct = 0.10
    elif cnt_pres >= 3:
        desc_pct = 0.05

    descuento_valor = round(total_pres_bruto * desc_pct, 2)
    total_final = round(total_bruto - descuento_valor, 2)

    # -------------------------------
    # Layout (coordenadas)
    # -------------------------------
    # Para PE/PY movemos la tabla a la derecha y hacia abajo
    TABLE_SHIFT_X = 28 if use_alt_format else 10
    TABLE_SHIFT_Y = 12 if use_alt_format else 0

    header_y = y_img(430 + TABLE_SHIFT_Y)
    col_codigo   = x_img(80)  + TABLE_SHIFT_X
    col_producto = x_img(200) + TABLE_SHIFT_X
    col_cantidad = x_img(505) + TABLE_SHIFT_X
    col_precio   = x_img(630) + TABLE_SHIFT_X
    col_subtotal = x_img(745) + TABLE_SHIFT_X

    top_row_y = header_y - 24
    bottom_limit = y_img(880 + TABLE_SHIFT_Y)  # límite inferior
    line_h = 13
    max_prod_width = (col_cantidad - 8) - col_producto

    # Totales
    tot_lbl_x = x_img(700)
    y_tot_1 = y_img(950)
    y_tot_2 = y_tot_1 - 15
    y_tot_3 = y_tot_2 - 15
    val_x = x_img(880)

    # Fondo para cubrir box si hay paginación
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
            if it.get("fragancia"):
                full_name += f" ({it['fragancia']})"
            if it.get("observacion"):
                full_name += f" | {it['observacion']}"

            qty_txt = cantidad_para_mostrar(it)
            body_font_size = 10 if use_alt_format else 9
            prod_lines = wrap_text(full_name, max_prod_width, FONT_REG, body_font_size)
            n_lines = len(prod_lines)
            h_needed = n_lines * line_h + 2
            if row_y - h_needed < bottom_limit:
                break

            c.setFont(FONT_REG, body_font_size)
            c.setFillColor(colors.black)
            c.drawString(col_codigo, row_y, str(it["codigo"]))
            for lidx, line in enumerate(prod_lines):
                c.drawString(col_producto, row_y - lidx * line_h, line)
            c.setFont(FONT_REG, body_font_size)
            c.drawRightString(col_cantidad + 30, row_y, qty_txt)
            c.drawRightString(col_precio + 50, row_y, fmt_money_pdf(float(nz(it.get("precio")))))
            c.drawRightString(col_subtotal + 40, row_y, fmt_money_pdf(float(nz(it.get("total")))))

            # Observaciones al pie: "- {codigo} {base_name}: {extra_from_name | observacion}"
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

        # Observaciones
        c.setFont(FONT_REG, 9)
        c.setFillColor(colors.black)
        obs_x = x_img(160)
        obs_y = y_tot_1
        for line in obs_lines:
            c.drawString(obs_x, obs_y, line[:135])
            obs_y -= 12
            if obs_y < obs_min_y:
                break

        is_last_page = (idx >= n_items)
        if is_last_page:
            # Etiquetas de totales
            c.setFont(FONT_BOLD, 10)
            c.setFillColor(colors.black)

            # Para PE/PY ocultamos "TOTAL BRUTO:" y "TOTAL FINAL:" (solo mostramos importes)
            if not use_alt_format:
                c.drawRightString(tot_lbl_x, y_tot_1, "TOTAL BRUTO:")
            c.drawRightString(tot_lbl_x, y_tot_2, "DESCUENTO:")
            if not use_alt_format:
                c.drawRightString(tot_lbl_x, y_tot_3, "TOTAL FINAL:")

            # Valores
            c.setFont(FONT_REG, 10)
            c.drawRightString(val_x, y_tot_1, fmt_money_pdf(total_bruto))
            c.drawRightString(val_x, y_tot_2, f"- {fmt_money_pdf(descuento_valor)}")
            c.drawRightString(val_x, y_tot_3, fmt_money_pdf(total_final))
        else:
            # Cubrimos el block de totales para no "ensuciar" páginas intermedias
            c.setFillColor(bg_color)
            c.rect(cover_x, cover_bottom, cover_w, cover_h, stroke=0, fill=1)
            c.setFillColor(colors.black)
            c.showPage()

    c.save()
    return nombre_archivo
