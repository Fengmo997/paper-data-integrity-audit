#!/usr/bin/env python3
"""Screen irregular scientific xlsx source-data workbooks.

Outputs workbook/sheet summaries, numeric-cell audits, digital-distribution
summaries, recognizable group-block summaries, near-exact arithmetic
progressions, constant adjacent-pair sum patterns, duplicate numeric sequences,
fixed-ratio scaled numeric sequences, and duplicate same-sheet numeric matrix
blocks. It also screens short sliding windows for basic arithmetic relations
with other same-sheet windows. The parser reads xlsx XML directly and does not
require pandas or openpyxl.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import math
import os
import re
import statistics
import zipfile
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
FIG_RE = re.compile(r"(?:^|\b)(?:fig|sfig|supplementary|extended|extneded)\.?\s*[\w .,\-]*", re.I)
AXIS_RE = re.compile(
    r"^(?:time|timepoint|day|week|hour|hours|0w|1w|2w|4w|6w|8w|12w|w(?:\b|\d)|h(?:\b|\d)|\d+\s*[whd]?\b|wavelength|retention)",
    re.I,
)
BENFORD = {str(i): math.log10(1 + 1 / i) for i in range(1, 10)}
LONG_DECIMAL_PLACES = 3
SHORT_SEQUENCE_LEN = 3
SCALED_SEQUENCE_LEN = 3
SCALE_REL_TOLERANCE = 1e-6
SCALE_ABS_TOLERANCE = 1e-9
WINDOW_RELATION_MIN_LEN = 3
WINDOW_RELATION_MAX_LEN = 6
WINDOW_RELATION_REL_TOLERANCE = 1e-6
WINDOW_RELATION_ABS_TOLERANCE = 1e-9
WINDOW_RELATION_MAX_BLOCK_VALUES = 80
WINDOW_RELATION_MAX_WINDOWS_PER_SHEET = 0
WINDOW_RELATION_MAX_HITS_PER_SHEET = 0
WINDOW_RELATION_WORKERS = max(1, min(4, os.cpu_count() or 1))
WINDOW_RELATION_EXHAUSTIVE_WINDOW_THRESHOLD = 600
MATRIX_BLOCK_MIN_ROWS = 3
MATRIX_BLOCK_MIN_COLS = 2
MATRIX_BLOCK_MIN_CELLS = 12
MATRIX_BLOCK_MIN_MATCH_FRACTION = 0.80
EXACT_REPEAT_DECIMAL_PLACES = 3
HIGH_RISK_REPEAT_DECIMAL_PLACES = 6
RELATION_AXIS_OR_ID_RE = re.compile(
    r"\b(?:time|minute|min|hour|day|week|month|age|dose|dosage|concentration|conc\.?|"
    r"wavelength|retention|elapsed|distance|pixel|lane|animal|mouse|rat|sample\s*(?:id|no|number)|"
    r"\bid\b|\bno\.?\b|number|replicate|well|position|coordinate|x\s*axis|y\s*axis)\b",
    re.I,
)
RELATION_DERIVED_OR_SUMMARY_RE = re.compile(
    r"\b(?:mean|average|avg|sd|sem|se\b|stderr|error|p\s*value|pval|q\s*value|ratio|"
    r"fold|normalized|normalised|relative|percent|percentage|total|sum|difference|delta|"
    r"change|auc|ic50|ec50|score|index|z\s*score|log2|log10|control\s*mean)\b|%",
    re.I,
)


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


def num_to_col(num: int) -> str:
    letters = ""
    while num:
        num, rem = divmod(num - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return letters


def ref_to_rc(ref: str) -> tuple[int, int]:
    match = re.match(r"([A-Z]+)(\d+)", ref)
    if not match:
        raise ValueError(f"Invalid cell reference: {ref}")
    return int(match.group(2)), col_to_num(match.group(1))


def rc_to_ref(row: int, col: int) -> str:
    return f"{num_to_col(col)}{row}"


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
        target = rel_map[sheet.attrib[f"{NS_REL}id"]].replace("\\", "/").lstrip("/")
        path = target if target.startswith("xl/") else "xl/" + target
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
    if "." in text and "e" not in text.lower():
        return len(text.split(".", 1)[1])
    try:
        number = Decimal(text)
    except InvalidOperation:
        return 0
    return max(0, -number.as_tuple().exponent)


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


def value_key(value: str) -> str:
    if not is_number(value):
        return str(value).strip()
    number = to_float(value)
    return "0" if number == 0 else f"{number:.15g}"


def normalize_label(text: str) -> str:
    return re.sub(r"\s+", " ", str(text).replace("\xa0", " ").strip().lower())


def cell_range(data_rows: list[int], col_start: int, col_end: int) -> str:
    if not data_rows:
        return ""
    start = rc_to_ref(data_rows[0] + 1, col_start + 1)
    end = rc_to_ref(data_rows[-1] + 1, col_end + 1)
    return start if start == end else f"{start}:{end}"


def inherited_label(matrix: list[list[str]], row: int, col: int, segment_start: int) -> str:
    for cc in range(col, segment_start - 1, -1):
        value = cell(matrix, row, cc).strip()
        if textish(value) and not FIG_RE.search(value):
            return value
    return ""


def context_labels(matrix: list[list[str]], header_row: int, group_col: int, segment_start: int) -> str:
    labels: list[str] = []
    for rr in range(max(0, header_row - 4), header_row):
        label = inherited_label(matrix, rr, group_col, segment_start)
        if label and label not in labels:
            labels.append(label)
    return " | ".join(labels)


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


def modal_cluster(values: list[float], tolerance: float) -> tuple[float, int]:
    if not values:
        return math.nan, 0
    ordered = sorted(values)
    best_mean = ordered[0]
    best_count = 1
    current = [ordered[0]]
    for value in ordered[1:]:
        center = statistics.fmean(current)
        if abs(value - center) <= tolerance:
            current.append(value)
            continue
        if len(current) > best_count:
            best_mean = statistics.fmean(current)
            best_count = len(current)
        current = [value]
    if len(current) > best_count:
        best_mean = statistics.fmean(current)
        best_count = len(current)
    return best_mean, best_count


def adjacent_pair_sum_pattern(
    matrix: list[list[str]],
    data_rows: list[int],
    group_start: int,
    group_end: int,
    tolerance: float,
) -> dict[str, Any]:
    pair_sums: list[float] = []
    row_pairs: list[tuple[int, int, float]] = []
    examples: list[str] = []
    rows_with_pairs = 0
    for rr in data_rows:
        row_values: list[tuple[int, float, str]] = []
        for cc in range(group_start, group_end + 1):
            raw = cell(matrix, rr, cc)
            if is_number(raw):
                row_values.append((cc, to_float(raw), raw))
        pairs_in_row = len(row_values) // 2
        if pairs_in_row < 2:
            continue
        rows_with_pairs += 1
        for idx in range(pairs_in_row):
            c1, v1, raw1 = row_values[2 * idx]
            c2, v2, raw2 = row_values[2 * idx + 1]
            pair_sum = v1 + v2
            pair_sums.append(pair_sum)
            row_pairs.append((rr, pairs_in_row, sum(v for _, v, _ in row_values[: pairs_in_row * 2])))
            if len(examples) < 6:
                examples.append(
                    f"{rc_to_ref(rr + 1, c1 + 1)}+{rc_to_ref(rr + 1, c2 + 1)}="
                    f"{raw1}+{raw2}={fmt(pair_sum)}"
                )
    mode_sum, mode_count = modal_cluster(pair_sums, tolerance)
    total_pairs = len(pair_sums)
    match_fraction = mode_count / total_pairs if total_pairs else math.nan
    row_total_matches = 0
    checked_rows: set[int] = set()
    for rr, pairs_in_row, row_total in row_pairs:
        if rr in checked_rows:
            continue
        checked_rows.add(rr)
        expected_total = mode_sum * pairs_in_row
        if math.isfinite(mode_sum) and abs(row_total - expected_total) <= tolerance * max(1, pairs_in_row):
            row_total_matches += 1
    candidate = (
        total_pairs >= 12
        and rows_with_pairs >= 4
        and mode_count >= max(10, math.ceil(0.80 * total_pairs))
        and row_total_matches >= max(4, math.ceil(0.80 * rows_with_pairs))
    )
    return {
        "pair_sum_target": fmt(mode_sum),
        "pair_sum_matching_pairs": mode_count,
        "pair_sum_total_pairs": total_pairs,
        "pair_sum_matching_fraction": fmt(match_fraction),
        "pair_sum_rows_with_pairs": rows_with_pairs,
        "pair_sum_rows_matching_total": row_total_matches,
        "pair_sum_examples": ";".join(examples),
        "constant_adjacent_pair_sum_candidate": candidate,
    }


def extract_group_blocks(
    workbook: str,
    sheet: str,
    matrix: list[list[str]],
    ap_tolerance: float,
    pair_sum_tolerance: float,
) -> list[dict[str, Any]]:
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
                value_cells: list[str] = []
                for rr in data_rows:
                    for cc in range(g_start, g_end + 1):
                        value = cell(matrix, rr, cc)
                        if is_number(value):
                            raw.append(value)
                            values.append(to_float(value))
                            value_cells.append(rc_to_ref(rr + 1, cc + 1))
                if len(values) < 2:
                    continue
                flags: list[str] = []
                places = [decimal_places(x) for x in raw]
                digits = [last_digit(x) for x in raw]
                digital = digital_distribution_summary(workbook, sheet, "group_block", panel, label, raw, values)
                duplicate_count = len(raw) - len(set(raw))
                if len(values) < 5:
                    flags.append("small_n_limited_distribution_interpretation")
                if any(p >= LONG_DECIMAL_PLACES for p in places):
                    flags.append(f"long_decimal_precision_ge_{LONG_DECIMAL_PLACES}")
                if len(set(values)) == 1:
                    flags.append("zero_variance")
                if len(values) >= 5 and duplicate_count / len(values) >= 0.4:
                    flags.append("high_duplicate_rate")
                is_ap, step, max_dev = arithmetic_progression(values, ap_tolerance)
                if is_ap:
                    flags.append("near_exact_arithmetic_progression")
                pair_pattern = adjacent_pair_sum_pattern(matrix, data_rows, g_start, g_end, pair_sum_tolerance)
                if pair_pattern["constant_adjacent_pair_sum_candidate"]:
                    flags.append("constant_adjacent_pair_sum_pattern")
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
                        "group_col_start": g_start + 1,
                        "group_col_end": g_end + 1,
                        "group_cell_range": cell_range(data_rows, g_start, g_end),
                        "context_labels": context_labels(matrix, r, g_start, start),
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
                        "pair_sum_target": pair_pattern["pair_sum_target"],
                        "pair_sum_matching_pairs": pair_pattern["pair_sum_matching_pairs"],
                        "pair_sum_total_pairs": pair_pattern["pair_sum_total_pairs"],
                        "pair_sum_matching_fraction": pair_pattern["pair_sum_matching_fraction"],
                        "pair_sum_rows_with_pairs": pair_pattern["pair_sum_rows_with_pairs"],
                        "pair_sum_rows_matching_total": pair_pattern["pair_sum_rows_matching_total"],
                        "pair_sum_examples": pair_pattern["pair_sum_examples"],
                        "values": ";".join(raw),
                        "value_cells": ";".join(value_cells),
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
        signature = "|".join(value_key(v) for v in values)
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


def likely_float_display_artifact(value: str) -> tuple[bool, str]:
    """Detect Excel/Python-style binary floating point display tails.

    Examples such as 0.23200000000000001 and 17.600000000000001 should remain
    reviewable exact repeats, but should not be escalated solely because the
    display string has many decimal places.
    """
    raw = str(value).strip()
    try:
        dec = Decimal(raw)
    except InvalidOperation:
        return False, ""
    places = decimal_places(raw)
    if places <= HIGH_RISK_REPEAT_DECIMAL_PLACES:
        return False, ""
    plain = format(dec, "f")
    fraction = plain.split(".", 1)[1] if "." in plain else ""
    has_long_float_tail = "000000" in fraction or "999999" in fraction
    if not has_long_float_tail:
        return False, ""
    for scale in range(0, HIGH_RISK_REPEAT_DECIMAL_PLACES + 1):
        quantum = Decimal(1).scaleb(-scale)
        rounded = dec.quantize(quantum)
        if abs(dec - rounded) <= Decimal("1e-12"):
            return True, format(rounded, "f")
    return False, ""


def exact_long_decimal_repeats(numeric_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_value: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in numeric_rows:
        try:
            places = int(row.get("decimal_places", 0))
        except (TypeError, ValueError):
            places = 0
        if places < EXACT_REPEAT_DECIMAL_PLACES:
            continue
        by_value[str(row.get("value", ""))].append(row)

    out: list[dict[str, Any]] = []
    repeat_counter = 1
    for value, hits in sorted(by_value.items(), key=lambda item: (-len(item[1]), item[0])):
        if len(hits) < 2:
            continue
        max_places = max(int(hit.get("decimal_places", 0) or 0) for hit in hits)
        artifact_hint, rounded_artifact_value = likely_float_display_artifact(value)
        risk_hint = (
            "HIGH-RISK_REVIEW"
            if max_places >= HIGH_RISK_REPEAT_DECIMAL_PLACES and not artifact_hint
            else "WARN_REVIEW"
        )
        repeat_id = f"EXREP-{repeat_counter:04d}"
        cells = ";".join(f"{hit['sheet']}!{rc_to_ref(int(hit['row']), int(hit['col']))}" for hit in hits)
        for hit in hits:
            out.append(
                {
                    "repeat_id": repeat_id,
                    "risk_hint": risk_hint,
                    "repeat_count": len(hits),
                    "value": value,
                    "max_decimal_places": max_places,
                    "workbook": hit["workbook"],
                    "sheet": hit["sheet"],
                    "cell": rc_to_ref(int(hit["row"]), int(hit["col"])),
                    "row": hit["row"],
                    "col": hit["col"],
                    "decimal_places": hit["decimal_places"],
                    "all_repeat_cells": cells,
                    "float_artifact_hint": artifact_hint,
                    "rounded_artifact_value": rounded_artifact_value,
                    "risk_rationale": (
                        "Exact repeated numeric values with displayed decimal precision >=3 are review candidates; "
                        "values with precision >=6 are higher-risk review candidates when they occur in nominally "
                        "independent biological observations and do not appear to be binary floating point display "
                        "artifacts. A documented shared source, detection floor, technical duplicate, rounding rule, "
                        "or Excel export artifact can lower the concern."
                    ),
                }
            )
        repeat_counter += 1
    return out


def mixed_cell_key(value: str) -> str:
    if is_number(value):
        return value_key(value)
    return normalize_label(value)


def first_numeric_rectangle_end(matrix: list[list[str]], row: int, start_col: int, hard_end: int) -> int:
    end = start_col - 1
    for cc in range(start_col, hard_end + 1):
        if not is_number(cell(matrix, row, cc)):
            break
        end = cc
    return end


def complete_numeric_rows(matrix: list[list[str]], start_row: int, col_start: int, col_end: int) -> list[int]:
    rows: list[int] = []
    for rr in range(start_row, len(matrix)):
        if all(is_number(cell(matrix, rr, cc)) for cc in range(col_start, col_end + 1)):
            rows.append(rr)
            continue
        break
    return rows


def nearby_summary_mismatch_examples(
    matrix: list[list[str]],
    a_label_col: int,
    b_label_col: int,
    a_row_end: int,
    b_row_end: int,
    width_with_label: int,
    max_rows: int = 6,
) -> tuple[int, str]:
    examples: list[str] = []
    mismatch_rows = 0
    for offset in range(1, max_rows + 1):
        rr_a = a_row_end + offset
        rr_b = b_row_end + offset
        if rr_a >= len(matrix) or rr_b >= len(matrix):
            break
        a_values = [cell(matrix, rr_a, cc) for cc in range(a_label_col, a_label_col + width_with_label)]
        b_values = [cell(matrix, rr_b, cc) for cc in range(b_label_col, b_label_col + width_with_label)]
        if not any(nonempty(v) for v in a_values + b_values):
            continue
        if [mixed_cell_key(v) for v in a_values] != [mixed_cell_key(v) for v in b_values]:
            mismatch_rows += 1
            if len(examples) < 4:
                a_ref = f"{rc_to_ref(rr_a + 1, a_label_col + 1)}:{rc_to_ref(rr_a + 1, a_label_col + width_with_label)}"
                b_ref = f"{rc_to_ref(rr_b + 1, b_label_col + 1)}:{rc_to_ref(rr_b + 1, b_label_col + width_with_label)}"
                examples.append(f"{a_ref} differs from {b_ref}")
    return mismatch_rows, ";".join(examples)


def matrix_subrange(candidate: dict[str, Any], n_rows: int, n_cols: int) -> str:
    data_rows = list(range(candidate["row_start"], candidate["row_start"] + n_rows))
    return cell_range(data_rows, candidate["col_start"], candidate["col_start"] + n_cols - 1)


def matrix_offset_range(candidate: dict[str, Any], row_offset: int, col_offset: int, n_rows: int, n_cols: int) -> str:
    data_rows = list(range(candidate["row_start"] + row_offset, candidate["row_start"] + row_offset + n_rows))
    col_start = candidate["col_start"] + col_offset
    return cell_range(data_rows, col_start, col_start + n_cols - 1)


def largest_exact_matching_submatrix(
    a: dict[str, Any],
    b: dict[str, Any],
    match_mask: list[list[bool]],
    min_rows: int,
    min_cols: int,
    min_cells: int,
) -> dict[str, Any] | None:
    if not match_mask or not match_mask[0]:
        return None
    n_rows = len(match_mask)
    n_cols = len(match_mask[0])
    best: dict[str, Any] | None = None
    for top in range(n_rows):
        col_still_matching = [True] * n_cols
        for bottom in range(top, n_rows):
            for col in range(n_cols):
                col_still_matching[col] = col_still_matching[col] and match_mask[bottom][col]
            height = bottom - top + 1
            if height < min_rows:
                continue
            run_start: int | None = None
            for col in range(n_cols + 1):
                is_match = col < n_cols and col_still_matching[col]
                if is_match and run_start is None:
                    run_start = col
                if (not is_match or col == n_cols) and run_start is not None:
                    run_end = col - 1
                    width = run_end - run_start + 1
                    cells = height * width
                    if width >= min_cols and cells >= min_cells:
                        values = [
                            a["matrix_values"][rr][cc]
                            for rr in range(top, bottom + 1)
                            for cc in range(run_start, run_end + 1)
                        ]
                        if len(set(value_key(v) for v in values)) > 1 and (
                            best is None or cells > best["matched_cells"]
                        ):
                            best = {
                                "row_offset": top,
                                "col_offset": run_start,
                                "n_rows": height,
                                "n_cols": width,
                                "matched_cells": cells,
                                "matched_cell_range_a": matrix_offset_range(a, top, run_start, height, width),
                                "matched_cell_range_b": matrix_offset_range(b, top, run_start, height, width),
                                "matched_values": ";".join(values),
                            }
                    run_start = None
    return best


def compare_matrix_overlap(
    a: dict[str, Any],
    b: dict[str, Any],
    min_rows: int,
    min_cols: int,
    min_cells: int,
    min_match_fraction: float,
) -> dict[str, Any] | None:
    n_rows = min(a["n_rows"], b["n_rows"])
    n_cols = min(a["n_cols"], b["n_cols"])
    compared_cells = n_rows * n_cols
    if compared_cells < min_cells:
        return None
    matched_cells = 0
    matched_values: list[str] = []
    mismatch_examples: list[str] = []
    match_mask: list[list[bool]] = []
    for r_offset in range(n_rows):
        mask_row: list[bool] = []
        for c_offset in range(n_cols):
            a_raw = a["matrix_values"][r_offset][c_offset]
            b_raw = b["matrix_values"][r_offset][c_offset]
            if value_key(a_raw) == value_key(b_raw):
                matched_cells += 1
                matched_values.append(a_raw)
                mask_row.append(True)
            elif len(mismatch_examples) < 6:
                a_ref = rc_to_ref(a["row_start"] + r_offset + 1, a["col_start"] + c_offset + 1)
                b_ref = rc_to_ref(b["row_start"] + r_offset + 1, b["col_start"] + c_offset + 1)
                mismatch_examples.append(f"{a_ref}!={b_ref}")
                mask_row.append(False)
            else:
                mask_row.append(False)
        match_mask.append(mask_row)
    match_fraction = matched_cells / compared_cells if compared_cells else math.nan
    same_size = a["n_rows"] == b["n_rows"] and a["n_cols"] == b["n_cols"]
    exact_overlap = matched_cells == compared_cells
    whole_matrix_pass = matched_cells >= min_cells and match_fraction >= min_match_fraction
    submatrix = largest_exact_matching_submatrix(
        a,
        b,
        match_mask,
        min_rows,
        min_cols,
        min_cells,
    )
    if not whole_matrix_pass and submatrix is None:
        return None
    if whole_matrix_pass and len(set(value_key(v) for v in matched_values)) <= 1:
        return None
    if whole_matrix_pass:
        match_type = "exact_matrix_reuse" if same_size and exact_overlap else "partial_matrix_overlap"
        evidence_basis = "whole_matrix_match_fraction"
        evidence_matched_cells = matched_cells
        contiguous_rows = n_rows if exact_overlap else (submatrix["n_rows"] if submatrix else "")
        contiguous_cols = n_cols if exact_overlap else (submatrix["n_cols"] if submatrix else "")
        matched_cell_range_a = matrix_subrange(a, n_rows, n_cols)
        matched_cell_range_b = matrix_subrange(b, n_rows, n_cols)
        evidence_values = ";".join(matched_values)
    else:
        match_type = "partial_contiguous_matrix_reuse"
        evidence_basis = "contiguous_exact_submatrix"
        evidence_matched_cells = submatrix["matched_cells"]
        contiguous_rows = submatrix["n_rows"]
        contiguous_cols = submatrix["n_cols"]
        matched_cell_range_a = submatrix["matched_cell_range_a"]
        matched_cell_range_b = submatrix["matched_cell_range_b"]
        evidence_values = submatrix["matched_values"]
    return {
        "match_type": match_type,
        "evidence_basis": evidence_basis,
        "overlap_rows": n_rows,
        "overlap_cols": n_cols,
        "compared_cells": compared_cells,
        "matched_cells": matched_cells,
        "mismatched_cells": compared_cells - matched_cells,
        "match_fraction": match_fraction,
        "evidence_matched_cells": evidence_matched_cells,
        "contiguous_match_rows": contiguous_rows,
        "contiguous_match_cols": contiguous_cols,
        "matched_cell_range_a": matched_cell_range_a,
        "matched_cell_range_b": matched_cell_range_b,
        "mismatch_examples": ";".join(mismatch_examples),
        "matched_values": evidence_values,
    }


def duplicate_numeric_matrix_blocks(
    workbook: str,
    sheet: str,
    matrix: list[list[str]],
    min_rows: int = MATRIX_BLOCK_MIN_ROWS,
    min_cols: int = MATRIX_BLOCK_MIN_COLS,
    min_cells: int = MATRIX_BLOCK_MIN_CELLS,
    min_match_fraction: float = MATRIX_BLOCK_MIN_MATCH_FRACTION,
) -> list[dict[str, Any]]:
    """Detect same-sheet figure/panel anchored numeric matrices copied as a block."""
    candidates: list[dict[str, Any]] = []
    ncol = max((len(row) for row in matrix), default=0)
    for rr, row in enumerate(matrix):
        fig_labels = [(cc, cell(matrix, rr, cc).strip()) for cc in range(ncol) if FIG_RE.search(cell(matrix, rr, cc))]
        if len(fig_labels) < 2:
            continue
        for idx, (label_col, label) in enumerate(fig_labels):
            start_col = label_col + 1
            if start_col >= ncol:
                continue
            hard_end = fig_labels[idx + 1][0] - 1 if idx + 1 < len(fig_labels) else ncol - 1
            end_col = first_numeric_rectangle_end(matrix, rr, start_col, hard_end)
            if end_col < start_col:
                continue
            data_rows = complete_numeric_rows(matrix, rr, start_col, end_col)
            n_rows = len(data_rows)
            n_cols = end_col - start_col + 1
            n_cells = n_rows * n_cols
            if n_rows < min_rows or n_cols < min_cols or n_cells < min_cells:
                continue
            matrix_values = [[cell(matrix, r, c) for c in range(start_col, end_col + 1)] for r in data_rows]
            raw_values = [value for row_values in matrix_values for value in row_values]
            keys = [value_key(v) for v in raw_values]
            if len(set(keys)) <= 1:
                continue
            candidates.append(
                {
                    "label_col": label_col,
                    "label_cell": rc_to_ref(rr + 1, label_col + 1),
                    "panel_label": label,
                    "row_start": data_rows[0],
                    "row_end": data_rows[-1],
                    "col_start": start_col,
                    "col_end": end_col,
                    "n_rows": n_rows,
                    "n_cols": n_cols,
                    "n_cells": n_cells,
                    "cell_range": cell_range(data_rows, start_col, end_col),
                    "signature": hashlib.sha256("|".join(keys).encode()).hexdigest(),
                    "matrix_values": matrix_values,
                    "values": ";".join(raw_values),
                }
            )

    rows: list[dict[str, Any]] = []
    match_counter = 1
    ordered = sorted(candidates, key=lambda item: (item["row_start"], item["label_col"]))
    for i, a in enumerate(ordered):
        for b in ordered[i + 1 :]:
            overlap = compare_matrix_overlap(a, b, min_rows, min_cols, min_cells, min_match_fraction)
            if overlap is None:
                continue
            mismatch_rows, summary_mismatch_examples = nearby_summary_mismatch_examples(
                matrix,
                a["label_col"],
                b["label_col"],
                a["row_end"],
                b["row_end"],
                min(a["n_cols"], b["n_cols"]) + 1,
            )
            basis = "same_sheet_same_row_distinct_panel" if a["row_start"] == b["row_start"] else "same_sheet_distinct_panel"
            high_risk = overlap["match_fraction"] >= 0.95 or overlap["evidence_matched_cells"] >= 30
            rows.append(
                {
                    "match_id": f"MAT-{match_counter:04d}",
                    "match_type": overlap["match_type"],
                    "risk_hint": "HIGH-RISK_REVIEW" if high_risk else "WARN_TO_HIGH-RISK_REVIEW",
                    "evidence_basis": overlap["evidence_basis"],
                    "comparison_basis": basis,
                    "workbook": workbook,
                    "sheet": sheet,
                    "panel_label_a": a["panel_label"],
                    "label_cell_a": a["label_cell"],
                    "cell_range_a": a["cell_range"],
                    "panel_label_b": b["panel_label"],
                    "label_cell_b": b["label_cell"],
                    "cell_range_b": b["cell_range"],
                    "n_rows_a": a["n_rows"],
                    "n_cols_a": a["n_cols"],
                    "n_cells_a": a["n_cells"],
                    "n_rows_b": b["n_rows"],
                    "n_cols_b": b["n_cols"],
                    "n_cells_b": b["n_cells"],
                    "overlap_rows": overlap["overlap_rows"],
                    "overlap_cols": overlap["overlap_cols"],
                    "compared_cells": overlap["compared_cells"],
                    "matched_cells": overlap["matched_cells"],
                    "mismatched_cells": overlap["mismatched_cells"],
                    "match_fraction": fmt(overlap["match_fraction"]),
                    "min_match_fraction_threshold": fmt(min_match_fraction),
                    "evidence_matched_cells": overlap["evidence_matched_cells"],
                    "contiguous_match_rows": overlap["contiguous_match_rows"],
                    "contiguous_match_cols": overlap["contiguous_match_cols"],
                    "matched_cell_range_a": overlap["matched_cell_range_a"],
                    "matched_cell_range_b": overlap["matched_cell_range_b"],
                    "mismatch_examples": overlap["mismatch_examples"],
                    "nearby_summary_mismatch_rows": mismatch_rows,
                    "nearby_summary_mismatch_examples": summary_mismatch_examples,
                    "matched_values": overlap["matched_values"],
                    "risk_rationale": (
                        "A same-sheet numeric matrix is reused or substantially overlapped under distinct "
                        "figure/panel labels. Escalate when the panels represent nominally different "
                        "treatments, conditions, cell lines, assays, or summary claims and the source does "
                        "not document a shared calculation source, technical duplicate, or intentional reuse. "
                        "Summary rows that differ after identical or highly overlapping raw blocks require "
                        "author clarification."
                    ),
                }
            )
            match_counter += 1
    return rows


def adjacent_block_pairs(blocks: list[dict[str, Any]]) -> set[tuple[int, int]]:
    by_layout: dict[tuple[Any, ...], list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for idx, block in enumerate(blocks):
        key = (block["workbook"], block["sheet"], block["panel"], block["header_row"], block["data_rows"])
        by_layout[key].append((idx, block))
    pairs: set[tuple[int, int]] = set()
    for hits in by_layout.values():
        ordered = sorted(hits, key=lambda item: (int(item[1].get("group_col_start", 0)), int(item[1].get("group_col_end", 0))))
        for left, right in zip(ordered, ordered[1:]):
            a, b = sorted((left[0], right[0]))
            pairs.add((a, b))
    return pairs


def longest_common_consecutive(a: list[str], b: list[str]) -> tuple[int, int, int]:
    best_len = best_a = best_b = 0
    previous = [0] * (len(b) + 1)
    for i, aval in enumerate(a, 1):
        current = [0] * (len(b) + 1)
        for j, bval in enumerate(b, 1):
            if aval == bval:
                current[j] = previous[j - 1] + 1
                if current[j] > best_len:
                    best_len = current[j]
                    best_a = i - best_len
                    best_b = j - best_len
        previous = current
    return best_a, best_b, best_len


def comparison_bases(a: dict[str, Any], b: dict[str, Any], is_adjacent: bool) -> list[str]:
    same_panel = a["workbook"] == b["workbook"] and a["sheet"] == b["sheet"] and a["panel"] == b["panel"]
    same_group = normalize_label(a.get("group", "")) == normalize_label(b.get("group", "")) and normalize_label(a.get("group", "")) != ""
    bases: list[str] = []
    if same_panel:
        if same_group:
            bases.append("same_panel_same_condition")
        if is_adjacent:
            bases.append("same_panel_adjacent_condition")
    elif same_group:
        bases.append("cross_panel_same_condition")
    return bases


def independent_context_hint(a: dict[str, Any], b: dict[str, Any]) -> tuple[bool, str]:
    same_panel = a["workbook"] == b["workbook"] and a["sheet"] == b["sheet"] and a["panel"] == b["panel"]
    same_group = normalize_label(a.get("group", "")) == normalize_label(b.get("group", "")) and normalize_label(a.get("group", "")) != ""
    same_context = (
        normalize_label(a.get("context_labels", ""))
        == normalize_label(b.get("context_labels", ""))
        and normalize_label(a.get("context_labels", "")) != ""
    )
    if same_panel and same_group and not same_context:
        return True, "same_panel_same_condition_different_readout_or_cytokine"
    if same_panel and not same_group:
        return True, "same_panel_adjacent_or_distinct_condition"
    if not same_panel and same_group:
        return True, "cross_panel_same_condition"
    if same_panel and same_group and same_context:
        return False, "same_panel_same_condition_same_context_shared_source_possible"
    return False, "independence_unclear"


def shared_source_hint(a: dict[str, Any], b: dict[str, Any]) -> str:
    same_context = (
        normalize_label(a.get("context_labels", ""))
        == normalize_label(b.get("context_labels", ""))
        and normalize_label(a.get("context_labels", "")) != ""
    )
    same_group = normalize_label(a.get("group", "")) == normalize_label(b.get("group", "")) and normalize_label(a.get("group", "")) != ""
    if same_context and same_group:
        return "possible_shared_source_check_formula_normalization_or_export_rule"
    return "no_shared_source_documented_by_raw_layout"


def short_risk_hint(bases: list[str], independent: bool, shared_hint: str) -> str:
    if independent and shared_hint == "no_shared_source_documented_by_raw_layout":
        return "HIGH-RISK_REVIEW"
    return "WARN_REVIEW"


def short_duplicate_sequences(blocks: list[dict[str, Any]], min_len: int) -> list[dict[str, Any]]:
    adjacent_pairs = adjacent_block_pairs(blocks)
    rows: list[dict[str, Any]] = []
    match_counter = 1
    for i, a in enumerate(blocks):
        a_raw = a["values"].split(";") if a.get("values") else []
        a_cells = a.get("value_cells", "").split(";") if a.get("value_cells") else []
        if len(a_raw) < min_len or AXIS_RE.match(str(a.get("group", ""))):
            continue
        for j in range(i + 1, len(blocks)):
            b = blocks[j]
            b_raw = b["values"].split(";") if b.get("values") else []
            b_cells = b.get("value_cells", "").split(";") if b.get("value_cells") else []
            if len(b_raw) < min_len or AXIS_RE.match(str(b.get("group", ""))):
                continue
            bases = comparison_bases(a, b, tuple(sorted((i, j))) in adjacent_pairs)
            if not bases:
                continue
            a_keys = [value_key(v) for v in a_raw]
            b_keys = [value_key(v) for v in b_raw]
            full_match = len(a_keys) == len(b_keys) and len(a_keys) >= min_len and a_keys == b_keys
            start_a, start_b, match_len = longest_common_consecutive(a_keys, b_keys)
            if not full_match and match_len < min_len:
                continue
            if full_match:
                start_a = start_b = 0
                match_len = len(a_keys)
                match_type = "full_short_sequence"
            else:
                match_type = "local_consecutive_overlap"
            matched_keys = a_keys[start_a : start_a + match_len]
            if len(set(matched_keys)) <= 1:
                continue
            matched_values = a_raw[start_a : start_a + match_len]
            independent, independence_hint = independent_context_hint(a, b)
            source_hint = shared_source_hint(a, b)
            rows.append(
                {
                    "match_id": f"SDS-{match_counter:04d}",
                    "match_type": match_type,
                    "risk_hint": short_risk_hint(bases, independent, source_hint),
                    "comparison_basis": ";".join(bases),
                    "independent_cytokine_or_condition_hint": independence_hint,
                    "shared_calculation_source_hint": source_hint,
                    "workbook_a": a["workbook"],
                    "sheet_a": a["sheet"],
                    "panel_a": a["panel"],
                    "group_a": a["group"],
                    "context_a": a.get("context_labels", ""),
                    "cell_range_a": a.get("group_cell_range", ""),
                    "matched_cells_a": ";".join(a_cells[start_a : start_a + match_len]),
                    "workbook_b": b["workbook"],
                    "sheet_b": b["sheet"],
                    "panel_b": b["panel"],
                    "group_b": b["group"],
                    "context_b": b.get("context_labels", ""),
                    "cell_range_b": b.get("group_cell_range", ""),
                    "matched_cells_b": ";".join(b_cells[start_b : start_b + match_len]),
                    "n_a": len(a_keys),
                    "n_b": len(b_keys),
                    "match_length": match_len,
                    "match_start_offset_a": start_a + 1,
                    "match_start_offset_b": start_b + 1,
                    "matched_values": ";".join(matched_values),
                    "risk_rationale": (
                        "Escalate when the matched vectors are nominally independent cytokines, conditions, "
                        "samples, or panels and the source does not document a shared calculation source, "
                        "technical duplicate, calibrator, or rounding/export rule."
                    ),
                }
            )
            match_counter += 1
    return rows


def simple_ratio_label(ratio: float, max_denominator: int = 20, rel_tolerance: float = 1e-5) -> str:
    if not math.isfinite(ratio):
        return ""
    fraction = Fraction(ratio).limit_denominator(max_denominator)
    approx = fraction.numerator / fraction.denominator
    if abs(ratio - approx) <= rel_tolerance * max(1.0, abs(ratio)):
        if fraction.denominator == 1:
            return str(fraction.numerator)
        return f"{fraction.numerator}/{fraction.denominator}"
    return ""


def scaled_comparison_bases(a: dict[str, Any], b: dict[str, Any], is_adjacent: bool) -> list[str]:
    bases = comparison_bases(a, b, is_adjacent)
    if not bases:
        bases.append("same_layout_distinct_condition")
    return bases


def scaled_risk_hint(independent: bool, shared_hint: str) -> str:
    if independent and shared_hint == "no_shared_source_documented_by_raw_layout":
        return "HIGH-RISK_REVIEW"
    if shared_hint == "possible_shared_source_check_formula_normalization_or_export_rule":
        return "WARN_REVIEW"
    return "WARN_TO_HIGH-RISK_REVIEW"


def scaled_numeric_sequence_matches(
    blocks: list[dict[str, Any]],
    min_len: int,
    rel_tolerance: float,
    abs_tolerance: float,
) -> list[dict[str, Any]]:
    by_layout: dict[tuple[Any, ...], list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for idx, block in enumerate(blocks):
        if AXIS_RE.match(str(block.get("group", ""))):
            continue
        key = (
            block["workbook"],
            block["sheet"],
            block["panel"],
            block["header_row"],
            block["data_rows"],
            block["col_start"],
            block["col_end"],
        )
        by_layout[key].append((idx, block))

    adjacent_pairs = adjacent_block_pairs(blocks)
    rows: list[dict[str, Any]] = []
    match_counter = 1
    for hits in by_layout.values():
        ordered = sorted(hits, key=lambda item: (int(item[1].get("group_col_start", 0)), int(item[1].get("group_col_end", 0))))
        for left_pos, (i, a) in enumerate(ordered):
            a_raw = a["values"].split(";") if a.get("values") else []
            a_cells = a.get("value_cells", "").split(";") if a.get("value_cells") else []
            if len(a_raw) < min_len or len(set(value_key(v) for v in a_raw)) <= 1:
                continue
            a_vals = [to_float(v) for v in a_raw]
            for j, b in ordered[left_pos + 1 :]:
                b_raw = b["values"].split(";") if b.get("values") else []
                b_cells = b.get("value_cells", "").split(";") if b.get("value_cells") else []
                if len(b_raw) != len(a_raw) or len(b_raw) < min_len:
                    continue
                if len(set(value_key(v) for v in b_raw)) <= 1:
                    continue
                b_vals = [to_float(v) for v in b_raw]
                comparable = [(av, bv) for av, bv in zip(a_vals, b_vals) if abs(av) > abs_tolerance]
                if len(comparable) < min_len:
                    continue
                denominator = sum(av * av for av, _ in comparable)
                if denominator <= abs_tolerance:
                    continue
                ratio = sum(av * bv for av, bv in comparable) / denominator
                if not math.isfinite(ratio) or abs(ratio) <= abs_tolerance:
                    continue
                if abs(ratio - 1.0) <= rel_tolerance:
                    continue
                all_pairs = list(zip(a_vals, b_vals))
                abs_errors = [abs(bv - ratio * av) for av, bv in all_pairs]
                rel_errors = [
                    err / max(abs(bv), abs(ratio * av), abs_tolerance)
                    for err, (av, bv) in zip(abs_errors, all_pairs)
                ]
                max_abs_error = max(abs_errors)
                max_rel_error = max(rel_errors)
                if max_abs_error > abs_tolerance and max_rel_error > rel_tolerance:
                    continue
                bases = scaled_comparison_bases(a, b, tuple(sorted((i, j))) in adjacent_pairs)
                independent, independence_hint = independent_context_hint(a, b)
                source_hint = shared_source_hint(a, b)
                ratio_simple = simple_ratio_label(ratio)
                rows.append(
                    {
                        "match_id": f"SCALE-{match_counter:04d}",
                        "risk_hint": scaled_risk_hint(independent, source_hint),
                        "comparison_basis": ";".join(bases),
                        "independent_cytokine_or_condition_hint": independence_hint,
                        "shared_calculation_source_hint": source_hint,
                        "workbook_a": a["workbook"],
                        "sheet_a": a["sheet"],
                        "panel_a": a["panel"],
                        "group_a": a["group"],
                        "context_a": a.get("context_labels", ""),
                        "cell_range_a": a.get("group_cell_range", ""),
                        "matched_cells_a": ";".join(a_cells),
                        "workbook_b": b["workbook"],
                        "sheet_b": b["sheet"],
                        "panel_b": b["panel"],
                        "group_b": b["group"],
                        "context_b": b.get("context_labels", ""),
                        "cell_range_b": b.get("group_cell_range", ""),
                        "matched_cells_b": ";".join(b_cells),
                        "n_a": len(a_raw),
                        "n_b": len(b_raw),
                        "aligned_numeric_pairs": len(comparable),
                        "ratio_b_over_a": fmt(ratio),
                        "simple_ratio": ratio_simple,
                        "max_abs_error": fmt(max_abs_error),
                        "max_rel_error": fmt(max_rel_error),
                        "matched_values_a": ";".join(a_raw),
                        "matched_values_b": ";".join(b_raw),
                        "risk_rationale": (
                            "A complete numeric vector is reproduced as a fixed scalar multiple of another vector. "
                            "Escalate when the vectors are nominally independent conditions, genes, samples, or "
                            "panels and the source does not document a shared normalization, calibrator, technical "
                            "duplicate, or deterministic transformation."
                        ),
                    }
                )
                match_counter += 1
    return rows


def relation_context_text(block: dict[str, Any]) -> str:
    return " ".join(
        str(block.get(key, ""))
        for key in ("panel", "context_labels", "group", "sheet")
        if block.get(key)
    )


def likely_axis_or_identifier_block(block: dict[str, Any]) -> bool:
    return bool(RELATION_AXIS_OR_ID_RE.search(relation_context_text(block))) or AXIS_RE.match(str(block.get("group", ""))) is not None


def relation_role_hint(block: dict[str, Any]) -> str:
    text = relation_context_text(block)
    if RELATION_DERIVED_OR_SUMMARY_RE.search(text):
        return "derived_or_summary_like_label"
    if likely_axis_or_identifier_block(block):
        return "axis_or_identifier_like_label"
    return "primary_measurement_like_label"


def relation_independent_hint(
    source_a: dict[str, Any],
    source_b: dict[str, Any] | None,
    target: dict[str, Any],
) -> tuple[bool, str]:
    target_role = relation_role_hint(target)
    source_roles = [relation_role_hint(source_a)]
    if source_b is not None:
        source_roles.append(relation_role_hint(source_b))
    if target_role == "axis_or_identifier_like_label" or any(role == "axis_or_identifier_like_label" for role in source_roles):
        return False, "axis_or_identifier_context"
    if target_role == "derived_or_summary_like_label":
        return False, "target_label_looks_derived_or_summary"
    same_panel = source_a.get("panel") == target.get("panel")
    same_context = normalize_label(source_a.get("context_labels", "")) == normalize_label(target.get("context_labels", ""))
    same_group = normalize_label(source_a.get("group", "")) == normalize_label(target.get("group", ""))
    if source_b is not None:
        same_panel = same_panel and source_b.get("panel") == target.get("panel")
        same_context = same_context and normalize_label(source_b.get("context_labels", "")) == normalize_label(target.get("context_labels", ""))
        same_group = same_group and normalize_label(source_b.get("group", "")) == normalize_label(target.get("group", ""))
    if same_panel and same_context and not same_group:
        return True, "same_panel_same_context_distinct_groups"
    if same_panel and not same_context:
        return True, "same_panel_distinct_readout_or_context"
    if not same_panel:
        return True, "cross_panel_relation"
    return False, "shared_context_or_independence_unclear"


def relation_risk_hint(
    source_a: dict[str, Any],
    source_b: dict[str, Any] | None,
    target: dict[str, Any],
) -> tuple[str, str, str]:
    independent, independence_hint = relation_independent_hint(source_a, source_b, target)
    target_role = relation_role_hint(target)
    source_roles = [relation_role_hint(source_a)]
    if source_b is not None:
        source_roles.append(relation_role_hint(source_b))
    if target_role == "derived_or_summary_like_label":
        return "WARN_REVIEW", independence_hint, "target_label_looks_like_declared_derivative_or_summary"
    if target_role == "axis_or_identifier_like_label" or any(role == "axis_or_identifier_like_label" for role in source_roles):
        return "WARN_REVIEW", independence_hint, "axis_or_identifier_context"
    if independent:
        return "HIGH-RISK_REVIEW", independence_hint, "no_shared_calculation_source_documented_by_raw_layout"
    return "WARN_REVIEW", independence_hint, "shared_context_or_relation_may_be_documented"


def window_residual_ok(
    observed: list[float],
    expected: list[float],
    rel_tolerance: float,
    abs_tolerance: float,
) -> tuple[bool, float, float]:
    errors = [abs(o - e) for o, e in zip(observed, expected)]
    if not errors:
        return False, math.nan, math.nan
    rel_errors = [
        err / max(abs(o), abs(e), abs_tolerance)
        for err, o, e in zip(errors, observed, expected)
    ]
    max_abs_error = max(errors)
    max_rel_error = max(rel_errors)
    return max_abs_error <= abs_tolerance or max_rel_error <= rel_tolerance, max_abs_error, max_rel_error


def fit_scale(source_values: list[float], target_values: list[float], abs_tolerance: float) -> float | None:
    comparable = [(s, t) for s, t in zip(source_values, target_values) if abs(s) > abs_tolerance]
    if len(comparable) < WINDOW_RELATION_MIN_LEN:
        return None
    denominator = sum(s * s for s, _ in comparable)
    if denominator <= abs_tolerance:
        return None
    ratio = sum(s * t for s, t in comparable) / denominator
    if not math.isfinite(ratio) or abs(ratio) <= abs_tolerance:
        return None
    return ratio


def fit_affine(
    source_values: list[float],
    target_values: list[float],
    abs_tolerance: float,
) -> tuple[float, float] | None:
    if len(source_values) != len(target_values) or len(source_values) < WINDOW_RELATION_MIN_LEN:
        return None
    if len(set(fmt(v) for v in source_values)) <= 1 or len(set(fmt(v) for v in target_values)) <= 1:
        return None
    mean_source = statistics.fmean(source_values)
    mean_target = statistics.fmean(target_values)
    denominator = sum((s - mean_source) ** 2 for s in source_values)
    if denominator <= abs_tolerance:
        return None
    slope = sum((s - mean_source) * (t - mean_target) for s, t in zip(source_values, target_values)) / denominator
    intercept = mean_target - slope * mean_source
    if not math.isfinite(slope) or not math.isfinite(intercept) or abs(slope) <= abs_tolerance:
        return None
    return slope, intercept


def scaled_relation_from_basis(
    basis_values: list[float],
    target_values: list[float],
    relation_type: str,
    detail_prefix: str,
    rel_tolerance: float,
    abs_tolerance: float,
) -> tuple[str, str, float, float] | None:
    if len(basis_values) != len(target_values) or len(basis_values) < WINDOW_RELATION_MIN_LEN:
        return None
    if len(set(fmt(v) for v in basis_values)) <= 1 or len(set(fmt(v) for v in target_values)) <= 1:
        return None
    scale = fit_scale(basis_values, target_values, abs_tolerance)
    if scale is None:
        return None
    expected = [scale * value for value in basis_values]
    ok, max_abs, max_rel = window_residual_ok(target_values, expected, rel_tolerance, abs_tolerance)
    if not ok:
        return None
    simple = simple_ratio_label(scale)
    detail = f"{detail_prefix};scale={fmt(scale)}" + (f" ({simple})" if simple else "")
    return relation_type, detail, max_abs, max_rel


def constant_offset_or_ratio_relation(
    source_values: list[float],
    target_values: list[float],
    rel_tolerance: float,
    abs_tolerance: float,
) -> tuple[str, str, float, float] | None:
    if len(source_values) != len(target_values) or len(source_values) < WINDOW_RELATION_MIN_LEN:
        return None
    if len(set(fmt(v) for v in source_values)) <= 1 or len(set(fmt(v) for v in target_values)) <= 1:
        return None

    offsets = [t - s for s, t in zip(source_values, target_values)]
    offset = statistics.fmean(offsets)
    ok, max_abs, max_rel = window_residual_ok(
        target_values,
        [s + offset for s in source_values],
        rel_tolerance,
        abs_tolerance,
    )
    if ok and abs(offset) > abs_tolerance:
        return "target = source + constant", f"constant={fmt(offset)}", max_abs, max_rel

    ratio = fit_scale(source_values, target_values, abs_tolerance)
    if ratio is None:
        return None
    if abs(ratio - 1.0) <= rel_tolerance:
        return None
    ok, max_abs, max_rel = window_residual_ok(
        target_values,
        [ratio * s for s in source_values],
        rel_tolerance,
        abs_tolerance,
    )
    if ok:
        simple = simple_ratio_label(ratio)
        detail = f"ratio={fmt(ratio)}" + (f" ({simple})" if simple else "")
        return "target = source * constant", detail, max_abs, max_rel

    affine = fit_affine(source_values, target_values, abs_tolerance)
    if affine is not None:
        slope, intercept = affine
        if abs(slope - 1.0) > rel_tolerance and abs(intercept) > abs_tolerance:
            expected = [slope * s + intercept for s in source_values]
            ok, max_abs, max_rel = window_residual_ok(target_values, expected, rel_tolerance, abs_tolerance)
            if ok:
                simple = simple_ratio_label(slope)
                detail = f"slope={fmt(slope)}" + (f" ({simple})" if simple else "") + f";intercept={fmt(intercept)}"
                return "target = source * constant + offset", detail, max_abs, max_rel

    if all(abs(s) > abs_tolerance for s in source_values):
        inverse_basis = [1 / s for s in source_values]
        inverse_relation = scaled_relation_from_basis(
            inverse_basis,
            target_values,
            "target = constant / source",
            "basis=1/source",
            rel_tolerance,
            abs_tolerance,
        )
        if inverse_relation is not None:
            return inverse_relation
    return None


def constant_sum_relation(
    source_values: list[float],
    target_values: list[float],
    rel_tolerance: float,
    abs_tolerance: float,
) -> tuple[str, str, float, float] | None:
    if len(source_values) != len(target_values) or len(source_values) < WINDOW_RELATION_MIN_LEN:
        return None
    if len(set(fmt(v) for v in source_values)) <= 1 or len(set(fmt(v) for v in target_values)) <= 1:
        return None
    sums = [s + t for s, t in zip(source_values, target_values)]
    sum_constant = statistics.fmean(sums)
    ok, max_abs, max_rel = window_residual_ok(
        sums,
        [sum_constant for _ in sums],
        rel_tolerance,
        abs_tolerance,
    )
    if not ok:
        return None
    if abs(sum_constant) <= abs_tolerance:
        return None
    return "target = constant - source", f"sum_constant={fmt(sum_constant)}", max_abs, max_rel


def constant_product_relation(
    source_values: list[float],
    target_values: list[float],
    rel_tolerance: float,
    abs_tolerance: float,
) -> tuple[str, str, float, float] | None:
    if len(source_values) != len(target_values) or len(source_values) < WINDOW_RELATION_MIN_LEN:
        return None
    if len(set(fmt(v) for v in source_values)) <= 1 or len(set(fmt(v) for v in target_values)) <= 1:
        return None
    if any(abs(s) <= abs_tolerance or abs(t) <= abs_tolerance for s, t in zip(source_values, target_values)):
        return None
    products = [s * t for s, t in zip(source_values, target_values)]
    product_constant = statistics.fmean(products)
    ok, max_abs, max_rel = window_residual_ok(
        products,
        [product_constant for _ in products],
        rel_tolerance,
        abs_tolerance,
    )
    if not ok:
        return None
    if abs(product_constant) <= abs_tolerance:
        return None
    return "target = constant / source", f"product_constant={fmt(product_constant)}", max_abs, max_rel


def binary_window_relation(
    a_values: list[float],
    b_values: list[float],
    target_values: list[float],
    rel_tolerance: float,
    abs_tolerance: float,
) -> tuple[str, str, float, float] | None:
    operations: list[tuple[str, str, list[float]]] = [
        ("target = A + B", "A+B", [a + b for a, b in zip(a_values, b_values)]),
        ("target = A - B", "A-B", [a - b for a, b in zip(a_values, b_values)]),
        ("target = B - A", "B-A", [b - a for a, b in zip(a_values, b_values)]),
        ("target = A * B", "A*B", [a * b for a, b in zip(a_values, b_values)]),
    ]
    if all(abs(b) > abs_tolerance for b in b_values):
        operations.append(("target = A / B", "A/B", [a / b for a, b in zip(a_values, b_values)]))
    if all(abs(a) > abs_tolerance for a in a_values):
        operations.append(("target = B / A", "B/A", [b / a for a, b in zip(a_values, b_values)]))
    for relation, detail, expected in operations:
        if len(set(fmt(v) for v in expected)) <= 1:
            continue
        ok, max_abs, max_rel = window_residual_ok(target_values, expected, rel_tolerance, abs_tolerance)
        if ok:
            return relation, detail, max_abs, max_rel

    basis_operations: list[tuple[str, str, list[float]]] = [
        ("target = constant * (A + B)", "basis=A+B", [a + b for a, b in zip(a_values, b_values)]),
        ("target = constant * (A - B)", "basis=A-B", [a - b for a, b in zip(a_values, b_values)]),
        ("target = constant * (B - A)", "basis=B-A", [b - a for a, b in zip(a_values, b_values)]),
        ("target = constant * A * B", "basis=A*B", [a * b for a, b in zip(a_values, b_values)]),
    ]
    if all(abs(b) > abs_tolerance for b in b_values):
        basis_operations.append(("target = constant * A / B", "basis=A/B", [a / b for a, b in zip(a_values, b_values)]))
    if all(abs(a) > abs_tolerance for a in a_values):
        basis_operations.append(("target = constant * B / A", "basis=B/A", [b / a for a, b in zip(a_values, b_values)]))
    if all(abs(a + b) > abs_tolerance for a, b in zip(a_values, b_values)):
        basis_operations.extend(
            [
                ("target = constant * A / (A + B)", "basis=A/(A+B)", [a / (a + b) for a, b in zip(a_values, b_values)]),
                ("target = constant * B / (A + B)", "basis=B/(A+B)", [b / (a + b) for a, b in zip(a_values, b_values)]),
            ]
        )

    for relation_type, detail_prefix, basis in basis_operations:
        relation = scaled_relation_from_basis(
            basis,
            target_values,
            relation_type,
            detail_prefix,
            rel_tolerance,
            abs_tolerance,
        )
        if relation is not None:
            return relation

    for relation_type, base_values, additive_values, detail_prefix in (
        ("target = A + constant * B", a_values, b_values, "base=A;scaled=B"),
        ("target = B + constant * A", b_values, a_values, "base=B;scaled=A"),
    ):
        residual_target = [t - base for t, base in zip(target_values, base_values)]
        relation = scaled_relation_from_basis(
            additive_values,
            residual_target,
            relation_type,
            detail_prefix,
            rel_tolerance,
            abs_tolerance,
        )
        if relation is not None:
            return relation
    return None


def block_windows(
    block_index: int,
    block: dict[str, Any],
    min_len: int,
    max_len: int,
) -> list[dict[str, Any]]:
    raw = block["values"].split(";") if block.get("values") else []
    cells = block.get("value_cells", "").split(";") if block.get("value_cells") else []
    if len(raw) != len(cells) or len(raw) < min_len:
        return []
    if len(raw) > WINDOW_RELATION_MAX_BLOCK_VALUES:
        return []
    if likely_axis_or_identifier_block(block):
        return []
    keys = [value_key(v) for v in raw]
    values = [to_float(v) for v in raw]
    windows: list[dict[str, Any]] = []
    for length in range(min_len, min(max_len, len(raw)) + 1):
        for start in range(0, len(raw) - length + 1):
            raw_slice = raw[start : start + length]
            key_slice = keys[start : start + length]
            if len(set(key_slice)) <= 1:
                continue
            windows.append(
                {
                    "block_index": block_index,
                    "block": block,
                    "start": start,
                    "length": length,
                    "raw": raw_slice,
                    "values": values[start : start + length],
                    "cells": cells[start : start + length],
                    "signature": "|".join(key_slice),
                }
            )
    return windows


def window_row_cells(row: dict[str, Any]) -> set[str]:
    cells: set[str] = set()
    for key in ("matched_cells_a", "matched_cells_b", "matched_cells_c"):
        for token in str(row.get(key, "")).split(";"):
            token = token.strip()
            if token:
                cells.add(token)
    return cells


def window_relation_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("relation_family", ""),
        row.get("relation_type", ""),
        row.get("relation_detail", "") if row.get("relation_family") == "constant_offset_or_ratio" else "",
        row.get("workbook_a", ""),
        row.get("sheet_a", ""),
        row.get("panel_a", ""),
        row.get("group_a", ""),
        row.get("cell_range_a", ""),
        row.get("panel_b", ""),
        row.get("group_b", ""),
        row.get("cell_range_b", ""),
        row.get("panel_c", ""),
        row.get("group_c", ""),
        row.get("cell_range_c", ""),
    )


def dedupe_window_relation_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep maximal windows while preserving separate non-overlapping candidates."""
    ranked = sorted(
        rows,
        key=lambda row: (
            1 if row.get("risk_hint") == "HIGH-RISK_REVIEW" else 0,
            int(row.get("window_length", 0) or 0),
            1 if row.get("relation_family") == "binary_basic_operation" else 0,
            -float(row.get("max_abs_error", 0) or 0),
        ),
        reverse=True,
    )
    kept: list[dict[str, Any]] = []
    kept_by_key: dict[tuple[Any, ...], list[set[str]]] = defaultdict(list)
    for row in ranked:
        key = window_relation_key(row)
        cells = window_row_cells(row)
        if not cells:
            continue
        if any(cells <= existing for existing in kept_by_key[key]):
            continue
        kept_by_key[key].append(cells)
        kept.append(row)
    for idx, row in enumerate(kept, 1):
        row["match_id"] = f"WINREL-{idx:04d}"
    return kept


