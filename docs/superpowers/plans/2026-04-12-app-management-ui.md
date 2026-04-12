# App Management UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a browser-based UI for creating, editing, and archiving app definitions (YAML files in `apps/`), backed by a new `harness/app_manager.py` module and new FastAPI routes.

**Architecture:** `harness/app_manager.py` owns all YAML filesystem operations. New routes in `web/routes/apps.py` (HTML pages) and additions to `web/routes/api.py` (JSON mutation endpoints) sit on top. Jinja2 templates render the app list and a create/edit form with Simple (structured fields) and Advanced (raw YAML textarea) modes. `web/main.py` gains `_apps_dir`, `get_apps_dir()`, and `reload_apps()` so routes can reload `_apps` after mutations without knowing the apps directory path.

**Tech Stack:** Python 3.11+, FastAPI, Jinja2, PyYAML, pytest, fastapi.testclient, js-yaml (CDN, client-side YAML parse/validate)

---

## File Map

```
harness/
└── app_manager.py          ← NEW: slugify, file-path helpers, all CRUD for app YAML files

web/
├── routes/
│   ├── apps.py             ← NEW: GET /apps, /apps/new, /apps/{name}/edit
│   └── api.py              ← UPDATE: add POST/PUT/DELETE/restore/permanent mutation endpoints
├── templates/
│   ├── base.html           ← UPDATE: add "Apps" nav link between brand and env-switcher
│   ├── apps.html           ← NEW: active-apps table + collapsed archived-apps section
│   └── app_form.html       ← NEW: create/edit form with Simple + Advanced (YAML textarea) modes
└── main.py                 ← UPDATE: store _apps_dir, expose get_apps_dir()/reload_apps(), register apps router

tests/
├── test_app_manager.py     ← NEW: unit tests for all app_manager functions (uses tmp_path)
└── test_web_apps.py        ← NEW: TestClient tests for HTML routes + mutation API endpoints
```

---

## Task 1: `harness/app_manager.py` — Filesystem CRUD

**Files:**
- Create: `harness/app_manager.py`
- Create: `tests/test_app_manager.py`

- [ ] **Step 1: Write all failing tests**

Create `tests/test_app_manager.py`:

```python
import pytest
import yaml
from harness.app_manager import (
    AppManagerError,
    slugify_app_name,
    app_file_path,
    write_app,
    update_app,
    read_app_raw,
    archive_app,
    restore_app,
    delete_archived_app,
    list_apps,
    list_archived,
)

SAMPLE_APP = {
    "app": "My API",
    "url": "https://example.com",
    "tests": [{"name": "health", "type": "availability", "expect_status": 200}],
}


def test_slugify_app_name():
    assert slugify_app_name("My API") == "my-api"
    assert slugify_app_name("Customer Portal!") == "customer-portal"
    assert slugify_app_name("  spaces  ") == "spaces"


def test_app_file_path(tmp_path):
    p = app_file_path("My API", apps_dir=str(tmp_path))
    assert p == tmp_path / "my-api.yaml"


def test_write_app_creates_yaml_file(tmp_path):
    path = write_app(SAMPLE_APP, apps_dir=str(tmp_path))
    assert path.exists()
    data = yaml.safe_load(path.read_text())
    assert data["app"] == "My API"
    assert data["url"] == "https://example.com"


def test_write_app_raises_on_duplicate(tmp_path):
    write_app(SAMPLE_APP, apps_dir=str(tmp_path))
    with pytest.raises(AppManagerError, match="already exists"):
        write_app(SAMPLE_APP, apps_dir=str(tmp_path))


def test_read_app_raw_returns_string_with_unresolved_vars(tmp_path):
    app_def = {**SAMPLE_APP, "url": "$BASE_URL"}
    write_app(app_def, apps_dir=str(tmp_path))
    raw = read_app_raw("My API", apps_dir=str(tmp_path))
    assert isinstance(raw, str)
    assert "$BASE_URL" in raw


def test_update_app_overwrites_file(tmp_path):
    write_app(SAMPLE_APP, apps_dir=str(tmp_path))
    updated = {**SAMPLE_APP, "url": "https://updated.com"}
    path = update_app("My API", updated, apps_dir=str(tmp_path))
    data = yaml.safe_load(path.read_text())
    assert data["url"] == "https://updated.com"


def test_update_app_raises_if_not_found(tmp_path):
    with pytest.raises(AppManagerError, match="not found"):
        update_app("Missing App", SAMPLE_APP, apps_dir=str(tmp_path))


def test_archive_app_moves_file_to_archived(tmp_path):
    write_app(SAMPLE_APP, apps_dir=str(tmp_path))
    archived_path = archive_app("My API", apps_dir=str(tmp_path))
    assert archived_path.exists()
    assert "archived" in str(archived_path)
    assert not app_file_path("My API", apps_dir=str(tmp_path)).exists()


def test_archive_app_raises_if_not_found(tmp_path):
    with pytest.raises(AppManagerError, match="not found"):
        archive_app("Missing App", apps_dir=str(tmp_path))


def test_restore_app_moves_file_back(tmp_path):
    write_app(SAMPLE_APP, apps_dir=str(tmp_path))
    archive_app("My API", apps_dir=str(tmp_path))
    path = restore_app("My API", apps_dir=str(tmp_path))
    assert path.exists()
    assert "archived" not in str(path)


def test_restore_app_raises_if_not_in_archived(tmp_path):
    with pytest.raises(AppManagerError, match="not found"):
        restore_app("Missing App", apps_dir=str(tmp_path))


def test_delete_archived_app_removes_file(tmp_path):
    write_app(SAMPLE_APP, apps_dir=str(tmp_path))
    archive_app("My API", apps_dir=str(tmp_path))
    delete_archived_app("My API", apps_dir=str(tmp_path))
    archived_path = tmp_path / "archived" / "my-api.yaml"
    assert not archived_path.exists()


def test_delete_archived_app_raises_if_not_found(tmp_path):
    with pytest.raises(AppManagerError, match="not found"):
        delete_archived_app("Missing App", apps_dir=str(tmp_path))


def test_list_apps_returns_active_only(tmp_path):
    write_app(SAMPLE_APP, apps_dir=str(tmp_path))
    apps = list_apps(apps_dir=str(tmp_path))
    assert len(apps) == 1
    assert apps[0]["app"] == "My API"


def test_list_apps_excludes_archived(tmp_path):
    write_app(SAMPLE_APP, apps_dir=str(tmp_path))
    archive_app("My API", apps_dir=str(tmp_path))
    apps = list_apps(apps_dir=str(tmp_path))
    assert len(apps) == 0


def test_list_archived_returns_archived_apps(tmp_path):
    write_app(SAMPLE_APP, apps_dir=str(tmp_path))
    archive_app("My API", apps_dir=str(tmp_path))
    archived = list_archived(apps_dir=str(tmp_path))
    assert len(archived) == 1
    assert archived[0]["app"] == "My API"
```

