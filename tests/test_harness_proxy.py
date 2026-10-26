"""Unit tests for outbound corporate forward proxy configuration in harness.proxy."""
import os
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from harness.proxy import (
    get_outbound_proxy_config,
    get_playwright_proxy_args,
    get_httpx_proxy_url,
)
from harness.browser import run_browser_test, run_availability_test
from harness.api import run_api_test


def test_get_outbound_proxy_config_from_dict():
    cfg = {
        "proxy": {
            "outbound": {
                "enabled": True,
                "https_proxy": "http://proxy.corp.example.com:8080",
                "no_proxy": "localhost,127.0.0.1,.corp.internal",
                "username": "corpuser",
                "password": "corppassword",
            }
        }
    }
    res = get_outbound_proxy_config(cfg)
    assert res is not None
    assert res["server"] == "http://proxy.corp.example.com:8080"
    assert res["bypass"] == "localhost,127.0.0.1,.corp.internal"
    assert res["username"] == "corpuser"
    assert res["password"] == "corppassword"
    assert res["url"] == "http://corpuser:corppassword@proxy.corp.example.com:8080"


def test_get_outbound_proxy_config_from_env(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://env-proxy.local:3128")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")

    res = get_outbound_proxy_config({})
    assert res is not None
    assert res["server"] == "http://env-proxy.local:3128"
    assert res["bypass"] == "127.0.0.1,localhost"
    assert res["url"] == "http://env-proxy.local:3128"


def test_get_outbound_proxy_embedded_credentials(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://admin:secret123@proxy.gateway:8080")
    monkeypatch.delenv("HTTPS_PROXY", raising=False)

    res = get_outbound_proxy_config(None)
    assert res is not None
    assert res["server"] == "http://proxy.gateway:8080"
    assert res["username"] == "admin"
    assert res["password"] == "secret123"
    assert res["url"] == "http://admin:secret123@proxy.gateway:8080"

    pw_args = get_playwright_proxy_args(None)
    assert pw_args == {
        "server": "http://proxy.gateway:8080",
        "username": "admin",
        "password": "secret123",
    }
    assert get_httpx_proxy_url(None) == "http://admin:secret123@proxy.gateway:8080"


def test_get_outbound_proxy_none_when_unset(monkeypatch):
    for k in ["HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"]:
        monkeypatch.delenv(k, raising=False)

    assert get_outbound_proxy_config(None) is None
    assert get_playwright_proxy_args(None) is None
    assert get_httpx_proxy_url(None) is None


@pytest.mark.asyncio
async def test_run_browser_test_passes_proxy_to_launch(monkeypatch):
    fake_browser = AsyncMock()
    fake_page = AsyncMock()
    fake_page.set_default_timeout = MagicMock()
    fake_page.url = "http://example.com"
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
    monkeypatch.setenv("HTTPS_PROXY", "http://corp-proxy:8080")

    result = await run_browser_test(
        run_id="run-1",
        app="test-app",
        environment="prod",
        base_url="http://example.com",
        test_def={"name": "test1", "steps": []},
    )

    fake_chromium.launch.assert_called_once()
    _, kwargs = fake_chromium.launch.call_args
    assert "proxy" in kwargs
    assert kwargs["proxy"]["server"] == "http://corp-proxy:8080"
    assert result.status == "pass"


@pytest.mark.asyncio
async def test_run_availability_test_uses_proxy(monkeypatch):
    called_proxy = []

    class FakeResponse:
        status_code = 200

    class FakeClient:
        def __init__(self, **kwargs):
            called_proxy.append(kwargs.get("proxy"))
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
        async def get(self, url):
            return FakeResponse()

    monkeypatch.setattr("httpx.AsyncClient", FakeClient)

    res = await run_availability_test(
        run_id="run-1",
        app="test-app",
        environment="prod",
        base_url="http://example.com",
        test_def={"name": "status"},
        proxy="http://custom-proxy:3128",
    )
    assert res.status == "pass"
    assert called_proxy == ["http://custom-proxy:3128"]


@pytest.mark.asyncio
async def test_run_api_test_uses_proxy(monkeypatch):
    called_proxy = []

    class FakeResponse:
        status_code = 200
        def json(self):
            return {"status": "ok"}

    class FakeClient:
        def __init__(self, **kwargs):
            called_proxy.append(kwargs.get("proxy"))
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
        async def request(self, method, url, **kwargs):
            return FakeResponse()

    monkeypatch.setattr("httpx.AsyncClient", FakeClient)

    res = await run_api_test(
        run_id="run-1",
        app="test-app",
        environment="prod",
        base_url="http://example.com",
        test_def={"name": "api-check", "method": "GET", "endpoint": "/health"},
        proxy="http://custom-proxy:3128",
    )
    assert res.status == "pass"
    assert called_proxy == ["http://custom-proxy:3128"]
