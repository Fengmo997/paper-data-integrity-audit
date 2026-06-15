# Raw Source Data Forensic Methods

Use this reference when auditing scientific source-data files, especially
Nature-style Excel workbooks where multiple figure panels are placed in one
sheet and not all data are tidy.

## 1. Source Inventory

Record every file and sheet before interpreting numbers:

- PDF files: page count, extractable text, figure legends, methods, data
  availability statements, and embedded image hashes when possible.
- Excel files: workbook name, sheet name, row/column count, nonempty cells,
  numeric cells, text cells, formula cells, maximum decimal places, and long
  decimal counts.
- Image files: original resolution, metadata, file hashes, and whether the file
  is a final figure crop or original raw image.

## 2. Figure-to-Data Map

Build a table linking figure panels to source files:

| Field | Required check |
|---|---|
| Figure panel | Exact panel name, including supplementary or extended data |
| Claimed n | Biological vs technical replicate status |
| Source location | Workbook, sheet, row, column, or image file |
| Parent panel | For derived panels, the raw panel used for calculation |
| Statistic | Mean, SD, SEM, CI, boxplot, AUC, ratio, percentage, score |
| Test | t test, one-way ANOVA, two-way ANOVA, Tukey, Sidak, nonparametric test |

Flag missing raw data as INSUFFICIENT DATA.

## 3. Numeric Cell Audit

For each numeric table, calculate:

- decimal-place distribution
- long-decimal counts, especially 3 or more decimal places
- last-digit distribution
- first-digit distribution
- digit entropy and last-digit concentration
- terminal 0/5 enrichment
- duplicate values and duplicate rates
- zero-variance groups
- values ending disproportionately in 0 or 5
- integer or ordinal measurements reported as arbitrary decimals
- impossible percentages given denominators

Interpretation guardrail: long decimals are not automatically suspicious when
the values are derived ratios, normalized quantities, or instrument exports.

## 4. Group Block Audit

For each recognizable group block, output:

- source workbook, sheet, panel, header row, data rows, group label
- n, mean, SD, SEM, min, max, CV
- duplicate count
- decimal-place and last-digit distributions
- first-digit distribution, last-digit entropy, last-digit uniformity screen,
  and terminal 0/5 fraction
- adjacent-pair sum targets, matching-pair counts, matching fractions, and
  row-total matches for constant-sum reconstruction screening
- raw values
- issue flags

Use `scripts/source_data_workbook_audit.py` for irregular xlsx source data and
`scripts/distribution_audit.R` for tidy CSV data.

## 5. Last-Digit And Numeric-Distribution Audit

Run digit-distribution checks at two levels:

- Sheet level: useful for broad workbook screening and method-specific numeric
  export patterns.
- Group-block level: useful for biological replicate checks and figure-panel
  review.

Record:

- decimal-place counts
- last-digit counts
- first-digit counts
- last-digit Shannon entropy
- last-digit chi-square deviation from a uniform 0-9 reference
- maximum last-digit fraction
- terminal 0/5 fraction
- Benford first-digit chi-square only when values are positive, numerous, and
  span at least two orders of magnitude

Interpretation guardrails:

- Small n cannot support strong digit-distribution conclusions. For n < 20,
  summarize only unless another arithmetic inconsistency is present.
- Instrument exports, normalized ratios, rounded percentages, bounded scores,
  and axes can have nonuniform digits for legitimate reasons.
- Benford checks are not appropriate for bounded, normalized, thresholded, or
  narrow-range biological assay values.
- Repeated digit anomalies across many unrelated groups are more informative
  than one isolated small group.

## 6. Pseudo-Randomness And Reconstructed-Value Screening

Treat "fake random" as a screen for structured or summary-derived values, not as
a direct accusation. Check for:

- exact or near-exact arithmetic progressions
- monotonic replicate order within independent biological groups
- values generated as `mean +/- k * step`
- symmetric values centered on the reported mean
- repeated adjacent-pair sums such as `x1+x2=2`, `x3+x4=2`, and `x5+x6=2`
  across nominally independent rows; this is stronger than ordinary control
  mean normalization because it constrains pair-level values, not only group
  totals
- group totals repeatedly forced to the nominal n after normalization,
  especially when paired sums or mirror constraints are also present
- completely identical long-decimal values or highly similar long-decimal tails
  across nominally independent biological replicates, conditions, samples, or
  panels; exact repeated values with 3 or more decimal places, or
  repeated/similar decimal tails of 3 or more digits, are HIGH-RISK unless a
  documented technical explanation is present
- identical short numeric vectors in same-panel adjacent condition columns,
  same-panel same-condition columns, or cross-panel same-condition columns;
  also check for local runs where at least 3 consecutive positions are identical
  even when the full column is not identical
- repeated decimal tails or copied terminal-digit patterns
- excessive avoidance or enrichment of round terminal digits
- identical sample ranking across unrelated assays
- p values that reproduce only from reconstructed values, not from parent raw
  values

Escalate when the same pseudo-random pattern appears in multiple independent
panels or when reconstructed values replace a declared parent raw panel.

