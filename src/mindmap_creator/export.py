"""Export renderers for ``mindmap.v1`` artifacts: Markdown, SVG, and PNG.

The mermaid parser here is a line-for-line port of the JS parser in
``view/index.html`` so exported images match what the in-app view shows.
Keep the two in sync when either changes.
"""

from __future__ import annotations

import io
import math
import os
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Tuple
from xml.sax.saxutils import escape

from loguru import logger
from open_notebook_creator_sdk import CreationFile
from PIL import Image, ImageDraw, ImageFont

# ---- geometry & palette (light theme, mirrors view/index.html CSS) ----------
FONT_SIZE = 14
ROOT_FONT_SIZE = 15
PAD_X = 12
ROOT_PAD_X = 16
PAD_Y = 7
H_GAP = 48
V_GAP = 12
MARGIN = 24
CORNER_RADIUS = 8
MAX_LABEL_CHARS = 60
DOT_SIZE = 8  # super-root marker when the map has no single root
# SVG is rendered with the viewer's system fonts while we measure with
# Pillow's embedded font — pad widths so real fonts never clip.
TEXT_SLACK = 1.08

BG = "#ffffff"
CARD = "#fafafa"
BORDER = "#e4e4e7"
CONNECTOR = "#d4d4d8"
FG = "#18181b"
ACCENT = "#4f46e5"
ACCENT_FG = "#ffffff"

FONT_FAMILY = "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"

PNG_SCALE = 2
PNG_SOFT_LIMIT = 8000  # px: above this at 2x, fall back to 1x
PNG_HARD_LIMIT = 16000  # px: refuse to rasterize beyond this


# ---- mermaid `mindmap` parser (port of view/index.html) ----------------------


@dataclass
class Node:
    label: Optional[str]  # None only for the synthetic super-root
    children: List["Node"] = field(default_factory=list)


_ICON_RE = re.compile(r"::icon\([^)]*\)\s*$")
_CLASS_RE = re.compile(r":::\S+\s*$")
# Same precedence as the JS if/else chain: first match wins.
_SHAPE_RES = (
    re.compile(r"^[A-Za-z0-9_]*\(\((.*)\)\)$"),
    re.compile(r"^\(\((.*)\)\)$"),
    re.compile(r"^\{\{(.*)\}\}$"),
    re.compile(r"^\[(.*)\]$"),
    re.compile(r"^\((.*)\)$"),
)


def clean_label(text: str) -> str:
    """Strip mermaid node-shape decorations and class/icon suffixes."""
    s = str(text or "").strip()
    s = _ICON_RE.sub("", s).strip()
    s = _CLASS_RE.sub("", s).strip()
    for shape in _SHAPE_RES:
        m = shape.match(s)
        if m:
            s = m.group(1)
            break
    return s.strip()


def parse_mindmap(syntax: str) -> Optional[Node]:
    """Parse a mermaid ``mindmap`` block into a tree using indentation depth."""
    lines = str(syntax or "").replace("\r\n", "\n").split("\n")
    rows: List[Tuple[int, str]] = []
    for line in lines:
        if not line.strip():
            continue
        if not rows and line.strip() == "mindmap":
            continue
        indent = len(line) - len(line.lstrip())
        label = clean_label(line.strip())
        if not label:
            continue
        rows.append((indent, label))
    if not rows:
        return None

    # Each row is a child of the nearest preceding row with a strictly smaller
    # indent. A synthetic super-root holds top-level rows.
    super_root = Node(label=None)
    stack: List[Tuple[int, Node]] = [(-1, super_root)]
    for indent, label in rows:
        while len(stack) > 1 and stack[-1][0] >= indent:
            stack.pop()
        node = Node(label=label)
        stack[-1][1].children.append(node)
        stack.append((indent, node))
    if len(super_root.children) == 1:
        return super_root.children[0]
    return super_root


# ---- layout ------------------------------------------------------------------


@dataclass
class LayoutNode:
    label: Optional[str]
    x: float
    y: float
    w: float
    h: float
    depth: int
    is_root: bool
    sub_h: float = 0.0
    children: List["LayoutNode"] = field(default_factory=list)


@dataclass
class Layout:
    root: LayoutNode
    width: float
    height: float


def _font(size: int) -> ImageFont.ImageFont:
    # Pillow >= 10.1: the embedded default font is scalable.
    return ImageFont.load_default(size=size)


def _truncate(label: str) -> str:
    if len(label) > MAX_LABEL_CHARS:
        return label[: MAX_LABEL_CHARS - 1] + "…"
    return label


