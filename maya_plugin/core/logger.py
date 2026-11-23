"""Plugin logging: rotating JSON-lines file + forwarding to the UI activity log."""
from __future__ import annotations

import json
import logging
import logging.handlers
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

ROOT_LOGGER_NAME = "maya_ai_toolkit"
_HANDLER_TAG = "_mait_handler"
_RESERVED = set(vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys()) | {"message", "asctime"}


def get_logger(name: str = "") -> logging.Logger:
    """Return a child of the toolkit logger, e.g. ``get_logger("connection")``."""
    return logging.getLogger(ROOT_LOGGER_NAME + ("." + name if name else ""))


class JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


class CallbackHandler(logging.Handler):
    """Forwards records to a callable (used to feed the UI activity log via the event bus).

    The callback may be invoked from any thread; the UI marshals it onto the main thread.
    """

    def __init__(self, callback: Callable[[Dict[str, Any]], None], level: int = logging.INFO) -> None:
        super().__init__(level)
        self._callback = callback

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._callback({"ts": record.created, "level": record.levelname, "logger": record.name,
                            "message": record.getMessage()})
        except Exception:  # never let UI forwarding break logging
            self.handleError(record)


def setup_logging(log_dir: Optional[Path], level: str = "INFO",
                  ui_callback: Optional[Callable[[Dict[str, Any]], None]] = None) -> logging.Logger:
    """Configure the toolkit logger. Safe to call repeatedly (e.g. after a module reload)."""
    logger = logging.getLogger(ROOT_LOGGER_NAME)
    logger.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    for handler in list(logger.handlers):
        if getattr(handler, _HANDLER_TAG, False):
            logger.removeHandler(handler)
            handler.close()
    if log_dir is not None:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                str(log_dir / "maya_plugin.jsonl"), maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
            file_handler.setFormatter(JsonLineFormatter())
            setattr(file_handler, _HANDLER_TAG, True)
            logger.addHandler(file_handler)
        except OSError:
            pass  # logging must never prevent the plugin from opening
    if ui_callback is not None:
        cb_handler = CallbackHandler(ui_callback)
        setattr(cb_handler, _HANDLER_TAG, True)
        logger.addHandler(cb_handler)
    return logger
