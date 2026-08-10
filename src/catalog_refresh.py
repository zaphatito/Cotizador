from __future__ import annotations

from collections.abc import Mapping
from typing import Any


_PRICE_KEYS = ("p_max", "p_min", "p_oferta", "precio_venta")


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


__all__ = ["refreshed_product_for_item"]
