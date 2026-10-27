# Web Testing Harness Session Handoff & Operational Runbook

**Generated:** 2026-10-27T08:35:00-04:00  
**Branch:** `master` (synchronized with `origin/master`)  
**Workspace:** `testharness`  
**Test Suite:** **895 / 895 tests passing** (100% green, ~38s)

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

1. **Guided Journey Creator – In-Browser Interactive Studio Modal & URL Recording (Issue #22 / Linear CRO-49 - COMPLETED)**:
   - Added [harness/journey_studio.py](harness/journey_studio.py) implementing `JourneySession` and `JourneyStudioManager`. Uses Playwright CDP screencasting (`Page.startScreencast` / `Page.screencastFrameAck`) to stream interactive live frames, resolve elements at `(x, y)` via `document.elementFromPoint`, synthesize robust CSS/text selectors (`#id`, `button:has-text()`, `[name=]`), and execute click, fill, assert, and screenshot actions.
   - Added [web/routes/journey.py](web/routes/journey.py) exposing REST endpoints (`POST /api/journey/start`, `POST /api/journey/{id}/action`, `POST /api/journey/{id}/stop`) and real-time bidirectional WebSocket (`/ws/journey/{session_id}`).
   - Embedded interactive Journey Studio modal in [web/templates/app_form.html](web/templates/app_form.html) allowing users to click "Launch Journey Studio (Interactive)", view the live screencast canvas, click directly on elements to record actions or use quick action buttons (Assert Text, Fill Input, Screenshot), and insert recorded steps directly into the test editor with a single click.
   - Added [harness/url_recorder.py](harness/url_recorder.py) implementing headless URL recording via Playwright `framenavigated` event subscription, relative path normalization against app `base_url`, and consecutive duplicate collapse.
   - Integrated into `run_browser_test` in [harness/browser.py](harness/browser.py) via `record_url_steps=True` flag, automatically synthesizing and prepending `StepResult` navigation steps to the test log while ensuring web vitals and performance threshold evaluations remain intact.
   - Comprehensive test suite in [tests/test_journey_studio.py](tests/test_journey_studio.py) and [tests/test_url_recorder.py](tests/test_url_recorder.py) (895 tests passing).
   - Fully documented in [docs/guides/url_recorder_spec.md](docs/guides/url_recorder_spec.md), [docs/guides/writing-tests.md](docs/guides/writing-tests.md), and [docs/OVERVIEW.md](docs/OVERVIEW.md).

2. **Notification Groups Management & Targeted App Alert Routing (Issue #23 - CLOSED)**:
   - Added [harness/db/_notification_groups.py](harness/db/_notification_groups.py) with `NotificationGroupsMixin` supporting group creation, listing, updating, deletion, and email address resolution (`resolve_emails_for_groups`).
   - Integrated into [harness/db/_base.py](harness/db/_base.py) with automatic schema migration (`notification_groups` table with unique name index).
   - Added full REST API endpoints in [web/routes/api.py](web/routes/api.py) (`GET`, `POST`, `PUT`, `DELETE /api/notification-groups`) with RBAC enforcement (`admin`, `runner`), input validation, and audit logging.
   - Built Admin UI in [web/routes/admin_notifications.py](web/routes/admin_notifications.py) and [web/templates/admin_notifications.html](web/templates/admin_notifications.html) with group creation, email badges, edit dialogs, delete confirmations, and assigned app cross-referencing.
   - Integrated notification group selection into application forms ([web/templates/app_form.html](web/templates/app_form.html), [web/routes/apps.py](web/routes/apps.py)) allowing one or more groups to be assigned to an application.
   - Enhanced alert engine in [harness/alerts.py](harness/alerts.py), [harness/runner.py](harness/runner.py), and [harness/worker_dispatch.py](harness/worker_dispatch.py) to resolve assigned groups, deduplicate recipient emails, and dynamically route alert emails to assigned groups with seamless fallback to global SMTP settings.
   - Verified 14 comprehensive unit and integration tests in [tests/test_notification_groups.py](tests/test_notification_groups.py).

2. **Prometheus Alerting Rules & Grafana Dashboard**:
   - Added [deploy/prometheus/alerts.yml](deploy/prometheus/alerts.yml) with production Prometheus alerts (`HarnessTestFailing`, `HarnessHighFailureRate`, `HarnessAppStale`, `HarnessSlowTestExecution`, `HarnessRunnerHeartbeatMissing`).
   - Added [deploy/grafana/dashboard.json](deploy/grafana/dashboard.json) with fleet health stats, test duration percentiles, app status timeline matrix, run volume bar charts, and staleness monitoring.
   - Added `harness_runner_last_seen_timestamp_seconds` heartbeat gauge to [web/routes/metrics.py](web/routes/metrics.py), tested in [tests/test_web_metrics.py](tests/test_web_metrics.py).

3. **Multi-Environment Selector & Persistent Cookie Filtering (Issue #20 - CLOSED)**:
   - Added interactive environment selector chips (`.env-chips`, `.env-chip`) to the dashboard header in [web/templates/dashboard.html](web/templates/dashboard.html) and breadcrumb bar in [web/templates/detail.html](web/templates/detail.html).
   - Added persistent cookie resolution (`selected_environment`) in [web/routes/dashboard.py](web/routes/dashboard.py), persisting chosen environments across dashboard views and app details.
   - Tested in [tests/test_web_environment_filter.py](tests/test_web_environment_filter.py).

4. **Custom Webhook Payload Templating & Incident Webhook Dispatch (Issue #21 - CLOSED)**:
   - Added Jinja2 template rendering (`_render_webhook_payload`) in [harness/alerts.py](harness/alerts.py) for outbound run webhooks and incident alerts.
   - Added pre-packaged formats for PagerDuty Events API v2 (`format: "pagerduty"`) and Opsgenie Alerts API (`format: "opsgenie"`).
   - Added support for custom headers (`headers: {...}`) and HMAC-SHA256 payload signing (`X-Harness-Signature`).
   - Enabled incident alert webhooks in `dispatch_alerts()` supporting both single webhook dicts and lists of webhook endpoints.
   - Tested in [tests/test_alerts_templating.py](tests/test_alerts_templating.py).

5. **TLS Certificate Expiration & Security Headers Prober (Issue #17 - CLOSED)**:
   - Added [harness/tls_engine.py](harness/tls_engine.py) implementing `run_tls_test` and `inspect_cert_sync` using Python `ssl` and `socket`.
   - Validates TLS certificate expiration date against configurable threshold (`min_days_valid`, default 30 days).
   - Verifies Subject Alternative Names (SANs) match the target hostname and checks negotiated TLS protocol against `min_tls_version` (e.g. `TLSv1.2` or `TLSv1.3`).
   - Checks security headers (`Strict-Transport-Security`, `X-Content-Type-Options`, `Content-Security-Policy`) over HTTPS responses.
   - Tested in [tests/test_tls_engine.py](tests/test_tls_engine.py).

6. **Visual Regression Diff Viewer & Baseline Approval Workflow (Issue #18 - CLOSED)**:
   - Added `generate_diff_image` in [harness/screenshot_diff.py](harness/screenshot_diff.py) generating a high-contrast magenta (`#FF007F`) visual heatmap overlay on a dimmed grayscale background.
   - Added `POST /api/visual/approve` endpoint in [web/routes/api.py](web/routes/api.py) requiring `admin` or `runner` role.
   - Added "Approve as New Baseline" UI button in [web/templates/detail.html](web/templates/detail.html).
   - Tested in [tests/test_visual_approval.py](tests/test_visual_approval.py).

7. **Core Web Vitals & Frontend Performance Timing Metrics (Issue #19 - CLOSED)**:
   - Added `extract_web_vitals` in [harness/browser.py](harness/browser.py) querying the Navigation Timing API and PerformanceObserver (TTFB, DOMContentLoaded, Load Time, FCP, LCP, CLS).
   - Tested in [tests/test_browser_performance.py](tests/test_browser_performance.py).

7. **Test Suite Milestone**:
   - Reached **882 passing unit and integration tests** (100% green, 0 failures, 0 regressions).

---

## 3. Key Architectural Invariants & Gotchas

- **Notification Group Email Deduplication**: `db.resolve_emails_for_groups(groups)` deduplicates and sorts emails across all assigned groups. If an app has no notification groups or none resolve to emails, alert dispatching falls back to global `alerts.email.to`.
- **Playwright Fallback**: `harness/browser.py` is written to be cleanly importable even on headless systems without Playwright installed; keep browser imports lazy inside test execution routines.
- **Visual Baseline Invariant**: `get_last_screenshot` only queries `status='pass'` so failures do not contaminate subsequent baseline comparisons unless explicitly approved by an operator or admin.
- **SQLite Concurrency**: Always keep `PRAGMA journal_mode=WAL;` and `PRAGMA busy_timeout=5000;` on connection pools to prevent `database is locked` during concurrent background runs and web requests.
- **Test Isolation**: `conftest.py` provides shared memory databases and test client fixtures. Never let test execution write to or mutate `data/harness.db`.
- **Bind-Mount Ownership & Test Execution**: Running `docker compose up` without `user:` pins will chown `./data` to container UID 10001. If running local pytest afterward as UID 1000, ensure host ownership of `./data` (`0600 data/secret.key`) matches the host user.

---

## 4. Prioritized Next Actions

1. **[Scheduled Maintenance Window Recurrence]**:
   - Add recurring maintenance window schedules (e.g. weekly Sunday 02:00-04:00 UTC) in addition to one-off datetime windows.
2. **[Optional Exploration: Windows-Level Control & Desktop Automation (Linear CRO-47)]**:
   - Evaluate hybrid browser + desktop automation (`rpaframework`, `pywinauto`) for Windows-hosted remote workers testing native desktop clients and OS dialogs.

---

## 5. Kickoff Prompt for Next Session

```text
I am resuming work on Web Testing Harness (testharness).
Please read docs/HANDOFF.md, docs/OVERVIEW.md, and docs/STANDARDS.md to understand the current architecture and state.
All 889 tests are passing on master in .venv/bin/pytest. GitHub issues #8-#23 and Linear CRO-49 are closed.
Our next priority is to review the new prioritized roadmap items in Section 4.
```
