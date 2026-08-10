from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any
import uuid

from sqlModels.api_identity import resolve_api_identity

from .cases import API_CASE_BOOTSTRAP_COTIZADOR_CONFIGURATION
from .controller import post


class InitialConfigurationApiError(RuntimeError):
    pass


def _auth_headers(token: str) -> dict[str, str]:
    clean_token = str(token or "").strip()
    if not clean_token:
        raise InitialConfigurationApiError("El login no devolvió un token de acceso.")
    return {"Authorization": f"Bearer {clean_token}"}


def _mapping(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise InitialConfigurationApiError(f"{label} no es un objeto JSON.")
    return dict(value)


def _extract_response(value: Any) -> dict[str, Any]:
    payload = _mapping(value, label="La respuesta de configuración")
    for key in ("data", "result", "payload"):
        nested = payload.get(key)
        if isinstance(nested, Mapping):
            nested_dict = dict(nested)
            if any(
                candidate in nested_dict
                for candidate in (
                    "configuration",
                    "configuracion",
                    "manifest_revision",
                    "receipt",
                    "idempotency_key",
                )
            ):
                return nested_dict
    return payload


def build_bootstrap_request(
    setup_payload: Mapping[str, Any],
    *,
    pid: str,
) -> dict[str, Any]:
    payload = _mapping(setup_payload, label="La configuración inicial")
    try:
        schema_version = int(payload.get("schema_version"))
    except (TypeError, ValueError) as exc:
        raise ValueError("schema_version debe ser 1.") from exc
    if schema_version != 1:
        raise ValueError("schema_version no soportada; se esperaba 1.")
    try:
        uuid.UUID(str(payload.get("idempotency_key") or ""))
    except (ValueError, AttributeError) as exc:
        raise ValueError("idempotency_key debe ser un UUID.") from exc
    clean_pid = str(pid or "").strip()
    if not clean_pid:
        raise ValueError("pid no puede estar vacío.")
    identity = _mapping(payload.get("identity"), label="identity")
    if not str(identity.get("username") or "").strip():
        raise ValueError("identity.username es obligatorio.")
    if not str(identity.get("id_cotizador") or "").strip():
        raise ValueError("identity.id_cotizador es obligatorio.")
    if not isinstance(payload.get("assignments"), list) or not payload["assignments"]:
        raise ValueError("assignments debe contener al menos un país/empresa/tienda.")
    return {**payload, "pid": clean_pid}


def bootstrap_initial_configuration(
    setup_payload: Mapping[str, Any],
    *,
    login_password: str | None = None,
    post_fn: Callable[..., Any] = post,
    login_fn: Callable[..., tuple[str, Any]] | None = None,
    pid_fn: Callable[[], str] | None = None,
) -> dict[str, Any]:
    # Preflight puro: una identidad offline/incompleta falla antes de importar
    # autenticación, generar PID o ejecutar cualquier operación de red.
    validated_payload = build_bootstrap_request(
        setup_payload,
        pid="preflight-local-only",
    )
    default_scope = _mapping(
        validated_payload.get("default_scope"),
        label="default_scope",
    )
    country = str(
        default_scope.get("country") or default_scope.get("country_code") or ""
    ).strip()
    company = str(default_scope.get("company_type") or "").strip()
    user_id, api_username = resolve_api_identity(country, company)

    if login_fn is None or pid_fn is None:
        from .presupuesto_client import _load_or_create_cotizador_pid, _login_api

        login_fn = login_fn or _login_api
        pid_fn = pid_fn or _load_or_create_cotizador_pid

    try:
        assert login_fn is not None
        assert pid_fn is not None
        request_payload = build_bootstrap_request(
            validated_payload,
            pid=pid_fn(),
        )
        token, _login_response = login_fn(
            user_id=int(user_id),
            api_username=str(api_username),
            login_password=login_password,
        )
        response = post_fn(
            API_CASE_BOOTSTRAP_COTIZADOR_CONFIGURATION,
            json_data=request_payload,
            headers=_auth_headers(token),
            expected_status=(200, 201),
            timeout=120,
            raise_for_status=True,
        )
    except Exception as exc:
        raise InitialConfigurationApiError(str(exc)) from exc

    result = _extract_response(getattr(response, "data", None))
    result.setdefault("http_status", int(getattr(response, "status_code", 0) or 0))
    return result


__all__ = [
    "InitialConfigurationApiError",
    "bootstrap_initial_configuration",
    "build_bootstrap_request",
]
