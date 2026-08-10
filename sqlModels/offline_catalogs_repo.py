from __future__ import annotations

import os
import sqlite3
import unicodedata

import pandas as pd

from .utils import now_iso


OFFLINE_CATALOG_DDL = [
    """
    CREATE TABLE IF NOT EXISTS offline_catalogs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        name_key TEXT NOT NULL UNIQUE,
        source_file TEXT NOT NULL DEFAULT '',
        source_hash TEXT NOT NULL DEFAULT '',
        is_active INTEGER NOT NULL DEFAULT 0 CHECK(is_active IN (0, 1)),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_offline_catalogs_active
    ON offline_catalogs(is_active)
    WHERE is_active = 1
    """,
    """
    CREATE TABLE IF NOT EXISTS offline_catalog_products (
        catalog_id INTEGER NOT NULL,
        id TEXT NOT NULL,
        codigo TEXT,
        nombre TEXT,
        categoria TEXT,
        departamento TEXT,
        genero TEXT,
        ml TEXT,
        cantidad_disponible REAL NOT NULL DEFAULT 0,
        p_max REAL NOT NULL DEFAULT 0,
        p_min REAL NOT NULL DEFAULT 0,
        p_oferta REAL NOT NULL DEFAULT 0,
        precio_venta INTEGER NOT NULL DEFAULT 1,
        fuente TEXT,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (catalog_id, id),
        FOREIGN KEY (catalog_id) REFERENCES offline_catalogs(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_offline_catalog_products_code
    ON offline_catalog_products(catalog_id, codigo)
    """,
    """
    CREATE TABLE IF NOT EXISTS offline_catalog_presentations (
        catalog_id INTEGER NOT NULL,
        codigo_norm TEXT NOT NULL,
        departamento TEXT NOT NULL DEFAULT '',
        genero TEXT NOT NULL DEFAULT '',
        codigo TEXT,
        nombre TEXT,
        descripcion TEXT,
        p_max REAL NOT NULL DEFAULT 0,
        p_min REAL NOT NULL DEFAULT 0,
        p_oferta REAL NOT NULL DEFAULT 0,
        requiere_botella INTEGER NOT NULL DEFAULT 0,
        stock_disponible REAL NOT NULL DEFAULT 0,
        codigos_producto TEXT NOT NULL DEFAULT '',
        fuente TEXT,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (catalog_id, codigo_norm, departamento, genero),
        FOREIGN KEY (catalog_id) REFERENCES offline_catalogs(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS offline_catalog_presentation_products (
        catalog_id INTEGER NOT NULL,
        cod_producto TEXT NOT NULL,
        cod_presentacion TEXT NOT NULL,
        departamento TEXT NOT NULL DEFAULT '',
        genero TEXT NOT NULL DEFAULT '',
        cantidad REAL NOT NULL DEFAULT 0,
        fuente TEXT,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (
            catalog_id,
            cod_producto,
            cod_presentacion,
            departamento,
            genero
        ),
        FOREIGN KEY (catalog_id) REFERENCES offline_catalogs(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_offline_catalog_rel_presentation
    ON offline_catalog_presentation_products(catalog_id, cod_presentacion)
    """,
]


def ensure_offline_catalog_schema(con: sqlite3.Connection) -> None:
    for statement in OFFLINE_CATALOG_DDL:
        con.execute(statement)


def normalize_catalog_name(name: object) -> str:
    normalized = " ".join(str(name or "").strip().split())
    if not normalized:
        raise ValueError("El nombre del catálogo no puede quedar vacío.")
    if len(normalized) > 120:
        raise ValueError("El nombre del catálogo no puede superar 120 caracteres.")
    return normalized


def catalog_name_key(name: object) -> str:
    normalized = normalize_catalog_name(name)
    return unicodedata.normalize("NFKC", normalized).casefold()


def _row_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def _catalog_select_sql(*, where: str = "") -> str:
    return f"""
        SELECT
            c.*,
            (SELECT COUNT(*)
             FROM offline_catalog_products p
             WHERE p.catalog_id = c.id) AS product_count,
            (SELECT COUNT(*)
             FROM offline_catalog_presentations p
             WHERE p.catalog_id = c.id) AS presentation_count
        FROM offline_catalogs c
        {where}
    """


