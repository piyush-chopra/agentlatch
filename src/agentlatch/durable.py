"""Durable scheduler ownership and terminal task outcomes shared by API replicas."""

import json

from .errors import CoordinationError

DURABLE_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY);
INSERT INTO schema_migrations VALUES (2) ON CONFLICT(version) DO NOTHING;
CREATE TABLE IF NOT EXISTS run_jobs (
 workflow_id TEXT PRIMARY KEY, mode TEXT NOT NULL, owner TEXT,
 generation INTEGER NOT NULL DEFAULT 0, expires_at REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS task_outcomes (
 workflow_id TEXT NOT NULL, task_id TEXT NOT NULL, outcome TEXT NOT NULL,
 PRIMARY KEY(workflow_id,task_id));
CREATE INDEX IF NOT EXISTS jobs_expiry ON run_jobs(expires_at);
"""


class DurableRuns:
    def remaining_seconds(self, workflow_id):
        with self.connection() as db:
            row = db.execute("SELECT deadline FROM workflows WHERE id=?", (workflow_id,)).fetchone()
            if not row:
                raise CoordinationError("workflow_missing", "Unknown workflow", 404)
            return row["deadline"] - self.now(db)

    def enqueue(self, workflow_id, mode):
        if mode not in {"scripted", "crew"}:
            raise ValueError("Unknown planner mode")
        with self.transaction() as db:
            row = db.execute(
                "SELECT mode FROM run_jobs WHERE workflow_id=?", (workflow_id,)
            ).fetchone()
            if row and row["mode"] != mode:
                raise CoordinationError(
                    "runtime_mismatch", "A durable run cannot change planner mode"
                )
            if not row:
                db.execute(
                    "INSERT INTO run_jobs(workflow_id,mode) VALUES (?,?)", (workflow_id, mode)
                )

    def pending_runs(self, limit=100):
        with self.connection() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT w.id,w.specification,j.mode FROM workflows w JOIN run_jobs j ON j.workflow_id=w.id WHERE w.status='running' AND j.expires_at<=? ORDER BY w.created_at LIMIT ?",
                    (self.now(db), limit),
                )
            ]

    def claim_run(self, workflow_id, owner, ttl=30):
        if not 0.05 <= ttl <= 300:
            raise ValueError("Claim TTL must be 0.05–300 seconds")
        with self.transaction() as db:
            self.active_workflow(db, workflow_id)
            row = db.execute(
                "SELECT * FROM run_jobs WHERE workflow_id=?", (workflow_id,)
            ).fetchone()
            now = self.now(db)
            if not row or row["expires_at"] > now:
                raise CoordinationError(
                    "run_active", "Run has a live scheduler claim or is not queued"
                )
            generation = row["generation"] + 1
            db.execute(
                "UPDATE run_jobs SET owner=?,generation=?,expires_at=? WHERE workflow_id=?",
                (owner, generation, now + ttl, workflow_id),
            )
            db.execute(
                "UPDATE attempts SET status='abandoned' WHERE workflow_id=? AND status='pending'",
                (workflow_id,),
            )
            self.event(db, workflow_id, "run_claimed", {"owner": owner, "generation": generation})
            return generation

    def check_execution(self, db, workflow_id, generation):
        row = db.execute("SELECT * FROM run_jobs WHERE workflow_id=?", (workflow_id,)).fetchone()
        if row and (
            generation != row["generation"] or row["expires_at"] <= self.now(db) or not row["owner"]
        ):
            raise CoordinationError(
                "execution_fenced", "Scheduler ownership expired or was replaced"
            )

    def renew_run(self, workflow_id, owner, generation, ttl=30):
        with self.transaction() as db:
            self.active_workflow(db, workflow_id)
            self.check_execution(db, workflow_id, generation)
            row = db.execute(
                "SELECT owner FROM run_jobs WHERE workflow_id=?", (workflow_id,)
            ).fetchone()
            if row["owner"] != owner:
                raise CoordinationError("execution_fenced", "Scheduler owner does not match")
            db.execute(
                "UPDATE run_jobs SET expires_at=? WHERE workflow_id=?",
                (self.now(db) + ttl, workflow_id),
            )

    def release_run(self, workflow_id, owner, generation):
        with self.transaction() as db:
            db.execute(
                "UPDATE run_jobs SET owner=NULL,expires_at=0 WHERE workflow_id=? AND owner=? AND generation=?",
                (workflow_id, owner, generation),
            )

    def save_outcome(self, workflow_id, task_id, outcome, generation):
        with self.transaction() as db:
            self.check_execution(db, workflow_id, generation)
            db.execute(
                "INSERT INTO task_outcomes VALUES (?,?,?) ON CONFLICT(workflow_id,task_id) DO NOTHING",
                (workflow_id, task_id, json.dumps(outcome)),
            )

    def task_outcomes(self, workflow_id):
        with self.connection() as db:
            return {
                r["task_id"]: json.loads(r["outcome"])
                for r in db.execute(
                    "SELECT * FROM task_outcomes WHERE workflow_id=?", (workflow_id,)
                )
            }

    def expire_runs(self):
        with self.transaction() as db:
            rows = db.execute(
                "SELECT id FROM workflows WHERE status='running' AND deadline<=?", (self.now(db),)
            ).fetchall()
            for row in rows:
                db.execute("UPDATE workflows SET status='failed' WHERE id=?", (row["id"],))
                db.execute(
                    "UPDATE attempts SET status='abandoned' WHERE workflow_id=? AND status='pending'",
                    (row["id"],),
                )
                self.event(db, row["id"], "workflow_failed", {"code": "deadline_exceeded"})
            return len(rows)

    def execution_status(self):
        with self.connection() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT j.*,w.status FROM run_jobs j JOIN workflows w ON w.id=j.workflow_id ORDER BY w.created_at DESC LIMIT 100"
                )
            ]
