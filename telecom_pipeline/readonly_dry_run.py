"""Diagnostic CSV versus Snowflake comparison; never approve or publish records."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

from .batch_control import BatchRegistry
from .incremental import InputMode, canonicalize_record, classify_records
from .readers import read_source
from .snowflake_config import WAREHOUSE_CONTRACT
from .snowflake_readonly import (
    COMPARISON_VERSION, TABLE, ReadOnlyError, fetch_quarterly_records,
)


def dry_run(path, *, input_mode, connector=None, allow_live=False, registry_path=None,
            encoding="utf-8-sig", declared_periods=None, environ=None):
    """Read immutable CSV bytes through existing reader, then one warehouse SELECT.

    Optional persistence registers only a diagnostic plan. No attempt is started,
    approved or completed. A warehouse snapshot hash isolates observation plans
    from other observations and exact-source offline approval contexts.
    """
    if connector is None and not allow_live:
        raise ReadOnlyError("LIVE_READ_OPT_IN_REQUIRED")
    mode = InputMode(input_mode)
    path = Path(path)
    if path.suffix.lower() != ".csv":
        raise ValueError("This dry-run entry point requires a CSV")
    source = read_source(path, encoding=encoding)
    incoming = tuple(canonicalize_record(row) for row in source.data.to_dict(orient="records"))
    # Validate the input and declared scope before any connection is attempted.
    declared = tuple(declared_periods) if declared_periods is not None else None
    classify_records(incoming, input_mode=mode, declared_periods=declared)
    snapshot = fetch_quarterly_records(connector=connector, allow_live=allow_live, environ=environ)
    plan = classify_records(incoming, snapshot.records, input_mode=mode, declared_periods=declared)
    report = {
        "workflow": "READ_ONLY_DRY_RUN", "warehouse_writes": False,
        "source_sha256": source.inspection["sha256"], "input_mode": mode.value,
        "schema_version": WAREHOUSE_CONTRACT["contract_version"], "source_rows": plan.input_row_count,
        "warehouse_table": TABLE, "warehouse_rows": len(snapshot.records),
        "read_started_at": snapshot.started_at, "read_finished_at": snapshot.finished_at,
        "warehouse_snapshot_sha256": snapshot.sha256,
        "comparison_representation": COMPARISON_VERSION,
        "comparison_note": "Decimal(str(FLOAT)); approximate warehouse values, exact decimal comparison, no tolerance. "
                           "UNCHANGED is representation equality, not proof of original source equality.",
        "classification_status": plan.status, "counts": plan.counts,
        "identical_duplicate_count": plan.identical_duplicate_count,
        "requires_approval": plan.requires_approval,
        "missing_accepted_periods": plan.missing_accepted_periods,
        "missing_declared_periods": plan.missing_declared_periods,
        "unexpected_periods": plan.unexpected_periods, "calendar_gaps": plan.calendar_gaps,
        "revisions": [{"year": row.key[0], "quarter": row.key[1], "changed_metrics": row.changed_metrics}
                      for row in plan.records if row.requires_approval],
        "batch_id": None, "registry_status": None,
    }
    if registry_path is not None:
        with BatchRegistry(registry_path) as registry:
            batch_id = registry.register(incoming, snapshot.records,
                source_sha256=source.inspection["sha256"], input_mode=mode, declared_periods=declared,
                policy_version=f"READ_ONLY_{COMPARISON_VERSION}:snapshot:{snapshot.sha256}")
            report.update(batch_id=batch_id, registry_status=registry.batch(batch_id)["status"])
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="Existing TRA or plain nine-column CSV")
    parser.add_argument("--input-mode", required=True, choices=[mode.value for mode in InputMode])
    parser.add_argument("--live-read-only", action="store_true", help="Explicitly permit one live SELECT")
    parser.add_argument("--registry", type=Path, help="Optional dedicated local dry-run SQLite registry")
    parser.add_argument("--encoding", default="utf-8-sig")
    parser.add_argument("--diagnostics", action="store_true",
                        help="Include sanitized failure stage and probable cause in error JSON")
    args = parser.parse_args(argv)
    if not args.live_read_only:
        print(json.dumps({"status": "ERROR", "error_code": "LIVE_READ_OPT_IN_REQUIRED"}), file=sys.stderr)
        return 2
    diagnostics = None
    try:
        report = dry_run(args.path, input_mode=args.input_mode, allow_live=True,
                         registry_path=args.registry, encoding=args.encoding)
        print(json.dumps(report, ensure_ascii=True, indent=2))
        return 2 if report["classification_status"].startswith("REJECTED_") else 0
    except KeyboardInterrupt:
        code = "INTERRUPTED"
    except ReadOnlyError as exc:
        code = str(exc)
        diagnostics = exc.diagnostics
    except (ValueError, LookupError):
        code = "INVALID_INPUT_OR_CONFIGURATION"
    except OSError:
        code = "LOCAL_FILE_ERROR"
    except sqlite3.Error:
        code = "REGISTRY_ERROR"
    except Exception:
        code = "DRY_RUN_FAILED"
    error_report = {"status": "ERROR", "error_code": code}
    if args.diagnostics and diagnostics is not None:
        error_report["diagnostics"] = diagnostics
    print(json.dumps(error_report), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
