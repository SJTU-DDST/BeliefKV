# BeliefKV paper figures

The November 27 manuscript groups its figures around parent KV migration,
first-prefill admission, and segmented KV migration. The review artwork
below illustrates earlier groupings. The segmented migration figure should
trace the demand restore, the GPU write-back it triggers, and the reuse of
host pages after the corresponding H2D acknowledgment. The historical main
experiments used `a7b698e2`.

**ARCO system overview under review.** [PNG](review/arco_system_overview_en.png) · [Editable PowerPoint](review/arco_system_overview_en_editable.pptx) · [PDF](review/arco_system_overview_en.pdf) · [SVG](review/arco_system_overview_en.svg). The slide uses [Comic Neue](../assets/comic_neue/). Regenerate it with `python docs/figures/generate_arco_system_overview.py`.

[Four-page paper PDF](beliefkv_icml_figures.pdf) · [Editable PowerPoint deck](beliefkv_icml_editable.pptx)

The overview is 6.75 inches wide. Each mechanism fits a 3.25-inch column.
PDFs contain vector graphics and embedded fonts. The slides contain editable text
and drawing objects. PNGs are exported at 600 dpi.

| Figure | Paper PDF | Editable slide | SVG | PNG |
| --- | --- | --- | --- | --- |
| MigraGate system overview | [PDF](01_overview_en.pdf) | [PPTX](01_overview_en_editable.pptx) | [SVG](01_overview_en.svg) | [PNG](01_overview_en.png) |
| Pressure-aware first KV materialization | [PDF](02_first_kv_materialization_en.pdf) | [PPTX](02_first_kv_materialization_en_editable.pptx) | [SVG](02_first_kv_materialization_en.svg) | [PNG](02_first_kv_materialization_en.png) |
| Lifecycle-aware incremental backup and on-demand offload | [PDF](03_incremental_backup_offload_en.pdf) | [PPTX](03_incremental_backup_offload_en_editable.pptx) | [SVG](03_incremental_backup_offload_en.svg) | [PNG](03_incremental_backup_offload_en.png) |
| JOIN-aware parent KV recovery | [PDF](04_join_parent_prefetch_en.pdf) | [PPTX](04_join_parent_prefetch_en_editable.pptx) | [SVG](04_join_parent_prefetch_en.svg) | [PNG](04_join_parent_prefetch_en.png) |

**Standalone BeliefKV migration pipeline.** A and B arrive together and share four HBM slots.
A spawns first. Dashed arrows connect each root to its children and back to the root at JOIN.
The triangular leading edge of a KV block shows the amount received during a transfer.
The destination slot is reserved throughout. Both parents start `PREPARE_HOST` at SPAWN.
When B's children need HBM, A and B release their backed slots immediately (open diamonds).
Long-waiting a1 releases its slot at D2H ACK (filled square). Short tool waits keep their KV
resident. On tool return a1 first waits for a slot and then waits for H2D into that slot.
The valid final EOS of a1 triggers A's prefetch, and that of b2 triggers B's prefetch.
In this example each H2D completes before the parent's actual ALL-JOIN. Time and capacity are
schematic. Regenerate with
`python docs/figures/generate_icml_kv_pipeline.py`.
[Editable PowerPoint](05_kv_migration_pipeline_en_editable.pptx) · [PDF](05_kv_migration_pipeline_en.pdf) · [SVG](05_kv_migration_pipeline_en.svg) · [PNG](05_kv_migration_pipeline_en.png)

## Paper captions

**Overview.** P spawns c1 and c2 as another root R arrives. The modified SGLang scheduler coordinates three decisions. At SPAWN it prepares parent extents 3 and 4, which have no valid Host copy. Only a later deficit calls for releasing backed HBM pages. After c1 returns, c2's valid final EOS opens the prefetch window for those same extents. P submits after actual ALL-JOIN and runs after native checks. During c1's tool wait the scheduler can use its remaining-wait forecast to free space for a ready peer. When new agents compete for first prefill, the scheduler protects live Host-only prefixes, reserves growth space, and favors fitting dependency blockers. The bottom rows are four snapshots of P's same prefix. [Design and implementation boundaries](../../design/three_design_synthesis_20261123_zh.md)

