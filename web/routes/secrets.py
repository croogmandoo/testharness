"""Secrets management routes — stub router for Phase 2 wiring."""
import os
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(
    directory=os.path.join(os.path.dirname(__file__), "..", "templates")
)


@router.get("/secrets", response_class=HTMLResponse)
async def secrets_list(request: Request):
    from web.main import get_db, get_secrets_store, get_config
    store = get_secrets_store()
    config = get_config()
    secrets = store.list() if store else []
    return templates.TemplateResponse(request, "secrets.html", {
        "request": request,
        "secrets": secrets,
        "environments": config.get("environments", {}),
        "environment": None,
        "current_user": None,
    })
