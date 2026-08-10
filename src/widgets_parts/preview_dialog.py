# src/widgets_parts/preview_dialog.py
from __future__ import annotations

import math

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QBrush
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLabel,
    QTableWidget,
    QHeaderView,
    QAbstractItemView,
    QTableWidgetItem,
    QPushButton,
    QWidget,
)

from ..config import APP_COUNTRY, convert_from_base, id_label_for_country
from ..pricing import cantidad_para_mostrar, factor_total_por_categoria, quantity_in_grams
from ..product_rules import uses_gram_quantity
from ..stock_policy import has_insufficient_stock
from ..utils import fmt_money_ui, nz
from .bounded_table_columns import install_bounded_columns


def _fmt_qty(x: float) -> str:
    """Formatea cantidades: si es entero, sin decimales; si no, con decimales limpios."""
    try:
        if math.isfinite(x) and math.isclose(x, round(x), abs_tol=1e-9):
            return str(int(round(x)))
    except Exception:
        pass
    return f"{x:.3f}".rstrip("0").rstrip(".")


def _esencia_a_gramos(it: dict, cant: float, *, country: str | None = None) -> float:
    """Convierte la cantidad interna del producto a gramos para el país activo."""
    return quantity_in_grams(it, cant, country=country or APP_COUNTRY)


