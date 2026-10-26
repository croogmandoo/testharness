"""Security regression tests for login and identity-provider hardening.

Covers: LDAP/local account collision, LDAP input escaping, GitHub OAuth
allowlisting and rename takeover, per-IP login throttling, constant-time
user lookup and the first-run setup token.
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import bcrypt
import pytest
from fastapi.testclient import TestClient

from web.main import create_app

LDAP_CFG = {
    "enabled": True,
    "server": "ldap://dc.corp.com",
    "port": 389,
    "use_tls": False,
    "base_dn": "DC=corp,DC=com",
    "user_search_filter": "(sAMAccountName={username})",
    "group_attribute": "memberOf",
    "role_map": {},
    "default_role": "read_only",
}


def _local_user(db, name, role="admin", pw="password123", is_active=1):
    uid = str(uuid.uuid4())
    db.insert_user({
        "id": uid, "username": name, "display_name": name, "email": None,
        "password_hash": bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode(),
        "role": role, "auth_provider": "local", "role_override": 0,
        "is_active": is_active,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_login_at": None,
    })
    return uid


def _ldap_client(db, apps_dir):
    config = {
        "environments": {"production": {"label": "Production"}},
        "auth": {"session_hours": 8, "secure_cookie": False, "ldap": LDAP_CFG},
    }
    app = create_app(db=db, config=config, apps_dir=str(apps_dir))
    from web.csrf import verify_csrf
    app.dependency_overrides[verify_csrf] = lambda: None
    return TestClient(app, follow_redirects=False)


def _fake_ldap(username, password, ldap_cfg):
    return {"username": username, "display_name": "Directory User",
            "email": None, "role": "read_only"}


# ── #2 LDAP / local account collision ────────────────────────────────────────

def test_ldap_upsert_does_not_take_over_local_admin(db):
    """Regression for the PoC: an LDAP identity named 'admin' must not
    resolve to (or modify) the local admin account."""
    admin_id = _local_user(db, "admin", "admin")
    u = db.upsert_ldap_user("admin", "Some AD user", None, "read_only")
    assert u is None
    row = db.get_user_by_id(admin_id)
    assert row["auth_provider"] == "local"
    assert row["role"] == "admin"
    assert row["display_name"] == "admin"


def test_ldap_login_colliding_with_local_admin_is_401(db, apps_dir, monkeypatch):
    admin_id = _local_user(db, "admin", "admin", pw="real-admin-pw")
    monkeypatch.setattr("harness.auth_manager.ldap_authenticate", _fake_ldap)
    c = _ldap_client(db, apps_dir)
    r = c.post("/auth/login", data={"username": "admin", "password": "ad-password"})
    assert r.status_code == 401
    assert "session" not in r.cookies
    assert db.get_user_by_id(admin_id)["role"] == "admin"
    actions = [e["action"] for e in db.list_audit_log()]
    assert "auth.ldap_conflict" in actions


def test_ldap_login_inactive_user_is_401(db, apps_dir, monkeypatch):
    u = db.upsert_ldap_user("bob", "Bob", None, "read_only")
    db.update_user(u["id"], is_active=0)
    monkeypatch.setattr("harness.auth_manager.ldap_authenticate", _fake_ldap)
    c = _ldap_client(db, apps_dir)
    r = c.post("/auth/login", data={"username": "bob", "password": "pw"})
    assert r.status_code == 401
    assert "session" not in r.cookies


def test_ldap_login_active_user_succeeds(db, apps_dir, monkeypatch):
    monkeypatch.setattr("harness.auth_manager.ldap_authenticate", _fake_ldap)
    c = _ldap_client(db, apps_dir)
    r = c.post("/auth/login", data={"username": "carol", "password": "pw"})
    assert r.status_code == 303
    assert "session" in r.cookies
    assert db.get_user_by_username("carol")["auth_provider"] == "ldap"


def _ldap_as_typed(username, password, ldap_cfg):
    """Fake directory that echoes the typed name in whatever case, but
    (like AD) treats it case-insensitively — i.e. no canonicalisation."""
    return {"username": username, "display_name": "Directory User",
            "email": None, "role": "admin"}


def test_ldap_disabled_user_cannot_return_with_different_case(db, apps_dir, monkeypatch):
    u = db.upsert_ldap_user("bob", "Bob", None, "read_only")
    db.update_user(u["id"], is_active=0)
    monkeypatch.setattr("harness.auth_manager.ldap_authenticate", _ldap_as_typed)
    c = _ldap_client(db, apps_dir)
    r = c.post("/auth/login", data={"username": "BOB", "password": "pw"})
    assert r.status_code == 401
    assert "session" not in r.cookies
    assert len(db.get_users_by_username_ci("bob")) == 1


def test_ldap_case_variant_keeps_role_override(db):
    u = db.upsert_ldap_user("bob", "Bob", None, "read_only")
    db.update_user(u["id"], role="read_only", role_override=1)
    again = db.upsert_ldap_user("BOB", "Bob", None, "admin")
    assert again["id"] == u["id"]
    assert again["role"] == "read_only"


def test_ldap_case_variant_of_local_admin_is_conflict(db, apps_dir, monkeypatch):
    admin_id = _local_user(db, "admin", "admin", pw="real-admin-pw")
    monkeypatch.setattr("harness.auth_manager.ldap_authenticate", _ldap_as_typed)
    c = _ldap_client(db, apps_dir)
    r = c.post("/auth/login", data={"username": "Admin", "password": "ad-pw"})
    assert r.status_code == 401
    assert "session" not in r.cookies
    assert [m["id"] for m in db.get_users_by_username_ci("admin")] == [admin_id]
    assert "auth.ldap_conflict" in [e["action"] for e in db.list_audit_log()]


def test_ldap_new_users_stored_lowercase(db):
    u = db.upsert_ldap_user("Dave", "Dave", None, "read_only")
    assert u["username"] == "dave"


def test_ldap_authenticate_returns_directory_name_lowercased():
    from harness.auth_manager import ldap_authenticate
    conn = MagicMock()
    conn.bind.return_value = True
    conn.entries = [MagicMock(**{
        "displayName.value": "Bob", "mail.value": None,
        "memberOf.values": [], "sAMAccountName.value": "Bob.Smith",
    })]
    with patch("harness.auth_manager.Connection", return_value=conn), \
         patch("harness.auth_manager.Server"):
        info = ldap_authenticate("BOB.SMITH", "pw", LDAP_CFG)
    assert info["username"] == "bob.smith"
    assert "sAMAccountName" in conn.search.call_args.kwargs["attributes"]


def test_ldap_username_attribute_follows_search_filter():
    from harness.auth_manager import _username_attribute
    assert _username_attribute(LDAP_CFG) == "sAMAccountName"
    assert _username_attribute(
        {"user_search_filter": "(&(objectClass=person)(uid={username}))"}) == "uid"
    assert _username_attribute(
        {"user_search_filter": "(uid={username})", "username_attribute": "cn"}) == "cn"


# ── #8 LDAP injection / unauthenticated bind ─────────────────────────────────

def _mock_ldap_conn():
    conn = MagicMock()
    conn.bind.return_value = True
    conn.search.return_value = True
    conn.entries = [MagicMock(**{
        "displayName.value": "X", "mail.value": None, "memberOf.values": [],
    })]
    return conn


def test_ldap_search_filter_is_escaped():
    from harness.auth_manager import ldap_authenticate
    conn = _mock_ldap_conn()
    with patch("harness.auth_manager.Connection", return_value=conn), \
         patch("harness.auth_manager.Server"):
        ldap_authenticate("*)(uid=*", "pw", LDAP_CFG)
    search_filter = conn.search.call_args.kwargs["search_filter"]
    assert search_filter == r"(sAMAccountName=\2a\29\28uid=\2a)"


@pytest.mark.parametrize("password", ["", "   ", "\t"])
def test_ldap_empty_password_never_binds(password):
    from harness.auth_manager import ldap_authenticate
    conn = _mock_ldap_conn()
    with patch("harness.auth_manager.Connection", return_value=conn) as ctor, \
         patch("harness.auth_manager.Server"):
        assert ldap_authenticate("bob", password, LDAP_CFG) is None
    ctor.assert_not_called()
    conn.bind.assert_not_called()


@pytest.mark.parametrize("username", [
    "bob@evil.com", "EVIL\\bob", "bob\x00", "bob\nx", "", "a" * 257,
    "bob ", " bob",
])
def test_ldap_rejects_unsafe_usernames(username):
    from harness.auth_manager import ldap_authenticate
    conn = _mock_ldap_conn()
    with patch("harness.auth_manager.Connection", return_value=conn) as ctor, \
         patch("harness.auth_manager.Server"):
        assert ldap_authenticate(username, "pw", LDAP_CFG) is None
    ctor.assert_not_called()


def test_ldap_bind_name_uses_domain_from_base_dn():
    from harness.auth_manager import ldap_authenticate
    conn = _mock_ldap_conn()
    with patch("harness.auth_manager.Connection", return_value=conn) as ctor, \
         patch("harness.auth_manager.Server"):
        assert ldap_authenticate("bob", "pw", LDAP_CFG) is not None
    assert ctor.call_args.kwargs["user"] == "bob@corp.com"


# ── #3 GitHub OAuth ──────────────────────────────────────────────────────────

def _resp(status, payload):
    r = MagicMock()
    r.status_code = status
    r.json = MagicMock(return_value=payload)
    return r


GH_USER = {"id": 777, "login": "octo", "name": "Octo Cat", "email": "o@x.io"}


def _github_callback(db, apps_dir, github_cfg, *, token=None, user=None,
                     memberships=None, state="st"):
    """Drive /auth/oauth/github/callback with mocked GitHub responses.

    ``token``/``user`` may be a response mock or an exception to raise;
    ``memberships`` maps org name -> membership response (default 404)."""
    from unittest.mock import AsyncMock
    config = {
        "environments": {"production": {"label": "Production"}},
        "auth": {"github": {"client_id": "cid", "client_secret": "cs",
                            "default_role": "read_only", **github_cfg}},
    }
    app = create_app(db=db, config=config, apps_dir=str(apps_dir))
    c = TestClient(app, follow_redirects=False)
    c.cookies.set("oauth_state", "st")

    token = token if token is not None else _resp(200, {"access_token": "gho_x"})
    user = user if user is not None else _resp(200, GH_USER)
    memberships = memberships or {}

    async def _post(url, **kw):
        if isinstance(token, Exception):
            raise token
        return token

    async def _get(url, **kw):
        prefix = "https://api.github.com/user/memberships/orgs/"
        if url.startswith(prefix):
            return memberships.get(url[len(prefix):], _resp(404, {}))
        if isinstance(user, Exception):
            raise user
        return user

    with patch("web.routes.auth.httpx") as mock_httpx:
        mc = AsyncMock()
        mc.__aenter__ = AsyncMock(return_value=mc)
        mc.__aexit__ = AsyncMock(return_value=False)
        mc.post = AsyncMock(side_effect=_post)
        mc.get = AsyncMock(side_effect=_get)
        mock_httpx.AsyncClient.return_value = mc
        r = c.get("/auth/oauth/github/callback",
                  params={"code": "abc", "state": state})
    return r, mc


def _github_row(db, login, provider_id, *, provider="github", active=1):
    uid = str(uuid.uuid4())
    db.insert_user({
        "id": uid, "username": f"github:{login}", "display_name": login,
        "email": None, "password_hash": None, "role": "admin",
        "auth_provider": provider, "oauth_provider_id": provider_id,
        "role_override": 0, "is_active": active,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_login_at": None,
    })
    return uid


def test_github_no_allowlist_new_user_is_403(db, apps_dir):
    r, _ = _github_callback(db, apps_dir, {})
    assert r.status_code == 403
    assert "session" not in r.cookies
    assert db.get_user_by_username("github:octo") is None


def test_github_allowlisted_user_is_created(db, apps_dir):
    r, _ = _github_callback(db, apps_dir, {"allowed_users": ["OCTO"]})
    assert r.status_code == 302
    u = db.get_user_by_oauth_provider_id("github", "777")
    assert u["username"] == "github:octo"
    assert u["role"] == "read_only"


def test_github_auto_provision_creates_user(db, apps_dir):
    r, _ = _github_callback(db, apps_dir, {"auto_provision": True})
    assert r.status_code == 302
    assert db.get_user_by_oauth_provider_id("github", "777") is not None


def test_github_org_membership_allows_login(db, apps_dir):
    r, mc = _github_callback(
        db, apps_dir, {"allowed_orgs": ["other", "ACME"]},
        memberships={"acme": _resp(200, {"state": "active"})},
    )
    assert r.status_code == 302
    urls = [call.args[0] for call in mc.get.call_args_list]
    assert "https://api.github.com/user/memberships/orgs/acme" in urls


@pytest.mark.parametrize("membership", [
    _resp(404, {"message": "Not Found"}),
    _resp(200, {"state": "pending"}),
    _resp(200, ["active"]),
])
def test_github_not_active_org_member_is_403(db, apps_dir, membership):
    r, _ = _github_callback(db, apps_dir, {"allowed_orgs": ["acme"]},
                            memberships={"acme": membership})
    assert r.status_code == 403


def test_github_org_lookup_network_error_is_502(db, apps_dir):
    import httpx
    from unittest.mock import AsyncMock
    with patch("web.routes.auth._github_user_in_orgs",
               AsyncMock(side_effect=httpx.ConnectTimeout("timed out"))):
        r, _ = _github_callback(db, apps_dir, {"allowed_orgs": ["acme"]})
    assert r.status_code == 502


@pytest.mark.parametrize("where", ["token", "user"])
def test_github_network_error_is_502(db, apps_dir, where):
    import httpx
    err = httpx.ReadTimeout("timed out")
    kw = {"token": err} if where == "token" else {"user": err}
    r, _ = _github_callback(db, apps_dir, {"auto_provision": True}, **kw)
    assert r.status_code == 502
    assert "session" not in r.cookies


def test_github_non_ascii_state_is_400_not_500(db, apps_dir):
    r, _ = _github_callback(db, apps_dir, {"auto_provision": True},
                            state="st\u00e9\u2603")
    assert r.status_code == 400


def test_github_new_user_stored_lowercase(db, apps_dir):
    r, _ = _github_callback(db, apps_dir, {"auto_provision": True},
                            user=_resp(200, {**GH_USER, "login": "OctoCat"}))
    assert r.status_code == 302
    u = db.get_user_by_oauth_provider_id("github", "777")
    assert u["username"] == "github:octocat"
    assert u["display_name"] == "Octo Cat"


def test_github_mixed_case_preprovisioned_row_gets_bound(db, apps_dir):
    uid = _github_row(db, "Octo", None)
    r, _ = _github_callback(db, apps_dir, {})
    assert r.status_code == 302
    assert db.get_user_by_id(uid)["oauth_provider_id"] == "777"


def test_github_case_variant_cannot_take_over_linked_row(db, apps_dir):
    victim = _github_row(db, "octo", "111")
    r, _ = _github_callback(db, apps_dir, {"auto_provision": True},
                            user=_resp(200, {**GH_USER, "login": "OCTO"}))
    assert r.status_code == 403
    assert db.get_user_by_id(victim)["oauth_provider_id"] == "111"


def test_github_allowlist_enforced_for_existing_linked_user(db, apps_dir):
    _github_row(db, "octo", "777")
    r, _ = _github_callback(db, apps_dir, {"allowed_users": ["someone-else"]})
    assert r.status_code == 403
    assert "session" not in r.cookies


def test_github_existing_linked_user_without_allowlist_still_works(db, apps_dir):
    _github_row(db, "octo", "777")
    r, _ = _github_callback(db, apps_dir, {})
    assert r.status_code == 302
    assert "session" in r.cookies


def test_github_renamed_login_cannot_take_over_linked_row(db, apps_dir):
    """A different GitHub account that now holds the login 'octo' must not
    inherit the row linked to the original 'octo' (id 111)."""
    victim = _github_row(db, "octo", "111")
    r, _ = _github_callback(db, apps_dir, {"auto_provision": True})
    assert r.status_code == 403
    assert "session" not in r.cookies
    assert db.get_user_by_id(victim)["oauth_provider_id"] == "111"


def test_github_login_cannot_claim_non_github_row(db, apps_dir):
    _github_row(db, "octo", None, provider="local")
    r, _ = _github_callback(db, apps_dir, {"auto_provision": True})
    assert r.status_code == 403


def test_github_preprovisioned_row_gets_bound(db, apps_dir):
    uid = _github_row(db, "octo", None)
    r, _ = _github_callback(db, apps_dir, {})
    assert r.status_code == 302
    assert db.get_user_by_id(uid)["oauth_provider_id"] == "777"
    assert db.get_user_by_oauth_provider_id("github", "777")["id"] == uid


def test_set_user_oauth_provider_id_never_overwrites(db):
    uid = _github_row(db, "octo", "111")
    assert db.set_user_oauth_provider_id(uid, "777") is False
    assert db.get_user_by_id(uid)["oauth_provider_id"] == "111"


@pytest.mark.parametrize("token", [
    _resp(500, {"access_token": "gho_x"}),
    _resp(200, {"error": "bad_verification_code"}),
    _resp(200, {"access_token": 123}),
    _resp(200, ["gho_x"]),
])
def test_github_bad_token_response_is_400(db, apps_dir, token):
    r, _ = _github_callback(db, apps_dir, {"auto_provision": True}, token=token)
    assert r.status_code == 400


@pytest.mark.parametrize("user", [
    _resp(401, GH_USER),
    _resp(200, {**GH_USER, "id": "777"}),
    _resp(200, {**GH_USER, "id": True}),
    _resp(200, {**GH_USER, "login": "octo/../admin"}),
    _resp(200, {**GH_USER, "login": "a" * 40}),
    _resp(200, {"id": 777}),
])
def test_github_malformed_user_response_is_400(db, apps_dir, user):
    r, _ = _github_callback(db, apps_dir, {"auto_provision": True}, user=user)
    assert r.status_code == 400


def test_github_session_cookie_has_max_age(db, apps_dir):
    r, _ = _github_callback(db, apps_dir, {"allowed_users": ["octo"]})
    assert r.status_code == 302
    set_cookie = [h for h in r.headers.get_list("set-cookie") if h.startswith("session=")]
    assert set_cookie
    assert "Max-Age=" in set_cookie[0]
    assert "samesite=lax" in set_cookie[0].lower()


def test_github_login_requests_read_org_only_with_orgs(db, apps_dir):
    def _loc(cfg):
        config = {"auth": {"github": {"client_id": "cid", **cfg}}}
        app = create_app(db=db, config=config, apps_dir=str(apps_dir))
        c = TestClient(app, follow_redirects=False)
        return c.get("/auth/oauth/github/login").headers["location"]
    assert "read%3Aorg" not in _loc({})
    assert "read%3Aorg" in _loc({"allowed_orgs": ["acme"]})


# ── #10 throttling and constant-time lookup ──────────────────────────────────

@pytest.fixture
def login_client(db, apps_dir):
    app = create_app(db=db, config={
        "environments": {"production": {"label": "Production"}},
        "auth": {"session_hours": 8, "secure_cookie": False},
    }, apps_dir=str(apps_dir))
    from web.csrf import verify_csrf
    app.dependency_overrides[verify_csrf] = lambda: None
    return TestClient(app, follow_redirects=False)


def test_21st_failed_login_from_one_ip_is_429(db, login_client, monkeypatch):
    # Skip real bcrypt work to keep the test fast.
    monkeypatch.setattr("bcrypt.checkpw", lambda pw, h: False)
    for i in range(20):
        r = login_client.post("/auth/login",
                              data={"username": f"nobody{i}", "password": "x"})
        assert r.status_code == 401
    r = login_client.post("/auth/login", data={"username": "nobody", "password": "x"})
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) >= 1
    assert "session" not in r.cookies


def test_throttled_ip_cannot_log_in_even_with_correct_password(db, login_client):
    from web.ratelimit import login_limiter
    _local_user(db, "alice", pw="right-password")
    for _ in range(20):
        login_limiter.hit("testclient")
    r = login_client.post("/auth/login",
                          data={"username": "alice", "password": "right-password"})
    assert r.status_code == 429


def test_successful_logins_are_not_counted(db, login_client):
    _local_user(db, "alice", pw="right-password")
    for _ in range(25):
        r = login_client.post("/auth/login",
                              data={"username": "alice", "password": "right-password"})
        assert r.status_code == 303


def test_sliding_window_expires_old_events():
    from web.ratelimit import SlidingWindowLimiter
    now = [1000.0]
    lim = SlidingWindowLimiter(3, 60, clock=lambda: now[0])
    for _ in range(3):
        lim.hit("ip")
    assert lim.is_limited("ip")
    assert not lim.is_limited("other-ip")
    assert 1 <= lim.retry_after("ip") <= 61
    now[0] += 61
    assert not lim.is_limited("ip")
    assert lim.retry_after("ip") == 0


def test_limiter_key_count_is_bounded():
    from web.ratelimit import SlidingWindowLimiter
    now = [0.0]
    lim = SlidingWindowLimiter(5, 60, clock=lambda: now[0], max_keys=100)
    for i in range(100):
        lim.hit(f"10.0.0.{i}")
    assert len(lim) == 100
    # All expired: the next new key triggers a sweep.
    now[0] += 61
    lim.hit("new-ip")
    assert len(lim) == 1
    # All live: the stalest key is evicted to make room.
    for i in range(150):
        now[0] += 0.01
        lim.hit(f"live-{i}")
    assert len(lim) == 100
    assert lim.is_limited("live-149") is False


@pytest.mark.parametrize("setup", ["missing", "ldap", "inactive", "no_hash"])
def test_unknown_or_ineligible_user_still_runs_bcrypt(db, monkeypatch, setup):
    import bcrypt as _bcrypt
    from harness.auth_manager import verify_local_password, _get_dummy_hash
    _get_dummy_hash()  # generate outside the counted window
    if setup == "ldap":
        db.upsert_ldap_user("bob", "Bob", None, "read_only")
    elif setup == "inactive":
        _local_user(db, "bob", is_active=0)
    elif setup == "no_hash":
        uid = _local_user(db, "bob")
        db.update_user(uid, password_hash=None)
    calls = []
    real = _bcrypt.checkpw
    monkeypatch.setattr("bcrypt.checkpw",
                        lambda pw, h: calls.append(h) or real(pw, h))
    assert verify_local_password("bob", "whatever", db) is None
    assert len(calls) == 1


# ── /setup race: first-run setup token ───────────────────────────────────────

SETUP_FORM = {"username": "root", "password": "Secret123!",
              "confirm": "Secret123!", "display_name": ""}


@pytest.fixture
def no_env_setup_token(monkeypatch):
    monkeypatch.delenv("HARNESS_SETUP_TOKEN", raising=False)


def test_setup_without_token_is_403(db, login_client, no_env_setup_token):
    r = login_client.post("/setup", data=SETUP_FORM)
    assert r.status_code == 403
    assert db.count_users() == 0


def test_setup_with_wrong_token_is_403(db, login_client, no_env_setup_token):
    r = login_client.post("/setup", data={**SETUP_FORM, "setup_token": "guess"})
    assert r.status_code == 403
    assert db.count_users() == 0


def test_setup_with_generated_token_is_303(db, login_client, no_env_setup_token):
    from web.routes.auth import get_setup_token
    r = login_client.post("/setup", data={**SETUP_FORM, "setup_token": get_setup_token()})
    assert r.status_code == 303
    assert db.get_user_by_username("root")["role"] == "admin"


def test_setup_token_from_env(db, login_client, monkeypatch):
    from web.routes.auth import get_setup_token
    monkeypatch.setenv("HARNESS_SETUP_TOKEN", "from-the-env-file")
    assert get_setup_token() == "from-the-env-file"
    r = login_client.post("/setup", data={**SETUP_FORM, "setup_token": "nope"})
    assert r.status_code == 403
    r = login_client.post("/setup", data={**SETUP_FORM, "setup_token": "from-the-env-file"})
    assert r.status_code == 303


def test_setup_token_is_logged_once_on_first_get(db, login_client, monkeypatch,
                                                 no_env_setup_token, caplog):
    import logging
    import web.routes.auth as auth_routes
    monkeypatch.setattr(auth_routes, "_setup_token_logged", False)
    with caplog.at_level(logging.WARNING, logger="web.routes.auth"):
        assert login_client.get("/setup").status_code == 200
        login_client.get("/setup")
    msgs = [r.getMessage() for r in caplog.records
            if "First-run setup token" in r.getMessage()]
    assert msgs == [f"First-run setup token: {auth_routes.get_setup_token()}"]


def test_setup_attempts_are_throttled(db, login_client, no_env_setup_token):
    for _ in range(10):
        r = login_client.post("/setup", data={**SETUP_FORM, "setup_token": "guess"})
        assert r.status_code == 403
    from web.routes.auth import get_setup_token
    r = login_client.post("/setup", data={**SETUP_FORM, "setup_token": get_setup_token()})
    assert r.status_code == 429
    assert db.count_users() == 0
