from __future__ import annotations

import json
import sqlite3
from typing import Any


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _compact_error(value: Any, *, max_len: int = 1800) -> str:
    msg = str(value or "").strip()
    if len(msg) <= max_len:
        return msg
    return msg[: max(64, max_len - 3)].rstrip() + "..."


def insert_label_print_log(con: sqlite3.Connection, payload: dict[str, Any]) -> int:
    event_id = str(payload.get("event_id") or "").strip()
    if not event_id:
        raise ValueError("event_id requerido para registrar impresion de etiquetas")

    items = payload.get("etiquetas")
    if not isinstance(items, list):
        items = []

    cur = con.execute(
        """
        INSERT INTO label_print_logs(
            event_id,
            quote_code,
            printed_at,
            user,
            api_username,
            id_user_api,
            cod_pais,
            id_cotizador,
            company,
            tienda,
            total_labels,
            items_json,
            hostname,
            ip_local,
            usuario_sistema,
            app_version,
            created_at,
            updated_at
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        ON CONFLICT(event_id) DO UPDATE SET
            quote_code=excluded.quote_code,
            printed_at=excluded.printed_at,
            user=excluded.user,
            api_username=excluded.api_username,
            id_user_api=excluded.id_user_api,
            cod_pais=excluded.cod_pais,
            id_cotizador=excluded.id_cotizador,
            company=excluded.company,
            tienda=excluded.tienda,
            total_labels=excluded.total_labels,
            items_json=excluded.items_json,
            hostname=excluded.hostname,
            ip_local=excluded.ip_local,
            usuario_sistema=excluded.usuario_sistema,
            app_version=excluded.app_version,
            updated_at=datetime('now')
        """,
        (
            event_id,
            str(payload.get("codigo") or payload.get("quote_code") or "").strip(),
            str(payload.get("printed_at") or "").strip(),
            str(payload.get("user") or "").strip(),
            str(payload.get("api_username") or "").strip(),
            int(payload["id_user_api"]) if payload.get("id_user_api") not in (None, "") else None,
            str(payload.get("cod_pais") or "").strip().upper(),
            str(payload.get("id_cotizador") or "").strip(),
            str(payload.get("empresa") or payload.get("company") or "").strip(),
            1 if bool(payload.get("tienda")) else 0,
            int(payload.get("total_etiquetas") or payload.get("total_labels") or 0),
            _json_dumps(items),
            str(payload.get("hostname") or "").strip(),
            str(payload.get("ip_local") or "").strip(),
            str(payload.get("usuario_sistema") or "").strip(),
            str(payload.get("app_version") or "").strip(),
        ),
    )
    if cur.lastrowid:
        return int(cur.lastrowid)
    row = con.execute(
        "SELECT id FROM label_print_logs WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    return int(row["id"]) if row else 0


def _json_loads_list(value: Any) -> list[Any]:
    try:
        parsed = json.loads(str(value or "[]"))
    except Exception:
        parsed = []
    return parsed if isinstance(parsed, list) else []


def _row_to_payload(row: sqlite3.Row) -> dict[str, Any]:
    items = _json_loads_list(row["items_json"])
    return {
        "event_id": str(row["event_id"] or "").strip(),
        "codigo": str(row["quote_code"] or "").strip(),
        "quote_code": str(row["quote_code"] or "").strip(),
        "printed_at": str(row["printed_at"] or "").strip(),
        "id_cotizador": str(row["id_cotizador"] or "").strip(),
        "user": str(row["user"] or "").strip(),
        "api_username": str(row["api_username"] or "").strip(),
        "id_user_api": int(row["id_user_api"]) if row["id_user_api"] is not None else None,
        "cod_pais": str(row["cod_pais"] or "").strip().upper(),
        "empresa": str(row["company"] or "").strip(),
        "tienda": bool(row["tienda"]),
        "total_etiquetas": int(row["total_labels"] or 0),
        "etiquetas": items,
        "hostname": str(row["hostname"] or "").strip(),
        "ip_local": str(row["ip_local"] or "").strip(),
        "usuario_sistema": str(row["usuario_sistema"] or "").strip(),
        "app_version": str(row["app_version"] or "").strip(),
    }


def list_pending_label_print_logs(
    con: sqlite3.Connection,
    *,
    retry_before_iso: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT
            id,
            event_id,
            quote_code,
            printed_at,
            user,
            api_username,
            id_user_api,
            cod_pais,
            id_cotizador,
            company,
            tienda,
            total_labels,
            items_json,
            hostname,
            ip_local,
            usuario_sistema,
            app_version
        FROM label_print_logs
        WHERE COALESCE(TRIM(api_sent_at), '') = ''
          AND (
              COALESCE(TRIM(api_error_at), '') = ''
              OR COALESCE(TRIM(api_error_at), '') <= ?
          )
        ORDER BY id ASC
        LIMIT ?
        """,
        (str(retry_before_iso or "").strip(), max(1, int(limit))),
    ).fetchall()

    out: list[dict[str, Any]] = []
    for row in rows:
        payload = _row_to_payload(row)
        out.append({"id": int(row["id"]), "event_id": payload["event_id"], "payload": payload})
    return out


def mark_label_print_log_sent(
    con: sqlite3.Connection,
    *,
    event_id: str,
    sent_at: str,
    response: Any = None,
) -> None:
    con.execute(
        """
        UPDATE label_print_logs
        SET api_sent_at = ?,
            api_error_at = '',
            api_error_message = '',
            api_response = ?,
            updated_at = datetime('now')
        WHERE event_id = ?
        """,
        (
            str(sent_at or "").strip(),
            _json_dumps(response) if response is not None else "",
            str(event_id or "").strip(),
        ),
    )


def mark_label_print_log_error(
    con: sqlite3.Connection,
    *,
    event_id: str,
    error_at: str,
    error_message: Any,
) -> None:
    con.execute(
        """
        UPDATE label_print_logs
        SET api_error_at = ?,
            api_error_message = ?,
            updated_at = datetime('now')
        WHERE event_id = ?
        """,
        (
            str(error_at or "").strip(),
            _compact_error(error_message),
            str(event_id or "").strip(),
        ),
    )
