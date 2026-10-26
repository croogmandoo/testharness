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

request:  cookie "session" (itsdangerous-signed [user_id, session_version])
            └─ get_current_user()        validates token, loads user row, checks is_active + session_version
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

The key file is created with mode `0600` and published atomically: the key is written to a private temp file in the same directory and hard-linked into place, so concurrent first boots agree on one key and nobody reads a half-written file (filesystems without hard links fall back to an `O_EXCL` create). A key file that does not decode to exactly 32 bytes (empty, truncated, corrupt) is never used: after a ~2 s retry window startup fails with an error naming the file. On POSIX systems an existing key file readable or writable by group/other is tightened to `0600` at startup, with a warning in the log.

**The `data/secret.key` file must be preserved across deployments and migrations.** A lost or replaced key file makes all stored secrets unrecoverable and invalidates all active sessions.

---

## AuthManager

`harness/auth_manager.py` provides two authentication functions:

### `verify_local_password(username, password, db)`

- Looks up the user by username; returns `None` if not found, not local, not active, or without a password hash. In those cases it still runs `bcrypt.checkpw` against a module-level dummy hash (default cost, generated lazily), so response time doesn't reveal whether a username exists.
- Verifies the password with `bcrypt.checkpw` (direct `import bcrypt` — see note below); the per-account lockout is evaluated after the hash for the same reason.
- Returns the full user dict on success.

### `ldap_authenticate(username, password, ldap_cfg)`

