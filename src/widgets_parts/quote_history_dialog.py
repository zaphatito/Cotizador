# src/widgets_parts/quote_history_dialog.py
from __future__ import annotations

import os
import datetime
import re
import threading

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, QTimer, QUrl, QEvent, Signal
from PySide6.QtGui import QDesktopServices, QAction, QCloseEvent, QBrush, QColor, QFont, QPen
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton,
    QTableView, QLabel, QMessageBox, QHeaderView, QMenu,
    QApplication, QDialog, QInputDialog, QCheckBox, QComboBox, QFormLayout, QGroupBox,
    QStyledItemDelegate, QStyleOptionViewItem, QStyle,
)

from sqlModels.db import connect, tx
from sqlModels.api_identity import API_LOGIN_PASSWORD, build_api_settings
from sqlModels.quote_statuses_repo import get_quote_statuses_cached
from sqlModels.settings_repo import get_setting, set_setting
from sqlModels.quotes_repo import (
    list_quotes, get_quote_header, get_quote_items, soft_delete_quote,
    update_quote_payment, update_quote_status,
    status_label,
)
from sqlModels.rates_repo import load_rates

from ..logging_setup import get_logger
from ..utils import nz
from ..paths import DATA_DIR, COTIZACIONES_DIR, resolve_pdf_path_portable

from ..db_path import resolve_db_path
from ..api.presupuesto_client import sync_pending_history_quotes_once, verify_cotizador_signature_once
from ..catalog_sync import (
    sync_catalog_from_excel_to_db,
    load_catalog_from_db,
    validate_products_catalog_df,
    products_update_required_message,
)
from ..config import (
    ALLOWED_COMPANY_TYPES,
    APP_CONFIG,
    APP_CURRENCY,
    APP_COUNTRY,
    COUNTRY_CODE,
    STORE_ID,
    get_currency_context,
    set_currency_context,
    is_ai_enabled,
    set_ai_enabled,
    is_recommendations_enabled,
    set_recommendations_enabled,
)
from ..quote_code import format_quote_code, quote_match_key, extract_quote_digits
from ..ui_theme import (
    normalize_theme_mode,
    set_theme_mode,
)

from ..app_window import SistemaCotizaciones
from ..app_window_parts.ticket_actions import generar_ticket_para_cotizacion
from ..pdfgen import generar_pdf

from .menu import MainMenuWindow, RatesDialog
from .rates_history_dialog import RatesHistoryDialog
from .quote_status_dialog import QuoteStatusDialog
from .status_colors import bg_for_status, best_text_color_for_bg
from .status_colors_dialog import StatusColorsDialog

# ✅ NUEVO: dock assistant

log = get_logger(__name__)


def center_on_screen(w):
    """Centra un widget en la pantalla (availableGeometry) donde esté."""
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


def _doc_header_for_country(country: str) -> str:
    return "Documento"


def _parse_dt(value) -> datetime.datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value
    if isinstance(value, datetime.date):
        return datetime.datetime.combine(value, datetime.time.min)

    s = str(value).strip()
    if not s:
        return None

    s2 = s[:-1] + "+00:00" if s.endswith("Z") else s

    try:
        return datetime.datetime.fromisoformat(s2)
    except Exception:
        pass

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(s, fmt)
        except Exception:
            continue

    return None


def format_dt_legible(value) -> str:
    dt = _parse_dt(value)
    if dt is None:
        return "" if value is None else str(value).strip()
    out = dt.strftime("%d/%m/%Y %I:%M %p")
    return out.replace("AM", "am").replace("PM", "pm")


def _is_today(value) -> bool:
    dt = _parse_dt(value)
    if not dt:
        return False
    try:
        return dt.date() == datetime.datetime.now().date()
    except Exception:
        return False


class _HistoryNoCellFocusDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._hover_row = -1

    def set_hover_row(self, row: int) -> None:
        new_row = int(row) if isinstance(row, int) and row >= 0 else -1
        if new_row == self._hover_row:
            return
        old_row = self._hover_row
        self._hover_row = new_row
        try:
            view = self.parent()
            if view is not None and hasattr(view, "viewport"):
                self._update_row_region(view, old_row)
                self._update_row_region(view, new_row)
        except Exception:
            pass

    @staticmethod
    def _update_row_region(view, row: int) -> None:
        try:
            r = int(row)
        except Exception:
            return
        if r < 0:
            return
        model = view.model() if hasattr(view, "model") else None
        if model is None:
            return
        cols = int(model.columnCount())
        if cols <= 0:
            return
        first = model.index(r, 0)
        last = model.index(r, cols - 1)
        if not first.isValid() or not last.isValid():
            return
        rect = view.visualRect(first).united(view.visualRect(last))
        if rect.isValid():
            view.viewport().update(rect)

    @staticmethod
    def _row_hover_color(option: QStyleOptionViewItem) -> QColor:
        base = option.palette.base().color()
        lum = (0.2126 * base.redF()) + (0.7152 * base.greenF()) + (0.0722 * base.blueF())
        if lum < 0.45:
            return QColor(255, 255, 255, 34)
        return QColor(15, 23, 35, 28)

    @staticmethod
    def _row_selected_overlay_color(option: QStyleOptionViewItem) -> QColor:
        base = option.palette.base().color()
        lum = (0.2126 * base.redF()) + (0.7152 * base.greenF()) + (0.0722 * base.blueF())
        if lum < 0.45:
            return QColor(255, 255, 255, 46)
        return QColor(15, 23, 35, 34)

    @staticmethod
    def _row_selected_border_color(option: QStyleOptionViewItem) -> QColor:
        c = QColor(option.palette.highlight().color())
        if not c.isValid():
            c = QColor("#6E7F95")
        c.setAlpha(210)
        return c

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        opt.state &= ~QStyle.State_HasFocus
        selected_row = bool(opt.state & QStyle.State_Selected)
        hovered_row = (index.row() == self._hover_row)
        if selected_row:
            # Mantiene el color de estado de la fila y evita que el style-sheet
            # global reemplace todo el fondo al seleccionar.
            opt.state &= ~QStyle.State_Selected
        if hovered_row:
            # Evita el hover por celda del estilo global.
            opt.state &= ~QStyle.State_MouseOver
        super().paint(painter, opt, index)
        if selected_row:
            painter.save()
            painter.fillRect(opt.rect, self._row_selected_overlay_color(opt))
            pen = QPen(self._row_selected_border_color(opt))
            pen.setWidth(1)
            painter.setPen(pen)
            painter.drawLine(opt.rect.topLeft(), opt.rect.topRight())
            painter.drawLine(opt.rect.bottomLeft(), opt.rect.bottomRight())
            try:
                last_col = index.model().columnCount() - 1
            except Exception:
                last_col = -1
            if index.column() == 0:
                painter.drawLine(opt.rect.topLeft(), opt.rect.bottomLeft())
            if last_col >= 0 and index.column() == last_col:
                painter.drawLine(opt.rect.topRight(), opt.rect.bottomRight())
            painter.restore()
        elif hovered_row:
            painter.fillRect(opt.rect, self._row_hover_color(opt))


