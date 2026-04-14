import uuid
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from web.auth import require_role

router = APIRouter()
templates = Jinja2Templates(
    directory=os.path.join(os.path.dirname(__file__), "..", "templates")
)


def _ctx(request: Request, current_user: dict, **extra) -> dict:
    from web.main import get_config
    config = get_config()
    return {
        "request": request,
        "environments": config.get("environments", {}),
        "environment": None,
        "current_user": current_user,
        **extra,
    }


@router.get("/users", response_class=HTMLResponse)
async def users_list(request: Request,
                     current_user: dict = Depends(require_role("admin"))):
    from web.main import get_db
    users = get_db().list_users()
    return templates.TemplateResponse(request, "users.html",
                                      _ctx(request, current_user, users=users))


@router.get("/users/new", response_class=HTMLResponse)
async def users_new(request: Request,
                    current_user: dict = Depends(require_role("admin"))):
    return templates.TemplateResponse(request, "user_form.html",
                                      _ctx(request, current_user, mode="create",
                                           user={}, error=None))


@router.post("/users/new")
async def users_create(
    request: Request,
    username: str = Form(...),
    display_name: str = Form(""),
    email: str = Form(""),
    role: str = Form("read_only"),
    password: str = Form(""),
    current_user: dict = Depends(require_role("admin")),
):
    from web.main import get_db
    import bcrypt as _bcrypt
    db = get_db()
    ctx = _ctx(request, current_user, mode="create",
               user={"username": username, "display_name": display_name,
                     "email": email, "role": role}, error=None)
    if not username.strip():
        ctx["error"] = "Username is required."
        return templates.TemplateResponse(request, "user_form.html", ctx, status_code=422)
    if db.get_user_by_username(username.strip()):
        ctx["error"] = "Username already exists."
        return templates.TemplateResponse(request, "user_form.html", ctx, status_code=409)
    if not password:
        ctx["error"] = "Password is required for local accounts."
        return templates.TemplateResponse(request, "user_form.html", ctx, status_code=422)
    pw_hash = _bcrypt.hashpw(password.encode(), _bcrypt.gensalt()).decode()
    db.insert_user({
        "id": str(uuid.uuid4()),
        "username": username.strip(),
        "display_name": display_name.strip() or username.strip(),
        "email": email.strip() or None,
        "password_hash": pw_hash,
        "role": role,
        "auth_provider": "local",
        "role_override": 0,
        "is_active": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_login_at": None,
    })
    return RedirectResponse("/users", status_code=303)


@router.get("/users/{user_id}/edit", response_class=HTMLResponse)
async def users_edit(request: Request, user_id: str,
                     current_user: dict = Depends(require_role("admin"))):
    from web.main import get_db
    from fastapi import HTTPException
    user = get_db().get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404)
    user.pop("password_hash", None)
    return templates.TemplateResponse(request, "user_form.html",
                                      _ctx(request, current_user, mode="edit",
                                           user=user, error=None))


@router.post("/users/{user_id}/edit")
async def users_update(
    request: Request,
    user_id: str,
    display_name: str = Form(""),
    email: str = Form(""),
    role: str = Form("read_only"),
    role_override: int = Form(0),
    is_active: int = Form(1),
    password: str = Form(""),
    current_user: dict = Depends(require_role("admin")),
):
    from web.main import get_db
    import bcrypt as _bcrypt
    from fastapi import HTTPException
    db = get_db()
    if not db.get_user_by_id(user_id):
        raise HTTPException(status_code=404)
    updates = dict(display_name=display_name.strip(),
                   email=email.strip() or None,
                   role=role, role_override=role_override, is_active=is_active)
    if password:
        updates["password_hash"] = _bcrypt.hashpw(password.encode(), _bcrypt.gensalt()).decode()
    db.update_user(user_id, **updates)
    return RedirectResponse("/users", status_code=303)
