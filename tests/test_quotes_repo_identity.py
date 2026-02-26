import re
import sqlite3

from sqlModels.quotes_repo import find_doc_identity_conflict


def _norm_doc(value: str) -> str:
    raw = str(value or "").strip().upper()
    m = re.match(r"^[A-Z]+-(.+)$", raw)
    if m:
        raw = str(m.group(1) or "").strip().upper()
    return re.sub(r"[^0-9A-Z]", "", raw)


def _mk_con() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute(
        """
        CREATE TABLE clients (
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
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    con.execute(
        """
        CREATE UNIQUE INDEX idx_clients_tipo_doc_norm
        ON clients(tipo_documento, documento_norm)
        """
    )
    con.execute(
        """
        CREATE TABLE quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            country_code TEXT NOT NULL DEFAULT '',
            quote_no TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT '',
            id_cliente INTEGER,
            deleted_at TEXT
        )
        """
    )
    return con


def _upsert_client(
    con: sqlite3.Connection,
    *,
    country_code: str,
    cliente: str,
    cedula: str,
    tipo_documento: str,
    telefono: str,
) -> int:
    tipo = str(tipo_documento or "").strip().upper()
    doc_norm = _norm_doc(cedula)
    doc_store = _norm_doc(cedula)
    con.execute(
        """
        INSERT INTO clients(
            country_code, tipo_documento, documento, documento_norm, nombre, telefono
        ) VALUES(?, ?, ?, ?, ?, ?)
        ON CONFLICT(tipo_documento, documento_norm) DO UPDATE SET
            country_code = excluded.country_code,
            documento = excluded.documento,
            nombre = excluded.nombre,
            telefono = excluded.telefono,
            updated_at = datetime('now')
        """,
        (country_code, tipo, doc_store, doc_norm, cliente, telefono),
    )
    row = con.execute(
        """
        SELECT id
        FROM clients
        WHERE tipo_documento = ? AND documento_norm = ?
        LIMIT 1
        """,
        (tipo, doc_norm),
    ).fetchone()
    return int(row["id"])


def _ins(
    con: sqlite3.Connection,
    *,
    quote_no: str,
    cliente: str,
    cedula: str,
    telefono: str,
    country_code: str = "PE",
    tipo_documento: str = "DNI",
    deleted_at: str | None = None,
):
    client_id = _upsert_client(
        con,
        country_code=country_code,
        cliente=cliente,
        cedula=cedula,
        tipo_documento=tipo_documento,
        telefono=telefono,
    )
    cur = con.execute(
        """
        INSERT INTO quotes(country_code, quote_no, created_at, id_cliente, deleted_at)
        VALUES(?, ?, '2026-02-24T10:00:00', ?, ?)
        """,
        (country_code, quote_no, client_id, deleted_at),
    )
    quote_id = int(cur.lastrowid)
    con.execute(
        """
        UPDATE clients
        SET source_quote_id = ?, source_created_at = '2026-02-24T10:00:00'
        WHERE id = ?
        """,
        (quote_id, client_id),
    )


def test_find_doc_identity_conflict_allows_same_triplet():
    con = _mk_con()
    _ins(con, quote_no="PE-001-0000001", cliente="Juan Perez", cedula="12345678", tipo_documento="DNI", telefono="912345678")
    got = find_doc_identity_conflict(
        con,
        country_code="PE",
        tipo_documento="DNI",
        cedula="12345678",
        cliente="Juan Perez",
        telefono="912345678",
    )
    assert got is None
    con.close()


def test_find_doc_identity_conflict_blocks_different_name_or_phone():
    con = _mk_con()
    _ins(con, quote_no="PE-001-0000002", cliente="Juan Perez", cedula="12345678", tipo_documento="DNI", telefono="912345678")

    got_name = find_doc_identity_conflict(
        con,
        country_code="PE",
        tipo_documento="DNI",
        cedula="12345678",
        cliente="Maria Lopez",
        telefono="912345678",
    )
    assert got_name is not None
    assert got_name["quote_no"] == "PE-001-0000002"
    assert got_name["same_cliente"] is False
    assert got_name["same_telefono"] is True

    got_phone = find_doc_identity_conflict(
        con,
        country_code="PE",
        tipo_documento="DNI",
        cedula="12345678",
        cliente="Juan Perez",
        telefono="999888777",
    )
    assert got_phone is not None
    assert got_phone["same_cliente"] is True
    assert got_phone["same_telefono"] is False
    con.close()


def test_find_doc_identity_conflict_matches_prefixed_doc_and_ignores_deleted():
    con = _mk_con()
    _ins(
        con,
        quote_no="PE-001-0000003",
        cliente="Cliente Viejo",
        cedula="DNI-12345678",
        tipo_documento="DNI",
        telefono="900111222",
        deleted_at="2026-02-24T12:00:00",
    )
    got_deleted = find_doc_identity_conflict(
        con,
        country_code="PE",
        tipo_documento="DNI",
        cedula="12345678",
        cliente="Otro",
        telefono="900999888",
    )
    assert got_deleted is None

    _ins(
        con,
        quote_no="PE-001-0000004",
        cliente="Cliente Activo",
        cedula="DNI-12345678",
        tipo_documento="DNI",
        telefono="900111222",
    )
    got_active = find_doc_identity_conflict(
        con,
        country_code="PE",
        tipo_documento="DNI",
        cedula="12345678",
        cliente="Otro",
        telefono="900999888",
    )
    assert got_active is not None
    assert got_active["quote_no"] == "PE-001-0000004"
    con.close()


def test_find_doc_identity_conflict_allows_same_number_with_different_type():
    con = _mk_con()
    _ins(
        con,
        quote_no="PE-001-0000005",
        cliente="Cliente DNI",
        cedula="12345678",
        tipo_documento="DNI",
        telefono="900123123",
    )
    got = find_doc_identity_conflict(
        con,
        country_code="PE",
        tipo_documento="CE",
        cedula="12345678",
        cliente="Cliente CE",
        telefono="900321321",
    )
    assert got is None
    con.close()
