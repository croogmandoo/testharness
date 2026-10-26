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
    asyncio.run(
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
    asyncio.run(
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
    asyncio.run(
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
    asyncio.run(
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
    asyncio.run(
        browser_mod.run_browser_test("run-1", "myapp", "prod",
                                     "http://example.com", test_def)
    )
    assert env_copy.get("SSL_CERT_FILE") == str(bundle)


def test_run_browser_test_unsets_ssl_cert_file_when_bundle_disappears(monkeypatch, tmp_path):
    """Regression: once SSL_CERT_FILE has been pointed at the bundle, deleting
    the last CA cert removes the bundle file but historically left the env
    var dangling at the now-missing path. The browser test must reset the
    env var so Playwright doesn't try to read a vanished file.
    """
    import asyncio
    import os
    from harness import browser as browser_mod

    bundle = tmp_path / "ca-bundle.pem"  # intentionally NOT created
    monkeypatch.setattr(browser_mod, "BUNDLE_PATH", str(bundle))

    # Simulate a prior run that set SSL_CERT_FILE to this same bundle path.
    env_copy = dict(os.environ)
    env_copy["SSL_CERT_FILE"] = str(bundle)
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
    asyncio.run(
        browser_mod.run_browser_test("run-1", "myapp", "prod",
                                     "http://example.com", test_def)
    )
    # Env var should have been cleaned up since the bundle file doesn't exist.
    assert "SSL_CERT_FILE" not in env_copy


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
    asyncio.run(
        browser_mod.run_browser_test("run-1", "myapp", "prod",
                                     "http://example.com", test_def)
    )
    assert "SSL_CERT_FILE" not in env_copy


def test_run_browser_test_resolves_async_playwright_without_module_global(monkeypatch):
    """Regression: run_browser_test must not reference ``async_playwright`` as
    a bare name expecting PEP-562 module ``__getattr__`` to resolve it.

    Module ``__getattr__`` fires only for attribute access on the module from
    *outside* (``browser_mod.async_playwright``), not for bare-name lookup
    inside the module's own code -- so a bare ``async_playwright()`` call
    would raise ``NameError`` at runtime on the full image and the runner
    would surface it as ``Internal error: NameError: name 'async_playwright'
    is not defined``. The other browser tests in this file all do
    ``monkeypatch.setattr(browser_mod, "async_playwright", ...)`` which writes
    into ``globals()`` and accidentally masks the bug. This test patches the
    underlying ``playwright.async_api.async_playwright`` symbol instead, so
    the function must resolve the name through the production code path
    (local lazy import). If the bare-name regression returns, this test
    fails with ``NameError``.
    """
    import asyncio
    import sys
    import types
    from harness import browser as browser_mod

    # Force a clean resolution path: drop any cached binding (set either by a
    # prior test's monkeypatch or by the PEP-562 __getattr__ caching itself
    # into globals on first attribute access).
    monkeypatch.delattr(browser_mod, "async_playwright", raising=False)

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

    fake_pkg = types.ModuleType("playwright")
    fake_async_api = types.ModuleType("playwright.async_api")
    fake_async_api.async_playwright = lambda: FakePW()
    fake_pkg.async_api = fake_async_api
    monkeypatch.setitem(sys.modules, "playwright", fake_pkg)
    monkeypatch.setitem(sys.modules, "playwright.async_api", fake_async_api)

    test_def = {"name": "visit", "type": "browser", "steps": []}
    result = asyncio.run(
        browser_mod.run_browser_test("run-1", "myapp", "prod",
                                     "http://example.com", test_def)
    )
    # Empty step list + no failures => pass. The point of the assertion is
    # to prove the call returned at all (no NameError).
    assert result.status == "pass"


def _make_self_signed_pem(tmp_path, common_name="harness-test-ca"):
    """Generate a self-signed cert and return (pem_bytes, expected_spki_b64).

    Computes the expected SPKI hash independently (raw cryptography +
    hashlib) so the assertion doesn't tautologically mirror the production
    implementation.
    """
    import base64
    import datetime
    import hashlib
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM)
    spki_der = cert.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    expected_b64 = base64.b64encode(hashlib.sha256(spki_der).digest()).decode("ascii")
    return pem, expected_b64


def test_chromium_spki_args_hashes_match_sha256_b64_of_spki_der(tmp_path):
    """chromium_spki_args emits --ignore-certificate-errors-spki-list with
    base64(sha256(SubjectPublicKeyInfo-DER)) for each cert in the bundle,
    which is what Chromium expects.
    """
    from harness.ssl_context import chromium_spki_args

    pem_a, expected_a = _make_self_signed_pem(tmp_path, "ca-a")
    pem_b, expected_b = _make_self_signed_pem(tmp_path, "ca-b")
    bundle = tmp_path / "ca-bundle.pem"
    bundle.write_bytes(pem_a + b"\n" + pem_b)

    args = chromium_spki_args(str(bundle))
    assert len(args) == 1
    flag, _, hashes_csv = args[0].partition("=")
    assert flag == "--ignore-certificate-errors-spki-list"
    emitted = hashes_csv.split(",")
    assert emitted == [expected_a, expected_b], (
        "SPKI hashes must be ordered as in the bundle and match independent "
        "base64(sha256(SPKI-DER)) computation"
    )


def test_chromium_spki_args_returns_empty_when_bundle_missing(tmp_path):
    from harness.ssl_context import chromium_spki_args
    assert chromium_spki_args(str(tmp_path / "does-not-exist.pem")) == []


def test_chromium_spki_args_returns_empty_when_bundle_blank(tmp_path):
    from harness.ssl_context import chromium_spki_args
    bundle = tmp_path / "ca-bundle.pem"
    bundle.write_text("   \n\n")
    assert chromium_spki_args(str(bundle)) == []


def test_chromium_spki_args_returns_empty_when_bundle_garbage(tmp_path):
    from harness.ssl_context import chromium_spki_args
    bundle = tmp_path / "ca-bundle.pem"
    bundle.write_text("-----BEGIN CERTIFICATE-----\nnot real base64\n-----END CERTIFICATE-----\n")
    # Best-effort: a malformed bundle must not crash the browser launch path.
    assert chromium_spki_args(str(bundle)) == []


def test_run_browser_test_passes_spki_args_to_chromium_launch(monkeypatch, tmp_path):
    """run_browser_test forwards chromium_spki_args(BUNDLE_PATH) into
    pw.chromium.launch(args=...). This is what makes the admin-uploaded CA
    actually trusted by Chromium at runtime (SSL_CERT_FILE is OpenSSL-only).
    """
    import asyncio
    from harness import browser as browser_mod

    pem, expected = _make_self_signed_pem(tmp_path, "wire-up-ca")
    bundle = tmp_path / "ca-bundle.pem"
    bundle.write_bytes(pem)
    monkeypatch.setattr(browser_mod, "BUNDLE_PATH", str(bundle))

    captured = {}

    class FakePage:
        def set_default_timeout(self, t): pass
        async def screenshot(self, path=None): pass

    class FakeBrowser:
        async def new_page(self): return FakePage()
        async def close(self): pass

    class FakeChromium:
        async def launch(self, **kw):
            captured.update(kw)
            return FakeBrowser()

    class FakePW:
        chromium = FakeChromium()
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass

    monkeypatch.setattr(browser_mod, "async_playwright", lambda: FakePW())

    test_def = {"name": "visit", "type": "browser", "steps": []}
    asyncio.run(
        browser_mod.run_browser_test("run-1", "myapp", "prod",
                                     "http://example.com", test_def)
    )
    args = captured.get("args")
    assert args == [f"--ignore-certificate-errors-spki-list={expected}"]


def test_run_browser_test_passes_empty_args_when_no_bundle(monkeypatch, tmp_path):
    """No bundle => no SPKI flag => normal Chromium trust (no surprise
    silent flag injection). Asserts launch_args is an empty list, not None.
    """
    import asyncio
    from harness import browser as browser_mod

    monkeypatch.setattr(browser_mod, "BUNDLE_PATH", str(tmp_path / "no-bundle.pem"))

    captured = {}

    class FakePage:
        def set_default_timeout(self, t): pass
        async def screenshot(self, path=None): pass

    class FakeBrowser:
        async def new_page(self): return FakePage()
        async def close(self): pass

    class FakeChromium:
        async def launch(self, **kw):
            captured.update(kw)
            return FakeBrowser()

    class FakePW:
        chromium = FakeChromium()
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass

    monkeypatch.setattr(browser_mod, "async_playwright", lambda: FakePW())

    test_def = {"name": "visit", "type": "browser", "steps": []}
    asyncio.run(
        browser_mod.run_browser_test("run-1", "myapp", "prod",
                                     "http://example.com", test_def)
    )
    assert captured.get("args") == []


def test_runner_passes_ssl_ctx_to_api_test(monkeypatch, tmp_path):
    """run_app builds an SSL context and passes it to run_api_test."""
    import asyncio
    from harness.db import Database
    from harness.runner import run_app

    db = Database(str(tmp_path / "test.db"))
    db.init_schema()

    api_calls = []

    async def fake_api_test(run_id, app, environment, base_url, test_def, ssl_ctx=None):
        api_calls.append({"ssl_ctx": ssl_ctx})
        from harness.models import TestResult
        from datetime import datetime, timezone
        r = TestResult(run_id=run_id, app=app, environment=environment,
                       test_name=test_def["name"])
        r.status = "pass"
        r.finished_at = datetime.now(timezone.utc).isoformat()
        r.duration_ms = 0
        return r

    monkeypatch.setattr("harness.runner.run_api_test", fake_api_test)

    app_def = {
        "app": "myapp",
        "environments": {"prod": "http://example.com"},
        "tests": [{"name": "ping", "type": "api", "method": "GET", "endpoint": "/ping"}],
    }
    config = {}
    asyncio.run(
        run_app(app_def, "prod", "test", db, config)
    )
    assert len(api_calls) == 1
    assert isinstance(api_calls[0]["ssl_ctx"], ssl.SSLContext)
