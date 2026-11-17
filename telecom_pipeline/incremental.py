"""Offline quarterly-record classification against canonical accepted observations.

Call classify_records(incoming, accepted, input_mode="FULL_SNAPSHOT") or use
input_mode="QUARTER_DELTA". Inputs are iterables of nine-field RAW or STAGING
mappings, or CanonicalRecord instances. Use strings, integers, Decimal, None,
or pandas.NA. Binary floats are rejected because their original source precision
cannot be recovered for canonical change detection.

Plans are immutable, deterministic observations. READY means classification
passed, WAITING_APPROVAL holds the whole batch, and REJECTED_* reports conflicts
or scope issues. This module provides no publication or approval application API.
declared_periods optionally checks an exact input key set. When omitted, the
input keys supply the observed scope. Publication freshness and approval/audit
persistence belong to later integration.
"""
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
import hashlib
import json

from pandas import NA

from .snowflake_config import WAREHOUSE_CONTRACT
from .validation import NUMBER, ValidationError


DATASET_ID = "oman_tra_traffic"
CANONICALIZATION_VERSION = "1"
COLUMN_MAPPING = tuple((item["raw"], item["staging"]) for item in WAREHOUSE_CONTRACT["column_mapping"])
KEY_COLUMNS = ("YEAR", "QUARTER")
METRIC_COLUMNS = tuple(staging for _, staging in COLUMN_MAPPING[2:])
Period = tuple[int, int]


class InputMode(str, Enum):
    FULL_SNAPSHOT = "FULL_SNAPSHOT"
    QUARTER_DELTA = "QUARTER_DELTA"


class Classification(str, Enum):
    NEW = "NEW"
    UNCHANGED = "UNCHANGED"
    REVISED = "REVISED"
    CONFLICTING_DUPLICATE = "CONFLICTING_DUPLICATE"


def _number(value, column):
    if value is None or value is NA:
        return None
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, (str, int)) and not isinstance(value, bool):
        text = str(value).strip()
        if not text:
            return None
        if not NUMBER.fullmatch(text):
            raise ValidationError(f"{column} must be a finite nonnegative decimal")
        number = Decimal(text.replace(",", ""))
    else:
        raise ValidationError(f"{column} requires source text, int, or Decimal; binary floats are unsupported")
    if not number.is_finite() or number < 0:
        raise ValidationError(f"{column} must be a finite nonnegative decimal")
    return number


def _period(year, quarter):
    parsed = []
    for column, value, upper in (("YEAR", year, 9999), ("QUARTER", quarter, 4)):
        number = _number(value, column)
        if number is None or number != number.to_integral_value() or not 1 <= number <= upper:
            raise ValidationError(f"{column} must be a required integer between 1 and {upper}")
        parsed.append(int(number))
    return tuple(parsed)


def _decimal_text(value):
    if value is None:
        return None
    if value == 0:
        return "0"
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


@dataclass(frozen=True)
class CanonicalRecord:
    year: int
    quarter: int
    metrics: tuple[Decimal | None, ...]

    def __post_init__(self):
        year, quarter = _period(self.year, self.quarter)
        if len(self.metrics) != len(METRIC_COLUMNS):
            raise ValidationError("A quarterly observation requires all seven metrics")
        metrics = tuple(_number(value, name) for name, value in zip(METRIC_COLUMNS, self.metrics))
        object.__setattr__(self, "year", year)
        object.__setattr__(self, "quarter", quarter)
        object.__setattr__(self, "metrics", metrics)

    @property
    def key(self) -> Period:
        return self.year, self.quarter

    def as_dict(self):
        return {"YEAR": self.year, "QUARTER": self.quarter, **dict(zip(METRIC_COLUMNS, self.metrics))}

    @property
    def payload_hash(self):
        values = {"YEAR": self.year, "QUARTER": self.quarter}
        values.update({name: _decimal_text(value) for name, value in zip(METRIC_COLUMNS, self.metrics)})
        payload = {"dataset_id": DATASET_ID, "canonicalization_version": CANONICALIZATION_VERSION,
                   "record": values}
        text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonicalize_record(record: Mapping | CanonicalRecord) -> CanonicalRecord:
    if isinstance(record, CanonicalRecord):
        return record
    if not isinstance(record, Mapping):
        raise ValidationError("Each observation must be a mapping or CanonicalRecord")
    raw_names = tuple(raw for raw, _ in COLUMN_MAPPING)
    staging_names = tuple(staging for _, staging in COLUMN_MAPPING)
    if set(record) == set(raw_names):
        values = {staging: record[raw] for raw, staging in COLUMN_MAPPING}
    elif set(record) == set(staging_names):
        values = record
    else:
        raise ValidationError("An observation must contain exactly the nine contract RAW or STAGING columns")
    return CanonicalRecord(values["YEAR"], values["QUARTER"], tuple(values[name] for name in METRIC_COLUMNS))


