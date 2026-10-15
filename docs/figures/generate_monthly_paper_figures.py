#!/usr/bin/env python3
"""Generate bilingual vector figures and native editable PowerPoint diagrams."""

from __future__ import annotations

import argparse
from functools import lru_cache
import hashlib
from html import escape
from io import BytesIO
import json
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

import cairosvg
from PIL import Image, ImageDraw, ImageFont
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.dml import MSO_LINE_DASH_STYLE
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE, MSO_CONNECTOR_TYPE, MSO_SHAPE_TYPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.xmlchemy import OxmlElement
from pptx.util import Inches, Pt

from generate_beliefkv_paper_figures import Figure


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "docs/reports/2026-10/assets"
WIDTH = 1800
HEIGHT = 1200
PAPER_WIDTH_INCHES = 7.2
DPI = 600
FONT_ROOT = Path("/usr/share/fonts")
ARCHITECTURE_DATE = "2026-10-15"
# Ownership uses color; residency uses borders and location.
INK = "#181818"
MUTED = "#535353"
RULE = "#a6a6a6"
PAPER = "#ffffff"
GRAY = "#969696"
GRAY_PALE = "#eeeeee"
BACKPLATE = "#dddddd"
BLUE = "#2676b8"
BLUE_PALE = "#a9cee8"
TEAL = "#528b36"
TEAL_PALE = "#b5d68b"
AMBER = "#be7725"
AMBER_PALE = "#f2bd7d"
PINK = "#e9a6bf"
YELLOW = "#f5d460"
RED = "#bf3335"
MATH_FAMILY = "Liberation Serif"
PALETTE = {
    "ink": INK, "gray_backplate": GRAY_PALE, "blue_section": BLUE_PALE,
    "parent": PINK, "child_1": TEAL_PALE, "child_2": AMBER_PALE,
    "transfer_engine": YELLOW, "control_action": RED,
}
ARCHITECTURE_SOURCES = (
    "beliefkv/control/causal_graph.py",
    "beliefkv/predictor/child_semantic_work.py",
    "beliefkv/predictor/conditional_work.py",
    "beliefkv/predictor/project_tool_history.py",
    "beliefkv/runtime/semantic_report_worker.py",
    "beliefkv/runtime/sglang_v0520_prediction.py",
    "beliefkv/runtime/sglang_v0520_runtime.py",
    "beliefkv/runtime/sglang_v0520_physical.py",
    "beliefkv/runtime/sglang_v0520_admission.py",
    "beliefkv/runtime/native_transfer_policy.py",
    "beliefkv/runtime/native_transfer_service.py",
    "patches/sglang-v0.5.20-beliefkv-staging.patch",
)


@lru_cache(maxsize=256)
def drawing_font(
    language: str, size: float, weight: int = 400,
    family: str | None = None, italic: bool = False,
):
    if family == MATH_FAMILY:
        style = (
            "BoldItalic" if weight >= 600 and italic else
            "Bold" if weight >= 600 else "Italic" if italic else "Regular"
        )
        return ImageFont.truetype(
            str(FONT_ROOT / "truetype/liberation" / f"LiberationSerif-{style}.ttf"),
            round(size * 4),
        )
    if language == "zh":
        filename = "NotoSansCJK-Bold.ttc" if weight >= 600 else "NotoSansCJK-Regular.ttc"
        return ImageFont.truetype(
            str(FONT_ROOT / "opentype/noto" / filename), round(size * 4), index=2,
        )
    filename = "DejaVuSans-Bold.ttf" if weight >= 600 else "DejaVuSans.ttf"
    return ImageFont.truetype(
        str(FONT_ROOT / "truetype/dejavu" / filename), round(size * 4),
    )


