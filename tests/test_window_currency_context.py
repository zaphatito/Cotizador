from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

# Mantener el orden de imports usado por las pruebas de superficies Qt para
# evitar el ciclo legacy entre widgets y app_window_parts.
import src.app  # noqa: F401
from src import utils
from src.app_window_parts import currency as currency_module
from src.app_window_parts import main as main_module
from src.app_window_parts import models as models_module
from src.app_window_parts import pdf_actions, table_actions
from src.app_window_parts.currency import CurrencyMixin
from src.app_window_parts.main import SistemaCotizaciones
from src.app_window_parts.models import ItemsModel
from src.app_window_parts.pdf_actions import PdfActionsMixin
from src.app_window_parts.table_actions import TableActionsMixin
from src.app_window_parts.ui import UiMixin
from src.currency import symbol_pdf, symbol_ui
from src.catalog_context import QuoteContext
from src.widgets_parts.helpers import _fmt_trim_decimal
from src.widgets_parts.quote_history_dialog import _quote_context_from_header


class _ScopedWindow(CurrencyMixin):
    def __init__(
        self,
        *,
        country_name: str,
        base_currency: str,
        current_currency: str,
        rate: float,
        secondary_currencies: list[str],
    ):
        self.country_name = country_name
        self.base_currency = base_currency
        self.current_currency = current_currency
        self.currency_rate = rate
        self.secondary_currencies = list(secondary_currencies)
        self.secondary_currency = self.secondary_currencies[0]
        self._rates = {
            **{code: 1.0 for code in self.secondary_currencies},
            current_currency: rate,
        }
        self._app_icon = object()


def _item(price: float = 10.0) -> dict:
    return {
        "codigo": "SKU001",
        "producto": "Producto",
        "categoria": "OTROS",
        "cantidad": 1.0,
        "precio": price,
        "total": price,
        "descuento_pct": 0.0,
        "descuento_monto": 0.0,
    }


class _ToggleButton:
    def __init__(self, checked: bool = False):
        self.checked = checked

    def isChecked(self):
        return self.checked

    def setChecked(self, checked):
        self.checked = bool(checked)


def test_quote_origin_toggle_maps_web_to_chatbot_boolean():
    window = SimpleNamespace(
        btn_origin_web=_ToggleButton(),
        btn_origin_organic=_ToggleButton(True),
    )

    UiMixin._set_chatbot_quote(window, True)
    assert UiMixin._is_chatbot_quote(window) is True
    assert window.btn_origin_organic.isChecked() is False

    UiMixin._set_chatbot_quote(window, False)
    assert UiMixin._is_chatbot_quote(window) is False
    assert window.btn_origin_organic.isChecked() is True


def test_paraguay_cash_mode_resolves_to_efectivo():
    window = SimpleNamespace(country_name="PARAGUAY", _py_cash_mode=True)

    assert PdfActionsMixin._get_metodo_pago_actual(window) == "Efectivo"


def test_paraguay_window_starts_with_cash_and_organic_origin(monkeypatch, tmp_path):
    QApplication.instance() or QApplication([])
    db_path = str(tmp_path / "ui-defaults.sqlite3")
    monkeypatch.setattr(main_module, "resolve_db_path", lambda: db_path)
    monkeypatch.setattr(currency_module, "resolve_db_path", lambda: db_path)
    monkeypatch.setattr(main_module, "is_ai_enabled", lambda **_kwargs: False)
    monkeypatch.setattr(main_module, "is_recommendations_enabled", lambda **_kwargs: False)

    context = QuoteContext.from_values(
        country_code="PY",
        company_type="EF PERFUMES",
        username="prueba",
        id_cotizador="C01",
        base_currency="PYG",
    )
    window = SistemaCotizaciones(
        pd.DataFrame(),
        pd.DataFrame(),
        QIcon(),
        quote_context=context,
    )
    try:
        assert window.btn_pay_cash.isChecked() is True
        assert window.btn_pay_card.isChecked() is False
        assert window._is_py_cash_mode() is True
        assert window._get_metodo_pago_actual() == "Efectivo"
        assert window.btn_origin_web.isChecked() is False
        assert window.btn_origin_organic.isChecked() is True
        assert window._is_chatbot_quote() is False

        window._set_chatbot_quote(True)
        assert window.btn_origin_web.isChecked() is True
        assert window.btn_origin_organic.isChecked() is False
        assert window._is_chatbot_quote() is True

        window._set_chatbot_quote(False)
        assert window.btn_origin_web.isChecked() is False
        assert window.btn_origin_organic.isChecked() is True
        assert window._is_chatbot_quote() is False
    finally:
        window.close()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(-0.09999999999999998, "-0.1", id="negative-float-residue"),
        pytest.param(0.09000000000000002, "0.09", id="positive-float-residue"),
        pytest.param(-0.00000000000000001, "0", id="negative-zero"),
        pytest.param(12.0, "12", id="integer"),
        pytest.param(0.125, "0.125", id="three-decimal-quantity"),
    ],
)
def test_fmt_trim_decimal_hides_float_residue(value, expected):
    assert _fmt_trim_decimal(value) == expected


