# src/widgets_parts/discount_item_dialog.py
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject, QEvent, QTimer, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLabel,
    QGroupBox,
    QFormLayout,
    QDoubleSpinBox,
    QDialogButtonBox,
    QWidget,
)

from ..config import convert_from_base, get_currency_context
from ..utils import fmt_money_ui, nz

MAX_DISCOUNT_PCT = 99.0
_EPS = 1e-9

# ✅ máximos "soft" (para que el validador no bloquee teclas)
SOFT_MAX_PCT = 9999999.0
SOFT_MAX_AMT = 1e15


def _clamp(v: float, lo: float, hi: float) -> float:
    try:
        v = float(v)
    except Exception:
        v = lo
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def _parse_float_from_text(txt: str) -> float:
    s = (txt or "").strip().replace(",", ".")
    out = []
    for ch in s:
        if ch.isdigit() or ch in ".-":
            out.append(ch)
    try:
        return float("".join(out)) if out else 0.0
    except Exception:
        return 0.0


def _cursor_end_no_select(le):
    """Deja el cursor al final y sin selección."""
    try:
        le.deselect()
    except Exception:
        try:
            le.setSelection(0, 0)
        except Exception:
            pass
    try:
        le.setCursorPosition(len(le.text()))
    except Exception:
        pass


class _SelectAllOnKeyboardFocus(QObject):
    """
    Selecciona todo SOLO si el foco llega por teclado (Tab/Backtab).
    Si el foco llega por mouse, NO selecciona (para permitir poner cursor entre dígitos).
    """
    def eventFilter(self, obj, event):
        if event.type() == QEvent.FocusIn:
            try:
                reason = event.reason()
            except Exception:
                reason = None

            if reason in (Qt.TabFocusReason, Qt.BacktabFocusReason):
                QTimer.singleShot(0, obj.selectAll)

        return super().eventFilter(obj, event)


