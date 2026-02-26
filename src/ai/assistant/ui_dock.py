# src/ai/assistant/ui_dock.py
from __future__ import annotations

import os
import math
from dataclasses import dataclass
from typing import Callable, Optional

from PySide6.QtCore import Qt, QTimer, Signal, QRect, QEvent, QSize, QPoint
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPalette
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QDockWidget, QPlainTextEdit, QToolButton,
    QDialog, QFormLayout, QComboBox, QLineEdit, QColorDialog, QMessageBox,
    QSizePolicy, QLayout, QLayoutItem
)

from sqlModels.db import connect, ensure_schema, tx
from sqlModels.settings_repo import set_setting
from ...db_path import resolve_db_path

AI_NAME = "Samuelito"

CHAT_THEME_MODE_KEY = "chat_theme_mode"
CHAT_BUBBLE_USER_BG_KEY = "chat_bubble_user_bg"
CHAT_BUBBLE_ASSIST_BG_KEY = "chat_bubble_assist_bg"
CHAT_SEND_BG_KEY = "chat_send_bg"


@dataclass
class ChatButton:
    text: str
    on_click: Callable[[], None]


def _qcolor(s: str, fallback: str) -> QColor:
    try:
        c = QColor((s or "").strip())
        if c.isValid():
            return c
    except Exception:
        pass
    return QColor(fallback)


def _is_dark_from_palette(pal: QPalette) -> bool:
    try:
        w = pal.color(QPalette.Window)
        return w.lightness() < 128
    except Exception:
        return False


def _soft_wrap_anywhere(text: str, *, max_run: int = 26) -> str:
    """
    Inserta ZERO-WIDTH SPACE para permitir wrap incluso en rutas/códigos sin espacios.
    Evita que QLabel expanda el layout => evita scroll horizontal.
    """
    s = text or ""
    out: list[str] = []
    run = 0
    for ch in s:
        if ch.isspace():
            run = 0
            out.append(ch)
            continue
        run += 1
        out.append(ch)
        if run >= max_run:
            out.append("\u200b")
            run = 0
    return "".join(out)


@dataclass
class ThemeColors:
    root_bg: str
    title_bg: str
    title_text: str
    title_btn_hover: str

    bubble_user_bg: str
    bubble_assist_bg: str
    bubble_border: str
    text_color: str
    typing_dot: str

    composer_bg: str
    composer_border: str
    input_text: str
    input_placeholder: str
    send_bg: str
    send_bg_pressed: str
    send_text: str

    fab_bg: str
    fab_text: str


def _default_theme(dark: bool) -> ThemeColors:
    if dark:
        return ThemeColors(
            root_bg="#0F141A",
            title_bg="#151B22",
            title_text="#E8EEF5",
            title_btn_hover="rgba(255,255,255,0.08)",

            bubble_user_bg="#005C4B",
            bubble_assist_bg="#1F2A35",
            bubble_border="rgba(255,255,255,0.08)",
            text_color="#E8EEF5",
            typing_dot="#C6D2DE",

            composer_bg="rgba(21,27,34,0.92)",
            composer_border="rgba(255,255,255,0.10)",
            input_text="#E8EEF5",
            input_placeholder="rgba(232,238,245,0.55)",
            send_bg="#00A884",
            send_bg_pressed="#009174",
            send_text="#FFFFFF",

            fab_bg="rgba(31,42,53,0.92)",
            fab_text="#E8EEF5",
        )
    else:
        return ThemeColors(
            root_bg="#ECE5DD",
            title_bg="#F6F6F6",
            title_text="#111111",
            title_btn_hover="rgba(0,0,0,0.07)",

            bubble_user_bg="#D9FDD3",
            bubble_assist_bg="#FFFFFF",
            bubble_border="rgba(0,0,0,0.06)",
            text_color="#111111",
            typing_dot="#6A6A6A",

            composer_bg="rgba(255,255,255,0.92)",
            composer_border="rgba(0,0,0,0.08)",
            input_text="#111111",
            input_placeholder="rgba(0,0,0,0.45)",
            send_bg="#25D366",
            send_bg_pressed="#20B85B",
            send_text="#FFFFFF",

            fab_bg="rgba(255,255,255,0.92)",
            fab_text="#111111",
        )


