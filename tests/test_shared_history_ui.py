"""Qt presentation tests with temporary data and startup isolated by conftest."""
import ast
import datetime
import os
from pathlib import Path
import re
import unittest

import pytest

from src.widgets_parts.quote_history_dialog import QuotesTableModel

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex
from PySide6.QtGui import QFont, QBrush, QFontDatabase
from PySide6.QtWidgets import QApplication, QTableView, QHeaderView

SOURCE = Path(__file__).resolve().parents[1] / 'src/widgets_parts/quote_history_dialog.py'


class HistoryPresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        font = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts/arial.ttf'
        if font.is_file():
            font_id = QFontDatabase.addApplicationFont(str(font))
            families = QFontDatabase.applicationFontFamilies(font_id)
            if families:
                cls.app.setFont(QFont(families[0], 10))
        cls.tree = ast.parse(SOURCE.read_text(encoding='utf-8-sig'))

    def test_model_renders_currency_items_and_pdf(self):
        model_node = next(node for node in self.tree.body
                          if isinstance(node, ast.ClassDef) and node.name == 'QuotesTableModel')
        # Legacy date/color helpers are irrelevant to these added columns.
        namespace = dict(Qt=Qt, QAbstractTableModel=QAbstractTableModel, QModelIndex=QModelIndex,
            QFont=QFont, QBrush=QBrush, APP_COUNTRY='PERU', datetime=datetime, os=os,
            _doc_header_for_country=lambda country: 'Documento', _parse_dt=lambda value: None,
            format_dt_legible=str, status_label=str, bg_for_status=lambda value: None,
            best_text_color_for_bg=lambda value: None, nz=lambda value, fallback: fallback if value is None else value,
            extract_quote_digits=lambda value: re.search(r'\d+$', value).group(), quote_match_key=lambda value: value,
            find_quote_pdf_path=lambda row: '')
        exec(compile(ast.Module(body=[model_node], type_ignores=[]), str(SOURCE), 'exec'), namespace)
        model = namespace['QuotesTableModel'](show_payment=True)
        model.set_rows([dict(id=1, quote_no='PE-001-0000001', cliente='Cliente de prueba',
            created_at='2026-09-07 10:00', country_code='PE', company_type='LA CASA DEL PERFUME',
            currency_shown='USD', total_shown=10, estado='PAGADO', metodo_pago='EFECTIVO',
            items_count=1, pdf_path='quote.pdf')])
        self.assertEqual(model.data(model.index(0, 1)), '0000001')
        self.assertEqual(model.data(model.index(0, 7)), '10.00 USD')
        self.assertEqual(model.data(model.index(0, model.columnCount()-2)), '1')
        self.assertEqual(model.data(model.index(0, model.columnCount()-1)), 'quote.pdf')
        view = QTableView()
        view.setModel(model)
        view.resize(1600, 200)
        view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        view.show()
        self.app.processEvents()
        pixmap = view.grab()
        self.assertFalse(pixmap.isNull())
        if os.environ.get('COTIZADOR_TEST_PREVIEW'):
            path = Path(os.environ['COTIZADOR_TEST_PREVIEW'])
            path.parent.mkdir(parents=True, exist_ok=True)
            self.assertTrue(pixmap.save(str(path)))
        view.close()

    def test_refresh_preserves_page_and_selection(self):
        window = next(node for node in self.tree.body
                      if isinstance(node, ast.ClassDef) and node.name == 'QuoteHistoryWindow')
        method = next(node for node in window.body
                      if isinstance(node, ast.FunctionDef) and node.name == '_refresh_shared_history')
        namespace = {}
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(SOURCE), 'exec'), namespace)
        class History:
            offset = 100
            selected = 27
            reloaded = False
            def _selected_quote_id(self): return self.selected
            def _reload_current_page(self): self.reloaded = True; self.selected = None
            def _select_row_by_quote_id(self, selected): self.selected = selected
        history = History()
        namespace['_refresh_shared_history'](history)
        self.assertEqual(history.offset, 100)
        self.assertEqual(history.selected, 27)
        self.assertTrue(history.reloaded)


@pytest.mark.parametrize('show_payment', [False, True])
def test_history_keeps_items_and_pdf_without_company_or_sync_columns(qapp, show_payment):
    model = QuotesTableModel(show_payment=show_payment, country='PERU')
    model.set_rows([dict(id=1, items_count=2, pdf_path='quote.pdf',
                         company_type='LA CASA DEL PERFUME', sync_label='Sincronizado')])
    headers = [model.headerData(c, Qt.Horizontal) for c in range(model.columnCount())]
    assert 'País / empresa' not in headers
    assert 'Sincronización' not in headers
    assert headers[-2:] == ['Items', 'PDF']
    assert model.data(model.index(0, model._idx_items())) == '2'
    assert model.data(model.index(0, model._idx_pdf())) == 'quote.pdf'


