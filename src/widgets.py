# src/widgets.py
from __future__ import annotations
from decimal import Decimal, InvalidOperation

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLineEdit, QTableWidget, QHeaderView, QAbstractItemView,
    QTableWidgetItem, QPushButton, QLabel, QFormLayout, QHBoxLayout, QGroupBox,
    QDoubleSpinBox, QDialogButtonBox, QFrame, QWidget
)
from PySide6.QtGui import QIcon
from PySide6.QtCore import Qt

from .config import listing_allows_products, listing_allows_presentations, ALLOW_NO_STOCK
from .pricing import precio_base_para_listado
from .utils import fmt_money_ui, nz
from .presentations import map_pc_to_bottle_code

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
            v = float(nz(d.get(k), 0.0))
        except Exception:
            v = 0.0
        if v > 0:
            return v
    return 0.0


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
        self.entry_buscar = QLineEdit(); self.entry_buscar.setPlaceholderText("Filtrar…")
        v.addWidget(self.entry_buscar)

        self.tabla = QTableWidget(0, 4)
        self.tabla.setHorizontalHeaderLabels(["Código", "Nombre", "Departamento", "Género"])
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
                self.tabla.setItem(i, 0, QTableWidgetItem(str(r.get("codigo",""))))
                self.tabla.setItem(i, 1, QTableWidgetItem(str(r.get("nombre",""))))
                self.tabla.setItem(i, 2, QTableWidgetItem(str(r.get("categoria",""))))
                self.tabla.setItem(i, 3, QTableWidgetItem(str(r.get("genero",""))))
        self._pintar = pintar
        self._pintar(self._rows)

        def filtrar(txt):
            t = txt.lower().strip()
            if not t:
                self._pintar(self._rows); return
            filtrados = []
            for r in self._rows:
                if (
                    t in str(r.get("codigo","")).lower()
                    or t in str(r.get("nombre","")).lower()
                    or t in str(r.get("categoria","")).lower()
                    or t in str(r.get("genero","")).lower()
                ):
                    filtrados.append(r)
            self._pintar(filtrados)
        self.entry_buscar.textChanged.connect(filtrar)

        self.tabla.cellDoubleClicked.connect(lambda row, _col: self._guardar(row))
        btn = QPushButton("Seleccionar"); btn.clicked.connect(lambda: self._guardar(self.tabla.currentRow()))
        v.addWidget(btn)

    def _guardar(self, row):
        if row < 0: return
        item = {
            "codigo": self.tabla.item(row, 0).text() if self.tabla.item(row, 0) else "",
            "nombre": self.tabla.item(row, 1).text() if self.tabla.item(row, 1) else "",
            "categoria": self.tabla.item(row, 2).text() if self.tabla.item(row, 2) else "",
            "genero": self.tabla.item(row, 3).text() if self.tabla.item(row, 3) else "",
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
        self.edCodigo = QLineEdit(); self.edCodigo.setPlaceholderText("Ej: SRV001 o PERS001")
        self.edNombre = QLineEdit(); self.edNombre.setPlaceholderText("Nombre del producto/servicio")
        self.edObs    = QLineEdit(); self.edObs.setPlaceholderText("Observación (opcional)")
        self.edPrecio = QLineEdit(); self.edPrecio.setPlaceholderText("Precio unitario"); self.edPrecio.setText("0.00")
        self.edCant   = QLineEdit(); self.edCant.setPlaceholderText("Cantidad"); self.edCant.setText("1")
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
        from PySide6.QtWidgets import QMessageBox
        codigo = self.edCodigo.text().strip()
        nombre = self.edNombre.text().strip()
        obs    = self.edObs.text().strip()
        precio = to_float(self.edPrecio.text(), 0.0)
        cant   = to_float(self.edCant.text(), 1.0)
        if not codigo:
            QMessageBox.warning(self, "Falta código", "Ingrese un código para el producto personalizado."); return
        if not nombre:
            QMessageBox.warning(self, "Falta nombre", "Ingrese un nombre para el producto personalizado."); return
        if precio < 0:
            QMessageBox.warning(self, "Precio inválido", "El precio no puede ser negativo."); return
        if cant <= 0:
            QMessageBox.warning(self, "Cantidad inválida", "La cantidad debe ser mayor que 0."); return
        try:
            cant = int(round(float(cant)))
            if cant <= 0: cant = 1
        except Exception:
            cant = 1
        self.resultado = {"codigo": codigo, "nombre": nombre, "observacion": obs, "precio": float(precio), "cantidad": int(cant)}
        self.accept()


class ListadoProductosDialog(QDialog):
    def __init__(self, parent, productos, presentaciones, on_select, app_icon: QIcon = QIcon()):
        super().__init__(parent)
        self.setWindowTitle("Listado de Productos")
        self.resize(720, 480)
        if not app_icon.isNull():
            self.setWindowIcon(app_icon)
        self._on_select = on_select

        v = QVBoxLayout(self)
        self.entry_buscar = QLineEdit(); self.entry_buscar.setPlaceholderText("Filtrar por código, nombre, categoría, precio, stock o género…")
        v.addWidget(self.entry_buscar)

        self.tabla = QTableWidget(0, 6)
        self.tabla.setHorizontalHeaderLabels(["Código", "Nombre", "Categoría", "Precio", "Stock", "Tipo"])
        self.tabla.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tabla.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tabla.setSelectionBehavior(QAbstractItemView.SelectRows)
        v.addWidget(self.tabla)

        self._rows = []
        if listing_allows_products():
            for p in productos:
                stock = nz(p.get("cantidad_disponible"), 0.0)
                precio = precio_base_para_listado(p)
                self._rows.append({
                    "codigo": p.get("id", ""), "nombre": p.get("nombre", ""), "categoria": p.get("categoria", ""),
                    "genero": p.get("genero", ""), "precio": precio, "stock": stock, "tipo": "Catálogo"
                })
        if listing_allows_presentations():
            pcs = [
                p for p in productos
                if str(p.get("id", "")).upper().startswith("PC")
                and (p.get("categoria", "").upper() == "OTROS")
            ]
            for pc in pcs:
                stock_to_show = nz(pc.get("cantidad_disponible"), 0.0)
                self._rows.append({
                    "codigo": pc.get("id", ""),
                    "nombre": f"Presentación (PC) - {pc.get('nombre','')}",
                    "categoria": "PRESENTACION",
                    "genero": pc.get("genero",""),
                    "precio": float(nz(pc.get("precio_unitario", pc.get("precio_venta")))),
                    "stock": stock_to_show,
                    "tipo": "Presentación"
                })

        self._pintar_tabla(self._rows)
        self.entry_buscar.textChanged.connect(self._filtrar)
        self.tabla.cellDoubleClicked.connect(self._doble_click)

    def _pintar_tabla(self, rows):
        self.tabla.setRowCount(0)
        for r in rows:
            i = self.tabla.rowCount()
            self.tabla.insertRow(i)
            self.tabla.setItem(i, 0, QTableWidgetItem(str(r["codigo"])))
            self.tabla.setItem(i, 1, QTableWidgetItem(str(r["nombre"])))
            self.tabla.setItem(i, 2, QTableWidgetItem(str(r["categoria"])))
            self.tabla.setItem(i, 3, QTableWidgetItem(fmt_money_ui(nz(r["precio"], 0.0))))
            stock_txt = _fmt_trim_decimal(r.get("stock", 0.0))
            self.tabla.setItem(i, 4, QTableWidgetItem(stock_txt))
            self.tabla.setItem(i, 5, QTableWidgetItem(str(r["tipo"])))

    def _filtrar(self, txt):
        t = txt.lower().strip()
        if not t:
            self._pintar_tabla(self._rows); return
        filtrados = []
        for r in self._rows:
            if (
                t in str(r["codigo"]).lower()
                or t in str(r["nombre"]).lower()
                or t in str(r["categoria"]).lower()
                or t in str(r["tipo"]).lower()
                or t in str(r.get("genero","")).lower()
                or t in str(r["precio"]).lower()
                or t in str(r.get("stock","")).lower()
            ):
                filtrados.append(r)
        self._pintar_tabla(filtrados)

    def _doble_click(self, row, _col):
        item_cod = self.tabla.item(row, 0)
        if not item_cod: return
        codigo = item_cod.text().strip()
        if self._on_select:
            self._on_select(codigo)


# ================== Selector de precio (modal, robusto) ==================

def show_price_picker(parent, app_icon: QIcon, item: dict) -> dict | None:
    """
    Devuelve:
      {"mode":"tier", "tier": "unitario|oferta|minimo|base", "price": float? }
      {"mode":"custom","price": float}
      o None si se cancela.
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
    cat  = (item.get("categoria") or "").upper()

    # Tiers de catálogo
    unitario = _first_nonzero(prod, ["precio_unidad", "precio_unitario", "precio_venta"])
    oferta   = _first_nonzero(prod, [
        "precio_oferta", "precio_oferta_base", "oferta",
        ">12 unidades", "precio_12", "precio_12_unidades", "mayor_12", "mayor12", "docena", "precio_mayorista"
    ])
    minimo   = _first_nonzero(prod, [
        "precio_minimo", "precio_minimo_base", "minimo",
        ">100 unidades", "precio_100", "precio_100_unidades", "mayor_100", "ciento"
    ])
    base_val = _first_nonzero(prod, ["precio_unitario", "precio_unidad", "precio_base_50g", "precio_venta"])

    box = QGroupBox("Elige un precio")
    grid = QHBoxLayout(box)

    # Tarjetas-botón (cards)
    def make_card(title: str, value: float):
        btn = QPushButton()
        btn.setCursor(Qt.PointingHandCursor)
        btn.setEnabled(value > 0.0)
        btn.setMinimumHeight(72)
        btn.setStyleSheet("""
            QPushButton {
                border: 1px solid #bbb; border-radius: 8px; padding: 10px 16px; text-align: left;
            }
            QPushButton:disabled { color: #888; border-color: #ddd; }
            QPushButton:checked  { border: 2px solid #2d7; }
        """)
        price = fmt_money_ui(value) if value > 0 else "—"
        btn.setText(f"{title}\n{price}")
        btn.setCheckable(True)
        return btn

    # BOTELLAS: Unitario / Oferta / Mínimo
    btn_u = make_card("Unitario", unitario)
    btn_o = make_card("Oferta",   oferta)
    btn_m = make_card("Mínimo",   minimo)

    # OTRAS CATEGORÍAS: Base
    btn_b = make_card("Base",     base_val)

    # ---------- Card "Personalizado" con input embebido ----------
    # Card contenedora (frame) con estilo de tarjeta
    card_custom = QFrame()
    card_custom.setStyleSheet("""
        QFrame#customCard {
            border: 1px solid #bbb; border-radius: 8px;
        }
    """)
    card_custom.setObjectName("customCard")
    custom_layout = QVBoxLayout(card_custom); custom_layout.setContentsMargins(10, 10, 10, 10)

    # Botón "card" (checkable)
    btn_c = QPushButton()
    btn_c.setCursor(Qt.PointingHandCursor)
    btn_c.setCheckable(True)
    btn_c.setMinimumHeight(72)
    btn_c.setStyleSheet("""
        QPushButton {
            border: none; text-align: left; padding: 0;
        }
        QPushButton:checked { }
    """)

    # Spin oculto hasta que se escoja "Personalizado"
    row_input = QWidget()
    row_input_layout = QHBoxLayout(row_input); row_input_layout.setContentsMargins(0, 6, 0, 0)
    row_input_layout.addWidget(QLabel("Monto:"))
    sp = QDoubleSpinBox()
    sp.setDecimals(4); sp.setMinimum(0.0); sp.setMaximum(999999999.0); sp.setButtonSymbols(QDoubleSpinBox.NoButtons)
    sp.setValue(float(nz(item.get("precio_override"), item.get("precio", 0.0))))
    row_input_layout.addWidget(sp, 1)
    row_input.setVisible(False)  # ⬅️ oculto por defecto

    def custom_text():
        return f"Personalizado\n{fmt_money_ui(float(sp.value()))}"
    btn_c.setText(custom_text())

    # Ensamble de la card
    custom_layout.addWidget(btn_c)
    custom_layout.addWidget(row_input)

    # Reaccionar a selección y a cambios de valor
    def on_custom_clicked(_checked: bool):
        row_input.setVisible(btn_c.isChecked())
        # des-seleccionar las otras cards
        for b in (btn_u, btn_o, btn_m, btn_b):
            if b: b.setChecked(False)
    btn_c.clicked.connect(on_custom_clicked)

    def sync_custom_text():
        btn_c.setText(custom_text())
    sp.valueChanged.connect(lambda _v: sync_custom_text())

    # Layout por categoría
    if cat == "BOTELLAS":
        grid.addWidget(btn_u); grid.addWidget(btn_o); grid.addWidget(btn_m)
    else:
        grid.addWidget(btn_b)

    v.addWidget(box)
    v.addWidget(card_custom)

    # Estado inicial: respeta tier/override; si hay override -> personalizado visible
    cur_tier = (item.get("precio_tier") or "").upper()
    if item.get("precio_override") is not None:
        btn_c.setChecked(True); row_input.setVisible(True)
    elif cat == "BOTELLAS":
        if cur_tier == "OFERTA" and btn_o.isEnabled(): btn_o.setChecked(True)
        elif cur_tier == "MINIMO" and btn_m.isEnabled(): btn_m.setChecked(True)
        elif cur_tier == "UNITARIO" and btn_u.isEnabled(): btn_u.setChecked(True)
        elif btn_u.isEnabled(): btn_u.setChecked(True)
    else:
        if cur_tier == "BASE" and btn_b.isEnabled(): btn_b.setChecked(True)
        elif btn_b.isEnabled(): btn_b.setChecked(True)

    # Al hacer click en cualquier tarjeta normal, des-selecciona personalizado y oculta input
    def pick(btn):
        # marcar el elegido y desmarcar los demás
        for b in (btn_u, btn_o, btn_m, btn_b):
            if b: b.setChecked(b is btn)
        # desactivar personalizado
        btn_c.setChecked(False)
        row_input.setVisible(False)

    for b in (btn_u, btn_o, btn_m, btn_b):
        if b:
            b.clicked.connect(lambda _=None, bb=b: pick(bb))

    # Botonera
    bb = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
    v.addWidget(bb)

    payload = {"mode": None}

    def accept():
        # Personalizado
        if btn_c.isChecked():
            payload["mode"] = "custom"
            payload["price"] = float(sp.value())
            dlg.accept(); return

        # BOTELLAS
        if cat == "BOTELLAS":
            if btn_u.isChecked():
                payload.update({"mode":"tier", "tier":"unitario", "price": unitario})
            elif btn_o.isChecked():
                payload.update({"mode":"tier", "tier":"oferta",   "price": oferta})
            elif btn_m.isChecked():
                payload.update({"mode":"tier", "tier":"minimo",   "price": minimo})
        else:
            # OTRAS CATEGORÍAS
            if btn_b.isChecked():
                payload.update({"mode":"tier", "tier":"base",     "price": base_val})

        dlg.accept()

    bb.accepted.connect(accept)
    bb.rejected.connect(dlg.reject)

    ok = dlg.exec() == QDialog.Accepted
    if not ok or payload.get("mode") is None:
        return None
    return payload
