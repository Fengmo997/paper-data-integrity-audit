---
name: paper-data-integrity-audit
description: Use when auditing scientific paper raw data, supplementary tables, figure source data, decimal precision, last-digit distribution, numeric distribution, pseudo-random or reconstructed values, arithmetic-progressed pseudo-values, fixed-ratio/scaled numeric sequences, duplicated same-sheet numeric matrix blocks, constant-sum or mirror-pair constraints, duplicate numeric sequences, derived-panel recalculation, n consistency, statistical recalculation, figure reproducibility, image reuse, and cautious evidence-based risk grading.
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
   pseudo-randomness flags, arithmetic-progressed blocks, fixed-ratio scaled
   sequence blocks, duplicated numeric matrix blocks, duplicate numeric
   sequences, and same-sheet sliding-window arithmetic relation candidates
   manually.
3. Audit decimal precision. For CSV/XLSX numeric tables, run
   `scripts/decimal_audit.py`; read `resources/decimal_rules.md` for assay-aware
   interpretation.
4. Audit distributions. For each group compute n, mean, median, SD, SEM, CV,
   min, max, IQR, skewness, kurtosis, duplicate counts, decimal-place
   distribution, last-digit distribution, first-digit distribution, digit
   entropy, terminal 0/5 enrichment, adjacent-pair sum constraints, row-total
   constraints, and pseudo-randomness screens. Use
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
8. Screen image panels separately from numeric-data findings. For PDF figure
   files, run the four-layer PDF/image workflow in
   `scripts/pdf_image_integrity_scan.py` when dependencies are available. For
   Western blot, microscopy, colony formation, migration, invasion, IF, and IHC
   images, check for duplicated regions, rotated/flipped reuse, repeated
   backgrounds, suspicious splicing, contrast discontinuity, duplicated bands,
   loading-control reuse, and condition label mismatch.
9. Grade risk using `resources/risk_grading.md`. The final verdict must be one
   of PASS, WARN, HIGH-RISK, or INSUFFICIENT DATA.

## Small-n Rule

For n less than 5, do not overinterpret distribution shape, missing outliers, or
last-digit frequencies. Mark distribution findings as "limited interpretability"
unless the issue is arithmetic, such as an impossible mean, impossible
percentage, n mismatch, unreproducible error bar, or p-value incompatibility.

## Script Quick Reference

Run scripts from the skill directory or pass absolute paths.

For a new computer or a full paper re-audit, prefer the standard wrapper so the
current checked parameters and visual-report behavior are reproduced exactly:

```bash
py -m pip install --user -r requirements-lock.txt
py scripts/preflight_check.py --strict-versions --check-file-hashes
py scripts/run_standard_audit.py paper_source_data_dir --out audit_results/rescan_full --scan-pdfs
```

`preflight_check.py` treats missing Rscript as a warning unless
`--require-rscript` is used. Excel/PDF/source-data checks do not require R; only
the optional R helper scripts do.

`scripts/run_standard_audit.py` reads
`resources/reproducibility_contract.json` and fixes the current defaults:
source-data window relation caps disabled, matrix subblock detection enabled,
formula tolerance `1e-9`, PDF/image thresholds unchanged, and visual reports
built with `--max-per-type 0`. It also writes
`audit_environment_manifest.json` so future reruns can compare the installed
skill and dependency versions.

Manual script calls remain useful for targeted checks:

```bash
py -m pip install --user -r requirements.txt
py scripts/source_data_workbook_audit.py paper_source_data_dir --out audit_results/source_data_scan
py scripts/decimal_audit.py raw_data.xlsx --out decimal_audit.csv
py scripts/formula_recheck.py raw_data.xlsx --out audit_results/source_data_scan/formula_recheck.csv
py scripts/pdf_image_integrity_scan.py paper.pdf --out audit_results/pdf_image_scan
py scripts/build_visual_audit_report.py --audit-dir audit_results --source-scan audit_results/source_data_scan --pdf-scan audit_results/pdf_image_scan
Rscript scripts/distribution_audit.R raw_data.csv group value distribution_audit.csv
Rscript scripts/stat_recheck.R raw_data.csv group value Control Treatment stat_recheck.csv
Rscript scripts/figure_reproduce.R raw_data.csv group value fig3a bar sem
```

