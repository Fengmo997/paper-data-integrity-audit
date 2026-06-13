#!/usr/bin/env Rscript

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 4) {
  stop("Usage: Rscript distribution_audit.R input.csv group_col value_col output.csv", call. = FALSE)
}

input <- args[[1]]
group_col <- args[[2]]
value_col <- args[[3]]
output <- args[[4]]

df <- read.csv(input, stringsAsFactors = FALSE, check.names = FALSE, colClasses = "character")
if (!group_col %in% names(df)) stop("group_col not found: ", group_col, call. = FALSE)
if (!value_col %in% names(df)) stop("value_col not found: ", value_col, call. = FALSE)

clean_numeric <- function(x) {
  suppressWarnings(as.numeric(gsub("%|,", "", trimws(x))))
}

decimal_places <- function(x) {
  x <- trimws(gsub("%|,", "", x))
  ifelse(grepl("\\.", x), nchar(sub("^[^.]*\\.", "", x)), 0L)
}

last_digit <- function(x) {
  digits <- gsub("\\D", "", x)
  ifelse(nchar(digits) > 0, substr(digits, nchar(digits), nchar(digits)), NA_character_)
}

skewness <- function(x) {
  x <- x[is.finite(x)]
  if (length(x) < 3 || sd(x) == 0) return(NA_real_)
  mean(((x - mean(x)) / sd(x))^3)
}

kurtosis <- function(x) {
  x <- x[is.finite(x)]
  if (length(x) < 4 || sd(x) == 0) return(NA_real_)
  mean(((x - mean(x)) / sd(x))^4) - 3
}

collapse_counts <- function(x) {
  x <- x[!is.na(x)]
  if (!length(x)) return("")
  counts <- sort(table(x), decreasing = TRUE)
  paste(paste(names(counts), as.integer(counts), sep = ":"), collapse = ";")
}

groups <- sort(unique(df[[group_col]][nzchar(df[[group_col]])]))
records <- lapply(groups, function(g) {
  raw <- df[df[[group_col]] == g, value_col, drop = TRUE]
  values <- clean_numeric(raw)
  values <- values[is.finite(values)]
  n <- length(values)
  dup_count <- if (n > 0) n - length(unique(values)) else 0L
  dec <- decimal_places(raw)
  digs <- last_digit(raw)
  data.frame(
    group = g,
    n = n,
    mean = if (n) mean(values) else NA_real_,
    median = if (n) median(values) else NA_real_,
    sd = if (n > 1) sd(values) else NA_real_,
    sem = if (n > 1) sd(values) / sqrt(n) else NA_real_,
    cv = if (n > 1 && mean(values) != 0) sd(values) / abs(mean(values)) else NA_real_,
    min = if (n) min(values) else NA_real_,
    max = if (n) max(values) else NA_real_,
    iqr = if (n) IQR(values) else NA_real_,
    skewness = skewness(values),
    kurtosis_excess = kurtosis(values),
    duplicate_value_count = dup_count,
    duplicate_rate = if (n) dup_count / n else NA_real_,
    decimal_place_counts = collapse_counts(as.character(dec[!is.na(dec)])),
    last_digit_counts = collapse_counts(digs),
    stringsAsFactors = FALSE,
    check.names = FALSE
  )
})

out <- do.call(rbind, records)
write.csv(out, output, row.names = FALSE, na = "")
message("Wrote distribution audit to ", output)

