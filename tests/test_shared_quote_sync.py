import copy
import json
import sqlite3
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

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


@pytest.fixture
def local_history(tmp_path):
    from sqlModels.db import connect, ensure_schema
    from sqlModels.quotes_repo import insert_quote
    from src.quote_sync_adapter import snapshot_for
    db_path = str(tmp_path / 'history.db')
    con = connect(db_path)
    try:
        ensure_schema(con)
        data = full_snapshot()
        header = dict(data['header'])
        header.pop('estado')
        with con:
            con.execute("INSERT INTO settings VALUES('store_id','001')")
            qid = insert_quote(con, **header,
                pdf_path='C-PE-001-0000001_Cliente_de_prueba.pdf',
                items_base=data['items_base'], items_shown=data['items_shown'])
        return db_path, qid, snapshot_for(con, qid)
    finally:
        con.close()


@pytest.mark.parametrize('changed', [False, True])
def test_ack_preserves_local_pdf_until_its_content_changes(local_history, changed):
    from sqlModels.db import connect
    from src.quote_sync_adapter import materialize
    db_path, qid, snapshot = local_history
    remote = dict(quote_uuid='server-quote', completeness='complete', snapshot=copy.deepcopy(snapshot))
    if changed:
        remote['snapshot']['header']['cliente'] = 'Otro cliente de prueba'
    con = connect(db_path)
    try:
        with con:
            materialize(con, remote, qid)
        path = con.execute('SELECT pdf_path FROM quotes WHERE id=?', (qid,)).fetchone()[0]
        assert path == ('' if changed else 'C-PE-001-0000001_Cliente_de_prueba.pdf')
    finally:
        con.close()


@pytest.mark.parametrize('changed', [False, True])
def test_actual_delivery_ack_keeps_or_invalidates_pdf_from_stored_content(local_history, monkeypatch, changed):
    from sqlModels.db import connect
    from src import quote_sync_adapter as adapter
    from src.quote_sync_service import QuoteSyncService
    db_path, qid, snapshot = local_history
    monkeypatch.setattr(adapter, 'projection', lambda _: None)
    adapter.inventory_batch(db_path, owner_id='9', username='TESTUSER',
        pid='test-installation-001', scopes=[('PE', 'LA CASA DEL PERFUME')])
    def mutate(payload):
        remote = dict(payload, snapshot=copy.deepcopy(snapshot), owner_id='9', revision='1',
                      completeness='complete', deleted_at=None)
        if changed:
            remote['snapshot']['header']['cliente'] = 'Nombre conciliado'
        return remote
    service = QuoteSyncService(connect=lambda: connect(db_path),
        transport=SimpleNamespace(mutate=mutate, changes=lambda _: dict(events=[], next_cursor='next')),
        owner_id='9', pid='test-installation-001', scopes=[('PE', 'LA CASA DEL PERFUME')],
        materialize=adapter.materialize)
    assert service.cycle()['sent'] == 1
    con = connect(db_path)
    try:
        path = con.execute('SELECT pdf_path FROM quotes WHERE id=?', (qid,)).fetchone()[0]
        assert path == ('' if changed else 'C-PE-001-0000001_Cliente_de_prueba.pdf')
        assert con.execute('SELECT count(*) FROM quote_sync_outbox').fetchone()[0] == 0
    finally:
        con.close()


@pytest.mark.parametrize('old_name', ['', 'cotizacion_3475.pdf', 'C-PE-001-0000001_3475.pdf'])
def test_regenerated_pdf_uses_client_name_even_without_a_local_file(local_history, monkeypatch, tmp_path, old_name, qapp):
    from sqlModels.db import connect
    from src.widgets_parts import quote_history_dialog as ui
    db_path, qid, _ = local_history
    con = connect(db_path)
    try:
        with con:
            con.execute('UPDATE quotes SET pdf_path=? WHERE id=?', (old_name, qid))
    finally:
        con.close()
    monkeypatch.setattr(ui, 'COTIZACIONES_DIR', str(tmp_path))
    monkeypatch.setattr(ui, 'resolve_pdf_path_portable', lambda p: str(tmp_path / p) if p else '')
    rendered = []
    def generate(data, **kwargs):
        rendered.append(data)
        Path(kwargs['out_path']).write_bytes(b'isolated-pdf-output')
    monkeypatch.setattr(ui, 'generar_pdf', generate)
    code, path = ui.QuoteHistoryWindow._regen_pdf_overwrite_for_quote_id(
        SimpleNamespace(_db_path=db_path), qid)
    assert Path(path).name == 'C-PE-001-0000001_Cliente_de_prueba.pdf'
    assert code == 'PE-001-0000001'
    assert rendered[0]['cliente'] == 'Cliente de prueba'
    assert rendered[0]['fecha'].isoformat() == '2026-09-07T10:00:00-05:00'


def test_pdf_regeneration_rejects_missing_historical_date(local_history, monkeypatch, qapp):
    from sqlModels.db import connect
    from src.widgets_parts import quote_history_dialog as ui
    db_path, qid, _ = local_history
    con = connect(db_path)
    try:
        with con:
            con.execute("UPDATE quotes SET created_at='' WHERE id=?", (qid,))
    finally:
        con.close()
    generate = Mock()
    monkeypatch.setattr(ui, 'generar_pdf', generate)
    with pytest.raises(ValueError, match='fecha de emisión'):
        ui.QuoteHistoryWindow._regen_pdf_overwrite_for_quote_id(SimpleNamespace(_db_path=db_path), qid)
    generate.assert_not_called()


