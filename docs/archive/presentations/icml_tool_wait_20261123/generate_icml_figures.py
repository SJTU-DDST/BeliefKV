#!/usr/bin/env python3
"""Draw the BeliefKV ICML figures and export the same geometry to PowerPoint."""

from __future__ import annotations

import argparse
from html import escape
from io import BytesIO
import math
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

import cairocffi as cairo
import cairosvg
from PIL import Image

from generate_monthly_paper_figures import MATH_FAMILY, ROOT, PaperFigure, PowerPointExporter


OUTPUT = ROOT / "docs/figures/icml"
# Coordinates and type sizes are points at the intended paper placement.
TEXT_WIDTH = 486
COLUMN_WIDTH = 234
DPI = 600
PAPER = "#ffffff"
INK = "#20282e"
RULE = "#acbac0"
FAINT = "#d9e1e4"
NEUTRAL = "#f3f5f6"
POLICY = "#1e6571"
POLICY_BG = "#edf5f6"
A_EDGE = "#235c8d"
A_FILL = "#afcce6"
B_EDGE = "#306a55"
B_FILL = "#bad8c9"
HBM_EDGE = "#4d5269"
HBM_FILL = "#e4e6ef"
HOST_EDGE = "#7d5933"
HOST_FILL = "#f3e8d4"
COPY = "#97481b"
COPY_TINT = "#f4e4d7"
MINUS = "\N{MINUS SIGN}"
GE = "\N{GREATER-THAN OR EQUAL TO}"

SIGNALS = {
    "flow": (INK, "triangle", 1.0, None),
    "signal": (POLICY, "open", 1.1, "2.5 2.1"),
    "copy": (COPY, "triangle", 2.25, None),
    "axis": (HBM_EDGE, "triangle", 0.85, None),
}


