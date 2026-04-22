# Logging, CSRF Protection, and DB Index Improvements

**Date:** 2026-04-22  
**Status:** Approved  
**Addresses:** Security review findings I-1, I-2, I-7 + structured logging feature

---

## Overview

Three improvements shipped together because they all touch the same core files (db.py, route handlers, templates) and have no inter-dependencies that require sequencing.

1. **Structured logging** — replace all `print()` calls with Python's `logging` module; add a `logging:` section to `config.yaml` that controls log level and optional file output to a configurable directory.
2. **CSRF protection** — add synchronizer tokens to all state-mutating HTML form POST routes.
3. **DB indexes** — add two missing indexes and fix the dashboard's triple-connection hot path.

---

## 1. Structured Logging

### Config

New optional section in `config.yaml`:

```yaml
logging:
  level: INFO           # DEBUG | INFO | WARNING | ERROR | CRITICAL (default: INFO)
  dir: logs/            # path relative to CWD or absolute; omit for console-only
  max_bytes: 10485760   # per-file rotation size (default: 10 MB)
  backup_count: 5       # rotated files to keep (default: 5)
```

When `dir` is omitted, only the console handler is active. When set, a `RotatingFileHandler` writing to `{dir}/harness.log` is added alongside the console handler.

### Implementation

**New file: `harness/logging_config.py`**

```python
import logging
import logging.handlers
import os

def setup_logging(cfg: dict) -> None:
    level = getattr(logging, cfg.get("level", "INFO").upper(), logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")

    root = logging.getLogger()
    root.setLevel(level)

    # Console handler — always present
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    root.addHandler(ch)

    # File handler — only when dir is configured
    log_dir = cfg.get("dir")
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            os.path.join(log_dir, "harness.log"),
            maxBytes=cfg.get("max_bytes", 10 * 1024 * 1024),
            backupCount=cfg.get("backup_count", 5),
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)
```

`setup_logging` is called once at startup:
- In `web/main.py` `create_app()` — after config is loaded
- In `web/main.py` `main()` — for the CLI entry point

### Print → Logging Migration

| File | Calls | New logger |
|------|-------|-----------|
| `harness/alerts.py` | 5 `print()` | `logging.getLogger(__name__)` — WARNING level |
| `harness/scheduler.py` | 2 `print()` | `logging.getLogger(__name__)` — INFO level |

The runner's existing `_log = logging.getLogger(__name__)` (added in PR#7) already follows this pattern.

### Docker

`docker-compose.yml` gets a commented example:
```yaml
# volumes:
#   - ./logs:/app/logs   # bind-mount log directory; set logging.dir: /app/logs in config.yaml
```

---

## 2. CSRF Protection

### Threat Model

State-mutating HTML form POSTs (secrets, CA certs, API keys, user management, logout) are vulnerable to cross-site request forgery. The session cookie uses `SameSite=Lax`, which provides partial protection for top-level navigations but not all POST scenarios. A synchronizer token adds defense in depth.

API routes (`/api/...`) are exempt — they authenticate via Bearer token in the `Authorization` header, which browsers do not send cross-origin without explicit `fetch` calls.

### Token Design

**Stateless synchronizer token** using `itsdangerous.URLSafeTimedSerializer` (already a project dependency):

- Token = signed serialization of the session cookie value
- Salt: `"csrf-v1"`
- Expiry: 1 hour (independent of session expiry)
- Signing key: the session signing key from `SecretsStore` (already derived via HKDF)
- Tying the token to the session cookie means it is automatically invalidated if the cookie changes

**New file: `web/csrf.py`**

