from __future__ import annotations

import datetime as _datetime
import re
import sqlite3
import sys
import uuid
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

from sqlModels.api_identity import API_LOGIN_PASSWORD, build_api_settings
from sqlModels.settings_repo import get_setting, set_setting

from .country_rules import SUPPORTED_COUNTRIES, country_code_for, normalize_country_name


REMOTE_CONFIGURATION_REVISION_KEY = "remote_configuration_revision"
REMOTE_CONFIGURATION_APPLIED_AT_KEY = "remote_configuration_applied_at"
REMOTE_CONFIGURATION_PENDING_RESTART_KEY = "remote_configuration_pending_restart"
DEFAULT_STORE_ASSIGNMENT_KEY = "default_store_assignment_id"

_REVISION_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_LISTING_TYPES = frozenset({"AMBOS", "PRODUCTOS", "PRESENTACIONES"})
_COMPANY_TYPES = frozenset({"LA CASA DEL PERFUME", "EF PERFUMES"})


@dataclass(frozen=True, slots=True)
class RemoteConfigurationOutcome:
    applied: bool
    revision: str
    changed_settings: dict[str, Any]
    restart_required: bool


@contextmanager
def _atomic(con: sqlite3.Connection) -> Iterator[None]:
    if con.in_transaction:
        savepoint = f"remote_configuration_{uuid.uuid4().hex}"
        con.execute(f"SAVEPOINT {savepoint}")
        try:
            yield
            con.execute(f"RELEASE SAVEPOINT {savepoint}")
        except Exception:
            con.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            con.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise
        return
    con.execute("BEGIN")
    try:
        yield
        con.commit()
    except Exception:
        con.rollback()
        raise


def _text(value: Any) -> str:
    return str(value or "").strip()


