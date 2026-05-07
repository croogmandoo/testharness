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


# ── Tier-2: select_option / press_key / hover / wait_for_function ──────────

@pytest.mark.asyncio
async def test_execute_select_option_by_value():
    page = AsyncMock()
    result = await execute_step(
        page,
        {"select_option": {"field": "select#country", "value": "US"}},
        "https://x.com",
    )
    page.select_option.assert_called_once_with("select#country", value="US")
    assert result.status == "pass"


@pytest.mark.asyncio
async def test_execute_select_option_by_label():
    page = AsyncMock()
    result = await execute_step(
        page,
        {"select_option": {"field": "select#country", "label": "United States"}},
        "https://x.com",
    )
    page.select_option.assert_called_once_with("select#country", label="United States")
    assert result.status == "pass"


@pytest.mark.asyncio
async def test_execute_select_option_requires_value_or_label():
    page = AsyncMock()
    result = await execute_step(
        page,
        {"select_option": {"field": "select#country"}},
        "https://x.com",
    )
    assert result.status == "error"
    assert "value/label/index" in (result.error or "")


@pytest.mark.asyncio
async def test_execute_press_key_global():
    page = AsyncMock()
    result = await execute_step(page, {"press_key": "Enter"}, "https://x.com")
    page.keyboard.press.assert_called_once_with("Enter")
    assert result.status == "pass"


@pytest.mark.asyncio
async def test_execute_press_key_on_field():
    page = AsyncMock()
    result = await execute_step(
        page,
        {"press_key": {"field": "#search", "key": "Enter"}},
        "https://x.com",
    )
    page.press.assert_called_once_with("#search", "Enter")
    assert result.status == "pass"


@pytest.mark.asyncio
async def test_execute_hover():
    page = AsyncMock()
    result = await execute_step(page, {"hover": ".tooltip-target"}, "https://x.com")
    page.hover.assert_called_once_with(".tooltip-target")
    assert result.status == "pass"


@pytest.mark.asyncio
async def test_execute_wait_for_function():
    page = AsyncMock()
    expr = "() => window.dataLoaded === true"
    result = await execute_step(page, {"wait_for_function": expr}, "https://x.com")
    page.wait_for_function.assert_called_once_with(expr)
    assert result.status == "pass"