def list_catalogs(con: sqlite3.Connection) -> list[dict]:
    ensure_offline_catalog_schema(con)
    rows = con.execute(
        _catalog_select_sql(
            where="ORDER BY c.is_active DESC, c.name COLLATE NOCASE, c.id"
        )
    ).fetchall()
    return [dict(row) for row in rows]


def get_catalog(con: sqlite3.Connection, catalog_id: int) -> dict | None:
    ensure_offline_catalog_schema(con)
    row = con.execute(
        _catalog_select_sql(where="WHERE c.id = ?"),
        (int(catalog_id),),
    ).fetchone()
    return _row_dict(row)


def get_active_catalog(con: sqlite3.Connection) -> dict | None:
    ensure_offline_catalog_schema(con)
    row = con.execute(
        _catalog_select_sql(
            where="ORDER BY c.is_active DESC, c.id ASC LIMIT 1"
        )
    ).fetchone()
    return _row_dict(row)


def create_catalog(con: sqlite3.Connection, name: object) -> int:
    ensure_offline_catalog_schema(con)
    normalized = normalize_catalog_name(name)
    now = now_iso()
    try:
        cursor = con.execute(
            """
            INSERT INTO offline_catalogs(
                name, name_key, source_file, source_hash,
                is_active, created_at, updated_at
            )
            VALUES(?, ?, '', '', 0, ?, ?)
            """,
            (normalized, catalog_name_key(normalized), now, now),
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"Ya existe un catálogo llamado '{normalized}'.") from exc
    return int(cursor.lastrowid)


def _require_catalog(con: sqlite3.Connection, catalog_id: int) -> dict:
    record = get_catalog(con, int(catalog_id))
    if record is None:
        raise KeyError(f"No existe el catálogo offline {catalog_id}.")
    return record


def set_active_catalog(con: sqlite3.Connection, catalog_id: int) -> dict:
    _require_catalog(con, catalog_id)
    con.execute("UPDATE offline_catalogs SET is_active = 0 WHERE is_active <> 0")
    con.execute(
        "UPDATE offline_catalogs SET is_active = 1 WHERE id = ?",
        (int(catalog_id),),
    )
    return _require_catalog(con, catalog_id)


