# sqlModels/products_repo.py
from __future__ import annotations

import sqlite3
import math
import pandas as pd

from .utils import now_iso


def _to_float(v, default: float = 0.0) -> float:
    """
    Convierte a float de forma segura:
    - None / "" / NaN / inf => default
    """
    try:
        if v is None:
            return float(default)
        # pandas / numpy NaN
        try:
            if pd.isna(v):
                return float(default)
        except Exception:
            pass

        if isinstance(v, str):
            s = v.strip()
            if not s:
                return float(default)
            s = s.replace(",", "")
            v = s

        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return float(default)
        return x
    except Exception:
        return float(default)


def upsert_products_snapshot(con: sqlite3.Connection, import_id: int, df: pd.DataFrame) -> None:
    """
    Espera un df tipo:
      id, nombre, categoria, genero, cantidad_disponible,
      precio_unitario|precio_unidad|precio_base_50g,
      precio_venta, precio_oferta_base, precio_minimo_base, ml (opcional), __fuente (opcional)
    """
    if df is None or df.empty:
        return

    now = now_iso()
    rows_hist: list[tuple] = []
    rows_cur: list[tuple] = []

    for _, r in df.iterrows():
        pid = str(r.get("id") or "").strip()
        if not pid:
            continue

        nombre = str(r.get("nombre") or "")
        categoria = str(r.get("categoria") or "")
        genero = str(r.get("genero") or "")
        ml = str(r.get("ml") or "")

        cantidad = _to_float(r.get("cantidad_disponible"), 0.0)

        precio_unitario = _to_float(r.get("precio_unitario"), 0.0)
        precio_unidad = _to_float(r.get("precio_unidad"), 0.0)
        precio_base_50g = _to_float(r.get("precio_base_50g"), 0.0)

        precio_venta = _to_float(r.get("precio_venta"), 0.0)
        precio_oferta_base = _to_float(r.get("precio_oferta_base"), 0.0)
        precio_minimo_base = _to_float(r.get("precio_minimo_base"), 0.0)

        fuente = str(r.get("__fuente") or r.get("fuente") or "")

        rows_hist.append((
            int(import_id), pid, nombre, categoria, genero, ml,
            float(cantidad),
            float(precio_unitario), float(precio_unidad), float(precio_base_50g),
            float(precio_oferta_base), float(precio_minimo_base), float(precio_venta),
            fuente
        ))

        rows_cur.append((
            pid, nombre, categoria, genero, ml,
            float(cantidad),
            float(precio_unitario), float(precio_unidad), float(precio_base_50g),
            float(precio_oferta_base), float(precio_minimo_base), float(precio_venta),
            fuente, now
        ))

    con.executemany(
        """
        INSERT OR REPLACE INTO products_hist(
            import_id, id, nombre, categoria, genero, ml,
            cantidad_disponible,
            precio_unitario, precio_unidad, precio_base_50g,
            precio_oferta_base, precio_minimo_base, precio_venta,
            fuente
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        rows_hist,
    )

    con.executemany(
        """
        INSERT INTO products_current(
            id, nombre, categoria, genero, ml,
            cantidad_disponible,
            precio_unitario, precio_unidad, precio_base_50g,
            precio_oferta_base, precio_minimo_base, precio_venta,
            fuente, updated_at
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            nombre=excluded.nombre,
            categoria=excluded.categoria,
            genero=excluded.genero,
            ml=excluded.ml,
            cantidad_disponible=excluded.cantidad_disponible,
            precio_unitario=excluded.precio_unitario,
            precio_unidad=excluded.precio_unidad,
            precio_base_50g=excluded.precio_base_50g,
            precio_oferta_base=excluded.precio_oferta_base,
            precio_minimo_base=excluded.precio_minimo_base,
            precio_venta=excluded.precio_venta,
            fuente=excluded.fuente,
            updated_at=excluded.updated_at
        """,
        rows_cur,
    )


def load_products_current(con: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query("SELECT * FROM products_current", con)
