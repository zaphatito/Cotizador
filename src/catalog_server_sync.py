from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from sqlModels.db import connect, ensure_schema, tx

from .api.catalog_stock_client import fetch_catalog_stock
from .db_path import resolve_db_path
from .logging_setup import get_logger
from .remote_configuration import (
    RemoteConfigurationOutcome,
    add_configuration_known_state,
    apply_remote_configuration,
    extract_remote_configuration,
)

log = get_logger(__name__)
_DETACHED_THREADS: dict[QThread, QObject] = {}


@dataclass(frozen=True, slots=True)
class CatalogSyncOutcome:
    changes: dict[str, Any]
    sync_state: dict[str, Any]
    configuration: RemoteConfigurationOutcome | None = None


@dataclass(slots=True)
class SyncRetrySchedule:
    success_interval_ms: int = 60_000
    error_intervals_ms: tuple[int, ...] = (60_000, 120_000, 300_000)
    consecutive_errors: int = 0

    def after_success(self) -> int:
        self.consecutive_errors = 0
        return int(self.success_interval_ms)

    def after_error(self) -> int:
        idx = min(self.consecutive_errors, len(self.error_intervals_ms) - 1)
        self.consecutive_errors += 1
        return int(self.error_intervals_ms[idx])


def _with_cache_connection(db_path: str, callback: Callable[[Any], Any]) -> Any:
    con = connect(db_path)
    try:
        ensure_schema(con)
        return callback(con)
    finally:
        con.close()


def sync_catalog_stock_once(
    *,
    db_path: str,
    username: str,
    id_cotizador: str,
    fetch_fn: Callable[[dict[str, Any]], dict[str, Any]] = fetch_catalog_stock,
) -> CatalogSyncOutcome:
    """Ejecuta un ciclo de red sin mantener una conexion SQLite abierta."""

    clean_username = str(username or "").strip()
    clean_cotizador = str(id_cotizador or "").strip()
    if not clean_username or not clean_cotizador:
        raise ValueError("Se requieren username e id_cotizador para sincronizar el catalogo.")

    from sqlModels import catalog_cache_repo

    _with_cache_connection(
        db_path,
        lambda con: catalog_cache_repo.record_sync_attempt(con, clean_username, clean_cotizador),
    )
    try:
        def _known_state(con):
            catalog_state = catalog_cache_repo.build_known_state(
                con,
                clean_username,
                clean_cotizador,
            )
            return add_configuration_known_state(con, catalog_state)

        known_state = _with_cache_connection(db_path, _known_state)
        payload = fetch_fn(known_state)

        def _apply_payload(con):
            with tx(con):
                catalog_changes = catalog_cache_repo.apply_sync_payload(
                    con,
                    clean_username,
                    clean_cotizador,
                    payload,
                )
                configuration = extract_remote_configuration(payload)
                configuration_outcome = apply_remote_configuration(
                    con,
                    clean_username,
                    clean_cotizador,
                    configuration,
                )
            return catalog_changes, configuration_outcome

        changes, configuration_outcome = _with_cache_connection(db_path, _apply_payload)
    except Exception as exc:
        message = str(exc or exc.__class__.__name__).strip()[:1200]
        try:
            _with_cache_connection(
                db_path,
                lambda con: catalog_cache_repo.record_sync_error(
                    con,
                    clean_username,
                    clean_cotizador,
                    error_message=message,
                ),
            )
        except Exception:
            log.exception("No se pudo persistir el error de sincronizacion del catalogo.")
        raise

    sync_state = _with_cache_connection(
        db_path,
        lambda con: catalog_cache_repo.get_sync_state(con, clean_username, clean_cotizador),
    )
    return CatalogSyncOutcome(
        changes=dict(changes or {}),
        sync_state=dict(sync_state or {}),
        configuration=configuration_outcome,
    )


