# src/app_window_parts/add_items.py
from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QDialog
from PySide6.QtCore import QTimer, Qt

from ..config import (
    listing_allows_products,
    listing_allows_presentations,
    ALLOW_NO_STOCK,
    APP_COUNTRY,
    CATS,
)
from ..utils import nz
from ..pricing import precio_unitario_por_categoria, factor_total_por_categoria
from ..presentations import map_pc_to_bottle_code
from ..logging_setup import get_logger
from ..widgets import CustomProductDialog

log = get_logger(__name__)


def _normalize_tier_token(tier: str) -> str:
    """
    Normaliza tiers que pueden venir del chat (inglés/español) a los tiers internos.
    Retorna uno de: unitario|oferta|minimo|maximo|base|"" (si no reconoce)
    """
    t = (tier or "").strip().lower()
    mp = {
        # oferta
        "oferta": "oferta",
        "offer": "oferta",
        "promo": "oferta",
        "promotion": "oferta",
        # minimo
        "min": "minimo",
        "minimum": "minimo",
        "minimo": "minimo",
        # unitario
        "unit": "unitario",
        "unitario": "unitario",
        "regular": "unitario",
        # maximo
        "max": "maximo",
        "maximum": "maximo",
        "maximo": "maximo",
        "lista": "maximo",
        "pvp": "maximo",
        # base
        "base": "base",
    }
    return mp.get(t, t)


