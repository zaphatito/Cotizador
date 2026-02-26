from __future__ import annotations

import os
import re
import threading
import unicodedata
from typing import Any

from .db import connect


DEFAULT_QUOTE_STATUSES: tuple[dict[str, str], ...] = (
    {"code": "PAGADO", "label": "Pagado", "color_hex": "#06863B"},
    {"code": "POR_PAGAR", "label": "Por pagar", "color_hex": "#ECD060"},
    {"code": "PENDIENTE", "label": "Pendiente", "color_hex": "#E67E22"},
    {"code": "REENVIADO", "label": "Reenviado", "color_hex": "#BF0DE3"},
    {"code": "NO_APLICA", "label": "No aplica", "color_hex": "#811307"},
)

_LEGACY_ALIAS_TO_DEFAULT: dict[str, str] = {
    "PAGO": "PAGADO",
    "PAID": "PAGADO",
    "PORPAGAR": "POR_PAGAR",
    "POR-PAGAR": "POR_PAGAR",
    "PENDING": "PENDIENTE",
    "NOAPLICA": "NO_APLICA",
    "INACTIVA": "NO_APLICA",
    "INACTIVO": "NO_APLICA",
    "INACTIVE": "NO_APLICA",
    "REENVIADOS": "REENVIADO",
}

_CACHE_LOCK = threading.Lock()
_CACHE_ROWS: dict[str, list[dict[str, Any]]] = {}


def _db_key(db_path: str) -> str:
    return os.path.abspath(str(db_path or "")).strip().lower()


def _table_exists(con, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (str(name or ""),),
    ).fetchone()
    return row is not None


def _has_column(con, table: str, col: str) -> bool:
    if not _table_exists(con, table):
        return False
    try:
        rows = con.execute(f"PRAGMA table_info({table})").fetchall()
        cols = {str(r["name"]).lower() for r in rows}
        return str(col or "").lower() in cols
    except Exception:
        return False


def _collapse_spaces(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _ascii_fold(value: Any) -> str:
    raw = str(value or "")
    norm = unicodedata.normalize("NFKD", raw)
    return "".join(ch for ch in norm if not unicodedata.combining(ch))


def normalize_status_code(value: Any) -> str:
    s = _ascii_fold(value)
    s = str(s or "").strip().upper()
    if not s:
        return ""
    s = s.replace("-", "_")
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^A-Z0-9_]", "", s)
    s = re.sub(r"_+", "_", s).strip("_")
    if not s:
        return ""
    return _LEGACY_ALIAS_TO_DEFAULT.get(s, s)


def _status_label_from_code(code: Any) -> str:
    raw = normalize_status_code(code)
    if not raw:
        return ""
    words = [w.strip().capitalize() for w in raw.split("_") if w.strip()]
    return " ".join(words).strip()


def _normalize_hex_color(value: Any, *, fallback: str = "#5B708E") -> str:
    s = str(value or "").strip().upper()
    if s and (not s.startswith("#")):
        s = f"#{s}"
    if re.fullmatch(r"#[0-9A-F]{6}", s or ""):
        return s
    fb = str(fallback or "#5B708E").strip().upper()
    if fb and (not fb.startswith("#")):
        fb = f"#{fb}"
    if re.fullmatch(r"#[0-9A-F]{6}", fb or ""):
        return fb
    return "#5B708E"


