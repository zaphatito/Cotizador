"""Mapping of the shared protocol to the existing history and EFAPI session."""
from __future__ import annotations

import json

from sqlModels import quote_sync_repo as repo
from sqlModels.quotes_repo import get_quote_header, get_quote_items, insert_quote
from .server_identity import ServerIdentity, validate_functional_identity
from .quote_sync_service import SyncFailure


HEADER_FIELDS = ('country_code company_type quote_no cotizador_username id_cotizador '
    'created_at cliente cedula tipo_documento telefono direccion email estado metodo_pago '
    'chatbot base_currency currency_shown tasa_shown subtotal_bruto_base descuento_total_base '
    'total_neto_base subtotal_bruto_shown descuento_total_shown total_neto_shown').split()


def snapshot_for(con, quote_id):
    # Read original identity directly: the legacy UI accessor can infer context.
    raw = dict(con.execute('SELECT * FROM quotes WHERE id=?', (quote_id,)).fetchone())
    validate_functional_identity(raw.get('cotizador_username'), raw.get('id_cotizador'))
    header = get_quote_header(con, quote_id)
    base, shown = get_quote_items(con, quote_id)
    snapshot = dict(version=1, header={key: header.get(key) for key in HEADER_FIELDS},
                    items_base=base, items_shown=shown)
    # Context/price defaults are useful for new documents, never historical evidence.
    for key in ('country_code', 'company_type', 'base_currency', 'cotizador_username', 'id_cotizador'):
        snapshot['header'][key] = raw.get(key)
    snapshot['header']['chatbot'] = bool(header.get('chatbot'))
    if snapshot['header']['company_type'] == 'LCDP':
        snapshot['header']['company_type'] = 'LA CASA DEL PERFUME'
    if not base or raw.get('quote_no_status') not in ('confirmed', None, ''):
        raise ValueError('El documento necesita detalles y un código confirmado antes de sincronizar.')
    return snapshot


def inventory(con, *, owner_id, username, pid, scopes):
    allowed = set(scopes)
    result = dict(registered=0, pending=0)
    # Include sent and deleted records. Never replace the stored historical author.
    for raw in con.execute('''SELECT q.* FROM quotes q LEFT JOIN quote_sync_document d
        ON d.quote_id=q.id WHERE d.quote_id IS NULL ORDER BY q.id''').fetchall():
        raw = dict(raw)
        try:
            snapshot = snapshot_for(con, raw['id'])
            header = snapshot['header']
            if header['cotizador_username'].strip().casefold() != username.strip().casefold():
                raise ValueError('El autor histórico requiere conciliación; no se sustituye por el usuario actual.')
            if (header['country_code'], header['company_type']) not in allowed:
                raise ValueError('El ámbito histórico no está autorizado.')
            quote_uuid = repo.register(con, quote_id=raw['id'], owner_id=owner_id, origin_pid=pid,
                                       snapshot=snapshot, deleted_at=raw.get('deleted_at'))
            budget = None if raw.get('deleted_at') else projection(snapshot)
            repo.freeze(con, quote_uuid, pid=pid, presupuesto=budget)
            result['registered'] += 1
        except (ValueError, RuntimeError, TypeError) as exc:
            con.execute('UPDATE quotes SET api_error_message=? WHERE id=?', (str(exc), raw['id']))
            con.execute('UPDATE quote_sync_document SET error=? WHERE quote_id=?', (str(exc)[:500], raw['id']))
            result['pending'] += 1
    return result


