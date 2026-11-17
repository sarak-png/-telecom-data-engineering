"""Offline approval-gated logical MERGE plans for review of the Analytics fact.

The output is an immutable manifest. This module has no warehouse connection,
SQL renderer, executor, publication entry point, or registry mutation API.
"""
from dataclasses import dataclass
from datetime import date
import hashlib
import json
from pathlib import Path
import sqlite3

from .incremental import (
    CANONICALIZATION_VERSION, CanonicalRecord, Classification, canonicalize_record,
    classify_records,
)
from .readers import read_source
from .snowflake_config import WAREHOUSE_CONTRACT


PLANNER_VERSION = "1"


class MergePlanningError(ValueError):
    """The batch cannot supply a valid, approved offline plan."""


def _hash(value):
    text = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _record_values(record):
    if record is None:
        return None
    values = {"YEAR": record.year, "QUARTER": record.quarter}
    for name, value in list(record.as_dict().items())[2:]:
        if value is None:
            values[name] = None
        elif value == 0:
            values[name] = "0"
        else:
            text = format(value, "f")
            values[name] = text.rstrip("0").rstrip(".") if "." in text else text
    return values


def _table(object_key):
    obj = WAREHOUSE_CONTRACT["objects"][object_key]
    return ".".join((WAREHOUSE_CONTRACT["database"], obj["schema"], obj["name"]))


@dataclass(frozen=True)
class RevisionApproval:
    revision_id: str
    decision_id: str
    version: int
    reviewer: str
    decided_at: str
    rationale: str
    before_hash: str
    after_hash: str


@dataclass(frozen=True)
class MergeOperation:
    action: str
    before: CanonicalRecord | None
    after: CanonicalRecord
    approval: RevisionApproval | None

    def as_dict(self):
        return {
            "action": self.action,
            "key": [self.after.year, self.after.quarter],
            "before": _record_values(self.before),
            "after": _record_values(self.after),
            "before_hash": self.before.payload_hash if self.before else None,
            "after_hash": self.after.payload_hash,
            "quarter_start_date": date(self.after.year, (self.after.quarter - 1) * 3 + 1, 1).isoformat(),
            "approval": dict(vars(self.approval)) if self.approval else None,
        }


@dataclass(frozen=True)
class MergePlan:
    batch_id: str
    source_sha256: str
    accepted_sha256: str
    schema_version: str
    policy_version: str
    input_mode: str
    source_origin: str
    contract_sha256: str
    target_table: str
    source_table: str
    operations: tuple[MergeOperation, ...]
    unchanged_count: int
    identical_duplicate_count: int
    accepted_count: int

    @property
    def publication_allowed(self):
        return False

    def as_dict(self):
        inserts = sum(row.action == "INSERT" for row in self.operations)
        updates = sum(row.action == "UPDATE" for row in self.operations)
        manifest = {
            "purpose": "OFFLINE_REVIEW_ONLY", "planner_version": PLANNER_VERSION,
            "publication_allowed": self.publication_allowed,
            "batch_id": self.batch_id, "source_sha256": self.source_sha256,
            "accepted_sha256": self.accepted_sha256,
            "schema_version": self.schema_version, "contract_sha256": self.contract_sha256,
            "policy_version": self.policy_version,
            "canonicalization_version": CANONICALIZATION_VERSION,
            "input_mode": self.input_mode, "source_origin": self.source_origin,
            "target_table": self.target_table, "business_key": ["YEAR", "QUARTER"],
            "counts": {"INSERT": inserts, "UPDATE": updates, "UNCHANGED": self.unchanged_count},
            "identical_duplicate_count": self.identical_duplicate_count,
            "expected_total_periods": self.accepted_count + inserts,
            "operations": [row.as_dict() for row in self.operations],
            "lineage_proposal": {"SOURCE_TABLE": self.source_table,
                                 "ANALYTICS_BATCH_ID": self.batch_id,
                                 "ANALYTICS_LOADED_AT": "REQUIRES_FUTURE_RUNTIME_UTC_TIMESTAMP"},
            "merge_rules": {
                "matched": "UPDATE only approved REVISED keys whose accepted before-state still matches",
                "not_matched": "INSERT only NEW keys that remain absent",
                "unchanged_or_omitted": "Preserve all existing values and lineage",
                "nulls": "Assign incoming NULLs exactly; do not coalesce with previous values",
                "replay": "If the exact after-state is already committed, verify its durable batch before skipping",
            },
            "transaction_plan": [
                "Coordinate overlapping publishers and all source/target writers",
                "Revalidate provenance, contract, precision policy and latest approval versions",
                "BEGIN one future warehouse DML transaction",
                "Verify target key uniqueness, expected-before state and NEW-key absence",
                "Apply reviewed NEW inserts and approved REVISED updates across required layers",
                "Reconcile keys, all seven metrics, NULLs, row counts and lineage",
                "Persist accepted audit events and durable warehouse batch checkpoint atomically",
                "COMMIT only after all reconciliation checks pass",
            ],
            "rollback_plan": {
                "before_images": "Use each UPDATE operation's before payload and hash for review",
                "uncommitted_failure": "ROLLBACK the complete warehouse transaction and preserve the original error",
                "unknown_commit": "Inspect durable warehouse checkpoint and accepted versions before retrying",
                "committed_correction": "Obtain a new approval for a compensating revision; review NEW-key withdrawal separately",
                "metadata_recovery": "Future audit must retain complete original fact lineage as well as metric before-images",
            },
        }
        return {**manifest, "plan_sha256": _hash(manifest)}

    def to_json(self):
        return json.dumps(self.as_dict(), sort_keys=True, indent=2)


