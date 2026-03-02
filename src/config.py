# src/config.py
from __future__ import annotations

import os
import sys
import json
from typing import Dict, Any, Tuple, List

from .currency import normalize_currency_code
from .paths import DATA_DIR, user_docs_dir
from sqlModels.db import connect, ensure_schema, tx
from sqlModels.api_identity import API_LOGIN_PASSWORD, build_api_settings
from sqlModels.settings_repo import (
    get_setting,
    ensure_defaults,
    settings_is_empty,
    set_setting,
)

# Defaults (DB) como strings/None (solo se usan si NO hay config.json o para completar keys faltantes)
DEFAULT_CONFIG_STR: dict[str, str | None] = {
    "country": "PARAGUAY",
    "listing_type": "AMBOS",
    "company_type": "LA CASA DEL PERFUME",
    "store_id": "",
    "username": "",
    "tienda": None,
    "id_user_api": "",
    "user_api": "",
    "password_api_hash": "",
    "allow_no_stock": "0",
    "enable_ai": "0",
    "enable_recommendations": "1",
    "update_mode": "ASK",
    "update_check_on_startup": "1",
    "update_manifest_url": "",
    "update_flags": "/CLOSEAPPLICATIONS",
    "log_dir": "",
    "log_level": "INFO",
    "status_color_pagado": "#06863B",
    "status_color_por_pagar": "#ECD060",
    "status_color_pendiente": "#E67E22",
    "status_color_reenviado": "#BF0DE3",
    "status_color_no_aplica": "#811307",
    "chat_theme_mode": "auto",
    "chat_bubble_user_bg": "",
    "chat_bubble_assist_bg": "",
    "chat_send_bg": "",
}

# Categorías granel
CATS = ["ESENCIA", "AROMATERAPIA", "ESENCIAS"]

ALLOWED_COMPANY_TYPES: tuple[str, str] = (
    "LA CASA DEL PERFUME",
    "EF PERFUMES",
)


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


