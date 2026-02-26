from __future__ import annotations

import os

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QPushButton,
    QMessageBox,
    QFileDialog,
    QDialog,
    QFormLayout,
    QLineEdit,
    QHBoxLayout,
)
from PySide6.QtGui import QIcon

from ..paths import DATA_DIR, COTIZACIONES_DIR
from ..db_path import resolve_db_path
from ..logging_setup import get_logger

from ..app_window import SistemaCotizaciones
from ..config import APP_CURRENCY, get_secondary_currencies

from sqlModels.db import connect, ensure_schema, tx
from sqlModels.rates_repo import load_rates, set_rate

from ..catalog_sync import (
    sync_catalog_from_excel_path,
    load_catalog_from_db,
    validate_products_catalog_df,
    products_update_required_message,
)

from .rates_history_dialog import RatesHistoryDialog
from .clients_editor_dialog import ClientsEditorDialog

log = get_logger(__name__)


class RatesDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Tasas de cambio (DB)")
        self.setMinimumWidth(380)

        self._edits: dict[str, QLineEdit] = {}

        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)

        self.base = APP_CURRENCY

        db_path = resolve_db_path()
        con = connect(db_path)
        ensure_schema(con)
        rates = load_rates(con, self.base)
        con.close()

        for cur in (get_secondary_currencies() or []):
            cur_u = cur.upper()
            e = QLineEdit()
            e.setPlaceholderText(f"1 {self.base} = ? {cur_u}")
            e.setText(str(rates.get(cur_u, "")))
            self._edits[cur_u] = e
            form.addRow(f"{self.base} -> {cur_u}:", e)

        btns = QHBoxLayout()
        btn_save = QPushButton("Guardar")
        btn_save.setProperty("variant", "primary")
        btn_close = QPushButton("Cerrar")
        btn_save.clicked.connect(self._save)
        btn_close.clicked.connect(self.reject)
        btns.addStretch(1)
        btns.addWidget(btn_save)
        btns.addWidget(btn_close)
        layout.addLayout(btns)
        self.adjustSize()

    def _save(self):
        db_path = resolve_db_path()
        con = connect(db_path)
        ensure_schema(con)
        with tx(con):
            for cur, e in self._edits.items():
                txt = (e.text() or "").strip().replace(",", ".")
                try:
                    rate = float(txt) if txt else 1.0
                except Exception:
                    rate = 1.0
                set_rate(con, self.base, cur, rate)
        con.close()
        QMessageBox.information(self, "OK", "Tasas guardadas en DB.")
        self.accept()