class PaperFigure(Figure):
    def __init__(self, name: str, title: str, height: int, language: str):
        super().__init__(name + "_" + language + ".svg", title, WIDTH, height)
        self.language = language
        self.family = "Noto Sans CJK SC" if language == "zh" else "DejaVu Sans"
        self.parts = [
            '<rect width="100%" height="100%" fill="#ffffff"/>',
            "<defs>",
            *(self._marker(name, color) for name, color in (
                ("arrow", INK), ("blue-arrow", BLUE), ("teal-arrow", TEAL),
                ("amber-arrow", AMBER), ("red-arrow", RED), ("gray-arrow", GRAY),
            )),
            "</defs>",
            "<style>text{letter-spacing:0}"
            "path,line{stroke-linejoin:round;stroke-linecap:round}</style>",
        ]
        self.text_bounds = []
        self.math_objects = 0

    def tr(self, english: str, chinese: str) -> str:
        return chinese if self.language == "zh" else english

    def measure(
        self, value: str, size: float, weight: int = 400,
        family: str | None = None, italic: bool = False,
    ):
        font = drawing_font(self.language, size, weight, family, italic)
        _, top, _, bottom = font.getbbox(value, anchor="ls")
        return font.getlength(value) / 4, (bottom - top) / 4, -top / 4

    def text(
        self, x, y, value, size=24, color=INK, weight=400,
        anchor="start", family=None, italic=False,
    ):
        family = family or self.family
        width, height, baseline = self.measure(value, size, weight, family, italic)
        left = x - (width / 2 if anchor == "middle" else width if anchor == "end" else 0)
        self.text_bounds.append((value, left, y - baseline, width, height))
        self.parts.append(
            f'<text x="{x}" y="{y}" xml:space="preserve" font-size="{size}" fill="{color}" '
            f'font-weight="{weight}" text-anchor="{anchor}" '
            f'font-family="{escape(family)}" font-style="{"italic" if italic else "normal"}">'
            f"{escape(value)}</text>"
        )
        if family == MATH_FAMILY:
            self.math_objects += 1

    def panel(self, x, y, w, h, title, *, color=BLUE_PALE, fill=GRAY_PALE, size=28):
        self.rect(x, y, w, h, fill, INK, 2, 6)
        self.rect(x, y, w, 48, color, INK, 2, 6)
        self.rect(x + 1.2, y + 27, w - 2.4, 20, color, "none", 0, 0)
        self.line(x, y + 48, x + w, y + 48, INK, 1.7)
        self.text(x + 20, y + 33, title, size, INK, 700)

    def heading(self, english, chinese, subtitle_en, subtitle_zh):
        self.text(40, 51, self.tr(english, chinese), 37, INK, 700)
        self.text(40, 88, self.tr(subtitle_en, subtitle_zh), 24, MUTED)
        self.line(40, 108, self.width - 40, 108, INK, 1.6)

    def arrow(self, points, *, color=INK, dashed=False, width=2.6):
        marker = {
            BLUE: "blue-arrow", TEAL: "teal-arrow", AMBER: "amber-arrow",
            RED: "red-arrow", GRAY: "gray-arrow",
        }.get(color, "arrow")
        self.path(points, color, width, "8 6" if dashed else None, marker)

    def centered(self, x, y, value, *, size=23, color=INK, bold=False):
        self.text(x, y, value, size, color, 700 if bold else 400, "middle")

    def page(
        self, x, y, label="", *, fill=PINK, stroke=INK, dashed=False,
        w=54, h=47, size=21,
    ):
        self.rect(x, y, w, h, fill, stroke, 1.8, 1, "6 4" if dashed else None)
        if label:
            self.centered(x + w / 2, y + h / 2 + size * .34,
                          label, size=size, bold=True)

    def token(self, x, y, label, fill=PINK, stroke=INK, r=27, size=25):
        self.circle(x, y, r, fill, stroke, 2)
        self.centered(x, y + size * .34, label, size=size, bold=True)

    def badge(self, x, y, label, *, fill=PAPER, r=16):
        self.circle(x, y, r, fill, INK, 1.8)
        self.centered(x, y + 6, str(label), size=21, bold=True)

    def math_width(self, tokens, size=30):
        total = 0
        for token in tokens:
            if isinstance(token, str):
                total += self.measure(token, size, family=MATH_FAMILY)[0]
            else:
                base, sub, sup = (*token, None, None)[:3]
                total += self.measure(base, size, family=MATH_FAMILY, italic=True)[0]
                if sub or sup:
                    total += max(
                        self.measure(value or "", size * .65, family=MATH_FAMILY)[0]
                        for value in (sub, sup)
                    ) + 2
        return total

    def math(self, x, y, tokens, *, size=30, color=INK, anchor="start"):
        width = self.math_width(tokens, size)
        x -= width / 2 if anchor == "middle" else width if anchor == "end" else 0
        for token in tokens:
            if isinstance(token, str):
                self.text(x, y, token, size, color, family=MATH_FAMILY)
                x += self.measure(token, size, family=MATH_FAMILY)[0]
                continue
            base, sub, sup = (*token, None, None)[:3]
            self.text(x, y, base, size, color, family=MATH_FAMILY, italic=True)
            x += self.measure(base, size, family=MATH_FAMILY, italic=True)[0]
            if sub:
                self.text(x + 1, y + size * .25, sub, size * .65, color,
                          family=MATH_FAMILY)
            if sup:
                self.text(x + 1, y - size * .52, sup, size * .65, color,
                          family=MATH_FAMILY)
            if sub or sup:
                x += max(
                    self.measure(value or "", size * .65, family=MATH_FAMILY)[0]
                    for value in (sub, sup)
                ) + 2
        return width

    def fraction(self, x, y, numerator, denominator, *, size=30, color=INK):
        width = max(self.math_width(tokens, size) for tokens in (numerator, denominator)) + 20
        self.math(x + width / 2, y - 9, numerator, size=size, color=color, anchor="middle")
        self.line(x, y, x + width, y, color, 1.8)
        self.math(x + width / 2, y + size + 3, denominator,
                  size=size, color=color, anchor="middle")
        return width

    def document(self, x, y, w=130, h=112, *, color=BLUE_PALE):
        self.rect(x, y, w, h, PAPER, INK, 1.9, 2)
        self.polygon(((x + w - 24, y), (x + w, y + 24), (x + w - 24, y + 24)),
                     BACKPLATE, INK)
        for index, fraction in enumerate((.72, .84, .65, .77)):
            yy = y + 33 + index * 19
            if yy > y + h - 10:
                break
            self.rect(x + 12, yy - 9, (w - 25) * fraction, 10,
                      color if index in (1, 3) else BACKPLATE, "none", 0, 0)

    def polygon(self, points, fill=PAPER, stroke=INK, width=1.8):
        self.parts.append(
            '<polygon points="' + " ".join(f"{x},{y}" for x, y in points)
            + f'" fill="{fill}" stroke="{stroke}" stroke-width="{width}"/>'
        )

    def bank(self, x, y, w, h, colors, *, label=None, missing=()):
        self.rect(x, y, w, h, PAPER, INK, 2, 3)
        self.rect(x + 6, y + 6, w - 12, 12, BACKPLATE, "none", 0, 0)
        gap = 6
        page_w = (w - 20 - (len(colors) - 1) * gap) / len(colors)
        for index, color in enumerate(colors):
            self.page(x + 10 + index * (page_w + gap), y + 29,
                      fill=color if index not in missing else PAPER,
                      dashed=index in missing, w=page_w, h=h - 43)
        for xx in range(round(x + 13), round(x + w - 5), 17):
            self.rect(xx, y + h, 7, 8, YELLOW, INK, 1, 0)
        if label:
            self.centered(x + w / 2, y - 15, label, size=27, bold=True)

    def snapshot(self, x, y, label="", *, fill=PINK, scale=1, faded=False):
        for index in (2, 1, 0):
            self.rect(x + index * 7 * scale, y - index * 6 * scale,
                      79 * scale, 59 * scale, BACKPLATE if faded else fill,
                      GRAY if faded else INK, 1.5, 1)
        for row in range(2):
            for column in range(3):
                self.rect(x + (10 + column * 22) * scale,
                          y + (10 + row * 22) * scale,
                          15 * scale, 14 * scale, PAPER,
                          GRAY if faded else INK, 1, 0)
        if label:
            self.math(x + 40 * scale, y + 87 * scale, [label],
                      size=27 * scale, color=GRAY if faded else INK, anchor="middle")

    def gpu(self, x, y, w=130, h=114, *, label="GPU", running=None):
        for xx in range(round(x + 14), round(x + w - 5), 20):
            self.line(xx, y - 10, xx, y, INK, 2)
            self.line(xx, y + h, xx, y + h + 10, INK, 2)
        for yy in range(round(y + 15), round(y + h - 5), 20):
            self.line(x - 10, yy, x, yy, INK, 2)
            self.line(x + w, yy, x + w + 10, yy, INK, 2)
        self.rect(x, y, w, h, BLUE_PALE, INK, 2, 2)
        self.centered(x + w / 2, y + 29, label, size=24, bold=True)
        rows, columns = 2, 3
        for row in range(rows):
            for column in range(columns):
                index = row * columns + column
                fill = running[index] if running and index < len(running) else PAPER
                self.rect(x + 13 + column * (w - 22) / columns,
                          y + 42 + row * (h - 53) / rows,
                          (w - 35) / columns, (h - 66) / rows,
                          fill, INK, 1.1, 1)

    def dma(self, x, y, w=142, h=85, *, label="DMA"):
        self.rect(x, y, w, h, YELLOW, INK, 2, 3)
        self.centered(x + w / 2, y + min(29, h * .45), label, size=26, bold=True)
        self.arrow(((x + 19, y + h * .63), (x + w - 19, y + h * .63)), width=2)
        self.arrow(((x + w - 19, y + h * .82), (x + 19, y + h * .82)), width=2)

    def ticket(self, x, y, label, fill, *, w=180, h=52, resident=False, protected=False):
        self.rect(x, y, w, h, PAPER, INK, 1.8, 2)
        self.rect(x + 1, y + 1, 10, h - 2, fill, "none", 0, 0)
        size = 19 if h < 40 else 23
        self.centered(x + 48, y + h / 2 + size * .34, label, size=size, bold=True)
        for index in range(3):
            page_h = min(22, h - 8)
            self.page(x + w - 75 + index * 20, y + (h - page_h) / 2,
                      fill=fill if resident else PAPER,
                      dashed=not resident, w=15, h=page_h)
        if protected:
            self.lock(x + w - 20, y - 18, color=INK, scale=.6)

    def clock(self, x, y, radius=25, *, color=INK):
        self.circle(x, y, radius, PAPER, color, 1.8)
        self.path(((x, y - radius * .65), (x, y), (x + radius * .55, y + radius * .25)),
                  color, 2)

    def terminal(self, x, y, w=105, h=65):
        self.rect(x, y, w, h, PAPER, INK, 1.8, 3)
        self.rect(x + 1, y + 1, w - 2, 13, BACKPLATE, "none", 0, 0)
        self.path(((x + 16, y + 28), (x + 27, y + 38), (x + 16, y + 48)), INK, 2)
        self.line(x + 38, y + 47, x + w - 14, y + 47, INK, 2)

    def lock(self, x, y, *, color=INK, scale=1):
        self.path(((x + 5 * scale, y + 13 * scale),
                   (x + 5 * scale, y + 2 * scale),
                   (x + 19 * scale, y + 2 * scale),
                   (x + 19 * scale, y + 13 * scale)), color, 2)
        self.rect(x, y + 13 * scale, 24 * scale, 20 * scale, PAPER, color, 1.8, 2)
        self.circle(x + 12 * scale, y + 21 * scale, 2 * scale, color, color, 1)

    def check(self, x, y, *, color=TEAL, size=20):
        self.path(((x, y + size * .5), (x + size * .35, y + size),
                   (x + size, y)), color, 2.8)

    def cross(self, x, y, *, color=GRAY, size=20):
        self.line(x, y, x + size, y + size, color, 2.8)
        self.line(x, y + size, x + size, y, color, 2.8)

    def interval(self, x1, x2, y, *, color=TEAL, center=None):
        self.line(x1, y, x2, y, color, 2.5)
        self.line(x1, y - 9, x1, y + 9, color, 2)
        self.line(x2, y - 9, x2, y + 9, color, 2)
        self.circle(center or (x1 + x2) / 2, y, 5, color, color, 1)

    def save_to(self, directory: Path):
        for value, x, y, width, height in self.text_bounds:
            if x < 0 or y < 0 or x + width > self.width or y + height > self.height:
                raise ValueError(f"{self.filename}: text outside canvas: {value!r}")
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / self.filename
        physical_height = PAPER_WIDTH_INCHES * self.height / self.width
        svg = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{PAPER_WIDTH_INCHES}in" '
            f'height="{physical_height:.6f}in" viewBox="0 0 {self.width} {self.height}" '
            'role="img" aria-labelledby="title">\n'
            f'<title id="title">{escape(self.title)}</title>\n'
            + "\n".join(self.parts) + "\n</svg>\n"
        )
        path.write_text(svg, encoding="utf-8")
        cairosvg.svg2pdf(
            bytestring=svg.encode("utf-8"), write_to=str(path.with_suffix(".pdf")),
            dpi=72, output_width=PAPER_WIDTH_INCHES * 72,
            output_height=physical_height * 72,
        )
        png_width = round(PAPER_WIDTH_INCHES * DPI)
        png_height = round(physical_height * DPI)
        rendered = cairosvg.svg2png(
            bytestring=svg.encode("utf-8"), output_width=png_width,
            output_height=png_height,
        )
        with Image.open(BytesIO(rendered)) as image:
            image.convert("RGB").save(path.with_suffix(".png"), dpi=(DPI, DPI))
        return {
            "stem": path.stem, "language": self.language, "title": self.title,
            "canvas": [self.width, self.height], "png_pixels": [png_width, png_height],
            "paper_width_inches": PAPER_WIDTH_INCHES, "dpi": DPI,
            "text_objects": len(self.text_bounds),
            "editable_math_text_objects": self.math_objects,
        }


