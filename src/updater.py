from __future__ import annotations
"""
Actualizador automático para Sistema de Cotizaciones.

- type="files": descarga solo archivos cambiados y aplica con updater/apply_update.exe
- fallback legacy: url + sha256 (Setup_*.exe)

Modo "reintentar luego":
- Si hay update pero falla por red/verificación/etc., NO bloquea el arranque.
- Guarda un estado local con backoff y reintenta en el próximo arranque (SILENT).
"""

import os
import sys
import json
import re
import hashlib
import tempfile
import subprocess
import time
import urllib.request
import urllib.error
from typing import Dict, Any, Tuple


# ----------------- base helpers -----------------

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

def _http_get_raw(url: str, timeout: int = 12, log=None) -> bytes:
    sep = "&" if "?" in url else "?"
    bust = f"{sep}_ts={int(time.time())}"
    u = url + bust if "raw.githubusercontent.com" in url else url
    req = urllib.request.Request(
        u,
        headers={
            "User-Agent": "Cotizador-Updater/1.4",
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

def _download(url: str, dest: str, timeout: int = 120, log=None) -> None:
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "Cotizador-Updater/1.4"})
    if log:
        log.debug(f"Updater: GET {url} -> {dest}")
    with urllib.request.urlopen(req, timeout=timeout) as r, open(dest, "wb") as f:
        while True:
            chunk = r.read(1024 * 64)
            if not chunk:
                break
            f.write(chunk)

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


# ----------------- ignore rules -----------------

def _build_ignore_set(app_config: Dict[str, Any]) -> set[str]:
    ignore: set[str] = set()
    user_list = app_config.get("update_ignore_paths", [])
    if isinstance(user_list, list):
        for x in user_list:
            if isinstance(x, str) and x.strip():
                ignore.add(_normalize_rel(x))

    # hard rules: NUNCA tocar estas rutas
    ignore.add("config/config.json")
    ignore.add("sqlmodels/app.sqlite3")
    return ignore


# ----------------- retry-later state -----------------

def _state_path() -> str:
    d = os.path.join(_app_root(), "updater")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
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
    """
    base, max en segundos.
    default: 300s -> 6h
    """
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

def _mark_failure(app_config: Dict[str, Any], state: Dict[str, Any], remote_version: str, err: Exception, log=None) -> None:
    base, maxs = _retry_params(app_config)

    # si cambió la versión, reinicia contador
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
        log.warning(
            "Updater: falló update (v%s). Reintentar en %ss (fail_count=%s). Error=%s",
            remote_version, delay, fail_count, state["last_error"]
        )

def _clear_failure(state: Dict[str, Any], log=None) -> None:
    # limpia solo lo relacionado a retry-later
    changed = False
    for k in ("pending_version", "fail_count", "last_error", "last_fail_ts", "next_retry_ts"):
        if k in state:
            state.pop(k, None)
            changed = True
    if changed:
        _write_state(state, log=log)


# ----------------- FILES mode -----------------

def _plan_files_update(manifest: Dict[str, Any], app_config: Dict[str, Any], log=None) -> Dict[str, Any]:
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

    # delete[]
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

    # files[]
    for f in files:
        if not isinstance(f, dict):
            continue
        rel = str(f.get("path", "") or "").replace("\\", "/").strip()
        sha = str(f.get("sha256", "") or "").strip().lower()
        if not rel or not sha:
            continue
        if not _is_safe_relpath(rel):
            continue

        rel_n = _normalize_rel(rel)
        if rel_n in ignore:
            continue

        dst = _dst_for_rel(rel)

        need = True
        if os.path.exists(dst):
            try:
                need = (_sha256_file(dst).lower() != sha)
            except Exception:
                need = True
        if not need:
            continue

        url = _normalize_github_url(base_url + rel)
        staged = os.path.join(staging_root, rel.replace("/", os.sep))

        _download(url, staged, timeout=180, log=log)
        if _is_git_lfs_pointer_file(staged):
            url2 = _normalize_github_url(url)
            if url2 != url:
                if log:
                    log.info("Updater: archivo fue pointer LFS; reintentando con media.githubusercontent.com")
                _download(url2, staged, timeout=180, log=log)
            else:
                raise RuntimeError(f"Pointer LFS en {rel}")

        calc = _sha256_file(staged).lower()
        if calc != sha:
            raise RuntimeError(f"SHA mismatch en {rel} (esperado {sha}, obt {calc})")

        plan["files"].append({"src": staged, "dst": dst})

    return plan