class HistoryConfigDialog(QDialog):
    _ADMIN_PASSWORD = "Papina."

    def __init__(self, history_window: "QuoteHistoryWindow"):
        super().__init__(history_window)
        self._history = history_window
        self.setWindowTitle("Configuración")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)

        self.chk_ai = QCheckBox("Activar IA (chat y arranque de Ollama)")
        self.chk_ai.setChecked(bool(is_ai_enabled(refresh=True)))
        self.chk_ai.toggled.connect(self._on_ai_toggled)

        self.chk_recs = QCheckBox("Activar recomendaciones")
        self.chk_recs.setChecked(bool(is_recommendations_enabled(refresh=True)))
        self.chk_recs.toggled.connect(self._on_recs_toggled)

        self.cmb_theme = QComboBox()
        self.cmb_theme.addItem("Sistema (auto)", "system")
        self.cmb_theme.addItem("Claro", "light")
        self.cmb_theme.addItem("Oscuro", "dark")
        self._load_theme_setting()
        self.cmb_theme.currentIndexChanged.connect(self._on_theme_changed)

        self.btn_chat_style = QPushButton("Personalizar chat")
        self.btn_chat_style.clicked.connect(self._open_chat_style)

        self.btn_rates = QPushButton("Configurar tasas de cambio")
        self.btn_rates.clicked.connect(self._open_rates)
        self.btn_status_colors = QPushButton("Configurar estados")
        self.btn_status_colors.clicked.connect(self._open_status_colors)

        self.btn_unlock_app_values = QPushButton("Modificar valores del sistema")
        self.btn_unlock_app_values.clicked.connect(self._unlock_app_values)

        self.grp_app_values = QGroupBox("Valores del sistema")
        self.grp_app_values.setVisible(False)
        self.grp_app_values.setEnabled(False)

        form = QFormLayout(self.grp_app_values)
        self.cmb_country = QComboBox()
        self.cmb_country.addItems(["PARAGUAY", "PERU", "VENEZUELA"])

        self.cmb_listing_type = QComboBox()
        self.cmb_listing_type.addItems(["AMBOS", "PRODUCTOS", "PRESENTACIONES"])

        self.chk_allow_no_stock = QCheckBox("Permitir cotizar sin stock")

        self.ed_store_id = QLineEdit()
        self.ed_store_id.setPlaceholderText("Ej: 01")

        self.cmb_company_type = QComboBox()
        self.cmb_company_type.addItems([str(x) for x in ALLOWED_COMPANY_TYPES])

        self.ed_username = QLineEdit()
        self.ed_username.setPlaceholderText("Nombre de usuario")
        self.chk_tienda = QCheckBox("Este equipo es tienda")
        self.chk_tienda.setToolTip("Marcado: si. Desmarcado: no.")

        form.addRow("", self.chk_ai)
        form.addRow("País:", self.cmb_country)
        form.addRow("Tipo de listado:", self.cmb_listing_type)
        form.addRow("", self.chk_allow_no_stock)
        form.addRow("Store ID:", self.ed_store_id)
        form.addRow("Compañía:", self.cmb_company_type)
        form.addRow("Nombre de usuario:", self.ed_username)
        form.addRow("Tienda:", self.chk_tienda)

        row_actions = QHBoxLayout()
        self.btn_save_app_values = QPushButton("Guardar valores del sistema")
        self.btn_save_app_values.setProperty("variant", "primary")
        self.btn_save_app_values.clicked.connect(self._save_app_values)
        row_actions.addStretch(1)
        row_actions.addWidget(self.btn_save_app_values)
        form.addRow(row_actions)

        btn_close = QPushButton("Cerrar")
        btn_close.clicked.connect(self.accept)

        layout.addWidget(self.chk_recs)
        theme_row = QHBoxLayout()
        theme_row.addWidget(QLabel("Tema de la interfaz:"))
        theme_row.addWidget(self.cmb_theme, 1)
        layout.addLayout(theme_row)
        layout.addSpacing(8)
        layout.addWidget(self.btn_chat_style)
        layout.addWidget(self.btn_rates)
        layout.addWidget(self.btn_status_colors)
        layout.addSpacing(12)
        layout.addWidget(self.btn_unlock_app_values)
        layout.addWidget(self.grp_app_values)
        layout.addStretch(1)
        layout.addWidget(btn_close)

        self._load_app_values()
        self._sync_controls()
        self.adjustSize()

    def _sync_controls(self):
        ai_on = bool(is_ai_enabled(refresh=True))
        self.btn_chat_style.setVisible(ai_on)
        self.btn_chat_style.setEnabled(ai_on)
        if ai_on:
            self.btn_chat_style.setToolTip("Personalizar apariencia del chat.")
        else:
            self.btn_chat_style.setToolTip("No disponible: IA desactivada.")

    def _on_ai_toggled(self, checked: bool):
        try:
            set_ai_enabled(bool(checked))
        except Exception as e:
            self.chk_ai.blockSignals(True)
            self.chk_ai.setChecked(bool(is_ai_enabled(refresh=True)))
            self.chk_ai.blockSignals(False)
            QMessageBox.critical(self, "Error", f"No se pudo actualizar IA:\n{e}")
            return

        try:
            self._history.refresh_ai_controls()
        except Exception:
            pass
        self._sync_controls()

    def _on_recs_toggled(self, checked: bool):
        try:
            set_recommendations_enabled(bool(checked))
        except Exception as e:
            self.chk_recs.blockSignals(True)
            self.chk_recs.setChecked(bool(is_recommendations_enabled(refresh=True)))
            self.chk_recs.blockSignals(False)
            QMessageBox.critical(self, "Error", f"No se pudo actualizar recomendaciones:\n{e}")
            return

        try:
            self._history.refresh_recommendations_controls()
        except Exception:
            pass

    def _open_chat_style(self):
        try:
            self._history.open_chat_personalization()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo abrir personalizacion:\n{e}")

    def _open_rates(self):
        dlg = RatesDialog(self)
        if dlg.exec() == QDialog.Accepted:
            try:
                if self._history.quote_events is not None:
                    self._history.quote_events.rates_updated.emit()
            except Exception:
                pass

    def _open_status_colors(self):
        dlg = StatusColorsDialog(
            self,
            on_colors_applied=(
                self._history.refresh_status_colors
                if (self._history is not None and hasattr(self._history, "refresh_status_colors"))
                else None
            ),
        )
        dlg.exec()

    @staticmethod
    def _set_combo_value(combo: QComboBox, value: str) -> None:
        val = str(value or "").strip()
        if not val:
            return
        idx = combo.findText(val, Qt.MatchExactly)
        if idx < 0:
            idx = combo.findText(val.upper(), Qt.MatchExactly)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    @staticmethod
    def _parse_optional_bool(value) -> bool | None:
        if value is None:
            return None

        s = str(value).strip().lower()
        if not s:
            return None
        if s in ("1", "true", "yes", "on", "si"):
            return True
        if s in ("0", "false", "no", "off"):
            return False
        return None

    @staticmethod
    def _optional_bool_to_check_state(value: bool | None) -> Qt.CheckState:
        if value is None:
            return Qt.CheckState.Unchecked
        return Qt.CheckState.Checked if value else Qt.CheckState.Unchecked

    @staticmethod
    def _check_state_to_optional_bool(state: Qt.CheckState) -> bool:
        return state == Qt.CheckState.Checked

    def _load_app_values(self) -> None:
        country = str(APP_CONFIG.get("country", "PARAGUAY")).strip().upper()
        listing_type = str(APP_CONFIG.get("listing_type", "AMBOS")).strip().upper()
        allow_no_stock = bool(APP_CONFIG.get("allow_no_stock", False))
        store_id = str(APP_CONFIG.get("store_id", "")).strip()
        company_type = str(APP_CONFIG.get("company_type", ALLOWED_COMPANY_TYPES[0])).strip().upper()
        username = str(APP_CONFIG.get("username", "")).strip()
        tienda = self._parse_optional_bool(APP_CONFIG.get("tienda"))

        con = None
        try:
            con = connect(resolve_db_path())

            country = get_setting(con, "country", country).strip().upper()
            listing_type = get_setting(con, "listing_type", listing_type).strip().upper()
            allow_raw = get_setting(con, "allow_no_stock", "1" if allow_no_stock else "0").strip().lower()
            allow_no_stock = allow_raw in ("1", "true", "yes", "on", "si")
            store_id = get_setting(con, "store_id", store_id).strip()
            company_type = get_setting(con, "company_type", company_type).strip().upper()
            username = get_setting(con, "username", username).strip()
            tienda_default = None if tienda is None else ("1" if tienda else "0")
            tienda = self._parse_optional_bool(get_setting(con, "tienda", tienda_default))
        except Exception:
            log.exception("No se pudieron cargar los valores protegidos de configuracion.")
        finally:
            if con is not None:
                try:
                    con.close()
                except Exception:
                    pass

        self._set_combo_value(self.cmb_country, country)
        self._set_combo_value(self.cmb_listing_type, listing_type)
        self.chk_allow_no_stock.setChecked(bool(allow_no_stock))
        self.ed_store_id.setText(store_id)
        self._set_combo_value(self.cmb_company_type, company_type)
        self.ed_username.setText(username)
        self.chk_tienda.setCheckState(self._optional_bool_to_check_state(tienda))

    def _load_theme_setting(self):
        mode = "system"
        con = None
        try:
            con = connect(resolve_db_path())
            mode = get_setting(con, "ui_theme_mode", "system")
        except Exception:
            mode = "system"
        finally:
            if con is not None:
                try:
                    con.close()
                except Exception:
                    pass

        mode = normalize_theme_mode(mode)
        idx = self.cmb_theme.findData(mode)
        self.cmb_theme.blockSignals(True)
        if idx >= 0:
            self.cmb_theme.setCurrentIndex(idx)
        else:
            self.cmb_theme.setCurrentIndex(0)
        self.cmb_theme.blockSignals(False)

    def _on_theme_changed(self):
        mode = normalize_theme_mode(self.cmb_theme.currentData())
        con = None
        try:
            con = connect(resolve_db_path())
            with tx(con):
                set_setting(con, "ui_theme_mode", mode)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo guardar el tema:\n{e}")
            return
        finally:
            if con is not None:
                try:
                    con.close()
                except Exception:
                    pass

        try:
            set_theme_mode(mode, app=QApplication.instance(), persist=False)
        except Exception:
            pass

    def _unlock_app_values(self) -> None:
        text, ok = QInputDialog.getText(
            self,
            "Clave de configuracion",
            "Ingrese la clave:",
            QLineEdit.Password,
        )
        if not ok:
            return

        if str(text or "").strip() != self._ADMIN_PASSWORD:
            log.warning("Intento de desbloqueo de configuración con clave incorrecta.")
            QMessageBox.warning(self, "Clave incorrecta", "La clave ingresada no es valida.")
            return

        self.grp_app_values.setVisible(True)
        self.grp_app_values.setEnabled(True)
        self.btn_unlock_app_values.setEnabled(False)
        self.btn_unlock_app_values.setText("Valores del sistema habilitados")
        log.info("Se habilitaron los valores protegidos de configuración.")

    def _save_app_values(self) -> None:
        allowed_countries = {"PARAGUAY", "PERU", "VENEZUELA"}
        allowed_listing_types = {"AMBOS", "PRODUCTOS", "PRESENTACIONES"}
        allowed_company_types = {str(x).strip().upper() for x in ALLOWED_COMPANY_TYPES}

        country = str(self.cmb_country.currentText() or "").strip().upper()
        listing_type = str(self.cmb_listing_type.currentText() or "").strip().upper()
        allow_no_stock = bool(self.chk_allow_no_stock.isChecked())
        store_id = str(self.ed_store_id.text() or "").strip().upper()
        company_type = str(self.cmb_company_type.currentText() or "").strip().upper()
        username = str(self.ed_username.text() or "").strip()
        tienda = self._check_state_to_optional_bool(self.chk_tienda.checkState())

        if country not in allowed_countries:
            QMessageBox.warning(self, "Validación", "País inválido.")
            return
        if listing_type not in allowed_listing_types:
            QMessageBox.warning(self, "Validación", "Tipo de listado inválido.")
            return
        if company_type not in allowed_company_types:
            QMessageBox.warning(self, "Validación", "Compañía inválida.")
            return
        if store_id and not re.fullmatch(r"[A-Za-z0-9]+", store_id):
            QMessageBox.warning(
                self,
                "Validación",
                "Store ID inválido. Use solo letras y números.",
            )
            return

        con = None
        try:
            con = connect(resolve_db_path())

            with tx(con):
                set_setting(con, "country", country)
                set_setting(con, "listing_type", listing_type)
                set_setting(con, "allow_no_stock", "1" if allow_no_stock else "0")
                set_setting(con, "store_id", store_id)
                set_setting(con, "company_type", company_type)
                set_setting(con, "username", username)
                set_setting(con, "tienda", "1" if tienda else "0")
                api_vals = build_api_settings(
                    country=country,
                    company_type=company_type,
                    password_plain=API_LOGIN_PASSWORD,
                )
                for k, v in api_vals.items():
                    set_setting(con, k, v)
        except Exception as e:
            log.exception("No se pudieron guardar los valores protegidos de configuración.")
            QMessageBox.critical(self, "Error", f"No se pudieron guardar los cambios:\n{e}")
            return
        finally:
            if con is not None:
                try:
                    con.close()
                except Exception:
                    pass

        APP_CONFIG["country"] = country
        APP_CONFIG["listing_type"] = listing_type
        APP_CONFIG["allow_no_stock"] = allow_no_stock
        APP_CONFIG["store_id"] = store_id
        APP_CONFIG["company_type"] = company_type
        APP_CONFIG["username"] = username
        APP_CONFIG["tienda"] = tienda
        APP_CONFIG["id_user_api"] = str(api_vals.get("id_user_api", ""))
        APP_CONFIG["user_api"] = str(api_vals.get("user_api", ""))
        APP_CONFIG["password_api_hash"] = str(api_vals.get("password_api_hash", ""))

        log.info(
            "Configuración protegida actualizada: country=%s listing_type=%s allow_no_stock=%s store_id=%s company_type=%s username=%s tienda=%s",
            country,
            listing_type,
            allow_no_stock,
            store_id,
            company_type,
            username,
            tienda,
        )
        try:
            if self._history is not None and hasattr(self._history, "_wake_background_api_sync"):
                self._history._wake_background_api_sync()
        except Exception:
            pass
        QMessageBox.information(
            self,
            "Configuración guardada",
            "Los cambios fueron guardados.\nReinicie la aplicación para aplicar todos los cambios globales.",
        )


