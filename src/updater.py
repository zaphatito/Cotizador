from __future__ import annotations
"""
Actualizador automático para Sistema de Cotizaciones.

- type="files": descarga solo archivos cambiados y aplica con updater/apply_update.exe
- fallback legacy: url + sha256 (Setup_*.exe)

Mejoras:
- Cuando falla FILES, muestra el error en UI y hace fallback.
- El instalador YA NO se ejecuta desde la app: se ejecuta desde apply_update.exe (después de cerrar).
- Evita cache viejo agregando ?ts= a TODAS las descargas (incluye media.githubusercontent.com)
- Ignora sqlModels/app.sqlite3 y updater/apply_update.exe
"""

import os, sys, json, re, hashlib, tempfile, subprocess, time, urllib.request, shutil, tempfile
from typing import Dict, Any, Tuple, Optional, Callable

UiCb = Optional[Callable[[str, Dict[str, Any]], None]]


def _emit(ui: UiCb, kind: str, **payload) -> None:
    if not ui:
        return
    try:
        ui(kind, payload)
    except Exception:
        pass


def _parse_version(v: str) -> tuple[int, int, int]:
    parts = re.findall(r"\d+", str(v or ""))
    nums = [int(x) for x in parts[:3]]
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)

def _is_newer(remote: str, local: str) -> bool:
    return _parse_version(remote) > _parse_version(local)

def _normalize_github_url(url: str) -> str:
    u = str(url or "").strip()
    m = re.match(r"^https?://raw\.githubusercontent\.com/([^/]+)/([^/]+)/([^/]+)/(.*)$", u, re.I)
    if m:
        owner, repo, branch, path = m.groups()
        return f"https://media.githubusercontent.com/media/{owner}/{repo}/{branch}/{path}"
    m = re.match(r"^https?://github\.com/([^/]+)/([^/]+)/raw/([^/]+)/(.*)$", u, re.I)
    if m:
        owner, repo, branch, path = m.groups()
        return f"https://media.githubusercontent.com/media/{owner}/{repo}/{branch}/{path}"
    return u

def _is_git_lfs_pointer_file(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            head = f.read(256).decode("utf-8", errors="ignore")
        return head.startswith("version https://git-lfs.github.com/spec/v1")
    except Exception:
        return False

def _cachebust(url: str) -> str:
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}ts={int(time.time())}"

