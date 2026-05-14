"""Tests for the asset cache-buster in web/templating.py.

The goal: every <link rel=stylesheet> and <script src=...> tag pointing at
/static/*.css or *.js carries a ?v=<hash> query param so browsers refetch
the file when its content changes. Without this, `compose up -d` to a new
image leaves the user's browser running yesterday's CSS.
"""
import bcrypt
import re
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from harness.db import Database
from web.main import create_app

CONFIG = {
    "default_environment": "production",
    "environments": {"production": {"label": "Production"}},
    "auth": {"session_hours": 8, "secure_cookie": False},
}


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.init_schema()
    return d


@pytest.fixture
def client(db, tmp_path):
    (tmp_path / "apps").mkdir()
    app = create_app(db=db, config=CONFIG, apps_dir=str(tmp_path / "apps"))
    from web.csrf import verify_csrf
    app.dependency_overrides[verify_csrf] = lambda: None
    return TestClient(app, follow_redirects=False)


def test_asset_version_is_stable_within_a_process():
    """Same process must produce the same hash — that's the cache contract."""
    from web.templating import _asset_version
    assert _asset_version() == _asset_version()
    assert len(_asset_version()) == 10  # short hash


def test_asset_version_is_a_hex_string():
    """A non-hex value would break query-param semantics (or look suspicious)."""
    from web.templating import _asset_version
    v = _asset_version()
    assert re.fullmatch(r"[0-9a-f]+", v), f"asset_version not hex: {v!r}"


def test_login_page_links_style_css_with_cache_buster(client):
    """Unauthenticated route — verifies the cache-buster reaches non-base templates."""
    resp = client.get("/auth/login")
    assert resp.status_code == 200, resp.text
    body = resp.text
    # Must have ?v=<10-char-hex> on the stylesheet link
    m = re.search(r'href="/static/style\.css\?v=([0-9a-f]+)"', body)
    assert m, f"style.css link missing cache-buster in /auth/login: {body[:500]!r}"
    assert len(m.group(1)) == 10


def test_dashboard_links_all_static_assets_with_cache_buster(client, db):
    """Base template — verifies CSS *and* JS bundles get the param."""
    # First-run middleware redirects to /setup when there are no users;
    # seed one so we hit the real dashboard (which then redirects to /auth/login
    # because we're not authed — we just want the rendered HTML).
    pw = bcrypt.hashpw(b"Secret123!", bcrypt.gensalt()).decode()
    db.insert_user({
        "id": str(uuid.uuid4()),
        "username": "alice",
        "display_name": "Alice",
        "email": None,
        "password_hash": pw,
        "role": "admin",
        "auth_provider": "local",
        "role_override": 0,
        "is_active": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_login_at": None,
    })
    # Log in to actually render the base-template page.
    login = client.post("/auth/login", data={"username": "alice", "password": "Secret123!"})
    assert login.status_code in (302, 303), login.text
    session_cookie = login.cookies.get("session")
    assert session_cookie

    resp = client.get("/", cookies={"session": session_cookie})
    assert resp.status_code == 200, resp.text
    body = resp.text

    # All four /static refs in base.html must carry ?v=<hash>
    for asset in ("style.css", "harness.js", "keymap.js", "cmdk.js"):
        pattern = rf'/static/{re.escape(asset)}\?v=([0-9a-f]+)"'
        m = re.search(pattern, body)
        assert m, f"missing cache-buster on /static/{asset} in base.html"
        assert len(m.group(1)) == 10


def test_asset_version_changes_when_static_content_changes(tmp_path, monkeypatch):
    """If /static contents change between processes, the hash must change.

    Simulates a deploy: we swap out the _STATIC_DIR module-level constant,
    clear the lru_cache, recompute, and verify the hash differs.
    """
    from web import templating

    static_a = tmp_path / "a"
    static_a.mkdir()
    (static_a / "style.css").write_bytes(b"body { color: red; }")
    (static_a / "harness.js").write_bytes(b"console.log('a');")

    static_b = tmp_path / "b"
    static_b.mkdir()
    (static_b / "style.css").write_bytes(b"body { color: blue; }")  # changed
    (static_b / "harness.js").write_bytes(b"console.log('a');")

    monkeypatch.setattr(templating, "_STATIC_DIR", str(static_a))
    templating._asset_version.cache_clear()
    hash_a = templating._asset_version()

    monkeypatch.setattr(templating, "_STATIC_DIR", str(static_b))
    templating._asset_version.cache_clear()
    hash_b = templating._asset_version()

    assert hash_a != hash_b, "asset hash unchanged despite CSS edit"


def test_asset_version_falls_back_when_static_dir_missing(tmp_path, monkeypatch):
    """Degenerate dev setups shouldn't crash the page render."""
    from web import templating
    monkeypatch.setattr(templating, "_STATIC_DIR", str(tmp_path / "does-not-exist"))
    templating._asset_version.cache_clear()
    v = templating._asset_version()
    assert v  # any non-empty string is fine
