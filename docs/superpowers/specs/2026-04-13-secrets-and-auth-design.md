# Secrets Management & Authentication — Design Spec

**Date:** 2026-04-13  
**Status:** Approved  
**Deployment targets:** Windows Server (service) + Docker (container)

---

## Overview

Two interrelated features added to the Web Testing Harness:

1. **Secrets Management** — encrypted storage of secret values (replaces plain-text `.env`). Secrets are decrypted at test-run time and injected as environment variables, so all existing `$VAR` references in app YAML files continue working unchanged.

2. **Authentication & RBAC** — session-based login with local accounts and LDAP/Active Directory support, plus four roles controlling what each user can do.

---

## Part 1: Secrets Management

### Encryption

- **Library:** `cryptography` — Fernet symmetric encryption (AES-128-CBC + HMAC-SHA256)
- **Key file:** `data/secret.key` — auto-generated on first run if missing
- **Key derivation:** Two keys are derived from the key file via HKDF — one for Fernet (secrets encryption), one for session cookie signing. The raw key file bytes are never used directly.
- **At rest:** Only Fernet-encrypted blobs are stored in the database. The key never touches the DB.
- **At runtime:** `SecretsStore.inject_to_env()` decrypts all secrets and sets them as `os.environ` entries before each test run. Existing `$VAR` references in app YAML files require no changes.

### Key File

- Path: `data/secret.key`
- Format: Fernet URL-safe base64-encoded 32-byte random key (text file, single line)
- Generated automatically on first startup if absent
- **Must be backed up and migrated manually** — see `docs/secrets-migration.md`

### Database Schema — `secrets` table

```sql
CREATE TABLE secrets (
    id              TEXT PRIMARY KEY,
    name            TEXT UNIQUE NOT NULL,   -- variable name, e.g. RADARR_PASSWORD
    encrypted_value TEXT NOT NULL,          -- Fernet-encrypted value
    description     TEXT,                   -- optional human note
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    updated_by      TEXT REFERENCES users(id)
);
```

### New module: `harness/secrets_store.py`

`SecretsStore` class responsibilities:
- Load or generate `data/secret.key` on init
- `set(name, value)` — encrypt and upsert
- `get(name) -> str` — decrypt and return
- `delete(name)` — remove from DB
- `list() -> list[dict]` — return names, descriptions, timestamps (never values)
- `inject_to_env()` — decrypt all secrets and set as `os.environ` entries

---

## Part 2: Authentication

### Session Mechanism

- **Library:** `itsdangerous.URLSafeTimedSerializer`
- **Cookie:** `session` — HttpOnly, SameSite=Lax. `Secure` flag toggled via `auth.secure_cookie` in `config.yaml` (set `true` for HTTPS deployments)
- **Session content:** signed `user_id` only — no sensitive data in the cookie
- **Expiry:** configurable via `auth.session_hours` (default: 8)
- **Revocation:** no sessions table — revocation is achieved by setting `users.is_active = 0`, checked on every request

### First-Run Setup

On startup, if the `users` table is empty, all routes redirect to `/setup`. The setup page creates the first admin (local auth). Once one user exists, `/setup` returns 404.

### Auth Resolution Order

A single login form handles both providers. On `POST /auth/login` the server applies this order:

1. Look up `username` in the `users` table with `auth_provider = 'local'`
2. If found → verify bcrypt hash → succeed or fail (no LDAP fallback for local users)
3. If not found locally and LDAP is enabled → attempt LDAP bind with supplied credentials
4. If LDAP bind succeeds → load or create the user row, sync role, set cookie
5. If all attempts fail → return generic "Invalid username or password"

This means local accounts always take priority. To force a user onto LDAP, simply don't create a local account for them.

### Local Auth Flow

```
GET  /auth/login   → render login form
POST /auth/login   → verify username + bcrypt hash
                   → set signed session cookie
                   → redirect to /
POST /auth/logout  → delete cookie → redirect to /auth/login
```

- Passwords hashed with `bcrypt` via `passlib[bcrypt]`
- Login failure always returns generic "Invalid username or password" — no username enumeration

### LDAP / Active Directory Flow

```
POST /auth/login   → no local account found + LDAP enabled
                   → ldap3 bind with supplied credentials against configured server
                   → on success: load or create user row (password_hash = NULL)
                   → sync role from LDAP group membership (re-synced on every login)
                   → set signed session cookie → redirect to /
```

- LDAP users' roles can be overridden locally via the Users admin page (`role_override` flag in DB)
- If `role_override = true`, LDAP group sync is skipped for that user

### LDAP Configuration (config.yaml)

```yaml
auth:
  session_hours: 8
  secure_cookie: false   # set true for HTTPS
  ldap:
    enabled: true
    server: ldap://dc.company.com
    port: 389
    use_tls: true
    base_dn: "DC=company,DC=com"
    user_search_filter: "(sAMAccountName={username})"
    group_search_base: "OU=Groups,DC=company,DC=com"
    group_attribute: "memberOf"
    role_map:
      "CN=Harness-Admins,OU=Groups,DC=company,DC=com": admin
      "CN=Harness-Runners,OU=Groups,DC=company,DC=com": runner
      "CN=Harness-Reporting,OU=Groups,DC=company,DC=com": reporting
      "CN=Harness-ReadOnly,OU=Groups,DC=company,DC=com": read_only
    default_role: read_only
```

---

## Part 3: Role-Based Access Control

### Roles

| Role | Description |
|------|-------------|
| `admin` | Full access — users, secrets, LDAP config, apps, runs, exports |
| `runner` | Can manage apps and trigger runs; cannot touch secrets or users |
| `reporting` | Can view results and export reports; cannot trigger runs or edit |
| `read_only` | View dashboard and results only |