def test_shared_delivery_resumes_after_empty_scopes_and_does_not_duplicate_upload(local_history, monkeypatch):
    from src import quote_sync_adapter as adapter
    from src.api import controller, presupuesto_client as pc
    from src.server_identity import ApiIdentity
    db_path, _, _ = local_history
    capabilities = dict(enabled=True, owner_id='9', username='TESTUSER', scopes=[])
    verification = dict(status='ACTIVE', pid='test-installation-001', sync=capabilities)
    login = Mock(return_value=('test-token', None))
    monkeypatch.setattr(pc, '_login_api', login)
    monkeypatch.setattr(pc, '_load_api_identity', lambda: ApiIdentity(1, 'api-test', 'TESTUSER',
        'PERU', 'LA CASA DEL PERFUME', '001', False))
    calls = []
    def post(case, *, json_data, **_):
        calls.append(case)
        if case == 8:
            document = dict(json_data, revision='1', owner_id='9', completeness='complete',
                            deleted_at=None, id_presupuesto='55')
            return SimpleNamespace(data={'data': document})
        assert case == 9
        return SimpleNamespace(data={'data': {'events': [], 'next_cursor': 'confirmed-cursor'}})
    monkeypatch.setattr(controller, 'post', post)
    paused = adapter.run_shared_cycle(db_path, verification)
    assert paused.get('paused') is True
    assert paused.get('message')
    assert not login.called
    capabilities['scopes'] = [dict(country_code='PE', company_type='LA CASA DEL PERFUME')]
    assert adapter.run_shared_cycle(db_path, verification)['sent'] == 1
    assert adapter.run_shared_cycle(db_path, verification)['sent'] == 0
    assert calls.count(8) == 1
    assert calls.count(9) == 2


def add_history_quote(con, number, **overrides):
    from sqlModels.quotes_repo import insert_quote
    data = full_snapshot(number)
    header = dict(data['header'], **overrides)
    header.pop('estado')
    return insert_quote(con, **header, pdf_path='',
        items_base=data['items_base'], items_shown=data['items_shown'])


def downloaded(snapshot, revision='1', uuid='server-quote'):
    return dict(quote_uuid=uuid, owner_id='9', country_code='PE',
        company_type='LA CASA DEL PERFUME', origin_pid='another-installation',
        revision=revision, snapshot=copy.deepcopy(snapshot), deleted_at=None,
        completeness='complete')


@pytest.mark.parametrize('code', ['PE-001-0000001', '001-0000001', '0000001', 'PE-0000001'])
def test_download_links_existing_history_before_backfill_without_duplicate(local_history, code):
    from sqlModels.db import connect
    from src.quote_sync_adapter import materialize
    db_path, qid, snapshot = local_history
    con = connect(db_path)
    try:
        with con:
            con.execute('UPDATE quotes SET quote_no=?,api_sent_at=? WHERE id=?',
                        (code, '2026-09-07T10:01:00', qid))
            repo.apply_page(con, owner_id='9', country_code='PE', company_type='LA CASA DEL PERFUME',
                page=dict(events=[downloaded(snapshot)], next_cursor='next'), materialize=materialize)
        assert con.execute('SELECT count(*) FROM quotes').fetchone()[0] == 1
        assert repo.document(con, 'server-quote')['quote_id'] == qid
        assert con.execute('SELECT created_at FROM quotes WHERE id=?', (qid,)).fetchone()[0] == snapshot['header']['created_at']
    finally:
        con.close()


def test_download_before_lost_ack_keeps_frozen_upload_and_one_local_row(local_history):
    from sqlModels.db import connect
    from src.quote_sync_adapter import materialize
    db_path, qid, snapshot = local_history
    con = connect(db_path)
    try:
        with con:
            uid = repo.register(con, quote_id=qid, owner_id='9', origin_pid='local', snapshot=snapshot)
            sent = repo.freeze(con, uid, pid='local')
            repo.local_metadata(con, qid, pago='EFECTIVO')
            remote = downloaded(snapshot)
            repo.apply_page(con, owner_id='9', country_code='PE', company_type='LA CASA DEL PERFUME',
                page=dict(events=[remote], next_cursor='next'), materialize=materialize)
        assert con.execute('SELECT count(*) FROM quotes').fetchone()[0] == 1
        assert repo.freeze(con, uid, pid='local')['payload'] == sent['payload']
        with con:
            repo.acknowledge(con, sent['mutation_id'], remote)
        saved = repo.document(con, remote['quote_uuid'])
        assert saved['quote_id'] == qid
        assert json.loads(saved['snapshot'])['header']['metodo_pago'] == 'EFECTIVO'
        assert con.execute('SELECT count(*) FROM quote_sync_conflict').fetchone()[0] == 0
    finally:
        con.close()


def test_ack_cannot_replace_original_emission_with_retry_time(local_history):
    from sqlModels.db import connect
    db_path, qid, snapshot = local_history
    con = connect(db_path)
    try:
        with con:
            uid = repo.register(con, quote_id=qid, owner_id='9', origin_pid='local', snapshot=snapshot)
            sent = repo.freeze(con, uid, pid='local')
            remote = downloaded(snapshot, uuid=uid)
            remote['snapshot']['header']['created_at'] = '2026-09-15T12:00:00-05:00'
            repo.acknowledge(con, sent['mutation_id'], remote)
        assert json.loads(repo.document(con, uid)['snapshot'])['header']['created_at'] == snapshot['header']['created_at']
        assert con.execute('SELECT count(*) FROM quote_sync_conflict').fetchone()[0] == 1
    finally:
        con.close()


