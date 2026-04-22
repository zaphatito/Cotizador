# sqlModels/schema.py
from __future__ import annotations

SCHEMA_VERSION = 32

DDL = [
    # =========================
    # Meta
    # =========================
    """
    CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,

    # =========================
    # Settings (config en DB)
    # =========================
    """
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """,

    # =========================
    # Sequences (correlativos)
    # =========================
    """
    CREATE TABLE IF NOT EXISTS sequences (
        name TEXT PRIMARY KEY,
        value INTEGER NOT NULL DEFAULT 0
    )
    """,

    # =========================
    # Exchange rates (tasa)
    # =========================
    """
    CREATE TABLE IF NOT EXISTS exchange_rates (
        base_currency TEXT NOT NULL,
        currency TEXT NOT NULL,
        rate REAL NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (base_currency, currency)
    )
    """,

    # =========================
    # Imports (historico de imports de Excel)
    # =========================
    """
    CREATE TABLE IF NOT EXISTS imports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,
        source_file TEXT NOT NULL,
        source_mtime REAL NOT NULL,
        source_size INTEGER NOT NULL,
        source_hash TEXT NOT NULL,
        imported_at TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_imports_kind_time
    ON imports(kind, imported_at)
    """,

    # =========================
    # Products (compat current + hist)
    # =========================
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
    """,
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
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_products_hist_id
    ON products_hist(id)
    """,

    # =========================
    # Presentations (compat current + hist)
    # =========================
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
    """,
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
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_presentations_hist_code
    ON presentations_hist(codigo_norm)
    """,

    # =========================
    # Presentation-product links (hoja 3)
    # =========================
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
    """,
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
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_presentacion_prod_current_presentacion
    ON presentacion_prod_current(cod_presentacion)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_presentacion_prod_current_producto
    ON presentacion_prod_current(cod_producto)
    """,

    # =========================
    # Clients (maestro de clientes)
    # =========================
    """
    CREATE TABLE IF NOT EXISTS clients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        country_code TEXT NOT NULL DEFAULT '',
        tipo_documento TEXT NOT NULL DEFAULT '',
        documento TEXT NOT NULL DEFAULT '',
        documento_norm TEXT NOT NULL DEFAULT '',
        nombre TEXT NOT NULL DEFAULT '',
        telefono TEXT NOT NULL DEFAULT '',
        direccion TEXT NOT NULL DEFAULT '-',
        email TEXT NOT NULL DEFAULT '-',
        source_quote_id INTEGER,
        source_created_at TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
        deleted_at TEXT
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_clients_tipo_doc_norm
    ON clients(tipo_documento, documento_norm)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_clients_nombre
    ON clients(nombre)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_clients_country
    ON clients(country_code)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_clients_deleted
    ON clients(deleted_at)
    """,

    # =========================
    # Quote statuses (catalogo de estados)
    # =========================
    """
    CREATE TABLE IF NOT EXISTS quote_statuses (
        code TEXT PRIMARY KEY,
        label TEXT NOT NULL DEFAULT '',
        color_hex TEXT NOT NULL DEFAULT '',
        sort_order INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_quote_statuses_sort
    ON quote_statuses(sort_order, code)
    """,

    # =========================
    # Quotes (historico)
    # =========================
    """
    CREATE TABLE IF NOT EXISTS quotes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        country_code TEXT NOT NULL,
        quote_no TEXT NOT NULL,
        created_at TEXT NOT NULL,
        id_cliente INTEGER,

        metodo_pago TEXT NOT NULL DEFAULT '',
        estado TEXT NOT NULL DEFAULT '',

        currency_shown TEXT NOT NULL,
        tasa_shown REAL,

        subtotal_bruto_base REAL NOT NULL DEFAULT 0,
        descuento_total_base REAL NOT NULL DEFAULT 0,
        total_neto_base REAL NOT NULL DEFAULT 0,

        subtotal_bruto_shown REAL NOT NULL DEFAULT 0,
        descuento_total_shown REAL NOT NULL DEFAULT 0,
        total_neto_shown REAL NOT NULL DEFAULT 0,

        pdf_path TEXT NOT NULL,
        api_sent_at TEXT,
        api_error_at TEXT,
        api_error_message TEXT,

        deleted_at TEXT,

        FOREIGN KEY (id_cliente) REFERENCES clients(id) ON DELETE SET NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_quotes_created ON quotes(created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_quotes_deleted ON quotes(deleted_at)",
    "CREATE INDEX IF NOT EXISTS idx_quotes_estado ON quotes(estado)",
    "CREATE INDEX IF NOT EXISTS idx_quotes_id_cliente ON quotes(id_cliente)",
    "CREATE INDEX IF NOT EXISTS idx_quotes_api_sent_at ON quotes(api_sent_at)",
    "CREATE INDEX IF NOT EXISTS idx_quotes_api_error_at ON quotes(api_error_at)",

    """
    CREATE TABLE IF NOT EXISTS quote_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        quote_id INTEGER NOT NULL,

        codigo TEXT,
        producto TEXT,
        categoria TEXT,
        tipo_prod TEXT NOT NULL DEFAULT 'prod',
        fragancia TEXT,
        observacion TEXT,

        cantidad REAL NOT NULL DEFAULT 0,

        -- Base
        precio_base REAL NOT NULL DEFAULT 0,
        subtotal_base REAL NOT NULL DEFAULT 0,
        descuento_mode TEXT,
        descuento_pct REAL NOT NULL DEFAULT 0,
        descuento_monto_base REAL NOT NULL DEFAULT 0,
        total_base REAL NOT NULL DEFAULT 0,
        precio_override_base REAL,
        precio_tier TEXT,
        id_precioventa INTEGER NOT NULL DEFAULT 1,

        -- Shown
        precio_shown REAL NOT NULL DEFAULT 0,
        subtotal_shown REAL NOT NULL DEFAULT 0,
        descuento_monto_shown REAL NOT NULL DEFAULT 0,
        total_shown REAL NOT NULL DEFAULT 0,

        FOREIGN KEY (quote_id) REFERENCES quotes(id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_quote_items_quote ON quote_items(quote_id)",
    "CREATE INDEX IF NOT EXISTS idx_quote_items_codigo ON quote_items(codigo)",
]
