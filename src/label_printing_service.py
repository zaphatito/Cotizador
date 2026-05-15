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

LABEL_WIDTH = 800
LABEL_HEIGHT = 170
OUTER_X = 35
OUTER_Y = 20
OUTER_W = 750
OUTER_H = 135
LEFT_BOX_X = 42
LEFT_BOX_Y = 27
LEFT_BOX_W = 360
LEFT_BOX_H = 120
RIGHT_BOX_X = 425
RIGHT_BOX_Y = 27
RIGHT_BOX_W = 360
RIGHT_BOX_H = 120
NOMBRE_X = 55
NOMBRE_Y = 47
NOMBRE_FONT_H = 30
NOMBRE_FONT_W = 30
LOGO_X = 358
LOGO_Y = 48
LOGO_WIDTH = 34
CODIGO_X = 55
CODIGO_Y = 88
CODIGO_FONT_H = 43
CODIGO_FONT_W = 43
GRAMOS_X = 220
GRAMOS_Y = 97
GRAMOS_FONT_H = 26
GRAMOS_FONT_W = 26


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


def generar_zpl_etiqueta(et: ZplEtiqueta, logo_gfa: str) -> str:
    nombre = _clean_zpl_text(et.nombre)
    codigo = _clean_zpl_text(et.codigo)
    gramos = _clean_zpl_text(et.gramos)
    copias = max(1, int(et.copias))
    return f"""
^XA
^CI28
^PW{LABEL_WIDTH}
^LL{LABEL_HEIGHT}
^LH0,0
^PR4
^MD20
^MTD
^FO{OUTER_X},{OUTER_Y}
^GB{OUTER_W},{OUTER_H},3,B,0^FS
^FO{OUTER_X + 2},{OUTER_Y + 2}
^GB{OUTER_W - 4},{OUTER_H - 4},2,B,0^FS
^FO{LEFT_BOX_X},{LEFT_BOX_Y}
^GB{LEFT_BOX_W},{LEFT_BOX_H},2,B,0^FS
^FO{RIGHT_BOX_X},{RIGHT_BOX_Y}
^GB{RIGHT_BOX_W},{RIGHT_BOX_H},2,B,0^FS
^FO{NOMBRE_X},{NOMBRE_Y}
^A0N,{NOMBRE_FONT_H},{NOMBRE_FONT_W}
^FD{nombre}^FS
^FO{LOGO_X},{LOGO_Y}
{logo_gfa}
^FS
^FO{CODIGO_X},{CODIGO_Y}
^A0N,{CODIGO_FONT_H},{CODIGO_FONT_W}
^FD{codigo}^FS
^FO{GRAMOS_X},{GRAMOS_Y}
^A0N,{GRAMOS_FONT_H},{GRAMOS_FONT_W}
^FD{gramos}^FS
^PQ{copias}
^XZ
""".strip()


def generar_zpl_lote(etiquetas: list[ZplEtiqueta], logo_path: str) -> str:
    logo_gfa, _, _ = image_to_zpl_gfa(logo_path, target_width=LOGO_WIDTH, threshold=150)
    return "\n".join(generar_zpl_etiqueta(e, logo_gfa) for e in (etiquetas or []))


def imprimir_zpl_red(zpl: str, ip: str, port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(12)
        sock.connect((str(ip).strip(), int(port)))
        sock.sendall((zpl or "").encode("utf-8"))
