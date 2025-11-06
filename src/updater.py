from __future__ import annotations
"""
Actualizador automático para Sistema de Cotizaciones.
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
            "User-Agent": "Cotizador-Updater/1.1",
            "Accept": "application/json,text/plain;q=0.9,*/*;q=0.8",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }
    )
    if log: log.debug(f"Updater: GET {u}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
        if log: log.debug(f"Updater: {len(data)} bytes recibidos del manifiesto")
        return data

def _http_get_json(url: str, timeout: int = 12, log=None) -> Tuple[Dict[str, Any], str]:
    try:
        raw = _http_get_raw(url, timeout=timeout, log=log)
    except Exception as e:
        if log: log.warning(f"Updater: error descargando manifiesto: {e}")
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

def _download(url: str, dest: str, timeout: int = 60, log=None) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "Cotizador-Updater/1.1"})
    if log: log.debug(f"Updater: descargando binario {url}")
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

def _q_msg_question(parent, title: str, text: str) -> bool:
    from PySide6.QtWidgets import QMessageBox
    from PySide6.QtCore import Qt
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Question)
    box.setWindowTitle(title)
    box.setText(text)
    box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    box.setDefaultButton(QMessageBox.Yes)
    box.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    box.setWindowModality(Qt.ApplicationModal)
    return box.exec() == QMessageBox.Yes

def _q_msg_info(parent, title: str, text: str) -> None:
    from PySide6.QtWidgets import QMessageBox
    from PySide6.QtCore import Qt
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Information)
    box.setWindowTitle(title)
    box.setText(text)
    box.setStandardButtons(QMessageBox.Ok)
    box.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    box.setWindowModality(Qt.ApplicationModal)
    box.exec()

def _q_msg_error(parent, title: str, text: str) -> None:
    from PySide6.QtWidgets import QMessageBox
    from PySide6.QtCore import Qt
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Critical)
    box.setWindowTitle(title)
    box.setText(text)
    box.setStandardButtons(QMessageBox.Ok)
    box.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    box.setWindowModality(Qt.ApplicationModal)
    box.exec()

def check_for_updates_and_maybe_install(app_config: Dict[str, Any], parent=None, log=None) -> None:
    try:
        if not app_config.get("update_check_on_startup", True):
            return

        manifest_url = (app_config.get("update_manifest_url") or "").strip()
        if not manifest_url:
            if log: log.info("Updater: sin update_manifest_url configurado; omitiendo chequeo.")
            return
        if log: log.info(f"Updater: manifest_url={manifest_url}")

        try:
            from .version import __version__ as local_version
        except Exception:
            local_version = "0.0.0"

        mode = str(app_config.get("update_mode", "ASK")).strip().upper()
        flags_raw = str(app_config.get("update_flags", "") or "").strip()
        extra_flags = [f for f in flags_raw.split() if f]

        manifest, raw_text = _http_get_json(manifest_url, timeout=12, log=log)
        remote_version = str(manifest.get("version", "")).strip()

        if not remote_version:
            if log:
                log.warning("Updater: manifiesto sin 'version' o vacío.")
                if raw_text:
                    log.debug(f"Updater: manifiesto (raw head)='{raw_text[:240].replace(chr(10),'\\n')}'")
            return

        if not _is_newer(remote_version, local_version):
            if log: log.info("Updater: sin novedades. local=%s, remoto=%s", local_version, remote_version)
            return

        url = str(manifest.get("url", "")).strip()
        if not url:
            if log: log.error("Updater: manifiesto sin 'url'.")
            return

        url = _normalize_github_url(url)

        sha256 = str(manifest.get("sha256", "")).strip().lower()
        mandatory = bool(manifest.get("mandatory", False))
        notes = str(manifest.get("notes", "") or "")

        msg = f"Hay una nueva versión {remote_version} disponible.\nTu versión actual es {local_version}."
        if notes:
            msg += f"\n\nNotas de versión:\n{notes}"
        if mandatory:
            msg += "\n\nEsta actualización es obligatoria."

        if mode == "OFF":
            if log: log.info("Updater: modo OFF; no se actualiza.")
            return
        elif mode == "ASK" and not mandatory:
            if not _q_msg_question(parent, "Actualización disponible", msg + "\n\n¿Descargar e instalar ahora?"):
                if log: log.info("Updater: usuario pospuso la actualización.")
                return
        else:
            if mode == "SILENT" and log:
                log.info("Updater: modo SILENT; se instalará sin preguntar.")

        fd, tmp = tempfile.mkstemp(suffix=".exe", prefix="CotizadorSetup_")
        os.close(fd)
        try:
            _download(url, tmp, timeout=90, log=log)
            if _is_git_lfs_pointer_file(tmp):
                url2 = _normalize_github_url(url)
                if url2 != url:
                    if log: log.info("Updater: EXE fue pointer LFS; reintentando con media.githubusercontent.com")
                    _download(url2, tmp, timeout=90, log=log)
                    url = url2
                else:
                    raise RuntimeError("Descargado pointer LFS. Ajusta el 'url' del manifiesto a media.githubusercontent.com.")

            if sha256:
                calc = _sha256_file(tmp)
                if calc.lower() != sha256.lower():
                    _q_msg_error(parent, "Actualización fallida",
                                 "El instalador descargado no superó la verificación de integridad (SHA-256).")
                    if log: log.error("Updater: sha256 mismatch. esperado=%s, obtenido=%s", sha256, calc)
                    try: os.remove(tmp)
                    except Exception: pass
                    return

        except urllib.error.URLError as e:
            if log: log.exception("Updater: error de red al descargar instalador")
            _q_msg_error(parent, "Error de actualización", f"No se pudo descargar el instalador.\n{e}")
            try: os.remove(tmp)
            except Exception: pass
            return
        except Exception as e:
            if log: log.exception("Updater: error al descargar/guardar instalador")
            _q_msg_error(parent, "Error de actualización", f"No se pudo descargar el instalador.\n{e}")
            try: os.remove(tmp)
            except Exception: pass
            return

        args = [tmp] + (extra_flags or [])
        try:
            subprocess.Popen(args, close_fds=True)
        except Exception as e:
            if log: log.exception("Updater: no se pudo lanzar el instalador")
            _q_msg_error(parent, "Error", f"No se pudo ejecutar el instalador:\n{e}")
            return

        _q_msg_info(parent, "Instalador iniciado",
                    "Se abrirá el instalador de la nueva versión.\nLa aplicación actual se cerrará.")
        try:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
            if app: app.quit()
        finally:
            os._exit(0)

    except Exception:
        if log:
            log.exception("Updater: error inesperado")
        return
