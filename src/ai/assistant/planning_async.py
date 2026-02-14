# src/ai/assistant/planning_async.py
from __future__ import annotations

import datetime
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal

from ...logging_setup import get_logger
from .plan_builder import build_plan

log = get_logger(__name__)


class PlannerSignals(QObject):
    finished = Signal(int, object, bool, str)  # req_id, plan(dict), used_fallback, ollama_error


class PlannerTask(QRunnable):
    def __init__(self, req_id: int, *, planner: Any, text: str, ctx: dict, today_iso: str, country: str):
        super().__init__()
        self.setAutoDelete(True)
        self.req_id = int(req_id)
        self.planner = planner
        self.text = text or ""
        self.ctx = ctx or {}
        self.today_iso = today_iso or datetime.date.today().isoformat()
        self.country = country or ""
        self.signals = PlannerSignals()

    def run(self):
        used_fallback = True
        err = ""
        try:
            plan, used_fallback, err = build_plan(
                self.text,
                ctx=self.ctx,
                planner=self.planner,
                today_iso=self.today_iso,
                country=self.country,
            )
        except Exception as e:
            # esto es un error de nuestro código (no del LLM)
            used_fallback = True
            err = str(e).strip()
            plan = {"action": "create_quote", "args": {}, "needs_confirmation": True, "explanation": "Error interno en build_plan (ver logs)."}

            log.exception("assistant.planning_async.build_plan_crash req_id=%s err=%s", self.req_id, err)

        self.signals.finished.emit(self.req_id, plan, bool(used_fallback), err or "")


class WarmupTask(QRunnable):
    def __init__(self, planner: Any):
        super().__init__()
        self.setAutoDelete(True)
        self.planner = planner

    def run(self):
        try:
            ok = bool(self.planner.warmup(timeout=180.0))
            log.info("assistant.ollama_warmup ok=%s model=%s", ok, getattr(self.planner, "model", "?"))
        except Exception as e:
            log.warning("assistant.ollama_warmup failed: %s", e)