def _http_get_raw(url: str, timeout: int = 12, log=None) -> bytes:
    u = _cachebust(url) if "raw.githubusercontent.com" in url else url
    req = urllib.request.Request(
        u,
        headers={
            "User-Agent": "Cotizador-Updater/1.6",
            "Accept": "application/json,text/plain;q=0.9,*/*;q=0.8",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
    )
    if log:
        log.debug(f"Updater: GET {u}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def _http_get_json(url: str, timeout: int = 12, log=None) -> Tuple[Dict[str, Any], str]:
    try:
        raw = _http_get_raw(url, timeout=timeout, log=log)
    except Exception as e:
        if log:
            log.warning(f"Updater: error descargando manifiesto: {e}")
        return {}, ""
    text = ""
    try:
        text = raw.decode("utf-8-sig")
        data = json.loads(text)
        return (data if isinstance(data, dict) else {}), text
    except Exception as e:
        if log:
            head = (text or "").replace("\r", "").replace("\n", "\\n")
            log.warning(f"Updater: JSON inválido. Error={e}. Inicio respuesta='{head[:240]}'")
        return {}, text

def _download(url: str, dest: str, timeout: int = 180, log=None, ui: UiCb = None, rel: str = "") -> None:
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    u = _cachebust(_normalize_github_url(url))  # ✅ cachebust también para media
    req = urllib.request.Request(u, headers={"User-Agent": "Cotizador-Updater/1.6", "Cache-Control": "no-cache"})
    if log:
        log.debug(f"Updater: GET {u} -> {dest}")

    with urllib.request.urlopen(req, timeout=timeout) as r, open(dest, "wb") as f:
        total = 0
        try:
            total = int(r.headers.get("Content-Length") or 0)
        except Exception:
            total = 0

        read = 0
        last_ui = 0.0
        while True:
            chunk = r.read(1024 * 64)
            if not chunk:
                break
            f.write(chunk)
            read += len(chunk)
            now = time.time()
            if ui and (now - last_ui) >= 0.12:
                last_ui = now
                _emit(ui, "download_bytes", rel=rel, read=read, total=total)

def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def _app_root() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

def _is_safe_relpath(rel: str) -> bool:
    if not rel:
        return False
    p = rel.replace("\\", "/").strip()
    if ":" in p:
        return False
    if p.startswith("/") or p.startswith("\\"):
        return False
    if ".." in p.split("/"):
        return False
    return True

def _dst_for_rel(rel: str) -> str:
    return os.path.join(_app_root(), rel.replace("/", os.sep).replace("\\", os.sep))

def _normalize_rel(rel: str) -> str:
    return rel.replace("\\", "/").strip().lower()

def _build_ignore_set(app_config: Dict[str, Any]) -> set[str]:
    ignore: set[str] = set()
    user_list = app_config.get("update_ignore_paths", [])
    if isinstance(user_list, list):
        for x in user_list:
            if isinstance(x, str) and x.strip():
                ignore.add(_normalize_rel(x))
    ignore.add("config/config.json")
    ignore.add("sqlmodels/app.sqlite3")
    ignore.add("updater/apply_update.exe")  # ✅ no auto-reemplazar el aplicador
    return ignore


# ----------------- retry-later -----------------

def _state_path() -> str:
    d = os.path.join(_app_root(), "updater")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "update_state.json")

def _read_state(log=None) -> Dict[str, Any]:
    p = _state_path()
    try:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                x = json.load(f)
            return x if isinstance(x, dict) else {}
    except Exception:
        if log:
            log.debug("Updater: no se pudo leer update_state.json", exc_info=True)
    return {}

def _write_state(state: Dict[str, Any], log=None) -> None:
    p = _state_path()
    try:
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
    except Exception:
        if log:
            log.debug("Updater: no se pudo escribir update_state.json", exc_info=True)

def _retry_params(app_config: Dict[str, Any]) -> tuple[int, int]:
    base = int(app_config.get("update_retry_base_seconds", 300) or 300)
    maxs = int(app_config.get("update_retry_max_seconds", 6 * 3600) or (6 * 3600))
    base = max(30, base)
    maxs = max(base, maxs)
    return base, maxs

def _should_backoff(state: Dict[str, Any]) -> bool:
    try:
        next_ts = float(state.get("next_retry_ts") or 0)
        return time.time() < next_ts
    except Exception:
        return False

def _mark_failure(app_config: Dict[str, Any], state: Dict[str, Any], remote_version: str, err: Exception, log=None) -> int:
    base, maxs = _retry_params(app_config)
    pending = str(state.get("pending_version") or "")
    fail_count = int(state.get("fail_count") or 0)
    if pending != remote_version:
        fail_count = 0

    fail_count += 1
    delay = min(maxs, base * (2 ** (fail_count - 1)))

    state["pending_version"] = remote_version
    state["fail_count"] = fail_count
    state["last_error"] = str(err)[:240]
    state["last_fail_ts"] = int(time.time())
    state["next_retry_ts"] = int(time.time() + delay)
    _write_state(state, log=log)

    if log:
        log.warning("Updater: falló update v%s; retry en %ss; err=%s", remote_version, delay, state["last_error"])
    return delay

def _clear_failure(state: Dict[str, Any], log=None) -> None:
    changed = False
    for k in ("pending_version", "fail_count", "last_error", "last_fail_ts", "next_retry_ts"):
        if k in state:
            state.pop(k, None)
            changed = True
    if changed:
        _write_state(state, log=log)


# ----------------- runner (apply_update.exe) -----------------

def _apply_exe(app_config: Dict[str, Any]) -> str:
    rel_apply = str(app_config.get("update_apply_exe", "") or "").strip() or r"updater\apply_update.exe"
    return os.path.join(_app_root(), rel_apply.replace("/", os.sep).replace("\\", os.sep))

def _spawn_apply(plan: Dict[str, Any], app_config: Dict[str, Any], ui=None, log=None) -> None:
    apply_src = _apply_exe(app_config)  # tu ruta actual en {app}\updater\apply_update.exe
    if not os.path.exists(apply_src):
        raise RuntimeError(f"No existe apply_update.exe: {apply_src}")

    # ✅ copiar a TEMP para que el instalador pueda reemplazar {app}\updater\apply_update.exe sin locks
    run_dir = os.path.join(tempfile.gettempdir(), "CotizadorUpdate")
    os.makedirs(run_dir, exist_ok=True)
    apply_run = os.path.join(run_dir, "apply_update_run.exe")
    shutil.copy2(apply_src, apply_run)

    plan_path = os.path.join(run_dir, f"plan_{plan.get('version','0.0.0')}.json")
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)

    log_path = os.path.join(_app_root(), "updater", "apply_update.log")

    pid = os.getpid()
    restart_exe = sys.executable

    subprocess.Popen(
        [apply_run, "--plan", plan_path, "--pid", str(pid), "--restart", restart_exe, "--log", log_path],
        close_fds=True
    )

