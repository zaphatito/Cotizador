# sqlModels/rates_repo.py
from __future__ import annotations

import sqlite3
from .utils import now_iso


def load_rates(con: sqlite3.Connection, base_currency: str) -> dict[str, float]:
    bc = (base_currency or "").strip().upper()
    rows = con.execute(
        "SELECT currency, rate FROM exchange_rates WHERE base_currency = ?",
        (bc,),
    ).fetchall()
    out: dict[str, float] = {}
    for r in rows:
        cur = str(r["currency"]).strip().upper()
        try:
            out[cur] = float(r["rate"])
        except Exception:
            out[cur] = 1.0
    return out


def _get_current_rate(con: sqlite3.Connection, bc: str, cur: str) -> float | None:
    row = con.execute(
        "SELECT rate FROM exchange_rates WHERE base_currency = ? AND currency = ?",
        (bc, cur),
    ).fetchone()
    if not row:
        return None
    try:
        return float(row["rate"])
    except Exception:
        return None


def _insert_rate_history(con: sqlite3.Connection, bc: str, cur: str, r: float) -> None:
    con.execute(
        """
        INSERT INTO exchange_rates_history(base_currency, currency, rate, recorded_at)
        VALUES(?,?,?,?)
        """,
        (bc, cur, float(r), now_iso()),
    )


def set_rate(con: sqlite3.Connection, base_currency: str, currency: str, rate: float) -> None:
    bc = (base_currency or "").strip().upper()
    cur = (currency or "").strip().upper()
    if not bc or not cur:
        return

    try:
        r = float(rate)
        if r <= 0:
            r = 1.0
    except Exception:
        r = 1.0

    # leer tasa anterior para evitar “histórico” duplicado cuando no cambió
    old = _get_current_rate(con, bc, cur)

    # upsert en tabla actual
    con.execute(
        """
        INSERT INTO exchange_rates(base_currency, currency, rate, updated_at)
        VALUES(?,?,?,?)
        ON CONFLICT(base_currency, currency) DO UPDATE SET
            rate=excluded.rate,
            updated_at=excluded.updated_at
        """,
        (bc, cur, r, now_iso()),
    )

    # guardar histórico SOLO si cambió (o si no existía)
    changed = (old is None) or (abs(float(old) - float(r)) > 1e-12)
    if changed:
        _insert_rate_history(con, bc, cur, r)


def list_rate_history(
    con: sqlite3.Connection,
    base_currency: str,
    currency: str,
    limit: int = 200,
) -> list[dict]:
    """
    Útil si luego quieres mostrar el histórico en UI.
    """
    bc = (base_currency or "").strip().upper()
    cur = (currency or "").strip().upper()
    lim = int(limit) if limit and int(limit) > 0 else 200

    rows = con.execute(
        """
        SELECT id, base_currency, currency, rate, recorded_at
        FROM exchange_rates_history
        WHERE base_currency = ? AND currency = ?
        ORDER BY recorded_at DESC, id DESC
        LIMIT ?
        """,
        (bc, cur, lim),
    ).fetchall()

    out: list[dict] = []
    for r in rows:
        out.append(
            {
                "id": int(r["id"]),
                "base_currency": r["base_currency"],
                "currency": r["currency"],
                "rate": float(r["rate"]),
                "recorded_at": r["recorded_at"],
            }
        )
    return out
