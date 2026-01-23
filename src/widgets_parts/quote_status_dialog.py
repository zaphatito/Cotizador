# src/widgets_parts/quote_status_dialog.py
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QComboBox,
)

from sqlModels.quotes_repo import (
    STATUS_PAGADO,
    STATUS_POR_PAGAR,
    STATUS_PENDIENTE,
    STATUS_NO_APLICA,
    normalize_status,
    status_label,
)

from .status_colors import bg_for_status, best_text_color_for_bg


class QuoteStatusDialog(QDialog):
    """
    Dialog para elegir 'Estado' de una cotización.
    Devuelve estado canónico (PAGADO/POR_PAGAR/PENDIENTE/NO_APLICA) o None (sin estado).
    """

    def __init__(self, parent=None, *, current_status: str | None = None):
        super().__init__(parent)
        self.setWindowTitle("Cambiar estado")
        self.setModal(True)

        self._current = normalize_status(current_status)

        lay = QVBoxLayout(self)

        lay.addWidget(QLabel("Selecciona el estado para esta cotización:"))

        self.cbo = QComboBox()
        self.cbo.addItem("Sin estado", "")
        self.cbo.addItem(status_label(STATUS_PAGADO), STATUS_PAGADO)
        self.cbo.addItem(status_label(STATUS_POR_PAGAR), STATUS_POR_PAGAR)
        self.cbo.addItem(status_label(STATUS_PENDIENTE), STATUS_PENDIENTE)
        self.cbo.addItem(status_label(STATUS_NO_APLICA), STATUS_NO_APLICA)
        lay.addWidget(self.cbo)

        self.preview = QLabel(" ")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setFixedHeight(28)
        lay.addWidget(self.preview)

        btns = QHBoxLayout()
        btns.addStretch(1)
        self.btn_ok = QPushButton("Guardar")
        btns.addWidget(self.btn_ok)
        lay.addLayout(btns)

        self.btn_ok.clicked.connect(self.accept)
        self.cbo.currentIndexChanged.connect(self._update_preview)

        self._set_initial()

    def _set_initial(self):
        if not self._current:
            self.cbo.setCurrentIndex(0)
        else:
            for i in range(self.cbo.count()):
                if normalize_status(self.cbo.itemData(i)) == self._current:
                    self.cbo.setCurrentIndex(i)
                    break
        self._update_preview()

    def _update_preview(self):
        st = normalize_status(self.cbo.currentData())
        if not st:
            self.preview.setText("Sin estado")
            self.preview.setStyleSheet("border: 1px solid #999; border-radius: 6px;")
            return

        self.preview.setText(status_label(st))

        bg = bg_for_status(st)
        if bg is None:
            self.preview.setStyleSheet("border: 1px solid #999; border-radius: 6px;")
            return

        fg = best_text_color_for_bg(bg)

        self.preview.setStyleSheet(
            "border: 1px solid #999; border-radius: 6px; "
            f"background-color: {bg.name()}; color: {fg.name()};"
        )

    def status(self) -> str | None:
        """Return canonical status or None (meaning: no status)."""
        return normalize_status(self.cbo.currentData())
