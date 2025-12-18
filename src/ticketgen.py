# src/ticketgen.py
from __future__ import annotations

import os
import base64
from typing import Callable, Optional

# 203 dpi ≈ 8 dots/mm
DOTS_PER_MM = 8

# RPT004: DIP SW-5 define 42 o 48 chars/line. Usa el que te salga en self-test.
DEFAULT_TICKET_WIDTH = 48
DEFAULT_PRINTER_NAME = "TICKERA"

DEFAULT_TOP_MM = 0.0
DEFAULT_BOTTOM_MM = 10.0  # <-- lo que pediste: cortar 10mm después del último item

# Modos:
# - "full" / "partial": cortan (sin extra feed)
# - "full_feed" / "partial_feed": feed hasta posición de corte + extra y cortan (recomendado)
# - "full_save"/"partial_save": alias de *_feed (tu impresora no soporta reverse-feed 103/104)
DEFAULT_CUT_MODE = "full_feed"
OBS_MAX_LEN = 50


def _mm_to_units(mm: float) -> int:
    """Convierte mm -> unidades (aprox dots) para n (0..255)."""
    try:
        return max(0, min(255, int(round(float(mm) * DOTS_PER_MM))))
    except Exception:
        return 0


def _cut_cmd(mode: str, extra_units: int) -> bytes:
    """
    GS V (ESC/POS)
      Function A: 1D 56 m            m=0/48 full, m=1/49 partial
      Function B: 1D 56 m n          m=65 full_feed, m=66 partial_feed
    Function B: feed to (cut pos + n * vertical motion unit) and cut.
    """
    m = (mode or "").strip().lower()
    n = max(0, min(255, int(extra_units)))

    if m in ("none", "no", "off"):
        return b""

    # aliases (tu "save" ahora es simplemente feed+cut)
    if m == "full_save":
        m = "full_feed"
    elif m == "partial_save":
        m = "partial_feed"

    if m in ("full_feed", "feed_full"):
        return b"\x1d\x56" + bytes([65, n])  # GS V 65 n
    if m in ("partial_feed", "feed_partial"):
        return b"\x1d\x56" + bytes([66, n])  # GS V 66 n

    if m == "partial":
        return b"\x1d\x56\x01"  # GS V 1
    # default: full
    return b"\x1d\x56\x00"      # GS V 0


def build_ticket_text(
    items: list[dict],
    *,
    quote_number: str,
    cliente_nombre: str = "",
    width: int = DEFAULT_TICKET_WIDTH,
    qty_text_fn: Optional[Callable[[dict], str]] = None,
    obs_max_len: int = OBS_MAX_LEN,
) -> str:
    if not items:
        return ""

    width = max(1, int(width))
    qty_col = 10
    code_col = max(1, width - qty_col)

    def _pick_code(it: dict) -> str:
        for k in ("codigo", "code", "cod", "sku", "SKU", "codigo_producto", "id_producto", "id"):
            v = it.get(k)
            if v is None:
                continue
            s = str(v).strip()
            if s:
                return s
        return ""

    lines: list[str] = []

    qn = (quote_number or "").strip()
    header1 = f"COTIZACION #{qn}" if qn else "COTIZACION"
    lines.append(header1[:width])

    cli = (cliente_nombre or "").strip()
    if cli:
        lines.append(f"Nombre: {cli}"[:width])

    lines.append(("-" * width)[:width])

    for it in items:
        code = _pick_code(it)
        if not code:
            continue

        qty_txt = ""
        if qty_text_fn is not None:
            try:
                qty_txt = str(qty_text_fn(it) or "").strip()
            except Exception:
                qty_txt = ""
        if not qty_txt:
            qty_txt = str(it.get("cantidad", "")).strip()
        if not qty_txt:
            continue

        obs = (it.get("observacion") or "").strip()
        if obs:
            obs = obs[: max(0, int(obs_max_len))].strip()

        disp_code = code.strip()
        if obs:
            sep = " - "
            max_obs = code_col - len(disp_code) - len(sep)
            if max_obs > 0:
                disp_code = f"{disp_code}{sep}{obs[:max_obs]}"
            else:
                disp_code = disp_code[:code_col]

        # --- CAMBIO: rellenar la separación con guiones ---
        code_print = disp_code[:code_col].ljust(code_col, "-")
        qty_raw = qty_txt[:qty_col]
        qty_print = qty_raw.rjust(qty_col, "-")
        # -------------------------------------------------
        lines.append(f"{code_print}{qty_print}")

    if len(lines) <= 2:
        return ""

    return "\n".join(lines) + "\n"


