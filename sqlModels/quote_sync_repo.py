"""Durable sync state. All writes participate in the caller's SQLite transaction.

Network requests must happen outside that transaction. An outbox payload is frozen
before sending and never rewritten, including after an uncertain network result.
"""
from __future__ import annotations

import json
import sqlite3
import uuid


DDL = [
    """CREATE TABLE IF NOT EXISTS quote_sync_document (
        quote_uuid TEXT PRIMARY KEY, quote_id INTEGER UNIQUE,
        owner_id TEXT NOT NULL, country_code TEXT NOT NULL,
        company_type TEXT NOT NULL, origin_pid TEXT NOT NULL,
        revision TEXT NOT NULL DEFAULT '0', generation INTEGER NOT NULL DEFAULT 1,
        acknowledged_generation INTEGER NOT NULL DEFAULT 0,
        snapshot TEXT NOT NULL, remote_document TEXT, deferred_document TEXT,
        deleted_at TEXT, completeness TEXT NOT NULL DEFAULT 'complete',
        error TEXT NOT NULL DEFAULT '',
        FOREIGN KEY(quote_id) REFERENCES quotes(id)
    )""",
    """CREATE TABLE IF NOT EXISTS quote_sync_outbox (
        mutation_id TEXT PRIMARY KEY, quote_uuid TEXT NOT NULL,
        generation INTEGER NOT NULL, payload TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0, retry_at REAL NOT NULL DEFAULT 0,
        error TEXT NOT NULL DEFAULT '', blocked INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY(quote_uuid) REFERENCES quote_sync_document(quote_uuid)
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS quote_sync_outbox_document
        ON quote_sync_outbox(quote_uuid)""",
    """CREATE TABLE IF NOT EXISTS quote_sync_cursor (
        owner_id TEXT NOT NULL, country_code TEXT NOT NULL,
        company_type TEXT NOT NULL, cursor TEXT,
        PRIMARY KEY(owner_id,country_code,company_type)
    )""",
    """CREATE TABLE IF NOT EXISTS quote_sync_conflict (
        quote_uuid TEXT PRIMARY KEY, local_proposal TEXT NOT NULL,
        remote_document TEXT NOT NULL, reason TEXT NOT NULL,
        FOREIGN KEY(quote_uuid) REFERENCES quote_sync_document(quote_uuid)
    )""",
    """CREATE TABLE IF NOT EXISTS quote_client_snapshot (
        quote_id INTEGER PRIMARY KEY, snapshot TEXT NOT NULL,
        FOREIGN KEY(quote_id) REFERENCES quotes(id) ON DELETE CASCADE
    )""",
]


def install(con: sqlite3.Connection) -> None:
    for statement in DDL:
        con.execute(statement)
    if 'deferred_document' not in {row[1] for row in con.execute('PRAGMA table_info(quote_sync_document)')}:
        con.execute('ALTER TABLE quote_sync_document ADD COLUMN deferred_document TEXT')
    if 'sync_uuid' not in {row[1] for row in con.execute('PRAGMA table_info(quotes)')}:
        con.execute('ALTER TABLE quotes ADD COLUMN sync_uuid TEXT')
    con.execute('CREATE UNIQUE INDEX IF NOT EXISTS quotes_sync_uuid ON quotes(sync_uuid)')


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      sort_keys=True, separators=(',', ':'))


def _row(con, query, args=()):
    cursor = con.execute(query, args)
    value = cursor.fetchone()
    return dict(zip((col[0] for col in cursor.description), value)) if value else None


def document(con, quote_uuid):
    return _row(con, 'SELECT * FROM quote_sync_document WHERE quote_uuid=?', (quote_uuid,))


def requeue_code_rejections(con, *, owner_id, scopes):
    """Retry explicit code rejections after the server advertises compatibility.

    Only scheduling changes: the mutation ID, bytes and pending edits stay intact.
    The caller records the recovery marker in this same transaction.
    """
    recovered = 0
    for country, company in scopes:
        recovered += con.execute('''UPDATE quote_sync_outbox SET blocked=0,retry_at=0
            WHERE blocked=1 AND (error='Código de cotización inválido.'
                OR error LIKE 'INVALID_QUOTE_CODE: %') AND quote_uuid IN (
                SELECT d.quote_uuid FROM quote_sync_document d
                LEFT JOIN quote_sync_conflict c ON c.quote_uuid=d.quote_uuid
                WHERE d.owner_id=? AND d.country_code=? AND d.company_type=?
                AND d.revision='0' AND d.generation>d.acknowledged_generation
                AND d.error='' AND c.quote_uuid IS NULL)''',
            (str(owner_id), country, company)).rowcount
    return recovered


