# sqlModels/settings_repo.py
from __future__ import annotations

import sqlite3


def get_setting(con: sqlite3.Connection, key: str, default: str = "") -> str:
    k = str(key or "").strip()
    if not k:
        return default
    r = con.execute("SELECT value FROM settings WHERE key = ?", (k,)).fetchone()
    return str(r["value"]) if r and r["value"] is not None else default


def set_setting(con: sqlite3.Connection, key: str, value: str) -> None:
    k = str(key or "").strip()
    if not k:
        return
    v = "" if value is None else str(value)
    con.execute(
        """
        INSERT INTO settings(key, value) VALUES(?,?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        (k, v),
    )


def ensure_defaults(con: sqlite3.Connection, defaults: dict[str, str]) -> None:
    for k, v in (defaults or {}).items():
        con.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)",
            (str(k), "" if v is None else str(v)),
        )


def settings_is_empty(con: sqlite3.Connection) -> bool:
    r = con.execute("SELECT 1 AS x FROM settings LIMIT 1").fetchone()
    return (r is None)


def seed_settings_if_empty(
    con: sqlite3.Connection,
    *,
    defaults: dict[str, str],
    overrides: dict[str, str] | None = None,
) -> bool:
    """
    Si settings está vacío:
      - inserta defaults
      - aplica overrides (sobre-escribe)
    Retorna True si sembró, False si ya había settings.
    """
    if not settings_is_empty(con):
        return False

    ensure_defaults(con, defaults)

    for k, v in (overrides or {}).items():
        set_setting(con, k, v)

    return True
