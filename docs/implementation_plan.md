# BeliefKV Current Execution Plan

Status date: 2026-10-04.
Latest direction update: 2026-10-05.

## Mandatory Experiment Notes

Read `docs/experiment_operating_notes_zh.md` before launching workloads.
The user now authorizes **84 roots in a single arrival wave**, keeping
running=48 and the existing Host/device pool settings. This supersedes the
64-root notes below; it does not authorize 128 or a staggered 64+64 launch.
Next pair reverses v4's order to reactive first. Record realized demand and
all-workflow trace drift; a single live pair is pressure exploration, not
trajectory-controlled or statistically established policy throughput benefit.
See `docs/experiments/pressure84_and_fair_comparison_2026-10-05_zh.md`.
The user approved **64 roots** on 2026-10-04 with multi-round delegation,
selective waiting-parent PREPARE and pressure-triggered Host-only residency.
This replaces the previous 36-root diagnostic, not with 128 or 64+64.
Do not increase concurrency beyond the approved configuration to manufacture H2D
opportunities. Long workflows have an explicit **14400-second** budget,
graph limit 2048 with the accepted 32-step finalization reserve, and the
native-reactive guard profile. Check effective launch arguments, not just
defaults. Natural-language child replies remain valid.

## Active Full H2D A/B

The next development pair is v4:
`docs/experiments/joint_tool_join_h2d_v4_zh.md`.
It retains 64 roots/running=48/NUMA Host 200 GB and widens the predicted
lead window to 1000 ms. JOIN and tool-return H2D share native safe submission,
but are audited against their own actual RETURN/TOOL_END boundaries.
The calibrated tool event model is independently loaded with pinned source/model
validation, without promoting its legacy online/action eligibility metadata.
Both arms share long-wait preparation and pressure-triggered demotion; only
predictive enables pre-return loads. This is not an untouched native baseline.
Pure predictive queues start immediately while preserving native layer/fence
dependencies; size/shape-local timing replaces maximum byte-scaled ACK latency.
The CPU checks pass 218 project tests, 63 native tests and five patch tests.
GPU timing, transfer reuse and paired throughput remain to be measured.

The active pair is now
`docs/experiments/join_prepare_h2d_64root_2026-10-04_zh.md`.
Both arms share JOIN parent preparation and pressure parking; only predictive
has pre-RETURN semantic H2D. This isolates early restoration and is not an
untouched-native baseline. Seed transfer timing with verified historical ACKs,
audit actual submission relative to child RETURN, and keep work-head research
separate from the frozen deployed model during the pair.
The following 36-root paragraphs document the completed earlier comparison.

The 2026-10-04 v2 pair is terminal, with zero predictive H2D and no proven
transfer benefit. A pre-EOS candidate-window audit finds nine JOINs with
earlier restore targets but no sampled target during their final requests.
All nine suffered internal summaries attributed to the waiting parent.
Preserve child callback ancestry and model invocation scope, and keep JOIN
and foreground-call dependencies conjunctive. The related 156 CPU regressions
pass; GPU residency and actual pre-RETURN restoration remain unverified.
Next compare short-token completion CDFs on frozen semantic features, keeping
workflow-disjoint fit, selector and evaluation roles. Do not deploy the
rejected MLP or reinterpret stale EOS windows as predictive actions.

The short-token CDF comparisons at 250/100 ms produce no reliable selector
operating point and remain undeployed. Fresh acquisition now records bounded
100 ms text observations and matching Linux monotonic clock domains; only
verified domains remove the 100 ms causal progress guard. Historical traces
retain their conservative clock bracket. The combined regression suite passes
220 tests. v3 starts with a full 64-root predictive arm, retaining the deployed
phase/work weights, and requires actual opportunity/action evidence before a
matched reactive comparison. Source and runtime settings must remain frozen.

The asynchronous semantic head is now connected through bounded delivered-text
observations and native decode progress, with request/session/epoch validation.
Native final-request priority is separate from predictive H2D and is shared by
both arms. The next full development comparison is 36 roots, a fresh native server
and KV cache per arm, actual Device-matched Host split, no PREPARE_HOST, and no
canary. Reactive has neither semantic inference nor predictive transfers.
Predictive uses semantic JOIN H2D while the parent still WAIT_JOINs; known GPU
EOS is not confused with a confirmed child RETURN.

Configuration and evidence interpretation:
`docs/experiments/semantic_h2d_ab_36root_2026-09-30_zh.md`.
The 2026-10-03 restart first completes a full 36-root predictive arm with
the safe input-checkpoint fixes, durable JOIN prefetch budget and the
frozen-phase/updated-work artifact. Inspect Host sources, first-use,
eviction and recomputation before scheduling a matched reactive arm.
Do not automatically run a no-op baseline or raise root count after a
zero-opportunity result. This is a full workload, not a short gate/canary.
Do not change shared runtime code during a comparison; retain failures
and unknown Mamba attribution and measure first-use and throughput rather
than interpreting offline MAE or ACK count as benefit.

## Stage 2 Semantic And Calibration Goal

The next bounded milestone is a frozen pretrained text encoder, a task-adapted
comparison, and truly separated model-fit / score-bias-calibration /
interval-calibration / validation roles. Six existing projects are fit-only;
Astropy workflows are deterministically split into two calibration subsets;
Sphinx is model-held-out validation. These are reused historical traces,
not a newly sealed experiment. Preserve source/harness differences.

Compare raw and calibrated semantic heads, a numeric/event head, the small CNN,
and the native notice signal on identical observations. Report request-level
precision/recall, work point error, token-bound width and workflow coverage,
and full-encoder versus cached-feature CPU cost. Calibrated bounds are not
P10/P90 quantiles or precise RETURN-time predictions. Runtime still owns H2D
identity, capacity and value checks. No scoring threshold may cancel children.

