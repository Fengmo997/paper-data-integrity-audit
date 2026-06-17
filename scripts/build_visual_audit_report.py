#!/usr/bin/env python3
"""Build a visual paper-data integrity report from audit CSV outputs.

This script does not decide fraud or misconduct. It turns existing screening
outputs into reviewable visual evidence:

* image duplicate contact sheets from PDF/image scans
* rendered source-data table excerpts with suspicious cells highlighted
* a concise HTML and Markdown report that links each finding to its evidence

For paired or grouped numeric anomalies, each issue/match_id receives stable
role colors. Evidence pages opened for one issue show only that issue's
highlighted cells, with source and target ranges visually separated when the
finding has calculation direction.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import math
import re
import shutil
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


DEFAULT_MAX_PER_TYPE = 0
FLOAT_DISPLAY_ARTIFACT_MIN_DECIMAL_PLACES = 10
FLOAT_DISPLAY_ARTIFACT_MAX_SCALE = 12
FLOAT_DISPLAY_MAX_ABS_CORRECTION = Decimal("1e-12")
FLOAT_DISPLAY_MAX_REL_CORRECTION = Decimal("1e-14")
SCIENTIFIC_TO_FIXED_MAX_CHARS = 36
NUMERIC_TOKEN_RE = re.compile(r"(?<![\w.])([+-]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[eE][+-]?\d+)?%?)(?![\w.])")
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

CATEGORY_ORDER = [
    "Numeric matrix reuse",
    "Numeric sequence reuse",
    "Fixed-ratio numeric vectors",
    "Window arithmetic relation",
    "Exact long-decimal repeat",
    "Formula validation",
    "Group-level numeric screen",
    "Image integrity",
    "Image exact embedded duplicates",
    "Image near embedded matches",
    "Rendered page matches",
    "Rendered region candidates",
]

PALETTE = [
    ("#FDE2E2", "#F97373", "#7F1D1D"),
    ("#E0F2FE", "#38BDF8", "#075985"),
    ("#DCFCE7", "#4ADE80", "#14532D"),
    ("#FEF3C7", "#FBBF24", "#78350F"),
    ("#EDE9FE", "#A78BFA", "#4C1D95"),
    ("#FFE4E6", "#FB7185", "#881337"),
    ("#CCFBF1", "#2DD4BF", "#134E4A"),
    ("#FCE7F3", "#F472B6", "#831843"),
    ("#E2E8F0", "#94A3B8", "#1E293B"),
    ("#FFEDD5", "#FB923C", "#7C2D12"),
]


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def read_optional_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace").strip()


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_") or "item"


def col_to_num(col: str) -> int:
    value = 0
    for ch in col:
        value = value * 26 + ord(ch.upper()) - ord("A") + 1
    return value


def num_to_col(num: int) -> str:
    letters = ""
    while num:
        num, rem = divmod(num - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return letters


def ref_to_rc(ref: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"\$?([A-Za-z]+)\$?(\d+)", ref.strip())
    if not match:
        return None
    return int(match.group(2)), col_to_num(match.group(1))


def parse_cell_refs(text: str) -> set[tuple[int, int]]:
    refs: set[tuple[int, int]] = set()
    for token in re.split(r"[;,\s|]+", text or ""):
        token = token.strip()
        if not token:
            continue
        if ":" in token:
            refs.update(parse_range(token))
            continue
        rc = ref_to_rc(token)
        if rc:
            refs.add(rc)
    return refs


def parse_range(text: str) -> set[tuple[int, int]]:
    text = (text or "").strip()
    if not text:
        return set()
    if ":" not in text:
        rc = ref_to_rc(text)
        return {rc} if rc else set()
    left, right = text.split(":", 1)
    a = ref_to_rc(left)
    b = ref_to_rc(right)
    if not a or not b:
        return set()
    r1, c1 = a
    r2, c2 = b
    if r1 > r2:
        r1, r2 = r2, r1
    if c1 > c2:
        c1, c2 = c2, c1
    return {(r, c) for r in range(r1, r2 + 1) for c in range(c1, c2 + 1)}


def cell_range_bounds(cells: set[tuple[int, int]]) -> tuple[int, int, int, int] | None:
    if not cells:
        return None
    rows = [r for r, _ in cells]
    cols = [c for _, c in cells]
    return min(rows), max(rows), min(cols), max(cols)


def load_sheet_csv(sheet_dir: Path, workbook: str, sheet: str) -> tuple[Path | None, list[list[str]]]:
    stem = Path(workbook).stem
    target = f"{safe_name(stem)}__{safe_name(sheet)}.csv"
    path = sheet_dir / target
    if not path.exists():
        matches = list(sheet_dir.glob(f"*__{safe_name(sheet)}.csv"))
        if len(matches) == 1:
            path = matches[0]
        else:
            return None, []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return path, list(csv.reader(handle))


def get_cell(matrix: list[list[str]], row: int, col: int) -> str:
    if row < 1 or col < 1 or row > len(matrix):
        return ""
    line = matrix[row - 1]
    if col > len(line):
        return ""
    return line[col - 1]


def trim_decimal_text(text: str) -> str:
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in {"-0", "+0"}:
        return "0"
    return text


def normalize_scientific_display_token(token: str) -> str:
    text = str(token)
    suffix = "%" if text.endswith("%") else ""
    core = text[:-1] if suffix else text
    if "e" not in core.lower():
        return text
    try:
        value = Decimal(core)
    except InvalidOperation:
        return text
    fixed = trim_decimal_text(format(value, "f"))
    if len(fixed) <= SCIENTIFIC_TO_FIXED_MAX_CHARS:
        return f"{fixed}{suffix}"
    return text


def normalize_float_display_token(token: str) -> str:
    text = str(token)
    text = normalize_scientific_display_token(text)
    suffix = "%" if text.endswith("%") else ""
    core = text[:-1] if suffix else text
    if "." not in core or "e" in core.lower():
        return text
    sign = ""
    unsigned = core
    if unsigned.startswith(("+", "-")):
        sign, unsigned = unsigned[0], unsigned[1:]
    fraction = unsigned.split(".", 1)[1]
    if len(fraction) < FLOAT_DISPLAY_ARTIFACT_MIN_DECIMAL_PLACES:
        return text
    if "000000" not in fraction and "999999" not in fraction:
        return text
    try:
        value = Decimal(core)
    except InvalidOperation:
        return text
    abs_value = abs(value)
    for scale in range(0, FLOAT_DISPLAY_ARTIFACT_MAX_SCALE + 1):
        quantum = Decimal(1).scaleb(-scale)
        try:
            rounded = value.quantize(quantum)
        except InvalidOperation:
            continue
        correction = abs(value - rounded)
        rel_ok = abs_value == 0 or correction / abs_value <= FLOAT_DISPLAY_MAX_REL_CORRECTION
        if correction <= FLOAT_DISPLAY_MAX_ABS_CORRECTION or rel_ok:
            normalized = trim_decimal_text(format(rounded, "f"))
            if sign == "+" and not normalized.startswith("-"):
                normalized = "+" + normalized
            return f"{normalized}{suffix}"
    return text


def display_text(value: object) -> str:
    text = "" if value is None else str(value)
    if not text or not any(ch in text for ch in ".eE"):
        return text
    return NUMERIC_TOKEN_RE.sub(lambda match: normalize_float_display_token(match.group(1)), text)


def text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0], box[3] - box[1]


def choose_font(size: int = 13) -> ImageFont.ImageFont:
    for name in ("arial.ttf", "calibri.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


@dataclass
class Highlight:
    cells: set[tuple[int, int]]
    label: str
    fill: str
    outline: str
    text: str
    issue_id: str = ""


@dataclass
class VisualFinding:
    issue_id: str
    category: str
    risk: str
    title: str
    location: str
    evidence: str
    specific_issue: str = ""
    raw_data_file: str = ""
    visual_path: Path | None = None
    source_csv: Path | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class FindingTypeStat:
    category: str
    source_csv: Path
    total_candidates: int
    shown_in_report: int
    high_risk_candidates: int = 0
    warn_candidates: int = 0
    display_limit: int | None = None
    note: str = ""

    @property
    def hidden_candidates(self) -> int:
        return max(0, self.total_candidates - self.shown_in_report)


def highlight_key(workbook: str, sheet: str) -> str:
    return f"{workbook}\n{sheet}"


def is_image_path(path: Path | None) -> bool:
    return bool(path and path.suffix.lower() in IMAGE_EXTENSIONS)


def is_html_path(path: Path | None) -> bool:
    return bool(path and path.suffix.lower() in {".html", ".htm"})


def alpha_hex(hex_color: str, alpha: str = "33") -> str:
    color = hex_color.strip()
    if re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
        return f"{color}{alpha}"
    return color


def issue_palette(index: int) -> tuple[str, str, str]:
    return palette_for(index)


def issue_role_palette(index: int, role: str) -> tuple[str, str, str]:
    if role in {"b", "source_b"}:
        return palette_for(index + 3)
    if role == "target":
        return palette_for(index + 6)
    return issue_palette(index)


def css_var_name(issue_id: str, suffix: str) -> str:
    return f"--{safe_name(issue_id or 'issue').lower()}-{suffix}"


def render_full_sheet_html(
    matrix: list[list[str]],
    highlights: list[Highlight],
    out_path: Path,
    title: str,
    workbook: str,
    sheet: str,
) -> bool:
    if not matrix:
        return False
    max_cols = max((len(row) for row in matrix), default=0)
    highlight_by_cell: dict[tuple[int, int], list[Highlight]] = defaultdict(list)
    for highlight in highlights:
        for cell in highlight.cells:
            highlight_by_cell[cell].append(highlight)

    rows_html: list[str] = []
    for r, row in enumerate(matrix, start=1):
        cells_html = [f'<th class="row-head">{r}</th>']
        for c in range(1, max_cols + 1):
            raw_value = row[c - 1] if c <= len(row) else ""
            value = display_text(raw_value)
            cell_highlights = highlight_by_cell.get((r, c), [])
            attrs = ""
            class_name = "marked" if cell_highlights else ""
            if cell_highlights:
                primary = cell_highlights[-1]
                labels = "; ".join(h.label for h in cell_highlights)
                texts = "; ".join(h.text for h in cell_highlights if h.text)
                title_text = display_text(labels + (": " + texts if texts else ""))
                issue_attr = " ".join(sorted({h.issue_id for h in cell_highlights if h.issue_id}))
                issue_payload: dict[str, dict[str, str]] = {}
                for highlight in cell_highlights:
                    if not highlight.issue_id:
                        continue
                    issue_title = display_text(
                        highlight.label + (": " + highlight.text if highlight.text else "")
                    )
                    if highlight.issue_id in issue_payload:
                        issue_payload[highlight.issue_id]["title"] += "; " + issue_title
                    else:
                        issue_payload[highlight.issue_id] = {
                            "fill": alpha_hex(highlight.fill, "66"),
                            "outline": highlight.outline,
                            "title": issue_title,
                        }
                issue_payload_text = json.dumps(issue_payload, ensure_ascii=False, separators=(",", ":"))
                attrs = (
                    f' id="cell-{r}-{c}"'
                    f' class="{class_name}"'
                    f' data-issues="{html.escape(issue_attr)}"'
                    f' data-highlight-map="{html.escape(issue_payload_text, quote=True)}"'
                    f' data-fill="{html.escape(alpha_hex(primary.fill, "66"))}"'
                    f' data-outline="{html.escape(primary.outline)}"'
                    f' data-title-overview="{html.escape(title_text)}"'
                    f' title="{html.escape(title_text)}"'
                )
            cells_html.append(f"<td{attrs}>{html.escape(value)}</td>")
        rows_html.append("<tr>" + "".join(cells_html) + "</tr>")

    col_heads = "".join(f"<th>{html.escape(num_to_col(c))}</th>" for c in range(1, max_cols + 1))
    legend_rows = []
    seen: set[str] = set()
    for highlight in highlights:
        key = f"{highlight.label}|{highlight.fill}|{highlight.outline}|{highlight.text}"
        if key in seen:
            continue
        seen.add(key)
        first_cell = next(iter(sorted(highlight.cells)), None)
        href = f"#cell-{first_cell[0]}-{first_cell[1]}" if first_cell else "#top"
        legend_rows.append(
            f'<tr class="legend-row" data-issue="{html.escape(highlight.issue_id)}">'
            f'<td><span class="swatch" style="background:{html.escape(alpha_hex(highlight.fill, "66"))};border-color:{html.escape(highlight.outline)}"></span></td>'
            f"<td><a href=\"{href}\">{html.escape(highlight.label)}</a></td>"
            f"<td>{html.escape(display_text(highlight.text))}</td>"
            "</tr>"
        )
    legend_html = (
        "<table class=\"legend\"><tr><th></th><th>Issue / match</th><th>Description</th></tr>"
        + "".join(legend_rows)
        + "</table>"
        if legend_rows
        else "<p>No highlighted cells were recorded.</p>"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_text(
        out_path,
        f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
body {{ margin: 0; font-family: "Segoe UI", Arial, sans-serif; color: #111827; background: #F8FAFC; }}
#top {{ padding: 18px 22px; background: #FFFFFF; border-bottom: 1px solid #CBD5E1; position: sticky; top: 0; z-index: 3; }}
h1 {{ margin: 0 0 8px; font-size: 22px; letter-spacing: 0; }}
.meta {{ margin: 0; color: #475569; font-size: 13px; overflow-wrap: anywhere; }}
.layout {{ display: grid; grid-template-columns: 320px minmax(0, 1fr); align-items: start; }}
aside {{ position: sticky; top: 88px; max-height: calc(100vh - 88px); overflow: auto; padding: 14px; background: #FFFFFF; border-right: 1px solid #CBD5E1; }}
main {{ min-width: 0; padding: 14px; }}
.legend {{ width: 100%; border-collapse: collapse; margin: 0; table-layout: fixed; }}
.legend th, .legend td {{ border: 1px solid #CBD5E1; padding: 6px; vertical-align: top; font-size: 12px; overflow-wrap: anywhere; }}
.legend th {{ background: #E2E8F0; }}
.swatch {{ display: inline-block; width: 18px; height: 18px; border: 2px solid #111827; border-radius: 4px; }}
.sheet-scroll {{ max-width: 100%; max-height: calc(100vh - 120px); overflow: auto; border: 1px solid #CBD5E1; background: #FFFFFF; }}
table.sheet {{ border-collapse: collapse; table-layout: fixed; }}
.sheet th, .sheet td {{ border: 1px solid #E2E8F0; min-width: 86px; max-width: 220px; padding: 5px 7px; font-size: 12px; line-height: 1.35; white-space: pre-wrap; overflow-wrap: anywhere; vertical-align: top; }}
.sheet thead th {{ position: sticky; top: 0; z-index: 2; background: #E2E8F0; color: #334155; }}
.sheet .corner, .sheet .row-head {{ position: sticky; left: 0; z-index: 1; background: #F1F5F9; color: #334155; min-width: 54px; max-width: 54px; }}
.sheet thead .corner {{ z-index: 4; }}
.sheet td.marked.active {{ font-weight: 700; }}
.sheet td.marked:not(.active) {{ background: #FFFFFF; outline: none; }}
.single-issue-note {{ margin: 8px 0 0; color: #475569; font-size: 13px; }}
a {{ color: #0F5E9C; }}
@media (max-width: 900px) {{
  .layout {{ display: block; }}
  aside {{ position: static; max-height: none; border-right: 0; border-bottom: 1px solid #CBD5E1; }}
  #top {{ position: static; }}
}}
</style>
</head>
<body>
<section id="top">
<h1>{html.escape(title)}</h1>
<p class="meta">Workbook: {html.escape(workbook or "not recorded")} | Sheet: {html.escape(sheet or "not recorded")} | rendered cells: {len(matrix)} rows x {max_cols} columns. Values are presentation-normalized for obvious binary-float tails and compact scientific notation; backing CSVs are unchanged.</p>
<p class="single-issue-note" id="issue-mode-note">Showing highlighted cells for one issue only.</p>
</section>
<div class="layout">
<aside>
<h2>Highlighted Findings</h2>
{legend_html}
</aside>
<main>
<div class="sheet-scroll">
<table class="sheet">
<thead><tr><th class="corner"></th>{col_heads}</tr></thead>
<tbody>
{''.join(rows_html)}
</tbody>
</table>
</div>
</main>
</div>
<script>
(function () {{
  const params = new URLSearchParams(window.location.search);
  const requestedIssue = params.get("issue") || "";
  const cells = Array.from(document.querySelectorAll("td.marked"));
  const rows = Array.from(document.querySelectorAll(".legend-row"));
  const note = document.getElementById("issue-mode-note");
  function activate(issue) {{
    let activeCount = 0;
    cells.forEach((cell) => {{
      const issues = (cell.dataset.issues || "").split(/\\s+/).filter(Boolean);
      const active = !issue || issues.includes(issue);
      cell.classList.toggle("active", active);
      if (active) {{
        let style = {{}};
        if (issue && cell.dataset.highlightMap) {{
          try {{
            const map = JSON.parse(cell.dataset.highlightMap);
            style = map[issue] || {{}};
          }} catch (error) {{
            style = {{}};
          }}
        }}
        cell.style.background = style.fill || cell.dataset.fill || "#FEF3C7";
        cell.style.outline = `2px solid ${{style.outline || cell.dataset.outline || "#F59E0B"}}`;
        cell.title = style.title || cell.dataset.titleOverview || cell.title || "";
        activeCount += 1;
      }} else {{
        cell.style.background = "";
        cell.style.outline = "";
        cell.title = cell.dataset.titleOverview || cell.title || "";
      }}
    }});
    rows.forEach((row) => {{
      row.style.display = !issue || row.dataset.issue === issue ? "" : "none";
    }});
    if (note) {{
      note.textContent = issue
        ? `Showing highlighted cells for current issue: ${{issue}} (${{activeCount}} cell(s)).`
        : "Showing all highlighted cells in this source table.";
    }}
    const firstActive = document.querySelector("td.marked.active");
    if (firstActive && window.location.hash === "") {{
      firstActive.scrollIntoView({{ block: "center", inline: "center" }});
    }}
  }}
  activate(requestedIssue);
}})();
</script>
</body>
</html>
""",
    )
    return True


