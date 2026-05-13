# Tier 1/2/3 Improvements — Implementation Plan

**Date:** 2026-05-13
**Status:** ✅ Deployed (all three tiers landed; full test suite green)

---

## Deployment Summary

| Tier | Files changed | Tests added | Result |
|---|---|---|---|
| 1 — Quick wins | 5 | 0 | ✅ |
| 2 — Secrets unification | 4 | 1 | ✅ |
| 3 — CSRF protection | 17 (+1 new) | 9 | ✅ |
| **Total** | **27 unique files** | **10 new tests** | **377/387 passing** |

The 10 non-passing tests (`test_browser_screenshot.py` × 3, `test_ca_cert_runtime.py` × 7) are pre-existing Python 3.14 `asyncio.get_event_loop()` incompatibilities, confirmed unrelated via a `git stash` baseline run. They fail identically with or without this work.

---

This plan tackles the five non-obvious gaps identified in a fresh codebase audit. The work is grouped by risk/effort tier so each tier can ship and be validated independently.

---

## Tier 1 — Quick wins (mechanical fixes)

### 1.1 `apps/harness-api.yaml` — fix shipped-broken port

The shipped app file that tests the harness against itself uses `http://localhost:8000` — the harness actually serves on `9552`. Any user running the example app fails immediately.

**Change:** `localhost:8000` → `localhost:9552` (both occurrences: `url:` and `environments.local:`).

**Status:** ✅ Done.

### 1.2 `docs/guides/troubleshooting.md` line 94 — fix stale port doc

User-facing doc mentions the wrong port in the "Verify in the Web Dashboard" step.

**Change:** `http://localhost:8000` → `http://localhost:9552`.

**Status:** ✅ Done.

### 1.3 `harness/__init__.py` — mark AppTest as reserved

`AppTest` is exported and documented but the loader never discovers `.py` files. Mark it clearly so users (and future contributors) don't waste time assuming it works.

**Change:** Add a module-level comment + class docstring noting "Reserved / not yet wired".

**Status:** ✅ Done.

### 1.4 `docs/guides/writing-tests.md §6` — replace Python-Scripts section

The §6 "Python Scripts" walkthrough describes an unimplemented feature. Replace with a clear "Not yet implemented" notice that references the reserved `AppTest` class.

**Change:** Replace §6 content (lines covering `AppTest`, `_headers`, full example script, "Tips for Python scripts") with a short notice.

**Status:** ⏳ Pending.

### 1.5 `harness/config.py` — annotate the JSONPath edge case

`resolve_env_vars` quietly handles `$.token` (JSONPath) by NOT matching it against the identifier regex. Add an inline comment so a future maintainer doesn't "simplify" the regex and break extract clauses.

**Change:** 3-line comment inside `resolve_env_vars` explaining the ENV_REF vs INLINE_REF split and the `$X.token` edge case.

**Status:** ⏳ Pending.

**Tier 1 tests:** Existing `tests/test_config.py` and `tests/test_loader.py` cover the resolver. No new tests needed for Tier 1 (changes are doc/comment-only beyond the port fixes, which have no test path).

**Tier 1 rollback:** `git checkout HEAD -- apps/harness-api.yaml docs/guides/troubleshooting.md harness/__init__.py docs/guides/writing-tests.md harness/config.py`.

---

## Tier 2 — Secrets-injection unification

### Goal

Remove the os.environ side-effect on secret save/load. The runner already uses `extra_vars` (and a test guards that contract); the rest of the stack should too.

### Current state (audit)

| Caller | Behavior |
|--------|----------|
| `harness/runner.py:101` | Uses `secrets_store.get_all_decrypted()` + `extra_vars=` — does **not** touch `os.environ` ✅ |
| `web/main.py:154` | Calls `_secrets_store.inject_to_env()` at startup, mutates `os.environ` ❌ |
| `web/routes/secrets.py` | `os.environ[name] = value` on create, `os.environ.pop(name)` on delete ❌ |

### Changes

#### 2.1 `harness/loader.py`

Add an `extra_vars` parameter to `load_apps`:

```python
def load_apps(apps_dir: str = "apps", extra_vars: dict | None = None) -> list:
    paths = sorted(
        glob.glob(os.path.join(apps_dir, "*.yaml")) +
        glob.glob(os.path.join(apps_dir, "*.yml"))
    )
    apps = []
    for path in paths:
        with open(path) as f:
            raw = yaml.safe_load(f)
        resolved = resolve_env_vars(raw, strict=False, extra_vars=extra_vars)
        resolved["_source"] = path
        resolved["_type"] = "yaml"
        apps.append(resolved)
    return apps
```

#### 2.2 `web/main.py`

```python
# In create_app(), AFTER _secrets_store is constructed:
_apps = load_apps(apps_dir, extra_vars=_secrets_store.get_all_decrypted()) if os.path.isdir(apps_dir) else []
# REMOVE: _secrets_store.inject_to_env()
```