The R scripts expect tidy data: one row per observation, one group column, and one
numeric value column.

## Environment Dependencies

Before running the full audit, read `resources/environment_dependencies.md` and
install the Python dependencies with `requirements-lock.txt` when exact
reproducibility with the current version is needed. Use `requirements.txt` only
when you deliberately want compatible newer dependency versions.

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

```powershell
py scripts/preflight_check.py --strict-versions
```

Primary xlsx screening outputs:

- `workbook_sheet_summary.csv`: workbook/sheet inventory and numeric-cell counts.
- `numeric_cell_audit.csv`: every numeric cell with decimal place and last digit.
- `exact_long_decimal_repeats.csv`: exact repeated displayed numeric values with
  decimal precision `>=3`; values with precision `>=6` are
  `HIGH-RISK_REVIEW` only when they do not look like binary floating point
  display artifacts.
- `digital_distribution_summary.csv`: sheet-level and group-level last-digit,
  first-digit, entropy, terminal 0/5, Benford-reference, and pseudo-randomness
  screens.
- `group_block_summary.csv`: per-group n, mean, SD, SEM, duplicate values,
  decimal places, digit distributions, arithmetic checks, and raw values.
- `arithmetic_progression_blocks.csv`: group blocks with near-exact arithmetic
  progressions.
- `constant_pair_sum_blocks.csv`: group blocks where many adjacent replicate
  pairs have the same sum and many rows have totals implied by that pair sum.
- `duplicate_numeric_sequences.csv`: repeated long numeric sequences across
  blocks, with default minimum sequence length `3`.
- `short_duplicate_numeric_sequences.csv`: same-panel adjacent or same-condition
  short vector matches, cross-panel same-condition matches, and local runs where
  at least `3` consecutive numeric positions are identical. Review the
  `independent_cytokine_or_condition_hint` and `shared_calculation_source_hint`
  fields before assigning a final risk grade. Every retained short-column or
  short-vector reuse is at least `WARN_REVIEW`; keep `HIGH-RISK_REVIEW` when the
  values are in an independent context and no shared source is documented by the
  raw-data layout.
- `scaled_numeric_sequence_blocks.csv`: same-layout numeric vectors where one
  column/group is a fixed scalar multiple of another, with ratio, simple-ratio
  label, matched cells, residual error, and risk hints. Review whether a
  documented normalization, calibrator, technical duplicate, or deterministic
  transformation explains the scaling before assigning a final risk grade.
- `window_arithmetic_relation_candidates.csv`: same-sheet adjacent numeric
  windows where at least `3` consecutive target values can be generated from
  one or two other same-sheet windows by basic arithmetic. The screen checks
  fixed offset, fixed ratio, constant-sum complement
  `target = constant - source`, constant-product inverse
  `target = constant / source`, affine transforms
  `target = constant * source + offset`, scaled sums/differences/products/
  ratios, weighted two-window combinations such as `target = A + constant * B`,
  composition fractions such as `target = constant * A/(A+B)`, and direct
  `A+B`, `A-B`, `B-A`, `A*B`, `A/B`, and `B/A` relationships. It reports
  relation type, constants/scales, and residual errors. Treat every retained
  row as at least `WARN_REVIEW`; escalate to `HIGH-RISK_REVIEW` only when the
  target and source windows appear to be independent primary measurements
  rather than documented derived, normalized, summary, axis, identifier,
  percentage, or compositional fields.