def test_money_formatters_accept_explicit_currency_and_keep_legacy_fallback(monkeypatch):
    monkeypatch.setattr(utils, "get_currency_context", lambda: ("USD", "", 1.0))

    assert utils.fmt_money_ui(12.5) == f"{symbol_ui('USD')} 12.50"
    assert utils.fmt_money_pdf(12.5) == f"{symbol_pdf('USD')} 12.50"
    assert utils.fmt_money_ui(12.5, currency="BOB") == f"{symbol_ui('BOB')} 12.50"
    assert utils.fmt_money_pdf(12.5, currency="PYG") == f"{symbol_pdf('PYG')} 12.50"
    assert utils.fmt_money_ui(12.5, currency="SOL") == f"{symbol_ui('PEN')} 12.50"
    assert utils.fmt_money_pdf(12.5, currency="GS") == f"{symbol_pdf('PYG')} 12.50"


def test_two_scoped_window_models_keep_currency_and_rate_isolated(monkeypatch):
    monkeypatch.setattr(utils, "get_currency_context", lambda: ("VES", "", 99.0))
    peru = _ScopedWindow(
        country_name="PERU",
        base_currency="PEN",
        current_currency="PEN",
        rate=1.0,
        secondary_currencies=["BOB", "USD"],
    )
    paraguay = _ScopedWindow(
        country_name="PARAGUAY",
        base_currency="PYG",
        current_currency="PYG",
        rate=1.0,
        secondary_currencies=["ARS", "BRL", "USD"],
    )

    peru_item = _item()
    peru_item["descuento_monto"] = 2.0
    peru_model = ItemsModel(
        [peru_item],
        country=peru.country_name,
        converter=peru._convert_from_base,
        currency_provider=lambda: peru._currency_context()[0],
    )
    paraguay_model = ItemsModel(
        [_item()],
        country=paraguay.country_name,
        converter=paraguay._convert_from_base,
        currency_provider=lambda: paraguay._currency_context()[0],
    )

    assert peru_model.data(peru_model.index(0, 4), Qt.DisplayRole) == (
        f"{symbol_ui('PEN')} 10.00"
    )
    assert peru_model.data(peru_model.index(0, 2), Qt.DisplayRole) == (
        f"-{symbol_ui('PEN')} 2.00"
    )
    assert paraguay_model.data(paraguay_model.index(0, 4), Qt.DisplayRole) == (
        f"{symbol_ui('PYG')} 10.00"
    )
    assert paraguay_model.data(paraguay_model.index(0, 5), Qt.DisplayRole) == (
        f"{symbol_ui('PYG')} 10.00"
    )

    peru._set_currency_context("USD", 0.25)
    assert peru_model.data(peru_model.index(0, 4), Qt.DisplayRole) == (
        f"{symbol_ui('USD')} 2.50"
    )
    assert paraguay_model.data(paraguay_model.index(0, 4), Qt.DisplayRole) == (
        f"{symbol_ui('PYG')} 10.00"
    )


def test_items_model_legacy_constructor_keeps_global_converter_and_currency(monkeypatch):
    monkeypatch.setattr(models_module, "convert_from_base", lambda value: float(value) * 3.0)
    monkeypatch.setattr(utils, "get_currency_context", lambda: ("USD", "", 3.0))

    model = ItemsModel([_item()])

    assert model.data(model.index(0, 4), Qt.DisplayRole) == f"{symbol_ui('USD')} 30.00"


def test_history_payload_restores_display_currency_rate_and_copies_shown_snapshots():
    class HistoryWindow(CurrencyMixin):
        base_currency = "PEN"
        current_currency = "PEN"
        currency_rate = 1.0
        _rates = {"USD": 0.30}

        def _update_currency_label(self):
            self.label_updates = getattr(self, "label_updates", 0) + 1

    window = HistoryWindow()
    payload = {
        "currency_shown": "USD",
        "tasa_shown": 0.25,
        "items_shown": [
            {
                "codigo": "SKU001",
                "precio": 2.51,
                "subtotal": 5.02,
                "descuento": 0.01,
                "total": 5.01,
            }
        ],
        "shown_totals": {
            "subtotal_bruto": 5.02,
            "descuento_total": 0.01,
            "total_general": 5.01,
        },
    }

    SistemaCotizaciones._restore_history_display_snapshot(window, payload)
    payload["items_shown"][0]["precio"] = 999
    payload["shown_totals"]["total_general"] = 999

    assert window._currency_context() == ("USD", "", 0.25)
    assert window._history_display_snapshot == {"currency": "USD", "rate": 0.25}
    assert window._history_shown_items_snapshot[0]["precio"] == 2.51
    assert window._history_shown_totals_snapshot["total_general"] == 5.01
    assert window.label_updates == 1


