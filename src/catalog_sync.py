# src/catalog_sync.py
from __future__ import annotations

import os
import math
import pandas as pd

from .logging_setup import get_logger
from .config import CATS
from .dataio import _leer_inventario_xlsx  # header=4
from .presentations import cargar_presentaciones  # header=4

import sqlModels.imports_repo as imports_repo
import sqlModels.products_repo as products_repo
import sqlModels.presentations_repo as presentations_repo

log = get_logger(__name__)


def _to_float(v, default: float = 0.0) -> float:
    try:
        if v is None:
            return float(default)
        try:
            if pd.isna(v):
                return float(default)
        except Exception:
            pass
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return float(default)
            s = s.replace(",", "")
            v = s
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return float(default)
        return x
    except Exception:
        return float(default)


def _products_df_for_repo(df_raw: pd.DataFrame) -> pd.DataFrame:
    """
    Asegura SIEMPRE columnas numéricas NOT NULL:
      precio_unitario, precio_unidad, precio_base_50g
    """
    if df_raw is None or df_raw.empty:
        return pd.DataFrame()

    out_rows: list[dict] = []

    for _, r in df_raw.iterrows():
        pid = str(r.get("id") or "").strip()
        if not pid:
            continue

        cat = str(r.get("categoria") or "").upper().strip()

        precio_venta = _to_float(r.get("precio_venta"), 0.0)
        precio_oferta_base = _to_float(r.get("precio_oferta_base"), 0.0)
        precio_minimo_base = _to_float(r.get("precio_minimo_base"), 0.0)

        row = {
            "id": pid,
            "nombre": str(r.get("nombre") or ""),
            "categoria": cat,
            "genero": str(r.get("genero") or ""),
            "ml": str(r.get("ml") or ""),
            "cantidad_disponible": _to_float(r.get("cantidad_disponible"), 0.0),

            "precio_venta": precio_venta,
            "precio_oferta_base": precio_oferta_base,
            "precio_minimo_base": precio_minimo_base,

            # NOT NULL siempre presentes
            "precio_unitario": 0.0,
            "precio_unidad": 0.0,
            "precio_base_50g": 0.0,

            "__fuente": str(r.get("__fuente") or r.get("fuente") or ""),
        }

        if cat == "BOTELLAS":
            row["precio_unidad"] = precio_venta
        elif cat in CATS:
            row["precio_base_50g"] = precio_venta
        else:
            row["precio_unitario"] = precio_venta

        out_rows.append(row)

    df = pd.DataFrame(out_rows)

    for col in [
        "cantidad_disponible",
        "precio_venta",
        "precio_oferta_base",
        "precio_minimo_base",
        "precio_unitario",
        "precio_unidad",
        "precio_base_50g",
    ]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    return df


def _normalize_presentations_df_for_app(df: pd.DataFrame) -> pd.DataFrame:
    """
    DB -> app (mayúsculas).
    """
    if df is None or df.empty:
        return pd.DataFrame()

    if "CODIGO_NORM" in df.columns and "PRECIO_PRESENT" in df.columns:
        out = df.copy()
        out["REQUIERE_BOTELLA"] = out.get("REQUIERE_BOTELLA", False).astype(bool)
        return out

    low = {str(c).strip().lower(): c for c in df.columns}
    mapping = {}
    if "codigo_norm" in low:       mapping[low["codigo_norm"]] = "CODIGO_NORM"
    if "codigo" in low:            mapping[low["codigo"]] = "CODIGO"
    if "nombre" in low:            mapping[low["nombre"]] = "NOMBRE"
    if "departamento" in low:      mapping[low["departamento"]] = "DEPARTAMENTO"
    if "genero" in low:            mapping[low["genero"]] = "GENERO"
    if "precio_present" in low:    mapping[low["precio_present"]] = "PRECIO_PRESENT"
    if "requiere_botella" in low:  mapping[low["requiere_botella"]] = "REQUIERE_BOTELLA"

    out = df.rename(columns=mapping).copy()

    for k in ["CODIGO", "CODIGO_NORM", "NOMBRE", "DEPARTAMENTO", "GENERO"]:
        if k not in out.columns:
            out[k] = ""
    if "PRECIO_PRESENT" not in out.columns:
        out["PRECIO_PRESENT"] = 0.0
    if "REQUIERE_BOTELLA" not in out.columns:
        out["REQUIERE_BOTELLA"] = False

    out["CODIGO"] = out["CODIGO"].astype(str).str.strip().str.upper()
    out["CODIGO_NORM"] = out["CODIGO_NORM"].astype(str).str.strip().str.upper()
    out["DEPARTAMENTO"] = out["DEPARTAMENTO"].astype(str).str.upper()
    out["PRECIO_PRESENT"] = pd.to_numeric(out["PRECIO_PRESENT"], errors="coerce").fillna(0.0)

    def _to_bool(x):
        try:
            if isinstance(x, (int, float)):
                return bool(int(x))
        except Exception:
            pass
        s = str(x).strip().lower()
        if s in ("1", "true", "t", "yes", "si"):
            return True
        if s in ("0", "false", "f", "no"):
            return False
        return bool(x)

    out["REQUIERE_BOTELLA"] = out["REQUIERE_BOTELLA"].map(_to_bool)
    return out


