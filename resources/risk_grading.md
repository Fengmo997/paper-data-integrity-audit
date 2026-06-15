# Risk Grading

Use one overall verdict and issue-level risk labels.

## PASS

- Data precision matches the assay.
- Distribution is plausible for the assay and n.
- Raw data reproduce figures.
- Recalculated statistics match reported values within rounding tolerance.
- Only minor rounding differences are present.
- Last-digit, first-digit, and numeric-distribution screens do not show repeated
  cross-panel structure after accounting for assay scale and rounding.

## WARN

- Some source values or metadata are missing.
- Decimal precision is inconsistent but explainable.
- Some p values, means, or error bars differ slightly.
- Small n limits distribution interpretation.
- Patterns are suspicious but have plausible technical explanations.
- One isolated last-digit or first-digit anomaly appears, but n is small or the
  assay context plausibly explains the pattern.
- Method wording is internally inconsistent, but p values reproduce under a
  reasonable documented model.

## HIGH-RISK

- Raw data cannot reproduce figure means, visible points, or error bars.
- Reported means or percentages are mathematically incompatible with stated n.
- Integer or ordinal raw data contain impossible decimal values.
- Multiple panels reuse the same data sequence without explanation.
- Biological replicate values form exact or near-exact arithmetic progressions
  suggesting reconstruction from summary statistics.
- Biological replicate values repeatedly satisfy adjacent-pair or mirror-pair
  constant-sum constraints, such as many pairs summing to 2 in normalized
  control data, without a documented pairwise normalization, calibrator,
  technical-duplicate, percentage-complement, or compositional rule.
- Nominally independent biological replicates, conditions, samples, or panels
  contain completely identical long-decimal values or highly similar
  long-decimal tails. Exact repeated values with 6 or more decimal places, or
  repeated/similar decimal tails of 6 or more digits, are HIGH-RISK unless the
  source explicitly documents a shared calibrator, detection floor, technical
  duplicate, or rounding/export rule.
- A derived panel, such as AUC, ratio, percentage, or fold change, cannot be
  recalculated from its declared parent raw panel.
- Reported p values reproduce only from suspicious reconstructed source values,
  not from parent/raw values.
- SD, SEM, p values, or n are repeatedly inconsistent.
- Multiple unrelated groups show pseudo-random or reconstructed numeric
  structure, such as arithmetic progressions, symmetric jitter around means,
  repeated terminal-digit templates, or monotonic replicate order.
- Images show duplicated or manipulated regions that affect conclusions.
- Different assays show implausibly identical sample-level patterns.

## INSUFFICIENT DATA

- Raw data are unavailable for the relevant claim.
- The figure source data omit required denominators, sample IDs, or exclusions.
- Image panels lack original-resolution files needed for integrity screening.
- Reported statistics cannot be recalculated from the provided materials.

## Required Wording

- "This is a high-risk inconsistency."
- "This requires author clarification."
- "The reported value is mathematically incompatible with the stated n."
- "The figure cannot be reproduced from the provided raw data."
- "This pattern is suspicious but not conclusive."
