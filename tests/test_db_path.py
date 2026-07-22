from __future__ import annotations

import importlib.util
import logging
import os
import sys
import types
from pathlib import Path

import pytest


def _load_db_path_module():
    module_name = "src._db_path_test_module"
    paths_name = "src.paths"
    logging_name = "src.logging_setup"
    previous_paths = sys.modules.get(paths_name)
    previous_logging = sys.modules.get(logging_name)

    paths_stub = types.ModuleType(paths_name)
    paths_stub.DATA_DIR = ""
    logging_stub = types.ModuleType(logging_name)
    logging_stub.get_logger = logging.getLogger

    try:
        sys.modules[paths_name] = paths_stub
        sys.modules[logging_name] = logging_stub
        source = Path(__file__).resolve().parents[1] / "src" / "db_path.py"
        spec = importlib.util.spec_from_file_location(module_name, source)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop(module_name, None)
        if previous_paths is None:
            sys.modules.pop(paths_name, None)
        else:
            sys.modules[paths_name] = previous_paths
        if previous_logging is None:
            sys.modules.pop(logging_name, None)
        else:
            sys.modules[logging_name] = previous_logging


db_path = _load_db_path_module()


@pytest.fixture(autouse=True)
def _reset_db_path_cache(monkeypatch):
    monkeypatch.setattr(db_path, "_CACHED_DB_PATH", None)
    monkeypatch.setattr(db_path, "_CACHED_KIND", None)


def test_resolve_db_path_keeps_existing_primary_on_transient_lock(monkeypatch, tmp_path):
    app_dir = tmp_path / "app"
    primary = app_dir / "sqlModels" / "app.sqlite3"
    fallback_dir = tmp_path / "data"
    primary.parent.mkdir(parents=True)
    primary.touch()

    calls: list[str] = []

    def fake_probe(path: str) -> tuple[bool, bool]:
        calls.append(os.path.normpath(path))
        return False, True

    monkeypatch.setattr(db_path, "_base_dir_for_app", lambda: str(app_dir))
    monkeypatch.setattr(db_path, "DATA_DIR", str(fallback_dir))
    monkeypatch.setattr(db_path, "_probe_sqlite_write", fake_probe)

    selected = db_path.resolve_db_path()

    assert os.path.normpath(selected) == os.path.normpath(str(primary))
    assert calls == [os.path.normpath(str(primary))]
    assert "primary_busy" in db_path.db_path_debug_info()


def test_resolve_db_path_uses_fallback_for_permanent_primary_failure(monkeypatch, tmp_path):
    app_dir = tmp_path / "app"
    primary = app_dir / "sqlModels" / "app.sqlite3"
    fallback = tmp_path / "data" / "app.sqlite3"

    def fake_probe(path: str) -> tuple[bool, bool]:
        if os.path.normpath(path) == os.path.normpath(str(primary)):
            return False, False
        return True, False

    monkeypatch.setattr(db_path, "_base_dir_for_app", lambda: str(app_dir))
    monkeypatch.setattr(db_path, "DATA_DIR", str(fallback.parent))
    monkeypatch.setattr(db_path, "_probe_sqlite_write", fake_probe)

    selected = db_path.resolve_db_path()

    assert os.path.normpath(selected) == os.path.normpath(str(fallback))
    assert "fallback" in db_path.db_path_debug_info()
