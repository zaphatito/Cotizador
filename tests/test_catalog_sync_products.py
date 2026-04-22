from pathlib import Path

import pandas as pd

from sqlModels.db import connect, ensure_schema
from src.catalog_sync import sync_catalog_from_excel_path, sync_catalog_from_excel_to_db


def _products_df(codes: list[str], fuente: str) -> pd.DataFrame:
    rows: list[dict] = []
    for code in codes:
        rows.append(
            {
                "CODIGO": code,
                "NOMBRE": f"Producto {code}",
                "DEPARTAMENTO": "ESENCIAS",
                "GENERO": "dama",
                "CANTIDAD_DISPONIBLE": 10.0,
                "P_MAX": 1.0,
                "P_MIN": 0.9,
                "P_OFERTA": 0.8,
                "PRECIO_VENTA": 1,
                "__FUENTE": fuente,
            }
        )
    return pd.DataFrame(rows)


def _current_products(con) -> list[tuple[str, str]]:
    rows = con.execute(
        """
        SELECT id, COALESCE(fuente, '') AS fuente
        FROM products_current
        ORDER BY id
        """
    ).fetchall()
    return [(str(r["id"]), str(r["fuente"])) for r in rows]


def _table_exists(con, name: str) -> bool:
    row = con.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        LIMIT 1
        """,
        (name,),
    ).fetchone()
    return row is not None


def _patch_catalog_readers(monkeypatch, inventory_by_file: dict[str, pd.DataFrame]) -> None:
    def fake_inventory_reader(path: str, fuente: str) -> pd.DataFrame:
        return inventory_by_file[Path(path).name].copy()

    monkeypatch.setattr("src.catalog_sync._leer_inventario_xlsx", fake_inventory_reader)
    monkeypatch.setattr("src.catalog_sync.cargar_presentaciones", lambda _path: pd.DataFrame())
    monkeypatch.setattr("src.catalog_sync.cargar_presentaciones_prod", lambda _path: pd.DataFrame())


def test_sync_catalog_from_excel_path_replaces_current_products(tmp_path, monkeypatch):
    db_path = tmp_path / "catalog.sqlite3"
    con = connect(str(db_path))
    ensure_schema(con)

    old_excel = tmp_path / "inventario_old.xlsx"
    new_excel = tmp_path / "inventario_new.xlsx"
    old_excel.write_text("old", encoding="utf-8")
    new_excel.write_text("new-version", encoding="utf-8")

    inventory_by_file = {
        old_excel.name: _products_df(["AAA001", "BBB001"], old_excel.name),
        new_excel.name: _products_df(["AAA001"], new_excel.name),
    }
    _patch_catalog_readers(monkeypatch, inventory_by_file)

    sync_catalog_from_excel_path(con, str(old_excel))
    assert _current_products(con) == [
        ("AAA001", old_excel.name),
        ("BBB001", old_excel.name),
    ]

    sync_catalog_from_excel_path(con, str(new_excel))
    assert _current_products(con) == [("AAA001", new_excel.name)]

    con.close()


def test_sync_catalog_from_excel_to_db_replaces_only_changed_source(tmp_path, monkeypatch):
    db_path = tmp_path / "catalog.sqlite3"
    con = connect(str(db_path))
    ensure_schema(con)

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    lcdp = data_dir / "inventario_lcdp.xlsx"
    ef = data_dir / "inventario_ef.xlsx"
    lcdp.write_text("lcdp-v1", encoding="utf-8")
    ef.write_text("ef-v1", encoding="utf-8")

    inventory_by_file = {
        lcdp.name: _products_df(["AAA001", "BBB001"], lcdp.name),
        ef.name: _products_df(["CCC001"], ef.name),
    }
    _patch_catalog_readers(monkeypatch, inventory_by_file)

    sync_catalog_from_excel_to_db(con, str(data_dir))
    assert _current_products(con) == [
        ("AAA001", lcdp.name),
        ("BBB001", lcdp.name),
        ("CCC001", ef.name),
    ]

    inventory_by_file[lcdp.name] = _products_df(["AAA001"], lcdp.name)
    lcdp.write_text("lcdp-v2-with-change", encoding="utf-8")

    sync_catalog_from_excel_to_db(con, str(data_dir))
    assert _current_products(con) == [
        ("AAA001", lcdp.name),
        ("CCC001", ef.name),
    ]

    con.close()


def test_schema_drops_duplicate_catalog_tables(tmp_path):
    db_path = tmp_path / "catalog.sqlite3"
    con = connect(str(db_path))
    ensure_schema(con)

    assert not _table_exists(con, "producto_current")
    assert not _table_exists(con, "producto_hist")
    assert not _table_exists(con, "presentacion_current")
    assert not _table_exists(con, "presentacion_hist")
    assert _table_exists(con, "products_current")
    assert _table_exists(con, "products_hist")
    assert _table_exists(con, "presentations_current")
    assert _table_exists(con, "presentations_hist")
    assert _table_exists(con, "presentacion_prod_current")
    assert _table_exists(con, "presentacion_prod_hist")

    con.close()
