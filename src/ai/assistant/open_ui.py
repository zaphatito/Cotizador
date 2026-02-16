# src/ai/assistant/open_ui.py
from __future__ import annotations

from typing import Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QTableView, QPushButton
from ...quote_code import quote_match_key


def norm_quote_digits(v: object) -> str:
    return quote_match_key(v)


def pretty_quote_no(digits: str) -> str:
    d = norm_quote_digits(digits)
    if not d:
        return ""
    try:
        n = int(d)
        return str(n).zfill(7)
    except Exception:
        return d


def _find_best_history_table(root: QWidget) -> Optional[QTableView]:
    best_score = -1
    best_tv: Optional[QTableView] = None

    for tv in root.findChildren(QTableView):
        model = tv.model()
        if model is None:
            continue
        try:
            rows = int(model.rowCount())
            cols = int(model.columnCount())
        except Exception:
            continue
        if rows <= 0 or cols <= 0:
            continue

        # score por headers típicos
        headers = []
        try:
            for c in range(cols):
                h = model.headerData(c, Qt.Horizontal, Qt.DisplayRole)
                headers.append(str(h or ""))
        except Exception:
            headers = []

        ht = " | ".join(headers).lower()
        score = rows
        if "cliente" in ht:
            score += 200
        if "dni" in ht or "ruc" in ht or "cedula" in ht:
            score += 100
        if "n°" in ht or "nº" in ht or "n" in ht or "número" in ht or "numero" in ht:
            score += 60

        if score > best_score:
            best_score = score
            best_tv = tv

    return best_tv


def _find_button(root: QWidget, contains_text: str) -> Optional[QPushButton]:
    key = (contains_text or "").strip().lower()
    for b in root.findChildren(QPushButton):
        txt = (b.text() or "").strip().lower()
        if key in txt:
            return b
    return None


def _find_col_by_header(model, keywords: list[str]) -> int:
    try:
        cols = int(model.columnCount())
    except Exception:
        return -1

    headers = []
    for c in range(cols):
        h = model.headerData(c, Qt.Horizontal, Qt.DisplayRole)
        headers.append(str(h or "").lower())

    for kw in keywords:
        for i, h in enumerate(headers):
            if kw in h:
                return i
    return -1


def _select_row_by_quote_no(tv: QTableView, quote_no_digits: str) -> bool:
    model = tv.model()
    if model is None:
        return False

    target = norm_quote_digits(quote_no_digits)
    if not target:
        return False

    cols = int(model.columnCount())
    rows = int(model.rowCount())

    col_no = _find_col_by_header(model, ["n°", "nº", "numero", "número", "n"])
    search_all = (col_no < 0)

    for r in range(rows):
        if not search_all:
            idx = model.index(r, col_no)
            val = model.data(idx, Qt.DisplayRole)
            if norm_quote_digits(val) == target:
                tv.selectRow(r)
                tv.scrollTo(idx, QTableView.PositionAtCenter)
                return True
        else:
            for c in range(cols):
                idx = model.index(r, c)
                val = model.data(idx, Qt.DisplayRole)
                if norm_quote_digits(val) == target:
                    tv.selectRow(r)
                    tv.scrollTo(idx, QTableView.PositionAtCenter)
                    return True

    return False


def open_quote_or_pdf_via_ui(window: QWidget, quote_no_digits: str, target: str) -> Tuple[bool, str]:
    """
    target: "quote" | "pdf"
    """
    q = norm_quote_digits(quote_no_digits)
    if not q:
        return False, "No entendí el número."

    tv = _find_best_history_table(window)
    if tv is None:
        return False, "No encontré la tabla del histórico."

    if not _select_row_by_quote_no(tv, q):
        return False, f"No encontré la cotización #{pretty_quote_no(q)} en el histórico."

    if target == "pdf":
        btn = _find_button(window, "abrir pdf")
        if btn is None:
            return False, "No encontré el botón 'Abrir PDF'."
        btn.click()
        return True, f"Abrí el PDF de la cotización #{pretty_quote_no(q)}."

    btn = _find_button(window, "abrir cotización") or _find_button(window, "abrir cotizacion")
    if btn is None:
        return False, "No encontré el botón 'Abrir cotización'."
    btn.click()
    return True, f"Abrí la cotización #{pretty_quote_no(q)}."
