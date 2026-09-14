from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
import math
import threading
import time
from typing import Any, Mapping, Sequence

from .cases import API_CASES, API_CASE_LOGIN, API_DEFAULT_HEADERS, API_DEFAULT_TIMEOUT_SECONDS
from .generic_controller import ApiRequestError, ApiResponse, GenericApiController

_controller: GenericApiController | None = None
_login_sessions: dict = {}
_login_lock = threading.RLock()


def _token_lifetime(payload: Mapping) -> float:
    limits = [3600.0]
    try:
        seconds = float(payload.get('access_token_expires_in', 0))
        if math.isfinite(seconds):
            limits.append(seconds)
    except (ValueError, TypeError):
        return 0.0
    # La expiración del JWT puede ser anterior a la anunciada por el login.
    # Solo sirve para renovar; la autorización sigue validándose en el servidor.
    try:
        encoded = str(payload.get('access_token', '')).split('.')[1]
        claims = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
        remaining = float(claims['exp']) - time.time()
        if math.isfinite(remaining):
            limits.append(remaining)
    except (ValueError, TypeError, KeyError, IndexError, UnicodeError):
        pass
    lifetime = min(limits)
    return max(0.0, lifetime - min(30.0, lifetime * 0.1))


def _cached_login(client, key, options, *, rejected_token=None):
    # Un único login también cuando histórico y stock arrancan a la vez.
    with _login_lock:
        current = _login_sessions.get(key)
        if (current and current['expires_at'] > time.monotonic()
                and current['token'] != rejected_token):
            return deepcopy(current['response'])
        _login_sessions.pop(key, None)
        started = time.monotonic()
        result = client.request(**options)
        payload = result.data if isinstance(result.data, Mapping) else {}
        token = str(payload.get('access_token') or '').strip()
        lifetime = _token_lifetime(payload)
        if result.ok and token and lifetime > 0:
            _login_sessions[key] = dict(client=client, token=token,
                response=deepcopy(result), expires_at=started + lifetime,
                options=deepcopy(options))
        return result


def _session_for(client, headers):
    authorization = next((str(value) for name, value in (headers or {}).items()
                          if str(name).lower() == 'authorization'), '')
    with _login_lock:
        return next(((key, session) for key, session in _login_sessions.items()
                     if session['client'] is client
                     and authorization == f"Bearer {session['token']}"), None)


def _discard_session(key, token):
    with _login_lock:
        if (_login_sessions.get(key) or {}).get('token') == token:
            _login_sessions.pop(key, None)


def _get_controller() -> GenericApiController:
    global _controller
    if _controller is None:
        _controller = GenericApiController(
            cases=API_CASES,
            default_headers=API_DEFAULT_HEADERS,
            default_timeout=API_DEFAULT_TIMEOUT_SECONDS,
            logger_name="src.api.controller",
        )
    return _controller


def reload_cases(cases: tuple[tuple[int, str], ...] | None = None) -> None:
    global _controller
    with _login_lock:
        _login_sessions.clear()
    target_cases = API_CASES if cases is None else cases
    if _controller is None:
        _controller = GenericApiController(
            cases=target_cases,
            default_headers=API_DEFAULT_HEADERS,
            default_timeout=API_DEFAULT_TIMEOUT_SECONDS,
            logger_name="src.api.controller",
        )
        return
    _controller.set_cases(target_cases)


def request(
    method: str,
    case: int,
    *,
    params: Mapping[str, Any] | None = None,
    data: dict[str, Any] | list[Any] | str | bytes | bytearray | None = None,
    json_data: Any | None = None,
    headers: Mapping[str, Any] | None = None,
    timeout: float | int | None = None,
    expected_status: Sequence[int] | None = (200, 201, 202, 204),
    path_params: Mapping[str, Any] | None = None,
    raise_for_status: bool = True,
) -> ApiResponse:
    client = _get_controller()
    options = dict(
        method=method,
        case=case,
        params=params,
        data=data,
        json_data=json_data,
        headers=headers,
        timeout=timeout,
        expected_status=expected_status,
        path_params=path_params,
        raise_for_status=raise_for_status,
    )
    if (method.upper() == 'POST' and case == API_CASE_LOGIN
            and isinstance(json_data, Mapping)
            and all(name in json_data for name in ('id', 'username', 'password'))):
        key = (id(client), str(json_data['id']), str(json_data['username']),
               hashlib.sha256(str(json_data['password']).encode('utf-8')).digest())
        return _cached_login(client, key, options)

    session = _session_for(client, headers)
    try:
        result = client.request(**options)
    except ApiRequestError as exc:
        if not session or not exc.response or exc.response.status_code != 401:
            raise
    else:
        if not session or result.status_code != 401:
            return result

    key, previous = session
    renewed = _cached_login(client, key, previous['options'],
                            rejected_token=previous['token'])
    token = str(renewed.data.get('access_token') or '') if isinstance(renewed.data, Mapping) else ''
    if not renewed.ok or not token:
        raise ApiRequestError('No se pudo renovar la sesión del API.', response=renewed)
    options['headers'] = {name: value for name, value in (headers or {}).items()
                          if str(name).lower() != 'authorization'}
    options['headers']['Authorization'] = f'Bearer {token}'
    # Repetir solo un rechazo de autenticación, conservando datos e idempotencia.
    try:
        result = client.request(**options)
    except ApiRequestError as exc:
        if exc.response and exc.response.status_code == 401:
            _discard_session(key, token)
        raise
    if result.status_code == 401:
        _discard_session(key, token)
    return result


