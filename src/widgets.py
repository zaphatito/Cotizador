# src/widgets.py
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Optional

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLineEdit,
    QTableWidget,
    QHeaderView,
    QAbstractItemView,
    QTableWidgetItem,
    QPushButton,
    QLabel,
    QFormLayout,
    QHBoxLayout,
    QGroupBox,
    QDoubleSpinBox,
    QDialogButtonBox,
    QFrame,
    QWidget,
    QTabWidget,
    QMessageBox,
    QRadioButton,
)
from PySide6.QtGui import QIcon, QBrush
from PySide6.QtCore import Qt

from .config import (
    listing_allows_products,
    listing_allows_presentations,
    ALLOW_NO_STOCK,
    convert_from_base,
    APP_COUNTRY,
    id_label_for_country,
    CATS,
    get_currency_context,
)
from .pricing import precio_base_para_listado, cantidad_para_mostrar
from .utils import fmt_money_ui, nz
from .presentations import map_pc_to_bottle_code  # mantenido por compatibilidad


# ===== helpers =====
def _fmt_trim_decimal(x) -> str:
    try:
        d = Decimal(str(x)).normalize()
        s = format(d, "f")
        return "0" if s == "-0" else s
    except (InvalidOperation, Exception):
        try:
            f = float(x)
            return str(int(f)) if f.is_integer() else str(f)
        except Exception:
            return str(x)


def _first_nonzero(d: dict, keys: list[str]) -> float:
    for k in keys:
        try:
            v = float(nz(d.get(k, 0.0), 0.0))
        except Exception:
            v = 0.0
        if v > 0:
            return v
    return 0.0


