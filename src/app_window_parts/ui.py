# src/app_window_parts/ui.py
from __future__ import annotations

import os

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QMessageBox,
    QGroupBox,
    QHeaderView,
    QAbstractItemView,
    QTableView,
    QMenu,
    QDialog,
    QToolButton,
    QSizePolicy,
    QApplication,
    QAbstractItemDelegate,
    QButtonGroup,
)
from PySide6.QtGui import QAction, QKeySequence, QShortcut, QDesktopServices
from PySide6.QtCore import Qt, QUrl, QModelIndex, QTimer

from ..paths import BASE_APP_TITLE, DATA_DIR, COTIZACIONES_DIR
from ..config import APP_COUNTRY, id_label_for_country
from ..models import ItemsModel
from .delegates import QuantityDelegate
from ..widgets import Toast


class UiMixin:
    def _update_title_with_client(self, text: str):
        name = (text or "").strip()
        self.setWindowTitle(f"{name} - {BASE_APP_TITLE}" if name else BASE_APP_TITLE)

    def _center_on_screen(self):
        scr = self.screen()
        if not scr:
            return
        geo = self.frameGeometry()
        center = scr.availableGeometry().center()
        geo.moveCenter(center)
        self.move(geo.topLeft())

    def showEvent(self, event):
        super().showEvent(event)
        if not self._shown_once:
            self._shown_once = True
            self._center_on_screen()

    # =============================
    # FLUJO ENTER ENTRE INPUTS
    # =============================
    def _wire_enter_flow(self):
        try:
            self.entry_cliente.returnPressed.connect(self._go_doc)
            self.entry_cedula.returnPressed.connect(self._go_phone)
            self.entry_telefono.returnPressed.connect(self._go_product_search)
        except Exception:
            pass

    def _go_doc(self):
        try:
            self.entry_cedula.setFocus()
            self.entry_cedula.selectAll()
        except Exception:
            pass

    def _go_phone(self):
        try:
            self.entry_telefono.setFocus()
            self.entry_telefono.selectAll()
        except Exception:
            pass

    def _go_product_search(self):
        self._focus_product_search(clear=True)

    def _focus_product_search(self, *, clear: bool = False):
        try:
            self.entry_producto.setFocus()
            if clear:
                self.entry_producto.clear()
            self.entry_producto.selectAll()
        except Exception:
            pass

    # =============================
    # Foco última fila (editar qty)
    # =============================
    def _focus_last_row(self, row_index: int):
        try:
            r = row_index if isinstance(row_index, int) else (self.model.rowCount() - 1)
            if r < 0:
                return

            idx_qty = self.model.index(r, 3)

            self.table.selectRow(r)
            self.table.setCurrentIndex(idx_qty)
            self.table.scrollTo(idx_qty, QAbstractItemView.PositionAtBottom)

            if QApplication.activeModalWidget() is None:
                self.table.setFocus()

            QTimer.singleShot(0, lambda: self.table.edit(idx_qty))

        except Exception:
            pass

    def _on_qty_editor_closed(self, editor, hint):
        try:
            idx = self.table.currentIndex()
            if not idx.isValid():
                return
            if idx.column() != 3:
                return

            if hint not in (
                QAbstractItemDelegate.EditNextItem,
                QAbstractItemDelegate.NoHint,
                QAbstractItemDelegate.SubmitModelCache,
            ):
                return
        except Exception:
            pass

        QTimer.singleShot(0, lambda: self._focus_product_search(clear=True))

    # =============================
    # Pago PY: Tarjeta / Efectivo
    # =============================
    def _is_py_cash_mode(self) -> bool:
        return bool(getattr(self, "_py_cash_mode", False))

    def _set_py_cash_mode(self, enabled: bool, *, assume_items_already: bool = False):
        """
        assume_items_already=True:
          - Usar al reabrir desde histórico.
          - No re-aplica BASE, solo sincroniza para evitar duplicado.
        """
        self._py_cash_mode = bool(enabled)
        try:
            if hasattr(self, "model") and self.model is not None:
                self.model.set_py_cash_mode(self._py_cash_mode, assume_items_already=assume_items_already)
        except Exception:
            pass

    def _on_py_payment_clicked(self, btn: QPushButton):
        if not btn:
            return
        is_cash = (btn is getattr(self, "btn_pay_cash", None))
        self._set_py_cash_mode(is_cash)

    # =============================
    # Pago PE: input libre (observación)
    # =============================
    def _get_pe_payment_text(self) -> str:
        try:
            if getattr(self, "entry_metodo_pago", None) is None:
                return ""
            return (self.entry_metodo_pago.text() or "").strip()
        except Exception:
            return ""

    def _set_pe_payment_text(self, text: str):
        try:
            if getattr(self, "entry_metodo_pago", None) is None:
                return
            self.entry_metodo_pago.setText((text or "").strip())
        except Exception:
            pass

    # =============================
    # UI helpers
    # =============================
    def abrir_carpeta_data(self):
        if not os.path.isdir(DATA_DIR):
            QMessageBox.warning(self, "Carpeta no encontrada", f"No se encontró la carpeta:\n{DATA_DIR}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(DATA_DIR)))

    def abrir_carpeta_cotizaciones(self):
        if not os.path.isdir(COTIZACIONES_DIR):
            QMessageBox.warning(
                self,
                "Carpeta no encontrada",
                f"No se encontró la carpeta:\n{COTIZACIONES_DIR}",
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(COTIZACIONES_DIR)))

    def _apply_btn_responsive(self, btn: QPushButton, min_w: int = 80, min_h: int = 28):
        sp = QSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        btn.setSizePolicy(sp)
        btn.setMinimumSize(min_w, min_h)
        btn.setAutoDefault(False)
        btn.setDefault(False)
        btn.setFlat(False)
        btn.setCursor(Qt.PointingHandCursor)

    def _make_tool_icon(self, text: str, tooltip: str, on_click):
        tbtn = QToolButton()
        tbtn.setText(text)
        tbtn.setToolTip(tooltip)
        tbtn.setAutoRaise(True)
        tbtn.setToolButtonStyle(Qt.ToolButtonTextOnly)
        tbtn.setCursor(Qt.PointingHandCursor)
        tbtn.setFixedSize(34, 34)
        tbtn.clicked.connect(on_click)
        return tbtn

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main = QVBoxLayout(central)

        # --- Cliente
        grp_cli = QGroupBox("Datos del Cliente")
        form_cli = QFormLayout()
        self.entry_cliente = QLineEdit()
        self.entry_cedula = QLineEdit()
        self.entry_telefono = QLineEdit()
        self.lbl_doc = QLabel(id_label_for_country(APP_COUNTRY) + ":")
        form_cli.addRow("Nombre Completo:", self.entry_cliente)
        form_cli.addRow(self.lbl_doc, self.entry_cedula)
        form_cli.addRow("Teléfono:", self.entry_telefono)
        grp_cli.setLayout(form_cli)
        main.addWidget(grp_cli)

        self._wire_enter_flow()

        # --- Barra superior
        htop = QHBoxLayout()

        self.btn_moneda = self._make_tool_icon(
            "💱", "Cambiar moneda y configurar tasa", self.abrir_dialogo_moneda_y_tasa
        )

        self.lbl_moneda = QLabel()
        self.lbl_moneda.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)

        btn_listado = QPushButton("Listado de productos")
        self._apply_btn_responsive(btn_listado, 140, 36)
        btn_listado.clicked.connect(self.abrir_listado_productos)

        htop.addWidget(self.btn_moneda)
        htop.addWidget(self.lbl_moneda)

        htop.addStretch(1)

        # =============================
        # Pago (según país)
        # =============================

        # ✅ Toggle Tarjeta/Efectivo SOLO Paraguay
        self._py_cash_mode = False
        if APP_COUNTRY == "PARAGUAY":
            grp_pay = QGroupBox("Pago")
            hp = QHBoxLayout(grp_pay)
            hp.setContentsMargins(8, 6, 8, 6)

            self.btn_pay_card = QPushButton("Tarjeta")
            self.btn_pay_cash = QPushButton("Efectivo")
            for b in (self.btn_pay_card, self.btn_pay_cash):
                self._apply_btn_responsive(b, 90, 32)
                b.setCheckable(True)

            self.btn_pay_card.setChecked(True)

            self.pay_group = QButtonGroup(self)
            self.pay_group.setExclusive(True)
            self.pay_group.addButton(self.btn_pay_card, 0)
            self.pay_group.addButton(self.btn_pay_cash, 1)
            self.pay_group.buttonClicked.connect(self._on_py_payment_clicked)

            hp.addWidget(self.btn_pay_card)
            hp.addWidget(self.btn_pay_cash)

            htop.addWidget(grp_pay)

        # ✅ Input libre SOLO Perú
        elif APP_COUNTRY == "PERU":
            grp_pay = QGroupBox("Pago")
            hp = QHBoxLayout(grp_pay)
            hp.setContentsMargins(8, 6, 8, 6)

            self.entry_metodo_pago = QLineEdit()
            self.entry_metodo_pago.setPlaceholderText("Método de pago (opcional)")
            self.entry_metodo_pago.setClearButtonEnabled(True)
            self.entry_metodo_pago.setFixedWidth(220)

            hp.addWidget(self.entry_metodo_pago)
            htop.addWidget(grp_pay)

        htop.addWidget(btn_listado)
        main.addLayout(htop)

        self._update_currency_label()

        # --- Búsqueda
        grp_bus = QGroupBox("Búsqueda de Productos")
        vbus = QVBoxLayout()
        hbus = QHBoxLayout()

        self.entry_producto = QLineEdit()
        self.entry_producto.setPlaceholderText("Código, nombre, categoría o tipo")
        self.entry_producto.returnPressed.connect(self._on_return_pressed)

        lbl_bus = QLabel("Código o Nombre:")

        btn_agregar_srv = QPushButton("Agregar Servicio")
        self._apply_btn_responsive(btn_agregar_srv, 110, 36)
        btn_agregar_srv.setToolTip("Agregar un ítem de tipo SERVICIO / personalizado")
        btn_agregar_srv.clicked.connect(self.agregar_producto_personalizado)

        hbus.addWidget(lbl_bus)
        hbus.addWidget(self.entry_producto)
        hbus.addWidget(btn_agregar_srv)

        vbus.addLayout(hbus)
        grp_bus.setLayout(vbus)
        main.addWidget(grp_bus)

        # --- Tabla seleccionados
        grp_tab = QGroupBox("Productos Seleccionados")
        vtab = QVBoxLayout()
        self.table = QTableView()
        self.model = ItemsModel(self.items)
        self.table.setModel(self.model)

        try:
            self.model.toast_requested.connect(lambda msg: Toast.notify(self, msg, duration_ms=4000, fade_ms=1000))
        except Exception:
            pass

        if APP_COUNTRY == "PARAGUAY":
            self.model.set_py_cash_mode(self._is_py_cash_mode())

        self.qty_delegate = QuantityDelegate(self.table)
        self.table.setItemDelegateForColumn(3, self.qty_delegate)
        try:
            self.qty_delegate.closeEditor.connect(self._on_qty_editor_closed)
        except Exception:
            pass

        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)

        self.act_edit = QAction("Editar observación…", self)
        self.act_edit.triggered.connect(self.editar_observacion)

        self.act_edit_price = QAction("Editar precio…", self)
        self.act_edit_price.triggered.connect(self.editar_precio_unitario)

        self.act_clear_price = QAction("Quitar precio personalizado", self)
        self.act_clear_price.triggered.connect(self.quitar_reescritura_precio)

        self.act_edit_discount = QAction("Editar descuento…", self)
        self.act_edit_discount.triggered.connect(self.editar_descuento_item)

        self.act_del = QAction("Eliminar", self)
        self.act_del.triggered.connect(self.eliminar_producto)

        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.mostrar_menu_tabla)
        self.table.doubleClicked.connect(self._double_click_tabla)
        QShortcut(QKeySequence.Delete, self.table, activated=self.eliminar_producto)

        vtab.addWidget(self.table)
        grp_tab.setLayout(vtab)
        main.addWidget(grp_tab)

        # --- Botonera final
        hact = QHBoxLayout()

        btn_prev = QPushButton("Previsualizar")
        self._apply_btn_responsive(btn_prev, 120, 36)
        btn_prev.clicked.connect(self.previsualizar_datos)

        btn_gen = QPushButton("Generar Cotización")
        self._apply_btn_responsive(btn_gen, 140, 36)
        btn_gen.clicked.connect(self.generar_cotizacion)

        btn_lim = QPushButton("Limpiar")
        self._apply_btn_responsive(btn_lim, 110, 36)
        btn_lim.clicked.connect(self.limpiar_formulario)

        for w in (btn_prev, btn_gen, btn_lim):
            hact.addWidget(w)
        main.addLayout(hact)
