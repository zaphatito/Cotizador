import sqlite3

from sqlModels.db import connect, ensure_schema
from sqlModels.presentations_repo import rebuild_presentations_rollup


def test_rebuild_presentations_rollup_requires_real_service_product(tmp_path):
    db_path = tmp_path / "rollup.sqlite3"
    con = connect(str(db_path))
    ensure_schema(con)

    con.execute(
        """
        INSERT INTO products_current(
            id, codigo, nombre, categoria, departamento, genero, ml,
            cantidad_disponible, p_max, p_min, p_oferta, precio_venta,
            fuente, updated_at
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "ESENCIAS",
            "ESENCIAS",
            "ESENCIAS",
            "ESENCIAS",
            "ESENCIAS",
            "Otro",
            "",
            0.0,
            0.0,
            0.0,
            0.0,
            1,
            "",
            "2026-03-04T00:00:00",
        ),
    )
    con.execute(
        """
        INSERT INTO products_current(
            id, codigo, nombre, categoria, departamento, genero, ml,
            cantidad_disponible, p_max, p_min, p_oferta, precio_venta,
            fuente, updated_at
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "BASE02",
            "BASE02",
            "ALCOHOL PERFUMERIA",
            "ESENCIAS",
            "ESENCIAS",
            "Otro",
            "",
            100.0,
            0.0,
            0.0,
            0.0,
            1,
            "",
            "2026-03-04T00:00:00",
        ),
    )
    con.execute(
        """
        INSERT INTO products_current(
            id, codigo, nombre, categoria, departamento, genero, ml,
            cantidad_disponible, p_max, p_min, p_oferta, precio_venta,
            fuente, updated_at
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "FIJ001",
            "FIJ001",
            "FIJADOR",
            "ESENCIAS",
            "ESENCIAS",
            "Otro",
            "",
            50.0,
            0.0,
            0.0,
            0.0,
            1,
            "",
            "2026-03-04T00:00:00",
        ),
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
            0.0,
            0.0,
            0.0,
            0,
            0.0,
            "",
            "",
            "2026-03-04T00:00:00",
        ),
    )

    rel_rows = [
        ("ESENCIAS", "0003", "ESENCIAS", "dama", 1.0, "", "2026-03-04T00:00:00"),
        ("BASE02", "0003", "ESENCIAS", "dama", 2.0, "", "2026-03-04T00:00:00"),
        ("FIJ001", "0003", "ESENCIAS", "dama", 2.0, "", "2026-03-04T00:00:00"),
    ]
    con.executemany(
        """
        INSERT INTO presentacion_prod_current(
            cod_producto, cod_presentacion, departamento, genero,
            cantidad, fuente, updated_at
        )
        VALUES(?,?,?,?,?,?,?)
        """,
        rel_rows,
    )
    con.commit()

    rebuild_presentations_rollup(con)

    row = con.execute(
        """
        SELECT stock_disponible, codigos_producto
        FROM presentations_current
        WHERE codigo_norm = '0003'
          AND departamento = 'ESENCIAS'
          AND genero = 'dama'
        """
    ).fetchone()

    assert row is not None
    assert float(row["stock_disponible"]) == 0.0
    assert str(row["codigos_producto"]) == "BASE02,ESENCIAS,FIJ001"

    con.close()


def test_rebuild_presentations_rollup_uses_best_matching_service_product(tmp_path):
    db_path = tmp_path / "rollup-service.sqlite3"
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
            ("BASE02", "BASE02", "ALCOHOL PERFUMERIA", "ESENCIAS", "ESENCIAS", "Otro", "", 700.0, 0.0, 0.0, 0.0, 1, "", "2026-03-04T00:00:00"),
            ("FIJ001", "FIJ001", "FIJADOR", "ESENCIAS", "ESENCIAS", "Otro", "", 10.0, 0.0, 0.0, 0.0, 1, "", "2026-03-04T00:00:00"),
            ("CC001", "CC001", "ESENCIA HOMBRE 1", "ESENCIAS", "ESENCIAS", "caballero", "", 90.0, 0.0, 0.0, 0.0, 1, "", "2026-03-04T00:00:00"),
            ("CC002", "CC002", "ESENCIA HOMBRE 2", "ESENCIAS", "ESENCIAS", "caballero", "", 60.0, 0.0, 0.0, 0.0, 1, "", "2026-03-04T00:00:00"),
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
            "0100",
            "ESENCIAS",
            "caballero",
            "0100",
            "KIT 100 ML",
            "",
            0.0,
            0.0,
            0.0,
            0,
            0.0,
            "",
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
            ("BASE02", "0100", "ESENCIAS", "caballero", 70.0, "", "2026-03-04T00:00:00"),
            ("ESENCIAS", "0100", "ESENCIAS", "caballero", 30.0, "", "2026-03-04T00:00:00"),
            ("FIJ001", "0100", "ESENCIAS", "caballero", 1.0, "", "2026-03-04T00:00:00"),
        ],
    )
    con.commit()

    rebuild_presentations_rollup(con)

    row = con.execute(
        """
        SELECT stock_disponible, codigos_producto
        FROM presentations_current
        WHERE codigo_norm = '0100'
          AND departamento = 'ESENCIAS'
          AND genero = 'caballero'
        """
    ).fetchone()

    assert row is not None
    assert float(row["stock_disponible"]) == 3.0
    assert str(row["codigos_producto"]) == "BASE02,ESENCIAS,FIJ001"

    con.close()