# ===================== Diálogos genéricos extra =====================


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

    Ahora soporta TODAS las monedas disponibles para el país:
      - Moneda base (no necesita tasa).
      - Varias monedas secundarias (cada una con su tasa propia).

    Devuelve un dict con:
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

    # --- monedas disponibles ---
    from .config import APP_CURRENCY, get_secondary_currencies, get_currency_context

    base = (base_currency or APP_CURRENCY).upper()

    # Lista de secundarias del país
    sec_list = [c.upper() for c in (get_secondary_currencies() or []) if c]
    # Compat: si no viene nada en config pero igual te pasan una "secondary_currency"
    if secondary_currency:
        sec = secondary_currency.upper()
        if sec not in sec_list:
            sec_list.append(sec)

    # Si no hay secundarias, solo mostramos la base
    all_codes = [base] + [c for c in sec_list if c != base]
    all_codes = list(dict.fromkeys(all_codes))  # dedupe preservando orden

    # Tasas conocidas (por moneda secundaria)
    rates: dict[str, float] = {}
    if saved_rates:
        for code in sec_list:
            try:
                val = float(saved_rates.get(code, 0.0))
            except Exception:
                val = 0.0
            rates[code] = val if val > 0 else 0.0

    # Fallback legacy: solo viene "exchange_rate" (1 moneda secundaria)
    if not rates and exchange_rate and sec_list:
        try:
            val = float(exchange_rate)
        except Exception:
            val = 0.0
        if val > 0:
            rates[sec_list[0]] = val

    # Código de moneda actualmente activa en la UI
    cur, _sec_principal, rate_global = get_currency_context()
    cur = (cur or "").upper()
    if cur not in all_codes:
        cur = base

    # --- Radios de selección de moneda ---
    v.addWidget(QLabel("Seleccione la moneda en la que desea trabajar:"))

    radios: dict[str, QRadioButton] = {}
    for code in all_codes:
        if code == base:
            text = f"Moneda principal ({code})"
        else:
            text = f"Moneda secundaria ({code})"
        rb = QRadioButton(text)
        radios[code] = rb
        v.addWidget(rb)

    # Preseleccionar la moneda actual
    if cur in radios:
        radios[cur].setChecked(True)
    else:
        radios[base].setChecked(True)

    # --- Bloque de tasas (solo para secundarias) ---
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

        # valor inicial para esta moneda
        initial = rates.get(code, 0.0)
        if initial <= 0.0:
            # Si es la moneda actual, usar rate_global como sugerencia
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

    # --- lógica de visibilidad ---
    def _apply_visibility():
        # moneda seleccionada
        selected_code = None
        for code, rb in radios.items():
            if rb.isChecked():
                selected_code = code
                break
        if not selected_code:
            selected_code = base

        # Si es la moneda principal, no se muestra el input de tasa
        if selected_code == base or not sec_list:
            grp_tasas.setVisible(False)
        else:
            grp_tasas.setVisible(True)
            # Solo se muestra el input de ESA moneda secundaria
            for code, (row, _sp) in rate_rows.items():
                row.setVisible(code == selected_code)

    for code, rb in radios.items():
        rb.toggled.connect(_apply_visibility)

    _apply_visibility()

    # --- botones OK / Cancel ---
    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    v.addWidget(bb)

    result: dict | None = None

    def on_accept():
        nonlocal result
        # Moneda seleccionada
        selected_code = None
        for code, rb in radios.items():
            if rb.isChecked():
                selected_code = code
                break
        if not selected_code:
            selected_code = base

        is_base = (selected_code == base)

        # Guardar tasas
        rate_for_selected = 1.0
        new_rates: dict[str, float] = {}

        for code, (row, sp) in rate_rows.items():
            val = float(sp.value())
            if val <= 0:
                val = 0.0
            new_rates[code] = val

        # Validar tasa si es secundaria
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
    subtotal_base = float(
        nz(it.get("subtotal_base"), round(precio_base * qty, 2))
    )
    d_pct = float(nz(it.get("descuento_pct"), 0.0))
    d_monto_base = float(nz(it.get("descuento_monto"), 0.0))

    # Convertir a moneda actual para mostrar
    precio_ui = convert_from_base(precio_base)
    subtotal_ui = convert_from_base(subtotal_base)
    d_monto_ui = convert_from_base(d_monto_base)

    dlg = QDialog(parent)
    dlg.setWindowTitle("Editar descuento")
    dlg.resize(420, 260)
    if not app_icon.isNull():
        dlg.setWindowIcon(app_icon)

    v = QVBoxLayout(dlg)
    v.addWidget(
        QLabel(f"<b>{it.get('codigo','')}</b> — {it.get('producto','')}")
    )

    # --- Resumen de línea ---
    info = QGroupBox("Resumen de línea")
    info_layout = QFormLayout(info)
    info_layout.addRow("Cantidad:", QLabel(str(qty)))
    info_layout.addRow("Precio unitario:", QLabel(fmt_money_ui(precio_ui)))
    info_layout.addRow("Subtotal:", QLabel(fmt_money_ui(subtotal_ui)))
    v.addWidget(info)

    # --- Bloque de descuento ---
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

    # --- Sincronización % ↔ monto ---
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
        if subtotal_ui > 0:
            amt = round(subtotal_ui * pct / 100.0, 2)
        else:
            amt = 0.0
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
        if subtotal_ui > 0:
            pct = (amt / subtotal_ui) * 100.0
        else:
            pct = 0.0
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
            # Convertir monto desde moneda actual a base
            cur, _, rate = get_currency_context()
            if cur == base_currency or not rate:
                amt_base = amt_ui
            else:
                # rate ~ base→moneda_actual
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


def show_observation_dialog(
    parent: QWidget,
    app_icon: QIcon,
    initial_text: str,
) -> Optional[str]:
    """
    Diálogo simple para editar la observación de un ítem.

    Devuelve el nuevo texto o None si se cancela.
    """
    dlg = QDialog(parent)
    dlg.setWindowTitle("Editar Observación")
    dlg.resize(320, 120)
    if not app_icon.isNull():
        dlg.setWindowIcon(app_icon)

    v = QVBoxLayout(dlg)
    v.addWidget(QLabel("Ingrese observación (ej: Color ámbar):"))
    entry = QLineEdit()
    entry.setText(initial_text or "")
    v.addWidget(entry)
    btn = QPushButton("Guardar")
    v.addWidget(btn)

    result: dict[str, Optional[str]] = {"text": None}

    def _save():
        result["text"] = entry.text().strip()
        dlg.accept()

    btn.clicked.connect(_save)

    if dlg.exec() != QDialog.Accepted:
        return None
    return result["text"]


