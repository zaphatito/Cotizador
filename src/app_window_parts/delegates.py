# src/app_window_parts/delegates.py
from __future__ import annotations

from PySide6.QtWidgets import QLineEdit, QStyledItemDelegate
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtCore import QRegularExpression


class QuantityDelegate(QStyledItemDelegate):
    """
    Delegate para la columna 'Cantidad':
    Solo permite números y separadores decimales (.,-) en el editor.
    Evita que el usuario escriba letras directamente.
    """

    def createEditor(self, parent, option, index):
        editor = QLineEdit(parent)
        rx = QRegularExpression(r"^[0-9.,-]*$")
        validator = QRegularExpressionValidator(rx, editor)
        editor.setValidator(validator)
        return editor
