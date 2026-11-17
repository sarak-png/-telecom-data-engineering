"""Offline plans and the historical RAW/OPS executor. See docs/warehouse-contract.md."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import logging
import os
from pathlib import Path
import sys
import uuid

import pandas as pd

from .pipeline import event, now
from .readers import read_source
from .snowflake_config import DEFAULT_DATABASE, WAREHOUSE_CONTRACT, connect, identifier, project_database
from .validation import COLUMNS, validate


SQL_DIR = Path(__file__).resolve().parents[1] / "sql"
# Historical executor layouts. Adapter work must precede targeting the current objects.
RAW_COLUMNS = ("RECORD_HASH", "SOURCE_SHA256", "SOURCE_ROW_NUMBER", "FIRST_BATCH_ID", "LOADED_AT", *COLUMNS)
OPS_COLUMNS = ("BATCH_ID", "SOURCE_SHA256", "STARTED_AT", "FINISHED_AT", "STATUS",
               "EXPECTED_ROWS", "INSERTED_ROWS", "VERIFIED_ROWS", "REPORT_JSON", "ERROR_TYPE")


def render_sql(filename, database):
    return (SQL_DIR / filename).read_text(encoding="utf-8").replace("{{DATABASE}}", project_database(database))


def prepare(source, *, encoding="utf-8-sig", sheet=0):
    result = read_source(Path(source), encoding=encoding, sheet=sheet)
    _, quality = validate(result.data)
    batch_id, timestamp = str(uuid.uuid4()), now()
    source_hash = result.inspection["sha256"]
    records = []
    for number, row in enumerate(result.data.itertuples(index=False, name=None), 1):
        records.append({
            "record_hash": hashlib.sha256(f"{source_hash}:{number}".encode()).hexdigest(),
            "source_sha256": source_hash, "row_number": number,
            "batch_id": batch_id, "loaded_at": timestamp,
            "values": [None if pd.isna(value) else str(value) for value in row],
        })
    return {"batch_id": batch_id, "started_at": timestamp, "source_sha256": source_hash,
            "expected_rows": len(records), "quality": quality, "inspection": result.inspection}, records


def verify_rows(actual, records):
    expected = {r["record_hash"]: (r["source_sha256"], r["row_number"], *r["values"]) for r in records}
    if len(actual) != len(expected) or len({r[0] for r in actual}) != len(actual):
        raise ValueError("RAW row count or record uniqueness verification failed")
    if {row[0]: tuple(row[1:]) for row in actual} != expected:
        raise ValueError("RAW source-row identity or exact cell-value verification failed")


@contextmanager
def local_writer_lock(directory, database):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{identifier(database)}.snowflake.lock"
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError("Another loader holds the local writer lock; see recovery documentation") from exc
    try:
        os.write(descriptor, str(os.getpid()).encode())
        yield
    finally:
        os.close(descriptor)
        path.unlink()


def load(prepared, records, *, database, approved=False, connector=connect):
    """No DDL. Serialized, insert-only RAW loading plus OPS audit updates."""
    if not approved:
        raise PermissionError("Cloud SQL execution requires prior user approval")
    database = project_database(database)
    if not records or prepared["expected_rows"] != len(records):
        raise ValueError("Invalid prepared batch")
    logger = logging.getLogger("telecom_pipeline.snowflake")
    report = dict(prepared)
    connection = connector()
    cursor = None
    registered = False
    committing = False
    try:
        cursor = connection.cursor()
        for schema, table, expected in (("RAW", "TRA_TRAFFIC", RAW_COLUMNS),
                                         ("OPS", "INGESTION_BATCHES", OPS_COLUMNS)):
            cursor.execute(f"SELECT * FROM {database}.{schema}.{table} LIMIT 0")
            if tuple(column[0] for column in cursor.description) != expected:
                raise ValueError(f"Incompatible {schema}.{table} column layout; refusing writes")
        event(logger, "snowflake_batch_started", batch_id=report["batch_id"])
        cursor.execute(f"INSERT INTO {database}.OPS.INGESTION_BATCHES "
                       "(BATCH_ID,SOURCE_SHA256,STARTED_AT,STATUS,EXPECTED_ROWS) VALUES (%s,%s,%s,'RUNNING',%s)",
                       (report["batch_id"], report["source_sha256"], report["started_at"], len(records)))
        registered = True
        cursor.execute("BEGIN")
        cursor.execute(f"SELECT COUNT(*) FROM {database}.RAW.TRA_TRAFFIC WHERE SOURCE_SHA256=%s",
                       (report["source_sha256"],))
        before = cursor.fetchone()[0]
        cursor.execute(render_sql("002_merge_raw.sql", database), (json.dumps(records),))
        names = ', '.join(f'"{name}"' for name in COLUMNS)
        cursor.execute(f"SELECT RECORD_HASH,SOURCE_SHA256,SOURCE_ROW_NUMBER,{names} "
                       f"FROM {database}.RAW.TRA_TRAFFIC WHERE SOURCE_SHA256=%s",
                       (report["source_sha256"],))
        actual = cursor.fetchall()
        verify_rows(actual, records)
        report.update(status="SUCCEEDED", inserted_rows=len(actual) - before,
                      verified_rows=len(actual), finished_at=now())
        cursor.execute(f"UPDATE {database}.OPS.INGESTION_BATCHES SET STATUS='SUCCEEDED', "
                       "FINISHED_AT=%s,INSERTED_ROWS=%s,VERIFIED_ROWS=%s,REPORT_JSON=%s WHERE BATCH_ID=%s",
                       (report["finished_at"], report["inserted_rows"], len(actual), json.dumps(report), report["batch_id"]))
        committing = True
        connection.commit()
        committing = False
        event(logger, "snowflake_batch_succeeded", batch_id=report["batch_id"],
              inserted_rows=report["inserted_rows"], verified_rows=len(actual))
        return report
    except Exception as exc:
        # Connection/auth errors are never serialized; they can contain sensitive details.
        try:
            connection.rollback()
            if registered:
                status = "UNKNOWN" if committing else "FAILED"
                cursor.execute(f"UPDATE {database}.OPS.INGESTION_BATCHES SET STATUS=%s,FINISHED_AT=%s,ERROR_TYPE=%s "
                               "WHERE BATCH_ID=%s", (status, now(), type(exc).__name__, report["batch_id"]))
        except Exception:
            event(logger, "snowflake_audit_update_failed", batch_id=report["batch_id"])
        event(logger, "batch_failed", batch_id=report["batch_id"], error_type=type(exc).__name__)
        raise
    finally:
        try:
            if cursor is not None:
                cursor.close()
        finally:
            connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?", default="data/raw/tra-open-data-traffic.csv")
    parser.add_argument("--database", default=os.environ.get("SNOWFLAKE_DATABASE", DEFAULT_DATABASE))
    parser.add_argument("--render-sql", choices=("001_bootstrap.sql", "002_merge_raw.sql", "003_monitoring.sql",
                                                "004_staging_preview.sql", "005_mart_preview.sql"))
    parser.add_argument("--execute-approved", action="store_true", help="Only after explicit approval of cloud SQL")
    parser.add_argument("--encoding", default="utf-8-sig")
    parser.add_argument("--sheet", default=0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    try:
        database = project_database(args.database)
        if args.render_sql:
            print(render_sql(args.render_sql, database))
            return 0
        report, records = prepare(args.source, encoding=args.encoding, sheet=args.sheet)
        if args.execute_approved:
            # Match CLI target to the authenticated connection database.
            from .snowflake_config import connection_options
            if connection_options()["database"] != database:
                raise ValueError("CLI database and connection database differ")
            with local_writer_lock("data/ingestion", database):
                report = load(report, records, database=database, approved=True)
        else:
            report.update(status="OFFLINE_PLAN", cloud_executed=False)
            report["warehouse_contract"] = {
                "version": WAREHOUSE_CONTRACT["contract_version"],
                "confirmed_objects": {
                    key: f"{DEFAULT_DATABASE}.{value['schema']}.{value['name']}"
                    for key, value in WAREHOUSE_CONTRACT["objects"].items()
                },
                "executor_status": "legacy_raw_ops_adapter_requires_schema_and_behavior_changes",
            }
        print(json.dumps(report, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "FAILED", "error_type": type(exc).__name__}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