class QuotesTableModel(QAbstractTableModel):
    def __init__(self, *, show_payment: bool):
        super().__init__()
        self.rows: list[dict] = []
        self.show_payment = bool(show_payment)
        doc_hdr = _doc_header_for_country(APP_COUNTRY)

        if self.show_payment:
            self.HEADERS = [
                "Fecha/Hora", "N°", "Cliente", doc_hdr, "Teléfono",
                "Estado", "Pago", "Total", "Moneda", "Items", "PDF"
            ]
        else:
            self.HEADERS = [
                "Fecha/Hora", "N°", "Cliente", doc_hdr, "Teléfono",
                "Estado", "Total", "Moneda", "Items", "PDF"
            ]

        self._today_font = QFont()
        self._today_font.setBold(True)
        self._centered_cols = {
            self._idx_no(),
            self._idx_estado(),
            self._idx_total(),
            self._idx_currency(),
            self._idx_items(),
        }
        idx_pago = self._idx_pago()
        if self.show_payment and idx_pago is not None:
            self._centered_cols.add(idx_pago)

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return self.HEADERS[section]
        return str(section + 1)

    def _idx_no(self) -> int:
        return 1

    def _idx_estado(self) -> int:
        return 5

    def _idx_pago(self) -> int | None:
        return 6 if self.show_payment else None

    def _idx_total(self) -> int:
        return 7 if self.show_payment else 6

    def _idx_currency(self) -> int:
        return 8 if self.show_payment else 7

    def _idx_items(self) -> int:
        return 9 if self.show_payment else 8

    def _idx_pdf(self) -> int:
        return 10 if self.show_payment else 9

    @staticmethod
    def _text_key(v) -> str:
        return ("" if v is None else str(v)).casefold()

    def _hydrate_row_cache(self, row: dict, *, today: datetime.date) -> None:
        created_raw = row.get("created_at", "")
        created_dt = _parse_dt(created_raw)
        row["_cache_dt"] = created_dt
        row["_cache_is_today"] = bool(created_dt and created_dt.date() == today)
        row["_cache_created_display"] = format_dt_legible(created_raw)

        estado_val = row.get("estado")
        estado_txt = status_label(estado_val)
        row["_cache_estado_txt"] = estado_txt
        row["_cache_estado_key"] = self._text_key(estado_txt)

        bg = bg_for_status(estado_val)
        row["_cache_bg_color"] = bg
        row["_cache_bg_brush"] = QBrush(bg) if bg is not None else None
        row["_cache_fg_brush"] = QBrush(best_text_color_for_bg(bg)) if bg is not None else None

        pago_txt = str(row.get("metodo_pago") or "").strip()
        row["_cache_pago_txt"] = pago_txt
        row["_cache_pago_key"] = self._text_key(pago_txt)

        try:
            total_num = float(nz(row.get("total_shown"), 0.0))
            row["_cache_total_num"] = total_num
            row["_cache_total_txt"] = f"{total_num:.2f}"
        except Exception:
            row["_cache_total_num"] = 0.0
            row["_cache_total_txt"] = str(row.get("total_shown", "0.00"))

        currency_txt = str(row.get("currency_shown") or "")
        row["_cache_currency_txt"] = currency_txt
        row["_cache_currency_key"] = self._text_key(currency_txt)

        items_raw = row.get("items_count", 0)
        try:
            items_num = int(nz(items_raw, 0))
        except Exception:
            try:
                items_num = int(str(items_raw).strip())
            except Exception:
                items_num = 0
        row["_cache_items_num"] = items_num
        row["_cache_items_txt"] = str(items_num)

        pdf_path = str(row.get("pdf_path") or "")
        pdf_name = os.path.basename(pdf_path)
        row["_cache_pdf_path"] = pdf_path
        row["_cache_pdf_name"] = pdf_name
        row["_cache_pdf_key"] = self._text_key(pdf_name)

        qn = row.get("quote_no")
        qn_raw = "" if qn is None else str(qn).strip()
        qn_digits = extract_quote_digits(qn_raw)
        if qn_digits:
            try:
                qn_text = str(int(qn_digits)).zfill(max(7, len(qn_digits)))
            except Exception:
                qn_text = qn_digits
        else:
            qn_text = qn_raw
        row["_cache_quote_no_txt"] = qn_text
        row["_cache_quote_no_key"] = self._text_key(qn_text)
        try:
            row["_cache_quote_no_num"] = int(quote_match_key(qn))
            row["_cache_quote_no_has_num"] = True
        except Exception:
            row["_cache_quote_no_num"] = 0
            row["_cache_quote_no_has_num"] = False

        row["_cache_cliente_key"] = self._text_key(row.get("cliente"))
        row["_cache_cedula_key"] = self._text_key(row.get("cedula"))
        row["_cache_telefono_key"] = self._text_key(row.get("telefono"))

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        r = self.rows[index.row()]
        c = index.column()

        if role == Qt.FontRole:
            return self._today_font if r.get("_cache_is_today") else None

        if role == Qt.BackgroundRole:
            return r.get("_cache_bg_brush")

        if role == Qt.ForegroundRole:
            return r.get("_cache_fg_brush")

        if role == Qt.DisplayRole:
            if c == 0:
                return r.get("_cache_created_display", "")
            if c == 1:
                return r.get("_cache_quote_no_txt", "")
            if c == 2:
                return r.get("cliente", "")
            if c == 3:
                return r.get("cedula", "")
            if c == 4:
                return r.get("telefono", "")

            if c == self._idx_estado():
                return r.get("_cache_estado_txt", "")

            if self.show_payment and c == self._idx_pago():
                return r.get("_cache_pago_txt", "")

            if c == self._idx_total():
                return r.get("_cache_total_txt", "0.00")

            if c == self._idx_currency():
                return r.get("_cache_currency_txt", "")

            if c == self._idx_items():
                return r.get("_cache_items_txt", "0")

            if c == self._idx_pdf():
                return r.get("_cache_pdf_name", "")

        if role == Qt.TextAlignmentRole:
            if c in self._centered_cols:
                return int(Qt.AlignVCenter | Qt.AlignCenter)
            return int(Qt.AlignVCenter | Qt.AlignLeft)

        if role == Qt.ToolTipRole and c == self._idx_pdf():
            return r.get("_cache_pdf_path", "")

        return None

    def set_rows(self, rows: list[dict]):
        self.beginResetModel()
        today = datetime.datetime.now().date()
        hydrated: list[dict] = []
        for src in (rows or []):
            row = dict(src or {})
            self._hydrate_row_cache(row, today=today)
            hydrated.append(row)
        self.rows = hydrated
        self.endResetModel()

    def get_id_at(self, row: int) -> int | None:
        if 0 <= row < len(self.rows):
            try:
                return int(self.rows[row]["id"])
            except Exception:
                return None
        return None

    def _sort_key(self, r: dict, c: int):
        if c == 0:
            dt = r.get("_cache_dt")
            return (dt is None, dt or datetime.datetime.min)

        if c == self._idx_no():
            if r.get("_cache_quote_no_has_num"):
                return (
                    False,
                    int(r.get("_cache_quote_no_num", 0)),
                    r.get("_cache_quote_no_key", ""),
                )
            return (True, r.get("_cache_quote_no_key", ""))

        if c == 2:
            v = r.get("cliente")
            return (v is None, r.get("_cache_cliente_key", ""))
        if c == 3:
            v = r.get("cedula")
            return (v is None, r.get("_cache_cedula_key", ""))
        if c == 4:
            v = r.get("telefono")
            return (v is None, r.get("_cache_telefono_key", ""))

        if c == self._idx_estado():
            return (False, r.get("_cache_estado_key", ""))

        if self.show_payment and c == self._idx_pago():
            v = r.get("metodo_pago")
            return (v is None, r.get("_cache_pago_key", ""))

        if c == self._idx_total():
            v = r.get("total_shown")
            return (v is None, float(r.get("_cache_total_num", 0.0)))

        if c == self._idx_currency():
            v = r.get("currency_shown")
            return (v is None, r.get("_cache_currency_key", ""))

        if c == self._idx_items():
            v = r.get("items_count")
            return (v is None, int(r.get("_cache_items_num", 0)))

        if c == self._idx_pdf():
            p = r.get("_cache_pdf_path") or ""
            return (not bool(p), r.get("_cache_pdf_key", ""))

        return (False, "")

    def sort(self, column: int, order: Qt.SortOrder = Qt.AscendingOrder):
        if not self.rows:
            return
        self.layoutAboutToBeChanged.emit()
        try:
            reverse = (order == Qt.DescendingOrder)
            self.rows.sort(key=lambda r: self._sort_key(r, column), reverse=reverse)
        finally:
            self.layoutChanged.emit()


