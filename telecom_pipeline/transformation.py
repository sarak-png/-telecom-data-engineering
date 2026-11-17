"""Local typed transformations; this module never imports a Snowflake connector."""
import argparse
from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import pandas as pd

from .readers import read_source
from .validation import COLUMNS, ValidationError, validate


METRICS = {column: column.upper().replace(" ", "_") for column in COLUMNS[2:]}
VERSION = "local-transform-v1"


def warehouse_decimal(value):
    if value is None:
        return None
    number = Decimal(value)
    _, digits, exponent = number.as_tuple()
    if max(-exponent, 0) > 6 or max(len(digits) + exponent, 0) > 32:
        raise ValidationError("Metric would lose precision or overflow NUMBER(38,6)")
    return number


def transform(frame, source_sha256):
    """Deduplicate within one validated snapshot; never combine different snapshots."""
    records, quality = validate(frame)
    positions = {}
    for ordinal, row in enumerate(frame.itertuples(index=False, name=None), 1):
        key = tuple(int(Decimal(str(value).strip().replace(",", ""))) for value in row[:2])
        positions.setdefault(key, ordinal)
    staging, mart = [], []
    for record in records:
        year, quarter = int(record["Year"]), int(record["Quarter"])
        row_hash = hashlib.sha256(f"{source_sha256}:{positions[(year, quarter)]}".encode()).hexdigest()
        values = {target: warehouse_decimal(record[source]) for source, target in METRICS.items()}
        staging.append({"RECORD_HASH": row_hash, "SOURCE_SHA256": source_sha256,
                        "YEAR": year, "QUARTER": quarter, **values})
        mart.append({"YEAR": year, "QUARTER": quarter,
                     "QUARTER_START_DATE": date(year, (quarter - 1) * 3 + 1, 1),
                     **values, "SOURCE_SHA256": source_sha256})
    staging, mart = pd.DataFrame(staging), pd.DataFrame(mart)
    for table in (staging, mart):
        table["YEAR"] = table["YEAR"].astype("Int64")
        table["QUARTER"] = table["QUARTER"].astype("Int64")
    return staging, mart, quality


def build(source):
    result = read_source(Path(source))
    staging, mart, quality = transform(result.data, result.inspection["sha256"])
    return {"transform_version": VERSION, "source_sha256": result.inspection["sha256"],
            "quality": quality, "inspection": result.inspection,
            "staging": staging.to_dict(orient="records"), "mart": mart.to_dict(orient="records")}


def serialize(report):
    # Decimal strings preserve exact values; dates use ISO 8601.
    def encode(value):
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, date):
            return value.isoformat()
        raise TypeError(type(value).__name__)
    return json.dumps(report, default=encode, ensure_ascii=True, indent=2) + "\n"


def save(report, output, *, source):
    output, source = Path(output).resolve(), Path(source).resolve()
    if output == source or source.parent == output.parent or source.parent in output.parents:
        raise ValueError("Derived output must be outside the source directory")
    payload = serialize(report)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
    except FileExistsError:
        if output.read_text(encoding="utf-8") != payload:
            raise FileExistsError("Output exists with different content; choose a new path")
        return "REUSED"
    return "CREATED"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?", default="data/raw/tra-open-data-traffic.csv")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = build(args.source)
        output_status = save(report, args.output, source=args.source) if args.output else "NOT_REQUESTED"
        print(json.dumps({"status": "LOCAL_TRANSFORM_SUCCEEDED", "cloud_executed": False,
                          "source_rows": report["quality"]["rows_read"],
                          "staging_rows": len(report["staging"]), "mart_rows": len(report["mart"]),
                          "quality": report["quality"], "output_status": output_status,
                          "first_quarter": report["mart"][0]["QUARTER_START_DATE"].isoformat()}))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "LOCAL_TRANSFORM_FAILED", "error_type": type(exc).__name__, "error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