class ICMLFigure(PaperFigure):
    def __init__(self, stem, title, width, height, caption):
        super().__init__(stem, title, height, "en", width=width)
        self.family = "Liberation Sans"
        self.caption = caption
        self.parts = [
            '<rect width="100%" height="100%" fill="#ffffff"/>',
            "<defs>",
            *(self.flow_marker(role, color, kind, size)
              for role, (color, kind, _, _) in SIGNALS.items()
              for size in (4, 5, 6)),
            "</defs>",
            "<style>path,line{stroke-linejoin:round;stroke-linecap:round}</style>",
        ]

    def center(self, x, y, value, size=8.4, *, bold=False, white=False):
        self.text(x, y + size * .35, value, size, PAPER if white else INK,
                  700 if bold else 400, "middle")

    def arrow(self, points, role="flow", *, size=5):
        color, kind, width, dash = SIGNALS[role]
        self.path(points, color, width, dash, f"{role}-{size}")
        head = f'data-arrow-kind="{kind}" data-arrow-size="{size}"'
        self.parts[-1] = self.parts[-1].replace("<path ", f"<path {head} ", 1)

    def tile(self, x, y, w, h, label, *, fill=NEUTRAL, edge=RULE, bold=True):
        self.rect(x, y, w, h, fill, edge, .8, 3)
        self.center(x + w / 2, y + h / 2, label, bold=bold)

    def agent(self, x, y, name):
        """Compact owner tokens shared by the four panels."""
        if name == "B":
            edge, fill = B_EDGE, B_FILL
        elif name in ("A", "a1", "a2"):
            edge, fill = A_EDGE, A_FILL
        else:
            edge, fill = HBM_EDGE, NEUTRAL
        parent = name in ("A", "B")
        self.rect(x, y - 6.5, 16, 13, edge if parent else fill, edge, .75, 3)
        self.center(x + 8, y, name, 8.2, bold=True, white=parent)

    def page(self, x, y, *, fill, edge, w=13, h=10, number=None, missing=False):
        self.rect(x, y, w, h, PAPER if missing else fill, edge, .8, 1.6,
                  "2.2 1.8" if missing else None)
        if number is not None:
            self.center(x + w / 2, y + h / 2, str(number), 8.0)

    def diamond_event(self, x, y, *, fill=INK):
        self.parts.append(
            f'<polygon points="{x},{y-3.4} {x+3.4},{y} {x},{y+3.4} {x-3.4},{y}" '
            f'fill="{fill}" stroke="{INK}" stroke-width=".8"/>'
        )

    def curve(self, start, control1, control2, end, *, color, width, dash=None):
        points = " ".join(f"{x} {y}" for x, y in (control1, control2, end))
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        self.parts.append(
            f'<path d="M {start[0]} {start[1]} C {points}" fill="none" '
            f'stroke="{color}" stroke-width="{width}"{dash_attr}/>'
        )

    def math(self, x, y, parts, *, size=12):
        """Center math with editable baseline and subscript text runs."""
        metrics = [
            (value, shift, size * scale,
             self.measure(value, size * scale, family=MATH_FAMILY, italic=italic)[0], italic)
            for value, shift, scale, italic in parts
        ]
        current = x - sum(part[3] for part in metrics) / 2
        for value, shift, font_size, w, italic in metrics:
            self.text(current, y + shift, value, font_size, family=MATH_FAMILY,
                      italic=italic)
            current += w

    def save_to(self, directory):
        for label, x, y, w, h in self.text_bounds:
            if min(x, y) < 0 or x + w > self.width or y + h > self.height:
                raise ValueError(f"{self.filename}: label outside the figure: {label!r}")
        document = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.width / 72:g}in" '
            f'height="{self.height / 72:.8f}in" viewBox="0 0 {self.width} {self.height}" '
            'role="img" aria-labelledby="title caption">\n'
            f'<title id="title">{escape(self.title)}</title>\n'
            f'<desc id="caption">{escape(self.caption)}</desc>\n'
            + "\n".join(self.parts) + "\n</svg>\n"
        )
        path = directory / self.filename
        path.write_text(document, encoding="utf-8")

        # Nimbus provides embedded Type 1C fonts in the paper PDF.
        pdf_root = ET.fromstring(document)
        svg_ns = "{http://www.w3.org/2000/svg}"
        for label in pdf_root.findall(svg_ns + "text"):
            family = "Nimbus Roman" if label.get("font-family") == MATH_FAMILY else "Nimbus Sans"
            label.set("font-family", family)
            if MINUS in label.text or GE in label.text:
                outline_operator(label, family, svg_ns)
        cairosvg.svg2pdf(
            bytestring=ET.tostring(pdf_root), output_width=self.width,
            output_height=self.height, dpi=72, write_to=str(path.with_suffix(".pdf")),
        )
        raster = cairosvg.svg2png(
            bytestring=document.encode(), output_width=round(self.width * DPI / 72),
            output_height=round(self.height * DPI / 72),
        )
        with Image.open(BytesIO(raster)) as image:
            image.convert("RGB").save(path.with_suffix(".png"), dpi=(DPI, DPI))


def outline_operator(label, family, svg_ns):
    # Preserve standard Type 1C text subsets by drawing the two non-Latin operators.
    context = cairo.Context(cairo.RecordingSurface(cairo.CONTENT_COLOR_ALPHA, None))
    context.select_font_face(family)
    context.set_font_size(float(label.get("font-size")))
    context.move_to(float(label.get("x")), float(label.get("y")))
    context.text_path(label.text)
    commands = ("M", "L", "C", "Z")
    path = " ".join(
        " ".join((commands[command], *(f"{point:g}" for point in points)))
        for command, points in context.copy_path()
    )
    fill = label.get("fill")
    label.clear()
    label.tag = svg_ns + "path"
    label.set("d", path)
    label.set("fill", fill)


