# src/app_window_parts/ui.py
from __future__ import annotations

import copy
import os
import re

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
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
    QComboBox,
)
from PySide6.QtGui import QAction, QKeySequence, QShortcut, QDesktopServices, QRegularExpressionValidator
from PySide6.QtCore import Qt, QUrl, QModelIndex, QTimer, QEvent, QRegularExpression

from ..paths import BASE_APP_TITLE, DATA_DIR, COTIZACIONES_DIR
from ..config import APP_COUNTRY, COUNTRY_CODE, listing_allows_products, listing_allows_presentations
from .models import ItemsModel
from .delegates import QuantityDelegate, InlineTextDelegate
from ..widgets import Toast
from ..widgets_parts.excel_table_behavior import ExcelTableController
from sqlModels.quotes_repo import (
    doc_regex_for_country,
    document_type_rule,
    document_type_rules_for_country,
    infer_tipo_documento_from_doc,
    resolve_doc_type_for_form,
    validate_document_for_type,
)


_DOC_REGEX_BY_COUNTRY: dict[str, str] = {
    # VE: V/E/J/P/G
    "VENEZUELA": doc_regex_for_country("VE"),
    # PE: DNI/CE/RUC/P
    "PERU": doc_regex_for_country("PE"),
    # PY: CI/RUC/P
    "PARAGUAY": doc_regex_for_country("PY"),
}

_PHONE_REGEX_BY_COUNTRY: dict[str, str] = {
    "VENEZUELA": r"^(?:0[24]\d{9}|(?:\+58|58)[24]\d{9})$",
    "PERU": r"^(?:(?:\+51|51)?9\d{8})$",
    "PARAGUAY": r"^(?:(?:\+595|595)?9\d{8}|0?9\d{8})$",
}

_DOC_HINT_BY_COUNTRY: dict[str, str] = {
    "VENEZUELA": "V/E/J/P/G",
    "PERU": "DNI/CE/RUC/P",
    "PARAGUAY": "CI/RUC/P",
}

_PHONE_HINT_BY_COUNTRY: dict[str, str] = {
    "VENEZUELA": "04121234567 o +584121234567",
    "PERU": "912345678 o +51912345678",
    "PARAGUAY": "0981123456 o +595981123456",
}

