"""Single-host, cross-process coordination. No database transaction spans an LLM call."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, SchemaError, ValidationError

from .errors import CoordinationError
from .models import Commit, Lease, LeaseRequest, Resource, ResourceCreate, WorkflowCreate


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def validate_value(value: dict, schema: dict):
    def check_refs(node):
        if isinstance(node, dict):
            for key, item in node.items():
                if key in {"$ref", "$dynamicRef"} and (
                    not isinstance(item, str) or not item.startswith("#")
                ):
                    raise CoordinationError(
                        "invalid_schema", "Only local JSON Schema references are supported", 422
                    )
                check_refs(item)
        elif isinstance(node, list):
            for item in node:
                check_refs(item)

    try:
        canonical(value)
        canonical(schema)
        check_refs(schema)
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(value)
    except (SchemaError, ValidationError, ValueError, TypeError, RecursionError) as exc:
        raise CoordinationError("schema_violation", str(exc)[:1000], 422) from exc


DDL = """
CREATE TABLE IF NOT EXISTS resources (
 key TEXT PRIMARY KEY, value TEXT NOT NULL, json_schema TEXT NOT NULL,
 version INTEGER NOT NULL, schema_version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS workflows (
 id TEXT PRIMARY KEY, status TEXT NOT NULL, steps INTEGER NOT NULL DEFAULT 0,
 max_steps INTEGER NOT NULL, deadline REAL NOT NULL, repeat_limit INTEGER NOT NULL,
 specification TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS lease_sequence (token INTEGER PRIMARY KEY AUTOINCREMENT);
CREATE TABLE IF NOT EXISTS leases (
 resource TEXT PRIMARY KEY, owner TEXT NOT NULL, token INTEGER NOT NULL, expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS attempts (
 id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_id TEXT NOT NULL, operation_id TEXT NOT NULL,
 owner TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS operations (
 workflow_id TEXT NOT NULL, operation_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
 result TEXT NOT NULL, PRIMARY KEY(workflow_id, operation_id));
CREATE TABLE IF NOT EXISTS transitions (
 workflow_id TEXT NOT NULL, fingerprint TEXT NOT NULL, count INTEGER NOT NULL,
 PRIMARY KEY(workflow_id, fingerprint));
CREATE TABLE IF NOT EXISTS events (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, workflow_id TEXT, kind TEXT NOT NULL,
 payload TEXT NOT NULL, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS events_workflow ON events(workflow_id, sequence);
"""


class Coordinator:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path == ":memory:":
            raise ValueError("Use a file-backed SQLite database; connections must share state")
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(DDL)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("PRAGMA synchronous=FULL")
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    @staticmethod
    def event(db, workflow_id, kind, payload):
        db.execute(
            "INSERT INTO events(workflow_id,kind,payload,created_at) VALUES (?,?,?,?)",
            (workflow_id, kind, canonical(payload), time.time()),
        )

    def record(self, workflow_id: str | None, kind: str, payload: dict):
        with self.transaction() as db:
            self.event(db, workflow_id, kind, payload)

    @staticmethod
    def resource(row) -> Resource:
        return Resource(
            key=row["key"],
            value=json.loads(row["value"]),
            json_schema=json.loads(row["json_schema"]),
            version=row["version"],
            schema_version=row["schema_version"],
        )

    def create_resource(self, request: ResourceCreate) -> Resource:
        validate_value(request.value, request.json_schema)
        with self.transaction() as db:
            row = db.execute("SELECT * FROM resources WHERE key=?", (request.key,)).fetchone()
            if row:
                raise CoordinationError("resource_exists", f"Resource {request.key} already exists")
            db.execute(
                "INSERT INTO resources VALUES (?,?,?,1,1)",
                (request.key, canonical(request.value), canonical(request.json_schema)),
            )
            self.event(db, None, "resource_created", {"key": request.key})
        return self.snapshot([request.key])[request.key]

    def snapshot(self, keys: list[str] | None = None) -> dict[str, Resource]:
        # A single SELECT guarantees a consistent snapshot across all requested resources.
        with self.connection() as db:
            if keys is None:
                rows = db.execute("SELECT * FROM resources ORDER BY key").fetchall()
            elif not keys:
                return {}
            else:
                rows = db.execute(
                    f"SELECT * FROM resources WHERE key IN ({','.join('?' for _ in keys)})", keys
                ).fetchall()
        result = {row["key"]: self.resource(row) for row in rows}
        if keys is not None and set(keys) - result.keys():
            raise CoordinationError(
                "resource_missing", f"Unknown resources: {sorted(set(keys) - result.keys())}", 404
            )
        return result

    def create_workflow(self, spec: WorkflowCreate) -> dict:
        with self.transaction() as db:
            existing = db.execute("SELECT * FROM workflows WHERE id=?", (spec.id,)).fetchone()
            if existing:
                if (
                    existing["specification"] != canonical(spec.specification)
                    or existing["max_steps"] != spec.max_steps
                    or existing["repeat_limit"] != spec.repeat_limit
                ):
                    raise CoordinationError(
                        "workflow_mismatch",
                        "A run ID cannot be reused for a different specification",
                    )
                return dict(existing)
            now = time.time()
            db.execute(
                "INSERT INTO workflows VALUES (?, 'running', 0, ?, ?, ?, ?, ?)",
                (
                    spec.id,
                    spec.max_steps,
                    now + spec.timeout_seconds,
                    spec.repeat_limit,
                    canonical(spec.specification),
                    now,
                ),
            )
            self.event(db, spec.id, "workflow_started", {"max_steps": spec.max_steps})
        return self.get_workflow(spec.id)

    def get_workflow(self, workflow_id: str) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT * FROM workflows WHERE id=?", (workflow_id,)).fetchone()
        if not row:
            raise CoordinationError("workflow_missing", "Unknown workflow", 404)
        return dict(row)

    @staticmethod
    def active_workflow(db, workflow_id):
        row = db.execute("SELECT * FROM workflows WHERE id=?", (workflow_id,)).fetchone()
        if not row:
            raise CoordinationError("workflow_missing", "Unknown workflow", 404)
        if row["status"] != "running":
            raise CoordinationError("workflow_closed", f"Workflow is {row['status']}")
        if row["deadline"] <= time.time():
            raise CoordinationError("deadline_exceeded", "Workflow deadline has passed")
        return row

    def begin_attempt(self, workflow_id: str, operation_id: str, owner: str) -> int:
        with self.transaction() as db:
            row = self.active_workflow(db, workflow_id)
            if row["steps"] >= row["max_steps"]:
                raise CoordinationError(
                    "step_budget_exceeded", "Workflow exhausted its attempt budget"
                )
            db.execute("UPDATE workflows SET steps=steps+1 WHERE id=?", (workflow_id,))
            attempt_id = db.execute(
                "INSERT INTO attempts(workflow_id,operation_id,owner,status,created_at) VALUES (?,?,?,'pending',?)",
                (workflow_id, operation_id, owner, time.time()),
            ).lastrowid
            self.event(
                db,
                workflow_id,
                "attempt_started",
                {"operation_id": operation_id, "owner": owner, "attempt_id": attempt_id},
            )
            return attempt_id

    def attempt_count(self, workflow_id: str, operation_id: str) -> int:
        with self.connection() as db:
            return db.execute(
                "SELECT count(*) FROM attempts WHERE workflow_id=? AND operation_id=?",
                (workflow_id, operation_id),
            ).fetchone()[0]

    def finish(self, workflow_id: str, status: str):
        if status not in {"completed", "failed", "cancelled"}:
            raise ValueError("Invalid terminal status")
        with self.transaction() as db:
            updated = db.execute(
                "UPDATE workflows SET status=? WHERE id=? AND status='running'",
                (status, workflow_id),
            ).rowcount
            if updated:
                db.execute(
                    "UPDATE attempts SET status='abandoned' WHERE workflow_id=? AND status='pending'",
                    (workflow_id,),
                )
                self.event(db, workflow_id, "workflow_" + status, {})

    def abandon_attempt(self, attempt_id: int):
        with self.transaction() as db:
            db.execute(
                "UPDATE attempts SET status='abandoned' WHERE id=? AND status='pending'",
                (attempt_id,),
            )

    def acquire(self, request: LeaseRequest) -> Lease:
        keys = sorted(set(request.resources))
        with self.transaction() as db:
            now = time.time()
            for key in keys:
                if not db.execute("SELECT 1 FROM resources WHERE key=?", (key,)).fetchone():
                    raise CoordinationError("resource_missing", f"Unknown resource {key}", 404)
                row = db.execute(
                    "SELECT * FROM leases WHERE resource=? AND expires_at>?", (key, now)
                ).fetchone()
                if row:
                    raise CoordinationError("lease_busy", f"Resource {key} has an active lease")
            # All-or-nothing acquisition: never hold one resource while waiting for another.
            token = db.execute("INSERT INTO lease_sequence DEFAULT VALUES").lastrowid
            expiry = now + request.ttl_seconds
            for key in keys:
                db.execute(
                    "INSERT OR REPLACE INTO leases VALUES (?,?,?,?)",
                    (key, request.owner, token, expiry),
                )
            self.event(
                db,
                None,
                "lease_acquired",
                {"owner": request.owner, "token": token, "resources": keys},
            )
        return Lease(owner=request.owner, token=token, resources=keys, expires_at=expiry)

    def release(self, owner: str, token: int):
        with self.transaction() as db:
            db.execute("DELETE FROM leases WHERE owner=? AND token=?", (owner, token))

    def operation(self, workflow_id: str, operation_id: str) -> dict | None:
        with self.connection() as db:
            row = db.execute(
                "SELECT result FROM operations WHERE workflow_id=? AND operation_id=?",
                (workflow_id, operation_id),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def commit(self, request: Commit) -> dict:
        # Token and attempt are transport details. Identical logical retries remain idempotent.
        fingerprint = digest(
            {
                "reads": {k: v.model_dump() for k, v in request.reads.items()},
                "plan": request.plan.model_dump(),
            }
        )
        try:
            with self.transaction() as db:
                existing = db.execute(
                    "SELECT * FROM operations WHERE workflow_id=? AND operation_id=?",
                    (request.workflow_id, request.operation_id),
                ).fetchone()
                if existing:
                    if existing["fingerprint"] != fingerprint:
                        raise CoordinationError(
                            "idempotency_mismatch",
                            "Operation ID was already committed with different content",
                        )
                    return json.loads(existing["result"])
                workflow = self.active_workflow(db, request.workflow_id)
                attempt = db.execute(
                    "SELECT * FROM attempts WHERE id=?", (request.attempt_id,)
                ).fetchone()
                if not attempt or (
                    attempt["workflow_id"],
                    attempt["operation_id"],
                    attempt["owner"],
                    attempt["status"],
                ) != (request.workflow_id, request.operation_id, request.owner, "pending"):
                    raise CoordinationError(
                        "invalid_attempt",
                        "Commit needs a pending, budgeted attempt for this owner and operation",
                    )
                writes = {w.key: w for w in request.plan.writes}
                if not writes.keys() <= request.reads.keys():
                    raise CoordinationError(
                        "blind_write", "Every write requires a snapshot version"
                    )
                now = time.time()
                current = {}
                for key, expected in request.reads.items():
                    lease = db.execute("SELECT * FROM leases WHERE resource=?", (key,)).fetchone()
                    if (
                        not lease
                        or lease["owner"] != request.owner
                        or lease["token"] != request.lease_token
                        or lease["expires_at"] <= now
                    ):
                        raise CoordinationError(
                            "stale_lease", f"Lease for {key} is absent, expired, or fenced out"
                        )
                    row = db.execute("SELECT * FROM resources WHERE key=?", (key,)).fetchone()
                    if not row:
                        raise CoordinationError("resource_missing", f"Unknown resource {key}", 404)
                    resource = self.resource(row)
                    if resource.stamp != expected:
                        raise CoordinationError(
                            "version_conflict",
                            f"{key}: expected v{expected.version}/s{expected.schema_version}, found v{resource.version}/s{resource.schema_version}",
                        )
                    current[key] = resource
                # Validate every write before any mutation. A schema migration changes data and contract together.
                prepared = []
                for key, write in writes.items():
                    old = current[key]
                    schema = write.json_schema if write.json_schema is not None else old.json_schema
                    validate_value(write.value, schema)
                    changed_schema = canonical(schema) != canonical(old.json_schema)
                    prepared.append(
                        Resource(
                            key=key,
                            value=write.value,
                            json_schema=schema,
                            version=old.version + 1,
                            schema_version=old.schema_version + int(changed_schema),
                        )
                    )
                transition = digest(
                    {
                        "reads": {
                            k: {"value": v.value, "schema": v.json_schema}
                            for k, v in current.items()
                        },
                        "writes": {
                            r.key: {"value": r.value, "schema": r.json_schema} for r in prepared
                        },
                    }
                )
                seen = db.execute(
                    "SELECT count FROM transitions WHERE workflow_id=? AND fingerprint=?",
                    (request.workflow_id, transition),
                ).fetchone()
                if seen and seen[0] >= workflow["repeat_limit"]:
                    raise CoordinationError(
                        "loop_detected", "Repeated state transition exceeded the workflow limit"
                    )
                for resource in prepared:
                    db.execute(
                        "UPDATE resources SET value=?,json_schema=?,version=?,schema_version=? WHERE key=?",
                        (
                            canonical(resource.value),
                            canonical(resource.json_schema),
                            resource.version,
                            resource.schema_version,
                            resource.key,
                        ),
                    )
                db.execute(
                    "INSERT INTO transitions VALUES (?,?,1) ON CONFLICT(workflow_id,fingerprint) DO UPDATE SET count=count+1",
                    (request.workflow_id, transition),
                )
                result = {
                    "operation_id": request.operation_id,
                    "resources": {r.key: r.model_dump() for r in prepared},
                }
                db.execute(
                    "INSERT INTO operations VALUES (?,?,?,?)",
                    (request.workflow_id, request.operation_id, fingerprint, canonical(result)),
                )
                db.execute(
                    "UPDATE attempts SET status='committed' WHERE id=?", (request.attempt_id,)
                )
                self.event(
                    db,
                    request.workflow_id,
                    "commit_accepted",
                    {
                        "operation_id": request.operation_id,
                        "owner": request.owner,
                        "versions": {r.key: r.version for r in prepared},
                        "rationale": request.plan.rationale,
                    },
                )
                return result
        except CoordinationError as exc:
            self.record(
                request.workflow_id,
                "commit_rejected",
                {"operation_id": request.operation_id, "code": exc.code, "message": exc.message},
            )
            raise

    def state(self) -> dict:
        with self.connection() as db:
            workflows = [
                dict(row)
                for row in db.execute(
                    "SELECT id,status,steps,max_steps,deadline,created_at FROM workflows ORDER BY created_at DESC LIMIT 100"
                )
            ]
            leases = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM leases WHERE expires_at>? ORDER BY resource", (time.time(),)
                )
            ]
            totals = dict(db.execute("SELECT kind,count(*) FROM events GROUP BY kind").fetchall())
        return {
            "resources": [r.model_dump() for r in self.snapshot().values()],
            "workflows": workflows,
            "leases": leases,
            "totals": totals,
        }

    def events(
        self, after: int = 0, limit: int = 200, workflow_id: str | None = None
    ) -> list[dict]:
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM events WHERE sequence>? AND (? IS NULL OR workflow_id=?) ORDER BY sequence LIMIT ?",
                (after, workflow_id, workflow_id, limit),
            ).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]
