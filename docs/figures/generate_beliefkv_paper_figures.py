#!/usr/bin/env python3
"""Generate editable, paper-style BeliefKV architecture figures."""

from __future__ import annotations

from html import escape
from pathlib import Path


OUT = Path(__file__).resolve().parent
INK = "#263746"
MUTED = "#687680"
RULE = "#d4dbe0"
BLUE = "#3d6f9f"
BLUE_PALE = "#edf3f8"
TEAL = "#357b73"
TEAL_PALE = "#eaf3f1"
AMBER = "#a96f1f"
AMBER_PALE = "#fbf2e3"
RED = "#a6534d"
RED_PALE = "#f8edeb"
PAPER = "#ffffff"
GRAY_PALE = "#f2f4f5"
GRAY = "#89959d"


class Figure:
    def __init__(self, filename: str, title: str, width: int, height: int):
        self.filename = filename
        self.title = title
        self.width = width
        self.height = height
        self.parts: list[str] = [
            '<rect width="100%" height="100%" fill="#ffffff"/>',
            "<defs>",
            self._marker("arrow", INK),
            self._marker("blue-arrow", BLUE),
            self._marker("teal-arrow", TEAL),
            self._marker("amber-arrow", AMBER),
            "</defs>",
            '<style>text{font-family:"DejaVu Sans",Arial,sans-serif;font-style:normal}'
            ".hair{stroke:#d4dbe0;stroke-width:1}</style>",
        ]

    @staticmethod
    def _marker(name: str, color: str) -> str:
        return (
            f'<marker id="{name}" viewBox="0 0 10 10" refX="8.5" refY="5" '
            f'markerWidth="7" markerHeight="7" orient="auto">'
            f'<path d="M0 0L10 5L0 10z" fill="{color}"/></marker>'
        )

    def text(
        self,
        x: float,
        y: float,
        value: str,
        size: float = 14,
        color: str = INK,
        weight: int = 400,
        anchor: str = "start",
        family: str | None = None,
    ) -> None:
        family_attr = f' font-family="{escape(family)}"' if family else ""
        self.parts.append(
            f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" '
            f'font-weight="{weight}" text-anchor="{anchor}"{family_attr}>'
            f"{escape(value)}</text>"
        )

    def header(self) -> None:
        self.text(56, 55, self.title, 25, INK, 700)
        self.parts.append('<line x1="56" y1="82" x2="1444" y2="82" class="hair"/>')

    def section(self, x: float, y: float, value: str) -> None:
        self.text(x, y, value.upper(), 10.5, MUTED, 700)

    def line(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        color: str = INK,
        width: float = 1.7,
        dash: str | None = None,
        arrow: str | None = None,
    ) -> None:
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        arrow_attr = f' marker-end="url(#{arrow})"' if arrow else ""
        self.parts.append(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
            f'stroke="{color}" stroke-width="{width}"{dash_attr}{arrow_attr}/>'
        )

    def path(
        self,
        points: tuple[tuple[float, float], ...] | list[tuple[float, float]],
        color: str = INK,
        width: float = 1.7,
        dash: str | None = None,
        arrow: str | None = None,
    ) -> None:
        d = " ".join(
            ("M" if i == 0 else "L") + f"{x} {y}" for i, (x, y) in enumerate(points)
        )
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        arrow_attr = f' marker-end="url(#{arrow})"' if arrow else ""
        self.parts.append(
            f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{width}"'
            f'{dash_attr}{arrow_attr}/>'
        )

    def rect(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        fill: str = PAPER,
        stroke: str = RULE,
        width: float = 1.3,
        radius: float = 2,
        dash: str | None = None,
    ) -> None:
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        self.parts.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{width}"{dash_attr}/>'
        )

    def circle(
        self,
        x: float,
        y: float,
        r: float,
        fill: str = PAPER,
        stroke: str = INK,
        width: float = 1.5,
    ) -> None:
        self.parts.append(
            f'<circle cx="{x}" cy="{y}" r="{r}" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="{width}"/>'
        )

    def diamond(
        self,
        cx: float,
        cy: float,
        w: float,
        h: float,
        fill: str = AMBER_PALE,
        stroke: str = AMBER,
    ) -> None:
        pts = f"{cx},{cy-h/2} {cx+w/2},{cy} {cx},{cy+h/2} {cx-w/2},{cy}"
        self.parts.append(
            f'<polygon points="{pts}" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>'
        )

    def node(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        label: str,
        fill: str = GRAY_PALE,
        stroke: str = GRAY,
        size: float = 12,
        dashed: bool = False,
    ) -> None:
        self.rect(x, y, w, h, fill, stroke, 1.3, 2, "5 3" if dashed else None)
        self.text(x + w / 2, y + h / 2 + size * 0.34, label, size, INK, 600, "middle")

    def token(
        self,
        x: float,
        y: float,
        label: str,
        fill: str,
        stroke: str,
        r: float = 17,
        size: float = 11,
    ) -> None:
        self.circle(x, y, r, fill, stroke, 1.4)
        self.text(x, y + size * 0.35, label, size, INK, 700, "middle")

    def save(self) -> Path:
        svg = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.width}" '
            f'height="{self.height}" viewBox="0 0 {self.width} {self.height}" '
            'role="img" aria-labelledby="title">\n'
            f'<title id="title">{escape(self.title)}</title>\n'
            + "\n".join(self.parts)
            + "\n</svg>\n"
        )
        path = OUT / self.filename
        path.write_text(svg, encoding="utf-8")
        return path


