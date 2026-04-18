# CA Certificate Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow admins to upload named CA certificates that are transparently applied to all HTTP and browser test clients, enabling testing of services with private or self-signed TLS.

**Architecture:** A new `ca_certs` DB table stores PEM blobs. A helper module (`harness/ssl_context.py`) builds an SSL context from stored certs and writes an on-disk bundle for Playwright. The runner passes the SSL context to httpx-based tests; browser tests pick up the bundle via `SSL_CERT_FILE`. Admin routes in `web/routes/admin_ca_certs.py` expose add/delete UI.

**Tech Stack:** Python stdlib `ssl`, SQLite via existing `Database` class, FastAPI, httpx, Playwright (Node.js picks up `SSL_CERT_FILE`), Jinja2 templates.

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `harness/db.py` | Add `ca_certs` table DDL + 4 CRUD methods |
| Create | `harness/ssl_context.py` | `get_ssl_context()`, `write_ca_bundle()`, `BUNDLE_PATH` |
| Modify | `harness/api.py` | Add `ssl_ctx` param to `run_api_test` |
| Modify | `harness/browser.py` | Add `ssl_ctx` param to `run_availability_test`; set `SSL_CERT_FILE` in `run_browser_test` |
| Modify | `harness/runner.py` | Build SSL context once; pass to test functions; call `write_ca_bundle` |
| Create | `web/routes/admin_ca_certs.py` | GET/POST list+add, POST delete; admin-only |
| Create | `web/templates/admin_ca_certs.html` | Add form + stored certs table |
| Modify | `web/templates/base.html` | Add "CA Certs" nav link next to "LDAP" |
| Modify | `web/main.py` | Register `admin_ca_certs` router |
| Create | `tests/test_ca_cert_db.py` | Unit tests: insert, list, delete, bundle writer |
| Create | `tests/test_web_admin_ca_certs.py` | Integration tests: routes, 403, validation |
| Create | `tests/test_ca_cert_runtime.py` | Runtime tests: SSL context building, `SSL_CERT_FILE` |

---

### Task 1: DB — `ca_certs` table DDL