- [ ] **Step 2: Run tests to verify they all fail**

```bash
cd /c/webtestingharness && python -m pytest tests/test_app_manager.py -v 2>&1 | head -30
```

Expected: `ImportError` or `ModuleNotFoundError` — `app_manager` doesn't exist yet.

- [ ] **Step 3: Implement `harness/app_manager.py`**

Create `harness/app_manager.py`:

```python
import re
import shutil
import yaml
from pathlib import Path
from harness.loader import load_apps


class AppManagerError(Exception):
    pass


def slugify_app_name(name: str) -> str:
    name = name.strip().lower()
    name = re.sub(r"\s+", "-", name)
    name = re.sub(r"[^a-z0-9-]", "", name)
    return name


def app_file_path(name: str, apps_dir: str = "apps") -> Path:
    return Path(apps_dir) / f"{slugify_app_name(name)}.yaml"


def write_app(app_def: dict, apps_dir: str = "apps") -> Path:
    Path(apps_dir).mkdir(parents=True, exist_ok=True)
    path = app_file_path(app_def["app"], apps_dir=apps_dir)
    if path.exists():
        raise AppManagerError(f"App '{app_def['app']}' already exists at {path}")
    path.write_text(yaml.dump(app_def, default_flow_style=False, allow_unicode=True))
    return path


def update_app(app_name: str, app_def: dict, apps_dir: str = "apps") -> Path:
    path = app_file_path(app_name, apps_dir=apps_dir)
    if not path.exists():
        raise AppManagerError(f"App '{app_name}' not found at {path}")
    path.write_text(yaml.dump(app_def, default_flow_style=False, allow_unicode=True))
    return path


def read_app_raw(app_name: str, apps_dir: str = "apps") -> str:
    path = app_file_path(app_name, apps_dir=apps_dir)
    if not path.exists():
        raise AppManagerError(f"App '{app_name}' not found at {path}")
    return path.read_text()


def archive_app(app_name: str, apps_dir: str = "apps") -> Path:
    src = app_file_path(app_name, apps_dir=apps_dir)
    if not src.exists():
        raise AppManagerError(f"App '{app_name}' not found at {src}")
    archived_dir = Path(apps_dir) / "archived"
    archived_dir.mkdir(parents=True, exist_ok=True)
    dest = archived_dir / src.name
    shutil.move(str(src), str(dest))
    return dest


def restore_app(app_name: str, apps_dir: str = "apps") -> Path:
    archived_dir = Path(apps_dir) / "archived"
    src = archived_dir / f"{slugify_app_name(app_name)}.yaml"
    if not src.exists():
        raise AppManagerError(f"App '{app_name}' not found in archived at {src}")
    dest = Path(apps_dir) / src.name
    shutil.move(str(src), str(dest))
    return dest


def delete_archived_app(app_name: str, apps_dir: str = "apps") -> None:
    archived_dir = Path(apps_dir) / "archived"
    path = archived_dir / f"{slugify_app_name(app_name)}.yaml"
    if not path.exists():
        raise AppManagerError(f"App '{app_name}' not found in archived at {path}")
    path.unlink()


def list_apps(apps_dir: str = "apps") -> list:
    if not Path(apps_dir).is_dir():
        return []
    return load_apps(apps_dir)


def list_archived(apps_dir: str = "apps") -> list:
    archived_dir = str(Path(apps_dir) / "archived")
    if not Path(archived_dir).is_dir():
        return []
    return load_apps(archived_dir)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /c/webtestingharness && python -m pytest tests/test_app_manager.py -v
```

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
cd /c/webtestingharness && git add harness/app_manager.py tests/test_app_manager.py && git commit -m "feat: add harness/app_manager.py with CRUD operations for app YAML files"
```

---

## Task 2: Update `web/main.py` — `get_apps_dir()`, `reload_apps()`, register apps router

**Files:**
- Modify: `web/main.py`
- Create: `web/routes/apps.py` (stub only — full implementation in Task 4)

Routes need `get_apps_dir()` so they can pass the right directory to `app_manager` functions without hardcoding `"apps"`. `reload_apps()` refreshes `_apps` after mutations.

- [ ] **Step 1: Create stub `web/routes/apps.py`**

This stub is needed so the import in `create_app` doesn't fail before the real router is implemented:

```python
from fastapi import APIRouter
router = APIRouter()
```

- [ ] **Step 2: Edit `web/main.py`**

Replace the entire file with:

```python
import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from harness.db import Database
from harness.loader import load_apps

_db: Database = None
_config: dict = {}
_apps: list = []
_apps_dir: str = "apps"


def get_db() -> Database:
    return _db


def get_config() -> dict:
    return _config


def get_apps() -> list:
    return _apps


def get_apps_dir() -> str:
    return _apps_dir


def reload_apps() -> None:
    global _apps
    _apps = load_apps(_apps_dir) if os.path.isdir(_apps_dir) else []