def system_overview(language: str) -> PaperFigure:
    f = PaperFigure("01_system_overview", "BeliefKV Execution and KV Coordination", HEIGHT, language)
    f.heading(
        "BeliefKV: Execution and KV Coordination",
        "BeliefKV：动态 Agent 执行与 KV 协同管理",
        "Who executes next?   →   What stays or moves?   →   When is it served?",
        "谁即将执行？  →  哪些缓存应保留或迁移？  →  怎样及时消费？",
    )
    f.panel(40, 132, 1720, 202, f.tr("(a) Observed workflow", "(a) 已观测工作流"))
    f.token(111, 257, "P", PINK)
    f.token(307, 222, "C1", TEAL_PALE)
    f.token(307, 294, "C2", AMBER_PALE)
    f.terminal(443, 197, 109, 52)
    f.text(448, 283, "Tool", 23)
    f.diamond(708, 257, 124, 82, PAPER, INK)
    f.centered(708, 266, "JOIN", size=25, bold=True)
    f.token(872, 257, "P", PINK)
    for points, color in (
        (((140, 247), (277, 224)), INK),
        (((140, 269), (277, 293)), INK),
        (((337, 222), (441, 222)), INK),
        (((554, 222), (605, 222), (651, 245)), INK),
        (((337, 294), (605, 294), (651, 270)), INK),
        (((771, 257), (842, 257)), INK),
    ):
        f.arrow(points, color=color)
    f.text(159, 215, "SPAWN", 23)
    f.text(557, 322, "RETURN", 23)
    f.arrow(((899, 265), (983, 265), (983, 310), (872, 310), (872, 288)),
            dashed=True)
    f.text(905, 222, f.tr("Round 2", "下一轮"), 23, MUTED)
    f.line(1030, 200, 1030, 316, RULE, 1.4)
    for index, label in enumerate(("SPAWN", "WAIT", "RETURN")):
        f.rect(1072 + index * 6, 213 + index * 25, 128, 29, PAPER, INK, 1.3, 1)
        f.text(1082 + index * 6, 234 + index * 25, label, 19)
    f.document(1270, 205, 114, 88, color=AMBER_PALE)
    f.path(((1480, 275), (1500, 275), (1500, 255), (1524, 255),
            (1524, 238), (1551, 238), (1551, 212), (1580, 212)), BLUE, 3)
    f.clock(1661, 245, 25)
    f.centered(1139, 320, f.tr("Events", "依赖事件"), size=23)
    f.centered(1327, 320, f.tr("Text + notice", "正文 + 通知"), size=23)
    f.centered(1576, 320, f.tr("Decode progress", "生成进度"), size=23)
    f.arrow(((1140, 334), (1140, 359), (310, 359), (310, 393)),
            color=BLUE, dashed=True)
    f.arrow(((1327, 334), (1327, 375), (454, 375), (454, 393)),
            color=BLUE, dashed=True)

    for x, title in zip((40, 630, 1220), (
        f.tr("1  Execution demand", "1  执行需求预测"),
        f.tr("2  KV lifecycle", "2  KV 生命周期"),
        f.tr("3  Admission + prefetch", "3  准入与预取协同"),
    )):
        f.panel(x, 394, 540, 482, title)

    f.text(70, 489, "RCCG", 27, INK, 700)
    f.token(103, 586, "P", PINK)
    f.token(224, 534, "C1", TEAL_PALE)
    f.token(224, 656, "C2", AMBER_PALE)
    f.arrow(((131, 574), (194, 546)))
    f.arrow(((128, 603), (196, 641)))
    f.check(253, 507, color=INK, size=20)
    f.clock(260, 654, 16)
    f.text(70, 722, "GraphDelta", 23, BLUE)
    f.rect(319, 472, 234, 268, PAPER, INK, 1.5, 4, "7 5")
    f.text(335, 504, f.tr("Async CPU", "异步 CPU"), 24, INK, 700)
    for column in range(3):
        for row in range(3):
            f.circle(359 + column * 53, 543 + row * 32, 9,
                     BLUE_PALE if column < 2 else AMBER_PALE, INK, 1.3)
            if column:
                f.line(359 + (column - 1) * 53 + 10, 543 + row * 32,
                       359 + column * 53 - 10, 543 + row * 32, INK, 1.3)
    f.arrow(((253, 656), (294, 656), (294, 585), (318, 585)), color=BLUE, dashed=True)
    f.math(436, 655, [("p", "final")], size=29, anchor="middle")
    f.interval(352, 520, 686, color=INK, center=448)
    f.math(436, 724, ["[", ("W", "lo"), ", ", ("W", "hi"), "]"],
           size=27, anchor="middle")
    f.arrow(((224, 685), (224, 757), (168, 757), (168, 780)))
    f.arrow(((436, 742), (436, 779)), color=RED)
    f.ticket(70, 782, "P / A", PINK, w=483, h=65)
    f.centered(311, 824, f.tr("Next consumer", "下一消费者"), size=25)
    f.arrow(((582, 813), (628, 813)), color=RED)

    f.bank(660, 518, 202, 88, [PINK, PINK, TEAL_PALE, AMBER_PALE], label="HBM")
    f.bank(943, 518, 202, 88, [PINK, PINK, PINK, AMBER_PALE], label="Host", missing=(2, 3))
    f.arrow(((866, 535), (938, 535)), color=RED)
    f.text(871, 522, "D2H", 20)
    f.arrow(((939, 590), (867, 590)), color=RED)
    f.text(871, 626, "H2D", 20)
    for x, label, rows in (
        (668, "HBM", 1), (846, "H+Host", 2), (1023, "Host-only", 1),
    ):
        for row in range(rows):
            for column in range(3):
                f.page(x + column * 28, 669 + row * 24, fill=PINK, w=23, h=19)
        f.centered(x + 40, 735, label, size=21)
    f.arrow(((754, 680), (839, 680)), color=RED)
    f.arrow(((932, 680), (1017, 680)), color=RED)
    f.text(760, 660, "prepare", 20)
    f.text(940, 660, f.tr("pressure", "压力"), 20)
    f.page(679, 788, fill=PINK, w=39, h=41)
    f.page(724, 788, fill=PINK, w=39, h=41)
    f.math(792, 818, ["ΔFULL"], size=25)
    f.centered(931, 816, "+", size=32)
    f.snapshot(985, 779, scale=.62)
    f.math(1100, 816, [("s", None, "*")], size=30)
    f.arrow(((1171, 813), (1218, 813)), color=RED)

    f.text(1250, 489, f.tr("Native waiting queue", "原生等待队列"), 25, INK, 700)
    f.ticket(1250, 515, "old", BACKPLATE, w=208)
    f.ticket(1250, 610, "hit", TEAL_PALE, w=208, resident=True)
    f.ticket(1250, 705, "P", PINK, w=208, resident=True, protected=True)
    f.text(1250, 597, f.tr("aging", "老化"), 22, MUTED)
    f.text(1250, 693, f.tr("HBM-resident", "HBM 命中"), 22, MUTED)
    f.text(1250, 788, f.tr("restored", "恢复就绪"), 22, MUTED)
    f.gpu(1570, 588, 140, 133, running=[PINK, TEAL_PALE, AMBER_PALE, PAPER, PINK, PAPER])
    f.arrow(((1461, 541), (1514, 541), (1514, 609), (1557, 609)), dashed=True)
    f.arrow(((1461, 636), (1557, 636)))
    f.arrow(((1461, 731), (1514, 731), (1514, 693), (1557, 693)), color=RED)
    f.check(1536, 788, color=INK, size=17)
    f.text(1562, 807, "KV ∧ slot", 25)
    f.text(1531, 851, "PrefillAdder", 24, INK, 700)

    f.panel(40, 938, 1720, 206, f.tr("(b) Native SGLang data plane", "(b) 原生 SGLang 数据路径"))
    f.arrow(((899, 876), (899, 910), (587, 910), (587, 987)), color=RED)
    f.arrow(((1628, 876), (1628, 936)), color=RED)
    for x, y, fill in ((110, 1049, PINK), (222, 1018, TEAL_PALE), (222, 1092, AMBER_PALE)):
        f.page(x, y - 14, fill=fill, w=50, h=29)
    f.line(160, 1048, 219, 1020, INK, 2)
    f.line(160, 1050, 219, 1092, INK, 2)
    f.text(301, 1031, "Unified", 25, INK, 700)
    f.text(301, 1068, "RadixCache", 25, INK, 700)
    f.arrow(((457, 1051), (505, 1051)))
    f.dma(512, 1008, 162, 87, label="HiCache")
    f.arrow(((681, 1051), (741, 1051)))
    f.bank(751, 1027, 235, 63, [PINK, TEAL_PALE, AMBER_PALE, PINK])
    f.snapshot(1019, 1027, scale=.75)
    f.text(802, 1126, "FULL", 23)
    f.text(1016, 1126, "Mamba", 23)
    f.arrow(((1105, 1051), (1160, 1051)))
    for index in range(3):
        f.ticket(1175, 1010 + index * 30, f"r{index}", BACKPLATE, w=170, h=25, resident=True)
    f.arrow(((1353, 1051), (1406, 1051)))
    f.gpu(1444, 1005, 137, 114, running=[PINK, TEAL_PALE, PINK, AMBER_PALE, PAPER, PAPER])
    f.text(1610, 1040, "Native", 24, INK, 700)
    f.text(1610, 1073, "batch", 24, INK, 700)
    f.arrow(((1730, 1123), (1730, 1164), (20, 1164), (20, 655), (39, 655)),
            color=BLUE, dashed=True, width=2)
    for x, fill, label in (
        (70, PINK, "Parent"), (281, TEAL_PALE, "Child 1"), (490, AMBER_PALE, "Child 2"),
    ):
        f.rect(x, 1176, 25, 19, fill, INK, 1.3, 1)
        f.text(x + 36, 1187, label, 21)
    f.arrow(((757, 1184), (801, 1184)), color=RED, width=2)
    f.text(817, 1187, f.tr("Action", "控制动作"), 21)
    f.arrow(((1064, 1184), (1108, 1184)), color=BLUE, dashed=True, width=2)
    f.text(1125, 1187, "ACK / service / reuse", 21)
    return f


