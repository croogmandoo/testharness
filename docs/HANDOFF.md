# Web Testing Harness Session Handoff & Operational Runbook

**Generated:** 2026-10-27T15:02:00-04:00  
**Branch:** `master` (synchronized with `origin/master`)  
**Workspace:** `testharness`  
**Test Suite:** **897 / 897 tests passing** (100% green, ~38s)

---

## 1. System Snapshot & Live State

- **Application Port**: `http://localhost:9552` (Docker default) or local development via `uvicorn web.main:create_app --factory --port 9552`
- **Environment**: Python 3.12.15 in local `.venv` (managed via `uv`)
- **Database Engine**: SQLite 3 (WAL mode)
  - Test / Metrics DB: `data/harness.db`
  - Authentication DB: `data/auth.db`
- **Secrets Key**: `data/secret.key` (Fernet 32-byte key, enforced `0600` permissions)
- **Primary Dependencies**: FastAPI, Uvicorn, Playwright, SQLite3, Jinja2, Pytest, Pillow, ReportLab, Cryptography, APScheduler
- **GitHub Task Tracking**: Issues #8 through #23 closed and verified on `croogmandoo/testharness`.
- **Linear Project**: `Web Testing Harness (testharness)` on `Croogmando-homelab` (`CRO-47`, `CRO-49` - Completed).
- **Observability Assets**: Prometheus alerting rules in [deploy/prometheus/alerts.yml](deploy/prometheus/alerts.yml) and Grafana dashboard in [deploy/grafana/dashboard.json](deploy/grafana/dashboard.json).

---

## 2. Recent Work & Hardening Accomplishments

1. **Custom Request Headers, SAML Session Reuse (`storage_state`), & Certificate Status UI (COMPLETED)**:
   - **Custom Headers with Secret Expansion**: In [harness/browser.py](harness/browser.py), added header support using `page.set_extra_http_headers()` with recursive `$VAR` resolution for sensitive API keys, tokens, or forward-auth headers (`Remote-User`, `Authorization: Bearer <token>`). Also wired into [harness/journey_studio.py](harness/journey_studio.py) live screencast sessions and [web/routes/journey.py](web/routes/journey.py).
   - **SAML Session State Reuse**: In [harness/browser.py](harness/browser.py), added Playwright `storage_state` loading from custom config or auto-resolved `data/storage_states/{app}_{env}.json`. Added `save_storage_state` action step and post-run persistence, plus `add_cookies` action step to handle authenticated journeys without repeated IdP logins.
   - **Certificate Status UI**:
     - Added `tls` (SSL / Certificate) test type to [web/templates/app_form.html](web/templates/app_form.html) with settings for target host, expiration threshold, and security headers.
     - Added dynamic Key-Value headers manager in [web/templates/app_form.html](web/templates/app_form.html) for both API and Browser tests.
     - Added SSL certificate status badges (`🔒 45d`, `⚠️ 12d`, `🔒 expired`) to app cards on [web/templates/dashboard.html](web/templates/dashboard.html).
     - Added prominent SSL/TLS Certificate banner to [web/templates/detail.html](web/templates/detail.html) with days remaining, issuer, expiry date, and status color coding.
     - Added `get_latest_cert_statuses()` on `AppStateMixin` in [harness/db/_app_state.py](harness/db/_app_state.py).
     - Full test suite verified (897 tests passing).

