# src/app.py
from __future__ import annotations

import os
import sys
import time
import json
import ctypes
import pandas as pd
from ctypes import wintypes

from PySide6.QtWidgets import (
    QApplication, QMessageBox, QDialog, QVBoxLayout, QLabel, QProgressBar, QPlainTextEdit, QPushButton
)
from PySide6.QtCore import Qt

from .paths import set_win_app_id, load_app_icon, ensure_data_seed_if_empty, DATA_DIR
from .logging_setup import get_logger
from .config import COUNTRY_CODE, APP_CONFIG, ENABLE_AI

from .db_path import resolve_db_path
from .catalog_sync import (
    sync_catalog_from_excel_to_db,
    load_catalog_from_db,
    validate_products_catalog_df,
    products_update_required_message,
)
from .catalog_manager import CatalogManager
from .quote_events import QuoteEvents

from sqlModels.db import connect, ensure_schema, tx
from .widgets_parts.quote_history_dialog import QuoteHistoryWindow

from .ai.search_index import LocalSearchIndex
from .ai.assistant.ollama_bootstrap import ensure_ollama_on_startup
from .api.presupuesto_client import verify_cotizador_signature_once
from .ui_theme import apply_modern_theme
log = get_logger(__name__)

_MUTEX_HANDLE = None
_MUTEX_NAME = "Local\\SistemaCotizaciones_SingleInstance"
_SHOW_EVENT_NAME = "Local\\SistemaCotizaciones_ShowMainWindow"
ERROR_ALREADY_EXISTS = 183


def _app_root() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class UpdateProgressDialog(QDialog):
    def __init__(self, app_icon=None):
        super().__init__(None)
        self.setWindowTitle("Actualizando Sistema de Cotizaciones")
        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.setMinimumWidth(520)

        if app_icon is not None and not app_icon.isNull():
            self.setWindowIcon(app_icon)

        lay = QVBoxLayout(self)

        self.lbl = QLabel("Iniciando…")
        lay.addWidget(self.lbl)

        self.bar = QProgressBar()
        self.bar.setRange(0, 0)  # indeterminado hasta saber total
        lay.addWidget(self.bar)

        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        self.out.setMaximumBlockCount(500)
        lay.addWidget(self.out)

    def handle_event(self, kind: str, payload: dict):
        if kind == "status":
            t = str(payload.get("text", "") or "")
            if t:
                self.lbl.setText(t)
                self.out.appendPlainText(t)

        elif kind == "progress_total":
            total = int(payload.get("total") or 0)
            if total > 0:
                self.bar.setRange(0, total)
                self.bar.setValue(0)
                self.lbl.setText("Preparando descarga…")
                self.out.appendPlainText(f"Total archivos: {total}")
            else:
                self.bar.setRange(0, 0)

        elif kind == "progress":
            cur = int(payload.get("current") or 0)
            total = int(payload.get("total") or 0)
            text = str(payload.get("text") or "")
            if total > 0:
                self.bar.setRange(0, total)
                self.bar.setValue(cur)
            if text:
                self.lbl.setText(text)
                self.out.appendPlainText(text)

        elif kind == "download_bytes":
            rel = str(payload.get("rel") or "")
            read = int(payload.get("read") or 0)
            total = int(payload.get("total") or 0)
            if total > 0 and rel:
                pct = int((read / total) * 100)
                self.lbl.setText(f"Descargando {rel}… {pct}%")

        elif kind == "failed":
            err = str(payload.get("error") or "")
            retry_in = int(payload.get("retry_in") or 0)
            msg = "Falló la actualización. Se reintentará luego."
            if retry_in > 0:
                msg += f" (en ~{retry_in}s)"
            if err:
                msg += f"\n\nDetalle: {err}"
            self.out.appendPlainText(msg)

        QApplication.processEvents()


class ChangelogDialog(QDialog):
    def __init__(self, version: str, text: str, app_icon=None):
        super().__init__(None)
        self.setWindowTitle("Novedades de la actualización")
        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.setMinimumWidth(560)

        if app_icon is not None and not app_icon.isNull():
            self.setWindowIcon(app_icon)

        lay = QVBoxLayout(self)

        title = "Se instalaron cambios nuevos."
        if version:
            title = f"Actualización instalada: {version}"
        lay.addWidget(QLabel(title))

        box = QPlainTextEdit()
        box.setReadOnly(True)
        box.setMaximumBlockCount(2000)
        box.setPlainText(text or "")
        lay.addWidget(box)

        btn = QPushButton("Entendido")
        btn.setProperty("variant", "primary")
        btn.clicked.connect(self.accept)
        lay.addWidget(btn, alignment=Qt.AlignRight)


