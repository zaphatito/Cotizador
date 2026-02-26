from __future__ import annotations

from typing import Final

# DEV (activo por ahora):
API_BASE_URL: Final[str] = "http://localhost:3000/service"

# PROD (manual, cuando quieras cambiar):
# API_BASE_URL: Final[str] = "http://efperfumes.online:3005/service"

# Cases API de sesion + presupuesto.
API_CASE_LOGIN: Final[int] = 1
API_CASE_POST_PRESUPUESTO: Final[int] = 2

API_CASES: Final[tuple[tuple[int, str], ...]] = (
    (API_CASE_LOGIN, f"{API_BASE_URL}/sessions/busLogin"),
    (API_CASE_POST_PRESUPUESTO, f"{API_BASE_URL}/db/postPresupuesto"),
)

# Headers por defecto para todos los requests.
API_DEFAULT_HEADERS: Final[dict[str, str]] = {
    "Accept": "application/json",
}

# Timeout por defecto en segundos.
API_DEFAULT_TIMEOUT_SECONDS: Final[float] = 20.0

