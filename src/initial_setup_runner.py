from __future__ import annotations

import datetime as _datetime
import os
from collections.abc import Mapping
from typing import Any

from sqlModels import catalog_cache_repo
from sqlModels.db import connect, ensure_schema, tx
from sqlModels.settings_repo import set_setting

from .initial_setup import (
    SETUP_PENDING_FILENAME,
    SETUP_RECEIPT_FILENAME,
    SETUP_REQUIRED_FILENAME,
    load_json_object,
    save_json_atomic,
)
from .remote_configuration import (
    DEFAULT_STORE_ASSIGNMENT_KEY,
    REMOTE_CONFIGURATION_APPLIED_AT_KEY,
    REMOTE_CONFIGURATION_PENDING_RESTART_KEY,
    REMOTE_CONFIGURATION_REVISION_KEY,
    apply_remote_configuration,
    extract_remote_configuration,
)
from .server_identity import has_complete_server_identity


class InitialSetupSubmissionError(RuntimeError):
    pass


def _resolved_db_path(db_path: str | None) -> str:
    if db_path:
        return str(db_path)
    from .db_path import resolve_db_path

    return resolve_db_path()


def _text(value: Any) -> str:
    return str(value or "").strip()


def apply_initial_seed_settings(
    seed: Mapping[str, Any],
    *,
    db_path: str | None = None,
) -> None:
    """Aplica el seed al SQLite existente sin importar src.config ni usar red."""

    boolean_keys = {
        "telemarketing",
        "allow_no_stock",
        "enable_ai",
        "enable_recommendations",
        "update_check_on_startup",
    }
    text_keys = {
        "country",
        "listing_type",
        "company_type",
        "store_id",
        "username",
        "update_mode",
        "update_manifest_url",
        "update_flags",
    }
    con = connect(_resolved_db_path(db_path))
    try:
        ensure_schema(con)
        with tx(con):
            for key in text_keys:
                if key in seed:
                    set_setting(con, key, _text(seed.get(key)))
            for key in boolean_keys:
                if key in seed:
                    set_setting(con, key, "1" if bool(seed.get(key)) else "0")
            for key in (
                REMOTE_CONFIGURATION_REVISION_KEY,
                REMOTE_CONFIGURATION_APPLIED_AT_KEY,
                REMOTE_CONFIGURATION_PENDING_RESTART_KEY,
                DEFAULT_STORE_ASSIGNMENT_KEY,
            ):
                set_setting(con, key, "")
    finally:
        con.close()


