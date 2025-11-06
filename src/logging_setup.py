# src/logging_setup.py
import os, logging, sys
from .config import LOG_DIR, LOG_LEVEL

_LEVEL_MAP = {
    "ERROR":   logging.ERROR,
    "WARNING": logging.WARNING,
    "INFO":    logging.INFO,
    "DEBUG":   logging.DEBUG,
}

def _build_formatter() -> logging.Formatter:
    fmt = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    return logging.Formatter(fmt, datefmt)

def _get_log_file() -> str:
    try: os.makedirs(LOG_DIR, exist_ok=True)
    except Exception: pass
    return os.path.join(LOG_DIR, "app.log")

def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    level = _LEVEL_MAP.get(str(LOG_LEVEL).upper(), logging.INFO)
    logger.setLevel(level)

    # File handler
    fh = logging.FileHandler(_get_log_file(), encoding="utf-8")
    fh.setLevel(level); fh.setFormatter(_build_formatter())
    # Console handler (stderr)
    ch = logging.StreamHandler(stream=sys.stderr)
    ch.setLevel(level); ch.setFormatter(_build_formatter())

    logger.addHandler(fh)
    logger.addHandler(ch)
    logger.propagate = False
    return logger
