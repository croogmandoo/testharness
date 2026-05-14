"""Tests for harness.logging_config — defaults, rotation, harness.* routing,
and custom path overrides.

Each test uses its own tmp_path log directory and tears the handlers down
afterwards so we don't leak state between tests or interfere with caplog.
"""
from __future__ import annotations

import logging
import logging.handlers
import os

import pytest

from harness import logging_config


@pytest.fixture(autouse=True)
def _isolate_logging():
    """Snapshot/restore handlers on root + `harness` so each test starts
    clean and the rest of the suite isn't poisoned by handlers we install."""
    root = logging.getLogger()
    harness_log = logging.getLogger("harness")
    saved_root_handlers = list(root.handlers)
    saved_root_level = root.level
    saved_harness_handlers = list(harness_log.handlers)
    saved_harness_level = harness_log.level
    saved_harness_propagate = harness_log.propagate
    try:
        yield
    finally:
        for h in list(root.handlers):
            if h not in saved_root_handlers:
                try:
                    h.close()
                except Exception:
                    pass
                root.removeHandler(h)
        for h in list(harness_log.handlers):
            if h not in saved_harness_handlers:
                try:
                    h.close()
                except Exception:
                    pass
                harness_log.removeHandler(h)
        root.setLevel(saved_root_level)
        harness_log.setLevel(saved_harness_level)
        harness_log.propagate = saved_harness_propagate


def _find_rotating_handler(logger: logging.Logger):
    for h in logger.handlers:
        if isinstance(h, logging.handlers.RotatingFileHandler):
            return h
    return None


def test_configure_empty_dict_installs_defaults(tmp_path, monkeypatch):
    """`configure({})` should produce a working rotating file handler + console
    handler at the documented default location."""
    monkeypatch.chdir(tmp_path)
    resolved = logging_config.configure({})
    assert resolved["path"].endswith(os.path.join("data", "logs", "harness.log"))
    assert resolved["level"] == logging.INFO
    assert resolved["max_bytes"] == 10_000_000
    assert resolved["backup_count"] == 5
    assert resolved["console"] is True

    root = logging.getLogger()
    rfh = _find_rotating_handler(root)
    assert rfh is not None, "RotatingFileHandler not installed on root"
    assert any(
        isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.handlers.RotatingFileHandler)
        for h in root.handlers
    ), "Console StreamHandler not installed"

    # Default log dir was created.
    assert os.path.isdir(os.path.join(tmp_path, "data", "logs"))

    # Smoke-test that writing through the logger actually lands on disk.
    logging.getLogger("harness.smoke").warning("hello")
    rfh.flush()
    log_path = os.path.join(tmp_path, "data", "logs", "harness.log")
    assert os.path.exists(log_path)
    with open(log_path) as f:
        body = f.read()
    assert "hello" in body
    assert "harness.smoke" in body
    assert "[WARNING]" in body


def test_rotation_triggers_backup_file(tmp_path):
    """Writing past `max_bytes` should roll the log and create a `.1` backup."""
    log_path = tmp_path / "rot.log"
    logging_config.configure({
        "logging": {
            "path": str(log_path),
            "max_bytes": 500,       # tiny so we roll in a handful of writes
            "backup_count": 3,
            "console": False,       # keep test output clean
        }
    })
    log = logging.getLogger("harness.rotation_test")
    payload = "x" * 200
    # Each emitted record is ~250 bytes once the formatter prepends timestamps.
    for i in range(20):
        log.warning("msg %d %s", i, payload)
    rfh = _find_rotating_handler(logging.getLogger())
    assert rfh is not None
    rfh.flush()
    # At least one rotated backup must exist.
    backup = tmp_path / "rot.log.1"
    assert backup.exists(), (
        f"No backup file created — files in tmp: {sorted(os.listdir(tmp_path))}"
    )


def test_harness_alerts_logger_routes_through_configured_handlers(tmp_path):
    """A `logging.getLogger('harness.alerts')` call (the one alerts.py uses)
    must reach the configured file handler via propagation."""
    log_path = tmp_path / "alerts.log"
    logging_config.configure({
        "logging": {
            "path": str(log_path),
            "level": "INFO",
            "console": False,
        }
    })
    alerts_log = logging.getLogger("harness.alerts")
    assert alerts_log.propagate, "harness.alerts must propagate to root"
    alerts_log.warning("Slack error: boom")
    alerts_log.error("Email failed: nope")
    _find_rotating_handler(logging.getLogger()).flush()

    body = log_path.read_text()
    assert "Slack error: boom" in body
    assert "Email failed: nope" in body
    assert "harness.alerts" in body
    assert "[WARNING]" in body and "[ERROR]" in body


def test_custom_path_is_honored(tmp_path):
    """A `logging.path` override in config must be used verbatim (no surprise
    rewrites to data/logs/...)."""
    custom = tmp_path / "weird" / "place" / "my.log"
    resolved = logging_config.configure({
        "logging": {
            "path": str(custom),
            "console": False,
        }
    })
    assert resolved["path"] == str(custom)
    assert custom.parent.is_dir(), "log dir should be created on configure()"
    logging.getLogger("harness.custom_path").info("ping")
    _find_rotating_handler(logging.getLogger()).flush()
    assert custom.exists()
    assert "ping" in custom.read_text()


def test_configure_is_idempotent(tmp_path):
    """Calling configure() twice must not stack duplicate handlers."""
    logging_config.configure({"logging": {"path": str(tmp_path / "a.log"),
                                          "console": True}})
    first_count = len(logging.getLogger().handlers)
    logging_config.configure({"logging": {"path": str(tmp_path / "b.log"),
                                          "console": True}})
    second_count = len(logging.getLogger().handlers)
    assert first_count == second_count, (
        f"handlers stacked: {first_count} -> {second_count}"
    )


def test_string_level_is_parsed(tmp_path):
    resolved = logging_config.configure({
        "logging": {
            "path": str(tmp_path / "lvl.log"),
            "level": "debug",
            "console": False,
        }
    })
    assert resolved["level"] == logging.DEBUG
    rfh = _find_rotating_handler(logging.getLogger())
    assert rfh.level == logging.DEBUG