- `duplicate_numeric_matrix_blocks.csv`: same-sheet figure/panel anchored
  numeric rectangles whose matrix values are identical, highly overlapping, or
  contain a continuous identical submatrix while preserving row and column
  position. The default whole-matrix partial-overlap threshold is
  `matched_cells >= 12` and `match_fraction >= 0.80`; tune with
  `--matrix-match-fraction` when a stricter screen is needed. A continuous
  exact submatrix is also retained when it has at least `3` rows, `2` columns,
  and `12` cells; tune these with `--matrix-min-rows`, `--matrix-min-cols`, and
  `--matrix-min-cells`. Treat this as stronger than a collection of isolated
  short duplicate vectors when the rectangles are under distinct
  panel/treatment labels, especially when downstream summary rows differ.

Do not weaken or change these baseline source-data screening parameters unless
the user explicitly asks for a parameter-tuning experiment:

- Workbook/sheet inventory: input `.xlsx`; output rows, columns, numeric cells,
  formula cells, `long_decimal_cells_ge_3`, and `long_decimal_cells_ge_8`.
- Numeric cell audit: per numeric cell, record workbook, sheet, row, column,
  value, decimal places, last digit, and long-decimal flag. Use
  `LONG_DECIMAL_PLACES = 3`; count both `>=3` and `>=8` decimal-place cells.
- Decimal column audit: treat a column as numeric when at least `80%` of
  non-empty values are numeric. Use long decimal `>=3`, six-decimal count `>=6`,
  repeated decimal tail as the last `3` decimal digits repeated at least `3`
  times, last-digit entropy `<2.5`, and last-digit chi-square `>=27.88`
  (p<0.001 reference threshold). Count decimal places from the displayed Excel
  value or a canonical decimal string, not from a Python/R binary-float
  representation. Typical float display tails such as `1.209999999999` or
  `1.2100000000000002` should be normalized to the nearest plausible displayed
  value when the correction is tiny, and the normalization count/examples should
  be reported separately rather than treated as true long-decimal evidence.
- Group-level digit/distribution screen: compute n, mean, SD, SEM, CV, min,
  max, decimal-place counts, last-digit counts, first-digit reference,
  entropy, terminal `0/5`, duplicate counts, arithmetic-progression metrics,
  and adjacent-pair-sum metrics.
- Arithmetic progression screen: default `--ap-tolerance=0.001`; require
  `n>=5`, monotonic, nonconstant values, and maximum adjacent-step deviation
  `<=0.001`.
- Adjacent pair-sum/constant-sum screen: default
  `--pair-sum-tolerance=1e-9`; require `total_pairs>=12`,
  `rows_with_pairs>=4`, main sum-cluster count `>=max(10, 80% total_pairs)`,
  and row-total matches `>=max(4, 80% rows_with_pairs)`.
- Duplicate sequence and exact repeat screen: full duplicate sequence minimum
  `--min-sequence-len=3`; short duplicate sequence minimum
  `--short-sequence-len=3`; exclude time, concentration, wavelength, retention,
  and other axis-like labels from sequence reuse calls. Exact repeated values
  with displayed decimal precision `>=3` are listed; precision `>=6` is
  high-risk review only when not explained by binary floating point display
  artifacts.
