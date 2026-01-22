# src/config.py
from __future__ import annotations

import os
import sys
import json
from typing import Dict, Any, Tuple, List

from .paths import DATA_DIR, user_docs_dir
from sqlModels.db import connect, ensure_schema, tx
from sqlModels.settings_repo import (
    get_setting,
    ensure_defaults,
    settings_is_empty,
    set_setting,
)

# Defaults (DB) como strings (solo se usan si NO hay config.json o para completar keys faltantes)
DEFAULT_CONFIG_STR: dict[str, str] = {
    "country": "PARAGUAY",
    "listing_type": "AMBOS",
    "allow_no_stock": "0",

    "update_mode": "ASK",
    "update_check_on_startup": "1",
    "update_manifest_url": "",
    "update_flags": "/CLOSEAPPLICATIONS",

    "log_dir": "",
    "log_level": "INFO",
}

# Categorías granel
CATS = ["ESENCIA", "AROMATERAPIA", "ESENCIAS"]


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _base_dir_for_app() -> str:
    if _is_frozen():
        return os.path.dirname(sys.executable)
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _can_write_sqlite(db_path: str) -> bool:
    try:
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        import sqlite3
        con = sqlite3.connect(db_path)
        con.execute("CREATE TABLE IF NOT EXISTS __write_test(x INTEGER)")
        con.execute("DROP TABLE __write_test")
        con.commit()
        con.close()
        return True
    except Exception:
        return False


def _resolve_db_path_no_log() -> str:
    primary = os.path.join(_base_dir_for_app(), "sqlModels", "app.sqlite3")
    fallback = os.path.join(DATA_DIR, "app.sqlite3")
    if _can_write_sqlite(primary):
        return primary
    return fallback


def _resolve_db_path_for_config() -> str:
    """
    Usa la MISMA DB que el resto de la app.
    Si db_path.resolve_db_path falla, cae al fallback interno.
    """
    try:
        from .db_path import resolve_db_path
        return resolve_db_path()
    except Exception:
        return _resolve_db_path_no_log()


def _ensure_dir(p: str) -> str:
    try:
        os.makedirs(p, exist_ok=True)
    except Exception:
        pass
    return p


def _normalize_path(p: str) -> str:
    if not p:
        return ""
    try:
        return os.path.abspath(os.path.expanduser(os.path.expandvars(str(p))))
    except Exception:
        return str(p)


def _candidate_config_paths() -> list[str]:
    """
    Busca un config json SOLO para SEED inicial.
    Prioriza:
      - <exe>/config/config.json (frozen)
      - <root>/config/config.json (dev)
      - cwd/config/config.json
    y lo mismo para app_config.json.
    """
    base = _base_dir_for_app()
    cands = []

    # frozen / install
    cands.append(os.path.join(base, "config", "config.json"))
    cands.append(os.path.join(base, "config", "app_config.json"))

    # dev (repo)
    cands.append(os.path.join(base, "config", "config.json"))
    cands.append(os.path.join(base, "config", "app_config.json"))

    # cwd
    cands.append(os.path.join(os.getcwd(), "config", "config.json"))
    cands.append(os.path.join(os.getcwd(), "config", "app_config.json"))

    out = []
    seen = set()
    for p in cands:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _load_seed_overrides_from_json() -> dict[str, str]:
    """
    Lee config.json/app_config.json SOLO para sembrar LA PRIMERA VEZ.
    Devuelve overrides en formato string compatible con settings.

    OJO: si no hay json, devuelve {} (y ahí se usan DEFAULT_CONFIG_STR).
    """
    path = None
    for p in _candidate_config_paths():
        if os.path.exists(p):
            path = p
            break
    if not path:
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            return {}
    except Exception:
        return {}

    def b01(x) -> str:
        try:
            return "1" if bool(x) else "0"
        except Exception:
            return "0"

    out: dict[str, str] = {}

    if "country" in raw:
        out["country"] = str(raw["country"]).strip().upper()
    if "listing_type" in raw:
        out["listing_type"] = str(raw["listing_type"]).strip().upper()
    if "allow_no_stock" in raw:
        out["allow_no_stock"] = b01(raw["allow_no_stock"])

    if "update_mode" in raw:
        out["update_mode"] = str(raw["update_mode"]).strip().upper()
    if "update_check_on_startup" in raw:
        out["update_check_on_startup"] = b01(raw["update_check_on_startup"])
    if "update_manifest_url" in raw:
        out["update_manifest_url"] = str(raw["update_manifest_url"]).strip()
    if "update_flags" in raw:
        out["update_flags"] = str(raw["update_flags"]).strip()

    if "log_dir" in raw:
        out["log_dir"] = str(raw["log_dir"]).strip()
    if "log_level" in raw:
        out["log_level"] = str(raw["log_level"]).strip().upper()

    return out


