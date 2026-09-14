"""Qt-independent coordinator; transport, clock and database are injectable."""
from __future__ import annotations

import json
import random
import time

from sqlModels import quote_sync_repo as repo


class SyncFailure(RuntimeError):
    def __init__(self, message, *, status=0, remote=None):
        super().__init__(message)
        self.status = status
        self.remote = remote


class QuoteSyncService:
    def __init__(self, *, connect, transport, pid, owner_id, scopes,
                 materialize, projection=None, clock=time.time, jitter=random.uniform):
        self.connect = connect
        self.transport = transport
        self.pid = pid
        self.owner_id = str(owner_id)
        self.scopes = tuple(scopes)
        self.materialize = materialize
        self.projection = projection
        self.clock = clock
        self.jitter = jitter
        self.offset = 0

    def cycle(self):
        """One upload and one download per scope; bad documents cannot starve peers."""
        result = dict(sent=0, received=0, failed=0, conflicts=0, offline=False)
        if not self.scopes:
            return result
        scopes = self.scopes[self.offset:] + self.scopes[:self.offset]
        self.offset = (self.offset + 1) % len(self.scopes)
        for country, company in scopes:
            self._send_one(country, company, result)
            try:
                con = self.connect()
                try:
                    cursor = con.execute('''SELECT cursor FROM quote_sync_cursor
                        WHERE owner_id=? AND country_code=? AND company_type=?''',
                        (self.owner_id, country, company)).fetchone()
                finally:
                    con.close()
                page = self.transport.changes(dict(pid=self.pid, country_code=country,
                    company_type=company, cursor=cursor[0] if cursor else None))
                con = self.connect()
                try:
                    with con:
                        repo.apply_page(con, owner_id=self.owner_id, country_code=country,
                            company_type=company, page=page, materialize=self.materialize)
                    result['received'] += len(page['events'])
                finally:
                    con.close()
            except (SyncFailure, OSError, ValueError) as exc:
                result['failed'] += 1
                result['offline'] = result['offline'] or isinstance(exc, OSError) or (
                    isinstance(exc, SyncFailure) and (exc.status == 0 or exc.status >= 500))
        return result

    def _send_one(self, country, company, result):
        con = self.connect()
        try:
            with con:
                candidates = con.execute('''SELECT d.quote_uuid FROM quote_sync_document d
                    LEFT JOIN quote_sync_outbox o ON o.quote_uuid=d.quote_uuid
                    LEFT JOIN quote_sync_conflict c ON c.quote_uuid=d.quote_uuid
                    WHERE d.owner_id=? AND d.country_code=? AND d.company_type=?
                    AND d.generation>d.acknowledged_generation AND c.quote_uuid IS NULL
                    AND COALESCE(o.blocked,0)=0 AND COALESCE(o.retry_at,0)<=?
                    AND d.error='' ORDER BY COALESCE(o.retry_at,0),d.rowid LIMIT 1''',
                    (self.owner_id, country, company, self.clock())).fetchone()
                budget = None
                prior = con.execute('SELECT 1 FROM quote_sync_outbox WHERE quote_uuid=?',
                                    (candidates[0],)).fetchone() if candidates else None
                if candidates and self.projection and not prior:
                    row = repo.document(con, candidates[0])
                    if row['revision'] == '0' and not row['deleted_at']:
                        try:
                            budget = self.projection(json.loads(row['snapshot']))
                        except (ValueError, RuntimeError, TypeError) as exc:
                            con.execute('UPDATE quote_sync_document SET error=? WHERE quote_uuid=?',
                                        (str(exc)[:500], candidates[0]))
                            result['failed'] += 1
                            return
                sent = repo.freeze(con, candidates[0], pid=self.pid, presupuesto=budget) if candidates else None
        finally:
            con.close()
        if not sent:
            return
        try:
            remote = self.transport.mutate(json.loads(sent['payload']))
        except (SyncFailure, OSError) as exc:
            result['offline'] = result['offline'] or isinstance(exc, OSError) or (
                isinstance(exc, SyncFailure) and (exc.status == 0 or exc.status >= 500))
            con = self.connect()
            try:
                with con:
                    if isinstance(exc, SyncFailure) and exc.status == 409 and exc.remote:
                        repo.conflict(con, sent['quote_uuid'], exc.remote, str(exc))
                        result['conflicts'] += 1
                    else:
                        attempts = sent['attempts'] + 1
                        delay = (5, 15, 30, 60, 120, 300)[min(attempts - 1, 5)]
                        permanent = isinstance(exc, SyncFailure) and exc.status in (403, 409, 422)
                        con.execute('''UPDATE quote_sync_outbox SET attempts=?,retry_at=?,
                            error=?,blocked=? WHERE mutation_id=?''',
                            (attempts, self.clock() + min(300, delay * self.jitter(.9, 1.1)),
                             str(exc)[:500], int(permanent), sent['mutation_id']))
                result['failed'] += 1
            finally:
                con.close()
            return
        con = self.connect()
        try:
            with con:
                current = repo.document(con, sent['quote_uuid'])
                repo.acknowledge(con, sent['mutation_id'], remote)
                acknowledged = repo.document(con, remote['quote_uuid'])
                still_pending = con.execute('SELECT 1 FROM quote_sync_outbox WHERE mutation_id=?',
                                            (sent['mutation_id'],)).fetchone()
                if current and acknowledged and not still_pending:
                    self.materialize(con, dict(remote, snapshot=json.loads(acknowledged['snapshot']),
                        deleted_at=acknowledged['deleted_at']), current['quote_id'])
            result['sent'] += 1
        finally:
            con.close()
