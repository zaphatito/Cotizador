import os, sys, time, ctypes, pandas as pd
from ctypes import wintypes
from PySide6.QtWidgets import (
    QApplication, QMessageBox, QDialog, QVBoxLayout, QLabel, QProgressBar, QPlainTextEdit, QPushButton
)
from PySide6.QtCore import QTimer, Qt

from .paths import set_win_app_id, load_app_icon, ensure_data_seed_if_empty, DATA_DIR
from .logging_setup import get_logger
from .config import COUNTRY_CODE, APP_CONFIG

from .db_path import resolve_db_path
from .catalog_sync import sync_catalog_from_excel_to_db, load_catalog_from_db
from .catalog_manager import CatalogManager
from .quote_events import QuoteEvents

from sqlModels.db import connect, ensure_schema, tx
from .widgets_parts.quote_history_dialog import QuoteHistoryWindow

log = get_logger(__name__)

_MUTEX_HANDLE = None
_MUTEX_NAME = "Local\\SistemaCotizaciones_SingleInstance"
_SHOW_EVENT_NAME = "Local\\SistemaCotizaciones_ShowMainWindow"
ERROR_ALREADY_EXISTS = 183


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
        # status text
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
            # opcional: no cambiamos barra global por bytes (solo dejamos log)
            rel = str(payload.get("rel") or "")
            read = int(payload.get("read") or 0)
            total = int(payload.get("total") or 0)
            if total > 0 and rel:
                pct = int((read / total) * 100)
                self.lbl.setText(f"Descargando {rel}… {pct}%")

        elif kind == "failed":
            err = str(payload.get("error") or "")
            retry_in = int(payload.get("retry_in") or 0)
            msg = f"Falló la actualización. Se reintentará luego."
            if retry_in > 0:
                msg += f" (en ~{retry_in}s)"
            if err:
                msg += f"\n\nDetalle: {err}"
            self.out.appendPlainText(msg)

        QApplication.processEvents()


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


def run_app():
    set_win_app_id()
    _single_instance_or_raise_existing()

    app = QApplication(sys.argv)

    app_icon = load_app_icon(COUNTRY_CODE)
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)

    # ===== CUADRO DE UPDATE =====
    dlg = UpdateProgressDialog(app_icon=app_icon)
    dlg.show()
    dlg.handle_event("status", {"text": "Buscando actualizaciones…"})

    try:
        from .updater import check_for_updates_and_maybe_install
        res = check_for_updates_and_maybe_install(APP_CONFIG, ui=dlg.handle_event, parent=None, log=log)
    except Exception as e:
        log.exception("Fallo al ejecutar el chequeo de actualización")
        res = {"status": "FAILED_RETRY_LATER", "error": str(e), "retry_in": 0}

    # Si inició update -> cerrar app para que apply_update pueda trabajar
    if res.get("status") == "UPDATE_STARTED":
        dlg.handle_event("status", {"text": "Actualización iniciada. Cerrando para aplicar…"})

        # deja respirar la UI un instante
        QApplication.processEvents()
        time.sleep(0.35)
        os._exit(0)

    # Si falló -> botón "Reintentar luego" y continuar abriendo la app
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

        con.close()

        if df_productos is None:
            df_productos = pd.DataFrame()
        if df_presentaciones is None:
            df_presentaciones = pd.DataFrame()

        if df_productos.empty:
            QMessageBox.information(
                None,
                "Catálogo no cargado",
                "No hay productos cargados todavía.\n\n"
                "Puedes abrir el menú (☰) y usar 'Actualizar productos' para importar el Excel.\n"
                "El historial y configuraciones sí estarán disponibles, pero no se podrán abrir cotizaciones.",
            )

    except Exception as e:
        log.exception("Error inicializando DB/Schema")
        QMessageBox.critical(None, "Error", f"❌ Error inicializando la base de datos:\n{e}")
        sys.exit(1)

    catalog = CatalogManager(df_productos, df_presentaciones)
    events = QuoteEvents()
    win = QuoteHistoryWindow(catalog_manager=catalog, quote_events=events, app_icon=app_icon)
    win.show()
    sys.exit(app.exec())
