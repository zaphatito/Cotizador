from copy import deepcopy
from itertools import combinations

import pandas as pd
import pytest
from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QDialogButtonBox

from src.catalog_context import CatalogScope
from src.country_rules import country_profile
from src.quote_context_service import build_quote_context, quote_context_from_header
from src.app_window_parts import main, currency
from src.widgets_parts.catalog_scope_dialog import CatalogScopeDialog
from src.widgets_parts.stock_matrix_dialog import StockMatrixDialog


COUNTRIES = ('BO', 'PE', 'PY', 'VE')
COMBINATIONS = [c for n in range(1, 5) for c in combinations(COUNTRIES, n)]


class Manager(QObject):
    scopes_updated = Signal(object)
    stock_updated = Signal(object)
    scope_catalog_updated = Signal(object, object, object)
    server_mode = True
    username = 'operador'
    id_cotizador = 'C01'

    def __init__(self, countries=COUNTRIES):
        super().__init__()
        self.available_scopes = tuple(CatalogScope(c, 'EF PERFUMES') for c in countries)
        self.active_scope = self.available_scopes[-1]
        self.healthy = True
        self.frames = {}
        for index, scope in enumerate(self.available_scopes, 1):
            self.frames[scope] = (pd.DataFrame([dict(id='SKU1', nombre='Producto', categoria='OTROS', p_max=index * 10., p_min=index * 8., p_oferta=index * 9., cantidad_disponible=index * 3., precio_venta=1)]), pd.DataFrame())

    def scope_record(self, scope):
        return {'base_currency': 'EUR', 'group_key': f'remote-{scope.country_code}'}

    def catalog_health(self, scope):
        return self.healthy, 'Catálogo pendiente'

    def catalog_for_scope(self, scope):
        return self.frames[scope]

    def stock_matrix(self, scope):
        return {'stores': [{'id_tienda': scope.country_code, 'name': f'Tienda {scope.country_code}'}], 'rows': []}


@pytest.mark.parametrize('countries', COMBINATIONS)
def test_all_fifteen_country_combinations(qapp, countries):
    manager = Manager(countries)
    before = manager.active_scope
    dialog = CatalogScopeDialog(None, manager)
    try:
        assert dialog.country.count() == len(countries)
        for scope in manager.available_scopes:
            dialog.country.setCurrentIndex(dialog.country.findData(scope.country_code))
            assert dialog.company.currentData() == scope
            assert dialog.company.isHidden()
            assert dialog.currency.text() == country_profile(scope.country_code).base_currency
            assert dialog.stores.text() == f'Tienda {scope.country_code}'
            context = build_quote_context(manager, scope)
            assert (context.username, context.id_cotizador) == ('operador', 'C01')
        dialog.accept()
        assert dialog.selected_scope == manager.available_scopes[-1]
        assert manager.active_scope == before
    finally:
        dialog.deleteLater()


def test_selector_company_visibility_catalog_gate_and_revocation(qapp):
    manager = Manager(('PE',))
    manager.available_scopes += (CatalogScope('PE', 'LCDP'),)
    dialog = CatalogScopeDialog(None, manager)
    try:
        assert dialog.country.count() == 1
        assert dialog.company.count() == 2
        assert not dialog.company.isHidden()
        manager.healthy = False
        manager.stock_updated.emit(manager.active_scope)
        assert not dialog.buttons.button(QDialogButtonBox.Ok).isEnabled()
        manager.healthy = True
        manager.available_scopes = ()
        manager.scopes_updated.emit(())
        dialog.accept()
        assert dialog.selected_scope is None
    finally:
        dialog.deleteLater()