def test_sync_reports_date_conflict_without_claiming_delivery(local_history):
    from sqlModels.db import connect
    from src.quote_sync_adapter import materialize
    from src.quote_sync_service import QuoteSyncService
    db_path, qid, snapshot = local_history
    con = connect(db_path)
    try:
        with con:
            uid = repo.register(con, quote_id=qid, owner_id='9', origin_pid='local', snapshot=snapshot)
    finally:
        con.close()
    remote = downloaded(snapshot, uuid=uid)
    remote['snapshot']['header']['created_at'] = '2026-09-15T12:00:00-05:00'
    service = QuoteSyncService(connect=lambda: connect(db_path),
        transport=SimpleNamespace(mutate=lambda _: remote,
            changes=lambda _: dict(events=[], next_cursor='1')),
        owner_id='9', pid='local', scopes=[('PE', 'LA CASA DEL PERFUME')], materialize=materialize,
        functional_username='TESTUSER', id_cotizador='001')
    result = service.cycle()
    assert result['sent'] == 0
    assert result['conflicts'] == 1
    assert result['blocked'] == 1


@pytest.mark.parametrize('deleted', [False, True])
def test_download_adoption_preserves_pending_local_metadata_and_deletion(local_history, deleted):
    from sqlModels.db import connect
    from src.quote_sync_adapter import materialize
    db_path, qid, snapshot = local_history
    con = connect(db_path)
    stamp = '2026-09-08T11:00:00-05:00' if deleted else None
    try:
        with con:
            con.execute('UPDATE quotes SET metodo_pago=?,deleted_at=?,api_sent_at=? WHERE id=?',
                ('EFECTIVO', stamp, '2026-09-07T10:01:00' if deleted else None, qid))
            repo.apply_page(con, owner_id='9', country_code='PE', company_type='LA CASA DEL PERFUME',
                page=dict(events=[downloaded(snapshot)], next_cursor='1'), materialize=materialize)
        row = repo.document(con, 'server-quote')
        assert row['quote_id'] == qid
        assert row['deleted_at'] == stamp
        if not deleted:
            assert json.loads(row['snapshot'])['header']['metodo_pago'] == 'EFECTIVO'
        payload = json.loads(repo.freeze(con, 'server-quote', pid='local')['payload'])
        assert payload['operation'] == ('delete' if deleted else 'update_metadata')
        assert con.execute('SELECT deleted_at FROM quotes WHERE id=?', (qid,)).fetchone()[0] == stamp
        assert con.execute('SELECT count(*) FROM quotes').fetchone()[0] == 1
    finally:
        con.close()


@pytest.mark.parametrize('incoming', ['2026-09-07T15:00:00Z', '2026-09-08T10:00:00-05:00'])
def test_download_never_replaces_original_issue_timestamp(local_history, incoming):
    from sqlModels.db import connect
    from src.quote_sync_adapter import materialize
    db_path, qid, snapshot = local_history
    con = connect(db_path)
    try:
        with con:
            remote = downloaded(snapshot)
            repo.apply_page(con, owner_id='9', country_code='PE', company_type='LA CASA DEL PERFUME',
                page=dict(events=[remote], next_cursor='1'), materialize=materialize)
            remote['revision'] = '2'
            remote['snapshot']['header']['created_at'] = incoming
            repo.apply_page(con, owner_id='9', country_code='PE', company_type='LA CASA DEL PERFUME',
                page=dict(events=[remote], next_cursor='2'), materialize=materialize)
        assert con.execute('SELECT created_at FROM quotes WHERE id=?', (qid,)).fetchone()[0] == snapshot['header']['created_at']
        expected_conflicts = int(incoming.startswith('2026-09-08'))
        assert con.execute('SELECT count(*) FROM quote_sync_conflict').fetchone()[0] == expected_conflicts
        assert con.execute('SELECT count(*) FROM quotes').fetchone()[0] == 1
    finally:
        con.close()


def test_partial_event_then_complete_download_links_local_history(local_history):
    from sqlModels.db import connect
    from src.quote_sync_adapter import materialize
    db_path, qid, snapshot = local_history
    con = connect(db_path)
    try:
        with con:
            remote = downloaded(snapshot)
            remote['completeness'] = 'partial'
            remote['snapshot'] = dict(version=1, header={key: snapshot['header'][key] for key in
                ('country_code', 'company_type', 'quote_no', 'cotizador_username', 'id_cotizador')})
            repo.apply_page(con, owner_id='9', country_code='PE', company_type='LA CASA DEL PERFUME',
                page=dict(events=[remote], next_cursor='1'), materialize=materialize)
            assert con.execute('SELECT count(*) FROM quotes').fetchone()[0] == 1
            repo.apply_page(con, owner_id='9', country_code='PE', company_type='LA CASA DEL PERFUME',
                page=dict(events=[downloaded(snapshot, revision='2')], next_cursor='2'), materialize=materialize)
        assert repo.document(con, 'server-quote')['quote_id'] == qid
        assert con.execute('SELECT count(*) FROM quote_sync_document').fetchone()[0] == 1
        assert con.execute('SELECT count(*) FROM quote_sync_conflict').fetchone()[0] == 0
    finally:
        con.close()