def overview():
    f = ICMLFigure(
        "01_overview", "BeliefKV system overview", TEXT_WIDTH, 127,
        "BeliefKV consumes workflow events and live SGLang state. D3 supplies first-KV "
        "permits to admission. Stable KV can be backed up during delegation or tool waits. "
        "The offload policy releases backed pages for a ready request's live HBM deficit. "
        "The last outstanding child's final EOS can trigger the JOIN prefetch policy. "
        "HiCache manages both tiers, and native SGLang retains allocation and admission.",
    )
    f.text(8, 16, "Agent workflows", 11, weight=700)
    f.text(156, 16, "BeliefKV", 11, weight=700)
    f.text(349, 16, "SGLang", 11, weight=700)
    f.text(312, 15, "KV / slots", 8.3, anchor="end")
    f.arrow(((344, 12), (316, 12)), "signal", size=4)

    f.agent(8, 53, "A")
    f.path(((24, 53), (41, 53)), INK, 1)
    f.arrow(((41, 53), (41, 36), (57, 36)), size=4)
    f.arrow(((41, 53), (41, 71), (57, 71)), size=4)
    f.agent(59, 36, "a1")
    f.agent(59, 71, "a2")
    f.text(8, 78, "SPAWN", 8.4)
    f.text(80, 38.8, "Tool wait", 8.4)
    f.diamond_event(83, 51)
    f.center(110, 51, "Final EOS", 8.4)
    f.circle(83, 71, 2.5, INK, INK, .7)
    f.center(110, 71, "RETURN", 8.4)
    f.agent(8, 105, "B")
    f.text(30, 108, "Root arrival", 8.4)
    f.path(((126, 28), (133, 28), (133, 117), (126, 117)), RULE, .9)
    f.arrow(((133, 73), (153, 73)), "signal", size=4)

    f.rect(156, 26, 158, 94, POLICY_BG, POLICY, .9, 5)
    for y, mark, action in (
        (42, "D3", "First-KV budget"),
        (73, "Tool", "Prepare / offload"),
        (104, "JOIN", "Parent prefetch"),
    ):
        f.rect(163, y - 6.5, 28, 13, POLICY, "none", 0, 3)
        f.center(177, y, mark, 8.25, white=True, bold=True)
        f.text(196, y + 3.3, action, 9.6, weight=700)
    for y in (57.5, 88.5):
        f.line(163, y, 307, y, FAINT, .8)
    f.arrow(((314, 42), (346, 42)), "signal")
    f.center(331, 33, "Permit", 8.1)
    f.path(((314, 73), (330, 73), (330, 104), (314, 104)), POLICY, 1.1, "2.5 2.1")
    f.arrow(((330, 88.5), (346, 88.5)), "signal")

    f.tile(349, 31, 129, 22, "Native admission", fill=NEUTRAL, edge=HBM_EDGE)
    f.rect(349, 57, 129, 63, NEUTRAL, HBM_EDGE, .9, 4)
    f.center(408, 63.5, "HiCache", 8.15, bold=True)
    for y, name, edge, fill in (
        (70, "HBM", HBM_EDGE, HBM_FILL), (101, "Host", HOST_EDGE, HOST_FILL),
    ):
        f.rect(355, y, 117, 15, fill, edge, .8, 3)
        f.text(365, y + 10.5, name, 8.65, weight=700)
        for x in (432, 444, 456):
            f.page(x, y + 3.5, fill=PAPER, edge=edge, w=8, h=8)
    f.arrow(((371, 87), (371, 99)), "copy", size=5)
    f.text(379, 96, "D2H", 8.0, weight=700)
    f.arrow(((431, 99), (431, 87)), "copy", size=5)
    f.text(439, 96, "H2D", 8.0, weight=700)
    return f


