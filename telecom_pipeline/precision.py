"""Opt-in offline binary64 assessment with exact source comparisons and evidence gates."""
import argparse
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
import json
import math
from pathlib import Path

from .incremental import COLUMN_MAPPING, METRIC_COLUMNS, CanonicalRecord
from .readers import read_source
from .snowflake_config import WAREHOUSE_CONTRACT


POLICY_VERSION = "OFFLINE_FLOAT_ASSESSMENT_V1"
_UNKNOWN = object()


@dataclass(frozen=True)
class DocumentedPrecision:
    """Caller-supplied metric-specific, approved source-grid evidence.

    No TRA precision rules are supplied by default. Test evidence must be labeled
    synthetic. References are audit assertions that require independent review.
    """
    metric: str
    decimal_places: int
    evidence_reference: str
    approved_by: str

    def __post_init__(self):
        if self.metric not in METRIC_COLUMNS:
            raise ValueError("Unknown telecom metric")
        if type(self.decimal_places) is not int or self.decimal_places < 0:
            raise ValueError("Documented decimal places must be a nonnegative integer")
        if not self.evidence_reference.strip() or not self.approved_by.strip():
            raise ValueError("Documented precision requires evidence and reviewer references")


def _decimal(metric, value):
    values = tuple(value if name == metric else None for name in METRIC_COLUMNS)
    return CanonicalRecord(1, 1, values).metrics[METRIC_COLUMNS.index(metric)]


def _grid_value(number, rule):
    """Recover a unique nonnegative grid point strictly inside the float cell.

    Exact midpoint boundaries remain unresolved. Fraction arithmetic makes this
    independent of the active Decimal context and introduces no ULP threshold.
    """
    current = Fraction.from_float(number)
    previous = Fraction.from_float(math.nextafter(number, -math.inf))
    following_float = math.nextafter(number, math.inf)
    following = (Fraction.from_float(following_float) if math.isfinite(following_float)
                 else current + (current - previous))
    lower, upper = (previous + current) / 2, (current + following) / 2
    multiplier = 10 ** rule.decimal_places
    if (lower * multiplier).denominator == 1 or (upper * multiplier).denominator == 1:
        return None
    first = max(0, math.floor(lower * multiplier) + 1)
    last = math.ceil(upper * multiplier) - 1
    if first != last:
        return None
    candidate = Decimal((0, tuple(int(digit) for digit in str(first)), -rule.decimal_places))
    # Exclude boundary cases and verify Python's actual binary64 projection.
    return candidate if float(candidate) == number else None


def assess_metric(metric, incoming, observed_float, *, accepted_source=_UNKNOWN, precision=None):
    """Assess one metric without changing classifier hashes or approval state.

    Source changes use exact accepted Decimal evidence when supplied. Otherwise,
    numeric source equality stays unresolved until a documented grid identifies
    a unique candidate. NULL transitions are always kept distinct from zero.
    """
    if metric not in METRIC_COLUMNS:
        raise ValueError("Unknown telecom metric")
    if observed_float is not None and (
            type(observed_float) is not float or not math.isfinite(observed_float) or observed_float < 0):
        raise ValueError("Observed metric requires a finite nonnegative Python float or None")
    incoming = _decimal(metric, incoming)
    if precision is not None and precision.metric != metric:
        raise ValueError("Precision evidence belongs to a different metric")
    accepted_known = accepted_source is not _UNKNOWN
    accepted = _decimal(metric, accepted_source) if accepted_known else None
    if precision is not None:
        for value in (incoming, accepted):
            if value is not None and (Fraction(value) * 10 ** precision.decimal_places).denominator != 1:
                raise ValueError("Source value violates the documented precision grid")
    recovered = None
    if not accepted_known and precision is not None and observed_float is not None:
        recovered = _grid_value(observed_float, precision)
        if recovered is not None:
            accepted, accepted_known = recovered, True
    if accepted_known:
        source_change = "UNCHANGED" if incoming == accepted else "REVISED"
    elif incoming is None and observed_float is None:
        source_change = "UNCHANGED"
    elif (incoming is None) != (observed_float is None):
        source_change = "REVISED"
    else:
        source_change = "UNRESOLVED"

    reference = accepted if accepted_known else incoming
    exact_binary = Decimal.from_float(observed_float) if observed_float is not None else None
    projection = None
    if reference is None or observed_float is None:
        readback = "NULL_MATCH" if reference is None and observed_float is None else "NULL_MISMATCH"
    else:
        projection = float(reference)
        if not math.isfinite(projection):
            readback = "OVERFLOW"
        elif reference != 0 and projection == 0:
            readback = "UNDERFLOW"
        else:
            readback = "MATCH" if projection == observed_float else "MISMATCH"
    ulp_distance = None
    if projection is not None and math.isfinite(projection) and observed_float is not None:
        distance = abs(Fraction.from_float(projection) - Fraction.from_float(observed_float))
        ulp_distance = str(distance / Fraction.from_float(math.ulp(projection)))
    return {
        "policy_version": POLICY_VERSION, "metric": metric,
        "source_change": source_change,
        "accepted_evidence": "CANONICAL_SOURCE" if accepted_source is not _UNKNOWN else
                             "DOCUMENTED_GRID" if recovered is not None else "UNAVAILABLE",
        "documented_decimal_places": precision.decimal_places if precision else None,
        "precision_evidence": precision.evidence_reference if precision else None,
        "precision_approved_by": precision.approved_by if precision else None,
        "recovered_grid_value": str(recovered) if recovered is not None else None,
        "observed_shortest_decimal": str(observed_float) if observed_float is not None else None,
        "observed_exact_binary_decimal": str(exact_binary) if exact_binary is not None else None,
        "reference_exactly_representable": reference == exact_binary if reference is not None and exact_binary is not None else None,
        "projection_reference": "ACCEPTED_SOURCE" if accepted_known else "INCOMING_DIAGNOSTIC",
        "readback": readback, "ulp_distance_from_projection": ulp_distance,
        "requires_review": source_change != "UNCHANGED" or readback not in ("MATCH", "NULL_MATCH"),
        "publication_allowed": False,
    }


def profile_source(path):
    """Report observed lexical scales; never infer authoritative precision."""
    source = read_source(Path(path))
    frame = source.data
    if tuple(frame.columns) != tuple(raw for raw, _ in COLUMN_MAPPING):
        raise ValueError("Precision profiling requires the nine contract RAW columns")
    metrics = {}
    for raw, metric in COLUMN_MAPPING[2:]:
        counts = Counter()
        nulls = 0
        for value in frame[raw]:
            parsed = _decimal(metric, value)
            if parsed is None:
                nulls += 1
            else:
                text = str(value).strip()
                places = len(text.split(".")[1]) if "." in text else 0
                counts[places] += 1
        metrics[metric] = {
            "source_column": raw, "warehouse_type": next(column["type"] for column in
                WAREHOUSE_CONTRACT["objects"]["quarterly_fact"]["columns"] if column["name"] == metric),
            "observed_decimal_places": dict(sorted(counts.items())), "null_count": nulls,
            "documented_decimal_places": None, "precision_evidence": None,
        }
    return {"policy_version": POLICY_VERSION, "source_sha256": source.inspection["sha256"],
            "row_count": len(frame), "metrics": metrics, "publication_allowed": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Profile source decimal formatting offline")
    parser.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(profile_source(args.path), sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
