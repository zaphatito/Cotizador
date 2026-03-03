# src/widgets_parts/quote_status_dialog.py
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from sqlModels.quote_statuses_repo import get_quote_statuses_cached
from sqlModels.quotes_repo import normalize_status

from ..db_path import resolve_db_path
from .status_colors import bg_for_status, best_text_color_for_bg


class QuoteStatusDialog(QDialog):
    def __init__(self, parent=None, *, current_status: str | None = None):
        super().__init__(parent)
        self.setWindowTitle("Cambiar estado")
        self.setModal(True)

        self._current = normalize_status(current_status)
        self._statuses = self._load_statuses()

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Selecciona el estado para esta cotizacion:"))

        self.cbo = QComboBox()
        self.cbo.addItem("Sin estado", "")
        for st in self._statuses:
            self.cbo.addItem(str(st.get("label") or ""), str(st.get("code") or ""))
        lay.addWidget(self.cbo)

        self.preview = QLabel(" ")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setFixedHeight(28)
        lay.addWidget(self.preview)

        btns = QHBoxLayout()
        btns.addStretch(1)
        self.btn_ok = QPushButton("Guardar")
        self.btn_ok.setProperty("variant", "primary")
        btns.addWidget(self.btn_ok)
        lay.addLayout(btns)

        self.btn_ok.clicked.connect(self.accept)
        self.cbo.currentIndexChanged.connect(self._update_preview)

        self._set_initial()

    def _load_statuses(self) -> list[dict]:
        try:
            rows = get_quote_statuses_cached(db_path=resolve_db_path(), force_reload=False)
            return rows or []
        except Exception:
            return []

    def _set_initial(self):
        if not self._current:
            self.cbo.setCurrentIndex(0)
        else:
            for i in range(self.cbo.count()):
                if normalize_status(self.cbo.itemData(i)) == self._current:
                    self.cbo.setCurrentIndex(i)
                    break
        self._update_preview()

    def reload_statuses(self) -> None:
        current = normalize_status(self.cbo.currentData())
        self._statuses = self._load_statuses()

        self.cbo.blockSignals(True)
        try:
            self.cbo.clear()
            self.cbo.addItem("Sin estado", "")
            for st in self._statuses:
                self.cbo.addItem(str(st.get("label") or ""), str(st.get("code") or ""))
        finally:
            self.cbo.blockSignals(False)

        self._current = current
        self._set_initial()

    def _update_preview(self):
        st = normalize_status(self.cbo.currentData())
        if not st:
            self.preview.setText("Sin estado")
            self.preview.setStyleSheet(
                "border: 1px solid #8AA0BC; border-radius: 10px; background: transparent;"
            )
            return

        self.preview.setText(str(self.cbo.currentText() or "").strip() or st)

        bg = bg_for_status(st)
        if bg is None:
            self.preview.setStyleSheet(
                "border: 1px solid #8AA0BC; border-radius: 10px; background: transparent;"
            )
            return

        fg = best_text_color_for_bg(bg)

        self.preview.setStyleSheet(
            "border: 1px solid #8AA0BC; border-radius: 10px; "
            f"background-color: {bg.name()}; color: {fg.name()};"
        )

    def status(self) -> str | None:
        return normalize_status(self.cbo.currentData())
