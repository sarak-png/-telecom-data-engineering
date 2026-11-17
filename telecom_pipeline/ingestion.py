"""Read the bilingual TRA traffic export without changing source values."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


class SourceFormatError(ValueError):
    """The input does not match the inspected TRA export structure."""


@dataclass
class IngestionResult:
    data: pd.DataFrame
    inspection: dict


def load_tra_csv(path: str | Path, *, encoding: str | None = None) -> IngestionResult:
    """Load strings, preserve missing cells, and reject malformed records.

    UTF-8 (with optional BOM) is the safe default. Other encodings require an
    explicit caller choice: replacement decoding would silently corrupt Arabic.
    English column names are discovered from the file, never synthesized.
    """
    path = Path(path)
    raw = path.read_bytes()
    return parse_tra_csv(raw, source=str(path), encoding=encoding)


def parse_tra_csv(raw: bytes, *, source: str, encoding: str | None = None) -> IngestionResult:
    """Parse an immutable byte snapshot; used by the batch pipeline."""
    selected_encoding = encoding or "utf-8-sig"
    try:
        text = raw.decode(selected_encoding, errors="strict")
    except UnicodeDecodeError as exc:
        raise SourceFormatError("Invalid UTF-8; supply a verified encoding explicitly") from exc
    lines = text.splitlines(keepends=True)
    offset = 0
    while offset < len(lines) and (not lines[offset].strip() or lines[offset].lstrip().startswith("#")):
        offset += 1
    try:
        rows = list(csv.reader(io.StringIO("".join(lines[offset:])), delimiter=",", strict=True))
    except csv.Error as exc:
        raise SourceFormatError(f"Malformed CSV: {exc}") from exc
    if len(rows) < 3:
        raise SourceFormatError("Missing English header, Arabic header, or records")
    header, arabic = rows[:2]
    if len(header) < 3 or header[:2] != ["Year", "Quarter"]:
        raise SourceFormatError("Expected the TRA English Year,Quarter header")
    if any(not name.strip() for name in header) or len(set(header)) != len(header):
        raise SourceFormatError("Empty or duplicate column names")
    if len(arabic) != len(header) or not all(re.search(r"[\u0600-\u06ff]", name) for name in arabic):
        raise SourceFormatError("Missing or invalid bilingual header")
    records = []
    blank_rows = 0
    for index, row in enumerate(rows[2:], start=3):
        if not row or not any(cell.strip() for cell in row):
            blank_rows += 1
            continue
        if len(row) != len(header):
            raise SourceFormatError(f"CSV record {index}: expected {len(header)} cells, got {len(row)}")
        records.append(row)
    if not records:
        raise SourceFormatError("No data records")
    declared = re.search(r"^#\s*Records:\s*(\d+)\s*$", "".join(lines[:offset]), re.MULTILINE)
    # Parse only the validated data section: commas inside quoted years are safe.
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(records)
    buffer.seek(0)
    frame = pd.read_csv(buffer, dtype="string", keep_default_na=False)
    frame = frame.mask(frame.eq(""), pd.NA)
    return IngestionResult(frame, {
        "source": source, "sha256": hashlib.sha256(raw).hexdigest(),
        "encoding": selected_encoding, "delimiter": ",", "metadata_lines": offset,
        "english_header_line": offset + 1, "arabic_columns": arabic,
        "columns": header, "empty_rows_skipped": blank_rows,
        "declared_records": int(declared[1]) if declared else None,
        "record_count_matches_metadata": int(declared[1]) == len(records) if declared else None,
        "rows": len(frame), "duplicate_records": int(frame.duplicated().sum()),
        "null_counts": {name: int(count) for name, count in frame.isna().sum().items()},
    })


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--encoding", default=None)
    args = parser.parse_args()
    result = load_tra_csv(args.path, encoding=args.encoding)
    print(json.dumps(result.inspection, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
