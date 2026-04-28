from pathlib import Path

import src.updater as updater


def _write_local_version(app_root: Path, version: str) -> None:
    app_root.mkdir(parents=True, exist_ok=True)
    (app_root / "version.txt").write_text(version, encoding="utf-8")


def _status_texts(events: list[tuple[str, dict]]) -> list[str]:
    return [str(payload.get("text") or "") for kind, payload in events if kind == "status"]


def test_check_for_updates_uses_files_manifest_when_incremental_base_matches(tmp_path, monkeypatch):
    app_root = tmp_path / "app"
    _write_local_version(app_root, "2.0.4")

    manifest = {
        "version": "2.0.5",
        "type": "files",
        "from_version": "2.0.4",
        "base_url": "https://example.invalid/Output/updates/2.0.5/",
        "files": [{"path": "src/app.py", "sha256": "abc123"}],
        "delete": [],
        "url": "https://example.invalid/Setup_SistemaCotizaciones_2.0.5.exe",
        "sha256": "def456",
    }
    files_plan = {
        "version": "2.0.5",
        "staging_root": "C:/tmp/staging",
        "files": [{"src": "C:/tmp/staging/src/app.py", "dst": "C:/app/src/app.py"}],
        "delete": [],
    }

    events: list[tuple[str, dict]] = []
    spawned: list[dict] = []

    monkeypatch.setattr(updater, "_app_root", lambda: str(app_root))
    monkeypatch.setattr(updater, "_http_get_json", lambda *args, **kwargs: (manifest, "{}"))
    monkeypatch.setattr(updater, "_read_state", lambda log=None: {})
    monkeypatch.setattr(updater, "_clear_failure", lambda state, log=None: None)
    monkeypatch.setattr(updater, "_plan_files_update", lambda *args, **kwargs: files_plan)
    monkeypatch.setattr(
        updater,
        "_plan_installer",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no debe usar installer cuando files aplica")),
    )
    monkeypatch.setattr(updater, "_spawn_apply", lambda plan, app_config, ui=None, log=None: spawned.append(plan))

    result = updater.check_for_updates_and_maybe_install(
        {"update_manifest_url": "https://example.invalid/config/cotizador.json"},
        ui=lambda kind, payload: events.append((kind, dict(payload))),
    )

    assert result == {"status": "UPDATE_STARTED", "method": "files", "remote": "2.0.5"}
    assert spawned == [files_plan]
    assert any("Preparando actualización (files)" in text for text in _status_texts(events))
    assert all("installer" not in text.lower() for text in _status_texts(events))


def test_check_for_updates_switches_to_installer_when_incremental_base_differs(tmp_path, monkeypatch):
    app_root = tmp_path / "app"
    _write_local_version(app_root, "2.0.3")

    manifest = {
        "version": "2.0.5",
        "type": "files",
        "from_version": "2.0.4",
        "base_url": "https://example.invalid/Output/updates/2.0.5/",
        "files": [],
        "delete": [],
        "url": "https://example.invalid/Setup_SistemaCotizaciones_2.0.5.exe",
        "sha256": "abc123",
    }
    installer_plan = {"version": "2.0.5", "installer": {"path": "setup.exe"}}

    events: list[tuple[str, dict]] = []
    spawned: list[dict] = []

    monkeypatch.setattr(updater, "_app_root", lambda: str(app_root))
    monkeypatch.setattr(updater, "_http_get_json", lambda *args, **kwargs: (manifest, "{}"))
    monkeypatch.setattr(updater, "_read_state", lambda log=None: {})
    monkeypatch.setattr(
        updater,
        "_plan_files_update",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no debe intentar files cuando from_version no coincide")),
    )
    monkeypatch.setattr(updater, "_plan_installer", lambda *args, **kwargs: installer_plan)
    monkeypatch.setattr(updater, "_spawn_apply", lambda plan, app_config, ui=None, log=None: spawned.append(plan))

    result = updater.check_for_updates_and_maybe_install(
        {"update_manifest_url": "https://example.invalid/config/cotizador.json"},
        ui=lambda kind, payload: events.append((kind, dict(payload))),
    )

    texts = _status_texts(events)
    assert result == {
        "status": "UPDATE_STARTED",
        "method": "installer_due_to_base_mismatch",
        "remote": "2.0.5",
    }
    assert spawned == [installer_plan]
    assert any("Paquete incremental requiere base 2.0.4; local=2.0.3. Se usará instalador completo." in text for text in texts)
    assert not any("Error en actualización" in text for text in texts)
    assert not any("Fallback:" in text for text in texts)
    assert all(kind != "failed" for kind, _payload in events)


