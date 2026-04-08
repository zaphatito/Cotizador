from __future__ import annotations

import sys

from typing import Final

API_BASE_URL_DEV: Final[str] = "http://localhost:3000/service"
API_BASE_URL_PROD: Final[str] = "http://efperfumes.online:3005/service"


def _is_dev_environment() -> bool:
    return not bool(getattr(sys, "frozen", False))


def _resolve_api_base_url() -> str:
    return API_BASE_URL_DEV if _is_dev_environment() else API_BASE_URL_PROD


def build_api_cases(base_url: str | None = None) -> tuple[tuple[int, str], ...]:
    root = str(base_url or API_BASE_URL).rstrip("/")
    return (
        (API_CASE_LOGIN, f"{root}/sessions/busLogin"),
        (API_CASE_POST_PRESUPUESTO, f"{root}/db/postPresupuesto"),
        (API_CASE_VERIFY_COTIZADOR, f"{root}/db/verifyCotizador"),
        (API_CASE_GET_NEXT_QUOTE_CODE, f"{root}/db/getNextQuoteCode"),
        (API_CASE_GET_COUNTRY_CLIENTS, f"{root}/db/getCountryClients"),
    )


API_BASE_URL: Final[str] = _resolve_api_base_url()

# Cases API de sesion + presupuesto.
API_CASE_LOGIN: Final[int] = 1
API_CASE_POST_PRESUPUESTO: Final[int] = 2
API_CASE_VERIFY_COTIZADOR: Final[int] = 3
API_CASE_GET_NEXT_QUOTE_CODE: Final[int] = 4
API_CASE_GET_COUNTRY_CLIENTS: Final[int] = 5

API_CASES: Final[tuple[tuple[int, str], ...]] = build_api_cases(API_BASE_URL)

# Headers por defecto para todos los requests.
API_DEFAULT_HEADERS: Final[dict[str, str]] = {
    "Accept": "application/json",
}

# Timeout por defecto en segundos.
API_DEFAULT_TIMEOUT_SECONDS: Final[float] = 20.0

