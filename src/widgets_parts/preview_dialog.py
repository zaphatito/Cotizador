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

from ..config import APP_COUNTRY, CATS, convert_from_base, id_label_for_country
from ..pricing import cantidad_para_mostrar
from ..utils import fmt_money_ui, nz


def _fmt_qty(x: float) -> str:
    """Formatea cantidades: si es entero, sin decimales; si no, con decimales limpios."""
    try:
        if math.isfinite(x) and math.isclose(x, round(x), abs_tol=1e-9):
            return str(int(round(x)))
    except Exception:
        pass
    return f"{x:.3f}".rstrip("0").rstrip(".")


def _esencia_a_gramos(cant: float) -> float:
    """
    Convierte 'cantidad' a gramos para categorías en CATS,
    consistente con cómo se suele mostrar en UI:

    - VE/PY: 1 unidad = 50 g
    - Otros (ej. PERÚ): cantidad está en KG => gramos = KG * 1000
    """
    if APP_COUNTRY in ("VENEZUELA", "PARAGUAY"):
        return cant * 50.0
    return cant * 1000.0


def show_preview_dialog(
    parent: QWidget,
    app_icon: QIcon,
    cliente: str,
    cedula: str,
    telefono: str,
    items: list[dict],
) -> None:
    """Diálogo de previsualización de cotización (solo lectura)."""
    dlg = QDialog(parent)
    dlg.setWindowTitle("Previsualización de Cotización")
    dlg.resize(860, 520)
    if not app_icon.isNull():
        parent.setWindowIcon(app_icon)
        dlg.setWindowIcon(app_icon)

    v = QVBoxLayout(dlg)
    id_lbl = id_label_for_country(APP_COUNTRY)
    v.addWidget(QLabel(f"<b>Nombre:</b> {cliente}"))
    v.addWidget(QLabel(f"<b>{id_lbl}:</b> {cedula}"))
    v.addWidget(QLabel(f"<b>Teléfono:</b> {telefono}"))

    tbl = QTableWidget(0, 6)
    tbl.setHorizontalHeaderLabels(
        ["Código", "Producto", "Cantidad", "Precio", "Descuento", "Subtotal"]
    )
    tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
    tbl.setSelectionMode(QAbstractItemView.NoSelection)

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

        qty_txt = cantidad_para_mostrar(it)

        precio_base = float(nz(it.get("precio"), 0.0))
        total_line_base = float(nz(it.get("total"), 0.0))
        subtotal_line_base = float(
            nz(it.get("subtotal_base"), precio_base * nz(it.get("cantidad"), 0.0))
        )
        d_monto_base = float(nz(it.get("descuento_monto"), 0.0))
        d_pct = float(nz(it.get("descuento_pct"), 0.0))

        subtotal_bruto_base += subtotal_line_base
        descuento_total_base += d_monto_base
        total_neto_base += total_line_base

        precio_ui = fmt_money_ui(convert_from_base(precio_base))
        subtotal_ui = fmt_money_ui(convert_from_base(total_line_base))

        if d_pct > 0:
            desc_txt = f"-{d_pct:.1f}%"
        elif d_monto_base > 0:
            desc_txt = "-" + fmt_money_ui(convert_from_base(d_monto_base))
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

            if cat_u in CATS:
                total_esencias_g += _esencia_a_gramos(cant)
        except Exception:
            pass

        # Chequeo de stock visual
        try:
            cat_u = (it.get("categoria") or "").upper()
            disp = float(nz(it.get("stock_disponible"), 0.0))
            cant = float(nz(it.get("cantidad"), 0.0))
            mult = 50.0 if (APP_COUNTRY in ("VENEZUELA", "PARAGUAY") and cat_u in CATS) else 1.0
            if cant * mult > disp and disp >= 0.0:
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

    v.addWidget(
        QLabel(
            f"<b>Subtotal sin descuento:</b> {fmt_money_ui(convert_from_base(subtotal_bruto_base))}"
        )
    )
    v.addWidget(
        QLabel(
            f"<b>Descuento total:</b> -{fmt_money_ui(convert_from_base(descuento_total_base))}"
        )
    )
    v.addWidget(
        QLabel(f"<b>Total General:</b> {fmt_money_ui(convert_from_base(total_neto_base))}")
    )

    btn = QPushButton("Cerrar")
    btn.clicked.connect(dlg.accept)
    v.addWidget(btn)
    dlg.exec()