def system_overview() -> Figure:
    f = Figure("beliefkv_system_overview_paper.svg", "BeliefKV System Overview", 1500, 850)
    f.header()
    f.section(58, 112, "Agent workflow")
    f.section(445, 112, "Causal state")
    f.section(755, 112, "Action frontier")
    f.section(1004, 112, "Scheduler / SGLang")

    # The graph is a concrete spawn, tool wait, and join episode.
    f.token(98, 190, "R", TEAL_PALE, TEAL, 20, 12)
    f.token(213, 144, "A", PAPER, GRAY, 19)
    f.token(213, 239, "B", AMBER_PALE, AMBER, 19)
    f.node(291, 126, 64, 34, "tool", AMBER_PALE, AMBER, 10)
    f.diamond(365, 239, 78, 54)
    f.text(365, 243, "JOIN", 9.5, INK, 700, "middle")
    f.path(((118, 182), (150, 158), (194, 148)), TEAL, 1.5, arrow="teal-arrow")
    f.path(((118, 198), (152, 225), (194, 236)), TEAL, 1.5, arrow="teal-arrow")
    f.line(232, 144, 286, 144, AMBER, 1.5, arrow="amber-arrow")
    f.path(((355, 144), (378, 144), (378, 212)), AMBER, 1.5, arrow="amber-arrow")
    f.path(((232, 239), (321, 239)), AMBER, 1.5, arrow="amber-arrow")
    f.text(98, 225, "parent", 9.5, MUTED, 400, "middle")
    f.text(213, 178, "child", 9.5, MUTED, 400, "middle")
    f.text(213, 273, "child", 9.5, MUTED, 400, "middle")
    f.text(366, 278, "barrier", 9.5, MUTED, 400, "middle")
    f.text(58, 315, "SPAWN  ·  TOOL  ·  RETURN  ·  JOIN", 10, MUTED, 600)
    f.line(58, 330, 407, 330, RULE, 1)

    # RCCG is drawn as invocation state and dependency edges, not as a single block.
    f.token(482, 178, "R", TEAL_PALE, TEAL, 18)
    f.token(572, 140, "A", PAPER, GRAY, 17)
    f.token(572, 216, "B", AMBER_PALE, AMBER, 17)
    f.diamond(666, 178, 62, 48)
    f.text(666, 181, "AND", 9, INK, 700, "middle")
    f.line(500, 170, 555, 147, TEAL, 1.4, arrow="teal-arrow")
    f.line(500, 186, 555, 209, TEAL, 1.4, arrow="teal-arrow")
    f.path(((589, 140), (630, 140), (646, 164)), INK, 1.4, arrow="arrow")
    f.line(589, 216, 635, 187, AMBER, 1.5, arrow="amber-arrow")
    f.text(572, 177, "WAIT_TOOL", 8.5, MUTED, 400, "middle")
    f.text(572, 253, "RUN", 8.5, MUTED, 400, "middle")
    f.text(666, 218, "JOIN_ALL", 9, MUTED, 400, "middle")

    # Priority is represented as an ordered frontier, while prediction remains a hint.
    frontier = [
        (132, "JOIN blocker", AMBER_PALE, AMBER),
        (169, "causal unlock", TEAL_PALE, TEAL),
        (206, "ready", GRAY_PALE, GRAY),
    ]
    for y, label, fill, stroke in frontier:
        f.circle(777, y, 7, fill, stroke, 1.2)
        f.text(793, y + 4, label, 10.5, INK, 500)
    f.path(((701, 178), (735, 178), (735, 169), (766, 169)), INK, 1.5, arrow="arrow")
    f.text(755, 247, "RETURN changes readiness;", 9.5, MUTED)
    f.text(755, 262, "ETA only shifts action timing.", 9.5, MUTED)

    # Native admission is a solid path; a dashed candidate path is not enabled by default.
    f.section(1012, 139, "waiting queue")
    for i, (x, label) in enumerate(((1022, "r1"), (1064, "r2"), (1106, "r3"))):
        f.rect(x, 154, 34, 24, PAPER, GRAY, 1)
        f.text(x + 17, 170, label, 9.5, INK, 600, "middle")
        if i < 2:
            f.line(x + 35, 166, x + 40, 166, INK, 1.2, arrow="arrow")
    f.node(1155, 144, 68, 44, "safe\npoint", AMBER_PALE, AMBER, 10)
    f.node(1240, 144, 91, 44, "PrefillAdder", GRAY_PALE, GRAY, 10)
    f.rect(1361, 139, 80, 54, BLUE_PALE, BLUE, 1.4, 2)
    for i in range(3):
        f.rect(1370 + i * 21, 149, 15, 33, "#d9e5ef", BLUE, 0.8, 1)
    f.text(1401, 207, "GPU batch", 9.5, MUTED, 500, "middle")
    f.line(1140, 166, 1150, 166, INK, 1.4, arrow="arrow")
    f.line(1223, 166, 1235, 166, INK, 1.4, arrow="arrow")
    f.line(1331, 166, 1355, 166, INK, 1.4, arrow="arrow")

    f.section(461, 304, "Asynchronous prediction")
    f.node(468, 325, 104, 38, "snapshot", GRAY_PALE, GRAY, 10)
    f.node(611, 325, 122, 38, "predictor", BLUE_PALE, BLUE, 10, True)
    f.node(776, 325, 114, 38, "candidate", BLUE_PALE, BLUE, 10, True)
    f.path(((666, 203), (666, 318), (520, 318), (520, 320)), BLUE, 1.2, "5 3", "blue-arrow")
    f.line(572, 344, 606, 344, BLUE, 1.5, "5 3", "blue-arrow")
    f.line(733, 344, 771, 344, BLUE, 1.5, "5 3", "blue-arrow")
    f.path(((890, 344), (928, 344), (928, 293), (1142, 293), (1142, 166), (1150, 166)), BLUE, 1.5, "5 3", "blue-arrow")
    f.text(958, 286, "latest result; live recheck", 9.5, BLUE, 500)
    f.text(903, 375, "P6 candidate path: disabled by default on Qwen3.5 / SGLang 0.5.20", 9.2, BLUE, 500)

    f.section(58, 432, "KV data plane")
    f.text(1030, 432, "device", 9.5, MUTED, 600, "middle")
    f.text(1289, 432, "NUMA-local host", 9.5, MUTED, 600, "middle")
    f.rect(964, 447, 142, 30, BLUE_PALE, BLUE, 1.2, 1)
    f.rect(964, 481, 142, 30, TEAL_PALE, TEAL, 1.2, 1)
    f.text(1035, 467, "FULL pages", 10, BLUE, 600, "middle")
    f.text(1035, 501, "MAMBA slots", 10, TEAL, 600, "middle")
    f.rect(1216, 447, 142, 30, BLUE_PALE, BLUE, 1.2, 1)
    f.rect(1216, 481, 142, 30, TEAL_PALE, TEAL, 1.2, 1)
    f.text(1287, 467, "FULL pool", 10, BLUE, 600, "middle")
    f.text(1287, 501, "MAMBA pool", 10, TEAL, 600, "middle")
    f.line(1112, 457, 1207, 457, INK, 1.4, arrow="arrow")
    f.line(1207, 491, 1112, 491, TEAL, 1.4, arrow="teal-arrow")
    f.text(1160, 451, "D2H", 8.5, INK, 600, "middle")
    f.text(1160, 507, "H2D", 8.5, TEAL, 600, "middle")
    f.node(1082, 531, 126, 34, "ACK ledger", TEAL_PALE, TEAL, 10)
    f.path(((1160, 512), (1160, 524)), TEAL, 1.2, "2 3", "teal-arrow")
    f.text(58, 474, "FULL and MAMBA have", 10, MUTED)
    f.text(58, 490, "separate pool accounting.", 10, MUTED)
    f.text(58, 519, "ACK updates physical state;", 10, MUTED)
    f.text(58, 535, "enqueue alone does not.", 10, MUTED)

    # Evidence path is shown below the runtime, separate from the online control loop.
    f.line(58, 609, 1442, 609, RULE, 1)
    f.section(58, 636, "Offline evidence loop")
    for y, label in ((669, "causal events"), (691, "request tokens"), (713, "pool / ACK")):
        f.circle(74, y - 3, 4, TEAL, TEAL, 0.8)
        f.text(86, y, label, 9.5, MUTED)
        f.line(179, y - 3, 242, y - 3, TEAL, 1.1, "2 3")
    f.line(242, 666, 242, 710, TEAL, 1.1)
    f.line(242, 688, 266, 688, TEAL, 1.2, "2 3", "teal-arrow")
    f.node(272, 668, 106, 40, "trace join", GRAY_PALE, GRAY, 10)
    f.line(378, 688, 409, 688, INK, 1.3, arrow="arrow")
    f.node(415, 668, 120, 40, "label + audit", AMBER_PALE, AMBER, 10)
    f.line(535, 688, 566, 688, INK, 1.3, arrow="arrow")
    f.node(572, 668, 128, 40, "train / calibrate", AMBER_PALE, AMBER, 10)
    f.line(700, 688, 731, 688, BLUE, 1.3, "5 3", "blue-arrow")
    f.node(737, 668, 126, 40, "gated model", BLUE_PALE, BLUE, 10)
    return f


