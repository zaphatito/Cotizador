from sqlModels.db import connect, ensure_schema
from src.ai.search_index import LocalSearchIndex


def test_search_products_does_not_use_fuzzy_fallback(tmp_path, monkeypatch):
    db_path = tmp_path / "search.sqlite3"
    con = connect(str(db_path))
    ensure_schema(con)

    con.executemany(
        """
        INSERT INTO products_current(
            id, codigo, nombre, categoria, departamento, genero, ml,
            cantidad_disponible, p_max, p_min, p_oferta, precio_venta,
            fuente, updated_at
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        [
            (
                "ACT001",
                "ACT001",
                "ACTIVADOR",
                "OTROS",
                "OTROS",
                "Otro",
                "",
                10.0,
                1.0,
                1.0,
                1.0,
                1,
                "",
                "2026-03-04T00:00:00",
            ),
            (
                "ZZZ001",
                "ZZZ001",
                "PRODUCTO ZETA",
                "OTROS",
                "OTROS",
                "Otro",
                "",
                10.0,
                1.0,
                1.0,
                1.0,
                1,
                "",
                "2026-03-04T00:00:00",
            ),
        ],
    )
    con.commit()
    con.close()

    def _should_not_run(*args, **kwargs):
        raise AssertionError("search_products no debe usar rapidfuzz en la UI")

    monkeypatch.setattr("src.ai.search_index.process.extract", _should_not_run)

    idx = LocalSearchIndex(str(db_path))
    rows = idx.search_products("ac", limit=10)

    assert rows
    assert rows[0]["codigo"] == "ACT001"


def test_search_products_ignores_fixed_components_when_relation_has_category_marker(tmp_path):
    db_path = tmp_path / "search-combos.sqlite3"
    con = connect(str(db_path))
    ensure_schema(con)

    con.executemany(
        """
        INSERT INTO products_current(
            id, codigo, nombre, categoria, departamento, genero, ml,
            cantidad_disponible, p_max, p_min, p_oferta, precio_venta,
            fuente, updated_at
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        [
            ("ESENCIAS", "ESENCIAS", "ESENCIAS", "ESENCIAS", "ESENCIAS", "Otro", "", 0.0, 1.0, 1.0, 1.0, 1, "", "2026-03-04T00:00:00"),
            ("BASE02", "BASE02", "ALCOHOL PERFUMERIA", "ESENCIAS", "ESENCIAS", "dama", "", 10.0, 1.0, 1.0, 1.0, 1, "", "2026-03-04T00:00:00"),
            ("FIJ001", "FIJ001", "FIJADOR", "ESENCIAS", "ESENCIAS", "dama", "", 10.0, 1.0, 1.0, 1.0, 1, "", "2026-03-04T00:00:00"),
            ("ESENH001", "ESENH001", "ESENCIA HOMME", "ESENCIAS", "ESENCIAS", "dama", "", 10.0, 1.0, 1.0, 1.0, 1, "", "2026-03-04T00:00:00"),
        ],
    )
    con.execute(
        """
        INSERT INTO presentations_current(
            codigo_norm, departamento, genero, codigo, nombre, descripcion,
            p_max, p_min, p_oferta, requiere_botella,
            stock_disponible, codigos_producto, fuente, updated_at
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "0003",
            "ESENCIAS",
            "dama",
            "0003",
            "KIT 3 ML",
            "",
            1.0,
            1.0,
            1.0,
            0,
            1.0,
            "BASE02,ESENCIAS,FIJ001",
            "",
            "2026-03-04T00:00:00",
        ),
    )
    con.executemany(
        """
        INSERT INTO presentacion_prod_current(
            cod_producto, cod_presentacion, departamento, genero,
            cantidad, fuente, updated_at
        )
        VALUES(?,?,?,?,?,?,?)
        """,
        [
            ("BASE02", "0003", "ESENCIAS", "dama", 1.0, "", "2026-03-04T00:00:00"),
            ("ESENCIAS", "0003", "ESENCIAS", "dama", 1.0, "", "2026-03-04T00:00:00"),
            ("FIJ001", "0003", "ESENCIAS", "dama", 1.0, "", "2026-03-04T00:00:00"),
        ],
    )
    con.commit()
    con.close()

    idx = LocalSearchIndex(str(db_path))
    rows_ok = idx.search_products("ESENH0010003", limit=30)
    codes_ok = {str(r.get("codigo") or "").strip().upper() for r in rows_ok}
    rows_bad = idx.search_products("BASE020003", limit=30)
    codes_bad = {str(r.get("codigo") or "").strip().upper() for r in rows_bad}

    assert "ESENH0010003" in codes_ok
    assert "BASE020003" not in codes_bad
    assert "FIJ0010003" not in codes_bad
