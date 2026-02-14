#src/ai/assistant/audit.py
from __future__ import annotations

import json
import os
import datetime


def append_audit_jsonl(path: str, record: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except Exception:
        pass

    rec = dict(record or {})
    rec.setdefault("ts", datetime.datetime.now().isoformat(timespec="seconds"))

    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        # nunca rompas el flujo por auditoría
        pass