@pytest.mark.parametrize('quote_no', ['PE-004-0000501', '0000501'])
def test_history_finds_existing_pdf_after_its_path_was_cleared(qapp, monkeypatch, tmp_path, quote_no):
    from src import paths
    monkeypatch.setattr(paths, 'COTIZACIONES_DIR', str(tmp_path))
    pdf = tmp_path / 'C-PE-004-0000501_Cliente_de_prueba.pdf'
    pdf.write_bytes(b'local-pdf')
    model = QuotesTableModel(show_payment=True, country='PERU')
    model.set_rows([dict(id=1, quote_no=quote_no, country_code='PE', id_cotizador='004',
                         cliente='Cliente de prueba', pdf_path='')])
    index = model.index(0, model._idx_pdf())
    assert model.data(index) == pdf.name
    assert model.data(index, Qt.ToolTipRole) == str(pdf)
    # Finding a file for display must not mark an invalidated PDF as current.
    assert model.rows[0]['pdf_path'] == ''


@pytest.mark.parametrize('overrides', [
    {'country_code': 'PY', 'quote_no': 'PY-004-0000501'},
    {'id_cotizador': '005', 'quote_no': 'PE-005-0000501'},
    {'cliente': 'Otro cliente'},
    {'id_cotizador': ''},
    {'quote_no': ''},
])
def test_history_pdf_recovery_requires_matching_identity(qapp, monkeypatch, tmp_path, overrides):
    from src import paths
    monkeypatch.setattr(paths, 'COTIZACIONES_DIR', str(tmp_path))
    (tmp_path / 'C-PE-004-0000501_Cliente_de_prueba.pdf').write_bytes(b'other-pdf')
    row = dict(id=1, quote_no='PE-004-0000501', country_code='PE', id_cotizador='004',
               cliente='Cliente de prueba', pdf_path='')
    row.update(overrides)
    model = QuotesTableModel(show_payment=True, country='PERU')
    model.set_rows([row])
    assert model.data(model.index(0, model._idx_pdf())) == ''


def test_history_has_no_sync_report_or_status_label(qapp, monkeypatch, tmp_path):
    from types import SimpleNamespace
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QPushButton, QStatusBar
    from sqlModels.db import connect, ensure_schema
    from src.widgets_parts import quote_history_dialog as ui

    db_path = str(tmp_path / 'history.db')
    con = connect(db_path)
    try:
        ensure_schema(con)
    finally:
        con.close()
    monkeypatch.setattr(ui, 'resolve_db_path', lambda: db_path)
    monkeypatch.setattr(ui, 'is_ai_enabled', lambda **_: False)
    monkeypatch.setattr(ui.QTimer, 'singleShot', lambda *_: None)
    for method in ('_restore_window_state', '_save_window_state', '_apply_catalog_gate',
                   'refresh_recommendations_controls', '_wake_background_api_sync'):
        monkeypatch.setattr(ui.QuoteHistoryWindow, method, lambda *_: None)
    window = ui.QuoteHistoryWindow(
        catalog_manager=SimpleNamespace(server_mode=True), quote_events=None, app_icon=QIcon())
    try:
        assert not hasattr(window, 'shared_sync_status')
        assert not hasattr(window, '_show_sync_report')
        assert not window.findChildren(QStatusBar)
        assert window.btn_menu.toolTip() == ''
        assert not any('Sincronización' in button.text() for button in window.findChildren(QPushButton))
        assert window.btn_pdf.text() == 'Abrir PDF'
        # Render the real history layout with synthetic rows, without startup.
        window.model.set_rows([dict(id=1, quote_no='PE-004-0000501',
            cliente='Cliente de prueba', created_at='2026-09-15 10:00:00',
            cedula='00000000', telefono='-', estado='PAGADO', metodo_pago='EFECTIVO',
            currency_shown='PEN', total_shown=42.94, items_count=2,
            pdf_path='C-PE-004-0000501_Cliente_de_prueba.pdf')])
        window.lbl_page.setText('Mostrando 1-1 de 1')
        window.btn_chat.hide()
        window.show()
        qapp.processEvents()
        pixmap = window.grab()
        assert not pixmap.isNull()
        preview = os.environ.get('COTIZADOR_HISTORY_PREVIEW')
        if preview:
            path = Path(preview)
            path.parent.mkdir(parents=True, exist_ok=True)
            assert pixmap.save(str(path))
    finally:
        window.close()
        window.deleteLater()


if __name__ == '__main__':
    unittest.main()
