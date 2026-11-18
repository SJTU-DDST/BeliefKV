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

from generate_monthly_paper_figures import ROOT, PaperFigure, PowerPointExporter


OUTPUT = ROOT / "docs/figures/two_designs"
DATE = "2026-11-07"
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
    "D2 now shows both prediction-guided release and JOIN prefetch. One physical "
    "HBM and one Host appear once. Source, backed and restored pages are stages "
    "of a transition, not simultaneous allocations or exclusive page-number reuse. "
    "(1) An actually WAIT_TOOL agent T supplies tool class, elapsed wait and "
    "observed history to a conditional residual-survival forecast. The demand-cost "
    "path checks P(R_tool > C_move) >= configured survival, real waiting consumer "
    "shortfall, Host capacity and safety. C_move includes missing backup, consumer "
    "load, donor future reload, control margin and any applicable handover cost. "
    "It is a runtime decision, not a model net-benefit label. Short/unsupported "
    "waits retain normal HBM policy. (2) PREPARE_HOST asynchronously transfers "
    "only unbacked stable FULL extents. Tool donor t0 is already backed, so only "
    "t1 is shown moving. A known parent can also prepare after observed SPAWN; "
    "the separate observed WAIT_JOIN refreshes its incremental safe prefix. SPAWN "
    "alone grants no demotion authority. (3) After each owner's settled Host ACK "
    "and current lock/session/DMA checks, actual pressure/consumer demand can "
    "offload blocked private FULL through the native allocator. Backup alone "
    "releases no HBM. The shared ACK path depicts the same protocol, not a global "
    "barrier requiring every donor backup to finish. P can then be Host-only. "
    "(4) With an observed ALL-JOIN and C2 already returned, the last child C1's "
    "validated final native EOS provides an implemented early parent hint. "
    "Configured final-report progress prediction can also supply hints but is "
    "not required by this default-EOS example. Parent H2D restores only missing "
    "safe checkpoint pages when real capacity, time and admission conditions fit. "
    "The schematic intentionally places it before harness RETURN/JOIN; not all "
    "actual transfers finish before that boundary and partial overlap can help. "
    "(5) Only actual RETURN/JOIN satisfaction, a submitted parent request and "
    "native data/slot dependencies permit parent service. Bounded restoration "
    "protection and subsequent service observations manage actual reuse. The "
    "bottom axis is relative illustrative order, not measured latency, a fixed "
    "prefetch lead or a whole-transfer-before-RETURN requirement. FULL-only; no "
    "Mamba backup, ordinary/tool speculative prefetch, physical layer handoff or "
    "mixed chunk is re-enabled by this drawing."
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


