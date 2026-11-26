#!/usr/bin/env python3
"""Export the four connected BeliefKV lifecycle figures at ICML print widths."""

import argparse
from html import escape
from io import BytesIO
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

import cairosvg
from PIL import Image

from generate_monthly_paper_figures import MATH_FAMILY, ROOT, PaperFigure, PowerPointExporter


OUTPUT = ROOT / "docs/figures/icml"
WHITE, INK, RULE = "#ffffff", "#203036", "#a4b1b1"
LIGHT, FAINT = "#e1e7e7", "#f4f7f7"
BLUE, PALE_BLUE = "#355b84", "#dce6ee"
TEAL, PALE_TEAL = "#23665f", "#e3eeea"
COPPER, PEACH, PALE_PEACH = "#954922", "#e9b586", "#faead8"
BUSY = "#64747b"
PAGE_NUMBERS = (1, 2, 3, 4)
PREFIX = frozenset((1, 2))
TAIL = frozenset((3, 4))
STABLE = PREFIX | TAIL
FULL_WIDTH, COLUMN = 486, 234


class Art(PaperFigure):
    pdf_sans_family = "Nimbus Sans"

    def __init__(self, name, title, width, height, caption):
        super().__init__(name, title, height, "en", width=width)
        self.family = "Liberation Sans"
        self.caption = caption
        self.parts = ['<rect width="100%" height="100%" fill="#ffffff"/>']

    def label(self, x, y, value, size=8.6, *, align="middle", bold=False,
              serif=False, italic=False, color=INK):
        """Anchor on the optical midline and use the same center in Office."""
        family = MATH_FAMILY if serif else self.family
        weight = 700 if bold else 400
        width = self.measure(value, size, weight, family, italic)[0]
        x += {"start": width / 2, "end": -width / 2, "middle": 0}[align]
        self.text(x, y + size * .34, value, size, color, weight, "middle", family, italic)
        element = ET.fromstring(self.parts[-1])
        element.set("fill", color)
        self.parts[-1] = ET.tostring(element, encoding="unicode")

    def arrow(self, points, *, color=INK, width=.9, head=4.5, dashed=False):
        self.path(points, color, width, "2.4 2.0" if dashed else None)
        self.arrowhead(*points[-2:], color, width, head, dashed)

    def arrowhead(self, previous, end, color, width, head, dashed=False):
        px, py = previous
        x, y = end
        dx, dy = x - px, y - py
        length = (dx * dx + dy * dy) ** .5
        ux, uy = dx / length, dy / length
        base_x, base_y = x - head * ux, y - head * uy
        vertices = [(base_x - .46 * head * uy, base_y + .46 * head * ux),
                    (x, y), (base_x + .46 * head * uy, base_y - .46 * head * ux)]
        if dashed:
            self.path(vertices, color, width)
        else:
            self.polygon(vertices, color, color, .35)

    def transfer(self, start, control1, control2, end):
        path = " ".join(f"{x},{y}" for x, y in (control1, control2, end))
        self.parts.append(
            f'<path d="M {start[0]},{start[1]} C {path}" fill="none" '
            f'stroke="{COPPER}" stroke-width="1.8" stroke-linecap="round"/>'
        )
        self.arrowhead(control2, end, COPPER, 1.8, 4.8)

    def rule(self, x1, y, x2):
        self.line(x1, y, x2, y, LIGHT, .7)

    def brace(self, x1, x2, y, *, below=True, color=RULE):
        notch = -3 if below else 3
        self.path(((x1, y + notch), (x1, y), (x2, y), (x2, y + notch)), color, .85)

    def pages(self, x, y, present, *, pitch=12.2, height=12, ids=PAGE_NUMBERS, numbered=True):
        """One fixed column per native extent, including when its tier is missing it."""
        for i, page in enumerate(ids):
            left = x + i * pitch
            active = page in present
            fill, stroke = (PEACH, COPPER) if page in TAIL else (PALE_BLUE, BLUE)
            self.rect(left, y, pitch - 1.5, height, fill if active else WHITE,
                      stroke if active else RULE, .8, .7, None if active else "1.8 1.4")
            if active and numbered:
                self.label(left + (pitch - 1.5) / 2, y + height / 2, str(page),
                           8.1, bold=True, color=stroke)

    def bank(self, x, y, host):
        edge, fill = (TEAL, PALE_TEAL) if host else (BLUE, PALE_BLUE)
        self.rect(x, y - 6.5, 30, 13, fill, edge, .7, 1.4)
        self.label(x + 15, y, "Host" if host else "HBM", 8.4, bold=True, color=edge)

    def endpoint(self, x, y, kind):
        if kind == "eos":
            self.polygon(((x, y - 3.3), (x + 3.3, y), (x, y + 3.3), (x - 3.3, y)),
                         TEAL, TEAL, .7)
        elif kind == "submit":
            self.rect(x - 2.3, y - 2.3, 4.6, 4.6, WHITE, INK, 1, 0)
        else:
            self.circle(x, y, 2.35, INK, INK, .6)

    def save_to(self, directory):
        path = directory / self.filename
        for text, x, y, w, h in self.text_bounds:
            if min(x, y) < 0 or x + w > self.width or y + h > self.height:
                raise ValueError(f"{self.filename}: {text!r} outside print canvas")
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.width/72:g}in" '
            f'height="{self.height/72:.8f}in" viewBox="0 0 {self.width} {self.height}" '
            'role="img" aria-labelledby="title caption">\n'
            f'<title id="title">{escape(self.title)}</title>\n'
            f'<desc id="caption">{escape(self.caption)}</desc>\n'
            + "\n".join(self.parts) + "\n</svg>\n"
        )
        path.write_text(svg, encoding="utf-8")
        root = ET.fromstring(svg)
        for text in root.findall("{http://www.w3.org/2000/svg}text"):
            text.set("font-family", "Nimbus Roman" if text.get("font-family") == MATH_FAMILY
                     else self.pdf_sans_family)
        cairosvg.svg2pdf(bytestring=ET.tostring(root), output_width=self.width,
                        output_height=self.height, dpi=72, write_to=str(path.with_suffix(".pdf")))
        raster = cairosvg.svg2png(bytestring=svg.encode(), output_width=round(self.width * 600/72),
                                  output_height=round(self.height * 600/72))
        with Image.open(BytesIO(raster)) as img:
            img.convert("RGB").save(path.with_suffix(".png"), dpi=(600, 600))


