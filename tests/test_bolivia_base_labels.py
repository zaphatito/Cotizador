from copy import deepcopy
from unittest.mock import Mock
from PIL import Image

import pytest

from src.widgets_parts import labels_dialog as module


@pytest.mark.parametrize('country', ['BO', 'BOL', 'BOLIVIA'])
@pytest.mark.parametrize('category', ['ESENCIA', 'ESENCIAS', 'AROMATERAPIA', 'DILUYENTES'])
def test_bolivia_cats_keep_entered_quantity_and_unit_price(qapp, country, category):
    from PySide6.QtCore import Qt
    from src.app_window_parts.models import ItemsModel
    from src.api.presupuesto_client import _build_presupuesto_items
    from src.pricing import cantidad_para_mostrar, quantity_in_grams

    item = dict(codigo='TEST', producto='Producto de prueba', categoria=category,
                cantidad=1, precio=2, _prod=dict(p_max=2))
    model = ItemsModel([item], country=country, converter=float, currency_code='BOB')
    index = model.index(0, 3)
    assert model.setData(index, '50', Qt.EditRole)
    assert item['cantidad'] == 50
    assert item['total'] == 100
    assert model.data(index, Qt.DisplayRole) == '50.000'
    assert model.data(index, Qt.EditRole) == '50.000'
    assert quantity_in_grams(item, country=country) == 50
    assert cantidad_para_mostrar(item, country=country) == '50 g'
    assert _build_presupuesto_items([item], cod_pais=country)[0]['cantidad'] == 50
    from src.app_window_parts.ticket_actions import _peru_header_extra_lines
    from src.widgets_parts.preview_dialog import _esencia_a_gramos
    assert _peru_header_extra_lines([item], country=country) == ['Total de Esencias: 50 g']
    assert _esencia_a_gramos(item, 50, country=country) == 50
    assert model.setData(index, '2,5', Qt.EditRole)
    assert item['cantidad'] == 2.5
    assert item['total'] == 5


@pytest.mark.parametrize('country,expected', [('BO', 1), ('BOL', 1), ('PE', 0.001)])
def test_new_weight_product_starts_with_country_quantity(qapp, monkeypatch, country, expected):
    from types import SimpleNamespace
    from src.app_window_parts import add_items
    from src.app_window_parts.models import ItemsModel

    monkeypatch.setattr(add_items, 'listing_allows_products', lambda _: True)
    items = []
    model = ItemsModel(items, country=country, converter=float, currency_code='BOB')
    window = SimpleNamespace(
        country_name=country, model=model, presentaciones=[],
        _try_add_presentacion_by_combo_code=lambda *a, **k: False,
        productos=[dict(id='TEST', nombre='Producto de prueba', categoria='ESENCIAS',
                        cantidad_disponible=100, p_max=2)],
    )
    assert add_items.AddItemsMixin._agregar_por_codigo(window, 'TEST')
    assert items[0]['cantidad'] == expected


@pytest.mark.parametrize('country,expected', [('BO', 50), ('BOL', 50), ('PE', 0.05)])
def test_assistant_keeps_bolivia_quantities_direct(monkeypatch, country, expected):
    from types import SimpleNamespace
    from src.ai.assistant import actions

    monkeypatch.setattr(actions, 'is_cats_code', lambda *_: True)
    window = SimpleNamespace(country_name=country)
    assert actions.normalize_qty_for_code(window, 'TEST', 'producto', '50') == expected


def test_peru_cats_still_convert_integer_input_to_kilograms(qapp):
    from PySide6.QtCore import Qt
    from src.app_window_parts.models import ItemsModel

    item = dict(codigo='TEST', producto='Producto de prueba', categoria='ESENCIAS',
                cantidad=1, precio=200, _prod=dict(p_max=200))
    model = ItemsModel([item], country='PE', converter=float, currency_code='PEN')
    assert model.setData(model.index(0, 3), '50', Qt.EditRole)
    assert item['cantidad'] == 0.05
    assert item['total'] == 10


@pytest.mark.parametrize("country", ["BO", "BOL", "BOLIVIA"])
def test_base01_labels_use_one_liter_per_unit(qapp, monkeypatch, country, tmp_path):
    items = [{"codigo": "BASE01", "producto": "Base", "categoria": "PRODUCTO", "cantidad": 2}]
    before = deepcopy(items)
    dialog = module.LabelsDialog(None, quote_code="BO-TEST", country=country, items=items)
    try:
        assert dialog.table.rowCount() == 1
        assert dialog.table.item(0, 1).text() == "2 L"
        assert dialog.table.item(0, 2).text() == "2"
        assert dialog.table.item(0, 3).text() == "1 1"
        monkeypatch.setattr(module, "connect", lambda *_: Mock())
        monkeypatch.setattr(module, "resolve_db_path", lambda: ":memory:")
        monkeypatch.setattr(module, "get_setting", lambda con, key, default: default)
        logo = tmp_path / "logo.png"
        Image.new("RGB", (10, 10), "white").save(logo)
        monkeypatch.setattr(module, "resolve_logo_path_for_company", lambda *_: logo)
        monkeypatch.setattr(module, "resolve_label_printer", lambda *a, **k: ("usb", Mock(name="printer")))
        printed = Mock()
        monkeypatch.setattr(module, "imprimir_zpl_usb", printed)
        monkeypatch.setattr(module.threading, "Thread", Mock())
        monkeypatch.setattr(module.QMessageBox, "information", Mock())
        monkeypatch.setattr(module.QMessageBox, "critical", Mock(side_effect=AssertionError))
        dialog._on_print_clicked()
        zpl = printed.call_args.args[0]
        assert zpl.count("^FD1 L^FS") == 2
        assert "1000g" not in zpl
        assert items == before
    finally:
        dialog.deleteLater()


def test_bolivia_mixed_labels_keep_grams_and_allow_liter_splits(qapp):
    dialog = module.LabelsDialog(None, quote_code="BO-TEST", country="BO", items=[
        {"codigo": "BASE01", "cantidad": 2},
        {"codigo": "BASE02", "categoria": "DILUYENTES", "cantidad": 25},
    ])
    try:
        assert dialog.table.rowCount() == 2
        assert dialog.table.item(1, 1).text() == "25"
        dialog.table.item(0, 2).setText("4")
        assert dialog.table.item(0, 3).text() == "0.5 0.5 0.5 0.5"
        assert dialog.btn_print.isEnabled()
    finally:
        dialog.deleteLater()