def layout_tree(tree: Node) -> Layout:
    """Left-to-right tiered tree layout: root in column 0, children fan right."""
    body_font = _font(FONT_SIZE)
    root_font = _font(ROOT_FONT_SIZE)
    body_h = sum(body_font.getmetrics()) + 2 * PAD_Y
    root_h = sum(root_font.getmetrics()) + 2 * PAD_Y

    def measure(node: Node, depth: int, is_root: bool) -> LayoutNode:
        if node.label is None:
            label, w, h = None, float(DOT_SIZE), float(DOT_SIZE)
        else:
            label = _truncate(node.label)
            font = root_font if is_root else body_font
            pad_x = ROOT_PAD_X if is_root else PAD_X
            w = font.getlength(label) * TEXT_SLACK + 2 * pad_x
            h = float(root_h if is_root else body_h)
        ln = LayoutNode(label=label, x=0.0, y=0.0, w=w, h=h, depth=depth, is_root=is_root)
        ln.children = [measure(c, depth + 1, False) for c in node.children]
        return ln

    root = measure(tree, 0, tree.label is not None)

    col_w: Dict[int, float] = {}
    for n in _walk(root):
        col_w[n.depth] = max(col_w.get(n.depth, 0.0), n.w)
    max_depth = max(col_w)
    col_x: Dict[int, float] = {}
    x = float(MARGIN)
    for d in range(max_depth + 1):
        col_x[d] = x
        x += col_w[d] + H_GAP
    width = col_x[max_depth] + col_w[max_depth] + MARGIN

    def subtree_h(n: LayoutNode) -> float:
        if n.children:
            kids = sum(subtree_h(c) for c in n.children) + V_GAP * (len(n.children) - 1)
            n.sub_h = max(n.h, kids)
        else:
            n.sub_h = n.h
        return n.sub_h

    total_h = subtree_h(root)

    def place(n: LayoutNode, y0: float) -> None:
        n.x = col_x[n.depth]
        n.y = y0 + (n.sub_h - n.h) / 2
        if n.children:
            kids_h = sum(c.sub_h for c in n.children) + V_GAP * (len(n.children) - 1)
            cy = y0 + (n.sub_h - kids_h) / 2
            for c in n.children:
                place(c, cy)
                cy += c.sub_h + V_GAP

    place(root, float(MARGIN))
    return Layout(root=root, width=width, height=total_h + 2 * MARGIN)


def _walk(n: LayoutNode) -> Iterator[LayoutNode]:
    yield n
    for c in n.children:
        yield from _walk(c)


def _edges(n: LayoutNode) -> Iterator[Tuple[LayoutNode, LayoutNode]]:
    for c in n.children:
        yield n, c
        yield from _edges(c)


# ---- renderers ----------------------------------------------------------------


def render_markdown(data: dict) -> str:
    title = (data.get("title") or "Mindmap").strip() or "Mindmap"
    lines = [f"# {title}", ""]
    description = (data.get("description") or "").strip()
    if description:
        lines += [description, ""]
    lines += ["```mermaid", data.get("mermaid_syntax") or "", "```", ""]
    return "\n".join(lines)


def render_svg(layout: Layout) -> str:
    w, h = math.ceil(layout.width), math.ceil(layout.height)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
        f'width="{w}" height="{h}" font-family="{FONT_FAMILY}">',
        f'<rect width="{w}" height="{h}" fill="{BG}"/>',
    ]
    for p, c in _edges(layout.root):
        x1, y1 = p.x + p.w, p.y + p.h / 2
        x2, y2 = c.x, c.y + c.h / 2
        mx = (x1 + x2) / 2
        parts.append(
            f'<path d="M {x1:.1f} {y1:.1f} C {mx:.1f} {y1:.1f}, {mx:.1f} {y2:.1f}, '
            f'{x2:.1f} {y2:.1f}" fill="none" stroke="{CONNECTOR}" stroke-width="1.5"/>'
        )
    for n in _walk(layout.root):
        cy = n.y + n.h / 2
        if n.label is None:
            parts.append(
                f'<circle cx="{n.x + n.w / 2:.1f}" cy="{cy:.1f}" r="{n.w / 2:.1f}" fill="{ACCENT}"/>'
            )
        elif n.is_root:
            parts.append(
                f'<rect x="{n.x:.1f}" y="{n.y:.1f}" width="{n.w:.1f}" height="{n.h:.1f}" '
                f'rx="{n.h / 2:.1f}" fill="{ACCENT}"/>'
            )
            parts.append(
                f'<text x="{n.x + ROOT_PAD_X:.1f}" y="{cy:.1f}" dominant-baseline="central" '
                f'font-size="{ROOT_FONT_SIZE}" font-weight="600" fill="{ACCENT_FG}">'
                f"{escape(n.label)}</text>"
            )
        else:
            parts.append(
                f'<rect x="{n.x:.1f}" y="{n.y:.1f}" width="{n.w:.1f}" height="{n.h:.1f}" '
                f'rx="{CORNER_RADIUS}" fill="{CARD}" stroke="{BORDER}"/>'
            )
            parts.append(
                f'<text x="{n.x + PAD_X:.1f}" y="{cy:.1f}" dominant-baseline="central" '
                f'font-size="{FONT_SIZE}" fill="{FG}">{escape(n.label)}</text>'
            )
    parts.append("</svg>")
    return "\n".join(parts)