def test_currency_label_uses_the_window_rate_instead_of_the_saved_rate_table():
    class Label:
        def setText(self, value):
            self.value = value

    window = SimpleNamespace(
        base_currency="PEN",
        current_currency="USD",
        currency_rate=0.25,
        _rates={"USD": 0.30},
        country_name="PERU",
        lbl_moneda=Label(),
        _currency_context=lambda: ("USD", "", 0.25),
    )

    CurrencyMixin._update_currency_label(window)

    assert "0.2500" in window.lbl_moneda.value
    assert "0.3000" not in window.lbl_moneda.value


def test_pdf_items_reuse_exact_history_shown_snapshot_while_base_item_is_unchanged():
    item = {
        "codigo": "SKU001",
        "cantidad": 2.0,
        "precio": 10.0,
        "subtotal_base": 20.0,
        "descuento_monto": 0.0,
        "total": 20.0,
    }
    item["_history_base_snapshot"] = pdf_actions.history_base_snapshot(item)
    item["_history_shown_snapshot"] = {
        "codigo": "SKU001",
        "precio": 2.51,
        "subtotal": 5.02,
        "descuento": 0.01,
        "total": 5.01,
    }
    window = SimpleNamespace(
        items=[item],
        _history_display_snapshot={"currency": "USD", "rate": 0.25},
        _currency_context=lambda: ("USD", "", 0.25),
        _convert_from_base=lambda amount: float(amount) * 0.25,
    )

    shown = PdfActionsMixin._build_items_for_pdf(window)

    assert shown[0]["precio"] == 2.51
    assert shown[0]["subtotal"] == 5.02
    assert shown[0]["descuento"] == 0.01
    assert shown[0]["total"] == 5.01
    assert "_history_shown_snapshot" not in shown[0]


def test_items_model_displays_exact_history_snapshot_in_the_restored_context():
    item = {
        "codigo": "SKU001",
        "producto": "Producto",
        "categoria": "OTROS",
        "cantidad": 2.0,
        "precio": 10.0,
        "subtotal_base": 20.0,
        "descuento_monto": 0.04,
        "total": 19.96,
    }
    item["_history_base_snapshot"] = pdf_actions.history_base_snapshot(item)
    item["_history_shown_snapshot"] = {
        "precio": 2.51,
        "subtotal": 5.02,
        "descuento": 0.01,
        "total": 5.01,
    }
    item["_history_display_snapshot"] = {"currency": "USD", "rate": 0.25}
    model = ItemsModel(
        [item],
        country="PERU",
        converter=lambda amount: float(amount) * 0.25,
        currency_code="USD",
    )

    assert model.data(model.index(0, 2), Qt.DisplayRole) == f"-{symbol_ui('USD')} 0.01"
    assert model.data(model.index(0, 4), Qt.DisplayRole) == f"{symbol_ui('USD')} 2.51"
    assert model.data(model.index(0, 5), Qt.DisplayRole) == f"{symbol_ui('USD')} 5.01"


def test_pdf_items_recalculate_when_history_item_was_edited():
    item = {
        "codigo": "SKU001",
        "cantidad": 2.0,
        "precio": 10.0,
        "subtotal_base": 20.0,
        "descuento_monto": 0.0,
        "total": 20.0,
    }
    item["_history_base_snapshot"] = pdf_actions.history_base_snapshot(item)
    item["_history_shown_snapshot"] = {
        "precio": 2.51,
        "subtotal": 5.02,
        "descuento": 0.01,
        "total": 5.01,
    }
    item["precio"] = 12.0
    item["subtotal_base"] = 24.0
    item["total"] = 24.0
    window = SimpleNamespace(
        items=[item],
        _history_display_snapshot={"currency": "USD", "rate": 0.25},
        _currency_context=lambda: ("USD", "", 0.25),
        _convert_from_base=lambda amount: float(amount) * 0.25,
    )

    shown = PdfActionsMixin._build_items_for_pdf(window)

    assert shown[0]["precio"] == 3.0
    assert shown[0]["subtotal"] == 6.0
    assert shown[0]["total"] == 6.0


