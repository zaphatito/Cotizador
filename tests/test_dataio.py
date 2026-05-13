import pandas as pd
import pytest

import src.dataio as dataio


def test_leer_inventario_empty_sheet_returns_empty(monkeypatch):
    class DummyXls:
        sheet_names = ["Inventario"]

    def fake_excel_file(path, engine=None):
        return DummyXls()

    def fake_read_excel(xls, sheet_name=None, header=0):
        return pd.DataFrame(
            columns=[
                "Codigo",
                "Nombre",
                "Departamento",
                "Genero",
                "Cantidad Disponible",
                "Precio Maximo",
                "Precio Minimo",
                "Precio Oferta",
            ]
        )

    monkeypatch.setattr(dataio.pd, "ExcelFile", fake_excel_file)
    monkeypatch.setattr(dataio.pd, "read_excel", fake_read_excel)

    out = dataio._leer_inventario_xlsx("fake.xlsx", "fake.xlsx")
    assert out.empty


def test_leer_inventario_bad_columns_raises(monkeypatch):
    class DummyXls:
        sheet_names = ["Inventario"]

    def fake_excel_file(path, engine=None):
        return DummyXls()

    def fake_read_excel(xls, sheet_name=None, header=0):
        return pd.DataFrame({"Otra Columna": ["x"], "Dato": [1]})

    monkeypatch.setattr(dataio.pd, "ExcelFile", fake_excel_file)
    monkeypatch.setattr(dataio.pd, "read_excel", fake_read_excel)

    with pytest.raises(ValueError, match="Formato invalido"):
        dataio._leer_inventario_xlsx("fake.xlsx", "fake.xlsx")


def test_leer_inventario_partial_empty_headers_raise(monkeypatch):
    class DummyXls:
        sheet_names = ["Inventario"]

    def fake_excel_file(path, engine=None):
        return DummyXls()

    def fake_read_excel(xls, sheet_name=None, header=0):
        return pd.DataFrame(columns=["Codigo", "Nombre"])

    monkeypatch.setattr(dataio.pd, "ExcelFile", fake_excel_file)
    monkeypatch.setattr(dataio.pd, "read_excel", fake_read_excel)

    with pytest.raises(ValueError, match="faltan columnas"):
        dataio._leer_inventario_xlsx("fake.xlsx", "fake.xlsx")
