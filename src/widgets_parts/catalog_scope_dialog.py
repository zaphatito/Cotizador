from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel,
    QMessageBox, QVBoxLayout, QWidget,
)

from ..catalog_context import CatalogScope
from ..country_rules import country_profile


def scopes_by_country(catalog_manager) -> dict[str, tuple[CatalogScope, ...]]:
    countries: dict[str, list[CatalogScope]] = {}
    for scope in tuple(getattr(catalog_manager, "available_scopes", ()) or ()):
        if isinstance(scope, CatalogScope):
            code = country_profile(scope.country_code).code
            countries.setdefault(code, []).append(scope)
    return {code: tuple(scopes) for code, scopes in countries.items()}


class CatalogScopeDialog(QDialog):
    """Selección por cotización, sin cambiar settings ni el catálogo global."""

    def __init__(self, parent, catalog_manager, *, preferred=None, require_catalog=True):
        super().__init__(parent)
        self.catalog_manager = catalog_manager
        self.require_catalog = require_catalog
        self.selected_scope: CatalogScope | None = None
        self.setWindowTitle("País de la cotización" if require_catalog else "Seleccionar país")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.country = QComboBox()
        self.company = QComboBox()
        self.company_label = QLabel("Empresa:")
        self.currency = QLabel()
        self.stores = QLabel()
        self.stores.setWordWrap(True)
        self.status = QLabel()
        self.status.setWordWrap(True)
        form.addRow("País:", self.country)
        form.addRow(self.company_label, self.company)
        form.addRow("Moneda base:", self.currency)
        form.addRow("Tiendas asignadas:", self.stores)
        layout.addLayout(form)
        layout.addWidget(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Crear cotización" if require_catalog else "Seleccionar")
        self.buttons.button(QDialogButtonBox.Cancel).setText("Cancelar")
        layout.addWidget(self.buttons)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.country.currentIndexChanged.connect(self._country_changed)
        self.company.currentIndexChanged.connect(self._refresh_details)
        self._reload_scopes(preferred or getattr(catalog_manager, "active_scope", None))
        signal = getattr(catalog_manager, "scopes_updated", None)
        if signal is not None:
            signal.connect(self._on_scopes_updated)
        for name in ("scope_catalog_updated", "stock_updated"):
            signal = getattr(catalog_manager, name, None)
            if signal is not None:
                signal.connect(self._refresh_details)

    def _reload_scopes(self, preferred=None):
        self._countries = scopes_by_country(self.catalog_manager)
        self.country.blockSignals(True)
        self.country.clear()
        for code in sorted(self._countries, key=lambda c: country_profile(c).name):
            self.country.addItem(country_profile(code).name, code)
        if preferred is not None:
            index = self.country.findData(country_profile(preferred.country_code).code)
            if index >= 0:
                self.country.setCurrentIndex(index)
        self.country.blockSignals(False)
        self._country_changed()
        if preferred in tuple(getattr(self.catalog_manager, "available_scopes", ()) or ()):
            index = self.company.findData(preferred)
            if index >= 0:
                self.company.setCurrentIndex(index)

    def _on_scopes_updated(self, _scopes):
        self._reload_scopes(self.company.currentData())

    def _country_changed(self, *_args):
        self.company.blockSignals(True)
        self.company.clear()
        for scope in self._countries.get(self.country.currentData(), ()):
            self.company.addItem(scope.company_type, scope)
        multiple = self.company.count() > 1
        self.company.setVisible(multiple)
        self.company_label.setVisible(multiple)
        self.company.blockSignals(False)
        self._refresh_details()

    def _refresh_details(self, *_args):
        scope = self.company.currentData()
        available = tuple(getattr(self.catalog_manager, "available_scopes", ()) or ())
        authorized = scope is not None and scope in available
        self.currency.setText(country_profile(scope.country_code).base_currency if authorized else "—")
        if authorized:
            matrix = self.catalog_manager.stock_matrix(scope)
            stores = matrix.get("stores") or []
            names = [str(store.get("name") or store.get("tienda_nombre") or store.get("code") or store.get("id_tienda")) for store in stores]
            self.stores.setText(", ".join(names) if names else "Sin tiendas disponibles en caché")
            healthy, reason = self.catalog_manager.catalog_health(scope)
            self.status.setText("Catálogo disponible" if healthy else reason)
        else:
            healthy = False
            self.stores.setText("—")
            self.status.setText("No hay países y empresas asignados a este usuario/cotizador.")
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(authorized and (healthy or not self.require_catalog))

    def accept(self):
        self._refresh_details()
        if not self.buttons.button(QDialogButtonBox.Ok).isEnabled():
            return
        self.selected_scope = self.company.currentData()
        super().accept()


def select_catalog_scope(
    parent: QWidget,
    catalog_manager,
    *,
    preferred: CatalogScope | None = None,
    require_catalog: bool = True,
) -> CatalogScope | None:
    """Selecciona un scope remoto sin modificar settings globales."""
    if not bool(getattr(catalog_manager, "server_mode", False)):
        return None

    scopes = tuple(getattr(catalog_manager, "available_scopes", ()) or ())
    if not scopes:
        QMessageBox.warning(
            parent,
            "Catálogo remoto no disponible",
            "Este usuario/cotizador no tiene tiendas asignadas ni un catálogo guardado.",
        )
        return None
    dialog = CatalogScopeDialog(parent, catalog_manager, preferred=preferred, require_catalog=require_catalog)
    try:
        return dialog.selected_scope if dialog.exec() == QDialog.Accepted else None
    finally:
        dialog.deleteLater()


__all__ = ["CatalogScopeDialog", "scopes_by_country", "select_catalog_scope"]