**Files:**
- Modify: `harness/db.py`
- Test: `tests/test_ca_cert_db.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ca_cert_db.py
import pytest
from harness.db import Database


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.init_schema()
    return d


def test_ca_certs_table_exists(db):
    """Schema init creates ca_certs table without errors."""
    import sqlite3
    conn = sqlite3.connect(db.path)
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='ca_certs'"
    ).fetchone()
    conn.close()
    assert row is not None
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_ca_cert_db.py::test_ca_certs_table_exists -v
```
Expected: FAIL — `AssertionError` (table doesn't exist yet)

- [ ] **Step 3: Add DDL to `harness/db.py`**

Append the `ca_certs` table definition inside the `SCHEMA` string, immediately after the `secrets` table closing `);`:

```python
CREATE TABLE IF NOT EXISTS ca_certs (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    pem_content TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    added_by    TEXT REFERENCES users(id)
);
```

- [ ] **Step 4: Run test to verify it passes**

```
pytest tests/test_ca_cert_db.py::test_ca_certs_table_exists -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add harness/db.py tests/test_ca_cert_db.py
git commit -m "feat: add ca_certs table schema"
```

---

### Task 2: DB — CRUD methods

**Files:**
- Modify: `harness/db.py`
- Test: `tests/test_ca_cert_db.py`

- [ ] **Step 1: Write failing tests for the four CRUD methods**

Append to `tests/test_ca_cert_db.py`:

```python
from datetime import datetime, timezone


def test_insert_and_list_ca_cert(db):
    db.insert_ca_cert({
        "id": "cert-1",
        "name": "Corp Root CA",
        "pem_content": "-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "added_by": None,
    })
    certs = db.list_ca_certs()
    assert len(certs) == 1
    assert certs[0]["name"] == "Corp Root CA"
    assert certs[0]["id"] == "cert-1"


def test_get_ca_cert(db):
    db.insert_ca_cert({
        "id": "cert-2",
        "name": "Dev CA",
        "pem_content": "-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "added_by": None,
    })
    cert = db.get_ca_cert("cert-2")
    assert cert is not None
    assert cert["name"] == "Dev CA"


def test_get_ca_cert_not_found(db):
    assert db.get_ca_cert("nonexistent") is None


def test_delete_ca_cert(db):
    db.insert_ca_cert({
        "id": "cert-3",
        "name": "Old CA",
        "pem_content": "-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "added_by": None,
    })
    db.delete_ca_cert("cert-3")
    assert db.get_ca_cert("cert-3") is None
    assert db.list_ca_certs() == []


def test_list_ca_certs_ordered_desc(db):
    db.insert_ca_cert({
        "id": "cert-a",
        "name": "Older CA",
        "pem_content": "-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----",
        "created_at": "2026-01-01T00:00:00+00:00",
        "added_by": None,
    })
    db.insert_ca_cert({
        "id": "cert-b",
        "name": "Newer CA",
        "pem_content": "-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----",
        "created_at": "2026-06-01T00:00:00+00:00",
        "added_by": None,
    })
    certs = db.list_ca_certs()
    assert certs[0]["id"] == "cert-b"
    assert certs[1]["id"] == "cert-a"
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_ca_cert_db.py -v
```
Expected: 4 new tests FAIL with `AttributeError: 'Database' object has no attribute 'insert_ca_cert'`

- [ ] **Step 3: Add CRUD methods to `harness/db.py`**

Append a new `# ── CA Certs ──` section at the end of the `Database` class (after the `list_secrets` method):

```python
    # ── CA Certs ───────────────────────────────────────────────────────────────

    def insert_ca_cert(self, row: dict) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO ca_certs (id, name, pem_content, created_at, added_by) "
                "VALUES (:id, :name, :pem_content, :created_at, :added_by)",
                row,
            )

    def list_ca_certs(self) -> list:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM ca_certs ORDER BY created_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_ca_cert(self, cert_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM ca_certs WHERE id=?", (cert_id,)
            ).fetchone()
            return dict(row) if row else None

    def delete_ca_cert(self, cert_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM ca_certs WHERE id=?", (cert_id,))
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_ca_cert_db.py -v
```
Expected: all 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add harness/db.py tests/test_ca_cert_db.py
git commit -m "feat: add ca_cert CRUD methods to Database"
```

---

### Task 3: SSL context helper and bundle writer

**Files:**
- Create: `harness/ssl_context.py`
- Test: `tests/test_ca_cert_db.py`

- [ ] **Step 1: Write failing tests for ssl_context helpers**

Append to `tests/test_ca_cert_db.py`:

```python
import ssl
import os


def test_get_ssl_context_no_certs(db):
    from harness.ssl_context import get_ssl_context
    ctx = get_ssl_context(db)
    assert isinstance(ctx, ssl.SSLContext)


def test_get_ssl_context_with_cert(db, tmp_path):
    """get_ssl_context loads stored certs without raising."""
    # Use a real self-signed cert PEM for load_verify_locations to accept
    import subprocess, sys
    # Generate a minimal self-signed cert via Python
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        import datetime as dt
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, u"test")])
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(dt.datetime.now(dt.timezone.utc))
            .not_valid_after(dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=365))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256())
        )
        pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    except ImportError:
        pytest.skip("cryptography package not available")
    db.insert_ca_cert({
        "id": "cert-ssl",
        "name": "Test CA",
        "pem_content": pem,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "added_by": None,
    })
    from harness.ssl_context import get_ssl_context
    ctx = get_ssl_context(db)
    assert isinstance(ctx, ssl.SSLContext)


def test_write_ca_bundle_creates_file(db, tmp_path):
    from harness.ssl_context import write_ca_bundle
    db.insert_ca_cert({
        "id": "cert-bundle",
        "name": "Bundle CA",
        "pem_content": "-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "added_by": None,
    })
    bundle_path = str(tmp_path / "ca-bundle.pem")
    write_ca_bundle(db, path=bundle_path)
    assert os.path.exists(bundle_path)
    content = open(bundle_path).read()
    assert "BEGIN CERTIFICATE" in content


def test_write_ca_bundle_removes_file_when_empty(db, tmp_path):
    from harness.ssl_context import write_ca_bundle
    bundle_path = str(tmp_path / "ca-bundle.pem")
    # Create a stale file
    open(bundle_path, "w").write("old content")
    write_ca_bundle(db, path=bundle_path)
    assert not os.path.exists(bundle_path)


def test_write_ca_bundle_multiple_certs(db, tmp_path):
    from harness.ssl_context import write_ca_bundle
    for i in range(3):
        db.insert_ca_cert({
            "id": f"cert-m{i}",
            "name": f"CA {i}",
            "pem_content": f"-----BEGIN CERTIFICATE-----\nfake{i}\n-----END CERTIFICATE-----",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "added_by": None,
        })
    bundle_path = str(tmp_path / "ca-bundle.pem")
    write_ca_bundle(db, path=bundle_path)
    content = open(bundle_path).read()
    assert content.count("BEGIN CERTIFICATE") == 3
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_ca_cert_db.py::test_write_ca_bundle_creates_file tests/test_ca_cert_db.py::test_write_ca_bundle_removes_file_when_empty tests/test_ca_cert_db.py::test_write_ca_bundle_multiple_certs tests/test_ca_cert_db.py::test_get_ssl_context_no_certs -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'harness.ssl_context'`

- [ ] **Step 3: Create `harness/ssl_context.py`**

```python
import os
import ssl
from harness.db import Database

