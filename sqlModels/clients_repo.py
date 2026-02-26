from __future__ import annotations

import re
import sqlite3
from typing import Any

from .quotes_repo import (
    document_type_rule,
    document_type_rules_for_country,
    infer_tipo_documento_from_doc,
    validate_document_for_type,
)


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return row is not None


def _has_column(con: sqlite3.Connection, table: str, col: str) -> bool:
    try:
        rows = con.execute(f"PRAGMA table_info({table})").fetchall()
        cols = {str(r["name"]).lower() for r in rows}
        return str(col or "").lower() in cols
    except Exception:
        return False


def ensure_clients_table(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            country_code TEXT NOT NULL DEFAULT '',
            tipo_documento TEXT NOT NULL DEFAULT '',
            documento TEXT NOT NULL DEFAULT '',
            documento_norm TEXT NOT NULL DEFAULT '',
            nombre TEXT NOT NULL DEFAULT '',
            telefono TEXT NOT NULL DEFAULT '',
            source_quote_id INTEGER,
            source_created_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            deleted_at TEXT
        )
        """
    )
    if not _has_column(con, "clients", "deleted_at"):
        try:
            con.execute("ALTER TABLE clients ADD COLUMN deleted_at TEXT")
        except sqlite3.OperationalError:
            pass
    con.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_clients_tipo_doc_norm
        ON clients(tipo_documento, documento_norm)
        """
    )
    con.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_clients_nombre
        ON clients(nombre)
        """
    )
    con.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_clients_country
        ON clients(country_code)
        """
    )
    con.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_clients_deleted
        ON clients(deleted_at)
        """
    )


def _country_code_norm(country_code: Any) -> str:
    c = str(country_code or "").strip().upper()
    if c in ("PE", "PERU"):
        return "PE"
    if c in ("VE", "VENEZUELA"):
        return "VE"
    if c in ("PY", "PARAGUAY"):
        return "PY"
    return c


def _collapse_spaces(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _extract_doc_body(value: Any) -> str:
    raw = str(value or "").strip().upper()
    if not raw:
        return ""
    m = re.match(r"^([A-Z]+)-(.+)$", raw)
    if m:
        return str(m.group(2) or "").strip()
    return raw


def _normalize_doc_store(value: Any) -> str:
    body = _extract_doc_body(value)
    if not body:
        return ""
    body = re.sub(r"\s+", "", body)
    return re.sub(r"[^0-9A-Z]", "", body)


def _normalize_doc_key(value: Any) -> str:
    return _normalize_doc_store(value)


_DEFAULT_DOC_TYPE_BY_COUNTRY: dict[str, str] = {
    "PE": "DNI",
    "VE": "V",
    "PY": "CI",
}

_GENERIC_CLIENTS: tuple[dict[str, str], ...] = (
    {
        "country_code": "PE",
        "tipo_documento": "DNI",
        "documento": "00000000",
        "nombre": "CLIENTE GENERICO",
        "telefono": "0",
    },
    {
        "country_code": "PY",
        "tipo_documento": "CI",
        "documento": "00000000",
        "nombre": "CLIENTE GENERICO",
        "telefono": "0",
    },
    {
        "country_code": "VE",
        "tipo_documento": "V",
        "documento": "0",
        "nombre": "CLIENTE GENERICO",
        "telefono": "0",
    },
)


def _known_country_code(country_code: Any) -> str:
    cc = _country_code_norm(country_code)
    if cc in _DEFAULT_DOC_TYPE_BY_COUNTRY:
        return cc
    return "PE"


def _normalize_name_key(value: Any) -> str:
    return str(_collapse_spaces(value) or "").strip().lower()


def _normalize_phone_key(value: Any) -> str:
    raw = str(_collapse_spaces(value) or "")
    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    if digits:
        return digits
    return raw.lower()


def _default_doc_type_for_country(country_code: Any) -> str:
    cc = _known_country_code(country_code)
    preferred = str(_DEFAULT_DOC_TYPE_BY_COUNTRY.get(cc, "") or "").strip().upper()
    rules = document_type_rules_for_country(cc) or []
    allowed = {str(r.get("nombre") or "").strip().upper() for r in rules}
    if preferred and preferred in allowed:
        return preferred
    for r in rules:
        t = str(r.get("nombre") or "").strip().upper()
        if t:
            return t
    return preferred or "DNI"


def ensure_generic_clients(con: sqlite3.Connection) -> None:
    """
    Garantiza clientes genéricos por país:
    - PE: DNI 00000000
    - PY: CI 00000000
    - VE: V 0
    """
    ensure_clients_table(con)
    for spec in _GENERIC_CLIENTS:
        payload = normalize_client_payload(
            country_code=spec.get("country_code", ""),
            tipo_documento=spec.get("tipo_documento", ""),
            documento=spec.get("documento", ""),
            nombre=spec.get("nombre", ""),
            telefono=spec.get("telefono", ""),
            require_valid_document=False,
        )
        con.execute(
            """
            INSERT INTO clients(
                country_code,
                tipo_documento,
                documento,
                documento_norm,
                nombre,
                telefono,
                source_quote_id,
                source_created_at,
                created_at,
                updated_at,
                deleted_at
            ) VALUES(?, ?, ?, ?, ?, ?, NULL, '', datetime('now'), datetime('now'), NULL)
            ON CONFLICT(tipo_documento, documento_norm) DO UPDATE SET
                country_code = excluded.country_code,
                documento = excluded.documento,
                nombre = excluded.nombre,
                telefono = excluded.telefono,
                source_quote_id = NULL,
                source_created_at = '',
                deleted_at = NULL,
                updated_at = datetime('now')
            """,
            (
                payload["country_code"],
                payload["tipo_documento"],
                payload["documento"],
                payload["documento_norm"],
                payload["nombre"],
                payload["telefono"],
            ),
        )


def _doc_pad_for_type(country_code: Any, doc_type: Any) -> int:
    rule = document_type_rule(_known_country_code(country_code), doc_type)
    if not rule:
        return 0
    try:
        return int(rule.get("validation_pad") or 0)
    except Exception:
        return 0


def _document_is_valid(country_code: Any, doc_type: Any, documento: Any) -> bool:
    cc = _known_country_code(country_code)
    tipo = str(doc_type or "").strip().upper()
    doc = str(documento or "").strip().upper()
    if (not tipo) or (not doc):
        return False
    ok, _msg = validate_document_for_type(cc, tipo, doc)
    if not ok:
        return False
    return bool(_normalize_doc_key(doc))


def _current_used_doc_keys(con: sqlite3.Connection) -> set[tuple[str, str]]:
    ensure_clients_table(con)
    rows = con.execute(
        """
        SELECT
            COALESCE(tipo_documento, '') AS tipo_documento,
            COALESCE(documento_norm, '') AS documento_norm
        FROM clients
        """
    ).fetchall()
    out: set[tuple[str, str]] = set()
    for r in rows:
        t = str(r["tipo_documento"] or "").strip().upper()
        d = str(r["documento_norm"] or "").strip().upper()
        if t and d:
            out.add((t, d))
    return out


def _next_synthetic_document(
    *,
    country_code: Any,
    used_doc_keys: set[tuple[str, str]],
    seq_state: dict[str, int],
) -> tuple[str, str, str]:
    cc = _known_country_code(country_code)
    doc_type = _default_doc_type_for_country(cc)
    pad = _doc_pad_for_type(cc, doc_type)
    seq = int(seq_state.get(cc, 1) or 1)
    max_iter = 1_000_000
    cur_iter = 0
    while cur_iter < max_iter:
        cur_iter += 1
        if pad > 0:
            doc = str(seq).zfill(pad)
        else:
            doc = str(seq)
        seq += 1

        doc_norm = _normalize_doc_key(doc)
        if not doc_norm:
            continue
        key = (doc_type, doc_norm)
        if key in used_doc_keys:
            continue
        if not _document_is_valid(cc, doc_type, doc):
            continue
        used_doc_keys.add(key)
        seq_state[cc] = seq
        return doc_type, doc, doc_norm
    raise RuntimeError("No se pudo generar documento sintético disponible.")


def normalize_client_payload(
    *,
    country_code: Any,
    tipo_documento: Any,
    documento: Any,
    nombre: Any,
    telefono: Any,
    require_valid_document: bool = True,
) -> dict[str, Any]:
    cc = _known_country_code(country_code)
    name = _collapse_spaces(nombre)
    phone = _collapse_spaces(telefono)

    doc_store = _normalize_doc_store(documento)
    doc_key = _normalize_doc_key(doc_store)

    tipo_in = str(tipo_documento or "").strip().upper()
    tipo_norm = infer_tipo_documento_from_doc(
        cc,
        doc_store,
        explicit_tipo=tipo_in,
    )
    if not tipo_norm:
        tipo_norm = tipo_in

    if require_valid_document:
        if not tipo_norm:
            raise ValueError("Selecciona un tipo de documento valido.")
        ok, msg = validate_document_for_type(cc, tipo_norm, doc_store)
        if not ok:
            raise ValueError(msg or "Documento invalido.")

    return {
        "country_code": cc,
        "tipo_documento": str(tipo_norm or "").strip().upper(),
        "documento": str(doc_store or "").strip().upper(),
        "documento_norm": str(doc_key or "").strip().upper(),
        "nombre": name,
        "telefono": phone,
    }


def _get_client_id_by_key(
    con: sqlite3.Connection,
    *,
    tipo_documento: str,
    documento_norm: str,
    include_deleted: bool = True,
) -> int | None:
    where = ["tipo_documento = ?", "documento_norm = ?"]
    if (not include_deleted) and _has_column(con, "clients", "deleted_at"):
        where.append("deleted_at IS NULL")
    row = con.execute(
        f"""
        SELECT id
        FROM clients
        WHERE {' AND '.join(where)}
        ORDER BY id ASC
        LIMIT 1
        """,
        (str(tipo_documento or ""), str(documento_norm or "")),
    ).fetchone()
    if not row:
        return None
    return int(row["id"])


def _find_client_id_by_identity(
    con: sqlite3.Connection,
    *,
    country_code: Any,
    nombre: Any,
    telefono: Any,
    exclude_id: int | None = None,
) -> int | None:
    cc = _known_country_code(country_code)
    name_key = _normalize_name_key(nombre)
    phone_key = _normalize_phone_key(telefono)
    if (not name_key) or (not phone_key):
        return None

    rows = con.execute(
        """
        SELECT id, nombre, telefono
        FROM clients
        WHERE UPPER(TRIM(COALESCE(country_code, ''))) = ?
          AND LOWER(TRIM(COALESCE(nombre, ''))) = ?
          AND deleted_at IS NULL
        ORDER BY id ASC
        """,
        (cc, name_key),
    ).fetchall()
    for r in rows:
        cid = int(r["id"] or 0)
        if exclude_id is not None and cid == int(exclude_id):
            continue
        if _normalize_phone_key(r["telefono"]) == phone_key:
            return cid
    return None


def upsert_client(
    con: sqlite3.Connection,
    *,
    country_code: Any,
    tipo_documento: Any,
    documento: Any,
    nombre: Any,
    telefono: Any,
    source_quote_id: int | None = None,
    source_created_at: str | None = None,
    require_valid_document: bool = True,
) -> int | None:
    ensure_clients_table(con)

    payload = normalize_client_payload(
        country_code=country_code,
        tipo_documento=tipo_documento,
        documento=documento,
        nombre=nombre,
        telefono=telefono,
        require_valid_document=require_valid_document,
    )
    tipo = str(payload.get("tipo_documento") or "")
    doc_norm = str(payload.get("documento_norm") or "")
    if (not tipo) or (not doc_norm) or (not _document_is_valid(payload.get("country_code"), tipo, payload.get("documento"))):
        if require_valid_document:
            return None
        used = _current_used_doc_keys(con)
        seq_state: dict[str, int] = {}
        tipo_syn, doc_syn, doc_norm_syn = _next_synthetic_document(
            country_code=payload.get("country_code"),
            used_doc_keys=used,
            seq_state=seq_state,
        )
        payload["tipo_documento"] = tipo_syn
        payload["documento"] = doc_syn
        payload["documento_norm"] = doc_norm_syn

    if require_valid_document and not str(payload.get("nombre") or "").strip():
        raise ValueError("Nombre de cliente vacio.")
    if require_valid_document and not str(payload.get("telefono") or "").strip():
        raise ValueError("Telefono de cliente vacio.")

    # Dedupe por identidad (nombre + telefono) para evitar duplicados
    # con documentos distintos para la misma persona.
    same_identity_id = _find_client_id_by_identity(
        con,
        country_code=payload.get("country_code"),
        nombre=payload.get("nombre"),
        telefono=payload.get("telefono"),
    )
    if same_identity_id is not None:
        con.execute(
            """
            UPDATE clients
            SET
                country_code = ?,
                nombre = CASE
                    WHEN TRIM(COALESCE(?, '')) <> '' THEN ?
                    ELSE nombre
                END,
                telefono = CASE
                    WHEN TRIM(COALESCE(?, '')) <> '' THEN ?
                    ELSE telefono
                END,
                source_quote_id = CASE
                    WHEN ? IS NOT NULL THEN ?
                    ELSE source_quote_id
                END,
                source_created_at = CASE
                    WHEN TRIM(COALESCE(?, '')) <> '' THEN ?
                    ELSE source_created_at
                END,
                deleted_at = NULL,
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (
                payload["country_code"],
                payload["nombre"],
                payload["nombre"],
                payload["telefono"],
                payload["telefono"],
                (int(source_quote_id) if source_quote_id is not None else None),
                (int(source_quote_id) if source_quote_id is not None else None),
                str(source_created_at or ""),
                str(source_created_at or ""),
                int(same_identity_id),
            ),
        )
        return int(same_identity_id)

    con.execute(
        """
        INSERT INTO clients(
            country_code,
            tipo_documento,
            documento,
            documento_norm,
            nombre,
            telefono,
            source_quote_id,
            source_created_at,
            created_at,
            updated_at
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        ON CONFLICT(tipo_documento, documento_norm) DO UPDATE SET
            country_code = excluded.country_code,
            documento = excluded.documento,
            nombre = CASE
                WHEN TRIM(COALESCE(excluded.nombre, '')) <> '' THEN excluded.nombre
                ELSE clients.nombre
            END,
            telefono = CASE
                WHEN TRIM(COALESCE(excluded.telefono, '')) <> '' THEN excluded.telefono
                ELSE clients.telefono
            END,
            source_quote_id = CASE
                WHEN excluded.source_quote_id IS NOT NULL THEN excluded.source_quote_id
                ELSE clients.source_quote_id
            END,
            source_created_at = CASE
                WHEN TRIM(COALESCE(excluded.source_created_at, '')) <> '' THEN excluded.source_created_at
                ELSE clients.source_created_at
            END,
            deleted_at = NULL,
            updated_at = datetime('now')
        """,
        (
            payload["country_code"],
            payload["tipo_documento"],
            payload["documento"],
            payload["documento_norm"],
            payload["nombre"],
            payload["telefono"],
            (int(source_quote_id) if source_quote_id is not None else None),
            str(source_created_at or ""),
        ),
    )
    return _get_client_id_by_key(
        con,
        tipo_documento=payload["tipo_documento"],
        documento_norm=payload["documento_norm"],
        include_deleted=True,
    )