def prediction_and_overlap(language: str) -> PaperFigure:
    f = PaperFigure("02_prediction_and_overlap", "Event and Content-driven Execution Demand", HEIGHT, language)
    f.heading(
        "From Completion Signals to Near-term Demand",
        "事件与内容驱动的执行需求预测",
        "Delivered content + service progress → separate phase / work heads → runtime timing",
        "已送达正文 + 服务进度 → 阶段 / 工作量双头 → Runtime 决定启动时机",
    )
    f.panel(40, 132, 1110, 440, f.tr("(a) Asynchronous child model", "(a) 异步 Child 模型"))
    f.document(76, 238, 145, 123, color=AMBER_PALE)
    f.math(148, 219, [("x", "t−k:t")], size=30, anchor="middle")
    f.arrow(((223, 298), (291, 298)))
    layer_x = (315, 398, 481)
    for left, right in zip(layer_x, layer_x[1:]):
        for source in range(3):
            for target in range(3):
                f.line(left + 11, 250 + source * 45,
                       right - 11, 250 + target * 45, RULE, 1.1)
    for column, xx in enumerate(layer_x):
        for row in range(3):
            f.circle(xx, 250 + row * 45, 12,
                     BLUE_PALE if column != 2 else TEAL_PALE, INK, 1.6)
    f.lock(471, 197, scale=.75)
    f.centered(398, 399, f.tr("Frozen encoder", "冻结编码器"), size=24, bold=True)
    f.arrow(((496, 297), (553, 297)))
    for index, fill in enumerate((BLUE_PALE, AMBER_PALE, BLUE_PALE, TEAL_PALE, PINK, BLUE_PALE)):
        f.rect(568, 234 + index * 20, 30, 17, fill, INK, 1.1, 0)
    f.math(583, 395, [("z", "t")], size=30, anchor="middle")
    f.circle(668, 297, 26, PAPER, INK, 1.8)
    f.math(668, 307, ["∥"], size=30, anchor="middle")
    f.arrow(((601, 297), (639, 297)))

    f.polygon(((82, 448), (215, 448), (215, 504), (145, 504),
               (126, 520), (126, 504), (82, 504)), AMBER_PALE, INK)
    f.centered(148, 483, f.tr("Notice", "收尾通知"), size=24, bold=True)
    f.path(((310, 504), (335, 504), (335, 488), (365, 488),
            (365, 470), (396, 470), (396, 450), (422, 450)), BLUE, 2.7)
    f.math(361, 538, [("N", "t"), ", { }"], size=29, anchor="middle")
    f.arrow(((217, 477), (258, 477), (258, 418), (668, 418)))
    f.arrow(((427, 477), (668, 477), (668, 327)))
    f.arrow(((697, 297), (730, 297), (730, 256), (777, 256)), color=RED)
    f.arrow(((730, 297), (730, 442), (777, 442)), color=RED)
    for yy, title in (
        (220, f.tr("Phase", "阶段头")), (405, f.tr("Work", "工作量头")),
    ):
        for row in range(3):
            for column in range(3):
                f.rect(790 + column * 15, yy + row * 15, 11, 11,
                       BLUE_PALE if row != column else AMBER_PALE, INK, .8, 0)
        f.text(850, yy + 31, title, 25, INK, 700)
    f.arrow(((926, 255), (958, 255)))
    f.arrow(((935, 440), (964, 440)))
    for xx, height, label, fill in (
        (982, 25, "W", BLUE_PALE), (1031, 43, "N", AMBER_PALE),
        (1080, 83, "F", PINK),
    ):
        f.rect(xx, 321 - height, 29, height, fill, INK, 1.5, 0)
        f.centered(xx + 14, 352, label, size=21)
    f.math(1046, 214, [("p", "phase")], size=29, anchor="middle")
    f.interval(978, 1116, 440, color=INK, center=1033)
    f.math(1046, 498, ["[", ("W", "lo"), ", ", ("W", "c"), ", ", ("W", "hi"), "]"],
           size=26, anchor="middle")
    f.arrow(((1080, 548), (54, 548), (54, 310), (75, 310)),
            color=BLUE, dashed=True, width=1.8)
    f.rect(518, 531, 214, 30, GRAY_PALE, "none", 0, 0)
    f.math(625, 554, [("t",), " → ", ("t",), " + Δ", ("t",)],
           size=26, color=BLUE, anchor="middle")

    f.panel(1190, 132, 570, 440, f.tr("(b) Direct JOIN", "(b) 直接 JOIN"))
    f.token(1248, 307, "P", PINK, r=25, size=24)
    f.token(1383, 243, "C1", TEAL_PALE, r=25, size=23)
    f.token(1383, 366, "C2", AMBER_PALE, r=25, size=23)
    f.check(1418, 218, color=INK, size=20)
    f.diamond(1565, 307, 106, 77, PAPER, INK)
    f.centered(1565, 316, "ALL", size=25, bold=True)
    f.token(1700, 307, "P", PINK, r=25, size=24)
    for points, color in (
        (((1274, 294), (1356, 253)), INK),
        (((1274, 321), (1356, 355)), INK),
        (((1411, 243), (1466, 243), (1517, 292)), GRAY),
        (((1411, 366), (1466, 366), (1517, 321)), RED),
        (((1621, 307), (1671, 307)), RED),
    ):
        f.arrow(points, color=color)
    f.text(1244, 426, "pending = {C2}", 26, INK, 700)
    f.math(1238, 493, [("τ", "join"), " = "], size=34)
    f.fraction(1374, 480, ["[", ("W",), " − Δ", ("N",), ("]", "+")],
               [("v",), "(", ("t",), ")"], size=33)
    f.text(1238, 551, f.tr("Observed service rate; queue separate",
                          "实测服务速率；排队单独处理"), 22, MUTED)
    f.arrow(((1119, 440), (1212, 440), (1212, 480), (1233, 480)), color=RED)
    f.arrow(((1502, 573), (1502, 595), (1780, 595), (1780, 840),
             (1282, 840), (1282, 908)), color=RED)

    f.panel(40, 618, 1720, 206, f.tr("(c) Tool timing conditioned on survival",
                                                 "(c) 仍在执行条件下的工具时机"))
    f.clock(103, 718, 29)
    f.math(103, 783, [("a",), "(", ("t",), ")"], size=30, anchor="middle")
    f.terminal(184, 691, 116, 65)
    f.text(177, 793, "cmd / project", 22)
    f.arrow(((135, 718), (181, 718)))
    f.arrow(((303, 718), (378, 718)))
    for index in (2, 1, 0):
        f.rect(389 + index * 8, 695 - index * 8, 180, 58, PAPER, INK, 1.5, 2)
    f.centered(479, 733, f.tr("History", "完成历史"), size=25, bold=True)
    f.check(554, 671, color=INK, size=17)
    f.arrow(((591, 718), (655, 718)))
    f.rect(794, 677, 173, 101, TEAL_PALE, "none", 0, 0)
    f.line(682, 779, 968, 779, INK, 1.8)
    f.line(682, 779, 682, 680, INK, 1.8)
    f.path(((683, 778), (717, 778), (717, 760), (751, 760),
            (751, 740), (794, 740), (794, 710), (841, 710),
            (841, 691), (909, 691), (909, 680), (965, 680)), INK, 2.6)
    f.line(793, 681, 793, 785, RED, 2, "6 4")
    f.math(793, 810, [("a",)], size=25, color=RED, anchor="middle")
    f.math(948, 810, [("T",)], size=25)
    f.math(672, 691, [("F",)], size=24, anchor="end")
    f.arrow(((987, 718), (1036, 718)))
    f.math(1060, 713, ["P(", ("T",), " ≤ ", ("a",), " + ", ("r",),
                       " | ", ("T",), " > ", ("a",), ")"], size=34)
    f.interval(1090, 1471, 762, color=INK, center=1259)
    for xx, label in ((1090, "10"), (1259, "50"), (1471, "90")):
        f.math(xx, 805, [("q", label)], size=27, anchor="middle")
    f.arrow(((1490, 744), (1553, 744)), color=RED)
    f.math(1647, 753, [("τ", "tool"), "(", ("t",), ")"], size=35, anchor="middle")
    f.line(1647, 825, 1647, 840, RED, 2.6)
    f.circle(1647, 840, 4, RED, RED, 1)

    f.panel(40, 864, 1720, 132, f.tr("(d) Runtime transfer window", "(d) Runtime 传输窗口"))
    f.text(64, 958, "ACK history", 25, INK, 700)
    for index, height in enumerate((19, 29, 23, 39)):
        f.rect(242 + index * 15, 974 - height, 10, height, BLUE_PALE, INK, 1, 0)
    f.arrow(((310, 946), (358, 946)))
    f.math(386, 958, [
        ("H",), " = min(", ("L",), ", ", ("S", "90"), " + ",
        ("Q", "90"), " + ", ("Δ", "obs"), " + ", ("ε", "early"), ")",
    ], size=34)
    f.arrow(((1091, 946), (1168, 946)), color=RED)
    f.diamond(1282, 946, 218, 72, PAPER, INK)
    f.math(1282, 958, [("τ",), " ≤ ", ("H",)], size=34, anchor="middle")
    f.arrow(((1395, 946), (1550, 946)), color=RED)
    f.dma(1567, 925, 165, 58, label="H2D")

    left, trigger, boundary, ack, serve, native_serve, right = 360, 900, 1160, 1300, 1460, 1560, 1760
    f.line(boundary, 1029, boundary, 1190, GRAY, 1.7, "6 5")
    f.text(boundary + 12, 1025, "RETURN / TOOL_END", 22, INK, 700)
    f.line(trigger, 1028, boundary - 4, 1028, RED, 2)
    f.line(trigger, 1022, trigger, 1033, RED, 2)
    f.math(1020, 1021, ["Δ", ("t",)], size=25, color=RED, anchor="middle")
    for y, label in (
        (1056, "child / tool"), (1114, f.tr("prefetch", "预取")),
        (1172, f.tr("reactive", "响应式")),
    ):
        f.text(65, y + 8, label, 26, INK, 700)
        f.line(left, y, right, y, RULE, 1)
    f.rect(left, 1039, boundary - left, 34, AMBER_PALE, INK, 1.5, 1)
    f.centered(704, 1064, "decode / tool", size=25)
    f.rect(trigger, 1097, ack - trigger, 34, YELLOW, INK, 1.5, 1)
    f.centered(1080, 1122, "H2D", size=25)
    f.rect(ack, 1097, serve - ack, 34, BACKPLATE, INK, 1.3, 1)
    f.rect(serve, 1097, right - serve, 34, BLUE_PALE, INK, 1.5, 1)
    f.centered(1610, 1122, "GPU", size=25)
    f.text(ack + 4, 1090, "ACK", 21)
    f.text(serve + 7, 1090, f.tr("First use", "首次复用"), 21)
    f.rect(boundary, 1155, native_serve - boundary, 34, YELLOW, INK, 1.5, 1)
    f.centered((boundary + native_serve) / 2, 1180, "H2D", size=25)
    f.rect(native_serve, 1155, right - native_serve, 34, BLUE_PALE, INK, 1.5, 1)
    f.centered(1660, 1180, "GPU", size=25)
    return f