def causal_frontier() -> Figure:
    f = Figure("beliefkv_causal_frontier_paper.svg", "Causal Frontier: From Events to Useful Work", 1320, 720)
    f.header()
    f.section(58, 119, "Observed dependency graph")

    # One unfinished child is the only remaining blocker of a parent JOIN.
    f.token(132, 266, "P", TEAL_PALE, TEAL, 23, 13)
    f.text(132, 306, "parent", 10, MUTED, 500, "middle")
    f.diamond(315, 266, 100, 76)
    f.text(315, 270, "JOIN_ALL", 10, INK, 700, "middle")
    for y, label, state, fill, stroke in (
        (168, "A", "RETURN", TEAL_PALE, TEAL),
        (266, "B", "RUN", AMBER_PALE, AMBER),
        (364, "C", "RETURN", TEAL_PALE, TEAL),
    ):
        f.token(517, y, label, fill, stroke, 22, 12)
        f.text(517, y + 40, state, 9.5, stroke, 600, "middle")
        f.path(((495, y), (430, y), (366, 254 if y < 266 else 266 if y == 266 else 278)), stroke, 1.5, arrow="arrow" if label != "B" else "amber-arrow")
    f.line(155, 266, 263, 266, TEAL, 1.5, arrow="teal-arrow")
    f.text(234, 245, "waits", 9, MUTED, 500, "middle")
    f.text(400, 419, "B is the sole unfinished member", 10, AMBER, 600, "middle")
    f.line(58, 450, 620, 450, RULE, 1)

    f.section(58, 486, "Event truth")
    states = (
        (102, "RUN", GRAY_PALE, GRAY),
        (224, "WAIT", AMBER_PALE, AMBER),
        (346, "RETURN", TEAL_PALE, TEAL),
        (468, "READY", BLUE_PALE, BLUE),
    )
    for x, label, fill, stroke in states:
        f.rect(x, 520, 86, 36, fill, stroke, 1.2, 2)
        f.text(x + 43, 543, label, 10, INK, 650, "middle")
    for x in (188, 310, 432):
        f.line(x, 538, x + 31, 538, INK, 1.4, arrow="arrow")
    f.text(58, 601, "Only confirmed RETURN satisfies the edge.", 10, MUTED, 500)

    f.section(712, 119, "Frontier output")
    rows = (
        ("1", "sole JOIN blocker", AMBER_PALE, AMBER, 430),
        ("2", "unlocking chain", TEAL_PALE, TEAL, 365),
        ("3", "message producer", BLUE_PALE, BLUE, 300),
        ("4", "other ready work", GRAY_PALE, GRAY, 235),
    )
    for i, (rank, label, fill, stroke, length) in enumerate(rows):
        y = 169 + i * 72
        f.circle(750, y, 13, fill, stroke, 1.2)
        f.text(750, y + 4, rank, 10, stroke, 700, "middle")
        f.text(779, y + 4, label, 11, INK, 550)
        f.rect(1007, y - 6, length * 0.42, 12, fill, stroke, 0.8, 1)
        f.text(1218, y + 4, "priority", 8.5, MUTED, 500, "end")
    f.path(((620, 266), (674, 266), (674, 169), (725, 169)), AMBER, 1.6, arrow="amber-arrow")
    f.rect(750, 506, 222, 54, BLUE_PALE, BLUE, 1.1, 2, "5 3")
    f.text(861, 528, "RETURN-time belief", 10, BLUE, 600, "middle")
    f.text(861, 545, "sets timing, not causality", 9, MUTED, 450, "middle")
    f.path(((517, 408), (517, 535), (744, 535)), BLUE, 1.3, "5 3", "blue-arrow")
    f.text(58, 666, "The same frontier semantics feed native and predictive scheduling.", 10, MUTED, 500)
    return f


