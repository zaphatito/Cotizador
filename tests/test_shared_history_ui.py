"""Isolated Qt presentation tests; no application/config/installed DB imports."""
import ast
import datetime
import os
from pathlib import Path
import re
import unittest

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

    def test_model_renders_origin_currency_scope_and_sync_state(self):
        model_node = next(node for node in self.tree.body
                          if isinstance(node, ast.ClassDef) and node.name == 'QuotesTableModel')
        # Legacy date/color helpers are irrelevant to these added columns.
        namespace = dict(Qt=Qt, QAbstractTableModel=QAbstractTableModel, QModelIndex=QModelIndex,
            QFont=QFont, QBrush=QBrush, APP_COUNTRY='PERU', datetime=datetime, os=os,
            _doc_header_for_country=lambda country: 'Documento', _parse_dt=lambda value: None,
            format_dt_legible=str, status_label=str, bg_for_status=lambda value: None,
            best_text_color_for_bg=lambda value: None, nz=lambda value, fallback: fallback if value is None else value,
            extract_quote_digits=lambda value: re.search(r'\d+$', value).group(), quote_match_key=lambda value: value)
        exec(compile(ast.Module(body=[model_node], type_ignores=[]), str(SOURCE), 'exec'), namespace)
        model = namespace['QuotesTableModel'](show_payment=True)
        model.set_rows([dict(id=1, quote_no='PE-001-0000001', cliente='Cliente de prueba',
            created_at='2026-09-07 10:00', country_code='PE', company_type='LA CASA DEL PERFUME',
            currency_shown='USD', total_shown=10, estado='PAGADO', metodo_pago='EFECTIVO',
            items_count=1, sync_label='Sincronizado')])
        self.assertEqual(model.data(model.index(0, 1)), 'PE-001-0000001')
        self.assertEqual(model.data(model.index(0, 7)), '10.00 USD')
        self.assertIn('LA CASA DEL PERFUME', model.data(model.index(0, model.columnCount()-2)))
        self.assertEqual(model.data(model.index(0, model.columnCount()-1)), 'Sincronizado')
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


if __name__ == '__main__':
    unittest.main()
