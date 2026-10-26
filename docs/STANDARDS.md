# Web Testing Harness Engineering Standards & Conventions

This document establishes the architecture, code quality, security, and testing standards for all code in **Web Testing Harness (`testharness`)**.

---

## 1. Architecture & Layer Separation

### 1.1 Web Layer (`web/`)
- API endpoints in `web/routes/` must be lean. Handle query/body validation, session verification, status code mapping, and response serialization.
- Do not place heavy test execution logic directly inside HTTP handler bodies; delegate to `harness/runner.py` or background worker tasks.
- Keep HTML views (`templates/`) separate from REST JSON endpoints (`/api/v1/...`).

### 1.2 Harness Engine (`harness/`)
- Pure Python logic with minimal external side effects.
- Engines (`http_engine.py`, `api_engine.py`, `tls_engine.py`, `browser.py`) must be independent, returning typed `TestResult` and `StepResult` models.
- Graceful degradation: `harness/browser.py` must remain safely importable even in environments where Playwright or full browser binaries are not installed.
- Performance & Vitals: Browser journeys should capture Navigation Timing and Web Vitals (TTFB, FCP, LCP, CLS) without blocking UI execution.
- Visual Baselines: Visual comparison diffs highlight regressions with high-contrast heatmaps; baseline promotion is strictly gated by authentication and audit logging.

### 1.3 Database & Storage Layer
- Use SQLite with `PRAGMA journal_mode=WAL;` and `PRAGMA busy_timeout=5000;`.
- Use parameterized queries exclusively (`?` placeholders). Never format or concatenate strings into SQL queries.
- Schema migrations are tracked in code via `init_db()` in `harness/db.py` and `init_auth_db()` in `harness/auth_db.py`.

---

## 2. Python & Asynchronous Discipline

- **Python Version**: Target Python 3.12+. Use modern type annotations (`list[str]`, `dict[str, Any]`, `X | None`).
- **Async vs Sync**:
  - FastAPI endpoints and async HTTP requests must use `async def` and `await`.
  - Do not call blocking synchronous libraries (e.g. `ldap3`, `reportlab`, large disk file writes) directly on the asyncio loop. Use `await asyncio.to_thread(...)`.
- **Error Handling**:
  - Catch specific exceptions (`httpx.RequestError`, `asyncio.TimeoutError`, `sqlite3.OperationalError`).
  - Never allow an unhandled exception inside a test step to crash the runner loop or scheduler.

---

## 3. Testing & Verification Standards

- **Zero Regressions**: All 860+ tests (currently 868 tests) in `tests/` must pass prior to any commit or handoff.
  ```bash
  .venv/bin/pytest
  ```
- **Fixtures & Isolation**:
  - Shared fixtures for test databases (`temp_db`), FastAPI clients (`client`), and authentication tokens reside in `conftest.py`.
  - Tests must never touch the real `data/harness.db` or production secrets. Use in-memory SQLite (`:memory:`) or isolated `tmp_path` directories.
- **Browser Mocking**:
  - Unit tests for `harness/browser.py` should mock Playwright context/pages.
  - Integration tests requiring real browser execution must be explicitly gated or marked so CI runs quickly without dependencies.

---

## 4. Security & Cryptographic Invariants

- **Secrets Storage**:
  - Encryption uses Fernet symmetric keys.
  - The secret key must reside in `data/secret.key` created with atomic write and strict `0600` permissions, or via `TESTHARNESS_SECRET_KEY` environment variable.
  - Secret key files must never be empty or shorter than 32 bytes base64.
- **Session Tokens & Auth**:
  - Session tokens are cryptographically secure, versioned, and revocable.
  - Revoke active user sessions on password change, role change, or account deactivation.
  - Enforce role whitelist (`admin`, `operator`, `viewer`) and minimum password length (8+ chars).
- **Custom CA Injection**:
  - Custom internal certificates are pinned via Chromium launch argument `--ignore-certificate-errors-spki-list` rather than disabling certificate validation entirely.

---

## 5. Git & Commit Guidelines

- Use conventional commit messages:
  - `feat(...)`: New user-facing or runner features
  - `fix(...)`: Bug fixes or security patches
  - `refactor(...)`: Internal cleanup without behavioral change
  - `test(...)`: Adding or updating test fixtures and test cases
  - `docs(...)`: Documentation and handoff updates
