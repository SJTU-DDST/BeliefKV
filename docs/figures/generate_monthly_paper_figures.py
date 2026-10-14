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

from generate_beliefkv_paper_figures import (
    AMBER, AMBER_PALE, BLUE, BLUE_PALE, Figure, GRAY, GRAY_PALE,
    INK, MUTED, PAPER, RULE, TEAL, TEAL_PALE,
)


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "docs/reports/2026-10/assets"
WIDTH = 1800
HEIGHT = 1200
PAPER_WIDTH_INCHES = 7.2
DPI = 600
FONT_ROOT = Path("/usr/share/fonts")
ARCHITECTURE_DATE = "2026-10-14"
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


@lru_cache(maxsize=64)
def drawing_font(language: str, size: float, weight: int = 400):
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
        self.parts.append(
            f"<style>text{{font-family:'{self.family}';letter-spacing:0}}"
            "path,line{stroke-linejoin:round;stroke-linecap:round}</style>"
        )
        self.text_bounds = []

    def tr(self, english: str, chinese: str) -> str:
        return chinese if self.language == "zh" else english

    def measure(self, value: str, size: float, weight: int = 400):
        font = drawing_font(self.language, size, weight)
        _, top, _, bottom = font.getbbox(value, anchor="ls")
        return font.getlength(value) / 4, (bottom - top) / 4, -top / 4

    def text(
        self, x, y, value, size=24, color=INK, weight=400,
        anchor="start", family=None,
    ):
        width, height, baseline = self.measure(value, size, weight)
        left = x - (width / 2 if anchor == "middle" else width if anchor == "end" else 0)
        self.text_bounds.append((value, left, y - baseline, width, height))
        super().text(x, y, value, size, color, weight, anchor, family)

    def panel(self, x, y, w, h, title, *, color=BLUE, fill=PAPER, size=28):
        self.rect(x, y, w, h, fill, RULE, 1.7, 3)
        self.line(x + 1, y + 1, x + w - 1, y + 1, color, 3)
        self.text(x + 22, y + 40, title, size, color, 700)

    def heading(self, english, chinese, subtitle_en, subtitle_zh):
        self.text(40, 51, self.tr(english, chinese), 36, INK, 700)
        self.text(40, 88, self.tr(subtitle_en, subtitle_zh), 25, MUTED)
        self.line(40, 105, self.width - 40, 105, RULE, 1.3)

    def arrow(self, points, *, color=INK, dashed=False):
        marker = {BLUE: "blue-arrow", TEAL: "teal-arrow", AMBER: "amber-arrow"}.get(
            color, "arrow",
        )
        self.path(points, color, 2.4, "8 6" if dashed else None, marker)

    def centered(self, x, y, value, *, size=23, color=INK, bold=False):
        self.text(x, y, value, size, color, 700 if bold else 400, "middle")

    def page(self, x, y, label, *, fill=BLUE_PALE, stroke=BLUE, dashed=False):
        self.rect(x, y, 54, 47, fill, stroke, 1.8, 2, "6 4" if dashed else None)
        self.centered(x + 27, y + 31, label, size=21, color=stroke, bold=True)

    def lock(self, x, y, *, color=BLUE, scale=1):
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
        }


