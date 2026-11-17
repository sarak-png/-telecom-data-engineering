"""User-run SELECT-only validation of original decimal values against fact FLOATs."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys

from .incremental import COLUMN_MAPPING, METRIC_COLUMNS, canonicalize_record
from .precision import POLICY_VERSION, assess_metric
from .readers import read_source
from .snowflake_config import WAREHOUSE_CONTRACT
from .snowflake_readonly import TABLE, ReadOnlyError, fetch_quarterly_records


_FAILURES = {"NULL_MISMATCH", "UNEXPLAINED_FLOAT_MISMATCH", "OVERFLOW", "UNDERFLOW", "MISSING_PERIOD"}


def compare_snapshot(source, snapshot):
    """Pure report building; source is a reader result, snapshot an adapter result."""
    raw_names = tuple(raw for raw, _ in COLUMN_MAPPING)
    if tuple(source.data.columns) != raw_names:
        raise ValueError("Expected the original nine-column RAW CSV schema")
    source_rows = []
    source_keys = set()
    for raw in source.data.to_dict(orient="records"):
        record = canonicalize_record(raw)
        if record.key in source_keys:
            raise ValueError("Duplicate source period")
        source_keys.add(record.key)
        source_rows.append((record, raw))
    warehouse = {record.key: record for record in snapshot.records}
    if len(warehouse) != len(snapshot.records):
        raise ValueError("Duplicate warehouse period")
    source_rows.sort(key=lambda pair: pair[0].key)
    metrics, total, differences = {}, Counter(), []
    for index, (raw_name, metric) in enumerate(COLUMN_MAPPING[2:]):
        checks, counts = [], Counter()
        for record, raw in source_rows:
            value = record.metrics[index]
            sample = {"year": record.year, "quarter": record.quarter, "metric": metric,
                      "source_decimal_text": str(raw[raw_name]) if value is not None else None}
            if record.key not in warehouse:
                check = {**sample, "result": "MISSING_PERIOD", "source_is_zero": value == 0,
                         "observed_shortest_decimal": None, "observed_exact_binary_decimal": None}
            else:
                transported = warehouse[record.key].metrics[index]
                # The adapter uses Decimal(str(native_float)). Converting that
                # shortest round-trip string back recovers the native binary64.
                observed = float(transported) if transported is not None else None
                assessment = assess_metric(metric, value, observed)
                readback = assessment["readback"]
                if readback == "MATCH":
                    result = ("EXACT_NUMERIC_MATCH" if assessment["reference_exactly_representable"]
                              else "EXPECTED_BINARY_REPRESENTATION")
                elif readback == "MISMATCH":
                    result = "UNEXPLAINED_FLOAT_MISMATCH"
                else:
                    result = readback
                check = {**sample, "result": result, "source_is_zero": value == 0,
                         "observed_shortest_decimal": assessment["observed_shortest_decimal"],
                         "observed_exact_binary_decimal": assessment["observed_exact_binary_decimal"],
                         "source_equals_shortest_decimal": value == transported,
                         "ulp_distance_from_projection": assessment["ulp_distance_from_projection"]}
            checks.append(check)
            counts[check["result"]] += 1
            if check["result"] in _FAILURES:
                differences.append(check)
        # First occurrence of each result category plus zero and source min/max.
        selected = {}
        for check in checks:
            selected.setdefault(check["result"], check)
            if check["source_is_zero"]:
                selected.setdefault("ZERO", check)
        numeric = [(record.metrics[index], record.key) for record, _ in source_rows
                   if record.metrics[index] is not None]
        for label, candidate in (("MINIMUM", min(numeric, default=None)),
                                 ("MAXIMUM", max(numeric, default=None))):
            if candidate is not None:
                selected[label] = next(check for check in checks if (check["year"], check["quarter"]) == candidate[1])
        samples = {}
        for reason, check in selected.items():
            key = (check["year"], check["quarter"])
            samples.setdefault(key, {**check, "selection_reasons": []})["selection_reasons"].append(reason)
        metrics[metric] = {"counts": dict(sorted(counts.items())),
                           "source_nulls": sum(record.metrics[index] is None for record, _ in source_rows),
                           "source_zeros": sum(record.metrics[index] == 0 for record, _ in source_rows),
                           "samples": [samples[key] for key in sorted(samples)]}
        total.update(counts)
    return {
        "workflow": "READ_ONLY_FLOAT_CONVERSION_VALIDATION", "publication_allowed": False,
        "status": "REQUIRES_REVIEW" if differences else "PROJECTION_CHECKS_PASSED",
        "policy_version": POLICY_VERSION, "schema_version": WAREHOUSE_CONTRACT["contract_version"],
        "warehouse_table": TABLE, "source_sha256": source.inspection["sha256"],
        "source_is_historical_snapshot": source.inspection["sha256"] ==
            WAREHOUSE_CONTRACT["historical_baseline"]["source_sha256"],
        "source_periods": len(source_rows), "warehouse_periods": len(warehouse),
        "metric_checks": len(source_rows) * len(METRIC_COLUMNS),
        "read_started_at": snapshot.started_at, "read_finished_at": snapshot.finished_at,
        "warehouse_snapshot_sha256": snapshot.sha256,
        "extra_warehouse_periods": [list(key) for key in sorted(set(warehouse) - source_keys)],
        "counts": dict(sorted(total.items())), "metrics": metrics,
        "differences_requiring_review": differences,
        "interpretation": "Projection matches agree with Python binary64 conversion of the CSV value. "
                          "They do not establish historical source identity or the original loading path.",
    }


def validate_conversion(path, *, connector=None, allow_live=False, environ=None):
    if connector is None and not allow_live:
        raise ReadOnlyError("LIVE_READ_OPT_IN_REQUIRED")
    environment = os.environ if environ is None else environ
    if environment.get("SNOWFLAKE_ROLE", "").strip().upper() != "TELECOM_READONLY":
        raise ReadOnlyError("TELECOM_READONLY_ROLE_REQUIRED")
    source = read_source(Path(path))
    if tuple(source.data.columns) != tuple(raw for raw, _ in COLUMN_MAPPING):
        raise ValueError("Expected the original nine-column RAW CSV schema")
    # Validate source keys, schema and numbers before connecting.
    records = [canonicalize_record(row) for row in source.data.to_dict(orient="records")]
    if len({row.key for row in records}) != len(records):
        raise ValueError("Duplicate source period")
    snapshot = fetch_quarterly_records(connector=connector, allow_live=allow_live, environ=environment)
    return compare_snapshot(source, snapshot)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, default=Path("data/raw/tra-open-data-traffic.csv"))
    parser.add_argument("--live-read-only", action="store_true")
    parser.add_argument("--diagnostics", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = validate_conversion(args.path, allow_live=args.live_read_only)
        print(json.dumps(report, sort_keys=True, indent=2))
        return 2 if report["status"] == "REQUIRES_REVIEW" else 0
    except ReadOnlyError as error:
        result = {"status": "ERROR", "error_code": str(error), "publication_allowed": False}
        if args.diagnostics and error.diagnostics is not None:
            result["diagnostics"] = error.diagnostics
    except KeyboardInterrupt:
        result = {"status": "ERROR", "error_code": "INTERRUPTED", "publication_allowed": False}
    except (ValueError, LookupError):
        result = {"status": "ERROR", "error_code": "INVALID_SOURCE", "publication_allowed": False}
    except OSError:
        result = {"status": "ERROR", "error_code": "LOCAL_FILE_ERROR", "publication_allowed": False}
    except Exception:
        result = {"status": "ERROR", "error_code": "FLOAT_VALIDATION_FAILED", "publication_allowed": False}
    print(json.dumps(result), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
