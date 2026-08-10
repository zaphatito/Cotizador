# src/widgets_parts/listado_productos_dialog.py
from __future__ import annotations

from collections.abc import Mapping

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QTabWidget,
    QWidget,
    QLineEdit,
    QTableWidget,
    QAbstractItemView,
    QTableWidgetItem,
)

from ..config import (
    listing_allows_products,
    listing_allows_presentations,
    convert_from_base,
)
from ..pricing import precio_base_para_listado
from ..stock_policy import stock_enforcement_enabled
from ..stock_matrix import store_label
from ..utils import fmt_money_ui, nz
from .helpers import _fmt_trim_decimal
from .excel_table_behavior import ExcelTableController
from .bounded_table_columns import install_bounded_columns


class ListadoProductosDialog(QDialog):
    """
    Diálogo con pestañas:
      - Productos
      - Presentaciones

    IMPORTANTE:
      - Los productos cuyo id empieza con "PC" y categoría "OTROS"
        no se muestran en la pestaña de Presentaciones.
      - En modo Excel respeta la política local de stock; en modo servidor es informativo.
    """

    def __init__(
        self,
        self_parent,
        productos,
        presentaciones,
        on_select,
        app_icon: QIcon = QIcon(),
        *,
        converter=None,
        current_currency: str | None = None,
        quote_context=None,
        stock_matrix=None,
    ):
        super().__init__(self_parent)
        self.setWindowTitle("Listado de Productos")
        self.resize(720, 480)
        if not app_icon.isNull():
            self.setWindowIcon(app_icon)
        self._on_select = on_select
        self._convert_from_base = converter if callable(converter) else convert_from_base
        self._current_currency = str(current_currency or "").strip().upper() or None
        self._quote_context = quote_context
        self._stock_matrix = dict(stock_matrix) if isinstance(stock_matrix, Mapping) else {}
        self._stock_stores: list[tuple[str, str]] = []
        for store in self._stock_matrix.get("stores") or []:
            if not isinstance(store, Mapping):
                continue
            store_id = str(store.get("id_tienda") or "").strip()
            if store_id:
                self._stock_stores.append((store_id, store_label(store)))
        self._stock_rows: dict[str, dict] = {}
        for row in self._stock_matrix.get("rows") or []:
            if not isinstance(row, Mapping):
                continue
            code = str(row.get("codigo_norm") or row.get("codigo") or "").strip().upper()
            if code:
                self._stock_rows[code] = dict(row)
        if self._stock_stores:
            self.resize(1120, 560)

        self._rows_prod: list[dict] = []
        self._rows_pres: list[dict] = []
        self._rows_pres_view: list[dict] = []
        self._excel_tables: list[ExcelTableController] = []

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

            self.tabla_prod = QTableWidget(0, 6 + len(self._stock_stores))
            self.tabla_prod.setHorizontalHeaderLabels(
                self._table_headers()
            )
            self._configure_table_header(self.tabla_prod)
            self.tabla_prod.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.tabla_prod.setSelectionBehavior(QAbstractItemView.SelectItems)
            self.tabla_prod.setSelectionMode(QAbstractItemView.ExtendedSelection)
            self.tabla_prod.setAlternatingRowColors(True)
            self.tabla_prod.setShowGrid(False)
            self.tabla_prod.verticalHeader().setVisible(True)
            self._excel_tables.append(
                ExcelTableController(
                    self.tabla_prod,
                    allow_copy=True,
                    allow_paste=False,
                    allow_cut=False,
                    clear_on_delete=False,
                    move_on_enter=True,
                    move_on_tab=True,
                    skip_enter_preview_rows=False,
                )
            )
            layout_prod.addWidget(self.tabla_prod)

            self.tabs.addTab(self.tab_prod, "Productos")

            for p in productos or []:
                stock = float(nz(p.get("cantidad_disponible"), 0.0))

                # ✅ NO mostrar sin stock si está deshabilitado
                if stock_enforcement_enabled(self._quote_context) and stock <= 0.0:
                    continue

                precio = precio_base_para_listado(p)
                stock_row = self._find_stock_row(
                    p.get("id"), p.get("codigo"), p.get("CODIGO")
                )
                if stock_row is not None:
                    stock = float(nz(stock_row.get("total_stock"), stock))
                self._rows_prod.append(
                    {
                        "codigo": p.get("id", ""),
                        "nombre": p.get("nombre", ""),
                        "categoria": p.get("categoria", ""),
                        "genero": p.get("genero", ""),
                        "precio": precio,
                        "stock": stock,
                        "stocks": dict(stock_row.get("stocks") or {}) if stock_row else {},
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

            self.tabla_pres = QTableWidget(0, 6 + len(self._stock_stores))
            self.tabla_pres.setHorizontalHeaderLabels(
                self._table_headers()
            )
            self._configure_table_header(self.tabla_pres)
            self.tabla_pres.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.tabla_pres.setSelectionBehavior(QAbstractItemView.SelectItems)
            self.tabla_pres.setSelectionMode(QAbstractItemView.ExtendedSelection)
            self.tabla_pres.setAlternatingRowColors(True)
            self.tabla_pres.setShowGrid(False)
            self.tabla_pres.verticalHeader().setVisible(True)
            self._excel_tables.append(
                ExcelTableController(
                    self.tabla_pres,
                    allow_copy=True,
                    allow_paste=False,
                    allow_cut=False,
                    clear_on_delete=False,
                    move_on_enter=True,
                    move_on_tab=True,
                    skip_enter_preview_rows=False,
                )
            )
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
                if codigo.upper().startswith("PC"):
                    continue

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
                stock_row = self._find_stock_row(
                    pr.get("presentation_key"),
                    pr.get("codigo_norm"),
                    pr.get("CODIGO_NORM"),
                    codigo,
                )
                if stock_row is not None:
                    stock = float(nz(stock_row.get("total_stock"), stock))

                # ✅ NO mostrar sin stock si está deshabilitado
                if stock_enforcement_enabled(self._quote_context) and stock <= 0.0:
                    continue

                precio = precio_base_para_listado(pr)
                if not precio:
                    precio = nz(
                        pr.get("P_MAX") or pr.get("p_max"),
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
                        "stocks": dict(stock_row.get("stocks") or {}) if stock_row else {},
                        "tipo": "Presentación",
                    }
                )

            self._pintar_tabla_pres(self._rows_pres)
            self.entry_buscar_pres.textChanged.connect(self._filtrar_pres)
            self.tabla_pres.cellDoubleClicked.connect(
                lambda row, _col: self._doble_click("pres", row)
            )

    def _table_headers(self) -> list[str]:
        return [
            "Código",
            "Nombre",
            "Categoría",
            "Precio",
            "Stock total",
            *[label for _store_id, label in self._stock_stores],
            "Tipo",
        ]

    def _find_stock_row(self, *codes) -> dict | None:
        for value in codes:
            code = str(value or "").strip().upper()
            if code and code in self._stock_rows:
                return self._stock_rows[code]
        return None

    def _configure_table_header(self, table: QTableWidget) -> None:
        install_bounded_columns(table, minimum_section_size=40, fill_column=1)

    def _paint_stock_cells(self, table: QTableWidget, row_index: int, row: dict) -> None:
        stocks = row.get("stocks") if isinstance(row.get("stocks"), Mapping) else {}
        for offset, (store_id, _label) in enumerate(self._stock_stores, start=5):
            value = stocks.get(store_id)
            text = "—" if value is None else _fmt_trim_decimal(value)
            item = QTableWidgetItem(text)
            item.setTextAlignment(Qt.AlignCenter)
            table.setItem(row_index, offset, item)
        table.setItem(
            row_index,
            5 + len(self._stock_stores),
            QTableWidgetItem(str(row["tipo"])),
        )

    def _pintar_tabla_prod(self, rows):
        if not self.tabla_prod:
            return
        rows_view = list(rows or [])
        self.tabla_prod.setUpdatesEnabled(False)
        self.tabla_prod.setRowCount(len(rows_view))
        for i, r in enumerate(rows_view):
            self.tabla_prod.setItem(i, 0, QTableWidgetItem(str(r["codigo"])))
            self.tabla_prod.setItem(i, 1, QTableWidgetItem(str(r["nombre"])))
            self.tabla_prod.setItem(i, 2, QTableWidgetItem(str(r["categoria"])))

            precio_base = float(nz(r["precio"], 0.0))
            precio_mostrado = self._convert_from_base(precio_base)
            self.tabla_prod.setItem(
                i,
                3,
                QTableWidgetItem(
                    fmt_money_ui(precio_mostrado, currency=self._current_currency)
                ),
            )

            stock_txt = _fmt_trim_decimal(r.get("stock", 0.0))
            self.tabla_prod.setItem(i, 4, QTableWidgetItem(stock_txt))
            self._paint_stock_cells(self.tabla_prod, i, r)
        self.tabla_prod.setUpdatesEnabled(True)

    def _pintar_tabla_pres(self, rows):
        if not self.tabla_pres:
            return
        self._rows_pres_view = list(rows or [])
        self.tabla_pres.setUpdatesEnabled(False)
        self.tabla_pres.setRowCount(len(self._rows_pres_view))
        for i, r in enumerate(self._rows_pres_view):
            self.tabla_pres.setItem(i, 0, QTableWidgetItem(str(r["codigo"])))
            self.tabla_pres.setItem(i, 1, QTableWidgetItem(str(r["nombre"])))
            self.tabla_pres.setItem(i, 2, QTableWidgetItem(str(r["categoria"])))

            precio_base = float(nz(r["precio"], 0.0))
            precio_mostrado = self._convert_from_base(precio_base)
            self.tabla_pres.setItem(
                i,
                3,
                QTableWidgetItem(
                    fmt_money_ui(precio_mostrado, currency=self._current_currency)
                ),
            )

            stock_txt = _fmt_trim_decimal(r.get("stock", 0.0))
            self.tabla_pres.setItem(i, 4, QTableWidgetItem(stock_txt))
            self._paint_stock_cells(self.tabla_pres, i, r)
        self.tabla_pres.setUpdatesEnabled(True)

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
            if source == "pres":
                payload = (
                    self._rows_pres_view[row]
                    if 0 <= row < len(self._rows_pres_view)
                    else {"codigo": codigo}
                )
                self._on_select(payload)
                return
            self._on_select(codigo)
