# src/widgets_parts/price_picker.py
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPalette
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

from ..pricing import default_price_id_for_product
from ..config import convert_from_base
from ..utils import fmt_money_ui
from .helpers import _first_nonzero


def _resolve_current_price_id(item: dict) -> int:
    try:
        pid = int(item.get("id_precioventa") or 0)
    except Exception:
        pid = 0
    if pid in (1, 2, 3, 4):
        return pid

    tier = str(item.get("precio_tier") or "").strip().lower()
    if "min" in tier:
        return 2
    if "oferta" in tier or "promo" in tier:
        return 3
    if item.get("precio_override") is not None:
        return 4
    prod = item.get("_prod", {}) or {}
    return int(default_price_id_for_product(prod))


def show_price_picker(parent, app_icon: QIcon, item: dict) -> Optional[dict]:
    """
    Devuelve:
      {"mode":"tier", "tier":"unitario|minimo|oferta", "price": float, "id_precioventa": int}
      {"mode":"custom","price": float, "id_precioventa": 4}
      o None si se cancela.

    El price siempre esta en moneda base.
    """
    dlg = QDialog(parent)
    dlg.setWindowTitle("Seleccionar precio")
    dlg.setMinimumWidth(460)
    if not app_icon.isNull():
        dlg.setWindowIcon(app_icon)

    v = QVBoxLayout(dlg)
    lbl = QLabel(f"<b>{item.get('codigo','')}</b> - {item.get('producto','')}")
    lbl.setTextFormat(Qt.RichText)
    v.addWidget(lbl)

    prod = item.get("_prod", {}) or {}
    cat = (item.get("categoria") or "").upper()
    is_service = (cat == "SERVICIO")

    p_max = _first_nonzero(
        prod,
        ["p_max", "P_MAX"],
    )
    p_oferta = _first_nonzero(
        prod,
        [
            "p_oferta",
            "P_OFERTA",
        ],
    )
    p_min = _first_nonzero(
        prod,
        [
            "p_min",
            "P_MIN",
        ],
    )

    def _format_tier_value(val_base: float) -> str:
        return fmt_money_ui(convert_from_base(val_base)) if val_base > 0 else "-"

    try:
        win_col = dlg.palette().color(QPalette.Window)
        lum = (0.2126 * win_col.redF()) + (0.7152 * win_col.greenF()) + (0.0722 * win_col.blueF())
        is_dark = lum < 0.45
    except Exception:
        is_dark = True

    def make_card(title: str, value: float):
        btn = QPushButton()
        btn.setCursor(Qt.PointingHandCursor)
        btn.setEnabled(value > 0.0)
        btn.setMinimumHeight(72)
        if is_dark:
            card_style = """
                QPushButton {
                    border: 1px solid #4a5870;
                    border-radius: 10px;
                    padding: 10px 16px;
                    text-align: left;
                    background-color: #233042;
                    color: #e7edf7;
                }
                QPushButton:hover {
                    background-color: #2a3a4e;
                }
                QPushButton:disabled {
                    color: #8d99ab;
                    border-color: #3a4558;
                    background-color: #1d2735;
                }
                QPushButton:checked {
                    border: 2px solid #5b96b0;
                    background-color: #365a78;
                    color: #f4f9ff;
                }
                QPushButton:checked:hover {
                    background-color: #426b8c;
                }
            """
        else:
            card_style = """
                QPushButton {
                    border: 1px solid #c5d4e7;
                    border-radius: 10px;
                    padding: 10px 16px;
                    text-align: left;
                    background-color: #ffffff;
                    color: #223045;
                }
                QPushButton:hover {
                    background-color: #f3f7fc;
                }
                QPushButton:disabled {
                    color: #8895a8;
                    border-color: #dbe3ef;
                    background-color: #f5f7fb;
                }
                QPushButton:checked {
                    border: 2px solid #4a7796;
                    background-color: #dbeaf6;
                    color: #143047;
                }
                QPushButton:checked:hover {
                    background-color: #cfe3f3;
                }
            """
        btn.setStyleSheet(card_style)
        btn.setText(f"{title}\n{_format_tier_value(value)}")
        btn.setCheckable(True)
        return btn

    bb = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
    ok_btn = bb.button(QDialogButtonBox.Ok)
    if ok_btn is not None:
        ok_btn.setProperty("variant", "primary")
    payload: dict = {"mode": None}

    if not is_service:
        box = QGroupBox("Elige un precio")
        grid = QHBoxLayout(box)
        btn_u = make_card("P. Max", p_max)
        btn_o = make_card("P. Oferta", p_oferta)
        btn_m = make_card("P. Min", p_min)
        grid.addWidget(btn_u)
        grid.addWidget(btn_o)
        grid.addWidget(btn_m)
        v.addWidget(box)

        def pick(btn):
            for b in (btn_u, btn_o, btn_m):
                b.setChecked(b is btn)

        for b in (btn_u, btn_o, btn_m):
            b.clicked.connect(lambda _=None, bb_=b: pick(bb_))

        cur_pid = _resolve_current_price_id(item)
        if cur_pid == 2 and btn_m.isEnabled():
            btn_m.setChecked(True)
        elif cur_pid == 3 and btn_o.isEnabled():
            btn_o.setChecked(True)
        elif btn_u.isEnabled():
            btn_u.setChecked(True)
        elif btn_o.isEnabled():
            btn_o.setChecked(True)
        elif btn_m.isEnabled():
            btn_m.setChecked(True)

        def accept_tier():
            if btn_m.isChecked():
                payload.update({"mode": "tier", "tier": "minimo", "price": p_min, "id_precioventa": 2})
            elif btn_o.isChecked():
                payload.update({"mode": "tier", "tier": "oferta", "price": p_oferta, "id_precioventa": 3})
            elif btn_u.isChecked():
                payload.update({"mode": "tier", "tier": "unitario", "price": p_max, "id_precioventa": 1})
            if payload.get("mode"):
                dlg.accept()

        bb.accepted.connect(accept_tier)
        bb.rejected.connect(dlg.reject)
        v.addWidget(bb)
        dlg.adjustSize()

        ok = dlg.exec() == QDialog.Accepted
        if not ok or payload.get("mode") is None:
            return None
        return payload

    # SERVICIO: siempre personalizado
    card_custom = QFrame()
    card_custom.setStyleSheet(
        """
        QFrame#customCard {
            border: 1px solid #c5d4e7;
            border-radius: 10px;
        }
    """
    )
    card_custom.setObjectName("customCard")
    custom_layout = QVBoxLayout(card_custom)
    custom_layout.setContentsMargins(10, 10, 10, 10)

    btn_c = QPushButton()
    btn_c.setCursor(Qt.PointingHandCursor)
    btn_c.setCheckable(True)
    btn_c.setChecked(True)
    btn_c.setMinimumHeight(72)
    btn_c.setStyleSheet(
        """
        QPushButton {
            border: none;
            text-align: left;
            padding: 0;
        }
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

    def custom_text():
        return f"Personalizado\n{fmt_money_ui(convert_from_base(float(sp.value())))}"

    btn_c.setText(custom_text())
    custom_layout.addWidget(btn_c)
    custom_layout.addWidget(row_input)
    sp.valueChanged.connect(lambda _v: btn_c.setText(custom_text()))
    v.addWidget(card_custom)

    def accept_custom():
        payload["mode"] = "custom"
        payload["price"] = float(sp.value())
        payload["id_precioventa"] = 4
        dlg.accept()

    bb.accepted.connect(accept_custom)
    bb.rejected.connect(dlg.reject)
    v.addWidget(bb)
    dlg.adjustSize()

    ok = dlg.exec() == QDialog.Accepted
    if not ok or payload.get("mode") is None:
        return None
    return payload
