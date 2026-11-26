#!/usr/bin/env python3
"""Draw an English system overview and dedicated editable D1/D2 schematics."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from generate_monthly_paper_figures import ROOT, PaperFigure, PowerPointExporter, vector_commands


OUTPUT = ROOT / "docs/archive/presentations/beliefkv_two_designs_20261120"
DATE = "2026-11-07"
D2_REVISION_DATE = "2026-11-20"
WIDTH = 1680
COLLECTION_HEIGHT = 930
PALETTE_PATH = OUTPUT / "paper_palette.json"
PALETTE = json.loads(PALETTE_PATH.read_text())
C = {key: value.lower() for key, value in PALETTE["roles"].items()}
INK, PAPER = C["ink"], C["paper"]
CONTROL_EDGE, CONTROL_PALE = C["control_edge"], C["control_background"]
DATA_EDGE, DATA_PALE = C["data_edge"], C["data_background"]
BLUE_PALE, AMBER_PALE = C["control_header"], C["data_header"]
FLOW, BLUE, ACTION = C["execution_arrow"], C["control_edge"], C["control_arrow"]
GRAY, GRAY_PALE, BACKPLATE = C["neutral_edge"], C["neutral_background"], C["donor"]
OWNERS = {name: C[role] for name, role in (
    ("P", "parent"), ("C1", "child_1"), ("C2", "child_2"),
    ("T", "donor"), ("B", "other_workflow"),
)}
D2_LANES = {
    "Workflow A": ("d2_workflow_a", "workflow"),
    "Workflow B": ("d2_workflow_b", "workflow"),
    "HBM": ("d2_hbm", "stack"),
    "DMA": ("d2_dma", "copy_engine"),
    "Host DRAM": ("d2_host", "dimm"),
}
D2_OWNERS = {
    owner: tuple(C[key] for key in keys)
    for owner, keys in {
        "A": ("d2_workflow_a_edge", "d2_workflow_a_edge", "d2_a_idle"),
        "a1": ("d2_a_children", "d2_workflow_a_edge", "d2_a_idle"),
        "a2": ("d2_a_children", "d2_workflow_a_edge", "d2_a_idle"),
        "B": ("d2_workflow_b_edge", "d2_workflow_b_edge", "d2_b_idle"),
        "b1": ("d2_b_children", "d2_workflow_b_edge", "d2_b_idle"),
        "b2": ("d2_b_children", "d2_workflow_b_edge", "d2_b_idle"),
        "b3": ("d2_b_children", "d2_workflow_b_edge", "d2_b_idle"),
        "b1+b2": ("d2_b_children", "d2_workflow_b_edge", "d2_b_idle"),
    }.items()
}
D2_TEXTURES = {"a1": "dots", "a2": "diagonal", "b1": "dots", "b2": "diagonal", "b3": "dashes"}
D2_MARK_AXES = {
    "dots": ((2, 0), (0, 2)),
    "diagonal": ((4.5, 4), (-.7, .8)),
    "dashes": ((4.6, 0), (0, 1.15)),
}
D2_LABEL_SIZE = 21
SOURCES = (
    "beliefkv/control/causal_graph.py",
    "beliefkv/policy/workflow_combination.py",
    "beliefkv/predictor/workflow_work.py",
    "beliefkv/predictor/project_tool_history.py",
    "beliefkv/predictor/tool_survival.py",
    "beliefkv/runtime/workflow_combination.py",
    "beliefkv/runtime/workflow_work_worker.py",
    "beliefkv/runtime/workflow_size_admission.py",
    "beliefkv/runtime/reference_thunder_admission.py",
    "beliefkv/runtime/reference_tool_replacement.py",
    "beliefkv/runtime/predicted_tool_offload.py",
    "beliefkv/runtime/join_blocked_handoff.py",
    "beliefkv/runtime/join_physical_recovery.py",
    "beliefkv/runtime/demand_prefill_pipeline.py",
    "beliefkv/runtime/full_prefix_migration.py",
    "beliefkv/runtime/sglang_v0520_runtime.py",
    "beliefkv/runtime/sglang_v0520_prediction.py",
    "beliefkv/runtime/sglang_v0520_physical.py",
    "patches/sglang-v0.5.20-native-session-close-staging.patch",
)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class CompactFigure(PaperFigure):
    def __init__(self, stem, title, height, notes, *, width=WIDTH):
        super().__init__(stem, title, height, "en", width=width)
        self.notes = notes
        self.parts = [
            f'<rect width="100%" height="100%" fill="{PAPER}"/>',
            "<defs>",
            *(self.flow_marker(name, color, kind, size)
              for name, color, kind in (
                  ("execution", FLOW, "triangle"), ("hint", BLUE, "open"),
                  ("action", ACTION, "notched"), ("inactive", GRAY, "open"),
              ) for size in (12, 18, 24, 32)),
            "</defs>",
            "<style>text{letter-spacing:0}"
            "path,line{stroke-linejoin:round;stroke-linecap:round}</style>",
        ]

    def heading(self, value):
        self.text(28, 44, value, 31, INK, 700)

    def panel(self, x, y, w, h, title, *, color=BLUE_PALE, fill=PAPER,
              size=26, band=38, border=CONTROL_EDGE, title_color=INK, role=None):
        super().panel(x, y, w, h, title, color=color, fill=fill, size=size,
                      band=band, border=border, title_color=title_color, role=role)

    def _route(self, commands, *, color, dashed, width, geometry):
        if color == INK:
            color = FLOW
        role, kind = (
            ("inactive", "open") if color == GRAY else
            ("hint", "open") if dashed else
            ("action", "notched") if color == ACTION else ("execution", "triangle")
        )
        width = max(width, 4)
        points = [point for _, part in commands for point in part]
        last_length = math.dist(points[-2], points[-1])
        size = 12 if last_length < 28 else 24 if role == "action" and last_length > 70 else 18
        data = " ".join(command + " ".join(f"{x:.4f},{y:.4f}" for x, y in part)
                        for command, part in commands)
        self.parts.append(
            f'<path d="{data}" fill="none" stroke="{PAPER}" stroke-width="{width + 2}"/>'
        )
        dash = ' stroke-dasharray="10 7"' if dashed else ""
        self.parts.append(
            f'<path d="{data}" fill="none" stroke="{color}" stroke-width="{width}"{dash} '
            f'marker-end="url(#{role}-{size})" data-arrow-kind="{kind}" '
            f'data-arrow-size="{size}" data-flow-role="{role}" '
            f'data-flow-geometry="{geometry}"/>'
        )
        self.flow_types[role] += 1
        self.flow_geometry[geometry] += 1

    def arrow(self, points, *, color=FLOW, dashed=False, width=4.8):
        super().arrow(points, color=color, dashed=dashed, width=width)

    def memory_pool(self, x, y, w, h, title, tier):
        prefix = tier.lower()
        edge, body, page = (C[prefix + suffix] for suffix in
                            ("_edge", "_background", "_page"))
        self.rect(x, y, w, h, body, edge, 2.2, 8)
        self.parts[-1] = self.parts[-1].replace(
            "<rect ", f'<rect data-memory-tier="{tier}" ', 1)
        self.rect(x + 1, y + 1, w - 2, 37, page, "none", 0, 7)
        self.rect(x + 1, y + 20, w - 2, 18, page, "none", 0, 0)
        self.line(x + 1, y + 38, x + w - 1, y + 38, edge, 1.2)
        self.text(x + 16, y + 28, title, 27, INK, 700)
        self.memory_tiers.append({
            "tier": tier, "position": [x, y], "size": [w, h],
            "header": page, "body": body, "border": edge, "role": "peer_storage_tier",
        })

    def storage_page(self, x, y, label="", *, tier, owner=BACKPLATE, missing=False,
                     w=54, h=47, size=21):
        prefix = tier.lower()
        self.rect(x, y, w, h, PAPER if missing else C[prefix + "_page"],
                  C[prefix + "_edge"], 1.6, 2, "6 4" if missing else None)
        self.parts[-1] = self.parts[-1].replace(
            "<rect ", f'<rect data-page-tier="{tier}" data-page-owner="{owner}" '
            f'data-page-missing="{str(missing).lower()}" ', 1)
        self.rect(x + 2, y + 2, w - 4, min(5, h * .15), owner, "none", 0, 1)
        if label:
            self.label(x + w / 2, y + h / 2 + size * .4, label, size, True)
        self.storage_pages.append({
            "tier": tier, "owner": owner, "missing": missing, "position": [x, y],
        })

    def transfer_curve(self, start, control1, control2, end, *, width=17):
        super().transfer_curve(start, control1, control2, end, width=width)
        node = ET.fromstring(self.parts[-1])
        node.set("fill", C["transfer_fill"])
        node.set("stroke", C["transfer_edge"])
        self.parts[-1] = ET.tostring(node, encoding="unicode")

    def transfer_band(self, x1, y, x2, *, h=25, label=None, allocation=False):
        direction = 1 if x2 > x1 else -1
        shoulder = x2 - direction * min(19, abs(x2 - x1) * .35)
        self.polygon((
            (x1, y - h * .34), (shoulder, y - h * .34),
            (shoulder, y - h * .65), (x2, y),
            (shoulder, y + h * .65), (shoulder, y + h * .34),
            (x1, y + h * .34),
        ), BACKPLATE if allocation else C["transfer_fill"],
            GRAY if allocation else C["transfer_edge"], 1.6)
        self.flow_types["capacity_allocation" if allocation else "transfer_band"] += 1
        self.flow_geometry["wide_block"] += 1
        if label:
            self.label((x1 + x2) / 2 - 4, y + 7, label, min(23, h * .75))

    def transfer_engine(self, x, y, w, h):
        self.polygon(((x + 10, y), (x + w - 10, y), (x + w, y + 10),
                      (x + w, y + h - 10), (x + w - 10, y + h),
                      (x + 10, y + h), (x, y + h - 10), (x, y + 10)),
                     BACKPLATE, INK, 1.6)
        self.label(x + w / 2, y + h / 2 + 8, "DMA", 25, True)
        for xx in (x - 6, x + w - 6):
            for offset in (h * .3, h * .5, h * .7):
                self.line(xx, y + offset, xx + 12, y + offset, FLOW, 1.7)

    def label(self, x, y, value, size=23, bold=False):
        self.centered(x, y, value, size=size, bold=bold)

    def agent(self, x, y, name, *, radius=24):
        self.token(x, y, name, OWNERS[name], r=radius, size=23)

    def pages(self, x, y, labels, tier, owner, *, released=False, w=45, h=43, gap=12):
        for index, label in enumerate(labels):
            self.storage_page(x + index * (w + gap), y, label, tier=tier,
                              owner=OWNERS[owner], missing=released, w=w, h=h,
                              size=22)

    def requests(self, x, y, *, w=128, h=43, status=False):
        for index, name in enumerate(("C1", "C2")):
            yy = y + index * (h + 15)
            self.rect(x, yy, w, h, PAPER, INK, 1.8, 3)
            self.rect(x + 1, yy + 1, 9, h - 2, OWNERS[name], "none", 0, 0)
            self.label(x + 36, yy + h / 2 + 8, name, 23, True)
            if status:
                self.text(x + 72, yy + h / 2 + 7,
                          "missing" if name == "C1" else "hot", 18)
            for column in range(2):
                self.storage_page(x + w - 52 + 23 * column, yy + (h - 22) / 2,
                                  tier="HBM", owner=OWNERS[name], missing=name == "C1",
                                  w=18, h=22)

    def choice(self, x, y, second, *, selected=False):
        self.rect(x, y, 235, 78, PAPER, ACTION if selected else GRAY, 2, 7)
        self.agent(x + 57, y + 39, "C1")
        self.agent(x + 175, y + 39, second)
        if selected:
            self.check(x + 213, y + 8, color=ACTION, size=20)

    def distribution(self, x, y, name, probabilities, *, width=264, height=85,
                     labels=True, values=True):
        self.text(x - 34, y + height / 2, name, 25, INK, 700)
        column = width / 3
        for index, (label, value) in enumerate(zip(("S", "M", "L"), probabilities)):
            xx = x + index * column
            if labels:
                self.label(xx + column / 2, y - 8, label, 21, True)
            self.rect(xx + 12, y, column - 24, height, GRAY_PALE, "none", 0, 2)
            self.rect(xx + 12, y + height * (1 - value), column - 24,
                      height * value, BLUE_PALE, "none", 0, 2)
            if values:
                self.label(xx + column / 2, y + height + 27, f"{value:.1f}", 21)

    def tool_survival(self, x, y, *, width=200, height=78):
        self.line(x, y, x, y + height, INK, 1.5)
        self.line(x, y + height, x + width, y + height, INK, 1.5)
        self.curve_arrow((x + 6, y + 6), (x + width * .55, y + 7),
                         (x + width * .76, y + height * .36),
                         (x + width, y + height * .88), color=ACTION, width=4)
        self.line(x + width * .36, y + 6, x + width * .36, y + height + 6,
                  GRAY, 1.5, "4 4")
        self.label(x + width * .36, y + height + 27, "cost", 18)
        self.text(x + width - 8, y + height + 27, "time", 18)

    def gpu(self, x, y, *, w=140, h=106, labels=False, actors=("C1", "C2")):
        for xx in range(round(x + 16), round(x + w - 7), 21):
            self.line(xx, y - 9, xx, y, INK, 1.7)
            self.line(xx, y + h, xx, y + h + 9, INK, 1.7)
        for yy in range(round(y + 16), round(y + h - 7), 21):
            self.line(x - 9, yy, x, yy, INK, 1.7)
            self.line(x + w, yy, x + w + 9, yy, INK, 1.7)
        self.rect(x, y, w, h, GRAY_PALE, INK, 2, 3)
        self.label(x + w / 2, y + 30, "GPU", 25, True)
        if labels:
            for index, name in enumerate(actors):
                self.rect(x + 14, y + 44 + index * 29, w - 28, 24,
                          OWNERS[name], INK, 1.1, 2)
                self.label(x + w / 2, y + 63 + index * 29, name, 20, True)
        else:
            for index in range(4):
                row, column = divmod(index, 2)
                self.rect(x + 12 + column * (w - 17) / 2,
                          y + 46 + row * 25, (w - 31) / 2, 18,
                          OWNERS[("C1", "C2")[index]] if index < 2 else PAPER,
                          INK, 1.1, 2)

    def returns_to_parent(self, x, *, gpu_bottom, join_y, parent_y):
        self.arrow(((x, gpu_bottom + 12), (x, join_y - 35)))
        self.text(x - 112, (gpu_bottom + join_y) / 2 + 5, "RETURNs", 20)
        self.diamond(x, join_y, 82, 53, PAPER, INK)
        self.label(x, join_y + 7, "ALL", 20, True)
        self.arrow(((x, join_y + 33), (x, parent_y - 32)))
        self.agent(x, parent_y, "P", radius=26)
        self.label(x, parent_y + 49, "ready", 20)


OVERVIEW_NOTES = (
    "Current main-tree design, 2026-11-07. One shared physical HBM pool and one "
    "shared Host pool appear exactly once. D1 observes true workflow dependencies "
    "and optional R5 remaining-model-work probabilities, then chooses agent "
    "eligibility under resource constraints. C1/C2 are the chosen agents; the same "
    "C1 identity is used by D2 and native execution. D2 binds their real demand to "
    "a safe donor T that is actually blocked on a sufficiently long tool wait. "
    "Its page plan identifies the safe checkpoint, missing FULL and growth reserve. "
    "Incremental D2H backs up only stable missing FULL. Settled Host ACK and current "
    "native locks/ownership authorize demotion; enqueue is not free capacity. "
    "HiCache executes the plan and native data/slot checks retain final authority. "
    "The tool residual-survival model plus demand/cost check feeds incremental "
    "PREPARE_HOST and ACK-gated physical release. Observed delegation/JOIN waits "
    "can also back P's stable FULL, without granting eviction at SPAWN. The "
    "last child's final-EOS hint drives missing-parent JOIN H2D, distinct from "
    "ordinary/tool prefetch. The HBM and Host P columns represent that parent, "
    "not a C1 restore target. C2 is already resident; it does not need to wait for an artificial whole-set "
    "barrier. RETURNs, not the forecast or selected set, satisfy the observed ALL-JOIN "
    "and make P ready. R5/v7 remains an opt-in candidate with no established stable "
    "incremental JCT gain. Existing active agents are not displaced by D1. The "
    "FULL-only path omits Mamba, ordinary/tool speculative prefetch, physical layer "
    "handoff and mixed chunk. The drawing explains orchestration, not simultaneous "
    "measured residency, a performance result or a guarantee of immediate service."
)

D1_NOTES = (
    "D1 exposes the complete implemented short-workflow bias rather than hiding it "
    "behind arbitrary weights. Task/delegation metadata and attained input/output/"
    "completed calls and observed children feed the asynchronous remaining-model-"
    "work predictor. The illustrative A distribution is (.7,.2,.1), and B is "
    "(.1,.2,.7), in short/mid/long order. The actual probability_weight function "
    "gives qA=.825 and qB=.375, using (1,.5,.25). These are invented explanatory "
    "probabilities, not measurements or a wall-clock JCT prediction. Agents within "
    "a workflow share its weight; they are not independently assigned the complete "
    "workflow's work. With two free slots, equal KV footprints and zero existing "
    "active coverage, {C1,B} scores .825*(1/2)^2+.375=.58125; {C1,C2} scores .825. "
    "The displayed .58/.83 are rounded. Fractions are selected members divided by "
    "unfinished observed group members. The general code uses the change in "
    "squared coverage relative to existing active members and caps each workflow "
    "once; it does not guarantee that siblings always win or that this proxy "
    "minimizes JCT. Actual input/growth, slots, logical and physical KV constraints "
    "remain required. Existing active work and protected JOIN rights are retained; "
    "unknown predictions fall back to the reference. D2 supplies native KV/service "
    "for the selected identities. Actual RETURNs, not the score or prediction, "
    "satisfy ALL and permit P to resume; P may delegate again. Its future final "
    "completion is not assumed. Progress then updates the same prediction loop. "
    "The v7 benefit objective and learned model are explicitly configured paths; "
    "no isolated, stable additional JCT gain is claimed by this illustration."
)

D2_NOTES = (
    "A and B share one HBM pool and one Host pool. B's root is already running. "
    "After A spawns and enters WAIT_JOIN, a1 and a2 start together and the system "
    "starts PREPARE_HOST for A's stable private FULL pages. A retains its HBM "
    "pages at the backup ACK. B spawns later. The allocation shortfall lets the "
    "native allocator reclaim A's backed and unlocked pages, which provide "
    "enough capacity including growth for b1 and b2. B's resident prefix is "
    "still shared by live requests. The third child, b3, still lacks capacity. "
    "The runtime conditions the offline tool-wait prior on a1's elapsed wait "
    "and compares its survival probability with the migration-cost threshold. "
    "It selects the eligible a1 checkpoint to back up for this ready consumer. "
    "The D2H ACK and a fresh safety check permit a1's HBM release and b3's "
    "admission. b3 starts its first request from available prefix pages and "
    "new input. The illustrated FULL-only case assumes enough Host space, "
    "input and growth headroom in each exchange, and ready request slots. "
    "a1's tool completes during b3's service. a1's next request waits for "
    "capacity, then issues demand H2D when b3's one-shot private pages can be "
    "discarded. a1 resumes after that H2D. a2 returns and retires, making room "
    "for A's missing pages and its next request. a1 is now the last blocker. "
    "Its final EOS triggers A's prefetch before RETURN satisfies ALL-JOIN. "
    "A resumes after JOIN, H2D ACK and native readiness while b1 and b2 "
    "continue. The hatched cells denote free pages and reservations count "
    "against their destination pools. Band heights and durations are schematic. "
    "Dashed execution bars indicate waiting. Parents use solid dark fills. "
    "Within each workflow the children share a fill color and use large dots, "
    "long diagonal marks, or wide horizontal dashes. The same colors and "
    "textures fill the occupied cells in HBM and Host DRAM and the radix leaves. "
    "The DMA arrows span short schematic intervals from copy start to ACK. "
    "The dark part of A's H2D is the interval before JOIN. "
    "Marker a connects A's private leaf to its HBM pages. The offload inset "
    "shows how the runtime decides to initiate transfer 3. "
    "Matching prefixes can share pages within a compatible namespace. "
    "Session ID, generation, and native reference and lock checks restrict "
    "release to eligible private pages."
)


def overview():
    f = CompactFigure("01_system_overview", "BeliefKV architecture", 740, OVERVIEW_NOTES)
    f.heading("BeliefKV: Workflow-aware Execution and KV Management")
    f.panel(28, 82, 870, 630, "Control plane",
            color=CONTROL_EDGE, fill=CONTROL_PALE, border=CONTROL_EDGE,
            title_color=PAPER, size=27, role="control_plane")
    f.panel(922, 82, 730, 630, "SGLang data plane",
            color=DATA_EDGE, fill=DATA_PALE, border=DATA_EDGE,
            title_color=PAPER, size=27, role="data_plane")

    f.panel(48, 134, 830, 238, "D1  Short-workflow + JOIN selection",
            color=BLUE_PALE, fill=PAPER, border=CONTROL_EDGE, size=26)
    f.agent(96, 225, "C1", radius=21)
    f.agent(96, 290, "C2", radius=21)
    f.diamond(217, 258, 71, 51, PAPER, INK)
    f.label(217, 265, "ALL", 19, True)
    f.agent(313, 258, "P", radius=22)
    f.arrow(((122, 225), (153, 225), (180, 241)))
    f.arrow(((122, 290), (153, 290), (180, 275)))
    f.arrow(((257, 258), (285, 258)))
    f.text(71, 197, "Workflow A", 20, INK, 700)
    f.text(401, 197, "Remaining work", 20, INK, 700)
    f.distribution(430, 232, "A", (.7, .2, .1), width=144, height=40, values=False)
    f.distribution(430, 309, "B", (.1, .2, .7), width=144, height=40,
                   labels=False, values=False)
    f.diamond(653, 270, 90, 56, PAPER, CONTROL_EDGE)
    f.math(653, 281, [("S", None, "*")], size=33, anchor="middle")
    f.arrow(((582, 270), (604, 270)), color=BLUE, dashed=True)
    f.arrow(((339, 258), (367, 258), (367, 353), (653, 353), (653, 306)),
            color=ACTION)
    f.arrow(((704, 270), (731, 270)), color=ACTION)
    f.requests(743, 234, w=116, h=40)

    f.panel(48, 392, 830, 296, "D2  Predict wait → prepare / offload → prefetch",
            color=AMBER_PALE, fill=PAPER, border=CONTROL_EDGE, size=26)
    f.agent(92, 506, "T", radius=22)
    f.clock(122, 532, 12)
    f.label(95, 566, "tool wait", 20)
    f.arrow(((122, 506), (171, 506)), color=ACTION)
    f.text(180, 468, "Tool residual", 21, INK, 700)
    f.tool_survival(182, 482, width=140, height=55)
    f.arrow(((334, 511), (393, 511)), color=ACTION)
    f.diamond(450, 511, 100, 64, PAPER, CONTROL_EDGE)
    f.label(450, 518, "offload?", 18, True)
    f.text(391, 468, "cost + demand", 18)
    f.arrow(((801, 343), (801, 442), (450, 442), (450, 477)), color=ACTION)
    f.step(801, 442, 1, "D1 supplies real consumer demand to the tool offload cost decision")
    f.arrow(((507, 511), (901, 511), (901, 396), (943, 396)), color=ACTION)
    f.text(590, 495, "PREPARE_HOST", 22, INK, 700)
    f.text(569, 541, "ACK → safe offload", 21)
    f.agent(92, 627, "P", radius=22)
    f.label(103, 674, "JOIN wait", 20)
    f.arrow(((122, 627), (156, 627), (156, 583), (802, 583), (802, 519)),
            color=ACTION)
    f.label(453, 610, "SPAWN / WAIT_JOIN: backup", 19)
    f.text(287, 651, "last-child EOS", 21, INK, 700)
    f.arrow(((464, 643), (916, 643), (916, 458), (998, 458), (998, 425)),
            color=ACTION)
    f.text(593, 630, "JOIN prefetch", 22, INK, 700)
    f.step(916, 558, 2, "Final-child evidence requests safe missing-parent H2D")

    for tier, yy in (("HBM", 142), ("Host", 545)):
        f.memory_pool(948, yy, 410, 150, tier, tier)
        labels = (
            (973, "T", ("t0", "t1")), (1103, "P", ("p0", "p1")),
            (1233, "C1", ("c0", "c1")),
        )
        for xx, owner, names in labels:
            if tier == "Host" and owner == "C1":
                for index in range(2):
                    f.page(xx + index * 50, yy + 61, fill=PAPER, stroke=GRAY, w=42, h=42)
                f.label(xx + 46, yy + 137, "free", 20)
            else:
                f.pages(xx, yy + 61, names, tier, owner,
                        w=42, h=42, gap=8)
                f.label(xx + 46, yy + (59 if tier == "HBM" else 137), owner, 20)
    f.transfer_engine(949, 370, 100, 52)
    f.label(998, 355, "HiCache", 21, True)
    f.transfer_curve((1044, 251), (1100, 325), (1100, 511), (1044, 600), width=15)
    f.transfer_curve((1144, 600), (1201, 510), (1201, 329), (1144, 251), width=15)
    f.operation_label(1115, 533, "D2H", size=21)
    f.operation_label(1270, 440, "H2D", size=21)
    f.arrow(((1366, 218), (1440, 218)))
    f.step(1402, 218, 3, "Native KV and slot dependencies govern actual GPU service")
    f.gpu(1450, 166, w=140, h=106)
    f.label(1401, 281, "admit", 20)
    f.returns_to_parent(1520, gpu_bottom=272, join_y=450, parent_y=625)
    return f


def d1_working_set():
    f = CompactFigure(
        "02_dependency_working_set", "D1: dependency-aware working-set admission",
        650, D1_NOTES,
    )
    f.heading("D1  Short-workflow Bias with Observed JOIN Dependencies")
    for x, w, label in ((28, 342, "Task + service progress"),
                        (410, 380, "Remaining model work"),
                        (825, 475, "Choose a feasible set"),
                        (1350, 302, "KV-ready execution")):
        f.rect(x, 74, w, 34, BLUE_PALE, "none", 0, 6)
        f.text(x + 12, 99, label, 23, INK, 700)

    f.text(40, 151, "Workflow A", 23, INK, 700)
    f.agent(76, 204, "C1")
    f.agent(76, 291, "C2")
    f.diamond(191, 248, 76, 54, PAPER, INK)
    f.label(191, 256, "ALL", 20, True)
    f.agent(307, 248, "P")
    f.arrow(((105, 204), (130, 204), (150, 229)))
    f.arrow(((105, 291), (130, 291), (150, 265)))
    f.arrow(((233, 248), (277, 248)))
    f.label(307, 297, "wait", 20)
    f.agent(76, 409, "B")
    f.label(88, 473, "Workflow B", 18, True)
    f.text(154, 376, "Served work", 22, INK, 700)
    for index, (name, height) in enumerate((("tokens", 24), ("calls", 43), ("children", 63))):
        xx = 164 + 60 * index
        f.rect(xx, 455 - height, 28, height, BACKPLATE, INK, 1, 2)
        f.label(xx + 14, 486, name, 15)

    f.arrow(((341, 248), (385, 248), (385, 232), (417, 232)), color=ACTION)
    f.arrow(((340, 420), (385, 420), (385, 395), (417, 395)), color=ACTION)
    f.line(385, 253, 385, 395, ACTION, 4)
    f.step(385, 327, 1, "Update conditional model-work distributions from task and attained service")
    f.distribution(475, 175, "A", (.7, .2, .1))
    f.distribution(475, 352, "B", (.1, .2, .7))
    f.label(603, 140, "short / mid / long", 20)
    f.math(603, 310, [("q", "A"), " = .825"], size=27, anchor="middle")
    f.math(603, 487, [("q", "B"), " = .375"], size=27, anchor="middle")
    f.math(603, 553, [
        ("q",), " = ", ("p", "S"), " + ½", ("p", "M"), " + ¼", ("p", "L"),
    ], size=27, anchor="middle")
    f.arrow(((771, 303), (811, 303)), color=ACTION)

    f.text(839, 151, "2 free slots / equal KV", 21)
    f.label(1098, 190, "A", 21, True)
    f.label(1180, 190, "B", 21, True)
    f.label(1253, 190, "gain", 21, True)
    for yy, second, score in ((209, "B", ".58"), (353, "C2", ".83")):
        selected = second == "C2"
        f.rect(835, yy, 465, 104, DATA_PALE if selected else GRAY_PALE,
               ACTION if selected else GRAY, 2, 7)
        f.agent(882, yy + 51, "C1")
        f.agent(966, yy + 51, second)
        f.label(1098, yy + 61, "2/2" if selected else "1/2", 25)
        f.label(1180, yy + 61, "0/1" if selected else "1/1", 25)
        f.math(1253, yy + 62, [score], size=30, anchor="middle")
        if selected:
            f.check(1275, yy + 7, color=ACTION, size=18)
    f.label(1118, 493, "selected / unfinished", 20)
    f.math(1076, 551, ["Σ ", ("q", "w"), " × ", ("coverage", None, "2")],
           size=27, anchor="middle")
    f.arrow(((1307, 405), (1330, 405), (1330, 270), (1367, 270)), color=ACTION)
    f.step(1330, 335, 2, "Prefer the supported short-workflow/JOIN gain within capacity")
    f.requests(1375, 206, w=250, h=48, status=True)
    f.text(1415, 175, "C1 + C2", 25, INK, 700)
    f.arrow(((1632, 270), (1640, 270), (1640, 363), (1460, 363),
             (1460, 407)), color=ACTION)
    f.label(1547, 351, "D2: KV + service", 20, True)
    f.diamond(1460, 441, 76, 54, PAPER, INK)
    f.label(1460, 449, "ALL", 20, True)
    f.text(1338, 401, "RETURNs", 20)
    f.arrow(((1503, 441), (1540, 441)))
    f.agent(1573, 441, "P")
    f.label(1573, 494, "resume A", 21, True)
    f.arrow(((1605, 441), (1640, 441), (1640, 596), (175, 596), (175, 521)),
            color=BLUE, dashed=True)
    f.label(1038, 625, "refresh after actual service / RETURN", 21)
    return f


def d2_detail_marker(f, x, y, letter, color, *, target):
    f.circle(x, y, 14, PAPER, color, 2.6)
    f.parts[-1] = f.parts[-1].replace(
        "<circle ", f'<circle data-d2-detail="{letter}" data-target="{target}" ', 1,
    )
    f.label(x, y + 7, letter, 22, True)


def d2_texture(f, x, y, width, height, owner, target, *, label=None, strong=False,
               column_spacing=1):
    pattern = D2_TEXTURES.get(owner)
    if pattern is None:
        return
    u, v = D2_MARK_AXES[pattern]
    column_step, row_step, margin = 16, 11, 8
    if strong:
        if pattern == "diagonal":
            u, v = (.33 * height, .36 * height), (-2.3, 1.9)
            column_step, row_step = 30, height
        elif pattern == "dots":
            u, v = (3.5, 0), (0, 3.5)
            column_step, row_step = 23, 16
        else:
            u, v = (8, 0), (0, 2.8)
            column_step, row_step = 25, 16
        margin = math.ceil(math.hypot(u[0], v[0])) + 4
    column_step = round(column_spacing * column_step)
    if label:
        text, center, size, weight = label
        quiet_half = f.measure(text, size, weight, f.family)[0] / 2 + max(7, margin - 1)
    axes = (u, v, (-u[0], -u[1]), (-v[0], -v[1]))
    kappa = .5522847498
    rows = max(1, int((height - 11) // row_step) + 1)
    path = []
    for row in range(rows):
        cy = y + (height - row_step * (rows - 1)) / 2 + row_step * row
        start = math.ceil(x + margin + column_step // 2 * (row % 2))
        stop = math.floor(x + width - margin + 1)
        for cx in range(start, stop, column_step):
            if label and abs(cx - center) < quiet_half:
                continue
            path.append(f"M{cx + u[0]:.2f},{cy + u[1]:.2f}")
            for (ax, ay), (bx, by) in zip(axes, (*axes[1:], axes[0])):
                path.append(
                    f"C{cx + ax + kappa * bx:.2f},{cy + ay + kappa * by:.2f} "
                    f"{cx + bx + kappa * ax:.2f},{cy + by + kappa * ay:.2f} "
                    f"{cx + bx:.2f},{cy + by:.2f}"
                )
            path.append("Z")
    if path:
        # One compound shape per texture keeps SVG and PowerPoint editable.
        f.parts.append(
            f'<path d="{" ".join(path)}" fill="{D2_OWNERS[owner][1]}" stroke="none" '
            f'data-d2-texture="{pattern}" data-d2-texture-for="{target}" '
            f'data-d2-texture-owner="{owner}"/>'
        )


def d2_control_arrow(f, points, *, dashed=False):
    color = C["d2_trigger"]
    f.path(points, color, 3.6, "7 5" if dashed else None, "d2-control-14")
    f.parts[-1] = f.parts[-1].replace(
        "<path ", '<path data-arrow-kind="open" data-arrow-size="14" ', 1,
    )
    f.flow_types["hint" if dashed else "action"] += 1


def d2_tool_offload_inset(f, x, top, width):
    control_color = C["d2_trigger"]
    mid = x + width / 2
    f.panel(x, top, width, 442, "Tool-aware offload", color=C["d2_a_idle"], fill=PAPER,
            size=25, band=56, border=control_color, role="tool_offload")
    f.circle(x + width - 24, top + 28, 12, INK, PAPER, 1.8)
    f.parts[-1] = f.parts[-1].replace("<circle ", '<circle data-d2-detail-step="3" ', 1)
    f.text(x + width - 24, top + 35, "3", 19, PAPER, 700, anchor="middle")
    f.label(mid, top + 93, "Tool history + elapsed wait", D2_LABEL_SIZE)
    d2_control_arrow(f, ((mid, top + 101), (mid, top + 119)))

    # Condition on elapsed wait and evaluate survival at the combined move cost.
    axis_left, axis_right = x + 42, x + width - 20
    f.text(axis_left, top + 143, "Residual survival S(t)", D2_LABEL_SIZE, INK, 700)
    f.path(((axis_left, top + 157), (axis_left, top + 249),
            (axis_right, top + 249)), INK, 2.4)
    f.line(axis_left, top + 216, axis_right, top + 216, GRAY, 2.2, "5 5")
    f.line(mid, top + 184, mid, top + 249, GRAY, 2.2, "5 5")
    f.text(axis_left - 9, top + 223, "\u03c4", D2_LABEL_SIZE, INK, 700,
           anchor="end", italic=True)
    points = ((42, 160), (77, 160), (77, 171), (117, 171), (117, 184),
              (174, 184), (174, 198), (227, 198), (227, 227), (280, 227))
    f.path(tuple((x + dx, top + dy) for dx, dy in points), control_color, 4)
    f.circle(mid, top + 184, 6.5, control_color, PAPER, 1.6)
    f.label(mid, top + 275, "Move cost", D2_LABEL_SIZE)

    f.label(x + 83, top + 319, "S(cost) \u2265 \u03c4", D2_LABEL_SIZE, True)
    f.label(x + width - 77, top + 319, "b3 KV deficit", D2_LABEL_SIZE, True)
    f.path(((x + 83, top + 330), (x + 132, top + 354)), control_color, 2.8)
    f.path(((x + width - 77, top + 330), (x + width - 132, top + 354)),
           control_color, 2.8)
    f.diamond(mid, top + 366, 86, 44, PAPER, control_color)
    f.label(mid, top + 373, "AND", D2_LABEL_SIZE, True)
    d2_control_arrow(f, ((mid, top + 389), (mid, top + 411)))
    f.label(mid, top + 433, "a1 D2H", D2_LABEL_SIZE, True)


def d2_radix_inset(f, x, top, width):
    border = C["d2_hbm_edge"]
    f.panel(x, top, width, 307, "RadixAttention KV", color=C["d2_hbm_body"], fill=PAPER,
            size=25, band=56, border=border, role="radix_kv")
    root_x = x + width / 2
    leaf_x = (x + 56, root_x, x + width - 56)
    f.label(root_x, top + 104, "Shared prefix", D2_LABEL_SIZE, True)
    f.rect(root_x - 66, top + 118, 132, 30, BACKPLATE, border, 2.2, 4)
    for dx in (-33, 0, 33):
        f.line(root_x + dx, top + 118, root_x + dx, top + 148, border, 1.6)
    f.line(root_x, top + 148, root_x, top + 183, border, 2.6)
    f.path(((leaf_x[0], top + 183), (leaf_x[0], top + 163),
            (leaf_x[2], top + 163), (leaf_x[2], top + 183)), border, 2.6)
    for center, owner in zip(leaf_x, ("A", "a1", "a2")):
        fill, border, _ = D2_OWNERS[owner]
        f.rect(center - 41, top + 183, 82, 42, fill, border, 2.2, 5)
        f.parts[-1] = f.parts[-1].replace("<rect ", f'<rect data-d2-leaf="{owner}" ', 1)
        if owner == "A":
            f.parts[-1] = f.parts[-1].replace("<rect ", '<rect data-d2-span="radix_leaf" ', 1)
            d2_detail_marker(f, center - 19, top + 204, "a", border, target="radix_leaf")
            label_x = center + 11
        else:
            label_x = center - 18
            d2_texture(f, center - 9, top + 185, 49, 38, owner, f"leaf-{owner}", strong=True)
        f.text(label_x, top + 212, owner, D2_LABEL_SIZE, PAPER if owner == "A" else INK, 700,
               anchor="middle")
    d2_control_arrow(f, ((leaf_x[0], top + 262), (leaf_x[0], top + 227)), dashed=True)
    f.text(x + 18, top + 289, "Session ID + gen.", D2_LABEL_SIZE)
    f.path(((leaf_x[0] + 30, top + 225), (x + 106, top + 247),
            (x + 121, top + 247)), C["d2_hbm_edge"], 2.2)
    f.text(x + 128, top + 254, "1 FULL ref", D2_LABEL_SIZE)


def d2_kv_turnover():
    f = CompactFigure(
        "03_execution_kv_turnover", "D2: KV turnover across workflows",
        1096, D2_NOTES, width=1380,
    )
    f.family = "Liberation Sans"
    f.parts.insert(f.parts.index("</defs>"), f.flow_marker("workflow", INK, "triangle", 18))
    control_color = C["d2_trigger"]
    f.parts.insert(f.parts.index("</defs>"), f.flow_marker("d2-control", control_color, "open", 14))
    left, label_x, origin, right = 24, 36, 184, 1032
    side_x, side_width = 1056, 300
    a_spawn, a_children, prepare_ack = 253, 290, 330
    tool_start, b_spawn, b_children = 396, 433, 480
    offload_start, offload_ack, tool_done = 536, 576, 608
    b3_return, b3_release, a1_restore_ack = 648, 681, 721
    a2_return, a2_release = 765, 787
    final_eos, join_copy, a_join, join_ack, a_resume = 818, 849, 875, 890, 929
    a1_release = 915
    a_top, a_bottom, b_top, b_bottom = 87, 307, 322, 553
    a_y, a1_y, a2_y = 129, 202, 266
    b_y, b1_y, b2_y, b3_y = 358, 416, 467, 518
    bar_half, label_size = 19, D2_LABEL_SIZE
    hbm_top, hbm_base_end, hbm_a_end, hbm_a1_end, hbm_bottom = 578, 625, 672, 719, 766
    dma_top, dma_y, dma_bottom = 780, 859, 900
    host_top, host_a_end, host_a1_end, host_bottom = 914, 957, 1000, 1043
    copy_edge, copy_gold = C["d2_dma_edge"], C["d2_copy_fill"]

    def edge(points, *, relation=None, arrow=True):
        f.path(points, INK, 3.8, arrow="workflow-18" if arrow else None)
        attributes = 'data-arrow-kind="triangle" data-arrow-size="18"'
        if relation:
            event, source, target = relation
            attributes += (
                f' data-workflow-event="{event}" data-from="{source}" data-to="{target}"'
            )
        f.parts[-1] = f.parts[-1].replace(
            "<path ", f"<path {attributes} ", 1)
        f.flow_types["workflow"] += 1

    def bar(start, end, center, label, owner, role, *, waiting=False, label_center=None,
            continue_right=False):
        fill, border, idle = D2_OWNERS[owner]
        top = center - bar_half
        height = 2 * bar_half
        f.rect(start, top, end - start, height, idle if waiting else fill,
               border, 2.4, 6, "8 5" if waiting else None)
        state = "wait" if waiting else "run"
        f.parts[-1] = f.parts[-1].replace(
            "<rect ", f'<rect data-d2-span="{role}" data-d2-agent="{owner}" '
            f'data-d2-agent-state="{state}" ', 1,
        )
        x = (start + end) / 2 if label_center is None else label_center
        if not waiting:
            d2_texture(f, start, top, end - start, height, owner, role,
                       label=(label, x, label_size, 700) if label else None, strong=True)
        text_color = PAPER if not waiting and owner in ("A", "B") else INK
        if label:
            f.text(x, center + 7, label, label_size, text_color, 700, anchor="middle")
        if continue_right:
            # The chevron extends the service strip beyond the plotted interval.
            f.rect(end - 30, center - 11, 25, 22, fill, "none", 0, 0)
            f.path(((end - 25, center - 7), (end - 16, center), (end - 25, center + 7)),
                   border, 3)

    def lane_glyph(name, kind, y, color):
        x = label_x
        first = len(f.parts)
        if kind == "workflow":
            f.path(((x + 3, y), (x + 12, y), (x + 12, y - 8), (x + 21, y - 8)),
                   color, 2.8)
            f.path(((x + 12, y), (x + 12, y + 8), (x + 21, y + 8)), color, 2.8)
            for dx, dy in ((3, 0), (21, -8), (21, 8)):
                f.circle(x + dx, y + dy, 4.4, color, PAPER, 1)
        elif kind == "stack":
            for dy in (-10, -3, 4):
                f.rect(x, y + dy, 26, 5, "none", color, 2.2, 1)
        elif kind == "dimm":
            f.rect(x, y - 8, 26, 13, "none", color, 2.2, 2)
            for dx in (4, 10, 16):
                f.rect(x + dx, y - 4, 4, 5, color, "none", 0, 0)
            for dx in (5, 12, 19):
                f.line(x + dx, y + 5, x + dx, y + 9, color, 2.2)
        elif kind == "copy_engine":
            f.rect(x, y - 11, 26, 22, "none", color, 2.2, 3)
            for dy, x1, x2, direction in ((-4, 5, 19, 1), (4, 19, 5, -1)):
                f.line(x + x1, y + dy, x + x2, y + dy, color, 2.2)
                head = x + x2 - 4 * direction
                f.path(((head, y + dy - 3), (x + x2, y + dy),
                        (head, y + dy + 3)), color, 2.2)
        f.parts[first] = f.parts[first].replace(
            " ", f' data-d2-glyph="{kind}" data-lane="{name}" ', 1,
        )

    def lane(name, top, bottom, *, tier=False):
        prefix, kind = D2_LANES[name]
        border = C[f"{prefix}_edge"]
        width, height = right - left, bottom - top
        workflow = kind == "workflow"
        fill = PAPER if workflow else C[f"{prefix}_body"]
        f.rect(left, top, width, height, fill, "none" if workflow else border,
               0 if workflow else 3.3, 0)
        style = "open" if workflow else "hardware"
        tags = f'data-d2-lane="{name}" data-lane-style="{style}"'
        if tier:
            tags += f' data-memory-tier="{name}"'
            f.memory_tiers.append({
                "tier": name, "position": [left, top], "size": [width, height],
                "header": border, "body": fill, "border": border, "role": "pool_occupancy",
            })
        f.parts[-1] = f.parts[-1].replace("<rect ", f"<rect {tags} ", 1)
        center = (top + bottom) / 2
        if workflow:
            f.path(((left + 8, top + 12), (left, top + 12), (left, bottom - 12),
                    (left + 8, bottom - 12)), border, 3.8)
            f.line(origin, bottom, right, bottom, GRAY, 1.6)
        else:
            f.rect(left, top, origin - left, height, border, "none", 0, 0)
            f.parts[-1] = f.parts[-1].replace("<rect ", f'<rect data-d2-header="{name}" ', 1)
            f.line(origin, top, origin, bottom, border, 2.7)
            f.parts[-1] = f.parts[-1].replace("<line ", f'<line data-d2-divider="{name}" ', 1)
        lane_glyph(name, kind, center, border if workflow else PAPER)
        f.text(label_x + 30, center + 7, name, label_size, INK if workflow else PAPER, 700)
        f.parts[-1] = f.parts[-1].replace("<text ", f'<text data-d2-label="{name}" ', 1)

    def hatch(start, end, top, bottom, color=GRAY):
        # Draw clipped strokes so SVG and the editable slide use the same geometry.
        height = bottom - top
        for base in range(start - height, end, 20):
            x1, x2 = max(base, start + 2), min(base + height, end - 2)
            if x1 < x2:
                f.line(x1, bottom - (x1 - base), x2, bottom - (x2 - base), color, 1.8)

    def reservation(start, end, top, bottom, color):
        f.rect(start + 4, top + 4, end - start - 8, bottom - top - 8,
               "none", color, 2.3, 2, "6 4")

    def occupancy(tier, start, end, top, bottom, label, role, *,
                  owner=None, state="resident", label_center=None):
        prefix = D2_LANES[tier][0]
        grid = C[f"{prefix}_grid"]
        fill = C[f"{prefix}_body"]
        if state == "free":
            fill = PAPER
        if owner:
            owner_fill, owner_edge, idle = D2_OWNERS[owner]
            if state != "reserved":
                fill = owner_fill
            elif tier == "HBM":
                fill = idle
        f.rect(start, top, end - start, bottom - top, fill, grid, 1.55, 0)
        f.parts[-1] = f.parts[-1].replace(
            "<rect ", f'<rect data-d2-pool="{tier}" data-d2-span="{role}" '
            f'data-kv-state="{state}" data-kv-owner="{owner or ""}" ', 1,
        )
        center = (start + end) / 2 if label_center is None else label_center
        label_y = (top + bottom) / 2
        if state == "free":
            hatch(start, end, top, bottom, grid)
        elif state == "reserved":
            reservation(start, end, top, bottom, owner_edge)
        elif owner:
            d2_texture(f, start, top, end - start, bottom - top, owner, role,
                       label=(label, center, label_size, 700) if label else None, strong=True)
        if label:
            text_color = PAPER if owner == "A" and state in ("resident", "backed") else INK
            f.text(center, label_y + 7, label, label_size, text_color, 700, anchor="middle")

    def pool_outline(top, bottom, stroke):
        f.rect(left, top, right - left, bottom - top, "none", stroke, 3.3, 0)
        f.line(origin, top, origin, bottom, stroke, 2.7)

    def event(x, y, name, baseline, role, *, color=INK, side_label=False):
        f.circle(x, y, 7.5, color, PAPER, 2)
        f.parts[-1] = f.parts[-1].replace("<circle ", f'<circle data-d2-event="{role}" ', 1)
        if name:
            f.text(x + 14 if side_label else x, baseline, name, label_size, INK, 700,
                   anchor="start" if side_label else "middle")

    def step(x, y, number, meaning):
        f.circle(x, y, 11, INK, PAPER, 1.8)
        f.parts[-1] = f.parts[-1].replace("<circle ", f'<circle data-execution-step="{number}" ', 1)
        f.text(x, y + 6, str(number), 18, PAPER, 700, anchor="middle")
        f.execution_steps.append({"number": number, "position": [x, y], "meaning": meaning})

    def endpoint_link(x, start_y, end_y, owner, direction, endpoint):
        f.line(x, start_y, x, end_y, copy_edge, 2.7)
        f.parts[-1] = f.parts[-1].replace(
            "<line ", f'<line data-copy-link="{endpoint}" data-owner="{owner}" '
            f'data-direction="{direction}" ', 1,
        )

    def copy_arrow(start, end, y, half_height, *, overlaps_join=False):
        head = min(half_height + 5, (end - start) * .35)
        shoulder = end - head
        f.polygon(((start, y - half_height), (shoulder, y - half_height),
                   (shoulder, y - half_height - 5), (end, y),
                   (shoulder, y + half_height + 5),
                   (shoulder, y + half_height), (start, y + half_height)),
                  C["d2_copy_tail"] if overlaps_join else copy_gold, copy_edge, 2.6)
        f.flow_types["transfer_band"] += 1
        f.flow_geometry["wide_block"] += 1

    def copy(direction, owner, start, ack, title, number, *, source_y, destination_y, overlap=None):
        endpoint_link(start, source_y, dma_y, owner, direction, "start")
        endpoint_link(ack, dma_y, destination_y, owner, direction, "ack")
        copy_arrow(start, ack, dma_y, 14, overlaps_join=overlap is not None)
        f.parts[-1] = f.parts[-1].replace(
            "<polygon ", f'<polygon data-copy-direction="{direction}" data-owner="{owner}" '
            f'data-start-x="{start}" data-ack-x="{ack}" ', 1,
        )
        if overlap is not None:
            f.rect(start + 1, dma_y - 13, overlap - start - 1, 26, copy_gold, "none", 0, 0)
            f.parts[-1] = f.parts[-1].replace("<rect ", '<rect data-d2-span="pre_join" ', 1)
            f.line(overlap, dma_y - 20, overlap, dma_y + 20, copy_edge, 2.3)
        for x, fill, endpoint in ((start, PAPER, "start"), (ack, copy_edge, "ack")):
            f.circle(x, dma_y, 4.5, fill, copy_edge, 2)
            f.parts[-1] = f.parts[-1].replace(
                "<circle ", f'<circle data-copy-endpoint="{endpoint}" '
                f'data-owner="{owner}" data-direction="{direction}" ', 1,
            )
        center = (start + ack) / 2
        step(center, dma_y, number, title)
        title_width = f.measure(title, label_size, 700)[0]
        f.rect(center - title_width / 2 - 6, dma_y - 57, title_width + 12, 27,
               C["d2_dma_body"], "none", 0, 0)
        f.label(center, dma_y - 37, title, label_size, True)

    f.text(left, 54, "D2  KV Turnover", 39, INK, 700)
    edge(((356, 43), (393, 43)))
    f.text(404, 50, "Dependency", label_size)
    d2_control_arrow(f, ((533, 43), (568, 43)), dashed=True)
    f.text(578, 50, "Trigger", label_size)
    copy_arrow(658, 695, 43, 7)
    f.text(705, 50, "KV copy", label_size)
    f.rect(797, 31, 26, 24, PAPER, GRAY, 1.5, 0)
    hatch(797, 823, 31, 55)
    f.text(833, 50, "Free", label_size)
    f.rect(890, 31, 26, 24, C["d2_a_idle"], GRAY, 1.5, 0)
    reservation(890, 916, 31, 55, control_color)
    f.text(926, 50, "Reserved", label_size)

    lane("Workflow A", a_top, a_bottom)
    lane("Workflow B", b_top, b_bottom)
    lane("HBM", hbm_top, hbm_bottom, tier=True)
    lane("DMA", dma_top, dma_bottom)
    lane("Host DRAM", host_top, host_bottom, tier=True)
    f.text(left, 1077, "Schematic time", label_size, INK, italic=True)
    f.path(((origin, 1070), (right, 1070)), GRAY, 2.6, arrow="inactive-12")
    f.parts[-1] = f.parts[-1].replace(
        "<path ", '<path data-d2-time-axis="true" data-arrow-kind="open" data-arrow-size="12" ', 1,
    )

    # Draw timing guides behind the execution bars of the other workflow.
    f.line(a_children, a2_y + bar_half, a_children, hbm_top, control_color, 2, "6 5")
    f.line(join_copy, a1_y + 42, join_copy, hbm_top, control_color, 2, "6 5")
    f.line(a_join, a2_y, a_join, hbm_top, GRAY, 1.8, "5 5")
    for x, y in (
        (offload_ack, b3_y + bar_half), (b3_release, b_bottom), (a2_release, a_bottom),
    ):
        f.line(x, y, x, hbm_top, GRAY, 1.8, "5 5")

    def spawn_fork(x, request_start, parent_y, parent, children):
        branch = x + 15
        edge(((x, parent_y), (request_start, parent_y)), arrow=False)
        edge(((x, parent_y), (branch, parent_y + 13), (branch, children[-1][1])),
             arrow=False)
        for child, y in children:
            edge(((branch, y), (request_start, y)), relation=("SPAWN", parent, child))

    spawn_fork(a_spawn, a_children, a_y, "A", (("a1", a1_y), ("a2", a2_y)))
    spawn_fork(b_spawn, b_children, b_y, "B", (("b1", b1_y), ("b2", b2_y), ("b3", b3_y)))
    edge(((a2_return, a2_y), (a_join, a2_y), (a_join, a1_y)),
         relation=("RETURN", "a2", "A_JOIN"), arrow=False)
    edge(((a_join, a1_y), (a_join, a_y + 15)),
         relation=("RETURN", "a1", "A_JOIN"))
    edge(((a_join + 16, a_y), (a_resume, a_y)))

    bar(origin, a_spawn, a_y, "A", "A", "a_initial")
    bar(a_children, a_join - 16, a_y, "Join wait", "A", "a_wait", waiting=True)
    bar(a_resume, right - 1, a_y, "A", "A", "a_resume")
    event(a_spawn, a_y, "SPAWN", a_y - 25, "a_spawn")
    f.diamond(a_join, a_y, 32, 30, PAPER, INK)
    f.parts[-1] = f.parts[-1].replace("<polygon ", '<polygon data-d2-event="a_join" ', 1)
    f.label(a_join, a_y - 25, "JOIN", label_size, True)

    bar(a_children, tool_start, a1_y, "a1", "a1", "a1_first")
    bar(tool_start, tool_done, a1_y, "Tool wait", "a1", "a1_tool_wait", waiting=True)
    bar(tool_done, a1_restore_ack, a1_y, None, "a1", "a1_kv_wait", waiting=True)
    bar(a1_restore_ack, final_eos, a1_y, "a1", "a1", "a1_resume")
    bar(a_children, a2_return, a2_y, "a2", "a2", "a2_service")
    f.line(final_eos, a1_y, a_join, a1_y, INK, 2.6, "5 5")
    event(tool_start, a1_y, None, None, "tool_start")
    event(tool_done, a1_y, None, None, "tool_done", color=control_color)
    event(final_eos, a1_y, "Final EOS", a1_y - 24, "final_eos", color=control_color)
    event(a_join, a1_y, "RETURN", a1_y + 8, "a1_return", side_label=True)
    event(a2_return, a2_y, None, None, "a2_return")
    d2_control_arrow(f, ((final_eos, a1_y + 9), (final_eos, a1_y + 29),
                         (join_copy, a1_y + 29), (join_copy, a1_y + 42)), dashed=True)
    f.parts[-1] = f.parts[-1].replace(
        "<path ", '<path data-d2-trigger="final_eos" ', 1,
    )

    bar(origin, b_spawn, b_y, "B", "B", "b_initial", label_center=origin + 39)
    bar(b_children, right - 1, b_y, None, "B", "b_wait", waiting=True)
    event(b_spawn, b_y, None, None, "b_spawn")
    for owner, y in (("b1", b1_y), ("b2", b2_y)):
        bar(b_children, right - 1, y, owner, owner, f"{owner}_service",
            label_center=b_children + 32, continue_right=True)
    bar(b_children, offload_ack, b3_y, "KV wait", "b3", "b3_wait", waiting=True)
    bar(offload_ack, b3_return, b3_y, "b3", "b3", "b3_service")
    event(b3_return, b3_y, None, None, "b3_return")
    d2_control_arrow(f, ((b_children, b3_y + bar_half + 2), (b_children, hbm_top)), dashed=True)
    step(b_children, hbm_top - 20, 2, "Reclaim backed A for b1 and b2")
    f.text(b_children - 20, hbm_top - 13, "Reclaim A", label_size, INK, 700, anchor="end")

    # Each horizontal strip is part of the same allocator, with all space accounted for.
    occupancy("HBM", origin, right, hbm_top, hbm_base_end, "B prefix + other KV", "hbm_other")
    occupancy("HBM", origin, b_children, hbm_base_end, hbm_a_end, "A", "a_initial_kv", owner="A")
    occupancy("HBM", b_children, right, hbm_base_end, hbm_a_end, "b1 + b2",
              "b12_kv", owner="b1+b2")
    for start, end, label, role, owner, state in (
        (origin, a_children, None, "before_a1_free", None, "free"),
        (a_children, offload_ack, "a1", "a1_initial_kv", "a1", "resident"),
        (offload_ack, b3_release, "b3", "b3_kv", "b3", "resident"),
        (b3_release, a1_restore_ack, None, "a1_loading", "a1", "reserved"),
        (a1_restore_ack, a1_release, "a1", "a1_restored_kv", "a1", "resident"),
        (a1_release, right, None, "after_a1_free", None, "free"),
    ):
        occupancy("HBM", start, end, hbm_a_end, hbm_a1_end, label, role,
                  owner=owner, state=state)
    for start, end, label, role, owner, state in (
        (origin, a_children, None, "before_a2_free", None, "free"),
        (a_children, a2_release, "a2", "a2_kv", "a2", "resident"),
        (a2_release, join_copy, None, "after_a2_free", None, "free"),
        (join_copy, join_ack, None, "a_loading", "A", "reserved"),
        (join_ack, right, "A", "a_restored_kv", "A", "resident"),
    ):
        occupancy("HBM", start, end, hbm_a1_end, hbm_bottom, label, role, owner=owner, state=state,
                  label_center=(a_children + offload_start) / 2 if owner == "a2" else None)
    pool_outline(hbm_top, hbm_bottom, C["d2_hbm_edge"])
    d2_detail_marker(f, origin + 27, (hbm_base_end + hbm_a_end) / 2, "a",
                     D2_OWNERS["A"][1], target="a_initial_kv")

    for top, bottom, start, ack, owner in (
        (host_top, host_a_end, a_children, prepare_ack, "A"),
        (host_a_end, host_a1_end, offload_start, offload_ack, "a1"),
    ):
        key = owner.lower()
        occupancy("Host DRAM", origin, start, top, bottom, None, f"{key}_host_free",
                  state="free")
        occupancy("Host DRAM", start, ack, top, bottom, None,
                  f"{key}_host_loading", owner=owner, state="reserved")
        occupancy("Host DRAM", ack, right, top, bottom, owner,
                  f"{key}_host_copy", owner=owner, state="backed",
                  label_center=(ack + offload_ack) / 2 if owner == "A" else None)
    occupancy("Host DRAM", origin, right, host_a1_end, host_bottom, "Other KV",
              "host_other")
    pool_outline(host_top, host_bottom, C["d2_host_edge"])

    f.line(b_children, hbm_top, b_children, hbm_a_end, C["d2_hbm_edge"], 2.5)
    f.line(offload_start, b3_y + bar_half, offload_start, hbm_a1_end, control_color, 1.8, "5 5")
    f.line(a_join, hbm_bottom, a_join, dma_y - 20, GRAY, 1.8, "5 5")
    f.line(a_join, dma_y + 20, a_join, host_top, GRAY, 1.8, "5 5")
    copy("D2H", "A", a_children, prepare_ack, "PREPARE_HOST", 1,
         source_y=hbm_a_end, destination_y=host_top)
    copy("D2H", "a1", offload_start, offload_ack, "Tool offload", 3,
         source_y=hbm_a1_end, destination_y=host_a_end)
    copy("H2D", "a1", b3_release, a1_restore_ack, "Demand H2D", 4,
         source_y=host_a_end, destination_y=hbm_a1_end)
    copy("H2D", "A", join_copy, join_ack, "JOIN prefetch", 5,
         source_y=host_top, destination_y=hbm_bottom, overlap=a_join)

    d2_radix_inset(f, side_x, 163, side_width)
    d2_tool_offload_inset(f, side_x, 509, side_width)
    return f


def notes_export(exporter, figure, directory):
    exporter.add_figure(figure, directory)
    exporter.presentation.slides[-1].notes_slide.notes_text_frame.text = figure.notes
    exporter.presentation.core_properties.subject = (
        "D2 KV turnover revision " + D2_REVISION_DATE
        if figure.filename.startswith("03_") else
        "Overview and dedicated D1/D2 figures as of " + DATE
    )


def export(figures, directory):
    height = max(COLLECTION_HEIGHT, *(math.ceil(figure.height * WIDTH / figure.width)
                                      for figure in figures))
    collection = PowerPointExporter("en", width=WIDTH, height=height)
    decks = []
    for figure in figures:
        notes_export(collection, figure, directory)
        single = PowerPointExporter("en", width=figure.width, height=figure.height)
        notes_export(single, figure, directory)
        name = figure.filename.replace(".svg", "_editable.pptx")
        single.save(directory / name, expected_slides=1)
        decks.append(name)
    name = "beliefkv_two_designs_en.pptx"
    collection.save(directory / name, expected_slides=len(figures))
    decks.append(name)
    subprocess.run([
        "pdfunite", *(str(directory / f.filename.replace(".svg", ".pdf")) for f in figures),
        str(directory / "beliefkv_two_designs_en.pdf"),
    ], check=True)
    return decks


def preview(directory, figures, *, office=None):
    images = []
    if office is not None:
        with tempfile.TemporaryDirectory(prefix="beliefkv-connected-preview-") as folder:
            prefix = Path(folder) / "page"
            subprocess.run([
                "pdftoppm", "-png", "-scale-to", "1800",
                str(office / "beliefkv_two_designs_en.pdf"), str(prefix),
            ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            pages = sorted(Path(folder).glob("page-*.png"))
            assert len(pages) == len(figures)
            for page, row in zip(pages, figures):
                with Image.open(page) as source:
                    image = source.convert("RGB")
                content_height = round(image.width * row["canvas"][1] / row["canvas"][0])
                top = (image.height - content_height) // 2
                images.append(image.crop((0, top, image.width, top + content_height)))
    else:
        for row in figures:
            with Image.open(directory / (row["stem"] + ".png")) as source:
                images.append(source.convert("RGB"))
    images = [
        image.resize((1440, round(image.height * 1440 / image.width)),
                     Image.Resampling.LANCZOS)
        for image in images
    ]
    sheet = Image.new("RGB", (1488, 24 + sum(image.height + 24 for image in images)), "white")
    top = 24
    for image in images:
        sheet.paste(image, (24, top))
        top += image.height + 24
    sheet.save(directory / ("pptx_preview_en.png" if office else "preview_en.png"))


def validate_d2_timeline(root):
    def marked(attribute):
        return {node.get(attribute): node for node in root if node.get(attribute)}

    def extent(node, origin, length):
        start = float(node.get(origin))
        return start, start + float(node.get(length))

    spans = marked("data-d2-span")
    events = marked("data-d2-event")
    lanes = marked("data-d2-lane")
    gutter = marked("data-d2-divider")
    labels = marked("data-d2-label")
    hardware = {name for name, (_, glyph) in D2_LANES.items() if glyph != "workflow"}
    assert list(lanes) == ["Workflow A", "Workflow B", "HBM", "DMA", "Host DRAM"]
    assert len({extent(node, "x", "width") for node in lanes.values()}) == 1
    assert labels.keys() == lanes.keys() and gutter.keys() == hardware
    assert len({label.get("x") for label in labels.values()}) == 1
    assert len({float(node.get(key)) for node in gutter.values() for key in ("x1", "x2")}) == 1
    glyphs = {node.get("data-lane"): node for node in root if node.get("data-d2-glyph")}
    headers = marked("data-d2-header")
    assert glyphs.keys() == lanes.keys() and headers.keys() == hardware
    for name, (prefix, glyph) in D2_LANES.items():
        border = C[f"{prefix}_edge"]
        lane = lanes[name]
        if name in hardware:
            assert lane.get("data-lane-style") == "hardware"
            assert lane.get("stroke") == headers[name].get("fill") == border
            assert lane.get("fill") == C[f"{prefix}_body"]
            assert labels[name].get("fill") == glyphs[name].get("stroke") == PAPER
        else:
            assert lane.get("data-lane-style") == "open"
            assert lane.get("stroke") == "none" and lane.get("fill") == PAPER
            assert labels[name].get("fill") == INK and glyphs[name].get("stroke") == border
        assert glyphs[name].get("data-d2-glyph") == glyph
    assert len({header.get("fill") for header in headers.values()}) == len(headers)

    panels = marked("data-container-role")
    assert panels.keys() == {"radix_kv", "tool_offload"}
    radix, tool = (panels[name] for name in ("radix_kv", "tool_offload"))
    _, main_right = extent(lanes["HBM"], "x", "width")
    side_left, side_right = extent(radix, "x", "width")
    assert extent(tool, "x", "width") == (side_left, side_right)
    assert main_right < side_left < side_right < float(root.get("viewBox").split()[2])
    assert extent(radix, "y", "height")[1] < float(tool.get("y"))
    assert radix.get("stroke") == C["d2_hbm_edge"]
    assert tool.get("stroke") == C["d2_trigger"]

    paired_patterns = {}

    def expect_pattern(key, node, owner):
        if owner in D2_TEXTURES:
            paired_patterns[key] = (node, owner)

    assert D2_OWNERS["a1"] == D2_OWNERS["a2"]
    assert D2_OWNERS["b1"] == D2_OWNERS["b2"] == D2_OWNERS["b3"]
    for key, bar in spans.items():
        owner = bar.get("data-d2-agent")
        if owner:
            fill, edge, idle = D2_OWNERS[owner]
            waiting = bar.get("data-d2-agent-state") == "wait"
            assert bar.get("stroke") == edge
            assert bar.get("fill") == (idle if waiting else fill)
            assert bool(bar.get("stroke-dasharray")) == waiting
            if not waiting:
                expect_pattern(key, bar, owner)

    leaves = marked("data-d2-leaf")
    assert leaves.keys() == {"A", "a1", "a2"}
    for owner, leaf in leaves.items():
        fill, edge, _ = D2_OWNERS[owner]
        assert (leaf.get("fill"), leaf.get("stroke")) == (fill, edge)
        expect_pattern(f"leaf-{owner}", leaf, owner)

    axis = marked("data-d2-time-axis")["true"]
    axis_commands = vector_commands(axis.get("d"))
    origin_x, _ = axis_commands[0][1][0]
    end_x, _ = axis_commands[-1][1][-1]
    assert origin_x == float(spans["a_initial"].get("x"))
    assert origin_x == float(spans["a_initial_kv"].get("x"))

    for tier in ("HBM", "Host DRAM"):
        bands = {}
        for key, cell in spans.items():
            if cell.get("data-d2-pool") == tier:
                owner, state = cell.get("data-kv-owner"), cell.get("data-kv-state")
                if tier == "HBM" and owner:
                    fill, _, idle = D2_OWNERS[owner]
                    assert cell.get("fill") == (idle if state == "reserved" else fill)
                    if state == "resident":
                        expect_pattern(key, cell, owner)
                if tier == "Host DRAM" and state == "backed":
                    assert cell.get("fill") == D2_OWNERS[owner][0]
                    expect_pattern(key, cell, owner)
                band = extent(cell, "y", "height")
                bands.setdefault(band, []).append(extent(cell, "x", "width"))
        tier_top, tier_bottom = extent(lanes[tier], "y", "height")
        for (top, bottom), cells in sorted(bands.items()):
            assert top == tier_top < bottom
            tier_top = bottom
            cursor = origin_x
            for start, end in sorted(cells):
                assert start == cursor < end
                cursor = end
            assert cursor == end_x
        assert tier_top == tier_bottom

    patterns = marked("data-d2-texture-for")
    assert patterns.keys() == paired_patterns.keys()
    for key, (cell, owner) in paired_patterns.items():
        texture = patterns[key]
        assert texture.get("data-d2-texture-owner") == owner
        assert texture.get("data-d2-texture") == D2_TEXTURES[owner]
        assert texture.get("fill") == D2_OWNERS[owner][1]
        x0, x1 = extent(cell, "x", "width")
        y0, y1 = extent(cell, "y", "height")
        commands = vector_commands(texture.get("d"))
        assert any(command == "C" for command, _ in commands)
        assert all(x0 < x < x1 and y0 < y < y1 for _, points in commands for x, y in points)

    assert [(node.get("data-workflow-event"), node.get("data-from"), node.get("data-to"))
            for node in root if node.get("data-workflow-event")] == [
        ("SPAWN", "A", "a1"), ("SPAWN", "A", "a2"),
        ("SPAWN", "B", "b1"), ("SPAWN", "B", "b2"), ("SPAWN", "B", "b3"),
        ("RETURN", "a2", "A_JOIN"), ("RETURN", "a1", "A_JOIN"),
    ]
    copies = {(node.get("data-copy-direction"), node.get("data-owner")): node
              for node in root if node.get("data-copy-direction")}
    routes = {
        ("D2H", "A"): ("a_initial_kv", "a_host_copy"),
        ("D2H", "a1"): ("a1_initial_kv", "a1_host_copy"),
        ("H2D", "a1"): ("a1_host_copy", "a1_restored_kv"),
        ("H2D", "A"): ("a_host_copy", "a_restored_kv"),
    }
    assert copies.keys() == routes.keys()
    dma_top, dma_bottom = extent(lanes["DMA"], "y", "height")
    dma_centers, intervals = set(), {}
    for (direction, owner), band in copies.items():
        start, ack = (float(band.get(key)) for key in ("data-start-x", "data-ack-x"))
        intervals[direction, owner] = start, ack
        points = [tuple(map(float, point.split(","))) for point in band.get("points").split()]
        xs, ys = zip(*points)
        assert origin_x <= start == min(xs) < ack == max(xs)
        assert ack - start <= .05 * (end_x - origin_x)
        assert dma_top < min(ys) < max(ys) < dma_bottom
        center_y = (min(ys) + max(ys)) / 2
        dma_centers.add(center_y)
        assert (ack, center_y) in points
        source, destination = routes[direction, owner]
        source_top, source_bottom = extent(spans[source], "y", "height")
        destination_top, destination_bottom = extent(spans[destination], "y", "height")
        source_y = source_bottom if direction == "D2H" else source_top
        destination_y = destination_top if direction == "D2H" else destination_bottom
        for endpoint, x, expected_ys in (
            ("start", start, (source_y, center_y)), ("ack", ack, (center_y, destination_y)),
        ):
            point, = [node for node in root if node.get("data-copy-endpoint") == endpoint
                      and node.get("data-owner") == owner and node.get("data-direction") == direction]
            link, = [node for node in root if node.get("data-copy-link") == endpoint
                     and node.get("data-owner") == owner and node.get("data-direction") == direction]
            assert (float(point.get("cx")), float(point.get("cy"))) == (x, center_y)
            assert float(link.get("x1")) == float(link.get("x2")) == x
            assert tuple(float(link.get(key)) for key in ("y1", "y2")) == expected_ys
    assert len(dma_centers) == 1
    intervals_in_time = sorted(intervals.values())
    assert all(prev[1] < curr[0] for prev, curr in zip(intervals_in_time, intervals_in_time[1:]))

    def x_span(name):
        return extent(spans[name], "x", "width")

    def event_x(name):
        return float(events[name].get("cx"))

    prepare_start, prepare_ack = intervals["D2H", "A"]
    offload_start, offload_ack = intervals["D2H", "a1"]
    restore_start, restore_ack = intervals["H2D", "a1"]
    join_start, join_ack = intervals["H2D", "A"]
    a_start, tool_start = x_span("a1_first")
    a2_start, a2_return = x_span("a2_service")
    a_release, b_end = x_span("b12_kv")
    b1 = x_span("b1_service")
    b2 = x_span("b2_service")
    b3_start, b3_return = x_span("b3_service")
    tool_done = event_x("tool_done")

    assert event_x("a_spawn") < a_start == a2_start == prepare_start == x_span("a_wait")[0]
    assert prepare_start < prepare_ack < tool_start == event_x("tool_start") < event_x("b_spawn")
    assert event_x("b_spawn") < a_release < offload_start < offload_ack
    assert b1 == b2 == (a_release, b_end - 1)
    assert x_span("b3_wait") == (a_release, offload_ack)
    assert x_span("a_initial_kv") == (origin_x, a_release)
    assert x_span("a_host_loading") == (prepare_start, prepare_ack)
    assert x_span("a_host_copy") == (prepare_ack, end_x)

    assert x_span("a1_tool_wait") == (tool_start, tool_done)
    assert b3_start == offload_ack < tool_done < b3_return == event_x("b3_return") < restore_start
    assert x_span("a1_kv_wait") == (tool_done, restore_ack)
    assert x_span("a1_initial_kv") == (a_start, offload_ack)
    assert x_span("a1_host_loading") == (offload_start, offload_ack)
    assert x_span("a1_host_copy") == (offload_ack, end_x)
    assert x_span("b3_kv") == (offload_ack, restore_start)
    assert x_span("a1_loading") == (restore_start, restore_ack)
    assert x_span("a1_restored_kv")[0] == restore_ack
    assert x_span("a1_resume") == (restore_ack, event_x("final_eos"))

    a2_release = x_span("a2_kv")[1]
    join_points = events["a_join"].get("points").split()
    join_x = sum(float(point.split(",")[0]) for point in join_points) / len(join_points)
    eos = event_x("final_eos")
    assert a2_return == event_x("a2_return") < a2_release < eos < join_start
    assert join_start < event_x("a1_return") == join_x < join_ack < x_span("a_resume")[0] < b1[1]
    assert x_span("a2_kv")[0] == a_start
    assert x_span("after_a2_free") == (a2_release, join_start)
    assert x_span("a_loading") == (join_start, join_ack)
    assert x_span("a_restored_kv") == (join_ack, end_x)
    assert x_span("pre_join") == (join_start + 1, join_x)
    assert event_x("a1_return") < x_span("a1_restored_kv")[1]
    trigger = marked("data-d2-trigger")["final_eos"]
    trigger_points = [part[-1] for _, part in vector_commands(trigger.get("d"))]
    assert trigger_points[0][0] == eos and trigger_points[-1][0] == join_start
    assert trigger.get("stroke-dasharray")

    # The reclaimed capacity must cover the illustrated destinations.
    def hbm_height(name):
        assert spans[name].get("data-d2-pool") == "HBM"
        return float(spans[name].get("height"))

    assert hbm_height("b12_kv") <= hbm_height("a_initial_kv")
    assert hbm_height("a1_loading") <= hbm_height("b3_kv") <= hbm_height("a1_initial_kv")
    assert hbm_height("a_initial_kv") <= hbm_height("a_loading") <= hbm_height("a2_kv")
    steps = marked("data-execution-step")
    step_positions = [float(steps[str(i)].get("cx")) for i in range(1, 6)]
    assert step_positions == sorted(step_positions) and step_positions[1] == a_release
    assert offload_start < step_positions[2] < offload_ack
    tool_detail = marked("data-d2-detail-step")["3"]
    assert side_left < float(tool_detail.get("cx")) < side_right
    tool_top, tool_bottom = extent(tool, "y", "height")
    assert tool_top < float(tool_detail.get("cy")) < tool_bottom

    details = {node.get("data-target"): node for node in root if node.get("data-d2-detail")}
    assert {target: node.get("data-d2-detail") for target, node in details.items()} == {
        "a_initial_kv": "a", "radix_leaf": "a",
    }
    for target, detail in details.items():
        x1, x2 = x_span(target)
        y1, y2 = extent(spans[target], "y", "height")
        assert x1 < float(detail.get("cx")) < x2
        assert y1 < float(detail.get("cy")) < y2


def validate(directory, office, reviewed):
    manifest = json.loads((directory / "manifest.json").read_text())
    allowed = set(C.values())
    assert manifest["generator_sha256"] == digest(__file__)
    assert manifest["drawing_helper_sha256"] == digest(
        Path(__file__).with_name("generate_monthly_paper_figures.py"))
    assert manifest["palette_sha256"] == digest(PALETTE_PATH)
    for name, recipe in PALETTE["derived_backgrounds"].items():
        base = recipe["base"].lstrip("#")
        fraction = recipe["white_fraction"]
        mixed = "#" + "".join(
            f"{round(int(base[index:index + 2], 16) * (1 - fraction) + 255 * fraction):02x}"
            for index in (0, 2, 4))
        assert mixed == C[name], (name, mixed, C[name])
    for source, expected in manifest["architecture_source_sha256"].items():
        assert digest(ROOT / source) == expected, "Runtime changed: " + source
    forbidden = {
        "outerShdw", "innerShdw", "prstShdw", "glow", "reflection",
        "softEdge", "scene3d", "sp3d",
    }
    for row in manifest["figures"]:
        root = ET.parse(directory / (row["stem"] + ".svg")).getroot()
        colors = {value.lower() for node in root.iter() for key in ("fill", "stroke")
                  if (value := node.get(key, "")).startswith("#")}
        assert colors <= allowed
        tiers = [node.get("data-memory-tier") for node in root if node.get("data-memory-tier")]
        expected_tiers = (
            [] if row["stem"].startswith("02_") else
            ["HBM", "Host DRAM"] if row["stem"].startswith("03_") else ["HBM", "Host"]
        )
        assert tiers == expected_tiers, "Storage tiers must be absent in D1 and unique in overview/D2"
        text = {"".join(node.itertext()) for node in root if node.tag.endswith("}text")}
        required = (
            {"Remaining model work", "short / mid / long", "2 free slots / equal KV",
             "selected / unfinished", "D2: KV + service", "RETURNs"}
            if row["stem"].startswith("02_") else
            {"Workflow A", "Workflow B", "SPAWN", "Join wait", "Tool wait", "KV wait",
             "Reclaim A", "PREPARE_HOST", "Tool offload", "Demand H2D", "JOIN prefetch",
             "Final EOS", "RETURN", "JOIN", "RadixAttention KV", "Shared prefix",
             "Session ID + gen.", "1 FULL ref", "Tool-aware offload",
             "Tool history + elapsed wait", "Residual survival S(t)", "Move cost",
             "b3 KV deficit", "a1 D2H"}
            if row["stem"].startswith("03_") else
            {"PREPARE_HOST", "ACK → safe offload", "JOIN prefetch", "Tool residual"}
        )
        assert required <= text, "A required causal path is missing from " + row["stem"]
        if row["stem"].startswith("03_"):
            validate_d2_timeline(root)
            assert colors.isdisjoint({C["hbm_edge"], C["hbm_page"], C["hbm_background"]})
    decks = []
    for name in manifest["powerpoints"]:
        deck = Presentation(directory / name)
        rows = manifest["figures"] if name.startswith("beliefkv_") else [
            row for row in manifest["figures"] if name == row["stem"] + "_editable.pptx"
        ]
        assert len(deck.slides) == len(rows)
        count = 0
        for slide, row in zip(deck.slides, rows):
            root = ET.parse(directory / (row["stem"] + ".svg")).getroot()
            expected = ["".join(node.itertext()) for node in root if node.tag.endswith("}text")]
            assert [shape.text for shape in slide.shapes if shape.has_text_frame and shape.text] == expected
            for shape in slide.shapes:
                assert shape.shape_type != MSO_SHAPE_TYPE.PICTURE
                assert 0 <= shape.left <= shape.left + shape.width <= deck.slide_width
                assert 0 <= shape.top <= shape.top + shape.height <= deck.slide_height
                assert all("#" + node.get("val").lower() in allowed
                           for node in shape._element.xpath(".//a:srgbClr"))
            assert len(slide.notes_slide.notes_text_frame.text) > 500
            count += len(slide.shapes)
        with ZipFile(directory / name) as archive:
            for item in archive.namelist():
                if item.endswith(".xml"):
                    root = ET.fromstring(archive.read(item))
                    assert not any(node.tag.rsplit("}", 1)[-1] in forbidden for node in root.iter())
        pdf = office / name.replace(".pptx", ".pdf")
        info = subprocess.check_output(["pdfinfo", str(pdf)], text=True)
        assert "Pages:" in info
        decks.append({"file": name, "sha256": digest(directory / name),
                      "native_editable_objects": count, "picture_objects": 0,
                      "office_render_sha256": digest(pdf)})
    preview(directory, manifest["figures"], office=office)
    receipt = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "language": "en", "design_points": 2, "figures": 3,
        "dedicated_d1_and_d2": True,
        "tool_prediction_prepare_offload_join_prefetch_paths_present": True,
        "short_workflow_probability_to_selection_path_present": True,
        "single_hbm_host_pair_in_overview_and_d2": True,
        "d2_full_pool_coverage_and_cross_workflow_reuse_verified": True,
        "d2_prepare_tool_offload_retire_and_join_order_verified": True,
        "d2_transfer_intervals_and_detail_targets_verified": True,
        "d2_lane_glyphs_and_owner_colors_verified": True,
        "d2_same_color_sibling_textures_and_hardware_chassis_verified": True,
        "d2_full_host_copy_fills_and_short_transfer_bands_verified": True,
        "d2_sidebar_placement_and_offload_link_verified": True,
        "native_editable_ppt_verified": True, "documented_palette_colors_only": True,
        "derived_backgrounds_reproduced": True,
        "no_shadows_or_3d": True, "runtime_sources_unchanged": True,
        "visual_review_complete": reviewed, "powerpoints": decks,
    }
    (directory / "validation.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--office-pdf-directory", type=Path)
    parser.add_argument("--visual-review-complete", action="store_true")
    args = parser.parse_args()
    if args.validate_only:
        if args.office_pdf_directory is None:
            parser.error("--validate-only requires --office-pdf-directory")
        validate(args.output, args.office_pdf_directory, args.visual_review_complete)
        return
    figures = [overview(), d1_working_set(), d2_kv_turnover()]
    rows = [figure.save_to(args.output) for figure in figures]
    decks = export(figures, args.output)
    preview(args.output, rows)
    manifest = {
        "architecture_date": DATE, "language": "en", "design_points": 2,
        "d2_revision_date": D2_REVISION_DATE,
        "revision": "d2-narrow-timeline-and-sidebar",
        "source_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "source_scope": "Current main-tree bytes, including existing uncommitted changes",
        "architecture_source_sha256": {name: digest(ROOT / name) for name in SOURCES},
        "generator_sha256": digest(__file__),
        "drawing_helper_sha256": digest(Path(__file__).with_name("generate_monthly_paper_figures.py")),
        "palette_sha256": digest(PALETTE_PATH), "palette": PALETTE,
        "layout": (
            "One overview plus dedicated D1 and D2. D2 aligns two concurrent workflows "
            "with DMA and occupancy histories for one HBM pool and one Host pool. "
            "The right sidebar shows RadixAttention ownership and the tool offload decision."
        ),
        "schematic_not_empirical_performance": True,
        "d1_example": {
            "probabilities_short_mid_long": {"A": [.7, .2, .1], "B": [.1, .2, .7]},
            "weight": {"A": .825, "B": .375},
            "gain": {"C1+B": .58125, "C1+C2": .825},
            "scope": "two free slots, equal footprints, zero prior active coverage; explanatory only",
        },
        "d1_state": "R5/v7 opt-in candidate; stable additional JCT benefit unproven",
        "d2_state": (
            "A backs its private FULL while a1 and a2 start together. B spawns later "
            "and takes A's backed capacity for b1 and b2. b3 uses a1's pages after "
            "tool-aware write-back. b3 retires to make room for demand H2D to a1. "
            "a2 retires before a1's final EOS triggers A's pre-JOIN H2D. "
            "b1 and b2 continue throughout A's recovery."
        ),
        "figures": rows, "powerpoints": decks,
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"output": str(args.output),
                      "figures": [row["stem"] for row in rows], "powerpoints": decks}, indent=2))


if __name__ == "__main__":
    main()