def _pending_changelog_path(app_root: str) -> str:
    return os.path.join(app_root, "updater", "pending_changelog.json")


def _show_changelog_if_pending(app_root: str, app_icon=None) -> None:
    p = _pending_changelog_path(app_root)
    if not os.path.exists(p):
        return

    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}

    version = str(data.get("version") or "").strip()
    rel = str(data.get("changelog_rel") or "changelog.txt").strip() or "changelog.txt"

    changelog_path = rel
    if not os.path.isabs(changelog_path):
        changelog_path = os.path.join(app_root, rel)

    text = ""
    try:
        if os.path.exists(changelog_path):
            text = (open(changelog_path, "r", encoding="utf-8-sig", errors="ignore").read() or "").strip()
        else:
            text = "(No se encontró el archivo de changelog.)"
    except Exception as e:
        text = f"(No se pudo leer el changelog: {e})"

    dlg = ChangelogDialog(version=version, text=text, app_icon=app_icon)
    dlg.exec()

    try:
        os.remove(p)
    except Exception:
        pass


def _request_show_existing_and_exit() -> None:
    try:
        OpenEventW = ctypes.windll.kernel32.OpenEventW
        OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        OpenEventW.restype = wintypes.HANDLE

        SetEvent = ctypes.windll.kernel32.SetEvent
        SetEvent.argtypes = [wintypes.HANDLE]
        SetEvent.restype = wintypes.BOOL

        CloseHandle = ctypes.windll.kernel32.CloseHandle
        CloseHandle.argtypes = [wintypes.HANDLE]
        CloseHandle.restype = wintypes.BOOL

        EVENT_MODIFY_STATE = 0x0002
        h_evt = OpenEventW(EVENT_MODIFY_STATE, False, _SHOW_EVENT_NAME)
        if h_evt:
            SetEvent(h_evt)
            CloseHandle(h_evt)
    except Exception:
        pass
    sys.exit(0)


def _single_instance_or_raise_existing() -> None:
    global _MUTEX_HANDLE
    try:
        CreateMutexW = ctypes.windll.kernel32.CreateMutexW
        CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        CreateMutexW.restype = wintypes.HANDLE

        GetLastError = ctypes.windll.kernel32.GetLastError

        h = CreateMutexW(None, True, _MUTEX_NAME)
        if not h:
            return

        if GetLastError() == ERROR_ALREADY_EXISTS:
            _request_show_existing_and_exit()

        _MUTEX_HANDLE = h
    except Exception:
        return


def _verify_access_before_startup(*, app_icon=None) -> bool:
    try:
        log.info("Verificando acceso del cotizador antes del chequeo de actualizacion.")
        result = verify_cotizador_signature_once()
    except Exception as exc:
        # Fallback defensivo: la verificacion normal ya maneja soft/hard fail.
        log.exception("Fallo inesperado al verificar acceso del cotizador.")
        result = {
            "status": "SOFT_FAIL",
            "allowed": True,
            "blocked": False,
            "message": str(exc),
        }

    status = str(result.get("status") or "").strip().upper()
    message = str(result.get("message") or "").strip()

    if status == "SOFT_FAIL":
        log.warning("No se pudo verificar acceso al iniciar: %s", message or "sin detalle")
        return True

    if bool(result.get("blocked")):
        mb = QMessageBox()
        mb.setIcon(QMessageBox.Critical)
        mb.setWindowTitle("Programa bloqueado")
        if app_icon is not None and not app_icon.isNull():
            mb.setWindowIcon(app_icon)
        mb.setText(message or "Este programa fue bloqueado por un administrador.")
        mb.exec()
        return False

    log.info("Verificacion de acceso completada al iniciar | status=%s", status or "ACTIVE")
    return True


