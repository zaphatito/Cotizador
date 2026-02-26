from __future__ import annotations

from PySide6.QtCore import QEvent, QModelIndex, QObject, QItemSelection, QItemSelectionModel, Qt
from PySide6.QtWidgets import QAbstractItemView, QApplication, QTableView


class ExcelTableController(QObject):
    """
    Small controller that adds spreadsheet-like keyboard behavior to QTableView.
    Works with QTableWidget as well because it inherits QTableView.
    """

    def __init__(
        self,
        table: QTableView,
        *,
        allow_copy: bool = True,
        allow_paste: bool = True,
        allow_cut: bool = True,
        clear_on_delete: bool = True,
        move_on_enter: bool = True,
        move_on_tab: bool = True,
        skip_enter_preview_rows: bool = False,
    ):
        super().__init__(table)
        self._table = table
        self._allow_copy = bool(allow_copy)
        self._allow_paste = bool(allow_paste)
        self._allow_cut = bool(allow_cut)
        self._clear_on_delete = bool(clear_on_delete)
        self._move_on_enter = bool(move_on_enter)
        self._move_on_tab = bool(move_on_tab)
        self._skip_enter_preview_rows = bool(skip_enter_preview_rows)

        table.installEventFilter(self)
        if table.viewport() is not None:
            table.viewport().installEventFilter(self)

    def eventFilter(self, obj, event):
        try:
            table = self._table
            viewport = table.viewport() if table is not None else None
        except RuntimeError:
            return False
        if obj in (table, viewport) and event.type() == QEvent.KeyPress:
            if self._handle_key_press(event):
                return True
        return super().eventFilter(obj, event)

    def _handle_key_press(self, ev) -> bool:
        key = int(ev.key())
        mod = ev.modifiers()

        ctrl = bool(mod & Qt.ControlModifier)
        shift = bool(mod & Qt.ShiftModifier)

        if ctrl and key in (Qt.Key_C, Qt.Key_Insert):
            return self.copy_selection()
        if ctrl and key == Qt.Key_V:
            return self.paste_from_clipboard()
        if ctrl and key == Qt.Key_X:
            return self.cut_selection()

        if key == Qt.Key_F2:
            return self.edit_current()

        if self._clear_on_delete and key in (Qt.Key_Delete, Qt.Key_Backspace):
            return self.clear_selection()

        if self._move_on_enter and key in (Qt.Key_Return, Qt.Key_Enter):
            if self._is_editing():
                return False
            if self._skip_enter_preview_rows and self._is_current_preview_row():
                return False
            delta = -1 if shift else 1
            return self.move_vertical(delta)

        if self._move_on_tab and key in (Qt.Key_Tab, Qt.Key_Backtab):
            if self._is_editing():
                return False
            delta = -1 if (shift or key == Qt.Key_Backtab) else 1
            return self.move_horizontal(delta)

        return False

    def _is_editing(self) -> bool:
        try:
            return self._table.state() == QAbstractItemView.EditingState
        except Exception:
            return False

    def _is_current_preview_row(self) -> bool:
        idx = self._table.currentIndex()
        if not idx.isValid():
            return False
        model = self._table.model()
        if model is None or not hasattr(model, "is_preview_row"):
            return False
        try:
            return bool(model.is_preview_row(idx.row()))
        except Exception:
            return False

    def _current_or_first_index(self) -> QModelIndex:
        idx = self._table.currentIndex()
        model = self._table.model()
        if idx.isValid() or model is None:
            return idx
        try:
            if model.rowCount() > 0 and model.columnCount() > 0:
                return model.index(0, 0)
        except Exception:
            pass
        return QModelIndex()

    def _set_current(self, idx: QModelIndex) -> bool:
        if not idx.isValid():
            return False
        sm = self._table.selectionModel()
        if sm is not None:
            sm.setCurrentIndex(
                idx,
                QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Current,
            )
        else:
            self._table.setCurrentIndex(idx)
        self._table.scrollTo(idx, QAbstractItemView.PositionAtCenter)
        return True

    def move_vertical(self, delta: int) -> bool:
        model = self._table.model()
        if model is None:
            return False
        try:
            rows = int(model.rowCount())
            cols = int(model.columnCount())
        except Exception:
            return False
        if rows <= 0 or cols <= 0:
            return False

        idx = self._current_or_first_index()
        if idx.isValid():
            row, col = idx.row(), idx.column()
        else:
            row, col = 0, 0
        row = max(0, min(rows - 1, row + int(delta)))
        return self._set_current(model.index(row, col))

    def move_horizontal(self, delta: int) -> bool:
        model = self._table.model()
        if model is None:
            return False
        try:
            rows = int(model.rowCount())
            cols = int(model.columnCount())
        except Exception:
            return False
        if rows <= 0 or cols <= 0:
            return False

        idx = self._current_or_first_index()
        if idx.isValid():
            flat = int(idx.row() * cols + idx.column())
        else:
            flat = 0
        flat += int(delta)
        flat = max(0, min(rows * cols - 1, flat))
        row, col = divmod(flat, cols)
        return self._set_current(model.index(row, col))

    def edit_current(self) -> bool:
        idx = self._current_or_first_index()
        if not idx.isValid():
            return False
        if not self._is_editable(idx):
            return False
        self._table.setCurrentIndex(idx)
        self._table.edit(idx)
        return True

    def _selected_or_current(self) -> list[QModelIndex]:
        indexes = list(self._table.selectedIndexes() or [])
        if indexes:
            return indexes
        idx = self._table.currentIndex()
        return [idx] if idx.isValid() else []

    def _selected_grid(self) -> tuple[int, int, int, int, dict[tuple[int, int], QModelIndex]] | None:
        indexes = self._selected_or_current()
        if not indexes:
            return None
        by_rc = {(ix.row(), ix.column()): ix for ix in indexes if ix.isValid()}
        if not by_rc:
            return None
        rows = [r for (r, _c) in by_rc.keys()]
        cols = [c for (_r, c) in by_rc.keys()]
        return min(rows), max(rows), min(cols), max(cols), by_rc

    def copy_selection(self) -> bool:
        if not self._allow_copy:
            return False
        grid = self._selected_grid()
        if not grid:
            return False
        row_min, row_max, col_min, col_max, by_rc = grid

        model = self._table.model()
        if model is None:
            return False

        lines: list[str] = []
        for r in range(row_min, row_max + 1):
            vals: list[str] = []
            for c in range(col_min, col_max + 1):
                ix = by_rc.get((r, c))
                if ix is None:
                    vals.append("")
                    continue
                raw = model.data(ix, Qt.DisplayRole)
                if raw is None:
                    raw = model.data(ix, Qt.EditRole)
                vals.append(str(raw or ""))
            lines.append("\t".join(vals))

        text = "\n".join(lines)
        if not text:
            return False
        cb = QApplication.clipboard()
        if cb is None:
            return False
        cb.setText(text)
        return True

    def paste_from_clipboard(self) -> bool:
        if not self._allow_paste:
            return False

        cb = QApplication.clipboard()
        if cb is None:
            return False
        text = str(cb.text() or "")
        if not text.strip():
            return False

        model = self._table.model()
        if model is None:
            return False

        start = self._current_or_first_index()
        if not start.isValid():
            return False

        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if len(lines) > 0 and lines[-1] == "":
            lines = lines[:-1]
        rows_data = [ln.split("\t") for ln in lines]
        if not rows_data:
            return False

        changed = False
        max_r = start.row()
        max_c = start.column()
        for dr, row_vals in enumerate(rows_data):
            for dc, raw in enumerate(row_vals):
                r = start.row() + dr
                c = start.column() + dc
                try:
                    if r < 0 or c < 0 or r >= model.rowCount() or c >= model.columnCount():
                        continue
                except Exception:
                    continue
                idx = model.index(r, c)
                if not self._is_editable(idx):
                    continue
                if model.setData(idx, raw, Qt.EditRole):
                    changed = True
                    if r > max_r:
                        max_r = r
                    if c > max_c:
                        max_c = c

        if changed:
            self._select_range(start.row(), start.column(), max_r, max_c)
        return changed

    def cut_selection(self) -> bool:
        if not self._allow_cut:
            return False
        copied = self.copy_selection()
        cleared = self.clear_selection()
        return copied or cleared

    def clear_selection(self) -> bool:
        indexes = self._selected_or_current()
        if not indexes:
            return False
        model = self._table.model()
        if model is None:
            return False

        changed = False
        for idx in indexes:
            if not idx.isValid():
                continue
            if not self._is_editable(idx):
                continue
            if model.setData(idx, "", Qt.EditRole):
                changed = True
        return changed

    def _is_editable(self, idx: QModelIndex) -> bool:
        if not idx.isValid():
            return False
        model = self._table.model()
        if model is None:
            return False
        try:
            return bool(model.flags(idx) & Qt.ItemIsEditable)
        except Exception:
            return False

    def _select_range(self, r0: int, c0: int, r1: int, c1: int) -> None:
        model = self._table.model()
        sm = self._table.selectionModel()
        if model is None or sm is None:
            return
        top_left = model.index(min(r0, r1), min(c0, c1))
        bottom_right = model.index(max(r0, r1), max(c0, c1))
        if not top_left.isValid() or not bottom_right.isValid():
            return
        sel = QItemSelection(top_left, bottom_right)
        sm.select(sel, QItemSelectionModel.ClearAndSelect)
        sm.setCurrentIndex(top_left, QItemSelectionModel.Current)
        self._table.scrollTo(top_left, QAbstractItemView.PositionAtCenter)
