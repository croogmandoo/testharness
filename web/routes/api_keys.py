"""API key management routes."""
import hashlib
import json
import os
import secrets
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from web.auth import KNOWN_SCOPES, get_current_user
from web.csrf import csrf_token_for, verify_csrf
from web.templating import make_templates

router = APIRouter()
templates = make_templates()

_EXPIRY_MAP = {
    "7": 7,
    "30": 30,
    "90": 90,
    "365": 365,
    "never": None,
}


# Scope for keys created with a blank scope field. Read-only on purpose: the
# old wildcard default let any user mint a key that could act as a worker.
_DEFAULT_SCOPES = ["read"]

# Scopes only some roles may grant. Worker scopes (and the wildcard, which
# includes them) unlock /api/runner/*, whose job descriptors carry resolved
# secrets. Scopes not listed here can be granted by anyone.
_SCOPE_ROLES = {
    "*": ("admin", "runner"),
    "worker:pull": ("admin", "runner"),
    "worker:results": ("admin", "runner"),
    "admin": ("admin",),
}


def _parse_scope_input(raw: str) -> list[str]:
    """Normalise the comma/space-separated scope field from the form. Empty
    input → ``_DEFAULT_SCOPES`` (read-only). Unknown scopes raise 422 so
    typos surface immediately."""
    if not raw or not raw.strip():
        return list(_DEFAULT_SCOPES)
    tokens = [t.strip() for t in raw.replace("\n", ",").split(",")]
    scopes = [t for t in tokens if t]
    if not scopes:
        return list(_DEFAULT_SCOPES)
    unknown = [s for s in scopes if s not in KNOWN_SCOPES]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown scope(s): {', '.join(unknown)}. "
                   f"Valid: {', '.join(sorted(KNOWN_SCOPES))}",
        )
    return sorted(set(scopes))


def _check_scope_grants(scopes: list[str], role: str) -> None:
    """403 if ``role`` may not grant one of ``scopes`` (see ``_SCOPE_ROLES``)."""
    denied = [s for s in scopes if role not in _SCOPE_ROLES.get(s, (role,))]
    if denied:
        raise HTTPException(
            status_code=403,
            detail=f"Your role ({role}) cannot create keys with scope(s): "
                   f"{', '.join(denied)}",
        )


def _ctx(request: Request, current_user: dict, **extra) -> dict:
    from web.main import get_config
    config = get_config()
    return {
        "request": request,
        "environments": config.get("environments", {}),
        "environment": None,
        "current_user": current_user,
        "csrf_token": csrf_token_for(request),
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
    new_key = request.cookies.get("flash_new_key")
    user_keys = db.list_api_keys_for_user(current_user["id"])
    all_keys = db.list_all_api_keys() if current_user["role"] == "admin" else []
    response = templates.TemplateResponse(
        request, "api_keys.html",
        _ctx(request, current_user,
             user_keys=user_keys,
             all_keys=all_keys,
             new_key=new_key),
    )
    if new_key:
        response.delete_cookie("flash_new_key")
    return response


@router.post("/api-keys")
async def api_keys_create(
    request: Request,
    name: str = Form(...),
    expiry_days: str = Form("never"),
    scope: str = Form(""),
    current_user: dict = Depends(get_current_user),
    _csrf=Depends(verify_csrf),
):
    from web.main import get_db
    db = get_db()
    name = name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Name is required.")

    if expiry_days not in _EXPIRY_MAP:
        raise HTTPException(status_code=422, detail=f"Invalid expiry option.")

    scopes = _parse_scope_input(scope)
    _check_scope_grants(scopes, current_user["role"])

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
        "scope": json.dumps(scopes),
    })
    db.log_audit("api_key.create", user_id=current_user["id"],
                 username=current_user.get("username"),
                 target=name, details=f"prefix={prefix} scope={scopes}")
    response = RedirectResponse("/api-keys", status_code=303)
    response.set_cookie("flash_new_key", plaintext, httponly=True, max_age=60, samesite="lax")
    return response


@router.post("/api-keys/{key_id}/revoke")
async def api_keys_revoke(
    request: Request,
    key_id: str,
    current_user: dict = Depends(get_current_user),
    _csrf=Depends(verify_csrf),
):
    from web.main import get_db
    db = get_db()
    if current_user["role"] == "admin":
        db.revoke_api_key(key_id, user_id=None)
    else:
        db.revoke_api_key(key_id, user_id=current_user["id"])
    db.log_audit("api_key.revoke", user_id=current_user["id"],
                 username=current_user.get("username"), target=key_id)
    return RedirectResponse("/api-keys", status_code=303)
