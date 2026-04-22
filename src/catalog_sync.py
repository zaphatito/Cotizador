# src/catalog_sync.py
from __future__ import annotations

import os
import pandas as pd

from .logging_setup import get_logger
from .dataio import _leer_inventario_xlsx
from .presentations import cargar_presentaciones, cargar_presentaciones_prod

import sqlModels.imports_repo as imports_repo
import sqlModels.products_repo as products_repo
import sqlModels.presentations_repo as presentations_repo

log = get_logger(__name__)


def _find_col_ci(df: pd.DataFrame, *candidates: str) -> str | None:
    low = {str(c).strip().lower(): str(c) for c in list(df.columns)}
    for cand in candidates:
        key = str(cand or "").strip().lower()
        if key in low:
            return low[key]
    return None


def validate_products_catalog_df(df: pd.DataFrame) -> tuple[bool, str]:
    """
    Verifica que el catalogo de productos tenga estructura y datos minimos validos.
    Retorna (ok, motivo).
    """
    if df is None:
        return False, "No hay productos cargados."
    if not isinstance(df, pd.DataFrame):
        return False, "El catalogo de productos no es valido."
    if df.empty:
        return False, "No hay productos cargados."

    col_id = _find_col_ci(df, "id", "codigo")
    col_nombre = _find_col_ci(df, "nombre")
    col_categoria = _find_col_ci(df, "categoria", "departamento")
    col_p_max = _find_col_ci(df, "p_max")
    col_p_min = _find_col_ci(df, "p_min")
    col_p_oferta = _find_col_ci(df, "p_oferta")
    col_precio_venta = _find_col_ci(df, "precio_venta")

    missing: list[str] = []
    if not col_id:
        missing.append("id/codigo")
    if not col_nombre:
        missing.append("nombre")
    if not col_categoria:
        missing.append("categoria/departamento")
    if not col_p_max:
        missing.append("p_max")
    if not col_p_min:
        missing.append("p_min")
    if not col_p_oferta:
        missing.append("p_oferta")
    if not col_precio_venta:
        missing.append("precio_venta")
    if missing:
        return False, "Faltan columnas requeridas: " + ", ".join(missing)

    id_ser = df[col_id].fillna("").astype(str).str.strip()
    if (id_ser == "").any():
        bad = int((id_ser == "").sum())
        return False, f"Hay {bad} productos sin codigo."

    nom_ser = df[col_nombre].fillna("").astype(str).str.strip()
    if (nom_ser == "").any():
        bad = int((nom_ser == "").sum())
        return False, f"Hay {bad} productos sin nombre."

    cat_ser = df[col_categoria].fillna("").astype(str).str.strip()
    if (cat_ser == "").any():
        bad = int((cat_ser == "").sum())
        return False, f"Hay {bad} productos sin categoria."

    p_max = pd.to_numeric(df[col_p_max], errors="coerce")
    p_min = pd.to_numeric(df[col_p_min], errors="coerce")
    p_oferta = pd.to_numeric(df[col_p_oferta], errors="coerce")
    if p_max.isna().any() or p_min.isna().any() or p_oferta.isna().any():
        return False, "Hay productos con precios no numericos."

    any_price = (p_max > 0) | (p_min > 0) | (p_oferta > 0)
    if not bool(any_price.any()):
        return False, "Todos los precios estan en 0."

    pv = pd.to_numeric(df[col_precio_venta], errors="coerce")
    pv_int = pv.round()
    pv_bad = pv.isna() | (pv != pv_int) | (~pv_int.isin([1.0, 2.0, 3.0]))
    if bool(pv_bad.any()):
        bad = int(pv_bad.sum())
        return False, f"Hay {bad} productos con precio_venta invalido (solo 1, 2 o 3)."

    return True, ""


def products_update_required_message(df: pd.DataFrame) -> str:
    ok, reason = validate_products_catalog_df(df)
    if ok:
        return ""
    reason_txt = str(reason or "El catalogo de productos esta mal cargado.").strip()
    return (
        f"{reason_txt}\n\n"
        "Debes usar 'Actualizar productos' para recargar el Excel."
    )


