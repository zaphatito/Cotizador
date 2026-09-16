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


def test_prices_edition_never_checks_updates_even_if_settings_enable_them(monkeypatch):
    from src import updater
    import urllib.request
    def forbidden(*args, **kwargs):
        raise AssertionError("No debe consultar actualizaciones")
    monkeypatch.setattr(urllib.request, 'urlopen', forbidden)
    result = updater.check_for_updates_and_maybe_install({
        'update_check_on_startup': True, 'update_mode': 'SILENT',
        'update_manifest_url': 'https://example.invalid/update.json',
    })
    assert result['status'] == 'PILOT_MANUAL_ONLY'


@pytest.mark.parametrize('amount,pct,available', [(12.49, 12, 12), (12.50, 13, 13), (12.51, 13, 13)])
def test_entered_amount_snaps_to_nearest_integer(amount, pct, available):
    percentage = percentage_from_entered_amount(100, amount)
    assert percentage == pct
    assert line_discount(100, 'percent', percentage)[1] == available


@pytest.mark.parametrize('country', ['PE', 'PY', 'VE', 'BO'])
def test_inline_amount_uses_integer_percentage_in_all_countries(qapp, country):
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