def test_ack_adopts_previously_downloaded_partial_stub(local_history):
    from sqlModels.db import connect
    from src.quote_sync_adapter import materialize
    db_path, qid, snapshot = local_history
    con = connect(db_path)
    try:
        with con:
            con.execute("UPDATE quotes SET quote_no_status='provisional' WHERE id=?", (qid,))
            partial = dict(downloaded(snapshot), completeness='partial')
            repo.apply_page(con, owner_id='9', country_code='PE', company_type='LA CASA DEL PERFUME',
                page=dict(events=[partial], next_cursor='1'), materialize=materialize)
            assert repo.document(con, 'server-quote')['quote_id'] is None
            con.execute("UPDATE quotes SET quote_no_status='confirmed' WHERE id=?", (qid,))
            uid = repo.register(con, quote_id=qid, owner_id='9', origin_pid='local', snapshot=snapshot)
            sent = repo.freeze(con, uid, pid='local')
            repo.acknowledge(con, sent['mutation_id'], downloaded(snapshot, revision='2'))
        assert repo.document(con, 'server-quote')['quote_id'] == qid
        assert con.execute('SELECT count(*) FROM quote_sync_document').fetchone()[0] == 1
        assert con.execute('SELECT count(*) FROM quote_sync_outbox').fetchone()[0] == 0
        assert con.execute('SELECT count(*) FROM quotes').fetchone()[0] == 1
    finally:
        con.close()


def test_existing_ambiguous_duplicates_do_not_block_other_downloads(local_history):
    from sqlModels.db import connect
    from src.quote_sync_adapter import materialize
    db_path, qid, snapshot = local_history
    con = connect(db_path)
    try:
        with con:
            duplicate_id = add_history_quote(con, 1, created_at='2026-09-08T11:00:00-05:00')
            other = downloaded(full_snapshot(2), uuid='another-quote')
            repo.apply_page(con, owner_id='9', country_code='PE', company_type='LA CASA DEL PERFUME',
                page=dict(events=[downloaded(snapshot), other], next_cursor='2'), materialize=materialize)
        assert con.execute('SELECT count(*) FROM quotes').fetchone()[0] == 3
        assert repo.document(con, 'another-quote')['quote_id'] not in (qid, duplicate_id)
        assert con.execute('SELECT count(*) FROM quote_sync_conflict').fetchone()[0] == 2
        assert con.execute('SELECT cursor FROM quote_sync_cursor').fetchone()[0] == '2'
        assert con.execute('SELECT created_at FROM quotes WHERE id=?', (duplicate_id,)).fetchone()[0] == '2026-09-08T11:00:00-05:00'
    finally:
        con.close()


def test_history_filters_unregistered_quotes_by_configured_user_and_store(local_history):
    from sqlModels.db import connect
    from sqlModels.quotes_repo import list_quotes
    db_path, qid, _ = local_history
    con = connect(db_path)
    try:
        with con:
            add_history_quote(con, 2, id_cotizador='004', quote_no='PE-004-0000002')
            add_history_quote(con, 3, cotizador_username='OTHER')
            add_history_quote(con, 4, quote_no='PE-004-0000004')
            caps = dict(enabled=True, owner_id='9', username='TESTUSER',
                scopes=[dict(country_code='PE', company_type='LA CASA DEL PERFUME')])
            con.execute('INSERT INTO settings VALUES (?,?)', ('shared_quote_sync_capabilities', repo.encode(caps)))
        rows, total = list_quotes(con)
        assert total == 1
        assert [row['id'] for row in rows] == [qid]
    finally:
        con.close()


@pytest.mark.parametrize('value', [None, '', 'fecha inválida', '0', '2026-09-07T10:00:00XYZ'])
def test_invalid_emission_is_not_replaced_by_current_time(value):
    from src.api.presupuesto_client import _normalize_issue_timestamp
    with pytest.raises(ValueError, match='fecha de emisión'):
        _normalize_issue_timestamp(value)


def test_legacy_emission_preserves_its_day_and_time():
    import datetime
    from src.api.presupuesto_client import _normalize_issue_timestamp
    expected = int(datetime.datetime(2026, 9, 7, 10, 23, 45).timestamp() * 1000)
    assert _normalize_issue_timestamp('07/09/2026 10:23:45') == expected


def test_download_omits_other_store_even_if_server_owner_id_matches(local_history):
    from sqlModels.db import connect
    from src.quote_sync_adapter import materialize
    from src.quote_sync_service import QuoteSyncService
    db_path, _, snapshot = local_history
    other = downloaded(full_snapshot(2, installation='004'), uuid='other-store')
    own = downloaded(snapshot)
    service = QuoteSyncService(connect=lambda: connect(db_path),
        transport=SimpleNamespace(changes=lambda _: dict(events=[other, own], next_cursor='2')),
        owner_id='9', pid='local', scopes=[('PE', 'LA CASA DEL PERFUME')], materialize=materialize,
        functional_username='TESTUSER', id_cotizador='001')
    result = service.cycle()
    con = connect(db_path)
    try:
        assert result['received'] == 1
        assert repo.document(con, 'other-store') is None
        assert con.execute('SELECT count(*) FROM quotes').fetchone()[0] == 1
        assert con.execute('SELECT cursor FROM quote_sync_cursor').fetchone()[0] == '2'
    finally:
        con.close()


