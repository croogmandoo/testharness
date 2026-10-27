from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock
from harness.url_recorder import URLRecorder, record_url_steps
from harness.browser import run_browser_test
from harness.models import StepResult


class MockFrame:
    def __init__(self, url: str):
        self.url = url


class MockPage:
    def __init__(self):
        self.main_frame = MockFrame("https://example.com")
        self._handlers: dict[str, list] = {}
        self.goto = AsyncMock()
        self.set_default_timeout = MagicMock()
        self.screenshot = AsyncMock()
        self.evaluate = AsyncMock(return_value={})

    def on(self, event: str, handler):
        self._handlers.setdefault(event, []).append(handler)

    def remove_listener(self, event: str, handler):
        if event in self._handlers and handler in self._handlers[event]:
            self._handlers[event].remove(handler)

    def off(self, event: str, handler):
        self.remove_listener(event, handler)

    def emit_navigation(self, frame: MockFrame):
        for handler in self._handlers.get("framenavigated", []):
            handler(frame)

    def navigate_main(self, url: str):
        self.main_frame.url = url
        self.emit_navigation(self.main_frame)


@pytest.mark.asyncio
async def test_url_recorder_start_stop_idempotent():
    page = MockPage()
    recorder = URLRecorder(page, "https://example.com")

    # stop before start
    assert await recorder.stop() == []

    await recorder.start()
    await recorder.start()  # second start is no-op
    assert len(page._handlers.get("framenavigated", [])) == 1

    steps = await recorder.stop()
    assert steps == []
    # handler removed
    assert len(page._handlers.get("framenavigated", [])) == 0

    # stopping again is safe
    assert await recorder.stop() == []


@pytest.mark.asyncio
async def test_url_recorder_supports_python_playwright_remove_listener_only():
    # Real Python Playwright Page has remove_listener but NOT off
    class PlaywrightStylePage:
        def __init__(self):
            self.main_frame = MockFrame("https://example.com")
            self._handlers: dict[str, list] = {}

        def on(self, event: str, handler):
            self._handlers.setdefault(event, []).append(handler)

        def remove_listener(self, event: str, handler):
            if event in self._handlers and handler in self._handlers[event]:
                self._handlers[event].remove(handler)

    page = PlaywrightStylePage()
    assert not hasattr(page, "off")
    assert hasattr(page, "remove_listener")

    recorder = URLRecorder(page, "https://example.com")
    await recorder.start()
    assert len(page._handlers["framenavigated"]) == 1

    steps = await recorder.stop()
    assert steps == []
    assert len(page._handlers["framenavigated"]) == 0


@pytest.mark.asyncio
async def test_url_recorder_captures_and_deduplicates_consecutive_urls():
    page = MockPage()
    recorder = URLRecorder(page, "https://example.com")
    await recorder.start()

    # Simulate navigations
    page.navigate_main("https://example.com/dashboard")
    page.navigate_main("https://example.com/dashboard")  # consecutive duplicate
    page.navigate_main("https://example.com/settings")
    page.navigate_main("https://example.com/dashboard")  # non-consecutive kept

    steps = await recorder.stop()
    assert steps == [
        {"navigate": "/dashboard"},
        {"navigate": "/settings"},
        {"navigate": "/dashboard"},
    ]


@pytest.mark.asyncio
async def test_url_recorder_preserves_external_urls_and_normalizes_relative():
    page = MockPage()
    recorder = URLRecorder(page, "https://example.com")
    await recorder.start()

    page.navigate_main("https://example.com/login")
    page.navigate_main("https://idp.external.com/auth?redirect=1")
    page.navigate_main("https://example.com/welcome")

    steps = await recorder.stop()
    assert steps == [
        {"navigate": "/login"},
        {"navigate": "https://idp.external.com/auth?redirect=1"},
        {"navigate": "/welcome"},
    ]


@pytest.mark.asyncio
async def test_url_recorder_ignores_subframe_navigations():
    page = MockPage()
    recorder = URLRecorder(page, "https://example.com")
    await recorder.start()

    sub_frame = MockFrame("https://example.com/iframe-ad")
    page.emit_navigation(sub_frame)  # should be ignored since not main_frame

    page.emit_navigation(page.main_frame)  # main frame navigation

    steps = await recorder.stop()
    assert steps == [{"navigate": "/"}]


@pytest.mark.asyncio
async def test_record_url_steps_helper():
    page = MockPage()
    # convenience helper starts, waits a tick, and stops
    steps = await record_url_steps(page, "https://example.com")
    assert steps == []


@pytest.mark.asyncio
async def test_run_browser_test_with_record_url_steps(monkeypatch):
    fake_page = MockPage()

    async def fake_goto(url):
        fake_page.main_frame.url = url
        fake_page.emit_navigation(fake_page.main_frame)

    fake_page.goto.side_effect = fake_goto

    fake_browser = AsyncMock()
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
        "name": "Guided Journey Test",
        "steps": [
            {"navigate": "/login"},
            {"navigate": "/dashboard"},
        ],
    }

    result = await run_browser_test(
        run_id="run-1",
        app="demo",
        environment="prod",
        base_url="https://example.com",
        test_def=test_def,
        record_url_steps=True,
    )

    assert result.status == "pass"
    # The recorded navigation steps should be prepended
    steps_recorded = [s.step for s in result.step_log if s.step.startswith("navigate /")]
    assert "navigate /login" in steps_recorded
    assert "navigate /dashboard" in steps_recorded
    # Verify prepended recorded step duration is 0
    assert result.step_log[0].duration_ms == 0
    assert result.step_log[0].step == "navigate /login"


@pytest.mark.asyncio
async def test_run_browser_test_without_record_url_steps(monkeypatch):
    fake_page = MockPage()
    fake_browser = AsyncMock()
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
        "name": "Standard Test",
        "steps": [
            {"navigate": "/login"},
        ],
    }

    result = await run_browser_test(
        run_id="run-2",
        app="demo",
        environment="prod",
        base_url="https://example.com",
        test_def=test_def,
        record_url_steps=False,
    )

    assert result.status == "pass"
    assert len(result.step_log) == 1
    assert result.step_log[0].step == "navigate /login"


@pytest.mark.asyncio
async def test_run_browser_test_with_empty_steps_defaults_to_root_when_recording(monkeypatch):
    fake_page = MockPage()

    async def fake_goto(url):
        fake_page.main_frame.url = url
        fake_page.emit_navigation(fake_page.main_frame)

    fake_page.goto.side_effect = fake_goto

    fake_browser = AsyncMock()
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
        "name": "Empty Steps Recording Test",
        "steps": [],
    }

    result = await run_browser_test(
        run_id="run-empty",
        app="demo",
        environment="prod",
        base_url="https://example.com",
        test_def=test_def,
        record_url_steps=True,
    )

    assert result.status == "pass"
    fake_page.goto.assert_called_once_with("https://example.com/")
    assert result.step_log[0].step == "navigate /"
