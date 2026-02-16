from __future__ import annotations

from typing import Any, Mapping, Sequence

from .cases import API_CASES, API_DEFAULT_HEADERS, API_DEFAULT_TIMEOUT_SECONDS
from .generic_controller import ApiResponse, GenericApiController

_controller: GenericApiController | None = None


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
    return _get_controller().request(
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
