from __future__ import annotations

from typing import Any

from .config import APP_COUNTRY


PY_UNIT_PRODUCT_CODES = frozenset({"FERO001", "FIJ002"})


def normalize_product_code(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("codigo", "id", "CODIGO", "ID"):
            code = str(value.get(key) or "").strip().upper()
            if code:
                return code
        return ""
    return str(value or "").strip().upper()


def is_py_unit_product(code_or_item: Any, *, country: str | None = None) -> bool:
    current_country = str(country or APP_COUNTRY or "").strip().upper()
    if current_country != "PARAGUAY":
        return False
    return normalize_product_code(code_or_item) in PY_UNIT_PRODUCT_CODES