def pages_and_handoff(language: str) -> PaperFigure:
    f = PaperFigure("03_pages_and_capacity_handoff", "Workflow-aware KV Lifecycle and Admission", HEIGHT, language)
    f.heading(
        "KV Lifecycle and Restore-aware Admission",
        "工作流感知的 KV 生命周期与恢复感知准入",
        "Stable FULL extents · checkpoint state · missing pages · child-to-parent capacity",
        "稳定 FULL 前缀 · checkpoint 状态 · 缺失页恢复 · child → parent 容量交接",
    )
    f.panel(40, 132, 1040, 285, f.tr("(a) Stable FULL: incremental PREPARE_HOST",
                                                   "(a) 稳定 FULL：增量 PREPARE_HOST"))
    f.text(68, 244, "HBM", 28, INK, 700)
    f.text(68, 327, "Host", 28, INK, 700)
    for i in range(8):
        x = 213 + i * 86
        f.page(x, 213, f"p{i}", w=64, h=44)
        f.page(x, 296, f"p{i}", fill=PINK if i < 4 else PAPER,
               stroke=INK if i < 4 else GRAY, dashed=i >= 4, w=64, h=44)
        if i >= 4:
            f.arrow(((x + 32, 259), (x + 32, 291)), color=RED, width=2)
    f.math(968, 285, ["ΔFULL"], size=26)
    for xx, label, rows in (
        (90, "HBM", 1), (449, "HBM + Host", 2), (840, "Host-only", 1),
    ):
        for row in range(rows):
            for column in range(3):
                f.page(xx + column * 22, 362 + row * 15, fill=PINK, w=17, h=12)
        f.text(xx + 85, 386, label, 22)
    f.arrow(((267, 378), (438, 378)), color=RED)
    f.text(307, 363, "prepare", 20)
    f.arrow(((704, 378), (827, 378)), color=RED)
    f.text(718, 363, f.tr("pressure", "真实压力"), 20)

    f.panel(1120, 132, 640, 285, f.tr("(b) Required Mamba state", "(b) 必要 Mamba 状态"))
    for xx, sub, faded in ((1150, "0", True), (1340, "1", True), (1556, None, False)):
        f.snapshot(xx, 226, scale=.82, faded=faded)
        f.math(xx + 34, 306, [("s", sub, "*" if sub is None else None)],
               size=30, color=GRAY if faded else INK, anchor="middle")
    f.arrow(((1233, 249), (1324, 249)), color=GRAY, width=2)
    f.arrow(((1424, 249), (1541, 249)), color=GRAY, width=2)
    f.text(1427, 219, "decode", 21, MUTED)
    f.math(1697, 257, [("c", None, "*")], size=32, anchor="middle")
    f.line(1640, 250, 1668, 250, INK, 1.6)
    f.text(1148, 382, "PREPARE", 24, MUTED)
    f.cross(1300, 357, color=RED, size=24)
    f.snapshot(1556, 354, scale=.6)
    f.text(1400, 392, "Host", 25, INK, 700)
    f.arrow(((1580, 342), (1580, 317)), color=RED, width=2)
    f.text(1633, 365, "H2D", 22)

    f.panel(40, 459, 1720, 326, f.tr("(c) Safe-input checkpoint → missing pages → native layer pipeline",
                                                   "(c) 安全输入 checkpoint → 缺失页 → 原生逐层流水线"))
    f.text(67, 550, f.tr("Native radix tree", "原生前缀树"), 24, INK, 700)
    for xx, yy, label, fill in (
        (81, 605, "p0", BACKPLATE), (218, 565, "p1", PINK),
        (350, 605, "p5", PINK), (218, 682, "g", BACKPLATE),
    ):
        f.page(xx, yy, label, fill=fill, w=56, h=43, dashed=label == "g")
    f.arrow(((138, 621), (215, 590)))
    f.arrow(((275, 590), (347, 620)), color=RED)
    f.arrow(((131, 649), (217, 697)), color=GRAY, dashed=True)
    f.cross(285, 692, color=GRAY, size=20)
    f.lock(90, 655, scale=.65)
    f.math(377, 588, [("c", None, "*")], size=31, anchor="middle")
    f.text(67, 754, "session / epoch", 23, MUTED)
    f.arrow(((409, 625), (494, 625)), color=RED)

    f.text(511, 571, "Host", 26, INK, 700)
    f.text(511, 654, "HBM", 26, INK, 700)
    for i in range(6):
        x = 625 + i * 63
        f.page(x, 543, f"p{i}", fill=BACKPLATE if i == 0 else PINK, w=51, h=42)
        f.page(x, 626, f"p{i}", fill=BACKPLATE if i == 0 else PINK if i < 4 else PAPER,
               stroke=INK if i < 4 else GRAY, dashed=i >= 4, w=51, h=42)
        if 2 <= i < 4:
            f.arrow(((x + 25, 589), (x + 25, 620)), color=RED, width=2)
    f.line(625, 682, 739, 682, INK, 1.6)
    f.line(751, 682, 865, 682, RED, 2.4)
    f.text(629, 715, f.tr("hit", "已驻留"), 20)
    f.text(753, 715, f.tr("restore", "本次恢复"), 20, RED)
    f.text(887, 715, f.tr("defer", "延后"), 20, MUTED)
    f.math(512, 760, [
        ("B",), " ≤ ", ("C", "free"), " + ", ("C", "cold"), " − ", ("G", "next"),
    ], size=31)
    f.arrow(((1006, 647), (1040, 647), (1040, 602), (1090, 602)), color=RED)
    f.dma(1101, 551, 163, 101, label="HiCache")
    f.snapshot(1124, 698, scale=.53)
    f.math(1215, 725, [("s", None, "*")], size=31)
    f.arrow(((1266, 602), (1328, 602), (1328, 651)), color=RED)
    f.arrow(((1252, 715), (1328, 715), (1328, 683)), color=RED)
    f.circle(1328, 667, 18, PAPER, INK, 1.8)
    f.math(1328, 676, ["∧"], size=28, anchor="middle")
    f.arrow(((1349, 667), (1393, 667), (1393, 561), (1453, 561)))
    for index in range(3):
        yy = 534 + index * 78
        f.rect(1457, yy, 263, 55, BLUE_PALE, INK, 1.8, 3)
        f.math(1488, yy + 35, [("ℓ", str(index))], size=30)
        for column in range(4):
            f.rect(1550 + column * 37, yy + 16, 27, 25, PAPER, INK, 1.1, 1)
        if index < 2:
            f.arrow(((1590, yy + 57), (1590, yy + 75)), width=2)
    f.arrow(((1183, 654), (1183, 673), (1032, 673), (1032, 766), (1450, 766)),
            color=BLUE, dashed=True, width=1.7)
    f.text(1353, 754, "ACK", 21, BLUE)

    f.panel(40, 830, 1070, 326, f.tr("(d) Terminal child → native free capacity",
                                                   "(d) 终态 child → 原生可用容量"))
    f.token(100, 979, "P", PINK)
    f.token(253, 925, "C1", TEAL_PALE)
    f.token(253, 1080, "C2", AMBER_PALE)
    f.arrow(((128, 969), (224, 935)))
    f.arrow(((125, 994), (226, 1064)))
    f.check(287, 898, color=INK, size=18)
    f.arrow(((283, 925), (351, 925)))
    for index in range(4):
        f.page(361 + index * 45, 906, fill=BACKPLATE if index == 0 else TEAL_PALE,
               w=36, h=34)
    f.lock(369, 946, scale=.6)
    f.text(423, 977, f.tr("private", "私有后缀"), 21)
    f.arrow(((548, 923), (635, 923)), color=RED)
    f.rect(648, 901, 123, 48, PAPER, INK, 1.7, 2)
    for index in range(4):
        f.rect(657 + index * 28, 913, 20, 24, PAPER, INK, 1.2, 0)
    f.text(648, 979, f.tr("Free list", "空闲列表"), 22, INK, 700)
    f.arrow(((776, 923), (871, 923)), color=RED)
    for index, label in enumerate(("q0", "q1", "q2")):
        f.page(886 + index * 61, 903, label, w=50, h=43)
    f.text(894, 977, "Parent KV", 23, INK, 700)
    f.line(931, 1012, 931, 1140, GRAY, 1.7, "6 5")
    f.text(942, 1030, "RETURN", 21, INK, 700)
    f.rect(371, 1102, 560, 32, AMBER_PALE, INK, 1.6, 1)
    f.centered(628, 1125, "C2: decode", size=23)
    f.rect(721, 1048, 181, 33, YELLOW, INK, 1.5, 1)
    f.centered(811, 1072, "H2D", size=24)
    f.rect(933, 1048, 148, 33, BACKPLATE, INK, 1.5, 1)
    f.centered(1007, 1072, "demand", size=21)
    f.arrow(((648, 941), (615, 941), (615, 1001), (811, 1001), (811, 1044)), color=RED)
    f.lock(242, 1114, scale=.65)

    f.panel(1150, 830, 610, 326, f.tr("(e) Timely consumption", "(e) 恢复后及时消费"))
    f.ticket(1181, 906, "old", BACKPLATE, w=196)
    f.ticket(1181, 984, "hit", TEAL_PALE, w=196, resident=True)
    f.ticket(1181, 1062, "P", PINK, w=196, resident=True, protected=True)
    f.clock(1402, 925, 15)
    f.text(1426, 933, f.tr("aging", "老化"), 21, MUTED)
    f.gpu(1560, 969, 141, 135,
          running=[PINK, TEAL_PALE, BACKPLATE, PINK, AMBER_PALE, PAPER])
    f.arrow(((1381, 947), (1517, 947), (1517, 987), (1548, 987)), dashed=True, width=2)
    f.arrow(((1381, 1010), (1548, 1010)))
    f.arrow(((1381, 1088), (1517, 1088), (1517, 1074), (1548, 1074)), color=RED)
    f.text(1390, 1140, "KV ∧ slot  /  batch fill", 23)
    for x, fill, dashed, label in (
        (65, PINK, False, f.tr("Parent pages", "Parent 页")),
        (352, PAPER, True, f.tr("Missing / deferred", "缺失 / 延后")),
        (718, TEAL_PALE, False, f.tr("Child private pages", "Child 私有页")),
    ):
        f.page(x, 1174, fill=fill, dashed=dashed, w=28, h=22)
        f.text(x + 42, 1187, label, 21)
    f.arrow(((1220, 1183), (1263, 1183)), color=RED, width=2)
    f.text(1281, 1187, f.tr("Action", "控制动作"), 21)
    f.arrow(((1474, 1183), (1517, 1183)), color=BLUE, dashed=True, width=2)
    f.text(1534, 1187, "ACK", 21)
    return f