def _find_catalog_payload(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    payload = dict(value)
    if "manifest_revision" in payload and (
        "groups" in payload or "grupos" in payload
    ):
        return payload
    for key in ("catalog_stock", "catalogo_stock", "data", "result", "payload"):
        found = _find_catalog_payload(payload.get(key))
        if found is not None:
            return found
    return None


def _validate_acknowledgement(
    request_payload: Mapping[str, Any],
    response_payload: Mapping[str, Any],
) -> None:
    expected_key = _text(request_payload.get("idempotency_key"))
    receipt = response_payload.get("receipt")
    receipt_key = (
        _text(receipt.get("idempotency_key"))
        if isinstance(receipt, Mapping)
        else ""
    )
    returned_key = _text(
        response_payload.get("idempotency_key")
        or response_payload.get("request_id")
        or receipt_key
    )
    if not returned_key:
        raise InitialSetupSubmissionError(
            "El servidor no devolvió el idempotency_key de la configuración."
        )
    if returned_key != expected_key:
        raise InitialSetupSubmissionError(
            "El servidor confirmó una solicitud de configuración diferente."
        )
    success = response_payload.get("success")
    if success is not True:
        message = _text(response_payload.get("message") or response_payload.get("error"))
        raise InitialSetupSubmissionError(
            message or "El servidor no confirmó la configuración inicial."
        )


def apply_bootstrap_response(
    request_payload: Mapping[str, Any],
    response_payload: Mapping[str, Any],
    *,
    db_path: str | None = None,
) -> dict[str, Any]:
    request = dict(request_payload)
    response = dict(response_payload)
    _validate_acknowledgement(request, response)
    identity = request.get("identity")
    if not isinstance(identity, Mapping):
        raise InitialSetupSubmissionError("La solicitud no contiene identity.")
    username = _text(identity.get("username"))
    id_cotizador = _text(identity.get("id_cotizador")).upper()
    if not username or not id_cotizador:
        raise InitialSetupSubmissionError("La identidad de la configuración está incompleta.")

    catalog_payload = _find_catalog_payload(response)
    configuration_payload = extract_remote_configuration(response)
    catalog_changes: dict[str, Any] = {}
    configuration_revision = ""

    con = connect(_resolved_db_path(db_path))
    try:
        ensure_schema(con)
        with tx(con):
            if catalog_payload is not None:
                catalog_changes = catalog_cache_repo.apply_sync_payload(
                    con,
                    username,
                    id_cotizador,
                    catalog_payload,
                )
            if configuration_payload is not None:
                outcome = apply_remote_configuration(
                    con,
                    username,
                    id_cotizador,
                    configuration_payload,
                    mark_restart=False,
                )
                configuration_revision = outcome.revision
    finally:
        con.close()

    return {
        "idempotency_key": _text(request.get("idempotency_key")),
        "applied_at": _datetime.datetime.now(_datetime.timezone.utc).isoformat(
            timespec="seconds"
        ),
        "http_status": int(response.get("http_status") or 0),
        "configuration_revision": configuration_revision,
        "manifest_revision": _text(
            (catalog_payload or {}).get("manifest_revision")
        ),
        "catalog_changes": catalog_changes,
    }


def submit_pending_initial_setup(
    app_root: str,
    *,
    submit_fn=None,
    db_path: str | None = None,
) -> dict[str, Any]:
    root = os.path.abspath(str(app_root or "").strip())
    config_dir = os.path.join(root, "config")
    pending_path = os.path.join(config_dir, SETUP_PENDING_FILENAME)
    receipt_path = os.path.join(config_dir, SETUP_RECEIPT_FILENAME)
    request_payload = load_json_object(pending_path)
    identity = request_payload.get("identity")
    if not isinstance(identity, Mapping) or not has_complete_server_identity(
        identity.get("username"),
        identity.get("id_cotizador"),
    ):
        raise InitialSetupSubmissionError(
            "La solicitud pendiente no tiene usuario e ID del cotizador; "
            "no se enviará nada al servidor."
        )

    seed_path = os.path.join(config_dir, "config.json")
    if os.path.isfile(seed_path):
        apply_initial_seed_settings(
            load_json_object(seed_path),
            db_path=db_path,
        )

    if submit_fn is None:
        # Import tardío: config.json ya existe y puede sembrar SQLite antes del login.
        from .api.configuration_client import bootstrap_initial_configuration

        submit_fn = bootstrap_initial_configuration

    try:
        response = submit_fn(request_payload)
    except Exception as exc:
        raise InitialSetupSubmissionError(str(exc)) from exc
    if not isinstance(response, Mapping):
        raise InitialSetupSubmissionError("El servidor no devolvió una confirmación JSON.")

    receipt = apply_bootstrap_response(
        request_payload,
        dict(response),
        db_path=db_path,
    )
    save_json_atomic(receipt_path, receipt)
    try:
        os.remove(pending_path)
    except FileNotFoundError:
        pass
    try:
        os.remove(os.path.join(config_dir, SETUP_REQUIRED_FILENAME))
    except FileNotFoundError:
        pass
    return receipt


__all__ = [
    "InitialSetupSubmissionError",
    "apply_initial_seed_settings",
    "apply_bootstrap_response",
    "submit_pending_initial_setup",
]
