# src/app_window.py
from __future__ import annotations
import os, re, datetime
import pandas as pd
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit, QPushButton,
    QMessageBox, QGroupBox, QHeaderView, QAbstractItemView, QTableView, QTableWidget, QTableWidgetItem,
    QMenu, QDialog, QToolButton, QSizePolicy, QApplication, QStyledItemDelegate
)
from PySide6.QtGui import (
    QAction, QKeySequence, QShortcut, QDesktopServices, QIcon, QBrush, QRegularExpressionValidator
)
from PySide6.QtCore import Qt, QTimer, QUrl, QModelIndex, QStringListModel, QRegularExpression

from .paths import BASE_APP_TITLE, DATA_DIR, COTIZACIONES_DIR, resolve_country_asset
from .config import (
    APP_COUNTRY, id_label_for_country, listing_allows_products,
    listing_allows_presentations, ALLOW_NO_STOCK, COUNTRY_CODE, CATS,
    APP_CURRENCY, SECONDARY_CURRENCY, get_currency_context, set_currency_context,
    convert_from_base,
)
from .utils import nz, fmt_money_ui
from .pricing import precio_unitario_por_categoria, cantidad_para_mostrar
from .presentations import map_pc_to_bottle_code, extract_ml_from_text, ml_from_pres_code_norm
from .widgets import SelectorTablaSimple, ListadoProductosDialog, CustomProductDialog, show_price_picker
from .models import ItemsModel, CAN_EDIT_UNIT_PRICE
from .pdfgen import generar_pdf
from .logging_setup import get_logger

log = get_logger(__name__)


def build_completer_strings(productos, botellas_pc):
    sugs = []
    if listing_allows_products():
        for p in productos:
            cat = p.get("categoria", "")
            gen = p.get("genero", "")
            sugs.append(
                f"{p['id']} - {p['nombre']} - {cat}" + (f" - {gen}" if gen else "")
            )
    if listing_allows_presentations():
        for pc in botellas_pc:
            sugs.append(f"{pc.get('id')} - Presentación (PC) - {pc.get('nombre', '')}")
    return sugs


class QuantityDelegate(QStyledItemDelegate):
    """
    Delegate para la columna 'Cantidad':
    Solo permite números y separadores decimales (.,-) en el editor.
    Evita que el usuario escriba letras directamente.
    """
    def createEditor(self, parent, option, index):
        editor = QLineEdit(parent)
        # Solo dígitos, punto, coma y signo menos
        rx = QRegularExpression(r"^[0-9.,-]*$")
        validator = QRegularExpressionValidator(rx, editor)
        editor.setValidator(validator)
        return editor