class MainMenuWindow(QMainWindow):
    """
    Ventana menu (singleton). No contiene historico.
    """

    _instance = None

    @classmethod
    def show_singleton(
        cls,
        *,
        catalog_manager,
        quote_events,
        app_icon: QIcon,
        parent=None,
        assistant_controller=None,
    ):
        if cls._instance is not None:
            try:
                cls._instance._apply_catalog_gate()
            except Exception:
                pass
            cls._instance.show()
            cls._instance.raise_()
            cls._instance.activateWindow()
            return cls._instance

        win = cls(
            catalog_manager=catalog_manager,
            quote_events=quote_events,
            app_icon=app_icon,
            parent=parent,
            assistant_controller=assistant_controller,
        )
        cls._instance = win
        win.show()
        win.raise_()
        win.activateWindow()
        return win

    def __init__(self, *, catalog_manager, quote_events, app_icon: QIcon, parent=None, assistant_controller=None):
        super().__init__(parent)
        self.setWindowTitle("Menu")
        self.resize(520, 360)
        if not app_icon.isNull():
            self.setWindowIcon(app_icon)

        self.catalog_manager = catalog_manager
        self.quote_events = quote_events
        self._app_icon = app_icon

        # Se conserva por compatibilidad con callers existentes.
        self._assistant_controller = assistant_controller

        self._open_windows: list[SistemaCotizaciones] = []

        if self.catalog_manager is not None:
            try:
                self.catalog_manager.catalog_updated.connect(self._on_catalog_updated)
            except Exception:
                pass

        w = QWidget()
        lay = QVBoxLayout(w)

        self.btn_new = QPushButton("➕ Crear nueva cotización")
        self.btn_new.setProperty("variant", "primary")
        btn_config = QPushButton("⚙️ Configuración")
        btn_rates_hist = QPushButton("📈 Ver histórico de tasas")
        btn_clients = QPushButton("👥 Editar clientes")
        btn_update = QPushButton("📦 Actualizar productos")
        btn_open_quotes = QPushButton("📁 Abrir carpeta cotizaciones")
        btn_close = QPushButton("Cerrar menú")

        self.btn_new.clicked.connect(self._open_new_quote)
        btn_config.clicked.connect(self._open_config_dialog)
        btn_rates_hist.clicked.connect(self._open_rates_history)
        btn_clients.clicked.connect(self._open_clients_editor)
        btn_update.clicked.connect(self._update_products_choose_excel)
        btn_open_quotes.clicked.connect(self._open_quotes_folder)
        btn_close.clicked.connect(self.close)

        lay.addWidget(self.btn_new)
        lay.addWidget(btn_config)
        lay.addSpacing(6)
        lay.addWidget(btn_rates_hist)
        lay.addWidget(btn_clients)
        lay.addWidget(btn_update)
        lay.addSpacing(10)
        lay.addWidget(btn_open_quotes)
        lay.addStretch(1)
        lay.addWidget(btn_close)

        self.setCentralWidget(w)
        self._apply_catalog_gate()

    def closeEvent(self, event):
        try:
            p = self.parentWidget()
            if p is not None and p.isVisible():
                self.hide()
                event.ignore()
                return
        except Exception:
            pass
        try:
            MainMenuWindow._instance = None
        except Exception:
            pass
        super().closeEvent(event)

    def _close_soon(self):
        QTimer.singleShot(0, self.close)

    def _on_catalog_updated(self, *_):
        self._apply_catalog_gate()
        self._rebuild_ai_index_soon()

    def _catalog_health(self) -> tuple[bool, str]:
        try:
            mgr = self.catalog_manager
        except Exception:
            return False, "No se pudo leer el catalogo de productos."
        try:
            if mgr is not None and hasattr(mgr, "catalog_health"):
                return mgr.catalog_health()
        except Exception:
            pass
        try:
            df = getattr(mgr, "df_productos", None)
        except Exception:
            return False, "No se pudo leer el catalogo de productos."
        return validate_products_catalog_df(df)

    def _has_products(self) -> bool:
        ok, _reason = self._catalog_health()
        return bool(ok)

    def _apply_catalog_gate(self):
        ok, reason = self._catalog_health()
        self.btn_new.setEnabled(ok)
        tip = (
            "Primero importa/actualiza productos para poder crear cotizaciones."
            if ok
            else products_update_required_message(getattr(self.catalog_manager, "df_productos", None))
        )
        if (not ok) and (not tip.strip()):
            tip = reason or "Debes actualizar productos."
        self.btn_new.setToolTip("" if ok else tip)

    def _rebuild_ai_index_soon(self):
        def _run():
            try:
                from ..ai.search_index import LocalSearchIndex

                idx = LocalSearchIndex(resolve_db_path())
                idx.ensure_and_rebuild()
            except Exception:
                return

        QTimer.singleShot(0, _run)

    def _open_new_quote(self):
        ok, reason = self._catalog_health()
        if not ok:
            msg = products_update_required_message(getattr(self.catalog_manager, "df_productos", None))
            if not msg.strip():
                msg = reason or "Debes actualizar productos."
            QMessageBox.warning(
                self,
                "Catalogo invalido",
                msg,
            )
            self._apply_catalog_gate()
            return

        win = SistemaCotizaciones(
            df_productos=self.catalog_manager.df_productos,
            df_presentaciones=self.catalog_manager.df_presentaciones,
            app_icon=self._app_icon,
            catalog_manager=self.catalog_manager,
            quote_events=self.quote_events,
        )
        win.show()
        self._open_windows.append(win)

        self._close_soon()

    def _open_rates_history(self):
        dlg = RatesHistoryDialog(self, base_currency=APP_CURRENCY, quote_events=self.quote_events)
        dlg.exec()
        self._close_soon()

    def _open_clients_editor(self):
        dlg = ClientsEditorDialog(self, app_icon=self._app_icon)
        dlg.exec()
        self._close_soon()

    def _open_config_dialog(self):
        p = self.parent()
        if p is not None and hasattr(p, "_open_config_dialog"):
            try:
                p._open_config_dialog()
            except Exception as e:
                QMessageBox.critical(self, "Error", f"No se pudo abrir configuracion:\n{e}")
            self._close_soon()
            return

        try:
            from .quote_history_dialog import HistoryConfigDialog

            dlg = HistoryConfigDialog(self)
            dlg.exec()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo abrir configuracion:\n{e}")
        self._close_soon()

    def _update_products_choose_excel(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Seleccionar Excel de inventario",
            DATA_DIR if os.path.isdir(DATA_DIR) else os.getcwd(),
            "Excel (*.xlsx *.xlsm *.xls)",
        )
        if not path:
            self._close_soon()
            return

        try:
            db_path = resolve_db_path()
            con = connect(db_path)
            ensure_schema(con)

            with tx(con):
                sync_catalog_from_excel_path(con, path)

            df_productos, df_presentaciones = load_catalog_from_db(con)
            con.close()

            ok, reason = validate_products_catalog_df(df_productos)
            if not ok:
                raise RuntimeError(
                    "El catalogo quedo invalido luego de actualizar: "
                    f"{reason}. Revisa el Excel e intenta de nuevo."
                )

            self.catalog_manager.set_catalog(df_productos, df_presentaciones)

            self._apply_catalog_gate()
            self._rebuild_ai_index_soon()

            QMessageBox.information(
                self,
                "Catalogo actualizado",
                f"Excel: {os.path.basename(path)}\n"
                f"Productos: {len(df_productos)}\nPresentaciones: {len(df_presentaciones)}\n\n"
                "Se actualizo el catalogo en todas las ventanas abiertas.",
            )

        except Exception as e:
            log.exception("Error actualizando catalogo desde Excel seleccionado")
            QMessageBox.critical(self, "Error", f"No se pudo actualizar el catalogo:\n{e}")

        self._close_soon()

    def _open_quotes_folder(self):
        try:
            os.startfile(COTIZACIONES_DIR)
        except Exception:
            pass
        self._close_soon()
