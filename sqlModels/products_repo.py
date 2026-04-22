# sqlModels/products_repo.py
from __future__ import annotations

import math
import sqlite3

import pandas as pd

from .utils import now_iso

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


def _to_price_id(v, default: int = 1) -> int:
    try:
        if v is None:
            return int(default)

        if isinstance(v, (int, float)):
            iv = int(v)
            return iv if iv in (1, 2, 3) else int(default)

        s = str(v).strip().lower()
        if not s:
            return int(default)

        if s in {"1", "p_max", "max", "maximo", "unitario", "base", "lista"}:
            return 1
        if s in {"2", "p_min", "min", "minimo"}:
            return 2
        if s in {"3", "p_oferta", "oferta", "promo", "promocion"}:
            return 3

        try:
            iv = int(float(s.replace(",", ".")))
            return iv if iv in (1, 2, 3) else int(default)
        except Exception:
            return int(default)
    except Exception:
        return int(default)


def _delete_current_products_by_sources(con: sqlite3.Connection, fuentes: set[str]) -> None:
    fuentes_norm = sorted({_to_text(f) for f in (fuentes or set())})
    if not fuentes_norm:
        return

    placeholders = ",".join("?" for _ in fuentes_norm)
    con.execute(
        f"DELETE FROM products_current WHERE COALESCE(fuente, '') IN ({placeholders})",
        tuple(fuentes_norm),
    )


def upsert_products_snapshot(
    con: sqlite3.Connection,
    import_id: int,
    df: pd.DataFrame,
    *,
    replace_current: bool = False,
    replace_sources: bool = False,
) -> None:
    """
    Espera columnas de producto (estructura excel):
      CODIGO, NOMBRE, DEPARTAMENTO, GENERO,
      CANTIDAD_DISPONIBLE, P_MAX, P_MIN, P_OFERTA, PRECIO_VENTA, __FUENTE

    Tambien soporta columnas de compatibilidad previas.

    Modos:
      - replace_current=True: reemplaza todo el current por este snapshot.
      - replace_sources=True: reemplaza solo las filas current de las fuentes
        presentes en este snapshot.
    """
    if df is None or df.empty:
        return

    now = now_iso()

    rows_hist: list[tuple] = []
    rows_cur: list[tuple] = []
    fuentes_snapshot: set[str] = set()

    for _, r in df.iterrows():
        codigo = _to_text(r.get("CODIGO") or r.get("codigo") or r.get("id"))
        if not codigo:
            continue

        nombre = _to_text(r.get("NOMBRE") or r.get("nombre"))
        depto = _to_text(r.get("DEPARTAMENTO") or r.get("departamento") or r.get("categoria"))
        genero = _to_text(r.get("GENERO") or r.get("genero"))

        cantidad = _to_float(r.get("CANTIDAD_DISPONIBLE") if "CANTIDAD_DISPONIBLE" in r else r.get("cantidad_disponible"), 0.0)
        p_max = _to_float(r.get("P_MAX") if "P_MAX" in r else r.get("p_max"), 0.0)
        p_min = _to_float(r.get("P_MIN") if "P_MIN" in r else r.get("p_min"), 0.0)
        p_oferta = _to_float(r.get("P_OFERTA") if "P_OFERTA" in r else r.get("p_oferta"), 0.0)
        precio_venta = _to_price_id(
            r.get("PRECIO_VENTA") if "PRECIO_VENTA" in r else r.get("precio_venta"),
            1,
        )

        fuente = _to_text(r.get("__FUENTE") or r.get("__fuente") or r.get("fuente"))
        fuentes_snapshot.add(fuente)

        depto_u = depto.upper()
        categoria = depto_u

        ml = _to_text(r.get("ml"))

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
                int(precio_venta),
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
                int(precio_venta),
                fuente,
                now,
            )
        )

    if replace_current:
        con.execute("DELETE FROM products_current")
    elif replace_sources:
        _delete_current_products_by_sources(con, fuentes_snapshot)

    if rows_hist:
        con.executemany(
            """
            INSERT OR REPLACE INTO products_hist(
                import_id, id, codigo,
                nombre, categoria, departamento, genero, ml,
                cantidad_disponible,
                p_max, p_min, p_oferta,
                precio_venta,
                fuente
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                precio_venta,
                fuente, updated_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                precio_venta=excluded.precio_venta,
                fuente=excluded.fuente,
                updated_at=excluded.updated_at
            """,
            rows_cur,
        )


def load_products_current(con: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query("SELECT * FROM products_current", con)
