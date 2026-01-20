# sqlModels/db.py
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Callable

from .schema import DDL, SCHEMA_VERSION
from .migrations import MIGRATIONS


def connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row

    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA synchronous = NORMAL")
    con.execute("PRAGMA busy_timeout = 5000")
    return con


@contextmanager
def tx(con: sqlite3.Connection):
    try:
        con.execute("BEGIN")
        yield
        con.commit()
    except Exception:
        con.rollback()
        raise


def _get_meta(con: sqlite3.Connection, key: str) -> str | None:
    try:
        r = con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return str(r["value"]) if r and r["value"] is not None else None
    except Exception:
        return None


def _set_meta(con: sqlite3.Connection, key: str, value: str) -> None:
    con.execute(
        """
        INSERT INTO meta(key, value) VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        (key, str(value)),
    )


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    r = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return r is not None


def _infer_schema_version(con: sqlite3.Connection) -> int:
    """
    Si la DB viene de versiones MUY viejas (sin meta o sin schema_version),
    inferimos una versión aproximada mirando tablas.
    """
    # si no hay nada relevante, asumir nueva
    any_user_table = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' LIMIT 1"
    ).fetchone()
    if not any_user_table:
        return 0

    # si existe imports/exchange_rates/settings/sequences => >=2 (tu v2 actual)
    if _table_exists(con, "imports") or _table_exists(con, "exchange_rates") or _table_exists(con, "settings") or _table_exists(con, "sequences"):
        return 2

    # si existe quotes/products_current/etc => >=1
    if _table_exists(con, "quotes") or _table_exists(con, "products_current") or _table_exists(con, "presentations_current"):
        return 1

    return 0


def ensure_schema(con: sqlite3.Connection) -> None:
    """
    - Aplica DDL idempotente
    - Ejecuta migraciones incrementales (si hacen falta)
    - Deja meta.schema_version = SCHEMA_VERSION
    """
    with tx(con):
        # 1) DDL base (idempotente)
        for stmt in DDL:
            con.execute(stmt)

        # 2) leer versión actual
        cur_v_s = _get_meta(con, "schema_version")
        if cur_v_s is None:
            cur_v = _infer_schema_version(con)
        else:
            try:
                cur_v = int(cur_v_s)
            except Exception:
                cur_v = _infer_schema_version(con)

        # 3) si es DB nueva (0) y no quieres migrar nada, simplemente setear
        #    PERO ojo: si era DB vieja sin meta, _infer_schema_version no devolverá 0.
        if cur_v < SCHEMA_VERSION:
            # 4) migrar incremental
            for target_v in range(cur_v + 1, SCHEMA_VERSION + 1):
                mig = MIGRATIONS.get(target_v)
                if mig:
                    mig(con)  # debe ser segura/condicional

        # 5) fijar versión final
        _set_meta(con, "schema_version", str(SCHEMA_VERSION))
