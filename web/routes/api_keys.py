"""API key management routes."""
import hashlib
import os
import secrets
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from web.auth import get_current_user

router = APIRouter()
templates = Jinja2Templates(
    directory=os.path.join(os.path.dirname(__file__), "..", "templates")
)

_EXPIRY_MAP = {
    "7": 7,
    "30": 30,
    "90": 90,
    "365": 365,
    "never": None,
}


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


def _generate_key() -> tuple[str, str, str]:
    """Return (plaintext, prefix, sha256_hex)."""
    plaintext = "hth_" + secrets.token_urlsafe(30)
    prefix = plaintext[4:12]
    key_hash = hashlib.sha256(plaintext.encode()).hexdigest()
    return plaintext, prefix, key_hash


@router.get("/api-keys", response_class=HTMLResponse)
async def api_keys_list(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    from web.main import get_db
    db = get_db()
    new_key = request.query_params.get("new_key")
    user_keys = db.list_api_keys_for_user(current_user["id"])
    all_keys = db.list_all_api_keys() if current_user["role"] == "admin" else []
    return templates.TemplateResponse(
        request, "api_keys.html",
        _ctx(request, current_user,
             user_keys=user_keys,
             all_keys=all_keys,
             new_key=new_key),
    )


@router.post("/api-keys")
async def api_keys_create(
    request: Request,
    name: str = Form(...),
    expiry_days: str = Form("never"),
    current_user: dict = Depends(get_current_user),
):
    from web.main import get_db
    db = get_db()
    name = name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Name is required.")

    if expiry_days not in _EXPIRY_MAP:
        raise HTTPException(status_code=422, detail=f"Invalid expiry option.")

    days = _EXPIRY_MAP.get(expiry_days)
    expires_at: Optional[str] = None
    if days is not None:
        expires_at = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()

    plaintext, prefix, key_hash = _generate_key()
    db.insert_api_key({
        "id": str(uuid.uuid4()),
        "user_id": current_user["id"],
        "name": name,
        "key_prefix": prefix,
        "key_hash": key_hash,
        "expires_at": expires_at,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_used_at": None,
        "is_active": 1,
    })
    from urllib.parse import quote
    return RedirectResponse(f"/api-keys?new_key={quote(plaintext)}", status_code=303)


@router.post("/api-keys/{key_id}/revoke")
async def api_keys_revoke(
    request: Request,
    key_id: str,
    current_user: dict = Depends(get_current_user),
):
    from web.main import get_db
    db = get_db()
    if current_user["role"] == "admin":
        db.revoke_api_key(key_id, user_id=None)
    else:
        db.revoke_api_key(key_id, user_id=current_user["id"])
    return RedirectResponse("/api-keys", status_code=303)
