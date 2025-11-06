# src/widgets.py
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLineEdit, QTableWidget, QHeaderView, QAbstractItemView,
    QTableWidgetItem, QPushButton, QLabel, QFormLayout
)
from PySide6.QtGui import QIcon
from PySide6.QtCore import Qt

from .config import listing_allows_products, listing_allows_presentations, ALLOW_NO_STOCK
from .pricing import precio_base_para_listado
from .utils import fmt_money_ui, nz
from .presentations import map_pc_to_bottle_code


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

        def doble_click(row, _col):
            self._guardar(row)
        self.tabla.cellDoubleClicked.connect(doble_click)

        btn = QPushButton("Seleccionar")
        btn.clicked.connect(lambda: self._guardar(self.tabla.currentRow()))
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
            QMessageBox.warning(self, "Falta código", "Ingrese un código para el producto personalizado.")
            return
        if not nombre:
            QMessageBox.warning(self, "Falta nombre", "Ingrese un nombre para el producto personalizado.")
            return
        if precio < 0:
            QMessageBox.warning(self, "Precio inválido", "El precio no puede ser negativo.")
            return
        if cant <= 0:
            QMessageBox.warning(self, "Cantidad inválida", "La cantidad debe ser mayor que 0.")
            return

        # Cantidad entera (no granel)
        try:
            cant = int(round(float(cant)))
            if cant <= 0: cant = 1
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
    def __init__(self, parent, productos, presentaciones, on_select, app_icon: QIcon = QIcon()):
        super().__init__(parent)
        self.setWindowTitle("Listado de Productos")
        self.resize(720, 480)
        if not app_icon.isNull():
            self.setWindowIcon(app_icon)
        self._on_select = on_select

        v = QVBoxLayout(self)

        self.entry_buscar = QLineEdit()
        self.entry_buscar.setPlaceholderText("Filtrar por código, nombre, categoría, precio, stock o género…")
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
                stock = int(nz(p.get("cantidad_disponible"), 0))
                if stock <= 0 and not ALLOW_NO_STOCK:
                    continue
                precio = precio_base_para_listado(p)
                self._rows.append({
                    "codigo": p.get("id", ""),
                    "nombre": p.get("nombre", ""),
                    "categoria": p.get("categoria", ""),
                    "genero": p.get("genero", ""),
                    "precio": precio,
                    "stock": stock,
                    "tipo": "Catálogo"
                })

        if listing_allows_presentations():
            pcs = [
                p for p in productos
                if str(p.get("id", "")).upper().startswith("PC")
                and (p.get("categoria", "").upper() == "OTROS")
            ]
            for pc in pcs:
                bot_code = map_pc_to_bottle_code(pc.get("id", ""))
                bot = next(
                    (b for b in productos
                     if str(b.get("id", "")).upper() == (bot_code or "").upper()
                     and (b.get("categoria", "").upper() == "BOTELLAS")),
                    None
                )
                bot_stock = int(nz(bot.get("cantidad_disponible"), 0)) if bot else None
                if bot is not None and bot_stock <= 0 and not ALLOW_NO_STOCK:
                    continue

                stock_to_show = bot_stock if bot is not None else int(nz(pc.get("cantidad_disponible"), 0))

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
            self.tabla.setItem(i, 4, QTableWidgetItem(str(int(nz(r.get("stock", 0))))))
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
