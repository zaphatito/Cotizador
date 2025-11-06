# src/app_window.py
from __future__ import annotations
import os, re, datetime
import pandas as pd
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit, QPushButton,
    QMessageBox, QGroupBox, QHeaderView, QAbstractItemView, QTableView, QTableWidget, QTableWidgetItem,
    QMenu, QDialog, QToolButton, QSizePolicy
)
from PySide6.QtGui import QAction, QKeySequence, QShortcut, QDesktopServices, QIcon, QBrush
from PySide6.QtCore import Qt, QTimer, QUrl, QModelIndex, QStringListModel

from .paths import BASE_APP_TITLE, DATA_DIR, COTIZACIONES_DIR, resolve_country_asset, resolve_template_path

from .config import (
    APP_COUNTRY, id_label_for_country, listing_allows_products,
    listing_allows_presentations, ALLOW_NO_STOCK, COUNTRY_CODE
)

from .utils import nz, fmt_money_ui
from .pricing import precio_unitario_por_categoria, reglas_cantidad, cantidad_para_mostrar
from .presentations import map_pc_to_bottle_code, extract_ml_from_text, ml_from_pres_code_norm
from .widgets import SelectorTablaSimple, ListadoProductosDialog, CustomProductDialog
from .models import ItemsModel
from .pdfgen import generar_pdf
from .logging_setup import get_logger

log = get_logger(__name__)

# País que permite editar precio unitario directamente
CAN_EDIT_UNIT_PRICE = (APP_COUNTRY == "PARAGUAY")


