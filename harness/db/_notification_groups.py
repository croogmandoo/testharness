"""Notification groups storage and management.

Allows creating and managing named notification groups (name, description, multiple email addresses)
and resolving notification group names/IDs to recipient email lists.
"""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any


class NotificationGroupsMixin:
    def create_notification_group(
        self,
        name: str,
        emails: List[str],
        description: str = "",
    ) -> Dict[str, Any]:
        """Create a new notification group."""
        cleaned_emails = sorted(list({e.strip().lower() for e in emails if e and "@" in e}))
        if not name or not name.strip():
            raise ValueError("Notification group name cannot be empty")
        if not cleaned_emails:
            raise ValueError("Notification group must contain at least one valid email address")

        group_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        row = {
            "id": group_id,
            "name": name.strip(),
            "description": description.strip() if description else "",
            "emails": json.dumps(cleaned_emails),
            "created_at": now,
            "updated_at": now,
        }

        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO notification_groups (id, name, description, emails, created_at, updated_at) "
                    "VALUES (:id, :name, :description, :emails, :created_at, :updated_at)",
                    row,
                )
        except sqlite3.IntegrityError:
            raise ValueError(f"Notification group with name '{name.strip()}' already exists")

        return {
            "id": group_id,
            "name": row["name"],
            "description": row["description"],
            "emails": cleaned_emails,
            "created_at": now,
            "updated_at": now,
        }

    def list_notification_groups(self) -> List[Dict[str, Any]]:
        """List all notification groups ordered by name."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM notification_groups ORDER BY name ASC"
            ).fetchall()
            result = []
            for r in rows:
                item = dict(r)
                try:
                    item["emails"] = json.loads(item["emails"])
                except Exception:
                    item["emails"] = []
                result.append(item)
            return result

    def get_notification_group(self, group_id: str) -> Optional[Dict[str, Any]]:
        """Get notification group by ID."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM notification_groups WHERE id=?",
                (group_id,),
            ).fetchone()
            if not row:
                return None
            item = dict(row)
            try:
                item["emails"] = json.loads(item["emails"])
            except Exception:
                item["emails"] = []
            return item

    def get_notification_group_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Get notification group by name (case-insensitive)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM notification_groups WHERE name=? COLLATE NOCASE",
                (name.strip(),),
            ).fetchone()
            if not row:
                return None
            item = dict(row)
            try:
                item["emails"] = json.loads(item["emails"])
            except Exception:
                item["emails"] = []
            return item

    def update_notification_group(
        self,
        group_id: str,
        name: Optional[str] = None,
        emails: Optional[List[str]] = None,
        description: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update an existing notification group."""
        group = self.get_notification_group(group_id)
        if not group:
            return None

        new_name = name.strip() if name is not None else group["name"]
        new_desc = description.strip() if description is not None else group["description"]

        if emails is not None:
            new_emails = sorted(list({e.strip().lower() for e in emails if e and "@" in e}))
            if not new_emails:
                raise ValueError("Notification group must contain at least one valid email address")
        else:
            new_emails = group["emails"]

        if not new_name:
            raise ValueError("Notification group name cannot be empty")

        now = datetime.now(timezone.utc).isoformat()
        try:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE notification_groups SET name=?, description=?, emails=?, updated_at=? WHERE id=?",
                    (new_name, new_desc, json.dumps(new_emails), now, group_id),
                )
        except sqlite3.IntegrityError:
            raise ValueError(f"Notification group with name '{new_name}' already exists")

        return self.get_notification_group(group_id)

    def delete_notification_group(self, group_id: str) -> bool:
        """Delete a notification group by ID."""
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM notification_groups WHERE id=?",
                (group_id,),
            )
            return cur.rowcount > 0

    def resolve_emails_for_groups(self, group_names_or_ids: List[str]) -> List[str]:
        """Resolve a list of notification group names or IDs to a deduplicated list of emails."""
        if not group_names_or_ids:
            return []

        resolved_emails = set()
        with self._connect() as conn:
            for ident in group_names_or_ids:
                if not ident:
                    continue
                row = conn.execute(
                    "SELECT emails FROM notification_groups WHERE id=? OR name=? COLLATE NOCASE",
                    (ident, ident),
                ).fetchone()
                if row and row["emails"]:
                    try:
                        em_list = json.loads(row["emails"])
                        for em in em_list:
                            if em and "@" in em:
                                resolved_emails.add(em.strip().lower())
                    except Exception:
                        pass

        return sorted(list(resolved_emails))