BUNDLE_PATH = "data/ca-bundle.pem"


def get_ssl_context(db: Database) -> ssl.SSLContext:
    """Return system default SSL context with any stored CA certs appended."""
    ctx = ssl.create_default_context()
    certs = db.list_ca_certs()
    if certs:
        combined = "\n".join(c["pem_content"] for c in certs)
        ctx.load_verify_locations(cadata=combined)
    return ctx


def write_ca_bundle(db: Database, path: str = BUNDLE_PATH) -> None:
    """Write all CA certs to a PEM bundle file. Removes the file if no certs."""
    certs = db.list_ca_certs()
    if certs:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w") as f:
            f.write("\n".join(c["pem_content"] for c in certs))
    elif os.path.exists(path):
        os.remove(path)
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_ca_cert_db.py -v
```
Expected: all tests PASS (the `test_get_ssl_context_with_cert` may skip if `cryptography` is absent — that is acceptable)

- [ ] **Step 5: Commit**

```bash
git add harness/ssl_context.py tests/test_ca_cert_db.py
git commit -m "feat: add ssl_context helper with get_ssl_context and write_ca_bundle"
```

---

### Task 4: Runtime integration — `harness/api.py`

**Files:**
- Modify: `harness/api.py`
- Test: `tests/test_ca_cert_runtime.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_ca_cert_runtime.py::test_run_api_test_accepts_ssl_ctx tests/test_ca_cert_runtime.py::test_run_api_test_default_ssl_no_ctx -v
```
Expected: FAIL — `run_api_test` doesn't accept `ssl_ctx` yet; `verify=True` assertion also fails

- [ ] **Step 3: Update `harness/api.py`**

Change the `run_api_test` signature and `AsyncClient` call:

```python
import ssl
import time
import httpx
from datetime import datetime, timezone
from typing import Optional
from harness.models import TestResult, StepResult

async def run_api_test(run_id: str, app: str, environment: str,
                       base_url: str, test_def: dict,
                       ssl_ctx: Optional[ssl.SSLContext] = None) -> TestResult:
    result = TestResult(run_id=run_id, app=app, environment=environment,
                        test_name=test_def["name"])
    start = time.monotonic()
    try:
        method = test_def.get("method", "GET").upper()
        endpoint = test_def.get("endpoint", "/")
        url = base_url.rstrip("/") + endpoint
        expect_status = test_def.get("expect_status", 200)
        expect_json = test_def.get("expect_json")

        async with httpx.AsyncClient(timeout=30, verify=ssl_ctx or True) as client:
            response = await client.request(method, url)

        step = StepResult(step=f"{method} {endpoint}", status="pass",
                          duration_ms=int((time.monotonic() - start) * 1000))

        if response.status_code != expect_status:
            step.status = "fail"
            step.error = f"Expected status {expect_status}, got {response.status_code}"
            result.status = "fail"
            result.error_msg = step.error
            result.step_log = [step]
            result.duration_ms = step.duration_ms
            result.finished_at = datetime.now(timezone.utc).isoformat()
            return result

        if expect_json:
            try:
                body = response.json()
            except Exception:
                body = {}
            mismatches = {k: f"expected {v!r}, got {body.get(k)!r}"
                          for k, v in expect_json.items() if body.get(k) != v}
            if mismatches:
                step.status = "fail"
                step.error = "JSON mismatch: " + ", ".join(
                    f"{k}: {m}" for k, m in mismatches.items())
                result.status = "fail"
                result.error_msg = step.error
                result.step_log = [step]
                result.duration_ms = step.duration_ms
                result.finished_at = datetime.now(timezone.utc).isoformat()
                return result

        result.status = "pass"
        result.step_log = [step]
        result.duration_ms = int((time.monotonic() - start) * 1000)
        result.finished_at = datetime.now(timezone.utc).isoformat()
    except Exception as e:
        result.status = "error"
        result.error_msg = str(e)
        result.duration_ms = int((time.monotonic() - start) * 1000)
        result.finished_at = datetime.now(timezone.utc).isoformat()
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_ca_cert_runtime.py::test_run_api_test_accepts_ssl_ctx tests/test_ca_cert_runtime.py::test_run_api_test_default_ssl_no_ctx -v
```
Expected: both PASS

- [ ] **Step 5: Commit**

```bash
git add harness/api.py tests/test_ca_cert_runtime.py
git commit -m "feat: add ssl_ctx param to run_api_test"
```

---

### Task 5: Runtime integration — `harness/browser.py`

**Files:**
- Modify: `harness/browser.py`
- Test: `tests/test_ca_cert_runtime.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_ca_cert_runtime.py`:

```python
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


