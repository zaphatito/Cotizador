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


class TableActionsMixin:
    def _selected_item_rows(self) -> list[int]:
        sm = self.table.selectionModel()
        if sm is None:
            return []

        rows = sorted(
            {
                int(ix.row())
                for ix in (sm.selectedIndexes() or [])
                if 0 <= int(ix.row()) < len(self.items)
            }
        )
        if rows:
            return rows

        try:
            cur = sm.currentIndex()
        except Exception:
            cur = QModelIndex()
        if cur.isValid() and 0 <= int(cur.row()) < len(self.items):
            return [int(cur.row())]
        return []

    def _consume_ctx_row(self) -> int | None:
        row = self._ctx_row
        self._ctx_row = None
        if row is None:
            return None
        try:
            row = int(row)
        except Exception:
            return None
        if 0 <= row < len(self.items):
            return row
        return None

    def _current_item_row(self) -> int | None:
        try:
            cur = self.table.currentIndex()
        except Exception:
            cur = QModelIndex()
        if cur.isValid() and 0 <= int(cur.row()) < len(self.items):
            return int(cur.row())

        try:
            sm = self.table.selectionModel()
            cur = sm.currentIndex() if sm is not None else QModelIndex()
        except Exception:
            cur = QModelIndex()
        if cur.isValid() and 0 <= int(cur.row()) < len(self.items):
            return int(cur.row())
        return None

    def _single_item_action_row(self) -> int | None:
        row = self._consume_ctx_row()
        if row is not None:
            return row

        row = self._current_item_row()
        if row is not None:
            return row

        rows = self._selected_item_rows()
        if rows:
            return rows[0]
        return None

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

        can_edit_price = (cat == "SERVICIO") or self.model._can_edit_unit_price()
        if can_edit_price:
            menu.addAction(self.act_edit_price)

        if menu.actions():
            menu.addSeparator()
        menu.addAction(self.act_edit)

        if menu.actions():
            menu.addSeparator()
        menu.addAction(self.act_del)
        try:
            menu.exec(self.table.viewport().mapToGlobal(pos))
        finally:
            self._ctx_row = None

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
            if (cat == "SERVICIO") or self.model._can_edit_unit_price() or (cat == "BOTELLAS"):
                self._abrir_selector_precio(row)
            return

        if col in (0, 1):  # Código o Producto → Observación
            self._abrir_dialogo_observacion(row, item)

    def _abrir_dialogo_descuento(self, row: int):
        if row < 0 or row >= len(self.items):
            return
        it = self.items[row]
        current_currency, _secondary, _rate = self._currency_context()
        payload = show_discount_dialog_for_item(
            self,
            self._app_icon,
            it,
            self.base_currency,
            converter=self._convert_from_base,
            current_currency=current_currency,
            country=getattr(self, "country_code", ""),
        )
        if not payload:
            return
        idx = self.model.index(row, 2)  # col Descuento
        self.model.setData(idx, payload, Qt.EditRole)

    def editar_descuento_item(self):
        row = self._single_item_action_row()
        if row is None:
            return
        self._abrir_dialogo_descuento(row)

    def _abrir_selector_precio(self, row: int):
        if row < 0 or row >= len(self.items):
            return
        item = self.items[row]
        current_currency, _secondary, _rate = self._currency_context()

        payload = show_price_picker(
            self,
            self._app_icon,
            item,
            converter=self._convert_from_base,
            current_currency=current_currency,
        )
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
        row = self._single_item_action_row()
        if row is None:
            return
        if row < 0 or row >= len(self.items):
            return
        item = self.items[row]
        self._abrir_dialogo_observacion(row, item)

    def editar_precio_unitario(self):
        row = self._single_item_action_row()
        if row is None:
            return
        self._abrir_selector_precio(row)

    def _recalc_price_from_rules(self, item: dict):
        """
        (Mantenido tal cual) Recalcula precio/subtotal/descuento/total en base.
        """
        from .models import _price_from_tier
        from ..pricing import (
            precio_unitario_por_categoria,
            default_price_id_for_product,
            factor_total_por_categoria,
            discount_from_amount,
            discount_percentage_decimals,
            round_discount_percentage,
        )

        cat = (item.get("categoria") or "").upper()
        qty = float(nz(item.get("cantidad"), 0.0))
        base_prod = item.get("_prod") or {}

        override = item.get("precio_override", None)
        if cat != "SERVICIO":
            override = None
            item["precio_override"] = None
        if override is not None:
            unit_price = float(override)
            item["id_precioventa"] = 4
        elif item.get("precio_tier"):
            unit_price = float(_price_from_tier(base_prod, item["precio_tier"]) or 0.0)
            if unit_price <= 0:
                unit_price = float(
                    precio_unitario_por_categoria(cat, base_prod, qty) or 0.0
                )
            tier_l = str(item.get("precio_tier") or "").strip().lower()
            if "min" in tier_l:
                item["id_precioventa"] = 2
            elif "oferta" in tier_l or "promo" in tier_l:
                item["id_precioventa"] = 3
            elif "base" in tier_l:
                item["id_precioventa"] = int(default_price_id_for_product(base_prod))
            else:
                item["id_precioventa"] = 1
        else:
            unit_price = float(
                precio_unitario_por_categoria(cat, base_prod, qty) or 0.0
            )
            item["id_precioventa"] = (
                int(default_price_id_for_product(base_prod)) if cat != "SERVICIO" else 4
            )

        item["precio"] = unit_price

        factor = float(
            factor_total_por_categoria(
                cat,
                item if item else base_prod,
                country=getattr(self, "country_name", None),
            )
        )
        item["factor_total"] = factor
        subtotal = round(unit_price * qty * factor, 2)
        item["subtotal_base"] = subtotal

        d_pct = float(nz(item.get("descuento_pct"), 0.0))
        d_monto = float(nz(item.get("descuento_monto"), 0.0))
        discount_country = getattr(self, "country_code", "") or getattr(self, "country_name", "")
        integer_discount = discount_percentage_decimals(discount_country) == 0
        discount_mode = str(item.get("descuento_mode") or "").strip().lower()

        if integer_discount and discount_mode == "amount" and d_monto > 0 and subtotal > 0:
            d_pct, d_monto = discount_from_amount(subtotal, d_monto, discount_country)
            item["descuento_mode"] = "percent"
        elif d_pct > 0 and subtotal > 0:
            if integer_discount:
                d_pct = round_discount_percentage(d_pct, discount_country)
            d_monto = round(subtotal * d_pct / 100.0, 2)
        elif integer_discount and d_monto > 0 and subtotal > 0:
            d_pct, d_monto = discount_from_amount(subtotal, d_monto, discount_country)
            item["descuento_mode"] = "percent"

        if d_monto > subtotal:
            d_monto = subtotal

        item["descuento_pct"] = d_pct
        item["descuento_monto"] = d_monto

        total = round(subtotal - d_monto, 2)
        if total < 0:
            total = 0.0
        item["total"] = total

    def quitar_reescritura_precio(self):
        from ..pricing import default_price_id_for_product

        rows = self._selected_item_rows()
        if not rows:
            return

        for r in rows:
            idx = self.model.index(r, 4)
            item = self.items[r]
            cat = (item.get("categoria") or "").upper()
            if cat == "SERVICIO":
                continue
            pid = int(default_price_id_for_product(item.get("_prod") or {}))
            tier = "unitario"
            if pid == 2:
                tier = "minimo"
            elif pid == 3:
                tier = "oferta"
            self.model.setData(idx, {"mode": "tier", "tier": tier}, Qt.EditRole)

    def eliminar_producto(self):
        rows = self._selected_item_rows()
        if not rows:
            return
        self.model.remove_rows(rows)
