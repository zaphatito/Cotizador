# sqlModels/products_repo.py
from __future__ import annotations

import math
import sqlite3

import pandas as pd

from .utils import now_iso

_CATS = {"ESENCIA", "AROMATERAPIA", "ESENCIAS"}


def _to_float(v, default: float = 0.0) -> float:
    try:
        if v is None:
            return float(default)

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
        return float(x)
    except Exception:
        return float(default)


def _to_text(v) -> str:
    return str(v or "").strip()


def upsert_products_snapshot(con: sqlite3.Connection, import_id: int, df: pd.DataFrame) -> None:
    """
    Espera columnas de producto (estructura excel):
      CODIGO, NOMBRE, DEPARTAMENTO, GENERO,
      CANTIDAD_DISPONIBLE, P_MAX, P_MIN, P_OFERTA, __FUENTE

    Tambien soporta columnas de compatibilidad previas.
    """
    if df is None or df.empty:
        return

    now = now_iso()

    rows_raw_hist: list[tuple] = []
    rows_raw_cur: list[tuple] = []

    rows_hist: list[tuple] = []
    rows_cur: list[tuple] = []

    for _, r in df.iterrows():
        codigo = _to_text(r.get("CODIGO") or r.get("codigo") or r.get("id"))
        if not codigo:
            continue

        nombre = _to_text(r.get("NOMBRE") or r.get("nombre"))
        depto = _to_text(r.get("DEPARTAMENTO") or r.get("departamento") or r.get("categoria"))
        genero = _to_text(r.get("GENERO") or r.get("genero"))

        cantidad = _to_float(r.get("CANTIDAD_DISPONIBLE") if "CANTIDAD_DISPONIBLE" in r else r.get("cantidad_disponible"), 0.0)
        p_max = _to_float(r.get("P_MAX") if "P_MAX" in r else r.get("precio_venta"), 0.0)
        p_min = _to_float(r.get("P_MIN") if "P_MIN" in r else r.get("precio_minimo_base"), 0.0)
        p_oferta = _to_float(r.get("P_OFERTA") if "P_OFERTA" in r else r.get("precio_oferta_base"), 0.0)

        fuente = _to_text(r.get("__FUENTE") or r.get("__fuente") or r.get("fuente"))

        depto_u = depto.upper()
        categoria = depto_u

        precio_venta = float(p_max)
        precio_oferta_base = float(p_oferta)
        precio_minimo_base = float(p_min)

        precio_unitario = 0.0
        precio_unidad = 0.0
        precio_base_50g = 0.0

        if categoria == "BOTELLAS":
            precio_unidad = precio_venta
        elif categoria in _CATS:
            precio_base_50g = precio_venta
        else:
            precio_unitario = precio_venta

        ml = _to_text(r.get("ml"))

        rows_raw_hist.append(
            (
                int(import_id),
                codigo,
                nombre,
                depto,
                genero,
                float(cantidad),
                float(p_max),
                float(p_min),
                float(p_oferta),
                fuente,
            )
        )
        rows_raw_cur.append(
            (
                codigo,
                nombre,
                depto,
                genero,
                float(cantidad),
                float(p_max),
                float(p_min),
                float(p_oferta),
                fuente,
                now,
            )
        )

        rows_hist.append(
            (
                int(import_id),
                codigo,
                codigo,
                nombre,
                categoria,
                depto,
                genero,
                ml,
                float(cantidad),
                float(p_max),
                float(p_min),
                float(p_oferta),
                float(precio_unitario),
                float(precio_unidad),
                float(precio_base_50g),
                float(precio_oferta_base),
                float(precio_minimo_base),
                float(precio_venta),
                fuente,
            )
        )
        rows_cur.append(
            (
                codigo,
                codigo,
                nombre,
                categoria,
                depto,
                genero,
                ml,
                float(cantidad),
                float(p_max),
                float(p_min),
                float(p_oferta),
                float(precio_unitario),
                float(precio_unidad),
                float(precio_base_50g),
                float(precio_oferta_base),
                float(precio_minimo_base),
                float(precio_venta),
                fuente,
                now,
            )
        )

    if rows_raw_hist:
        con.executemany(
            """
            INSERT OR REPLACE INTO producto_hist(
                import_id, codigo, nombre, departamento, genero,
                cantidad_disponible, p_max, p_min, p_oferta, fuente
            )
            VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            rows_raw_hist,
        )

    if rows_raw_cur:
        con.executemany(
            """
            INSERT INTO producto_current(
                codigo, nombre, departamento, genero,
                cantidad_disponible, p_max, p_min, p_oferta,
                fuente, updated_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(codigo) DO UPDATE SET
                nombre=excluded.nombre,
                departamento=excluded.departamento,
                genero=excluded.genero,
                cantidad_disponible=excluded.cantidad_disponible,
                p_max=excluded.p_max,
                p_min=excluded.p_min,
                p_oferta=excluded.p_oferta,
                fuente=excluded.fuente,
                updated_at=excluded.updated_at
            """,
            rows_raw_cur,
        )

    if rows_hist:
        con.executemany(
            """
            INSERT OR REPLACE INTO products_hist(
                import_id, id, codigo,
                nombre, categoria, departamento, genero, ml,
                cantidad_disponible,
                p_max, p_min, p_oferta,
                precio_unitario, precio_unidad, precio_base_50g,
                precio_oferta_base, precio_minimo_base, precio_venta,
                fuente
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            rows_hist,
        )

    if rows_cur:
        con.executemany(
            """
            INSERT INTO products_current(
                id, codigo,
                nombre, categoria, departamento, genero, ml,
                cantidad_disponible,
                p_max, p_min, p_oferta,
                precio_unitario, precio_unidad, precio_base_50g,
                precio_oferta_base, precio_minimo_base, precio_venta,
                fuente, updated_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                codigo=excluded.codigo,
                nombre=excluded.nombre,
                categoria=excluded.categoria,
                departamento=excluded.departamento,
                genero=excluded.genero,
                ml=excluded.ml,
                cantidad_disponible=excluded.cantidad_disponible,
                p_max=excluded.p_max,
                p_min=excluded.p_min,
                p_oferta=excluded.p_oferta,
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