def get(
    case: int,
    *,
    params: Mapping[str, Any] | None = None,
    headers: Mapping[str, Any] | None = None,
    timeout: float | int | None = None,
    expected_status: Sequence[int] | None = (200,),
    path_params: Mapping[str, Any] | None = None,
    raise_for_status: bool = True,
) -> ApiResponse:
    return request(
        "GET",
        case,
        params=params,
        headers=headers,
        timeout=timeout,
        expected_status=expected_status,
        path_params=path_params,
        raise_for_status=raise_for_status,
    )


def post(
    case: int,
    *,
    data: dict[str, Any] | list[Any] | str | bytes | bytearray | None = None,
    json_data: Any | None = None,
    params: Mapping[str, Any] | None = None,
    headers: Mapping[str, Any] | None = None,
    timeout: float | int | None = None,
    expected_status: Sequence[int] | None = (200, 201, 202),
    path_params: Mapping[str, Any] | None = None,
    raise_for_status: bool = True,
) -> ApiResponse:
    return request(
        "POST",
        case,
        params=params,
        data=data,
        json_data=json_data,
        headers=headers,
        timeout=timeout,
        expected_status=expected_status,
        path_params=path_params,
        raise_for_status=raise_for_status,
    )


def put(
    case: int,
    *,
    data: dict[str, Any] | list[Any] | str | bytes | bytearray | None = None,
    json_data: Any | None = None,
    params: Mapping[str, Any] | None = None,
    headers: Mapping[str, Any] | None = None,
    timeout: float | int | None = None,
    expected_status: Sequence[int] | None = (200, 204),
    path_params: Mapping[str, Any] | None = None,
    raise_for_status: bool = True,
) -> ApiResponse:
    return request(
        "PUT",
        case,
        params=params,
        data=data,
        json_data=json_data,
        headers=headers,
        timeout=timeout,
        expected_status=expected_status,
        path_params=path_params,
        raise_for_status=raise_for_status,
    )


def patch(
    case: int,
    *,
    data: dict[str, Any] | list[Any] | str | bytes | bytearray | None = None,
    json_data: Any | None = None,
    params: Mapping[str, Any] | None = None,
    headers: Mapping[str, Any] | None = None,
    timeout: float | int | None = None,
    expected_status: Sequence[int] | None = (200, 204),
    path_params: Mapping[str, Any] | None = None,
    raise_for_status: bool = True,
) -> ApiResponse:
    return request(
        "PATCH",
        case,
        params=params,
        data=data,
        json_data=json_data,
        headers=headers,
        timeout=timeout,
        expected_status=expected_status,
        path_params=path_params,
        raise_for_status=raise_for_status,
    )


def delete(
    case: int,
    *,
    data: dict[str, Any] | list[Any] | str | bytes | bytearray | None = None,
    json_data: Any | None = None,
    params: Mapping[str, Any] | None = None,
    headers: Mapping[str, Any] | None = None,
    timeout: float | int | None = None,
    expected_status: Sequence[int] | None = (200, 202, 204),
    path_params: Mapping[str, Any] | None = None,
    raise_for_status: bool = True,
) -> ApiResponse:
    return request(
        "DELETE",
        case,
        params=params,
        data=data,
        json_data=json_data,
        headers=headers,
        timeout=timeout,
        expected_status=expected_status,
        path_params=path_params,
        raise_for_status=raise_for_status,
    )
