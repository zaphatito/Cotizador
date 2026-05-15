from __future__ import annotations

import math
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..config import APP_COMPANY_TYPE, CATS
from ..product_rules import is_py_unit_product
from ..utils import nz
from sqlModels.db import connect
from sqlModels.settings_repo import get_setting
from ..label_printing_service import (
    ZEBRA_IP_DEFAULT,
    ZEBRA_PORT_DEFAULT,
    ZplEtiqueta,
    generar_zpl_lote,
    imprimir_zpl_red,
    resolve_logo_path_for_company,
)


def _fmt_num(x: float) -> str:
    try:
        if math.isfinite(x) and math.isclose(x, round(x), abs_tol=1e-9):
            return str(int(round(x)))
    except Exception:
        pass
    return f"{float(nz(x, 0.0)):.3f}".rstrip("0").rstrip(".")


def _esencia_a_gramos(item: dict, qty: float, country: str) -> float:
    country_u = str(country or "").strip().upper()
    if is_py_unit_product(item, country=country_u):
        return 0.0
    if country_u in ("VENEZUELA", "PARAGUAY"):
        return float(qty) * 50.0
    return float(qty) * 1000.0


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



class LabelsDialog(QDialog):
    def __init__(self, parent, *, quote_code: str, country: str, items: list[dict]):
        super().__init__(parent)
        self._country = str(country or "").strip().upper()
        self.setWindowTitle(f"Etiquetas - {quote_code}".strip(" -"))
        self.resize(860, 500)

        v = QVBoxLayout(self)
        v.addWidget(QLabel("Define los gramos por etiqueta separados por coma, espacio o '+'."))

        self.table = QTableWidget(0, 4, self)
        self.table.setHorizontalHeaderLabels(["Codigo", "Gramos Totales", "Numero Etiq.", "Etiquetas"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setAlternatingRowColors(True)
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

            it_n = QTableWidgetItem("0")
            it_n.setFlags(it_n.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(r, 2, it_n)

            self.table.setItem(r, 3, QTableWidgetItem(""))

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item is None or item.column() != 3:
            return
        self._revalidate_row(item.row())
        self._revalidate_all()

    def _revalidate_row(self, row: int) -> bool:
        raw = str(self.table.item(row, 3).text() if self.table.item(row, 3) else "")
        total_item = self.table.item(row, 1)
        total_g = float(total_item.data(Qt.UserRole) or 0.0) if total_item else 0.0

        ok = True
        n_labels = 0
        bg = QColor("#ffffff")
        status = ""

        try:
            grams = _parse_labels_grams(raw)
            n_labels = len(grams)
            s = float(sum(grams))
            if n_labels == 0:
                ok = True
                status = "sin etiquetas"
            else:
                ok = math.isclose(s, total_g, abs_tol=1e-6)
                status = f"suma {_fmt_num(s)} g"
                if not ok:
                    status += f" (debe ser {_fmt_num(total_g)} g)"
        except Exception:
            ok = False
            status = "formato invalido"

        if not ok:
            bg = QColor("#ffd7d7")
        lbl_n = self.table.item(row, 2)
        if lbl_n is not None:
            lbl_n.setText(str(n_labels))
        cell = self.table.item(row, 3)
        if cell is not None:
            cell.setBackground(bg)
            cell.setData(Qt.UserRole, status)
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
                con = connect()
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
            imprimir_zpl_red(zpl, ip=ip, port=port)
            QMessageBox.information(self, "Etiquetas", f"Impresion enviada a {ip}:{port}.")
        except Exception as e:
            QMessageBox.critical(self, "Etiquetas", f"No se pudo imprimir etiquetas:\n{e}")
