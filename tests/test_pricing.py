import pytest

import src.pricing as pr


def test_cantidad_para_mostrar_granel_peru(monkeypatch):
    monkeypatch.setattr(pr, "APP_COUNTRY", "PERU")
    monkeypatch.setattr(pr, "format_grams", lambda g: f"{int(round(g))} g")

    it = {"codigo": "ES001", "categoria": "ESENCIA", "cantidad": 0.005}
    assert pr.cantidad_para_mostrar(it) == "5 g"


def test_cantidad_para_mostrar_granel_paraguay_regular(monkeypatch):
    monkeypatch.setattr(pr, "APP_COUNTRY", "PARAGUAY")

    it = {"codigo": "ES001", "categoria": "ESENCIA", "cantidad": 3}
    assert pr.cantidad_para_mostrar(it) == "150 g"


def test_cantidad_para_mostrar_granel_paraguay_unit_exception(monkeypatch):
    monkeypatch.setattr(pr, "APP_COUNTRY", "PARAGUAY")

    it = {"codigo": "FERO001", "categoria": "ESENCIAS", "cantidad": 3}
    assert pr.cantidad_para_mostrar(it) == "3"


def test_factor_total_por_categoria_respects_py_unit_exception(monkeypatch):
    monkeypatch.setattr(pr, "APP_COUNTRY", "PARAGUAY")

    assert pr.factor_total_por_categoria("ESENCIAS", {"codigo": "FERO001"}) == 1.0
    assert pr.factor_total_por_categoria("ESENCIAS", {"codigo": "FIJ002"}) == 1.0
    assert pr.factor_total_por_categoria("ESENCIAS", {"codigo": "ES001"}) == 50.0
    assert pr.factor_total_por_categoria("BOTELLAS", {"codigo": "BT001"}) == 1.0


@pytest.mark.parametrize("category", ["FEROMONA", "FEROMONAS", "FIJADOR", "FIJADORES"])
def test_cantidad_para_mostrar_peru_extra_departments_in_grams(monkeypatch, category):
    monkeypatch.setattr(pr, "APP_COUNTRY", "PE")
    item = {"codigo": "EXTRA001", "departamento": category, "cantidad": 0.050}
    assert pr.cantidad_para_mostrar(item) == "50 g"


@pytest.mark.parametrize(
    ("country", "item", "expected"),
    [
        ("PE", {"categoria": "FIJADOR", "cantidad": 0.050}, 50.0),
        ("PY", {"categoria": "ESENCIAS", "cantidad": 3}, 150.0),
        ("VE", {"categoria": "ESENCIA", "cantidad": 2}, 100.0),
        ("PY", {"categoria": "FIJADOR", "cantidad": 3}, 0.0),
        ("PY", {"codigo": "FERO001", "categoria": "ESENCIAS", "cantidad": 3}, 0.0),
        ("OTRO", {"categoria": "ESENCIAS", "cantidad": 3}, 0.0),
    ],
)
def test_quantity_in_grams_uses_country_rules(country, item, expected):
    assert pr.quantity_in_grams(item, country=country) == expected


def test_factor_total_peru_extra_departments_and_non_peru_regressions(monkeypatch):
    monkeypatch.setattr(pr, "APP_COUNTRY", "PE")
    assert pr.factor_total_por_categoria("FIJADOR", {"codigo": "FIJ001"}) == 1.0
    assert pr.factor_total_por_categoria("FEROMONAS", {"codigo": "FERO001"}) == 1.0

    monkeypatch.setattr(pr, "APP_COUNTRY", "VE")
    assert pr.factor_total_por_categoria("ESENCIAS", {"codigo": "ES001"}) == 50.0
    assert pr.factor_total_por_categoria("FIJADOR", {"codigo": "FIJ001"}) == 1.0
