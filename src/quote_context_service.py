from __future__ import annotations

from typing import Any

from .catalog_context import CatalogScope, QuoteContext
from .config import currency_for_country


def build_quote_context(catalog_manager: Any, scope: CatalogScope) -> QuoteContext:
    record = dict(catalog_manager.scope_record(scope) or {})
    base_currency = str(
        record.get("base_currency")
        or record.get("currency")
        or currency_for_country(scope.country_code)
    ).strip().upper()
    return QuoteContext.from_values(
        country_code=scope.country_code,
        company_type=scope.company_type,
        username=str(getattr(catalog_manager, "username", "") or ""),
        id_cotizador=str(getattr(catalog_manager, "id_cotizador", "") or ""),
        base_currency=base_currency,
    )