def materialize(con, remote, quote_id):
    if remote['completeness'] != 'complete':
        # A partial record stays in sync storage; do not fabricate missing amounts.
        return quote_id
    snapshot = remote['snapshot']
    h = snapshot['header']
    if quote_id is None:
        args = {key: h.get(key, '') for key in HEADER_FIELDS if key != 'estado'}
        con.execute('CREATE TEMP TABLE quote_sync_importing(marker INTEGER)')
        try:
            quote_id = insert_quote(con, **args, pdf_path='',
                                    items_base=snapshot['items_base'], items_shown=snapshot['items_shown'])
        finally:
            con.execute('DROP TABLE quote_sync_importing')
    # Reconciliation can adopt a different initial snapshot. Ordinary metadata
    # updates compare equal and leave detail rows (including their IDs) untouched.
    columns = ['codigo', 'producto', 'categoria', 'tipo_prod', 'fragancia', 'observacion',
        'cantidad', 'factor_total', 'precio_base', 'subtotal_base', 'descuento_mode',
        'descuento_pct', 'descuento_monto_base', 'total_base', 'precio_override_base',
        'precio_tier', 'id_precioventa', 'precio_shown', 'subtotal_shown',
        'descuento_monto_shown', 'total_shown']
    expected = []
    for base, shown in zip(snapshot['items_base'], snapshot['items_shown']):
        expected.append(tuple([base.get(key, '') for key in columns[:6]] + [
            base['cantidad'], base.get('factor_total', 1), base['precio'], base['subtotal_base'],
            base.get('descuento_mode'), base.get('descuento_pct', 0), base.get('descuento_monto', 0),
            base['total'], base.get('precio_override'), base.get('precio_tier'), base['id_precioventa'],
            shown['precio'], shown['subtotal'], shown['descuento'], shown['total']]))
    existing = [tuple(row) for row in con.execute(
        f"SELECT {','.join(columns)} FROM quote_items WHERE quote_id=? ORDER BY id", (quote_id,))]
    if existing != expected:
        con.execute('DELETE FROM quote_items WHERE quote_id=?', (quote_id,))
        con.executemany(f"INSERT INTO quote_items(quote_id,{','.join(columns)}) VALUES ({','.join('?' for _ in range(len(columns)+1))})",
                        [(quote_id,) + row for row in expected])
    stable_columns = ['country_code', 'company_type', 'quote_no', 'base_currency', 'currency_shown',
        'tasa_shown', 'subtotal_bruto_base', 'descuento_total_base', 'total_neto_base',
        'subtotal_bruto_shown', 'descuento_total_shown', 'total_neto_shown',
        'cotizador_username', 'id_cotizador', 'created_at']
    con.execute(f"UPDATE quotes SET {','.join(column+'=?' for column in stable_columns)} WHERE id=?",
                tuple(h[key] for key in stable_columns) + (quote_id,))
    con.execute('UPDATE quote_client_snapshot SET snapshot=? WHERE quote_id=?',
        (repo.encode({key: h.get(key, '') for key in ('cliente','cedula','tipo_documento','telefono','direccion','email')}), quote_id))
    con.execute('''UPDATE quotes SET estado=?,metodo_pago=?,chatbot=?,deleted_at=?,
        pdf_path='',sync_uuid=? WHERE id=?''',
        (h.get('estado') or '', h.get('metodo_pago') or '', int(bool(h.get('chatbot'))),
         remote.get('deleted_at'), remote['quote_uuid'], quote_id))
    return quote_id


class EfapiQuoteTransport:
    def __init__(self, token):
        self.token = token

    def _post(self, case, payload):
        from .api.controller import post
        from .api.generic_controller import ApiRequestError
        try:
            response = post(case, json_data=payload,
                headers={'Authorization': f'Bearer {self.token}'}, timeout=12,
                expected_status=(200,), raise_for_status=True)
            return response.data['data']
        except ApiRequestError as exc:
            response = exc.response
            body = response.data if response and isinstance(response.data, dict) else {}
            raise SyncFailure(body.get('message') or 'No se pudo contactar al servidor.',
                status=response.status_code if response else 0,
                remote=(body.get('details') or {}).get('remote') if isinstance(body.get('details'), dict) else None) from exc

    def mutate(self, payload):
        from .api.cases import API_CASE_POST_QUOTE_SYNC
        return self._post(API_CASE_POST_QUOTE_SYNC, payload)

    def changes(self, payload):
        from .api.cases import API_CASE_GET_QUOTES_SYNC
        return self._post(API_CASE_GET_QUOTES_SYNC, payload)

    def get(self, payload):
        from .api.cases import API_CASE_GET_QUOTE_SYNC
        return self._post(API_CASE_GET_QUOTE_SYNC, payload)


