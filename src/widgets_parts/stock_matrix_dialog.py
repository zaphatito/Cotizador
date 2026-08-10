from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ..catalog_context import CatalogScope
from ..stock_matrix import build_store_stock_tabs
from .bounded_table_columns import install_bounded_columns


class StockMatrixDialog(QDialog):
    def __init__(self, *, catalog_manager, sync_service=None, parent=None):
        super().__init__(parent)
        self.catalog_manager = catalog_manager
        self.sync_service = None
        self.setWindowTitle("Stock por tiendas")
        self.resize(1120, 680)
        self.setMinimumSize(760, 460)

        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar por código o nombre")
        self.search.setClearButtonEnabled(True)
        self.refresh_button = QPushButton("Actualizar")
        self.status_label = QLabel("Caché local")
        controls.addWidget(self.search, 1)
        controls.addWidget(self.refresh_button)
        controls.addWidget(self.status_label)
        layout.addLayout(controls)

        self.tabs = QTabWidget()
        self.tabs.setUsesScrollButtons(True)
        self.tabs.setElideMode(Qt.ElideNone)
        layout.addWidget(self.tabs, 1)

        close_button = QPushButton("Cerrar")
        close_button.clicked.connect(self.accept)
        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_row.addWidget(close_button)
        layout.addLayout(close_row)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(180)
        self._search_timer.timeout.connect(self.reload)
        self.search.textChanged.connect(lambda *_: self._search_timer.start())
        self.refresh_button.clicked.connect(self._request_refresh)

        try:
            self.catalog_manager.stock_updated.connect(self._on_stock_updated)
            self.catalog_manager.scopes_updated.connect(self._on_scopes_updated)
        except Exception:
            pass
        self.set_sync_service(sync_service)
        self.reload()

    def _connect_sync_service(self, service) -> None:
        service.sync_started.connect(self._on_sync_started)
        service.sync_succeeded.connect(self._on_sync_succeeded)
        service.sync_failed.connect(self._on_sync_failed)

    def _disconnect_sync_service(self, service) -> None:
        for signal, callback in (
            (service.sync_started, self._on_sync_started),
            (service.sync_succeeded, self._on_sync_succeeded),
            (service.sync_failed, self._on_sync_failed),
        ):
            try:
                signal.disconnect(callback)
            except (RuntimeError, TypeError, ValueError):
                pass

    def set_sync_service(self, service) -> None:
        """Permite enlazar el servicio aunque el dialogo se abriera antes de crearlo."""
        previous = self.sync_service
        if previous is service:
            return
        if previous is not None:
            self._disconnect_sync_service(previous)
        self.sync_service = service
        if service is not None:
            try:
                self._connect_sync_service(service)
            except (AttributeError, RuntimeError, TypeError):
                self._disconnect_sync_service(service)
                self.sync_service = None

    def _current_store_key(self) -> str:
        widget = self.tabs.currentWidget()
        return str(widget.property("store_key") or "") if widget is not None else ""

    @staticmethod
    def _table(headers: list[str], rows: list[list[str]]) -> QTableView:
        table = QTableView()
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QTableView.NoEditTriggers)
        table.setSelectionBehavior(QTableView.SelectRows)
        table.setWordWrap(False)
        model = QStandardItemModel(len(rows), len(headers), table)
        model.setHorizontalHeaderLabels(headers)
        for row_index, values in enumerate(rows):
            for column_index, value in enumerate(values):
                item = QStandardItem(value)
                if column_index == len(headers) - 1:
                    item.setTextAlignment(Qt.AlignCenter)
                model.setItem(row_index, column_index, item)
        table.setModel(model)
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        if headers:
            header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        if len(headers) > 1:
            header.setSectionResizeMode(1, QHeaderView.Stretch)
        if len(headers) > 2:
            header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        if len(headers) > 3:
            header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        install_bounded_columns(table, fill_column=1)
        return table

    def reload(self) -> None:
        selected_key = self._current_store_key()
        self.tabs.clear()
        scopes = tuple(getattr(self.catalog_manager, "available_scopes", ()) or ())
        multiple_scopes = len(scopes) > 1
        for scope in scopes:
            if not isinstance(scope, CatalogScope):
                continue
            matrix = self.catalog_manager.stock_matrix(scope)
            record = self.catalog_manager.scope_record(scope)
            country = str(record.get("country_name") or scope.country_code).strip()
            company = str(record.get("company_type") or scope.company_type).strip()
            for store_view in build_store_stock_tabs(matrix, query=self.search.text()):
                page = QWidget()
                store_key = f"{scope.group_key}:{store_view['store_id']}"
                page.setProperty("store_key", store_key)
                page_layout = QVBoxLayout(page)
                context = QLabel(f"{country} · {company}")
                context.setProperty("role", "muted")
                page_layout.addWidget(context)
                type_tabs = QTabWidget()
                type_tabs.setUsesScrollButtons(True)
                for section in store_view["sections"]:
                    type_tabs.addTab(
                        self._table(section["headers"], section["rows"]),
                        f"{section['label']} ({len(section['rows'])})",
                    )
                if type_tabs.count() == 0:
                    empty = QLabel("No hay ítems que coincidan con la búsqueda.")
                    empty.setAlignment(Qt.AlignCenter)
                    type_tabs.addTab(empty, "Sin resultados")
                page_layout.addWidget(type_tabs, 1)
                tab_label = str(store_view["label"])
                if multiple_scopes:
                    tab_label = f"{tab_label} · {country}/{company}"
                self.tabs.addTab(page, tab_label)
                self.tabs.setTabToolTip(self.tabs.count() - 1, tab_label)

        if self.tabs.count() == 0:
            empty = QLabel("No hay tiendas asignadas para este usuario/cotizador.")
            empty.setAlignment(Qt.AlignCenter)
            self.tabs.addTab(empty, "Sin tiendas")
        elif selected_key:
            for index in range(self.tabs.count()):
                page = self.tabs.widget(index)
                if str(page.property("store_key") or "") == selected_key:
                    self.tabs.setCurrentIndex(index)
                    break

    def _request_refresh(self) -> None:
        if self.sync_service is None:
            self.status_label.setText("Sin sincronización configurada")
            return
        accepted = self.sync_service.request_sync(manual=True)
        if accepted:
            self.status_label.setText("Actualizando…")
            self.refresh_button.setEnabled(False)
        else:
            self.status_label.setText("Actualización en curso…")

    def _on_sync_started(self, _manual: bool) -> None:
        self.status_label.setText("Actualizando…")
        self.refresh_button.setEnabled(False)

    def _on_sync_succeeded(self, _outcome: object) -> None:
        self.status_label.setText("Stock actualizado")
        self.refresh_button.setEnabled(True)
        self.reload()

    def _on_sync_failed(self, _message: str) -> None:
        self.status_label.setText("Sin conexión; mostrando el último stock guardado")
        self.refresh_button.setEnabled(True)

    def _on_stock_updated(self, _scope: object) -> None:
        self.reload()

    def _on_scopes_updated(self, _scopes: object) -> None:
        self.reload()


__all__ = ["StockMatrixDialog"]
