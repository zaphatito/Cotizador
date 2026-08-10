from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWizard,
    QWizardPage,
    QWidget,
)

from .country_rules import SUPPORTED_COUNTRIES, normalize_country_name
from .initial_setup import (
    SETUP_PENDING_FILENAME,
    SetupStoreAssignment,
    build_initial_setup_payload,
    build_local_seed_config,
    finalize_offline_initial_setup,
    load_json_object,
    normalize_seed_config,
    save_json_atomic,
)
from .server_identity import (
    has_complete_server_identity,
    validate_server_identity_pair,
)


_COMPANIES = ("LA CASA DEL PERFUME", "EF PERFUMES")


class IdentityPage(QWizardPage):
    def __init__(self, seed: dict[str, Any]):
        super().__init__()
        self.setTitle("Identidad y preferencias")
        self.setSubTitle(
            "Ingrese usuario e ID para usar el servidor. Deje ambos vacíos para "
            "trabajar completamente offline; en ese caso no se enviará ningún dato."
        )
        layout = QFormLayout(self)
        self.username = QLineEdit(str(seed.get("username") or "").strip())
        self.username.setPlaceholderText("Nombre de usuario")
        self.id_cotizador = QLineEdit(
            str(seed.get("id_cotizador") or seed.get("store_id") or "").strip().upper()
        )
        self.id_cotizador.setPlaceholderText("Ej.: COT01")
        self.id_cotizador.setToolTip(
            "Identidad del cotizador/equipo. No representa una única tienda asignada."
        )
        self.telemarketing = QCheckBox("Este equipo es telemarketing")
        self.telemarketing.setChecked(bool(seed.get("telemarketing", seed.get("tienda", False))))
        self.listing_type = QComboBox()
        self.listing_type.addItems(["AMBOS", "PRODUCTOS", "PRESENTACIONES"])
        listing = str(seed.get("listing_type") or "AMBOS").strip().upper()
        index = self.listing_type.findText(listing)
        self.listing_type.setCurrentIndex(max(0, index))
        self.allow_no_stock = QCheckBox("Permitir cotizar sin stock")
        self.allow_no_stock.setChecked(bool(seed.get("allow_no_stock", False)))
        self.enable_ai = QCheckBox("Habilitar asistente local")
        self.enable_ai.setChecked(bool(seed.get("enable_ai", False)))
        self.enable_recommendations = QCheckBox("Habilitar recomendaciones")
        self.enable_recommendations.setChecked(
            bool(seed.get("enable_recommendations", True))
        )
        self.username.textChanged.connect(lambda _text: self.completeChanged.emit())
        self.id_cotizador.textChanged.connect(lambda _text: self.completeChanged.emit())

        layout.addRow("Usuario:", self.username)
        layout.addRow("ID del cotizador:", self.id_cotizador)
        layout.addRow("", self.telemarketing)
        layout.addRow("Tipo de listado:", self.listing_type)
        layout.addRow("", self.allow_no_stock)
        layout.addRow("", self.enable_ai)
        layout.addRow("", self.enable_recommendations)

    def values(self, original_seed: dict[str, Any]) -> dict[str, Any]:
        return {
            **original_seed,
            "username": self.username.text().strip(),
            "id_cotizador": self.id_cotizador.text().strip().upper(),
            "telemarketing": self.telemarketing.isChecked(),
            "listing_type": self.listing_type.currentText().strip().upper(),
            "allow_no_stock": self.allow_no_stock.isChecked(),
            "enable_ai": self.enable_ai.isChecked(),
            "enable_recommendations": self.enable_recommendations.isChecked(),
        }

    def validatePage(self) -> bool:
        wizard = self.wizard()
        seed = self.values(getattr(wizard, "seed", {}))
        try:
            normalize_seed_config(seed)
        except Exception as exc:
            QMessageBox.warning(self, "Datos incompletos", str(exc))
            return False
        return True

    def nextId(self) -> int:
        wizard = self.wizard()
        seed = self.values(getattr(wizard, "seed", {}))
        if not has_complete_server_identity(
            seed.get("username"),
            seed.get("id_cotizador"),
        ):
            return -1
        return int(getattr(wizard, "assignments_page_id", -1))