def run_app():
    set_win_app_id()
    _single_instance_or_raise_existing()

    app = QApplication(sys.argv)
    apply_modern_theme(app)

    app_icon = load_app_icon(COUNTRY_CODE)
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)

    if not _verify_access_before_startup(app_icon=app_icon):
        return

    # ===== CUADRO DE UPDATE =====
    dlg = UpdateProgressDialog(app_icon=app_icon)
    dlg.show()

    try:
        from .updater import check_for_updates_and_maybe_install
        res = check_for_updates_and_maybe_install(APP_CONFIG, ui=dlg.handle_event, parent=None, log=log)
    except Exception as e:
        log.exception("Fallo al ejecutar el chequeo de actualización")
        res = {"status": "FAILED_RETRY_LATER", "error": str(e), "retry_in": 0}

    # Si inició update -> cerrar app para que apply_update pueda trabajar
    if res.get("status") == "UPDATE_STARTED":
        dlg.handle_event("status", {"text": "Actualización iniciada. Cerrando para aplicar…"})
        QApplication.processEvents()
        time.sleep(0.35)
        os._exit(0)

    # ===== ✅ OLLAMA: iniciar server siempre + pull solo 1 vez post-update =====
    # - Mantén el diálogo abierto en el camino "normal" para mostrar logs si toca descargar.
    # - En FAILED, igual intentamos levantar server pero sin bloquear UI con el dialog.
    try:
        if ENABLE_AI:
            app_root = _app_root()

            if res.get("status") != "FAILED_RETRY_LATER":
                dlg.handle_event("status", {"text": "Inicializando IA offline (Ollama)…"})
                ensure_ollama_on_startup(app_root=app_root, ui=dlg.handle_event, model="qwen2.5:14b-instruct")
            else:
                ensure_ollama_on_startup(app_root=app_root, ui=None, model="qwen2.5:14b-instruct")
        else:
            log.info("IA desactivada por configuracion, no se inicia Ollama al arrancar.")
    except Exception:
        log.exception("Ollama: no se pudo iniciar/asegurar (se usará fallback).")

    # Si falló -> continuar abriendo la app
    if res.get("status") == "FAILED_RETRY_LATER":
        dlg.close()
        mb = QMessageBox()
        mb.setIcon(QMessageBox.Warning)
        mb.setWindowTitle("Actualización")
        retry_in = int(res.get("retry_in") or 0)
        err = str(res.get("error") or "")
        txt = "No se pudo completar la actualización.\nSe reintentará luego."
        if retry_in > 0:
            txt += f"\n\nReintento en ~{retry_in} segundos."
        if err:
            txt += f"\n\nDetalle:\n{err}"
        mb.setText(txt)
        btn = mb.addButton("Reintentar luego", QMessageBox.AcceptRole)
        mb.setDefaultButton(btn)
        mb.exec()
    else:
        dlg.close()

    # ✅ mostrar changelog SOLO en el primer arranque post-update
    try:
        _show_changelog_if_pending(_app_root(), app_icon=app_icon)
    except Exception:
        log.exception("No se pudo mostrar el changelog")

    # ===== normal arranque =====
    ensure_data_seed_if_empty()

    df_productos = pd.DataFrame()
    df_presentaciones = pd.DataFrame()

    try:
        db_path = resolve_db_path()
        con = connect(db_path)
        ensure_schema(con)

        try:
            with tx(con):
                sync_catalog_from_excel_to_db(con, DATA_DIR)
        except Exception as e:
            log.exception("Falló sync_catalog_from_excel_to_db (se abre sin catálogo): %s", e)

        try:
            df_productos, df_presentaciones = load_catalog_from_db(con)
        except Exception as e:
            log.exception("Falló load_catalog_from_db (se abre sin catálogo): %s", e)

        try:
            # Rebuild rápido (2000 productos es nada). Esto habilita autocompletado.
            idx = LocalSearchIndex(db_path)
            idx.ensure_and_rebuild()
        except Exception as e:
            log.exception("AI index: no se pudo reconstruir: %s", e)

        con.close()

        if df_productos is None:
            df_productos = pd.DataFrame()
        if df_presentaciones is None:
            df_presentaciones = pd.DataFrame()

        ok_catalog, reason_catalog = validate_products_catalog_df(df_productos)
        if not ok_catalog:
            log.warning("Catalogo de productos invalido al iniciar: %s", reason_catalog)
            QMessageBox.warning(
                None,
                "Catalogo invalido",
                products_update_required_message(df_productos),
            )
            df_productos = pd.DataFrame()
            df_presentaciones = pd.DataFrame()
    except Exception as e:
        log.exception("Error inicializando DB/Schema")
        QMessageBox.critical(None, "Error", f"❌ Error inicializando la base de datos:\n{e}")
        sys.exit(1)

    catalog = CatalogManager(df_productos, df_presentaciones)
    events = QuoteEvents()
    win = QuoteHistoryWindow(catalog_manager=catalog, quote_events=events, app_icon=app_icon)
    win.show()

    sys.exit(app.exec())
