from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest


_ROOT = Path(__file__).resolve().parents[1]


def _load_without_package_init(module_name: str, relative_path: str):
    """Carga un submódulo sin ejecutar los __init__ con UI circular del proyecto."""
    cached = sys.modules.get(module_name)
    if cached is not None:
        return cached

    package_name = module_name.rsplit(".", 1)[0]
    package_path = (_ROOT / relative_path).parent
    before = {
        name: module
        for name, module in sys.modules.items()
        if name == package_name or name.startswith(package_name + ".")
    }

    package = types.ModuleType(package_name)
    package.__path__ = [str(package_path)]
    sys.modules[package_name] = package

    try:
        spec = importlib.util.spec_from_file_location(module_name, _ROOT / relative_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module
    finally:
        for name in list(sys.modules):
            if (name == package_name or name.startswith(package_name + ".")) and name not in before:
                sys.modules.pop(name, None)
        sys.modules.update(before)


actions = _load_without_package_init("src.ai.assistant.actions", "src/ai/assistant/actions.py")
ticket_actions = _load_without_package_init(
    "src.app_window_parts.ticket_actions", "src/app_window_parts/ticket_actions.py"
)
labels_dialog = _load_without_package_init(
    "src.widgets_parts.labels_dialog", "src/widgets_parts/labels_dialog.py"
)
preview_dialog = _load_without_package_init(
    "src.widgets_parts.preview_dialog", "src/widgets_parts/preview_dialog.py"
)


class _CatalogWindow:
    def __init__(self, *, products=None, presentations=None):
        self.productos = list(products or [])
        self.presentaciones = list(presentations or [])


@pytest.mark.parametrize("category", ["FEROMONA", "FEROMONAS", "FIJADOR", "FIJADORES"])
def test_ai_normalizes_peru_gram_departments_from_entered_grams(monkeypatch, category):
    monkeypatch.setattr(actions, "APP_COUNTRY", "PERU")
    window = _CatalogWindow(products=[{"codigo": "SKU001", "categoria": category}])

    assert actions.normalize_qty_for_code(window, "SKU001", "product", "50") == pytest.approx(0.050)


def test_ai_does_not_treat_a_presentation_department_as_gram_category(monkeypatch):
    monkeypatch.setattr(actions, "APP_COUNTRY", "PERU")
    window = _CatalogWindow(
        presentations=[
            {
                "codigo": "PRES001",
                "categoria": "PRESENTACION",
                "departamento": "ESENCIAS",
            }
        ]
    )

    assert actions.normalize_qty_for_code(window, "PRES001", "presentation", "50") == 50.0


def test_ai_keeps_existing_paraguay_rules_and_unit_exception(monkeypatch):
    monkeypatch.setattr(actions, "APP_COUNTRY", "PARAGUAY")
    window = _CatalogWindow(
        products=[
            {"codigo": "ES001", "categoria": "ESENCIAS"},
            {"codigo": "FERO001", "categoria": "ESENCIAS"},
            {"codigo": "FIJ001", "categoria": "FIJADOR"},
        ]
    )

    assert actions.normalize_qty_for_code(window, "ES001", "product", "50") == 1.0
    assert actions.normalize_qty_for_code(window, "FERO001", "product", "50") == 50.0
    assert actions.normalize_qty_for_code(window, "FIJ001", "product", "50") == 50.0


def test_peru_ticket_header_includes_all_weight_departments():
    items = [
        {"codigo": "ES001", "categoria": "ESENCIAS", "cantidad": 0.020},
        {"codigo": "FERO001", "categoria": "FEROMONAS", "cantidad": 0.050},
        {"codigo": "FIJ001", "categoria": "FIJADOR", "cantidad": 0.030},
        {"codigo": "BOT001", "categoria": "BOTELLAS", "cantidad": 2},
        {
            "codigo": "PRES001",
            "categoria": "PRESENTACION",
            "departamento": "ESENCIAS",
            "cantidad": 7,
        },
    ]

    assert ticket_actions._peru_header_extra_lines(items) == [
        "Total de Botellas: 2",
        "Total de Esencias: 100 g",
    ]


@pytest.mark.parametrize("category", ["FEROMONAS", "FIJADOR"])
def test_preview_and_labels_convert_peru_weight_departments_to_grams(monkeypatch, category):
    item = {"codigo": "SKU001", "categoria": category, "cantidad": 0.050}
    monkeypatch.setattr(preview_dialog, "APP_COUNTRY", "PERU")

    assert preview_dialog._esencia_a_gramos(item, 0.050) == pytest.approx(50.0)
    assert labels_dialog._esencia_a_gramos(item, 0.050, "PE") == pytest.approx(50.0)


def test_labels_keep_paraguay_unit_exception_out_of_grams():
    item = {"codigo": "FERO001", "categoria": "ESENCIAS", "cantidad": 3}

    assert labels_dialog._esencia_a_gramos(item, 3, "PY") == 0.0


@pytest.mark.parametrize(
    ("count_raw", "labels_raw"),
    [
        ("2", "50 50"),
        ("1", "100"),
        ("3", "60 60 60"),
    ],
)
def test_label_validation_allows_partial_labels_that_do_not_sum_to_total(count_raw, labels_raw):
    count_ok, labels_ok, labels_count = labels_dialog._validate_label_entries(count_raw, labels_raw)

    assert count_ok is True
    assert labels_ok is True
    assert labels_count == int(count_raw)


def test_label_validation_still_rejects_count_mismatch():
    count_ok, labels_ok, labels_count = labels_dialog._validate_label_entries("2", "50")

    assert count_ok is False
    assert labels_ok is True
    assert labels_count == 1