The pinned plan is
`configs/migration/child_semantic_work_stage2_2026-09-30.json`; results and
scope are recorded in
`docs/experiments/child_semantic_work_stage2_2026-09-30_zh.md`.
This bounded milestone is complete: train-only adaptation reduced validation
tool false first-triggers from 29 to six at the independently calibrated
operating points, with 33/35 natural RETURNs detected. Token work error has
not consistently beaten the checkpoint prior, and calibrated intervals remain
hundreds of tokens wide. The artifact is advisory, not an online H2D timer.
Next use fresh protocol/pressure-matched projects and an asynchronous shadow
consumer; full runtime integration and physical benefit are separate work.

## Active Child Report Phase And Work Goal

The current predictor experiment is event/content driven, not a larger direct
RETURN-wall-time regressor. A bounded delivered-token sequence encoder and
causally valid completion notices classify a no-more-tools report round.
Conditional heads predict remaining output-token work, with ordered nominal
intervals. Labels use matched native output-token counts; future service gaps
and the parent's first GPU service are not model inputs or work labels.
Scheduler/worker decode intervals and no-service gaps are audited separately.
The runtime remains the authority for Host-copy validity, FULL/Mamba capacity,
transfer service, other-child state and H2D value.

The first implementation is a CPU-only, project-held-out replay on the
36-root trace, with old 32-root data allowed only on the training side and
all samples of the held project excluded. Compare notice-only,
content/progress and content/event signals at identical observed snapshots,
count tool false first-triggers, report conditional token error and nominal
interval coverage, and measure per-observation CPU cost. Scores are explicitly
uncalibrated and checkpoints are offline-only. No guard or mandatory completion
schema is added, and no model output directly authorizes a transfer.

See `docs/experiments/child_report_phase_work_2026-09-30_zh.md` for execution,
results and remaining limitations. Hidden-state extraction and a new GPU
workload are later comparisons, not prerequisites for this fast replay.
The replay is complete: the small token CNN raises natural-RETURN recall
but produces too many tool false first-triggers and undercovered token
intervals; it is not an accurate RETURN timer. Completion-notice rounds
are now a separate phase label, and class balancing did not make that phase
identifiable. Keep native notices as independent evidence, next compare
richer semantic representations and project-disjoint work calibration,
and do not connect this raw checkpoint to physical H2D decisions.

## Event-Time Labels Versus Physical Action Policy

The Qwen3.5 predictor learns when a tool group ends and when each child
RETURN occurs. A parent's first GPU service after JOIN is **not** a child
RETURN label. It does not learn whether PREPARE_HOST or PREFETCH_GPU is
profitable from reactive traces: the counterfactual transfer, freed capacity,
and interference are not observed in those traces. At each scheduler safe
point, the runtime must independently compare current FULL/Mamba residency,
valid Host copies, physical free lists, transfer service estimates, and the
opportunity cost of occupying either pool before issuing an action.

The native v4 fit now accepts verified WAIT_TOOL snapshots paired with their
external-wait trace. It fits an event-time survival curve at fixed 50-5000 ms
probe horizons, properly handling parallel tools and right censoring. This
is timing supervision, not a label saying an action had positive reward.
Child completion training continues to use member RETURN timestamps from
JOIN reentries. Held-out project calibration uses the same horizons; count
independent tool episodes/workflows, not all correlated probe points as
independent observations. The metadata remains offline and
`predictive_action_eligible=false` until runtime physical safety and paired
use/benefit have been independently verified. Do not bypass the physical
action gate just because event-time targets are nonzero.

The 2026-09-28 native fit used 128 training workflows and 34,746 eligible
WAIT_TOOL snapshots (243,222 event-horizon probes). Its child RETURN
training weighted MAE was approximately 856 seconds: the child point ETA is
not a subsecond trigger. Held-out calibration used 66 project-disjoint
workflows and 19,051 WAIT_TOOL event-timing snapshots; a 90%-target
`remaining_to_return_ms` interval needed about 533 seconds of additional
slack. This slack is an interval-calibration quantity, not a measured
held-out MAE or proof of 90% coverage on a new test set. The timing-head
`prepare_host` and `prefetch_gpu` probability scores describe complementary
tool-release horizon events, not action reward, Host-copy availability, or
first-service reuse. The calibrated artifact is
`experiments/models/qwen35_native_event_horizons_20260928_calibrated.json`;
it retains `online_eligible=false` and
`predictive_action_eligible=false`. Evaluate child RETURN timing, short
event windows, abstention, and transfer utility separately before using
predicted timing to schedule physical migrations.

The active execution order is the Qwen3.5 section below. Older planning notes
are retained at the end for traceability but are not startup instructions.
Completed and superseded plans are indexed under `docs/archive/`.

