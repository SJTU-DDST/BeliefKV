# BeliefKV Current Execution Plan

Status date: 2026-10-06.

This is the active plan, not a chronological log. The previous detailed plan
is available at `c219604:docs/implementation_plan.md`; older snapshots remain
under `docs/archive/`. Current design, implementation evidence and execution
constraints must agree across `beliefkv_design.md`, `architecture_status_zh.md`
and `experiment_operating_notes_zh.md`.

## 1. Current Objective

On Qwen3.5-35B-A3B / SGLang 0.5.20, validate selective PREPARE_HOST and
JOIN/tool-return predictive H2D using real recoverable Host-only safe inputs.
Model heads predict phase, remaining work or external completion time;
runtime decides actions from identity, capacity, service and opportunity cost.
Do not train an offline net-benefit head or promote old eligibility metadata.

The user authorizes 84 roots in one arrival wave. Running stays 48, Host
stays 200 GB on NUMA node 1, and FULL/Mamba pool settings stay fixed.
Do not change to 128, staggered 64+64 or artificial eviction.
More traffic is not inherently more useful traffic: report first-use,
exposed restore waiting, advance residency, Host churn and recomputation.

The single v5 development pair is complete. The coherent-time/soft-residency
fix passed 225 related CPU tests; the single v6 pair has started with the same
84-root configuration, reactive then predictive. No extra repetitions are queued.
Multi-pair averaging is
a later formal-experiment requirement, not a reason to slow development.
Fixed-demand GPU replay and deterministic-kernel migration are not required
or active mainline tasks.

## 2. Frozen v5 Development Pair

- Status: complete; reactive 84 completed, predictive 83 completed/1 incomplete.
- Directory: `experiments/raw/qwen35_joint_wait_h2d_ab_84root_20261005_v5`.
- Code at launch: `c219604`, including runtime fixes `1aba1be`.
- Order: reactive, then predictive_h2d; no queued repetitions.
- Same manifest first 84 tasks, single arrival, temperature 0, seed 21.
- Context 131072, completion 8192, workflow 14400 seconds.
- Graph 2048, accepted 32-step finalization reserve.
- Native-reactive guard profile, natural-language child/root returns.
- Both arms share completion notice/final-report control and priority,
  waiting-state preparation and real-pressure demotion.
- Only predictive enables JOIN and tool-return predictive loads.
- Predicted lead window: 1000 ms; actual submission lead is audited separately.
- Fresh server, cache and per-workflow workspace in each arm.

This isolates early restoration over a shared residency policy; it is not
an untouched native baseline or a complete old-algorithm ablation.
Predictive also supplies forecast-dependent stage state and CPU processing;
the pair is not a DMA-only counterfactual.
Do not edit runtime, prompts, weights, kernels or launch arguments mid-pair.
Documentation-only maintenance must leave the runtime source fingerprint
and experiment launch record unchanged.

## 3. Immediate Work

Prioritize the observed v5 failure before another pressure experiment:

1. CPU implementation complete: reconcile tool long-wait parking with imminent H2D selection. All six tool
   prefetches were ACKed, pressure-demoted again and reactively reloaded.
   At issue, recorded forecasts satisfy both opposing policies. Do not add
   a second CDF veto; use one conditional time/residency interpretation.
2. CPU implementation complete: a past P50 clipped to zero is not a completion
   signal. P50 is now inverted from the same surviving CDF used for parking;
   unsupported tails remain unknown, not zero.
3. CPU implementation complete, GPU pending: coordinate post-ACK short residency with pressure candidates and true
   next service or explicit cancellation. Bound FULL/Mamba bytes/time, keep
   native capacity authority, and do not allow an obsolete target to block
   runnable requests. This is KV lifecycle coordination, not an Agent guard.
4. Keep phase/encoder weights fixed; improve the conditional work head only
   after target reuse remains valid. v5 had zero pre-EOS predictive loads.
5. Identify exposed restore waiting and repair block attribution overflow
   before calling 84 roots a low-useful-recompute formal workload. Both Host
   pools reached capacity despite aggregate FULL hits above 95%.
6. Preserve single-pair negative results and realized work. No repeated queue,
   concurrency escalation, new canary, blanket guard or automatic retraining.
7. Finished-workspace cleanup is already complete for 84/83 tasks; retain
   pytest-7324's incomplete workspace, trace and patch.

The user requested continuation after inspection. Runtime implementation and
related CPU regression are complete for items 1-3; GPU benefit is not proven.
The soft lease excludes BeliefKV pressure parking but does not pin native
allocations. Native eviction explicitly cancels it; do not claim guaranteed reuse.
Only one v6 pair is planned, same model/prompt/weights/pools/running/budgets.
Read-only v5 replay has 525 sampled restore targets, one near/fit intersection
under the new timing policy, and zero opposing decisions. No action-reward
model, eligibility promotion or extra Agent guard is introduced.
v6 directory: `experiments/raw/qwen35_joint_wait_h2d_ab_84root_20261006_v6`.
Launch commit: `573f32c`. Runtime fingerprint:
`c65caec44ecc934cd5cff9d740ec96f19459f48527505c85927bd4ae969fd6b9`.
Freeze runtime/model/prompt/parameters now; documentation-only updates are separate.

