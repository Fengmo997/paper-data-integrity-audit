# Distribution Audit Rules

Distribution checks require assay context and adequate n. For n less than 5,
report "limited interpretability" unless the anomaly is arithmetic or directly
reproducible.

## Common Checks

- Compare n, mean, median, SD, SEM, CV, min, max, IQR, skewness, kurtosis,
  duplicate count, decimal-place distribution, last-digit distribution,
  first-digit distribution, digit entropy, terminal 0/5 fraction, and digit
  uniformity screens.
- Check exact or near-exact arithmetic progressions within biological replicate
  values, especially when n is 5 or larger.
- Check pseudo-random or reconstructed-value patterns: monotonic replicate
  order, symmetric values around the mean, repeated decimal tails, repeated
  terminal-digit patterns, and identical sample ranking across unrelated assays.
- Check constant-sum and mirror constraints: adjacent pairs with repeated sums,
  row totals forced to the nominal n after normalization, left-right mirror
  pairs around a mean, and percentage/compositional complements.
- Check whether SD or SEM is identical across unrelated groups or assays.
- Check whether within-group variance is implausibly small for the assay.
- Check whether group separation is overly perfect relative to known assay noise.
- Check whether large samples lack plausible natural outliers.
- Check whether duplicate values exceed what rounding and assay resolution would
  reasonably explain.
- Check whether dose-response or time-course curves are perfectly monotonic at
  small n across many panels.
- Check whether sample rankings are suspiciously identical across unrelated
  assays.
- Check same-panel adjacent condition columns, same-panel same-condition
  columns, and cross-panel same-condition columns for identical short vectors
  of length `3` or more, including local runs where at least `3` consecutive
  numeric positions match.

## Interpretation Guardrails

- Small n limits shape-based inference.
- Rounded or binned assays naturally create duplicates.
- Normalization to controls can reduce variance, but raw values and denominators
  are required to verify the transformation.
- Control normalization can force a group mean to 1, but it does not by itself
  explain repeated adjacent-pair sums such as `x1+x2=2`, `x3+x4=2`, and
  `x5+x6=2` across many independent rows.
- Batch effects can create distribution differences that are not integrity
  problems; inspect sample and batch labels before grading.
- Time axes, wavelength axes, and chromatogram baseline zeros can be repeated
  legitimately; do not treat shared axes as duplicated measurements.
- A derived panel that claims to be calculated from another panel must reproduce
  per-sample values, not only group means.
- Last-digit and first-digit tests are screening tools. They are not conclusive
  for small n, bounded assays, normalized ratios, rounded scores, or instrument
  exports unless supported by repeated cross-panel evidence.
- Benford-style first-digit checks require many positive values spanning at
  least two orders of magnitude; do not apply them to narrow-range fold changes,
  percentages, Ct values, scores, or normalized expression.

## High-Risk Distribution Patterns

- Every biological replicate group in one panel is an arithmetic progression
  centered on the group mean.
- Adjacent biological-replicate pairs repeatedly have the same exact or
  near-exact sum across many rows or groups. Detection target: within each
  candidate group block, compute adjacent pair sums row-wise; retain candidates
  when `total_pairs >= 12`, `rows_with_pairs >= 4`, one sum cluster accounts for
  at least `80%` of all pairs and at least `10` pairs, and at least `80%` of
  rows have totals implied by that pair sum. Grade as HIGH-RISK when the values
  are nominally independent observations and the source does not document
  pairwise normalization, paired calibrators, technical duplicates, percentage
  complements, or compositional constraints.
- Normalized control rows repeatedly have totals exactly equal to n, especially
  when the same rows also show constant adjacent-pair sums. Group-total
  equality alone is a weaker screen because mean normalization can produce it;
  pair-level equality is the stronger red flag.
- Left-right or otherwise indexed mirror pairs repeatedly satisfy
  `x_i + x_j = 2 * center`, where `center` is the group mean or the normalized
  control value. Check adjacent, edge-mirror, and sample-ID matched pairings
  when the workbook layout suggests possible pairing.
- Values appear generated from `mean +/- k * step`, where `step` is compatible
  with the reported SD or SEM.
- Nominally independent observations contain completely identical long-decimal
  values or highly similar long-decimal tails. Treat exact repeats with 3 or
  more decimal places, or repeated/similar tails of 3 or more digits across
  independent biological replicates, conditions, samples, or panels, as
  HIGH-RISK unless a documented technical reason is present.
- Same-panel adjacent or same-condition columns, or cross-panel same-condition
  columns, contain identical short numeric vectors of length `3` or more, or a
  local run of at least `3` consecutive identical values. Grade as HIGH-RISK
  when the values are nominally independent cytokines, conditions, samples, or
  panels and no shared calculation source, calibrator, technical duplicate, or
  rounding/export rule is documented; grade as WARN when a shared source is
  plausible but not explicit.
- Multiple unrelated biological groups show pseudo-random structure, such as
  low last-digit entropy, repeated terminal-digit templates, monotonic row
  order, or symmetric jitter around means.
- Recalculated parent-panel values, such as per-animal AUC, do not match the
  derivative source-data values.
- Long numeric sequences are exactly duplicated across nominally different
  panels or conditions.

## Constant-Sum False-Positive Checks

- Shared axes, dose labels, time labels, and denominator rows are not replicate
  measurements.
- Percentages or compositional parts can legitimately sum to 100 or 1 when they
  are parts of the same whole; do not grade as HIGH-RISK unless the table labels
  claim independent observations.
- Technical duplicate averaging, paired calibrator normalization, plate-pair
  correction, or sample-ID matched transformations can produce paired
  constraints, but the source file or method must document the rule.
- Display rounding can create occasional exact sums; repeated exact sums across
  many rows, genes, panels, or assays remain suspicious after checking the raw
  stored values.
