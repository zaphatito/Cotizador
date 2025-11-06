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
    try: os.makedirs(p, exist_ok=True)
    except Exception: pass
    return p

# --------------------------
# Detección de carpeta y archivo de configuración
# --------------------------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))

def _candidate_config_dirs() -> List[str]:
    dirs: List[str] = []
    if getattr(sys, "frozen", False):
        dirs.append(os.path.join(os.path.dirname(sys.executable), "config"))
    dirs.append(os.path.join(os.getcwd(), "config"))
    dirs.append(os.path.join(_THIS_DIR, "config"))
    out, seen = [], set()
    for d in dirs:
        if d not in seen:
            seen.add(d); out.append(d)
    return out

def _pick_config_path() -> Tuple[str, str]:
    for d in _candidate_config_dirs():
        for fname in ("config.json", "app_config.json"):
            p = os.path.join(d, fname)
            if os.path.exists(p):
                return d, p
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

    # ==== NUEVO: Auto-updater ====
    # Forma de comprobar e instalar actualizaciones al iniciar:
    # "ASK"     => pregunta y lanza instalador con UI
    # "SILENT"  => instala en silencio (recomendado añadir flags) 
    # "OFF"     => deshabilitado
    "update_mode": "ASK",
    "update_check_on_startup": True,
    # Coloca aquí tu JSON de manifiesto (GitHub Raw, S3, tu servidor, etc.)
    "update_manifest_url": "",  # ej: "https://tu-dominio/updates/cotizador.json"
    # Flags opcionales para Inno Setup (cuando no quieras UI: /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS)
    "update_flags": "/CLOSEAPPLICATIONS",

    # Campos de logging opcionales:
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

        # ==== NUEVO: claves de update ====
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
    if c == "PERU":       return "PEN"
    if c == "VENEZUELA":  return "USD"
    return "PYG"  # default PY

APP_CURRENCY: str = currency_for_country(APP_COUNTRY)

def _country_suffix(country: str) -> str:
    m = {"VENEZUELA": "VE", "PERU": "PE", "PARAGUAY": "PY"}
    return m.get((country or "").upper(), "PY")

COUNTRY_CODE: str = _country_suffix(APP_COUNTRY)

def id_label_for_country(country: str) -> str:
    c = (country or "").upper()
    if c == "PERU":       return "DNI/RUC"
    if c == "VENEZUELA":  return "CEDULA / RIF"
    return "CEDULA / RUC"

# --------------------------
# Reglas de listado
# --------------------------
def listing_allows_products() -> bool:
    return APP_LISTING_TYPE in ("PRODUCTOS", "AMBOS")

def listing_allows_presentations() -> bool:
    return APP_LISTING_TYPE in ("PRESENTACIONES", "AMBOS")

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
    "APP_CURRENCY", "COUNTRY_CODE",
    "CATS",
    "currency_for_country", "id_label_for_country",
    "listing_allows_products", "listing_allows_presentations",
    "LOG_DIR", "LOG_LEVEL",
]
