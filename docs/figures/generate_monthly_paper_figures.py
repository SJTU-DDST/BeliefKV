#!/usr/bin/env python3
"""Generate bilingual vector figures and native editable PowerPoint diagrams."""

from __future__ import annotations

import argparse
from functools import lru_cache
import hashlib
from html import escape
from io import BytesIO
import json
import math
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

import cairosvg
from lxml import etree
from PIL import Image, ImageFont
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
CONTROL_PALE = "#e8f1f8"
DATA_PALE = "#eaf2e7"
GREEN_BAND = "#9dc38b"
MATH_FAMILY = "Liberation Serif"
PALETTE = {
    "ink": INK, "gray_backplate": GRAY_PALE, "blue_section": BLUE_PALE,
    "green_section": GREEN_BAND, "control_plane": CONTROL_PALE, "data_plane": DATA_PALE,
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

    def panel(
        self, x, y, w, h, title, *, color=BLUE_PALE, fill=GRAY_PALE,
        size=26, band=38,
    ):
        self.rect(x, y, w, h, fill, INK, 1.8, 2)
        self.rect(x, y, w, band, color, INK, 1.8, 2)
        self.text(x + 14, y + band * .72 + (4 if self.language == "zh" else 0),
                  title, size, INK, 700)

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
                     PAPER, INK)
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
        self.line(x + 8, y + 17, x + w - 8, y + 17, INK, 1)
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
        self.rect(x, y, 79 * scale, 59 * scale, PAPER if faded else fill,
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

    def table(self, x, y, widths, rows, *, row_h=30, size=20, header=True):
        for row_index, values in enumerate(rows):
            xx = x
            for width, value in zip(widths, values):
                self.rect(xx, y + row_index * row_h, width, row_h,
                          BLUE_PALE if header and row_index == 0 else PAPER,
                          INK, 1, 0)
                self.text(xx + 8, y + row_index * row_h + row_h * .71,
                          value, size, INK, 700 if header and row_index == 0 else 400)
                xx += width

    def strip(self, x, y, fills, *, labels=None, w=40, h=30, gap=5, missing=()):
        for index, fill in enumerate(fills):
            self.page(x + index * (w + gap), y,
                      labels[index] if labels else "",
                      fill=PAPER if index in missing else fill,
                      stroke=GRAY if index in missing else INK,
                      dashed=index in missing, w=w, h=h, size=min(21, h * .65))

    def bracket(self, x1, x2, y, *, color=INK, down=False):
        direction = -1 if down else 1
        self.path(((x1, y + 7 * direction), (x1, y),
                   (x2, y), (x2, y + 7 * direction)), color, 1.6)

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
        self.centered(x + w / 2, y + min(29, h * .45) + (4 if self.language == "zh" else 0),
                      label, size=26, bold=True)
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
        self.line(x + 1, y + h * .21, x + w - 1, y + h * .21, INK, 1)
        self.path(((x + 16, y + h * .43), (x + 27, y + h * .61),
                   (x + 16, y + h * .79)), INK, 2)
        self.line(x + 38, y + h * .78, x + w - 14, y + h * .78, INK, 2)

    def junction(self, x, y, *, kind="and", radius=18):
        self.circle(x, y, radius, PAPER, INK, 1.6)
        if kind == "concat":
            for offset in (-4, 4):
                self.line(x + offset, y - 10, x + offset, y + 10, INK, 1.8)
        else:
            self.path(((x - 8, y + 6), (x, y - 8), (x + 8, y + 6)), INK, 1.8)

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
        "BeliefKV: Workflow-aware Execution and KV Management",
        "BeliefKV：工作流感知的执行与 KV 协同管理",
        "Observed dependencies → demand forecasts → page migration and native admission",
        "运行时依赖 → 近期执行需求 → 页级迁移、驻留与原生准入",
    )
    f.panel(32, 124, 1736, 152, f.tr("Agent harness: dynamic requests and completion signals",
                                                   "Agent harness：动态请求与完成信号"))
    f.token(83, 220, "P", PINK, r=24, size=23)
    f.token(245, 194, "C1", TEAL_PALE, r=23, size=22)
    f.token(245, 246, "C2", AMBER_PALE, r=23, size=22)
    f.terminal(347, 175, 95, 40)
    f.diamond(551, 220, 107, 66, PAPER, INK)
    f.centered(551, 229, "JOIN", size=24, bold=True)
    f.token(692, 220, "P", PINK, r=24, size=23)
    for points in (
        ((109, 211), (218, 196)), ((109, 231), (218, 245)),
        ((270, 194), (343, 194)), ((445, 194), (474, 194), (498, 209)),
        ((270, 246), (474, 246), (498, 232)), ((607, 220), (665, 220)),
    ):
        f.arrow(points)
    f.text(128, 185, "SPAWN", 21)
    f.text(347, 271 if language == "zh" else 267, "WAIT_TOOL", 20)
    f.arrow(((719, 226), (804, 226), (804, 257), (692, 257), (692, 248)), dashed=True, width=2)
    f.text(730, 195, f.tr("Next round", "下一轮"), 21)
    f.line(844, 173, 844, 264, RULE, 1.2)
    f.table(866, 174, (122, 92, 136), (
        ("context", "epoch", "request"), ("P / C", "e", "r"),
    ), row_h=30, size=20)
    f.centered(1040, 258, f.tr("Causal identity", "因果身份"), size=21)
    f.document(1255, 172, 96, 77, color=AMBER_PALE)
    f.path(((1411, 245), (1434, 245), (1434, 225), (1460, 225),
            (1460, 209), (1491, 209), (1491, 187), (1521, 187)), BLUE, 2.5)
    f.clock(1588, 207, 23)
    f.centered(1303, 271 if language == "zh" else 267, f.tr("Text / notice", "正文 / 通知"), size=20)
    f.centered(1500, 271 if language == "zh" else 267, f.tr("Service progress", "服务进度"), size=20)
    f.strip(1638, 181, [PINK, AMBER_PALE, PINK], w=26, h=50, gap=6)
    f.text(1633, 260, "tokens", 20)

    f.panel(32, 300, 912, 844, f.tr("BeliefKV control plane", "BeliefKV 控制面"),
            fill=CONTROL_PALE, size=28)
    f.panel(968, 300, 800, 844, f.tr("Native SGLang data plane", "原生 SGLang 数据面"),
            color=GREEN_BAND, fill=DATA_PALE, size=28)
    f.arrow(((1040, 278), (1040, 288), (453, 288), (453, 348)), color=BLUE, dashed=True, width=2)
    f.arrow(((1304, 278), (1304, 294), (707, 294), (707, 348)), color=BLUE, dashed=True, width=2)
    f.panel(48, 350, 418, 316, f.tr("1  Dependency state / RCCG", "1  依赖状态 / RCCG"), size=24)
    f.token(90, 471, "P", PINK, r=23, size=22)
    f.token(206, 422, "C1", TEAL_PALE, r=23, size=22)
    f.token(206, 510, "C2", AMBER_PALE, r=23, size=22)
    f.check(236, 402, color=INK, size=18)
    f.diamond(369, 470, 93, 60, PAPER, INK)
    f.centered(369, 478, "ALL", size=22, bold=True)
    for points in (
        ((115, 461), (181, 432)), ((115, 481), (181, 502)),
        ((232, 422), (288, 422), (325, 456)),
    ):
        f.arrow(points, width=2)
    f.arrow(((232, 510), (288, 510), (325, 483)), color=RED, width=2)
    f.table(64, 548, (89, 145, 150), (
        ("child", "state", "waiter"),
        ("C1", "RETURN", "P"),
        ("C2", "NOTICE", "P"),
    ), size=20, row_h=29)
    f.text(65, 657 if language == "zh" else 653, "GraphDelta", 21, BLUE)
    f.text(241, 657 if language == "zh" else 653, "pending = {C2}", 21, INK, 700)

    f.panel(486, 350, 442, 316, f.tr("2  Async demand forecasts", "2  异步执行需求预测"), size=24)
    f.strip(504, 409, [PINK, AMBER_PALE, PINK, TEAL_PALE], w=30, h=42, gap=5)
    f.arrow(((645, 429), (684, 429)), width=2)
    f.rect(699, 402, 211, 65, PAPER, INK, 1.5, 2, "6 4")
    f.text(716, 428, "CPU worker", 23, INK, 700)
    f.text(716, 453, f.tr("text + progress", "正文 + 进度"), 20)
    f.math(553, 490, [("p", "final")], size=25)
    for index, height in enumerate((17, 30, 49)):
        f.rect(520 + index * 41, 550 - height, 26, height,
               (BLUE_PALE, AMBER_PALE, PINK)[index], INK, 1.2, 0)
    f.interval(720, 894, 526, color=INK, center=799)
    f.math(807, 567, ["[", ("W", "lo"), ", ", ("W", "hi"), "]"], size=25, anchor="middle")
    f.line(504, 582, 910, 582, RULE, 1)
    f.token(524, 616, "P", PINK, r=18, size=20)
    f.text(554, 624, "JOIN", 21, INK, 700)
    f.math(654, 623, [("τ", "join")], size=27)
    f.clock(764, 616, 18)
    f.math(797, 623, [("τ", "tool")], size=27)
    f.text(504, 657 if language == "zh" else 652,
           f.tr("Model → demand; runtime → action", "模型预测需求；runtime 决定动作"), 21)

    f.panel(48, 690, 880, 438, f.tr("3  Checkpoint, timing, capacity and execution order",
                                                  "3  Checkpoint、时机、容量与执行顺序"), size=24)
    for xx in (334, 628):
        f.line(xx, 744, xx, 1109, RULE, 1.2)
    f.text(64, 763, f.tr("Reusable checkpoint", "可复用 checkpoint"), 22, INK, 700)
    f.page(76, 806, "p0", fill=BACKPLATE, w=43, h=32)
    f.page(166, 781, "p1", w=43, h=32)
    f.page(253, 806, "p5", w=43, h=32)
    f.arrow(((120, 820), (163, 804)), width=1.7)
    f.arrow(((210, 804), (250, 820)), color=RED, width=1.7)
    f.page(167, 867, "g", fill=PAPER, dashed=True, w=43, h=32)
    f.arrow(((117, 838), (164, 879)), color=GRAY, dashed=True, width=1.6)
    f.cross(225, 876, color=GRAY, size=17)
    f.math(272, 877, [("c", None, "*")], size=28, anchor="middle")
    f.text(65, 939, "context / epoch / prefix", 20)
    f.check(307, 917, color=INK, size=16)
    f.text(65, 979, "ΔFULL + required state", 21)

    f.text(350, 763, f.tr("Timing window", "实测传输启动窗口"), 22, INK, 700)
    f.line(365, 852, 579, 852, INK, 1.1)
    f.path(((365, 846), (394, 846), (394, 834), (428, 834),
            (428, 808), (469, 808), (469, 793), (578, 793)), INK, 2)
    f.line(468, 788, 468, 858, RED, 1.8, "5 4")
    f.math(477, 890, [("S", "90"), " + ", ("Q", "90")], size=28, anchor="middle")
    f.diamond(481, 944, 176, 62, PAPER, INK)
    f.math(481, 954, [("τ",), " ≤ ", ("H",)], size=30, anchor="middle")

    f.text(645, 763, f.tr("Capacity + admission", "容量与准入机会"), 22, INK, 700)
    f.strip(649, 788, [PAPER] * 3 + [TEAL_PALE] * 3 + [BACKPLATE] * 2, w=26, h=28, gap=4)
    for xx, symbol, sub in ((679, "C", "free"), (765, "C", "cold"), (857, "G", "next")):
        f.math(xx, 845, [(symbol, sub)], size=26, anchor="middle")
    f.ticket(650, 866, "old", BACKPLATE, w=257, h=28)
    f.ticket(650, 903, "hit", TEAL_PALE, w=257, h=28, resident=True)
    f.ticket(650, 940, "P", PINK, w=257, h=28, resident=True)
    f.text(648, 994, "aging / batch fill", 21)

    f.line(64, 1010, 911, 1010, INK, 1.2)
    for xx, title in (
        (65, "PREPARE_HOST"), (350, f.tr("Reclaim cold FULL", "回收冷 FULL")), (645, "PREFETCH_GPU"),
    ):
        f.text(xx, 1041, title, 22, INK, 700)
    f.strip(68, 1062, [PINK] * 3, w=28, h=29, missing=(2,))
    f.arrow(((172, 1076), (215, 1076)), color=RED, width=2)
    f.text(228, 1085, "Host", 23)
    f.strip(353, 1062, [PINK] * 2, w=28, h=29)
    f.arrow(((424, 1076), (460, 1076)), color=RED, width=2)
    f.strip(477, 1062, [PAPER] * 3, w=24, h=29)
    f.strip(648, 1062, [PINK] * 3, w=28, h=29)
    f.snapshot(783, 1061, scale=.48)
    f.lock(866, 1057, scale=.65)
    f.arrow(((467, 615), (478, 615), (478, 680), (247, 680), (247, 689)), color=BLUE, dashed=True, width=1.8)
    f.arrow(((706, 667), (706, 688)), color=RED, width=2)

    f.panel(986, 350, 764, 206, "HBM: FULL pages + checkpoint state", color=GREEN_BAND, size=24)
    f.text(1002, 433, "FULL", 22, INK, 700)
    pool_fills = [PINK] * 4 + [TEAL_PALE] * 3 + [AMBER_PALE] * 3 + [PAPER] * 2
    f.strip(1083, 404, pool_fills, w=48, h=42, gap=6)
    f.text(1002, 512, "Mamba", 22, INK, 700)
    for index, (fill, label) in enumerate(((PINK, "P"), (TEAL_PALE, "C1"), (AMBER_PALE, "C2"), (PAPER, "free"))):
        xx = 1092 + index * 160
        f.snapshot(xx, 483, scale=.65, fill=fill)
        f.text(xx + 64, 511, label, 21)
    f.text(1083, 547 if language == "zh" else 543,
           f.tr("Native allocator · shared pages · growth reserve",
                "原生分配器 · 共享页 · 输入增长预留"), 21)

    f.panel(986, 578, 330, 204, "UnifiedRadixCache", color=GREEN_BAND, size=24)
    for xx, yy, label, fill in (
        (1005, 667, "p0", BACKPLATE), (1114, 635, "P", PINK),
        (1114, 723, "C1", TEAL_PALE), (1227, 635, "c*", PINK),
    ):
        f.page(xx, yy, label, fill=fill, w=49, h=31)
    f.line(1056, 679, 1111, 650, INK, 1.7)
    f.line(1056, 689, 1111, 736, INK, 1.7)
    f.arrow(((1166, 650), (1224, 650)), color=RED, width=2)
    f.lock(1017, 707, scale=.65)
    f.text(1197, 746, "safe input", 20)

    f.panel(1338, 578, 412, 204, "HiCache / DMA", color=YELLOW, size=25)
    f.dma(1355, 636, 166, 79, label="D2H")
    f.dma(1563, 636, 166, 79, label="H2D")
    f.strip(1359, 737, [PINK] * 4, w=30, h=25, gap=5)
    f.strip(1569, 737, [PINK] * 3, w=30, h=25, gap=5)
    f.snapshot(1691, 734, scale=.43)
    f.arrow(((1717, 557), (1717, 623), (1439, 623), (1439, 633)), color=RED, width=2)
    f.arrow(((1439, 717), (1439, 728)), color=RED, width=2)
    f.arrow(((1439, 784), (1439, 804)), color=RED, width=2)
    f.arrow(((1646, 804), (1646, 784)), color=RED, width=2)
    f.arrow(((1646, 633), (1646, 557)), color=RED, width=2)
    f.arrow(((1318, 682), (1336, 682)), width=2)

    f.panel(986, 806, 764, 152, f.tr("Host pool: backed FULL + valid state",
                                                  "Host 池：已备份 FULL 与有效状态"), color=GREEN_BAND, size=24)
    f.text(1001, 880, "FULL", 22, INK, 700)
    f.strip(1083, 852, [PINK] * 5 + [TEAL_PALE] * 3 + [PAPER] * 4,
            w=48, h=32, gap=6, missing=(8, 9, 10, 11))
    f.text(1001, 935, "Mamba", 22, INK, 700)
    for index, (fill, label) in enumerate(((PINK, "P: c*"), (PAPER, "C1: ∅"), (AMBER_PALE, "C2"), (PAPER, "free"))):
        xx = 1092 + index * 160
        f.snapshot(xx, 909, scale=.47, fill=fill)
        f.text(xx + 46, 931, label, 20)
    f.arrow(((945, 866), (983, 866)), color=RED, width=2)

    f.panel(986, 980, 764, 148, f.tr("Restore-aware native admission", "恢复感知的原生准入"), color=GREEN_BAND, size=24)
    for yy, label, fill in ((1030, "old", BACKPLATE), (1060, "hit", TEAL_PALE), (1090, "P", PINK)):
        f.ticket(1003, yy, label, fill, w=173, h=25, resident=label != "old")
    f.arrow(((1179, 1042), (1244, 1042), (1244, 1065), (1280, 1065)), dashed=True, width=1.8)
    f.arrow(((1179, 1072), (1280, 1072)), width=2)
    f.arrow(((1179, 1102), (1244, 1102), (1244, 1082), (1280, 1082)), color=RED, width=2)
    f.text(1289, 1055, "PrefillAdder", 23, INK, 700)
    f.centered(1380, 1095, "KV ∧ slot", size=25)
    f.arrow(((1480, 1074), (1558, 1074)), width=2)
    f.gpu(1580, 1030, 146, 83, running=[PINK, TEAL_PALE, AMBER_PALE, PINK, PAPER, PAPER])
    f.arrow(((945, 1041), (982, 1041)), color=RED, width=2)
    f.arrow(((1738, 1129), (1738, 1157), (17, 1157), (17, 598), (46, 598)),
            color=BLUE, dashed=True, width=1.8)
    for xx, fill, label in ((48, PINK, "Parent"), (252, TEAL_PALE, "Child 1"), (460, AMBER_PALE, "Child 2")):
        f.rect(xx, 1176, 25, 18, fill, INK, 1.1, 0)
        f.text(xx + 36, 1188, label, 21)
    f.arrow(((711, 1184), (756, 1184)), color=RED, width=2)
    f.text(773, 1189, f.tr("Runtime action", "Runtime 动作"), 21)
    f.arrow(((1084, 1184), (1129, 1184)), color=BLUE, dashed=True, width=2)
    f.text(1146, 1189, "ACK / service / first reuse", 21)
    return f