def _run_files_update(plan: Dict[str, Any], app_config: Dict[str, Any], log=None) -> None:
    rel_apply = str(app_config.get("update_apply_exe", "") or "").strip() or r"updater\apply_update.exe"
    apply_exe = os.path.join(_app_root(), rel_apply.replace("/", os.sep).replace("\\", os.sep))
    if not os.path.exists(apply_exe):
        raise RuntimeError(f"No existe apply_update.exe: {apply_exe}")

    plan_path = os.path.join(tempfile.gettempdir(), "CotizadorUpdate", f"plan_{plan.get('version','0.0.0')}.json")
    os.makedirs(os.path.dirname(plan_path), exist_ok=True)
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)

    pid = os.getpid()
    restart_exe = sys.executable

    subprocess.Popen([apply_exe, "--plan", plan_path, "--pid", str(pid), "--restart", restart_exe], close_fds=True)


# ----------------- installer fallback -----------------

def _run_installer_update(manifest: Dict[str, Any], app_config: Dict[str, Any], log=None) -> None:
    url = str(manifest.get("url", "")).strip()
    if not url:
        raise RuntimeError("Manifiesto sin 'url' (installer fallback)")
    url = _normalize_github_url(url)

    sha256 = str(manifest.get("sha256", "")).strip().lower()

    flags_raw = str(app_config.get("update_flags", "") or "").strip()
    extra_flags = [f for f in flags_raw.split() if f]

    # garantizar silencioso
    upper = " ".join(extra_flags).upper()
    if ("/VERYSILENT" not in upper) and ("/SILENT" not in upper):
        extra_flags = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"] + extra_flags

    fd, tmp = tempfile.mkstemp(suffix=".exe", prefix="CotizadorSetup_")
    os.close(fd)

    try:
        _download(url, tmp, timeout=240, log=log)

        if _is_git_lfs_pointer_file(tmp):
            url2 = _normalize_github_url(url)
            if url2 != url:
                if log:
                    log.info("Updater: Setup fue pointer LFS; reintentando con media.githubusercontent.com")
                _download(url2, tmp, timeout=240, log=log)
            else:
                raise RuntimeError("Setup descargado como pointer LFS")

        if sha256:
            calc = _sha256_file(tmp).lower()
            if calc != sha256:
                raise RuntimeError("Setup no superó verificación SHA-256")
    except Exception:
        try:
            os.remove(tmp)
        except Exception:
            pass
        raise

    subprocess.Popen([tmp] + extra_flags, close_fds=True)


# ----------------- entrypoint -----------------

def check_for_updates_and_maybe_install(app_config: Dict[str, Any], parent=None, log=None) -> None:
    """
    SILENT por defecto:
    - si hay update y se puede aplicar -> lo aplica y reinicia (sale con os._exit(0))
    - si falla -> "reintentar luego": guarda backoff y deja abrir la app
    """
    try:
        if not app_config.get("update_check_on_startup", True):
            return

        manifest_url = (app_config.get("update_manifest_url") or "").strip()
        if not manifest_url:
            return

        mode = str(app_config.get("update_mode", "SILENT")).strip().upper()
        if mode == "OFF":
            return

        # backoff por fallos previos
        state = _read_state(log=log)
        if _should_backoff(state):
            if log:
                nxt = int(state.get("next_retry_ts") or 0)
                log.info("Updater: en backoff, se reintentará luego (next_retry_ts=%s).", nxt)
            return

        try:
            from .version import __version__ as local_version
        except Exception:
            local_version = "0.0.0"

        manifest, _raw = _http_get_json(manifest_url, timeout=12, log=log)
        remote_version = str(manifest.get("version", "")).strip()
        if not remote_version:
            return

        if not _is_newer(remote_version, local_version):
            _clear_failure(state, log=log)
            return

        pkg_type = str(manifest.get("type", "") or "").strip().lower() or "installer"

        try:
            if pkg_type == "files":
                plan = _plan_files_update(manifest, app_config, log=log)
                if not plan.get("files") and not plan.get("delete"):
                    _clear_failure(state, log=log)
                    return
                _run_files_update(plan, app_config, log=log)
                os._exit(0)
            else:
                _run_installer_update(manifest, app_config, log=log)
                os._exit(0)

        except Exception as e:
            # fallback al instalador si está disponible
            try:
                if str(manifest.get("url", "") or "").strip():
                    _run_installer_update(manifest, app_config, log=log)
                    os._exit(0)
            except Exception as e2:
                if log:
                    log.exception("Updater: fallback instalador también falló: %s", e2)

            # reintentar luego (backoff)
            _mark_failure(app_config, state, remote_version, e, log=log)
            return

    except Exception as e:
        if log:
            log.exception("Updater: error inesperado; se continúa con la app: %s", e)
        return