def _normalize_presentations_df_for_app(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normaliza columns de presentations_current para la app.
    """
    if df is None or df.empty:
        return pd.DataFrame()

    low = {str(c).strip().lower(): c for c in df.columns}
    mapping = {}

    if "codigo_norm" in low:
        mapping[low["codigo_norm"]] = "CODIGO_NORM"
    if "codigo" in low:
        mapping[low["codigo"]] = "CODIGO"
    if "nombre" in low:
        mapping[low["nombre"]] = "NOMBRE"
    if "descripcion" in low:
        mapping[low["descripcion"]] = "DESCRIPCION"
    if "departamento" in low:
        mapping[low["departamento"]] = "DEPARTAMENTO"
    if "genero" in low:
        mapping[low["genero"]] = "GENERO"
    if "p_max" in low:
        mapping[low["p_max"]] = "P_MAX"
    if "p_min" in low:
        mapping[low["p_min"]] = "P_MIN"
    if "p_oferta" in low:
        mapping[low["p_oferta"]] = "P_OFERTA"
    if "requiere_botella" in low:
        mapping[low["requiere_botella"]] = "REQUIERE_BOTELLA"
    if "stock_disponible" in low:
        mapping[low["stock_disponible"]] = "STOCK_DISPONIBLE"
    if "codigos_producto" in low:
        mapping[low["codigos_producto"]] = "CODIGOS_PRODUCTO"

    out = df.rename(columns=mapping).copy()

    req_text = [
        "CODIGO",
        "CODIGO_NORM",
        "NOMBRE",
        "DESCRIPCION",
        "DEPARTAMENTO",
        "GENERO",
        "CODIGOS_PRODUCTO",
    ]
    for c in req_text:
        if c not in out.columns:
            out[c] = ""
        out[c] = out[c].fillna("").astype(str).str.strip()

    req_num = ["P_MAX", "P_MIN", "P_OFERTA", "STOCK_DISPONIBLE"]
    for c in req_num:
        if c not in out.columns:
            out[c] = 0.0
        out[c] = pd.to_numeric(out[c], errors="coerce").fillna(0.0)

    if "REQUIERE_BOTELLA" not in out.columns:
        out["REQUIERE_BOTELLA"] = False

    def _to_bool(x):
        try:
            if isinstance(x, (int, float)):
                return bool(int(x))
        except Exception:
            pass
        s = str(x or "").strip().lower()
        if s in ("1", "true", "t", "yes", "si", "s"):
            return True
        if s in ("0", "false", "f", "no", "n", ""):
            return False
        return bool(x)

    out["REQUIERE_BOTELLA"] = out["REQUIERE_BOTELLA"].map(_to_bool)

    out["CODIGO"] = out["CODIGO"].str.upper()
    out["CODIGO_NORM"] = out["CODIGO_NORM"].str.upper()
    out["DEPARTAMENTO"] = out["DEPARTAMENTO"].str.upper()

    # Aliases para componentes que buscan columnas en minuscula / categoria.
    out["codigo"] = out["CODIGO"]
    out["codigo_norm"] = out["CODIGO_NORM"]
    out["nombre"] = out["NOMBRE"]
    out["descripcion"] = out["DESCRIPCION"]
    out["departamento"] = out["DEPARTAMENTO"]
    out["genero"] = out["GENERO"]
    out["categoria"] = "PRESENTACION"
    out["p_max"] = out["P_MAX"]
    out["p_min"] = out["P_MIN"]
    out["p_oferta"] = out["P_OFERTA"]
    out["precio_venta"] = 1
    out["cantidad_disponible"] = out["STOCK_DISPONIBLE"]

    return out


def sync_catalog_from_excel_path(con, excel_path: str) -> None:
    """
    Actualiza catalogo en DB usando UN solo archivo excel seleccionado.
    - Hoja 1: Inventario
    - Hoja 2: Presentaciones
    - Hoja 3: PresentacionesProd
    """
    if not excel_path or not os.path.exists(excel_path):
        raise FileNotFoundError(f"No existe el Excel: {excel_path}")

    changed_for_rollup = False

    # Productos
    need, meta = imports_repo.needs_import(con, "products", excel_path)
    if need:
        import_id = imports_repo.create_import(con, "products", excel_path, meta["mtime"], meta["size"], meta["hash"])
        log.info("Import productos (seleccionado): %s (import_id=%s)", os.path.basename(excel_path), import_id)

        df_prod = _leer_inventario_xlsx(excel_path, os.path.basename(excel_path))
        products_repo.upsert_products_snapshot(
            con,
            import_id,
            df_prod,
            replace_current=True,
        )
        changed_for_rollup = True

    # Presentaciones
    need2, meta2 = imports_repo.needs_import(con, "presentations", excel_path)
    if need2:
        import_id2 = imports_repo.create_import(con, "presentations", excel_path, meta2["mtime"], meta2["size"], meta2["hash"])
        log.info("Import presentaciones (seleccionado): %s (import_id=%s)", os.path.basename(excel_path), import_id2)

        df_pres = cargar_presentaciones(excel_path)
        presentations_repo.upsert_presentations_snapshot(con, import_id2, df_pres, replace_current=True)
        changed_for_rollup = True

    # PresentacionesProd (relacion)
    need3, meta3 = imports_repo.needs_import(con, "presentacion_prod", excel_path)
    if need3:
        import_id3 = imports_repo.create_import(con, "presentacion_prod", excel_path, meta3["mtime"], meta3["size"], meta3["hash"])
        log.info("Import presentacion_prod (seleccionado): %s (import_id=%s)", os.path.basename(excel_path), import_id3)

        df_pres_prod = cargar_presentaciones_prod(excel_path)
        presentations_repo.upsert_presentacion_prod_snapshot(con, import_id3, df_pres_prod, replace_current=True)
        changed_for_rollup = True

    if changed_for_rollup:
        presentations_repo.rebuild_presentations_rollup(con)


def sync_catalog_from_excel_to_db(con, data_dir: str) -> None:
    """
    Modo startup: busca inventario_lcdp.xlsx e inventario_ef.xlsx en DATA_DIR.
    Productos se importan desde ambos, presentaciones/relacion desde inventario_lcdp.xlsx.
    """
    inv_lcdp = os.path.join(data_dir, "inventario_lcdp.xlsx")
    inv_ef = os.path.join(data_dir, "inventario_ef.xlsx")

    changed_for_rollup = False

    for path in (inv_lcdp, inv_ef):
        if not os.path.exists(path):
            continue

        need, meta = imports_repo.needs_import(con, "products", path)
        if not need:
            continue

        import_id = imports_repo.create_import(con, "products", path, meta["mtime"], meta["size"], meta["hash"])
        log.info("Import productos: %s (import_id=%s)", os.path.basename(path), import_id)

        df_prod = _leer_inventario_xlsx(path, os.path.basename(path))
        products_repo.upsert_products_snapshot(
            con,
            import_id,
            df_prod,
            replace_sources=True,
        )
        changed_for_rollup = True

    if os.path.exists(inv_lcdp):
        need2, meta2 = imports_repo.needs_import(con, "presentations", inv_lcdp)
        if need2:
            import_id2 = imports_repo.create_import(con, "presentations", inv_lcdp, meta2["mtime"], meta2["size"], meta2["hash"])
            log.info("Import presentaciones: %s (import_id=%s)", os.path.basename(inv_lcdp), import_id2)

            df_pres = cargar_presentaciones(inv_lcdp)
            presentations_repo.upsert_presentations_snapshot(con, import_id2, df_pres, replace_current=True)
            changed_for_rollup = True

        need3, meta3 = imports_repo.needs_import(con, "presentacion_prod", inv_lcdp)
        if need3:
            import_id3 = imports_repo.create_import(con, "presentacion_prod", inv_lcdp, meta3["mtime"], meta3["size"], meta3["hash"])
            log.info("Import presentacion_prod: %s (import_id=%s)", os.path.basename(inv_lcdp), import_id3)

            df_pres_prod = cargar_presentaciones_prod(inv_lcdp)
            presentations_repo.upsert_presentacion_prod_snapshot(con, import_id3, df_pres_prod, replace_current=True)
            changed_for_rollup = True

    if changed_for_rollup:
        presentations_repo.rebuild_presentations_rollup(con)


def load_catalog_from_db(con) -> tuple[pd.DataFrame, pd.DataFrame]:
    df_prod = products_repo.load_products_current(con)
    df_pres = presentations_repo.load_presentations_current(con)
    df_pres = _normalize_presentations_df_for_app(df_pres)
    return df_prod, df_pres
