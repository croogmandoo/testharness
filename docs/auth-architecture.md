<!-- generated-by: gsd-doc-writer -->
# Authentication & RBAC Architecture

**Status:** Complete (Phase 2 merged to master)
**Tests:** 149 passing

---

## Key Files

```
harness/
  secrets_store.py   SecretsStore — Fernet encryption, HKDF key derivation, inject_to_env()
  auth_manager.py    verify_local_password() · ldap_authenticate()

web/
  auth.py            set_auth_config() · get_current_user() · require_role()
  routes/
    auth.py          GET/POST /setup · GET/POST /auth/login · POST /auth/logout
    users.py         /users CRUD (admin only)
    admin.py         GET /admin/ldap · POST /admin/ldap/test (AJAX)
    secrets.py       /secrets (admin + runner, delete requires admin)
  templates/
    setup.html       First-run admin account creation
    login.html       Login form
    403.html         Access denied page
    users.html       User list table
    user_form.html   Create / edit user form
    admin_ldap.html  LDAP config display + test-connection widget
```

---

## How It Fits Together

```
startup:  create_app()
            └─ SecretsStore(db, key_path="data/secret.key")
                 │   derives two keys via HKDF-SHA256 from data/secret.key
                 ├─ .session_signing_key  (info="harness-sessions-v1")
                 │     └─ set_auth_config()  → used by itsdangerous URLSafeTimedSerializer
                 └─ Fernet key            (info="harness-secrets-v1")
                       └─ encrypts/decrypts secret values in harness.db secrets table

request:  cookie "session" (itsdangerous-signed user_id)
            └─ get_current_user()        validates token, loads user row, checks is_active
                 └─ require_role(...)    checks user["role"] membership → 403 if insufficient

first-run: _FirstRunMiddleware
            └─ if count_users() == 0 → redirect all non-/setup requests to /setup
```

---

## Secrets Storage

`SecretsStore` (`harness/secrets_store.py`) manages two concerns:

1. **Fernet encryption** — secret values are encrypted before writing to the `secrets` table in `data/harness.db` and decrypted on read. The Fernet key is derived from `data/secret.key` via HKDF (info label `harness-secrets-v1`).
2. **Session key derivation** — a separate 32-byte key is derived from the same root key file (info label `harness-sessions-v1`) and exposed as `.session_signing_key` for use by `web/auth.py`.
3. **`inject_to_env()`** — called at startup to populate `os.environ` with all stored secrets so app YAML `$VAR` references resolve correctly.

**The `data/secret.key` file must be preserved across deployments and migrations.** A lost or replaced key file makes all stored secrets unrecoverable and invalidates all active sessions.

---

## AuthManager

`harness/auth_manager.py` provides two authentication functions:

### `verify_local_password(username, password, db)`

- Looks up the user by username; returns `None` if not found, not local, or not active.
- Verifies the password with `bcrypt.checkpw` (direct `import bcrypt` — see note below).
- Returns the full user dict on success.

### `ldap_authenticate(username, password, ldap_cfg)`

- Derives the bind UPN as `username@domain` from the `base_dn` DC components.
- Binds to the configured LDAP server using `ldap3.Connection`.
- Searches for the user entry and reads `displayName`, `mail`, and the group attribute (default `memberOf`).
- Maps group DNs to harness roles via `auth.ldap.role_map`; falls back to `auth.ldap.default_role`.
- Returns `None` on bind failure or exception.

**bcrypt/passlib note:** `passlib` is broken with bcrypt 5.x — the `__about__` attribute it inspects was removed. All password operations use `import bcrypt` directly (`hashpw`, `checkpw`, `gensalt`). Do not introduce `passlib` anywhere in this codebase.

---

## Session Cookies (`web/auth.py`)

| Function | Purpose |
|---|---|
| `set_auth_config(signing_key, session_hours, secure_cookie)` | Called at startup with values from `SecretsStore` and `config.yaml` |
| `get_current_user(request)` | Reads `session` cookie, validates HMAC + expiry, loads user row, checks `is_active` |
| `require_role(*roles)` | FastAPI dependency factory; wraps `get_current_user` and enforces role membership |
| `make_session_token(user_id, ...)` | Creates a signed, time-limited token for the session cookie |
| `load_session_token(token, ...)` | Validates and decodes a session token; returns `user_id` or `None` |

Cookie attributes: `HttpOnly=true`, `SameSite=lax`, `Secure` controlled by `auth.secure_cookie` in `config.yaml`, `Max-Age` = `auth.session_hours * 3600` (default 8 hours).

For API paths (`/api/*`), unauthenticated requests receive HTTP 401. For browser paths, they receive HTTP 307 → `/auth/login`.

---

## Roles & Access

| Role | Trigger runs | Export | Manage apps | Secrets | Users/LDAP |
|------|:---:|:---:|:---:|:---:|:---:|
| `admin` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `runner` | ✅ | ✅ | ✅ | ✅ (create) | ❌ |
| `reporting` | ❌ | ✅ | ❌ | ❌ | ❌ |
| `read_only` | ❌ | ❌ | ❌ | ❌ | ❌ |

Secrets deletion (`POST /secrets/{name}/delete`) is restricted to `admin` only; creation and listing allow `admin` or `runner`.

---

## Auth Flow

**Local auth:**
`POST /auth/login` → `verify_local_password()` → `bcrypt.checkpw` → set `session` cookie → redirect `/`

**LDAP auth (fallback):**
`POST /auth/login` → `verify_local_password()` returns `None` + `auth.ldap.enabled` is true → `ldap_authenticate()` → `db.upsert_ldap_user()` → set `session` cookie → redirect `/`

**LDAP role override:**
If `users.role_override = 1` for an LDAP user, their role is not updated from LDAP group membership on subsequent logins — the locally-set role is preserved.

**First-run setup:**
`_FirstRunMiddleware` intercepts every request when `count_users() == 0` and redirects to `GET /setup`. `POST /setup` creates the initial admin account and sets the session cookie in one step.

**Session revocation:**
Set `users.is_active = 0` in the database. There is no sessions table — `get_current_user` checks `is_active` on every request, so deactivation takes effect immediately on the next request.

---

## Configuration Reference

Relevant keys in `config.yaml`:

| Key | Default | Description |
|---|---|---|
| `auth.session_hours` | `8` | Session cookie lifetime in hours |
| `auth.secure_cookie` | `false` | Set `Secure` flag on session cookie (enable for HTTPS deployments) |
| `auth.ldap.enabled` | `false` | Enable LDAP authentication fallback |
| `auth.ldap.server` | — | LDAP server hostname |
| `auth.ldap.port` | — | LDAP server port (typically 389 or 636) |
| `auth.ldap.base_dn` | — | Base DN for user searches (e.g. `DC=example,DC=com`) |
| `auth.ldap.use_tls` | `false` | Use LDAPS (SSL) connection |
| `auth.ldap.user_search_filter` | — | LDAP filter with `{username}` placeholder |
| `auth.ldap.group_attribute` | `memberOf` | Attribute containing group DNs |
| `auth.ldap.role_map` | `{}` | Map of group DN → harness role |
| `auth.ldap.default_role` | `read_only` | Role assigned when no group DN matches |

---

## Critical Notes

- `data/secret.key` must be preserved across migrations — a lost key makes stored secrets unrecoverable and invalidates all sessions.
- `passlib` is **broken** with bcrypt 5.x — use `import bcrypt` directly everywhere.
- Session revocation: set `users.is_active = 0` (no sessions table; checked on every request).
- LDAP users are upserted into the local `users` table on every successful login (unless `role_override = 1`).
