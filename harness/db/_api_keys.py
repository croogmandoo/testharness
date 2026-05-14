"""API-key storage.

Methods call ``self._connect()`` — mix into a class that provides it
(see ``harness.db._base.Database``).
"""
from typing import Optional


class APIKeysMixin:
    def insert_api_key(self, row: dict) -> None:
        # ``scope`` is optional for back-compat with callers that pre-date
        # feature #5. Default to wildcard JSON.
        row = {**row}
        row.setdefault("scope", '["*"]')
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO api_keys "
                "(id, user_id, name, key_prefix, key_hash, expires_at, "
                "created_at, last_used_at, is_active, scope) "
                "VALUES (:id,:user_id,:name,:key_prefix,:key_hash,:expires_at,"
                ":created_at,:last_used_at,:is_active,:scope)",
                row,
            )

    def get_api_key_by_prefix(self, prefix: str) -> list:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM api_keys WHERE key_prefix=?", (prefix,)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_api_key_by_id(self, key_id: str) -> Optional[dict]:
        """Look up an api_keys row by primary key. Returns None if not found.
        Used by /admin/runners to surface the scope of the key a worker
        registered with (feature workers-v2 #5 cross-link)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM api_keys WHERE id=?", (key_id,),
            ).fetchone()
            return dict(row) if row else None

    def revoke_api_key(self, key_id: str, user_id: Optional[str] = None) -> None:
        with self._connect() as conn:
            if user_id is None:
                conn.execute(
                    "UPDATE api_keys SET is_active=0 WHERE id=?", (key_id,)
                )
            else:
                conn.execute(
                    "UPDATE api_keys SET is_active=0 WHERE id=? AND user_id=?",
                    (key_id, user_id),
                )

    def list_api_keys_for_user(self, user_id: str) -> list:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM api_keys WHERE user_id=? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def list_all_api_keys(self) -> list:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT k.*, u.username FROM api_keys k "
                "JOIN users u ON k.user_id = u.id "
                "ORDER BY k.created_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def touch_api_key_last_used(self, key_id: str, timestamp: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE api_keys SET last_used_at=? WHERE id=?",
                (timestamp, key_id),
            )
