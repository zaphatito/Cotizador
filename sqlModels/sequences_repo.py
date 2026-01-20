# sqlModels/sequences_repo.py
from __future__ import annotations

import sqlite3

def next_quote_no(con: sqlite3.Connection, country_code: str, width: int = 7) -> str:
    """
    Devuelve correlativo como string con padding: "0000001".
    Maneja secuencia por país: name = "quote_no_<CC>".
    Debe ejecutarse dentro de una transacción (tx) para evitar duplicados.
    """
    cc = (country_code or "PY").strip().upper()
    name = f"quote_no_{cc}"

    con.execute("INSERT OR IGNORE INTO sequences(name, value) VALUES(?, 0)", (name,))
    row = con.execute("SELECT value FROM sequences WHERE name = ?", (name,)).fetchone()
    last = int(row["value"]) if row else 0
    nxt = last + 1
    con.execute("UPDATE sequences SET value = ? WHERE name = ?", (nxt, name))
    return str(nxt).zfill(width)
