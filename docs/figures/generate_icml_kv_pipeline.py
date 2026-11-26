#!/usr/bin/env python3
"""Draw a BeliefKV four-slot migration example from checked execution intervals."""

import argparse
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from generate_icml_figures import Art, OUTPUT, PowerPointExporter


W, H = 486, 292
LEFT, RIGHT = 62, 475
INK, WHITE, GUIDE = "#1e2022", "#ffffff", "#555b60"
EDGES = {"tool": "#ae713a", "space": "#367d74", "transfer": "#805c84"}
FAMILY = {"A": ("#a7bfd6", "#c7d8e7", "#425e82", "#648098"),
          "B": ("#acc0b2", "#cbd9ce", "#426450", "#657f6c")}
FONT_SIZE = 8.8
COPY = 5
CHILDREN = {"A": ("a1", "a2"), "B": ("b1", "b2", "b3")}
CAPTION = (
    "A and B arrive together. Dashed arrows follow children from SPAWN to ALL-JOIN. "
    "One HBM row holds one agent's private KV. Host rows show the three stable checkpoints "
    "that move. The rising edge shows KV arriving at a reserved destination. The whole slot "
    "is committed from the start of the transfer. The source stays valid through completion. "
    "Both roots start PREPARE_HOST at SPAWN. When B's children need space, A and B immediately "
    "release backed slots for b1 and b2. An open diamond marks this release. Long tool wait "
    "makes a1 a donor for b3. A filled square marks release at writeback ACK. During short tool "
    "calls the children's KV stays resident. When a1's tool completes, a1 waits for b3's slot, "
    "then for H2D into that slot. The validated final EOS of a1 and b2 triggers recovery of A "
    "and B, respectively. Both copies complete before the actual ALL-JOIN and native admission. "
    "The drawing shows writable private "
    "capacity in HBM and reusable stable prefixes on Host. Host copies retire at the agent's "
    "RETURN. Durations are schematic."
)


@dataclass(frozen=True)
class Interval:
    owner: str
    start: float
    end: float
    kind: str


@dataclass(frozen=True)
class Allocation:
    owner: str
    pool: str
    row: int
    start: float
    ready: float
    end: float


@dataclass(frozen=True)
class Transfer:
    owner: str
    target: str
    start: float

    @property
    def ack(self):
        return self.start + COPY


@dataclass
class Schedule:
    actions: list
    copies: list
    allocations: list
    spawns: dict
    returns: dict
    eos: dict

    def release(self, owner):
        return min(a.end for a in self.allocations if a.owner == owner and a.pool == "HBM")

    def join(self, root):
        return max(self.returns[c] for c in CHILDREN[root])

    def allocation(self, owner, pool, start, end, *, ready):
        hits = [a for a in self.allocations if a.owner == owner and a.pool == pool
                and (a.ready if ready else a.start) <= start + 1e-7 and a.end >= end - 1e-7]
        assert len(hits) == 1, (owner, pool, start, end, hits)
        return hits[0]

    def check(self):
        by_row, by_owner = defaultdict(list), defaultdict(list)
        for a in self.allocations:
            assert a.start <= a.ready < a.end
            assert 0 <= a.row < (4 if a.pool == "HBM" else 3)
            by_row[a.pool, a.row].append((a.start, a.end))
            by_owner[a.owner, a.pool].append((a.start, a.end))
        for intervals in [*by_row.values(), *by_owner.values()]:
            ordered = sorted(intervals)
            assert all(a[1] <= b[0] for a, b in zip(ordered, ordered[1:]))
        for op in self.copies:
            src = "HBM" if op.target == "Host" else "Host"
            self.allocation(op.owner, src, op.start, op.ack, ready=True)
            dest = self.allocation(op.owner, op.target, op.start, op.ack, ready=False)
            assert (dest.start, dest.ready) == (op.start, op.ack)
        for a in self.allocations:
            assert (a.ready > a.start) == any((c.owner, c.target, c.start, c.ack) ==
                                              (a.owner, a.pool, a.start, a.ready)
                                              for c in self.copies)
        for root, children in CHILDREN.items():
            root_service = [s for s in self.actions if s.owner == root and s.kind == "run"]
            assert root_service[0].end < self.spawns[root] < self.join(root) < root_service[1].start
            assert all(self.spawns[root] < s.start < s.end <= self.returns[s.owner]
                       for s in self.actions if s.owner in children)
            prepare, restore = sorted((c for c in self.copies if c.owner == root),
                                      key=lambda c: c.start)
            assert prepare.target == "Host" and prepare.start == self.spawns[root]
            assert prepare.ack < self.release(root)
            last = max(children, key=self.returns.get)
            assert max(s.end for s in self.actions if s.owner == last) == self.eos[last]
            assert max(self.returns[k] for k in children if k != last) < self.eos[last]
            assert restore.target == "HBM" and restore.start == self.eos[last]
            assert restore.ack <= self.join(root)
        for s in self.actions:
            if s.kind == "run" or (s.kind == "tool" and s.owner != "a1"):
                self.allocation(s.owner, "HBM", s.start, s.end, ready=True)
            elif s.kind == "transfer":
                target = self.allocation(s.owner, "HBM", s.start, s.end, ready=False)
                assert (s.start, s.end) == (target.start, target.ready)
            elif s.kind == "space":
                hbm = [a for a in self.allocations if a.pool == "HBM"]
                cuts = sorted({s.start, s.end} | {t for a in hbm for t in (a.start, a.end)
                                                  if s.start < t < s.end})
                for left, right in zip(cuts, cuts[1:]):
                    occupied = [a.owner for a in hbm if a.start <= left and right <= a.end]
                    assert len(occupied) == 4 and s.owner not in occupied
        for owner in self.returns:
            spans = sorted((s.start, s.end) for s in self.actions if s.owner == owner)
            assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:]))
        for (owner, _pool), intervals in by_owner.items():
            assert max(end for _, end in intervals) == self.returns[owner]
        for donor, recipient in (("A", "b1"), ("B", "b2"), ("a1", "b3")):
            old = next(a for a in self.allocations if a.owner == donor and a.pool == "HBM"
                       and a.end == self.release(donor))
            new = next(a for a in self.allocations if a.owner == recipient and a.pool == "HBM")
            assert (old.row, old.end) == (new.row, new.start)
            self.allocation(donor, "Host", old.end, old.end, ready=True)
        assert next(c.ack for c in self.copies if c.owner == "a1" and c.target == "Host") == self.release("a1")
        assert all(min(a.start for a in self.actions if a.owner == root) == 0 for root in CHILDREN)