def test_history_header_totals_are_reused_only_while_all_items_are_unchanged():
    item = {
        "codigo": "SKU001",
        "cantidad": 2.0,
        "precio": 10.0,
        "subtotal_base": 20.0,
        "descuento_monto": 0.0,
        "total": 20.0,
    }
    shown_item = {
        "precio": 2.51,
        "subtotal": 5.02,
        "descuento": 0.01,
        "total": 5.01,
    }
    item["_history_base_snapshot"] = pdf_actions.history_base_snapshot(item)
    item["_history_shown_snapshot"] = shown_item
    totals = {
        "subtotal_bruto": 5.02,
        "descuento_total": 0.01,
        "total_general": 5.01,
    }
    window = SimpleNamespace(
        items=[item],
        _history_display_snapshot={"currency": "USD", "rate": 0.25},
        _history_shown_items_snapshot=[shown_item],
        _history_shown_totals_snapshot=totals,
        _currency_context=lambda: ("USD", "", 0.25),
    )

    assert PdfActionsMixin._history_shown_totals_if_current(window) == totals

    item["total"] = 24.0
    assert PdfActionsMixin._history_shown_totals_if_current(window) is None


def test_mixed_history_and_edited_rows_sum_the_exact_shown_items_for_header():
    historical = {
        "codigo": "SKU001",
        "cantidad": 2.0,
        "precio": 10.0,
        "subtotal_base": 20.0,
        "descuento_monto": 0.0,
        "total": 20.0,
    }
    historical["_history_base_snapshot"] = pdf_actions.history_base_snapshot(historical)
    historical["_history_shown_snapshot"] = {
        "precio": 2.51,
        "subtotal": 5.02,
        "descuento": 0.01,
        "total": 5.01,
    }
    edited = {
        "codigo": "SKU002",
        "cantidad": 2.0,
        "precio": 12.0,
        "subtotal_base": 24.0,
        "descuento_monto": 0.0,
        "total": 24.0,
    }
    window = SimpleNamespace(
        items=[historical, edited],
        _history_display_snapshot={"currency": "USD", "rate": 0.25},
        _currency_context=lambda: ("USD", "", 0.25),
        _convert_from_base=lambda amount: float(amount) * 0.25,
        _history_shown_totals_if_current=lambda: None,
    )

    items_shown = PdfActionsMixin._build_items_for_pdf(window)
    totals = PdfActionsMixin._shown_totals_for_output(window, items_shown)

    assert [item["total"] for item in items_shown] == [5.01, 6.0]
    assert totals["subtotal_bruto"] == pytest.approx(11.02)
    assert totals["descuento_total"] == pytest.approx(0.01)
    assert totals["total_general"] == pytest.approx(11.01)


def test_preview_receives_shown_items_totals_and_quote_context(monkeypatch):
    class Field:
        def __init__(self, value):
            self.value = value

        def text(self):
            return self.value

    context = QuoteContext.from_values(
        country_code="PE",
        company_type="EF PERFUMES",
        username="operador",
        id_cotizador="007",
        base_currency="PEN",
    )
    shown_items = [{"codigo": "SKU001", "precio": 2.51, "total": 5.01}]
    totals = {"subtotal_bruto": 5.02, "descuento_total": 0.01, "total_general": 5.01}
    captured = {}
    monkeypatch.setattr(
        pdf_actions,
        "show_preview_dialog",
        lambda *args, **kwargs: captured.update(args=args, kwargs=kwargs),
    )
    window = SimpleNamespace(
        entry_cliente=Field("Cliente"),
        entry_cedula=Field("123"),
        entry_telefono=Field("900000000"),
        entry_direccion=Field("Dirección"),
        entry_email=Field("cliente@example.test"),
        items=[{"total": 20.0}],
        _validate_doc_phone_values=lambda *_args, **_kwargs: (True, "", "DNI"),
        _currency_context=lambda: ("USD", "", 0.25),
        _convert_from_base=lambda amount: float(amount) * 0.25,
        _build_items_for_pdf=lambda: shown_items,
        _history_shown_totals_if_current=lambda: totals,
        country_name="PERU",
        quote_context=context,
        _app_icon=object(),
    )

    PdfActionsMixin.previsualizar_datos(window)

    assert captured["args"][5] is shown_items
    assert captured["kwargs"]["amounts_are_shown"] is True
    assert captured["kwargs"]["shown_totals"] is totals
    assert captured["kwargs"]["quote_context"] is context


