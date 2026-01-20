# sqlModels/migrations.py
from __future__ import annotations

import sqlite3


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    r = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return r is not None


def _column_exists(con: sqlite3.Connection, table: str, col: str) -> bool:
    if not _table_exists(con, table):
        return False
    rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    cols = {str(r["name"]).lower() for r in rows}
    return col.lower() in cols


def _add_column_if_missing(con: sqlite3.Connection, table: str, col: str, col_def_sql: str) -> None:
    """
    col_def_sql ejemplo: "TEXT NOT NULL DEFAULT ''"
    """
    if _column_exists(con, table, col):
        return
    con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_def_sql}")



def mig_1(con: sqlite3.Connection) -> None:
    return


def mig_2(con: sqlite3.Connection) -> None:
    return


def mig_3(con: sqlite3.Connection) -> None:
    """
    v3: Histórico de tasas de cambio
    - Crea exchange_rates_history
    - Index para consultas rápidas por par y fecha
    - Backfill: inserta el “current” como primer histórico si aún no existe
    """
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS exchange_rates_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            base_currency TEXT NOT NULL,
            currency TEXT NOT NULL,
            rate REAL NOT NULL,
            recorded_at TEXT NOT NULL
        )
        """
    )

    con.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_exchange_rates_history_pair_time
        ON exchange_rates_history(base_currency, currency, recorded_at)
        """
    )

    # backfill (1 sola vez por par) usando updated_at como recorded_at
    if _table_exists(con, "exchange_rates"):
        con.execute(
            """
            INSERT INTO exchange_rates_history(base_currency, currency, rate, recorded_at)
            SELECT er.base_currency, er.currency, er.rate,
                   COALESCE(er.updated_at, datetime('now'))
            FROM exchange_rates er
            WHERE NOT EXISTS (
                SELECT 1
                FROM exchange_rates_history h
                WHERE h.base_currency = er.base_currency
                  AND h.currency = er.currency
            )
            """
        )


# Mapa: versión destino -> función migración
MIGRATIONS: dict[int, callable] = {
    1: mig_1,
    2: mig_2,
    3: mig_3,
}