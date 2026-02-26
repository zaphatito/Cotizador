# src/app_window_parts/delegates.py
from __future__ import annotations

from PySide6.QtWidgets import QLineEdit, QStyledItemDelegate
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtCore import QRegularExpression, Qt, QTimer


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
        editor.setAlignment(Qt.AlignCenter)
        editor.setMaxLength(18)
        QTimer.singleShot(0, editor.selectAll)
        return editor

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect.adjusted(1, 1, -1, -1))


class InlineTextDelegate(QStyledItemDelegate):
    """
    Delegate de texto para edicion inline en tabla.
    Evita clipping causado por padding global del tema.
    """

    def createEditor(self, parent, option, index):
        editor = QLineEdit(parent)
        editor.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        editor.setStyleSheet("QLineEdit { padding-top: 0px; padding-bottom: 0px; }")
        QTimer.singleShot(0, editor.selectAll)
        return editor

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect.adjusted(0, 0, 0, 0))
