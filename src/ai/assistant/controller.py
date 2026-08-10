# src/ai/assistant/controller.py
from __future__ import annotations

import copy
import datetime
import json
import os
import re
from typing import Optional

from PySide6.QtCore import Qt, QThreadPool, QTimer
from PySide6.QtGui import QKeySequence, QShortcut

from ...db_path import resolve_db_path
from ...app_window import SistemaCotizaciones
from ...logging_setup import get_logger
from ...config import (
    APP_COUNTRY,
    currency_for_country,
    secondary_currencies_for_country,
)
from ...country_rules import normalize_country_name
from ...quote_code import quote_match_key
from ...catalog_context import CatalogScope

from sqlModels.quotes_repo import ALL_STATUSES

from .controller_state import AssistantState
from .ui_dock import AssistantDock, ChatButton, AI_NAME
from .planner_ollama import OllamaPlanner
from .audit import append_audit_jsonl

from .audit_recent import load_recent_examples, format_examples_for_prompt
from .audit_examples import load_recent_plan_examples


from .planning_async import PlannerTask, WarmupTask
from .clarify_flow import ClarifyFlowMixin
from .parsing import (
    find_currency_in_text,
    fallback_parse_plan,
    parse_client_payment_edits,
    parse_item_edits,
)

from .actions import (
    is_yes, is_no,
    export_catalog_for_assistant,
    create_quote_preview, execute_create_quote,
    list_quotes_filtered, top_clients,
    normalize_qty_for_code, normalize_price,
    lookup_base_price_for_code, product_prices_text, report_text,
)
from .resolvers import effective_catalog_scope

log = get_logger(__name__)

_REPEAT_RE = re.compile(r"^\s*(hazlo\s+de\s+nuevo|otra\s+vez|repite|rep[ií]telo|lo\s+mismo|de\s+nuevo)\s*$", re.I)


def matching_quote_rows(
    rows,
    quote_reference: object = "",
    *,
    quote_id: object = None,
) -> list[dict]:
    normalized_rows = [dict(row or {}) for row in (rows or [])]
    if quote_id not in (None, ""):
        try:
            wanted_id = int(quote_id)
        except (TypeError, ValueError):
            return []
        matches = []
        for row in normalized_rows:
            try:
                row_id = int(row.get("id") or 0)
            except (TypeError, ValueError):
                continue
            if row_id == wanted_id:
                matches.append(row)
        return matches

    raw = str(quote_reference or "").strip().lstrip("#").upper()
    if not raw:
        return []
    if not raw.isdigit():
        return [
            row
            for row in normalized_rows
            if str(row.get("quote_no") or "").strip().upper() == raw
        ]

    key = quote_match_key(raw)
    return [
        row
        for row in normalized_rows
        if quote_match_key(row.get("quote_no")) == key
    ]


