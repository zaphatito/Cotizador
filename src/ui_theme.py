from __future__ import annotations

import os
import sqlite3

from PySide6.QtCore import QObject, QEvent, QTimer, Qt
from PySide6.QtGui import QFont, QPalette, QFontDatabase
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox

from .db_path import resolve_db_path
from .paths import resource_path

THEME_MODE_SYSTEM = "system"
THEME_MODE_LIGHT = "light"
THEME_MODE_DARK = "dark"
THEME_SETTING_KEY = "ui_theme_mode"

_CURRENT_THEME_MODE = THEME_MODE_SYSTEM
_CURRENT_EFFECTIVE_MODE = THEME_MODE_LIGHT
_SYSTEM_THEME_HOOKED = False
_MSGBOX_FILTER: QObject | None = None


class _MessageBoxAutoSizer(QObject):
    def eventFilter(self, obj, event):
        try:
            if isinstance(obj, QMessageBox):
                et = event.type()
                if et == QEvent.Show:
                    if not bool(obj.property("_cotizador_autosize_scheduled")):
                        obj.setProperty("_cotizador_autosize_scheduled", True)
                        QTimer.singleShot(0, lambda o=obj: _autosize_message_box_once(o))
                elif et == QEvent.Hide:
                    # Si el QMessageBox se vuelve a reutilizar, permitimos autoajuste nuevamente.
                    obj.setProperty("_cotizador_autosize_done", False)
                    obj.setProperty("_cotizador_autosize_scheduled", False)
        except Exception:
            pass
        return super().eventFilter(obj, event)


def _autosize_message_box_once(box: QMessageBox) -> None:
    if box is None:
        return
    try:
        if bool(box.property("_cotizador_autosize_done")):
            return
        _autosize_message_box(box)
        box.setProperty("_cotizador_autosize_done", True)
    finally:
        try:
            box.setProperty("_cotizador_autosize_scheduled", False)
        except Exception:
            pass


def _autosize_message_box(box: QMessageBox) -> None:
    if box is None:
        return

    screen = box.screen() or QApplication.primaryScreen()
    avail_w = int(screen.availableGeometry().width()) if screen is not None else 1366

    min_w = 300
    max_w = max(min_w, min(680, int(avail_w * 0.68)))
    text_max_w = max(220, max_w - 120)

    labels: list[QLabel] = []
    for lbl in box.findChildren(QLabel):
        name = str(lbl.objectName() or "")
        if name in ("qt_msgbox_label", "qt_msgbox_informativelabel"):
            try:
                lbl.setWordWrap(True)
                lbl.setMaximumWidth(text_max_w)
                lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
                labels.append(lbl)
            except Exception:
                pass

    longest = 0
    for lbl in labels:
        txt = str(lbl.text() or "")
        for line in txt.splitlines() or [""]:
            ln = len(line.strip())
            if ln > longest:
                longest = ln

    est_w = int(220 + (min(longest, 85) * 6.4))
    est_w = max(min_w, min(max_w, est_w))

    try:
        # Avoid forcing a fixed width/height pair here: on Windows, QMessageBox may
        # already have a fixed layout height and aggressive resize attempts emit
        # QWindowsWindow::setGeometry warnings.
        size_hint = box.sizeHint()
        min_size = box.minimumSizeHint().expandedTo(box.minimumSize())
        max_size = box.maximumSize()
        lay = box.layout()
        lay_min = lay.minimumSize() if lay is not None else min_size
        lay_max = lay.maximumSize() if lay is not None else max_size

        max_w_bound = max_w
        if 0 < int(max_size.width()) < 16777215:
            max_w_bound = min(max_w_bound, int(max_size.width()))
        if 0 < int(lay_max.width()) < 16777215:
            max_w_bound = min(max_w_bound, int(lay_max.width()))

        target_w = max(min_w, min(max_w_bound, max(est_w, int(size_hint.width()))))
        target_w = max(target_w, int(min_size.width()), int(lay_min.width()))

        target_h = max(int(min_size.height()), int(size_hint.height()))
        if 0 < int(max_size.height()) < 16777215:
            target_h = min(target_h, int(max_size.height()))
        if 0 < int(lay_max.height()) < 16777215:
            target_h = min(target_h, int(lay_max.height()))
        target_h = max(target_h, int(lay_min.height()))

        if target_w > 0 and target_h > 0:
            box.resize(target_w, target_h)
    except Exception:
        pass


