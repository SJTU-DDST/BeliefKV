# BeliefKV Current Execution Plan

Status date: 2026-09-17.

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
