import os
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
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


@router.get("/admin/ldap", response_class=HTMLResponse)
async def ldap_config(request: Request,
                      current_user: dict = Depends(require_role("admin"))):
    from web.main import get_config
    config = get_config()
    ldap_cfg = config.get("auth", {}).get("ldap", {})
    return templates.TemplateResponse(request, "admin_ldap.html",
                                      _ctx(request, current_user, ldap_cfg=ldap_cfg))


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
