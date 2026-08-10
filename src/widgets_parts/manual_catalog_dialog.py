from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


@dataclass(frozen=True)
class ManualCatalogChoice:
    operation: str
    catalog_id: int | None = None


def _records(value: Iterable[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    return [dict(record) for record in (value or ())]


class ManualCatalogDialog(QDialog):
    """Lista primero los catálogos; el archivo se solicita después de aceptar."""

    def __init__(
        self,
        parent: QWidget | None,
        catalogs: Iterable[Mapping[str, Any]],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Catálogos offline")
        self.setMinimumSize(500, 360)
        self.choice: ManualCatalogChoice | None = None

        layout = QVBoxLayout(self)
        explanation = QLabel(
            "Selecciona un catálogo para reemplazarlo por completo o crea uno "
            "nuevo para agregar otra tienda offline."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        self.catalog_list = QListWidget(self)
        self.catalog_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        records = _records(catalogs)
        for record in records:
            name = str(record.get("name") or "Catálogo sin nombre").strip()
            if bool(record.get("is_active")):
                name += "  —  activo"
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, int(record["id"]))
            product_count = int(record.get("product_count") or 0)
            presentation_count = int(record.get("presentation_count") or 0)
            item.setToolTip(
                f"Productos: {product_count} · Presentaciones: {presentation_count}"
            )
            self.catalog_list.addItem(item)
            if bool(record.get("is_active")):
                self.catalog_list.setCurrentItem(item)
        layout.addWidget(self.catalog_list, 1)

        if not records:
            empty = QListWidgetItem("Todavía no hay catálogos cargados")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.catalog_list.addItem(empty)

        actions = QHBoxLayout()
        self.new_button = QPushButton("Nuevo")
        self.new_button.setProperty("variant", "primary")
        self.update_button = QPushButton("Actualizar")
        self.update_button.setEnabled(self.selected_catalog_id() is not None)
        actions.addWidget(self.new_button)
        actions.addWidget(self.update_button)
        actions.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(self.reject)
        actions.addWidget(buttons)
        layout.addLayout(actions)

        self.new_button.clicked.connect(self._choose_new)
        self.update_button.clicked.connect(self._choose_update)
        self.catalog_list.itemSelectionChanged.connect(self._refresh_actions)
        self.catalog_list.itemDoubleClicked.connect(lambda _item: self._choose_update())

    def selected_catalog_id(self) -> int | None:
        item = self.catalog_list.currentItem()
        if item is None:
            return None
        value = item.data(Qt.ItemDataRole.UserRole)
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _refresh_actions(self) -> None:
        self.update_button.setEnabled(self.selected_catalog_id() is not None)

    def _choose_new(self) -> None:
        self.choice = ManualCatalogChoice("new")
        self.accept()

    def _choose_update(self) -> None:
        catalog_id = self.selected_catalog_id()
        if catalog_id is None:
            return
        self.choice = ManualCatalogChoice("update", catalog_id)
        self.accept()


def choose_manual_catalog_operation(
    parent: QWidget | None,
    catalogs: Iterable[Mapping[str, Any]],
) -> ManualCatalogChoice | None:
    dialog = ManualCatalogDialog(parent, catalogs)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.choice


def select_local_catalog(
    parent: QWidget | None,
    catalog_manager,
) -> dict[str, Any] | None:
    catalogs = _records(getattr(catalog_manager, "available_local_catalogs", ()))
    if not catalogs:
        return None

    active_id = getattr(catalog_manager, "active_local_catalog_id", None)
    if len(catalogs) == 1:
        selected = catalogs[0]
    else:
        labels = [str(record.get("name") or "Catálogo sin nombre") for record in catalogs]
        current_index = next(
            (
                index
                for index, record in enumerate(catalogs)
                if int(record["id"]) == active_id
            ),
            0,
        )
        selected_label, accepted = QInputDialog.getItem(
            parent,
            "Tienda / catálogo offline",
            "Selecciona el catálogo para esta cotización:",
            labels,
            current_index,
            False,
        )
        if not accepted:
            return None
        try:
            selected = catalogs[labels.index(str(selected_label))]
        except ValueError:
            return None

    catalog_id = int(selected["id"])
    catalog_manager.set_active_local_catalog(catalog_id)
    return catalog_manager.local_catalog_record(catalog_id)


__all__ = [
    "ManualCatalogChoice",
    "ManualCatalogDialog",
    "choose_manual_catalog_operation",
    "select_local_catalog",
]