def schedule():
    spawns = {"A": 6.4, "B": 15.7}
    first_a, b_ready, tool_done = 9.4, 22, 37.25
    backups = {root: Transfer(root, "Host", t) for root, t in spawns.items()}
    offload = Transfer("a1", "Host", 24.5)
    ret = {"a2": 51, "b1": 55, "b3": 44}
    reload = Transfer("a1", "HBM", ret["b3"])
    final = reload.ack + 1
    eos = {"a1": final + 10.5, "b2": 72.3}
    restores = {"A": Transfer("A", "HBM", eos["a1"]), "B": Transfer("B", "HBM", eos["b2"])}
    ret.update(a1=eos["a1"] + 6.3, b2=eos["b2"] + 6.3)
    resume_a, resume_b = ret["a1"] + .8, ret["b2"] + .8
    ret.update(A=resume_a + 10.4, B=resume_b + 9.4)
    runs = {
        "A": [(0, 5.8), (resume_a, ret["A"] - .4)],
        "a1": [(first_a, 16.3), (final, eos["a1"])],
        "a2": [(first_a, 21.6), (25.5, 50)],
        "B": [(0, 15.1), (resume_b, ret["B"] - .4)],
        "b1": [(b_ready+.6, 33.3), (37.2, ret["b1"]-1)],
        "b2": [(b_ready+.6, 46), (50, eos["b2"])],
        "b3": [(offload.ack+.7, ret["b3"]-1)],
    }
    actions = [Interval(owner, lo, hi, "run")
               for owner, spans in runs.items() for lo, hi in spans]
    actions.extend([Interval("a1", 17.25, tool_done, "tool"),
                    Interval("a2", 22.3, 24.8, "tool"),
                    Interval("b1", 34, 36.5, "tool"),
                    Interval("b2", 46.8, 49.2, "tool"),
                    Interval("a1", tool_done+.5, reload.start, "space"),
                    Interval("a1", reload.start, reload.ack, "transfer"),
                    Interval("b3", b_ready, offload.ack, "space")])
    alloc = [
        Allocation("a2", "HBM", 0, first_a, first_a, ret["a2"]),
        Allocation("a1", "HBM", 1, first_a, first_a, offload.ack),
        Allocation("b3", "HBM", 1, offload.ack, offload.ack, ret["b3"]),
        Allocation("a1", "HBM", 1, reload.start, reload.ack, ret["a1"]),
        Allocation("B", "HBM", 1, restores["B"].start, restores["B"].ack, ret["B"]),
        Allocation("B", "HBM", 2, 0, 0, b_ready),
        Allocation("b2", "HBM", 2, b_ready, b_ready, ret["b2"]),
        Allocation("A", "HBM", 3, 0, 0, b_ready),
        Allocation("b1", "HBM", 3, b_ready, b_ready, ret["b1"]),
        Allocation("A", "HBM", 3, restores["A"].start, restores["A"].ack, ret["A"]),
        *(Allocation(root, "Host", row, op.start, op.ack, ret[root])
          for row, (root, op) in enumerate(backups.items())),
        Allocation("a1", "Host", 2, offload.start, offload.ack, ret["a1"]),
    ]
    result = Schedule(actions, [*backups.values(), offload, reload, *restores.values()],
                      alloc, spawns, ret, eos)
    result.check()
    return result


