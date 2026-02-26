from __future__ import annotations

from sqlModels.db import connect, ensure_schema, tx
from src.ai.search_index import LocalSearchIndex, _FTS_CLIENTS, ensure_ai_schema


def test_search_clients_excludes_deleted_even_with_stale_fts(tmp_path):
    db_path = str(tmp_path / "search_clients.sqlite3")
    con = connect(db_path)
    ensure_schema(con)
    with tx(con):
        cur = con.execute(
            """
            INSERT INTO clients(
                country_code, tipo_documento, documento, documento_norm,
                nombre, telefono, source_quote_id, source_created_at
            )
            VALUES('PE','DNI','12345678','12345678',
                   'Cliente Activo','900111222',NULL,'2026-02-24T10:00:00')
            """
        )
        active_client_id = int(cur.lastrowid)

        con.execute(
            """
            INSERT INTO quotes(
                country_code, quote_no, created_at,
                id_cliente,
                currency_shown, pdf_path, deleted_at
            )
            VALUES('PE','PE-001-0000001','2026-02-24T10:00:00',
                   ?,
                   'PEN','activo.pdf',NULL)
            """,
            (active_client_id,),
        )
        con.execute(
            """
            INSERT INTO quotes(
                country_code, quote_no, created_at,
                id_cliente,
                currency_shown, pdf_path, deleted_at
            )
            VALUES('PE','PE-001-0000002','2026-02-24T11:00:00',
                   NULL,
                   'PEN','elim.pdf','2026-02-24T12:00:00')
            """
        )
        con.execute(
            """
            UPDATE clients
            SET source_quote_id = 1
            WHERE id = ?
            """,
            (active_client_id,),
        )

        # Simula indice FTS stale con cliente eliminado.
        if ensure_ai_schema(con):
            con.execute(
                f"""
                INSERT INTO {_FTS_CLIENTS}(cliente, cedula, telefono)
                VALUES('Cliente Eliminado','22222222','900222333')
                """
            )
    con.close()

    idx = LocalSearchIndex(db_path)
    rows_deleted = idx.search_clients("Eliminado", limit=10)
    assert rows_deleted == []

    rows_active = idx.search_clients("Activo", limit=10)
    assert rows_active
    assert any(str(r.get("cliente") or "") == "Cliente Activo" for r in rows_active)
    assert all(str(r.get("cliente") or "") != "Cliente Eliminado" for r in rows_active)


def test_search_clients_keeps_generic_client_as_first_option_when_query_matches_generic(tmp_path):
    db_path = str(tmp_path / "search_clients_generic_first.sqlite3")
    con = connect(db_path)
    ensure_schema(con)
    with tx(con):
        con.execute(
            """
            INSERT INTO settings(key, value) VALUES('country', 'PERU')
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """
        )
        cur = con.execute(
            """
            INSERT INTO clients(
                country_code, tipo_documento, documento, documento_norm,
                nombre, telefono, source_quote_id, source_created_at
            )
            VALUES('PE','DNI','12345678','12345678',
                   'Cliente Activo','900111222',NULL,'2026-02-24T10:00:00')
            """
        )
        active_client_id = int(cur.lastrowid)

        con.execute(
            """
            INSERT INTO quotes(
                country_code, quote_no, created_at,
                id_cliente,
                currency_shown, pdf_path, deleted_at
            )
            VALUES('PE','PE-001-0000003','2026-02-24T10:00:00',
                   ?,
                   'PEN','activo.pdf',NULL)
            """,
            (active_client_id,),
        )
    con.close()

    idx = LocalSearchIndex(db_path)
    rows = idx.search_clients("00000000", limit=10)
    assert rows
    assert str(rows[0].get("cliente") or "").strip().upper() == "CLIENTE GENERICO"
    assert str(rows[0].get("country_code") or "").strip().upper() == "PE"


def test_search_clients_does_not_force_generic_first_when_query_is_other_client(tmp_path):
    db_path = str(tmp_path / "search_clients_generic_not_first.sqlite3")
    con = connect(db_path)
    ensure_schema(con)
    with tx(con):
        con.execute(
            """
            INSERT INTO settings(key, value) VALUES('country', 'PERU')
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """
        )
        cur = con.execute(
            """
            INSERT INTO clients(
                country_code, tipo_documento, documento, documento_norm,
                nombre, telefono, source_quote_id, source_created_at
            )
            VALUES('PE','DNI','12345678','12345678',
                   'Cliente Activo','900111222',NULL,'2026-02-24T10:00:00')
            """
        )
        active_client_id = int(cur.lastrowid)
        con.execute(
            """
            INSERT INTO quotes(
                country_code, quote_no, created_at,
                id_cliente,
                currency_shown, pdf_path, deleted_at
            )
            VALUES('PE','PE-001-0000004','2026-02-24T10:00:00',
                   ?,
                   'PEN','activo.pdf',NULL)
            """,
            (active_client_id,),
        )
    con.close()

    idx = LocalSearchIndex(db_path)
    rows = idx.search_clients("Activo", limit=10)
    assert rows
    assert str(rows[0].get("cliente") or "").strip().upper() != "CLIENTE GENERICO"


def test_search_clients_prioritizes_more_similar_client_over_generic(tmp_path):
    db_path = str(tmp_path / "search_clients_similarity_priority.sqlite3")
    con = connect(db_path)
    ensure_schema(con)
    with tx(con):
        con.execute(
            """
            INSERT INTO settings(key, value) VALUES('country', 'PERU')
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """
        )
        cur = con.execute(
            """
            INSERT INTO clients(
                country_code, tipo_documento, documento, documento_norm,
                nombre, telefono, source_quote_id, source_created_at
            )
            VALUES('PE','DNI','12345678','12345678',
                   'CLIENTE','900111222',NULL,'2026-02-24T10:00:00')
            """
        )
        cid = int(cur.lastrowid)
        con.execute(
            """
            INSERT INTO quotes(
                country_code, quote_no, created_at,
                id_cliente,
                currency_shown, pdf_path, deleted_at
            )
            VALUES('PE','PE-001-0000005','2026-02-24T10:00:00',
                   ?,
                   'PEN','cliente.pdf',NULL)
            """,
            (cid,),
        )
    con.close()

    idx = LocalSearchIndex(db_path)
    rows = idx.search_clients("cliente", limit=10)
    assert rows
    assert str(rows[0].get("cliente") or "").strip().upper() == "CLIENTE"


def test_search_clients_hides_when_query_has_no_real_match(tmp_path):
    db_path = str(tmp_path / "search_clients_no_match.sqlite3")
    con = connect(db_path)
    ensure_schema(con)
    with tx(con):
        con.execute(
            """
            INSERT INTO settings(key, value) VALUES('country', 'PERU')
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """
        )
        con.execute(
            """
            INSERT INTO clients(
                country_code, tipo_documento, documento, documento_norm,
                nombre, telefono, source_quote_id, source_created_at
            )
            VALUES('PE','DNI','12345678','12345678',
                   'Cliente Activo','900111222',NULL,'2026-02-24T10:00:00')
            """
        )
    con.close()

    idx = LocalSearchIndex(db_path)
    rows = idx.search_clients("qqqzxyw-no-match", limit=10)
    assert rows == []
