"""Remote-runner (worker) registration and lifecycle.

Methods call ``self._connect()`` — mix into a class that provides it
(see ``harness.db._base.Database``).
"""
import json
import uuid
from datetime import datetime, timezone
from typing import Optional


class RunnersMixin:
    def upsert_runner(self, *, name: str, tags: list, capabilities: list,
                       version: Optional[str] = None,
                       api_key_id: Optional[str] = None,
                       version_stale: Optional[int] = None) -> dict:
        """Insert or update a runner row. Sets first_seen_at on insert,
        last_seen_at on every call, and marks status='online'.

        ``version_stale`` (0|1) is written when supplied — callers compute it
        from ``harness.version_skew.is_version_stale``. None preserves the
        existing value on update / defaults to 0 on insert.
        """
        now = datetime.now(timezone.utc).isoformat()
        tags_json = json.dumps(sorted(set(tags or [])))
        caps_json = json.dumps(sorted(set(capabilities or [])))
        existing = self.get_runner_by_name(name)
        vs = int(bool(version_stale)) if version_stale is not None else 0
        with self._connect() as conn:
            if existing is None:
                rid = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO runners (id, name, tags, capabilities, version, "
                    "api_key_id, first_seen_at, last_seen_at, status, version_stale) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (rid, name, tags_json, caps_json, version,
                     api_key_id, now, now, "online", vs),
                )
            else:
                # Only overwrite version_stale when caller supplied a value;
                # otherwise leave whatever was last set.
                if version_stale is None:
                    conn.execute(
                        "UPDATE runners SET tags=?, capabilities=?, version=?, "
                        "api_key_id=COALESCE(?, api_key_id), last_seen_at=?, "
                        "status=CASE WHEN status='draining' THEN 'draining' ELSE 'online' END "
                        "WHERE name=?",
                        (tags_json, caps_json, version, api_key_id, now, name),
                    )
                else:
                    conn.execute(
                        "UPDATE runners SET tags=?, capabilities=?, version=?, "
                        "api_key_id=COALESCE(?, api_key_id), last_seen_at=?, "
                        "version_stale=?, "
                        "status=CASE WHEN status='draining' THEN 'draining' ELSE 'online' END "
                        "WHERE name=?",
                        (tags_json, caps_json, version, api_key_id, now, vs, name),
                    )
        return self.get_runner_by_name(name)

    def set_runner_version_stale(self, name: str, stale: bool) -> None:
        """Mark a runner row's version_stale flag (0 or 1). Used by the
        heartbeat path which doesn't otherwise mutate the row's metadata."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE runners SET version_stale=? WHERE name=?",
                (1 if stale else 0, name),
            )

    def set_runner_health(self, name: str, health: Optional[str]) -> None:
        """Record the worker's self-reported health: 'healthy' | 'unhealthy'
        | None. Heartbeat handlers also auto-toggle ``status`` based on this
        value (unhealthy → draining; healthy → online) — see runner.py."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE runners SET health=? WHERE name=?", (health, name),
            )

    def get_runner_by_name(self, name: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM runners WHERE name=?", (name,)
            ).fetchone()
            if not row:
                return None
            r = dict(row)
            r["tags"] = json.loads(r.get("tags") or "[]")
            r["capabilities"] = json.loads(r.get("capabilities") or "[]")
            return r

    def list_runners(self) -> list:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM runners ORDER BY name"
            ).fetchall()
        result = []
        for row in rows:
            r = dict(row)
            r["tags"] = json.loads(r.get("tags") or "[]")
            r["capabilities"] = json.loads(r.get("capabilities") or "[]")
            result.append(r)
        return result

    def touch_runner_heartbeat(self, name: str, timestamp: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE runners SET last_seen_at=?, "
                "status=CASE WHEN status='draining' THEN 'draining' ELSE 'online' END "
                "WHERE name=?",
                (timestamp, name),
            )

    def set_runner_status(self, name: str, status: str) -> None:
        """Set status to 'online' | 'offline' | 'draining'."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE runners SET status=? WHERE name=?", (status, name),
            )

    def delete_runner(self, name: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM runners WHERE name=?", (name,))
