# src/app_window_parts/completer.py
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, QStringListModel
from PySide6.QtWidgets import QCompleter, QApplication

from ..config import listing_allows_products, listing_allows_presentations, ALLOW_NO_STOCK
from ..utils import nz


def build_completer_strings(productos, botellas_pc):
    sugs = []

    if listing_allows_products():
        for p in productos or []:
            if (not ALLOW_NO_STOCK) and float(nz(p.get("cantidad_disponible"), 0.0)) <= 0.0:
                continue

            cat = p.get("categoria", "")
            gen = p.get("genero", "")
            sugs.append(
                f"{p['id']} - {p['nombre']} - {cat}" + (f" - {gen}" if gen else "")
            )

    if listing_allows_presentations():
        for pc in botellas_pc or []:
            if (not ALLOW_NO_STOCK) and float(nz(pc.get("cantidad_disponible"), 0.0)) <= 0.0:
                continue
            sugs.append(f"{pc.get('id')} - Presentación (PC) - {pc.get('nombre', '')}")

    return sugs


class CompleterMixin:
    def _hide_all_client_popups(self):
        for k in ("_ai_cli", "_ai_doc", "_ai_tel"):
            c = getattr(self, k, None)
            try:
                if c is not None:
                    c.hide_popup()
            except Exception:
                pass

    def _on_ai_client_picked(self, payload: dict):
        """
        Rellena cliente / DNI / teléfono desde el payload.
        Evita popups duplicados cerrando todos primero.
        """
        try:
            # ✅ cierra todos los popups de cliente (por si 2 quedaron abiertos)
            self._hide_all_client_popups()

            cli = str(payload.get("cliente") or "").strip()
            doc = str(payload.get("cedula") or "").strip()
            tel = str(payload.get("telefono") or "").strip()

            # setText() ya NO abrirá popups porque SmartCompleter usa textEdited
            if cli:
                self.entry_cliente.setText(cli)
            if doc:
                self.entry_cedula.setText(doc)
            if tel:
                self.entry_telefono.setText(tel)

            # mover foco según el campo donde estabas
            w = QApplication.focusWidget()
            if w is getattr(self, "entry_cliente", None):
                self._go_doc()
            elif w is getattr(self, "entry_cedula", None):
                self._go_phone()
            elif w is getattr(self, "entry_telefono", None):
                self._go_product_search()

        except Exception:
            pass

    def _build_completer(self):
        if bool(getattr(self, "_use_ai_completer", False)):
            try:
                self._setup_ai_completers()
            except Exception:
                pass
            return

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