def _load_seed_overrides_from_json() -> dict[str, str | None]:
    """
    Lee config.json/app_config.json SOLO para sembrar LA PRIMERA VEZ.
    Devuelve overrides en formato string/None compatible con settings.

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
            if isinstance(x, str):
                s = x.strip().lower()
                if s in ("1", "true", "yes", "on", "si"):
                    return "1"
                if s in ("0", "false", "no", "off", ""):
                    return "0"
            return "1" if bool(x) else "0"
        except Exception:
            return "0"

    def b01_or_none(x) -> str | None:
        if x is None:
            return None
        return b01(x)

    out: dict[str, str | None] = {}

    if "country" in raw:
        out["country"] = str(raw["country"]).strip().upper()
    if "listing_type" in raw:
        out["listing_type"] = str(raw["listing_type"]).strip().upper()
    if "company_type" in raw:
        out["company_type"] = str(raw["company_type"]).strip().upper()
    elif "company" in raw:
        out["company_type"] = str(raw["company"]).strip().upper()
    if "store_id" in raw:
        out["store_id"] = str(raw["store_id"]).strip()
    if "username" in raw:
        out["username"] = str(raw["username"]).strip()
    elif "user_name" in raw:
        out["username"] = str(raw["user_name"]).strip()
    if "tienda" in raw:
        out["tienda"] = b01_or_none(raw["tienda"])
    if "allow_no_stock" in raw:
        out["allow_no_stock"] = b01(raw["allow_no_stock"])
    if "enable_ai" in raw:
        out["enable_ai"] = b01(raw["enable_ai"])
    elif "ai_enabled" in raw:
        out["enable_ai"] = b01(raw["ai_enabled"])
    if "enable_recommendations" in raw:
        out["enable_recommendations"] = b01(raw["enable_recommendations"])
    elif "recommendations_enabled" in raw:
        out["enable_recommendations"] = b01(raw["recommendations_enabled"])

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

        # Calcula credenciales API en base a pais + compania seleccionados.
        country_now = get_setting(con, "country", DEFAULT_CONFIG_STR["country"]).strip().upper()
        company_now = get_setting(con, "company_type", DEFAULT_CONFIG_STR["company_type"]).strip().upper()
        api_vals = build_api_settings(
            country=country_now,
            company_type=company_now,
            password_plain=API_LOGIN_PASSWORD,
        )
        for k, v in api_vals.items():
            set_setting(con, k, v)

        _set_meta(con, "settings_seeded", "1")

        if seed_from_json:
            _set_meta(con, "settings_seeded_from_json", "1")

    else:
        if settings_is_empty(con):
            ensure_defaults(con, DEFAULT_CONFIG_STR)


def _parse_optional_bool_setting(value: str | None) -> bool | None:
    if value is None:
        return None

    s = str(value).strip().lower()
    if not s:
        return None
    if s in ("1", "true", "yes", "on", "si"):
        return True
    if s in ("0", "false", "no", "off"):
        return False
    return None


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
    company_type = get_setting(con, "company_type", DEFAULT_CONFIG_STR["company_type"]).strip().upper()
    store_id = get_setting(con, "store_id", DEFAULT_CONFIG_STR["store_id"]).strip()
    username = get_setting(con, "username", DEFAULT_CONFIG_STR["username"]).strip()
    tienda = _parse_optional_bool_setting(get_setting(con, "tienda", DEFAULT_CONFIG_STR["tienda"]))
    id_user_api = get_setting(con, "id_user_api", DEFAULT_CONFIG_STR["id_user_api"]).strip()
    user_api = get_setting(con, "user_api", DEFAULT_CONFIG_STR["user_api"]).strip()
    password_api_hash = get_setting(con, "password_api_hash", DEFAULT_CONFIG_STR["password_api_hash"]).strip()
    allow_no_stock = get_setting(con, "allow_no_stock", DEFAULT_CONFIG_STR["allow_no_stock"]).strip()
    enable_ai = get_setting(con, "enable_ai", DEFAULT_CONFIG_STR["enable_ai"]).strip()
    enable_recs = get_setting(
        con,
        "enable_recommendations",
        DEFAULT_CONFIG_STR["enable_recommendations"],
    ).strip()

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
        "company_type": company_type if company_type in ALLOWED_COMPANY_TYPES else DEFAULT_CONFIG_STR["company_type"],
        "store_id": store_id,
        "username": username,
        "tienda": tienda,
        "id_user_api": id_user_api,
        "user_api": user_api,
        "password_api_hash": password_api_hash,
        "allow_no_stock": (allow_no_stock == "1"),
        "enable_ai": (enable_ai != "0"),
        "enable_recommendations": (enable_recs != "0"),

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
APP_COMPANY_TYPE: str = APP_CONFIG["company_type"]
STORE_ID: str = APP_CONFIG["store_id"]
APP_USERNAME: str = APP_CONFIG["username"]
APP_TIENDA: bool | None = APP_CONFIG["tienda"]
ALLOW_NO_STOCK: bool = bool(APP_CONFIG["allow_no_stock"])
ENABLE_AI: bool = bool(APP_CONFIG["enable_ai"])
ENABLE_RECOMMENDATIONS: bool = bool(APP_CONFIG["enable_recommendations"])


def is_ai_enabled(*, refresh: bool = True) -> bool:
    """
    Estado actual del kill switch IA.
    Si refresh=True, relee de DB para reflejar cambios en caliente.
    """
    global ENABLE_AI
    if refresh:
        try:
            db_path = _resolve_db_path_for_config()
            con = connect(db_path)
            ensure_schema(con)
            try:
                v = get_setting(con, "enable_ai", DEFAULT_CONFIG_STR["enable_ai"]).strip()
            finally:
                con.close()
            ENABLE_AI = (v != "0")
            APP_CONFIG["enable_ai"] = ENABLE_AI
        except Exception:
            pass
    return bool(APP_CONFIG.get("enable_ai", ENABLE_AI))


def set_ai_enabled(enabled: bool) -> bool:
    """
    Persiste enable_ai en DB y sincroniza cache en memoria.
    """
    global ENABLE_AI
    v = "1" if bool(enabled) else "0"
    db_path = _resolve_db_path_for_config()
    con = connect(db_path)
    try:
        ensure_schema(con)
        with tx(con):
            set_setting(con, "enable_ai", v)
    finally:
        con.close()

    ENABLE_AI = (v == "1")
    APP_CONFIG["enable_ai"] = ENABLE_AI
    return ENABLE_AI


def is_recommendations_enabled(*, refresh: bool = True) -> bool:
    """
    Estado actual del switch de recomendaciones.
    Si refresh=True, relee DB para reflejar cambios en caliente.
    """
    global ENABLE_RECOMMENDATIONS
    if refresh:
        try:
            db_path = _resolve_db_path_for_config()
            con = connect(db_path)
            ensure_schema(con)
            try:
                v = get_setting(
                    con,
                    "enable_recommendations",
                    DEFAULT_CONFIG_STR["enable_recommendations"],
                ).strip()
            finally:
                con.close()
            ENABLE_RECOMMENDATIONS = (v != "0")
            APP_CONFIG["enable_recommendations"] = ENABLE_RECOMMENDATIONS
        except Exception:
            pass
    return bool(APP_CONFIG.get("enable_recommendations", ENABLE_RECOMMENDATIONS))


def set_recommendations_enabled(enabled: bool) -> bool:
    """
    Persiste enable_recommendations en DB y sincroniza cache en memoria.
    """
    global ENABLE_RECOMMENDATIONS
    v = "1" if bool(enabled) else "0"
    db_path = _resolve_db_path_for_config()
    con = connect(db_path)
    try:
        ensure_schema(con)
        with tx(con):
            set_setting(con, "enable_recommendations", v)
    finally:
        con.close()

    ENABLE_RECOMMENDATIONS = (v == "1")
    APP_CONFIG["enable_recommendations"] = ENABLE_RECOMMENDATIONS
    return ENABLE_RECOMMENDATIONS


# -----------------------------
# Monedas (NORMALIZADAS)
# -----------------------------
def _normalize_currencies_list(values: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for v in values or []:
        c = normalize_currency_code(v)
        if c and c not in seen:
            out.append(c)
            seen.add(c)
    return out


def currency_for_country(country: str) -> str:
    """
    Devuelve moneda BASE (CANÓNICA ISO).
    """
    c = (country or "").upper()
    if c == "PERU":
        return "PEN"
    if c == "VENEZUELA":
        return "USD"
    return "PYG"


def secondary_currencies_for_country(country: str) -> List[str]:
    """
    Devuelve monedas secundarias (CANÓNICAS ISO).
    """
    c = (country or "").upper()
    if c == "PARAGUAY":
        return ["ARS", "BRL", "USD"]
    if c == "VENEZUELA":
        return ["VES"]
    if c == "PERU":
        return ["BOB", "USD"]
    return []


def secondary_currency_for_country(country: str, base: str) -> str:
    """
    Devuelve una secundaria “preferida” distinta a la base.
    """
    base_c = normalize_currency_code(base)
    secs = _normalize_currencies_list(secondary_currencies_for_country(country))

    for s in secs:
        if s and s != base_c:
            return s

    # fallback razonable
    if base_c != "USD":
        return "USD"
    return ""


APP_CURRENCY: str = normalize_currency_code(currency_for_country(APP_COUNTRY))
SECONDARY_CURRENCIES: List[str] = _normalize_currencies_list(secondary_currencies_for_country(APP_COUNTRY))
SECONDARY_CURRENCY: str = normalize_currency_code(secondary_currency_for_country(APP_COUNTRY, APP_CURRENCY))


def _country_suffix(country: str) -> str:
    m = {"VENEZUELA": "VE", "PERU": "PE", "PARAGUAY": "PY"}
    return m.get((country or "").upper(), "PY")


COUNTRY_CODE: str = _country_suffix(APP_COUNTRY)


def id_label_for_country(country: str) -> str:
    c = (country or "").upper()
    if c == "PERU":
        return "Documento"
    if c == "VENEZUELA":
        return "Documento"
    return "Documento"


def listing_allows_products() -> bool:
    return APP_LISTING_TYPE in ("PRODUCTOS", "AMBOS")


def listing_allows_presentations() -> bool:
    return APP_LISTING_TYPE in ("PRESENTACIONES", "AMBOS")


# Contexto de moneda actual (siempre CANÓNICO)
CURRENT_CURRENCY: str = APP_CURRENCY
_CURRENCY_RATE: float = 1.0


def get_currency_context() -> Tuple[str, str, float]:
    # siempre devuelve normalizado
    return normalize_currency_code(CURRENT_CURRENCY), normalize_currency_code(SECONDARY_CURRENCY), float(_CURRENCY_RATE)


def get_secondary_currencies() -> List[str]:
    return SECONDARY_CURRENCIES[:]


def set_currency_context(new_currency: str, rate: float) -> None:
    """
    Fija moneda actual y tasa.
    - new_currency se normaliza (SOL->PEN, GS->PYG, etc)
    - si new_currency es base => tasa 1
    - si hay lista de monedas permitidas (base+sec), clampa para evitar códigos raros
    """
    global CURRENT_CURRENCY, _CURRENCY_RATE

    base = normalize_currency_code(APP_CURRENCY)
    allowed = {base, *(_normalize_currencies_list(SECONDARY_CURRENCIES))}
    cur = normalize_currency_code(new_currency or base)

    # clamp: si no está permitido, vuelve a base
    if allowed and cur not in allowed:
        cur = base

    if not cur or cur == base:
        CURRENT_CURRENCY = base
        _CURRENCY_RATE = 1.0
        return

    CURRENT_CURRENCY = cur
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
    "APP_CONFIG", "APP_COUNTRY", "APP_LISTING_TYPE", "APP_COMPANY_TYPE", "STORE_ID",
    "APP_USERNAME", "APP_TIENDA",
    "ALLOW_NO_STOCK", "ENABLE_AI", "ENABLE_RECOMMENDATIONS", "ALLOWED_COMPANY_TYPES",
    "is_ai_enabled", "set_ai_enabled", "is_recommendations_enabled", "set_recommendations_enabled",
    "APP_CURRENCY", "SECONDARY_CURRENCY", "SECONDARY_CURRENCIES", "COUNTRY_CODE",
    "CATS",
    "currency_for_country", "secondary_currency_for_country", "secondary_currencies_for_country",
    "id_label_for_country",
    "listing_allows_products", "listing_allows_presentations",
    "get_currency_context", "get_secondary_currencies", "set_currency_context", "convert_from_base",
    "LOG_DIR", "LOG_LEVEL",
]
