#!/usr/bin/env Rscript

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 6) {
  stop("Usage: Rscript figure_reproduce.R input.csv group_col value_col output_prefix plot_type error_type", call. = FALSE)
}

input <- args[[1]]
group_col <- args[[2]]
value_col <- args[[3]]
output_prefix <- args[[4]]
plot_type <- args[[5]]
error_type <- args[[6]]

df <- read.csv(input, stringsAsFactors = FALSE, check.names = FALSE)
if (!group_col %in% names(df)) stop("group_col not found: ", group_col, call. = FALSE)
if (!value_col %in% names(df)) stop("value_col not found: ", value_col, call. = FALSE)

df[[value_col]] <- suppressWarnings(as.numeric(gsub("%|,", "", df[[value_col]])))
df <- df[is.finite(df[[value_col]]) & nzchar(as.character(df[[group_col]])), , drop = FALSE]
df[[group_col]] <- factor(df[[group_col]], levels = unique(df[[group_col]]))

groups <- levels(df[[group_col]])
values_by_group <- split(df[[value_col]], df[[group_col]])
n <- vapply(values_by_group, length, integer(1))
means <- vapply(values_by_group, mean, numeric(1))
sds <- vapply(values_by_group, function(x) if (length(x) > 1) sd(x) else NA_real_, numeric(1))
sems <- sds / sqrt(n)
ci95 <- qt(0.975, pmax(n - 1, 1)) * sems

errors <- switch(
  tolower(error_type),
  sd = sds,
  sem = sems,
  ci = ci95,
  none = rep(0, length(groups)),
  stop("error_type must be sd, sem, ci, or none", call. = FALSE)
)

summary_df <- data.frame(
  group = groups,
  n = n,
  mean = means,
  sd = sds,
  sem = sems,
  ci95 = ci95,
  error_type = error_type,
  plotted_error = errors,
  stringsAsFactors = FALSE
)
write.csv(summary_df, paste0(output_prefix, "_summary.csv"), row.names = FALSE, na = "")

png(paste0(output_prefix, ".png"), width = 1800, height = 1400, res = 220)
par(mar = c(7, 5, 2, 1), las = 2)

if (tolower(plot_type) == "bar") {
  ylim <- range(c(0, means - errors, means + errors, df[[value_col]]), na.rm = TRUE)
  xpos <- barplot(means, names.arg = groups, col = "grey80", border = "grey25", ylim = ylim, ylab = value_col)
  arrows(xpos, means - errors, xpos, means + errors, angle = 90, code = 3, length = 0.04)
  stripchart(values_by_group, vertical = TRUE, add = TRUE, method = "jitter", pch = 16, col = "grey20", at = xpos)
} else if (tolower(plot_type) == "box") {
  boxplot(values_by_group, ylab = value_col, col = "grey90", border = "grey25")
  stripchart(values_by_group, vertical = TRUE, add = TRUE, method = "jitter", pch = 16, col = "grey20")
} else if (tolower(plot_type) == "dot") {
  stripchart(values_by_group, vertical = TRUE, method = "jitter", pch = 16, col = "grey20", ylab = value_col)
  points(seq_along(groups), means, pch = 95, cex = 2.5, col = "red")
  arrows(seq_along(groups), means - errors, seq_along(groups), means + errors, angle = 90, code = 3, length = 0.04, col = "red")
} else {
  dev.off()
  stop("plot_type must be bar, box, or dot", call. = FALSE)
}

dev.off()
message("Wrote ", output_prefix, ".png and ", output_prefix, "_summary.csv")

