from __future__ import annotations

import os
import re

from .country_rules import local_country_code_for

_NEW_CODE_RE = re.compile(r"^\s*([A-Za-z]{2,3})-([A-Za-z0-9]+)-(\d+)\s*$")
_LEGACY_CODE_RE = re.compile(r"^\s*([A-Za-z]{2,3})-(\d+)\s*$")
_ONLY_DIGITS_RE = re.compile(r"\d+")
_NON_ALNUM_RE = re.compile(r"[^A-Za-z0-9]+")


def normalize_country_code(country_code: str | None, *, default: str = "PY") -> str:
    s = str(country_code or "").strip().upper()
    if not s:
        return default
    s = _NON_ALNUM_RE.sub("", s)
    if not s:
        return default
    return local_country_code_for(s, default=default)


def normalize_store_id(store_id: str | None, *, default: str = "00") -> str:
    s = str(store_id or "").strip().upper()
    s = _NON_ALNUM_RE.sub("", s)
    return s or default


def extract_quote_digits(value: object) -> str:
    s = str(value or "").strip()
    if not s:
        return ""

    m_new = _NEW_CODE_RE.match(s)
    if m_new:
        return m_new.group(3)

    m_legacy = _LEGACY_CODE_RE.match(s)
    if m_legacy:
        return m_legacy.group(2)

    groups = _ONLY_DIGITS_RE.findall(s)
    if not groups:
        return ""
    return groups[-1]


def normalize_quote_digits(value: object, *, width: int = 7) -> str:
    digits = extract_quote_digits(value)
    if not digits:
        return "".zfill(max(1, int(width)))
    n = int(digits)
    return str(n).zfill(max(1, int(width)))


def quote_match_key(value: object) -> str:
    digits = extract_quote_digits(value)
    if not digits:
        return ""
    key = digits.lstrip("0")
    return key if key else "0"


def format_quote_code(
    *,
    country_code: str | None,
    store_id: str | None,
    quote_no: object,
    width: int = 7,
) -> str:
    raw_quote_no = str(quote_no or "").strip()
    complete_code = _NEW_CODE_RE.match(raw_quote_no)
    if complete_code:
        cc = normalize_country_code(complete_code.group(1))
        st = normalize_store_id(complete_code.group(2))
        qn = normalize_quote_digits(complete_code.group(3), width=width)
        return f"{cc}-{st}-{qn}"

    cc = normalize_country_code(country_code)
    st = normalize_store_id(store_id)
    qn = normalize_quote_digits(raw_quote_no, width=width)
    return f"{cc}-{st}-{qn}"


def format_quote_display_no(
    *,
    quote_code: object,
    store_id: str | None,
    width: int = 7,
) -> str:
    s = str(quote_code or "").strip()
    m_new = _NEW_CODE_RE.match(s)
    if m_new:
        st = normalize_store_id(m_new.group(2))
        qn = normalize_quote_digits(m_new.group(3), width=width)
        return f"{st}-{qn}"

    st = normalize_store_id(store_id)
    qn = normalize_quote_digits(s, width=width)
    return f"{st}-{qn}"


def extract_quote_code_from_pdf_path(pdf_path: str) -> str:
    base = os.path.splitext(os.path.basename(pdf_path or ""))[0]
    if not base:
        return ""

    if base.upper().startswith("C-"):
        body = base[2:]
    else:
        body = base

    token = body.split("_", 1)[0].strip()
    if not token:
        return ""

    if _NEW_CODE_RE.match(token):
        return token.upper()

    m_legacy = _LEGACY_CODE_RE.match(token)
    if m_legacy:
        return f"{m_legacy.group(1).upper()}-{m_legacy.group(2)}"

    return token

