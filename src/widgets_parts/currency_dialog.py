# src/widgets_parts/currency_dialog.py
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLabel,
    QRadioButton,
    QGroupBox,
    QHBoxLayout,
    QDoubleSpinBox,
    QDialogButtonBox,
    QMessageBox,
    QWidget,
)


def show_currency_dialog(
    parent: QWidget,
    app_icon: QIcon,
    base_currency: str,
    secondary_currency: str,
    exchange_rate: Optional[float],
    saved_rates: Optional[dict[str, float]] = None,
) -> Optional[dict]:
    """
    Diálogo de moneda y tasas de cambio.

    Soporta:
      - Moneda base (sin tasa)
      - Varias monedas secundarias (cada una con tasa propia)

    Devuelve:
      {
          "currency": "<moneda_seleccionada>",
          "is_base": True|False,
          "rate": <tasa_para_moneda_seleccionada (1.0 si es base)>,
          "rates": { "<sec1>": <tasa>, "<sec2>": <tasa>, ... }
      }
    o None si se cancela.
    """
    dlg = QDialog(parent)
    dlg.setWindowTitle("Moneda y tasas de cambio")
    dlg.resize(380, 260)
    if not app_icon.isNull():
        dlg.setWindowIcon(app_icon)

    v = QVBoxLayout(dlg)

    # imports locales para evitar ciclos
    from ..config import APP_CURRENCY, get_secondary_currencies, get_currency_context

    base = (base_currency or APP_CURRENCY).upper()

    sec_list = [c.upper() for c in (get_secondary_currencies() or []) if c]
    if secondary_currency:
        sec = secondary_currency.upper()
        if sec not in sec_list:
            sec_list.append(sec)

    all_codes = [base] + [c for c in sec_list if c != base]
    all_codes = list(dict.fromkeys(all_codes))

    rates: dict[str, float] = {}
    if saved_rates:
        for code in sec_list:
            try:
                val = float(saved_rates.get(code, 0.0))
            except Exception:
                val = 0.0
            rates[code] = val if val > 0 else 0.0

    if not rates and exchange_rate and sec_list:
        try:
            val = float(exchange_rate)
        except Exception:
            val = 0.0
        if val > 0:
            rates[sec_list[0]] = val

    cur, _sec_principal, rate_global = get_currency_context()
    cur = (cur or "").upper()
    if cur not in all_codes:
        cur = base

    v.addWidget(QLabel("Seleccione la moneda en la que desea trabajar:"))

    radios: dict[str, QRadioButton] = {}
    for code in all_codes:
        text = f"Moneda principal ({code})" if code == base else f"Moneda secundaria ({code})"
        rb = QRadioButton(text)
        radios[code] = rb
        v.addWidget(rb)

    if cur in radios:
        radios[cur].setChecked(True)
    else:
        radios[base].setChecked(True)

    grp_tasas = QGroupBox("Tasa de cambio")
    tasas_layout = QVBoxLayout(grp_tasas)

    rate_rows: dict[str, tuple[QWidget, QDoubleSpinBox]] = {}
    for code in sec_list:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(QLabel(f"1 {base} ="))

        sp = QDoubleSpinBox()
        sp.setDecimals(6)
        sp.setMinimum(0.000001)
        sp.setMaximum(999999999.0)

        initial = rates.get(code, 0.0)
        if initial <= 0.0:
            if code == cur and cur != base and rate_global and rate_global > 0:
                initial = float(rate_global)
            else:
                initial = 1.0
        sp.setValue(initial)

        h.addWidget(sp, 1)
        h.addWidget(QLabel(code))
        tasas_layout.addWidget(row)

        rate_rows[code] = (row, sp)

    if not sec_list:
        grp_tasas.setVisible(False)

    v.addWidget(grp_tasas)

    def _apply_visibility():
        selected_code = None
        for code, rb in radios.items():
            if rb.isChecked():
                selected_code = code
                break
        if not selected_code:
            selected_code = base

        if selected_code == base or not sec_list:
            grp_tasas.setVisible(False)
        else:
            grp_tasas.setVisible(True)
            for code, (row, _sp) in rate_rows.items():
                row.setVisible(code == selected_code)

    for _code, rb in radios.items():
        rb.toggled.connect(_apply_visibility)

    _apply_visibility()

    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    v.addWidget(bb)

    result: dict | None = None

    def on_accept():
        nonlocal result

        selected_code = None
        for code, rb in radios.items():
            if rb.isChecked():
                selected_code = code
                break
        if not selected_code:
            selected_code = base

        is_base = (selected_code == base)

        new_rates: dict[str, float] = {}
        for code, (_row, sp) in rate_rows.items():
            val = float(sp.value())
            new_rates[code] = val if val > 0 else 0.0

        rate_for_selected = 1.0
        if not is_base:
            r = new_rates.get(selected_code, 0.0)
            if r <= 0:
                QMessageBox.warning(
                    parent,
                    "Tasa requerida",
                    f"Ingrese una tasa válida para la moneda secundaria {selected_code}.",
                )
                return
            rate_for_selected = r

        result = {
            "currency": selected_code,
            "is_base": is_base,
            "rate": rate_for_selected,
            "rates": new_rates,
        }
        dlg.accept()

    bb.accepted.connect(on_accept)
    bb.rejected.connect(dlg.reject)

    if dlg.exec() != QDialog.Accepted or result is None:
        return None
    return result