def render_table_excerpt(
    matrix: list[list[str]],
    highlights: list[Highlight],
    out_path: Path,
    title: str,
    context: int = 2,
    max_rows: int = 26,
    max_cols: int = 14,
) -> bool:
    all_cells: set[tuple[int, int]] = set()
    for highlight in highlights:
        all_cells.update(highlight.cells)
    bounds = cell_range_bounds(all_cells)
    if bounds is None:
        return False
    r_min, r_max, c_min, c_max = bounds
    r_min = max(1, r_min - context)
    c_min = max(1, c_min - context)
    r_max = min(len(matrix), r_max + context)
    c_max = max(max((len(row) for row in matrix), default=1), c_max + context)
    if r_max - r_min + 1 > max_rows:
        center = (bounds[0] + bounds[1]) // 2
        half = max_rows // 2
        r_min = max(1, center - half)
        r_max = min(len(matrix), r_min + max_rows - 1)
    if c_max - c_min + 1 > max_cols:
        center = (bounds[2] + bounds[3]) // 2
        half = max_cols // 2
        c_min = max(1, center - half)
        c_max = c_min + max_cols - 1

    font = choose_font(13)
    header_font = choose_font(13)
    title_font = choose_font(15)
    probe = Image.new("RGB", (10, 10), "white")
    draw = ImageDraw.Draw(probe)
    row_header_w = 54
    col_widths: list[int] = []
    for c in range(c_min, c_max + 1):
        values = [num_to_col(c)]
        for r in range(r_min, r_max + 1):
            values.append(display_text(get_cell(matrix, r, c)))
        width = min(190, max(64, max(text_size(draw, str(v)[:30], font)[0] for v in values) + 18))
        col_widths.append(width)
    row_h = 28
    title_h = 42
    legend_h = 28 * max(1, min(5, len(highlights)))
    width = row_header_w + sum(col_widths) + 2
    height = title_h + row_h + (r_max - r_min + 1) * row_h + legend_h + 10
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)

    draw.rectangle((0, 0, width - 1, height - 1), outline="#CBD5E1")
    draw.text((10, 10), title[:120], fill="#111827", font=title_font)
    y = title_h
    draw.rectangle((0, y, width, y + row_h), fill="#F1F5F9", outline="#CBD5E1")
    draw.text((8, y + 7), "row", fill="#334155", font=header_font)
    x = row_header_w
    for idx, c in enumerate(range(c_min, c_max + 1)):
        draw.rectangle((x, y, x + col_widths[idx], y + row_h), fill="#F1F5F9", outline="#CBD5E1")
        draw.text((x + 6, y + 7), num_to_col(c), fill="#334155", font=header_font)
        x += col_widths[idx]
    highlight_by_cell: dict[tuple[int, int], Highlight] = {}
    for highlight in highlights:
        for cell in highlight.cells:
            highlight_by_cell[cell] = highlight

    y += row_h
    for r in range(r_min, r_max + 1):
        draw.rectangle((0, y, row_header_w, y + row_h), fill="#F8FAFC", outline="#CBD5E1")
        draw.text((8, y + 7), str(r), fill="#334155", font=header_font)
        x = row_header_w
        for idx, c in enumerate(range(c_min, c_max + 1)):
            h = highlight_by_cell.get((r, c))
            fill = h.fill if h else "white"
            outline = h.outline if h else "#E2E8F0"
            draw.rectangle((x, y, x + col_widths[idx], y + row_h), fill=fill, outline=outline, width=2 if h else 1)
            value = display_text(get_cell(matrix, r, c))
            clipped = value if len(value) <= 28 else value[:25] + "..."
            draw.text((x + 6, y + 7), clipped, fill="#111827", font=font)
            x += col_widths[idx]
        y += row_h

    y += 4
    if highlights:
        seen: set[str] = set()
        for highlight in highlights:
            key = f"{highlight.label}|{highlight.fill}|{highlight.outline}"
            if key in seen:
                continue
            seen.add(key)
            draw.rectangle((10, y + 6, 24, y + 20), fill=highlight.fill, outline=highlight.outline, width=2)
            legend_text = display_text(f"{highlight.label}: {highlight.text}")
            draw.text((32, y + 6), legend_text[:110], fill="#334155", font=font)
            y += 24

    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path)
    return True


def palette_for(index: int) -> tuple[str, str, str]:
    return PALETTE[index % len(PALETTE)]


def summarize_rows(rows: list[dict[str, str]], limit: int = 5) -> list[dict[str, str]]:
    return rows[:limit]


def risk_bucket(risk: str) -> str:
    risk = risk or ""
    if "HIGH" in risk:
        return "HIGH"
    if "WARN" in risk:
        return "WARN"
    return ""


def count_risks(rows: list[dict[str, str]], default_risk: str = "") -> tuple[int, int]:
    high = warn = 0
    for row in rows:
        bucket = risk_bucket(row.get("risk_hint") or row.get("risk") or default_risk)
        if bucket == "HIGH":
            high += 1
        elif bucket == "WARN":
            warn += 1
    return high, warn


def stat_from_rows(
    category: str,
    source_csv: Path,
    rows: list[dict[str, str]],
    shown: int,
    display_limit: int | None = None,
    default_risk: str = "",
    note: str = "",
) -> FindingTypeStat:
    high, warn = count_risks(rows, default_risk)
    return FindingTypeStat(
        category=category,
        source_csv=source_csv,
        total_candidates=len(rows),
        shown_in_report=shown,
        high_risk_candidates=high,
        warn_candidates=warn,
        display_limit=display_limit,
        note=note,
    )


def effective_display_limit(max_per_type: int) -> int | None:
    return max_per_type if max_per_type > 0 else None


def display_limit_label(max_per_type: int | None) -> str:
    if max_per_type is None or max_per_type <= 0:
        return "unlimited"
    return str(max_per_type)


def limit_items(items: list[Any], max_per_type: int) -> list[Any]:
    if max_per_type <= 0:
        return items
    return items[:max_per_type]


def compact_text(text: str, limit: int = 180) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def split_issue_flags(text: str) -> list[str]:
    return [flag.strip() for flag in re.split(r"[;,|]+", text or "") if flag.strip()]


def describe_group_issue(row: dict[str, str]) -> str:
    descriptions: list[str] = []
    flags = split_issue_flags(row.get("issue_flags", ""))
    values = compact_text(row.get("values", ""), 120)
    cells = compact_text(row.get("value_cells", "") or row.get("group_cell_range", ""), 100)
    for flag in flags:
        if flag == "near_exact_arithmetic_progression":
            descriptions.append(
                "重复/replicate 数值近似构成等差排列"
                f"（步长={row.get('arithmetic_step')}，最大步长偏差={row.get('arithmetic_max_step_deviation')}）。"
            )
        elif flag == "constant_adjacent_pair_sum_pattern":
            descriptions.append(
                "相邻重复值成对反复满足同一个加和目标"
                f"（目标和={row.get('pair_sum_target')}，匹配 pair={row.get('pair_sum_matching_pairs')}/"
                f"{row.get('pair_sum_total_pairs')}）。"
            )
        elif flag == "low_last_digit_entropy":
            descriptions.append(
                "末位数字分布异常集中"
                f"（熵={row.get('last_digit_entropy_log2')}，计数={row.get('last_digit_counts')}）。"
            )
        elif flag == "last_digit_concentration":
            descriptions.append(
                "某一个末位数字占比过高"
                f"（最高占比={row.get('last_digit_max_fraction')}，计数={row.get('last_digit_counts')}）。"
            )
        elif flag == "terminal_0_5_enrichment":
            descriptions.append(
                "末位 0/5 出现比例偏高"
                f"（比例={row.get('terminal_0_5_fraction')}，计数={row.get('last_digit_counts')}）。"
            )
        elif flag == "long_decimal_precision_ge_3":
            descriptions.append(
                "该组至少有一个数值显示小数位数 >=3；需要结合实验精度或导出规则判断是否合理"
                f"（小数位分布={row.get('decimal_place_counts')}）。"
            )
        elif flag == "monotonic_nonconstant_sequence":
            descriptions.append(
                "源表顺序中的数值单调但并非常数；需要确认是否只是排序结果或坐标轴，而不是独立 replicate 顺序"
                f"（values={values}; cells={cells}）。"
            )
        else:
            descriptions.append(f"筛查阳性标记：{flag}。")
    return " ".join(descriptions) or "组级数值筛查阳性，需要结合原始表上下文复核。"


def describe_exact_repeat(rows: list[dict[str, str]]) -> str:
    if not rows:
        return "同一个显示数值在多个单元格中精确重复。"
    row = rows[0]
    cells = row.get("all_repeat_cells") or ";".join(item.get("cell", "") for item in rows)
    return (
        "同一个显示数值在多个单元格中精确重复"
        f"（value={row.get('value')}，重复次数={row.get('repeat_count')}，"
        f"最大小数位={row.get('max_decimal_places')}，cells={compact_text(cells, 140)}）。"
    )


