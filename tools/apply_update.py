from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time

IS_WIN = os.name == "nt"

def _wait_pid_windows(pid: int, timeout_s: int = 180) -> None:
    # Espera a que el proceso pid termine (sin dependencias externas)
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
                if rc == 0:  # signaled
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

    if args.pid and IS_WIN:
        _wait_pid_windows(args.pid, timeout_s=180)

    # Deletes
    for p in plan.get("delete", []) or []:
        if isinstance(p, str) and p:
            _safe_remove(p)

    # Replace files
    for item in plan.get("files", []) or []:
        try:
            src = item.get("src")
            dst = item.get("dst")
            if not src or not dst:
                continue
            _atomic_replace(src, dst)
        except Exception:
            return 20

    # Cleanup
    staging_root = plan.get("staging_root") or ""
    if isinstance(staging_root, str) and staging_root:
        _safe_remove(staging_root)
    _safe_remove(args.plan)

    # Restart app
    if args.restart:
        try:
            subprocess.Popen([args.restart], close_fds=True)
        except Exception:
            pass

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