def overview():
    from migragate_design_overview import make_figure

    return make_figure()


def capacity_cell(f, x, y, w, state):
    colors = {
        "used": (BUSY, BUSY), "free": (WHITE, RULE), "reclaim": (PALE_TEAL, TEAL),
        "reserved": (LIGHT, BUSY), "child": (PALE_BLUE, BLUE),
    }
    fill, edge = colors[state]
    f.rect(x, y, w, 10, fill, edge, .7, .65)
    if state in ("reclaim", "reserved"):
        for dx in (1.5, w/2):
            f.line(x+dx, y+8.3, x+dx+3, y+1.7, edge, .65)


def materialization():
    f = Art("02_first_kv_materialization", "Pressure-aware first KV materialization",
            COLUMN, 152, "In this example the Host contributes three free and two safely "
            "reclaimable units, and HBM contributes three free units. After charging three "
            "units for growth, demand reserves, and commitments, five units remain for "
            "all first root and child requests. Under pressure an observed JOIN-blocking "
            "child takes three. A root requiring four waits. A permit is rechecked before "
            "native first prefill. All units are illustrative.")
    f.bank(8, 12, True)
    f.bank(143, 12, False)
    for x, states in ((8, ["used"]*3 + ["reclaim"]*2 + ["free"]*3),
                      (143, ["used"]*2 + ["free"]*3)):
        for i, state in enumerate(states):
            capacity_cell(f, x + i*15.9, 23, 13.9, state)
    for x1, x2, label in ((8, 54, "Live"), (56, 85, "Reclaim"),
                          (88, 133, "Free"), (143, 173, "Used"), (175, 221, "Free")):
        f.brace(x1, x2, 37)
        f.label((x1+x2)/2, 43.5, label, 8)
    f.rule(7, 52, 227)

    f.label(8, 63, "Shared headroom", 10.3, align="start", serif=True, bold=True)
    f.label(219, 63, "8 units", 8.3, align="end", italic=True)
    for i in range(8):
        capacity_cell(f, 9 + i*27, 72, 24.8, "reserved" if i < 3 else "free")
    f.brace(9, 88, 87)
    f.brace(90, 223, 87)
    f.label(48, 94, "Growth + commitments", 8.0)
    f.label(157, 94, "5 for first prefill", 8.6, bold=True, color=TEAL)
    f.rule(7, 103, 227)

    for y, name, cost, outcome, color in (
        (117, "JOIN child", 3, "Permit", TEAL), (139, "New root", 4, "Hold", COPPER),
    ):
        f.label(8, y, name, 8.8, align="start", bold=True)
        for i in range(cost):
            capacity_cell(f, 70+i*11, y-5, 9.4, "child" if y == 117 else "free")
        f.arrow(((118, y), (137, y)), color=color, dashed=True)
        f.label(146, y, outcome, 8.8, align="start", bold=True, color=color)
    f.label(225, 117, "2 left", 8.3, align="end")
    f.label(225, 139, "4 > 2", 8.3, align="end")
    return f


