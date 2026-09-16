import json
import sqlite3

import pytest

from sqlModels.db import ensure_schema
from sqlModels.quotes_repo import list_quotes
from src.quote_code import sync_quote_key


@pytest.mark.parametrize('country,prefix', [('BO', 'BOL'), ('BOL', 'BO'), ('BO', 'BO'), ('BOL', 'BOL')])
def test_sync_key_accepts_bolivia_aliases(country, prefix):
    assert sync_quote_key(f'{prefix}-001-0000007', country, '001') == '7'
    assert sync_quote_key(f'C-{prefix}-001-0000007', country, '001') == '7'


@pytest.mark.parametrize('code,country,store', [
    ('BOL-002-0000007', 'BO', '001'),
    ('PE-001-0000007', 'BO', '001'),
    ('BOL-001-0000007', 'PE', '001'),
    ('XX-001-0000007', 'BO', '001'),
])
def test_sync_key_rejects_other_country_or_store(code, country, store):
    assert sync_quote_key(code, country, store) is None


def test_history_shows_bolivia_aliases_without_relaxing_identity_filters():
    con = sqlite3.connect(':memory:')
    con.row_factory = sqlite3.Row
    try:
        ensure_schema(con)
        caps = dict(owner_id='9', username='TESTUSER', scopes=[
            dict(country_code='BO', company_type='LCDP')])
        con.execute('INSERT INTO settings VALUES (?, ?)',
                    ('shared_quote_sync_capabilities', json.dumps(caps)))
        con.execute("INSERT INTO settings VALUES ('store_id', '001')")
        cases = [
            ('BOL', 'BOL-001-0000001', 'TESTUSER', '001', None),
            ('BO', 'BOL-001-0000002', 'TESTUSER', '001', None),
            ('BO', 'BOL-001-0000003', 'OTHERUSER', '001', None),
            ('BO', 'BOL-002-0000004', 'TESTUSER', '002', None),
            ('BO', 'PE-001-0000005', 'TESTUSER', '001', None),
            ('BO', 'BOL-001-0000006', 'TESTUSER', '001', '2026-09-01'),
        ]
        for country, code, username, store, deleted in cases:
            con.execute('''INSERT INTO quotes
                (country_code, quote_no, cotizador_username, id_cotizador,
                 created_at, deleted_at, currency_shown, pdf_path)
                VALUES (?, ?, ?, ?, '2026-09-01T10:00:00-04:00', ?, 'BOB', '')''',
                (country, code, username, store, deleted))
        rows, total = list_quotes(con)
        assert total == 2
        assert {row['quote_no'] for row in rows} == {
            'BOL-001-0000001', 'BOL-001-0000002'}
    finally:
        con.close()
