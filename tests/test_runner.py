import pytest
from unittest.mock import AsyncMock, patch
from harness.runner import run_app, determine_alert, AlertType
from harness.models import TestResult, AppState
from harness.db import Database

@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.init_schema()
    return d

def make_result(status: str, test_name: str = "login") -> TestResult:
    return TestResult(run_id="r1", app="myapp", environment="production",
                      test_name=test_name, status=status)

def test_determine_alert_unknown_to_failing():
    assert determine_alert("unknown", "fail") == AlertType.FAIL

def test_determine_alert_passing_to_failing():
    assert determine_alert("passing", "fail") == AlertType.FAIL

def test_determine_alert_failing_to_passing():
    assert determine_alert("failing", "pass") == AlertType.RESOLVE

def test_determine_alert_no_change_passing():
    assert determine_alert("passing", "pass") is None

def test_determine_alert_no_change_failing():
    assert determine_alert("failing", "fail") is None

@pytest.mark.asyncio
async def test_run_app_updates_db_and_state(db):
    app_def = {
        "app": "myapp", "url": "https://example.com",
        "environments": {"production": "https://example.com"},
        "_type": "yaml",
        "tests": [{"name": "Health check", "type": "api", "endpoint": "/health",
                   "method": "GET", "expect_status": 200}]
    }
    mock_result = make_result("pass", "Health check")
    with patch("harness.runner.run_api_test", new=AsyncMock(return_value=mock_result)), \
         patch("harness.runner.dispatch_alerts", new=AsyncMock()):
        run_id = await run_app(app_def, "production", "api", db, config={})
    run = db.get_run(run_id)
    assert run["status"] == "complete"
    state = db.get_app_state("myapp", "production", "Health check")
    assert state["state"] == "passing"


@pytest.mark.asyncio
async def test_runner_passes_per_test_timeout_to_browser(db):
    app_def = {
        "app": "myapp", "url": "https://example.com",
        "environments": {"production": "https://example.com"},
        "_type": "yaml",
        "tests": [{"name": "slow-browser", "type": "browser",
                   "timeout_ms": 60000, "steps": []}],
    }
    captured = {}

    async def fake_browser(run_id, app, env, base_url, test_def,
                           headless=True, timeout_ms=30000):
        captured["timeout_ms"] = timeout_ms
        return make_result("pass", "slow-browser")

    with patch("harness.runner.run_browser_test", new=fake_browser), \
         patch("harness.runner.dispatch_alerts", new=AsyncMock()):
        await run_app(app_def, "production", "api", db, config={})

    assert captured["timeout_ms"] == 60000
