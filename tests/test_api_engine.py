import pytest
import httpx
from pytest_httpx import HTTPXMock
from harness.api import run_api_test
from harness.models import TestResult

@pytest.mark.asyncio
async def test_api_test_passes_on_expected_status(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url="https://api.example.com/health", status_code=200,
                            json={"status": "ok"})
    test_def = {"name": "Health check", "type": "api", "endpoint": "/health",
                "method": "GET", "expect_status": 200}
    result = await run_api_test("run-1", "myapi", "production",
                                "https://api.example.com", test_def)
    assert result.status == "pass"
    assert result.error_msg is None

@pytest.mark.asyncio
async def test_api_test_fails_on_wrong_status(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url="https://api.example.com/health", status_code=500)
    test_def = {"name": "Health check", "type": "api", "endpoint": "/health",
                "method": "GET", "expect_status": 200}
    result = await run_api_test("run-1", "myapi", "production",
                                "https://api.example.com", test_def)
    assert result.status == "fail"
    assert "500" in result.error_msg

@pytest.mark.asyncio
async def test_api_test_checks_expect_json(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url="https://api.example.com/health", status_code=200,
                            json={"status": "degraded"})
    test_def = {"name": "Health check", "type": "api", "endpoint": "/health",
                "method": "GET", "expect_status": 200, "expect_json": {"status": "ok"}}
    result = await run_api_test("run-1", "myapi", "production",
                                "https://api.example.com", test_def)
    assert result.status == "fail"
    assert "status" in result.error_msg

@pytest.mark.asyncio
async def test_api_test_includes_custom_headers(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url="https://api.example.com/protected", status_code=200,
                            json={"data": "secret"})
    test_def = {
        "name": "Protected endpoint",
        "type": "api",
        "endpoint": "/protected",
        "method": "GET",
        "expect_status": 200,
        "headers": {
            "Authorization": "Bearer token123",
            "X-Custom-Header": "custom-value"
        }
    }
    result = await run_api_test("run-1", "myapi", "production",
                                "https://api.example.com", test_def)
    assert result.status == "pass"
    # Verify the request was made with the headers
    request = httpx_mock.get_request()
    assert request.headers["Authorization"] == "Bearer token123"
    assert request.headers["X-Custom-Header"] == "custom-value"