def show_preview_dialog(
    parent: QWidget,
    app_icon: QIcon,
    cliente: str,
    cedula: str,
    telefono: str,
    items: list[dict],
) -> None:
    """
    Diálogo de previsualización de cotización.

    Solo muestra datos; no modifica nada.
    """
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
            nz(
                it.get("subtotal_base"),
                precio_base * nz(it.get("cantidad"), 0.0),
            )
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

        vals = [
            it.get("codigo", ""),
            prod,
            qty_txt,
            precio_ui,
            desc_txt,
            subtotal_ui,
        ]
        for col, val in enumerate(vals):
            tbl.setItem(r, col, QTableWidgetItem(str(val)))

        # Chequeo de stock con float (visual) en cantidad
        try:
            cat_u = (it.get("categoria") or "").upper()
            disp = float(nz(it.get("stock_disponible"), 0.0))
            cant = float(nz(it.get("cantidad"), 0.0))
            mult = 50.0 if (
                APP_COUNTRY in ("VENEZUELA", "PARAGUAY") and cat_u in CATS
            ) else 1.0
            if cant * mult > disp and disp >= 0.0:
                qty_item = tbl.item(r, 2)
                if qty_item:
                    qty_item.setForeground(QBrush(Qt.red))
        except Exception:
            pass

    v.addWidget(tbl)

    # Totales generales
    v.addWidget(
        QLabel(
            f"<b>Subtotal sin descuento:</b> "
            f"{fmt_money_ui(convert_from_base(subtotal_bruto_base))}"
        )
    )
    v.addWidget(
        QLabel(
            f"<b>Descuento total:</b> "
            f"-{fmt_money_ui(convert_from_base(descuento_total_base))}"
        )
    )
    v.addWidget(
        QLabel(
            f"<b>Total General:</b> "
            f"{fmt_money_ui(convert_from_base(total_neto_base))}"
        )
    )

    btn = QPushButton("Cerrar")
    btn.clicked.connect(dlg.accept)
    v.addWidget(btn)
    dlg.exec()


# ===================== Diálogos de listado / custom =====================


class SelectorTablaSimple(QDialog):
    def __init__(self, parent, titulo, filas, app_icon: QIcon = QIcon()):
        super().__init__(parent)
        self.setWindowTitle(titulo)
        self.resize(560, 420)
        if not app_icon.isNull():
            self.setWindowIcon(app_icon)
        self.seleccion = None

        v = QVBoxLayout(self)
        self.entry_buscar = QLineEdit()
        self.entry_buscar.setPlaceholderText("Filtrar…")
        v.addWidget(self.entry_buscar)

        self.tabla = QTableWidget(0, 4)
        self.tabla.setHorizontalHeaderLabels(
            ["Código", "Nombre", "Departamento", "Género"]
        )
        self.tabla.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tabla.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tabla.setSelectionBehavior(QAbstractItemView.SelectRows)
        v.addWidget(self.tabla)

        self._rows = filas[:]

        def pintar(rows):
            self.tabla.setRowCount(0)
            for r in rows:
                i = self.tabla.rowCount()
                self.tabla.insertRow(i)
                self.tabla.setItem(
                    i, 0, QTableWidgetItem(str(r.get("codigo", "")))
                )
                self.tabla.setItem(
                    i, 1, QTableWidgetItem(str(r.get("nombre", "")))
                )
                self.tabla.setItem(
                    i, 2, QTableWidgetItem(str(r.get("categoria", "")))
                )
                self.tabla.setItem(
                    i, 3, QTableWidgetItem(str(r.get("genero", "")))
                )

        self._pintar = pintar
        self._pintar(self._rows)

        def filtrar(txt):
            t = txt.lower().strip()
            if not t:
                self._pintar(self._rows)
                return
            filtrados = []
            for r in self._rows:
                if (
                    t in str(r.get("codigo", "")).lower()
                    or t in str(r.get("nombre", "")).lower()
                    or t in str(r.get("categoria", "")).lower()
                    or t in str(r.get("genero", "")).lower()
                ):
                    filtrados.append(r)
            self._pintar(filtrados)

        self.entry_buscar.textChanged.connect(filtrar)

        self.tabla.cellDoubleClicked.connect(lambda row, _col: self._guardar(row))
        btn = QPushButton("Seleccionar")
        btn.clicked.connect(lambda: self._guardar(self.tabla.currentRow()))
        v.addWidget(btn)

    def _guardar(self, row):
        if row < 0:
            return
        item = {
            "codigo": self.tabla.item(row, 0).text()
            if self.tabla.item(row, 0)
            else "",
            "nombre": self.tabla.item(row, 1).text()
            if self.tabla.item(row, 1)
            else "",
            "categoria": self.tabla.item(row, 2).text()
            if self.tabla.item(row, 2)
            else "",
            "genero": self.tabla.item(row, 3).text()
            if self.tabla.item(row, 3)
            else "",
        }
        self.seleccion = item
        self.accept()


