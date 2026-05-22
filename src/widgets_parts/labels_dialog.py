from __future__ import annotations

import math
import re
import threading

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..config import APP_COMPANY_TYPE, CATS
from ..db_path import resolve_db_path
from ..logging_setup import get_logger
from ..product_rules import is_py_unit_product
from ..utils import nz
from sqlModels.db import connect
from sqlModels.settings_repo import get_setting
from ..api.presupuesto_client import record_and_send_label_print_log
from ..label_printing_service import (
    ZEBRA_IP_DEFAULT,
    ZEBRA_PORT_DEFAULT,
    ZplEtiqueta,
    count_requested_labels,
    generar_zpl_lote,
    get_printer_label_counter,
    imprimir_zpl_red,
    labels_prefix,
    resolve_logo_path_for_company,
    wait_for_label_print_confirmation,
)

log = get_logger(__name__)


def _fmt_num(x: float) -> str:
    try:
        if math.isfinite(x) and math.isclose(x, round(x), abs_tol=1e-9):
            return str(int(round(x)))
    except Exception:
        pass
    return f"{float(nz(x, 0.0)):.3f}".rstrip("0").rstrip(".")


def _esencia_a_gramos(item: dict, qty: float, country: str) -> float:
    country_u = _normalize_country(country)
    if is_py_unit_product(item, country=country_u):
        return 0.0
    if country_u in ("VENEZUELA", "PARAGUAY"):
        return float(qty) * 50.0
    return float(qty) * 1000.0


def _normalize_country(country: str) -> str:
    c = str(country or "").strip().upper()
    if c == "PY":
        return "PARAGUAY"
    if c == "PE":
        return "PERU"
    if c == "VE":
        return "VENEZUELA"
    return c


def _parse_labels_grams(raw: str) -> list[float]:
    s = str(raw or "").strip()
    if not s:
        return []
    parts = [p for p in re.split(r"[,\+;\s]+", s) if p.strip()]
    out: list[float] = []
    for p in parts:
        v = float(p.replace(",", "."))
        if v > 0:
            out.append(v)
    return out


def _parse_label_count(raw: str) -> int:
    s = str(raw or "").strip()
    if not s:
        return 0
    if not re.fullmatch(r"\d+", s):
        raise ValueError("Numero de etiquetas invalido")
    return int(s)


def _split_grams(total_g: float, count: int) -> list[float]:
    if count <= 0:
        return []
    total_mg = int(round(float(total_g) * 1000.0))
    base = total_mg // count
    remainder = total_mg % count
    return [(base + (1 if i < remainder else 0)) / 1000.0 for i in range(count)]


def _is_dark_widget(widget) -> bool:
    base = widget.palette().base().color()
    lum = (0.2126 * base.redF()) + (0.7152 * base.greenF()) + (0.0722 * base.blueF())
    return lum < 0.45


def _invalid_cell_color(widget) -> QColor:
    return QColor("#5a1f23") if _is_dark_widget(widget) else QColor("#ffd7d7")


class _LabelsTableDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        editor = QLineEdit(parent)
        pal = parent.palette()
        base = pal.base().color().name()
        text = pal.text().color().name()
        highlight = pal.highlight().color().name()
        editor.setFrame(False)
        editor.setStyleSheet(
            "QLineEdit {"
            f"background-color: {base};"
            f"color: {text};"
            f"selection-background-color: {highlight};"
            "border: 1px solid transparent;"
            "padding: 0px 6px;"
            "}"
        )
        return editor