def create_app(db: Database = None, config: dict = None, apps_dir: str = "apps") -> FastAPI:
    global _db, _config, _apps, _apps_dir
    _config = config or {}
    _apps_dir = apps_dir
    _apps = load_apps(apps_dir) if os.path.isdir(apps_dir) else []

    if db is None:
        os.makedirs("data", exist_ok=True)
        _db = Database("data/harness.db")
        _db.init_schema()
    else:
        _db = db

    app = FastAPI(title="Web Testing Harness")

    from web.routes.api import router as api_router
    app.include_router(api_router)

    from web.routes.dashboard import router as dashboard_router
    app.include_router(dashboard_router)

    from web.routes.apps import router as apps_router
    app.include_router(apps_router)

    screenshots_dir = "data/screenshots"
    os.makedirs(screenshots_dir, exist_ok=True)
    app.mount("/screenshots", StaticFiles(directory=screenshots_dir), name="screenshots")

    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.isdir(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    return app


def main():
    import sys
    import uvicorn
    from dotenv import load_dotenv
    from harness.config import load_config

    load_dotenv()

    if __name__ == "__main__" or sys.modules.get("web.main") is None:
        sys.modules["web.main"] = sys.modules[__name__]

    config = load_config("config.yaml")
    app = create_app(config=config)
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run existing tests to verify no regressions**

```bash
cd /c/webtestingharness && python -m pytest tests/test_web_api.py -v
```

Expected: All tests PASS.

- [ ] **Step 4: Commit**

```bash
cd /c/webtestingharness && git add web/main.py web/routes/apps.py && git commit -m "feat: add get_apps_dir/reload_apps to web/main.py, stub apps router"
```

---

## Task 3: API Mutation Endpoints (`web/routes/api.py`) + Tests

**Files:**
- Modify: `web/routes/api.py`
- Create: `tests/test_web_apps.py` (mutation endpoint tests)

- [ ] **Step 1: Write failing tests for mutation endpoints**

Create `tests/test_web_apps.py`:

```python
import pytest
import yaml
from fastapi.testclient import TestClient
from web.main import create_app
from harness.db import Database


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.init_schema()
    return d


@pytest.fixture
def apps_dir(tmp_path):
    d = tmp_path / "apps"
    d.mkdir()
    return d


@pytest.fixture
def client(db, apps_dir):
    config = {
        "default_environment": "production",
        "environments": {"production": {"label": "Production"}},
    }
    app = create_app(db=db, config=config, apps_dir=str(apps_dir))
    return TestClient(app)


@pytest.fixture
def client_with_app(db, apps_dir):
    """Client with one pre-existing app."""
    app_def = {
        "app": "my-api",
        "url": "https://example.com",
        "tests": [{"name": "health", "type": "availability", "expect_status": 200}],
    }
    (apps_dir / "my-api.yaml").write_text(yaml.dump(app_def))
    config = {
        "default_environment": "production",
        "environments": {"production": {"label": "Production"}},
    }
    app = create_app(db=db, config=config, apps_dir=str(apps_dir))
    return TestClient(app), apps_dir


# --- Mutation API endpoint tests ---

def test_post_api_apps_creates_file_and_returns_201(client, apps_dir):
    app_def = {
        "app": "new-app",
        "url": "https://new.example.com",
        "tests": [{"name": "health", "type": "availability", "expect_status": 200}],
    }
    resp = client.post("/api/apps", json={"app_def": app_def})
    assert resp.status_code == 201
    data = resp.json()
    assert data["app"] == "new-app"
    assert "file" in data
    assert (apps_dir / "new-app.yaml").exists()


def test_post_api_apps_returns_409_on_duplicate(client, apps_dir):
    app_def = {"app": "dup-app", "url": "https://dup.example.com", "tests": []}
    client.post("/api/apps", json={"app_def": app_def})
    resp = client.post("/api/apps", json={"app_def": app_def})
    assert resp.status_code == 409


def test_put_api_apps_updates_file_and_returns_200(client_with_app):
    client, apps_dir = client_with_app
    updated = {"app": "my-api", "url": "https://updated.example.com", "tests": []}
    resp = client.put("/api/apps/my-api", json={"app_def": updated})
    assert resp.status_code == 200
    data = resp.json()
    assert data["app"] == "my-api"
    saved = yaml.safe_load((apps_dir / "my-api.yaml").read_text())
    assert saved["url"] == "https://updated.example.com"


def test_put_api_apps_returns_404_if_not_found(client):
    resp = client.put(
        "/api/apps/nonexistent",
        json={"app_def": {"app": "x", "url": "https://x.com", "tests": []}},
    )
    assert resp.status_code == 404


def test_delete_api_apps_archives_app_and_returns_200(client_with_app):
    client, apps_dir = client_with_app
    resp = client.delete("/api/apps/my-api")
    assert resp.status_code == 200
    assert "archived" in resp.json()
    assert not (apps_dir / "my-api.yaml").exists()
    assert (apps_dir / "archived" / "my-api.yaml").exists()


def test_delete_api_apps_returns_404_if_not_found(client):
    resp = client.delete("/api/apps/nonexistent")
    assert resp.status_code == 404


def test_post_restore_returns_200_and_restores_file(client_with_app):
    client, apps_dir = client_with_app
    client.delete("/api/apps/my-api")
    resp = client.post("/api/apps/my-api/restore")
    assert resp.status_code == 200
    assert (apps_dir / "my-api.yaml").exists()


def test_post_restore_returns_404_if_not_archived(client):
    resp = client.post("/api/apps/nonexistent/restore")
    assert resp.status_code == 404


def test_delete_permanent_removes_file_and_returns_204(client_with_app):
    client, apps_dir = client_with_app
    client.delete("/api/apps/my-api")  # archive first
    resp = client.delete("/api/apps/my-api/permanent")
    assert resp.status_code == 204
    assert not (apps_dir / "archived" / "my-api.yaml").exists()


def test_delete_permanent_returns_404_if_not_archived(client):
    resp = client.delete("/api/apps/nonexistent/permanent")
    assert resp.status_code == 404


# --- HTML route tests ---

def test_get_apps_returns_200_html(client):
    resp = client.get("/apps")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_get_apps_new_returns_200_html(client):
    resp = client.get("/apps/new")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_get_apps_edit_returns_200_html(client_with_app):
    client, apps_dir = client_with_app
    resp = client.get("/apps/my-api/edit")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_get_apps_edit_returns_404_for_unknown_app(client):
    resp = client.get("/apps/nonexistent/edit")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /c/webtestingharness && python -m pytest tests/test_web_apps.py -v 2>&1 | head -40
```

Expected: 404s or 405s — routes not implemented yet.

- [ ] **Step 3: Add mutation endpoints to `web/routes/api.py`**

Append to the existing file after the `GET /api/results/...` route. The `GET /api/apps` route must remain above the new mutation routes to avoid conflicts.

```python
# ---- App management mutation endpoints ----

from typing import Any as _Any


class AppDefRequest(BaseModel):
    app_def: dict[str, _Any]


@router.post("/apps", status_code=201)
async def create_app_def(req: AppDefRequest):
    from web.main import get_apps_dir, reload_apps
    import harness.app_manager as mgr
    try:
        path = mgr.write_app(req.app_def, apps_dir=get_apps_dir())
    except mgr.AppManagerError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    reload_apps()
    return {"app": req.app_def.get("app", ""), "file": str(path)}


@router.put("/apps/{app_name}", status_code=200)
async def update_app_def(app_name: str, req: AppDefRequest):
    from web.main import get_apps_dir, reload_apps
    import harness.app_manager as mgr
    try:
        path = mgr.update_app(app_name, req.app_def, apps_dir=get_apps_dir())
    except mgr.AppManagerError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    reload_apps()
    return {"app": app_name, "file": str(path)}


@router.delete("/apps/{app_name}", status_code=200)
async def archive_app_def(app_name: str):
    from web.main import get_apps_dir, reload_apps
    import harness.app_manager as mgr
    try:
        path = mgr.archive_app(app_name, apps_dir=get_apps_dir())
    except mgr.AppManagerError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    reload_apps()
    return {"archived": str(path)}


@router.post("/apps/{app_name}/restore", status_code=200)
async def restore_app_def(app_name: str):
    from web.main import get_apps_dir, reload_apps
    import harness.app_manager as mgr
    try:
        path = mgr.restore_app(app_name, apps_dir=get_apps_dir())
    except mgr.AppManagerError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    reload_apps()
    return {"app": app_name, "file": str(path)}


@router.delete("/apps/{app_name}/permanent", status_code=204)
async def delete_app_permanently(app_name: str):
    from web.main import get_apps_dir
    import harness.app_manager as mgr
    try:
        mgr.delete_archived_app(app_name, apps_dir=get_apps_dir())
    except mgr.AppManagerError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
```

**Route order in the final file must be:**
1. `GET /api/runs` (existing)
2. `GET /api/runs/{run_id}` (existing)
3. `GET /api/apps` (existing — read-only, unchanged)
4. `GET /api/results/{app}/{environment}` (existing)
5. `POST /api/apps` (new — specific, before path params)
6. `PUT /api/apps/{app_name}` (new)
7. `DELETE /api/apps/{app_name}` (new)
8. `POST /api/apps/{app_name}/restore` (new)
9. `DELETE /api/apps/{app_name}/permanent` (new)

- [ ] **Step 4: Run mutation tests to verify they pass**

```bash
cd /c/webtestingharness && python -m pytest tests/test_web_apps.py -k "api" -v
```

Expected: All mutation tests PASS. HTML route tests will still fail (templates not yet created).

- [ ] **Step 5: Run full test suite to check for regressions**

```bash
cd /c/webtestingharness && python -m pytest tests/ -v --ignore=tests/test_web_apps.py && python -m pytest tests/test_web_apps.py -k "api" -v
```

Expected: All pre-existing tests PASS; mutation API tests PASS.

- [ ] **Step 6: Commit**

```bash
cd /c/webtestingharness && git add web/routes/api.py tests/test_web_apps.py && git commit -m "feat: add app mutation API endpoints (POST/PUT/DELETE/restore/permanent)"
```

---

## Task 4: HTML Routes (`web/routes/apps.py`) + Skeleton Templates

**Files:**
- Modify: `web/routes/apps.py` (replace stub)
- Create: `web/templates/apps.html` (skeleton)
- Create: `web/templates/app_form.html` (skeleton)

The HTML route tests were already written in Task 3's `test_web_apps.py`. Run them now.

- [ ] **Step 1: Run HTML route tests to confirm they fail**

```bash
cd /c/webtestingharness && python -m pytest tests/test_web_apps.py -k "html or get_apps" -v 2>&1 | head -20
```

Expected: FAIL — templates don't exist yet.

- [ ] **Step 2: Create skeleton templates**

Create `web/templates/apps.html`:

```html
{% extends "base.html" %}
{% block content %}
<h1>App Management</h1>
{% endblock %}
```

Create `web/templates/app_form.html`:

```html
{% extends "base.html" %}
{% block content %}
<h1>{{ "Edit" if mode == "edit" else "New App" }}</h1>
{% endblock %}
```

- [ ] **Step 3: Implement `web/routes/apps.py`**

Replace the stub:

```python
import os
import yaml
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(
    directory=os.path.join(os.path.dirname(__file__), "..", "templates")
)


def _nav_ctx(request: Request) -> dict:
    from web.main import get_config
    config = get_config()
    return {
        "request": request,
        "environments": config.get("environments", {}),
        "environment": None,
    }


@router.get("/apps", response_class=HTMLResponse)
async def apps_list(request: Request):
    import harness.app_manager as mgr
    from web.main import get_apps_dir
    apps_dir = get_apps_dir()
    active = mgr.list_apps(apps_dir=apps_dir)
    archived = mgr.list_archived(apps_dir=apps_dir)
    return templates.TemplateResponse("apps.html", {
        **_nav_ctx(request),
        "active_apps": active,
        "archived_apps": archived,
    })


@router.get("/apps/new", response_class=HTMLResponse)
async def apps_new(request: Request):
    return templates.TemplateResponse("app_form.html", {
        **_nav_ctx(request),
        "mode": "create",
        "app_name": "",
        "app_def": {},
        "raw_yaml": "",
    })


@router.get("/apps/{app_name}/edit", response_class=HTMLResponse)
async def apps_edit(request: Request, app_name: str):
    import harness.app_manager as mgr
    from web.main import get_apps_dir
    apps_dir = get_apps_dir()
    try:
        raw_yaml = mgr.read_app_raw(app_name, apps_dir=apps_dir)
    except mgr.AppManagerError:
        raise HTTPException(status_code=404, detail=f"App '{app_name}' not found")
    app_def = yaml.safe_load(raw_yaml) or {}
    return templates.TemplateResponse("app_form.html", {
        **_nav_ctx(request),
        "mode": "edit",
        "app_name": app_name,
        "app_def": app_def,
        "raw_yaml": raw_yaml,
    })
```

- [ ] **Step 4: Run all tests to verify they pass**

```bash
cd /c/webtestingharness && python -m pytest tests/test_web_apps.py -v
```

Expected: All 14 tests in `test_web_apps.py` PASS.

- [ ] **Step 5: Run full suite**

```bash
cd /c/webtestingharness && python -m pytest -v
```

Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
cd /c/webtestingharness && git add web/routes/apps.py web/templates/apps.html web/templates/app_form.html && git commit -m "feat: add HTML routes for app management (/apps, /apps/new, /apps/{name}/edit)"
```

---

## Task 5: Full Templates + Nav Update

**Files:**
- Modify: `web/templates/base.html`
- Modify: `web/templates/apps.html`
- Modify: `web/templates/app_form.html`

No new tests — existing route tests cover 200 responses. The templates are pure presentation.

**Note on JavaScript DOM safety:** All JS in `app_form.html` constructs DOM nodes using `createElement`/`appendChild`/`textContent`. User-supplied text is always assigned via `.textContent` or `.value` (never concatenated into HTML strings). The only `innerHTML` usage is setting it to `''` to clear a container — no user data is involved.

- [ ] **Step 1: Update `web/templates/base.html` — add Apps nav link**

The existing `<nav>` block:
```html
  <nav class="nav">
    <a href="/" class="nav-brand">Testing Harness</a>
    <div class="env-switcher">
```

Replace with:
```html
  <nav class="nav">
    <div style="display:flex; align-items:center; gap:1.5rem;">
      <a href="/" class="nav-brand">Testing Harness</a>
      <a href="/apps" style="color:#a0aec0; font-size:.875rem;">Apps</a>
    </div>
    <div class="env-switcher">
```

- [ ] **Step 2: Implement full `web/templates/apps.html`**

```html
{% extends "base.html" %}
{% block content %}
<div class="toolbar">
  <h1>App Management</h1>
  <a href="/apps/new" class="btn btn-primary">+ New App</a>
</div>

{% if active_apps %}
<table class="table">
  <thead>
    <tr>
      <th>App</th>
      <th>URL</th>
      <th>Tests</th>
      <th></th>
    </tr>
  </thead>
  <tbody>
    {% for app in active_apps %}
    <tr>
      <td>{{ app.app }}</td>
      <td><a href="{{ app.url }}" target="_blank">{{ app.url }}</a></td>
      <td>{{ app.tests | length }}</td>
      <td style="display:flex; gap:.5rem; justify-content:flex-end;">
        <a href="/apps/{{ app.app }}/edit" class="btn btn-sm">Edit</a>
        <button class="btn btn-sm" style="border-color:#ef4444;color:#f87171;"
                data-action="archive" data-app="{{ app.app | e }}">Archive</button>
      </td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% else %}
