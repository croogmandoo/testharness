"""Tier-2: variable extraction & chaining for API tests."""
import pytest
from pytest_httpx import HTTPXMock

from harness.api import _extract_path, run_api_test


# ── _extract_path unit tests ────────────────────────────────────────────────

def test_extract_path_simple_key():
    assert _extract_path({"token": "abc"}, "$.token") == "abc"


def test_extract_path_no_dollar_prefix():
    assert _extract_path({"token": "abc"}, "token") == "abc"


def test_extract_path_nested():
    assert _extract_path({"user": {"id": 42}}, "$.user.id") == 42


def test_extract_path_array_index():
    assert _extract_path({"items": [{"name": "a"}, {"name": "b"}]},
                          "$.items.1.name") == "b"


def test_extract_path_missing_returns_none():
    assert _extract_path({"a": 1}, "$.b") is None
    assert _extract_path({"a": {"b": 1}}, "$.a.c") is None


def test_extract_path_through_non_dict_returns_none():
    """Path attempts to index into a string -> None, not crash."""
    assert _extract_path({"a": "hello"}, "$.a.length") is None


def test_extract_path_root():
    assert _extract_path({"a": 1}, "$") == {"a": 1}


# ── run_api_test extract integration ────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_api_test_extracts_captures_from_response(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="https://example.com/auth/login",
        method="POST",
        status_code=200,
        json={"token": "tok-xyz", "user": {"id": 42}},
    )
    test_def = {
        "name": "Login",
        "type": "api",
        "endpoint": "/auth/login",
        "method": "POST",
        "expect_status": 200,
        "json": {"username": "u", "password": "p"},
        "extract": {
            "AUTH_TOKEN": "$.token",
            "USER_ID": "$.user.id",
        },
    }
    tr = await run_api_test("run-1", "MyApp", "production",
                            "https://example.com", test_def)
    assert tr.status == "pass", tr.error_msg
    assert tr.captures == {"AUTH_TOKEN": "tok-xyz", "USER_ID": "42"}


@pytest.mark.asyncio
async def test_run_api_test_fails_when_extract_path_missing(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="https://example.com/x", status_code=200, json={"token": "abc"},
    )
    test_def = {
        "name": "Get",
        "type": "api",
        "endpoint": "/x",
        "extract": {"MISSING": "$.does_not_exist"},
    }
    tr = await run_api_test("run-1", "MyApp", "production",
                            "https://example.com", test_def)
    assert tr.status == "fail"
    assert "Extract failed" in (tr.error_msg or "")
    assert "MISSING" in (tr.error_msg or "")


@pytest.mark.asyncio
async def test_run_api_test_no_extract_clause_means_empty_captures(
        httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="https://example.com/x", status_code=200, json={"ok": True},
    )
    test_def = {"name": "Get", "type": "api", "endpoint": "/x"}
    tr = await run_api_test("run-1", "MyApp", "production",
                            "https://example.com", test_def)
    assert tr.status == "pass"
    assert tr.captures == {}


@pytest.mark.asyncio
async def test_run_api_test_sends_json_body(httpx_mock: HTTPXMock):
    """Verify the new `json:` body field actually reaches the server."""
    httpx_mock.add_response(
        url="https://example.com/x", method="POST",
        status_code=200, json={"echoed": True},
    )
    test_def = {
        "name": "Post",
        "type": "api",
        "endpoint": "/x",
        "method": "POST",
        "json": {"foo": "bar"},
    }
    tr = await run_api_test("run-1", "MyApp", "production",
                            "https://example.com", test_def)
    assert tr.status == "pass", tr.error_msg
    request = httpx_mock.get_request()
    assert request is not None
    assert b'"foo"' in request.content
    assert b'"bar"' in request.content