def scheduler_scopes() -> Figure:
    f = Figure("beliefkv_joint_scheduler_paper.svg", "Scheduling Scopes and Commit Authority", 1500, 760)
    f.header()
    xs = (70, 430, 800, 1170)
    f.section(xs[0], 121, "1  Workflow scope")
    f.section(xs[1], 121, "2  Request scope")
    f.section(xs[2], 121, "3  Joint action scope")
    f.section(xs[3], 121, "4  Commit authority")
    f.line(390, 130, 390, 500, RULE, 1)
    f.line(760, 130, 760, 500, RULE, 1)
    f.line(1130, 130, 1130, 500, RULE, 1)

    # Dynamic working set: workflows and active target, not per-request commands.
    workflows = (
        (157, "W1", TEAL_PALE, TEAL),
        (222, "W2", TEAL_PALE, TEAL),
        (287, "W3", GRAY_PALE, GRAY),
        (352, "W4", GRAY_PALE, GRAY),
    )
    for y, label, fill, stroke in workflows:
        f.rect(82, y, 43, 25, fill, stroke, 1, 2)
        f.text(104, y + 17, label, 9, INK, 600, "middle")
        for j in range(3):
            color = stroke if j < (2 if y < 250 else 1) else RULE
            f.circle(160 + j * 23, y + 12, 5, color, color, 0.7)
    f.path(((80, 145), (80, 137), (224, 137), (224, 145)), AMBER, 1.4)
    f.text(152, 132, "active set", 9, AMBER, 550, "middle")
    f.text(82, 414, "input: pressure + frontier", 9.5, MUTED)
    f.text(82, 432, "output: workflow target", 9.5, MUTED)
    f.text(82, 457, "no ticket; no KV command", 9, AMBER, 550)

    # Ticket compiler: short-lived identity and eligibility result for visible requests.
    f.text(438, 151, "visible queue", 9, MUTED, 500)
    for i, (label, status) in enumerate((("r1", "yes"), ("r2", "skip"), ("r3", "yes"))):
        y = 170 + i * 57
        f.rect(442, y, 47, 27, PAPER, GRAY, 1)
        f.text(466, y + 18, label, 9, INK, 600, "middle")
        ok = status == "yes"
        f.circle(510, y + 13, 5, TEAL if ok else RED, TEAL if ok else RED, 0.7)
        f.text(521, y + 17, status, 8.5, TEAL if ok else RED, 550)
        if ok:
            f.rect(574, y + 3, 104, 21, TEAL_PALE, TEAL, 0.9, 1)
            f.text(626, y + 17, "id · epoch · gen", 8, INK, 500, "middle")
        else:
            f.text(626, y + 17, "stale session", 8, RED, 500, "middle")
    f.text(438, 370, "one prefill epoch", 9.5, MUTED)
    f.text(438, 388, "eligible IDs + skip reason", 9.5, MUTED)
    f.text(438, 420, "does not reserve GPU memory", 9, AMBER, 550)

    # Joint action package couples a beneficiary to a safe victim and a deadline.
    f.token(841, 186, "B", BLUE_PALE, BLUE, 20, 12)
    f.text(841, 222, "beneficiary", 9, BLUE, 550, "middle")
    f.token(841, 318, "V", AMBER_PALE, AMBER, 20, 12)
    f.text(841, 354, "parkable victim", 9, AMBER, 550, "middle")
    f.node(901, 168, 147, 38, "future deficit", BLUE_PALE, BLUE, 10)
    f.node(901, 300, 147, 38, "reclaimable KV", AMBER_PALE, AMBER, 10)
    f.line(862, 186, 896, 186, BLUE, 1.3, arrow="blue-arrow")
    f.line(862, 318, 896, 318, AMBER, 1.3, arrow="amber-arrow")
    f.rect(1061, 204, 51, 98, PAPER, BLUE, 1.2, 2, "5 3")
    f.text(1086, 227, "SCHED", 8.5, BLUE, 650, "middle")
    f.text(1086, 249, "D2H", 9, BLUE, 600, "middle")
    f.text(1086, 269, "H2D", 9, BLUE, 600, "middle")
    f.line(1048, 187, 1056, 216, BLUE, 1.2, "5 3", "blue-arrow")
    f.line(1048, 319, 1056, 287, BLUE, 1.2, "5 3", "blue-arrow")
    f.text(800, 388, "candidate = order + action + expiry", 9.5, MUTED)
    f.text(800, 420, "async result; never touches live scheduler", 9, BLUE, 500)

    # The scheduler safe point checks identity/state; native SGLang remains final authority.
    f.node(1181, 195, 101, 44, "safe point", AMBER_PALE, AMBER, 10)
    f.node(1327, 195, 111, 44, "allocator", GRAY_PALE, GRAY, 10)
    f.line(1282, 217, 1322, 217, INK, 1.5, arrow="arrow")
    for i, label in enumerate(("id / epoch", "context gen", "capacity + lock")):
        y = 281 + i * 31
        f.circle(1191, y - 3, 3.4, TEAL, TEAL, 0.7)
        f.text(1203, y, label, 9.5, MUTED)
    f.path(((1112, 253), (1150, 253), (1150, 217), (1176, 217)), BLUE, 1.4, "5 3", "blue-arrow")
    f.text(1179, 409, "stale / unsafe", 9, RED, 550)
    f.path(((1194, 417), (1276, 417), (1276, 217), (1322, 217)), RED, 1.1, "3 3", "arrow")
    f.text(1327, 266, "final admit / reject", 9, MUTED)

    f.line(58, 475, 1442, 475, RULE, 1)
    f.section(58, 511, "Native admission path")
    f.rect(235, 531, 118, 35, GRAY_PALE, GRAY, 1, 2)
    f.text(294, 553, "ticket compiler", 9.5, INK, 550, "middle")
    f.line(353, 549, 461, 549, INK, 1.5, arrow="arrow")
    f.rect(468, 531, 125, 35, AMBER_PALE, AMBER, 1, 2)
    f.text(530, 553, "safe point", 9.5, INK, 550, "middle")
    f.line(593, 549, 701, 549, INK, 1.5, arrow="arrow")
    f.rect(708, 531, 142, 35, GRAY_PALE, GRAY, 1, 2)
    f.text(779, 553, "SGLang allocator", 9.5, INK, 550, "middle")
    f.text(901, 553, "solid = current native admission", 9, MUTED)

    f.section(58, 620, "Predictive physical action path")
    f.rect(235, 640, 165, 35, BLUE_PALE, BLUE, 1, 2, "5 3")
    f.text(317, 662, "JointPlan candidate", 9.5, BLUE, 550, "middle")
    f.line(400, 658, 508, 658, BLUE, 1.4, "5 3", "blue-arrow")
    f.rect(515, 640, 135, 35, AMBER_PALE, AMBER, 1, 2)
    f.text(582, 662, "live revalidate", 9.5, INK, 550, "middle")
    f.line(650, 658, 758, 658, BLUE, 1.4, "5 3", "blue-arrow")
    f.rect(765, 640, 168, 35, BLUE_PALE, BLUE, 1, 2, "5 3")
    f.text(849, 662, "PREPARE / COMMIT / PREFETCH", 8.4, BLUE, 550, "middle")
    f.text(958, 662, "staged; action gate OFF on Qwen3.5 / 0.5.20", 9, BLUE, 500)
    return f