The 180 GB Host pressure scan is documented in
`docs/experiments/qwen35_native_regime_scan_2026-09-28_zh.md`.
Both ordinary write-through trials saturated the Mamba Host pool; their
Mamba eviction-to-revisit rows do not identify whether the revisited state
was recomputed. The selective 6-root trial avoided Host evictions but has
not established predictive physical transfer or official task correctness;
none is a predictive result or a matched A/B comparison. A later independent
6-root read-only probe has 6/6 native-agent JCT-eligible workflows but
`successful_workflows=0` because its self-report gate requires a structured
completion that natural-language replies do not provide. Do not silently
promote measured JCT to correct-task throughput: official patch grading
must be integrated before the paired A/B correctness claim. The same probe
identified a native NumPy float64 node timestamp rejected by the H2D
selector. Its normalized 6-root rerun completed 6/6 measurement-eligible
workflows with no FULL/Mamba Host eviction, and read-only sampling now finds
Host-backed targets: all 577 free-list-fitting snapshots concern just two
shared Mamba physical nodes (28 and 49), with zero missing FULL device
tokens. Independent SWE-bench 4.1.0 grading of its six exported patches
resolved two xarray tasks, found three unresolved pylint tasks and one
empty xarray patch, with no evaluator errors. This replaces the inference
that structured self-report failure means all six tasks were incorrect;
it does not supply enough successes for a throughput A/B claim. No
predictive H2D or first-service reuse has been established. The later
200 GB/25:75/8-root probe was stopped at full Mamba Host occupancy with
six of eight workflows complete: 54 Mamba and eight FULL Host evictions,
and no FULL H2D targets among its 995 free-list-fitting observations.
Neither is the FULL-transfer primary workload. Deduplicate sampled session
observations by physical target/epoch before estimating opportunities.

## Objective (Current Stage)

The read-only child decode-content research track is specified in
`docs/experiments/qwen35_decode_content_conditioned_plan_2026-09-28_zh.md`.
It separates (a) a no-more-tools child RETURN boundary from (b) enough
lead for the measured H2D/control service budget. The current lexical
tail-plus-length pilot is development data, not a calibrated predictive
action head: workflow-balanced, length-conditioned content helped only
one of two reused projects and hit none of three JOIN-last target windows.
Freeze and validate on genuinely new projects, with early-final and
tool-continuation false starts counted at the request level, before
considering EOS-distribution or low-overhead hidden-state signals.

**Primary goal:** On Qwen3.5/SGLang v0.5.20, identify a reproducible
workload where the required FULL/Mamba pools have genuinely free HBM for
useful transfers, NUMA-local Host pools remain stable, PCIe has useful
transfer windows, and little useful KV is evicted and later recomputed.
This is a physical operating regime, not a fixed root count or a target
HBM utilization; bounded reclamation of cold KV is a separate extension.
Root count and aggregate HBM occupancy do not qualify a workload. There
must be real future consumers: Host-backed KV absent from Device that a
subsequent request will use, and/or a native offload that can consume a
selectively prepared, possibly partial, Host shadow. Spare HBM with no
such consumer is not an opportunity.

In this regime, implement and verify selective `PREPARE_HOST` and bounded
tool-return, JOIN and pre-admission predictive H2D. PREPARE requires
observable live Device KV, Host headroom and a transfer window, not free
Device space; H2D requires capacity for the target's actual FULL/Mamba
demands. Use already-free HBM for the primary H2D experiment; evaluate
bounded displacement of demonstrably cold KV separately only where its
measured cost is below the prospective gain. Neither policy may sacrifice
useful KV to manufacture apparent opportunities. Complete
the causal chain: valid target -> physical transfer ACK -> later native
offload consumption or first GPU service reuse -> saved synchronous
transfer wait. Measure useful and wasted bytes, Host/Device residency
cost and other workflows' delay; neither ACK counts nor native cache hits
alone constitute a predictive benefit.

Compare against P5 reactive with identical tasks, arrivals and physical
configuration, reporting independently graded correct workflow throughput
and JCT. Correctness, FULL/Mamba capacity safety, liveness and bounded
tail interference are hard constraints. Evaluate tool-return and complete
JOIN timing accuracy separately from action utility: subsecond point ETA
is not a prerequisite for a bounded, physically justified opportunity,
but inaccurate predictions must remain subject to budgets and abstention.

**Out of scope for this stage:** blanket victim eviction for speculative
H2D, joint victim/beneficiary handoff, high-pressure recompute
reduction, Host-thrash tuning and SSD coordination. Preserve these as
future separately evaluated extensions. Compute-saturated or Host-thrashing
loads serve only to characterize abstention, safety and the benefit
boundary; do not increase load solely to inflate migration counts.

Freeze the primary stratum on train projects only after checking FULL and
Mamba device/Host headroom separately, usable Host-backed targets or
consumable future shadows, transfer-to-first-use lead, eviction-to-subsequent-
miss/recompute attribution, and workflow correctness. Set numerical
opportunity, low-recompute, transfer-slack and residency-cost budgets on
train projects before independent paired evaluation; never use idle HBM,
low eviction counts or root count alone as a proxy. If no stratum qualifies,
report the actionable-opportunity upper bound instead of escalating load
just to create migration events.

Success is staged: (1) establish a reproducible stratum with sufficient
free HBM for each target's FULL/Mamba requirements, stable Host pools,
low useful eviction-to-recomputation and genuinely actionable physical targets;
(2) demonstrate selective PREPARE -> later native consumption and predictive
H2D -> ACK -> first-service reuse with measurable synchronous stall saved;
(3) show a net gain against matched reactive P5 without violating correctness,
capacity, liveness or displaced-workflow tail-latency constraints. Native
transfers, read-only opportunities and early HBM residency alone do not
complete any later stage.

Stop/redirect criteria: if no train-project configuration has both usable
targets and low recomputation without Host thrash, report the opportunity
bound rather than raise load to force migrations. If physical actions occur
but do not reduce blocking or improve the matched workflow outcome after
accounting for HBM byte-time and other workflows, report that limit rather
than promote action count as success. Cold-KV replacement, speculative
victim selection, joint handoff and high-pressure eviction optimization are
future experiments, not dependencies of this stage. Keep high pressure only
as an abstention/safety boundary.

