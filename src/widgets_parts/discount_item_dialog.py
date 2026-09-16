"""Editor de descuentos sobre el importe comercial de la línea."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QLabel, QDoubleSpinBox,
    QDialogButtonBox,
)

from ..config import convert_from_base
from ..pricing import discount_percentage_decimals
from ..lcdp_pricing import line_discount, line_subtotal, money, percentage_from_entered_amount
from ..utils import fmt_money_ui


def show_discount_dialog_for_item(
    parent, app_icon, item, base_currency, *, converter=None,
    current_currency=None, country=None,
):
    convert = converter if callable(converter) else convert_from_base
    rate = float(convert(1.0))
    if rate <= 0:
        raise ValueError("Configura una tasa positiva para convertir entre monedas")
    subtotal = float(item.get("subtotal_base", line_subtotal(
        item.get("precio", 0), item.get("cantidad", 0), item.get("factor_total", 1)
    )))
    currency = current_currency or base_currency
    dlg = QDialog(parent)
    dlg.setWindowTitle("Editar descuento")
    dlg.setMinimumWidth(420)
    if not app_icon.isNull():
        dlg.setWindowIcon(app_icon)
    layout = QVBoxLayout(dlg)
    label = QLabel(str(item.get("codigo", "")) + " — " + str(item.get("producto", "")))
    label.setTextFormat(Qt.PlainText)
    layout.addWidget(label)
    layout.addWidget(QLabel("Subtotal: " + fmt_money_ui(convert(subtotal), currency=currency)))
    form = QFormLayout()
    pct = QDoubleSpinBox()
    pct.setObjectName("discount_percent")
    pct.setDecimals(discount_percentage_decimals(country))
    pct.setRange(0, 99)
    pct.setSuffix(" %")
    amount = QDoubleSpinBox()
    amount.setObjectName("discount_amount")
    final = QDoubleSpinBox()
    final.setObjectName("discount_final")
    for field in (amount, final):
        field.setDecimals(2)
        field.setRange(0, max(0, money(convert(subtotal))))
    for field in (pct, amount, final):
        field.setButtonSymbols(QDoubleSpinBox.NoButtons)
        field.setKeyboardTracking(False)
    form.addRow("Porcentaje:", pct)
    form.addRow("Importe descontado:", amount)
    form.addRow("Total después del descuento:", final)
    layout.addLayout(form)
    preview = QLabel()
    preview.setWordWrap(True)
    layout.addWidget(preview)
    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    layout.addWidget(buttons)
    mode = {"value": item.get("descuento_mode") or "percent", "locked": False}
    payload = {}

    def recalculate(source):
        if mode["locked"]:
            return
        mode["locked"] = True
        try:
            if source == "pct":
                method, percent, amt = "percent", pct.value(), 0
            else:
                method = "percent"
                amt = amount.value() / rate if source == "amt" else subtotal - final.value() / rate
                percent = percentage_from_entered_amount(subtotal, amt)
            percentage, discount, total = line_discount(subtotal, method, percent, amt, country)
            mode["value"] = method
            # El porcentaje redondeado determina el importe disponible.
            for field in (pct, amount, final):
                field.blockSignals(True)
            pct.setValue(percentage)
            amount.setValue(convert(discount))
            final.setValue(convert(total))
            for field in (pct, amount, final):
                field.blockSignals(False)
            payload.clear()
            payload.update({"mode": method, "percent": percentage, "amount": discount})
            preview.setText("Total: " + fmt_money_ui(convert(total), currency=currency))
            buttons.button(QDialogButtonBox.Ok).setEnabled(True)
        except ValueError as exc:
            preview.setText(str(exc))
            buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        finally:
            mode["locked"] = False

    pct.setValue(float(item.get("descuento_pct", 0)))
    amount.setValue(convert(float(item.get("descuento_monto", 0))))
    pct.valueChanged.connect(lambda _: recalculate("pct"))
    amount.valueChanged.connect(lambda _: recalculate("amt"))
    final.valueChanged.connect(lambda _: recalculate("final"))
    recalculate("amt" if mode["value"] == "amount" else "pct")
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    if dlg.exec() != QDialog.Accepted:
        return None
    return payload
