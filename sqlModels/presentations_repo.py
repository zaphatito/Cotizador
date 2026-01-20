# sqlModels/presentations_repo.py
from __future__ import annotations

import sqlite3
import pandas as pd
from .utils import now_iso

def upsert_presentations_snapshot(con: sqlite3.Connection, import_id: int, df: pd.DataFrame) -> None:
    """
    Espera tu df de cargar_presentaciones() con columnas:
      CODIGO, CODIGO_NORM, NOMBRE, DEPARTAMENTO, GENERO, PRECIO_PRESENT, REQUIERE_BOTELLA
    """
    if df is None or df.empty:
        return

    now = now_iso()
    rows_hist = []
    rows_cur = []

    for _, r in df.iterrows():
        codigo_norm = str(r.get("CODIGO_NORM") or "").strip().upper()
        if not codigo_norm:
            continue

        codigo = str(r.get("CODIGO") or "").strip().upper()
        nombre = str(r.get("NOMBRE") or "")
        depto = str(r.get("DEPARTAMENTO") or "")
        genero = str(r.get("GENERO") or "")
        precio = float(r.get("PRECIO_PRESENT") or 0.0)
        req = 1 if bool(r.get("REQUIERE_BOTELLA")) else 0

        rows_hist.append((import_id, codigo_norm, codigo, nombre, depto, genero, precio, req))
        rows_cur.append((codigo_norm, codigo, nombre, depto, genero, precio, req, now))

    con.executemany(
        """
        INSERT OR REPLACE INTO presentations_hist(
            import_id, codigo_norm, codigo, nombre, departamento, genero,
            precio_present, requiere_botella
        )
        VALUES(?,?,?,?,?,?,?,?)
        """,
        rows_hist,
    )

    con.executemany(
        """
        INSERT INTO presentations_current(
            codigo_norm, codigo, nombre, departamento, genero,
            precio_present, requiere_botella, updated_at
        )
        VALUES(?,?,?,?,?,?,?,?)
        ON CONFLICT(codigo_norm) DO UPDATE SET
            codigo=excluded.codigo,
            nombre=excluded.nombre,
            departamento=excluded.departamento,
            genero=excluded.genero,
            precio_present=excluded.precio_present,
            requiere_botella=excluded.requiere_botella,
            updated_at=excluded.updated_at
        """,
        rows_cur,
    )

def load_presentations_current(con: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query("SELECT * FROM presentations_current", con)