<p class="empty">No apps defined yet. <a href="/apps/new">Create one.</a></p>
{% endif %}

{% if archived_apps %}
<details style="margin-top:2rem;">
  <summary style="cursor:pointer; color:#718096; font-size:.875rem; margin-bottom:.75rem;">
    Archived Apps ({{ archived_apps | length }})
  </summary>
  <table class="table">
    <thead>
      <tr><th>App</th><th></th></tr>
    </thead>
    <tbody>
      {% for app in archived_apps %}
      <tr>
        <td>{{ app.app }}</td>
        <td style="display:flex; gap:.5rem; justify-content:flex-end;">
          <button class="btn btn-sm"
                  data-action="restore" data-app="{{ app.app | e }}">Restore</button>
          <button class="btn btn-sm" style="border-color:#ef4444;color:#f87171;"
                  data-action="delete-permanent" data-app="{{ app.app | e }}">Delete Permanently</button>
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</details>
{% endif %}

<div id="status-msg" style="display:none; margin-top:1rem; padding:.75rem 1rem;
     border-radius:6px; font-size:.875rem;"></div>

<script>
document.addEventListener('click', function(e) {
  const btn = e.target.closest('[data-action]');
  if (!btn) return;
  const action = btn.dataset.action;
  const app = btn.dataset.app;
  if (action === 'archive') handleArchive(app);
  else if (action === 'restore') handleRestore(app);
  else if (action === 'delete-permanent') handleDeletePermanent(app);
});

