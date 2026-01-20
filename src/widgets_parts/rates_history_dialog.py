# src/widgets_parts/rates_history_dialog.py
from __future__ import annotations

import datetime

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableView,
    QHeaderView,
    QVBoxLayout,
)

from ..config import APP_CURRENCY, get_secondary_currencies
from ..db_path import resolve_db_path
from ..logging_setup import get_logger

from sqlModels.db import connect, ensure_schema

log = get_logger(__name__)


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


def center_on_screen(w):
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


class RatesHistoryTableModel(QAbstractTableModel):
    HEADERS = ["Fecha/Hora", "Tasa"]

    def __init__(self):
        super().__init__()
        self.rows: list[dict] = []

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

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        r = self.rows[index.row()]
        c = index.column()

        if role == Qt.DisplayRole:
            if c == 0:
                return format_dt_legible(r.get("recorded_at"))
            if c == 1:
                try:
                    return f"{float(r.get('rate', 0.0)):.6f}"
                except Exception:
                    return str(r.get("rate", ""))

        if role == Qt.TextAlignmentRole:
            if c == 1:
                return int(Qt.AlignVCenter | Qt.AlignRight)
            return int(Qt.AlignVCenter | Qt.AlignLeft)

        return None

    def set_rows(self, rows: list[dict]):
        self.beginResetModel()
        self.rows = rows or []
        self.endResetModel()

    def sort(self, column: int, order: Qt.SortOrder = Qt.AscendingOrder):
        if not self.rows:
            return

        self.layoutAboutToBeChanged.emit()
        try:
            reverse = (order == Qt.DescendingOrder)

            def key_dt(x):
                dt = _parse_dt(x.get("recorded_at"))
                return (dt is None, dt or datetime.datetime.min)

            def key_rate(x):
                try:
                    return float(x.get("rate", 0.0))
                except Exception:
                    return 0.0

            if column == 0:
                self.rows.sort(key=key_dt, reverse=reverse)
            elif column == 1:
                self.rows.sort(key=key_rate, reverse=reverse)
        finally:
            self.layoutChanged.emit()


class RatesHistoryDialog(QDialog):
    """
    Ventanita para ver el histórico de tasas (exchange_rates_history).
    Se actualiza sola cuando quote_events.rates_updated se emite.
    """
    def __init__(
        self,
        parent=None,
        *,
        base_currency: str | None = None,
        initial_currency: str | None = None,
        quote_events=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Histórico de tasas de cambio")
        self.resize(640, 460)

        self.base = (base_currency or APP_CURRENCY).strip().upper()
        self.cur = (initial_currency or "").strip().upper()

        self._quote_events = quote_events
        self._events_connected = False

        self.model = RatesHistoryTableModel()

        lay = QVBoxLayout(self)

        # Top: selector moneda
        top = QHBoxLayout()
        self.lbl_title = QLabel("")
        self.lbl_title.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.cmb_currency = QComboBox()
        currencies = [c.upper() for c in (get_secondary_currencies() or [])]
        self.cmb_currency.addItems(currencies)

        if self.cur and self.cur in currencies:
            self.cmb_currency.setCurrentText(self.cur)
        elif currencies:
            self.cur = currencies[0]

        top.addWidget(QLabel("Moneda:"))
        top.addWidget(self.cmb_currency, 0)
        top.addStretch(1)

        lay.addLayout(top)
        lay.addWidget(self.lbl_title)

        # Tabla
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSortIndicatorShown(True)

        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)

        lay.addWidget(self.table, 1)

        # Botones
        bottom = QHBoxLayout()
        self.btn_close = QPushButton("Cerrar")
        self.btn_close.clicked.connect(self.accept)
        bottom.addStretch(1)
        bottom.addWidget(self.btn_close)
        lay.addLayout(bottom)

        self.cmb_currency.currentTextChanged.connect(self._on_currency_changed)

        self._connect_events()
        self._reload()
        center_on_screen(self)

    def closeEvent(self, event):
        self._disconnect_events()
        super().closeEvent(event)

    def _connect_events(self):
        qe = self._quote_events
        if qe is None:
            return
        try:
            qe.rates_updated.connect(self._reload)
            self._events_connected = True
        except Exception:
            self._events_connected = False

    def _disconnect_events(self):
        if not self._events_connected:
            return
        qe = self._quote_events
        if qe is None:
            return
        try:
            qe.rates_updated.disconnect(self._reload)
        except Exception:
            pass
        self._events_connected = False

    def _on_currency_changed(self, txt: str):
        self.cur = (txt or "").strip().upper()
        self._reload()

    def _reload(self):
        cur = (self.cur or "").strip().upper()
        if not cur:
            self.lbl_title.setText("Selecciona una moneda.")
            self.model.set_rows([])
            return

        self.lbl_title.setText(f"1 {self.base} = ? {cur}")

        try:
            db_path = resolve_db_path()
            con = connect(db_path)
            ensure_schema(con)

            rows = con.execute(
                """
                SELECT rate, recorded_at
                FROM exchange_rates_history
                WHERE base_currency = ? AND currency = ?
                ORDER BY recorded_at DESC, id DESC
                LIMIT 500
                """,
                (self.base, cur),
            ).fetchall()
            con.close()

            out: list[dict] = [{"rate": r["rate"], "recorded_at": r["recorded_at"]} for r in rows]
            self.model.set_rows(out)

            # default: orden por fecha desc
            self.model.sort(0, Qt.DescendingOrder)
            self.table.horizontalHeader().setSortIndicator(0, Qt.DescendingOrder)

        except Exception as e:
            log.exception("Error cargando histórico de tasas")
            QMessageBox.warning(
                self,
                "Histórico no disponible",
                "No se pudo leer exchange_rates_history.\n\n"
                "Asegúrate de tener la migración/tabla creada (schema_version >= 3).\n\n"
                f"Detalle: {e}",
            )
            self.model.set_rows([])
