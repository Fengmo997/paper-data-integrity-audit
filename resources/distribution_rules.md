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

## Interpretation Guardrails

- Small n limits shape-based inference.
- Rounded or binned assays naturally create duplicates.
- Normalization to controls can reduce variance, but raw values and denominators
  are required to verify the transformation.
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
- Values appear generated from `mean +/- k * step`, where `step` is compatible
  with the reported SD or SEM.
- Nominally independent observations contain completely identical long-decimal
  values or highly similar long-decimal tails. Treat exact repeats with 6 or
  more decimal places, or repeated/similar tails of 6 or more digits across
  independent biological replicates, conditions, samples, or panels, as
  HIGH-RISK unless a documented technical reason is present.
- Multiple unrelated biological groups show pseudo-random structure, such as
  low last-digit entropy, repeated terminal-digit templates, monotonic row
  order, or symmetric jitter around means.
- Recalculated parent-panel values, such as per-animal AUC, do not match the
  derivative source-data values.
- Long numeric sequences are exactly duplicated across nominally different
  panels or conditions.