def safe_point_sequence() -> Figure:
    f = Figure("beliefkv_safe_point_sequence_paper.svg", "Safe Point in the Scheduler Selection Path", 1450, 800)
    f.header()
    actors = (
        (145, "Agent runtime"),
        (370, "Event adapter"),
        (590, "Predictor process"),
        (865, "Scheduler main"),
        (1110, "SGLang allocator"),
        (1320, "DMA worker"),
    )
    for x, label in actors:
        f.text(x, 120, label, 10, INK, 650, "middle")
        f.line(x, 137, x, 713, RULE, 1, "3 4")
    f.text(56, 163, "event", 9, MUTED, 600)
    f.line(145, 176, 365, 176, TEAL, 1.5, arrow="teal-arrow")
    f.text(253, 168, "TOOL / SPAWN / RETURN", 9, TEAL, 550, "middle")

    f.text(56, 224, "snapshot", 9, MUTED, 600)
    f.line(865, 235, 375, 235, BLUE, 1.4, "5 3", "blue-arrow")
    f.text(618, 227, "compact causal + demand delta", 9, BLUE, 500, "middle")
    f.line(370, 248, 585, 248, BLUE, 1.4, "5 3", "blue-arrow")
    f.text(478, 264, "capacity-one latest-wins", 8.5, MUTED, 450, "middle")

    # Worker inference runs independently while the scheduler continues selecting.
    f.rect(513, 286, 154, 61, BLUE_PALE, BLUE, 1.1, 2, "5 3")
    f.text(590, 311, "infer", 10, BLUE, 650, "middle")
    f.text(590, 329, "no live scheduler access", 8.5, MUTED, 450, "middle")
    f.line(590, 347, 590, 383, BLUE, 1.3, "5 3")
    f.rect(531, 384, 118, 32, PAPER, BLUE, 1, 2, "5 3")
    f.text(590, 405, "result mailbox", 9, BLUE, 550, "middle")
    f.path(((649, 400), (865, 400)), BLUE, 1.5, "5 3", "blue-arrow")
    f.text(755, 391, "fd wakeup", 8.5, BLUE, 500, "middle")

    f.text(56, 308, "selection", 9, MUTED, 600)
    f.rect(772, 278, 187, 169, "#fbfcfd", AMBER, 1.4, 2)
    f.text(865, 301, "synchronous safe point", 10, AMBER, 650, "middle")
    steps = (
        ("1", "drain bounded events"),
        ("2", "poll latest result"),
        ("3", "rebind ID / epoch"),
        ("4", "check live state"),
    )
    for i, (n, label) in enumerate(steps):
        y = 329 + i * 27
        f.circle(800, y - 3, 8, AMBER_PALE, AMBER, 1)
        f.text(800, y, n, 8, AMBER, 700, "middle")
        f.text(817, y, label, 8.8, INK, 500)
    f.text(865, 437, "inside batch selection", 8.5, MUTED, 450, "middle")

    f.text(56, 494, "native call", 9, MUTED, 600)
    f.line(959, 506, 1105, 506, INK, 1.6, arrow="arrow")
    f.text(1032, 498, "admit / skip", 8.5, INK, 500, "middle")
    f.text(56, 553, "transfer", 9, MUTED, 600)
    f.line(1110, 565, 1315, 565, BLUE, 1.5, arrow="blue-arrow")
    f.text(1212, 557, "native async DMA", 8.5, BLUE, 500, "middle")
    f.line(1320, 591, 1115, 591, TEAL, 1.5, arrow="teal-arrow")
    f.text(1216, 608, "ACK / capacity result", 8.5, TEAL, 500, "middle")
    f.path(((1110, 591), (1110, 650), (865, 650)), TEAL, 1.2, "2 3", "teal-arrow")
    f.text(987, 643, "receipt updates mirror", 8.5, TEAL, 500, "middle")

    f.line(56, 688, 1394, 688, RULE, 1)
    f.text(56, 721, "Safe point runs when the scheduler selects a batch; it is not a separate periodic thread.", 10, MUTED, 500)
    f.text(56, 742, "Predictor latency never blocks this path; SGLang remains the physical admission authority.", 10, MUTED, 500)
    return f


