"""Audit-log queries.

Methods call ``self._connect()`` — mix into a class that provides it
(see ``harness.db._base.Database``).
"""
import uuid
from datetime import datetime, timezone
from typing import Optional


class AuditMixin:
    def log_audit(self, action: str, *,
                  user_id: Optional[str] = None,
                  username: Optional[str] = None,
                  target: Optional[str] = None,
                  details: Optional[str] = None) -> str:
        """Append an audit event.  Always best-effort: SQL errors are swallowed
        so that audit-log failures never block the action being audited."""
        entry_id = str(uuid.uuid4())
        ts = datetime.now(timezone.utc).isoformat()
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO audit_log (id, timestamp, user_id, username, "
                    "action, target, details) VALUES (?,?,?,?,?,?,?)",
                    (entry_id, ts, user_id, username, action, target, details),
                )
        except Exception:  # pragma: no cover — see comment above
            pass
        return entry_id

    def list_audit_log(self, limit: int = 200,
                       user_id: Optional[str] = None,
                       action: Optional[str] = None) -> list:
        clauses, args = [], []
        if user_id:
            clauses.append("user_id=?"); args.append(user_id)
        if action:
            clauses.append("action=?"); args.append(action)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        args.append(int(limit))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM audit_log {where} "
                "ORDER BY timestamp DESC LIMIT ?",
                args,
            ).fetchall()
        return [dict(r) for r in rows]
