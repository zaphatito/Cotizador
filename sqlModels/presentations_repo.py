# sqlModels/presentations_repo.py
from __future__ import annotations

import sqlite3

import pandas as pd

from .utils import now_iso


def _to_text(v) -> str:
    return str(v or "").strip()


def _to_float(v, default: float = 0.0) -> float:
    try:
        if v is None:
            return float(default)
        if isinstance(v, str):
            s = v.strip().replace(",", "")
            if not s:
                return float(default)
            return float(s)
        return float(v)
    except Exception:
        return float(default)


def _is_generic_category_row(row: sqlite3.Row | dict | None) -> bool:
    if not row:
        return False

    try:
        pid = _to_text(row["id"]).upper()
    except Exception:
        pid = _to_text(getattr(row, "get", lambda *_: "")("id")).upper()

    try:
        name = _to_text(row["nombre"]).upper()
    except Exception:
        name = _to_text(getattr(row, "get", lambda *_: "")("nombre")).upper()

    try:
        cat = _to_text(row["categoria"]).upper()
    except Exception:
        cat = _to_text(getattr(row, "get", lambda *_: "")("categoria")).upper()

    try:
        depto = _to_text(row["departamento"]).upper()
    except Exception:
        depto = _to_text(getattr(row, "get", lambda *_: "")("departamento")).upper()

    if not pid:
        return False
    if pid == cat and name == cat:
        return True
    if pid == depto and name == depto:
        return True
    return False


