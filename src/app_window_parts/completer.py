# src/app_window_parts/completer.py
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, QStringListModel
from PySide6.QtWidgets import QCompleter, QApplication

from ..config import listing_allows_products, listing_allows_presentations, ALLOW_NO_STOCK, CATS
from ..utils import nz


def _is_generic_category_product(prod: dict) -> bool:
    pid = str(prod.get("id", "")).strip().upper()
    name = str(prod.get("nombre", "")).strip().upper()
    cat = str(prod.get("categoria", "")).strip().upper()
    dept = str(prod.get("departamento", "") or prod.get("categoria", "")).strip().upper()
    return bool(pid and ((pid == cat and name == cat) or (pid == dept and name == dept)))


def _generic_relation_categories(productos) -> set[str]:
    cats = {str(c or "").strip().upper() for c in (CATS or []) if str(c or "").strip()}
    for p in productos or []:
        if _is_generic_category_product(p):
            dept = str(p.get("departamento", "") or p.get("categoria", "")).strip().upper()
            if dept:
                cats.add(dept)
    return cats


def _split_relation_tokens(raw: str, *, generic_categories: set[str]) -> tuple[list[str], list[str]]:
    exact_codes: list[str] = []
    wildcard_categories: list[str] = []
    seen_exact: set[str] = set()
    seen_wild: set[str] = set()

    for tok in str(raw or "").split(","):
        code = str(tok or "").strip().upper()
        if not code:
            continue
        if code in generic_categories:
            if code not in seen_wild:
                seen_wild.add(code)
                wildcard_categories.append(code)
            continue
        if code not in seen_exact:
            seen_exact.add(code)
            exact_codes.append(code)

    return exact_codes, wildcard_categories


def _global_fixed_component_codes(presentaciones, *, generic_categories: set[str]) -> set[str]:
    fixed_codes: set[str] = set()
    for pr in presentaciones or []:
        raw = str(pr.get("CODIGOS_PRODUCTO") or pr.get("codigos_producto") or "").strip()
        if not raw:
            continue
        exact_codes, wildcard_categories = _split_relation_tokens(
            raw,
            generic_categories=generic_categories,
        )
        if wildcard_categories:
            fixed_codes.update(exact_codes)
    return fixed_codes


def build_completer_strings(productos, botellas_pc, presentaciones=None):
    sugs = []
    seen = set()
    prod_map = {
        str(p.get("id", "")).strip().upper(): p
        for p in (productos or [])
        if str(p.get("id", "")).strip()
    }
    generic_categories = _generic_relation_categories(productos or [])
    fixed_component_codes = _global_fixed_component_codes(
        presentaciones or [],
        generic_categories=generic_categories,
    )

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

            exact_codes, wildcard_categories = _split_relation_tokens(
                rel_codes,
                generic_categories=generic_categories,
            )

            base_candidates = []
            if wildcard_categories:
                for base in productos or []:
                    if _is_generic_category_product(base):
                        continue
                    base_code = str(base.get("id") or "").strip().upper()
                    if base_code in fixed_component_codes or base_code in set(exact_codes):
                        continue
                    base_dep = str(base.get("departamento") or base.get("categoria") or "").strip().upper()
                    if base_dep not in set(wildcard_categories):
                        continue
                    base_candidates.append(base)
            else:
                for base_code in exact_codes:
                    base = prod_map.get(base_code)
                    if not base or _is_generic_category_product(base):
                        continue
                    base_candidates.append(base)

            for base in base_candidates:
                base_code = str(base.get("id") or "").strip().upper()
                if not base_code:
                    continue

                base_stock = float(nz(base.get("cantidad_disponible"), 0.0))
                if (not ALLOW_NO_STOCK) and base_stock <= 0.0:
                    continue

                base_gen = str(base.get("genero") or "").strip().lower()
                if gen and base_gen and base_gen != str(gen).strip().lower():
                    continue

                base_name = str(base.get("nombre") or "").strip()
                combo_name = " ".join([x for x in [base_name, name] if x]).strip() or name or code
                combo_code = f"{base_code}{code}"
                _add_sug(
                    f"{combo_code} - {combo_name} - PRESENTACION - {dep}" + (f" - {gen}" if gen else "")
                )

    return sugs


class CompleterMixin:
    def _teardown_plain_completer(self):
        comp = getattr(self, "_completer", None)
        if comp is not None:
            try:
                if getattr(self, "entry_producto", None) is not None:
                    self.entry_producto.setCompleter(None)
            except Exception:
                pass
            try:
                comp.deleteLater()
            except Exception:
                pass
        self._completer = None
        self._sug_model = None

    def _teardown_ai_completers(self):
        for attr in ("_ai_prod", "_ai_cli", "_ai_doc", "_ai_tel", "_ai_dir", "_ai_email"):
            comp = getattr(self, attr, None)
            if comp is None:
                continue
            try:
                if hasattr(comp, "hide_popup"):
                    comp.hide_popup()
            except Exception:
                pass
            try:
                comp.deleteLater()
            except Exception:
                pass
            setattr(self, attr, None)

        self._ai_index = None

    def _rebuild_search_completers(self):
        self._teardown_plain_completer()
        self._teardown_ai_completers()
        self._build_completer()

    def _hide_all_client_popups(self):
        for k in ("_ai_cli", "_ai_doc", "_ai_tel", "_ai_dir", "_ai_email"):
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
            addr = str(payload.get("direccion") or "-").strip() or "-"
            mail = str(payload.get("email") or "-").strip() or "-"

            if cli:
                self.entry_cliente.setText(cli)
            if doc:
                self.entry_cedula.setText(doc)
            if tel:
                self.entry_telefono.setText(tel)
            if getattr(self, "entry_direccion", None) is not None:
                self.entry_direccion.setText(addr)
            if getattr(self, "entry_email", None) is not None:
                self.entry_email.setText(mail)

            w = QApplication.focusWidget()
            if w is getattr(self, "entry_cliente", None):
                self._go_doc()
            elif w is getattr(self, "entry_cedula", None):
                self._go_name()
            elif w is getattr(self, "entry_telefono", None):
                self._go_address()
            elif w is getattr(self, "entry_direccion", None):
                self._go_email()
            elif w is getattr(self, "entry_email", None):
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