def build_merge_plan(registry, batch_id, *, source_path, current_accepted,
                     expected_policy_version="1", synthetic=False):
    """Produce a deterministic review manifest from an existing approved batch.

    The source is parsed and hashed from one byte snapshot by the shared reader.
    The registry is read inside a consistent SQLite snapshot, without changing
    decisions, attempts or statuses. A current canonical accepted snapshot is
    required; approximate read-only observations are rejected.
    """
    source_path = Path(source_path)
    source = read_source(source_path)
    incoming = tuple(canonicalize_record(row) for row in source.data.to_dict(orient="records"))
    accepted = tuple(canonicalize_record(row) for row in current_accepted)
    accepted_hash = _hash(sorted((row.year, row.quarter, row.payload_hash) for row in accepted))
    if registry.db.in_transaction:
        raise MergePlanningError("Finish the active local registry transaction before planning")
    registry.db.execute("BEGIN")
    try:
        batch = registry.batch(batch_id)
        if batch["schema_version"] != WAREHOUSE_CONTRACT["contract_version"]:
            raise MergePlanningError("Warehouse contract version changed")
        if batch["canonicalization_version"] != CANONICALIZATION_VERSION:
            raise MergePlanningError("Canonicalization version changed")
        if batch["policy_version"].startswith("READ_ONLY_"):
            raise MergePlanningError("Approximate read-only observations cannot authorize MERGE planning")
        if batch["policy_version"] != expected_policy_version:
            raise MergePlanningError("Review the batch policy version before planning")
        if batch["status"] in ("RUNNING", "COMPLETED"):
            raise MergePlanningError("Plan from a held or pending batch before an execution attempt")
        if batch["source_sha256"] != source.inspection["sha256"]:
            raise MergePlanningError("Source bytes differ from the registered file")
        if batch["input_hash"] != _hash([row.payload_hash for row in incoming]):
            raise MergePlanningError("Parsed input differs from the registered records")
        if batch["accepted_hash"] != accepted_hash:
            raise MergePlanningError("Accepted snapshot changed; reclassification and review are required")
        stored_plan = json.loads(batch["plan_json"])
        if stored_plan["declared_periods"] is None:
            raise MergePlanningError("Declare the exact expected period scope in the batch before planning")
        classified = classify_records(incoming, accepted, input_mode=batch["input_mode"],
                                      declared_periods=stored_plan["declared_periods"])
        if classified.status.startswith("REJECTED_"):
            raise MergePlanningError("Conflicting duplicates or invalid period scope block this batch")
        revisions = {(row["year"], row["quarter"]): row for row in registry.revisions(batch_id)}
        operations = []
        for row in classified.records:
            if row.classification == Classification.UNCHANGED:
                continue
            approval = None
            if row.classification == Classification.REVISED:
                revision = revisions.get(row.key)
                if revision is None:
                    raise MergePlanningError("Missing revision audit evidence")
                expected_hashes = (row.before.payload_hash, row.after.payload_hash)
                if (revision["before_hash"], revision["after_hash"]) != expected_hashes:
                    raise MergePlanningError("Revision audit hashes differ from the proposed change")
                decisions = registry.decisions(revision["revision_id"])
                latest = decisions[-1] if decisions else None
                if latest is None or latest["decision"] != "APPROVE":
                    raise MergePlanningError("Every historical revision requires explicit approval")
                if (latest["before_hash"], latest["after_hash"]) != expected_hashes:
                    raise MergePlanningError("Approval does not bind the exact before/after payloads")
                approval = RevisionApproval(revision["revision_id"], latest["decision_id"],
                    latest["version"], latest["reviewer"], latest["decided_at"], latest["rationale"], *expected_hashes)
            action = "INSERT" if row.classification == Classification.NEW else "UPDATE"
            operations.append(MergeOperation(action, row.before, row.after, approval))
        origin = "SYNTHETIC_TEST" if synthetic or "synthetic" in source_path.name.lower() or any(
            part.lower() == "fixtures" for part in source_path.parts) else "UNVERIFIED_SOURCE"
        result = MergePlan(batch_id, batch["source_sha256"], accepted_hash, batch["schema_version"],
                           batch["policy_version"], batch["input_mode"], origin,
                           _hash(WAREHOUSE_CONTRACT), _table("quarterly_fact"), _table("staging_clean"),
                           tuple(operations), classified.counts["UNCHANGED"],
                           classified.identical_duplicate_count, len(accepted))
    except BaseException:
        try:
            registry.db.rollback()
        except sqlite3.Error:
            pass
        raise
    else:
        registry.db.rollback()
        return result
