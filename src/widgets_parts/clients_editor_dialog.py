from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, QTimer, QObject, Signal, QRunnable, QThreadPool
from PySide6.QtGui import QIcon, QFont
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStyledItemDelegate,
    QTableView,
    QHeaderView,
    QVBoxLayout,
)

from sqlModels.clients_repo import (
    list_clients,
    save_client,
    upsert_client,
    delete_client,
    get_client,
    is_generic_client_row,
)
from sqlModels.db import connect, ensure_schema, tx
from sqlModels.quotes_repo import (
    document_type_rules_for_country,
    validate_document_for_type,
)

from ..config import COUNTRY_CODE
from ..db_path import resolve_db_path
from ..ai.search_index import LocalSearchIndex
from ..api.presupuesto_client import fetch_country_clients_page
from .excel_table_behavior import ExcelTableController


def center_on_screen(w) -> None:
    try:
        screen = w.screen() or QApplication.primaryScreen()
        if not screen:
            return
        geo = screen.availableGeometry()
        fg = w.frameGeometry()
        fg.moveCenter(geo.center())
        w.move(fg.topLeft())
    except Exception:
        pass


class ClientsTableModel(QAbstractTableModel):
    HEADERS = ["Nombre", "Tipo", "Documento", "Telefono", "Direccion", "Email", "Pais", "Actualizado"]
    EDITABLE_COLS = {0, 1, 2, 3, 4, 5}

    def __init__(self):
        super().__init__()
        self.rows: list[dict[str, Any]] = []
        self._read_only = False
        self._dirty_rows: set[int] = set()
        self._dirty_cells: set[tuple[int, int]] = set()
        self._dirty_font = QFont()
        self._dirty_font.setBold(True)

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal and 0 <= section < len(self.HEADERS):
            return self.HEADERS[section]
        if orientation == Qt.Vertical:
            return str(section + 1)
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        row_idx = index.row()
        row = self.rows[row_idx]
        col = index.column()

        if role in (Qt.DisplayRole, Qt.EditRole):
            if col == 0:
                return str(row.get("nombre") or "")
            if col == 1:
                return str(row.get("tipo_documento") or "")
            if col == 2:
                return str(row.get("documento") or "")
            if col == 3:
                return str(row.get("telefono") or "")
            if col == 4:
                return str(row.get("direccion") or "")
            if col == 5:
                return str(row.get("email") or "")
            if col == 6:
                return str(row.get("country_code") or "")
            if col == 7:
                return str(row.get("updated_at") or "")

        if role == Qt.TextAlignmentRole:
            if col in (1, 2, 6):
                return int(Qt.AlignCenter)
            return int(Qt.AlignVCenter | Qt.AlignLeft)
        if role == Qt.FontRole:
            if (row_idx, col) in self._dirty_cells:
                return self._dirty_font
        return None

    def flags(self, index: QModelIndex):
        if not index.isValid():
            return Qt.NoItemFlags
        base = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        row = self.rows[index.row()] if 0 <= index.row() < len(self.rows) else {}
        if (not self._read_only) and index.column() in self.EDITABLE_COLS and not is_generic_client_row(row):
            base |= Qt.ItemIsEditable
        return base

    def setData(self, index: QModelIndex, value: Any, role: int = Qt.EditRole) -> bool:
        if role != Qt.EditRole or not index.isValid():
            return False
        if index.column() not in self.EDITABLE_COLS:
            return False
        i = index.row()
        if i < 0 or i >= len(self.rows):
            return False
        if is_generic_client_row(self.rows[i]):
            return False

        col = index.column()
        key_by_col = {
            0: "nombre",
            1: "tipo_documento",
            2: "documento",
            3: "telefono",
            4: "direccion",
            5: "email",
        }
        key = key_by_col.get(col)
        if not key:
            return False

        new_value = str(value or "").strip()
        if key in ("tipo_documento", "documento"):
            new_value = new_value.upper()

        old_value = str(self.rows[i].get(key) or "")
        if old_value == new_value:
            return False

        self.rows[i][key] = new_value
        self._dirty_rows.add(i)
        self._dirty_cells.add((i, col))
        self.dataChanged.emit(index, index, [Qt.DisplayRole, Qt.EditRole, Qt.FontRole])
        return True

    def set_rows(self, rows: list[dict[str, Any]]) -> None:
        self.beginResetModel()
        self.rows = rows or []
        self._dirty_rows = set()
        self._dirty_cells = set()
        self.endResetModel()

    def append_rows(self, rows: list[dict[str, Any]]) -> None:
        chunk = list(rows or [])
        if not chunk:
            return
        start = len(self.rows)
        end = start + len(chunk) - 1
        self.beginInsertRows(QModelIndex(), start, end)
        self.rows.extend(chunk)
        self.endInsertRows()

    def row_by_index(self, index: QModelIndex) -> dict[str, Any] | None:
        if not index.isValid():
            return None
        i = index.row()
        if i < 0 or i >= len(self.rows):
            return None
        return self.rows[i]

    def row_by_client_id(self, client_id: int) -> int:
        cid = int(client_id)
        for i, row in enumerate(self.rows):
            if int(row.get("id") or 0) == cid:
                return i
        return -1

    def add_empty_row(self, *, country_code: str, default_tipo: str) -> int:
        idx = len(self.rows)
        self.beginInsertRows(QModelIndex(), idx, idx)
        self.rows.append(
            {
                "id": None,
                "country_code": str(country_code or "").strip().upper(),
                "tipo_documento": str(default_tipo or "").strip().upper(),
                "documento": "",
                "documento_norm": "",
                "nombre": "",
                "telefono": "",
                "direccion": "-",
                "email": "-",
                "source_quote_id": None,
                "source_created_at": "",
                "created_at": "",
                "updated_at": "",
                "deleted_at": None,
            }
        )
        self._dirty_rows.add(idx)
        for col in self.EDITABLE_COLS:
            self._dirty_cells.add((idx, col))
        self.endInsertRows()
        return idx

    def remove_row_at(self, row_idx: int) -> None:
        if row_idx < 0 or row_idx >= len(self.rows):
            return
        self.beginRemoveRows(QModelIndex(), row_idx, row_idx)
        self.rows.pop(row_idx)
        self._dirty_rows = {
            (i - 1 if i > row_idx else i)
            for i in self._dirty_rows
            if i != row_idx
        }
        self._dirty_cells = {
            ((r - 1) if r > row_idx else r, c)
            for (r, c) in self._dirty_cells
            if r != row_idx
        }
        self.endRemoveRows()

    def dirty_row_indices(self) -> list[int]:
        return sorted(i for i in self._dirty_rows if 0 <= i < len(self.rows))

    def clear_dirty_rows(self) -> None:
        had_dirty_cells = bool(self._dirty_cells)
        self._dirty_rows.clear()
        self._dirty_cells.clear()
        if had_dirty_cells and self.rows:
            tl = self.index(0, 0)
            br = self.index(len(self.rows) - 1, len(self.HEADERS) - 1)
            self.dataChanged.emit(tl, br, [Qt.FontRole])

    def set_read_only(self, value: bool) -> None:
        self._read_only = bool(value)
        if self.rows:
            tl = self.index(0, 0)
            br = self.index(len(self.rows) - 1, len(self.HEADERS) - 1)
            self.dataChanged.emit(tl, br, [])