## 7. Arithmetic-Progression Audit

Detect exact or near-exact arithmetic progressions in replicate values.

High-risk pattern:

- n is 5 or larger
- replicate values are monotonic and evenly spaced
- the middle value equals the mean or the series is symmetric around the mean
- the step can be derived from the reported SD, for example for n=5:
  sample SD = step * sqrt(2.5)
- the panel claims biological replicates rather than a simulated display range

Interpretation:

- A single dose-response or time-course series can naturally be monotonic.
- Biological replicate values within one group should not usually form a
  perfect arithmetic progression.
- If arithmetic-progressed values preserve the group mean but differ from raw
  parent-panel values, grade as HIGH-RISK.

## 7b. Constant-Sum And Mirror-Reconstruction Audit

Detect row-wise constraints that preserve means while changing individual
replicate values:

- adjacent-pair sums: `x1+x2`, `x3+x4`, `x5+x6`
- edge-mirror sums: `x1+xn`, `x2+x(n-1)`
- sample-ID matched sums when labels imply pairing
- row totals exactly equal to n for normalized controls

High-risk pattern:

- at least 12 checked pairs across at least 4 rows
- one pair-sum target accounts for at least 80% of checked pairs and at least
  10 pairs
- at least 80% of rows have totals implied by that same pair-sum target
- the panel labels the values as independent biological observations or
  replicate source values
- no documented pairwise normalization, calibrator, technical-duplicate,
  percentage-complement, or compositional rule explains the constraint

Interpretation:

- Control normalization may force the group mean to 1, but it does not justify
  repeated adjacent pairs summing exactly to 2.
- Percentage complements and compositional parts can legitimately sum to 100 or
  1 when they are parts of the same whole.
- Report exact source cells, the pair-sum target, matching-pair count, total
  checked pairs, and raw file in every retained finding.

## 8. Duplicate Numeric Sequence Audit

Hash full numeric sequences by panel and group after standardizing values. Use
a default minimum sequence length of `3`. Escalate when sequences are exactly
reused across nominally different conditions, samples, or panels.

Also screen short column/vector reuse:

- same-panel adjacent condition columns
- same-panel same-condition columns across different analytes, cytokines, genes,
  or readouts
- cross-panel same-condition columns
- local consecutive overlap where at least `3` numeric positions match in order

Grade by context. Treat the match as HIGH-RISK when the repeated values are
nominally independent cytokines, conditions, samples, or panels and the source
does not document a shared calculation source, calibrator, technical duplicate,
or rounding/export rule. Treat as WARN when shared controls or technical reuse
are plausible but not explicitly documented.

High-risk examples:

- HPLC/MS chromatogram intensity series copied across different peptides or
  conditions.
- qPCR, ELISA, image quantification, or behavior traces reused across unrelated
  panels.
- Same replicate sequence appearing under different labels without explanation.

Low-risk examples:

- Same source block duplicated once for layout convenience.
- Shared time axis or wavelength axis reused across traces.
- Common all-zero baseline in a chromatogram or blank region.

## 9. Derived-Panel Recalculation

When a panel claims values calculated from another panel, recalculate from the
parent data:

- AUC from time-course values using the trapezoid rule.
- Percentage from numerator and denominator counts.
- Ratio from numerator and denominator columns.
- Fold change from raw paired values.
- Normalized signal from baseline/control values.
- Composite score from component scores.

Compare field-by-field:

| Field | Parent-derived value | Source-data value | Risk |
|---|---:|---:|---|
| per-sample values | must match or be explained | mismatch is high risk |
| group mean | may match even if values are reconstructed | not sufficient |
| SD/SEM | must match raw per-sample values | mismatch can alter p values |
| p value | must match raw/parent-derived values | mismatch is high risk |

If only group means match but per-sample values are different, report that the
source data preserve summaries but not raw biological replicate values.

## 10. Statistical Recalculation

Recalculate:

- mean, SD, SEM, median, IQR
- Welch and Student t tests
- one-way ANOVA with Tukey HSD
- two-way ANOVA simple effects with Sidak or Bonferroni when figure structure
  is time-by-condition or factor-by-condition
- nonparametric alternatives when methods claim them
- adjusted p values where multiple testing is claimed

Important: if p values reproduce from suspicious source data, also recalculate
from the underlying parent/raw data when available.

## 11. Image Integrity Screening

Keep image findings separate from numeric findings:

- exact embedded-image hash duplicates
- duplicated regions
- rotated/flipped reuse
- repeated backgrounds
- splicing or contrast discontinuity
- duplicated bands or loading controls
- source image resolution mismatch

PDF exact-hash duplicates are only a first screen. Original images are required
for conclusive microscopy, histology, western blot, IF, and IHC checks.

## 12. Risk Interpretation

Do not infer fabrication from one internal inconsistency. Use cautious language:

- "high-risk source-data inconsistency"
- "mathematically incompatible with the stated source relationship"
- "requires author clarification"
- "source values appear reconstructed from summary statistics"
- "cannot be reproduced from the parent raw panel"
