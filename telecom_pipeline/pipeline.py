"""Phase 2 local ingestion CLI with batch history and JSON event logs."""
import argparse
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sys
import uuid

from .readers import read_source
from .store import connect, persist
from .validation import validate


def now():
    return datetime.now(timezone.utc).isoformat()


def event(logger, name, **fields):
    level = logging.ERROR if name == "batch_failed" else logging.INFO
    logger.log(level, json.dumps({"timestamp": now(), "event": name, **fields}, ensure_ascii=True))


def run(source, *, state_dir="data/ingestion", encoding="utf-8-sig", sheet=0, logger=None):
    source, state_dir = Path(source).resolve(), Path(state_dir).resolve()
    if state_dir == source.parent or source.parent in state_dir.parents:
        raise ValueError("State directory must be outside the source directory")
    state_dir.mkdir(parents=True, exist_ok=True)
    logger = logger or logging.getLogger("telecom_pipeline")
    connection = connect(state_dir / "ingestion.sqlite3")
    batch_id, started = str(uuid.uuid4()), now()
    try:
        with connection:
            connection.execute("INSERT INTO batches(batch_id, source, started_at, status) VALUES (?, ?, ?, 'RUNNING')",
                               (batch_id, str(source), started))
        event(logger, "batch_started", batch_id=batch_id, source=str(source))
        result = read_source(source, encoding=encoding, sheet=sheet)
        with connection:
            connection.execute("UPDATE batches SET source_sha256=? WHERE batch_id=?",
                               (result.inspection["sha256"], batch_id))
        records, quality = validate(result.data)
        warnings = []
        if result.inspection.get("record_count_matches_metadata") is False:
            warnings.append("Declared record count differs from parsed data rows")
        report = {"batch_id": batch_id, "source": str(source), "started_at": started,
                  "source_sha256": result.inspection["sha256"], "inspection": result.inspection,
                  "quality": quality, "warnings": warnings}
        with connection:
            inserted = persist(connection, batch_id, records, started)
            report.update(status="SUCCEEDED", inserted_rows=inserted,
                          existing_rows=len(records) - inserted, finished_at=now())
            connection.execute("UPDATE batches SET status=?, finished_at=?, source_sha256=?, report_json=? WHERE batch_id=?",
                               (report["status"], report["finished_at"], report["source_sha256"], json.dumps(report), batch_id))
        event(logger, "batch_succeeded", **report)
        return report
    except Exception as exc:
        with connection:
            connection.execute("UPDATE batches SET status='FAILED', finished_at=?, error=? WHERE batch_id=?",
                               (now(), f"{type(exc).__name__}: {exc}", batch_id))
        event(logger, "batch_failed", batch_id=batch_id, error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--state-dir", type=Path, default=Path("data/ingestion"))
    parser.add_argument("--encoding", default="utf-8-sig")
    parser.add_argument("--sheet", default=0, help="Excel worksheet name (default: first worksheet)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    try:
        report = run(args.source, state_dir=args.state_dir, encoding=args.encoding, sheet=args.sheet)
    except Exception as exc:
        print(json.dumps({"status": "FAILED", "error_type": type(exc).__name__, "error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