def x(t):
    return LEFT + (RIGHT - LEFT) * t / 90


def label(f, x_pos, y, text, *, size=FONT_SIZE, align="middle", bold=False):
    f.label(x_pos, y, text, size, align=align, bold=bold, color=INK)


def state(f, left, right, y, kind, owner="A"):
    height = 10.3
    edge = FAMILY[owner[0].upper()][2 if owner.isupper() else 3] if kind == "run" else EDGES[kind]
    f.rect(left, y-height/2, right-left, height, WHITE, edge, 1.15,
           min(2.7, (right-left)/3), "2.15 1.35" if kind == "space" else None)
    if kind == "transfer":
        f.line(left+2.7, y+height/2-2, right-2.7, y+height/2-2, edge, .7)


def bezier(f, p0, p1, p2, p3, color, width):
    f.parts.append(f'<path d="M {p0[0]},{p0[1]} C {p1[0]},{p1[1]} '
                   f'{p2[0]},{p2[1]} {p3[0]},{p3[1]}" fill="none" '
                   f'stroke="{color}" stroke-width="{width}" stroke-linecap="round"/>')


SLOT_HEIGHT = 12
TIER_TOP = {"HBM": 132, "Host": 194}


def slot_top(a):
    return TIER_TOP[a.pool] + SLOT_HEIGHT * a.row


def release_marker(f, px, py, backed):
    if backed:
        f.polygon(((px, py-2.8), (px+2.8, py), (px, py+2.8), (px-2.8, py)),
                  WHITE, INK, .95)
    else:
        f.rect(px-2.4, py-2.4, 4.8, 4.8, INK, WHITE, .55, 0)


def draw_memory(f, s):
    for pool, rows, subtitle, paper in (("HBM", 4, "4 slots", "#f2f4f7"),
                                        ("Host", 3, "DRAM", "#f5f4f0")):
        y, height = TIER_TOP[pool], rows*SLOT_HEIGHT
        f.rect(LEFT, y, RIGHT-LEFT, height, paper, "#c0c5c8", .5, 0)
        label(f, LEFT-6, y+height/2-5, pool, align="end", bold=True)
        label(f, LEFT-6, y+height/2+5, subtitle, align="end")
    for a in s.allocations:
        y = slot_top(a)
        top, bottom = y+.4, y+SLOT_HEIGHT-.4
        fill = FAMILY[a.owner[0].upper()][0 if a.owner.isupper() else 1]
        if a.start < a.ready:
            # One editable freeform keeps the triangular arrival flush with its plateau.
            f.polygon(((x(a.start), bottom), (x(a.ready), top), (x(a.end), top),
                       (x(a.end), bottom)), fill, "none", 0)
        else:
            f.rect(x(a.start), top, x(a.end)-x(a.start), bottom-top, fill, "none", 0, 0)
        center = (x(a.ready)+x(a.end))/2
        if a.pool == "Host":
            center = x(37 if a.owner == "A" else 54 if a.owner == "B" else 56)
        label(f, center, y+SLOT_HEIGHT/2, a.owner)
    for op in s.copies:
        down = op.target == "Host"
        verb = ("Prepare" if down else "Prefetch") if op.owner.isupper() else ("Offload" if down else "Reload")
        src = s.allocation(op.owner, "HBM" if down else "Host", op.start, op.ack, ready=True)
        dst = s.allocation(op.owner, op.target, op.start, op.ack, ready=False)
        p0 = (x(op.start), slot_top(src) + (10 if down else 2))
        p3 = (x(op.ack), slot_top(dst) + (3 if down else 9))
        dy = p3[1]-p0[1]
        p1 = (p0[0]+8, p0[1]+.13*dy)
        p2 = (p3[0]-6, p3[1]-.28*dy)
        bezier(f, p0, p1, p2, p3, WHITE, 2.6)
        bezier(f, p0, p1, p2, p3, INK, 1.2)
        f.arrowhead(p2, p3, INK, 1.2, 4.2)
        offset = {"A": -4, "B": -2, "a1": 4}[op.owner] if down else 0
        label(f, (p0[0]+p3[0])/2 + offset, 237, f"{verb} {op.owner}")
    for a in s.allocations:
        if a.pool == "HBM" and a.owner in ("A", "B", "a1") and a.end == s.release(a.owner):
            release_marker(f, x(a.end), slot_top(a)+SLOT_HEIGHT/2, a.owner.isupper())


