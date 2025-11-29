# src/config.py
from __future__ import annotations
import os, sys, json
from typing import Dict, Any, Tuple, List

# --------------------------
# Utilidades de rutas
# --------------------------
def _windows_documents_dir() -> str:
    if os.name == "nt":
        try:
            from ctypes import windll, create_unicode_buffer
            CSIDL_PERSONAL = 5
            SHGFP_TYPE_CURRENT = 0
            buf = create_unicode_buffer(260)
            if windll.shell32.SHGetFolderPathW(None, CSIDL_PERSONAL, None, SHGFP_TYPE_CURRENT, buf) == 0:
                return buf.value
        except Exception:
            pass
    return os.path.join(os.path.expanduser("~"), "Documents")


def _ensure_dir(p: str) -> str:
    try:
        os.makedirs(p, exist_ok=True)
    except Exception:
        pass
    return p


# --------------------------
# Detección de carpeta y archivo de configuración
# --------------------------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))


def _candidate_config_dirs() -> List[str]:
    dirs: List[str] = []
    # 1) Si está "frozen" (PyInstaller), priorizar carpeta junto al ejecutable
    if getattr(sys, "frozen", False):
        dirs.append(os.path.join(os.path.dirname(sys.executable), "config"))

    # 2) Carpeta "config" relativa al cwd
    dirs.append(os.path.join(os.getcwd(), "config"))

    # 3) Carpeta "config" relativa a este módulo
    dirs.append(os.path.join(_THIS_DIR, "config"))

    # Eliminar duplicados conservando orden
    out: List[str] = []
    seen: set[str] = set()
    for d in dirs:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def _pick_config_path() -> Tuple[str, str]:
    for d in _candidate_config_dirs():
        for fname in ("config.json", "app_config.json"):
            p = os.path.join(d, fname)
            if os.path.exists(p):
                return d, p
    # Si no existe, crear en el primer candidato
    base = _candidate_config_dirs()[0]
    _ensure_dir(base)
    return base, os.path.join(base, "config.json")


CONFIG_DIR, CONFIG_PATH = _pick_config_path()

# --------------------------
# Defaults + constantes exportadas
# --------------------------
DEFAULT_CONFIG: Dict[str, Any] = {
    "country": "PARAGUAY",     # "PARAGUAY" | "PERU" | "VENEZUELA"
    "listing_type": "AMBOS",   # "PRODUCTOS" | "PRESENTACIONES" | "AMBOS"
    "allow_no_stock": False,

    # ==== Auto-updater ====
    "update_mode": "ASK",              # "ASK" | "SILENT" | "OFF"
    "update_check_on_startup": True,
    "update_manifest_url": "",         # ej: "https://tu-dominio/updates/cotizador.json"
    "update_flags": "/CLOSEAPPLICATIONS",

    # logging opcional:
    # "log_dir": "C:/Users/<usuario>/Documents/Cotizaciones/logs"
    # "log_level": "INFO"  # ERROR, WARNING, INFO, DEBUG
}

# Categorías a granel usadas por pricing/app_window/etc.
CATS = ["ESENCIA", "AROMATERAPIA", "ESENCIAS"]


def _load_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_app_config() -> Dict[str, Any]:
    cfg = DEFAULT_CONFIG.copy()
    raw = _load_json(CONFIG_PATH)
    if raw:
        # country
        val = str(raw.get("country", cfg["country"])).strip().upper()
        if val in ("PARAGUAY", "PERU", "VENEZUELA"):
            cfg["country"] = val

        # listing_type
        lt = str(raw.get("listing_type", cfg["listing_type"])).strip().upper()
        if lt in ("PRODUCTOS", "PRESENTACIONES", "AMBOS"):
            cfg["listing_type"] = lt

        # allow_no_stock
        try:
            cfg["allow_no_stock"] = bool(raw.get("allow_no_stock", cfg["allow_no_stock"]))
        except Exception:
            pass

        # ==== claves de update ====
        umode = str(raw.get("update_mode", cfg["update_mode"])).strip().upper()
        if umode in ("ASK", "SILENT", "OFF"):
            cfg["update_mode"] = umode

        try:
            cfg["update_check_on_startup"] = bool(raw.get("update_check_on_startup", cfg["update_check_on_startup"]))
        except Exception:
            pass

        if "update_manifest_url" in raw and str(raw["update_manifest_url"]).strip():
            cfg["update_manifest_url"] = str(raw["update_manifest_url"]).strip()

        if "update_flags" in raw and isinstance(raw["update_flags"], str):
            cfg["update_flags"] = raw["update_flags"].strip()

        # logging (opcionales)
        if "log_dir" in raw and str(raw["log_dir"]).strip():
            cfg["log_dir"] = str(raw["log_dir"]).strip()
        if "log_level" in raw and str(raw["log_level"]).strip():
            cfg["log_level"] = str(raw["log_level"]).strip().upper()
    return cfg


APP_CONFIG = load_app_config()

# --------------------------
# Parámetros principales
# --------------------------
APP_COUNTRY: str      = APP_CONFIG["country"]
APP_LISTING_TYPE: str = APP_CONFIG["listing_type"]
ALLOW_NO_STOCK: bool  = APP_CONFIG["allow_no_stock"]