@pytest.fixture
def windows(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(main, 'resolve_db_path', lambda: str(tmp_path / 'windows.db'))
    monkeypatch.setattr(currency, 'resolve_db_path', lambda: str(tmp_path / 'windows.db'))
    monkeypatch.setattr(main, 'is_ai_enabled', lambda **_: False)
    monkeypatch.setattr(main, 'is_recommendations_enabled', lambda **_: False)
    manager = Manager()
    result = [main.SistemaCotizaciones(*manager.catalog_for_scope(scope), QIcon(), catalog_manager=manager, quote_context=build_quote_context(manager, scope)) for scope in manager.available_scopes]
    yield manager, result
    for window in result:
        window.close()
        window.deleteLater()
    qapp.processEvents()


def payload(country, *, rate=0.25, shown='USD'):
    return dict(country_code=country, base_currency='EUR', currency_shown=shown, tasa_shown=rate, cliente='Cliente', items_base=[dict(codigo='SKU1', producto='Producto histórico', categoria='OTROS', cantidad=2., factor_total=1., precio=7., subtotal_base=14., descuento_mode='monto', descuento_monto=1., total=13.)], items_shown=[dict(precio=70., subtotal=140., descuento=10., total=130.)], shown_totals=dict(subtotal_bruto=140., descuento_total=10., total_general=130.))


def test_four_real_windows_keep_prices_stock_rates_and_history_independent(windows, qapp):
    manager, editors = windows
    for index, window in enumerate(editors, 1):
        window.show()
        window.activateWindow()
        window.load_from_history_payload(payload(window.country_code, shown='VES' if window.country_code == 'VE' else 'USD'))
        assert window.base_currency == country_profile(window.country_code).base_currency
        assert window.items[0]['precio'] == 7.
        assert window.items[0]['total'] == 13.
        assert window.items[0]['stock_disponible'] == index * 3.
        assert window._build_items_for_pdf()[0]['total'] == 130.
    qapp.processEvents()
    before = deepcopy([w.items for w in editors])
    target = editors[1]
    target.productos[0]['cantidad_disponible'] = 99.
    scope = target.quote_context.scope
    frame, pres = manager.frames[scope]
    updated = frame.copy()
    updated.loc[0, 'cantidad_disponible'] = 17.
    updated.loc[0, 'p_max'] = 999.
    manager.frames[scope] = (updated, pres)
    manager.scope_catalog_updated.emit(scope, updated, pres)
    for index, window in enumerate(editors):
        window._on_rates_updated()
        assert window.currency_rate == 0.25
        assert window.items[0]['total'] == before[index][0]['total']
        assert window._build_items_for_pdf()[0]['total'] == 130.
        assert window.items[0]['stock_disponible'] == (17. if window is target else (index + 1) * 3.)
    target._set_currency_context('USD', 0.5)
    assert all(w.currency_rate == 0.25 for w in editors if w is not target)
    assert target._build_items_for_pdf()[0]['total'] == 6.5


def test_missing_rate_preserves_snapshots_and_removed_sku(windows, monkeypatch):
    manager, editors = windows
    editor = editors[1]
    editor.productos = []
    editor.load_from_history_payload(payload('PE', rate=None))
    assert editor.currency_rate == 0
    assert editor.items[0]['stock_disponible'] == -1
    assert editor._build_items_for_pdf()[0]['total'] == 130
    monkeypatch.setattr(editor, 'abrir_dialogo_moneda_y_tasa', lambda: None)
    monkeypatch.setattr(currency.QMessageBox, 'information', lambda *_: None)
    assert not editor.model.setData(editor.model.index(0, 0), 3, Qt.EditRole)
    assert editor.items[0]['cantidad'] == 2


def test_stock_dialog_revocation_keeps_items_and_blocks_emission(windows):
    manager, editors = windows
    editor = editors[1]
    editor.load_from_history_payload(payload('PE'))
    dialog = StockMatrixDialog(catalog_manager=manager, scope=editor.quote_context.scope)
    try:
        assert dialog.tabs.count() == 1
        assert 'PE' in dialog.tabs.tabText(0)
        manager.available_scopes = tuple(s for s in manager.available_scopes if s != editor.quote_context.scope)
        manager.scopes_updated.emit(manager.available_scopes)
        assert not editor.btn_generar.isEnabled()
        assert editor.items[0]['total'] == 13
        assert dialog.tabs.tabText(0) == 'Sin tiendas'
    finally:
        dialog.deleteLater()


def test_historical_country_contradiction_and_ambiguous_company():
    manager = Manager(('PE',))
    manager.available_scopes += (CatalogScope('PE', 'LCDP'),)
    with pytest.raises(ValueError, match='ambiguos'):
        quote_context_from_header({'country_code': 'PE'}, catalog_manager=manager)
    with pytest.raises(ValueError, match='no coincide'):
        quote_context_from_header({'country_code': 'PE', 'quote_no': 'PY-C01-0000001'}, catalog_manager=manager)


def test_visual_qa_selector_and_four_windows(windows, qapp, tmp_path):
    manager, editors = windows
    dialog = CatalogScopeDialog(None, manager)
    dialog.show()
    qapp.processEvents()
    assert dialog.grab().save(str(tmp_path / 'selector.png'))
    dialog.close()
    dialog.deleteLater()
    for editor in editors:
        editor.load_from_history_payload(payload(editor.country_code, shown='VES' if editor.country_code == 'VE' else 'USD'))
        editor.show()
        qapp.processEvents()
        assert editor.grab().save(str(tmp_path / f'{editor.country_code}.png'))