def projection(snapshot):
    from .api.presupuesto_client import build_presupuesto_payload
    h = snapshot['header']
    return build_presupuesto_payload(quote_code=h['quote_no'], fecha_emision_ts=h['created_at'],
        cliente=h['cliente'], cedula=h['cedula'], telefono=h['telefono'],
        metodo_pago=h['metodo_pago'], direccion=h['direccion'], email=h['email'],
        estado=h['estado'], tipo_documento=h['tipo_documento'], cod_pais=h['country_code'],
        empresa=h['company_type'], id_cotizador=h['id_cotizador'],
        items_base=snapshot['items_base'], app_username=h['cotizador_username'],
        chatbot=h['chatbot'])['presupuesto']


def run_shared_cycle(db_path, verification):
    from sqlModels.db import connect
    from sqlModels.settings_repo import get_setting, set_setting
    from .api.presupuesto_client import _load_api_identity, _login_api, _reserve_provisional_quote_number
    from .quote_sync_service import QuoteSyncService
    capabilities = verification.get('sync') or {}
    con = connect(db_path)
    try:
        sticky = get_setting(con, 'shared_quote_sync_owner', '')
        if not capabilities.get('enabled'):
            # Never fall back to blind writes after this installation was enabled.
            return {'enabled': bool(sticky), 'paused': bool(sticky), 'sent': 0, 'received': 0}
        owner_id = str(capabilities['owner_id'])
        scopes = [(s['country_code'], s['company_type']) for s in capabilities['scopes']]
        with con:
            set_setting(con, 'shared_quote_sync_owner', owner_id)
            set_setting(con, 'shared_quote_sync_capabilities', repo.encode(capabilities))
        pending_number = con.execute('''SELECT id FROM quotes WHERE quote_no_status IN ('provisional','reserved')
            AND deleted_at IS NULL AND upper(trim(cotizador_username))=upper(trim(?))
            AND id_cotizador=? ORDER BY id LIMIT 1''',
            (capabilities['username'], get_setting(con, 'store_id', ''))).fetchone()
    finally:
        con.close()
    if pending_number:
        try:
            _reserve_provisional_quote_number(pending_number[0], db_path=db_path)
            con = connect(db_path)
            try:
                with con:
                    con.execute("UPDATE quotes SET quote_no_status='confirmed',pdf_path='' WHERE id=? AND quote_no_status='reserved'",
                                (pending_number[0],))
            finally:
                con.close()
        except (ValueError, RuntimeError, OSError) as exc:
            con = connect(db_path)
            try:
                with con:
                    con.execute('UPDATE quotes SET api_error_message=? WHERE id=?',
                                (str(exc)[:500], pending_number[0]))
            finally:
                con.close()
    con = connect(db_path)
    try:
        with con:
            inventory(con, owner_id=owner_id, username=capabilities['username'],
                      pid=verification['pid'], scopes=scopes)
    finally:
        con.close()
    configured = _load_api_identity()
    identity = ServerIdentity(api_username=configured.technical_username,
        functional_username=configured.functional_username, pid=verification['pid'],
        id_cotizador=configured.id_cotizador).validated()
    token, _response = _login_api(user_id=configured.technical_user_id, api_username=identity.api_username)
    coordinator = QuoteSyncService(connect=lambda: connect(db_path),
        transport=EfapiQuoteTransport(token), pid=identity.pid, owner_id=owner_id,
        scopes=scopes, materialize=materialize, projection=projection)
    return dict(coordinator.cycle(), enabled=True)