- Same-sheet sliding-window arithmetic relation screen: default
  `--window-relation-min-len=3`, `--window-relation-max-len=6`,
  `--window-relation-rel-tolerance=1e-6`,
  `--window-relation-abs-tolerance=1e-9`,
  `--window-relation-max-windows-per-sheet=0`, and
  `--window-relation-max-hits-per-sheet=0`; `0` means no per-sheet window cap
  and no per-sheet retained-hit cap. Compare only same-sheet group-block
  windows already identified by the workbook parser, and skip obvious
  axis/identifier contexts. Retain candidates where a target window of at least
  three adjacent values is reproduced by fixed offset, fixed ratio,
  constant-sum complement (`target = constant - source`, equivalent to
  `target + source = constant`), constant-product inverse
  (`target = constant / source`, equivalent to
  `target * source = constant`), affine transform
  (`target = constant * source + offset`), direct binary operations (`A+B`,
  `A-B`, `B-A`, `A*B`, `A/B`, `B/A`), scaled binary operations
  (`constant*(A+B)`, `constant*(A-B)`, `constant*A*B`, `constant*A/B`,
  `constant*B/A`), weighted combinations (`A + constant*B`,
  `B + constant*A`), or composition fractions (`constant*A/(A+B)` and
  `constant*B/(A+B)`). Keep as WARN when labels suggest a derived metric,
  summary statistic, normalization, percentage, total, axis, or ID. Escalate
  to HIGH-RISK only when labels and layout suggest independent biological
  conditions, genes, samples, panels, or primary measurements with no documented
  calculation relationship. Use an adaptive implementation rather than dropping
  checks for large tables: small sheets can use exhaustive comparison, while
  large sheets should use indexed/block-neighborhood matching with parallel
  workers. The CSV and visual report should expose `scan_strategy` so reviewers
  can see whether a hit came from `exhaustive-small-table` or
  `indexed-large-table`. This is an algorithmic scaling choice, not a risk
  downgrade or a threshold relaxation.
- For batch rescans, treat 20 minutes per article as a soft runtime threshold
  and 30 minutes per article as a hard ceiling. Continue past the soft threshold
  when work is still progressing, but record the soft-threshold status in the
  article summary. Stop that article at the hard ceiling, preserve partial
  outputs and logs, and report the timeout explicitly instead of silently
  dropping methods.
- Formula validation: run `scripts/formula_recheck.py` when formulas are
  present. Use tolerance `<=1e-9`; report `MATCH`, `MISMATCH`, or
  `UNSUPPORTED_*`. Treat unsupported formulas as review gaps, not mismatches.

PDF/image screening is not a single hash-only check. Use four layers:

1. Embedded image object extraction. Open the PDF with PyMuPDF, call
   `page.get_images(full=True)`, and record every raster image placement with
   `page`, `xref`, `width`, `height`, `colorspace`, byte length, placement
   coordinates, `sha256`, `dhash`, and `ahash`. `xref` is the internal PDF image
   object number; `placements` are the page coordinates where that object is
   drawn. Output: `embedded_image_placements.csv`.
2. Exact embedded-image duplicate scan. Compare raw embedded image bytes using
   `sha256`. Matching `sha256` values mean byte-level identical embedded image
   data, not merely similar appearance. When the same byte-identical object is
   placed under different biological conditions, cell lines, samples, or panel
   labels, treat it as high-risk evidence unless documented as intentional reuse.
   Output: `exact_embedded_image_duplicates.csv` plus a contact sheet.
3. Near embedded-image scan. For non-identical images, compare perceptual hashes:
   `dhash` from 9x8 grayscale adjacent-pixel differences and `ahash` from 8x8
   grayscale average thresholding. The default screen keeps images at least
   80 px wide/high with `dhash_distance <= 5` and `ahash_distance <= 8`.
   Interpret these as WARN review candidates only, because microscopy fields,
   wound-healing panels, dark fluorescence images, and similar assay layouts can
   naturally hash close together. Output: `near_embedded_image_matches.csv` plus
   a contact sheet.
4. Rendered page and local-region scan. Render each page, default 150 dpi, and
   compute page-level `sha256`, `dhash`, and `ahash` to detect repeated or highly
   similar pages. Also tile rendered pages into 224 x 224 px regions with
   224 px stride, skip low-information tiles (`gray_sd < 18`,
   `gray_mean > 248`, or `gray_mean < 8`), and group remaining tiles by dHash to
   find repeated local rendered regions. Outputs: `page_render_hashes.csv`,
   `near_page_render_matches.csv`, `region_duplicate_candidates.csv`, and a
   region contact sheet when candidates exist.

Keep the evidence language layered: byte-identical embedded-image reuse across
different labels is much stronger than perceptual or tile-level similarity.
Perceptual and rendered-region matches are triage candidates until reviewed
against the figure labels, assay type, crop boundaries, and original image files.

