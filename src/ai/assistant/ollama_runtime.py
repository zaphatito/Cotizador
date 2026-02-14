# src/ai/assistant/ollama_runtime.py
from __future__ import annotations

import json
import os
import sys
import time
import subprocess
import urllib.request
from typing import Optional, Callable

from ...logging_setup import get_logger

log = get_logger(__name__)

OLLAMA_HOST_URL = "http://127.0.0.1:11434"
TAGS_URL = OLLAMA_HOST_URL + "/api/tags"

DEFAULT_MODEL = "qwen2.5:14b-instruct"

# si un pull falla (offline), no reintentar cada arranque para no hacer lenta la app
PULL_BACKOFF_S = 6 * 3600


def _http_json(url: str, timeout: float = 1.2) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", errors="replace")
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _server_alive(timeout: float = 0.6) -> bool:
    try:
        _http_json(TAGS_URL, timeout=timeout)
        return True
    except Exception:
        return False


def _list_models(timeout: float = 1.2) -> set[str]:
    try:
        data = _http_json(TAGS_URL, timeout=timeout)
        models = data.get("models") or []
        out: set[str] = set()
        if isinstance(models, list):
            for m in models:
                if isinstance(m, dict):
                    name = str(m.get("name") or "").strip()
                    if name:
                        out.add(name)
        return out
    except Exception:
        return set()


def _wait_server(timeout_s: float = 10.0) -> bool:
    t0 = time.time()
    while (time.time() - t0) < timeout_s:
        if _server_alive(timeout=0.8):
            return True
        time.sleep(0.25)
    return False


def _app_version(app_root: str) -> str:
    try:
        p = os.path.join(app_root, "version.txt")
        if os.path.exists(p):
            v = (open(p, "r", encoding="utf-8").read() or "").strip()
            if v:
                return v
    except Exception:
        pass

    try:
        from ...version import __version__ as v2
        return str(v2 or "0.0.0")
    except Exception:
        return "0.0.0"


def _localappdata_dir(*parts: str) -> str:
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        base = os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(base, *parts)


def _state_path() -> str:
    # NO usar DATA_DIR para estado de IA: lo dejamos fijo en LocalAppData del sistema
    base = _localappdata_dir("SistemaCotizaciones", "assistant")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "ollama_state.json")


def _read_state() -> dict:
    p = _state_path()
    try:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                x = json.load(f)
            return x if isinstance(x, dict) else {}
    except Exception:
        return {}
    return {}


def _write_state(st: dict) -> None:
    p = _state_path()
    try:
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
    except Exception:
        pass


def _vendor_ollama_exe(app_root: str) -> str:
    return os.path.join(app_root, "vendor", "ollama", "ollama.exe")


def _prod_models_dir() -> str:
    return _localappdata_dir("SistemaCotizaciones", "ollama_models")


def _dev_models_dir(app_root: str) -> str:
    return os.path.join(app_root, "vendor", "ollama_models")