class InlineTextDelegate(QStyledItemDelegate):
    def createEditor(self, parent, _option, _index):
        editor = QLineEdit(parent)
        editor.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        editor.setStyleSheet("QLineEdit { padding-top: 0px; padding-bottom: 0px; }")
        QTimer.singleShot(0, editor.selectAll)
        return editor

    def updateEditorGeometry(self, editor, option, _index):
        editor.setGeometry(option.rect.adjusted(0, 0, 0, 0))


class DocTypeDelegate(QStyledItemDelegate):
    def __init__(self, parent, doc_types: list[str]):
        super().__init__(parent)
        seen: set[str] = set()
        out: list[str] = []
        for t in doc_types or []:
            code = str(t or "").strip().upper()
            if not code or code in seen:
                continue
            seen.add(code)
            out.append(code)
        self._doc_types = out

    def createEditor(self, parent, _option, _index):
        cb = QComboBox(parent)
        cb.setEditable(not bool(self._doc_types))
        for code in self._doc_types:
            cb.addItem(code, code)
        return cb

    def setEditorData(self, editor, index):
        if not isinstance(editor, QComboBox):
            return
        value = str(index.model().data(index, Qt.EditRole) or "").strip().upper()
        if editor.count() <= 0 and editor.isEditable():
            editor.setEditText(value)
            return
        i = editor.findData(value)
        if i < 0:
            i = editor.findText(value, Qt.MatchFixedString)
        if i >= 0:
            editor.setCurrentIndex(i)
        elif editor.count() > 0:
            editor.setCurrentIndex(0)

    def setModelData(self, editor, model, index):
        if not isinstance(editor, QComboBox):
            return
        raw = editor.currentData()
        if raw is None or str(raw or "").strip() == "":
            raw = editor.currentText()
        model.setData(index, str(raw or "").strip().upper(), Qt.EditRole)