class CustomProductDialog(QDialog):
    """Diálogo para agregar un producto personalizado o servicio."""

    def __init__(self, parent=None, app_icon: QIcon = QIcon()):
        super().__init__(parent)
        self.setWindowTitle("Agregar producto personalizado o servicio")
        self.resize(420, 260)
        if not app_icon.isNull():
            self.setWindowIcon(app_icon)
        self.resultado = None

        form = QFormLayout(self)
        self.edCodigo = QLineEdit()
        self.edCodigo.setPlaceholderText("Ej: SRV001 o PERS001")
        self.edNombre = QLineEdit()
        self.edNombre.setPlaceholderText("Nombre del producto/servicio")
        self.edObs = QLineEdit()
        self.edObs.setPlaceholderText("Observación (opcional)")
        self.edPrecio = QLineEdit()
        self.edPrecio.setPlaceholderText("Precio unitario")
        self.edPrecio.setText("0.00")
        self.edCant = QLineEdit()
        self.edCant.setPlaceholderText("Cantidad")
        self.edCant.setText("1")
        form.addRow("Código:", self.edCodigo)
        form.addRow("Nombre:", self.edNombre)
        form.addRow("Observación:", self.edObs)
        form.addRow("Precio:", self.edPrecio)
        form.addRow("Cantidad:", self.edCant)

        btnGuardar = QPushButton("Guardar")
        btnGuardar.clicked.connect(self._guardar)
        form.addRow(btnGuardar)

    def _guardar(self):
        from .utils import to_float

        codigo = self.edCodigo.text().strip()
        nombre = self.edNombre.text().strip()
        obs = self.edObs.text().strip()
        precio = to_float(self.edPrecio.text(), 0.0)
        cant = to_float(self.edCant.text(), 1.0)

        if not codigo:
            QMessageBox.warning(
                self,
                "Falta código",
                "Ingrese un código para el producto personalizado.",
            )
            return
        if not nombre:
            QMessageBox.warning(
                self,
                "Falta nombre",
                "Ingrese un nombre para el producto personalizado.",
            )
            return
        if precio < 0:
            QMessageBox.warning(
                self,
                "Precio inválido",
                "El precio no puede ser negativo.",
            )
            return
        if cant <= 0:
            QMessageBox.warning(
                self,
                "Cantidad inválida",
                "La cantidad debe ser mayor que 0.",
            )
            return

        try:
            cant = int(round(float(cant)))
            if cant <= 0:
                cant = 1
        except Exception:
            cant = 1

        self.resultado = {
            "codigo": codigo,
            "nombre": nombre,
            "observacion": obs,
            "precio": float(precio),
            "cantidad": int(cant),
        }
        self.accept()


