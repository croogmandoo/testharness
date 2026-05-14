"""Tier 3 UX smoke tests: keymap.js, cmdk.js, and mobile rules
on the app detail template.

We don't run JS in CI, so these tests verify the *plumbing*:
- New static files are served at /static/keymap.js and /static/cmdk.js.
- base.html includes both <script> tags and both mount divs.
- style.css defines the .cmdk-modal and .kbd-help classes.
- Detail template + media-query plumbing exist for mobile.
"""
import os
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from harness.db import Database
from web.auth import get_current_user
from web.main import create_app

WEB_DIR = os.path.join(os.path.dirname(__file__), "..", "web")
STATIC_DIR = os.path.join(WEB_DIR, "static")
TEMPLATE_DIR = os.path.join(WEB_DIR, "templates")


def _seed_admin(db):
    user = {
        "id": str(uuid.uuid4()),
        "username": "_admin",
        "display_name": "Admin",
        "email": None,
        "password_hash": None,
        "role": "admin",
        "auth_provider": "local",
        "role_override": 0,
        "is_active": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_login_at": None,
    }
    db.insert_user(user)
    return user


@pytest.fixture
def client(tmp_path):
    db = Database(str(tmp_path / "tier3.db"))
    db.init_schema()
    admin = _seed_admin(db)
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    app = create_app(
        db=db,
        config={"default_environment": "production",
                "environments": {"production": {"label": "Production"}}},
        apps_dir=str(apps_dir),
    )
    app.dependency_overrides[get_current_user] = lambda: admin
    return TestClient(app)


# ── Static files load ────────────────────────────────────────────────────────

def test_keymap_js_served(client):
    with client:
        resp = client.get("/static/keymap.js")
    assert resp.status_code == 200
    assert b"keydown" in resp.content


def test_cmdk_js_served(client):
    with client:
        resp = client.get("/static/cmdk.js")
    assert resp.status_code == 200
    assert b"cmdk-modal" in resp.content


# ── base.html wiring ─────────────────────────────────────────────────────────

def test_base_html_includes_new_scripts():
    path = os.path.join(TEMPLATE_DIR, "base.html")
    body = open(path, "r", encoding="utf-8").read()
    assert "/static/keymap.js" in body, "keymap.js script tag missing"
    assert "/static/cmdk.js" in body, "cmdk.js script tag missing"


def test_base_html_includes_mount_divs():
    path = os.path.join(TEMPLATE_DIR, "base.html")
    body = open(path, "r", encoding="utf-8").read()
    assert 'id="cmdk-mount"' in body
    assert 'id="kbd-help-mount"' in body


def test_base_html_renders_with_mount_divs(client):
    """End-to-end render of any page through base.html exposes the mount divs."""
    with client:
        resp = client.get("/")
    assert resp.status_code == 200
    assert b'id="cmdk-mount"' in resp.content
    assert b'id="kbd-help-mount"' in resp.content
    assert b"/static/keymap.js" in resp.content
    assert b"/static/cmdk.js" in resp.content


# ── style.css ────────────────────────────────────────────────────────────────

def test_style_css_defines_tier3_classes():
    path = os.path.join(STATIC_DIR, "style.css")
    body = open(path, "r", encoding="utf-8").read()
    assert ".cmdk-modal" in body
    assert ".cmdk-backdrop" in body
    assert ".cmdk-input" in body
    assert ".cmdk-result" in body
    assert ".cmdk-result.active" in body
    assert ".kbd-help" in body


def test_style_css_has_tier3_header():
    path = os.path.join(STATIC_DIR, "style.css")
    body = open(path, "r", encoding="utf-8").read()
    assert "=== Tier 3 UX ===" in body


def test_style_css_has_mobile_breakpoint():
    """Mobile rules for the detail page must live under a max-width: 640px
    media query so desktop styling is untouched."""
    path = os.path.join(STATIC_DIR, "style.css")
    body = open(path, "r", encoding="utf-8").read()
    assert "@media (max-width: 640px)" in body


# ── detail.html / mobile viewport ────────────────────────────────────────────

