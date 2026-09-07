"""Perfil fijo de esta distribución piloto; no depende de settings remotos."""

IS_PILOT = True
CHANNEL = "piloto-historico"
APP_TITLE = "Cotizador Piloto"
DATA_FOLDER = "Cotizaciones Piloto Historico"
APP_ID = "Cotizador.PilotoHistorico.1"
MUTEX_NAME = "Local\\CotizadorPilotoHistorico_SingleInstance"
SHOW_EVENT_NAME = "Local\\CotizadorPilotoHistorico_ShowMainWindow"


def automatic_updates_allowed() -> bool:
    return not IS_PILOT