class _ClientsLoadSignals(QObject):
    done = Signal(int, int, bool, list, bool, str)


class _ClientsLoadTask(QRunnable):
    def __init__(
        self,
        *,
        session_id: int,
        mode: str,
        country_code: str,
        search_text: str,
        limit: int,
        offset: int,
        db_path: str,
    ):
        super().__init__()
        self.session_id = int(session_id)
        self.mode = str(mode or "").strip().lower()
        self.country_code = str(country_code or "").strip().upper()
        self.search_text = str(search_text or "").strip()
        self.limit = max(1, int(limit))
        self.offset = max(0, int(offset))
        self.db_path = str(db_path or "")
        self.signals = _ClientsLoadSignals()

    def run(self):
        rows: list[dict[str, Any]] = []
        has_more = False
        error = ""
        try:
            if self.mode == ClientsEditorDialog.SOURCE_COUNTRY:
                page = fetch_country_clients_page(
                    search_text=self.search_text,
                    limit=self.limit,
                    offset=self.offset,
                )
                rows = list(page.get("rows") or [])
                has_more = bool(page.get("has_more"))
            else:
                fetch_limit = self.limit + 1
                con = connect(self.db_path)
                ensure_schema(con)
                try:
                    fetched = list_clients(
                        con,
                        country_code=self.country_code,
                        search_text=self.search_text,
                        limit=fetch_limit,
                        offset=self.offset,
                    )
                finally:
                    con.close()
                has_more = len(fetched) > self.limit
                rows = fetched[: self.limit] if has_more else fetched
        except Exception as e:
            rows = []
            has_more = False
            error = str(e or "").strip()

        self.signals.done.emit(
            self.session_id,
            self.offset,
            self.offset <= 0,
            rows,
            has_more,
            error,
        )