class _CatalogSyncWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, operation: Callable[[], CatalogSyncOutcome]):
        super().__init__()
        self._operation = operation

    @Slot()
    def run(self) -> None:
        try:
            self.succeeded.emit(self._operation())
        except Exception as exc:
            log.warning("Sincronizacion de catalogo/stock fallo: %s", exc)
            self.failed.emit(str(exc or exc.__class__.__name__))


class CatalogStockSyncService(QObject):
    """Programa sincronizaciones incrementales sin bloquear el hilo de interfaz."""

    sync_started = Signal(bool)
    sync_succeeded = Signal(object)
    sync_failed = Signal(str)
    status_changed = Signal(str)

    def __init__(
        self,
        *,
        username: str,
        id_cotizador: str,
        db_path: str | None = None,
        sync_fn: Callable[..., CatalogSyncOutcome] = sync_catalog_stock_once,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self.username = str(username or "").strip()
        self.id_cotizador = str(id_cotizador or "").strip()
        self.db_path = str(db_path or resolve_db_path())
        self._sync_fn = sync_fn
        self._schedule = SyncRetrySchedule()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._begin_sync)
        self._thread: QThread | None = None
        self._worker: _CatalogSyncWorker | None = None
        self._manual_request = False
        self._manual_pending = False
        self._stopped = True

    @property
    def is_running(self) -> bool:
        return self._thread is not None

    @property
    def enabled(self) -> bool:
        return bool(self.username and self.id_cotizador)

    def start(self) -> None:
        self._stopped = False
        if not self.enabled:
            self.status_changed.emit("disabled")
            return
        self._timer.start(0)

    def stop(self) -> None:
        self._stopped = True
        self._timer.stop()
        self._manual_pending = False
        thread = self._thread
        if thread is not None and thread.isRunning():
            thread.requestInterruption()
            thread.quit()
            if not thread.wait(250):
                worker = self._worker
                try:
                    thread.finished.disconnect(self._on_thread_finished)
                except (RuntimeError, TypeError):
                    pass
                thread.setParent(None)
                if worker is not None:
                    _DETACHED_THREADS[thread] = worker

                    def _release_detached() -> None:
                        _DETACHED_THREADS.pop(thread, None)

                    thread.finished.connect(_release_detached)
                self._thread = None
                self._worker = None

    def request_sync(self, *, manual: bool = True) -> bool:
        if self._stopped or not self.enabled:
            return False
        if self.is_running:
            self._manual_pending = self._manual_pending or bool(manual)
            return False
        self._manual_request = bool(manual)
        self._timer.start(0)
        return True

    @Slot()
    def _begin_sync(self) -> None:
        if self._stopped or not self.enabled or self.is_running:
            return

        manual = self._manual_request
        self._manual_request = False
        self.sync_started.emit(manual)
        self.status_changed.emit("syncing")

        thread = QThread(self)
        worker = _CatalogSyncWorker(
            lambda: self._sync_fn(
                db_path=self.db_path,
                username=self.username,
                id_cotizador=self.id_cotizador,
            )
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(self._on_success)
        worker.failed.connect(self._on_failure)
        worker.succeeded.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        worker.succeeded.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(self._on_thread_finished)
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        self._worker = worker
        thread.start()

    @Slot(object)
    def _on_success(self, outcome: CatalogSyncOutcome) -> None:
        self.status_changed.emit("ok")
        self.sync_succeeded.emit(outcome)
        self._next_interval_ms = self._schedule.after_success()

    @Slot(str)
    def _on_failure(self, message: str) -> None:
        self.status_changed.emit("error")
        self.sync_failed.emit(str(message or "Error de sincronizacion"))
        self._next_interval_ms = self._schedule.after_error()

    @Slot()
    def _on_thread_finished(self) -> None:
        self._thread = None
        self._worker = None
        if self._stopped:
            return
        if self._manual_pending:
            self._manual_pending = False
            self._manual_request = True
            self._timer.start(0)
            return
        self._timer.start(int(getattr(self, "_next_interval_ms", 60_000)))


__all__ = [
    "CatalogStockSyncService",
    "CatalogSyncOutcome",
    "SyncRetrySchedule",
    "sync_catalog_stock_once",
]
