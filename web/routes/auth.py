import uuid
import os
import secrets as _secrets
import httpx
from datetime import datetime, timezone

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
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
        "current_user": None,
    }


# ── First-run setup ───────────────────────────────────────────────────────────

@router.get("/setup", response_class=HTMLResponse)
async def setup_get(request: Request):
    from web.main import get_db
    if get_db().count_users() > 0:
        from fastapi import HTTPException
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(request, "setup.html", _nav_ctx(request))


@router.post("/setup")
async def setup_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    confirm: str = Form(...),
    display_name: str = Form(""),
):
    from web.main import get_db, get_config
    import bcrypt as _bcrypt
    from web.auth import _make_token
    db = get_db()
    if db.count_users() > 0:
        from fastapi import HTTPException
        raise HTTPException(status_code=404)

    ctx = {**_nav_ctx(request), "error": None}

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
):
    from web.main import get_db, get_config
    from harness.auth_manager import verify_local_password, ldap_authenticate
    from web.auth import _make_token

    db = get_db()
    config = get_config()

    user = verify_local_password(username, password, db)

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
async def logout(request: Request):
    response = RedirectResponse("/auth/login", status_code=303)
    response.delete_cookie("session")
    return response


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
    url = (
        f"https://github.com/login/oauth/authorize"
        f"?client_id={client_id}&state={state}&scope=user:email"
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
    if not stored_state or stored_state != state:
        raise HTTPException(status_code=400, detail="Invalid OAuth state parameter")

    config = get_config()
    github_cfg = config.get("auth", {}).get("github", {})
    client_id = github_cfg.get("client_id")
    client_secret = github_cfg.get("client_secret")
    default_role = github_cfg.get("default_role", "read_only")

    async with httpx.AsyncClient(timeout=15) as client:
        token_resp = await client.post(
            "https://github.com/login/oauth/access_token",
            json={"client_id": client_id, "client_secret": client_secret, "code": code},
            headers={"Accept": "application/json"},
        )
    token_data = token_resp.json()
    access_token = token_data.get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail="GitHub OAuth failed — no access token")

    async with httpx.AsyncClient(timeout=15) as client:
        user_resp = await client.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github.v3+json",
            },
        )
    github_user = user_resp.json()
    github_id = str(github_user["id"])
    username = f"github:{github_user['login']}"
    display_name = github_user.get("name") or github_user["login"]
    email = github_user.get("email")

    db = get_db()
    existing = db.get_user_by_oauth_provider_id("github", github_id)
    if existing:
        user = existing
        db.update_user_last_login(user["id"], datetime.now(timezone.utc).isoformat())
    else:
        existing_by_name = db.get_user_by_username(username)
        if existing_by_name:
            user = existing_by_name
            db.update_user_last_login(user["id"], datetime.now(timezone.utc).isoformat())
        else:
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
                "created_at": datetime.now(timezone.utc).isoformat(),
                "last_login_at": datetime.now(timezone.utc).isoformat(),
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
    )
    response.delete_cookie("oauth_state")
    return response
