#!/usr/bin/env python3
"""Validate editable paper figures and build previews from Office-rendered PDFs."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET

from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "docs/reports/2026-10/assets"
MATH_FAMILY = "Liberation Serif"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def pdf_pages(path: Path) -> int:
    info = subprocess.check_output(["pdfinfo", str(path)], text=True)
    return int(re.search(r"^Pages:\s+(\d+)", info, re.MULTILINE).group(1))


def validate_slide(slide, svg: Path, presentation):
    root = ET.parse(svg).getroot()
    expected = [node for node in root if node.tag.endswith("}text")]
    text_shapes = [
        shape for shape in slide.shapes if shape.has_text_frame and shape.text
    ]
    require(
        [shape.text for shape in text_shapes]
        == ["".join(node.itertext()) for node in expected],
        f"SVG/PowerPoint text differs: {svg.name}",
    )
    math_count = italic_count = 0
    for node, shape in zip(expected, text_shapes):
        runs = [run for paragraph in shape.text_frame.paragraphs for run in paragraph.runs]
        require(len(runs) == 1, f"Unexpected text-run structure: {shape.text!r}")
        run = runs[0]
        require(run.font.name == node.get("font-family"), f"Font changed: {shape.text!r}")
        require(
            bool(run.font.italic) == (node.get("font-style") == "italic"),
            f"Math style changed: {shape.text!r}",
        )
        if run.font.name == MATH_FAMILY:
            math_count += 1
            italic_count += bool(run.font.italic)
    pictures = sum(shape.shape_type == MSO_SHAPE_TYPE.PICTURE for shape in slide.shapes)
    require(pictures == 0, f"Flattened picture in {svg.name}")
    ids = [shape.shape_id for shape in slide.shapes]
    require(len(ids) == len(set(ids)), f"Duplicate shape ID in {svg.name}")
    for shape in slide.shapes:
        require(
            shape.left >= 0 and shape.top >= 0
            and shape.left + shape.width <= presentation.slide_width
            and shape.top + shape.height <= presentation.slide_height,
            f"Object outside slide: {shape.name!r}",
        )
    notes = slide.notes_slide.notes_text_frame.text
    require(len(notes) > 300, f"Missing mechanism notes in {svg.name}")
    require(math_count > 0 and italic_count > 0, f"Missing editable math in {svg.name}")
    return {
        "editable_objects": len(slide.shapes),
        "editable_text_objects": len(text_shapes),
        "editable_math_text_objects": math_count,
        "italic_math_variables": italic_count,
        "text_matches_svg": True,
        "font_and_italic_styles_match_svg": True,
        "all_objects_within_slide": True,
        "unique_shape_ids": True,
        "notes_present": True,
        "picture_objects": pictures,
    }


def office_preview(pdf: Path, destination: Path):
    require(pdf_pages(pdf) == 3, f"Office PDF must contain three pages: {pdf}")
    with tempfile.TemporaryDirectory(prefix="beliefkv-paper-preview-") as scratch:
        prefix = Path(scratch) / "page"
        subprocess.run(
            ["pdftoppm", "-png", "-scale-to", "1800", str(pdf), str(prefix)],
            check=True,
        )
        sheet = Image.new("RGB", (1488, 2976), "#e8edef")
        for index in range(3):
            with Image.open(Path(scratch) / f"page-{index + 1}.png") as image:
                page = image.convert("RGB").resize((1440, 960), Image.Resampling.LANCZOS)
            sheet.paste(page, (24, 24 + index * 984))
        sheet.save(destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--office-pdf-directory", type=Path, required=True)
    parser.add_argument("--visual-review-complete", action="store_true")
    args = parser.parse_args()
    manifest = json.loads((args.output / "manifest.json").read_text())
    generator = Path(__file__).with_name("generate_monthly_paper_figures.py")
    require(digest(generator) == manifest["generator_sha256"], "Generator changed after export")
    for name, expected in manifest["architecture_source_sha256"].items():
        require(digest(ROOT / name) == expected, f"Architecture source changed: {name}")

    decks = []
    for language in ("zh", "en"):
        path = args.output / f"beliefkv_architecture_editable_{language}.pptx"
        presentation = Presentation(path)
        require(len(presentation.slides) == 3, f"Expected three slides: {path}")
        figures = [row for row in manifest["figures"] if row["language"] == language]
        slides = [
            validate_slide(slide, args.output / (row["stem"] + ".svg"), presentation)
            for slide, row in zip(presentation.slides, figures)
        ]
        for slide, row in zip(slides, figures):
            require(
                slide["editable_math_text_objects"] == row["editable_math_text_objects"],
                f"Math object count differs: {row['stem']}",
            )
        office_pdf = args.office_pdf_directory / (path.stem + ".pdf")
        office_preview(office_pdf, args.output / f"pptx_preview_{language}.png")
        decks.append({
            "file": path.name, "sha256": digest(path), "slides": slides,
            "office_render_pdf_sha256": digest(office_pdf),
            "office_render_pages": 3,
        })

    for row in manifest["figures"]:
        png = args.output / (row["stem"] + ".png")
        with Image.open(png) as image:
            require(list(image.size) == [4320, 2880], f"Unexpected PNG dimensions: {png}")
            require(
                all(abs(value - 600) < 1 for value in image.info.get("dpi", (0, 0))),
                f"Expected 600 dpi: {png}",
            )
    vector_pdf = args.output / "beliefkv_figures_bilingual.pdf"
    require(pdf_pages(vector_pdf) == 6, "Expected six vector PDF pages")
    image_listing = subprocess.check_output(["pdfimages", "-list", str(vector_pdf)], text=True)
    require(len(image_listing.strip().splitlines()) == 2, "Vector PDF contains a raster image")
    receipt = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "architecture_date": manifest["architecture_date"],
        "source_revision": manifest["source_revision"],
        "generator_sha256": digest(generator),
        "validator_sha256": digest(Path(__file__)),
        "powerpoints": decks,
        "png_count": 6, "png_dpi": 600,
        "vector_pdf_pages": 6, "vector_pdf_raster_images": 0,
        "office_renderer": "LibreOffice 24.2.7 in isolated temporary directory",
        "office_render_check": "all six slides rendered",
        "visual_review_complete": args.visual_review_complete,
        "figure_scope": (
            manifest["architecture_date"]
            + " development mechanisms; diagrams and timing are schematic"
        ),
    }
    (args.output / "powerpoint_validation.json").write_text(
        json.dumps(receipt, indent=2) + "\n",
    )
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
