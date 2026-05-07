"""Tier-2 integration: sequential test execution with extract -> $VAR chaining
and per-test SLO thresholds enforced by the runner."""
import pytest
from pytest_httpx import HTTPXMock

from harness.db import Database
from harness.models import StepResult, TestResult
from harness.runner import _apply_slo_threshold, run_app


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "chain.db"))
    d.init_schema()
    return d


# ── _apply_slo_threshold unit tests ────────────────────────────────────────

def test_slo_passes_when_under_threshold():
    tr = TestResult(run_id="r", app="a", environment="e", test_name="t",
                    status="pass", duration_ms=500)
    _apply_slo_threshold({"max_duration_ms": 1000}, tr)
    assert tr.status == "pass"
    assert tr.error_msg is None


def test_slo_fails_passing_test_when_over_threshold():
    tr = TestResult(run_id="r", app="a", environment="e", test_name="t",
                    status="pass", duration_ms=2500)
    _apply_slo_threshold({"max_duration_ms": 1000}, tr)
    assert tr.status == "fail"
    assert "SLO violation" in (tr.error_msg or "")
    assert "2500" in (tr.error_msg or "")
    assert "1000" in (tr.error_msg or "")
    # Should append a step_log entry recording the SLO check
    assert any("SLO check" in s.step for s in tr.step_log)


def test_slo_does_not_touch_failing_test():
    """An already-failed test should keep its original error, not be overwritten."""
    tr = TestResult(run_id="r", app="a", environment="e", test_name="t",
                    status="fail", duration_ms=2500, error_msg="original failure")
    _apply_slo_threshold({"max_duration_ms": 1000}, tr)
    assert tr.error_msg == "original failure"


def test_slo_no_op_when_threshold_unset():
    tr = TestResult(run_id="r", app="a", environment="e", test_name="t",
                    status="pass", duration_ms=99999)
    _apply_slo_threshold({}, tr)
    assert tr.status == "pass"


def test_slo_handles_none_duration():
    tr = TestResult(run_id="r", app="a", environment="e", test_name="t",
                    status="pass", duration_ms=None)
    _apply_slo_threshold({"max_duration_ms": 1000}, tr)
    assert tr.status == "pass"


# ── Variable chaining via run_app ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_chain_login_then_use_token(db, httpx_mock: HTTPXMock):
    """End-to-end: first test extracts AUTH_TOKEN; second test uses it."""
    httpx_mock.add_response(
        url="https://example.com/auth/login", method="POST",
        status_code=200, json={"token": "tok-xyz"},
    )
    httpx_mock.add_response(
        url="https://example.com/me", method="GET",
        status_code=200, json={"username": "alice"},
    )
    app_def = {
        "app": "ChainApp",
        "environments": {"production": "https://example.com"},
        "tests": [
            {
                "name": "Login",
                "type": "api",
                "endpoint": "/auth/login",
                "method": "POST",
                "json": {"u": "alice", "p": "pw"},
                "extract": {"AUTH_TOKEN": "$.token"},
            },
            {
                "name": "Get me",
                "type": "api",
                "endpoint": "/me",
                "method": "GET",
                "headers": {"Authorization": "Bearer $AUTH_TOKEN"},
                "expect_json": {"username": "alice"},
            },
        ],
    }
    config = {"environments": {"production": {"label": "Production"}}}
    run_id = await run_app(app_def, "production", "test", db, config)

    # Both tests should pass and the second's request must carry the token
    results = db.get_results_for_run(run_id)
    assert len(results) == 2
    by_name = {r["test_name"]: r for r in results}
    assert by_name["Login"]["status"] == "pass"
    assert by_name["Get me"]["status"] == "pass"

    # Verify the auth header was actually sent
    me_request = next(
        r for r in httpx_mock.get_requests()
        if r.url.path == "/me"
    )
    assert me_request.headers.get("Authorization") == "Bearer tok-xyz"


@pytest.mark.asyncio
async def test_chain_failure_propagates_when_extract_misses(db, httpx_mock: HTTPXMock):
    """If the first test's extract path doesn't match, mark it failed and
    the second test still runs (each test is independent at the run level)."""
    httpx_mock.add_response(
        url="https://example.com/auth/login", method="POST",
        status_code=200, json={"no_token_here": True},
    )
    # Optional: this should NOT be requested — the resolver fails before the
    # second test's HTTP call.  Without is_optional pytest_httpx errors at
    # teardown over the unmatched mock.
    httpx_mock.add_response(
        url="https://example.com/me",
        status_code=200, json={"username": "alice"},
        is_optional=True,
    )
    app_def = {
        "app": "ChainApp",
        "environments": {"production": "https://example.com"},
        "tests": [
            {
                "name": "Login",
                "type": "api",
                "endpoint": "/auth/login",
                "method": "POST",
                "extract": {"AUTH_TOKEN": "$.token"},
            },
            {
                "name": "Get me",
                "type": "api",
                "endpoint": "/me",
                "headers": {"Authorization": "Bearer $AUTH_TOKEN"},
            },
        ],
    }
    config = {"environments": {"production": {"label": "Production"}}}
    run_id = await run_app(app_def, "production", "test", db, config)
    results = {r["test_name"]: r for r in db.get_results_for_run(run_id)}
    assert results["Login"]["status"] == "fail"
    # The second test's resolver should fail with strict=True since AUTH_TOKEN
    # was never captured.  The runner records that as an error result.
    assert results["Get me"]["status"] == "error"


@pytest.mark.asyncio
async def test_no_extract_keeps_parallel_path(db, httpx_mock: HTTPXMock):
    """Without any `extract:`, tests still run (and we don't break the
    existing behaviour where they go through asyncio.gather)."""
    httpx_mock.add_response(
        url="https://example.com/a", status_code=200, json={"ok": True},
    )
    httpx_mock.add_response(
        url="https://example.com/b", status_code=200, json={"ok": True},
    )
    app_def = {
        "app": "ParallelApp",
        "environments": {"production": "https://example.com"},
        "tests": [
            {"name": "A", "type": "api", "endpoint": "/a"},
            {"name": "B", "type": "api", "endpoint": "/b"},
        ],
    }
    config = {"environments": {"production": {"label": "Production"}}}
    run_id = await run_app(app_def, "production", "test", db, config)
    results = db.get_results_for_run(run_id)
    assert len(results) == 2
    assert all(r["status"] == "pass" for r in results)


@pytest.mark.asyncio
async def test_max_duration_ms_fails_slow_passing_test(db, httpx_mock: HTTPXMock):
    """A test that returns 200 but takes too long must be marked failed."""
    import asyncio as _asyncio

    async def _slow_response(request):
        await _asyncio.sleep(0.05)  # 50ms
        from httpx import Response as _R
        return _R(200, json={"ok": True})

    httpx_mock.add_callback(_slow_response, url="https://example.com/slow")

    app_def = {
        "app": "SLOApp",
        "environments": {"production": "https://example.com"},
        "tests": [{
            "name": "Slow endpoint",
            "type": "api",
            "endpoint": "/slow",
            "max_duration_ms": 1,  # extremely tight — guaranteed violation
        }],
    }
    config = {"environments": {"production": {"label": "Production"}}}
    run_id = await run_app(app_def, "production", "test", db, config)
    results = db.get_results_for_run(run_id)
    assert results[0]["status"] == "fail"
    assert "SLO violation" in (results[0]["error_msg"] or "")
