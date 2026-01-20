import sys
import ctypes
from ctypes import wintypes

import pandas as pd
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QTimer

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

# IDs Windows (deben ser constantes y únicas por app)
_MUTEX_NAME = "Local\\SistemaCotizaciones_SingleInstance"
_SHOW_EVENT_NAME = "Local\\SistemaCotizaciones_ShowMainWindow"

ERROR_ALREADY_EXISTS = 183


def _request_show_existing_and_exit() -> None:
    """
    Segunda instancia: señaliza un evento global/local para que la primera se muestre.
    Luego sale silenciosamente.
    """
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
    """
    Primera instancia: crea mutex.
    Segunda instancia: pide a la primera que se muestre y sale SIN mensaje.
    """
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


def _install_show_event_listener(app: QApplication, get_window_callable):
    """
    Primera instancia: crea (o abre) un evento y lo consulta periódicamente.
    Cuando se activa, hace focus/raise de la ventana.
    """
    try:
        CreateEventW = ctypes.windll.kernel32.CreateEventW
        CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
        CreateEventW.restype = wintypes.HANDLE

        WaitForSingleObject = ctypes.windll.kernel32.WaitForSingleObject
        WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        WaitForSingleObject.restype = wintypes.DWORD

        ResetEvent = ctypes.windll.kernel32.ResetEvent
        ResetEvent.argtypes = [wintypes.HANDLE]
        ResetEvent.restype = wintypes.BOOL

        CloseHandle = ctypes.windll.kernel32.CloseHandle
        CloseHandle.argtypes = [wintypes.HANDLE]
        CloseHandle.restype = wintypes.BOOL

        WAIT_OBJECT_0 = 0
        WAIT_TIMEOUT = 258

        # manual_reset=True para que podamos ResetEvent luego de manejarlo
        h_evt = CreateEventW(None, True, False, _SHOW_EVENT_NAME)
        if not h_evt:
            return

        def poll():
            try:
                rc = WaitForSingleObject(h_evt, 0)
                if rc == WAIT_OBJECT_0:
                    ResetEvent(h_evt)

                    win = get_window_callable()
                    if win is None:
                        return

                    # Traer al frente (lo más confiable en Qt/Windows)
                    try:
                        if win.isMinimized():
                            win.showNormal()
                        win.show()
                        win.raise_()
                        win.activateWindow()
                    except Exception:
                        pass
            except Exception:
                pass

        timer = QTimer(app)
        timer.setInterval(300)  # ms
        timer.timeout.connect(poll)
        timer.start()

        # Cierra handle cuando cierra la app
        def cleanup():
            try:
                CloseHandle(h_evt)
            except Exception:
                pass

        app.aboutToQuit.connect(cleanup)

    except Exception:
        return


def run_app():
    set_win_app_id()
    _single_instance_or_raise_existing()

    # ===== Check de actualización en arranque (SILENT) =====
    try:
        from .updater import check_for_updates_and_maybe_install
        check_for_updates_and_maybe_install(APP_CONFIG, parent=None, log=log)
    except Exception:
        log.exception("Fallo al ejecutar el chequeo de actualización")

    app = QApplication(sys.argv)

    app_icon = load_app_icon(COUNTRY_CODE)
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)

    ensure_data_seed_if_empty()

    # Por defecto, permitir abrir sin catálogo
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

        log.info("Catálogo desde DB: productos=%d presentaciones=%d", len(df_productos), len(df_presentaciones))

    except Exception as e:
        log.exception("Error inicializando DB/Schema")
        QMessageBox.critical(None, "Error", f"❌ Error inicializando la base de datos:\n{e}")
        sys.exit(1)

    catalog = CatalogManager(df_productos, df_presentaciones)
    events = QuoteEvents()

    win = QuoteHistoryWindow(catalog_manager=catalog, quote_events=events, app_icon=app_icon)

    # Listener: si alguien abre otra instancia, esta se trae al frente
    _install_show_event_listener(app, get_window_callable=lambda: win)

    win.show()
    sys.exit(app.exec())
