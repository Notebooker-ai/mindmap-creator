"""Unit tests for the export module (parser port, layout, renderers, files)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from io import BytesIO

import pytest
from mindmap_creator import export
from mindmap_creator.export import (
    build_export_files,
    clean_label,
    layout_tree,
    parse_mindmap,
    render_markdown,
    render_png,
    render_svg,
    slugify,
)
from PIL import Image

_SYNTAX = "mindmap\n  root((Topic))\n    Branch A\n      Leaf 1\n      Leaf 2\n    Branch B"
_DATA = {"title": "Topic", "mermaid_syntax": _SYNTAX, "description": "A map"}


# ---- clean_label (must match the JS cleanLabel in view/index.html) -----------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("root((Topic))", "Topic"),
        ("((Topic))", "Topic"),
        ("{{Topic}}", "Topic"),
        ("[Topic]", "Topic"),
        ("(Topic)", "Topic"),
        ("Plain", "Plain"),
        ("Label ::icon(fa fa-book)", "Label"),
        ("Label :::urgent", "Label"),
        ("((X))::icon(fa fa-x)", "X"),
        ("[Y]:::highlight", "Y"),
    ],
)
def test_clean_label(raw, expected):
    assert clean_label(raw) == expected


# ---- parse_mindmap ------------------------------------------------------------


def test_parse_single_root():
    tree = parse_mindmap(_SYNTAX)
    assert tree.label == "Topic"
    assert [c.label for c in tree.children] == ["Branch A", "Branch B"]
    assert [c.label for c in tree.children[0].children] == ["Leaf 1", "Leaf 2"]


def test_parse_multiple_top_level_gets_super_root():
    tree = parse_mindmap("mindmap\nA\nB")
    assert tree.label is None
    assert [c.label for c in tree.children] == ["A", "B"]


def test_parse_skips_blanks_and_crlf_and_tabs():
    tree = parse_mindmap("mindmap\r\n\r\n\tRoot\r\n\t\tKid")
    assert tree.label == "Root"
    assert tree.children[0].label == "Kid"


def test_parse_dedent_past_grandparent():
    # Kid dedents to the same indent as Root's parent level: stack semantics
    # make it a sibling of Root under the super-root.
    tree = parse_mindmap("mindmap\n  Root\n    Child\n  Other")
    assert tree.label is None
    assert [c.label for c in tree.children] == ["Root", "Other"]


def test_parse_empty_or_garbage_is_none():
    assert parse_mindmap("") is None
    assert parse_mindmap("   \n\n") is None
    assert parse_mindmap("mindmap\n") is None


def test_leading_mindmap_line_only_dropped_first():
    tree = parse_mindmap("mindmap\n  Root\n    mindmap")
    assert tree.label == "Root"
    assert tree.children[0].label == "mindmap"


# ---- renderers ------------------------------------------------------------------


def test_render_markdown_contains_fenced_source():
    md = render_markdown(_DATA)
    assert md.startswith("# Topic")
    assert "A map" in md
    fence = md.split("```mermaid\n", 1)[1].split("\n```", 1)[0]
    assert fence == _SYNTAX


def test_render_svg_is_valid_xml_with_labels():
    svg = render_svg(layout_tree(parse_mindmap(_SYNTAX)))
    root = ET.fromstring(svg)
    assert root.tag.endswith("svg")
    assert root.get("viewBox")
    for label in ("Topic", "Branch A", "Leaf 2"):
        assert label in svg
    assert export.ACCENT in svg  # root pill uses the accent color


def test_render_svg_escapes_labels():
    svg = render_svg(layout_tree(parse_mindmap("mindmap\n  a <b> & c")))
    assert "a &lt;b&gt; &amp; c" in svg
    ET.fromstring(svg)


def test_render_png_magic_and_dimensions():
    lay = layout_tree(parse_mindmap(_SYNTAX))
    png = render_png(lay)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    img = Image.open(BytesIO(png))
    assert 100 <= img.width <= 8000 and 50 <= img.height <= 8000
    # 2x scale of the layout canvas
    assert abs(img.width - 2 * lay.width) <= 2
    assert abs(img.height - 2 * lay.height) <= 2


def test_render_super_root_dot():
    svg = render_svg(layout_tree(parse_mindmap("mindmap\nA\nB")))
    assert "<circle" in svg


# ---- slugify ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("My Map! (v2)", "my-map-v2"),
        ("", "mindmap"),
        (None, "mindmap"),
        ("Résumé Überblick", "resume-uberblick"),
        ("../../etc/passwd", "etc-passwd"),
    ],
)
def test_slugify(title, expected):
    assert slugify(title) == expected


# ---- build_export_files ------------------------------------------------------------


def test_build_export_files_writes_all_three(tmp_path):
    files, warnings = build_export_files(_DATA, str(tmp_path))
    assert warnings == []
    assert [(f.filename, f.content_type, f.label) for f in files] == [
        ("topic.md", "text/markdown", "Markdown"),
        ("topic.svg", "image/svg+xml", "SVG"),
        ("topic.png", "image/png", "PNG"),
    ]
    for f in files:
        assert not f.path.startswith("/") and ".." not in f.path
        assert (tmp_path / f.path).is_file()


def test_unparseable_syntax_still_exports_markdown(tmp_path):
    data = {"title": "T", "mermaid_syntax": "   "}
    files, warnings = build_export_files(data, str(tmp_path))
    assert [f.filename for f in files] == ["t.md"]
    assert len(warnings) == 1
    assert "skipped" in warnings[0]


def test_png_failure_is_warning_not_exception(tmp_path, monkeypatch):
    def boom(_):
        raise RuntimeError("no raster today")

    monkeypatch.setattr(export, "render_png", boom)
    files, warnings = build_export_files(_DATA, str(tmp_path))
    assert [f.filename for f in files] == ["topic.md", "topic.svg"]
    assert any("PNG export failed" in w for w in warnings)