# --------------------------
# País / moneda / labels
# --------------------------
def currency_for_country(country: str) -> str:
    c = (country or "").upper()
    # Moneda base por país:
    if c == "PERU":
        return "PEN"   # Sol
    if c == "VENEZUELA":
        return "USD"   # Dólar
    # Default: Paraguay
    return "PYG"       # Guaraní


def secondary_currencies_for_country(country: str) -> List[str]:
    """
    Lista de monedas secundarias por país.

    La idea es permitir MÁS de una moneda secundaria por país.
    Ejemplo:
      - PARAGUAY   → ["ARS", "BRL"]   (peso argentino, real brasileño)
      - VENEZUELA  → ["VES"]
      - PERU       → ["BOB"]
    """
    c = (country or "").upper()
    if c == "PARAGUAY":
        # Ya existía ARS; añadimos BRL como segunda moneda secundaria.
        return ["ARS", "BRL"]
    if c == "VENEZUELA":
        return ["VES"]
    if c == "PERU":
        return ["BOB"]
    # Fallback: sin secundarias
    return []


def secondary_currency_for_country(country: str) -> str:
    """
    Versión legacy: devuelve SOLO la moneda secundaria principal
    (la primera de la lista de secondary_currencies_for_country).

    Se mantiene para compatibilidad con código existente.
    """
    lst = secondary_currencies_for_country(country)
    if lst:
        return lst[0]
    # Fallback genérico
    return "USD"


APP_CURRENCY: str               = currency_for_country(APP_COUNTRY)
SECONDARY_CURRENCIES: List[str] = secondary_currencies_for_country(APP_COUNTRY)
# Moneda secundaria "principal" (para compatibilidad)
SECONDARY_CURRENCY: str         = secondary_currency_for_country(APP_COUNTRY)


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


# --------------------------
# Reglas de listado
# --------------------------
def listing_allows_products() -> bool:
    return APP_LISTING_TYPE in ("PRODUCTOS", "AMBOS")


def listing_allows_presentations() -> bool:
    return APP_LISTING_TYPE in ("PRESENTACIONES", "AMBOS")


# --------------------------
# Contexto dinámico de moneda
# --------------------------
# Moneda en la que se muestran los precios en la UI (por defecto, la base).
CURRENT_CURRENCY: str = APP_CURRENCY
# Factor por el cual se multiplica un monto en moneda base para mostrarlo
# en la moneda actual. Si CURRENT_CURRENCY == APP_CURRENCY, es 1.0.
_CURRENCY_RATE: float = 1.0


def get_currency_context() -> Tuple[str, str, float]:
    """
    Devuelve (moneda_actual, moneda_secundaria_principal, factor_base_a_actual).

    - moneda_actual: código de la moneda que se está mostrando en la UI.
    - moneda_secundaria_principal: la primera de las monedas secundarias
      para el país actual (SECONDARY_CURRENCY).
    - factor_base_a_actual:
        * 1.0 cuando moneda_actual == APP_CURRENCY
        * >1 o <1 cuando se trabaja en una moneda alternativa (secundaria)
    """
    return CURRENT_CURRENCY, SECONDARY_CURRENCY, _CURRENCY_RATE


def get_secondary_currencies() -> List[str]:
    """
    Devuelve la lista de monedas secundarias disponibles para el país actual.

    Incluye SECONDARY_CURRENCY como primer elemento (si existe).
    Esto está pensado para poblar combos / botones en la UI.
    """
    return SECONDARY_CURRENCIES[:]


def set_currency_context(new_currency: str, rate: float) -> None:
    """
    Configura la moneda actual de la UI y la tasa:

    new_currency:
        - APP_CURRENCY              → se trabaja en moneda base (factor = 1.0)
        - Cualquier otra (por ej. alguna de get_secondary_currencies())
          → se trabaja en esa moneda, usando 'rate'.

    rate:
        - cuántas unidades de 'new_currency' equivale 1 unidad de APP_CURRENCY.
          Ej: si APP_CURRENCY='USD' y new_currency='VES', y 1 USD = 40 VES → rate=40.
    """
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
    """
    Inv: amount SIEMPRE viene expresado en la moneda base (APP_CURRENCY).

    Convierte ese monto a la moneda actualmente seleccionada en la UI
    usando la tasa configurada.
    Si no hay tasa válida, devuelve el mismo monto.
    """
    try:
        _, _, rate = get_currency_context()
        return float(amount) * float(rate)
    except Exception:
        try:
            return float(amount)
        except Exception:
            return 0.0


# --------------------------
# Logging (rutas y nivel)
# --------------------------
def _default_log_dir() -> str:
    base = _windows_documents_dir()
    return _ensure_dir(os.path.join(base, "Cotizaciones", "logs"))


_raw_log_dir = APP_CONFIG.get("log_dir", "").strip() if isinstance(APP_CONFIG.get("log_dir"), str) else ""
if _raw_log_dir:
    LOG_DIR: str = _ensure_dir(os.path.abspath(os.path.expanduser(os.path.expandvars(_raw_log_dir))))
else:
    LOG_DIR: str = _default_log_dir()

LOG_LEVEL: str = str(APP_CONFIG.get("log_level", "INFO")).strip().upper()
if LOG_LEVEL not in ("ERROR", "WARNING", "INFO", "DEBUG"):
    LOG_LEVEL = "INFO"


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
