from pathlib import Path

import src.updater as updater


def _write_local_version(app_root: Path, version: str) -> None:
    app_root.mkdir(parents=True, exist_ok=True)
    (app_root / "version.txt").write_text(version, encoding="utf-8")


def test_check_for_updates_uses_installer_manifest_for_major_release(tmp_path, monkeypatch):
    app_root = tmp_path / "app"
    _write_local_version(app_root, "1.2.13")

    manifest = {
        "version": "2.0.0",
        "type": "installer",
        "url": "https://example.invalid/Setup_SistemaCotizaciones_2.0.0.exe",
        "sha256": "abc123",
    }

    planned = []
    spawned = []

    monkeypatch.setattr(updater, "_app_root", lambda: str(app_root))
    monkeypatch.setattr(updater, "_http_get_json", lambda *args, **kwargs: (manifest, "{}"))
    monkeypatch.setattr(updater, "_read_state", lambda log=None: {})
    monkeypatch.setattr(updater, "_plan_installer", lambda *args, **kwargs: planned.append(args[0]) or {"version": "2.0.0", "installer": {"path": "setup.exe"}})
    monkeypatch.setattr(updater, "_spawn_apply", lambda plan, app_config, ui=None, log=None: spawned.append(plan))

    result = updater.check_for_updates_and_maybe_install(
        {"update_manifest_url": "https://example.invalid/config/cotizador.json"}
    )

    assert result == {"status": "UPDATE_STARTED", "method": "installer", "remote": "2.0.0"}
    assert planned == [manifest]
    assert spawned == [{"version": "2.0.0", "installer": {"path": "setup.exe"}}]


def test_check_for_updates_falls_back_to_installer_when_incremental_base_differs(tmp_path, monkeypatch):
    app_root = tmp_path / "app"
    _write_local_version(app_root, "1.2.12")

    manifest = {
        "version": "2.0.0",
        "type": "files",
        "from_version": "1.2.13",
        "base_url": "https://example.invalid/Output/updates/2.0.0/",
        "files": [],
        "delete": [],
        "url": "https://example.invalid/Setup_SistemaCotizaciones_2.0.0.exe",
        "sha256": "abc123",
    }

    spawned = []

    monkeypatch.setattr(updater, "_app_root", lambda: str(app_root))
    monkeypatch.setattr(updater, "_http_get_json", lambda *args, **kwargs: (manifest, "{}"))
    monkeypatch.setattr(updater, "_read_state", lambda log=None: {})
    monkeypatch.setattr(
        updater,
        "_plan_files_update",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no debe intentar files cuando from_version no coincide")),
    )
    monkeypatch.setattr(updater, "_plan_installer", lambda *args, **kwargs: {"version": "2.0.0", "installer": {"path": "setup.exe"}})
    monkeypatch.setattr(updater, "_spawn_apply", lambda plan, app_config, ui=None, log=None: spawned.append(plan))

    result = updater.check_for_updates_and_maybe_install(
        {"update_manifest_url": "https://example.invalid/config/cotizador.json"}
    )

    assert result == {"status": "UPDATE_STARTED", "method": "installer_fallback", "remote": "2.0.0"}
    assert spawned == [{"version": "2.0.0", "installer": {"path": "setup.exe"}}]
