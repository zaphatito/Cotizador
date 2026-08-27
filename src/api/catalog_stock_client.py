from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

from sqlModels.api_identity import resolve_api_identity

from ..config import APP_CONFIG
from .cases import API_CASE_GET_COTIZADOR_CATALOG_STOCK
from .controller import post
from .generic_controller import ApiRequestError
from .presupuesto_client import (
    PresupuestoApiError,
    _auth_headers,
    _load_or_create_cotizador_pid,
    _login_api,
)


class CatalogStockApiError(RuntimeError):
    pass


def _extract_sync_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CatalogStockApiError("EFAPI no devolvio un objeto JSON para catalogo y stock.")

    payload = dict(value)
    if any(key in payload for key in ("manifest_revision", "groups", "grupos")):
        return payload

    for key in ("data", "result", "payload"):
        nested = payload.get(key)
        if isinstance(nested, Mapping):
            try:
                return _extract_sync_payload(nested)
            except CatalogStockApiError:
                continue
    raise CatalogStockApiError("La respuesta de EFAPI no contiene el manifiesto de catalogo y stock.")


def build_catalog_stock_request(*, pid: str, known_state: Mapping[str, Any] | None) -> dict[str, Any]:
    clean_pid = str(pid or "").strip()
    if not clean_pid:
        raise ValueError("pid no puede estar vacio.")
    if known_state is not None and not isinstance(known_state, Mapping):
        raise TypeError("known_state debe ser un mapping.")

    state = dict(known_state or {})
    known_groups = state.get("known_groups") or []
    if not isinstance(known_groups, list):
        raise ValueError("known_groups debe ser una lista.")
    request = {
        "pid": clean_pid,
        "pricing_version": 2,
        "manifest_revision": str(state.get("manifest_revision") or ""),
        "known_groups": known_groups,
    }
    configuration_revision = str(state.get("configuration_revision") or "").strip()
    if configuration_revision:
        request["configuration_revision"] = configuration_revision
    return request


def fetch_catalog_stock(
    known_state: Mapping[str, Any] | None,
    *,
    login_password: str | None = None,
    post_fn: Callable[..., Any] = post,
) -> dict[str, Any]:
    """Obtiene un delta incremental usando el login y transporte API existentes."""

    country = str(APP_CONFIG.get("country") or "").strip()
    company_type = str(APP_CONFIG.get("company_type") or "").strip()
    user_id, api_username = resolve_api_identity(country, company_type)

    try:
        token, _login_response = _login_api(
            user_id=int(user_id),
            api_username=str(api_username),
            login_password=login_password,
        )
        request_payload = build_catalog_stock_request(
            pid=_load_or_create_cotizador_pid(),
            known_state=known_state,
        )
        response = post_fn(
            API_CASE_GET_COTIZADOR_CATALOG_STOCK,
            json_data=request_payload,
            headers=_auth_headers(token),
            expected_status=(200,),
            timeout=30,
            raise_for_status=True,
        )
    except (ApiRequestError, PresupuestoApiError, ValueError, TypeError) as exc:
        raise CatalogStockApiError(str(exc)) from exc

    return _extract_sync_payload(getattr(response, "data", None))


__all__ = [
    "CatalogStockApiError",
    "build_catalog_stock_request",
    "fetch_catalog_stock",
]
