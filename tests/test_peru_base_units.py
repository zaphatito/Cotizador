from copy import deepcopy

import pytest
from PySide6.QtCore import Qt

from src.product_rules import uses_gram_quantity
from src.pricing import cantidad_para_mostrar, quantity_in_grams


def base_item(code="BASE01", quantity=2):
    return {
        "codigo": code,
        "producto": "Base 1 Litro LCDP x 30 Unidades" if code == "BASE01" else "Diluyente de prueba",
        "categoria": "DILUYENTES",
        "departamento": "DILUYENTES",
        "cantidad": quantity,
        "precio": 25.0,
        "subtotal_base": 25.0 * quantity,
        "subtotal": 25.0 * quantity,
        "total": 25.0 * quantity,
        "factor_total": 1.0,
        "id_precioventa": 1,
        "_prod": {"p_max": 25.0},
    }


@pytest.mark.parametrize("country", ["PE", "PERU", "PERÚ"])
@pytest.mark.parametrize("product", [
    {"codigo": "BASE01", "categoria": "DILUYENTES"},
    {"ID": " base01 ", "CATEGORIA": "PRODUCTO", "DEPARTAMENTO": "DILUYENTES"},
])
def test_base01_peru_is_a_unit_even_in_a_weight_department(country, product):
    assert not uses_gram_quantity(product, country=country)
    assert quantity_in_grams(product, 2, country=country) == 0


@pytest.mark.parametrize("code", ["BASE02", "ES001", "FERO001", "FIJ002"])
def test_other_peru_products_keep_their_weight_rule(code):
    item = base_item(code, 0.025)
    assert uses_gram_quantity(item, country="PE")
    assert quantity_in_grams(item, country="PE") == 25


@pytest.mark.parametrize("country,grams", [("PY", 100), ("VE", 100), ("BO", 2000)])
def test_base01_exception_is_only_for_peru(country, grams):
    assert quantity_in_grams(base_item(), country=country) == grams


def test_base01_editor_keeps_whole_units_and_unit_price(qapp):
    from src.app_window_parts.models import ItemsModel

    item = base_item(quantity=1)
    model = ItemsModel([item], country="PE", converter=float, currency_code="PEN")
    quantity = model.index(0, 3)
    assert model.setData(quantity, "2", Qt.EditRole)
    assert item["cantidad"] == 2
    assert item["precio"] == 25
    assert item["total"] == 50
    assert model.data(quantity, Qt.DisplayRole) == "2"
    assert model.data(quantity, Qt.EditRole) == "2"


def test_base01_does_not_add_grams_to_labels_ticket_or_preview_and_api_keeps_units(qapp):
    from src.api.presupuesto_client import _build_presupuesto_items
    from src.app_window_parts.ticket_actions import _peru_header_extra_lines
    from src.widgets_parts.labels_dialog import LabelsDialog
    from src.widgets_parts.preview_dialog import _esencia_a_gramos

    items = [base_item(), base_item("BASE02", 0.025)]
    before = deepcopy(items)
    dialog = LabelsDialog(None, quote_code="PE-T01-0000001", country="PE", items=items)
    try:
        assert dialog.table.rowCount() == 1
        assert dialog.table.item(0, 0).text() == "BASE02"
        assert dialog.table.item(0, 1).text() == "25"
    finally:
        dialog.deleteLater()
    assert _peru_header_extra_lines(items, country="PE") == ["Total de Esencias: 25 g"]
    assert _esencia_a_gramos(items[0], 2, country="PE") == 0
    assert cantidad_para_mostrar(items[0], country="PE") == "2"
    assert _build_presupuesto_items(items, cod_pais="PE")[0]["cantidad"] == 2
    assert items == before


def test_pdf_renders_base01_as_units_and_other_diluent_as_grams(tmp_path, monkeypatch):
    from src import pdfgen

    quantities = {}
    real_quantity = pdfgen.cantidad_para_mostrar

    def record_quantity(item, *, country=None):
        result = real_quantity(item, country=country)
        quantities[item["codigo"]] = result
        return result

    monkeypatch.setattr(pdfgen, "cantidad_para_mostrar", record_quantity)
    output = tmp_path / "base01-peru.pdf"
    pdfgen.generar_pdf(
        {
            "fecha": "2026-09-14", "cliente": "Prueba de unidades", "cedula": "",
            "telefono": "", "items": [base_item(), base_item("BASE02", 0.025)],
            "subtotal_bruto": 50.625, "descuento_total": 0, "total_general": 50.625,
        },
        fixed_quote_no="PE-T01-0000001", out_path=str(output), country_code="PE",
        store_id="T01", company_type="LA CASA DEL PERFUME", currency_code="PEN",
    )
    assert output.is_file()
    assert quantities == {"BASE01": "2", "BASE02": "25 g"}