def materialization():
    f = ICMLFigure(
        "02_first_kv_materialization", "Pressure-aware first KV materialization", COLUMN_WIDTH, 163,
        "Free Host slots, safely reclaimable Host slots, and free HBM slots form the "
        "shared first-KV budget after subtracting growth and reserves G and outstanding "
        "commitments U. The figure shows five illustrative units. A fitting JOIN blocker "
        "takes three, the oversized child stays pending, and a fitting new root takes the "
        "two remaining units. A permit is rechecked before native first prefill. Normal "
        "continuations take the existing path.",
    )
    f.rect(6, 5, 135, 38, HOST_FILL, HOST_EDGE, .9, 4)
    f.rect(146, 5, 82, 38, HBM_FILL, HBM_EDGE, .9, 4)
    f.text(12, 16.5, "Host", 9.2, weight=700)
    f.text(152, 16.5, "HBM", 9.2, weight=700)
    for x in (13, 24, 153, 164):
        f.page(x, 21, fill=HBM_EDGE, edge=HBM_EDGE, w=8, h=8)
    for x in (48, 59, 70, 187, 199, 211):
        edge = HOST_EDGE if x < 141 else HBM_EDGE
        f.page(x, 21, fill=PAPER, edge=edge, w=8, h=8)
    for x in (102, 113):
        f.page(x, 21, fill="#d8bf97", edge=HOST_EDGE, w=8, h=8)
    f.center(22, 35.5, "Live", 8.0)
    for x, term, subscript in ((63, "F", "h"), (113, "R", "h"), (203, "F", "d")):
        f.math(x, 37, [(term, 0, 1, True), (subscript, 2, .74, True)], size=10.3)

    f.math(117, 58, [
        ("K", 0, 1, True), (" = ", 0, 1, False),
        ("F", 0, 1, True), ("h", 2.5, .7, True), (" + ", 0, 1, False),
        ("R", 0, 1, True), ("h", 2.5, .7, True), (" + ", 0, 1, False),
        ("F", 0, 1, True), ("d", 2.5, .7, True),
        (f" {MINUS} ", 0, 1, False), ("G", 0, 1, True),
        (f" {MINUS} ", 0, 1, False), ("U", 0, 1, True), (" = 5", 0, 1, False),
    ], size=12.6)

    f.text(7, 71, "First requests", 8.5, weight=700)
    f.center(106, 68, "Need", 8.5, bold=True)
    f.center(151, 68, "Budget", 8.5, bold=True)
    f.center(218, 68, "Left", 8.5, bold=True)
    f.line(6, 75, 228, 75, RULE, .8)
    capacity = 5
    charges = []
    for y, agent, label, need, edge, fill in (
        (85, "a1", "JOIN blocker", 3, A_EDGE, A_FILL),
        (102, "c1", "Child", 4, HBM_EDGE, NEUTRAL),
        (119, "B", "Root", 2, B_EDGE, B_FILL),
    ):
        fits = need <= capacity - len(charges)
        f.agent(6, y, agent)
        f.text(27, y + 3, label, 8.4)
        f.center(106, y, str(need), 8.8, bold=True)
        if fits:
            charges.extend([(edge, fill)] * need)
            f.path(((186, y), (189, y + 3), (195, y - 3.5)), POLICY, 1.25)
        else:
            f.center(191, y, "Hold", 8.3)
        for index in range(capacity):
            page_edge, page_fill = charges[index] if index < len(charges) else (RULE, PAPER)
            f.page(128 + 9.1 * index, y - 4, fill=page_fill, edge=page_edge, w=7.3, h=8)
        f.center(218, y, str(capacity - len(charges)), 8.8)
        f.line(6, y + 9, 228, y + 9, FAINT, .65)

    f.center(24, 142, "Permit", 8.5)
    f.arrow(((43, 142), (65, 142)), "signal", size=4)
    f.tile(67, 134.5, 63, 15, "Fits now?", fill=POLICY_BG, edge=POLICY)
    f.arrow(((132, 142), (151, 142)), size=4)
    f.tile(153, 134.5, 74, 15, "Native prefill", fill=HBM_FILL, edge=HBM_EDGE)
    f.arrow(((98.5, 150), (98.5, 156.5)), "signal", size=4)
    f.text(106, 156, "Hold", 8.3)
    return f