def build_escpos_payload(
    ticket_text: str,
    *,
    width: int = DEFAULT_TICKET_WIDTH,
    top_mm: float = DEFAULT_TOP_MM,
    bottom_mm: float = DEFAULT_BOTTOM_MM,
    cut_mode: str = DEFAULT_CUT_MODE,
) -> bytes:
    lines = ticket_text.splitlines()
    if not lines:
        return b""

    header1 = (lines[0] or "").strip()
    header2 = ""
    sep_line = ""
    body_start = 1

    if len(lines) >= 2 and (lines[1] or "").startswith("-"):
        # caso: no hay nombre, la 2da línea es separador
        sep_line = lines[1]
        body_start = 2
    else:
        # caso: hay nombre y luego separador
        if len(lines) >= 2:
            header2 = (lines[1] or "").strip()
        if len(lines) >= 3:
            sep_line = lines[2]
        body_start = 3

    body_lines = lines[body_start:] if len(lines) > body_start else []

    top_units = _mm_to_units(top_mm)
    bottom_units = _mm_to_units(bottom_mm)

    # Font A (48 cols en 80mm) / Font B si quisieras más cols
    font_cmd = b"\x1b\x4d\x00" if int(width) <= 48 else b"\x1b\x4d\x01"

    def feed_units(n: int) -> bytes:
        # ESC J n : feed n dots (0..255)
        n = max(0, min(255, int(n)))
        return b"\x1b\x4a" + bytes([n])

    out = bytearray()
    out += b"\x1b\x40"          # init
    out += font_cmd

    if top_units:
        out += feed_units(top_units)

    # header center + bold
    out += b"\x1b\x61\x01"
    out += b"\x1b\x45\x01"
    out += header1.encode("ascii", errors="ignore") + b"\n"
    if header2:
        out += header2.encode("ascii", errors="ignore") + b"\n"
    out += b"\x1b\x45\x00"

    # body left
    out += b"\x1b\x61\x00"
    if sep_line:
        out += sep_line.encode("ascii", errors="ignore") + b"\n"
    for ln in body_lines:
        out += (ln or "").encode("ascii", errors="ignore") + b"\n"

    # GS V Function B (65/66) ya "feed hasta posición de corte + n" y corta.
    out += _cut_cmd(cut_mode, bottom_units)

    return bytes(out)


def _ps_encode(script: str) -> str:
    return base64.b64encode(script.encode("utf-16le")).decode("ascii")