def export_collection(figures, directory: Path):
    destination = directory / "beliefkv_figures_bilingual.pdf"
    subprocess.run(
        ["pdfunite", *(str(directory / figure.filename.replace(".svg", ".pdf"))
                       for figure in figures), str(destination)],
        check=True,
    )


def slide_notes(figure: PaperFigure):
    if figure.filename.startswith("01_"):
        return figure.tr(
            "Architecture baseline: 2026-10-15 development implementation, not the frozen "
            "code of every running experiment. RCCG receives runtime events; GraphDelta "
            "updates affected wait groups. Text/phase/work inference runs in an independent "
            "CPU process. Tool history supplies separate conditional timing. "
            "NativeAdmissionRuntime combines causal identity, checkpoint, timing, capacity "
            "and next-batch observations. Waiting migrations can run after forward dispatch "
            "under the supported overlap configuration. The native waiting queue, "
            "PrefillAdder, allocation and data dependencies remain authoritative. "
            "Dashed arrows show feedback, not a claim that all control work is asynchronous. "
            "Explicit completion notices are harness protocol and must be identical "
            "across comparison arms. Root workflows can spawn again after JOIN; the current "
            "child harness does not expose nested task delegation. Colors identify memory "
            "owners, while location and solid/dashed borders indicate residency. "
            "Sources: causal_graph.py; semantic_report_worker.py; sglang_v0520_runtime.py "
            "(scheduler_step, dispatch_after_forward, plan_native_prefill); staging patch.",
            "架构基线：2026-10-15 当前开发实现，不代表所有运行实验的冻结代码。"
            "RCCG 接收运行时事件，GraphDelta 增量更新受影响的等待成员组。"
            "文本阶段/工作量推理在独立 CPU 进程执行，工具历史提供条件时机提示。"
            "NativeAdmissionRuntime 联合因果身份、checkpoint、时机、容量和下一批观测。"
            "受支持的 overlap 配置下，等待迁移规划安排在 forward 发射后。"
            "原生可见等待队列、PrefillAdder、分配器和数据依赖保持权威。"
            "虚线表达观测反馈，不表示所有控制工作均异步。"
            "显式完成通知属于 harness 协议，应在所有对照组保持一致。"
            "root 可以在 JOIN 后再次 spawn；当前 child harness 不提供嵌套 task 委派。"
            "页和请求票据的颜色表示归属，驻留状态由位置及实虚边框表达。"
            "代码：causal_graph.py、semantic_report_worker.py、sglang_v0520_runtime.py "
            "的 scheduler_step / dispatch_after_forward / plan_native_prefill，以及 staging patch。",
        )
    if figure.filename.startswith("02_"):
        return figure.tr(
            "The stage model uses frozen pooled text features plus event/progress features; "
            "the conditional work head can be fitted independently. The circle marked "
            "parallel bars denotes feature concatenation, not addition. The encoder "
            "network and head matrices are schematic, not literal layer counts. The "
            "phase and work paths share frozen encoder weights; a configured work-only "
            "text window can use a separate encoding of delivered text. Work bounds are "
            "calibrated bounds, not a calibrated joint JOIN interval. The current "
            "content-driven JOIN_ALL path targets the single pending child; earlier notices "
            "can be retained and rebound after sibling RETURN. A latest forecast can move "
            "its work endpoint in either direction before enqueue. The center-overrun path "
            "uses a still-valid upper endpoint. max(W-deltaN, 0)/rate estimates generation time, not future "
            "queueing or a guaranteed wall-clock RETURN. No-tool EOS is separate evidence. "
            "Tool CDFs are conditioned on survival and use completed-call history with "
            "fallbacks; the current live tool-transfer path requires at most one active call. "
            "H follows transfer_start_window: min(lead allowance, ACK P90 + enqueue P90 + "
            "observation spacing + early margin). Cost uses current direction/shape/size "
            "history. A smaller same-checkpoint payload can retain the measured cost of its "
            "larger supported selection when its own direct estimate is unavailable. "
            "The timeline is schematic and allows partial overlap. "
            "Sources: child_semantic_work.py; conditional_work.py; native_transfer_policy.py; "
            "native_transfer_service.py; _roll_final_stage / _roll_tool_prefetch / "
            "_wait_prefetch_timing_valid / _h2d_start_window in sglang_v0520_runtime.py.",
            "阶段模型融合冻结文本表示与事件/进度特征，条件剩余工作头可独立拟合。"
            "圆内平行线表示特征拼接，不是相加；网络和权重矩阵图形不表示实际层数。"
            "两头共享冻结编码器权重，配置工作头独立文本窗口时可另外编码已送达正文。"
            "工作量区间不是已校准的完整 JOIN 联合区间。当前内容驱动的 JOIN_ALL 路径"
            "关注唯一未返回 child；较早通知可以保留，在 sibling 返回后重新绑定。"
            "新预测可在入队前前移或后移工作端点，center 被越过时仅使用仍有效的 upper。"
            "max(W−ΔN,0)/实测速率是剩余生成时长提示，不包含未来排队，也不是 RETURN 墙钟保证。"
            "无工具 EOS 作为独立证据。工具 CDF 以仍在执行为条件，使用已完成调用历史"
            "及回退；当前工具迁移路径要求至多一个活跃调用。H 严格对应 "
            "transfer_start_window：min(提前驻留上限, ACK P90 + 入队 P90 + 观测间隔 + "
            "提前完成余量)。服务耗时按方向、形态、大小从实际 ACK 历史取得。"
            "同 checkpoint 的计划缩小时，直接估计优先，缺支持可沿用较大已支持计划"
            "的当前成本预算，不按字节缩放固定 ACK 开销。时序仅为机制示意，允许部分重叠。"
            "代码：child_semantic_work.py、conditional_work.py、native_transfer_policy.py、"
            "native_transfer_service.py 与 runtime 的 _roll_final_stage / "
            "_roll_tool_prefetch / _wait_prefetch_timing_valid / _h2d_start_window。",
        )
    return figure.tr(
        "Stable FULL extents are backed incrementally under actual pressure; "
        "PREPARE_HOST neither releases HBM nor speculatively backs Mamba. Native "
        "pressure reclaim can demote backed cold FULL while preserving needed Mamba. "
        "Choose a reusable safe-input checkpoint; restore ancestors root-first and "
        "only the required missing state, rather than every historical Mamba state. "
        "Native allocators decide byte, FULL and Mamba capacity. A page-aligned partial "
        "FULL prefix may load while state or tail remains deferred. "
        "FULL pipeline admission follows native layer dependencies; missing Mamba "
        "or legacy state loads still defer admission as required. ACK manages "
        "transaction/residency lifecycle, not a universal execution barrier. "
        "Bounded locks, restored/resident-first ordering and ordinary-request aging "
        "support consumption but do not guarantee it. Terminal child session retirement "
        "reclaims only unused private suffixes, retries blocked references and keeps "
        "shared ancestors. Released capacity is neither exclusive to the parent nor "
        "a reuse of the same physical page IDs. Earlier siblings may enable pre-RETURN "
        "H2D; the last child's own released capacity supports demand restoration. "
        "Sources: sglang_v0520_physical.py (plan_prefetch_gpu_steps); "
        "native_transfer_policy.py (native_residency_budget); "
        "sglang_v0520_runtime.py (_apply_terminal_child_reclaim, "
        "_wait_prefetch_opportunity, defer_prefill_for_prefetch, plan_native_prefill); "
        "staging patch (retire_beliefkv_child_session, prefetch_gpu_session_nodes).",
        "真实压力下增量备份稳定 FULL 前缀。PREPARE_HOST 不释放 HBM、不投机备份 Mamba；"
        "原生压力回收可释放已备份冷 FULL，并保留仍需使用的 Mamba。"
        "选择可复用的安全输入 checkpoint，从根到叶恢复缺失祖先和必要状态，"
        "并非恢复所有历史 Mamba。FULL、Mamba、字节与输入增长预算分别核算。"
        "容量不足允许页对齐的部分 FULL 恢复，状态或尾段可以延后。"
        "FULL pipeline 按原生逐层依赖准入；必要 Mamba 或旧式加载依赖未就绪时仍等待。"
        "ACK 管理事务和驻留生命周期，不是所有请求执行的统一屏障。"
        "有界原生锁、恢复/HBM命中排序和普通请求老化支持消费，但不保证每次命中。"
        "终态 child 只回收已失去用途的私有后缀，受阻部分重试，共享祖先保留。"
        "回收容量不是 parent 独占预留，也不要求复用 child 原来的物理页号。"
        "较早 sibling 可以支持 RETURN 前 H2D；最后 child 释放容量后属于需求恢复。"
        "代码：physical 的 plan_prefetch_gpu_steps、native_residency_budget、"
        "runtime 的 _apply_terminal_child_reclaim / _wait_prefetch_opportunity / "
        "defer_prefill_for_prefetch / plan_native_prefill，以及 staging patch 对应缓存 API。",
    )


