import uuid
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from web.auth import VALID_ROLES, issue_session_cookie, min_password_length, require_role
from web.csrf import csrf_token_for, verify_csrf
from web.templating import make_templates

router = APIRouter()
templates = make_templates()


def _ctx(request: Request, current_user: dict, **extra) -> dict:
    from web.main import get_config
    config = get_config()
    return {
        "request": request,
        "environments": config.get("environments", {}),
        "environment": None,
        "current_user": current_user,
        "csrf_token": csrf_token_for(request),
        "roles": VALID_ROLES,
        "min_password_length": min_password_length(),
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
    _csrf=Depends(verify_csrf),
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
    if role not in VALID_ROLES:
        ctx["error"] = "Invalid role."
        return templates.TemplateResponse(request, "user_form.html", ctx, status_code=422)
    if not password:
        ctx["error"] = "Password is required for local accounts."
        return templates.TemplateResponse(request, "user_form.html", ctx, status_code=422)
    min_len = min_password_length()
    if len(password) < min_len:
        ctx["error"] = f"Password must be at least {min_len} characters."
        return templates.TemplateResponse(request, "user_form.html", ctx, status_code=422)
    pw_hash = _bcrypt.hashpw(password.encode(), _bcrypt.gensalt()).decode()
    new_id = str(uuid.uuid4())
    db.insert_user({
        "id": new_id,
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
    db.log_audit("user.create", user_id=current_user["id"],
                 username=current_user.get("username"),
                 target=username.strip(), details=f"role={role}")
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


@router.post("/users/{user_id}/delete")
async def users_delete(request: Request, user_id: str,
                       current_user: dict = Depends(require_role("admin")),
                       _csrf=Depends(verify_csrf)):
    from web.main import get_db
    from fastapi import HTTPException
    if user_id == current_user["id"]:
        raise HTTPException(status_code=400, detail="Cannot delete your own account.")
    db = get_db()
    target_user = db.get_user_by_id(user_id)
    if not target_user:
        raise HTTPException(status_code=404)
    db.delete_user(user_id)
    db.log_audit("user.delete", user_id=current_user["id"],
                 username=current_user.get("username"),
                 target=target_user["username"])
    return RedirectResponse("/users", status_code=303)


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
    _csrf=Depends(verify_csrf),
):
    from web.main import get_db
    import bcrypt as _bcrypt
    from fastapi import HTTPException
    db = get_db()
    target_user = db.get_user_by_id(user_id)
    if not target_user:
        raise HTTPException(status_code=404)
    error = None
    if role not in VALID_ROLES:
        error = "Invalid role."
    elif password and len(password) < min_password_length():
        error = f"Password must be at least {min_password_length()} characters."
    if error:
        form_user = {k: v for k, v in target_user.items() if k != "password_hash"}
        form_user.update(display_name=display_name, email=email,
                         role_override=role_override, is_active=is_active)
        return templates.TemplateResponse(
            request, "user_form.html",
            _ctx(request, current_user, mode="edit", user=form_user, error=error),
            status_code=422,
        )
    updates = dict(display_name=display_name.strip(),
                   email=email.strip() or None,
                   role=role, role_override=role_override, is_active=is_active)
    if password:
        updates["password_hash"] = _bcrypt.hashpw(password.encode(), _bcrypt.gensalt()).decode()
    db.update_user(user_id, **updates)
    # Credential, privilege or status changes revoke the user's existing
    # sessions so an old cookie can't outlive them.
    revoke = (
        bool(password)
        or target_user.get("role") != role
        or (int(target_user.get("is_active") or 0) and not int(is_active))
    )
    if revoke:
        db.bump_session_version(user_id)
    changes = []
    if target_user.get("role") != role:
        changes.append(f"role: {target_user.get('role')} -> {role}")
    if password:
        changes.append("password reset")
    if int(target_user.get("is_active") or 0) != int(is_active):
        changes.append(f"is_active: {target_user.get('is_active')} -> {is_active}")
    db.log_audit("user.update", user_id=current_user["id"],
                 username=current_user.get("username"),
                 target=target_user["username"],
                 details="; ".join(changes) if changes else "no role/auth changes")
    response = RedirectResponse("/users", status_code=303)
    if (revoke and user_id == current_user["id"]
            and getattr(request.state, "api_key", None) is None
            and request.cookies.get("session")):
        # An admin editing their own account keeps this browser signed in
        # with a fresh cookie; their other sessions are still revoked.
        refreshed = db.get_user_by_id(user_id)
        if refreshed and refreshed.get("is_active"):
            issue_session_cookie(response, refreshed)
    return response
