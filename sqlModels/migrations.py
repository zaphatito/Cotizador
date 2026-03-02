from __future__ import annotations

import re
import sqlite3

from .api_identity import API_LOGIN_PASSWORD, build_api_settings


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


def _get_setting_value(con: sqlite3.Connection, key: str, default: str = "") -> str:
    if not _table_exists(con, "settings"):
        return str(default)
    r = con.execute(
        "SELECT value FROM settings WHERE key = ?",
        (str(key),),
    ).fetchone()
    if not r or r["value"] is None:
        return str(default)
    return str(r["value"])


def _refresh_api_settings_from_store_config(con: sqlite3.Connection) -> None:
    """
    Sincroniza id_user_api/user_api/password_api_hash en base a la configuracion
    actual de la tienda.
    """
    if not _table_exists(con, "settings"):
        return

    con.execute(
        """
        INSERT OR IGNORE INTO settings(key, value) VALUES('country', 'PARAGUAY')
        """
    )
    con.execute(
        """
        INSERT OR IGNORE INTO settings(key, value) VALUES('company_type', 'LA CASA DEL PERFUME')
        """
    )

    country = _get_setting_value(con, "country", "PARAGUAY")
    company = _get_setting_value(con, "company_type", "").strip()
    if not company:
        # Compatibilidad con instalaciones viejas que usaban key "company".
        company = _get_setting_value(con, "company", "LA CASA DEL PERFUME")
        _upsert_setting_value(con, "company_type", company)

    values = build_api_settings(
        country=country,
        company_type=company,
        password_plain=API_LOGIN_PASSWORD,
    )
    for k, v in values.items():
        _upsert_setting_value(con, k, v)


