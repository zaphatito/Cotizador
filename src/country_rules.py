from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


SUPPORTED_COUNTRIES: tuple[str, ...] = (
    "PARAGUAY",
    "PERU",
    "VENEZUELA",
    "BOLIVIA",
)

_COUNTRY_ALIASES: dict[str, str] = {
    "PY": "PARAGUAY",
    "PARAGUAY": "PARAGUAY",
    "PE": "PERU",
    "PERU": "PERU",
    "VE": "VENEZUELA",
    "VENEZUELA": "VENEZUELA",
    "BO": "BOLIVIA",
    # BOL es la referencia local histórica del cotizador; BO es el código
    # que usa el API para identificar el país.
    "BOL": "BOLIVIA",
    "BOLIVIA": "BOLIVIA",
}

_COUNTRY_CODES: dict[str, str] = {
    "PARAGUAY": "PY",
    "PERU": "PE",
    "VENEZUELA": "VE",
    "BOLIVIA": "BO",
}

# Bolivia comparte las reglas operativas de Peru. El perfil PDF se resuelve
# por separado para que BO conserve plantilla y layout propios.
PERU_BUSINESS_RULE_COUNTRIES = frozenset({"PERU", "BOLIVIA"})


@dataclass(frozen=True, slots=True)
class CountryProfile:
    code: str
    name: str
    base_currency: str
    secondary_currencies: tuple[str, ...]


_PROFILES = {
    "BOLIVIA": CountryProfile("BO", "BOLIVIA", "BOB", ("PEN", "USD")),
    "PERU": CountryProfile("PE", "PERU", "PEN", ("BOB", "USD")),
    "PARAGUAY": CountryProfile("PY", "PARAGUAY", "PYG", ("ARS", "BRL", "USD")),
    "VENEZUELA": CountryProfile("VE", "VENEZUELA", "USD", ("VES",)),
}


def country_profile(value: Any) -> CountryProfile:
    """Perfil estricto: nunca resolver un país desconocido como Paraguay."""
    profile = _PROFILES.get(normalize_country_name(value))
    if profile is None:
        raise ValueError("El país de la cotización está vacío o no está soportado.")
    return profile


def historical_country_code(header: Mapping[str, Any]) -> str:
    """Resolver país sin configuración global ni escritura sobre el histórico."""
    raw_country = str(header.get("country_code") or "").strip()
    country = country_profile(raw_country).code if raw_country else ""
    code = str(header.get("quote_no") or "").strip()
    match = re.fullmatch(r"([A-Za-z]{2,3})-(?:[A-Za-z0-9]+-)?[0-9]+", code)
    code_country = country_profile(match.group(1)).code if match else ""
    if country and code_country and country != code_country:
        raise ValueError("El país guardado no coincide con el código de la cotización.")
    if not country and not code_country:
        raise ValueError("No se puede determinar el país de la cotización histórica.")
    return country or code_country


def normalize_country_name(value: Any, *, default: str = "") -> str:
    if isinstance(value, dict):
        normalized_map = {
            str(key).strip().lower(): item for key, item in value.items()
        }
        value = next(
            (
                normalized_map.get(key)
                for key in ("country", "country_code", "cod_pais", "pais")
                if normalized_map.get(key) is not None
                and str(normalized_map.get(key)).strip()
            ),
            "",
        )

    normalized = str(value or "").strip()
    if not normalized:
        return str(default or "").strip().upper()
    try:
        normalized = normalized.encode("cp1252").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        pass
    normalized = "".join(
        character
        for character in unicodedata.normalize("NFKD", normalized.upper())
        if not unicodedata.combining(character)
    )
    return _COUNTRY_ALIASES.get(normalized, normalized)


def country_code_for(value: Any, *, default: str = "PY") -> str:
    country = normalize_country_name(value)
    if country in _COUNTRY_CODES:
        return _COUNTRY_CODES[country]

    raw = str(value or "").strip().upper()
    if raw in _COUNTRY_CODES.values():
        return raw
    return str(default or "PY").strip().upper() or "PY"


def local_country_code_for(value: Any, *, default: str = "PY") -> str:
    """Devuelve el código usado por las referencias locales del cotizador."""
    if normalize_country_name(value) == "BOLIVIA":
        return "BOL"
    return country_code_for(value, default=default)


def uses_peru_business_rules(value: Any) -> bool:
    return normalize_country_name(value) in PERU_BUSINESS_RULE_COUNTRIES
