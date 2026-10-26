# Web Testing Harness (`testharness`) Engineering Rules & Directives

You are developing **Web Testing Harness (`testharness`)**, a self-hosted synthetic monitoring and automated testing platform that executes HTTP availability, API (JSON/REST), and browser (Playwright) tests on a schedule, sends incident alerts, and serves an administrative dashboard with sparklines and public status pages.

---

## 1. Core Engineering Principles

1. **Layer Separation**:
   - **`web/`**: FastAPI application, API routes (`web/routes/`), and Jinja2 templates. Routers must remain thin: request validation, authentication dependencies, and template/JSON response delivery.
   - **`harness/`**: Core test execution engines (`harness/http_engine.py`, `harness/api_engine.py`, `harness/browser.py`), test runner orchestration (`harness/runner.py`), cron scheduler (`harness/scheduler.py`), and alerting (`harness/alerts.py`).
   - **Data Access**: All persistence flows through `harness/db.py` (results, runs, metrics), `harness/auth_db.py` (users, sessions, roles, API keys), and `harness/ca_certs.py` (custom root certificates).
2. **Deterministic Test Suite & Zero Regressions**:
   - The test suite contains 860+ unit and integration tests (868 tests passing).
   - **Always run `.venv/bin/pytest`** before finalizing any change. Never merge or hand off code with failing tests.
   - Browser tests must use mocks or headless fixtures rather than launching full real browsers during CI/testing unless explicitly intended.
3. **Database & Storage Discipline**:
   - Uses SQLite with `PRAGMA journal_mode=WAL;` and busy timeout handlers.
   - Schema migrations and table updates are managed programmatically via `init_db()` in `harness/db.py` and `init_auth_db()` in `harness/auth_db.py`.
4. **Security & Secrets Invariants**:
   - Secrets encryption uses Fernet (`harness/secrets.py`). The encryption key is loaded from `TESTHARNESS_SECRET_KEY` or `data/secret.key` (enforced with strict `0600` file permissions).
   - Never commit `.env`, `data/*.db`, or private keys.
   - Revocable session tokens and role enforcement (Admin, Operator, Viewer) are checked on every protected endpoint.
5. **Async & Playwright Discipline**:
   - FastAPI route handlers and Playwright browser execution run asynchronously (`async def`).
   - Blocking sync calls (LDAP lookups, ReportLab PDF generation, large image diffs) must be offloaded via `asyncio.to_thread(...)`.

---

## 2. Key Documentation Triad Reference

Always consult the documentation triad before making architectural modifications:
- **Architecture Blueprint**: [`docs/OVERVIEW.md`](docs/OVERVIEW.md)
- **Engineering & Code Standards**: [`docs/STANDARDS.md`](docs/STANDARDS.md)
- **Session State & Active Tasks**: [`docs/HANDOFF.md`](docs/HANDOFF.md)
- **Session Kickoff Skill**: [`.agents/skills/kickoff/SKILL.md`](.agents/skills/kickoff/SKILL.md)
- **Session Handoff Skill**: [`.agents/skills/session-handoff/SKILL.md`](.agents/skills/session-handoff/SKILL.md)