def show_discount_dialog_for_item(
    parent: QWidget,
    app_icon: QIcon,
    item: dict,
    base_currency: str,
) -> Optional[dict]:
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

    # ✅ hard max monto = 99% del subtotal (calculado en BASE y convertido a UI)
    max_amt_base = round(max(0.0, subtotal_base) * (MAX_DISCOUNT_PCT / 100.0), 2)
    max_amt_ui = float(convert_from_base(max_amt_base))

    d_monto_ui = float(convert_from_base(d_monto_base))

    # clamp inicial a hard max (solo para valor inicial)
    d_pct = _clamp(d_pct, 0.0, MAX_DISCOUNT_PCT)
    d_monto_ui = _clamp(d_monto_ui, 0.0, max_amt_ui)

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
    sp_pct.setMaximum(SOFT_MAX_PCT)      # ✅ soft max (deja teclear 190/888)
    sp_pct.setKeyboardTracking(True)
    sp_pct.setValue(d_pct if d_pct > 0 else 0.0)

    sp_amt = QDoubleSpinBox()
    sp_amt.setDecimals(2)
    sp_amt.setMinimum(0.0)
    sp_amt.setMaximum(SOFT_MAX_AMT)      # ✅ soft max
    sp_amt.setKeyboardTracking(True)
    sp_amt.setValue(d_monto_ui if d_monto_ui > 0 else 0.0)

    # ✅ Selección inteligente solo con Tab/Shift+Tab (no con mouse)
    filt = _SelectAllOnKeyboardFocus(dlg)
    sp_pct.lineEdit().installEventFilter(filt)
    sp_amt.lineEdit().installEventFilter(filt)

    form.addRow("Porcentaje (%):", sp_pct)
    form.addRow("Monto:", sp_amt)
    v.addWidget(grp)

    lbl_preview = QLabel()
    v.addWidget(lbl_preview)

    updating = {"lock": False}
    last_edit = {"who": None}  # "pct" | "amt" | None

    def _preview(pct_raw: float, amt_raw: float):
        pct = _clamp(pct_raw, 0.0, MAX_DISCOUNT_PCT)
        amt = _clamp(amt_raw, 0.0, max_amt_ui)
        total_ui = float(subtotal_ui) - amt
        if pct <= 0 and amt <= 0:
            lbl_preview.setText("Sin descuento aplicado.")
        else:
            lbl_preview.setText(
                f"Descuento: {fmt_money_ui(amt)} ({pct:.4f}%) → "
                f"Total: {fmt_money_ui(total_ui)}"
            )

    def _update_preview_live():
        pct_txt = sp_pct.lineEdit().text()
        amt_txt = sp_amt.lineEdit().text()
        pct_raw = _parse_float_from_text(pct_txt)
        amt_raw = _parse_float_from_text(amt_txt)

        if last_edit["who"] == "pct":
            amt_calc = (float(subtotal_ui) * pct_raw / 100.0) if float(subtotal_ui) > 0 else 0.0
            _preview(pct_raw, amt_calc)
        elif last_edit["who"] == "amt":
            pct_calc = (amt_raw / float(subtotal_ui) * 100.0) if float(subtotal_ui) > 0 else 0.0
            _preview(pct_calc, amt_raw)
        else:
            _preview(float(sp_pct.value()), float(sp_amt.value()))

    # ============================
    # ✅ Clamp inmediato EN EL INPUT (hard max) SIN seleccionar todo
    # ============================
    def clamp_pct_now():
        if updating["lock"]:
            return
        raw = _parse_float_from_text(sp_pct.lineEdit().text())
        if raw > MAX_DISCOUNT_PCT + _EPS:
            updating["lock"] = True
            try:
                sp_pct.setValue(MAX_DISCOUNT_PCT)  # se verá en el input
                QTimer.singleShot(0, lambda: _cursor_end_no_select(sp_pct.lineEdit()))
            finally:
                updating["lock"] = False
            last_edit["who"] = None

    def clamp_amt_now():
        if updating["lock"]:
            return
        raw = _parse_float_from_text(sp_amt.lineEdit().text())
        if raw > max_amt_ui + _EPS:
            updating["lock"] = True
            try:
                sp_amt.setValue(max_amt_ui)        # se verá en el input
                QTimer.singleShot(0, lambda: _cursor_end_no_select(sp_amt.lineEdit()))
            finally:
                updating["lock"] = False
            last_edit["who"] = None

    def on_pct_text_edited(_t: str):
        last_edit["who"] = "pct"
        clamp_pct_now()
        _update_preview_live()

    def on_amt_text_edited(_t: str):
        last_edit["who"] = "amt"
        clamp_amt_now()
        _update_preview_live()

    sp_pct.lineEdit().textEdited.connect(on_pct_text_edited)
    sp_amt.lineEdit().textEdited.connect(on_amt_text_edited)

    # confirmación: sincroniza el otro campo (aquí sí “cuadra” ambos)
    def commit_from_pct():
        if updating["lock"]:
            return
        updating["lock"] = True
        try:
            sp_pct.interpretText()
            pct = _clamp(float(sp_pct.value()), 0.0, MAX_DISCOUNT_PCT)
            amt = round(float(subtotal_ui) * pct / 100.0, 2) if float(subtotal_ui) > 0 else 0.0
            amt = _clamp(amt, 0.0, max_amt_ui)
            sp_pct.setValue(pct)
            sp_amt.setValue(amt)
        finally:
            updating["lock"] = False
        last_edit["who"] = None
        _update_preview_live()

    def commit_from_amt():
        if updating["lock"]:
            return
        updating["lock"] = True
        try:
            sp_amt.interpretText()
            amt = _clamp(float(sp_amt.value()), 0.0, max_amt_ui)
            pct = (amt / float(subtotal_ui) * 100.0) if float(subtotal_ui) > 0 else 0.0
            pct = _clamp(pct, 0.0, MAX_DISCOUNT_PCT)
            sp_amt.setValue(amt)
            sp_pct.setValue(pct)
        finally:
            updating["lock"] = False
        last_edit["who"] = None
        _update_preview_live()

    sp_pct.editingFinished.connect(commit_from_pct)
    sp_amt.editingFinished.connect(commit_from_amt)

    _update_preview_live()

    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    v.addWidget(bb)

    payload: dict = {}

    def on_accept():
        sp_pct.interpretText()
        sp_amt.interpretText()

        pct = _clamp(float(sp_pct.value()), 0.0, MAX_DISCOUNT_PCT)
        amt_ui = _clamp(float(sp_amt.value()), 0.0, max_amt_ui)

        updating["lock"] = True
        try:
            sp_pct.setValue(pct)
            sp_amt.setValue(amt_ui)
        finally:
            updating["lock"] = False

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
