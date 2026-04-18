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


@pytest.mark.asyncio
async def test_retry_exhausts_all_attempts_on_persistent_failure(db):
    app_def = {
        "app": "myapp", "url": "https://example.com",
        "environments": {"production": "https://example.com"},
        "_type": "yaml",
        "tests": [{"name": "flaky", "type": "api", "endpoint": "/h", "retry": 2}],
    }
    call_count = 0

    async def fake_api(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return make_result("fail", "flaky")

    with patch("harness.runner.run_api_test", new=fake_api), \
         patch("harness.runner.dispatch_alerts", new=AsyncMock()):
        await run_app(app_def, "production", "api", db, config={})

    assert call_count == 3  # 1 initial + 2 retries


@pytest.mark.asyncio
async def test_retry_stops_on_first_pass(db):
    app_def = {
        "app": "myapp", "url": "https://example.com",
        "environments": {"production": "https://example.com"},
        "_type": "yaml",
        "tests": [{"name": "flaky", "type": "api", "endpoint": "/h", "retry": 2}],
    }
    call_count = 0

    async def fake_api(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return make_result("fail" if call_count == 1 else "pass", "flaky")

    with patch("harness.runner.run_api_test", new=fake_api), \
         patch("harness.runner.dispatch_alerts", new=AsyncMock()):
        await run_app(app_def, "production", "api", db, config={})

    assert call_count == 2  # stopped after first pass


@pytest.mark.asyncio
async def test_runner_calls_webhook_on_completion(db):
    app_def = {
        "app": "myapp", "url": "https://example.com",
        "environments": {"production": "https://example.com"},
        "_type": "yaml",
        "tests": [{"name": "health", "type": "api", "endpoint": "/h"}],
    }
    webhook_calls = []

    async def fake_webhook(run_id, app, env, status, triggered_by, finished_at,
                           results, cfg):
        webhook_calls.append({"run_id": run_id, "app": app, "cfg": cfg})

    mock_result = make_result("pass", "health")
    config = {"alerts": {"webhook": {"url": "https://hook.example.com/complete"}}}

    with patch("harness.runner.run_api_test", new=AsyncMock(return_value=mock_result)), \
         patch("harness.runner.dispatch_alerts", new=AsyncMock()), \
         patch("harness.runner.dispatch_run_webhook", new=fake_webhook):
        await run_app(app_def, "production", "api", db, config=config)

    assert len(webhook_calls) == 1
    assert webhook_calls[0]["app"] == "myapp"
    assert webhook_calls[0]["cfg"]["url"] == "https://hook.example.com/complete"


@pytest.mark.asyncio
async def test_tests_run_concurrently(db):
    """Tests run in parallel: two slow tests finish faster than sequential 2×delay."""
    import asyncio, time
    from harness.runner import run_app

    app_def = {
        "app": "myapp", "url": "https://example.com",
        "environments": {"production": "https://example.com"},
        "_type": "yaml",
        "tests": [
            {"name": "slow-1", "type": "api", "endpoint": "/h"},
            {"name": "slow-2", "type": "api", "endpoint": "/h"},
        ],
    }

    async def slow_api(*args, **kwargs):
        await asyncio.sleep(0.15)
        test_name = args[4]["name"] if len(args) > 4 else kwargs["test_def"]["name"]
        return make_result("pass", test_name)

    start = time.monotonic()
    with patch("harness.runner.run_api_test", new=slow_api), \
         patch("harness.runner.dispatch_alerts", new=AsyncMock()), \
         patch("harness.runner.dispatch_run_webhook", new=AsyncMock()):
        await run_app(app_def, "production", "api", db, config={})
    elapsed = time.monotonic() - start

    # If sequential: >= 0.30 s. Parallel: < 0.25 s.
    assert elapsed < 0.25, f"Tests ran sequentially (elapsed {elapsed:.3f}s)"


@pytest.mark.asyncio
async def test_screenshot_diff_appends_fail_step_when_over_threshold(db, tmp_path):
    """If screenshot changes by more than threshold, a fail step is added."""
    from PIL import Image

    screenshots_dir = tmp_path / "data" / "screenshots"
    screenshots_dir.mkdir(parents=True)

    app_def = {
        "app": "myapp", "url": "https://example.com",
        "environments": {"production": "https://example.com"},
        "_type": "yaml",
        "tests": [{"name": "visual-test", "type": "browser", "steps": []}],
    }

    # Previous screenshot: white 10×10
    prev_rel = "myapp/production/run_prev/visual-test.png"
    prev_path = screenshots_dir / "myapp" / "production" / "run_prev"
    prev_path.mkdir(parents=True)
    Image.new("RGB", (10, 10), color=(255, 255, 255)).save(
        str(prev_path / "visual-test.png")
    )

    from harness.models import Run, TestResult
    prev_run = Run(app="myapp", environment="production",
                   triggered_by="test", status="complete", id="run_prev")
    db.insert_run(prev_run)
    prev_tr = TestResult(run_id="run_prev", app="myapp", environment="production",
                         test_name="visual-test", status="pass",
                         screenshot=prev_rel,
                         finished_at="2026-04-17T00:00:00Z")
    db.insert_test_result(prev_tr)

    curr_rel = "myapp/production/run_curr/visual-test.png"

    async def fake_browser(run_id, app, env, base_url, test_def, **kwargs):
        curr_path = screenshots_dir / "myapp" / "production" / "run_curr"
        curr_path.mkdir(parents=True)
        Image.new("RGB", (10, 10), color=(0, 0, 0)).save(
            str(curr_path / "visual-test.png")
        )
        result = TestResult(run_id=run_id, app=app, environment=env,
                            test_name="visual-test", status="pass")
        result.screenshot = curr_rel
        return result

    config = {
        "browser": {"screenshot_diff_threshold": 0.01},
    }
    screenshots_path = str(screenshots_dir)

    with patch("harness.runner.run_browser_test", new=fake_browser), \
         patch("harness.runner.dispatch_alerts", new=AsyncMock()), \
         patch("harness.runner.dispatch_run_webhook", new=AsyncMock()), \
         patch("harness.runner.SCREENSHOTS_DIR", screenshots_path):
        await run_app(app_def, "production", "api", db, config=config)

    results = db.get_results_for_run(
        db.get_recent_runs("myapp", "production")[0]["id"]
    )
    assert len(results) == 1
    import json
    step_log = json.loads(results[0]["step_log"] or "[]")
    diff_steps = [s for s in step_log if "diff" in s.get("step", "").lower()]
    assert len(diff_steps) == 1
    assert diff_steps[0]["status"] == "fail"