def test_history_header_builds_explicit_ticket_context():
    context = _quote_context_from_header(
        {
            "country_code": "PE",
            "company_type": "EF PERFUMES",
            "cotizador_username": "operador",
            "id_cotizador": "007",
            "base_currency": "PEN",
        }
    )

    assert context.scope.country_code == "PE"
    assert context.scope.company_type == "EF PERFUMES"
    assert context.username == "operador"
    assert context.id_cotizador == "007"
    assert context.base_currency == "PEN"


def test_item_dialog_calls_receive_the_window_currency_context(monkeypatch):
    window = _ScopedWindow(
        country_name="PERU",
        base_currency="PEN",
        current_currency="USD",
        rate=0.25,
        secondary_currencies=["BOB", "USD"],
    )
    window.items = [_item()]
    captured: dict[str, dict] = {}

    def fake_discount(*_args, **kwargs):
        captured["discount"] = kwargs
        return None

    def fake_price(*_args, **kwargs):
        captured["price"] = kwargs
        return None

    monkeypatch.setattr(table_actions, "show_discount_dialog_for_item", fake_discount)
    monkeypatch.setattr(table_actions, "show_price_picker", fake_price)

    TableActionsMixin._abrir_dialogo_descuento(window, 0)
    TableActionsMixin._abrir_selector_precio(window, 0)

    for kwargs in captured.values():
        assert kwargs["current_currency"] == "USD"
        assert kwargs["converter"](8.0) == 2.0


def test_preview_and_catalog_list_receive_the_window_currency_context(monkeypatch):
    window = _ScopedWindow(
        country_name="PARAGUAY",
        base_currency="PYG",
        current_currency="USD",
        rate=0.00013,
        secondary_currencies=["ARS", "BRL", "USD"],
    )
    window.productos = [_item()]
    window.presentaciones = []
    window.items = [_item()]
    window._agregar_por_codigo = lambda _code: None
    window.entry_cliente = SimpleNamespace(text=lambda: "Cliente")
    window.entry_cedula = SimpleNamespace(text=lambda: "123456")
    window.entry_telefono = SimpleNamespace(text=lambda: "0981000000")
    window.entry_direccion = SimpleNamespace(text=lambda: "-")
    window.entry_email = SimpleNamespace(text=lambda: "-")
    window._validate_doc_phone_values = lambda *_args, **_kwargs: (True, "", "CI")
    context = QuoteContext.from_values(
        country_code="PY",
        company_type="EF PERFUMES",
        username="operador",
        id_cotizador="007",
        base_currency="PYG",
    )
    window.quote_context = context
    stock_matrix = {
        "stores": [{"id_tienda": 1, "name": "Tienda Centro"}],
        "rows": [],
    }
    window._catalog_manager = SimpleNamespace(
        server_mode=True,
        stock_matrix=lambda scope: stock_matrix if scope == context.scope else {},
    )

    captured: dict[str, dict] = {}

    class _Dialog:
        def __init__(self, *_args, **kwargs):
            captured["list"] = kwargs

        def sizeHint(self):
            return SimpleNamespace(height=lambda: 100)

        def move(self, _x, _y):
            pass

        def exec(self):
            return 0

    window.frameGeometry = lambda: SimpleNamespace(
        center=lambda: SimpleNamespace(x=lambda: 100, y=lambda: 100)
    )

    def fake_preview(*_args, **kwargs):
        captured["preview"] = kwargs

    monkeypatch.setattr(pdf_actions, "ListadoProductosDialog", _Dialog)
    monkeypatch.setattr(pdf_actions, "show_preview_dialog", fake_preview)

    PdfActionsMixin.abrir_listado_productos(window)
    PdfActionsMixin.previsualizar_datos(window)

    for kwargs in captured.values():
        assert kwargs["current_currency"] == "USD"
        assert kwargs["converter"](10_000.0) == pytest.approx(1.3)
        assert kwargs["quote_context"] is context
    assert captured["list"]["stock_matrix"] is stock_matrix


def test_currency_dialog_receives_only_the_window_currency_options(monkeypatch):
    window = _ScopedWindow(
        country_name="BOLIVIA",
        base_currency="BOB",
        current_currency="USD",
        rate=0.14,
        secondary_currencies=["PEN", "USD"],
    )
    captured: dict = {}

    def fake_dialog(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return None

    monkeypatch.setattr(currency_module, "show_currency_dialog", fake_dialog)

    window.abrir_dialogo_moneda_y_tasa()

    assert captured["kwargs"]["current_currency"] == "USD"
    assert captured["kwargs"]["secondary_currencies"] == ["PEN", "USD"]
    assert captured["args"][2] == "BOB"
    assert captured["args"][4] == 0.14