def system_overview(language: str) -> PaperFigure:
    f = PaperFigure("01_system_overview", "BeliefKV Execution and KV Coordination", HEIGHT, language)
    f.heading(
        "BeliefKV: Coordinating Dynamic Agent Execution and KV",
        "BeliefKV：动态 Agent 执行与 KV 协同管理",
        "Observed dependencies → near-future demand → KV lifecycle → native admission",
        "已观测依赖 → 近期执行需求 → KV 生命周期 → 原生准入",
    )
    f.panel(40, 127, 1720, 220, f.tr("(a) Runtime workflow", "(a) 运行时工作流"))
    f.token(107, 247, "P", BLUE_PALE, BLUE, 29, 27)
    f.token(295, 211, "C1", PAPER, TEAL, 28, 26)
    f.token(295, 298, "C2", PAPER, TEAL, 28, 26)
    f.node(427, 185, 132, 53, "Tool", AMBER_PALE, AMBER, 25)
    f.diamond(699, 251, 126, 88, AMBER_PALE, AMBER)
    f.centered(699, 259, "JOIN", size=26, bold=True)
    f.token(860, 251, "P", BLUE_PALE, BLUE, 29, 27)
    for points, color in (
        (((138, 235), (264, 214)), BLUE),
        (((138, 259), (264, 294)), BLUE),
        (((325, 211), (426, 211)), AMBER),
        (((560, 211), (590, 211), (640, 237)), TEAL),
        (((325, 298), (594, 298), (644, 266)), TEAL),
        (((763, 251), (829, 251)), BLUE),
    ):
        f.arrow(points, color=color)
    f.text(150, 197, "SPAWN", 24, BLUE)
    f.text(532, 329, "RETURN", 24, TEAL)
    f.arrow(((861, 281), (861, 322), (946, 322), (946, 251)),
            color=BLUE, dashed=True)
    f.text(900, 222, f.tr("Next round", "下一轮"), 24, MUTED)
    f.line(1031, 184, 1031, 326, RULE, 1.4)
    f.node(1082, 192, 212, 48, f.tr("Events", "运行事件"), BLUE_PALE, BLUE, 26)
    f.node(1353, 192, 354, 48, f.tr("Notice + text + decode", "通知 + 正文 + decode"), AMBER_PALE, AMBER, 25)
    f.arrow(((1189, 242), (1189, 285)), color=BLUE)
    f.arrow(((1530, 242), (1530, 285)), color=AMBER)
    f.node(1082, 287, 212, 40, "RCCG", PAPER, BLUE, 25)
    f.node(1353, 287, 354, 40, f.tr("Observed progress", "已观测进度"), PAPER, AMBER, 25)

    columns = ((40, 530, BLUE), (635, 530, TEAL), (1230, 530, AMBER))
    for (x, width, color), title in zip(columns, (
        f.tr("(1) Execution demand", "(1) 执行需求预测"),
        f.tr("(2) KV lifecycle", "(2) KV 生命周期"),
        f.tr("(3) Admission + prefetch", "(3) 准入与预取协同"),
    )):
        f.panel(x, 412, width, 505, title, color=color)
    f.arrow(((307, 347), (307, 411)), color=BLUE)
    f.arrow(((1188, 347), (1188, 378), (900, 378), (900, 411)), color=BLUE)
    f.arrow(((1530, 347), (1530, 383), (594, 383), (594, 530), (570, 530)),
            color=AMBER)

    f.node(64, 493, 178, 54, "RCCG", BLUE_PALE, BLUE, 28)
    f.node(300, 493, 246, 54, f.tr("CPU predictor", "CPU 预测器"), AMBER_PALE, AMBER, 26)
    f.arrow(((243, 520), (299, 520)), color=BLUE)
    f.text(65, 585, "GraphDelta", 25, BLUE)
    f.token(126, 652, "P", BLUE_PALE, BLUE, 27, 25)
    f.token(247, 618, "C1", TEAL_PALE, TEAL, 25, 22)
    f.token(247, 699, "C2", AMBER_PALE, AMBER, 25, 22)
    f.arrow(((154, 644), (218, 624)), color=BLUE)
    f.arrow(((154, 663), (218, 691)), color=BLUE)
    f.text(278, 626, "DONE", 23, TEAL)
    f.text(278, 707, "RUN", 23, AMBER)
    f.node(373, 601, 173, 45, "p(final)", PAPER, AMBER, 25)
    f.node(373, 673, 173, 45, "[Rlo, Rhi]", PAPER, AMBER, 25)
    f.arrow(((423, 547), (423, 567), (349, 567), (349, 623), (372, 623)), color=AMBER)
    f.arrow(((349, 567), (349, 695), (372, 695)), color=AMBER)
    f.arrow(((548, 623), (557, 623), (557, 744), (417, 744), (417, 768)), color=AMBER)
    f.arrow(((247, 726), (247, 767)), color=BLUE)
    f.arrow(((460, 719), (460, 753), (417, 753), (417, 767)), color=AMBER)
    f.node(64, 769, 482, 65, f.tr("Next: parent / tool agent",
                                "下一消费者：parent / 工具 agent"), BLUE_PALE, BLUE, 25)
    f.text(65, 886, "context / epoch / session", 25, MUTED)
    f.arrow(((570, 802), (634, 802)), color=BLUE)

    for x, label, color, fill in (
        (662, "HBM", BLUE, BLUE_PALE), (961, "Host", TEAL, TEAL_PALE),
    ):
        f.node(x, 491, 178, 53, label, fill, color, 28)
        for i in range(3):
            f.rect(x + 7 + i * 53, 556, 45, 31, fill, color, 1.6, 2)
    f.arrow(((841, 506), (960, 506)), color=TEAL)
    f.text(850, 493, "D2H", 23, TEAL)
    f.arrow(((961, 577), (842, 577)), color=AMBER)
    f.text(851, 613, "H2D", 23, AMBER)
    states = ((662, "HBM"), (840, "H+Host"), (1008, "Host-only"))
    for x, label in states:
        f.node(x, 651, 130, 49, label, PAPER, TEAL, 22)
    f.arrow(((793, 676), (838, 676)), color=TEAL)
    f.arrow(((971, 676), (1006, 676)), color=TEAL)
    f.text(779, 641, "prepare", 22, TEAL)
    f.text(967, 742, f.tr("pressure", "压力"), 23, TEAL)
    f.node(662, 773, 177, 58, "ΔFULL", TEAL_PALE, TEAL, 29)
    f.node(961, 773, 178, 58, "s*", AMBER_PALE, AMBER, 31)
    f.circle(900, 802, 22, PAPER, INK, 1.8)
    f.centered(900, 811, "+", size=31, bold=True)
    f.arrow(((840, 802), (875, 802)), color=TEAL)
    f.arrow(((960, 802), (925, 802)), color=AMBER)
    f.arrow(((900, 825), (900, 858)), color=TEAL)
    f.node(814, 860, 185, 39, f.tr("Restore", "恢复计划"), TEAL_PALE, TEAL, 24)
    f.text(666, 873, "FULL", 25, TEAL)
    f.text(1036, 873, "Mamba", 25, AMBER)
    f.arrow(((1000, 879), (1175, 879), (1175, 804), (1252, 804)), color=TEAL)

    f.text(1254, 496, f.tr("Native waiting queue", "原生等待队列"), 25, MUTED)
    for x, label, color in ((1254, "r1", GRAY), (1412, "r2", BLUE), (1570, "r3", TEAL)):
        f.node(x, 517, 139, 52, label, PAPER, color, 28)
    f.text(1254, 605, f.tr("aging", "老化"), 23, GRAY)
    f.text(1412, 605, f.tr("HBM hit", "HBM 命中"), 23, BLUE)
    f.text(1570, 605, f.tr("restored", "已恢复"), 23, TEAL)
    for x, color in ((1323, GRAY), (1481, BLUE), (1639, TEAL)):
        f.line(x, 570, x, 577, color, 2)
    f.arrow(((1639, 618), (1639, 657), (1533, 657)), color=TEAL)
    f.arrow(((1481, 618), (1481, 642)), color=BLUE)
    f.arrow(((1323, 618), (1323, 657), (1428, 657)), color=GRAY, dashed=True)
    f.diamond(1481, 687, 154, 85, TEAL_PALE, TEAL)
    f.centered(1481, 696, f.tr("rank", "排序"), size=27, color=TEAL, bold=True)
    f.arrow(((1481, 731), (1481, 757), (1492, 757), (1492, 778)), color=TEAL)
    f.node(1254, 773, 201, 58, f.tr("KV ready", "KV 就绪"), TEAL_PALE, TEAL, 25)
    f.node(1534, 773, 201, 58, f.tr("slot", "执行槽"), PAPER, BLUE, 25)
    f.arrow(((1456, 802), (1470, 802)), color=TEAL)
    f.arrow(((1534, 802), (1512, 802)), color=BLUE)
    f.circle(1492, 802, 22, PAPER, INK, 1.8)
    f.centered(1492, 811, "&", size=29, bold=True)
    f.arrow(((1492, 825), (1492, 858)), color=BLUE)
    f.node(1361, 860, 262, 39, f.tr("Native admission", "原生准入"), BLUE_PALE, BLUE, 24)

    f.panel(40, 983, 1720, 151, f.tr("(b) Native SGLang", "(b) 原生 SGLang"))
    native = (
        (64, 300, "UnifiedRadixCache"),
        (429, 252, "HiCache / DMA"),
        (746, 299, "FULL / Mamba"),
        (1110, 275, "PrefillAdder"),
        (1450, 285, "GPU batch"),
    )
    for x, width, label in native:
        f.node(x, 1050, width, 58, label, PAPER, BLUE, 26)
    for (x, width, _), (x_next, _, _) in zip(native, native[1:]):
        f.arrow(((x + width + 1, 1079), (x_next - 1, 1079)), color=BLUE)
    f.arrow(((900, 917), (900, 982)), color=TEAL)
    f.arrow(((1492, 917), (1492, 982)), color=AMBER)
    f.arrow(((1592, 1134), (1592, 1157), (20, 1157), (20, 706), (39, 706)),
            color=BLUE, dashed=True)
    f.text(620, 1188, f.tr("ACK / service progress / first reuse",
                          "ACK / 服务进度 / 首次复用"), 24, BLUE)
    return f


