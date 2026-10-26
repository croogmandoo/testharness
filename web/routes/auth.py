import logging
import re
import uuid
import os
import secrets as _secrets
import httpx
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from web.csrf import csrf_token_for, verify_csrf
from web.templating import make_templates

router = APIRouter()
templates = make_templates()
_log = logging.getLogger("web.routes.auth")


def _nav_ctx(request: Request) -> dict:
    from web.main import get_config
    config = get_config()
    return {
        "request": request,
        "environments": config.get("environments", {}),
        "environment": None,
        "current_user": None,
        "csrf_token": csrf_token_for(request),
    }


# ── First-run setup ───────────────────────────────────────────────────────────

_generated_setup_token: "str | None" = None
_setup_token_logged = False


def get_setup_token() -> str:
    """The token ``POST /setup`` requires.

    ``HARNESS_SETUP_TOKEN`` wins when set. Otherwise a random per-process
    token is generated on first use; ``_announce_setup_token`` logs it so
    whoever has access to the server logs can finish first-run setup, and
    nobody else racing for the open ``/setup`` page can."""
    global _generated_setup_token
    env_token = os.environ.get("HARNESS_SETUP_TOKEN", "").strip()
    if env_token:
        return env_token
    if _generated_setup_token is None:
        _generated_setup_token = _secrets.token_urlsafe(24)
    return _generated_setup_token


def _announce_setup_token() -> None:
    global _setup_token_logged
    if _setup_token_logged or os.environ.get("HARNESS_SETUP_TOKEN", "").strip():
        return
    _setup_token_logged = True
    _log.warning("First-run setup token: %s", get_setup_token())


@router.get("/setup", response_class=HTMLResponse)
async def setup_get(request: Request):
    from web.main import get_db
    if get_db().count_users() > 0:
        from fastapi import HTTPException
        raise HTTPException(status_code=404)
    _announce_setup_token()
    return templates.TemplateResponse(request, "setup.html", _nav_ctx(request))


@router.post("/setup")
async def setup_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    confirm: str = Form(...),
    display_name: str = Form(""),
    setup_token: str = Form(""),
    _csrf=Depends(verify_csrf),
):
    from web.main import get_db, get_config
    import bcrypt as _bcrypt
    from web.auth import _make_token
    from web.ratelimit import setup_limiter, client_ip
    db = get_db()
    if db.count_users() > 0:
        from fastapi import HTTPException
        raise HTTPException(status_code=404)

    ctx = {**_nav_ctx(request), "error": None}

    ip = client_ip(request)
    if setup_limiter.is_limited(ip):
        ctx["error"] = "Too many setup attempts. Try again later."
        return templates.TemplateResponse(
            request, "setup.html", ctx, status_code=429,
            headers={"Retry-After": str(setup_limiter.retry_after(ip))},
        )
    setup_limiter.hit(ip)

    _announce_setup_token()
    if not _secrets.compare_digest(
        setup_token.strip().encode("utf-8"), get_setup_token().encode("utf-8")
    ):
        ctx["error"] = "Invalid setup token."
        return templates.TemplateResponse(request, "setup.html", ctx, status_code=403)

    if password != confirm:
        ctx["error"] = "Passwords do not match."
        return templates.TemplateResponse(request, "setup.html", ctx, status_code=422)

    if len(password) < 8:
        ctx["error"] = "Password must be at least 8 characters."
        return templates.TemplateResponse(request, "setup.html", ctx, status_code=422)

    config = get_config()
    user_id = str(uuid.uuid4())
    password_hash = _bcrypt.hashpw(password.encode(), _bcrypt.gensalt()).decode()
    db.insert_user({
        "id": user_id,
        "username": username.strip(),
        "display_name": display_name.strip() or username.strip(),
        "email": None,
        "password_hash": password_hash,
        "role": "admin",
        "auth_provider": "local",
        "role_override": 0,
        "is_active": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_login_at": None,
    })
    db.update_user_last_login(user_id, datetime.now(timezone.utc).isoformat())

    token = _make_token(user_id)
    session_hours = config.get("auth", {}).get("session_hours", 8)
    secure = config.get("auth", {}).get("secure_cookie", False)

    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        "session", token,
        httponly=True, samesite="lax", secure=secure,
        max_age=session_hours * 3600,
    )
    return response


# ── Login / logout ────────────────────────────────────────────────────────────

@router.get("/auth/login", response_class=HTMLResponse)
async def login_get(request: Request):
    from web.main import get_config
    ctx = {
        **_nav_ctx(request),
        "github_oauth_enabled": bool(
            get_config().get("auth", {}).get("github", {}).get("client_id")
        ),
    }
    return templates.TemplateResponse(request, "login.html", ctx)


