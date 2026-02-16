from __future__ import annotations

import sqlite3


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    r = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return r is not None


def _column_exists(con: sqlite3.Connection, table: str, col: str) -> bool:
    if not _table_exists(con, table):
        return False
    rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    cols = {str(r["name"]).lower() for r in rows}
    return col.lower() in cols


def _add_column_if_missing(con: sqlite3.Connection, table: str, col: str, col_def_sql: str) -> None:
    """
    col_def_sql ejemplo: "TEXT NOT NULL DEFAULT ''"
    """
    if _column_exists(con, table, col):
        return
    con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_def_sql}")


def mig_1(con: sqlite3.Connection) -> None:
    return


def mig_2(con: sqlite3.Connection) -> None:
    return


def mig_3(con: sqlite3.Connection) -> None:
    """
    v3: Histórico de tasas de cambio
    - Crea exchange_rates_history
    - Index para consultas rápidas por par y fecha
    - Backfill: inserta el “current” como primer histórico si aún no existe
    """
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS exchange_rates_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            base_currency TEXT NOT NULL,
            currency TEXT NOT NULL,
            rate REAL NOT NULL,
            recorded_at TEXT NOT NULL
        )
        """
    )

    con.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_exchange_rates_history_pair_time
        ON exchange_rates_history(base_currency, currency, recorded_at)
        """
    )

    if _table_exists(con, "exchange_rates"):
        con.execute(
            """
            INSERT INTO exchange_rates_history(base_currency, currency, rate, recorded_at)
            SELECT er.base_currency, er.currency, er.rate,
                   COALESCE(er.updated_at, datetime('now'))
            FROM exchange_rates er
            WHERE NOT EXISTS (
                SELECT 1
                FROM exchange_rates_history h
                WHERE h.base_currency = er.base_currency
                  AND h.currency = er.currency
            )
            """
        )


def mig_4(con: sqlite3.Connection) -> None:
    """
    v4: Guardar método de pago en quotes
    - Agrega columna quotes.metodo_pago (si falta)
    """
    _add_column_if_missing(con, "quotes", "metodo_pago", "TEXT NOT NULL DEFAULT ''")


def mig_5(con: sqlite3.Connection) -> None:
    """
    v5: Estado en quotes
    - Agrega columna quotes.estado (si falta)
    - Crea index idx_quotes_estado
    """
    _add_column_if_missing(con, "quotes", "estado", "TEXT NOT NULL DEFAULT ''")
    con.execute("CREATE INDEX IF NOT EXISTS idx_quotes_estado ON quotes(estado)")


def mig_6(con: sqlite3.Connection) -> None:
    """
    v6: Kill switch IA en settings
    - Garantiza settings.enable_ai = '0' (desactivado) en equipos actualizados.
    """
    con.execute(
        """
        INSERT INTO settings(key, value) VALUES('enable_ai', '0')
        ON CONFLICT(key) DO UPDATE SET value='0'
        """
    )


def mig_7(con: sqlite3.Connection) -> None:
    """
    v7: Switch de recomendaciones en settings
    - Garantiza settings.enable_recommendations (default activo).
    """
    con.execute(
        """
        INSERT OR IGNORE INTO settings(key, value) VALUES('enable_recommendations', '1')
        """
    )


