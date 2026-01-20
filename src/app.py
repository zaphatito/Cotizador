import sys
import ctypes
from ctypes import wintypes

import pandas as pd
from PySide6.QtWidgets import QApplication, QMessageBox

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

def _single_instance_or_exit() -> None:
    """
    Solo permite 1 instancia en Windows.
    Si ya existe, muestra un MessageBox nativo y sale.
    """
    global _MUTEX_HANDLE
    try:
        name = "Local\\SistemaCotizaciones_SingleInstance"

        CreateMutexW = ctypes.windll.kernel32.CreateMutexW
        CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        CreateMutexW.restype = wintypes.HANDLE

        GetLastError = ctypes.windll.kernel32.GetLastError
        ERROR_ALREADY_EXISTS = 183

        h = CreateMutexW(None, True, name)
        if not h:
            return

        if GetLastError() == ERROR_ALREADY_EXISTS:
            try:
                ctypes.windll.user32.MessageBoxW(
                    None,
                    "El Sistema de Cotizaciones ya está abierto.",
                    "Sistema de Cotizaciones",
                    0x40
                )
            except Exception:
                pass
            sys.exit(0)

        _MUTEX_HANDLE = h
    except Exception:
        # si algo raro pasa, no bloquees el arranque
        return


def run_app():
    set_win_app_id()
    _single_instance_or_exit()

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

        # Intento de sync (si falla, seguimos igual)
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
    win.show()
    sys.exit(app.exec())
