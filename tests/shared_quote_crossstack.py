"""Acceptance runner: two temporary SQLite stores against an isolated EFAPI fixture.

Invoked by EFAPI Jest; never resolves the installed application's database/config.
"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlModels.db import connect, ensure_schema
from sqlModels import quote_sync_repo as repo
from sqlModels.quotes_repo import insert_quote, update_quote_payment, soft_delete_quote
from src.quote_sync_adapter import materialize
from src.quote_sync_service import QuoteSyncService, SyncFailure
from test_shared_quote_sync import full_snapshot


class Transport:
    def __init__(self, port):
        self.base = f'http://127.0.0.1:{int(port)}'
        self.lose_ack = False
        self.confirmed_at = None

    def request(self, path, payload):
        request = urllib.request.Request(self.base + '/' + path,
            data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'}, method='POST')
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                result = json.load(response)['data']
        except urllib.error.HTTPError as exc:
            body = json.load(exc)
            raise SyncFailure(body['message'], status=exc.code,
                              remote=(body.get('details') or {}).get('remote')) from exc
        return result

    def mutate(self, payload):
        result = self.request('mutate', payload)
        self.confirmed_at = time.monotonic()
        if self.lose_ack:
            self.lose_ack = False
            raise OSError('Simulated connection loss after commit')
        return result

    def changes(self, payload):
        return self.request('changes', payload)


def main(port):
    transport = Transport(port)
    with tempfile.TemporaryDirectory(prefix='cotizador-shared-acceptance-') as directory:
        paths = [str(Path(directory) / f'{name}.sqlite3') for name in ['A', 'B']]
        for path in paths:
            con = connect(path)
            ensure_schema(con)
            con.close()
        caps = transport.request('capabilities', {'pid': 'test-installation-001'})
        owner = caps['owner_id']
        scopes = [(s['country_code'], s['company_type']) for s in caps['scopes']]
        services = [QuoteSyncService(connect=lambda p=path: connect(p), transport=transport,
            pid=f'test-installation-00{i+1}', owner_id=owner, scopes=scopes,
            materialize=materialize, jitter=lambda a, b: 1) for i, path in enumerate(paths)]

        def add(index, number, country='PE', company='LA CASA DEL PERFUME'):
            snapshot = full_snapshot(number, f'00{index+1}')
            snapshot['header'].update(country_code=country, company_type=company)
            snapshot['header']['quote_no'] = f'{country}-00{index+1}-{number:07d}'
            con = connect(paths[index])
            try:
                with con:
                    h = dict(snapshot['header']); h.pop('estado')
                    qid = insert_quote(con, **h, pdf_path='', items_base=snapshot['items_base'],
                                       items_shown=snapshot['items_shown'])
                    uid = repo.register(con, quote_id=qid, owner_id=owner,
                        origin_pid=f'test-installation-00{index+1}', snapshot=snapshot)
                return qid, uid
            finally:
                con.close()

        def count(index):
            con = connect(paths[index])
            try:
                return con.execute('SELECT count(*) FROM quotes WHERE deleted_at IS NULL').fetchone()[0]
            finally:
                con.close()

        ids = [add(0, n) for n in range(1, 6)]
        for _ in range(5):
            services[0].cycle()
        started = time.monotonic()
        services[1].cycle()
        assert count(0) == count(1) == 5, (count(0), count(1))
        sixth = add(1, 1)
        transport.lose_ack = True
        services[1].cycle()
        sixth_confirmed_at = transport.confirmed_at
        con = connect(paths[1])
        with con:
            con.execute('UPDATE quote_sync_outbox SET retry_at=0')
        con.close()
        services[1].cycle()
        services[0].cycle()
        confirmation_to_local = time.monotonic() - sixth_confirmed_at
        assert confirmation_to_local < 15
        assert count(0) == count(1) == 6
        # Metadata propagates without duplicating items or requiring an artifact.
        con = connect(paths[0])
        with con:
            update_quote_payment(con, ids[0][0], 'EFECTIVO')
        con.close()
        services[0].cycle(); services[1].cycle()
        con = connect(paths[1])
        remote_row = repo.document(con, ids[0][1])
        assert json.loads(remote_row['snapshot'])['header']['metodo_pago'] == 'EFECTIVO'
        con.close()
        # Both mutate revision 2. B's local proposal remains available after A wins.
        for i in range(2):
            con = connect(paths[i])
            with con:
                qid = repo.document(con, ids[0][1])['quote_id']
                update_quote_payment(con, qid, f'PROPUESTA {i}')
            con.close()
        services[0].cycle(); services[1].cycle()
        con = connect(paths[1])
        assert con.execute('SELECT count(*) FROM quote_sync_conflict').fetchone()[0] == 1
        with con:
            repo.resolve(con, ids[0][1], reapply=False, materialize=materialize)
        con.close()
        add(1, 2, 'PY', 'EF PERFUMES')
        services[1].cycle(); services[0].cycle()
        assert count(0) == count(1) == 7
        con = connect(paths[0])
        with con:
            soft_delete_quote(con, ids[1][0], '2026-09-07T11:00:00-05:00')
        con.close()
        services[0].cycle(); services[1].cycle()
        assert count(0) == count(1) == 6
        elapsed = time.monotonic() - started
        print(json.dumps(dict(initial_quotes=5, shared_after_create=6,
            final_visible=6, multi_scope=True, timeout_idempotency=True,
            conflict_preserved=True, shared_delete=True, elapsed_seconds=round(elapsed, 3),
            confirmation_to_local_seconds=round(confirmation_to_local, 3))))


if __name__ == '__main__':
    main(sys.argv[1])