def save_client(
    con: sqlite3.Connection,
    *,
    country_code: Any,
    tipo_documento: Any,
    documento: Any,
    nombre: Any,
    telefono: Any,
    client_id: int | None = None,
) -> int:
    ensure_clients_table(con)

    payload = normalize_client_payload(
        country_code=country_code,
        tipo_documento=tipo_documento,
        documento=documento,
        nombre=nombre,
        telefono=telefono,
        require_valid_document=True,
    )
    if not str(payload.get("nombre") or "").strip():
        raise ValueError("Nombre de cliente vacio.")
    if not str(payload.get("telefono") or "").strip():
        raise ValueError("Telefono de cliente vacio.")

    tipo = str(payload["tipo_documento"])
    doc_norm = str(payload["documento_norm"])
    existing_row = con.execute(
        """
        SELECT id, deleted_at
        FROM clients
        WHERE tipo_documento = ? AND documento_norm = ?
        ORDER BY id ASC
        LIMIT 1
        """,
        (tipo, doc_norm),
    ).fetchone()
    existing = int(existing_row["id"]) if existing_row else None
    existing_deleted = bool(existing_row and str(existing_row["deleted_at"] or "").strip())
    if existing is not None and (client_id is None or int(existing) != int(client_id)):
        # Si existia solo en estado eliminado, lo reactivamos en lugar de fallar.
        if client_id is None and existing_deleted:
            con.execute(
                """
                UPDATE clients
                SET
                    country_code = ?,
                    tipo_documento = ?,
                    documento = ?,
                    documento_norm = ?,
                    nombre = ?,
                    telefono = ?,
                    deleted_at = NULL,
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                (
                    payload["country_code"],
                    payload["tipo_documento"],
                    payload["documento"],
                    payload["documento_norm"],
                    payload["nombre"],
                    payload["telefono"],
                    int(existing),
                ),
            )
            return int(existing)
        raise ValueError("Ya existe un cliente con ese tipo y numero de documento.")

    same_identity = _find_client_id_by_identity(
        con,
        country_code=payload.get("country_code"),
        nombre=payload.get("nombre"),
        telefono=payload.get("telefono"),
        exclude_id=(int(client_id) if client_id is not None else None),
    )
    if same_identity is not None and (client_id is None or int(same_identity) != int(client_id)):
        raise ValueError("Ya existe un cliente con el mismo nombre y telefono.")

    if client_id is None:
        cur = con.execute(
            """
            INSERT INTO clients(
                country_code,
                tipo_documento,
                documento,
                documento_norm,
                nombre,
                telefono,
                created_at,
                updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            """,
            (
                payload["country_code"],
                payload["tipo_documento"],
                payload["documento"],
                payload["documento_norm"],
                payload["nombre"],
                payload["telefono"],
            ),
        )
        return int(cur.lastrowid)

    cur = con.execute(
        """
        UPDATE clients
        SET
            country_code = ?,
            tipo_documento = ?,
            documento = ?,
            documento_norm = ?,
            nombre = ?,
            telefono = ?,
            deleted_at = NULL,
            updated_at = datetime('now')
        WHERE id = ?
        """,
        (
            payload["country_code"],
            payload["tipo_documento"],
            payload["documento"],
            payload["documento_norm"],
            payload["nombre"],
            payload["telefono"],
            int(client_id),
        ),
    )
    if int(cur.rowcount or 0) <= 0:
        raise KeyError(f"Cliente no encontrado: {int(client_id)}")
    return int(client_id)


def get_client(
    con: sqlite3.Connection,
    client_id: int,
    *,
    include_deleted: bool = False,
) -> dict[str, Any] | None:
    ensure_clients_table(con)
    where = ["id = ?"]
    if (not include_deleted) and _has_column(con, "clients", "deleted_at"):
        where.append("deleted_at IS NULL")
    row = con.execute(
        """
        SELECT
            id,
            country_code,
            tipo_documento,
            documento,
            documento_norm,
            nombre,
            telefono,
            source_quote_id,
            source_created_at,
            created_at,
            updated_at,
            deleted_at
        FROM clients
        WHERE """
        + " AND ".join(where)
        + """
        LIMIT 1
        """,
        (int(client_id),),
    ).fetchone()
    if not row:
        return None
    return dict(row)


def list_clients(
    con: sqlite3.Connection,
    *,
    country_code: Any = "",
    search_text: str = "",
    include_deleted: bool = False,
    limit: int = 500,
    offset: int = 0,
) -> list[dict[str, Any]]:
    ensure_clients_table(con)

    where: list[str] = []
    params: list[Any] = []
    if (not include_deleted) and _has_column(con, "clients", "deleted_at"):
        where.append("deleted_at IS NULL")

    cc = _country_code_norm(country_code)
    if cc:
        where.append("UPPER(TRIM(COALESCE(country_code, ''))) = ?")
        params.append(cc)

    st = str(search_text or "").strip().lower()
    if st:
        like = f"%{st}%"
        st_ns = re.sub(r"\s+", "", st)
        like_ns = f"%{st_ns}%"
        where.append(
            """
            (
                LOWER(COALESCE(nombre, '')) LIKE ?
                OR LOWER(COALESCE(tipo_documento, '')) LIKE ?
                OR LOWER(COALESCE(documento, '')) LIKE ?
                OR LOWER(COALESCE(telefono, '')) LIKE ?
                OR LOWER(COALESCE(documento_norm, '')) LIKE ?
                OR REPLACE(LOWER(COALESCE(nombre, '')), ' ', '') LIKE ?
                OR REPLACE(LOWER(COALESCE(documento, '')), ' ', '') LIKE ?
                OR REPLACE(LOWER(COALESCE(telefono, '')), ' ', '') LIKE ?
            )
            """
        )
        params.extend([like, like, like, like, like, like_ns, like_ns, like_ns])

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    rows = con.execute(
        f"""
        SELECT
            id,
            country_code,
            tipo_documento,
            documento,
            documento_norm,
            nombre,
            telefono,
            source_quote_id,
            source_created_at,
            created_at,
            updated_at,
            deleted_at
        FROM clients
        {where_sql}
        ORDER BY LOWER(COALESCE(nombre, '')) ASC, id ASC
        LIMIT ? OFFSET ?
        """,
        tuple(params + [max(1, int(limit)), max(0, int(offset))]),
    ).fetchall()
    return [dict(r) for r in rows]


def delete_client(con: sqlite3.Connection, client_id: int) -> None:
    ensure_clients_table(con)
    con.execute(
        """
        UPDATE clients
        SET
            deleted_at = datetime('now'),
            updated_at = datetime('now')
        WHERE id = ?
        """,
        (int(client_id),),
    )
    ensure_generic_clients(con)


def rebuild_clients_from_quotes(con: sqlite3.Connection) -> dict[str, int]:
    ensure_clients_table(con)
    if not _table_exists(con, "quotes"):
        return {"quotes_scanned": 0, "quotes_normalized": 0, "clients_upserted": 0}

    has_tipo_doc = _has_column(con, "quotes", "tipo_documento")
    has_deleted = _has_column(con, "quotes", "deleted_at")
    has_id_cliente = _has_column(con, "quotes", "id_cliente")
    has_legacy_cliente = _has_column(con, "quotes", "cliente")
    has_legacy_cedula = _has_column(con, "quotes", "cedula")
    has_legacy_telefono = _has_column(con, "quotes", "telefono")

    # Esquema nuevo: quotes solo referencia clients por id_cliente.
    if has_id_cliente and not (has_legacy_cliente and has_legacy_cedula and has_legacy_telefono):
        rows = con.execute(
            f"""
            SELECT
                id,
                COALESCE(id_cliente, 0) AS id_cliente,
                COALESCE(created_at, '') AS created_at,
                {"deleted_at" if has_deleted else "NULL AS deleted_at"}
            FROM quotes
            ORDER BY created_at DESC, id DESC
            """
        ).fetchall()

        active_filter_sql = "AND q.deleted_at IS NULL" if has_deleted else ""
        active_filter_sql_sub = "AND q2.deleted_at IS NULL" if has_deleted else ""
        latest_rows = con.execute(
            f"""
            SELECT
                q.id AS quote_id,
                q.id_cliente AS client_id,
                COALESCE(q.created_at, '') AS created_at
            FROM quotes q
            WHERE q.id_cliente IS NOT NULL
              AND q.id_cliente > 0
              {active_filter_sql}
              AND q.id = (
                  SELECT q2.id
                  FROM quotes q2
                  WHERE q2.id_cliente = q.id_cliente
                    {active_filter_sql_sub}
                  ORDER BY q2.created_at DESC, q2.id DESC
                  LIMIT 1
              )
            """
        ).fetchall()

        updated_clients = 0
        if latest_rows:
            payload = [
                (int(r["quote_id"]), str(r["created_at"] or ""), int(r["client_id"]))
                for r in latest_rows
            ]
            con.executemany(
                """
                UPDATE clients
                SET
                    source_quote_id = ?,
                    source_created_at = ?,
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                payload,
            )
            updated_clients = len(payload)

        if has_deleted:
            con.execute(
                """
                DELETE FROM clients
                WHERE source_quote_id IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1
                      FROM quotes q
                      WHERE q.id_cliente = clients.id
                        AND q.deleted_at IS NULL
                  )
                """
            )
        else:
            con.execute(
                """
                DELETE FROM clients
                WHERE source_quote_id IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1
                      FROM quotes q
                      WHERE q.id_cliente = clients.id
                  )
                """
            )

        ensure_generic_clients(con)

        return {
            "quotes_scanned": int(len(rows)),
            "quotes_normalized": 0,
            "clients_upserted": int(updated_clients),
        }

    tipo_sel = "COALESCE(tipo_documento, '') AS tipo_documento" if has_tipo_doc else "'' AS tipo_documento"
    deleted_sel = "deleted_at" if has_deleted else "NULL AS deleted_at"

    rows = con.execute(
        f"""
        SELECT
            id,
            COALESCE(country_code, '') AS country_code,
            COALESCE(cliente, '') AS cliente,
            COALESCE(cedula, '') AS cedula,
            {tipo_sel},
            COALESCE(telefono, '') AS telefono,
            COALESCE(created_at, '') AS created_at,
            {deleted_sel}
        FROM quotes
        ORDER BY created_at DESC, id DESC
        """
    ).fetchall()

    by_identity: dict[tuple[str, str, str], dict[str, Any]] = {}
    quote_rows: list[dict[str, Any]] = []
    active_identities: set[tuple[str, str, str]] = set()
    anonymous_seq = 0

    def _identity_key(*, cc: str, name: str, phone: str, doc_norm: str, quote_id: int) -> tuple[str, str, str]:
        nk = _normalize_name_key(name)
        pk = _normalize_phone_key(phone)
        if pk:
            return (cc, nk, pk)
        if doc_norm:
            return (cc, nk, f"doc:{doc_norm}")
        return (cc, nk, f"id:{int(quote_id)}")

    def _candidate_score(rec: dict[str, Any]) -> tuple[int, int, int]:
        default_tipo = _default_doc_type_for_country(rec.get("country_code", ""))
        return (
            1 if bool(rec.get("doc_valid")) else 0,
            1 if str(rec.get("tipo_documento") or "").strip().upper() == default_tipo else 0,
            len(str(rec.get("documento_norm") or "")),
        )

    def _candidate_better(new_rec: dict[str, Any], old_rec: dict[str, Any]) -> bool:
        s_new = _candidate_score(new_rec)
        s_old = _candidate_score(old_rec)
        if s_new != s_old:
            return s_new > s_old
        return tuple(new_rec.get("_rank", ("", 0))) > tuple(old_rec.get("_rank", ("", 0)))

    for row in rows:
        quote_id = int(row["id"])
        cc = _known_country_code(row["country_code"])
        old_name = _collapse_spaces(row["cliente"])
        old_doc = str(row["cedula"] or "")
        old_tipo = str(row["tipo_documento"] or "")
        old_phone = _collapse_spaces(row["telefono"])
        created_at = str(row["created_at"] or "")
        deleted_at = row["deleted_at"]

        payload = normalize_client_payload(
            country_code=cc,
            tipo_documento=old_tipo,
            documento=old_doc,
            nombre=old_name,
            telefono=old_phone,
            require_valid_document=False,
        )
        name = str(payload.get("nombre") or "")
        if not name:
            anonymous_seq += 1
            name = f"CLIENTE SIN NOMBRE {anonymous_seq}"

        tipo = str(payload.get("tipo_documento") or "").strip().upper()
        doc = str(payload.get("documento") or "").strip().upper()
        doc_norm = str(payload.get("documento_norm") or "").strip().upper()
        phone = str(payload.get("telefono") or "")
        doc_valid = bool(_document_is_valid(cc, tipo, doc))

        ident = _identity_key(cc=cc, name=name, phone=phone, doc_norm=doc_norm, quote_id=quote_id)
        rec = {
            "country_code": cc,
            "tipo_documento": tipo,
            "documento": doc,
            "documento_norm": doc_norm,
            "nombre": name,
            "telefono": phone,
            "source_quote_id": quote_id,
            "source_created_at": created_at,
            "_rank": (created_at, quote_id),
            "doc_valid": doc_valid,
        }
        prev = by_identity.get(ident)
        if prev is None or _candidate_better(rec, prev):
            by_identity[ident] = rec

        quote_rows.append(
            {
                "quote_id": quote_id,
                "identity": ident,
                "old_name": old_name,
                "old_doc": old_doc,
                "old_tipo": old_tipo,
                "old_phone": old_phone,
            }
        )
        if deleted_at is None:
            active_identities.add(ident)

    used_doc_keys: set[tuple[str, str]] = set()
    seq_state: dict[str, int] = {}
    final_by_identity: dict[tuple[str, str, str], dict[str, Any]] = {}

    ordered_identities = sorted(
        by_identity.items(),
        key=lambda kv: tuple(kv[1].get("_rank", ("", 0))),
        reverse=True,
    )
    for ident, rec in ordered_identities:
        cc = str(rec.get("country_code") or "")
        tipo = str(rec.get("tipo_documento") or "").strip().upper()
        doc = str(rec.get("documento") or "").strip().upper()
        doc_norm = str(rec.get("documento_norm") or "").strip().upper()
        doc_key = (tipo, doc_norm) if tipo and doc_norm else ("", "")

        if bool(rec.get("doc_valid")) and tipo and doc_norm and doc_key not in used_doc_keys:
            used_doc_keys.add(doc_key)
            final_tipo = tipo
            final_doc = doc
            final_doc_norm = doc_norm
        else:
            final_tipo, final_doc, final_doc_norm = _next_synthetic_document(
                country_code=cc,
                used_doc_keys=used_doc_keys,
                seq_state=seq_state,
            )

        out = dict(rec)
        out["tipo_documento"] = final_tipo
        out["documento"] = final_doc
        out["documento_norm"] = final_doc_norm
        out["doc_valid"] = True
        final_by_identity[ident] = out

    updates: list[tuple[str, str, str, str, int]] = []
    for q in quote_rows:
        fin = final_by_identity.get(q["identity"])
        if fin is None:
            continue
        new_name = str(fin.get("nombre") or "")
        new_doc = str(fin.get("documento") or "")
        new_tipo = str(fin.get("tipo_documento") or "")
        new_phone = str(fin.get("telefono") or "")

        old_name = str(q.get("old_name") or "")
        old_doc_norm = _normalize_doc_store(q.get("old_doc"))
        old_tipo_norm = str(q.get("old_tipo") or "").strip().upper()
        old_phone = str(q.get("old_phone") or "")

        if (
            new_name != old_name
            or new_doc != old_doc_norm
            or new_tipo != old_tipo_norm
            or new_phone != old_phone
        ):
            updates.append((new_name, new_doc, new_tipo, new_phone, int(q["quote_id"])))

    if updates and has_tipo_doc:
        con.executemany(
            """
            UPDATE quotes
            SET
                cliente = ?,
                cedula = ?,
                tipo_documento = ?,
                telefono = ?
            WHERE id = ?
            """,
            updates,
        )
    elif updates:
        con.executemany(
            """
            UPDATE quotes
            SET
                cliente = ?,
                cedula = ?,
                telefono = ?
            WHERE id = ?
            """,
            [(x[0], x[1], x[3], x[4]) for x in updates],
        )

    con.execute("DELETE FROM clients")
    payloads: list[tuple[Any, ...]] = []
    for ident, item in ordered_identities:
        if ident not in active_identities:
            continue
        fin = final_by_identity.get(ident)
        if not fin:
            continue
        payloads.append(
            (
                fin.get("country_code", ""),
                fin.get("tipo_documento", ""),
                fin.get("documento", ""),
                fin.get("documento_norm", ""),
                fin.get("nombre", ""),
                fin.get("telefono", ""),
                fin.get("source_quote_id", None),
                fin.get("source_created_at", ""),
            )
        )

    if payloads:
        con.executemany(
            """
            INSERT INTO clients(
                country_code,
                tipo_documento,
                documento,
                documento_norm,
                nombre,
                telefono,
                source_quote_id,
                source_created_at,
                created_at,
                updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            """,
            payloads,
        )

    ensure_generic_clients(con)

    return {
        "quotes_scanned": int(len(rows)),
        "quotes_normalized": int(len(updates)),
        "clients_upserted": int(len(payloads)),
    }