**Tool forecast inset.** TraceLab v0.0.1 (SyFI Lab, CC BY 4.0) provides 54 training sessions and 17 held-out sessions. At one second, 58 WebFetch and 28 WebSearch calls are still waiting. Solid and dashed curves give the observed fraction and mean predicted probability of waiting longer than each additional horizon. The replay compares point forecasts on these 86 calls. The conditional median has 5.41 s MAE and the per-tool EWMA (alpha 0.3, no user hint) has 6.02 s MAE. This comparison measures remaining-wait error. [Replay script](../replay_migragate_tool_curves.py) · [Figure data](../assets/migragate_tool_survival.json)

**First materialization.** Three free Host units, two safely reclaimable Host units and three free HBM units provide eight units of effective capacity. Live Host-only prefixes stay protected. Existing growth, demand reserves and commitments claim three units. A child blocking its parent's JOIN takes three of the remaining five. A new root requiring four waits. The runtime rechecks a permit before native first prefill. Capacity units are illustrative. [Capacity and candidate ordering](../../../beliefkv/runtime/subagent_materialization.py)

**Incremental backup and on-demand offload.** At SPAWN or WAIT_JOIN, parent P has a stable four-extent checkpoint. Host already holds extents 1 and 2, so only 3 and 4 cross to Host. At ACK, all four remain in HBM. Later, a ready request needs space. After checking ownership, references, locks and in-flight transfers, the runtime can release P's backed tail without another D2H. Each column is a snapshot of the same HBM and Host pools. Dashed cells denote absent extents. [Backup and release conditions](../../design/three_design_synthesis_20261123_zh.md)

**JOIN recovery.** Starting from the previous figure's final checkpoint, c1 has returned and c2 is the last outstanding child. Its valid final EOS makes P's missing extents 3 and 4 eligible for H2D. The two rows use the same transfer duration, parent submission time and native service conditions. Earlier H2D can overlap return processing. Parent service in both rows requires the actual JOIN, a submitted request, sufficient data and a native slot. Intervals are schematic. [EOS trigger](../../../beliefkv/runtime/sglang_v0520_runtime.py) · [Physical recovery](../../../beliefkv/runtime/join_physical_recovery.py)

## Drawing references

- The overview uses the layered request and feedback flows of *MARS*, Figure 4, and the single pair of tiers with annotated movements in *MORI*, Figure 1. [MARS](https://arxiv.org/html/2604.26963v3) · [MORI](https://arxiv.org/html/2606.00866v2)
- The workflow strip and repeated agent and KV glyphs follow the visual hierarchy of *LatentMAS*, Figure 3. [Latent Collaboration in Multi-Agent Systems](https://arxiv.org/html/2511.20639v2)
- The dependency and page glyphs use the visual grouping in the supplied *MMGraph-Agent*, Figure 3 reference.
- The fixed positions of numbered extents across snapshots follow the visual comparison in *Monarch*, Figure 1. [ICML paper](https://proceedings.mlr.press/v162/dao22a/dao22a.pdf)
- The aligned schedules in the recovery figure draw on *FlexGen*, Figures 2 and 3. [ICML paper](https://proceedings.mlr.press/v202/sheng23a/sheng23a.pdf)
- The same visual vocabulary carries across panels, as in *DeCAF*, Figure 1. [ICML paper](https://proceedings.mlr.press/v32/donahue14.pdf)
- The editable drawing and visual review workflows were informed by [Academic Diagram](https://github.com/plbbl/academic-diagram-skill) and the `scientific-slides` skill.

## Regeneration and history

From the repository root, run `python docs/figures/generate_icml_figures.py`.
Dependencies include CairoSVG, Pillow, python-pptx, lxml, Liberation and Nimbus
fonts, and `pdfunite`. The combined deck uses a common slide size. Each standalone
PPTX has the aspect ratio of its paper figure.

[Previous ICML figures emphasizing tool-wait offload](../../archive/presentations/icml_tool_wait_20261123/README.md) · [Earlier two-design figures](../../archive/presentations/beliefkv_two_designs_20261120/README.md)
