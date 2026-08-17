from __future__ import annotations

import re


_NUMERIC_IDENTIFIER_RE = re.compile(r"^\d+$")
_TECHNICAL_API_RE = re.compile(r"^cotizador-[a-z]{2}-\d+$", re.IGNORECASE)

def normalized_server_identity(
    username: object,
    id_cotizador: object,
) -> tuple[str, str]:
    return (
        str(username or "").strip(),
        str(id_cotizador or "").strip().upper(),
    )


def has_complete_server_identity(username: object, id_cotizador: object) -> bool:
    clean_username, clean_id = normalized_server_identity(username, id_cotizador)
    return bool(clean_username and clean_id)


def is_offline_identity(username: object, id_cotizador: object) -> bool:
    clean_username, clean_id = normalized_server_identity(username, id_cotizador)
    return not clean_username and not clean_id


def validate_server_identity_pair(username: object, id_cotizador: object) -> bool:
    """Devuelve True para servidor y False para offline; rechaza pares incompletos."""

    clean_username, clean_id = normalized_server_identity(username, id_cotizador)
    if bool(clean_username) != bool(clean_id):
        raise ValueError(
            "Ingrese juntos el nombre de usuario y el ID del cotizador, "
            "o deje ambos vacíos para trabajar offline."
        )
    return bool(clean_username)


def validate_functional_identity(
    username: object,
    id_cotizador: object,
    *,
    api_username: object = "",
) -> tuple[str, str]:
    """Valida la pareja funcional antes de cualquier envío remoto."""

    clean_username, clean_id = normalized_server_identity(username, id_cotizador)
    if not clean_username or not clean_id:
        raise ValueError(
            "El usuario funcional y el ID del cotizador son obligatorios para sincronizar."
        )

    technical = str(api_username or "").strip()
    if (
        (technical and clean_username.casefold() == technical.casefold())
        or _TECHNICAL_API_RE.fullmatch(clean_username)
    ):
        raise ValueError(
            "La configuración usa la cuenta técnica del API como usuario funcional."
        )

    if _NUMERIC_IDENTIFIER_RE.fullmatch(clean_username) and not _NUMERIC_IDENTIFIER_RE.fullmatch(clean_id):
        raise ValueError(
            "La identidad del cotizador parece invertida: el usuario funcional "
            "es numérico y el ID contiene el nombre del usuario. Revise username/store_id."
        )

    return clean_username, clean_id


__all__ = [
    "has_complete_server_identity",
    "is_offline_identity",
    "normalized_server_identity",
    "validate_functional_identity",
    "validate_server_identity_pair",
]
