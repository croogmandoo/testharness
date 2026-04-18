<!-- generated-by: gsd-doc-writer -->
# Web Testing Harness — Approach Recommendations

## Context

- **Users**: Developers (configure tests) and non-technical ops/QA staff (trigger runs, view results)
- **Applications**: Mix of web UIs (browser-based) and REST APIs
- **Triggers**: Manual on-demand + scheduled automated runs
- **Scale**: 5–20 apps, multiple environments (staging + production)
- **On failure**: Alert team (email/Teams) + log results in UI
- **Infrastructure**: Primarily Windows on-prem servers, some Azure

---

## Options Considered

### Option A — Python + Playwright + FastAPI ✅ Chosen

Python handles everything: Playwright for browser automation, `httpx`/`requests` for API testing, FastAPI for a web UI that ops staff can use to trigger runs and view results. SQLite stores results. Windows Task Scheduler handles scheduling. Teams/email webhooks send alerts.

**Pros:**
- Runs natively on Windows, no Docker required
- One language for everything — easy to maintain
- Playwright is best-in-class for browser testing and also handles API calls
- FastAPI provides a real web UI and REST API your scheduler can call
- Easy to deploy to Azure App Service or a VM later

**Trade-off:** UI is built from scratch, but stays exactly what you need.

---

### Option B — Robot Framework + Allure Reporting

Keyword-driven test automation framework designed for teams with varying technical skill. Tests read almost like plain English. Allure generates rich HTML reports.

**Pros:** Non-technical staff can read (and write) basic tests. Rich reporting out of the box.

**Trade-off:** Has its own DSL to learn, less flexible for custom workflows, no built-in trigger UI — would need to add that separately.

---

### Option C — Node.js + Playwright Test + Express UI

Playwright Test (JS/TS framework) for browser + API testing, Express for the web UI.

**Pros:** Excellent built-in reporting and parallelism. TypeScript gives strong typing for test configs.

**Trade-off:** More setup friction on Windows, less familiar without a JS background, still requires building the trigger UI.

---

## Decision

**Option A selected.** Python is universally readable, runs cleanly on Windows, and Playwright is the best tool for browser automation today. Full control over the UI without a heavyweight framework. The whole stack can grow into Azure without rearchitecting.

---

## Key Dependencies

The following dependencies underpin the auth/RBAC and secrets subsystems added in Phase 2. Versions are pinned in `requirements.txt`.

### `cryptography >= 42.0.0`

Used by `harness/secrets_store.py` for all encryption operations.

- **Fernet** (`cryptography.fernet.Fernet`): AES-128-CBC + HMAC-SHA256 symmetric encryption. Encrypts secret values stored in `harness.db`.
- **HKDF** (`cryptography.hazmat.primitives.kdf.hkdf.HKDF`): Derives two independent 32-byte keys from the single raw key in `data/secret.key` — one for Fernet encryption and one for itsdangerous session signing. Using separate HKDF outputs (with distinct `info` labels `harness-secrets-v1` and `harness-sessions-v1`) ensures the encryption key and the session-signing key are cryptographically independent despite sharing one root key file.

### `bcrypt >= 4.0.0`

Used directly in `harness/auth_manager.py`, `web/routes/auth.py`, and `web/routes/users.py` for local password hashing and verification.

**Critical:** import as `import bcrypt` — **do not use `passlib`**. `passlib`'s `bcrypt` handler is broken with `bcrypt` 5.x (the `__about__` attribute it inspects was removed). All password operations use `bcrypt.hashpw` / `bcrypt.checkpw` / `bcrypt.gensalt` directly.

### `ldap3 >= 2.9.1`

Used by `harness/auth_manager.py` (`ldap_authenticate`) for LDAP/Active Directory authentication.

- Binds as `username@domain` (domain derived from `base_dn` DC components).
- Searches for the user entry, reads `displayName`, `mail`, and the configurable group attribute (default `memberOf`).
- Maps group DNs to harness roles via the `auth.ldap.role_map` config key.
- Returns `None` on bind failure — falls through to a local auth 401.

### `itsdangerous >= 2.1.2`

Used by `web/auth.py` for session cookie signing.

- `URLSafeTimedSerializer` signs a user UUID into the `session` HTTP-only cookie.
- Signatures are verified on every request by `get_current_user`; expired or tampered tokens are rejected automatically.
- The signing key is the HKDF-derived `session_signing_key` from `SecretsStore`, so cookie signatures are bound to the `data/secret.key` file. If that file is replaced, all existing sessions are immediately invalidated.
