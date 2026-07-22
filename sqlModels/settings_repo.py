# sqlModels/settings_repo.py
from __future__ import annotations

import sqlite3
from pathlib import Path


def get_setting(con: sqlite3.Connection, key: str, default: str | None = "") -> str | None:
    k = str(key or "").strip()
    if not k:
        return default
    r = con.execute("SELECT value FROM settings WHERE key = ?", (k,)).fetchone()
    if not r:
        return default
    return str(r["value"]) if r["value"] is not None else default


def set_setting(con: sqlite3.Connection, key: str, value: str | None) -> None:
    k = str(key or "").strip()
    if not k:
        return
    v = None if value is None else str(value)
    con.execute(
        """
        INSERT INTO settings(key, value) VALUES(?,?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        (k, v),
    )


def ensure_defaults(con: sqlite3.Connection, defaults: dict[str, str | None]) -> None:
    for k, v in (defaults or {}).items():
        con.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)",
            (str(k), None if v is None else str(v)),
        )


def settings_is_empty(con: sqlite3.Connection) -> bool:
    r = con.execute("SELECT 1 AS x FROM settings LIMIT 1").fetchone()
    return (r is None)


def seed_settings_if_empty(
    con: sqlite3.Connection,
    *,
    defaults: dict[str, str | None],
    overrides: dict[str, str | None] | None = None,
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


def recover_settings_from_readonly_db(
    con: sqlite3.Connection,
    *,
    source_db_path: str,
    keys: tuple[str, ...],
    required_keys: tuple[str, ...],
) -> bool:
    """
    Recupera un grupo atomico de settings desde otra DB.

    Solo copia cuando al destino le falta al menos una llave requerida y la
    fuente contiene todas las requeridas con valor. La fuente se abre en modo
    solo lectura para que una DB primaria degradada no reciba mas escrituras.
    El caller controla la transaccion del destino.
    """
    normalized_keys = tuple(dict.fromkeys(str(key or "").strip() for key in keys))
    normalized_keys = tuple(key for key in normalized_keys if key)
    required = tuple(dict.fromkeys(str(key or "").strip() for key in required_keys))
    required = tuple(key for key in required if key)
    if not normalized_keys or not required:
        return False

    target_values = {key: get_setting(con, key, None) for key in required}
    if all(str(target_values.get(key) or "").strip() for key in required):
        return False

    source_path = Path(str(source_db_path or "")).resolve()
    if not source_path.is_file():
        return False

    source = None
    try:
        source = sqlite3.connect(f"{source_path.as_uri()}?mode=ro", uri=True, timeout=2.0)
        source.row_factory = sqlite3.Row
        placeholders = ",".join("?" for _ in normalized_keys)
        rows = source.execute(
            f"SELECT key, value FROM settings WHERE key IN ({placeholders})",
            normalized_keys,
        ).fetchall()
        source_values = {str(row["key"]): row["value"] for row in rows}
    finally:
        if source is not None:
            source.close()

    if not all(str(source_values.get(key) or "").strip() for key in required):
        return False

    for key in normalized_keys:
        if key in source_values:
            set_setting(con, key, source_values[key])
    return True
