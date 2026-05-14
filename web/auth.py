import json
import logging
from typing import Optional

from fastapi import Depends, HTTPException, Request
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

_log = logging.getLogger("harness.auth")

_signing_key: bytes = b""
_session_hours: int = 8
_secure_cookie: bool = False

# Known scopes. The wildcard "*" grants all scopes (back-compat for keys
# created before feature #5 shipped). Endpoints declare their requirement
# via ``Depends(require_scope("worker:pull"))``.
KNOWN_SCOPES = frozenset({
    "*",
    "worker:pull",      # GET /api/runner/jobs (long-poll) + POST /api/runner/poll
    "worker:results",   # POST /api/runner/results, /heartbeat, /register
    "admin",            # admin-only operations (e.g. revoking other users' keys)
    "read",             # read-only API access (GET /api/...)
})


def set_auth_config(signing_key: bytes, session_hours: int, secure_cookie: bool) -> None:
    global _signing_key, _session_hours, _secure_cookie
    _signing_key = signing_key
    _session_hours = session_hours
    _secure_cookie = secure_cookie


def get_auth_config() -> dict:
    return {
        "signing_key": _signing_key,
        "session_hours": _session_hours,
        "secure_cookie": _secure_cookie,
    }


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
    import hashlib
    from datetime import datetime, timezone
    from web.main import get_db
    db = get_db()
    is_api = request.url.path.startswith("/api")

    def _not_authed():
        if is_api:
            raise HTTPException(status_code=401, detail="Not authenticated")
        raise HTTPException(status_code=307, headers={"Location": "/auth/login"})

    # --- API key check (runs before cookie) ---
    api_key_value: Optional[str] = None
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer hth_"):
        api_key_value = auth_header[len("Bearer "):]
    if api_key_value is None:
        api_key_value = request.headers.get("X-API-Key") or None
        if api_key_value and not api_key_value.startswith("hth_"):
            api_key_value = None

    if api_key_value is not None:
        prefix = api_key_value[4:12]  # chars after "hth_", first 8
        candidates = db.get_api_key_by_prefix(prefix)
        key_hash = hashlib.sha256(api_key_value.encode()).hexdigest()
        matched = next(
            (c for c in candidates if c["key_hash"] == key_hash), None
        )
        if matched is None or not matched["is_active"]:
            _not_authed()
        if matched["expires_at"]:
            expires = datetime.fromisoformat(matched["expires_at"])
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if expires < datetime.now(timezone.utc):
                _not_authed()
        user = db.get_user_by_id(matched["user_id"])
        if not user or not user.get("is_active"):
            _not_authed()
        now_ts = datetime.now(timezone.utc).isoformat()
        db.touch_api_key_last_used(matched["id"], now_ts)
        # Stash matched key on request.state so require_scope() can inspect
        # the scope list without re-doing the prefix/hash lookup.
        try:
            request.state.api_key = matched
        except Exception:
            pass
        return user

    # --- Session cookie fallback ---
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


def get_api_key_scopes(api_key: Optional[dict]) -> set:
    """Decode the JSON scope field on an api_keys row. Missing/malformed →
    default to ``{"*"}`` for backward compatibility (pre-feature-5 keys)."""
    if not api_key:
        return {"*"}
    raw = api_key.get("scope") or '["*"]'
    if isinstance(raw, list):
        return set(raw)
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list) and all(isinstance(s, str) for s in parsed):
            return set(parsed)
    except (ValueError, TypeError):
        pass
    _log.warning(
        "api_key %s has malformed scope=%r — treating as wildcard",
        api_key.get("id"), raw,
    )
    return {"*"}


def scope_grants(scopes: set, required: str) -> bool:
    """True if the scope set grants ``required``. The wildcard ``*`` grants
    every scope; exact-match otherwise."""
    return "*" in scopes or required in scopes


def require_scope(*required_scopes: str):
    """Dependency that enforces a per-endpoint scope check on API-key auth.

    Session-cookie auth bypasses the scope check — admins logged into the
    UI have full access by definition. Only Bearer-key requests are gated.

    Each required scope is matched against the key's scope list (which is
    persisted as a JSON array per row). A key with no scopes set, or with
    legacy '["*"]', satisfies any requirement.
    """
    async def _dep(request: Request, user: dict = Depends(get_current_user)):
        api_key = getattr(request.state, "api_key", None)
        if api_key is None:
            # Session-cookie auth — no scope check (would break the admin UI).
            return user
        scopes = get_api_key_scopes(api_key)
        for required in required_scopes:
            if not scope_grants(scopes, required):
                _log.info(
                    "require_scope: api_key %s denied — needs %r, has %r",
                    api_key.get("id"), required, sorted(scopes),
                )
                raise HTTPException(
                    status_code=403,
                    detail=f"API key lacks required scope: {required}",
                )
        return user
    return _dep
