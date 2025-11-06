# src/updater.py
from __future__ import annotations
import os, sys, json, tempfile, hashlib, subprocess, re, urllib.request, urllib.error
from typing import Optional, Dict, Any

def _parse_version(v: str) -> tuple[int, int, int]:
    # Acepta "1.2.3", "1.2", "1"
    parts = re.findall(r"\d+", str(v or ""))
    nums = [int(x) for x in parts[:3]]
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)

def _is_newer(remote: str, local: str) -> bool:
    return _parse_version(remote) > _parse_version(local)

def _http_get_json(url: str, timeout: int = 8) -> Dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "Cotizador-Updater/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}

def _download(url: str, dest: str, timeout: int = 30) -> None:
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

def _q_msg_question(parent, title: str, text: str) -> bool:
    # Parent puede ser None (se muestra igual). Importación perezosa para no forzar PySide6 al importar módulo.
    from PySide6.QtWidgets import QMessageBox
    btn = QMessageBox.question(parent, title, text, QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
    return btn == QMessageBox.Yes

def _q_msg_info(parent, title: str, text: str) -> None:
    from PySide6.QtWidgets import QMessageBox
    QMessageBox.information(parent, title, text)

def _q_msg_error(parent, title: str, text: str) -> None:
    from PySide6.QtWidgets import QMessageBox
    QMessageBox.critical(parent, title, text)

def check_for_updates_and_maybe_install(
    app_config: Dict[str, Any],
    parent=None,
    log=None
) -> None:
    """
    Lógica principal de actualización. Llamar tras crear QApplication.
    - app_config debe contener las llaves:
        update_check_on_startup: bool
        update_mode: "ASK" | "SILENT" | "OFF"
        update_manifest_url: str
        update_flags: str (opcional)  e.g. "/CLOSEAPPLICATIONS"
    """
    try:
        if not app_config.get("update_check_on_startup", True):
            return
        manifest_url = (app_config.get("update_manifest_url") or "").strip()
        if not manifest_url:
            return

        # Versión local
        try:
            from .version import __version__ as local_version
        except Exception:
            local_version = "0.0.0"

        mode = str(app_config.get("update_mode", "ASK")).strip().upper()
        flags_raw = str(app_config.get("update_flags", "") or "").strip()
        extra_flags = [f for f in flags_raw.split() if f]

        # Descarga manifiesto
        manifest = _http_get_json(manifest_url)
        remote_version = str(manifest.get("version", "")).strip()
        if not remote_version or not _is_newer(remote_version, local_version):
            if log: log.info("Updater: sin novedades. local=%s, remoto=%s", local_version, remote_version or "(vacío)")
            return

        url = str(manifest.get("url", "")).strip()
        sha256 = str(manifest.get("sha256", "")).strip().lower()
        mandatory = bool(manifest.get("mandatory", False))
        notes = str(manifest.get("notes", "") or "")

        if not url:
            if log: log.error("Updater: manifiesto sin URL de instalador")
            return

        # Mensaje
        msg = f"Hay una nueva versión {remote_version} disponible.\nTu versión actual es {local_version}."
        if notes:
            msg += f"\n\nNotas de versión:\n{notes}"
        if mandatory:
            msg += "\n\nEsta actualización es obligatoria."

        # Decidir si preguntar
        if mode == "OFF":
            if log: log.info("Updater: modo OFF, no se actualiza.")
            return
        elif mode == "ASK" and not mandatory:
            if not _q_msg_question(parent, "Actualización disponible", msg + "\n\n¿Descargar e instalar ahora?"):
                if log: log.info("Updater: usuario pospuso la actualización.")
                return
        else:
            # SILENT o mandatory => seguimos
            if mode == "SILENT" and log:
                log.info("Updater: modo SILENT; se instalará sin preguntar.")

        # Descargar a temp
        fd, tmp = tempfile.mkstemp(suffix=".exe", prefix="CotizadorSetup_")
        os.close(fd)
        try:
            _download(url, tmp)
            if sha256:
                calc = _sha256_file(tmp)
                if calc.lower() != sha256.lower():
                    _q_msg_error(parent, "Actualización fallida", "El instalador descargado no superó la verificación de integridad (SHA-256).")
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

        # Ejecutar instalador (con UI por defecto). Inno Setup se encarga del upgrade por AppId.
        # Sugerimos incluir /CLOSEAPPLICATIONS en flags para que cierre el EXE si sigue abierto.
        args = [tmp] + (extra_flags or [])
        try:
            subprocess.Popen(args, close_fds=True)
        except Exception as e:
            if log: log.exception("Updater: no se pudo lanzar el instalador")
            _q_msg_error(parent, "Error", f"No se pudo ejecutar el instalador:\n{e}")
            return

        # Avisar y salir para que el instalador pueda reemplazar archivos
        _q_msg_info(parent, "Instalador iniciado", "Se abrirá el instalador de la nueva versión.\nLa aplicación actual se cerrará.")
        # Importante: salir lo antes posible
        try:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
            if app:
                app.quit()
        finally:
            # Salida forzada
            os._exit(0)

    except Exception as e:
        if log: 
            log.exception("Updater: error inesperado")
        # No impedimos que la app inicie si hay errores de update.
        return