def window_identity(window: dict[str, Any]) -> tuple[Any, ...]:
    return (window["block_index"], window["start"], window["length"])


def same_window(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return window_identity(a) == window_identity(b)


def same_layout_blocks(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return (
        a.get("workbook") == b.get("workbook")
        and a.get("sheet") == b.get("sheet")
        and a.get("panel") == b.get("panel")
        and a.get("header_row") == b.get("header_row")
        and a.get("data_rows") == b.get("data_rows")
        and a.get("col_start") == b.get("col_start")
        and a.get("col_end") == b.get("col_end")
    )


def relation_comparison_allowed(a: dict[str, Any], b: dict[str, Any], adjacent_pairs: set[tuple[int, int]]) -> bool:
    if same_window(a, b):
        return False
    a_block = a["block"]
    b_block = b["block"]
    pair = tuple(sorted((int(a["block_index"]), int(b["block_index"]))))
    is_adjacent = pair in adjacent_pairs
    if is_adjacent and comparison_bases(a_block, b_block, True):
        return True
    if relation_role_hint(a_block) == "derived_or_summary_like_label" or relation_role_hint(b_block) == "derived_or_summary_like_label":
        return bool(comparison_bases(a_block, b_block, False))
    return same_layout_blocks(a_block, b_block) and is_adjacent


def block_relation_comparison_allowed(
    a_index: int,
    a_block: dict[str, Any],
    b_index: int,
    b_block: dict[str, Any],
    adjacent_pairs: set[tuple[int, int]],
) -> bool:
    if a_index == b_index:
        return False
    pair = tuple(sorted((int(a_index), int(b_index))))
    is_adjacent = pair in adjacent_pairs
    if is_adjacent and comparison_bases(a_block, b_block, True):
        return True
    if relation_role_hint(a_block) == "derived_or_summary_like_label" or relation_role_hint(b_block) == "derived_or_summary_like_label":
        return bool(comparison_bases(a_block, b_block, False))
    return same_layout_blocks(a_block, b_block) and is_adjacent


def exact_values_key(values: list[float]) -> tuple[str, ...]:
    return tuple(fmt(value) for value in values)


def relation_shape_key(
    values: list[float],
    abs_tolerance: float,
    centered: bool = False,
) -> tuple[float, ...] | None:
    if len(values) < WINDOW_RELATION_MIN_LEN:
        return None
    basis = [value - statistics.fmean(values) for value in values] if centered else list(values)
    max_abs = max((abs(value) for value in basis), default=0.0)
    if max_abs <= abs_tolerance:
        return None
    normalized = [value / max_abs for value in basis]
    for value in normalized:
        if abs(value) > 1e-12:
            if value < 0:
                normalized = [-item for item in normalized]
            break
    return tuple(round(0.0 if abs(value) <= 1e-12 else value, 6) for value in normalized)


def append_map(mapping: dict[tuple[Any, ...], list[dict[str, Any]]], key: tuple[Any, ...] | None, window: dict[str, Any]) -> None:
    if key is not None:
        mapping[key].append(window)


def window_relation_sheet_worker(payload: tuple[list[dict[str, Any]], int, int, float, float, int, int]) -> list[dict[str, Any]]:
    sheet_blocks, min_len, max_len, rel_tolerance, abs_tolerance, max_windows_per_sheet, max_hits_per_sheet = payload
    return same_sheet_window_arithmetic_relations(
        sheet_blocks,
        min_len,
        max_len,
        rel_tolerance,
        abs_tolerance,
        max_windows_per_sheet,
        max_hits_per_sheet,
        workers=1,
    )


def window_relation_length_worker(payload: tuple[list[dict[str, Any]], int, float, float, int]) -> list[dict[str, Any]]:
    sheet_blocks, length, rel_tolerance, abs_tolerance, max_hits_per_sheet = payload
    return same_sheet_window_arithmetic_relations(
        sheet_blocks,
        length,
        length,
        rel_tolerance,
        abs_tolerance,
        max_windows_per_sheet=0,
        max_hits_per_sheet=max_hits_per_sheet,
        workers=1,
    )


def same_sheet_window_arithmetic_relations(
    blocks: list[dict[str, Any]],
    min_len: int,
    max_len: int,
    rel_tolerance: float,
    abs_tolerance: float,
    max_windows_per_sheet: int,
    max_hits_per_sheet: int,
    workers: int,
) -> list[dict[str, Any]]:
    by_sheet: dict[tuple[str, str], list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for idx, block in enumerate(blocks):
        by_sheet[(block["workbook"], block["sheet"])].append((idx, block))

    if workers > 1 and by_sheet:
        payloads = [
            ([block for _, block in sheet_blocks], length, rel_tolerance, abs_tolerance, max_hits_per_sheet)
            for sheet_blocks in by_sheet.values()
            for length in range(min_len, max_len + 1)
        ]
        rows: list[dict[str, Any]] = []
        with ProcessPoolExecutor(max_workers=workers) as executor:
            for sheet_rows in executor.map(window_relation_length_worker, payloads):
                rows.extend(sheet_rows)
        return dedupe_window_relation_rows(rows)

    adjacent_pairs = adjacent_block_pairs(blocks)

    rows: list[dict[str, Any]] = []
    match_counter = 1
    seen: set[tuple[Any, ...]] = set()

    def append_pair_row(
        workbook: str,
        sheet: str,
        source: dict[str, Any],
        target: dict[str, Any],
        relation: tuple[str, str, float, float],
        rationale: str,
        scan_strategy: str,
        hits_for_sheet: int,
    ) -> int:
        nonlocal match_counter
        if max_hits_per_sheet > 0 and hits_for_sheet >= max_hits_per_sheet:
            return hits_for_sheet
        if same_window(source, target):
            return hits_for_sheet
        source_block = source["block"]
        target_block = target["block"]
        relation_type, relation_detail, max_abs, max_rel = relation
        target_role = relation_role_hint(target_block)
        if target_role != "derived_or_summary_like_label" and source["block_index"] >= target["block_index"]:
            return hits_for_sheet

        source_full_len = len(source_block.get("values", "").split(";")) if source_block.get("values") else 0
        target_full_len = len(target_block.get("values", "").split(";")) if target_block.get("values") else 0
        if relation_type == "target = source * constant" and source_full_len == target_full_len:
            source_full_values = [to_float(v) for v in source_block.get("values", "").split(";") if is_number(v)]
            target_full_values = [to_float(v) for v in target_block.get("values", "").split(";") if is_number(v)]
            full_relation = constant_offset_or_ratio_relation(
                source_full_values,
                target_full_values,
                rel_tolerance,
                abs_tolerance,
            )
            if full_relation is not None and full_relation[0] == "target = source * constant":
                return hits_for_sheet
        if (
            relation_type == "target = source * constant"
            and source_full_len == source["length"]
            and target_full_len == target["length"]
        ):
            return hits_for_sheet

        row_key = (
            "pair",
            relation_type,
            relation_detail,
            window_identity(source),
            window_identity(target),
            workbook,
            sheet,
        )
        if row_key in seen:
            return hits_for_sheet
        seen.add(row_key)
        risk_hint, independence_hint, shared_hint = relation_risk_hint(source_block, None, target_block)
        rows.append(
            {
                "match_id": f"WINREL-{match_counter:04d}",
                "relation_family": "constant_offset_or_ratio",
                "relation_type": relation_type,
                "relation_detail": relation_detail,
                "scan_strategy": scan_strategy,
                "risk_hint": risk_hint,
                "independent_condition_hint": independence_hint,
                "shared_calculation_source_hint": shared_hint,
                "workbook_a": workbook,
                "sheet_a": sheet,
                "panel_a": source_block.get("panel", ""),
                "group_a": source_block.get("group", ""),
                "context_a": source_block.get("context_labels", ""),
                "cell_range_a": source_block.get("group_cell_range", ""),
                "matched_cells_a": ";".join(source["cells"]),
                "workbook_b": workbook,
                "sheet_b": sheet,
                "panel_b": target_block.get("panel", ""),
                "group_b": target_block.get("group", ""),
                "context_b": target_block.get("context_labels", ""),
                "cell_range_b": target_block.get("group_cell_range", ""),
                "matched_cells_b": ";".join(target["cells"]),
                "window_length": source["length"],
                "source_start_offset": source["start"] + 1,
                "target_start_offset": target["start"] + 1,
                "source_values": ";".join(source["raw"]),
                "target_values": ";".join(target["raw"]),
                "max_abs_error": fmt(max_abs),
                "max_rel_error": fmt(max_rel),
                "risk_rationale": rationale,
            }
        )
        match_counter += 1
        return hits_for_sheet + 1

    def append_binary_row(
        workbook: str,
        sheet: str,
        source_a: dict[str, Any],
        source_b: dict[str, Any],
        target: dict[str, Any],
        relation: tuple[str, str, float, float],
        scan_strategy: str,
        hits_for_sheet: int,
    ) -> int:
        nonlocal match_counter
        if max_hits_per_sheet > 0 and hits_for_sheet >= max_hits_per_sheet:
            return hits_for_sheet
        if same_window(source_a, target) or same_window(source_b, target) or same_window(source_a, source_b):
            return hits_for_sheet
        source_a_block = source_a["block"]
        source_b_block = source_b["block"]
        target_block = target["block"]
        target_role = relation_role_hint(target_block)
        if target_role != "derived_or_summary_like_label" and target["block_index"] <= max(
            source_a["block_index"],
            source_b["block_index"],
        ):
            return hits_for_sheet
        relation_type, relation_detail, max_abs, max_rel = relation
        row_key = (
            "binary",
            relation_type,
            relation_detail,
            window_identity(source_a),
            window_identity(source_b),
            window_identity(target),
            workbook,
            sheet,
        )
        if row_key in seen:
            return hits_for_sheet
        seen.add(row_key)
        risk_hint, independence_hint, shared_hint = relation_risk_hint(source_a_block, source_b_block, target_block)
        rows.append(
            {
                "match_id": f"WINREL-{match_counter:04d}",
                "relation_family": "binary_basic_operation",
                "relation_type": relation_type,
                "relation_detail": relation_detail,
                "scan_strategy": scan_strategy,
                "risk_hint": risk_hint,
                "independent_condition_hint": independence_hint,
                "shared_calculation_source_hint": shared_hint,
                "workbook_a": workbook,
                "sheet_a": sheet,
                "panel_a": source_a_block.get("panel", ""),
                "group_a": source_a_block.get("group", ""),
                "context_a": source_a_block.get("context_labels", ""),
                "cell_range_a": source_a_block.get("group_cell_range", ""),
                "matched_cells_a": ";".join(source_a["cells"]),
                "workbook_b": workbook,
                "sheet_b": sheet,
                "panel_b": source_b_block.get("panel", ""),
                "group_b": source_b_block.get("group", ""),
                "context_b": source_b_block.get("context_labels", ""),
                "cell_range_b": source_b_block.get("group_cell_range", ""),
                "matched_cells_b": ";".join(source_b["cells"]),
                "workbook_c": workbook,
                "sheet_c": sheet,
                "panel_c": target_block.get("panel", ""),
                "group_c": target_block.get("group", ""),
                "context_c": target_block.get("context_labels", ""),
                "cell_range_c": target_block.get("group_cell_range", ""),
                "matched_cells_c": ";".join(target["cells"]),
                "window_length": source_a["length"],
                "source_a_start_offset": source_a["start"] + 1,
                "source_b_start_offset": source_b["start"] + 1,
                "target_start_offset": target["start"] + 1,
                "source_a_values": ";".join(source_a["raw"]),
                "source_b_values": ";".join(source_b["raw"]),
                "target_values": ";".join(target["raw"]),
                "max_abs_error": fmt(max_abs),
                "max_rel_error": fmt(max_rel),
                "risk_rationale": (
                    "A sliding window of at least three adjacent numeric values can be generated "
                    "from two other same-sheet windows by add/subtract/multiply/divide or a scaled "
                    "basic transform. Keep as WARN when a derived target is plausible; escalate when "
                    "the windows appear to be independent primary measurements."
                ),
            }
        )
        match_counter += 1
        return hits_for_sheet + 1

    for (workbook, sheet), sheet_blocks in by_sheet.items():
        windows: list[dict[str, Any]] = []
        for idx, block in sheet_blocks:
            windows.extend(block_windows(idx, block, min_len, max_len))
            if max_windows_per_sheet > 0 and len(windows) > max_windows_per_sheet:
                break
        if max_windows_per_sheet > 0 and len(windows) > max_windows_per_sheet:
            continue

        hits_for_sheet = 0
        scan_strategy = (
            "exhaustive-small-table"
            if len(windows) <= WINDOW_RELATION_EXHAUSTIVE_WINDOW_THRESHOLD
            else "indexed-large-table"
        )
        allowed_block_neighbors: dict[int, set[int]] = defaultdict(set)
        for left_pos, (left_idx, left_block) in enumerate(sheet_blocks):
            for right_idx, right_block in sheet_blocks[left_pos + 1 :]:
                if block_relation_comparison_allowed(left_idx, left_block, right_idx, right_block, adjacent_pairs):
                    allowed_block_neighbors[left_idx].add(right_idx)
                    allowed_block_neighbors[right_idx].add(left_idx)

        by_len: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for window in windows:
            by_len[window["length"]].append(window)
        for length in sorted(by_len):
            candidates = by_len[length]
            windows_by_block: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for window in candidates:
                windows_by_block[int(window["block_index"])].append(window)
            exact_map: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
            scale_shape_map: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
            centered_shape_map: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
            inverse_shape_map: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
            for window in candidates:
                exact_map[exact_values_key(window["values"])].append(window)
                append_map(scale_shape_map, relation_shape_key(window["values"], abs_tolerance), window)
                append_map(centered_shape_map, relation_shape_key(window["values"], abs_tolerance, centered=True), window)
                if all(abs(value) > abs_tolerance for value in window["values"]):
                    inverse_values = [1 / value for value in window["values"]]
                    append_map(inverse_shape_map, relation_shape_key(inverse_values, abs_tolerance), window)

            for target_idx, target in enumerate(candidates):
                target_block = target["block"]
                target_neighbors = allowed_block_neighbors.get(int(target["block_index"]), set())
                centered_key = relation_shape_key(target["values"], abs_tolerance, centered=True)
                centered_sources = centered_shape_map.get(centered_key, []) if centered_key is not None else []
                for source in centered_sources:
                    if int(source["block_index"]) not in target_neighbors:
                        continue
                    relation = constant_sum_relation(source["values"], target["values"], rel_tolerance, abs_tolerance)
                    if relation is not None:
                        hits_for_sheet = append_pair_row(
                            workbook,
                            sheet,
                            source,
                            target,
                            relation,
                            (
                                "A sliding window of at least three adjacent numeric values forms a constant-sum "
                                "complement with another same-sheet window, equivalent to target = constant - source. "
                                "Keep as WARN when a derived, normalized, percentage-complement, or compositional "
                                "relationship is plausible; escalate when the windows appear to be independent "
                                "primary measurements."
                            ),
                            scan_strategy,
                            hits_for_sheet,
                        )
                    relation = constant_offset_or_ratio_relation(source["values"], target["values"], rel_tolerance, abs_tolerance)
                    if relation is not None and not (
                        relation[0] == "target = source * constant + offset" and "slope=-1" in relation[1]
                    ):
                        hits_for_sheet = append_pair_row(
                            workbook,
                            sheet,
                            source,
                            target,
                            relation,
                            (
                                "A sliding window of at least three adjacent numeric values can be generated "
                                "from another same-sheet window by fixed offset, fixed ratio, affine transform, "
                                "or reciprocal scaling. Keep as WARN when the target looks derived or the relation "
                                "may be documented; escalate when the target and source appear to be independent "
                                "primary measurements."
                            ),
                            scan_strategy,
                            hits_for_sheet,
                        )
                    if max_hits_per_sheet > 0 and hits_for_sheet >= max_hits_per_sheet:
                        break
                if max_hits_per_sheet > 0 and hits_for_sheet >= max_hits_per_sheet:
                    break

                target_scale_key = relation_shape_key(target["values"], abs_tolerance)
                inverse_sources = inverse_shape_map.get(target_scale_key, []) if target_scale_key is not None else []
                for source in inverse_sources:
                    if int(source["block_index"]) not in target_neighbors:
                        continue
                    relation = constant_product_relation(source["values"], target["values"], rel_tolerance, abs_tolerance)
                    if relation is not None:
                        hits_for_sheet = append_pair_row(
                            workbook,
                            sheet,
                            source,
                            target,
                            relation,
                            (
                                "A sliding window of at least three adjacent numeric values forms a constant-product "
                                "relationship with another same-sheet window, equivalent to target = constant / source. "
                                "Keep as WARN when a reciprocal, rate, normalization, or documented transform is plausible; "
                                "escalate when the windows appear to be independent primary measurements."
                            ),
                            scan_strategy,
                            hits_for_sheet,
                        )
                    if max_hits_per_sheet > 0 and hits_for_sheet >= max_hits_per_sheet:
                        break
                if max_hits_per_sheet > 0 and hits_for_sheet >= max_hits_per_sheet:
                    break

                if scan_strategy == "exhaustive-small-table":
                    possible_source_as = [source for source in candidates if not same_window(source, target)]
                else:
                    possible_source_as = [
                        source
                        for neighbor in target_neighbors
                        for source in windows_by_block.get(neighbor, [])
                    ]
                for source_a in possible_source_as:
                    if same_window(source_a, target):
                        continue
                    t_values = target["values"]
                    a_values = source_a["values"]
                    needed_vectors: list[list[float]] = [
                        [t - a for t, a in zip(t_values, a_values)],
                        [a - t for t, a in zip(t_values, a_values)],
                        [t + a for t, a in zip(t_values, a_values)],
                        [t * a for t, a in zip(t_values, a_values)],
                    ]
                    if all(abs(a) > abs_tolerance for a in a_values):
                        needed_vectors.append([t / a for t, a in zip(t_values, a_values)])
                    if all(abs(t) > abs_tolerance for t in t_values):
                        needed_vectors.append([a / t for t, a in zip(t_values, a_values)])
                    for needed in needed_vectors:
                        for source_b in exact_map.get(exact_values_key(needed), []):
                            if not (
                                int(source_b["block_index"]) in allowed_block_neighbors.get(int(source_a["block_index"]), set())
                                or int(source_b["block_index"]) in target_neighbors
                            ):
                                continue
                            relation = binary_window_relation(
                                source_a["values"],
                                source_b["values"],
                                target["values"],
                                rel_tolerance,
                                abs_tolerance,
                            )
                            if relation is not None:
                                hits_for_sheet = append_binary_row(workbook, sheet, source_a, source_b, target, relation, scan_strategy, hits_for_sheet)
                            if max_hits_per_sheet > 0 and hits_for_sheet >= max_hits_per_sheet:
                                break
                        if max_hits_per_sheet > 0 and hits_for_sheet >= max_hits_per_sheet:
                            break
                    if max_hits_per_sheet > 0 and hits_for_sheet >= max_hits_per_sheet:
                        break

                    residual_key = relation_shape_key([t - a for t, a in zip(t_values, a_values)], abs_tolerance)
                    residual_sources = scale_shape_map.get(residual_key, []) if residual_key is not None else []
                    for source_b in residual_sources:
                        if not (
                            int(source_b["block_index"]) in allowed_block_neighbors.get(int(source_a["block_index"]), set())
                            or int(source_b["block_index"]) in target_neighbors
                        ):
                            continue
                        relation = binary_window_relation(
                            source_a["values"],
                            source_b["values"],
                            target["values"],
                            rel_tolerance,
                            abs_tolerance,
                        )
                        if relation is not None:
                            hits_for_sheet = append_binary_row(workbook, sheet, source_a, source_b, target, relation, scan_strategy, hits_for_sheet)
                        if max_hits_per_sheet > 0 and hits_for_sheet >= max_hits_per_sheet:
                            break
                    if max_hits_per_sheet > 0 and hits_for_sheet >= max_hits_per_sheet:
                        break

            if max_hits_per_sheet > 0 and hits_for_sheet >= max_hits_per_sheet:
                break

            for source_a_idx, source_a in enumerate(candidates):
                if scan_strategy == "exhaustive-small-table":
                    possible_source_bs = candidates[source_a_idx + 1 :]
                else:
                    possible_source_bs = [
                        source
                        for neighbor in allowed_block_neighbors.get(int(source_a["block_index"]), set())
                        for source in windows_by_block.get(neighbor, [])
                        if window_identity(source) > window_identity(source_a)
                    ]
                for source_b in possible_source_bs:
                    if window_identity(source_b) <= window_identity(source_a):
                        continue
                    if same_window(source_a, source_b):
                        continue
                    a_values = source_a["values"]
                    b_values = source_b["values"]
                    basis_vectors: list[list[float]] = [
                        [a + b for a, b in zip(a_values, b_values)],
                        [a - b for a, b in zip(a_values, b_values)],
                        [b - a for a, b in zip(a_values, b_values)],
                        [a * b for a, b in zip(a_values, b_values)],
                    ]
                    if all(abs(b) > abs_tolerance for b in b_values):
                        basis_vectors.append([a / b for a, b in zip(a_values, b_values)])
                    if all(abs(a) > abs_tolerance for a in a_values):
                        basis_vectors.append([b / a for a, b in zip(a_values, b_values)])
                    if all(abs(a + b) > abs_tolerance for a, b in zip(a_values, b_values)):
                        basis_vectors.extend(
                            [
                                [a / (a + b) for a, b in zip(a_values, b_values)],
                                [b / (a + b) for a, b in zip(a_values, b_values)],
                            ]
                        )
                    for basis in basis_vectors:
                        basis_key = relation_shape_key(basis, abs_tolerance)
                        if basis_key is None:
                            continue
                        possible_targets = scale_shape_map.get(basis_key, [])
                        for target in possible_targets:
                            if not (
                                int(target["block_index"]) in allowed_block_neighbors.get(int(source_a["block_index"]), set())
                                or int(target["block_index"]) in allowed_block_neighbors.get(int(source_b["block_index"]), set())
                            ):
                                continue
                            relation = binary_window_relation(
                                source_a["values"],
                                source_b["values"],
                                target["values"],
                                rel_tolerance,
                                abs_tolerance,
                            )
                            if relation is not None:
                                hits_for_sheet = append_binary_row(workbook, sheet, source_a, source_b, target, relation, scan_strategy, hits_for_sheet)
                            if max_hits_per_sheet > 0 and hits_for_sheet >= max_hits_per_sheet:
                                break
                        if max_hits_per_sheet > 0 and hits_for_sheet >= max_hits_per_sheet:
                            break
                    if max_hits_per_sheet > 0 and hits_for_sheet >= max_hits_per_sheet:
                        break
                if max_hits_per_sheet > 0 and hits_for_sheet >= max_hits_per_sheet:
                    break
            if max_hits_per_sheet > 0 and hits_for_sheet >= max_hits_per_sheet:
                break
    return dedupe_window_relation_rows(rows)


def audit_workbooks(
    inputs: list[Path],
    out: Path,
    ap_tolerance: float,
    pair_sum_tolerance: float,
    min_sequence_len: int,
    short_sequence_len: int,
    scaled_sequence_len: int,
    scale_rel_tolerance: float,
    scale_abs_tolerance: float,
    window_relation_min_len: int,
    window_relation_max_len: int,
    window_relation_rel_tolerance: float,
    window_relation_abs_tolerance: float,
    window_relation_max_windows_per_sheet: int,
    window_relation_max_hits_per_sheet: int,
    window_relation_workers: int,
    matrix_min_rows: int,
    matrix_min_cols: int,
    matrix_min_cells: int,
    matrix_match_fraction: float,
) -> None:
    sheet_rows: list[dict[str, Any]] = []
    label_rows: list[dict[str, Any]] = []
    numeric_rows: list[dict[str, Any]] = []
    digital_rows: list[dict[str, Any]] = []
    all_blocks: list[dict[str, Any]] = []
    matrix_hits: list[dict[str, Any]] = []
    sheet_dir = out / "sheet_csv"
    sheet_dir.mkdir(parents=True, exist_ok=True)
    for workbook in inputs:
        for sheet, payload in read_xlsx(workbook).items():
            matrix = payload["matrix"]
            formula_count = payload["formula_count"]
            rows = len(matrix)
            cols = max((len(r) for r in matrix), default=0)
            nonempty_cells = numeric_cells = text_cells = long_decimal_cells_ge_3 = long_decimal_cells_ge_8 = 0
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
                        if dp >= LONG_DECIMAL_PLACES:
                            long_decimal_cells_ge_3 += 1
                        if dp >= 8:
                            long_decimal_cells_ge_8 += 1
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
                                "issue_flags": f"long_decimal_precision_ge_{LONG_DECIMAL_PLACES}" if dp >= LONG_DECIMAL_PLACES else "",
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
                    "long_decimal_cells_ge_3": long_decimal_cells_ge_3,
                    "long_decimal_cells_ge_8": long_decimal_cells_ge_8,
                }
            )
            if sheet_values:
                digital_rows.append(digital_distribution_summary(workbook.name, sheet, "sheet", "", "", sheet_raw, sheet_values))
            with (sheet_dir / f"{safe_name(workbook.stem)}__{safe_name(sheet)}.csv").open("w", newline="", encoding="utf-8") as handle:
                csv.writer(handle).writerows(matrix)
            matrix_hits.extend(
                duplicate_numeric_matrix_blocks(
                    workbook.name,
                    sheet,
                    matrix,
                    min_rows=matrix_min_rows,
                    min_cols=matrix_min_cols,
                    min_cells=matrix_min_cells,
                    min_match_fraction=matrix_match_fraction,
                )
            )
            blocks = extract_group_blocks(workbook.name, sheet, matrix, ap_tolerance, pair_sum_tolerance)
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
    exact_repeats = exact_long_decimal_repeats(numeric_rows)
    csv_write(out / "exact_long_decimal_repeats.csv", exact_repeats)
    csv_write(out / "digital_distribution_summary.csv", digital_rows)
    csv_write(out / "group_block_summary.csv", all_blocks)
    csv_write(out / "arithmetic_progression_blocks.csv", [b for b in all_blocks if "near_exact_arithmetic_progression" in b["issue_flags"]])
    csv_write(out / "constant_pair_sum_blocks.csv", [b for b in all_blocks if "constant_adjacent_pair_sum_pattern" in b["issue_flags"]])
    csv_write(out / "duplicate_numeric_sequences.csv", duplicate_sequences(all_blocks, min_sequence_len))
    short_hits = short_duplicate_sequences(all_blocks, short_sequence_len)
    csv_write(out / "short_duplicate_numeric_sequences.csv", short_hits)
    scaled_hits = scaled_numeric_sequence_matches(all_blocks, scaled_sequence_len, scale_rel_tolerance, scale_abs_tolerance)
    csv_write(out / "scaled_numeric_sequence_blocks.csv", scaled_hits)
    window_relation_hits = same_sheet_window_arithmetic_relations(
        all_blocks,
        window_relation_min_len,
        window_relation_max_len,
        window_relation_rel_tolerance,
        window_relation_abs_tolerance,
        window_relation_max_windows_per_sheet,
        window_relation_max_hits_per_sheet,
        window_relation_workers,
    )
    csv_write(out / "window_arithmetic_relation_candidates.csv", window_relation_hits)
    csv_write(out / "duplicate_numeric_matrix_blocks.csv", matrix_hits)
    print(f"workbooks={len(inputs)}")
    print(f"sheets={len(sheet_rows)}")
    print(f"numeric_cells={len(numeric_rows)}")
    print(f"digital_distribution_rows={len(digital_rows)}")
    print(f"group_blocks={len(all_blocks)}")
    print(f"arithmetic_progression_blocks={sum('near_exact_arithmetic_progression' in b['issue_flags'] for b in all_blocks)}")
    print(f"constant_pair_sum_blocks={sum('constant_adjacent_pair_sum_pattern' in b['issue_flags'] for b in all_blocks)}")
    print(f"exact_long_decimal_repeats={len(exact_repeats)}")
    print(f"short_duplicate_numeric_sequences={len(short_hits)}")
    print(f"scaled_numeric_sequence_blocks={len(scaled_hits)}")
    print(f"window_arithmetic_relation_candidates={len(window_relation_hits)}")
    print(f"duplicate_numeric_matrix_blocks={len(matrix_hits)}")
    print(f"out={out}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="xlsx file or directory containing xlsx files")
    parser.add_argument("--out", default="source_data_workbook_audit", help="Output directory")
    parser.add_argument("--ap-tolerance", type=float, default=0.001, help="Maximum step deviation for arithmetic progression flag")
    parser.add_argument(
        "--pair-sum-tolerance",
        type=float,
        default=1e-9,
        help="Maximum deviation for constant adjacent-pair sum clustering",
    )
    parser.add_argument("--min-sequence-len", type=int, default=3, help="Minimum sequence length for duplicate-sequence hashing")
    parser.add_argument(
        "--short-sequence-len",
        type=int,
        default=SHORT_SEQUENCE_LEN,
        help="Minimum length for same-panel/cross-panel short vector and local consecutive duplicate screens",
    )
    parser.add_argument(
        "--scaled-sequence-len",
        type=int,
        default=SCALED_SEQUENCE_LEN,
        help="Minimum aligned vector length for fixed-ratio scaled sequence screens",
    )
    parser.add_argument(
        "--scale-rel-tolerance",
        type=float,
        default=SCALE_REL_TOLERANCE,
        help="Maximum relative residual for fixed-ratio scaled sequence screens",
    )
    parser.add_argument(
        "--scale-abs-tolerance",
        type=float,
        default=SCALE_ABS_TOLERANCE,
        help="Maximum absolute residual for fixed-ratio scaled sequence screens",
    )
    parser.add_argument(
        "--window-relation-min-len",
        type=int,
        default=WINDOW_RELATION_MIN_LEN,
        help="Minimum adjacent-window length for same-sheet arithmetic relation candidates",
    )
    parser.add_argument(
        "--window-relation-max-len",
        type=int,
        default=WINDOW_RELATION_MAX_LEN,
        help="Maximum adjacent-window length for same-sheet arithmetic relation candidates",
    )
    parser.add_argument(
        "--window-relation-rel-tolerance",
        type=float,
        default=WINDOW_RELATION_REL_TOLERANCE,
        help="Maximum relative residual for same-sheet arithmetic relation candidates",
    )
    parser.add_argument(
        "--window-relation-abs-tolerance",
        type=float,
        default=WINDOW_RELATION_ABS_TOLERANCE,
        help="Maximum absolute residual for same-sheet arithmetic relation candidates",
    )
    parser.add_argument(
        "--window-relation-max-windows-per-sheet",
        type=int,
        default=WINDOW_RELATION_MAX_WINDOWS_PER_SHEET,
        help="Skip the window-relation screen for sheets producing more windows than this cap; 0 disables the cap",
    )
    parser.add_argument(
        "--window-relation-max-hits-per-sheet",
        type=int,
        default=WINDOW_RELATION_MAX_HITS_PER_SHEET,
        help="Maximum same-sheet arithmetic relation candidates retained per sheet; 0 retains all hits before de-duplication",
    )
    parser.add_argument(
        "--window-relation-workers",
        type=int,
        default=WINDOW_RELATION_WORKERS,
        help="Parallel worker processes for same-sheet window relation scans",
    )
    parser.add_argument(
        "--matrix-min-rows",
        type=int,
        default=MATRIX_BLOCK_MIN_ROWS,
        help="Minimum rows for duplicate numeric matrix and continuous submatrix screens",
    )
    parser.add_argument(
        "--matrix-min-cols",
        type=int,
        default=MATRIX_BLOCK_MIN_COLS,
        help="Minimum columns for duplicate numeric matrix and continuous submatrix screens",
    )
    parser.add_argument(
        "--matrix-min-cells",
        type=int,
        default=MATRIX_BLOCK_MIN_CELLS,
        help="Minimum matched cells for duplicate numeric matrix and continuous submatrix screens",
    )
    parser.add_argument(
        "--matrix-match-fraction",
        type=float,
        default=MATRIX_BLOCK_MIN_MATCH_FRACTION,
        help=(
            "Minimum whole-matrix matched-cell fraction for same-sheet duplicate "
            "numeric matrix block screens; continuous exact submatrices are also "
            "retained when they meet the minimum row/column/cell thresholds"
        ),
    )
    args = parser.parse_args()

    src = Path(args.input)
    if src.is_dir():
        inputs = sorted(src.glob("*.xlsx"))
    else:
        inputs = [src]
    if not inputs:
        raise SystemExit("No xlsx files found.")
    audit_workbooks(
        inputs,
        Path(args.out),
        args.ap_tolerance,
        args.pair_sum_tolerance,
        args.min_sequence_len,
        args.short_sequence_len,
        args.scaled_sequence_len,
        args.scale_rel_tolerance,
        args.scale_abs_tolerance,
        args.window_relation_min_len,
        args.window_relation_max_len,
        args.window_relation_rel_tolerance,
        args.window_relation_abs_tolerance,
        args.window_relation_max_windows_per_sheet,
        args.window_relation_max_hits_per_sheet,
        max(1, args.window_relation_workers),
        args.matrix_min_rows,
        args.matrix_min_cols,
        args.matrix_min_cells,
        args.matrix_match_fraction,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