def mig_8(con: sqlite3.Connection) -> None:
    """
    v8: Estructura de catalogo alineada al Excel (producto/presentacion/presentacion_prod)
    - Agrega columnas nuevas en tablas compat existentes
    - Crea tablas raw con la estructura del Excel
    """
    # products_current / products_hist
    _add_column_if_missing(con, "products_current", "codigo", "TEXT")
    _add_column_if_missing(con, "products_current", "departamento", "TEXT")
    _add_column_if_missing(con, "products_current", "p_max", "REAL NOT NULL DEFAULT 0")
    _add_column_if_missing(con, "products_current", "p_min", "REAL NOT NULL DEFAULT 0")
    _add_column_if_missing(con, "products_current", "p_oferta", "REAL NOT NULL DEFAULT 0")

    _add_column_if_missing(con, "products_hist", "codigo", "TEXT")
    _add_column_if_missing(con, "products_hist", "departamento", "TEXT")
    _add_column_if_missing(con, "products_hist", "p_max", "REAL NOT NULL DEFAULT 0")
    _add_column_if_missing(con, "products_hist", "p_min", "REAL NOT NULL DEFAULT 0")
    _add_column_if_missing(con, "products_hist", "p_oferta", "REAL NOT NULL DEFAULT 0")

    con.execute(
        """
        UPDATE products_current
        SET
            codigo = COALESCE(NULLIF(codigo, ''), id),
            departamento = COALESCE(NULLIF(departamento, ''), categoria),
            p_max = CASE WHEN COALESCE(p_max, 0) = 0 THEN COALESCE(precio_venta, 0) ELSE p_max END,
            p_min = CASE WHEN COALESCE(p_min, 0) = 0 THEN COALESCE(precio_minimo_base, 0) ELSE p_min END,
            p_oferta = CASE WHEN COALESCE(p_oferta, 0) = 0 THEN COALESCE(precio_oferta_base, 0) ELSE p_oferta END
        """
    )
    con.execute(
        """
        UPDATE products_hist
        SET
            codigo = COALESCE(NULLIF(codigo, ''), id),
            departamento = COALESCE(NULLIF(departamento, ''), categoria),
            p_max = CASE WHEN COALESCE(p_max, 0) = 0 THEN COALESCE(precio_venta, 0) ELSE p_max END,
            p_min = CASE WHEN COALESCE(p_min, 0) = 0 THEN COALESCE(precio_minimo_base, 0) ELSE p_min END,
            p_oferta = CASE WHEN COALESCE(p_oferta, 0) = 0 THEN COALESCE(precio_oferta_base, 0) ELSE p_oferta END
        """
    )

    # presentations_current / presentations_hist
    _add_column_if_missing(con, "presentations_current", "descripcion", "TEXT")
    _add_column_if_missing(con, "presentations_current", "p_max", "REAL NOT NULL DEFAULT 0")
    _add_column_if_missing(con, "presentations_current", "p_min", "REAL NOT NULL DEFAULT 0")
    _add_column_if_missing(con, "presentations_current", "p_oferta", "REAL NOT NULL DEFAULT 0")
    _add_column_if_missing(con, "presentations_current", "stock_disponible", "REAL NOT NULL DEFAULT 0")
    _add_column_if_missing(con, "presentations_current", "codigos_producto", "TEXT NOT NULL DEFAULT ''")
    _add_column_if_missing(con, "presentations_current", "fuente", "TEXT")

    _add_column_if_missing(con, "presentations_hist", "descripcion", "TEXT")
    _add_column_if_missing(con, "presentations_hist", "p_max", "REAL NOT NULL DEFAULT 0")
    _add_column_if_missing(con, "presentations_hist", "p_min", "REAL NOT NULL DEFAULT 0")
    _add_column_if_missing(con, "presentations_hist", "p_oferta", "REAL NOT NULL DEFAULT 0")
    _add_column_if_missing(con, "presentations_hist", "stock_disponible", "REAL NOT NULL DEFAULT 0")
    _add_column_if_missing(con, "presentations_hist", "codigos_producto", "TEXT NOT NULL DEFAULT ''")
    _add_column_if_missing(con, "presentations_hist", "fuente", "TEXT")

    con.execute(
        """
        UPDATE presentations_current
        SET
            p_max = CASE WHEN COALESCE(p_max, 0) = 0 THEN COALESCE(precio_present, 0) ELSE p_max END,
            p_min = COALESCE(p_min, 0),
            p_oferta = COALESCE(p_oferta, 0)
        """
    )
    con.execute(
        """
        UPDATE presentations_hist
        SET
            p_max = CASE WHEN COALESCE(p_max, 0) = 0 THEN COALESCE(precio_present, 0) ELSE p_max END,
            p_min = COALESCE(p_min, 0),
            p_oferta = COALESCE(p_oferta, 0)
        """
    )

    # Raw tables (estructura excel)
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS producto_current (
            codigo TEXT PRIMARY KEY,
            nombre TEXT,
            departamento TEXT,
            genero TEXT,
            cantidad_disponible REAL NOT NULL DEFAULT 0,
            p_max REAL NOT NULL DEFAULT 0,
            p_min REAL NOT NULL DEFAULT 0,
            p_oferta REAL NOT NULL DEFAULT 0,
            fuente TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS producto_hist (
            import_id INTEGER NOT NULL,
            codigo TEXT NOT NULL,
            nombre TEXT,
            departamento TEXT,
            genero TEXT,
            cantidad_disponible REAL NOT NULL DEFAULT 0,
            p_max REAL NOT NULL DEFAULT 0,
            p_min REAL NOT NULL DEFAULT 0,
            p_oferta REAL NOT NULL DEFAULT 0,
            fuente TEXT,
            PRIMARY KEY (import_id, codigo),
            FOREIGN KEY (import_id) REFERENCES imports(id) ON DELETE CASCADE
        )
        """
    )
    con.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_producto_hist_codigo
        ON producto_hist(codigo)
        """
    )

    con.execute(
        """
        CREATE TABLE IF NOT EXISTS presentacion_current (
            codigo_norm TEXT PRIMARY KEY,
            codigo TEXT,
            nombre TEXT,
            descripcion TEXT,
            departamento TEXT,
            genero TEXT,
            p_max REAL NOT NULL DEFAULT 0,
            p_min REAL NOT NULL DEFAULT 0,
            p_oferta REAL NOT NULL DEFAULT 0,
            fuente TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS presentacion_hist (
            import_id INTEGER NOT NULL,
            codigo_norm TEXT NOT NULL,
            codigo TEXT,
            nombre TEXT,
            descripcion TEXT,
            departamento TEXT,
            genero TEXT,
            p_max REAL NOT NULL DEFAULT 0,
            p_min REAL NOT NULL DEFAULT 0,
            p_oferta REAL NOT NULL DEFAULT 0,
            fuente TEXT,
            PRIMARY KEY (import_id, codigo_norm),
            FOREIGN KEY (import_id) REFERENCES imports(id) ON DELETE CASCADE
        )
        """
    )
    con.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_presentacion_hist_codigo
        ON presentacion_hist(codigo_norm)
        """
    )

    con.execute(
        """
        CREATE TABLE IF NOT EXISTS presentacion_prod_current (
            cod_producto TEXT NOT NULL,
            cod_presentacion TEXT NOT NULL,
            departamento TEXT NOT NULL DEFAULT '',
            genero TEXT NOT NULL DEFAULT '',
            cantidad REAL NOT NULL DEFAULT 0,
            fuente TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (cod_producto, cod_presentacion, departamento, genero)
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS presentacion_prod_hist (
            import_id INTEGER NOT NULL,
            cod_producto TEXT NOT NULL,
            cod_presentacion TEXT NOT NULL,
            departamento TEXT NOT NULL DEFAULT '',
            genero TEXT NOT NULL DEFAULT '',
            cantidad REAL NOT NULL DEFAULT 0,
            fuente TEXT,
            PRIMARY KEY (import_id, cod_producto, cod_presentacion, departamento, genero),
            FOREIGN KEY (import_id) REFERENCES imports(id) ON DELETE CASCADE
        )
        """
    )
    con.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_presentacion_prod_current_presentacion
        ON presentacion_prod_current(cod_presentacion)
        """
    )
    con.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_presentacion_prod_current_producto
        ON presentacion_prod_current(cod_producto)
        """
    )

    # Backfill raw tables from compat tables (best effort)
    con.execute(
        """
        INSERT OR REPLACE INTO producto_current(
            codigo, nombre, departamento, genero,
            cantidad_disponible, p_max, p_min, p_oferta, fuente, updated_at
        )
        SELECT
            COALESCE(NULLIF(codigo, ''), id),
            nombre,
            COALESCE(NULLIF(departamento, ''), categoria),
            genero,
            COALESCE(cantidad_disponible, 0),
            COALESCE(p_max, precio_venta, 0),
            COALESCE(p_min, precio_minimo_base, 0),
            COALESCE(p_oferta, precio_oferta_base, 0),
            fuente,
            COALESCE(updated_at, datetime('now'))
        FROM products_current
        """
    )
    con.execute(
        """
        INSERT OR REPLACE INTO presentacion_current(
            codigo_norm, codigo, nombre, descripcion, departamento, genero,
            p_max, p_min, p_oferta, fuente, updated_at
        )
        SELECT
            codigo_norm,
            codigo,
            nombre,
            descripcion,
            departamento,
            genero,
            COALESCE(p_max, precio_present, 0),
            COALESCE(p_min, 0),
            COALESCE(p_oferta, 0),
            fuente,
            COALESCE(updated_at, datetime('now'))
        FROM presentations_current
        """
    )


def mig_9(con: sqlite3.Connection) -> None:
    """
    v9: Permitir multiples presentaciones con el mismo codigo (por departamento/genero).
    - Rebuild de tablas current/hist para usar PK compuesta:
      (codigo_norm, departamento, genero)
    """

    def _rebuild_table(name: str, create_sql: str, copy_sql: str) -> None:
        if not _table_exists(con, name):
            con.execute(create_sql)
            return
        bak = f"{name}__bak_v9"
        con.execute(f"DROP TABLE IF EXISTS {bak}")
        con.execute(f"ALTER TABLE {name} RENAME TO {bak}")
        con.execute(create_sql)
        con.execute(copy_sql.format(src=bak))
        con.execute(f"DROP TABLE IF EXISTS {bak}")

    _rebuild_table(
        "presentations_current",
        """
        CREATE TABLE IF NOT EXISTS presentations_current (
            codigo_norm TEXT NOT NULL,
            departamento TEXT NOT NULL DEFAULT '',
            genero TEXT NOT NULL DEFAULT '',
            codigo TEXT,
            nombre TEXT,
            descripcion TEXT,
            p_max REAL NOT NULL DEFAULT 0,
            p_min REAL NOT NULL DEFAULT 0,
            p_oferta REAL NOT NULL DEFAULT 0,
            precio_present REAL NOT NULL DEFAULT 0,
            requiere_botella INTEGER NOT NULL DEFAULT 0,
            stock_disponible REAL NOT NULL DEFAULT 0,
            codigos_producto TEXT NOT NULL DEFAULT '',
            fuente TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (codigo_norm, departamento, genero)
        )
        """,
        """
        INSERT OR REPLACE INTO presentations_current(
            codigo_norm, departamento, genero,
            codigo, nombre, descripcion,
            p_max, p_min, p_oferta,
            precio_present, requiere_botella,
            stock_disponible, codigos_producto,
            fuente, updated_at
        )
        SELECT
            UPPER(TRIM(COALESCE(codigo_norm, codigo, ''))),
            UPPER(TRIM(COALESCE(departamento, ''))),
            LOWER(TRIM(COALESCE(genero, ''))),
            UPPER(TRIM(COALESCE(codigo, codigo_norm, ''))),
            COALESCE(nombre, ''),
            COALESCE(descripcion, ''),
            COALESCE(p_max, 0),
            COALESCE(p_min, 0),
            COALESCE(p_oferta, 0),
            COALESCE(precio_present, 0),
            COALESCE(requiere_botella, 0),
            COALESCE(stock_disponible, 0),
            COALESCE(codigos_producto, ''),
            COALESCE(fuente, ''),
            COALESCE(updated_at, datetime('now'))
        FROM {src}
        WHERE TRIM(COALESCE(codigo_norm, codigo, '')) <> ''
        """,
    )

    _rebuild_table(
        "presentations_hist",
        """
        CREATE TABLE IF NOT EXISTS presentations_hist (
            import_id INTEGER NOT NULL,
            codigo_norm TEXT NOT NULL,
            departamento TEXT NOT NULL DEFAULT '',
            genero TEXT NOT NULL DEFAULT '',
            codigo TEXT,
            nombre TEXT,
            descripcion TEXT,
            p_max REAL NOT NULL DEFAULT 0,
            p_min REAL NOT NULL DEFAULT 0,
            p_oferta REAL NOT NULL DEFAULT 0,
            precio_present REAL NOT NULL DEFAULT 0,
            requiere_botella INTEGER NOT NULL DEFAULT 0,
            stock_disponible REAL NOT NULL DEFAULT 0,
            codigos_producto TEXT NOT NULL DEFAULT '',
            fuente TEXT,
            PRIMARY KEY (import_id, codigo_norm, departamento, genero),
            FOREIGN KEY (import_id) REFERENCES imports(id) ON DELETE CASCADE
        )
        """,
        """
        INSERT OR REPLACE INTO presentations_hist(
            import_id, codigo_norm, departamento, genero,
            codigo, nombre, descripcion,
            p_max, p_min, p_oferta,
            precio_present, requiere_botella,
            stock_disponible, codigos_producto,
            fuente
        )
        SELECT
            import_id,
            UPPER(TRIM(COALESCE(codigo_norm, codigo, ''))),
            UPPER(TRIM(COALESCE(departamento, ''))),
            LOWER(TRIM(COALESCE(genero, ''))),
            UPPER(TRIM(COALESCE(codigo, codigo_norm, ''))),
            COALESCE(nombre, ''),
            COALESCE(descripcion, ''),
            COALESCE(p_max, 0),
            COALESCE(p_min, 0),
            COALESCE(p_oferta, 0),
            COALESCE(precio_present, 0),
            COALESCE(requiere_botella, 0),
            COALESCE(stock_disponible, 0),
            COALESCE(codigos_producto, ''),
            COALESCE(fuente, '')
        FROM {src}
        WHERE TRIM(COALESCE(codigo_norm, codigo, '')) <> ''
        """,
    )
    con.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_presentations_hist_code
        ON presentations_hist(codigo_norm)
        """
    )

    _rebuild_table(
        "presentacion_current",
        """
        CREATE TABLE IF NOT EXISTS presentacion_current (
            codigo_norm TEXT NOT NULL,
            departamento TEXT NOT NULL DEFAULT '',
            genero TEXT NOT NULL DEFAULT '',
            codigo TEXT,
            nombre TEXT,
            descripcion TEXT,
            p_max REAL NOT NULL DEFAULT 0,
            p_min REAL NOT NULL DEFAULT 0,
            p_oferta REAL NOT NULL DEFAULT 0,
            fuente TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (codigo_norm, departamento, genero)
        )
        """,
        """
        INSERT OR REPLACE INTO presentacion_current(
            codigo_norm, departamento, genero,
            codigo, nombre, descripcion,
            p_max, p_min, p_oferta,
            fuente, updated_at
        )
        SELECT
            UPPER(TRIM(COALESCE(codigo_norm, codigo, ''))),
            UPPER(TRIM(COALESCE(departamento, ''))),
            LOWER(TRIM(COALESCE(genero, ''))),
            UPPER(TRIM(COALESCE(codigo, codigo_norm, ''))),
            COALESCE(nombre, ''),
            COALESCE(descripcion, ''),
            COALESCE(p_max, 0),
            COALESCE(p_min, 0),
            COALESCE(p_oferta, 0),
            COALESCE(fuente, ''),
            COALESCE(updated_at, datetime('now'))
        FROM {src}
        WHERE TRIM(COALESCE(codigo_norm, codigo, '')) <> ''
        """,
    )

    _rebuild_table(
        "presentacion_hist",
        """
        CREATE TABLE IF NOT EXISTS presentacion_hist (
            import_id INTEGER NOT NULL,
            codigo_norm TEXT NOT NULL,
            departamento TEXT NOT NULL DEFAULT '',
            genero TEXT NOT NULL DEFAULT '',
            codigo TEXT,
            nombre TEXT,
            descripcion TEXT,
            p_max REAL NOT NULL DEFAULT 0,
            p_min REAL NOT NULL DEFAULT 0,
            p_oferta REAL NOT NULL DEFAULT 0,
            fuente TEXT,
            PRIMARY KEY (import_id, codigo_norm, departamento, genero),
            FOREIGN KEY (import_id) REFERENCES imports(id) ON DELETE CASCADE
        )
        """,
        """
        INSERT OR REPLACE INTO presentacion_hist(
            import_id, codigo_norm, departamento, genero,
            codigo, nombre, descripcion,
            p_max, p_min, p_oferta,
            fuente
        )
        SELECT
            import_id,
            UPPER(TRIM(COALESCE(codigo_norm, codigo, ''))),
            UPPER(TRIM(COALESCE(departamento, ''))),
            LOWER(TRIM(COALESCE(genero, ''))),
            UPPER(TRIM(COALESCE(codigo, codigo_norm, ''))),
            COALESCE(nombre, ''),
            COALESCE(descripcion, ''),
            COALESCE(p_max, 0),
            COALESCE(p_min, 0),
            COALESCE(p_oferta, 0),
            COALESCE(fuente, '')
        FROM {src}
        WHERE TRIM(COALESCE(codigo_norm, codigo, '')) <> ''
        """,
    )
    con.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_presentacion_hist_codigo
        ON presentacion_hist(codigo_norm)
        """
    )

    # Forzar recarga desde Excel con la nueva PK compuesta.
    if _table_exists(con, "imports"):
        con.execute(
            """
            DELETE FROM imports
            WHERE kind IN ('presentations', 'presentacion_prod')
            """
        )


def mig_10(con: sqlite3.Connection) -> None:
    """
    v10: Configuracion de empresa y tienda en settings
    - Garantiza settings.company_type (2 valores permitidos)
    - Garantiza settings.store_id
    """
    con.execute(
        """
        INSERT OR IGNORE INTO settings(key, value) VALUES('company_type', 'LA CASA DEL PERFUME')
        """
    )
    con.execute(
        """
        INSERT OR IGNORE INTO settings(key, value) VALUES('store_id', '')
        """
    )
    con.execute(
        """
        UPDATE settings
        SET value = 'LA CASA DEL PERFUME'
        WHERE key = 'company_type'
          AND UPPER(TRIM(COALESCE(value, ''))) NOT IN ('EF PERFUMES', 'LA CASA DEL PERFUME')
        """
    )


MIGRATIONS: dict[int, callable] = {
    1: mig_1,
    2: mig_2,
    3: mig_3,
    4: mig_4,
    5: mig_5,
    6: mig_6,
    7: mig_7,
    8: mig_8,
    9: mig_9,
    10: mig_10,
}
