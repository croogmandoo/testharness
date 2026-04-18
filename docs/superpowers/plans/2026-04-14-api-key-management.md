# API Key Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add self-service API key creation, listing, and revocation so users can authenticate to the harness API from external systems using `hth_`-prefixed keys.

**Architecture:** New `api_keys` table in SQLite stores a prefix + SHA-256 hash (never plaintext). `web/auth.py::get_current_user` gains a pre-cookie API-key check — transparent to all existing routes. A new router `web/routes/api_keys.py` handles the CRUD UI; admins see all users' keys on the same page.

**Tech Stack:** Python 3, FastAPI, Jinja2, SQLite (via existing `harness/db.py` pattern), `hashlib` + `secrets` (stdlib), existing `web/auth.py` dependency injection.

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Modify | `harness/db.py` | Add `api_keys` table DDL + 6 new methods |
| Modify | `web/auth.py` | Pre-cookie API-key check in `get_current_user` |
| Create | `web/routes/api_keys.py` | GET + POST routes for /api-keys |
| Create | `web/templates/api_keys.html` | List + create form; one-time key banner |
| Modify | `web/templates/base.html` | Add "API Keys" nav link for all authenticated users |
| Modify | `web/main.py` | Register api_keys router |
| Create | `tests/test_api_key_db.py` | DB-layer unit tests |
| Create | `tests/test_web_api_keys.py` | Integration tests (create, auth, revoke, expiry) |

---

### Task 1: DB schema — `api_keys` table

**Files:**
- Modify: `harness/db.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_api_key_db.py
import pytest
from harness.db import Database

@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.init_schema()
    return d

def test_api_keys_table_created(db):
    """init_schema creates the api_keys table without error."""
    with db._connect() as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='api_keys'"
        ).fetchone()
    assert row is not None
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_api_key_db.py::test_api_keys_table_created -v
```
Expected: FAIL — table does not exist yet.

- [ ] **Step 3: Add the DDL to `harness/db.py`**

Locate the `SCHEMA` string (line 8). Append the following **before** the closing `"""`:

```python
CREATE TABLE IF NOT EXISTS api_keys (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL REFERENCES users(id),
    name         TEXT NOT NULL,
    key_prefix   TEXT NOT NULL,
    key_hash     TEXT NOT NULL,
    expires_at   TEXT,
    created_at   TEXT NOT NULL,
    last_used_at TEXT,
    is_active    INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_api_keys_prefix ON api_keys(key_prefix);
CREATE INDEX IF NOT EXISTS idx_api_keys_user   ON api_keys(user_id);
```

- [ ] **Step 4: Run test to verify it passes**

```
pytest tests/test_api_key_db.py::test_api_keys_table_created -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/db.py tests/test_api_key_db.py
git commit -m "feat: add api_keys table to schema"
```

---

### Task 2: DB methods — insert, lookup, revoke, list, touch

**Files:**
- Modify: `harness/db.py`
- Test: `tests/test_api_key_db.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_api_key_db.py`:

