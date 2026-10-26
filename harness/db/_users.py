"""User storage and authentication queries.

Methods call ``self._connect()`` — mix into a class that provides it
(see ``harness.db._base.Database``).
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

_log = logging.getLogger("harness.db.users")


class UsersMixin:
    def insert_user(self, user: dict) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO users (id, username, display_name, email, password_hash, "
                "role, auth_provider, role_override, is_active, created_at, last_login_at, "
                "oauth_provider_id) "
                "VALUES (:id,:username,:display_name,:email,:password_hash,"
                ":role,:auth_provider,:role_override,:is_active,:created_at,:last_login_at,"
                ":oauth_provider_id)",
                {**user, "oauth_provider_id": user.get("oauth_provider_id")},
            )

    def get_user_by_oauth_provider_id(self, provider: str, provider_id: str):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE auth_provider=? AND oauth_provider_id=?",
                (provider, provider_id),
            ).fetchone()
            return dict(row) if row else None

    def set_user_oauth_provider_id(self, user_id: str, provider_id: str) -> bool:
        """Bind an OAuth provider id to a pre-provisioned user row.

        Only fills an empty ``oauth_provider_id``; an already-linked row is
        never re-pointed at a different external identity. Returns True when
        the row was updated."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE users SET oauth_provider_id=? "
                "WHERE id=? AND oauth_provider_id IS NULL",
                (provider_id, user_id),
            )
            return cur.rowcount == 1

    def get_user_by_username(self, username: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE username=?", (username,)
            ).fetchone()
            return dict(row) if row else None

    def get_users_by_username_ci(self, username: str) -> list:
        """All rows whose username equals ``username`` ignoring ASCII case,
        oldest first. Used by external identity providers, whose login
        names are case-insensitive, so "BOB" can't dodge the row for "bob"."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM users WHERE username = ? COLLATE NOCASE "
                "ORDER BY created_at, id",
                (username,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_user_by_id(self, user_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE id=?", (user_id,)
            ).fetchone()
            return dict(row) if row else None

    def count_users(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def list_users(self) -> list:
        """Returns all users. password_hash is excluded."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id,username,display_name,email,role,auth_provider,"
                "role_override,is_active,created_at,last_login_at FROM users "
                "ORDER BY username"
            ).fetchall()
            return [dict(r) for r in rows]

    def update_user(self, user_id: str, **kwargs) -> None:
        """Update specified fields. Accepted keys: display_name, email, role,
        role_override, is_active, password_hash."""
        allowed = {"display_name", "email", "role", "role_override",
                   "is_active", "password_hash"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return
        pairs = list(updates.items())
        sets = ", ".join(f"{k}=?" for k, _ in pairs)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE users SET {sets} WHERE id=?",
                [v for _, v in pairs] + [user_id],
            )

    def update_user_last_login(self, user_id: str, timestamp: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET last_login_at=? WHERE id=?",
                (timestamp, user_id),
            )

    def delete_user(self, user_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM users WHERE id=?", (user_id,))

    def increment_failed_login(self, user_id: str, lock_until: "str | None") -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET failed_login_count = failed_login_count + 1, "
                "locked_until = ? WHERE id = ?",
                (lock_until, user_id)
            )

    def reset_failed_login(self, user_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET failed_login_count = 0, locked_until = NULL WHERE id = ?",
                (user_id,)
            )

    def upsert_ldap_user(self, username: str, display_name: str,
                         email: Optional[str], role: str) -> Optional[dict]:
        """Create or update an LDAP user. Skips role update if role_override=1.
        Returns the up-to-date user dict (no password_hash).

        The username match is case-insensitive (directories are), so "BOB"
        resolves to the existing "bob" row and inherits its is_active and
        role_override instead of getting a fresh account. New rows are stored
        lower-cased.

        Returns ``None`` without touching any row when ``username`` matches a
        non-LDAP account (local, GitHub, ...) in any letter case. An LDAP
        login must never be able to assume a same-named account from another
        identity provider."""
        matches = self.get_users_by_username_ci(username)
        foreign = [m for m in matches if m.get("auth_provider") != "ldap"]
        if foreign:
            _log.warning(
                "LDAP login for %r refused: username belongs to a %r account",
                username, foreign[0].get("auth_provider"),
            )
            return None
        if not matches:
            user_id = str(uuid.uuid4())
            self.insert_user({
                "id": user_id,
                "username": username.lower(),
                "display_name": display_name,
                "email": email,
                "password_hash": None,
                "role": role,
                "auth_provider": "ldap",
                "role_override": 0,
                "is_active": 1,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "last_login_at": None,
            })
        else:
            # Prefer an exact-case row; otherwise the oldest one.
            existing = next((m for m in matches if m["username"] == username),
                            matches[0])
            user_id = existing["id"]
            updates: dict = {"display_name": display_name, "email": email}
            if not existing.get("role_override"):
                updates["role"] = role
            self.update_user(user_id, **updates)
        user = self.get_user_by_id(user_id)
        user.pop("password_hash", None)
        return user
