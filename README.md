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
- Decimal precision: mixed precision, long decimal tails, integer-like values
  reported as arbitrary decimals, impossible percentages, and repeated decimal
  tails.
- Digit distribution: last-digit counts, first-digit counts, digit entropy,
  terminal `0/5` enrichment, and Benford-style reference checks when appropriate.
- Pseudo-random or reconstructed values: arithmetic progressions, monotonic
  replicate ordering, values centered on the mean, repeated terminal-digit
  templates, and suspiciously similar long decimals.
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
python -m pip install --user -r requirements.txt
```

Check the environment:

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

Dependency tiers:

- `source_data_workbook_audit.py` uses only the Python standard library.
- `decimal_audit.py` needs `pandas` and `openpyxl` for Excel `.xlsx` files.
- PDF/image screening needs `pymupdf`, `pillow`, `numpy`, and
  `opencv-python-headless`.
- R scripts require `Rscript`; bundled R scripts use base R only.

See [`resources/environment_dependencies.md`](resources/environment_dependencies.md)
for details.

## Quick Usage

Run source-data workbook screening on an irregular Excel source-data folder:

```bash
python scripts/source_data_workbook_audit.py /path/to/paper_source_data \
  --out audit_results/source_data_scan
```

Run column-level decimal audit:

```bash
python scripts/decimal_audit.py raw_data.xlsx --out audit_results/decimal_audit.csv
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
- `digital_distribution_summary.csv`: sheet-level and group-level digit
  distributions, entropy, terminal `0/5`, Benford reference, and pseudo-randomness
  screens.
- `group_block_summary.csv`: per-group `n`, mean, SD, SEM, duplicate values,
  decimal places, digit distributions, arithmetic checks, and raw values.
- `arithmetic_progression_blocks.csv`: near-exact arithmetic progression blocks.
- `duplicate_numeric_sequences.csv`: repeated long numeric sequences.
- `sheet_csv/`: worksheet exports as CSV files.

`decimal_audit.py` writes numeric-column summaries including:

- duplicate values
- decimal-place distributions
- long decimal counts
- integer-as-decimal counts
- last-digit distributions
- repeated decimal tails
- percentage denominator compatibility flags

## PDF/Image Screening Pattern

The skill supports PDF/image screening workflows using:

- embedded image extraction from PDF object IDs (`xref`)
- exact binary `sha256` duplicate detection
- perceptual hashes (`dhash`, `ahash`) for near-match candidates
- rendered-page hashes
- fixed-window repeated-region scans
- manual review contact sheets for image candidates

Exact `sha256` matches of image objects placed under different biological
conditions, samples, or figure labels are higher-risk than perceptual near
matches. Perceptual near matches are review candidates only.

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