def queue_status(con, *, owner_id, scopes):
    result = dict(pending=0, blocked=0)
    for country, company in scopes:
        row = con.execute('''SELECT count(*),COALESCE(sum(CASE
            WHEN d.error<>'' OR o.blocked=1 OR c.quote_uuid IS NOT NULL THEN 1 ELSE 0 END),0)
            FROM quote_sync_document d
            LEFT JOIN quote_sync_outbox o ON o.quote_uuid=d.quote_uuid
            LEFT JOIN quote_sync_conflict c ON c.quote_uuid=d.quote_uuid
            WHERE d.owner_id=? AND d.country_code=? AND d.company_type=?
            AND d.generation>d.acknowledged_generation''',
            (str(owner_id), country, company)).fetchone()
        result['pending'] += row[0]
        result['blocked'] += row[1]
    return result


def remember_client(con, quote_id, client):
    con.execute('INSERT OR IGNORE INTO quote_client_snapshot VALUES (?,?)',
                (quote_id, encode(client)))


def register_new_quote(con, quote_id):
    con.execute('UPDATE quotes SET sync_uuid=COALESCE(sync_uuid,?) WHERE id=?',
                (str(uuid.uuid4()), quote_id))
    capabilities = con.execute("SELECT value FROM settings WHERE key='shared_quote_sync_capabilities'").fetchone()
    if not capabilities:
        return
    # The importer suppresses local writes using its own transaction-local marker.
    if con.execute("SELECT 1 FROM sqlite_temp_master WHERE name='quote_sync_importing'").fetchone():
        return
    from src.quote_sync_adapter import inventory
    info = json.loads(capabilities[0])
    pid = con.execute("SELECT value FROM settings WHERE key='cotizador_pid'").fetchone()
    if info.get('enabled') and pid:
        inventory(con, owner_id=info['owner_id'], username=info['username'], pid=pid[0],
                  scopes=[(scope['country_code'], scope['company_type']) for scope in info['scopes']],
                  quote_id=quote_id)


def register(con, *, quote_id, owner_id, origin_pid, snapshot, deleted_at=None):
    existing = _row(con, 'SELECT * FROM quote_sync_document WHERE quote_id=?', (quote_id,))
    if existing:
        return existing['quote_uuid']
    header = snapshot['header']
    saved_uuid = con.execute('SELECT sync_uuid FROM quotes WHERE id=?', (quote_id,)).fetchone()
    quote_uuid = saved_uuid[0] if saved_uuid and saved_uuid[0] else str(uuid.uuid4())
    con.execute('UPDATE quotes SET sync_uuid=? WHERE id=?', (quote_uuid, quote_id))
    con.execute('''INSERT INTO quote_sync_document
        (quote_uuid,quote_id,owner_id,country_code,company_type,origin_pid,snapshot,deleted_at)
        VALUES (?,?,?,?,?,?,?,?)''',
        (quote_uuid, quote_id, str(owner_id), header['country_code'],
         header['company_type'], origin_pid, encode(snapshot), deleted_at))
    return quote_uuid


def local_metadata(con, quote_id, *, estado=None, pago=None, chatbot=None, deleted_at=None):
    row = _row(con, 'SELECT * FROM quote_sync_document WHERE quote_id=?', (quote_id,))
    if not row:
        return
    snapshot = json.loads(row['snapshot'])
    for key, value in [('estado', estado), ('metodo_pago', pago), ('chatbot', chatbot)]:
        if value is not None:
            snapshot['header'][key] = value
    con.execute('''UPDATE quote_sync_document SET snapshot=?,generation=generation+1,
        deleted_at=COALESCE(?,deleted_at),error='' WHERE quote_id=?''',
        (encode(snapshot), deleted_at, quote_id))
    # Estado, pago y origen web no se imprimen en el PDF. Conservar su vínculo
    # local mientras estos cambios siguen pendientes de sincronización.
    # If a conflict already exists, preserve subsequent edits in its proposal too.
    saved_conflict = _row(con, 'SELECT * FROM quote_sync_conflict WHERE quote_uuid=?', (row['quote_uuid'],))
    if saved_conflict:
        conflict(con, row['quote_uuid'], json.loads(saved_conflict['remote_document']), saved_conflict['reason'])
    if con.execute("SELECT 1 FROM sqlite_master WHERE name='settings'").fetchone():
        pid = con.execute("SELECT value FROM settings WHERE key='cotizador_pid'").fetchone()
        if pid and row['revision'] != '0':
            freeze(con, row['quote_uuid'], pid=pid[0])