def prediction_and_overlap(language: str) -> PaperFigure:
    f = PaperFigure("02_prediction_and_overlap", "Event and Content-driven Execution Demand", HEIGHT, language)
    f.heading(
        "Predicting the Next Execution Demand",
        "事件与内容驱动的执行需求预测",
        "Text + events + progress → stage / work → JOIN or tool reentry",
        "正文 + 事件 + 进度 → 阶段 / 剩余工作 → JOIN 或工具返回",
    )
    f.panel(40, 132, 1140, 410, f.tr("(a) Async child model", "(a) 异步 Child 模型"))
    f.text(66, 208, "text[−k:]", 27, BLUE, 600)
    f.rect(66, 231, 163, 97, PAPER, BLUE, 1.7, 2)
    for i, width in enumerate((131, 114, 138, 89)):
        f.line(79, 252 + i * 17, 79 + width, 252 + i * 17, BLUE, 2)
    f.arrow(((230, 278), (280, 278)), color=BLUE)
    f.node(282, 244, 231, 67, f.tr("Text encoder", "文本编码器"), BLUE_PALE, BLUE, 27)
    f.lock(468, 194, scale=.85)
    f.arrow(((514, 278), (557, 278)), color=BLUE)
    for i, height in enumerate((43, 67, 32, 53, 74)):
        f.rect(564 + i * 15, 306 - height, 10, height, AMBER_PALE, AMBER, 1.4, 0)
    f.centered(601, 347, "z(t)", size=27, color=AMBER)
    f.node(66, 400, 192, 56, f.tr("Notice", "收尾通知"), AMBER_PALE, AMBER, 27)
    f.node(293, 400, 226, 56, "N(t), structure", PAPER, BLUE, 25)
    f.circle(680, 303, 24, PAPER, INK, 1.8)
    f.centered(680, 313, "+", size=32, bold=True)
    f.arrow(((640, 278), (654, 278), (665, 285)), color=AMBER)
    f.arrow(((260, 428), (280, 428)), color=AMBER)
    f.arrow(((520, 428), (680, 428), (680, 330)), color=BLUE)
    f.arrow(((707, 303), (725, 303), (725, 241), (747, 241)), color=AMBER)
    f.arrow(((725, 303), (725, 394), (747, 394)), color=AMBER)
    f.node(749, 212, 191, 59, f.tr("Phase", "阶段头"), AMBER_PALE, AMBER, 29)
    f.node(749, 365, 191, 59, f.tr("Work", "工作量头"), AMBER_PALE, AMBER, 29)
    f.arrow(((941, 241), (977, 241)), color=AMBER)
    f.arrow(((941, 394), (977, 394)), color=AMBER)
    for x, height, label, color, fill in (
        (994, 22, "W", GRAY, GRAY_PALE),
        (1048, 36, "N", AMBER, AMBER_PALE),
        (1102, 73, "F", TEAL, TEAL_PALE),
    ):
        f.rect(x, 293 - height, 32, height, fill, color, 1.5, 0)
        f.centered(x + 16, 321, label, size=19, color=color)
    f.centered(1064, 195, "p(phase)", size=25, color=AMBER)
    f.interval(990, 1143, 394, color=TEAL, center=1064)
    f.centered(1066, 435, "[Wlo, Wc, Whi]", size=24, color=TEAL)
    f.text(66, 506, "request / context / epoch", 25, MUTED)
    f.arrow(((500, 489), (650, 489), (650, 446), (520, 446)),
            color=AMBER, dashed=True)
    f.text(767, 506, "t → t + Δt", 25, AMBER)

    f.panel(1225, 132, 535, 410, f.tr("(b) JOIN projection", "(b) JOIN 投影"), color=TEAL)
    f.token(1290, 293, "P", BLUE_PALE, BLUE, 27, 25)
    f.token(1433, 235, "C1", TEAL_PALE, TEAL, 27, 24)
    f.token(1433, 355, "C2", AMBER_PALE, AMBER, 27, 24)
    f.check(1472, 211, size=20)
    f.diamond(1608, 293, 114, 77, TEAL_PALE, TEAL)
    f.centered(1608, 301, "ALL", size=25, color=TEAL, bold=True)
    f.token(1707, 293, "P", BLUE_PALE, BLUE, 25, 24)
    for points, color in (
        (((1319, 283), (1404, 246)), BLUE),
        (((1319, 307), (1404, 344)), BLUE),
        (((1463, 235), (1506, 235), (1557, 278)), TEAL),
        (((1463, 355), (1506, 355), (1557, 307)), AMBER),
        (((1666, 293), (1679, 293)), BLUE),
    ):
        f.arrow(points, color=color)
    f.text(1260, 422, "pending = {C2}", 27, TEAL, 600)
    f.node(1251, 436, 482, 58, "τjoin = max(0, W − ΔN) / v(t)", PAPER, TEAL, 26)
    f.arrow(((1180, 394), (1202, 394), (1202, 465), (1250, 465)), color=AMBER)
    f.arrow(((1433, 383), (1433, 389), (1588, 389), (1588, 435)), color=TEAL)
    f.arrow(((1493, 542), (1493, 563), (1781, 563), (1781, 815),
             (1213, 815), (1213, 887)), color=TEAL)

    f.panel(40, 592, 1720, 185, f.tr("(c) Conditional tool timing", "(c) 条件工具时机"), color=AMBER)
    f.circle(112, 682, 27, PAPER, BLUE, 1.8)
    f.path(((112, 657), (112, 682), (130, 694)), BLUE, 2.4)
    f.text(67, 738, "a(t)", 26, BLUE)
    f.arrow(((140, 682), (177, 682)), color=BLUE)
    f.node(179, 653, 222, 58, "cmd / project", BLUE_PALE, BLUE, 25)
    f.arrow(((402, 682), (448, 682)), color=BLUE)
    for i in (2, 1, 0):
        f.rect(451 + i * 9, 654 - i * 8, 170, 57, TEAL_PALE, TEAL, 1.5, 2)
    f.centered(536, 692, f.tr("History", "完成历史"), size=26, color=TEAL, bold=True)
    f.check(603, 634, size=18)
    f.arrow(((641, 682), (693, 682)), color=AMBER)
    f.node(695, 653, 399, 58, "P(T ≤ a + τ | T > a)", AMBER_PALE, AMBER, 28)
    f.arrow(((1095, 682), (1150, 682)), color=AMBER)
    f.interval(1171, 1420, 682, color=TEAL, center=1280)
    for x, label in ((1171, "q10"), (1280, "q50"), (1420, "q90")):
        f.centered(x, 735, label, size=25, color=TEAL)
    f.arrow(((1437, 682), (1487, 682)), color=TEAL)
    f.node(1489, 653, 246, 58, "τtool(t)", PAPER, TEAL, 28)
    f.arrow(((1612, 712), (1612, 755), (637, 755), (637, 711)),
            color=TEAL, dashed=True)
    f.text(746, 751, "TOOL_END", 23, TEAL)
    f.arrow(((1612, 777), (1612, 798), (1257, 798), (1257, 887)), color=TEAL)

    f.panel(40, 838, 1720, 129, f.tr("(d) Runtime start window", "(d) Runtime 启动窗口"), color=TEAL)
    f.node(64, 896, 241, 52, "ACK history", BLUE_PALE, BLUE, 27)
    f.arrow(((306, 922), (349, 922)), color=BLUE)
    f.node(351, 891, 691, 62, "H = min(L, S90 + Q90 + Δobs + εearly)", PAPER, TEAL, 28)
    f.arrow(((1043, 922), (1130, 922)), color=TEAL)
    f.diamond(1235, 922, 208, 78, TEAL_PALE, TEAL)
    f.centered(1235, 931, "τ ≤ H", size=31, color=TEAL, bold=True)
    f.arrow(((1341, 922), (1441, 922)), color=TEAL)
    f.check(1379, 893, size=18)
    f.node(1443, 891, 292, 62, "PREFETCH_GPU", TEAL_PALE, TEAL, 27)

    left, trigger, boundary, ack, serve, native_serve, right = 390, 920, 1210, 1320, 1480, 1630, 1760
    f.line(boundary, 1005, boundary, 1178, GRAY, 1.8, "7 6")
    f.text(boundary + 10, 1007, "RETURN / TOOL_END", 25, INK, 600)
    f.interval(trigger, boundary, 1006, color=TEAL)
    f.text(1029, 999, "Δt", 23, TEAL)
    for y, label in (
        (1042, "child / tool"), (1103, f.tr("prefetch", "预取")), (1164, f.tr("native", "原生")),
    ):
        f.text(67, y + 6, label, 27, INK, 600)
        f.line(left, y - 8, right, y - 8, RULE, 1.2)
    f.rect(left, 1020, boundary - left, 38, BLUE_PALE, BLUE, 1.7, 2)
    f.centered(770, 1047, "decode / tool", size=26, color=BLUE)
    f.rect(trigger, 1081, ack - trigger, 38, TEAL_PALE, TEAL, 1.8, 2)
    f.centered((trigger + ack) / 2, 1108, "H2D", size=26, color=TEAL)
    f.rect(ack, 1081, serve - ack, 38, GRAY_PALE, RULE, 1.5, 2)
    f.rect(serve, 1081, right - serve, 38, BLUE_PALE, BLUE, 1.8, 2)
    f.centered((serve + right) / 2, 1108, "GPU", size=26, color=BLUE)
    f.text(ack - 18, 1074, "ACK", 23, TEAL)
    f.text(serve + 8, 1074, f.tr("First use", "首次复用"), 23, BLUE)
    f.rect(boundary, 1142, native_serve - boundary, 38, GRAY_PALE, GRAY, 1.7, 2)
    f.centered((boundary + native_serve) / 2, 1169, "H2D", size=26, color=MUTED)
    f.rect(native_serve, 1142, right - native_serve, 38, BLUE_PALE, BLUE, 1.7, 2)
    f.centered((native_serve + right) / 2, 1169, "GPU", size=26, color=BLUE)
    return f