class FlowLayout(QLayout):
    """
    FlowLayout clásico de Qt (wrap horizontal -> múltiples líneas).
    Sirve para que los botones no causen overflow y nunca haya scroll horizontal.
    """
    def __init__(self, parent=None, margin: int = 0, spacing: int = 8):
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)

    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> Optional[QLayoutItem]:
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> Optional[QLayoutItem]:
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.sizeHint())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect: QRect, *, test_only: bool) -> int:
        x = rect.x()
        y = rect.y()
        line_h = 0

        space_x = self.spacing()
        space_y = self.spacing()

        for item in self._items:
            w = item.widget()
            if w is None:
                continue

            hint = item.sizeHint()
            next_x = x + hint.width() + space_x

            if next_x - space_x > rect.right() and line_h > 0:
                x = rect.x()
                y = y + line_h + space_y
                next_x = x + hint.width() + space_x
                line_h = 0

            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))

            x = next_x
            line_h = max(line_h, hint.height())

        return (y + line_h) - rect.y()


class ChatInput(QPlainTextEdit):
    """
    - Enter: enviar
    - Shift+Enter: nueva línea
    """
    send_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ChatInput")
        self.setPlaceholderText("Escribe… (Enter para enviar, Shift+Enter = nueva línea)")
        self.setMinimumHeight(48)
        self.setMaximumHeight(120)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            # Shift+Enter => nueva línea
            if e.modifiers() & Qt.ShiftModifier:
                super().keyPressEvent(e)
                return
            # Enter => enviar
            self.send_requested.emit()
            return
        super().keyPressEvent(e)



class _Bubble(QFrame):
    def __init__(self, role: str, text: str, buttons: Optional[list[ChatButton]] = None, parent=None):
        super().__init__(parent)
        self.setObjectName("ChatBubble")
        self.setProperty("role", role)
        self.setFrameShape(QFrame.NoFrame)

        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)

        lbl = QLabel(_soft_wrap_anywhere(text or ""))
        lbl.setObjectName("BubbleText")
        lbl.setTextFormat(Qt.PlainText)
        lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lbl.setWordWrap(True)
        lbl.setMinimumWidth(0)
        lbl.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        lay.addWidget(lbl)

        if buttons:
            # Botones con WRAP (FlowLayout) para evitar overflow horizontal
            btn_wrap = QWidget(self)
            btn_wrap.setObjectName("BubbleActionsWrap")
            btn_wrap.setMinimumWidth(0)
            btn_wrap.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

            flow = FlowLayout(btn_wrap, margin=0, spacing=8)
            btn_wrap.setLayout(flow)

            for b in buttons:
                btn = QPushButton(_soft_wrap_anywhere(b.text or "", max_run=18))
                btn.setObjectName("BubbleAction")
                btn.setMinimumWidth(0)
                # Ignored permite que el layout lo “apriete” sin forzar width por sizeHint
                btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
                btn.clicked.connect(lambda _checked=False, cb=b.on_click: cb())
                flow.addWidget(btn)

            # alineado a la derecha, como WhatsApp
            lay.addWidget(btn_wrap, 0, Qt.AlignRight)


