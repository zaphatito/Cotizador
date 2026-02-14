# src/widgets_parts/menu.py
from __future__ import annotations

import os

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QPushButton, QMessageBox,
    QFileDialog, QDialog, QFormLayout, QLineEdit, QHBoxLayout
)
from PySide6.QtGui import QIcon

from ..paths import DATA_DIR, COTIZACIONES_DIR
from ..db_path import resolve_db_path
from ..logging_setup import get_logger

from ..app_window import SistemaCotizaciones
from ..config import APP_CURRENCY, get_secondary_currencies

from sqlModels.db import connect, ensure_schema, tx
from sqlModels.rates_repo import load_rates, set_rate

from ..catalog_sync import sync_catalog_from_excel_path, load_catalog_from_db

from .rates_history_dialog import RatesHistoryDialog

log = get_logger(__name__)


class RatesDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Tasas de cambio (DB)")
        self.resize(420, 220)

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
            form.addRow(f"{self.base} → {cur_u}:", e)

        btns = QHBoxLayout()
        btn_save = QPushButton("Guardar")
        btn_close = QPushButton("Cerrar")
        btn_save.clicked.connect(self._save)
        btn_close.clicked.connect(self.reject)
        btns.addStretch(1)
        btns.addWidget(btn_save)
        btns.addWidget(btn_close)
        layout.addLayout(btns)

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
    Ventana menú (singleton). NO contiene histórico.
    """
    _instance = None

    @classmethod
    def show_singleton(cls, *, catalog_manager, quote_events, app_icon: QIcon, parent=None, assistant_controller=None):
        if cls._instance is not None:
            cls._instance.show()
            cls._instance.raise_()
            cls._instance.activateWindow()
            return cls._instance

        # auto-detect (para no obligarte a cambiar el caller)
        if assistant_controller is None and parent is not None:
            for attr in ("assistant_controller", "_assistant_controller", "ai_assistant", "_ai_assistant"):
                if hasattr(parent, attr):
                    assistant_controller = getattr(parent, attr, None)
                    if assistant_controller is not None:
                        break

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
        self.setWindowTitle("Menú")
        self.resize(520, 420)
        if not app_icon.isNull():
            self.setWindowIcon(app_icon)

        self.catalog_manager = catalog_manager
        self.quote_events = quote_events
        self._app_icon = app_icon
        self.assistant_controller = assistant_controller

        self._open_windows: list[SistemaCotizaciones] = []

        if self.catalog_manager is not None:
            try:
                self.catalog_manager.catalog_updated.connect(self._on_catalog_updated)
            except Exception:
                pass

        w = QWidget()
        lay = QVBoxLayout(w)

        self.btn_new = QPushButton("➕ Crear nueva cotización")
        btn_chat_style = QPushButton("🎨 Personalizar chat")
        btn_rates = QPushButton("💱 Configurar tasas de cambio")
        btn_rates_hist = QPushButton("📈 Ver histórico de tasas")
        btn_update = QPushButton("📦 Actualizar productos")
        btn_open_quotes = QPushButton("📁 Abrir carpeta cotizaciones")
        btn_close = QPushButton("Cerrar menú")

        self.btn_new.clicked.connect(self._open_new_quote)
        btn_chat_style.clicked.connect(self._open_chat_style)
        btn_rates.clicked.connect(self._open_rates)
        btn_rates_hist.clicked.connect(self._open_rates_history)
        btn_update.clicked.connect(self._update_products_choose_excel)
        btn_open_quotes.clicked.connect(self._open_quotes_folder)
        btn_close.clicked.connect(self.close)

        lay.addWidget(self.btn_new)
        lay.addWidget(btn_chat_style)
        lay.addSpacing(6)
        lay.addWidget(btn_rates)
        lay.addWidget(btn_rates_hist)
        lay.addWidget(btn_update)
        lay.addSpacing(10)
        lay.addWidget(btn_open_quotes)
        lay.addStretch(1)
        lay.addWidget(btn_close)

        self.setCentralWidget(w)

        self._apply_catalog_gate()

    def closeEvent(self, event):
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

    def _has_products(self) -> bool:
        try:
            df = getattr(self.catalog_manager, "df_productos", None)
            return (df is not None) and (not df.empty)
        except Exception:
            return False

    def _apply_catalog_gate(self):
        ok = self._has_products()
        self.btn_new.setEnabled(ok)
        tip = "Primero importa/actualiza productos para poder crear cotizaciones."
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

    def _open_chat_style(self):
        """
        Abre el widget de personalización del chat (QSettings por dispositivo).
        """
        dock = None
        try:
            if self.assistant_controller is not None:
                dock = getattr(self.assistant_controller, "dock", None)
        except Exception:
            dock = None

        if dock is None:
            QMessageBox.information(
                self,
                "Chat no disponible",
                "No encontré el panel del asistente.\n\n"
                "Abre el chat (Ctrl+K) para inicializarlo y luego vuelve al menú."
            )
            self._close_soon()
            return

        try:
            dock.open_personalization_dialog()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo abrir la personalización:\n{e}")

        self._close_soon()

    def _open_new_quote(self):
        if not self._has_products():
            QMessageBox.warning(
                self,
                "Sin productos",
                "No puedes crear cotizaciones sin productos.\n\n"
                "Usa 📦 Actualizar productos.",
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

    def _open_rates(self):
        dlg = RatesDialog(self)
        dlg.exec()
        try:
            self.quote_events.rates_updated.emit()
        except Exception:
            pass
        self._close_soon()

    def _open_rates_history(self):
        dlg = RatesHistoryDialog(self, base_currency=APP_CURRENCY, quote_events=self.quote_events)
        dlg.exec()
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

            if df_productos is None or df_productos.empty:
                raise RuntimeError("products_current quedó vacío luego de actualizar.")

            self.catalog_manager.set_catalog(df_productos, df_presentaciones)

            self._apply_catalog_gate()
            self._rebuild_ai_index_soon()

            QMessageBox.information(
                self,
                "Catálogo actualizado",
                f"Excel: {os.path.basename(path)}\n"
                f"Productos: {len(df_productos)}\nPresentaciones: {len(df_presentaciones)}\n\n"
                "Se actualizó el catálogo en todas las ventanas abiertas.",
            )

        except Exception as e:
            log.exception("Error actualizando catálogo desde Excel seleccionado")
            QMessageBox.critical(self, "Error", f"No se pudo actualizar el catálogo:\n{e}")

        self._close_soon()

    def _open_quotes_folder(self):
        try:
            os.startfile(COTIZACIONES_DIR)
        except Exception:
            pass
        self._close_soon()