@pytest.mark.parametrize('server_accepts', [True, False])
def test_api_upgrade_retries_code_rejection_once_with_the_same_payload(local_history, monkeypatch, server_accepts):
    from sqlModels.db import connect
    from src import quote_sync_adapter as adapter
    from src.api import controller, presupuesto_client as pc
    from src.api.generic_controller import ApiRequestError, ApiResponse
    from src.server_identity import ApiIdentity
    db_path, qid, _ = local_history
    monkeypatch.setattr(adapter, 'projection', lambda _: None)
    monkeypatch.setattr(pc, '_login_api', lambda **_: ('test-token', None))
    monkeypatch.setattr(pc, '_load_api_identity', lambda: ApiIdentity(1, 'api-test', 'TESTUSER',
        'PERU', 'LA CASA DEL PERFUME', '001', False))
    capabilities = dict(enabled=True, owner_id='9', username='TESTUSER',
        scopes=[dict(country_code='PE', company_type='LA CASA DEL PERFUME')])
    verification = dict(status='ACTIVE', pid='test-installation-001', sync=capabilities)
    con = connect(db_path)
    try:
        with con:
            con.execute("UPDATE quotes SET quote_no='PE-1' WHERE id=?", (qid,))
        adapter.inventory_batch(db_path, owner_id='9', username='TESTUSER',
            pid=verification['pid'], scopes=[('PE', 'LA CASA DEL PERFUME')])
        with con:
            con.execute("UPDATE quote_sync_outbox SET blocked=1,attempts=1,error='Código de cotización inválido.'")
            frozen = con.execute('SELECT payload FROM quote_sync_outbox').fetchone()[0]
            repo.local_metadata(con, qid, pago='EFECTIVO')
    finally:
        con.close()
    sent = []
    def post(case, *, json_data, **_):
        if case == 8:
            sent.append(json_data)
            if not server_accepts:
                response = ApiResponse(status_code=422, data=dict(
                    code='INVALID_QUOTE_CODE', message='Código de cotización inválido.'),
                    text='', headers={}, ok=False, method='POST', case=8,
                    url='https://example.invalid/sync', elapsed_ms=1)
                raise ApiRequestError('rechazado', response=response)
            return SimpleNamespace(data={'data': dict(json_data, snapshot=json.loads(frozen)['snapshot'],
                revision='1', owner_id='9', completeness='complete', deleted_at=None)})
        return SimpleNamespace(data={'data': dict(events=[], next_cursor='cursor')})
    monkeypatch.setattr(controller, 'post', post)
    before = adapter.run_shared_cycle(db_path, verification)
    assert before['sent'] == 0
    assert before['blocked'] == 1
    assert sent == []
    capabilities['quote_code_version'] = 2
    retried = adapter.run_shared_cycle(db_path, verification)
    assert retried['sent'] == int(server_accepts)
    assert sent == [json.loads(frozen)]
    con = connect(db_path)
    try:
        row = con.execute('SELECT snapshot,revision FROM quote_sync_document WHERE quote_id=?', (qid,)).fetchone()
        assert json.loads(row[0])['header']['metodo_pago'] == 'EFECTIVO'
        assert json.loads(row[0])['header']['quote_no'] == 'PE-1'
        if not server_accepts:
            assert adapter.run_shared_cycle(db_path, verification)['blocked'] == 1
            assert len(sent) == 1
            assert con.execute('SELECT payload FROM quote_sync_outbox').fetchone()[0] == frozen
        else:
            assert row[1] == '1'
    finally:
        con.close()


def test_code_recovery_preserves_conflicts_other_errors_and_other_owners(local_history, monkeypatch):
    from sqlModels.db import connect, tx
    from src import quote_sync_adapter as adapter
    db_path, first_id, _ = local_history
    monkeypatch.setattr(adapter, 'projection', lambda _: None)
    con = connect(db_path)
    try:
        with con:
            for number in range(2, 6):
                add_history_quote(con, number)
        adapter.inventory_batch(db_path, owner_id='9', username='TESTUSER',
            pid='test-installation-001', scopes=[('PE', 'LA CASA DEL PERFUME')])
        ids = [r[0] for r in con.execute('SELECT quote_uuid FROM quote_sync_document ORDER BY quote_id')]
        with con:
            con.execute("UPDATE quote_sync_outbox SET blocked=1,retry_at=900,error='Código de cotización inválido.'")
            con.execute("UPDATE quote_sync_outbox SET error='Sin permiso' WHERE quote_uuid=?", (ids[1],))
            con.execute("UPDATE quote_sync_document SET owner_id='other' WHERE quote_uuid=?", (ids[2],))
            con.execute("UPDATE quote_sync_document SET country_code='PY' WHERE quote_uuid=?", (ids[3],))
            repo.conflict(con, ids[4], {}, 'Código de cotización inválido.')
        with pytest.raises(RuntimeError), tx(con, immediate=True):
            assert repo.requeue_code_rejections(con, owner_id='9', scopes=[('PE', 'LA CASA DEL PERFUME')]) == 1
            raise RuntimeError('interrupted recovery')
        assert all(r[0] == 1 for r in con.execute('SELECT blocked FROM quote_sync_outbox'))
        with tx(con, immediate=True):
            assert repo.requeue_code_rejections(con, owner_id='9', scopes=[('PE', 'LA CASA DEL PERFUME')]) == 1
        assert [r[0] for r in con.execute('SELECT quote_uuid FROM quote_sync_outbox WHERE blocked=0')] == [ids[0]]
    finally:
        con.close()