def _bezier_points(
    x1: float, y1: float, x2: float, y2: float, steps: int = 24
) -> List[Tuple[float, float]]:
    """Sample the same cubic the SVG uses (controls at the horizontal midpoint)."""
    mx = (x1 + x2) / 2
    pts = []
    for i in range(steps + 1):
        t = i / steps
        mt = 1 - t
        bx = mt**3 * x1 + 3 * mt**2 * t * mx + 3 * mt * t**2 * mx + t**3 * x2
        by = mt**3 * y1 + 3 * mt**2 * t * y1 + 3 * mt * t**2 * y2 + t**3 * y2
        pts.append((bx, by))
    return pts


def render_png(layout: Layout) -> bytes:
    s = PNG_SCALE
    if max(layout.width, layout.height) * s > PNG_SOFT_LIMIT:
        s = 1
    if max(layout.width, layout.height) * s > PNG_HARD_LIMIT:
        raise ValueError("mindmap too large to rasterize as PNG")

    img = Image.new("RGB", (math.ceil(layout.width * s), math.ceil(layout.height * s)), BG)
    draw = ImageDraw.Draw(img)
    body_font = _font(FONT_SIZE * s)
    root_font = _font(ROOT_FONT_SIZE * s)

    for p, c in _edges(layout.root):
        pts = _bezier_points(
            (p.x + p.w) * s, (p.y + p.h / 2) * s, c.x * s, (c.y + c.h / 2) * s
        )
        draw.line(pts, fill=CONNECTOR, width=max(1, round(1.5 * s)), joint="curve")

    for n in _walk(layout.root):
        box = (n.x * s, n.y * s, (n.x + n.w) * s, (n.y + n.h) * s)
        cy = (n.y + n.h / 2) * s
        if n.label is None:
            draw.ellipse(box, fill=ACCENT)
        elif n.is_root:
            draw.rounded_rectangle(box, radius=n.h * s / 2, fill=ACCENT)
            draw.text(
                ((n.x + ROOT_PAD_X) * s, cy), n.label, font=root_font, fill=ACCENT_FG, anchor="lm"
            )
        else:
            draw.rounded_rectangle(
                box, radius=CORNER_RADIUS * s, fill=CARD, outline=BORDER, width=max(1, s)
            )
            draw.text(((n.x + PAD_X) * s, cy), n.label, font=body_font, fill=FG, anchor="lm")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---- orchestration --------------------------------------------------------------


def slugify(title: object) -> str:
    s = unicodedata.normalize("NFKD", str(title or "")).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s or "mindmap"


def build_export_files(data: dict, output_dir: str) -> Tuple[List[CreationFile], List[str]]:
    """Write Markdown/SVG/PNG exports into ``output_dir``.

    Never raises: each format is attempted independently and failures become
    warnings, so export problems can't fail an otherwise successful generation.
    """
    files: List[CreationFile] = []
    warnings: List[str] = []
    stem = slugify(data.get("title"))

    def attach(filename: str, content_type: str, label: str, payload) -> None:
        path = os.path.join(output_dir, filename)
        if isinstance(payload, bytes):
            with open(path, "wb") as fh:
                fh.write(payload)
        else:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(payload)
        files.append(
            CreationFile(filename=filename, content_type=content_type, path=filename, label=label)
        )

    try:
        attach(f"{stem}.md", "text/markdown", "Markdown", render_markdown(data))
    except Exception as e:
        logger.warning(f"mindmaps: Markdown export failed: {e}")
        warnings.append(f"Markdown export failed: {e}")

    tree = parse_mindmap(data.get("mermaid_syntax") or "")
    if tree is None:
        warnings.append("Could not parse the mindmap syntax; SVG/PNG export skipped.")
        return files, warnings

    try:
        lay = layout_tree(tree)
    except Exception as e:
        logger.warning(f"mindmaps: layout failed: {e}")
        warnings.append(f"SVG/PNG export failed: {e}")
        return files, warnings

    try:
        attach(f"{stem}.svg", "image/svg+xml", "SVG", render_svg(lay))
    except Exception as e:
        logger.warning(f"mindmaps: SVG export failed: {e}")
        warnings.append(f"SVG export failed: {e}")

    try:
        attach(f"{stem}.png", "image/png", "PNG", render_png(lay))
    except Exception as e:
        logger.warning(f"mindmaps: PNG export failed: {e}")
        warnings.append(f"PNG export failed: {e}")

    return files, warnings