Execution priority: use a bounded training-project arrival/concurrency scan
to qualify actual missing-Device/Host-backed targets and future PREPARE
consumers; validate each physical action against first-use and saved
blocking time; only then freeze
the eligible configuration for paired reactive/predictive evaluation.
Do not classify an already-Device-resident JOIN, an ACK without first-use,
or a transfer count without a reactive counterfactual as success.

Current boundary: the 14-root write-back scan is a *candidate for stage 1*,
not proof of stage 2 or 3. Its 14 distinct H2D nodes (13 with missing FULL
Device KV) were observed in sampled snapshots. The subsequent v3 confirmed-
JOIN canary finished with 14/14 workflow measurements and no FULL/Mamba
Host eviction; six predictive H2D actions had physical ACKs, but **zero
of six reused the prefetched prefix at the first request after JOIN**.
Those JOINs followed an external initial-delegation planner: the parent
started a different prompt after the child reports, so a shared context ID
did not imply a reusable KV prefix. Bootstrap JOINs are now excluded from
parent-prefix prefetch. The naturally completed 14-root in-graph v7
had 16/16 satisfied JOINs and stable Host pools, but no actionable
Host-backed JOIN H2D target or predictive ACK; its two native H2D hits
were not predictive. The first 24-root startup v8 failed during VLM image
warmup before the client started; the corrected v9 found a Host-backed,
missing-Device tool-wait candidate, but FULL Host eviction forced the scan
to stop. Of 24 workflows, 21 completed naturally and three ended on
interruption. Node 52 also had a native H2D *before* its later candidate
window; the later post-tool H2D receipt named node 4721. Aggregate native
receipts cannot prove whether node 52 was restored as an ancestor, and
neither transfer proves predictive reuse. v9 is a
pressure/opportunity diagnostic, not a qualified stage-1 stratum or a
paired performance result. The same 24 tasks at 200 GB/35:65 write-back
(v10b) completed 24/24 naturally without either Host pool evicting, but
had only one free-list-fitting H2D snapshot (one Mamba slot, zero FULL
tokens) and no predictive ACK. Across 188 deduplicated PREPARE candidates,
no node-ID-only later native D2H match was observed; splits and ancestor
closures remain unproven rather than disproven. v10b is a stable capacity
bound, not yet an actionable predictive-benefit workload. Selective partial PREPARE
consumed by later native eviction, saved synchronous wait, independent
task correctness and matched reactive A/B
remain unverified. Keep the primary scan on free FULL/Mamba HBM capacity,
stable Host pools and low useful-KV recomputation; limited cold-KV
displacement needs separate cost evidence, while joint handoff and
high-pressure recomputation reduction remain future studies. Treat each
stage as a separate gate; do not
promote the objective on action counts or ACKs alone.

Primary metrics:

- successful workflows/hour and p50/p95/max workflow JCT, with correctness
  and bounded starvation as constraints;
- actual prepared bytes later used for a native eviction/writeback, and
  predictive H2D bytes later consumed at first GPU service;
- saved admission/reentry H2D stall, useful/wasted D2H/H2D bytes, HBM
  byte-seconds, displaced cold-KV miss and victim debt;
- eviction-to-miss/recompute as a low-rate workload qualification check and
  guardrail, not the primary claimed benefit;
- GPU service tokens/second, utilization and synchronous control overhead
  as explanations, not standalone wins.

## Active Order (Qwen3.5/v0.5.20)

1. **Sealed project-disjoint tool/JOIN evaluation: completed.**
   The `qwen35_terminal_join_sealed_20260927_v1` batch used 16
   Matplotlib/scikit-learn test roots plus 32 unrelated load-generating roots.
   The postprocessing CLI import failure was repaired without changing scoring,
   and both frozen reports are preserved. Only 5/15 matched natural JOIN
   groups passed `heavy_queue`: point-error p50 improved from 188 to 38 ms,
   but the service-window P10 overestimated 5/5, and tool-call-weighted ETA
   had no statistically supported improvement over its global prior.
   One root was incomplete. See
   `docs/experiments/qwen35_terminal_join_sealed_2026-09-27_zh.md`.
   Treat this holdout as consumed: no model/threshold selection on its
   outcomes; the next method needs new project-disjoint validation.
