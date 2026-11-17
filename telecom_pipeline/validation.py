"""Observed TRA traffic schema and lossless numeric validation."""
from decimal import Decimal, InvalidOperation
import re

import pandas as pd


COLUMNS = (
    "Year", "Quarter", "Outgoing International Minutes Million",
    "Incoming International Minutes Million", "Mobile Local Minutes Million",
    "Fixed Local Minutes Million", "Fixed Broadband Consumption Million Gb",
    "Mobile Broadband Consumption Million Gb", "International Internet Capacity Mbps",
)
NUMBER = re.compile(r"(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?\Z")


class ValidationError(ValueError):
    """An entire batch is rejected on invalid schema or values."""


def validate(frame: pd.DataFrame) -> tuple[list[dict], dict]:
    if tuple(frame.columns) != COLUMNS:
        raise ValidationError("Columns/order must exactly match the documented TRA traffic schema")
    if frame.empty:
        raise ValidationError("No data records")
    output, periods, unique = [], {}, set()
    nulls = {column: 0 for column in COLUMNS}
    duplicates = 0
    for index, row in enumerate(frame.itertuples(index=False, name=None), start=1):
        record = {}
        for column, value in zip(COLUMNS, row):
            if pd.isna(value) or not str(value).strip():
                nulls[column] += 1
                if column in COLUMNS[:2]:
                    raise ValidationError(f"Record {index}: {column} is required")
                record[column] = None
                continue
            text = str(value).strip()
            try:
                if not NUMBER.fullmatch(text):
                    raise InvalidOperation
                number = Decimal(text.replace(",", ""))
            except InvalidOperation as exc:
                raise ValidationError(f"Record {index}: {column} must be a finite nonnegative number") from exc
            if column in COLUMNS[:2]:
                if number != number.to_integral_value():
                    raise ValidationError(f"Record {index}: {column} must be integral")
                lower, upper = (1900, 2100) if column == "Year" else (1, 4)
                if not lower <= number <= upper:
                    raise ValidationError(f"Record {index}: {column} outside {lower}..{upper}")
            # Fixed decimal text avoids binary-float rounding and equates 1 / 1.00.
            record[column] = format(number, "f").rstrip("0").rstrip(".") if "." in format(number, "f") else format(number, "f")
        key = (record["Year"], record["Quarter"])
        signature = tuple(record.values())
        if key in periods and periods[key] != signature:
            raise ValidationError(f"Conflicting records for period {key}")
        periods[key] = signature
        if signature in unique:
            duplicates += 1
        else:
            unique.add(signature)
            output.append(record)
    return output, {"rows_read": len(frame), "unique_rows": len(output),
                    "duplicate_rows": duplicates, "missing_values": nulls}
