import sqlite3

from sqlModels.clients_repo import (
    delete_client,
    ensure_clients_table,
    get_client,
    list_clients,
    rebuild_clients_from_quotes,
    save_client,
    upsert_client,
)
from sqlModels.db import connect, ensure_schema, tx


def test_save_client_enforces_unique_tipo_numero():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    ensure_clients_table(con)

    cid = save_client(
        con,
        country_code="PE",
        tipo_documento="DNI",
        documento="12345678",
        nombre="Cliente Uno",
        telefono="912345678",
    )
    assert int(cid) > 0

    try:
        save_client(
            con,
            country_code="PE",
            tipo_documento="DNI",
            documento="12345678",
            nombre="Cliente Dos",
            telefono="900000000",
        )
        raise AssertionError("Se esperaba ValueError por documento duplicado.")
    except ValueError:
        pass
    finally:
        con.close()


def test_upsert_client_merges_same_tipo_numero():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    ensure_clients_table(con)

    c1 = upsert_client(
        con,
        country_code="PE",
        tipo_documento="DNI",
        documento="12345678",
        nombre="Cliente Uno",
        telefono="912345678",
        source_quote_id=10,
        source_created_at="2026-02-24T10:00:00",
        require_valid_document=True,
    )
    c2 = upsert_client(
        con,
        country_code="PE",
        tipo_documento="DNI",
        documento="DNI-12345678",
        nombre="Cliente Uno Editado",
        telefono="900111222",
        source_quote_id=11,
        source_created_at="2026-02-24T11:00:00",
        require_valid_document=True,
    )
    assert c1 == c2

    rows = list_clients(con, country_code="PE")
    assert len(rows) == 1
    assert str(rows[0]["tipo_documento"]) == "DNI"
    assert str(rows[0]["documento"]) == "12345678"
    assert str(rows[0]["nombre"]) == "Cliente Uno Editado"
    assert str(rows[0]["telefono"]) == "900111222"
    con.close()


def test_upsert_client_dedupes_same_name_phone_even_with_other_document():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    ensure_clients_table(con)

    c1 = upsert_client(
        con,
        country_code="PE",
        tipo_documento="DNI",
        documento="12345678",
        nombre="Yoneiker",
        telefono="912566666",
        require_valid_document=True,
    )
    c2 = upsert_client(
        con,
        country_code="PE",
        tipo_documento="P",
        documento="A123456",
        nombre="Yoneiker",
        telefono="912566666",
        require_valid_document=True,
    )
    assert c1 == c2

    rows = list_clients(con, country_code="PE")
    assert len(rows) == 1
    assert str(rows[0]["documento"]) == "12345678"
    assert str(rows[0]["tipo_documento"]) == "DNI"
    con.close()


def test_delete_client_is_soft_delete_and_hidden_from_list():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    ensure_clients_table(con)

    cid = save_client(
        con,
        country_code="PE",
        tipo_documento="DNI",
        documento="22334455",
        nombre="Cliente Borrable",
        telefono="900999111",
    )
    delete_client(con, int(cid))

    row = con.execute(
        "SELECT deleted_at FROM clients WHERE id = ? LIMIT 1",
        (int(cid),),
    ).fetchone()
    assert row is not None
    assert str(row["deleted_at"] or "").strip() != ""

    listed = list_clients(con, country_code="PE", search_text="borrable")
    assert listed == []

    assert get_client(con, int(cid)) is None
    assert get_client(con, int(cid), include_deleted=True) is not None
    con.close()


def test_save_client_reactivates_soft_deleted_document():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    ensure_clients_table(con)

    cid = save_client(
        con,
        country_code="PE",
        tipo_documento="DNI",
        documento="55667788",
        nombre="Cliente Original",
        telefono="900100100",
    )
    delete_client(con, int(cid))

    cid2 = save_client(
        con,
        country_code="PE",
        tipo_documento="DNI",
        documento="55667788",
        nombre="Cliente Reactivado",
        telefono="900200200",
    )
    assert int(cid2) == int(cid)

    got = get_client(con, int(cid2))
    assert got is not None
    assert str(got["deleted_at"] or "").strip() == ""
    assert str(got["nombre"] or "") == "Cliente Reactivado"
    assert str(got["telefono"] or "") == "900200200"
    con.close()


def test_migration_v24_backfills_id_cliente_and_drops_legacy_columns(tmp_path):
    db_path = str(tmp_path / "quotes_v24.sqlite3")
    con = connect(db_path)
    ensure_schema(con)

    with tx(con):
        con.execute("UPDATE meta SET value = '23' WHERE key = 'schema_version'")
        con.execute("DROP TABLE IF EXISTS quotes")
        con.execute(
            """
            CREATE TABLE quotes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                country_code TEXT NOT NULL,
                quote_no TEXT NOT NULL,
                created_at TEXT NOT NULL,
                cliente TEXT NOT NULL,
                cedula TEXT NOT NULL,
                tipo_documento TEXT NOT NULL DEFAULT '',
                telefono TEXT NOT NULL,
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
                deleted_at TEXT
            )
            """
        )
        con.execute(
            """
            INSERT INTO quotes(
                country_code, quote_no, created_at,
                cliente, cedula, tipo_documento, telefono,
                currency_shown, pdf_path, deleted_at
            ) VALUES(
                'PE', 'PE-001-0000001', '2026-02-24T10:00:00',
                'Juan Perez', 'DNI-12345678', 'dni', '912345678',
                'PEN', 'a.pdf', NULL
            )
            """
        )

    ensure_schema(con)

    cols = {str(r["name"]).lower() for r in con.execute("PRAGMA table_info(quotes)").fetchall()}
    assert "id_cliente" in cols
    assert "cliente" not in cols
    assert "cedula" not in cols
    assert "telefono" not in cols
    assert "tipo_documento" not in cols

    q = con.execute(
        """
        SELECT quote_no, id_cliente
        FROM quotes
        WHERE quote_no = 'PE-001-0000001'
        LIMIT 1
        """
    ).fetchone()
    assert q is not None
    assert int(q["id_cliente"] or 0) > 0

    c = con.execute(
        """
        SELECT nombre, tipo_documento, documento, telefono
        FROM clients
        WHERE id = ?
        LIMIT 1
        """,
        (int(q["id_cliente"]),),
    ).fetchone()
    assert c is not None
    assert str(c["nombre"]) == "Juan Perez"
    assert str(c["tipo_documento"]) == "DNI"
    assert str(c["documento"]) == "12345678"
    assert str(c["telefono"]) == "912345678"
    con.close()