class AddItemsMixin:
    def agregar_producto_personalizado(self):
        dlg = CustomProductDialog(self, app_icon=self._app_icon)
        if dlg.exec() != QDialog.Accepted or not dlg.resultado:
            return
        data = dlg.resultado
        unit_price = float(nz(data["precio"], 0.0))  # siempre en moneda base
        qty = float(nz(data["cantidad"], 1))

        factor = factor_total_por_categoria("SERVICIO")
        subtotal_base = round(unit_price * qty * factor, 2)

        item = {
            "_prod": {"precio_unitario": unit_price},
            "codigo": data["codigo"],
            "producto": data["nombre"],
            "categoria": "SERVICIO",
            "cantidad": qty,
            "ml": "",
            "precio": unit_price,
            "subtotal_base": subtotal_base,
            "total": subtotal_base,
            "observacion": data.get("observacion", ""),
            "stock_disponible": -1.0,
            "precio_override": None,
            "precio_tier": None,
            "factor_total": factor,
        }
        self.model.add_item(item)
        log.info(
            "Producto personalizado agregado: %s x%0.3f %0.2f",
            item["codigo"],
            qty,
            unit_price,
        )

    def _agregar_por_codigo(self, cod: str, *, silent: bool = False) -> bool:
        cod_u = (cod or "").strip().upper()
        if not cod_u:
            return False

        # 1) Presentación tipo PC…
        if cod_u.startswith("PC"):
            if not listing_allows_presentations():
                if not silent:
                    QMessageBox.warning(
                        self,
                        "Restringido por configuración",
                        "El tipo de listado actual no permite Presentaciones.",
                    )
                return False
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
                    if not silent:
                        QMessageBox.warning(
                            self,
                            "Sin botellas",
                            "❌ No hay botellas disponibles para esta presentación.",
                        )
                    return False
                self._selector_pc(pc)
                return True

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
                if not silent:
                    QMessageBox.warning(
                        self,
                        "Restringido por configuración",
                        "El tipo de listado actual no permite Presentaciones.",
                    )
                return False
            self._selector_presentacion(pres)
            return True

        # 3) Producto de catálogo
        prod = next(
            (p for p in self.productos if str(p.get("id", "")).upper() == cod_u),
            None,
        )
        if not prod:
            if not silent:
                QMessageBox.warning(self, "Advertencia", "❌ Producto no encontrado")
            return False

        if not listing_allows_products():
            if not silent:
                QMessageBox.warning(
                    self,
                    "Restringido por configuración",
                    "El tipo de listado actual no permite Productos.",
                )
            return False

        if float(nz(prod.get("cantidad_disponible"), 0.0)) <= 0 and not ALLOW_NO_STOCK:
            if not silent:
                QMessageBox.warning(
                    self, "Sin stock", "❌ Este producto no tiene stock disponible."
                )
            return False

        cat = (prod.get("categoria") or "").upper()

        qty_default = 0.001 if (APP_COUNTRY == "PERU" and cat in CATS) else 1.0
        unit_price = precio_unitario_por_categoria(cat, prod, qty_default)

        factor = factor_total_por_categoria(cat)
        subtotal_base = round(float(unit_price) * float(qty_default) * factor, 2)

        item = {
            "_prod": prod,
            "codigo": prod["id"],
            "producto": prod["nombre"],
            "categoria": cat,
            "cantidad": qty_default,
            "ml": prod.get("ml", ""),
            "precio": float(unit_price),
            "subtotal_base": subtotal_base,
            "total": subtotal_base,
            "observacion": "",
            "stock_disponible": float(nz(prod.get("cantidad_disponible"), 0.0)),
            "precio_override": None,
            "precio_tier": "UNITARIO" if cat == "BOTELLAS" else None,
            "factor_total": factor,
        }
        self.model.add_item(item)
        return True

    def agregar_recomendado(
        self,
        codigo: str,
        *,
        qty: float | None = None,
        precio_override_base: float | str | None = None,
    ) -> bool:
        """
        Agrega un producto/presentación y luego aplica qty + precio recomendado.

        FIXES:
        - NO usar model.rowCount() porque incluye preview rows (_recs_preview).
          Se usa len(self.items) para detectar el/los ítems realmente agregados.
        - Soporta que el “precio” venga como tier string del chat: oferta/minimo/base/etc.
          En ese caso, se aplica como precio_tier y se recalcula.
        """
        items_list = getattr(self, "items", []) or []
        before = len(items_list)

        ok = self._agregar_por_codigo(codigo, silent=True)
        if not ok:
            return False

        items_list = getattr(self, "items", []) or []
        after = len(items_list)
        if after <= before:
            return False

        cod_u = (codigo or "").strip().upper()
        new_items = items_list[before:after]
        if not new_items:
            return False

        target = None
        for it in new_items:
            if str(it.get("codigo") or "").strip().upper() == cod_u:
                target = it
                break
        if target is None:
            target = new_items[-1]

        # Normaliza qty según reglas
        if qty is not None:
            try:
                q = float(qty)
            except Exception:
                q = 1.0

            cat_u = str(target.get("categoria") or "").upper()
            if APP_COUNTRY == "PERU" and cat_u in CATS:
                q = round(max(0.001, q), 3)
            else:
                q = int(round(q))
                if q < 1:
                    q = 1
            target["cantidad"] = q

        # ---- Precio: puede venir como tier string ("offer") o como número base ----
        tier_req = None
        if isinstance(precio_override_base, str):
            tier_req = _normalize_tier_token(precio_override_base)
            precio_override_base = None

        if tier_req:
            # base = sin tier / sin override
            if tier_req == "base":
                target["precio_override"] = None
                target["precio_tier"] = None
            else:
                target["precio_override"] = None
                target["precio_tier"] = tier_req

            try:
                self.model._recalc_price_for_qty(target)
            except Exception:
                pass

        elif precio_override_base is not None:
            try:
                p = float(precio_override_base)
            except Exception:
                p = 0.0

            if p > 0:
                target["precio_override"] = p
                target["precio_tier"] = None
                try:
                    self.model._apply_price_and_total(target, p)
                except Exception:
                    pass
            else:
                try:
                    self.model._recalc_price_for_qty(target)
                except Exception:
                    pass
        else:
            try:
                self.model._recalc_price_for_qty(target)
            except Exception:
                pass

        # refrescar fila(s) reales (0..len(items)-1), NO las preview
        try:
            row0 = before
            row1 = after - 1
            top = self.model.index(row0, 0)
            bottom = self.model.index(row1, self.model.columnCount() - 1)
            self.model.dataChanged.emit(top, bottom, [Qt.DisplayRole, Qt.EditRole])
        except Exception:
            pass

        return True
