import types
import src.pricing as pr

def test_cantidad_para_mostrar_granel_peru(monkeypatch):
    # Forzamos país y CATS
    monkeypatch.setattr(pr, "APP_COUNTRY", "PERU")
    monkeypatch.setattr(pr, "CATS", {"ESENCIA", "AROMATERAPIA", "ESENCIAS"})
    # Hacemos determinista format_grams
    monkeypatch.setattr(pr, "format_grams", lambda g: f"{int(round(g))} g")

    it = {"categoria": "ESENCIA", "cantidad": 0.005}  # 5 gramos
    assert pr.cantidad_para_mostrar(it) == "5 g"

def test_cantidad_para_mostrar_granel_no_peru(monkeypatch):
    monkeypatch.setattr(pr, "APP_COUNTRY", "PARAGUAY")
    monkeypatch.setattr(pr, "CATS", {"ESENCIA", "AROMATERAPIA"})
    it = {"categoria": "ESENCIA", "cantidad": 3}  # 3 unidades de 50g
    assert pr.cantidad_para_mostrar(it) == "150 g"

def test_precio_base_para_listado_bottles_and_granel(monkeypatch):
    monkeypatch.setattr(pr, "APP_COUNTRY", "PERU")
    monkeypatch.setattr(pr, "CATS", {"ESENCIA"})
    btl = {"categoria": "BOTELLAS", "precio_unidad": 12.0}
    gr = {"categoria": "ESENCIA", "precio_base_50g": 2.5}
    assert pr.precio_base_para_listado(btl) == 12.0
    assert pr.precio_base_para_listado(gr) == 2.5

def test_precio_unitario_por_categoria_bottles_thresholds(monkeypatch):
    monkeypatch.setattr(pr, "APP_COUNTRY", "PERU")
    monkeypatch.setattr(pr, "CATS", {"ESENCIA"})
    prod = {"precio_venta": 10.0, "precio_oferta": 9.0, "precio_minimo": 8.0}

    assert pr.precio_unitario_por_categoria("BOTELLAS", prod, 1) == 10.0
    assert pr.precio_unitario_por_categoria("BOTELLAS", prod, 12) == 9.0
    assert pr.precio_unitario_por_categoria("BOTELLAS", prod, 100) == 8.0

def test_precio_unitario_por_categoria_granel_country_factor(monkeypatch):
    monkeypatch.setattr(pr, "CATS", {"ESENCIA"})
    prod = {"precio_base_50g": 3.0}

    monkeypatch.setattr(pr, "APP_COUNTRY", "PERU")
    assert pr.precio_unitario_por_categoria("ESENCIA", prod, 1) == 3.0

    monkeypatch.setattr(pr, "APP_COUNTRY", "PARAGUAY")
    assert pr.precio_unitario_por_categoria("ESENCIA", prod, 1) == 3.0 * 50.0

def test_reglas_cantidad(monkeypatch):
    monkeypatch.setattr(pr, "CATS", {"ESENCIA"})
    monkeypatch.setattr(pr, "APP_COUNTRY", "PERU")
    assert pr.reglas_cantidad("ESENCIA") == (0.001, 0.001)
    monkeypatch.setattr(pr, "APP_COUNTRY", "PARAGUAY")
    assert pr.reglas_cantidad("ESENCIA") == (1.0, 1.0)
