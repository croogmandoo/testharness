from typing import Optional
from ldap3 import Server, Connection, ALL, SUBTREE
from ldap3.utils.conv import escape_filter_chars

_LDAP_USERNAME_MAX_LEN = 256


_dummy_hash: Optional[bytes] = None


def _get_dummy_hash() -> bytes:
    """A throwaway bcrypt hash (default cost) used to spend the same time on
    unknown/ineligible users as on real ones, so response timing does not
    reveal which usernames exist."""
    global _dummy_hash
    if _dummy_hash is None:
        import bcrypt as _bcrypt
        _dummy_hash = _bcrypt.hashpw(b"harness-dummy-password", _bcrypt.gensalt())
    return _dummy_hash


def verify_local_password(username: str, password: str, db) -> Optional[dict]:
    import bcrypt as _bcrypt
    from datetime import datetime, timezone
    pw_bytes = password.encode("utf-8")
    user = db.get_user_by_username(username)
    eligible = (
        user is not None
        and user.get("auth_provider") == "local"
        and bool(user.get("is_active"))
        and bool(user.get("password_hash"))
    )
    if not eligible:
        _bcrypt.checkpw(pw_bytes, _get_dummy_hash())
        return None
    hash_bytes = user["password_hash"].encode("utf-8") if isinstance(user["password_hash"], str) else user["password_hash"]
    ok = _bcrypt.checkpw(pw_bytes, hash_bytes)
    # Check account lockout (after the hash so a locked account costs the same)
    locked_until = user.get("locked_until")
    if locked_until and datetime.fromisoformat(locked_until) > datetime.now(timezone.utc):
        return None  # Still locked
    # An expired lock lets the password check stand (reset happens on success)
    if not ok:
        return None
    return user


def _is_safe_ldap_username(username: str) -> bool:
    """Reject usernames that could change the bind identity or that no
    directory account would legitimately carry. ``@`` and ``\\`` would let
    the caller pick a different UPN/domain for the bind; control characters
    (including NUL) have no business in a login name."""
    if not username or len(username) > _LDAP_USERNAME_MAX_LEN:
        return False
    if "@" in username or "\\" in username:
        return False
    return not any(ord(ch) < 32 or ord(ch) == 127 for ch in username)


def ldap_authenticate(username: str, password: str, ldap_cfg: dict) -> Optional[dict]:
    # An empty password turns a simple bind into an unauthenticated bind,
    # which many directories accept — never let that count as a login.
    if not password or not password.strip():
        return None
    if not _is_safe_ldap_username(username):
        return None

    dc_parts = [
        p.split("=")[1]
        for p in ldap_cfg["base_dn"].split(",")
        if p.strip().upper().startswith("DC=")
    ]
    domain = ".".join(dc_parts)
    bind_user = f"{username}@{domain}"

    server = Server(ldap_cfg["server"], port=ldap_cfg["port"],
                    use_ssl=ldap_cfg.get("use_tls", False), get_info=ALL)
    conn = Connection(server, user=bind_user, password=password, auto_bind=False)
    try:
        if not conn.bind():
            return None
        group_attr = ldap_cfg.get("group_attribute", "memberOf")
        conn.search(
            search_base=ldap_cfg["base_dn"],
            search_filter=ldap_cfg["user_search_filter"].format(
                username=escape_filter_chars(username)),
            search_scope=SUBTREE,
            attributes=["displayName", "mail", group_attr],
        )
        if not conn.entries:
            return None
        entry = conn.entries[0]
        display_name = _safe_attr(entry, "displayName") or username
        email = _safe_attr(entry, "mail")
        groups = _safe_list_attr(entry, group_attr)
        role = ldap_cfg.get("default_role", "read_only")
        for group_dn in groups:
            if group_dn in ldap_cfg.get("role_map", {}):
                role = ldap_cfg["role_map"][group_dn]
                break
        return {"username": username, "display_name": display_name, "email": email, "role": role}
    except Exception:
        return None
    finally:
        try:
            conn.unbind()
        except Exception:
            pass


def _safe_attr(entry, attr: str) -> Optional[str]:
    try:
        val = getattr(entry, attr).value
        return str(val) if val is not None else None
    except Exception:
        return None


def _safe_list_attr(entry, attr: str) -> list:
    try:
        vals = getattr(entry, attr).values
        return list(vals) if vals else []
    except Exception:
        return []
