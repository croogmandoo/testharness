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
