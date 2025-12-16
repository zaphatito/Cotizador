# src/app_window_parts/main.py
from __future__ import annotations

import os
import pandas as pd

from PySide6.QtWidgets import QMainWindow
from PySide6.QtGui import QIcon

from ..paths import BASE_APP_TITLE, DATA_DIR
from ..config import (
    APP_CURRENCY,
    SECONDARY_CURRENCY,
    get_secondary_currencies,
    set_currency_context,
)
from ..logging_setup import get_logger

from .ui import UiMixin
from .currency import CurrencyMixin
from .completer import CompleterMixin
from .add_items import AddItemsMixin
from .presentations import PresentationsMixin
from .table_actions import TableActionsMixin
from .pdf_actions import PdfActionsMixin

log = get_logger(__name__)


class SistemaCotizaciones(
    UiMixin,
    CurrencyMixin,
    CompleterMixin,
    AddItemsMixin,
    PresentationsMixin,
    TableActionsMixin,
    PdfActionsMixin,
    QMainWindow,
):
    def __init__(
        self,
        df_productos: pd.DataFrame,
        df_presentaciones: pd.DataFrame,
        app_icon: QIcon,
    ):
        super().__init__()
        self.setWindowTitle(BASE_APP_TITLE)
        self.resize(980, 640)
        if not app_icon.isNull():
            self.setWindowIcon(app_icon)

        self.productos = df_productos.to_dict("records")
        self.presentaciones = df_presentaciones.to_dict("records")
        self.items: list[dict] = []
        self._suppress_next_return = False
        self._ignore_completer = False
        self._shown_once = False
        self._app_icon = app_icon
        self._ctx_row = None

        # === Moneda / tasa ===
        self.base_currency = APP_CURRENCY
        self.secondary_currency = SECONDARY_CURRENCY
        self.secondary_currencies = [c.upper() for c in (get_secondary_currencies() or []) if c]
        self._tasa_path = os.path.join(DATA_DIR, "tasa.txt")
        self._rates: dict[str, float] = self._load_exchange_rate_file()
        set_currency_context(self.base_currency, 1.0)

        # PCs visibles: códigos que empiezan por "PC" y categoría "OTROS"
        self._botellas_pc = [
            p
            for p in self.productos
            if str(p.get("id", "")).upper().startswith("PC")
            and (p.get("categoria", "").upper() == "OTROS")
        ]

        log.info(
            "Ventana iniciada. productos=%d presentaciones=%d botellasPC=%d tasas=%s",
            len(self.productos),
            len(self.presentaciones),
            len(self._botellas_pc),
            self._rates,
        )

        self._build_ui()
        self.entry_cliente.textChanged.connect(self._update_title_with_client)
        self._update_title_with_client(self.entry_cliente.text())
        self._build_completer()

        self.model.item_added.connect(self._focus_last_row)
