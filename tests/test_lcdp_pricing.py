import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QDialog, QDoubleSpinBox

from src.lcdp_pricing import money, line_subtotal, line_discount, percentage_from_entered_amount
from src.pricing import price_for_price_id
from src.app_window_parts.models import ItemsModel
from src.api.presupuesto_client import _build_presupuesto_items
from src.widgets_parts.discount_item_dialog import show_discount_dialog_for_item


@pytest.mark.parametrize('price,qty,pct,total', [
    (5.99, 2, 25, 8.99), (10.13, 1.5, 10, 13.68),
    (9.995, 2, 10, 18), (0.335, 10, 0, 3.4),
])
def test_lcdp_commercial_rounding(price, qty, pct, total):
    gross = line_subtotal(price, qty)
    _, discount, result = line_discount(gross, 'percent', pct, country='PE')
    assert result == total
    assert money(discount + result) == gross


@pytest.mark.parametrize('country', ['PE', 'PY', 'VE', 'BO'])
def test_amount_is_authoritative(country):
    pct, amount, total = line_discount(100, 'amount', amount=12.5, country=country)
    assert (pct, amount, total) == (12.5, 12.5, 87.5)


@pytest.mark.parametrize('subtotal,pct', [(0.99, 1), (2, 51), (10, 99)])
@pytest.mark.parametrize('country', ['PE', 'PY', 'VE', 'BO'])
def test_country_minimum(subtotal, pct, country):
    with pytest.raises(ValueError, match='1.00'):
        line_discount(subtotal, 'percent', pct, country=country)


def test_price_tiers_and_weight_factor():
    product = dict(p_max=10.129, p_min=9.995, p_oferta=8.104)
    assert [price_for_price_id(product, i) for i in (1, 2, 3)] == [10.13, 10, 8.1]
    assert line_subtotal(0.123, 2, 50) == 12


def test_presentation_combines_components_before_rounding():
    component = price_for_price_id(dict(p_max=1.114), 1, rounded=False)
    assert money(10.124 + component) == 11.24


@pytest.mark.parametrize('country,expected', [('PE', 87), ('PY', 87.5), ('VE', 87.5)])
def test_percentage_precision(country, expected):
    assert line_discount(100, 'percent', 12.5, country=country)[2] == expected


def make_model(country='PE'):
    model = ItemsModel([], country=country, converter=lambda x: x, currency_code='PEN')
    item = dict(codigo='TEST', producto='Prueba', categoria='BOTELLAS',
                precio=5.99, cantidad=2, _prod=dict(p_max=5.99, p_min=4, p_oferta=3))
    model.add_item(item)
    return model, item


def test_model_amount_quantity_and_api_snapshot(qapp):
    model, item = make_model()
    assert model.setData(model.index(0, 2), dict(mode='amount', amount=1.25), Qt.EditRole)
    assert item['total'] == 10.78
    assert item['descuento_mode'] == 'percent'
    assert model.setData(model.index(0, 3), '3', Qt.EditRole)
    assert item['total'] == 16.17
    assert item['descuento_monto'] == 1.80
    sent = _build_presupuesto_items([item], cod_pais='PE')[0]
    assert sent['monto_descuento'] == 1.80
    assert sent['prc_descuento'] == item['descuento_pct']


@pytest.mark.parametrize('country', ['PE', 'PY', 'VE', 'BO'])
def test_model_rejects_discount_without_mutating_line(qapp, country):
    model, item = make_model(country)
    before = dict(item)
    assert not model.setData(model.index(0, 2), dict(mode='percent', percent=99), Qt.EditRole)
    assert item == before


@pytest.mark.parametrize('country', ['PE', 'PY', 'VE', 'BO'])
def test_reducing_quantity_removes_invalid_discount(qapp, country):
    model, item = make_model(country)
    assert model.setData(model.index(0, 2), dict(mode='amount', amount=10.5), Qt.EditRole)
    assert model.setData(model.index(0, 3), '1', Qt.EditRole)
    assert item['total'] == 5.99
    assert item['descuento_mode'] is None