@router.post("/auth/login")
async def login_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    _csrf=Depends(verify_csrf),
):
    from web.main import get_db, get_config
    from harness.auth_manager import verify_local_password, ldap_authenticate
    from web.auth import _make_token
    from web.ratelimit import login_limiter, client_ip

    db = get_db()
    config = get_config()

    ip = client_ip(request)
    if login_limiter.is_limited(ip):
        ctx = {**_nav_ctx(request),
               "error": "Too many failed sign-in attempts. Try again later."}
        return templates.TemplateResponse(
            request, "login.html", ctx, status_code=429,
            headers={"Retry-After": str(login_limiter.retry_after(ip))},
        )

    user = verify_local_password(username, password, db)

    # Handle failed attempt tracking
    _raw_user = db.get_user_by_username(username)
    if user is None and _raw_user is not None and _raw_user.get("auth_provider") == "local":
        new_count = (_raw_user.get("failed_login_count") or 0) + 1
        lock_until = None
        if new_count >= 10:
            lock_until = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
        db.increment_failed_login(_raw_user["id"], lock_until)
    elif user is not None:
        db.reset_failed_login(user["id"])

    if user is None:
        ldap_cfg = config.get("auth", {}).get("ldap", {})
        if ldap_cfg.get("enabled"):
            ldap_info = ldap_authenticate(username, password, ldap_cfg)
            if ldap_info:
                user = db.upsert_ldap_user(
                    ldap_info["username"],
                    ldap_info["display_name"],
                    ldap_info["email"],
                    ldap_info["role"],
                )
                if user is None:
                    # Username is taken by a local/GitHub account; never
                    # bind the directory identity to it.
                    db.log_audit("auth.ldap_conflict",
                                 username=ldap_info["username"],
                                 target=ldap_info["username"],
                                 details="username belongs to a non-LDAP account")
                elif not user.get("is_active"):
                    user = None

    if user is None:
        login_limiter.hit(ip)
        ctx = {**_nav_ctx(request), "error": "Invalid username or password."}
        return templates.TemplateResponse(request, "login.html", ctx, status_code=401)

    db.update_user_last_login(user["id"], datetime.now(timezone.utc).isoformat())

    token = _make_token(user["id"])
    session_hours = config.get("auth", {}).get("session_hours", 8)
    secure = config.get("auth", {}).get("secure_cookie", False)

    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        "session", token,
        httponly=True, samesite="lax", secure=secure,
        max_age=session_hours * 3600,
    )
    return response


@router.post("/auth/logout")
async def logout(request: Request, _csrf=Depends(verify_csrf)):
    response = RedirectResponse("/auth/login", status_code=303)
    response.delete_cookie("session")
    return response


# ── GitHub OAuth ──────────────────────────────────────────────────────────────

_GITHUB_LOGIN_RE = re.compile(r"^[A-Za-z0-9-]{1,39}$")
_auto_provision_warned = False


def _github_allowlist(github_cfg: dict) -> tuple:
    """Return (allowed_users, allowed_orgs) as lower-cased sets."""
    users = {str(u).lower() for u in (github_cfg.get("allowed_users") or [])}
    orgs = {str(o).lower() for o in (github_cfg.get("allowed_orgs") or [])}
    return users, orgs