def d2_kv_turnover():
    f = CompactFigure(
        "03_execution_kv_turnover", "D2: predicted tool offload and JOIN prefetch",
        930, D2_NOTES,
    )
    f.heading("D2  Prediction-guided Offload, PREPARE_HOST and JOIN Prefetch")
    for x, w, title in ((28, 720, "Tool duration → offload decision"),
                         (810, 842, "Observed JOIN → parent prefetch")):
        f.rect(x, 76, w, 34, AMBER_PALE, "none", 0, 6)
        f.text(x + 12, 101, title, 24, INK, 700)
    f.agent(74, 180, "T")
    f.clock(110, 207, 13)
    f.label(81, 244, "WAIT_TOOL", 20, True)
    f.label(89, 278, "tool + elapsed", 17)
    f.arrow(((106, 180), (141, 180)), color=ACTION)
    f.text(152, 135, "Remaining wait", 23, INK, 700)
    f.tool_survival(152, 150)
    f.arrow(((368, 180), (640, 180)), color=ACTION)
    f.step(491, 180, 1, "Conditional tool survival must cover runtime movement cost and a real consumer deficit")
    f.label(503, 151, "long + demand", 21, True)
    f.text(651, 188, "lend T", 22, INK, 700)
    f.arrow(((400, 195), (400, 236), (490, 236)), color=GRAY)
    f.text(503, 244, "retain HBM", 21)
    f.math(340, 286, [
        "P(", ("R", "tool"), " > ", ("C", "move"), ") ≥ τ",
    ], size=26, anchor="middle")

    f.agent(856, 164, "C1")
    f.agent(856, 244, "C2")
    f.text(735, 251, "RETURN", 17, INK, 700)
    f.diamond(1011, 204, 76, 54, PAPER, INK)
    f.label(1011, 212, "ALL", 20, True)
    f.agent(1140, 204, "P")
    f.arrow(((887, 164), (943, 164), (970, 185)), dashed=True)
    f.arrow(((887, 244), (943, 244), (970, 221)))
    f.arrow(((1054, 204), (1108, 204)))
    f.text(1072, 151, "WAIT_JOIN", 20, INK, 700)
    f.label(856, 141, "last child", 18)
    f.arrow(((887, 160), (1230, 160)), dashed=True)
    f.rect(1240, 144, 58, 34, PAPER, GRAY, 1.6, 3)
    f.label(1269, 168, "...", 24)
    f.rect(1308, 144, 80, 34, OWNERS["C1"], INK, 1.5, 3)
    f.label(1348, 168, "EOS", 22, True)
    f.label(1313, 132, "final output", 19)
    f.arrow(((1140, 236), (1140, 285), (760, 285), (760, 332)),
            color=ACTION)
    f.label(1274, 261, "SPAWN / WAIT_JOIN", 19)
    f.arrow(((1398, 161), (1432, 161), (1432, 309), (870, 309), (870, 332)),
            color=ACTION)

    f.memory_pool(330, 340, 980, 145, "HBM", "HBM")
    f.memory_pool(330, 610, 980, 145, "Host", "Host")
    for tier, yy in (("HBM", 408), ("Host", 671)):
        f.pages(365, yy, ("t0", "t1"), tier, "T", w=60, h=44, gap=12)
        f.pages(760, yy, ("p0", "p1"), tier, "P", w=60, h=44, gap=12)
        if tier == "HBM":
            f.pages(1100, yy, ("c0", "c1"), tier, "C1", w=60, h=44, gap=12)
        else:
            for xx in (1100, 1172):
                f.page(xx, yy, fill=PAPER, stroke=GRAY, w=60, h=44)
    for xx, label in ((431, "T"), (633, "offload / reuse"),
                       (826, "P"), (1166, "C1 running")):
        f.label(xx, 400, label, 20, True)
    f.transfer_band(527, 430, 738, h=27, allocation=True)
    f.step(633, 430, 3, "After each settled Host ACK, safely offload blocked FULL only under real capacity need")
    f.transfer_curve((467, 460), (467, 515), (467, 605), (467, 665), width=19)
    f.arrow(((675, 203), (675, 313), (285, 313), (285, 550), (445, 550)),
            color=ACTION)
    f.operation_label(150, 530, "PREPARE_HOST", size=25)
    f.label(150, 571, "D2H: new stable FULL", 20)
    f.step(467, 550, 2, "Incrementally back up only unbacked stable FULL; parent preparation uses the same operation")
    f.label(397, 738, "backed", 20)
    f.label(467, 606, "t1", 20, True)
    f.check(566, 725, color=INK, size=18)
    f.label(656, 738, "Host ACK", 21, True)
    f.arrow(((504, 695), (633, 695), (633, 453)), color=ACTION)
    f.line(752, 695, 633, 695, ACTION, 4)
    f.transfer_curve((790, 460), (735, 515), (735, 605), (790, 665), width=17)
    f.operation_label(826, 537, "D2H", size=23)
    f.label(826, 568, "ΔFULL", 20)
    f.transfer_curve((862, 665), (1020, 601), (1020, 514), (862, 460), width=20)
    f.operation_label(1140, 536, "JOIN prefetch", size=26)
    f.label(1140, 574, "H2D: missing P pages", 21)
    f.step(948, 549, 4, "On the last child's final-EOS hint, restore P before actual harness RETURN when feasible")
    f.label(1166, 738, "free", 20)
    f.arrow(((1318, 425), (1440, 425)))
    f.step(1373, 425, 5, "Real JOIN completion, parent submission, KV readiness and native capacity govern service")
    f.label(1540, 329, "JOIN + request", 19, True)
    f.label(1540, 354, "KV + slot", 19)
    f.gpu(1450, 371, w=180, h=116, labels=True, actors=("P",))

    f.text(35, 812, "JOIN overlap", 25, INK, 700)
    f.text(35, 844, "schematic time", 20)
    f.label(442, 823, "last child", 21)
    f.label(442, 879, "parent P", 21)
    for xx, label in ((990, "final EOS"), (1190, "RETURN / JOIN")):
        f.line(xx, 788, xx, 889, GRAY, 1.5, "5 4")
        f.label(xx, 779, label, 20, True)
    f.rect(560, 799, 430, 26, OWNERS["C1"], INK, 1.3, 3)
    f.label(771, 820, "final decode", 21)
    f.line(1000, 812, 1185, 812, GRAY, 2, "5 4")
    f.label(1091, 839, "protocol", 18)
    f.rect(560, 853, 420, 29, C["host_background"], C["host_edge"], 1.3, 3)
    f.label(771, 875, "P Host-only", 21)
    f.rect(1010, 853, 150, 29, C["transfer_fill"], C["transfer_edge"], 1.4, 3)
    f.label(1085, 875, "P H2D", 20, True)
    f.rect(1290, 853, 270, 29, OWNERS["P"], INK, 1.3, 3)
    f.label(1425, 875, "P service", 21, True)
    f.arrow(((550, 907), (1627, 907)), color=GRAY, width=2)
    f.label(1237, 898, "submit", 17)
    return f


