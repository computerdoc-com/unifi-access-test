#!/usr/bin/env python3
"""Convert the UniFi Access API reference PDF into Markdown.

Uses PyMuPDF font metadata to recover document structure:
  21.9pt bold -> H1 (chapter)      14.6pt bold -> H3 (Request Header, ...)
  17.1pt bold -> H2 (endpoint)     LucidaConsole -> fenced code block
Ruled boxes with >= 2 columns are extracted as Markdown tables.

Usage: pdf2md.py <input.pdf> <output.md> [--assets DIR]
"""

import argparse
import os
import re
import sys

import fitz

MONO = "LucidaConsole"
H1_SIZE, H2_SIZE, H3_SIZE = 21.0, 16.0, 13.0
TOC_PAGES = range(1, 6)  # table-of-contents pages, regenerated from headings


# --------------------------------------------------------------------------- #
# extraction
# --------------------------------------------------------------------------- #

def clean(text):
    """Normalize whitespace and the PDF's stray control/space characters."""
    text = text.replace("\xa0", " ").replace("​", "")
    text = text.replace("ﬀ", "ff").replace("ﬁ", "fi").replace("ﬂ", "fl")
    return re.sub(r"[ \t]+", " ", text).strip()


def keep_tables(page):
    """Return ruled regions that are real tables, outermost-first, no nesting.

    Single-row full-width boxes are page frames, not tables — without this
    guard a decorative border swallows the whole page into one cell.
    """
    page_area = page.rect.width * page.rect.height
    found = []
    for tab in page.find_tables().tables:
        if tab.col_count < 2 or tab.row_count < 2:
            continue
        area = (tab.bbox[2] - tab.bbox[0]) * (tab.bbox[3] - tab.bbox[1])
        if area > 0.7 * page_area and tab.row_count < 3:
            continue
        found.append(tab)
    found.sort(key=lambda t: (t.bbox[2] - t.bbox[0]) * (t.bbox[3] - t.bbox[1]), reverse=True)
    kept = []
    for tab in found:
        if any(inside(tab.bbox, k.bbox) for k in kept):
            continue
        kept.append(tab)
    return kept


def inside(inner, outer, pad=2):
    return (inner[0] >= outer[0] - pad and inner[1] >= outer[1] - pad
            and inner[2] <= outer[2] + pad and inner[3] <= outer[3] + pad)


def line_items(page, table_boxes):
    """Text lines outside any table box, as dicts sorted later by position."""
    items = []
    for block in page.get_text("dict")["blocks"]:
        if block["type"] != 0:
            continue
        for line in block["lines"]:
            spans = [s for s in line["spans"] if s["text"].strip()]
            if not spans:
                continue
            bbox = line["bbox"]
            cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
            if any(b[0] - 2 <= cx <= b[2] + 2 and b[1] - 2 <= cy <= b[3] + 2
                   for b in table_boxes):
                continue
            # style checks ignore blank spans, but the text must keep them:
            # code indentation and inter-word gaps live in whitespace-only spans
            raw = "".join(s["text"] for s in line["spans"])
            mono = all(s["font"].startswith(MONO) for s in spans)
            items.append({
                "kind": "line",
                "y": bbox[1],
                "x": bbox[0],
                "bottom": bbox[3],
                "spans": spans,
                "all_spans": line["spans"],
                "size": max(round(s["size"], 1) for s in spans),
                "mono": mono,
                # code keeps its leading \xa0 indentation verbatim
                "text": raw.replace("\xa0", " ").rstrip() if mono else clean(raw),
            })
    return [i for i in items if i["text"].strip()]


IDENT = re.compile(r"^[A-Za-z_][\w\[\]./{}-]*$")

# indented one-line facts under an endpoint heading; each gets its own bullet
META_LABEL = re.compile(
    r"^\*{0,2}(Request URL|Permission Key|Method|Protocol|Schemas?"
    r"|UniFi Access Requirement|API version)\*{0,2}\s*:", re.I)


def contains(bbox, cx, cy, pad=1):
    return bbox[0] - pad <= cx <= bbox[2] + pad and bbox[1] - pad <= cy <= bbox[3] + pad


def real_rows(tab):
    """Rows of the table, minus sub-rows nested inside a taller row.

    Irregular ruling inside a tall cell makes find_tables emit extra rows that
    duplicate a slice of their parent; keeping them shreds the parent's text.
    """
    rows = list(tab.rows)
    return [r for r in rows
            if not any(other is not r and inside(r.bbox, other.bbox)
                       and (other.bbox[3] - other.bbox[1]) > (r.bbox[3] - r.bbox[1])
                       for other in rows)]