# ----------------- FILES -----------------

def _plan_files_update(manifest: Dict[str, Any], app_config: Dict[str, Any], log=None, ui: UiCb = None) -> Dict[str, Any]:
    base_url = str(manifest.get("base_url", "") or "").strip()
    if not base_url:
        raise RuntimeError("Manifiesto FILES sin base_url")
    if not base_url.endswith("/"):
        base_url += "/"

    files = manifest.get("files", [])
    if not isinstance(files, list):
        raise RuntimeError("Manifiesto FILES inválido: files no es lista")

    ignore = _build_ignore_set(app_config)

    version = str(manifest.get("version") or "0.0.0").strip()
    staging_root = os.path.join(tempfile.gettempdir(), "CotizadorUpdate", version)
    os.makedirs(staging_root, exist_ok=True)

    plan: Dict[str, Any] = {
        "version": version,
        "staging_root": staging_root,
        "files": [],
        "delete": [],
    }

    deletes = manifest.get("delete", [])
    if isinstance(deletes, list):
        for rel in deletes:
            if not isinstance(rel, str):
                continue
            if not _is_safe_relpath(rel):
                continue
            rel_n = _normalize_rel(rel)
            if rel_n in ignore:
                continue
            plan["delete"].append(_dst_for_rel(rel))

    need_items: list[dict[str, str]] = []
    for f in files:
        if not isinstance(f, dict):
            continue
        rel = str(f.get("path", "") or "").replace("\\", "/").strip()
        sha = str(f.get("sha256", "") or "").strip().lower()
        if not rel or not sha:
            continue
        if not _is_safe_relpath(rel):
            continue
        if _normalize_rel(rel) in ignore:
            continue

        dst = _dst_for_rel(rel)
        need = True
        if os.path.exists(dst):
            try:
                need = (_sha256_file(dst).lower() != sha)
            except Exception:
                need = True
        if need:
            need_items.append({"rel": rel, "sha": sha})

    _emit(ui, "progress_total", total=len(need_items))
    _emit(ui, "status", text=f"Archivos a actualizar: {len(need_items)}")

    for idx, it in enumerate(need_items, start=1):
        rel = it["rel"]
        sha = it["sha"]

        _emit(ui, "progress", current=idx - 1, total=len(need_items), text=f"Descargando {idx}/{len(need_items)}: {rel}")

        url = _normalize_github_url(base_url + rel)
        staged = os.path.join(staging_root, rel.replace("/", os.sep))

        _download(url, staged, timeout=180, log=log, ui=ui, rel=rel)

        if _is_git_lfs_pointer_file(staged):
            raise RuntimeError(f"Pointer LFS en {rel}")

        _emit(ui, "status", text=f"Verificando: {rel}")
        calc = _sha256_file(staged).lower()
        if calc != sha:
            raise RuntimeError(f"SHA mismatch en {rel} (esperado {sha}, obt {calc})")

        plan["files"].append({"src": staged, "dst": _dst_for_rel(rel)})

        _emit(ui, "progress", current=idx, total=len(need_items), text=f"Listo: {rel}")

    return plan


# ----------------- INSTALLER (descarga + plan para apply_update) -----------------

def _plan_installer(manifest: Dict[str, Any], app_config: Dict[str, Any], log=None, ui: UiCb = None) -> Dict[str, Any]:
    url = str(manifest.get("url", "")).strip()
    if not url:
        raise RuntimeError("Manifiesto sin 'url' (installer)")
    url = _normalize_github_url(url)

    sha256 = str(manifest.get("sha256", "")).strip().lower()

    flags_raw = str(app_config.get("update_flags", "") or "").strip()
    extra_flags = [f for f in flags_raw.split() if f]

    upper = " ".join(extra_flags).upper()
    if ("/VERYSILENT" not in upper) and ("/SILENT" not in upper):
        extra_flags = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"] + extra_flags

    version = str(manifest.get("version") or "0.0.0").strip()

    fd, tmp = tempfile.mkstemp(suffix=".exe", prefix="CotizadorSetup_")
    os.close(fd)

    _emit(ui, "status", text="Descargando instalador…")
    _download(url, tmp, timeout=240, log=log, ui=ui, rel="Setup")

    if _is_git_lfs_pointer_file(tmp):
        raise RuntimeError("Setup descargado como pointer LFS")

    if sha256:
        _emit(ui, "status", text="Verificando instalador…")
        calc = _sha256_file(tmp).lower()
        if calc != sha256:
            raise RuntimeError("Setup no superó verificación SHA-256")

    plan: Dict[str, Any] = {
        "version": version,
        "staging_root": "",
        "files": [],
        "delete": [],
        "installer": {
            "path": tmp,
            "args": extra_flags,
            "wait": True,
            "delete_after": True,
        },
    }
    return plan


