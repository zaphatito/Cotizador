# src/ai/assistant/planner_ollama.py
from __future__ import annotations

import json
import os
import re
import sys
import time
import shutil
import subprocess
import urllib.request
import urllib.error
from urllib.parse import urlparse, urlunparse
from typing import Optional, Any

from ...logging_setup import get_logger

log = get_logger(__name__)

IS_WIN = os.name == "nt"


def _extract_json_obj(text: str) -> Optional[dict]:
    if not text:
        return None

    try:
        o = json.loads(text)
        if isinstance(o, dict):
            return o
    except Exception:
        pass

    s = text
    start = s.find("{")
    if start < 0:
        return None

    depth = 0
    for i in range(start, len(s)):
        ch = s[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                chunk = s[start : i + 1]
                try:
                    o = json.loads(chunk)
                    return o if isinstance(o, dict) else None
                except Exception:
                    return None

    return None


def _coerce_number(x: Any) -> Optional[float]:
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip()
    if not s:
        return None
    s = s.replace(",", ".")
    s = re.sub(r"[^\d\.\-]", "", s)
    if not s:
        return None
    try:
        return float(s)
    except Exception:
        return None


def _coerce_int(x: Any, default: int) -> int:
    try:
        n = _coerce_number(x)
        if n is None:
            return int(default)
        return int(round(float(n)))
    except Exception:
        return int(default)


def _http_json(url: str, *, method: str = "GET", payload: Optional[dict] = None, timeout: float = 60.0) -> dict:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        raise RuntimeError(f"Ollama HTTPError {e.code}: {body or str(e)}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Ollama no disponible: {e}")
    except Exception as e:
        raise RuntimeError(f"Ollama error de red: {e}")

    try:
        r = json.loads(raw)
        return r if isinstance(r, dict) else {}
    except Exception:
        raise RuntimeError(f"Ollama devolvió respuesta no-JSON: {raw[:300]}")


def _parse_base_and_api(url_or_base: str) -> tuple[str, str]:
    u = (url_or_base or "").strip()
    if not u:
        u = "http://127.0.0.1:11434"

    p = urlparse(u)
    if not p.scheme:
        p = urlparse("http://" + u)

    base_host = urlunparse((p.scheme, p.netloc, "", "", "", ""))
    api_base = base_host + "/api"
    return base_host, api_base


def _guess_ollama_exe() -> Optional[str]:
    env_exe = (os.environ.get("OLLAMA_EXE") or "").strip()
    if env_exe and os.path.exists(env_exe):
        return env_exe

    base_dir = ""
    try:
        if getattr(sys, "frozen", False):
            base_dir = os.path.dirname(sys.executable)
        else:
            base_dir = os.path.dirname(os.path.abspath(sys.argv[0] or "")) or os.getcwd()
    except Exception:
        base_dir = os.getcwd()

    cand = os.path.join(base_dir, "ollama.exe" if IS_WIN else "ollama")
    if os.path.exists(cand):
        return cand
    cand2 = os.path.join(base_dir, "vendor", "ollama", "ollama.exe" if IS_WIN else "ollama")
    if os.path.exists(cand2):
        return cand2
    cand3 = os.path.join(base_dir, "vendor", "ollama", "bin", "ollama.exe" if IS_WIN else "ollama")
    if os.path.exists(cand3):
        return cand3

    return shutil.which("ollama")


def _spawn_ollama_serve_hidden(ollama_exe: str, *, host: str = "127.0.0.1:11434") -> None:
    env = os.environ.copy()
    env["OLLAMA_HOST"] = host

    cmd = [ollama_exe, "serve"]

    kwargs: dict[str, Any] = {
        "env": env,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }

    if IS_WIN:
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        kwargs["startupinfo"] = si
        kwargs["close_fds"] = False
    else:
        kwargs["close_fds"] = True

    try:
        subprocess.Popen(cmd, **kwargs)
    except Exception as e:
        raise RuntimeError(f"No pude iniciar 'ollama serve' con '{ollama_exe}': {e}")


def _normalize_qty_text(qty_raw: Any) -> str:
    qty_s = str(qty_raw).strip() if qty_raw is not None else ""
    if not qty_s:
        return "1"

    if "," in qty_s and "." not in qty_s:
        qty_s = qty_s.replace(",", ".")

    qty_s2 = re.sub(r"[^0-9\.\-]", "", qty_s)
    if not qty_s2 or qty_s2 in ("-", ".", "-."):
        return "1"

    if qty_s2.count("-") > 1:
        qty_s2 = qty_s2.replace("-", "")

    if qty_s2.count(".") > 1:
        head, *rest = qty_s2.split(".")
        qty_s2 = head + "." + "".join(rest)

    return qty_s2


def _normalize_price_mode(pmode_raw: Any) -> str:
    pmode = str(pmode_raw or "").strip().lower()
    if not pmode:
        return ""

    if pmode in ("oferta", "promo", "promoción", "promocion", "sale", "offer"):
        return "oferta"
    if pmode in ("min", "mín", "mínimo", "minimo", "minimum"):
        return "min"
    if pmode in ("max", "máx", "máximo", "maximo", "maximum"):
        return "max"
    if pmode in ("base", "normal", "lista", "unitario", "unit"):
        return "base"

    if pmode in ("oferta", "min", "max", "base"):
        return pmode

    return ""


def _normalize_open_target(t_raw: Any) -> str:
    """
    Normaliza target para open_quote:
      - "pdf" | "quote" | "ask"
    """
    t = str(t_raw or "").strip().lower()
    if not t:
        return ""
    if t in ("pdf", "quote", "ask"):
        return t
    # aliases comunes
    if t in ("cotizacion", "cotización", "panel", "ventana"):
        return "quote"
    return ""


class OllamaPlanner:
    # ✅ Priorizamos tu modelo preentrenado
    DEFAULT_FAST_MODELS = [
        "cotizador-planner:latest",
    ]

    def __init__(
        self,
        model: str = "auto",
        url: str = "http://127.0.0.1:11434/api/chat",
        *,
        auto_start_server: bool = True,
        keep_alive: Any = "24h",
        think: Any = False,
        ollama_exe: Optional[str] = None,
        chat_timeout: float = 12.0,
        num_predict: int = 512,
        num_ctx: int = 4096,  # ✅ más contexto para ~30 ejemplos
    ):
        self.model = (model or "auto").strip()
        self.url = url

        self.base_host, self.api_base = _parse_base_and_api(url)
        self.chat_url = self.api_base + "/chat"
        self.tags_url = self.api_base + "/tags"

        self.auto_start_server = bool(auto_start_server)
        self.keep_alive = keep_alive
        self.think = think
        self.ollama_exe = ollama_exe
        self.chat_timeout = float(chat_timeout or 12.0)

        self.num_predict = int(num_predict or 512)
        self.num_ctx = int(num_ctx or 2048)

        self._did_autoselect = False

    def _list_available_models(self) -> list[str]:
        r = _http_json(self.tags_url, method="GET", payload=None, timeout=2.0)
        models = r.get("models") or []
        out: list[str] = []
        if isinstance(models, list):
            for m in models:
                if isinstance(m, dict):
                    name = str(m.get("name") or "").strip()
                    if name:
                        out.append(name)
        return out

    def _maybe_autoselect_model(self):
        if self._did_autoselect:
            return
        self._did_autoselect = True

        if self.model.lower() != "auto":
            return

        preferred_env = (os.environ.get("COTIZADOR_ASSISTANT_MODELS") or "").strip()
        preferred = [x.strip() for x in preferred_env.split(",") if x.strip()] if preferred_env else list(self.DEFAULT_FAST_MODELS)

        try:
            avail = self._list_available_models()
        except Exception as e:
            log.warning("ollama.autoselect: no pude leer tags: %s", e)
            return

        if not avail:
            return

        for cand in preferred:
            if cand in avail:
                self.model = cand
                log.info("ollama.autoselect picked model=%s", self.model)
                return

        self.model = avail[0]
        log.info("ollama.autoselect fallback model=%s", self.model)

    def warmup(self, *, timeout: float = 180.0) -> bool:
        try:
            self._ensure_server()
            self._maybe_autoselect_model()

            payload = {
                "model": self.model,
                "stream": False,
                "format": "json",
                "messages": [
                    {"role": "system", "content": "Devuelve SOLO un JSON válido: {\"ok\": true}."},
                    {"role": "user", "content": "ping"},
                ],
                "options": {
                    "temperature": 0.0,
                    "num_predict": 16,
                    "num_ctx": self.num_ctx,
                },
                "keep_alive": self.keep_alive,
                "think": False,
            }
            _http_json(self.chat_url, method="POST", payload=payload, timeout=float(timeout or 180.0))
            return True
        except Exception as e:
            log.warning("ollama.warmup failed: %s", e)
            return False

    def plan(self, user_text: str, *, today_iso: str, context: dict) -> dict:
        self._ensure_server()
        self._maybe_autoselect_model()

        system = self._system_prompt(today_iso=today_iso, context=context)

        payload = {
            "model": self.model,
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_text},
            ],
            "options": {
                "temperature": 0.0,
                "num_predict": self.num_predict,
                "num_ctx": self.num_ctx,
            },
            "keep_alive": self.keep_alive,
            "think": self.think,
        }

        obj = self._call_chat(payload)
        plan = self._postprocess_plan(obj, context=context)
        self._validate_plan(plan, context=context)
        return plan

    def _ensure_server(self) -> None:
        if self._ping_tags():
            return

        if not self.auto_start_server:
            raise RuntimeError("Ollama no responde en localhost (server apagado).")

        exe = self.ollama_exe or _guess_ollama_exe()
        if not exe:
            raise RuntimeError(
                "Ollama no responde y no encontré 'ollama'/'ollama.exe'. "
                "Instálalo o pon OLLAMA_EXE apuntando al ejecutable."
            )

        _spawn_ollama_serve_hidden(exe, host=urlparse(self.base_host).netloc or "127.0.0.1:11434")

        for _ in range(60):
            if self._ping_tags():
                return
            time.sleep(0.25)

        raise RuntimeError("Intenté iniciar Ollama pero no respondió (timeout).")

    def _ping_tags(self) -> bool:
        try:
            r = _http_json(self.tags_url, method="GET", payload=None, timeout=2.0)
            return isinstance(r.get("models"), list)
        except Exception:
            return False

    def _call_chat(self, payload: dict) -> dict:
        r = _http_json(self.chat_url, method="POST", payload=payload, timeout=self.chat_timeout)

        done_reason = str(r.get("done_reason") or r.get("done") or "").lower()
        if done_reason == "length":
            raise RuntimeError("Ollama truncó la salida (done_reason=length). Sube num_predict o reduce el prompt.")

        msg = (r.get("message") or {}) if isinstance(r, dict) else {}
        content = str(msg.get("content") or "").strip()

        obj = _extract_json_obj(content)
        if obj and isinstance(obj, dict) and ("action" in obj) and ("args" in obj):
            return obj

        raise RuntimeError(f"El modelo no devolvió JSON válido (ActionPlan). content[:200]={content[:200]!r}")

    def _postprocess_plan(self, plan: dict, *, context: dict) -> dict:
        out = dict(plan or {})
        action = str(out.get("action") or "").strip()
        args = out.get("args") if isinstance(out.get("args"), dict) else {}

        out["action"] = action
        out["args"] = args

        if "needs_confirmation" not in out or not isinstance(out.get("needs_confirmation"), bool):
            out["needs_confirmation"] = (action == "create_quote")

        if "limit" in args:
            args["limit"] = _coerce_int(args.get("limit"), 20)

        if action == "create_quote":
            items = args.get("items")
            if not isinstance(items, list):
                items = []

            fixed: list[dict] = []
            seen = set()

            for it in items:
                if not isinstance(it, dict):
                    continue

                q = str(it.get("query") or "").strip()
                if not q:
                    continue

                q_key = q.strip().upper()
                if q_key in seen:
                    continue
                seen.add(q_key)

                qty_s = _normalize_qty_text(it.get("qty"))
                price = _coerce_number(it.get("price"))
                pmode = _normalize_price_mode(it.get("price_mode"))

                o = {"query": q, "qty": qty_s}
                if price is not None:
                    o["price"] = float(price)
                if pmode:
                    o["price_mode"] = pmode

                fixed.append(o)

            args["items"] = fixed
            out["args"] = args

        if action in ("chat", "reply"):
            txt = str(args.get("text") or "").strip()
            out["action"] = "chat"
            out["args"] = {"text": txt}

        if action == "edit_quote":
            et = str(args.get("edits_text") or "").strip()
            out["args"]["edits_text"] = et

        if action == "open_quote":
            which = str(args.get("which") or "").strip().lower()
            if which not in ("by_number", "last"):
                args["which"] = "last"

            tgt = str(args.get("target") or "").strip().lower()
            if tgt not in ("ask", "pdf", "quote"):
                tgt = "ask"
            args["target"] = tgt

            if "quote_no" in args and args.get("quote_no") is not None:
                qn = str(args.get("quote_no") or "").strip().lstrip("#")
                args["quote_no"] = qn

            out["args"] = args

        return out

    def _validate_plan(self, plan: dict, *, context: dict) -> None:
        action = str(plan.get("action") or "").strip()
        allowed_actions = {
            "create_quote", "list_quotes", "top_clients", "open_quote", "edit_quote", "chat",
            "product_prices", "report"
        }
        if action not in allowed_actions:
            raise RuntimeError(f"Plan inválido: action no soportada '{action}'.")

        if not isinstance(plan.get("args"), dict):
            raise RuntimeError("Plan inválido: args no es dict.")

        if action == "create_quote":
            items = plan["args"].get("items")
            if items is None:
                plan["args"]["items"] = []
            elif not isinstance(items, list):
                raise RuntimeError("Plan inválido: items no es list.")

        if action == "open_quote":
            args = plan.get("args") or {}
            which = str(args.get("which") or "").strip().lower()
            if which not in ("by_number", "last"):
                raise RuntimeError("Plan inválido: open_quote.which debe ser 'by_number' o 'last'.")

            tgt = _normalize_open_target(args.get("target"))
            if tgt and tgt not in ("ask", "pdf", "quote"):
                raise RuntimeError("Plan inválido: open_quote.target debe ser 'ask'|'pdf'|'quote'.")

    def _system_prompt(self, *, today_iso: str, context: dict) -> str:
        statuses = [str(x or "") for x in (context.get("statuses") or [])]
        currencies = [str(x or "").upper() for x in (context.get("currencies") or []) if str(x or "").strip()]
        country = str((context.get("country") or "")).upper().strip()
        hint = str(context.get("intent_hint") or "").strip()

        session = context.get("session") or {}
        try:
            session_s = json.dumps(session, ensure_ascii=False)
        except Exception:
            session_s = str(session)

        cats_rules = str(context.get("cats_qty_rules") or "").strip()
        doc_rule = str(context.get("doc_rule") or "").strip()

        if "" not in statuses:
            statuses.append("")

        extra_rules = ""
        if doc_rule:
            extra_rules += f"- Documento: {doc_rule}\n"
        if cats_rules:
            extra_rules += f"- CATS: {cats_rules}\n"

        # ✅ Ejemplos recientes del audit (few-shot)
        recent_examples = str(context.get("recent_examples") or "").strip()
        recent_block = ""
        if recent_examples:
            recent_block = (
                "\n\nEjemplos recientes (logs reales; imita el FORMATO y las NORMALIZACIONES):\n"
                + recent_examples
                + "\n"
            )

        examples = context.get("recent_plan_examples") or []
        ex_block = ""
        if isinstance(examples, list) and examples:
            buf = []
            total = 0
            for i, ex in enumerate(examples[:30], 1):
                try:
                    s = json.dumps(ex, ensure_ascii=False)
                except Exception:
                    continue
                line = f"{i}) {s}"
                total += len(line)
                if total > 6500:
                    break
                buf.append(line)
            if buf:
                ex_block = "\n\nEjemplos recientes de SALIDA JSON válida (úsalos como guía de formato):\n" + "\n".join(buf)

        return (
            "Eres el asistente del Sistema de Cotizaciones. Devuelve SOLO un JSON válido (sin texto extra) con claves: "
            "action, args, needs_confirmation, explanation.\n"
            f"Hoy: {today_iso}\n"
            f"País: {country or 'N/A'}\n"
            f"Estados: {', '.join(statuses)}\n"
            f"Monedas: {', '.join(currencies) if currencies else 'PEN, USD'}\n"
            f"Hint de intención: {hint or '—'}\n"
            f"Sesión: {session_s}\n"
            "\n"
            "Reglas importantes:\n"
            "- price_mode permitido: oferta|min|max|base. (NO uses 'offer').\n"
            "- qty debe ser texto si tiene ceros (ej: 0.050).\n"
            "- open_quote.target: usa 'pdf' si el usuario pide PDF; 'quote' si pide abrir la cotización/panel; si es ambiguo usa 'ask'.\n"
            + (extra_rules or "")
            + recent_block
            + "\n"
            "Acciones soportadas:\n"
            "- create_quote: crear/armar una cotización. args: client_query, client_doc?, client_phone?, payment_method?, currency?, items[{query, qty, price?, price_mode?}]\n"
            "- list_quotes: listar/ver cotizaciones. args: status?, currency?, limit?\n"
            "- top_clients: ranking clientes. args: currency, limit?\n"
            "- open_quote: abrir cotización o su PDF. args: which='by_number'|'last', quote_no?, client_query?, target? ('ask'|'quote'|'pdf')\n"
            "- product_prices: consultar precios del producto/presentación. args: code|query, currency?\n"
            "- report: reportes/consultas del sistema (solo lectura). args: title?, sql? (solo SELECT), limit?\n"
            "- edit_quote: editar cotización en pantalla (SIN tocar DB). args: edits_text\n"
            "- chat: responder dudas del usuario sobre el cotizador. args: text\n"
            "\n"
            "needs_confirmation: true solo para create_quote; false para el resto.\n"
            + ex_block
        ).strip()
