import pytest

from src.config import CATS
from src.product_rules import (
    is_py_unit_product,
    normalize_country,
    normalize_product_category,
    uses_gram_quantity,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("pe", "PERU"),
        ("Perú", "PERU"),
        ("PERÃš", "PERU"),
        ("PY", "PARAGUAY"),
        ({"country_code": "ve"}, "VENEZUELA"),
    ],
)
def test_normalize_country_accepts_codes_names_and_dicts(value, expected):
    assert normalize_country(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "fijador",
        {"categoria": "FIJADORES"},
        {"Departamento": "feromona"},
        {"DEPARTAMENTO": "FEROMONAS"},
    ],
)
def test_peru_extra_departments_use_gram_quantities(value):
    assert uses_gram_quantity(value, country="PE") is True


def test_normalize_product_category_accepts_category_and_department_keys():
    assert normalize_product_category({"Categoria": " fijador "}) == "FIJADOR"
    assert normalize_product_category({"departamento": " feromonas "}) == "FEROMONAS"


@pytest.mark.parametrize("country", ["PY", "PARAGUAY", "VE", "VENEZUELA"])
@pytest.mark.parametrize("category", ["FEROMONA", "FEROMONAS", "FIJADOR", "FIJADORES"])
def test_extra_departments_are_not_gram_quantities_outside_peru(country, category):
    assert uses_gram_quantity(category, country=country) is False


@pytest.mark.parametrize("country", ["PE", "PY", "VE"])
def test_configured_cats_remain_gram_quantities_in_supported_countries(country):
    assert uses_gram_quantity("esencia", country=country) is True


@pytest.mark.parametrize("code", ["FERO001", "fij002"])
def test_paraguay_unit_product_exceptions_remain_units(code):
    item = {"codigo": code, "categoria": "ESENCIAS"}
    assert is_py_unit_product(item, country="PY") is True
    assert uses_gram_quantity(item, country="PY") is False
    assert uses_gram_quantity(item, country="PE") is True


def test_global_cats_are_not_expanded_with_peru_departments():
    configured = {str(category).strip().upper() for category in CATS}
    assert configured.isdisjoint({"FEROMONA", "FEROMONAS", "FIJADOR", "FIJADORES"})
