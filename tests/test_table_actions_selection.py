import importlib.util
import sys
import types
from pathlib import Path


def _load_table_actions_mixin():
    module_name = "src.app_window_parts.table_actions"
    cached = sys.modules.get(module_name)
    if cached is not None:
        return cached.TableActionsMixin

    src_dir = Path(__file__).resolve().parents[1] / "src"
    app_parts_dir = src_dir / "app_window_parts"

    app_parts_pkg = sys.modules.get("src.app_window_parts")
    if app_parts_pkg is None:
        app_parts_pkg = types.ModuleType("src.app_window_parts")
        app_parts_pkg.__path__ = [str(app_parts_dir)]
        sys.modules["src.app_window_parts"] = app_parts_pkg

    utils_mod = types.ModuleType("src.utils")
    utils_mod.nz = lambda value, default=0: default if value is None else value
    sys.modules.setdefault("src.utils", utils_mod)

    widgets_mod = types.ModuleType("src.widgets")
    widgets_mod.show_price_picker = lambda *args, **kwargs: None
    widgets_mod.show_discount_dialog_for_item = lambda *args, **kwargs: None
    widgets_mod.show_observation_dialog = lambda *args, **kwargs: None
    sys.modules.setdefault("src.widgets", widgets_mod)

    models_mod = types.ModuleType("src.app_window_parts.models")
    models_mod.CAN_EDIT_UNIT_PRICE = True
    sys.modules.setdefault("src.app_window_parts.models", models_mod)

    spec = importlib.util.spec_from_file_location(module_name, app_parts_dir / "table_actions.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module.TableActionsMixin


TableActionsMixin = _load_table_actions_mixin()


class _FakeIndex:
    def __init__(self, row=None):
        self._row = row

    def row(self):
        return self._row

    def isValid(self):
        return self._row is not None


class _FakeSelectionModel:
    def __init__(self, selected_rows=None, current_row=None):
        self._selected_rows = list(selected_rows or [])
        self._current_row = current_row

    def selectedIndexes(self):
        return [_FakeIndex(r) for r in self._selected_rows]

    def currentIndex(self):
        return _FakeIndex(self._current_row)


class _FakeTable:
    def __init__(self, selected_rows=None, current_row=None):
        self._selection_model = _FakeSelectionModel(selected_rows, current_row)
        self._current_row = current_row

    def selectionModel(self):
        return self._selection_model

    def currentIndex(self):
        return _FakeIndex(self._current_row)


class _FakeWindow(TableActionsMixin):
    def __init__(self, *, selected_rows=None, current_row=None, ctx_row=None, items_count=3):
        self.table = _FakeTable(selected_rows, current_row)
        self.items = [{} for _ in range(items_count)]
        self._ctx_row = ctx_row


def test_single_item_action_row_consumes_context_row():
    win = _FakeWindow(selected_rows=[0, 2], current_row=2, ctx_row=0)

    row = win._single_item_action_row()

    assert row == 0
    assert win._ctx_row is None


def test_single_item_action_row_prefers_current_row_over_selected_rows():
    win = _FakeWindow(selected_rows=[0, 2], current_row=2, ctx_row=None)

    row = win._single_item_action_row()

    assert row == 2