def replace_catalog_from_current(
    con: sqlite3.Connection,
    catalog_id: int,
    *,
    source_file: str = "",
    source_hash: str = "",
) -> dict:
    """Reemplaza un snapshot offline usando las tablas ``*_current`` activas."""
    _require_catalog(con, catalog_id)
    catalog_id = int(catalog_id)

    con.execute(
        "DELETE FROM offline_catalog_presentation_products WHERE catalog_id = ?",
        (catalog_id,),
    )
    con.execute(
        "DELETE FROM offline_catalog_presentations WHERE catalog_id = ?",
        (catalog_id,),
    )
    con.execute(
        "DELETE FROM offline_catalog_products WHERE catalog_id = ?",
        (catalog_id,),
    )

    con.execute(
        """
        INSERT INTO offline_catalog_products(
            catalog_id, id, codigo, nombre, categoria, departamento, genero, ml,
            cantidad_disponible, p_max, p_min, p_oferta, precio_venta,
            fuente, updated_at
        )
        SELECT
            ?, id, codigo, nombre, categoria, departamento, genero, ml,
            cantidad_disponible, p_max, p_min, p_oferta, precio_venta,
            fuente, updated_at
        FROM products_current
        """,
        (catalog_id,),
    )
    con.execute(
        """
        INSERT INTO offline_catalog_presentations(
            catalog_id, codigo_norm, departamento, genero, codigo, nombre,
            descripcion, p_max, p_min, p_oferta, requiere_botella,
            stock_disponible, codigos_producto, fuente, updated_at
        )
        SELECT
            ?, codigo_norm, departamento, genero, codigo, nombre,
            descripcion, p_max, p_min, p_oferta, requiere_botella,
            stock_disponible, codigos_producto, fuente, updated_at
        FROM presentations_current
        """,
        (catalog_id,),
    )
    con.execute(
        """
        INSERT INTO offline_catalog_presentation_products(
            catalog_id, cod_producto, cod_presentacion, departamento, genero,
            cantidad, fuente, updated_at
        )
        SELECT
            ?, cod_producto, cod_presentacion, departamento, genero,
            cantidad, fuente, updated_at
        FROM presentacion_prod_current
        """,
        (catalog_id,),
    )

    product_count = con.execute(
        "SELECT COUNT(*) AS total FROM offline_catalog_products WHERE catalog_id = ?",
        (catalog_id,),
    ).fetchone()["total"]
    if int(product_count or 0) <= 0:
        raise ValueError("El archivo no contiene productos válidos para el catálogo.")

    con.execute(
        """
        UPDATE offline_catalogs
        SET source_file = ?, source_hash = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            os.path.basename(str(source_file or "").strip()),
            str(source_hash or "").strip(),
            now_iso(),
            catalog_id,
        ),
    )
    set_active_catalog(con, catalog_id)
    return _require_catalog(con, catalog_id)


def materialize_catalog(con: sqlite3.Connection, catalog_id: int) -> dict:
    """Convierte un snapshot offline en el catálogo current usado por la app."""
    record = _require_catalog(con, catalog_id)
    catalog_id = int(catalog_id)

    con.execute("DELETE FROM presentacion_prod_current")
    con.execute("DELETE FROM presentations_current")
    con.execute("DELETE FROM products_current")

    con.execute(
        """
        INSERT INTO products_current(
            id, codigo, nombre, categoria, departamento, genero, ml,
            cantidad_disponible, p_max, p_min, p_oferta, precio_venta,
            fuente, updated_at
        )
        SELECT
            id, codigo, nombre, categoria, departamento, genero, ml,
            cantidad_disponible, p_max, p_min, p_oferta, precio_venta,
            fuente, updated_at
        FROM offline_catalog_products
        WHERE catalog_id = ?
        """,
        (catalog_id,),
    )
    con.execute(
        """
        INSERT INTO presentations_current(
            codigo_norm, departamento, genero, codigo, nombre, descripcion,
            p_max, p_min, p_oferta, requiere_botella, stock_disponible,
            codigos_producto, fuente, updated_at
        )
        SELECT
            codigo_norm, departamento, genero, codigo, nombre, descripcion,
            p_max, p_min, p_oferta, requiere_botella, stock_disponible,
            codigos_producto, fuente, updated_at
        FROM offline_catalog_presentations
        WHERE catalog_id = ?
        """,
        (catalog_id,),
    )
    con.execute(
        """
        INSERT INTO presentacion_prod_current(
            cod_producto, cod_presentacion, departamento, genero,
            cantidad, fuente, updated_at
        )
        SELECT
            cod_producto, cod_presentacion, departamento, genero,
            cantidad, fuente, updated_at
        FROM offline_catalog_presentation_products
        WHERE catalog_id = ?
        """,
        (catalog_id,),
    )
    set_active_catalog(con, catalog_id)
    return record


def load_catalog_frames(
    con: sqlite3.Connection,
    catalog_id: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    _require_catalog(con, catalog_id)
    products = pd.read_sql_query(
        """
        SELECT
            id, codigo, nombre, categoria, departamento, genero, ml,
            cantidad_disponible, p_max, p_min, p_oferta, precio_venta,
            fuente, updated_at
        FROM offline_catalog_products
        WHERE catalog_id = ?
        """,
        con,
        params=(int(catalog_id),),
    )
    presentations = pd.read_sql_query(
        """
        SELECT
            codigo_norm, departamento, genero, codigo, nombre, descripcion,
            p_max, p_min, p_oferta, requiere_botella, stock_disponible,
            codigos_producto, fuente, updated_at
        FROM offline_catalog_presentations
        WHERE catalog_id = ?
        """,
        con,
        params=(int(catalog_id),),
    )
    return products, presentations


def bootstrap_current_catalog(
    con: sqlite3.Connection,
    *,
    name: str = "Catálogo local",
) -> dict | None:
    """Adopta una instalación legacy solo cuando aún no hay catálogos offline."""
    if list_catalogs(con):
        return get_active_catalog(con)
    product_count = con.execute(
        "SELECT COUNT(*) AS total FROM products_current"
    ).fetchone()["total"]
    if int(product_count or 0) <= 0:
        return None
    catalog_id = create_catalog(con, name)
    return replace_catalog_from_current(con, catalog_id)


__all__ = [
    "OFFLINE_CATALOG_DDL",
    "bootstrap_current_catalog",
    "catalog_name_key",
    "create_catalog",
    "ensure_offline_catalog_schema",
    "get_active_catalog",
    "get_catalog",
    "list_catalogs",
    "load_catalog_frames",
    "materialize_catalog",
    "normalize_catalog_name",
    "replace_catalog_from_current",
    "set_active_catalog",
]