def backup_offload():
    f = Art("03_incremental_backup_offload", "Lifecycle-aware incremental backup and on-demand offload",
            COLUMN, 143, "P's stable checkpoint contains extents 1 through 4. Extents 1 and "
            "2 already have valid Host copies. PREPARE_HOST at SPAWN or WAIT_JOIN copies "
            "only 3 and 4 by asynchronous D2H. ACK confirms backing while all four extents "
            "remain in HBM. Later, an eligible ready request needs HBM. After native checks "
            "the runtime releases the backed private tail 3 and 4. Its Host copy supplies "
            "the JOIN restore in the next figure.")
    for x, tag, desc in ((58, "PREPARE_HOST", "SPAWN / WAIT_JOIN"),
                         (128, "Host ACK", "Keep in HBM"), (201, "HBM deficit", "Ready request")):
        f.label(x, 10, tag, 9.05, bold=True, color=TEAL if x == 58 else INK)
        f.label(x, 22, desc, 7.9, italic=True)
    f.rule(6, 30, 228)
    for y, host in ((50, False), (94, True)):
        f.rect(5, y-10, 224, 20, PALE_TEAL if host else PALE_BLUE, "none", 0, 2)
        f.bank(7, y, host)
    xs = (40, 108, 177)
    for x, dev, host in zip(xs, (STABLE, STABLE, PREFIX), (PREFIX, STABLE, STABLE)):
        f.pages(x, 44, dev)
        f.pages(x, 88, host)
    f.arrow(((89, 50), (104, 50)), color=RULE)
    f.arrow(((158, 50), (173, 50)), color=TEAL, dashed=True)
    f.arrow(((77, 57), (90, 74), (134, 74), (146, 86)), color=COPPER, width=1.9, head=5.5)
    f.label(112, 66.5, "Async D2H", 8.4, bold=True, color=COPPER)
    f.label(200, 68, "3, 4 released", 8.1)
    f.brace(108, 224, 107)
    f.label(166, 115, "Valid Host copy", 8.65, bold=True, color=TEAL)
    f.line(6, 123, 227, 123, LIGHT, .7)
    f.rect(12, 128, 8, 7, PALE_BLUE, BLUE, .75, .5)
    f.label(25, 131.5, "Already backed", 8.1, align="start")
    f.rect(120, 128, 8, 7, PEACH, COPPER, .75, .5)
    f.label(133, 131.5, "Incremental tail", 8.1, align="start")
    return f


