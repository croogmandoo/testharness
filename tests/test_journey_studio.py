from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from harness.journey_studio import JourneySession, JourneyStudioManager


class FakeCDP:
    def __init__(self):
        self._handlers = {}
        self.send = AsyncMock(return_value={})

    def on(self, event, handler):
        self._handlers.setdefault(event, []).append(handler)

    def emit(self, event, data):
        for h in self._handlers.get(event, []):
            h(data)


class FakePage:
    def __init__(self):
        self.main_frame = MagicMock()
        self.main_frame.url = "https://example.com"
        self._handlers = {}
        self.goto = AsyncMock()
        self.mouse = MagicMock()
        self.mouse.click = AsyncMock()
        self.fill = AsyncMock()
        self.set_extra_http_headers = AsyncMock()
        self.evaluate = AsyncMock(return_value={"selector": "button#submit", "tag": "button"})

    def on(self, event, handler):
        self._handlers.setdefault(event, []).append(handler)

    def emit_navigation(self, url):
        self.main_frame.url = url
        for h in self._handlers.get("framenavigated", []):
            h(self.main_frame)


class FakeContext:
    def __init__(self, page, cdp):
        self._page = page
        self._cdp = cdp
        self.close = AsyncMock()

    async def new_page(self):
        return self._page

    async def new_cdp_session(self, page):
        return self._cdp


class FakeBrowser:
    def __init__(self, context):
        self._context = context
        self.close = AsyncMock()

    async def new_context(self, **kwargs):
        return self._context


class FakePlaywright:
    def __init__(self, browser):
        self.chromium = MagicMock()
        self.chromium.launch = AsyncMock(return_value=browser)
        self.stop = AsyncMock()


@pytest.fixture
def mock_playwright_stack():
    page = FakePage()
    cdp = FakeCDP()
    context = FakeContext(page, cdp)
    browser = FakeBrowser(context)
    pw = FakePlaywright(browser)

    class FakeLauncher:
        async def start(self):
            return pw

    with patch("harness.journey_studio._get_playwright_launcher", return_value=FakeLauncher):
        yield {
            "page": page,
            "cdp": cdp,
            "context": context,
            "browser": browser,
            "pw": pw,
        }


@pytest.mark.asyncio
async def test_journey_session_lifecycle_and_actions(mock_playwright_stack):
    page = mock_playwright_stack["page"]
    cdp = mock_playwright_stack["cdp"]

    session = JourneySession("test-session", "https://example.com")
    await session.start("https://example.com/home")

    assert session.is_running is True
    page.goto.assert_called_with("https://example.com/home", timeout=30000)

    # Simulate frame navigation event
    page.emit_navigation("https://example.com/dashboard")
    assert len(session.steps) >= 1
    assert {"navigate": "/dashboard"} in session.steps

    # Test click action
    res = await session.click(100, 200)
    page.mouse.click.assert_called_once_with(100, 200)
    assert res["step"] == {"click": "button#submit"}
    assert {"click": "button#submit"} in session.steps

    # Test fill action
    res_fill = await session.fill("input#user", "admin")
    page.fill.assert_called_once_with("input#user", "admin", timeout=10000)
    assert res_fill["step"] == {"fill": {"field": "input#user", "value": "admin"}}

    # Test assert_text
    await session.assert_text("Welcome back")
    assert {"assert_text": "Welcome back"} in session.steps

    # Test screenshot
    await session.screenshot()
    assert {"screenshot": None} in session.steps

    # Test remove_step
    initial_count = len(session.steps)
    await session.remove_step(0)
    assert len(session.steps) == initial_count - 1

    # Close session
    steps = await session.close()
    assert session.is_running is False
    assert isinstance(steps, list)


@pytest.mark.asyncio
async def test_journey_studio_manager(mock_playwright_stack):
    mgr = JourneyStudioManager()
    session = await mgr.create_session("https://example.com", "https://example.com")
    assert session.session_id in mgr._sessions

    fetched = await mgr.get_session(session.session_id)
    assert fetched == session

    steps = await mgr.close_session(session.session_id)
    assert session.session_id not in mgr._sessions
    assert isinstance(steps, list)


@pytest.mark.asyncio
async def test_journey_api_endpoints(mock_playwright_stack, client):
    # Test POST /api/journey/start
    res = client.post(
        "/api/journey/start",
        json={"url": "https://example.com/login", "base_url": "https://example.com"},
    )
    assert res.status_code == 200
    data = res.json()
    assert "session_id" in data
    session_id = data["session_id"]

    # Test POST /api/journey/{id}/action
    action_res = client.post(
        f"/api/journey/{session_id}/action",
        json={"action": "assert_text", "text": "Login Portal"},
    )
    assert action_res.status_code == 200
    action_data = action_res.json()
    assert action_data["status"] == "ok"
    assert any("assert_text" in s for s in action_data["steps"])

    # Test POST /api/journey/{id}/stop
    stop_res = client.post(
        f"/api/journey/{session_id}/stop",
    )
    assert stop_res.status_code == 200
    stop_data = stop_res.json()
    assert stop_data["status"] == "complete"
    assert isinstance(stop_data["steps"], list)


@pytest.mark.asyncio
async def test_journey_session_with_headers(mock_playwright_stack):
    stack = mock_playwright_stack
    session = JourneySession("sess-hdr", "https://example.com")
    await session.start("https://example.com/api", headers={"Authorization": "Bearer tok"})
    stack["page"].set_extra_http_headers.assert_called_once_with({"Authorization": "Bearer tok"})
    await session.close()