def _install_messagebox_auto_sizer(app: QApplication) -> None:
    global _MSGBOX_FILTER
    if app is None or _MSGBOX_FILTER is not None:
        return
    try:
        _MSGBOX_FILTER = _MessageBoxAutoSizer(app)
        app.installEventFilter(_MSGBOX_FILTER)
    except Exception:
        _MSGBOX_FILTER = None


def normalize_theme_mode(mode: str | None) -> str:
    m = str(mode or "").strip().lower()
    if m in ("light", "claro"):
        return THEME_MODE_LIGHT
    if m in ("dark", "oscuro"):
        return THEME_MODE_DARK
    return THEME_MODE_SYSTEM


def _read_theme_mode_from_settings(default: str = THEME_MODE_SYSTEM) -> str:
    try:
        db_path = resolve_db_path()
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT value FROM settings WHERE key = ?",
            (THEME_SETTING_KEY,),
        ).fetchone()
        con.close()
        if row and row["value"] is not None:
            return normalize_theme_mode(str(row["value"]))
    except Exception:
        pass

    env_mode = str(os.environ.get("UI_THEME_MODE") or "").strip()
    if env_mode:
        return normalize_theme_mode(env_mode)
    return normalize_theme_mode(default)


def load_saved_theme_mode(default: str = THEME_MODE_SYSTEM) -> str:
    return _read_theme_mode_from_settings(default=default)