def test_run_browser_test_sets_ssl_cert_file(monkeypatch, tmp_path):
    """run_browser_test sets SSL_CERT_FILE when bundle exists."""
    import asyncio
    import os
    from harness import browser as browser_mod
    from harness.ssl_context import BUNDLE_PATH

    bundle = tmp_path / "ca-bundle.pem"
    bundle.write_text("-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----")

    monkeypatch.setattr(browser_mod.os.path, "exists",
                        lambda p: str(p) == str(bundle) if "ca-bundle" in str(p) else os.path.exists(p))
    monkeypatch.setattr(browser_mod, "BUNDLE_PATH", str(bundle))

    env_set = {}
    monkeypatch.setattr(browser_mod.os, "environ",
                        {**os.environ, **env_set})

    # Mock playwright to avoid actually launching a browser
    class FakePage:
        default_timeout = None
        def set_default_timeout(self, t): pass
        async def goto(self, url): pass
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
    assert browser_mod.os.environ.get("SSL_CERT_FILE") == str(bundle)
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_ca_cert_runtime.py::test_run_availability_test_accepts_ssl_ctx tests/test_ca_cert_runtime.py::test_run_availability_test_default_ssl_no_ctx tests/test_ca_cert_runtime.py::test_run_browser_test_sets_ssl_cert_file -v
```
Expected: FAIL — `run_availability_test` signature unchanged; `run_browser_test` doesn't set `SSL_CERT_FILE`

- [ ] **Step 3: Update `harness/browser.py`**

Add `import ssl` and `from harness.ssl_context import BUNDLE_PATH` at the top of the existing imports block:

```python
import os
import ssl
import time
from datetime import datetime, timezone
from typing import Optional
from playwright.async_api import async_playwright, Page
from harness.models import TestResult, StepResult
from harness.loader import slugify_test_name
from harness.ssl_context import BUNDLE_PATH
```

Update `run_browser_test` — add the `SSL_CERT_FILE` block immediately before the `async with async_playwright()` line (at the start of the function body, after the `result` and `step_log` assignments):

```python
async def run_browser_test(run_id: str, app: str, environment: str,
                           base_url: str, test_def: dict,
                           screenshot_dir: str = "data/screenshots",
                           headless: bool = True, timeout_ms: int = 30000) -> TestResult:
    result = TestResult(run_id=run_id, app=app, environment=environment,
                        test_name=test_def["name"])
    step_log = []
    start = time.monotonic()
    if os.path.exists(BUNDLE_PATH):
        os.environ["SSL_CERT_FILE"] = BUNDLE_PATH
    async with async_playwright() as pw:
        # ... rest of function unchanged
```

Update `run_availability_test` signature and `AsyncClient` call:

```python
async def run_availability_test(run_id: str, app: str, environment: str,
                                base_url: str, test_def: dict,
                                ssl_ctx: Optional[ssl.SSLContext] = None) -> TestResult:
    import httpx
    result = TestResult(run_id=run_id, app=app, environment=environment,
                        test_name=test_def["name"])
    start = time.monotonic()
    expect_status = test_def.get("expect_status", 200)
    try:
        async with httpx.AsyncClient(timeout=30, verify=ssl_ctx or True) as client:
            resp = await client.get(base_url)
        elapsed = int((time.monotonic() - start) * 1000)
        if resp.status_code == expect_status:
            result.status = "pass"
        else:
            result.status = "fail"
            result.error_msg = f"Expected status {expect_status}, got {resp.status_code}"
        result.step_log = [StepResult(step=f"GET {base_url}", status=result.status,
                                      duration_ms=elapsed, error=result.error_msg)]
    except Exception as e:
        result.status = "error"
        result.error_msg = str(e)
    result.duration_ms = int((time.monotonic() - start) * 1000)
    result.finished_at = datetime.now(timezone.utc).isoformat()
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_ca_cert_runtime.py -v
```
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add harness/browser.py tests/test_ca_cert_runtime.py
git commit -m "feat: add ssl_ctx to run_availability_test, set SSL_CERT_FILE in run_browser_test"
```

---

### Task 6: Runtime integration — `harness/runner.py`

**Files:**
- Modify: `harness/runner.py`
- Test: `tests/test_ca_cert_runtime.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ca_cert_runtime.py`:

```python
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
        r = TestResult(run_id=run_id, app=app, environment=environment,
                       test_name=test_def["name"])
        r.status = "pass"
        from datetime import datetime, timezone
        r.finished_at = datetime.now(timezone.utc).isoformat()
        r.duration_ms = 0
        return r

    monkeypatch.setattr("harness.runner.run_api_test", fake_api_test)

    app_def = {
        "app": "myapp",
        "environments": {"prod": {"base_url": "http://example.com"}},
        "tests": [{"name": "ping", "type": "api", "method": "GET", "endpoint": "/ping"}],
    }
    config = {}
    asyncio.get_event_loop().run_until_complete(
        run_app(app_def, "prod", "test", db, config)
    )
    assert len(api_calls) == 1
    assert isinstance(api_calls[0]["ssl_ctx"], ssl.SSLContext)
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_ca_cert_runtime.py::test_runner_passes_ssl_ctx_to_api_test -v
```
Expected: FAIL — `run_app` doesn't build or pass `ssl_ctx` yet

- [ ] **Step 3: Update `harness/runner.py`**

Full replacement (changes are: import `get_ssl_context` and `write_ca_bundle`, build `ssl_ctx` once per run, pass to api and availability calls):

```python
import asyncio
from datetime import datetime, timezone
from typing import Optional
from harness.db import Database
from harness.models import Run, AppState
from harness.loader import resolve_base_url
from harness.api import run_api_test
from harness.browser import run_browser_test, run_availability_test
from harness.alerts import dispatch_alerts
from harness.types import AlertType
from harness.ssl_context import get_ssl_context, write_ca_bundle

def determine_alert(previous_state: str, new_status: str) -> Optional[AlertType]:
    is_fail = new_status in ("fail", "error")
    if is_fail and previous_state in ("unknown", "passing"):
        return AlertType.FAIL
    if not is_fail and previous_state == "failing":
        return AlertType.RESOLVE
    return None

async def run_app(app_def: dict, environment: str, triggered_by: str,
                  db: Database, config: dict, run_id: str = None,
                  secrets_store=None) -> str:
    if secrets_store is not None:
        secrets_store.inject_to_env()
    from harness.config import resolve_env_vars
    app_def = resolve_env_vars(app_def, strict=True)
    if run_id:
        run = Run(id=run_id, app=app_def["app"], environment=environment, triggered_by=triggered_by)
    else:
        run = Run(app=app_def["app"], environment=environment, triggered_by=triggered_by)
        db.insert_run(run)
    db.update_run_status(run.id, "running",
                         started_at=datetime.now(timezone.utc).isoformat())
    base_url = resolve_base_url(app_def, environment)
    browser_cfg = config.get("browser", {})
    headless = browser_cfg.get("headless", True)
    timeout_ms = browser_cfg.get("timeout_ms", 30000)
    alerts_cfg = config.get("alerts", {})
    ssl_ctx = get_ssl_context(db)
    write_ca_bundle(db)
    alerts_to_send = []
    for test_def in app_def.get("tests", []):
        test_type = test_def.get("type", "availability")
        if test_type == "api":
            result = await run_api_test(run.id, run.app, environment, base_url, test_def,
                                        ssl_ctx=ssl_ctx)
        elif test_type == "browser":
            result = await run_browser_test(run.id, run.app, environment, base_url,
                                            test_def, headless=headless, timeout_ms=timeout_ms)
        else:
            result = await run_availability_test(run.id, run.app, environment, base_url,
                                                 test_def, ssl_ctx=ssl_ctx)
        db.insert_test_result(result)
        prev = db.get_app_state(run.app, environment, test_def["name"])
        prev_state = prev["state"] if prev else "unknown"
        new_state_str = "passing" if result.status == "pass" else "failing"
        new_state = AppState(app=run.app, environment=environment,
                             test_name=test_def["name"], state=new_state_str,
                             since=result.finished_at or datetime.now(timezone.utc).isoformat())
        db.upsert_app_state(new_state)
        alert_type = determine_alert(prev_state, result.status)
        if alert_type:
            alerts_to_send.append((alert_type, run.app, environment, test_def["name"],
                                   result.error_msg))
    db.update_run_status(run.id, "complete",
                         finished_at=datetime.now(timezone.utc).isoformat())
    if alerts_to_send:
        await dispatch_alerts(alerts_to_send, alerts_cfg)
    return run.id
```

- [ ] **Step 4: Run all runtime tests**

```
pytest tests/test_ca_cert_runtime.py -v
```
Expected: all PASS

- [ ] **Step 5: Run existing runner tests to check for regressions**

```
pytest tests/test_runner.py -v
```
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add harness/runner.py tests/test_ca_cert_runtime.py
git commit -m "feat: build SSL context and bundle in runner, pass to test functions"
```

---

### Task 7: Web route — `web/routes/admin_ca_certs.py`

**Files:**
- Create: `web/routes/admin_ca_certs.py`
- Test: `tests/test_web_admin_ca_certs.py`

- [ ] **Step 1: Write the failing integration tests**

```python
# tests/test_web_admin_ca_certs.py
import uuid
import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from web.main import create_app
from harness.db import Database


