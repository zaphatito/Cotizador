# src/db_path.py
from __future__ import annotations

import os
import sys
import sqlite3

from .paths import DATA_DIR
from .logging_setup import get_logger

log = get_logger(__name__)

_CACHED_DB_PATH: str | None = None
_CACHED_KIND: str | None = None  # "primary" | "fallback" | "fallback_unverified"


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


def _can_write_sqlite(db_path: str) -> bool:
    try:
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        con = sqlite3.connect(db_path)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("CREATE TABLE IF NOT EXISTS __write_test(x INTEGER)")
        con.execute("DROP TABLE __write_test")
        con.commit()
        con.close()
        return True
    except Exception as e:
        log.warning("No se puede escribir DB en %s (%s)", db_path, e)
        return False


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

    base_dir = _base_dir_for_app()
    primary = os.path.join(base_dir, "sqlModels", "app.sqlite3")
    fallback = os.path.join(DATA_DIR, "app.sqlite3")

    if _can_write_sqlite(primary):
        _CACHED_DB_PATH = primary
        _CACHED_KIND = "primary"
        log.info("DB path (primary): %s", primary)
        return primary

    if _can_write_sqlite(fallback):
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
