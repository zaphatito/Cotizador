# src/widgets_parts/listado_productos_dialog.py
from __future__ import annotations

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QTabWidget,
    QWidget,
    QLineEdit,
    QTableWidget,
    QHeaderView,
    QAbstractItemView,
    QTableWidgetItem,
)

from ..config import (
    listing_allows_products,
    listing_allows_presentations,
    convert_from_base,
    ALLOW_NO_STOCK,  # ✅
)
from ..pricing import precio_base_para_listado
from ..utils import fmt_money_ui, nz
from .helpers import _fmt_trim_decimal


class ListadoProductosDialog(QDialog):
    """
    Diálogo con pestañas:
      - Productos
      - Presentaciones

    IMPORTANTE:
      - Los productos cuyo id empieza con "PC" y categoría "OTROS"
        se consideran presentaciones → solo aparecen en la pestaña
        "Presentaciones", NO en "Productos".
      - Si NOT ALLOW_NO_STOCK: no se listan items con stock <= 0
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
            self.tabla_prod.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
            self.tabla_prod.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.tabla_prod.setSelectionBehavior(QAbstractItemView.SelectRows)
            layout_prod.addWidget(self.tabla_prod)

            self.tabs.addTab(self.tab_prod, "Productos")

            for p in productos or []:
                pid = str(p.get("id", "")).upper()
                cat = (p.get("categoria", "") or "").upper()
                if pid.startswith("PC") and cat == "OTROS":
                    continue

                stock = float(nz(p.get("cantidad_disponible"), 0.0))

                # ✅ NO mostrar sin stock si está deshabilitado
                if (not ALLOW_NO_STOCK) and stock <= 0.0:
                    continue

                precio = precio_base_para_listado(p)
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
            self.tabla_pres.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
            self.tabla_pres.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.tabla_pres.setSelectionBehavior(QAbstractItemView.SelectRows)
            layout_pres.addWidget(self.tabla_pres)

            self.tabs.addTab(self.tab_pres, "Presentaciones")

            for pr in presentaciones or []:
                codigo = pr.get("id") or pr.get("codigo") or pr.get("CODIGO") or ""
                nombre = pr.get("nombre") or pr.get("NOMBRE") or ""
                categoria = pr.get("categoria") or pr.get("departamento") or pr.get("DEPARTAMENTO") or "PRESENTACION"
                genero = pr.get("genero") or pr.get("GENERO") or ""

                codigo = str(codigo).strip()
                nombre = str(nombre).strip()
                categoria = str(categoria).strip() or "PRESENTACION"

                if not codigo and not nombre:
                    continue

                stock = float(
                    nz(
                        pr.get("cantidad_disponible")
                        or pr.get("stock_disponible")
                        or pr.get("STOCK")
                        or 0.0,
                        0.0,
                    )
                )

                # ✅ NO mostrar sin stock si está deshabilitado
                if (not ALLOW_NO_STOCK) and stock <= 0.0:
                    continue

                precio = precio_base_para_listado(pr)
                if not precio:
                    precio = nz(
                        pr.get("PRECIO_PRESENT") or pr.get("precio_present") or pr.get("p_venta"),
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

            # PCs (presentaciones desde productos con id PC* y cat OTROS)
            pcs = [
                p
                for p in (productos or [])
                if str(p.get("id", "")).upper().startswith("PC")
                and (p.get("categoria", "").upper() == "OTROS")
            ]
            for pc in pcs:
                stock_to_show = float(nz(pc.get("cantidad_disponible"), 0.0))

                # ✅ NO mostrar sin stock si está deshabilitado
                if (not ALLOW_NO_STOCK) and stock_to_show <= 0.0:
                    continue

                self._rows_pres.append(
                    {
                        "codigo": pc.get("id", ""),
                        "nombre": f"Presentación (PC) - {pc.get('nombre','')}",
                        "categoria": "PRESENTACION",
                        "genero": pc.get("genero", ""),
                        "precio": float(nz(pc.get("precio_unitario", pc.get("precio_venta")), 0.0)),
                        "stock": stock_to_show,
                        "tipo": "Presentación",
                    }
                )

            self._pintar_tabla_pres(self._rows_pres)
            self.entry_buscar_pres.textChanged.connect(self._filtrar_pres)
            self.tabla_pres.cellDoubleClicked.connect(
                lambda row, _col: self._doble_click("pres", row)
            )

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
            self.tabla_prod.setItem(i, 3, QTableWidgetItem(fmt_money_ui(precio_mostrado)))

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
            self.tabla_pres.setItem(i, 3, QTableWidgetItem(fmt_money_ui(precio_mostrado)))

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

        table = self.tabla_prod if source == "prod" else self.tabla_pres if source == "pres" else None
        if not table:
            return

        item_cod = table.item(row, 0)
        if not item_cod:
            return

        codigo = item_cod.text().strip()
        if self._on_select:
            self._on_select(codigo)