## 4. Latest v5 Findings

Completed throughput is 68.75/62.59 workflows per hour (reactive/predictive),
so predictive is -8.95%. Paired completed mean JCT is 1929.83/1759.18 seconds
(-8.84%), but LLM/tool/input demand also differs; no causal speedup claim.
GPU utilization is 76.50/81.66%, not the v4 low-utilization pattern. The
predictive tail with only two workflows lasts about 899 seconds and remains
busy; high utilization alone does not imply high batched throughput.

17 tagged H2D ACKs total 1.298 GB: 11 JOIN loads all reuse FULL, while six tool
loads do not. All JOIN submits follow native EOS; six are within 1 second of
RETURN. Tool submits precede TOOL_END by 3.70-7.11 seconds, then are demoted
94-1652 ms after ACK. 38 pressure-demotion events have prior PREPARE ACKs on
32 nodes, including loops, so consumption exists but is not a net-benefit result.
Both arms have 113 children and joins with no child cancellation; multi-round
roots number 19/15, every round still has one child.
The sole incomplete root loops in its last 8192-token, length-ended response;
it is not a format guard, expired deadline or old graph=512 configuration.

See `docs/experiments/joint_tool_join_h2d_v5_84root_zh.md` and the existing
pair's comparison/window files. Serving/ledger/writer failures were absent.

## 5. Known v4 Findings

Both arms completed 64/64. Predictive/Reactive duration was 4219.81/3032.21
seconds, mean JCT 1614.74/1341.92 seconds, completed throughput
54.60/75.98 workflows per hour. No independent task grading was performed.
Predictive produced 6 tagged H2D ACKs, all FULL first-use verified; 5 Mamba
forward uses verified. Tool H2D remained zero despite 12 tool-wait demotions.

Do not explain the negative result only as random model-path variation:
NVML accounting identifies a roughly 896-second last-workflow tail on
django-16938, including two 600-second whole-Django-suite commands.
There is also a smaller utilization gap during nonempty serving demand.
See the existing v4 report and `experiments/analysis/v4_gpu_root_cause_20261005.json`.
Scheduler/worker service intervals are not CUDA kernel time.

Already implemented after v4:

- The configured tool P50 window is no longer vetoed by CDF >= 0.8.
- Timing-only hint acceptance defers expensive physical ancestry inspection.
- Fast and unchanged long waits do not submit at every decode tick.
- Physical opportunity reads are briefly cached but revalidated before enqueue.
- A completed tool load does not drain overlap again for already restored pages.
- Pre-EOS JOIN decisions use the existing remaining-work upper bound.

These CPU-tested fixes are included in v5, not retrospectively in v4.
v5 confirms deferred hint inspection and immediate submission. Tool loads
are now issued, but conflict with parking; pre-EOS JOIN loads remain absent.
This does not independently prove throughput benefit from those fixes.

## 6. Other Next Changes

Prioritize evidenced bottlenecks rather than another blind pressure increase:

1. Explicit, truthful timeout feedback distinguishing configured tool timeout,
   host timeout and unknown process failure. Preserve normal long tools.
2. Pipeline upstream failure propagation instead of reporting a failing Python
   test as success because `tail` returned 0. Validate shell compatibility.
3. Low-overhead scheduler-stage/profile spans for model-result handling,
   ancestry inspection, packet processing and native transfer preparation.
   Use matched nonempty-demand phases and account for instrumentation overhead.
4. Tool timing/features only if the candidate audit proves residual-time
   failure. Known command deadline is observable information, not the label
   of a natural early tool completion.
5. Work-head improvements only on workflow-separated data, keeping the
   deployed phase encoder stable; do not deploy rejected candidates.

Do not retrospectively change completed v5 or reintroduce repeat/format guards.
Do not reduce every tool's timeout simply to improve the makespan metric.

## 7. Formal Evaluation Later

Run multiple independent live pairs using the same task set and settings;
alternate arm order, retain all failures/censors and show every pair plus
mean and variation. The unit for whole-run throughput uncertainty is a run
or pair, not all workflows sharing that run's GPU.
Do not require fixed-demand replay, do not discard divergent trajectories,
and do not divide JCT by realized token counts as a posthoc correction.
Completion curves, mean/median/tail JCT and independently graded correctness
should accompany makespan throughput.

Before final testing, freeze workload stratum from actual recoverable targets,
FULL/Mamba capacity and useful eviction/recompute evidence, not root count alone.
Complete algorithm/native baseline/prepare/early-load ablations must be named
explicitly; their controls are not interchangeable.

## 8. Deferred Scope

- Full legacy COMMIT_CPU/JointPlan/selective running retraction migration.
- Joint victim/beneficiary handoff and critical-path hot-page preemption.
- Host value models, SSD tiering, KV FP8 and another engine/model migration.
- Online model updates without stable timing/physical validation.
- Hidden-state probes without demonstrated low extraction cost.
- GPU fixed-demand replay as a non-required optional research branch.

## 9. Documentation Rule

Update current design/status/plan/operating constraints together when changing
mainline direction. Experiment reports preserve dated evidence, not competing
current defaults. Keep actual SHA/configuration in each run's launch record.
Do not leave an old "current" section naming a 36-root/canary/old-model
workflow while a different configuration is active.
