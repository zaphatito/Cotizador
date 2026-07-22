from src.quote_code import format_quote_code


def test_format_quote_code_preserves_complete_historical_identity():
    result = format_quote_code(
        country_code="PE",
        store_id="",
        quote_no="pe-123-42",
        width=7,
    )

    assert result == "PE-123-0000042"
