# src/widgets_parts/price_picker.py
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLabel,
    QGroupBox,
    QHBoxLayout,
    QPushButton,
    QFrame,
    QWidget,
    QDoubleSpinBox,
    QDialogButtonBox,
)

from ..config import convert_from_base
from ..utils import fmt_money_ui
from .helpers import _first_nonzero


def show_price_picker(parent, app_icon: QIcon, item: dict) -> Optional[dict]:
    """
    Devuelve:
      {"mode":"tier", "tier": "unitario|oferta|minimo|base", "price": float}
      {"mode":"custom","price": float}
      o None si se cancela.

    El 'price' que devuelve SIEMPRE está en moneda base.
    """
    dlg = QDialog(parent)
    dlg.setWindowTitle("Seleccionar precio")
    dlg.resize(560, 320)
    if not app_icon.isNull():
        dlg.setWindowIcon(app_icon)

    v = QVBoxLayout(dlg)

    lbl = QLabel(f"<b>{item.get('codigo','')}</b> — {item.get('producto','')}")
    lbl.setTextFormat(Qt.RichText)
    v.addWidget(lbl)

    prod = item.get("_prod", {}) or {}
    cat = (item.get("categoria") or "").upper()

    unitario = _first_nonzero(prod, ["precio_unidad", "precio_unitario", "precio_venta"])
    oferta = _first_nonzero(
        prod,
        [
            "precio_oferta",
            "precio_oferta_base",
            "oferta",
            ">12 unidades",
            "precio_12",
            "precio_12_unidades",
            "mayor_12",
            "mayor12",
            "docena",
            "precio_mayorista",
        ],
    )
    minimo = _first_nonzero(
        prod,
        [
            "precio_minimo",
            "precio_minimo_base",
            "minimo",
            ">100 unidades",
            "precio_100",
            "precio_100_unidades",
            "mayor_100",
            "ciento",
        ],
    )
    base_val = _first_nonzero(
        prod,
        ["precio_unitario", "precio_unidad", "precio_base_50g", "precio_venta"],
    )

    box = QGroupBox("Elige un precio")
    grid = QHBoxLayout(box)

    def _format_tier_value(val_base: float) -> str:
        return fmt_money_ui(convert_from_base(val_base)) if val_base > 0 else "—"

    def make_card(title: str, value: float):
        btn = QPushButton()
        btn.setCursor(Qt.PointingHandCursor)
        btn.setEnabled(value > 0.0)
        btn.setMinimumHeight(72)
        btn.setStyleSheet(
            """
            QPushButton {
                border: 1px solid #bbb;
                border-radius: 8px;
                padding: 10px 16px;
                text-align: left;
            }
            QPushButton:disabled { color: #888; border-color: #ddd; }
            QPushButton:checked  { border: 2px solid #2d7; }
        """
        )
        btn.setText(f"{title}\n{_format_tier_value(value)}")
        btn.setCheckable(True)
        return btn

    btn_u = make_card("Unitario", unitario)
    btn_o = make_card("Oferta", oferta)
    btn_m = make_card("Mínimo", minimo)
    btn_b = make_card("Base", base_val)

    card_custom = QFrame()
    card_custom.setStyleSheet(
        """
        QFrame#customCard {
            border: 1px solid #bbb; border-radius: 8px;
        }
    """
    )
    card_custom.setObjectName("customCard")
    custom_layout = QVBoxLayout(card_custom)
    custom_layout.setContentsMargins(10, 10, 10, 10)

    btn_c = QPushButton()
    btn_c.setCursor(Qt.PointingHandCursor)
    btn_c.setCheckable(True)
    btn_c.setMinimumHeight(72)
    btn_c.setStyleSheet(
        """
        QPushButton {
            border: none;
            text-align: left;
            padding: 0;
        }
        QPushButton:checked { }
    """
    )

    row_input = QWidget()
    row_input_layout = QHBoxLayout(row_input)
    row_input_layout.setContentsMargins(0, 6, 0, 0)
    row_input_layout.addWidget(QLabel("Monto:"))
    sp = QDoubleSpinBox()
    sp.setDecimals(4)
    sp.setMinimum(0.0)
    sp.setMaximum(999999999.0)
    sp.setButtonSymbols(QDoubleSpinBox.NoButtons)

    sp.setValue(float(item.get("precio_override") if item.get("precio_override") is not None else item.get("precio", 0.0)))
    row_input_layout.addWidget(sp, 1)
    row_input.setVisible(False)

    def custom_text():
        return f"Personalizado\n{fmt_money_ui(convert_from_base(float(sp.value())))}"

    btn_c.setText(custom_text())

    custom_layout.addWidget(btn_c)
    custom_layout.addWidget(row_input)

    def on_custom_clicked(_checked: bool):
        row_input.setVisible(btn_c.isChecked())
        for b in (btn_u, btn_o, btn_m, btn_b):
            b.setChecked(False)

    btn_c.clicked.connect(on_custom_clicked)
    sp.valueChanged.connect(lambda _v: btn_c.setText(custom_text()))

    if cat == "BOTELLAS":
        grid.addWidget(btn_u)
        grid.addWidget(btn_o)
        grid.addWidget(btn_m)
    else:
        grid.addWidget(btn_b)

    v.addWidget(box)
    v.addWidget(card_custom)

    cur_tier = (item.get("precio_tier") or "").upper()
    if item.get("precio_override") is not None:
        btn_c.setChecked(True)
        row_input.setVisible(True)
    elif cat == "BOTELLAS":
        if cur_tier == "OFERTA" and btn_o.isEnabled():
            btn_o.setChecked(True)
        elif cur_tier == "MINIMO" and btn_m.isEnabled():
            btn_m.setChecked(True)
        elif cur_tier == "UNITARIO" and btn_u.isEnabled():
            btn_u.setChecked(True)
        elif btn_u.isEnabled():
            btn_u.setChecked(True)
    else:
        if cur_tier == "BASE" and btn_b.isEnabled():
            btn_b.setChecked(True)
        elif btn_b.isEnabled():
            btn_b.setChecked(True)

    def pick(btn):
        for b in (btn_u, btn_o, btn_m, btn_b):
            b.setChecked(b is btn)
        btn_c.setChecked(False)
        row_input.setVisible(False)

    for b in (btn_u, btn_o, btn_m, btn_b):
        b.clicked.connect(lambda _=None, bb=b: pick(bb))

    bb = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
    v.addWidget(bb)

    payload: dict = {"mode": None}

    def accept():
        if btn_c.isChecked():
            payload["mode"] = "custom"
            payload["price"] = float(sp.value())
            dlg.accept()
            return

        if cat == "BOTELLAS":
            if btn_u.isChecked():
                payload.update({"mode": "tier", "tier": "unitario", "price": unitario})
            elif btn_o.isChecked():
                payload.update({"mode": "tier", "tier": "oferta", "price": oferta})
            elif btn_m.isChecked():
                payload.update({"mode": "tier", "tier": "minimo", "price": minimo})
        else:
            if btn_b.isChecked():
                payload.update({"mode": "tier", "tier": "base", "price": base_val})

        dlg.accept()

    bb.accepted.connect(accept)
    bb.rejected.connect(dlg.reject)

    ok = dlg.exec() == QDialog.Accepted
    if not ok or payload.get("mode") is None:
        return None
    return payload
