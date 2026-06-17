# paper-data-integrity-audit

`paper-data-integrity-audit` is a Codex/Claude/OpenClaw-style skill for auditing
scientific paper source data. It focuses on raw numeric data, figure source
tables, decimal precision, digit distributions, pseudo-random or reconstructed
values, statistical consistency, figure reproducibility, and PDF/image reuse.

The skill is designed for cautious integrity screening. It should not be used to
declare misconduct from internal inconsistencies alone. Reports should use
wording such as "high-risk inconsistency", "mathematically incompatible",
"requires author clarification", and "cannot be reproduced from the provided raw
data".

## What It Checks

- Figure-to-data mapping: links each panel to source files, claimed `n`, groups,
  plotted statistics, and main claims.
- Decimal precision: mixed precision, 3+ place decimal tails, integer-like
  values reported as arbitrary decimals, impossible percentages, and repeated
  decimal tails.
- Digit distribution: last-digit counts, first-digit counts, digit entropy,
  terminal `0/5` enrichment, and Benford-style reference checks when appropriate.
- Pseudo-random or reconstructed values: arithmetic progressions, monotonic
  replicate ordering, values centered on the mean, repeated terminal-digit
  templates, suspiciously similar 3+ place decimals, reused short vectors, and
  repeated local runs of 3 or more consecutive numeric positions.
- Group-block statistics: `n`, mean, SD, SEM, CV, min/max, duplicate values,
  decimal-place distribution, and raw values.
- Derived-panel recalculation: AUC, percentage, ratio, fold change, normalized
  signal, and composite-score checks from parent raw data.
- Statistical consistency: mean/SD/SEM, t tests, ANOVA, nonparametric tests,
  adjusted p values, and reported significance claims.
- Figure reproduction: dot, bar, box, and basic summary plots from tidy raw data.
- PDF/image integrity: embedded image reuse, exact hash duplicates, perceptual
  near matches, repeated rendered-page regions, Western blot reuse, microscopy
  reuse, splicing, and label mismatch candidates.

## Repository Layout

```text
paper-data-integrity-audit/
├── SKILL.md
├── requirements.txt
├── agents/
│   └── openai.yaml
├── resources/
│   ├── decimal_rules.md
│   ├── distribution_rules.md
│   ├── environment_dependencies.md
│   ├── raw_data_forensic_methods.md
│   └── risk_grading.md
├── scripts/
│   ├── decimal_audit.py
│   ├── distribution_audit.R
│   ├── figure_reproduce.R
│   ├── source_data_workbook_audit.py
│   └── stat_recheck.R
└── templates/
    ├── audit_report.md
    ├── figure_checklist.md
    └── raw_data_checklist.md
```

## Installation

Clone the repository into your skill directory:

```bash
mkdir -p ~/.codex/skills
git clone https://github.com/Fengmo997/paper-data-integrity-audit.git \
  ~/.codex/skills/paper-data-integrity-audit
```

For Claude/OpenClaw-style directories:

```bash
mkdir -p ~/.claude/skills
git clone https://github.com/Fengmo997/paper-data-integrity-audit.git \
  ~/.claude/skills/paper-data-integrity-audit
```

Install Python dependencies when you need Excel `.xlsx`, PDF, or image scanning:

```bash
cd ~/.codex/skills/paper-data-integrity-audit
py -m pip install --user -r requirements.txt
```

For reproducible output matching the current validated workflow, install the
locked dependency set instead:

```bash
cd ~/.codex/skills/paper-data-integrity-audit
py -m pip install --user -r requirements-lock.txt
py scripts/preflight_check.py --strict-versions --check-file-hashes
```

`requirements.txt` is intentionally compatible and may install newer packages.
`requirements-lock.txt` pins the versions used to validate the current output
format and thresholds.
Missing `Rscript` is reported as a warning by default because the Excel/PDF
standard pipeline does not require R. Use `--require-rscript` only when you plan
to run the optional R helper scripts.

Check the environment:

```powershell
py scripts/preflight_check.py
```

Dependency tiers:

- `source_data_workbook_audit.py` uses only the Python standard library.
- `decimal_audit.py` needs `pandas` and `openpyxl` for Excel `.xlsx` files.
- PDF/image screening needs `pymupdf`, `pillow`, `numpy`, and
  `opencv-python-headless`.
- R scripts require `Rscript`; bundled R scripts use base R only.

