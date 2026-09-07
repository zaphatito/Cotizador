from __future__ import annotations

import re
from dataclasses import dataclass
from typing import NamedTuple


class ApiIdentity(NamedTuple):
    technical_user_id: int
    technical_username: str
    functional_username: str
    country: str
    company_type: str
    id_cotizador: str
    telemarketing: bool


_NUMERIC_IDENTIFIER_RE = re.compile(r"^\d+$")
_TECHNICAL_API_RE = re.compile(r"^cotizador[\s_-]+[a-z]{2}[\s_-]+\d+$", re.IGNORECASE)


@dataclass(frozen=True, kw_only=True)
class ServerIdentity:
    api_username: str
    functional_username: str
    pid: str
    id_cotizador: str

    def validated(self) -> "ServerIdentity":
        username, installation = validate_functional_identity(
            self.functional_username, self.id_cotizador,
            api_username=self.api_username,
        )
        if not str(self.pid or "").strip():
            raise ValueError("El PID de la instalación es obligatorio.")
        return ServerIdentity(api_username=self.api_username,
                              functional_username=username,
                              pid=self.pid.strip(), id_cotizador=installation)

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
    if clean_username:
        validate_functional_identity(clean_username, clean_id)
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

    if len(clean_username) > 100 or len(clean_id) > 100 or any(ord(char) < 32 or ord(char) == 127 for char in clean_username + clean_id):
        raise ValueError("El usuario funcional admite hasta 100 caracteres sin controles.")

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
    "ServerIdentity",
    "has_complete_server_identity",
    "is_offline_identity",
    "normalized_server_identity",
    "validate_functional_identity",
    "validate_server_identity_pair",
]
