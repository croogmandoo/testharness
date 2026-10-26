"""Unit and integration tests for Core Web Vitals and frontend performance metrics in browser testing."""

import os
import pytest
from unittest.mock import AsyncMock, MagicMock

from harness.browser import (
    extract_web_vitals,
    evaluate_performance_thresholds,
    execute_step,
    run_browser_test,
)
from harness.models import StepResult


def test_evaluate_performance_thresholds_all_pass():
    metrics = {
        "ttfb": 120,
        "fcp": 450,
        "lcp": 1100,
        "cls": 0.015,
        "load_time": 1300,
    }
    thresholds = {
        "max_ttfb_ms": 200,
        "max_fcp_ms": 1000,
        "max_lcp_ms": 2500,
        "max_cls": 0.1,
        "max_load_ms": 3000,
    }
    summary, err = evaluate_performance_thresholds(metrics, thresholds)
    assert err is None
    assert "TTFB=120ms" in summary
    assert "FCP=450ms" in summary
    assert "LCP=1100ms" in summary
    assert "CLS=0.015" in summary
    assert "Load=1300ms" in summary


def test_evaluate_performance_thresholds_violations():
    metrics = {
        "ttfb": 850,
        "fcp": 2200,
        "lcp": 4200,
        "cls": 0.25,
        "load_time": 6000,
    }
    thresholds = {
        "max_ttfb_ms": 500,
        "max_fcp_ms": 1800,
        "max_lcp_ms": 2500,
        "max_cls": 0.1,
        "max_load_ms": 4000,
    }
    summary, err = evaluate_performance_thresholds(metrics, thresholds)
    assert err is not None
    assert "LCP 4200ms exceeded max_lcp_ms 2500ms" in err
    assert "FCP 2200ms exceeded max_fcp_ms 1800ms" in err
    assert "TTFB 850ms exceeded max_ttfb_ms 500ms" in err
    assert "CLS 0.25 exceeded max_cls 0.1" in err
    assert "Load time 6000ms exceeded max_load_ms 4000ms" in err


def test_evaluate_performance_thresholds_empty_metrics():
    summary, err = evaluate_performance_thresholds({}, {"max_lcp_ms": 2500})
    assert err is None
    assert "metrics unavailable" in summary


@pytest.mark.asyncio
async def test_extract_web_vitals_from_page():
    mock_page = AsyncMock()
    mock_page.evaluate.return_value = {
        "ttfb": 95,
        "fcp": 320,
        "lcp": 780,
        "cls": 0.005,
        "load_time": 950,
    }
    metrics = await extract_web_vitals(mock_page)
    assert metrics["lcp"] == 780
    assert metrics["ttfb"] == 95


@pytest.mark.asyncio
async def test_extract_web_vitals_handles_page_error():
    mock_page = AsyncMock()
    mock_page.evaluate.side_effect = RuntimeError("Context destroyed")
    metrics = await extract_web_vitals(mock_page)
    assert metrics == {}


@pytest.mark.asyncio
async def test_execute_step_assert_performance_passing():
    mock_page = AsyncMock()
    mock_page.evaluate.return_value = {
        "ttfb": 100,
        "fcp": 500,
        "lcp": 1200,
        "cls": 0.02,
        "load_time": 1400,
    }
    step = {
        "assert_performance": {
            "max_lcp_ms": 2500,
            "max_fcp_ms": 1000,
        }
    }
    res = await execute_step(mock_page, step, "http://localhost:8000")
    assert res.status == "pass"
    assert "LCP=1200ms" in res.step
    assert res.error is None


@pytest.mark.asyncio
async def test_execute_step_assert_performance_failing():
    mock_page = AsyncMock()
    mock_page.evaluate.return_value = {
        "ttfb": 100,
        "fcp": 500,
        "lcp": 3200,
        "cls": 0.02,
        "load_time": 1400,
    }
    step = {
        "assert_performance": {
            "max_lcp_ms": 2500,
        }
    }
    res = await execute_step(mock_page, step, "http://localhost:8000")
    assert res.status == "fail"
    assert "LCP 3200ms exceeded max_lcp_ms 2500ms" in res.error


@pytest.mark.asyncio
async def test_run_browser_test_with_top_level_thresholds_passing(monkeypatch):
    fake_browser = AsyncMock()
    fake_page = AsyncMock()
    fake_page.set_default_timeout = MagicMock()
    fake_page.url = "http://example.com"
    fake_page.evaluate.return_value = {
        "ttfb": 80,
        "fcp": 290,
        "lcp": 950,
        "cls": 0.01,
        "load_time": 1100,
    }
    fake_browser.new_page.return_value = fake_page
    fake_chromium = AsyncMock()
    fake_chromium.launch.return_value = fake_browser

    fake_pw = AsyncMock()
    fake_pw.chromium = fake_chromium

    class FakeAsyncPlaywright:
        async def __aenter__(self):
            return fake_pw
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    monkeypatch.setattr("harness.browser.async_playwright", lambda: FakeAsyncPlaywright())

    test_def = {
        "name": "Performance Happy Path",
        "max_lcp_ms": 2000,
        "max_fcp_ms": 1000,
        "steps": [
            {"navigate": "http://example.com"},
        ],
    }

    result = await run_browser_test(
        run_id="run-perf-1",
        app="perf-app",
        environment="prod",
        base_url="http://example.com",
        test_def=test_def,
    )

    assert result.status == "pass"
    assert len(result.step_log) == 2  # navigate step + vitals step
    vitals_step = result.step_log[1]
    assert vitals_step.status == "pass"
    assert "LCP=950ms" in vitals_step.step


@pytest.mark.asyncio
async def test_run_browser_test_with_top_level_thresholds_failing(monkeypatch, tmp_path):
    fake_browser = AsyncMock()
    fake_page = AsyncMock()
    fake_page.set_default_timeout = MagicMock()
    fake_page.url = "http://example.com"
    fake_page.evaluate.return_value = {
        "ttfb": 120,
        "fcp": 600,
        "lcp": 3500,
        "cls": 0.03,
        "load_time": 3800,
    }
    fake_browser.new_page.return_value = fake_page
    fake_chromium = AsyncMock()
    fake_chromium.launch.return_value = fake_browser

    fake_pw = AsyncMock()
    fake_pw.chromium = fake_chromium

    class FakeAsyncPlaywright:
        async def __aenter__(self):
            return fake_pw
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    monkeypatch.setattr("harness.browser.async_playwright", lambda: FakeAsyncPlaywright())

    test_def = {
        "name": "Slow Landing Page",
        "max_lcp_ms": 2500,
        "steps": [
            {"navigate": "http://example.com"},
        ],
    }

    result = await run_browser_test(
        run_id="run-perf-2",
        app="perf-app",
        environment="prod",
        base_url="http://example.com",
        test_def=test_def,
        screenshot_dir=str(tmp_path / "screenshots"),
    )

    assert result.status == "fail"
    assert "LCP 3500ms exceeded max_lcp_ms 2500ms" in result.error_msg
    assert result.screenshot is not None
