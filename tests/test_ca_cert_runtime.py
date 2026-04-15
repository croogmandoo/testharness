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


def test_run_availability_test_accepts_ssl_ctx(monkeypatch):
    """run_availability_test passes ssl_ctx as verify= to AsyncClient."""
    import asyncio
    from harness import browser as browser_mod

    captured = {}

    class FakeResponse:
        status_code = 200

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, url): return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    ctx = ssl.create_default_context()
    test_def = {"name": "up", "type": "availability"}
    asyncio.get_event_loop().run_until_complete(
        browser_mod.run_availability_test("run-1", "myapp", "prod",
                                          "http://example.com", test_def, ssl_ctx=ctx)
    )
    assert captured.get("verify") is ctx


def test_run_availability_test_default_ssl_no_ctx(monkeypatch):
    """run_availability_test with ssl_ctx=None falls back to verify=True."""
    import asyncio
    from harness import browser as browser_mod

    captured = {}

    class FakeResponse:
        status_code = 200

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, url): return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    test_def = {"name": "up", "type": "availability"}
    asyncio.get_event_loop().run_until_complete(
        browser_mod.run_availability_test("run-1", "myapp", "prod",
                                          "http://example.com", test_def)
    )
    assert captured.get("verify") is True


def test_run_browser_test_sets_ssl_cert_file_when_bundle_exists(monkeypatch, tmp_path):
    """run_browser_test sets os.environ['SSL_CERT_FILE'] when bundle file exists."""
    import asyncio
    import os
    from harness import browser as browser_mod

    # Create a fake bundle file
    bundle = tmp_path / "ca-bundle.pem"
    bundle.write_text("-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----")

    # Point BUNDLE_PATH to the temp file
    monkeypatch.setattr(browser_mod, "BUNDLE_PATH", str(bundle))

    # Track env assignments
    env_copy = dict(os.environ)
    monkeypatch.setattr(browser_mod.os, "environ", env_copy)

    # Mock playwright to avoid launching real browser
    class FakePage:
        def set_default_timeout(self, t): pass
        async def screenshot(self, path=None): pass

    class FakeBrowser:
        async def new_page(self): return FakePage()
        async def close(self): pass

    class FakeChromium:
        async def launch(self, **kw): return FakeBrowser()

    class FakePW:
        chromium = FakeChromium()
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass

    monkeypatch.setattr(browser_mod, "async_playwright", lambda: FakePW())

    test_def = {"name": "visit", "type": "browser", "steps": []}
    asyncio.get_event_loop().run_until_complete(
        browser_mod.run_browser_test("run-1", "myapp", "prod",
                                     "http://example.com", test_def)
    )
    assert env_copy.get("SSL_CERT_FILE") == str(bundle)


def test_run_browser_test_no_ssl_cert_file_when_no_bundle(monkeypatch, tmp_path):
    """run_browser_test does NOT set SSL_CERT_FILE when bundle file is absent."""
    import asyncio
    import os
    from harness import browser as browser_mod

    # Point BUNDLE_PATH to a non-existent path
    monkeypatch.setattr(browser_mod, "BUNDLE_PATH", str(tmp_path / "nonexistent.pem"))

    env_copy = dict(os.environ)
    env_copy.pop("SSL_CERT_FILE", None)  # ensure it's not set
    monkeypatch.setattr(browser_mod.os, "environ", env_copy)

    class FakePage:
        def set_default_timeout(self, t): pass
        async def screenshot(self, path=None): pass

    class FakeBrowser:
        async def new_page(self): return FakePage()
        async def close(self): pass

    class FakeChromium:
        async def launch(self, **kw): return FakeBrowser()

    class FakePW:
        chromium = FakeChromium()
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass

    monkeypatch.setattr(browser_mod, "async_playwright", lambda: FakePW())

    test_def = {"name": "visit", "type": "browser", "steps": []}
    asyncio.get_event_loop().run_until_complete(
        browser_mod.run_browser_test("run-1", "myapp", "prod",
                                     "http://example.com", test_def)
    )
    assert "SSL_CERT_FILE" not in env_copy