class LabelsDialog(QDialog):
    def __init__(self, parent, *, quote_code: str, country: str, items: list[dict]):
        super().__init__(parent)
        self._quote_code = str(quote_code or "").strip()
        self._country = str(country or "").strip().upper()
        self._updating_table = False
        self.setWindowTitle(f"Etiquetas - {quote_code}".strip(" -"))
        self.resize(860, 500)

        v = QVBoxLayout(self)
        v.addWidget(QLabel("Define los gramos por etiqueta separados por coma, espacio o '+'."))

        self.table = QTableWidget(0, 4, self)
        self.table.setObjectName("labelsTable")
        self.table.setHorizontalHeaderLabels(["Codigo", "Gramos Totales", "Numero Etiq.", "Etiquetas"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setItemDelegate(_LabelsTableDelegate(self.table))
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        v.addWidget(self.table, 1)

        btns = QHBoxLayout()
        self.lbl_status = QLabel("")
        self.btn_print = QPushButton("Imprimir")
        self.btn_close = QPushButton("Cerrar")
        self.btn_close.clicked.connect(self.reject)
        self.btn_print.clicked.connect(self._on_print_clicked)
        btns.addWidget(self.lbl_status, 1)
        btns.addWidget(self.btn_print, 0)
        btns.addWidget(self.btn_close, 0)
        v.addLayout(btns)

        self._load_rows(items or [])
        self.table.itemChanged.connect(self._on_item_changed)
        self._revalidate_all()

    def _load_rows(self, items: list[dict]) -> None:
        cats = {str(c or "").strip().upper() for c in (CATS or []) if str(c or "").strip()}
        esencia_items: list[dict] = []
        for it in items:
            cat_u = str((it or {}).get("categoria") or "").strip().upper()
            if cat_u in cats:
                esencia_items.append(it)

        for it in esencia_items:
            r = self.table.rowCount()
            self.table.insertRow(r)

            codigo = str(it.get("codigo") or "").strip()
            nombre = str(it.get("producto") or "").strip()
            qty = float(nz(it.get("cantidad"), 0.0))
            gramos_tot = _esencia_a_gramos(it, qty, self._country)

            it_code = QTableWidgetItem(codigo)
            it_code.setData(Qt.UserRole, nombre)
            it_code.setFlags(it_code.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(r, 0, it_code)

            it_grams = QTableWidgetItem(_fmt_num(gramos_tot))
            it_grams.setData(Qt.UserRole, float(gramos_tot))
            it_grams.setFlags(it_grams.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(r, 1, it_grams)

            it_n = QTableWidgetItem("1")
            self.table.setItem(r, 2, it_n)

            self.table.setItem(r, 3, QTableWidgetItem(_fmt_num(gramos_tot)))

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item is None or self._updating_table:
            return
        if item.column() == 2:
            self._apply_label_count(item.row())
        elif item.column() == 3:
            self._sync_count_from_labels(item.row())
        self._revalidate_all()

    def _total_grams_for_row(self, row: int) -> float:
        total_item = self.table.item(row, 1)
        return float(total_item.data(Qt.UserRole) or 0.0) if total_item else 0.0

    def _set_cell_text(self, row: int, col: int, value: str) -> None:
        item = self.table.item(row, col)
        if item is None:
            item = QTableWidgetItem("")
            self.table.setItem(row, col, item)
        item.setText(value)

    def _apply_label_count(self, row: int) -> None:
        item = self.table.item(row, 2)
        try:
            count = _parse_label_count(item.text() if item else "")
        except Exception:
            return

        grams = _split_grams(self._total_grams_for_row(row), count)
        labels_text = " ".join(_fmt_num(g) for g in grams)
        self._updating_table = True
        try:
            self._set_cell_text(row, 2, str(count))
            self._set_cell_text(row, 3, labels_text)
        finally:
            self._updating_table = False

    def _sync_count_from_labels(self, row: int) -> None:
        raw = str(self.table.item(row, 3).text() if self.table.item(row, 3) else "")
        try:
            count = len(_parse_labels_grams(raw))
        except Exception:
            return

        self._updating_table = True
        try:
            self._set_cell_text(row, 2, str(count))
        finally:
            self._updating_table = False

    def _revalidate_row(self, row: int) -> bool:
        raw = str(self.table.item(row, 3).text() if self.table.item(row, 3) else "")
        total_g = self._total_grams_for_row(row)

        ok = True
        clear_bg = QBrush()
        count_bg = clear_bg
        labels_bg = clear_bg
        invalid_bg = QBrush(_invalid_cell_color(self.table))

        try:
            expected_count = _parse_label_count(self.table.item(row, 2).text() if self.table.item(row, 2) else "")
            grams = _parse_labels_grams(raw)
            n_labels = len(grams)
            s = float(sum(grams))
            if expected_count != n_labels:
                ok = False
                count_bg = invalid_bg
            if expected_count == 0 and n_labels == 0:
                pass
            else:
                sum_ok = math.isclose(s, total_g, abs_tol=1e-6)
                ok = ok and sum_ok
                if not sum_ok:
                    labels_bg = invalid_bg
        except Exception:
            ok = False
            count_bg = invalid_bg
            labels_bg = invalid_bg

        lbl_n = self.table.item(row, 2)
        if lbl_n is not None:
            lbl_n.setBackground(count_bg)
        cell = self.table.item(row, 3)
        if cell is not None:
            cell.setBackground(labels_bg)
        return ok

    def _revalidate_all(self) -> None:
        all_ok = True
        with_labels = 0
        for r in range(self.table.rowCount()):
            row_ok = self._revalidate_row(r)
            all_ok = all_ok and row_ok
            try:
                if int(self.table.item(r, 2).text() or "0") > 0:
                    with_labels += 1
            except Exception:
                pass

        self.btn_print.setEnabled(all_ok)
        if not all_ok:
            self.lbl_status.setText("Hay filas con suma de gramos invalida.")
            self.lbl_status.setStyleSheet("color: #b42318;")
        elif with_labels == 0:
            self.lbl_status.setText("No se imprimira ninguna etiqueta (todas en 0).")
            self.lbl_status.setStyleSheet("color: #475467;")
        else:
            self.lbl_status.setText("Todo listo para imprimir.")
            self.lbl_status.setStyleSheet("color: #027a48;")

    def _wake_background_api_sync(self) -> None:
        try:
            parent = self.parent()
            if parent is not None and hasattr(parent, "_wake_background_api_sync"):
                parent._wake_background_api_sync()
        except Exception:
            pass

    def _confirm_and_record_print(
        self,
        *,
        labels: list[ZplEtiqueta],
        ip: str,
        port: int,
        counter_before: int,
        requested_labels: int,
    ) -> None:
        try:
            confirmation = wait_for_label_print_confirmation(
                ip=ip,
                port=port,
                counter_before=counter_before,
                requested_labels=requested_labels,
            )
            if not confirmation.ok or confirmation.printed_labels <= 0:
                log.warning(
                    "No se confirmo impresion de etiquetas quote=%s status=%s error=%s",
                    self._quote_code,
                    confirmation.status,
                    confirmation.error,
                )
                return

            confirmed_labels = labels_prefix(labels, confirmation.requested_labels)
            if not confirmed_labels:
                log.warning("Contador confirmo impresion, pero no hay etiquetas confirmadas para registrar.")
                return

            sync_result = record_and_send_label_print_log(
                quote_code=self._quote_code,
                labels=confirmed_labels,
                requested_labels=confirmation.requested_labels,
                printed_labels=confirmation.printed_labels,
                printer_counter_before=confirmation.counter_before,
                printer_counter_after=confirmation.counter_after,
                printer_counter_delta=confirmation.counter_delta_rows,
                printer_status=confirmation.status,
                printer_ip=ip,
                printer_port=port,
            )
            if str(sync_result.get("status") or "").strip().upper() != "SENT":
                self._wake_background_api_sync()
        except Exception as exc:
            log.warning("No se pudo confirmar/registrar impresion de etiquetas quote=%s: %s", self._quote_code, exc)

    def _on_print_clicked(self) -> None:
        try:
            labels: list[ZplEtiqueta] = []
            for r in range(self.table.rowCount()):
                code_item = self.table.item(r, 0)
                raw_item = self.table.item(r, 3)
                if code_item is None or raw_item is None:
                    continue
                codigo = str(code_item.text() or "").strip()
                nombre = str(code_item.data(Qt.UserRole) or codigo).strip()
                grams_list = _parse_labels_grams(raw_item.text() or "")
                for g in grams_list:
                    labels.append(ZplEtiqueta(nombre=nombre, codigo=codigo, gramos=f"{_fmt_num(g)}g", copias=1))

            if not labels:
                QMessageBox.information(self, "Etiquetas", "No hay etiquetas para imprimir.")
                return

            con = None
            try:
                con = connect(resolve_db_path())
                ip = str(get_setting(con, "label_printer_ip", ZEBRA_IP_DEFAULT) or ZEBRA_IP_DEFAULT).strip()
                port_raw = get_setting(con, "label_printer_port", str(ZEBRA_PORT_DEFAULT))
                port = int(str(port_raw or ZEBRA_PORT_DEFAULT).strip())
            finally:
                if con is not None:
                    try:
                        con.close()
                    except Exception:
                        pass

            logo_path = resolve_logo_path_for_company(APP_COMPANY_TYPE)
            zpl = generar_zpl_lote(labels, logo_path=logo_path)
            requested_labels = count_requested_labels(labels)
            counter_before = None
            counter_error = ""
            try:
                counter_before = get_printer_label_counter(ip, port, timeout=5.0)
            except Exception as exc:
                counter_error = str(exc)

            imprimir_zpl_red(zpl, ip=ip, port=port)

            if counter_before is None:
                QMessageBox.warning(
                    self,
                    "Etiquetas",
                    "Impresion enviada, pero la impresora no devolvio contador de etiquetas.\n"
                    "No se enviara el registro para evitar falsos positivos.\n\n"
                    f"Detalle: {counter_error}",
                )
                return

            threading.Thread(
                target=self._confirm_and_record_print,
                kwargs={
                    "labels": list(labels),
                    "ip": ip,
                    "port": port,
                    "counter_before": int(counter_before),
                    "requested_labels": int(requested_labels),
                },
                name="label-print-confirmation",
                daemon=True,
            ).start()
            QMessageBox.information(
                self,
                "Etiquetas",
                f"Impresion enviada a {ip}:{port}.\n"
                "La impresora confirmara el contador en segundo plano antes de enviar el registro.",
            )
        except Exception as e:
            QMessageBox.critical(self, "Etiquetas", f"No se pudo imprimir etiquetas:\n{e}")