def predictive_timeline() -> Figure:
    f = Figure("beliefkv_predictive_timeline_paper.svg", "Predictive KV Handoff: Copy, Reclaim, Prefetch", 1450, 790)
    f.header()
    f.text(1392, 109, "P6 TARGET", 9, BLUE, 700, "end")
    f.text(1392, 126, "action gate off on 0.5.20", 8.5, MUTED, 450, "end")
    left, right = 190, 1394
    ticks = (
        (220, "wait"),
        (485, "rolling update"),
        (745, "latest start"),
        (1060, "confirmed return"),
        (1240, "admit"),
        (1380, "service"),
    )
    for y in (180, 292, 408, 524, 640):
        f.line(left, y, right, y, RULE, 1)
    for x, label in ticks:
        f.line(x, 681, x, 697, INK, 1)
        f.text(x, 716, label, 8.8, MUTED, 450, "middle")
    f.line(left, 689, right, 689, INK, 1.2)

    lanes = (
        (160, "context"),
        (274, "belief"),
        (390, "D2H"),
        (506, "residency"),
        (622, "H2D / service"),
    )
    for y, label in lanes:
        f.text(58, y + 4, label.upper(), 9, MUTED, 650)

    f.circle(220, 180, 5, TEAL, TEAL)
    f.text(220, 155, "TOOL_START / child wait", 9, TEAL, 550, "middle")
    f.circle(1060, 180, 5, TEAL, TEAL)
    f.text(1060, 155, "RETURN / JOIN_SATISFIED", 9, TEAL, 550, "middle")
    f.line(226, 180, 1054, 180, TEAL, 1.5)

    # Rolling estimates revise the window; provisional completion is not RETURN.
    f.line(485, 292, 840, 292, BLUE, 4, "7 5")
    f.line(610, 279, 610, 305, BLUE, 1.2)
    f.line(745, 277, 745, 307, BLUE, 1.6)
    f.line(840, 279, 840, 305, BLUE, 1.2)
    f.text(485, 271, "P10", 8.5, BLUE, 600)
    f.text(745, 271, "P50", 8.5, BLUE, 600, "middle")
    f.text(840, 271, "P90", 8.5, BLUE, 600, "middle")
    f.circle(930, 292, 6, AMBER_PALE, AMBER, 1.4)
    f.text(942, 296, "provisional completion", 8.7, AMBER, 500)

    # Copying and reclaiming are distinct operations.
    f.rect(315, 377, 175, 26, BLUE_PALE, BLUE, 1, 1)
    f.text(402, 395, "PREPARE_HOST  ·  D2H", 8.8, BLUE, 600, "middle")
    f.circle(490, 390, 5, TEAL, TEAL)
    f.text(490, 414, "ACK", 8, TEAL, 550, "middle")
    f.rect(600, 377, 100, 26, AMBER_PALE, AMBER, 1, 1)
    f.text(650, 395, "COMMIT_CPU", 8.3, AMBER, 600, "middle")
    f.line(650, 403, 650, 465, AMBER, 1.2, arrow="amber-arrow")
    f.text(716, 426, "only on observed deficit", 8.3, AMBER, 500)

    f.line(220, 523, 650, 523, BLUE, 5)
    f.text(225, 512, "GPU copy", 8.5, BLUE, 550)
    f.line(490, 536, 940, 536, TEAL, 4, "2 3")
    f.text(498, 555, "Host shadow", 8.5, TEAL, 550)
    f.circle(650, 523, 5, AMBER, AMBER)
    f.text(650, 501, "release", 8, AMBER, 550, "middle")

    f.rect(745, 610, 255, 26, BLUE_PALE, BLUE, 1, 1, "6 3")
    f.text(872, 628, "PREFETCH_GPU  ·  H2D", 8.8, BLUE, 600, "middle")
    f.circle(1000, 623, 5, TEAL, TEAL)
    f.text(1000, 647, "ACK", 8, TEAL, 550, "middle")
    f.line(1060, 622, 1235, 622, INK, 1.8, arrow="arrow")
    f.circle(1240, 622, 5, TEAL, TEAL)
    f.text(1240, 603, "admit", 8, TEAL, 550, "middle")
    f.line(1246, 622, 1372, 622, INK, 1.8, arrow="arrow")
    f.circle(1380, 622, 5, TEAL, TEAL)
    f.text(1380, 603, "first service", 8, TEAL, 550, "middle")

    f.text(58, 755, "PREPARE creates a Host copy; COMMIT releases HBM; H2D ACK precedes reentry.", 9.5, MUTED, 500)
    return f