def freeze(con, quote_uuid, *, pid, presupuesto=None):
    """Return the same bytes until ACK, conflict resolution, or explicit rejection."""
    prior = _row(con, 'SELECT * FROM quote_sync_outbox WHERE quote_uuid=?', (quote_uuid,))
    if prior:
        return prior
    row = document(con, quote_uuid)
    if row['generation'] <= row['acknowledged_generation']:
        return None
    if _row(con, 'SELECT 1 FROM quote_sync_conflict WHERE quote_uuid=?', (quote_uuid,)):
        return None
    snapshot = json.loads(row['snapshot'])
    mutation_id = str(uuid.uuid4())
    operation = 'delete' if row['deleted_at'] else (
        'create' if row['revision'] == '0' else 'update_metadata')
    payload = dict(pid=pid, country_code=row['country_code'],
                   company_type=row['company_type'], quote_uuid=quote_uuid,
                   mutation_id=mutation_id, base_revision=row['revision'], operation=operation)
    if operation == 'create':
        payload['snapshot'] = snapshot
        if presupuesto is not None:
            payload['presupuesto'] = presupuesto
    elif operation == 'update_metadata':
        header = snapshot['header']
        baseline = json.loads(row['remote_document'])['snapshot']['header'] if row['remote_document'] else {}
        payload['metadata'] = {field: header[key] for field, key in
            [('estado', 'estado'), ('pago', 'metodo_pago'), ('chatbot', 'chatbot')]
            if header.get(key) != baseline.get(key)}
        if not payload['metadata']:
            con.execute('UPDATE quote_sync_document SET acknowledged_generation=generation WHERE quote_uuid=?',
                        (quote_uuid,))
            return None
    elif row['revision'] == '0':
        payload['legacy_code'] = snapshot['header']['quote_no']
        payload['legacy_snapshot'] = snapshot
    encoded = encode(payload)
    if len(encode(snapshot).encode('utf-8')) > 4 * 1024 * 1024:
        con.execute('UPDATE quote_sync_document SET error=? WHERE quote_uuid=?',
                    ('El documento supera 4 MiB.', quote_uuid))
        return None
    con.execute('''INSERT INTO quote_sync_outbox
        (mutation_id,quote_uuid,generation,payload) VALUES (?,?,?,?)''',
        (mutation_id, quote_uuid, row['generation'], encoded))
    return _row(con, 'SELECT * FROM quote_sync_outbox WHERE mutation_id=?', (mutation_id,))


def conflict(con, quote_uuid, remote, reason):
    row = document(con, quote_uuid)
    proposal = dict(snapshot=json.loads(row['snapshot']), deleted_at=row['deleted_at'])
    baseline = json.loads(row['remote_document'])['snapshot']['header'] if row['remote_document'] else {}
    proposal['metadata'] = {key: proposal['snapshot']['header'].get(key)
        for key in ('estado', 'metodo_pago', 'chatbot')
        if proposal['snapshot']['header'].get(key) != baseline.get(key)}
    con.execute('''INSERT INTO quote_sync_conflict VALUES (?,?,?,?)
        ON CONFLICT(quote_uuid) DO UPDATE SET local_proposal=excluded.local_proposal,
        remote_document=excluded.remote_document,reason=excluded.reason''',
        (quote_uuid, encode(proposal), encode(remote), reason))
    con.execute('UPDATE quote_sync_outbox SET blocked=1,error=? WHERE quote_uuid=?',
                (reason, quote_uuid))


def _discard_partial_stub(con, row, owner_id):
    """Only an unedited remote placeholder can yield its UUID to a local quote."""
    if (row['quote_id'] is not None or row['completeness'] != 'partial'
            or row['owner_id'] != str(owner_id)
            or row['generation'] != row['acknowledged_generation']):
        return False
    if con.execute('SELECT 1 FROM quote_sync_outbox WHERE quote_uuid=? UNION ALL '
                   'SELECT 1 FROM quote_sync_conflict WHERE quote_uuid=?',
                   (row['quote_uuid'], row['quote_uuid'])).fetchone():
        return False
    con.execute('DELETE FROM quote_sync_document WHERE quote_uuid=?', (row['quote_uuid'],))
    return True