def test_new_unsent_quotes_go_before_already_sent_history(local_history, monkeypatch):
    from sqlModels.db import connect
    from src import quote_sync_adapter as adapter
    from src.quote_sync_service import QuoteSyncService
    db_path, old_id, _ = local_history
    monkeypatch.setattr(adapter, 'projection', lambda _: None)
    con = connect(db_path)
    try:
        with con:
            con.execute("UPDATE quotes SET api_sent_at='2026-09-14T10:00:00' WHERE id=?", (old_id,))
            new_id = add_history_quote(con, 2)
        adapter.inventory_batch(db_path, owner_id='9', username='TESTUSER',
            pid='test-installation-001', scopes=[('PE', 'LA CASA DEL PERFUME')])
    finally:
        con.close()
    sent = []
    def mutate(payload):
        sent.append(payload)
        return dict(payload, revision='1', owner_id='9', completeness='complete', deleted_at=None)
    service = QuoteSyncService(connect=lambda: connect(db_path),
        transport=SimpleNamespace(mutate=mutate, changes=lambda _: dict(events=[], next_cursor='cursor')),
        owner_id='9', pid='test-installation-001', scopes=[('PE', 'LA CASA DEL PERFUME')],
        materialize=adapter.materialize)
    assert service.cycle()['sent'] == 1
    assert sent[0]['snapshot']['header']['quote_no'] == 'PE-001-0000002'
    assert service.cycle()['sent'] == 1
    assert sent[1]['snapshot']['header']['quote_no'] == 'PE-001-0000001'


def test_recent_pending_quote_is_registered_before_old_history_backfill(local_history, monkeypatch):
    from sqlModels.db import connect
    from src import quote_sync_adapter as adapter
    db_path, _, _ = local_history
    monkeypatch.setattr(adapter, 'projection', lambda _: None)
    con = connect(db_path)
    try:
        with con:
            for number in range(2, 60):
                add_history_quote(con, number)
            con.execute("UPDATE quotes SET api_sent_at='2026-09-14T10:00:00'")
            recent = add_history_quote(con, 60)
            con.execute("UPDATE quotes SET api_error_message='El ámbito histórico no está autorizado.' WHERE id=?", (recent,))
        args = dict(owner_id='9', username='TESTUSER', pid='test-installation-001',
                    scopes=[('PE', 'LA CASA DEL PERFUME')])
        first = adapter.inventory_batch(db_path, **args)
        assert first == dict(registered=25, pending=0, more=True)
        assert con.execute('SELECT 1 FROM quote_sync_document WHERE quote_id=?', (recent,)).fetchone()
        assert con.execute('SELECT api_error_message FROM quotes WHERE id=?', (recent,)).fetchone()[0] == ''
        assert adapter.inventory_batch(db_path, **args)['registered'] == 25
        assert adapter.inventory_batch(db_path, **args)['registered'] == 10
        assert con.execute('SELECT count(*) FROM quote_sync_document').fetchone()[0] == 60
    finally:
        con.close()


def test_saving_one_quote_does_not_inventory_the_entire_history(local_history, monkeypatch):
    from sqlModels.db import connect
    from src import quote_sync_adapter as adapter
    db_path, old_id, _ = local_history
    monkeypatch.setattr(adapter, 'projection', lambda _: None)
    con = connect(db_path)
    try:
        info = dict(enabled=True, owner_id='9', username='TESTUSER',
            scopes=[dict(country_code='PE', company_type='LA CASA DEL PERFUME')])
        with con:
            con.execute('INSERT INTO settings VALUES (?,?)', ('shared_quote_sync_capabilities', repo.encode(info)))
            con.execute('INSERT INTO settings VALUES (?,?)', ('cotizador_pid', 'test-installation-001'))
            new_id = add_history_quote(con, 2)
            assert con.execute('SELECT quote_id FROM quote_sync_document').fetchone()[0] == new_id
            assert con.execute('SELECT count(*) FROM quote_sync_outbox').fetchone()[0] == 1
        assert con.execute('SELECT sync_uuid FROM quotes WHERE id=?', (old_id,)).fetchone()[0]
    finally:
        con.close()


def test_inventory_releases_each_quote_and_resumes_after_interruption(local_history, monkeypatch):
    from sqlModels.db import connect
    from src import quote_sync_adapter as adapter
    db_path, first_id, _ = local_history
    con = connect(db_path)
    try:
        with con:
            second_id = add_history_quote(con, 2)
            add_history_quote(con, 3)
            con.execute("UPDATE quotes SET api_sent_at='2026-09-14T10:00:00'")
        projection = Mock(side_effect=[None, OSError('Interrupted backfill')])
        monkeypatch.setattr(adapter, 'projection', projection)
        args = dict(owner_id='9', username='TESTUSER', pid='test-installation-001',
                    scopes=[('PE', 'LA CASA DEL PERFUME')])
        with pytest.raises(OSError, match='Interrupted'):
            adapter.inventory_batch(db_path, **args)
        assert [r[0] for r in con.execute('SELECT quote_id FROM quote_sync_document')] == [first_id]
        assert con.execute('SELECT count(*) FROM quote_sync_outbox').fetchone()[0] == 1
        # Another writer can delete while the next batch is not holding a transaction.
        from sqlModels.db import tx
        from sqlModels.quotes_repo import soft_delete_quote
        with tx(con, immediate=True):
            soft_delete_quote(con, second_id, '2026-09-14T15:30:00')
        monkeypatch.setattr(adapter, 'projection', lambda _: None)
        assert adapter.inventory_batch(db_path, **args)['registered'] == 2
        assert con.execute('SELECT count(*) FROM quote_sync_outbox').fetchone()[0] == 3
        deleted_payload = json.loads(con.execute('''SELECT o.payload FROM quote_sync_outbox o
            JOIN quote_sync_document d ON d.quote_uuid=o.quote_uuid WHERE d.quote_id=?''', (second_id,)).fetchone()[0])
        assert deleted_payload['operation'] == 'delete'
    finally:
        con.close()