class PowerPointExporter:
    """Map SVG drawing primitives to editable DrawingML objects."""

    def __init__(self, language: str):
        self.language = language
        self.presentation = Presentation()
        self.presentation.slide_width = Inches(14.4)
        self.presentation.slide_height = Inches(14.4 * HEIGHT / WIDTH)
        self.unit = Inches(14.4 / WIDTH)
        self.point_scale = 14.4 * 72 / WIDTH
        self.presentation.core_properties.title = (
            "BeliefKV 系统架构图" if language == "zh" else "BeliefKV Architecture Figures"
        )
        self.presentation.core_properties.author = "BeliefKV"
        self.presentation.core_properties.subject = (
            f"Current development architecture as of {ARCHITECTURE_DATE}"
        )
        self.presentation.core_properties.keywords = (
            "BeliefKV, dynamic agent workflows, KV cache, native editable diagrams"
        )

    def length(self, pixels: float):
        return round(pixels * self.unit)

    @staticmethod
    def rgb(value: str):
        return RGBColor.from_string(value.lstrip("#"))

    def style(self, shape, attributes):
        fill = attributes.get("fill", "none")
        if shape.shape_type != MSO_SHAPE_TYPE.LINE:
            if fill == "none":
                shape.fill.background()
            else:
                shape.fill.solid()
                shape.fill.fore_color.rgb = self.rgb(fill)
        stroke = attributes.get("stroke", "none")
        if stroke == "none":
            shape.line.fill.background()
            return
        shape.line.color.rgb = self.rgb(stroke)
        shape.line.width = Pt(float(attributes.get("stroke-width", 1)) * self.point_scale)
        if "stroke-dasharray" in attributes:
            shape.line.dash_style = MSO_LINE_DASH_STYLE.DASH
        if "marker-end" in attributes:
            line = shape.line._get_or_add_ln()
            arrow = OxmlElement("a:tailEnd")
            arrow.set("type", "triangle")
            arrow.set("w", "lg")
            arrow.set("len", "lg")
            line.append(arrow)

    def add_text(self, slide, element, figure, offset_y):
        value = "".join(element.itertext())
        size = float(element.get("font-size", 24))
        weight = int(element.get("font-weight", 400))
        family = element.get("font-family", figure.family)
        italic = element.get("font-style") == "italic"
        font = drawing_font(figure.language, size, weight, family, italic)
        width = figure.measure(value, size, weight, family, italic)[0]
        ascent, descent = (metric / 4 for metric in font.getmetrics())
        # Keep spare width for Office font substitution; alignment preserves SVG anchors.
        box_width = width + max(12, size * .3)
        x, y = float(element.get("x", 0)), float(element.get("y", 0)) + offset_y
        anchor = element.get("text-anchor", "start")
        left = x - (box_width / 2 if anchor == "middle" else box_width if anchor == "end" else 0)
        shape = slide.shapes.add_textbox(
            self.length(left), self.length(y - ascent),
            self.length(box_width), self.length(ascent + descent + 4),
        )
        shape.name = value[:100]
        frame = shape.text_frame
        frame.margin_left = frame.margin_right = 0
        frame.margin_top = frame.margin_bottom = 0
        frame.vertical_anchor = MSO_ANCHOR.TOP
        frame.word_wrap = False
        paragraph = frame.paragraphs[0]
        paragraph.alignment = {
            "start": PP_ALIGN.LEFT, "middle": PP_ALIGN.CENTER, "end": PP_ALIGN.RIGHT,
        }[anchor]
        paragraph.space_before = paragraph.space_after = Pt(0)
        paragraph.line_spacing = 1.0
        run = paragraph.add_run()
        run.text = value
        run.font.name = family
        run.font.size = Pt(size * self.point_scale)
        run.font.bold = weight >= 600
        run.font.italic = italic
        run.font.color.rgb = self.rgb(element.get("fill", INK))
        properties = run._r.get_or_add_rPr()
        properties.set("lang", "zh-CN" if figure.language == "zh" else "en-US")
        properties.set("spc", "0")
        east_asian = OxmlElement("a:ea")
        east_asian.set("typeface", family)
        properties.append(east_asian)

    def add_primitive(self, slide, element, figure, offset_y):
        tag = element.tag.rsplit("}", 1)[-1]
        attributes = dict(element.attrib)
        if tag == "text":
            self.add_text(slide, element, figure, offset_y)
            return
        if tag == "rect":
            if element.get("width") == "100%":
                return
            shape = slide.shapes.add_shape(
                MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE if float(element.get("rx", 0))
                else MSO_AUTO_SHAPE_TYPE.RECTANGLE,
                self.length(float(element.get("x", 0))),
                self.length(float(element.get("y", 0)) + offset_y),
                self.length(float(element.get("width", 0))),
                self.length(float(element.get("height", 0))),
            )
            if shape.adjustments:
                shape.adjustments[0] = (
                    float(element.get("rx", 0))
                    / min(float(element.get("width")), float(element.get("height")))
                )
        elif tag == "circle":
            radius = float(element.get("r", 0))
            shape = slide.shapes.add_shape(
                MSO_AUTO_SHAPE_TYPE.OVAL,
                self.length(float(element.get("cx", 0)) - radius),
                self.length(float(element.get("cy", 0)) - radius + offset_y),
                self.length(radius * 2), self.length(radius * 2),
            )
        elif tag == "line":
            shape = slide.shapes.add_connector(
                MSO_CONNECTOR_TYPE.STRAIGHT,
                self.length(float(element.get("x1", 0))),
                self.length(float(element.get("y1", 0)) + offset_y),
                self.length(float(element.get("x2", 0))),
                self.length(float(element.get("y2", 0)) + offset_y),
            )
        elif tag in ("path", "polygon"):
            if tag == "path":
                numbers = re.findall(r"[-+]?(?:\d*\.\d+|\d+)", element.get("d", ""))
                if re.sub(r"[-+]?(?:\d*\.\d+|\d+)|[ML\s]", "", element.get("d", "")):
                    raise ValueError("Only M/L paths are supported in editable diagrams")
            else:
                numbers = re.findall(r"[-+]?(?:\d*\.\d+|\d+)", element.get("points", ""))
            points = [
                (float(numbers[index]), float(numbers[index + 1]) + offset_y)
                for index in range(0, len(numbers), 2)
            ]
            if len(points) == 2:
                shape = slide.shapes.add_connector(
                    MSO_CONNECTOR_TYPE.STRAIGHT,
                    *(self.length(coordinate) for point in points for coordinate in point),
                )
            else:
                builder = slide.shapes.build_freeform(*points[0], scale=self.unit)
                builder.add_line_segments(points[1:], close=tag == "polygon")
                shape = builder.convert_to_shape()
        else:
            raise ValueError(f"Unsupported diagram element {tag}")
        shape.name = f"{tag} {len(slide.shapes)}"
        self.style(shape, attributes)
        # Disable theme shadows and other inherited decorative effects.
        for effect in shape._element.xpath(".//a:effectRef"):
            effect.set("idx", "0")
        shape._element.spPr.append(OxmlElement("a:effectLst"))

    def add_figure(self, figure: PaperFigure, directory: Path):
        slide = self.presentation.slides.add_slide(self.presentation.slide_layouts[6])
        slide.background.fill.solid()
        slide.background.fill.fore_color.rgb = self.rgb(PAPER)
        offset_y = (HEIGHT - figure.height) / 2
        root = ET.parse(directory / figure.filename).getroot()
        for element in root:
            if element.tag.rsplit("}", 1)[-1] in ("title", "defs", "style"):
                continue
            self.add_primitive(slide, element, figure, offset_y)
        slide.notes_slide.notes_text_frame.text = slide_notes(figure)

    def save(self, path: Path):
        self.presentation.save(path)
        reopened = Presentation(path)
        slides = []
        for slide in reopened.slides:
            pictures = [shape for shape in slide.shapes if shape.shape_type == MSO_SHAPE_TYPE.PICTURE]
            if pictures:
                raise ValueError("Architecture slide contains a flattened picture")
            for shape in slide.shapes:
                if (
                    shape.left < 0 or shape.top < 0
                    or shape.left + shape.width > reopened.slide_width
                    or shape.top + shape.height > reopened.slide_height
                ):
                    raise ValueError(f"PowerPoint object outside slide: {shape.name!r}")
            slides.append({
                "objects": len(slide.shapes),
                "text_objects": sum(shape.has_text_frame and bool(shape.text) for shape in slide.shapes),
                "picture_objects": len(pictures),
            })
        if len(slides) != 3:
            raise ValueError("Expected three editable architecture slides")
        return {"file": path.name, "language": self.language, "slides": slides}