def tool_offload():
    f = ICMLFigure(
        "03_tool_aware_offload", "Demand-driven KV offload during tool waits", COLUMN_WIDTH, 153,
        "Evaluate a1 while its tool is still running and B has a live HBM deficit. "
        "The residual survival probability at the estimated movement cost C must "
        "reach the policy threshold. C includes missing donor backup, consumer load, "
        "eventual donor reload, and margins. In the example page 1 already has valid "
        "Host backing. Only page 2 is copied. After ACK, live demand, donor eligibility, "
        "and native page safety are checked again. Released capacity returns to the "
        "native allocator for admission.",
    )
    f.agent(6, 10, "a1")
    f.text(27, 13, "WAIT_TOOL", 8.65)
    f.agent(124, 10, "B")
    f.text(146, 13, "HBM deficit", 8.65, weight=700)
    f.line(6, 21, 228, 21, RULE, .8)

    x0, x1, cost_x, cutoff_y = 26, 110, 64, 55
    f.arrow(((x0, 75), (x0, 28)), "axis", size=4)
    f.arrow(((x0, 75), (x1, 75)), "axis", size=4)
    f.line(x0, cutoff_y, x1 - 4, cutoff_y, RULE, .85, "2.1 2")
    f.line(cost_x, 43, cost_x, 75, RULE, .85, "2.1 2")
    f.text(5.5, 34, "S(r)", 8.6, family=MATH_FAMILY, italic=True)
    f.text(6, cutoff_y + 2.2, "p", 9.3, family=MATH_FAMILY, italic=True)
    f.text(11, cutoff_y + 4, "min", 7.25, family=MATH_FAMILY, italic=True)
    f.text(101, 85, "r", 8.8, family=MATH_FAMILY, italic=True)
    f.text(cost_x, 85, "C", 9.6, anchor="middle", family=MATH_FAMILY, italic=True)
    f.curve((x0, 32), (40, 33), (54, 37), (cost_x, 43), color=A_EDGE, width=1.7)
    f.curve((cost_x, 43), (80, 51), (94, 63), (106, 67), color=A_EDGE, width=1.7)
    f.circle(cost_x, 43, 2.4, A_EDGE, A_EDGE, .6)

    f.center(174, 31, "Movement cost C", 9.2, bold=True)
    f.center(136, 45, "D2H", 8.0)
    f.center(174, 45, "H2D", 8.0)
    f.center(214, 45, "Margin", 8.0)
    for x, w, text, edge, fill in (
        (123, 25, "a1", A_EDGE, A_FILL),
        (149, 25, "B", B_EDGE, B_FILL),
        (175, 25, "a1", A_EDGE, A_FILL),
        (201, 26, "m", RULE, NEUTRAL),
    ):
        f.rect(x, 50, w, 13, fill, edge, .8, 2)
        f.center(x + w / 2, 56.5, text, 8.3, bold=True)
    f.math(175, 80, [
        ("S", 0, 1, True), ("(", 0, 1, False), ("C", 0, 1, True),
        (f") {GE} ", 0, 1, False), ("p", 0, 1, True), ("min", 2, .7, True),
    ], size=12)

    f.line(6, 89, 228, 89, RULE, .8)
    f.center(168, 97, "Recheck", 8.2, bold=True)
    f.center(208, 97, "Free", 8.2, bold=True)
    f.text(6, 112.5, "HBM", 8.6, weight=700)
    f.text(6, 141.5, "Host", 8.6, weight=700)
    f.rect(38, 102, 190, 15, HBM_FILL, HBM_EDGE, .85, 3)
    f.rect(38, 131, 190, 15, HOST_FILL, HOST_EDGE, .85, 3)
    for x in (52, 117, 195):
        if x != 195:
            for page in (1, 2):
                f.page(x + 13 * (page - 1), 104, fill=A_FILL, edge=A_EDGE, w=11, h=11, number=page)
        else:
            for offset in (0, 13):
                f.page(x + offset, 104, fill=PAPER, edge=HBM_EDGE, w=11, h=11)
        f.page(x, 133, fill=A_FILL, edge=A_EDGE, w=11, h=11, number=1)
        f.page(x + 13, 133, fill=A_FILL, edge=A_EDGE, w=11, h=11,
               number=2, missing=x == 52)
    f.arrow(((79, 109.5), (114, 109.5)), "axis", size=4)
    f.arrow(((144, 109.5), (191, 109.5)), size=5)
    f.arrow(((71, 116), (82, 128), (131, 128), (135.5, 132)), "copy", size=5)
    f.center(106, 122.5, "D2H", 8.2, bold=True)
    f.arrow(((144, 138.5), (168, 138.5), (168, 113)), "signal", size=4)
    f.center(185, 128, "ACK", 8.2, bold=True)
    return f