class UiMixin:
    # ✅ umbral nuevo
    REC_P_THRESHOLD = 0.20

    def _doc_type_rules(self) -> list[dict]:
        return document_type_rules_for_country(COUNTRY_CODE)

    def _selected_doc_type(self) -> str:
        cb = getattr(self, "combo_tipo_documento", None)
        if cb is None:
            return ""
        try:
            raw = cb.currentData()
            if raw is None:
                raw = cb.currentText()
            return str(raw or "").strip().upper()
        except Exception:
            return ""

    def _set_selected_doc_type(self, doc_type: str):
        cb = getattr(self, "combo_tipo_documento", None)
        if cb is None:
            return False
        t = resolve_doc_type_for_form(COUNTRY_CODE, "", doc_type)
        if not t:
            return False
        i = cb.findData(t)
        if i < 0:
            i = cb.findText(t, Qt.MatchFixedString)
        if i >= 0:
            cb.setCurrentIndex(i)
            return True
        return False

    def _resolve_doc_type_for_form(self, doc: str, doc_type: str = "") -> str:
        return resolve_doc_type_for_form(COUNTRY_CODE, str(doc or ""), str(doc_type or ""))

    def _doc_regex_pattern(self, *, doc_type: str | None = None) -> str:
        dt = str(doc_type or self._selected_doc_type() or "").strip().upper()
        if dt:
            rule = document_type_rule(COUNTRY_CODE, dt)
            if rule:
                pat = str(rule.get("regex_validation") or "").strip()
                if pat:
                    return pat
        return _DOC_REGEX_BY_COUNTRY.get(APP_COUNTRY, r"^[A-Za-z0-9\-]{4,20}$")

    def _phone_regex_pattern(self) -> str:
        # Telefono sin formato obligatorio.
        return r"^.*$"

    def _doc_regex_hint(self, *, doc_type: str | None = None) -> str:
        dt = str(doc_type or self._selected_doc_type() or "").strip().upper()
        if dt:
            rule = document_type_rule(COUNTRY_CODE, dt)
            if rule:
                desc = str(rule.get("descripcion") or "").strip().upper()
                pad = int(rule.get("validation_pad") or 0)
                if pad > 0:
                    return f"{dt} ({desc}) - {pad} caracteres"
                return f"{dt} ({desc}) - longitud variable"
        return _DOC_HINT_BY_COUNTRY.get(APP_COUNTRY, "DOC-123456")

    def _phone_regex_hint(self) -> str:
        return "Sin formato obligatorio"

    def _on_doc_type_changed(self, _idx: int = -1):
        self._apply_client_validators()
        try:
            self._schedule_refresh_recs_preview()
        except Exception:
            pass

    def _apply_client_validators(self):
        try:
            rx_doc = QRegularExpression(self._doc_regex_pattern(doc_type=self._selected_doc_type()))
            self.entry_cedula.setValidator(QRegularExpressionValidator(rx_doc, self.entry_cedula))
            self.entry_telefono.setValidator(None)
            self.entry_cedula.setPlaceholderText(self._doc_regex_hint(doc_type=self._selected_doc_type()))
            self.entry_telefono.setPlaceholderText(self._phone_regex_hint())
        except Exception:
            pass

    def _infer_tipo_documento(self, doc: str) -> str:
        return infer_tipo_documento_from_doc(COUNTRY_CODE, str(doc or ""))

    def _validate_doc_phone_values(
        self,
        doc: str,
        phone: str,
        *,
        direccion: str = "",
        email: str = "",
    ) -> tuple[bool, str, str]:
        d = str(doc or "").strip()
        t = str(phone or "").strip()
        addr = str(direccion or "").strip()
        mail = str(email or "").strip()
        doc_type = self._selected_doc_type()
        if not doc_type:
            return (
                False,
                "Selecciona un tipo de documento.",
                "",
            )
        ok_doc, doc_err = validate_document_for_type(COUNTRY_CODE, doc_type, d)
        if not ok_doc:
            return (
                False,
                (
                    f"Documento invalido para {APP_COUNTRY}.\n"
                    f"{doc_err}\n"
                    f"Formato permitido: {self._doc_regex_hint(doc_type=doc_type)}"
                ),
                "",
            )
        if not t:
            return (
                False,
                (
                    "Ingresa un telefono."
                ),
                "",
            )
        if not addr:
            return (
                False,
                (
                    "Ingresa una direccion."
                ),
                "",
            )
        if not mail:
            return (
                False,
                (
                    "Ingresa un email."
                ),
                "",
            )
        return True, "", str(doc_type or "").strip().upper()

    def _update_title_with_client(self, text: str):
        name = (text or "").strip()
        self.setWindowTitle(f"{name} - {BASE_APP_TITLE}" if name else BASE_APP_TITLE)

    def _on_ai_client_picked(self, payload: dict):
        cli = str(payload.get("cliente") or "").strip()
        doc = str(payload.get("cedula") or "").strip()
        tel = str(payload.get("telefono") or "").strip()
        addr = str(payload.get("direccion") or "-").strip() or "-"
        mail = str(payload.get("email") or "-").strip() or "-"
        doc_type = str(payload.get("tipo_documento") or "").strip().upper()
        if not doc_type and "-" in doc:
            pref, body = doc.split("-", 1)
            pref = str(pref or "").strip().upper()
            body = str(body or "").strip()
            if pref and body:
                doc_type = pref
                doc = body
        elif doc_type and doc.upper().startswith(f"{doc_type}-"):
            doc = doc[len(doc_type) + 1 :].strip()

        doc_type = self._resolve_doc_type_for_form(doc, doc_type)

        try:
            self.entry_cliente.setText(cli)
            if doc_type:
                self._set_selected_doc_type(doc_type)
            self.entry_cedula.setText(doc)
            self.entry_telefono.setText(tel)
            if getattr(self, "entry_direccion", None) is not None:
                self.entry_direccion.setText(addr)
            if getattr(self, "entry_email", None) is not None:
                self.entry_email.setText(mail)
        except Exception:
            pass

        for attr in ("_ai_cli", "_ai_doc", "_ai_tel", "_ai_dir", "_ai_email"):
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
        cod = text
        for sep in (" - ", " — ", " – ", " â€” ", " â€“ "):
            if sep in cod:
                cod = cod.split(sep, 1)[0].strip()
                break
        if not cod:
            return
        ok = self._agregar_por_codigo(cod, silent=True)
        if not ok:
            try:
                idx = getattr(self, "_ai_index", None)
                if idx is not None:
                    rows = idx.search_products(cod, limit=1) or []
                    if rows:
                        alt = str(rows[0].get("codigo") or rows[0].get("id") or "").strip()
                        if alt:
                            ok = self._agregar_por_codigo(alt, silent=True)
            except Exception:
                pass
        if not ok:
            self._agregar_por_codigo(cod, silent=False)
        try:
            self.entry_producto.clear()
        except Exception:
            pass

        self._schedule_refresh_recs_preview()

    def _handle_product_return_pressed(self):
        if bool(getattr(self, "_use_ai_completer", False)):
            self._on_ai_enter_pressed()
            return
        self._on_return_pressed()

    def _ensure_recommendation_engine(self):
        if getattr(self, "_rec_engine", None) is not None:
            return
        try:
            from ..db_path import resolve_db_path
            from ..ai.recommender import QuoteRecommender

            self._rec_engine = QuoteRecommender(resolve_db_path())
        except Exception:
            self._rec_engine = None

    def _ensure_ai_search_index(self):
        idx = getattr(self, "_ai_index", None)
        if idx is not None:
            return idx

        from ..db_path import resolve_db_path
        from ..ai.search_index import LocalSearchIndex

        idx = LocalSearchIndex(resolve_db_path())
        self._ai_index = idx
        try:
            idx.prewarm_async()
        except Exception:
            pass
        return idx

    def _ensure_client_search_index(self):
        idx = getattr(self, "_client_index", None)
        if idx is not None:
            return idx

        from ..db_path import resolve_db_path
        from ..ai.search_index import LocalSearchIndex

        idx = LocalSearchIndex(resolve_db_path(), auto_create_fts=False)
        self._client_index = idx
        return idx

    def _setup_client_completers(self):
        if getattr(self, "_ai_cli", None) is not None:
            return

        try:
            from ..ai.smart_completer import SmartCompleter

            idx = self._ensure_client_search_index()

            self._ai_cli = SmartCompleter(self.entry_cliente, index=idx, kind="client", parent=self)
            self._ai_cli.picked.connect(self._on_ai_client_picked)

            self._ai_doc = SmartCompleter(self.entry_cedula, index=idx, kind="client", parent=self)
            self._ai_doc.picked.connect(self._on_ai_client_picked)

            self._ai_tel = SmartCompleter(self.entry_telefono, index=idx, kind="client", parent=self)
            self._ai_tel.picked.connect(self._on_ai_client_picked)

            self._ai_dir = SmartCompleter(self.entry_direccion, index=idx, kind="client", parent=self)
            self._ai_dir.picked.connect(self._on_ai_client_picked)

            self._ai_email = SmartCompleter(self.entry_email, index=idx, kind="client", parent=self)
            self._ai_email.picked.connect(self._on_ai_client_picked)

        except Exception:
            return

    def _setup_ai_completers(self):
        if getattr(self, "_ai_prod", None) is None:
            try:
                from ..ai.smart_completer import SmartCompleter

                idx = self._ensure_ai_search_index()
                self._ai_prod = SmartCompleter(
                    self.entry_producto,
                    index=idx,
                    kind="product",
                    parent=self,
                    debounce_ms=0,
                )
                self._ai_prod.picked.connect(self._on_ai_product_picked)
            except Exception:
                return

        try:
            self._setup_client_completers()
        except Exception:
            pass

        # al activar AI, pinta preview de recomendaciones si hay
        self._schedule_refresh_recs_preview()

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
            if not bool(getattr(self, "_window_state_restored", False)):
                self._center_on_screen()
            self._schedule_refresh_recs_preview()

    def _wire_enter_flow(self):
        try:
            self.entry_cedula.returnPressed.connect(self._go_name)
            self.entry_cliente.returnPressed.connect(self._go_phone)
            self.entry_telefono.returnPressed.connect(self._go_address)
            self.entry_direccion.returnPressed.connect(self._go_email)
            self.entry_email.returnPressed.connect(self._go_product_search)
        except Exception:
            pass

    def _go_doc(self):
        try:
            self.entry_cedula.setFocus()
            self.entry_cedula.selectAll()
        except Exception:
            pass

    def _go_name(self):
        try:
            self.entry_cliente.setFocus()
            self.entry_cliente.selectAll()
        except Exception:
            pass

    def _go_phone(self):
        try:
            self.entry_telefono.setFocus()
            self.entry_telefono.selectAll()
        except Exception:
            pass

    def _go_address(self):
        try:
            self.entry_direccion.setFocus()
            self.entry_direccion.selectAll()
        except Exception:
            pass

    def _go_email(self):
        try:
            self.entry_email.setFocus()
            self.entry_email.selectAll()
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
        if bool(getattr(self, "_suppress_focus_last_row", False)):
            return
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

            def _qty_editor_is_active() -> bool:
                try:
                    if self.table.state() != QAbstractItemView.EditingState:
                        return False
                    cur = self.table.currentIndex()
                    if (not cur.isValid()) or cur.row() != r or cur.column() != 3:
                        return False
                    fw = QApplication.focusWidget()
                    if fw is None or fw is getattr(self, "entry_producto", None):
                        return False
                    return bool(self.table.isAncestorOf(fw))
                except Exception:
                    return False

            def _start_qty_edit(retries: int = 6):
                try:
                    self.table.setCurrentIndex(idx_qty)
                    self.table.scrollTo(idx_qty, QAbstractItemView.PositionAtBottom)
                    if QApplication.activeModalWidget() is None:
                        self.table.setFocus()
                    self.table.edit(idx_qty)
                    if _qty_editor_is_active():
                        fw = QApplication.focusWidget()
                        if isinstance(fw, QLineEdit):
                            fw.selectAll()
                        return
                    if retries > 0:
                        QTimer.singleShot(30, lambda: _start_qty_edit(retries - 1))
                except Exception:
                    pass

            QTimer.singleShot(0, _start_qty_edit)

        except Exception:
            pass

    def _on_qty_editor_closed(self, editor, hint):
        try:
            if hint == QAbstractItemDelegate.RevertModelCache:
                return

            def _return_to_product_input():
                self._focus_product_search(clear=True)
                if QApplication.focusWidget() is not getattr(self, "entry_producto", None):
                    QTimer.singleShot(25, lambda: self._focus_product_search(clear=True))

            QTimer.singleShot(0, _return_to_product_input)
        except Exception:
            pass

    def _move_inline_to_right(self, row: int, col: int):
        try:
            model = getattr(self, "model", None)
            table = getattr(self, "table", None)
            if model is None or table is None:
                return
            if row < 0 or col < 0:
                return
            cols = int(model.columnCount())
            rows = int(model.rowCount())
            if rows <= 0 or cols <= 0 or row >= rows:
                return

            next_col = min(col + 1, cols - 1)
            idx_next = model.index(row, next_col)
            if not idx_next.isValid():
                return

            table.setCurrentIndex(idx_next)
            table.scrollTo(idx_next, QAbstractItemView.PositionAtCenter)
            try:
                if bool(model.flags(idx_next) & Qt.ItemIsEditable):
                    table.edit(idx_next)
            except Exception:
                pass
        except Exception:
            pass

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

    def _recommendations_active(self) -> bool:
        return bool(getattr(self, "_recommendations_enabled", True))

    def _apply_recommendations_ui_state(self):
        enabled = self._recommendations_active()

        btn = getattr(self, "btn_autocompletar", None)
        if btn is not None:
            btn.setVisible(enabled)
            btn.setEnabled(enabled)

        if not enabled:
            try:
                if getattr(self, "_rec_prev_timer", None) is not None:
                    self._rec_prev_timer.stop()
            except Exception:
                pass

            self._tab_rec_sig = None
            self._tab_recs = []
            self._tab_rec_i = 0

            try:
                if getattr(self, "model", None) is not None and hasattr(self.model, "clear_recommendations_preview"):
                    self.model.clear_recommendations_preview()
            except Exception:
                pass
            return

        self._schedule_refresh_recs_preview()

    def _get_recommendations(self, *, limit: int = 10):
        if not self._recommendations_active():
            return []

        self._ensure_recommendation_engine()
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

    def _build_item_from_code_for_inline_edit(self, code_u: str) -> dict | None:
        code_u = str(code_u or "").strip().upper()
        if not code_u:
            return None

        old_len = len(getattr(self, "items", []) or [])
        self._suppress_focus_last_row = True
        self._suppress_recs_preview_refresh = True
        try:
            ok = bool(self._agregar_por_codigo(code_u, silent=True))
        except Exception:
            ok = False
        finally:
            self._suppress_focus_last_row = False

        if (not ok) or len(getattr(self, "items", []) or []) <= old_len:
            self._suppress_recs_preview_refresh = False
            return None

        probe_row = len(self.items) - 1
        try:
            built = copy.deepcopy(self.items[probe_row])
        except Exception:
            try:
                built = dict(self.items[probe_row])
            except Exception:
                built = None

        try:
            self.model.remove_rows([probe_row])
        except Exception:
            pass
        finally:
            self._suppress_recs_preview_refresh = False

        if isinstance(built, dict):
            return built
        return None

    def _replace_row_item_by_code(self, row: int, code_u: str) -> bool:
        try:
            row = int(row)
        except Exception:
            return False

        if row < 0 or row >= len(getattr(self, "items", []) or []):
            return False

        try:
            old_item = copy.deepcopy(self.items[row])
        except Exception:
            old_item = dict(self.items[row] or {})

        old_code = str(old_item.get("codigo") or "").strip().upper()
        new_code = str(code_u or "").strip().upper()
        if not new_code:
            return False
        if new_code == old_code:
            return True

        new_item = self._build_item_from_code_for_inline_edit(new_code)
        if not isinstance(new_item, dict):
            return False

        self.items[row] = new_item

        # Preservar observacion inline del item anterior.
        old_obs = str(old_item.get("observacion") or "")
        if old_obs:
            try:
                self.model.setData(self.model.index(row, 1), old_obs, Qt.EditRole)
            except Exception:
                pass

        # Preservar cantidad anterior (la normalizacion la hace el modelo).
        try:
            old_qty_txt = str(old_item.get("cantidad") or "").strip()
        except Exception:
            old_qty_txt = ""
        if old_qty_txt:
            try:
                self.model.setData(self.model.index(row, 3), old_qty_txt, Qt.EditRole)
            except Exception:
                pass

        # Preservar tipo de precio; fallback por defecto a p_max.
        old_tier = str(old_item.get("precio_tier") or "").strip().lower()
        if not old_tier:
            try:
                old_pid = int(old_item.get("id_precioventa") or 0)
            except Exception:
                old_pid = 0
            if old_pid == 2:
                old_tier = "minimo"
            elif old_pid == 3:
                old_tier = "oferta"
            else:
                old_tier = "unitario"
        if old_tier not in ("unitario", "minimo", "oferta"):
            old_tier = "unitario"

        idx_price = self.model.index(row, 4)
        applied_price = False
        new_cat = str(self.items[row].get("categoria") or "").strip().upper()
        if new_cat == "SERVICIO":
            old_override = old_item.get("precio_override", None)
            if old_override is not None:
                try:
                    override_price = float(old_override)
                    if override_price < 0:
                        override_price = 0.0
                    applied_price = bool(
                        self.model.setData(
                            idx_price,
                            {"mode": "custom", "price": override_price},
                            Qt.EditRole,
                        )
                    )
                except Exception:
                    applied_price = False

        if not applied_price:
            try:
                applied_price = bool(
                    self.model.setData(
                        idx_price,
                        {"mode": "tier", "tier": old_tier},
                        Qt.EditRole,
                    )
                )
            except Exception:
                applied_price = False
        if not applied_price:
            try:
                self.model.setData(
                    idx_price,
                    {"mode": "tier", "tier": "unitario"},
                    Qt.EditRole,
                )
            except Exception:
                pass

        # Preservar descuento anterior.
        try:
            d_mode = str(old_item.get("descuento_mode") or "").strip().lower()
            d_pct = float(old_item.get("descuento_pct") or 0.0)
            d_amt = float(old_item.get("descuento_monto") or 0.0)
        except Exception:
            d_mode, d_pct, d_amt = "", 0.0, 0.0

        discount_payload = {"mode": "clear"}
        if d_mode == "amount" and d_amt > 0:
            discount_payload = {"mode": "amount", "amount": d_amt}
        elif d_mode == "percent" and d_pct > 0:
            discount_payload = {"mode": "percent", "percent": d_pct}
        elif d_pct > 0:
            discount_payload = {"mode": "percent", "percent": d_pct}
        elif d_amt > 0:
            discount_payload = {"mode": "amount", "amount": d_amt}

        try:
            self.model.setData(self.model.index(row, 2), discount_payload, Qt.EditRole)
        except Exception:
            pass

        top = self.model.index(row, 0)
        bottom = self.model.index(row, self.model.columnCount() - 1)
        self.model.dataChanged.emit(top, bottom, [Qt.DisplayRole, Qt.EditRole])
        self._schedule_refresh_recs_preview()
        return True

    def _force_qty_price_on_row(self, row: int, qty: float, price_base: float, price_mode: str = ""):
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

            mode_raw = str(price_mode or "").strip().lower()
            mode_map = {
                "oferta": "oferta",
                "offer": "oferta",
                "promo": "oferta",
                "promocion": "oferta",
                "promoción": "oferta",
                "min": "minimo",
                "mínimo": "minimo",
                "minimo": "minimo",
                "minimum": "minimo",
                "max": "unitario",
                "máximo": "unitario",
                "maximo": "unitario",
                "maximum": "unitario",
                "base": "unitario",
                "unitario": "unitario",
                "unit": "unitario",
                "normal": "unitario",
                "lista": "unitario",
            }
            tier = mode_map.get(mode_raw, "")
            applied_tier = False

            try:
                item = self.items[row] if 0 <= row < len(self.items) else {}
                cat = str((item or {}).get("categoria") or "").strip().upper()
            except Exception:
                cat = ""

            if tier and cat != "SERVICIO":
                try:
                    applied_tier = bool(
                        self.model.setData(self.model.index(row, 4), {"mode": "tier", "tier": tier}, Qt.EditRole)
                    )
                except Exception:
                    applied_tier = False

            if (not applied_tier) and pr > 0:
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
        if code_u in cur and p6 > 0 and p6 in cur.get(code_u, set()):            return True

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
        if not self._recommendations_active():
            return False

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

            if col == 4:
                try:
                    self._abrir_selector_precio(row)
                except Exception:
                    pass
                return

            if col == 2:
                try:
                    self._abrir_dialogo_descuento(row)
                except Exception:
                    pass
                return

            if col == 0:
                try:
                    self.table.edit(model.index(row, col))
                except Exception:
                    pass
                return

            if col == 1:
                try:
                    self._abrir_dialogo_observacion(row, self.items[row])
                except Exception:
                    pass
                return

            if col == 3:
                try:
                    self.table.edit(model.index(row, col))
                except Exception:
                    pass
                return

        except Exception:
            pass

    def _tab_autocomplete_next(self) -> bool:
        if not self._recommendations_active():
            return False

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
        if not self._recommendations_active():
            return

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

    def _quantity_editor_is_active(self) -> bool:
        try:
            table = getattr(self, "table", None)
            if table is None:
                return False
            if table.state() != QAbstractItemView.EditingState:
                return False
            idx = table.currentIndex()
            if (not idx.isValid()) or idx.column() != 3:
                return False
            fw = QApplication.focusWidget()
            if fw is None:
                return False
            return bool(table.isAncestorOf(fw))
        except Exception:
            return False

    def _schedule_refresh_recs_preview(self):
        if bool(getattr(self, "_suppress_recs_preview_refresh", False)):
            return

        if not self._recommendations_active():
            try:
                if getattr(self, "model", None) is not None and hasattr(self.model, "clear_recommendations_preview"):
                    self.model.clear_recommendations_preview()
            except Exception:
                pass
            return

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
            if self._quantity_editor_is_active():
                try:
                    if getattr(self, "_rec_prev_timer", None) is not None:
                        self._rec_prev_timer.start(180)
                except Exception:
                    pass
                return

            self._ensure_recommendation_engine()
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
            if obj is getattr(self, "entry_cedula", None) and ev.type() == QEvent.KeyPress:
                if ev.key() in (Qt.Key_Return, Qt.Key_Enter):
                    try:
                        ai_doc = getattr(self, "_ai_doc", None)
                        if ai_doc is not None:
                            if hasattr(ai_doc, "is_popup_visible") and bool(ai_doc.is_popup_visible()):
                                if hasattr(ai_doc, "pick_first") and bool(ai_doc.pick_first()):
                                    return True
                            if hasattr(ai_doc, "hide_popup"):
                                ai_doc.hide_popup()
                    except Exception:
                        pass
                    self._go_name()
                    return True

            if obj is getattr(self, "entry_producto", None) and ev.type() == QEvent.KeyPress:
                key = ev.key()

                if key == Qt.Key_F9:
                    if not self._recommendations_active():
                        return False
                    if not self._tab_autocomplete_next():
                        try:
                            Toast.notify(self, "No hay recomendaciones con suficiente confianza (≥20%).", duration_ms=2500, fade_ms=800)
                        except Exception:
                            pass
                    return True

                if key in (Qt.Key_Return, Qt.Key_Enter):
                    txt = (self.entry_producto.text() or "").strip()
                    if not txt:
                        if not self._recommendations_active():
                            return False
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
                        if self.table.state() == QAbstractItemView.EditingState:
                            idx_edit = self.table.currentIndex()
                            if idx_edit and idx_edit.isValid() and idx_edit.column() != 3:
                                r = int(idx_edit.row())
                                c = int(idx_edit.column())
                                QTimer.singleShot(0, lambda rr=r, cc=c: self._move_inline_to_right(rr, cc))
                            return False

                        idx = self.table.currentIndex()
                        if idx and idx.isValid():
                            row = idx.row()
                            if hasattr(self.model, "is_preview_row") and bool(self.model.is_preview_row(row)):
                                if self._try_add_preview_from_row(row):
                                    return True
                                return True

                            if idx.column() in (0, 1, 2, 3, 4):
                                self.table.edit(idx)
                                return True
                            if idx.column() == 5:
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
        main.setContentsMargins(10, 8, 10, 10)
        main.setSpacing(7)

        grp_cli = QGroupBox("Datos del Cliente")
        form_cli = QGridLayout()
        form_cli.setContentsMargins(10, 8, 10, 8)
        form_cli.setHorizontalSpacing(8)
        form_cli.setVerticalSpacing(6)
        self.entry_cliente = QLineEdit()
        self.entry_cedula = QLineEdit()
        self.entry_telefono = QLineEdit()
        self.entry_direccion = QLineEdit()
        self.entry_email = QLineEdit()
        self.combo_tipo_documento = QComboBox()
        self.entry_cliente.setClearButtonEnabled(True)
        self.entry_cedula.setClearButtonEnabled(True)
        self.entry_telefono.setClearButtonEnabled(True)
        self.entry_direccion.setClearButtonEnabled(True)
        self.entry_email.setClearButtonEnabled(True)
        self.entry_cliente.setPlaceholderText("Nombre completo")
        self.entry_direccion.setPlaceholderText("Direccion")
        self.entry_email.setPlaceholderText("Email")
        self.combo_tipo_documento.setMinimumHeight(28)
        self.combo_tipo_documento.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.combo_tipo_documento.setMinimumWidth(112)
        self.combo_tipo_documento.setMaximumWidth(190)
        for r in (self._doc_type_rules() or []):
            code = str(r.get("nombre") or "").strip().upper()
            if not code:
                continue
            self.combo_tipo_documento.addItem(code, code)
        if self.combo_tipo_documento.count() > 0:
            # Siempre iniciar con el primer tipo disponible del pais.
            self.combo_tipo_documento.setCurrentIndex(0)
        self.combo_tipo_documento.currentIndexChanged.connect(self._on_doc_type_changed)
        lbl_phone = QLabel("Telefono:")
        form_cli.addWidget(self.combo_tipo_documento, 0, 0)
        form_cli.addWidget(self.entry_cedula, 0, 1, 1, 2)
        form_cli.addWidget(lbl_phone, 0, 3)
        form_cli.addWidget(self.entry_telefono, 0, 4)
        form_cli.addWidget(self.entry_cliente, 1, 0, 1, 5)
        form_cli.addWidget(self.entry_direccion, 2, 0, 1, 3)
        form_cli.addWidget(self.entry_email, 2, 3, 1, 2)
        form_cli.setColumnStretch(2, 3)
        form_cli.setColumnStretch(3, 1)
        form_cli.setColumnStretch(4, 2)
        self._apply_client_validators()
        grp_cli.setLayout(form_cli)
        grp_cli.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

        self._wire_enter_flow()

        try:
            self.entry_cliente.textChanged.connect(lambda _=None: self._schedule_refresh_recs_preview())
            self.entry_cedula.textChanged.connect(lambda _=None: self._schedule_refresh_recs_preview())
            self.entry_telefono.textChanged.connect(lambda _=None: self._schedule_refresh_recs_preview())
            self.entry_direccion.textChanged.connect(lambda _=None: self._schedule_refresh_recs_preview())
            self.entry_email.textChanged.connect(lambda _=None: self._schedule_refresh_recs_preview())
        except Exception:
            pass

        rate_row = QHBoxLayout()
        rate_row.setContentsMargins(0, 0, 0, 0)
        rate_row.setSpacing(5)

        self.btn_moneda = self._make_tool_icon(
            "💱", "Cambiar moneda y configurar tasa", self.abrir_dialogo_moneda_y_tasa
        )
        self.btn_moneda.setFixedSize(28, 28)

        self.lbl_moneda = QLabel()
        self.lbl_moneda.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.lbl_moneda.setMinimumWidth(105)
        self.lbl_moneda.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        btn_listado = QPushButton("Listado de productos")
        self._apply_btn_responsive(btn_listado, 118, 28)
        btn_listado.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        btn_listado.clicked.connect(self.abrir_listado_productos)

        actions_row = QHBoxLayout()
        actions_row.setContentsMargins(0, 0, 0, 0)
        actions_row.setSpacing(6)

        self._py_cash_mode = False
        if APP_COUNTRY == "PARAGUAY":
            self.btn_pay_card = QPushButton("Tarjeta")
            self.btn_pay_cash = QPushButton("Efectivo")
            for b in (self.btn_pay_card, self.btn_pay_cash):
                self._apply_btn_responsive(b, 86, 28)
                b.setCheckable(True)
                b.setProperty("role", "payment_toggle")

            self.btn_pay_card.setChecked(True)

            self.pay_group = QButtonGroup(self)
            self.pay_group.setExclusive(True)
            self.pay_group.addButton(self.btn_pay_card, 0)
            self.pay_group.addButton(self.btn_pay_cash, 1)
            self.pay_group.buttonClicked.connect(self._on_py_payment_clicked)

            rate_row.addStretch(1)
            rate_row.addWidget(self.btn_pay_card)
            rate_row.addWidget(self.btn_pay_cash)
            rate_row.addStretch(1)

            actions_row.addWidget(self.btn_moneda, 0)
            actions_row.addWidget(self.lbl_moneda, 1)
            actions_row.addWidget(btn_listado, 0)

        elif APP_COUNTRY == "PERU":
            rate_row.addWidget(self.btn_moneda, 0)
            rate_row.addWidget(self.lbl_moneda, 1)

            pay_row = QHBoxLayout()
            pay_row.setContentsMargins(0, 0, 0, 0)
            pay_row.setSpacing(4)

            self.entry_metodo_pago = QLineEdit()
            self.entry_metodo_pago.setPlaceholderText("Método de pago (opcional)")
            self.entry_metodo_pago.setClearButtonEnabled(True)
            self.entry_metodo_pago.setFixedWidth(138)
            self.entry_metodo_pago.setMinimumHeight(28)

            pay_row.addWidget(self.entry_metodo_pago)
            pay_row.addStretch(1)
            actions_row.addLayout(pay_row, 1)
            actions_row.addWidget(btn_listado, 0)

        else:
            rate_row.addWidget(self.btn_moneda, 0)
            rate_row.addWidget(self.lbl_moneda, 1)
            actions_row.addStretch(1)
            actions_row.addWidget(btn_listado, 0)

        grp_quick = QGroupBox("Acciones rápidas")
        quick = QVBoxLayout(grp_quick)
        quick.setContentsMargins(8, 5, 8, 5)
        quick.setSpacing(4)
        quick.addLayout(rate_row)
        quick.addLayout(actions_row)
        grp_quick.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

        top_panel = QHBoxLayout()
        top_panel.setContentsMargins(0, 0, 0, 0)
        top_panel.setSpacing(6)
        top_panel.addWidget(grp_cli, 13)
        top_panel.addWidget(grp_quick, 7)

        grp_cli.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        grp_quick.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        top_h = max(grp_cli.sizeHint().height(), grp_quick.sizeHint().height())
        grp_cli.setFixedHeight(top_h)
        grp_quick.setFixedHeight(top_h)
        main.addLayout(top_panel)

        self._update_currency_label()

        grp_bus = QGroupBox("Búsqueda de Productos")
        vbus = QVBoxLayout()
        vbus.setContentsMargins(6, 4, 6, 4)
        vbus.setSpacing(4)
        hbus = QHBoxLayout()
        hbus.setContentsMargins(0, 0, 0, 0)
        hbus.setSpacing(6)

        self.entry_producto = QLineEdit()
        self.entry_producto.setPlaceholderText("Código, nombre, categoría o tipo")
        self.entry_producto.returnPressed.connect(self._handle_product_return_pressed)

        self.entry_producto.installEventFilter(self)
        self.entry_cedula.installEventFilter(self)

        lbl_bus = QLabel("Código o Nombre:")

        btn_agregar_srv = QPushButton("Agregar Servicio")
        self._apply_btn_responsive(btn_agregar_srv, 104, 30)
        btn_agregar_srv.setToolTip("Agregar un ítem de tipo SERVICIO / personalizado")
        btn_agregar_srv.clicked.connect(self.agregar_producto_personalizado)

        self.btn_autocompletar = QPushButton("Autocompletar")
        self.btn_autocompletar.setProperty("variant", "primary")
        self._apply_btn_responsive(self.btn_autocompletar, 116, 30)
        self.btn_autocompletar.setToolTip("Agrega productos recomendados por historial (F9 o Enter vacío agrega 1 a 1).")
        self.btn_autocompletar.clicked.connect(self._on_autocomplete_clicked)
        self._apply_recommendations_ui_state()

        hbus.addWidget(lbl_bus)
        hbus.addWidget(self.entry_producto)
        hbus.addWidget(btn_agregar_srv)
        hbus.addWidget(self.btn_autocompletar)

        vbus.addLayout(hbus)
        grp_bus.setLayout(vbus)
        grp_bus.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        main.addWidget(grp_bus)

        grp_tab = QGroupBox("Productos Seleccionados")
        vtab = QVBoxLayout()
        self.table = QTableView()
        self.model = ItemsModel(self.items)
        self.model.set_code_edit_handler(self._replace_row_item_by_code)
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
        self.inline_text_delegate = InlineTextDelegate(self.table)
        self.table.setItemDelegateForColumn(0, self.inline_text_delegate)
        self.table.setItemDelegateForColumn(1, self.inline_text_delegate)
        self.table.setItemDelegateForColumn(2, self.inline_text_delegate)
        self.table.setItemDelegateForColumn(4, self.inline_text_delegate)
        try:
            self.qty_delegate.closeEditor.connect(self._on_qty_editor_closed)
        except Exception:
            pass

        # Doble clic se maneja manualmente en _double_click_tabla para evitar
        # que, tras abrir un modal, Qt abra además el editor inline.
        self.table.setEditTriggers(QAbstractItemView.EditKeyPressed)
        self.table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(True)
        self.table.verticalHeader().setDefaultSectionSize(34)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Interactive)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.table.setColumnWidth(3, 96)

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
        self._excel_table = ExcelTableController(
            self.table,
            allow_copy=True,
            allow_paste=True,
            allow_cut=True,
            clear_on_delete=False,
            move_on_enter=False,
            move_on_tab=True,
            skip_enter_preview_rows=True,
        )

        vtab.addWidget(self.table)
        grp_tab.setLayout(vtab)
        main.addWidget(grp_tab, 1)

        hact = QHBoxLayout()
        hact.setContentsMargins(0, 2, 0, 0)
        hact.setSpacing(8)

        btn_prev = QPushButton("Previsualizar")
        self._apply_btn_responsive(btn_prev, 120, 36)
        btn_prev.clicked.connect(self.previsualizar_datos)

        btn_gen = QPushButton("Generar Cotización")
        btn_gen.setProperty("variant", "primary")
        self._apply_btn_responsive(btn_gen, 140, 36)
        btn_gen.clicked.connect(self.generar_cotizacion)

        btn_lim = QPushButton("Limpiar")
        btn_lim.setProperty("variant", "danger")
        self._apply_btn_responsive(btn_lim, 110, 36)
        btn_lim.clicked.connect(self.limpiar_formulario)

        hact.addStretch(1)
        for w in (btn_prev, btn_gen, btn_lim):
            hact.addWidget(w)
        main.addLayout(hact)

        self._schedule_refresh_recs_preview()
        QTimer.singleShot(0, self._go_doc)