def save_theme_mode(mode: str) -> str:
    norm = normalize_theme_mode(mode)
    try:
        db_path = resolve_db_path()
        con = sqlite3.connect(db_path)
        con.execute(
            "CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        con.execute(
            """
            INSERT INTO settings(key, value) VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (THEME_SETTING_KEY, norm),
        )
        con.commit()
        con.close()
    except Exception:
        pass
    return norm


def detect_system_theme_mode(app: QApplication | None = None) -> str:
    app = app or QApplication.instance()
    if app is None:
        return THEME_MODE_LIGHT

    try:
        hints = app.styleHints()
        if hasattr(hints, "colorScheme"):
            scheme = hints.colorScheme()
            if scheme == Qt.ColorScheme.Dark:
                return THEME_MODE_DARK
            if scheme == Qt.ColorScheme.Light:
                return THEME_MODE_LIGHT
    except Exception:
        pass

    try:
        c = app.palette().color(QPalette.Window)
        lum = (0.2126 * c.redF()) + (0.7152 * c.greenF()) + (0.0722 * c.blueF())
        return THEME_MODE_DARK if lum < 0.45 else THEME_MODE_LIGHT
    except Exception:
        return THEME_MODE_LIGHT


def _resolve_effective_mode(mode: str, app: QApplication | None) -> str:
    norm = normalize_theme_mode(mode)
    if norm == THEME_MODE_SYSTEM:
        return detect_system_theme_mode(app)
    return norm


def _pick_app_font_family() -> str:
    candidates = (
        "Segoe UI Variable Text",
        "Segoe UI",
        "Calibri",
        "Arial",
    )
    try:
        fams = {str(x) for x in QFontDatabase.families()}
        for fam in candidates:
            if fam in fams:
                return fam
    except Exception:
        pass
    return "Segoe UI"


def _theme_tokens(effective_mode: str) -> dict[str, str]:
    if effective_mode == THEME_MODE_DARK:
        return {
            "window_a": "#171b21",
            "window_b": "#13171c",
            "text": "#e6e9ee",
            "group_bg": "#1c2129",
            "group_border": "#2d3440",
            "group_title": "#e6e9ee",
            "group_title_bg": "#1c2129",
            "input_bg": "#151b23",
            "input_border": "#3b4452",
            "input_focus": "#6f7f95",
            "input_dis_bg": "#10151c",
            "input_dis_fg": "#7e8898",
            "btn_bg": "#252c35",
            "btn_border": "#3d4755",
            "btn_text": "#dfe4eb",
            "btn_hover": "#2d3540",
            "btn_pressed": "#202833",
            "btn_dis_bg": "#171d25",
            "btn_dis_border": "#2f3947",
            "btn_dis_fg": "#7f8a99",
            "primary_bg": "#4b5f78",
            "primary_border": "#3f5167",
            "primary_hover": "#596f8a",
            "primary_pressed": "#3b4c61",
            "danger_bg": "#975252",
            "danger_border": "#824646",
            "danger_hover": "#a05d5d",
            "danger_pressed": "#763f3f",
            "table_bg": "#181e26",
            "table_alt": "#1d232c",
            "table_border": "#303a47",
            "table_sel_bg": "#34465f",
            "table_sel_fg": "#f1f4f8",
            "table_hover": "#273445",
            "header_bg": "#222a35",
            "header_fg": "#d6dde7",
            "menu_bg": "#1c222b",
            "menu_border": "#343f4d",
            "menu_sel_bg": "#2a3443",
            "menu_sel_fg": "#eef2f8",
            "tooltip_bg": "#242e3b",
            "tooltip_fg": "#e8edf5",
            "tooltip_border": "#4c5c72",
            "progress_bg": "#273141",
            "progress_fg": "#dce3ee",
            "progress_chunk": "#4c617c",
            "scroll_bg": "#1d2531",
            "scroll_handle": "#566678",
            "scroll_hover": "#667a91",
            "tool_bg": "#232b35",
            "tool_border": "#3b4655",
            "tool_hover": "#2b3542",
            "tool_pressed": "#1f2833",
            "selection_bg": "#4e6381",
            "selection_fg": "#f4f9ff",
        }

    return {
        "window_a": "#f4f5f7",
        "window_b": "#edf0f3",
        "text": "#1e2531",
        "group_bg": "#ffffff",
        "group_border": "#d3d9e1",
        "group_title": "#202734",
        "group_title_bg": "#ffffff",
        "input_bg": "#ffffff",
        "input_border": "#c2ccd8",
        "input_focus": "#6e7f95",
        "input_dis_bg": "#f1f3f6",
        "input_dis_fg": "#8b94a3",
        "btn_bg": "#ffffff",
        "btn_border": "#c7d0dc",
        "btn_text": "#212836",
        "btn_hover": "#f3f5f8",
        "btn_pressed": "#eaedf2",
        "btn_dis_bg": "#edf1f5",
        "btn_dis_border": "#d7dde7",
        "btn_dis_fg": "#99a3b3",
        "primary_bg": "#4f627b",
        "primary_border": "#44556b",
        "primary_hover": "#5b6f88",
        "primary_pressed": "#3f5167",
        "danger_bg": "#a45c5c",
        "danger_border": "#8f5050",
        "danger_hover": "#b06767",
        "danger_pressed": "#814949",
        "table_bg": "#ffffff",
        "table_alt": "#f7f9fb",
        "table_border": "#d4dae2",
        "table_sel_bg": "#dee5ee",
        "table_sel_fg": "#222b38",
        "table_hover": "#edf2f7",
        "header_bg": "#eef1f4",
        "header_fg": "#3b4658",
        "menu_bg": "#ffffff",
        "menu_border": "#d4dae2",
        "menu_sel_bg": "#e8edf3",
        "menu_sel_fg": "#273547",
        "tooltip_bg": "#f7f8fa",
        "tooltip_fg": "#202734",
        "tooltip_border": "#b7c2d0",
        "progress_bg": "#edf1f5",
        "progress_fg": "#2b3342",
        "progress_chunk": "#4f627b",
        "scroll_bg": "#edf1f4",
        "scroll_handle": "#b4bfcc",
        "scroll_hover": "#9ba8b8",
        "tool_bg": "#ffffff",
        "tool_border": "#c7d0dc",
        "tool_hover": "#f3f5f8",
        "tool_pressed": "#e9edf2",
        "selection_bg": "#4f627b",
        "selection_fg": "#f7f9fc",
    }


def _build_stylesheet(font_family: str, effective_mode: str) -> str:
    t = _theme_tokens(effective_mode)
    font_decl = f'font-family: "{font_family}", "Arial", sans-serif;' if font_family else ""
    combo_arrow_name = (
        "combo_chevron_dark.png"
        if effective_mode == THEME_MODE_DARK
        else "combo_chevron_light.png"
    )
    combo_arrow_path = resource_path(os.path.join("templates", "icons", combo_arrow_name))
    combo_arrow_path = combo_arrow_path.replace("\\", "/")

    return f"""
QWidget {{
    {font_decl}
    color: {t["text"]};
    background-color: {t["window_b"]};
    font-size: 10pt;
}}

QMainWindow, QDialog {{
    background-color: qlineargradient(
        x1: 0, y1: 0, x2: 0, y2: 1,
        stop: 0 {t["window_a"]},
        stop: 1 {t["window_b"]}
    );
}}

QLabel {{
    background: none;
    background-color: transparent;
    border: none;
}}

QGroupBox {{
    border: 1px solid {t["group_border"]};
    border-radius: 11px;
    margin-top: 11px;
    padding: 9px;
    background-color: {t["group_bg"]};
    font-weight: 600;
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 6px;
    color: {t["group_title"]};
    background: transparent;
}}

QLineEdit,
QComboBox,
QSpinBox,
QDoubleSpinBox,
QTextEdit,
QPlainTextEdit {{
    background-color: {t["input_bg"]};
    border: 1px solid {t["input_border"]};
    border-radius: 9px;
    padding: 6px 9px;
    selection-background-color: {t["selection_bg"]};
    selection-color: {t["selection_fg"]};
}}

QComboBox {{
    padding-right: 30px;
}}

QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 22px;
    margin: 1px 1px 1px 0px;
    border: none;
    border-left: 1px solid {t["input_border"]};
    border-top-right-radius: 8px;
    border-bottom-right-radius: 8px;
    background: transparent;
}}