@dataclass(frozen=True)
class ClassifiedRecord:
    key: Period
    classification: Classification
    before: CanonicalRecord | None
    candidates: tuple[CanonicalRecord, ...]
    source_ordinals: tuple[int, ...]
    changed_metrics: tuple[str, ...] = ()

    @property
    def after(self):
        return self.candidates[0] if len(self.candidates) == 1 else None

    @property
    def requires_approval(self):
        return self.classification == Classification.REVISED


@dataclass(frozen=True)
class ClassificationPlan:
    input_mode: InputMode
    records: tuple[ClassifiedRecord, ...]
    input_row_count: int
    identical_duplicate_count: int
    missing_accepted_periods: tuple[Period, ...]
    missing_declared_periods: tuple[Period, ...]
    unexpected_periods: tuple[Period, ...]
    calendar_gaps: tuple[Period, ...]
    declared_periods: tuple[Period, ...] | None

    @property
    def counts(self):
        return {kind.value: sum(record.classification == kind for record in self.records) for kind in Classification}

    @property
    def requires_approval(self):
        return any(record.requires_approval for record in self.records)

    @property
    def status(self):
        if self.counts[Classification.CONFLICTING_DUPLICATE.value]:
            return "REJECTED_CONFLICT"
        if self.missing_accepted_periods or self.missing_declared_periods or self.unexpected_periods or self.calendar_gaps:
            return "REJECTED_SCOPE"
        if self.requires_approval:
            return "WAITING_APPROVAL"
        return "READY"


def classify_records(
    incoming: Iterable[Mapping | CanonicalRecord],
    accepted: Iterable[Mapping | CanonicalRecord] = (),
    *,
    input_mode: InputMode | str,
    declared_periods: Iterable[Period] | None = None,
) -> ClassificationPlan:
    """Compare complete quarterly observations without mutating either input.

    FULL_SNAPSHOT must include all accepted keys. Both modes check continuity
    of the combined accepted/incoming calendar. Delta omissions outside input
    scope are allowed. Duplicate accepted keys are invalid current-state input.
    Incoming conflicts remain available in the rejected plan for investigation.
    """
    mode = InputMode(input_mode)
    accepted_by_key = {}
    for value in accepted:
        record = canonicalize_record(value)
        if record.key in accepted_by_key:
            raise ValidationError(f"Accepted observations have duplicate period {record.key}")
        accepted_by_key[record.key] = record

    groups = {}
    row_count = 0
    for ordinal, value in enumerate(incoming, start=1):
        record = canonicalize_record(value)
        variants = groups.setdefault(record.key, {})
        variants.setdefault(record, []).append(ordinal)
        row_count = ordinal
    if not row_count:
        raise ValidationError("Incoming batch has no records")

    results = []
    duplicate_count = 0
    for key, variants in sorted(groups.items()):
        candidates = tuple(sorted(variants, key=lambda record: record.payload_hash))
        ordinals = tuple(sorted(ordinal for positions in variants.values() for ordinal in positions))
        duplicate_count += sum(len(positions) - 1 for positions in variants.values())
        before = accepted_by_key.get(key)
        changed = ()
        if len(candidates) > 1:
            classification = Classification.CONFLICTING_DUPLICATE
        elif before is None:
            classification = Classification.NEW
        else:
            changed = tuple(name for name, old, new in zip(METRIC_COLUMNS, before.metrics, candidates[0].metrics) if old != new)
            classification = Classification.REVISED if changed else Classification.UNCHANGED
        results.append(ClassifiedRecord(key, classification, before, candidates, ordinals, changed))

    incoming_keys, accepted_keys = set(groups), set(accepted_by_key)
    missing_accepted = accepted_keys - incoming_keys if mode == InputMode.FULL_SNAPSHOT else set()
    declared = None
    missing_declared, unexpected = set(), set()
    if declared_periods is not None:
        scope = []
        for key in declared_periods:
            if not isinstance(key, (tuple, list)) or len(key) != 2:
                raise ValidationError("Declared periods must be YEAR/QUARTER pairs")
            scope.append(_period(*key))
        normalized_scope = tuple(scope)
        if len(set(normalized_scope)) != len(normalized_scope):
            raise ValidationError("Declared scope contains duplicate periods")
        declared = tuple(sorted(normalized_scope))
        missing_declared = set(declared) - incoming_keys
        unexpected = incoming_keys - set(declared)

    combined = incoming_keys | accepted_keys
    ordinals = {(year - 1) * 4 + quarter - 1 for year, quarter in combined}
    missing_ordinals = set(range(min(ordinals), max(ordinals) + 1)) - ordinals
    gaps = tuple((ordinal // 4 + 1, ordinal % 4 + 1) for ordinal in sorted(missing_ordinals))
    return ClassificationPlan(mode, tuple(results), row_count, duplicate_count,
                              tuple(sorted(missing_accepted)), tuple(sorted(missing_declared)),
                              tuple(sorted(unexpected)), gaps, declared)