async function handleArchive(name) {
  if (!confirm('Archive "' + name + '"?')) return;
  const resp = await fetch('/api/apps/' + encodeURIComponent(name), { method: 'DELETE' });
  if (resp.ok) { location.reload(); }
  else { const d = await resp.json(); showError(d.detail || 'Archive failed'); }
}

async function handleRestore(name) {
  const resp = await fetch('/api/apps/' + encodeURIComponent(name) + '/restore', { method: 'POST' });
  if (resp.ok) { location.reload(); }
  else { const d = await resp.json(); showError(d.detail || 'Restore failed'); }
}

async function handleDeletePermanent(name) {
  if (!confirm('Permanently delete "' + name + '"? This cannot be undone.')) return;
  const resp = await fetch('/api/apps/' + encodeURIComponent(name) + '/permanent', { method: 'DELETE' });
  if (resp.ok) { location.reload(); }
  else { const d = await resp.json(); showError(d.detail || 'Delete failed'); }
}

function showError(msg) {
  const el = document.getElementById('status-msg');
  el.textContent = msg;
  el.style.display = 'block';
  el.style.background = 'rgba(239,68,68,.15)';
  el.style.color = '#f87171';
}
</script>
{% endblock %}
```

- [ ] **Step 3: Implement full `web/templates/app_form.html`**

The form uses Simple mode (structured inputs) and Advanced mode (raw YAML textarea). All dynamic DOM construction uses `createElement`/`appendChild`/`textContent` — no user data is placed into HTML strings.

```html
{% extends "base.html" %}
{% block content %}
<div class="breadcrumb">
  <a href="/apps">Apps</a> / {{ ("Edit " + app_name) if mode == "edit" else "New App" }}
</div>
<h1 style="margin-bottom:1.5rem;">{{ ("Edit " + app_name) if mode == "edit" else "New App" }}</h1>

<div id="error-banner" style="display:none; background:rgba(239,68,68,.15); color:#f87171;
     padding:.75rem 1rem; border-radius:6px; margin-bottom:1rem; font-size:.875rem;"></div>

<!-- Mode toggle -->
<div style="display:flex; gap:.5rem; margin-bottom:1.5rem;">
  <button type="button" id="btn-simple" class="btn btn-primary btn-sm"
          onclick="switchMode('simple')">Simple</button>
  <button type="button" id="btn-advanced" class="btn btn-sm"
          onclick="switchMode('advanced')">Advanced (YAML)</button>
</div>

