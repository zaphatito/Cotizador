from copy import deepcopy
from unittest.mock import Mock
from PIL import Image

import pytest

from src.widgets_parts import labels_dialog as module


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
        {"codigo": "BASE02", "categoria": "DILUYENTES", "cantidad": 0.025},
    ])
    try:
        assert dialog.table.rowCount() == 2
        assert dialog.table.item(1, 1).text() == "25"
        dialog.table.item(0, 2).setText("4")
        assert dialog.table.item(0, 3).text() == "0.5 0.5 0.5 0.5"
        assert dialog.btn_print.isEnabled()
    finally:
        dialog.deleteLater()
