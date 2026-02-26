# src/ai/smart_completer.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from PySide6.QtCore import (
    Qt,
    QAbstractListModel,
    QModelIndex,
    QTimer,
    QObject,
    Signal,
    QRunnable,
    QThreadPool,
    QEvent,
)
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QFrame, QListView, QVBoxLayout, QLineEdit, QApplication

from .search_index import LocalSearchIndex


@dataclass
class SuggestItem:
    title: str
    subtitle: str
    payload: Dict[str, Any]


class _SuggestModel(QAbstractListModel):
    def __init__(self):
        super().__init__()
        self.items: List[SuggestItem] = []

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self.items)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        it = self.items[index.row()]
        if role == Qt.DisplayRole:
            return it.title
        if role == Qt.ToolTipRole:
            return it.subtitle
        return None

    def set_items(self, items: List[SuggestItem]):
        self.beginResetModel()
        self.items = items or []
        self.endResetModel()

    def get(self, row: int) -> Optional[SuggestItem]:
        if 0 <= row < len(self.items):
            return self.items[row]
        return None


class _SearchSignals(QObject):
    done = Signal(int, list)  # (req_id, rows)


class _SearchTask(QRunnable):
    def __init__(self, req_id: int, index: LocalSearchIndex, kind: str, query: str, limit: int):
        super().__init__()
        self.req_id = int(req_id)
        self.index = index
        self.kind = kind
        self.query = query
        self.limit = int(limit)
        self.signals = _SearchSignals()

    def run(self):
        try:
            if self.kind == "product":
                rows = self.index.search_products(self.query, self.limit)
            else:
                rows = self.index.search_clients(self.query, self.limit)
        except Exception:
            rows = []
        self.signals.done.emit(self.req_id, rows)


