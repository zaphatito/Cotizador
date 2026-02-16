from __future__ import annotations

from typing import Final

# Configura aqui tus endpoints por case.
# Formato: (numero_case, "url_completa")
API_CASES: Final[tuple[tuple[int, str], ...]] = (
    # (1, "https://api.tu-dominio.com/v1/clientes"),
    # (2, "https://api.tu-dominio.com/v1/ventas/{id}"),
)

# Headers por defecto para todos los requests.
API_DEFAULT_HEADERS: Final[dict[str, str]] = {
    "Accept": "application/json",
}

# Timeout por defecto en segundos.
API_DEFAULT_TIMEOUT_SECONDS: Final[float] = 20.0

