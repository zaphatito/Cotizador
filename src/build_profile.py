"""Perfiles de ejecución del Cotizador.

Producción es el perfil predeterminado. La edición piloto solo se activa de
forma explícita desde ``pilot_main.py`` mediante ``COTIZADOR_PROFILE=pilot``.
"""

import os


_PROFILE = str(os.environ.get("COTIZADOR_PROFILE") or "production").strip().lower()
IS_PILOT = _PROFILE == "pilot"
CHANNEL = "piloto-historico" if IS_PILOT else "production"
APP_TITLE = "Cotizador Piloto" if IS_PILOT else "Cotizador"
DATA_FOLDER = "Cotizaciones Piloto Historico" if IS_PILOT else "Cotizaciones"
APP_ID = "Cotizador.PilotoHistorico.1" if IS_PILOT else "Cotizador.Sistema.1"
MUTEX_NAME = (
    "Local\\CotizadorPilotoHistorico_SingleInstance"
    if IS_PILOT
    else "Local\\SistemaCotizaciones_SingleInstance"
)
SHOW_EVENT_NAME = (
    "Local\\CotizadorPilotoHistorico_ShowMainWindow"
    if IS_PILOT
    else "Local\\SistemaCotizaciones_ShowMainWindow"
)


def automatic_updates_allowed() -> bool:
    return not IS_PILOT
