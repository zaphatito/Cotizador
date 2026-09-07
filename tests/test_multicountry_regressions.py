from types import SimpleNamespace

import pytest

from src.catalog_context import CatalogScope, QuoteContext
from src.quote_context_service import build_quote_context, resolve_historical_quote_scope
from src.widgets_parts.quote_history_dialog import _quote_context_from_header
from src.app_window_parts.main import SistemaCotizaciones
from src.app_window_parts.currency import CurrencyMixin


@pytest.mark.parametrize("country,currency", [("BO", "BOB"), ("PE", "PEN"), ("PY", "PYG"), ("VE", "USD")])
@pytest.mark.parametrize("stored", ["", "EUR", "PYG"])
def test_country_always_defines_base_currency(country, currency, stored):
    manager = SimpleNamespace(username="prueba", id_cotizador="C01", scope_record=lambda _: {"base_currency": stored})
    assert build_quote_context(manager, CatalogScope(country, "EF PERFUMES")).base_currency == currency
    header = dict(country_code=country, base_currency=stored, company_type="EF PERFUMES", cotizador_username="prueba", id_cotizador="C01")
    assert _quote_context_from_header(header).base_currency == currency
    assert QuoteContext.from_values(country_code=country, company_type="EF PERFUMES", username="prueba", id_cotizador="C01", base_currency=stored).base_currency == currency


def test_historical_country_comes_from_code_without_global_fallback():
    scopes = (CatalogScope("PE", "EF PERFUMES"), CatalogScope("PY", "EF PERFUMES"))
    scope, _ = resolve_historical_quote_scope(
        {"quote_no": "PE-C01-0000001", "company_type": "EF PERFUMES"},
        scopes,
        default_country_code="PY",
    )
    assert scope == scopes[0]


def test_refresh_rates_does_not_change_applied_historical_rate():
    class Window(CurrencyMixin):
        base_currency = "PEN"
        current_currency = "USD"
        currency_rate = 0.25
        model = SimpleNamespace(rowCount=lambda: 0)

        def _load_exchange_rate_file(self):
            return {"USD": 0.31}

        def _update_currency_label(self):
            pass

    window = Window()
    SistemaCotizaciones._on_rates_updated(window)
    assert window.currency_rate == 0.25
    assert window._rates == {"USD": 0.31}


@pytest.mark.parametrize("country", ["BO", "PE", "PY", "VE"])
def test_duplicate_corrects_base_without_writing_original(tmp_path, country):
    from sqlModels.db import connect, ensure_schema, tx
    from sqlModels.quotes_repo import get_quote_header, insert_quote
    from src.country_rules import country_profile

    con = connect(str(tmp_path / 'duplicate.db'))
    ensure_schema(con)
    try:
        values = dict(country_code=country, company_type='EF PERFUMES', base_currency='EUR', cotizador_username='prueba', id_cotizador='C01', created_at='2026-09-07T10:00:00', cliente='Cliente', cedula='12345678', telefono='123456789', currency_shown='USD', tasa_shown=0.25, subtotal_bruto_base=13, descuento_total_base=0, total_neto_base=13, subtotal_bruto_shown=130, descuento_total_shown=0, total_neto_shown=130, pdf_path='original.pdf', items_base=[], items_shown=[], require_complete_context=True)
        with tx(con):
            original = insert_quote(con, quote_no=f'{country}-C01-0000001', **values)
            # Representar una cabecera desplegada históricamente con base errónea.
            con.execute("UPDATE quotes SET base_currency='EUR' WHERE id=?", (original,))
        before = tuple(con.execute('SELECT * FROM quotes WHERE id=?', (original,)).fetchone())
        header = get_quote_header(con, original)
        context = _quote_context_from_header(header)
        assert context.base_currency == country_profile(country).base_currency
        with tx(con):
            duplicate = insert_quote(con, quote_no=f'{country}-C01-0000002', **{**values, 'base_currency': context.base_currency, 'pdf_path': 'duplicate.pdf'})
        assert tuple(con.execute('SELECT * FROM quotes WHERE id=?', (original,)).fetchone()) == before
        copied = get_quote_header(con, duplicate)
        assert copied['base_currency'] == context.base_currency
        assert copied['total_neto_shown'] == 130
        assert copied['tasa_shown'] == 0.25
        with pytest.raises(ValueError), tx(con):
            insert_quote(con, quote_no=f'{country}-C01-0000003', **{**values, 'company_type': ''})
        assert con.execute('SELECT count(*) FROM quotes').fetchone()[0] == 2
    finally:
        con.close()


def test_stock_zero_is_distinct_from_unknown_and_missing_sku():
    from src.catalog_manager import _products_frame

    frame = _products_frame({'products': [dict(codigo=code, nombre=code) for code in ('ZERO', 'UNKNOWN', 'MISSING')]}, {'rows': [dict(codigo='ZERO', total_stock=0, stocks={'1': 0}), dict(codigo='UNKNOWN', total_stock=0, stocks={'1': None})]})
    stocks = dict(zip(frame['id'], frame['cantidad_disponible']))
    assert stocks == {'ZERO': 0., 'UNKNOWN': -1., 'MISSING': -1.}


@pytest.mark.parametrize('value', ['0', '-1', 'nan', 'inf', 'abc'])
def test_rates_dialog_rejects_invalid_rates_without_writes(qapp, tmp_path, monkeypatch, value):
    from src.widgets_parts import menu
    from sqlModels.db import connect
    from sqlModels.rates_repo import load_rates

    path = str(tmp_path / 'rates.db')
    monkeypatch.setattr(menu, 'resolve_db_path', lambda: path)
    warnings = []
    monkeypatch.setattr(menu.QMessageBox, 'warning', lambda *args: warnings.append(args))
    dialog = menu.RatesDialog(country_code='PE', base_currency='PYG')
    try:
        assert dialog.base == 'PEN'
        assert set(dialog._edits) == {'BOB', 'USD'}
        con = connect(path)
        before = load_rates(con, 'PEN')
        con.close()
        dialog._edits['USD'].setText(value)
        dialog._save()
        assert warnings
        con = connect(path)
        assert load_rates(con, 'PEN') == before
        con.close()
    finally:
        dialog.deleteLater()
