# BeliefKV Current Execution Plan

Status date: 2026-09-23.

This file contains only the active execution order. Completed and superseded
plans are indexed under `docs/archive/`.

## Objective

Obtain the first defensible end-to-end result for predictive Agent/KV joint
scheduling on the H200 BF16 system without expanding the mechanism surface.

Primary metrics:

- successful workflows/hour;
- GPU service tokens/second and utilization;
- useful action-unlock rate;
- admission and reentry stall;
- useful/wasted D2H/H2D bytes;
- synchronous control-plane overhead.

## Current Evidence

The active migration target is Qwen3.5-35B-A3B BF16 on SGLang v0.5.20.
The latest Qwen3.5 native reactive collection attempt completed its first
64-root shard in 951.9 seconds, with 21/64 workflows passing the correctness and
measurement gates. It emitted only two children from one root and one eligible
JOIN label; 14 roots hit the 512-step safety fuse. This is diagnostic evidence,
not a sufficient JOIN training collection.

The first overlapped 128-root attempt (`qwen35-overlap-v1`) was cancelled after
its runner started because both graph and LangGraph limits were frozen at 512.
Its partial trace/telemetry is retained under
`experiments/raw/qwen35_native_reactive_overlapped_128root_train_20260923_v1/`;
it has no complete dataset export and must not be used for training. The 580
recreatable root/child workspace checkouts were removed (about 119 GiB freed);
142 MiB of server telemetry, manifests, and request traces remain. SGLang and
the two residual containers were stopped, and the GPU was released.

The replacement uses a 2048-step graph hard fuse and a matching LangGraph
`recursion_limit`, retaining a 32-step finalization reserve and the 384-step
observe-only soft threshold. This follows the Qwen3 long-run setting: 2048
allowed execution beyond 512, while a separate repeated-empty-command loop
still reached the fuse. A brief v2 server startup was stopped before workflow
collection for disk cleanup. The clean run uses tmux session
`qwen35-overlap-v3` and a distinct `_v3` raw directory; verify its runtime
contract reports both limits as 2048 before treating output as the new collection.

The 128-root replacement is one frozen collection, not two sequential service
runs. It merges the two disjoint 64-root train shards, submits roots 0-63 at
`t=0` and roots 64-127 at `t=60s`, and keeps one SGLang instance and telemetry
stream throughout. Client concurrency is 128; server running/graph capacity
remains 48. All task images are prepared before service startup.

The earlier `native_dynamic_1to4` profile was prompt-only, so the model could
ignore the requested initial SPAWN. The next source revision makes the model
choose a structured one-to-four-task initial delegation plan, validates it at
runtime, launches those read-only children concurrently, then resumes the root
with native task middleware available for subsequent rounds. The selected
fan-out remains model-dependent rather than fixed at two.

The graph hard fuse is separate from the observe-only semantic and soft guards.
The replacement collection uses 2048 for both the loop-guard cap and LangGraph
`recursion_limit`, reserving the final 32 steps for bounded completion. Keep
the fuse and repeated-call circuit breaker, and censor post-intervention
full-episode/JOIN labels; do not treat a hard-finalized workflow as a natural
terminal sample.

The first action gate after startup is to verify one 128-client collector against
one server, the second 64-root arrival at `t=60s`, and model-selected initial
fan-out counts in the runtime traces. Do not fit on the earlier sequential shard.

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
- SGLang version migration;
- broad baseline emulation requiring a predefined DAG;
- additional model heads without an observed action-space failure.

## Documentation Rule

- Current design belongs in `beliefkv_design.md`.
- Current implementation evidence belongs in `architecture_status_zh.md`.
- One experiment produces one immutable report under `docs/experiments/`.
- Completed or superseded plans move to `docs/archive/plans/`.
- Do not append chronological experiment logs to the current status page.
