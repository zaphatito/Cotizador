import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace


def test_pilot_ignores_production_updater_even_when_settings_enable_it(monkeypatch):
    from src import updater
    import urllib.request
    def forbidden(*args, **kwargs):
        raise AssertionError("No debe consultar producción")
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    result = updater.check_for_updates_and_maybe_install({
        "update_check_on_startup": True, "update_mode": "SILENT",
        "update_manifest_url": "https://example.invalid/production.json",
    })
    assert result == {"status": "PILOT_MANUAL_ONLY"}


def test_application_import_and_database_paths_are_isolated(tmp_path):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, str(root / "pilot_main.py"),
        "--pilot-self-check", str(tmp_path)], cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads((tmp_path / "result.json").read_text())["production_sentinel_unchanged"]


def test_duplicate_uses_current_installation_and_preserves_historical_currency():
    from src.catalog_context import CatalogScope
    from src.quote_context_service import duplicate_quote_context
    header = dict(quote_context_version=1, country_code="PE", company_type="LA CASA DEL PERFUME",
        base_currency="USD", cotizador_username="TESTUSER", id_cotizador="001",
        sync_owner_id="9", sync_current_owner_id="9")
    manager = SimpleNamespace(available_scopes=(CatalogScope("PE", "LA CASA DEL PERFUME"),),
        username="TESTUSER", id_cotizador="002")
    copy = duplicate_quote_context(header, manager)
    assert copy.id_cotizador == "002"
    assert copy.base_currency == "USD"
    assert header["id_cotizador"] == "001"