### Permission Matrix

| Action | admin | runner | reporting | read_only |
|--------|:-----:|:------:|:---------:|:---------:|
| View dashboard, run history, test results | ✅ | ✅ | ✅ | ✅ |
| Trigger test runs | ✅ | ✅ | ❌ | ❌ |
| Export PDF / DOCX reports | ✅ | ✅ | ✅ | ❌ |
| Create / edit / delete / archive apps | ✅ | ✅ | ❌ | ❌ |
| Manage secrets (names only — values never shown) | ✅ | ❌ | ❌ | ❌ |
| Manage users (create, edit role, deactivate) | ✅ | ❌ | ❌ | ❌ |
| Configure LDAP settings | ✅ | ❌ | ❌ | ❌ |

### Implementation

Two FastAPI dependencies in `web/auth.py`:

- `get_current_user()` — validates cookie, loads user, checks `is_active`. Redirects HTML routes to `/auth/login` on failure; returns JSON 401 for API routes.
- `require_role(*roles)` — factory returning a dependency that calls `get_current_user()` then checks role membership. Returns 403 page (HTML) or JSON 403 (API) on insufficient role.

The `current_user` object is injected into every template context so the nav bar can hide links the user cannot access.

---

## Part 4: Database Schema — `users` table

```sql
CREATE TABLE users (
    id            TEXT PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    display_name  TEXT,
    email         TEXT,
    password_hash TEXT,               -- NULL for LDAP users
    role          TEXT NOT NULL,      -- admin | runner | reporting | read_only
    auth_provider TEXT NOT NULL DEFAULT 'local',   -- local | ldap
    role_override INTEGER DEFAULT 0,  -- 1 = skip LDAP group sync for this user
    is_active     INTEGER DEFAULT 1,
    created_at    TEXT NOT NULL,
    last_login_at TEXT
);
```

---

## Part 5: New Files & Changed Files

### New files

```
harness/
  secrets_store.py        SecretsStore class

web/
  auth.py                 get_current_user(), require_role() dependencies
  routes/
    auth.py               /setup, /auth/login, /auth/logout
    users.py              /users, /users/new, /users/{id}/edit
    secrets.py            /secrets CRUD
    admin.py              /admin/ldap (config + test-connection)
  templates/
    setup.html
    login.html
    users.html
    user_form.html
    secrets.html           (replaces current read-only version)
    secret_form.html
    admin_ldap.html
    403.html

docs/
  secrets-migration.md    Key file backup, Docker volumes, Windows service, key loss recovery
```

### Changed files

| File | Change |
|------|--------|
| `harness/db.py` | Add `users` and `secrets` table creation; CRUD methods for both |
| `harness/runner.py` | Call `SecretsStore.inject_to_env()` before test execution |
| `web/main.py` | Register 4 new routers; inject `current_user` into template globals |
| `web/routes/apps.py` | Remove `/secrets` route; add `require_role` deps to write endpoints |
| `web/routes/api.py` | Add `require_role` deps to run trigger and delete endpoints |
| `web/routes/dashboard.py` | Add `get_current_user` dep |
| `web/routes/export.py` | Add `require_role("admin", "runner", "reporting")` dep |
| `config.yaml` | Add `auth:` section |
| `requirements.txt` | Add `cryptography`, `passlib[bcrypt]`, `itsdangerous`, `ldap3` |

---

## Part 6: New Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `cryptography` | ≥42 | Fernet encryption, HKDF key derivation |
| `passlib[bcrypt]` | ≥1.7 | bcrypt password hashing for local users |
| `itsdangerous` | ≥2.1 | Signed session cookies |
| `ldap3` | ≥2.9 | LDAP / Active Directory bind and group search |

---

## Part 7: Implementation Phases

This work is split into two sequential phases, each independently shippable:

### Phase 1 — Secrets Management
1. Add `secrets` table schema to `db.py`
2. Implement `harness/secrets_store.py` (key generation, encrypt/decrypt, CRUD, inject_to_env)
3. Wire `inject_to_env()` into `runner.py`
4. Add `web/routes/secrets.py` (full CRUD UI, admin-only)
5. Add templates: `secrets.html`, `secret_form.html`
6. Remove old read-only `/secrets` route from `apps.py`
7. Write `docs/secrets-migration.md`
8. Tests: key generation, encrypt/decrypt round-trip, inject_to_env, route auth guards

### Phase 2 — Authentication & RBAC
1. Add `users` table schema to `db.py`
2. Implement `harness/auth_manager.py` (local password verify, LDAP bind + group sync)
3. Implement `web/auth.py` (cookie helpers, `get_current_user`, `require_role`)
4. Add `web/routes/auth.py` (setup, login, logout)
5. Add `web/routes/users.py` (user management)
6. Add `web/routes/admin.py` (LDAP config + test-connection)
7. Apply `require_role` / `get_current_user` deps to all existing routes
8. Update all templates: nav bar user menu, role-conditional link visibility
9. Add templates: `setup.html`, `login.html`, `users.html`, `user_form.html`, `admin_ldap.html`, `403.html`
10. Tests: login flow, role gates, LDAP sync, first-run redirect

---

## Migration Notes

See `docs/secrets-migration.md` (created in Phase 1) for full detail. Summary:

- `data/secret.key` **must be preserved** across host migrations — back it up with the same care as the database
- Docker: mount `data/` as a named volume so both `harness.db` and `secret.key` persist across container replacements
- Windows service reinstall: copy `data/` directory before uninstalling
- Lost key = encrypted secrets are unrecoverable — all secrets must be re-entered after generating a new key