def upsert_presentations_snapshot(
    con: sqlite3.Connection,
    import_id: int,
    df: pd.DataFrame,
    *,
    replace_current: bool = False,
) -> None:
    """
    Espera columnas (estructura excel parseada):
      CODIGO, CODIGO_NORM, NOMBRE, DESCRIPCION,
      DEPARTAMENTO, GENERO, P_MAX, P_MIN, P_OFERTA

    Compat:
      REQUIERE_BOTELLA
    """
    if df is None or df.empty:
        return

    now = now_iso()

    rows_raw_hist: list[tuple] = []
    rows_raw_cur: list[tuple] = []

    rows_hist: list[tuple] = []
    rows_cur: list[tuple] = []

    for _, r in df.iterrows():
        codigo_norm = _to_text(r.get("CODIGO_NORM") or r.get("codigo_norm") or r.get("CODIGO") or r.get("codigo")).upper()
        if not codigo_norm:
            continue

        codigo = _to_text(r.get("CODIGO") or r.get("codigo") or codigo_norm).upper()
        nombre = _to_text(r.get("NOMBRE") or r.get("nombre"))
        descripcion = _to_text(r.get("DESCRIPCION") or r.get("descripcion"))
        depto = _to_text(r.get("DEPARTAMENTO") or r.get("departamento")).upper()
        genero = _to_text(r.get("GENERO") or r.get("genero")).lower()

        p_max = _to_float(r.get("P_MAX") if "P_MAX" in r else r.get("p_max"), 0.0)
        p_min = _to_float(r.get("P_MIN") if "P_MIN" in r else r.get("p_min"), 0.0)
        p_oferta = _to_float(r.get("P_OFERTA") if "P_OFERTA" in r else r.get("p_oferta"), 0.0)

        req = 1 if bool(r.get("REQUIERE_BOTELLA")) else 0
        fuente = _to_text(r.get("__FUENTE") or r.get("__fuente") or r.get("fuente"))

        rows_raw_hist.append(
            (
                int(import_id),
                codigo_norm,
                codigo,
                nombre,
                descripcion,
                depto,
                genero,
                float(p_max),
                float(p_min),
                float(p_oferta),
                fuente,
            )
        )
        rows_raw_cur.append(
            (
                codigo_norm,
                codigo,
                nombre,
                descripcion,
                depto,
                genero,
                float(p_max),
                float(p_min),
                float(p_oferta),
                fuente,
                now,
            )
        )

        rows_hist.append(
            (
                int(import_id),
                codigo_norm,
                codigo,
                nombre,
                descripcion,
                depto,
                genero,
                float(p_max),
                float(p_min),
                float(p_oferta),
                int(req),
                0.0,
                "",
                fuente,
            )
        )
        rows_cur.append(
            (
                codigo_norm,
                codigo,
                nombre,
                descripcion,
                depto,
                genero,
                float(p_max),
                float(p_min),
                float(p_oferta),
                int(req),
                0.0,
                "",
                fuente,
                now,
            )
        )

    if replace_current:
        con.execute("DELETE FROM presentacion_current")
        con.execute("DELETE FROM presentations_current")

    if rows_raw_hist:
        con.executemany(
            """
            INSERT OR REPLACE INTO presentacion_hist(
                import_id, codigo_norm, codigo, nombre, descripcion,
                departamento, genero, p_max, p_min, p_oferta, fuente
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            rows_raw_hist,
        )

    if rows_raw_cur:
        con.executemany(
            """
            INSERT INTO presentacion_current(
                codigo_norm, codigo, nombre, descripcion,
                departamento, genero, p_max, p_min, p_oferta,
                fuente, updated_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(codigo_norm, departamento, genero) DO UPDATE SET
                codigo=excluded.codigo,
                nombre=excluded.nombre,
                descripcion=excluded.descripcion,
                departamento=excluded.departamento,
                genero=excluded.genero,
                p_max=excluded.p_max,
                p_min=excluded.p_min,
                p_oferta=excluded.p_oferta,
                fuente=excluded.fuente,
                updated_at=excluded.updated_at
            """,
            rows_raw_cur,
        )

    if rows_hist:
        con.executemany(
            """
            INSERT OR REPLACE INTO presentations_hist(
                import_id, codigo_norm, codigo, nombre, descripcion,
                departamento, genero,
                p_max, p_min, p_oferta,
                requiere_botella,
                stock_disponible, codigos_producto,
                fuente
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            rows_hist,
        )

    if rows_cur:
        con.executemany(
            """
            INSERT INTO presentations_current(
                codigo_norm, codigo, nombre, descripcion,
                departamento, genero,
                p_max, p_min, p_oferta,
                requiere_botella,
                stock_disponible, codigos_producto,
                fuente, updated_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(codigo_norm, departamento, genero) DO UPDATE SET
                codigo=excluded.codigo,
                nombre=excluded.nombre,
                descripcion=excluded.descripcion,
                departamento=excluded.departamento,
                genero=excluded.genero,
                p_max=excluded.p_max,
                p_min=excluded.p_min,
                p_oferta=excluded.p_oferta,
                requiere_botella=excluded.requiere_botella,
                stock_disponible=excluded.stock_disponible,
                codigos_producto=excluded.codigos_producto,
                fuente=excluded.fuente,
                updated_at=excluded.updated_at
            """,
            rows_cur,
        )


def upsert_presentacion_prod_snapshot(
    con: sqlite3.Connection,
    import_id: int,
    df: pd.DataFrame,
    *,
    replace_current: bool = False,
) -> None:
    """
    Espera columnas:
      COD_PRODUCTO, COD_PRESENTACION, DEPARTAMENTO, GENERO, CANTIDAD
    """
    if df is None or df.empty:
        return

    now = now_iso()

    rows_hist: list[tuple] = []
    rows_cur: list[tuple] = []

    for _, r in df.iterrows():
        cod_producto = _to_text(r.get("COD_PRODUCTO") or r.get("cod_producto")).upper()
        cod_presentacion = _to_text(r.get("COD_PRESENTACION") or r.get("cod_presentacion")).upper()
        if not cod_producto or not cod_presentacion:
            continue

        depto = _to_text(r.get("DEPARTAMENTO") or r.get("departamento")).upper()
        genero = _to_text(r.get("GENERO") or r.get("genero")).lower()
        cantidad = _to_float(r.get("CANTIDAD") if "CANTIDAD" in r else r.get("cantidad"), 0.0)
        fuente = _to_text(r.get("__FUENTE") or r.get("__fuente") or r.get("fuente"))

        rows_hist.append(
            (
                int(import_id),
                cod_producto,
                cod_presentacion,
                depto,
                genero,
                float(cantidad),
                fuente,
            )
        )
        rows_cur.append(
            (
                cod_producto,
                cod_presentacion,
                depto,
                genero,
                float(cantidad),
                fuente,
                now,
            )
        )

    if replace_current:
        con.execute("DELETE FROM presentacion_prod_current")

    if rows_hist:
        con.executemany(
            """
            INSERT OR REPLACE INTO presentacion_prod_hist(
                import_id, cod_producto, cod_presentacion,
                departamento, genero, cantidad, fuente
            )
            VALUES(?,?,?,?,?,?,?)
            """,
            rows_hist,
        )

    if rows_cur:
        con.executemany(
            """
            INSERT INTO presentacion_prod_current(
                cod_producto, cod_presentacion,
                departamento, genero, cantidad,
                fuente, updated_at
            )
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(cod_producto, cod_presentacion, departamento, genero) DO UPDATE SET
                cantidad=excluded.cantidad,
                fuente=excluded.fuente,
                updated_at=excluded.updated_at
            """,
            rows_cur,
        )


def rebuild_presentations_rollup(con: sqlite3.Connection) -> None:
    """
    Calcula stock_disponible y codigos_producto para presentations_current
    usando presentacion_prod_current + products_current.
    """
    service_markers = {"ESENCIA", "ESENCIAS", "AROMATERAPIA"}
    marker_rows = con.execute(
        """
        SELECT
            COALESCE(id, '') AS id,
            COALESCE(nombre, '') AS nombre,
            COALESCE(categoria, '') AS categoria,
            COALESCE(departamento, '') AS departamento
        FROM products_current
        """
    ).fetchall()
    for mr in marker_rows:
        if _is_generic_category_row(mr):
            cat = _to_text(mr["categoria"]).upper()
            depto = _to_text(mr["departamento"]).upper()
            if cat:
                service_markers.add(cat)
            if depto:
                service_markers.add(depto)

    pres_rows = con.execute(
        """
        SELECT
            codigo_norm,
            COALESCE(codigo, '') AS codigo,
            UPPER(COALESCE(departamento, '')) AS departamento,
            LOWER(COALESCE(genero, '')) AS genero
        FROM presentations_current
        """
    ).fetchall()

    updates: list[tuple] = []

    for p in pres_rows:
        codigo_norm = _to_text(p["codigo_norm"]).upper()
        codigo = _to_text(p["codigo"]).upper()
        depto = _to_text(p["departamento"]).upper()
        genero = _to_text(p["genero"]).lower()

        rels = con.execute(
            """
            SELECT
                cod_producto,
                COALESCE(cantidad, 0) AS cantidad,
                UPPER(COALESCE(departamento, '')) AS departamento,
                LOWER(COALESCE(genero, '')) AS genero
            FROM presentacion_prod_current
            WHERE UPPER(cod_presentacion) = ? OR UPPER(cod_presentacion) = ?
            """,
            (codigo_norm, codigo),
        ).fetchall()

        if not rels:
            updates.append((0.0, "", codigo_norm, depto, genero))
            continue

        ratios: list[float] = []
        codigos_producto: list[str] = []

        for r in rels:
            rel_dep = _to_text(r["departamento"]).upper()
            rel_gen = _to_text(r["genero"]).lower()
            if depto and rel_dep and rel_dep != depto:
                continue
            if genero and rel_gen and rel_gen != genero:
                continue

            cod_prod = _to_text(r["cod_producto"]).upper()
            if not cod_prod:
                continue

            need_qty = _to_float(r["cantidad"], 0.0)
            if need_qty <= 0:
                continue

            if cod_prod in service_markers:
                cand_rows = con.execute(
                    """
                    SELECT
                        COALESCE(id, '') AS id,
                        COALESCE(nombre, '') AS nombre,
                        COALESCE(categoria, '') AS categoria,
                        COALESCE(departamento, '') AS departamento,
                        LOWER(COALESCE(genero, '')) AS genero,
                        COALESCE(cantidad_disponible, 0) AS s
                    FROM products_current
                    WHERE UPPER(COALESCE(departamento, '')) = ?
                    """,
                    (cod_prod,),
                ).fetchall()

                best_ratio = 0.0
                for c in cand_rows:
                    if _is_generic_category_row(c):
                        continue
                    cand_gen = _to_text(c["genero"]).lower()
                    if rel_gen and cand_gen != rel_gen:
                        continue
                    stock_prod = _to_float(c["s"], 0.0)
                    best_ratio = max(best_ratio, stock_prod / need_qty)

                ratios.append(best_ratio)
                codigos_producto.append(cod_prod)
                continue

            srow = con.execute(
                """
                SELECT
                    COALESCE(id, '') AS id,
                    COALESCE(nombre, '') AS nombre,
                    COALESCE(categoria, '') AS categoria,
                    COALESCE(departamento, '') AS departamento,
                    COALESCE(cantidad_disponible, 0) AS s
                FROM products_current
                WHERE UPPER(id) = ?
                LIMIT 1
                """,
                (cod_prod,),
            ).fetchone()

            if srow and _is_generic_category_row(srow):
                ratios.append(0.0)
                codigos_producto.append(cod_prod)
                continue

            stock_prod = _to_float(srow["s"], 0.0) if srow else 0.0
            ratios.append(stock_prod / need_qty)
            codigos_producto.append(cod_prod)

        stock_disponible = min(ratios) if ratios else 0.0
        stock_disponible = round(max(0.0, float(stock_disponible)), 6)

        codigos_csv = ",".join(sorted(set(codigos_producto)))
        updates.append((stock_disponible, codigos_csv, codigo_norm, depto, genero))

    if updates:
        con.executemany(
            """
            UPDATE presentations_current
            SET stock_disponible = ?,
                codigos_producto = ?
            WHERE codigo_norm = ?
              AND UPPER(COALESCE(departamento, '')) = ?
              AND LOWER(COALESCE(genero, '')) = ?
            """,
            updates,
        )


def load_presentations_current(con: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query("SELECT * FROM presentations_current", con)
