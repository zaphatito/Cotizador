# src/app_window_parts/completer.py
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, QStringListModel
from PySide6.QtWidgets import QCompleter, QApplication

from ..config import listing_allows_products, listing_allows_presentations, ALLOW_NO_STOCK
from ..utils import nz


def build_completer_strings(productos, botellas_pc, presentaciones=None):
    sugs = []
    seen = set()
    prod_map = {
        str(p.get("id", "")).strip().upper(): p
        for p in (productos or [])
        if str(p.get("id", "")).strip()
    }

    def _add_sug(s: str):
        t = str(s or "").strip()
        if not t:
            return
        key = t.upper()
        if key in seen:
            return
        seen.add(key)
        sugs.append(t)

    if listing_allows_products():
        for p in productos or []:
            if (not ALLOW_NO_STOCK) and float(nz(p.get("cantidad_disponible"), 0.0)) <= 0.0:
                continue

            cat = p.get("categoria", "")
            gen = p.get("genero", "")
            _add_sug(f"{p['id']} - {p['nombre']} - {cat}" + (f" - {gen}" if gen else ""))

    if listing_allows_presentations():
        for pr in presentaciones or []:
            stock = float(
                nz(
                    pr.get("STOCK_DISPONIBLE")
                    or pr.get("stock_disponible")
                    or pr.get("cantidad_disponible")
                    or 0.0,
                    0.0,
                )
            )
            if (not ALLOW_NO_STOCK) and stock <= 0.0:
                continue

            code = str(pr.get("CODIGO") or pr.get("codigo") or pr.get("CODIGO_NORM") or "").strip().upper()
            if not code:
                continue
            if code.startswith("PC"):
                continue

            name = str(pr.get("NOMBRE") or pr.get("nombre") or "").strip()
            dep = str(pr.get("DEPARTAMENTO") or pr.get("departamento") or "").strip().upper()
            gen = str(pr.get("GENERO") or pr.get("genero") or "").strip()

            _add_sug(f"{code} - {name} - PRESENTACION - {dep}" + (f" - {gen}" if gen else ""))

            rel_codes = str(pr.get("CODIGOS_PRODUCTO") or pr.get("codigos_producto") or "").strip()
            if not rel_codes:
                continue

            for tok in rel_codes.split(","):
                base_code = str(tok or "").strip().upper()
                if not base_code:
                    continue

                base = prod_map.get(base_code)
                if not base:
                    continue

                base_stock = float(nz(base.get("cantidad_disponible"), 0.0))
                if (not ALLOW_NO_STOCK) and base_stock <= 0.0:
                    continue

                base_name = str(base.get("nombre") or "").strip()
                combo_name = " ".join([x for x in [base_name, name] if x]).strip() or name or code
                combo_code = f"{base_code}{code}"
                _add_sug(
                    f"{combo_code} - {combo_name} - PRESENTACION - {dep}" + (f" - {gen}" if gen else "")
                )

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
        Rellena cliente / DNI / telefono desde el payload.
        Evita popups duplicados cerrando todos primero.
        """
        try:
            self._hide_all_client_popups()

            cli = str(payload.get("cliente") or "").strip()
            doc = str(payload.get("cedula") or "").strip()
            tel = str(payload.get("telefono") or "").strip()

            if cli:
                self.entry_cliente.setText(cli)
            if doc:
                self.entry_cedula.setText(doc)
            if tel:
                self.entry_telefono.setText(tel)

            w = QApplication.focusWidget()
            if w is getattr(self, "entry_cliente", None):
                self._go_doc()
            elif w is getattr(self, "entry_cedula", None):
                self._go_name()
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
            build_completer_strings(self.productos, self._botellas_pc, self.presentaciones)
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