def assign_words(tab, page_words):
    """Map (row, col) -> words, keeping every word inside the table bbox.

    A word can fall in the table but in no cell when the ruling is irregular;
    those are attached to the nearest cell rather than dropped.
    """
    cells = [(r, c, bb) for r, row in enumerate(real_rows(tab))
             for c, bb in enumerate(row.cells) if bb]
    if not cells:
        return {}

    # group words into their source text lines: wrapped prose that overruns the
    # ruled column stays in one cell instead of being sliced at the boundary
    lines = {}
    for x0, y0, x1, y1, word, bno, lno, wno in page_words:
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if not contains(tab.bbox, cx, cy):
            continue
        lines.setdefault((bno, lno), []).append((wno, x0, cx, cy, word))

    buckets = {}
    for (bno, lno), group in lines.items():
        group.sort()
        anchor_x = group[0][2]                     # centre of the first word
        anchor_y = sum(g[3] for g in group) / len(group)
        hit = next(((r, c) for r, c, bb in cells if contains(bb, anchor_x, anchor_y)), None)
        if hit is None:
            def dist(entry):
                _, _, bb = entry
                dx = max(bb[0] - anchor_x, 0, anchor_x - bb[2])
                dy = max(bb[1] - anchor_y, 0, anchor_y - bb[3])
                return dy * 4 + dx                 # prefer the same row band
            r, c, _ = min(cells, key=dist)
            hit = (r, c)
        for wno, _, _, _, word in group:
            # order by the PDF's own sequence, not by baseline: inline code
            # sits lower than the surrounding text and would sort out of place
            buckets.setdefault(hit, []).append(((bno, lno, wno), word))
    return buckets


def format_cell(words):
    text = clean(" ".join(w for _, w in sorted(words)))
    if IDENT.match(text) and ("_" in text or "[]" in text or "." in text):
        text = f"`{text}`"
    return text.replace("|", "\\|")


def table_markdown(tab, page_words):
    """Render a pymupdf table as a Markdown table; returns (header, lines)."""
    buckets = assign_words(tab, page_words)
    rows = []
    for r, row in enumerate(real_rows(tab)):
        cells = [format_cell(buckets.get((r, c), []))
                 for c in range(len(row.cells))]
        if any(cells):
            rows.append(cells)
    if not rows:
        return None, []
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    # drop columns the ruling created but nothing ever fills
    used = [c for c in range(width) if any(r[c] for r in rows)]
    if not used:
        return None, []
    rows = [[r[c] for c in used] for r in rows]
    width = len(used)
    header = rows[0]
    body = rows[1:]
    lines = ["| " + " | ".join(header) + " |",
             "| " + " | ".join(["---"] * width) + " |"]
    lines += ["| " + " | ".join(r) + " |" for r in body]
    return tuple(header), lines


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #

def render_line(item):
    """Body line with inline bold/code, collapsing adjacent same-style spans."""
    runs, prev_style = [], None
    for span in item["all_spans"]:
        text = span["text"].replace("\xa0", " ")
        if not text:
            continue
        style = ("mono" if span["font"].startswith(MONO)
                 else "bold" if span["flags"] & 16 else "plain")
        if not text.strip():           # whitespace joins the run before it
            style = prev_style or "plain"
        if style == prev_style and runs:
            runs[-1][1] += text
        else:
            runs.append([style, text])
            prev_style = style

    parts = []
    for style, text in runs:
        lead = " " if text[:1].isspace() else ""
        trail = " " if text[-1:].isspace() else ""
        core = re.sub(r"\s+", " ", text.strip())
        if not core:
            parts.append(" ")
            continue
        marker = "`" if style == "mono" else "**" if style == "bold" else ""
        parts.append(f"{lead}{marker}{core}{marker}{trail}")
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def code_fence(group):
    """Fenced block from consecutive monospace lines, indentation preserved."""
    lines = [item["text"] for item in group]
    common = min((len(l) - len(l.lstrip()) for l in lines if l.strip()), default=0)
    lines = [l[common:] if l.strip() else "" for l in lines]
    body = "\n".join(lines)
    lang = ""
    stripped = body.lstrip()
    if stripped.startswith(("{", "[")):
        lang = "json"
    elif stripped.startswith(("curl", "wscat", "GET", "POST", "PUT", "DELETE")):
        lang = "bash"
    return [f"```{lang}", body, "```"]


def slugify(text):
    slug = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    return re.sub(r"[\s_]+", "-", slug)