class ClientsEditorDialog(QDialog):
    SOURCE_LOCAL = "local"
    SOURCE_COUNTRY = "country"

    def __init__(self, parent=None, *, app_icon: QIcon | None = None, country_code: str = COUNTRY_CODE):
        super().__init__(parent)
        self.setWindowTitle("Editor de clientes")
        self.resize(980, 620)
        if app_icon is not None and not app_icon.isNull():
            self.setWindowIcon(app_icon)

        self._country_code = str(country_code or "").strip().upper()
        self._current_client_id: int | None = None
        self._doc_types = self._doc_types_for_country()
        self._country_load_error = ""
        self._source_mode = self.SOURCE_LOCAL
        self._db_path = resolve_db_path()
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)
        self._load_session_id = 0
        self._load_show_errors: dict[int, bool] = {}
        self._loading = False
        self._next_offset = 0
        self._has_more = False
        self._page_size_local = 250
        self._page_size_country = 100
        self._editable_triggers = (
            QTableView.DoubleClicked | QTableView.SelectedClicked | QTableView.EditKeyPressed
        )
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(280)
        self._search_timer.timeout.connect(self._reload)

        self.model = ClientsTableModel()

        root = QVBoxLayout(self)

        top = QHBoxLayout()
        self.cmb_source = QComboBox()
        self.cmb_source.addItem("Locales", self.SOURCE_LOCAL)
        self.cmb_source.addItem("Consulta general de clientes", self.SOURCE_COUNTRY)
        self.ed_search = QLineEdit()
        self.ed_search.setPlaceholderText("Buscar por nombre, tipo, documento, telefono, direccion o email")
        self.btn_refresh = QPushButton("Recargar")
        self.btn_new = QPushButton("Nuevo")
        top.addWidget(self.cmb_source, 0)
        top.addWidget(self.ed_search, 1)
        top.addWidget(self.btn_refresh, 0)
        top.addWidget(self.btn_new, 0)
        root.addLayout(top)

        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QTableView.SelectItems)
        self.table.setSelectionMode(QTableView.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(True)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setSortingEnabled(False)
        self.table.setEditTriggers(self._editable_triggers)
        self.inline_text_delegate = InlineTextDelegate(self.table)
        self.table.setItemDelegateForColumn(0, self.inline_text_delegate)
        self.table.setItemDelegateForColumn(2, self.inline_text_delegate)
        self.table.setItemDelegateForColumn(3, self.inline_text_delegate)
        self.table.setItemDelegateForColumn(4, self.inline_text_delegate)
        self.table.setItemDelegateForColumn(5, self.inline_text_delegate)
        self.table.setItemDelegateForColumn(1, DocTypeDelegate(self.table, self._doc_types))
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.Stretch)
        hh.setSectionResizeMode(5, QHeaderView.Stretch)
        hh.setSectionResizeMode(6, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(7, QHeaderView.ResizeToContents)
        self._excel_table = ExcelTableController(
            self.table,
            allow_copy=True,
            allow_paste=True,
            allow_cut=True,
            clear_on_delete=True,
            move_on_enter=True,
            move_on_tab=True,
            skip_enter_preview_rows=False,
        )
        root.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        self.lbl_info = QLabel("")
        self.btn_save = QPushButton("Guardar")
        self.btn_save.setProperty("variant", "primary")
        self.btn_delete = QPushButton("Eliminar")
        self.btn_close = QPushButton("Cerrar")
        bottom.addWidget(self.lbl_info, 1)
        bottom.addWidget(self.btn_save, 0)
        bottom.addWidget(self.btn_delete, 0)
        bottom.addWidget(self.btn_close, 0)
        root.addLayout(bottom)

        self.cmb_source.currentIndexChanged.connect(self._on_source_changed)
        self.ed_search.textChanged.connect(self._on_search_changed)
        self.btn_refresh.clicked.connect(lambda _checked=False: self._reload(show_errors=True))
        self.btn_new.clicked.connect(self._new_client)
        self.btn_save.clicked.connect(self._save_current)
        self.btn_delete.clicked.connect(self._delete_current)
        self.btn_close.clicked.connect(self.accept)
        self.table.selectionModel().selectionChanged.connect(self._on_selection_changed)

        self._apply_source_mode()
        self._reload()
        center_on_screen(self)

    def _open_con(self) -> Any:
        con = connect(resolve_db_path())
        ensure_schema(con)
        return con

    def _rebuild_ai_index(self) -> None:
        try:
            idx = LocalSearchIndex(resolve_db_path())
            idx.ensure_and_rebuild()
        except Exception:
            pass

    def _doc_types_for_country(self) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for rule in (document_type_rules_for_country(self._country_code) or []):
            code = str(rule.get("nombre") or "").strip().upper()
            if not code or code in seen:
                continue
            seen.add(code)
            out.append(code)
        return out

    def _current_source_mode(self) -> str:
        mode = str(self.cmb_source.currentData() or self._source_mode or self.SOURCE_LOCAL).strip().lower()
        if mode not in (self.SOURCE_LOCAL, self.SOURCE_COUNTRY):
            return self.SOURCE_LOCAL
        return mode

    def _is_country_source(self) -> bool:
        return self._current_source_mode() == self.SOURCE_COUNTRY

    def _apply_source_mode(self) -> None:
        self._source_mode = self._current_source_mode()
        is_country_source = self._is_country_source()
        self.model.set_read_only(is_country_source)
        self.table.setEditTriggers(QTableView.NoEditTriggers if is_country_source else self._editable_triggers)
        self.btn_new.setEnabled(not is_country_source)
        self.btn_save.setText("Guardar en local" if is_country_source else "Guardar")
        self.btn_save.setEnabled(not self._loading)
        self.btn_delete.setEnabled(not is_country_source)
        if is_country_source:
            self.ed_search.setPlaceholderText("Buscar clientes del servidor")
        else:
            self.ed_search.setPlaceholderText("Buscar por nombre, tipo, documento, telefono, direccion o email")

    def _on_source_changed(self, _index: int) -> None:
        self._apply_source_mode()
        self._reload(show_errors=True)

    def _on_search_changed(self, _text: str) -> None:
        self._search_timer.start()

    def _render_load_state(self) -> None:
        is_country_source = self._is_country_source()
        self.btn_refresh.setEnabled(not self._loading)
        self.btn_new.setEnabled((not is_country_source) and (not self._loading))
        self.btn_delete.setEnabled((not is_country_source) and (not self._loading))
        self.btn_save.setEnabled(not self._loading)

    def _start_async_load(self, *, reset: bool, show_errors: bool = False) -> None:
        self._loading = True
        if reset:
            self._load_session_id += 1
            self._next_offset = 0
            self._has_more = False
            self.model.set_rows([])
            self._current_client_id = None
        session_id = self._load_session_id
        self._load_show_errors[session_id] = bool(show_errors)
        offset = 0 if reset else self._next_offset
        search_text = str(self.ed_search.text() or "").strip()
        limit = self._page_size_country if self._is_country_source() else self._page_size_local

        if self._is_country_source():
            base_info = f"Consulta general de clientes: {self.model.rowCount()}"
        else:
            base_info = f"Clientes: {self.model.rowCount()}"
        self.lbl_info.setText(f"{base_info} | cargando...")
        self._render_load_state()

        task = _ClientsLoadTask(
            session_id=session_id,
            mode=self._current_source_mode(),
            country_code=self._country_code,
            search_text=search_text,
            limit=limit,
            offset=offset,
            db_path=self._db_path,
        )
        task.signals.done.connect(self._on_load_done)
        self._pool.start(task)

    def _on_load_done(
        self,
        session_id: int,
        offset: int,
        reset: bool,
        rows: list[dict[str, Any]],
        has_more: bool,
        error: str,
    ) -> None:
        if int(session_id) != int(self._load_session_id):
            return

        self._loading = False
        self._country_load_error = str(error or "").strip()
        self._has_more = bool(has_more)
        self._next_offset = max(0, int(offset)) + len(rows or [])

        show_errors = bool(self._load_show_errors.pop(int(session_id), False))
        if self._country_load_error and show_errors:
            QMessageBox.warning(
                self,
                "Consulta no disponible",
                f"No se pudieron cargar los clientes:\n{self._country_load_error}",
            )

        previous_selection = self._current_client_id
        if reset:
            self.model.set_rows(rows or [])
        else:
            self.model.append_rows(rows or [])

        loaded_rows = self.model.rowCount()
        if self._is_country_source():
            if self._country_load_error:
                self.lbl_info.setText("Consulta general de clientes: 0 | consulta no disponible")
            else:
                suffix = " | cargando más..." if self._has_more else ""
                self.lbl_info.setText(f"Consulta general de clientes: {loaded_rows}{suffix}")
        else:
            suffix = " | cargando más..." if self._has_more else ""
            self.lbl_info.setText(f"Clientes: {loaded_rows}{suffix}")

        self._render_load_state()

        if loaded_rows <= 0:
            self._current_client_id = None
            return

        if reset:
            row_idx = -1
            if previous_selection is not None:
                row_idx = self.model.row_by_client_id(int(previous_selection))
            if row_idx < 0:
                row_idx = 0

            self.table.selectRow(row_idx)
            self._set_current_from_row(row_idx)

        if (not self._country_load_error) and self._has_more:
            QTimer.singleShot(0, self._load_more)

    def _reload(self, show_errors: bool = False) -> None:
        self._start_async_load(reset=True, show_errors=show_errors)

    def _load_more(self) -> None:
        if self._loading or (not self._has_more):
            return
        self._start_async_load(reset=False, show_errors=False)

    def _selected_row_index(self) -> int:
        sm = self.table.selectionModel()
        if sm is None:
            return -1
        rows = sm.selectedRows()
        if not rows:
            idx = sm.currentIndex()
            if idx.isValid():
                return int(idx.row())
            return -1
        return int(rows[0].row())

    def _on_selection_changed(self, selected, _deselected) -> None:
        indexes = selected.indexes()
        if not indexes:
            return
        self._set_current_from_row(indexes[0].row())

    def _set_current_from_row(self, row_idx: int) -> None:
        if row_idx < 0 or row_idx >= self.model.rowCount():
            self._current_client_id = None
            return
        row = self.model.rows[row_idx]
        cid = int(row.get("id") or 0)
        self._current_client_id = cid if cid > 0 else None

    def _new_client(self) -> None:
        if self._is_country_source():
            return
        default_tipo = self._doc_types[0] if self._doc_types else ""
        row_idx = self.model.add_empty_row(country_code=self._country_code, default_tipo=default_tipo)
        self.lbl_info.setText(f"Clientes: {self.model.rowCount()}")
        self.table.selectRow(row_idx)
        self._set_current_from_row(row_idx)
        first = self.model.index(row_idx, 0)
        self.table.scrollTo(first)
        self.table.setFocus()
        self.table.edit(first)

    def _validate_row(self, row: dict[str, Any]) -> tuple[bool, str]:
        nombre = str(row.get("nombre") or "").strip()
        tipo = str(row.get("tipo_documento") or "").strip().upper()
        doc = str(row.get("documento") or "").strip()
        tel = str(row.get("telefono") or "").strip()
        country_code = str(row.get("country_code") or self._country_code or "").strip().upper()

        if not nombre:
            return False, "Nombre de cliente vacio."
        if not tipo:
            return False, "Selecciona un tipo de documento."
        ok_doc, doc_msg = validate_document_for_type(country_code, tipo, doc)
        if not ok_doc:
            return False, doc_msg or "Documento invalido."
        if not tel:
            return False, "Telefono de cliente vacio."
        return True, ""

    def _selected_rows_for_local_save(self) -> list[dict[str, Any]]:
        sm = self.table.selectionModel()
        selected: list[dict[str, Any]] = []
        seen: set[int] = set()
        if sm is not None:
            for idx in sm.selectedRows():
                row_idx = int(idx.row())
                if row_idx in seen or row_idx < 0 or row_idx >= self.model.rowCount():
                    continue
                seen.add(row_idx)
                selected.append(self.model.rows[row_idx])
        if selected:
            return selected
        if self._current_client_id is not None:
            row_idx = self.model.row_by_client_id(int(self._current_client_id))
            if 0 <= row_idx < self.model.rowCount():
                return [self.model.rows[row_idx]]
        return list(self.model.rows)

    def _save_country_results_to_local(self) -> None:
        rows = self._selected_rows_for_local_save()
        if not rows:
            QMessageBox.information(self, "Sin datos", "No hay clientes cargados para guardar localmente.")
            return

        con = self._open_con()
        saved = 0
        try:
            with tx(con):
                for row in rows:
                    upsert_client(
                        con,
                        country_code=str(row.get("country_code") or self._country_code or "").strip().upper(),
                        tipo_documento=str(row.get("tipo_documento") or "").strip().upper(),
                        documento=str(row.get("documento") or "").strip(),
                        nombre=str(row.get("nombre") or "").strip(),
                        telefono=str(row.get("telefono") or "").strip(),
                        direccion=str(row.get("direccion") or "-").strip() or "-",
                        email=str(row.get("email") or "-").strip() or "-",
                        require_valid_document=True,
                    )
                    saved += 1
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudieron guardar los clientes en local:\n{e}")
            return
        finally:
            con.close()

        self._rebuild_ai_index()
        QMessageBox.information(
            self,
            "Clientes guardados",
            f"Se guardaron o actualizaron {saved} clientes en la base local.",
        )

    def _save_current(self) -> None:
        # Fuerza commit de la celda en edición antes de leer el modelo.
        self.table.clearFocus()

        if self._is_country_source():
            self._save_country_results_to_local()
            return
        dirty_rows = self.model.dirty_row_indices()
        if not dirty_rows:
            return

        prepared: list[tuple[int, dict[str, Any]]] = []
        for row_idx in dirty_rows:
            row = self.model.rows[row_idx]
            ok, msg = self._validate_row(row)
            if not ok:
                self.table.selectRow(row_idx)
                QMessageBox.warning(self, "Datos invalidos", f"Fila {row_idx + 1}: {msg}")
                return
            prepared.append((row_idx, row))

        con = self._open_con()
        try:
            with tx(con):
                for row_idx, row in prepared:
                    cid_raw = int(row.get("id") or 0)
                    client_id = cid_raw if cid_raw > 0 else None
                    try:
                        cid = save_client(
                            con,
                            country_code=str(row.get("country_code") or self._country_code or "").strip().upper(),
                            tipo_documento=str(row.get("tipo_documento") or "").strip().upper(),
                            documento=str(row.get("documento") or "").strip(),
                            nombre=str(row.get("nombre") or "").strip(),
                            telefono=str(row.get("telefono") or "").strip(),
                            direccion=str(row.get("direccion") or "-").strip() or "-",
                            email=str(row.get("email") or "-").strip() or "-",
                            client_id=client_id,
                        )
                    except ValueError as e:
                        raise ValueError(f"Fila {row_idx + 1}: {e}") from e
                    row["id"] = int(cid)
            self.model.clear_dirty_rows()
            sel_row = self._selected_row_index()
            if 0 <= sel_row < self.model.rowCount():
                self._set_current_from_row(sel_row)
        except ValueError as e:
            QMessageBox.warning(self, "No se pudo guardar", str(e))
            return
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudieron guardar los cambios:\n{e}")
            return
        finally:
            con.close()

        self._rebuild_ai_index()
        self._reload()

    def _delete_current(self) -> None:
        if self._is_country_source():
            return
        row_idx = self._selected_row_index()
        if row_idx < 0 or row_idx >= self.model.rowCount():
            return
        row_model = self.model.rows[row_idx]
        if is_generic_client_row(row_model):
            QMessageBox.warning(self, "No permitido", "El cliente generico no se puede eliminar.")
            return
        cid = int(row_model.get("id") or 0)
        if cid <= 0:
            self.model.remove_row_at(row_idx)
            self._current_client_id = None
            self.lbl_info.setText(f"Clientes: {self.model.rowCount()}")
            return

        con = self._open_con()
        try:
            row = get_client(con, cid)
        finally:
            con.close()
        if not row:
            self._reload()
            return

        nombre = str(row.get("nombre") or "").strip() or "cliente"
        doc = str(row.get("documento") or "").strip()
        tipo = str(row.get("tipo_documento") or "").strip().upper()
        confirm = QMessageBox.question(
            self,
            "Eliminar cliente",
            (
                "Se eliminara el cliente seleccionado.\n\n"
                f"Cliente: {nombre}\n"
                f"Documento: {tipo}-{doc}\n\n"
                "Esta accion no elimina cotizaciones historicas."
            ),
        )
        if confirm != QMessageBox.Yes:
            return

        con = self._open_con()
        try:
            with tx(con):
                delete_client(con, cid)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo eliminar el cliente:\n{e}")
            return
        finally:
            con.close()

        self._current_client_id = None
        self._rebuild_ai_index()
        self._reload()
