from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time

IS_WIN = os.name == "nt"


# -------- UI opcional (Tkinter) --------
class _TkUI:
    def __init__(self, total_steps: int):
        self.ok = False
        self.total = max(1, int(total_steps or 1))
        self.step = 0

        try:
            import tkinter as tk
            from tkinter import ttk

            self.tk = tk
            self.ttk = ttk

            root = tk.Tk()
            root.title("Actualizando Sistema de Cotizaciones")
            root.resizable(False, False)
            root.attributes("-topmost", True)

            self.var = tk.StringVar(value="Iniciando…")
            lbl = ttk.Label(root, textvariable=self.var, padding=(14, 12))
            lbl.pack(fill="x")

            self.pb = ttk.Progressbar(root, orient="horizontal", length=520, mode="determinate", maximum=self.total)
            self.pb.pack(padx=14, pady=(0, 12), fill="x")

            self.root = root
            self._pump()
            self.ok = True
        except Exception:
            self.ok = False

    def _pump(self):
        if not self.ok:
            return
        try:
            self.root.update_idletasks()
            self.root.update()
        except Exception:
            self.ok = False

    def set_text(self, text: str):
        if not self.ok:
            return
        try:
            self.var.set(text)
            self._pump()
        except Exception:
            pass

    def advance(self, text: str = ""):
        if not self.ok:
            return
        try:
            self.step = min(self.total, self.step + 1)
            if text:
                self.var.set(text)
            self.pb["value"] = self.step
            self._pump()
        except Exception:
            pass

    def close(self):
        if not self.ok:
            return
        try:
            self.root.destroy()
        except Exception:
            pass


def _wait_pid_windows(pid: int, timeout_s: int = 180) -> None:
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        SYNCHRONIZE = 0x00100000
        OpenProcess = kernel32.OpenProcess
        OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        OpenProcess.restype = wintypes.HANDLE

        WaitForSingleObject = kernel32.WaitForSingleObject
        WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        WaitForSingleObject.restype = wintypes.DWORD

        CloseHandle = kernel32.CloseHandle
        CloseHandle.argtypes = [wintypes.HANDLE]
        CloseHandle.restype = wintypes.BOOL

        h = OpenProcess(SYNCHRONIZE, False, pid)
        if not h:
            return

        try:
            waited = 0
            step_ms = 250
            while waited < timeout_s * 1000:
                rc = WaitForSingleObject(h, step_ms)
                if rc == 0:
                    return
                waited += step_ms
        finally:
            CloseHandle(h)
    except Exception:
        time.sleep(2)


def _atomic_replace(src: str, dst: str) -> None:
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".tmp"
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)

def _safe_remove(path: str) -> None:
    try:
        if os.path.isfile(path) or os.path.islink(path):
            os.remove(path)
        elif os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--pid", type=int, default=0)
    ap.add_argument("--restart", default="")
    args = ap.parse_args()

    try:
        with open(args.plan, "r", encoding="utf-8") as f:
            plan = json.load(f)
    except Exception:
        return 10

    deletes = [p for p in (plan.get("delete", []) or []) if isinstance(p, str) and p]
    files = [it for it in (plan.get("files", []) or []) if isinstance(it, dict)]

    total_steps = 1 + len(deletes) + len(files) + 1  # wait + actions + restart
    ui = _TkUI(total_steps)

    ui.set_text("Cerrando la aplicación…")
    if args.pid and IS_WIN:
        _wait_pid_windows(args.pid, timeout_s=180)
    ui.advance("Aplicando cambios…")

    # Deletes
    for p in deletes:
        ui.advance(f"Eliminando: {os.path.basename(p)}")
        _safe_remove(p)

    # Replace files
    for item in files:
        src = item.get("src")
        dst = item.get("dst")
        if not src or not dst:
            continue
        ui.advance(f"Copiando: {os.path.basename(dst)}")
        try:
            _atomic_replace(src, dst)
        except Exception:
            ui.close()
            return 20

    # Cleanup
    ui.advance("Limpiando…")
    staging_root = plan.get("staging_root") or ""
    if isinstance(staging_root, str) and staging_root:
        _safe_remove(staging_root)
    _safe_remove(args.plan)

    # Restart app
    ui.advance("Reiniciando…")
    if args.restart:
        try:
            subprocess.Popen([args.restart], close_fds=True)
        except Exception:
            pass

    time.sleep(0.25)
    ui.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
