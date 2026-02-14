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
from PySide6.QtCore import Qt, QUrl, QModelIndex, QTimer, QEvent

from ..paths import BASE_APP_TITLE, DATA_DIR, COTIZACIONES_DIR
from ..config import APP_COUNTRY, id_label_for_country, listing_allows_products, listing_allows_presentations
from .models import ItemsModel
from .delegates import QuantityDelegate
from ..widgets import Toast


class UiMixin:
    # ✅ umbral nuevo
    REC_P_THRESHOLD = 0.20

    def _update_title_with_client(self, text: str):
        name = (text or "").strip()
        self.setWindowTitle(f"{name} - {BASE_APP_TITLE}" if name else BASE_APP_TITLE)

    def _on_ai_client_picked(self, payload: dict):
        cli = str(payload.get("cliente") or "").strip()
        doc = str(payload.get("cedula") or "").strip()
        tel = str(payload.get("telefono") or "").strip()

        try:
            self.entry_cliente.setText(cli)
            self.entry_cedula.setText(doc)
            self.entry_telefono.setText(tel)
        except Exception:
            pass

        for attr in ("_ai_cli", "_ai_doc", "_ai_tel"):
            sc = getattr(self, attr, None)
            if sc is not None:
                try:
                    sc.hide_popup()
                except Exception:
                    pass

        try:
            self._focus_product_search(clear=True)
        except Exception:
            pass

        # ✅ refrescar preview recs cuando cambia cliente
        self._schedule_refresh_recs_preview()

    def _on_ai_product_picked(self, payload: dict):
        codigo = str(payload.get("codigo") or payload.get("id") or "").strip()
        if not codigo:
            return

        try:
            self._suppress_next_return = True
        except Exception:
            pass

        self._agregar_por_codigo(codigo)
        try:
            self.entry_producto.clear()
        except Exception:
            pass

        self._schedule_refresh_recs_preview()

    def _on_ai_enter_pressed(self):
        if bool(getattr(self, "_suppress_next_return", False)):
            self._suppress_next_return = False
            return

        text = (self.entry_producto.text() or "").strip()
        if not text:
            return
        cod = text.split(" - ")[0].strip()
        if not cod:
            return
        self._agregar_por_codigo(cod)
        try:
            self.entry_producto.clear()
        except Exception:
            pass

        self._schedule_refresh_recs_preview()

    def _setup_ai_completers(self):
        if getattr(self, "_ai_prod", None) is not None:
            return

        try:
            from ..db_path import resolve_db_path
            from ..ai.search_index import LocalSearchIndex
            from ..ai.smart_completer import SmartCompleter
            from ..ai.recommender import QuoteRecommender

            dbp = resolve_db_path()
            self._ai_index = LocalSearchIndex(dbp)
            self._rec_engine = QuoteRecommender(dbp)

            self._ai_prod = SmartCompleter(
                self.entry_producto,
                index=self._ai_index,
                kind="product",
                parent=self,
            )
            self._ai_prod.picked.connect(self._on_ai_product_picked)

            self._ai_cli = SmartCompleter(self.entry_cliente, index=self._ai_index, kind="client", parent=self)
            self._ai_cli.picked.connect(self._on_ai_client_picked)

            self._ai_doc = SmartCompleter(self.entry_cedula, index=self._ai_index, kind="client", parent=self)
            self._ai_doc.picked.connect(self._on_ai_client_picked)

            self._ai_tel = SmartCompleter(self.entry_telefono, index=self._ai_index, kind="client", parent=self)
            self._ai_tel.picked.connect(self._on_ai_client_picked)

            # ✅ al activar AI, pinta preview de recomendaciones si hay
            self._schedule_refresh_recs_preview()

        except Exception:
            return

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
            self._schedule_refresh_recs_preview()

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

    def _focus_last_row(self, row_index: int):
        try:
            r = row_index if isinstance(row_index, int) else (self.model.rowCount() - 1)
            if r < 0:
                return

            # solo enfocar si es una fila real (no placeholder)
            real_rows = len(getattr(self.model, "_items", []) or [])
            if r >= real_rows:
                r = max(0, real_rows - 1)

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

    def _is_py_cash_mode(self) -> bool:
        return bool(getattr(self, "_py_cash_mode", False))

    def _set_py_cash_mode(self, enabled: bool, *, assume_items_already: bool = False):
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

    # =============================
    # ✅ SMART AUTOCOMPLETE (recomendador)
    # =============================
    def _client_triplet_for_recs(self):
        cli = (self.entry_cliente.text() or "").strip()
        doc = (self.entry_cedula.text() or "").strip()
        tel = (self.entry_telefono.text() or "").strip()
        # Cliente “válido” SOLO si están los 3
        if cli and doc and tel:
            return (cli, doc, tel)
        return None

    def _current_code_prices(self) -> dict[str, set[float]]:
        mp: dict[str, set[float]] = {}
        for it in (getattr(self, "items", []) or []):
            code = str(it.get("codigo") or "").strip().upper()
            if not code:
                continue
            try:
                p = float(it.get("precio_override")) if it.get("precio_override") is not None else float(it.get("precio") or 0.0)
            except Exception:
                p = 0.0
            mp.setdefault(code, set()).add(round(p, 6))
        return mp

    def _get_recommendations(self, *, limit: int = 10):
        eng = getattr(self, "_rec_engine", None)
        if eng is None:
            return []

        seeds = [str(it.get("codigo") or "").strip().upper() for it in (self.items or []) if str(it.get("codigo") or "").strip()]
        client_triplet = self._client_triplet_for_recs()

        # Si no hay cliente completo, solo recomendamos si ya hay seeds
        if client_triplet is None and not seeds:
            return []

        recs = eng.recommend(
            client_triplet=client_triplet,
            seeds=seeds,
            limit=int(limit),
            p_threshold=float(self.REC_P_THRESHOLD),  # ✅ 20%
            min_support=2,
        )

        # filtra por config de listado (si algo cambió)
        out = []
        for r in recs:
            if r.kind in ("presentation", "pc"):
                if not listing_allows_presentations():
                    continue
            else:
                if not listing_allows_products():
                    continue
            out.append(r)
        return out

    def _ai_product_popup_visible(self) -> bool:
        sc = getattr(self, "_ai_prod", None)
        if sc is None:
            return False

        # intenta métodos “obvios”
        for name in ("is_popup_visible", "isPopupVisible", "popup_visible", "popupVisible"):
            fn = getattr(sc, name, None)
            if callable(fn):
                try:
                    return bool(fn())
                except Exception:
                    pass

        # intenta atributos de popup
        for name in ("popup", "_popup", "popup_widget", "_popup_widget"):
            pop = getattr(sc, name, None)
            if pop is not None:
                try:
                    return bool(pop.isVisible())
                except Exception:
                    pass

        return False

    def _find_last_row_by_code(self, code_u: str) -> int | None:
        try:
            items = getattr(self, "items", []) or []
            code_u = str(code_u or "").strip().upper()
            for i in range(len(items) - 1, -1, -1):
                if str(items[i].get("codigo") or "").strip().upper() == code_u:
                    return i
        except Exception:
            pass
        return None

    def _force_qty_price_on_row(self, row: int, qty: float, price_base: float):
        """
        Fuerza qty y precio recomendado en la fila real usando setData (recalcula totales).
        """
        try:
            if getattr(self, "model", None) is None:
                return

            try:
                q = float(qty)
            except Exception:
                q = 1.0
            if q <= 0:
                q = 1.0

            qty_str = f"{q:.3f}" if q < 1 or (abs(q - round(q)) > 1e-9) else str(int(round(q)))

            try:
                self.model.setData(self.model.index(row, 3), qty_str, Qt.EditRole)
            except Exception:
                pass

            try:
                pr = float(price_base)
            except Exception:
                pr = 0.0

            if pr > 0:
                try:
                    self.model.setData(self.model.index(row, 4), {"mode": "custom", "price": pr}, Qt.EditRole)
                except Exception:
                    pass

        except Exception:
            pass

    def _add_recommended_item(self, code_u: str, qty: float, price_base: float, reason: str = "") -> bool:
        """
        Agrega un recomendado y GARANTIZA que quede con qty y precio recomendados,
        aunque agregar_recomendado() no los aplique o retorne False por diseño.
        """
        code_u = str(code_u or "").strip().upper()
        if not code_u:
            return False

        try:
            q = float(qty)
        except Exception:
            q = 1.0
        if q <= 0:
            q = 1.0

        try:
            pr = float(price_base)
        except Exception:
            pr = 0.0

        # evitar duplicado por (codigo + precio)
        cur = self._current_code_prices()
        p6 = round(pr, 6) if pr > 0 else 0.0
        if code_u in cur and p6 > 0 and p6 in cur.get(code_u, set()):
            try:
                Toast.notify(self, "Ese recomendado ya está agregado.", duration_ms=2200, fade_ms=700)
            except Exception:
                pass
            return True

        items_before = getattr(self, "items", []) or []
        old_len = len(items_before)

        # 1) intentar tu flujo recomendado
        ret_ok = False
        try:
            ret_ok = bool(self.agregar_recomendado(code_u, qty=q, precio_override_base=pr))
        except Exception:
            ret_ok = False

        # ✅ FIX: aunque retorne False, si realmente agregó filas NO hacemos fallback
        items_after = getattr(self, "items", []) or []
        grew = len(items_after) > old_len
        ok = bool(ret_ok or grew)

        # 2) si NO agregó nada, fallback a agregar normal
        if not ok:
            try:
                self._agregar_por_codigo(code_u)
                ok = True
            except Exception:
                ok = False

        if not ok:
            return False

        # detecta fila real a ajustar
        row = self._find_last_row_by_code(code_u)

        # si no encontró por código, asume que se agregó al final (solo si creció exactamente 1)
        items = getattr(self, "items", []) or []
        if row is None and len(items) == old_len + 1:
            row = len(items) - 1

        if row is not None:
            self._force_qty_price_on_row(row, q, pr)

        if reason:
            try:
                Toast.notify(self, reason, duration_ms=4200, fade_ms=900)
            except Exception:
                pass

        self._schedule_refresh_recs_preview()
        return True

    def _apply_recommendation(self, rec) -> bool:
        try:
            code_u = str(rec.codigo or "").strip().upper()
        except Exception:
            code_u = ""

        try:
            qty = float(getattr(rec, "qty", 1.0) or 1.0)
        except Exception:
            qty = 1.0

        try:
            pr = float(getattr(rec, "price_base", 0.0) or 0.0)
        except Exception:
            pr = 0.0

        try:
            reason = str(getattr(rec, "reason", "") or "").strip()
        except Exception:
            reason = ""

        return self._add_recommended_item(code_u, qty, pr, reason=reason)

    # =============================
    # ✅ CLICK en placeholders (doble clic / Enter)
    # =============================
    def _try_add_preview_from_row(self, row: int) -> bool:
        try:
            model = getattr(self, "model", None)
            if model is None:
                return False

            try:
                payload = model.get_preview_payload(int(row)) if hasattr(model, "get_preview_payload") else None
            except Exception:
                payload = None

            if not payload:
                return False

            code_u = str(payload.get("codigo") or "").strip().upper()
            try:
                qty = float(payload.get("qty") or 1.0)
            except Exception:
                qty = 1.0
            try:
                pr = float(payload.get("price_base") or 0.0)
            except Exception:
                pr = 0.0
            reason = str(payload.get("reason") or "").strip()

            if not code_u:
                return False

            return self._add_recommended_item(code_u, qty, pr, reason=reason)

        except Exception:
            return False

    def _double_click_tabla(self, idx: QModelIndex):
        try:
            if not idx or not idx.isValid():
                return

            row = idx.row()
            col = idx.column()

            model = getattr(self, "model", None)
            if model is None:
                return

            # ✅ Si es preview: doble clic agrega
            try:
                if hasattr(model, "is_preview_row") and bool(model.is_preview_row(row)):
                    self._try_add_preview_from_row(row)
                    return
            except Exception:
                pass

            # ✅ Si es item real: restaurar UX de doble clic
            try:
                self.table.selectRow(row)
                self.table.setCurrentIndex(model.index(row, col))
            except Exception:
                pass

            it = None
            try:
                items = getattr(self, "items", []) or []
                if 0 <= row < len(items):
                    it = items[row]
            except Exception:
                it = None

            cat = str((it or {}).get("categoria") or "").upper()

            if col == 4:
                try:
                    self.editar_precio_unitario()
                except Exception:
                    pass
                return

            if col == 2:
                try:
                    self.editar_descuento_item()
                except Exception:
                    pass
                return

            if col == 1 and cat == "BOTELLAS":
                try:
                    self.editar_observacion()
                except Exception:
                    pass
                return

        except Exception:
            pass

    def _tab_autocomplete_next(self) -> bool:
        try:
            seeds = tuple([str(it.get("codigo") or "").strip().upper() for it in (self.items or []) if str(it.get("codigo") or "").strip()])
            trip = self._client_triplet_for_recs()
            sig = (trip, seeds, float(self.REC_P_THRESHOLD), bool(listing_allows_products()), bool(listing_allows_presentations()))
        except Exception:
            sig = None

        if getattr(self, "_tab_rec_sig", None) != sig or getattr(self, "_tab_recs", None) is None:
            self._tab_rec_sig = sig
            self._tab_recs = self._get_recommendations(limit=12)
            self._tab_rec_i = 0

        recs = getattr(self, "_tab_recs", []) or []
        i = int(getattr(self, "_tab_rec_i", 0))

        if not recs:
            return False

        tried = 0
        while tried < len(recs):
            if i >= len(recs):
                i = 0
            rec = recs[i]
            i += 1
            tried += 1
            if self._apply_recommendation(rec):
                self._tab_rec_i = i
                try:
                    self.entry_producto.clear()
                    self.entry_producto.setFocus()
                except Exception:
                    pass
                return True

        self._tab_rec_i = i
        return False

    def _on_autocomplete_clicked(self):
        recs = self._get_recommendations(limit=12)
        if not recs:
            try:
                Toast.notify(self, "No hay recomendaciones con suficiente confianza (≥20%).", duration_ms=2500, fade_ms=800)
            except Exception:
                pass
            return

        mb = QMessageBox(self)
        mb.setWindowTitle("Autocompletar productos")
        mb.setIcon(QMessageBox.Question)
        mb.setText(f"Encontré {len(recs)} recomendación(es) con confianza ≥20%.\n¿Qué deseas hacer?")

        btn_one = mb.addButton("Agregar primero", QMessageBox.AcceptRole)
        btn_all = mb.addButton("Agregar todos", QMessageBox.YesRole)
        btn_cancel = mb.addButton("Cancelar", QMessageBox.RejectRole)

        mb.exec()
        clicked = mb.clickedButton()

        if clicked is btn_one:
            self._apply_recommendation(recs[0])
            return

        if clicked is btn_all:
            added = 0
            for r in recs:
                if self._apply_recommendation(r):
                    added += 1
            try:
                Toast.notify(self, f"Autocompletar: agregados {added} ítem(s).", duration_ms=2500, fade_ms=800)
            except Exception:
                pass
            return

    def _schedule_refresh_recs_preview(self):
        try:
            if getattr(self, "_rec_prev_timer", None) is None:
                self._rec_prev_timer = QTimer(self)
                self._rec_prev_timer.setSingleShot(True)
                self._rec_prev_timer.timeout.connect(self._refresh_recs_preview_now)
            self._rec_prev_timer.start(220)
        except Exception:
            pass

    def _refresh_recs_preview_now(self):
        try:
            if getattr(self, "model", None) is None:
                return
            if not hasattr(self.model, "set_recommendations_preview"):
                return

            eng = getattr(self, "_rec_engine", None)
            if eng is None:
                try:
                    self.model.clear_recommendations_preview()
                except Exception:
                    pass
                return

            recs = self._get_recommendations(limit=6)
            if not recs:
                try:
                    self.model.clear_recommendations_preview()
                except Exception:
                    pass
                return

            cur = self._current_code_prices()
            payload = []
            for r in recs:
                code_u = str(r.codigo or "").strip().upper()
                try:
                    pr = round(float(r.price_base or 0.0), 6)
                except Exception:
                    pr = 0.0
                if code_u in cur and pr > 0 and pr in cur.get(code_u, set()):
                    continue

                try:
                    cat = getattr(r, "categoria", None)
                    if cat is None:
                        cat = getattr(r, "category", None)
                    if cat is None:
                        cat = getattr(r, "cat", None)
                    cat = str(cat or "").strip()
                except Exception:
                    cat = ""

                payload.append({
                    "codigo": code_u,
                    "nombre": str(r.nombre or "").strip(),
                    "qty": float(r.qty or 1.0),
                    "price_base": float(r.price_base or 0.0),
                    "score": float(r.score or 0.0),
                    "reason": str(r.reason or "").strip(),
                    "kind": str(r.kind or ""),
                    "categoria": cat,
                })

            if payload:
                self.model.set_recommendations_preview(payload)
            else:
                self.model.clear_recommendations_preview()

        except Exception:
            pass

    def eventFilter(self, obj, ev):
        try:
            if obj is getattr(self, "entry_producto", None) and ev.type() == QEvent.KeyPress:
                key = ev.key()

                if key == Qt.Key_F9:
                    if not self._tab_autocomplete_next():
                        try:
                            Toast.notify(self, "No hay recomendaciones con suficiente confianza (≥20%).", duration_ms=2500, fade_ms=800)
                        except Exception:
                            pass
                    return True

                if key in (Qt.Key_Return, Qt.Key_Enter):
                    txt = (self.entry_producto.text() or "").strip()
                    if not txt:
                        if self._ai_product_popup_visible():
                            return False
                        if not self._tab_autocomplete_next():
                            try:
                                Toast.notify(self, "No hay recomendaciones con suficiente confianza (≥20%).", duration_ms=2500, fade_ms=800)
                            except Exception:
                                pass
                        return True

            if obj is getattr(self, "table", None) and ev.type() == QEvent.KeyPress:
                if ev.key() in (Qt.Key_Return, Qt.Key_Enter):
                    try:
                        idx = self.table.currentIndex()
                        if idx and idx.isValid():
                            row = idx.row()
                            if hasattr(self.model, "is_preview_row") and bool(self.model.is_preview_row(row)):
                                if self._try_add_preview_from_row(row):
                                    return True
                    except Exception:
                        pass

        except Exception:
            pass

        try:
            return super().eventFilter(obj, ev)
        except Exception:
            return False

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main = QVBoxLayout(central)

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

        try:
            self.entry_cliente.textChanged.connect(lambda _=None: self._schedule_refresh_recs_preview())
            self.entry_cedula.textChanged.connect(lambda _=None: self._schedule_refresh_recs_preview())
            self.entry_telefono.textChanged.connect(lambda _=None: self._schedule_refresh_recs_preview())
        except Exception:
            pass

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

        grp_bus = QGroupBox("Búsqueda de Productos")
        vbus = QVBoxLayout()
        hbus = QHBoxLayout()

        self.entry_producto = QLineEdit()
        self.entry_producto.setPlaceholderText("Código, nombre, categoría o tipo")
        if bool(getattr(self, "_use_ai_completer", False)):
            self.entry_producto.returnPressed.connect(self._on_ai_enter_pressed)
        else:
            self.entry_producto.returnPressed.connect(self._on_return_pressed)

        self.entry_producto.installEventFilter(self)

        lbl_bus = QLabel("Código o Nombre:")

        btn_agregar_srv = QPushButton("Agregar Servicio")
        self._apply_btn_responsive(btn_agregar_srv, 110, 36)
        btn_agregar_srv.setToolTip("Agregar un ítem de tipo SERVICIO / personalizado")
        btn_agregar_srv.clicked.connect(self.agregar_producto_personalizado)

        self.btn_autocompletar = QPushButton("Autocompletar")
        self._apply_btn_responsive(self.btn_autocompletar, 125, 36)
        self.btn_autocompletar.setToolTip("Agrega productos recomendados por historial (F9 o Enter vacío agrega 1 a 1).")
        self.btn_autocompletar.clicked.connect(self._on_autocomplete_clicked)

        hbus.addWidget(lbl_bus)
        hbus.addWidget(self.entry_producto)
        hbus.addWidget(btn_agregar_srv)
        hbus.addWidget(self.btn_autocompletar)

        vbus.addLayout(hbus)
        grp_bus.setLayout(vbus)
        main.addWidget(grp_bus)

        grp_tab = QGroupBox("Productos Seleccionados")
        vtab = QVBoxLayout()
        self.table = QTableView()
        self.model = ItemsModel(self.items)
        self.table.setModel(self.model)

        self.table.installEventFilter(self)

        try:
            self.model.toast_requested.connect(lambda msg: Toast.notify(self, msg, duration_ms=4000, fade_ms=1000))
        except Exception:
            pass

        try:
            self.model.item_added.connect(lambda *_: self._schedule_refresh_recs_preview())
            self.model.rowsRemoved.connect(lambda *_: self._schedule_refresh_recs_preview())
            self.model.modelReset.connect(lambda *_: self._schedule_refresh_recs_preview())
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

        self._schedule_refresh_recs_preview()
