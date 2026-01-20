# sqlModels/schema.py
from __future__ import annotations

SCHEMA_VERSION = 3

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
        value TEXT NOT NULL
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
    # Imports (histórico de imports de Excel)
    # =========================
    """
    CREATE TABLE IF NOT EXISTS imports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,                 -- 'products' | 'presentations'
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
    # Products (current + hist)
    # =========================
    """
    CREATE TABLE IF NOT EXISTS products_current (
        id TEXT PRIMARY KEY,
        nombre TEXT,
        categoria TEXT,
        genero TEXT,
        ml TEXT,

        cantidad_disponible REAL NOT NULL DEFAULT 0,

        precio_unitario REAL NOT NULL DEFAULT 0,
        precio_unidad REAL NOT NULL DEFAULT 0,
        precio_base_50g REAL NOT NULL DEFAULT 0,

        precio_oferta_base REAL NOT NULL DEFAULT 0,
        precio_minimo_base REAL NOT NULL DEFAULT 0,
        precio_venta REAL NOT NULL DEFAULT 0,

        fuente TEXT,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS products_hist (
        import_id INTEGER NOT NULL,
        id TEXT NOT NULL,

        nombre TEXT,
        categoria TEXT,
        genero TEXT,
        ml TEXT,

        cantidad_disponible REAL NOT NULL DEFAULT 0,

        precio_unitario REAL NOT NULL DEFAULT 0,
        precio_unidad REAL NOT NULL DEFAULT 0,
        precio_base_50g REAL NOT NULL DEFAULT 0,

        precio_oferta_base REAL NOT NULL DEFAULT 0,
        precio_minimo_base REAL NOT NULL DEFAULT 0,
        precio_venta REAL NOT NULL DEFAULT 0,

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
    # Presentations (current + hist)
    # =========================
    """
    CREATE TABLE IF NOT EXISTS presentations_current (
        codigo_norm TEXT PRIMARY KEY,
        codigo TEXT,
        nombre TEXT,
        departamento TEXT,
        genero TEXT,
        precio_present REAL NOT NULL DEFAULT 0,
        requiere_botella INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS presentations_hist (
        import_id INTEGER NOT NULL,
        codigo_norm TEXT NOT NULL,

        codigo TEXT,
        nombre TEXT,
        departamento TEXT,
        genero TEXT,
        precio_present REAL NOT NULL DEFAULT 0,
        requiere_botella INTEGER NOT NULL DEFAULT 0,

        PRIMARY KEY (import_id, codigo_norm),
        FOREIGN KEY (import_id) REFERENCES imports(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_presentations_hist_code
    ON presentations_hist(codigo_norm)
    """,

    # =========================
    # Quotes (histórico)
    # =========================
    """
    CREATE TABLE IF NOT EXISTS quotes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        country_code TEXT NOT NULL,
        quote_no TEXT NOT NULL,              -- "0000001"
        created_at TEXT NOT NULL,

        cliente TEXT NOT NULL,
        cedula TEXT NOT NULL,
        telefono TEXT NOT NULL,

        currency_shown TEXT NOT NULL,
        tasa_shown REAL,

        subtotal_bruto_base REAL NOT NULL DEFAULT 0,
        descuento_total_base REAL NOT NULL DEFAULT 0,
        total_neto_base REAL NOT NULL DEFAULT 0,

        subtotal_bruto_shown REAL NOT NULL DEFAULT 0,
        descuento_total_shown REAL NOT NULL DEFAULT 0,
        total_neto_shown REAL NOT NULL DEFAULT 0,

        pdf_path TEXT NOT NULL,

        deleted_at TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_quotes_created ON quotes(created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_quotes_deleted ON quotes(deleted_at)",

    """
    CREATE TABLE IF NOT EXISTS quote_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        quote_id INTEGER NOT NULL,

        codigo TEXT,
        producto TEXT,
        categoria TEXT,
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