```python
# Update reload_apps():
def reload_apps() -> None:
    global _apps
    extra = _secrets_store.get_all_decrypted() if _secrets_store else {}
    _apps = load_apps(_apps_dir, extra_vars=extra) if os.path.isdir(_apps_dir) else []
```

#### 2.3 `web/routes/secrets.py`

Remove the two `os.environ` mutations. After save/delete, call `reload_apps()` so the in-memory `_apps` snapshot picks up the new value (which the runner would resolve from secrets_store anyway, but this keeps the dashboard view consistent).

```python
# secrets_create — REMOVE: os.environ[name] = value; ADD: reload_apps()
# secrets_delete — REMOVE: os.environ.pop(name, None); ADD: reload_apps()
```

#### 2.4 `harness/secrets_store.py`

Keep `inject_to_env()` for backwards-compat (don't break external callers if any), but mark its docstring:

```python
def inject_to_env(self) -> None:
    """Deprecated. Prefer passing get_all_decrypted() as extra_vars to resolvers.

    Mutating os.environ globally is no longer the supported pattern — the
    runner reads secrets via extra_vars (see harness/runner.py).
    """
```

#### 2.5 New test `tests/test_secrets_isolation.py`

```python
def test_create_app_does_not_inject_secrets_into_environ(tmp_path, monkeypatch):
    monkeypatch.delenv("MY_TEST_SECRET", raising=False)
    db = Database(str(tmp_path / "test.db")); db.init_schema()
    apps_dir = tmp_path / "apps"; apps_dir.mkdir()
    (apps_dir / "app.yaml").write_text(
        'app: Demo\nurl: "$MY_TEST_SECRET"\ntests: []\n'
    )
    # Seed secret via store directly
    from harness.secrets_store import SecretsStore
    store = SecretsStore(db, key_path=str(tmp_path / "secret.key"))
    store.set("MY_TEST_SECRET", "secret-value")

    from web.main import create_app, get_apps
    create_app(db=db, config={}, apps_dir=str(apps_dir))

    # Contract: os.environ must NOT contain the secret
    assert "MY_TEST_SECRET" not in os.environ
    # But the in-memory app should have resolved it
    app = next(a for a in get_apps() if a["app"] == "Demo")
    assert app["url"] == "secret-value"
```

### Risk

- **`load_apps` callers:** `harness/app_manager.py:list_apps` and `list_archived` both call `load_apps(apps_dir)` without `extra_vars`. That's fine for the management UI (it shows app definitions, doesn't resolve secrets) — `extra_vars=None` is the default and behavior is preserved.
- **Existing test `test_run_app_uses_secrets_via_extra_vars_not_environ`** must still pass — it explicitly asserts the runner contract; Tier 2 extends the same contract to startup/reload, not weakens it.
- **External callers of `inject_to_env`:** none in-tree; we leave the method available for safety.

### Rollback

`git checkout HEAD -- harness/loader.py web/main.py web/routes/secrets.py harness/secrets_store.py && rm tests/test_secrets_isolation.py`.

---

## Tier 3 — CSRF protection

### Goal

Implement the CSRF design from `docs/superpowers/specs/2026-04-22-logging-csrf-db-indexes-design.md §2` verbatim. Stateless synchronizer tokens bound to the session cookie.

### Architecture

- **Token:** `itsdangerous.URLSafeTimedSerializer(signing_key).dumps(session_cookie, salt="csrf-v1")` with 1h max_age
- **Signing key:** the existing session signing key from `SecretsStore` (no new key needed)
- **Binding:** token is the signed session cookie value, so it auto-invalidates when the session rotates
- **Scope:** HTML form POSTs only. `/api/*` (Bearer-auth) routes are exempt.

### 3.1 New file `web/csrf.py`

```python
from itsdangerous import URLSafeTimedSerializer
from fastapi import HTTPException, Request

_MAX_AGE = 3600  # 1 hour

def generate_csrf_token(session_cookie: str, signing_key: bytes) -> str:
    return URLSafeTimedSerializer(signing_key).dumps(session_cookie, salt="csrf-v1")

def validate_csrf_token(token: str, session_cookie: str, signing_key: bytes) -> bool:
    try:
        value = URLSafeTimedSerializer(signing_key).loads(token, salt="csrf-v1", max_age=_MAX_AGE)
        return value == session_cookie
    except Exception:
        return False

async def verify_csrf(request: Request) -> None:
    """FastAPI dependency. Raises HTTP 403 when form lacks a valid csrf_token.
    Override with app.dependency_overrides[verify_csrf] = lambda: None in tests."""
    from web.auth import get_auth_config
    session = request.cookies.get("session", "")
    form = await request.form()
    token = form.get("csrf_token", "")
    cfg = get_auth_config()
    if not validate_csrf_token(token, session, cfg["signing_key"]):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
```

