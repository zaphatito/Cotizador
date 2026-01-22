# src/widgets_parts/quote_history_dialog.py
from __future__ import annotations

import os
import datetime

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QAction, QCloseEvent
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton,
    QTableView, QLabel, QMessageBox, QHeaderView, QMenu,
    QApplication, QDialog, QInputDialog,
)

from sqlModels.db import connect, ensure_schema, tx
from sqlModels.quotes_repo import (
    list_quotes, get_quote_header, get_quote_items, soft_delete_quote, update_quote_payment
)
from sqlModels.rates_repo import load_rates

from ..logging_setup import get_logger
from ..utils import nz
from ..paths import DATA_DIR, COTIZACIONES_DIR, resolve_pdf_path_portable

from ..db_path import resolve_db_path
from ..catalog_sync import sync_catalog_from_excel_to_db, load_catalog_from_db
from ..config import APP_CURRENCY, APP_COUNTRY, get_currency_context, set_currency_context

from ..app_window import SistemaCotizaciones
from ..app_window_parts.ticket_actions import generar_ticket_para_cotizacion
from ..pdfgen import generar_pdf

from .menu import MainMenuWindow, RatesDialog
from .rates_history_dialog import RatesHistoryDialog

log = get_logger(__name__)


def center_on_screen(w):
    """Centra un widget en la pantalla (availableGeometry) donde esté."""
    try:
        screen = w.screen() or QApplication.primaryScreen()
        if not screen:
            return
        geo = screen.availableGeometry()
        fg = w.frameGeometry()
        fg.moveCenter(geo.center())
        w.move(fg.topLeft())
    except Exception:
        pass


def _doc_header_for_country(country: str) -> str:
    c = (country or "").strip().upper()
    if c == "PERU":
        return "DNI / RUC"
    if c == "VENEZUELA":
        return "Cédula/RIF"
    # Paraguay (y default)
    return "Cédula/RUC"


def _parse_dt(value) -> datetime.datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value
    if isinstance(value, datetime.date):
        return datetime.datetime.combine(value, datetime.time.min)

    s = str(value).strip()
    if not s:
        return None

    s2 = s[:-1] + "+00:00" if s.endswith("Z") else s

    try:
        return datetime.datetime.fromisoformat(s2)
    except Exception:
        pass

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(s, fmt)
        except Exception:
            continue

    return None


def format_dt_legible(value) -> str:
    dt = _parse_dt(value)
    if dt is None:
        return "" if value is None else str(value).strip()
    out = dt.strftime("%d/%m/%Y %I:%M %p")
    return out.replace("AM", "am").replace("PM", "pm")


