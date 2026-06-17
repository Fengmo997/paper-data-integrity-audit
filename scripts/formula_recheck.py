#!/usr/bin/env python3
"""Recalculate common Excel formulas against cached values.

This is a conservative source-data audit helper. It supports common formula
patterns seen in source-data sheets: AVERAGE, STDEV/STDEV.S, SUM, direct
cell/range references, absolute references, shared-formula translation, and
basic arithmetic expressions. Unsupported formulas are reported as UNSUPPORTED
rather than treated as mismatches.
"""

from __future__ import annotations

import argparse
import csv
import math
import re
import statistics
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
FORMULA_TOLERANCE = 1e-9


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
    match = re.fullmatch(r"\$?([A-Za-z]+)\$?(\d+)", ref)
    if not match:
        raise ValueError(f"Bad cell reference: {ref}")
    return int(match.group(2)), col_to_num(match.group(1))


def rc_to_ref(row: int, col: int) -> str:
    return f"{num_to_col(col)}{row}"


def translate_ref(ref: str, row_delta: int, col_delta: int) -> str:
    match = re.fullmatch(r"(\$?)([A-Za-z]+)(\$?)(\d+)", ref)
    if not match:
        return ref
    abs_col, col, abs_row, row = match.groups()
    new_col = col_to_num(col)
    new_row = int(row)
    if not abs_col:
        new_col += col_delta
    if not abs_row:
        new_row += row_delta
    return f"{'$' if abs_col else ''}{num_to_col(new_col)}{'$' if abs_row else ''}{new_row}"


def translate_formula(formula: str, origin: str, target: str) -> str:
    origin_row, origin_col = ref_to_rc(origin)
    target_row, target_col = ref_to_rc(target)
    row_delta = target_row - origin_row
    col_delta = target_col - origin_col

    def repl(match: re.Match[str]) -> str:
        return translate_ref(match.group(0), row_delta, col_delta)

    return re.sub(r"\$?[A-Za-z]{1,3}\$?\d+", repl, formula)


def parse_float(text: str) -> float | None:
    try:
        value = float(str(text).strip())
    except ValueError:
        return None
    return value if math.isfinite(value) else None


@dataclass
class FormulaCell:
    workbook: str
    sheet: str
    cell: str
    formula: str
    cached_value: str
    shared_index: str
    shared_ref: str


def sheet_paths(zf: zipfile.ZipFile) -> list[tuple[str, str]]:
    workbook_xml = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    rel_map = {
        rel.attrib["Id"]: rel.attrib["Target"].replace("\\", "/")
        for rel in rels
        if rel.attrib.get("Type", "").endswith("/worksheet")
    }
    paths: list[tuple[str, str]] = []
    for sheet in workbook_xml.findall(f"{NS_MAIN}sheets/{NS_MAIN}sheet"):
        name = sheet.attrib["name"]
        rel_id = sheet.attrib.get(f"{NS_REL}id")
        target = rel_map.get(rel_id or "")
        if not target:
            continue
        target = target.lstrip("/")
        if not target.startswith("xl/"):
            target = "xl/" + target
        paths.append((name, target))
    return paths


def shared_strings(zf: zipfile.ZipFile) -> list[str]:
    path = "xl/sharedStrings.xml"
    if path not in zf.namelist():
        return []
    root = ET.fromstring(zf.read(path))
    values: list[str] = []
    for si in root.findall(f"{NS_MAIN}si"):
        values.append("".join(t.text or "" for t in si.findall(f'.//{NS_MAIN}t')))
    return values


def decode_value(cell: ET.Element, shared: list[str]) -> str:
    data_type = cell.attrib.get("t", "")
    node = cell.find(f"{NS_MAIN}v")
    if node is None:
        return ""
    raw = node.text or ""
    if data_type == "s":
        try:
            idx = int(raw)
        except ValueError:
            return ""
        return shared[idx] if 0 <= idx < len(shared) else ""
    return raw


def read_workbook(path: Path) -> tuple[dict[str, dict[str, str]], list[FormulaCell]]:
    values_by_sheet: dict[str, dict[str, str]] = {}
    formulas: list[FormulaCell] = []
    with zipfile.ZipFile(path) as zf:
        shared = shared_strings(zf)
        for sheet_name, sheet_path in sheet_paths(zf):
            root = ET.fromstring(zf.read(sheet_path))
            values: dict[str, str] = {}
            shared_origins: dict[str, tuple[str, str]] = {}
            sheet_formulas: list[FormulaCell] = []
            for c in root.findall(f".//{NS_MAIN}c"):
                ref = c.attrib.get("r", "")
                if not ref:
                    continue
                value = decode_value(c, shared)
                values[ref] = value
                formula_node = c.find(f"{NS_MAIN}f")
                if formula_node is None:
                    continue
                formula = formula_node.text or ""
                shared_index = formula_node.attrib.get("si", "")
                shared_ref = formula_node.attrib.get("ref", "")
                formula_type = formula_node.attrib.get("t", "")
                if formula_type == "shared" and formula and shared_index:
                    shared_origins[shared_index] = (ref, formula)
                sheet_formulas.append(
                    FormulaCell(
                        workbook=path.name,
                        sheet=sheet_name,
                        cell=ref,
                        formula=formula,
                        cached_value=value,
                        shared_index=shared_index,
                        shared_ref=shared_ref,
                    )
                )
            for item in sheet_formulas:
                if not item.formula and item.shared_index in shared_origins:
                    origin_ref, origin_formula = shared_origins[item.shared_index]
                    item.formula = translate_formula(origin_formula, origin_ref, item.cell)
            values_by_sheet[sheet_name] = values
            formulas.extend(sheet_formulas)
    return values_by_sheet, formulas