def test_rebuild_clients_from_quotes_new_schema_prunes_deleted_quote_clients(tmp_path):
    db_path = str(tmp_path / "clients_rebuild_new_schema.sqlite3")
    con = connect(db_path)
    ensure_schema(con)

    with tx(con):
        cur1 = con.execute(
            """
            INSERT INTO clients(
                country_code, tipo_documento, documento, documento_norm,
                nombre, telefono, source_quote_id, source_created_at
            )
            VALUES('PE','DNI','12345678','12345678','Activo','900111222',NULL,'')
            """
        )
        c_active = int(cur1.lastrowid)

        cur2 = con.execute(
            """
            INSERT INTO clients(
                country_code, tipo_documento, documento, documento_norm,
                nombre, telefono, source_quote_id, source_created_at
            )
            VALUES('PE','DNI','87654321','87654321','Solo Eliminado','900222333',NULL,'')
            """
        )
        c_deleted_only = int(cur2.lastrowid)

        con.execute(
            """
            INSERT INTO clients(
                country_code, tipo_documento, documento, documento_norm,
                nombre, telefono, source_quote_id, source_created_at
            )
            VALUES('PE','DNI','11112222','11112222','Manual','900333444',NULL,'')
            """
        )

        q1 = con.execute(
            """
            INSERT INTO quotes(
                country_code, quote_no, created_at, id_cliente,
                currency_shown, pdf_path, deleted_at
            )
            VALUES('PE','PE-001-0000101','2026-02-24T10:00:00',?,'PEN','a.pdf',NULL)
            """,
            (c_active,),
        ).lastrowid
        q2 = con.execute(
            """
            INSERT INTO quotes(
                country_code, quote_no, created_at, id_cliente,
                currency_shown, pdf_path, deleted_at
            )
            VALUES('PE','PE-001-0000102','2026-02-24T11:00:00',?,'PEN','b.pdf','2026-02-24T12:00:00')
            """,
            (c_deleted_only,),
        ).lastrowid

        con.execute(
            "UPDATE clients SET source_quote_id = ?, source_created_at = '2026-02-24T10:00:00' WHERE id = ?",
            (int(q1), c_active),
        )
        con.execute(
            "UPDATE clients SET source_quote_id = ?, source_created_at = '2026-02-24T11:00:00' WHERE id = ?",
            (int(q2), c_deleted_only),
        )

    stats = rebuild_clients_from_quotes(con)
    assert int(stats["quotes_scanned"]) == 2

    rows = con.execute(
        """
        SELECT nombre
        FROM clients
        ORDER BY nombre
        """
    ).fetchall()
    names = [str(r["nombre"] or "") for r in rows]
    assert "Activo" in names
    assert "Manual" in names
    assert "Solo Eliminado" not in names
    con.close()


def test_migration_v25_normalizes_invalid_client_documents(tmp_path):
    db_path = str(tmp_path / "clients_v25.sqlite3")
    con = connect(db_path)
    ensure_schema(con)

    with tx(con):
        con.execute("UPDATE meta SET value = '24' WHERE key = 'schema_version'")
        con.execute("DELETE FROM clients")
        con.execute(
            """
            INSERT INTO clients(
                country_code, tipo_documento, documento, documento_norm,
                nombre, telefono, source_quote_id, source_created_at
            ) VALUES
                ('PE','DNI','45','45','Cliente 45','900111111',NULL,''),
                ('PE','DNI','10','10','Cliente 10','900222222',NULL,''),
                ('PE','DNI','00001234','00001234','Cliente Ok','900333333',NULL,'')
            """
        )

    ensure_schema(con)

    rows = con.execute(
        """
        SELECT nombre, tipo_documento, documento
        FROM clients
        WHERE nombre IN ('Cliente 45', 'Cliente 10', 'Cliente Ok')
        ORDER BY id ASC
        """
    ).fetchall()
    by_name = {str(r["nombre"]): str(r["documento"]) for r in rows}
    assert len(rows) == 3
    assert len({str(r["documento"]) for r in rows}) == 3
    assert str(by_name["Cliente Ok"]) == "00001234"
    assert len(str(by_name["Cliente 45"])) == 8
    assert len(str(by_name["Cliente 10"])) == 8

    bad = con.execute(
        """
        SELECT COUNT(*) AS n
        FROM clients
        WHERE UPPER(COALESCE(country_code,''))='PE'
          AND UPPER(COALESCE(tipo_documento,''))='DNI'
          AND LENGTH(TRIM(COALESCE(documento,''))) < 8
        """
    ).fetchone()
    assert int(bad["n"] or 0) == 0
    con.close()