class ListadoProductosDialog(QDialog):
    """
    Diálogo con pestañas:
      - Productos
      - Presentaciones

    Se muestran según:
      - listing_allows_products()
      - listing_allows_presentations()

    IMPORTANTE:
      - Los productos cuyo id empieza con "PC" y categoría "OTROS"
        se consideran presentaciones → solo aparecen en la pestaña
        "Presentaciones", NO en "Productos".
    """

    def __init__(
        self,
        self_parent,
        productos,
        presentaciones,
        on_select,
        app_icon: QIcon = QIcon(),
    ):
        super().__init__(self_parent)
        self.setWindowTitle("Listado de Productos")
        self.resize(720, 480)
        if not app_icon.isNull():
            self.setWindowIcon(app_icon)
        self._on_select = on_select

        self._rows_prod: list[dict] = []
        self._rows_pres: list[dict] = []

        v = QVBoxLayout(self)

        self.tabs = QTabWidget()
        v.addWidget(self.tabs)

        # --------- Tab PRODUCTOS ---------
        self.tab_prod = None
        self.entry_buscar_prod = None
        self.tabla_prod = None

        if listing_allows_products():
            self.tab_prod = QWidget()
            layout_prod = QVBoxLayout(self.tab_prod)

            self.entry_buscar_prod = QLineEdit()
            self.entry_buscar_prod.setPlaceholderText(
                "Filtrar productos por código, nombre, categoría, precio, stock o género…"
            )
            layout_prod.addWidget(self.entry_buscar_prod)

            self.tabla_prod = QTableWidget(0, 6)
            self.tabla_prod.setHorizontalHeaderLabels(
                ["Código", "Nombre", "Categoría", "Precio", "Stock", "Tipo"]
            )
            self.tabla_prod.horizontalHeader().setSectionResizeMode(
                QHeaderView.Stretch
            )
            self.tabla_prod.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.tabla_prod.setSelectionBehavior(QAbstractItemView.SelectRows)
            layout_prod.addWidget(self.tabla_prod)

            self.tabs.addTab(self.tab_prod, "Productos")

            # Construir filas de productos (Catálogo)
            for p in productos:
                # --- Omitir PCs que serán tratadas como presentaciones ---
                pid = str(p.get("id", "")).upper()
                cat = (p.get("categoria", "") or "").upper()
                if pid.startswith("PC") and cat == "OTROS":
                    continue

                stock = nz(p.get("cantidad_disponible"), 0.0)
                precio = precio_base_para_listado(p)  # en base
                self._rows_prod.append(
                    {
                        "codigo": p.get("id", ""),
                        "nombre": p.get("nombre", ""),
                        "categoria": p.get("categoria", ""),
                        "genero": p.get("genero", ""),
                        "precio": precio,
                        "stock": stock,
                        "tipo": "Catálogo",
                    }
                )

            self._pintar_tabla_prod(self._rows_prod)
            self.entry_buscar_prod.textChanged.connect(self._filtrar_prod)
            self.tabla_prod.cellDoubleClicked.connect(
                lambda row, _col: self._doble_click("prod", row)
            )

        # --------- Tab PRESENTACIONES ---------
        self.tab_pres = None
        self.entry_buscar_pres = None
        self.tabla_pres = None

        if listing_allows_presentations():
            self.tab_pres = QWidget()
            layout_pres = QVBoxLayout(self.tab_pres)

            self.entry_buscar_pres = QLineEdit()
            self.entry_buscar_pres.setPlaceholderText(
                "Filtrar presentaciones por código, nombre, categoría, precio, stock o género…"
            )
            layout_pres.addWidget(self.entry_buscar_pres)

            self.tabla_pres = QTableWidget(0, 6)
            self.tabla_pres.setHorizontalHeaderLabels(
                ["Código", "Nombre", "Categoría", "Precio", "Stock", "Tipo"]
            )
            self.tabla_pres.horizontalHeader().setSectionResizeMode(
                QHeaderView.Stretch
            )
            self.tabla_pres.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.tabla_pres.setSelectionBehavior(QAbstractItemView.SelectRows)
            layout_pres.addWidget(self.tabla_pres)

            self.tabs.addTab(self.tab_pres, "Presentaciones")

            # a) Dataset explícito de presentaciones (Hoja 2)
            for pr in presentaciones or []:
                # Soportar ambos formatos: legacy y nuevo (cargar_presentaciones)
                codigo = (
                    pr.get("id")
                    or pr.get("codigo")
                    or pr.get("CODIGO")
                    or ""
                )
                nombre = (
                    pr.get("nombre")
                    or pr.get("NOMBRE")
                    or ""
                )
                categoria = (
                    pr.get("categoria")
                    or pr.get("departamento")
                    or pr.get("DEPARTAMENTO")
                    or "PRESENTACION"
                )
                genero = pr.get("genero") or pr.get("GENERO") or ""

                codigo = str(codigo).strip()
                nombre = str(nombre).strip()
                categoria = str(categoria).strip() or "PRESENTACION"

                # Saltar filas completamente vacías (sin código ni nombre)
                if not codigo and not nombre:
                    continue

                stock = nz(
                    pr.get("cantidad_disponible")
                    or pr.get("stock_disponible")
                    or pr.get("STOCK")
                    or 0.0,
                    0.0,
                )

                precio = precio_base_para_listado(pr)
                if not precio:
                    # Fallback explícito para presentaciones (PRECIO_PRESENT / p_venta)
                    precio = nz(
                        pr.get("PRECIO_PRESENT")
                        or pr.get("precio_present")
                        or pr.get("p_venta"),
                        0.0,
                    )

                self._rows_pres.append(
                    {
                        "codigo": codigo,
                        "nombre": nombre or codigo,
                        "categoria": categoria or "PRESENTACION",
                        "genero": genero,
                        "precio": float(precio),
                        "stock": stock,
                        "tipo": "Presentación",
                    }
                )

            # b) PCs derivadas de productos
            pcs = [
                p
                for p in productos
                if str(p.get("id", "")).upper().startswith("PC")
                and (p.get("categoria", "").upper() == "OTROS")
            ]
            for pc in pcs:
                stock_to_show = nz(pc.get("cantidad_disponible"), 0.0)
                self._rows_pres.append(
                    {
                        "codigo": pc.get("id", ""),
                        "nombre": f"Presentación (PC) - {pc.get('nombre','')}",
                        "categoria": "PRESENTACION",
                        "genero": pc.get("genero", ""),
                        # base
                        "precio": float(
                            nz(pc.get("precio_unitario", pc.get("precio_venta")))
                        ),
                        "stock": stock_to_show,
                        "tipo": "Presentación",
                    }
                )

            self._pintar_tabla_pres(self._rows_pres)
            self.entry_buscar_pres.textChanged.connect(self._filtrar_pres)
            self.tabla_pres.cellDoubleClicked.connect(
                lambda row, _col: self._doble_click("pres", row)
            )

        # Si no hay pestañas, el widget queda vacío (caso extremo).

    # --------- helpers de pintado / filtro ---------
    def _pintar_tabla_prod(self, rows):
        if not self.tabla_prod:
            return
        self.tabla_prod.setRowCount(0)
        for r in rows:
            i = self.tabla_prod.rowCount()
            self.tabla_prod.insertRow(i)
            self.tabla_prod.setItem(i, 0, QTableWidgetItem(str(r["codigo"])))
            self.tabla_prod.setItem(i, 1, QTableWidgetItem(str(r["nombre"])))
            self.tabla_prod.setItem(i, 2, QTableWidgetItem(str(r["categoria"])))

            precio_base = float(nz(r["precio"], 0.0))
            precio_mostrado = convert_from_base(precio_base)
            self.tabla_prod.setItem(
                i, 3, QTableWidgetItem(fmt_money_ui(precio_mostrado))
            )

            stock_txt = _fmt_trim_decimal(r.get("stock", 0.0))
            self.tabla_prod.setItem(i, 4, QTableWidgetItem(stock_txt))
            self.tabla_prod.setItem(i, 5, QTableWidgetItem(str(r["tipo"])))

    def _pintar_tabla_pres(self, rows):
        if not self.tabla_pres:
            return
        self.tabla_pres.setRowCount(0)
        for r in rows:
            i = self.tabla_pres.rowCount()
            self.tabla_pres.insertRow(i)
            self.tabla_pres.setItem(i, 0, QTableWidgetItem(str(r["codigo"])))
            self.tabla_pres.setItem(i, 1, QTableWidgetItem(str(r["nombre"])))
            self.tabla_pres.setItem(i, 2, QTableWidgetItem(str(r["categoria"])))

            precio_base = float(nz(r["precio"], 0.0))
            precio_mostrado = convert_from_base(precio_base)
            self.tabla_pres.setItem(
                i, 3, QTableWidgetItem(fmt_money_ui(precio_mostrado))
            )

            stock_txt = _fmt_trim_decimal(r.get("stock", 0.0))
            self.tabla_pres.setItem(i, 4, QTableWidgetItem(stock_txt))
            self.tabla_pres.setItem(i, 5, QTableWidgetItem(str(r["tipo"])))

    def _filtrar_prod(self, txt):
        t = (txt or "").lower().strip()
        if not t:
            self._pintar_tabla_prod(self._rows_prod)
            return
        filtrados = []
        for r in self._rows_prod:
            if (
                t in str(r["codigo"]).lower()
                or t in str(r["nombre"]).lower()
                or t in str(r["categoria"]).lower()
                or t in str(r["tipo"]).lower()
                or t in str(r.get("genero", "")).lower()
                or t in str(r["precio"]).lower()
                or t in str(r.get("stock", "")).lower()
            ):
                filtrados.append(r)
        self._pintar_tabla_prod(filtrados)

    def _filtrar_pres(self, txt):
        t = (txt or "").lower().strip()
        if not t:
            self._pintar_tabla_pres(self._rows_pres)
            return
        filtrados = []
        for r in self._rows_pres:
            if (
                t in str(r["codigo"]).lower()
                or t in str(r["nombre"]).lower()
                or t in str(r["categoria"]).lower()
                or t in str(r["tipo"]).lower()
                or t in str(r.get("genero", "")).lower()
                or t in str(r["precio"]).lower()
                or t in str(r.get("stock", "")).lower()
            ):
                filtrados.append(r)
        self._pintar_tabla_pres(filtrados)

    def _doble_click(self, source: str, row: int):
        if row < 0:
            return

        table = None
        if source == "prod":
            table = self.tabla_prod
        elif source == "pres":
            table = self.tabla_pres

        if not table:
            return

        item_cod = table.item(row, 0)
        if not item_cod:
            return
        codigo = item_cod.text().strip()
        if self._on_select:
            self._on_select(codigo)