<!-- Simple mode -->
<div id="simple-mode">
  <div style="margin-bottom:1rem;">
    <label style="display:block; margin-bottom:.25rem; font-size:.8rem; color:#a0aec0;">App Name *</label>
    <input id="f-name" type="text" class="search" style="max-width:400px;"
           value="{{ app_def.app | default('') | e }}"
           {{ 'readonly' if mode == 'edit' else '' }}
           placeholder="my-app">
    <div id="err-name" style="display:none; color:#f87171; font-size:.75rem; margin-top:.25rem;"></div>
  </div>

  <div style="margin-bottom:1rem;">
    <label style="display:block; margin-bottom:.25rem; font-size:.8rem; color:#a0aec0;">URL *</label>
    <input id="f-url" type="text" class="search" style="max-width:500px;"
           value="{{ app_def.url | default('') | e }}"
           placeholder="https://example.com">
    <div id="err-url" style="display:none; color:#f87171; font-size:.75rem; margin-top:.25rem;"></div>
  </div>

  <!-- Environments -->
  <div style="margin-bottom:1rem;">
    <label style="display:block; margin-bottom:.5rem; font-size:.8rem; color:#a0aec0;">Environments</label>
    <div id="envs-container">
      {% for key, url in (app_def.environments or {}).items() %}
      <div class="env-row" style="display:flex; gap:.5rem; margin-bottom:.5rem;">
        <input class="search env-key" type="text" placeholder="staging"
               value="{{ key | e }}" style="max-width:150px; margin-bottom:0;">
        <input class="search env-url" type="text" placeholder="https://staging.example.com"
               value="{{ url | e }}" style="flex:1; margin-bottom:0;">
        <button type="button" class="btn btn-sm" style="border-color:#ef4444;color:#f87171;"
                onclick="this.closest('.env-row').remove()">&#215;</button>
      </div>
      {% endfor %}
    </div>
    <button type="button" class="btn btn-sm" onclick="addEnvRow()"
            style="margin-top:.25rem;">+ Add Environment</button>
  </div>

  <!-- Tests -->
  <div style="margin-bottom:1.5rem;">
    <label style="display:block; margin-bottom:.5rem; font-size:.8rem; color:#a0aec0;">Tests</label>
    <div id="tests-container">
      {% for test in (app_def.tests or []) %}
      <div class="test-block" style="border:1px solid #2d3748; border-radius:6px; padding:1rem; margin-bottom:.75rem;">
        <div style="display:flex; gap:.5rem; margin-bottom:.75rem; align-items:center;">
          <input class="search test-name" type="text" placeholder="Test name"
                 value="{{ test.name | default('') | e }}" style="flex:1; margin-bottom:0;">
          <select class="search test-type" style="max-width:150px; margin-bottom:0;"
                  onchange="onTypeChange(this)">
            <option value="">-- type --</option>
            {% for t in ['availability', 'api', 'browser'] %}
            <option value="{{ t }}" {{ 'selected' if test.type == t else '' }}>{{ t }}</option>
            {% endfor %}
          </select>
          <button type="button" class="btn btn-sm" style="border-color:#ef4444;color:#f87171;"
                  onclick="this.closest('.test-block').remove()">&#215; Remove</button>
        </div>

        <div class="fields-availability"
             style="{{ '' if test.type == 'availability' else 'display:none;' }}">
          <label style="font-size:.75rem; color:#718096;">Expect Status</label>
          <input class="search field-expect-status" type="number"
                 value="{{ test.expect_status | default(200) }}"
                 style="max-width:100px; margin-bottom:0;">
        </div>

        <div class="fields-api"
             style="{{ '' if test.type == 'api' else 'display:none;' }}">
          <div style="display:flex; gap:.5rem; margin-bottom:.5rem;">
            <div style="flex:1;">
              <label style="font-size:.75rem; color:#718096;">Endpoint</label>
              <input class="search field-endpoint" type="text" placeholder="/api/health"
                     value="{{ test.endpoint | default('') | e }}" style="margin-bottom:0;">
            </div>
            <div>
              <label style="font-size:.75rem; color:#718096;">Method</label>
              <select class="search field-method" style="margin-bottom:0;">
                {% for m in ['GET','POST','PUT','DELETE'] %}
                <option value="{{ m }}" {{ 'selected' if test.method == m else '' }}>{{ m }}</option>
                {% endfor %}
              </select>
            </div>
            <div>
              <label style="font-size:.75rem; color:#718096;">Expect Status</label>
              <input class="search field-expect-status" type="number"
                     value="{{ test.expect_status | default(200) }}"
                     style="max-width:100px; margin-bottom:0;">
            </div>
          </div>
          <label style="font-size:.75rem; color:#718096;">Expect JSON (optional)</label>
          <textarea class="search field-expect-json" rows="2"
                    style="margin-bottom:0; font-family:monospace; font-size:.8rem;">{{ test.expect_json | default('') | e }}</textarea>
        </div>

        <div class="fields-browser"
             style="{{ '' if test.type == 'browser' else 'display:none;' }}">
          <label style="font-size:.75rem; color:#718096; display:block; margin-bottom:.5rem;">Steps</label>
          <div class="steps-container">
            {% for step in (test.steps or []) %}
            {% set action = step.keys() | list | first %}
            <div class="step-row"
                 style="display:flex; gap:.5rem; margin-bottom:.5rem; align-items:flex-start;">
              <select class="search step-action" style="max-width:200px; margin-bottom:0;"
                      onchange="onStepActionChange(this)">
                {% for a in ['navigate','fill','click','assert_text','assert_url_contains',
                             'wait_for_selector','wait_for_url','wait'] %}
                <option value="{{ a }}" {{ 'selected' if action == a else '' }}>{{ a }}</option>
                {% endfor %}
              </select>
              {% if action == 'fill' %}
              <input class="search step-fill-field" type="text" placeholder="CSS selector"
                     value="{{ step.fill.field | default('') | e }}"
                     style="flex:1; margin-bottom:0;">
              <input class="search step-fill-value" type="text" placeholder="value"
                     value="{{ step.fill.value | default('') | e }}"
                     style="flex:1; margin-bottom:0;">
              {% else %}
              <input class="search step-value" type="text" placeholder="value"
                     value="{{ step[action] | default('') | e }}"
                     style="flex:1; margin-bottom:0;">
              <span style="flex:1;"></span>
              {% endif %}
              <button type="button" class="btn btn-sm" style="border-color:#ef4444;color:#f87171;"
                      onclick="this.closest('.step-row').remove()">&#215;</button>
            </div>
            {% endfor %}
          </div>
          <button type="button" class="btn btn-sm" onclick="addStepRow(this)"
                  style="margin-top:.25rem;">+ Add Step</button>
        </div>
      </div>
      {% endfor %}
    </div>
    <button type="button" class="btn btn-sm" onclick="addTestBlock()"
            style="margin-top:.25rem;">+ Add Test</button>
  </div>
</div>

<!-- Advanced mode -->
<div id="advanced-mode" style="display:none;">
  <div id="yaml-error" style="display:none; color:#f87171; font-size:.8rem; margin-bottom:.5rem;"></div>
  <textarea id="yaml-textarea" class="search" rows="20"
            style="font-family:monospace; font-size:.8rem; white-space:pre;">{{ raw_yaml | e }}</textarea>
</div>

<div style="display:flex; gap:.75rem; margin-top:1.5rem;">
  <button type="button" class="btn btn-primary" onclick="submitForm()">Save</button>
  <a href="/apps" class="btn">Cancel</a>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/js-yaml/4.1.0/js-yaml.min.js"
        crossorigin="anonymous" referrerpolicy="no-referrer"></script>
<script>
const FORM_MODE = {{ '"edit"' if mode == 'edit' else '"create"' }};
const APP_NAME = {{ ('"' + app_name + '"') if app_name else '""' }};
let currentMode = 'simple';

// ---- Mode switching ----

function switchMode(target) {
  if (target === 'advanced') {
    document.getElementById('yaml-textarea').value = buildYaml();
    document.getElementById('simple-mode').style.display = 'none';
    document.getElementById('advanced-mode').style.display = 'block';
    document.getElementById('btn-simple').classList.remove('btn-primary');
    document.getElementById('btn-advanced').classList.add('btn-primary');
    currentMode = 'advanced';
  } else {
    if (currentMode === 'advanced') {
      showBanner('Switching back to Simple mode is not supported. Edit the YAML directly or reload the page to start over.');
      return;
    }
    document.getElementById('simple-mode').style.display = 'block';
    document.getElementById('advanced-mode').style.display = 'none';
    document.getElementById('btn-simple').classList.add('btn-primary');
    document.getElementById('btn-advanced').classList.remove('btn-primary');
    currentMode = 'simple';
  }
}

// ---- YAML serialisation from Simple form ----

function buildYaml() {
  return jsyaml.dump(collectSimpleFormData(), { lineWidth: -1 });
}

