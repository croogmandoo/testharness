import os
import yaml
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse
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
    }


@router.get("/apps", response_class=HTMLResponse)
async def apps_list(request: Request):
    import harness.app_manager as mgr
    from web.main import get_apps_dir
    apps_dir = get_apps_dir()
    active = mgr.list_apps(apps_dir=apps_dir)
    archived = mgr.list_archived(apps_dir=apps_dir)
    return templates.TemplateResponse("apps.html", {
        **_nav_ctx(request),
        "active_apps": active,
        "archived_apps": archived,
    })


@router.get("/apps/new", response_class=HTMLResponse)
async def apps_new(request: Request):
    return templates.TemplateResponse("app_form.html", {
        **_nav_ctx(request),
        "mode": "create",
        "app_name": "",
        "app_def": {},
        "raw_yaml": "",
    })


@router.get("/apps/{app_name}/edit", response_class=HTMLResponse)
async def apps_edit(request: Request, app_name: str):
    import harness.app_manager as mgr
    from web.main import get_apps_dir
    apps_dir = get_apps_dir()
    try:
        raw_yaml = mgr.read_app_raw(app_name, apps_dir=apps_dir)
    except mgr.AppManagerError:
        raise HTTPException(status_code=404, detail=f"App '{app_name}' not found")
    app_def = yaml.safe_load(raw_yaml) or {}
    return templates.TemplateResponse("app_form.html", {
        **_nav_ctx(request),
        "mode": "edit",
        "app_name": app_name,
        "app_def": app_def,
        "raw_yaml": raw_yaml,
    })


@router.get("/secrets", response_class=HTMLResponse)
async def secrets_page(request: Request):
    from harness.app_manager import get_known_vars
    from web.main import get_apps_dir
    apps_dir = get_apps_dir()
    var_names = get_known_vars(apps_dir=apps_dir)
    # Check presence only — never read values
    vars_with_status = [(v, os.environ.get(v[1:]) is not None) for v in var_names]
    return templates.TemplateResponse(request, "secrets.html", {
        "vars": vars_with_status,
        **_nav_ctx(request),
    })
