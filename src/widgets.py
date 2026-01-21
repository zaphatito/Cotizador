# src/widgets.py
from __future__ import annotations

# Re-export de compatibilidad: antes importabas desde .widgets
# Ahora la lógica vive en .widgets_parts.* pero el import externo no cambia.

from .widgets_parts.helpers import _fmt_trim_decimal, _first_nonzero
from .widgets_parts.currency_dialog import show_currency_dialog
from .widgets_parts.discount_item_dialog import show_discount_dialog_for_item
from .widgets_parts.observation_dialog import show_observation_dialog
from .widgets_parts.preview_dialog import show_preview_dialog
from .widgets_parts.selector_tabla_simple import SelectorTablaSimple
from .widgets_parts.custom_product_dialog import CustomProductDialog
from .widgets_parts.listado_productos_dialog import ListadoProductosDialog
from .widgets_parts.discount_editor import show_discount_editor
from .widgets_parts.price_picker import show_price_picker
from .widgets_parts.toast import Toast
from .presentations import map_pc_to_bottle_code 

__all__ = [
    "_fmt_trim_decimal",
    "_first_nonzero",
    "show_currency_dialog",
    "show_discount_dialog_for_item",
    "show_observation_dialog",
    "show_preview_dialog",
    "SelectorTablaSimple",
    "CustomProductDialog",
    "ListadoProductosDialog",
    "show_discount_editor",
    "show_price_picker",
    "map_pc_to_bottle_code",
    "Toast",
]
