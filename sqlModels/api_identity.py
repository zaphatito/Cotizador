from __future__ import annotations

import base64
import hashlib
import hmac
import os

API_LOGIN_PASSWORD = (
    "123456"
)

_DEFAULT_COUNTRY = "PARAGUAY"
_DEFAULT_COMPANY = "LA CASA DEL PERFUME"

_IDENTITY_MAP: dict[tuple[str, str], tuple[int, str]] = {
    ("VENEZUELA", "EF PERFUMES"): (1000, "cotizador-VE-1"),
    ("VENEZUELA", "LA CASA DEL PERFUME"): (1001, "cotizador-VE-2"),
    ("PERU", "EF PERFUMES"): (1002, "cotizador-PE-1"),
    ("PERU", "LA CASA DEL PERFUME"): (1003, "cotizador-PE-2"),
    ("PARAGUAY", "EF PERFUMES"): (1004, "cotizador-PY-1"),
    ("PARAGUAY", "LA CASA DEL PERFUME"): (1005, "cotizador-PY-2"),
}


def _norm_country(value: str | None) -> str:
    v = str(value or "").strip().upper()
    if v in ("PY", "PARAGUAY"):
        return "PARAGUAY"
    if v in ("PE", "PERU"):
        return "PERU"
    if v in ("VE", "VENEZUELA"):
        return "VENEZUELA"
    return _DEFAULT_COUNTRY


def _norm_company(value: str | None) -> str:
    v = str(value or "").strip().upper()
    if v == "EF PERFUMES":
        return "EF PERFUMES"
    return _DEFAULT_COMPANY


def resolve_api_identity(country: str | None, company_type: str | None) -> tuple[int, str]:
    key = (_norm_country(country), _norm_company(company_type))
    return _IDENTITY_MAP.get(key, _IDENTITY_MAP[(_DEFAULT_COUNTRY, _DEFAULT_COMPANY)])


def _b64u_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64u_decode(text: str) -> bytes:
    t = str(text or "").strip()
    if not t:
        return b""
    pad = "=" * ((4 - (len(t) % 4)) % 4)
    return base64.urlsafe_b64decode((t + pad).encode("ascii"))


def is_scrypt_hash(value: str | None) -> bool:
    parts = str(value or "").strip().split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        return False
    try:
        int(parts[1])
        int(parts[2])
        int(parts[3])
    except Exception:
        return False
    return bool(parts[4] and parts[5])


def hash_password_scrypt(
    password: str,
    *,
    n: int = 16384,
    r: int = 8,
    p: int = 1,
    dklen: int = 32,
) -> str:
    secret = str(password or "").encode("utf-8")
    salt = os.urandom(16)
    digest = hashlib.scrypt(secret, salt=salt, n=int(n), r=int(r), p=int(p), dklen=int(dklen))
    return f"scrypt${int(n)}${int(r)}${int(p)}${_b64u_encode(salt)}${_b64u_encode(digest)}"


def verify_password_scrypt(password: str, encoded_hash: str) -> bool:
    try:
        parts = str(encoded_hash or "").split("$")
        if len(parts) != 6 or parts[0] != "scrypt":
            return False
        n = int(parts[1])
        r = int(parts[2])
        p = int(parts[3])
        salt = _b64u_decode(parts[4])
        expected = _b64u_decode(parts[5])
        if not salt or not expected:
            return False
        got = hashlib.scrypt(
            str(password or "").encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
        )
        return bool(hmac.compare_digest(got, expected))
    except Exception:
        return False


def build_api_settings(
    *,
    country: str | None,
    company_type: str | None,
    password_plain: str = API_LOGIN_PASSWORD,
) -> dict[str, str]:
    user_id, username = resolve_api_identity(country, company_type)
    pwd = str(password_plain or "").strip()
    if is_scrypt_hash(pwd):
        password_hash = pwd
    else:
        password_hash = hash_password_scrypt(pwd)
    return {
        "id_user_api": str(int(user_id)),
        "user_api": str(username),
        "password_api_hash": password_hash,
    }