def describe_pair_issue(kind: str, row: dict[str, str]) -> str:
    if kind == "matrix":
        return (
            "两个数值矩阵块完全相同或高度重叠；需要确认比较的 panel/处理条件是否应当独立"
            f"（匹配比例={row.get('match_fraction') or row.get('matched_fraction')}）。"
        )
    if kind == "short_sequence":
        return (
            "短数值向量或局部连续片段在不同位置被精确复用"
            f"（匹配长度={row.get('match_length')}，类型={row.get('match_type')}）。"
        )
    if kind == "scaled_sequence":
        return (
            "对齐的数值向量之间存在固定倍数关系"
            f"（B/A={row.get('ratio_b_over_a')}，简单比例={row.get('simple_ratio')}）。"
        )
    if kind == "window_relation":
        if row.get("relation_family") == "binary_basic_operation":
            return (
                "同表中至少 3 个相邻数值构成的目标窗口可由另外两个窗口通过基础运算得到"
                f"（关系={row.get('relation_type')}，长度={row.get('window_length')}，"
                f"最大绝对误差={row.get('max_abs_error')}，最大相对误差={row.get('max_rel_error')}）。"
            )
        return (
            "同表中至少 3 个相邻数值构成的目标窗口可由另一个窗口通过常数加减或固定比例得到"
            f"（关系={row.get('relation_type')}，{row.get('relation_detail')}，长度={row.get('window_length')}，"
            f"最大绝对误差={row.get('max_abs_error')}，最大相对误差={row.get('max_rel_error')}）。"
        )
    return "数值复用或计算关系筛查阳性，需要结合原始表上下文复核。"


def as_float(text: str) -> float:
    try:
        return float(text)
    except (TypeError, ValueError):
        return 0.0


def score_exact_repeat_group(item: tuple[str, list[dict[str, str]]]) -> tuple[int, float, float, str]:
    repeat_id, rows = item
    high = 1 if any("HIGH" in row.get("risk_hint", "") for row in rows) else 0
    max_decimal_places = max((as_float(row.get("max_decimal_places", "0")) for row in rows), default=0.0)
    repeat_count = max((as_float(row.get("repeat_count", "0")) for row in rows), default=0.0)
    return high, max_decimal_places, repeat_count, repeat_id


PDF_SCAN_MARKER_FILES = (
    "embedded_images.csv",
    "exact_embedded_image_duplicates.csv",
    "near_embedded_image_matches.csv",
    "near_page_render_matches.csv",
    "region_duplicate_candidates.csv",
    "summary.json",
)


def looks_like_pdf_scan_dir(path: Path) -> bool:
    return any((path / name).exists() for name in PDF_SCAN_MARKER_FILES)


def add_pdf_scan_dir(path: Path, dirs: list[Path], seen: set[Path]) -> None:
    if not path.exists() or not path.is_dir():
        return
    if looks_like_pdf_scan_dir(path):
        candidates = [path]
    else:
        candidates = [
            child
            for child in sorted(path.iterdir())
            if child.is_dir() and looks_like_pdf_scan_dir(child)
        ]
    if not candidates:
        candidates = [path]
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        dirs.append(candidate)
        seen.add(resolved)


def find_pdf_scan_dirs(audit_dir: Path, explicit: list[Path]) -> list[Path]:
    if explicit:
        dirs: list[Path] = []
        seen: set[Path] = set()
        for p in explicit:
            add_pdf_scan_dir(p, dirs, seen)
        return dirs
    dirs: list[Path] = []
    seen: set[Path] = set()
    for name in ("pdf_image_scan", "pdf_image_scan_skill"):
        p = audit_dir / name
        add_pdf_scan_dir(p, dirs, seen)
    for p in audit_dir.glob("*pdf*image*scan*"):
        add_pdf_scan_dir(p, dirs, seen)
    return dirs


def copy_visual(src: Path, dest_dir: Path) -> Path | None:
    if not src.exists():
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if src.resolve() != dest.resolve():
        shutil.copy2(src, dest)
    return dest


def collect_image_findings(pdf_dirs: list[Path], visual_dir: Path) -> tuple[list[VisualFinding], list[FindingTypeStat]]:
    findings: list[VisualFinding] = []
    stats: list[FindingTypeStat] = []
    issue_no = 1
    for pdf_dir in pdf_dirs:
        pdf_visual_dir = visual_dir / safe_name(pdf_dir.name)
        input_pdf = read_optional_text(pdf_dir / "input_pdf.txt") or str(pdf_dir)
        exact_rows = read_csv(pdf_dir / "exact_embedded_image_duplicates.csv")
        near_rows = read_csv(pdf_dir / "near_embedded_image_matches.csv")
        page_rows = read_csv(pdf_dir / "near_page_render_matches.csv")
        region_rows = read_csv(pdf_dir / "region_duplicate_candidates.csv")
        stats.extend(
            [
                stat_from_rows(
                    "Image exact embedded duplicates",
                    pdf_dir / "exact_embedded_image_duplicates.csv",
                    exact_rows,
                    len(exact_rows),
                    default_risk="HIGH-RISK_REVIEW",
                    note=f"PDF/image scan: {input_pdf}; candidates are summarized by contact sheet when non-empty.",
                ),
                stat_from_rows(
                    "Image near embedded matches",
                    pdf_dir / "near_embedded_image_matches.csv",
                    near_rows,
                    len(near_rows),
                    default_risk="WARN_REVIEW",
                    note=f"PDF/image scan: {input_pdf}; candidates are summarized by contact sheet when non-empty.",
                ),
                stat_from_rows(
                    "Rendered page matches",
                    pdf_dir / "near_page_render_matches.csv",
                    page_rows,
                    len(page_rows),
                    default_risk="WARN_REVIEW",
                    note=f"PDF/image scan: {input_pdf}; candidates are summarized by contact sheet when non-empty.",
                ),
                stat_from_rows(
                    "Rendered region candidates",
                    pdf_dir / "region_duplicate_candidates.csv",
                    region_rows,
                    len(region_rows),
                    default_risk="WARN_REVIEW",
                    note=f"PDF/image scan: {input_pdf}; candidates are summarized by contact sheet when non-empty.",
                ),
            ]
        )

        exact_sheet = None
        for name in ("exact_embedded_image_duplicates_contact_sheet.png", "exact_duplicate_review_contact_sheet.png"):
            copied = copy_visual(pdf_dir / name, pdf_visual_dir)
            if copied:
                exact_sheet = copied
                break
        if exact_rows:
            group_count = len({r.get("exact_group_id") or r.get("sha256") for r in exact_rows})
            findings.append(
                VisualFinding(
                    issue_id=f"IMG-{issue_no:03d}",
                    category="Image integrity",
                    risk="HIGH-RISK_REVIEW",
                    title="Byte-identical embedded image reuse candidates",
                    location=str(pdf_dir),
                    evidence=f"{len(exact_rows)} duplicate placements across {group_count} exact group(s).",
                    specific_issue=(
                        "PDF 内嵌图像对象的原始字节 SHA-256 完全一致；如果这些图像被放在不同生物学标签、"
                        "样本或 panel 下，需要作者解释。"
                    ),
                    raw_data_file=input_pdf,
                    visual_path=exact_sheet,
                    source_csv=pdf_dir / "exact_embedded_image_duplicates.csv",
                )
            )
            issue_no += 1

        near_sheet = None
        for name in ("near_embedded_image_matches_contact_sheet.png", "near_embedded_review_contact_sheet.png"):
            copied = copy_visual(pdf_dir / name, pdf_visual_dir)
            if copied:
                near_sheet = copied
                break
        if near_rows:
            findings.append(
                VisualFinding(
                    issue_id=f"IMG-{issue_no:03d}",
                    category="Image integrity",
                    risk="WARN_REVIEW",
                    title="Perceptual-hash near embedded-image matches",
                    location=str(pdf_dir),
                    evidence=f"{len(near_rows)} near-match candidate(s); visual review required.",
                    specific_issue=(
                        "内嵌图像对象在 dHash/aHash 阈值下视觉近似；这只是候选，需人工核对 panel 标签、裁剪范围和原始图。"
                    ),
                    raw_data_file=input_pdf,
                    visual_path=near_sheet,
                    source_csv=pdf_dir / "near_embedded_image_matches.csv",
                )
            )
            issue_no += 1

        if page_rows:
            findings.append(
                VisualFinding(
                    issue_id=f"IMG-{issue_no:03d}",
                    category="Image integrity",
                    risk="WARN_REVIEW",
                    title="Rendered-page duplicate or near-duplicate candidates",
                    location=str(pdf_dir),
                    evidence=f"{len(page_rows)} rendered-page candidate(s).",
                    specific_issue=(
                        "整页渲染图在页面级哈希下重复或高度相似；需要确认是模板/版式重复，还是图像内容重复。"
                    ),
                    raw_data_file=input_pdf,
                    source_csv=pdf_dir / "near_page_render_matches.csv",
                )
            )
            issue_no += 1

        region_sheet = copy_visual(pdf_dir / "region_duplicate_candidates_contact_sheet.png", pdf_visual_dir)
        if region_rows:
            findings.append(
                VisualFinding(
                    issue_id=f"IMG-{issue_no:03d}",
                    category="Image integrity",
                    risk="WARN_REVIEW",
                    title="Rendered local-region duplicate candidates",
                    location=str(pdf_dir),
                    evidence=f"{len(region_rows)} rendered tile candidate(s).",
                    specific_issue=(
                        "页面渲染后的局部 tile 出现重复区域候选；低信息块已跳过，仍需结合 contact sheet 和原图标签人工复核。"
                    ),
                    raw_data_file=input_pdf,
                    visual_path=region_sheet,
                    source_csv=pdf_dir / "region_duplicate_candidates.csv",
                )
            )
            issue_no += 1
    return findings, stats


def add_table_finding(
    findings: list[VisualFinding],
    issue_id: str,
    category: str,
    risk: str,
    title: str,
    row: dict[str, str],
    visual_path: Path | None,
    source_csv: Path,
    location: str,
    evidence: str,
    specific_issue: str = "",
) -> None:
    raw_data_file = row.get("workbook") or row.get("workbook_a") or row.get("raw_data_file") or ""
    findings.append(
        VisualFinding(
            issue_id=issue_id,
            category=category,
            risk=risk,
            title=title,
            location=location,
            evidence=evidence,
            specific_issue=specific_issue,
            raw_data_file=raw_data_file,
            visual_path=visual_path,
            source_csv=source_csv,
            details=row,
        )
    )


def sheet_evidence_filename(workbook: str, sheet: str) -> str:
    digest = hashlib.sha1(f"{workbook}\n{sheet}".encode("utf-8", errors="replace")).hexdigest()[:10]
    return f"full_table_{safe_name(workbook)[:80]}__{safe_name(sheet)[:80]}__{digest}.html"


def get_sheet_evidence(
    sheet_dir: Path,
    visual_dir: Path,
    sheet_evidence: dict[str, dict[str, Any]],
    workbook: str,
    sheet: str,
) -> dict[str, Any] | None:
    path, matrix = load_sheet_csv(sheet_dir, workbook, sheet)
    if not matrix:
        return None
    key = highlight_key(workbook, sheet)
    if key not in sheet_evidence:
        sheet_evidence[key] = {
            "workbook": workbook,
            "sheet": sheet,
            "matrix": matrix,
            "highlights": [],
            "path": visual_dir / "full_tables" / sheet_evidence_filename(workbook, sheet),
        }
    return sheet_evidence[key]


def add_sheet_highlights(
    sheet_evidence: dict[str, dict[str, Any]],
    sheet_dir: Path,
    visual_dir: Path,
    workbook: str,
    sheet: str,
    highlights: list[Highlight],
) -> Path | None:
    entry = get_sheet_evidence(sheet_dir, visual_dir, sheet_evidence, workbook, sheet)
    if entry is None:
        return None
    entry["highlights"].extend(highlight for highlight in highlights if highlight.cells)
    return entry["path"]


def write_sheet_evidence_pages(sheet_evidence: dict[str, dict[str, Any]]) -> None:
    for entry in sheet_evidence.values():
        title = f"Complete highlighted source table: {entry['sheet']}"
        render_full_sheet_html(
            entry["matrix"],
            entry["highlights"],
            entry["path"],
            title,
            entry["workbook"],
            entry["sheet"],
        )


def issue_evidence_filename(issue_id: str, kind: str, row: dict[str, str]) -> str:
    seed = "\n".join(
        [
            issue_id,
            kind,
            row.get("match_id", ""),
            row.get("workbook_a", row.get("workbook", "")),
            row.get("sheet_a", row.get("sheet", "")),
            row.get("cell_range_a", row.get("matched_cells_a", "")),
            row.get("workbook_b", ""),
            row.get("sheet_b", ""),
            row.get("cell_range_b", row.get("matched_cells_b", "")),
        ]
    )
    digest = hashlib.sha1(seed.encode("utf-8", errors="replace")).hexdigest()[:10]
    return f"{safe_name(issue_id)}_{safe_name(kind)}_{digest}.html"


