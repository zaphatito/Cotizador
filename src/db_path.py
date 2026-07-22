# src/db_path.py
from __future__ import annotations

import os
import sys
import sqlite3

from .paths import DATA_DIR
from .logging_setup import get_logger

log = get_logger(__name__)

_CACHED_DB_PATH: str | None = None
_CACHED_KIND: str | None = None  # "primary" | "primary_busy" | "fallback" | "fallback_unverified"


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _base_dir_for_app() -> str:
    """
    - Frozen: carpeta del exe (Inno Setup instala aquí)
    - Dev: carpeta raíz del repo (src/..)
    """
    if _is_frozen():
        return os.path.dirname(sys.executable)
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _is_transient_sqlite_error(exc: Exception) -> bool:
    code = getattr(exc, "sqlite_errorcode", None)
    try:
        base_code = int(code) & 0xFF
    except Exception:
        base_code = None
    transient_codes = {
        int(getattr(sqlite3, "SQLITE_BUSY", 5)),
        int(getattr(sqlite3, "SQLITE_LOCKED", 6)),
    }
    if base_code in transient_codes:
        return True
    message = str(exc or "").strip().lower()
    return "database is locked" in message or "database table is locked" in message


def _probe_sqlite_write(db_path: str) -> tuple[bool, bool]:
    con = None
    try:
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        con = sqlite3.connect(db_path, timeout=5.0)
        con.execute("PRAGMA busy_timeout = 5000")
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("CREATE TABLE IF NOT EXISTS __write_test(x INTEGER)")
        con.execute("DROP TABLE __write_test")
        con.commit()
        return True, False
    except Exception as e:
        log.warning("No se puede escribir DB en %s (%s)", db_path, e)
        return False, _is_transient_sqlite_error(e)
    finally:
        if con is not None:
            con.close()


def _can_write_sqlite(db_path: str) -> bool:
    writable, _transient = _probe_sqlite_write(db_path)
    return writable


def db_path_candidates() -> tuple[str, str]:
    base_dir = _base_dir_for_app()
    primary = os.path.join(base_dir, "sqlModels", "app.sqlite3")
    fallback = os.path.join(DATA_DIR, "app.sqlite3")
    return primary, fallback


def resolve_db_path(*, force_refresh: bool = False) -> str:
    """
    1) <app>/sqlModels/app.sqlite3  (preferido)
    2) DATA_DIR/app.sqlite3        (fallback permitido)

    Cachea el resultado para evitar:
      - logs repetidos
      - pruebas de escritura repetidas
    """
    global _CACHED_DB_PATH, _CACHED_KIND

    if _CACHED_DB_PATH and not force_refresh:
        return _CACHED_DB_PATH

    primary, fallback = db_path_candidates()

    primary_writable, primary_transient = _probe_sqlite_write(primary)
    if primary_writable:
        _CACHED_DB_PATH = primary
        _CACHED_KIND = "primary"
        log.info("DB path (primary): %s", primary)
        return primary

    if primary_transient and os.path.isfile(primary):
        _CACHED_DB_PATH = primary
        _CACHED_KIND = "primary_busy"
        log.warning(
            "DB principal temporalmente ocupada; se conserva para evitar cambiar de base: %s",
            primary,
        )
        return primary

    fallback_writable, _fallback_transient = _probe_sqlite_write(fallback)
    if fallback_writable:
        _CACHED_DB_PATH = fallback
        _CACHED_KIND = "fallback"
        log.info("DB path (fallback): %s", fallback)
        return fallback

    _CACHED_DB_PATH = fallback
    _CACHED_KIND = "fallback_unverified"
    log.error(
        "No se pudo validar escritura en primary/fallback. Devolviendo fallback: %s",
        fallback,
    )
    return fallback


def db_path_debug_info() -> str:
    return f"{_CACHED_DB_PATH or ''} ({_CACHED_KIND or 'not_resolved'})"
