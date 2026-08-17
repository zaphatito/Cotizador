from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .catalog_context import CatalogScope, QuoteContext
from .config import currency_for_country
from .country_rules import country_code_for


def is_legacy_quote_context(header: Mapping[str, Any]) -> bool:
    try:
        return int(header.get("quote_context_version") or 0) < 1
    except (TypeError, ValueError):
        return True


def build_quote_context(catalog_manager: Any, scope: CatalogScope) -> QuoteContext:
    record = dict(catalog_manager.scope_record(scope) or {})
    country_code = country_code_for(scope.country_code)
    country_currency = str(currency_for_country(country_code) or "").strip().upper()
    manifest_currency = str(
        record.get("base_currency") or record.get("currency") or ""
    ).strip().upper()

    # El país manda para Bolivia: evita que una configuración de catálogo
    # histórica con PYG contamine la ventana de cotización y sus conversiones.
    base_currency = (
        country_currency
        if country_code == "BO"
        else (manifest_currency or country_currency)
    )
    return QuoteContext.from_values(
        country_code=country_code,
        company_type=scope.company_type,
        username=str(getattr(catalog_manager, "username", "") or ""),
        id_cotizador=str(getattr(catalog_manager, "id_cotizador", "") or ""),
        base_currency=base_currency,
    )


def resolve_historical_quote_scope(
    header: Mapping[str, Any],
    available_scopes: Iterable[CatalogScope],
    *,
    default_country_code: str,
) -> tuple[CatalogScope | None, bool]:
    """Resuelve el scope histórico sin ampliar las asignaciones actuales.

    Las cotizaciones creadas antes del contexto multidominio guardaron país y
    empresa mediante un backfill. Si ese valor inferido no coincide, se admite
    el único scope actualmente autorizado del mismo país. Los contextos modernos
    son explícitos y conservan el bloqueo cuando su scope fue retirado.
    """
    scopes = tuple(
        scope for scope in available_scopes if isinstance(scope, CatalogScope)
    )
    country_code = country_code_for(
        header.get("country_code"),
        default=default_country_code,
    )
    company_type = str(header.get("company_type") or "").strip()

    if company_type:
        historical_scope = CatalogScope(
            country_code=country_code,
            company_type=company_type,
        )
        if historical_scope in scopes:
            return historical_scope, False

    if not is_legacy_quote_context(header):
        return None, False

    country_scopes = tuple(
        scope
        for scope in scopes
        if country_code_for(scope.country_code) == country_code
    )
    if len(country_scopes) == 1:
        return country_scopes[0], True
    return None, False


def resolve_historical_quote_owner(
    header: Mapping[str, Any],
    *,
    current_username: str,
    current_id_cotizador: str,
) -> tuple[str, str] | None:
    current_username = str(current_username or "").strip()
    current_id = str(current_id_cotizador or "").strip()
    if is_legacy_quote_context(header):
        return current_username, current_id

    historical_username = str(header.get("cotizador_username") or "").strip()
    historical_id = str(header.get("id_cotizador") or "").strip()
    if (
        historical_username
        and historical_username.casefold() != current_username.casefold()
    ) or (historical_id and historical_id.casefold() != current_id.casefold()):
        return None
    return historical_username or current_username, historical_id or current_id