def show_preview_dialog(
    parent: QWidget,
    app_icon: QIcon,
    cliente: str,
    cedula: str,
    telefono: str,
    items: list[dict],
    *,
    country: str | None = None,
    converter=None,
    current_currency: str | None = None,
    quote_context=None,
    amounts_are_shown: bool = False,
    shown_totals: dict | None = None,
) -> None:
    """Diálogo de previsualización de cotización (solo lectura)."""
    dlg = QDialog(parent)
    dlg.setWindowTitle("Previsualización de Cotización")
    dlg.resize(860, 520)
    if not app_icon.isNull():
        parent.setWindowIcon(app_icon)
        dlg.setWindowIcon(app_icon)

    v = QVBoxLayout(dlg)
    scope = getattr(quote_context, "scope", None)
    country_name = str(
        country or getattr(scope, "country_code", "") or APP_COUNTRY
    )
    current_currency = str(
        current_currency
        or getattr(quote_context, "base_currency", "")
        or ""
    ).strip().upper() or None
    convert = converter if callable(converter) else convert_from_base
    id_lbl = id_label_for_country(country_name)
    v.addWidget(QLabel(f"<b>Nombre:</b> {cliente}"))
    v.addWidget(QLabel(f"<b>{id_lbl}:</b> {cedula}"))
    v.addWidget(QLabel(f"<b>Teléfono:</b> {telefono}"))

    tbl = QTableWidget(0, 6)
    tbl.setHorizontalHeaderLabels(
        ["Código", "Producto", "Cantidad", "Precio", "Descuento", "Subtotal"]
    )
    tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    install_bounded_columns(tbl, fill_column=1)
    tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
    tbl.setSelectionMode(QAbstractItemView.NoSelection)
    tbl.setAlternatingRowColors(True)
    tbl.setShowGrid(False)
    tbl.verticalHeader().setVisible(True)

    subtotal_bruto_base = 0.0
    descuento_total_base = 0.0
    total_neto_base = 0.0

    # Totales extra
    total_botellas = 0.0
    total_esencias_g = 0.0

    for it in items:
        r = tbl.rowCount()
        tbl.insertRow(r)

        prod = it.get("producto", "")
        if it.get("fragancia"):
            prod += f" ({it['fragancia']})"
        if it.get("observacion"):
            prod += f" | {it['observacion']}"

        qty_txt = cantidad_para_mostrar(it, country=country_name)

        precio_base = float(nz(it.get("precio"), 0.0))
        total_line_base = float(nz(it.get("total"), 0.0))
        subtotal_line_base = float(
            nz(it.get("subtotal_base"), precio_base * nz(it.get("cantidad"), 0.0))
        )
        d_monto_base = float(nz(it.get("descuento_monto"), 0.0))
        d_pct = float(nz(it.get("descuento_pct"), 0.0))

        if amounts_are_shown:
            precio_display = precio_base
            subtotal_display = float(
                nz(it.get("subtotal"), precio_base * nz(it.get("cantidad"), 0.0))
            )
            descuento_display = float(nz(it.get("descuento"), 0.0))
            total_display = total_line_base
        else:
            precio_display = float(convert(precio_base))
            subtotal_display = float(convert(subtotal_line_base))
            descuento_display = float(convert(d_monto_base))
            total_display = float(convert(total_line_base))

        subtotal_bruto_base += subtotal_display
        descuento_total_base += descuento_display
        total_neto_base += total_display

        precio_ui = fmt_money_ui(precio_display, currency=current_currency)
        subtotal_ui = fmt_money_ui(total_display, currency=current_currency)

        if d_pct > 0:
            desc_txt = f"-{d_pct:.1f}%"
        elif descuento_display > 0:
            desc_txt = "-" + fmt_money_ui(
                descuento_display,
                currency=current_currency,
            )
        else:
            desc_txt = "—"

        vals = [it.get("codigo", ""), prod, qty_txt, precio_ui, desc_txt, subtotal_ui]
        for col, val in enumerate(vals):
            tbl.setItem(r, col, QTableWidgetItem(str(val)))

        # Categoría / cantidades para labels
        try:
            cat_u = (it.get("categoria") or "").upper()
            cant = float(nz(it.get("cantidad"), 0.0))

            if cat_u == "BOTELLAS":
                total_botellas += cant

            if uses_gram_quantity(it, country=country_name):
                total_esencias_g += _esencia_a_gramos(it, cant, country=country_name)
        except Exception:
            pass

        # Chequeo de stock visual
        try:
            cat_u = (it.get("categoria") or "").upper()
            disp = float(nz(it.get("stock_disponible"), 0.0))
            cant = float(nz(it.get("cantidad"), 0.0))
            mult = factor_total_por_categoria(cat_u, it, country=country_name)
            if country_name == "VENEZUELA" and uses_gram_quantity(it, country=country_name):
                mult = 50.0
            if has_insufficient_stock(quantity=cant, available=disp, factor=mult):
                qty_item = tbl.item(r, 2)
                if qty_item:
                    qty_item.setForeground(QBrush(Qt.red))
        except Exception:
            pass

    v.addWidget(tbl)

    # Labels adicionales (solo si aplica)
    if total_botellas > 0:
        v.addWidget(QLabel(f"<b>Total de Botellas:</b> {_fmt_qty(total_botellas)}"))
    if total_esencias_g > 0:
        v.addWidget(QLabel(f"<b>Total de Esencias:</b> {_fmt_qty(total_esencias_g)} g"))

    totals = shown_totals if isinstance(shown_totals, dict) else {}
    subtotal_bruto_display = float(
        nz(totals.get("subtotal_bruto"), subtotal_bruto_base)
    )
    descuento_total_display = float(
        nz(totals.get("descuento_total"), descuento_total_base)
    )
    total_neto_display = float(nz(totals.get("total_general"), total_neto_base))

    v.addWidget(
        QLabel(
            f"<b>Subtotal sin descuento:</b> "
            f"{fmt_money_ui(subtotal_bruto_display, currency=current_currency)}"
        )
    )
    v.addWidget(
        QLabel(
            f"<b>Descuento total:</b> "
            f"-{fmt_money_ui(descuento_total_display, currency=current_currency)}"
        )
    )
    v.addWidget(
        QLabel(
            f"<b>Total General:</b> "
            f"{fmt_money_ui(total_neto_display, currency=current_currency)}"
        )
    )

    btn = QPushButton("Cerrar")
    btn.setProperty("variant", "primary")
    btn.clicked.connect(dlg.accept)
    v.addWidget(btn)
    dlg.exec()
