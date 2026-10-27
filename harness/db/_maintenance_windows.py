"""Maintenance windows storage and management mixin.

Supports one-off fixed range, recurring cron, and recurring weekly schedules
with optional targeting by application and environment.
"""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any


class MaintenanceWindowsMixin:
    def create_maintenance_window(
        self,
        name: str,
        window_type: str,
        description: str = "",
        app: Optional[str] = None,
        environment: Optional[str] = None,
        cron: Optional[str] = None,
        duration_minutes: Optional[int] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        days_of_week: Optional[List[Any]] = None,
        timezone_str: str = "UTC",
        enabled: bool = True,
        created_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a new maintenance window."""
        if not name or not name.strip():
            raise ValueError("Maintenance window name cannot be empty")

        valid_types = ("range", "cron", "weekly")
        window_type = window_type.strip().lower()
        if window_type not in valid_types:
            raise ValueError(f"Invalid window_type '{window_type}'; must be one of {valid_types}")

        if window_type == "range":
            if not start_time or not end_time:
                raise ValueError("Fixed range maintenance windows require start_time and end_time")
        elif window_type == "cron":
            if not cron or not cron.strip():
                raise ValueError("Cron maintenance windows require a cron expression")
            if not duration_minutes or duration_minutes <= 0:
                raise ValueError("Cron maintenance windows require duration_minutes > 0")
        elif window_type == "weekly":
            if not days_of_week:
                raise ValueError("Weekly maintenance windows require days_of_week")
            if not start_time:
                raise ValueError("Weekly maintenance windows require start_time (HH:MM)")
            if not duration_minutes and not end_time:
                raise ValueError("Weekly maintenance windows require duration_minutes or end_time")

        window_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        days_json = json.dumps(days_of_week) if days_of_week is not None else None

        row = {
            "id": window_id,
            "name": name.strip(),
            "description": description.strip() if description else "",
            "app": app.strip() if app and app.strip() else None,
            "environment": environment.strip() if environment and environment.strip() else None,
            "window_type": window_type,
            "cron": cron.strip() if cron and cron.strip() else None,
            "duration_minutes": int(duration_minutes) if duration_minutes is not None else None,
            "start_time": start_time.strip() if start_time and start_time.strip() else None,
            "end_time": end_time.strip() if end_time and end_time.strip() else None,
            "days_of_week": days_json,
            "timezone": timezone_str.strip() if timezone_str and timezone_str.strip() else "UTC",
            "enabled": 1 if enabled else 0,
            "created_at": now,
            "updated_at": now,
            "created_by": created_by,
        }

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO maintenance_windows (
                    id, name, description, app, environment, window_type,
                    cron, duration_minutes, start_time, end_time, days_of_week,
                    timezone, enabled, created_at, updated_at, created_by
                ) VALUES (
                    :id, :name, :description, :app, :environment, :window_type,
                    :cron, :duration_minutes, :start_time, :end_time, :days_of_week,
                    :timezone, :enabled, :created_at, :updated_at, :created_by
                )
                """,
                row,
            )

        row_copy = dict(row)
        row_copy["days_of_week"] = days_of_week
        row_copy["enabled"] = bool(enabled)
        return row_copy

    def list_maintenance_windows(
        self,
        enabled_only: bool = False,
        app: Optional[str] = None,
        environment: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List maintenance windows with optional filters."""
        query = "SELECT * FROM maintenance_windows WHERE 1=1"
        params: List[Any] = []

        if enabled_only:
            query += " AND enabled = 1"
        if app is not None:
            query += " AND (app IS NULL OR app = ?)"
            params.append(app)
        if environment is not None:
            query += " AND (environment IS NULL OR environment = ?)"
            params.append(environment)

        query += " ORDER BY created_at DESC"

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
            result = []
            for r in rows:
                item = dict(r)
                item["enabled"] = bool(item.get("enabled", 1))
                try:
                    item["days_of_week"] = json.loads(item["days_of_week"]) if item.get("days_of_week") else None
                except Exception:
                    item["days_of_week"] = None
                result.append(item)
            return result

    def get_maintenance_window(self, window_id: str) -> Optional[Dict[str, Any]]:
        """Get maintenance window by ID."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM maintenance_windows WHERE id=?",
                (window_id,),
            ).fetchone()
            if not row:
                return None
            item = dict(row)
            item["enabled"] = bool(item.get("enabled", 1))
            try:
                item["days_of_week"] = json.loads(item["days_of_week"]) if item.get("days_of_week") else None
            except Exception:
                item["days_of_week"] = None
            return item

    def update_maintenance_window(
        self,
        window_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        app: Optional[str] = None,
        environment: Optional[str] = None,
        window_type: Optional[str] = None,
        cron: Optional[str] = None,
        duration_minutes: Optional[int] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        days_of_week: Optional[List[Any]] = None,
        timezone_str: Optional[str] = None,
        enabled: Optional[bool] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update an existing maintenance window."""
        existing = self.get_maintenance_window(window_id)
        if not existing:
            return None

        updates: List[str] = []
        params: List[Any] = []

        if name is not None:
            if not name.strip():
                raise ValueError("Maintenance window name cannot be empty")
            updates.append("name = ?")
            params.append(name.strip())

        if description is not None:
            updates.append("description = ?")
            params.append(description.strip())

        if app is not None:
            updates.append("app = ?")
            params.append(app.strip() if app.strip() else None)

        if environment is not None:
            updates.append("environment = ?")
            params.append(environment.strip() if environment.strip() else None)

        if window_type is not None:
            w_type = window_type.strip().lower()
            valid_types = ("range", "cron", "weekly")
            if w_type not in valid_types:
                raise ValueError(f"Invalid window_type '{w_type}'; must be one of {valid_types}")
            updates.append("window_type = ?")
            params.append(w_type)

        if cron is not None:
            updates.append("cron = ?")
            params.append(cron.strip() if cron.strip() else None)

        if duration_minutes is not None:
            updates.append("duration_minutes = ?")
            params.append(int(duration_minutes) if duration_minutes > 0 else None)

        if start_time is not None:
            updates.append("start_time = ?")
            params.append(start_time.strip() if start_time.strip() else None)

        if end_time is not None:
            updates.append("end_time = ?")
            params.append(end_time.strip() if end_time.strip() else None)

        if days_of_week is not None:
            updates.append("days_of_week = ?")
            params.append(json.dumps(days_of_week))

        if timezone_str is not None:
            updates.append("timezone = ?")
            params.append(timezone_str.strip() if timezone_str.strip() else "UTC")

        if enabled is not None:
            updates.append("enabled = ?")
            params.append(1 if enabled else 0)

        now = datetime.now(timezone.utc).isoformat()
        updates.append("updated_at = ?")
        params.append(now)

        params.append(window_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE maintenance_windows SET {', '.join(updates)} WHERE id = ?",
                params,
            )

        return self.get_maintenance_window(window_id)

    def delete_maintenance_window(self, window_id: str) -> bool:
        """Delete maintenance window by ID."""
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM maintenance_windows WHERE id=?",
                (window_id,),
            )
            return cur.rowcount > 0
