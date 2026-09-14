from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.api import controller, presupuesto_client as pc
from src.api.cases import API_CASE_LOGIN, API_CASE_VERIFY_COTIZADOR
from src.api.generic_controller import ApiRequestError
from src import quote_sync_adapter
from src.widgets_parts import quote_history_dialog as history


def response(data, status=200):
    return SimpleNamespace(data=data, text='', status_code=status, ok=status < 400)


@pytest.fixture
def http(monkeypatch):
    calls = []
    now = [1000.0]

    def request(**kwargs):
        calls.append(kwargs)
        if kwargs['case'] == API_CASE_LOGIN:
            return response({'access_token': f'token-{len(calls)}',
                             'access_token_expires_in': 600}, 201)
        return response({'allowed': True})

    client = SimpleNamespace(request=Mock(side_effect=request))
    monkeypatch.setattr(controller, '_controller', client)
    monkeypatch.setattr(controller, '_login_sessions', {}, raising=False)
    import time
    monkeypatch.setattr(time, 'monotonic', lambda: now[0])
    return client, calls, now


def login(username='api-test', password='test-password'):
    return controller.post(API_CASE_LOGIN,
                           json_data={'id': 1, 'username': username, 'password': password})


def test_login_is_shared_across_concurrent_workers_and_renewed_before_expiry(http):
    _, calls, now = http
    with ThreadPoolExecutor(max_workers=4) as pool:
        tokens = list(pool.map(lambda _: login().data['access_token'], range(12)))
    assert len(set(tokens)) == 1
    assert len(calls) == 1
    now[0] += 571
    assert login().data['access_token'] != tokens[0]
    assert len(calls) == 2


def test_login_is_separate_for_credentials_and_transport(http, monkeypatch):
    client, calls, _ = http
    first = login().data['access_token']
    assert login('another-api').data['access_token'] != first
    assert login(password='changed-password').data['access_token'] != first
    monkeypatch.setattr(controller, '_controller', SimpleNamespace(request=client.request))
    assert login().data['access_token'] != first
    assert len(calls) == 4


@pytest.mark.parametrize('status', [403, 422, 503])
def test_non_authentication_errors_do_not_retry_or_discard_session(http, status):
    client, calls, _ = http
    initial = login()
    client.request.side_effect = ApiRequestError('rejected', response=response({}, status))
    with pytest.raises(ApiRequestError):
        controller.post(API_CASE_VERIFY_COTIZADOR,
                        headers={'Authorization': f"Bearer {initial.data['access_token']}"})
    client.request.side_effect = None
    assert login() == initial
    assert len(calls) == 1


@pytest.mark.parametrize('raise_for_status', [True, False])
def test_401_refreshes_once_and_replays_the_same_payload(http, raise_for_status):
    client, _, _ = http
    token = login().data['access_token']
    rejected = response({}, 401)
    client.request.side_effect = [
        ApiRequestError('expired', response=rejected) if raise_for_status else rejected,
        response({'access_token': 'renewed', 'access_token_expires_in': 600}, 201),
        response({'saved': True}),
    ]
    payload = {'mutation_id': 'unchanged-test-key', 'data': [1, 2]}
    result = controller.post(8, json_data=payload,
                             headers={'Authorization': f'Bearer {token}'},
                             raise_for_status=raise_for_status)
    assert result.data == {'saved': True}
    replay = client.request.call_args.kwargs
    assert replay['json_data'] == payload
    assert replay['headers'] == {'Authorization': 'Bearer renewed'}
    assert login().data['access_token'] == 'renewed'
    assert client.request.call_count == 4


def test_second_401_is_reported_without_an_infinite_retry(http):
    client, _, _ = http
    token = login().data['access_token']
    denied = ApiRequestError('denied', response=response({}, 401))
    client.request.side_effect = [denied,
        response({'access_token': 'renewed', 'access_token_expires_in': 600}, 201), denied]
    with pytest.raises(ApiRequestError):
        controller.post(7, headers={'Authorization': f'Bearer {token}'})
    assert client.request.call_count == 4


@pytest.fixture
def verification(monkeypatch, http, tmp_path):
    identity = [1, 'api-test', 'test-user', 'PERU', 'LA CASA DEL PERFUME', '001', False]
    pid = ['test-installation-pid']
    post = Mock(return_value=response({'allowed': True, 'sync': {'enabled': True}}))
    monkeypatch.setattr(pc, '_verification_cache', None, raising=False)
    monkeypatch.setattr(pc, '_load_api_identity', lambda: tuple(identity))
    monkeypatch.setattr(pc, 'resolve_db_path', lambda: str(tmp_path / 'verification.db'))
    monkeypatch.setattr(pc, '_load_or_create_cotizador_pid', lambda: pid[0])
    monkeypatch.setattr(pc, '_build_cotizador_verification_payload',
                        lambda **_: {'pid': pid[0]})
    monkeypatch.setattr(pc, '_load_verification_reference_at', lambda: 'recent')
    monkeypatch.setattr(pc, '_is_verification_stale', lambda _: False)
    monkeypatch.setattr(pc, '_persist_verification_state', Mock())
    monkeypatch.setattr(pc, '_login_api', lambda **_: ('test-token', response({}, 201)))
    monkeypatch.setattr(pc, 'post', post)
    return post, http[2], identity, pid