def acknowledge(con, mutation_id, remote):
    sent = _row(con, 'SELECT * FROM quote_sync_outbox WHERE mutation_id=?', (mutation_id,))
    if not sent:
        return
    row = document(con, sent['quote_uuid'])
    if str(remote['owner_id']) != row['owner_id']:
        raise ValueError('El ACK pertenece a otro propietario.')
    remote = _preserve_emission(con, row, remote)
    if remote is None:
        return
    # UUID adoption is explicit. Preserve all local relationships and pending edits.
    target = remote['quote_uuid']
    if target != row['quote_uuid']:
        target_row = document(con, target)
        if target_row and _discard_partial_stub(con, target_row, row['owner_id']):
            target_row = None
        if target_row:
            conflict(con, row['quote_uuid'], remote, 'Correspondencia local duplicada')
            return
        con.execute('DELETE FROM quote_sync_outbox WHERE mutation_id=?', (mutation_id,))
        con.execute('UPDATE quote_sync_document SET quote_uuid=? WHERE quote_uuid=?',
                    (target, row['quote_uuid']))
        con.execute('UPDATE quotes SET sync_uuid=? WHERE id=?', (target, row['quote_id']))
    if int(remote['revision']) < int(row['revision']):
        raise ValueError('El ACK retrocede la revisión.')
    newer = row['generation'] > sent['generation']
    acknowledged_snapshot = remote['snapshot']
    if newer:
        acknowledged_snapshot = json.loads(encode(remote['snapshot']))
        local_header = json.loads(row['snapshot'])['header']
        sent_payload = json.loads(sent['payload'])
        sent_header = (sent_payload.get('snapshot') or {}).get('header', {})
        sent_metadata = sent_payload.get('metadata') or {}
        if not sent_header and row['remote_document']:
            sent_header = dict(json.loads(row['remote_document'])['snapshot']['header'])
            for field, key in [('estado', 'estado'), ('pago', 'metodo_pago'), ('chatbot', 'chatbot')]:
                if field in sent_metadata:
                    sent_header[key] = sent_metadata[field]
        for key in ('estado', 'metodo_pago', 'chatbot'):
            if local_header.get(key) != sent_header.get(key):
                acknowledged_snapshot['header'][key] = local_header.get(key)
    con.execute('''UPDATE quote_sync_document SET revision=?,acknowledged_generation=?,
        remote_document=?,snapshot=?,deleted_at=?,error='',deferred_document=NULL WHERE quote_uuid=?''',
        (str(remote['revision']), sent['generation'], encode(remote),
         encode(acknowledged_snapshot),
         row['deleted_at'] if newer else remote.get('deleted_at'), target))
    con.execute('DELETE FROM quote_sync_outbox WHERE mutation_id=?', (mutation_id,))
    deferred = json.loads(row['deferred_document']) if row['deferred_document'] else None
    if deferred and int(deferred['revision']) > int(remote['revision']):
        conflict(con, target, deferred, 'El servidor cambió después de confirmar el envío')


def _preserve_emission(con, row, remote):
    if row['completeness'] != 'complete' or remote['completeness'] != 'complete':
        return remote
    from src.quote_dates import same_issue_datetime
    local = json.loads(row['snapshot'])['header'].get('created_at')
    incoming = remote['snapshot']['header'].get('created_at')
    if local is not None and incoming is not None and local != incoming:
        if not same_issue_datetime(local, incoming):
            conflict(con, row['quote_uuid'], remote, 'La fecha de emisión difiere del documento original')
            return None
        remote = json.loads(encode(remote))
        remote['snapshot']['header']['created_at'] = local
    return remote