function collectSimpleFormData() {
  const obj = {};
  obj.app = document.getElementById('f-name').value.trim();
  obj.url = document.getElementById('f-url').value.trim();

  const envRows = document.querySelectorAll('#envs-container .env-row');
  if (envRows.length > 0) {
    obj.environments = {};
    envRows.forEach(function(row) {
      const k = row.querySelector('.env-key').value.trim();
      const v = row.querySelector('.env-url').value.trim();
      if (k) obj.environments[k] = v;
    });
  }

  obj.tests = [];
  document.querySelectorAll('#tests-container .test-block').forEach(function(block) {
    const test = {};
    test.name = block.querySelector('.test-name').value.trim();
    test.type = block.querySelector('.test-type').value;
    if (test.type === 'availability') {
      test.expect_status = parseInt(block.querySelector('.field-expect-status').value) || 200;
    } else if (test.type === 'api') {
      test.endpoint = block.querySelector('.field-endpoint').value.trim();
      test.method = block.querySelector('.field-method').value;
      test.expect_status = parseInt(block.querySelector('.field-expect-status').value) || 200;
      const ej = block.querySelector('.field-expect-json').value.trim();
      if (ej) test.expect_json = ej;
    } else if (test.type === 'browser') {
      test.steps = [];
      block.querySelectorAll('.step-row').forEach(function(stepRow) {
        const action = stepRow.querySelector('.step-action').value;
        if (action === 'fill') {
          test.steps.push({ fill: {
            field: stepRow.querySelector('.step-fill-field').value.trim(),
            value: stepRow.querySelector('.step-fill-value').value.trim(),
          }});
        } else {
          const val = stepRow.querySelector('.step-value').value.trim();
          const step = {};
          step[action] = val;
          test.steps.push(step);
        }
      });
    }
    obj.tests.push(test);
  });
  return obj;
}

// ---- Validation ----

function validateSimple(obj) {
  let ok = true;
  function setErr(id, msg) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = msg;
    el.style.display = msg ? 'block' : 'none';
    if (msg) ok = false;
  }
  setErr('err-name', obj.app ? '' : 'App name is required');
  if (!obj.url) {
    setErr('err-url', 'URL is required');
  } else if (!obj.url.startsWith('http://') && !obj.url.startsWith('https://')) {
    setErr('err-url', 'URL must start with http:// or https://');
  } else {
    setErr('err-url', '');
  }
  for (const t of obj.tests) {
    if (!t.name) { showBanner('Each test must have a name'); ok = false; break; }
    if (!t.type) { showBanner('Each test must have a type'); ok = false; break; }
    if (t.type === 'browser' && (!t.steps || t.steps.length === 0)) {
      showBanner('Browser tests must have at least one step'); ok = false; break;
    }
    if (t.type === 'api' && !t.endpoint) {
      showBanner('API tests must have an endpoint'); ok = false; break;
    }
  }
  return ok;
}

// ---- Submission ----

async function submitForm() {
  clearBanner();
  let appDef;
  if (currentMode === 'simple') {
    appDef = collectSimpleFormData();
    if (!validateSimple(appDef)) return;
  } else {
    const raw = document.getElementById('yaml-textarea').value;
    try {
      appDef = jsyaml.load(raw);
    } catch (e) {
      const yamlErr = document.getElementById('yaml-error');
      yamlErr.textContent = 'Invalid YAML: ' + e.message;
      yamlErr.style.display = 'block';
      showBanner('Invalid YAML: ' + e.message);
      return;
    }
    document.getElementById('yaml-error').style.display = 'none';
  }

  const url = FORM_MODE === 'edit'
    ? '/api/apps/' + encodeURIComponent(APP_NAME)
    : '/api/apps';
  const method = FORM_MODE === 'edit' ? 'PUT' : 'POST';

  const resp = await fetch(url, {
    method: method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ app_def: appDef }),
  });

  if (resp.ok) {
    window.location.href = '/apps';
  } else {
    const err = await resp.json();
    showBanner(err.detail || 'Save failed');
  }
}

// ---- Dynamic row builders (DOM API only — no user data in HTML strings) ----

function addEnvRow() {
  const container = document.getElementById('envs-container');
  const row = document.createElement('div');
  row.className = 'env-row';
  row.style.cssText = 'display:flex; gap:.5rem; margin-bottom:.5rem;';

  const keyInput = makeInput('search env-key', 'text', 'staging', 'max-width:150px; margin-bottom:0;');
  const urlInput = makeInput('search env-url', 'text', 'https://staging.example.com', 'flex:1; margin-bottom:0;');
  const removeBtn = makeRemoveBtn(function() { row.remove(); });

  row.appendChild(keyInput);
  row.appendChild(urlInput);
  row.appendChild(removeBtn);
  container.appendChild(row);
}

function addTestBlock() {
  const container = document.getElementById('tests-container');
  const block = document.createElement('div');
  block.className = 'test-block';
  block.style.cssText = 'border:1px solid #2d3748; border-radius:6px; padding:1rem; margin-bottom:.75rem;';

  const header = document.createElement('div');
  header.style.cssText = 'display:flex; gap:.5rem; margin-bottom:.75rem; align-items:center;';

  const nameInput = makeInput('search test-name', 'text', 'Test name', 'flex:1; margin-bottom:0;');
  const typeSelect = makeTypeSelect();
  const removeBtn = makeRemoveBtn(function() { block.remove(); });
  removeBtn.textContent = '\u00d7 Remove';

  header.appendChild(nameInput);
  header.appendChild(typeSelect);
  header.appendChild(removeBtn);
  block.appendChild(header);
  block.appendChild(makeAvailabilityFields());
  block.appendChild(makeApiFields());
  block.appendChild(makeBrowserFields());
  container.appendChild(block);
}

function makeTypeSelect() {
  const sel = document.createElement('select');
  sel.className = 'search test-type';
  sel.style.cssText = 'max-width:150px; margin-bottom:0;';
  sel.onchange = function() { onTypeChange(sel); };
  [['', '-- type --'], ['availability', 'availability'], ['api', 'api'], ['browser', 'browser']].forEach(function(pair) {
    const opt = document.createElement('option');
    opt.value = pair[0];
    opt.textContent = pair[1];
    sel.appendChild(opt);
  });
  return sel;
}

function makeAvailabilityFields() {
  const div = document.createElement('div');
  div.className = 'fields-availability';
  div.style.display = 'none';
  const lbl = document.createElement('label');
  lbl.style.cssText = 'font-size:.75rem; color:#718096;';
  lbl.textContent = 'Expect Status';
  const inp = makeInput('search field-expect-status', 'number', '', 'max-width:100px; margin-bottom:0;');
  inp.value = '200';
  div.appendChild(lbl);
  div.appendChild(inp);
  return div;
}

