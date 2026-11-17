"""Offline MERGE review manifests using synthetic source data and local SQLite."""
import csv
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import socket

import pytest

from telecom_pipeline.batch_control import BatchRegistry
from telecom_pipeline.incremental import METRIC_COLUMNS, canonicalize_record
from telecom_pipeline.merge_planning import MergePlanningError, build_merge_plan
from telecom_pipeline.readers import read_source
from telecom_pipeline import snowflake_config


FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_tra_incremental.csv"


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Offline planner attempted a connection")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(snowflake_config, "connect", forbidden)


def baseline():
    return [
        {"YEAR": 2026, "QUARTER": 1, **dict(zip(METRIC_COLUMNS, ("101", "201", "301", None, "501", "601", "701")))},
        {"YEAR": 2026, "QUARTER": 2, **dict(zip(METRIC_COLUMNS, ("102", "202", "302", "402", None, "602", "702")))},
    ]


def register(registry, *, path=FIXTURE, accepted=None, declared=((2026, 1), (2026, 2), (2026, 3)), **options):
    source = read_source(path)
    incoming = source.data.to_dict(orient="records")
    return registry.register(incoming, baseline() if accepted is None else accepted,
        source_sha256=source.inspection["sha256"], input_mode="QUARTER_DELTA",
        declared_periods=declared, **options)


def approve(registry, batch_id, decision="APPROVE"):
    for revision in registry.revisions(batch_id):
        registry.decide(revision["revision_id"], decision, reviewer="synthetic-test-reviewer",
            rationale="Synthetic offline planning test only", before_hash=revision["before_hash"],
            after_hash=revision["after_hash"])


def plan(registry, batch_id, *, path=FIXTURE, accepted=None):
    return build_merge_plan(registry, batch_id, source_path=path,
                            current_accepted=baseline() if accepted is None else accepted)


def write_synthetic(tmp_path, rows):
    path = tmp_path / "synthetic-plan.csv"
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("YEAR", "QUARTER", *METRIC_COLUMNS))
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_mixed_fixture_generates_one_insert_one_approved_update(tmp_path):
    with BatchRegistry(tmp_path / "registry.sqlite3") as registry:
        batch_id = register(registry)
        approve(registry, batch_id)
        before = registry.batch(batch_id)
        history = registry.history(batch_id)
        result = plan(registry, batch_id)
        manifest = result.as_dict()
        assert manifest["counts"] == {"INSERT": 1, "UPDATE": 1, "UNCHANGED": 1}
        assert manifest["business_key"] == ["YEAR", "QUARTER"]
        assert manifest["expected_total_periods"] == 3
        assert manifest["target_table"] == "TELECOM_DB.ANALYTICS.FACT_TELECOM_QUARTERLY"
        assert manifest["lineage_proposal"]["SOURCE_TABLE"] == "TELECOM_DB.STAGING.TRA_TRAFFIC_CLEAN"
        update, insert = result.operations
        assert update.action == "UPDATE" and update.after.key == (2026, 2)
        assert update.before.metrics[0] == Decimal("102")
        assert update.after.metrics[0] == Decimal("112")
        assert update.before.metrics[4] is update.after.metrics[4] is None
        assert update.approval.before_hash == update.before.payload_hash
        assert update.approval.after_hash == update.after.payload_hash
        assert insert.action == "INSERT" and insert.before is None
        assert insert.after.key == (2026, 3)
        assert insert.approval is None
        assert manifest["operations"][1]["quarter_start_date"] == "2026-07-01"
        assert registry.batch(batch_id) == before
        assert registry.history(batch_id) == history
        assert not registry.db.in_transaction


@pytest.mark.parametrize("decision", [None, "REJECT"])
def test_revision_blocks_whole_batch_without_latest_approval(tmp_path, decision):
    with BatchRegistry(tmp_path / "r.sqlite3") as registry:
        batch_id = register(registry)
        if decision:
            approve(registry, batch_id, decision)
        with pytest.raises(MergePlanningError, match="explicit approval"):
            plan(registry, batch_id)
        assert not registry.db.in_transaction


def test_revocation_invalidates_previous_review_plan(tmp_path):
    with BatchRegistry(tmp_path / "r.sqlite3") as registry:
        batch_id = register(registry)
        approve(registry, batch_id)
        original = plan(registry, batch_id)
        approve(registry, batch_id, "REJECT")
        with pytest.raises(MergePlanningError, match="explicit approval"):
            plan(registry, batch_id)
        approve(registry, batch_id)
        updated = plan(registry, batch_id)
        assert original.operations[0].approval.version == 1
        assert updated.operations[0].approval.version == 3
        assert updated.as_dict()["plan_sha256"] != original.as_dict()["plan_sha256"]