# ----------------- entrypoint -----------------

def check_for_updates_and_maybe_install(app_config: Dict[str, Any], ui: UiCb = None, parent=None, log=None) -> Dict[str, Any]:
    try:
        if not app_config.get("update_check_on_startup", True):
            return {"status": "DISABLED"}

        manifest_url = (app_config.get("update_manifest_url") or "").strip()
        if not manifest_url:
            return {"status": "NO_MANIFEST_URL"}

        mode = str(app_config.get("update_mode", "SILENT")).strip().upper()
        if mode == "OFF":
            return {"status": "OFF"}

        state = _read_state(log=log)
        if _should_backoff(state):
            nxt = int(state.get("next_retry_ts") or 0)
            retry_in = max(1, nxt - int(time.time()))
            _emit(ui, "status", text=f"Actualización en espera. Reintento en ~{retry_in}s.")
            return {"status": "BACKOFF", "retry_in": retry_in}
        
        
        def _get_local_version(log=None) -> str:
            try:
                p = os.path.join(_app_root(), "version.txt")
                if os.path.exists(p):
                    v = (open(p, "r", encoding="utf-8").read() or "").strip()
                    if v:
                        return v
            except Exception:
                if log:
                    log.debug("No se pudo leer version.txt", exc_info=True)

            try:
                from .version import __version__ as v2
                return str(v2)
            except Exception:
                return "0.0.0"


        
        try:
            local_version = _get_local_version(log=log)
        except Exception:
            local_version = "0.0.0"

        _emit(ui, "status", text="Buscando actualizaciones…")
        manifest, _raw = _http_get_json(manifest_url, timeout=12, log=log)
        remote_version = str(manifest.get("version", "")).strip()
        if not remote_version:
            return {"status": "NO_REMOTE_VERSION"}

        if not _is_newer(remote_version, local_version):
            _clear_failure(state, log=log)
            _emit(ui, "status", text="Sin actualizaciones.")
            return {"status": "NO_UPDATE", "local": local_version, "remote": remote_version}

        _emit(ui, "status", text=f"Actualización encontrada: {local_version} → {remote_version}")
        pkg_type = str(manifest.get("type", "") or "").strip().lower() or "installer"

        try:
            if pkg_type == "files":
                _emit(ui, "status", text="Preparando actualización (files)…")
                plan = _plan_files_update(manifest, app_config, log=log, ui=ui)
                if not plan.get("files") and not plan.get("delete"):
                    _clear_failure(state, log=log)
                    _emit(ui, "status", text="No hay cambios que aplicar.")
                    return {"status": "NO_CHANGES"}

                _spawn_apply(plan, app_config, ui=ui, log=log)
                return {"status": "UPDATE_STARTED", "method": "files", "remote": remote_version}

            # installer directo
            _emit(ui, "status", text="Preparando actualización (installer)…")
            plan = _plan_installer(manifest, app_config, log=log, ui=ui)
            _spawn_apply(plan, app_config, ui=ui, log=log)
            return {"status": "UPDATE_STARTED", "method": "installer", "remote": remote_version}

        except Exception as e:
            # mostrar el error real antes del fallback
            _emit(ui, "status", text=f"Error en actualización: {e}")
            if log:
                log.exception("Updater: fallo; intentando fallback instalador")

            try:
                # fallback si hay url
                if str(manifest.get("url", "") or "").strip():
                    _emit(ui, "status", text="Fallback: usando instalador…")
                    plan = _plan_installer(manifest, app_config, log=log, ui=ui)
                    _spawn_apply(plan, app_config, ui=ui, log=log)
                    return {"status": "UPDATE_STARTED", "method": "installer_fallback", "remote": remote_version}
            except Exception as e2:
                if log:
                    log.exception("Updater: fallback instalador también falló: %s", e2)

            delay = _mark_failure(app_config, state, remote_version, e, log=log)
            _emit(ui, "failed", error=str(e), retry_in=delay)
            return {"status": "FAILED_RETRY_LATER", "error": str(e), "retry_in": delay}

    except Exception as e:
        if log:
            log.exception("Updater: error inesperado: %s", e)
        _emit(ui, "failed", error=str(e), retry_in=0)
        return {"status": "FAILED_RETRY_LATER", "error": str(e), "retry_in": 0}
