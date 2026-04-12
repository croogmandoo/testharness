import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from harness.browser import execute_step
from harness.models import StepResult


@pytest.fixture
def mock_page():
    page = MagicMock()
    page.screenshot = AsyncMock()
    return page


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_screenshot_step_saves_file_and_returns_path(mock_page, tmp_path):
    """execute_step with screenshot step saves the file and puts relative path in StepResult."""
    shot_path = str(tmp_path / "app" / "prod" / "run1" / "test-step-0.png")
    result = run(execute_step(mock_page, {"screenshot": None}, "https://example.com",
                              screenshot_path=shot_path))
    mock_page.screenshot.assert_called_once_with(path=shot_path)
    assert result.status == "pass"
    assert result.screenshot is not None
    assert "test-step-0" in result.screenshot


def test_screenshot_step_without_path_returns_error(mock_page):
    """execute_step with screenshot step and no screenshot_path returns error status."""
    result = run(execute_step(mock_page, {"screenshot": None}, "https://example.com",
                              screenshot_path=None))
    mock_page.screenshot.assert_not_called()
    assert result.status == "error"
    assert "No screenshot path" in result.error


def test_non_screenshot_step_ignores_screenshot_path(mock_page):
    """Passing screenshot_path to a non-screenshot step has no effect."""
    mock_page.goto = AsyncMock()
    result = run(execute_step(mock_page, {"navigate": "/"}, "https://example.com",
                              screenshot_path="/some/path.png"))
    mock_page.screenshot.assert_not_called()
    assert result.status == "pass"
