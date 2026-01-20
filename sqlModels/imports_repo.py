# sqlModels/imports_repo.py
from __future__ import annotations

import sqlite3
from typing import Any

from .utils import now_iso, sha256_file, stat_file


def get_last_import(con: sqlite3.Connection, kind: str, source_file: str) -> dict | None:
    row = con.execute(
        """
        SELECT *
        FROM imports
        WHERE kind = ? AND source_file = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (kind, source_file),
    ).fetchone()
    return dict(row) if row else None


def needs_import(con: sqlite3.Connection, kind: str, source_file: str) -> tuple[bool, dict]:
    """
    Retorna (need_import, meta):
      meta = {mtime: float, size: int, hash: str}

    REGLA CLAVE:
      - meta["hash"] NUNCA debe ser None, porque imports.source_hash es NOT NULL.
    """
    mtime, size = stat_file(source_file)
    last = get_last_import(con, kind, source_file)

    # 1) Primer import -> SIEMPRE calcular hash
    if not last:
        h = sha256_file(source_file)
        return True, {"mtime": mtime, "size": size, "hash": h}

    # 2) Quick check por mtime/size
    if float(last["source_mtime"]) == float(mtime) and int(last["source_size"]) == int(size):
        # No cambió el archivo (según stat), no reimportar
        return False, {"mtime": mtime, "size": size, "hash": str(last["source_hash"] or "")}

    # 3) Cambió mtime/size -> comparar hash real
    h = sha256_file(source_file)
    if h == str(last["source_hash"] or ""):
        return False, {"mtime": mtime, "size": size, "hash": h}

    return True, {"mtime": mtime, "size": size, "hash": h}


def create_import(con: sqlite3.Connection, kind: str, source_file: str, mtime: float, size: int, h: str) -> int:
    if not h:
        # Protección extra: jamás insertar NULL/"" si el schema es NOT NULL
        h = sha256_file(source_file)

    cur = con.execute(
        """
        INSERT INTO imports(kind, source_file, source_mtime, source_size, source_hash, imported_at)
        VALUES(?,?,?,?,?,?)
        """,
        (kind, source_file, float(mtime), int(size), str(h), now_iso()),
    )
    return int(cur.lastrowid)