class QuotesTableModel(QAbstractTableModel):
    def __init__(self, *, show_payment: bool):
        super().__init__()
        self.rows: list[dict] = []
        self.show_payment = bool(show_payment)
        doc_hdr = _doc_header_for_country(APP_COUNTRY)
        if self.show_payment:
            self.HEADERS = [
                "Fecha/Hora", "N°", "Cliente", doc_hdr, "Teléfono",
                "Pago", "Total", "Moneda", "Items", "PDF"
            ]
        else:
            self.HEADERS = [
                "Fecha/Hora", "N°", "Cliente", doc_hdr, "Teléfono",
                "Total", "Moneda", "Items", "PDF"
            ]

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return self.HEADERS[section]
        return str(section + 1)

    def _idx_no(self) -> int:
        return 1

    def _idx_pago(self) -> int | None:
        return 5 if self.show_payment else None

    def _idx_total(self) -> int:
        return 6 if self.show_payment else 5

    def _idx_currency(self) -> int:
        return 7 if self.show_payment else 6

    def _idx_items(self) -> int:
        return 8 if self.show_payment else 7

    def _idx_pdf(self) -> int:
        return 9 if self.show_payment else 8

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        r = self.rows[index.row()]
        c = index.column()

        if role == Qt.DisplayRole:
            if c == 0:
                return format_dt_legible(r.get("created_at", ""))
            if c == 1:
                return r.get("quote_no", "")
            if c == 2:
                return r.get("cliente", "")
            if c == 3:
                return r.get("cedula", "")
            if c == 4:
                return r.get("telefono", "")

            if self.show_payment and c == self._idx_pago():
                return (r.get("metodo_pago") or "").strip()

            if c == self._idx_total():
                try:
                    return f"{float(nz(r.get('total_shown'), 0.0)):.2f}"
                except Exception:
                    return str(r.get("total_shown", "0.00"))

            if c == self._idx_currency():
                return r.get("currency_shown", "")

            if c == self._idx_items():
                return str(r.get("items_count", 0))

            if c == self._idx_pdf():
                p = r.get("pdf_path", "") or ""
                return os.path.basename(p)

        if role == Qt.TextAlignmentRole:
            centered_cols = {self._idx_no(), self._idx_total(), self._idx_currency(), self._idx_items()}
            if self.show_payment and self._idx_pago() is not None:
                centered_cols.add(self._idx_pago())
            if c in centered_cols:
                return int(Qt.AlignVCenter | Qt.AlignCenter)
            return int(Qt.AlignVCenter | Qt.AlignLeft)

        if role == Qt.ToolTipRole and c == self._idx_pdf():
            return r.get("pdf_path", "")

        return None

    def set_rows(self, rows: list[dict]):
        self.beginResetModel()
        self.rows = rows or []
        self.endResetModel()

    def get_id_at(self, row: int) -> int | None:
        if 0 <= row < len(self.rows):
            return int(self.rows[row]["id"])
        return None

    def _sort_key(self, r: dict, c: int):
        def key_text(v):
            s = "" if v is None else str(v)
            return s.casefold()

        def key_float(v):
            try:
                return float(nz(v, 0.0))
            except Exception:
                return 0.0

        def key_int(v):
            try:
                return int(nz(v, 0))
            except Exception:
                try:
                    return int(str(v).strip())
                except Exception:
                    return 0

        if c == 0:
            dt = _parse_dt(r.get("created_at"))
            return (dt is None, dt or datetime.datetime.min)

        if c == self._idx_no():
            qn = r.get("quote_no")
            try:
                return (False, int(str(qn).strip()))
            except Exception:
                return (qn is None, key_text(qn))

        if c == 2:
            v = r.get("cliente")
            return (v is None, key_text(v))
        if c == 3:
            v = r.get("cedula")
            return (v is None, key_text(v))
        if c == 4:
            v = r.get("telefono")
            return (v is None, key_text(v))

        if self.show_payment and c == self._idx_pago():
            v = r.get("metodo_pago")
            return (v is None, key_text(v))

        if c == self._idx_total():
            v = r.get("total_shown")
            return (v is None, key_float(v))

        if c == self._idx_currency():
            v = r.get("currency_shown")
            return (v is None, key_text(v))

        if c == self._idx_items():
            v = r.get("items_count")
            return (v is None, key_int(v))

        if c == self._idx_pdf():
            p = r.get("pdf_path") or ""
            return (not bool(p), key_text(os.path.basename(p)))

        return (False, "")

    def sort(self, column: int, order: Qt.SortOrder = Qt.AscendingOrder):
        if not self.rows:
            return
        self.layoutAboutToBeChanged.emit()
        try:
            reverse = (order == Qt.DescendingOrder)
            self.rows.sort(key=lambda r: self._sort_key(r, column), reverse=reverse)
        finally:
            self.layoutChanged.emit()