def test_check_for_updates_falls_back_to_installer_when_files_plan_fails(tmp_path, monkeypatch):
    app_root = tmp_path / "app"
    _write_local_version(app_root, "2.0.4")

    manifest = {
        "version": "2.0.5",
        "type": "files",
        "from_version": "2.0.4",
        "base_url": "https://example.invalid/Output/updates/2.0.5/",
        "files": [{"path": "src/app.py", "sha256": "abc123"}],
        "delete": [],
        "url": "https://example.invalid/Setup_SistemaCotizaciones_2.0.5.exe",
        "sha256": "abc123",
    }
    installer_plan = {"version": "2.0.5", "installer": {"path": "setup.exe"}}

    events: list[tuple[str, dict]] = []
    spawned: list[dict] = []

    monkeypatch.setattr(updater, "_app_root", lambda: str(app_root))
    monkeypatch.setattr(updater, "_http_get_json", lambda *args, **kwargs: (manifest, "{}"))
    monkeypatch.setattr(updater, "_read_state", lambda log=None: {})
    monkeypatch.setattr(
        updater,
        "_plan_files_update",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom files")),
    )
    monkeypatch.setattr(updater, "_plan_installer", lambda *args, **kwargs: installer_plan)
    monkeypatch.setattr(updater, "_spawn_apply", lambda plan, app_config, ui=None, log=None: spawned.append(plan))

    result = updater.check_for_updates_and_maybe_install(
        {"update_manifest_url": "https://example.invalid/config/cotizador.json"},
        ui=lambda kind, payload: events.append((kind, dict(payload))),
    )

    texts = _status_texts(events)
    assert result == {"status": "UPDATE_STARTED", "method": "installer_fallback", "remote": "2.0.5"}
    assert spawned == [installer_plan]
    assert any("Preparando actualización (files)" in text for text in texts)
    assert any("La actualización incremental no pudo aplicarse. Cambiando a instalador completo" in text for text in texts)
    assert not any("Error en actualización" in text for text in texts)
    assert all(kind != "failed" for kind, _payload in events)


def test_check_for_updates_uses_archive_manifest_when_base_matches(tmp_path, monkeypatch):
    app_root = tmp_path / "app"
    _write_local_version(app_root, "2.0.4")

    manifest = {
        "version": "2.0.5",
        "type": "archive",
        "from_version": "2.0.4",
        "archive_url": "https://github.com/example/CotizadorReleases/releases/download/v2.0.5/update.zip",
        "archive_sha256": "abc123",
        "files": [{"path": "SistemaCotizaciones.exe", "sha256": "def456"}],
        "delete": [],
        "url": "https://github.com/example/CotizadorReleases/releases/download/v2.0.5/Setup.exe",
        "sha256": "ghi789",
    }
    archive_plan = {
        "version": "2.0.5",
        "staging_root": "C:/tmp/staging",
        "files": [{"src": "C:/tmp/staging/SistemaCotizaciones.exe", "dst": "C:/app/SistemaCotizaciones.exe"}],
        "delete": [],
    }

    events: list[tuple[str, dict]] = []
    spawned: list[dict] = []

    monkeypatch.setattr(updater, "_app_root", lambda: str(app_root))
    monkeypatch.setattr(updater, "_http_get_json", lambda *args, **kwargs: (manifest, "{}"))
    monkeypatch.setattr(updater, "_read_state", lambda log=None: {})
    monkeypatch.setattr(updater, "_clear_failure", lambda state, log=None: None)
    monkeypatch.setattr(updater, "_plan_archive_update", lambda *args, **kwargs: archive_plan)
    monkeypatch.setattr(
        updater,
        "_plan_installer",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no debe usar installer cuando archive aplica")),
    )
    monkeypatch.setattr(updater, "_spawn_apply", lambda plan, app_config, ui=None, log=None: spawned.append(plan))

    result = updater.check_for_updates_and_maybe_install(
        {"update_manifest_url": "https://github.com/example/CotizadorReleases/releases/latest/download/cotizador.json"},
        ui=lambda kind, payload: events.append((kind, dict(payload))),
    )

    assert result == {"status": "UPDATE_STARTED", "method": "archive", "remote": "2.0.5"}
    assert spawned == [archive_plan]
    assert any("Preparando actualización (archive)" in text for text in _status_texts(events))


def test_check_for_updates_fails_if_files_manifest_is_incompatible_and_has_no_installer(tmp_path, monkeypatch):
    app_root = tmp_path / "app"
    _write_local_version(app_root, "2.0.3")

    manifest = {
        "version": "2.0.5",
        "type": "files",
        "from_version": "2.0.4",
        "base_url": "https://example.invalid/Output/updates/2.0.5/",
        "files": [],
        "delete": [],
    }

    events: list[tuple[str, dict]] = []
    failures: list[tuple[str, str]] = []

    monkeypatch.setattr(updater, "_app_root", lambda: str(app_root))
    monkeypatch.setattr(updater, "_http_get_json", lambda *args, **kwargs: (manifest, "{}"))
    monkeypatch.setattr(updater, "_read_state", lambda log=None: {})
    monkeypatch.setattr(updater, "_mark_failure", lambda app_config, state, remote_version, err, log=None: failures.append((remote_version, str(err))) or 15)

    result = updater.check_for_updates_and_maybe_install(
        {"update_manifest_url": "https://example.invalid/config/cotizador.json"},
        ui=lambda kind, payload: events.append((kind, dict(payload))),
    )

    assert result == {
        "status": "FAILED_RETRY_LATER",
        "error": "Paquete incremental requiere base 2.0.4; local=2.0.3",
        "retry_in": 15,
    }
    assert failures == [("2.0.5", "Paquete incremental requiere base 2.0.4; local=2.0.3")]
    assert any(kind == "failed" and payload.get("error") == "Paquete incremental requiere base 2.0.4; local=2.0.3" for kind, payload in events)
