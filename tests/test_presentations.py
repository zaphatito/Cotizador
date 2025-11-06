import types
import pandas as pd
import pytest

import src.presentations as pres

def test_norm_pres_code_variants():
    assert pres.norm_pres_code("PZA") == ("PZA", False, True)
    assert pres.norm_pres_code("E100") == ("0100", True, False)
    assert pres.norm_pres_code("C240") == ("C240", False, False)
    assert pres.norm_pres_code("100")  == ("100", True, False)
    assert pres.norm_pres_code("0100") == ("0100", True, False)
    assert pres.norm_pres_code("X")    == ("X", False, False)

def test_extract_ml_and_from_code():
    assert pres.extract_ml_from_text("Botella 50 ml") == 50
    assert pres.extract_ml_from_text("100ml cristal") == 100
    assert pres.extract_ml_from_text("Sin ml 2025?") in (0, 2025)  # tolerante a fallback
    assert pres.ml_from_pres_code_norm("0100") == 100
    assert pres.ml_from_pres_code_norm("100") == 100
    assert pres.ml_from_pres_code_norm("C240") == 240

def test_map_pc_to_bottle_code():
    assert pres.map_pc_to_bottle_code("PC100") == "C100"
    assert pres.map_pc_to_bottle_code("pc050") == "C050"
    assert pres.map_pc_to_bottle_code("C100") is None
    assert pres.map_pc_to_bottle_code("") is None

def test_read_sheet2_presentations_picks_precio_maximo(monkeypatch):
    class DummyXls:
        sheet_names = ["Inventario", "Hoja 2"]
    def fake_excel_file(path, engine=None):
        return DummyXls()
    def fake_read_excel(xls, sheet_name=None, header=0):
        # Incluye varias columnas de precio; debe elegir "Precio Maximo"
        return pd.DataFrame({
            "Codigo": ["0100", "C240"],
            "Nombre": ["Pres 100", "Pres 240"],
            "Departamento": ["ESENCIA", "ESENCIA"],
            "Género": ["dama", "caballero"],
            "Precio Minimo": [8, 9],
            "Precio Oferta": [9, 10],
            "Precio Maximo": [10, 12]
        })

    monkeypatch.setattr(pres.pd, "ExcelFile", fake_excel_file)
    monkeypatch.setattr(pres.pd, "read_excel", fake_read_excel)

    out = pres.read_sheet2_presentations("fake.xlsx")
    assert list(out.columns) == ["codigo", "nombre", "departamento", "genero", "p_venta"]
    assert out.loc[0, "p_venta"] == 10
    assert out.loc[1, "p_venta"] == 12

def test_cargar_presentaciones_flags(monkeypatch):
    df = pd.DataFrame({
        "codigo": ["0100", "E100", "C240", "PZA", ""],
        "nombre": ["n1", "n2", "n3", "n4", "n5"],
        "departamento": ["ESENCIA", "ESENCIA", "ESENCIA", "ESENCIA", "ESENCIA"],
        "genero": ["dama", "dama", "caballero", "otro", "dama"],
        "p_venta": [10.0, 11.0, 12.0, 13.0, 0.0]
    })
    monkeypatch.setattr(pres, "read_sheet2_presentations", lambda path: df)

    out = pres.cargar_presentaciones("fake.xlsx")
    cods = set(out["CODIGO"])
    assert "PZA" not in cods           # ignorado
    assert "" not in cods              # vacío ignorado
    # Verificar flags
    rec_0100 = out[out["CODIGO"] == "0100"].iloc[0]
    assert rec_0100["CODIGO_NORM"] == "0100" and rec_0100["REQUIERE_BOTELLA"] is True
    rec_C240 = out[out["CODIGO"] == "C240"].iloc[0]
    assert rec_C240["CODIGO_NORM"] == "C240" and rec_C240["REQUIERE_BOTELLA"] is False