QComboBox::down-arrow {{
    image: url("{combo_arrow_path}");
    width: 10px;
    height: 6px;
}}

QLineEdit:focus,
QComboBox:focus,
QSpinBox:focus,
QDoubleSpinBox:focus,
QTextEdit:focus,
QPlainTextEdit:focus {{
    border: 1px solid {t["input_focus"]};
}}

QLineEdit:disabled,
QComboBox:disabled,
QSpinBox:disabled,
QDoubleSpinBox:disabled,
QTextEdit:disabled,
QPlainTextEdit:disabled {{
    background-color: {t["input_dis_bg"]};
    color: {t["input_dis_fg"]};
}}

QPushButton {{
    background-color: {t["btn_bg"]};
    border: 1px solid {t["btn_border"]};
    border-radius: 10px;
    padding: 6px 14px;
    color: {t["btn_text"]};
    font-weight: 600;
    min-height: 16px;
}}

QPushButton:hover {{
    background-color: {t["btn_hover"]};
}}

QPushButton:pressed {{
    background-color: {t["btn_pressed"]};
}}

QPushButton:disabled {{
    background-color: {t["btn_dis_bg"]};
    border-color: {t["btn_dis_border"]};
    color: {t["btn_dis_fg"]};
}}

QPushButton[role="payment_toggle"]:checked {{
    background-color: {t["primary_bg"]};
    border-color: {t["primary_border"]};
    color: #ffffff;
}}

QPushButton[role="payment_toggle"]:checked:hover {{
    background-color: {t["primary_hover"]};
}}

QPushButton[role="payment_toggle"]:checked:pressed {{
    background-color: {t["primary_pressed"]};
}}

QPushButton[variant="primary"] {{
    background-color: {t["primary_bg"]};
    border-color: {t["primary_border"]};
    color: #ffffff;
}}

QPushButton[variant="primary"]:hover {{
    background-color: {t["primary_hover"]};
}}

QPushButton[variant="primary"]:pressed {{
    background-color: {t["primary_pressed"]};
}}

QPushButton[variant="danger"] {{
    background-color: {t["danger_bg"]};
    border-color: {t["danger_border"]};
    color: #ffffff;
}}

QPushButton[variant="danger"]:hover {{
    background-color: {t["danger_hover"]};
}}

QPushButton[variant="danger"]:pressed {{
    background-color: {t["danger_pressed"]};
}}

QToolButton {{
    background-color: {t["tool_bg"]};
    border: 1px solid {t["tool_border"]};
    border-radius: 8px;
    padding: 4px;
    color: {t["btn_text"]};
}}

QToolButton:hover {{
    background-color: {t["tool_hover"]};
}}

QToolButton:pressed {{
    background-color: {t["tool_pressed"]};
}}

QCheckBox,
QRadioButton {{
    spacing: 8px;
    background: none;
    background-color: transparent;
    border: none;
}}

QTableView,
QTableWidget {{
    background-color: {t["table_bg"]};
    alternate-background-color: {t["table_alt"]};
    border: 1px solid {t["table_border"]};
    border-radius: 11px;
    gridline-color: transparent;
    selection-background-color: {t["table_sel_bg"]};
    selection-color: {t["table_sel_fg"]};
}}

QTableView::item:hover,
QTableWidget::item:hover {{
    background-color: {t["table_hover"]};
}}

QTableCornerButton::section {{
    background-color: {t["header_bg"]};
    border: none;
    border-right: 1px solid {t["table_border"]};
    border-bottom: 1px solid {t["table_border"]};
}}

QTableView::item,
QTableWidget::item {{
    padding: 4px;
}}

QHeaderView::section {{
    background-color: {t["header_bg"]};
    color: {t["header_fg"]};
    border: none;
    border-bottom: 1px solid {t["table_border"]};
    border-right: 1px solid {t["table_border"]};
    padding: 7px 8px;
    font-weight: 700;
}}

QTabWidget::pane {{
    border: 1px solid {t["table_border"]};
    border-radius: 10px;
    background-color: {t["group_bg"]};
    top: -1px;
}}