def kv_dataplane() -> Figure:
    f = Figure("beliefkv_kv_dataplane_paper.svg", "KV Residency, Sharing, and Transfer", 1450, 790)
    f.header()
    f.section(58, 121, "Radix sharing")
    f.section(482, 121, "Pool geometry")
    f.section(1023, 121, "Physical transaction")

    # Shared ancestor is owned by both contexts; only private leaves are reclaimable.
    f.circle(112, 244, 25, BLUE_PALE, BLUE, 1.4)
    f.text(112, 248, "p0", 10, BLUE, 700, "middle")
    f.text(112, 284, "shared", 8.5, MUTED, 450, "middle")
    f.circle(268, 183, 23, PAPER, GRAY, 1.3)
    f.circle(268, 304, 23, PAPER, GRAY, 1.3)
    f.text(268, 187, "A", 10, INK, 700, "middle")
    f.text(268, 308, "B", 10, INK, 700, "middle")
    f.rect(351, 160, 61, 45, AMBER_PALE, AMBER, 1.2, 1)
    f.rect(351, 281, 61, 45, TEAL_PALE, TEAL, 1.2, 1)
    f.text(381, 179, "a1 a2", 8.5, AMBER, 600, "middle")
    f.text(381, 300, "b1 b2", 8.5, TEAL, 600, "middle")
    f.line(137, 235, 244, 191, BLUE, 1.5)
    f.line(137, 253, 244, 296, BLUE, 1.5)
    f.line(291, 183, 346, 183, AMBER, 1.5, arrow="amber-arrow")
    f.line(291, 304, 346, 304, TEAL, 1.5, arrow="teal-arrow")
    f.rect(80, 329, 68, 23, PAPER, BLUE, 1, 1, "4 3")
    f.text(114, 345, "owner=2", 8.5, BLUE, 550, "middle")
    f.text(175, 345, "shared prefix != private reclaim", 8.5, MUTED, 500)
    f.text(350, 359, "victim candidates", 8.5, AMBER, 500)

    # FULL pages and MAMBA slots are independent pools on both tiers.
    f.text(625, 153, "GPU HBM", 11, INK, 650, "middle")
    f.text(848, 153, "NUMA-local Host DRAM", 11, INK, 650, "middle")
    for y, label, color, pale in (
        (177, "FULL pages", BLUE, BLUE_PALE),
        (236, "MAMBA slots", TEAL, TEAL_PALE),
    ):
        f.rect(495, y, 251, 42, pale, color, 1.2, 1)
        f.text(510, y + 25, label, 9.5, color, 600)
        for i in range(5):
            fill = color if i < 3 else PAPER
            f.rect(625 + i * 21, y + 10, 14, 22, fill, color, 0.9, 1)
    for y, label, color, pale in (
        (177, "FULL pool", BLUE, BLUE_PALE),
        (236, "MAMBA pool", TEAL, TEAL_PALE),
    ):
        f.rect(772, y, 251, 42, pale, color, 1.2, 1)
        f.text(787, y + 25, label, 9.5, color, 600)
        for i in range(5):
            fill = color if i < (2 if y == 177 else 3) else PAPER
            f.rect(902 + i * 21, y + 10, 14, 22, fill, color, 0.9, 1)
    f.line(751, 188, 766, 188, BLUE, 1.4, arrow="arrow")
    f.line(766, 208, 751, 208, TEAL, 1.4, arrow="teal-arrow")
    f.line(751, 247, 766, 247, BLUE, 1.4, arrow="arrow")
    f.line(766, 267, 751, 267, TEAL, 1.4, arrow="teal-arrow")
    f.text(758, 180, "D2H", 7.5, INK, 500, "middle")
    f.text(758, 221, "H2D", 7.5, TEAL, 500, "middle")
    f.text(758, 239, "D2H", 7.5, INK, 500, "middle")
    f.text(758, 280, "H2D", 7.5, TEAL, 500, "middle")
    f.text(495, 321, "independent capacity / accounting", 8.5, MUTED, 500)

    # A command is not a residency change: identity, generation, ownership and ACK matter.
    cert = (
        ("ID", "request / epoch"),
        ("GEN", "page generation"),
        ("OWN", "owner closure"),
        ("LOCK", "transaction"),
        ("CAP", "FULL + MAMBA"),
    )
    for i, (code, label) in enumerate(cert):
        y = 161 + i * 38
        f.circle(1055, y, 10, GRAY_PALE, GRAY, 1)
        f.text(1055, y + 3, code, 6.6, INK, 700, "middle")
        f.text(1073, y + 4, label, 8.7, MUTED, 500)
    f.path(((1136, 238), (1175, 238)), INK, 1.4, arrow="arrow")
    f.node(1180, 218, 91, 40, "command", AMBER_PALE, AMBER, 9)
    f.path(((1271, 238), (1302, 238)), BLUE, 1.4, "5 3", "blue-arrow")
    f.circle(1322, 238, 18, TEAL_PALE, TEAL, 1.3)
    f.text(1322, 242, "ACK", 8.5, TEAL, 700, "middle")
    f.path(((1322, 258), (1322, 304), (1060, 304)), TEAL, 1.2, "2 3", "teal-arrow")
    f.text(1188, 322, "only ACK changes residency", 8.8, TEAL, 550, "middle")
    f.text(1023, 370, "0.5.20: native observation exists;", 8.5, MUTED, 450)
    f.text(1023, 386, "cross-pool action proof is incomplete.", 8.5, MUTED, 450)

    f.line(58, 442, 1392, 442, RULE, 1)
    f.section(58, 477, "State transition")
    states = (
        (145, "GPU_ONLY", BLUE_PALE, BLUE),
        (390, "DUAL", TEAL_PALE, TEAL),
        (635, "CPU_ONLY", AMBER_PALE, AMBER),
        (880, "RESTORE", BLUE_PALE, BLUE),
        (1125, "GPU_ONLY", BLUE_PALE, BLUE),
    )
    for x, label, fill, stroke in states:
        f.rect(x, 514, 113, 40, fill, stroke, 1.2, 2)
        f.text(x + 56, 539, label, 9, INK, 600, "middle")
    for i, (x, label) in enumerate(((258, "D2H ACK"), (503, "commit"), (748, "H2D"), (993, "H2D ACK"))):
        f.line(x, 534, x + 128, 534, TEAL if "ACK" in label else INK, 1.4,
               arrow="teal-arrow" if "ACK" in label else "arrow")
        f.text(x + 64, 521, label, 8.3, TEAL if "ACK" in label else MUTED, 500, "middle")
    return f


