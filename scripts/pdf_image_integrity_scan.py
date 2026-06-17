#!/usr/bin/env python3
"""Four-layer PDF image integrity screening.

Outputs:
  embedded_image_placements.csv
  exact_embedded_image_duplicates.csv
  near_embedded_image_matches.csv
  page_render_hashes.csv
  near_page_render_matches.csv
  region_duplicate_candidates.csv

The script is a screening tool. Exact embedded-image sha256 matches are strong
evidence of byte-identical reuse; perceptual hash and tile matches are review
candidates that need visual/contextual confirmation.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import sys
from collections import defaultdict
from io import BytesIO
from pathlib import Path
from typing import Any

MISSING_DEPS: list[str] = []

try:
    import fitz  # PyMuPDF
except ModuleNotFoundError:
    fitz = None
    MISSING_DEPS.append("pymupdf (import name: fitz)")

try:
    import numpy as np
except ModuleNotFoundError:
    np = None
    MISSING_DEPS.append("numpy")

try:
    from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError
except ModuleNotFoundError:
    Image = None
    ImageDraw = None
    ImageOps = None
    UnidentifiedImageError = OSError
    MISSING_DEPS.append("pillow (import name: PIL)")

if Image is not None:
    try:
        RESAMPLE = Image.Resampling.LANCZOS
    except AttributeError:  # pragma: no cover - old Pillow fallback
        RESAMPLE = Image.LANCZOS
else:
    RESAMPLE = None


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def bits_to_hex(bits: np.ndarray) -> str:
    value = 0
    for bit in bits.astype(bool).ravel():
        value = (value << 1) | int(bit)
    return f"{value:016x}"


def hamming_hex(a: str, b: str) -> int:
    return (int(a, 16) ^ int(b, 16)).bit_count()


def dhash_image(image: Image.Image) -> str:
    gray = ImageOps.grayscale(image).resize((9, 8), RESAMPLE)
    arr = np.asarray(gray, dtype=np.int16)
    return bits_to_hex(arr[:, 1:] > arr[:, :-1])


def ahash_image(image: Image.Image) -> str:
    gray = ImageOps.grayscale(image).resize((8, 8), RESAMPLE)
    arr = np.asarray(gray, dtype=np.float32)
    return bits_to_hex(arr > float(arr.mean()))


def decode_pdf_image(doc: fitz.Document, xref: int, data: bytes) -> Image.Image | None:
    try:
        return Image.open(BytesIO(data)).convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError):
        pass

    try:
        pix = fitz.Pixmap(doc, xref)
        if pix.alpha or pix.n >= 4:
            pix = fitz.Pixmap(fitz.csRGB, pix)
        return Image.open(BytesIO(pix.tobytes("png"))).convert("RGB")
    except Exception:
        return None


def rect_to_text(rect: fitz.Rect | None) -> str:
    if rect is None:
        return ""
    return f"{rect.x0:.2f},{rect.y0:.2f},{rect.x1:.2f},{rect.y1:.2f}"


def write_csv(path: Path, rows: list[dict[str, Any]], headers: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def text_block(text: str, width: int, height: int) -> Image.Image:
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    words = text.replace("|", " | ").split()
    lines: list[str] = []
    line = ""
    for word in words:
        trial = f"{line} {word}".strip()
        if len(trial) <= 34:
            line = trial
        else:
            if line:
                lines.append(line)
            line = word[:34]
    if line:
        lines.append(line)
    draw.multiline_text((4, 4), "\n".join(lines[:4]), fill="black", spacing=2)
    return img


def thumbnail_cell(image: Image.Image | None, label: str, size: tuple[int, int]) -> Image.Image:
    thumb_w, thumb_h = size
    label_h = 58
    cell = Image.new("RGB", (thumb_w, thumb_h + label_h), "white")
    if image is None:
        draw = ImageDraw.Draw(cell)
        draw.rectangle((0, 0, thumb_w - 1, thumb_h - 1), outline="gray")
        draw.text((8, 8), "decode failed", fill="black")
    else:
        thumb = image.copy()
        thumb.thumbnail((thumb_w, thumb_h), RESAMPLE)
        x = (thumb_w - thumb.width) // 2
        y = (thumb_h - thumb.height) // 2
        cell.paste(thumb, (x, y))
    cell.paste(text_block(label, thumb_w, label_h), (0, thumb_h))
    return cell


def save_pair_contact_sheet(
    path: Path,
    pairs: list[tuple[Image.Image | None, str, Image.Image | None, str, str]],
    max_items: int,
) -> None:
    if not pairs:
        return
    thumb_size = (190, 130)
    note_w = 160
    gap = 12
    row_h = thumb_size[1] + 58
    width = thumb_size[0] * 2 + note_w + gap * 4
    rows = min(len(pairs), max_items)
    sheet = Image.new("RGB", (width, rows * (row_h + gap) + gap), "white")
    for idx, (left_img, left_label, right_img, right_label, note) in enumerate(pairs[:max_items]):
        y = gap + idx * (row_h + gap)
        x = gap
        sheet.paste(thumbnail_cell(left_img, left_label, thumb_size), (x, y))
        x += thumb_size[0] + gap
        sheet.paste(thumbnail_cell(right_img, right_label, thumb_size), (x, y))
        x += thumb_size[0] + gap
        sheet.paste(text_block(note, note_w, row_h), (x, y))
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path)


def save_group_contact_sheet(
    path: Path,
    groups: list[tuple[str, list[dict[str, Any]]]],
    image_cache: dict[int, dict[str, Any]],
    max_groups: int,
    max_per_group: int,
) -> None:
    if not groups:
        return
    thumb_size = (170, 120)
    label_h = 58
    gap = 10
    cols = max_per_group
    row_h = thumb_size[1] + label_h
    width = cols * thumb_size[0] + (cols + 1) * gap
    rows = min(len(groups), max_groups)
    sheet = Image.new("RGB", (width, rows * (row_h + gap) + gap), "white")
    for row_idx, (group_id, members) in enumerate(groups[:max_groups]):
        y = gap + row_idx * (row_h + gap)
        for col_idx, member in enumerate(members[:max_per_group]):
            x = gap + col_idx * (thumb_size[0] + gap)
            image = image_cache.get(int(member["xref"]), {}).get("image")
            label = (
                f"{group_id} p{member['page']} xref {member['xref']} "
                f"place {member['placement_index']} rect {member['placement_rect']}"
            )
            sheet.paste(thumbnail_cell(image, label, thumb_size), (x, y))
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path)


def extract_embedded_images(doc: fitz.Document) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    image_cache: dict[int, dict[str, Any]] = {}
    placement_id = 0

    for page_index, page in enumerate(doc, start=1):
        seen_page_xrefs: set[int] = set()
        for image_index, image_info in enumerate(page.get_images(full=True), start=1):
            xref = int(image_info[0])
            if xref in seen_page_xrefs:
                continue
            seen_page_xrefs.add(xref)

            width = int(image_info[2])
            height = int(image_info[3])
            bpc = image_info[4]
            colorspace = image_info[5]
            name = image_info[7] if len(image_info) > 7 else ""
            image_filter = image_info[8] if len(image_info) > 8 else ""

            if xref not in image_cache:
                extracted = doc.extract_image(xref)
                data = extracted.get("image", b"")
                ext = extracted.get("ext", "")
                pil_image = decode_pdf_image(doc, xref, data)
                if pil_image is not None:
                    dhash = dhash_image(pil_image)
                    ahash = ahash_image(pil_image)
                else:
                    dhash = ""
                    ahash = ""
                image_cache[xref] = {
                    "bytes": data,
                    "byte_length": len(data),
                    "sha256": sha256_bytes(data),
                    "ext": ext,
                    "image": pil_image,
                    "dhash": dhash,
                    "ahash": ahash,
                }

            cache = image_cache[xref]
            rects = page.get_image_rects(xref) or [None]
            placement_count = len(rects)
            placements_joined = "|".join(rect_to_text(rect) for rect in rects)
            for placement_index, rect in enumerate(rects, start=1):
                placement_id += 1
                rows.append(
                    {
                        "placement_id": placement_id,
                        "page": page_index,
                        "xref": xref,
                        "image_index_on_page": image_index,
                        "placement_index": placement_index,
                        "placement_count_for_page_xref": placement_count,
                        "placement_rect": rect_to_text(rect),
                        "placements": placements_joined,
                        "width": width,
                        "height": height,
                        "colorspace": colorspace,
                        "bits_per_component": bpc,
                        "name": name,
                        "filter": image_filter,
                        "ext": cache["ext"],
                        "byte_length": cache["byte_length"],
                        "sha256": cache["sha256"],
                        "dhash": cache["dhash"],
                        "ahash": cache["ahash"],
                    }
                )

    return rows, image_cache


def exact_duplicate_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[tuple[str, list[dict[str, Any]]]]]:
    by_sha: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_sha[row["sha256"]].append(row)

    out: list[dict[str, Any]] = []
    groups: list[tuple[str, list[dict[str, Any]]]] = []
    group_no = 0
    for sha, members in sorted(by_sha.items(), key=lambda item: (-len(item[1]), item[0])):
        if len(members) < 2:
            continue
        group_no += 1
        group_id = f"EXACT{group_no:04d}"
        groups.append((group_id, members))
        for member in members:
            row = dict(member)
            row["exact_group_id"] = group_id
            row["duplicate_placement_count"] = len(members)
            row["evidence_note"] = "byte-identical embedded image data"
            out.append(row)
    return out, groups


def near_image_matches(
    rows: list[dict[str, Any]],
    min_size: int,
    dhash_threshold: int,
    ahash_threshold: int,
    max_pairs: int,
) -> list[dict[str, Any]]:
    candidates = [
        row
        for row in rows
        if row["dhash"]
        and row["ahash"]
        and int(row["width"]) >= min_size
        and int(row["height"]) >= min_size
    ]
    out: list[dict[str, Any]] = []
    for left, right in itertools.combinations(candidates, 2):
        if left["sha256"] == right["sha256"]:
            continue
        dh_dist = hamming_hex(left["dhash"], right["dhash"])
        if dh_dist > dhash_threshold:
            continue
        ah_dist = hamming_hex(left["ahash"], right["ahash"])
        if ah_dist > ahash_threshold:
            continue
        out.append(
            {
                "candidate_id": f"NEAR{len(out) + 1:04d}",
                "dhash_distance": dh_dist,
                "ahash_distance": ah_dist,
                "page_a": left["page"],
                "xref_a": left["xref"],
                "placement_id_a": left["placement_id"],
                "placement_rect_a": left["placement_rect"],
                "width_a": left["width"],
                "height_a": left["height"],
                "sha256_a": left["sha256"],
                "dhash_a": left["dhash"],
                "ahash_a": left["ahash"],
                "page_b": right["page"],
                "xref_b": right["xref"],
                "placement_id_b": right["placement_id"],
                "placement_rect_b": right["placement_rect"],
                "width_b": right["width"],
                "height_b": right["height"],
                "sha256_b": right["sha256"],
                "dhash_b": right["dhash"],
                "ahash_b": right["ahash"],
                "screen_note": "perceptual-hash candidate; visual review required",
            }
        )
        if len(out) >= max_pairs:
            break
    return out


def render_pages(
    doc: fitz.Document,
    out_dir: Path,
    dpi: int,
    save_renders: bool,
) -> tuple[list[dict[str, Any]], dict[int, Image.Image]]:
    matrix = fitz.Matrix(dpi / 72, dpi / 72)
    rows: list[dict[str, Any]] = []
    images: dict[int, Image.Image] = {}
    render_dir = out_dir / "page_renders"
    if save_renders:
        render_dir.mkdir(parents=True, exist_ok=True)

    for page_index, page in enumerate(doc, start=1):
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        png = pix.tobytes("png")
        image = Image.open(BytesIO(png)).convert("RGB")
        images[page_index] = image
        if save_renders:
            image.save(render_dir / f"page_{page_index:03d}.png")
        rows.append(
            {
                "page": page_index,
                "render_dpi": dpi,
                "width": image.width,
                "height": image.height,
                "sha256": sha256_bytes(png),
                "dhash": dhash_image(image),
                "ahash": ahash_image(image),
            }
        )
    return rows, images


def near_page_matches(
    rows: list[dict[str, Any]],
    dhash_threshold: int,
    ahash_threshold: int,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for left, right in itertools.combinations(rows, 2):
        exact = left["sha256"] == right["sha256"]
        dh_dist = hamming_hex(left["dhash"], right["dhash"])
        ah_dist = hamming_hex(left["ahash"], right["ahash"])
        if not exact and (dh_dist > dhash_threshold or ah_dist > ahash_threshold):
            continue
        out.append(
            {
                "candidate_id": f"PAGE{len(out) + 1:04d}",
                "page_a": left["page"],
                "page_b": right["page"],
                "exact_render_sha256_match": exact,
                "dhash_distance": dh_dist,
                "ahash_distance": ah_dist,
                "sha256_a": left["sha256"],
                "sha256_b": right["sha256"],
                "screen_note": "rendered-page candidate; review layout context",
            }
        )
    return out


def region_duplicate_candidates(
    page_images: dict[int, Image.Image],
    tile_size: int,
    tile_stride: int,
    min_gray_sd: float,
    min_gray_mean: float,
    max_gray_mean: float,
    max_pairs: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    tiles: list[dict[str, Any]] = []
    for page, image in page_images.items():
        gray = np.asarray(ImageOps.grayscale(image), dtype=np.uint8)
        height, width = gray.shape
        tile_id = 0
        for y0 in range(0, max(0, height - tile_size + 1), tile_stride):
            for x0 in range(0, max(0, width - tile_size + 1), tile_stride):
                tile = gray[y0 : y0 + tile_size, x0 : x0 + tile_size]
                mean = float(tile.mean())
                sd = float(tile.std())
                if sd < min_gray_sd or mean < min_gray_mean or mean > max_gray_mean:
                    continue
                tile_id += 1
                tile_image = Image.fromarray(tile, mode="L")
                tiles.append(
                    {
                        "page": page,
                        "tile_id_on_page": tile_id,
                        "x0": x0,
                        "y0": y0,
                        "x1": x0 + tile_size,
                        "y1": y0 + tile_size,
                        "gray_mean": f"{mean:.3f}",
                        "gray_sd": f"{sd:.3f}",
                        "tile_sha256": sha256_bytes(tile.tobytes()),
                        "dhash": dhash_image(tile_image),
                    }
                )

    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for tile in tiles:
        buckets[tile["dhash"]].append(tile)

    out: list[dict[str, Any]] = []
    for dhash, members in sorted(buckets.items(), key=lambda item: (-len(item[1]), item[0])):
        if len(members) < 2:
            continue
        for left, right in itertools.combinations(members, 2):
            out.append(
                {
                    "candidate_id": f"REGION{len(out) + 1:04d}",
                    "dhash": dhash,
                    "page_a": left["page"],
                    "tile_id_a": left["tile_id_on_page"],
                    "rect_a": f"{left['x0']},{left['y0']},{left['x1']},{left['y1']}",
                    "gray_mean_a": left["gray_mean"],
                    "gray_sd_a": left["gray_sd"],
                    "tile_sha256_a": left["tile_sha256"],
                    "page_b": right["page"],
                    "tile_id_b": right["tile_id_on_page"],
                    "rect_b": f"{right['x0']},{right['y0']},{right['x1']},{right['y1']}",
                    "gray_mean_b": right["gray_mean"],
                    "gray_sd_b": right["gray_sd"],
                    "tile_sha256_b": right["tile_sha256"],
                    "screen_note": "same rendered-tile dHash bucket; visual review required",
                }
            )
            if len(out) >= max_pairs:
                return out, tiles
    return out, tiles


def make_near_pair_sheet(
    path: Path,
    matches: list[dict[str, Any]],
    image_cache: dict[int, dict[str, Any]],
    max_items: int,
) -> None:
    pairs = []
    for row in matches[:max_items]:
        left = image_cache.get(int(row["xref_a"]), {}).get("image")
        right = image_cache.get(int(row["xref_b"]), {}).get("image")
        pairs.append(
            (
                left,
                f"{row['candidate_id']} A p{row['page_a']} xref {row['xref_a']} rect {row['placement_rect_a']}",
                right,
                f"{row['candidate_id']} B p{row['page_b']} xref {row['xref_b']} rect {row['placement_rect_b']}",
                f"dHash {row['dhash_distance']} | aHash {row['ahash_distance']}",
            )
        )
    save_pair_contact_sheet(path, pairs, max_items)


def make_region_pair_sheet(
    path: Path,
    matches: list[dict[str, Any]],
    page_images: dict[int, Image.Image],
    max_items: int,
) -> None:
    pairs = []
    for row in matches[:max_items]:
        x0a, y0a, x1a, y1a = [int(v) for v in str(row["rect_a"]).split(",")]
        x0b, y0b, x1b, y1b = [int(v) for v in str(row["rect_b"]).split(",")]
        left = page_images[int(row["page_a"])].crop((x0a, y0a, x1a, y1a))
        right = page_images[int(row["page_b"])].crop((x0b, y0b, x1b, y1b))
        pairs.append(
            (
                left,
                f"{row['candidate_id']} A p{row['page_a']} {row['rect_a']}",
                right,
                f"{row['candidate_id']} B p{row['page_b']} {row['rect_b']}",
                "same tile dHash bucket",
            )
        )
    save_pair_contact_sheet(path, pairs, max_items)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path, help="PDF file to scan")
    parser.add_argument("--out", type=Path, default=Path("audit_results/pdf_image_scan"))
    parser.add_argument("--min-image-size", type=int, default=80)
    parser.add_argument("--dhash-threshold", type=int, default=5)
    parser.add_argument("--ahash-threshold", type=int, default=8)
    parser.add_argument("--render-dpi", type=int, default=150)
    parser.add_argument("--page-dhash-threshold", type=int, default=3)
    parser.add_argument("--page-ahash-threshold", type=int, default=5)
    parser.add_argument("--tile-size", type=int, default=224)
    parser.add_argument("--tile-stride", type=int, default=224)
    parser.add_argument("--tile-min-gray-sd", type=float, default=18.0)
    parser.add_argument("--tile-min-gray-mean", type=float, default=8.0)
    parser.add_argument("--tile-max-gray-mean", type=float, default=248.0)
    parser.add_argument("--max-near-pairs", type=int, default=5000)
    parser.add_argument("--max-region-pairs", type=int, default=5000)
    parser.add_argument("--contact-sheet-limit", type=int, default=80)
    parser.add_argument("--save-page-renders", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if MISSING_DEPS:
        print(
            "Missing dependencies for PDF/image screening: "
            + ", ".join(MISSING_DEPS),
            file=sys.stderr,
        )
        print(
            "Install with: py -m pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 2

    args.out.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(args.pdf)
    embedded_rows, image_cache = extract_embedded_images(doc)
    exact_rows, exact_groups = exact_duplicate_rows(embedded_rows)
    near_rows = near_image_matches(
        embedded_rows,
        args.min_image_size,
        args.dhash_threshold,
        args.ahash_threshold,
        args.max_near_pairs,
    )
    page_rows, page_images = render_pages(doc, args.out, args.render_dpi, args.save_page_renders)
    page_match_rows = near_page_matches(
        page_rows,
        args.page_dhash_threshold,
        args.page_ahash_threshold,
    )
    region_rows, tile_rows = region_duplicate_candidates(
        page_images,
        args.tile_size,
        args.tile_stride,
        args.tile_min_gray_sd,
        args.tile_min_gray_mean,
        args.tile_max_gray_mean,
        args.max_region_pairs,
    )
    doc.close()

    embedded_headers = [
        "placement_id",
        "page",
        "xref",
        "image_index_on_page",
        "placement_index",
        "placement_count_for_page_xref",
        "placement_rect",
        "placements",
        "width",
        "height",
        "colorspace",
        "bits_per_component",
        "name",
        "filter",
        "ext",
        "byte_length",
        "sha256",
        "dhash",
        "ahash",
    ]
    exact_headers = ["exact_group_id", "duplicate_placement_count"] + embedded_headers + ["evidence_note"]
    near_headers = [
        "candidate_id",
        "dhash_distance",
        "ahash_distance",
        "page_a",
        "xref_a",
        "placement_id_a",
        "placement_rect_a",
        "width_a",
        "height_a",
        "sha256_a",
        "dhash_a",
        "ahash_a",
        "page_b",
        "xref_b",
        "placement_id_b",
        "placement_rect_b",
        "width_b",
        "height_b",
        "sha256_b",
        "dhash_b",
        "ahash_b",
        "screen_note",
    ]
    page_headers = ["page", "render_dpi", "width", "height", "sha256", "dhash", "ahash"]
    page_match_headers = [
        "candidate_id",
        "page_a",
        "page_b",
        "exact_render_sha256_match",
        "dhash_distance",
        "ahash_distance",
        "sha256_a",
        "sha256_b",
        "screen_note",
    ]
    region_headers = [
        "candidate_id",
        "dhash",
        "page_a",
        "tile_id_a",
        "rect_a",
        "gray_mean_a",
        "gray_sd_a",
        "tile_sha256_a",
        "page_b",
        "tile_id_b",
        "rect_b",
        "gray_mean_b",
        "gray_sd_b",
        "tile_sha256_b",
        "screen_note",
    ]
    tile_headers = [
        "page",
        "tile_id_on_page",
        "x0",
        "y0",
        "x1",
        "y1",
        "gray_mean",
        "gray_sd",
        "tile_sha256",
        "dhash",
    ]

    write_csv(args.out / "embedded_image_placements.csv", embedded_rows, embedded_headers)
    write_csv(args.out / "exact_embedded_image_duplicates.csv", exact_rows, exact_headers)
    write_csv(args.out / "near_embedded_image_matches.csv", near_rows, near_headers)
    write_csv(args.out / "page_render_hashes.csv", page_rows, page_headers)
    write_csv(args.out / "near_page_render_matches.csv", page_match_rows, page_match_headers)
    write_csv(args.out / "region_duplicate_candidates.csv", region_rows, region_headers)
    write_csv(args.out / "rendered_tile_inventory.csv", tile_rows, tile_headers)

    save_group_contact_sheet(
        args.out / "exact_embedded_image_duplicates_contact_sheet.png",
        exact_groups,
        image_cache,
        args.contact_sheet_limit,
        4,
    )
    make_near_pair_sheet(
        args.out / "near_embedded_image_matches_contact_sheet.png",
        near_rows,
        image_cache,
        args.contact_sheet_limit,
    )
    make_region_pair_sheet(
        args.out / "region_duplicate_candidates_contact_sheet.png",
        region_rows,
        page_images,
        args.contact_sheet_limit,
    )

    summary_rows = [
        {"metric": "embedded_image_placements", "value": len(embedded_rows)},
        {"metric": "exact_duplicate_placements", "value": len(exact_rows)},
        {"metric": "exact_duplicate_groups", "value": len(exact_groups)},
        {"metric": "near_embedded_image_matches", "value": len(near_rows)},
        {"metric": "page_render_matches", "value": len(page_match_rows)},
        {"metric": "rendered_tiles_after_low_info_filter", "value": len(tile_rows)},
        {"metric": "region_duplicate_candidates", "value": len(region_rows)},
    ]
    write_csv(args.out / "summary.csv", summary_rows, ["metric", "value"])

    print(f"Wrote PDF image scan outputs to {args.out}")
    for row in summary_rows:
        print(f"{row['metric']}: {row['value']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