2. **Find and freeze the moderate-pressure, actionable-HBM regime on train projects.**
   Sweep bounded arrival concurrency and Host FULL/Mamba allocations on
   training workloads, without selecting a root count from a previous
   high-pressure experiment alone. First seek free HBM for an actual
   missing-Device/Host-backed target; assess cold/evictable-KV capacity
   separately under a measured opportunity-cost budget. PREPARE needs
   Host space and a future native offload consumer, not free HBM.
   Require valid H2D candidates or future-eviction PREPARE
   candidates, stable Host headroom in both pools and low useful
   eviction-to-miss/recompute before freezing a primary configuration.
   Reject a configuration if all apparent JOIN candidates already reside on
   Device or lack a valid Host-backed step; a low Host eviction count alone
   does not establish a useful predictive workload. Keep correctness/JCT
   eligibility separate from presence of telemetry and final natural-language
   output when judging completed diagnostic runs.
   Record GPU/PCIe utilization and reactive H2D stall: free HBM by itself
   cannot save a transfer if no future reuse exists. Characterize each load's
   request-level queue/service,
   FULL/Mamba resident vs evictable bytes, Host eviction-to-miss/recompute
   attribution, transfer identity/ACK, and critical-path blocker changes.
   Freeze the chosen regime and byte-time/slowdown budgets on training
   workloads before held-out evaluation. Separately measure idle-headroom
   prefetch and optional cold-KV replacement; do not equate free bytes with
   evictable bytes. Test high-pressure fallback where Host/compute pressure
   suppresses transfer opportunities; report abstentions and guardrail
   violations as well as successful actions. All regimes must use the same
   model, engine, Host pool, instrumented workload, admission cap and hardware.
   Existing 64/128 runs with unmatched Host configurations are not a paired
   throughput comparison. For each candidate action, log event time, online
   frontier/blockers, Host availability, reclaimable/locked FULL/Mamba bytes,
   affected physical extents, transfer ACK, first *actual* KV consumption,
   eviction-to-miss/recompute and displaced-workflow delay. Attribute
   queue/no-service time separately from H2D and GPU service. First compute
   an opportunity upper bound (recoverable KV, actionable reclaim capacity
   and enough live time to transfer); include partially prepared KV that is
   later offloaded. Do not use a reactive queue tail as counterfactual transfer
   savings. If higher load is compute-saturated or Host thrashes, quantify
   the limit rather than tune that load for a headline result.
   A train-only 128-root JOIN audit finds 0/105 FULL Host hits and 3/105
   Mamba Host hits at parent submit; 101/105 have a FULL Device prefix hit.
   These are submit-time observations, not notice-time residency or an
   actionability certificate. See
   `docs/experiments/qwen35_join_host_opportunity_train_only.md`.
   The action-local closure observer now recognizes the actual static
   FULL/Mamba allocator, and a read-only, epoch-bound session probe can
   report a candidate H2D node and instantaneous pool free-list counts.
   The admission safe point can now emit an opt-in, bounded, rotating
   `BELIEFKV_ADMISSION_OPPORTUNITY_DIR` observation stream: live native
   waiting candidates and tool/JOIN waits carry session epochs, one
   ancestor-closed H2D node, FULL/Mamba requirements and instantaneous
   free-list headroom or a rejection reason; tool waits also record an
   observable single-node PREPARE candidate with Host free lists where
   write-through and session radix are enabled. The census records sampled
   safe-point wall time and
   unsampled tail. This has CPU coverage only: GPU sampling overhead,
   representative physical opportunity rates, allocator ownership and
   transfer/first-consumption reconciliation are not yet verified. Do
   not treat `fits_current_free_lists` as a reservation or H2D permit.
   Real waiting requests are already `RUNNING_LLM` in RCCG after
   `LLM_SUBMIT`; the READY-only admission gate missed them. The bounded
   waiting-list prediction, read-only opportunity probe and pre-admission
   lease now admit that submitted state only with current request/session/
   context identity; a stale/terminal lease cannot keep deferring native
   admission. This is CPU-tested, not a claim of GPU H2D success; repeat
   the independent GPU gate with the revised server before evaluating
   next-agent handoff or promoting physical actions.
   Deep Agents now has an opt-in `--native-radix-sessions` mode requiring
   `--control-socket`. Start the server with
   `ENABLE_SESSION_RADIX_CACHE=1`, `HICACHE_WRITE_POLICY=write_through`,
   `BELIEFKV_ADMISSION_TELEMETRY_DIR=<new server dir>`,
   `--enable-beliefkv-admission` and
   `--beliefkv-event-socket-path <same socket>` before enabling the runner.
   The frozen reactive evidence path `BELIEFKV_NATIVE_TELEMETRY_DIR` remains
   admission-incompatible; never set both directories. The admission path
   records its own provenance and fans native transfer ACKs out to both the
   runtime physical ledger and telemetry writer. Absence of an ACK is still
   not evidence of useful predictive transfer.
   Runtime workflow/create and LLM submit/result events reach the control
   mirror; normal model rounds reuse the native session, while observed
   compaction rotates it. Validate real session generations, mirror discard
   counts, and sampled non-`no_bound_session` candidates in a bounded
   training-project GPU gate before a pressure sweep. Reject samples from
   degraded control delivery or opportunity-writer overflow. This wiring
   has CPU coverage but no physical transfer or utility result yet.
3. **Evaluate timing as an auxiliary signal, without blocking physical gates.**
   Test tool ETA and long-window calibration across task, project, and
   success/error strata; do not select the workflow-weighted candidate just
   because its held-out score was good (training-project LOO was worse).
   Score classifier coverage and conditional time error separately.
   Reconstruct child remaining work and service as follows:
   Partition observed RETURN time into actual request GPU service, queue/no
   service intervals and tool execution with request/epoch identity; report
   missing/censored intervals. Do not subtract all no-GPU time from the target
   (tool waits still affect RETURN) or feed future service/queue durations to
   inference. Validate work/service demand across pressure and project folds
   versus wall-clock ETA; discard if no stable improvement. Refit/calibrate
   separately for action policy and pressure as needed, with online updates
   using only past confirmed labels and drift-aware fallback. A policy may
   instead use confirmed JOIN/tool events, causal frontier and a bounded
   time-to-use interval; report full-group coverage, abstentions, false
   starts and conditional ETA error independently. Do not claim subsecond
   prediction from a handful of near-terminal groups.
   A training-project LOO ablation of landmark-conditioned remaining tool
   time improves the all-survivor median but *worsens* the same true-long
   calls versus the frozen calls head; it cannot be promoted as a tool-return
   clock. See `docs/experiments/qwen35_tool_live_remaining_train_loo_2026-09-27_zh.md`.
   The twelve projects in the existing Verified split are already allocated
   or used for development, calibration, or sealed evaluation; another
   formal unseen-project test needs an independently sourced project,
   not another rollout of the consumed test projects.
   A new SWE-bench-Live lite source is now *frozen, not evaluated*: eight
   tasks each from cfn-lint, Haystack and Reflex are disjoint from Verified,
   plus a separate one-task pvlib environment pilot. All 24 target image
   manifests exist; only the pilot source checkout and sandbox preflight
   have passed. This is not a valid new sealed result until the predictor,
   scoring, capacity, complete environment contract and arrival pressure
   are frozen independently. See
   `docs/experiments/qwen35_swebench_live_ood_preflight_2026-09-27_zh.md`.
