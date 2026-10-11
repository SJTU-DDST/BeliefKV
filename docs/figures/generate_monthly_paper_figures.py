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
WIDTH = 1440
PAPER_WIDTH_INCHES = 7.2
DPI = 600
FONT_ROOT = Path("/usr/share/fonts")


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

    def lines(self, x, y, values, *, width, size=23, leading=33, color=INK):
        for index, value in enumerate(values):
            actual = self.measure(value, size)[0]
            if actual > width:
                raise ValueError(f"{self.filename}: {value!r} is {actual}px in {width}px")
            self.text(x, y + index * leading, value, size, color)

    def panel(self, x, y, w, h, title, *, color=BLUE, fill=PAPER):
        self.rect(x, y, w, h, fill, RULE, 1.7, 3)
        self.line(x + 1, y + 1, x + w - 1, y + 1, color, 3)
        self.text(x + 22, y + 40, title, 26, color, 700)

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
    f = PaperFigure("01_system_overview", "BeliefKV System Architecture", 920, language)
    f.text(40, 49, f.tr(
        "BeliefKV: Workflow-aware KV Management",
        "BeliefKV：面向动态 Agent 工作流的 KV 管理",
    ), 34, INK, 700)
    f.text(40, 81, f.tr(
        "Dynamic dependencies  /  Predictive transfers  /  Native execution",
        "动态依赖感知  /  预测性迁移  /  原生执行协同",
    ), 22, MUTED)

    f.panel(40, 111, 1360, 176, f.tr("(a) Agent harness", "(a) Agent 执行框架"))
    f.token(104, 214, "P", BLUE_PALE, BLUE, 24, 25)
    f.token(247, 190, "C1", PAPER, TEAL, 23, 21)
    f.token(247, 250, "C2", PAPER, TEAL, 23, 21)
    f.diamond(390, 220, 91, 67, AMBER_PALE, AMBER)
    f.centered(390, 226, "JOIN", size=21, bold=True)
    f.arrow(((128, 207), (221, 192)), color=BLUE)
    f.arrow(((128, 221), (221, 247)), color=BLUE)
    f.arrow(((272, 190), (323, 190), (353, 204)), color=TEAL)
    f.arrow(((272, 250), (323, 250), (353, 236)), color=TEAL)
    f.arrow(((436, 220), (511, 220)), color=BLUE)
    f.token(538, 220, "P", BLUE_PALE, BLUE, 24, 25)
    f.centered(162, 180, "SPAWN", size=17, color=MUTED)
    f.centered(316, 274, "RETURN", size=17, color=MUTED)
    f.line(602, 174, 602, 267, RULE, 1.5)
    f.lines(630, 191, (
        f.tr("Events: SPAWN, TOOL, RETURN, JOIN", "事件：SPAWN、工具调用、RETURN、JOIN"),
        f.tr("Signals: completion notice and delivered text", "信号：收尾通知、已输出正文"),
        f.tr("Progress: observed decode and GPU service", "进度：已观测 decode 与 GPU 服务"),
    ), width=737, size=24, leading=35)

    f.centered(233, 319, f.tr("Causal updates", "因果状态更新"), size=21, color=BLUE)
    f.arrow(((233, 287), (233, 349)), color=BLUE)
    f.centered(676, 319, f.tr("Online observations", "在线观测"), size=21, color=AMBER)
    f.arrow(((676, 287), (676, 349)), color=AMBER)
    f.panel(40, 351, 394, 257, f.tr("(b) Causal state", "(b) 因果状态"), color=BLUE)
    f.lines(63, 433, (
        f.tr("Runtime workflow graph", "运行时工作流依赖图"),
        f.tr("JOIN_ALL / tool wait / ready", "JOIN_ALL / 工具等待 / 可执行"),
        f.tr("Context identity and epoch", "Context 身份与版本"),
    ), width=343, size=24, leading=39)
    for index, (label, fill, color) in enumerate((
        ("WAIT_JOIN", AMBER_PALE, AMBER), ("READY", TEAL_PALE, TEAL),
    )):
        f.node(63 + index * 179, 545, 161, 39, label, fill, color, 20)

    f.panel(470, 351, 414, 257, f.tr("(c) Prediction", "(c) 预测模块"), color=AMBER)
    f.text(492, 435, f.tr("Child phase + remaining work", "Child 阶段与剩余工作量"), 21, INK, 600)
    f.text(492, 469, f.tr("Text, notice and decode progress", "正文、通知与 decode 进度"), 21, MUTED)
    f.line(492, 493, 861, 493, RULE, 1.4)
    f.text(492, 530, f.tr("Tool residual-time distribution", "工具剩余时间分布"), 21, INK, 600)
    f.text(492, 564, f.tr("Type, elapsed time and history", "类型、已执行时间与历史"), 21, MUTED)
    f.arrow(((434, 485), (469, 485)), color=BLUE)
    f.arrow(((884, 485), (929, 485)), color=AMBER)
    f.centered(907, 464, f.tr("Hints", "提示"), size=17, color=AMBER)

    f.panel(930, 351, 470, 257, f.tr("(d) Runtime decisions", "(d) Runtime 决策"), color=TEAL)
    f.lines(952, 429, (
        f.tr("Safe checkpoint + valid Host copy", "安全 checkpoint 与有效 Host 副本"),
        f.tr("Capacity + transfer service cost", "容量与传输服务成本"),
        f.tr("Missing-page transfer planning", "缺失页迁移规划"),
        f.tr("Admission + residency + aging", "准入、驻留保护与请求老化"),
    ), width=426, size=24, leading=41)

    f.arrow(((1165, 608), (1165, 686)), color=TEAL)
    f.text(1185, 653, f.tr("Intents", "调度意图"), 21, TEAL)
    f.arrow(((666, 686), (666, 646), (985, 646), (985, 608)), color=BLUE, dashed=True)
    f.text(685, 636, f.tr("Capacity / ACK / first use", "容量 / ACK / 首次复用"), 21, BLUE)

    f.panel(40, 688, 1360, 178, f.tr(
        "(e) SGLang: physical cache and execution authority",
        "(e) SGLang：物理缓存与执行事实源",
    ), color=BLUE)
    f.node(62, 762, 266, 75, f.tr("Native GPU batches", "原生 GPU 批处理"), BLUE_PALE, BLUE, 21)
    f.node(397, 762, 410, 75, "Radix / HiCache / Allocators", GRAY_PALE, GRAY, 23)
    f.arrow(((329, 799), (396, 799)), color=BLUE)
    f.arrow(((808, 799), (867, 799)), color=BLUE)
    for x, label, fill, color in (
        (867, "HBM", BLUE_PALE, BLUE), (1190, "Host", TEAL_PALE, TEAL),
    ):
        f.rect(x, 757, 188, 83, fill, color, 1.8, 2)
        f.centered(x + 94, 790, label, size=25, color=color, bold=True)
        f.centered(x + 94, 821, "FULL + Mamba", size=19, color=MUTED)
    f.arrow(((1056, 779), (1189, 779)), color=TEAL)
    f.centered(1121, 768, "D2H", size=18, color=TEAL)
    f.arrow(((1190, 823), (1056, 823)), color=AMBER)
    f.centered(1121, 813, "H2D", size=18, color=AMBER)
    f.text(40, 900, f.tr(
        "Models estimate completion; runtime selects actions; native dependencies govern execution.",
        "模型估计完成进度，runtime 决定动作，原生数据依赖决定执行。",
    ), 22, MUTED)
    return f


