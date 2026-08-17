from __future__ import annotations

import unicodedata
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
    # El API/catálogo remoto usa BOL como código válido. Se conserva BO como
    # alias interno compatible con las reglas históricas del cotizador.
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


def uses_peru_business_rules(value: Any) -> bool:
    return normalize_country_name(value) in PERU_BUSINESS_RULE_COUNTRIES
