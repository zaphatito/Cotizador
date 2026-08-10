# src/app_window_parts/pdf_actions.py
from __future__ import annotations

import os
import datetime
from copy import deepcopy

from PySide6.QtWidgets import QMessageBox
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl, QTimer

from ..paths import COTIZACIONES_DIR, resolve_country_asset
from ..config import APP_COUNTRY, COUNTRY_CODE, STORE_ID
from ..country_rules import uses_peru_business_rules
from ..stock_policy import has_insufficient_stock, stock_enforcement_enabled
from ..utils import nz
from ..pdfgen import generar_pdf
from ..logging_setup import get_logger
from ..api.presupuesto_client import allocate_quote_code_for_new_quote
from ..widgets import show_preview_dialog, ListadoProductosDialog

from ..db_path import resolve_db_path

from sqlModels.db import connect, ensure_schema, tx
from sqlModels.quotes_repo import insert_quote

from .ticket_actions import generar_ticket_para_cotizacion
from .history_snapshot import (
    history_base_snapshot,
    matching_history_shown_snapshot,
)

log = get_logger(__name__)


class PdfActionsMixin:
    def _confirm_quote_stock(self) -> bool:
        shortages = []
        for item in self.items:
            factor = float(nz(item.get("factor_total"), 1.0))
            if has_insufficient_stock(
                quantity=item.get("cantidad", 0.0),
                available=item.get("stock_disponible", -1.0),
                factor=factor,
            ):
                shortages.append(item)

        if not shortages:
            return True

        if stock_enforcement_enabled(getattr(self, "quote_context", None)):
            QMessageBox.warning(
                self,
                "Stock insuficiente",
                "❌ Hay productos cuya cantidad supera el stock disponible. "
                "Ajusta las cantidades antes de generar la cotización.",
            )
            return False

        count = len(shortages)
        detail = "un producto" if count == 1 else f"{count} productos"
        return QMessageBox.question(
            self,
            "Confirmar cotización sin stock",
            f"⚠️ La cotización contiene {detail} con stock insuficiente. "
            "¿Deseas generar la cotización igualmente?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        ) == QMessageBox.Yes

    def abrir_manual(self):
        country_code = getattr(self, "country_code", COUNTRY_CODE)
        ruta = resolve_country_asset("manual_usuario_sistema.pdf", country_code)
        if not ruta or not os.path.exists(ruta):
            QMessageBox.warning(
                self,
                "Manual no encontrado",
                "No se encontró 'manual_usuario_sistema.pdf' en 'templates/<PAIS>/' "
                "ni en 'templates/'.\n"
                "Coloca el manual en 'templates/{COUNTRY_CODE}/' o en 'templates/' "
                "e inténtalo de nuevo.",
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(ruta)))

    def abrir_listado_productos(self):
        current_currency, _secondary, _rate = self._currency_context()
        stock_matrix = None
        catalog_manager = getattr(self, "_catalog_manager", None)
        quote_context = getattr(self, "quote_context", None)
        if bool(getattr(catalog_manager, "server_mode", False)):
            scope = getattr(quote_context, "scope", None)
            if scope is not None:
                stock_matrix = catalog_manager.stock_matrix(scope)
        dlg = ListadoProductosDialog(
            self,
            self.productos,
            self.presentaciones,
            self._agregar_por_codigo,
            app_icon=self._app_icon,
            converter=self._convert_from_base,
            current_currency=current_currency,
            quote_context=quote_context,
            stock_matrix=stock_matrix,
        )
        main_geo = self.frameGeometry()
        main_center = main_geo.center()
        dlg_size = dlg.sizeHint()
        x = main_center.x()
        y = main_center.y() - dlg_size.height()
        dlg.move(x, y)
        dlg.exec()

    def previsualizar_datos(self):
        c = self.entry_cliente.text()
        ci = self.entry_cedula.text()
        t = self.entry_telefono.text()
        d = self.entry_direccion.text()
        e = self.entry_email.text()
        items = self.items
        if not all([c, ci, t, d, e]):
            QMessageBox.warning(self, "Advertencia", "❌ Faltan datos del cliente")
            return
        ok_doc, msg_doc, _tipo_doc = self._validate_doc_phone_values(ci, t, direccion=d, email=e)
        if not ok_doc:
            QMessageBox.warning(self, "Advertencia", msg_doc)
            return
        total_items = sum(nz(i.get("total")) for i in items) if items else 0.0
        if not items or total_items <= 0.0:
            QMessageBox.warning(self, "Advertencia", "❌ Faltan productos en la cotización")
            return

        current_currency, _secondary, _rate = self._currency_context()
        item_builder = getattr(self, "_build_items_for_pdf", None)
        items_shown = (
            item_builder()
            if callable(item_builder)
            else PdfActionsMixin._build_items_for_pdf(self)
        )
        totals_builder = getattr(self, "_history_shown_totals_if_current", None)
        shown_totals = (
            totals_builder()
            if callable(totals_builder)
            else PdfActionsMixin._history_shown_totals_if_current(self)
        )
        show_preview_dialog(
            self,
            self._app_icon,
            c,
            ci,
            t,
            items_shown,
            country=getattr(self, "country_name", APP_COUNTRY),
            converter=self._convert_from_base,
            current_currency=current_currency,
            quote_context=getattr(self, "quote_context", None),
            amounts_are_shown=True,
            shown_totals=shown_totals,
        )

    def _build_items_for_pdf(self) -> list[dict]:
        cloned = deepcopy(self.items)
        current_currency, _secondary, current_rate = self._currency_context()
        display_snapshot = getattr(self, "_history_display_snapshot", None) or {}

        for it in cloned:
            shown_snapshot = matching_history_shown_snapshot(
                it,
                currency=current_currency,
                rate=current_rate,
                display_snapshot=display_snapshot,
            )

            price_base = float(nz(it.get("precio"), 0.0))
            total_base = float(nz(it.get("total"), 0.0))
            subtotal_base = float(nz(it.get("subtotal_base"), price_base * nz(it.get("cantidad"), 0.0)))
            d_monto_base = float(nz(it.get("descuento_monto"), 0.0))

            if shown_snapshot is not None:
                it["precio"] = (
                    shown_snapshot["precio"]
                    if "precio" in shown_snapshot
                    else self._convert_from_base(price_base)
                )
                it["total"] = (
                    shown_snapshot["total"]
                    if "total" in shown_snapshot
                    else self._convert_from_base(total_base)
                )
                it["subtotal"] = (
                    shown_snapshot["subtotal"]
                    if "subtotal" in shown_snapshot
                    else self._convert_from_base(subtotal_base)
                )
                it["descuento"] = (
                    shown_snapshot["descuento"]
                    if "descuento" in shown_snapshot
                    else self._convert_from_base(d_monto_base)
                )
            else:
                it["precio"] = self._convert_from_base(price_base)
                it["total"] = self._convert_from_base(total_base)
                it["subtotal"] = self._convert_from_base(subtotal_base)
                it["descuento"] = self._convert_from_base(d_monto_base)

            it.pop("_history_shown_snapshot", None)
            it.pop("_history_base_snapshot", None)
            it.pop("_history_display_snapshot", None)
        return cloned

    def _history_shown_totals_if_current(self) -> dict | None:
        totals = getattr(self, "_history_shown_totals_snapshot", None)
        historical_items = getattr(self, "_history_shown_items_snapshot", None)
        display_snapshot = getattr(self, "_history_display_snapshot", None)
        if not isinstance(totals, dict) or not isinstance(historical_items, list):
            return None
        if not {"subtotal_bruto", "descuento_total", "total_general"}.issubset(totals):
            return None
        if not isinstance(display_snapshot, dict) or len(self.items) != len(historical_items):
            return None

        current_currency, _secondary, current_rate = self._currency_context()
        for item in self.items:
            if matching_history_shown_snapshot(
                item,
                currency=current_currency,
                rate=current_rate,
                display_snapshot=display_snapshot,
            ) is None:
                return None
        return deepcopy(totals)

    def _shown_totals_for_output(self, items_shown: list[dict]) -> dict[str, float]:
        """Mantiene la cabecera alineada con los renglones realmente mostrados."""
        historical_totals = self._history_shown_totals_if_current()
        if historical_totals is not None:
            return {
                "subtotal_bruto": float(
                    nz(historical_totals.get("subtotal_bruto"), 0.0)
                ),
                "descuento_total": float(
                    nz(historical_totals.get("descuento_total"), 0.0)
                ),
                "total_general": float(
                    nz(historical_totals.get("total_general"), 0.0)
                ),
            }

        return {
            "subtotal_bruto": sum(
                float(nz(item.get("subtotal"), 0.0)) for item in items_shown
            ),
            "descuento_total": sum(
                float(nz(item.get("descuento"), 0.0)) for item in items_shown
            ),
            "total_general": sum(
                float(nz(item.get("total"), 0.0)) for item in items_shown
            ),
        }

    def _get_metodo_pago_actual(self) -> str:
        """
        Paraguay: Tarjeta/Efectivo (toggle)
        Perú/Bolivia: texto libre (puede ser vacío)
        Otros países: "Transferencia" (solo para PDF)
        """
        country_name = getattr(self, "country_name", APP_COUNTRY)
        if country_name == "PARAGUAY":
            is_cash = bool(getattr(self, "_py_cash_mode", False))
            return "Efectivo" if is_cash else "Tarjeta"

        if uses_peru_business_rules(country_name):
            try:
                return (getattr(self, "entry_metodo_pago").text() or "").strip()
            except Exception:
                return ""

        return "Transferencia"

    def _focus_history_after_close(self):
        """Cierra esta ventana y devuelve foco al histórico (si existe)."""
        hist = getattr(self, "_history_window", None)
        if hist is None:
            return

        def _bring_front(h=hist):
            try:
                h.showNormal()
            except Exception:
                pass
            try:
                h.raise_()
                h.activateWindow()
            except Exception:
                pass
            try:
                tbl = getattr(h, "table", None)
                if tbl is not None:
                    tbl.setFocus()
                else:
                    h.setFocus()
            except Exception:
                pass

        # Pequeña demora para que Explorer/diálogos no se queden con el foco
        QTimer.singleShot(200, _bring_front)

    def generar_cotizacion(self):
        c = self.entry_cliente.text()
        ci = self.entry_cedula.text()
        t = self.entry_telefono.text()
        d = self.entry_direccion.text()
        e = self.entry_email.text()
        if not all([c, ci, t, d, e]):
            QMessageBox.warning(self, "Advertencia", "❌ Faltan datos del cliente")
            return
        ok_doc, msg_doc, tipo_doc = self._validate_doc_phone_values(ci, t, direccion=d, email=e)
        if not ok_doc:
            QMessageBox.warning(self, "Advertencia", msg_doc)
            return

        total_items = sum(nz(i.get("total")) for i in self.items) if self.items else 0.0
        if not self.items or total_items <= 0:
            QMessageBox.warning(self, "Advertencia", "❌ Agrega al menos un producto a la cotización")
            return

        if not self._confirm_quote_stock():
            return

        # ===== Totales BASE =====
        subtotal_bruto_base = 0.0
        descuento_total_base = 0.0
        total_neto_base = 0.0

        for it in self.items:
            precio_base = float(nz(it.get("precio"), 0.0))
            subtotal_line_base = float(nz(it.get("subtotal_base"), precio_base * nz(it.get("cantidad"), 0.0)))
            d_monto_base = float(nz(it.get("descuento_monto"), 0.0))
            total_line_base = float(nz(it.get("total"), subtotal_line_base - d_monto_base))

            subtotal_bruto_base += subtotal_line_base
            descuento_total_base += d_monto_base
            total_neto_base += total_line_base

        items_pdf = self._build_items_for_pdf()

        shown_totals = self._shown_totals_for_output(items_pdf)
        subtotal_bruto_shown = shown_totals["subtotal_bruto"]
        descuento_total_shown = shown_totals["descuento_total"]
        total_neto_shown = shown_totals["total_general"]

        metodo_pago_pdf = self._get_metodo_pago_actual()

        # Paraguay y los países con reglas de Perú guardan el método de pago.
        metodo_pago_db = (
            metodo_pago_pdf
            if getattr(self, "country_name", APP_COUNTRY) == "PARAGUAY"
            or uses_peru_business_rules(getattr(self, "country_name", APP_COUNTRY))
            else ""
        )

        emission_dt = datetime.datetime.now()

        datos = {
            "fecha": emission_dt,
            "cliente": c,
            "cedula": ci,
            "tipo_documento": tipo_doc,
            "telefono": t,
            "direccion": d,
            "email": e,
            "metodo_pago": metodo_pago_pdf,
            "items": items_pdf,
            "subtotal_bruto": subtotal_bruto_shown,
            "descuento_total": descuento_total_shown,
            "total_general": total_neto_shown,
        }

        db_warn = ""
        offline_warn = ""
        saved_ok = False

        try:
            db_path = resolve_db_path()
            con = connect(db_path)
            ensure_schema(con)

            created_at = emission_dt.isoformat(timespec="seconds")
            curr, _sec, rate = self._currency_context()
            quote_allocation = allocate_quote_code_for_new_quote(
                con,
                country_code=getattr(self, "country_code", COUNTRY_CODE),
                store_id=getattr(self, "id_cotizador", STORE_ID),
                context=getattr(self, "quote_context", None),
            )
            quote_code = str(quote_allocation.get("quote_code") or "").strip().upper()
            quote_no_status = str(
                quote_allocation.get("quote_no_status") or "confirmed"
            ).strip().lower()
            if not quote_code:
                raise RuntimeError("No se pudo asignar un correlativo local valido.")
            if quote_no_status == "provisional":
                offline_warn = (
                    "\n\nSin conexion con el servidor: el correlativo es provisional. "
                    "Se reservara uno nuevo y se actualizaran el historico, el PDF y "
                    "el ticket automaticamente al recuperar la conexion."
                )

            ruta = generar_pdf(
                datos,
                fixed_quote_no=quote_code,
                country_code=getattr(self, "country_code", COUNTRY_CODE),
                store_id=getattr(self, "id_cotizador", STORE_ID),
                company_type=getattr(self, "company_type", ""),
                currency_code=curr,
            )
            log.info("PDF generado en %s", ruta)
            pdf_store = os.path.basename(ruta)
            try:
                with tx(con):
                    insert_quote(
                        con,
                        country_code=getattr(self, "country_code", COUNTRY_CODE),
                        company_type=getattr(self, "company_type", ""),
                        base_currency=getattr(self, "base_currency", ""),
                        cotizador_username=getattr(self, "cotizador_username", ""),
                        id_cotizador=getattr(self, "id_cotizador", STORE_ID),
                        quote_no=quote_code,
                        quote_no_status=quote_no_status,
                        created_at=created_at,
                        cliente=c,
                        cedula=ci,
                        telefono=t,
                        direccion=d,
                        email=e,
                        tipo_documento=tipo_doc,
                        metodo_pago=metodo_pago_db,
                        currency_shown=str(curr or ""),
                        tasa_shown=float(rate) if rate is not None else None,
                        subtotal_bruto_base=float(subtotal_bruto_base),
                        descuento_total_base=float(descuento_total_base),
                        total_neto_base=float(total_neto_base),
                        subtotal_bruto_shown=float(subtotal_bruto_shown),
                        descuento_total_shown=float(descuento_total_shown),
                        total_neto_shown=float(total_neto_shown),
                        pdf_path=pdf_store,
                        items_base=self.items,
                        items_shown=items_pdf,
                    )
                saved_ok = True
            except Exception as e:
                log.exception("No se pudo guardar la cotización en SQLite")
                db_warn = f"\n\n⚠️ No se pudo guardar en histórico:\n{e}"
                saved_ok = False

            if saved_ok:
                qe = getattr(self, "_quote_events", None)
                if qe is not None:
                    try:
                        qe.quote_saved.emit()
                    except Exception:
                        pass

            con.close()

            ticket_paths = generar_ticket_para_cotizacion(
                pdf_path=ruta,
                items_pdf=datos["items"],
                quote_code=quote_code,
                country=getattr(self, "country_name", APP_COUNTRY),
                store_id=getattr(self, "id_cotizador", STORE_ID),
                company_type=getattr(self, "company_type", ""),
                context=getattr(self, "quote_context", None),
                cliente_nombre=c,
                printer_name="TICKERA",
                width=48,
                top_mm=0.0,
                bottom_mm=10.0,
                cut_mode="full_feed",
            )

            msg = f"📄 Cotización generada:\n{ruta}{offline_warn}{db_warn}"

            if ticket_paths.get("ticket_cmd"):
                msg += (
                    "\n\n🧾 Ticket listo."
                    "\nSe creó un archivo para imprimir (doble click) en:"
                    f"\n{ticket_paths['ticket_cmd']}"
                    "\n\n(Se guarda en: cotizaciones/tickets/)"
                )

            QMessageBox.information(self, "Cotización Generada", msg)
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(COTIZACIONES_DIR)))

            # ✅ cerrar ventana y devolver foco al histórico
            self._focus_history_after_close()
            self.close()

        except Exception as e:
            log.exception("Error al generar PDF")
            QMessageBox.critical(
                self,
                "Error al generar PDF",
                (
                    "❌ No se pudo generar la cotización.\n\n"
                    "Si el servidor no esta disponible, la app usa un correlativo "
                    "provisional y lo sincroniza al recuperar la conexion.\n\n"
                    f"Detalle:\n{e}"
                ),
            )

    def limpiar_formulario(self):
        self.entry_cliente.clear()
        self.entry_cedula.clear()
        self.entry_telefono.clear()
        if getattr(self, "entry_direccion", None) is not None:
            self.entry_direccion.clear()
        if getattr(self, "entry_email", None) is not None:
            self.entry_email.clear()
        try:
            if getattr(self, "combo_tipo_documento", None) is not None:
                self.combo_tipo_documento.setCurrentIndex(0)
        except Exception:
            pass
        self.entry_producto.clear()
        self.model.remove_rows(list(range(len(self.items))))
        log.info("Formulario limpiado")