def _bool(value: Any, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    normalized = _text(value).casefold()
    if normalized in {"1", "true", "yes", "si", "sí", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{field} debe ser booleano.")


def _revision(value: Any, *, required: bool = True) -> str:
    revision = _text(value).lower()
    if not revision:
        if required:
            raise ValueError("configuration.revision es obligatoria.")
        return ""
    if _REVISION_RE.fullmatch(revision) is None:
        raise ValueError("configuration.revision debe ser un SHA-256 hexadecimal.")
    return revision


def _mapping(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} debe ser un objeto.")
    return dict(value)


def _normalize_scope(value: Any) -> dict[str, str]:
    raw = _mapping(value, field="configuration.default_scope")
    country_raw = (
        raw.get("country_code")
        or raw.get("cod_pais")
        or raw.get("country")
        or raw.get("pais")
    )
    country = normalize_country_name(country_raw)
    if country not in SUPPORTED_COUNTRIES:
        raise ValueError("configuration.default_scope contiene un país no soportado.")

    company = _text(
        raw.get("company_type") or raw.get("company") or raw.get("empresa")
    ).upper()
    if company not in _COMPANY_TYPES:
        raise ValueError("configuration.default_scope contiene una empresa no soportada.")
    return {
        "country": country,
        "country_code": country_code_for(country),
        "company_type": company,
    }


def extract_remote_configuration(payload: Any) -> dict[str, Any] | None:
    """Extrae el bloque opcional sin confundirlo con el manifiesto de catálogo."""

    if not isinstance(payload, Mapping):
        return None
    current = dict(payload)
    for key in ("configuration", "configuracion", "remote_configuration"):
        if key not in current:
            continue
        value = current.get(key)
        if not isinstance(value, Mapping):
            raise ValueError(f"{key} debe ser un objeto.")
        return dict(value)
    for key in ("data", "result", "payload"):
        nested = current.get(key)
        found = extract_remote_configuration(nested)
        if found is not None:
            return found
    return None


def get_remote_configuration_revision(con: sqlite3.Connection) -> str:
    revision = _text(get_setting(con, REMOTE_CONFIGURATION_REVISION_KEY, "")).lower()
    return revision if _REVISION_RE.fullmatch(revision) else ""


def add_configuration_known_state(
    con: sqlite3.Connection,
    known_state: Mapping[str, Any] | None,
) -> dict[str, Any]:
    state = dict(known_state or {})
    revision = get_remote_configuration_revision(con)
    if revision:
        state["configuration_revision"] = revision
    return state


def normalize_remote_configuration(
    value: Mapping[str, Any],
    *,
    current_revision: str = "",
) -> dict[str, Any]:
    raw = _mapping(value, field="configuration")
    revision = _revision(raw.get("revision") or raw.get("configuration_revision"))
    local_revision = _revision(current_revision, required=False)
    changed_declared = raw.get("changed")
    changed = revision != local_revision
    if changed_declared is not None and _bool(
        changed_declared,
        field="configuration.changed",
    ) != changed:
        raise ValueError("configuration.revision no es coherente con changed.")

    if not changed:
        return {
            "revision": revision,
            "changed": False,
            "settings": {},
        }

    settings_raw = raw.get("settings") or raw.get("preferences") or {}
    settings = _mapping(settings_raw, field="configuration.settings")
    normalized: dict[str, Any] = {}

    if "listing_type" in settings:
        listing_type = _text(settings.get("listing_type")).upper()
        if listing_type not in _LISTING_TYPES:
            raise ValueError("configuration.settings.listing_type es inválido.")
        normalized["listing_type"] = listing_type

    for key in (
        "allow_no_stock",
        "telemarketing",
        "enable_ai",
        "enable_recommendations",
    ):
        if key in settings:
            normalized[key] = _bool(
                settings.get(key),
                field=f"configuration.settings.{key}",
            )

    scope_value = raw.get("default_scope") or settings.get("default_scope")
    if scope_value is None and any(
        key in settings for key in ("country", "country_code", "company_type", "company")
    ):
        scope_value = settings
    if scope_value is not None:
        normalized["default_scope"] = _normalize_scope(scope_value)

    default_store = (
        raw.get("default_store_id")
        if "default_store_id" in raw
        else settings.get("default_store_id")
    )
    if default_store is not None:
        default_store_text = _text(default_store)
        if default_store_text and not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", default_store_text):
            raise ValueError("configuration.default_store_id es inválido.")
        normalized["default_store_id"] = default_store_text

    identity = raw.get("identity")
    if identity is not None:
        identity_map = _mapping(identity, field="configuration.identity")
        normalized["identity"] = {
            "username": _text(identity_map.get("username") or identity_map.get("user")),
            "id_cotizador": _text(
                identity_map.get("id_cotizador") or identity_map.get("store_id")
            ).upper(),
        }

    return {
        "revision": revision,
        "changed": True,
        "settings": normalized,
    }


def _stored_value(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    return _text(value)


def _runtime_value(key: str, value: str) -> Any:
    if key in {
        "allow_no_stock",
        "telemarketing",
        "enable_ai",
        "enable_recommendations",
    }:
        return value == "1"
    return value


def _refresh_runtime_cache(changes: Mapping[str, Any]) -> None:
    """Actualiza el caché central; imports legacy se completan al reiniciar."""

    if not changes:
        return
    try:
        config_module = sys.modules.get("src.config")
        if config_module is None:
            return

        for key, change in changes.items():
            if key in config_module.APP_CONFIG:
                config_module.APP_CONFIG[key] = change["after"]

        if "listing_type" in changes:
            config_module.APP_LISTING_TYPE = str(changes["listing_type"]["after"])
        if "allow_no_stock" in changes:
            config_module.ALLOW_NO_STOCK = bool(changes["allow_no_stock"]["after"])
        if "telemarketing" in changes:
            config_module.APP_TELEMARKETING = bool(changes["telemarketing"]["after"])
            config_module.APP_TIENDA = config_module.APP_TELEMARKETING
        if "enable_ai" in changes:
            config_module.ENABLE_AI = bool(changes["enable_ai"]["after"])
        if "enable_recommendations" in changes:
            config_module.ENABLE_RECOMMENDATIONS = bool(
                changes["enable_recommendations"]["after"]
            )
        if "country" in changes:
            config_module.APP_COUNTRY = str(changes["country"]["after"])
        if "company_type" in changes:
            config_module.APP_COMPANY_TYPE = str(changes["company_type"]["after"])
    except Exception:
        # La persistencia ya quedó aplicada. Los consumidores legacy releen al reiniciar.
        return


def apply_remote_configuration(
    con: sqlite3.Connection,
    username: str,
    id_cotizador: str,
    value: Mapping[str, Any] | None,
    *,
    applied_at: str | None = None,
    mark_restart: bool = True,
) -> RemoteConfigurationOutcome:
    if value is None:
        return RemoteConfigurationOutcome(False, get_remote_configuration_revision(con), {}, False)

    current_revision = get_remote_configuration_revision(con)
    normalized = normalize_remote_configuration(value, current_revision=current_revision)
    revision = str(normalized["revision"])
    if not normalized["changed"]:
        return RemoteConfigurationOutcome(False, revision, {}, False)

    settings = dict(normalized["settings"])
    identity = dict(settings.pop("identity", {}) or {})
    expected_user = _text(username).casefold()
    expected_cotizador = _text(id_cotizador).upper()
    response_user = _text(identity.get("username")).casefold()
    response_cotizador = _text(identity.get("id_cotizador")).upper()
    if response_user and response_user != expected_user:
        raise ValueError("La configuración remota pertenece a otro usuario.")
    if response_cotizador and response_cotizador != expected_cotizador:
        raise ValueError("La configuración remota pertenece a otro cotizador.")

    values_to_store: dict[str, str] = {}
    for key in (
        "listing_type",
        "allow_no_stock",
        "telemarketing",
        "enable_ai",
        "enable_recommendations",
    ):
        if key in settings:
            values_to_store[key] = _stored_value(settings[key])

    scope = settings.get("default_scope")
    if isinstance(scope, Mapping):
        values_to_store["country"] = _text(scope.get("country")).upper()
        values_to_store["company_type"] = _text(scope.get("company_type")).upper()
        current_country = _text(get_setting(con, "country", "")).upper()
        current_company = _text(get_setting(con, "company_type", "")).upper()
        if (
            current_country != values_to_store["country"]
            or current_company != values_to_store["company_type"]
            or not _text(get_setting(con, "id_user_api", ""))
        ):
            values_to_store.update(
                build_api_settings(
                    country=values_to_store["country"],
                    company_type=values_to_store["company_type"],
                    password_plain=API_LOGIN_PASSWORD,
                )
            )
    if "default_store_id" in settings:
        values_to_store[DEFAULT_STORE_ASSIGNMENT_KEY] = _stored_value(
            settings["default_store_id"]
        )

    changes: dict[str, Any] = {}
    observable_keys = {
        "listing_type",
        "allow_no_stock",
        "telemarketing",
        "enable_ai",
        "enable_recommendations",
        "country",
        "company_type",
        DEFAULT_STORE_ASSIGNMENT_KEY,
    }
    for key, new_value in values_to_store.items():
        if key not in observable_keys:
            continue
        old_value = _text(get_setting(con, key, ""))
        if old_value == new_value:
            continue
        changes[key] = {
            "before": _runtime_value(key, old_value),
            "after": _runtime_value(key, new_value),
        }

    timestamp = _text(applied_at) or _datetime.datetime.now(
        _datetime.timezone.utc
    ).isoformat(timespec="seconds")
    restart_required = bool(changes) and bool(mark_restart)
    with _atomic(con):
        for key, new_value in values_to_store.items():
            set_setting(con, key, new_value)
        set_setting(con, REMOTE_CONFIGURATION_REVISION_KEY, revision)
        set_setting(con, REMOTE_CONFIGURATION_APPLIED_AT_KEY, timestamp)
        if restart_required:
            set_setting(con, REMOTE_CONFIGURATION_PENDING_RESTART_KEY, "1")

    _refresh_runtime_cache(changes)
    return RemoteConfigurationOutcome(True, revision, changes, restart_required)


__all__ = [
    "DEFAULT_STORE_ASSIGNMENT_KEY",
    "REMOTE_CONFIGURATION_APPLIED_AT_KEY",
    "REMOTE_CONFIGURATION_PENDING_RESTART_KEY",
    "REMOTE_CONFIGURATION_REVISION_KEY",
    "RemoteConfigurationOutcome",
    "add_configuration_known_state",
    "apply_remote_configuration",
    "extract_remote_configuration",
    "get_remote_configuration_revision",
    "normalize_remote_configuration",
]
