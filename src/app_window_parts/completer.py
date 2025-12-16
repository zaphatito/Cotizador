# src/app_window_parts/completer.py
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, QStringListModel
from PySide6.QtWidgets import QCompleter

from ..config import listing_allows_products, listing_allows_presentations


def build_completer_strings(productos, botellas_pc):
    sugs = []
    if listing_allows_products():
        for p in productos:
            cat = p.get("categoria", "")
            gen = p.get("genero", "")
            sugs.append(
                f"{p['id']} - {p['nombre']} - {cat}" + (f" - {gen}" if gen else "")
            )
    if listing_allows_presentations():
        for pc in botellas_pc:
            sugs.append(f"{pc.get('id')} - Presentación (PC) - {pc.get('nombre', '')}")
    return sugs


class CompleterMixin:
    def _build_completer(self):
        self._sug_model = QStringListModel(
            build_completer_strings(self.productos, self._botellas_pc)
        )
        self._completer = QCompleter(self._sug_model, self)
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchContains)
        self.entry_producto.setCompleter(self._completer)

        def add_from_completion(text: str):
            if self._ignore_completer:
                self._ignore_completer = False
                return
            cod = str(text).split(" - ")[0].strip()
            self._suppress_next_return = True
            self._agregar_por_codigo(cod)
            QTimer.singleShot(0, self.entry_producto.clear)
            if self._completer.popup():
                self._completer.popup().hide()

        self._completer.activated[str].connect(add_from_completion)

    def _on_return_pressed(self):
        popup = self._completer.popup() if self._completer else None
        if popup and popup.isVisible():
            idx = popup.currentIndex()
            if idx.isValid():
                text = idx.data()
                cod = str(text).split(" - ")[0].strip()
                self._ignore_completer = True
                self._suppress_next_return = True
                self._agregar_por_codigo(cod)
                QTimer.singleShot(0, self.entry_producto.clear)
                popup.hide()
                return
        if self._suppress_next_return:
            self._suppress_next_return = False
            return
        text = self.entry_producto.text().strip()
        if not text:
            return
        cod = text.split(" - ")[0].strip()
        self._agregar_por_codigo(cod)
        self.entry_producto.clear()
