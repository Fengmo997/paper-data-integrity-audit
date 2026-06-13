#!/usr/bin/env python3
"""Audit numeric columns for decimal precision and digit-pattern anomalies."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable


NUMERIC_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?%?$")


def clean_cell(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"", "na", "nan", "none", "null", "inf", "-inf"}:
        return ""
    return text


def numeric_text(text: str) -> str:
    return text.replace(",", "").rstrip("%").strip()


def is_numeric_like(text: str) -> bool:
    return bool(NUMERIC_RE.match(numeric_text(text) + ("%" if text.endswith("%") else ""))) or bool(
        NUMERIC_RE.match(numeric_text(text))
    )


def decimal_places(text: str) -> int | None:
    raw = numeric_text(text)
    if not raw:
        return None
    if "." in raw and "e" not in raw.lower():
        return len(raw.split(".", 1)[1])
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    exponent = value.as_tuple().exponent
    return max(0, -exponent)


def numeric_value(text: str) -> float | None:
    raw = numeric_text(text)
    try:
        value = float(raw)
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    return value


def last_digit(text: str) -> str | None:
    digits = re.sub(r"\D", "", numeric_text(text))
    return digits[-1] if digits else None


def decimal_tail(text: str) -> str | None:
    raw = numeric_text(text)
    if "." not in raw or "e" in raw.lower():
        return None
    tail = raw.split(".", 1)[1]
    return tail if len(tail) >= 2 else None


def read_csv_like(path: Path, delimiter: str | None) -> dict[str, list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        if delimiter is None:
            sample = handle.read(4096)
            handle.seek(0)
            dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
            delimiter = dialect.delimiter
        reader = csv.DictReader(handle, delimiter=delimiter)
        rows = [{k: clean_cell(v) for k, v in row.items()} for row in reader]
    return {path.stem: rows}


def read_excel(path: Path, sheet: str | None) -> dict[str, list[dict[str, str]]]:
    try:
        import pandas as pd
    except ImportError as exc:
        raise SystemExit("Reading Excel files requires pandas and an Excel engine.") from exc

    if sheet:
        frames = {sheet: pd.read_excel(path, sheet_name=sheet, dtype=object)}
    else:
        frames = pd.read_excel(path, sheet_name=None, dtype=object)
    result: dict[str, list[dict[str, str]]] = {}
    for sheet_name, frame in frames.items():
        frame = frame.where(pd.notna(frame), "")
        result[str(sheet_name)] = [
            {str(k): clean_cell(v) for k, v in record.items()}
            for record in frame.to_dict(orient="records")
        ]
    return result


def load_tables(path: Path, sheet: str | None, delimiter: str | None) -> dict[str, list[dict[str, str]]]:
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xls", ".xlsm"}:
        return read_excel(path, sheet)
    if suffix in {".csv", ".tsv", ".txt"}:
        if suffix == ".tsv" and delimiter is None:
            delimiter = "\t"
        return read_csv_like(path, delimiter)
    raise SystemExit(f"Unsupported input type: {suffix}")


def column_names(rows: Iterable[dict[str, str]]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for name in row:
            if name not in seen:
                names.append(name)
                seen.add(name)
    return names


def audit_column(sheet: str, column: str, values: list[str], denominator: int | None) -> dict[str, object] | None:
    nonempty = [v for v in values if clean_cell(v)]
    numeric_values = [v for v in nonempty if is_numeric_like(v)]
    if not numeric_values:
        return None
    numeric_fraction = len(numeric_values) / max(1, len(nonempty))
    if numeric_fraction < 0.8:
        return None

    places = [decimal_places(v) for v in numeric_values]
    places = [p for p in places if p is not None]
    digits = [last_digit(v) for v in numeric_values]
    digits = [d for d in digits if d is not None]
    tails = [decimal_tail(v) for v in numeric_values]
    tails = [t for t in tails if t is not None]
    parsed = [numeric_value(v) for v in numeric_values]
    parsed = [v for v in parsed if v is not None]

    value_counts = Counter(numeric_values)
    repeated_values = {k: v for k, v in value_counts.items() if v > 1}
    place_counts = Counter(places)
    digit_counts = Counter(digits)
    tail_counts = Counter(tails)
    repeated_tails = {k: v for k, v in tail_counts.items() if v >= 3}

    long_decimal_count = sum(1 for p in places if p >= 6)
    integer_as_decimal_count = 0
    for text, value in zip(numeric_values, parsed):
        if "." in numeric_text(text) and float(value).is_integer():
            integer_as_decimal_count += 1

    zero_five_count = sum(1 for d in digits if d in {"0", "5"})
    zero_five_rate = zero_five_count / len(digits) if digits else None

    percent_increment_issue = ""
    if denominator and denominator > 0:
        step = 100.0 / denominator
        bad = []
        for value in parsed:
            if 0 <= value <= 100:
                nearest = round(value / step) * step
                if abs(value - nearest) > 1e-6:
                    bad.append(value)
        if bad:
            percent_increment_issue = f"{len(bad)} values incompatible with denominator {denominator}"

    issues: list[str] = []
    if len(place_counts) > 2:
        issues.append("mixed_decimal_precision")
    if long_decimal_count:
        issues.append("long_decimal_precision")
    if integer_as_decimal_count:
        issues.append("integer_reported_as_decimal")
    if zero_five_rate is not None and len(digits) >= 20 and zero_five_rate >= 0.45:
        issues.append("last_digit_0_or_5_enrichment")
    if repeated_tails:
        issues.append("repeated_decimal_tails")
    if percent_increment_issue:
        issues.append("percentage_denominator_incompatibility")

    return {
        "sheet": sheet,
        "column": column,
        "nonempty_n": len(nonempty),
        "numeric_n": len(numeric_values),
        "unique_numeric_values": len(value_counts),
        "duplicate_value_count": sum(v - 1 for v in value_counts.values() if v > 1),
        "repeated_values": json.dumps(repeated_values, sort_keys=True),
        "decimal_place_counts": json.dumps(dict(sorted(place_counts.items())), sort_keys=True),
        "max_decimal_places": max(places) if places else "",
        "long_decimal_count_ge_6": long_decimal_count,
        "integer_as_decimal_count": integer_as_decimal_count,
        "last_digit_counts": json.dumps(dict(sorted(digit_counts.items())), sort_keys=True),
        "last_digit_0_or_5_rate": round(zero_five_rate, 6) if zero_five_rate is not None else "",
        "repeated_decimal_tails": json.dumps(repeated_tails, sort_keys=True),
        "percentage_denominator_issue": percent_increment_issue,
        "issue_flags": ";".join(issues),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="CSV/TSV/TXT/XLS/XLSX table")
    parser.add_argument("--sheet", help="Excel sheet name; default audits all sheets")
    parser.add_argument("--delimiter", help="CSV delimiter; default detects comma/tab/semicolon")
    parser.add_argument("--denominator", type=int, help="Known denominator for percentage compatibility checks")
    parser.add_argument("--out", default="decimal_audit.csv", help="Output CSV path")
    args = parser.parse_args()

    tables = load_tables(Path(args.input), args.sheet, args.delimiter)
    records: list[dict[str, object]] = []
    for sheet, rows in tables.items():
        for column in column_names(rows):
            values = [row.get(column, "") for row in rows]
            record = audit_column(sheet, column, values, args.denominator)
            if record:
                records.append(record)

    fieldnames = [
        "sheet",
        "column",
        "nonempty_n",
        "numeric_n",
        "unique_numeric_values",
        "duplicate_value_count",
        "repeated_values",
        "decimal_place_counts",
        "max_decimal_places",
        "long_decimal_count_ge_6",
        "integer_as_decimal_count",
        "last_digit_counts",
        "last_digit_0_or_5_rate",
        "repeated_decimal_tails",
        "percentage_denominator_issue",
        "issue_flags",
    ]
    with Path(args.out).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)
    print(f"Wrote {len(records)} audited numeric columns to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