function makeApiFields() {
  const div = document.createElement('div');
  div.className = 'fields-api';
  div.style.display = 'none';

  const row = document.createElement('div');
  row.style.cssText = 'display:flex; gap:.5rem; margin-bottom:.5rem;';

  const endpointWrap = document.createElement('div');
  endpointWrap.style.flex = '1';
  const endpointLbl = makeLbl('Endpoint');
  const endpointInp = makeInput('search field-endpoint', 'text', '/api/health', 'margin-bottom:0;');
  endpointWrap.appendChild(endpointLbl);
  endpointWrap.appendChild(endpointInp);

  const methodWrap = document.createElement('div');
  const methodLbl = makeLbl('Method');
  const methodSel = document.createElement('select');
  methodSel.className = 'search field-method';
  methodSel.style.marginBottom = '0';
  ['GET', 'POST', 'PUT', 'DELETE'].forEach(function(m) {
    const o = document.createElement('option');
    o.value = m; o.textContent = m;
    methodSel.appendChild(o);
  });
  methodWrap.appendChild(methodLbl);
  methodWrap.appendChild(methodSel);

  const statusWrap = document.createElement('div');
  const statusLbl = makeLbl('Expect Status');
  const statusInp = makeInput('search field-expect-status', 'number', '', 'max-width:100px; margin-bottom:0;');
  statusInp.value = '200';
  statusWrap.appendChild(statusLbl);
  statusWrap.appendChild(statusInp);

  row.appendChild(endpointWrap);
  row.appendChild(methodWrap);
  row.appendChild(statusWrap);

  const jsonLbl = makeLbl('Expect JSON (optional)');
  const jsonTA = document.createElement('textarea');
  jsonTA.className = 'search field-expect-json';
  jsonTA.rows = 2;
  jsonTA.style.cssText = 'margin-bottom:0; font-family:monospace; font-size:.8rem;';

  div.appendChild(row);
  div.appendChild(jsonLbl);
  div.appendChild(jsonTA);
  return div;
}

function makeBrowserFields() {
  const div = document.createElement('div');
  div.className = 'fields-browser';
  div.style.display = 'none';

  const lbl = document.createElement('label');
  lbl.style.cssText = 'font-size:.75rem; color:#718096; display:block; margin-bottom:.5rem;';
  lbl.textContent = 'Steps';

  const stepsContainer = document.createElement('div');
  stepsContainer.className = 'steps-container';

  const addBtn = document.createElement('button');
  addBtn.type = 'button';
  addBtn.className = 'btn btn-sm';
  addBtn.style.marginTop = '.25rem';
  addBtn.textContent = '+ Add Step';
  addBtn.onclick = function() { addStepRow(addBtn); };

  div.appendChild(lbl);
  div.appendChild(stepsContainer);
  div.appendChild(addBtn);
  return div;
}

function addStepRow(btn) {
  const container = btn.previousElementSibling;
  const row = document.createElement('div');
  row.className = 'step-row';
  row.style.cssText = 'display:flex; gap:.5rem; margin-bottom:.5rem; align-items:flex-start;';

  const actionSel = makeStepActionSelect();
  const valueInp = makeInput('search step-value', 'text', 'value', 'flex:1; margin-bottom:0;');
  const spacer = document.createElement('span');
  spacer.style.flex = '1';
  const removeBtn = makeRemoveBtn(function() { row.remove(); });

  row.appendChild(actionSel);
  row.appendChild(valueInp);
  row.appendChild(spacer);
  row.appendChild(removeBtn);
  container.appendChild(row);
}

function makeStepActionSelect() {
  const sel = document.createElement('select');
  sel.className = 'search step-action';
  sel.style.cssText = 'max-width:200px; margin-bottom:0;';
  sel.onchange = function() { onStepActionChange(sel); };
  ['navigate','fill','click','assert_text','assert_url_contains',
   'wait_for_selector','wait_for_url','wait'].forEach(function(a) {
    const o = document.createElement('option');
    o.value = a; o.textContent = a;
    sel.appendChild(o);
  });
  return sel;
}

function onTypeChange(select) {
  const block = select.closest('.test-block');
  block.querySelector('.fields-availability').style.display = select.value === 'availability' ? '' : 'none';
  block.querySelector('.fields-api').style.display = select.value === 'api' ? '' : 'none';
  block.querySelector('.fields-browser').style.display = select.value === 'browser' ? '' : 'none';
}

function onStepActionChange(select) {
  const row = select.closest('.step-row');
  const removeBtn = row.querySelector('button');
  // Remove existing value inputs and spacers
  row.querySelectorAll('.step-value, .step-fill-field, .step-fill-value, span').forEach(function(el) { el.remove(); });
  if (select.value === 'fill') {
    const f = makeInput('search step-fill-field', 'text', 'CSS selector', 'flex:1; margin-bottom:0;');
    const v = makeInput('search step-fill-value', 'text', 'value', 'flex:1; margin-bottom:0;');
    row.insertBefore(f, removeBtn);
    row.insertBefore(v, removeBtn);
  } else {
    const v = makeInput('search step-value', 'text', 'value', 'flex:1; margin-bottom:0;');
    const sp = document.createElement('span');
    sp.style.flex = '1';
    row.insertBefore(v, removeBtn);
    row.insertBefore(sp, removeBtn);
  }
}

// ---- DOM helpers ----

function makeInput(cls, type, placeholder, style) {
  const inp = document.createElement('input');
  inp.className = cls;
  inp.type = type;
  inp.placeholder = placeholder;
  inp.style.cssText = style;
  return inp;
}

function makeLbl(text) {
  const lbl = document.createElement('label');
  lbl.style.cssText = 'font-size:.75rem; color:#718096;';
  lbl.textContent = text;
  return lbl;
}

function makeRemoveBtn(onclickFn) {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'btn btn-sm';
  btn.style.cssText = 'border-color:#ef4444;color:#f87171;';
  btn.textContent = '\u00d7';
  btn.onclick = onclickFn;
  return btn;
}

function showBanner(msg) {
  const el = document.getElementById('error-banner');
  el.textContent = msg;
  el.style.display = 'block';
}

function clearBanner() {
  const el = document.getElementById('error-banner');
  el.textContent = '';
  el.style.display = 'none';
}
</script>
{% endblock %}
```

- [ ] **Step 4: Verify all tests still pass**

```bash
cd /c/webtestingharness && python -m pytest -v
```

Expected: All tests PASS.

- [ ] **Step 5: Smoke-test in browser**

```bash
cd /c/webtestingharness && python -m web.main
```

Visit `http://localhost:8000/apps` and verify:
- Nav shows "Testing Harness | Apps" alongside env buttons
- "New App" button links to `/apps/new`
- Create form loads with Simple mode active
- Adding/removing environments, tests, and browser steps works
- Switching to Advanced mode shows serialised YAML
- Attempting to switch back shows the warning message
- Save in both Simple and Advanced mode redirects to `/apps`
- Archive, Restore, Delete Permanently buttons work on the list page

- [ ] **Step 6: Commit**

```bash
cd /c/webtestingharness && git add web/templates/base.html web/templates/apps.html web/templates/app_form.html && git commit -m "feat: add app management UI templates with Simple/Advanced form modes"
```

---

## Final verification

```bash
cd /c/webtestingharness && python -m pytest -v
```

All tests should PASS. New test coverage:
- `tests/test_app_manager.py` — 16 unit tests
- `tests/test_web_apps.py` — 14 integration tests (10 mutation API + 4 HTML routes)
