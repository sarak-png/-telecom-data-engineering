"""Read a single immutable CSV or Excel snapshot with explicit layout rules."""
import csv
import hashlib
import io
from pathlib import Path

import pandas as pd

from .ingestion import IngestionResult, SourceFormatError, parse_tra_csv


def read_source(path: Path, *, encoding: str = "utf-8-sig", sheet: str | int = 0) -> IngestionResult:
    raw = path.read_bytes()
    suffix = path.suffix.lower()
    if suffix == ".csv":
        try:
            text = raw.decode(encoding, errors="strict")
            # TRA exports contain a comment preamble and bilingual headers.
            if text.lstrip().startswith("#"):
                return parse_tra_csv(raw, source=str(path), encoding=encoding)
            rows = list(csv.reader(io.StringIO(text), strict=True))
        except (UnicodeError, csv.Error) as exc:
            raise SourceFormatError(f"CSV decoding/parsing failed: {exc}") from exc
    elif suffix == ".xlsx":
        from openpyxl import load_workbook

        book = load_workbook(io.BytesIO(raw), read_only=True, data_only=False)
        try:
            worksheet = book.worksheets[sheet] if isinstance(sheet, int) else book[sheet]
            rows = []
            for cells in worksheet.iter_rows():
                if any(cell.data_type == "f" for cell in cells):
                    raise SourceFormatError("Excel formulas are unsupported; provide literal source values")
                rows.append(["" if cell.value is None else str(cell.value) for cell in cells])
        finally:
            book.close()
    else:
        raise SourceFormatError("Supported inputs are .csv and .xlsx (legacy .xls is unsupported)")
    if not rows:
        raise SourceFormatError("Empty source")
    header = rows[0]
    if not all(header) or len(set(header)) != len(header):
        raise SourceFormatError("Empty or duplicate headers")
    records = []
    for index, row in enumerate(rows[1:], start=2):
        if not any(value.strip() for value in row):
            continue
        if len(row) != len(header):
            raise SourceFormatError(f"Record {index} has an invalid field count")
        records.append(row)
    frame = pd.DataFrame(records, columns=header, dtype="string")
    return IngestionResult(frame.mask(frame.eq(""), pd.NA), {
        "source": str(path), "sha256": hashlib.sha256(raw).hexdigest(),
        "format": suffix, "encoding": encoding if suffix == ".csv" else None,
        "sheet": sheet if suffix == ".xlsx" else None,
    })