def range_values(values: dict[str, str], range_ref: str) -> list[float]:
    left, right = range_ref.split(":", 1)
    r1, c1 = ref_to_rc(left.replace("$", ""))
    r2, c2 = ref_to_rc(right.replace("$", ""))
    if r1 > r2:
        r1, r2 = r2, r1
    if c1 > c2:
        c1, c2 = c2, c1
    out: list[float] = []
    for row in range(r1, r2 + 1):
        for col in range(c1, c2 + 1):
            value = parse_float(values.get(rc_to_ref(row, col), ""))
            if value is not None:
                out.append(value)
    return out


def eval_formula(formula: str, values: dict[str, str]) -> tuple[float | None, str]:
    formula = formula.strip()
    if not formula:
        return None, "UNSUPPORTED_EMPTY_OR_SHARED_WITHOUT_ORIGIN"
    if formula.startswith("="):
        formula = formula[1:]

    func_match = re.fullmatch(r"([A-Za-z.]+)\((.*)\)", formula)
    if func_match:
        func = func_match.group(1).upper()
        arg = func_match.group(2).strip()
        vals: list[float] = []
        for part in [p.strip() for p in arg.split(",") if p.strip()]:
            if ":" in part:
                vals.extend(range_values(values, part))
            else:
                ref_value = values.get(part.replace("$", ""))
                parsed = parse_float(ref_value if ref_value is not None else part)
                if parsed is not None:
                    vals.append(parsed)
        if not vals:
            return None, "UNSUPPORTED_NO_NUMERIC_ARGUMENTS"
        if func == "AVERAGE":
            return statistics.fmean(vals), "OK"
        if func in {"STDEV", "STDEV.S"}:
            if len(vals) < 2:
                return None, "UNSUPPORTED_STDEV_N_LT_2"
            return statistics.stdev(vals), "OK"
        if func in {"STDEVP", "STDEV.P"}:
            if len(vals) < 1:
                return None, "UNSUPPORTED_STDEVP_N_LT_1"
            return statistics.pstdev(vals), "OK"
        if func == "SUM":
            return sum(vals), "OK"
        return None, f"UNSUPPORTED_FUNCTION_{func}"

    expr = formula
    if re.search(r"[^0-9A-Za-z_$.:+\-*/(). ]", expr):
        return None, "UNSUPPORTED_EXPRESSION_CHARACTERS"

    def repl_range(match: re.Match[str]) -> str:
        vals = range_values(values, match.group(0))
        return str(sum(vals))

    expr = re.sub(r"\$?[A-Za-z]{1,3}\$?\d+:\$?[A-Za-z]{1,3}\$?\d+", repl_range, expr)

    def repl_cell(match: re.Match[str]) -> str:
        ref = match.group(0).replace("$", "")
        parsed = parse_float(values.get(ref, ""))
        if parsed is None:
            raise ValueError(f"Non-numeric reference {ref}")
        return str(parsed)

    try:
        expr = re.sub(r"\$?[A-Za-z]{1,3}\$?\d+", repl_cell, expr)
        if re.search(r"[A-Za-z_]", expr):
            return None, "UNSUPPORTED_EXPRESSION_TOKEN"
        value = eval(expr, {"__builtins__": {}}, {})
    except Exception as exc:
        return None, f"UNSUPPORTED_EVAL_{type(exc).__name__}"
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None, "UNSUPPORTED_NON_NUMERIC_RESULT"
    return result if math.isfinite(result) else None, "OK"


def audit_formulas(path: Path, tolerance: float) -> list[dict[str, Any]]:
    values_by_sheet, formulas = read_workbook(path)
    rows: list[dict[str, Any]] = []
    for item in formulas:
        values = values_by_sheet.get(item.sheet, {})
        recalculated, status = eval_formula(item.formula, values)
        cached = parse_float(item.cached_value)
        if status != "OK":
            verdict = status
            diff = ""
        elif cached is None or recalculated is None:
            verdict = "UNSUPPORTED_NON_NUMERIC_CACHED_OR_RESULT"
            diff = ""
        else:
            diff_value = abs(cached - recalculated)
            diff = f"{diff_value:.12g}"
            verdict = "MATCH" if diff_value <= tolerance else "MISMATCH"
        rows.append(
            {
                "workbook": item.workbook,
                "sheet": item.sheet,
                "cell": item.cell,
                "formula": item.formula,
                "cached_value": item.cached_value,
                "recalculated_value": f"{recalculated:.15g}" if recalculated is not None else "",
                "abs_diff": diff,
                "tolerance": tolerance,
                "verdict": verdict,
                "shared_index": item.shared_index,
                "shared_ref": item.shared_ref,
            }
        )
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "workbook",
        "sheet",
        "cell",
        "formula",
        "cached_value",
        "recalculated_value",
        "abs_diff",
        "tolerance",
        "verdict",
        "shared_index",
        "shared_ref",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="xlsx file or directory containing xlsx files")
    parser.add_argument("--out", type=Path, default=Path("formula_recheck.csv"))
    parser.add_argument("--tolerance", type=float, default=FORMULA_TOLERANCE)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    paths = sorted(args.input.glob("*.xlsx")) if args.input.is_dir() else [args.input]
    rows: list[dict[str, Any]] = []
    for path in paths:
        rows.extend(audit_formulas(path, args.tolerance))
    write_csv(args.out, rows)
    verdict_counts: dict[str, int] = {}
    for row in rows:
        verdict_counts[row["verdict"]] = verdict_counts.get(row["verdict"], 0) + 1
    print(f"Wrote {len(rows)} formula checks to {args.out}")
    for verdict, count in sorted(verdict_counts.items()):
        print(f"{verdict}={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
