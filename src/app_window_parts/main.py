# src/app_window_parts/main.py
from __future__ import annotations

from copy import deepcopy

import pandas as pd

from PySide6.QtWidgets import QMainWindow
from PySide6.QtGui import QIcon
from PySide6.QtCore import Qt

from sqlModels.db import connect, ensure_schema, tx
from sqlModels.settings_repo import get_setting, set_setting

from ..config import (
    APP_CURRENCY,
    APP_COUNTRY,
    APP_COMPANY_TYPE,
    APP_USERNAME,
    COUNTRY_CODE,
    STORE_ID,
    SECONDARY_CURRENCY,
    currency_for_country,
    secondary_currencies_for_country,
    get_secondary_currencies,
    is_ai_enabled,
    is_recommendations_enabled,
)
from ..country_rules import normalize_country_name
from ..catalog_refresh import refreshed_product_for_item
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
from .history_snapshot import history_base_snapshot

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
        quote_context=None,
        local_catalog_id: int | None = None,
    ):
        manager_server_mode = bool(getattr(catalog_manager, "server_mode", False))
        if manager_server_mode and quote_context is None:
            raise ValueError(
                "El modo servidor requiere un QuoteContext con pais y empresa."
            )
        if manager_server_mode:
            scope = getattr(quote_context, "scope", None)
            available_scopes = tuple(getattr(catalog_manager, "available_scopes", ()) or ())
            if scope is None or scope not in available_scopes:
                raise ValueError("El scope de la cotizacion ya no esta autorizado.")
            manager_username = str(getattr(catalog_manager, "username", "") or "").strip()
            manager_cotizador = str(
                getattr(catalog_manager, "id_cotizador", "") or ""
            ).strip()
            context_username = str(getattr(quote_context, "username", "") or "").strip()
            context_cotizador = str(
                getattr(quote_context, "id_cotizador", "") or ""
            ).strip()
            if (
                manager_username.casefold() != context_username.casefold()
                or manager_cotizador.casefold() != context_cotizador.casefold()
            ):
                raise ValueError(
                    "El propietario del QuoteContext no coincide con la cache activa."
                )

        super().__init__()
        self.setWindowTitle("Cotizador")
        self.resize(*self._DEFAULT_SIZE)
        if not app_icon.isNull():
            self.setWindowIcon(app_icon)

        self._db_path = resolve_db_path()
        self._window_state_restored = False

        self._catalog_manager = catalog_manager
        self._quote_events = quote_events
        self.quote_context = quote_context
        self._server_catalog_mode = manager_server_mode
        self._local_catalog_name = ""
        self._local_catalog_id = (
            int(local_catalog_id) if local_catalog_id is not None else None
        )
        if not manager_server_mode and self._local_catalog_id is not None:
            local_record = catalog_manager.local_catalog_record(self._local_catalog_id)
            if not local_record:
                raise ValueError("El catálogo offline seleccionado ya no existe.")
            local_name = str(local_record.get("name") or "").strip()
            if local_name:
                self._local_catalog_name = local_name
        scope = getattr(quote_context, "scope", None)
        self.country_code = str(getattr(scope, "country_code", "") or COUNTRY_CODE).strip().upper()
        self.country_name = normalize_country_name(self.country_code, default=APP_COUNTRY)
        self.company_type = str(
            getattr(scope, "company_type", "") or APP_COMPANY_TYPE
        ).strip().upper()
        self.cotizador_username = str(
            getattr(quote_context, "username", "") or APP_USERNAME
        ).strip()
        self.id_cotizador = str(
            getattr(quote_context, "id_cotizador", "") or STORE_ID
        ).strip().upper()

        self.productos = df_productos.to_dict("records") if df_productos is not None else []
        self.presentaciones = df_presentaciones.to_dict("records") if df_presentaciones is not None else []
        self._presentation_rel_cache = {}
        self._presentation_product_map_cache = None
        self._presentation_generic_categories_cache = None
        self._presentation_fixed_component_codes_cache = None
        self.items: list[dict] = []
        self._suppress_next_return = False
        self._ignore_completer = False
        self._shown_once = False
        self._app_icon = app_icon
        self._ctx_row = None

        # === Moneda / tasa (DB) ===
        self.base_currency = str(
            getattr(quote_context, "base_currency", "")
            or currency_for_country(self.country_code)
            or APP_CURRENCY
        ).strip().upper()
        scoped_secondary = (
            secondary_currencies_for_country(self.country_code)
            if quote_context is not None
            else get_secondary_currencies()
        )
        self.secondary_currencies = [c.upper() for c in (scoped_secondary or []) if c]
        self.secondary_currency = (
            self.secondary_currencies[0]
            if self.secondary_currencies
            else SECONDARY_CURRENCY
        )
        self.current_currency = self.base_currency
        self.currency_rate = 1.0
        self._rates: dict[str, float] = self._load_exchange_rate_file()  # <- DB

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

        self._use_ai_completer = bool(is_ai_enabled(refresh=True))
        self._recommendations_enabled = bool(is_recommendations_enabled(refresh=True))
        self._build_ui()
        self._restore_window_state()
        self.entry_cliente.textChanged.connect(self._update_title_with_client)
        self._update_title_with_client(self.entry_cliente.text())
        self._build_completer()
        self.set_recommendations_enabled(self._recommendations_enabled)

        self.model.item_added.connect(self._focus_last_row)


        # --- Asistente tipo chat (acciones con confirmación) ---
        self._assistant = None
        if bool(self._use_ai_completer):
            self._attach_inline_assistant()

        # Cada cotización remota escucha solamente su scope. El alias global se
        # conserva para el modo Excel y callers legacy.
        if self._catalog_manager is not None:
            if self.quote_context is not None and bool(
                getattr(self._catalog_manager, "server_mode", False)
            ):
                try:
                    self._catalog_manager.scope_catalog_updated.connect(
                        self._on_scope_catalog_updated
                    )
                    self._catalog_manager.stock_updated.connect(
                        self._on_scope_stock_updated
                    )
                except Exception:
                    pass
            elif self._local_catalog_id is not None and hasattr(
                self._catalog_manager, "local_catalog_updated"
            ):
                try:
                    self._catalog_manager.local_catalog_updated.connect(
                        self._on_local_catalog_updated
                    )
                except Exception:
                    pass
            else:
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

    def _attach_inline_assistant(self):
        if getattr(self, "_assistant", None) is not None:
            return
        try:
            from ..ai.assistant import attach_assistant

            self._assistant = attach_assistant(self)
        except Exception:
            self._assistant = None

    def _detach_inline_assistant(self):
        ctl = getattr(self, "_assistant", None)
        if ctl is None:
            return
        try:
            if hasattr(ctl, "uninstall"):
                ctl.uninstall()
            else:
                dock = getattr(ctl, "dock", None)
                if dock is not None:
                    try:
                        dock.hide()
                    except Exception:
                        pass
                    try:
                        self.removeDockWidget(dock)
                    except Exception:
                        pass
                    try:
                        dock.setParent(None)
                        dock.deleteLater()
                    except Exception:
                        pass
        except Exception:
            pass
        self._assistant = None

    def refresh_ai_features(self):
        ai_on = bool(is_ai_enabled(refresh=True))
        if ai_on != bool(getattr(self, "_use_ai_completer", False)):
            self._use_ai_completer = ai_on
            try:
                self._rebuild_search_completers()
            except Exception:
                pass

        if ai_on:
            self._attach_inline_assistant()
        else:
            self._detach_inline_assistant()

    def _on_rates_updated(self):
        # recargar rates desde DB y refrescar label/tabla
        try:
            self._rates = self._load_exchange_rate_file()
        except Exception:
            self._rates = {}
        current_currency = str(
            getattr(self, "current_currency", self.base_currency) or self.base_currency
        ).strip().upper()
        if current_currency and current_currency != self.base_currency:
            try:
                updated_rate = float((self._rates or {}).get(current_currency) or 0.0)
            except (TypeError, ValueError):
                updated_rate = 0.0
            if updated_rate > 0:
                self._set_currency_context(current_currency, updated_rate)
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
        self._apply_catalog_update(
            df_productos,
            df_presentaciones,
            reprice_items=True,
        )

    def _on_local_catalog_updated(
        self,
        catalog_id: object,
        df_productos: pd.DataFrame,
        df_presentaciones: pd.DataFrame,
    ):
        try:
            matches = int(catalog_id) == int(self._local_catalog_id)
        except (TypeError, ValueError):
            matches = False
        if not matches:
            return
        self._apply_catalog_update(
            df_productos,
            df_presentaciones,
            reprice_items=True,
        )

    def _on_scope_catalog_updated(
        self,
        scope,
        df_productos: pd.DataFrame,
        df_presentaciones: pd.DataFrame,
    ):
        if scope != getattr(self.quote_context, "scope", None):
            return
        self._apply_catalog_update(
            df_productos,
            df_presentaciones,
            reprice_items=False,
        )

    def _on_scope_stock_updated(self, scope):
        if scope != getattr(self.quote_context, "scope", None):
            return
        try:
            df_productos, df_presentaciones = self._catalog_manager.catalog_for_scope(scope)
        except Exception:
            return
        self._apply_catalog_update(
            df_productos,
            df_presentaciones,
            reprice_items=False,
            invalidate_recommender=False,
        )

    def _apply_catalog_update(
        self,
        df_productos: pd.DataFrame,
        df_presentaciones: pd.DataFrame,
        *,
        reprice_items: bool,
        invalidate_recommender: bool = True,
    ):
        try:
            self.productos = df_productos.to_dict("records") if df_productos is not None else []
            self.presentaciones = df_presentaciones.to_dict("records") if df_presentaciones is not None else []
            if invalidate_recommender:
                self._rec_engine = None
            self._presentation_rel_cache = {}
            self._presentation_product_map_cache = None
            self._presentation_generic_categories_cache = None
            self._presentation_fixed_component_codes_cache = None
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

                preserves_history_snapshot = isinstance(
                    it.get("_history_base_snapshot"),
                    dict,
                )

                prod = prod_map.get(codigo)
                if prod is None:
                    prod = pres_map.get(codigo.upper())

                if prod is not None:
                    it["_prod"] = refreshed_product_for_item(
                        prod,
                        it.get("_prod"),
                        preserve_prices=(
                            not reprice_items or preserves_history_snapshot
                        ),
                    )
                    if prod.get("categoria") and not preserves_history_snapshot:
                        it["categoria"] = prod.get("categoria")
                    if "cantidad_disponible" in prod:
                        it["stock_disponible"] = prod.get("cantidad_disponible")

                    if reprice_items and not preserves_history_snapshot:
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

    def _restore_history_display_snapshot(self, payload: dict) -> None:
        base_currency = str(getattr(self, "base_currency", "") or "").strip().upper()
        currency = str(
            (payload or {}).get("currency_shown")
            or (payload or {}).get("base_currency")
            or base_currency
        ).strip().upper()

        try:
            saved_rate = float((payload or {}).get("tasa_shown"))
        except (TypeError, ValueError):
            saved_rate = 0.0
        if currency == base_currency:
            rate = 1.0
        elif saved_rate > 0:
            rate = saved_rate
        else:
            try:
                rate = float((getattr(self, "_rates", None) or {}).get(currency) or 1.0)
            except (TypeError, ValueError):
                rate = 1.0
            if rate <= 0:
                rate = 1.0

        shown_items = (payload or {}).get("items_shown") or []
        self._history_shown_items_snapshot = deepcopy(
            shown_items if isinstance(shown_items, list) else []
        )
        shown_totals = (payload or {}).get("shown_totals") or {}
        self._history_shown_totals_snapshot = deepcopy(
            shown_totals if isinstance(shown_totals, dict) else {}
        )
        self._history_display_snapshot = {
            "currency": currency or base_currency,
            "rate": float(rate),
        }
        self._set_currency_context(currency or base_currency, rate)
        try:
            self._update_currency_label()
        except Exception:
            log.exception("No se pudo actualizar la etiqueta de moneda histórica")

    def load_from_history_payload(self, payload: dict):
        prev_focus_suppressed = bool(getattr(self, "_suppress_focus_last_row", False))
        prev_recs_suppressed = bool(getattr(self, "_suppress_recs_preview_refresh", False))
        self._suppress_focus_last_row = True
        self._suppress_recs_preview_refresh = True
        try:
            self.limpiar_formulario()
            self._restore_history_display_snapshot(payload)
            self._load_from_history_payload_impl(payload)
        finally:
            self._suppress_focus_last_row = prev_focus_suppressed
            self._suppress_recs_preview_refresh = prev_recs_suppressed

        try:
            self._schedule_refresh_recs_preview()
        except Exception:
            pass

    def _load_from_history_payload_impl(self, payload: dict):

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
        if hasattr(self, "_resolve_doc_type_for_form"):
            try:
                doc_type_value = self._resolve_doc_type_for_form(doc_value, doc_type_value)
            except Exception:
                pass
        try:
            if hasattr(self, "_set_selected_doc_type"):
                self._set_selected_doc_type(doc_type_value)
        except Exception:
            pass
        self.entry_cedula.setText(doc_value)
        self.entry_telefono.setText(payload.get("telefono", "") or "")
        if getattr(self, "entry_direccion", None) is not None:
            self.entry_direccion.setText(payload.get("direccion", "") or "")
        if getattr(self, "entry_email", None) is not None:
            self.entry_email.setText(payload.get("email", "") or "")
        if getattr(self, "chk_chatbot", None) is not None:
            self.chk_chatbot.setChecked(bool(payload.get("chatbot", False)))

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

        def _with_snapshot_prices(prod: dict, it_row: dict) -> dict:
            """Mantiene los precios históricos aunque el catálogo cambie después."""
            out = dict(prod or {})
            if "precio" not in it_row:
                return out
            try:
                snapshot_price = float(nz(it_row.get("precio"), 0.0))
            except Exception:
                return out
            try:
                snapshot_price_id = int(nz(it_row.get("id_precioventa"), 1) or 1)
            except Exception:
                snapshot_price_id = 1
            if snapshot_price_id not in (1, 2, 3):
                snapshot_price_id = 1
            out.update(
                {
                    "p_max": snapshot_price,
                    "p_min": snapshot_price,
                    "p_oferta": snapshot_price,
                    "precio_venta": snapshot_price_id,
                }
            )
            return out

        shown_items = getattr(self, "_history_shown_items_snapshot", None) or []
        for item_index, it in enumerate(payload.get("items_base") or []):
            codigo = str(it.get("codigo") or "").strip()
            cat_u_in = str(it.get("categoria") or "").strip().upper()

            prod = prod_map.get(codigo)
            if prod is None:
                prod = pres_map.get(codigo.upper())
            if prod is None and cat_u_in == "PRESENTACION":
                prod = _build_presentation_combo_prod(codigo, it)
            if prod is None:
                prod = _build_fallback_prod_for_item(it)
            prod = _with_snapshot_prices(prod or {}, it)

            item = dict(it)
            item["_prod"] = prod or {}
            if item_index < len(shown_items) and isinstance(shown_items[item_index], dict):
                item["_history_shown_snapshot"] = deepcopy(shown_items[item_index])
                item["_history_display_snapshot"] = deepcopy(
                    getattr(self, "_history_display_snapshot", None) or {}
                )

            # La categoria del renglon sigue siendo la guardada en el historico.

            # ✅ refrescar categoría actual (si existe)
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
            self.model.add_item(item, preserve_snapshot=True)
            item["_history_base_snapshot"] = history_base_snapshot(item)

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
