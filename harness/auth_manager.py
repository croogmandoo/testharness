from typing import Optional
from ldap3 import Server, Connection, ALL, SUBTREE


def verify_local_password(username: str, password: str, db) -> Optional[dict]:
    import bcrypt as _bcrypt
    user = db.get_user_by_username(username)
    if user is None:
        return None
    if user.get("auth_provider") != "local":
        return None
    if not user.get("is_active"):
        return None
    if not user.get("password_hash"):
        return None
    pw_bytes = password.encode("utf-8")
    hash_bytes = user["password_hash"].encode("utf-8") if isinstance(user["password_hash"], str) else user["password_hash"]
    if not _bcrypt.checkpw(pw_bytes, hash_bytes):
        return None
    return user


def ldap_authenticate(username: str, password: str, ldap_cfg: dict) -> Optional[dict]:
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
            search_filter=ldap_cfg["user_search_filter"].format(username=username),
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