After source-data and PDF/image scans, build a visual evidence report with
`scripts/build_visual_audit_report.py`. The default report outputs are
`visual_report/visual_report.html`, `visual_report/visual_report.md`, and
`visual_report/visual_evidence/*.png`. The HTML report is the primary review
artifact; the Markdown report is for copying into written summaries.

Visual report standards:

- Start every visual report with a prominent display-limit notice and a
  candidate statistics table before the inputs and detailed findings. The table
  must include each check type, total candidate rows/groups, high-risk count,
  warn count, number shown in the detail table, number hidden by display limits,
  whether the type was truncated, and the backing Source CSV. If
  `--max-per-type` hides any candidate, say this clearly at the top of both HTML
  and Markdown reports so reviewers know the detail table is not exhaustive.
- In HTML reports, use a polished review-template home screen before the
  detailed sections. The home screen should include a centered large visual
  evidence frame, previous/next buttons, keyboard left/right navigation, and a
  detail panel under the image showing issue ID, risk, category, location, raw
  data file, localized `Specific issue`, evidence text, Source CSV, and a jump
  link to the detailed table row. Image evidence must open in a larger overlay
  when clicked, with a visible Close button, Escape-key close behavior,
  mouse-wheel zoom, drag-to-pan while zoomed, and zoom reset on open/close.
  Source-table evidence should be embedded or linked as a complete highlighted
  HTML table, not only as a cropped PNG excerpt. Include findings without
  generated images in the same browser with a clear placeholder so the
  front-page sequence remains complete. Keep the later Candidate Statistics,
  Inputs, Risk Summary, and Findings sections semantically equivalent to the
  original report.
- Use `--max-per-type 0` as the default visual-report expansion setting; `0`
  means unlimited, so every screen-positive candidate in the available audit
  CSV outputs is expanded in the main report. Positive values remain allowed
  only for deliberate quick previews. Sort findings by candidate type, then risk
  severity, then stable issue ID. Add a table of contents that jumps to each
  candidate type. In HTML reports, keep the table of contents visible while
  scrolling with a sticky side navigation.
- When any candidate type is deliberately truncated after a positive
  `--max-per-type` limit, create
  a separate companion document, `visual_report/full_csv_visualization.html`
  plus `visual_report/full_csv_visualization.md`, that visualizes the complete
  backing CSV rows for every truncated type. Link this companion document from
  the top warning area of the main report. The companion document should also be
  sorted by candidate type, include a jump table of contents, and keep the HTML
  navigation sticky during browsing.
- Include exact embedded-image duplicate contact sheets in the report whenever
  `exact_embedded_image_duplicates.csv` is non-empty. Treat these as
  HIGH-RISK_REVIEW when the placements map to different biological labels or
  panels.
- Include near image, rendered-page, and rendered-region contact sheets when
  candidate CSVs are non-empty, but keep them as WARN_REVIEW unless manual
  inspection and label context justify escalation.
- For source-data anomalies with cell coordinates, render the original
  `sheet_csv` as a complete scrollable HTML table and highlight the suspect
  cells. Do not show only a cropped slice as the primary evidence, because
  surrounding rows/columns can be needed to interpret the anomaly. For large
  sheets, keep the complete table in a separate linked evidence HTML page with
  sticky headers and a highlight legend; the main report may embed it in an
  iframe. When a reviewer opens evidence from a specific finding, highlight only
  that finding's cells so other issues in the same sheet do not visually compete
  with the current problem. For cross-sheet or cross-workbook A/B findings,
  render a per-issue evidence page that shows each side in its own complete
  source table; never project B-side coordinates onto the A-side sheet. Directly
  opening the base table page may show all highlights as an overview. This
  applies to arithmetic progressions, constant-pair-sum blocks, duplicate short
  numeric sequences, fixed-ratio scaled sequences, same-sheet sliding-window
  arithmetic relation candidates, duplicate numeric matrix blocks, and
  group-level digit/order screens.
