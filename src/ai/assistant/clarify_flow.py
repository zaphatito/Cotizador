# src/ai/assistant/clarify_flow.py
from __future__ import annotations

import re
from typing import Optional, Any

from ...logging_setup import get_logger
from .ui_dock import ChatButton
from .actions import is_no, normalize_price, normalize_qty_for_code

log = get_logger(__name__)


def _extract_choice_number(text: str) -> Optional[int]:
    t = (text or "").strip()
    if not t:
        return None
    m = re.search(r"\b([1-9])\b", t)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def _extract_code_like(text: str) -> Optional[str]:
    t = (text or "").strip()
    if not t:
        return None
    m = re.search(r"\b([A-Za-z]{1,6}\d{1,8}|\d{3,8})\b", t)
    if not m:
        return None
    return str(m.group(1) or "").strip().upper() or None


def _safe_float(x: Any, default: Optional[float] = None) -> Optional[float]:
    if x is None:
        return default
    try:
        if isinstance(x, str):
            x = x.strip().replace(",", ".")
        return float(x)
    except Exception:
        return default


def _pick_first(*vals, default=None):
    for v in vals:
        if v is not None and v != "":
            return v
    return default


class ClarifyFlowMixin:
    """
    Requiere en el controller:
      - self.st (AssistantState)
      - self.dock
      - self.w
      - self._apply_edits_to_resolved(resolved, edit_text) -> bool
      - self._build_final_preview_text(resolved) -> str
      - self._execute_pending()
      - self._cancel_all()

    Opcional (mejor UX cuando no hay candidates):
      - self._lookup_code_metadata(code: str) -> dict | None
        (puede devolver {"nombre": "...", "kind": "product|presentation|...", ...})
    """

    def _start_clarify(self, resolved: dict):
        self.st.clarify_active = True
        self.st.clarify_resolved = resolved
        self.st.clarify_queue = list((resolved or {}).get("unresolved") or [])
        self._ask_next_clarification()

    def _ask_next_clarification(self):
        if not getattr(self, "dock", None) or not self.st.clarify_active or not self.st.clarify_resolved:
            return

        if not self.st.clarify_queue:
            # listo: pasar a pending
            self.st.clarify_active = False
            self.st.clarify_resolved["unresolved"] = []

            self.st.pending_plan = {
                "action": "create_quote",
                "args": self.st.clarify_resolved.get("raw_args", {}),
            }
            self.st.pending_resolved = self.st.clarify_resolved

            self.dock.add_message(
                "assistant",
                self._build_final_preview_text(self.st.pending_resolved) + "\n\n¿Ejecuto esto?",
                buttons=[
                    ChatButton("✅ Ejecutar", self._execute_pending),
                    ChatButton("❌ Cancelar", self._cancel_all),
                ],
            )
            return

        u = self.st.clarify_queue[0]
        q = str(u.get("query") or "").strip()
        cands = u.get("candidates") or []

        lines = [f"Necesito que elijas para: '{q}'"]
        if cands:
            for i, c in enumerate(cands, start=1):
                lines.append(
                    f"{i}) {c.get('codigo','')} — {c.get('nombre','')} ({(c.get('score',0) or 0):.0%})"
                )
        else:
            lines.append("(No tengo opciones claras. Escribe el código exacto, ej: CH2156)")

        buttons: list[ChatButton] = []
        if cands:
            for i, c in enumerate(cands, start=1):
                code = str(c.get("codigo") or "").strip().upper()
                buttons.append(ChatButton(f"{i}) {code}", (lambda idx=i: self._choose_candidate(idx))))
        buttons.append(ChatButton("❌ Cancelar", self._cancel_all))

        self.dock.add_message("assistant", "\n".join(lines), buttons=buttons)

    def _choose_candidate(self, idx: int):
        if not self.st.clarify_active or not self.st.clarify_queue or not self.st.clarify_resolved:
            return

        u = self.st.clarify_queue.pop(0)
        cands = u.get("candidates") or []

        if not cands or idx < 1 or idx > len(cands):
            if self.dock:
                self.dock.add_message("assistant", "Opción inválida. Usa el número (1,2,3…) o el código exacto.")
            self.st.clarify_queue.insert(0, u)
            return

        chosen = cands[idx - 1]
        code = str(chosen.get("codigo") or "").strip().upper()

        # ✅ IMPORTANTÍSIMO: kind puede venir en chosen o en u; si no, cae a product
        kind = str(_pick_first(chosen.get("kind"), u.get("kind"), default="product")).strip().lower()

        name = str(_pick_first(chosen.get("nombre"), chosen.get("name"), default="") or "")

        # ✅ conservar qty/price originales aunque el unresolved use otras claves
        qty_raw = _pick_first(u.get("qty_raw"), u.get("qty"), u.get("cantidad"), default=1)
        price_raw = _pick_first(u.get("price_raw"), u.get("price"), u.get("precio"), default=None)

        # si manejas “modo precio” (oferta/minimo/maximo/base), guárdalo
        price_mode = _pick_first(u.get("price_mode"), u.get("precio_tipo"), u.get("priceType"), default=None)

        qty_norm = normalize_qty_for_code(self.w, code, kind, qty_raw)
        qty_f = _safe_float(qty_norm, default=1.0)

        price_norm = normalize_price(price_raw)
        price_f = _safe_float(price_norm, default=None)

        item = {
            "query": u.get("query"),
            "codigo": code,
            "nombre": name,
            "kind": kind,
            "qty": float(qty_f),
            "confidence": float(chosen.get("score") or 0.0),
            "qty_raw": qty_raw,
        }
        if price_mode:
            item["price_mode"] = str(price_mode)
        if price_raw is not None:
            item["price_raw"] = price_raw
        if price_f is not None:
            item["price"] = float(price_f)

        # ✅ si ya existía un placeholder del mismo query, lo actualizamos (evita duplicados)
        items = self.st.clarify_resolved.setdefault("items", [])
        qkey = str(u.get("query") or "").strip().upper()
        replaced = False
        for it in items:
            if str(it.get("query") or "").strip().upper() == qkey and not str(it.get("codigo") or "").strip():
                it.clear()
                it.update(item)
                replaced = True
                break
        if not replaced:
            items.append(item)

        log.info(
            "assistant.clarify_chosen query=%s code=%s kind=%s qty_raw=%s qty=%s",
            str(u.get("query") or ""),
            code,
            kind,
            str(qty_raw),
            str(qty_f),
        )
        self._ask_next_clarification()

    def _choose_manual_code(self, code: str):
        """Cuando no hay candidates y el usuario escribe el código."""
        if not self.st.clarify_active or not self.st.clarify_queue or not self.st.clarify_resolved:
            return

        u = self.st.clarify_queue.pop(0)

        meta = {}
        lookup = getattr(self, "_lookup_code_metadata", None)
        if callable(lookup):
            try:
                meta = lookup(code) or {}
            except Exception:
                meta = {}

        kind = str(_pick_first(meta.get("kind"), u.get("kind"), default="product")).strip().lower()
        name = str(_pick_first(meta.get("nombre"), meta.get("name"), default="") or "")

        qty_raw = _pick_first(u.get("qty_raw"), u.get("qty"), u.get("cantidad"), default=1)
        price_raw = _pick_first(u.get("price_raw"), u.get("price"), u.get("precio"), default=None)
        price_mode = _pick_first(u.get("price_mode"), u.get("precio_tipo"), u.get("priceType"), default=None)

        qty_norm = normalize_qty_for_code(self.w, code, kind, qty_raw)
        qty_f = _safe_float(qty_norm, default=1.0)

        price_norm = normalize_price(price_raw)
        price_f = _safe_float(price_norm, default=None)

        item = {
            "query": u.get("query"),
            "codigo": code,
            "nombre": name,
            "kind": kind,
            "qty": float(qty_f),
            "confidence": 0.0,
            "qty_raw": qty_raw,
        }
        if price_mode:
            item["price_mode"] = str(price_mode)
        if price_raw is not None:
            item["price_raw"] = price_raw
        if price_f is not None:
            item["price"] = float(price_f)

        self.st.clarify_resolved.setdefault("items", []).append(item)
        log.info("assistant.clarify_manual_code query=%s code=%s kind=%s qty_raw=%s qty=%s",
                 str(u.get("query") or ""), code, kind, str(qty_raw), str(qty_f))
        self._ask_next_clarification()

    def _handle_clarify_text(self, text: str) -> bool:
        if not self.st.clarify_active or not self.st.clarify_queue:
            return False

        if is_no(text):
            self._cancel_all()
            return True

        # permitir editar mientras se aclara
        if self.st.clarify_resolved and self._apply_edits_to_resolved(self.st.clarify_resolved, text):
            if self.dock:
                self.dock.add_message("assistant", "✅ Actualizado. Seguimos con la aclaración…")
            self._ask_next_clarification()
            return True

        n = _extract_choice_number(text)
        if n is not None:
            self._choose_candidate(n)
            return True

        code = _extract_code_like(text)
        if code:
            u = self.st.clarify_queue[0]
            cands = u.get("candidates") or []

            # ✅ si no hay candidates, aceptamos el código directo
            if not cands:
                self._choose_manual_code(code)
                return True

            # si hay candidates, debe coincidir con alguno
            for i, c in enumerate(cands, start=1):
                if str(c.get("codigo") or "").strip().upper() == code:
                    self._choose_candidate(i)
                    return True

            if self.dock:
                self.dock.add_message("assistant", "No coincide con las opciones mostradas. Usa el número (1,2,3…).")
            return True

        return False