See [`resources/environment_dependencies.md`](resources/environment_dependencies.md)
for details.

## Quick Usage

For full paper/source-data rescans, prefer the standard wrapper. It fixes the
current checked parameters and writes an environment manifest:

```bash
py scripts/run_standard_audit.py /path/to/paper_or_raw_data_dir \
  --out audit_results/rescan_full \
  --scan-pdfs
```

The standard wrapper:

- stages discovered `.xlsx` files into `_standard_xlsx_inputs/` before scanning;
- skips generated directories such as `audit_results/` by default;
- runs `source_data_workbook_audit.py` with the current full parameter set;
- runs `formula_recheck.py` with tolerance `1e-9`;
- optionally runs the four-layer PDF/image scan for discovered PDFs;
- builds `visual_report/visual_report.html` with `--max-per-type 0`;
- records `audit_environment_manifest.json` for later comparison.

Use `--dry-run` to print the exact commands without running them. Use
`--include-generated-dirs` only when you intentionally want to scan files inside
old audit output folders.

Manual script calls are still available for targeted checks:

Run source-data workbook screening on an irregular Excel source-data folder:

```bash
py scripts/source_data_workbook_audit.py /path/to/paper_source_data \
  --out audit_results/source_data_scan
```

Run column-level decimal audit:

```bash
py scripts/decimal_audit.py raw_data.xlsx --out audit_results/decimal_audit.csv
```

Recalculate common Excel formulas against cached values:

```bash
py scripts/formula_recheck.py raw_data.xlsx \
  --out audit_results/source_data_scan/formula_recheck.csv
```

Run four-layer PDF/image screening:

```bash
py scripts/pdf_image_integrity_scan.py paper.pdf --out audit_results/pdf_image_scan
```

Build the visual evidence report:

```bash
py scripts/build_visual_audit_report.py --audit-dir audit_results \
  --source-scan audit_results/source_data_scan \
  --pdf-scan audit_results/pdf_image_scan \
  --max-per-type 0
```

Run tidy-data distribution audit:

```bash
Rscript scripts/distribution_audit.R raw_data.csv group value \
  audit_results/distribution_audit.csv
```

Recalculate basic two-group statistics:

```bash
Rscript scripts/stat_recheck.R raw_data.csv group value Control Treatment \
  audit_results/stat_recheck.csv
```

Reproduce a simple figure panel:

```bash
Rscript scripts/figure_reproduce.R raw_data.csv group value \
  audit_results/fig3a bar sem
```

## Main Outputs

`source_data_workbook_audit.py` writes:

- `workbook_sheet_summary.csv`: workbook/sheet inventory and numeric-cell counts.
- `panel_label_cells.csv`: detected figure/panel labels in worksheets.
- `numeric_cell_audit.csv`: every numeric cell with decimal places and last digit.
- `exact_long_decimal_repeats.csv`: exact repeated values with displayed decimal
  precision `>=3`; precision `>=6` repeats are high-risk review only when they
  are not binary floating point display artifacts.
- `digital_distribution_summary.csv`: sheet-level and group-level digit
  distributions, entropy, terminal `0/5`, Benford reference, and pseudo-randomness
  screens.
- `group_block_summary.csv`: per-group `n`, mean, SD, SEM, duplicate values,
  decimal places, digit distributions, arithmetic checks, and raw values.
- `arithmetic_progression_blocks.csv`: near-exact arithmetic progression blocks.
- `duplicate_numeric_sequences.csv`: repeated numeric sequences, defaulting to
  length 3 or longer.
- `short_duplicate_numeric_sequences.csv`: same-panel adjacent/same-condition
  short vector matches, cross-panel same-condition matches, and local repeated
  runs of 3 or more consecutive numeric positions, with risk hints that consider
  independent cytokine/condition labels versus likely shared calculation source.
- `scaled_numeric_sequence_blocks.csv`: aligned numeric vectors that are fixed
  scalar multiples of one another.
- `duplicate_numeric_matrix_blocks.csv`: same-sheet figure/panel anchored
  numeric rectangles that are identical, highly overlapping, or contain a
  continuous identical submatrix.
- `sheet_csv/`: worksheet exports as CSV files.

