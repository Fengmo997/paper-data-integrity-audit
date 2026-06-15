# Decimal Precision Rules

Use decimal precision as a screening signal, not as proof of misconduct.

## Common Checks

- Cell counts, colony counts, animal counts, and positive-cell counts should be
  integers before normalization.
- Percentages must be compatible with their denominator. For denominator n, the
  percentage step is 100 / n.
- Ordinal pathology scores should be integers or values from the stated scoring
  rubric; group means must be compatible with n and the score grid.
- qPCR Ct values commonly have limited instrument precision; many raw Ct columns
  with 3 or more decimal places require explanation.
- Mixed decimal precision in the same raw-data column can be valid after manual
  entry, but it should be flagged when no measurement-method reason is given.
- Repeated decimal tails across many unrelated values are higher-risk than one
  repeated rounded value.
- Values ending in 0 or 5 can be normal after rounding. Treat enrichment as a
  risk signal only when sample size is adequate and the column is expected to be
  raw rather than rounded.
- Last-digit distributions should be summarized for every numeric source table,
  but interpreted only with assay precision, rounding, and n in view.
- Pseudo-randomness screens should look for structured decimal tails, symmetric
  jitter around means, monotonic row order, and copied terminal-digit patterns.

## Higher-Risk Patterns

- A mean or percentage is mathematically impossible for the stated n.
- Integer or ordinal raw measurements are reported with arbitrary long decimals.
- Completely identical long-decimal values, or highly similar long-decimal
  tails, appear across nominally independent biological replicates, conditions,
  samples, or panels. Exact repeated values with 3 or more decimal places, or
  repeated/similar decimal tails of 3 or more digits, should be graded
  HIGH-RISK unless the source explicitly documents a shared calibrator,
  detection floor, technical duplicate, or rounding/export rule.
- Multiple unrelated panels share the same decimal sequence or repeated tails.
- Multiple unrelated replicate groups show the same pseudo-random terminal-digit
  structure or reconstructed decimal pattern.
- Normalized data are provided without the raw values needed to verify the
  normalization denominator.
