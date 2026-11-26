# Archived ICML figures with tool-wait offload

This snapshot predates the lifecycle-aware incremental backup figure. The [current paper figures](../../../figures/icml/README.md) include the revised overview and three mechanisms. The generator alongside this archive preserves the drawing source at that revision.

The overview spans two columns (6.75 in). Each mechanism figure fits one
column (3.25 in). The vector PDFs use embedded Type 1C text, with the minus
and inequality operators drawn as vector outlines. Captions belong below
the graphics. [ICML 2027 example paper](https://media.icml.cc/Conferences/ICML2027/styles/example_paper.pdf)

| Figure | Paper PDF | Editable slide | SVG | PNG |
| --- | --- | --- | --- | --- |
| System overview | [PDF](01_overview_en.pdf) | [PPTX](01_overview_en_editable.pptx) | [SVG](01_overview_en.svg) | [PNG](01_overview_en.png) |
| D3: Pressure-aware first KV materialization | [PDF](02_first_kv_materialization_en.pdf) | [PPTX](02_first_kv_materialization_en_editable.pptx) | [SVG](02_first_kv_materialization_en.svg) | [PNG](02_first_kv_materialization_en.png) |
| Demand-driven tool-wait KV offload | [PDF](03_tool_aware_offload_en.pdf) | [PPTX](03_tool_aware_offload_en_editable.pptx) | [SVG](03_tool_aware_offload_en.svg) | [PNG](03_tool_aware_offload_en.png) |
| JOIN-aware parent KV prefetch | [PDF](04_join_parent_prefetch_en.pdf) | [PPTX](04_join_parent_prefetch_en_editable.pptx) | [SVG](04_join_parent_prefetch_en.svg) | [PNG](04_join_parent_prefetch_en.png) |

[All four PDFs](beliefkv_icml_figures.pdf) · [Combined editable deck](beliefkv_icml_editable.pptx)

## Captions

**System overview.** BeliefKV consumes observed workflow events and SGLang's
live capacity and slot state. D3 sends first-KV permits to native admission.
The other two policies request incremental backing, release, and prefetch
through the shared cache. Dashed arrows carry control signals. Solid copper
arrows carry KV pages. A spawns a1 and a2. B arrives as an independent root.
[System design](../../../beliefkv_design.md)

**Pressure-aware first KV materialization.** Free Host \(F_h\), safely
reclaimable Host \(R_h\), and free HBM \(F_d\) fund the common budget
\(K\). First deduct growth and demand reserves \(G\) and unallocated
commitments \(U\). Live Host-only KV stays outside \(R_h\). Among fitting
candidates, CALL or ALL-JOIN blockers precede other children and then new
roots in arrival order. In the example a1 charges three of five units.
c1 stays pending and B charges the final two. Checkmarks indicate permits
and filled slots show budget charges. A permit that fails the live recheck
returns to hold. Existing continuations use native admission. A progress
exception can permit an attempt when no existing work can advance.
[D3 design](../../../design/subagent_materialization_pressure_20261116_zh.md)
· [Budget and ordering](../../../../beliefkv/runtime/subagent_materialization.py)

**Demand-driven tool-wait KV offload.** A ready B has a live HBM deficit.
Compare a1's conditional remaining-wait survival at \(C\) with \(p_{\min}\).
The cost \(C\) includes the missing donor backup, any required consumer load,
the donor reload, and margins \(m\). In this example page 1 is already backed,
so only page 2 crosses to Host. ACK confirms the copy. The runtime rechecks
live demand, tool wait, and native page safety before returning capacity to
the allocator. B can then go through native admission.
[Tool offload implementation](../../../../beliefkv/runtime/predicted_tool_offload.py)

**JOIN-aware parent KV prefetch.** After a2 returns, only a1 remains
outstanding in A's observed ALL-JOIN. Its final no-tool EOS can trigger H2D
for missing pages 3 and 4 from the safe Host checkpoint, which can be backed
in advance with `PREPARE_HOST`. The dark part of the transfer precedes JOIN.
The timeline shows separate RETURN, JOIN, and submission milestones. In this
example H2D finishes after submission and A starts when native data and slot
requirements are satisfied. Page counts and intervals are illustrative.
[JOIN selection and dispatch](../../../../beliefkv/runtime/sglang_v0520_runtime.py)
· [Physical recovery](../../../../beliefkv/runtime/join_physical_recovery.py)

## Related Work

- **First materialization.** MARS gates root admission using KV utilization.
  Children inherit root admission and go through in-engine scheduling with
  elastic prefill sizing. D3 charges both root and child first allocations
  against the same Host and HBM budget. [MARS §§4–5](https://arxiv.org/html/2604.26963v3)
- **Tool waits.** ThunderAgent discounts acting-phase cache weight as a tool
  wait progresses. The illustrated policy uses a specific ready request's
  deficit, the donor's conditional remaining wait, and the transfers needed in
  that case. [ThunderAgent §4.3](https://arxiv.org/html/2602.13692v3)
- **Parent restores.** HiCache matches prefixes when processing an arriving
  request. KVFlow prefetches using an Agent Step Graph. The illustrated
  trigger uses observed ALL-JOIN membership and the last outstanding
  child's final EOS. [HiCache design](https://docs.sglang.io/docs/advanced_features/hicache_design)
  · [KVFlow §3](https://arxiv.org/html/2507.07400)

## Regeneration

Run from the repository root with CairoSVG, Pillow, python-pptx, lxml, the
Liberation and Nimbus fonts, and `pdfunite` available. The PowerPoint files
reuse the vector geometry as editable shapes, connectors, and text.

```bash
python docs/figures/generate_icml_figures.py
```

The prior system overview, D1/D2 figures, compact D2 and comparison, their
palette, and source provenance are in the [two-design archive](../beliefkv_two_designs_20261120/README.md).
The October monthly report keeps its original figures.
