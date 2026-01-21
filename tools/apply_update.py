from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
import tempfile

IS_WIN = os.name == "nt"


def _msgbox(title: str, text: str) -> None:
    if not IS_WIN:
        return
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)  # MB_ICONERROR
    except Exception:
        pass


class _TkUI:
    def __init__(self, total_steps: int):
        self.ok = False
        self.total = max(1, int(total_steps or 1))
        self.step = 0

        try:
            import tkinter as tk
            from tkinter import ttk

            root = tk.Tk()
            root.title("Actualizando Sistema de Cotizaciones")
            root.resizable(False, False)
            root.attributes("-topmost", True)

            self.var = tk.StringVar(value="Iniciando…")
            ttk.Label(root, textvariable=self.var, padding=(14, 12)).pack(fill="x")

            self.pb = ttk.Progressbar(root, orient="horizontal", length=560, mode="determinate", maximum=self.total)
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


def _dedupe_args(args: list[str]) -> list[str]:
    # quita duplicados (case-insensitive) preservando orden
    out: list[str] = []
    seen: set[str] = set()
    for a in args:
        k = str(a).strip()
        if not k:
            continue
        kk = k.lower()
        if kk in seen:
            continue
        seen.add(kk)
        out.append(k)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--pid", type=int, default=0)
    ap.add_argument("--restart", default="")
    ap.add_argument("--log", default="")
    args = ap.parse_args()

    # ---- logging ----
    log_path = args.log.strip()
    if not log_path:
        if args.restart:
            app_root = os.path.dirname(os.path.abspath(args.restart))
            log_path = os.path.join(app_root, "updater", "apply_update.log")
        else:
            log_path = os.path.join(tempfile.gettempdir(), "CotizadorUpdate", "apply_update.log")

    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    def log(msg: str):
        try:
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{ts}] {msg}\n")
        except Exception:
            pass

    log("=== apply_update start ===")
    log(f"plan={args.plan}")
    log(f"pid={args.pid}")
    log(f"restart={args.restart}")
    log(f"log={log_path}")

    # ---- load plan ----
    try:
        with open(args.plan, "r", encoding="utf-8") as f:
            plan = json.load(f)
        if not isinstance(plan, dict):
            raise RuntimeError("plan no es dict")
    except Exception as e:
        log(f"ERROR leyendo plan: {e}")
        _msgbox("Actualización fallida", f"No se pudo leer el plan de actualización.\n\nLog:\n{log_path}")
        return 10

    deletes = [p for p in (plan.get("delete", []) or []) if isinstance(p, str) and p]
    files = [it for it in (plan.get("files", []) or []) if isinstance(it, dict)]
    installer = plan.get("installer") if isinstance(plan.get("installer"), dict) else None

    total_steps = 2 + len(deletes) + len(files) + (4 if installer else 0)
    ui = _TkUI(total_steps)

    try:
        ui.set_text("Cerrando la aplicación…")
        log("Waiting for pid to exit...")
        if args.pid and IS_WIN:
            _wait_pid_windows(args.pid, timeout_s=180)
        ui.advance("Aplicando cambios…")

        # Deletes
        for p in deletes:
            ui.advance(f"Eliminando: {os.path.basename(p)}")
            log(f"Delete: {p}")
            _safe_remove(p)

        # Replace files
        for item in files:
            src = item.get("src")
            dst = item.get("dst")
            if not src or not dst:
                continue
            ui.advance(f"Copiando: {os.path.basename(dst)}")
            log(f"Copy: {src} -> {dst}")
            _atomic_replace(src, dst)

        # Installer
        if installer:
            inst_path = str(installer.get("path") or "")
            inst_args = _dedupe_args(list(installer.get("args") or []))
            wait = bool(installer.get("wait", True))
            delete_after = bool(installer.get("delete_after", True))

            # log file para Inno (si no viene ya)
            app_root = os.path.dirname(os.path.abspath(args.restart)) if args.restart else os.path.dirname(log_path)
            inno_log = os.path.join(app_root, "updater", "inno_update.log")
            os.makedirs(os.path.dirname(inno_log), exist_ok=True)

            # Inno acepta /LOG="ruta"
            if not any(a.lower().startswith("/log") for a in inst_args):
                inst_args.append(f'/LOG="{inno_log}"')

            # Fuerza cierre por si quedó algún proceso vivo
            if IS_WIN:
                try:
                    subprocess.run(["taskkill", "/IM", "SistemaCotizaciones.exe", "/F"], capture_output=True)
                except Exception:
                    pass
                time.sleep(0.4)

            ui.advance("Ejecutando instalador…")
            log(f"Run installer: {inst_path} args={inst_args}")

            rc = 0
            if IS_WIN and wait:
                cmd = os.environ.get("ComSpec", "cmd.exe")
                proc = subprocess.Popen([cmd, "/c", "start", "", "/wait", inst_path] + inst_args, close_fds=True)
                rc = proc.wait()
            else:
                proc = subprocess.Popen([inst_path] + inst_args, close_fds=True)
                rc = proc.wait() if wait else 0

            log(f"Installer exit code: {rc}")
            if rc != 0:
                raise RuntimeError(f"Installer failed rc={rc}. Ver inno log: {inno_log}")

            if delete_after and inst_path:
                log(f"Delete installer: {inst_path}")
                _safe_remove(inst_path)

        # Cleanup
        ui.advance("Limpiando…")
        staging_root = plan.get("staging_root") or ""
        if isinstance(staging_root, str) and staging_root:
            log(f"Cleanup staging_root: {staging_root}")
            _safe_remove(staging_root)

        log(f"Delete plan: {args.plan}")
        _safe_remove(args.plan)

        # Restart
        ui.advance("Reiniciando…")
        if args.restart:
            log(f"Restart: {args.restart}")
            subprocess.Popen([args.restart], close_fds=True)

        log("SUCCESS")
        time.sleep(0.25)
        ui.close()
        return 0

    except Exception as e:
        log(f"FATAL: {e}")
        try:
            ui.close()
        except Exception:
            pass
        _msgbox("Actualización fallida", f"No se pudo aplicar la actualización.\n\nLog:\n{log_path}")
        return 99


if __name__ == "__main__":
    raise SystemExit(main())
