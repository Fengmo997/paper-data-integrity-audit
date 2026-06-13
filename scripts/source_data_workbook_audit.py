#!/usr/bin/env python3
"""Screen irregular scientific xlsx source-data workbooks.

Outputs workbook/sheet summaries, numeric-cell audits, digital-distribution
summaries, recognizable group-block summaries, near-exact arithmetic
progressions, and duplicate numeric sequences. The parser reads xlsx XML
directly and does not require pandas or openpyxl.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import math
import re
import statistics
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
FIG_RE = re.compile(r"(?:^|\b)(?:fig|sfig|supplementary|extended|extneded)\.?\s*[\w .,\-]*", re.I)
AXIS_RE = re.compile(r"^(?:time|day|week|w|h|0w|1w|2w|4w|6w|8w|12w|\d+\s*[whd]?|wavelength|retention)", re.I)
BENFORD = {str(i): math.log10(1 + 1 / i) for i in range(1, 10)}


def csv_write(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = []
        seen: set[str] = set()
        for row in rows:
            for key in row:
                if key not in seen:
                    fieldnames.append(key)
                    seen.add(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_") or "sheet"


def col_to_num(col: str) -> int:
    value = 0
    for ch in col:
        value = value * 26 + ord(ch.upper()) - ord("A") + 1
    return value


def ref_to_rc(ref: str) -> tuple[int, int]:
    match = re.match(r"([A-Z]+)(\d+)", ref)
    if not match:
        raise ValueError(f"Invalid cell reference: {ref}")
    return int(match.group(2)), col_to_num(match.group(1))


def read_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return ["".join(t.text or "" for t in si.findall(f".//{NS_MAIN}t")) for si in root.findall(f"{NS_MAIN}si")]


def workbook_sheets(zf: zipfile.ZipFile) -> list[tuple[str, str]]:
    workbook = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    rel_map = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels.findall(f"{REL_NS}Relationship")}
    sheets: list[tuple[str, str]] = []
    for sheet in workbook.findall(f".//{NS_MAIN}sheet"):
        target = rel_map[sheet.attrib[f"{NS_REL}id"]].lstrip("/")
        path = "xl/" + target
        if path.startswith("xl//"):
            path = path.replace("xl//", "xl/", 1)
        sheets.append((sheet.attrib["name"], path))
    return sheets


def decode_cell(cell: ET.Element, shared_strings: list[str]) -> tuple[str, str, str]:
    data_type = cell.attrib.get("t", "")
    formula_node = cell.find(f"{NS_MAIN}f")
    formula = formula_node.text or "" if formula_node is not None else ""
    if data_type == "s":
        node = cell.find(f"{NS_MAIN}v")
        if node is None or node.text is None:
            return "", formula, data_type
        idx = int(node.text)
        return shared_strings[idx] if 0 <= idx < len(shared_strings) else "", formula, data_type
    if data_type == "inlineStr":
        return "".join(t.text or "" for t in cell.findall(f".//{NS_MAIN}t")), formula, data_type
    node = cell.find(f"{NS_MAIN}v")
    return (node.text or "") if node is not None else "", formula, data_type


def read_xlsx(path: Path) -> dict[str, dict[str, Any]]:
    with zipfile.ZipFile(path) as zf:
        shared = read_shared_strings(zf)
        sheets: dict[str, dict[str, Any]] = {}
        for sheet_name, sheet_path in workbook_sheets(zf):
            root = ET.fromstring(zf.read(sheet_path))
            cells: dict[tuple[int, int], str] = {}
            formula_count = 0
            max_row = 0
            max_col = 0
            for c in root.findall(f".//{NS_MAIN}c"):
                ref = c.attrib.get("r")
                if not ref:
                    continue
                row, col = ref_to_rc(ref)
                value, formula, _ = decode_cell(c, shared)
                if formula:
                    formula_count += 1
                cells[(row, col)] = value.strip()
                max_row = max(max_row, row)
                max_col = max(max_col, col)
            matrix = [["" for _ in range(max_col)] for _ in range(max_row)]
            for (row, col), value in cells.items():
                matrix[row - 1][col - 1] = value
            sheets[sheet_name] = {"matrix": matrix, "formula_count": formula_count}
        return sheets


def nonempty(value: str) -> bool:
    return str(value).strip() != ""


def is_number(value: str) -> bool:
    text = str(value).strip().replace(",", "").rstrip("%")
    if text == "" or text.lower() in {"na", "nan", "none", "null", "inf", "-inf"}:
        return False
    try:
        number = float(text)
    except ValueError:
        return False
    return math.isfinite(number)


def to_float(value: str) -> float:
    return float(str(value).strip().replace(",", "").rstrip("%"))


def decimal_places(value: str) -> int:
    text = str(value).strip().replace(",", "").rstrip("%")
    if "e" in text.lower():
        return 0
    return len(text.split(".", 1)[1]) if "." in text else 0


def last_digit(value: str) -> str:
    digits = re.sub(r"\D", "", str(value))
    return digits[-1] if digits else ""


def first_digit(value: str) -> str:
    text = str(value).strip().replace(",", "").lstrip("+-")
    for ch in text:
        if ch in "123456789":
            return ch
    return ""


def textish(value: str) -> bool:
    return nonempty(value) and not is_number(value)


def cell(matrix: list[list[str]], row: int, col: int) -> str:
    if row < 0 or col < 0 or row >= len(matrix) or col >= len(matrix[row]):
        return ""
    return matrix[row][col]


def active_segments(matrix: list[list[str]], row: int, lookahead: int = 5) -> list[tuple[int, int]]:
    ncol = max((len(r) for r in matrix), default=0)
    active = [False] * ncol
    for r in range(row, min(len(matrix), row + lookahead + 1)):
        for c in range(ncol):
            if nonempty(cell(matrix, r, c)):
                active[c] = True
    segments: list[tuple[int, int]] = []
    c = 0
    while c < ncol:
        while c < ncol and not active[c]:
            c += 1
        if c >= ncol:
            break
        start = c
        while c < ncol and active[c]:
            c += 1
        segments.append((start, c - 1))
    return segments


def row_numeric_count(matrix: list[list[str]], row: int, start: int, end: int) -> int:
    return sum(1 for c in range(start, end + 1) if is_number(cell(matrix, row, c)))


def row_text_count(matrix: list[list[str]], row: int, start: int, end: int) -> int:
    return sum(1 for c in range(start, end + 1) if textish(cell(matrix, row, c)))


def nearest_panel_label(matrix: list[list[str]], row: int, start: int, end: int) -> str:
    for r in range(row, max(-1, row - 8), -1):
        for c in range(start, end + 1):
            value = cell(matrix, r, c)
            if textish(value) and FIG_RE.search(value):
                return value
    for r in range(row, -1, -1):
        for value in matrix[r]:
            if textish(value) and FIG_RE.search(value):
                return value
    return ""


def fmt(value: float) -> str:
    return f"{value:.10g}" if math.isfinite(value) else ""


def count_string(items: list[str] | list[int]) -> str:
    return ";".join(f"{k}:{v}" for k, v in sorted(Counter(items).items()))


def shannon_entropy(items: list[str]) -> float:
    counts = Counter(x for x in items if x != "")
    total = sum(counts.values())
    if total == 0:
        return math.nan
    return -sum((count / total) * math.log2(count / total) for count in counts.values())


def uniform_chisq(items: list[str], categories: list[str]) -> float:
    counts = Counter(x for x in items if x in categories)
    total = sum(counts.values())
    if total == 0:
        return math.nan
    expected = total / len(categories)
    return sum((counts.get(category, 0) - expected) ** 2 / expected for category in categories)


def benford_chisq(values: list[float], digits: list[str]) -> tuple[float, float]:
    positives = [abs(v) for v in values if v != 0 and math.isfinite(v)]
    observed = [d for d in digits if d in BENFORD]
    if len(positives) < 50 or not observed:
        return math.nan, math.nan
    min_positive = min(positives)
    max_positive = max(positives)
    span = max_positive / min_positive if min_positive else math.inf
    if span < 100:
        return math.nan, span
    counts = Counter(observed)
    total = sum(counts.values())
    chi = 0.0
    for digit, expected_fraction in BENFORD.items():
        expected = total * expected_fraction
        chi += (counts.get(digit, 0) - expected) ** 2 / expected
    return chi, span


def monotonic_nonconstant(values: list[float]) -> bool:
    if len(values) < 5 or max(values) == min(values):
        return False
    diffs = [values[i + 1] - values[i] for i in range(len(values) - 1)]
    return all(d >= 0 for d in diffs) or all(d <= 0 for d in diffs)


def digital_distribution_summary(
    workbook: str,
    sheet: str,
    scope: str,
    panel: str,
    group: str,
    raw: list[str],
    values: list[float],
) -> dict[str, Any]:
    places = [decimal_places(x) for x in raw]
    last_digits = [last_digit(x) for x in raw]
    first_digits = [first_digit(x) for x in raw]
    n = len(values)
    last_counts = Counter(x for x in last_digits if x != "")
    terminal_0_5 = sum(1 for x in last_digits if x in {"0", "5"})
    max_last_fraction = max(last_counts.values()) / n if n and last_counts else math.nan
    last_chisq = uniform_chisq(last_digits, list("0123456789"))
    entropy = shannon_entropy(last_digits)
    b_chisq, magnitude_span = benford_chisq(values, first_digits)
    flags: list[str] = []
    if n < 20:
        flags.append("small_n_digit_distribution_summary_only")
    else:
        if math.isfinite(max_last_fraction) and max_last_fraction >= 0.35:
            flags.append("last_digit_concentration")
        if terminal_0_5 / n >= 0.4:
            flags.append("terminal_0_5_enrichment")
        if math.isfinite(entropy) and entropy < 2.5:
            flags.append("low_last_digit_entropy")
        if math.isfinite(last_chisq) and last_chisq >= 27.88:
            flags.append("last_digit_nonuniform_p_lt_0_001_reference")
    if math.isfinite(b_chisq) and b_chisq >= 26.12:
        flags.append("first_digit_benford_mismatch_context_required")
    if monotonic_nonconstant(values):
        flags.append("monotonic_nonconstant_sequence")
    return {
        "workbook": workbook,
        "sheet": sheet,
        "scope": scope,
        "panel": panel,
        "group": group,
        "n": n,
        "decimal_place_counts": count_string(places),
        "last_digit_counts": count_string(last_digits),
        "first_digit_counts": count_string([x for x in first_digits if x]),
        "last_digit_entropy_log2": fmt(entropy),
        "last_digit_chisq_uniform_df9": fmt(last_chisq),
        "last_digit_max_fraction": fmt(max_last_fraction),
        "terminal_0_5_fraction": fmt(terminal_0_5 / n) if n else "",
        "first_digit_benford_chisq_df8": fmt(b_chisq),
        "order_of_magnitude_span": fmt(magnitude_span),
        "issue_flags": ";".join(flags),
    }


def arithmetic_progression(values: list[float], tolerance: float) -> tuple[bool, float, float]:
    if len(values) < 5:
        return False, math.nan, math.nan
    diffs = [values[i + 1] - values[i] for i in range(len(values) - 1)]
    monotonic = all(d >= 0 for d in diffs) or all(d <= 0 for d in diffs)
    if not monotonic or max(values) == min(values):
        return False, math.nan, math.nan
    step = statistics.fmean(diffs)
    max_dev = max(abs(d - step) for d in diffs)
    return max_dev <= tolerance, step, max_dev


def extract_group_blocks(workbook: str, sheet: str, matrix: list[list[str]], ap_tolerance: float) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    seen: set[tuple[int, int, int]] = set()
    for r in range(max(0, len(matrix) - 1)):
        for start, end in active_segments(matrix, r):
            header = [(c, cell(matrix, r, c).strip()) for c in range(start, end + 1) if textish(cell(matrix, r, c))]
            if len(header) < 2 or row_numeric_count(matrix, r + 1, start, end) < 2:
                continue
            key = (r, start, end)
            if key in seen:
                continue
            seen.add(key)
            data_rows: list[int] = []
            for rr in range(r + 1, len(matrix)):
                nums = row_numeric_count(matrix, rr, start, end)
                txts = row_text_count(matrix, rr, start, end)
                if nums == 0:
                    break
                if txts >= 2 and data_rows:
                    break
                data_rows.append(rr)
            if not data_rows:
                continue
            groups: list[tuple[int, str]] = []
            for c, label in header:
                if FIG_RE.search(label):
                    continue
                if AXIS_RE.match(label) and len(header) > 2:
                    continue
                groups.append((c, label))
            if len(groups) < 2:
                continue
            panel = nearest_panel_label(matrix, r, start, end)
            for i, (g_start, label) in enumerate(groups):
                g_end = groups[i + 1][0] - 1 if i + 1 < len(groups) else end
                raw: list[str] = []
                values: list[float] = []
                for rr in data_rows:
                    for cc in range(g_start, g_end + 1):
                        value = cell(matrix, rr, cc)
                        if is_number(value):
                            raw.append(value)
                            values.append(to_float(value))
                if len(values) < 2:
                    continue
                flags: list[str] = []
                places = [decimal_places(x) for x in raw]
                digits = [last_digit(x) for x in raw]
                digital = digital_distribution_summary(workbook, sheet, "group_block", panel, label, raw, values)
                duplicate_count = len(raw) - len(set(raw))
                if len(values) < 5:
                    flags.append("small_n_limited_distribution_interpretation")
                if any(p >= 8 for p in places):
                    flags.append("long_decimal_precision_ge_8")
                if len(set(values)) == 1:
                    flags.append("zero_variance")
                if len(values) >= 5 and duplicate_count / len(values) >= 0.4:
                    flags.append("high_duplicate_rate")
                is_ap, step, max_dev = arithmetic_progression(values, ap_tolerance)
                if is_ap:
                    flags.append("near_exact_arithmetic_progression")
                if "last_digit_concentration" in digital["issue_flags"]:
                    flags.append("last_digit_concentration")
                if "terminal_0_5_enrichment" in digital["issue_flags"]:
                    flags.append("terminal_0_5_enrichment")
                if "low_last_digit_entropy" in digital["issue_flags"]:
                    flags.append("low_last_digit_entropy")
                if "monotonic_nonconstant_sequence" in digital["issue_flags"]:
                    flags.append("monotonic_nonconstant_sequence")
                mean = statistics.fmean(values)
                sd = statistics.stdev(values) if len(values) > 1 else math.nan
                blocks.append(
                    {
                        "workbook": workbook,
                        "sheet": sheet,
                        "panel": panel,
                        "header_row": r + 1,
                        "data_rows": f"{data_rows[0] + 1}-{data_rows[-1] + 1}",
                        "col_start": start + 1,
                        "col_end": end + 1,
                        "group": label,
                        "n": len(values),
                        "mean": fmt(mean),
                        "sd": fmt(sd),
                        "sem": fmt(sd / math.sqrt(len(values))) if len(values) > 1 else "",
                        "min": fmt(min(values)),
                        "max": fmt(max(values)),
                        "cv": fmt(sd / abs(mean)) if len(values) > 1 and mean else "",
                        "duplicate_value_count": duplicate_count,
                        "decimal_place_counts": count_string(places),
                        "last_digit_counts": count_string(digits),
                        "first_digit_counts": digital["first_digit_counts"],
                        "last_digit_entropy_log2": digital["last_digit_entropy_log2"],
                        "last_digit_chisq_uniform_df9": digital["last_digit_chisq_uniform_df9"],
                        "last_digit_max_fraction": digital["last_digit_max_fraction"],
                        "terminal_0_5_fraction": digital["terminal_0_5_fraction"],
                        "first_digit_benford_chisq_df8": digital["first_digit_benford_chisq_df8"],
                        "order_of_magnitude_span": digital["order_of_magnitude_span"],
                        "arithmetic_step": fmt(step),
                        "arithmetic_max_step_deviation": fmt(max_dev),
                        "values": ";".join(raw),
                        "issue_flags": ";".join(flags),
                    }
                )
    return blocks


def duplicate_sequences(blocks: list[dict[str, Any]], min_len: int) -> list[dict[str, Any]]:
    by_hash: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for block in blocks:
        values = block["values"].split(";")
        if len(values) < min_len or len(set(values)) <= 1:
            continue
        if AXIS_RE.match(str(block["group"])):
            continue
        signature = "|".join(values)
        by_hash[hashlib.sha256(signature.encode()).hexdigest()].append(block)
    rows: list[dict[str, Any]] = []
    for digest, hits in by_hash.items():
        if len(hits) < 2:
            continue
        for hit in hits:
            rows.append(
                {
                    "sha256": digest,
                    "duplicate_sequence_count": len(hits),
                    "workbook": hit["workbook"],
                    "sheet": hit["sheet"],
                    "panel": hit["panel"],
                    "header_row": hit["header_row"],
                    "group": hit["group"],
                    "n": hit["n"],
                    "values": hit["values"],
                }
            )
    return rows


def audit_workbooks(inputs: list[Path], out: Path, ap_tolerance: float, min_sequence_len: int) -> None:
    sheet_rows: list[dict[str, Any]] = []
    label_rows: list[dict[str, Any]] = []
    numeric_rows: list[dict[str, Any]] = []
    digital_rows: list[dict[str, Any]] = []
    all_blocks: list[dict[str, Any]] = []
    sheet_dir = out / "sheet_csv"
    sheet_dir.mkdir(parents=True, exist_ok=True)
    for workbook in inputs:
        for sheet, payload in read_xlsx(workbook).items():
            matrix = payload["matrix"]
            formula_count = payload["formula_count"]
            rows = len(matrix)
            cols = max((len(r) for r in matrix), default=0)
            nonempty_cells = numeric_cells = text_cells = long_decimal_cells = 0
            max_dp = 0
            sheet_raw: list[str] = []
            sheet_values: list[float] = []
            for r, row in enumerate(matrix, 1):
                for c, value in enumerate(row, 1):
                    if not nonempty(value):
                        continue
                    nonempty_cells += 1
                    if is_number(value):
                        numeric_cells += 1
                        dp = decimal_places(value)
                        max_dp = max(max_dp, dp)
                        if dp >= 8:
                            long_decimal_cells += 1
                        sheet_raw.append(value)
                        sheet_values.append(to_float(value))
                        numeric_rows.append(
                            {
                                "workbook": workbook.name,
                                "sheet": sheet,
                                "row": r,
                                "col": c,
                                "value": value,
                                "decimal_places": dp,
                                "last_digit": last_digit(value),
                                "issue_flags": "long_decimal_precision_ge_8" if dp >= 8 else "",
                            }
                        )
                    else:
                        text_cells += 1
                        if FIG_RE.search(value):
                            label_rows.append({"workbook": workbook.name, "sheet": sheet, "row": r, "col": c, "label": value})
            sheet_rows.append(
                {
                    "workbook": workbook.name,
                    "sheet": sheet,
                    "rows": rows,
                    "cols": cols,
                    "nonempty_cells": nonempty_cells,
                    "numeric_cells": numeric_cells,
                    "text_cells": text_cells,
                    "formula_cells": formula_count,
                    "max_decimal_places": max_dp,
                    "long_decimal_cells_ge_8": long_decimal_cells,
                }
            )
            if sheet_values:
                digital_rows.append(digital_distribution_summary(workbook.name, sheet, "sheet", "", "", sheet_raw, sheet_values))
            with (sheet_dir / f"{safe_name(workbook.stem)}__{safe_name(sheet)}.csv").open("w", newline="", encoding="utf-8") as handle:
                csv.writer(handle).writerows(matrix)
            blocks = extract_group_blocks(workbook.name, sheet, matrix, ap_tolerance)
            all_blocks.extend(blocks)
            for block in blocks:
                raw = block["values"].split(";")
                values = [to_float(x) for x in raw if is_number(x)]
                digital_rows.append(
                    digital_distribution_summary(
                        block["workbook"],
                        block["sheet"],
                        "group_block",
                        block["panel"],
                        block["group"],
                        raw,
                        values,
                    )
                )
    csv_write(out / "workbook_sheet_summary.csv", sheet_rows)
    csv_write(out / "panel_label_cells.csv", label_rows)
    csv_write(out / "numeric_cell_audit.csv", numeric_rows)
    csv_write(out / "digital_distribution_summary.csv", digital_rows)
    csv_write(out / "group_block_summary.csv", all_blocks)
    csv_write(out / "arithmetic_progression_blocks.csv", [b for b in all_blocks if "near_exact_arithmetic_progression" in b["issue_flags"]])
    csv_write(out / "duplicate_numeric_sequences.csv", duplicate_sequences(all_blocks, min_sequence_len))
    print(f"workbooks={len(inputs)}")
    print(f"sheets={len(sheet_rows)}")
    print(f"numeric_cells={len(numeric_rows)}")
    print(f"digital_distribution_rows={len(digital_rows)}")
    print(f"group_blocks={len(all_blocks)}")
    print(f"arithmetic_progression_blocks={sum('near_exact_arithmetic_progression' in b['issue_flags'] for b in all_blocks)}")
    print(f"out={out}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="xlsx file or directory containing xlsx files")
    parser.add_argument("--out", default="source_data_workbook_audit", help="Output directory")
    parser.add_argument("--ap-tolerance", type=float, default=0.001, help="Maximum step deviation for arithmetic progression flag")
    parser.add_argument("--min-sequence-len", type=int, default=5, help="Minimum sequence length for duplicate-sequence hashing")
    args = parser.parse_args()

    src = Path(args.input)
    if src.is_dir():
        inputs = sorted(src.glob("*.xlsx"))
    else:
        inputs = [src]
    if not inputs:
        raise SystemExit("No xlsx files found.")
    audit_workbooks(inputs, Path(args.out), args.ap_tolerance, args.min_sequence_len)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