def notes_export(exporter, figure, directory):
    exporter.add_figure(figure, directory)
    exporter.presentation.slides[-1].notes_slide.notes_text_frame.text = figure.notes
    exporter.presentation.core_properties.subject = "Overview and dedicated D1/D2 figures as of " + DATE


def export(figures, directory):
    collection = PowerPointExporter("en", width=WIDTH, height=COLLECTION_HEIGHT)
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
        expected_tiers = [] if row["stem"].startswith("02_") else ["HBM", "Host"]
        assert tiers == expected_tiers, "Storage tiers must be absent in D1 and unique in overview/D2"
        text = {"".join(node.itertext()) for node in root if node.tag.endswith("}text")}
        required = (
            {"Remaining model work", "short / mid / long", "2 free slots / equal KV",
             "selected / unfinished", "D2: KV + service", "RETURNs"}
            if row["stem"].startswith("02_") else
            {"WAIT_TOOL", "SPAWN / WAIT_JOIN", "PREPARE_HOST", "Host ACK",
             "JOIN prefetch", "RETURN / JOIN", "P Host-only", "P H2D", "P service"}
            if row["stem"].startswith("03_") else
            {"PREPARE_HOST", "ACK → safe offload", "JOIN prefetch", "Tool residual"}
        )
        assert required <= text, "A required causal path is missing from " + row["stem"]
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
        "revision": "explicit-tool-offload-prepare-join-prefetch-short-workflow",
        "source_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "source_scope": "Current main-tree bytes, including existing uncommitted changes",
        "architecture_source_sha256": {name: digest(ROOT / name) for name in SOURCES},
        "generator_sha256": digest(__file__),
        "drawing_helper_sha256": digest(Path(__file__).with_name("generate_monthly_paper_figures.py")),
        "palette_sha256": digest(PALETTE_PATH), "palette": PALETTE,
        "layout": "one overview plus dedicated D1 and D2; same agents/pages and one shared storage pair",
        "schematic_not_empirical_performance": True,
        "d1_example": {
            "probabilities_short_mid_long": {"A": [.7, .2, .1], "B": [.1, .2, .7]},
            "weight": {"A": .825, "B": .375},
            "gain": {"C1+B": .58125, "C1+C2": .825},
            "scope": "two free slots, equal footprints, zero prior active coverage; explanatory only",
        },
        "d1_state": "R5/v7 opt-in candidate; stable additional JCT benefit unproven",
        "d2_state": "conditional tool survival/cost -> incremental PREPARE_HOST -> ACK-gated offload; final-EOS -> missing-parent JOIN H2D; real JOIN/request/native checks -> service",
        "figures": rows, "powerpoints": decks,
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"output": str(args.output),
                      "figures": [row["stem"] for row in rows], "powerpoints": decks}, indent=2))


if __name__ == "__main__":
    main()
