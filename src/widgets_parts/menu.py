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
    QInputDialog,
)
from PySide6.QtGui import QIcon

from ..paths import DATA_DIR, COTIZACIONES_DIR
from ..db_path import resolve_db_path
from ..logging_setup import get_logger

from ..app_window import SistemaCotizaciones
from ..config import APP_CURRENCY, get_secondary_currencies, is_ai_enabled
from ..quote_context_service import build_quote_context

from sqlModels.db import connect, ensure_schema, tx
from sqlModels.rates_repo import load_rates, set_rate

from ..catalog_sync import validate_products_catalog_df, products_update_required_message

from .rates_history_dialog import RatesHistoryDialog
from .clients_editor_dialog import ClientsEditorDialog
from .catalog_scope_dialog import select_catalog_scope
from .manual_catalog_dialog import (
    choose_manual_catalog_operation,
    select_local_catalog,
)

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
        stock_matrix_opener=None,
    ):
        if cls._instance is not None:
            cls._instance._stock_matrix_opener = stock_matrix_opener
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
            stock_matrix_opener=stock_matrix_opener,
        )
        cls._instance = win
        win.show()
        win.raise_()
        win.activateWindow()
        return win

    def __init__(
        self,
        *,
        catalog_manager,
        quote_events,
        app_icon: QIcon,
        parent=None,
        assistant_controller=None,
        stock_matrix_opener=None,
    ):
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
        self._stock_matrix_opener = stock_matrix_opener

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
        btn_clients = QPushButton("👥 Consultar clientes")
        self.btn_update = QPushButton("📦 Administrar catálogos offline")
        self.btn_update.setToolTip(
            "Disponible solo en modo offline, sin usuario e ID de cotizador completos."
        )
        self.btn_stock_matrix = QPushButton("🏬 Stock por tiendas")
        btn_open_quotes = QPushButton("📁 Abrir carpeta cotizaciones")
        btn_close = QPushButton("Cerrar menú")

        self.btn_new.clicked.connect(self._open_new_quote)
        btn_config.clicked.connect(self._open_config_dialog)
        btn_rates_hist.clicked.connect(self._open_rates_history)
        btn_clients.clicked.connect(self._open_clients_editor)
        self.btn_update.clicked.connect(self._update_products_choose_excel)
        self.btn_stock_matrix.clicked.connect(self._open_stock_matrix)
        btn_open_quotes.clicked.connect(self._open_quotes_folder)
        btn_close.clicked.connect(self.close)

        lay.addWidget(self.btn_new)
        lay.addWidget(btn_config)
        lay.addSpacing(6)
        lay.addWidget(btn_rates_hist)
        lay.addWidget(btn_clients)
        lay.addWidget(self.btn_update)
        lay.addWidget(self.btn_stock_matrix)
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

    def _catalog_unavailable_message(self, reason: str = "") -> str:
        if bool(getattr(self.catalog_manager, "server_mode", False)):
            return (
                str(reason or "No hay un catalogo remoto guardado para este usuario/cotizador.").strip()
                + "\n\nComprueba las tiendas asignadas y usa 'Stock por tiendas' para actualizar."
            )
        return products_update_required_message(getattr(self.catalog_manager, "df_productos", None))

    def _apply_catalog_gate(self):
        ok, reason = self._catalog_health()
        self.btn_new.setEnabled(ok)
        server_mode = bool(getattr(self.catalog_manager, "server_mode", False))
        manual_catalog_allowed = bool(
            getattr(self.catalog_manager, "manual_catalog_allowed", not server_mode)
        )
        identity_complete = bool(
            getattr(self.catalog_manager, "server_identity_complete", False)
        )
        self.btn_update.setVisible(manual_catalog_allowed)
        self.btn_stock_matrix.setVisible(server_mode)
        self.btn_stock_matrix.setEnabled(
            server_mode and identity_complete and callable(self._stock_matrix_opener)
        )
        tip = (
            "Primero importa/actualiza productos para poder crear cotizaciones."
            if ok
            else self._catalog_unavailable_message(reason)
        )
        if (not ok) and (not tip.strip()):
            tip = reason or "Debes actualizar productos."
        self.btn_new.setToolTip("" if ok else tip)

    def _open_stock_matrix(self):
        opener = self._stock_matrix_opener
        if not callable(opener):
            QMessageBox.information(self, "Stock", "La matriz de stock no esta disponible.")
            return
        opener()
        self._close_soon()

    def _rebuild_ai_index_soon(self):
        if bool(getattr(self.catalog_manager, "server_mode", False)):
            return

        def _run():
            try:
                from ..ai.search_index import LocalSearchIndex

                idx = LocalSearchIndex(resolve_db_path())
                if is_ai_enabled(refresh=True):
                    idx.ensure_and_rebuild()
                else:
                    idx.drop_schema()
            except Exception:
                return

        QTimer.singleShot(0, _run)

    def _open_new_quote(self):
        quote_context = None
        local_catalog_id = None
        df_productos = self.catalog_manager.df_productos
        df_presentaciones = self.catalog_manager.df_presentaciones

        if bool(getattr(self.catalog_manager, "server_mode", False)):
            scope = select_catalog_scope(self, self.catalog_manager)
            if scope is None:
                return
            df_productos, df_presentaciones = self.catalog_manager.catalog_for_scope(scope)
            ok, reason = self.catalog_manager.catalog_health(scope)
            if ok:
                quote_context = build_quote_context(self.catalog_manager, scope)
        else:
            local_catalogs = tuple(
                getattr(self.catalog_manager, "available_local_catalogs", ()) or ()
            )
            if local_catalogs:
                selected_catalog = select_local_catalog(self, self.catalog_manager)
                if selected_catalog is None:
                    return
                local_catalog_id = int(selected_catalog["id"])
                df_productos, df_presentaciones = self.catalog_manager.catalog_for_local(
                    local_catalog_id
                )
                ok, reason = self.catalog_manager.local_catalog_health(local_catalog_id)
            else:
                ok, reason = self._catalog_health()

        if not ok:
            msg = self._catalog_unavailable_message(reason)
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
            df_productos=df_productos,
            df_presentaciones=df_presentaciones,
            app_icon=self._app_icon,
            catalog_manager=self.catalog_manager,
            quote_events=self.quote_events,
            quote_context=quote_context,
            local_catalog_id=local_catalog_id,
        )
        win.show()
        self._open_windows.append(win)

        self._close_soon()

    def _open_rates_history(self):
        base_currency = APP_CURRENCY
        if bool(getattr(self.catalog_manager, "server_mode", False)):
            scope = select_catalog_scope(self, self.catalog_manager)
            if scope is None:
                return
            base_currency = build_quote_context(self.catalog_manager, scope).base_currency
        dlg = RatesHistoryDialog(self, base_currency=base_currency, quote_events=self.quote_events)
        dlg.exec()
        self._close_soon()

    def _open_clients_editor(self):
        country_code = None
        quote_context = None
        if bool(getattr(self.catalog_manager, "server_mode", False)):
            scope = select_catalog_scope(self, self.catalog_manager)
            if scope is None:
                return
            country_code = scope.country_code
            quote_context = build_quote_context(self.catalog_manager, scope)
        kwargs = {
            "app_icon": self._app_icon,
            "server_mode": bool(getattr(self.catalog_manager, "server_mode", False)),
        }
        if country_code:
            kwargs["country_code"] = country_code
        if quote_context is not None:
            kwargs["quote_context"] = quote_context
        dlg = ClientsEditorDialog(self, **kwargs)
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
        server_mode = bool(getattr(self.catalog_manager, "server_mode", False))
        manual_catalog_allowed = bool(
            getattr(self.catalog_manager, "manual_catalog_allowed", not server_mode)
        )
        if not manual_catalog_allowed:
            QMessageBox.warning(
                self,
                "Catalogo remoto",
                "Con usuario e ID de cotizador configurados, el catálogo solo puede "
                "provenir de EFAPI. Una falla temporal conserva el último caché local.",
            )
            return

        catalogs = tuple(
            getattr(self.catalog_manager, "available_local_catalogs", ()) or ()
        )
        choice = choose_manual_catalog_operation(self, catalogs)
        if choice is None:
            return

        catalog_id = choice.catalog_id
        catalog_name = ""
        if choice.operation == "new":
            catalog_name, accepted = QInputDialog.getText(
                self,
                "Nuevo catálogo offline",
                "Nombre de la tienda o catálogo:",
            )
            catalog_name = " ".join(str(catalog_name or "").strip().split())
            if not accepted:
                return
            if not catalog_name:
                QMessageBox.warning(
                    self,
                    "Nombre requerido",
                    "Debes ingresar un nombre para el nuevo catálogo.",
                )
                return
        else:
            selected_record = next(
                (
                    dict(record)
                    for record in catalogs
                    if int(record.get("id") or 0) == int(catalog_id or 0)
                ),
                {},
            )
            catalog_name = str(selected_record.get("name") or "").strip()

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
            record = self.catalog_manager.import_local_catalog(
                path,
                name=catalog_name if choice.operation == "new" else None,
                catalog_id=catalog_id,
            )
            df_productos = self.catalog_manager.df_productos
            df_presentaciones = self.catalog_manager.df_presentaciones
            catalog_name = str(record.get("name") or catalog_name).strip()

            self._apply_catalog_gate()
            self._rebuild_ai_index_soon()

            QMessageBox.information(
                self,
                "Catálogo creado" if choice.operation == "new" else "Catálogo actualizado",
                f"Catálogo: {catalog_name}\n"
                f"Excel: {os.path.basename(path)}\n"
                f"Productos: {len(df_productos)}\nPresentaciones: {len(df_presentaciones)}\n\n"
                + (
                    "Se agregó una nueva tienda offline y quedó activa."
                    if choice.operation == "new"
                    else "Se reemplazó por completo el contenido del catálogo seleccionado."
                ),
            )

        except Exception as e:
            log.exception("Error cargando catálogo offline desde Excel seleccionado")
            QMessageBox.critical(self, "Error", f"No se pudo cargar el catálogo:\n{e}")

        self._close_soon()

    def _open_quotes_folder(self):
        try:
            os.startfile(COTIZACIONES_DIR)
        except Exception:
            pass
        self._close_soon()