```python
from datetime import datetime, timezone, timedelta

def _make_key_row(user_id: str, *, expires_at=None, is_active=1) -> dict:
    import uuid, hashlib, secrets
    plaintext = "hth_" + secrets.token_urlsafe(30)
    prefix = plaintext[4:12]
    return {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "name": "CI pipeline",
        "key_prefix": prefix,
        "key_hash": hashlib.sha256(plaintext.encode()).hexdigest(),
        "expires_at": expires_at,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_used_at": None,
        "is_active": is_active,
        "plaintext": plaintext,  # only used in test setup, not inserted
    }

def _insert_row(db, row: dict):
    """Insert without the plaintext helper key."""
    r = {k: v for k, v in row.items() if k != "plaintext"}
    db.insert_api_key(r)

def test_insert_and_list_for_user(db):
    db.insert_user({
        "id": "u1", "username": "alice", "display_name": "Alice", "email": None,
        "password_hash": None, "role": "runner", "auth_provider": "local",
        "role_override": 0, "is_active": 1,
        "created_at": datetime.now(timezone.utc).isoformat(), "last_login_at": None,
    })
    row = _make_key_row("u1")
    _insert_row(db, row)
    keys = db.list_api_keys_for_user("u1")
    assert len(keys) == 1
    assert keys[0]["name"] == "CI pipeline"

def test_get_by_prefix(db):
    db.insert_user({
        "id": "u2", "username": "bob", "display_name": "Bob", "email": None,
        "password_hash": None, "role": "runner", "auth_provider": "local",
        "role_override": 0, "is_active": 1,
        "created_at": datetime.now(timezone.utc).isoformat(), "last_login_at": None,
    })
    row = _make_key_row("u2")
    _insert_row(db, row)
    results = db.get_api_key_by_prefix(row["key_prefix"])
    assert len(results) == 1
    assert results[0]["key_hash"] == row["key_hash"]

def test_revoke_own_key(db):
    db.insert_user({
        "id": "u3", "username": "carol", "display_name": "Carol", "email": None,
        "password_hash": None, "role": "runner", "auth_provider": "local",
        "role_override": 0, "is_active": 1,
        "created_at": datetime.now(timezone.utc).isoformat(), "last_login_at": None,
    })
    row = _make_key_row("u3")
    _insert_row(db, row)
    db.revoke_api_key(row["id"], user_id="u3")
    keys = db.list_api_keys_for_user("u3")
    assert keys[0]["is_active"] == 0

def test_admin_revoke_any_key(db):
    db.insert_user({
        "id": "u4", "username": "dave", "display_name": "Dave", "email": None,
        "password_hash": None, "role": "runner", "auth_provider": "local",
        "role_override": 0, "is_active": 1,
        "created_at": datetime.now(timezone.utc).isoformat(), "last_login_at": None,
    })
    row = _make_key_row("u4")
    _insert_row(db, row)
    # admin passes user_id=None to revoke any key
    db.revoke_api_key(row["id"], user_id=None)
    keys = db.list_api_keys_for_user("u4")
    assert keys[0]["is_active"] == 0

def test_touch_last_used(db):
    db.insert_user({
        "id": "u5", "username": "eve", "display_name": "Eve", "email": None,
        "password_hash": None, "role": "runner", "auth_provider": "local",
        "role_override": 0, "is_active": 1,
        "created_at": datetime.now(timezone.utc).isoformat(), "last_login_at": None,
    })
    row = _make_key_row("u5")
    _insert_row(db, row)
    ts = "2026-04-14T12:00:00+00:00"
    db.touch_api_key_last_used(row["id"], ts)
    keys = db.list_api_keys_for_user("u5")
    assert keys[0]["last_used_at"] == ts

def test_list_all_api_keys(db):
    for uid, uname in [("u6", "frank"), ("u7", "grace")]:
        db.insert_user({
            "id": uid, "username": uname, "display_name": uname.title(), "email": None,
            "password_hash": None, "role": "runner", "auth_provider": "local",
            "role_override": 0, "is_active": 1,
            "created_at": datetime.now(timezone.utc).isoformat(), "last_login_at": None,
        })
        _insert_row(db, _make_key_row(uid))
    all_keys = db.list_all_api_keys()
    user_ids = {k["user_id"] for k in all_keys}
    assert "u6" in user_ids and "u7" in user_ids
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_api_key_db.py -v
```
Expected: FAIL for all new tests — methods don't exist yet.

- [ ] **Step 3: Add the 6 DB methods to `harness/db.py`**

Add after the `# ── Secrets ──` section (after `list_secrets`, end of file):

