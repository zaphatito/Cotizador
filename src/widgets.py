from __future__ import annotations
from decimal import Decimal, InvalidOperation

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLineEdit, QTableWidget, QHeaderView, QAbstractItemView,
    QTableWidgetItem, QPushButton, QLabel, QFormLayout, QHBoxLayout, QGroupBox,
    QDoubleSpinBox, QDialogButtonBox, QFrame, QWidget, QTabWidget
)
from PySide6.QtGui import QIcon
from PySide6.QtCore import Qt

from .config import (
    listing_allows_products,
    listing_allows_presentations,
    ALLOW_NO_STOCK,
    convert_from_base,
)
from .pricing import precio_base_para_listado
    # asegúrate que soporte PRECIO_PRESENT para presentaciones
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
        from PySide6.QtWidgets import QMessageBox

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
        parent,
        productos,
        presentaciones,
        on_select,
        app_icon: QIcon = QIcon(),
    ):
        super().__init__(parent)
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


def show_discount_editor(
    parent,
    app_icon: QIcon,
    base_unit_price: float,
    quantity: float,
    current_pct: float = 0.0,
) -> dict | None:
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
    # Más precisión en el porcentaje para poder clavar montos redondos
    sp_pct.setDecimals(6)
    sp_pct.setSingleStep(0.0001)
    sp_pct.setRange(0.0, 100.0)
    sp_pct.setSuffix(" %")

    sp_monto = QDoubleSpinBox()
    sp_monto.setDecimals(2)  # montos siguen con 2 decimales
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

    payload = {}

    def accept():
        pct = float(sp_pct.value())
        if pct < 0:
            pct = 0.0
        if pct > 100:
            pct = 100.0

        amount_ui = float(sp_monto.value())
        # Convertir a base con la misma proporción
        amount_base = total_base * pct / 100.0 if total_base > 0 else 0.0

        payload["pct"] = pct              # ahora con muchos más decimales
        payload["amount_ui"] = amount_ui
        payload["amount_base"] = amount_base
        dlg.accept()

    bb.accepted.connect(accept)
    bb.rejected.connect(dlg.reject)

    if dlg.exec() != QDialog.Accepted:
        return None
    return payload


# ================== Selector de precio (modal, robusto) ==================
# (Trabaja en moneda base internamente; lo que muestra se adapta a la moneda actual)

def show_price_picker(parent, app_icon: QIcon, item: dict) -> dict | None:
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

    payload = {"mode": None}

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
