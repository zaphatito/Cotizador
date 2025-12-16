# src/widgets_parts/selector_tabla_simple.py
from __future__ import annotations

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLineEdit,
    QTableWidget,
    QHeaderView,
    QAbstractItemView,
    QTableWidgetItem,
    QPushButton,
)


class SelectorTablaSimple(QDialog):
    def __init__(self, parent, titulo, filas, app_icon: QIcon = QIcon()):
        super().__init__(parent)
        self.setWindowTitle(titulo)
        self.resize(560, 420)
        if not app_icon.isNull():
            self.setWindowIcon(app_icon)
        self.seleccion = None

        v = QVBoxLayout(self)
        self.entry_buscar = QLineEdit()
        self.entry_buscar.setPlaceholderText("Filtrar…")
        v.addWidget(self.entry_buscar)

        self.tabla = QTableWidget(0, 4)
        self.tabla.setHorizontalHeaderLabels(["Código", "Nombre", "Departamento", "Género"])
        self.tabla.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tabla.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tabla.setSelectionBehavior(QAbstractItemView.SelectRows)
        v.addWidget(self.tabla)

        self._rows = filas[:]

        def pintar(rows):
            self.tabla.setRowCount(0)
            for r in rows:
                i = self.tabla.rowCount()
                self.tabla.insertRow(i)
                self.tabla.setItem(i, 0, QTableWidgetItem(str(r.get("codigo", ""))))
                self.tabla.setItem(i, 1, QTableWidgetItem(str(r.get("nombre", ""))))
                self.tabla.setItem(i, 2, QTableWidgetItem(str(r.get("categoria", ""))))
                self.tabla.setItem(i, 3, QTableWidgetItem(str(r.get("genero", ""))))

        self._pintar = pintar
        self._pintar(self._rows)

        def filtrar(txt):
            t = (txt or "").lower().strip()
            if not t:
                self._pintar(self._rows)
                return
            filtrados = []
            for r in self._rows:
                if (
                    t in str(r.get("codigo", "")).lower()
                    or t in str(r.get("nombre", "")).lower()
                    or t in str(r.get("categoria", "")).lower()
                    or t in str(r.get("genero", "")).lower()
                ):
                    filtrados.append(r)
            self._pintar(filtrados)

        self.entry_buscar.textChanged.connect(filtrar)

        self.tabla.cellDoubleClicked.connect(lambda row, _col: self._guardar(row))
        btn = QPushButton("Seleccionar")
        btn.clicked.connect(lambda: self._guardar(self.tabla.currentRow()))
        v.addWidget(btn)

    def _guardar(self, row):
        if row < 0:
            return
        item = {
            "codigo": self.tabla.item(row, 0).text() if self.tabla.item(row, 0) else "",
            "nombre": self.tabla.item(row, 1).text() if self.tabla.item(row, 1) else "",
            "categoria": self.tabla.item(row, 2).text() if self.tabla.item(row, 2) else "",
            "genero": self.tabla.item(row, 3).text() if self.tabla.item(row, 3) else "",
        }
        self.seleccion = item
        self.accept()