```python
    # ── API Keys ────────────────────────────────────────────────────────────

    def insert_api_key(self, row: dict) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO api_keys "
                "(id, user_id, name, key_prefix, key_hash, expires_at, "
                "created_at, last_used_at, is_active) "
                "VALUES (:id,:user_id,:name,:key_prefix,:key_hash,:expires_at,"
                ":created_at,:last_used_at,:is_active)",
                row,
            )

    def get_api_key_by_prefix(self, prefix: str) -> list:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM api_keys WHERE key_prefix=?", (prefix,)
            ).fetchall()
            return [dict(r) for r in rows]

    def revoke_api_key(self, key_id: str, user_id: Optional[str] = None) -> None:
        with self._connect() as conn:
            if user_id is None:
                conn.execute(
                    "UPDATE api_keys SET is_active=0 WHERE id=?", (key_id,)
                )
            else:
                conn.execute(
                    "UPDATE api_keys SET is_active=0 WHERE id=? AND user_id=?",
                    (key_id, user_id),
                )

    def list_api_keys_for_user(self, user_id: str) -> list:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM api_keys WHERE user_id=? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def list_all_api_keys(self) -> list:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT k.*, u.username FROM api_keys k "
                "JOIN users u ON k.user_id = u.id "
                "ORDER BY k.created_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def touch_api_key_last_used(self, key_id: str, timestamp: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE api_keys SET last_used_at=? WHERE id=?",
                (timestamp, key_id),
            )
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_api_key_db.py -v
```
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/db.py tests/test_api_key_db.py
git commit -m "feat: add api_keys DB methods"
```

---

### Task 3: Auth — API key check in `get_current_user`

**Files:**
- Modify: `web/auth.py`
- Test: `tests/test_web_api_keys.py`

- [ ] **Step 1: Write failing integration tests**

Create `tests/test_web_api_keys.py`:

```python
"""Integration tests for API key auth flow."""
import hashlib, secrets, uuid
from datetime import datetime, timezone, timedelta
import pytest
from fastapi.testclient import TestClient
from harness.db import Database
from web.main import create_app


