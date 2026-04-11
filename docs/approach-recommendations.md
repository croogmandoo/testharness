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
