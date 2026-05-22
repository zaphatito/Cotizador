from __future__ import annotations

import socket
import time
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
LOGO_WIDTH = 40
CODIGO_X = 14
CODIGO_Y = 65
CODIGO_FONT_H = 52
CODIGO_FONT_W = 52
GRAMOS_X = 225
GRAMOS_Y = 80
GRAMOS_FIELD_W = 120
GRAMOS_FONT_H = 38
GRAMOS_FONT_W = 38


@dataclass
class ZplEtiqueta:
    nombre: str
    codigo: str
    gramos: str
    copias: int = 1


@dataclass(frozen=True)
class LabelPrintConfirmation:
    ok: bool
    requested_labels: int
    printed_labels: int
    expected_physical_labels: int
    counter_before: int | None = None
    counter_after: int | None = None
    counter_delta_rows: int | None = None
    status: str = ""
    error: str = ""


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


def count_requested_labels(etiquetas: list[ZplEtiqueta]) -> int:
    return len(_expand_copies(etiquetas or []))


def expected_physical_labels_for_count(requested_labels: int) -> int:
    requested = max(0, int(requested_labels or 0))
    if requested <= 0:
        return 0
    return ((requested + 1) // 2) * 2


def effective_requested_labels_for_printed(*, requested_labels: int, printed_labels: int) -> int:
    return min(max(0, int(requested_labels or 0)), max(0, int(printed_labels or 0)))


def labels_prefix(etiquetas: list[ZplEtiqueta], count: int) -> list[ZplEtiqueta]:
    expanded = _expand_copies(etiquetas or [])
    return expanded[: max(0, int(count or 0))]


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


def _decode_printer_response(data: bytes) -> str:
    txt = (data or b"").decode("utf-8", errors="ignore")
    return txt.replace("\x00", "").strip().strip('"').strip()


def get_printer_sgd_value(ip: str, port: int, variable: str, *, timeout: float = 5.0) -> str:
    cmd = f'! U1 getvar "{str(variable or "").strip()}"\r\n'.encode("ascii", errors="ignore")
    data = bytearray()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(float(timeout))
        sock.connect((str(ip).strip(), int(port)))
        sock.sendall(cmd)
        while True:
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                break
            if not chunk:
                break
            data.extend(chunk)
            if b"\n" in chunk or b"\r" in chunk:
                break
    return _decode_printer_response(bytes(data))


def get_printer_label_counter(ip: str, port: int, *, timeout: float = 5.0) -> int:
    raw = get_printer_sgd_value(ip, port, "odometer.total_label_count", timeout=timeout)
    try:
        return int(str(raw).strip().strip('"'))
    except Exception as exc:
        raise RuntimeError(f"La impresora no devolvio contador de etiquetas valido: {raw!r}") from exc


def get_printer_status(ip: str, port: int, *, timeout: float = 5.0) -> str:
    try:
        return get_printer_sgd_value(ip, port, "device.status", timeout=timeout)
    except Exception:
        return ""


def wait_for_label_print_confirmation(
    *,
    ip: str,
    port: int,
    counter_before: int,
    requested_labels: int,
    timeout_s: float = 90.0,
    poll_s: float = 1.5,
) -> LabelPrintConfirmation:
    requested = max(0, int(requested_labels or 0))
    expected_physical = expected_physical_labels_for_count(requested)
    deadline = time.monotonic() + max(1.0, float(timeout_s))
    before = int(counter_before)
    last_counter = before
    last_change_at = time.monotonic()
    last_status = ""
    last_error = ""

    while time.monotonic() < deadline:
        try:
            after = get_printer_label_counter(ip, port, timeout=min(5.0, max(1.0, float(poll_s))))
            status = get_printer_status(ip, port, timeout=2.5)
            last_status = status or last_status
        except Exception as exc:
            last_error = str(exc)
            time.sleep(max(0.2, float(poll_s)))
            continue

        if after != last_counter:
            last_counter = after
            last_change_at = time.monotonic()

        delta_rows = max(0, int(after) - before)
        printed = min(delta_rows * 2, expected_physical)
        if expected_physical > 0 and printed >= expected_physical:
            return LabelPrintConfirmation(
                ok=True,
                requested_labels=requested,
                printed_labels=printed,
                expected_physical_labels=expected_physical,
                counter_before=before,
                counter_after=after,
                counter_delta_rows=delta_rows,
                status=status,
            )

        status_l = str(status or "").strip().lower()
        has_stop_status = any(x in status_l for x in ("paper", "media", "head", "pause", "paused", "error"))
        stable_for = time.monotonic() - last_change_at
        if printed > 0 and (has_stop_status or stable_for >= 8.0):
            effective_requested = effective_requested_labels_for_printed(
                requested_labels=requested,
                printed_labels=printed,
            )
            return LabelPrintConfirmation(
                ok=True,
                requested_labels=effective_requested,
                printed_labels=printed,
                expected_physical_labels=expected_physical,
                counter_before=before,
                counter_after=after,
                counter_delta_rows=delta_rows,
                status=status,
            )

        time.sleep(max(0.2, float(poll_s)))

    delta_rows = max(0, int(last_counter) - before)
    printed = min(delta_rows * 2, expected_physical)
    if printed > 0:
        effective_requested = effective_requested_labels_for_printed(
            requested_labels=requested,
            printed_labels=printed,
        )
        return LabelPrintConfirmation(
            ok=True,
            requested_labels=effective_requested,
            printed_labels=printed,
            expected_physical_labels=expected_physical,
            counter_before=before,
            counter_after=last_counter,
            counter_delta_rows=delta_rows,
            status=last_status,
            error="timeout esperando finalizacion completa",
        )

    return LabelPrintConfirmation(
        ok=False,
        requested_labels=0,
        printed_labels=0,
        expected_physical_labels=expected_physical,
        counter_before=before,
        counter_after=last_counter,
        counter_delta_rows=delta_rows,
        status=last_status,
        error=last_error or "No se confirmo avance del contador de la impresora.",
    )
