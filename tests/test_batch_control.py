"""SQLite control-plane tests using synthetic quarterly observations only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import hashlib
import json
import sqlite3

import pytest

from telecom_pipeline.batch_control import BatchRegistry, BatchStateError
from telecom_pipeline.incremental import METRIC_COLUMNS


FILE_HASH = hashlib.sha256(b"synthetic TRA engineering fixture").hexdigest()
MODE = "QUARTER_DELTA"


def observation(quarter=2, **changes):
    return {"YEAR": 2026, "QUARTER": quarter,
            **dict.fromkeys(METRIC_COLUMNS, "100.00"), **changes}


def register(registry, incoming=None, accepted=None, **kwargs):
    return registry.register(incoming if incoming is not None else [observation(3)],
        accepted if accepted is not None else [observation()],
        **{"source_sha256": FILE_HASH, "input_mode": MODE, **kwargs})


def revision_batch(registry):
    return register(registry, [observation(**{METRIC_COLUMNS[0]: None}), observation(3)])


def decide(registry, revision, decision="APPROVE", **kwargs):
    return registry.decide(revision["revision_id"], decision,
        **{"reviewer": "test-reviewer", "rationale": "Synthetic correction reviewed",
           "before_hash": revision["before_hash"], "after_hash": revision["after_hash"], **kwargs})


def test_registry_metadata_and_duplicate_file_persist_across_reopen(tmp_path):
    path = tmp_path / "batches.sqlite3"
    with BatchRegistry(path) as registry:
        batch_id = register(registry)
        first = registry.batch(batch_id)
        assert first["status"] == "PENDING"
        assert first["checkpoint"] == "CLASSIFIED"
        assert first["source_sha256"] == FILE_HASH
        assert first["schema_version"] == "1.1"
        assert first["input_mode"] == MODE
        assert first["canonicalization_version"] == "1"
        assert datetime.fromisoformat(first["created_at"]).utcoffset().total_seconds() == 0
    with BatchRegistry(path) as registry:
        assert register(registry) == batch_id
        assert registry.batch(batch_id) == first
        assert len(registry.history(batch_id)) == 1


def test_intent_includes_mode_scope_and_policy(tmp_path):
    with BatchRegistry(tmp_path / "b.sqlite3") as registry:
        ids = {register(registry), register(registry, policy_version="2"),
               register(registry, declared_periods=[(2026, 3)]),
               register(registry, [observation(), observation(3)], input_mode="FULL_SNAPSHOT")}
        assert len(ids) == 4


def test_same_file_identity_with_different_records_is_rejected_atomically(tmp_path):
    with BatchRegistry(tmp_path / "b.sqlite3") as registry:
        batch_id = register(registry)
        with pytest.raises(BatchStateError, match="different parsed"):
            register(registry, [observation(3, **{METRIC_COLUMNS[0]: "101"})])
        assert len(registry.history(batch_id)) == 1


@pytest.mark.parametrize("file_hash", ["not-a-hash", "a" * 63, None])
def test_bad_source_hash_rejected(tmp_path, file_hash):
    with BatchRegistry(tmp_path / "b.sqlite3") as registry:
        with pytest.raises(ValueError, match="sha256"):
            register(registry, source_sha256=file_hash)


def test_unsupported_contract_rejected(tmp_path):
    with BatchRegistry(tmp_path / "b.sqlite3") as registry:
        with pytest.raises(ValueError, match="contract"):
            register(registry, schema_version="0.9")


def test_entire_mixed_batch_waits_for_revision_approval(tmp_path):
    with BatchRegistry(tmp_path / "b.sqlite3") as registry:
        batch_id = revision_batch(registry)
        assert registry.batch(batch_id)["status"] == "WAITING_APPROVAL"
        revision = registry.revisions(batch_id)[0]
        assert json.loads(revision["after_json"])[METRIC_COLUMNS[0]] is None
        assert json.loads(revision["before_json"])[METRIC_COLUMNS[0]] == "100.00"
        assert json.loads(revision["changed_metrics_json"]) == [METRIC_COLUMNS[0]]
        with pytest.raises(BatchStateError):
            registry.start(batch_id)
        with pytest.raises(BatchStateError):
            registry.prepare_publication(batch_id, "invented-attempt", current_accepted=[observation()])
        decide(registry, revision)
        assert registry.batch(batch_id)["status"] == "PENDING"
        attempt = registry.start(batch_id)
        changes = registry.prepare_publication(batch_id, attempt, current_accepted=[observation()])
        assert len(changes) == 2
        assert changes[0].metrics[0] is None
        registry.complete_offline(batch_id, attempt, current_accepted=[observation()])
        assert registry.batch(batch_id)["checkpoint"] == "OFFLINE_COMPLETED"


def test_all_revisions_require_decisions(tmp_path):
    with BatchRegistry(tmp_path / "b.sqlite3") as registry:
        accepted = [observation(1), observation(2)]
        incoming = [observation(1, **{METRIC_COLUMNS[0]: "99"}), observation(2, **{METRIC_COLUMNS[1]: None})]
        batch_id = register(registry, incoming, accepted)
        first, second = registry.revisions(batch_id)
        decide(registry, first)
        assert registry.batch(batch_id)["status"] == "WAITING_APPROVAL"
        with pytest.raises(BatchStateError):
            registry.start(batch_id)
        decide(registry, second)
        assert registry.start(batch_id)


def test_rejection_survives_reopen_and_duplicate_registration(tmp_path):
    path = tmp_path / "b.sqlite3"
    with BatchRegistry(path) as registry:
        batch_id = revision_batch(registry)
        revision = registry.revisions(batch_id)[0]
        decide(registry, revision, "REJECT")
    with BatchRegistry(path) as registry:
        assert revision_batch(registry) == batch_id
        assert registry.batch(batch_id)["error_code"] == "REJECTED_REVISION"
        with pytest.raises(BatchStateError, match="approval"):
            registry.start(batch_id)
        # Explicit superseding approval is required; retry alone cannot reopen it.
        decide(registry, revision)
        decisions = registry.decisions(revision["revision_id"])
        assert [row["decision"] for row in decisions] == ["REJECT", "APPROVE"]
        assert [row["version"] for row in decisions] == [1, 2]
        assert all(datetime.fromisoformat(row["decided_at"]).tzinfo for row in decisions)
        assert registry.start(batch_id)


@pytest.mark.parametrize("field", ["before_hash", "after_hash"])
def test_decision_hash_binding_failure_has_no_partial_writes(tmp_path, field):
    with BatchRegistry(tmp_path / "b.sqlite3") as registry:
        batch_id = revision_batch(registry)
        revision = registry.revisions(batch_id)[0]
        with pytest.raises(BatchStateError, match="hashes"):
            decide(registry, revision, **{field: "f" * 64})
        assert registry.decisions(revision["revision_id"]) == []
        assert registry.batch(batch_id)["status"] == "WAITING_APPROVAL"


@pytest.mark.parametrize("updates", [{"reviewer": " "}, {"rationale": ""}, {"decision": "YES"}])
def test_decisions_must_be_explicit(tmp_path, updates):
    with BatchRegistry(tmp_path / "b.sqlite3") as registry:
        batch_id = revision_batch(registry)
        revision = registry.revisions(batch_id)[0]
        with pytest.raises(ValueError):
            decide(registry, revision, **updates)
        assert not registry.decisions(revision["revision_id"])


@pytest.mark.parametrize("checkpoint", ["CLASSIFIED", "PUBLISH_READY"])
def test_interrupted_run_restart_and_stale_worker_fencing(tmp_path, checkpoint):
    path = tmp_path / "b.sqlite3"
    with BatchRegistry(path) as registry:
        batch_id = register(registry)
        old_attempt = registry.start(batch_id)
        if checkpoint == "PUBLISH_READY":
            registry.prepare_publication(batch_id, old_attempt, current_accepted=[observation()])
    with BatchRegistry(path) as registry:
        assert registry.batch(batch_id)["status"] == "RUNNING"
        with pytest.raises(BatchStateError):
            registry.start(batch_id)
        with pytest.raises(BatchStateError):
            registry.recover_interrupted(batch_id, expected_attempt_id="wrong-owner")
        registry.recover_interrupted(batch_id, expected_attempt_id=old_attempt)
        assert registry.batch(batch_id)["checkpoint"] == checkpoint
        attempt = registry.start(batch_id)
        assert attempt != old_attempt
        for operation in (
            lambda: registry.fail(batch_id, old_attempt, error_code="OLD_WORKER"),
            lambda: registry.complete_offline(batch_id, old_attempt, current_accepted=[observation()]),
            lambda: registry.prepare_publication(batch_id, old_attempt, current_accepted=[observation()]),
        ):
            with pytest.raises(BatchStateError, match="stale"):
                operation()
        registry.prepare_publication(batch_id, attempt, current_accepted=[observation()])
        registry.complete_offline(batch_id, attempt, current_accepted=[observation()])
        assert registry.db.execute("SELECT outcome FROM attempts WHERE attempt_id=?", (old_attempt,)).fetchone()[0] == "INTERRUPTED"


def test_retry_keeps_approvals_and_does_not_duplicate_records(tmp_path):
    with BatchRegistry(tmp_path / "b.sqlite3") as registry:
        batch_id = revision_batch(registry)
        revision = registry.revisions(batch_id)[0]
        decide(registry, revision)
        attempt = registry.start(batch_id)
        registry.fail(batch_id, attempt, error_code="SIMULATED_IO_FAILURE")
        assert revision_batch(registry) == batch_id
        retry = registry.start(batch_id)
        assert retry != attempt
        assert len(registry.revisions(batch_id)) == len(registry.decisions(revision["revision_id"])) == 1
        assert len(registry.prepare_publication(batch_id, retry, current_accepted=[observation()])) == 2
        assert registry.history(batch_id)[-3]["attempt_id"] == attempt


def test_completed_is_terminal_and_lost_ack_retry_is_noop(tmp_path):
    path = tmp_path / "b.sqlite3"
    with BatchRegistry(path) as registry:
        batch_id = register(registry)
        attempt = registry.start(batch_id)
        with pytest.raises(BatchStateError, match="Prepare"):
            registry.complete_offline(batch_id, attempt, current_accepted=[observation()])
        registry.prepare_publication(batch_id, attempt, current_accepted=[observation()])
        registry.complete_offline(batch_id, attempt, current_accepted=[observation()])
        completed = registry.batch(batch_id)
    with BatchRegistry(path) as registry:
        assert register(registry) == batch_id
        assert registry.start(batch_id) is None
        with pytest.raises(BatchStateError):
            registry.fail(batch_id, attempt, error_code="LATE_ERROR")
        with pytest.raises(BatchStateError):
            registry.recover_interrupted(batch_id, expected_attempt_id=attempt)
        assert registry.batch(batch_id) == completed


def test_stale_accepted_state_blocks_prepare_and_completion(tmp_path):
    with BatchRegistry(tmp_path / "b.sqlite3") as registry:
        batch_id = revision_batch(registry)
        decide(registry, registry.revisions(batch_id)[0])
        attempt = registry.start(batch_id)
        changed = [observation(**{METRIC_COLUMNS[0]: "101"})]
        with pytest.raises(BatchStateError, match="Accepted state changed"):
            registry.prepare_publication(batch_id, attempt, current_accepted=changed)
        registry.prepare_publication(batch_id, attempt, current_accepted=[observation()])
        with pytest.raises(BatchStateError, match="Accepted state changed"):
            registry.complete_offline(batch_id, attempt, current_accepted=changed)
        assert registry.batch(batch_id)["status"] == "RUNNING"


def test_decisions_frozen_during_run_and_after_completion(tmp_path):
    with BatchRegistry(tmp_path / "b.sqlite3") as registry:
        batch_id = revision_batch(registry)
        revision = registry.revisions(batch_id)[0]
        decide(registry, revision)
        attempt = registry.start(batch_id)
        with pytest.raises(BatchStateError, match="frozen"):
            decide(registry, revision, "REJECT")
        registry.prepare_publication(batch_id, attempt, current_accepted=[observation()])
        registry.complete_offline(batch_id, attempt, current_accepted=[observation()])
        with pytest.raises(BatchStateError, match="frozen"):
            decide(registry, revision, "REJECT")


@pytest.mark.parametrize("incoming", [
    [observation(3), observation(3, **{METRIC_COLUMNS[0]: "101"})],
    [observation(4)],
])
def test_conflicts_and_calendar_gaps_cannot_be_retried_into_publication(tmp_path, incoming):
    with BatchRegistry(tmp_path / "b.sqlite3") as registry:
        batch_id = register(registry, incoming)
        assert registry.batch(batch_id)["status"] == "FAILED"
        with pytest.raises(BatchStateError, match="Classification rejected"):
            registry.start(batch_id)


def test_unchanged_and_identical_duplicate_rows_produce_no_changes(tmp_path):
    with BatchRegistry(tmp_path / "b.sqlite3") as registry:
        batch_id = register(registry, [observation(), observation()], input_mode="FULL_SNAPSHOT")
        assert json.loads(registry.batch(batch_id)["plan_json"])["identical_duplicate_count"] == 1
        attempt = registry.start(batch_id)
        assert registry.prepare_publication(batch_id, attempt, current_accepted=[observation()]) == ()


def test_concurrent_registration_and_claim_have_one_winner(tmp_path):
    path = tmp_path / "b.sqlite3"
    with BatchRegistry(path):
        pass
    def worker(_):
        with BatchRegistry(path) as registry:
            batch_id = register(registry)
            try:
                attempt = registry.start(batch_id)
            except BatchStateError:
                attempt = None
            return batch_id, attempt
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(worker, range(2)))
    assert len({result[0] for result in results}) == 1
    assert sum(result[1] is not None for result in results) == 1


def test_no_environment_secrets_are_captured(tmp_path, monkeypatch):
    monkeypatch.setenv("SNOWFLAKE_PASSWORD", "SENTINEL_DO_NOT_STORE_123")
    with BatchRegistry(tmp_path / "b.sqlite3") as registry:
        batch_id = register(registry)
        attempt = registry.start(batch_id)
        with pytest.raises(ValueError, match="error code"):
            registry.fail(batch_id, attempt, error_code="password=SENTINEL_DO_NOT_STORE_123")
        registry.fail(batch_id, attempt, error_code="IO_FAILURE")
        assert "SENTINEL_DO_NOT_STORE_123" not in "\n".join(registry.db.iterdump())


def test_other_sqlite_database_is_not_overwritten(tmp_path):
    path = tmp_path / "unrelated.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE unrelated (value TEXT)")
    with pytest.raises(BatchStateError, match="incompatible"):
        BatchRegistry(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [("unrelated",)]


def test_transaction_failure_rolls_back_registration(tmp_path):
    with BatchRegistry(tmp_path / "b.sqlite3") as registry:
        registry.db.execute("""CREATE TRIGGER fail_event BEFORE INSERT ON events
            BEGIN SELECT RAISE(ABORT, 'synthetic disk failure'); END""")
        with pytest.raises(sqlite3.IntegrityError, match="synthetic disk failure"):
            revision_batch(registry)
        assert registry.db.execute("SELECT COUNT(*) FROM batches").fetchone()[0] == 0
        assert registry.db.execute("SELECT COUNT(*) FROM revisions").fetchone()[0] == 0
        registry.db.execute("DROP TRIGGER fail_event")
        assert registry.batch(revision_batch(registry))["status"] == "WAITING_APPROVAL"


@pytest.mark.parametrize("before,after", [(None, "0"), ("0", None), (None, None),
    ("123456789012345678901234567890.12345678901234567890",
     "123456789012345678901234567890.12345678901234567891")])
def test_decimal_precision_and_null_transitions_survive_restart(tmp_path, before, after):
    path = tmp_path / "b.sqlite3"
    accepted = [observation(**{METRIC_COLUMNS[0]: before})]
    incoming = [observation(**{METRIC_COLUMNS[0]: after})]
    with BatchRegistry(path) as registry:
        batch_id = register(registry, incoming, accepted)
    with BatchRegistry(path) as registry:
        if before != after:
            revision = registry.revisions(batch_id)[0]
            decide(registry, revision)
        else:
            assert registry.revisions(batch_id) == []
        attempt = registry.start(batch_id)
        changes = registry.prepare_publication(batch_id, attempt, current_accepted=accepted)
        if before != after:
            from decimal import Decimal
            assert changes[0].metrics[0] == (Decimal(after) if after is not None else None)
        else:
            assert changes == ()


def test_duplicate_current_keys_are_not_accepted_as_freshness_evidence(tmp_path):
    with BatchRegistry(tmp_path / "b.sqlite3") as registry:
        batch_id = register(registry)
        attempt = registry.start(batch_id)
        with pytest.raises(BatchStateError, match="duplicate periods"):
            registry.prepare_publication(batch_id, attempt, current_accepted=[observation(), observation()])


def test_scope_rejection_cannot_be_overridden_by_revision_approval(tmp_path):
    with BatchRegistry(tmp_path / "b.sqlite3") as registry:
        batch_id = register(registry, [observation(**{METRIC_COLUMNS[0]: None})],
                            declared_periods=[(2026, 2), (2026, 3)])
        decide(registry, registry.revisions(batch_id)[0])
        assert registry.batch(batch_id)["status"] == "FAILED"
        with pytest.raises(BatchStateError, match="Classification rejected"):
            registry.start(batch_id)


def test_registry_workflow_uses_no_network_or_snowflake_connector(tmp_path, monkeypatch):
    import socket
    from telecom_pipeline import snowflake_config
    def forbidden(*args, **kwargs):
        pytest.fail("Offline control workflow attempted a connection")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(snowflake_config, "connect", forbidden)
    with BatchRegistry(tmp_path / "b.sqlite3") as registry:
        batch_id = register(registry)
        attempt = registry.start(batch_id)
        registry.prepare_publication(batch_id, attempt, current_accepted=[observation()])
        registry.complete_offline(batch_id, attempt, current_accepted=[observation()])


def test_atomic_approval_failure_retains_pending_state(tmp_path):
    with BatchRegistry(tmp_path / "b.sqlite3") as registry:
        batch_id = revision_batch(registry)
        revision = registry.revisions(batch_id)[0]
        registry.db.execute("""CREATE TRIGGER fail_decision_event BEFORE INSERT ON events
            BEGIN SELECT RAISE(ABORT, 'synthetic audit failure'); END""")
        with pytest.raises(sqlite3.IntegrityError, match="synthetic audit failure"):
            decide(registry, revision)
        assert registry.decisions(revision["revision_id"]) == []
        assert registry.batch(batch_id)["status"] == "WAITING_APPROVAL"