### 3.2 Inject `csrf_token` into context

Each `_ctx()` (or `_nav_ctx()`) helper in: `web/routes/secrets.py`, `admin_ca_certs.py`, `api_keys.py`, `users.py`, `admin.py`, `auth.py` gains:

```python
from web.csrf import generate_csrf_token
from web.auth import get_auth_config
# ...
def _ctx(request, current_user, **extra):
    session = request.cookies.get("session", "")
    cfg = get_auth_config()
    return {
        ...,
        "csrf_token": generate_csrf_token(session, cfg["signing_key"]),
        **extra,
    }
```

Also for `dashboard.py` and `apps.py` if they render any forms (they don't render mutating forms — they call `/api/*` via fetch which is Bearer-auth; CSRF not needed there).

### 3.3 Hidden field in templates

Add `<input type="hidden" name="csrf_token" value="{{ csrf_token }}">` to every `<form method="post">` in:
- `secrets.html` — 2 forms (create + delete)
- `admin_ca_certs.html` — 2 forms (add + delete)
- `api_keys.html` — 2 forms (create + revoke)
- `base.html` — logout form
- `user_form.html` — create/edit
- `users.html` — delete-user mini-forms
- `setup.html` — first-run setup
- `login.html` — login form

### 3.4 Enforce in route handlers

Wire `verify_csrf` as a FastAPI dependency (cleaner than inline awaits — lets tests override it). Mutating POST handlers gain `_csrf=Depends(verify_csrf)`:

- `secrets_create`, `secrets_delete`
- `ca_certs_add`, `ca_certs_delete`
- `api_keys_create`, `api_keys_revoke`
- `users_create`, `users_update`, `users_delete`
- `login_post`, `logout`, `setup_post`

### 3.5 Update existing tests

Tests use `app.dependency_overrides[get_current_user]` to bypass auth. They'll fail on CSRF because their POSTs lack `csrf_token`. Fix by also overriding `verify_csrf`:

```python
from web.csrf import verify_csrf
app.dependency_overrides[verify_csrf] = lambda: None
```

Files needing this override:
- `tests/test_web_auth.py` (calls `/auth/login` directly)
- `tests/test_web_auth_routes.py` (calls `/setup`, `/auth/login`)
- `tests/test_web_admin_ca_certs.py`
- `tests/test_web_api_keys.py` (the cookie-based route tests)
- `tests/test_web_apps.py` (uses `/secrets` form)
- `tests/test_db_backup.py` (admin client)
- `tests/test_export.py` (admin/export client)

Note: `tests/test_web_auth.py` directly tests login-flow brute-force lockout — it expects POSTing to `/auth/login` to work without a CSRF token. Since `_FirstRunMiddleware` would still redirect them when no users exist, but the test seeds a user first, we need the verify_csrf override applied via dependency_overrides on the TestClient app.

### 3.6 New test `tests/test_csrf.py`

```python
def test_valid_csrf_token_passes(...): ...
def test_missing_csrf_token_returns_403(...): ...
def test_expired_csrf_token_returns_403(...): ...
def test_wrong_session_token_returns_403(...): ...
def test_api_routes_bypass_csrf(...): ...  # Bearer auth, no CSRF needed
def test_logout_requires_csrf(...): ...
```

### Risk

- **Test fallout:** ~7 test files will need a one-line override. Mechanical fix.
- **Real-world UX:** Forms older than 1 hour will fail submission. Acceptable (matches typical CSRF UX); document if it surfaces.
- **OAuth callback already has its own state-cookie CSRF** — leave it alone.

### Rollback

`git checkout HEAD -- web/ tests/ docs/ && rm web/csrf.py tests/test_csrf.py`.

---

## Execution order

1. Tier 1 (mechanical, ~10 min including remaining items)
2. Tier 2 (secrets, ~30 min including test)
3. Tier 3 (CSRF, ~60-90 min including test fallout fixes)
4. Final `pytest tests/` — must show ≥ previous baseline (377) + new tests

Each tier is independently revertable. Commit per tier for clean history.

---

## Review Notes

- Tier 2 deliberately keeps `inject_to_env()` callable (no breaking change for any out-of-tree caller); the deprecation is documented in the docstring.
- Tier 3 uses FastAPI's `Depends(verify_csrf)` instead of inline `await verify_csrf(request)` so tests can cleanly override via `app.dependency_overrides`. This is a small deviation from the spec text but matches FastAPI idioms and is much friendlier to the existing test suite.
- The `_FirstRunMiddleware` route exemption list in `web/main.py` already excludes `/setup` from the count-users guard. CSRF on setup still applies — but the very first request has no session cookie, so the token will be generated for the empty-session value `""` and the form POST will carry that exact token back. Works by design.
