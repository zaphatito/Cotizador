from __future__ import annotations

import unicodedata
from typing import Any

from .config import APP_COUNTRY, CATS


PY_UNIT_PRODUCT_CODES = frozenset({"FERO001", "FIJ002"})
_PERU_EXTRA_GRAM_CATEGORIES = frozenset(
    {"FEROMONA", "FEROMONAS", "FIJADOR", "FIJADORES"}
)
_COUNTRY_ALIASES = {
    "PE": "PERU",
    "PERU": "PERU",
    "PY": "PARAGUAY",
    "PARAGUAY": "PARAGUAY",
    "VE": "VENEZUELA",
    "VENEZUELA": "VENEZUELA",
}


def _mapping_value(value: Any, *keys: str) -> Any:
    if not isinstance(value, dict):
        return None
    normalized = {str(key).strip().lower(): item for key, item in value.items()}
    for key in keys:
        candidate = normalized.get(key.lower())
        if candidate is not None and str(candidate).strip():
            return candidate
    return None


def normalize_country(value: Any) -> str:
    """Return the canonical configured country name for known aliases."""
    if isinstance(value, dict):
        value = _mapping_value(value, "country", "country_code", "cod_pais", "pais")
    normalized = str(value or "").strip()
    try:
        normalized = normalized.encode("cp1252").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        pass
    normalized = normalized.upper()
    normalized = "".join(
        character
        for character in unicodedata.normalize("NFKD", normalized)
        if not unicodedata.combining(character)
    )
    return _COUNTRY_ALIASES.get(normalized, normalized)


def normalize_product_category(value: Any) -> str:
    """Extract and normalize a product category or department."""
    if isinstance(value, dict):
        value = _mapping_value(value, "categoria", "departamento")
    return str(value or "").strip().upper()


def normalize_product_code(value: Any) -> str:
    if isinstance(value, dict):
        value = _mapping_value(value, "codigo", "id")
    return str(value or "").strip().upper()


def is_py_unit_product(code_or_item: Any, *, country: str | None = None) -> bool:
    current_country = normalize_country(APP_COUNTRY if country is None else country)
    if current_country != "PARAGUAY":
        return False
    return normalize_product_code(code_or_item) in PY_UNIT_PRODUCT_CODES


def uses_gram_quantity(category_or_item: Any, *, country: str | None = None) -> bool:
    """Whether quantity represents a weight-based product for the country."""
    current_country = normalize_country(APP_COUNTRY if country is None else country)
    category = normalize_product_category(category_or_item)
    gram_categories = {
        str(configured_category or "").strip().upper()
        for configured_category in (CATS or [])
        if str(configured_category or "").strip()
    }
    if current_country == "PERU":
        gram_categories.update(_PERU_EXTRA_GRAM_CATEGORIES)
    if category not in gram_categories:
        return False
    return not is_py_unit_product(category_or_item, country=current_country)