def _upsert_setting_value(con: sqlite3.Connection, key: str, value: str | None) -> None:
    if not _table_exists(con, "settings"):
        return
    con.execute(
        """
        INSERT INTO settings(key, value) VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        (str(key), None if value is None else str(value)),
    )


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


def mig_11(con: sqlite3.Connection) -> None:
    """
    v11: precio de venta por id en quote_items.
    Reglas:
    - 1 = p_max
    - 2 = p_min
    - 3 = p_oferta
    - 4 = personalizado (solo SERVICIO)
    """
    if not _table_exists(con, "quote_items"):
        return

    _add_column_if_missing(con, "quote_items", "id_precioventa", "INTEGER NOT NULL DEFAULT 1")

    con.execute(
        """
        UPDATE quote_items
        SET id_precioventa = CASE
            WHEN UPPER(TRIM(COALESCE(categoria, ''))) = 'SERVICIO' THEN 4
            WHEN LOWER(TRIM(COALESCE(precio_tier, ''))) IN ('minimo', 'mínimo', 'min') THEN 2
            WHEN LOWER(TRIM(COALESCE(precio_tier, ''))) IN ('oferta', 'promo', 'promocion', 'promoción') THEN 3
            WHEN COALESCE(id_precioventa, 0) BETWEEN 1 AND 3 THEN id_precioventa
            ELSE 1
        END
        """
    )

    con.execute(
        """
        UPDATE quote_items
        SET
            id_precioventa = 4,
            precio_tier = NULL,
            precio_override_base = COALESCE(precio_override_base, precio_base, 0)
        WHERE UPPER(TRIM(COALESCE(categoria, ''))) = 'SERVICIO'
        """
    )

    con.execute(
        """
        UPDATE quote_items
        SET
            id_precioventa = CASE
                WHEN COALESCE(id_precioventa, 0) = 2 THEN 2
                WHEN COALESCE(id_precioventa, 0) = 3 THEN 3
                ELSE 1
            END,
            precio_override_base = NULL,
            precio_tier = CASE
                WHEN COALESCE(id_precioventa, 0) = 2 THEN 'minimo'
                WHEN COALESCE(id_precioventa, 0) = 3 THEN 'oferta'
                ELSE 'unitario'
            END
        WHERE UPPER(TRIM(COALESCE(categoria, ''))) <> 'SERVICIO'
        """
    )


def mig_12(con: sqlite3.Connection) -> None:
    """
    v12: tipo de producto canonico en quote_items.
    Reglas:
    - serv = servicios
    - pres = presentaciones
    - prod = productos (default)
    """
    if not _table_exists(con, "quote_items"):
        return

    _add_column_if_missing(con, "quote_items", "tipo_prod", "TEXT NOT NULL DEFAULT 'prod'")

    con.execute(
        """
        UPDATE quote_items
        SET tipo_prod = CASE
            WHEN UPPER(TRIM(COALESCE(categoria, ''))) = 'SERVICIO' THEN 'serv'
            WHEN UPPER(TRIM(COALESCE(categoria, ''))) = 'PRESENTACION' THEN 'pres'
            WHEN LOWER(TRIM(COALESCE(tipo_prod, ''))) IN ('serv', 'servicio', 'service') THEN 'serv'
            WHEN LOWER(TRIM(COALESCE(tipo_prod, ''))) IN ('pres', 'presentacion', 'presentation') THEN 'pres'
            WHEN LOWER(TRIM(COALESCE(tipo_prod, ''))) IN ('prod', 'producto', 'product') THEN 'prod'
            ELSE 'prod'
        END
        """
    )


def mig_13(con: sqlite3.Connection) -> None:
    """
    v13: credenciales API para login externo en settings.
    Llaves:
    - id_user_api
    - user_api
    - password_api_hash (scrypt)
    """
    _refresh_api_settings_from_store_config(con)


def mig_14(con: sqlite3.Connection) -> None:
    """
    v14: resincroniza credenciales API con la configuracion actual de tienda.
    Corre una sola vez para bases que ya estaban en v13.
    """
    _refresh_api_settings_from_store_config(con)


def mig_15(con: sqlite3.Connection) -> None:
    """
    v15: marca de envio API en historico.
    - Agrega quotes.api_sent_at
    - Crea indice para pendientes de sincronizacion
    """
    if not _table_exists(con, "quotes"):
        return
    _add_column_if_missing(con, "quotes", "api_sent_at", "TEXT")
    con.execute("CREATE INDEX IF NOT EXISTS idx_quotes_api_sent_at ON quotes(api_sent_at)")


def mig_16(con: sqlite3.Connection) -> None:
    """
    v16: simplifica columnas de precio en products/presentations.

    Objetivo:
    - products_current/products_hist: conservar solo p_max, p_min, p_oferta
      y usar precio_venta como TIPO de precio por defecto (1/2/3).
    - presentations_current/presentations_hist: conservar solo p_max, p_min, p_oferta
      (eliminar precio_present).
    """

    def _rebuild_products_current() -> None:
        if not _table_exists(con, "products_current"):
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS products_current (
                    id TEXT PRIMARY KEY,
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
                    updated_at TEXT NOT NULL
                )
                """
            )
            return

        bak = "products_current__bak_v16"
        con.execute(f"DROP TABLE IF EXISTS {bak}")
        con.execute(f"ALTER TABLE products_current RENAME TO {bak}")

        has_precio_venta = _column_exists(con, bak, "precio_venta")
        has_p_min = _column_exists(con, bak, "p_min")
        has_p_oferta = _column_exists(con, bak, "p_oferta")
        has_updated_at = _column_exists(con, bak, "updated_at")
        has_p_max = _column_exists(con, bak, "p_max")

        p_max_expr = "COALESCE(p_max, 0)" if has_p_max else "0"
        p_min_expr = "COALESCE(p_min, 0)" if has_p_min else "0"
        p_oferta_expr = "COALESCE(p_oferta, 0)" if has_p_oferta else "0"
        updated_at_expr = "COALESCE(updated_at, datetime('now'))" if has_updated_at else "datetime('now')"

        if has_precio_venta:
            if has_p_min and has_p_oferta:
                precio_tipo_expr = """
                CASE
                    WHEN ABS(COALESCE(precio_venta, 0) - CAST(COALESCE(precio_venta, 0) AS INTEGER)) < 0.000001
                         AND CAST(COALESCE(precio_venta, 0) AS INTEGER) BETWEEN 1 AND 3
                        THEN CAST(COALESCE(precio_venta, 1) AS INTEGER)
                    WHEN ROUND(COALESCE(precio_venta, 0), 6) = ROUND(COALESCE(p_min, 0), 6)
                         AND COALESCE(p_min, 0) > 0
                        THEN 2
                    WHEN ROUND(COALESCE(precio_venta, 0), 6) = ROUND(COALESCE(p_oferta, 0), 6)
                         AND COALESCE(p_oferta, 0) > 0
                        THEN 3
                    ELSE 1
                END
                """
            else:
                precio_tipo_expr = """
                CASE
                    WHEN ABS(COALESCE(precio_venta, 0) - CAST(COALESCE(precio_venta, 0) AS INTEGER)) < 0.000001
                         AND CAST(COALESCE(precio_venta, 0) AS INTEGER) BETWEEN 1 AND 3
                        THEN CAST(COALESCE(precio_venta, 1) AS INTEGER)
                    ELSE 1
                END
                """
        else:
            precio_tipo_expr = "1"

        con.execute(
            """
            CREATE TABLE IF NOT EXISTS products_current (
                id TEXT PRIMARY KEY,
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
                updated_at TEXT NOT NULL
            )
            """
        )
        con.execute(
            f"""
            INSERT OR REPLACE INTO products_current(
                id, codigo, nombre, categoria, departamento, genero, ml,
                cantidad_disponible, p_max, p_min, p_oferta, precio_venta,
                fuente, updated_at
            )
            SELECT
                COALESCE(id, ''),
                COALESCE(codigo, id, ''),
                COALESCE(nombre, ''),
                COALESCE(categoria, ''),
                COALESCE(departamento, categoria, ''),
                COALESCE(genero, ''),
                COALESCE(ml, ''),
                COALESCE(cantidad_disponible, 0),
                {p_max_expr},
                {p_min_expr},
                {p_oferta_expr},
                {precio_tipo_expr},
                COALESCE(fuente, ''),
                {updated_at_expr}
            FROM {bak}
            WHERE TRIM(COALESCE(id, '')) <> ''
            """
        )
        con.execute(f"DROP TABLE IF EXISTS {bak}")

    def _rebuild_products_hist() -> None:
        if not _table_exists(con, "products_hist"):
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS products_hist (
                    import_id INTEGER NOT NULL,
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
                    PRIMARY KEY (import_id, id),
                    FOREIGN KEY (import_id) REFERENCES imports(id) ON DELETE CASCADE
                )
                """
            )
            con.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_products_hist_id
                ON products_hist(id)
                """
            )
            return

        bak = "products_hist__bak_v16"
        con.execute(f"DROP TABLE IF EXISTS {bak}")
        con.execute(f"ALTER TABLE products_hist RENAME TO {bak}")

        has_precio_venta = _column_exists(con, bak, "precio_venta")
        has_p_min = _column_exists(con, bak, "p_min")
        has_p_oferta = _column_exists(con, bak, "p_oferta")
        has_p_max = _column_exists(con, bak, "p_max")

        p_max_expr = "COALESCE(p_max, 0)" if has_p_max else "0"
        p_min_expr = "COALESCE(p_min, 0)" if has_p_min else "0"
        p_oferta_expr = "COALESCE(p_oferta, 0)" if has_p_oferta else "0"

        if has_precio_venta:
            if has_p_min and has_p_oferta:
                precio_tipo_expr = """
                CASE
                    WHEN ABS(COALESCE(precio_venta, 0) - CAST(COALESCE(precio_venta, 0) AS INTEGER)) < 0.000001
                         AND CAST(COALESCE(precio_venta, 0) AS INTEGER) BETWEEN 1 AND 3
                        THEN CAST(COALESCE(precio_venta, 1) AS INTEGER)
                    WHEN ROUND(COALESCE(precio_venta, 0), 6) = ROUND(COALESCE(p_min, 0), 6)
                         AND COALESCE(p_min, 0) > 0
                        THEN 2
                    WHEN ROUND(COALESCE(precio_venta, 0), 6) = ROUND(COALESCE(p_oferta, 0), 6)
                         AND COALESCE(p_oferta, 0) > 0
                        THEN 3
                    ELSE 1
                END
                """
            else:
                precio_tipo_expr = """
                CASE
                    WHEN ABS(COALESCE(precio_venta, 0) - CAST(COALESCE(precio_venta, 0) AS INTEGER)) < 0.000001
                         AND CAST(COALESCE(precio_venta, 0) AS INTEGER) BETWEEN 1 AND 3
                        THEN CAST(COALESCE(precio_venta, 1) AS INTEGER)
                    ELSE 1
                END
                """
        else:
            precio_tipo_expr = "1"

        con.execute(
            """
            CREATE TABLE IF NOT EXISTS products_hist (
                import_id INTEGER NOT NULL,
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
                PRIMARY KEY (import_id, id),
                FOREIGN KEY (import_id) REFERENCES imports(id) ON DELETE CASCADE
            )
            """
        )
        con.execute(
            f"""
            INSERT OR REPLACE INTO products_hist(
                import_id, id, codigo, nombre, categoria, departamento, genero, ml,
                cantidad_disponible, p_max, p_min, p_oferta, precio_venta, fuente
            )
            SELECT
                import_id,
                COALESCE(id, ''),
                COALESCE(codigo, id, ''),
                COALESCE(nombre, ''),
                COALESCE(categoria, ''),
                COALESCE(departamento, categoria, ''),
                COALESCE(genero, ''),
                COALESCE(ml, ''),
                COALESCE(cantidad_disponible, 0),
                {p_max_expr},
                {p_min_expr},
                {p_oferta_expr},
                {precio_tipo_expr},
                COALESCE(fuente, '')
            FROM {bak}
            WHERE TRIM(COALESCE(id, '')) <> ''
            """
        )
        con.execute(f"DROP TABLE IF EXISTS {bak}")
        con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_products_hist_id
            ON products_hist(id)
            """
        )

    def _rebuild_presentations_current() -> None:
        if not _table_exists(con, "presentations_current"):
            con.execute(
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
                    requiere_botella INTEGER NOT NULL DEFAULT 0,
                    stock_disponible REAL NOT NULL DEFAULT 0,
                    codigos_producto TEXT NOT NULL DEFAULT '',
                    fuente TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (codigo_norm, departamento, genero)
                )
                """
            )
            return

        bak = "presentations_current__bak_v16"
        con.execute(f"DROP TABLE IF EXISTS {bak}")
        con.execute(f"ALTER TABLE presentations_current RENAME TO {bak}")

        has_p_max = _column_exists(con, bak, "p_max")
        has_p_min = _column_exists(con, bak, "p_min")
        has_p_oferta = _column_exists(con, bak, "p_oferta")
        has_precio_present = _column_exists(con, bak, "precio_present")
        has_updated_at = _column_exists(con, bak, "updated_at")

        if has_p_max and has_precio_present:
            p_max_expr = "COALESCE(p_max, precio_present, 0)"
        elif has_p_max:
            p_max_expr = "COALESCE(p_max, 0)"
        elif has_precio_present:
            p_max_expr = "COALESCE(precio_present, 0)"
        else:
            p_max_expr = "0"

        p_min_expr = "COALESCE(p_min, 0)" if has_p_min else "0"
        p_oferta_expr = "COALESCE(p_oferta, 0)" if has_p_oferta else "0"
        updated_at_expr = "COALESCE(updated_at, datetime('now'))" if has_updated_at else "datetime('now')"

        con.execute(
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
                requiere_botella INTEGER NOT NULL DEFAULT 0,
                stock_disponible REAL NOT NULL DEFAULT 0,
                codigos_producto TEXT NOT NULL DEFAULT '',
                fuente TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (codigo_norm, departamento, genero)
            )
            """
        )
        con.execute(
            f"""
            INSERT OR REPLACE INTO presentations_current(
                codigo_norm, departamento, genero,
                codigo, nombre, descripcion,
                p_max, p_min, p_oferta,
                requiere_botella, stock_disponible, codigos_producto,
                fuente, updated_at
            )
            SELECT
                UPPER(TRIM(COALESCE(codigo_norm, codigo, ''))),
                UPPER(TRIM(COALESCE(departamento, ''))),
                LOWER(TRIM(COALESCE(genero, ''))),
                UPPER(TRIM(COALESCE(codigo, codigo_norm, ''))),
                COALESCE(nombre, ''),
                COALESCE(descripcion, ''),
                {p_max_expr},
                {p_min_expr},
                {p_oferta_expr},
                COALESCE(requiere_botella, 0),
                COALESCE(stock_disponible, 0),
                COALESCE(codigos_producto, ''),
                COALESCE(fuente, ''),
                {updated_at_expr}
            FROM {bak}
            WHERE TRIM(COALESCE(codigo_norm, codigo, '')) <> ''
            """
        )
        con.execute(f"DROP TABLE IF EXISTS {bak}")

    def _rebuild_presentations_hist() -> None:
        if not _table_exists(con, "presentations_hist"):
            con.execute(
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
                    requiere_botella INTEGER NOT NULL DEFAULT 0,
                    stock_disponible REAL NOT NULL DEFAULT 0,
                    codigos_producto TEXT NOT NULL DEFAULT '',
                    fuente TEXT,
                    PRIMARY KEY (import_id, codigo_norm, departamento, genero),
                    FOREIGN KEY (import_id) REFERENCES imports(id) ON DELETE CASCADE
                )
                """
            )
            con.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_presentations_hist_code
                ON presentations_hist(codigo_norm)
                """
            )
            return

        bak = "presentations_hist__bak_v16"
        con.execute(f"DROP TABLE IF EXISTS {bak}")
        con.execute(f"ALTER TABLE presentations_hist RENAME TO {bak}")

        has_p_max = _column_exists(con, bak, "p_max")
        has_p_min = _column_exists(con, bak, "p_min")
        has_p_oferta = _column_exists(con, bak, "p_oferta")
        has_precio_present = _column_exists(con, bak, "precio_present")

        if has_p_max and has_precio_present:
            p_max_expr = "COALESCE(p_max, precio_present, 0)"
        elif has_p_max:
            p_max_expr = "COALESCE(p_max, 0)"
        elif has_precio_present:
            p_max_expr = "COALESCE(precio_present, 0)"
        else:
            p_max_expr = "0"

        p_min_expr = "COALESCE(p_min, 0)" if has_p_min else "0"
        p_oferta_expr = "COALESCE(p_oferta, 0)" if has_p_oferta else "0"

        con.execute(
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
                requiere_botella INTEGER NOT NULL DEFAULT 0,
                stock_disponible REAL NOT NULL DEFAULT 0,
                codigos_producto TEXT NOT NULL DEFAULT '',
                fuente TEXT,
                PRIMARY KEY (import_id, codigo_norm, departamento, genero),
                FOREIGN KEY (import_id) REFERENCES imports(id) ON DELETE CASCADE
            )
            """
        )
        con.execute(
            f"""
            INSERT OR REPLACE INTO presentations_hist(
                import_id, codigo_norm, departamento, genero,
                codigo, nombre, descripcion,
                p_max, p_min, p_oferta,
                requiere_botella, stock_disponible, codigos_producto,
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
                {p_max_expr},
                {p_min_expr},
                {p_oferta_expr},
                COALESCE(requiere_botella, 0),
                COALESCE(stock_disponible, 0),
                COALESCE(codigos_producto, ''),
                COALESCE(fuente, '')
            FROM {bak}
            WHERE TRIM(COALESCE(codigo_norm, codigo, '')) <> ''
            """
        )
        con.execute(f"DROP TABLE IF EXISTS {bak}")
        con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_presentations_hist_code
            ON presentations_hist(codigo_norm)
            """
        )

    _rebuild_products_current()
    _rebuild_products_hist()
    _rebuild_presentations_current()
    _rebuild_presentations_hist()


def mig_17(con: sqlite3.Connection) -> None:
    """
    v17: normaliza precio_venta como id de tipo y fuerza reimport de productos.
    - precio_venta debe quedar en 1/2/3
    - elimina imports.kind='products' para que el proximo sync lea el valor real desde Excel
    """
    if _table_exists(con, "products_current"):
        con.execute(
            """
            UPDATE products_current
            SET precio_venta = CASE
                WHEN CAST(COALESCE(precio_venta, 0) AS INTEGER) BETWEEN 1 AND 3
                    THEN CAST(COALESCE(precio_venta, 1) AS INTEGER)
                ELSE 1
            END
            """
        )

    if _table_exists(con, "products_hist"):
        con.execute(
            """
            UPDATE products_hist
            SET precio_venta = CASE
                WHEN CAST(COALESCE(precio_venta, 0) AS INTEGER) BETWEEN 1 AND 3
                    THEN CAST(COALESCE(precio_venta, 1) AS INTEGER)
                ELSE 1
            END
            """
        )

    if _table_exists(con, "imports"):
        con.execute(
            """
            DELETE FROM imports
            WHERE kind = 'products'
            """
        )


def mig_18(con: sqlite3.Connection) -> None:
    """
    v18: tipo_documento en quotes.
    - Agrega columna quotes.tipo_documento (si falta)
    - Backfill best effort desde prefijo de quotes.cedula (ej: DNI-..., RUC-...)
    """
    _add_column_if_missing(con, "quotes", "tipo_documento", "TEXT NOT NULL DEFAULT ''")

    if _table_exists(con, "quotes"):
        con.execute(
            """
            UPDATE quotes
            SET tipo_documento = UPPER(TRIM(COALESCE(tipo_documento, '')))
            """
        )
        con.execute(
            """
            UPDATE quotes
            SET tipo_documento = UPPER(TRIM(substr(cedula, 1, instr(cedula, '-') - 1)))
            WHERE COALESCE(TRIM(tipo_documento), '') = ''
              AND instr(COALESCE(cedula, ''), '-') > 0
            """
        )
        con.execute(
            """
            UPDATE quotes
            SET tipo_documento = ''
            WHERE COALESCE(TRIM(tipo_documento), '') <> ''
              AND UPPER(TRIM(tipo_documento)) NOT IN ('CE', 'RIF', 'DNI', 'RUC', 'CI', 'J', 'P', 'E', 'G', 'V')
            """
        )


def mig_19(con: sqlite3.Connection) -> None:
    """
    v19: refuerza el backfill de quotes.tipo_documento con inferencia por pais+cedula.
    """
    if not _table_exists(con, "quotes"):
        return

    from .quotes_repo import infer_tipo_documento_from_doc

    rows = con.execute(
        """
        SELECT id, country_code, cedula, tipo_documento
        FROM quotes
        """
    ).fetchall()
    updates: list[tuple[str, int]] = []

    for r in rows:
        try:
            quote_id = int(r["id"])
            country_code = str(r["country_code"] or "")
            cedula = str(r["cedula"] or "")
            current_tipo = str(r["tipo_documento"] or "")
        except Exception:
            quote_id = int(r[0])
            country_code = str(r[1] or "")
            cedula = str(r[2] or "")
            current_tipo = str(r[3] or "")

        inferred_tipo = infer_tipo_documento_from_doc(
            country_code,
            cedula,
            explicit_tipo=current_tipo,
        )
        current_norm = str(current_tipo or "").strip().upper()
        if inferred_tipo != current_norm:
            updates.append((inferred_tipo, quote_id))

    if updates:
        con.executemany(
            "UPDATE quotes SET tipo_documento = ? WHERE id = ?",
            updates,
        )


def mig_20(con: sqlite3.Connection) -> None:
    """
    v20: sincroniza tipo_documento segun regex por pais (VE/PE/PY).
    """
    if not _table_exists(con, "quotes"):
        return

    from .quotes_repo import infer_tipo_documento_from_doc

    rows = con.execute(
        """
        SELECT id, country_code, cedula, tipo_documento
        FROM quotes
        """
    ).fetchall()
    updates: list[tuple[str, int]] = []

    for r in rows:
        try:
            quote_id = int(r["id"])
            country_code = str(r["country_code"] or "")
            cedula = str(r["cedula"] or "")
            current_tipo = str(r["tipo_documento"] or "")
        except Exception:
            quote_id = int(r[0])
            country_code = str(r[1] or "")
            cedula = str(r[2] or "")
            current_tipo = str(r[3] or "")

        inferred_tipo = infer_tipo_documento_from_doc(
            country_code,
            cedula,
            explicit_tipo=current_tipo,
        )
        current_norm = str(current_tipo or "").strip().upper()
        if inferred_tipo != current_norm:
            updates.append((inferred_tipo, quote_id))

    if updates:
        con.executemany(
            "UPDATE quotes SET tipo_documento = ? WHERE id = ?",
            updates,
        )


def mig_21(con: sqlite3.Connection) -> None:
    """
    v21: revalida tipo_documento existente contra regex por pais.
    """
    if not _table_exists(con, "quotes"):
        return

    from .quotes_repo import infer_tipo_documento_from_doc

    rows = con.execute(
        """
        SELECT id, country_code, cedula, tipo_documento
        FROM quotes
        """
    ).fetchall()
    updates: list[tuple[str, int]] = []

    for r in rows:
        try:
            quote_id = int(r["id"])
            country_code = str(r["country_code"] or "")
            cedula = str(r["cedula"] or "")
            current_tipo = str(r["tipo_documento"] or "")
        except Exception:
            quote_id = int(r[0])
            country_code = str(r[1] or "")
            cedula = str(r[2] or "")
            current_tipo = str(r[3] or "")

        inferred_tipo = infer_tipo_documento_from_doc(
            country_code,
            cedula,
            explicit_tipo=current_tipo,
        )
        current_norm = str(current_tipo or "").strip().upper()
        if inferred_tipo != current_norm:
            updates.append((inferred_tipo, quote_id))

    if updates:
        con.executemany(
            "UPDATE quotes SET tipo_documento = ? WHERE id = ?",
            updates,
        )


def mig_22(con: sqlite3.Connection) -> None:
    """
    v22: normaliza datos de cliente y migra a tabla clients.
    - Crea tabla clients + indices (si faltan)
    - Normaliza quotes(cliente/cedula/tipo_documento/telefono)
    - Backfill de clientes activos (deleted_at IS NULL) deduplicando por tipo+numero
    """
    from .clients_repo import ensure_clients_table, rebuild_clients_from_quotes

    _normalize_quote_doc_types_for_country_policy(con)
    ensure_clients_table(con)
    rebuild_clients_from_quotes(con)
    _normalize_clients_documents_for_country_policy(con)


def mig_23(con: sqlite3.Connection) -> None:
    """
    v23: re-dedupe de clientes desde quotes.
    - Unifica por cliente (nombre+telefono)
    - Asigna documento sintetico secuencial cuando el doc no cumple formato
      o colisiona con otro cliente
    """
    from .clients_repo import ensure_clients_table, rebuild_clients_from_quotes

    # Debe correr antes de dedupe para que la reconstruccion de clients
    # ya salga con los tipos restringidos por pais.
    _normalize_quote_doc_types_for_country_policy(con)
    ensure_clients_table(con)
    rebuild_clients_from_quotes(con)
    _normalize_clients_documents_for_country_policy(con)


def mig_24(con: sqlite3.Connection) -> None:
    """
    v24: quotes guarda cliente solo por referencia (id_cliente).
    - Agrega quotes.id_cliente si falta
    - Backfill de id_cliente desde columnas legacy (cliente/cedula/tipo_documento/telefono)
    - Elimina columnas legacy de cliente en quotes
    """
    from .clients_repo import ensure_clients_table, upsert_client

    ensure_clients_table(con)
    if not _table_exists(con, "quotes"):
        return

    _add_column_if_missing(con, "quotes", "id_cliente", "INTEGER")

    has_cliente = _column_exists(con, "quotes", "cliente")
    has_cedula = _column_exists(con, "quotes", "cedula")
    has_tipo_doc = _column_exists(con, "quotes", "tipo_documento")
    has_telefono = _column_exists(con, "quotes", "telefono")
    has_id_cliente = _column_exists(con, "quotes", "id_cliente")

    # Backfill de referencia de cliente usando columnas legacy.
    if has_cliente and has_cedula and has_telefono:
        tipo_sel = "COALESCE(tipo_documento, '') AS tipo_documento" if has_tipo_doc else "'' AS tipo_documento"
        id_cliente_sel = "COALESCE(id_cliente, 0) AS id_cliente" if has_id_cliente else "0 AS id_cliente"
        rows = con.execute(
            f"""
            SELECT
                id,
                COALESCE(country_code, '') AS country_code,
                COALESCE(created_at, '') AS created_at,
                COALESCE(cliente, '') AS cliente,
                COALESCE(cedula, '') AS cedula,
                {tipo_sel},
                COALESCE(telefono, '') AS telefono,
                {id_cliente_sel}
            FROM quotes
            ORDER BY created_at ASC, id ASC
            """
        ).fetchall()

        updates: list[tuple[int, int]] = []
        for row in rows:
            quote_id = int(row["id"] or 0)
            if int(row["id_cliente"] or 0) > 0:
                continue

            country_code = str(row["country_code"] or "")
            created_at = str(row["created_at"] or "")
            cliente = str(row["cliente"] or "").strip()
            cedula = str(row["cedula"] or "").strip()
            tipo_documento = str(row["tipo_documento"] or "").strip()
            telefono = str(row["telefono"] or "").strip()

            if not (cliente or cedula or telefono):
                continue

            client_id = upsert_client(
                con,
                country_code=country_code,
                tipo_documento=tipo_documento,
                documento=cedula,
                nombre=cliente,
                telefono=telefono,
                source_quote_id=quote_id,
                source_created_at=created_at,
                require_valid_document=False,
            )
            if client_id is not None and int(client_id) > 0:
                updates.append((int(client_id), quote_id))

        if updates:
            con.executemany(
                "UPDATE quotes SET id_cliente = ? WHERE id = ?",
                updates,
            )

    # Importante: no reconstruir tabla completa de quotes dentro de migraciones
    # transaccionales para evitar efectos colaterales por FK CASCADE en quote_items.
    # Solo se agrega la referencia y se eliminan columnas legacy por DROP COLUMN.
    con.execute("CREATE INDEX IF NOT EXISTS idx_quotes_id_cliente ON quotes(id_cliente)")
    _drop_quotes_legacy_client_columns(con)


def _drop_quotes_legacy_client_columns(con: sqlite3.Connection) -> None:
    if not _table_exists(con, "quotes"):
        return

    # ALTER TABLE ... DROP COLUMN requiere SQLite >= 3.35.
    if tuple(sqlite3.sqlite_version_info) < (3, 35, 0):
        return

    legacy_cols = ("cliente", "cedula", "tipo_documento", "telefono")
    for col in legacy_cols:
        if not _column_exists(con, "quotes", col):
            continue
        con.execute(f"ALTER TABLE quotes DROP COLUMN {col}")


def _country_code_norm(value: str | None) -> str:
    v = str(value or "").strip().upper()
    if v in ("PE", "PERU"):
        return "PE"
    if v in ("PY", "PARAGUAY"):
        return "PY"
    if v in ("VE", "VENEZUELA"):
        return "VE"
    return v


def _normalize_doc_store(value: str | None) -> str:
    raw = str(value or "").strip().upper()
    if not raw:
        return ""
    m = re.match(r"^([A-Z]+)-(.+)$", raw)
    if m:
        raw = str(m.group(2) or "").strip().upper()
    raw = re.sub(r"\s+", "", raw)
    return re.sub(r"[^0-9A-Z]", "", raw)


def _target_doc_type_for_country(*, country_code: str, current_type: str, documento_norm: str) -> str:
    cc = _country_code_norm(country_code)
    td = str(current_type or "").strip().upper()
    doc_u = str(documento_norm or "").strip().upper()

    if cc == "PE":
        if td == "RUC":
            return "RUC"
        if td in ("DNI", "CI"):
            return "DNI"
        if doc_u.isdigit() and len(doc_u) == 11:
            return "RUC"
        return "DNI"

    if cc == "PY":
        if td == "RUC":
            return "RUC"
        return "CI"

    if cc == "VE":
        if td in ("RIF", "J"):
            return "RIF"
        if doc_u.isdigit() and len(doc_u) == 9:
            return "RIF"
        return "V"

    return td


def _normalize_quote_doc_types_for_country_policy(con: sqlite3.Connection) -> None:
    if not _table_exists(con, "quotes"):
        return
    if not _column_exists(con, "quotes", "country_code"):
        return
    if not _column_exists(con, "quotes", "cedula"):
        return
    if not _column_exists(con, "quotes", "tipo_documento"):
        return

    rows = con.execute(
        """
        SELECT
            id,
            COALESCE(country_code, '') AS country_code,
            COALESCE(cedula, '') AS cedula,
            COALESCE(tipo_documento, '') AS tipo_documento
        FROM quotes
        ORDER BY id ASC
        """
    ).fetchall()
    if not rows:
        return

    updates: list[tuple[str, int]] = []
    for row in rows:
        qid = int(row["id"])
        cc = _country_code_norm(row["country_code"])
        if cc not in ("PE", "PY", "VE"):
            continue
        current_type = str(row["tipo_documento"] or "").strip().upper()
        doc_norm = _normalize_doc_store(row["cedula"])
        target_type = _target_doc_type_for_country(
            country_code=cc,
            current_type=current_type,
            documento_norm=doc_norm,
        )
        if target_type and target_type != current_type:
            updates.append((target_type, qid))

    if updates:
        con.executemany(
            "UPDATE quotes SET tipo_documento = ? WHERE id = ?",
            updates,
        )


def _normalize_clients_documents_for_country_policy(con: sqlite3.Connection) -> None:
    if not _table_exists(con, "clients"):
        return

    required_cols = ("id", "country_code", "tipo_documento", "documento", "documento_norm")
    for col in required_cols:
        if not _column_exists(con, "clients", col):
            return

    from .quotes_repo import document_type_rule, validate_document_for_type

    def _default_type(cc: str) -> str:
        if cc == "PE":
            return "DNI"
        if cc == "PY":
            return "CI"
        if cc == "VE":
            return "V"
        return "DNI"

    def _pad_for(cc: str, td: str) -> int:
        rule = document_type_rule(cc, td)
        if not rule:
            return 0
        try:
            return int(rule.get("validation_pad") or 0)
        except Exception:
            return 0

    seq_state: dict[tuple[str, str], int] = {}

    def _next_synthetic(cc: str, td: str, used: set[tuple[str, str]]) -> tuple[str, str, str]:
        target_td = str(td or "").strip().upper() or _default_type(cc)
        pad = _pad_for(cc, target_td)
        key = (cc, target_td)
        seq = int(seq_state.get(key, 1) or 1)
        max_iter = 1_000_000
        cur_iter = 0
        while cur_iter < max_iter:
            cur_iter += 1
            doc_candidate = str(seq).zfill(pad) if pad > 0 else str(seq)
            seq += 1
            doc_norm_candidate = _normalize_doc_store(doc_candidate)
            if not doc_norm_candidate:
                continue
            doc_key = (target_td, doc_norm_candidate)
            if doc_key in used:
                continue
            ok, _msg = validate_document_for_type(cc, target_td, doc_candidate)
            if not ok:
                continue
            used.add(doc_key)
            seq_state[key] = seq
            return target_td, doc_candidate, doc_norm_candidate
        raise RuntimeError("No se pudo asignar documento sintetico valido y unico.")

    rows = con.execute(
        """
        SELECT
            id,
            COALESCE(country_code, '') AS country_code,
            COALESCE(tipo_documento, '') AS tipo_documento,
            COALESCE(documento, '') AS documento,
            COALESCE(documento_norm, '') AS documento_norm
        FROM clients
        ORDER BY id ASC
        """
    ).fetchall()
    if not rows:
        return

    used_doc_keys: set[tuple[str, str]] = set()
    old_keys_by_id: dict[int, tuple[str, str] | None] = {}
    for row in rows:
        rid = int(row["id"])
        old_tipo = str(row["tipo_documento"] or "").strip().upper()
        old_norm = _normalize_doc_store(row["documento_norm"] or row["documento"])
        if old_tipo and old_norm:
            old_key = (old_tipo, old_norm)
            used_doc_keys.add(old_key)
            old_keys_by_id[rid] = old_key
        else:
            old_keys_by_id[rid] = None

    for row in rows:
        rid = int(row["id"])
        cc = _country_code_norm(row["country_code"])
        if cc not in ("PE", "PY", "VE"):
            continue

        old_tipo = str(row["tipo_documento"] or "").strip().upper()
        old_doc = _normalize_doc_store(row["documento"])
        old_norm = _normalize_doc_store(row["documento_norm"] or old_doc)

        old_key = old_keys_by_id.get(rid)
        if old_key and old_key in used_doc_keys:
            used_doc_keys.remove(old_key)

        target_tipo = _target_doc_type_for_country(
            country_code=cc,
            current_type=old_tipo,
            documento_norm=(old_norm or old_doc),
        ) or _default_type(cc)

        target_doc = old_doc
        pad = _pad_for(cc, target_tipo)
        if target_doc.isdigit() and pad > 0 and len(target_doc) < pad:
            target_doc = target_doc.zfill(pad)

        target_norm = _normalize_doc_store(target_doc)
        target_key = (target_tipo, target_norm) if target_tipo and target_norm else ("", "")
        ok_doc, _msg = validate_document_for_type(cc, target_tipo, target_doc)

        if (not ok_doc) or (not target_norm) or (target_key in used_doc_keys):
            target_tipo, target_doc, target_norm = _next_synthetic(cc, target_tipo, used_doc_keys)
        else:
            used_doc_keys.add(target_key)

        if (
            cc == _country_code_norm(row["country_code"])
            and target_tipo == old_tipo
            and target_doc == old_doc
            and target_norm == old_norm
        ):
            continue

        con.execute(
            """
            UPDATE clients
            SET
                country_code = ?,
                tipo_documento = ?,
                documento = ?,
                documento_norm = ?,
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (cc, target_tipo, target_doc, target_norm, rid),
        )


