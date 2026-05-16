from __future__ import annotations

import socket
from dataclasses import dataclass
from pathlib import Path

from .paths import TEMPLATES_DIR

try:
    from PIL import Image
except Exception:
    Image = None

ZEBRA_PORT_DEFAULT = 9100
ZEBRA_IP_DEFAULT = "192.168.1.50"

LABEL_WIDTH = 824
LABEL_HEIGHT = 136
RIGHT_LABEL_OFFSET_X = 424
NOMBRE_X = 14
NOMBRE_Y = 25
NOMBRE_FONT_H = 30
NOMBRE_FONT_W = 30
NOMBRE_FONT_MIN_H = 22
NOMBRE_FONT_MIN_W = 18
NOMBRE_MAX_WIDTH = 320
LOGO_X = 317
LOGO_Y = 26
LOGO_WIDTH = 34
CODIGO_X = 14
CODIGO_Y = 65
CODIGO_FONT_H = 52
CODIGO_FONT_W = 52
GRAMOS_X = 196
GRAMOS_Y = 72
GRAMOS_FIELD_W = 120
GRAMOS_FONT_H = 32
GRAMOS_FONT_W = 32


@dataclass
class ZplEtiqueta:
    nombre: str
    codigo: str
    gramos: str
    copias: int = 1


def _clean_zpl_text(texto: str) -> str:
    if texto is None:
        return ""
    t = str(texto).strip()
    return t.replace("^", "").replace("~", "")


def _estimated_text_width(texto: str, font_w: int) -> int:
    return int(round(len(str(texto or "")) * int(font_w) * 0.62))


def _name_font_for_text(texto: str) -> tuple[int, int]:
    text = str(texto or "")
    height = int(NOMBRE_FONT_H)
    width = int(NOMBRE_FONT_W)
    while width > NOMBRE_FONT_MIN_W and _estimated_text_width(text, width) > NOMBRE_MAX_WIDTH:
        width -= 1
        height = max(NOMBRE_FONT_MIN_H, height - 1)
    return height, width


def resolve_logo_path_for_company(company_type: str) -> str:
    comp = str(company_type or "").strip().upper()
    if comp == "EF PERFUMES":
        cands = [
            Path(TEMPLATES_DIR) / "Logo_EFperfumes.bmp",
            Path(TEMPLATES_DIR) / "Logo_EFperfumes.png",
            Path(TEMPLATES_DIR) / "logo efperfumes.bmp",
        ]
    else:
        cands = [
            Path(TEMPLATES_DIR) / "logo lcdp.jpg",
            Path(TEMPLATES_DIR) / "logo_lcdp.jpg",
            Path(TEMPLATES_DIR) / "logo.png",
        ]
    for p in cands:
        if p.exists():
            return str(p)
    raise FileNotFoundError("No se encontro el logo de la empresa en templates.")


def image_to_zpl_gfa(image_path: str, target_width: int = LOGO_WIDTH, threshold: int = 150) -> tuple[str, int, int]:
    if Image is None:
        raise RuntimeError("Pillow no esta disponible para convertir el logo a ZPL.")
    p = Path(image_path)
    if not p.exists():
        raise FileNotFoundError(f"No se encontro el logo: {image_path}")

    img = Image.open(p).convert("RGBA")
    bg = Image.new("RGBA", img.size, "WHITE")
    alpha = img.split()[3] if len(img.split()) == 4 else None
    bg.paste(img, mask=alpha)
    img = bg.convert("L")
    ratio = float(target_width) / float(img.width)
    target_height = max(1, int(img.height * ratio))
    img = img.resize((target_width, target_height))
    img = img.point(lambda px: 0 if px < threshold else 255, "1")

    width, height = img.size
    bpr = (width + 7) // 8
    total = bpr * height
    pixels = img.load()
    rows: list[str] = []
    for y in range(height):
        row = []
        for byte_index in range(bpr):
            byte = 0
            for bit in range(8):
                x = byte_index * 8 + bit
                if x < width and pixels[x, y] == 0:
                    byte |= 1 << (7 - bit)
            row.append(f"{byte:02X}")
        rows.append("".join(row))
    return f"^GFA,{total},{total},{bpr},{''.join(rows)}", width, height


def _render_label_content(et: ZplEtiqueta, logo_gfa: str, *, x_offset: int = 0) -> str:
    nombre = _clean_zpl_text(et.nombre)
    nombre_font_h, nombre_font_w = _name_font_for_text(nombre)
    codigo = _clean_zpl_text(et.codigo)
    gramos = _clean_zpl_text(et.gramos)
    x = int(x_offset)
    return f"""
^FO{x + NOMBRE_X},{NOMBRE_Y}
^A0N,{nombre_font_h},{nombre_font_w}
^FD{nombre}^FS
^FO{x + LOGO_X},{LOGO_Y}
{logo_gfa}
^FS
^FO{x + CODIGO_X},{CODIGO_Y}
^A0N,{CODIGO_FONT_H},{CODIGO_FONT_W}
^FD{codigo}^FS
^FO{x + GRAMOS_X},{GRAMOS_Y}
^A0N,{GRAMOS_FONT_H},{GRAMOS_FONT_W}
^FB{GRAMOS_FIELD_W},1,0,R,0
^FD{gramos}^FS
""".strip()


def _expand_copies(etiquetas: list[ZplEtiqueta]) -> list[ZplEtiqueta]:
    expanded: list[ZplEtiqueta] = []
    for et in etiquetas or []:
        try:
            copias = max(1, int(et.copias))
        except Exception:
            copias = 1
        for _ in range(copias):
            expanded.append(ZplEtiqueta(nombre=et.nombre, codigo=et.codigo, gramos=et.gramos, copias=1))
    return expanded


def _generar_zpl_fila(etiquetas: list[ZplEtiqueta], logo_gfa: str) -> str:
    etiqueta_izq = etiquetas[0] if len(etiquetas) >= 1 else None
    etiqueta_der = etiquetas[1] if len(etiquetas) >= 2 else None
    contenido: list[str] = []
    if etiqueta_izq is not None:
        contenido.append(_render_label_content(etiqueta_izq, logo_gfa, x_offset=0))
    if etiqueta_der is not None:
        contenido.append(_render_label_content(etiqueta_der, logo_gfa, x_offset=RIGHT_LABEL_OFFSET_X))

    return f"""
^XA
^CI28
^PW{LABEL_WIDTH}
^LL{LABEL_HEIGHT}
^LH0,0
^PR4
^MD20
^MTD
{chr(10).join(contenido)}
^PQ1
^XZ
""".strip()


def generar_zpl_etiqueta(et: ZplEtiqueta, logo_gfa: str) -> str:
    copias = max(1, int(et.copias))
    return "\n".join(_generar_zpl_fila([ZplEtiqueta(et.nombre, et.codigo, et.gramos, 1)], logo_gfa) for _ in range(copias))


def generar_zpl_lote(etiquetas: list[ZplEtiqueta], logo_path: str) -> str:
    logo_gfa, _, _ = image_to_zpl_gfa(logo_path, target_width=LOGO_WIDTH, threshold=150)
    expanded = _expand_copies(etiquetas or [])
    rows: list[str] = []
    for i in range(0, len(expanded), 2):
        rows.append(_generar_zpl_fila(expanded[i : i + 2], logo_gfa))
    return "\n".join(rows)


def imprimir_zpl_red(zpl: str, ip: str, port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(12)
        sock.connect((str(ip).strip(), int(port)))
        sock.sendall((zpl or "").encode("utf-8"))