def test_deterministic_retries_and_reopen(tmp_path):
    path = tmp_path / "r.sqlite3"
    with BatchRegistry(path) as registry:
        batch_id = register(registry)
        approve(registry, batch_id)
        first = plan(registry, batch_id).to_json()
        assert plan(registry, batch_id).to_json() == first
    with BatchRegistry(path) as registry:
        assert register(registry) == batch_id
        assert plan(registry, batch_id).to_json() == first


def test_insert_only_and_already_accepted_replay_produces_no_operations(tmp_path):
    incoming = {"YEAR": 2026, "QUARTER": 3, **dict.fromkeys(METRIC_COLUMNS, "103")}
    path = write_synthetic(tmp_path, [incoming])
    with BatchRegistry(tmp_path / "r.sqlite3") as registry:
        first_batch = register(registry, path=path, declared=[(2026, 3)])
        first = plan(registry, first_batch, path=path)
        assert [row.action for row in first.operations] == ["INSERT"]
        # A different file with equivalent rows arrives after the quarter was accepted.
        accepted = baseline() + [incoming]
        replay_path = tmp_path / "synthetic-replay.csv"
        replay_path.write_bytes(path.read_bytes() + b"\n")
        replay_batch = register(registry, path=replay_path, accepted=accepted, declared=[(2026, 3)])
        replay = plan(registry, replay_batch, path=replay_path, accepted=accepted)
        assert replay.operations == ()
        assert replay.as_dict()["counts"] == {"INSERT": 0, "UPDATE": 0, "UNCHANGED": 1}


@pytest.mark.parametrize("before,after", [(None, "0"), ("0", None), ("100", None), (None, "100")])
def test_approved_null_transitions_preserve_before_and_after(tmp_path, before, after):
    accepted = [baseline()[1]]
    accepted[0][METRIC_COLUMNS[0]] = before
    incoming = {**accepted[0], METRIC_COLUMNS[0]: after}
    path = write_synthetic(tmp_path, [incoming])
    with BatchRegistry(tmp_path / "r.sqlite3") as registry:
        batch_id = register(registry, path=path, accepted=accepted, declared=[(2026, 2)])
        approve(registry, batch_id)
        operation = plan(registry, batch_id, path=path, accepted=accepted).operations[0]
        assert operation.before.metrics[0] == (Decimal(before) if before is not None else None)
        assert operation.after.metrics[0] == (Decimal(after) if after is not None else None)
        if after is None:
            assert operation.as_dict()["after"][METRIC_COLUMNS[0]] is None
        else:
            assert operation.after.metrics[0] == Decimal(after)


@pytest.mark.parametrize("kind", ["conflict", "unexpected", "gap", "missing_history"])
def test_invalid_classification_scope_is_rejected(tmp_path, kind):
    rows = read_source(FIXTURE).data.to_dict(orient="records")
    source_rows = [canonicalize_record(row).as_dict() for row in rows]
    declared = [(2026, 1), (2026, 2), (2026, 3)]
    if kind == "conflict":
        source_rows.append({**source_rows[0], METRIC_COLUMNS[0]: "999"})
    elif kind == "unexpected":
        declared = [(2026, 1), (2026, 2)]
    elif kind == "gap":
        source_rows[0]["QUARTER"] = 4
        declared = [(2026, 1), (2026, 2), (2026, 4)]
    path = write_synthetic(tmp_path, source_rows)
    with BatchRegistry(tmp_path / "r.sqlite3") as registry:
        if kind == "missing_history":
            incoming = [source_rows[0]]
            path = write_synthetic(tmp_path, incoming)
            source = read_source(path)
            batch_id = registry.register(source.data.to_dict(orient="records"), baseline(),
                source_sha256=source.inspection["sha256"], input_mode="FULL_SNAPSHOT", declared_periods=[(2026, 3)])
        else:
            batch_id = register(registry, path=path, declared=declared)
        approve(registry, batch_id)
        with pytest.raises(MergePlanningError, match="invalid period scope"):
            plan(registry, batch_id, path=path)


def test_explicit_scope_required_to_identify_unexpected_periods(tmp_path):
    with BatchRegistry(tmp_path / "r.sqlite3") as registry:
        batch_id = register(registry, declared=None)
        approve(registry, batch_id)
        with pytest.raises(MergePlanningError, match="exact expected period scope"):
            plan(registry, batch_id)


def test_approximate_readonly_approval_context_is_rejected(tmp_path):
    with BatchRegistry(tmp_path / "r.sqlite3") as registry:
        batch_id = register(registry, policy_version="READ_ONLY_FLOAT_TEXT_V1:snapshot:example")
        approve(registry, batch_id)
        with pytest.raises(MergePlanningError, match="Approximate read-only"):
            plan(registry, batch_id)


def test_stale_before_state_invalidates_approval(tmp_path):
    with BatchRegistry(tmp_path / "r.sqlite3") as registry:
        batch_id = register(registry)
        approve(registry, batch_id)
        changed = baseline()
        changed[1][METRIC_COLUMNS[0]] = "104"
        with pytest.raises(MergePlanningError, match="Accepted snapshot changed"):
            plan(registry, batch_id, accepted=changed)