# ================== Selector de precio (modal, robusto) ==================
# (Trabaja en moneda base internamente; lo que muestra se adapta a la moneda actual)


def show_discount_editor(
    parent,
    app_icon: QIcon,
    base_unit_price: float,
    quantity: float,
    current_pct: float = 0.0,
) -> Optional[dict]:
    """
    Diálogo genérico para configurar descuento de un ítem.

    Trabaja SIEMPRE en moneda base para los cálculos, pero muestra los montos
    convertidos a la moneda actual mediante convert_from_base.

    Devuelve:
      {"pct": float, "amount_base": float, "amount_ui": float}
      o None si se cancela.
    """
    from PySide6.QtWidgets import QDoubleSpinBox

    dlg = QDialog(parent)
    dlg.setWindowTitle("Descuento del ítem")
    dlg.resize(420, 220)
    if not app_icon.isNull():
        dlg.setWindowIcon(app_icon)

    v = QVBoxLayout(dlg)

    # Montos en base y en moneda actual
    base_unit_price = float(nz(base_unit_price, 0.0))
    quantity = float(nz(quantity, 0.0))
    if quantity < 0:
        quantity = 0.0

    total_base = max(base_unit_price * quantity, 0.0)
    unit_ui = convert_from_base(base_unit_price)
    total_ui = convert_from_base(total_base)

    # Info superior
    info = QLabel(
        f"<b>Precio base:</b> {fmt_money_ui(unit_ui)}   "
        f"<b>Cantidad:</b> {quantity}   "
        f"<b>Total base:</b> {fmt_money_ui(total_ui)}"
    )
    info.setTextFormat(Qt.RichText)
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

    # Inicializar con el porcentaje actual
    pct_init = max(0.0, float(nz(current_pct, 0.0)))
    if pct_init > 100.0:
        pct_init = 100.0
    sp_pct.setValue(pct_init)
    descuento_ui_init = total_ui * pct_init / 100.0
    sp_monto.setValue(descuento_ui_init)

    form.addRow("Porcentaje de descuento:", sp_pct)
    form.addRow("Monto descontado:", sp_monto)
    v.addLayout(form)

    # Sincronización porcentajes <-> montos
    guard = {"updating": False}

    def on_pct_changed(val: float):
        if guard["updating"]:
            return
        guard["updating"] = True
        desc_ui = total_ui * float(val) / 100.0
        sp_monto.setValue(desc_ui)
        guard["updating"] = False

    def on_monto_changed(val: float):
        if guard["updating"]:
            return
        guard["updating"] = True
        if total_ui > 0:
            pct = max(0.0, min((float(val) / total_ui) * 100.0, 100.0))
        else:
            pct = 0.0
        sp_pct.setValue(pct)
        guard["updating"] = False

    sp_pct.valueChanged.connect(on_pct_changed)
    sp_monto.valueChanged.connect(on_monto_changed)

    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    v.addWidget(bb)

    payload: dict = {}

    def accept():
        pct = float(sp_pct.value())
        if pct < 0:
            pct = 0.0
        if pct > 100:
            pct = 100.0

        amount_ui = float(sp_monto.value())
        # Convertir a base con la misma proporción
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

    # encabezado
    lbl = QLabel(f"<b>{item.get('codigo','')}</b> — {item.get('producto','')}")
    lbl.setTextFormat(Qt.RichText)
    v.addWidget(lbl)

    prod = item.get("_prod", {}) or {}
    cat = (item.get("categoria") or "").upper()

    # Tiers de catálogo (en base)
    unitario = _first_nonzero(
        prod, ["precio_unidad", "precio_unitario", "precio_venta"]
    )
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
        # Mostrar siempre en moneda actual
        return fmt_money_ui(convert_from_base(val_base)) if val_base > 0 else "—"

    # Tarjetas-botón
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
        price = _format_tier_value(value)
        btn.setText(f"{title}\n{price}")
        btn.setCheckable(True)
        return btn

    btn_u = make_card("Unitario", unitario)
    btn_o = make_card("Oferta", oferta)
    btn_m = make_card("Mínimo", minimo)
    btn_b = make_card("Base", base_val)

    # ---------- Card "Personalizado" con input embebido ----------
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
    from .utils import nz as _nz_loc

    sp.setValue(float(_nz_loc(item.get("precio_override"), item.get("precio", 0.0))))
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
            if b:
                b.setChecked(False)

    btn_c.clicked.connect(on_custom_clicked)

    def sync_custom_text():
        btn_c.setText(custom_text())

    sp.valueChanged.connect(lambda _v: sync_custom_text())

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
            if b:
                b.setChecked(b is btn)
        btn_c.setChecked(False)
        row_input.setVisible(False)

    for b in (btn_u, btn_o, btn_m, btn_b):
        if b:
            b.clicked.connect(lambda _=None, bb=b: pick(bb))

    bb = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
    v.addWidget(bb)

    payload: dict = {"mode": None}

    def accept():
        if btn_c.isChecked():
            payload["mode"] = "custom"
            # Valor introducido por el usuario (en base)
            payload["price"] = float(sp.value())
            dlg.accept()
            return

        if cat == "BOTELLAS":
            if btn_u.isChecked():
                payload.update(
                    {"mode": "tier", "tier": "unitario", "price": unitario}
                )
            elif btn_o.isChecked():
                payload.update(
                    {"mode": "tier", "tier": "oferta", "price": oferta}
                )
            elif btn_m.isChecked():
                payload.update(
                    {"mode": "tier", "tier": "minimo", "price": minimo}
                )
        else:
            if btn_b.isChecked():
                payload.update(
                    {"mode": "tier", "tier": "base", "price": base_val}
                )

        dlg.accept()

    bb.accepted.connect(accept)
    bb.rejected.connect(dlg.reject)

    ok = dlg.exec() == QDialog.Accepted
    if not ok or payload.get("mode") is None:
        return None
    return payload
