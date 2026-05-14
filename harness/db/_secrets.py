"""Secrets and CA-cert storage.

Methods call ``self._connect()`` — mix into a class that provides it
(see ``harness.db._base.Database``).
"""
import uuid
from datetime import datetime, timezone
from typing import Optional


class SecretsMixin:
    # ── Secrets ────────────────────────────────────────────────────────────

    def upsert_secret(self, name: str, encrypted_value: str,
                      description: Optional[str] = None,
                      user_id: Optional[str] = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO secrets (id, name, encrypted_value, description, created_at, updated_at, updated_by) "
                "VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET "
                "encrypted_value=excluded.encrypted_value, description=excluded.description, "
                "updated_at=excluded.updated_at, updated_by=excluded.updated_by",
                (str(uuid.uuid4()), name, encrypted_value, description, now, now, user_id),
            )

    def get_secret(self, name: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM secrets WHERE name=?", (name,)
            ).fetchone()
            return dict(row) if row else None

    def delete_secret(self, name: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM secrets WHERE name=?", (name,))

    def list_secrets(self) -> list:
        """Returns all secrets. encrypted_value is excluded."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, name, description, created_at, updated_at, updated_by "
                "FROM secrets ORDER BY name"
            ).fetchall()
            return [dict(r) for r in rows]

    # ── CA Certs ───────────────────────────────────────────────────────────

    def insert_ca_cert(self, row: dict) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO ca_certs (id, name, pem_content, created_at, added_by) "
                "VALUES (:id, :name, :pem_content, :created_at, :added_by)",
                row,
            )

    def list_ca_certs(self) -> list:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM ca_certs ORDER BY created_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_ca_cert(self, cert_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM ca_certs WHERE id=?", (cert_id,)
            ).fetchone()
            return dict(row) if row else None

    def delete_ca_cert(self, cert_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM ca_certs WHERE id=?", (cert_id,))
