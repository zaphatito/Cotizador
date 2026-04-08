# sqlModels/sequences_repo.py
from __future__ import annotations

import sqlite3

def _sequence_name(country_code: str) -> str:
    cc = (country_code or "PY").strip().upper()
    return f"quote_no_{cc}"


def get_quote_no_value(con: sqlite3.Connection, country_code: str) -> int:
    name = _sequence_name(country_code)
    con.execute("INSERT OR IGNORE INTO sequences(name, value) VALUES(?, 0)", (name,))
    row = con.execute("SELECT value FROM sequences WHERE name = ?", (name,)).fetchone()
    return int(row["value"]) if row else 0


def ensure_quote_no_at_least(con: sqlite3.Connection, country_code: str, value: int) -> int:
    name = _sequence_name(country_code)
    target = max(0, int(value or 0))
    current = get_quote_no_value(con, country_code)
    if current < target:
        con.execute("UPDATE sequences SET value = ? WHERE name = ?", (target, name))
        return target
    return current


def next_quote_no(con: sqlite3.Connection, country_code: str, width: int = 7) -> str:
    """
    Devuelve correlativo como string con padding: "0000001".
    Maneja secuencia por país: name = "quote_no_<CC>".
    Debe ejecutarse dentro de una transacción (tx) para evitar duplicados.
    """
    name = _sequence_name(country_code)
    last = get_quote_no_value(con, country_code)
    nxt = last + 1
    con.execute("UPDATE sequences SET value = ? WHERE name = ?", (nxt, name))
    return str(nxt).zfill(width)
