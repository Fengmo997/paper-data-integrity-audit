#!/usr/bin/env python3
r"""Run the current standard paper-data-integrity-audit pipeline.

Example:

    py scripts/run_standard_audit.py C:\path\paper-or-raw-data-dir --out audit_results\rescan_full --scan-pdfs

This wrapper exists so new computers use the same parameters as the current
validated workflow.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPT_DIR = SKILL_DIR / "scripts"
CONTRACT_PATH = SKILL_DIR / "resources" / "reproducibility_contract.json"
DEFAULT_EXCLUDED_DIR_NAMES = {
    ".git",
    "__pycache__",
    "audit_results",
    "source_data_scan",
    "pdf_image_scan",
    "visual_report",
    "_standard_xlsx_inputs",
}


def load_contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def run(cmd: list[str], dry_run: bool = False) -> None:
    print("+ " + " ".join(str(x) for x in cmd))
    if dry_run:
        return
    proc = subprocess.run(cmd, text=True)
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)


def py_cmd(script_name: str) -> list[str]:
    return [sys.executable, str(SCRIPT_DIR / script_name)]


def append_args(cmd: list[str], args_by_name: dict[str, str]) -> list[str]:
    for key, value in args_by_name.items():
        cmd.extend([key, str(value)])
    return cmd


def under_excluded_dir(path: Path, root: Path) -> bool:
    try:
        rel_parts = path.relative_to(root).parts
    except ValueError:
        return False
    return any(part in DEFAULT_EXCLUDED_DIR_NAMES for part in rel_parts[:-1])


def xlsx_inputs(path: Path, include_generated_dirs: bool = False) -> list[Path]:
    if path.is_file() and path.suffix.lower() == ".xlsx":
        return [path]
    if path.is_dir():
        return sorted(
            p
            for p in path.rglob("*.xlsx")
            if not p.name.startswith("~$")
            and (include_generated_dirs or not under_excluded_dir(p, path))
        )
    return []


def pdf_inputs(path: Path, include_generated_dirs: bool = False) -> list[Path]:
    if path.is_file() and path.suffix.lower() == ".pdf":
        return [path]
    if path.is_dir():
        return sorted(
            p
            for p in path.rglob("*.pdf")
            if include_generated_dirs or not under_excluded_dir(p, path)
        )
    return []


def unique_existing(paths: Iterable[Path]) -> list[Path]:
    seen: set[Path] = set()
    out: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved in seen or not path.exists():
            continue
        seen.add(resolved)
        out.append(path)
    return out


def stage_xlsx_inputs(paths: list[Path], staging_dir: Path, dry_run: bool = False) -> Path | None:
    if not paths:
        return None
    if len(paths) == 1 and paths[0].is_file():
        return paths[0]
    staging_dir.mkdir(parents=True, exist_ok=True)
    for index, source in enumerate(paths, start=1):
        dest = staging_dir / f"{index:03d}_{source.name}"
        print(f"stage xlsx {source} -> {dest}")
        if not dry_run:
            shutil.copy2(source, dest)
    return staging_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Paper/source-data directory, xlsx file, or PDF")
    parser.add_argument("--out", type=Path, default=Path("audit_results/standard_full_audit"), help="Audit output directory")
    parser.add_argument("--scan-pdfs", action="store_true", help="Run PDF/image scans for PDFs found under input")
    parser.add_argument("--pdf", type=Path, action="append", default=[], help="Additional PDF to scan; repeatable")
    parser.add_argument("--skip-source", action="store_true", help="Skip source-data workbook audit")
    parser.add_argument("--skip-formula", action="store_true", help="Skip formula recheck")
    parser.add_argument("--skip-visual", action="store_true", help="Skip visual report build")
    parser.add_argument("--strict-preflight", action="store_true", help="Require locked package versions before running")
    parser.add_argument("--include-generated-dirs", action="store_true", help="Include audit_results and other generated directories when discovering input files")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without running them")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    contract = load_contract()
    out_dir = args.out
    source_dir = out_dir / "source_data_scan"
    pdf_root = out_dir / "pdf_image_scan"
    visual_dir = out_dir / "visual_report"
    out_dir.mkdir(parents=True, exist_ok=True)

    preflight = py_cmd("preflight_check.py")
    preflight.extend(["--write-manifest", str(out_dir / "audit_environment_manifest.json")])
    if args.strict_preflight:
        preflight.append("--strict-versions")
    run(preflight, args.dry_run)

    xlsx_files = xlsx_inputs(args.input, args.include_generated_dirs)
    staged_xlsx_input = stage_xlsx_inputs(xlsx_files, out_dir / "_standard_xlsx_inputs", args.dry_run) if xlsx_files else None
    discovered_pdfs = pdf_inputs(args.input, args.include_generated_dirs) if args.scan_pdfs else []
    pdf_files = unique_existing([*discovered_pdfs, *args.pdf])
    pdf_scan_dirs: list[Path] = []

    if not args.skip_source:
        if not xlsx_files:
            print("No xlsx files found; source-data workbook audit skipped.")
        else:
            source_cmd = py_cmd("source_data_workbook_audit.py")
            source_cmd.extend([str(staged_xlsx_input), "--out", str(source_dir)])
            append_args(source_cmd, contract["source_data_workbook_audit"]["standard_args"])
            run(source_cmd, args.dry_run)

    if not args.skip_formula and xlsx_files:
        formula_cmd = py_cmd("formula_recheck.py")
        formula_cmd.extend([str(staged_xlsx_input), "--out", str(source_dir / "formula_recheck.csv")])
        append_args(formula_cmd, contract["formula_recheck"]["standard_args"])
        run(formula_cmd, args.dry_run)

    if args.scan_pdfs or args.pdf:
        for index, pdf in enumerate(pdf_files, start=1):
            scan_out = pdf_root / f"{index:02d}_{pdf.stem[:80]}"
            pdf_scan_dirs.append(scan_out)
            pdf_cmd = py_cmd("pdf_image_integrity_scan.py")
            pdf_cmd.extend([str(pdf), "--out", str(scan_out)])
            append_args(pdf_cmd, contract["pdf_image_integrity_scan"]["standard_args"])
            run(pdf_cmd, args.dry_run)

    if not args.skip_visual:
        visual_cmd = py_cmd("build_visual_audit_report.py")
        visual_cmd.extend(["--audit-dir", str(out_dir), "--out", str(visual_dir)])
        if source_dir.exists() or args.dry_run:
            visual_cmd.extend(["--source-scan", str(source_dir)])
        for scan_dir in pdf_scan_dirs:
            visual_cmd.extend(["--pdf-scan", str(scan_dir)])
        append_args(visual_cmd, contract["visual_report"]["standard_args"])
        run(visual_cmd, args.dry_run)

    print(f"standard_audit_out={out_dir}")
    if not args.skip_visual:
        print(f"visual_report={visual_dir / 'visual_report.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