def join_prefetch():
    f = ICMLFigure(
        "04_join_parent_prefetch", "JOIN-aware prefetch of parent KV", COLUMN_WIDTH, 140,
        "A waits on an observed ALL-JOIN. After a2 returns, the final no-tool EOS of a1 "
        "can trigger a bounded H2D for pages 3 and 4 from A's settled Host checkpoint. "
        "The shaded interval is the portion before the real JOIN. The last RETURN, JOIN, "
        "and submission are shown as distinct events. In this example the transfer runs "
        "past submission. A starts GPU service once its JOIN and native request, data, "
        "and slot dependencies are satisfied. Positions are schematic.",
    )
    f.agent(6, 12.5, "A")
    for x, name, edge, fill in (
        (29, "Host", HOST_EDGE, HOST_FILL), (130, "HBM", HBM_EDGE, HBM_FILL),
    ):
        f.rect(x, 4.5, 98, 16, fill, edge, .85, 3)
        f.text(x + 4, 15.4, name, 8.2, weight=700)
        for page in (1, 2, 3, 4):
            f.page(x + 30 + 16 * (page - 1), 7, fill=A_FILL, edge=A_EDGE,
                   w=14, h=11, number=page, missing=name == "HBM" and page >= 3)
    f.line(6, 25, 228, 25, RULE, .8)

    start, early, eos, copy, returned, join, submit, data, run, end = (
        34, 67, 98, 104, 136, 154, 178, 189, 205, 227,
    )
    f.line(join, 71, join, 118, RULE, .85, "2.2 2")
    f.agent(6, 38, "a2")
    f.rect(start, 34.5, early - start, 7, A_FILL, A_EDGE, .75, 2)
    f.circle(early, 38, 2.55, INK, INK, .65)
    f.text(74, 41, "RETURN", 8.2)
    f.agent(6, 62, "a1")
    f.rect(start, 58.5, eos - start, 7, A_FILL, A_EDGE, .75, 2)
    f.line(eos, 62, returned, 62, INK, .95, "2.4 2")
    f.diamond_event(eos, 62)
    f.circle(returned, 62, 2.55, INK, INK, .65)
    f.text(eos - 3, 54, "Final EOS", 8.35, weight=700, anchor="middle")
    f.text(returned + 5, 54, "RETURN", 8.2, anchor="middle")
    f.arrow(((eos, 67), (eos, 74), (copy, 80)), "signal", size=4)

    f.text(6, 87.5, "H2D", 8.5, weight=700)
    f.rect(copy, 79, data - copy, 11, COPY_TINT, COPY, .85, 2.2)
    f.rect(copy, 79, join - copy, 11, COPY, COPY, .85, 2.2)
    for page, x in ((3, 118), (4, 130)):
        f.page(x, 80.5, fill=PAPER, edge=PAPER, w=9, h=8, number=page)
    f.circle(data, 84.5, 2.35, COPY, COPY, .6)
    f.center(210, 84.5, "KV ready", 8.25)
    f.line(data, 89.5, data, 102, RULE, .8, "2.1 2")

    f.agent(6, 108, "A")
    f.rect(start, 101.5, join - start, 13, PAPER, A_EDGE, .85, 2.5, "2.8 2.1")
    f.center(90, 108, "ALL-JOIN", 8.4)
    f.line(join, 108, run, 108, HBM_EDGE, 1)
    f.diamond_event(join, 108)
    f.rect(submit - 2.2, 105.8, 4.4, 4.4, PAPER, INK, .9, 0)
    f.text(join - 3, 124, "JOIN", 8.3, anchor="middle", weight=700)
    f.text(submit, 124, "Submit", 8.3, anchor="middle")
    f.rect(run, 101.5, end - run, 13, A_EDGE, A_EDGE, .75, 2.5)
    f.center((run + end) / 2, 108, "Run", 8.3, white=True, bold=True)
    f.arrow(((start, 132), (end, 132)), "axis", size=4)
    f.text(6, 134, "Time", 8.1)
    return f


def export_decks(figures, directory):
    height = max(math.ceil(f.height * TEXT_WIDTH / f.width) for f in figures)
    collection = PowerPointExporter("en", width=TEXT_WIDTH, height=height)
    for figure in figures:
        single = PowerPointExporter("en", width=figure.width, height=figure.height)
        for exporter in (single, collection):
            exporter.add_figure(figure, directory)
            exporter.presentation.slides[-1].notes_slide.notes_text_frame.text = figure.caption
            exporter.presentation.core_properties.subject = "BeliefKV ICML figure set"
        name = Path(figure.filename).stem + "_editable.pptx"
        single.save(directory / name, expected_slides=1)
    collection.save(directory / "beliefkv_icml_editable.pptx", expected_slides=len(figures))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    directory = parser.parse_args().output
    directory.mkdir(parents=True, exist_ok=True)
    figures = (overview(), materialization(), tool_offload(), join_prefetch())
    for figure in figures:
        figure.save_to(directory)
    export_decks(figures, directory)
    subprocess.run([
        "pdfunite", *(str(directory / Path(f.filename).with_suffix(".pdf")) for f in figures),
        str(directory / "beliefkv_icml_figures.pdf"),
    ], check=True)


if __name__ == "__main__":
    main()
