"""Comprobación del binario empaquetado usando únicamente datos sintéticos."""
import hashlib
import json
import os
from pathlib import Path
import socket


def run_self_check(output_dir: str) -> int:
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    production = root / "production"
    (production / "sqlModels").mkdir(parents=True, exist_ok=True)
    sentinel = production / "sqlModels/app.sqlite3"
    sentinel.write_bytes(b"SENTINEL - NO MODIFICAR")
    original = hashlib.sha256(sentinel.read_bytes()).hexdigest()
    os.chdir(production)
    os.environ["LOG_DIR"] = str(root / "logs")
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtCore import QStandardPaths
    QStandardPaths.writableLocation = staticmethod(lambda _: str(root / "documents"))

    def forbidden(*args, **kwargs):
        raise AssertionError("La comprobación no permite conexiones externas.")
    socket.create_connection = forbidden
    socket.socket.connect = forbidden

    from src import build_profile, updater
    import urllib.request
    urllib.request.urlopen = forbidden
    assert updater.check_for_updates_and_maybe_install({
        "update_check_on_startup": True, "update_mode": "SILENT",
        "update_manifest_url": "https://example.invalid/production.json"
    })["status"] == "PILOT_MANUAL_ONLY"
    from src import config, db_path, paths
    expected = root / "documents" / build_profile.DATA_FOLDER / "data/app.sqlite3"
    assert Path(db_path.resolve_db_path()).resolve() == expected
    assert Path(config._resolve_db_path_no_log()).resolve() == expected
    assert config.APP_CONFIG["update_check_on_startup"] is False
    assert config.APP_CONFIG["update_manifest_url"] == ""
    assert not config._recover_identity_settings_if_using_fallback(None, str(expected))
    assert all(str(production) not in p for p in config._candidate_config_paths())
    from src import app
    from src.widgets_parts.quote_history_dialog import QuoteHistoryWindow
    from PySide6.QtWidgets import QApplication
    qt = QApplication.instance() or QApplication([])
    assert app._MUTEX_NAME == build_profile.MUTEX_NAME
    assert app._MUTEX_NAME != "Local\\SistemaCotizaciones_SingleInstance"
    from sqlModels.db import connect, ensure_schema
    con = connect(str(expected))
    try:
        ensure_schema(con)
        con.execute("CREATE TABLE IF NOT EXISTS pilot_preservation_test(value TEXT)")
        con.execute("INSERT INTO pilot_preservation_test VALUES('preservar')")
        con.commit()
        ensure_schema(con)
        assert con.execute("SELECT value FROM pilot_preservation_test").fetchone()[0] == "preservar"
    finally:
        con.close()
    assert hashlib.sha256(sentinel.read_bytes()).hexdigest() == original
    report = {"passed": True, "channel": build_profile.CHANNEL, "database_isolated": True,
        "production_sentinel_unchanged": True, "updates_disabled_before_network": True,
        "schema_reapply_preserves_data": True, "qt_and_history_import": True}
    (root / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0