def pages_and_handoff(language: str) -> PaperFigure:
    f = PaperFigure("03_pages_and_capacity_handoff", "Workflow-aware KV Lifecycle and Admission", HEIGHT, language)
    f.heading(
        "KV Lifecycle, Page Migration and Timely Consumption",
        "工作流感知的 KV 生命周期与恢复感知准入",
        "Incremental backup → pressure reclaim → partial restore → native execution",
        "增量备份 → 压力回收 → 部分恢复 → 原生执行",
    )
    f.panel(40, 132, 980, 309, f.tr("(a) Incremental FULL", "(a) FULL 增量备份"), color=TEAL)
    f.text(66, 237, "HBM", 28, BLUE, 700)
    f.text(66, 330, "Host", 28, TEAL, 700)
    for i in range(8):
        x = 206 + i * 77
        f.page(x, 206, f"p{i}")
        f.page(x, 299, f"p{i}", fill=TEAL_PALE if i < 4 else PAPER,
               stroke=TEAL if i < 4 else GRAY, dashed=i >= 4)
        if i >= 4:
            f.arrow(((x + 27, 257), (x + 27, 292)), color=TEAL)
    f.text(854, 279, "ΔFULL", 27, TEAL, 600)
    f.node(65, 389, 168, 46, "HBM", PAPER, BLUE, 25)
    f.node(391, 389, 189, 46, "HBM + Host", PAPER, TEAL, 25)
    f.node(826, 389, 168, 46, "Host-only", PAPER, TEAL, 24)
    f.arrow(((234, 412), (390, 412)), color=TEAL)
    f.arrow(((581, 412), (825, 412)), color=TEAL)
    f.text(261, 379, "PREPARE", 23, TEAL)
    f.text(655, 379, f.tr("Pressure", "真实压力"), 24, TEAL)

    f.panel(1065, 132, 695, 309, f.tr("(b) Mamba checkpoint", "(b) Mamba checkpoint"), color=AMBER)
    for x, label, fill, color in (
        (1092, "s0", GRAY_PALE, GRAY), (1317, "s1", GRAY_PALE, GRAY),
        (1542, "s*", AMBER_PALE, AMBER),
    ):
        f.node(x, 215, 157, 61, label, fill, color, 32)
    f.arrow(((1250, 245), (1316, 245)), color=GRAY)
    f.arrow(((1475, 245), (1541, 245)), color=AMBER)
    f.text(1299, 196, "decode", 24, MUTED)
    f.node(1120, 348, 253, 56, "PREPARE", PAPER, GRAY, 25)
    f.cross(1388, 361, color=AMBER, size=26)
    f.node(1542, 348, 157, 56, "s*Host", TEAL_PALE, TEAL, 27)
    f.arrow(((1620, 347), (1620, 279)), color=AMBER)
    f.text(1660, 320, "H2D", 25, AMBER)
    f.text(1450, 426, "c* ↔ s*", 27, AMBER, 600)

    f.panel(40, 488, 1720, 311, f.tr("(c) Checkpoint → partial native restore",
                                                    "(c) Checkpoint → 原生部分恢复"))
    f.text(66, 563, "c*", 28, BLUE, 600)
    f.token(119, 607, "p0", BLUE_PALE, BLUE, 25, 23)
    f.token(257, 572, "p1", BLUE_PALE, BLUE, 25, 23)
    f.token(388, 606, "p5", TEAL_PALE, TEAL, 25, 23)
    f.token(257, 684, "g", GRAY_PALE, GRAY, 25, 23)
    f.arrow(((146, 600), (231, 578)), color=BLUE)
    f.arrow(((284, 578), (361, 600)), color=TEAL)
    f.arrow(((139, 625), (234, 674)), color=GRAY, dashed=True)
    f.cross(286, 675, size=22)
    f.text(66, 747, "session / epoch", 25, MUTED)
    f.arrow(((415, 606), (501, 606)), color=TEAL)

    f.text(519, 559, "Host", 26, TEAL, 600)
    f.text(519, 663, "HBM", 26, BLUE, 600)
    for i in range(6):
        x = 610 + i * 61
        f.rect(x, 568, 49, 39, TEAL_PALE, TEAL, 1.5, 2)
        f.centered(x + 24, 595, f"p{i}", size=23, color=TEAL, bold=True)
        fill, color = (BLUE_PALE, BLUE) if i < 2 else (TEAL_PALE, TEAL) if i < 4 else (PAPER, GRAY)
        f.rect(x, 630, 49, 39, fill, color, 1.6, 2, "5 4" if i >= 4 else None)
        f.centered(x + 24, 657, f"p{i}", size=23, color=color, bold=True)
        if 2 <= i < 4:
            f.arrow(((x + 24, 609), (x + 24, 626)), color=TEAL)
    f.text(519, 722, "B ≤ Cfree + Ccold − Gnext", 28, TEAL)
    f.text(519, 771, "ΔFULL + s*", 26, AMBER, 600)
    f.arrow(((973, 649), (995, 649), (995, 591), (1031, 591)), color=TEAL)
    f.node(1032, 564, 299, 54, "HiCache H2D", TEAL_PALE, TEAL, 28)
    f.node(1032, 698, 299, 54, "s*", AMBER_PALE, AMBER, 30)
    f.arrow(((1181, 618), (1181, 645), (1437, 645)), color=TEAL)
    f.arrow(((1332, 725), (1370, 725), (1370, 665), (1437, 665)), color=AMBER)
    f.diamond(1488, 655, 100, 71, TEAL_PALE, TEAL)
    f.centered(1488, 664, "&", size=31, bold=True)
    f.node(1594, 571, 139, 45, "ℓ0", BLUE_PALE, BLUE, 27)
    f.node(1594, 636, 139, 45, "ℓ1", BLUE_PALE, BLUE, 27)
    f.node(1594, 701, 139, 45, "ℓ2", BLUE_PALE, BLUE, 27)
    f.arrow(((1540, 655), (1568, 655), (1568, 593), (1593, 593)), color=BLUE)
    f.arrow(((1663, 617), (1663, 634)), color=BLUE)
    f.arrow(((1663, 682), (1663, 699)), color=BLUE)
    f.text(1442, 576, f.tr("layer-ready", "逐层就绪"), 23, BLUE)
    f.arrow(((1064, 618), (1007, 618), (1007, 775), (1429, 775)),
            color=TEAL, dashed=True)
    f.text(1337, 766, "ACK", 23, TEAL)

    f.panel(40, 847, 1040, 303, f.tr("(d) Child → parent capacity", "(d) Child → parent 容量"), color=TEAL)
    f.token(100, 974, "P", BLUE_PALE, BLUE, 27, 25)
    f.token(261, 922, "C1", TEAL_PALE, TEAL, 27, 24)
    f.token(261, 1064, "C2", AMBER_PALE, AMBER, 27, 24)
    f.arrow(((130, 962), (232, 931)), color=BLUE)
    f.arrow(((130, 988), (233, 1051)), color=BLUE)
    f.check(294, 896, size=19)
    for i, color in enumerate((GRAY, TEAL, TEAL, TEAL)):
        f.rect(369 + i * 50, 906, 40, 34, GRAY_PALE if i == 0 else TEAL_PALE,
               color, 1.6, 2)
    f.lock(378, 947, color=GRAY, scale=.8)
    f.arrow(((290, 922), (360, 922)), color=TEAL)
    f.arrow(((570, 922), (653, 922)), color=TEAL)
    f.node(655, 897, 175, 50, f.tr("Free list", "空闲列表"), PAPER, TEAL, 25)
    f.arrow(((831, 922), (879, 922)), color=TEAL)
    f.node(881, 897, 173, 50, "Parent KV", BLUE_PALE, BLUE, 25)
    f.text(445, 968, f.tr("private", "私有"), 24, TEAL)
    f.line(905, 991, 905, 1127, GRAY, 1.7, "6 5")
    f.text(914, 1004, "RETURN", 23, INK)
    f.rect(403, 1093, 502, 31, AMBER_PALE, AMBER, 1.6, 2)
    f.centered(645, 1116, "C2: decode", size=23, color=AMBER)
    f.rect(680, 1038, 194, 35, TEAL_PALE, TEAL, 1.7, 2)
    f.centered(777, 1063, "H2D", size=24, color=TEAL)
    f.rect(907, 1038, 147, 35, GRAY_PALE, GRAY, 1.6, 2)
    f.centered(980, 1063, "demand", size=22, color=MUTED)
    f.arrow(((742, 948), (742, 985), (777, 985), (777, 1036)), color=TEAL)
    f.lock(249, 1107, color=AMBER, scale=.6)

    f.panel(1125, 847, 635, 349, f.tr("(e) Restore-aware admission", "(e) 恢复感知准入"))
    for x, label, color, fill in (
        (1152, "r_old", GRAY, GRAY_PALE), (1346, "r_hot", BLUE, BLUE_PALE),
        (1540, "P", TEAL, TEAL_PALE),
    ):
        f.node(x, 913, 166, 48, label, fill, color, 27)
    f.arrow(((1623, 962), (1623, 1003), (1520, 1003)), color=TEAL)
    f.arrow(((1429, 962), (1429, 980)), color=BLUE)
    f.arrow(((1235, 962), (1235, 1003), (1348, 1003)), color=GRAY, dashed=True)
    f.text(1152, 1046, f.tr("aging", "老化"), 24, GRAY)
    f.lock(1655, 967, color=TEAL, scale=.6)
    f.node(1350, 982, 168, 44, f.tr("rank", "排序"), PAPER, BLUE, 26)
    f.arrow(((1433, 1027), (1433, 1060)), color=BLUE)
    f.node(1183, 1063, 142, 49, f.tr("KV", "KV"), TEAL_PALE, TEAL, 27)
    f.node(1536, 1063, 142, 49, f.tr("slot", "执行槽"), PAPER, BLUE, 26)
    f.arrow(((1326, 1087), (1385, 1087)), color=TEAL)
    f.arrow(((1536, 1087), (1476, 1087)), color=BLUE)
    f.circle(1433, 1087, 25, PAPER, INK, 1.8)
    f.centered(1433, 1097, "&", size=32, bold=True)
    f.arrow(((1433, 1114), (1433, 1159)), color=BLUE)
    f.node(1342, 1161, 182, 35, "GPU", BLUE_PALE, BLUE, 26)
    for x, label, fill, color, dashed in (
        (64, f.tr("resident", "已驻留"), BLUE_PALE, BLUE, False),
        (263, f.tr("selected", "本次恢复"), TEAL_PALE, TEAL, False),
        (478, f.tr("deferred", "延后"), PAPER, GRAY, True),
    ):
        f.rect(x, 1170, 27, 21, fill, color, 1.5, 1, "5 4" if dashed else None)
        f.text(x + 39, 1189, label, 24, color)
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
            "Architecture baseline: 2026-10-14 development implementation, not the frozen "
            "code of every running experiment. RCCG receives runtime events; GraphDelta "
            "updates affected wait groups. Text/phase/work inference runs in an independent "
            "CPU process. Tool history supplies separate conditional timing. "
            "NativeAdmissionRuntime combines causal identity, checkpoint, timing, capacity "
            "and next-batch observations. Waiting migrations can run after forward dispatch "
            "under the supported overlap configuration. The native waiting queue, "
            "PrefillAdder, allocation and data dependencies remain authoritative. "
            "Dashed arrows show feedback, not a claim that all control work is asynchronous. "
            "Explicit completion notices are harness protocol and must be identical "
            "across comparison arms. Sources: causal_graph.py; semantic_report_worker.py; sglang_v0520_runtime.py "
            "(scheduler_step, dispatch_after_forward, plan_native_prefill); staging patch.",
            "架构基线：2026-10-14 当前开发实现，不代表所有运行实验的冻结代码。"
            "RCCG 接收运行时事件，GraphDelta 增量更新受影响的等待成员组。"
            "文本阶段/工作量推理在独立 CPU 进程执行，工具历史提供条件时机提示。"
            "NativeAdmissionRuntime 联合因果身份、checkpoint、时机、容量和下一批观测。"
            "受支持的 overlap 配置下，等待迁移规划安排在 forward 发射后。"
            "原生可见等待队列、PrefillAdder、分配器和数据依赖保持权威。"
            "虚线表达观测反馈，不表示所有控制工作均异步。"
            "显式完成通知属于 harness 协议，应在所有对照组保持一致。"
            "代码：causal_graph.py、semantic_report_worker.py、sglang_v0520_runtime.py "
            "的 scheduler_step / dispatch_after_forward / plan_native_prefill，以及 staging patch。",
        )
    if figure.filename.startswith("02_"):
        return figure.tr(
            "The stage model uses frozen pooled text features plus event/progress features; "
            "the conditional work head can be fitted independently. Work bounds are "
            "calibrated bounds, not a calibrated joint JOIN interval. The current "
            "content-driven JOIN_ALL path targets the single pending child; earlier notices "
            "can be retained and rebound after sibling RETURN. A latest forecast can move "
            "its work endpoint in either direction before enqueue. The center-overrun path "
            "uses a still-valid upper endpoint. R/rate estimates generation time, not future "
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
            "工作量区间不是已校准的完整 JOIN 联合区间。当前内容驱动的 JOIN_ALL 路径"
            "关注唯一未返回 child；较早通知可以保留，在 sibling 返回后重新绑定。"
            "新预测可在入队前前移或后移工作端点，center 被越过时仅使用仍有效的 upper。"
            "R/实测速率是剩余生成时长提示，不包含未来排队，也不是 RETURN 墙钟保证。"
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
        font = drawing_font(figure.language, size, weight)
        width = figure.measure(value, size, weight)[0]
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
        run.font.name = figure.family
        run.font.size = Pt(size * self.point_scale)
        run.font.bold = weight >= 600
        run.font.color.rgb = self.rgb(element.get("fill", INK))
        properties = run._r.get_or_add_rPr()
        properties.set("lang", "zh-CN" if figure.language == "zh" else "en-US")
        properties.set("spc", "0")
        east_asian = OxmlElement("a:ea")
        east_asian.set("typeface", figure.family)
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
        "renderer": "CairoSVG / Pillow / python-pptx",
        "figures": manifest, "powerpoints": powerpoints,
    }, indent=2) + "\n")
    print(json.dumps({
        "output": str(args.output), "figures": manifest, "powerpoints": powerpoints,
    }, indent=2))


if __name__ == "__main__":
    main()