4. **Implement bounded PREPARE and pre-admission H2D only after the
   physical observability and safety gates.** Use tool wait, JOIN
   straggler and factual frontier as signals. Prepare the ancestor-closed
   part likely to need eviction rather than backing up every waiting agent;
   measure later shadow consumption. In the primary stratum, restore Host-backed
   KV before native admission only when its destination fits actual free
   physical capacity; compare time saved with byte-time and transfer overhead.
   Study colder-KV replacement and victim future-use separately after the
   idle-headroom result; do not require replacement to produce the primary
   H2D canary. Include next-agent handoff when the same constraints
   permit it, not as a prerequisite for H2D-only gains. Reserve
   complete or useful partial ancestor-closed KV, bind request/context epoch,
   page generation and lease expiry; commit admission only on valid capacity
   and sufficient ACK. Include next-agent handoff and native Host KV, not just
   predictive PREPARE-created shadows. Keep P5 fallback for stale/no-KV
   opportunities. Verify liveness, expired tickets and no starvation. Test
   idle-capacity-only prefetch separately from critical-path cold-KV
   replacement: do not bundle their benefits into a single action count.
5. **Run staged physical validation, then matched A/B.** Shadow-log proposed
   time, bytes, alternative beneficiary and reason for abstaining. Canary
   checks intent -> physical transfer -> ACK -> parent ready -> first service
   and subsequent actual KV use; for PREPARE also observe whether the
   shadow is consumed by later eviction, not merely D2H ACK. Distinguish
   complete/partial/late/wasted bytes. For joined parent define useful lead
   `0 <= R-C <= T_max`, with
   `T_max` set from measured HBM opportunity cost rather than the old
   reactive queue tail. Count partially hidden transfers by measured stall
   saved. Matched P5 vs P6 uses same tasks/arrival order, source fingerprint,
   runtime/hardware/Host/graph48 contract and instrumentation; report workflow
   completion throughput and JCT distribution plus other-workflow slowdown,
   recompute, HBM occupancy time and guardrail violations. Include primary
   ablations for PREPARE, speculative H2D and idle-capacity use. Report
   cold-KV replacement and execution handoff only if separately implemented
   and validated; do not mix them into the primary comparison.
   Count correctness failures, censored workflows, p50/p95 JCT and maximal
   per-workflow slowdown; never trade an unbounded victim tail for a higher
   mean completion rate. If execution order is changed, include an
   execution-order-only ablation; include a transfer-only ablation to isolate
   idle-bandwidth gains from workflow prioritization. Do not compare
   different root counts as if they were a matched policy A/B.

Decision gate: if no chosen operating stratum has reusable KV (or a later
useful PREPARE shadow), sufficient lead and transfer/capacity slack, report
a workload/hardware limit rather than tuning predictive thresholds.
Do not use unqualified zero-pressure success to claim benefits from
overlapping transfers with meaningful workload execution. Retain reactive
P5 at high pressure when handoff shifts delays or raises miss/recompute.
Promotion requires physical benefit and no correctness/liveness regression;
time-model accuracy alone is insufficient. Do not enable Qwen3.5
`online_eligible` or `predictive_action_eligible` just because an offline
window gate or shadow decision looks promising. Full safety/ownership evidence
and paired GPU results remain necessary.

The sections below are older migration evidence and
**Qwen3-Coder/0.5.2rc1 P0-P5 planning snapshots**. They are not active
prerequisites for the new model; where they conflict with this section, this
section takes precedence.

## Historical Evidence Snapshot (Superseded)

The active stage is a fresh Qwen3.5-35B-A3B BF16 native-reactive collection on
SGLang v0.5.20. The 65/35 and 70/30 Host-pool allocation comparison used the same
64 root tasks. 70/30 improved FULL Host prompt-token hit share (31.8% vs. 22.0%)
and reduced uncached prompt share (4.15% vs. 4.84%), while increasing MAMBA Host
eviction count (13,387 vs. 11,268). Select 70/30 provisionally for the training
collection; this is a single-run capacity decision, not a statistical claim.

The 70/30 raw trace had 63/64 complete workflows and was rejected by the old
100%-coverage export gate. The revised collector/exporter admits at least 95%
batch trace coverage but continues to exclude malformed workflows individually;
runtime/model provenance, core event pairing, and telemetry writer health remain
fail-closed. The raw 65/35 and 70/30 traces, summaries, and capacity
calibration remain available for the Host-pool decision. Their exported
training tables are not reused; invalidated dataset manifests are retained
outside the standard training path.

The active collection is one frozen 128-root plan: 64 roots arrive at `t=0`,
the next 64 at `t=60s`, client inflight is 128, and one SGLang server uses
`MAX_RUNNING_REQUESTS=48`/graph48. LangGraph `recursion_limit=2048` is the hard
limit; a 32-step reserve starts bounded FINALIZE at approximately step 2016.
This is the only enforced graph-step guard. Semantic loop/stuck patterns and
the 384-step soft budget are telemetry-only; repeated-tool suppression,
tool circuit breaker, completion gate, and format repair are disabled.
Model-request and sandbox-command timeouts remain. The server uses the verified
180 decimal GB Host pool with a 70/30 FULL/MAMBA split on NUMA node 1 and
`mem_fraction_static=0.94`. Model-selected initial fan-out remains one to four
children, with the root allowed to spawn again after JOIN.