def _matching_local_document(con, remote):
    """The local inventory may still be pending when its server copy arrives."""
    from src.quote_code import sync_quote_key
    required = {'quote_no', 'country_code', 'company_type', 'cotizador_username', 'id_cotizador'}
    if not required.issubset({c[1] for c in con.execute('PRAGMA table_info(quotes)')}):
        return None
    header = remote['snapshot']['header']
    country, company = remote['country_code'], remote['company_type']
    username = str(header.get('cotizador_username') or '').strip().upper()
    installation = str(header.get('id_cotizador') or '').strip().upper()
    code = sync_quote_key(header.get('quote_no'), country, installation)
    if not username or code is None:
        return None
    con.create_function('cotizador_quote_key', 3, sync_quote_key, deterministic=True)
    matches = con.execute('''SELECT id,api_sent_at FROM quotes
        WHERE country_code=? AND (company_type=? OR (company_type='LCDP' AND ?='LA CASA DEL PERFUME'))
        AND upper(trim(cotizador_username))=? AND upper(trim(id_cotizador))=?
        AND COALESCE(quote_no_status,'confirmed')='confirmed'
        AND cotizador_quote_key(quote_no,country_code,id_cotizador)=?''',
        (country, company, company, username, installation, code)).fetchall()
    if not matches:
        return None
    for quote_id, sent_at in matches:
        row = _row(con, 'SELECT * FROM quote_sync_document WHERE quote_id=?', (quote_id,))
        if row and row['owner_id'] != str(remote['owner_id']):
            raise ValueError('La cotización local pertenece a otro usuario.')
        if not row:
            from src.quote_sync_adapter import snapshot_for
            deleted = con.execute('SELECT deleted_at FROM quotes WHERE id=?', (quote_id,)).fetchone()[0]
            uid = register(con, quote_id=quote_id, owner_id=remote['owner_id'], origin_pid='',
                           snapshot=snapshot_for(con, quote_id), deleted_at=deleted)
            row = document(con, uid)
        row['_unsent_local'] = not sent_at
        if len(matches) > 1:
            conflict(con, row['quote_uuid'], remote, 'Hay varias cotizaciones locales con la misma identidad')
            row['_identity_ambiguous'] = True
    return row


def _same_content(left, right):
    from src.quote_code import sync_quote_key
    from src.quote_dates import same_issue_datetime
    left, right = json.loads(encode(left)), json.loads(encode(right))
    if not same_issue_datetime(left['header'].get('created_at'), right['header'].get('created_at')):
        return False
    for snapshot in (left, right):
        h = snapshot['header']
        for key in ('estado', 'metodo_pago', 'chatbot', 'created_at'):
            h.pop(key, None)
        h['quote_no'] = sync_quote_key(h.get('quote_no'), h.get('country_code'), h.get('id_cotizador'))
        h['cotizador_username'] = str(h.get('cotizador_username') or '').strip().upper()
        h['id_cotizador'] = str(h.get('id_cotizador') or '').strip().upper()
        if h.get('company_type') == 'LCDP':
            h['company_type'] = 'LA CASA DEL PERFUME'
    return left == right