def ensure_quote_statuses_table(con) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS quote_statuses (
            code TEXT PRIMARY KEY,
            label TEXT NOT NULL DEFAULT '',
            color_hex TEXT NOT NULL DEFAULT '',
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    con.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_quote_statuses_sort
        ON quote_statuses(sort_order, code)
        """
    )
    if _has_column(con, "quote_statuses", "color"):
        con.execute(
            """
            UPDATE quote_statuses
            SET color_hex = CASE
                WHEN TRIM(COALESCE(color_hex, '')) = '' THEN COALESCE(color, '')
                ELSE color_hex
            END
            """
        )


def ensure_default_quote_statuses(con) -> None:
    ensure_quote_statuses_table(con)
    for idx, item in enumerate(DEFAULT_QUOTE_STATUSES):
        code = normalize_status_code(item.get("code"))
        if not code:
            continue
        label = _collapse_spaces(item.get("label")) or _status_label_from_code(code)
        color_hex = _normalize_hex_color(item.get("color_hex"))
        con.execute(
            """
            INSERT INTO quote_statuses(code, label, color_hex, sort_order, created_at, updated_at)
            VALUES(?, ?, ?, ?, datetime('now'), datetime('now'))
            ON CONFLICT(code) DO NOTHING
            """,
            (code, label, color_hex, int(idx)),
        )


def _next_sort_order(con) -> int:
    row = con.execute(
        "SELECT COALESCE(MAX(sort_order), -1) AS n FROM quote_statuses"
    ).fetchone()
    try:
        return int(row["n"] or -1) + 1
    except Exception:
        return 0


def backfill_quote_statuses_from_quotes(con) -> None:
    ensure_quote_statuses_table(con)
    if (not _table_exists(con, "quotes")) or (not _has_column(con, "quotes", "estado")):
        return

    rows = con.execute(
        """
        SELECT DISTINCT TRIM(COALESCE(estado, '')) AS estado
        FROM quotes
        WHERE TRIM(COALESCE(estado, '')) <> ''
        ORDER BY estado
        """
    ).fetchall()
    if not rows:
        return

    sort_order = _next_sort_order(con)
    for r in rows:
        raw = str(r["estado"] or "").strip()
        if not raw:
            continue
        code = normalize_status_code(raw)
        if not code:
            continue

        if code != raw:
            con.execute(
                "UPDATE quotes SET estado = ? WHERE TRIM(COALESCE(estado, '')) = ?",
                (code, raw),
            )

        row_existing = con.execute(
            "SELECT 1 FROM quote_statuses WHERE code = ? LIMIT 1",
            (code,),
        ).fetchone()
        if row_existing:
            continue

        label = _status_label_from_code(raw) or _status_label_from_code(code) or raw
        color_hex = _default_color_for_code(code, fallback="#5B708E")
        con.execute(
            """
            INSERT INTO quote_statuses(code, label, color_hex, sort_order, created_at, updated_at)
            VALUES(?, ?, ?, ?, datetime('now'), datetime('now'))
            """,
            (code, label, color_hex, int(sort_order)),
        )
        sort_order += 1


def ensure_quote_statuses_ready(con) -> None:
    ensure_quote_statuses_table(con)
    ensure_default_quote_statuses(con)
    backfill_quote_statuses_from_quotes(con)


def list_quote_statuses(con) -> list[dict[str, Any]]:
    ensure_quote_statuses_ready(con)
    rows = con.execute(
        """
        SELECT
            COALESCE(code, '') AS code,
            COALESCE(label, '') AS label,
            COALESCE(color_hex, '') AS color_hex,
            COALESCE(sort_order, 0) AS sort_order
        FROM quote_statuses
        ORDER BY sort_order ASC, code ASC
        """
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        code = normalize_status_code(r["code"])
        if not code:
            continue
        label = _collapse_spaces(r["label"]) or _status_label_from_code(code)
        color_hex = _normalize_hex_color(r["color_hex"], fallback=_default_color_for_code(code))
        try:
            sort_order = int(r["sort_order"] or 0)
        except Exception:
            sort_order = 0
        out.append(
            {
                "code": code,
                "label": label,
                "color_hex": color_hex,
                "sort_order": sort_order,
            }
        )
    return out


def _default_color_for_code(code: Any, *, fallback: str = "#5B708E") -> str:
    c = normalize_status_code(code)
    for item in DEFAULT_QUOTE_STATUSES:
        if normalize_status_code(item.get("code")) == c:
            return _normalize_hex_color(item.get("color_hex"), fallback=fallback)
    return _normalize_hex_color(fallback, fallback="#5B708E")


def _build_unique_code(label: str, *, used_codes: set[str]) -> str:
    base = normalize_status_code(label) or "ESTADO"
    code = base
    idx = 2
    while code in used_codes:
        code = f"{base}_{idx}"
        idx += 1
    used_codes.add(code)
    return code


def replace_quote_statuses(con, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ensure_quote_statuses_ready(con)
    current = list_quote_statuses(con)
    current_codes = {str(r.get("code") or "") for r in current}
    used_codes: set[str] = set()
    next_rows: list[dict[str, Any]] = []

    for idx, r in enumerate(rows or []):
        label = _collapse_spaces(r.get("label") or "")
        if not label:
            continue

        in_code = normalize_status_code(r.get("code") or "")
        code = in_code if in_code and (in_code not in used_codes) else _build_unique_code(label, used_codes=used_codes)
        if in_code:
            used_codes.add(in_code)

        color_hex = _normalize_hex_color(
            r.get("color_hex"),
            fallback=_default_color_for_code(code),
        )
        next_rows.append(
            {
                "code": code,
                "label": label,
                "color_hex": color_hex,
                "sort_order": int(idx),
            }
        )

    if not next_rows:
        raise ValueError("Debe existir al menos un estado.")

    next_codes = {str(r["code"]) for r in next_rows}
    deleted_codes = sorted(c for c in current_codes if c and c not in next_codes)

    for r in next_rows:
        con.execute(
            """
            INSERT INTO quote_statuses(code, label, color_hex, sort_order, created_at, updated_at)
            VALUES(?, ?, ?, ?, datetime('now'), datetime('now'))
            ON CONFLICT(code) DO UPDATE SET
                label=excluded.label,
                color_hex=excluded.color_hex,
                sort_order=excluded.sort_order,
                updated_at=datetime('now')
            """,
            (
                str(r["code"]),
                str(r["label"]),
                str(r["color_hex"]),
                int(r["sort_order"]),
            ),
        )

    if deleted_codes:
        if _table_exists(con, "quotes") and _has_column(con, "quotes", "estado"):
            for code in deleted_codes:
                con.execute(
                    "UPDATE quotes SET estado = '' WHERE TRIM(COALESCE(estado, '')) = ?",
                    (str(code),),
                )
        con.executemany(
            "DELETE FROM quote_statuses WHERE code = ?",
            [(str(code),) for code in deleted_codes],
        )

    out = list_quote_statuses(con)
    invalidate_quote_statuses_cache()
    return out


def set_default_status_colors_from_legacy_settings(con) -> None:
    ensure_quote_statuses_ready(con)
    key_map = {
        "PAGADO": "status_color_pagado",
        "POR_PAGAR": "status_color_por_pagar",
        "PENDIENTE": "status_color_pendiente",
        "REENVIADO": "status_color_reenviado",
        "NO_APLICA": "status_color_no_aplica",
    }
    if not _table_exists(con, "settings"):
        return

    for code, key in key_map.items():
        row = con.execute(
            "SELECT value FROM settings WHERE key = ? LIMIT 1",
            (str(key),),
        ).fetchone()
        if not row:
            continue
        hx = _normalize_hex_color(row["value"], fallback="")
        if not hx:
            continue
        con.execute(
            """
            UPDATE quote_statuses
            SET color_hex = ?, updated_at = datetime('now')
            WHERE code = ?
            """,
            (hx, code),
        )


def invalidate_quote_statuses_cache(db_path: str | None = None) -> None:
    with _CACHE_LOCK:
        if db_path:
            _CACHE_ROWS.pop(_db_key(db_path), None)
        else:
            _CACHE_ROWS.clear()


def get_quote_statuses_cached(*, db_path: str, force_reload: bool = False) -> list[dict[str, Any]]:
    key = _db_key(db_path)
    if (not force_reload) and key:
        with _CACHE_LOCK:
            cached = _CACHE_ROWS.get(key)
            if cached is not None:
                return [dict(r) for r in cached]

    con = connect(db_path)
    try:
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("PRAGMA busy_timeout = 5000")
        ensure_quote_statuses_ready(con)
        rows = list_quote_statuses(con)
    finally:
        con.close()

    with _CACHE_LOCK:
        _CACHE_ROWS[key] = [dict(r) for r in rows]
    return [dict(r) for r in rows]


def build_status_lookup_from_rows(rows: list[dict[str, Any]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for r in rows or []:
        code = normalize_status_code(r.get("code"))
        if not code:
            continue
        label = _collapse_spaces(r.get("label"))
        for candidate in (code, label):
            tok = normalize_status_code(candidate)
            if not tok:
                continue
            out[tok] = code
            out[tok.replace("_", "")] = code
    for alias, target in _LEGACY_ALIAS_TO_DEFAULT.items():
        t = normalize_status_code(target)
        if t:
            out[normalize_status_code(alias)] = t
    return out