All earlier Qwen3.5 native-reactive training exports are invalidated because
their step/guard configuration or telemetry contract does not match this
collection. Preserve raw runtime and per-workflow traces, contracts, summaries,
and capacity calibration for audit; discard unusable derived tables and the
uncalibrated fitted artifact. The current collector requires block-level Host
eviction attribution (`eviction_attribution.jsonl`) and fails closed if the
patched SGLang TreeCore observer is unavailable.

This is training-data collection, not a predictive throughput experiment:
predictor and predictive actions remain disabled. After collection, validate
trace coverage and natural/censored JOIN labels, then fit and evaluate the
Qwen3.5 prediction heads. The earlier P0-P4 predictive-action roadmap below is
deferred until usable model data and action calibration are available.

The graph48 gate passed on the H200: the static FULL/MAMBA capacities remain
1,798,995 tokens and 513 slots, CUDA graphs cover decode batch 48, 64/64
contract-matched requests completed, and runtime reached 47 running requests
without OOM. The next collection therefore uses a hard running limit of 48.
This is an admission/queue experiment, not a claim that GPU utilization will
rise: the graph32 run already averaged about 94% GPU utilization while queued.

The observed 58% FULL usage peak is effective non-evictable usage, not physical
HBM residency. Native write-back D2H is triggered by allocator free-slot
shortfall and can occur while evictable Radix leaves remain physically resident.

The P5 correctness baseline and the bounded predictive transaction machinery
are frozen at `0ab8c09`. The latest development artifact is FrontierBelief v6:

- token-demand intervals provide useful but broad envelopes;
- PREFETCH operational timing has positive Brier skill (+15.80%) but only
  36.17% precision at the recall-oriented threshold;
- boundary, tool-terminal, and PREPARE timing do not beat their relevant
  majority or balanced-accuracy baselines;
- `online_eligible=false` and `predictive_action_eligible=false` remain set.

The runtime supports full, ancestor-closed partial, and commit-ready-victim
funded prefetch. No natural online run has yet completed the full
`intent -> H2D -> lease -> first service` attribution chain or shown throughput
gain.

Online scheduling now uses the action-minimal v1 contract. Calibrated decode and
next-output demand may reorder an observed seed; boundary and tool-terminal
classifiers are diagnostic only. Missing demand support preserves observed order.
Transfer actions continue to require live-tau survival, beneficiary, capacity,
physical-envelope, and net-benefit validation.

## P0: Freeze The Current Correctness Baseline (Complete)

Use:

- Qwen3-Coder-30B-A3B-Instruct BF16;
- SGLang 0.5.2rc1 at the pinned upstream commit;
- `configs/p6/h200_bf16_v7/frozen_runtime_profile.json`;
- 850K-token GPU KV pool and 96 GiB Host KV pool;
- native `2to3` subagent workload;
- performance-mode instrumentation shared by every arm.

The ownership/retraction gates have established the required invariants:

- allocator/Radix/engine ownership remains consistent;
- all transfer commands reach terminal ACK;
- no orphan transaction, lease, reservation, or restore obligation;
- workflows are not terminated by an artificial activation cutoff;
- ordinary waiting requests do not create a global restore barrier.

Do not reopen this stage unless a later action violates an invariant.

## P1: Freeze Predictor Permissions (Complete)

Only the following heads may influence action value:

- prompt growth and remaining decode as calibrated demand intervals;
- PREFETCH operational-tau probability;
- RCCG-observed child/JOIN dependencies.

Boundary rare classes, tool error/censor classification, and PREPARE timing
must not independently reorder execution or authorize a physical action. Exact
incremental boundary remains unavailable, so early dispatch and run-to-action
are out of scope.

Keep v6 development-only. Do not open `test_id` until the action path and
evaluation protocol are frozen.

## P2: Freeze An Active-KV Pressure Workload

The current 40-root workload sustains a waiting backlog but only reached about
31% native KV usage in the latest bounded run. Increasing root count alone does
not help because `max_running_requests=32` leaves additional roots outside the
resident working set.

Freeze a long-context workload that makes the 32 active contexts naturally
accumulate a larger unique KV working set. It must preserve native parent-child
continuation and fixed root arrival; do not shrink the KV pool or condition
release on runtime pressure.

Characterization stops after one of:

- three fresh and timely positive packages;
- 32 closure-complete candidates;
- five minutes above the frozen HBM pressure threshold without a positive.

Report active-context KV usage separately from waiting queue length.

## P3: Bounded Predictive Action Canary

Use the P2 workload and allow at most one in-flight predictive transaction.
The accepted action may be `PREPARE_HOST`, full/partial `PREFETCH_GPU`, or
`RECLAIM_AND_PREFETCH`, but must satisfy:

- action-specific prediction support;
- beneficiary or reentry identity and epoch;
- live closure/capacity certificate;
- validation before latest-start;
- PREPARE transfer completion before block;
- victim reclaim bytes covering the certified deficit;
- positive net value after transfer, interference, Host residency, and restore
  debt.

Verify the full applicable chain, including ACK, service lease, first service,
and any reverse migration. Do not treat a published or safe-point-rejected
intent as a useful action.

## P4: Matched Throughput A/B

After a useful natural P3 canary, run:

- A: P5 observed JointPlan, predictor off;
- B: identical P5 plus P6 predictive PREPARE.

Both arms must use the same frozen workload selection, model, runtime profile,
timeout policy, instrumentation, and artifact keys.

Report:

- workflows/hour and completed workflow count;
- GPU service tokens/second and utilization;
- HBM/Host occupancy over time;
- admission/reentry stalls;
- D2H/H2D bytes and transfer interference;
- useful, wasted, late, stale, and censored PREPARE outcomes;
- beneficiary first-service latency.