def recovery():
    f = Art("04_join_parent_prefetch", "JOIN-aware parent KV recovery", COLUMN, 166,
            "The starting checkpoint is the final state of the backup figure. c1 has already "
            "returned. The last child c2's valid final EOS makes P's missing extents 3 and "
            "4 eligible for H2D. The demand-only illustration uses the same checkpoint, "
            "transfer duration, slot availability and native checks, starting H2D at "
            "parent submission. Both parent requests execute only after actual JOIN, "
            "submission, sufficient data and a native slot. Timings are schematic.")
    for x, name, present, fill, edge in ((7, "Host", STABLE, PALE_TEAL, TEAL),
                                         (122, "HBM", PREFIX, PALE_BLUE, BLUE)):
        f.rect(x, 4, 105, 17, fill, "none", 0, 2)
        f.label(x+7, 12.5, name, 8.4, align="start", bold=True, color=edge)
        f.pages(x+39, 7, present, pitch=14.4, height=11)
    f.rule(7, 27, 227)
    early, eos, returned, join, submit = 73, 103, 127, 142, 158
    start, duration, checks = eos+4, 49, 4
    assert early < eos < start < returned < join < submit
    f.label(8, 39, "c1", 8.7, align="start", bold=True, color=BLUE)
    f.line(30, 39, early, 39, BLUE, 2.3)
    f.endpoint(early, 39, "return")
    f.label(80, 39, "RETURN", 8, align="start")
    f.label(8, 62, "c2", 8.7, align="start", bold=True, color=BLUE)
    f.line(30, 62, eos, 62, BLUE, 2.3)
    f.endpoint(eos, 62, "eos")
    f.line(eos+4, 62, returned, 62, RULE, .9, "2.1 2")
    f.endpoint(returned, 62, "return")
    f.label(eos-7, 53, "Final EOS", 8.35, bold=True, color=TEAL)
    f.label(returned+12, 53, "RETURN", 8)
    f.path(((returned+3, 62), (join, 62), (join, 77)), RULE, .8)
    f.label(8, 78, "Parent P", 8.7, align="start", bold=True, color=BLUE)
    f.line(52, 78, join, 78, RULE, .8, "2.1 2")
    f.label(76, 72, "WAIT_JOIN", 7.8)
    f.line(join, 78, submit, 78, INK, .85)
    f.endpoint(join, 78, "return")
    f.endpoint(submit, 78, "submit")
    f.label(join-6, 88, "JOIN", 8.1)
    f.label(submit+9, 88, "Submit", 8.1)
    f.rule(7, 94, 227)
    f.path(((eos, 66), (eos, 99), (start, 103)), TEAL, .65, "2 2")
    f.line(submit, 95, submit, 140, RULE, .85, "2 2")
    for y, label, begin, bold in ((108, "BeliefKV", start, True),
                                  (130, "On demand", submit, False)):
        end = begin + duration
        service = max(submit, end) + checks
        f.label(8, y, label, 8.6, align="start", bold=bold)
        f.rect(begin, y-4.5, duration, 9, PEACH, COPPER, .85, .7)
        f.line(end, y, service, y, INK, .85)
        f.rect(service, y-4.5, 12, 9, BLUE, BLUE, .85, .7)
    f.arrow(((57, 143), (226, 143)), color=RULE)
    f.label(8, 143, "Time", 8.15, align="start", italic=True)
    f.rect(25, 153, 10, 7, PEACH, COPPER, .75, .5)
    f.label(40, 157, "H2D 3, 4", 8.2, align="start")
    f.rect(124, 153, 10, 7, BLUE, BLUE, .75, .5)
    f.label(139, 157, "Parent service", 8.2, align="start")
    return f


def export(figures, directory):
    directory.mkdir(parents=True, exist_ok=True)
    common_height = max(f.height / f.width for f in figures) * FULL_WIDTH
    collection = PowerPointExporter("en", width=FULL_WIDTH, height=common_height)
    for f in figures:
        f.save_to(directory)
        single = PowerPointExporter("en", width=f.width, height=f.height)
        for ppt in (collection, single):
            ppt.add_figure(f, directory)
            ppt.presentation.slides[-1].notes_slide.notes_text_frame.text = f.caption
            ppt.presentation.core_properties.subject = "BeliefKV lifecycle figures"
        single.save(directory / (Path(f.filename).stem + "_editable.pptx"), expected_slides=1)
    collection.save(directory / "beliefkv_icml_editable.pptx", expected_slides=len(figures))
    subprocess.run(["pdfunite", *(str(directory / Path(f.filename).with_suffix(".pdf"))
                                  for f in figures), str(directory / "beliefkv_icml_figures.pdf")],
                   check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    export((overview(), materialization(), backup_offload(), recovery()), parser.parse_args().output)
