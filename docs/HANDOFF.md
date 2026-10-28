# Web Testing Harness Session Handoff & Operational Runbook

**Generated:** 2026-10-28T16:22:00-04:00  
**Branch:** `master` (synchronized with `origin/master`, commit `8665688`)  
**Workspace:** `testharness`  
**Test Suite:** **919 / 919 tests passing** (100% green, ~49s)

---

## 1. System Snapshot & Live State

- **Application Port**: `http://localhost:9552` (Docker default) or local development via `uvicorn web.main:create_app --factory --port 9552`
- **Environment**: Python 3.12.15 in local `.venv` (managed via `uv`)
- **Database Engine**: SQLite 3 (WAL mode)
  - Test / Metrics DB: `data/harness.db`
  - Authentication DB: `data/auth.db`
- **Secrets Key**: `data/secret.key` (Fernet 32-byte key, enforced `0600` permissions)
- **Container Registry**: `ghcr.io/croogmandoo/testharness:latest` & `ghcr.io/croogmandoo/testharness:8665688` (built and verified via GitHub Actions `Docker` workflow).
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

7. **Scheduled Maintenance Window Recurrence & Management UI/API (COMPLETED)**:
   - Added [harness/db/_maintenance_windows.py](harness/db/_maintenance_windows.py) with `MaintenanceWindowsMixin` supporting creation, listing, updating, toggling, and deletion of maintenance windows.
   - Enhanced [harness/maintenance.py](harness/maintenance.py) to support weekly recurring windows (`days_of_week`, `start_time`, `end_time` or `duration_minutes`, `timezone`), cron expressions, and fixed ranges, with optional app and environment targeting.
   - Integrated into [harness/runner.py](harness/runner.py), [harness/worker_dispatch.py](harness/worker_dispatch.py), and [harness/staleness.py](harness/staleness.py) with DB window evaluation and app/env scoped suppression.
   - Added REST API endpoints (`GET /api/maintenance-windows`, `POST /api/maintenance-windows`, `GET/PUT/DELETE /api/maintenance-windows/{id}`, `GET /api/maintenance-windows/active`) with RBAC and audit logging.
   - Built Admin UI in [web/routes/admin_maintenance.py](web/routes/admin_maintenance.py) and [web/templates/admin_maintenance.html](web/templates/admin_maintenance.html) with active status banner, multi-schedule creator modal, enable/disable toggles, and direct dashboard integration.
   - Verified in [tests/test_maintenance.py](tests/test_maintenance.py) and [tests/test_maintenance_windows_api.py](tests/test_maintenance_windows_api.py).

8. **Automated Synthetic SSL Renewal Verification Alerting (COMPLETED)**:
   - Added certificate renewal transition detection in [harness/runner.py](harness/runner.py) and [harness/db/_app_state.py](harness/db/_app_state.py): detects when a certificate transitions from failing or <30d validity to renewed (>60d or validity extension), emitting a targeted `AlertType.RESOLVE` notification detailing renewal validity, expiration, and issuer.
   - Added [harness/tls_verifier.py](harness/tls_verifier.py) implementing `get_expiring_or_failing_tls_apps` and `check_and_verify_expiring_certificates`.
   - Added automated background scheduler job `add_cert_check_job` in [harness/auto_scheduler.py](harness/auto_scheduler.py) wired into [web/main.py](web/main.py) for expedited re-checks of expiring or failing certificates.
   - Added on-demand REST endpoints `POST /api/tls/verify/{app}` and `GET /api/tls/expiring` in [web/routes/api.py](web/routes/api.py).
   - Verified in [tests/test_tls_renewal_verification.py](tests/test_tls_renewal_verification.py).

9. **Microsoft Edge Session Import & Power Apps Synthetic Automation (COMPLETED)**:
   - Added [harness/edge_session.py](harness/edge_session.py) implementing session import, multi-format cookie conversion (Cookie-Editor, EditThisCookie, Netscape `.txt`, Playwright format), storage state inspection (detecting Entra ID tokens `ESTSAUTH`, `MSPOK`, `buid`, `fpc`), and disk persistence.
   - Enhanced [harness/browser.py](harness/browser.py) with automatic `storage_state` loading from `data/storage_states/{app}_{env}.json`, `browser_channel: "msedge"` launch support, explicit `frame` scoping, automatic fallback to `iframe[name="fullscreen-app-host"]` for canvas controls (`[data-control-name="..."]`), and the `wait_for_powerapps` step.
   - Enhanced [harness/journey_studio.py](harness/journey_studio.py) to automatically load session state into interactive recording sessions and prioritize `[data-control-name]` for PowerApps controls in the live screencast.
   - Added REST API endpoints in [web/routes/api.py](web/routes/api.py) (`GET`, `POST`, `DELETE /api/storage-states/{app}/{env}`) and an interactive "Import Edge / M365 Session" modal in [web/templates/app_form.html](web/templates/app_form.html).
   - Documented in comprehensive guide [`docs/guides/powerapps_and_edge.md`](docs/guides/powerapps_and_edge.md).
   - Verified 7 unit & integration tests in [tests/test_powerapps_and_edge_session.py](tests/test_powerapps_and_edge_session.py).

