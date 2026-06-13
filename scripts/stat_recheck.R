#!/usr/bin/env Rscript

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 6) {
  stop("Usage: Rscript stat_recheck.R input.csv group_col value_col control treatment output.csv", call. = FALSE)
}

input <- args[[1]]
group_col <- args[[2]]
value_col <- args[[3]]
control <- args[[4]]
treatment <- args[[5]]
output <- args[[6]]

df <- read.csv(input, stringsAsFactors = FALSE, check.names = FALSE)
if (!group_col %in% names(df)) stop("group_col not found: ", group_col, call. = FALSE)
if (!value_col %in% names(df)) stop("value_col not found: ", value_col, call. = FALSE)

df[[value_col]] <- suppressWarnings(as.numeric(gsub("%|,", "", df[[value_col]])))
df <- df[is.finite(df[[value_col]]) & nzchar(as.character(df[[group_col]])), , drop = FALSE]

summarize_group <- function(g) {
  x <- df[df[[group_col]] == g, value_col, drop = TRUE]
  data.frame(
    test = "summary",
    group1 = g,
    group2 = "",
    n1 = length(x),
    n2 = NA_integer_,
    statistic = NA_real_,
    p_value = NA_real_,
    mean1 = if (length(x)) mean(x) else NA_real_,
    mean2 = NA_real_,
    sd1 = if (length(x) > 1) sd(x) else NA_real_,
    sd2 = NA_real_,
    sem1 = if (length(x) > 1) sd(x) / sqrt(length(x)) else NA_real_,
    sem2 = NA_real_,
    note = "",
    stringsAsFactors = FALSE
  )
}

records <- lapply(sort(unique(df[[group_col]])), summarize_group)

x <- df[df[[group_col]] == control, value_col, drop = TRUE]
y <- df[df[[group_col]] == treatment, value_col, drop = TRUE]
if (length(x) > 1 && length(y) > 1) {
  welch <- t.test(x, y, var.equal = FALSE)
  student <- t.test(x, y, var.equal = TRUE)
  wilcox <- suppressWarnings(wilcox.test(x, y, exact = FALSE))
  two_group <- data.frame(
    test = c("welch_t_test", "student_t_test", "wilcoxon_rank_sum"),
    group1 = control,
    group2 = treatment,
    n1 = length(x),
    n2 = length(y),
    statistic = c(unname(welch$statistic), unname(student$statistic), unname(wilcox$statistic)),
    p_value = c(welch$p.value, student$p.value, wilcox$p.value),
    mean1 = mean(x),
    mean2 = mean(y),
    sd1 = sd(x),
    sd2 = sd(y),
    sem1 = sd(x) / sqrt(length(x)),
    sem2 = sd(y) / sqrt(length(y)),
    note = c("default for unequal variance", "assumes equal variance", "nonparametric rank test"),
    stringsAsFactors = FALSE
  )
  records[[length(records) + 1]] <- two_group
}

group_count <- length(unique(df[[group_col]]))
if (group_count > 2) {
  formula <- stats::as.formula(paste(value_col, "~", group_col))
  fit <- stats::aov(formula, data = df)
  anova_table <- summary(fit)[[1]]
  kw <- kruskal.test(formula, data = df)
  omnibus <- data.frame(
    test = c("one_way_anova", "kruskal_wallis"),
    group1 = "all_groups",
    group2 = "",
    n1 = nrow(df),
    n2 = NA_integer_,
    statistic = c(anova_table[1, "F value"], unname(kw$statistic)),
    p_value = c(anova_table[1, "Pr(>F)"], kw$p.value),
    mean1 = NA_real_,
    mean2 = NA_real_,
    sd1 = NA_real_,
    sd2 = NA_real_,
    sem1 = NA_real_,
    sem2 = NA_real_,
    note = c("parametric omnibus test", "nonparametric omnibus test"),
    stringsAsFactors = FALSE
  )
  records[[length(records) + 1]] <- omnibus
}

out <- do.call(rbind, records)
write.csv(out, output, row.names = FALSE, na = "")
message("Wrote statistical recalculation to ", output)

