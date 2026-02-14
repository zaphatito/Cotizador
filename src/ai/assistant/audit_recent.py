from __future__ import annotations

import json
import os
from collections import deque
from typing import Any, Optional


_ALLOWED_KINDS = {
    "planned",
    "executed",
    "list_quotes",
    "top_clients",
    "open_quote",
    "chat",
    "edited_pending",
    "edited_from_last",
}


def _safe_load(line: str) -> Optional[dict]:
    try:
        o = json.loads(line)
        return o if isinstance(o, dict) else None
    except Exception:
        return None


def _norm_price_mode(v: Any) -> str:
    s = str(v or "").strip().lower()
    if not s:
        return ""
    if s in ("offer", "oferta", "promo", "promoción", "promocion", "sale", "offerprice"):
        return "oferta"
    if s in ("min", "mín", "mínimo", "minimo", "minimum"):
        return "min"
    if s in ("max", "máx", "máximo", "maximo", "maximum"):
        return "max"
    if s in ("base", "normal", "lista", "unitario", "unit"):
        return "base"
    if s in ("oferta", "min", "max", "base"):
        return s
    return ""


def _canon_plan(plan: dict) -> dict:
    out = dict(plan or {})
    action = str(out.get("action") or "").strip()
    args = out.get("args") if isinstance(out.get("args"), dict) else {}

    out["action"] = action
    out["args"] = args

    if "needs_confirmation" not in out or not isinstance(out.get("needs_confirmation"), bool):
        out["needs_confirmation"] = (action == "create_quote")

    if "explanation" not in out or not isinstance(out.get("explanation"), str):
        out["explanation"] = ""

    if action == "create_quote":
        items = args.get("items") if isinstance(args.get("items"), list) else []
        fixed: list[dict] = []
        seen = set()

        for it in items:
            if not isinstance(it, dict):
                continue
            q = str(it.get("query") or it.get("code") or "").strip()
            if not q:
                continue
            k = q.strip().upper()
            if k in seen:
                continue
            seen.add(k)

            qty = str(it.get("qty") or "").strip() or "1"
            pm = _norm_price_mode(it.get("price_mode"))
            o: dict[str, Any] = {"query": q, "qty": qty}
            if pm:
                o["price_mode"] = pm
            if it.get("price") is not None:
                try:
                    o["price"] = float(it.get("price"))
                except Exception:
                    pass
            fixed.append(o)

        args["items"] = fixed
        out["args"] = args

    if action in ("chat", "reply"):
        txt = str(args.get("text") or "").strip()
        out["action"] = "chat"
        out["args"] = {"text": txt}

    return out


def load_recent_examples(path: str, *, limit: int = 5) -> list[dict]:
    if not path or not os.path.exists(path):
        return []

    # leemos un poco más para filtrar y aún quedarnos con ~30 útiles
    dq: deque[dict] = deque(maxlen=max(50, int(limit) * 6))

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                rec = _safe_load(line)
                if not rec:
                    continue

                kind = str(rec.get("kind") or "").strip()
                if kind not in _ALLOWED_KINDS:
                    continue

                plan = rec.get("plan")
                if not isinstance(plan, dict):
                    continue

                # En tu audit actual casi nunca hay texto del usuario en planned/executed,
                # pero con el patch de controller esto sí quedará guardado.
                text = (
                    rec.get("user_text")
                    or rec.get("text")
                    or rec.get("edit")
                    or ""
                )
                text_s = str(text).strip()
                if len(text_s) > 220:
                    text_s = text_s[:220] + "…"

                dq.append(
                    {
                        "kind": kind,
                        "text": text_s,
                        "plan": _canon_plan(plan),
                    }
                )
    except Exception:
        return []

    out = list(dq)
    if len(out) > limit:
        out = out[-limit:]
    return out


def format_examples_for_prompt(
    examples: list[dict],
    *,
    max_examples: int = 5,
    max_chars: int = 7000,
) -> str:
    exs = [e for e in (examples or []) if isinstance(e, dict)]
    exs = exs[-max_examples:]

    blocks: list[str] = []
    used = 0

    for e in exs:
        plan = e.get("plan") if isinstance(e.get("plan"), dict) else {}
        plan_json = json.dumps(plan, ensure_ascii=False, separators=(",", ":"))
        t = str(e.get("text") or "").strip()

        if t:
            chunk = f"Usuario: {t}\nJSON: {plan_json}"
        else:
            chunk = f"JSON: {plan_json}"

        if blocks:
            chunk = "---\n" + chunk

        if blocks and (used + len(chunk) > max_chars):
            break

        blocks.append(chunk)
        used += len(chunk)

    return "\n".join(blocks).strip()
