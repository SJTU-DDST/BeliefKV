# BeliefKV Current Execution Plan

Status date: 2026-09-15.

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

## P0: Freeze The Current Correctness Baseline

Use:

- Qwen3-Coder-30B-A3B-Instruct BF16;
- SGLang 0.5.2rc1 at the pinned upstream commit;
- `configs/p6/h200_bf16_v7/frozen_runtime_profile.json`;
- 850K-token GPU KV pool and 96 GiB Host KV pool;
- native `2to3` subagent workload;
- performance-mode instrumentation shared by every arm.

Run one high-pressure predictor-off gate after the latest ownership/retraction
repairs. Required conditions:

- allocator/Radix/engine ownership remains consistent;
- all transfer commands reach terminal ACK;
- no orphan transaction, lease, reservation, or restore obligation;
- workflows are not terminated by an artificial activation cutoff;
- ordinary waiting requests do not create a global restore barrier.

Do not tune prediction or migration thresholds during this gate.

## P1: Measure Prediction Overhead And Opportunity

Run the same frozen workload with the predictive worker enabled but physical
predictive actions disabled.

Measure:

- safe-point capture and submit P50/P95/P99;
- worker planning latency, backlog, stale rate, and trigger count;
- deferred beneficiary classification;
- projected HBM deficit;
- fresh/timely/positive package count;
- predictor OOD and required-head availability.

The run is useful even when it produces zero actions: it decides whether the
limitation is workload opportunity, prediction quality, plan freshness, or
physical feasibility.

## P2: Single PREPARE_HOST Canary

Enable exactly one natural `PREPARE_HOST` only after P1 observes a package that
is simultaneously:

- beneficiary-bound;
- fresh at validation;
- positive under measured transfer and GPU service artifacts;
- complete before latest-start;
- supported by the live PhysicalBundle shape.

Verify:

```text
PredictiveIntent
 -> safe-point rematerialization
 -> SHADOW_CONTEXT queue
 -> D2H dispatch
 -> ACK
 -> GPU_AND_CPU_SHADOW
 -> later reclaim/admission outcome
 -> beneficiary GPU service or censored terminal
```

Do not lower the benefit threshold or inject a synthetic beneficiary to force
this performance result. The existing deterministic mechanism gate is already
sufficient for mechanism correctness.

## P3: Matched Throughput A/B

After a useful natural canary, run:

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

A run with high HBM but no beneficiary-bound opportunity is characterization,
not a negative or positive performance result.

## P4: Decide The Prediction Branch

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

The following tasks must not block P0-P3:

- Oracle action-space expansion;
- morphology as an independent policy;
- peer multi-agent-specific optimization;
- SGLang version migration;
- broad baseline emulation requiring a predefined DAG;
- additional model heads without an observed action-space failure.

## Documentation Rule

- Current design belongs in `beliefkv_design_2026-07-14_zh.md`.
- Current implementation evidence belongs in `architecture_status_zh.md`.
- One experiment produces one immutable report under `docs/experiments/`.
- Completed or superseded plans move to `docs/archive/plans/`.
- Do not append chronological experiment logs to the current status page.
