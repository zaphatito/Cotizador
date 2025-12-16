# src/app.py
import sys, os
from PySide6.QtWidgets import QApplication, QMessageBox
from .paths import set_win_app_id, load_app_icon, ensure_data_seed_if_empty, DATA_DIR
from .dataio import cargar_excel_productos_desde_inventarios
from .presentations import cargar_presentaciones
from .app_window import SistemaCotizaciones
from .logging_setup import get_logger
from .config import COUNTRY_CODE, APP_CONFIG

log = get_logger(__name__)

def run_app():
    set_win_app_id()
    app = QApplication(sys.argv)

    
    app_icon = load_app_icon(COUNTRY_CODE)
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)

    # ===== Check de actualización en arranque =====
    try:
        from .updater import check_for_updates_and_maybe_install
        check_for_updates_and_maybe_install(APP_CONFIG, parent=None, log=log)
    except Exception:
        log.exception("Fallo al ejecutar el chequeo de actualización")

    ensure_data_seed_if_empty()  # solo asegura carpeta, no copia inventarios por diseño “data vacía”

    try:
        df_productos = cargar_excel_productos_desde_inventarios(DATA_DIR)
        log.info("Productos cargados: %d", len(df_productos))
    except Exception as e:
        log.exception("Error cargando inventarios")
        QMessageBox.critical(None, "Error", f"❌ Error al cargar inventarios:\n{e}")
        sys.exit(1)

    try:
        df_presentaciones = cargar_presentaciones(os.path.join(DATA_DIR, "inventario_lcdp.xlsx"))
        log.info("Presentaciones cargadas: %d", len(df_presentaciones))
    except Exception as e:
        log.exception("Error cargando presentaciones")
        QMessageBox.critical(None, "Error", f"❌ Error al cargar presentaciones (Hoja 2 de inventario_lcdp.xlsx):\n{e}")
        sys.exit(1)

    window = SistemaCotizaciones(df_productos, df_presentaciones, app_icon=app_icon)
    window.show()
    sys.exit(app.exec())
