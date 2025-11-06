import os, shutil
import pytest
from pathlib import Path

@pytest.fixture(autouse=True, scope="session")
def _init_logging_for_tests(tmp_path_factory):
    # Cada corrida de tests escribe logs a un directorio temporal
    log_dir = tmp_path_factory.mktemp("logs")
    os.environ["LOG_DIR"] = str(log_dir)
    os.environ["LOG_LEVEL"] = "DEBUG"

    # Evita que PySide6 o tu main se importen: probamos módulos puros
    from src.logging_setup import init_logging
    init_logging(level="DEBUG", log_dir=str(log_dir))

    yield
    # Limpieza opcional de logs generados por las pruebas
    try:
        shutil.rmtree(log_dir, ignore_errors=True)
    except Exception:
        pass
