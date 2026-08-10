from __future__ import annotations

import math
from copy import deepcopy


HISTORY_BASE_SNAPSHOT_KEYS = (
    "codigo",
    "categoria",
    "tipo_prod",
    "cantidad",
    "factor_total",
    "precio",
    "subtotal_base",
    "descuento_mode",
    "descuento_pct",
    "descuento_monto",
    "total",
    "precio_override",
    "precio_tier",
    "id_precioventa",
)


def history_base_snapshot(item: dict) -> dict:
    """Captura los campos editables que determinan los importes mostrados."""
    return {key: deepcopy(item.get(key)) for key in HISTORY_BASE_SNAPSHOT_KEYS}


def matching_history_shown_snapshot(
    item: dict,
    *,
    currency: object,
    rate: object,
    display_snapshot: dict | None = None,
) -> dict | None:
    shown_snapshot = item.get("_history_shown_snapshot")
    base_snapshot = item.get("_history_base_snapshot")
    historical_display = display_snapshot or item.get("_history_display_snapshot")
    if not all(
        isinstance(value, dict)
        for value in (shown_snapshot, base_snapshot, historical_display)
    ):
        return None

    current_currency = str(currency or "").strip().upper()
    snapshot_currency = str(
        historical_display.get("currency") or ""
    ).strip().upper()
    try:
        current_rate = float(rate)
        snapshot_rate = float(historical_display.get("rate"))
    except (TypeError, ValueError):
        return None

    if current_currency != snapshot_currency or not math.isclose(
        current_rate,
        snapshot_rate,
        rel_tol=1e-12,
        abs_tol=1e-12,
    ):
        return None
    if history_base_snapshot(item) != base_snapshot:
        return None
    return shown_snapshot