2. **Guided Journey Creator – In-Browser Interactive Studio Modal & URL Recording (Issue #22 / Linear CRO-49 - COMPLETED)**:
   - Added [harness/journey_studio.py](harness/journey_studio.py) implementing `JourneySession` and `JourneyStudioManager`. Uses Playwright CDP screencasting (`Page.startScreencast` / `Page.screencastFrameAck`) to stream interactive live frames, resolve elements at `(x, y)` via `document.elementFromPoint`, synthesize robust CSS/text selectors (`#id`, `button:has-text()`, `[name=]`), and execute click, fill, assert, and screenshot actions.
   - Added [web/routes/journey.py](web/routes/journey.py) exposing REST endpoints (`POST /api/journey/start`, `POST /api/journey/{id}/action`, `POST /api/journey/{id}/stop`) and real-time bidirectional WebSocket (`/ws/journey/{session_id}`).
   - Embedded interactive Journey Studio modal in [web/templates/app_form.html](web/templates/app_form.html) allowing users to click "Launch Journey Studio (Interactive)", view the live screencast canvas, click directly on elements to record actions or use quick action buttons (Assert Text, Fill Input, Screenshot), and insert recorded steps directly into the test editor with a single click.
   - Added [harness/url_recorder.py](harness/url_recorder.py) implementing headless URL recording via Playwright `framenavigated` event subscription, relative path normalization against app `base_url`, and consecutive duplicate collapse.
   - Integrated into `run_browser_test` in [harness/browser.py](harness/browser.py) via `record_url_steps=True` flag, automatically synthesizing and prepending `StepResult` navigation steps to the test log while ensuring web vitals and performance threshold evaluations remain intact.
   - Comprehensive test suite in [tests/test_journey_studio.py](tests/test_journey_studio.py) and [tests/test_url_recorder.py](tests/test_url_recorder.py) (895 tests passing).
   - Fully documented in [docs/guides/url_recorder_spec.md](docs/guides/url_recorder_spec.md), [docs/guides/writing-tests.md](docs/guides/writing-tests.md), and [docs/OVERVIEW.md](docs/OVERVIEW.md).

3. **Notification Groups Management & Targeted App Alert Routing (Issue #23 - CLOSED)**:
   - Added [harness/db/_notification_groups.py](harness/db/_notification_groups.py) with `NotificationGroupsMixin` supporting group creation, listing, updating, deletion, and email address resolution (`resolve_emails_for_groups`).
   - Integrated into [harness/db/_base.py](harness/db/_base.py) with automatic schema migration (`notification_groups` table with unique name index).
   - Added full REST API endpoints in [web/routes/api.py](web/routes/api.py) (`GET`, `POST`, `PUT`, `DELETE /api/notification-groups`) with RBAC enforcement (`admin`, `runner`), input validation, and audit logging.
   - Built Admin UI in [web/routes/admin_notifications.py](web/routes/admin_notifications.py) and [web/templates/admin_notifications.html](web/templates/admin_notifications.html) with group creation, email badges, edit dialogs, delete confirmations, and assigned app cross-referencing.
   - Integrated notification group selection into application forms ([web/templates/app_form.html](web/templates/app_form.html), [web/routes/apps.py](web/routes/apps.py)) allowing one or more groups to be assigned to an application.
   - Enhanced alert engine in [harness/alerts.py](harness/alerts.py), [harness/runner.py](harness/runner.py), and [harness/worker_dispatch.py](harness/worker_dispatch.py) to resolve assigned groups, deduplicate recipient emails, and dynamically route alert emails to assigned groups with seamless fallback to global SMTP settings.
   - Verified 14 comprehensive unit and integration tests in [tests/test_notification_groups.py](tests/test_notification_groups.py).

4. **Multi-Environment Selector & Persistent Cookie Filtering (Issue #20 - CLOSED)**:
   - Added interactive environment selector chips (`.env-chips`, `.env-chip`) to the dashboard header in [web/templates/dashboard.html](web/templates/dashboard.html) and breadcrumb bar in [web/templates/detail.html](web/templates/detail.html).
   - Added persistent cookie resolution (`selected_environment`) in [web/routes/dashboard.py](web/routes/dashboard.py), persisting chosen environments across dashboard views and app details.
   - Tested in [tests/test_web_environment_filter.py](tests/test_web_environment_filter.py).

5. **Custom Webhook Payload Templating & Incident Webhook Dispatch (Issue #21 - CLOSED)**:
   - Added Jinja2 template rendering (`_render_webhook_payload`) in [harness/alerts.py](harness/alerts.py) for outbound run webhooks and incident alerts.
   - Added pre-packaged formats for PagerDuty Events API v2 (`format: "pagerduty"`) and Opsgenie Alerts API (`format: "opsgenie"`).
   - Added support for custom headers (`headers: {...}`) and HMAC-SHA256 payload signing (`X-Harness-Signature`).
   - Enabled incident alert webhooks in `dispatch_alerts()` supporting both single webhook dicts and lists of webhook endpoints.
   - Tested in [tests/test_alerts_templating.py](tests/test_alerts_templating.py).

6. **TLS Certificate Expiration & Security Headers Prober (Issue #17 - CLOSED)**:
   - Added [harness/tls_engine.py](harness/tls_engine.py) implementing `run_tls_test` and `inspect_cert_sync` using Python `ssl` and `socket`.
   - Validates TLS certificate expiration date against configurable threshold (`min_days_valid`, default 30 days).
   - Verifies Subject Alternative Names (SANs) match the target hostname and checks negotiated TLS protocol against `min_tls_version` (e.g. `TLSv1.2` or `TLSv1.3`).
   - Checks security headers (`Strict-Transport-Security`, `X-Content-Type-Options`, `Content-Security-Policy`) over HTTPS responses.
   - Tested in [tests/test_tls_engine.py](tests/test_tls_engine.py).

---

## 3. Key Architectural Invariants & Gotchas

- **Header and Secret Invariant**: Custom headers support `$VAR` expansion. Missing variables are kept literally so templated strings don't fail, but configured secrets (`TESTHARNESS_SECRET_KEY`, etc.) should always be injected into runner environments.
- **SAML Storage State File Hierarchy**: Browser execution checks `context_kwargs["storage_state"]` first; if not provided, it falls back to checking `data/storage_states/{app}_{env}.json`. Saving state writes back atomically.
- **Certificate Data Aggregation**: App state queries extract certificate expiration from the latest passing TLS tests via `get_latest_cert_statuses()`. The detail view parses `step_log` and displays an amber alert if `< 30` days or red if expired.
- **Notification Group Email Deduplication**: `db.resolve_emails_for_groups(groups)` deduplicates and sorts emails across all assigned groups. If an app has no notification groups or none resolve to emails, alert dispatching falls back to global `alerts.email.to`.
- **Playwright Fallback**: `harness/browser.py` is written to be cleanly importable even on headless systems without Playwright installed; keep browser imports lazy inside test execution routines.
- **Visual Baseline Invariant**: `get_last_screenshot` only queries `status='pass'` so failures do not contaminate subsequent baseline comparisons unless explicitly approved by an operator or admin.
- **SQLite Concurrency**: Always keep `PRAGMA journal_mode=WAL;` and `PRAGMA busy_timeout=5000;` on connection pools to prevent `database is locked` during concurrent background runs and web requests.
- **Test Isolation**: `conftest.py` provides shared memory databases and test client fixtures. Never let test execution write to or mutate `data/harness.db`.

---

## 4. Prioritized Next Actions (Top 5)

1. **[Scheduled Maintenance Window Recurrence]**:
   - Add recurring maintenance window schedules (e.g. weekly Sunday 02:00-04:00 UTC) with cron expressions in addition to one-off datetime windows.
2. **[Multi-Factor / TOTP Auth Helper in Studio & Runner]**:
   - Provide built-in TOTP generation helper (`totp_secret` or `totp_token`) for journeys requiring two-factor authentication during synthetic runs.
3. **[Automated Synthetic SSL Renewal Verification Alerting]**:
   - Trigger automated immediate TLS re-checks when certificate expiry transitions from <30d to renewed (>60d) and emit resolution alerts.
4. **[Windows-Level Control & Desktop Automation (Linear CRO-47)]**:
   - Evaluate hybrid browser + desktop automation (`rpaframework`, `pywinauto`) for Windows-hosted remote workers testing native desktop clients and OS dialogs.
5. **[Out-of-the-Box OpenID Connect / OAuth2 Test Step Helper]**:
   - Add first-class OAuth2 PKCE authorization code grant step helper to browser engine for IdPs requiring live browser-driven grant flows.

---

## 5. Kickoff Prompt for Next Session

```text
I am resuming work on Web Testing Harness (testharness).
Please read docs/HANDOFF.md, docs/OVERVIEW.md, and docs/STANDARDS.md to understand the current architecture and state.
All 897 tests are passing on master in .venv/bin/pytest (100% green). Linear CRO-49 is completed, and Docker image ghcr.io/croogmandoo/testharness:latest is built and deployed.
Our next priority is to review the Top 5 prioritized roadmap items in Section 4 of docs/HANDOFF.md.
```