def build_completer_strings(productos, botellas_pc):
    sugs = []
    if listing_allows_products():
        for p in productos:
            cat = p.get("categoria", ""); gen = p.get("genero","")
            sugs.append(f"{p['id']} - {p['nombre']} - {cat}" + (f" - {gen}" if gen else ""))

    if listing_allows_presentations():
        for pc in botellas_pc:
            sugs.append(f"{pc.get('id')} - Presentación (PC) - {pc.get('nombre','')}")
    return sugs


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

        # PCs visibles: códigos que empiezan por "PC" y categoría "OTROS"
        self._botellas_pc = [
            p for p in self.productos
            if str(p.get("id","")).upper().startswith("PC") and (p.get("categoria","").upper() == "OTROS")
        ]
        log.info("Ventana iniciada. productos=%d presentaciones=%d botellasPC=%d",
                 len(self.productos), len(self.presentaciones), len(self._botellas_pc))

        self._build_ui()
        self.entry_cliente.textChanged.connect(self._update_title_with_client)
        self._update_title_with_client(self.entry_cliente.text())
        self._build_completer()

    def _center_on_screen(self):
        scr = self.screen()
        if not scr: return
        geo = self.frameGeometry()
        center = scr.availableGeometry().center()
        geo.moveCenter(center)
        self.move(geo.topLeft())

    def showEvent(self, event):
        super().showEvent(event)
        if not self._shown_once:
            self._shown_once = True
            self._center_on_screen()

    # ==== abrir carpetas ====
    def abrir_carpeta_data(self):
        if not os.path.isdir(DATA_DIR):
            QMessageBox.warning(self, "Carpeta no encontrada", f"No se encontró la carpeta:\n{DATA_DIR}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(DATA_DIR)))

    def abrir_carpeta_cotizaciones(self):
        if not os.path.isdir(COTIZACIONES_DIR):
            QMessageBox.warning(self, "Carpeta no encontrada", f"No se encontró la carpeta:\n{COTIZACIONES_DIR}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(COTIZACIONES_DIR)))

    # ===== Helpers de estilo (responsive, sin forzar colores) =====
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
        tbtn.setAutoRaise(True)                 # respeta tema claro/oscuro
        tbtn.setToolButtonStyle(Qt.ToolButtonTextOnly)
        tbtn.setCursor(Qt.PointingHandCursor)
        tbtn.setFixedSize(34, 34)               # cuadrado y compacto
        tbtn.clicked.connect(on_click)
        return tbtn

    # ===== UI =====
    def _build_ui(self):
        central = QWidget(); self.setCentralWidget(central)
        main = QVBoxLayout(central)

        # --- Cliente
        grp_cli = QGroupBox("Datos del Cliente")
        form_cli = QFormLayout()
        self.entry_cliente = QLineEdit()
        self.entry_cedula  = QLineEdit()
        self.entry_telefono= QLineEdit()
        self.lbl_doc = QLabel(id_label_for_country(APP_COUNTRY) + ":")
        form_cli.addRow("Nombre Completo:", self.entry_cliente)
        form_cli.addRow(self.lbl_doc, self.entry_cedula)
        form_cli.addRow("Teléfono:", self.entry_telefono)
        grp_cli.setLayout(form_cli); main.addWidget(grp_cli)

        # --- Barra superior (como tu diseño: Cambiar, Cotizaciones, [📘], Listado)
        htop = QHBoxLayout()
        btn_cambiar = QPushButton("Cambiar productos")
        self._apply_btn_responsive(btn_cambiar, 120, 36)
        btn_cambiar.clicked.connect(self.abrir_carpeta_data)

        btn_cotizaciones = QPushButton("Cotizaciones")
        self._apply_btn_responsive(btn_cotizaciones, 120, 36)
        btn_cotizaciones.clicked.connect(self.abrir_carpeta_cotizaciones)

        btn_manual = self._make_tool_icon("📘", "Abrir manual de usuario (PDF)", self.abrir_manual)

        btn_listado = QPushButton("Listado de productos")
        self._apply_btn_responsive(btn_listado, 140, 36)
        btn_listado.clicked.connect(self.abrir_listado_productos)

        htop.addWidget(btn_cambiar)
        htop.addWidget(btn_cotizaciones)
        htop.addWidget(btn_manual)
        htop.addStretch(1)
        htop.addWidget(btn_listado)
        main.addLayout(htop)

        # --- Búsqueda (con Agregar y + Servicio como en tu mock)
        grp_bus = QGroupBox("Búsqueda de Productos")
        vbus = QVBoxLayout(); hbus = QHBoxLayout()
        self.entry_producto = QLineEdit()
        self.entry_producto.setPlaceholderText("Código, nombre, categoría o tipo")
        self.entry_producto.returnPressed.connect(self._on_return_pressed)

        lbl_bus = QLabel("Código o Nombre:")


        btn_agregar_srv = QPushButton("+ Servicio")
        self._apply_btn_responsive(btn_agregar_srv, 110, 36)
        btn_agregar_srv.setToolTip("Agregar un ítem de tipo SERVICIO / personalizado")
        btn_agregar_srv.clicked.connect(self.agregar_producto_personalizado)

        hbus.addWidget(lbl_bus)
        hbus.addWidget(self.entry_producto)
        hbus.addWidget(btn_agregar_srv)

        vbus.addLayout(hbus); grp_bus.setLayout(vbus); main.addWidget(grp_bus)

        # --- Tabla seleccionados
        grp_tab = QGroupBox("Productos Seleccionados"); vtab = QVBoxLayout()
        self.table = QTableView(); self.model = ItemsModel(self.items); self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)

        # Acciones del menú contextual
        self.act_edit         = QAction("Editar observación…", self);      self.act_edit.triggered.connect(self.editar_observacion)
        self.act_edit_price   = QAction("Editar precio unitario…", self);  self.act_edit_price.triggered.connect(self.editar_precio_unitario)
        self.act_clear_price  = QAction("Quitar reescritura de precio", self); self.act_clear_price.triggered.connect(self.quitar_reescritura_precio)
        self.act_del          = QAction("Eliminar", self);                  self.act_del.triggered.connect(self.eliminar_producto)

        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.mostrar_menu_tabla)
        self.table.doubleClicked.connect(self._double_click_tabla)
        QShortcut(QKeySequence.Delete, self.table, activated=self.eliminar_producto)

        vtab.addWidget(self.table); grp_tab.setLayout(vtab); main.addWidget(grp_tab)

        # --- Botonera final (responsiva, sin colores)
        hact = QHBoxLayout()
        btn_prev = QPushButton("Previsualizar");      self._apply_btn_responsive(btn_prev, 120, 36); btn_prev.clicked.connect(self.previsualizar_datos)
        btn_gen  = QPushButton("Generar Cotización"); self._apply_btn_responsive(btn_gen, 140, 36); btn_gen.clicked.connect(self.generar_cotizacion)
        btn_lim  = QPushButton("Limpiar");            self._apply_btn_responsive(btn_lim, 110, 36); btn_lim.clicked.connect(self.limpiar_formulario)
        for w in (btn_prev, btn_gen, btn_lim): hact.addWidget(w)
        main.addLayout(hact)

    # ===== Menú contextual y doble-click =====
    def mostrar_menu_tabla(self, pos):
        index = self.table.indexAt(pos)
        if not index.isValid(): return
        row = index.row()
        if row < 0 or row >= len(self.items): return
        self._ctx_row = row
        self.table.selectRow(row)
        item = self.items[row]

        menu = QMenu(self)

        # Precio editable sólo en Paraguay
        if CAN_EDIT_UNIT_PRICE:
            menu.addAction(self.act_edit_price)
            self.act_clear_price.setEnabled(item.get("precio_override") is not None)
            menu.addAction(self.act_clear_price)

        # Observación solo en BOTELLAS o SERVICIO
        cat = (item.get("categoria") or "").upper()
        if cat in ("BOTELLAS", "SERVICIO"):
            if menu.actions(): menu.addSeparator()
            menu.addAction(self.act_edit)

        if menu.actions(): menu.addSeparator()
        menu.addAction(self.act_del)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _double_click_tabla(self, index: QModelIndex):
        if not index.isValid(): return
        col = index.column()
        if col in (0, 1):  # sólo si hace doble click en código o nombre
            row = index.row()
            if row < 0 or row >= len(self.items): return
            item = self.items[row]
            cat = (item.get("categoria") or "").upper()
            if cat in ("BOTELLAS", "SERVICIO"):
                self._abrir_dialogo_observacion(row, item)

    # ===== Completer =====
    def _build_completer(self):
        from PySide6.QtWidgets import QCompleter
        self._sug_model = QStringListModel(build_completer_strings(self.productos, self._botellas_pc))
        self._completer = QCompleter(self._sug_model, self)
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchContains)
        self.entry_producto.setCompleter(self._completer)

        def add_from_completion(text: str):
            if self._ignore_completer:
                self._ignore_completer = False; return
            cod = str(text).split(" - ")[0].strip()
            self._suppress_next_return = True
            self._agregar_por_codigo(cod)
            QTimer.singleShot(0, self.entry_producto.clear)
            if self._completer.popup(): self._completer.popup().hide()
            self.entry_producto.setFocus()

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
                self.entry_producto.setFocus()
                return

        if self._suppress_next_return:
            self._suppress_next_return = False
            return

        text = self.entry_producto.text().strip()
        if not text: return
        cod = text.split(" - ")[0].strip()
        self._agregar_por_codigo(cod)
        self.entry_producto.clear()
        self.entry_producto.setFocus()

    # ===== Agregar producto personalizado/servicio
    def agregar_producto_personalizado(self):
        dlg = CustomProductDialog(self, app_icon=self._app_icon)
        if dlg.exec() != QDialog.Accepted or not dlg.resultado:
            return
        data = dlg.resultado
        unit_price = float(nz(data["precio"], 0.0))
        qty = int(nz(data["cantidad"], 1))

        item = {
            "_prod": {"precio_unitario": unit_price},  # para recalcular si cambia cantidad
            "codigo": data["codigo"],
            "producto": data["nombre"],
            "categoria": "SERVICIO",
            "cantidad": qty,
            "ml": "",
            "precio": unit_price,
            "total": round(unit_price * qty, 2),
            "observacion": data.get("observacion", ""),
            "stock_disponible": -1,  # -1 => sin chequeo
            "precio_override": None,
        }
        self.model.add_item(item)
        log.info("Producto personalizado agregado: %s x%d %0.2f", item["codigo"], qty, unit_price)

    # ===== Agregar por código (respeta listing y ALLOW_NO_STOCK)
    def _agregar_por_codigo(self, cod: str):
        cod_u = (cod or "").strip().upper()

        # 1) Presentación tipo PC…
        if cod_u.startswith("PC"):
            if not listing_allows_presentations():
                QMessageBox.warning(self, "Restringido por configuración", "El tipo de listado actual no permite Presentaciones.")
                return
            pc = next((p for p in self._botellas_pc if str(p.get("id","")).upper() == cod_u), None)
            if pc:
                bot_code = map_pc_to_bottle_code(str(pc.get("id", "")))
                bot = next(
                    (b for b in self.productos
                     if str(b.get("id", "")).upper() == (bot_code or "").upper()
                     and (b.get("categoria", "").upper() == "BOTELLAS")),
                    None
                )
                if bot is not None and int(nz(bot.get("cantidad_disponible"), 0)) <= 0 and not ALLOW_NO_STOCK:
                    QMessageBox.warning(self, "Sin botellas", "❌ No hay botellas disponibles para esta presentación.")
                    return
                self._selector_pc(pc); return

        # 2) Presentación de Hoja 2
        pres = next((p for p in self.presentaciones if str(p.get("CODIGO","")).upper() == cod_u), None)
        if pres:
            if not listing_allows_presentations():
                QMessageBox.warning(self, "Restringido por configuración", "El tipo de listado actual no permite Presentaciones.")
                return
            self._selector_presentacion(pres); return

        # 3) Producto de catálogo
        prod = next((p for p in self.productos if str(p.get("id","")).upper() == cod_u), None)
        if not prod:
            QMessageBox.warning(self, "Advertencia", "❌ Producto no encontrado"); return
        if not listing_allows_products():
            QMessageBox.warning(self, "Restringido por configuración", "El tipo de listado actual no permite Productos."); return

        if int(nz(prod.get("cantidad_disponible"), 0)) <= 0 and not ALLOW_NO_STOCK:
            QMessageBox.warning(self, "Sin stock", "❌ Este producto no tiene stock disponible."); return

        cat = (prod.get("categoria") or "").upper()
        min_u, _ = reglas_cantidad(cat)
        qty_default = float(min_u)
        unit_price = precio_unitario_por_categoria(cat, prod, qty_default)

        item = {
            "_prod": prod,
            "codigo": prod["id"],
            "producto": prod["nombre"],
            "categoria": cat,
            "cantidad": qty_default,
            "ml": prod.get("ml", ""),
            "precio": float(unit_price),
            "total": round(float(unit_price) * qty_default, 2),
            "observacion": "",
            "stock_disponible": int(nz(prod.get("cantidad_disponible"), 0)),
            "precio_override": None,
        }
        self.model.add_item(item)

    # ========== Flujos de presentaciones ==========
    def _selector_pc(self, pc: dict):
        mapped_code = map_pc_to_bottle_code(str(pc.get("id","")))
        botella_ref = next(
            (b for b in self.productos if str(b.get("id","")).upper() == (mapped_code or "") and b.get("categoria","").upper()=="BOTELLAS"),
            None
        )
        ml_botella = extract_ml_from_text(botella_ref.get("nombre","")) if botella_ref else 0
        if ml_botella == 0:
            ml_botella = extract_ml_from_text(pc.get("nombre",""))
        if ml_botella == 0:
            QMessageBox.warning(self, "PC sin ML", "No pude inferir los ml de la botella asociada a este PC.")
            return

        pres_ml_matches = [pr for pr in self.presentaciones if ml_from_pres_code_norm(pr.get("CODIGO_NORM") or pr.get("CODIGO")) == ml_botella]

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
            "codigo": p.get("id",""),
            "nombre": p.get("nombre",""),
            "categoria": p.get("categoria",""),
            "genero": p.get("genero",""),
        } for p in self.productos if (ALLOW_NO_STOCK or int(nz(p.get("cantidad_disponible"), 0)) > 0) and base_has_match(p)]
        if not filas_base:
            QMessageBox.warning(self, "Sin bases", "No hay productos base compatibles para este PC."); return

        dlg_base = SelectorTablaSimple(self, "Seleccionar Producto Base", filas_base, self._app_icon)
        if dlg_base.exec() != QDialog.Accepted or not dlg_base.seleccion: return
        cod_base = dlg_base.seleccion["codigo"]
        base = next((p for p in self.productos if str(p.get("id")) == cod_base), None)
        if not base: return

        dep_base = (base.get("categoria","") or "").upper()
        gen_base = (base.get("genero","") or "").strip().lower()
        pres_candidates = []
        for pr in pres_ml_matches:
            if (pr.get("DEPARTAMENTO","") or "").upper() == dep_base:
                pr_gen = (pr.get("GENERO","") or "").strip().lower()
                if not pr_gen or pr_gen == gen_base:
                    pres_candidates.append(pr)
        if not pres_candidates:
            QMessageBox.warning(self, "Presentación no encontrada", f"No hay una presentación de {ml_botella} ml que coincida con '{dep_base}'."); return

        pres_final = pres_candidates[0]
        precio_pres = float(nz(pres_final.get("PRECIO_PRESENT"), 0.0))
        precio_pc   = float(nz(pc.get("precio_unitario", pc.get("precio_venta")), 0.0))
        unit_price  = precio_pres + precio_pc

        nombre_pres = pres_final.get("NOMBRE") or pres_final.get("CODIGO_NORM") or pres_final.get("CODIGO")
        nombre_final = f"A LA MODE {base.get('nombre','')} {nombre_pres}".strip()
        codigo_final = f"{pc.get('id','')}{base.get('id','')}"
        ml = ml_botella

        stock_bot = int(nz(botella_ref.get("cantidad_disponible"), 0)) if botella_ref else None
        stock_base = int(nz(base.get("cantidad_disponible"), 0))
        if stock_bot is not None:
            if stock_bot > 0 and stock_base > 0: stock_ref = min(stock_bot, stock_base)
            elif stock_bot > 0:                  stock_ref = stock_bot
            elif stock_base > 0:                 stock_ref = stock_base
            else:                                stock_ref = 0
        else:
            stock_ref = stock_base if stock_base > 0 else 0

        item = {
            "_prod": {"precio_unitario": unit_price},
            "codigo": codigo_final,
            "producto": nombre_final,
            "categoria": "PRESENTACION",
            "cantidad": 1.0,
            "ml": str(ml) if ml else "",
            "precio": float(unit_price),
            "total": round(float(unit_price) * 1.0, 2),
            "fragancia": base.get("nombre","") if dep_base in ("ESENCIA","ESENCIAS") else "",
            "observacion": "",
            "stock_disponible": int(stock_ref),
            "precio_override": None,
        }
        self.model.add_item(item)

    def _selector_presentacion(self, pres: dict):
        dep = (pres.get("DEPARTAMENTO") or "").upper()
        gen = (pres.get("GENERO") or "").strip().lower()
        base_candidates = [
            p for p in self.productos
            if (p.get("categoria","").upper() == dep)
            and ((not gen) or (str(p.get("genero","")).strip().lower() == gen))
            and (ALLOW_NO_STOCK or int(nz(p.get("cantidad_disponible"), 0)) > 0)
        ]
        if not base_candidates:
            QMessageBox.warning(self, "Sin coincidencias", f"No hay productos base para {dep} / {pres.get('GENERO','')}"); return

        filas_base = [{"codigo": p.get("id",""), "nombre": p.get("nombre",""), "categoria": p.get("categoria",""), "genero": p.get("genero","")} for p in base_candidates]
        dlg_base = SelectorTablaSimple(self, "Seleccionar Producto Base", filas_base, self._app_icon)
        if dlg_base.exec() != QDialog.Accepted or not dlg_base.seleccion: return
        cod_base = dlg_base.seleccion["codigo"]
        base = next((p for p in base_candidates if str(p.get("id")) == cod_base), None)
        if not base: return

        botella = None
        if bool(pres.get("REQUIERE_BOTELLA", False)):
            ml_pres = ml_from_pres_code_norm(pres.get("CODIGO_NORM") or pres.get("CODIGO") or "")
            bot_opts = []
            for b in self._botellas_pc:
                bot_code = map_pc_to_bottle_code(str(b.get("id", "")))
                bot = next(
                    (bb for bb in self.productos
                     if str(bb.get("id", "")).upper() == (bot_code or "").upper()
                     and (bb.get("categoria", "").upper() == "BOTELLAS")),
                    None
                )
                if not bot: continue
                if int(nz(bot.get("cantidad_disponible"), 0)) <= 0 and not ALLOW_NO_STOCK: continue
                ml_b = extract_ml_from_text(bot.get("nombre","")) or extract_ml_from_text(b.get("nombre",""))
                if ml_b != ml_pres: continue
                bot_opts.append(b)
            if not bot_opts:
                QMessageBox.warning(self, "Sin botellas PC", "No hay botellas PC compatibles para esta presentación."); return
            botella = bot_opts[0]

        precio_pres = float(nz(pres.get("PRECIO_PRESENT"), 0.0))
        precio_bot  = float(nz(botella.get("precio_unitario"), 0.0)) if botella else 0.0
        unit_price  = precio_pres + precio_bot

        nombre_pres  = pres.get("NOMBRE") or pres.get("CODIGO_NORM") or pres.get("CODIGO")
        nombre_final = f"A LA MODE {base.get('nombre','')} {nombre_pres}".strip()

        if botella:
            codigo_final = f"{botella.get('id','')}{base.get('id','')}"
            ml = extract_ml_from_text(botella.get("nombre",""))
        else:
            codigo_final = f"{base.get('id','')}{pres.get('CODIGO_NORM') or pres.get('CODIGO')}"
            ml = ml_from_pres_code_norm(pres.get('CODIGO_NORM') or pres.get('CODIGO') or "")

        stock_base = int(nz(base.get("cantidad_disponible"), 0))
        stock_ref = stock_base
        if botella:
            stock_bot = int(nz(next((bb for bb in self.productos
                                     if str(bb.get("id","")).upper() == map_pc_to_bottle_code(str(botella.get("id",""))) and (bb.get("categoria","").upper()=="BOTELLAS")), {}).get("cantidad_disponible", 0)))
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
            "precio": float(unit_price),
            "total": round(float(unit_price) * 1.0, 2),
            "fragancia": base.get("nombre","") if dep in ("ESENCIA","ESENCIAS") else "",
            "observacion": "",
            "stock_disponible": int(stock_ref),
            "precio_override": None,
        }
        self.model.add_item(item)

    # ===== Abrir manual (usa resolve_template_path del módulo paths)
    def abrir_manual(self):
        # Busca primero en templates/<PAIS>/manual_usuario_sistema.pdf, luego en templates/
        ruta = resolve_country_asset("manual_usuario_sistema.pdf", COUNTRY_CODE)
        if not ruta or not os.path.exists(ruta):
            QMessageBox.warning(
                self,
                "Manual no encontrado",
                "No se encontró 'manual_usuario_sistema.pdf' en 'templates/<PAIS>/' ni en 'templates/'.\n"
                "Coloca el manual en 'templates/{COUNTRY_CODE}/' o en 'templates/' e inténtalo de nuevo."
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(ruta)))

    def abrir_listado_productos(self):
        dlg = ListadoProductosDialog(self, self.productos, self.presentaciones, self._agregar_por_codigo, app_icon=self._app_icon)
        main_geo = self.frameGeometry(); main_center = main_geo.center()
        dlg_size = dlg.sizeHint()
        x = main_center.x(); y = main_center.y() - dlg_size.height()
        dlg.move(x, y); dlg.exec()

    def _abrir_dialogo_observacion(self, row: int, item: dict):
        dlg = QDialog(self); dlg.setWindowTitle("Editar Observación"); dlg.resize(320, 120)
        if not self._app_icon.isNull(): dlg.setWindowIcon(self._app_icon)
        from PySide6.QtWidgets import QVBoxLayout, QLineEdit, QPushButton
        v = QVBoxLayout(dlg); v.addWidget(QLabel("Ingrese observación (ej: Color ámbar):"))
        entry = QLineEdit(); entry.setText(item.get("observacion", "")); v.addWidget(entry)
        btn = QPushButton("Guardar")
        def _save():
            item["observacion"] = entry.text().strip()
            self.model.dataChanged.emit(self.model.index(row, 0), self.model.index(row, self.model.columnCount()-1), [Qt.DisplayRole])
            dlg.accept()
        btn.clicked.connect(_save); v.addWidget(btn); dlg.exec()

    def editar_observacion(self):
        sel = self.table.selectionModel().selectedRows()
        if not sel: return
        row = sel[0].row()
        if row < 0 or row >= len(self.items): return
        item = self.items[row]
        if (item.get("categoria") or "").upper() not in ("BOTELLAS", "SERVICIO"): return
        self._abrir_dialogo_observacion(row, item)

    def editar_precio_unitario(self):
        if not CAN_EDIT_UNIT_PRICE: return
        row = self._ctx_row
        if row is None:
            sel = self.table.selectionModel().selectedRows()
            if not sel: return
            row = sel[0].row()
        if row < 0 or row >= len(self.items): return
        idx_price = self.model.index(row, 3)
        if not idx_price.isValid(): return
        self.table.setCurrentIndex(idx_price)
        self.table.edit(idx_price)

    def _recalc_price_from_rules(self, item: dict):
        cat = (item.get("categoria") or "").upper()
        qty = float(nz(item.get("cantidad"), 0.0))
        base_prod = item.get("_prod", {})
        unit_price = precio_unitario_por_categoria(cat, base_prod, qty)
        item["precio"] = float(unit_price)
        item["total"] = round(float(unit_price) * qty, 2)

    def quitar_reescritura_precio(self):
        if not CAN_EDIT_UNIT_PRICE: return
        sel = self.table.selectionModel().selectedRows()
        if not sel: return
        rows = [ix.row() for ix in sel if 0 <= ix.row() < len(self.items)]
        changed = False
        for r in rows:
            it = self.items[r]
            if it.get("precio_override") is not None:
                it["precio_override"] = None
                self._recalc_price_from_rules(it)
                changed = True
        if changed:
            top = self.model.index(min(rows), 0); bottom = self.model.index(max(rows), self.model.columnCount() - 1)
            self.model.dataChanged.emit(top, bottom, [Qt.DisplayRole, Qt.EditRole])

    def eliminar_producto(self):
        sel = self.table.selectionModel().selectedRows()
        if not sel: return
        rows = [ix.row() for ix in sel]
        self.model.remove_rows(rows)
        log.info("Items eliminados: %s", rows)

    # ====== Previsualización / PDF ======
    def previsualizar_datos(self):
        c = self.entry_cliente.text(); ci = self.entry_cedula.text(); t = self.entry_telefono.text(); items = self.items
        if not all([c, ci, t]):
            QMessageBox.warning(self, "Advertencia", "❌ Faltan datos del cliente"); return
        total_items = sum(nz(i.get("total")) for i in items) if items else 0.0
        if not items or total_items <= 0.0:
            QMessageBox.warning(self, "Advertencia", "❌ Faltan productos en la cotización"); return

        dlg = QDialog(self); dlg.setWindowTitle("Previsualización de Cotización"); dlg.resize(860, 520)
        if not self._app_icon.isNull(): self.setWindowIcon(self._app_icon); dlg.setWindowIcon(self._app_icon)
        from PySide6.QtWidgets import QVBoxLayout
        v = QVBoxLayout(dlg)
        id_lbl = id_label_for_country(APP_COUNTRY)
        v.addWidget(QLabel(f"<b>Nombre:</b> {c}"))
        v.addWidget(QLabel(f"<b>{id_lbl}:</b> {ci}"))
        v.addWidget(QLabel(f"<b>Teléfono:</b> {t}"))

        tbl = QTableWidget(0, 5)
        tbl.setHorizontalHeaderLabels(["Código", "Producto", "Cantidad", "Precio", "Subtotal"])
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tbl.setSelectionMode(QAbstractItemView.NoSelection)

        total_desc_base = 0.0; cnt_desc = 0; otros_total = 0.0
        for it in self.items:
            r = tbl.rowCount(); tbl.insertRow(r)
            prod = it["producto"]
            if it.get("fragancia"):   prod += f" ({it['fragancia']})"
            if it.get("observacion"): prod += f" | {it['observacion']}"
            qty_txt = cantidad_para_mostrar(it)
            vals = [it["codigo"], prod, qty_txt, fmt_money_ui(nz(it.get("precio"))), fmt_money_ui(nz(it.get("total")))]
            for col, val in enumerate(vals): tbl.setItem(r, col, QTableWidgetItem(str(val)))

            try:
                cat_u = (it.get("categoria") or "").upper()
                disp = int(nz(it.get("stock_disponible"), 0))
                cant = float(nz(it.get("cantidad"), 0))
                mult = 50.0 if (APP_COUNTRY in ("VENEZUELA", "PARAGUAY") and cat_u in ("ESENCIA","AROMATERAPIA","ESENCIAS")) else 1.0
                if cant * mult > disp and disp >= 0:
                    qty_item = tbl.item(r, 2)
                    if qty_item: qty_item.setForeground(QBrush(Qt.red))
            except Exception:
                pass

            if (it.get("categoria") == "PRESENTACION") and it.get("ml") and int(it["ml"]) >= 30:
                total_desc_base += float(nz(it.get("total"))); cnt_desc += int(nz(it.get("cantidad"), 0)) or 0
            else:
                otros_total += float(nz(it.get("total")))
        v.addWidget(tbl)

        desc = 0
        if cnt_desc >= 20: desc = 0.20
        elif cnt_desc >= 10: desc = 0.15
        elif cnt_desc >= 5:  desc = 0.10
        elif cnt_desc >= 3:  desc = 0.05

        tot_desc = round(total_desc_base * (1 - desc), 2)
        ahorro   = round(total_desc_base - tot_desc, 2)
        total_general = round(tot_desc + otros_total, 2)

        v.addWidget(QLabel(f"<b>Total Presentaciones ≥30ml:</b> {fmt_money_ui(total_desc_base)}"))
        if desc > 0:
            v.addWidget(QLabel(f"<b>Descuento ({int(desc * 100)}%):</b> -{fmt_money_ui(ahorro)}"))
            v.addWidget(QLabel(f"<b>Presentaciones con descuento:</b> {fmt_money_ui(tot_desc)}"))
        v.addWidget(QLabel(f"<b>Insumos/Otros:</b> {fmt_money_ui(otros_total)}"))
        v.addWidget(QLabel(f"<b>Total General:</b> {fmt_money_ui(total_general)}"))

        btn = QPushButton("Cerrar"); btn.clicked.connect(dlg.accept); v.addWidget(btn)
        dlg.exec()

    def generar_cotizacion(self):
        c = self.entry_cliente.text(); ci = self.entry_cedula.text(); t = self.entry_telefono.text()
        if not all([c, ci, t]):
            QMessageBox.warning(self, "Advertencia", "❌ Faltan datos del cliente"); return
        total_items = sum(nz(i.get("total")) for i in self.items) if self.items else 0.0
        if not self.items or total_items <= 0:
            QMessageBox.warning(self, "Advertencia", "❌ Agrega al menos un producto a la cotización"); return
        datos = {
            "fecha": datetime.datetime.now().strftime("%d/%m/%Y"),
            "cliente": c, "cedula": ci, "telefono": t,
            "metodo_pago": "Transferencia", "items": self.items
        }
        try:
            ruta = generar_pdf(datos)
            log.info("PDF generado en %s", ruta)
            QMessageBox.information(self, "PDF Generado", f"📄 Cotización generada:\n{ruta}")
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(COTIZACIONES_DIR)))
        except Exception as e:
            log.exception("Error al generar PDF")
            QMessageBox.critical(self, "Error al generar PDF", f"❌ No se pudo generar la cotización:\n{e}")

    def limpiar_formulario(self):
        self.entry_cliente.clear(); self.entry_cedula.clear(); self.entry_telefono.clear(); self.entry_producto.clear()
        self.model.remove_rows(list(range(len(self.items))))
        log.info("Formulario limpiado")
