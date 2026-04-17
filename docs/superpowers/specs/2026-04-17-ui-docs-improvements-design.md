# UI & Docs Improvements Design Spec

**Date:** 2026-04-17

## Scope

Five independent improvements delivered as one plan:

1. Light mode toggle
2. Docs folder reorganization
3. API quickstart guide
4. `graphify-out/` served as static files (graph.html accessible in-app)
5. `.gitignore` additions

---

## 1. Light Mode

### Mechanism
- **localStorage toggle, no backend changes.** A sun/moon icon button in the nav writes `data-theme="light"` or `data-theme="dark"` to `<html>`. On page load, an inline `<script>` in `<head>` (before CSS renders) reads localStorage and sets the attribute — prevents flash of wrong theme.
- Default: dark (no localStorage key set = dark).

### CSS
Add a `[data-theme="light"]` block to `web/static/style.css` that overrides the `:root` variables:

| Variable | Dark | Light |
|---|---|---|
| `--bg` | `#0b0b0c` | `#f5f5f4` |
| `--bg-1` | `#111113` | `#ffffff` |
| `--bg-2` | `#18181a` | `#f0f0ef` |
| `--bg-3` | `#202022` | `#e8e8e7` |
| `--bg-4` | `#2a2a2c` | `#dcdcdb` |
| `--border` | `#272728` | `#d4d4d3` |
| `--border-2` | `#343436` | `#c0c0bf` |
| `--text` | `#ffffff` | `#1c1b1c` |
| `--text-2` | `#787778` | `#5a5959` |
| `--text-3` | `#4a494a` | `#909090` |
| `--amber` | `#d11f44` | `#d11f44` |
| `--amber-hi` | `#e8304f` | `#b81a38` |
| `--amber-lo` | `#5c0d1e` | `#fde8ec` |
| `--pass` | `#2dd680` | `#15803d` |
| `--pass-dim` | `rgba(45,214,128,.1)` | `#dcfce7` |
| `--fail` | `#d11f44` | `#b91c1c` |
| `--fail-dim` | `rgba(209,31,68,.1)` | `#fee2e2` |
| `--run` | `#5ba0f0` | `#1d4ed8` |
| `--run-dim` | `rgba(91,160,240,.1)` | `#dbeafe` |

The scanline `body::after` overlay is hidden in light mode (it has no effect on light backgrounds and adds noise).

### Nav toggle
A `<button id="theme-toggle">` in `.nav-right`, before the user menu. Displays `☀` in dark mode, `☾` in light mode. Pure HTML/JS — no server involvement.

### Template change (`base.html`)
- Add inline `<script>` in `<head>`: reads `localStorage.getItem('theme')`, sets `document.documentElement.setAttribute('data-theme', ...)` immediately.
- Add toggle button to nav with `onclick` handler that flips the attribute and writes to localStorage.

---

## 2. Docs Reorganization

### New structure
```
docs/
  guides/
    api-quickstart.md      ← new (see §3)
    writing-tests.md       ← moved from docs/writing-tests.md
    troubleshooting.md     ← moved + renamed from docs/troubleshooting-stuck-runs.md
  reference/
    api.md                 ← renamed from docs/api-reference.md
    auth.md                ← renamed from docs/auth-architecture.md
  architecture/
    decisions.md           ← renamed from docs/approach-recommendations.md
  superpowers/             ← unchanged (internal planning docs)
    plans/
    specs/
```

All internal links between docs updated. `README.md` doc links updated.

---

## 3. API Quickstart Guide (`docs/guides/api-quickstart.md`)

Covers:
1. **What API keys are** — `hth_`-prefixed tokens for non-browser/CI access; same RBAC as session auth.
2. **Generating a key** — step-by-step: navigate to API Keys, create with a name and expiry, copy the one-time display.
3. **Making requests** — three curl examples:
   - `GET /api/apps` — list all apps
   - `POST /api/runs` — trigger a test run
   - `GET /api/runs/{run_id}` — poll for results
4. **Authentication header formats** — both `Authorization: Bearer hth_...` and `X-API-Key: hth_...` shown.
5. **Expiry and revocation** — how to revoke from the UI.

References `docs/reference/api.md` for full endpoint listing.

---

## 4. `graphify-out/` Static Files

### FastAPI mount
In `web/main.py` `create_app()`, after existing static mount:

```python
if os.path.isdir("graphify-out"):
    app.mount("/graphify-out", StaticFiles(directory="graphify-out"), name="graphify-out")
```

Conditional on directory existing so tests without it don't break.

`graph.html` is then accessible at `/graphify-out/graph.html`.

### Docs reference
`docs/architecture/decisions.md` gets a header section:

> **Knowledge Graph:** An interactive graph of this codebase is available at [`/graphify-out/graph.html`](/graphify-out/graph.html). Run `/graphify` to rebuild it after significant changes.

---

## 5. `.gitignore` Additions

```
graphify-out/cache/
.superpowers/
```

---

## Self-Review

- No TBDs or placeholders.
- Light mode palette confirmed by user in visual companion session.
- StaticFiles mount is conditional — existing tests unaffected.
- Docs reorganization is purely file moves + renames; no new infrastructure.
- API quickstart uses only already-documented endpoints.
- All five items are independent — can be implemented and committed separately.
