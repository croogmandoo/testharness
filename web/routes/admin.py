import os
import tempfile
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask
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


@router.get("/admin/ldap", response_class=HTMLResponse)
async def ldap_config(request: Request,
                      current_user: dict = Depends(require_role("admin"))):
    from web.main import get_config
    config = get_config()
    ldap_cfg = config.get("auth", {}).get("ldap", {})
    return templates.TemplateResponse(request, "admin_ldap.html",
                                      _ctx(request, current_user, ldap_cfg=ldap_cfg))


@router.get("/admin/backup")
async def db_backup(current_user: dict = Depends(require_role("admin"))):
    """Stream a consistent online snapshot of the live SQLite DB.

    Uses SQLite's BACKUP API so the harness can keep serving requests
    while the snapshot is taken.  The temp file is removed via FastAPI's
    BackgroundTask after the response body finishes streaming.
    """
    from web.main import get_db
    db = get_db()

    fd, tmp_path = tempfile.mkstemp(prefix="harness-backup-", suffix=".db")
    os.close(fd)
    db.backup_to_path(tmp_path)
    db.log_audit(
        "db.backup",
        user_id=current_user["id"],
        username=current_user.get("username"),
        target=os.path.basename(tmp_path),
    )

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return FileResponse(
        tmp_path,
        media_type="application/octet-stream",
        filename=f"harness-{ts}.db",
        background=BackgroundTask(_remove_quietly, tmp_path),
    )


def _remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


@router.get("/admin/audit", response_class=HTMLResponse)
async def audit_log(request: Request,
                    user: str = "",
                    action: str = "",
                    limit: int = 200,
                    current_user: dict = Depends(require_role("admin"))):
    from web.main import get_db
    db = get_db()
    user_id_filter = None
    if user:
        u = db.get_user_by_username(user)
        if u:
            user_id_filter = u["id"]
    entries = db.list_audit_log(
        limit=min(max(int(limit), 1), 1000),
        user_id=user_id_filter,
        action=action or None,
    )
    return templates.TemplateResponse(
        request, "admin_audit.html",
        _ctx(request, current_user,
             entries=entries,
             filter_user=user, filter_action=action, filter_limit=limit),
    )


@router.post("/admin/ldap/test")
async def ldap_test(request: Request,
                    current_user: dict = Depends(require_role("admin"))):
    from web.main import get_config
    from harness.auth_manager import ldap_authenticate
    body = await request.json()
    ldap_cfg = get_config().get("auth", {}).get("ldap", {})
    if not ldap_cfg.get("enabled"):
        return JSONResponse({"ok": False, "error": "LDAP is not enabled in config."})
    try:
        result = ldap_authenticate(body.get("username", ""), body.get("password", ""), ldap_cfg)
        if result:
            return JSONResponse({"ok": True, "role": result["role"],
                                 "display_name": result["display_name"]})
        return JSONResponse({"ok": False, "error": "Bind failed — invalid credentials."})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)})
