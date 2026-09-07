"""Aislar también los efectos de importación de config, Qt y SQLite."""
from __future__ import annotations

import logging
import os
from pathlib import Path
import sqlite3
import tempfile
import urllib.request

import pytest


def pytest_configure(config):
    sandbox = tempfile.TemporaryDirectory(prefix="cotizador-tests-", ignore_cleanup_errors=True)
    root = Path(sandbox.name).resolve()
    patches = pytest.MonkeyPatch()
    config._cotizador_sandbox = sandbox
    config._cotizador_patches = patches
    if config.option.basetemp is None:
        config.option.basetemp = str(root / "pytest")
    allowed = (root, Path(config.option.basetemp).resolve())
    patches.setenv("QT_QPA_PLATFORM", "offscreen")
    patches.setenv("LOG_DIR", str(root / "logs"))

    # config puede probar la DB instalada antes de caer a Documentos. Impedir
    # esa apertura, incluso durante colección y recuperación de settings.
    original_connect = sqlite3.connect

    def isolated_connect(database, *args, **kwargs):
        target = os.fspath(database)
        if target != ":memory:" and not (target.startswith("file:") and "mode=memory" in target):
            raw = target.removeprefix("file:").split("?", 1)[0]
            path = Path(raw).resolve()
            if not any(path.is_relative_to(base) for base in allowed):
                raise sqlite3.OperationalError("Las pruebas solo pueden abrir SQLite temporal")
        return original_connect(database, *args, **kwargs)

    patches.setattr(sqlite3, "connect", isolated_connect)

    def no_network(*args, **kwargs):
        raise AssertionError("Las pruebas deben simular la red")

    patches.setattr(urllib.request, "urlopen", no_network)
    from PySide6.QtCore import QStandardPaths

    patches.setattr(QStandardPaths, "writableLocation", lambda *_: str(root))
    # El orden evita el ciclo histórico entre widgets y app_window_parts.
    # No se ejecuta run_app: nada de listener, updater, impresoras ni Ollama.
    import src.app  # noqa: F401


def pytest_unconfigure(config):
    patches = getattr(config, "_cotizador_patches", None)
    if patches is not None:
        logging.shutdown()
        patches.undo()
        config._cotizador_sandbox.cleanup()


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from PySide6.QtGui import QFont, QFontDatabase

    font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "segoeui.ttf"
    if font_path.is_file():
        font_id = QFontDatabase.addApplicationFont(str(font_path))
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            app.setFont(QFont(families[0], 9))
    yield app