def export_powerpoints(figures, directory: Path):
    outputs = []
    for language in ("zh", "en"):
        exporter = PowerPointExporter(language)
        for figure in figures:
            if figure.language == language:
                exporter.add_figure(figure, directory)
        outputs.append(exporter.save(directory / f"beliefkv_architecture_editable_{language}.pptx"))
    return outputs


def preview(directory: Path, manifest: list[dict]):
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 23)
    tile_width = 1020
    rows = []
    for row in manifest:
        if row["language"] != "en":
            continue
        with Image.open(directory / (row["stem"] + ".png")) as original:
            image = original.copy()
        image.thumbnail((tile_width - 48, 690), Image.Resampling.LANCZOS)
        tile = Image.new("RGB", (tile_width, image.height + 74), "white")
        ImageDraw.Draw(tile).text((24, 16), row["title"], fill=INK, font=font)
        tile.paste(image, (24, 57))
        rows.append(tile)
    sheet = Image.new("RGB", (tile_width, sum(row.height for row in rows) + 24), "#e8edef")
    position = 8
    for row in rows:
        sheet.paste(row, (0, position))
        position += row.height + 4
    sheet.save(directory / "preview.png")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    figures = [
        builder(language)
        for builder in (system_overview, prediction_and_overlap, pages_and_handoff)
        for language in ("en", "zh")
    ]
    manifest = [figure.save_to(args.output) for figure in figures]
    export_collection(figures, args.output)
    powerpoints = export_powerpoints(figures, args.output)
    preview(args.output, manifest)
    revision = subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True,
    ).strip()
    (args.output / "manifest.json").write_text(json.dumps({
        "source_revision": revision, "architecture_date": ARCHITECTURE_DATE,
        "source_revision_scope": (
            "latest development implementation, not every frozen experiment; "
            "source files and drawing generator are individually fingerprinted"
        ),
        "architecture_source_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in ARCHITECTURE_SOURCES
        },
        "generator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "drawing_helpers_sha256": hashlib.sha256(
            (Path(__file__).parent / "generate_beliefkv_paper_figures.py").read_bytes(),
        ).hexdigest(),
        "schematic_not_empirical_performance": True,
        "visual_style": "gray backplates, black outlines, semantic ownership colors",
        "palette": PALETTE,
        "math_typography": (
            "editable Liberation Serif italic variables; positioned upright subscripts "
            "and superscripts; editable fraction bars; no rasterized equations"
        ),
        "renderer": "CairoSVG / Pillow / python-pptx",
        "figures": manifest, "powerpoints": powerpoints,
    }, indent=2) + "\n")
    print(json.dumps({
        "output": str(args.output), "figures": manifest, "powerpoints": powerpoints,
    }, indent=2))


if __name__ == "__main__":
    main()
