import pytest
from PySide6.QtCore import Qt

# La aplicación carga primero el historial de cotizaciones; mantener ese orden
# evita el ciclo de imports legacy entre widgets y app_window_parts.
import src.app  # noqa: F401
from src.app_window_parts import add_items
from src.app_window_parts.add_items import AddItemsMixin
from src.app_window_parts import models
from src.app_window_parts.models import ItemsModel


class _Quote(AddItemsMixin):
    def __init__(self, product: dict):
        self.productos = [product]
        self.presentaciones = []
        self._botellas_pc = []
        self.items = []
        self.model = ItemsModel(self.items)


def _product(category: str = "FIJADOR") -> dict:
    return {
        "id": "FIJ001",
        "nombre": "Fijador",
        "categoria": category,
        "cantidad_disponible": 10.0,
        "p_max": 240.0002,
        "p_min": 220.0,
        "p_oferta": 230.0,
    }


@pytest.fixture
def peru_quote(monkeypatch):
    monkeypatch.setattr(add_items, "APP_COUNTRY", "PERU")
    monkeypatch.setattr(add_items, "listing_allows_products", lambda: True)
    return _Quote(_product())


def test_peru_fijador_starts_at_one_gram(peru_quote):
    assert peru_quote._agregar_por_codigo("FIJ001", silent=True)

    item = peru_quote.items[0]
    assert item["cantidad"] == pytest.approx(0.001)
    assert item["subtotal_base"] == pytest.approx(0.24)


def test_peru_fijador_recommended_quantity_remains_decimal(peru_quote):
    assert peru_quote.agregar_recomendado("FIJ001", qty=0.050)

    item = peru_quote.items[0]
    assert item["cantidad"] == pytest.approx(0.050)
    assert item["subtotal_base"] == pytest.approx(12.00)


@pytest.mark.parametrize("category", ["FIJADOR", "FEROMONAS"])
def test_peru_gram_quantity_editor_converts_50_to_point_050(monkeypatch, category):
    monkeypatch.setattr(models, "APP_COUNTRY", "PERU")
    prod = _product(category)
    item = {
        "_prod": prod,
        "codigo": prod["id"],
        "producto": prod["nombre"],
        "categoria": category,
        "cantidad": 0.001,
        "precio": prod["p_max"],
        "subtotal_base": 0.24,
        "total": 0.24,
        "factor_total": 1.0,
        "id_precioventa": 1,
        "precio_tier": "unitario",
        "precio_override": None,
    }
    model = ItemsModel([item])
    quantity_index = model.index(0, 3)

    assert model.setData(quantity_index, "50", Qt.EditRole)
    assert item["cantidad"] == pytest.approx(0.050)
    assert item["subtotal_base"] == pytest.approx(12.00)
    assert model.data(quantity_index, Qt.DisplayRole) == "0.050"
    assert model.data(quantity_index, Qt.EditRole) == "0.050"


def test_peru_feromonas_recommendation_preview_keeps_decimal_qty(monkeypatch):
    monkeypatch.setattr(models, "APP_COUNTRY", "PERU")
    model = ItemsModel([])

    model.set_recommendations_preview(
        [
            {
                "codigo": "FERO001",
                "nombre": "Feromona",
                "categoria": "FEROMONAS",
                "qty": 0.050,
                "price_base": 240.0002,
            }
        ]
    )

    payload = model.get_preview_payload(0)
    assert payload is not None
    assert payload["qty"] == pytest.approx(0.050)
    assert model.data(model.index(0, 3), Qt.DisplayRole) == "0.050"


@pytest.mark.parametrize("country", ["PARAGUAY", "VENEZUELA"])
def test_non_peru_bulk_quantity_editor_keeps_whole_units(monkeypatch, country):
    monkeypatch.setattr(models, "APP_COUNTRY", country)
    item = {
        "codigo": "ES001",
        "producto": "Esencia",
        "categoria": "ESENCIA",
        "cantidad": 1,
        "precio": 10.0,
        "factor_total": 1.0,
        "_prod": {"p_max": 10.0},
        "id_precioventa": 1,
    }
    model = ItemsModel([item])
    quantity_index = model.index(0, 3)

    assert model.setData(quantity_index, "50", Qt.EditRole)
    assert item["cantidad"] == 50
    assert model.data(quantity_index, Qt.DisplayRole) == "50"