def mig_25(con: sqlite3.Connection) -> None:
    """
    v25: normaliza clients para cumplir politica documental por pais.
    - PE: DNI/RUC
    - PY: CI/RUC
    - VE: V/RIF
    - Corrige documentos invalidos (pad/sintetico) y asegura unicidad tipo+documento_norm
    """
    _normalize_clients_documents_for_country_policy(con)


def mig_26(con: sqlite3.Connection) -> None:
    """
    v26: soft delete para clientes.
    - Agrega clients.deleted_at (si falta)
    - Crea index por deleted_at para filtros de activos/eliminados
    """
    if not _table_exists(con, "clients"):
        return

    _add_column_if_missing(con, "clients", "deleted_at", "TEXT")
    con.execute("CREATE INDEX IF NOT EXISTS idx_clients_deleted ON clients(deleted_at)")


def mig_27(con: sqlite3.Connection) -> None:
    """
    v27: elimina columnas legacy de cliente en quotes.
    - quotes: drop cliente/cedula/tipo_documento/telefono
    - Mantiene solo referencia por quotes.id_cliente
    """
    _drop_quotes_legacy_client_columns(con)


def mig_28(con: sqlite3.Connection) -> None:
    """
    v28: estados de cotizacion configurables en DB.
    - Crea quote_statuses
    - Inserta estados por defecto (solo si faltan)
    - Backfill desde quotes.estado (no perder historico)
    - Migra colores legacy desde settings para defaults
    """
    from .quote_statuses_repo import (
        ensure_quote_statuses_ready,
        set_default_status_colors_from_legacy_settings,
    )

    ensure_quote_statuses_ready(con)
    set_default_status_colors_from_legacy_settings(con)