def apply_page(con, *, owner_id, country_code, company_type, page, materialize):
    """Caller commits all events plus cursor together; materialize must not commit."""
    for remote in page['events']:
        if (str(remote['owner_id']), remote['country_code'], remote['company_type']) != (
                str(owner_id), country_code, company_type):
            raise ValueError('Documento fuera del ámbito solicitado.')
        row = document(con, remote['quote_uuid'])
        if not row or row['quote_id'] is None:
            local = _matching_local_document(con, remote)
            if local:
                if local.get('_identity_ambiguous'):
                    continue
                if row and row['quote_uuid'] != local['quote_uuid']:
                    if not _discard_partial_stub(con, row, owner_id):
                        conflict(con, local['quote_uuid'], remote, 'Correspondencia local duplicada')
                        continue
                row = local
        if row and row['revision'] != '0' and row['quote_uuid'] != remote['quote_uuid']:
            conflict(con, row['quote_uuid'], remote, 'El número ya está vinculado a otra cotización compartida')
            continue
        if row and int(row['revision']) >= int(remote['revision']):
            continue
        if row:
            remote = _preserve_emission(con, row, remote)
            if remote is None:
                continue
        pending_metadata = None
        if row and row['revision'] == '0' and remote['completeness'] == 'complete':
            sending = _row(con, 'SELECT * FROM quote_sync_outbox WHERE quote_uuid=?', (row['quote_uuid'],))
            saved_conflict = _row(con, 'SELECT 1 FROM quote_sync_conflict WHERE quote_uuid=?', (row['quote_uuid'],))
            local_snapshot = json.loads(row['snapshot'])
            if not sending and not saved_conflict and _same_content(local_snapshot, remote['snapshot']):
                if row.get('_unsent_local') or row['generation'] > 1 or row['deleted_at']:
                    pending_metadata = {key: local_snapshot['header'].get(key)
                        for key in ('estado', 'metodo_pago', 'chatbot')
                        if local_snapshot['header'].get(key) != remote['snapshot']['header'].get(key)}
                pending_delete = row['deleted_at']
                target = remote['quote_uuid']
                con.execute('''UPDATE quote_sync_document SET quote_uuid=?,
                    acknowledged_generation=generation WHERE quote_uuid=?''', (target, row['quote_uuid']))
                con.execute('UPDATE quotes SET sync_uuid=? WHERE id=?', (target, row['quote_id']))
                row = document(con, target)
        if row and row['generation'] > row['acknowledged_generation']:
            sending = _row(con, 'SELECT * FROM quote_sync_outbox WHERE quote_uuid=?', (row['quote_uuid'],))
            if (sending and not sending['blocked']) or remote['completeness'] == 'partial':
                # An uncertain ACK is resolved by retrying its immutable mutation.
                # Retain later events so advancing the cursor cannot lose them.
                con.execute('UPDATE quote_sync_document SET deferred_document=? WHERE quote_uuid=?',
                            (encode(remote), row['quote_uuid']))
                continue
            conflict(con, row['quote_uuid'], remote, 'Hay cambios locales pendientes')
            continue
        quote_id = materialize(con, remote, row['quote_id'] if row else None)
        if row:
            con.execute('''UPDATE quote_sync_document SET quote_id=?,revision=?,snapshot=?,
                remote_document=?,deleted_at=?,completeness=? WHERE quote_uuid=?''',
                (quote_id, str(remote['revision']), encode(remote['snapshot']), encode(remote),
                 remote.get('deleted_at'), remote['completeness'], remote['quote_uuid']))
        else:
            con.execute('''INSERT INTO quote_sync_document
                (quote_uuid,quote_id,owner_id,country_code,company_type,origin_pid,
                 revision,generation,acknowledged_generation,snapshot,remote_document,
                 deleted_at,completeness) VALUES (?,?,?,?,?,?,?,0,0,?,?,?,?)''',
                (remote['quote_uuid'], quote_id, str(owner_id), country_code, company_type,
                 remote.get('origin_pid') or '', str(remote['revision']), encode(remote['snapshot']),
                 encode(remote), remote.get('deleted_at'), remote['completeness']))
        if pending_metadata is not None and (pending_metadata or pending_delete):
            local_metadata(con, quote_id, estado=pending_metadata.get('estado'),
                pago=pending_metadata.get('metodo_pago'), chatbot=pending_metadata.get('chatbot'),
                deleted_at=pending_delete)
            updated = document(con, remote['quote_uuid'])
            materialize(con, dict(remote, snapshot=json.loads(updated['snapshot']),
                                  deleted_at=updated['deleted_at']), quote_id)
    con.execute('''INSERT INTO quote_sync_cursor VALUES (?,?,?,?)
        ON CONFLICT(owner_id,country_code,company_type) DO UPDATE SET cursor=excluded.cursor''',
        (str(owner_id), country_code, company_type, page['next_cursor']))


def resolve(con, quote_uuid, *, reapply, materialize):
    saved = _row(con, 'SELECT * FROM quote_sync_conflict WHERE quote_uuid=?', (quote_uuid,))
    if not saved:
        raise ValueError('No existe un conflicto pendiente.')
    remote = json.loads(saved['remote_document'])
    row = document(con, quote_uuid)
    if remote['quote_uuid'] != quote_uuid and document(con, remote['quote_uuid']):
        raise ValueError('La correspondencia duplicada requiere conciliación individual.')
    if reapply and remote.get('deleted_at'):
        raise ValueError('Una cotización eliminada no se puede restaurar.')
    materialize(con, remote, row['quote_id'])
    con.execute('DELETE FROM quote_sync_outbox WHERE quote_uuid=?', (quote_uuid,))
    con.execute('DELETE FROM quote_sync_conflict WHERE quote_uuid=?', (quote_uuid,))
    if remote['quote_uuid'] != quote_uuid:
        con.execute('UPDATE quote_sync_document SET quote_uuid=? WHERE quote_uuid=?',
                    (remote['quote_uuid'], quote_uuid))
        quote_uuid = remote['quote_uuid']
    snapshot = remote['snapshot']
    deleted_at = remote.get('deleted_at')
    if reapply:
        proposal = json.loads(saved['local_proposal'])
        for key, value in proposal.get('metadata', {}).items():
            snapshot['header'][key] = value
        deleted_at = proposal['deleted_at']
        materialize(con, dict(remote, snapshot=snapshot, deleted_at=deleted_at), row['quote_id'])
    con.execute('''UPDATE quote_sync_document SET revision=?,snapshot=?,remote_document=?,
        deleted_at=?,acknowledged_generation=generation,generation=generation+?,error=''
        WHERE quote_uuid=?''',
        (str(remote['revision']), encode(snapshot), saved['remote_document'], deleted_at,
         int(reapply), quote_uuid))
