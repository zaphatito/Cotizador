# src/app_window_parts/main.py
from __future__ import annotations

import pandas as pd

from PySide6.QtWidgets import QMainWindow
from PySide6.QtGui import QIcon
from PySide6.QtCore import Qt

from sqlModels.db import connect, ensure_schema, tx
from sqlModels.settings_repo import get_setting, set_setting

from ..config import (
    APP_CURRENCY,
    SECONDARY_CURRENCY,
    get_secondary_currencies,
    set_currency_context,
    is_ai_enabled,
    is_recommendations_enabled,
)
from ..logging_setup import get_logger
from ..db_path import resolve_db_path
from ..utils import nz

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
    _DEFAULT_SIZE = (980, 640)
    _WIN_KEY_PREFIX = "ui_window_quote"

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
        self.resize(*self._DEFAULT_SIZE)
        if not app_icon.isNull():
            self.setWindowIcon(app_icon)

        self._db_path = resolve_db_path()
        self._window_state_restored = False

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

        self._use_ai_completer = True
        self._recommendations_enabled = bool(is_recommendations_enabled(refresh=True))
        self._build_ui()
        self._restore_window_state()
        self.entry_cliente.textChanged.connect(self._update_title_with_client)
        self._update_title_with_client(self.entry_cliente.text())
        self._build_completer()
        self.set_recommendations_enabled(self._recommendations_enabled)

        self.model.item_added.connect(self._focus_last_row)


        # --- Asistente tipo chat (acciones con confirmación) ---
        if is_ai_enabled(refresh=True):
            try:
                from ..ai.assistant import attach_assistant
                self._assistant = attach_assistant(self)
            except Exception:
                self._assistant = None
        else:
            self._assistant = None

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

    def set_recommendations_enabled(self, enabled: bool):
        self._recommendations_enabled = bool(enabled)
        try:
            self._apply_recommendations_ui_state()
        except Exception:
            pass

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
            pres_map = {}
            for p in (self.presentaciones or []):
                k1 = str(p.get("CODIGO_NORM", "")).strip().upper()
                k2 = str(p.get("CODIGO", "")).strip().upper()
                if k1:
                    pres_map[k1] = p
                if k2:
                    pres_map[k2] = p

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
        doc_value = str(payload.get("cedula", "") or "").strip()
        doc_type_value = str(payload.get("tipo_documento", "") or "").strip().upper()
        if not doc_type_value and "-" in doc_value:
            pref, body = doc_value.split("-", 1)
            pref = str(pref or "").strip().upper()
            body = str(body or "").strip()
            if pref and body:
                doc_type_value = pref
                doc_value = body
        elif doc_type_value and doc_value.upper().startswith(f"{doc_type_value}-"):
            doc_value = doc_value[len(doc_type_value) + 1 :].strip()
        try:
            if hasattr(self, "_set_selected_doc_type"):
                self._set_selected_doc_type(doc_type_value)
        except Exception:
            pass
        self.entry_cedula.setText(doc_value)
        self.entry_telefono.setText(payload.get("telefono", "") or "")

        prod_map = {str(p.get("id", "")).strip(): p for p in (self.productos or [])}
        pres_map = {}
        for p in (self.presentaciones or []):
            k1 = str(p.get("CODIGO_NORM", "")).strip().upper()
            k2 = str(p.get("CODIGO", "")).strip().upper()
            if k1:
                pres_map[k1] = p
            if k2:
                pres_map[k2] = p

        def _build_fallback_prod_for_item(it_row: dict) -> dict:
            """
            Si un item historico ya no matchea catalogo vigente, construir un _prod
            minimo para evitar que futuras recalculaciones dejen el precio en 0.
            """
            cat_u = str(it_row.get("categoria") or "").strip().upper()
            base_price = float(nz(it_row.get("precio"), 0.0))
            try:
                pid = int(nz(it_row.get("id_precioventa"), 1) or 1)
            except Exception:
                pid = 1
            if pid not in (1, 2, 3):
                pid = 1
            if base_price <= 0:
                return {}
            return {
                "categoria": cat_u,
                "p_max": float(base_price),
                "p_min": float(base_price),
                "p_oferta": float(base_price),
                "precio_venta": int(pid),
            }

        def _build_presentation_combo_prod(codigo_combo: str, it_row: dict) -> dict:
            """
            Reconstruye _prod para codigos combinados de presentacion (ej: DD0040100)
            usando la presentacion/base actuales.
            """
            if not hasattr(self, "_find_presentacion_combo_match"):
                return {}
            try:
                match = self._find_presentacion_combo_match(str(codigo_combo or "").strip().upper())
            except Exception:
                match = None
            if not match:
                return {}
            try:
                pres, _base = match
            except Exception:
                return {}

            p_max = float(nz(pres.get("P_MAX", pres.get("p_max", 0.0)), 0.0))
            p_oferta = float(nz(pres.get("P_OFERTA", pres.get("p_oferta", 0.0)), 0.0))
            p_min = float(nz(pres.get("P_MIN", pres.get("p_min", 0.0)), 0.0))

            if p_max <= 0:
                p_max = p_oferta if p_oferta > 0 else p_min
            if p_oferta <= 0:
                p_oferta = p_max if p_max > 0 else p_min
            if p_min <= 0:
                p_min = p_oferta if p_oferta > 0 else p_max

            if p_max <= 0 and p_oferta <= 0 and p_min <= 0:
                return _build_fallback_prod_for_item(it_row)

            return {
                "categoria": "PRESENTACION",
                "p_max": float(p_max),
                "p_oferta": float(p_oferta if p_oferta > 0 else p_max),
                "p_min": float(p_min if p_min > 0 else (p_oferta if p_oferta > 0 else p_max)),
                "precio_venta": 1,
            }

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
            cat_u_in = str(it.get("categoria") or "").strip().upper()

            prod = prod_map.get(codigo)
            if prod is None:
                prod = pres_map.get(codigo.upper())
            if prod is None and cat_u_in == "PRESENTACION":
                prod = _build_presentation_combo_prod(codigo, it)
            if prod is None:
                prod = _build_fallback_prod_for_item(it)

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

            # Al reabrir desde histórico, conservamos el snapshot guardado.
            self.model.add_item(item)

    @staticmethod
    def _parse_int(value, default: int = 0) -> int:
        try:
            return int(str(value).strip())
        except Exception:
            return int(default)

    @staticmethod
    def _parse_bool(value) -> bool:
        s = str(value or "").strip().lower()
        return s in ("1", "true", "yes", "on", "si")

    def _restore_window_state(self):
        con = None
        try:
            con = connect(self._db_path)
            ensure_schema(con)
            p = self._WIN_KEY_PREFIX
            w = self._parse_int(get_setting(con, f"{p}_w", "0"), 0)
            h = self._parse_int(get_setting(con, f"{p}_h", "0"), 0)
            x = self._parse_int(get_setting(con, f"{p}_x", "-1"), -1)
            y = self._parse_int(get_setting(con, f"{p}_y", "-1"), -1)
            is_max = self._parse_bool(get_setting(con, f"{p}_max", "0"))
        except Exception:
            return
        finally:
            if con is not None:
                try:
                    con.close()
                except Exception:
                    pass

        if w > 100 and h > 100:
            self.resize(w, h)
            self._window_state_restored = True
        if x >= 0 and y >= 0:
            self.move(x, y)
            self._window_state_restored = True
        if is_max:
            self.showMaximized()
            self._window_state_restored = True

    def _save_window_state(self):
        try:
            geo = self.normalGeometry() if self.isMaximized() else self.geometry()
            w = int(geo.width())
            h = int(geo.height())
            x = int(geo.x())
            y = int(geo.y())
            is_max = bool(self.isMaximized())
        except Exception:
            return

        if w <= 100 or h <= 100:
            return

        con = None
        try:
            con = connect(self._db_path)
            ensure_schema(con)
            p = self._WIN_KEY_PREFIX
            with tx(con):
                set_setting(con, f"{p}_w", str(w))
                set_setting(con, f"{p}_h", str(h))
                set_setting(con, f"{p}_x", str(x))
                set_setting(con, f"{p}_y", str(y))
                set_setting(con, f"{p}_max", "1" if is_max else "0")
        except Exception:
            pass
        finally:
            if con is not None:
                try:
                    con.close()
                except Exception:
                    pass

    def closeEvent(self, event):
        try:
            self._save_window_state()
        except Exception:
            pass
        super().closeEvent(event)