def prediction_and_overlap(language: str) -> PaperFigure:
    f = PaperFigure("02_prediction_and_overlap", "Prediction and Scheduling Coordination", 1010, language)
    f.text(40, 49, f.tr(
        "From Completion Signals to Useful Prefetch",
        "从完成信号到可被执行消费的预取",
    ), 34, INK, 700)
    f.text(40, 82, f.tr(
        "Separate remaining work, scheduling opportunity and physical data dependencies",
        "分离剩余工作量、调度机会与物理数据依赖",
    ), 22, MUTED)

    cols = ((40, 357), (473, 379), (928, 472))
    f.panel(*cols[0][:1], 121, cols[0][1], 129, f.tr("Child observations", "Child 在线观测"), color=BLUE)
    f.lines(63, 200, (
        f.tr("Notice, text, decode progress", "通知、正文与 decode 进度"),
        f.tr("Already delivered information", "仅使用已经送达的信息"),
    ), width=313, size=21, leading=30)
    f.panel(473, 121, 379, 129, f.tr("Phase / work model", "阶段 / 工作量模型"), color=AMBER)
    f.lines(496, 200, (
        f.tr("Final report or further tool use", "最终报告或继续调用工具"),
        f.tr("Conditional remaining work", "条件剩余生成工作量"),
    ), width=332, size=21, leading=30)
    f.panel(928, 121, 472, 129, f.tr("Advisory output", "预测输出"), color=AMBER)
    f.lines(951, 200, (
        f.tr("Stage score + remaining-token bounds", "阶段分数与剩余 token 区间"),
        f.tr("Observed EOS: separate signal", "已观测 EOS 作为独立信号"),
    ), width=425, size=21, leading=30)
    f.arrow(((397, 200), (472, 200)), color=BLUE)
    f.arrow(((852, 200), (927, 200)), color=AMBER)
    f.arrow(
        ((1400, 200), (1420, 200), (1420, 427), (1260, 427), (1260, 447)),
        color=AMBER,
    )

    f.panel(40, 283, 357, 122, f.tr("Tool observations", "工具在线观测"), color=BLUE)
    f.lines(63, 356, (
        f.tr("Type, elapsed time, history", "工具类型、已执行时间、历史"),
        f.tr("History of completed calls", "历史仅来自已完成调用"),
    ), width=313, size=21, leading=30)
    f.panel(473, 283, 379, 122, f.tr("Residual-time model", "剩余时间模型"), color=AMBER)
    f.lines(496, 356, (
        f.tr("Given the tool is still running", "以工具仍在执行为条件"),
        f.tr("Update as observations arrive", "随新观测滚动更新"),
    ), width=332, size=21, leading=30)
    f.panel(928, 283, 472, 122, f.tr("Advisory output", "预测输出"), color=AMBER)
    f.lines(951, 356, (
        f.tr("Residual CDF / timing bounds", "剩余时间 CDF / 时间区间"),
        f.tr("No learned net-benefit decision", "不学习离线不可识别的净收益"),
    ), width=425, size=21, leading=30)
    f.arrow(((397, 356), (472, 356)), color=BLUE)
    f.arrow(((852, 356), (927, 356)), color=AMBER)
    f.arrow(((1164, 405), (1164, 447)), color=AMBER)

    f.panel(40, 449, 1360, 138, f.tr(
        "(a) Runtime turns hints into bounded actions",
        "(a) Runtime 将预测提示转为有界动作",
    ), color=TEAL)
    for x, title, detail in (
        (63, f.tr("Causal readiness", "因果就绪"), f.tr("JOIN / tool state", "JOIN / 工具状态")),
        (391, f.tr("Reusable checkpoint", "可复用 checkpoint"), f.tr("Valid missing Host pages", "有效 Host 缺失页")),
        (732, f.tr("Capacity and cost", "容量与传输成本"), f.tr("FULL / Mamba / H2D", "FULL / Mamba / H2D")),
        (1072, f.tr("Service opportunity", "执行机会"), f.tr("Admission + residency", "准入与驻留保护")),
    ):
        f.text(x, 531, title, 23, TEAL, 600)
        f.text(x, 563, detail, 20, MUTED)

    f.text(40, 639, f.tr(
        "(b) Overlap with the observed wait or final-generation interval",
        "(b) 与工具等待或最终生成区间重叠",
    ), 26, BLUE, 700)
    x0, trigger, boundary, ack, service, native_service, right = 315, 720, 982, 1050, 1150, 1295, 1380
    f.line(boundary, 674, boundary, 936, GRAY, 1.8, "7 6")
    f.line(ack, 748, ack, 850, TEAL, 1.6, "4 5")
    f.centered(boundary, 669, "RETURN / TOOL_END", size=20, color=INK, bold=True)
    f.text(44, 726, f.tr("Child / tool", "Child / 工具"), 24, INK, 600)
    f.text(44, 817, f.tr("Predictive path", "预测路径"), 24, TEAL, 600)
    f.text(44, 908, f.tr("Demand path", "需求恢复路径"), 24, GRAY, 600)
    for y in (713, 804, 895):
        f.line(x0, y, right, y, RULE, 1.2)
    f.rect(x0, 688, boundary - x0, 43, BLUE_PALE, BLUE, 1.7, 2)
    f.centered((x0 + boundary) / 2, 716, f.tr(
        "Final generation / external tool execution", "最终报告生成 / 外部工具执行",
    ), size=22, color=BLUE)
    for x in (382, 465, 548, 631):
        f.circle(x, 754, 4.5, AMBER, AMBER, 1)
    f.arrow(((649, 754), (704, 754)), color=AMBER)
    f.text(375, 780, f.tr("Rolling observations", "滚动观测"), 20, AMBER)

    f.rect(trigger, 780, ack - trigger, 50, TEAL_PALE, TEAL, 1.8, 2)
    f.centered((trigger + ack) / 2, 812, f.tr("Missing-page H2D", "缺失页 H2D"), size=22, color=TEAL)
    f.rect(ack, 780, service - ack, 50, GRAY_PALE, RULE, 1.4, 1)
    f.rect(service, 780, right - service, 50, BLUE_PALE, BLUE, 1.8, 2)
    f.centered((service + right) / 2, 812, f.tr("GPU service", "GPU 服务"), size=22, color=BLUE)
    f.centered(ack, 857, "ACK", size=19, color=TEAL)
    f.text(1163, 855, f.tr("First reuse", "首次复用"), 20, BLUE)
    f.line(trigger, 760, boundary, 760, TEAL, 2)
    f.line(trigger, 754, trigger, 766, TEAL, 2)
    f.line(boundary, 754, boundary, 766, TEAL, 2)
    f.centered((trigger + boundary) / 2, 750, f.tr("Advance", "提前量"), size=19, color=TEAL)
    f.rect(boundary, 871, native_service - boundary, 50, GRAY_PALE, GRAY, 1.8, 2)
    f.centered((boundary + native_service) / 2, 904, f.tr("Demand H2D", "需求 H2D"), size=22, color=MUTED)
    f.rect(native_service, 871, right - native_service, 50, BLUE_PALE, BLUE, 1.8, 2)
    f.centered((native_service + right) / 2, 902, f.tr("Serve", "服务"), size=19, color=BLUE)
    f.lines(40, 970, (
        f.tr(
            "Schematic, not measured speedup. Partial overlap is useful; native data dependencies govern execution.",
            "示意图，非实测加速。部分传输重叠也可利用；执行遵循原生数据依赖。",
        ),
    ), width=1360, size=22)
    return f


