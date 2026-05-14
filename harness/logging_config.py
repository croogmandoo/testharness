"""Structured logging setup for the harness.

Single entrypoint — `configure(config)` — wires up the root logger and the
`harness.*` namespace with:

  * a `logging.handlers.RotatingFileHandler` (path / size / backups / level all
    config-driven)
  * a `logging.StreamHandler` so systemd/Docker journaling still works

It is idempotent: calling it twice replaces the previously installed harness
handlers, which keeps repeated `create_app()` calls (the test suite does this
a lot) from stacking duplicate handlers on the root logger.

Sensible defaults match the spec in
``docs/superpowers/specs/2026-04-22-logging-csrf-db-indexes-design.md``:

    path          data/logs/harness.log
    level         INFO
    max_bytes     10_000_000   (~10 MB)
    backup_count  5
    format        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

Config shape (all keys optional; any subset is fine)::

    logging:
      level: INFO
      path: data/logs/harness.log
      max_bytes: 10000000
      backup_count: 5
      console: true
      console_level: INFO          # defaults to `level` when omitted
"""
from __future__ import annotations

import logging
import logging.handlers
import os
from typing import Any

# Sentinel attribute used to identify handlers we installed, so re-calling
# `configure()` can replace ours without disturbing handlers added by tests or
# third-party libraries (pytest's caplog, uvicorn, etc.).
_HANDLER_TAG = "_harness_logging_handler"

DEFAULT_PATH = os.path.join("data", "logs", "harness.log")
DEFAULT_LEVEL = "INFO"
DEFAULT_MAX_BYTES = 10_000_000
DEFAULT_BACKUP_COUNT = 5
DEFAULT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def _coerce_level(value: Any, fallback: int) -> int:
    """Accept level as either a string (`"DEBUG"`) or an int (`10`)."""
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return getattr(logging, value.strip().upper(), fallback)
    return fallback


def _remove_existing_harness_handlers(logger: logging.Logger) -> None:
    for h in list(logger.handlers):
        if getattr(h, _HANDLER_TAG, False):
            try:
                h.close()
            except Exception:
                pass
            logger.removeHandler(h)


def configure(config: dict | None = None) -> dict:
    """Install rotating-file + console handlers from `config["logging"]`.

    Returns the resolved settings dict (useful for tests and ops introspection).
    Safe to call multiple times — previous harness handlers are removed first.
    """
    cfg = (config or {}).get("logging") or {}

    path = cfg.get("path") or DEFAULT_PATH
    level = _coerce_level(cfg.get("level", DEFAULT_LEVEL), logging.INFO)
    max_bytes = int(cfg.get("max_bytes", DEFAULT_MAX_BYTES))
    backup_count = int(cfg.get("backup_count", DEFAULT_BACKUP_COUNT))
    fmt_str = cfg.get("format", DEFAULT_FORMAT)
    console_enabled = bool(cfg.get("console", True))
    console_level = _coerce_level(cfg.get("console_level", level), level)

    formatter = logging.Formatter(fmt_str)

    # Ensure the log dir exists before we hand the path to RotatingFileHandler;
    # tolerating EACCES here would just defer the same error to first write.
    log_dir = os.path.dirname(os.path.abspath(path))
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    root = logging.getLogger()
    harness_logger = logging.getLogger("harness")

    # Idempotent: drop anything we installed before, leave everyone else alone.
    _remove_existing_harness_handlers(root)
    _remove_existing_harness_handlers(harness_logger)

    file_handler = logging.handlers.RotatingFileHandler(
        path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
        delay=True,  # don't open the file until first write
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    setattr(file_handler, _HANDLER_TAG, True)
    root.addHandler(file_handler)

    if console_enabled:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(console_level)
        console_handler.setFormatter(formatter)
        setattr(console_handler, _HANDLER_TAG, True)
        root.addHandler(console_handler)

    # Root threshold = the most permissive of our handlers, so nothing gets
    # filtered out by the root before reaching them.
    effective_root_level = min(level, console_level) if console_enabled else level
    if root.level == logging.NOTSET or root.level > effective_root_level:
        root.setLevel(effective_root_level)

    # `harness.*` loggers propagate to root by default, which is what we want
    # — no need to attach handlers to the harness namespace itself. Just make
    # sure propagation is on and the level isn't blocking anything.
    harness_logger.setLevel(effective_root_level)
    harness_logger.propagate = True

    return {
        "path": path,
        "level": level,
        "max_bytes": max_bytes,
        "backup_count": backup_count,
        "console": console_enabled,
        "console_level": console_level,
        "format": fmt_str,
    }