def test_verification_is_reused_for_three_minutes_without_extending_its_age(verification):
    post, now, _, _ = verification
    first = pc.verify_cotizador_signature_once()
    first['sync']['enabled'] = False
    for elapsed in (10, 30, 60, 120, 179):
        now[0] = 1000 + elapsed
        assert pc.verify_cotizador_signature_once()['sync']['enabled'] is True
    assert post.call_count == 1
    now[0] = 1180
    assert pc.verify_cotizador_signature_once()['allowed'] is True
    assert post.call_count == 2


@pytest.mark.parametrize('change', ['user', 'pid'])
def test_identity_changes_require_a_fresh_verification(verification, change):
    post, _, identity, pid = verification
    pc.verify_cotizador_signature_once()
    if change == 'user':
        identity[2] = 'another-user'
    else:
        pid[0] = 'another-installation-pid'
    pc.verify_cotizador_signature_once()
    assert post.call_count == 2


def test_server_block_takes_effect_when_verification_is_due(verification):
    post, now, _, _ = verification
    pc.verify_cotizador_signature_once()
    post.return_value = response({'allowed': False, 'message': 'Bloqueado'})
    now[0] += 180
    assert pc.verify_cotizador_signature_once()['blocked'] is True


def test_verification_failure_backs_off_and_keeps_the_existing_grace_limit(verification, monkeypatch):
    post, now, _, _ = verification
    post.side_effect = ApiRequestError('offline')
    assert pc.verify_cotizador_signature_once()['status'] == 'SOFT_FAIL'
    now[0] += 30
    assert pc.verify_cotizador_signature_once()['status'] == 'SOFT_FAIL'
    assert post.call_count == 1
    monkeypatch.setattr(pc, '_is_verification_stale', lambda _: True)
    now[0] += 30
    result = pc.verify_cotizador_signature_once()
    assert result['status'] == 'HARD_FAIL'
    assert result['blocked'] is True


@pytest.mark.parametrize('active,expected', [(True, 30.0), (False, 120.0)])
def test_history_polls_less_often_while_keeping_the_wake_event(monkeypatch, active, expected):
    waits = run_loop(monkeypatch, [{'enabled': True}] * 3, active=active)
    assert waits == [25.0, expected, expected]


def test_history_errors_use_backoff_and_reset_after_success(monkeypatch):
    outcomes = [{'enabled': True, 'offline': True}] * 3 + [{'enabled': True}] * 2
    assert run_loop(monkeypatch, outcomes) == [25.0, 60.0, 120.0, 300.0, 30.0]


def test_pending_history_drains_promptly_then_returns_to_idle_polling(monkeypatch):
    outcomes = [{'enabled': True, 'more': True}, {'enabled': True}, {'enabled': True}]
    assert run_loop(monkeypatch, outcomes) == [25.0, 1.0, 30.0]


def test_history_exceptions_do_not_retry_every_five_seconds(monkeypatch):
    assert run_loop(monkeypatch, [RuntimeError('offline')] * 4) == [25.0, 60.0, 120.0, 300.0]


def run_loop(monkeypatch, outcomes, active=True):
    waits = []
    stop = SimpleNamespace(is_set=lambda: len(waits) >= len(outcomes))
    wake = SimpleNamespace(wait=lambda timeout: waits.append(timeout), clear=lambda: None)
    signal = SimpleNamespace(emit=lambda *_: None)
    window = SimpleNamespace(_api_sync_stop_event=stop, _api_sync_wake_event=wake,
        _shared_sync_active=active, _shared_sync_enabled=True,
        _db_path='unused', shared_sync_status=signal, history_refresh_requested=signal)
    monkeypatch.setattr(history, 'APP_CONFIG', {'username': 'test-user', 'store_id': '001'})
    monkeypatch.setattr(history, 'verify_cotizador_signature_once', lambda: {'status': 'ACTIVE'})
    monkeypatch.setattr(quote_sync_adapter, 'run_shared_cycle', Mock(side_effect=outcomes))
    # Stop on the next loop iteration, after each completed cycle.
    completed = [0]
    cycle = quote_sync_adapter.run_shared_cycle
    def run(*args):
        completed[0] += 1
        return cycle(*args)
    stop.is_set = lambda: completed[0] >= len(outcomes)
    monkeypatch.setattr(quote_sync_adapter, 'run_shared_cycle', run)
    history.QuoteHistoryWindow._api_sync_loop(window)
    return waits