- When rendering HTML/Markdown tables or PNG table excerpts, normalize only
  obvious binary floating-point display tails in the presentation layer, such as
  `18.010000000000002` to `18.01` or `8.3699999999999992` to `8.37`. Do not
  rewrite the backing CSVs or suppress true long-decimal evidence. Source CSV
  links must still expose the original values used by the checks. Also expand
  compact scientific notation to ordinary decimal display when the resulting
  string is reasonably short, so reviewers do not see avoidable mixtures of
  `1.2e-3`, `0.0012`, and binary-float tails in the visual layer.
- For paired or multi-block numeric anomalies, color each issue or `match_id`
  with stable role colors. Within one `NUM-xxx`, each evidence block should
  have its own fill/outline: for A/B evidence, A and B should be different
  colors; for arithmetic or derived-relation evidence, source A, source B, and
  target should be three different colors when all three blocks are present.
  Keep these colors stable for that issue in the full table, front-page iframe,
  and per-issue evidence page. When a reviewer opens a specific `NUM-xxx`
  evidence page or the front-page preview for that finding, render only that
  `NUM-xxx` highlight set; do not display other `NUM` highlights from the same
  data block.
- User-facing visual reports should write the `Specific issue` content in
  Chinese when the surrounding report is Chinese. The description must state
  what was detected, the key threshold or residual, why the item is WARN or
  HIGH-RISK review, and what context could explain or lower the concern. Keep
  backing CSV field names machine-readable; localize the narrative explanation,
  not the raw CSV schema.
- Do not let the visual evidence table imply completeness when a positive
  `--max-per-type` preview cap is used. Important later candidates such as
  `SDS-0053` can be hidden by a per-type display cap even though they exist in
  the Source CSV. The default must be unlimited; if a preview cap is deliberately
  used, the report must direct reviewers to the full CSV or to rerun
  `scripts/build_visual_audit_report.py` with `--max-per-type 0`.
- Every visual finding must still include the backing CSV path, raw data file or
  PDF path, sheet/cell range when available, risk grade, and cautious
  interpretation. The visual is evidence support, not a substitute for the CSV.

## Raw Source Data Red Flags

Escalate to HIGH-RISK when any of these are present and not explained by the
methods or source-data structure:

- Values for biological replicates form exact or near-exact arithmetic
  progressions centered on the mean.
- Adjacent biological-replicate pairs repeatedly sum to an exact or near-exact
  constant, such as `x1+x2=2`, `x3+x4=2`, and `x5+x6=2` across many rows, or a
  control group repeatedly has row totals forced to the nominal n. Treat this as
  HIGH-RISK when no documented pairwise normalization, shared calibrator,
  percentage-complement, technical-duplicate, or compositional constraint
  explains it.
- Replicate values show repeated pseudo-random structure, such as unusually
  patterned last digits, low digit entropy, excessive terminal 0/5 values,
  monotonic row order, or summary-derived jitter, across multiple independent
  groups.
- Nominally independent biological replicates, conditions, samples, or panels
  contain completely identical long-decimal values or highly similar
  long-decimal tails. Treat exact repeated values with 3 or more decimal places,
  or repeated/similar decimal tails of 3 or more digits across independent
  observations, as HIGH-RISK unless the source explicitly documents a shared
  calibrator, detection floor, technical duplicate, or rounding rule.
- Same-panel adjacent condition columns, same-panel same-condition columns, or
  cross-panel same-condition columns contain identical short vectors of length
  `3` or more, or contain a local run of at least `3` consecutive identical
  numeric positions. Grade by context: WARN for any retained short-column reuse
  so it is reviewed, even when a shared source is plausible. Grade as HIGH-RISK
  when the matched values are nominally independent cytokines, conditions,
  samples, or panels and no shared calculation source, technical duplicate,
  calibrator, or rounding/export rule is documented.
