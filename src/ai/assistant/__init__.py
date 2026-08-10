from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .controller import AssistantController


def attach_assistant(main_window, *, catalog_manager=None, quote_events=None, app_icon=None) -> "AssistantController":
    """
    Conecta el asistente tipo chat a una QMainWindow (HistoryWindow o SistemaCotizaciones).
    """
    from .controller import AssistantController

    ctl = AssistantController(
        main_window,
        catalog_manager=catalog_manager,
        quote_events=quote_events,
        app_icon=app_icon,
    )
    ctl.install()
    return ctl


def __getattr__(name: str):
    if name == "AssistantController":
        from .controller import AssistantController

        return AssistantController
    raise AttributeError(name)
