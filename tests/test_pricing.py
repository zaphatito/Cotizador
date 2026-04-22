import src.pricing as pr


def test_cantidad_para_mostrar_granel_peru(monkeypatch):
    monkeypatch.setattr(pr, "APP_COUNTRY", "PERU")
    monkeypatch.setattr(pr, "CATS", {"ESENCIA", "AROMATERAPIA", "ESENCIAS"})
    monkeypatch.setattr(pr, "format_grams", lambda g: f"{int(round(g))} g")

    it = {"codigo": "ES001", "categoria": "ESENCIA", "cantidad": 0.005}
    assert pr.cantidad_para_mostrar(it) == "5 g"


def test_cantidad_para_mostrar_granel_paraguay_regular(monkeypatch):
    monkeypatch.setattr(pr, "APP_COUNTRY", "PARAGUAY")
    monkeypatch.setattr(pr, "CATS", {"ESENCIA", "AROMATERAPIA", "ESENCIAS"})
    monkeypatch.setattr(pr, "is_py_unit_product", lambda value, country=None: False)

    it = {"codigo": "ES001", "categoria": "ESENCIA", "cantidad": 3}
    assert pr.cantidad_para_mostrar(it) == "150 g"


def test_cantidad_para_mostrar_granel_paraguay_unit_exception(monkeypatch):
    monkeypatch.setattr(pr, "APP_COUNTRY", "PARAGUAY")
    monkeypatch.setattr(pr, "CATS", {"ESENCIA", "AROMATERAPIA", "ESENCIAS"})
    monkeypatch.setattr(
        pr,
        "is_py_unit_product",
        lambda value, country=None: str((value or {}).get("codigo") or "").upper() in {"FERO001", "FIJ002"},
    )

    it = {"codigo": "FERO001", "categoria": "ESENCIAS", "cantidad": 3}
    assert pr.cantidad_para_mostrar(it) == "3"


def test_factor_total_por_categoria_respects_py_unit_exception(monkeypatch):
    monkeypatch.setattr(pr, "APP_COUNTRY", "PARAGUAY")
    monkeypatch.setattr(pr, "CATS", {"ESENCIA", "AROMATERAPIA", "ESENCIAS"})
    monkeypatch.setattr(
        pr,
        "is_py_unit_product",
        lambda value, country=None: str((value or {}).get("codigo") or "").upper() in {"FERO001", "FIJ002"},
    )

    assert pr.factor_total_por_categoria("ESENCIAS", {"codigo": "FERO001"}) == 1.0
    assert pr.factor_total_por_categoria("ESENCIAS", {"codigo": "FIJ002"}) == 1.0
    assert pr.factor_total_por_categoria("ESENCIAS", {"codigo": "ES001"}) == 50.0
    assert pr.factor_total_por_categoria("BOTELLAS", {"codigo": "BT001"}) == 1.0
