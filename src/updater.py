# src/updater.py
from __future__ import annotations
"""
Actualizador automático para Sistema de Cotizaciones.

Uso:
- Llama a check_for_updates_and_maybe_install(APP_CONFIG, parent=None, log=log)
  justo después de crear QApplication (en app.py).
- APP_CONFIG debe incluir:
    - update_check_on_startup: bool
    - update_mode: "ASK" | "SILENT" | "OFF"
    - update_manifest_url: str  (URL pública a un JSON con campos: version, url, sha256?, mandatory?, notes?)
    - update_flags: str (opcional) por ejemplo: "/CLOSEAPPLICATIONS" o "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS"

Manifiesto JSON esperado (ejemplo):
{
  "version": "1.2.1",
  "url": "https://.../Setup_SistemaCotizaciones_1.2.1.exe",
  "sha256": "abc123... (opcional pero recomendado)",
  "mandatory": true,
  "notes": "Correcciones y mejoras."
}
"""

import os
import sys
import json
import re
import hashlib
import tempfile
import subprocess
import urllib.request
import urllib.error
from typing import Dict, Any


# =========================
# Utilidades de versión
# =========================
def _parse_version(v: str) -> tuple[int, int, int]:
    """Acepta '1.2.3', '1.2', '1' y devuelve tupla comparable."""
    parts = re.findall(r"\d+", str(v or ""))
    nums = [int(x) for x in parts[:3]]
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)


def _is_newer(remote: str, local: str) -> bool:
    """True si remote > local (semánticamente)."""
    return _parse_version(remote) > _parse_version(local)


# =========================
# HTTP helpers
# =========================
def _http_get_json(url: str, timeout: int = 8) -> Dict[str, Any]:
    """Descarga y parsea JSON. Devuelve {} en error."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Cotizador-Updater/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


def _download(url: str, dest: str, timeout: int = 30) -> None:
    """Descarga binario a 'dest' o lanza excepción en error."""
    req = urllib.request.Request(url, headers={"User-Agent": "Cotizador-Updater/1.0"})
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


# =========================
# UI helpers (siempre al frente)
# =========================
def _q_msg_question(parent, title: str, text: str) -> bool:
    # Importación perezosa para no forzar dependencia si no hay Qt cargado aún
    from PySide6.QtWidgets import QMessageBox
    from PySide6.QtCore import Qt
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Question)
    box.setWindowTitle(title)
    box.setText(text)
    box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    box.setDefaultButton(QMessageBox.Yes)
    # Siempre al frente + modal
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


# =========================
# API principal
# =========================
def check_for_updates_and_maybe_install(
    app_config: Dict[str, Any],
    parent=None,
    log=None
) -> None:
    """
    Chequea si hay versión nueva y, si aplica, descarga y lanza el instalador.
    Debe llamarse tras crear QApplication (para poder mostrar diálogos).
    No bloquea el arranque si hay errores: sólo registra y continúa.
    """
    try:
        # 1) ¿Está habilitado el chequeo?
        if not app_config.get("update_check_on_startup", True):
            return

        manifest_url = (app_config.get("update_manifest_url") or "").strip()
        if not manifest_url:
            if log: log.info("Updater: sin update_manifest_url configurado; omitiendo chequeo.")
            return

        # 2) Versión local (fallback a 0.0.0 si algo falla)
        try:
            from .version import __version__ as local_version
        except Exception:
            local_version = "0.0.0"

        mode = str(app_config.get("update_mode", "ASK")).strip().upper()
        flags_raw = str(app_config.get("update_flags", "") or "").strip()
        extra_flags = [f for f in flags_raw.split() if f]

        # 3) Descarga manifiesto
        manifest = _http_get_json(manifest_url)
        remote_version = str(manifest.get("version", "")).strip()

        if not remote_version:
            if log: log.warning("Updater: manifiesto sin 'version' o vacío.")
            return

        if not _is_newer(remote_version, local_version):
            if log: log.info("Updater: sin novedades. local=%s, remoto=%s", local_version, remote_version)
            return

        url = str(manifest.get("url", "")).strip()
        if not url:
            if log: log.error("Updater: manifiesto sin 'url'.")
            return

        sha256 = str(manifest.get("sha256", "")).strip().lower()
        mandatory = bool(manifest.get("mandatory", False))
        notes = str(manifest.get("notes", "") or "")

        # 4) Mensaje al usuario
        msg = f"Hay una nueva versión {remote_version} disponible.\nTu versión actual es {local_version}."
        if notes:
            msg += f"\n\nNotas de versión:\n{notes}"
        if mandatory:
            msg += "\n\nEsta actualización es obligatoria."

        # 5) Decidir interacción
        if mode == "OFF":
            if log: log.info("Updater: modo OFF; no se actualiza.")
            return
        elif mode == "ASK" and not mandatory:
            if not _q_msg_question(parent, "Actualización disponible", msg + "\n\n¿Descargar e instalar ahora?"):
                if log: log.info("Updater: usuario pospuso la actualización.")
                return
        else:
            # SILENT o mandatory => seguimos sin preguntar
            if mode == "SILENT" and log:
                log.info("Updater: modo SILENT; se instalará sin preguntar.")

        # 6) Descargar instalador a TEMP
        fd, tmp = tempfile.mkstemp(suffix=".exe", prefix="CotizadorSetup_")
        os.close(fd)
        try:
            _download(url, tmp)
            if sha256:
                calc = _sha256_file(tmp)
                if calc.lower() != sha256.lower():
                    _q_msg_error(parent, "Actualización fallida",
                                 "El instalador descargado no superó la verificación de integridad (SHA-256).")
                    if log: log.error("Updater: sha256 mismatch. esperado=%s, obtenido=%s", sha256, calc)
                    try:
                        os.remove(tmp)
                    except Exception:
                        pass
                    return
        except urllib.error.URLError as e:
            if log: log.exception("Updater: error de red al descargar instalador")
            _q_msg_error(parent, "Error de actualización", f"No se pudo descargar el instalador.\n{e}")
            try:
                os.remove(tmp)
            except Exception:
                pass
            return
        except Exception as e:
            if log: log.exception("Updater: error al descargar/guardar instalador")
            _q_msg_error(parent, "Error de actualización", f"No se pudo descargar el instalador.\n{e}")
            try:
                os.remove(tmp)
            except Exception:
                pass
            return

        # 7) Ejecutar instalador (Inno Setup hará upgrade por AppId)
        #    Recomendado incluir /CLOSEAPPLICATIONS en flags para cerrar el EXE actual.
        args = [tmp] + (extra_flags or [])
        try:
            subprocess.Popen(args, close_fds=True)
        except Exception as e:
            if log: log.exception("Updater: no se pudo lanzar el instalador")
            _q_msg_error(parent, "Error", f"No se pudo ejecutar el instalador:\n{e}")
            return

        # 8) Aviso y salida para liberar archivos
        _q_msg_info(parent, "Instalador iniciado",
                    "Se abrirá el instalador de la nueva versión.\nLa aplicación actual se cerrará.")
        try:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
            if app:
                app.quit()
        finally:
            # Salida forzada para garantizar que el instalador pueda reemplazar archivos
            os._exit(0)

    except Exception:
        # No bloqueamos el arranque de la app por fallos del updater.
        if log:
            log.exception("Updater: error inesperado")
        return
