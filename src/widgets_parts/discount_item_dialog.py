# src/widgets_parts/discount_item_dialog.py
from __future__ import annotations

from typing import Optional

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLabel,
    QGroupBox,
    QFormLayout,
    QDoubleSpinBox,
    QDialogButtonBox,
    QMessageBox,
    QWidget,
)

from ..config import convert_from_base, get_currency_context
from ..utils import fmt_money_ui, nz


def show_discount_dialog_for_item(
    parent: QWidget,
    app_icon: QIcon,
    item: dict,
    base_currency: str,
) -> Optional[dict]:
    """
    Diálogo para editar el descuento de una fila.

    Devuelve un payload listo para setData en la columna Descuento:
      {"mode": "clear"} |
      {"mode": "percent", "percent": pct} |
      {"mode": "amount", "amount": monto_base}
    """
    it = item

    try:
        precio_base = float(nz(it.get("precio"), 0.0))
    except Exception:
        precio_base = 0.0

    qty = float(nz(it.get("cantidad"), 0.0))
    subtotal_base = float(nz(it.get("subtotal_base"), round(precio_base * qty, 2)))
    d_pct = float(nz(it.get("descuento_pct"), 0.0))
    d_monto_base = float(nz(it.get("descuento_monto"), 0.0))

    precio_ui = convert_from_base(precio_base)
    subtotal_ui = convert_from_base(subtotal_base)
    d_monto_ui = convert_from_base(d_monto_base)

    dlg = QDialog(parent)
    dlg.setWindowTitle("Editar descuento")
    dlg.resize(420, 260)
    if not app_icon.isNull():
        dlg.setWindowIcon(app_icon)

    v = QVBoxLayout(dlg)
    v.addWidget(QLabel(f"<b>{it.get('codigo','')}</b> — {it.get('producto','')}"))

    info = QGroupBox("Resumen de línea")
    info_layout = QFormLayout(info)
    info_layout.addRow("Cantidad:", QLabel(str(qty)))
    info_layout.addRow("Precio unitario:", QLabel(fmt_money_ui(precio_ui)))
    info_layout.addRow("Subtotal:", QLabel(fmt_money_ui(subtotal_ui)))
    v.addWidget(info)

    grp = QGroupBox("Descuento")
    form = QFormLayout(grp)

    sp_pct = QDoubleSpinBox()
    sp_pct.setDecimals(4)
    sp_pct.setSingleStep(0.0001)
    sp_pct.setMinimum(0.0)
    sp_pct.setMaximum(100.0)
    sp_pct.setValue(d_pct if d_pct > 0 else 0.0)

    sp_amt = QDoubleSpinBox()
    sp_amt.setDecimals(2)
    sp_amt.setMinimum(0.0)
    sp_amt.setMaximum(max(subtotal_ui, 0.0))
    sp_amt.setValue(d_monto_ui if d_monto_ui > 0 else 0.0)

    form.addRow("Porcentaje (%):", sp_pct)
    form.addRow("Monto:", sp_amt)
    v.addWidget(grp)

    lbl_preview = QLabel()
    v.addWidget(lbl_preview)

    updating = {"from": None}

    def _update_preview():
        pct = float(sp_pct.value())
        amt = float(sp_amt.value())
        total_ui = subtotal_ui - amt
        if pct <= 0 and amt <= 0:
            lbl_preview.setText("Sin descuento aplicado.")
        else:
            lbl_preview.setText(
                f"Descuento: {fmt_money_ui(amt)} ({pct:.4f}%) → "
                f"Total: {fmt_money_ui(total_ui)}"
            )

    def update_from_pct(val):
        if updating["from"] == "amt":
            return
        updating["from"] = "pct"
        try:
            pct = float(val)
        except Exception:
            pct = 0.0
        pct = max(0.0, min(pct, 100.0))
        amt = round(subtotal_ui * pct / 100.0, 2) if subtotal_ui > 0 else 0.0
        sp_amt.setValue(amt)
        updating["from"] = None
        _update_preview()

    def update_from_amt(val):
        if updating["from"] == "pct":
            return
        updating["from"] = "amt"
        try:
            amt = float(val)
        except Exception:
            amt = 0.0
        amt = max(0.0, min(amt, subtotal_ui))
        pct = (amt / subtotal_ui) * 100.0 if subtotal_ui > 0 else 0.0
        sp_pct.setValue(pct)
        updating["from"] = None
        _update_preview()

    sp_pct.valueChanged.connect(update_from_pct)
    sp_amt.valueChanged.connect(update_from_amt)
    _update_preview()

    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    v.addWidget(bb)

    payload: dict = {}

    def on_accept():
        pct = float(sp_pct.value())
        amt_ui = float(sp_amt.value())

        if subtotal_base <= 0 or (pct <= 0 and amt_ui <= 0):
            payload.clear()
            payload.update({"mode": "clear"})
        else:
            cur, _, rate = get_currency_context()
            if cur == base_currency or not rate:
                amt_base = amt_ui
            else:
                amt_base = amt_ui / float(rate)

            if pct > 0:
                payload.clear()
                payload.update({"mode": "percent", "percent": pct})
            else:
                payload.clear()
                payload.update({"mode": "amount", "amount": amt_base})

        dlg.accept()

    bb.accepted.connect(on_accept)
    bb.rejected.connect(dlg.reject)

    if dlg.exec() != QDialog.Accepted:
        return None
    return payload
