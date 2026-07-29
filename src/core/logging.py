from __future__ import annotations

import logging
from typing import Any

_CONFIGURED = False


def setup_logging(log_level: str = "INFO") -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    logging.basicConfig(
        level=log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


class LoggerAdapter:
    """Thin adapter so call sites can use logger.info("event", extra={...})."""

    def __init__(self, logger: logging.Logger, context: dict[str, Any] | None = None) -> None:
        self._logger = logger
        self._context = context or {}

    def _log(self, level: int, msg: str, extra: dict[str, Any] | None = None) -> None:
        merged = {**self._context, **(extra or {})}
        self._logger.log(level, msg, extra=merged)

    def debug(self, msg: str, extra: dict[str, Any] | None = None) -> None:
        self._log(logging.DEBUG, msg, extra)

    def info(self, msg: str, extra: dict[str, Any] | None = None) -> None:
        self._log(logging.INFO, msg, extra)

    def warning(self, msg: str, extra: dict[str, Any] | None = None) -> None:
        self._log(logging.WARNING, msg, extra)

    def error(self, msg: str, extra: dict[str, Any] | None = None) -> None:
        self._log(logging.ERROR, msg, extra)

    def exception(self, msg: str, extra: dict[str, Any] | None = None) -> None:
        merged = {**self._context, **(extra or {})}
        self._logger.exception(msg, extra=merged)
