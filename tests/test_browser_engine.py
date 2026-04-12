import pytest
from unittest.mock import AsyncMock
from harness.browser import execute_step
from harness.models import StepResult

@pytest.mark.asyncio
async def test_execute_navigate_step():
    page = AsyncMock()
    result = await execute_step(page, {"navigate": "/login"}, "https://example.com")
    page.goto.assert_called_once_with("https://example.com/login")
    assert result.status == "pass"

@pytest.mark.asyncio
async def test_execute_navigate_absolute_url():
    page = AsyncMock()
    result = await execute_step(page, {"navigate": "https://other.com/page"}, "https://example.com")
    page.goto.assert_called_once_with("https://other.com/page")

@pytest.mark.asyncio
async def test_execute_fill_step():
    page = AsyncMock()
    result = await execute_step(page, {"fill": {"field": "#user", "value": "admin"}}, "https://x.com")
    page.fill.assert_called_once_with("#user", "admin")
    assert result.status == "pass"

@pytest.mark.asyncio
async def test_execute_click_step():
    page = AsyncMock()
    result = await execute_step(page, {"click": "button"}, "https://x.com")
    page.click.assert_called_once_with("button")
    assert result.status == "pass"

@pytest.mark.asyncio
async def test_execute_assert_url_contains_pass():
    page = AsyncMock()
    page.url = "https://example.com/dashboard"
    result = await execute_step(page, {"assert_url_contains": "/dashboard"}, "https://example.com")
    assert result.status == "pass"

@pytest.mark.asyncio
async def test_execute_assert_url_contains_fail():
    page = AsyncMock()
    page.url = "https://example.com/login?error=1"
    result = await execute_step(page, {"assert_url_contains": "/dashboard"}, "https://example.com")
    assert result.status == "fail"
    assert "/dashboard" in result.error

@pytest.mark.asyncio
async def test_execute_assert_text_pass():
    page = AsyncMock()
    page.text_content = AsyncMock(return_value="Welcome, Admin")
    result = await execute_step(page, {"assert_text": "Welcome"}, "https://x.com")
    assert result.status == "pass"

@pytest.mark.asyncio
async def test_execute_assert_text_fail():
    page = AsyncMock()
    page.text_content = AsyncMock(return_value="Error: not found")
    result = await execute_step(page, {"assert_text": "Welcome"}, "https://x.com")
    assert result.status == "fail"