def pages_and_handoff(language: str) -> PaperFigure:
    f = PaperFigure("03_pages_and_capacity_handoff", "Page Migration and Child-Parent Capacity Handoff", 995, language)
    f.text(40, 49, f.tr(
        "Incremental Migration and Child-to-Parent Capacity Handoff",
        "增量迁移与 Child → Parent 容量交接",
    ), 31, INK, 700)
    f.text(40, 82, f.tr(
        "Agent semantics select the beneficiary; native pages define the physical transfer",
        "Agent 语义选择受益对象，原生页决定实际迁移范围",
    ), 22, MUTED)

    f.panel(40, 123, 751, 298, f.tr(
        "(a) Incremental FULL backup", "(a) FULL 增量备份",
    ), color=TEAL)
    f.text(62, 238, "HBM", 25, BLUE, 700)
    f.text(62, 340, "Host", 25, TEAL, 700)
    for i in range(8):
        x = 236 + i * 64
        f.page(x, 206, f"p{i}")
        f.page(x, 295, f"p{i}", fill=TEAL_PALE if i < 4 else PAPER,
               stroke=TEAL if i < 4 else GRAY, dashed=i >= 4)
        if i >= 4:
            f.arrow(((x + 27, 258), (x + 27, 289)), color=TEAL)
    f.centered(355, 375, f.tr("Valid copy: skip", "已有有效副本：跳过"), size=21, color=MUTED)
    f.centered(610, 375, f.tr("Copy missing extents", "仅复制缺失段"), size=20, color=TEAL)
    f.lines(62, 407, (f.tr(
        "PREPARE_HOST: missing FULL; HBM remains resident.",
        "PREPARE_HOST 仅备份缺失 FULL；备份本身不释放 HBM。",
    ),), width=704, size=21)

    f.panel(830, 123, 570, 298, f.tr(
        "(b) Necessary Mamba checkpoint", "(b) 必要 Mamba checkpoint",
    ), color=AMBER)
    for x, label, fill, color in (
        (868, "s0", GRAY_PALE, GRAY), (1035, "s1", GRAY_PALE, GRAY),
        (1202, "s2", AMBER_PALE, AMBER),
    ):
        f.node(x, 206, 130, 68, label, fill, color, 25)
    f.arrow(((1001, 240), (1033, 240)), color=MUTED)
    f.arrow(((1168, 240), (1200, 240)), color=AMBER)
    f.centered(1098, 311, f.tr("Select the required checkpoint state", "选择目标 checkpoint 所需状态"), size=22, color=AMBER)
    f.lines(852, 355, (
        f.tr("No speculative Mamba in PREPARE_HOST.", "PREPARE_HOST 不投机备份 Mamba。"),
        f.tr("Native cache retains other valid states.", "其他有效状态仍由原生缓存管理。"),
    ), width=525, size=22, leading=33)

    f.panel(40, 464, 1360, 440, f.tr(
        "(c) Coordinate child reclamation, parent restore and admission",
        "(c) 协同 Child 回收、Parent 恢复与准入",
    ), color=BLUE)
    f.token(117, 599, "P", BLUE_PALE, BLUE, 25, 25)
    f.token(263, 568, "C1", TEAL_PALE, TEAL, 26, 22)
    f.token(263, 652, "C2", AMBER_PALE, AMBER, 26, 22)
    f.arrow(((142, 590), (235, 566)), color=BLUE)
    f.arrow(((142, 610), (235, 646)), color=BLUE)
    f.centered(114, 652, "WAIT_JOIN", size=18, color=MUTED)
    f.centered(261, 531, "RETURN", size=18, color=TEAL, bold=True)
    f.centered(265, 704, f.tr("Still running", "仍在运行"), size=19, color=AMBER)
    f.arrow(((291, 568), (371, 568)), color=TEAL)

    f.rect(374, 532, 289, 176, TEAL_PALE, TEAL, 1.8, 2)
    f.centered(518, 569, f.tr("Reclaim private pages", "回收私有页"), size=21, color=TEAL, bold=True)
    f.lines(395, 610, (
        f.tr("No live or shared use", "无活跃或共享引用"),
        f.tr("No in-flight transfer use", "无在途传输依赖"),
        f.tr("Keep shared prefixes", "保留共享前缀"),
    ), width=249, size=19, leading=33)
    f.arrow(((664, 620), (721, 620)), color=TEAL)
    f.rect(723, 532, 267, 176, GRAY_PALE, GRAY, 1.8, 2)
    f.centered(856, 570, f.tr("Native free lists", "原生空闲列表"), size=23, bold=True)
    f.node(746, 598, 221, 39, f.tr("FULL pages", "FULL 页"), PAPER, BLUE, 21)
    f.node(746, 650, 221, 39, f.tr("Mamba slots", "Mamba 槽位"), PAPER, AMBER, 21)
    f.arrow(((991, 620), (1046, 620)), color=BLUE)
    f.rect(1048, 532, 324, 176, BLUE_PALE, BLUE, 1.8, 2)
    f.centered(1210, 570, f.tr("Parent restore + service", "Parent 恢复与执行"), size=21, color=BLUE, bold=True)
    f.lines(1070, 610, (
        f.tr("Load missing prefix pages", "加载缺失 checkpoint 页"),
        f.tr("Reserve input and decode", "预留输入与 decode 增长"),
        f.tr("Bounded restore admission", "有界恢复感知准入"),
    ), width=280, size=19, leading=33)

    f.line(124, 747, 1366, 747, RULE, 1.3)
    f.text(65, 794, f.tr("Sibling", "较早结束的 Child"), 22, TEAL, 600)
    f.text(65, 843, f.tr("Last child", "最后一个 Child"), 22, AMBER, 600)
    f.circle(348, 785, 5, TEAL, TEAL, 1)
    f.text(368, 794, f.tr("RETURN → capacity released", "RETURN → 释放容量"), 21, TEAL)
    f.rect(700, 764, 359, 41, TEAL_PALE, TEAL, 1.6, 2)
    f.centered(879, 792, f.tr("Parent predictive H2D", "Parent 预测性 H2D"), size=21, color=TEAL)
    f.rect(349, 817, 791, 39, AMBER_PALE, AMBER, 1.6, 2)
    f.centered(744, 844, f.tr("Final generation continues", "最后生成仍在继续"), size=21, color=AMBER)
    f.line(1141, 755, 1141, 870, GRAY, 1.6, "6 5")
    f.text(1156, 841, "RETURN", 19, INK, 600)
    f.lines(40, 952, (
        f.tr(
            "Earlier siblings can enable pre-RETURN H2D. The last child's capacity is available only after completion.",
            "较早结束的 sibling 可支持 RETURN 前预取；最后一个 child 的容量只能在结束后使用。",
        ),
        f.tr(
            "Reclaimed capacity uses native allocation; shared and still-live pages remain intact.",
            "回收容量通过原生分配器使用；共享页与仍在执行的页面保留。",
        ),
    ), width=1360, size=22, leading=31)
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
            "System overview. The workflow graph is discovered from live events. "
            "Models estimate child/tool completion; runtime selects transfers and admission. "
            "SGLang remains the authority for cache validity, allocation and execution. "
            "This architecture diagram does not establish a throughput improvement.",
            "系统架构。运行时事件构建动态工作流依赖图；模型估计 child/tool 完成进度，"
            "runtime 决定迁移与准入。SGLang 负责物理缓存有效性、分配和执行。"
            "架构图不代表已经取得吞吐提升。",
        )
    if figure.filename.startswith("02_"):
        return figure.tr(
            "Schematic timeline, not empirical speedup. Child prediction uses delivered text, "
            "completion notices and observed progress. Future service is not an online input. "
            "Tool prediction conditions on elapsed execution. Partial pre-boundary H2D overlap "
            "can be useful; native per-layer dependencies govern whether execution can begin.",
            "时序为机制示意，不是实测加速。Child 模型使用已送达正文、收尾通知及已观测进度，"
            "不将未来服务时间用作在线输入。工具模型以已执行时长为条件滚动预测。"
            "部分传输重叠也可利用；是否能开始执行由原生逐层数据依赖决定。",
        )
    return figure.tr(
        "PREPARE_HOST copies missing FULL extents without releasing HBM and does not "
        "speculatively copy Mamba. Restore only the state required by the selected checkpoint. "
        "Reclaim terminal child pages only when private, unreferenced and free of in-flight use. "
        "Earlier siblings can enable predictive H2D while the last child runs. The last child's "
        "own capacity is available only after completion. Native allocation need not reuse page IDs.",
        "PREPARE_HOST 仅增量备份缺失 FULL，不释放 HBM、不投机备份 Mamba；"
        "恢复时选择目标 checkpoint 必需的状态。仅回收终态 child 的私有、无引用、"
        "无在途使用页面。较早返回的 sibling 可在最后一个 child 运行时为预测 H2D 提供容量；"
        "最后一个 child 自身容量只能在完成后使用。容量交接不要求复用相同物理页号。",
    )


class PowerPointExporter:
    """Map SVG drawing primitives to editable DrawingML objects."""

    def __init__(self, language: str):
        self.language = language
        self.presentation = Presentation()
        # Match the diagram canvas instead of squeezing dense figures onto a wide slide.
        self.presentation.slide_width = Inches(14.4)
        self.presentation.slide_height = Inches(10.1)
        self.unit = Inches(.01)
        self.presentation.core_properties.title = (
            "BeliefKV 系统架构图" if language == "zh" else "BeliefKV Architecture Figures"
        )
        self.presentation.core_properties.author = "BeliefKV"
        self.presentation.core_properties.subject = "2026-08-15 through 2026-10-11"
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
        shape.line.width = Pt(float(attributes.get("stroke-width", 1)) * .72)
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
        run.font.size = Pt(size * .72)
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
        offset_y = (1010 - figure.height) / 2
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
        "source_revision": revision, "reported_period": "2026-08-15 through 2026-10-11",
        "source_revision_scope": "architecture baseline; generator identity is recorded by SHA256",
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