- Two figure/panel-anchored numeric rectangles in the same sheet contain an
  identical matrix, a high-overlap matrix, or a continuous identical submatrix
  of values across multiple rows and columns. Use
  `duplicate_numeric_matrix_blocks.csv` to catch cases where only part of a
  larger panel block is duplicated. Grade as HIGH-RISK when the panels represent
  nominally different drugs, treatments, cell lines, assays, or summary claims
  and no documented shared source, technical duplicate, or intentional reuse
  explains it. If downstream AUC, IC50, mean, error-bar, or other summary rows
  differ after identical or highly overlapping raw blocks, explicitly report
  that the summaries are not reproducible from the duplicated source block
  without an additional documented transformation.
- Same-layout condition columns or group vectors are exact or near-exact fixed
  scalar multiples of one another, such as `B = 2 * A`, `B = 0.5 * A`, or
  `B = 3/5 * A`, across at least `3` aligned numeric positions. Grade as
  HIGH-RISK when the scaled vectors are nominally independent conditions,
  genes, samples, or panels and no shared normalization, calibrator, technical
  duplicate, or deterministic calculation rule is documented.
- Three or more adjacent values in one same-sheet target window can be generated
  from one or two other same-sheet windows by basic arithmetic, such as
  `target=A+B`, `target=A-B`, `target=A*B`, `target=A/B`, fixed ratio, fixed
  offset, constant-sum complement such as `target=2-source`, reciprocal
  transform such as `target=10/source`, affine transform such as
  `target=2*source+1`, weighted combination such as `target=A+0.5*B`, or
  composition fraction such as `target=100*A/(A+B)`. Grade as WARN by default
  because many source-data workbooks contain legitimate calculated columns.
  Escalate to HIGH-RISK only when the target appears to be an independent
  primary measurement and there is no label, formula, legend, or source-table
  structure documenting a calculation relationship. Do not escalate axis-like
  fields, IDs, dose/time/concentration series, totals, means, ratios,
  percentages, normalized values, AUC/IC50, or other explicitly derived
  summaries solely because they satisfy arithmetic.
- A derivative panel, such as AUC or percentage, cannot be recalculated from the
  parent raw panel even though the legend says it is calculated from that panel.
- Different panels or conditions share an exact long numeric sequence.
- Recalculated p values match only the suspicious reconstructed source values
  and not the underlying raw or parent data.
- Means, percentages, ordinal scores, n, SD, SEM, or error bars are
  mathematically incompatible with the supplied raw values.

## Reporting Rules

Use `templates/audit_report.md` as the default narrative report shape and
generate `visual_report/visual_report.html` for visual review whenever audit CSV
outputs are available. Keep numeric-data, statistical, figure-reproduction, and
image-integrity concerns in separate sections. For every issue, include the
source file, figure or table location, the exact recalculated value when
available, the reported value when available, visual evidence path when
available, and a risk grade.

Every user-facing finding table must include a `Raw data file` column or field
that names the exact source workbook, CSV, PDF, image file, or other raw-data
artifact supporting the finding. If the finding depends on missing raw data,
write the expected raw-data file type or the available parent file, such as the
PDF-only source. For final reports, include a concise key-findings table with
`Issue ID`, `Risk`, `Figure/panel`, `Raw data file`, `Sheet/cells`, and
`Finding`. In Chinese reports, use `具体问题` for the localized narrative version
of `Specific issue`, and include enough detail for the reader to understand the
exact anomaly without opening the CSV. When exporting to PDF, preserve the
`Raw data file` field in the PDF output.

Do not write "the authors fabricated data" unless the user provides external
verified evidence beyond this audit. Prefer cautious statements:

- "This is a high-risk inconsistency."
- "This requires author clarification."
- "The reported value is mathematically incompatible with the stated n."
- "The figure cannot be reproduced from the provided raw data."
- "This pattern is suspicious but not conclusive."