def test_paraguay_cash_roundtrip(qapp):
    model, item = make_model('PY')
    model.set_py_cash_mode(True)
    assert item['total'] == money(11.98 * (1 - 4.7619 / 100))
    model.set_py_cash_mode(False)
    assert item['total'] == 11.98


def test_history_remains_unchanged(qapp):
    model, item = make_model()
    old = dict(item, precio=5.9999, descuento_pct=12.345, descuento_monto=1.234,
               total=10.765, subtotal_base=11.999)
    model.add_item(old, preserve_snapshot=True)
    sent = _build_presupuesto_items([old], cod_pais='PE')[0]
    assert old['total'] == 10.765
    assert sent['monto_unitario'] == 5.9999
    assert sent['monto_descuento'] == 1.234


@pytest.mark.parametrize('field,value,amount', [
    ('discount_amount', 1.25, 1.20), ('discount_final', 10, 2.04),
])
def test_dialog_normalizes_to_percentage(qapp, field, value, amount):
    _, item = make_model()
    def edit():
        dialog = QApplication.activeModalWidget()
        dialog.findChild(QDoubleSpinBox, field).setValue(value)
        dialog.accept()
    QTimer.singleShot(0, edit)
    result = show_discount_dialog_for_item(None, QIcon(), item, 'PEN',
        converter=lambda x: x, current_currency='PEN', country='PE')
    assert result['mode'] == 'percent'
    assert result['amount'] == amount


@pytest.mark.parametrize('country', ['PE', 'PY', 'VE', 'BO'])
def test_amount_minimum_and_exact_boundary(country):
    with pytest.raises(ValueError, match='1.00'):
        line_discount(2, 'amount', amount=1.01, country=country)
    assert line_discount(2, 'amount', amount=1, country=country)[2] == 1
    assert line_discount(0.5, country=country)[2] == 0.5


def test_cash_cannot_bypass_minimum(qapp):
    model, item = make_model('PY')
    model.set_py_cash_mode(True)
    before = dict(item)
    assert not model.setData(model.index(0, 2), dict(mode='amount', amount=11.5), Qt.EditRole)
    assert item == before
    small = dict(codigo='SMALL', categoria='SERVICIO', precio=1, cantidad=1)
    model.add_item(small)
    assert small['total'] == 1
    assert small['descuento_monto'] == 0


@pytest.mark.parametrize('pilot,allowed', [(False, True), (True, False)])
def test_public_release_allows_updates_and_pilot_stays_manual(monkeypatch, pilot, allowed):
    from src import build_profile
    monkeypatch.setattr(build_profile, 'IS_PILOT', pilot)
    assert build_profile.automatic_updates_allowed() is allowed


@pytest.mark.parametrize('amount,pct,available', [(12.49, 12, 12), (12.50, 13, 13), (12.51, 13, 13)])
def test_entered_amount_snaps_to_nearest_integer(amount, pct, available):
    percentage = percentage_from_entered_amount(100, amount)
    assert percentage == pct
    assert line_discount(100, 'percent', percentage)[1] == available


@pytest.mark.parametrize('country', ['PE', 'VE'])
def test_inline_amount_uses_integer_percentage_where_required(qapp, country):
    model, item = make_model(country)
    assert model.setData(model.index(0, 2), dict(mode='amount', amount=1.26), Qt.EditRole)
    assert item['descuento_pct'] == 11
    assert item['descuento_monto'] == 1.32


def test_discount_dialog_dark_theme(qapp, tmp_path):
    from src.ui_theme import apply_modern_theme
    apply_modern_theme(qapp, mode='dark')
    _, item = make_model()
    captured = {}
    def inspect():
        dialog = QApplication.activeModalWidget()
        fields = dialog.findChildren(QDoubleSpinBox)
        captured['buttons'] = [field.buttonSymbols() for field in fields]
        captured['tracking'] = [field.keyboardTracking() for field in fields]
        dialog.findChild(QDoubleSpinBox, 'discount_amount').setValue(1.26)
        dialog.grab().save(str(tmp_path / 'discount-dark.png'))
        dialog.accept()
    QTimer.singleShot(0, inspect)
    try:
        result = show_discount_dialog_for_item(None, QIcon(), item, 'PEN',
            converter=lambda x: x, current_currency='PEN', country='PE')
        assert captured['buttons'] == [QDoubleSpinBox.NoButtons] * 3
        assert captured['tracking'] == [False] * 3
        assert result['percent'] == 11
        assert result['amount'] == 1.32
    finally:
        apply_modern_theme(qapp, mode='system')


