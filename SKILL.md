---
name: paper-data-integrity-audit
description: Use when auditing scientific paper raw data, supplementary tables, figure source data, decimal precision, last-digit distribution, numeric distribution, pseudo-random or reconstructed values, arithmetic-progressed pseudo-values, duplicate numeric sequences, derived-panel recalculation, n consistency, statistical recalculation, figure reproducibility, image reuse, and cautious evidence-based risk grading.
---

# Paper Data Integrity Audit

Use this skill when the user asks to check whether paper PDFs, raw data,
supplementary tables, figure source data, images, or reported statistics contain
suspicious inconsistencies.

Never conclude "fabrication" or "fraud" from internal data checks alone. Use
language such as "inconsistent", "mathematically incompatible", "requires author
clarification", "potentially suspicious", and "high-risk anomaly".

## Inputs

Accept paper PDFs, supplementary tables, raw Excel/CSV files, figure source data,
Western blot or microscopy images, analysis scripts, and GraphPad/R/Python output
files. When raw values are unavailable, classify the relevant panel as
INSUFFICIENT DATA rather than inferring beyond the evidence.

## Audit Workflow

1. Build a figure-to-data map. For each panel record figure panel, assay type,
   claimed n, biological vs technical replicate status, groups, source file,
   statistic, plotted error type, and main claim. Flag missing raw data.
2. Run source-data workbook screening. For Nature-style or irregular xlsx source
   data, run `scripts/source_data_workbook_audit.py`; read
   `resources/raw_data_forensic_methods.md` before interpreting the outputs.
   Always inspect last-digit distribution, numeric distribution,
   pseudo-randomness flags, arithmetic-progressed blocks, and duplicate numeric
   sequences manually.
3. Audit decimal precision. For CSV/XLSX numeric tables, run
   `scripts/decimal_audit.py`; read `resources/decimal_rules.md` for assay-aware
   interpretation.
4. Audit distributions. For each group compute n, mean, median, SD, SEM, CV,
   min, max, IQR, skewness, kurtosis, duplicate counts, decimal-place
   distribution, last-digit distribution, first-digit distribution, digit
   entropy, terminal 0/5 enrichment, and pseudo-randomness screens. Use
   `scripts/distribution_audit.R` for tidy CSV tables; read
   `resources/distribution_rules.md` before grading.
5. Recalculate derived panels from their declared parent data. Examples: AUC
   from time-course curves, percentages from counts and denominators, ratios
   from numerator/denominator columns, normalized values from raw baselines, and
   fold changes from paired values. If a derived panel preserves means but not
   per-sample values, treat it as high risk.
6. Recalculate statistics from raw data: mean, SD, SEM, t tests, ANOVA,
   nonparametric tests, adjusted p values, and confidence intervals when
   applicable. Use `scripts/stat_recheck.R` for simple tidy group/value CSVs.
7. Reproduce figures from raw data where possible. Use
   `scripts/figure_reproduce.R` for basic dot, bar, and box panels, then compare
   points, means, error bars, significance marks, legends, and text claims.
8. Screen image panels separately from numeric-data findings. For Western blot,
   microscopy, colony formation, migration, invasion, IF, and IHC images, check
   for duplicated regions, rotated/flipped reuse, repeated backgrounds,
   suspicious splicing, contrast discontinuity, duplicated bands, loading-control
   reuse, and condition label mismatch.
9. Grade risk using `resources/risk_grading.md`. The final verdict must be one
   of PASS, WARN, HIGH-RISK, or INSUFFICIENT DATA.

## Small-n Rule

For n less than 5, do not overinterpret distribution shape, missing outliers, or
last-digit frequencies. Mark distribution findings as "limited interpretability"
unless the issue is arithmetic, such as an impossible mean, impossible
percentage, n mismatch, unreproducible error bar, or p-value incompatibility.

## Script Quick Reference

Run scripts from the skill directory or pass absolute paths.

```bash
python -m pip install --user -r requirements.txt
python scripts/source_data_workbook_audit.py paper_source_data_dir --out audit_results/source_data_scan
python scripts/decimal_audit.py raw_data.xlsx --out decimal_audit.csv
Rscript scripts/distribution_audit.R raw_data.csv group value distribution_audit.csv
Rscript scripts/stat_recheck.R raw_data.csv group value Control Treatment stat_recheck.csv
Rscript scripts/figure_reproduce.R raw_data.csv group value fig3a bar sem
```