class QuoteHistoryWindow(QMainWindow):
    lockdown_requested = Signal(str)
    _DEFAULT_SIZE = (1300, 720)
    _MIN_REASONABLE = (980, 620)
    _WIN_KEY_PREFIX = "ui_window_history"

    def __init__(self, *, catalog_manager, quote_events, app_icon):
        super().__init__()
        self.setWindowTitle("Sistema de cotizaciones")
        self.resize(*self._DEFAULT_SIZE)
        if not app_icon.isNull():
            self.setWindowIcon(app_icon)

        self._db_path = resolve_db_path()
        self._window_state_restored = False

        self._centered_once = False
        self.catalog_manager = catalog_manager
        self.quote_events = quote_events

        self._restore_window_state()

        # ✅ Asistente dock (reemplaza ChatQuoteDialog)
        self.assistant = None
        if is_ai_enabled(refresh=True):
            self._attach_assistant()

        show_payment = (APP_COUNTRY in ("PARAGUAY", "PERU"))
        self.model = QuotesTableModel(show_payment=show_payment)

        self.page_size = 50
        self.offset = 0
        self.total = 0

        self._open_windows: list[SistemaCotizaciones] = []
        self._closing_with_children = False
        self._lockdown_active = False

        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(220)
        self._filter_timer.timeout.connect(self._reload_first_page)

        self._rt_timer = QTimer(self)
        self._rt_timer.setSingleShot(True)
        self._rt_timer.setInterval(120)
        self._rt_timer.timeout.connect(self._reload_first_page)

        if self.quote_events is not None:
            try:
                self.quote_events.quote_saved.connect(self._on_quote_saved)
            except Exception:
                pass

        if self.catalog_manager is not None:
            try:
                self.catalog_manager.catalog_updated.connect(self._on_catalog_updated)
            except Exception:
                pass

        self._api_sync_stop_event = threading.Event()
        self._api_sync_wake_event = threading.Event()
        self._api_sync_thread: threading.Thread | None = None
        self.lockdown_requested.connect(self._apply_admin_lockdown)

        central = QWidget()
        self.setCentralWidget(central)
        main = QVBoxLayout(central)
        main.setContentsMargins(10, 8, 10, 8)
        main.setSpacing(8)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(7)

        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText(
            "Filtrar (cualquier columna): cliente / doc / teléfono / N° / estado / pago / total / moneda / items / PDF…"
        )
        self.txt_search.setClearButtonEnabled(True)
        self.txt_search.textChanged.connect(self._on_filters_changed)

        self.txt_prod = QLineEdit()
        self.txt_prod.setPlaceholderText("Contiene producto: código o nombre")
        self.txt_prod.setClearButtonEnabled(True)
        self.txt_prod.textChanged.connect(self._on_filters_changed)

        self.btn_new = QPushButton("➕ Nueva cotización")
        self.btn_new.setProperty("variant", "primary")
        self.btn_new.setMinimumHeight(30)
        self.btn_new.setMinimumWidth(165)
        self.btn_new.clicked.connect(self._open_new_quote)

        self.btn_chat = QPushButton("💬 Chat")
        self.btn_chat.setMinimumHeight(30)
        self.btn_chat.setMinimumWidth(110)
        self.btn_chat.setToolTip("Asistente (dock). Ctrl+K abre/cierra.")
        self.btn_chat.clicked.connect(self._open_chat)

        self.btn_menu = QPushButton("☰ Menú")
        self.btn_menu.setMinimumHeight(30)
        self.btn_menu.setMinimumWidth(104)
        self.btn_menu.clicked.connect(self._open_main_menu)

        top.addWidget(self.txt_search, 3)
        top.addWidget(self.txt_prod, 2)
        top.addWidget(self.btn_new, 0)
        top.addWidget(self.btn_chat, 0)
        top.addWidget(self.btn_menu, 0)
        main.addLayout(top)

        self.table = QTableView()
        self.table.setObjectName("historyTable")
        self.table.setMouseTracking(True)
        self._history_delegate = _HistoryNoCellFocusDelegate(self.table)
        self.table.setItemDelegate(self._history_delegate)
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setSelectionMode(QTableView.SingleSelection)
        self.table.setEditTriggers(QTableView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(True)
        self.table.doubleClicked.connect(self._on_table_double_clicked)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_table_context_menu)
        self.table.entered.connect(self._on_table_hovered)
        self.table.viewport().installEventFilter(self)

        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSortIndicatorShown(True)

        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Stretch)
        try:
            hh.setResizeContentsPrecision(8)
        except Exception:
            pass

        try:
            idx_no = self.model._idx_no()
            idx_estado = self.model._idx_estado()
            idx_pago = self.model._idx_pago()
            idx_total = self.model._idx_total()
            idx_items = self.model._idx_items()
            idx_pdf = self.model._idx_pdf()
            idx_curr = self.model._idx_currency()

            hh.setSectionResizeMode(idx_no, QHeaderView.ResizeToContents)
            hh.setSectionResizeMode(idx_estado, QHeaderView.ResizeToContents)
            if idx_pago is not None:
                hh.setSectionResizeMode(idx_pago, QHeaderView.ResizeToContents)
            hh.setSectionResizeMode(idx_total, QHeaderView.ResizeToContents)
            hh.setSectionResizeMode(idx_curr, QHeaderView.ResizeToContents)
            hh.setSectionResizeMode(idx_items, QHeaderView.ResizeToContents)
            hh.setSectionResizeMode(idx_pdf, QHeaderView.ResizeToContents)
        except Exception:
            pass

        main.addWidget(self.table)

        self.btn_pdf = QPushButton("Abrir PDF")
        self.btn_dup = QPushButton("Abrir cotización")
        self.btn_hide = QPushButton("Eliminar")
        self.btn_hide.setProperty("variant", "danger")
        self.btn_pdf.setMinimumHeight(30)
        self.btn_dup.setMinimumHeight(30)
        self.btn_hide.setMinimumHeight(30)
        self.btn_pdf.setMinimumWidth(110)
        self.btn_dup.setMinimumWidth(132)
        self.btn_hide.setMinimumWidth(96)

        self.btn_pdf.clicked.connect(self._open_pdf)
        self.btn_dup.clicked.connect(self._duplicate)
        self.btn_hide.clicked.connect(self._soft_delete)

        nav = QHBoxLayout()
        nav.setContentsMargins(0, 0, 0, 0)
        nav.setSpacing(8)
        self.lbl_page = QLabel("—")
        btn_prev = QPushButton("◀")
        btn_next = QPushButton("▶")
        btn_prev.setMinimumHeight(30)
        btn_next.setMinimumHeight(30)
        btn_prev.setMinimumWidth(34)
        btn_next.setMinimumWidth(34)
        btn_prev.clicked.connect(self._prev_page)
        btn_next.clicked.connect(self._next_page)
        nav.addWidget(self.lbl_page)
        nav.addWidget(btn_prev)
        nav.addWidget(btn_next)
        nav.addStretch(1)
        nav.addWidget(self.btn_pdf)
        nav.addWidget(self.btn_dup)
        nav.addWidget(self.btn_hide)
        main.addLayout(nav)

        self._apply_catalog_gate()
        self.refresh_recommendations_controls()
        self._reload_first_page()
        QTimer.singleShot(350, self._preload_status_catalog)
        QTimer.singleShot(1500, self._start_background_api_sync)

    # -----------------------------
    #  ✅ CONTROL DE VENTANAS HIJAS
    # -----------------------------
    def _is_qt_alive(self, obj) -> bool:
        if obj is None:
            return False
        try:
            _ = obj.windowTitle()
            return True
        except RuntimeError:
            return False
        except Exception:
            return True

    def _prune_open_windows(self):
        try:
            self._open_windows = [w for w in (self._open_windows or []) if self._is_qt_alive(w)]
        except Exception:
            pass

    def _register_open_quote_window(self, win: SistemaCotizaciones):
        try:
            if win is None:
                return
            try:
                win.setAttribute(Qt.WA_DeleteOnClose, True)
            except Exception:
                pass

            try:
                win.destroyed.connect(lambda *_: self._prune_open_windows())
            except Exception:
                pass

            try:
                if hasattr(win, "set_recommendations_enabled"):
                    win.set_recommendations_enabled(bool(is_recommendations_enabled(refresh=True)))
            except Exception:
                pass

            self._open_windows.append(win)
            self._prune_open_windows()
        except Exception:
            pass

    def _alive_quote_windows(self) -> list[SistemaCotizaciones]:
        self._prune_open_windows()
        return list(self._open_windows or [])

    def _close_all_quotes(self) -> bool:
        self._prune_open_windows()
        wins = list(self._open_windows or [])
        for w in wins:
            try:
                if self._is_qt_alive(w):
                    w.close()
            except Exception:
                pass

        try:
            QApplication.processEvents()
        except Exception:
            pass

        self._prune_open_windows()
        return len(self._open_windows or []) == 0

    def _attach_assistant(self):
        if self.assistant is not None:
            return
        try:
            from ..ai.assistant import attach_assistant
            self.assistant = attach_assistant(
                self,
                catalog_manager=self.catalog_manager,
                quote_events=self.quote_events,
                app_icon=self.windowIcon(),
            )
        except Exception:
            self.assistant = None

    def _detach_assistant(self):
        ctl = self.assistant
        if ctl is None:
            return
        try:
            if hasattr(ctl, "uninstall"):
                ctl.uninstall()
            else:
                dock = getattr(ctl, "dock", None)
                if dock is not None:
                    try:
                        dock.hide()
                    except Exception:
                        pass
                    try:
                        self.removeDockWidget(dock)
                    except Exception:
                        pass
                    try:
                        dock.setParent(None)
                        dock.deleteLater()
                    except Exception:
                        pass
        except Exception:
            pass
        self.assistant = None

    def refresh_ai_controls(self):
        ai_on = bool(is_ai_enabled(refresh=True))
        if ai_on:
            self._attach_assistant()
        else:
            self._detach_assistant()

        wins = list(self._alive_quote_windows())
        try:
            for w in QApplication.topLevelWidgets():
                if isinstance(w, SistemaCotizaciones) and w not in wins:
                    wins.append(w)
        except Exception:
            pass

        for w in wins:
            try:
                if hasattr(w, "refresh_ai_features"):
                    w.refresh_ai_features()
            except Exception:
                pass
        self._apply_catalog_gate()

    def refresh_recommendations_controls(self):
        enabled = bool(is_recommendations_enabled(refresh=True))
        wins = list(self._alive_quote_windows())
        try:
            for w in QApplication.topLevelWidgets():
                if isinstance(w, SistemaCotizaciones) and w not in wins:
                    wins.append(w)
        except Exception:
            pass

        for w in wins:
            try:
                if hasattr(w, "set_recommendations_enabled"):
                    w.set_recommendations_enabled(enabled)
            except Exception:
                pass

    def refresh_status_colors(self):
        try:
            current_qid = self._selected_quote_id()
        except Exception:
            current_qid = None

        try:
            get_quote_statuses_cached(db_path=self._db_path, force_reload=True)
        except Exception:
            pass

        try:
            self._reload_current_page()
        except Exception:
            pass

        try:
            if current_qid:
                self._select_row_by_quote_id(current_qid)
        except Exception:
            pass

        try:
            for w in QApplication.topLevelWidgets():
                if isinstance(w, QuoteStatusDialog):
                    try:
                        w.reload_statuses()
                    except Exception:
                        pass
        except Exception:
            pass

        try:
            self.table.viewport().update()
        except Exception:
            pass

    def open_chat_personalization(self):
        if not is_ai_enabled(refresh=True):
            QMessageBox.information(self, "IA desactivada", "Activa la IA para personalizar el chat.")
            return

        self.refresh_ai_controls()
        dock = None
        try:
            if self.assistant is not None:
                dock = getattr(self.assistant, "dock", None)
        except Exception:
            dock = None

        if dock is None:
            QMessageBox.information(
                self,
                "Chat no disponible",
                "No encontre el panel del asistente.\n\nAbre el chat (Ctrl+K) para inicializarlo y vuelve a intentar.",
            )
            return

        try:
            dock.open_personalization_dialog()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo abrir la personalizacion:\n{e}")

    # -----------------------------

    def _preload_status_catalog(self):
        try:
            get_quote_statuses_cached(db_path=self._db_path, force_reload=False)
        except Exception:
            pass

    def _on_quote_saved(self):
        self._rt_timer.start()
        self._wake_background_api_sync()

    def _start_background_api_sync(self):
        if (self._api_sync_thread is not None) and self._api_sync_thread.is_alive():
            return
        self._api_sync_stop_event.clear()
        self._api_sync_wake_event.clear()
        self._api_sync_thread = threading.Thread(
            target=self._api_sync_loop,
            name="quote-api-sync",
            daemon=True,
        )
        self._api_sync_thread.start()

    def _wake_background_api_sync(self):
        try:
            self._api_sync_wake_event.set()
        except Exception:
            pass

    def _stop_background_api_sync(self):
        try:
            self._api_sync_stop_event.set()
            self._api_sync_wake_event.set()
            t = self._api_sync_thread
            if (t is not None) and t.is_alive():
                t.join(timeout=1.5)
        except Exception:
            pass
        finally:
            self._api_sync_thread = None

    def _api_sync_loop(self):
        interval_idle_s = 180.0
        interval_batch_s = 45.0
        interval_error_s = 300.0
        interval_disabled_s = 900.0
        batch_limit = 25
        wait_s = 25.0
        disabled_logged = False

        def _has_api_identity() -> bool:
            try:
                store_id = str(APP_CONFIG.get("store_id", "") or "").strip()
                username = str(APP_CONFIG.get("username", "") or "").strip()
                return bool(store_id and username)
            except Exception:
                return False

        while not self._api_sync_stop_event.is_set():
            self._api_sync_wake_event.wait(timeout=max(0.2, float(wait_s)))
            self._api_sync_wake_event.clear()
            if self._api_sync_stop_event.is_set():
                break

            if not _has_api_identity():
                if not disabled_logged:
                    log.info("Sync API automatico deshabilitado: falta username/store_id.")
                    disabled_logged = True
                wait_s = interval_disabled_s
                continue

            try:
                verification = verify_cotizador_signature_once()
                if bool(verification.get("blocked")):
                    detail = str(verification.get("detail") or "").strip()
                    msg = str(verification.get("message") or "Este programa fue bloqueado por un administrador.").strip()
                    if detail:
                        msg = f"{msg}\n\nDetalle tecnico:\n{detail}"
                    self.lockdown_requested.emit(msg)
                    break

                if str(verification.get("status") or "").strip().upper() == "SOFT_FAIL":
                    log.warning("Verificacion de firma no disponible: %s", verification.get("message"))

                res = sync_pending_history_quotes_once(limit=batch_limit)
                if bool(res.get("disabled")):
                    if not disabled_logged:
                        log.info("Sync API automatico deshabilitado: falta username/store_id.")
                        disabled_logged = True
                    wait_s = interval_disabled_s
                    continue

                if disabled_logged:
                    log.info("Sync API automatico habilitado: username/store_id configurados.")
                    disabled_logged = False

                found = int(res.get("found") or 0)
                sent = int(res.get("sent") or 0)
                skipped = int(res.get("skipped") or 0)
                failed = int(res.get("failed") or 0)

                if sent or failed:
                    log.info(
                        "Sync API automatico: found=%s sent=%s skipped=%s failed=%s",
                        found,
                        sent,
                        skipped,
                        failed,
                    )

                if found >= batch_limit:
                    wait_s = interval_batch_s
                elif failed > 0:
                    wait_s = interval_error_s
                else:
                    wait_s = interval_idle_s
            except Exception as e:
                log.warning("Sync API automatico fallo: %s", e)
                wait_s = interval_error_s

    def _apply_admin_lockdown(self, message: str):
        if self._lockdown_active:
            return

        self._lockdown_active = True
        self._api_sync_stop_event.set()
        self._api_sync_wake_event.set()

        app = QApplication.instance()
        if app is None:
            return

        previous_quit_mode = app.quitOnLastWindowClosed()
        app.setQuitOnLastWindowClosed(False)

        try:
            for widget in list(QApplication.topLevelWidgets()):
                if widget is None:
                    continue
                try:
                    widget.close()
                except Exception:
                    pass

            QApplication.processEvents()

            QMessageBox.critical(
                None,
                "Programa bloqueado",
                str(message or "Este programa fue bloqueado por un administrador."),
            )
        finally:
            app.setQuitOnLastWindowClosed(previous_quit_mode)
            app.quit()

    def _on_catalog_updated(self, *_):
        self._apply_catalog_gate()

    def _catalog_health(self) -> tuple[bool, str]:
        try:
            mgr = self.catalog_manager
        except Exception:
            return False, "No se pudo leer el catalogo de productos."
        try:
            if mgr is not None and hasattr(mgr, "catalog_health"):
                return mgr.catalog_health()
        except Exception:
            pass
        try:
            df = getattr(mgr, "df_productos", None)
        except Exception:
            return False, "No se pudo leer el catalogo de productos."
        return validate_products_catalog_df(df)

    def _has_products(self) -> bool:
        ok, _reason = self._catalog_health()
        return bool(ok)

    def _apply_catalog_gate(self):
        ok, reason = self._catalog_health()
        ai_on = bool(is_ai_enabled(refresh=True))
        chat_ok = ok and ai_on and (self.assistant is not None)
        self.btn_new.setEnabled(ok)
        self.btn_dup.setEnabled(ok)
        self.btn_chat.setVisible(ai_on)
        self.btn_chat.setEnabled(chat_ok)
        if ok:
            tip = "Primero importa/actualiza productos para poder abrir/crear cotizaciones."
        else:
            tip = products_update_required_message(getattr(self.catalog_manager, "df_productos", None))
            if not tip.strip():
                tip = reason or "Debes actualizar productos."
        self.btn_new.setToolTip("" if ok else tip)
        self.btn_dup.setToolTip("" if ok else tip)
        if not ai_on:
            self.btn_chat.setToolTip("Asistente desactivado por configuración (enable_ai=0).")
        elif not ok:
            self.btn_chat.setToolTip(tip)
        elif self.assistant is None:
            self.btn_chat.setToolTip("No se pudo iniciar el asistente.")
        else:
            self.btn_chat.setToolTip("Asistente (dock). Ctrl+K abre/cierra.")

    def showEvent(self, event):
        super().showEvent(event)
        if not self._centered_once:
            self._centered_once = True
            self._ensure_startup_size()
            if not self.isMaximized():
                center_on_screen(self)

    def _on_table_hovered(self, index: QModelIndex):
        try:
            delegate = getattr(self, "_history_delegate", None)
            if delegate is None:
                return
            row = index.row() if (index is not None and index.isValid()) else -1
            delegate.set_hover_row(row)
        except Exception:
            pass

    def eventFilter(self, obj, event):
        try:
            if (
                getattr(self, "table", None) is not None
                and obj is self.table.viewport()
                and event is not None
                and event.type() == QEvent.Leave
            ):
                delegate = getattr(self, "_history_delegate", None)
                if delegate is not None:
                    delegate.set_hover_row(-1)
        except Exception:
            pass
        return super().eventFilter(obj, event)

    def _ensure_startup_size(self):
        try:
            screen = self.screen() or QApplication.primaryScreen()
            if not screen:
                return
            geo = screen.availableGeometry()

            max_w = max(self._MIN_REASONABLE[0], geo.width() - 40)
            max_h = max(self._MIN_REASONABLE[1], geo.height() - 60)

            target_w = min(self._DEFAULT_SIZE[0], max_w)
            target_h = min(self._DEFAULT_SIZE[1], max_h)

            # Solo corrige si llega "chico" por layout/estilo.
            if self.width() < self._MIN_REASONABLE[0] or self.height() < self._MIN_REASONABLE[1]:
                self.resize(target_w, target_h)
        except Exception:
            pass

    def _on_filters_changed(self, *_):
        self._filter_timer.start()

    def _selected_quote_id(self) -> int | None:
        idx = self.table.selectionModel().currentIndex()
        if not idx.isValid():
            return None
        return self.model.get_id_at(idx.row())

    def _select_row_by_quote_id(self, quote_id: int):
        try:
            for i, r in enumerate(self.model.rows or []):
                if int(r.get("id") or 0) == int(quote_id):
                    self.table.selectRow(i)
                    self.table.setCurrentIndex(self.model.index(i, 0))
                    self.table.scrollTo(self.model.index(i, 0))
                    break
        except Exception:
            pass

    def _reload_first_page(self):
        self.offset = 0
        self._reload_current_page()

    def _apply_current_sort(self):
        try:
            if not self.table.isSortingEnabled():
                return
            hh = self.table.horizontalHeader()
            col = hh.sortIndicatorSection()
            order = hh.sortIndicatorOrder()
            self.model.sort(col, order)
        except Exception:
            pass

    def _reload_current_page(self):
        con = None
        try:
            con = connect(self._db_path)
            rows, total = list_quotes(
                con,
                search_text=(self.txt_search.text() or "").strip(),
                contains_product=(self.txt_prod.text() or "").strip(),
                include_deleted=False,
                limit=self.page_size,
                offset=self.offset,
            )
        except Exception as e:
            log.exception("Error listando cotizaciones")
            QMessageBox.critical(self, "Error", f"No se pudo cargar el histórico:\n{e}")
            return
        finally:
            if con is not None:
                try:
                    con.close()
                except Exception:
                    pass

        self.total = total
        self.model.set_rows(rows)
        self._apply_current_sort()

        a = self.offset + 1 if total > 0 else 0
        b = min(self.offset + self.page_size, total)
        self.lbl_page.setText(f"Mostrando {a}-{b} de {total}")

    def _prev_page(self):
        if self.offset <= 0:
            return
        self.offset = max(0, self.offset - self.page_size)
        self._reload_current_page()

    def _next_page(self):
        if self.offset + self.page_size >= self.total:
            return
        self.offset += self.page_size
        self._reload_current_page()

    def _on_table_double_clicked(self, index: QModelIndex):
        ok, reason = self._catalog_health()
        if not ok:
            msg = products_update_required_message(getattr(self.catalog_manager, "df_productos", None))
            if not msg.strip():
                msg = reason or "Debes actualizar productos."
            QMessageBox.warning(self, "Catalogo invalido", msg)
            return
        try:
            if index and index.isValid():
                self.table.setCurrentIndex(index)
                self.table.selectRow(index.row())
        except Exception:
            pass
        self._duplicate()

    def _on_table_context_menu(self, pos):
        idx = self.table.indexAt(pos)
        if idx.isValid():
            self.table.setCurrentIndex(idx)
            self.table.selectRow(idx.row())

        has_sel = self._selected_quote_id() is not None
        has_catalog = self._has_products()

        menu = QMenu(self)

        act_dup = QAction("🧾 Abrir Cotización", self)
        act_pdf = QAction("📄 Abrir PDF", self)
        act_state = QAction("🔄 Cambiar estado…", self)
        act_ticket = QAction("🖨️ Reimprimir ticket", self)
        act_regen = QAction("♻️ Regenerar PDF", self)
        act_hide = QAction("🗑️ Eliminar", self)

        act_edit_pay = None
        if APP_COUNTRY == "PERU":
            act_edit_pay = QAction("💳 Editar pago…", self)
            act_edit_pay.setEnabled(has_sel)

        act_dup.setEnabled(has_sel and has_catalog)
        act_pdf.setEnabled(has_sel)
        act_state.setEnabled(has_sel)
        act_ticket.setEnabled(has_sel)
        act_regen.setEnabled(has_sel)
        act_hide.setEnabled(has_sel)


        menu.addAction(act_dup)
        menu.addSeparator()
        menu.addAction(act_pdf)
        menu.addAction(act_state)

        if act_edit_pay is not None:
            menu.addAction(act_edit_pay)

        menu.addAction(act_ticket)
        menu.addAction(act_regen)
        menu.addSeparator()
        menu.addAction(act_hide)
        menu.addSeparator()
        picked = menu.exec(self.table.viewport().mapToGlobal(pos))
        if picked is None:
            return

        fn = None
        if picked is act_dup:
            fn = self._duplicate
        elif picked is act_pdf:
            fn = self._open_pdf
        elif picked is act_state:
            fn = self._change_status
        elif act_edit_pay is not None and picked is act_edit_pay:
            fn = self._edit_payment_peru
        elif picked is act_ticket:
            fn = self._reprint_ticket
        elif picked is act_regen:
            fn = self._regen_pdf_overwrite
        elif picked is act_hide:
            fn = self._soft_delete

        if fn is not None:
            QTimer.singleShot(120, fn)

    def _open_chat(self):
        ok, reason = self._catalog_health()
        if not ok:
            msg = products_update_required_message(getattr(self.catalog_manager, "df_productos", None))
            if not msg.strip():
                msg = reason or "Debes actualizar productos."
            QMessageBox.warning(self, "Catalogo invalido", msg)
            return
        self.refresh_ai_controls()
        if (not is_ai_enabled(refresh=True)) or (self.assistant is None):
            QMessageBox.information(self, "Chat desactivado", "El asistente está desactivado en configuración.")
            return
        try:
            self.assistant.toggle()
        except Exception:
            pass
        
    def _change_status(self):
        qid = self._selected_quote_id()
        if not qid:
            QMessageBox.information(self, "Atención", "Selecciona una cotización.")
            return

        con = None
        try:
            con = connect(self._db_path)
            header = get_quote_header(con, qid)
            current = (header.get("estado") or "").strip()
        except Exception:
            current = ""
        finally:
            if con is not None:
                try:
                    con.close()
                except Exception:
                    pass

        dlg = QuoteStatusDialog(self, current_status=current)
        if dlg.exec() != QDialog.Accepted:
            return

        new_status = dlg.status()
        if (str(new_status or "").strip()) == current:
            return

        con = None
        try:
            con = connect(self._db_path)
            with tx(con):
                update_quote_status(con, qid, new_status)
            self._reload_current_page()
            self._select_row_by_quote_id(qid)
            self._wake_background_api_sync()
        except Exception as e:
            log.exception("Error actualizando estado")
            QMessageBox.critical(self, "Error", f"No se pudo actualizar el estado:\n{e}")
        finally:
            if con is not None:
                try:
                    con.close()
                except Exception:
                    pass

    def _edit_payment_peru(self):
        if APP_COUNTRY != "PERU":
            return

        qid = self._selected_quote_id()
        if not qid:
            QMessageBox.information(self, "Atención", "Selecciona una cotización.")
            return

        con = None
        try:
            con = connect(self._db_path)
            header = get_quote_header(con, qid)
        except Exception as e:
            log.exception("Error leyendo cotización")
            QMessageBox.critical(self, "Error", f"No se pudo leer la cotización:\n{e}")
            return
        finally:
            if con is not None:
                try:
                    con.close()
                except Exception:
                    pass

        current_mp = (header.get("metodo_pago") or "").strip()

        text, ok = QInputDialog.getText(
            self,
            "Editar pago",
            "Método de pago (opcional):",
            QLineEdit.Normal,
            current_mp,
        )
        if not ok:
            return

        new_mp = (text or "").strip()
        if new_mp == current_mp:
            return

        con = None
        try:
            con = connect(self._db_path)
            with tx(con):
                update_quote_payment(con, qid, new_mp)
            self._reload_current_page()
            self._select_row_by_quote_id(qid)
            self._wake_background_api_sync()
        except Exception as e:
            log.exception("Error actualizando pago")
            QMessageBox.critical(self, "Error", f"No se pudo actualizar el pago:\n{e}")
        finally:
            if con is not None:
                try:
                    con.close()
                except Exception:
                    pass

    def _open_new_quote(self):
        ok, reason = self._catalog_health()
        if not ok:
            msg = products_update_required_message(getattr(self.catalog_manager, "df_productos", None))
            if not msg.strip():
                msg = reason or "Debes actualizar productos."
            QMessageBox.warning(self, "Catalogo invalido", msg)
            return

        win = SistemaCotizaciones(
            df_productos=self.catalog_manager.df_productos,
            df_presentaciones=self.catalog_manager.df_presentaciones,
            app_icon=self.windowIcon(),
            catalog_manager=self.catalog_manager,
            quote_events=self.quote_events,
        )

        win._history_window = self

        win.show()
        center_on_screen(win)
        self._register_open_quote_window(win)

    def _open_main_menu(self):
        MainMenuWindow.show_singleton(
            catalog_manager=self.catalog_manager,
            quote_events=self.quote_events,
            app_icon=self.windowIcon(),
            parent=self,
        )

    def _open_config_dialog(self):
        dlg = HistoryConfigDialog(self)
        dlg.exec()
        self.refresh_ai_controls()
        self.refresh_recommendations_controls()

    def _open_pdf(self):
        qid = self._selected_quote_id()
        if not qid:
            QMessageBox.information(self, "Atención", "Selecciona una cotización.")
            return
        try:
            con = connect(self._db_path)

            q = get_quote_header(con, qid)

            pdf = resolve_pdf_path_portable(q.get("pdf_path"))
            if not pdf or not os.path.exists(pdf):
                pdf_name = os.path.basename(pdf) if pdf else "(sin ruta)"
                pdf_dir = os.path.dirname(pdf) if pdf else ""
                mb = QMessageBox(self)
                mb.setIcon(QMessageBox.Warning)
                mb.setWindowTitle("PDF no encontrado")
                mb.setText("No se encontró el archivo PDF.")
                mb.setInformativeText(
                    f"Archivo: {pdf_name}"
                    + (f"\nUbicación: {pdf_dir}" if pdf_dir else "")
                )
                if pdf:
                    mb.setDetailedText(pdf)
                mb.exec()
                return
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(pdf)))
        except Exception as e:
            log.exception("Error abriendo PDF")
            QMessageBox.critical(self, "Error", f"No se pudo abrir el PDF:\n{e}")

    def _duplicate(self):
        ok, reason = self._catalog_health()
        if not ok:
            msg = products_update_required_message(getattr(self.catalog_manager, "df_productos", None))
            if not msg.strip():
                msg = reason or "Debes actualizar productos."
            QMessageBox.warning(self, "Catalogo invalido", msg)
            return

        qid = self._selected_quote_id()
        if not qid:
            QMessageBox.information(self, "Atención", "Selecciona una cotización.")
            return
        try:
            con = connect(self._db_path)

            header = get_quote_header(con, qid)
            items_base, _items_shown = get_quote_items(con, qid)

            payload = {
                "cliente": header.get("cliente", ""),
                "cedula": header.get("cedula", ""),
                "tipo_documento": header.get("tipo_documento", ""),
                "telefono": header.get("telefono", ""),
                "direccion": header.get("direccion", ""),
                "email": header.get("email", ""),
                "items_base": items_base,
            }

            win = SistemaCotizaciones(
                df_productos=self.catalog_manager.df_productos,
                df_presentaciones=self.catalog_manager.df_presentaciones,
                app_icon=self.windowIcon(),
                catalog_manager=self.catalog_manager,
                quote_events=self.quote_events,
            )

            win._history_window = self

            if APP_COUNTRY == "PARAGUAY":
                mp = (header.get("metodo_pago") or "").strip().lower()
                is_cash = (mp == "efectivo")

                try:
                    if getattr(win, "btn_pay_cash", None) is not None:
                        win.btn_pay_cash.blockSignals(True)
                    if getattr(win, "btn_pay_card", None) is not None:
                        win.btn_pay_card.blockSignals(True)

                    if is_cash and getattr(win, "btn_pay_cash", None) is not None:
                        win.btn_pay_cash.setChecked(True)
                    elif getattr(win, "btn_pay_card", None) is not None:
                        win.btn_pay_card.setChecked(True)
                finally:
                    try:
                        if getattr(win, "btn_pay_cash", None) is not None:
                            win.btn_pay_cash.blockSignals(False)
                        if getattr(win, "btn_pay_card", None) is not None:
                            win.btn_pay_card.blockSignals(False)
                    except Exception:
                        pass

                try:
                    if hasattr(win, "_set_py_cash_mode"):
                        win._set_py_cash_mode(is_cash, assume_items_already=True)
                except Exception:
                    pass

            elif APP_COUNTRY == "PERU":
                mp = (header.get("metodo_pago") or "")
                try:
                    if getattr(win, "entry_metodo_pago", None) is not None:
                        win.entry_metodo_pago.setText(mp)
                except Exception:
                    pass

            win.show()
            center_on_screen(win)
            self._register_open_quote_window(win)

            win.load_from_history_payload(payload)

            if APP_COUNTRY == "PARAGUAY":
                mp = (header.get("metodo_pago") or "").strip().lower()
                is_cash = (mp == "efectivo")
                try:
                    if hasattr(win, "_set_py_cash_mode"):
                        win._set_py_cash_mode(is_cash, assume_items_already=True)
                except Exception:
                    pass

        except Exception as e:
            log.exception("Error duplicando cotización")
            QMessageBox.critical(self, "Error", f"No se pudo duplicar:\n{e}")

    def _soft_delete(self):
        qid = self._selected_quote_id()
        if not qid:
            QMessageBox.information(self, "Atención", "Selecciona una cotización.")
            return

        if QMessageBox.question(self, "Eliminar", "Eliminar esta cotización del historial?") != QMessageBox.Yes:
            return

        try:
            con = connect(self._db_path)

            with tx(con):
                soft_delete_quote(con, qid, datetime.datetime.now().isoformat(timespec="seconds"))

            self._reload_first_page()
        except Exception as e:
            log.exception("Error eliminando cotización")
            QMessageBox.critical(self, "Error", f"No se pudo eliminar:\n{e}")

    def _reprint_ticket(self):
        qid = self._selected_quote_id()
        if not qid:
            QMessageBox.information(self, "Atención", "Selecciona una cotización.")
            return

        try:
            con = connect(self._db_path)

            header = get_quote_header(con, qid)
            _items_base, items_shown = get_quote_items(con, qid)

            pdf_path = resolve_pdf_path_portable(header.get("pdf_path"))
            cliente = header.get("cliente", "")
            quote_code = format_quote_code(
                country_code=header.get("country_code") or COUNTRY_CODE,
                store_id=STORE_ID,
                quote_no=header.get("quote_no"),
                width=7,
            )

            ticket_paths = generar_ticket_para_cotizacion(
                pdf_path=pdf_path,
                items_pdf=items_shown,
                quote_code=quote_code,
                country=header.get("country_code") or APP_COUNTRY,
                cliente_nombre=cliente,
                printer_name="TICKERA",
                width=48,
                top_mm=0.0,
                bottom_mm=10.0,
                cut_mode="full_feed",
            )

            cmd = ticket_paths.get("ticket_cmd")
            if cmd and os.path.exists(cmd):
                QMessageBox.information(self, "Ticket", f"Se creó el .cmd:\n{cmd}\n\nDoble click para imprimir.")
                QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(os.path.dirname(cmd))))
            else:
                QMessageBox.warning(self, "Ticket", "No se pudo generar el ticket.")
        except Exception as e:
            log.exception("Error reimprimiendo ticket")
            QMessageBox.critical(self, "Error", f"No se pudo reimprimir el ticket:\n{e}")

    def _regen_pdf_overwrite(self):
        qid = self._selected_quote_id()
        if not qid:
            QMessageBox.information(self, "Atención", "Selecciona una cotización.")
            return

        try:
            con = connect(self._db_path)

            header = get_quote_header(con, qid)
            _items_base, items_shown = get_quote_items(con, qid)

            old_out_path = resolve_pdf_path_portable(header.get("pdf_path"))
            if not old_out_path:
                QMessageBox.warning(self, "Error", "La cotización no tiene ruta de PDF.")
                return

            quote_code = format_quote_code(
                country_code=header.get("country_code") or COUNTRY_CODE,
                store_id=STORE_ID,
                quote_no=header.get("quote_no"),
                width=7,
            )

            old_base = os.path.splitext(os.path.basename(old_out_path))[0]
            if "_" in old_base:
                suffix = old_base.split("_", 1)[1].strip()
            else:
                cli_slug = re.sub(r"[^A-Za-z0-9_-]+", "_", str(header.get("cliente") or "").strip()).strip("_")
                suffix = cli_slug or "cliente"

            new_filename = f"C-{quote_code}_{suffix}.pdf"
            new_out_path = os.path.join(os.path.dirname(old_out_path), new_filename)

            metodo_pago = (header.get("metodo_pago") or "").strip()
            if APP_COUNTRY == "PERU":
                pass
            elif APP_COUNTRY == "PARAGUAY":
                if not metodo_pago:
                    metodo_pago = "Tarjeta"
            else:
                if not metodo_pago:
                    metodo_pago = "Transferencia"

            datos = {
                "fecha": (header.get("created_at", "") or "")[:10],
                "cliente": header.get("cliente", ""),
                "cedula": header.get("cedula", ""),
                "telefono": header.get("telefono", ""),
                "metodo_pago": metodo_pago,
                "items": items_shown,
                "subtotal_bruto": float(nz(header.get("subtotal_bruto_shown"), 0.0)),
                "descuento_total": float(nz(header.get("descuento_total_shown"), 0.0)),
                "total_general": float(nz(header.get("total_neto_shown"), 0.0)),
            }

            generar_pdf(datos, fixed_quote_no=quote_code, out_path=new_out_path)

            con = connect(self._db_path)

            with tx(con):
                con.execute(
                    "UPDATE quotes SET quote_no = ?, pdf_path = ? WHERE id = ?",
                    (quote_code, os.path.basename(new_out_path), int(qid)),
                )

            if os.path.abspath(old_out_path) != os.path.abspath(new_out_path):
                try:
                    if os.path.exists(old_out_path):
                        os.remove(old_out_path)
                except Exception:
                    pass

            self._reload_current_page()
            self._select_row_by_quote_id(qid)

            pdf_name = os.path.basename(new_out_path)
            pdf_dir = os.path.dirname(new_out_path)
            QMessageBox.information(
                self,
                "PDF regenerado",
                f"PDF actualizado:\n{pdf_name}\n\nUbicación:\n{pdf_dir}\n\nCódigo: {quote_code}",
            )
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(COTIZACIONES_DIR)))
        except Exception as e:
            log.exception("Error regenerando PDF")
            QMessageBox.critical(self, "Error", f"No se pudo regenerar el PDF:\n{e}")

    def _regen_pdf_overwrite_for_quote_id(self, qid: int) -> tuple[str, str]:
        con = None
        try:
            con = connect(self._db_path)
            header = get_quote_header(con, int(qid))
            _items_base, items_shown = get_quote_items(con, int(qid))
        finally:
            if con is not None:
                try:
                    con.close()
                except Exception:
                    pass

        old_out_path = resolve_pdf_path_portable(header.get("pdf_path"))
        if not old_out_path:
            raise RuntimeError("La cotización no tiene ruta de PDF.")

        quote_code = format_quote_code(
            country_code=header.get("country_code") or COUNTRY_CODE,
            store_id=STORE_ID,
            quote_no=header.get("quote_no"),
            width=7,
        )

        old_base = os.path.splitext(os.path.basename(old_out_path))[0]
        if "_" in old_base:
            suffix = old_base.split("_", 1)[1].strip()
        else:
            cli_slug = re.sub(r"[^A-Za-z0-9_-]+", "_", str(header.get("cliente") or "").strip()).strip("_")
            suffix = cli_slug or "cliente"

        new_filename = f"C-{quote_code}_{suffix}.pdf"
        new_out_path = os.path.join(os.path.dirname(old_out_path), new_filename)

        metodo_pago = (header.get("metodo_pago") or "").strip()
        if APP_COUNTRY == "PERU":
            pass
        elif APP_COUNTRY == "PARAGUAY":
            if not metodo_pago:
                metodo_pago = "Tarjeta"
        else:
            if not metodo_pago:
                metodo_pago = "Transferencia"

        datos = {
            "fecha": (header.get("created_at", "") or "")[:10],
            "cliente": header.get("cliente", ""),
            "cedula": header.get("cedula", ""),
            "telefono": header.get("telefono", ""),
            "metodo_pago": metodo_pago,
            "items": items_shown,
            "subtotal_bruto": float(nz(header.get("subtotal_bruto_shown"), 0.0)),
            "descuento_total": float(nz(header.get("descuento_total_shown"), 0.0)),
            "total_general": float(nz(header.get("total_neto_shown"), 0.0)),
        }

        generar_pdf(datos, fixed_quote_no=quote_code, out_path=new_out_path)

        con = None
        try:
            con = connect(self._db_path)
            with tx(con):
                con.execute(
                    "UPDATE quotes SET quote_no = ?, pdf_path = ? WHERE id = ?",
                    (quote_code, os.path.basename(new_out_path), int(qid)),
                )
        finally:
            if con is not None:
                try:
                    con.close()
                except Exception:
                    pass

        if os.path.abspath(old_out_path) != os.path.abspath(new_out_path):
            try:
                if os.path.exists(old_out_path):
                    os.remove(old_out_path)
            except Exception:
                pass

        return quote_code, new_out_path

    def _regen_pdf_and_cmd_for_quote_id(self, qid: int) -> tuple[str, str, str]:
        quote_code, new_pdf_path = self._regen_pdf_overwrite_for_quote_id(int(qid))
        if not new_pdf_path or not os.path.exists(new_pdf_path):
            raise RuntimeError("No se pudo regenerar el PDF.")

        con = None
        try:
            con = connect(self._db_path)
            header = get_quote_header(con, int(qid))
            _items_base, items_shown = get_quote_items(con, int(qid))
        finally:
            if con is not None:
                try:
                    con.close()
                except Exception:
                    pass

        ticket_paths = generar_ticket_para_cotizacion(
            pdf_path=new_pdf_path,
            items_pdf=items_shown,
            quote_code=quote_code,
            country=header.get("country_code") or APP_COUNTRY,
            cliente_nombre=header.get("cliente", ""),
            printer_name="TICKERA",
            width=48,
            top_mm=0.0,
            bottom_mm=10.0,
            cut_mode="full_feed",
        )
        cmd_path = str(ticket_paths.get("ticket_cmd") or "").strip()
        if not cmd_path or not os.path.exists(cmd_path):
            raise RuntimeError("No se pudo regenerar el archivo CMD del ticket.")

        return quote_code, new_pdf_path, cmd_path

    def closeEvent(self, event: QCloseEvent):
        if self._lockdown_active:
            self._stop_background_api_sync()
            self._save_window_state()
            event.accept()
            return

        if self._closing_with_children:
            self._stop_background_api_sync()
            self._save_window_state()
            event.accept()
            return

        self._prune_open_windows()
        open_wins = self._alive_quote_windows()

        if not open_wins:
            self._stop_background_api_sync()
            self._save_window_state()
            event.accept()
            return

        n = len(open_wins)
        plural = (n != 1)

        mb = QMessageBox(self)
        mb.setIcon(QMessageBox.Warning)
        mb.setWindowTitle("Cerrar histórico")
        mb.setText(
            f"Hay {n} cotización{'es' if plural else ''} abierta{'s' if plural else ''}.\n\n"
            f"Si cierras el histórico, se cerrarán {'todas' if plural else 'la'} "
            f"{'las' if plural else 'la'} cotización{'es' if plural else ''} abierta{'s' if plural else ''}."
        )
        btn_close_all = mb.addButton("Cerrar todas", QMessageBox.AcceptRole)
        btn_cancel = mb.addButton("Cancelar", QMessageBox.RejectRole)
        mb.setDefaultButton(btn_cancel)

        mb.exec()

        if mb.clickedButton() != btn_close_all:
            event.ignore()
            return

        self._closing_with_children = True
        ok = self._close_all_quotes()
        self._closing_with_children = False

        if not ok:
            QMessageBox.warning(
                self,
                "Atención",
                "No se pudo cerrar el histórico porque aún hay cotizaciones abiertas."
            )
            event.ignore()
            return

        self._save_window_state()
        self._stop_background_api_sync()
        event.accept()

    @staticmethod
    def _parse_int(value, default: int = 0) -> int:
        try:
            return int(str(value).strip())
        except Exception:
            return int(default)

    @staticmethod
    def _parse_bool(value) -> bool:
        s = str(value or "").strip().lower()
        return s in ("1", "true", "yes", "on", "si")

    def _restore_window_state(self):
        con = None
        try:
            con = connect(self._db_path)
            p = self._WIN_KEY_PREFIX
            w = self._parse_int(get_setting(con, f"{p}_w", "0"), 0)
            h = self._parse_int(get_setting(con, f"{p}_h", "0"), 0)
            x = self._parse_int(get_setting(con, f"{p}_x", "-1"), -1)
            y = self._parse_int(get_setting(con, f"{p}_y", "-1"), -1)
            is_max = self._parse_bool(get_setting(con, f"{p}_max", "0"))
        except Exception:
            return
        finally:
            if con is not None:
                try:
                    con.close()
                except Exception:
                    pass

        if w > 100 and h > 100:
            self.resize(w, h)
            self._window_state_restored = True
        if x >= 0 and y >= 0:
            self.move(x, y)
            self._window_state_restored = True
        if is_max:
            self.showMaximized()
            self._window_state_restored = True

    def _save_window_state(self):
        try:
            geo = self.normalGeometry() if self.isMaximized() else self.geometry()
            w = int(geo.width())
            h = int(geo.height())
            x = int(geo.x())
            y = int(geo.y())
            is_max = bool(self.isMaximized())
        except Exception:
            return

        if w <= 100 or h <= 100:
            return

        con = None
        try:
            con = connect(self._db_path)
            p = self._WIN_KEY_PREFIX
            with tx(con):
                set_setting(con, f"{p}_w", str(w))
                set_setting(con, f"{p}_h", str(h))
                set_setting(con, f"{p}_x", str(x))
                set_setting(con, f"{p}_y", str(y))
                set_setting(con, f"{p}_max", "1" if is_max else "0")
        except Exception:
            pass
        finally:
            if con is not None:
                try:
                    con.close()
                except Exception:
                    pass