def test_typing_amount_waits_for_commit_before_rounding(qapp):
    from PySide6.QtTest import QTest
    _, item = make_model()
    observed = {}
    def edit():
        dialog = QApplication.activeModalWidget()
        amount = dialog.findChild(QDoubleSpinBox, 'discount_amount')
        dialog.activateWindow()
        amount.setFocus()
        qapp.processEvents()
        amount.selectAll()
        QTest.keyClicks(amount, '1.26')
        observed['typed'] = amount.lineEdit().text()
        QTest.keyClick(amount, Qt.Key_Tab)
        qapp.processEvents()
        observed['adjusted'] = amount.value()
        dialog.accept()
    QTimer.singleShot(0, edit)
    result = show_discount_dialog_for_item(None, QIcon(), item, 'PEN',
        converter=lambda x: x, current_currency='PEN', country='PE')
    assert observed == {'typed': '1.26', 'adjusted': 1.32}
    assert result['percent'] == 11


@pytest.mark.parametrize('percentage', [95, 99])
def test_paraguay_cash_caps_discount_and_restores_user_percentage(qapp, percentage):
    item = dict(codigo='TEST', categoria='BOTELLAS', cantidad=1,
                precio=1_000_000, _prod=dict(p_max=1_000_000))
    model = ItemsModel([], country='PY', converter=float, currency_code='PYG')
    model.add_item(item)
    assert model.setData(model.index(0, 2), dict(mode='percent', percent=percentage), Qt.EditRole)
    original_total = item['total']
    model.set_py_cash_mode(True)
    assert item['descuento_pct'] == 99
    assert item['total'] == 10_000
    assert item['total'] <= original_total
    assert model.setData(model.index(0, 3), '2', Qt.EditRole)
    assert item['total'] == 20_000
    model.set_py_cash_mode(False)
    assert item['descuento_pct'] == percentage
    assert item['total'] == original_total * 2


@pytest.mark.parametrize('percentage', [99.1, 100, float('nan'), float('inf')])
def test_paraguay_cash_rejects_invalid_percentage_without_changing_item(qapp, percentage):
    model, item = make_model('PY')
    model.set_py_cash_mode(True)
    before = dict(item)
    assert not model.setData(model.index(0, 2), dict(mode='percent', percent=percentage), Qt.EditRole)
    assert item == before


@pytest.mark.parametrize('country,quantity,factor,api_quantity', [
    ('PE', 0.025, 1, 0.025), ('PY', 20, 50, 1000),
    ('BO', 1000, 1, 1000), ('VE', 20, 50, 1000),
])
@pytest.mark.parametrize('tier,field', [('unitario', 'p_max'), ('minimo', 'p_min'), ('oferta', 'p_oferta')])
def test_weight_price_tiers_and_api_keep_the_same_line_amount(qapp, country, quantity, factor, api_quantity, tier, field):
    prices = dict(p_max=1.005, p_min=0.024, p_oferta=0.005)
    item = dict(codigo='AUDIT', categoria='ESENCIAS', cantidad=quantity,
                precio=prices[field], precio_tier=tier, _prod=prices)
    model = ItemsModel([], country=country, converter=float)
    model.add_item(item)
    assert model.setData(model.index(0, 3), str(quantity), Qt.EditRole)
    assert item['factor_total'] == factor
    assert item['precio'] == money(prices[field])
    assert item['total'] == line_subtotal(prices[field], quantity, factor)
    sent = _build_presupuesto_items([item], cod_pais=country)[0]
    assert sent['cantidad'] == api_quantity
    assert line_subtotal(sent['monto_unitario'], sent['cantidad']) == item['total']


