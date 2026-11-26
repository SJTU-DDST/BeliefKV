#!/usr/bin/env python3
"""Draw the compact D2 example against demand-driven KV transfers."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from pptx.enum.dml import MSO_PATTERN_TYPE

from generate_monthly_paper_figures import PowerPointExporter
from generate_two_design_paper_figures import (
    C,
    D2_OWNERS,
    GRAY,
    INK,
    OUTPUT,
    PAPER,
    CompactFigure,
)


A_SPAWN = 263
A_CHILDREN = 292
TOOL_START = 403
TOOL_DONE = 670
A2_RETURN = 902
B_SPAWN = 468
COPY_DURATION = 60
RELEASE_DELAY = 12
ADMISSION_DELAY = 12
A_RESUME_WORK = 116
B_CHILD_WORK = (445, 568)
B_RESUME_WORK = 108
WIDTH = 1290
HEIGHT = 622
FONT_SIZE = 20
CHILD_PATTERNS = {
    "a1": MSO_PATTERN_TYPE.DOTTED_DIAMOND,
    "a2": MSO_PATTERN_TYPE.DOWNWARD_DIAGONAL,
    "b1": MSO_PATTERN_TYPE.DOTTED_DIAMOND,
    "b2": MSO_PATTERN_TYPE.DOWNWARD_DIAGONAL,
    "b3": MSO_PATTERN_TYPE.DASHED_HORIZONTAL,
}

# Vector equivalents of the PowerPoint presets for SVG, PDF, and PNG exports.
PATTERN_TILES = {
    MSO_PATTERN_TYPE.DOTTED_DIAMOND: (
        16, '<path d="M0 0L16 16 M0 16L16 0" fill="none" stroke="{ink}" '
        'stroke-width="1.8" stroke-dasharray="1.5 4.2"/>',
    ),
    MSO_PATTERN_TYPE.DOWNWARD_DIAGONAL: (
        9, '<path d="M-1 -1L10 10 M-1 8L1 10 M8 -1L10 1" '
        'fill="none" stroke="{ink}" stroke-width="1.45"/>',
    ),
    MSO_PATTERN_TYPE.DASHED_HORIZONTAL: (
        8, '<path d="M0 4H8" fill="none" stroke="{ink}" stroke-width="1.45" '
        'stroke-dasharray="5.5 2.5"/>',
    ),
}

NOTES = (
    "Both panels illustrate the same A/B workload, tool-ready time, model work, "
    "HBM budget, victim pages, and copy durations. The on-demand policy backs "
    "up A when B's children need space and loads A after JOIN. Both policies "
    "can write back a1 to admit b3. BeliefKV backs up A while its children "
    "execute and uses a1's predicted remaining tool wait plus b3's live "
    "capacity deficit to select a1. The D2H ACK and native safety checks "
    "precede HBM release. b3's retirement makes room for a1's demand "
    "reload. a2's retired pages leave room for A's prefetch. a1's final EOS "
    "starts A's copy ahead of the actual RETURN that satisfies ALL-JOIN. "
    "A resumes after JOIN, transfer completion and native readiness. B joins "
    "after all three of its children return. Its parent resumes from its "
    "resident prefix and completes. The horizontal DMA extent shows copy time. "
    "Down arrows indicate HBM to host, up arrows indicate host to HBM, and "
    "the darker part of JOIN prefetch precedes JOIN. Pale gold on execution "
    "tracks marks waiting for KV. Squares mark workflow completion. Durations "
    "and offsets are schematic."
)


def pattern_colors(owner):
    background, edge, _ = D2_OWNERS[owner]
    foreground = "#" + "".join(
        f"{round((int(background[i:i + 2], 16) + int(edge[i:i + 2], 16)) / 2):02x}"
        for i in (1, 3, 5)
    )
    return foreground, background


def pattern_definition(owner):
    foreground, background = pattern_colors(owner)
    repeat, marks = PATTERN_TILES[CHILD_PATTERNS[owner]]
    return (
        f'<pattern id="child-{owner}" patternUnits="userSpaceOnUse" '
        f'width="{repeat}" height="{repeat}">'
        f'<rect width="{repeat}" height="{repeat}" fill="{background}"/>'
        f'{marks.format(ink=foreground)}</pattern>'
    )


class ComparisonPowerPointExporter(PowerPointExporter):
    def style(self, shape, attributes):
        owner = attributes.get("data-child-pattern")
        if owner is None:
            super().style(shape, attributes)
            return
        foreground, background = pattern_colors(owner)
        super().style(shape, {**attributes, "fill": background})
        shape.fill.patterned()
        shape.fill.pattern = CHILD_PATTERNS[owner]
        shape.fill.fore_color.rgb = self.rgb(foreground)
        shape.fill.back_color.rgb = self.rgb(background)
        shape.name = f"{owner} service"


@dataclass(frozen=True)
class Timeline:
    parent_backup: tuple[int, int]
    b1_service: tuple[int, int]
    b2_service: tuple[int, int]
    tool_offload: tuple[int, int]
    b3_service: tuple[int, int]
    b_join: int
    b_resume: int
    b_done: int
    tool_restore: tuple[int, int]
    a1_final_service: tuple[int, int]
    a_join: int
    parent_restore: tuple[int, int]
    a_resume: int
    a_done: int


def schedule(*, predictive):
    backup_start = A_CHILDREN if predictive else B_SPAWN
    backup_ack = backup_start + COPY_DURATION
    b_children_start = max(B_SPAWN, backup_ack) + ADMISSION_DELAY
    b1_return, b2_return = (b_children_start + work for work in B_CHILD_WORK)

    # Both policies can use the same donor. A's earlier backup shifts admission.
    offload_start = b_children_start + 24
    offload_ack = offload_start + COPY_DURATION
    b3_start = offload_ack + ADMISSION_DELAY
    b3_return = b3_start + 150
    b_join = max(b1_return, b2_return, b3_return) + 16
    b_resume = b_join + ADMISSION_DELAY

    restore_start = max(TOOL_DONE, b3_return + RELEASE_DELAY)
    restore_ack = restore_start + COPY_DURATION
    a1_final_start = restore_ack + ADMISSION_DELAY
    final_eos = a1_final_start + 126
    a_join = max(A2_RETURN, final_eos + 48)
    parent_restore_start = max(final_eos + 13, A2_RETURN + RELEASE_DELAY) if predictive else a_join
    parent_restore_ack = parent_restore_start + COPY_DURATION
    a_resume = max(a_join, parent_restore_ack) + ADMISSION_DELAY
    return Timeline(
        parent_backup=(backup_start, backup_ack),
        b1_service=(b_children_start, b1_return),
        b2_service=(b_children_start, b2_return),
        tool_offload=(offload_start, offload_ack),
        b3_service=(b3_start, b3_return),
        b_join=b_join,
        b_resume=b_resume,
        b_done=b_resume + B_RESUME_WORK,
        tool_restore=(restore_start, restore_ack),
        a1_final_service=(a1_final_start, final_eos),
        a_join=a_join,
        parent_restore=(parent_restore_start, parent_restore_ack),
        a_resume=a_resume,
        a_done=a_resume + A_RESUME_WORK,
    )


def d2_comparison():
    figure = CompactFigure(
        "03_execution_kv_turnover_comparison", "KV turnover: Naive and BeliefKV",
        HEIGHT, NOTES, width=WIDTH,
    )
    figure.family = "Liberation Sans"
    control = C["d2_trigger"]
    dma_edge, gold, pale_gold = (C[key] for key in ("d2_dma_edge", "d2_copy_fill", "d2_copy_tail"))
    end_defs = figure.parts.index("</defs>")
    figure.parts[end_defs:end_defs] = [
        figure.flow_marker("dependency", INK, "triangle", 11),
        figure.flow_marker("trigger", control, "open", 13),
        *(pattern_definition(owner) for owner in CHILD_PATTERNS),
    ]
    left, origin, right = 22, 162, 1268
    bar_height, copy_height = 22, 22

    def arrow(points, *, trigger=False):
        kind, size = ("open", 13) if trigger else ("triangle", 11)
        marker = f"{'trigger' if trigger else 'dependency'}-{size}"
        figure.path(points, control if trigger else INK, 2.7,
                    "6 5" if trigger else None, marker)
        figure.parts[-1] = figure.parts[-1].replace(
            "<path ", f'<path data-arrow-kind="{kind}" data-arrow-size="{size}" ', 1,
        )

    def bar(owner, start, end, y, label="", *, waiting=False):
        fill, edge, idle = D2_OWNERS[owner]
        patterned = owner in CHILD_PATTERNS and not waiting
        color = f"url(#child-{owner})" if patterned else idle if waiting else fill
        figure.rect(start, y - bar_height / 2, end - start, bar_height, color,
                    edge, 1.5, 2, "6 4" if waiting else None)
        if patterned:
            figure.parts[-1] = figure.parts[-1].replace(
                "<rect ", f'<rect data-child-pattern="{owner}" ', 1,
            )
        if label:
            color = PAPER if owner in ("A", "B") else INK
            figure.text((start + end) / 2, y + 7, label, FONT_SIZE, color,
                        400 if waiting else 700, anchor="middle")

    def kv_wait(start, end, y):
        if start < end:
            figure.rect(start, y - 8, end - start, 16, pale_gold, dma_edge, 1.3, 2, "4 4")

    def copy_block(start, ack, y, direction, *, join=None):
        half = copy_height / 2
        figure.rect(start, y - half, ack - start, copy_height,
                    pale_gold if join is not None else gold, dma_edge, 1.6, 2)
        figure.parts[-1] = figure.parts[-1].replace(
            "<rect ", f'<rect data-dma-direction="{direction}" ', 1,
        )
        if join is not None:
            figure.rect(start + 1, y - half + 1, join - start - 1, copy_height - 2,
                        gold, "none", 0, 0)
            figure.line(join, y - half, join, y + half, dma_edge, 1.4)
        center = (start + ack) / 2
        sign = 1 if direction == "D2H" else -1
        figure.polygon(
            tuple((center + dx, y + sign * dy) for dx, dy in
                  ((-1.7, -7.5), (1.7, -7.5), (1.7, .5), (5.8, .5),
                   (0, 7.5), (-5.8, .5), (-1.7, .5))),
            dma_edge, "none", 0,
        )
        figure.parts[-1] = figure.parts[-1].replace(
            "<polygon ", f'<polygon data-dma-arrow="{direction}" ', 1,
        )

    def event(x, y, color=INK):
        figure.circle(x, y, 4.3, color, PAPER, 1.2)

    def done(x, y):
        figure.rect(x - 4.5, y - 4.5, 9, 9, INK, PAPER, 1.2, 0)

    def join_dependencies(returns, x, parent_y):
        for return_x, y in returns:
            figure.line(return_x, y, x, y, INK, 1.8)
        arrow(((x, returns[-1][1]), (x, parent_y + 9)))

    def join_marker(x, y):
        figure.diamond(x, y, 18, 18, PAPER, INK)
        figure.label(x, y - 16, "JOIN", FONT_SIZE, True)

    def panel(top, timing, *, predictive):
        name = "BeliefKV" if predictive else "Naive (on demand)"
        figure.text(left, top + 29, name, 26, INK, 700)
        a_y, a1_y, a2_y = top + 56, top + 83, top + 110
        b_y, b1_y, b2_y, b3_y = top + 145, top + 172, top + 199, top + 226
        dma_y = top + 264
        for y in (a_y, a1_y, a2_y, b_y, b1_y, b2_y, b3_y):
            figure.line(origin, y, right, y, GRAY, 1.2)
        figure.path(((origin, dma_y), (right, dma_y)), GRAY, 1.5,
                    arrow="inactive-12" if predictive else None)
        if predictive:
            figure.parts[-1] = figure.parts[-1].replace(
                "<path ", '<path data-arrow-kind="open" data-arrow-size="12" ', 1,
            )
        for text, y in (("Workflow A", a1_y), ("Workflow B", b1_y), ("DMA", dma_y)):
            figure.text(left, y + 7, text, FONT_SIZE, INK, 700)

        final_start, eos = timing.a1_final_service
        b1_start, b1_return = timing.b1_service
        b2_start, b2_return = timing.b2_service
        b3_start, b3_return = timing.b3_service
        restore_start, restore_ack = timing.parent_restore
        backup_start, backup_ack = timing.parent_backup
        a_join, b_join = timing.a_join, timing.b_join
        for x, guide_y in (
            (backup_start if predictive else backup_ack, a2_y + 11 if predictive else b2_y + 8),
            (timing.tool_offload[1], b3_y),
            (timing.tool_restore[0], b3_y),
            (a_join, a2_y),
        ):
            figure.line(x, guide_y, x, dma_y - copy_height / 2, GRAY, 1.3, "4 5")
        if predictive:
            arrow(((eos, a1_y + 5), (eos, a1_y + 13), (restore_start, a1_y + 13),
                   (restore_start, dma_y - copy_height / 2)), trigger=True)

        figure.line(A_SPAWN, a_y, A_SPAWN, a2_y, INK, 1.9)
        figure.line(B_SPAWN, b_y, B_SPAWN, b3_y, INK, 1.9)
        for y in (a1_y, a2_y):
            arrow(((A_SPAWN, y), (A_CHILDREN, y)))
        a_returns = ((a_join, a1_y), (A2_RETURN, a2_y))
        b_returns = ((b1_return, b1_y), (b2_return, b2_y), (b3_return, b3_y))
        join_dependencies(((A2_RETURN, a2_y),), a_join, a_y)
        join_dependencies(b_returns, b_join, b_y)
        figure.line(eos, a1_y, a_join, a1_y, INK, 1.8, "4 4")

        kv_wait(a_join, restore_ack, a_y)
        kv_wait(TOOL_DONE, timing.tool_restore[1], a1_y)
        for start, y in ((b1_start, b1_y), (b2_start, b2_y)):
            kv_wait(B_SPAWN, backup_ack, y)
            arrow(((max(B_SPAWN, backup_ack), y), (start, y)))
        figure.line(B_SPAWN, b3_y, b3_start, b3_y, INK, 1.7, "4 4")

        bar("A", origin, A_SPAWN, a_y, "A")
        bar("A", timing.a_resume, timing.a_done, a_y, "A")
        bar("a1", A_CHILDREN, TOOL_START, a1_y, "a1")
        bar("a1", TOOL_START, TOOL_DONE, a1_y, "Tool wait", waiting=True)
        bar("a1", final_start, eos, a1_y)
        bar("a2", A_CHILDREN, A2_RETURN, a2_y, "a2")
        bar("B", origin, B_SPAWN, b_y, "B")
        bar("B", timing.b_resume, timing.b_done, b_y, "B")
        bar("b1", b1_start, b1_return, b1_y, "b1")
        bar("b2", b2_start, b2_return, b2_y, "b2")
        bar("b3", b3_start, b3_return, b3_y, "b3")

        for x, y in ((A_SPAWN, a_y), (B_SPAWN, b_y), *a_returns, *b_returns):
            event(x, y)
        event(eos, a1_y, control)
        join_marker(a_join, a_y)
        join_marker(b_join, b_y)
        done(timing.a_done, a_y)
        done(timing.b_done, b_y)
        if predictive:
            figure.text(eos - 8, a1_y + 7, "Final EOS", FONT_SIZE, anchor="end")

        copies = (
            (timing.parent_backup, "D2H", "PREPARE_HOST" if predictive else "A"),
            (timing.tool_offload, "D2H", "Tool-aware offload" if predictive else "a1"),
            (timing.tool_restore, "H2D", "a1"),
            (timing.parent_restore, "H2D", "JOIN prefetch" if predictive else "A"),
        )
        for (start, ack), direction, label in copies:
            copy_block(start, ack, dma_y, direction, join=a_join if start < a_join < ack else None)
            figure.label((start + ack) / 2, dma_y + 31, label, FONT_SIZE, True)

    figure.label(B_SPAWN, 28, "B spawns", FONT_SIZE, True)
    figure.line(B_SPAWN, 36, B_SPAWN, 573, GRAY, 1.3, "4 5")
    for x, direction in ((625, "D2H"), (721, "H2D")):
        copy_block(x, x + 28, 21, direction)
        figure.text(x + 37, 28, direction, FONT_SIZE)
    kv_wait(817, 845, 21)
    figure.text(854, 28, "KV wait", FONT_SIZE)
    done(941, 21)
    figure.text(964, 28, "Done", FONT_SIZE)
    figure.line(left, 308, right, 308, GRAY, 1.2)
    panel(0, schedule(predictive=False), predictive=False)
    panel(309, schedule(predictive=True), predictive=True)
    figure.text(right, 604, "Schematic time", FONT_SIZE, anchor="end", italic=True)
    return figure


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    figure = d2_comparison()
    figure.save_to(args.output)
    exporter = ComparisonPowerPointExporter("en", width=figure.width, height=figure.height)
    exporter.add_figure(figure, args.output)
    exporter.presentation.slides[0].notes_slide.notes_text_frame.text = figure.notes
    exporter.presentation.core_properties.subject = figure.title
    path = args.output / figure.filename.replace(".svg", "_editable.pptx")
    exporter.save(path, expected_slides=1)
    print(path)


if __name__ == "__main__":
    main()
