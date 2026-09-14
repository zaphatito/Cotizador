import copy
import json
import sqlite3
import unittest

from sqlModels import quote_sync_repo as repo
from src.server_identity import validate_functional_identity, validate_server_identity_pair


def full_snapshot(number=1, installation='001'):
    h = dict(country_code='PE', company_type='LA CASA DEL PERFUME',
        quote_no=f'PE-{installation}-{number:07d}', cotizador_username='TESTUSER',
        id_cotizador=installation, created_at='2026-09-07T10:00:00-05:00',
        cliente='Cliente de prueba', cedula='12345678', tipo_documento='DNI',
        telefono='000000000', direccion='Prueba', email='test@example.invalid',
        estado='', metodo_pago='', chatbot=False, base_currency='PEN',
        currency_shown='USD', tasa_shown=3.8, subtotal_bruto_base=38.,
        descuento_total_base=0., total_neto_base=38., subtotal_bruto_shown=10.,
        descuento_total_shown=0., total_neto_shown=10.)
    common = dict(codigo='SERV-TEST', producto='Servicio prueba', categoria='SERVICIOS',
                  tipo_prod='serv', fragancia='', observacion='Observación histórica', cantidad=1.)
    return dict(version=1, header=h,
        items_base=[dict(common, precio=38., total=38., factor_total=1., subtotal_base=38.,
                         descuento_mode=None, descuento_pct=0., descuento_monto=0.,
                         precio_override=38., precio_tier=None, id_precioventa=4)],
        items_shown=[dict(common, precio=10., total=10., subtotal=10., descuento=0.)])


class FullSQLiteHistoryTests(unittest.TestCase):
    def test_local_quote_and_exact_outbox_rollback_together(self):
        from unittest.mock import patch
        from sqlModels.db import ensure_schema
        from sqlModels.quotes_repo import insert_quote
        con = sqlite3.connect(':memory:')
        con.row_factory = sqlite3.Row
        try:
            ensure_schema(con)
            capabilities = dict(enabled=True, owner_id='9', username='TESTUSER',
                scopes=[dict(country_code='PE', company_type='LA CASA DEL PERFUME')])
            with con:
                con.execute('INSERT INTO settings VALUES (?,?)', ('shared_quote_sync_capabilities', repo.encode(capabilities)))
                con.execute('INSERT INTO settings VALUES (?,?)', ('cotizador_pid', 'test-installation-001'))
            snapshot = full_snapshot()
            header = dict(snapshot['header']); header.pop('estado')
            with patch('src.quote_sync_adapter.projection', return_value=None):
                with self.assertRaises(RuntimeError), con:
                    insert_quote(con, **header, pdf_path='', items_base=snapshot['items_base'], items_shown=snapshot['items_shown'])
                    self.assertEqual(con.execute('SELECT count(*) FROM quote_sync_outbox').fetchone()[0], 1)
                    raise RuntimeError('Save aborted')
            self.assertEqual(con.execute('SELECT count(*) FROM quotes').fetchone()[0], 0)
            self.assertEqual(con.execute('SELECT count(*) FROM quote_sync_outbox').fetchone()[0], 0)
        finally:
            con.close()

    def test_download_preserves_client_services_and_currency_without_local_files(self):
        from sqlModels.db import ensure_schema
        from sqlModels.quotes_repo import get_quote_header, get_quote_items, list_quotes
        from src.quote_sync_adapter import materialize
        con = sqlite3.connect(':memory:')
        con.row_factory = sqlite3.Row
        try:
            ensure_schema(con)
            remote = dict(quote_uuid='test-uuid', owner_id='9', country_code='PE',
                company_type='LA CASA DEL PERFUME', origin_pid='origin-A', revision='1',
                snapshot=full_snapshot(), deleted_at=None, completeness='complete')
            with con:
                repo.apply_page(con, owner_id='9', country_code='PE',
                    company_type='LA CASA DEL PERFUME', page=dict(events=[remote], next_cursor='1'),
                    materialize=materialize)
            qid = repo.document(con, 'test-uuid')['quote_id']
            header = get_quote_header(con, qid)
            self.assertEqual(header['currency_shown'], 'USD')
            self.assertEqual(header['id_cotizador'], '001')
            self.assertEqual(header['pdf_path'], '')
            listed, total = list_quotes(con, search_text='Cliente de prueba')
            self.assertEqual(total, 1)
            self.assertEqual(listed[0]['cliente'], 'Cliente de prueba')
            con.execute("UPDATE clients SET nombre='Nuevo nombre'")
            self.assertEqual(get_quote_header(con, qid)['cliente'], 'Cliente de prueba')
            base, shown = get_quote_items(con, qid)
            self.assertEqual(base[0]['tipo_prod'], 'serv')
            self.assertEqual(base[0]['precio'], 38.)
            self.assertEqual(shown[0]['precio'], 10.)
            with con:
                repo.apply_page(con, owner_id='9', country_code='PE',
                    company_type='LA CASA DEL PERFUME', page=dict(events=[remote], next_cursor='1'),
                    materialize=materialize)
            self.assertEqual(con.execute('SELECT count(*) FROM quotes').fetchone()[0], 1)
        finally:
            con.close()


class SharedQuoteSyncTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(':memory:')
        self.con.execute('PRAGMA foreign_keys=ON')
        self.con.execute('CREATE TABLE quotes(id INTEGER PRIMARY KEY)')
        self.con.execute('INSERT INTO quotes VALUES (1)')
        repo.install(self.con)
        repo.install(self.con)
        self.snapshot = dict(version=1, header=dict(
            country_code='PE', company_type='LA CASA DEL PERFUME',
            quote_no='PE-001-0000001', estado='', metodo_pago='', chatbot=False),
            items_base=[dict(precio=3.25, tipo_prod='serv')], items_shown=[dict(precio=12.35)])
        self.uid = repo.register(self.con, quote_id=1, owner_id='9', origin_pid='A',
                                 snapshot=self.snapshot)
        self.con.commit()

    def tearDown(self):
        self.con.close()

    def remote(self, revision='1'):
        return dict(quote_uuid=self.uid, owner_id='9', country_code='PE',
                    company_type='LA CASA DEL PERFUME', origin_pid='A',
                    revision=revision, snapshot=copy.deepcopy(self.snapshot),
                    deleted_at=None, completeness='complete')

    def test_reject_technical_variants_and_inversion(self):
        for username, installation in [('cotizador pe 2', '001'),
                                       ('COTIZADOR_PE_2', '001'), ('', '001'),
                                       ('001', 'SAMUEL'), ('x' * 101, '001')]:
            with self.subTest(username=username), self.assertRaises(ValueError):
                validate_functional_identity(username, installation)
        with self.assertRaises(ValueError):
            validate_server_identity_pair('cotizador pe 2', '001')
        self.assertFalse(validate_server_identity_pair('', ''))

    def test_retry_preserves_exact_payload_after_later_edit(self):
        sent = repo.freeze(self.con, self.uid, pid='A')
        repo.local_metadata(self.con, 1, pago='EFECTIVO')
        self.assertEqual(sent['payload'], repo.freeze(self.con, self.uid, pid='A')['payload'])
        repo.acknowledge(self.con, sent['mutation_id'], self.remote())
        pending = repo.freeze(self.con, self.uid, pid='A')
        body = json.loads(pending['payload'])
        self.assertEqual(body['metadata']['pago'], 'EFECTIVO')
        self.assertEqual(body['base_revision'], '1')
        self.assertNotEqual(sent['mutation_id'], pending['mutation_id'])

    def test_ack_adopts_uuid_without_losing_edits(self):
        sent = repo.freeze(self.con, self.uid, pid='A')
        repo.local_metadata(self.con, 1, chatbot=True)
        remote = self.remote()
        remote['quote_uuid'] = 'central-uuid'
        repo.acknowledge(self.con, sent['mutation_id'], remote)
        self.assertIsNone(repo.document(self.con, self.uid))
        row = repo.document(self.con, 'central-uuid')
        self.assertEqual(row['quote_id'], 1)
        self.assertTrue(json.loads(row['snapshot'])['header']['chatbot'])

    def test_page_failure_rolls_back_cursor_and_documents(self):
        self.con.execute('DELETE FROM quote_sync_document')
        self.con.commit()
        page = dict(events=[self.remote(), dict(self.remote(), quote_uuid='second')],
                    next_cursor='next')
        count = 0
        def materialize(con, remote, quote_id):
            nonlocal count
            count += 1
            if count == 2:
                raise RuntimeError('Simulated shutdown')
            return 1
        with self.assertRaises(RuntimeError), self.con:
            repo.apply_page(self.con, owner_id='9', country_code='PE',
                            company_type='LA CASA DEL PERFUME', page=page,
                            materialize=materialize)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM quote_sync_document').fetchone()[0], 0)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM quote_sync_cursor').fetchone()[0], 0)

    def test_conflict_preserves_local_proposal_and_requires_resolution(self):
        sent = repo.freeze(self.con, self.uid, pid='A')
        repo.acknowledge(self.con, sent['mutation_id'], self.remote())
        repo.local_metadata(self.con, 1, pago='EFECTIVO')
        remote = self.remote('2')
        remote['snapshot']['header']['estado'] = 'PAGADO'
        repo.apply_page(self.con, owner_id='9', country_code='PE',
                        company_type='LA CASA DEL PERFUME',
                        page=dict(events=[remote], next_cursor='2'),
                        materialize=lambda *args: self.fail('Must not overwrite pending work'))
        self.assertIsNone(repo.freeze(self.con, self.uid, pid='A'))
        repo.resolve(self.con, self.uid, reapply=True, materialize=lambda *args: 1)
        body = json.loads(repo.freeze(self.con, self.uid, pid='A')['payload'])
        self.assertEqual(body['base_revision'], '2')
        self.assertEqual(body['metadata']['pago'], 'EFECTIVO')
        self.assertEqual(json.loads(repo.document(self.con, self.uid)['snapshot'])['header']['estado'], 'PAGADO')

    def test_owner_mismatch_does_not_ack(self):
        sent = repo.freeze(self.con, self.uid, pid='A')
        with self.assertRaises(ValueError):
            repo.acknowledge(self.con, sent['mutation_id'], dict(self.remote(), owner_id='other'))
        self.assertEqual(repo.document(self.con, self.uid)['revision'], '0')

    def test_new_metadata_and_delete_rollback_together(self):
        with self.assertRaises(RuntimeError), self.con:
            repo.local_metadata(self.con, 1, pago='EFECTIVO', deleted_at='today')
            raise RuntimeError('Aborted local save')
        row = repo.document(self.con, self.uid)
        self.assertIsNone(row['deleted_at'])
        self.assertEqual(row['generation'], 1)


if __name__ == '__main__':
    unittest.main()