@pytest.mark.parametrize('country,code,quantity,factor', [
    ('PE', 'BASE01', 2, 1), ('PY', 'FERO001', 2, 1),
    ('PY', 'FIJ002', 2, 1), ('BO', 'BASE01', 2, 1),
])
def test_country_unit_exceptions_keep_price_rounding_and_api_amount(qapp, country, code, quantity, factor):
    item = dict(codigo=code, categoria='DILUYENTES', cantidad=quantity,
                precio=1.005, _prod=dict(p_max=1.005))
    model = ItemsModel([], country=country, converter=float)
    model.add_item(item)
    sent = _build_presupuesto_items([item], cod_pais=country)[0]
    assert item['precio'] == 1.01
    assert item['factor_total'] == factor
    assert item['total'] == 2.02
    assert sent['cantidad'] == 2


@pytest.mark.parametrize('country', ['PY', 'PARAGUAY', 'BO', 'BOL', 'BOLIVIA'])
@pytest.mark.parametrize('amount', [4900, 5000, 125000.01])
def test_paraguay_bolivia_inline_amount_remains_exact(qapp, country, amount):
    item = dict(codigo='TEST', categoria='BOTELLAS', cantidad=1,
                precio=1_000_000, _prod=dict(p_max=1_000_000))
    model = ItemsModel([], country=country, converter=float)
    model.add_item(item)
    assert model.setData(model.index(0, 2), dict(mode='amount', amount=amount), Qt.EditRole)
    assert item['descuento_mode'] == 'amount'
    assert item['descuento_monto'] == amount
    assert item['total'] == money(1_000_000 - amount)
    assert _build_presupuesto_items([item], cod_pais=country)[0]['monto_descuento'] == amount
    assert model.setData(model.index(0, 3), '2', Qt.EditRole)
    assert item['descuento_monto'] == amount
    assert item['total'] == money(2_000_000 - amount)


@pytest.mark.parametrize('country,currency', [('PY', 'PYG'), ('BO', 'BOB')])
@pytest.mark.parametrize('field,value', [('discount_amount', 4900), ('discount_final', 995100)])
def test_paraguay_bolivia_dialog_preserves_entered_amount(qapp, country, currency, field, value):
    item = dict(codigo='TEST', producto='Prueba', categoria='BOTELLAS', cantidad=1,
                precio=1_000_000, subtotal_base=1_000_000)
    def edit():
        dialog = QApplication.activeModalWidget()
        dialog.findChild(QDoubleSpinBox, field).setValue(value)
        dialog.accept()
    QTimer.singleShot(0, edit)
    result = show_discount_dialog_for_item(None, QIcon(), item, currency,
        converter=float, current_currency=currency, country=country)
    assert result['mode'] == 'amount'
    assert result['amount'] == 4900


def test_paraguay_cash_keeps_exact_amount_above_cash_base(qapp):
    item = dict(codigo='TEST', categoria='BOTELLAS', cantidad=1,
                precio=1_000_000, _prod=dict(p_max=1_000_000))
    model = ItemsModel([], country='PY', converter=float)
    model.add_item(item)
    model.set_py_cash_mode(True)
    assert model.setData(model.index(0, 2), dict(mode='amount', amount=125000.01), Qt.EditRole)
    assert item['descuento_monto'] == 125000.01
    assert item['total'] == 874999.99
    before = dict(item)
    assert not model.setData(model.index(0, 2), dict(mode='amount', amount=990000.01), Qt.EditRole)
    assert item == before


@pytest.mark.parametrize('country', ['PE', 'PY', 'BO', 'VE'])
def test_small_positive_minimum_price_does_not_jump_to_maximum_on_quantity_edit(qapp, country):
    item = dict(codigo='TEST', categoria='BOTELLAS', cantidad=1,
                precio=0.0001, precio_tier='minimo',
                _prod=dict(p_max=100, p_min=0.0001, p_oferta=80))
    model = ItemsModel([], country=country, converter=float)
    model.add_item(item)
    assert item['precio'] == 0
    assert model.setData(model.index(0, 3), '1000', Qt.EditRole)
    assert item['id_precioventa'] == 2
    assert item['precio'] == 0
    assert item['total'] == 0