def convert(path, out_path, assets_dir):
    doc = fitz.open(path)
    body = []          # markdown lines
    toc = []           # (level, title, slug)
    last_header = None  # header tuple of the previously emitted table
    img_count = 0

    for pno, page in enumerate(doc):
        if pno == 0 or pno in TOC_PAGES:
            continue

        tables = keep_tables(page)
        boxes = [t.bbox for t in tables]
        page_words = page.get_text("words")
        items = line_items(page, boxes)
        for tab in tables:
            items.append({"kind": "table", "y": tab.bbox[1], "x": tab.bbox[0], "tab": tab})
        for block in page.get_text("dict")["blocks"]:
            if block["type"] == 1:
                items.append({"kind": "image", "y": block["bbox"][1],
                              "x": block["bbox"][0], "block": block})
        items.sort(key=lambda i: (round(i["y"] / 4), i["x"]))

        first_on_page = True
        idx = 0
        while idx < len(items):
            item = items[idx]

            if item["kind"] == "table":
                header, lines = table_markdown(item["tab"], page_words)
                if not lines:
                    idx += 1
                    continue
                # A table split across a page break repeats its header row.
                if first_on_page and header and header == last_header and body:
                    body.extend(lines[2:])
                else:
                    if body and body[-1] != "":
                        body.append("")
                    body.extend(lines)
                last_header = header
                first_on_page = False
                idx += 1
                continue

            if item["kind"] == "image":
                bbox = item["block"]["bbox"]
                if bbox[2] - bbox[0] < 24 or bbox[3] - bbox[1] < 24:
                    # a broken-image icon: the artwork is absent from the PDF
                    body += ["", "*(Diagram missing from the source PDF.)*", ""]
                    first_on_page = False
                    idx += 1
                    continue
                img_count += 1
                name = f"figure-{img_count:02d}.png"
                if assets_dir:
                    os.makedirs(assets_dir, exist_ok=True)
                    pix = page.get_pixmap(clip=fitz.Rect(item["block"]["bbox"]), dpi=200)
                    pix.save(os.path.join(assets_dir, name))
                    rel = os.path.join(os.path.basename(assets_dir), name)
                    body += ["", f"![Figure {img_count}]({rel})", ""]
                last_header = None
                first_on_page = False
                idx += 1
                continue

            # headings
            size = item["size"]
            is_bold = any(s["flags"] & 16 for s in item["spans"])
            if is_bold and size >= H3_SIZE and not item["mono"]:
                level = 1 if size >= H1_SIZE else 2 if size >= H2_SIZE else 3
                title = item["text"]
                # headings that wrap onto a second line
                while (idx + 1 < len(items) and items[idx + 1]["kind"] == "line"
                       and abs(items[idx + 1].get("size", 0) - size) < 0.2
                       and any(s["flags"] & 16 for s in items[idx + 1]["spans"])
                       and items[idx + 1]["y"] - item["bottom"] < 8):
                    idx += 1
                    title += " " + items[idx]["text"]
                title = clean(title)
                slug = slugify(title)
                toc.append((level, title, slug))
                body += ["", "#" * (level + 1) + " " + title, ""]
                last_header = None
                first_on_page = False
                idx += 1
                continue

            # code blocks
            if item["mono"]:
                group = [item]
                while (idx + 1 < len(items) and items[idx + 1]["kind"] == "line"
                       and items[idx + 1]["mono"]
                       and items[idx + 1]["y"] - group[-1]["bottom"] < 14):
                    idx += 1
                    group.append(items[idx])
                if body and body[-1] != "":
                    body.append("")
                body.extend(code_fence(group))
                body.append("")
                last_header = None
                first_on_page = False
                idx += 1
                continue

            # paragraphs / list items
            text = render_line(item)
            list_item = bool(re.match(r"^\d+\.\s|^[•·–-]\s", text))
            key_value = bool(META_LABEL.match(text))
            note = bool(re.match(r"^\*{0,2}NOTE?\*{0,2}\s*:", text, re.I))
            if list_item:
                text = re.sub(r"^[•·–-]\s", "- ", text)
                body += ["", text]
            elif key_value:
                body += ["", f"- {text}"]
            elif note:
                body += ["", text]
            else:
                gap_ok = (body and body[-1] not in ("",) and not body[-1].startswith(("#", "|", "```"))
                          and idx > 0 and items[idx - 1]["kind"] == "line"
                          and item["y"] - items[idx - 1].get("bottom", item["y"]) < 10)
                if gap_ok:
                    body[-1] = (body[-1] + " " + text).strip()
                else:
                    body += ["", text]
            last_header = None
            first_on_page = False
            idx += 1

    # assemble
    out = ["# UniFi Access API Reference", "",
           f"Converted from `{os.path.basename(path)}` "
           f"({doc.page_count} pages) — source: "
           "<https://assets.identity.ui.com/unifi-access/api_reference.pdf>", "",
           "## Contents", ""]
    for level, title, slug in toc:
        if level <= 2:
            out.append("  " * (level - 1) + f"- [{title}](#{slug})")
    out.append("")
    out.extend(body)

    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    with open(out_path, "w") as fh:
        fh.write(text.rstrip() + "\n")
    print(f"wrote {out_path}: {len(text.splitlines())} lines, "
          f"{len(toc)} headings, {img_count} figures")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("out")
    ap.add_argument("--assets", default=None)
    args = ap.parse_args()
    convert(args.pdf, args.out, args.assets)


if __name__ == "__main__":
    sys.exit(main())