def test_inventory_batches_progress_past_unmatched_authors(local_history, monkeypatch):
    from sqlModels.db import connect
    from src import quote_sync_adapter as adapter
    db_path, first_id, _ = local_history
    monkeypatch.setattr(adapter, 'projection', lambda _: None)
    con = connect(db_path)
    try:
        with con:
            con.execute("UPDATE quotes SET cotizador_username='OTHER' WHERE id=?", (first_id,))
            for number in range(2, 28):
                add_history_quote(con, number)
        args = dict(owner_id='9', username='TESTUSER', pid='test-installation-001',
                    scopes=[('PE', 'LA CASA DEL PERFUME')])
        assert adapter.inventory_batch(db_path, **args) == dict(registered=24, pending=1, more=True)
        assert adapter.inventory_batch(db_path, **args) == dict(registered=2, pending=0, more=False)
        assert con.execute('SELECT count(*) FROM quote_sync_document').fetchone()[0] == 26
        assert con.execute('SELECT count(*) FROM quotes').fetchone()[0] == 27
    finally:
        con.close()


def test_delete_serializes_with_another_sqlite_writer_and_closes_connection(local_history, monkeypatch, qapp):
    from sqlModels.db import connect
    from sqlModels.quotes_repo import soft_delete_quote
    from src import quote_sync_adapter as adapter
    from src.widgets_parts import quote_history_dialog as ui
    db_path, qid, _ = local_history
    monkeypatch.setattr(adapter, 'projection', lambda _: None)
    adapter.inventory_batch(db_path, owner_id='9', username='TESTUSER',
        pid='test-installation-001', scopes=[('PE', 'LA CASA DEL PERFUME')])
    opened = []
    def open_ui(path):
        opened.append(connect(path))
        return opened[-1]
    def competing_delete(con, quote_id, timestamp):
        # A sync commit between this read and the update used to invalidate the
        # deferred transaction, causing SQLITE_BUSY_SNAPSHOT immediately.
        con.execute('SELECT generation FROM quote_sync_document WHERE quote_id=?', (quote_id,)).fetchone()
        other = connect(db_path)
        try:
            other.execute('PRAGMA busy_timeout=0')
            try:
                with other:
                    other.execute("INSERT OR REPLACE INTO settings VALUES ('competing_sync', '1')")
            except sqlite3.OperationalError as exc:
                assert 'locked' in str(exc)
        finally:
            other.close()
        soft_delete_quote(con, quote_id, timestamp)
    monkeypatch.setattr(ui, 'connect', open_ui)
    monkeypatch.setattr(ui, 'soft_delete_quote', competing_delete)
    monkeypatch.setattr(ui.QMessageBox, 'question', lambda *_: ui.QMessageBox.Yes)
    error = Mock()
    monkeypatch.setattr(ui.QMessageBox, 'critical', error)
    window = SimpleNamespace(_db_path=db_path, _selected_quote_id=lambda: qid,
        _reload_first_page=Mock(), _wake_background_api_sync=Mock())
    ui.QuoteHistoryWindow._soft_delete(window)
    assert not error.called
    window._wake_background_api_sync.assert_called_once()
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        opened[0].execute('SELECT 1')
    con = connect(db_path)
    try:
        assert con.execute('SELECT deleted_at FROM quotes WHERE id=?', (qid,)).fetchone()[0]
        row = con.execute('SELECT deleted_at,generation FROM quote_sync_document WHERE quote_id=?', (qid,)).fetchone()
        assert row[0] and row[1] == 2
        with con:
            con.execute("INSERT OR REPLACE INTO settings VALUES ('competing_sync', '2')")
    finally:
        con.close()


def test_delete_refreshes_only_its_client_and_preserves_historical_name(local_history):
    from sqlModels.db import connect, tx
    from sqlModels.quotes_repo import soft_delete_quote, get_quote_header
    db_path, first_id, _ = local_history
    con = connect(db_path)
    try:
        with con:
            second_id = add_history_quote(con, 2)
            other_id = add_history_quote(con, 3, cedula='87654321', cliente='Otro cliente')
            client_id = con.execute('SELECT id_cliente FROM quotes WHERE id=?', (first_id,)).fetchone()[0]
            other_client = con.execute('SELECT id_cliente FROM quotes WHERE id=?', (other_id,)).fetchone()[0]
            con.execute("UPDATE clients SET updated_at='unchanged' WHERE id=?", (other_client,))
        with tx(con, immediate=True):
            soft_delete_quote(con, second_id, '2026-09-14T15:30:00')
        assert con.execute('SELECT source_quote_id FROM clients WHERE id=?', (client_id,)).fetchone()[0] == first_id
        assert con.execute('SELECT updated_at FROM clients WHERE id=?', (other_client,)).fetchone()[0] == 'unchanged'
        with tx(con, immediate=True):
            soft_delete_quote(con, first_id, '2026-09-14T15:31:00')
        assert get_quote_header(con, first_id)['cliente'] == 'Cliente de prueba'
        assert con.execute('SELECT count(*) FROM clients WHERE id=?', (other_client,)).fetchone()[0] == 1
    finally:
        con.close()


