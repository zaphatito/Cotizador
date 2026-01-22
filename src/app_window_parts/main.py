# src/app_window_parts/main.py
from __future__ import annotations

import pandas as pd

from PySide6.QtWidgets import QMainWindow
from PySide6.QtGui import QIcon
from PySide6.QtCore import Qt

from ..config import (
    APP_CURRENCY,
    SECONDARY_CURRENCY,
    get_secondary_currencies,
    set_currency_context,
)
from ..logging_setup import get_logger

from .ui import UiMixin
from .currency import CurrencyMixin
from .completer import CompleterMixin
from .add_items import AddItemsMixin
from .presentations import PresentationsMixin
from .table_actions import TableActionsMixin
from .pdf_actions import PdfActionsMixin

log = get_logger(__name__)


class SistemaCotizaciones(
    UiMixin,
    CurrencyMixin,
    CompleterMixin,
    AddItemsMixin,
    PresentationsMixin,
    TableActionsMixin,
    PdfActionsMixin,
    QMainWindow,
):
    def __init__(
        self,
        df_productos: pd.DataFrame,
        df_presentaciones: pd.DataFrame,
        app_icon: QIcon,
        catalog_manager=None,
        quote_events=None,
    ):
        super().__init__()
        self.setWindowTitle("Cotizador")
        self.resize(980, 640)
        if not app_icon.isNull():
            self.setWindowIcon(app_icon)

        self._catalog_manager = catalog_manager
        self._quote_events = quote_events

        self.productos = df_productos.to_dict("records") if df_productos is not None else []
        self.presentaciones = df_presentaciones.to_dict("records") if df_presentaciones is not None else []
        self.items: list[dict] = []
        self._suppress_next_return = False
        self._ignore_completer = False
        self._shown_once = False
        self._app_icon = app_icon
        self._ctx_row = None

        # === Moneda / tasa (DB) ===
        self.base_currency = APP_CURRENCY
        self.secondary_currency = SECONDARY_CURRENCY
        self.secondary_currencies = [c.upper() for c in (get_secondary_currencies() or []) if c]
        self._rates: dict[str, float] = self._load_exchange_rate_file()  # <- DB
        set_currency_context(self.base_currency, 1.0)

        # PCs visibles: códigos que empiezan por "PC" y categoría "OTROS"
        self._botellas_pc = [
            p
            for p in (self.productos or [])
            if str(p.get("id", "")).upper().startswith("PC")
            and (p.get("categoria", "").upper() == "OTROS")
        ]

        log.info(
            "Ventana iniciada. productos=%d presentaciones=%d botellasPC=%d tasas=%s",
            len(self.productos),
            len(self.presentaciones),
            len(self._botellas_pc),
            self._rates,
        )

        self._build_ui()
        self.entry_cliente.textChanged.connect(self._update_title_with_client)
        self._update_title_with_client(self.entry_cliente.text())
        self._build_completer()

        self.model.item_added.connect(self._focus_last_row)

        # Suscripción a catálogo global
        if self._catalog_manager is not None:
            try:
                self._catalog_manager.catalog_updated.connect(self._on_catalog_updated)
            except Exception:
                pass

        # Suscripción a “rates_updated” (si lo estás usando)
        if self._quote_events is not None:
            try:
                self._quote_events.rates_updated.connect(self._on_rates_updated)
            except Exception:
                pass

    def _on_rates_updated(self):
        # recargar rates desde DB y refrescar label/tabla
        try:
            self._rates = self._load_exchange_rate_file()
        except Exception:
            self._rates = {}
        try:
            self._update_currency_label()
        except Exception:
            pass
        if self.model.rowCount() > 0:
            top = self.model.index(0, 0)
            bottom = self.model.index(self.model.rowCount() - 1, self.model.columnCount() - 1)
            self.model.dataChanged.emit(top, bottom, [Qt.DisplayRole, Qt.EditRole])

    def _on_catalog_updated(self, df_productos: pd.DataFrame, df_presentaciones: pd.DataFrame):
        try:
            self.productos = df_productos.to_dict("records") if df_productos is not None else []
            self.presentaciones = df_presentaciones.to_dict("records") if df_presentaciones is not None else []
            self._botellas_pc = [
                p
                for p in (self.productos or [])
                if str(p.get("id", "")).upper().startswith("PC")
                and (p.get("categoria", "").upper() == "OTROS")
            ]

            try:
                self._build_completer()
            except Exception:
                pass

            prod_map = {str(p.get("id", "")).strip(): p for p in (self.productos or [])}
            pres_map = {str(p.get("CODIGO_NORM", "")).strip().upper(): p for p in (self.presentaciones or [])}

            changed_any = False
            for it in (self.items or []):
                codigo = str(it.get("codigo") or "").strip()
                if not codigo:
                    continue

                prod = prod_map.get(codigo)
                if prod is None:
                    prod = pres_map.get(codigo.upper())

                if prod is not None:
                    it["_prod"] = prod
                    if prod.get("categoria"):
                        it["categoria"] = prod.get("categoria")
                    if "cantidad_disponible" in prod:
                        it["stock_disponible"] = prod.get("cantidad_disponible")

                    try:
                        self.model._recalc_price_for_qty(it)
                    except Exception:
                        pass

                    changed_any = True

            if changed_any and self.model.rowCount() > 0:
                top = self.model.index(0, 0)
                bottom = self.model.index(self.model.rowCount() - 1, self.model.columnCount() - 1)
                self.model.dataChanged.emit(top, bottom, [Qt.DisplayRole, Qt.EditRole])

        except Exception:
            log.exception("Error aplicando actualización de catálogo")

    def load_from_history_payload(self, payload: dict):
        self.limpiar_formulario()

        self.entry_cliente.setText(payload.get("cliente", "") or "")
        self.entry_cedula.setText(payload.get("cedula", "") or "")
        self.entry_telefono.setText(payload.get("telefono", "") or "")

        prod_map = {str(p.get("id", "")).strip(): p for p in (self.productos or [])}
        pres_map = {str(p.get("CODIGO_NORM", "")).strip().upper(): p for p in (self.presentaciones or [])}

        def _extract_stock(prod: dict) -> float | None:
            # ✅ soporta diferentes nombres de columna según tu Excel/DB
            keys = (
                "cantidad_disponible", "CANTIDAD_DISPONIBLE",
                "stock_disponible", "STOCK_DISPONIBLE",
                "stock", "STOCK",
                "existencia", "EXISTENCIA",
            )
            for k in keys:
                if isinstance(prod, dict) and k in prod:
                    v = prod.get(k)
                    if v is None or v == "":
                        continue
                    try:
                        return float(v)
                    except Exception:
                        try:
                            return float(str(v).replace(",", ".").strip())
                        except Exception:
                            return None
            return None

        for it in (payload.get("items_base") or []):
            codigo = str(it.get("codigo") or "").strip()

            prod = prod_map.get(codigo)
            if prod is None:
                prod = pres_map.get(codigo.upper())

            item = dict(it)
            item["_prod"] = prod or {}

            # ✅ refrescar categoría actual (si existe)
            if prod is not None and prod.get("categoria"):
                item["categoria"] = prod.get("categoria")

            # ✅ refrescar stock actual (si existe)
            if prod is not None:
                stock = _extract_stock(prod)
                if stock is not None:
                    item["stock_disponible"] = stock
                else:
                    # si no se puede leer stock desde el catálogo, no forzar rojo
                    item.setdefault("stock_disponible", -1)
            else:
                # producto ya no existe en catálogo -> no control de stock
                item.setdefault("stock_disponible", -1)

            item.setdefault("precio_override", None)
            item.setdefault("precio_tier", None)
            item.setdefault("descuento_mode", None)
            item.setdefault("descuento_pct", 0.0)
            item.setdefault("descuento_monto", 0.0)

            self.model.add_item(item)
