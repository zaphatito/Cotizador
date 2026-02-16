import pandas as pd

import src.presentations as pres


def test_norm_pres_code_variants():
    assert pres.norm_pres_code("PZA") == ("PZA", False, True)
    assert pres.norm_pres_code("E100") == ("E100", False, False)
    assert pres.norm_pres_code("C240") == ("C240", False, False)
    assert pres.norm_pres_code("100") == ("0100", False, False)
    assert pres.norm_pres_code("0100") == ("0100", False, False)
    assert pres.norm_pres_code("X") == ("X", False, False)


def test_extract_ml_and_from_code():
    assert pres.extract_ml_from_text("Botella 50 ml") == 50
    assert pres.extract_ml_from_text("100ml cristal") == 100
    assert pres.extract_ml_from_text("Sin ml 2025?") in (0, 2025)
    assert pres.ml_from_pres_code_norm("0100") == 100
    assert pres.ml_from_pres_code_norm("100") == 100
    assert pres.ml_from_pres_code_norm("C240") == 240


def test_map_pc_to_bottle_code():
    assert pres.map_pc_to_bottle_code("PC100") == "C100"
    assert pres.map_pc_to_bottle_code("pc050") == "C050"
    assert pres.map_pc_to_bottle_code("C100") is None
    assert pres.map_pc_to_bottle_code("") is None


def test_read_sheet2_presentations_columns(monkeypatch):
    class DummyXls:
        sheet_names = ["Inventario", "Presentaciones"]

    def fake_excel_file(path, engine=None):
        return DummyXls()

    def fake_read_excel(xls, sheet_name=None, header=0):
        return pd.DataFrame(
            {
                "Codigo": ["0100", "C240"],
                "Departamento": ["ESENCIA", "ESENCIA"],
                "Genero": ["dama", "caballero"],
                "Nombre": ["Pres 100", "Pres 240"],
                "Descripcion": ["Desc 1", "Desc 2"],
                "Precio Minimo": [8, 9],
                "Precio Oferta": [9, 10],
                "Precio Maximo": [10, 12],
            }
        )

    monkeypatch.setattr(pres.pd, "ExcelFile", fake_excel_file)
    monkeypatch.setattr(pres.pd, "read_excel", fake_read_excel)

    out = pres.read_sheet2_presentations("fake.xlsx")
    assert list(out.columns) == [
        "codigo",
        "departamento",
        "genero",
        "nombre",
        "descripcion",
        "p_max",
        "p_min",
        "p_oferta",
    ]
    assert out.loc[0, "p_max"] == 10
    assert out.loc[1, "p_oferta"] == 10


def test_read_sheet3_presentacion_prod(monkeypatch):
    class DummyXls:
        sheet_names = ["Inventario", "Presentaciones", "PresentacionesProd"]

    def fake_excel_file(path, engine=None):
        return DummyXls()

    def fake_read_excel(xls, sheet_name=None, header=0):
        return pd.DataFrame(
            {
                "Cod Producto": ["AA01", "AA02"],
                "Cod Presentacion": ["100", "0100"],
                "Departamento": ["ESENCIA", "ESENCIA"],
                "Genero": ["dama", "caballero"],
                "Cantidad": [1, 2],
            }
        )

    monkeypatch.setattr(pres.pd, "ExcelFile", fake_excel_file)
    monkeypatch.setattr(pres.pd, "read_excel", fake_read_excel)

    out = pres.read_sheet3_presentacion_prod("fake.xlsx")
    assert list(out.columns) == ["cod_producto", "cod_presentacion", "departamento", "genero", "cantidad"]
    assert out.loc[0, "cod_presentacion"] == "0100"
    assert out.loc[1, "cantidad"] == 2.0


def test_cargar_presentaciones_fields(monkeypatch):
    df = pd.DataFrame(
        {
            "codigo": ["0100", "C240", "PZA", ""],
            "nombre": ["n1", "n2", "n3", "n4"],
            "descripcion": ["d1", "d2", "d3", "d4"],
            "departamento": ["ESENCIA", "ESENCIA", "ESENCIA", "ESENCIA"],
            "genero": ["dama", "caballero", "otro", "dama"],
            "p_max": [10.0, 12.0, 13.0, 0.0],
            "p_min": [8.0, 10.0, 11.0, 0.0],
            "p_oferta": [9.0, 11.0, 12.0, 0.0],
        }
    )
    monkeypatch.setattr(pres, "read_sheet2_presentations", lambda path: df)
    monkeypatch.setattr(pres.os.path, "exists", lambda path: True)

    out = pres.cargar_presentaciones("fake.xlsx")
    cods = set(out["CODIGO"])
    assert "PZA" not in cods
    assert "" not in cods

    rec_0100 = out[out["CODIGO"] == "0100"].iloc[0]
    assert rec_0100["P_MAX"] == 10.0
    assert rec_0100["P_MIN"] == 8.0
    assert rec_0100["P_OFERTA"] == 9.0
    assert rec_0100["PRECIO_PRESENT"] == 10.0
    assert rec_0100["REQUIERE_BOTELLA"] is False
