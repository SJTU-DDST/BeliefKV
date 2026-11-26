#!/usr/bin/env python3
"""Export a compact D2 timeline as standalone English paper figures."""

from __future__ import annotations

import argparse
from pathlib import Path

from generate_monthly_paper_figures import PowerPointExporter
from generate_two_design_paper_figures import (
    C,
    D2_OWNERS,
    GRAY,
    INK,
    OUTPUT,
    PAPER,
    CompactFigure,
    d2_texture,
)


NOTES = (
    "Schematic FULL KV example with two concurrent workflows on one HBM pool. "
    "A backs its stable private pages while a1 and a2 run. B spawns later. "
    "Reclaiming A's already backed HBM pages admits b1 and b2. The remaining "
    "deficit for b3 and a1's predicted long tool wait make a1 a donor. "
    "After D2H completes and native safety checks pass, a1's HBM pages are "
    "released and b3 starts. The tool finishes during b3's service. Retiring "
    "b3 frees capacity for a1's demand H2D. a2 retires before A's prefetch. "
    "Final EOS from a1 triggers A's H2D before a1 RETURN satisfies JOIN. "
    "A resumes after JOIN and native readiness while b1 and b2 continue. "
    "Solid bars show GPU service and dashed bars show waiting. Gold arrows "
    "span copy start to ACK. Dark gold marks the portion of A's prefetch "
    "before JOIN. Transfer durations are schematic."
)


