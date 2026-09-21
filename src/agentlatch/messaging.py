"""Transactional mailbox/outbox with fenced, at-least-once delivery."""

import json

from .errors import CoordinationError

MESSAGING_DDL = """
CREATE TABLE IF NOT EXISTS channels (
 destination TEXT NOT NULL, kind TEXT NOT NULL, schema_version INTEGER NOT NULL,
 json_schema TEXT NOT NULL, PRIMARY KEY(destination,kind,schema_version));
CREATE TABLE IF NOT EXISTS deliveries (
 id TEXT PRIMARY KEY, logical_id TEXT NOT NULL, workflow_id TEXT NOT NULL,
 operation_id TEXT NOT NULL, kind TEXT NOT NULL, destination TEXT NOT NULL,
 schema_version INTEGER NOT NULL, payload TEXT NOT NULL, context TEXT NOT NULL,
 status TEXT NOT NULL, owner TEXT, token INTEGER NOT NULL DEFAULT 0,
 expires_at REAL NOT NULL DEFAULT 0, available_at REAL NOT NULL DEFAULT 0,
 attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 5,
 last_error TEXT, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS delivery_pending ON deliveries(kind,destination,status,available_at);
"""


class Messaging:
    def register_channel(self, destination, kind, version, schema):
        from .coordinator import canonical, validate_value

        validate_value({}, schema, check_value=False)
        with self.transaction() as db:
            old = db.execute(
                "SELECT json_schema FROM channels WHERE destination=? AND kind=? AND schema_version=?",
                (destination, kind, version),
            ).fetchone()
            if old and old["json_schema"] != canonical(schema):
                raise CoordinationError("channel_immutable", "Publish a new channel schema version")
            db.execute(
                "INSERT INTO channels VALUES (?,?,?,?) ON CONFLICT(destination,kind,schema_version) DO NOTHING",
                (destination, kind, version, canonical(schema)),
            )

    def stage_envelopes(self, db, request, specification):
        from .coordinator import canonical, digest, validate_value

        envelopes = request.plan.envelopes
        if len({e.id for e in envelopes}) != len(envelopes):
            raise CoordinationError(
                "duplicate_envelope", "Envelope IDs must be unique within a proposal", 422
            )
        task = next(
            (t for t in specification.get("tasks", []) if t["id"] == request.operation_id), None
        )
        if envelopes and (
            task is None
            or any(e.destination not in task.get("destinations", []) for e in envelopes)
        ):
            raise CoordinationError(
                "scope_violation", "Envelope destination is not declared by the task", 422
            )
        ids = []
        for envelope in envelopes:
            channel = db.execute(
                "SELECT json_schema FROM channels WHERE destination=? AND kind=? AND schema_version=?",
                (envelope.destination, envelope.kind, envelope.schema_version),
            ).fetchone()
            if not channel:
                raise CoordinationError(
                    "channel_missing", "Register the destination schema before publishing", 422
                )
            validate_value(envelope.payload, json.loads(channel["json_schema"]))
            delivery_id = "msg-" + digest([request.workflow_id, request.operation_id, envelope.id])
            db.execute(
                "INSERT INTO deliveries(id,logical_id,workflow_id,operation_id,kind,destination,schema_version,payload,context,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    delivery_id,
                    envelope.id,
                    request.workflow_id,
                    request.operation_id,
                    envelope.kind,
                    envelope.destination,
                    envelope.schema_version,
                    canonical(envelope.payload),
                    canonical({k: v.model_dump() for k, v in request.reads.items()}),
                    "pending",
                    self.now(db),
                ),
            )
            self.event(
                db,
                request.workflow_id,
                "envelope_queued",
                {"id": delivery_id, "kind": envelope.kind, "destination": envelope.destination},
            )
            ids.append(delivery_id)
        return ids

    def claim_delivery(self, kind, destination, owner, ttl=30, workflow_id=None):
        if kind not in {"message", "effect"} or not 0.05 <= ttl <= 300:
            raise ValueError("Invalid delivery kind or TTL")
        with self.transaction() as db:
            now = self.now(db)
            # Expired final attempts become dead letters rather than getting stuck forever.
            exhausted = db.execute(
                "SELECT id,workflow_id FROM deliveries WHERE kind=? AND destination=? AND status IN ('pending','inflight') AND expires_at<=? AND attempts>=max_attempts",
                (kind, destination, now),
            ).fetchall()
            for row in exhausted:
                db.execute(
                    "UPDATE deliveries SET status='dead',owner=NULL WHERE id=?", (row["id"],)
                )
                self.event(db, row["workflow_id"], "delivery_dead", {"id": row["id"]})
            row = db.execute(
                "SELECT * FROM deliveries WHERE kind=? AND destination=? AND status IN ('pending','inflight') AND expires_at<=? AND available_at<=? AND attempts<max_attempts AND (CAST(? AS TEXT) IS NULL OR workflow_id=?) ORDER BY created_at,id LIMIT 1",
                (kind, destination, now, now, workflow_id, workflow_id),
            ).fetchone()
            if not row:
                return None
            token = row["token"] + 1
            db.execute(
                "UPDATE deliveries SET status='inflight',owner=?,token=?,expires_at=?,attempts=attempts+1 WHERE id=?",
                (owner, token, now + ttl, row["id"]),
            )
            result = dict(row)
            result.update(
                owner=owner,
                token=token,
                status="inflight",
                expires_at=now + ttl,
                attempts=row["attempts"] + 1,
            )
            result["payload"] = json.loads(result["payload"])
            result["context"] = json.loads(result["context"])
            return result

    def acknowledge_in_transaction(self, db, ack, workflow_id=None):
        row = db.execute("SELECT * FROM deliveries WHERE id=?", (ack.id,)).fetchone()
        if not row or row["owner"] != ack.owner or row["token"] != ack.token:
            raise CoordinationError("delivery_fenced", "Delivery owner or token does not match")
        if workflow_id and (row["workflow_id"] != workflow_id or row["kind"] != "message"):
            raise CoordinationError(
                "delivery_scope", "Only messages from this workflow may be consumed atomically"
            )
        if row["status"] == "delivered":
            return
        if row["status"] != "inflight" or row["expires_at"] <= self.now(db):
            raise CoordinationError("delivery_fenced", "Delivery claim has expired")
        db.execute("UPDATE deliveries SET status='delivered' WHERE id=?", (ack.id,))
        self.event(db, row["workflow_id"], "delivery_acknowledged", {"id": ack.id})

    def acknowledge(self, ack):
        with self.transaction() as db:
            self.acknowledge_in_transaction(db, ack)
        return {"id": ack.id, "status": "delivered"}

    def reject_delivery(self, ack, retry_after=1):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM deliveries WHERE id=?", (ack.id,)).fetchone()
            if (
                not row
                or row["owner"] != ack.owner
                or row["token"] != ack.token
                or row["status"] != "inflight"
                or row["expires_at"] <= self.now(db)
            ):
                raise CoordinationError(
                    "delivery_fenced", "Delivery claim no longer belongs to this worker"
                )
            status = "dead" if row["attempts"] >= row["max_attempts"] else "pending"
            db.execute(
                "UPDATE deliveries SET status=?,owner=NULL,expires_at=0,available_at=?,last_error=? WHERE id=?",
                (status, self.now(db) + retry_after, "handler_failed", ack.id),
            )
            self.event(db, row["workflow_id"], "delivery_" + status, {"id": ack.id})

    def deliveries(self, limit=100):
        with self.connection() as db:
            return [
                {
                    **dict(r),
                    "payload": json.loads(r["payload"]),
                    "context": json.loads(r["context"]),
                }
                for r in db.execute(
                    "SELECT * FROM deliveries ORDER BY created_at DESC,id LIMIT ?", (limit,)
                )
            ]