The R scripts expect tidy data: one row per observation, one group column, and one
numeric value column.

## Environment Dependencies

Before running the full audit, read `resources/environment_dependencies.md` and
install the Python dependencies with `requirements.txt` when the environment
lacks Excel/PDF/image packages.

Dependency tiers:

- Core xlsx source-data screening:
  `scripts/source_data_workbook_audit.py` uses only Python standard library.
- Excel column-level decimal audit:
  `scripts/decimal_audit.py` needs `pandas` and `openpyxl` for `.xlsx` files.
  CSV/TSV input uses Python standard library.
- PDF/image integrity screening:
  use `pymupdf`, `pillow`, `numpy`, and `opencv-python-headless` for extracting
  embedded PDF images, rendering pages, perceptual hashing, and repeated-region
  screening.
- R statistics/figure scripts:
  require `Rscript`; current scripts use base R packages only.

Quick preflight:

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

Primary xlsx screening outputs:

- `workbook_sheet_summary.csv`: workbook/sheet inventory and numeric-cell counts.
- `numeric_cell_audit.csv`: every numeric cell with decimal place and last digit.
- `digital_distribution_summary.csv`: sheet-level and group-level last-digit,
  first-digit, entropy, terminal 0/5, Benford-reference, and pseudo-randomness
  screens.
- `group_block_summary.csv`: per-group n, mean, SD, SEM, duplicate values,
  decimal places, digit distributions, arithmetic checks, and raw values.
- `arithmetic_progression_blocks.csv`: group blocks with near-exact arithmetic
  progressions.
- `duplicate_numeric_sequences.csv`: repeated long numeric sequences across
  blocks.

## Raw Source Data Red Flags

Escalate to HIGH-RISK when any of these are present and not explained by the
methods or source-data structure:

- Values for biological replicates form exact or near-exact arithmetic
  progressions centered on the mean.
- Replicate values show repeated pseudo-random structure, such as unusually
  patterned last digits, low digit entropy, excessive terminal 0/5 values,
  monotonic row order, or summary-derived jitter, across multiple independent
  groups.
- Nominally independent biological replicates, conditions, samples, or panels
  contain completely identical long-decimal values or highly similar
  long-decimal tails. Treat exact repeated values with 6 or more decimal places,
  or repeated/similar decimal tails of 6 or more digits across independent
  observations, as HIGH-RISK unless the source explicitly documents a shared
  calibrator, detection floor, technical duplicate, or rounding rule.
- A derivative panel, such as AUC or percentage, cannot be recalculated from the
  parent raw panel even though the legend says it is calculated from that panel.
- Different panels or conditions share an exact long numeric sequence.
- Recalculated p values match only the suspicious reconstructed source values
  and not the underlying raw or parent data.
- Means, percentages, ordinal scores, n, SD, SEM, or error bars are
  mathematically incompatible with the supplied raw values.

## Reporting Rules

Use `templates/audit_report.md` as the default report shape. Keep numeric-data,
statistical, figure-reproduction, and image-integrity concerns in separate
sections. For every issue, include the source file, figure or table location,
the exact recalculated value when available, the reported value when available,
and a risk grade.

Every user-facing finding table must include a `Raw data file` column or field
that names the exact source workbook, CSV, PDF, image file, or other raw-data
artifact supporting the finding. If the finding depends on missing raw data,
write the expected raw-data file type or the available parent file, such as the
PDF-only source. For final reports, include a concise key-findings table with
`Issue ID`, `Risk`, `Figure/panel`, `Raw data file`, `Sheet/cells`, and
`Finding`. When exporting to PDF, preserve the `Raw data file` field in the
PDF output.

Do not write "the authors fabricated data" unless the user provides external
verified evidence beyond this audit. Prefer cautious statements:

- "This is a high-risk inconsistency."
- "This requires author clarification."
- "The reported value is mathematically incompatible with the stated n."
- "The figure cannot be reproduced from the provided raw data."
- "This pattern is suspicious but not conclusive."