10. **Guided Journey Studio In-App Session Capture, Dynamic Test Creation & Keyboard Forwarding (COMPLETED)**:
    - **Dynamic Test Creation**: Updated `makeBrowserFields()` in [web/templates/app_form.html](web/templates/app_form.html) so that dynamically created browser test blocks immediately render the **"🔑 Import Edge / M365 Session"** button without requiring the operator to save the app and reload.
    - **Context-Bound Studio Sessions**: Updated `/api/journey/start` in [web/routes/journey.py](web/routes/journey.py) and `studio_manager.create_session()` in [harness/journey_studio.py](harness/journey_studio.py) to accept `app` and `environment` parameters, ensuring Journey Studio sessions automatically bind and load `data/storage_states/{app}_{env}.json`.
    - **In-Studio Session State Persistence**: Added `POST /api/journey/{session_id}/save-state` endpoint and a **"💾 Save Session State"** button to the Journey Studio modal footer. Operators can open Journey Studio, log into Microsoft Entra ID / MFA interactively once in the live browser view, and click "Save Session State" to capture complete cross-domain cookies (`.login.microsoftonline.com`, `.powerapps.com`) and MSAL `localStorage` tokens directly to disk in 1 click.
    - **Live Keyboard Forwarding**: Added `keypress` WebSocket event forwarding so operators can click input fields in the screencast canvas and type credentials naturally from their keyboard.
    - Verified in [tests/test_journey_studio.py](tests/test_journey_studio.py).

---

## 3. Key Architectural Invariants & Gotchas

- **Header and Secret Invariant**: Custom headers support `$VAR` expansion. Missing variables are kept literally so templated strings don't fail, but configured secrets (`TESTHARNESS_SECRET_KEY`, etc.) should always be injected into runner environments.
- **SAML & Entra ID Storage State File Hierarchy**: Browser execution checks `context_kwargs["storage_state"]` first; if not provided, it falls back to checking `data/storage_states/{app}_{env}.json`. Saving state writes back atomically.
- **Microsoft 365 / Entra ID Cross-Domain & LocalStorage Invariant**: Microsoft SSO requires cookies from `.login.microsoftonline.com` AND MSAL client tokens in `localStorage`. Single-domain cookie exports from `powerapps.com` alone lack login credentials. Always capture via Journey Studio ("Save Session State") or `playwright codegen --save-storage=auth.json`.
- **Certificate Data Aggregation & Renewal Detection**: App state queries extract certificate expiration from the latest passing TLS tests via `get_latest_cert_statuses()`. Transitions from failing or <30d validity to >=60d validity automatically trigger `AlertType.RESOLVE` with detailed renewal metrics.
- **Maintenance Scoping**: Maintenance windows can be global or targeted by `app` and `environment`. Execution and run history continue to record normally while alert dispatch is suppressed.
- **Notification Group Email Deduplication**: `db.resolve_emails_for_groups(groups)` deduplicates and sorts emails across all assigned groups. If an app has no notification groups or none resolve to emails, alert dispatching falls back to global `alerts.email.to`.
- **Playwright Fallback**: `harness/browser.py` is written to be cleanly importable even on headless systems without Playwright installed; keep browser imports lazy inside test execution routines.
- **Visual Baseline Invariant**: `get_last_screenshot` only queries `status='pass'` so failures do not contaminate subsequent baseline comparisons unless explicitly approved by an operator or admin.
- **SQLite Concurrency**: Always keep `PRAGMA journal_mode=WAL;` and `PRAGMA busy_timeout=5000;` on connection pools to prevent `database is locked` during concurrent background runs and web requests.
- **Test Isolation**: `conftest.py` provides shared memory databases and test client fixtures. Never let test execution write to or mutate `data/harness.db`.

---

## 4. Prioritized Next Actions (Top 5)

1. **[Multi-Factor / TOTP Auth Helper in Studio & Runner]**:
   - Provide built-in TOTP generation helper (`totp_secret` or `totp_token`) for journeys requiring two-factor authentication during synthetic runs.
2. **[Windows-Level Control & Desktop Automation (Linear CRO-47)]**:
   - Evaluate hybrid browser + desktop automation (`rpaframework`, `pywinauto`) for Windows-hosted remote workers testing native desktop clients and OS dialogs.
3. **[Out-of-the-Box OpenID Connect / OAuth2 Test Step Helper]**:
   - Add first-class OAuth2 PKCE authorization code grant step helper to browser engine for IdPs requiring live browser-driven grant flows.
4. **[Synthetic Alert Flapping Protection & Exponential Backoff]**:
   - Prevent alert storms when endpoints oscillate rapidly between pass and fail around threshold margins.
5. **[Automated Synthetic Performance Trend Regression Analysis]**:
   - Detect gradual latency degradation (p95 regression) over sliding 7-day windows before hard timeouts occur.

---

## 5. Kickoff Prompt for Next Session

```text
I am resuming work on Web Testing Harness (testharness).
Please read docs/HANDOFF.md, docs/OVERVIEW.md, and docs/STANDARDS.md to understand the current architecture and state.
All 919 tests are passing on master in .venv/bin/pytest (100% green). Scheduled maintenance recurrence, automated SSL renewal verification, and Microsoft Edge / PowerApps automation are completed and verified.
Our next priority is to review the Top 5 prioritized roadmap items in Section 4 of docs/HANDOFF.md.
```