def test_detail_template_inherits_viewport_meta():
    """detail.html extends base.html which has the responsive viewport meta —
    confirming mobile rendering works without a per-page override."""
    base_body = open(os.path.join(TEMPLATE_DIR, "base.html"),
                     "r", encoding="utf-8").read()
    detail_body = open(os.path.join(TEMPLATE_DIR, "detail.html"),
                       "r", encoding="utf-8").read()
    assert 'name="viewport"' in base_body
    assert "width=device-width" in base_body
    assert 'extends "base.html"' in detail_body


def test_cmdk_js_references_api_apps_endpoint():
    """The Cmd+K palette must hit GET /api/apps for the dynamic app list,
    not just scrape the DOM. We pin the literal URL so a casual rename
    breaks loudly here."""
    path = os.path.join(STATIC_DIR, "cmdk.js")
    body = open(path, "r", encoding="utf-8").read()
    assert '/api/apps' in body, "cmdk.js must fetch /api/apps"
    # The fetch must travel session cookies / api keys with the request.
    assert 'credentials: "same-origin"' in body
    # Endpoint URL is centralised in a named constant — easier to audit.
    assert 'APPS_ENDPOINT' in body


def test_api_apps_endpoint_returns_json_for_authenticated_user(client):
    """Authenticated user hitting /api/apps gets a JSON list (possibly
    empty on a fresh DB). This is the contract cmdk.js depends on."""
    with client:
        resp = client.get("/api/apps")
    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith("application/json")
    data = resp.json()
    assert isinstance(data, list), f"expected list, got {type(data).__name__}"


def test_api_apps_endpoint_returns_app_keyed_rows(client):
    """Each row in /api/apps must carry an 'app' key — that's what
    cmdk.js dereferences as the label. Seeding an app_state row
    exercises the populated-list path."""
    from datetime import datetime, timezone
    from harness.models import AppState
    from web.main import get_db
    db = get_db()
    db.upsert_app_state(AppState(
        app="paletteapp",
        environment="production",
        test_name="ping",
        state="passing",
        since=datetime.now(timezone.utc).isoformat(),
    ))
    with client:
        resp = client.get("/api/apps?environment=production")
    assert resp.status_code == 200
    rows = resp.json()
    assert any(r.get("app") == "paletteapp" for r in rows), \
        f"expected 'paletteapp' in /api/apps response, got {rows!r}"


def test_api_apps_endpoint_requires_auth(tmp_path):
    """Without a session/api-key, /api/apps must not leak the app list.
    The route depends on get_current_user, which returns 401/redirect
    when no credentials are present."""
    db = Database(str(tmp_path / "noauth.db"))
    db.init_schema()
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    app = create_app(
        db=db,
        config={"default_environment": "production",
                "environments": {"production": {"label": "Production"}}},
        apps_dir=str(apps_dir),
    )
    c = TestClient(app, follow_redirects=False)
    with c:
        resp = c.get("/api/apps")
    # Either 401 (browser-style) or 302/303 (redirect to login) are
    # acceptable — what matters is that anonymous traffic doesn't get
    # 200-OK with a JSON body of app names.
    assert resp.status_code in (401, 302, 303, 307), (
        f"/api/apps should not be anonymous; got {resp.status_code}"
    )


def test_detail_classes_have_mobile_rules():
    """The mobile media query targets at least one class actually used by
    detail.html so the rules aren't dead code."""
    css = open(os.path.join(STATIC_DIR, "style.css"),
               "r", encoding="utf-8").read()
    detail = open(os.path.join(TEMPLATE_DIR, "detail.html"),
                  "r", encoding="utf-8").read()
    # Extract the mobile media-query block
    needle = "@media (max-width: 640px)"
    idx = css.find(needle)
    assert idx != -1
    # Take everything from the @media opening through end-of-file —
    # cheaper than nested-brace matching for a smoke test.
    mobile_block = css[idx:]
    # At least one of these detail-page classes should receive mobile rules.
    # The CSS references them with a leading "." while the template uses
    # bare names — strip the dot when checking detail.html membership.
    referenced = [
        cls for cls in (".run-meta", ".run-strip", ".test-card",
                        ".breadcrumb", ".step-log")
        if cls in mobile_block and cls[1:] in detail
    ]
    assert referenced, "no detail.html classes have mobile rules"
