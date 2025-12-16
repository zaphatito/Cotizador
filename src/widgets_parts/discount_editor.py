# src/widgets_parts/discount_editor.py
from __future__ import annotations

from typing import Optional

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLabel,
    QFormLayout,
    QDoubleSpinBox,
    QDialogButtonBox,
)

from ..config import convert_from_base
from ..utils import fmt_money_ui, nz


def show_discount_editor(
    parent,
    app_icon: QIcon,
    base_unit_price: float,
    quantity: float,
    current_pct: float = 0.0,
) -> Optional[dict]:
    """
    Devuelve:
      {"pct": float, "amount_base": float, "amount_ui": float}
      o None si se cancela.
    """
    dlg = QDialog(parent)
    dlg.setWindowTitle("Descuento del ítem")
    dlg.resize(420, 220)
    if not app_icon.isNull():
        dlg.setWindowIcon(app_icon)

    v = QVBoxLayout(dlg)

    base_unit_price = float(nz(base_unit_price, 0.0))
    quantity = float(nz(quantity, 0.0))
    if quantity < 0:
        quantity = 0.0

    total_base = max(base_unit_price * quantity, 0.0)
    unit_ui = convert_from_base(base_unit_price)
    total_ui = convert_from_base(total_base)

    info = QLabel(
        f"<b>Precio base:</b> {fmt_money_ui(unit_ui)}   "
        f"<b>Cantidad:</b> {quantity}   "
        f"<b>Total base:</b> {fmt_money_ui(total_ui)}"
    )
    info.setWordWrap(True)
    v.addWidget(info)

    form = QFormLayout()
    sp_pct = QDoubleSpinBox()
    sp_pct.setDecimals(6)
    sp_pct.setSingleStep(0.0001)
    sp_pct.setRange(0.0, 100.0)
    sp_pct.setSuffix(" %")

    sp_monto = QDoubleSpinBox()
    sp_monto.setDecimals(2)
    sp_monto.setRange(0.0, float(total_ui) if total_ui > 0 else 0.0)
    sp_monto.setButtonSymbols(QDoubleSpinBox.NoButtons)

    pct_init = max(0.0, float(nz(current_pct, 0.0)))
    if pct_init > 100.0:
        pct_init = 100.0
    sp_pct.setValue(pct_init)
    sp_monto.setValue(total_ui * pct_init / 100.0)

    form.addRow("Porcentaje de descuento:", sp_pct)
    form.addRow("Monto descontado:", sp_monto)
    v.addLayout(form)

    guard = {"updating": False}

    def on_pct_changed(val: float):
        if guard["updating"]:
            return
        guard["updating"] = True
        sp_monto.setValue(total_ui * float(val) / 100.0)
        guard["updating"] = False

    def on_monto_changed(val: float):
        if guard["updating"]:
            return
        guard["updating"] = True
        pct = (float(val) / total_ui) * 100.0 if total_ui > 0 else 0.0
        sp_pct.setValue(max(0.0, min(pct, 100.0)))
        guard["updating"] = False

    sp_pct.valueChanged.connect(on_pct_changed)
    sp_monto.valueChanged.connect(on_monto_changed)

    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    v.addWidget(bb)

    payload: dict = {}

    def accept():
        pct = max(0.0, min(float(sp_pct.value()), 100.0))
        amount_ui = float(sp_monto.value())
        amount_base = total_base * pct / 100.0 if total_base > 0 else 0.0

        payload["pct"] = pct
        payload["amount_ui"] = amount_ui
        payload["amount_base"] = amount_base
        dlg.accept()

    bb.accepted.connect(accept)
    bb.rejected.connect(dlg.reject)

    if dlg.exec() != QDialog.Accepted:
        return None
    return payload