async def _github_user_in_orgs(access_token: str, allowed_orgs: set) -> bool:
    """True if the token's user belongs to any of ``allowed_orgs``.
    Any GitHub error or malformed response counts as "not a member"."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://api.github.com/user/orgs?per_page=100",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github.v3+json",
                },
            )
        if resp.status_code != 200:
            return False
        orgs = resp.json()
    except Exception:
        return False
    if not isinstance(orgs, list):
        return False
    for org in orgs:
        if isinstance(org, dict) and str(org.get("login", "")).lower() in allowed_orgs:
            return True
    return False


@router.get("/auth/oauth/github/login")
async def github_oauth_login(request: Request):
    from web.main import get_config
    config = get_config()
    github_cfg = config.get("auth", {}).get("github", {})
    client_id = github_cfg.get("client_id")
    if not client_id:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="GitHub OAuth not configured")
    state = _secrets.token_urlsafe(16)
    scope = "user:email"
    if github_cfg.get("allowed_orgs"):
        scope += " read:org"
    url = "https://github.com/login/oauth/authorize?" + urlencode(
        {"client_id": client_id, "state": state, "scope": scope}
    )
    response = RedirectResponse(url, status_code=302)
    response.set_cookie("oauth_state", state, max_age=300, httponly=True, samesite="lax")
    return response


@router.get("/auth/oauth/github/callback")
async def github_oauth_callback(request: Request, code: str = "", state: str = ""):
    from fastapi import HTTPException
    from web.main import get_config, get_db
    from web.auth import make_session_token, get_auth_config

    stored_state = request.cookies.get("oauth_state", "")
    if not stored_state or not _secrets.compare_digest(stored_state, state):
        raise HTTPException(status_code=400, detail="Invalid OAuth state parameter")

    config = get_config()
    github_cfg = config.get("auth", {}).get("github", {})
    client_id = github_cfg.get("client_id")
    client_secret = github_cfg.get("client_secret")
    default_role = github_cfg.get("default_role", "read_only")
    allowed_users, allowed_orgs = _github_allowlist(github_cfg)
    allowlist_configured = bool(allowed_users or allowed_orgs)
    auto_provision = bool(github_cfg.get("auto_provision", False))

    global _auto_provision_warned
    if auto_provision and not allowlist_configured and not _auto_provision_warned:
        _auto_provision_warned = True
        _log.warning(
            "auth.github.auto_provision is enabled with no allowed_users/allowed_orgs: "
            "any GitHub account can sign in and is created with role %r",
            default_role,
        )

    # ── Exchange the code for an access token ────────────────────────────────
    async with httpx.AsyncClient(timeout=15) as client:
        token_resp = await client.post(
            "https://github.com/login/oauth/access_token",
            json={"client_id": client_id, "client_secret": client_secret, "code": code},
            headers={"Accept": "application/json"},
        )
    try:
        token_data = token_resp.json() if token_resp.status_code == 200 else None
    except ValueError:
        token_data = None
    access_token = token_data.get("access_token") if isinstance(token_data, dict) else None
    if not isinstance(access_token, str) or not access_token:
        raise HTTPException(status_code=400, detail="GitHub OAuth failed — no access token")

    # ── Fetch and validate the GitHub identity ───────────────────────────────
    async with httpx.AsyncClient(timeout=15) as client:
        user_resp = await client.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github.v3+json",
            },
        )
    try:
        github_user = user_resp.json() if user_resp.status_code == 200 else None
    except ValueError:
        github_user = None
    if (
        not isinstance(github_user, dict)
        or not isinstance(github_user.get("id"), int)
        or isinstance(github_user.get("id"), bool)
        or not isinstance(github_user.get("login"), str)
        or not _GITHUB_LOGIN_RE.match(github_user["login"])
    ):
        raise HTTPException(status_code=400, detail="GitHub OAuth failed — invalid user response")

    login = github_user["login"]
    github_id = str(github_user["id"])
    username = f"github:{login}"
    name = github_user.get("name")
    display_name = name if isinstance(name, str) and name else login
    email = github_user.get("email") if isinstance(github_user.get("email"), str) else None

    # ── Allowlist: enforced on every login once configured ───────────────────
    allowed = False
    if allowlist_configured:
        allowed = login.lower() in allowed_users
        if not allowed and allowed_orgs:
            allowed = await _github_user_in_orgs(access_token, allowed_orgs)
        if not allowed:
            _log.warning("GitHub login %r refused: not in allowed_users/allowed_orgs", login)
            raise HTTPException(status_code=403, detail="GitHub account is not allowed")

    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    existing = db.get_user_by_oauth_provider_id("github", github_id)
    if existing:
        if not existing.get("is_active"):
            raise HTTPException(status_code=403, detail="Account is disabled")
        user = existing
        db.update_user_last_login(user["id"], now)
    else:
        existing_by_name = db.get_user_by_username(username)
        if existing_by_name:
            # Only a pre-provisioned GitHub row that has never been linked may
            # be bound to this GitHub id. A row already linked to a different
            # id means the login was renamed/recycled on GitHub — refuse it.
            if (existing_by_name.get("auth_provider") != "github"
                    or existing_by_name.get("oauth_provider_id")):
                _log.warning(
                    "GitHub login %r (id %s) refused: username belongs to another identity",
                    login, github_id,
                )
                raise HTTPException(status_code=403, detail="Account is linked to a different identity")
            if not existing_by_name.get("is_active"):
                raise HTTPException(status_code=403, detail="Account is disabled")
            if not db.set_user_oauth_provider_id(existing_by_name["id"], github_id):
                raise HTTPException(status_code=403, detail="Account is linked to a different identity")
            user = existing_by_name
            db.update_user_last_login(user["id"], now)
        else:
            if not (allowed or auto_provision):
                _log.warning(
                    "GitHub login %r refused: no matching user and auto_provision is off",
                    login,
                )
                raise HTTPException(status_code=403, detail="GitHub account is not allowed")
            user = {
                "id": str(uuid.uuid4()),
                "username": username,
                "display_name": display_name,
                "email": email,
                "password_hash": None,
                "role": default_role,
                "auth_provider": "github",
                "oauth_provider_id": github_id,
                "role_override": 0,
                "is_active": 1,
                "created_at": now,
                "last_login_at": now,
            }
            db.insert_user(user)

    auth_config = get_auth_config()
    token = make_session_token(
        user["id"], auth_config["signing_key"], auth_config["session_hours"]
    )
    response = RedirectResponse("/", status_code=302)
    response.set_cookie(
        "session", token,
        httponly=True, samesite="lax",
        secure=auth_config.get("secure_cookie", False),
        max_age=int(auth_config["session_hours"] * 3600),
    )
    response.delete_cookie("oauth_state")
    return response