def telemetry_pipeline() -> Figure:
    f = Figure("beliefkv_telemetry_training_paper.svg", "Request-Aligned Telemetry and Labels", 1450, 790)
    f.header()
    f.section(58, 121, "Independent evidence streams")

    streams = (
        (158, "causal", TEAL),
        (222, "request", BLUE),
        (286, "pool", AMBER),
        (350, "HiCache", GRAY),
    )
    xs = (233, 358, 493, 640, 793, 947)
    for y, name, color in streams:
        f.text(58, y + 4, name, 9, color, 600)
        f.line(143, y, 992, y, RULE, 1)
        for i, x in enumerate(xs):
            if (i + (y // 64)) % 2 == 0:
                f.circle(x, y, 4, color, color, 0.7)
        f.text(1001, y + 3, "time", 8, MUTED, 450)
    f.text(233, 136, "SPAWN", 7.5, TEAL, 500, "middle")
    f.text(358, 200, "tokens", 7.5, BLUE, 500, "middle")
    f.text(493, 264, "evict", 7.5, AMBER, 500, "middle")
    f.text(640, 328, "ACK", 7.5, GRAY, 500, "middle")
    f.path(((1026, 250), (1081, 250)), INK, 1.4, arrow="arrow")
    f.node(1087, 210, 256, 80, "join by run · request · epoch · time", GRAY_PALE, GRAY, 10)
    f.text(1215, 268, "token + unit conservation", 8, MUTED, 450, "middle")

    f.line(58, 408, 1392, 408, RULE, 1)
    f.section(58, 438, "Per-decision labels")
    labels = (
        (102, "return time", TEAL_PALE, TEAL),
        (305, "token demand", BLUE_PALE, BLUE),
        (508, "admission", AMBER_PALE, AMBER),
        (711, "KV action", BLUE_PALE, BLUE),
        (914, "hit / recompute", TEAL_PALE, TEAL),
    )
    for x, name, fill, stroke in labels:
        f.rect(x, 466, 175, 42, fill, stroke, 1.1, 2)
        f.text(x + 87, 492, name, 9.5, INK, 550, "middle")
    f.line(188, 456, 1001, 456, RULE, 1.2)
    f.path(((1215, 290), (1215, 456), (1001, 456)), INK, 1.2, arrow="arrow")
    for x, _, _, _ in labels:
        f.line(x + 87, 456, x + 87, 470, INK, 1, arrow="arrow")

    f.node(1180, 466, 166, 42, "censor audit", RED_PALE, RED, 9)
    f.path(((1215, 290), (1364, 290), (1364, 487), (1351, 487)), RED, 1.2, "4 3", "arrow")
    f.text(1180, 526, "intervention / missing evidence", 8, RED, 500)

    f.line(58, 565, 1392, 565, RULE, 1)
    f.section(58, 598, "Workflow-level split and model gate")
    for i, (x, label, color) in enumerate(
        ((131, "train", TEAL), (333, "calibration", AMBER), (535, "test", BLUE))
    ):
        f.rect(x, 622, 151, 48, PAPER, color, 1.2, 2)
        f.text(x + 75, 651, label, 10, color, 600, "middle")
        f.text(x + 75, 688, f"workflow IDs {i + 1}", 8, MUTED, 450, "middle")
    f.path(((686, 646), (775, 646)), INK, 1.4, arrow="arrow")
    f.node(782, 622, 173, 48, "versioned artifact", BLUE_PALE, BLUE, 9.5)
    f.path(((955, 646), (1044, 646)), BLUE, 1.4, "5 3", "blue-arrow")
    f.node(1051, 622, 173, 48, "online eligibility gate", AMBER_PALE, AMBER, 9)
    f.text(58, 744, "Native 0.5.20 ACKs lack DMA bytes / duration; PCIe service labels remain unavailable.", 9.2, MUTED, 500)
    return f


def main() -> None:
    figures = (
        system_overview(),
        causal_frontier(),
        scheduler_scopes(),
        safe_point_sequence(),
        predictive_timeline(),
        kv_dataplane(),
        telemetry_pipeline(),
    )
    for figure in figures:
        print(figure.save())


if __name__ == "__main__":
    main()