A run with high HBM but no beneficiary/reentry-bound opportunity is
characterization, not a negative or positive performance result. Report model
quality separately from action utilization and throughput.

## P4.5: Deferred Dynamic Running Target

Do not change the runtime profile used by the current frozen P3/P4 experiment.
After the PREFETCH transaction, shutdown, and attribution gates pass, evaluate
a fixed physical limit with a runtime-selected soft target:

- start SGLang with `max_running_requests=48` and CUDA Graph coverage through
  batch 48;
- keep initialized request/token pools fixed for the lifetime of the server;
- let JointPlan choose a soft running target from `{32, 48}`;
- expand toward 48 only when GPU-ready backlog exists and the projected HBM
  envelope (resident KV, startup demand, calibrated growth, restore/funding
  obligations, and safety margin) remains feasible;
- on projected HBM pressure, stop new admission and drain naturally toward 32;
- do not retract a running request merely because a pressure threshold was
  crossed. Reclaim parked KV only through a beneficiary-bound causal package,
  and retract running work only when its measured value exceeds transfer and
  restore debt;
- use hysteresis and a minimum wall-time hold to prevent 32/48 oscillation.

This is a secondary throughput optimization, not an explanation for the
current P6 prediction-to-action gap. In the frozen baseline trace,
`running >= 32 && waiting > 0` occupied about 11.40 minutes (4.89% of active
time), including about 5.75 minutes below 70% HBM pressure. The measurable
opportunity is therefore bounded and should be established by matched A/B,
not assumed from the larger hard limit.

Use four matched arms under the same hard-48/graph-48 runtime contract:

1. fixed soft target 32, predictor off;
2. fixed soft target 48, predictor off;
3. dynamic soft target 32/48, predictor off;
4. dynamic soft target 32/48 with P6 prediction enabled.

The fixed-32 arm must still pay the graph-48 memory cost. Report saturated
time, batch-size distribution, prefill/decode tokens per second, action/tool
start throughput, HBM occupancy, transfer/retraction churn, and beneficiary
first-service latency. Promote the optimization only when throughput improves
without OOM, restore-liveness regression, or repeated target oscillation.

## P5: Decide The Prediction Branch

Continue the current P6 branch only when at least one of the following holds:

- PREPARE removes measurable later D2H/admission stall;
- the same causal information changes victim or execution-package choice;
- repeated traces expose reactive commits that start too late.

Otherwise, simplify P6 to a low-cost shadow observer and revisit workload or
the action space before adding more model complexity.

## Optional Future Branch: Predictive Eviction
只有当以下两件事有明确的收益时才考虑增加predictive eviction：
beneficiary 到达时无需等待 victim selection 和 commit。
提前释放 HBM 后，可以更早 admission 更多 GPU-ready 请求，提高 batch size 和利用率。

This branch is not on the immediate critical path.

## Near-Term Host Semantic Eviction

Host high-watermark cleanup must release semantic garbage before reusable replicas:

1. dead `CPU_ONLY` KV first, without requiring raw-prompt replay;
2. dead `DUAL_CLEAN` second;
3. native-writeback shadow before explicit/predictive shadow;
4. live `CPU_ONLY` only when replay is guaranteed and all owners are safely parked;
5. active restore obligations, prefetch service leases, semantic pins, and non-parked owners stay
   protected.

The implementation tracks `host_copy_source` so native writeback pollution is visible and can be
reclaimed before predicted future-use copies. Report cleanup bytes by mode, forced recompute,
Host miss, predictive H2D success, and reverse migration.

## Optional Future Branches: Global Value Model And SSD Tier

Do not put a global KV value model into the online JointPlan yet. It couples execution ordering,
HBM victim selection, Host cleanup, and reentry prediction, and can reintroduce a high-overhead
global optimizer. Evaluate it only after semantic Host eviction is stable, using shadow decisions
and paired replay against the bounded policy.

Do not add an SSD tier yet. SSD is a possible cold/parked-KV layer when Host forced eviction is
proven to discard reusable KV, but it introduces asynchronous I/O, durable metadata, staging
buffers, and another eviction policy. A future design must stage through Host (`SSD -> Host ->
GPU`), keep KV extents append-only, and preserve active leases. It must not block the current P6
correctness and throughput gates.

Current P6 predicts `PREPARE_HOST`, which copies KV to Host without releasing
HBM. A future branch may add `PREDICTIVE_COMMIT_CPU` after the shadow is
complete:

```text
PREPARE_HOST
 -> CPU shadow ready
 -> projected beneficiary HBM deficit
 -> latest-safe-time risk check
 -> PREDICTIVE_COMMIT_CPU
 -> HBM released before reactive blocking
```

The commit must require:

- victim still parked and physically movable;
- calibrated wait-open probability above the frozen threshold;
- concrete or projected beneficiary;
- expected saved stall greater than restore/recompute debt, transfer
  interference, and risk margin;
- fresh RCCG and physical certificates.

Evaluation must compare predictive commit against reactive commit, not against
no KV management. Required metrics are early-released HBM-time, saved
beneficiary stall, wasted eviction bytes, reverse H2D, recompute debt, and
workflows/hour.

## Deferred Work

The following tasks must not block P2-P4:

- Oracle action-space expansion;
- morphology as an independent policy;
- peer multi-agent-specific optimization;
- another SGLang version migration;
- broad baseline emulation requiring a predefined DAG;
- additional model heads without an observed action-space failure.

## Documentation Rule

- Current design belongs in `beliefkv_design.md`.
- Current implementation evidence belongs in `architecture_status_zh.md`.
- One experiment produces one immutable report under `docs/experiments/`.
- Completed or superseded plans move to `docs/archive/plans/`.
- Do not append chronological experiment logs to the current status page.