class SmartCompleter(QObject):
    picked = Signal(dict)

    def __init__(
        self,
        line: QLineEdit,
        *,
        index: LocalSearchIndex,
        kind: str,  # "product" | "client"
        parent=None,
        limit: int = 12,
        debounce_ms: int = 60,
        min_chars: int = 1,
        use_popup: bool = False,  # ✅ si quieres Qt.Popup explícito
    ):
        super().__init__(parent)
        self.line = line
        self.index = index
        self.kind = kind
        self.limit = int(limit)
        self.min_chars = max(1, int(min_chars))

        # Pool dedicado por completer para evitar backlog global de tareas viejas.
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(int(debounce_ms))
        self._timer.timeout.connect(self._do_search)

        self._req_id = 0
        self._task_refs: Dict[int, _SearchTask] = {}
        self._search_busy = False
        self._pending_query: Optional[str] = None

        # ✅ ToolTip es estable en Windows; Popup se puede usar si desactivas el auto-hide por FocusOut
        flags = (Qt.Popup | Qt.FramelessWindowHint) if bool(use_popup) else (Qt.ToolTip | Qt.FramelessWindowHint)
        self._popup = QFrame(None, flags)
        self._popup.setObjectName("SmartCompleterPopup")
        self._popup.setFocusPolicy(Qt.NoFocus)
        self._popup.setAttribute(Qt.WA_ShowWithoutActivating, True)

        lay = QVBoxLayout(self._popup)
        lay.setContentsMargins(2, 2, 2, 2)

        self._view = QListView(self._popup)
        self._view.setUniformItemSizes(True)
        self._view.setEditTriggers(QListView.NoEditTriggers)
        self._view.setFocusPolicy(Qt.NoFocus)
        lay.addWidget(self._view)

        self._model = _SuggestModel()
        self._view.setModel(self._model)

        # textChanged cubre escritura, pegar y entradas de lector/IME.
        self.line.textChanged.connect(self._on_text)

        self.line.installEventFilter(self)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

        self._view.clicked.connect(self._pick_index)

    def hide_popup(self):
        self._hide()

    def is_popup_visible(self) -> bool:
        try:
            return bool(self._popup.isVisible() and self._model.rowCount() > 0)
        except Exception:
            return False

    def pick_first(self) -> bool:
        try:
            if self._model.rowCount() <= 0:
                return False
            idx = self._model.index(0, 0)
            if not idx.isValid():
                return False
            self._view.setCurrentIndex(idx)
            self._pick_index(idx)
            return True
        except Exception:
            return False

    def eventFilter(self, obj, ev):
        et = ev.type()

        # click afuera => ocultar (igual que menu)
        if et == QEvent.MouseButtonPress and self._popup.isVisible():
            w = QApplication.widgetAt(QCursor.pos())
            if w is None:
                self._hide()
            else:
                if w is self.line:
                    return False
                if w is self._popup or self._popup.isAncestorOf(w):
                    return False
                self._hide()
                return False

        if obj is self.line:
            if et == QEvent.KeyPress:
                key = ev.key()
                if self._popup.isVisible():
                    if key in (Qt.Key_Down, Qt.Key_Up):
                        self._move_sel(1 if key == Qt.Key_Down else -1)
                        return True
                    if key in (Qt.Key_Return, Qt.Key_Enter):
                        if self.kind == "product":
                            typed_code = self._typed_code()
                            if typed_code:
                                if self._pick_by_code(typed_code):
                                    return True
                            self._pick_current()
                            return True
                        # Cliente: deja Enter al owner (UiMixin) para decidir
                        # entre seleccionar sugerencia o mover el foco.
                        return False
                    if key == Qt.Key_Escape:
                        self._hide()
                        return True

            elif et == QEvent.FocusOut:
                # ✅ FIX: con Qt.Popup, el line pierde foco al mostrar popup; NO lo ocultes en ese caso.
                if bool(self._popup.windowFlags() & Qt.Popup) and self._popup.isVisible():
                    fw = QApplication.focusWidget()
                    if fw is not None and (fw is self._popup or self._popup.isAncestorOf(fw)):
                        return False
                self._hide()

        return super().eventFilter(obj, ev)

    def _on_text(self, *_):
        if not self.line.hasFocus():
            return
        self._timer.start()

    def _start_search(self, query: str) -> None:
        self._req_id += 1
        rid = self._req_id
        self._search_busy = True

        task = _SearchTask(rid, self.index, self.kind, query, self.limit)
        self._task_refs[rid] = task
        task.signals.done.connect(self._on_results)
        self._pool.start(task)

    def _drain_pending_query(self) -> None:
        if self._search_busy:
            return
        q = self._pending_query
        self._pending_query = None
        if q is None:
            return
        q = str(q or "").strip()
        if len(q) < self.min_chars:
            self._hide()
            return
        self._start_search(q)

    def _do_search(self):
        if not self.line.hasFocus():
            self._hide()
            return
        q = (self.line.text() or "").strip()
        if self._search_busy:
            self._pending_query = q
            return
        if len(q) < self.min_chars:
            self._hide()
            return
        self._start_search(q)

    def _on_results(self, req_id: int, rows: list):
        self._search_busy = False
        if int(req_id) != int(self._req_id):
            self._task_refs.pop(int(req_id), None)
            self._drain_pending_query()
            return

        self._task_refs.pop(int(req_id), None)

        # Si el campo ya no tiene foco, no mostrar/reabrir popup.
        if not self.line.hasFocus():
            self._hide()
            self._drain_pending_query()
            return

        items: List[SuggestItem] = []
        if self.kind == "product":
            for r in rows or []:
                codigo = str(r.get("codigo") or r.get("id") or "").strip()
                nombre = str(r.get("nombre") or "").strip()
                cat = str(r.get("categoria") or "").strip()
                gen = str(r.get("genero") or "").strip()
                ml = str(r.get("ml") or "")
                # Formato fijo solicitado: codigo - nombre - categoria - genero
                title = f"{codigo} - {nombre} - {cat} - {gen}".strip()
                subtitle = ml.strip()
                items.append(SuggestItem(title=title, subtitle=subtitle, payload=dict(r)))
        else:
            for r in rows or []:
                cli = str(r.get("cliente") or "").strip()
                doc = str(r.get("cedula") or "").strip()
                tel = str(r.get("telefono") or "").strip()
                # ✅ pedido: "nombre - documento - tlf"
                title = " - ".join([x for x in [cli, doc, tel] if x]).strip()
                subtitle = ""
                items.append(SuggestItem(title=title, subtitle=subtitle, payload=dict(r)))

        self._model.set_items(items)

        if not items:
            self._hide()
            self._drain_pending_query()
            return

        self._show_under_line()
        self._view.setCurrentIndex(self._model.index(0, 0))
        self._drain_pending_query()

    def _show_under_line(self):
        r = self.line.rect()
        gp = self.line.mapToGlobal(r.bottomLeft())
        w = max(self.line.width(), 520 if self.kind == "product" else 520)
        h = min(320, 24 * max(4, self._model.rowCount()))
        self._popup.setGeometry(gp.x(), gp.y() + 2, w, h)
        self._popup.show()

    def _hide(self):
        if self._popup.isVisible():
            self._popup.hide()

    def _move_sel(self, delta: int):
        cur = self._view.currentIndex()
        row = cur.row() if cur.isValid() else 0
        row = max(0, min(self._model.rowCount() - 1, row + int(delta)))
        self._view.setCurrentIndex(self._model.index(row, 0))

    def _payload_code(self, payload: Dict[str, Any]) -> str:
        return str(payload.get("codigo") or payload.get("id") or "").strip().upper()

    def _typed_code(self) -> str:
        text = str(self.line.text() or "").strip()
        if not text:
            return ""
        for sep in (" - ", " — ", " – ", " â€” ", " â€“ "):
            if sep in text:
                text = text.split(sep, 1)[0].strip()
                break
        return text.upper()

    def _pick_by_code(self, code_u: str) -> bool:
        if not code_u:
            return False
        for row, it in enumerate(self._model.items):
            if self._payload_code(it.payload) == code_u:
                idx = self._model.index(row, 0)
                self._view.setCurrentIndex(idx)
                self._pick_index(idx)
                return True
        return False

    def _pick_index(self, idx: QModelIndex):
        it = self._model.get(idx.row())
        if not it:
            return
        self._hide()
        self.picked.emit(it.payload)

    def _pick_current(self):
        idx = self._view.currentIndex()
        if idx.isValid():
            self._pick_index(idx)
