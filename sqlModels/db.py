from __future__ import annotations

import sqlite3
from contextlib import contextmanager

from .schema import DDL, SCHEMA_VERSION
from .migrations import MIGRATIONS


def connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row

    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA synchronous = NORMAL")
    con.execute("PRAGMA busy_timeout = 5000")
    return con


@contextmanager
def tx(con: sqlite3.Connection):
    try:
        con.execute("BEGIN")
        yield
        con.commit()
    except Exception:
        con.rollback()
        raise


def _get_meta(con: sqlite3.Connection, key: str) -> str | None:
    try:
        r = con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return str(r["value"]) if r and r["value"] is not None else None
    except Exception:
        return None


def _set_meta(con: sqlite3.Connection, key: str, value: str) -> None:
    con.execute(
        """
        INSERT INTO meta(key, value) VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        (key, str(value)),
    )


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    r = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return r is not None


def _column_exists(con: sqlite3.Connection, table: str, col: str) -> bool:
    if not _table_exists(con, table):
        return False
    try:
        rows = con.execute(f"PRAGMA table_info({table})").fetchall()
        cols = {str(r["name"]).lower() for r in rows}
        return col.lower() in cols
    except Exception:
        return False


def _looks_like_head_schema_without_meta(con: sqlite3.Connection) -> bool:
    """
    Detecta una DB nueva creada con el DDL actual (sin meta.schema_version).
    En ese caso evitamos ejecutar migraciones antiguas.
    """
    required = [
        ("quotes", "api_sent_at"),
        ("quotes", "api_error_at"),
        ("quotes", "api_error_message"),
        ("quotes", "id_cliente"),
        ("quote_items", "id_precioventa"),
        ("quote_items", "tipo_prod"),
        ("clients", "documento_norm"),
        ("clients", "deleted_at"),
        ("products_current", "p_max"),
        ("products_current", "p_min"),
        ("products_current", "p_oferta"),
        ("products_current", "precio_venta"),
        ("presentations_current", "p_max"),
        ("presentations_current", "p_min"),
        ("presentations_current", "p_oferta"),
    ]
    for table, col in required:
        if not _column_exists(con, table, col):
            return False

    forbidden = [
        ("quotes", "cliente"),
        ("quotes", "cedula"),
        ("quotes", "tipo_documento"),
        ("quotes", "telefono"),
        ("products_current", "precio_unitario"),
        ("products_current", "precio_unidad"),
        ("products_current", "precio_base_50g"),
        ("products_current", "precio_oferta_base"),
        ("products_current", "precio_minimo_base"),
        ("presentations_current", "precio_present"),
    ]
    for table, col in forbidden:
        if _column_exists(con, table, col):
            return False
    return True


def _infer_schema_version(con: sqlite3.Connection) -> int:
    """
    Si la DB viene de versiones MUY viejas (sin meta o sin schema_version),
    inferimos una versión aproximada mirando tablas.
    """
    any_user_table = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' LIMIT 1"
    ).fetchone()
    if not any_user_table:
        return 0

    if (
        _table_exists(con, "imports")
        or _table_exists(con, "exchange_rates")
        or _table_exists(con, "settings")
        or _table_exists(con, "sequences")
    ):
        return 2

    if (
        _table_exists(con, "quotes")
        or _table_exists(con, "products_current")
        or _table_exists(con, "presentations_current")
    ):
        return 1

    return 0


def _is_create_index_stmt(stmt: str) -> bool:
    s = (stmt or "").lstrip().lower()
    return s.startswith("create index") or s.startswith("create unique index")


def ensure_schema(con: sqlite3.Connection) -> None:
    """
    - Aplica DDL idempotente
    - Ejecuta migraciones incrementales (si hacen falta)
    - Reintenta índices que dependan de columnas nuevas (ej: estado)
    - Deja meta.schema_version = SCHEMA_VERSION
    """
    with tx(con):
        deferred_indexes: list[str] = []

        # 1) DDL base (idempotente)
        for stmt in DDL:
            try:
                con.execute(stmt)
            except sqlite3.OperationalError as e:
                msg = str(e).lower()

                # ✅ Si falla un INDEX por columna/tabla inexistente (DB vieja),
                # lo diferimos hasta después de migrar.
                if _is_create_index_stmt(stmt) and (
                    "no such column" in msg or "no such table" in msg
                ):
                    deferred_indexes.append(stmt)
                    continue

                raise

        # 2) leer versión actual
        meta_dirty = False
        cur_v_s = _get_meta(con, "schema_version")
        if cur_v_s is None:
            if _looks_like_head_schema_without_meta(con):
                cur_v = SCHEMA_VERSION
            else:
                cur_v = _infer_schema_version(con)
            meta_dirty = True
        else:
            try:
                cur_v = int(cur_v_s)
            except Exception:
                cur_v = _infer_schema_version(con)
                meta_dirty = True

        # 3) migrar incremental
        migrated = False
        if cur_v < SCHEMA_VERSION:
            for target_v in range(cur_v + 1, SCHEMA_VERSION + 1):
                mig = MIGRATIONS.get(target_v)
                if mig:
                    mig(con)  # debe ser segura/condicional
            migrated = True
            meta_dirty = True

        # 4) Reintentar índices diferidos (ahora sí existen columnas)
        for stmt in deferred_indexes:
            con.execute(stmt)

        # 4.5) Post-setup solo cuando migra o falta meta.
        if migrated or meta_dirty:
            try:
                from .clients_repo import ensure_generic_clients

                ensure_generic_clients(con)
            except Exception:
                pass

            try:
                from .quote_statuses_repo import ensure_quote_statuses_ready

                ensure_quote_statuses_ready(con)
            except Exception:
                pass

        # 5) fijar versión final (evita escrituras en cada ensure_schema)
        if meta_dirty or (str(cur_v_s or "").strip() != str(SCHEMA_VERSION)):
            _set_meta(con, "schema_version", str(SCHEMA_VERSION))