Baseline source-data parameters are fixed unless a run explicitly documents a
parameter-tuning experiment: numeric column threshold `80%`, long decimal `>=3`,
six-decimal count `>=6`, repeated decimal tail last `3` digits repeated at least
`3` times, last-digit entropy `<2.5`, last-digit chi-square `>=27.88`,
`--ap-tolerance=0.001`, `--pair-sum-tolerance=1e-9`,
`--min-sequence-len=3`, `--short-sequence-len=3`, and formula validation
tolerance `<=1e-9`.

`decimal_audit.py` writes numeric-column summaries including:

- duplicate values
- decimal-place distributions
- long decimal counts
- integer-as-decimal counts
- last-digit distributions
- repeated decimal tails
- percentage denominator compatibility flags

## PDF/Image Screening Pattern

The skill supports four-layer PDF/image screening with
`scripts/pdf_image_integrity_scan.py`:

1. Extract embedded raster image placements from PDF image object IDs (`xref`),
   including dimensions, colorspace, placement coordinates, `sha256`, `dhash`,
   and `ahash`.
2. Detect exact binary `sha256` duplicates. Byte-identical embedded image data
   placed under different biological conditions, samples, or figure labels is
   stronger evidence than visual similarity alone.
3. Detect near embedded-image matches with perceptual hashes (`dhash`, `ahash`).
   These are WARN review candidates until inspected on contact sheets.
4. Render pages and scan both whole-page hashes and fixed-window local regions.
   Repeated rendered tiles are also review candidates and need label/context
   checks.

Default outputs include `embedded_image_placements.csv`,
`exact_embedded_image_duplicates.csv`, `near_embedded_image_matches.csv`,
`page_render_hashes.csv`, `near_page_render_matches.csv`,
`region_duplicate_candidates.csv`, and contact sheets for candidate review.

## Visual Report Standard

`build_visual_audit_report.py` turns audit CSVs into a reviewable HTML/Markdown
report with evidence images:

- exact image duplicate contact sheets are embedded when present
- near image/page/region candidates are embedded as WARN review evidence
- source-data anomalies with cell coordinates are rendered as complete
  highlighted HTML tables from `sheet_csv`
- opening a `?issue=NUM-xxx` table shows only that NUM's highlights
- cross-sheet A/B evidence uses per-issue wrapper pages so B coordinates are
  never projected onto the A sheet
- each evidence block inside one NUM has its own color: A/B blocks differ, and
  window arithmetic source A, source B, and target use three different colors
- image evidence opens in a lightbox with a Close button, Escape close,
  mouse-wheel zoom, drag-to-pan while zoomed, and automatic zoom reset
- binary-float display tails and compact scientific notation are normalized in
  the visual layer only; backing CSVs remain unchanged
- the default report expansion is unlimited (`--max-per-type 0`)

The visual report complements the CSV files. Keep the backing CSV path, raw data
file, sheet/cells, risk grade, and cautious interpretation in the final report.

## Risk Grading

Use four top-level verdicts:

- `PASS`: source data reproduce figures and statistics; only minor rounding
  differences are present.
- `WARN`: missing metadata, explainable decimal/digit patterns, small-n limits,
  or minor inconsistencies requiring clarification.
- `HIGH-RISK`: figures cannot be reproduced from raw data; means/p values are
  mathematically incompatible; biological replicates appear reconstructed; long
  sequences are reused; or image objects are duplicated across different
  conditions/panels.
- `INSUFFICIENT DATA`: required raw data, denominators, sample IDs, image files,
  or statistical outputs are unavailable.

Do not write "fabrication" or "fraud" unless there is external verified evidence
beyond the audit artifacts.

## Example Prompt

```text
Use paper-data-integrity-audit to audit this paper PDF, supplementary tables,
and raw Excel files.

Focus on:
1. abnormal decimals and long decimal tails;
2. last-digit and numeric-distribution patterns;
3. pseudo-random or reconstructed replicate values;
4. n, mean, SD/SEM, p-value consistency;
5. whether figures can be reproduced from raw data;
6. exact or near image reuse in PDF/Western blot/microscopy panels.

Output PASS / WARN / HIGH-RISK / INSUFFICIENT DATA, and use cautious forensic
language rather than declaring misconduct.
```

## Notes

- This skill is a screening and reproducibility tool, not a legal or misconduct
  determination tool.
- Small `n` limits digit-distribution interpretation.
- Normalized values, bounded percentages, instrument exports, and Excel floating
  point expansion can create benign digit-distribution artifacts.
- Original raw images and uncropped blots are required for conclusive image
  integrity review.