class _TypingIndicator(QWidget):
    """3 dots bouncing estilo WhatsApp."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TypingIndicator")
        self._phase = 0.0
        self._dot_color = QColor("#666666")
        self._tm = QTimer(self)
        self._tm.setInterval(16)
        self._tm.timeout.connect(self._tick)
        self._tm.start()
        self.setFixedSize(44, 18)

    def set_dot_color(self, c: QColor):
        if c and c.isValid():
            self._dot_color = c
            self.update()

    def _tick(self):
        self._phase += 0.10
        if self._phase > 10_000:
            self._phase = 0.0
        self.update()

    def stop(self):
        try:
            self._tm.stop()
        except Exception:
            pass

    def paintEvent(self, e: QPaintEvent):
        _ = e
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        w = self.width()
        h = self.height()
        cx = w / 2.0
        cy = h / 2.0

        r = 3.6
        gap = 10.5
        xs = [cx - gap, cx, cx + gap]

        amp = 3.0
        base_alpha = 0.45

        for i, x in enumerate(xs):
            ph = self._phase + i * 0.85
            y = cy - (math.sin(ph) * amp * 0.5 + amp * 0.5)
            a = base_alpha + (0.55 * (0.5 + 0.5 * math.sin(ph)))
            c = QColor(self._dot_color)
            c.setAlphaF(max(0.0, min(1.0, a)))
            p.setBrush(c)
            p.setPen(Qt.NoPen)
            p.drawEllipse(QRect(int(x - r), int(y - r), int(2 * r), int(2 * r)))

        p.end()


class _TypingBubble(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ChatBubble")
        self.setProperty("role", "assistant")
        self.setFrameShape(QFrame.NoFrame)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(0)

        self.ind = _TypingIndicator(self)
        lay.addWidget(self.ind, 0, Qt.AlignLeft | Qt.AlignVCenter)

    def set_dot_color(self, c: QColor):
        try:
            self.ind.set_dot_color(c)
        except Exception:
            pass

    def stop(self):
        try:
            self.ind.stop()
        except Exception:
            pass


class _TitleBar(QWidget):
    minimize_requested = Signal()
    maximize_requested = Signal()
    close_requested = Signal()
    settings_requested = Signal()

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("DockTitleBar")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(8)

        self.lbl = QLabel(title or "Asistente")
        self.lbl.setObjectName("DockTitle")
        f = self.lbl.font()
        f.setBold(True)
        self.lbl.setFont(f)

        lay.addWidget(self.lbl, 1)

        self.btn_settings = QToolButton()
        self.btn_settings.setObjectName("TitleBtn")
        self.btn_settings.setText("⚙")
        self.btn_settings.setToolTip("Personalizar chat")
        self.btn_settings.clicked.connect(self.settings_requested.emit)

        self.btn_min = QToolButton()
        self.btn_min.setObjectName("TitleBtn")
        self.btn_min.setText("—")
        self.btn_min.setToolTip("Minimizar")
        self.btn_min.clicked.connect(self.minimize_requested.emit)

        self.btn_max = QToolButton()
        self.btn_max.setObjectName("TitleBtn")
        self.btn_max.setText("▢")
        self.btn_max.setToolTip("Maximizar / Restaurar")
        self.btn_max.clicked.connect(self.maximize_requested.emit)

        self.btn_close = QToolButton()
        self.btn_close.setObjectName("TitleBtn")
        self.btn_close.setText("✕")
        self.btn_close.setToolTip("Cerrar")
        self.btn_close.clicked.connect(self.close_requested.emit)

        lay.addWidget(self.btn_settings)
        lay.addWidget(self.btn_min)
        lay.addWidget(self.btn_max)
        lay.addWidget(self.btn_close)


class ChatAppearanceDialog(QDialog):
    """Dialog de personalización. Guarda en SQLite local."""
    def __init__(self, dock: "AssistantDock", parent=None):
        super().__init__(parent)
        self.dock = dock
        self.setWindowTitle("Personalizar chat")
        self.resize(420, 220)

        lay = QVBoxLayout(self)
        form = QFormLayout()
        lay.addLayout(form)

        self.cmb_theme = QComboBox()
        self.cmb_theme.addItems(["auto", "light", "dark"])

        self.ed_user = QLineEdit()
        self.ed_assist = QLineEdit()
        self.ed_send = QLineEdit()

        def add_color_row(label: str, ed: QLineEdit):
            row = QHBoxLayout()
            btn = QPushButton("Elegir…")
            btn.setMaximumWidth(90)

            def pick():
                cur = QColor(ed.text().strip()) if ed.text().strip() else QColor()
                c = QColorDialog()
                if cur.isValid():
                    c.setCurrentColor(cur)
                if c.exec():
                    picked = c.currentColor()
                    if picked.isValid():
                        ed.setText(picked.name())

            btn.clicked.connect(pick)
            row.addWidget(ed, 1)
            row.addWidget(btn)
            w = QWidget()
            w.setLayout(row)
            form.addRow(label, w)

        form.addRow("Tema", self.cmb_theme)
        add_color_row("Burbuja (Tú)", self.ed_user)
        add_color_row("Burbuja (Asistente)", self.ed_assist)
        add_color_row("Botón enviar", self.ed_send)

        btns = QHBoxLayout()
        btn_reset = QPushButton("Restaurar")
        btn_save = QPushButton("Guardar")
        btn_close = QPushButton("Cerrar")

        btns.addWidget(btn_reset)
        btns.addStretch(1)
        btns.addWidget(btn_save)
        btns.addWidget(btn_close)
        lay.addLayout(btns)

        btn_close.clicked.connect(self.reject)
        btn_save.clicked.connect(self._save)
        btn_reset.clicked.connect(self._reset)

        prefs = self.dock.get_preferences()
        self.cmb_theme.setCurrentText(prefs.get("theme_mode", "auto") or "auto")
        self.ed_user.setText(prefs.get("bubble_user_bg", "") or "")
        self.ed_assist.setText(prefs.get("bubble_assist_bg", "") or "")
        self.ed_send.setText(prefs.get("send_bg", "") or "")

    def _reset(self):
        self.dock.reset_preferences_to_default()
        QMessageBox.information(self, "OK", "Personalización restaurada.")
        self.accept()

    def _save(self):
        theme = self.cmb_theme.currentText().strip().lower() or "auto"
        user_bg = self.ed_user.text().strip()
        assist_bg = self.ed_assist.text().strip()
        send_bg = self.ed_send.text().strip()

        self.dock.set_theme_mode(theme)
        if user_bg or assist_bg:
            self.dock.set_bubble_colors(user_bg=user_bg or None, assist_bg=assist_bg or None)
        if send_bg:
            self.dock.set_send_color(send_bg)

        self.dock.save_preferences()
        QMessageBox.information(self, "OK", "Personalización guardada en este dispositivo.")
        self.accept()


class AssistantDock(QDockWidget):
    send_text = Signal(str)
    minimize_requested = Signal()
    maximize_requested = Signal()
    close_requested = Signal()

    def __init__(self, parent=None, *, assistant_name: str = AI_NAME):
        super().__init__("", parent)
        self.setObjectName("AssistantDock")
        self.assistant_name = assistant_name or AI_NAME

        # Theme
        self._theme_mode = "auto"
        self._theme_dark = False
        self._theme_overrides: dict[str, str] = {}

        # Scroll behavior (WhatsApp-like)
        self._near_bottom_threshold = 72
        self._at_bottom = True
        self._stick_to_bottom = True
        self._autoscroll_pending = False
        self._autoscroll_tries = 0

        self.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea | Qt.BottomDockWidgetArea)

        self._titlebar = _TitleBar(self.assistant_name, self)
        self._titlebar.minimize_requested.connect(self.minimize_requested.emit)
        self._titlebar.maximize_requested.connect(self.maximize_requested.emit)
        self._titlebar.close_requested.connect(self.close_requested.emit)
        self._titlebar.settings_requested.connect(self.open_personalization_dialog)
        self.setTitleBarWidget(self._titlebar)

        root = QWidget(self)
        root.setObjectName("AssistantRoot")
        self.setWidget(root)

        main = QVBoxLayout(root)
        main.setContentsMargins(10, 10, 10, 10)
        main.setSpacing(10)

        # Scroll messages
        self.scroll = QScrollArea()
        self.scroll.setObjectName("ChatScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)

        # ✅ NO horizontal scroll nunca
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        # Para reaccionar a cambios de tamaño del viewport
        try:
            self.scroll.viewport().installEventFilter(self)
        except Exception:
            pass

        self._scroll_body = QWidget()
        self._scroll_body.setObjectName("ChatBody")
        self._scroll_body.setMinimumWidth(0)
        self._scroll_body.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

        self._v = QVBoxLayout(self._scroll_body)
        self._v.setContentsMargins(6, 6, 6, 6)
        self._v.setSpacing(10)
        self._v.addStretch(1)

        self.scroll.setWidget(self._scroll_body)
        main.addWidget(self.scroll, 1)

        # Composer
        composer = QFrame()
        composer.setObjectName("Composer")
        hb = QHBoxLayout(composer)
        hb.setContentsMargins(10, 8, 10, 8)
        hb.setSpacing(10)

        self.input = ChatInput()
        self.input.setMinimumWidth(0)
        self.input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.btn_send = QToolButton()
        self.btn_send.setObjectName("SendBtn")
        self.btn_send.setText("➤")
        self.btn_send.setToolTip("Enviar (Enter)")
        self.btn_send.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        hb.addWidget(self.input, 1)
        hb.addWidget(self.btn_send)
        main.addWidget(composer)

        self.btn_send.clicked.connect(self._emit_send)
        self.input.send_requested.connect(self._emit_send)

        # Typing row
        self._typing_row: Optional[QWidget] = None
        self._typing_bubble: Optional[_TypingBubble] = None

        # Floating scroll-to-bottom
        self.btn_to_bottom = QToolButton(root)
        self.btn_to_bottom.setObjectName("ToBottomBtn")
        self.btn_to_bottom.setText("↓")
        self.btn_to_bottom.setToolTip("Bajar al último mensaje")
        self.btn_to_bottom.clicked.connect(self.scroll_to_bottom_force)
        self.btn_to_bottom.hide()

        # Track scroll position
        try:
            bar = self.scroll.verticalScrollBar()
            bar.valueChanged.connect(self._on_scroll_changed)
            bar.rangeChanged.connect(self._on_scroll_range_changed)
        except Exception:
            pass

        self._load_env_overrides()
        self.load_preferences()
        self.apply_theme()

        # Ajuste inicial responsivo
        QTimer.singleShot(0, self._update_responsive_widths)

    # ---------- EventFilter: resize viewport (recalcula widths) ----------
    def eventFilter(self, obj, ev):
        if obj is getattr(self.scroll, "viewport", lambda: None)():
            if ev.type() == QEvent.Resize:
                self._update_responsive_widths()
                self._reposition_to_bottom_btn()
        return super().eventFilter(obj, ev)

    # ---------- Responsive widths ----------
    def _bubble_max_width(self) -> int:
        vp = self.scroll.viewport()
        if vp is None:
            return 480
        # ancho usable del viewport menos márgenes/padding
        w = int(vp.width())
        w = max(220, w - 24)
        return w

    def _update_responsive_widths(self):
        bw = self._bubble_max_width()

        # Globitos
        for bub in self._scroll_body.findChildren(QFrame):
            if bub.objectName() == "ChatBubble":
                try:
                    bub.setMinimumWidth(0)
                    bub.setMaximumWidth(bw)
                except Exception:
                    pass

        # Botones (para que nunca “empujen” a overflow)
        for btn in self._scroll_body.findChildren(QPushButton):
            if btn.objectName() == "BubbleAction":
                try:
                    btn.setMinimumWidth(0)
                    btn.setMaximumWidth(max(120, bw - 24))
                    btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
                except Exception:
                    pass

    # ---------- SQLite settings ----------
    def _db_conn(self):
        con = connect(resolve_db_path())
        ensure_schema(con)
        return con

    def _read_chat_prefs_from_db_raw(self) -> tuple[dict[str, str], dict[str, bool]]:
        out = {
            "theme_mode": "auto",
            "bubble_user_bg": "",
            "bubble_assist_bg": "",
            "send_bg": "",
        }
        exists = {k: False for k in out.keys()}

        con = None
        try:
            con = self._db_conn()

            def _read_key(db_key: str) -> tuple[str, bool]:
                row = con.execute("SELECT value FROM settings WHERE key = ?", (db_key,)).fetchone()
                if row is None:
                    return "", False
                val = row["value"]
                return ("" if val is None else str(val).strip(), True)

            mode, mode_exists = _read_key(CHAT_THEME_MODE_KEY)
            user_bg, user_exists = _read_key(CHAT_BUBBLE_USER_BG_KEY)
            assist_bg, assist_exists = _read_key(CHAT_BUBBLE_ASSIST_BG_KEY)
            send_bg, send_exists = _read_key(CHAT_SEND_BG_KEY)

            out["theme_mode"] = mode or "auto"
            out["bubble_user_bg"] = user_bg
            out["bubble_assist_bg"] = assist_bg
            out["send_bg"] = send_bg

            exists["theme_mode"] = mode_exists
            exists["bubble_user_bg"] = user_exists
            exists["bubble_assist_bg"] = assist_exists
            exists["send_bg"] = send_exists
        except Exception:
            pass
        finally:
            if con is not None:
                try:
                    con.close()
                except Exception:
                    pass

        return out, exists

    def _read_chat_prefs_from_qsettings_legacy(self) -> dict[str, str]:
        try:
            from PySide6.QtCore import QSettings
        except Exception:
            return {}

        s = QSettings("SistemaCotizaciones", "AssistantChat")
        return {
            "theme_mode": str(s.value("theme_mode", "auto") or "auto").strip(),
            "bubble_user_bg": str(s.value("bubble_user_bg", "") or "").strip(),
            "bubble_assist_bg": str(s.value("bubble_assist_bg", "") or "").strip(),
            "send_bg": str(s.value("send_bg", "") or "").strip(),
        }

    def _save_chat_prefs_to_db(self, prefs: dict[str, str]) -> None:
        con = None
        try:
            con = self._db_conn()
            with tx(con):
                set_setting(con, CHAT_THEME_MODE_KEY, str(prefs.get("theme_mode", "auto") or "auto").strip().lower())
                set_setting(con, CHAT_BUBBLE_USER_BG_KEY, str(prefs.get("bubble_user_bg", "") or "").strip())
                set_setting(con, CHAT_BUBBLE_ASSIST_BG_KEY, str(prefs.get("bubble_assist_bg", "") or "").strip())
                set_setting(con, CHAT_SEND_BG_KEY, str(prefs.get("send_bg", "") or "").strip())
        finally:
            if con is not None:
                try:
                    con.close()
                except Exception:
                    pass

    def load_preferences(self):
        db_vals, exists = self._read_chat_prefs_from_db_raw()
        legacy = self._read_chat_prefs_from_qsettings_legacy()

        merged = {
            "theme_mode": db_vals.get("theme_mode", "auto"),
            "bubble_user_bg": db_vals.get("bubble_user_bg", ""),
            "bubble_assist_bg": db_vals.get("bubble_assist_bg", ""),
            "send_bg": db_vals.get("send_bg", ""),
        }

        migrated = False
        for k in ("theme_mode", "bubble_user_bg", "bubble_assist_bg", "send_bg"):
            if not exists.get(k, False):
                lv = str(legacy.get(k, "") or "").strip()
                if lv:
                    merged[k] = lv
                    migrated = True

        mode = str(merged.get("theme_mode", "auto") or "auto").strip().lower()
        if mode not in ("auto", "light", "dark"):
            mode = "auto"
        self._theme_mode = mode
        merged["theme_mode"] = mode

        user_bg = str(merged.get("bubble_user_bg", "") or "").strip()
        assist_bg = str(merged.get("bubble_assist_bg", "") or "").strip()
        send_bg = str(merged.get("send_bg", "") or "").strip()

        if user_bg:
            self._theme_overrides["bubble_user_bg"] = user_bg
        if assist_bg:
            self._theme_overrides["bubble_assist_bg"] = assist_bg
        if send_bg:
            self._theme_overrides["send_bg"] = send_bg

        if migrated:
            try:
                self._save_chat_prefs_to_db(merged)
            except Exception:
                pass

    def save_preferences(self):
        prefs = {
            "theme_mode": self._theme_mode,
            "bubble_user_bg": self._theme_overrides.get("bubble_user_bg", ""),
            "bubble_assist_bg": self._theme_overrides.get("bubble_assist_bg", ""),
            "send_bg": self._theme_overrides.get("send_bg", ""),
        }
        try:
            self._save_chat_prefs_to_db(prefs)
        except Exception:
            pass

    def get_preferences(self) -> dict:
        return {
            "theme_mode": self._theme_mode,
            "bubble_user_bg": self._theme_overrides.get("bubble_user_bg", ""),
            "bubble_assist_bg": self._theme_overrides.get("bubble_assist_bg", ""),
            "send_bg": self._theme_overrides.get("send_bg", ""),
        }

    def reset_preferences_to_default(self):
        self._theme_mode = "auto"
        self._theme_overrides = {}
        self.save_preferences()
        self.apply_theme()

    # ---------- Theme ----------
    def _load_env_overrides(self):
        env_map = {
            "bubble_user_bg": os.environ.get("COTI_CHAT_BUBBLE_USER_BG"),
            "bubble_assist_bg": os.environ.get("COTI_CHAT_BUBBLE_ASSIST_BG"),
            "send_bg": os.environ.get("COTI_CHAT_SEND_BG"),
        }
        for k, v in env_map.items():
            if v and str(v).strip():
                self._theme_overrides[k] = str(v).strip()

    def set_theme_mode(self, mode: str):
        m = (mode or "").strip().lower()
        if m not in ("auto", "light", "dark"):
            m = "auto"
        self._theme_mode = m
        self.apply_theme()

    def set_bubble_colors(self, *, user_bg: Optional[str] = None, assist_bg: Optional[str] = None):
        if user_bg:
            self._theme_overrides["bubble_user_bg"] = user_bg
        if assist_bg:
            self._theme_overrides["bubble_assist_bg"] = assist_bg
        self.apply_theme()

    def set_send_color(self, send_bg: Optional[str] = None):
        if send_bg:
            self._theme_overrides["send_bg"] = send_bg
        self.apply_theme()

    def _compute_theme(self) -> ThemeColors:
        if self._theme_mode == "dark":
            dark = True
        elif self._theme_mode == "light":
            dark = False
        else:
            dark = _is_dark_from_palette(self.palette())

        self._theme_dark = dark
        t = _default_theme(dark)

        for k, v in (self._theme_overrides or {}).items():
            if hasattr(t, k) and v:
                setattr(t, k, v)
        return t

    def apply_theme(self):
        t = self._compute_theme()

        try:
            dot_c = _qcolor(t.typing_dot, "#666666")
            if self._typing_bubble is not None:
                self._typing_bubble.set_dot_color(dot_c)
        except Exception:
            pass

        self.setStyleSheet(
            f"""
            QWidget#AssistantRoot {{
                background: {t.root_bg};
            }}

            QScrollArea#ChatScroll {{
                border: none;
                background: transparent;
            }}
            QWidget#ChatBody {{
                background: transparent;
            }}

            QFrame#ChatBubble[role="assistant"] {{
                background: {t.bubble_assist_bg};
                border-radius: 14px;
                border: 1px solid {t.bubble_border};
            }}
            QFrame#ChatBubble[role="user"] {{
                background: {t.bubble_user_bg};
                border-radius: 14px;
                border: 1px solid {t.bubble_border};
            }}

            QLabel#BubbleText {{
                color: {t.text_color};
                font-size: 12px;
            }}

            QFrame#Composer {{
                background: {t.composer_bg};
                border-radius: 18px;
                border: 1px solid {t.composer_border};
            }}
            QPlainTextEdit#ChatInput {{
                border: none;
                background: transparent;
                padding: 6px 8px;
                font-size: 12px;
                color: {t.input_text};
            }}
            QPlainTextEdit#ChatInput::placeholder {{
                color: {t.input_placeholder};
            }}

            QToolButton#SendBtn {{
                border: none;
                border-radius: 16px;
                padding: 8px 10px;
                background: {t.send_bg};
                color: {t.send_text};
                font-weight: bold;
                min-width: 34px;
            }}
            QToolButton#SendBtn:pressed {{
                background: {t.send_bg_pressed};
            }}

            QWidget#DockTitleBar {{
                background: {t.title_bg};
                border-bottom: 1px solid {t.bubble_border};
            }}
            QLabel#DockTitle {{
                color: {t.title_text};
            }}
            QToolButton#TitleBtn {{
                border: none;
                padding: 4px 8px;
                border-radius: 10px;
                background: transparent;
                color: {t.title_text};
            }}
            QToolButton#TitleBtn:hover {{
                background: {t.title_btn_hover};
            }}

            QPushButton#BubbleAction {{
                border-radius: 10px;
                padding: 6px 10px;
                background: rgba(0,0,0,0.06);
                color: {t.text_color};
            }}
            QPushButton#BubbleAction:hover {{
                background: rgba(0,0,0,0.10);
            }}

            QToolButton#ToBottomBtn {{
                border: 1px solid {t.composer_border};
                background: {t.fab_bg};
                color: {t.fab_text};
                border-radius: 16px;
                min-width: 32px;
                min-height: 32px;
                font-size: 16px;
            }}
            QToolButton#ToBottomBtn:hover {{
                background: rgba(255,255,255,0.12);
            }}
            """
        )
        self._reposition_to_bottom_btn()
        self._update_responsive_widths()

    def event(self, ev):
        if ev.type() in (QEvent.PaletteChange, QEvent.ApplicationPaletteChange):
            if self._theme_mode == "auto":
                self.apply_theme()
        return super().event(ev)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._reposition_to_bottom_btn()
        self._update_responsive_widths()

    # ---------- Personalization UI ----------
    def open_personalization_dialog(self):
        dlg = ChatAppearanceDialog(self, self)
        dlg.exec()

    # ---------- Messaging ----------
    def _emit_send(self):
        txt = (self.input.toPlainText() or "").strip()
        if not txt:
            return
        self.input.clear()
        self.send_text.emit(txt)

    def _wrap_row(self, bubble: QWidget, role: str) -> QWidget:
        row = QWidget(self._scroll_body)
        row.setObjectName("ChatRow")
        row.setMinimumWidth(0)
        row.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

        hb = QHBoxLayout(row)
        hb.setContentsMargins(0, 0, 0, 0)
        hb.setSpacing(0)

        if role == "user":
            hb.addStretch(1)
            hb.addWidget(bubble, 0, Qt.AlignRight | Qt.AlignTop)
        else:
            hb.addWidget(bubble, 0, Qt.AlignLeft | Qt.AlignTop)
            hb.addStretch(1)

        return row

    def _is_near_bottom(self) -> bool:
        try:
            bar = self.scroll.verticalScrollBar()
            return (bar.maximum() - bar.value()) <= self._near_bottom_threshold
        except Exception:
            return True

    # ---------- AUTOSCROLL robusto ----------
    def _request_follow_scroll(self):
        self._autoscroll_pending = True
        self._autoscroll_tries = 8
        QTimer.singleShot(0, self._autoscroll_step)

    def _autoscroll_step(self):
        if not self._autoscroll_pending:
            return

        try:
            bar = self.scroll.verticalScrollBar()
            bar.setValue(bar.maximum())
            at_bottom = (bar.maximum() - bar.value()) <= 1
        except Exception:
            at_bottom = True

        if at_bottom:
            self._autoscroll_pending = False
            self._autoscroll_tries = 0
            return

        self._autoscroll_tries -= 1
        if self._autoscroll_tries <= 0:
            self._autoscroll_pending = False
            return

        QTimer.singleShot(16, self._autoscroll_step)

    def _on_scroll_range_changed(self, _min: int, _max: int):
        if self._stick_to_bottom or self._autoscroll_pending:
            QTimer.singleShot(0, self._request_follow_scroll)

    # ---------- UI actions ----------
    def add_message(self, role: str, text: str, buttons: Optional[list[ChatButton]] = None):
        follow = True if role == "user" else self._is_near_bottom()

        if role == "assistant":
            self.hide_typing()

        bubble = _Bubble(role=role, text=text, buttons=buttons, parent=self._scroll_body)
        bubble.setMaximumWidth(self._bubble_max_width())
        bubble.setMinimumWidth(0)

        row = self._wrap_row(bubble, role=role)
        self._v.insertWidget(self._v.count() - 1, row)

        QTimer.singleShot(0, lambda: self._after_append(follow))

    def show_typing(self):
        follow = self._is_near_bottom()

        self.hide_typing()

        bub = _TypingBubble(parent=self._scroll_body)
        bub.setMaximumWidth(self._bubble_max_width())
        bub.setMinimumWidth(0)

        try:
            t = self._compute_theme()
            bub.set_dot_color(_qcolor(t.typing_dot, "#666666"))
        except Exception:
            pass

        row = self._wrap_row(bub, role="assistant")
        self._typing_row = row
        self._typing_bubble = bub

        self._v.insertWidget(self._v.count() - 1, row)
        QTimer.singleShot(0, lambda: self._after_append(follow))

    def hide_typing(self):
        if self._typing_bubble is not None:
            try:
                self._typing_bubble.stop()
            except Exception:
                pass

        removed = False
        if self._typing_row is not None:
            try:
                self._typing_row.setParent(None)
                self._typing_row.deleteLater()
                removed = True
            except Exception:
                pass

        self._typing_row = None
        self._typing_bubble = None

        if removed and self._stick_to_bottom:
            QTimer.singleShot(0, self._request_follow_scroll)

    def reset(self, *, welcome_text: str = ""):
        self.hide_typing()

        while self._v.count():
            item = self._v.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

        self._v.addStretch(1)

        self.btn_to_bottom.hide()
        self._at_bottom = True
        self._stick_to_bottom = True

        if welcome_text:
            self.add_message("assistant", welcome_text)

        QTimer.singleShot(0, self.scroll_to_bottom_force)

    # ---------- Scroll behavior ----------
    def _after_append(self, follow: bool):
        self._update_responsive_widths()

        if follow:
            self._stick_to_bottom = True
            self._at_bottom = True
            self.btn_to_bottom.hide()
            self._request_follow_scroll()
        else:
            self._stick_to_bottom = False
            self._at_bottom = False
            self.btn_to_bottom.show()
            self._reposition_to_bottom_btn()

    def _on_scroll_changed(self, _v):
        near = self._is_near_bottom()
        self._at_bottom = near
        self._stick_to_bottom = near
        if near:
            self.btn_to_bottom.hide()
        else:
            self.btn_to_bottom.show()
            self._reposition_to_bottom_btn()

    def scroll_to_bottom_force(self):
        self._stick_to_bottom = True
        self._at_bottom = True
        self.btn_to_bottom.hide()
        self._request_follow_scroll()

    def _reposition_to_bottom_btn(self):
        try:
            vp = self.scroll.viewport()
            if vp is None:
                return
            top_left = vp.mapTo(self.widget(), vp.rect().topLeft())
            r = vp.rect()
            margin = 14
            x = top_left.x() + r.width() - margin - self.btn_to_bottom.width()
            y = top_left.y() + r.height() - margin - self.btn_to_bottom.height()
            y -= 10
            self.btn_to_bottom.move(max(0, x), max(0, y))
        except Exception:
            pass

    # ---------- Helpers ----------
    def set_assistant_name(self, name: str):
        self.assistant_name = name or AI_NAME
        try:
            self._titlebar.lbl.setText(self.assistant_name)
        except Exception:
            pass