def prediction_and_overlap(language: str) -> PaperFigure:
    f = PaperFigure("02_prediction_and_overlap", "Event and Content-driven Execution Demand", HEIGHT, language)
    f.heading(
        "From Completion Signals to Near-term Demand",
        "事件与内容驱动的执行需求预测",
        "Delivered content + service progress → separate phase / work heads → runtime timing",
        "已送达正文 + 服务进度 → 阶段 / 工作量双头 → Runtime 决定启动时机",
    )
    f.panel(32, 124, 1060, 564, f.tr("(a) Content, events and rolling work forecasts",
                                                  "(a) 内容、事件与滚动剩余工作预测"))
    f.document(57, 220, 122, 110, color=AMBER_PALE)
    f.math(117, 202, [("x", "t−k:t")], size=29, anchor="middle")
    f.arrow(((182, 269), (232, 269)))
    layer_x = (250, 320, 390)
    for left, right in zip(layer_x, layer_x[1:]):
        for source in range(3):
            for target in range(3):
                f.line(left + 10, 228 + source * 43,
                       right - 10, 228 + target * 43, RULE, 1)
    for column, xx in enumerate(layer_x):
        for row in range(3):
            f.circle(xx, 228 + row * 43, 10,
                     BLUE_PALE if column != 2 else TEAL_PALE, INK, 1.4)
    f.lock(395, 182, scale=.65)
    f.text(429, 194, "request / epoch", 20, MUTED)
    f.centered(320, 357, f.tr("Frozen encoder", "冻结编码器"), size=23, bold=True)
    f.arrow(((403, 269), (453, 269)))
    for index, fill in enumerate((BLUE_PALE, AMBER_PALE, BLUE_PALE, TEAL_PALE, PINK, BLUE_PALE)):
        f.rect(466, 218 + index * 17, 27, 14, fill, INK, 1, 0)
    f.math(480, 355, [("z", "t")], size=29, anchor="middle")
    f.junction(576, 269, kind="concat", radius=24)
    f.arrow(((496, 269), (549, 269)))
    f.polygon(((57, 392), (182, 392), (182, 439), (115, 439),
               (100, 451), (100, 439), (57, 439)), AMBER_PALE, INK)
    f.centered(119, 423, f.tr("Notice", "收尾通知"), size=23, bold=True)
    f.path(((251, 425), (275, 425), (275, 410), (300, 410),
            (300, 394), (327, 394), (327, 378), (351, 378)), BLUE, 2.5)
    f.math(303, 467, [("N", "t"), ", { }"], size=28, anchor="middle")
    f.table(374, 389, (77, 80, 79), (("notice", "tokens", "syntax"), ("1", "N(t)", "{ }")),
            row_h=29, size=19)
    f.arrow(((184, 416), (209, 416), (209, 370), (576, 370), (576, 297)), width=2)
    f.arrow(((355, 409), (371, 409)), width=2)
    f.arrow(((612, 435), (619, 435), (619, 350), (576, 350), (576, 297)), width=2)
    f.arrow(((603, 269), (636, 269), (636, 244), (665, 244)), color=RED)
    f.arrow(((636, 269), (636, 399), (665, 399)), color=RED)
    for yy, title in (
        (215, f.tr("Phase", "阶段头")), (370, f.tr("Work", "工作量头")),
    ):
        for row in range(3):
            for column in range(3):
                f.rect(680 + column * 14, yy + row * 14, 10, 10,
                       BLUE_PALE if row != column else AMBER_PALE, INK, .8, 0)
        f.text(735, yy + 31, title, 24, INK, 700)
    f.arrow(((820, 244), (862, 244)), width=2)
    f.arrow(((839, 399), (862, 399)), width=2)
    for xx, height, label, fill in (
        (884, 22, "W", BLUE_PALE), (946, 40, "N", AMBER_PALE),
        (1008, 73, "F", PINK),
    ):
        f.rect(xx, 304 - height, 31, height, fill, INK, 1.3, 0)
        f.centered(xx + 15, 335, label, size=21)
    f.math(968, 199, [("p", "phase")], size=29, anchor="middle")
    f.interval(881, 1065, 399, color=INK, center=977)
    f.math(973, 447, ["[", ("W", "lo"), ", ", ("W", "c"), ", ", ("W", "hi"), "]"],
           size=25, anchor="middle")
    f.line(48, 484, 1076, 484, INK, 1.2)
    f.text(49, 516, f.tr("Online snapshots → revised endpoints", "在线快照 → 滚动修正工作端点"), 24, INK, 700)
    f.math(530, 550, [("N", "t")], size=26, anchor="middle")
    f.math(902, 550, [("W", "lo"), " … ", ("W", "hi")], size=26, anchor="middle")
    for index, (width, lo, hi, center) in enumerate((
        (110, 778, 1047, 935), (180, 725, 944, 837), (245, 754, 990, 901),
    )):
        yy = 574 + index * 39
        f.math(82, yy + 8, [("t", str(index))], size=27, anchor="middle")
        f.strip(124, yy - 11, [PINK] * 4, w=29, h=23, gap=5)
        f.arrow(((271, yy), (363, yy)), color=BLUE, dashed=True, width=1.8)
        f.rect(397, yy - 9, width, 18, BLUE_PALE, INK, 1, 0)
        f.arrow(((666, yy), (710, yy)), width=1.8)
        f.interval(lo, hi, yy, color=INK, center=center)
    f.arrow(((1066, 669), (43, 669), (43, 272), (54, 272)),
            color=BLUE, dashed=True, width=1.6)

    f.panel(1116, 124, 652, 564, f.tr("(b) JOIN projection + observed service",
                                                   "(b) JOIN 投影与已观测服务进度"))
    f.token(1176, 273, "P", PINK, r=24, size=23)
    f.token(1315, 213, "C1", TEAL_PALE, r=24, size=23)
    f.token(1315, 334, "C2", AMBER_PALE, r=24, size=23)
    f.check(1350, 192, color=INK, size=18)
    f.diamond(1518, 273, 113, 74, PAPER, INK)
    f.centered(1518, 282, "ALL", size=25, bold=True)
    f.token(1698, 273, "P", PINK, r=24, size=23)
    for points, color in (
        (((1203, 263), (1288, 224)), INK),
        (((1203, 286), (1288, 322)), INK),
        (((1342, 213), (1404, 213), (1467, 257)), GRAY),
        (((1342, 334), (1404, 334), (1467, 289)), RED),
        (((1576, 273), (1671, 273)), RED),
    ):
        f.arrow(points, color=color, width=2.2)
    f.table(1139, 389, (107, 247, 248), (
        ("group", "pending", "signal"),
        ("g", "{C1, C2}", "WAIT"),
        ("g", "{C2}", "NOTICE"),
    ), row_h=29, size=21)
    f.math(1144, 534, [("τ", "join"), "(", ("t",), ") = "], size=32)
    f.fraction(1355, 520, ["[", ("W",), " − Δ", ("N",), ("]", "+")],
               [("v",), "(", ("t",), ")"], size=32)
    f.text(1560, 532, "pending = 1", 23, INK, 700)
    f.line(1138, 579, 1747, 579, RULE, 1)
    for index, width in enumerate((70, 44, 89, 57)):
        xx = 1150 + index * 145
        f.rect(xx, 605, width, 23, BLUE_PALE, INK, 1, 0)
        f.line(xx + width + 5, 617, xx + 131, 617, GRAY, 1.3, "5 4")
    f.math(1146, 668, [("v",), "(", ("t",), ")"], size=28)
    f.text(1245, 664, f.tr("served work", "已获服务"), 21)
    f.text(1468, 664, f.tr("queue separate", "排队单独处理"), 21)

    f.panel(32, 712, 838, 273, f.tr("(c) Conditional tool timing", "(c) 存活条件下的工具时机"))
    f.table(50, 777, (114, 117, 103), (
        ("project", "cmd", "done"),
        ("p", "k", "T1"),
        ("p", "k", "T2"),
    ), row_h=29, size=21)
    f.arrow(((386, 819), (418, 819)), width=1.8)
    f.rect(512, 777, 102, 105, TEAL_PALE, "none", 0, 0)
    f.line(432, 882, 616, 882, INK, 1.4)
    f.line(432, 882, 432, 778, INK, 1.4)
    f.path(((432, 880), (464, 880), (464, 861), (493, 861),
            (493, 837), (528, 837), (528, 806), (572, 806), (572, 784), (615, 784)),
           INK, 2.3)
    f.line(511, 778, 511, 889, RED, 1.6, "5 4")
    f.math(511, 907, [("a",)], size=24, color=RED, anchor="middle")
    f.clock(657, 792, 20)
    f.interval(661, 836, 850, color=INK, center=748)
    for xx, sub in ((661, "10"), (748, "50"), (836, "90")):
        f.math(xx, 894, [("q", sub)], size=25, anchor="middle")
    f.math(50, 960, ["P(", ("T",), " ≤ ", ("a",), " + ", ("r",),
                     " | ", ("T",), " > ", ("a",), ")"], size=30)
    f.math(360, 961, [" = "], size=29)
    f.fraction(409, 946, [
        ("F",), "(", ("a",), " + ", ("r",), ") − ", ("F",), "(", ("a",), ")",
    ], ["1 − ", ("F",), "(", ("a",), ")"], size=25)
    f.arrow(((623, 946), (695, 946)), color=RED, width=2)
    f.math(762, 956, [("τ", "tool")], size=32, anchor="middle")

    f.panel(894, 712, 874, 273, f.tr("(d) Runtime: cost, timing and physical checks",
                                                   "(d) Runtime：成本、时机与物理校验"), color=GREEN_BAND)
    f.table(914, 769, (103, 126, 139), (
        ("dir", "shape", "bytes"),
        ("H2D", "extents", "B"),
    ), row_h=29, size=21)
    f.text(1320, 788, "submit → ACK", 21)
    f.text(1532, 788, "enqueue → submit", 21)
    for index, height in enumerate((14, 25, 39, 30)):
        f.rect(1325 + index * 25, 841 - height, 17, height, BLUE_PALE, INK, 1, 0)
        f.rect(1545 + index * 25, 841 - height * .7, 17, height * .7, AMBER_PALE, INK, 1, 0)
    f.math(1457, 837, [("S", "90")], size=26)
    f.math(1674, 837, [("Q", "90")], size=26)
    f.math(916, 886, [
        ("H",), " = min(", ("L",), ", ", ("S", "90"), " + ",
        ("Q", "90"), " + ", ("Δ", "obs"), " + ", ("ε", "early"), ")",
    ], size=31)
    f.diamond(1161, 944, 179, 56, PAPER, INK)
    f.math(1161, 953, [("τ",), " ≤ ", ("H",)], size=31, anchor="middle")
    f.arrow(((1255, 944), (1320, 944)), color=RED, width=2)
    f.junction(1343, 944)
    f.math(1375, 929, [("c", None, "*"), " / ", ("B",)], size=25)
    f.arrow(((1450, 929), (1462, 929), (1462, 901), (1343, 901), (1343, 923)), width=1.7)
    f.arrow(((1365, 944), (1545, 944)), color=RED, width=2)
    f.dma(1560, 918, 185, 55, label="H2D")
    f.arrow(((1504, 689), (1504, 700), (882, 700), (882, 944), (1068, 944)), color=RED, width=2)
    f.line(825, 944, 882, 944, RED, 2)
    f.circle(882, 944, 4, RED, RED, 1)

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
                                                   "(a) 稳定 FULL：增量 PREPARE_HOST"), color=GREEN_BAND)
    f.text(68, 244, "HBM", 28, INK, 700)
    f.text(68, 327, "Host", 28, INK, 700)
    for index in range(4):
        xx = 213 + index * 172
        f.bracket(xx, xx + 150, 202)
        f.math(xx + 75, 193, [("e", str(index))], size=24, anchor="middle")
    for i in range(8):
        x = 213 + i * 86
        f.page(x, 213, f"p{i}", w=64, h=44)
        f.page(x, 296, f"p{i}", fill=PINK if i < 4 else PAPER,
               stroke=INK if i < 4 else GRAY, dashed=i >= 4, w=64, h=44)
        if i >= 4:
            f.arrow(((x + 32, 259), (x + 32, 291)), color=RED, width=2)
    f.math(968, 285, ["ΔFULL"], size=26)
    f.text(68, 389, f.tr("Host valid", "Host 有效位"), 22, INK, 700)
    f.strip(213, 362, [PINK] * 4 + [PAPER] * 4,
            labels=["1"] * 4 + ["0"] * 4, w=64, h=32, gap=22)
    f.math(974, 389, [("Δ",), " only"], size=25, anchor="middle")

    f.panel(1120, 132, 640, 285, f.tr("(b) Checkpoint-matched Mamba", "(b) Checkpoint 匹配的 Mamba"), color=GREEN_BAND)
    for xx, sub, faded in ((1150, "0", True), (1340, "1", True), (1556, None, False)):
        f.snapshot(xx, 216, scale=.82, faded=faded)
        f.math(xx + 34, 295, [("s", sub, "*" if sub is None else None)],
               size=30, color=GRAY if faded else INK, anchor="middle")
    f.arrow(((1233, 249), (1324, 249)), color=GRAY, width=2)
    f.arrow(((1424, 249), (1541, 249)), color=GRAY, width=2)
    f.text(1427, 219, "decode", 21, MUTED)
    f.math(1697, 257, [("c", None, "*")], size=32, anchor="middle")
    f.line(1640, 250, 1668, 250, INK, 1.6)
    f.table(1142, 322, (124, 135, 333), (
        ("context", "epoch", "Host checkpoint"),
        ("P", "e", "c*"),
    ), row_h=28, size=21)
    f.text(1146, 404, "PREPARE", 21, MUTED)
    f.cross(1260, 385, color=RED, size=19)
    f.arrow(((1697, 322), (1697, 287), (1640, 287), (1640, 243), (1623, 243)),
            color=RED, width=2)
    f.text(1500, 405, f.tr("Restore matched state", "只恢复匹配状态"), 21)

    f.panel(40, 459, 1720, 326, f.tr("(c) Safe-input checkpoint → missing pages → native layer pipeline",
                                                   "(c) 安全输入 checkpoint → 缺失页 → 原生逐层流水线"), color=GREEN_BAND)
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
    f.junction(1328, 667)
    f.arrow(((1349, 667), (1376, 667), (1376, 544), (1450, 544), (1450, 551)), width=2)
    f.text(1453, 530, "H2D", 22, INK, 700)
    f.text(1607, 530, "GPU", 22, INK, 700)
    for index in range(3):
        yy = 554 + index * 73
        copy_x = 1450 + index * 38
        gpu_x = 1563 + index * 54
        f.math(1411, yy + 23, [("ℓ", str(index))], size=29, anchor="middle")
        f.line(1438, yy + 38, 1738, yy + 38, RULE, 1)
        f.rect(copy_x, yy, 82, 29, YELLOW, INK, 1.4, 0)
        f.strip(copy_x + 8, yy + 7, [PINK] * 3, w=17, h=15, gap=5)
        f.arrow(((copy_x + 85, yy + 14), (gpu_x - 5, yy + 14)), width=1.7)
        f.rect(gpu_x, yy, 1736 - gpu_x, 29, BLUE_PALE, INK, 1.4, 0)
        if index:
            previous_x = 1563 + (index - 1) * 54
            f.arrow(((previous_x + 25, yy - 41), (previous_x + 25, yy - 13),
                     (gpu_x + 17, yy - 13), (gpu_x + 17, yy - 3)), width=1.6)
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
    f.table(359, 876, (80, 80, 80), (("private", "refs=0", "DMA=0"),),
            header=False, row_h=22, size=17)
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

    f.panel(1150, 830, 610, 326, f.tr("(e) Residency and consumption",
                                                   "(e) 驻留、准入与首次消费"), color=GREEN_BAND)
    f.text(1176, 892, "ACK", 21, BLUE)
    f.arrow(((1231, 885), (1274, 885)), width=1.6)
    f.lock(1285, 866, scale=.65)
    f.text(1310, 892, "lease", 21)
    f.arrow(((1372, 885), (1411, 885)), width=1.6)
    f.text(1428, 892, f.tr("use / expiry", "消费 / 到期"), 21)
    f.clock(1663, 883, 13)
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
            "NOTICE is an observed completion announcement, not ground-truth terminal phase. "
            "The control plane combines checkpoints, measured timing, native capacity and "
            "candidate order; physical FULL pages and Mamba slots remain separate. "
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
            "NOTICE 是已观测到的收尾通知，不是真实终态标签。控制面结合 checkpoint、"
            "实测时机、原生容量和候选顺序；数据面的 FULL 页与 Mamba 槽分别核算。"
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
            "The t0/t1/t2 snapshots, work intervals, history records and CDF are schematic, "
            "not measurements. Input notice=1 is observable; true final phase is never an "
            "input. The conditional tool CDF normalizes the surviving historical mass. "
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
            "t0/t1/t2、区间、历史记录及 CDF 都是机制示意，不是实测。输入 notice=1"
            "是在线可观测信息，不把真实 final 阶段标签作为模型输入。工具条件 CDF"
            "对尚未完成的历史概率质量重新归一化。"
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

    @staticmethod
    def clear_shape_effects(shape):
        for effect in shape._element.xpath(".//a:effectRef"):
            effect.set("idx", "0")
        for effect in shape._element.xpath(".//a:effectLst|.//a:effectDag"):
            effect.getparent().remove(effect)
        shape._element.spPr.append(OxmlElement("a:effectLst"))
        for properties in shape._element.xpath(".//a:rPr|.//a:defRPr|.//a:endParaRPr"):
            properties.append(OxmlElement("a:effectLst"))

    def clear_package_effects(self):
        namespace = "http://schemas.openxmlformats.org/drawingml/2006/main"
        removed = {
            "outerShdw", "innerShdw", "prstShdw", "glow", "reflection",
            "softEdge", "blur", "scene3d", "sp3d",
        }
        for part in self.presentation.part.package.iter_parts():
            if not part.content_type.endswith("+xml"):
                continue
            live_root = getattr(part, "_element", None)
            root = live_root if live_root is not None else etree.fromstring(part.blob)
            for element in list(root.iter()):
                name = etree.QName(element)
                if name.namespace != namespace:
                    continue
                if name.localname in ("effectLst", "effectDag"):
                    for child in list(element):
                        element.remove(child)
                elif name.localname in removed and element.getparent() is not None:
                    element.getparent().remove(element)
                elif name.localname == "effectRef":
                    element.set("idx", "0")
            if live_root is None:
                part._blob = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)

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

    def add_arrowhead(self, slide, points, attributes):
        end = points[-1]
        previous = next((point for point in reversed(points[:-1]) if point != end), None)
        if previous is None:
            return
        length = math.hypot(end[0] - previous[0], end[1] - previous[1])
        ux, uy = (end[0] - previous[0]) / length, (end[1] - previous[1]) / length
        size = float(attributes.get("stroke-width", 1)) * 7
        tip = (end[0] + .15 * size * ux, end[1] + .15 * size * uy)
        base = (end[0] - .85 * size * ux, end[1] - .85 * size * uy)
        vertices = [
            tip, (base[0] - .5 * size * uy, base[1] + .5 * size * ux),
            (base[0] + .5 * size * uy, base[1] - .5 * size * ux),
        ]
        builder = slide.shapes.build_freeform(*vertices[0], scale=self.unit)
        builder.add_line_segments(vertices[1:], close=True)
        head = builder.convert_to_shape()
        head.name = f"arrowhead {len(slide.shapes)}"
        self.style(head, {"fill": attributes.get("stroke", INK), "stroke": "none"})
        self.clear_shape_effects(head)

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
        self.clear_shape_effects(shape)

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
            points = [
                (float(element.get("x1", 0)), float(element.get("y1", 0)) + offset_y),
                (float(element.get("x2", 0)), float(element.get("y2", 0)) + offset_y),
            ]
            shape = slide.shapes.add_connector(
                MSO_CONNECTOR_TYPE.STRAIGHT,
                *(self.length(coordinate) for point in points for coordinate in point),
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
        self.clear_shape_effects(shape)
        if "marker-end" in attributes:
            self.add_arrowhead(slide, points, attributes)

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
        self.clear_package_effects()
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
    tile_width = 1020
    rows = []
    for row in manifest:
        if row["language"] != "en":
            continue
        with Image.open(directory / (row["stem"] + ".png")) as original:
            image = original.copy()
        image.thumbnail((tile_width - 48, 690), Image.Resampling.LANCZOS)
        tile = Image.new("RGB", (tile_width, image.height + 24), PAPER)
        tile.paste(image, (24, 12))
        rows.append(tile)
    sheet = Image.new("RGB", (tile_width, sum(row.height for row in rows) + 24), PAPER)
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
        "visual_style": (
            "compact control/data planes, native page pools and state ledgers, "
            "flat black outlines and semantic ownership colors"
        ),
        "shadow_policy": (
            "no offset decorative layers; explicit shape/text resets; package-wide "
            "theme, master, layout, shadow, glow, reflection and 3D effects removed"
        ),
        "powerpoint_arrowheads": (
            "editable native triangles oriented from the final SVG path segment"
        ),
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
