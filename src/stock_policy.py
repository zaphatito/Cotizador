from __future__ import annotations

from typing import Any

from .server_identity import has_complete_server_identity


INFORMATIONAL_STOCK_POLICY = "INFORMATIONAL"


def has_insufficient_stock(
    *,
    quantity: Any,
    available: Any,
    factor: Any = 1.0,
) -> bool:
    """Indica si la cantidad requerida supera un stock conocido, incluido cero."""
    try:
        required = float(quantity) * float(factor)
        stock = float(available)
    except (TypeError, ValueError):
        return False

    # Los valores negativos se usan como centinela cuando el stock es desconocido.
    return stock >= 0.0 and required > stock


def should_enforce_stock(
    *,
    username: str,
    allow_no_stock: bool,
    id_cotizador: str = "",
    stock_policy: str = "",
) -> bool:
    """Devuelve si la disponibilidad debe bloquear acciones de catálogo."""
    if str(stock_policy or "").strip().upper() == INFORMATIONAL_STOCK_POLICY:
        return False
    if has_complete_server_identity(username, id_cotizador):
        return False
    return not bool(allow_no_stock)


def stock_enforcement_enabled(context: Any = None) -> bool:
    """Resuelve la política runtime preservando el modo Excel legado."""
    from .config import ALLOW_NO_STOCK, APP_USERNAME, STORE_ID

    return should_enforce_stock(
        username=str(getattr(context, "username", "") or APP_USERNAME or ""),
        id_cotizador=str(
            getattr(context, "id_cotizador", "") or STORE_ID or ""
        ),
        allow_no_stock=bool(ALLOW_NO_STOCK),
        stock_policy=str(getattr(context, "stock_policy", "") or ""),
    )