QTabBar::tab {{
    background-color: {t["header_bg"]};
    color: {t["header_fg"]};
    border: 1px solid {t["table_border"]};
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    padding: 6px 12px;
    margin-right: 3px;
}}

QTabBar::tab:selected {{
    background-color: {t["group_bg"]};
}}

QMenu {{
    background-color: {t["menu_bg"]};
    border: 1px solid {t["menu_border"]};
    border-radius: 8px;
    padding: 5px;
}}

QMenu::item {{
    padding: 6px 12px;
    border-radius: 6px;
}}

QMenu::item:selected {{
    background-color: {t["menu_sel_bg"]};
    color: {t["menu_sel_fg"]};
}}

QToolTip {{
    color: {t["tooltip_fg"]};
    background-color: {t["tooltip_bg"]};
    border: 1px solid {t["tooltip_border"]};
    padding: 4px 8px;
}}

QProgressBar {{
    border: 1px solid {t["table_border"]};
    border-radius: 8px;
    background-color: {t["progress_bg"]};
    text-align: center;
    color: {t["progress_fg"]};
    min-height: 14px;
}}

QProgressBar::chunk {{
    border-radius: 7px;
    background-color: {t["progress_chunk"]};
}}

QScrollBar:vertical {{
    background: {t["scroll_bg"]};
    width: 10px;
    margin: 0px;
    border-radius: 5px;
}}

QScrollBar::handle:vertical {{
    background: {t["scroll_handle"]};
    min-height: 24px;
    border-radius: 5px;
}}

QScrollBar::handle:vertical:hover {{
    background: {t["scroll_hover"]};
}}

QScrollBar:horizontal {{
    background: {t["scroll_bg"]};
    height: 10px;
    margin: 0px;
    border-radius: 5px;
}}

QScrollBar::handle:horizontal {{
    background: {t["scroll_handle"]};
    min-width: 24px;
    border-radius: 5px;
}}

QScrollBar::handle:horizontal:hover {{
    background: {t["scroll_hover"]};
}}

QScrollBar::add-page,
QScrollBar::sub-page,
QScrollBar::add-line,
QScrollBar::sub-line {{
    background: transparent;
    border: none;
}}

QDialogButtonBox QPushButton {{
    min-width: 96px;
}}
"""


def _ensure_system_theme_hook(app: QApplication):
    global _SYSTEM_THEME_HOOKED
    if _SYSTEM_THEME_HOOKED or app is None:
        return
    try:
        hints = app.styleHints()
        sig = getattr(hints, "colorSchemeChanged", None)
        if sig is None:
            return
        sig.connect(lambda *_: _on_system_theme_changed())
        _SYSTEM_THEME_HOOKED = True
    except Exception:
        pass


def _on_system_theme_changed():
    app = QApplication.instance()
    if app is None:
        return
    if _CURRENT_THEME_MODE == THEME_MODE_SYSTEM:
        apply_modern_theme(app, mode=THEME_MODE_SYSTEM, persist=False)


def current_theme_mode() -> str:
    return _CURRENT_THEME_MODE


def current_effective_theme_mode() -> str:
    return _CURRENT_EFFECTIVE_MODE


def set_theme_mode(
    mode: str,
    *,
    app: QApplication | None = None,
    persist: bool = True,
) -> str:
    norm = normalize_theme_mode(mode)
    if persist:
        save_theme_mode(norm)
    app = app or QApplication.instance()
    if app is not None:
        return apply_modern_theme(app, mode=norm, persist=False)
    return norm


def apply_modern_theme(
    app: QApplication,
    *,
    mode: str | None = None,
    persist: bool = False,
) -> str:
    global _CURRENT_THEME_MODE, _CURRENT_EFFECTIVE_MODE

    if app is None:
        return THEME_MODE_LIGHT

    _ensure_system_theme_hook(app)
    _install_messagebox_auto_sizer(app)

    pref = normalize_theme_mode(mode if mode is not None else load_saved_theme_mode())
    if persist:
        save_theme_mode(pref)
    effective = _resolve_effective_mode(pref, app)
    font_family = _pick_app_font_family()

    try:
        app.setStyle("Fusion")
    except Exception:
        pass

    try:
        font = QFont(font_family, 10)
        font.setStyleStrategy(QFont.PreferAntialias)
        app.setFont(font)
    except Exception:
        pass

    app.setStyleSheet(_build_stylesheet(font_family, effective))
    _CURRENT_THEME_MODE = pref
    _CURRENT_EFFECTIVE_MODE = effective
    return effective
