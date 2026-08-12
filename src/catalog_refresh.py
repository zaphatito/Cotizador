from __future__ import annotations

from collections.abc import Mapping
from typing import Any


_PRICE_KEYS = ("p_max", "p_min", "p_oferta", "precio_venta")


def historical_product_for_item(
    product: Mapping[str, Any] | None,
    historical_item: Mapping[str, Any],
) -> dict[str, Any]:
    """Usa tiers vigentes y solo sintetiza tiers si el SKU ya no existe."""
    if isinstance(product, Mapping) and product:
        return dict(product)

    try:
        snapshot_price = float(historical_item.get("precio") or 0.0)
    except (TypeError, ValueError):
        return {}
    if snapshot_price <= 0:
        return {}

    try:
        snapshot_price_id = int(historical_item.get("id_precioventa") or 1)
    except (TypeError, ValueError):
        snapshot_price_id = 1
    if snapshot_price_id not in (1, 2, 3):
        snapshot_price_id = 1

    return {
        "categoria": str(historical_item.get("categoria") or "").strip().upper(),
        "p_max": snapshot_price,
        "p_min": snapshot_price,
        "p_oferta": snapshot_price,
        "precio_venta": snapshot_price_id,
    }


def refreshed_product_for_item(
    product: Mapping[str, Any],
    previous_product: Mapping[str, Any] | None,
    *,
    preserve_prices: bool,
) -> dict[str, Any]:
    """Actualiza metadatos/stock sin perder el snapshot de precios del renglón."""
    refreshed = dict(product)
    if preserve_prices and isinstance(previous_product, Mapping):
        for key in _PRICE_KEYS:
            if key in previous_product:
                refreshed[key] = previous_product[key]
    return refreshed


__all__ = ["historical_product_for_item", "refreshed_product_for_item"]
