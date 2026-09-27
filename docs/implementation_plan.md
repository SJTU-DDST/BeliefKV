# BeliefKV Current Execution Plan

Status date: 2026-09-28.

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
selector; its normalized rerun is in progress. Early rerun samples show
feasible Host-backed Mamba targets (mostly shared physical nodes 28 and 49),
not physical H2D or first-service reuse. Deduplicate sampled session
observations by physical target/epoch before estimating opportunities.

## Objective

Establish a defensible Qwen3.5/SGLang v0.5.20 result for selective predictive
Host backup and GPU restoration in a workload where HBM has **free space or
safely reclaimable cold KV**, Host/PCIe have transfer capacity, useful KV
loss followed by recomputation is rare, and some future consumers actually
need Host-backed KV or benefit from an early Host shadow. This is a
workload-qualification gate, not a predefined root count or occupancy
threshold. Start with idle-HBM opportunities; treat cold-KV replacement as
a separately measured extension. Use the same tasks, arrivals and physical
configuration for a paired P5 reactive baseline. The primary question is
whether PREPARE_HOST and predictive H2D save synchronous transfer wait and
improve successful workflow throughput/JCT *after* accounting for wasted
transfers, HBM residency and interference. A session already on Device is
not an H2D opportunity. High-pressure, compute-bound or Host-thrashing
regimes are fallback and limitation tests, not the primary optimization
target. Separate time accuracy, physical utility and workflow outcomes;
exact wall-clock RETURN/JOIN ETA is not a prerequisite for a bounded
physical-opportunity experiment.

Freeze the primary stratum on train projects only after checking FULL and
Mamba headroom separately, valid Host-backed targets or consumable future
shadows, transfer-to-first-use lead, eviction-to-subsequent-miss/recompute
attribution, and workflow correctness. Set any numerical opportunity,
recompute and residency-cost budgets on those train projects before an
independent paired evaluation; do not infer actionability from idle HBM,
low eviction counts or a chosen root count alone. If no stratum qualifies,
report the actionable-opportunity upper bound instead of escalating load
just to create migration events.

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
2. **Find and freeze the actionable-HBM regime on train projects.**
   Sweep bounded arrival concurrency on training workloads, without selecting
   a root count from a previous high-pressure experiment alone. Require
   observed free HBM or revalidatable cold/evictable KV, Host-backed H2D
   candidates or future-eviction PREPARE candidates, and low useful
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
   prefetch and cold-KV replacement; do not equate free bytes with
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
   measure later shadow consumption. Restore Host-backed KV before native
   admission when its destination is available or can replace strictly colder
   evictable KV; compare time saved with victim future-use, byte-time and
   transfer overhead. Include next-agent handoff when the same constraints
   permit it, not as a prerequisite for H2D-only gains.
   Parent can replace cold KV, not engine-locked or hotter work. Reserve
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
   recompute, HBM occupancy time and guardrail violations. Include ablations
   for PREPARE, speculative H2D, idle-capacity use, cold-KV replacement and
   execution handoff.
   Count correctness failures, censored workflows, p50/p95 JCT and maximal
   per-workflow slowdown; never trade an unbounded victim tail for a higher
   mean completion rate. Include an execution-order-only ablation to isolate
   recomputation reduction from H2D gains, and a transfer-only ablation to
   isolate idle-bandwidth gains from workflow prioritization. Do not compare
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
