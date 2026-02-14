from __future__ import annotations

from .controller import AssistantController


def attach_assistant(main_window, *, catalog_manager=None, quote_events=None, app_icon=None) -> AssistantController:
    """
    Conecta el asistente tipo chat a una QMainWindow (HistoryWindow o SistemaCotizaciones).
    """
    ctl = AssistantController(
        main_window,
        catalog_manager=catalog_manager,
        quote_events=quote_events,
        app_icon=app_icon,
    )
    ctl.install()
    return ctl