def render_multi_sheet_issue_html(
    out_path: Path,
    issue_id: str,
    title: str,
    sections: list[dict[str, str]],
) -> bool:
    if not sections:
        return False
    section_html = []
    for section in sections:
        src = html.escape(section["href"])
        section_html.append(
            f"""
<section class="pane">
  <div class="pane-head">
    <h2>{html.escape(section["label"])}</h2>
    <p>{html.escape(section["meta"])}</p>
    <a href="{src}" target="_blank" rel="noopener">Open this complete table</a>
  </div>
  <iframe src="{src}" title="{html.escape(section["label"])}"></iframe>
</section>
"""
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_text(
        out_path,
        f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html.escape(issue_id)} Evidence Tables</title>
<style>
body {{ margin: 0; font-family: "Segoe UI", Arial, sans-serif; color: #111827; background: #F8FAFC; }}
header {{ padding: 18px 22px; background: #FFFFFF; border-bottom: 1px solid #CBD5E1; position: sticky; top: 0; z-index: 2; }}
h1 {{ margin: 0 0 6px; font-size: 22px; letter-spacing: 0; }}
header p {{ margin: 0; color: #475569; font-size: 13px; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(520px, 1fr)); gap: 14px; padding: 14px; }}
.pane {{ min-width: 0; background: #FFFFFF; border: 1px solid #CBD5E1; border-radius: 8px; overflow: hidden; }}
.pane-head {{ padding: 12px 14px; border-bottom: 1px solid #CBD5E1; background: #F8FAFC; }}
.pane h2 {{ margin: 0 0 5px; font-size: 16px; }}
.pane p {{ margin: 0 0 7px; color: #475569; font-size: 12px; overflow-wrap: anywhere; }}
.pane a {{ color: #0F5E9C; font-size: 12px; font-weight: 700; }}
iframe {{ display: block; width: 100%; height: 72vh; border: 0; background: #FFFFFF; }}
@media (max-width: 760px) {{
  .grid {{ display: block; padding: 10px; }}
  .pane {{ margin-bottom: 12px; }}
  iframe {{ height: 64vh; }}
}}
</style>
</head>
<body>
<header>
<h1>{html.escape(issue_id)}: {html.escape(title)}</h1>
<p>Each pane shows the complete source table for one side of the finding. Only cells for the current issue are highlighted inside each table.</p>
</header>
<main class="grid">
{''.join(section_html)}
</main>
</body>
</html>
""",
    )
    return True


def collect_pair_highlight_sections(
    row: dict[str, str],
    issue_id: str,
    kind: str,
    match_index: int,
) -> list[dict[str, Any]]:
    workbook_a = row.get("workbook_a") or row.get("workbook") or ""
    sheet_a = row.get("sheet_a") or row.get("sheet") or ""
    workbook_b = row.get("workbook_b") or workbook_a
    sheet_b = row.get("sheet_b") or sheet_a
    a_light, a_strong, _ = issue_role_palette(match_index, "a")
    b_light, b_strong, _ = issue_role_palette(match_index, "b")
    cells_a = parse_cell_refs(row.get("matched_cells_a", "")) or parse_range(row.get("matched_cell_range_a", "")) or parse_range(row.get("cell_range_a", ""))
    cells_b = parse_cell_refs(row.get("matched_cells_b", "")) or parse_range(row.get("matched_cell_range_b", "")) or parse_range(row.get("cell_range_b", ""))
    return [
        {
            "role": "A",
            "workbook": workbook_a,
            "sheet": sheet_a,
            "cells_label": row.get("matched_cells_a") or row.get("matched_cell_range_a") or row.get("cell_range_a", ""),
            "panel": row.get("panel_a", ""),
            "group": row.get("group_a") or row.get("panel_label_a") or "A",
            "highlights": [
                Highlight(cells_a, f"{issue_id} A", a_light, a_strong, f"{row.get('match_id', issue_id)}; {row.get('group_a') or row.get('panel_label_a') or 'A'}", issue_id)
            ],
        },
        {
            "role": "B",
            "workbook": workbook_b,
            "sheet": sheet_b,
            "cells_label": row.get("matched_cells_b") or row.get("matched_cell_range_b") or row.get("cell_range_b", ""),
            "panel": row.get("panel_b", ""),
            "group": row.get("group_b") or row.get("panel_label_b") or "B",
            "highlights": [
                Highlight(cells_b, f"{issue_id} B", b_light, b_strong, f"{row.get('match_id', issue_id)}; {row.get('group_b') or row.get('panel_label_b') or 'B'}", issue_id)
            ],
        },
    ]


def add_pair_evidence(
    sheet_evidence: dict[str, dict[str, Any]],
    sheet_dir: Path,
    visual_dir: Path,
    row: dict[str, str],
    issue_id: str,
    kind: str,
    match_index: int,
) -> Path | None:
    sections = collect_pair_highlight_sections(row, issue_id, kind, match_index)
    resolved_sections: list[dict[str, Any]] = []
    for section in sections:
        path = add_sheet_highlights(
            sheet_evidence,
            sheet_dir,
            visual_dir,
            section["workbook"],
            section["sheet"],
            section["highlights"],
        )
        if path is not None:
            resolved = dict(section)
            resolved["path"] = path
            resolved_sections.append(resolved)
    if not resolved_sections:
        return None
    unique_tables = {
        highlight_key(section["workbook"], section["sheet"])
        for section in resolved_sections
    }
    if len(unique_tables) == 1:
        return resolved_sections[0]["path"]

    out_path = visual_dir / "issue_tables" / issue_evidence_filename(issue_id, kind, row)
    issue_sections: list[dict[str, str]] = []
    for section in resolved_sections:
        rel = rel_link(section["path"], out_path.parent)
        href = f"{rel}?issue={issue_id}"
        label = f"{issue_id} {section['role']}: {section['sheet']} {section['cells_label']}"
        meta_bits = [
            f"Workbook: {section['workbook'] or 'not recorded'}",
            f"Panel: {section['panel'] or 'not recorded'}",
            f"Group: {section['group'] or 'not recorded'}",
        ]
        issue_sections.append({"href": href, "label": label, "meta": " | ".join(meta_bits)})
    render_multi_sheet_issue_html(out_path, issue_id, "Cross-table matched numeric evidence", issue_sections)
    return out_path


def collect_window_highlights(
    row: dict[str, str],
    issue_id: str,
    match_index: int,
) -> tuple[str, str, list[Highlight]]:
    workbook = row.get("workbook_a") or row.get("workbook") or ""
    sheet = row.get("sheet_a") or row.get("sheet") or ""
    source_a_light, source_a_strong, _ = issue_role_palette(match_index, "source_a")
    source_b_light, source_b_strong, _ = issue_role_palette(match_index, "source_b")
    target_light, target_strong, _ = issue_role_palette(match_index, "target")
    cells_a = parse_cell_refs(row.get("matched_cells_a", ""))
    cells_b = parse_cell_refs(row.get("matched_cells_b", ""))
    cells_c = parse_cell_refs(row.get("matched_cells_c", ""))
    is_binary = row.get("relation_family") == "binary_basic_operation"
    highlights = [
        Highlight(cells_a, f"{issue_id} A", source_a_light, source_a_strong, f"{row.get('match_id', issue_id)}; 源窗口 A: {row.get('group_a') or row.get('panel_a') or 'A'}", issue_id),
    ]
    if is_binary and cells_b:
        highlights.append(
            Highlight(cells_b, f"{issue_id} B", source_b_light, source_b_strong, f"{row.get('match_id', issue_id)}; 源窗口 B: {row.get('group_b') or row.get('panel_b') or 'B'}", issue_id)
        )
    highlights.append(
        Highlight(
            cells_c or cells_b,
            f"{issue_id} target",
            target_light,
            target_strong,
            f"{row.get('match_id', issue_id)}; 目标窗口: {row.get('group_c') or row.get('group_b') or row.get('panel_c') or row.get('panel_b') or 'target'}",
            issue_id,
        )
    )
    return workbook, sheet, highlights


def collect_group_highlights(
    row: dict[str, str],
    issue_id: str,
    match_index: int,
) -> tuple[str, str, list[Highlight]]:
    workbook = row.get("workbook", "")
    sheet = row.get("sheet", "")
    light, strong, _ = palette_for(match_index)
    cells = parse_cell_refs(row.get("value_cells", "")) or parse_range(row.get("group_cell_range", ""))
    if not cells:
        data_rows = row.get("data_rows", "")
        col_start = row.get("group_col_start") or row.get("col_start")
        col_end = row.get("group_col_end") or row.get("col_end")
        if re.fullmatch(r"\d+-\d+", data_rows) and col_start and col_end:
            r1, r2 = [int(x) for x in data_rows.split("-")]
            c1, c2 = int(col_start), int(col_end)
            cells = {(r, c) for r in range(r1, r2 + 1) for c in range(c1, c2 + 1)}
    highlights = [
        Highlight(cells, issue_id, light, strong, f"{row.get('issue_flags', '')}; {row.get('group', '')}", issue_id),
    ]
    return workbook, sheet, highlights


def collect_numeric_findings(source_dir: Path, visual_dir: Path, max_per_type: int) -> tuple[list[VisualFinding], list[FindingTypeStat]]:
    findings: list[VisualFinding] = []
    stats: list[FindingTypeStat] = []
    sheet_dir = source_dir / "sheet_csv"
    display_limit = effective_display_limit(max_per_type)
    sheet_evidence: dict[str, dict[str, Any]] = {}
    issue_no = 1

    specs = [
        (
            "duplicate_numeric_matrix_blocks.csv",
            "Numeric matrix reuse",
            "HIGH-RISK_REVIEW",
            "Duplicate or highly overlapping numeric matrix blocks",
            "matrix",
        ),
        (
            "short_duplicate_numeric_sequences.csv",
            "Numeric sequence reuse",
            "HIGH-RISK_REVIEW",
            "Identical short numeric vectors or local runs",
            "short_sequence",
        ),
        (
            "scaled_numeric_sequence_blocks.csv",
            "Fixed-ratio numeric vectors",
            "WARN_TO_HIGH-RISK_REVIEW",
            "Fixed scalar multiple between aligned numeric vectors",
            "scaled_sequence",
        ),
    ]
    for filename, category, default_risk, title, kind in specs:
        path = source_dir / filename
        rows = read_csv(path)
        shown_rows = limit_items(rows, max_per_type)
        stats.append(
            stat_from_rows(
                category,
                path,
                rows,
                len(shown_rows),
                display_limit=display_limit,
                default_risk=default_risk,
            )
        )
        for idx, row in enumerate(shown_rows):
            risk = row.get("risk_hint") or default_risk
            issue_id = f"NUM-{issue_no:03d}"
            visual = add_pair_evidence(sheet_evidence, sheet_dir, visual_dir, row, issue_id, kind, issue_no)
            sheet_a = row.get("sheet_a") or row.get("sheet") or ""
            sheet_b = row.get("sheet_b") or sheet_a
            location = sheet_a
            if row.get("cell_range_a") or row.get("matched_cells_a"):
                location += f" A={sheet_a}!{row.get('cell_range_a') or row.get('matched_cells_a')}"
            if row.get("cell_range_b") or row.get("matched_cells_b"):
                location += f" B={sheet_b}!{row.get('cell_range_b') or row.get('matched_cells_b')}"
            evidence_bits = []
            for key in ("match_id", "match_type", "comparison_basis", "match_fraction", "matched_cells", "match_length", "simple_ratio", "ratio_b_over_a"):
                if row.get(key):
                    evidence_bits.append(f"{key}={row[key]}")
            add_table_finding(
                findings,
                issue_id,
                category,
                risk,
                title,
                row,
                visual,
                path,
                location,
                "; ".join(evidence_bits) or "screen-positive numeric anomaly",
                describe_pair_issue(kind, row),
            )
            issue_no += 1

    window_path = source_dir / "window_arithmetic_relation_candidates.csv"
    window_rows = read_csv(window_path)
    shown_window_rows = limit_items(window_rows, max_per_type)
    stats.append(
        stat_from_rows(
            "Window arithmetic relation",
            window_path,
            window_rows,
            len(shown_window_rows),
            display_limit=display_limit,
            default_risk="WARN_REVIEW",
        )
    )
    for row in shown_window_rows:
        issue_id = f"NUM-{issue_no:03d}"
        workbook, sheet, highlights = collect_window_highlights(row, issue_id, issue_no)
        visual = add_sheet_highlights(sheet_evidence, sheet_dir, visual_dir, workbook, sheet, highlights)
        location = row.get("sheet_a") or row.get("sheet") or ""
        relation_target = row.get("matched_cells_c") or row.get("matched_cells_b") or ""
        if relation_target:
            location += f" target={relation_target}"
        evidence_bits = []
        for key in (
            "match_id",
            "relation_family",
            "relation_type",
            "relation_detail",
            "scan_strategy",
            "window_length",
            "max_abs_error",
            "max_rel_error",
            "independent_condition_hint",
            "shared_calculation_source_hint",
        ):
            if row.get(key):
                evidence_bits.append(f"{key}={row[key]}")
        add_table_finding(
            findings,
            issue_id,
            "Window arithmetic relation",
            row.get("risk_hint") or "WARN_REVIEW",
            "Adjacent numeric window can be generated by basic arithmetic",
            row,
            visual,
            window_path,
            location,
            "; ".join(evidence_bits) or "same-sheet window arithmetic relation candidate",
            describe_pair_issue("window_relation", row),
        )
        issue_no += 1

    exact_repeat_rows = read_csv(source_dir / "exact_long_decimal_repeats.csv")
    by_repeat: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in exact_repeat_rows:
        by_repeat[row.get("repeat_id", "")].append(row)
    exact_repeat_groups = sorted(by_repeat.items(), key=score_exact_repeat_group, reverse=True)
    exact_group_rows = [rows[0] for _, rows in exact_repeat_groups if rows]
    stats.append(
        stat_from_rows(
            "Exact long-decimal repeat",
            source_dir / "exact_long_decimal_repeats.csv",
            exact_group_rows,
            len(limit_items(exact_group_rows, max_per_type)),
            display_limit=display_limit,
            default_risk="WARN_REVIEW",
            note=f"{len(exact_repeat_rows)} CSV row(s) grouped into {len(exact_group_rows)} repeat group(s).",
        )
    )
    for repeat_id, rows in limit_items(exact_repeat_groups, max_per_type):
        if not rows:
            continue
        row = rows[0]
        issue_id = f"NUM-{issue_no:03d}"
        workbook = row.get("workbook", "")
        sheet = row.get("sheet", "")
        path, matrix = load_sheet_csv(sheet_dir, workbook, sheet)
        visual = None
        if matrix:
            light, strong, _ = palette_for(issue_no)
            cells = {rc for item in rows if (rc := ref_to_rc(item.get("cell", "")))}
            highlights = [Highlight(cells, issue_id, light, strong, f"{repeat_id or issue_id}; value={row.get('value', '')}", issue_id)]
            visual = add_sheet_highlights(sheet_evidence, sheet_dir, visual_dir, workbook, sheet, highlights)
        add_table_finding(
            findings,
            issue_id,
            "Exact long-decimal repeat",
            row.get("risk_hint") or "WARN_REVIEW",
            "Exact repeated value with displayed decimal precision >=3",
            row,
            visual,
            source_dir / "exact_long_decimal_repeats.csv",
            f"{sheet} / {row.get('all_repeat_cells', '')}",
            f"value={row.get('value')}; repeat_count={row.get('repeat_count')}; max_decimal_places={row.get('max_decimal_places')}",
            describe_exact_repeat(rows),
        )
        issue_no += 1

    formula_rows = [
        row
        for row in read_csv(source_dir / "formula_recheck.csv")
        if row.get("verdict") and row.get("verdict") != "MATCH"
    ]
    shown_formula_rows = limit_items(formula_rows, max_per_type)
    stats.append(
        stat_from_rows(
            "Formula validation",
            source_dir / "formula_recheck.csv",
            formula_rows,
            len(shown_formula_rows),
            display_limit=display_limit,
            default_risk="WARN_REVIEW",
            note="MATCH rows are excluded from this candidate count.",
        )
    )
    for row in shown_formula_rows:
        issue_id = f"NUM-{issue_no:03d}"
        add_table_finding(
            findings,
            issue_id,
            "Formula validation",
            "WARN_REVIEW" if row.get("verdict", "").startswith("UNSUPPORTED") else "HIGH-RISK_REVIEW",
            "Formula cached value did not validate cleanly",
            row,
            None,
            source_dir / "formula_recheck.csv",
            f"{row.get('sheet', '')}!{row.get('cell', '')}",
            f"verdict={row.get('verdict')}; cached={row.get('cached_value')}; recalculated={row.get('recalculated_value')}; abs_diff={row.get('abs_diff')}",
            "公式缓存值与脚本重算结果不一致，或该公式类型暂时无法由检查器重算；需结合公式语义和 Excel 原表复核。",
        )
        issue_no += 1

    group_rows = [
        row
        for row in read_csv(source_dir / "group_block_summary.csv")
        if row.get("issue_flags")
        and any(
            flag in row.get("issue_flags", "")
            for flag in (
                "near_exact_arithmetic_progression",
                "constant_adjacent_pair_sum_pattern",
                "low_last_digit_entropy",
                "last_digit_concentration",
                "terminal_0_5_enrichment",
                "monotonic_nonconstant_sequence",
            )
        )
    ]
    group_rows.sort(key=lambda r: score_group_issue(r), reverse=True)
    group_stat_rows: list[dict[str, str]] = []
    for row in group_rows:
        row_for_stat = dict(row)
        flags = row_for_stat.get("issue_flags", "")
        row_for_stat["risk_hint"] = (
            "HIGH-RISK_REVIEW"
            if "near_exact_arithmetic_progression" in flags or "constant_adjacent_pair_sum_pattern" in flags
            else "WARN_REVIEW"
        )
        group_stat_rows.append(row_for_stat)
    shown_group_rows = limit_items(group_rows, max_per_type)
    stats.append(
        stat_from_rows(
            "Group-level numeric screen",
            source_dir / "group_block_summary.csv",
            group_stat_rows,
            len(shown_group_rows),
            display_limit=display_limit,
            default_risk="WARN_REVIEW",
        )
    )
    for row in shown_group_rows:
        issue_id = f"NUM-{issue_no:03d}"
        workbook, sheet, highlights = collect_group_highlights(row, issue_id, issue_no)
        visual = add_sheet_highlights(sheet_evidence, sheet_dir, visual_dir, workbook, sheet, highlights)
        flags = row.get("issue_flags", "")
        risk = "WARN_REVIEW"
        if "near_exact_arithmetic_progression" in flags or "constant_adjacent_pair_sum_pattern" in flags:
            risk = "HIGH-RISK_REVIEW"
        location = f"{row.get('sheet', '')} / {row.get('group', '')}"
        evidence = f"flags={flags}; n={row.get('n')}; mean={row.get('mean')}; sd={row.get('sd')}"
        specific_issue = describe_group_issue(row)
        add_table_finding(
            findings,
            issue_id,
            "Group-level numeric screen",
            risk,
            "Group-level digit/order/pair-sum anomaly",
            row,
            visual,
            source_dir / "group_block_summary.csv",
            location,
            evidence,
            specific_issue,
        )
        issue_no += 1

    write_sheet_evidence_pages(sheet_evidence)
    return findings, stats


def score_group_issue(row: dict[str, str]) -> int:
    flags = row.get("issue_flags", "")
    score = 0
    if "near_exact_arithmetic_progression" in flags:
        score += 100
    if "constant_adjacent_pair_sum_pattern" in flags:
        score += 90
    if "low_last_digit_entropy" in flags:
        score += 20
    if "last_digit_concentration" in flags:
        score += 20
    if "terminal_0_5_enrichment" in flags:
        score += 15
    if "monotonic_nonconstant_sequence" in flags:
        score += 10
    try:
        score += min(20, int(float(row.get("n", "0"))))
    except ValueError:
        pass
    return score


def rel_link(target: Path | None, base: Path) -> str:
    if target is None:
        return ""
    try:
        return target.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return target.as_posix()


def risk_counts(findings: list[VisualFinding]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for finding in findings:
        counts[finding.risk] += 1
    return dict(sorted(counts.items()))


def category_rank(category: str) -> tuple[int, str]:
    try:
        return CATEGORY_ORDER.index(category), category
    except ValueError:
        return len(CATEGORY_ORDER), category


def risk_rank(risk: str) -> int:
    bucket = risk_bucket(risk)
    if bucket == "HIGH":
        return 0
    if bucket == "WARN":
        return 1
    return 2


def finding_sort_key(finding: VisualFinding) -> tuple[int, str, int, str]:
    number_match = re.search(r"(\d+)$", finding.issue_id)
    number = int(number_match.group(1)) if number_match else 999999
    return (*category_rank(finding.category), risk_rank(finding.risk), f"{number:06d}:{finding.issue_id}")


def sorted_findings(findings: list[VisualFinding]) -> list[VisualFinding]:
    return sorted(findings, key=finding_sort_key)


def grouped_findings(findings: list[VisualFinding]) -> dict[str, list[VisualFinding]]:
    groups: dict[str, list[VisualFinding]] = defaultdict(list)
    for finding in sorted_findings(findings):
        groups[finding.category].append(finding)
    return dict(sorted(groups.items(), key=lambda item: category_rank(item[0])))


def slug(text: str) -> str:
    slugged = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return slugged or "section"


def truncated_stats(stats: list[FindingTypeStat]) -> list[FindingTypeStat]:
    return [stat for stat in visible_stats(stats) if stat.hidden_candidates > 0]


def visible_stats(stats: list[FindingTypeStat]) -> list[FindingTypeStat]:
    rows = [stat for stat in stats if stat.total_candidates or stat.shown_in_report]
    return sorted(rows, key=lambda stat: (*category_rank(stat.category), stat.source_csv.as_posix()))


def report_limit_notice(stats: list[FindingTypeStat], max_per_type: int) -> str:
    if max_per_type <= 0:
        return "Display limit: unlimited. All screen-positive candidates from the available audit CSV outputs are expanded in this report."
    truncated = [stat for stat in stats if stat.hidden_candidates > 0]
    if not truncated:
        return (
            f"Display limit: this report shows at most {max_per_type} findings per candidate type; "
            "no candidate type is truncated by this limit."
        )
    hidden_total = sum(stat.hidden_candidates for stat in truncated)
    names = ", ".join(f"{stat.category}: hidden {stat.hidden_candidates}" for stat in truncated[:8])
    if len(truncated) > 8:
        names += f", plus {len(truncated) - 8} more type(s)"
    return (
        f"Important: this report shows at most {max_per_type} findings per candidate type. "
        f"A total of {hidden_total} candidate row(s)/group(s) are not expanded in the detail table. "
        f"Truncated types: {names}. Open the corresponding Source CSV for the complete list, "
        "or rebuild the report with --max-per-type 0 for unlimited expansion."
    )

def html_stats_table(stats: list[FindingTypeStat], out_dir: Path) -> str:
    rows = []
    for stat in visible_stats(stats):
        source_rel = html.escape(rel_link(stat.source_csv, out_dir))
        source_link = f'<a href="{source_rel}">{html.escape(stat.source_csv.name)}</a>'
        truncated = "YES" if stat.hidden_candidates > 0 else "NO"
        rows.append(
            "<tr>"
            f"<td>{html.escape(stat.category)}</td>"
            f"<td>{stat.total_candidates}</td>"
            f"<td>{stat.high_risk_candidates}</td>"
            f"<td>{stat.warn_candidates}</td>"
            f"<td>{stat.shown_in_report}</td>"
            f"<td>{stat.hidden_candidates}</td>"
            f"<td><span class=\"{'truncated' if stat.hidden_candidates > 0 else ''}\">{truncated}</span></td>"
            f"<td>{html.escape(display_limit_label(stat.display_limit))}</td>"
            f"<td>{source_link}<br><small>{html.escape(stat.note)}</small></td>"
            "</tr>"
        )
    if not rows:
        return "<p>No screen-positive candidate statistics were available.</p>"
    return (
        "<table>"
        "<tr><th>Check type</th><th>Total candidates</th><th>High-risk candidates</th>"
        "<th>Warn candidates</th><th>Shown in report</th><th>Hidden by display limit</th>"
        "<th>Truncated?</th><th>Display limit</th><th>Source CSV / note</th></tr>"
        + "".join(rows)
        + "</table>"
    )


def markdown_stats_table(stats: list[FindingTypeStat], out_dir: Path) -> list[str]:
    lines = [
        "| Check type | Total | High-risk | Warn | Shown | Hidden | Truncated? | Source CSV |",
        "|---|---:|---:|---:|---:|---:|---|---|",
    ]
    rows = visible_stats(stats)
    if not rows:
        return ["No screen-positive candidate statistics were available."]
    for stat in rows:
        truncated = "YES" if stat.hidden_candidates > 0 else "NO"
        source = rel_link(stat.source_csv, out_dir)
        note = f"<br>{stat.note}" if stat.note else ""
        lines.append(
            f"| {stat.category} | {stat.total_candidates} | {stat.high_risk_candidates} | "
            f"{stat.warn_candidates} | {stat.shown_in_report} | {stat.hidden_candidates} | "
            f"{truncated} | `{source}`{note} |"
        )
    return lines


def html_toc(items: list[tuple[str, str]], title: str = "Contents") -> str:
    links = "\n".join(
        f'<a href="#{html.escape(anchor)}">{html.escape(label)}</a>' for label, anchor in items
    )
    return f'<nav class="toc"><div class="toc-title">{html.escape(title)}</div>{links}</nav>'


def markdown_toc(items: list[tuple[str, str]]) -> list[str]:
    return [f"- [{label}](#{anchor})" for label, anchor in items]


def html_full_csv_link(full_csv_path: Path | None, out_dir: Path) -> str:
    if not full_csv_path:
        return ""
    rel = html.escape(rel_link(full_csv_path, out_dir))
    return (
        '<p class="full-csv-link">'
        f'<a href="{rel}">Open full CSV visualization for truncated candidate types</a>'
        "</p>"
    )


def markdown_full_csv_link(full_csv_path: Path | None, out_dir: Path) -> list[str]:
    if not full_csv_path:
        return []
    return [
        f"完整 CSV 可视化文档: [{full_csv_path.name}]({rel_link(full_csv_path, out_dir)})",
        "",
    ]


def visual_kind(path: Path | None) -> str:
    if is_image_path(path):
        return "image"
    if is_html_path(path):
        return "table"
    return ""


def visual_href(finding: VisualFinding, out_dir: Path) -> str:
    if not finding.visual_path:
        return ""
    rel = rel_link(finding.visual_path, out_dir)
    if visual_kind(finding.visual_path) == "table":
        sep = "&" if "?" in rel else "?"
        return f"{rel}{sep}issue={finding.issue_id}"
    return rel


def html_finding_rows(findings: list[VisualFinding], out_dir: Path) -> str:
    rows = []
    for finding in findings:
        visual = ""
        if finding.visual_path:
            visual_rel = html.escape(visual_href(finding, out_dir))
            kind = visual_kind(finding.visual_path)
            if kind == "image":
                visual = (
                    f'<a class="evidence-thumb evidence-image" href="{visual_rel}" data-lightbox-src="{visual_rel}" '
                    f'data-lightbox-title="{html.escape(finding.issue_id)}">'
                    f'<img src="{visual_rel}" alt="{html.escape(finding.issue_id)} visual evidence"></a>'
                )
            elif kind == "table":
                visual = f'<a class="evidence-table-link" href="{visual_rel}">Open complete highlighted table</a>'
            else:
                visual = f'<a href="{visual_rel}">Open evidence</a>'
        source_rel = html.escape(rel_link(finding.source_csv, out_dir)) if finding.source_csv else ""
        source_link = f'<a href="{source_rel}">{html.escape(finding.source_csv.name)}</a>' if finding.source_csv else ""
        rows.append(
            f'<tr id="finding-row-{html.escape(slug(finding.issue_id))}">'
            f"<td>{html.escape(finding.issue_id)}</td>"
            f"<td><span class=\"risk\">{html.escape(finding.risk)}</span></td>"
            f"<td>{html.escape(finding.title)}<br><small>{html.escape(display_text(finding.location))}</small></td>"
            f"<td>{html.escape(finding.raw_data_file)}</td>"
            f"<td>{html.escape(display_text(finding.specific_issue))}</td>"
            f"<td>{html.escape(display_text(finding.evidence))}</td>"
            f"<td>{source_link}</td>"
            f"<td>{visual}</td>"
            "</tr>"
        )
    return "".join(rows)


def html_grouped_findings(findings: list[VisualFinding], out_dir: Path) -> str:
    if not findings:
        return "<p>No screen-positive findings were available in the provided audit outputs.</p>"
    sections = []
    for category, group in grouped_findings(findings).items():
        anchor = f"findings-{slug(category)}"
        sections.append(
            f'<section id="{html.escape(anchor)}">'
            f"<h3>{html.escape(category)} <span class=\"count\">{len(group)}</span></h3>"
            "<table>"
            "<tr><th>Issue ID</th><th>Risk</th><th>Location</th><th>Raw data file</th>"
            "<th>具体问题</th><th>Evidence</th><th>Source CSV</th><th>Visual Evidence</th></tr>"
            f"{html_finding_rows(group, out_dir)}"
            "</table>"
            "</section>"
        )
    return "".join(sections)


def gallery_findings(findings: list[VisualFinding]) -> list[VisualFinding]:
    return sorted_findings(findings)


def gallery_payload(findings: list[VisualFinding], out_dir: Path) -> str:
    items = []
    for finding in gallery_findings(findings):
        items.append(
            {
                "issue_id": finding.issue_id,
                "category": finding.category,
                "risk": finding.risk,
                "title": finding.title,
                "location": display_text(finding.location),
                "raw_data_file": finding.raw_data_file,
                "specific_issue": display_text(finding.specific_issue),
                "evidence": display_text(finding.evidence),
                "source_csv": rel_link(finding.source_csv, out_dir) if finding.source_csv else "",
                "source_csv_name": finding.source_csv.name if finding.source_csv else "",
                "visual": visual_href(finding, out_dir) if finding.visual_path else "",
                "visual_kind": visual_kind(finding.visual_path),
                "anchor": f"finding-row-{slug(finding.issue_id)}",
            }
        )
    return html.escape(json.dumps(items, ensure_ascii=False))


def html_evidence_gallery(findings: list[VisualFinding], out_dir: Path) -> str:
    items = gallery_findings(findings)
    payload = gallery_payload(findings, out_dir)
    if not items:
        return (
            '<section id="evidence-gallery" class="hero-panel">'
            '<div class="hero-copy"><p class="eyebrow">Visual Review</p>'
            "<h2>No screen-positive findings were generated</h2>"
            "<p>Candidate statistics and inputs continue below.</p></div>"
            "</section>"
        )
    first = items[0]
    first_visual = html.escape(visual_href(first, out_dir)) if first.visual_path else ""
    first_source = html.escape(rel_link(first.source_csv, out_dir)) if first.source_csv else ""
    first_source_name = html.escape(first.source_csv.name if first.source_csv else "")
    first_kind = visual_kind(first.visual_path)
    image_display = "flex" if first_kind == "image" else "none"
    table_display = "block" if first_kind == "table" else "none"
    placeholder_display = "flex" if not first_kind else "none"
    return f"""
<section id="evidence-gallery" class="hero-panel" data-gallery="{payload}">
  <div class="hero-copy">
    <p class="eyebrow">Visual Review</p>
    <h2>Evidence Image Browser</h2>
    <p>Use the arrow buttons or keyboard left/right keys to move through the generated visual evidence. Each image is centered for inspection, with the corresponding analysis directly below.</p>
  </div>
  <div class="viewer-shell">
    <div class="viewer-toolbar" aria-label="Evidence image controls">
      <button type="button" class="nav-button" id="gallery-prev" aria-label="Previous finding">&lsaquo;</button>
      <div class="viewer-status">
        <span id="gallery-counter">1 / {len(items)}</span>
        <span id="gallery-category">{html.escape(first.category)}</span>
      </div>
      <button type="button" class="nav-button" id="gallery-next" aria-label="Next finding">&rsaquo;</button>
    </div>
    <a id="gallery-image-link" href="{first_visual}" style="display:{image_display}">
      <img id="gallery-image" src="{first_visual}" alt="{html.escape(first.issue_id)} visual evidence">
    </a>
    <iframe id="gallery-table-frame" src="{first_visual if first_kind == 'table' else ''}" style="display:{table_display}" title="Complete highlighted source table"></iframe>
    <div id="gallery-placeholder" class="viewer-placeholder" style="display:{placeholder_display}">No visual image was generated for this finding. Review the analysis and source CSV below.</div>
  </div>
  <div class="analysis-panel">
    <div class="analysis-header">
      <span class="issue-pill" id="gallery-issue">{html.escape(first.issue_id)}</span>
      <span class="risk-pill" id="gallery-risk">{html.escape(first.risk)}</span>
    </div>
    <h3 id="gallery-title">{html.escape(first.title)}</h3>
    <p id="gallery-specific">{html.escape(first.specific_issue)}</p>
    <dl>
      <div><dt>Location</dt><dd id="gallery-location">{html.escape(first.location)}</dd></div>
      <div><dt>Raw data file</dt><dd id="gallery-raw">{html.escape(first.raw_data_file or "not recorded")}</dd></div>
      <div><dt>Evidence</dt><dd id="gallery-evidence">{html.escape(first.evidence)}</dd></div>
      <div><dt>Source CSV</dt><dd><a id="gallery-source" href="{first_source}">{first_source_name}</a></dd></div>
    </dl>
    <p class="detail-link"><a id="gallery-detail-link" href="#finding-row-{html.escape(slug(first.issue_id))}">Jump to detailed table row</a></p>
  </div>
</section>
<div id="lightbox" class="lightbox" aria-hidden="true">
  <button type="button" id="lightbox-close" class="lightbox-close" aria-label="Close enlarged image">Close</button>
  <div id="lightbox-viewport" class="lightbox-viewport">
    <img id="lightbox-image" alt="Enlarged visual evidence">
  </div>
</div>
"""


def markdown_grouped_findings(findings: list[VisualFinding], out_dir: Path) -> list[str]:
    lines: list[str] = []
    if not findings:
        return ["No screen-positive findings were available in the provided audit outputs."]
    for category, group in grouped_findings(findings).items():
        lines.extend([f"### {category}", ""])
        for finding in group:
            lines.extend(
                [
                    f"#### {finding.issue_id}: {finding.title}",
                    "",
                    f"- Risk: `{finding.risk}`",
                    f"- Location: {display_text(finding.location)}",
                    f"- Raw data file: {finding.raw_data_file or 'not recorded'}",
                    f"- 具体问题: {display_text(finding.specific_issue) if finding.specific_issue else '见 Evidence 字段。'}",
                    f"- Evidence: {display_text(finding.evidence)}",
                ]
            )
            if finding.source_csv:
                lines.append(f"- Source CSV: `{rel_link(finding.source_csv, out_dir)}`")
            if finding.visual_path:
                rel = visual_href(finding, out_dir)
                lines.append("")
                if visual_kind(finding.visual_path) == "image":
                    lines.append(f"![{finding.issue_id} visual evidence]({rel})")
                else:
                    lines.append(f"[{finding.issue_id} complete highlighted table]({rel})")
            lines.append("")
    return lines


def csv_row_sort_key(row: dict[str, str]) -> tuple[int, str, str, str]:
    risk = row.get("risk_hint") or row.get("risk") or row.get("verdict") or ""
    primary_id = (
        row.get("match_id")
        or row.get("repeat_id")
        or row.get("exact_group_id")
        or row.get("candidate_id")
        or row.get("region_group_id")
        or ""
    )
    sheet = row.get("sheet") or row.get("sheet_a") or row.get("page") or ""
    cells = (
        row.get("cell_range_a")
        or row.get("matched_cells_a")
        or row.get("all_repeat_cells")
        or row.get("cell")
        or ""
    )
    return risk_rank(risk), sheet, primary_id, cells


def html_csv_table(rows: list[dict[str, str]], out_dir: Path, source_csv: Path) -> str:
    if not rows:
        return "<p>No rows.</p>"
    headers = list(rows[0].keys())
    sorted_rows = sorted(rows, key=csv_row_sort_key)
    header_html = "".join(f"<th>{html.escape(header)}</th>" for header in headers)
    body_rows = []
    for row in sorted_rows:
        cells = []
        for header in headers:
            value = display_text(row.get(header, ""))
            if header.lower().endswith("path") or header in {"source_csv", "visual_path"}:
                cells.append(f"<td>{html.escape(value)}</td>")
            else:
                cells.append(f"<td>{html.escape(value)}</td>")
        body_rows.append("<tr>" + "".join(cells) + "</tr>")
    source_rel = html.escape(rel_link(source_csv, out_dir))
    return (
        f'<p class="caption">Source CSV: <a href="{source_rel}">{html.escape(source_csv.name)}</a>. '
        f"Rows shown: {len(sorted_rows)}.</p>"
        '<div class="csv-scroll"><table class="csv-table">'
        f"<tr>{header_html}</tr>{''.join(body_rows)}</table></div>"
    )


def markdown_csv_table(rows: list[dict[str, str]], source_csv: Path, out_dir: Path) -> list[str]:
    if not rows:
        return ["No rows."]
    headers = list(rows[0].keys())
    sorted_rows = sorted(rows, key=csv_row_sort_key)
    lines = [
        f"Source CSV: `{rel_link(source_csv, out_dir)}`",
        f"Rows shown: {len(sorted_rows)}",
        "",
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in sorted_rows:
        values = [display_text(str(row.get(header, ""))).replace("|", "\\|").replace("\n", " ") for header in headers]
        lines.append("| " + " | ".join(values) + " |")
    return lines


def unique_truncated_sources(stats: list[FindingTypeStat]) -> list[FindingTypeStat]:
    seen: set[Path] = set()
    unique: list[FindingTypeStat] = []
    for stat in truncated_stats(stats):
        resolved = stat.source_csv.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(stat)
    return sorted(unique, key=lambda stat: (*category_rank(stat.category), stat.source_csv.as_posix()))


def full_csv_visualization_html(stats: list[FindingTypeStat], out_dir: Path, max_per_type: int) -> str:
    truncated = unique_truncated_sources(stats)
    toc_items = [("Overview", "overview")]
    toc_items.extend((stat.category, f"csv-{slug(stat.category)}-{idx}") for idx, stat in enumerate(truncated, 1))
    sections = []
    for idx, stat in enumerate(truncated, 1):
        rows = read_csv(stat.source_csv)
        anchor = f"csv-{slug(stat.category)}-{idx}"
        sections.append(
            f'<section id="{html.escape(anchor)}">'
            f"<h2>{html.escape(stat.category)}</h2>"
            f"<p class=\"caption\">Main report limit: {max_per_type}; total candidates: {stat.total_candidates}; "
            f"hidden in main detail table: {stat.hidden_candidates}.</p>"
            f"{html_csv_table(rows, out_dir, stat.source_csv)}"
            "</section>"
        )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Full CSV Visualization</title>
<style>
body {{ font-family: Arial, sans-serif; margin: 0; color: #111827; background: #FFFFFF; }}
.layout {{ display: grid; grid-template-columns: 260px minmax(0, 1fr); gap: 24px; align-items: start; }}
.toc {{ position: sticky; top: 0; max-height: 100vh; overflow: auto; padding: 20px 16px; background: #F8FAFC; border-right: 1px solid #CBD5E1; }}
.toc-title {{ font-weight: 700; margin-bottom: 12px; color: #0F172A; }}
.toc a {{ display: block; color: #0F172A; text-decoration: none; padding: 7px 0; font-size: 14px; }}
.toc a:hover {{ text-decoration: underline; }}
main {{ min-width: 0; padding: 28px 32px 48px 0; }}
h1, h2 {{ color: #0F172A; }}
.note {{ background: #F8FAFC; border-left: 4px solid #64748B; padding: 12px 14px; margin: 16px 0; }}
.warning {{ background: #FEF2F2; border-left: 4px solid #DC2626; padding: 12px 14px; margin: 16px 0; font-weight: 700; }}
.caption {{ color: #475569; font-size: 13px; }}
.csv-scroll {{ max-width: 100%; overflow: auto; border: 1px solid #CBD5E1; margin: 12px 0 32px; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #CBD5E1; padding: 7px; vertical-align: top; font-size: 12px; white-space: pre-wrap; }}
th {{ background: #E2E8F0; text-align: left; position: sticky; top: 0; z-index: 1; }}
.csv-table {{ min-width: 1200px; }}
@media (max-width: 900px) {{
  .layout {{ display: block; }}
  .toc {{ position: sticky; top: 0; max-height: 45vh; border-right: 0; border-bottom: 1px solid #CBD5E1; }}
  main {{ padding: 20px; }}
}}
</style>
</head>
<body>
<div class="layout">
{html_toc(toc_items)}
<main>
<section id="overview">
<h1>Full CSV Visualization For Truncated Candidate Types</h1>
<div class="note">This companion document visualizes the complete backing CSV rows for candidate types truncated in the main visual report. It is a review aid and does not conclude fabrication or misconduct.</div>
<div class="warning">{html.escape(report_limit_notice(stats, max_per_type))}</div>
{html_stats_table(truncated, out_dir)}
</section>
{''.join(sections)}
</main>
</div>
</body>
</html>
"""


def full_csv_visualization_markdown(stats: list[FindingTypeStat], out_dir: Path, max_per_type: int) -> str:
    truncated = unique_truncated_sources(stats)
    lines = [
        "# Full CSV Visualization For Truncated Candidate Types",
        "",
        "This companion document visualizes the complete backing CSV rows for candidate types truncated in the main visual report. It is a review aid and does not conclude fabrication or misconduct.",
        "",
        f"**Display limit notice:** {report_limit_notice(stats, max_per_type)}",
        "",
        "## Contents",
        "",
    ]
    lines.extend(markdown_toc([("Overview", "overview")] + [(stat.category, f"csv-{slug(stat.category)}-{idx}") for idx, stat in enumerate(truncated, 1)]))
    lines.extend(["", "## Candidate Statistics", ""])
    lines.extend(markdown_stats_table(truncated, out_dir))
    for stat in truncated:
        rows = read_csv(stat.source_csv)
        lines.extend(["", f"## {stat.category}", ""])
        lines.extend(markdown_csv_table(rows, stat.source_csv, out_dir))
    return "\n".join(lines)


def write_full_csv_visualizations(stats: list[FindingTypeStat], out_dir: Path, max_per_type: int) -> Path | None:
    html_path = out_dir / "full_csv_visualization.html"
    markdown_path = out_dir / "full_csv_visualization.md"
    if not truncated_stats(stats):
        for stale_path in (html_path, markdown_path):
            if stale_path.exists():
                stale_path.unlink()
        return None
    write_text(html_path, full_csv_visualization_html(stats, out_dir, max_per_type))
    write_text(markdown_path, full_csv_visualization_markdown(stats, out_dir, max_per_type))
    return html_path


def report_css() -> str:
    return """
:root {
  --ink: #182033;
  --muted: #64748B;
  --line: #D6DEE8;
  --paper: #FFFFFF;
  --wash: #F5F7FB;
  --nav: #111827;
  --nav-muted: #CBD5E1;
  --accent: #0F766E;
  --danger: #B42318;
  --warn: #B54708;
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0;
  color: var(--ink);
  background: var(--wash);
  font-family: Inter, "Segoe UI", Arial, sans-serif;
}
a { color: #0F5E9C; }
.layout {
  display: grid;
  grid-template-columns: 282px minmax(0, 1fr);
  align-items: start;
  min-height: 100vh;
}
.toc {
  position: sticky;
  top: 0;
  height: 100vh;
  overflow: auto;
  padding: 22px 18px;
  background: var(--nav);
  border-right: 1px solid #0B1220;
}
.toc-title {
  color: #FFFFFF;
  font-weight: 800;
  margin-bottom: 16px;
  font-size: 15px;
}
.toc a {
  display: block;
  color: var(--nav-muted);
  text-decoration: none;
  padding: 8px 9px;
  margin: 2px 0;
  border-radius: 7px;
  font-size: 13px;
  line-height: 1.25;
}
.toc a:hover { color: #FFFFFF; background: #1F2937; }
main {
  min-width: 0;
  padding: 24px 30px 54px;
}
section { scroll-margin-top: 18px; }
h1, h2, h3 { color: #111827; letter-spacing: 0; }
h1 { font-size: 32px; margin: 0 0 12px; }
h2 { font-size: 23px; margin: 0 0 14px; }
h3 { font-size: 18px; margin: 0 0 12px; }
.report-hero {
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: 8px;
  overflow: hidden;
  margin-bottom: 18px;
}
.hero-top {
  padding: 26px 28px;
  background: #F8FAFC;
  border-bottom: 1px solid var(--line);
}
.hero-top p {
  max-width: 980px;
  margin: 0;
  color: #475569;
  line-height: 1.65;
}
.eyebrow {
  margin: 0 0 8px;
  color: var(--accent);
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0;
  text-transform: uppercase;
}
.notice-row {
  display: grid;
  gap: 12px;
  padding: 0 28px 22px;
}
.note {
  background: #F8FAFC;
  border: 1px solid var(--line);
  border-left: 4px solid #64748B;
  padding: 12px 14px;
  margin: 16px 0;
  line-height: 1.55;
}
.warning {
  background: #FFF7ED;
  border: 1px solid #FED7AA;
  border-left: 4px solid var(--warn);
  padding: 12px 14px;
  margin: 16px 0;
  font-weight: 700;
  line-height: 1.55;
}
.hero-panel {
  padding: 24px 28px 28px;
  border-top: 1px solid var(--line);
}
.hero-copy {
  display: grid;
  gap: 6px;
  margin-bottom: 16px;
}
.hero-copy h2 { margin: 0; }
.hero-copy p:not(.eyebrow) {
  margin: 0;
  color: #475569;
  line-height: 1.55;
}
.viewer-shell {
  background: #0B1220;
  border: 1px solid #1E293B;
  border-radius: 8px;
  padding: 14px;
}
.viewer-toolbar {
  display: grid;
  grid-template-columns: 44px minmax(0, 1fr) 44px;
  gap: 10px;
  align-items: center;
  margin-bottom: 12px;
}
.nav-button {
  width: 44px;
  height: 40px;
  border: 1px solid #334155;
  border-radius: 7px;
  background: #182235;
  color: #FFFFFF;
  font-size: 28px;
  line-height: 1;
  cursor: pointer;
}
.nav-button:hover { background: #263449; }
.viewer-status {
  min-width: 0;
  display: flex;
  justify-content: center;
  gap: 10px;
  color: #E5E7EB;
  font-size: 13px;
  font-weight: 700;
}
#gallery-category {
  color: #99F6E4;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
#gallery-image-link {
  display: flex;
  min-height: 420px;
  max-height: 72vh;
  align-items: center;
  justify-content: center;
  background: #111827;
  border: 1px solid #334155;
  border-radius: 8px;
  overflow: auto;
}
#gallery-image {
  display: block;
  max-width: 100%;
  max-height: 70vh;
  object-fit: contain;
  border: 0;
  background: #FFFFFF;
}
#gallery-table-frame {
  display: none;
  width: 100%;
  min-height: 620px;
  height: 72vh;
  border: 1px solid #334155;
  border-radius: 8px;
  background: #FFFFFF;
}
.viewer-placeholder {
  display: none;
  min-height: 260px;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 24px;
  color: #CBD5E1;
  background: #111827;
  border: 1px dashed #475569;
  border-radius: 8px;
}
.analysis-panel {
  margin-top: 14px;
  background: #FFFFFF;
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 18px;
}
.analysis-header {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}
.issue-pill,
.risk-pill {
  display: inline-flex;
  align-items: center;
  min-height: 28px;
  padding: 5px 9px;
  border-radius: 7px;
  font-size: 12px;
  font-weight: 800;
}
.issue-pill { color: #134E4A; background: #CCFBF1; border: 1px solid #99F6E4; }
.risk-pill { color: #7F1D1D; background: #FEE2E2; border: 1px solid #FECACA; }
.analysis-panel p {
  line-height: 1.62;
  margin: 8px 0 14px;
}
.analysis-panel dl {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin: 0;
}
.analysis-panel dl div {
  background: #F8FAFC;
  border: 1px solid var(--line);
  border-radius: 7px;
  padding: 10px;
}
.analysis-panel dt {
  color: var(--muted);
  font-size: 12px;
  font-weight: 800;
  margin-bottom: 4px;
}
.analysis-panel dd {
  margin: 0;
  overflow-wrap: anywhere;
  font-size: 13px;
}
.detail-link { margin-bottom: 0; font-weight: 700; }
.content-section {
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 22px;
  margin-top: 18px;
}
table {
  border-collapse: collapse;
  width: 100%;
  margin: 16px 0 28px;
  table-layout: fixed;
  background: #FFFFFF;
}
th, td {
  border: 1px solid var(--line);
  padding: 8px;
  vertical-align: top;
  font-size: 13px;
  overflow-wrap: anywhere;
}
th { background: #EAF0F7; text-align: left; color: #243044; }
tr[id^="finding-row-"]:target { outline: 3px solid #0F766E; outline-offset: 3px; }
small { color: var(--muted); }
td img {
  max-width: 520px;
  width: 100%;
  height: auto;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: white;
}
.evidence-table-link {
  display: inline-block;
  padding: 9px 11px;
  border: 1px solid #CBD5E1;
  border-radius: 7px;
  background: #F8FAFC;
  font-weight: 800;
  text-decoration: none;
}
.lightbox {
  position: fixed;
  inset: 0;
  display: none;
  align-items: center;
  justify-content: center;
  padding: 52px 28px 28px;
  background: rgba(15, 23, 42, 0.92);
  z-index: 50;
}
.lightbox.open { display: flex; }
.lightbox-viewport {
  width: 96vw;
  height: 88vh;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  cursor: grab;
  touch-action: none;
}
.lightbox-viewport.dragging { cursor: grabbing; }
.lightbox img {
  display: block;
  max-width: 96vw;
  max-height: 88vh;
  object-fit: contain;
  background: #FFFFFF;
  border: 1px solid #475569;
  transform: translate3d(0, 0, 0) scale(1);
  transform-origin: center center;
  transition: transform 80ms ease-out;
  user-select: none;
  cursor: inherit;
}
.lightbox-close {
  position: fixed;
  top: 16px;
  right: 18px;
  min-width: 76px;
  height: 36px;
  border: 1px solid #CBD5E1;
  border-radius: 7px;
  background: #FFFFFF;
  color: #111827;
  font-weight: 800;
  cursor: pointer;
}
.risk { font-weight: 800; color: var(--danger); }
.truncated { color: var(--danger); font-weight: 800; }
.caption { color: var(--muted); font-size: 13px; }
.count { color: var(--muted); font-size: 13px; font-weight: 400; }
.full-csv-link a {
  display: inline-block;
  padding: 9px 12px;
  border: 1px solid var(--danger);
  border-radius: 7px;
  color: var(--danger);
  text-decoration: none;
  font-weight: 800;
  background: #FFFFFF;
}
.full-csv-link a:hover { background: #FEF2F2; }
@media (max-width: 980px) {
  .layout { display: block; }
  .toc {
    height: auto;
    max-height: 42vh;
    border-right: 0;
    border-bottom: 1px solid #0B1220;
  }
  main { padding: 18px; }
  .analysis-panel dl { grid-template-columns: 1fr; }
  #gallery-image-link { min-height: 280px; }
  #gallery-table-frame { min-height: 420px; height: 64vh; }
}
"""


def gallery_script() -> str:
    return """
<script>
(function () {
  const root = document.getElementById("evidence-gallery");
  if (!root || !root.dataset.gallery) return;
  const items = JSON.parse(root.dataset.gallery);
  if (!items.length) return;
  let index = 0;
  const els = {
    prev: document.getElementById("gallery-prev"),
    next: document.getElementById("gallery-next"),
    counter: document.getElementById("gallery-counter"),
    category: document.getElementById("gallery-category"),
    image: document.getElementById("gallery-image"),
    imageLink: document.getElementById("gallery-image-link"),
    tableFrame: document.getElementById("gallery-table-frame"),
    placeholder: document.getElementById("gallery-placeholder"),
    issue: document.getElementById("gallery-issue"),
    risk: document.getElementById("gallery-risk"),
    title: document.getElementById("gallery-title"),
    specific: document.getElementById("gallery-specific"),
    location: document.getElementById("gallery-location"),
    raw: document.getElementById("gallery-raw"),
    evidence: document.getElementById("gallery-evidence"),
    source: document.getElementById("gallery-source"),
    detail: document.getElementById("gallery-detail-link")
  };
  function setText(el, value) {
    if (el) el.textContent = value || "";
  }
  function render() {
    const item = items[index];
    setText(els.counter, `${index + 1} / ${items.length}`);
    setText(els.category, item.category);
    setText(els.issue, item.issue_id);
    setText(els.risk, item.risk);
    setText(els.title, item.title);
    setText(els.specific, item.specific_issue || "No localized analysis was recorded for this item.");
    setText(els.location, item.location || "not recorded");
    setText(els.raw, item.raw_data_file || "not recorded");
    setText(els.evidence, item.evidence || "not recorded");
    if (item.visual && item.visual_kind === "image") {
      els.image.src = item.visual;
      els.image.alt = `${item.issue_id} visual evidence`;
      els.imageLink.href = item.visual;
      els.imageLink.style.display = "flex";
      els.tableFrame.removeAttribute("src");
      els.tableFrame.style.display = "none";
      els.placeholder.style.display = "none";
    } else if (item.visual && item.visual_kind === "table") {
      els.image.removeAttribute("src");
      els.imageLink.removeAttribute("href");
      els.imageLink.style.display = "none";
      els.tableFrame.src = item.visual;
      els.tableFrame.style.display = "block";
      els.placeholder.style.display = "none";
    } else {
      els.image.removeAttribute("src");
      els.imageLink.removeAttribute("href");
      els.imageLink.style.display = "none";
      els.tableFrame.removeAttribute("src");
      els.tableFrame.style.display = "none";
      els.placeholder.style.display = "flex";
    }
    if (item.source_csv) {
      els.source.href = item.source_csv;
      els.source.textContent = item.source_csv_name || item.source_csv;
      els.source.style.display = "inline";
    } else {
      els.source.removeAttribute("href");
      els.source.textContent = "not recorded";
    }
    els.detail.href = `#${item.anchor}`;
  }
  function move(delta) {
    index = (index + delta + items.length) % items.length;
    render();
  }
  els.prev && els.prev.addEventListener("click", () => move(-1));
  els.next && els.next.addEventListener("click", () => move(1));
  const lightbox = document.getElementById("lightbox");
  const lightboxImage = document.getElementById("lightbox-image");
  const lightboxClose = document.getElementById("lightbox-close");
  const lightboxViewport = document.getElementById("lightbox-viewport");
  const MIN_LIGHTBOX_SCALE = 1;
  const MAX_LIGHTBOX_SCALE = 8;
  const LIGHTBOX_ZOOM_STEP = 1.18;
  let lightboxScale = 1;
  let lightboxPanX = 0;
  let lightboxPanY = 0;
  let lightboxDragging = false;
  let lightboxLastX = 0;
  let lightboxLastY = 0;
  let lightboxPointerId = null;
  function clampLightboxScale(value) {
    return Math.min(MAX_LIGHTBOX_SCALE, Math.max(MIN_LIGHTBOX_SCALE, value));
  }
  function applyLightboxTransform() {
    if (!lightboxImage) return;
    if (lightboxScale <= MIN_LIGHTBOX_SCALE + 0.001) {
      lightboxScale = MIN_LIGHTBOX_SCALE;
      lightboxPanX = 0;
      lightboxPanY = 0;
    }
    lightboxImage.style.transform = `translate3d(${lightboxPanX}px, ${lightboxPanY}px, 0) scale(${lightboxScale})`;
    if (lightboxViewport) lightboxViewport.classList.toggle("zoomed", lightboxScale > MIN_LIGHTBOX_SCALE);
  }
  function resetLightboxTransform() {
    lightboxScale = MIN_LIGHTBOX_SCALE;
    lightboxPanX = 0;
    lightboxPanY = 0;
    lightboxDragging = false;
    lightboxPointerId = null;
    if (lightboxViewport) lightboxViewport.classList.remove("dragging", "zoomed");
    applyLightboxTransform();
  }
  function closeLightbox() {
    if (!lightbox) return;
    lightbox.classList.remove("open");
    lightbox.setAttribute("aria-hidden", "true");
    resetLightboxTransform();
    if (lightboxImage) lightboxImage.removeAttribute("src");
  }
  function openLightbox(src, alt) {
    if (!lightbox || !lightboxImage || !src) return;
    lightboxImage.src = src;
    lightboxImage.alt = alt || "Enlarged visual evidence";
    resetLightboxTransform();
    lightbox.classList.add("open");
    lightbox.setAttribute("aria-hidden", "false");
  }
  function startLightboxDrag(event) {
    if (lightboxScale <= MIN_LIGHTBOX_SCALE || !lightboxViewport) return;
    if (typeof event.button === "number" && event.button !== 0) return;
    event.preventDefault();
    lightboxDragging = true;
    lightboxPointerId = event.pointerId;
    lightboxLastX = event.clientX;
    lightboxLastY = event.clientY;
    lightboxViewport.classList.add("dragging");
    if (typeof lightboxViewport.setPointerCapture === "function") {
      lightboxViewport.setPointerCapture(event.pointerId);
    }
  }
  function moveLightboxDrag(event) {
    if (!lightboxDragging || lightboxPointerId !== event.pointerId) return;
    event.preventDefault();
    lightboxPanX += event.clientX - lightboxLastX;
    lightboxPanY += event.clientY - lightboxLastY;
    lightboxLastX = event.clientX;
    lightboxLastY = event.clientY;
    applyLightboxTransform();
  }
  function stopLightboxDrag(event) {
    if (!lightboxDragging || lightboxPointerId !== event.pointerId) return;
    lightboxDragging = false;
    lightboxPointerId = null;
    if (lightboxViewport) {
      lightboxViewport.classList.remove("dragging");
      if (typeof lightboxViewport.releasePointerCapture === "function") {
        lightboxViewport.releasePointerCapture(event.pointerId);
      }
    }
  }
  lightboxViewport && lightboxViewport.addEventListener("wheel", (event) => {
    if (!lightbox || !lightbox.classList.contains("open")) return;
    event.preventDefault();
    const oldScale = lightboxScale;
    const zoomFactor = event.deltaY < 0 ? LIGHTBOX_ZOOM_STEP : 1 / LIGHTBOX_ZOOM_STEP;
    lightboxScale = clampLightboxScale(lightboxScale * zoomFactor);
    if (lightboxScale > MIN_LIGHTBOX_SCALE && lightboxViewport) {
      const rect = lightboxViewport.getBoundingClientRect();
      const pivotX = event.clientX - rect.left - rect.width / 2;
      const pivotY = event.clientY - rect.top - rect.height / 2;
      const ratio = oldScale > 0 ? lightboxScale / oldScale : 1;
      lightboxPanX = (lightboxPanX - pivotX) * ratio + pivotX;
      lightboxPanY = (lightboxPanY - pivotY) * ratio + pivotY;
    }
    applyLightboxTransform();
  }, { passive: false });
  lightboxViewport && lightboxViewport.addEventListener("pointerdown", startLightboxDrag);
  lightboxViewport && lightboxViewport.addEventListener("pointermove", moveLightboxDrag);
  lightboxViewport && lightboxViewport.addEventListener("pointerup", stopLightboxDrag);
  lightboxViewport && lightboxViewport.addEventListener("pointercancel", stopLightboxDrag);
  lightboxImage && lightboxImage.addEventListener("dragstart", (event) => event.preventDefault());
  els.imageLink && els.imageLink.addEventListener("click", (event) => {
    const item = items[index];
    if (!item.visual || item.visual_kind !== "image") return;
    event.preventDefault();
    openLightbox(item.visual, `${item.issue_id} visual evidence`);
  });
  document.querySelectorAll("[data-lightbox-src]").forEach((link) => {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      openLightbox(link.getAttribute("data-lightbox-src"), link.getAttribute("data-lightbox-title"));
    });
  });
  lightboxClose && lightboxClose.addEventListener("click", closeLightbox);
  lightbox && lightbox.addEventListener("click", (event) => {
    if (event.target === lightbox) closeLightbox();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && lightbox && lightbox.classList.contains("open")) {
      closeLightbox();
      return;
    }
    if (lightbox && lightbox.classList.contains("open")) return;
    if (event.key === "ArrowLeft") move(-1);
    if (event.key === "ArrowRight") move(1);
  });
  render();
})();
</script>
"""


def html_report(
    findings: list[VisualFinding],
    stats: list[FindingTypeStat],
    out_dir: Path,
    source_dir: Path | None,
    pdf_dirs: list[Path],
    max_per_type: int,
    full_csv_path: Path | None = None,
) -> str:
    counts = risk_counts(findings)
    summary_rows = "\n".join(
        f"<tr><td>{html.escape(k)}</td><td>{v}</td></tr>" for k, v in counts.items()
    )
    if not summary_rows:
        summary_rows = "<tr><td>No screen-positive findings</td><td>0</td></tr>"

    source_text = html.escape(str(source_dir)) if source_dir else "not provided"
    pdf_text = html.escape("; ".join(str(p) for p in pdf_dirs)) if pdf_dirs else "not provided"
    grouped = grouped_findings(findings)
    toc_items: list[tuple[str, str]] = [
        ("Top notice", "top"),
        ("Candidate Statistics", "candidate-statistics"),
        ("Inputs", "inputs"),
        ("Risk Summary", "risk-summary"),
        ("Findings", "findings"),
    ]
    toc_items.extend((f"{category} ({len(group)})", f"findings-{slug(category)}") for category, group in grouped.items())
    full_csv_link = html_full_csv_link(full_csv_path, out_dir)
    gallery_html = html_evidence_gallery(findings, out_dir)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Visual Paper Data Integrity Report</title>
<style>
{report_css()}
</style>
</head>
<body>
<div class="layout">
{html_toc(toc_items)}
<main>
<section id="top" class="report-hero">
<div class="hero-top">
<p class="eyebrow">Paper Data Integrity Audit</p>
<h1>Visual Paper Data Integrity Report</h1>
<p>This report is a visual review layer for audit CSV outputs. It does not conclude fabrication or misconduct. Exact embedded-image duplicates and numeric reuse patterns require author clarification when labels or contexts are independent; perceptual image matches and digit/order screens remain review candidates until manually inspected.</p>
</div>
<div class="notice-row">
<div class="warning">{html.escape(report_limit_notice(stats, max_per_type))}</div>
{full_csv_link}
</div>
{gallery_html}
</section>
<section id="candidate-statistics" class="content-section">
<h2>Candidate Statistics</h2>
{html_stats_table(stats, out_dir)}
</section>
<section id="inputs" class="content-section">
<h2>Inputs</h2>
<table>
<tr><th>Input type</th><th>Path</th></tr>
<tr><td>Source-data scan directory</td><td>{source_text}</td></tr>
<tr><td>PDF/image scan directories</td><td>{pdf_text}</td></tr>
</table>
</section>
<section id="risk-summary" class="content-section">
<h2>Risk Summary</h2>
<table><tr><th>Risk</th><th>Count</th></tr>{summary_rows}</table>
</section>
<section id="findings" class="content-section">
<h2>Findings With Visual Evidence</h2>
{html_grouped_findings(findings, out_dir)}
</section>
</main>
</div>
{gallery_script()}
</body>
</html>
"""


def markdown_report(
    findings: list[VisualFinding],
    stats: list[FindingTypeStat],
    out_dir: Path,
    source_dir: Path | None,
    pdf_dirs: list[Path],
    max_per_type: int,
    full_csv_path: Path | None = None,
) -> str:
    grouped = grouped_findings(findings)
    toc_items: list[tuple[str, str]] = [
        ("Candidate Statistics", "candidate-statistics"),
        ("Inputs", "inputs"),
        ("Risk Summary", "risk-summary"),
        ("Findings", "findings"),
    ]
    toc_items.extend((f"{category} ({len(group)})", slug(category)) for category, group in grouped.items())
    counts = risk_counts(findings)
    lines = [
        "# Visual Paper Data Integrity Report",
        "",
        "This report is a visual review layer for audit CSV outputs. It does not conclude fabrication or misconduct.",
        "",
        f"**Display limit notice:** {report_limit_notice(stats, max_per_type)}",
        "",
    ]
    lines.extend(markdown_full_csv_link(full_csv_path, out_dir))
    lines.extend(
        [
            "## Contents",
            "",
        ]
    )
    lines.extend(markdown_toc(toc_items))
    lines.extend(
        [
            "",
        "## Candidate Statistics",
        "",
        ]
    )
    lines.extend(markdown_stats_table(stats, out_dir))
    lines.extend(
        [
            "",
            "## Inputs",
            "",
            f"- Source-data scan directory: `{source_dir or 'not provided'}`",
            f"- PDF/image scan directories: `{'; '.join(str(p) for p in pdf_dirs) if pdf_dirs else 'not provided'}`",
            "",
            "## Risk Summary",
            "",
            "| Risk | Count |",
            "|---|---:|",
        ]
    )
    if counts:
        for risk, count in counts.items():
            lines.append(f"| {risk} | {count} |")
    else:
        lines.append("| No screen-positive findings | 0 |")
    lines.extend(
        [
            "",
            "## Findings",
            "",
        ]
    )
    lines.extend(markdown_grouped_findings(findings, out_dir))
    return "\n".join(lines)

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-dir", type=Path, required=True, help="Parent audit_results directory")
    parser.add_argument("--source-scan", type=Path, help="source_data_scan directory")
    parser.add_argument("--pdf-scan", type=Path, action="append", default=[], help="PDF/image scan directory; repeatable")
    parser.add_argument("--out", type=Path, help="Output directory, default audit-dir/visual_report")
    parser.add_argument(
        "--max-per-type",
        type=int,
        default=DEFAULT_MAX_PER_TYPE,
        help="Maximum numeric findings per source CSV type; 0 means unlimited",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    audit_dir = args.audit_dir
    out_dir = args.out or (audit_dir / "visual_report")
    visual_dir = out_dir / "visual_evidence"
    out_dir.mkdir(parents=True, exist_ok=True)
    visual_dir.mkdir(parents=True, exist_ok=True)

    source_dir = args.source_scan or (audit_dir / "source_data_scan")
    if not source_dir.exists():
        source_dir = None
    pdf_dirs = find_pdf_scan_dirs(audit_dir, args.pdf_scan)

    findings: list[VisualFinding] = []
    stats: list[FindingTypeStat] = []
    if source_dir is not None:
        numeric_findings, numeric_stats = collect_numeric_findings(source_dir, visual_dir, args.max_per_type)
        findings.extend(numeric_findings)
        stats.extend(numeric_stats)
    image_findings, image_stats = collect_image_findings(pdf_dirs, visual_dir)
    findings.extend(image_findings)
    stats.extend(image_stats)

    findings = sorted_findings(findings)
    full_csv_path = write_full_csv_visualizations(stats, out_dir, args.max_per_type)

    write_text(out_dir / "visual_report.html", html_report(findings, stats, out_dir, source_dir, pdf_dirs, args.max_per_type, full_csv_path))
    write_text(out_dir / "visual_report.md", markdown_report(findings, stats, out_dir, source_dir, pdf_dirs, args.max_per_type, full_csv_path))

    print(f"Wrote visual report to {out_dir / 'visual_report.html'}")
    if full_csv_path:
        print(f"Wrote full CSV visualization to {full_csv_path}")
    print(f"findings={len(findings)}")
    for risk, count in risk_counts(findings).items():
        print(f"{risk}={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
