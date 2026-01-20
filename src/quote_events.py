# src/quote_events.py
from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class QuoteEvents(QObject):
    quote_saved = Signal()      # cuando se guarda una cotización en DB
    rates_updated = Signal()    # cuando se actualizan tasas en DB
