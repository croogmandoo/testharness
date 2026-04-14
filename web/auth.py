from typing import Optional
from fastapi import Depends, HTTPException, Request
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

_signing_key: bytes = b""
_session_hours: int = 8
_secure_cookie: bool = False


def set_auth_config(signing_key: bytes, session_hours: int, secure_cookie: bool) -> None:
    global _signing_key, _session_hours, _secure_cookie
    _signing_key = signing_key
    _session_hours = session_hours
    _secure_cookie = secure_cookie


def make_session_token(user_id: str, signing_key: bytes, session_hours: int) -> str:
    return URLSafeTimedSerializer(signing_key).dumps(user_id)


def load_session_token(token: str, signing_key: bytes, session_hours: int) -> Optional[str]:
    if not token:
        return None
    try:
        return URLSafeTimedSerializer(signing_key).loads(token, max_age=session_hours * 3600)
    except (BadSignature, SignatureExpired, Exception):
        return None


def _make_token(user_id: str) -> str:
    return make_session_token(user_id, _signing_key, _session_hours)


def _load_token(token: str) -> Optional[str]:
    return load_session_token(token, _signing_key, _session_hours)


async def get_current_user(request: Request) -> dict:
    from web.main import get_db
    db = get_db()
    is_api = request.url.path.startswith("/api")

    def _not_authed():
        if is_api:
            raise HTTPException(status_code=401, detail="Not authenticated")
        raise HTTPException(status_code=307, headers={"Location": "/auth/login"})

    token = request.cookies.get("session", "")
    user_id = _load_token(token)
    if not user_id:
        _not_authed()
    user = db.get_user_by_id(user_id)
    if not user or not user.get("is_active"):
        _not_authed()
    return user


def require_role(*roles: str):
    async def _dep(request: Request, user: dict = Depends(get_current_user)):
        if user["role"] not in roles:
            if request.url.path.startswith("/api"):
                raise HTTPException(status_code=403, detail="Insufficient permissions")
            raise HTTPException(status_code=403)
        return user
    return _dep