def build_print_cmd_singlefile(
    *,
    escpos_b64: str,
    printer_name: str = DEFAULT_PRINTER_NAME,
) -> str:
    wanted_ps = (printer_name or "").replace("'", "''")

    ps_template = r"""$ErrorActionPreference = 'Stop'
$wanted = '__PRINTER__'
$b64 = '__B64__'
$bytes = [Convert]::FromBase64String($b64)

$printers = Get-CimInstance Win32_Printer
$target = $printers | Where-Object {
    ($_.ShareName -and ($_.ShareName -ieq $wanted -or $_.ShareName -ilike ('*' + $wanted + '*'))) -or
    ($_.Name -ieq $wanted) -or
    ($_.Name -ilike ('*' + $wanted + '*'))
} | Select-Object -First 1

if(-not $target) {
    Write-Host ('ERROR: No se encontró la impresora: ' + $wanted)
    Write-Host 'Impresoras detectadas (Name | ShareName):'
    $printers | Select-Object Name, ShareName | Format-Table -AutoSize | Out-String | Write-Host
    exit 1
}

Add-Type -Language CSharp -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class RawPrinter {
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public class DOCINFOA {
        [MarshalAs(UnmanagedType.LPWStr)]
        public string pDocName;
        [MarshalAs(UnmanagedType.LPWStr)]
        public string pOutputFile;
        [MarshalAs(UnmanagedType.LPWStr)]
        public string pDataType;
    }

    [DllImport("winspool.Drv", EntryPoint="OpenPrinterW", SetLastError=true, CharSet=CharSet.Unicode)]
    public static extern bool OpenPrinter(string pPrinterName, out IntPtr phPrinter, IntPtr pDefault);

    [DllImport("winspool.Drv", SetLastError=true)]
    public static extern bool ClosePrinter(IntPtr hPrinter);

    [DllImport("winspool.Drv", SetLastError=true, CharSet=CharSet.Unicode)]
    public static extern int StartDocPrinter(IntPtr hPrinter, int Level, [In] DOCINFOA pDocInfo);

    [DllImport("winspool.Drv", SetLastError=true)]
    public static extern bool EndDocPrinter(IntPtr hPrinter);

    [DllImport("winspool.Drv", SetLastError=true)]
    public static extern bool StartPagePrinter(IntPtr hPrinter);

    [DllImport("winspool.Drv", SetLastError=true)]
    public static extern bool EndPagePrinter(IntPtr hPrinter);

    [DllImport("winspool.Drv", SetLastError=true)]
    public static extern bool WritePrinter(IntPtr hPrinter, byte[] pBytes, int dwCount, out int dwWritten);

    public static void Send(string printerName, byte[] bytes) {
        IntPtr h;
        if(!OpenPrinter(printerName, out h, IntPtr.Zero)) {
            throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), "OpenPrinter failed");
        }
        try {
            DOCINFOA di = new DOCINFOA();
            di.pDocName = "Cotizacion Ticket";
            di.pDataType = "RAW";

            int job = StartDocPrinter(h, 1, di);
            if(job == 0) {
                throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), "StartDocPrinter failed");
            }
            try {
                if(!StartPagePrinter(h)) {
                    throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), "StartPagePrinter failed");
                }
                try {
                    int written = 0;
                    if(!WritePrinter(h, bytes, bytes.Length, out written) || written != bytes.Length) {
                        throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), "WritePrinter failed");
                    }
                } finally {
                    EndPagePrinter(h);
                }
            } finally {
                EndDocPrinter(h);
            }
        } finally {
            ClosePrinter(h);
        }
    }
}
'@

[RawPrinter]::Send($target.Name, $bytes)
exit 0
"""
    ps = ps_template.replace("__PRINTER__", wanted_ps).replace("__B64__", escpos_b64)
    enc = _ps_encode(ps)

    return f"""@echo off
setlocal
chcp 65001 >nul

powershell -NoProfile -ExecutionPolicy Bypass -EncodedCommand {enc}

if errorlevel 1 (
  echo.
  echo No se pudo imprimir el ticket.
  echo Impresora solicitada: {printer_name}
  echo.
  pause
)

endlocal
"""


def write_ticket_cmd_for_pdf(
    pdf_path: str,
    ticket_text: str,
    *,
    width: int = DEFAULT_TICKET_WIDTH,
    printer_name: str = DEFAULT_PRINTER_NAME,
    top_mm: float = DEFAULT_TOP_MM,
    bottom_mm: float = DEFAULT_BOTTOM_MM,
    cut_mode: str = DEFAULT_CUT_MODE,
) -> dict[str, str]:
    pdf_folder = os.path.dirname(os.path.abspath(pdf_path))
    base = os.path.splitext(os.path.basename(pdf_path))[0]

    tickets_dir = os.path.join(pdf_folder, "tickets")
    os.makedirs(tickets_dir, exist_ok=True)

    ticket_cmd = os.path.join(tickets_dir, f"{base}.IMPRIMIR_TICKET.cmd")

    payload = build_escpos_payload(
        ticket_text,
        width=width,
        top_mm=top_mm,
        bottom_mm=bottom_mm,
        cut_mode=cut_mode,
    )
    escpos_b64 = base64.b64encode(payload).decode("ascii")

    cmd_content = build_print_cmd_singlefile(
        escpos_b64=escpos_b64,
        printer_name=printer_name,
    )
    with open(ticket_cmd, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(cmd_content)

    return {"ticket_cmd": ticket_cmd}