class SistemaCotizaciones(QMainWindow):
    def _update_title_with_client(self, text: str):
        name = (text or "").strip()
        self.setWindowTitle(f"{name} - {BASE_APP_TITLE}" if name else BASE_APP_TITLE)

    def __init__(self, df_productos: pd.DataFrame, df_presentaciones: pd.DataFrame, app_icon: QIcon):
        super().__init__()
        self.setWindowTitle(BASE_APP_TITLE)
        self.resize(980, 640)
        if not app_icon.isNull():
            self.setWindowIcon(app_icon)

        self.productos = df_productos.to_dict("records")
        self.presentaciones = df_presentaciones.to_dict("records")
        self.items: list[dict] = []
        self._suppress_next_return = False
        self._ignore_completer = False
        self._shown_once = False
        self._app_icon = app_icon
        self._ctx_row = None

        # === Moneda / tasa ===
        self.base_currency = APP_CURRENCY
        self.secondary_currency = SECONDARY_CURRENCY
        self._tasa_path = os.path.join(DATA_DIR, "tasa.txt")
        # Lee tasa guardada (si existe); la UI siempre arranca en moneda base
        self._exchange_rate = self._load_exchange_rate_file()
        set_currency_context(self.base_currency, 1.0)

        # PCs visibles: códigos que empiezan por "PC" y categoría "OTROS"
        self._botellas_pc = [
            p for p in self.productos
            if str(p.get("id", "")).upper().startswith("PC")
            and (p.get("categoria", "").upper() == "OTROS")
        ]
        log.info(
            "Ventana iniciada. productos=%d presentaciones=%d botellasPC=%d tasa=%s",
            len(self.productos), len(self.presentaciones),
            len(self._botellas_pc), self._exchange_rate
        )

        self._build_ui()
        self.entry_cliente.textChanged.connect(self._update_title_with_client)
        self._update_title_with_client(self.entry_cliente.text())
        self._build_completer()

        # Enfocar último ítem al agregar
        self.model.item_added.connect(self._focus_last_row)

    # === Moneda / tasa helpers ===
    def _load_exchange_rate_file(self) -> float | None:
        """
        Lee tasa.txt (si existe) y devuelve la tasa base→sec válida PARA HOY o None.

        Formato esperado (una o varias líneas), por ejemplo:
            2.000000  # tasa PEN->BOB (2025-11-15)

        - Si hay varias líneas para la fecha de hoy, usa la última.
        - Si no hay tasa para hoy, se considera que no hay tasa configurada.
        """
        try:
            if not self._tasa_path or not os.path.exists(self._tasa_path):
                return None

            today = datetime.datetime.now().strftime("%Y-%m-%d")
            today_rates: list[float] = []

            with open(self._tasa_path, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue

                    # Número de la tasa
                    m_num = re.search(r"([0-9]+(?:[.,][0-9]+)?)", line)
                    if not m_num:
                        continue
                    num_raw = m_num.group(1).replace(",", ".")
                    try:
                        v = float(num_raw)
                    except ValueError:
                        continue
                    if v <= 0:
                        continue

                    # Fecha YYYY-MM-DD (en el comentario)
                    m_date = re.search(r"(\d{4}-\d{2}-\d{2})", line)
                    if not m_date:
                        # Línea antigua sin fecha explícita → no sirve para lógica diaria
                        continue
                    date_str = m_date.group(1)
                    if date_str == today:
                        today_rates.append(v)

            if not today_rates:
                log.info("No hay tasa registrada para hoy en %s", self._tasa_path)
                return None

            rate = today_rates[-1]  # última tasa de hoy
            log.info("Tasa para hoy cargada desde %s: %s", self._tasa_path, rate)
            return rate
        except Exception as e:
            log.warning("No se pudo leer tasa.txt (%s): %s", self._tasa_path, e)
            return None

    def _save_exchange_rate_file(self, rate: float) -> None:
        """
        Guarda la tasa del día en tasa.txt, manteniendo histórico.

        Cada llamada añade una línea como:
            2.000000  # tasa PEN->BOB (2025-11-15)

        Si en el mismo día se guarda más de una vez, la función de lectura
        usará siempre la última línea de ese día.
        """
        try:
            os.makedirs(os.path.dirname(self._tasa_path), exist_ok=True)
            today = datetime.datetime.now().strftime("%Y-%m-%d")
            with open(self._tasa_path, "a", encoding="utf-8") as f:
                f.write(
                    f"{rate:.6f}  # tasa {self.base_currency}->{self.secondary_currency} ({today})\n"
                )
            log.info("Tasa guardada en %s: %s", self._tasa_path, rate)
        except Exception as e:
            log.warning("No se pudo guardar tasa.txt (%s): %s", self._tasa_path, e)

    def _update_currency_label(self):
        if not hasattr(self, "lbl_moneda"):
            return
        cur, _, rate = get_currency_context()
        base = self.base_currency
        sec_code = self.secondary_currency
        if cur == base:
            if self._exchange_rate and self._exchange_rate > 0:
                txt = f"Moneda: {base} (tasa {base}→{sec_code}: {self._exchange_rate:.4f})"
            else:
                txt = f"Moneda: {base} (tasa {base}→{sec_code} sin configurar)"
        else:
            txt = f"Moneda: {sec_code} (1 {base} = {rate:.4f} {sec_code})"
        self.lbl_moneda.setText(txt)

    def abrir_dialogo_moneda_y_tasa(self):
        """
        Botón 💱: permite elegir moneda de trabajo (base/secundaria) y configurar la tasa.
        """
        dlg = QDialog(self)
        dlg.setWindowTitle("Moneda y tasa de cambio")
        dlg.resize(360, 220)
        if not self._app_icon.isNull():
            dlg.setWindowIcon(self._app_icon)

        from PySide6.QtWidgets import (
            QVBoxLayout, QHBoxLayout, QRadioButton, QDoubleSpinBox, QDialogButtonBox
        )

        v = QVBoxLayout(dlg)

        base = self.base_currency
        sec_code = self.secondary_currency

        cur, _, rate_global = get_currency_context()
        current_is_base = (cur == base)
        current_rate = self._exchange_rate or rate_global or 0.0

        if self._exchange_rate is None:
            info = QLabel(
                "<b style='color:#c00'>No hay tasa guardada.</b> "
                "Ingrese la tasa del día para poder usar la moneda secundaria."
            )
            info.setTextFormat(Qt.RichText)
            v.addWidget(info)

        rb_base = QRadioButton(f"Trabajar en moneda base ({base})")
        rb_sec = QRadioButton(f"Trabajar en moneda secundaria ({sec_code})")
        rb_base.setChecked(current_is_base or self._exchange_rate is None)
        rb_sec.setChecked(not current_is_base and self._exchange_rate is not None)
        v.addWidget(rb_base)
        v.addWidget(rb_sec)

        h_rate = QHBoxLayout()
        h_rate.addWidget(QLabel(f"Tasa 1 {base} ="))
        sp_rate = QDoubleSpinBox()
        sp_rate.setDecimals(6)
        sp_rate.setMinimum(0.000001)
        sp_rate.setMaximum(999999999.0)
        init_rate = current_rate if current_rate > 0 else 1.0
        sp_rate.setValue(init_rate)
        h_rate.addWidget(sp_rate)
        h_rate.addWidget(QLabel(sec_code))
        v.addLayout(h_rate)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        v.addWidget(bb)

        def on_accept():
            use_secondary = rb_sec.isChecked()
            new_rate = float(sp_rate.value())
            if use_secondary and new_rate <= 0:
                QMessageBox.warning(
                    self,
                    "Tasa requerida",
                    "Ingrese la tasa del día para cambiar a la moneda secundaria.",
                )
                return
            self._apply_currency_settings(use_secondary, new_rate)
            dlg.accept()

        bb.accepted.connect(on_accept)
        bb.rejected.connect(dlg.reject)
        dlg.exec()

    def _apply_currency_settings(self, use_secondary: bool, new_rate: float):
        base = self.base_currency
        sec_code = self.secondary_currency

        cur, _, _ = get_currency_context()
        old_currency = cur

        # 1) Siempre actualizar y persistir la tasa del día,
        #    aunque sigas trabajando en moneda base.
        try:
            rate = max(float(new_rate), 0.000001)
        except Exception:
            rate = 1.0

        self._exchange_rate = rate
        self._save_exchange_rate_file(self._exchange_rate)

        # 2) Actualizar el contexto según la moneda seleccionada
        if use_secondary:
            # Trabajar en moneda secundaria con la tasa recién guardada
            set_currency_context(sec_code, self._exchange_rate)
        else:
            # Seguir en moneda base (factor 1), pero ya quedó guardada la tasa
            # para cuando pases a la moneda secundaria.
            set_currency_context(base, 1.0)

        # 3) Refrescar label y tabla
        self._update_currency_label()

        if self.model.rowCount() > 0:
            top = self.model.index(0, 0)
            bottom = self.model.index(
                self.model.rowCount() - 1, self.model.columnCount() - 1
            )
            self.model.dataChanged.emit(top, bottom, [Qt.DisplayRole, Qt.EditRole])

        log.info(
            "Cambio de moneda: %s → %s (rate=%s)",
            old_currency, get_currency_context()[0], self._exchange_rate
        )


    # === posicionamiento ventana ===
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

    # === Helper: enfoca y desplaza al último item
    def _focus_last_row(self, row_index: int):
        try:
            r = row_index if isinstance(row_index, int) else (self.model.rowCount() - 1)
            if r < 0:
                return
            idx0 = self.model.index(r, 0)
            self.table.selectRow(r)
            self.table.setCurrentIndex(idx0)
            self.table.scrollTo(idx0, QAbstractItemView.PositionAtBottom)
            if QApplication.activeModalWidget() is None:
                self.table.setFocus()
        except Exception:
            pass

    # ==== abrir carpetas ====
    def abrir_carpeta_data(self):
        if not os.path.isdir(DATA_DIR):
            QMessageBox.warning(
                self, "Carpeta no encontrada",
                f"No se encontró la carpeta:\n{DATA_DIR}"
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(DATA_DIR)))

    def abrir_carpeta_cotizaciones(self):
        if not os.path.isdir(COTIZACIONES_DIR):
            QMessageBox.warning(
                self, "Carpeta no encontrada",
                f"No se encontró la carpeta:\n{COTIZACIONES_DIR}"
            )
            return
        QDesktopServices.openUrl(
            QUrl.fromLocalFile(os.path.abspath(COTIZACIONES_DIR))
        )

    # ===== Helpers de estilo =====
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

    # ===== UI =====
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

        # --- Barra superior
        htop = QHBoxLayout()
        btn_cambiar = QPushButton("Cambiar productos")
        self._apply_btn_responsive(btn_cambiar, 120, 36)
        btn_cambiar.clicked.connect(self.abrir_carpeta_data)

        btn_cotizaciones = QPushButton("Cotizaciones")
        self._apply_btn_responsive(btn_cotizaciones, 120, 36)
        btn_cotizaciones.clicked.connect(self.abrir_carpeta_cotizaciones)

        # 🔁 Botón de moneda/tasa
        self.btn_moneda = self._make_tool_icon(
            "💱", "Cambiar moneda y configurar tasa",
            self.abrir_dialogo_moneda_y_tasa,
        )

        # Label de estado de moneda
        self.lbl_moneda = QLabel()
        self.lbl_moneda.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)

        btn_listado = QPushButton("Listado de productos")
        self._apply_btn_responsive(btn_listado, 140, 36)
        btn_listado.clicked.connect(self.abrir_listado_productos)

        htop.addWidget(btn_cambiar)
        htop.addWidget(btn_cotizaciones)
        htop.addWidget(self.btn_moneda)
        htop.addWidget(self.lbl_moneda)
        htop.addStretch(1)
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
        btn_agregar_srv.setToolTip(
            "Agregar un ítem de tipo SERVICIO / personalizado"
        )
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

        # Delegate numérico para la columna de cantidades (col 3)
        self.table.setItemDelegateForColumn(3, QuantityDelegate(self.table))

        # Edición: cantidad (col 3) y precio (col 4) vía doble clic / tecla
        self.table.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
        )

        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)

        header = self.table.horizontalHeader()
        # Columnas: 0=Cod,1=Prod,2=Desc,3=Cant,4=Precio,5=Subtotal
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)

        # Acciones del menú contextual
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

    # ===== Menú contextual y doble-clic =====
    def mostrar_menu_tabla(self, pos):
        index = self.table.indexAt(pos)
        if not index.isValid():
            return
        row = index.row()
        if row < 0 or row >= len(self.items):
            return
        self._ctx_row = row
        self.table.selectRow(row)
        item = self.items[row]

        menu = QMenu(self)
        cat = (item.get("categoria") or "").upper()

        # Descuento: siempre disponible
        menu.addAction(self.act_edit_discount)

        # Precio: siempre si es SERVICIO; o países que permiten editar
        can_edit_price = (cat == "SERVICIO") or CAN_EDIT_UNIT_PRICE
        if can_edit_price:
            menu.addAction(self.act_edit_price)
            self.act_clear_price.setEnabled(item.get("precio_override") is not None)
            menu.addAction(self.act_clear_price)

        if cat in ("BOTELLAS", "SERVICIO"):
            if menu.actions():
                menu.addSeparator()
            menu.addAction(self.act_edit)

        if menu.actions():
            menu.addSeparator()
        menu.addAction(self.act_del)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _double_click_tabla(self, index: QModelIndex):
        if not index.isValid():
            return
        col = index.column()
        row = index.row()
        if row < 0 or row >= len(self.items):
            return
        item = self.items[row]
        cat = (item.get("categoria") or "").upper()

        if col == 2:  # Descuento
            self._abrir_dialogo_descuento(row)
            return

        if col == 4:  # columna Precio → abrir selector modal
            if (cat == "SERVICIO") or CAN_EDIT_UNIT_PRICE or (cat == "BOTELLAS"):
                self._abrir_selector_precio(row)
            return

        if col in (0, 1):  # Código o Producto → Observación (solo si aplica)
            if cat in ("BOTELLAS", "SERVICIO"):
                self._abrir_dialogo_observacion(row, item)

    # ===== Completer =====
    def _build_completer(self):
        from PySide6.QtWidgets import QCompleter
        self._sug_model = QStringListModel(
            build_completer_strings(self.productos, self._botellas_pc)
        )
        self._completer = QCompleter(self._sug_model, self)
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchContains)
        self.entry_producto.setCompleter(self._completer)

        def add_from_completion(text: str):
            if self._ignore_completer:
                self._ignore_completer = False
                return
            cod = str(text).split(" - ")[0].strip()
            self._suppress_next_return = True
            self._agregar_por_codigo(cod)
            QTimer.singleShot(0, self.entry_producto.clear)
            if self._completer.popup():
                self._completer.popup().hide()

        self._completer.activated[str].connect(add_from_completion)

    def _on_return_pressed(self):
        popup = self._completer.popup() if self._completer else None
        if popup and popup.isVisible():
            idx = popup.currentIndex()
            if idx.isValid():
                text = idx.data()
                cod = str(text).split(" - ")[0].strip()
                self._ignore_completer = True
                self._suppress_next_return = True
                self._agregar_por_codigo(cod)
                QTimer.singleShot(0, self.entry_producto.clear)
                popup.hide()
                return
        if self._suppress_next_return:
            self._suppress_next_return = False
            return
        text = self.entry_producto.text().strip()
        if not text:
            return
        cod = text.split(" - ")[0].strip()
        self._agregar_por_codigo(cod)
        self.entry_producto.clear()

    # ===== Agregar producto personalizado/servicio
    def agregar_producto_personalizado(self):
        dlg = CustomProductDialog(self, app_icon=self._app_icon)
        if dlg.exec() != QDialog.Accepted or not dlg.resultado:
            return
        data = dlg.resultado
        unit_price = float(nz(data["precio"], 0.0))  # siempre en moneda base
        qty = int(nz(data["cantidad"], 1))

        item = {
            "_prod": {"precio_unitario": unit_price},  # para recalcular si cambia cantidad
            "codigo": data["codigo"],
            "producto": data["nombre"],
            "categoria": "SERVICIO",
            "cantidad": qty,
            "ml": "",
            "precio": unit_price,
            "total": round(unit_price * qty, 2),   # se normaliza en el modelo
            "observacion": data.get("observacion", ""),
            "stock_disponible": -1.0,  # sin chequeo (float)
            "precio_override": None,
            "precio_tier": None,
        }
        self.model.add_item(item)
        log.info(
            "Producto personalizado agregado: %s x%d %0.2f",
            item["codigo"], qty, unit_price
        )
    
    # ===== Agregar por código (respeta listing y ALLOW_NO_STOCK)
    def _agregar_por_codigo(self, cod: str):
        cod_u = (cod or "").strip().upper()

        # 1) Presentación tipo PC…
        if cod_u.startswith("PC"):
            if not listing_allows_presentations():
                QMessageBox.warning(
                    self, "Restringido por configuración",
                    "El tipo de listado actual no permite Presentaciones."
                )
                return
            pc = next(
                (p for p in self._botellas_pc if str(p.get("id", "")).upper() == cod_u),
                None
            )
            if pc:
                bot_code = map_pc_to_bottle_code(str(pc.get("id", "")))
                bot = next(
                    (b for b in self.productos
                     if str(b.get("id", "")).upper() == (bot_code or "").upper()
                     and (b.get("categoria", "").upper() == "BOTELLAS")),
                    None
                )
                if (
                    bot is not None
                    and float(nz(bot.get("cantidad_disponible"), 0.0)) <= 0
                    and not ALLOW_NO_STOCK
                ):
                    QMessageBox.warning(
                        self, "Sin botellas",
                        "❌ No hay botellas disponibles para esta presentación."
                    )
                    return
                self._selector_pc(pc)
                return

        # 2) Presentación de Hoja 2
        pres = next(
            (p for p in self.presentaciones
             if str(p.get("CODIGO", "")).upper() == cod_u),
            None
        )
        if pres:
            if not listing_allows_presentations():
                QMessageBox.warning(
                    self, "Restringido por configuración",
                    "El tipo de listado actual no permite Presentaciones."
                )
            else:
                self._selector_presentacion(pres)
            return

        # 3) Producto de catálogo
        prod = next(
            (p for p in self.productos if str(p.get("id", "")).upper() == cod_u),
            None
        )
        if not prod:
            QMessageBox.warning(self, "Advertencia", "❌ Producto no encontrado")
            return
        if not listing_allows_products():
            QMessageBox.warning(
                self, "Restringido por configuración",
                "El tipo de listado actual no permite Productos."
            )
            return

        if float(nz(prod.get("cantidad_disponible"), 0.0)) <= 0 and not ALLOW_NO_STOCK:
            QMessageBox.warning(self, "Sin stock", "❌ Este producto no tiene stock disponible.")
            return

        cat = (prod.get("categoria") or "").upper()
        qty_default = 0.001 if (APP_COUNTRY == "PERU" and cat in CATS) else 1.0
        unit_price = precio_unitario_por_categoria(cat, prod, qty_default)  # siempre en base

        item = {
            "_prod": prod,
            "codigo": prod["id"],
            "producto": prod["nombre"],
            "categoria": cat,
            "cantidad": qty_default,
            "ml": prod.get("ml", ""),
            "precio": float(unit_price),            # almacenado en base
            "total": round(float(unit_price) * qty_default, 2),
            "observacion": "",
            "stock_disponible": float(nz(prod.get("cantidad_disponible"), 0.0)),
            "precio_override": None,
            "precio_tier": "UNITARIO" if cat == "BOTELLAS" else None,
        }
        self.model.add_item(item)

   

        # ===== Diálogo de descuento por fila =====
    def _abrir_dialogo_descuento(self, row: int):
        """
        Abre un diálogo para editar descuento de una fila:
        - Muestra precio unitario, cantidad y subtotal.
        - Permite setear descuento por porcentaje o monto.
        """
        if row < 0 or row >= len(self.items):
            return
        it = self.items[row]

        # Datos base en moneda base
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

        dlg = QDialog(self)
        dlg.setWindowTitle("Editar descuento")
        dlg.resize(420, 260)
        if not self._app_icon.isNull():
            dlg.setWindowIcon(self._app_icon)

        from PySide6.QtWidgets import (
            QVBoxLayout, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QGroupBox
        )

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
        # MUCHOS MÁS DECIMALES EN EL PORCENTAJE
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

        # --- Botones OK / Cancel ---
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        v.addWidget(bb)

        def on_accept():
            pct = float(sp_pct.value())
            amt_ui = float(sp_amt.value())

            # Si no hay subtotal o ambos 0 → limpiar descuento
            if subtotal_base <= 0 or (pct <= 0 and amt_ui <= 0):
                payload = {"mode": "clear"}
            else:
                # Convertir monto desde moneda actual a base
                cur, _, rate = get_currency_context()
                if cur == self.base_currency or not rate:
                    amt_base = amt_ui
                else:
                    # rate ~ base→moneda_actual
                    amt_base = amt_ui / float(rate)

                if pct > 0:
                    payload = {"mode": "percent", "percent": pct}
                else:
                    payload = {"mode": "amount", "amount": amt_base}

            idx = self.model.index(row, 2)  # col Descuento
            self.model.setData(idx, payload, Qt.EditRole)
            dlg.accept()

        bb.accepted.connect(on_accept)
        bb.rejected.connect(dlg.reject)
        dlg.exec()


    def editar_descuento_item(self):
        row = self._ctx_row
        if row is None:
            sel = self.table.selectionModel().selectedRows()
            if not sel:
                return
            row = sel[0].row()
        self._abrir_dialogo_descuento(row)
    
    # ========== Flujos de presentaciones (igual que antes, con floats) ==========
    def _selector_pc(self, pc: dict):
        mapped_code = map_pc_to_bottle_code(str(pc.get("id", "")))
        botella_ref = next(
            (b for b in self.productos
             if str(b.get("id", "")).upper() == (mapped_code or "")
             and b.get("categoria", "").upper() == "BOTELLAS"),
            None
        )
        ml_botella = extract_ml_from_text(botella_ref.get("nombre", "")) if botella_ref else 0
        if ml_botella == 0:
            ml_botella = extract_ml_from_text(pc.get("nombre", ""))
        if ml_botella == 0:
            QMessageBox.warning(
                self, "PC sin ML",
                "No pude inferir los ml de la botella asociada a este PC."
            )
            return

        pres_ml_matches = [
            pr for pr in self.presentaciones
            if ml_from_pres_code_norm(pr.get("CODIGO_NORM") or pr.get("CODIGO"))
            == ml_botella
        ]

        def base_has_match(p):
            dep_base = (p.get("categoria", "") or "").upper()
            gen_base = (p.get("genero", "") or "").strip().lower()
            for pr in pres_ml_matches:
                if (pr.get("DEPARTAMENTO", "") or "").upper() == dep_base:
                    pr_gen = (pr.get("GENERO", "") or "").strip().lower()
                    if not pr_gen or pr_gen == gen_base:
                        return True
            return False

        filas_base = [{
            "codigo": p.get("id", ""),
            "nombre": p.get("nombre", ""),
            "categoria": p.get("categoria", ""),
            "genero": p.get("genero", ""),
        } for p in self.productos
            if (ALLOW_NO_STOCK or float(nz(p.get("cantidad_disponible"), 0.0)) > 0.0)
            and base_has_match(p)
        ]
        if not filas_base:
            QMessageBox.warning(self, "Sin bases", "No hay productos base compatibles para este PC.")
            return

        dlg_base = SelectorTablaSimple(
            self, "Seleccionar Producto Base", filas_base, self._app_icon
        )
        if dlg_base.exec() != QDialog.Accepted or not dlg_base.seleccion:
            return
        cod_base = dlg_base.seleccion["codigo"]
        base = next((p for p in self.productos if str(p.get("id")) == cod_base), None)
        if not base:
            return

        dep_base = (base.get("categoria", "") or "").upper()
        gen_base = (base.get("genero", "") or "").strip().lower()
        pres_candidates = []
        for pr in pres_ml_matches:
            if (pr.get("DEPARTAMENTO", "") or "").upper() == dep_base:
                pr_gen = (pr.get("GENERO", "") or "").strip().lower()
                if not pr_gen or pr_gen == gen_base:
                    pres_candidates.append(pr)
        if not pres_candidates:
            QMessageBox.warning(
                self, "Presentación no encontrada",
                f"No hay una presentación de {ml_botella} ml que coincida con '{dep_base}'."
            )
            return

        pres_final = pres_candidates[0]
        precio_pres = float(nz(pres_final.get("PRECIO_PRESENT"), 0.0))
        precio_pc = float(nz(pc.get("precio_unitario", pc.get("precio_venta")), 0.0))
        unit_price = precio_pres + precio_pc  # base

        nombre_pres = (
            pres_final.get("NOMBRE")
            or pres_final.get("CODIGO_NORM")
            or pres_final.get("CODIGO")
        )
        nombre_final = f"A LA MODE {base.get('nombre', '')} {nombre_pres}".strip()
        codigo_final = f"{pc.get('id', '')}{base.get('id', '')}"
        ml = ml_botella

        stock_bot = float(nz(botella_ref.get("cantidad_disponible"), 0.0)) if botella_ref else None
        stock_base = float(nz(base.get("cantidad_disponible"), 0.0))
        if stock_bot is not None:
            if stock_bot > 0 and stock_base > 0:
                stock_ref = min(stock_bot, stock_base)
            elif stock_bot > 0:
                stock_ref = stock_bot
            elif stock_base > 0:
                stock_ref = stock_base
            else:
                stock_ref = 0.0
        else:
            stock_ref = stock_base if stock_base > 0 else 0.0

        item = {
            "_prod": {"precio_unitario": unit_price},
            "codigo": codigo_final,
            "producto": nombre_final,
            "categoria": "PRESENTACION",
            "cantidad": 1.0,
            "ml": str(ml) if ml else "",
            "precio": float(unit_price),                  # base
            "total": round(float(unit_price) * 1.0, 2),   # base
            "fragancia": base.get("nombre", "")
            if dep_base in ("ESENCIA", "ESENCIAS") else "",
            "observacion": "",
            "stock_disponible": float(stock_ref),
            "precio_override": None,
            "precio_tier": None,
        }
        self.model.add_item(item)

    def _selector_presentacion(self, pres: dict):
        dep = (pres.get("DEPARTAMENTO") or "").upper()
        gen = (pres.get("GENERO") or "").strip().lower()
        base_candidates = [
            p for p in self.productos
            if (p.get("categoria", "").upper() == dep)
            and ((not gen) or (str(p.get("genero", "")).strip().lower() == gen))
            and (ALLOW_NO_STOCK or float(nz(p.get("cantidad_disponible"), 0.0)) > 0.0)
        ]
        if not base_candidates:
            QMessageBox.warning(
                self, "Sin coincidencias",
                f"No hay productos base para {dep} / {pres.get('GENERO', '')}"
            )
            return

        filas_base = [{
            "codigo": p.get("id", ""),
            "nombre": p.get("nombre", ""),
            "categoria": p.get("categoria", ""),
            "genero": p.get("genero", ""),
        } for p in base_candidates]
        dlg_base = SelectorTablaSimple(
            self, "Seleccionar Producto Base", filas_base, self._app_icon
        )
        if dlg_base.exec() != QDialog.Accepted or not dlg_base.seleccion:
            return
        cod_base = dlg_base.seleccion["codigo"]
        base = next((p for p in base_candidates if str(p.get("id")) == cod_base), None)
        if not base:
            return

        botella = None
        if bool(pres.get("REQUIERE_BOTELLA", False)):
            ml_pres = ml_from_pres_code_norm(
                pres.get("CODIGO_NORM") or pres.get("CODIGO") or ""
            )
            bot_opts = []
            for b in self._botellas_pc:
                bot_code = map_pc_to_bottle_code(str(b.get("id", "")))
                bot = next(
                    (bb for bb in self.productos
                     if str(bb.get("id", "")).upper() == (bot_code or "").upper()
                     and (bb.get("categoria", "").upper() == "BOTELLAS")),
                    None
                )
                if not bot:
                    continue
                if (
                    float(nz(bot.get("cantidad_disponible"), 0.0)) <= 0
                    and not ALLOW_NO_STOCK
                ):
                    continue
                ml_b = (
                    extract_ml_from_text(bot.get("nombre", ""))
                    or extract_ml_from_text(b.get("nombre", ""))
                )
                if ml_b != ml_pres:
                    continue
                bot_opts.append(b)
            if not bot_opts:
                QMessageBox.warning(
                    self, "Sin botellas PC",
                    "No hay botellas PC compatibles para esta presentación."
                )
                return
            botella = bot_opts[0]

        precio_pres = float(nz(pres.get("PRECIO_PRESENT"), 0.0))
        precio_bot = float(nz(botella.get("precio_unitario"), 0.0)) if botella else 0.0
        unit_price = precio_pres + precio_bot  # base

        nombre_pres = (
            pres.get("NOMBRE")
            or pres.get("CODIGO_NORM")
            or pres.get("CODIGO")
        )
        nombre_final = f"A LA MODE {base.get('nombre', '')} {nombre_pres}".strip()

        if botella:
            codigo_final = f"{botella.get('id', '')}{base.get('id', '')}"
            ml = extract_ml_from_text(botella.get("nombre", ""))
        else:
            codigo_final = f"{base.get('id', '')}{pres.get('CODIGO_NORM') or pres.get('CODIGO')}"
            ml = ml_from_pres_code_norm(
                pres.get('CODIGO_NORM') or pres.get('CODIGO') or ""
            )

        stock_base = float(nz(base.get("cantidad_disponible"), 0.0))
        stock_ref = stock_base
        if botella:
            stock_bot = float(nz(next(
                (bb for bb in self.productos
                 if str(bb.get("id", "")).upper()
                 == map_pc_to_bottle_code(str(botella.get("id", "")))
                 and (bb.get("categoria", "").upper() == "BOTELLAS")),
                {}
            ).get("cantidad_disponible", 0.0)))
            if stock_base > 0 and stock_bot > 0:
                stock_ref = min(stock_base, stock_bot)
            elif stock_bot > 0:
                stock_ref = stock_bot

        item = {
            "_prod": {"precio_unitario": unit_price},
            "codigo": codigo_final,
            "producto": nombre_final,
            "categoria": "PRESENTACION",
            "cantidad": 1.0,
            "ml": str(ml) if ml else "",
            "precio": float(unit_price),                 # base
            "total": round(float(unit_price) * 1.0, 2),  # base
            "fragancia": base.get("nombre", "")
            if dep in ("ESENCIA", "ESENCIAS") else "",
            "observacion": "",
            "stock_disponible": float(stock_ref),
            "precio_override": None,
            "precio_tier": None,
        }
        self.model.add_item(item)

    # ===== Abrir manual (por compatibilidad) =====
    def abrir_manual(self):
        ruta = resolve_country_asset("manual_usuario_sistema.pdf", COUNTRY_CODE)
        if not ruta or not os.path.exists(ruta):
            QMessageBox.warning(
                self,
                "Manual no encontrado",
                "No se encontró 'manual_usuario_sistema.pdf' en 'templates/<PAIS>/' "
                "ni en 'templates/'.\n"
                "Coloca el manual en 'templates/{COUNTRY_CODE}/' o en 'templates/' "
                "e inténtalo de nuevo."
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(ruta)))

    def abrir_listado_productos(self):
        dlg = ListadoProductosDialog(
            self, self.productos, self.presentaciones,
            self._agregar_por_codigo, app_icon=self._app_icon
        )
        main_geo = self.frameGeometry()
        main_center = main_geo.center()
        dlg_size = dlg.sizeHint()
        x = main_center.x()
        y = main_center.y() - dlg_size.height()
        dlg.move(x, y)
        dlg.exec()

    def _abrir_selector_precio(self, row: int):
        if row < 0 or row >= len(self.items):
            return
        item = self.items[row]

        payload = show_price_picker(self, self._app_icon, item)
        if not payload:  # cancelado
            return
        idx = self.model.index(row, 4)
        self.model.setData(idx, payload, Qt.EditRole)



    def _abrir_dialogo_observacion(self, row: int, item: dict):
        dlg = QDialog(self)
        dlg.setWindowTitle("Editar Observación")
        dlg.resize(320, 120)
        if not self._app_icon.isNull():
            dlg.setWindowIcon(self._app_icon)
        from PySide6.QtWidgets import QVBoxLayout, QLineEdit, QPushButton
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel("Ingrese observación (ej: Color ámbar):"))
        entry = QLineEdit()
        entry.setText(item.get("observacion", ""))
        v.addWidget(entry)
        btn = QPushButton("Guardar")

        def _save():
            item["observacion"] = entry.text().strip()
            self.model.dataChanged.emit(
                self.model.index(row, 0),
                self.model.index(row, self.model.columnCount() - 1),
                [Qt.DisplayRole]
            )
            dlg.accept()

        btn.clicked.connect(_save)
        v.addWidget(btn)
        dlg.exec()

    def editar_observacion(self):
        sel = self.table.selectionModel().selectedRows()
        if not sel:
            return
        row = sel[0].row()
        if row < 0 or row >= len(self.items):
            return
        item = self.items[row]
        if (item.get("categoria") or "").upper() not in ("BOTELLAS", "SERVICIO"):
            return
        self._abrir_dialogo_observacion(row, item)

    def editar_precio_unitario(self):
        # Abre SIEMPRE diálogo modal (nada de editores en celda)
        row = self._ctx_row
        if row is None:
            sel = self.table.selectionModel().selectedRows()
            if not sel:
                return
            row = sel[0].row()
        self._abrir_selector_precio(row)

    def _recalc_price_from_rules(self, item: dict):
        """
        Recalcula el precio unitario en base a:
        - precio_override (custom),
        - precio_tier (UNITARIO, X12, X50, etc.),
        - o reglas de precio por categoría.

        Y además recalcula:
        - subtotal_base
        - descuento_monto (si el descuento es porcentual)
        - total

        Todo en moneda base.
        """
        from .models import _price_from_tier
        from .pricing import precio_unitario_por_categoria

        cat = (item.get("categoria") or "").upper()
        qty = float(nz(item.get("cantidad"), 0.0))
        base_prod = item.get("_prod") or {}

        # --- Determinar nuevo precio unitario (base) ---
        override = item.get("precio_override", None)
        if override is not None:
            unit_price = float(override)
        elif item.get("precio_tier"):
            unit_price = float(_price_from_tier(base_prod, item["precio_tier"]) or 0.0)
            if unit_price <= 0:
                unit_price = float(
                    precio_unitario_por_categoria(cat, base_prod, qty) or 0.0
                )
        else:
            unit_price = float(
                precio_unitario_por_categoria(cat, base_prod, qty) or 0.0
            )

        item["precio"] = unit_price

        # --- Recalcular subtotal base ---
        subtotal = round(unit_price * qty, 2)
        item["subtotal_base"] = subtotal

        # --- Recalcular descuento y total ---
        d_pct = float(nz(item.get("descuento_pct"), 0.0))
        d_monto = float(nz(item.get("descuento_monto"), 0.0))

        # Si hay porcentaje, el monto se recalcula sobre el NUEVO subtotal
        if d_pct > 0 and subtotal > 0:
            d_monto = round(subtotal * d_pct / 100.0, 2)

        # Nunca permitir que el descuento supere al subtotal
        if d_monto > subtotal:
            d_monto = subtotal

        item["descuento_monto"] = d_monto

        total = round(subtotal - d_monto, 2)
        if total < 0:
            total = 0.0
        item["total"] = total

    def quitar_reescritura_precio(self):
        sel = self.table.selectionModel().selectedRows()
        if not sel:
            return
        rows = [ix.row() for ix in sel if 0 <= ix.row() < len(self.items)]

        for r in rows:
            idx = self.model.index(r, 4)
            # 'tier' = 'base' → limpia override y tier y recalcula todo
            self.model.setData(idx, {"mode": "tier", "tier": "base"}, Qt.EditRole)


    def eliminar_producto(self):
        sel = self.table.selectionModel().selectedRows()
        if not sel:
            return
        rows = [ix.row() for ix in sel]
        self.model.remove_rows(rows)
        log.info("Items eliminados: %s", rows)

    # ====== Previsualización / PDF ======
    def previsualizar_datos(self):
        c = self.entry_cliente.text()
        ci = self.entry_cedula.text()
        t = self.entry_telefono.text()
        items = self.items
        if not all([c, ci, t]):
            QMessageBox.warning(
                self, "Advertencia", "❌ Faltan datos del cliente"
            )
            return
        total_items = sum(nz(i.get("total")) for i in items) if items else 0.0
        if not items or total_items <= 0.0:
            QMessageBox.warning(
                self, "Advertencia", "❌ Faltan productos en la cotización"
            )
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Previsualización de Cotización")
        dlg.resize(860, 520)
        if not self._app_icon.isNull():
            self.setWindowIcon(self._app_icon)
            dlg.setWindowIcon(self._app_icon)
        from PySide6.QtWidgets import QVBoxLayout
        v = QVBoxLayout(dlg)
        id_lbl = id_label_for_country(APP_COUNTRY)
        v.addWidget(QLabel(f"<b>Nombre:</b> {c}"))
        v.addWidget(QLabel(f"<b>{id_lbl}:</b> {ci}"))
        v.addWidget(QLabel(f"<b>Teléfono:</b> {t}"))

        # Tabla de preview: agregamos columna de descuento
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

        for it in self.items:
            r = tbl.rowCount()
            tbl.insertRow(r)
            prod = it["producto"]
            if it.get("fragancia"):
                prod += f" ({it['fragancia']})"
            if it.get("observacion"):
                prod += f" | {it['observacion']}"

            qty_txt = cantidad_para_mostrar(it)

            precio_base = float(nz(it.get("precio"), 0.0))
            total_line_base = float(nz(it.get("total"), 0.0))
            subtotal_line_base = float(
                nz(it.get("subtotal_base"), precio_base * nz(it.get("cantidad"), 0.0))
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
                it["codigo"],
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

    def _build_items_for_pdf(self) -> list[dict]:
        """
        Clona los items pero con precios/totales convertidos a la moneda actual,
        e incluye campos de subtotal y descuento en la moneda actual.
        """
        from copy import deepcopy
        cloned = deepcopy(self.items)
        for it in cloned:
            try:
                price_base = float(nz(it.get("precio"), 0.0))
                total_base = float(nz(it.get("total"), 0.0))
                subtotal_base = float(
                    nz(it.get("subtotal_base"),
                       price_base * nz(it.get("cantidad"), 0.0))
                )
                d_monto_base = float(nz(it.get("descuento_monto"), 0.0))
            except Exception:
                price_base = nz(it.get("precio"), 0.0)
                total_base = nz(it.get("total"), 0.0)
                subtotal_base = nz(
                    it.get("subtotal_base"),
                    price_base * nz(it.get("cantidad"), 0.0)
                )
                d_monto_base = nz(it.get("descuento_monto"), 0.0)

            it["precio"] = convert_from_base(price_base)
            it["total"] = convert_from_base(total_base)
            it["subtotal"] = convert_from_base(subtotal_base)
            it["descuento"] = convert_from_base(d_monto_base)
        return cloned

    def generar_cotizacion(self):
        c = self.entry_cliente.text()
        ci = self.entry_cedula.text()
        t = self.entry_telefono.text()
        if not all([c, ci, t]):
            QMessageBox.warning(
                self, "Advertencia", "❌ Faltan datos del cliente"
            )
            return
        total_items = sum(nz(i.get("total")) for i in self.items) if self.items else 0.0
        if not self.items or total_items <= 0:
            QMessageBox.warning(
                self, "Advertencia",
                "❌ Agrega al menos un producto a la cotización"
            )
            return

        # Totales en base
        subtotal_bruto_base = 0.0
        descuento_total_base = 0.0
        total_neto_base = 0.0
        for it in self.items:
            precio_base = float(nz(it.get("precio"), 0.0))
            subtotal_line_base = float(
                nz(it.get("subtotal_base"),
                   precio_base * nz(it.get("cantidad"), 0.0))
            )
            d_monto_base = float(nz(it.get("descuento_monto"), 0.0))
            total_line_base = float(nz(it.get("total"), subtotal_line_base - d_monto_base))

            subtotal_bruto_base += subtotal_line_base
            descuento_total_base += d_monto_base
            total_neto_base += total_line_base

        datos = {
            "fecha": datetime.datetime.now().strftime("%d/%m/%Y"),
            "cliente": c,
            "cedula": ci,
            "telefono": t,
            "metodo_pago": "Transferencia",
            # Items ya convertidos a la moneda actual para el PDF
            "items": self._build_items_for_pdf(),
            # Totales en moneda actual (por si el PDF los usa)
            "subtotal_bruto": convert_from_base(subtotal_bruto_base),
            "descuento_total": convert_from_base(descuento_total_base),
            "total_general": convert_from_base(total_neto_base),
        }
        try:
            ruta = generar_pdf(datos)
            log.info("PDF generado en %s", ruta)
            QMessageBox.information(
                self, "PDF Generado", f"📄 Cotización generada:\n{ruta}"
            )
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(os.path.abspath(COTIZACIONES_DIR))
            )
        except Exception as e:
            log.exception("Error al generar PDF")
            QMessageBox.critical(
                self,
                "Error al generar PDF",
                f"❌ No se pudo generar la cotización:\n{e}"
            )

    def limpiar_formulario(self):
        self.entry_cliente.clear()
        self.entry_cedula.clear()
        self.entry_telefono.clear()
        self.entry_producto.clear()
        self.model.remove_rows(list(range(len(self.items))))
        log.info("Formulario limpiado")
