"""Runner-job dispatch queue.

Queue / claim / complete / requeue logic for the distributed worker
pool, plus the reaper-support and admin queries used by /admin/runners.

Methods call ``self._connect()`` — mix into a class that provides it
(see ``harness.db._base.Database``).
"""
import uuid
from datetime import datetime, timezone
from typing import Optional


class RunnerJobsMixin:
    def queue_runner_job(self, *, run_id: str, app_def_json: str,
                          environment: str,
                          runner_name: Optional[str] = None,
                          target_tag: Optional[str] = None) -> str:
        """Insert a queued job. Exactly one of runner_name / target_tag should
        be set (or both NULL = any runner). Returns the job id."""
        job_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO runner_jobs "
                "(id, run_id, runner_name, target_tag, app_def_json, environment, "
                "state, queued_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (job_id, run_id, runner_name, target_tag, app_def_json,
                 environment, "queued", now),
            )
        return job_id

    def claim_runner_jobs(self, *, worker_name: str, worker_tags: list,
                           capacity: int) -> list:
        """Atomically claim up to `capacity` queued jobs that match this worker
        — pinned to its name OR tag-dispatched and matching one of its tags.
        Returns the claimed job rows (with state='assigned')."""
        if capacity <= 0:
            return []
        tags = list(worker_tags or [])
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            tag_placeholders = ",".join("?" * len(tags)) if tags else "NULL"
            tag_clause = (
                f"target_tag IN ({tag_placeholders})" if tags else "0"
            )
            sql = (
                f"SELECT id FROM runner_jobs WHERE state='queued' "
                f"AND (runner_name = ? "
                f"     OR (runner_name IS NULL AND target_tag IS NULL) "
                f"     OR (runner_name IS NULL AND {tag_clause})) "
                f"ORDER BY queued_at LIMIT ?"
            )
            params: list = [worker_name] + tags + [capacity]
            candidate_ids = [r["id"] for r in conn.execute(sql, params).fetchall()]
            claimed = []
            for jid in candidate_ids:
                cur = conn.execute(
                    "UPDATE runner_jobs SET state='assigned', assigned_to=?, assigned_at=? "
                    "WHERE id=? AND state='queued'",
                    (worker_name, now, jid),
                )
                if cur.rowcount == 1:
                    row = conn.execute(
                        "SELECT * FROM runner_jobs WHERE id=?", (jid,),
                    ).fetchone()
                    claimed.append(dict(row))
            return claimed

    def complete_runner_job(self, job_id: str,
                             error: Optional[str] = None) -> Optional[dict]:
        """Mark a job complete (or errored). Returns the job row."""
        now = datetime.now(timezone.utc).isoformat()
        state = "complete" if error is None else "error"
        with self._connect() as conn:
            conn.execute(
                "UPDATE runner_jobs SET state=?, completed_at=?, error=? WHERE id=?",
                (state, now, error, job_id),
            )
            row = conn.execute(
                "SELECT * FROM runner_jobs WHERE id=?", (job_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_runner_job(self, job_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM runner_jobs WHERE id=?", (job_id,),
            ).fetchone()
            return dict(row) if row else None

    def list_runner_jobs_for_runner(self, name: str,
                                     limit: int = 50) -> list:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM runner_jobs WHERE assigned_to=? "
                "ORDER BY queued_at DESC LIMIT ?",
                (name, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def count_inflight_jobs_for_runner(self, name: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM runner_jobs WHERE assigned_to=? "
                "AND state='assigned'",
                (name,),
            ).fetchone()
        return row[0] if row else 0

    def sum_retry_count_for_runner(self, name: str, *, since_iso: str) -> int:
        """Sum of runner_jobs.retry_count for jobs assigned to this worker
        whose queued_at >= since_iso. Surfaces in the /admin/runners 'Retries'
        column so operators can spot a worker that keeps stranding jobs.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(retry_count), 0) AS total "
                "FROM runner_jobs WHERE assigned_to=? AND queued_at >= ?",
                (name, since_iso),
            ).fetchone()
        return int(row["total"]) if row else 0

    def list_recent_screenshots_for_workers(self, since_iso: str) -> list:
        """Return test_results rows whose run was executed by ANY worker
        (runner_jobs.worker IS NOT NULL) and whose screenshot_path is set,
        finished within the last 24h. Used by /admin/runners 'Last 24h
        artifacts' panel.

        Each row: {test_result_id, screenshot_path, finished_at, run_id,
        worker_name}.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT tr.id AS test_result_id, "
                "       tr.screenshot_path, "
                "       tr.finished_at, "
                "       tr.run_id, "
                "       rj.assigned_to AS worker_name "
                "FROM test_results tr "
                "JOIN runner_jobs rj ON rj.run_id = tr.run_id "
                "WHERE tr.screenshot_path IS NOT NULL "
                "  AND tr.screenshot_path != '' "
                "  AND rj.assigned_to IS NOT NULL "
                "  AND tr.finished_at IS NOT NULL "
                "  AND tr.finished_at >= ?",
                (since_iso,),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_reaper_failed_runs(self, *, limit: int = 50) -> list:
        """Return runs that the reaper permanently failed (retry budget
        exhausted). Joins runner_jobs → runs so the dashboard panel can
        surface the original app name + last error in one query.

        A "reaper-exhausted" job is recognised by state='failed' on
        runner_jobs with retry_count >= max_retries; since we don't store
        max_retries per-job (it's a config value), we surface every
        failed runner_job that has retry_count > 0 — that's the practical
        definition: the reaper touched it at least once and gave up.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT rj.id AS job_id, rj.run_id, rj.error AS last_error, "
                "       rj.retry_count, rj.completed_at, rj.assigned_to AS worker_name, "
                "       r.app AS app_name, r.environment, r.triggered_by "
                "FROM runner_jobs rj "
                "JOIN runs r ON r.id = rj.run_id "
                "WHERE rj.state='failed' AND rj.retry_count > 0 "
                "ORDER BY rj.completed_at DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [dict(r) for r in rows]

    def find_stuck_runner_jobs(self, *, older_than: str) -> list:
        """Return runner_jobs in 'assigned' (claimed) state whose
        ``assigned_at`` is older than the supplied ISO timestamp. Used by the
        stuck-job reaper to detect workers that crashed mid-job.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM runner_jobs WHERE state='assigned' "
                "AND assigned_at IS NOT NULL AND assigned_at < ? "
                "ORDER BY assigned_at",
                (older_than,),
            ).fetchall()
        return [dict(r) for r in rows]

    def requeue_runner_job(self, job_id: str) -> Optional[dict]:
        """Flip a stuck 'assigned' job back to 'queued' so another worker can
        claim it. Clears assignment metadata and bumps retry_count by 1.
        Returns the row after the update (or None if not found)."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE runner_jobs "
                "SET state='queued', assigned_to=NULL, assigned_at=NULL, "
                "    retry_count = retry_count + 1 "
                "WHERE id=? AND state='assigned'",
                (job_id,),
            )
            row = conn.execute(
                "SELECT * FROM runner_jobs WHERE id=?", (job_id,),
            ).fetchone()
        return dict(row) if row else None

    def fail_runner_job(self, job_id: str, error: str) -> Optional[dict]:
        """Permanently mark a job 'failed' (retry budget exhausted). The
        associated Run is moved to 'error' so the dashboard surfaces it."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "UPDATE runner_jobs SET state='failed', completed_at=?, error=? "
                "WHERE id=?",
                (now, error, job_id),
            )
            row = conn.execute(
                "SELECT * FROM runner_jobs WHERE id=?", (job_id,),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE runs SET status='error', finished_at=? WHERE id=?",
                    (now, row["run_id"]),
                )
        return dict(row) if row else None

    def manually_requeue_failed_job(self, job_id: str) -> Optional[dict]:
        """Admin-initiated re-queue of a job the reaper had given up on.
        Resets state='queued', clears assignment metadata, leaves retry_count
        in place (so operators can see the history), and flips the linked
        Run back to 'queued' so the dashboard reflects the re-attempt.

        Returns the row after the update, or None if the job didn't exist
        or wasn't in state='failed'. The caller is expected to call
        ``notify_job_queued`` to wake polling workers.
        """
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE runner_jobs "
                "SET state='queued', assigned_to=NULL, assigned_at=NULL, "
                "    completed_at=NULL, error=NULL, queued_at=? "
                "WHERE id=? AND state='failed'",
                (now, job_id),
            )
            if cur.rowcount == 0:
                return None
            row = conn.execute(
                "SELECT * FROM runner_jobs WHERE id=?", (job_id,),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE runs SET status='queued', finished_at=NULL WHERE id=?",
                    (row["run_id"],),
                )
        return dict(row) if row else None
