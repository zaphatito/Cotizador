from __future__ import annotations

from PySide6.QtWidgets import QInputDialog, QMessageBox, QWidget

from ..catalog_context import CatalogScope


def select_catalog_scope(
    parent: QWidget,
    catalog_manager,
    *,
    preferred: CatalogScope | None = None,
) -> CatalogScope | None:
    """Selecciona un scope remoto sin modificar settings globales."""
    if not bool(getattr(catalog_manager, "server_mode", False)):
        return None

    scopes = tuple(getattr(catalog_manager, "available_scopes", ()) or ())
    if preferred is not None and preferred in scopes:
        catalog_manager.set_active_scope(preferred)
        return preferred
    if not scopes:
        QMessageBox.warning(
            parent,
            "Catálogo remoto no disponible",
            "Este usuario/cotizador no tiene tiendas asignadas ni un catálogo guardado.",
        )
        return None
    if len(scopes) == 1:
        catalog_manager.set_active_scope(scopes[0])
        return scopes[0]

    labels = [scope.label for scope in scopes]
    selected, accepted = QInputDialog.getItem(
        parent,
        "País y empresa",
        "Selecciona el catálogo para esta cotización:",
        labels,
        0,
        False,
    )
    if not accepted:
        return None
    try:
        index = labels.index(str(selected))
    except ValueError:
        return None
    scope = scopes[index]
    catalog_manager.set_active_scope(scope)
    return scope


__all__ = ["select_catalog_scope"]