def _dir_writable(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        test = os.path.join(path, ".write_test")
        with open(test, "w", encoding="utf-8") as f:
            f.write("ok")
        try:
            os.remove(test)
        except Exception:
            pass
        return True
    except Exception:
        return False


def _choose_models_dir(app_root: str) -> tuple[str, str]:
    """
    REGLA:
    - DEV (si existe .git): repo/vendor/ollama_models SOLO si es escribible
    - PROD (instalado): %LOCALAPPDATA%/SistemaCotizaciones/ollama_models SIEMPRE (fijo)
    - Si DEV no es escribible (OneDrive/CFA), cae a PROD sin pedir admin.
    """
    is_dev = os.path.isdir(os.path.join(app_root, ".git"))
    dev_dir = _dev_models_dir(app_root)
    prod_dir = _prod_models_dir()

    if is_dev and _dir_writable(dev_dir):
        return dev_dir, "dev"

    if _dir_writable(prod_dir):
        return prod_dir, "prod"

    # último recurso (no debería pasar): intenta dev si estamos en repo, si no prod.
    return (dev_dir if is_dev else prod_dir), ("dev" if is_dev else "prod")


def _hidden_flags() -> tuple[int, Optional[subprocess.STARTUPINFO]]:
    """
    Flags/StartupInfo para NO abrir ventana de consola en Windows.
    """
    creationflags = 0
    si: Optional[subprocess.STARTUPINFO] = None

    if os.name == "nt":
        creationflags |= int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        creationflags |= int(getattr(subprocess, "DETACHED_PROCESS", 0))
        creationflags |= int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        try:
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0
        except Exception:
            si = None

    return creationflags, si


def _start_server_best_effort(app_root: str) -> bool:
    """
    Inicia el server si no está vivo.
    IMPORTANTE: NO usamos serve.cmd ni cmd.exe (eso es lo que te abre consola).
    Arrancamos directo: ollama.exe serve en background sin ventana.
    """
    if _server_alive():
        return True

    ollama_exe = _vendor_ollama_exe(app_root)
    if not os.path.exists(ollama_exe):
        log.warning("Ollama: no existe ollama.exe en %s", ollama_exe)
        return False

    models_dir, mode = _choose_models_dir(app_root)

    env = dict(os.environ)
    env["OLLAMA_HOST"] = OLLAMA_HOST_URL
    env["OLLAMA_MODELS"] = models_dir

    creationflags, si = _hidden_flags()

    try:
        subprocess.Popen(
            [ollama_exe, "serve"],
            cwd=app_root,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            startupinfo=si,
            creationflags=creationflags,
        )
    except Exception as e:
        log.warning("Ollama: no pude iniciar serve: %s", e)
        return False

    ok = _wait_server(timeout_s=10.0)
    if ok:
        log.info("Ollama server arriba (mode=%s, models=%s)", mode, models_dir)
    return ok


def _ui_emit(ui: Optional[Callable[[str, dict], None]], text: str) -> None:
    if not ui:
        return
    try:
        ui("status", {"text": text})
    except Exception:
        pass


def _run_pull(app_root: str, model: str, ui: Optional[Callable[[str, dict], None]]) -> bool:
    ollama_exe = _vendor_ollama_exe(app_root)
    if not os.path.exists(ollama_exe):
        _ui_emit(ui, f"IA offline: no existe ollama.exe en {ollama_exe}")
        return False

    # Asegura server (sin server, pull falla)
    if not _start_server_best_effort(app_root):
        _ui_emit(ui, "IA offline: no pude levantar el server Ollama.")
        return False

    models_dir, mode = _choose_models_dir(app_root)

    env = dict(os.environ)
    env["OLLAMA_HOST"] = OLLAMA_HOST_URL
    env["OLLAMA_MODELS"] = models_dir

    _ui_emit(ui, f"IA offline: descargando/verificando modelo {model}… (mode={mode})")

    creationflags, si = _hidden_flags()

    try:
        p = subprocess.Popen(
            [ollama_exe, "pull", model],
            cwd=app_root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            startupinfo=si,
            creationflags=creationflags,
        )
    except Exception as e:
        _ui_emit(ui, f"IA offline: no pude ejecutar pull: {e}")
        return False

    try:
        assert p.stdout is not None
        for line in p.stdout:
            t = (line or "").rstrip("\r\n")
            if t:
                _ui_emit(ui, t)
    except Exception:
        pass

    try:
        rc = int(p.wait())
    except Exception:
        rc = 1

    if rc != 0:
        _ui_emit(ui, f"IA offline: pull falló (rc={rc}).")
        return False

    models = _list_models(timeout=2.0)
    if model not in models:
        _ui_emit(ui, "IA offline: pull terminó, pero el modelo no aparece en /api/tags.")
        return False

    _ui_emit(ui, f"IA offline: OK (modelo listo: {model}).")
    return True


def ensure_ollama_on_startup(
    *,
    app_root: str,
    ui: Optional[Callable[[str, dict], None]] = None,
    model: str = DEFAULT_MODEL,
) -> dict:
    """
    Política:
    - Cada arranque: asegurar server arriba (best effort).
    - Pull SOLO en el primer arranque tras update (o primera instalación),
      y SOLO si falta el modelo.
    - Si pull falla, backoff para no hacer lenta la app cada arranque.
    """
    st = _read_state()
    ver = _app_version(app_root)

    ready_ver = str(st.get("model_ready_version") or "")
    need_check = (ready_ver != ver)

    server_ok = _start_server_best_effort(app_root)

    models = _list_models(timeout=1.5) if server_ok else set()
    has_model = (model in models)

    pulled = False
    pull_attempted = False

    if need_check and server_ok:
        if has_model:
            st["model_ready_version"] = ver
            st.pop("last_pull_fail_ts", None)
            _write_state(st)
        else:
            now = int(time.time())
            last_fail = int(st.get("last_pull_fail_ts") or 0)
            if last_fail and (now - last_fail) < PULL_BACKOFF_S:
                _ui_emit(ui, "IA offline: modelo faltante, pero pull en backoff (sin reintento por ahora).")
            else:
                pull_attempted = True
                ok = _run_pull(app_root, model, ui)
                if ok:
                    pulled = True
                    st["model_ready_version"] = ver
                    st.pop("last_pull_fail_ts", None)
                    _write_state(st)
                    has_model = True
                else:
                    st["last_pull_fail_ts"] = int(time.time())
                    _write_state(st)

    return {
        "app_version": ver,
        "server_ok": bool(server_ok),
        "has_model": bool(has_model),
        "pulled": bool(pulled),
        "pull_attempted": bool(pull_attempted),
        "need_check": bool(need_check),
    }