def mig_29(con: sqlite3.Connection) -> None:
    """
    v29: control de errores/reintentos API por cotizacion.
    - Agrega quotes.api_error_at
    - Agrega quotes.api_error_message
    - Crea index por api_error_at para barridos de pendientes
    """
    if not _table_exists(con, "quotes"):
        return
    _add_column_if_missing(con, "quotes", "api_error_at", "TEXT")
    _add_column_if_missing(con, "quotes", "api_error_message", "TEXT")
    con.execute("CREATE INDEX IF NOT EXISTS idx_quotes_api_error_at ON quotes(api_error_at)")


def mig_30(con: sqlite3.Connection) -> None:
    """
    v30: settings.value acepta NULL y agrega settings.tienda triestado.
    - Rebuild de settings para permitir value NULL.
    - Garantiza settings.tienda = NULL si no existe.
    """
    if not _table_exists(con, "settings"):
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
    else:
        rows = con.execute("PRAGMA table_info(settings)").fetchall()
        value_not_null = True
        for row in rows:
            if str(row["name"]).lower() == "value":
                value_not_null = bool(row["notnull"])
                break

        if value_not_null:
            bak = "settings__bak_v30"
            con.execute(f"DROP TABLE IF EXISTS {bak}")
            con.execute(f"ALTER TABLE settings RENAME TO {bak}")
            con.execute(
                """
                CREATE TABLE settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
                """
            )
            con.execute(
                f"""
                INSERT INTO settings(key, value)
                SELECT key, value
                FROM {bak}
                """
            )
            con.execute(f"DROP TABLE IF EXISTS {bak}")

    if _table_exists(con, "settings"):
        con.execute(
            """
            INSERT OR IGNORE INTO settings(key, value) VALUES('tienda', NULL)
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
    11: mig_11,
    12: mig_12,
    13: mig_13,
    14: mig_14,
    15: mig_15,
    16: mig_16,
    17: mig_17,
    18: mig_18,
    19: mig_19,
    20: mig_20,
    21: mig_21,
    22: mig_22,
    23: mig_23,
    24: mig_24,
    25: mig_25,
    26: mig_26,
    27: mig_27,
    28: mig_28,
    29: mig_29,
    30: mig_30,
}