def test_generating_quote_saves_it_when_sync_tries_to_write(local_history, monkeypatch, qapp, tmp_path):
    from sqlModels.db import connect
    from sqlModels.quotes_repo import insert_quote
    from src.app_window_parts import pdf_actions as ui
    db_path, _, data = local_history
    entry = lambda text: SimpleNamespace(text=lambda: text)
    window = SimpleNamespace(entry_cliente=entry('Cliente nuevo'), entry_cedula=entry('23456789'),
        entry_telefono=entry('000000000'), entry_direccion=entry('Prueba'), entry_email=entry('a@example.invalid'),
        _validate_doc_phone_values=lambda *a, **kw: (True, '', 'DNI'), _confirm_quote_stock=lambda: True,
        items=data['items_base'], _build_items_for_pdf=lambda: data['items_shown'],
        _shown_totals_for_output=lambda _: dict(subtotal_bruto=10, descuento_total=0, total_general=10),
        _get_metodo_pago_actual=lambda: '', country_name='PERU', country_code='PE',
        company_type='LA CASA DEL PERFUME', base_currency='PEN', cotizador_username='TESTUSER',
        id_cotizador='001', _currency_context=lambda: ('USD', None, 3.8),
        _quote_events=SimpleNamespace(quote_saved=Mock()), _focus_history_after_close=Mock(), close=Mock())
    def interleaved_insert(con, **kwargs):
        con.execute('SELECT count(*) FROM quotes').fetchone()
        other = connect(db_path)
        try:
            other.execute('PRAGMA busy_timeout=0')
            try:
                with other:
                    other.execute("INSERT OR REPLACE INTO settings VALUES ('sync_during_save', '1')")
            except sqlite3.OperationalError as exc:
                assert 'locked' in str(exc)
        finally:
            other.close()
        return insert_quote(con, **kwargs)
    monkeypatch.setattr(ui, 'resolve_db_path', lambda: db_path)
    monkeypatch.setattr(ui, 'insert_quote', interleaved_insert)
    monkeypatch.setattr(ui, 'allocate_quote_code_for_new_quote', lambda *a, **kw:
        dict(quote_code='PE-001-0000002', quote_no_status='confirmed'))
    monkeypatch.setattr(ui, 'generar_pdf', lambda *a, **kw: str(tmp_path / 'new.pdf'))
    monkeypatch.setattr(ui, 'generar_ticket_para_cotizacion', lambda **kw: {})
    monkeypatch.setattr(ui.QMessageBox, 'information', Mock())
    error = Mock()
    monkeypatch.setattr(ui.QMessageBox, 'critical', error)
    monkeypatch.setattr(ui.QDesktopServices, 'openUrl', Mock())
    ui.PdfActionsMixin.generar_cotizacion(window)
    assert not error.called
    window._quote_events.quote_saved.emit.assert_called_once()
    con = connect(db_path)
    try:
        assert con.execute("SELECT count(*) FROM quotes WHERE quote_no='PE-001-0000002'").fetchone()[0] == 1
    finally:
        con.close()


class FullSQLiteHistoryTests(unittest.TestCase):
    def test_inventory_groups_only_the_same_user_and_code(self):
        from unittest.mock import patch
        from sqlModels.db import ensure_schema
        from sqlModels.quotes_repo import insert_quote
        from src.quote_sync_adapter import inventory
        con = sqlite3.connect(':memory:')
        con.row_factory = sqlite3.Row
        try:
            ensure_schema(con)
            with con:
                con.execute('INSERT INTO settings VALUES (?,?)', ('store_id', '001'))
                ids = []
                for number, code, username in [(1, '001', 'TESTUSER'), (2, '001', 'testuser'),
                                               (3, '002', 'TESTUSER'), (4, '001', 'OTRO')]:
                    snapshot = full_snapshot(number, installation=code)
                    snapshot['header']['cotizador_username'] = username
                    header = dict(snapshot['header'])
                    header.pop('estado')
                    ids.append(insert_quote(con, **header, pdf_path='',
                        items_base=snapshot['items_base'], items_shown=snapshot['items_shown']))
                with patch('src.quote_sync_adapter.projection', return_value=None):
                    result = inventory(con, owner_id='9', username=' TESTUSER ',
                        pid='test-installation-001', scopes=[('PE', 'LA CASA DEL PERFUME')])
            self.assertEqual(result, {'registered': 2, 'pending': 2})
            linked = con.execute('SELECT quote_id FROM quote_sync_document ORDER BY quote_id').fetchall()
            self.assertEqual([row[0] for row in linked], ids[:2])
            self.assertEqual(con.execute('SELECT count(*) FROM quote_sync_outbox').fetchone()[0], 2)
            self.assertEqual(con.execute('SELECT count(*) FROM quotes').fetchone()[0], 4)
        finally:
            con.close()

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
                con.execute('INSERT INTO settings VALUES (?,?)', ('store_id', '001'))
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
