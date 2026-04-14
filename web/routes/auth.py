import uuid
import os
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
    return templates.TemplateResponse(request, "login.html", _nav_ctx(request))


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
