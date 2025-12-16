# src/app_window_parts/add_items.py
from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QDialog
from PySide6.QtCore import QTimer

from ..config import (
    listing_allows_products,
    listing_allows_presentations,
    ALLOW_NO_STOCK,
    APP_COUNTRY,
    CATS,
)
from ..utils import nz
from ..pricing import precio_unitario_por_categoria
from ..presentations import map_pc_to_bottle_code
from ..logging_setup import get_logger
from ..widgets import CustomProductDialog

log = get_logger(__name__)


class AddItemsMixin:
    def agregar_producto_personalizado(self):
        dlg = CustomProductDialog(self, app_icon=self._app_icon)
        if dlg.exec() != QDialog.Accepted or not dlg.resultado:
            return
        data = dlg.resultado
        unit_price = float(nz(data["precio"], 0.0))  # siempre en moneda base
        qty = int(nz(data["cantidad"], 1))

        item = {
            "_prod": {"precio_unitario": unit_price},
            "codigo": data["codigo"],
            "producto": data["nombre"],
            "categoria": "SERVICIO",
            "cantidad": qty,
            "ml": "",
            "precio": unit_price,
            "total": round(unit_price * qty, 2),
            "observacion": data.get("observacion", ""),
            "stock_disponible": -1.0,
            "precio_override": None,
            "precio_tier": None,
        }
        self.model.add_item(item)
        log.info(
            "Producto personalizado agregado: %s x%d %0.2f",
            item["codigo"],
            qty,
            unit_price,
        )

    def _agregar_por_codigo(self, cod: str):
        cod_u = (cod or "").strip().upper()

        # 1) Presentación tipo PC…
        if cod_u.startswith("PC"):
            if not listing_allows_presentations():
                QMessageBox.warning(
                    self,
                    "Restringido por configuración",
                    "El tipo de listado actual no permite Presentaciones.",
                )
                return
            pc = next(
                (
                    p
                    for p in self._botellas_pc
                    if str(p.get("id", "")).upper() == cod_u
                ),
                None,
            )
            if pc:
                bot_code = map_pc_to_bottle_code(str(pc.get("id", "")))
                bot = next(
                    (
                        b
                        for b in self.productos
                        if str(b.get("id", "")).upper() == (bot_code or "").upper()
                        and (b.get("categoria", "").upper() == "BOTELLAS")
                    ),
                    None,
                )
                if (
                    bot is not None
                    and float(nz(bot.get("cantidad_disponible"), 0.0)) <= 0
                    and not ALLOW_NO_STOCK
                ):
                    QMessageBox.warning(
                        self,
                        "Sin botellas",
                        "❌ No hay botellas disponibles para esta presentación.",
                    )
                    return
                self._selector_pc(pc)
                return

        # 2) Presentación de Hoja 2
        pres = next(
            (
                p
                for p in self.presentaciones
                if str(p.get("CODIGO", "")).upper() == cod_u
            ),
            None,
        )
        if pres:
            if not listing_allows_presentations():
                QMessageBox.warning(
                    self,
                    "Restringido por configuración",
                    "El tipo de listado actual no permite Presentaciones.",
                )
            else:
                self._selector_presentacion(pres)
            return

        # 3) Producto de catálogo
        prod = next(
            (p for p in self.productos if str(p.get("id", "")).upper() == cod_u),
            None,
        )
        if not prod:
            QMessageBox.warning(self, "Advertencia", "❌ Producto no encontrado")
            return
        if not listing_allows_products():
            QMessageBox.warning(
                self,
                "Restringido por configuración",
                "El tipo de listado actual no permite Productos.",
            )
            return

        if float(nz(prod.get("cantidad_disponible"), 0.0)) <= 0 and not ALLOW_NO_STOCK:
            QMessageBox.warning(
                self, "Sin stock", "❌ Este producto no tiene stock disponible."
            )
            return

        cat = (prod.get("categoria") or "").upper()
        qty_default = 0.001 if (APP_COUNTRY == "PERU" and cat in CATS) else 1.0
        unit_price = precio_unitario_por_categoria(cat, prod, qty_default)

        item = {
            "_prod": prod,
            "codigo": prod["id"],
            "producto": prod["nombre"],
            "categoria": cat,
            "cantidad": qty_default,
            "ml": prod.get("ml", ""),
            "precio": float(unit_price),
            "total": round(float(unit_price) * qty_default, 2),
            "observacion": "",
            "stock_disponible": float(nz(prod.get("cantidad_disponible"), 0.0)),
            "precio_override": None,
            "precio_tier": "UNITARIO" if cat == "BOTELLAS" else None,
        }
        self.model.add_item(item)