def sync_catalog_from_excel_path(con, excel_path: str) -> None:
    """
    ✅ Actualiza catálogo en DB usando UN SOLO excel seleccionado:
      - Productos: hoja 1 (sheet 0), header=4
      - Presentaciones: hoja 2 vía cargar_presentaciones(), header=4

    Se registra en imports con source_file = excel_path (para detección de cambios).
    """
    if not excel_path or not os.path.exists(excel_path):
        raise FileNotFoundError(f"No existe el Excel: {excel_path}")

    # Productos
    need, meta = imports_repo.needs_import(con, "products", excel_path)
    if need:
        import_id = imports_repo.create_import(con, "products", excel_path, meta["mtime"], meta["size"], meta["hash"])
        log.info("Import productos (seleccionado): %s (import_id=%s)", os.path.basename(excel_path), import_id)
        df_raw = _leer_inventario_xlsx(excel_path, os.path.basename(excel_path))
        df_for_repo = _products_df_for_repo(df_raw)
        products_repo.upsert_products_snapshot(con, import_id, df_for_repo)

    # Presentaciones
    need2, meta2 = imports_repo.needs_import(con, "presentations", excel_path)
    if need2:
        import_id2 = imports_repo.create_import(con, "presentations", excel_path, meta2["mtime"], meta2["size"], meta2["hash"])
        log.info("Import presentaciones (seleccionado): %s (import_id=%s)", os.path.basename(excel_path), import_id2)
        df_pres = cargar_presentaciones(excel_path)
        presentations_repo.upsert_presentations_snapshot(con, import_id2, df_pres)


def sync_catalog_from_excel_to_db(con, data_dir: str) -> None:
    """
    Modo “startup”: busca inventario_lcdp.xlsx e inventario_ef.xlsx en DATA_DIR.
    """
    inv_lcdp = os.path.join(data_dir, "inventario_lcdp.xlsx")
    inv_ef = os.path.join(data_dir, "inventario_ef.xlsx")

    for path in (inv_lcdp, inv_ef):
        if not os.path.exists(path):
            continue

        need, meta = imports_repo.needs_import(con, "products", path)
        if not need:
            continue

        import_id = imports_repo.create_import(con, "products", path, meta["mtime"], meta["size"], meta["hash"])
        log.info("Import productos: %s (import_id=%s)", os.path.basename(path), import_id)

        df_raw = _leer_inventario_xlsx(path, os.path.basename(path))
        df_for_repo = _products_df_for_repo(df_raw)
        products_repo.upsert_products_snapshot(con, import_id, df_for_repo)

    if os.path.exists(inv_lcdp):
        need, meta = imports_repo.needs_import(con, "presentations", inv_lcdp)
        if need:
            import_id = imports_repo.create_import(con, "presentations", inv_lcdp, meta["mtime"], meta["size"], meta["hash"])
            log.info("Import presentaciones: %s (import_id=%s)", os.path.basename(inv_lcdp), import_id)
            df_pres = cargar_presentaciones(inv_lcdp)
            presentations_repo.upsert_presentations_snapshot(con, import_id, df_pres)


def load_catalog_from_db(con) -> tuple[pd.DataFrame, pd.DataFrame]:
    df_prod = products_repo.load_products_current(con)
    df_pres = presentations_repo.load_presentations_current(con)
    df_pres = _normalize_presentations_df_for_app(df_pres)
    return df_prod, df_pres
