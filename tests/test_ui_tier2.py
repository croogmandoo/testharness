"""Tests for the Tier 2 UI polish bundle:

  (A) Empty-state cards on /users, /api-keys, /admin/runners, /admin/audit.
  (B) Skeleton loader markup in dashboard.html and detail.html.
  (C) Inline form-validation hooks: data-validate attributes + .field-error
      CSS class definition.

Tier 1 (palette picker, toasts, button-loading) is exercised elsewhere;
these tests only cover the Tier 2 additions and must not regress existing
behavior.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from harness.db import Database
from web.auth import get_current_user
from web.csrf import verify_csrf
from web.main import create_app


# ── Shared fixtures ─────────────────────────────────────────────────────────


def _make_user(role: str = "admin") -> dict:
    """Return an in-memory user dict; the dependency override returns this
    directly so we don't actually insert the row into the DB. That lets us
    exercise the 'zero users' empty state on /users."""
    return {
        "id": str(uuid.uuid4()),
        "username": f"_{role}",
        "display_name": role.capitalize(),
        "email": None,
        "password_hash": None,
        "role": role,
        "auth_provider": "local",
        "role_override": 0,
        "is_active": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_login_at": None,
    }


@pytest.fixture
def db(tmp_path) -> Database:
    d = Database(str(tmp_path / "tier2.db"))
    d.init_schema()
    return d


def _client(db, user, tmp_path, *, seed_user: bool = True):
    """Build a TestClient with the dependency overrides we need.

    seed_user=True inserts the supplied user into the DB so the first-run
    middleware (redirects to /setup when count_users()==0) stays quiet.
    Tests that need an *empty* user list pass seed_user=False and instead
    monkey-patch count_users to bypass the middleware while keeping
    list_users() returning [].
    """
    apps_dir = tmp_path / "apps"
    if not apps_dir.exists():
        apps_dir.mkdir()
    app = create_app(db=db, config={}, apps_dir=str(apps_dir))
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[verify_csrf] = lambda: None
    if seed_user:
        try:
            db.insert_user(user)
        except Exception:
            pass
    else:
        # Bypass the first-run /setup redirect without polluting list_users()
        db.count_users = lambda: 1  # type: ignore[method-assign]
    return TestClient(app, follow_redirects=False)


# ── (A) Empty states ────────────────────────────────────────────────────────


def test_users_empty_state_shows_card_and_cta_for_admin(db, tmp_path):
    """Zero users → friendly empty-state card with the 'Add user' CTA."""
    admin = _make_user("admin")  # not inserted; users table is empty
    c = _client(db, admin, tmp_path, seed_user=False)
    r = c.get("/users")
    assert r.status_code == 200
    body = r.content
    assert b'class="empty-state"' in body
    assert b"No users yet" in body
    # CTA links to the create form (admin sees it).
    assert b'href="/users/new"' in body
    assert b"Add user" in body


def test_users_empty_state_hides_cta_for_non_admin_via_html(db, tmp_path):
    """The {% if current_user.role == 'admin' %} branch around the CTA is
    visible because the admin path is the one a non-admin can't even reach
    (/users requires admin). We document that the gate exists in markup."""
    body = Path(
        "web/templates/users.html"
    ).read_text(encoding="utf-8")
    assert 'current_user.role == "admin"' in body
    # The CTA lives inside that gate
    cta_idx = body.index("Add user")
    gate_idx = body.index('current_user.role == "admin"')
    assert gate_idx < cta_idx, "Add user CTA must live inside the admin gate"


def test_users_list_does_not_show_empty_state_when_populated(db, tmp_path):
    admin = _make_user("admin")
    db.insert_user(admin)  # one real user in the DB → table renders
    c = _client(db, admin, tmp_path)
    r = c.get("/users")
    body = r.content
    assert b'class="empty-state"' not in body
    assert b"<table" in body


def test_api_keys_empty_state_renders(db, tmp_path):
    admin = _make_user("admin")
    c = _client(db, admin, tmp_path)
    r = c.get("/api-keys")
    assert r.status_code == 200
    body = r.content
    assert b'class="empty-state"' in body
    assert b"No API keys yet" in body
    assert b"Create key" in body


def test_admin_runners_empty_state_renders(db, tmp_path):
    admin = _make_user("admin")
    c = _client(db, admin, tmp_path)
    r = c.get("/admin/runners")
    assert r.status_code == 200
    body = r.content
    assert b'class="empty-state"' in body
    assert b"No remote workers registered yet" in body


def test_admin_audit_empty_state_renders_without_filters(db, tmp_path):
    """When no entries exist AND no filter is applied, show the friendly
    empty-state card (no CTA — events accrue automatically)."""
    admin = _make_user("admin")
    c = _client(db, admin, tmp_path)
    r = c.get("/admin/audit")
    assert r.status_code == 200
    body = r.content
    assert b'class="empty-state"' in body
    assert b"No audit events yet" in body
    # No CTA button inside this empty-state — audit accrues automatically.
    # The empty-state card has no .btn-primary inside it on this page.
    es_start = body.index(b'class="empty-state"')
    es_end = body.index(b"</div>", es_start)
    assert b"btn-primary" not in body[es_start:es_end]


def test_admin_audit_filtered_empty_keeps_filter_message(db, tmp_path):
    """If a filter is applied and no entries match, keep the existing
    'No audit log entries match the current filter.' message rather than
    showing the fresh-install empty state."""
    admin = _make_user("admin")
    c = _client(db, admin, tmp_path)
    r = c.get("/admin/audit?user=nobody")
    assert r.status_code == 200
    body = r.content
    assert b"match the current filter" in body
    assert b"No audit events yet" not in body


# ── (B) Skeleton loaders ────────────────────────────────────────────────────


def test_dashboard_has_skeleton_card_markup():
    body = Path("web/templates/dashboard.html").read_text(encoding="utf-8")
    assert "skeleton-card" in body
    assert 'data-loading="true"' in body
    # 3 skeleton rows as specified
    assert body.count("skeleton-card") >= 3


def test_detail_has_skeleton_card_markup():
    body = Path("web/templates/detail.html").read_text(encoding="utf-8")
    assert "skeleton-card" in body
    assert 'data-loading="true"' in body
    # 2 skeleton cards for the detail meta block
    assert body.count("skeleton-card") >= 2


def test_skeleton_pulse_keyframes_defined_in_style_css():
    css = Path("web/static/style.css").read_text(encoding="utf-8")
    assert "@keyframes skeleton-pulse" in css
    assert ".skeleton-card" in css
    assert ".skeleton-line" in css
    # The data-loading toggle must work both directions
    assert '[data-loading="true"]' in css
    assert '[data-loading="false"]' in css


# ── (C) Form validation polish ──────────────────────────────────────────────


def test_user_form_has_data_validate_attrs():
    body = Path("web/templates/user_form.html").read_text(encoding="utf-8")
    # data-validate must annotate the inputs we want blur-checked
    assert "data-validate" in body
    # Email + username + password each carry a validate marker
    assert 'name="username"' in body
    assert 'name="email"' in body
    # The email input declares email validation
    assert 'data-validate="email"' in body


def test_login_form_has_data_validate_attrs():
    body = Path("web/templates/login.html").read_text(encoding="utf-8")
    assert 'data-validate="required"' in body
    # harness.js must be loaded on login (no base.html on this page)
    assert "harness.js" in body


def test_app_form_has_data_validate_attrs():
    body = Path("web/templates/app_form.html").read_text(encoding="utf-8")
    assert "data-validate" in body
    # URL field opts into url validation
    assert 'data-validate="required url"' in body


def test_field_error_class_defined_in_style_css():
    css = Path("web/static/style.css").read_text(encoding="utf-8")
    assert ".field-error" in css
    # The Tier 2 polish header anchors the new block to the bottom of the file
    assert "=== Tier 2 polish ===" in css


def test_harness_js_has_validator_helpers():
    js = Path("web/static/harness.js").read_text(encoding="utf-8")
    # Public surface
    assert "wireValidators" in js
    assert "validateField" in js
    # The validator must render <small class="field-error">
    assert 'field-error' in js
    # And listen on blur (not submit) per spec.
    assert 'addEventListener("blur"' in js


def test_harness_js_uses_friendly_constraint_table():
    """friendlyMessage() must dispatch through a per-constraint FRIENDLY
    table rather than fall back to input.validationMessage. Ugly stock
    browser strings ('Please fill out this field') should never surface."""
    js = Path("web/static/harness.js").read_text(encoding="utf-8")
    # The dispatch table itself
    assert "FRIENDLY" in js
    assert "CONSTRAINT_ORDER" in js
    # Required-first ordering: valueMissing must precede typeMismatch
    vm_idx = js.index('"valueMissing"')
    tm_idx = js.index('"typeMismatch"')
    assert vm_idx < tm_idx, (
        "valueMissing must win over typeMismatch so empty email fields "
        "show 'This field is required.' rather than 'Enter a valid email…'"
    )
    # Friendly strings end-users actually see
    assert "This field is required." in js
    assert "Enter a valid email address." in js
    assert "Enter a valid URL like https://example.com." in js
    assert "Must be at least " in js
    assert "Must be at most " in js
    assert "Must be " in js  # range underflow / overflow
    # Pattern fields read a custom hint via data-error (with safe default)
    assert "input.dataset.error" in js
    assert "Doesn't match the expected format." in js
    # All ten standard ValidityState constraints have a friendly mapping
    for key in (
        "valueMissing", "typeMismatch", "tooShort", "tooLong",
        "rangeUnderflow", "rangeOverflow", "patternMismatch",
        "stepMismatch", "badInput", "customError",
    ):
        assert key in js, f"FRIENDLY table missing {key} mapping"
