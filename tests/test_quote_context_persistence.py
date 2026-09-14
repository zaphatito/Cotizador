from __future__ import annotations

from sqlModels.db import connect, ensure_schema, tx
from sqlModels.quotes_repo import get_quote_header, insert_quote, list_quotes
from sqlModels.schema import SCHEMA_VERSION
from sqlModels.settings_repo import set_setting


def _insert_quote(con, **context) -> int:
    return insert_quote(
        con,
        country_code="PE",
        quote_no="PE-007-0000001",
        created_at="2026-08-07T10:00:00",
        cliente="Cliente Prueba",
        cedula="12345678",
        telefono="900000001",
        tipo_documento="DNI",
        currency_shown="PEN",
        tasa_shown=None,
        subtotal_bruto_base=20,
        descuento_total_base=0,
        total_neto_base=20,
        subtotal_bruto_shown=20,
        descuento_total_shown=0,
        total_neto_shown=20,
        pdf_path="quote.pdf",
        items_base=[],
        items_shown=[],
        **context,
    )


def test_insert_load_and_list_preserve_explicit_quote_context(tmp_path):
    con = connect(str(tmp_path / "quote_context.sqlite3"))
    ensure_schema(con)
    try:
        with tx(con):
            quote_id = _insert_quote(
                con,
                company_type="EF PERFUMES",
                base_currency="PEN",
                cotizador_username="operador.pe",
                id_cotizador="007",
                chatbot=True,
            )

        header = get_quote_header(con, quote_id)
        rows, total = list_quotes(con)

        assert total == 1
        assert {
            "country_code": header["country_code"],
            "company_type": header["company_type"],
            "base_currency": header["base_currency"],
            "cotizador_username": header["cotizador_username"],
            "id_cotizador": header["id_cotizador"],
        } == {
            "country_code": "PE",
            "company_type": "EF PERFUMES",
            "base_currency": "PEN",
            "cotizador_username": "operador.pe",
            "id_cotizador": "007",
        }
        assert rows[0]["country_code"] == "PE"
        assert rows[0]["company_type"] == "EF PERFUMES"
        assert rows[0]["base_currency"] == "PEN"
        assert rows[0]["cotizador_username"] == "operador.pe"
        assert rows[0]["id_cotizador"] == "007"
        assert header["quote_context_version"] == 1
        assert header["chatbot"] == 1
    finally:
        con.close()


def test_list_quotes_sorts_all_matches_before_paginating(tmp_path):
    con = connect(str(tmp_path / "quote_history_sort.sqlite3"))
    ensure_schema(con)
    try:
        quote_rows = [
            (
                "PE",
                f"PE-007-{index:07d}",
                f"2026-08-07T10:{index % 60:02d}:00",
                "PEN",
                float(54 - index),
                index % 2,
                f"quote-{index}.pdf",
            )
            for index in range(55)
        ]
        with tx(con):
            con.executemany(
                """
                INSERT INTO quotes(
                    country_code, quote_no, created_at, currency_shown,
                    total_neto_shown, chatbot, pdf_path
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                quote_rows,
            )

        first_page, total = list_quotes(
            con,
            limit=50,
            offset=0,
            sort_by="total",
            sort_desc=False,
        )
        second_page, _ = list_quotes(
            con,
            limit=50,
            offset=50,
            sort_by="total",
            sort_desc=False,
        )

        assert total == 55
        assert [row["total_shown"] for row in first_page] == [float(i) for i in range(50)]
        assert [row["total_shown"] for row in second_page] == [float(i) for i in range(50, 55)]
        assert all("chatbot" in row for row in first_page)
    finally:
        con.close()


def test_list_quotes_search_matches_visible_quote_type(tmp_path):
    con = connect(str(tmp_path / "quote_history_type_search.sqlite3"))
    ensure_schema(con)
    try:
        with tx(con):
            con.executemany(
                """
                INSERT INTO quotes(
                    country_code, quote_no, created_at, currency_shown,
                    chatbot, pdf_path
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    ("PE", "PE-007-0000001", "2026-08-07T10:00:00", "PEN", 1, "web.pdf"),
                    ("PE", "PE-007-0000002", "2026-08-07T10:01:00", "PEN", 0, "organico.pdf"),
                ],
            )

        web_rows, web_total = list_quotes(con, search_text="Web")
        organic_rows, organic_total = list_quotes(con, search_text="Organico")

        assert web_total == 1
        assert web_rows[0]["chatbot"] == 1
        assert organic_total == 1
        assert organic_rows[0]["chatbot"] == 0
    finally:
        con.close()


def test_legacy_insert_signature_derives_context_from_settings_and_quote_code(tmp_path):
    con = connect(str(tmp_path / "quote_context_defaults.sqlite3"))
    ensure_schema(con)
    try:
        with tx(con):
            set_setting(con, "company_type", "LA CASA DEL PERFUME")
            set_setting(con, "username", "operador.local")
            set_setting(con, "store_id", "999")
            quote_id = _insert_quote(con)

        header = get_quote_header(con, quote_id)

        assert header["company_type"] == "LA CASA DEL PERFUME"
        assert header["base_currency"] == "PEN"
        assert header["cotizador_username"] == "operador.local"
        assert header["id_cotizador"] == "007"
    finally:
        con.close()


def test_migration_39_backfills_context_without_overwriting_quote_identity(tmp_path):
    con = connect(str(tmp_path / "upgrade_v38_context.sqlite3"))
    try:
        con.executescript(
            """
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO meta(key, value) VALUES('schema_version', '38');

            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
            INSERT INTO settings(key, value) VALUES
                ('company_type', 'EF PERFUMES'),
                ('username', 'usuario.migrado'),
                ('store_id', '999');

            CREATE TABLE quotes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                country_code TEXT NOT NULL,
                quote_no TEXT NOT NULL,
                quote_no_status TEXT NOT NULL DEFAULT 'confirmed',
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
                deleted_at TEXT
            );
            INSERT INTO quotes(country_code, quote_no, created_at, currency_shown, pdf_path)
            VALUES
                ('PE', 'PE-007-0000001', '2026-08-07T10:00:00', 'USD', 'pe.pdf'),
                ('BO', 'BO-0000002', '2026-08-07T10:01:00', 'USD', 'bo.pdf');
            """
        )
        con.commit()

        ensure_schema(con)

        version = con.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        rows = con.execute(
            """
            SELECT
                country_code, company_type, base_currency,
                cotizador_username, id_cotizador, quote_context_version
            FROM quotes
            ORDER BY id
            """
        ).fetchall()
        assert str(version["value"]) == str(SCHEMA_VERSION)
        assert tuple(rows[0]) == (
            "PE",
            "EF PERFUMES",
            "PEN",
            "usuario.migrado",
            "007",
            0,
        )
        assert tuple(rows[1]) == (
            "BO",
            "EF PERFUMES",
            "BOB",
            "usuario.migrado",
            "999",
            0,
        )
    finally:
        con.close()