FAKE_PEM = (
    "-----BEGIN CERTIFICATE-----\n"
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAfake\n"
    "-----END CERTIFICATE-----"
)


def _seed_admin(db) -> dict:
    user = {
        "id": str(uuid.uuid4()),
        "username": "_admin",
        "display_name": "Admin",
        "email": None,
        "password_hash": None,
        "role": "admin",
        "auth_provider": "local",
        "role_override": 0,
        "is_active": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_login_at": None,
    }
    db.insert_user(user)
    return user


def _seed_runner(db) -> dict:
    user = {
        "id": str(uuid.uuid4()),
        "username": "_runner",
        "display_name": "Runner",
        "email": None,
        "password_hash": None,
        "role": "runner",
        "auth_provider": "local",
        "role_override": 0,
        "is_active": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_login_at": None,
    }
    db.insert_user(user)
    return user


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.init_schema()
    _seed_admin(d)
    _seed_runner(d)
    return d


def _make_client(db, user):
    config = {
        "default_environment": "production",
        "environments": {"production": {"label": "Production"}},
    }
    app = create_app(db=db, config=config, apps_dir="apps")
    from web.auth import get_current_user
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


@pytest.fixture
def admin_client(db):
    return _make_client(db, db.get_user_by_username("_admin"))


@pytest.fixture
def runner_client(db):
    return _make_client(db, db.get_user_by_username("_runner"))


def test_list_page_renders_empty(admin_client):
    r = admin_client.get("/admin/ca-certs")
    assert r.status_code == 200
    assert "No CA certificates stored" in r.text


def test_add_cert_via_paste(admin_client, db):
    r = admin_client.post("/admin/ca-certs", data={"name": "Corp CA", "pem_content": FAKE_PEM})
    assert r.status_code in (200, 303)
    certs = db.list_ca_certs()
    assert len(certs) == 1
    assert certs[0]["name"] == "Corp CA"
    assert certs[0]["pem_content"] == FAKE_PEM


def test_add_cert_via_file_upload(admin_client, db, tmp_path):
    pem_file = tmp_path / "test.pem"
    pem_file.write_text(FAKE_PEM)
    with open(pem_file, "rb") as f:
        r = admin_client.post(
            "/admin/ca-certs",
            data={"name": "Upload CA", "pem_content": ""},
            files={"pem_file": ("test.pem", f, "application/octet-stream")},
        )
    assert r.status_code in (200, 303)
    certs = db.list_ca_certs()
    assert len(certs) == 1
    assert certs[0]["name"] == "Upload CA"


def test_add_cert_file_takes_priority_over_paste(admin_client, db, tmp_path):
    pem_file = tmp_path / "test.pem"
    pem_file.write_text(FAKE_PEM)
    with open(pem_file, "rb") as f:
        r = admin_client.post(
            "/admin/ca-certs",
            data={"name": "Priority CA", "pem_content": "should be ignored"},
            files={"pem_file": ("test.pem", f, "application/octet-stream")},
        )
    assert r.status_code in (200, 303)
    cert = db.list_ca_certs()[0]
    assert cert["pem_content"] == FAKE_PEM


def test_add_cert_invalid_pem_rejected(admin_client, db):
    r = admin_client.post("/admin/ca-certs",
                          data={"name": "Bad", "pem_content": "this is not a cert"},
                          follow_redirects=False)
    assert r.status_code == 422
    assert db.list_ca_certs() == []


def test_add_cert_missing_name_rejected(admin_client, db):
    r = admin_client.post("/admin/ca-certs",
                          data={"name": "", "pem_content": FAKE_PEM},
                          follow_redirects=False)
    assert r.status_code == 422
    assert db.list_ca_certs() == []


def test_list_page_shows_cert_after_add(admin_client, db):
    db.insert_ca_cert({
        "id": "cert-show",
        "name": "Visible CA",
        "pem_content": FAKE_PEM,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "added_by": db.get_user_by_username("_admin")["id"],
    })
    r = admin_client.get("/admin/ca-certs")
    assert r.status_code == 200
    assert "Visible CA" in r.text