def d2_schematic():
    figure = CompactFigure(
        "03_execution_kv_turnover_compact", "D2 KV turnover across workflows",
        383, NOTES, width=1380,
    )
    figure.family = "Liberation Sans"
    end_defs = figure.parts.index("</defs>")
    control = C["d2_trigger"]
    figure.parts[end_defs:end_defs] = [
        figure.flow_marker("dependency", INK, "triangle", 12),
        figure.flow_marker("control", control, "open", 14),
    ]

    left, origin, right = 27, 171, 1353
    a_spawn, a_start, prepare_ack = 258, 290, 339
    tool_start, b_spawn, b_ready = 395, 471, 511
    offload_start, offload_ack, tool_done = 588, 637, 700
    b3_return, b3_release, restore_ack = 773, 791, 840
    a2_return, eos = 961, 1046
    prefetch_start, join, prefetch_ack, a_resume = 1071, 1101, 1120, 1152
    a_y, a1_y, a2_y = 46, 107, 153
    b_y, b3_y, dma_y = 216, 262, 326
    dma_edge, gold = C["d2_dma_edge"], C["d2_copy_fill"]

    def arrow(points, *, trigger=False):
        kind = "open" if trigger else "triangle"
        size = 14 if trigger else 12
        marker = f"{'control' if trigger else 'dependency'}-{size}"
        figure.path(points, control if trigger else INK, 3, "6 5" if trigger else None, marker)
        figure.parts[-1] = figure.parts[-1].replace(
            "<path ", f'<path data-arrow-kind="{kind}" data-arrow-size="{size}" ', 1,
        )

    def bar(owner, start, end, y, text="", *, wait=False, continues=False):
        fill, edge, idle = D2_OWNERS[owner]
        height = 36
        figure.rect(start, y - height / 2, end - start, height, idle if wait else fill,
                    edge, 2, 4, "7 5" if wait else None)
        x = (start + end) / 2
        is_root = owner in ("A", "B")
        if not wait:
            d2_texture(figure, start, y - height / 2, end - start, height, owner,
                       f"{owner}-{start}", label=(text, x, 22, 700) if text else None,
                       strong=True, column_spacing=1.55)
        if text:
            figure.text(x, y + 8, text, 22, PAPER if is_root else INK, 700, anchor="middle")
        if continues:
            figure.path(((end - 22, y - 6), (end - 14, y), (end - 22, y + 6)), edge, 2.5)

    def copy(start, ack, label, *, crossing=None):
        half_height = 13
        shoulder = ack - 16
        figure.polygon((
            (start, dma_y - half_height), (shoulder, dma_y - half_height),
            (shoulder, dma_y - 18), (ack, dma_y), (shoulder, dma_y + 18),
            (shoulder, dma_y + half_height), (start, dma_y + half_height),
        ), gold if crossing is None else C["d2_copy_tail"], dma_edge, 2)
        if crossing is not None:
            figure.rect(start + 1, dma_y - 12, crossing - start - 1, 24,
                        gold, "none", 0, 0)
            figure.line(crossing, dma_y - 15, crossing, dma_y + 15, dma_edge, 1.8)
        figure.label((start + ack) / 2, dma_y + 45, label, 21, True)

    figure.text(left, a1_y + 8, "Workflow A", 23, INK, 700)
    figure.text(left, b_y + 8, "Workflow B", 23, INK, 700)
    figure.text(left, dma_y + 8, "DMA", 23, INK, 700)
    for y in (a_y, a1_y, a2_y, b_y, b3_y):
        figure.line(origin, y, right, y, GRAY, 1.7)
    figure.path(((origin, dma_y), (right, dma_y)), GRAY, 2, arrow="inactive-12")
    figure.parts[-1] = figure.parts[-1].replace(
        "<path ", '<path data-arrow-kind="open" data-arrow-size="12" ', 1,
    )
    figure.line(a_start, a2_y + 19, a_start, dma_y - 13, GRAY, 1.8, "4 6")
    figure.line(offload_ack, b3_y + 19, offload_ack, dma_y - 2, GRAY, 1.8, "4 6")
    figure.line(b3_release, b3_y, b3_release, dma_y - 13, GRAY, 1.8, "4 6")
    figure.line(join, a2_y, join, dma_y - 13, GRAY, 1.8, "4 6")
    arrow(((eos, a1_y + 9), (eos, a1_y + 26), (prefetch_start, a1_y + 26),
           (prefetch_start, dma_y - 14)), trigger=True)

    branch = a_spawn + 12
    figure.path(((a_spawn, a_y), (branch, a_y + 12), (branch, a2_y)), INK, 2.6)
    for y in (a1_y, a2_y):
        arrow(((branch, y), (a_start, y)))
    figure.line(a2_return, a2_y, join, a2_y, INK, 2.3)
    arrow(((join, a2_y), (join, a_y + 12)))
    figure.line(eos, a1_y, join, a1_y, INK, 2.2, "5 5")

    bar("A", origin, a_spawn, a_y, "A")
    bar("A", a_resume, right, a_y, "A")
    figure.circle(a_spawn, a_y, 5.5, INK, PAPER, 1.5)
    figure.label(a_spawn, a_y - 23, "SPAWN", 21, True)
    figure.diamond(join, a_y, 24, 24, PAPER, INK)
    figure.label(join, a_y - 23, "JOIN", 21, True)

    bar("a1", a_start, tool_start, a1_y, "a1")
    bar("a1", tool_start, tool_done, a1_y, "Tool wait", wait=True)
    bar("a1", restore_ack, eos, a1_y)
    bar("a2", a_start, a2_return, a2_y, "a2")
    figure.circle(eos, a1_y, 5.5, control, PAPER, 1.5)
    figure.label(eos - 9, a1_y - 26, "Final EOS", 21, True)
    figure.circle(join, a1_y, 5.5, INK, PAPER, 1.5)
    figure.circle(a2_return, a2_y, 5.5, INK, PAPER, 1.5)

    arrow(((b_spawn, b_y), (b_ready, b_y)))
    figure.path(((b_spawn, b_y), (b_spawn + 13, b3_y), (offload_ack, b3_y)),
                INK, 2, "5 6")
    bar("B", origin, b_spawn, b_y, "B")
    bar("b1+b2", b_ready, right, b_y, "b1 + b2", continues=True)
    bar("b3", offload_ack, b3_return, b3_y, "b3")
    figure.circle(b_spawn, b_y, 5.5, INK, PAPER, 1.5)

    copy(a_start, prepare_ack, "PREPARE_HOST")
    copy(offload_start, offload_ack, "Offload a1")
    copy(b3_release, restore_ack, "Load a1")
    copy(prefetch_start, prefetch_ack, "JOIN prefetch", crossing=join)

    figure.text(right, dma_y + 45, "Time", 21, anchor="end", italic=True)
    return figure


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    figure = d2_schematic()
    figure.save_to(args.output)
    exporter = PowerPointExporter("en", width=figure.width, height=figure.height)
    exporter.add_figure(figure, args.output)
    exporter.presentation.slides[0].notes_slide.notes_text_frame.text = figure.notes
    exporter.presentation.core_properties.subject = figure.title
    deck = figure.filename.replace(".svg", "_editable.pptx")
    exporter.save(args.output / deck, expected_slides=1)
    print(args.output / deck)


if __name__ == "__main__":
    main()
