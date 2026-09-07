from __future__ import annotations

from src import pdfgen
from src.currency import symbol_pdf
from src.utils import fmt_money_pdf_whole


def _paraguay_quote_data() -> dict:
    return {
        "fecha": "2026-08-10",
        "cliente": "Cliente de prueba",
        "cedula": "1234567",
        "telefono": "0981000000",
        "items": [
            {
                "codigo": "SKU001",
                "producto": "Producto de prueba",
                "categoria": "PRODUCTOS",
                "cantidad": 1,
                "precio": 1_234_567.5,
                "subtotal": 1_234_567.5,
                "descuento": 1_000.0,
                "total": 1_233_567.5,
            }
        ],
        "subtotal_bruto": 1_234_567.5,
        "descuento_total": 1_000.0,
        "total_general": 1_233_567.5,
    }


def test_pdf_uses_explicit_country_and_currency_context(monkeypatch, tmp_path):
    quantity_countries: list[str] = []
    money_currencies: list[str] = []

    def fake_quantity(_item, *, country=None):
        quantity_countries.append(str(country or ""))
        return "50 g"

    def fake_money(value, *, currency=None):
        money_currencies.append(str(currency or ""))
        return f"$ {float(value):.2f}"

    monkeypatch.setattr(pdfgen, "cantidad_para_mostrar", fake_quantity)
    monkeypatch.setattr(pdfgen, "fmt_money_pdf", fake_money)

    output = pdfgen.generar_pdf(
        {
            "fecha": "2026-08-07",
            "cliente": "Cliente contexto",
            "cedula": "12345678",
            "telefono": "900000000",
            "items": [
                {
                    "codigo": "FIJ001",
                    "producto": "Fijador",
                    "categoria": "FIJADOR",
                    "cantidad": 0.050,
                    "precio": 10.0,
                    "subtotal": 10.0,
                    "descuento": 0.0,
                    "total": 10.0,
                }
            ],
            "subtotal_bruto": 10.0,
            "descuento_total": 0.0,
            "total_general": 10.0,
        },
        fixed_quote_no="PE-C01-0000001",
        out_path=str(tmp_path / "context.pdf"),
        country_code="PE",
        store_id="C01",
        company_type="EF PERFUMES",
        currency_code="USD",
    )

    assert output == str(tmp_path / "context.pdf")
    assert quantity_countries and set(quantity_countries) == {"PE"}
    assert money_currencies and set(money_currencies) == {"USD"}


def test_paraguay_whole_money_format_rounds_and_separates_thousands():
    assert fmt_money_pdf_whole(1_234_567.5, currency="PYG") == (
        f"{symbol_pdf('PYG')} 1.234.568"
    )


def test_paraguay_pdf_formats_every_money_column_as_whole_units(monkeypatch, tmp_path):
    whole_values: list[tuple[float, str]] = []
    decimal_values: list[tuple[float, str]] = []

    def fake_whole(value, currency=None, **_kwargs):
        whole_values.append((float(value), str(currency or "")))
        return "Gs. 1.234.568"

    def fake_decimal(value, currency=None):
        decimal_values.append((float(value), str(currency or "")))
        return "Gs. 1234567.50"

    monkeypatch.setattr(pdfgen, "fmt_money_pdf_whole", fake_whole)
    monkeypatch.setattr(pdfgen, "fmt_money_pdf", fake_decimal)

    pdfgen.generar_pdf(
        _paraguay_quote_data(),
        fixed_quote_no="PY-C01-0000001",
        out_path=str(tmp_path / "paraguay.pdf"),
        country_code="PY",
        store_id="C01",
        currency_code="PYG",
    )

    assert len(whole_values) == 7
    assert set(currency for _value, currency in whole_values) == {"PYG"}
    assert decimal_values == []