def _get_meta(con, key: str) -> str | None:
    r = con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return str(r["value"]) if r and r["value"] is not None else None


def _set_meta(con, key: str, value: str) -> None:
    con.execute(
        """
        INSERT INTO meta(key, value) VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        (str(key), str(value)),
    )


def _seed_settings_once(con) -> None:
    """
    Regla (robusta):
      - Siempre asegura DEFAULT_CONFIG_STR (INSERT OR IGNORE)
      - Si meta.settings_seeded NO existe (primera ejecución “real”):
          * Lee config.json/app_config.json si existe
          * APLICA overrides con UPSERT (set_setting) aunque settings ya tenga filas
            (esto evita que una DB "seed" traída desde dist bloquee el seed)
          * Marca meta.settings_seeded = 1
      - En ejecuciones siguientes:
          * No vuelve a aplicar JSON
          * Solo agrega nuevas keys por defaults (INSERT OR IGNORE)
    """
    seeded_flag = _get_meta(con, "settings_seeded")

    ensure_defaults(con, DEFAULT_CONFIG_STR)

    if seeded_flag is None:
        seed_from_json = _load_seed_overrides_from_json()

        # aplica overrides (sobrescribe SOLO estas keys) en la DB
        for k, v in (seed_from_json or {}).items():
            set_setting(con, k, v)

        _set_meta(con, "settings_seeded", "1")

        if seed_from_json:
            _set_meta(con, "settings_seeded_from_json", "1")

    else:
        if settings_is_empty(con):
            ensure_defaults(con, DEFAULT_CONFIG_STR)


def _load_db_config() -> Dict[str, Any]:
    db_path = _resolve_db_path_for_config()
    con = connect(db_path)
    ensure_schema(con)

    # ✅ transacción para que SE GUARDE (commit)
    with tx(con):
        _seed_settings_once(con)

    # leer settings
    country = get_setting(con, "country", DEFAULT_CONFIG_STR["country"]).strip().upper()
    listing_type = get_setting(con, "listing_type", DEFAULT_CONFIG_STR["listing_type"]).strip().upper()
    allow_no_stock = get_setting(con, "allow_no_stock", DEFAULT_CONFIG_STR["allow_no_stock"]).strip()

    update_mode = get_setting(con, "update_mode", DEFAULT_CONFIG_STR["update_mode"]).strip().upper()
    update_check = get_setting(con, "update_check_on_startup", DEFAULT_CONFIG_STR["update_check_on_startup"]).strip()
    update_manifest_url = get_setting(con, "update_manifest_url", DEFAULT_CONFIG_STR["update_manifest_url"]).strip()
    update_flags = get_setting(con, "update_flags", DEFAULT_CONFIG_STR["update_flags"]).strip()

    log_dir = get_setting(con, "log_dir", DEFAULT_CONFIG_STR["log_dir"]).strip()
    log_level = get_setting(con, "log_level", DEFAULT_CONFIG_STR["log_level"]).strip().upper()

    con.close()

    cfg: Dict[str, Any] = {
        "country": country if country in ("PARAGUAY", "PERU", "VENEZUELA") else "PARAGUAY",
        "listing_type": listing_type if listing_type in ("PRODUCTOS", "PRESENTACIONES", "AMBOS") else "AMBOS",
        "allow_no_stock": (allow_no_stock == "1"),

        "update_mode": update_mode if update_mode in ("ASK", "SILENT", "OFF") else "ASK",
        "update_check_on_startup": (update_check != "0"),
        "update_manifest_url": update_manifest_url,
        "update_flags": update_flags,

        "log_dir": log_dir,
        "log_level": log_level if log_level in ("ERROR", "WARNING", "INFO", "DEBUG") else "INFO",
    }
    return cfg


APP_CONFIG = _load_db_config()

APP_COUNTRY: str = APP_CONFIG["country"]
APP_LISTING_TYPE: str = APP_CONFIG["listing_type"]
ALLOW_NO_STOCK: bool = bool(APP_CONFIG["allow_no_stock"])


def currency_for_country(country: str) -> str:
    c = (country or "").upper()
    if c == "PERU":
        return "PEN"
    if c == "VENEZUELA":
        return "USD"
    return "PYG"


def secondary_currencies_for_country(country: str) -> List[str]:
    c = (country or "").upper()
    if c == "PARAGUAY":
        return ["ARS", "BRL"]
    if c == "VENEZUELA":
        return ["VES"]
    if c == "PERU":
        return ["BOB"]
    return []


def secondary_currency_for_country(country: str) -> str:
    lst = secondary_currencies_for_country(country)
    return lst[0] if lst else "USD"


APP_CURRENCY: str = currency_for_country(APP_COUNTRY)
SECONDARY_CURRENCIES: List[str] = secondary_currencies_for_country(APP_COUNTRY)
SECONDARY_CURRENCY: str = secondary_currency_for_country(APP_COUNTRY)


def _country_suffix(country: str) -> str:
    m = {"VENEZUELA": "VE", "PERU": "PE", "PARAGUAY": "PY"}
    return m.get((country or "").upper(), "PY")


COUNTRY_CODE: str = _country_suffix(APP_COUNTRY)


def id_label_for_country(country: str) -> str:
    c = (country or "").upper()
    if c == "PERU":
        return "DNI/RUC"
    if c == "VENEZUELA":
        return "CEDULA / RIF"
    return "CEDULA / RUC"


def listing_allows_products() -> bool:
    return APP_LISTING_TYPE in ("PRODUCTOS", "AMBOS")


def listing_allows_presentations() -> bool:
    return APP_LISTING_TYPE in ("PRESENTACIONES", "AMBOS")


CURRENT_CURRENCY: str = APP_CURRENCY
_CURRENCY_RATE: float = 1.0


def get_currency_context() -> Tuple[str, str, float]:
    return CURRENT_CURRENCY, SECONDARY_CURRENCY, _CURRENCY_RATE


def get_secondary_currencies() -> List[str]:
    return SECONDARY_CURRENCIES[:]


def set_currency_context(new_currency: str, rate: float) -> None:
    global CURRENT_CURRENCY, _CURRENCY_RATE
    new = (new_currency or APP_CURRENCY).upper()
    if new == APP_CURRENCY:
        CURRENT_CURRENCY = APP_CURRENCY
        _CURRENCY_RATE = 1.0
    else:
        CURRENT_CURRENCY = new
        try:
            r = float(rate)
            _CURRENCY_RATE = r if r > 0 else 1.0
        except Exception:
            _CURRENCY_RATE = 1.0


def convert_from_base(amount: float) -> float:
    try:
        return float(amount) * float(_CURRENCY_RATE)
    except Exception:
        try:
            return float(amount)
        except Exception:
            return 0.0


_raw_log_dir = _normalize_path(APP_CONFIG.get("log_dir", "") or "")
if _raw_log_dir:
    LOG_DIR: str = _ensure_dir(_raw_log_dir)
else:
    LOG_DIR: str = user_docs_dir("logs")

LOG_LEVEL: str = str(APP_CONFIG.get("log_level", "INFO")).strip().upper()
if LOG_LEVEL not in ("ERROR", "WARNING", "INFO", "DEBUG"):
    LOG_LEVEL = "INFO"


CONFIG_DIR = ""
CONFIG_PATH = ""

__all__ = [
    "CONFIG_DIR", "CONFIG_PATH",
    "APP_CONFIG", "APP_COUNTRY", "APP_LISTING_TYPE", "ALLOW_NO_STOCK",
    "APP_CURRENCY", "SECONDARY_CURRENCY", "SECONDARY_CURRENCIES", "COUNTRY_CODE",
    "CATS",
    "currency_for_country", "secondary_currency_for_country", "secondary_currencies_for_country",
    "id_label_for_country",
    "listing_allows_products", "listing_allows_presentations",
    "get_currency_context", "get_secondary_currencies", "set_currency_context", "convert_from_base",
    "LOG_DIR", "LOG_LEVEL",
]