def dependency(f, points):
    f.path(points, GUIDE, .77, "2.4 1.8")
    f.arrowhead(*points[-2:], GUIDE, .77, 3.4)


def draw_work(f, s):
    lane = dict(A=43, a1=33, a2=53, B=97, b1=79, b2=97, b3=115)
    for root, kids in CHILDREN.items():
        y = lane[root]
        split, join = x(s.spawns[root]), x(s.join(root))
        label(f, LEFT-6, y, "Workflow "+root, align="end", bold=True)
        root_runs = [a for a in s.actions if a.owner == root and a.kind == "run"]
        f.line(x(root_runs[0].end), y, split, y, GUIDE, .7)
        for kid in kids:
            ky = lane[kid]
            spans = [a for a in s.actions if a.owner == kid]
            first = x(min(a.start for a in spans))
            dependency(f, ((split, y), (first-1.5, ky)))
            end = x(max(a.end for a in spans)) + .8
            # Keep long-lived dependencies clear of siblings' execution bars.
            turn = max(end, join-19)
            incoming = [(end, ky)]
            if turn > end + .2 and ky != y:
                incoming.append((turn, ky))
            incoming.append((join, y + (2.6 if ky > y else -2.6 if ky < y else 0)))
            dependency(f, incoming)
        label(f, split, lane[kids[0]]-10, "SPAWN")
        label(f, join+1, lane["a2"]+10 if root == "A" else lane[kids[0]]-10, "JOIN")
    for a in s.actions:
        y, left, right = lane[a.owner], x(a.start), x(a.end)
        state(f, left, right, y, a.kind, a.owner)
        if a.kind == "run":
            label(f, (left+right)/2, y, a.owner)
    for kid, t in s.eos.items():
        ex, ey = x(t), lane[kid]
        f.circle(ex, ey, 1.55, INK, WHITE, .45)
        label(f, ex-2, ey-9.4, "final EOS", align="end")


def draw_legend(f):
    rows = ((("run", "Inference"), ("tool", "Tool wait"), ("space", "Wait for HBM slot"),
             ("transfer", "Wait for H2D")),
            (("ramp", "Incoming KV"), ("copy", "D2H / H2D"), ("backed", "Release backed KV"),
             ("writeback", "Release at D2H ACK")))
    gap, glyph = 13, 16
    widths = [glyph+4+max(f.measure(text, FONT_SIZE)[0] for _, text in column)
              for column in zip(*rows)]
    width = sum(widths) + gap*(len(widths)-1) + 14
    start = (W-width)/2
    f.rect(start, 249, width, 35, WHITE, "#9b9fa2", .65, 2.6)
    for y, row in zip((258, 275), rows):
        left = start+7
        for (kind, text), item_width in zip(row, widths):
            if kind == "run":
                state(f, left, left+7, y, kind, "a1")
                state(f, left+9, left+16, y, kind, "b1")
            elif kind in EDGES:
                state(f, left, left+glyph, y, kind)
            elif kind == "ramp":
                f.polygon(((left, y+4.6), (left+glyph, y-4.6), (left+glyph, y+4.6)),
                          FAMILY["A"][0], "none", 0)
            elif kind == "copy":
                for dx, sign in ((0, 1), (8.4, -1)):
                    f.arrow(((left+dx, y-3.2*sign), (left+dx+6.6, y+3.2*sign)),
                            color=INK, width=.9, head=3)
            else:
                release_marker(f, left+glyph/2, y, kind == "backed")
            label(f, left+glyph+4, y, text, align="start")
            left += item_width+gap


def make_figure():
    f = Art("05_kv_migration_pipeline", "BeliefKV migration pipeline", W, H, CAPTION)
    f.family = f.pdf_sans_family = "Liberation Sans"
    label(f, 9, 9, f.title, size=11.5, bold=True, align="start")
    label(f, RIGHT-31, 9, "Time", align="end")
    f.arrow(((RIGHT-26, 9), (RIGHT, 9)), color=INK, width=.8, head=3.8)
    s = schedule()
    draw_work(f, s)
    draw_memory(f, s)
    draw_legend(f)
    return f


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    directory = parser.parse_args().output
    directory.mkdir(parents=True, exist_ok=True)
    f = make_figure()
    f.save_to(directory)
    ppt = PowerPointExporter("en", width=W, height=H)
    ppt.add_figure(f, directory)
    ppt.presentation.slides[0].notes_slide.notes_text_frame.text = CAPTION
    ppt.presentation.core_properties.title = f.title
    print(ppt.save(directory / (Path(f.filename).stem + "_editable.pptx"), expected_slides=1))


if __name__ == "__main__":
    main()
