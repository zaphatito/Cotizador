# src/ai/assistant/controller_state.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Any

from PySide6.QtCore import QTimer


@dataclass
class AssistantState:
    # async planning
    plan_req_seq: int = 0
    active_plan_req: int = 0
    plan_text_by_id: dict[int, str] = field(default_factory=dict)
    timed_out_plan_reqs: set[int] = field(default_factory=set)
    plan_tasks: dict[int, Any] = field(default_factory=dict)
    plan_timeout_timers: dict[int, QTimer] = field(default_factory=dict)
    pending_plan: Optional[dict] = None
    pending_resolved: Optional[dict] = None
    clarify_active: bool = False
    clarify_queue: list[dict] = field(default_factory=list)
    clarify_resolved: Optional[dict] = None
    last_user_command: str = ""
    last_action: str = ""
    last_client: str = ""
    last_quote_no: str = ""
    last_plan: Optional[dict] = None
    last_resolved: Optional[dict] = None
    chat_history: list[dict[str, str]] = field(default_factory=list)
    chat_history_max: int = 14
    conversation_summary: str = ""