@pytest.fixture
def client(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.init_schema()
    # Create a test user
    db.insert_user({
        "id": "user1", "username": "tester", "display_name": "Tester",
        "email": None, "password_hash": None, "role": "runner",
        "auth_provider": "local", "role_override": 0, "is_active": 1,
        "created_at": datetime.now(timezone.utc).isoformat(), "last_login_at": None,
    })
    app = create_app(db=db, config={})
    return TestClient(app, raise_server_exceptions=True)


def _make_and_store_key(client_fixture, *, expires_at=None, is_active=1):
    """Helper: insert a key row directly into DB and return the plaintext key."""
    from web.main import get_db
    db = get_db()
    plaintext = "hth_" + secrets.token_urlsafe(30)
    prefix = plaintext[4:12]
    row = {
        "id": str(uuid.uuid4()),
        "user_id": "user1",
        "name": "test key",
        "key_prefix": prefix,
        "key_hash": hashlib.sha256(plaintext.encode()).hexdigest(),
        "expires_at": expires_at,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_used_at": None,
        "is_active": is_active,
    }
    db.insert_api_key(row)
    return plaintext, row["id"]


def test_bearer_auth_succeeds(client):
    plaintext, _ = _make_and_store_key(client)
    resp = client.get("/api/apps",
                      headers={"Authorization": f"Bearer {plaintext}"})
    assert resp.status_code == 200


def test_x_api_key_header_auth_succeeds(client):
    plaintext, _ = _make_and_store_key(client)
    resp = client.get("/api/apps",
                      headers={"X-API-Key": plaintext})
    assert resp.status_code == 200


def test_invalid_key_returns_401(client):
    resp = client.get("/api/apps",
                      headers={"Authorization": "Bearer hth_invalid00000000000"})
    assert resp.status_code == 401


def test_revoked_key_returns_401(client):
    plaintext, key_id = _make_and_store_key(client, is_active=0)
    resp = client.get("/api/apps",
                      headers={"Authorization": f"Bearer {plaintext}"})
    assert resp.status_code == 401


def test_expired_key_returns_401(client):
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    plaintext, _ = _make_and_store_key(client, expires_at=past)
    resp = client.get("/api/apps",
                      headers={"Authorization": f"Bearer {plaintext}"})
    assert resp.status_code == 401


def test_session_cookie_still_works(client):
    """Existing cookie auth must not be broken."""
    from web.auth import make_session_token
    from web.auth import _signing_key, _session_hours
    token = make_session_token("user1", _signing_key, _session_hours)
    resp = client.get("/api/apps", cookies={"session": token})
    assert resp.status_code == 200
```

- [ ] **Step 2: Run the tests to confirm they fail with the right error**

```
pytest tests/test_web_api_keys.py -v
```
Expected: FAIL with 401 on the "succeeds" tests (API key auth not implemented yet).

- [ ] **Step 3: Implement API key check in `web/auth.py`**

Replace the existing `get_current_user` function (lines 38-55) with:

```python
async def get_current_user(request: Request) -> dict:
    import hashlib
    from datetime import datetime, timezone
    from web.main import get_db
    db = get_db()
    is_api = request.url.path.startswith("/api")

    def _not_authed():
        if is_api:
            raise HTTPException(status_code=401, detail="Not authenticated")
        raise HTTPException(status_code=307, headers={"Location": "/auth/login"})

    # --- API key check (runs before cookie) ---
    api_key_value: Optional[str] = None
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer hth_"):
        api_key_value = auth_header[len("Bearer "):]
    if api_key_value is None:
        api_key_value = request.headers.get("X-API-Key") or None
        if api_key_value and not api_key_value.startswith("hth_"):
            api_key_value = None

    if api_key_value is not None:
        prefix = api_key_value[4:12]  # chars after "hth_", first 8
        candidates = db.get_api_key_by_prefix(prefix)
        key_hash = hashlib.sha256(api_key_value.encode()).hexdigest()
        matched = next(
            (c for c in candidates if c["key_hash"] == key_hash), None
        )
        if matched is None or not matched["is_active"]:
            _not_authed()
        if matched["expires_at"]:
            expires = datetime.fromisoformat(matched["expires_at"])
            if expires < datetime.now(timezone.utc):
                _not_authed()
        user = db.get_user_by_id(matched["user_id"])
        if not user or not user.get("is_active"):
            _not_authed()
        now_ts = datetime.now(timezone.utc).isoformat()
        db.touch_api_key_last_used(matched["id"], now_ts)
        return user

    # --- Session cookie fallback ---
    token = request.cookies.get("session", "")
    user_id = _load_token(token)
    if not user_id:
        _not_authed()
    user = db.get_user_by_id(user_id)
    if not user or not user.get("is_active"):
        _not_authed()
    return user
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_web_api_keys.py -v
```
Expected: All PASS.

- [ ] **Step 5: Run the full test suite to check for regressions**

```
pytest -v
```
Expected: All previously passing tests still PASS.

- [ ] **Step 6: Commit**

```bash
git add web/auth.py tests/test_web_api_keys.py
git commit -m "feat: add API key auth check to get_current_user"
```

---

### Task 4: Routes — `/api-keys` CRUD

**Files:**
- Create: `web/routes/api_keys.py`
- Modify: `web/main.py`

- [ ] **Step 1: Write failing route tests**

Append to `tests/test_web_api_keys.py`:

```python
# ── Route tests ──────────────────────────────────────────────────────────

def _session_cookie(user_id="user1"):
    from web.auth import make_session_token, _signing_key, _session_hours
    return {"session": make_session_token(user_id, _signing_key, _session_hours)}


def test_get_api_keys_page_requires_auth(client):
    resp = client.get("/api-keys", follow_redirects=False)
    assert resp.status_code in (307, 401)


def test_get_api_keys_page_authenticated(client):
    resp = client.get("/api-keys", cookies=_session_cookie(), follow_redirects=False)
    assert resp.status_code == 200
    assert b"API Keys" in resp.content


def test_create_key_returns_redirect_with_flash(client):
    resp = client.post(
        "/api-keys",
        data={"name": "My CI", "expiry_days": "30"},
        cookies=_session_cookie(),
        follow_redirects=False,
    )
    # Should redirect back to /api-keys
    assert resp.status_code == 303
    assert resp.headers["location"] == "/api-keys"


def test_created_key_appears_in_list(client):
    client.post(
        "/api-keys",
        data={"name": "Deploy key", "expiry_days": "7"},
        cookies=_session_cookie(),
        follow_redirects=False,
    )
    resp = client.get("/api-keys", cookies=_session_cookie())
    assert b"Deploy key" in resp.content


def test_revoke_own_key(client):
    client.post(
        "/api-keys",
        data={"name": "To revoke", "expiry_days": "never"},
        cookies=_session_cookie(),
        follow_redirects=False,
    )
    from web.main import get_db
    keys = get_db().list_api_keys_for_user("user1")
    key_id = keys[0]["id"]
    resp = client.post(
        f"/api-keys/{key_id}/revoke",
        cookies=_session_cookie(),
        follow_redirects=False,
    )
    assert resp.status_code == 303
    keys_after = get_db().list_api_keys_for_user("user1")
    assert keys_after[0]["is_active"] == 0
```

- [ ] **Step 2: Run to confirm they fail**

```
pytest tests/test_web_api_keys.py -k "route or page or create or revoke" -v
```
Expected: FAIL — router not wired yet.

- [ ] **Step 3: Create `web/routes/api_keys.py`**

```python
"""API key management routes."""
import hashlib
import os
import secrets
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from web.auth import get_current_user, require_role

router = APIRouter()
templates = Jinja2Templates(
    directory=os.path.join(os.path.dirname(__file__), "..", "templates")
)

_EXPIRY_MAP = {
    "7": 7,
    "30": 30,
    "90": 90,
    "365": 365,
    "never": None,
}


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


def _generate_key() -> tuple[str, str, str]:
    """Return (plaintext, prefix, sha256_hex)."""
    plaintext = "hth_" + secrets.token_urlsafe(30)
    prefix = plaintext[4:12]
    key_hash = hashlib.sha256(plaintext.encode()).hexdigest()
    return plaintext, prefix, key_hash


@router.get("/api-keys", response_class=HTMLResponse)
async def api_keys_list(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    from web.main import get_db
    db = get_db()
    # Pop one-time plaintext from cookie-backed flash (stored as query param)
    new_key = request.query_params.get("new_key")
    user_keys = db.list_api_keys_for_user(current_user["id"])
    all_keys = db.list_all_api_keys() if current_user["role"] == "admin" else []
    return templates.TemplateResponse(
        request, "api_keys.html",
        _ctx(request, current_user,
             user_keys=user_keys,
             all_keys=all_keys,
             new_key=new_key),
    )


@router.post("/api-keys")
async def api_keys_create(
    request: Request,
    name: str = Form(...),
    expiry_days: str = Form("never"),
    current_user: dict = Depends(get_current_user),
):
    from web.main import get_db
    db = get_db()
    name = name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Name is required.")

    days = _EXPIRY_MAP.get(expiry_days)
    expires_at: Optional[str] = None
    if days is not None:
        expires_at = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()

    plaintext, prefix, key_hash = _generate_key()
    db.insert_api_key({
        "id": str(uuid.uuid4()),
        "user_id": current_user["id"],
        "name": name,
        "key_prefix": prefix,
        "key_hash": key_hash,
        "expires_at": expires_at,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_used_at": None,
        "is_active": 1,
    })
    # Pass the plaintext key in the redirect query param (shown once, then gone)
    from urllib.parse import quote
    return RedirectResponse(f"/api-keys?new_key={quote(plaintext)}", status_code=303)


@router.post("/api-keys/{key_id}/revoke")
async def api_keys_revoke(
    request: Request,
    key_id: str,
    current_user: dict = Depends(get_current_user),
):
    from web.main import get_db
    db = get_db()
    # Admin can revoke any key; others only their own
    if current_user["role"] == "admin":
        db.revoke_api_key(key_id, user_id=None)
    else:
        db.revoke_api_key(key_id, user_id=current_user["id"])
    return RedirectResponse("/api-keys", status_code=303)
```

- [ ] **Step 4: Register the router in `web/main.py`**

In `web/main.py`, after the `admin_router` include (around line 125), add:

```python
    from web.routes.api_keys import router as api_keys_router
    app.include_router(api_keys_router)
```

- [ ] **Step 5: Run route tests**

```
pytest tests/test_web_api_keys.py -v
```
Expected: All PASS (template not yet created — some tests may still fail with 500).

- [ ] **Step 6: Commit**

```bash
git add web/routes/api_keys.py web/main.py
git commit -m "feat: add /api-keys routes"
```

---

### Task 5: Template — `api_keys.html`

**Files:**
- Create: `web/templates/api_keys.html`

- [ ] **Step 1: Create `web/templates/api_keys.html`**

```html
{% extends "base.html" %}
{% block content %}
<div class="page-header">
  <h1>API Keys</h1>
</div>

{% if new_key %}
<div class="card" style="border:1px solid var(--pass);max-width:720px;">
  <div class="card-title" style="color:var(--pass);">New API Key — copy it now</div>
  <p style="color:var(--text-2);font-size:.75rem;margin-bottom:.6rem;">
    This key will not be shown again. Store it in your CI secret manager or the harness Secrets store.
  </p>
  <div style="display:flex;align-items:center;gap:.5rem;">
    <code id="new-key-display" style="font-size:.78rem;background:var(--surface-2);padding:.4rem .6rem;border-radius:4px;flex:1;word-break:break-all;">{{ new_key }}</code>
    <button class="btn btn-sm" onclick="navigator.clipboard.writeText(document.getElementById('new-key-display').innerText)">Copy</button>
  </div>
</div>
{% endif %}

<div class="card" style="max-width:640px;">
  <div class="card-title">Create API Key</div>
  <form method="post" action="/api-keys">
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:.6rem;margin-bottom:.9rem;">
      <div>
        <label class="label">Name</label>
        <input class="input" name="name" placeholder="e.g. CI pipeline" required>
      </div>
      <div>
        <label class="label">Expires</label>
        <select class="input" name="expiry_days">
          <option value="7">7 days</option>
          <option value="30">30 days</option>
          <option value="90">90 days</option>
          <option value="365">1 year</option>
          <option value="never" selected>Never</option>
        </select>
      </div>
    </div>
    <button type="submit" class="btn btn-primary">Create Key</button>
  </form>
</div>

<div class="card">
  <div class="card-title">Your Keys</div>
  {% if user_keys %}
  <table class="table">
    <thead>
      <tr>
        <th>Prefix</th>
        <th>Name</th>
        <th>Expires</th>
        <th>Last used</th>
        <th>Status</th>
        <th></th>
      </tr>
    </thead>
    <tbody>
      {% for k in user_keys %}
      <tr>
        <td><code style="font-size:.75rem;">hth_{{ k.key_prefix }}…</code></td>
        <td>{{ k.name }}</td>
        <td style="color:var(--text-3);font-size:.7rem;">
          {{ k.expires_at[:10] if k.expires_at else "Never" }}
        </td>
        <td style="color:var(--text-3);font-size:.7rem;">
          {{ k.last_used_at[:16].replace('T',' ') if k.last_used_at else "Never" }}
        </td>
        <td>
          {% if k.is_active %}
            <span style="color:var(--pass);font-size:.65rem;font-weight:700;">Active</span>
          {% else %}
            <span style="color:var(--fail);font-size:.65rem;font-weight:700;">Revoked</span>
          {% endif %}
        </td>
        <td style="text-align:right;">
          {% if k.is_active %}
          <form method="post" action="/api-keys/{{ k.id }}/revoke" style="display:inline;">
            <button type="submit" class="btn btn-sm btn-danger">Revoke</button>
          </form>
          {% endif %}
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  {% else %}
  <p class="empty">No API keys yet.</p>
  {% endif %}
</div>

{% if current_user.role == "admin" and all_keys %}
<div class="card">
  <div class="card-title">All Keys (Admin)</div>
  <table class="table">
    <thead>
      <tr>
        <th>User</th>
        <th>Prefix</th>
        <th>Name</th>
        <th>Expires</th>
        <th>Last used</th>
        <th>Status</th>
        <th></th>
      </tr>
    </thead>
    <tbody>
      {% for k in all_keys %}
      <tr>
        <td style="color:var(--text-2);">{{ k.username }}</td>
        <td><code style="font-size:.75rem;">hth_{{ k.key_prefix }}…</code></td>
        <td>{{ k.name }}</td>
        <td style="color:var(--text-3);font-size:.7rem;">
          {{ k.expires_at[:10] if k.expires_at else "Never" }}
        </td>
        <td style="color:var(--text-3);font-size:.7rem;">
          {{ k.last_used_at[:16].replace('T',' ') if k.last_used_at else "Never" }}
        </td>
        <td>
          {% if k.is_active %}
            <span style="color:var(--pass);font-size:.65rem;font-weight:700;">Active</span>
          {% else %}
            <span style="color:var(--fail);font-size:.65rem;font-weight:700;">Revoked</span>
          {% endif %}
        </td>
        <td style="text-align:right;">
          {% if k.is_active %}
          <form method="post" action="/api-keys/{{ k.id }}/revoke" style="display:inline;">
            <button type="submit" class="btn btn-sm btn-danger">Revoke</button>
          </form>
          {% endif %}
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% endif %}
{% endblock %}
```

- [ ] **Step 2: Run the full test suite**

```
pytest -v
```
Expected: All PASS.

- [ ] **Step 3: Commit**

```bash
git add web/templates/api_keys.html
git commit -m "feat: add api_keys.html template"
```

---

### Task 6: Nav link

**Files:**
- Modify: `web/templates/base.html`

- [ ] **Step 1: Add "API Keys" link to nav**

In `web/templates/base.html`, after the closing `{% endif %}` for the Secrets nav link (line 18), add:

```html
      {% if current_user %}
      <a href="/api-keys" class="nav-link">API Keys</a>
      {% endif %}
```

The block around it currently looks like:
```html
      {% if current_user and current_user.role in ("admin", "runner") %}
      <a href="/secrets" class="nav-link">Secrets</a>
      {% endif %}
```

Insert the new block immediately after that closing `{% endif %}`.

- [ ] **Step 2: Write a test for nav link presence**

Append to `tests/test_web_api_keys.py`:

```python
def test_nav_link_visible_for_authenticated_user(client):
    resp = client.get("/api-keys", cookies=_session_cookie())
    assert b'href="/api-keys"' in resp.content
```

- [ ] **Step 3: Run all tests**

```
pytest -v
```
Expected: All PASS.

- [ ] **Step 4: Commit**

```bash
git add web/templates/base.html tests/test_web_api_keys.py
git commit -m "feat: add API Keys nav link for all authenticated users"
```

---

### Task 7: Final verification

- [ ] **Step 1: Run the complete test suite**

```
pytest -v
```
Expected: All tests PASS. Note the count — every test from `test_api_key_db.py` and `test_web_api_keys.py` must be green.

- [ ] **Step 2: Verify Task 3 Step 3 — find the real API route used in tests**

If you updated the test target in Task 3 Step 3, confirm the route used is real:

```
grep -n "router.get\|router.post" web/routes/api.py | head -20
```

Ensure the test is using a path that requires auth and exists in the app.

- [ ] **Step 3: Smoke-check auth flow manually (optional)**

If a running server is available:
```bash
# Create a key (replace SESSION_COOKIE with a real session token)
curl -s -b "session=SESSION_COOKIE" -d "name=smoke&expiry_days=never" http://localhost:9552/api-keys

# Use the returned key
curl -s -H "Authorization: Bearer hth_XXXX" http://localhost:9552/api/runs
```

- [ ] **Step 4: Final commit if any fixups were needed**

```bash
git add -p
git commit -m "fix: api key management cleanup"
```
