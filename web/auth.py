import json
import logging
from typing import Optional, Tuple

from fastapi import Depends, HTTPException, Request, Response
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


# Roles a user row may carry. Anything else is rejected at the edge
# (users.py) rather than silently matching no ``require_role`` check.
VALID_ROLES = ("admin", "runner", "reporting", "read_only")

DEFAULT_MIN_PASSWORD_LENGTH = 8


def min_password_length() -> int:
    """``auth.min_password_length`` from config (default 8, never below 1)."""
    from web.main import get_config
    raw = (get_config().get("auth") or {}).get(
        "min_password_length", DEFAULT_MIN_PASSWORD_LENGTH
    )
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        _log.warning("auth.min_password_length=%r is not an integer; using %d",
                     raw, DEFAULT_MIN_PASSWORD_LENGTH)
        return DEFAULT_MIN_PASSWORD_LENGTH


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


def make_session_token(user_id: str, signing_key: bytes, session_hours: int,
                       session_version: int = 0) -> str:
    """Sign ``[user_id, session_version]``. The version ties the token to
    ``users.session_version``; bumping that column revokes the token."""
    return URLSafeTimedSerializer(signing_key).dumps([user_id, int(session_version)])


def load_session_claims(token: str, signing_key: bytes,
                        session_hours: int) -> Optional[Tuple[str, int]]:
    """Verify ``token`` and return ``(user_id, session_version)``.

    Tokens issued before session versioning carry a bare user-id string;
    they are read as version 0 so existing sessions survive the upgrade
    until the user's first version bump. Returns None for anything invalid,
    expired or malformed."""
    if not token:
        return None
    try:
        payload = URLSafeTimedSerializer(signing_key).loads(
            token, max_age=session_hours * 3600
        )
    except (BadSignature, SignatureExpired, Exception):
        return None
    if isinstance(payload, str):
        return (payload, 0) if payload else None
    if (
        isinstance(payload, list)
        and len(payload) == 2
        and isinstance(payload[0], str) and payload[0]
        and isinstance(payload[1], int) and not isinstance(payload[1], bool)
    ):
        return payload[0], payload[1]
    return None


def load_session_token(token: str, signing_key: bytes, session_hours: int) -> Optional[str]:
    """Return only the user id from a valid session token (see
    ``load_session_claims``). Does not check the session version."""
    claims = load_session_claims(token, signing_key, session_hours)
    return claims[0] if claims else None


def _make_token(user_id: str, session_version: int = 0) -> str:
    return make_session_token(user_id, _signing_key, _session_hours, session_version)


def _load_token(token: str) -> Optional[str]:
    return load_session_token(token, _signing_key, _session_hours)


def _load_claims(token: str) -> Optional[Tuple[str, int]]:
    return load_session_claims(token, _signing_key, _session_hours)


def issue_session_cookie(response: Response, user: dict) -> Response:
    """Set the ``session`` cookie for ``user`` on ``response``.

    The single place session cookies are minted (setup, password/LDAP login
    and GitHub OAuth): the token carries the user's current
    ``session_version`` and the cookie is HttpOnly, SameSite=Lax, Secure
    per ``auth.secure_cookie`` and expires with the token."""
    token = _make_token(user["id"], int(user.get("session_version") or 0))
    response.set_cookie(
        "session", token,
        httponly=True, samesite="lax", secure=_secure_cookie,
        max_age=int(_session_hours * 3600),
    )
    return response


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
    claims = _load_claims(token)
    if not claims:
        _not_authed()
    user_id, session_version = claims
    user = db.get_user_by_id(user_id)
    if not user or not user.get("is_active"):
        _not_authed()
    # A logout, password/role change or deactivation bumps the stored
    # version, which revokes every cookie minted before it.
    if session_version != int(user.get("session_version") or 0):
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
