from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from sqlModels.db import connect, ensure_schema, tx
from sqlModels.quote_statuses_repo import replace_quote_statuses, list_quote_statuses

from ..db_path import resolve_db_path
from ..app_window_parts.delegates import InlineTextDelegate
from .status_colors import (
    best_text_color_for_bg,
    get_default_status_color_hex_map,
    reload_status_colors_from_db,
)


class StatusColorsDialog(QDialog):
    def __init__(
        self,
        parent=None,
        *,
        on_colors_applied: Callable[[], None] | None = None,
    ):
        super().__init__(parent)
        self._on_colors_applied = on_colors_applied

        self.setWindowTitle("Configurar estados")
        self.setMinimumWidth(700)
        self.setMinimumHeight(430)

        self._rows: list[dict] = self._load_rows()
        self._default_colors = get_default_status_color_hex_map()

        layout = QVBoxLayout(self)

        lbl = QLabel(
            "Administra estados de cotizacion. Puedes agregar, reordenar, cambiar color y eliminar.\n"
            "Al eliminar un estado, las cotizaciones que lo usaban quedan sin estado."
        )
        lbl.setWordWrap(True)
        layout.addWidget(lbl)

        self.tbl = QTableWidget(self)
        self.tbl.setColumnCount(2)
        self.tbl.setHorizontalHeaderLabels(["Estado", "Color"])
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.verticalHeader().setDefaultSectionSize(34)
        self.tbl.setSelectionBehavior(QTableWidget.SelectRows)
        self.tbl.setSelectionMode(QTableWidget.SingleSelection)
        self.tbl.setEditTriggers(
            QAbstractItemView.DoubleClicked
            | QAbstractItemView.EditKeyPressed
            | QAbstractItemView.SelectedClicked
        )
        self.tbl.horizontalHeader().setStretchLastSection(True)
        self.tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._inline_text_delegate = InlineTextDelegate(self.tbl)
        self.tbl.setItemDelegateForColumn(0, self._inline_text_delegate)
        layout.addWidget(self.tbl, 1)

        row_tools = QHBoxLayout()
        self.btn_add = QPushButton("Agregar")
        self.btn_delete = QPushButton("Eliminar")
        self.btn_up = QPushButton("Subir")
        self.btn_down = QPushButton("Bajar")
        self.btn_pick = QPushButton("Elegir color")
        self.btn_reset_defaults = QPushButton("Restablecer predeterminados")

        self.btn_add.clicked.connect(self._add_status)
        self.btn_delete.clicked.connect(self._delete_selected)
        self.btn_up.clicked.connect(lambda: self._move_selected(-1))
        self.btn_down.clicked.connect(lambda: self._move_selected(1))
        self.btn_pick.clicked.connect(self._pick_selected_color)
        self.btn_reset_defaults.clicked.connect(self._reset_default_colors_in_table)

        row_tools.addWidget(self.btn_add)
        row_tools.addWidget(self.btn_delete)
        row_tools.addWidget(self.btn_up)
        row_tools.addWidget(self.btn_down)
        row_tools.addWidget(self.btn_pick)
        row_tools.addWidget(self.btn_reset_defaults)
        row_tools.addStretch(1)
        layout.addLayout(row_tools)

        row_actions = QHBoxLayout()
        self.btn_save = QPushButton("Guardar")
        self.btn_save.setProperty("variant", "primary")
        self.btn_close = QPushButton("Cerrar")

        self.btn_save.clicked.connect(self._save)
        self.btn_close.clicked.connect(self.accept)

        row_actions.addWidget(self.btn_save)
        row_actions.addStretch(1)
        row_actions.addWidget(self.btn_close)
        layout.addLayout(row_actions)

        self._render_table()

    def _load_rows(self) -> list[dict]:
        con = None
        try:
            con = connect(resolve_db_path())
            ensure_schema(con)
            return list_quote_statuses(con)
        except Exception:
            return []
        finally:
            if con is not None:
                try:
                    con.close()
                except Exception:
                    pass

    @staticmethod
    def _normalize_hex(value: str, fallback: str = "#5B708E") -> str:
        c = QColor(str(value or "").strip())
        if c.isValid():
            return c.name(QColor.HexRgb).upper()
        fbc = QColor(str(fallback or "#5B708E"))
        if fbc.isValid():
            return fbc.name(QColor.HexRgb).upper()
        return "#5B708E"

    def _render_table(self) -> None:
        self.tbl.blockSignals(True)
        try:
            self.tbl.setRowCount(0)
            for row in self._rows:
                r = self.tbl.rowCount()
                self.tbl.insertRow(r)

                label = str(row.get("label") or "").strip()
                code = str(row.get("code") or "").strip()
                color_hex = self._normalize_hex(str(row.get("color_hex") or ""), fallback="#5B708E")

                it_label = QTableWidgetItem(label)
                it_label.setData(Qt.UserRole, code)
                self.tbl.setItem(r, 0, it_label)

                btn = QPushButton(color_hex)
                btn.setProperty("row_index", r)
                btn.clicked.connect(self._pick_color_from_button)
                self._paint_color_button(btn, color_hex)
                self.tbl.setCellWidget(r, 1, btn)

            if self.tbl.rowCount() > 0 and self.tbl.currentRow() < 0:
                self.tbl.selectRow(0)
        finally:
            self.tbl.blockSignals(False)

    @staticmethod
    def _paint_color_button(btn: QPushButton, color_hex: str) -> None:
        c = QColor(color_hex)
        if not c.isValid():
            c = QColor("#5B708E")
        fg = best_text_color_for_bg(c)
        hx = c.name(QColor.HexRgb).upper()
        btn.setText(hx)
        btn.setStyleSheet(
            "border: 1px solid #5B708E; border-radius: 8px; padding: 4px 10px; "
            f"background-color: {hx}; color: {fg.name()};"
        )

    def _default_color_for_code(self, code: str, *, fallback: str = "#5B708E") -> str:
        code_u = str(code or "").strip().upper()
        hx = str(self._default_colors.get(code_u) or "").strip()
        if hx:
            return self._normalize_hex(hx, fallback=fallback)
        return self._normalize_hex(fallback, fallback="#5B708E")

    def _selected_row(self) -> int:
        r = int(self.tbl.currentRow())
        if r < 0 or r >= self.tbl.rowCount():
            return -1
        return r

    def _status_code_for_row(self, row_index: int) -> str:
        if row_index < 0 or row_index >= self.tbl.rowCount():
            return ""
        it = self.tbl.item(row_index, 0)
        if it is None:
            return ""
        return str(it.data(Qt.UserRole) or "").strip().upper()

    def _show_color_picker_dialog(
        self,
        *,
        current_hex: str,
        default_hex: str = "",
    ) -> str | None:
        picker = QColorDialog(QColor(current_hex), self)
        picker.setWindowTitle("Color del estado")
        picker.setOption(QColorDialog.DontUseNativeDialog, True)
        picker.setCurrentColor(QColor(current_hex))

        default_hex_norm = self._normalize_hex(default_hex, fallback=current_hex) if default_hex else ""
        btn_reset = QPushButton("Restablecer predeterminado", picker)
        btn_reset.clicked.connect(lambda: picker.setCurrentColor(QColor(default_hex_norm)))

        buttons = picker.findChild(QDialogButtonBox)
        if buttons is not None:
            buttons.addButton(btn_reset, QDialogButtonBox.ResetRole)
            for btn in buttons.buttons():
                try:
                    role = buttons.buttonRole(btn)
                except Exception:
                    role = None
                if role == QDialogButtonBox.AcceptRole:
                    btn.setProperty("variant", "primary")
        else:
            layout = picker.layout()
            if layout is not None:
                row = QHBoxLayout()
                row.addStretch(1)
                row.addWidget(btn_reset)
                layout.addLayout(row)

        if default_hex_norm:
            pass
        else:
            btn_reset.setEnabled(False)
            btn_reset.setToolTip("Este estado no tiene un color predeterminado del sistema.")

        if picker.exec() != QDialog.Accepted:
            return None

        picked = picker.currentColor()
        if not picked.isValid():
            return None
        return picked.name(QColor.HexRgb).upper()

    def _pick_color_from_button(self) -> None:
        btn = self.sender()
        if not isinstance(btn, QPushButton):
            return
        try:
            row = int(btn.property("row_index"))
        except Exception:
            row = -1
        if row < 0 or row >= self.tbl.rowCount():
            return
        self.tbl.selectRow(row)
        self._pick_selected_color()

    def _pick_selected_color(self) -> None:
        r = self._selected_row()
        if r < 0:
            return
        btn = self.tbl.cellWidget(r, 1)
        if not isinstance(btn, QPushButton):
            return
        code = self._status_code_for_row(r)
        cur_hex = self._normalize_hex(btn.text(), fallback="#5B708E")
        picked_hex = self._show_color_picker_dialog(
            current_hex=cur_hex,
            default_hex=(self._default_colors.get(code) or ""),
        )
        if not picked_hex:
            return
        self._paint_color_button(btn, picked_hex)

    def _reset_default_colors_in_table(self) -> None:
        selected_row = self._selected_row()
        changed = False
        for r in range(self.tbl.rowCount()):
            code = self._status_code_for_row(r)
            if not code or code not in self._default_colors:
                continue
            btn = self.tbl.cellWidget(r, 1)
            if not isinstance(btn, QPushButton):
                continue
            default_hex = self._default_color_for_code(code)
            if self._normalize_hex(btn.text(), fallback="#5B708E") == default_hex:
                continue
            self._paint_color_button(btn, default_hex)
            changed = True

        if selected_row >= 0 and selected_row < self.tbl.rowCount():
            self.tbl.selectRow(selected_row)

        if not changed:
            QMessageBox.information(
                self,
                "Sin cambios",
                "Los estados predeterminados ya usan sus colores originales.",
            )

    def _add_status(self) -> None:
        self._rows = self._collect_rows_from_table()
        self._rows.append({"code": "", "label": "Nuevo estado", "color_hex": "#5B708E"})
        self._render_table()
        if self.tbl.rowCount() > 0:
            self.tbl.selectRow(self.tbl.rowCount() - 1)
            it = self.tbl.item(self.tbl.rowCount() - 1, 0)
            if it is not None:
                self.tbl.editItem(it)

    def _delete_selected(self) -> None:
        r = self._selected_row()
        if r < 0:
            return
        self._rows = self._collect_rows_from_table()
        if self.tbl.rowCount() <= 1:
            QMessageBox.warning(self, "Validacion", "Debe existir al menos un estado.")
            return
        del self._rows[r]
        self._render_table()
        if self.tbl.rowCount() > 0:
            self.tbl.selectRow(min(r, self.tbl.rowCount() - 1))

    def _move_selected(self, delta: int) -> None:
        r = self._selected_row()
        if r < 0:
            return
        self._rows = self._collect_rows_from_table()
        nr = r + int(delta)
        if nr < 0 or nr >= len(self._rows):
            return
        self._rows[r], self._rows[nr] = self._rows[nr], self._rows[r]
        self._render_table()
        self.tbl.selectRow(nr)

    def _collect_rows_from_table(self) -> list[dict]:
        rows: list[dict] = []
        for r in range(self.tbl.rowCount()):
            it = self.tbl.item(r, 0)
            code = str(it.data(Qt.UserRole) if it is not None else "").strip()
            label = str(it.text() if it is not None else "").strip()
            btn = self.tbl.cellWidget(r, 1)
            color_hex = "#5B708E"
            if isinstance(btn, QPushButton):
                color_hex = self._normalize_hex(btn.text(), fallback="#5B708E")
            rows.append(
                {
                    "code": code,
                    "label": label,
                    "color_hex": color_hex,
                }
            )
        return rows

    def _save(self) -> None:
        rows = self._collect_rows_from_table()

        clean_rows: list[dict] = []
        seen_labels: set[str] = set()
        for r in rows:
            label = str(r.get("label") or "").strip()
            if not label:
                continue
            lk = label.lower()
            if lk in seen_labels:
                QMessageBox.warning(self, "Validacion", f"Estado duplicado: {label}")
                return
            seen_labels.add(lk)
            clean_rows.append(
                {
                    "code": str(r.get("code") or "").strip(),
                    "label": label,
                    "color_hex": self._normalize_hex(r.get("color_hex"), fallback="#5B708E"),
                }
            )

        if not clean_rows:
            QMessageBox.warning(self, "Validacion", "Debe existir al menos un estado.")
            return

        con = None
        try:
            con = connect(resolve_db_path())
            ensure_schema(con)
            with tx(con):
                saved = replace_quote_statuses(con, clean_rows)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudieron guardar los estados:\n{e}")
            return
        finally:
            if con is not None:
                try:
                    con.close()
                except Exception:
                    pass

        self._rows = saved
        self._render_table()

        try:
            reload_status_colors_from_db()
        except Exception:
            pass

        try:
            if callable(self._on_colors_applied):
                self._on_colors_applied()
        except Exception:
            pass

        try:
            for w in QApplication.topLevelWidgets():
                if w is self:
                    continue
                fn = getattr(w, "refresh_status_colors", None)
                if callable(fn):
                    try:
                        fn()
                    except Exception:
                        pass
        except Exception:
            pass

        QMessageBox.information(self, "Estados guardados", "Los estados fueron actualizados.")
