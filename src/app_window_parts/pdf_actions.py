# src/app_window_parts/pdf_actions.py
from __future__ import annotations

import os
import datetime
from copy import deepcopy

from PySide6.QtWidgets import QMessageBox, QDialog
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl

from ..paths import COTIZACIONES_DIR, resolve_country_asset
from ..config import APP_COUNTRY, COUNTRY_CODE, convert_from_base, get_currency_context
from ..utils import nz
from ..pdfgen import generar_pdf
from ..logging_setup import get_logger
from ..widgets import show_preview_dialog, ListadoProductosDialog

from ..db_path import resolve_db_path

from sqlModels.db import connect, ensure_schema, tx
from sqlModels.quotes_repo import insert_quote
from sqlModels.sequences_repo import next_quote_no

from .ticket_actions import generar_ticket_para_cotizacion

log = get_logger(__name__)


class PdfActionsMixin:
    def abrir_manual(self):
        ruta = resolve_country_asset("manual_usuario_sistema.pdf", COUNTRY_CODE)
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
        dlg = ListadoProductosDialog(
            self,
            self.productos,
            self.presentaciones,
            self._agregar_por_codigo,
            app_icon=self._app_icon,
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
        items = self.items
        if not all([c, ci, t]):
            QMessageBox.warning(self, "Advertencia", "❌ Faltan datos del cliente")
            return
        total_items = sum(nz(i.get("total")) for i in items) if items else 0.0
        if not items or total_items <= 0.0:
            QMessageBox.warning(self, "Advertencia", "❌ Faltan productos en la cotización")
            return

        show_preview_dialog(self, self._app_icon, c, ci, t, items)

    def _build_items_for_pdf(self) -> list[dict]:
        cloned = deepcopy(self.items)
        for it in cloned:
            price_base = float(nz(it.get("precio"), 0.0))
            total_base = float(nz(it.get("total"), 0.0))
            subtotal_base = float(nz(it.get("subtotal_base"), price_base * nz(it.get("cantidad"), 0.0)))
            d_monto_base = float(nz(it.get("descuento_monto"), 0.0))

            it["precio"] = convert_from_base(price_base)
            it["total"] = convert_from_base(total_base)
            it["subtotal"] = convert_from_base(subtotal_base)
            it["descuento"] = convert_from_base(d_monto_base)
        return cloned

    def _get_metodo_pago_actual(self) -> str:
        """
        Paraguay: Tarjeta/Efectivo (toggle)
        Perú: texto libre (puede ser vacío)
        Otros países: "Transferencia" (solo para PDF)
        """
        if APP_COUNTRY == "PARAGUAY":
            is_cash = bool(getattr(self, "_py_cash_mode", False))
            return "Efectivo" if is_cash else "Tarjeta"

        if APP_COUNTRY == "PERU":
            try:
                return (getattr(self, "entry_metodo_pago").text() or "").strip()
            except Exception:
                return ""

        return "Transferencia"

    def generar_cotizacion(self):
        c = self.entry_cliente.text()
        ci = self.entry_cedula.text()
        t = self.entry_telefono.text()
        if not all([c, ci, t]):
            QMessageBox.warning(self, "Advertencia", "❌ Faltan datos del cliente")
            return

        total_items = sum(nz(i.get("total")) for i in self.items) if self.items else 0.0
        if not self.items or total_items <= 0:
            QMessageBox.warning(self, "Advertencia", "❌ Agrega al menos un producto a la cotización")
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

        subtotal_bruto_shown = convert_from_base(subtotal_bruto_base)
        descuento_total_shown = convert_from_base(descuento_total_base)
        total_neto_shown = convert_from_base(total_neto_base)

        metodo_pago_pdf = self._get_metodo_pago_actual()

        # ✅ BD: solo Paraguay y Perú guardan metodo_pago (Perú puede ser vacío)
        metodo_pago_db = metodo_pago_pdf if APP_COUNTRY in ("PARAGUAY", "PERU") else ""

        datos = {
            "fecha": datetime.datetime.now().strftime("%d/%m/%Y"),
            "cliente": c,
            "cedula": ci,
            "telefono": t,
            "metodo_pago": metodo_pago_pdf,
            "items": items_pdf,
            "subtotal_bruto": subtotal_bruto_shown,
            "descuento_total": descuento_total_shown,
            "total_general": total_neto_shown,
        }

        db_warn = ""
        saved_ok = False

        try:
            db_path = resolve_db_path()
            con = connect(db_path)
            ensure_schema(con)

            created_at = datetime.datetime.now().isoformat(timespec="seconds")
            curr, _sec, rate = get_currency_context()

            with tx(con):
                quote_no = next_quote_no(con, COUNTRY_CODE, width=7)

            ruta = generar_pdf(datos, fixed_quote_no=quote_no)
            log.info("PDF generado en %s", ruta)

            try:
                with tx(con):
                    insert_quote(
                        con,
                        country_code=COUNTRY_CODE,
                        quote_no=str(quote_no).zfill(7),
                        created_at=created_at,
                        cliente=c,
                        cedula=ci,
                        telefono=t,
                        metodo_pago=metodo_pago_db,
                        currency_shown=str(curr or ""),
                        tasa_shown=float(rate) if rate is not None else None,
                        subtotal_bruto_base=float(subtotal_bruto_base),
                        descuento_total_base=float(descuento_total_base),
                        total_neto_base=float(total_neto_base),
                        subtotal_bruto_shown=float(subtotal_bruto_shown),
                        descuento_total_shown=float(descuento_total_shown),
                        total_neto_shown=float(total_neto_shown),
                        pdf_path=ruta,
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
                cliente_nombre=c,
                printer_name="TICKERA",
                width=48,
                top_mm=0.0,
                bottom_mm=10.0,
                cut_mode="full_feed",
            )

            msg = f"📄 Cotización generada:\n{ruta}{db_warn}"

            if ticket_paths.get("ticket_cmd"):
                msg += (
                    "\n\n🧾 Ticket listo."
                    "\nSe creó un archivo para imprimir (doble click) en:"
                    f"\n{ticket_paths['ticket_cmd']}"
                    "\n\n(Se guarda en: cotizaciones/tickets/)"
                )

            QMessageBox.information(self, "Cotización Generada", msg)
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(COTIZACIONES_DIR)))

        except Exception as e:
            log.exception("Error al generar PDF")
            QMessageBox.critical(self, "Error al generar PDF", f"❌ No se pudo generar la cotización:\n{e}")

    def limpiar_formulario(self):
        self.entry_cliente.clear()
        self.entry_cedula.clear()
        self.entry_telefono.clear()
        self.entry_producto.clear()
        self.model.remove_rows(list(range(len(self.items))))
        log.info("Formulario limpiado")
