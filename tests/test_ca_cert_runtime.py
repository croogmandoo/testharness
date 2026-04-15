# tests/test_ca_cert_runtime.py
import ssl
import pytest
import httpx


def test_run_api_test_accepts_ssl_ctx(monkeypatch):
    """run_api_test passes ssl_ctx as the verify= arg to AsyncClient."""
    import asyncio
    from harness.api import run_api_test

    captured = {}

    class FakeResponse:
        status_code = 200
        def json(self): return {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def request(self, method, url): return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    ctx = ssl.create_default_context()
    test_def = {"name": "ping", "type": "api", "method": "GET", "endpoint": "/ping"}
    asyncio.get_event_loop().run_until_complete(
        run_api_test("run-1", "myapp", "prod", "http://example.com", test_def, ssl_ctx=ctx)
    )
    assert captured.get("verify") is ctx


def test_run_api_test_default_ssl_no_ctx(monkeypatch):
    """run_api_test with ssl_ctx=None falls back to verify=True (httpx default)."""
    import asyncio
    from harness.api import run_api_test

    captured = {}

    class FakeResponse:
        status_code = 200
        def json(self): return {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def request(self, method, url): return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    test_def = {"name": "ping", "type": "api", "method": "GET", "endpoint": "/ping"}
    asyncio.get_event_loop().run_until_complete(
        run_api_test("run-1", "myapp", "prod", "http://example.com", test_def)
    )
    assert captured.get("verify") is True
