# Environment Dependencies

Use this reference before running a full paper-data integrity audit. Install
only what is needed for the requested inputs.

## Python

Recommended install command:

```bash
python -m pip install --user -r requirements.txt
```

Required for Excel `.xlsx` column-level audit:

- `pandas`: reads Excel workbooks for `scripts/decimal_audit.py`.
- `openpyxl`: Excel engine used by pandas for `.xlsx` files.

Required for PDF/image integrity screening:

- `pymupdf`: imported as `fitz`; extracts embedded PDF images, coordinates, text,
  metadata, and rendered pages.
- `pillow`: reads, writes, crops, and converts extracted images.
- `numpy`: grayscale arrays, image statistics, and hash preprocessing.
- `opencv-python-headless`: optional image-processing backend for future
  repeated-region and similarity scans; use the headless build on servers.

Not required for irregular xlsx source-data screening:

- `scripts/source_data_workbook_audit.py` parses `.xlsx` XML directly and uses
  only Python standard library modules.

## R

Required command:

```bash
Rscript --version
```

Current R scripts use base R packages only:

- `scripts/distribution_audit.R`: `read.csv`, summary statistics, skewness and
  kurtosis implemented in-script.
- `scripts/stat_recheck.R`: base `stats` tests, including t tests, ANOVA,
  Wilcoxon rank-sum, and Kruskal-Wallis.
- `scripts/figure_reproduce.R`: base R plotting with `png`.

No CRAN package installation is required for the bundled R scripts.

## Preflight

Run this before a full Excel/PDF/image audit:

```bash
python - <<'PY'
for m in ["pandas", "openpyxl", "fitz", "PIL", "cv2", "numpy"]:
    try:
        mod = __import__(m)
        print(m, "OK", getattr(mod, "__version__", ""))
    except Exception as exc:
        print(m, "MISSING", type(exc).__name__, exc)
PY
Rscript --version
```

Expected module names:

| Package | Import name |
|---|---|
| pandas | `pandas` |
| openpyxl | `openpyxl` |
| PyMuPDF | `fitz` |
| Pillow | `PIL` |
| opencv-python-headless | `cv2` |
| numpy | `numpy` |

## Common Failure Modes

- `ModuleNotFoundError: No module named 'pandas'`: install `pandas` and
  `openpyxl`, or use `scripts/source_data_workbook_audit.py` for XML-level xlsx
  screening.
- `ModuleNotFoundError: No module named 'fitz'`: install `pymupdf` before PDF
  image extraction.
- GUI/OpenCV library errors on servers: install `opencv-python-headless`, not
  the GUI `opencv-python` build.
- Missing `Rscript`: install R before running the R-based distribution,
  statistical, or figure reproduction scripts.
