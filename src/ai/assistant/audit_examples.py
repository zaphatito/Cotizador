from __future__ import annotations
import json
from collections import deque
from typing import Any

def load_recent_plan_examples(audit_path: str, n: int = 30) -> list[dict[str, Any]]:
    """
    Devuelve ejemplos compactos (plan JSON) desde assistant_audit.jsonl.
    Nota: el audit actual no siempre guarda el texto del usuario; esto sirve igual como 'output-shots'.
    """
    if not audit_path:
        return []
    try:
        q: deque[str] = deque(maxlen=max(3000, n * 80))
        with open(audit_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = (line or "").strip()
                if line:
                    q.append(line)
    except Exception:
        return []

    out: list[dict[str, Any]] = []
    seen = set()

    for line in reversed(q):
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue

        plan = obj.get("plan")
        if not isinstance(plan, dict):
            continue

        action = str(plan.get("action") or "").strip()
        args = plan.get("args") if isinstance(plan.get("args"), dict) else {}
        key = (action, json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)[:300])
        if key in seen:
            continue
        seen.add(key)

        # compacta: solo lo que el modelo debe imitar (JSON de salida)
        out.append(
            {
                "action": action,
                "args": args,
                "needs_confirmation": bool(plan.get("needs_confirmation", False)),
            }
        )
        if len(out) >= int(n):
            break

    out.reverse()
    return out
