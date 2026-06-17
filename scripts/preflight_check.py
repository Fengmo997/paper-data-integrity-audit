#!/usr/bin/env python3
"""Preflight checks for reproducible paper-data-integrity-audit runs.

Run this after installing the skill on a new computer:

    py scripts/preflight_check.py

The script checks the dependency versions, required files, and key source-code
markers that define the current audit/report behavior.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


SKILL_DIR = Path(__file__).resolve().parents[1]
CONTRACT_PATH = SKILL_DIR / "resources" / "reproducibility_contract.json"

PACKAGE_IMPORTS = {
    "pandas": "pandas",
    "openpyxl": "openpyxl",
    "PyMuPDF": "fitz",
    "pillow": "PIL",
    "numpy": "numpy",
    "opencv-python-headless": "cv2",
}

REQUIRED_FILES = [
    "SKILL.md",
    "README.md",
    "requirements.txt",
    "requirements-lock.txt",
    "resources/reproducibility_contract.json",
    "scripts/source_data_workbook_audit.py",
    "scripts/pdf_image_integrity_scan.py",
    "scripts/build_visual_audit_report.py",
    "scripts/formula_recheck.py",
    "scripts/preflight_check.py",
    "scripts/run_standard_audit.py",
]

SOURCE_MARKERS = {
    "scripts/source_data_workbook_audit.py": [
        "LONG_DECIMAL_PLACES = 3",
        "WINDOW_RELATION_MIN_LEN = 3",
        "WINDOW_RELATION_MAX_LEN = 6",
        "WINDOW_RELATION_MAX_WINDOWS_PER_SHEET = 0",
        "WINDOW_RELATION_MAX_HITS_PER_SHEET = 0",
        "MATRIX_BLOCK_MIN_MATCH_FRACTION = 0.80",
    ],
    "scripts/build_visual_audit_report.py": [
        "DEFAULT_MAX_PER_TYPE = 0",
        "data-highlight-map",
        "style = map[issue] || {{}}",
        "return palette_for(index + 3)",
        "return palette_for(index + 6)",
        "full_csv_visualization.html",
        "lightboxViewport.addEventListener(\"wheel\"",
        "applyLightboxTransform",
        "startLightboxDrag",
    ],
    "scripts/pdf_image_integrity_scan.py": [
        "default=80",
        "default=5",
        "default=8",
        "default=150",
        "default=224",
        "default=18.0",
        "default=248.0",
    ],
}


def load_contract() -> dict[str, Any]:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def version_of(import_name: str) -> str:
    module = importlib.import_module(import_name)
    return str(getattr(module, "__version__", "unknown"))


def distribution_version(package_name: str) -> str:
    return importlib.metadata.version(package_name)


def check_dependencies(contract: dict[str, Any], strict: bool) -> tuple[list[str], list[str]]:
    ok: list[str] = []
    problems: list[str] = []
    pinned = contract["pinned_python_packages"]
    for package, expected in pinned.items():
        import_name = PACKAGE_IMPORTS[package]
        try:
            actual = distribution_version(package)
            import_version = version_of(import_name)
        except Exception as exc:  # pragma: no cover - diagnostic path
            problems.append(f"MISSING {package}: {type(exc).__name__}: {exc}")
            continue
        if actual == expected:
            ok.append(f"{package}=={actual} import={import_version}")
        elif strict:
            problems.append(f"VERSION {package}: expected package {expected}, found package {actual} import={import_version}")
        else:
            ok.append(f"{package} found package {actual} import={import_version} (locked reference {expected})")
    return ok, problems


def check_files() -> tuple[list[str], list[str]]:
    ok: list[str] = []
    problems: list[str] = []
    for rel in REQUIRED_FILES:
        path = SKILL_DIR / rel
        if path.exists():
            ok.append(f"file {rel}")
        else:
            problems.append(f"MISSING file {rel}")
    return ok, problems


def check_markers() -> tuple[list[str], list[str]]:
    ok: list[str] = []
    problems: list[str] = []
    for rel, markers in SOURCE_MARKERS.items():
        path = SKILL_DIR / rel
        text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        for marker in markers:
            if marker in text:
                ok.append(f"marker {rel}: {marker}")
            else:
                problems.append(f"MISSING marker {rel}: {marker}")
    return ok, problems


def check_file_hashes(contract: dict[str, Any]) -> tuple[list[str], list[str]]:
    ok: list[str] = []
    problems: list[str] = []
    for rel, expected in contract.get("file_hashes", {}).items():
        path = SKILL_DIR / rel
        if not path.exists():
            problems.append(f"HASH file missing {rel}")
            continue
        actual = sha256(path)
        if actual == expected:
            ok.append(f"hash {rel}")
        else:
            problems.append(f"HASH {rel}: expected {expected}, found {actual}")
    return ok, problems


def check_rscript() -> tuple[list[str], list[str]]:
    exe = shutil.which("Rscript")
    if not exe:
        return [], ["Rscript not found; R helper scripts will be unavailable"]
    try:
        proc = subprocess.run([exe, "--version"], text=True, capture_output=True, check=False)
        version = (proc.stdout or proc.stderr).strip().splitlines()[0]
    except Exception as exc:  # pragma: no cover - diagnostic path
        return [], [f"Rscript version check failed: {type(exc).__name__}: {exc}"]
    return [f"Rscript {version}"], []


def build_manifest(contract: dict[str, Any]) -> dict[str, Any]:
    files = {}
    for rel in REQUIRED_FILES:
        path = SKILL_DIR / rel
        if path.exists():
            files[rel] = sha256(path)
    for rel in sorted(SOURCE_MARKERS):
        path = SKILL_DIR / rel
        if path.exists():
            files[rel] = sha256(path)
    return {
        "skill_name": contract["skill_name"],
        "skill_version": contract["skill_version"],
        "skill_dir": str(SKILL_DIR),
        "python": sys.version,
        "file_hashes": files,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict-versions", action="store_true", help="Fail when installed package versions differ from requirements-lock.txt")
    parser.add_argument("--no-rscript", action="store_true", help="Do not warn when Rscript is missing")
    parser.add_argument("--require-rscript", action="store_true", help="Fail when Rscript is missing")
    parser.add_argument("--check-file-hashes", action="store_true", help="Fail when key file hashes differ from reproducibility_contract.json")
    parser.add_argument("--write-manifest", type=Path, help="Write a JSON manifest with file hashes and runtime metadata")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    contract = load_contract()
    ok: list[str] = []
    warnings: list[str] = []
    problems: list[str] = []
    for good, bad in (
        check_files(),
        check_dependencies(contract, args.strict_versions),
        check_markers(),
    ):
        ok.extend(good)
        problems.extend(bad)
    if not args.no_rscript:
        good, bad = check_rscript()
        ok.extend(good)
        if args.require_rscript:
            problems.extend(bad)
        else:
            warnings.extend(bad)
    if args.check_file_hashes:
        good, bad = check_file_hashes(contract)
        ok.extend(good)
        problems.extend(bad)

    if args.write_manifest:
        args.write_manifest.parent.mkdir(parents=True, exist_ok=True)
        args.write_manifest.write_text(json.dumps(build_manifest(contract), indent=2, ensure_ascii=False), encoding="utf-8")
        ok.append(f"wrote manifest {args.write_manifest}")

    print(f"skill={contract['skill_name']} version={contract['skill_version']}")
    print(f"skill_dir={SKILL_DIR}")
    print(f"python={sys.version.split()[0]}")
    print(f"ok={len(ok)} warnings={len(warnings)} problems={len(problems)}")
    for item in ok:
        print(f"OK {item}")
    for item in warnings:
        print(f"WARNING {item}")
    for item in problems:
        print(f"PROBLEM {item}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