def test_delete_cert(admin_client, db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db.insert_ca_cert({
        "id": "cert-del",
        "name": "Delete Me",
        "pem_content": FAKE_PEM,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "added_by": None,
    })
    r = admin_client.post("/admin/ca-certs/cert-del/delete")
    assert r.status_code in (200, 303)
    assert db.get_ca_cert("cert-del") is None


def test_non_admin_gets_403(runner_client):
    r = runner_client.get("/admin/ca-certs", follow_redirects=False)
    assert r.status_code == 403


def test_non_admin_cannot_post(runner_client):
    r = runner_client.post("/admin/ca-certs",
                           data={"name": "Corp CA", "pem_content": FAKE_PEM},
                           follow_redirects=False)
    assert r.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_web_admin_ca_certs.py -v
```
Expected: FAIL with 404 (routes don't exist yet)

- [ ] **Step 3: Create `web/routes/admin_ca_certs.py`**

```python
"""Admin CA certificate management routes."""
import os
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from web.auth import require_role

router = APIRouter()
templates = Jinja2Templates(
    directory=os.path.join(os.path.dirname(__file__), "..", "templates")
)


def _ctx(request: Request, current_user: dict, **extra) -> dict:
    from web.main import get_config
    config = get_config()
    return {
        "request": request,
        "environments": config.get("environments", {}),
        "environment": None,
        "current_user": current_user,
        **extra,
    }


def _valid_pem(content: str) -> bool:
    return "-----BEGIN CERTIFICATE-----" in content


@router.get("/admin/ca-certs", response_class=HTMLResponse)
async def ca_certs_list(
    request: Request,
    current_user: dict = Depends(require_role("admin")),
):
    from web.main import get_db
    db = get_db()
    certs = db.list_ca_certs()
    return templates.TemplateResponse(
        request, "admin_ca_certs.html",
        _ctx(request, current_user, certs=certs, error=None),
    )


@router.post("/admin/ca-certs", response_class=HTMLResponse)
async def ca_certs_add(
    request: Request,
    name: str = Form(""),
    pem_content: str = Form(""),
    pem_file: UploadFile = File(None),
    current_user: dict = Depends(require_role("admin")),
):
    from web.main import get_db
    from harness.ssl_context import write_ca_bundle
    db = get_db()

    # File upload takes priority over paste
    if pem_file and pem_file.filename:
        raw = await pem_file.read()
        pem_content = raw.decode("utf-8", errors="replace")

    name = name.strip()
    pem_content = pem_content.strip()

    def _err(msg: str):
        certs = db.list_ca_certs()
        return templates.TemplateResponse(
            request, "admin_ca_certs.html",
            _ctx(request, current_user, certs=certs, error=msg),
            status_code=422,
        )

    if not name:
        return _err("Certificate name is required.")
    if not _valid_pem(pem_content):
        return _err("Content must contain at least one -----BEGIN CERTIFICATE----- block.")

    db.insert_ca_cert({
        "id": str(uuid.uuid4()),
        "name": name,
        "pem_content": pem_content,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "added_by": current_user["id"],
    })
    write_ca_bundle(db)
    return RedirectResponse("/admin/ca-certs", status_code=303)


@router.post("/admin/ca-certs/{cert_id}/delete")
async def ca_certs_delete(
    request: Request,
    cert_id: str,
    current_user: dict = Depends(require_role("admin")),
):
    from web.main import get_db
    from harness.ssl_context import write_ca_bundle
    db = get_db()
    db.delete_ca_cert(cert_id)
    write_ca_bundle(db)
    return RedirectResponse("/admin/ca-certs", status_code=303)
```

- [ ] **Step 4: Run tests to verify they fail on template (404 → 500 or TemplateNotFound)**

```
pytest tests/test_web_admin_ca_certs.py -v
```
Expected: FAIL — `TemplateNotFound: admin_ca_certs.html` (route works but template missing)

- [ ] **Step 5: Commit route (template in next task)**

```bash
git add web/routes/admin_ca_certs.py tests/test_web_admin_ca_certs.py
git commit -m "feat: add admin CA certs routes"
```

---

### Task 8: Template and navigation

**Files:**
- Create: `web/templates/admin_ca_certs.html`
- Modify: `web/templates/base.html`
- Modify: `web/main.py`

- [ ] **Step 1: Create `web/templates/admin_ca_certs.html`**

```html
{% extends "base.html" %}
{% block content %}
<div class="page-header">
  <h1>CA Certificates</h1>
</div>

<div style="max-width:640px;">
  <div class="card">
    <div class="card-title">Add Certificate</div>
    {% if error %}
    <div style="color:var(--fail);font-size:.75rem;margin-bottom:.75rem;">{{ error }}</div>
    {% endif %}
    <form method="post" enctype="multipart/form-data">
      <div style="margin-bottom:.75rem;">
        <label class="label">Name</label>
        <input class="input" name="name" placeholder="Corp Root CA" required>
      </div>
      <div style="margin-bottom:.75rem;">
        <label class="label">PEM Content (paste)</label>
        <textarea class="input" name="pem_content" rows="6"
                  placeholder="-----BEGIN CERTIFICATE-----&#10;...&#10;-----END CERTIFICATE-----"
                  style="font-family:monospace;font-size:.7rem;resize:vertical;"></textarea>
      </div>
      <div style="margin-bottom:.75rem;">
        <label class="label">— or upload a file —</label>
        <input type="file" name="pem_file" accept=".pem,.crt,.cer"
               style="font-size:.75rem;color:var(--text-2);">
      </div>
      <button type="submit" class="btn btn-primary">Add Certificate</button>
    </form>
  </div>

  <div class="card">
    <div class="card-title">Stored Certificates</div>
    {% if certs %}
    <table class="table">
      <thead>
        <tr>
          <th>Name</th>
          <th>Added by</th>
          <th>Date</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        {% for cert in certs %}
        <tr>
          <td style="font-weight:600;">{{ cert.name }}</td>
          <td style="color:var(--text-3);font-size:.72rem;">{{ cert.added_by or "—" }}</td>
          <td style="color:var(--text-3);font-size:.72rem;">{{ cert.created_at[:10] }}</td>
          <td>
            <form method="post" action="/admin/ca-certs/{{ cert.id }}/delete"
                  style="margin:0;"
                  onsubmit="return confirm('Delete {{ cert.name }}?')">
              <button type="submit" class="btn btn-danger"
                      style="font-size:.68rem;padding:.2rem .5rem;">Delete</button>
            </form>
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
    {% else %}
    <p class="empty">No CA certificates stored.</p>
    {% endif %}
  </div>
</div>
{% endblock %}
```

- [ ] **Step 2: Add "CA Certs" nav link in `web/templates/base.html`**

In `base.html`, change line 21 (the LDAP link line) to:

```html
      <a href="/admin/ldap" class="nav-link">LDAP</a>
      <a href="/admin/ca-certs" class="nav-link">CA Certs</a>
```

- [ ] **Step 3: Register the router in `web/main.py`**

After the existing `from web.routes.admin import router as admin_router` / `app.include_router(admin_router)` block (around line 124–125), add:

```python
    from web.routes.admin_ca_certs import router as admin_ca_certs_router
    app.include_router(admin_ca_certs_router)
```

- [ ] **Step 4: Run all CA cert web tests**

```
pytest tests/test_web_admin_ca_certs.py -v
```
Expected: all tests PASS

- [ ] **Step 5: Run the full test suite to check for regressions**

```
pytest -v
```
Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
git add web/templates/admin_ca_certs.html web/templates/base.html web/main.py
git commit -m "feat: add CA certs admin page, template, and nav link"
```

---

## Self-Review

### Spec coverage

| Spec requirement | Covered by |
|-----------------|-----------|
| Admins add/delete named certs via UI | Tasks 7–8 |
| PEM paste input | Task 7 (route), Task 8 (template) |
| File upload input (`.pem`/`.crt`/`.cer`) | Task 7 |
| File takes priority over paste | Task 7, tested in Task 7 |
| `ca_certs` table schema | Task 1 |
| `insert_ca_cert`, `list_ca_certs`, `get_ca_cert`, `delete_ca_cert` | Task 2 |
| `get_ssl_context` with system + stored certs | Task 3 |
| `write_ca_bundle` creates/removes file | Task 3 |
| `BUNDLE_PATH` constant in `ssl_context.py` | Task 3 |
| `run_api_test` gets `ssl_ctx` param | Task 4 |
| `run_availability_test` gets `ssl_ctx` param | Task 5 |
| `run_browser_test` sets `SSL_CERT_FILE` | Task 5 |
| Runner builds SSL context once and passes it | Task 6 |
| Runner calls `write_ca_bundle` each run | Task 6 |
| No certs → no change in behaviour | Tasks 4–6 (verify=True default; bundle absent → no env var) |
| POST validates PEM block present | Task 7 |
| POST validates name not empty | Task 7 |
| Admin-only access (non-admin 403) | Task 7, tested in Task 7 |
| "CA Certs" nav link next to "LDAP" | Task 8 |
| Empty state: "No CA certificates stored." | Task 8 (template) |
| `data/ca-bundle.pem` on disk | Tasks 3 + 6 |
| `added_by` FK stored on insert | Task 7 |
| Unit tests: insert, list, delete, bundle | Tasks 1–3 |
| Integration tests: paste, upload, list, delete, 403 | Task 7 |
| Runtime tests: ssl context, SSL_CERT_FILE | Tasks 4–6 |

### Placeholder scan

No TBD, TODO, or "similar to Task N" references. All code blocks are complete.

### Type consistency

- `ssl_ctx: Optional[ssl.SSLContext] = None` used in `run_api_test` (Task 4), `run_availability_test` (Task 5), and passed by `run_app` (Task 6) — consistent.
- `write_ca_bundle(db)` called in runner (Task 6) and in both route handlers (Task 7) — consistent with signature `write_ca_bundle(db: Database, path: str = BUNDLE_PATH)`.
- `BUNDLE_PATH` imported in `browser.py` (Task 5) and used in tests (Task 5) — consistent source of truth.