```python
from itsdangerous import URLSafeTimedSerializer, BadSignature

_MAX_AGE = 3600  # 1 hour

def generate_csrf_token(session_cookie: str, signing_key: bytes) -> str:
    s = URLSafeTimedSerializer(signing_key)
    return s.dumps(session_cookie, salt="csrf-v1")

def validate_csrf_token(token: str, session_cookie: str, signing_key: bytes) -> bool:
    s = URLSafeTimedSerializer(signing_key)
    try:
        value = s.loads(token, salt="csrf-v1", max_age=_MAX_AGE)
        return value == session_cookie
    except Exception:
        return False

async def verify_csrf(request) -> None:
    """Call at the top of any state-mutating POST route. Raises HTTP 403 on failure."""
    from fastapi import HTTPException
    from web.auth import get_auth_config
    session = request.cookies.get("session", "")
    form = await request.form()
    token = form.get("csrf_token", "")
    cfg = get_auth_config()
    if not validate_csrf_token(token, session, cfg["signing_key"]):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
```

### Template Integration

A `csrf_token` variable is injected into every route's template context. Each route file's `_ctx()` helper is updated to include:

```python
from web.csrf import generate_csrf_token
from web.auth import get_auth_config

def _ctx(request, current_user, **extra):
    session = request.cookies.get("session", "")
    cfg = get_auth_config()
    return {
        "request": request,
        "csrf_token": generate_csrf_token(session, cfg["signing_key"]),
        ...
        **extra,
    }
```

Hidden field added to all destructive forms:
```html
<input type="hidden" name="csrf_token" value="{{ csrf_token }}">
```

**Templates requiring the hidden field (7 files):**

| Template | Forms |
|----------|-------|
| `secrets.html` | create secret, delete secret |
| `admin_ca_certs.html` | add cert, delete cert |
| `api_keys.html` | create key, revoke key |
| `base.html` | logout button |
| `user_form.html` | update user |
| `users.html` | deactivate/delete user |
| `setup.html` | first-run admin creation |

**Route handlers requiring `await verify_csrf(request)`:**

All state-mutating POST routes in: `web/routes/secrets.py`, `web/routes/admin_ca_certs.py`, `web/routes/api_keys.py`, `web/routes/auth.py` (logout), `web/routes/users.py` (update/delete), `web/routes/auth.py` (setup post).

`web/routes/auth.py` login POST is also updated for consistency (prevents session fixation via CSRF).

### Error Handling

A 403 CSRF failure renders the `403.html` template (already handled by the existing `_http_exc_handler` in `web/main.py`).

---

## 3. DB Indexes and Dashboard Fix

### Missing Indexes

Two `CREATE INDEX IF NOT EXISTS` statements added to `SCHEMA` in `harness/db.py`:

```sql
-- Used by dashboard active-run query (WHERE environment=? AND status=?)
CREATE INDEX IF NOT EXISTS idx_runs_env_status
    ON runs(environment, status);

-- Used by app detail page (WHERE app=? AND environment=? ORDER BY started_at DESC)
CREATE INDEX IF NOT EXISTS idx_runs_app_env
    ON runs(app, environment, started_at DESC);
```

These take effect immediately on next startup via `init_schema()` which runs `executescript(SCHEMA)`.

### Dashboard Connection Fix

`get_app_summary` in `harness/db.py` currently calls `_connect()` three times (one per query). Refactored to open a single connection and run all three queries within it:

```python
def get_app_summary(self, environment: str) -> dict:
    with self._connect() as conn:
        # query 1: app_state
        # query 2: last_runs  
        # query 3: active_rows
        ...
```

---

## Testing

- **Logging:** Test that `setup_logging` with `dir` set creates `harness.log` in that directory; test console-only when `dir` omitted; test level filtering.
- **CSRF:** Test that valid token passes; expired token rejected; wrong-session token rejected; missing token returns 403; API routes bypass validation.
- **DB indexes:** Verify indexes exist after `init_schema()` via `sqlite_master` query. Test `get_app_summary` returns correct structure with single-connection implementation.

---

## Docker Deployment Notes

To enable file logging in Docker, add a bind mount and set the log dir in config:

```yaml
# docker-compose.yml
volumes:
  - ./data:/app/data
  - ./apps:/app/apps
  - ./config.yaml:/app/config.yaml
  - ./logs:/app/logs        # add this line
```

```yaml
# config.yaml
logging:
  level: INFO
  dir: /app/logs
```
