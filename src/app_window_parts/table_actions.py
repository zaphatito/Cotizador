# src/app_window_parts/table_actions.py
from __future__ import annotations

from PySide6.QtWidgets import QMenu
from PySide6.QtGui import QAction
from PySide6.QtCore import Qt, QModelIndex

from ..utils import nz
from ..widgets import (
    show_price_picker,
    show_discount_dialog_for_item,
    show_observation_dialog,
)
from .models import CAN_EDIT_UNIT_PRICE


class TableActionsMixin:
    def mostrar_menu_tabla(self, pos):
        index = self.table.indexAt(pos)
        if not index.isValid():
            return
        row = index.row()
        if row < 0 or row >= len(self.items):
            return
        self._ctx_row = row
        self.table.selectRow(row)
        item = self.items[row]

        menu = QMenu(self)
        cat = (item.get("categoria") or "").upper()

        menu.addAction(self.act_edit_discount)

        can_edit_price = (cat == "SERVICIO") or CAN_EDIT_UNIT_PRICE
        if can_edit_price:
            menu.addAction(self.act_edit_price)
            self.act_clear_price.setEnabled(item.get("precio_override") is not None)
            menu.addAction(self.act_clear_price)

        if cat in ("BOTELLAS", "SERVICIO"):
            if menu.actions():
                menu.addSeparator()
            menu.addAction(self.act_edit)

        if menu.actions():
            menu.addSeparator()
        menu.addAction(self.act_del)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _double_click_tabla(self, index: QModelIndex):
        if not index.isValid():
            return
        col = index.column()
        row = index.row()
        if row < 0 or row >= len(self.items):
            return
        item = self.items[row]
        cat = (item.get("categoria") or "").upper()

        if col == 2:  # Descuento
            self._abrir_dialogo_descuento(row)
            return

        if col == 4:  # Precio
            if (cat == "SERVICIO") or CAN_EDIT_UNIT_PRICE or (cat == "BOTELLAS"):
                self._abrir_selector_precio(row)
            return

        if col in (0, 1):  # Código o Producto → Observación
            if cat in ("BOTELLAS", "SERVICIO"):
                self._abrir_dialogo_observacion(row, item)

    def _abrir_dialogo_descuento(self, row: int):
        if row < 0 or row >= len(self.items):
            return
        it = self.items[row]
        payload = show_discount_dialog_for_item(
            self, self._app_icon, it, self.base_currency
        )
        if not payload:
            return
        idx = self.model.index(row, 2)  # col Descuento
        self.model.setData(idx, payload, Qt.EditRole)

    def editar_descuento_item(self):
        row = self._ctx_row
        if row is None:
            sel = self.table.selectionModel().selectedRows()
            if not sel:
                return
            row = sel[0].row()
        self._abrir_dialogo_descuento(row)

    def _abrir_selector_precio(self, row: int):
        if row < 0 or row >= len(self.items):
            return
        item = self.items[row]

        payload = show_price_picker(self, self._app_icon, item)
        if not payload:
            return
        idx = self.model.index(row, 4)
        self.model.setData(idx, payload, Qt.EditRole)

    def _abrir_dialogo_observacion(self, row: int, item: dict):
        new_obs = show_observation_dialog(
            self, self._app_icon, item.get("observacion", "")
        )
        if new_obs is None:
            return
        item["observacion"] = new_obs
        self.model.dataChanged.emit(
            self.model.index(row, 0),
            self.model.index(row, self.model.columnCount() - 1),
            [Qt.DisplayRole],
        )

    def editar_observacion(self):
        sel = self.table.selectionModel().selectedRows()
        if not sel:
            return
        row = sel[0].row()
        if row < 0 or row >= len(self.items):
            return
        item = self.items[row]
        if (item.get("categoria") or "").upper() not in ("BOTELLAS", "SERVICIO"):
            return
        self._abrir_dialogo_observacion(row, item)

    def editar_precio_unitario(self):
        row = self._ctx_row
        if row is None:
            sel = self.table.selectionModel().selectedRows()
            if not sel:
                return
            row = sel[0].row()
        self._abrir_selector_precio(row)

    def _recalc_price_from_rules(self, item: dict):
        """
        (Mantenido tal cual) Recalcula precio/subtotal/descuento/total en base.
        """
        from .models import _price_from_tier
        from ..pricing import precio_unitario_por_categoria

        cat = (item.get("categoria") or "").upper()
        qty = float(nz(item.get("cantidad"), 0.0))
        base_prod = item.get("_prod") or {}

        override = item.get("precio_override", None)
        if override is not None:
            unit_price = float(override)
        elif item.get("precio_tier"):
            unit_price = float(_price_from_tier(base_prod, item["precio_tier"]) or 0.0)
            if unit_price <= 0:
                unit_price = float(
                    precio_unitario_por_categoria(cat, base_prod, qty) or 0.0
                )
        else:
            unit_price = float(
                precio_unitario_por_categoria(cat, base_prod, qty) or 0.0
            )

        item["precio"] = unit_price

        subtotal = round(unit_price * qty, 2)
        item["subtotal_base"] = subtotal

        d_pct = float(nz(item.get("descuento_pct"), 0.0))
        d_monto = float(nz(item.get("descuento_monto"), 0.0))

        if d_pct > 0 and subtotal > 0:
            d_monto = round(subtotal * d_pct / 100.0, 2)

        if d_monto > subtotal:
            d_monto = subtotal

        item["descuento_monto"] = d_monto

        total = round(subtotal - d_monto, 2)
        if total < 0:
            total = 0.0
        item["total"] = total

    def quitar_reescritura_precio(self):
        sel = self.table.selectionModel().selectedRows()
        if not sel:
            return
        rows = [ix.row() for ix in sel if 0 <= ix.row() < len(self.items)]

        for r in rows:
            idx = self.model.index(r, 4)
            self.model.setData(idx, {"mode": "tier", "tier": "base"}, Qt.EditRole)

    def eliminar_producto(self):
        sel = self.table.selectionModel().selectedRows()
        if not sel:
            return
        rows = [ix.row() for ix in sel]
        self.model.remove_rows(rows)
