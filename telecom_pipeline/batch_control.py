"""Opt-in SQLite control state for the OFFLINE incremental workflow.

No production entry point imports this module. Completion records a local
simulation, never warehouse publication. SQLite must reside on a local disk;
this is a single-host development control plane, not a distributed lock service.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from uuid import NAMESPACE_URL, uuid4, uuid5

from .incremental import (
    CANONICALIZATION_VERSION, DATASET_ID, Classification, canonicalize_record,
    classify_records,
)
from .snowflake_config import WAREHOUSE_CONTRACT


class BatchStateError(ValueError):
    """A transition, decision binding, or retry is unsafe."""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _hash(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _payload(record):
    if record is None:
        return None
    # Text retains arbitrary Decimal precision; JSON null retains missing values.
    return {key: str(value) if value is not None else None for key, value in record.as_dict().items()}


def _accepted_hash(records):
    return _hash(sorted((record.year, record.quarter, record.payload_hash) for record in records))


class BatchRegistry:
    """Persistent plans, append-only decisions, attempt fencing and checkpoints.

    Public mutations are atomic SQLite transactions. Keep returned attempt IDs
    private to the worker. Recovery explicitly invalidates the previous attempt;
    stop that worker before recovering. No credentials or environment dumps are
    accepted. Reviewer/rationale fields must contain only non-secret audit text.
    """

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.execute("PRAGMA foreign_keys = ON")
            with self._transaction():
                version = self.db.execute("PRAGMA user_version").fetchone()[0]
                application = self.db.execute("PRAGMA application_id").fetchone()[0]
                tables = self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                if not ((version == 0 and application == 0 and not tables) or
                        (version == 1 and application == 0x54454C43)):
                    raise BatchStateError("Use a new registry file; incompatible SQLite schema")
                self.db.execute("""CREATE TABLE IF NOT EXISTS batches (
                    batch_id TEXT PRIMARY KEY, intent_hash TEXT NOT NULL UNIQUE,
                    source_sha256 TEXT NOT NULL, input_mode TEXT NOT NULL,
                    schema_version TEXT NOT NULL, canonicalization_version TEXT NOT NULL,
                    policy_version TEXT NOT NULL, input_hash TEXT NOT NULL,
                    accepted_hash TEXT NOT NULL, plan_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN
                        ('PENDING','RUNNING','WAITING_APPROVAL','FAILED','COMPLETED')),
                    checkpoint TEXT NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, completed_at TEXT, active_attempt_id TEXT,
                    error_code TEXT)""")
                self.db.execute("""CREATE TABLE IF NOT EXISTS attempts (
                    attempt_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batches,
                    started_at TEXT NOT NULL, ended_at TEXT,
                    outcome TEXT NOT NULL CHECK(outcome IN ('RUNNING','FAILED','INTERRUPTED','COMPLETED'))
                )""")
                self.db.execute("""CREATE TABLE IF NOT EXISTS revisions (
                    revision_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batches,
                    year INTEGER NOT NULL, quarter INTEGER NOT NULL,
                    before_hash TEXT NOT NULL, after_hash TEXT NOT NULL,
                    before_json TEXT NOT NULL, after_json TEXT NOT NULL,
                    changed_metrics_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(batch_id,year,quarter))""")
                self.db.execute("""CREATE TABLE IF NOT EXISTS decisions (
                    decision_id TEXT PRIMARY KEY, revision_id TEXT NOT NULL REFERENCES revisions,
                    version INTEGER NOT NULL, decision TEXT NOT NULL CHECK(decision IN ('APPROVE','REJECT')),
                    reviewer TEXT NOT NULL, rationale TEXT NOT NULL, decided_at TEXT NOT NULL,
                    before_hash TEXT NOT NULL, after_hash TEXT NOT NULL,
                    UNIQUE(revision_id,version))""")
                self.db.execute("""CREATE TABLE IF NOT EXISTS events (
                    event_id INTEGER PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batches,
                    attempt_id TEXT, status TEXT NOT NULL, checkpoint TEXT NOT NULL,
                    occurred_at TEXT NOT NULL, event_code TEXT NOT NULL)""")
                self.db.execute("PRAGMA user_version = 1")
                self.db.execute("PRAGMA application_id = 1413827651")
        except BaseException:
            self.db.close()
            raise

    @contextmanager
    def _transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            # A rollback failure must not replace the original exception.
            try:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def batch(self, batch_id):
        row = self.db.execute("SELECT * FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
        if row is None:
            raise BatchStateError("Unknown batch")
        return dict(row)

    def revisions(self, batch_id):
        self.batch(batch_id)
        return [dict(row) for row in self.db.execute("""SELECT r.*,
            (SELECT decision FROM decisions d WHERE d.revision_id=r.revision_id
             ORDER BY version DESC LIMIT 1) AS latest_decision
            FROM revisions r WHERE batch_id=? ORDER BY year,quarter""", (batch_id,))]

    def decisions(self, revision_id):
        return [dict(row) for row in self.db.execute(
            "SELECT * FROM decisions WHERE revision_id=? ORDER BY version", (revision_id,))]

    def history(self, batch_id):
        self.batch(batch_id)
        return [dict(row) for row in self.db.execute(
            "SELECT * FROM events WHERE batch_id=? ORDER BY event_id", (batch_id,))]

    def _transition(self, batch_id, status, checkpoint, code, *, attempt_id=None, error=None):
        now = _now()
        self.db.execute("""UPDATE batches SET status=?,checkpoint=?,updated_at=?,
            active_attempt_id=?,error_code=?,completed_at=? WHERE batch_id=?""",
            (status, checkpoint, now, attempt_id if status == "RUNNING" else None,
             error, now if status == "COMPLETED" else None, batch_id))
        self.db.execute("""INSERT INTO events
            (batch_id,attempt_id,status,checkpoint,occurred_at,event_code) VALUES (?,?,?,?,?,?)""",
            (batch_id, attempt_id, status, checkpoint, now, code))

    def register(self, incoming, accepted=(), *, source_sha256, input_mode,
                 schema_version=None, policy_version="1", declared_periods=None):
        """Classify and persist a batch; identical file/intent registration is a no-op.

        Caller hashes the exact immutable bytes used for parsing. Scope and version
        changes deliberately constitute different intents. An existing plan is
        never silently reclassified against a later accepted state.
        """
        if not isinstance(source_sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", source_sha256):
            raise ValueError("source_sha256 must contain 64 hexadecimal characters")
        schema_version = WAREHOUSE_CONTRACT["contract_version"] if schema_version is None else schema_version
        if schema_version != WAREHOUSE_CONTRACT["contract_version"]:
            raise ValueError("Only the current warehouse contract is supported")
        if not isinstance(policy_version, str) or not policy_version.strip():
            raise ValueError("policy_version is required")
        incoming = tuple(canonicalize_record(row) for row in incoming)
        accepted = tuple(canonicalize_record(row) for row in accepted)
        plan = classify_records(incoming, accepted, input_mode=input_mode, declared_periods=declared_periods)
        intent = {"dataset_id": DATASET_ID, "source_sha256": source_sha256.lower(),
                  "input_mode": plan.input_mode.value, "schema_version": schema_version,
                  "canonicalization_version": CANONICALIZATION_VERSION, "policy_version": policy_version,
                  "declared_periods": plan.declared_periods}
        intent_hash = _hash(intent)
        batch_id = str(uuid5(NAMESPACE_URL, "telecom-batch:" + intent_hash))
        input_hash = _hash([row.payload_hash for row in incoming])
        details = {"status": plan.status, "counts": plan.counts,
                   "input_row_count": plan.input_row_count,
                   "identical_duplicate_count": plan.identical_duplicate_count,
                   "declared_periods": plan.declared_periods,
                   **{name: getattr(plan, name) for name in ("missing_accepted_periods",
                       "missing_declared_periods", "unexpected_periods", "calendar_gaps")},
                   "records": [{"classification": row.classification.value,
                       "before": _payload(row.before), "candidates": [_payload(r) for r in row.candidates],
                       "source_ordinals": row.source_ordinals} for row in plan.records]}
        with self._transaction():
            existing = self.db.execute("SELECT input_hash FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
            if existing:
                if existing["input_hash"] != input_hash:
                    raise BatchStateError("File identity reused with different parsed records")
                return batch_id
            now = _now()
            self.db.execute("""INSERT INTO batches (batch_id,intent_hash,source_sha256,input_mode,
                schema_version,canonicalization_version,policy_version,input_hash,accepted_hash,
                plan_json,status,checkpoint,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (batch_id, intent_hash, source_sha256.lower(), plan.input_mode.value, schema_version,
                 CANONICALIZATION_VERSION, policy_version, input_hash, _accepted_hash(accepted),
                 _json(details), "PENDING", "CLASSIFIED", now, now))
            for row in plan.records:
                if row.classification != Classification.REVISED:
                    continue
                before_hash, after_hash = row.before.payload_hash, row.after.payload_hash
                revision_id = _hash([batch_id, row.key, before_hash, after_hash])
                self.db.execute("INSERT INTO revisions VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (revision_id, batch_id, *row.key, before_hash, after_hash,
                     _json(_payload(row.before)), _json(_payload(row.after)), _json(row.changed_metrics), now))
            status = ("FAILED" if plan.status.startswith("REJECTED_") else
                      "WAITING_APPROVAL" if plan.requires_approval else "PENDING")
            self._transition(batch_id, status, "CLASSIFIED", "REGISTERED",
                             error=plan.status if status == "FAILED" else None)
        return batch_id

    def decide(self, revision_id, decision, *, reviewer, rationale, before_hash, after_hash):
        """Append an exact-payload decision. Superseding decisions retain all history."""
        if decision not in ("APPROVE", "REJECT") or not reviewer.strip() or not rationale.strip():
            raise ValueError("Explicit APPROVE/REJECT, reviewer and rationale are required")
        with self._transaction():
            row = self.db.execute("SELECT * FROM revisions WHERE revision_id=?", (revision_id,)).fetchone()
            if row is None:
                raise BatchStateError("Unknown revision")
            batch = self.batch(row["batch_id"])
            if batch["status"] in ("RUNNING", "COMPLETED"):
                raise BatchStateError("Decisions are frozen during an attempt and after completion")
            if (before_hash, after_hash) != (row["before_hash"], row["after_hash"]):
                raise BatchStateError("Decision does not match the stored revision hashes")
            version = len(self.decisions(revision_id)) + 1
            decision_id = str(uuid4())
            self.db.execute("INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?)",
                (decision_id, revision_id, version, decision, reviewer.strip(), rationale.strip(),
                 _now(), before_hash, after_hash))
            revisions = self.revisions(row["batch_id"])
            plan_status = json.loads(batch["plan_json"])["status"]
            rejected = plan_status.startswith("REJECTED_") or any(r["latest_decision"] == "REJECT" for r in revisions)
            waiting = any(r["latest_decision"] is None for r in revisions)
            status = "FAILED" if rejected else "WAITING_APPROVAL" if waiting else "PENDING"
            self._transition(row["batch_id"], status, "CLASSIFIED", "REVISION_DECISION",
                             error=plan_status if plan_status.startswith("REJECTED_") else
                             "REJECTED_REVISION" if rejected else None)
        return decision_id

    def _approvals(self, batch):
        if json.loads(batch["plan_json"])["status"].startswith("REJECTED_"):
            raise BatchStateError("Classification rejected this batch")
        if any(row["latest_decision"] != "APPROVE" for row in self.revisions(batch["batch_id"])):
            raise BatchStateError("Every historical revision requires explicit approval")

    def start(self, batch_id):
        """Claim a fresh attempt, or return None for an already completed batch."""
        with self._transaction():
            batch = self.batch(batch_id)
            if batch["status"] == "COMPLETED":
                return None
            if batch["status"] not in ("PENDING", "FAILED"):
                raise BatchStateError("Batch is held or already running")
            self._approvals(batch)
            attempt_id = str(uuid4())
            self.db.execute("INSERT INTO attempts VALUES (?,?,?,?,?)", (attempt_id, batch_id, _now(), None, "RUNNING"))
            self._transition(batch_id, "RUNNING", "CLASSIFIED", "ATTEMPT_STARTED", attempt_id=attempt_id)
            return attempt_id

    def _active(self, batch_id, attempt_id):
        batch = self.batch(batch_id)
        if batch["status"] != "RUNNING" or batch["active_attempt_id"] != attempt_id:
            raise BatchStateError("Attempt is stale or not running")
        return batch

    def _publication_guard(self, batch, current_accepted):
        self._approvals(batch)
        records = tuple(canonicalize_record(row) for row in current_accepted)
        if len({row.key for row in records}) != len(records):
            raise BatchStateError("Current accepted state contains duplicate periods")
        if _accepted_hash(records) != batch["accepted_hash"]:
            raise BatchStateError("Accepted state changed; stored approvals cannot authorize this plan")

    def prepare_publication(self, batch_id, attempt_id, *, current_accepted):
        """Guard an offline proposal and return only NEW/approved REVISED records.

        No data is published. A future warehouse executor must repeat freshness
        checks and all writes in its own approved transaction.
        """
        with self._transaction():
            batch = self._active(batch_id, attempt_id)
            self._publication_guard(batch, current_accepted)
            plan = json.loads(batch["plan_json"])
            changes = tuple(canonicalize_record(row["candidates"][0]) for row in plan["records"]
                            if row["classification"] in ("NEW", "REVISED"))
            self._transition(batch_id, "RUNNING", "PUBLISH_READY", "PUBLICATION_GUARDED", attempt_id=attempt_id)
            return changes

    def complete_offline(self, batch_id, attempt_id, *, current_accepted):
        """Record successful LOCAL simulation only, with approvals/freshness rechecked."""
        with self._transaction():
            batch = self._active(batch_id, attempt_id)
            if batch["checkpoint"] != "PUBLISH_READY":
                raise BatchStateError("Prepare the guarded proposal before local completion")
            self._publication_guard(batch, current_accepted)
            self.db.execute("UPDATE attempts SET outcome='COMPLETED',ended_at=? WHERE attempt_id=?", (_now(), attempt_id))
            self._transition(batch_id, "COMPLETED", "OFFLINE_COMPLETED", "LOCAL_SIMULATION_COMPLETED",
                             attempt_id=attempt_id)

    def fail(self, batch_id, attempt_id, *, error_code):
        """Persist a retryable operational failure without exception text or secrets."""
        if not isinstance(error_code, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", error_code):
            raise ValueError("Use a non-secret uppercase error code, not exception text")
        with self._transaction():
            batch = self._active(batch_id, attempt_id)
            self.db.execute("UPDATE attempts SET outcome='FAILED',ended_at=? WHERE attempt_id=?", (_now(), attempt_id))
            self._transition(batch_id, "FAILED", batch["checkpoint"], "ATTEMPT_FAILED",
                             attempt_id=attempt_id, error=error_code)

    def recover_interrupted(self, batch_id, *, expected_attempt_id):
        """Explicitly fence a stopped worker. No time-based automatic takeover."""
        with self._transaction():
            batch = self._active(batch_id, expected_attempt_id)
            self.db.execute("UPDATE attempts SET outcome='INTERRUPTED',ended_at=? WHERE attempt_id=?",
                            (_now(), expected_attempt_id))
            self._transition(batch_id, "FAILED", batch["checkpoint"], "INTERRUPTED_ATTEMPT",
                             attempt_id=expected_attempt_id, error="INTERRUPTED")
