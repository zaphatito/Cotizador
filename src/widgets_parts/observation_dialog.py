# src/widgets_parts/observation_dialog.py
from __future__ import annotations

from typing import Optional

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)


def show_observation_dialog(
    parent: QWidget,
    app_icon: QIcon,
    initial_text: str,
) -> Optional[str]:
    """Devuelve el nuevo texto o None si se cancela."""
    dlg = QDialog(parent)
    dlg.setWindowTitle("Editar Observación")
    dlg.resize(320, 120)
    if not app_icon.isNull():
        dlg.setWindowIcon(app_icon)

    v = QVBoxLayout(dlg)
    v.addWidget(QLabel("Ingrese observación (ej: Color ámbar):"))
    entry = QLineEdit()
    entry.setText(initial_text or "")
    v.addWidget(entry)
    btn = QPushButton("Guardar")
    v.addWidget(btn)

    result: dict[str, Optional[str]] = {"text": None}

    def _save():
        result["text"] = entry.text().strip()
        dlg.accept()

    btn.clicked.connect(_save)

    if dlg.exec() != QDialog.Accepted:
        return None
    return result["text"]