def test_source_substitution_is_rejected(tmp_path):
    with BatchRegistry(tmp_path / "r.sqlite3") as registry:
        batch_id = register(registry)
        approve(registry, batch_id)
        path = tmp_path / "synthetic-copy.csv"
        path.write_bytes(FIXTURE.read_bytes() + b"\n")
        with pytest.raises(MergePlanningError, match="Source bytes differ"):
            plan(registry, batch_id, path=path)


def test_registered_records_must_match_actual_source_bytes(tmp_path):
    source = read_source(FIXTURE)
    rows = source.data.to_dict(orient="records")
    rows[0]["Outgoing International Minutes Million"] = "999"
    with BatchRegistry(tmp_path / "r.sqlite3") as registry:
        batch_id = registry.register(rows, baseline(), source_sha256=source.inspection["sha256"],
            input_mode="QUARTER_DELTA", declared_periods=[(2026, 1), (2026, 2), (2026, 3)])
        approve(registry, batch_id)
        with pytest.raises(MergePlanningError, match="Parsed input differs"):
            plan(registry, batch_id)


def test_synthetic_plans_cannot_authorize_real_publication(tmp_path):
    with BatchRegistry(tmp_path / "r.sqlite3") as registry:
        batch_id = register(registry)
        approve(registry, batch_id)
        result = plan(registry, batch_id)
        assert result.publication_allowed is False
        assert result.as_dict()["purpose"] == "OFFLINE_REVIEW_ONLY"
        assert result.source_origin == "SYNTHETIC_TEST"
        assert not hasattr(result, "execute")
        assert not hasattr(result, "publish")


def test_rollback_plan_preserves_before_images_and_distinguishes_commit_uncertainty(tmp_path):
    with BatchRegistry(tmp_path / "r.sqlite3") as registry:
        batch_id = register(registry)
        approve(registry, batch_id)
        result = plan(registry, batch_id)
        manifest = result.as_dict()
        rollback = manifest["rollback_plan"]
        assert "ROLLBACK" in rollback["uncommitted_failure"]
        assert "original error" in rollback["uncommitted_failure"]
        assert "checkpoint" in rollback["unknown_commit"]
        assert "new approval" in rollback["committed_correction"]
        assert manifest["operations"][0]["before"][METRIC_COLUMNS[0]] == "102"
        assert manifest["operations"][0]["before"][METRIC_COLUMNS[4]] is None
        assert manifest["operations"][1]["before"] is None
        assert "audit" in rollback["metadata_recovery"]
        assert manifest["transaction_plan"][-1].startswith("COMMIT only after")


def test_plan_digest_covers_payload_approvals_and_rollback_rules(tmp_path):
    with BatchRegistry(tmp_path / "r.sqlite3") as registry:
        batch_id = register(registry)
        approve(registry, batch_id)
        manifest = plan(registry, batch_id).as_dict()
        digest = manifest.pop("plan_sha256")
        expected = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        assert digest == expected
        manifest["operations"][0]["after"][METRIC_COLUMNS[0]] = "999"
        assert digest != hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def test_existing_plan_captures_contract_instead_of_reading_later_mutations(tmp_path, monkeypatch):
    with BatchRegistry(tmp_path / "r.sqlite3") as registry:
        batch_id = register(registry)
        approve(registry, batch_id)
        result = plan(registry, batch_id)
        before = result.to_json()
        monkeypatch.setitem(snowflake_config.WAREHOUSE_CONTRACT, "database", "SYNTHETIC_CHANGED_DB")
        assert result.to_json() == before


@pytest.mark.parametrize("field", ["before_hash", "after_hash"])
def test_mismatched_approval_evidence_blocks_planning(tmp_path, field):
    with BatchRegistry(tmp_path / "r.sqlite3") as registry:
        batch_id = register(registry)
        approve(registry, batch_id)
        decision = registry.decisions(registry.revisions(batch_id)[0]["revision_id"])[-1]
        # Deliberate corruption of a disposable LOCAL SQLite fixture.
        column = "before_hash" if field == "before_hash" else "after_hash"
        registry.db.execute(f"UPDATE decisions SET {column}=? WHERE decision_id=?", ("0" * 64, decision["decision_id"]))
        with pytest.raises(MergePlanningError, match="exact before/after"):
            plan(registry, batch_id)


def test_running_batch_is_not_replanned(tmp_path):
    with BatchRegistry(tmp_path / "r.sqlite3") as registry:
        batch_id = register(registry)
        approve(registry, batch_id)
        attempt = registry.start(batch_id)
        with pytest.raises(MergePlanningError, match="before an execution attempt"):
            plan(registry, batch_id)
        assert registry.batch(batch_id)["active_attempt_id"] == attempt