class QuoteHistoryWindow(QMainWindow):
    def __init__(self, *, catalog_manager, quote_events, app_icon):
        super().__init__()
        self.setWindowTitle("Sistema de cotizaciones")
        self.resize(1300, 720)
        if not app_icon.isNull():
            self.setWindowIcon(app_icon)

        self._db_path = resolve_db_path()

        self._centered_once = False
        self.catalog_manager = catalog_manager
        self.quote_events = quote_events

        show_payment = (APP_COUNTRY in ("PARAGUAY", "PERU"))
        self.model = QuotesTableModel(show_payment=show_payment)

        self.page_size = 200
        self.offset = 0
        self.total = 0

        self._open_windows: list[SistemaCotizaciones] = []
        self._closing_with_children = False

        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(220)
        self._filter_timer.timeout.connect(self._reload_first_page)

        self._rt_timer = QTimer(self)
        self._rt_timer.setSingleShot(True)
        self._rt_timer.setInterval(120)
        self._rt_timer.timeout.connect(self._reload_first_page)

        if self.quote_events is not None:
            try:
                self.quote_events.quote_saved.connect(self._on_quote_saved)
            except Exception:
                pass

        if self.catalog_manager is not None:
            try:
                self.catalog_manager.catalog_updated.connect(self._on_catalog_updated)
            except Exception:
                pass

        central = QWidget()
        self.setCentralWidget(central)
        main = QVBoxLayout(central)

        top = QHBoxLayout()

        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText("Filtrar: cliente / cédula / teléfono / N°")
        self.txt_search.textChanged.connect(self._on_filters_changed)

        self.txt_prod = QLineEdit()
        self.txt_prod.setPlaceholderText("Contiene producto: código o nombre")
        self.txt_prod.textChanged.connect(self._on_filters_changed)

        self.btn_new = QPushButton("➕ Nueva cotización")
        self.btn_new.clicked.connect(self._open_new_quote)

        self.btn_menu = QPushButton("☰ Menú")
        self.btn_menu.clicked.connect(self._open_main_menu)

        top.addWidget(self.txt_search, 2)
        top.addWidget(self.txt_prod, 2)
        top.addWidget(self.btn_new, 0)
        top.addWidget(self.btn_menu, 0)
        main.addLayout(top)

        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setSelectionMode(QTableView.SingleSelection)
        self.table.setEditTriggers(QTableView.NoEditTriggers)
        self.table.doubleClicked.connect(self._on_table_double_clicked)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_table_context_menu)

        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSortIndicatorShown(True)

        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Stretch)

        try:
            idx_no = 1
            idx_pago = self.model._idx_pago()
            idx_total = self.model._idx_total()
            idx_items = self.model._idx_items()
            idx_pdf = self.model._idx_pdf()
            idx_curr = self.model._idx_currency()

            hh.setSectionResizeMode(idx_no, QHeaderView.ResizeToContents)
            if idx_pago is not None:
                hh.setSectionResizeMode(idx_pago, QHeaderView.ResizeToContents)
            hh.setSectionResizeMode(idx_total, QHeaderView.ResizeToContents)
            hh.setSectionResizeMode(idx_curr, QHeaderView.ResizeToContents)
            hh.setSectionResizeMode(idx_items, QHeaderView.ResizeToContents)
            hh.setSectionResizeMode(idx_pdf, QHeaderView.ResizeToContents)
        except Exception:
            pass

        main.addWidget(self.table)

        nav = QHBoxLayout()
        self.lbl_page = QLabel("—")
        btn_prev = QPushButton("◀")
        btn_next = QPushButton("▶")
        btn_prev.clicked.connect(self._prev_page)
        btn_next.clicked.connect(self._next_page)
        nav.addWidget(self.lbl_page)
        nav.addStretch(1)
        nav.addWidget(btn_prev)
        nav.addWidget(btn_next)
        main.addLayout(nav)

        actions = QHBoxLayout()
        self.btn_pdf = QPushButton("Abrir PDF")
        self.btn_dup = QPushButton("Abrir cotización")
        self.btn_hide = QPushButton("Eliminar")

        self.btn_pdf.clicked.connect(self._open_pdf)
        self.btn_dup.clicked.connect(self._duplicate)
        self.btn_hide.clicked.connect(self._soft_delete)

        for b in (self.btn_pdf, self.btn_dup, self.btn_hide):
            actions.addWidget(b)
        actions.addStretch(1)
        main.addLayout(actions)

        self._apply_catalog_gate()
        self._reload_first_page()

    # -----------------------------
    #  ✅ CONTROL DE VENTANAS HIJAS
    # -----------------------------
    def _is_qt_alive(self, obj) -> bool:
        if obj is None:
            return False
        try:
            _ = obj.windowTitle()
            return True
        except RuntimeError:
            return False
        except Exception:
            return True

    def _prune_open_windows(self):
        try:
            self._open_windows = [w for w in (self._open_windows or []) if self._is_qt_alive(w)]
        except Exception:
            pass

    def _register_open_quote_window(self, win: SistemaCotizaciones):
        try:
            if win is None:
                return
            try:
                win.setAttribute(Qt.WA_DeleteOnClose, True)
            except Exception:
                pass

            try:
                win.destroyed.connect(lambda *_: self._prune_open_windows())
            except Exception:
                pass

            self._open_windows.append(win)
            self._prune_open_windows()
        except Exception:
            pass

    def _alive_quote_windows(self) -> list[SistemaCotizaciones]:
        self._prune_open_windows()
        return list(self._open_windows or [])

    def _close_all_quotes(self) -> bool:
        self._prune_open_windows()
        wins = list(self._open_windows or [])
        for w in wins:
            try:
                if self._is_qt_alive(w):
                    w.close()
            except Exception:
                pass

        try:
            QApplication.processEvents()
        except Exception:
            pass

        self._prune_open_windows()
        return len(self._open_windows or []) == 0

    # -----------------------------

    def _on_quote_saved(self):
        self._rt_timer.start()

    def _on_catalog_updated(self, *_):
        self._apply_catalog_gate()

    def _has_products(self) -> bool:
        try:
            df = getattr(self.catalog_manager, "df_productos", None)
            return (df is not None) and (not df.empty)
        except Exception:
            return False

    def _apply_catalog_gate(self):
        ok = self._has_products()
        self.btn_new.setEnabled(ok)
        self.btn_dup.setEnabled(ok)
        tip = "Primero importa/actualiza productos para poder abrir/crear cotizaciones."
        self.btn_new.setToolTip("" if ok else tip)
        self.btn_dup.setToolTip("" if ok else tip)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._centered_once:
            self._centered_once = True
            center_on_screen(self)

    def _on_filters_changed(self, *_):
        self._filter_timer.start()

    def _selected_quote_id(self) -> int | None:
        idx = self.table.selectionModel().currentIndex()
        if not idx.isValid():
            return None
        return self.model.get_id_at(idx.row())

    def _select_row_by_quote_id(self, quote_id: int):
        try:
            for i, r in enumerate(self.model.rows or []):
                if int(r.get("id") or 0) == int(quote_id):
                    self.table.selectRow(i)
                    self.table.setCurrentIndex(self.model.index(i, 0))
                    self.table.scrollTo(self.model.index(i, 0))
                    break
        except Exception:
            pass

    def _reload_first_page(self):
        self.offset = 0
        self._reload_current_page()

    def _apply_current_sort(self):
        try:
            if not self.table.isSortingEnabled():
                return
            hh = self.table.horizontalHeader()
            col = hh.sortIndicatorSection()
            order = hh.sortIndicatorOrder()
            self.model.sort(col, order)
        except Exception:
            pass

    def _reload_current_page(self):
        try:
            con = connect(self._db_path)
            ensure_schema(con)

            rows, total = list_quotes(
                con,
                search_text=(self.txt_search.text() or "").strip(),
                contains_product=(self.txt_prod.text() or "").strip(),
                include_deleted=False,
                limit=self.page_size,
                offset=self.offset,
            )
            con.close()
        except Exception as e:
            log.exception("Error listando cotizaciones")
            QMessageBox.critical(self, "Error", f"No se pudo cargar el histórico:\n{e}")
            return

        self.total = total
        self.model.set_rows(rows)
        self._apply_current_sort()

        a = self.offset + 1 if total > 0 else 0
        b = min(self.offset + self.page_size, total)
        self.lbl_page.setText(f"Mostrando {a}-{b} de {total}")

    def _prev_page(self):
        if self.offset <= 0:
            return
        self.offset = max(0, self.offset - self.page_size)
        self._reload_current_page()

    def _next_page(self):
        if self.offset + self.page_size >= self.total:
            return
        self.offset += self.page_size
        self._reload_current_page()

    def _on_table_double_clicked(self, index: QModelIndex):
        if not self._has_products():
            QMessageBox.warning(
                self,
                "Sin productos",
                "No puedes abrir cotizaciones sin productos.\n\n"
                "Usa ☰ Menú → Actualizar productos.",
            )
            return
        try:
            if index and index.isValid():
                self.table.setCurrentIndex(index)
                self.table.selectRow(index.row())
        except Exception:
            pass
        self._duplicate()

    def _on_table_context_menu(self, pos):
        idx = self.table.indexAt(pos)
        if idx.isValid():
            self.table.setCurrentIndex(idx)
            self.table.selectRow(idx.row())

        has_sel = self._selected_quote_id() is not None
        has_catalog = self._has_products()

        menu = QMenu(self)

        act_dup = QAction("Abrir Cotización", self)
        act_pdf = QAction("Abrir PDF", self)
        act_ticket = QAction("Reimprimir ticket", self)
        act_regen = QAction("Regenerar PDF", self)
        act_hide = QAction("Eliminar", self)

        # ✅ SOLO PERÚ: editar método de pago desde histórico
        act_edit_pay = None
        if APP_COUNTRY == "PERU":
            act_edit_pay = QAction("Editar pago…", self)
            act_edit_pay.setEnabled(has_sel)
            act_edit_pay.triggered.connect(self._edit_payment_peru)

        act_dup.setEnabled(has_sel and has_catalog)
        act_pdf.setEnabled(has_sel)
        act_ticket.setEnabled(has_sel)
        act_regen.setEnabled(has_sel)
        act_hide.setEnabled(has_sel)

        act_dup.triggered.connect(self._duplicate)
        act_pdf.triggered.connect(self._open_pdf)
        act_ticket.triggered.connect(self._reprint_ticket)
        act_regen.triggered.connect(self._regen_pdf_overwrite)
        act_hide.triggered.connect(self._soft_delete)

        menu.addAction(act_dup)
        menu.addSeparator()
        menu.addAction(act_pdf)

        if act_edit_pay is not None:
            menu.addAction(act_edit_pay)

        menu.addAction(act_ticket)
        menu.addAction(act_regen)
        menu.addSeparator()
        menu.addAction(act_hide)
        menu.addSeparator()
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _edit_payment_peru(self):
        if APP_COUNTRY != "PERU":
            return

        qid = self._selected_quote_id()
        if not qid:
            QMessageBox.information(self, "Atención", "Selecciona una cotización.")
            return

        try:
            con = connect(self._db_path)
            ensure_schema(con)
            header = get_quote_header(con, qid)
            con.close()
        except Exception as e:
            log.exception("Error leyendo cotización")
            QMessageBox.critical(self, "Error", f"No se pudo leer la cotización:\n{e}")
            return

        current_mp = (header.get("metodo_pago") or "").strip()

        text, ok = QInputDialog.getText(
            self,
            "Editar pago",
            "Método de pago (opcional):",
            QLineEdit.Normal,
            current_mp,
        )
        if not ok:
            return

        new_mp = (text or "").strip()

        try:
            con = connect(self._db_path)
            ensure_schema(con)
            with tx(con):
                update_quote_payment(con, qid, new_mp)
            con.close()

            self._reload_current_page()
            self._select_row_by_quote_id(qid)

        except Exception as e:
            log.exception("Error actualizando pago")
            QMessageBox.critical(self, "Error", f"No se pudo actualizar el pago:\n{e}")

    def _open_new_quote(self):
        if not self._has_products():
            QMessageBox.warning(
                self,
                "Sin productos",
                "No puedes crear cotizaciones sin productos.\n\n"
                "Usa ☰ Menú → Actualizar productos.",
            )
            return

        win = SistemaCotizaciones(
            df_productos=self.catalog_manager.df_productos,
            df_presentaciones=self.catalog_manager.df_presentaciones,
            app_icon=self.windowIcon(),
            catalog_manager=self.catalog_manager,
            quote_events=self.quote_events,
        )

        # ✅ para que al generar se cierre y vuelva foco al histórico
        win._history_window = self

        win.show()
        center_on_screen(win)
        self._register_open_quote_window(win)

    def _open_main_menu(self):
        MainMenuWindow.show_singleton(
            catalog_manager=self.catalog_manager,
            quote_events=self.quote_events,
            app_icon=self.windowIcon(),
            parent=self,
        )

    def _open_pdf(self):
        qid = self._selected_quote_id()
        if not qid:
            QMessageBox.information(self, "Atención", "Selecciona una cotización.")
            return
        try:
            con = connect(self._db_path)
            ensure_schema(con)
            q = get_quote_header(con, qid)
            con.close()

            pdf = resolve_pdf_path_portable(q.get("pdf_path"))
            if not pdf or not os.path.exists(pdf):
                QMessageBox.warning(self, "PDF no encontrado", f"No existe:\n{pdf}")
                return
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(pdf)))
        except Exception as e:
            log.exception("Error abriendo PDF")
            QMessageBox.critical(self, "Error", f"No se pudo abrir el PDF:\n{e}")

    def _duplicate(self):
        if not self._has_products():
            QMessageBox.warning(self, "Sin productos", "Usa ☰ Menú → Actualizar productos.")
            return

        qid = self._selected_quote_id()
        if not qid:
            QMessageBox.information(self, "Atención", "Selecciona una cotización.")
            return
        try:
            con = connect(self._db_path)
            ensure_schema(con)
            header = get_quote_header(con, qid)
            items_base, _items_shown = get_quote_items(con, qid)
            con.close()

            payload = {
                "cliente": header.get("cliente", ""),
                "cedula": header.get("cedula", ""),
                "telefono": header.get("telefono", ""),
                "items_base": items_base,
            }

            win = SistemaCotizaciones(
                df_productos=self.catalog_manager.df_productos,
                df_presentaciones=self.catalog_manager.df_presentaciones,
                app_icon=self.windowIcon(),
                catalog_manager=self.catalog_manager,
                quote_events=self.quote_events,
            )

            # ✅ para que al generar se cierre y vuelva foco al histórico
            win._history_window = self

            if APP_COUNTRY == "PARAGUAY":
                mp = (header.get("metodo_pago") or "").strip().lower()
                is_cash = (mp == "efectivo")

                try:
                    if getattr(win, "btn_pay_cash", None) is not None:
                        win.btn_pay_cash.blockSignals(True)
                    if getattr(win, "btn_pay_card", None) is not None:
                        win.btn_pay_card.blockSignals(True)

                    if is_cash and getattr(win, "btn_pay_cash", None) is not None:
                        win.btn_pay_cash.setChecked(True)
                    elif getattr(win, "btn_pay_card", None) is not None:
                        win.btn_pay_card.setChecked(True)
                finally:
                    try:
                        if getattr(win, "btn_pay_cash", None) is not None:
                            win.btn_pay_cash.blockSignals(False)
                        if getattr(win, "btn_pay_card", None) is not None:
                            win.btn_pay_card.blockSignals(False)
                    except Exception:
                        pass

                try:
                    if hasattr(win, "_set_py_cash_mode"):
                        win._set_py_cash_mode(is_cash, assume_items_already=True)
                except Exception:
                    pass

            elif APP_COUNTRY == "PERU":
                mp = (header.get("metodo_pago") or "")
                try:
                    if getattr(win, "entry_metodo_pago", None) is not None:
                        win.entry_metodo_pago.setText(mp)
                except Exception:
                    pass

            win.show()
            center_on_screen(win)
            self._register_open_quote_window(win)

            win.load_from_history_payload(payload)

            if APP_COUNTRY == "PARAGUAY":
                mp = (header.get("metodo_pago") or "").strip().lower()
                is_cash = (mp == "efectivo")
                try:
                    if hasattr(win, "_set_py_cash_mode"):
                        win._set_py_cash_mode(is_cash, assume_items_already=True)
                except Exception:
                    pass

        except Exception as e:
            log.exception("Error duplicando cotización")
            QMessageBox.critical(self, "Error", f"No se pudo duplicar:\n{e}")

    def _soft_delete(self):
        qid = self._selected_quote_id()
        if not qid:
            QMessageBox.information(self, "Atención", "Selecciona una cotización.")
            return

        if QMessageBox.question(self, "Eliminar", "Eliminar esta cotización del historial?") != QMessageBox.Yes:
            return

        try:
            con = connect(self._db_path)
            ensure_schema(con)
            with tx(con):
                soft_delete_quote(con, qid, datetime.datetime.now().isoformat(timespec="seconds"))
            con.close()
            self._reload_first_page()
        except Exception as e:
            log.exception("Error eliminando cotización")
            QMessageBox.critical(self, "Error", f"No se pudo eliminar:\n{e}")

    def _reprint_ticket(self):
        qid = self._selected_quote_id()
        if not qid:
            QMessageBox.information(self, "Atención", "Selecciona una cotización.")
            return

        try:
            con = connect(self._db_path)
            ensure_schema(con)
            header = get_quote_header(con, qid)
            _items_base, items_shown = get_quote_items(con, qid)
            con.close()

            pdf_path = header.get("pdf_path", "")
            cliente = header.get("cliente", "")

            ticket_paths = generar_ticket_para_cotizacion(
                pdf_path=pdf_path,
                items_pdf=items_shown,
                cliente_nombre=cliente,
                printer_name="TICKERA",
                width=48,
                top_mm=0.0,
                bottom_mm=10.0,
                cut_mode="full_feed",
            )

            cmd = ticket_paths.get("ticket_cmd")
            if cmd and os.path.exists(cmd):
                QMessageBox.information(self, "Ticket", f"Se creó el .cmd:\n{cmd}\n\nDoble click para imprimir.")
                QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(os.path.dirname(cmd))))
            else:
                QMessageBox.warning(self, "Ticket", "No se pudo generar el ticket.")
        except Exception as e:
            log.exception("Error reimprimiendo ticket")
            QMessageBox.critical(self, "Error", f"No se pudo reimprimir el ticket:\n{e}")

    def _regen_pdf_overwrite(self):
        qid = self._selected_quote_id()
        if not qid:
            QMessageBox.information(self, "Atención", "Selecciona una cotización.")
            return

        try:
            con = connect(self._db_path)
            ensure_schema(con)
            header = get_quote_header(con, qid)
            _items_base, items_shown = get_quote_items(con, qid)
            con.close()

            out_path = resolve_pdf_path_portable(header.get("pdf_path"))
            if not out_path:
                QMessageBox.warning(self, "Error", "La cotización no tiene ruta de PDF.")
                return

            metodo_pago = (header.get("metodo_pago") or "").strip()
            if APP_COUNTRY == "PERU":
                pass
            elif APP_COUNTRY == "PARAGUAY":
                if not metodo_pago:
                    metodo_pago = "Tarjeta"
            else:
                if not metodo_pago:
                    metodo_pago = "Transferencia"

            datos = {
                "fecha": (header.get("created_at", "") or "")[:10],
                "cliente": header.get("cliente", ""),
                "cedula": header.get("cedula", ""),
                "telefono": header.get("telefono", ""),
                "metodo_pago": metodo_pago,
                "items": items_shown,
                "subtotal_bruto": float(nz(header.get("subtotal_bruto_shown"), 0.0)),
                "descuento_total": float(nz(header.get("descuento_total_shown"), 0.0)),
                "total_general": float(nz(header.get("total_neto_shown"), 0.0)),
            }

            generar_pdf(datos, fixed_quote_no=str(header.get("quote_no", "")).zfill(7), out_path=out_path)

            QMessageBox.information(self, "PDF regenerado", f"PDF actualizado:\n{out_path}")
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(COTIZACIONES_DIR)))
        except Exception as e:
            log.exception("Error regenerando PDF")
            QMessageBox.critical(self, "Error", f"No se pudo regenerar el PDF:\n{e}")

    def closeEvent(self, event: QCloseEvent):
        if self._closing_with_children:
            event.accept()
            return

        self._prune_open_windows()
        open_wins = self._alive_quote_windows()

        if not open_wins:
            event.accept()
            return

        n = len(open_wins)
        plural = (n != 1)

        mb = QMessageBox(self)
        mb.setIcon(QMessageBox.Warning)
        mb.setWindowTitle("Cerrar histórico")
        mb.setText(
            f"Hay {n} cotización{'es' if plural else ''} abierta{'s' if plural else ''}.\n\n"
            f"Si cierras el histórico, se cerrarán {'todas' if plural else 'la'} "
            f"{'las' if plural else 'la'} cotización{'es' if plural else ''} abierta{'s' if plural else ''}."
        )
        btn_close_all = mb.addButton("Cerrar todas", QMessageBox.AcceptRole)
        btn_cancel = mb.addButton("Cancelar", QMessageBox.RejectRole)
        mb.setDefaultButton(btn_cancel)

        mb.exec()

        if mb.clickedButton() != btn_close_all:
            event.ignore()
            return

        self._closing_with_children = True
        ok = self._close_all_quotes()
        self._closing_with_children = False

        if not ok:
            QMessageBox.warning(
                self,
                "Atención",
                "No se pudo cerrar el histórico porque aún hay cotizaciones abiertas."
            )
            event.ignore()
            return

        event.accept()