class AssistantController(ClarifyFlowMixin):
    def __init__(self, main_window, *, catalog_manager=None, quote_events=None, app_icon=None):
        self.w = main_window
        self.dock: Optional[AssistantDock] = None

        self.catalog_manager = catalog_manager or getattr(self.w, "catalog_manager", None)
        self.quote_events = quote_events or getattr(self.w, "quote_events", None)
        self.app_icon = app_icon

        self.assistant_name = AI_NAME

        if self.catalog_manager is not None and not hasattr(self.w, "catalog_manager"):
            try:
                setattr(self.w, "catalog_manager", self.catalog_manager)
            except Exception:
                pass
        if self.quote_events is not None and not hasattr(self.w, "quote_events"):
            try:
                setattr(self.w, "quote_events", self.quote_events)
            except Exception:
                pass

        # ✅ ahora por defecto AUTO (autoselect) para que elija cotizador-planner:latest
        model = (os.environ.get("COTI_ASSISTANT_MODEL") or "").strip() or "auto"
        keep_alive = (os.environ.get("COTI_ASSISTANT_KEEP_ALIVE") or "").strip() or "24h"

        try:
            self._plan_timeout_ms = int((os.environ.get("COTI_ASSISTANT_PLAN_TIMEOUT_MS") or "").strip() or "30000")
        except Exception:
            self._plan_timeout_ms = 30000
        self._plan_timeout_ms = max(1500, min(self._plan_timeout_ms, 60000))

        plan_timeout_s = self._plan_timeout_ms / 1000.0
        default_chat_timeout = max(1.0, plan_timeout_s - 0.8)
        try:
            chat_timeout = float((os.environ.get("COTI_ASSISTANT_CHAT_TIMEOUT") or "").strip() or str(default_chat_timeout))
        except Exception:
            chat_timeout = default_chat_timeout
        chat_timeout = max(1.0, min(chat_timeout, max(1.0, plan_timeout_s - 0.2)))

        # ✅ más ctx para few-shot (~30 ejemplos)
        self.planner = OllamaPlanner(
            model=model,
            keep_alive=keep_alive,
            think=False,
            chat_timeout=chat_timeout,
            num_ctx=2048,
            num_predict=512,
        )

        self.st = AssistantState()
        self.w._ai_db_path = resolve_db_path()

        self.audit_path = None
        try:
            from ...paths import DATA_DIR as _D
            self.audit_path = str(_D) + "/assistant_audit.jsonl"
        except Exception:
            self.audit_path = "assistant_audit.jsonl"

        # ✅ cache de ejemplos (para no leer el archivo cada tecla)
        self._audit_cache_mtime: float = 0.0
        self._audit_cache_examples: list[dict] = []
        self._audit_cache_prompt: str = ""
        self._pending_open_quote_no: str = ""
        self._pending_open_quote: dict | None = None
        self._pool = QThreadPool.globalInstance()
        self._sc_toggle: Optional[QShortcut] = None
        self._sc_esc: Optional[QShortcut] = None



    def _norm_quote_no(self, v) -> str:
        return quote_match_key(v)

    def _load_recent_examples_prompt(self, *, limit: int = 5) -> str:
        p = str(self.audit_path or "").strip()
        if not p or not os.path.exists(p):
            return ""

        try:
            st = os.stat(p)
            mtime = float(st.st_mtime)
        except Exception:
            return ""

        # si cambió el archivo, recargamos
        if mtime != self._audit_cache_mtime:
            ex = load_recent_examples(p, limit=limit)
            prompt = format_examples_for_prompt(ex, max_examples=limit, max_chars=7000)

            self._audit_cache_mtime = mtime
            self._audit_cache_examples = ex
            self._audit_cache_prompt = prompt

            log.info("assistant.audit_examples loaded=%s path=%s", len(ex), p)

        return self._audit_cache_prompt

    def install(self):
        self.dock = AssistantDock(self.w, assistant_name=self.assistant_name)
        self.w.addDockWidget(Qt.RightDockWidgetArea, self.dock)
        self.dock.hide()

        self.dock.send_text.connect(self.on_user_text)

        self.dock.minimize_requested.connect(self._hide_and_reset)
        self.dock.close_requested.connect(self._hide_and_reset)
        self.dock.maximize_requested.connect(self._toggle_maximize)

        self._sc_toggle = QShortcut(QKeySequence("Ctrl+K"), self.w, activated=self.toggle)
        self._sc_esc = QShortcut(QKeySequence("Esc"), self.w, activated=self._hide_and_reset)

        try:
            cm = self.catalog_manager or getattr(self.w, "catalog_manager", None)
            if cm is not None:
                export_catalog_for_assistant(cm)
                try:
                    cm.catalog_updated.connect(lambda *_: export_catalog_for_assistant(cm))
                except Exception:
                    pass
        except Exception:
            pass

        self._reset_chat_ui()

        try:
            self._pool.start(WarmupTask(self.planner))
        except Exception:
            pass

    def uninstall(self):
        """
        Desmonta el asistente del window (dock + atajos).
        """
        try:
            self._hide_and_reset()
        except Exception:
            pass

        for attr in ("_sc_toggle", "_sc_esc"):
            sc = getattr(self, attr, None)
            if sc is None:
                continue
            try:
                sc.setEnabled(False)
            except Exception:
                pass
            try:
                sc.activated.disconnect()
            except Exception:
                pass
            try:
                sc.setParent(None)
                sc.deleteLater()
            except Exception:
                pass
            setattr(self, attr, None)

        dock = self.dock
        if dock is not None:
            try:
                self.w.removeDockWidget(dock)
            except Exception:
                pass
            try:
                dock.setParent(None)
                dock.deleteLater()
            except Exception:
                pass
        self.dock = None

    def _welcome_text(self) -> str:
        return (
            f"¡Hola! Soy {self.assistant_name}.\n\n"
            "Ejemplos:\n"
            "• Crea cotización para Juan con dni/ruc 123, tlf 123, en Soles, pago con Yape con CH2156 x 2 precio oferta y CC001 x 0.050\n"
            "• Muéstrame las cotizaciones por pagar del mes\n"
            "• ¿Qué clientes piden más en Soles?\n"
            "• abrir 0000123\n"
            "• abre la última cotización de Juan\n"
            "• edita: quita CH1104 y agrega CC001 x0.050\n\n"
            "Tips:\n• Enter envía.\n• Shift+Enter nueva línea.\n• Ctrl+K abre/cierra el chat.\n"
            "• Al cerrar y abrir, el chat se reinicia (sin histórico por ahora)."
        )

    def _abort_active_plan(self):
        rid = int(getattr(self.st, "active_plan_req", 0) or 0)
        if rid:
            try:
                self._stop_timeout_timer(rid)
            except Exception:
                pass
            try:
                self.st.timed_out_plan_reqs.add(rid)
            except Exception:
                pass

        self.st.active_plan_req = 0
        try:
            self.st.plan_text_by_id.clear()
        except Exception:
            pass
        try:
            self.st.plan_tasks.clear()
        except Exception:
            pass

        if self.dock:
            try:
                self.dock.hide_typing()
            except Exception:
                pass

    def _reset_chat_ui(self):
        self._abort_active_plan()
        self._cancel_all()

        self.st.last_action = ""
        self.st.last_client = ""
        self.st.last_quote_no = ""
        self.st.last_user_command = ""
        self.st.last_plan = None
        self.st.last_resolved = None

        if self.dock:
            self.dock.reset(welcome_text=self._welcome_text())

    def _hide_and_reset(self):
        if self.dock and self.dock.isVisible():
            self.dock.hide()
        self._reset_chat_ui()

    def _toggle_maximize(self):
        if not self.dock:
            return

        try:
            if not self.dock.isFloating():
                self.dock.setFloating(True)
                r = self.w.geometry()
                self.dock.setGeometry(r.adjusted(40, 40, -40, -40))
            else:
                self.dock.setFloating(False)
        except Exception:
            pass

        try:
            self.dock.raise_()
            self.dock.activateWindow()
            self.dock.input.setFocus()
        except Exception:
            pass

    def toggle(self):
        if not self.dock:
            return

        if self.dock.isVisible():
            self._hide_and_reset()
            return

        self._reset_chat_ui()
        self.dock.show()
        try:
            self.dock.raise_()
            self.dock.activateWindow()
            self.dock.input.setFocus()
        except Exception:
            pass

    def _context_for_planner(self) -> dict:
        scope = effective_catalog_scope(self.w)
        base = str(getattr(self.w, "base_currency", "") or "").upper()
        secs = [str(x or "").upper() for x in (getattr(self.w, "secondary_currencies", []) or [])]
        if scope is not None and not base:
            base = currency_for_country(scope.country_code)
        if scope is not None and not secs:
            secs = secondary_currencies_for_country(scope.country_code)
        currencies = [c for c in ([base] + secs) if c]
        if not currencies:
            currencies = ["PEN", "USD"]

        statuses = sorted(list(ALL_STATUSES))
        if "" not in statuses:
            statuses.append("")

        session = {
            "last_action": getattr(self.st, "last_action", ""),
            "last_client": getattr(self.st, "last_client", ""),
            "last_quote_no": getattr(self.st, "last_quote_no", ""),
            "last_user_command": getattr(self.st, "last_user_command", ""),
        }

        examples = []
        try:
            if self.audit_path:
                examples = load_recent_plan_examples(self.audit_path, n=5)
        except Exception:
            examples = []

        country = normalize_country_name(
            getattr(scope, "country_code", "")
            or getattr(self.w, "country_name", "")
            or getattr(self.w, "country_code", "")
            or APP_COUNTRY,
            default=APP_COUNTRY,
        )

        return {
            "statuses": statuses,
            "currencies": currencies,
            "session": session,
            "country": country,
            "recent_plan_examples": examples,
            "recent_examples": self._load_recent_examples_prompt(limit=5),
        }


    def _build_final_preview_text(self, resolved: dict) -> str:
        cli = resolved.get("client") or {}
        lines = []
        lines.append("Resumen final:")
        lines.append(f"• Cliente: {cli.get('cliente','—') or '—'}")
        if cli.get("cedula"):
            lines.append(f"• Doc: {cli.get('cedula')}")
        if cli.get("telefono"):
            lines.append(f"• Tel: {cli.get('telefono')}")
        if resolved.get("payment_method"):
            lines.append(f"• Pago: {resolved.get('payment_method')}")
        if resolved.get("currency"):
            lines.append(f"• Moneda: {resolved.get('currency')}")

        lines.append("• Ítems:")
        for it in (resolved.get("items") or []):
            pr = float(it.get("price") or 0.0)
            pmode = str(it.get("price_mode") or "").strip().lower()
            extra = ""
            if pr > 0:
                extra = f" | precio unitario: {pr:g}"
            elif pmode:
                extra = f" | precio: {pmode}"
            lines.append(f"  - {it.get('codigo')} x{it.get('qty')}{extra} — {it.get('nombre','')}")

        warns = resolved.get("warnings") or []
        if warns:
            lines.append("\nAvisos:")
            for w in warns[:6]:
                lines.append(f"• {w}")
        return "\n".join(lines)
    

    def _is_report_like(self, text: str) -> bool:
        s = (text or "").strip().lower()
        if not s:
            return False

        # evita confundir creación de cotización (con códigos/cantidades) con reportes
        from .parsing import route_intent
        if route_intent(text) in ("create_quote", "edit_quote"):
            return False

        return bool(re.search(
            r"\b(m[aá]s\s+vendid|top\s*\d+|ranking|reporte|report|ventas?\b|"
            r"por\s+d[ií]a|m[eé]todo\s+de\s+pago|por\s+pago|estado\b|"
            r"por\s+pagar|pendiente|pagad[oa]|stock\b|inventario|agotad|"
            r"tier|oferta|minimo|mínimo|maximo|máximo|top\s+clientes|clientes?\s+m[aá]s)\b",
            s, flags=re.I
        ))

    def _parse_open_command(self, text: str) -> tuple[str, str]:
        """
        Retorna (referencia completa o dígitos, target)
        target: "ask" | "pdf" | "quote" | ""
        """
        s = (text or "").strip()
        if not s:
            return "", ""

        m = re.match(r"^\s*(?:abrir|abre|open)\b(.*)$", s, flags=re.I)
        if not m:
            return "", ""

        tail = (m.group(1) or "").strip().lower()

        target = "ask"
        if re.search(r"\bpdf\b", tail, flags=re.I):
            target = "pdf"
        elif re.search(r"\b(cotizaci[oó]n|cotizacion|panel)\b", tail, flags=re.I):
            target = "quote"

        mnum = re.search(
            r"([A-Za-z]{2}-[A-Za-z0-9]+-\d{1,10}|[A-Za-z]{2}-\d{1,10}|#?\d{1,10})\b",
            tail,
            flags=re.I,
        )
        if not mnum:
            return "", ""
        qn = str(mnum.group(1) or "").strip().lstrip("#").upper()
        return qn, target

    def _resolve_quote_reference(
        self,
        quote_reference: object = "",
        *,
        quote_id: object = None,
        refresh: bool = True,
    ) -> dict | None:
        if isinstance(quote_reference, dict):
            row = dict(quote_reference)
            quote_id = row.get("id")
            quote_reference = row.get("quote_no")
            refresh = False

        if refresh:
            self._ensure_list_loaded(limit=400)
        rows = getattr(self.w, "_assistant_last_list", None) or []
        matches = matching_quote_rows(
            rows,
            quote_reference,
            quote_id=quote_id,
        )
        if len(matches) == 1:
            return matches[0]

        raw = str(quote_reference or "").strip()
        if not matches:
            if self.dock:
                self.dock.add_message(
                    "assistant",
                    f"No encontre la cotizacion {raw or quote_id} en este contexto.",
                )
            return None

        if self.dock:
            options = []
            for row in matches[:8]:
                code = str(row.get("quote_no") or "").strip()
                company = str(row.get("company_type") or "").strip()
                options.append(f"- {code}" + (f" ({company})" if company else ""))
            self.dock.add_message(
                "assistant",
                "Ese numero coincide con varias cotizaciones. Usa el codigo completo:\n"
                + "\n".join(options),
            )
        return None

    def _ask_open_target(
        self,
        quote_reference: object,
        *,
        pdf_path: str = "",
        quote_id: object = None,
    ):
        if not self.dock:
            return

        row = self._resolve_quote_reference(
            quote_reference,
            quote_id=quote_id,
            refresh=not isinstance(quote_reference, dict),
        )
        if row is None:
            return
        identity = dict(row)
        qpretty = str(identity.get("quote_no") or "").strip()
        pdf_path = str(pdf_path or identity.get("pdf_path") or "").strip()

        self._pending_open_quote = identity
        self._pending_open_quote_no = qpretty

        buttons = [
            ChatButton(
                "🧾 Cotización",
                lambda _=False, q=identity: self._open_quote_target(q, "quote"),
            ),
        ]

        if pdf_path:
            buttons.append(
                ChatButton(
                    "📄 PDF",
                    lambda _=False, q=identity: self._open_quote_target(q, "pdf"),
                )
            )
        else:
            buttons.append(ChatButton("📄 PDF", lambda _=False: self.dock.add_message("assistant", "Esa cotización no tiene ruta de PDF guardada.")))

        buttons.append(ChatButton("❌ Cancelar", lambda _=False: self._clear_pending_open()))

        self.dock.add_message(
            "assistant",
            f"¿Qué quieres abrir de la cotización {qpretty}?",
            buttons=buttons
        )

    def _clear_pending_open(self):
        self._pending_open_quote_no = ""
        self._pending_open_quote = None

    def _try_resolve_pending_open(self, text: str) -> bool:
        """
        Si antes preguntamos "¿cotización o PDF?" y el usuario responde, resolvemos sin LLM.
        """
        pending = getattr(self, "_pending_open_quote", None)
        if not pending:
            return False

        s = (text or "").strip().lower()
        if not s:
            return True

        if re.search(r"\bpdf\b", s, flags=re.I):
            q = dict(pending)
            self._clear_pending_open()
            self._open_quote_target(q, "pdf")
            return True

        if re.search(r"\b(cotizaci[oó]n|cotizacion|panel)\b", s, flags=re.I):
            q = dict(pending)
            self._clear_pending_open()
            self._open_quote_target(q, "quote")
            return True

        # si escribió otra cosa, seguimos esperando elección (no lo mandes al LLM)
        if self.dock:
            self.dock.add_message("assistant", "Responde 'cotización' o 'pdf'.")
        return True

    def _open_quote_target(self, quote_reference: object, target: str, *_):
        """
        target: "quote" | "pdf"
        """
        if not self.dock:
            return

        hit = self._resolve_quote_reference(
            quote_reference,
            refresh=not isinstance(quote_reference, dict),
        )
        if hit is None:
            return
        quote_code = str(hit.get("quote_no") or "").strip()
        quote_id = hit.get("id")
        pdf = str(hit.get("pdf_path") or "").strip()

        # 1) intenta abrir por UI (selecciona fila + click botón)
        try:
            from .open_ui import open_quote_or_pdf_via_ui
            ok, msg = open_quote_or_pdf_via_ui(
                self.w,
                quote_code,
                "pdf" if target == "pdf" else "quote",
                quote_id=quote_id,
            )
            if ok:
                self.dock.add_message("assistant", msg)
                return
        except Exception:
            pass

        # 2) fallback PDF por ruta si target=pdf
        if target == "pdf" and pdf:
            try:
                if os.path.exists(pdf):
                    os.startfile(pdf)
                    self.dock.add_message("assistant", f"Abrí el PDF: {pdf}")
                    return
            except Exception:
                pass

        # 3) si no se pudo
        if target == "pdf":
            self.dock.add_message("assistant", f"No pude abrir el PDF de la cotización {quote_code}. PDF: {pdf or '—'}")
        else:
            self.dock.add_message("assistant", f"No pude abrir la cotización {quote_code} desde el histórico.")

    def _apply_edits_to_resolved(self, resolved: dict, edit_text: str) -> bool:
        if not resolved:
            return False

        changed = False
        ctx = self._context_for_planner()
        currencies = ctx.get("currencies") or []

        cur = ""
        if re.search(r"\b(moneda|currency)\b", edit_text or "", flags=re.I) or re.search(r"\ben\b", edit_text or "", flags=re.I):
            cur = find_currency_in_text(edit_text, currencies)
        if cur and str(resolved.get("currency") or "").upper().strip() != cur:
            resolved["currency"] = cur
            changed = True

        edits = parse_client_payment_edits(edit_text)
        if edits:
            cli = resolved.get("client") or {}
            if edits.get("cliente") and (cli.get("cliente") or "") != edits["cliente"]:
                cli["cliente"] = edits["cliente"]
                changed = True
            if edits.get("cedula") and (cli.get("cedula") or "") != edits["cedula"]:
                cli["cedula"] = edits["cedula"]
                changed = True
            if edits.get("telefono") and (cli.get("telefono") or "") != edits["telefono"]:
                cli["telefono"] = edits["telefono"]
                changed = True
            if edits.get("payment_method") and (resolved.get("payment_method") or "") != edits["payment_method"]:
                resolved["payment_method"] = edits["payment_method"]
                changed = True
            resolved["client"] = cli

        item_ed = parse_item_edits(edit_text)

        items = list(resolved.get("items") or [])
        by_code = {str(i.get("codigo") or "").strip().upper(): i for i in items if str(i.get("codigo") or "").strip()}

        for rp in (item_ed.get("replace") or []):
            old = str(rp.get("old") or "").strip().upper()
            new = str(rp.get("new") or "").strip().upper()
            if not old or not new:
                continue
            old_it = by_code.pop(old, None)
            if old_it is not None:
                ni = dict(old_it)
                ni["codigo"] = new
                ni["query"] = new
                by_code[new] = ni
                changed = True
            else:
                by_code.setdefault(new, {"codigo": new, "query": new, "nombre": "", "kind": "product", "qty": 1.0, "price": 0.0, "confidence": 0.0})
                changed = True

        for code in (item_ed.get("remove") or []):
            cu = str(code or "").strip().upper()
            if cu and cu in by_code:
                by_code.pop(cu, None)
                changed = True

        for sp in (item_ed.get("set_price") or []):
            cu = str(sp.get("code") or "").strip().upper()
            if not cu:
                continue
            if cu in by_code:
                price = normalize_price(sp.get("price"))
                if float(by_code[cu].get("price") or 0.0) != float(price):
                    by_code[cu]["price"] = float(price)
                    by_code[cu].pop("price_mode", None)
                    changed = True

        for sq in (item_ed.get("set_qty") or []):
            cu = str(sq.get("code") or "").strip().upper()
            if not cu:
                continue
            if cu in by_code:
                kind = str(by_code[cu].get("kind") or "product")
                qty = normalize_qty_for_code(self.w, cu, kind, sq.get("qty"))
                if float(by_code[cu].get("qty") or 0.0) != float(qty):
                    by_code[cu]["qty"] = float(qty)
                    changed = True

        for a in (item_ed.get("add") or []):
            cu = str(a.get("code") or "").strip().upper()
            if not cu:
                continue

            if cu not in by_code:
                qty = normalize_qty_for_code(self.w, cu, "product", a.get("qty", 1))
                price = normalize_price(a.get("price")) if a.get("price") is not None else 0.0
                by_code[cu] = {
                    "codigo": cu,
                    "nombre": "",
                    "kind": "product",
                    "qty": float(qty),
                    "price": float(price),
                    "confidence": 0.0,
                    "query": cu,
                }
                changed = True
            else:
                kind = str(by_code[cu].get("kind") or "product")
                delta_qty = normalize_qty_for_code(self.w, cu, kind, a.get("qty", 1))
                new_qty = float(by_code[cu].get("qty") or 0.0) + float(delta_qty)
                if float(by_code[cu].get("qty") or 0.0) != float(new_qty):
                    by_code[cu]["qty"] = float(new_qty)
                    changed = True

                if a.get("price") is not None:
                    price = normalize_price(a.get("price"))
                    if float(price) > 0 and float(by_code[cu].get("price") or 0.0) != float(price):
                        by_code[cu]["price"] = float(price)
                        by_code[cu].pop("price_mode", None)
                        changed = True

        if changed:
            resolved["items"] = list(by_code.values())

        return changed

    def _stop_timeout_timer(self, req_id: int):
        tm = self.st.plan_timeout_timers.pop(int(req_id), None)
        if tm is not None:
            try:
                tm.stop()
                tm.deleteLater()
            except Exception:
                pass

    def _on_planning_timeout(self, req_id: int):
        self._stop_timeout_timer(req_id)

        if not self.dock:
            return
        if int(req_id) != int(self.st.active_plan_req):
            return
        if int(req_id) in self.st.timed_out_plan_reqs:
            return

        try:
            self.dock.hide_typing()
        except Exception:
            pass

        text = self.st.plan_text_by_id.pop(int(req_id), "")
        self.st.timed_out_plan_reqs.add(int(req_id))
        self.st.plan_tasks.pop(int(req_id), None)

        self.dock.add_message("assistant", "⚠️ Ollama tardó demasiado; usé el parser local.")
        log.warning("assistant.planning_timeout req_id=%s text=%r", req_id, (text[:220] + "…") if len(text) > 220 else text)
        self._audit("ollama_failed", error="timeout", text=text)

        self.st.active_plan_req = 0

        ctx = self._context_for_planner()
        plan = fallback_parse_plan(text, ctx, country=ctx.get("country") or APP_COUNTRY)
        self._handle_plan_from_planner(text, plan)

    def _start_planning_async(self, text: str, ctx: dict):
        if not self.dock:
            return

        if self.st.active_plan_req:
            self._stop_timeout_timer(self.st.active_plan_req)

        self.st.plan_req_seq += 1
        req_id = self.st.plan_req_seq
        self.st.active_plan_req = req_id
        self.st.plan_text_by_id[req_id] = text
        self.st.timed_out_plan_reqs.discard(req_id)

        log.info("assistant.planning_start req_id=%s model=%s text=%r", req_id, getattr(self.planner, "model", "?"), (text or "")[:220])

        self.dock.show_typing()

        task = PlannerTask(
            req_id,
            planner=self.planner,
            text=text,
            ctx=ctx,
            today_iso=datetime.date.today().isoformat(),
            country=str(ctx.get("country") or APP_COUNTRY),
        )
        task.signals.finished.connect(self._on_planning_finished)
        self.st.plan_tasks[req_id] = task

        tm = QTimer()
        tm.setSingleShot(True)
        tm.timeout.connect(lambda rid=req_id: self._on_planning_timeout(rid))
        tm.start(self._plan_timeout_ms)
        self.st.plan_timeout_timers[req_id] = tm

        self._pool.start(task)

    def _on_planning_finished(self, req_id: int, plan_obj: object, used_fallback: bool, ollama_error: str):
        self._stop_timeout_timer(req_id)
        self.st.plan_tasks.pop(int(req_id), None)

        if int(req_id) in self.st.timed_out_plan_reqs:
            log.warning("assistant.planning_finished_after_timeout req_id=%s ignored", req_id)
            return

        if not self.dock:
            return
        if int(req_id) != int(self.st.active_plan_req):
            return

        try:
            self.dock.hide_typing()
        except Exception:
            pass

        self.st.active_plan_req = 0

        text = self.st.plan_text_by_id.pop(int(req_id), "")
        plan = plan_obj if isinstance(plan_obj, dict) else {}

        log.info("assistant.planning_finished req_id=%s used_fallback=%s err=%r", req_id, used_fallback, (ollama_error or "")[:200])

        if used_fallback:
            short = ""
            if (ollama_error or "").strip():
                parts = (ollama_error or "").splitlines()
                short = (parts[0] if parts else "")[:160]
            self.dock.add_message(
                "assistant",
                f"⚠️ Ollama falló; usé fallback. {(f'Detalle: {short}' if short else '')}\n"
                f"Revisa app.log para el error completo."
            )
            self._audit("ollama_failed", error=ollama_error or "unknown", text=text)

        self._handle_plan_from_planner(text, plan)

    def _ensure_list_loaded(self, *, limit: int = 200):
        self.w._assistant_last_list = []
        try:
            list_quotes_filtered(
                self.w,
                {
                    "limit": int(limit),
                    "date_from": "1900-01-01",
                    "date_to": "2999-12-31",
                },
            )
        except Exception:
            pass

    def _find_last_quote_for_client(self, client_query: str) -> Optional[dict]:
        cq = (client_query or "").strip().lower()
        if not cq:
            return None
        lst = getattr(self.w, "_assistant_last_list", None) or []
        for r in lst:
            c = str(r.get("client") or r.get("cliente") or "").strip().lower()
            if c and cq in c:
                if str(r.get("quote_no") or "").strip():
                    return dict(r)
        return None

    def _handle_open_quote(self, args: dict):
        which = str(args.get("which") or "last").strip()
        quote_no = str(args.get("quote_no") or "").strip()
        quote_id = args.get("quote_id")
        client_q = str(args.get("client_query") or "").strip()

        target = str(args.get("target") or "ask").strip().lower()
        if target not in ("ask", "pdf", "quote"):
            target = "ask"

        def _open(reference: object, *, selected_id: object = None):
            hit = self._resolve_quote_reference(
                reference,
                quote_id=selected_id,
                refresh=not isinstance(reference, dict),
            )
            if hit is None:
                return
            if target == "ask":
                self._ask_open_target(hit)
            else:
                self._open_quote_target(hit, target)

        if which == "by_number" and (quote_no or quote_id not in (None, "")):
            _open(quote_no, selected_id=quote_id)
            return

        if client_q:
            self._ensure_list_loaded(limit=400)
            row = self._find_last_quote_for_client(client_q)
            if row:
                _open(row)
                return
            self.dock.add_message("assistant", f"No encontré una cotización reciente para '{client_q}'. Prueba con el número (ej: abrir 0000187).")
            return

        self._ensure_list_loaded(limit=200)
        lst = getattr(self.w, "_assistant_last_list", None) or []
        if lst:
            row = dict(lst[0] or {})
            if str(row.get("quote_no") or "").strip():
                _open(row)
                return

        self.dock.add_message("assistant", "No pude abrir ninguna. Prueba: 'Muéstrame las cotizaciones' y luego 'abrir 0000187'.")

    def _handle_plan_from_planner(self, text: str, plan: dict):
        if not self.dock:
            return

        action = str(plan.get("action") or "").strip()
        args = plan.get("args") or {}
        expl = str(plan.get("explanation") or "").strip()

        if action == "reply":
            action = "chat"

        if action not in ("chat", "create_quote", "list_quotes", "top_clients", "open_quote", "edit_quote", "product_prices", "reply", "report"):
            self.dock.add_message("assistant", f"Acción no soportada: '{action}'.")
            self._audit("error", error=f"action_not_supported:{action}", plan=plan, user_text=text)
            return
        if action == "create_quote":
            self.st.last_user_command = text
            self.st.last_action = "create_quote"

            items = args.get("items") if isinstance(args.get("items"), list) else []
            if not items:
                self._audit("needs_items", plan=plan, user_text=text)
                self.dock.add_message("assistant", "Me faltan los productos. Escríbeme los códigos con cantidades, ej: CH2156 x2 y CC001 x0.050.")
                return

            preview, resolved = create_quote_preview(self.w, args)

            if resolved.get("unresolved"):
                self.dock.add_message("assistant", preview + (f"\n\nMotivo: {expl}" if expl else ""))
                self._start_clarify(resolved)
                return

            self.st.pending_plan = plan
            self.st.pending_resolved = resolved

            self.st.last_plan = plan
            self.st.last_resolved = copy.deepcopy(resolved)

            try:
                cli = resolved.get("client") or {}
                self.st.last_client = str(cli.get("cliente") or "").strip()
            except Exception:
                pass

            self._audit("planned", plan=plan, resolved=resolved, user_text=text)

            self.dock.add_message(
                "assistant",
                preview + (f"\n\n{('Motivo: ' + expl) if expl else ''}\n\n¿Ejecuto esto?"),
                buttons=[
                    ChatButton("✅ Ejecutar", self._execute_pending),
                    ChatButton("❌ Cancelar", self._cancel_all),
                ],
            )
            return

        if action == "product_prices":
            self.st.last_user_command = text
            self.st.last_action = "product_prices"
            out = product_prices_text(self.w, args if isinstance(args, dict) else {})
            self._audit("product_prices", plan=plan, text=text)
            self.dock.add_message("assistant", out)
            return
        
        if action == "report":
            self.st.last_user_command = text
            self.st.last_action = "report"
            out = report_text(self.w, args if isinstance(args, dict) else {})
            self._audit("report", plan=plan, text=text)
            self.dock.add_message("assistant", out)
            return

        if action == "chat":
            msg = str((args or {}).get("text") or "").strip() or "¿En qué te ayudo con el Cotizador?"
            self._audit("chat", plan=plan, user_text=text)
            self.dock.add_message("assistant", msg)
            return

       
        if action == "list_quotes":
            self.st.last_user_command = text
            self.st.last_action = "list_quotes"
            out = list_quotes_filtered(self.w, args)
            self._audit("list_quotes", plan=plan, user_text=text)
            self.dock.add_message("assistant", out)
            return

        if action == "top_clients":
            self.st.last_user_command = text
            self.st.last_action = "top_clients"
            out = top_clients(self.w, args)
            self._audit("top_clients", plan=plan, user_text=text)
            self.dock.add_message("assistant", out)
            return

        if action == "open_quote":
            self.st.last_user_command = text
            self.st.last_action = "open_quote"
            self._audit("open_quote", plan=plan, user_text=text)
            self._handle_open_quote(args if isinstance(args, dict) else {})
            return

        if action == "edit_quote":
            self.st.last_user_command = text
            self.st.last_action = "edit_quote"

            edits_text = str((args or {}).get("edits_text") or "").strip() or text

            if self.st.pending_resolved is not None:
                if self._apply_edits_to_resolved(self.st.pending_resolved, edits_text):
                    self._audit("edited_pending", edit=edits_text, resolved=self.st.pending_resolved, user_text=text)
                    self.dock.add_message(
                        "assistant",
                        self._build_final_preview_text(self.st.pending_resolved) + "\n\n¿Ejecuto esto?",
                        buttons=[
                            ChatButton("✅ Ejecutar", self._execute_pending),
                            ChatButton("❌ Cancelar", self._cancel_all),
                        ],
                    )
                    return
                self.dock.add_message("assistant", "No detecté cambios aplicables. Prueba: 'quita CH1104' o 'agrega CC001 x0.050'.")
                return

            if getattr(self.st, "last_resolved", None):
                self.st.pending_plan = {"action": "create_quote"}
                self.st.pending_resolved = copy.deepcopy(self.st.last_resolved)
                if self._apply_edits_to_resolved(self.st.pending_resolved, edits_text):
                    self._audit("edited_from_last", edit=edits_text, resolved=self.st.pending_resolved, user_text=text)
                    self.dock.add_message(
                        "assistant",
                        self._build_final_preview_text(self.st.pending_resolved) + "\n\n¿Ejecuto esto?",
                        buttons=[
                            ChatButton("✅ Ejecutar", self._execute_pending),
                            ChatButton("❌ Cancelar", self._cancel_all),
                        ],
                    )
                    return
                self.dock.add_message("assistant", "No detecté cambios aplicables sobre la última cotización guardada.")
                return

            self.dock.add_message("assistant", "Para editar, primero crea una cotización o abre una del histórico.")
            return

    def on_user_text(self, text: str):
        if not self.dock:
            return

        self.dock.add_message("user", text)

        if _REPEAT_RE.match(text or ""):
            if self.st.pending_resolved and self.st.last_action == "create_quote":
                self.dock.add_message(
                    "assistant",
                    self._build_final_preview_text(self.st.pending_resolved) + "\n\n¿Lo ejecuto otra vez?",
                    buttons=[
                        ChatButton("✅ Ejecutar", self._execute_pending),
                        ChatButton("❌ Cancelar", self._cancel_all),
                    ],
                )
                return

            if self.st.last_resolved and self.st.last_action == "create_quote":
                self.st.pending_plan = self.st.last_plan or {"action": "create_quote"}
                self.st.pending_resolved = copy.deepcopy(self.st.last_resolved)
                self.dock.add_message(
                    "assistant",
                    self._build_final_preview_text(self.st.pending_resolved) + "\n\n¿Lo ejecuto otra vez?",
                    buttons=[
                        ChatButton("✅ Ejecutar", self._execute_pending),
                        ChatButton("❌ Cancelar", self._cancel_all),
                    ],
                )
                return

            if self.st.last_user_command:
                ctx = self._context_for_planner()
                self._start_planning_async(self.st.last_user_command, ctx)
                return

            self.dock.add_message("assistant", "¿Qué quieres repetir exactamente? (no tengo una acción previa guardada)")
            return

        if self._handle_clarify_text(text):
            return

        if self.st.pending_plan is not None and self.st.pending_resolved is not None:
            if is_no(text):
                self._cancel_all()
                self.dock.add_message("assistant", "Cancelado. No ejecuté nada.")
                return

            if is_yes(text):
                self._execute_pending()
                return

            if self._apply_edits_to_resolved(self.st.pending_resolved, text):
                self._audit("edited_pending", edit=text, resolved=self.st.pending_resolved, user_text=text)
                self.dock.add_message(
                    "assistant",
                    self._build_final_preview_text(self.st.pending_resolved) + "\n\n¿Ejecuto esto?",
                    buttons=[
                        ChatButton("✅ Ejecutar", self._execute_pending),
                        ChatButton("❌ Cancelar", self._cancel_all),
                    ],
                )
                return

            # ✅ mantener contexto
            self.dock.add_message(
                "assistant",
                "Sigo con la cotización pendiente. Si quieres editarla, dime por ejemplo:\n"
                "• 'cantidad de CH1006 a 60'\n"
                "• 'quita CC001'\n"
                "O confirma con 'sí' / botón ✅ Ejecutar."
            )
            return


        # 1) si estamos esperando respuesta "cotización o pdf"
        if self._try_resolve_pending_open(text):
            return

        # 2) comando abrir (ambigüo pregunta; explícito abre directo)
        qn, target = self._parse_open_command(text)
        if qn:
            hit = self._resolve_quote_reference(qn)
            if hit is not None:
                if target == "ask":
                    self._ask_open_target(hit)
                elif target in ("pdf", "quote"):
                    self._open_quote_target(hit, target)
            return

        # 3) atajo: reportes sin depender del LLM
        if self._is_report_like(text):
            out = report_text(self.w, {"query": text})
            self._audit("report", text=text)
            self.dock.add_message("assistant", out)
            return



        ctx = self._context_for_planner()
        self._start_planning_async(text, ctx)

    def _cancel_all(self):
        self.st.pending_plan = None
        self.st.pending_resolved = None
        self.st.clarify_active = False
        self.st.clarify_queue = []
        self.st.clarify_resolved = None

    @staticmethod
    def _scope_from_resolved(resolved: dict | None):
        raw = (resolved or {}).get("catalog_scope")
        if not isinstance(raw, dict):
            return None
        try:
            return CatalogScope.from_mapping(raw)
        except (TypeError, ValueError):
            return None

    def _ensure_quote_window_target(self, resolved: dict | None = None):
        pending_scope = self._scope_from_resolved(resolved)
        if hasattr(self.w, "limpiar_formulario"):
            current_scope = effective_catalog_scope(self.w)
            if pending_scope is not None and current_scope != pending_scope:
                raise RuntimeError(
                    "El contexto de la cotizacion pendiente no coincide con esta ventana."
                )
            return self.w

        cm = self.catalog_manager or getattr(self.w, "catalog_manager", None)
        if cm is None:
            raise RuntimeError("No hay catalog_manager disponible para crear una cotización nueva.")

        quote_context = None
        local_catalog_id = None
        df_productos = cm.df_productos
        df_presentaciones = cm.df_presentaciones
        if bool(getattr(cm, "server_mode", False)):
            from ...quote_context_service import build_quote_context

            scope = pending_scope or effective_catalog_scope(self.w)
            if scope is None:
                from ...widgets_parts.catalog_scope_dialog import select_catalog_scope

                scope = select_catalog_scope(self.w, cm)
            if scope is None:
                raise RuntimeError("No se seleccionó un país y empresa para la cotización.")
            available = tuple(getattr(cm, "available_scopes", ()) or ())
            if available and scope not in available:
                raise RuntimeError(
                    "El pais y empresa de la cotizacion pendiente ya no estan autorizados."
                )
            self.w._assistant_catalog_scope = scope
            df_productos, df_presentaciones = cm.catalog_for_scope(scope)
            quote_context = build_quote_context(cm, scope)
        else:
            active_local_id = getattr(cm, "active_local_catalog_id", None)
            local_catalogs = tuple(getattr(cm, "available_local_catalogs", ()) or ())
            if active_local_id is not None and local_catalogs:
                local_catalog_id = int(active_local_id)
                df_productos, df_presentaciones = cm.catalog_for_local(
                    local_catalog_id
                )

        win = SistemaCotizaciones(
            df_productos=df_productos,
            df_presentaciones=df_presentaciones,
            app_icon=self.w.windowIcon(),
            catalog_manager=cm,
            quote_events=self.quote_events,
            quote_context=quote_context,
            local_catalog_id=local_catalog_id,
        )
        try:
            win._history_window = self.w
        except Exception:
            pass

        win.show()
        return win

    def _execute_pending(self):
        if not self.st.pending_resolved or not self.dock:
            return

        plan = self.st.pending_plan or {"action": "create_quote"}
        resolved = self.st.pending_resolved
        user_text = str(getattr(self.st, "last_user_command", "") or "")

        self.st.pending_plan = None
        self.st.pending_resolved = None

        try:
            target = self._ensure_quote_window_target(resolved)
            msg = execute_create_quote(target, resolved)
            self._audit("executed", plan=plan, resolved=resolved, user_text=user_text)

            self.st.last_plan = plan
            self.st.last_resolved = copy.deepcopy(resolved)
            self.dock.add_message("assistant", msg)
        except Exception as e:
            self._audit("error", plan=plan, resolved=resolved, error=str(e), user_text=user_text)
            self.dock.add_message("assistant", f"Falló la ejecución:\n{e}")

    def _open_from_last_list(self, quote_no: str):
        if not self.dock:
            return

        quote_reference = str(quote_no or "").strip()
        if not quote_reference:
            self.dock.add_message("assistant", "Dime el número. Ej: abrir 0000171")
            return

        # ✅ Asegura que exista _assistant_last_list (no depende de que el usuario haya pedido “listar” antes)
        lst = getattr(self.w, "_assistant_last_list", None) or []
        if not lst:
            self._ensure_list_loaded(limit=250)
            lst = getattr(self.w, "_assistant_last_list", None) or []

        hit = self._resolve_quote_reference(
            quote_reference,
            refresh=not bool(lst),
        )
        if hit is None:
            return

        pdf = str(hit.get("pdf_path") or "").strip()
        self.st.last_quote_no = str(hit.get("quote_no") or "").strip()

        # ✅ Opcional: abrir PDF automáticamente si existe (Windows)
        opened_pdf = False
        if pdf:
            try:
                if os.path.exists(pdf):
                    os.startfile(pdf)  # abre el PDF
                    opened_pdf = True
            except Exception:
                opened_pdf = False

        if opened_pdf:
            self.dock.add_message("assistant", f"Listo, abrí el PDF de la cotización #{hit.get('quote_no')}.")
        else:
            self.dock.add_message("assistant", f"Encontré la cotización #{hit.get('quote_no')}. PDF: {pdf or '—'}")


    def _audit(self, kind: str, **data):
        try:
            if kind in ("planned", "executed"):
                plan = data.get("plan") or {}
                resolved = data.get("resolved") or {}
                action = str((plan.get("action") or resolved.get("action") or "")).strip()
                cli = (resolved.get("client") or {})
                items = (resolved.get("items") or [])
                items_s = ", ".join([f"{i.get('codigo')}x{i.get('qty')}" for i in items[:8]])
                if len(items) > 8:
                    items_s += f" (+{len(items)-8})"
                log.info(
                    "assistant.%s action=%s client=%s doc=%s tel=%s cur=%s pay=%s items=%s",
                    kind,
                    action,
                    (cli.get("cliente") or ""),
                    (cli.get("cedula") or ""),
                    (cli.get("telefono") or ""),
                    (resolved.get("currency") or ""),
                    (resolved.get("payment_method") or ""),
                    items_s,
                )
            elif kind == "ollama_failed":
                log.error("assistant.ollama_failed error=%s text=%s", (data.get("error") or ""), (data.get("text") or "")[:250])
            else:
                log.info("assistant.%s %s", kind, json.dumps(data or {}, ensure_ascii=False, default=str)[:1200])
        except Exception:
            pass

        if not self.audit_path:
            return
        try:
            append_audit_jsonl(self.audit_path, {"kind": kind, **(data or {})})
        except Exception:
            pass
