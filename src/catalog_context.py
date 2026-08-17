from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .country_rules import local_country_code_for


def _required_upper(value: Any, *, field: str) -> str:
    normalized = str(value or "").strip().upper()
    if not normalized:
        raise ValueError(f"{field} no puede estar vacio.")
    return normalized


@dataclass(frozen=True, slots=True)
class CatalogScope:
    """Identidad inmutable de un catalogo remoto canonico."""

    country_code: str
    company_type: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "country_code", _required_upper(self.country_code, field="country_code"))
        object.__setattr__(self, "company_type", _required_upper(self.company_type, field="company_type"))

    @property
    def group_key(self) -> str:
        return f"{self.country_code}:{self.company_type}"

    @property
    def label(self) -> str:
        return f"{local_country_code_for(self.country_code)} - {self.company_type}"

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CatalogScope":
        if not isinstance(value, Mapping):
            raise TypeError("El scope debe ser un mapping.")
        return cls(
            country_code=value.get("country_code") or value.get("cod_pais") or value.get("country"),
            company_type=value.get("company_type") or value.get("empresa") or value.get("company"),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "country_code": self.country_code,
            "company_type": self.company_type,
            "group_key": self.group_key,
        }


@dataclass(frozen=True, slots=True)
class QuoteContext:
    """Contexto de negocio que debe acompanar una cotizacion durante toda su vida."""

    scope: CatalogScope
    username: str
    id_cotizador: str
    base_currency: str
    stock_policy: str = "INFORMATIONAL"

    def __post_init__(self) -> None:
        if not isinstance(self.scope, CatalogScope):
            raise TypeError("scope debe ser CatalogScope.")
        object.__setattr__(self, "username", str(self.username or "").strip())
        object.__setattr__(self, "id_cotizador", _required_upper(self.id_cotizador, field="id_cotizador"))
        object.__setattr__(self, "base_currency", _required_upper(self.base_currency, field="base_currency"))
        policy = _required_upper(self.stock_policy, field="stock_policy")
        if policy != "INFORMATIONAL":
            raise ValueError("El catalogo remoto solo admite stock_policy=INFORMATIONAL.")
        object.__setattr__(self, "stock_policy", policy)

    @classmethod
    def from_values(
        cls,
        *,
        country_code: str,
        company_type: str,
        username: str,
        id_cotizador: str,
        base_currency: str,
        stock_policy: str = "INFORMATIONAL",
    ) -> "QuoteContext":
        return cls(
            scope=CatalogScope(country_code=country_code, company_type=company_type),
            username=username,
            id_cotizador=id_cotizador,
            base_currency=base_currency,
            stock_policy=stock_policy,
        )

    def to_dict(self) -> dict[str, str]:
        return {
            **self.scope.to_dict(),
            "username": self.username,
            "id_cotizador": self.id_cotizador,
            "base_currency": self.base_currency,
            "stock_policy": self.stock_policy,
        }
