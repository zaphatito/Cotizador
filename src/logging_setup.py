# src/logging_setup.py
from __future__ import annotations

import logging
import os
import sys

from .config import LOG_DIR as _DEFAULT_LOG_DIR
from .config import LOG_LEVEL as _DEFAULT_LOG_LEVEL

_LEVEL_MAP = {
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
}

_LOG_DIR = ""
_LOG_LEVEL_NAME = "INFO"
_LOG_LEVEL = logging.INFO
_KNOWN_LOGGERS: set[str] = set()


def _build_formatter() -> logging.Formatter:
    fmt = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    return logging.Formatter(fmt, datefmt)


def _normalize_level_name(level) -> str:
    name = str(level or "").strip().upper()
    return name if name in _LEVEL_MAP else "INFO"


def _resolve_log_dir(log_dir: str | None = None) -> str:
    candidate = str(log_dir or "").strip()
    if not candidate:
        candidate = str(os.environ.get("LOG_DIR") or "").strip()
    if not candidate:
        candidate = str(_DEFAULT_LOG_DIR or "").strip()
    if not candidate:
        candidate = "."
    try:
        os.makedirs(candidate, exist_ok=True)
    except Exception:
        pass
    return candidate


def _resolve_level(level=None) -> tuple[str, int]:
    raw = level
    if raw is None:
        raw = os.environ.get("LOG_LEVEL", "")
    if not str(raw or "").strip():
        raw = _DEFAULT_LOG_LEVEL
    name = _normalize_level_name(raw)
    return name, _LEVEL_MAP[name]


def _is_api_logger(name: str) -> bool:
    lname = str(name or "").strip().lower()
    return lname == "api" or lname.startswith("src.api")


def _get_log_file(name: str) -> str:
    filename = "api.log" if _is_api_logger(name) else "app.log"
    return os.path.join(_LOG_DIR, filename)


def _is_ours(handler: logging.Handler) -> bool:
    return bool(getattr(handler, "_cotizador_handler", False))


def _configure_logger(logger: logging.Logger, name: str, *, replace_ours: bool = True) -> None:
    if replace_ours:
        for h in list(logger.handlers):
            if _is_ours(h):
                try:
                    logger.removeHandler(h)
                    h.close()
                except Exception:
                    pass

    logger.setLevel(_LOG_LEVEL)

    has_ours = any(_is_ours(h) for h in logger.handlers)
    if not has_ours:
        formatter = _build_formatter()

        try:
            fh = logging.FileHandler(_get_log_file(name), encoding="utf-8")
            fh._cotizador_handler = True
            fh.setLevel(_LOG_LEVEL)
            fh.setFormatter(formatter)
            logger.addHandler(fh)
        except Exception:
            # If file logging fails, keep console logging alive.
            pass

        ch = logging.StreamHandler(stream=sys.stderr)
        ch._cotizador_handler = True
        ch.setLevel(_LOG_LEVEL)
        ch.setFormatter(formatter)
        logger.addHandler(ch)

    logger.propagate = False


def init_logging(*, level=None, log_dir: str | None = None, force: bool = False) -> dict[str, str]:
    """
    Backward-compatible logging initializer used by tests and runtime.
    - `level`: DEBUG/INFO/WARNING/ERROR
    - `log_dir`: output directory for app.log/api.log
    - `force`: reconfigure known loggers even if values did not change
    """
    global _LOG_DIR, _LOG_LEVEL_NAME, _LOG_LEVEL

    new_dir = _resolve_log_dir(log_dir)
    new_level_name, new_level = _resolve_level(level)

    changed = (new_dir != _LOG_DIR) or (new_level != _LOG_LEVEL)
    _LOG_DIR = new_dir
    _LOG_LEVEL_NAME = new_level_name
    _LOG_LEVEL = new_level

    if changed or force:
        for name in list(_KNOWN_LOGGERS):
            try:
                _configure_logger(logging.getLogger(name), name, replace_ours=True)
            except Exception:
                pass

    return {"log_dir": _LOG_DIR, "level": _LOG_LEVEL_NAME}


def get_logger(name: str) -> logging.Logger:
    if not _LOG_DIR:
        init_logging()

    logger = logging.getLogger(name)
    _KNOWN_LOGGERS.add(name)
    _configure_logger(logger, name, replace_ours=False)
    return logger
