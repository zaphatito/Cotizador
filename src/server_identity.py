from __future__ import annotations


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


__all__ = [
    "has_complete_server_identity",
    "is_offline_identity",
    "normalized_server_identity",
    "validate_server_identity_pair",
]