- Returns `None` straight away (no bind attempted) when the password is empty or whitespace-only, since a simple bind with an empty password is an unauthenticated bind on many directories.
- Rejects usernames containing `@`, `\`, NUL or other control characters, or longer than 256 characters, so the caller cannot choose a different bind domain.
- Derives the bind UPN as `username@domain` from the `base_dn` DC components.
- Escapes the username with `ldap3.utils.conv.escape_filter_chars` before substituting it into `auth.ldap.user_search_filter`.
- Returns the account name as the directory spells it, lower-cased: the value of `auth.ldap.username_attribute`, or, when that is unset, of the attribute the search filter compares with `{username}` (e.g. `sAMAccountName`, `uid`). Falls back to the typed name, lower-cased.
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
| `get_current_user(request)` | Reads `session` cookie, validates HMAC + expiry, loads user row, checks `is_active` and that the token's session version equals `users.session_version` |
| `require_role(*roles)` | FastAPI dependency factory; wraps `get_current_user` and enforces role membership |
| `make_session_token(user_id, ..., session_version=0)` | Creates a signed, time-limited token over `[user_id, session_version]` |
| `load_session_claims(token, ...)` | Validates and decodes a session token; returns `(user_id, session_version)` or `None` |
| `load_session_token(token, ...)` | Same, but returns only `user_id` (no version check) |
| `issue_session_cookie(response, user)` | Sets the `session` cookie for `user` at their current `session_version`. Setup, local/LDAP login and the GitHub callback all use it |

Cookie attributes: `HttpOnly=true`, `SameSite=lax`, `Secure` controlled by `auth.secure_cookie` in `config.yaml`, `Max-Age` = `auth.session_hours * 3600` (default 8 hours).

### Revocation

Every user row has an integer `session_version` (default 0). The session token carries the version it was issued at, and `get_current_user` rejects a token whose version no longer matches the row. `db.bump_session_version(user_id)` therefore ends **all** of that user's sessions at once. It is bumped:

- on `POST /auth/logout` (when the presented cookie is still valid). Logging out on one browser signs the user out everywhere;
- in `POST /users/{id}/edit` when the password is reset, the role changes, or the account is deactivated. When an admin changes their own password or role, the browser they did it from gets a fresh cookie; their other sessions end.

Tokens issued before session versioning carry a bare user id. They are read as version 0, so they keep working after the upgrade until the user's first bump. API keys are not session tokens and are unaffected; revoke them separately.

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

`POST /users/new` and `POST /users/{id}/edit` accept only these four role names (`web.auth.VALID_ROLES`); anything else returns 422 and changes nothing.

### Password policy

Local passwords must be at least `auth.min_password_length` characters (default 8). The limit applies to `POST /setup`, `POST /users/new` and to a new password set through `POST /users/{id}/edit`; a shorter password returns 422. The forms carry a matching `minlength` attribute. Existing passwords are not re-checked.

---

## Auth Flow

**Local auth:**
`POST /auth/login` → `verify_local_password()` → `bcrypt.checkpw` → set `session` cookie → redirect `/`

**LDAP auth (fallback):**
`POST /auth/login` → `verify_local_password()` returns `None` + `auth.ldap.enabled` is true → `ldap_authenticate()` → `db.upsert_ldap_user()` → set `session` cookie → redirect `/`

**Login throttling:**
`web/ratelimit.py` keeps in-memory sliding windows per client IP (`request.client.host`). `POST /auth/login` returns **429** with a `Retry-After` header once an IP has recorded 20 failed logins in the last 300 seconds; only failures count, and while the IP is limited even a correct password is refused. `POST /setup` allows 10 submissions per IP per 300 seconds. The windows are per process and reset on restart; each limiter tracks at most 10,000 IPs, sweeping expired entries and then evicting the stalest one when full. Behind a reverse proxy all clients share the proxy's IP unless uvicorn is configured to trust `X-Forwarded-For` (`forwarded_allow_ips`). This sits on top of the per-account lockout (10 failures → 15-minute lock on local accounts).

**GitHub OAuth:**
`GET /auth/oauth/github/login` → GitHub → `GET /auth/oauth/github/callback`. The callback rejects (400) a token response that is not HTTP 200 with a string `access_token`, and a `/user` response that is not HTTP 200 with an integer `id` and a `login` matching `^[A-Za-z0-9-]{1,39}$`. It then resolves the account in this order:

1. A row whose `oauth_provider_id` equals the GitHub numeric id (must be `is_active`).
2. A row named `github:<login>` (matched case-insensitively; new rows are stored lower-cased) — bound to the GitHub id only if its `auth_provider` is `github` and its `oauth_provider_id` is still empty (an admin-pre-provisioned account). A `github:<login>` row already linked to a *different* id, or one owned by another provider, is refused with 403. This blocks a renamed or recycled GitHub login from inheriting someone else's account. Note that when no allowlist is configured, a pre-provisioned row binds to whichever GitHub account first signs in while owning that login.
3. Otherwise a new user is created with `auth.github.default_role` — but only if the login is in `auth.github.allowed_users` (case-insensitive), the user belongs to one of `auth.github.allowed_orgs` (checked per org via `GET /user/memberships/orgs/{org}`, which must report `state: active`; the authorize URL then requests the `read:org` scope), or `auth.github.auto_provision: true`. Otherwise 403.

When `allowed_users` or `allowed_orgs` is set, it is enforced on **every** GitHub login, not just at account creation, so removing someone from the org (or the list) cuts off their access at their next login. `auto_provision: true` without any allowlist lets any GitHub account sign in and logs a warning on the first callback. If GitHub cannot be reached (timeout or network error) the callback returns 502. The GitHub session cookie has the same `Max-Age`, `SameSite=lax` and `Secure` settings as the local-login cookie.

**LDAP / local account collision:**
`upsert_ldap_user()` matches usernames case-insensitively (as directories do) and stores new LDAP rows lower-cased, so `BOB` resolves to the existing `bob` row and keeps its `is_active` and `role_override`. It only ever creates or updates rows whose `auth_provider` is `ldap`. If the directory username matches an existing local or GitHub account in any letter case, the upsert returns `None`, the row is left untouched, the login fails with the generic 401, and an `auth.ldap_conflict` audit event is written. Rename one of the accounts to resolve the clash. An LDAP user whose row has `is_active = 0` is also refused with 401, even when the directory bind succeeds.

**LDAP role override:**
If `users.role_override = 1` for an LDAP user, their role is not updated from LDAP group membership on subsequent logins — the locally-set role is preserved.

**First-run setup:**
`_FirstRunMiddleware` intercepts every request when `count_users() == 0` and redirects to `GET /setup`. `POST /setup` creates the initial admin account and sets the session cookie in one step.

Until the first account exists, anyone who can reach the server could claim admin through `/setup`. To close that race, `POST /setup` requires a **setup token** (form field `setup_token`, compared with `secrets.compare_digest`; a missing or wrong token returns 403):

- If the `HARNESS_SETUP_TOKEN` environment variable is set, its value is the token.
- Otherwise the server generates a random per-process token (`secrets.token_urlsafe(24)`) and logs it once at WARNING as `First-run setup token: <token>` the first time `/setup` is requested while there are no users. A restart generates a new one, and so does each worker process: if you run more than one worker process, set `HARNESS_SETUP_TOKEN` so every process accepts the same token.

`/setup` submissions are also limited to 10 per client IP per 5 minutes (429 after that). Once any user exists, `/setup` returns 404 as before.

**Session revocation:**
See [Revocation](#revocation). Deactivating a user, resetting their password or changing their role from `/users` ends their existing sessions on the next request; logging out ends all of the user's own sessions. There is no sessions table — `get_current_user` checks `is_active` and `session_version` on every request.

---

## Configuration Reference

Relevant keys in `config.yaml`:

| Key | Default | Description |
|---|---|---|
| `auth.session_hours` | `8` | Session cookie lifetime in hours |
| `auth.secure_cookie` | `false` | Set `Secure` flag on session cookie (enable for HTTPS deployments) |
| `auth.min_password_length` | `8` | Minimum length for local passwords (setup, user create, password reset) |
| `auth.github.client_id` | — | GitHub OAuth app client id (enables the GitHub button) |
| `auth.github.client_secret` | — | GitHub OAuth app client secret |
| `auth.github.default_role` | `read_only` | Role given to newly created GitHub users |
| `auth.github.allowed_users` | `[]` | GitHub logins allowed to sign in (case-insensitive) |
| `auth.github.allowed_orgs` | `[]` | GitHub orgs whose members may sign in (adds the `read:org` scope) |
| `auth.github.auto_provision` | `false` | Create accounts for any GitHub user when no allowlist is set |
| `HARNESS_SETUP_TOKEN` (env) | random, logged | Token required by `POST /setup` on first run |
| `auth.ldap.enabled` | `false` | Enable LDAP authentication fallback |
| `auth.ldap.server` | — | LDAP server hostname |
| `auth.ldap.port` | — | LDAP server port (typically 389 or 636) |
| `auth.ldap.base_dn` | — | Base DN for user searches (e.g. `DC=example,DC=com`) |
| `auth.ldap.use_tls` | `false` | Use LDAPS (SSL) connection |
| `auth.ldap.user_search_filter` | — | LDAP filter with `{username}` placeholder |
| `auth.ldap.group_attribute` | `memberOf` | Attribute containing group DNs |
| `auth.ldap.role_map` | `{}` | Map of group DN → harness role |
| `auth.ldap.default_role` | `read_only` | Role assigned when no group DN matches |
| `auth.ldap.username_attribute` | from filter | Directory attribute holding the canonical login name |

---

## Critical Notes

- `data/secret.key` must be preserved across migrations — a lost key makes stored secrets unrecoverable and invalidates all sessions.
- `passlib` is **broken** with bcrypt 5.x — use `import bcrypt` directly everywhere.
- Session revocation: bump `users.session_version` (logout, password reset, role change and deactivation do this); there is no sessions table.
- LDAP users are upserted into the local `users` table on every successful login (unless `role_override = 1`).

---

## Audit Log

Every privileged mutation is recorded in the `audit_log` table.  Admins
can review entries at `/admin/audit` (filterable by user, action, limit).

### Captured actions

| Action | Source | Captured detail |
|---|---|---|
| `user.create` | `POST /users/new` | `role=<role>` |
| `user.update` | `POST /users/{id}/edit` | Changed fields, e.g. `role: viewer -> admin; password reset` |
| `user.delete` | `POST /users/{id}/delete` | — |
| `secret.upsert` | `POST /secrets` | — |
| `secret.delete` | `POST /secrets/{name}/delete` | — |
| `ca_cert.add` | `POST /admin/ca-certs` | — |
| `ca_cert.delete` | `POST /admin/ca-certs/{id}/delete` | — |
| `api_key.create` | `POST /api-keys` | `prefix=<key_prefix>` |
| `api_key.revoke` | `POST /api-keys/{id}/revoke` | — |
| `db.backup` | `GET /admin/backup` | Temp filename |
| `auth.ldap_conflict` | `POST /auth/login` | LDAP username collides with a non-LDAP account |

### Schema

```sql
CREATE TABLE audit_log (
    id          TEXT PRIMARY KEY,
    timestamp   TEXT NOT NULL,        -- ISO 8601 UTC
    user_id     TEXT,                 -- nullable for system events
    username    TEXT,                 -- denormalized snapshot
    action      TEXT NOT NULL,
    target      TEXT,                 -- what was acted on
    details     TEXT                  -- free-form context
);
CREATE INDEX idx_audit_log_timestamp ON audit_log(timestamp DESC);
CREATE INDEX idx_audit_log_user      ON audit_log(user_id, timestamp DESC);
```

### Failure semantics

`Database.log_audit()` swallows DB errors silently.  An audit-log write
failure must NEVER block the action it's auditing — e.g. you should
still be able to delete a malicious user account even if the audit
table is corrupt.  Errors do show up in the harness logs.
