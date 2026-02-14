# src/ai/assistant/dataset.py
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Optional

from ...logging_setup import get_logger

log = get_logger(__name__)


def _ensure_parent_dir(path: str) -> None:
    try:
        d = os.path.dirname(os.path.abspath(path))
        if d:
            os.makedirs(d, exist_ok=True)
    except Exception:
        pass


def _mask_pii(s: str) -> str:
    """
    Mascara dígitos largos (tel/doc) conservando lo mínimo.
    Ej:
      12345678 -> ******78
      +51987654321 -> +51*******21
    """
    if not s:
        return s

    def repl(m):
        t = m.group(0)
        if len(t) <= 4:
            return t
        keep = 2
        return ("*" * (len(t) - keep)) + t[-keep:]

    # grupos de dígitos >= 6 (incluye + y espacios)
    return re.sub(r"[+]*\d[\d\s\-]{5,}\d", repl, s)


def append_jsonl(path: str, record: Dict[str, Any]) -> None:
    try:
        _ensure_parent_dir(path)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception as e:
        log.warning("assistant.dataset_append_failed path=%s err=%s", path, e)


def make_training_example(user_text: str, plan: dict, resolved: dict) -> Dict[str, Any]:
    """
    Formato simple para dataset (opcional).
    OJO: máscara PII.
    """
    return {
        "user": _mask_pii(user_text or ""),
        "plan": plan or {},
        "resolved": resolved or {},
    }