class AssignmentsPage(QWizardPage):
    COL_DEFAULT = 0
    COL_COUNTRY = 1
    COL_COMPANY = 2
    COL_STORE_CODE = 3
    COL_STORE_NAME = 4
    COL_INVENTORY = 5

    def __init__(self, seed: dict[str, Any]):
        super().__init__()
        self.setTitle("Países, empresas, tiendas y stocks")
        self.setSubTitle(
            "Agregue todas las tiendas que podrá manejar este usuario. Un inventario "
            "Excel carga el catálogo y el stock inicial de esa tienda; si se omite, "
            "el servidor conserva los datos existentes."
        )
        self._seed = seed
        self._default_row = 0
        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Pred.", "País", "Empresa", "Código tienda", "Nombre tienda", "Inventario Excel"]
        )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        add_button = QPushButton("Agregar tienda")
        add_button.clicked.connect(self.add_row)
        remove_button = QPushButton("Quitar")
        remove_button.clicked.connect(self.remove_current_row)
        default_button = QPushButton("Marcar predeterminada")
        default_button.clicked.connect(self.mark_current_default)
        inventory_button = QPushButton("Seleccionar inventario…")
        inventory_button.clicked.connect(self.select_inventory)
        actions.addWidget(add_button)
        actions.addWidget(remove_button)
        actions.addWidget(default_button)
        actions.addStretch(1)
        actions.addWidget(inventory_button)
        layout.addLayout(actions)

        note = QLabel(
            "La tienda predeterminada solo define el contexto inicial. El usuario podrá "
            "cambiar entre todas las asignaciones autorizadas por el servidor."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        self.add_row(
            country=str(seed.get("country") or "PARAGUAY"),
            company=str(seed.get("company_type") or _COMPANIES[0]),
            store_code=str(seed.get("store_id") or ""),
        )

    @staticmethod
    def _combo(values: tuple[str, ...] | list[str], selected: str) -> QComboBox:
        combo = QComboBox()
        combo.addItems([str(value) for value in values])
        normalized = str(selected or "").strip().upper()
        index = combo.findText(normalized)
        combo.setCurrentIndex(max(0, index))
        return combo

    def add_row(
        self,
        _checked: bool = False,
        *,
        country: str = "",
        company: str = "",
        store_code: str = "",
        store_name: str = "",
        inventory_path: str = "",
    ) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, self.COL_DEFAULT, QTableWidgetItem(""))
        self.table.setCellWidget(
            row,
            self.COL_COUNTRY,
            self._combo(list(SUPPORTED_COUNTRIES), normalize_country_name(country)),
        )
        self.table.setCellWidget(
            row,
            self.COL_COMPANY,
            self._combo(_COMPANIES, company),
        )
        self.table.setItem(
            row,
            self.COL_STORE_CODE,
            QTableWidgetItem(str(store_code or "").strip().upper()),
        )
        self.table.setItem(
            row,
            self.COL_STORE_NAME,
            QTableWidgetItem(str(store_name or "").strip()),
        )
        self.table.setItem(
            row,
            self.COL_INVENTORY,
            QTableWidgetItem(str(inventory_path or "").strip()),
        )
        if row == 0:
            self._default_row = 0
        self._paint_default()
        self.table.setCurrentCell(row, self.COL_STORE_CODE)
        self.table.resizeColumnsToContents()

    def _paint_default(self) -> None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, self.COL_DEFAULT)
            if item is None:
                item = QTableWidgetItem()
                self.table.setItem(row, self.COL_DEFAULT, item)
            item.setText("Sí" if row == self._default_row else "")
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)

    def remove_current_row(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        self.table.removeRow(row)
        if self.table.rowCount() == 0:
            self._default_row = 0
            self.add_row()
            return
        if row < self._default_row:
            self._default_row -= 1
        elif row == self._default_row:
            self._default_row = min(row, self.table.rowCount() - 1)
        self._paint_default()

    def mark_current_default(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        self._default_row = row
        self._paint_default()

    def select_inventory(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Inventario", "Seleccione primero una tienda.")
            return
        current = self._item_text(row, self.COL_INVENTORY)
        start_dir = os.path.dirname(current) if current else str(Path.home())
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "Seleccionar inventario de la tienda",
            start_dir,
            "Inventarios Excel (*.xlsx *.xlsm)",
        )
        if path:
            self.table.setItem(row, self.COL_INVENTORY, QTableWidgetItem(path))
            self.table.resizeColumnsToContents()

    def _item_text(self, row: int, column: int) -> str:
        item = self.table.item(row, column)
        return str(item.text() if item is not None else "").strip()

    def assignments(self) -> list[SetupStoreAssignment]:
        result: list[SetupStoreAssignment] = []
        for row in range(self.table.rowCount()):
            country_combo = self.table.cellWidget(row, self.COL_COUNTRY)
            company_combo = self.table.cellWidget(row, self.COL_COMPANY)
            result.append(
                SetupStoreAssignment(
                    country=(
                        country_combo.currentText()
                        if isinstance(country_combo, QComboBox)
                        else ""
                    ),
                    company_type=(
                        company_combo.currentText()
                        if isinstance(company_combo, QComboBox)
                        else ""
                    ),
                    store_code=self._item_text(row, self.COL_STORE_CODE),
                    store_name=self._item_text(row, self.COL_STORE_NAME),
                    inventory_path=self._item_text(row, self.COL_INVENTORY),
                    is_default=(row == self._default_row),
                ).normalized()
            )
        keys = [(*assignment.scope_key, assignment.store_code) for assignment in result]
        if len(keys) != len(set(keys)):
            raise ValueError("Hay tiendas repetidas dentro del mismo país y empresa.")
        return result

    def validatePage(self) -> bool:
        try:
            assignments = self.assignments()
        except Exception as exc:
            QMessageBox.warning(self, "Asignaciones incompletas", str(exc))
            return False
        if not any(item.inventory_path for item in assignments):
            answer = QMessageBox.question(
                self,
                "Sin stock inicial",
                "No seleccionó ningún inventario. Se guardarán las asignaciones, pero "
                "no se enviará catálogo ni stock inicial. ¿Desea continuar?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        return True


class InitialSetupWizard(QWizard):
    def __init__(self, *, seed_path: str):
        super().__init__()
        self.seed_path = os.path.abspath(seed_path)
        try:
            self.seed = load_json_object(self.seed_path)
        except FileNotFoundError:
            self.seed = {}
        self.setWindowTitle("Configuración inicial — Sistema de Cotizaciones")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.resize(1040, 620)
        self.identity_page = IdentityPage(self.seed)
        self.assignments_page = AssignmentsPage(self.seed)
        self.identity_page_id = self.addPage(self.identity_page)
        self.assignments_page_id = self.addPage(self.assignments_page)
        self._last_payload_without_request_meta: str = ""
        self._last_idempotency_key: str | None = None

    @property
    def app_root(self) -> str:
        return os.path.dirname(os.path.dirname(self.seed_path))

    @staticmethod
    def _semantic_payload(payload: dict[str, Any]) -> str:
        value = dict(payload)
        value.pop("idempotency_key", None)
        value.pop("generated_at", None)
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def accept(self) -> None:
        try:
            seed_values = self.identity_page.values(self.seed)
            normalized_seed = normalize_seed_config(seed_values)
            if not has_complete_server_identity(
                normalized_seed["username"],
                normalized_seed["id_cotizador"],
            ):
                from .initial_setup_runner import apply_initial_seed_settings

                finalize_offline_initial_setup(
                    self.seed_path,
                    seed_values,
                    settings_applier=apply_initial_seed_settings,
                )
                QMessageBox.information(
                    self,
                    "Modo offline configurado",
                    "La configuración quedó guardada únicamente en este equipo. "
                    "No se preparó ni se envió ninguna solicitud al servidor.",
                )
                super().accept()
                return

            assignments = self.assignments_page.assignments()

            first_payload = build_initial_setup_payload(
                normalized_seed,
                assignments,
                idempotency_key=self._last_idempotency_key,
            )
            semantic = self._semantic_payload(first_payload)
            if self._last_payload_without_request_meta and semantic != self._last_payload_without_request_meta:
                first_payload = build_initial_setup_payload(normalized_seed, assignments)
                semantic = self._semantic_payload(first_payload)
            self._last_payload_without_request_meta = semantic
            self._last_idempotency_key = str(first_payload["idempotency_key"])

            default_assignment = next(item for item in assignments if item.is_default)
            local_seed = build_local_seed_config(normalized_seed, default_assignment)
            config_dir = os.path.dirname(self.seed_path)
            pending_path = os.path.join(config_dir, SETUP_PENDING_FILENAME)
            save_json_atomic(self.seed_path, local_seed)
            save_json_atomic(pending_path, first_payload)
        except Exception as exc:
            QMessageBox.critical(
                self,
                "No se pudo preparar la configuración",
                str(exc),
            )
            return

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            # Import tardío: el seed definitivo ya existe antes de cargar src.config.
            from .initial_setup_runner import submit_pending_initial_setup

            receipt = submit_pending_initial_setup(self.app_root)
        except Exception as exc:
            QMessageBox.critical(
                self,
                "No se guardó en el servidor",
                "La instalación conserva la solicitud para reintentar, pero no puede "
                f"finalizar la configuración hasta que el servidor la confirme.\n\n{exc}",
            )
            return
        finally:
            QApplication.restoreOverrideCursor()

        QMessageBox.information(
            self,
            "Configuración completada",
            "La identidad, preferencias, asignaciones, catálogos y stocks disponibles "
            "fueron guardados en el servidor. Las futuras revisiones se sincronizarán "
            "automáticamente con este equipo.\n\n"
            f"Solicitud: {receipt.get('idempotency_key', '')}",
        )
        super().accept()


def default_seed_path() -> str:
    if getattr(sys, "frozen", False):
        root = os.path.dirname(os.path.abspath(sys.executable))
    else:
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    return os.path.join(root, "config", "config.json")


def run_initial_setup_wizard(*, seed_path: str | None = None) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    resolved_seed = os.path.abspath(seed_path or default_seed_path())
    pending_path = os.path.join(os.path.dirname(resolved_seed), SETUP_PENDING_FILENAME)
    if os.path.isfile(pending_path):
        try:
            retry_seed = load_json_object(resolved_seed)
        except Exception:
            retry_seed = None

        retry_server_mode: bool | None = None
        if retry_seed is not None:
            try:
                retry_id = (
                    retry_seed.get("id_cotizador")
                    if "id_cotizador" in retry_seed
                    else retry_seed.get("store_id")
                )
                retry_server_mode = validate_server_identity_pair(
                    retry_seed.get("username"),
                    retry_id,
                )
            except ValueError:
                retry_server_mode = None

        if retry_server_mode is False:
            from .initial_setup_runner import apply_initial_seed_settings

            finalize_offline_initial_setup(
                resolved_seed,
                retry_seed or {},
                settings_applier=apply_initial_seed_settings,
            )
            return 0

        if retry_server_mode is True:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                from .initial_setup_runner import submit_pending_initial_setup

                receipt = submit_pending_initial_setup(
                    os.path.dirname(os.path.dirname(resolved_seed))
                )
            except Exception as exc:
                QMessageBox.warning(
                    None,
                    "Configuración pendiente",
                    "Se encontró una configuración preparada, pero el servidor aún no "
                    "pudo confirmarla. Puede reintentar cerrando y abriendo este asistente, "
                    "o editar los datos ahora.\n\n"
                    f"{exc}",
                )
            else:
                QMessageBox.information(
                    None,
                    "Configuración completada",
                    "El servidor confirmó la configuración pendiente.\n\n"
                    f"Solicitud: {receipt.get('idempotency_key', '')}",
                )
                return 0
            finally:
                QApplication.restoreOverrideCursor()
        else:
            QMessageBox.warning(
                None,
                "Identidad incompleta",
                "La solicitud pendiente no se envió. Complete juntos usuario e ID "
                "del cotizador, o deje ambos vacíos para continuar offline.",
            )

    wizard = InitialSetupWizard(seed_path=resolved_seed)
    result = wizard.exec()
    return 0 if result == QWizard.DialogCode.Accepted else 2


__all__ = [
    "AssignmentsPage",
    "IdentityPage",
    "InitialSetupWizard",
    "default_seed_path",
    "run_initial_setup_wizard",
]
