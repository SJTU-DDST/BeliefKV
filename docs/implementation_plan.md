# BeliefKV Current Execution Plan

Status date: 2026-10-08.

## Active Native-Policy Baseline

The user authorizes a native SGLang policy baseline on the same first 108
manifest tasks, one arrival wave. Launch directory:
`experiments/raw/qwen35_native_policy_108root_2to4_20261008_v9/native`.
Disable BeliefKV admission/control socket, PREPARE, predictive H2D and final
priority. Keep shared harness notifications, natural-language returns,
first-turn 2-4 generation compatibility, radix sessions and read-only
telemetry. This is native FCFS/HiCache on the shared compatibility patch,
not a pristine upstream wheel or BeliefKV reactive with H2D switched off.
Keep running=48, Host=200 GB on NUMA1, Device Mamba/FULL bytes=0.9 and
matching Host byte proportions; context=131072/completion=8192, seed=21,
temperature=0, graph=2048 with accepted 32-step FINALIZE reserve,
workflow deadline=14400 s.

Launched on October 8 at 23:07 Asia/Shanghai, runtime commit `f188443`.
Live preflight confirmed session=true, admission=false, socket=null, FCFS
and native priority=false. Device FULL/Mamba bytes are 36.843/33.096 GB;
Host bytes are 105.358/94.652 GB; CUDA graphs include batch 48. Initial
inspection found all 108 workflows started and 58 initial delegation groups,
all with two children; telemetry dropped/failed records are zero.
This is startup evidence only, not a completed performance result.

Implemented before launch:
- Native receipt locks after H2D ACK, at most four leases and 1 GiB closure.
  Predictive holding stays lead+1 s. Only a matching, actually submitted
  next request extends a locked lease to at most 10 s after ACK. First
  service, invalidation, expiry or actual NO_TOKEN pressure releases it.
- Restore-ready and final-stage priority share one promotion per four
  normal admissions. Failed native unlock retains ownership evidence,
  disables the physical lane and never retries a possibly partial release.
- Rolling tool ETA drift no longer invalidates an ACKed same-episode load.
- Request-attributed Mamba source/destination COW witnesses support batched,
  completed non-speculative extend forwards. Unverified remains unknown.
- JOIN projection uses observed 500 ms/2 s/5 s wall-clock rates rather than
  extrapolating one short fast burst. The work head is unchanged; RETURN
  precision is still unproven and residual fitting remains a separate task.

Verification: 325 passed, one skipped; staging patch reverse-check passed.
Commit before launch and freeze executable code/model/prompt during the run.
Do not automatically append another predictive run or call v8c/v8d a
controlled speedup. Record native baseline results with realized workload,
H2D time/bytes, cache reuse and recompute, not just GPU utilization.

Offline HTML export is scheduled separately from the frozen experiment.
V8c reactive and v8d predictive timelines are in
`experiments/reports/qwen35_v8_timelines_20261008/`, with embedded gzip data
and `.json.gz` sidecars. The renderer now consumes native v0.5.20 service,
transfer and pool telemetry without copying raw traces. The independent
`beliefkv-native-timeline-v9` tmux job uses `--wait-for-pid 423778` and will
produce `native_v9_execution_timeline.html` after the experiment driver
exits, including non-completed workflows rather than hiding them.
Because collection rechecks on-disk source fingerprints, the native renderer
runs from `/tmp/beliefkv-policy-20261009` on branch
`next/latest-mamba-restore-ready`. Keep the active checkout's metrics/runtime
and engine staging patch at their launch contents until v9 has exited.

V8d read-only work audit found 101 natural final requests among 135 first
phase crossings (34 were not final). First-crossing median signed/absolute
work errors were -134.02/172.77 tokens; last pre-EOS snapshots were
+40.96/45.05 tokens with median actual remaining work 9 tokens. Do not
correct both regimes with one global bias or count post-EOS zero labels
as pre-EOS accuracy. This is same-run diagnosis, not held-out validation.

## V8d Result And Priorities

V8d finished on October 7 at 23:47: 107 completed, one incomplete, no
physical-lane disable/receipt failure/telemetry loss. It issued 1,094
predictive H2D commands, all with first-service records, but only 71 FULL
targets were verified reused (22 JOIN, 49 tool); 168 had native reloads.
Mamba verification currently requires singleton prefill, so its 14 verified
commands must not turn all remaining transfers into claimed waste.

V8d diagnoses and remaining work (new implementation status is above):
1. Repair JOIN work/time projection: all 19 pre-EOS estimated-work transfers
   missed the 0-1 s RETURN window; median advance was 12.968 s. Audit
   intrinsic remaining tokens and actual service share separately.
2. Couple restore with admission and bounded residency. First-service lag
   median is 8.167 s versus a 1.5 s soft policy lease; 724 leases lost native
   residency before expiry. Avoid blanket longer pins and premature eviction.
3. Instrument batched Mamba first use and native PREPARE consumption.
   PREPARE copied 40.047 GB of FULL, but custom pressure demotion stayed zero;
   this is not proof that native eviction never consumed a backup.
4. Keep Host churn and block-attribution censoring explicit. Both Host pools
   filled; 28,103 eviction-index records expired and Mamba hit location is
   incomplete despite 95.28% aggregate input-token hits.
5. Keep workflow/model outcomes explicit: 91 single-round, 16 two-round,
   one four-round workflows and 11 singleton JOIN groups. pytest-6197's
   root repeated text until length truncation; its children and JOIN completed.
6. Only after repairs, collect same-version reactive for a valid comparison.
   Old v8c disabled physical actions; v8d changed shell feedback. The +2.03%
   observed throughput is not a controlled speedup, and mean JCT rose 11.57%.

The result review itself authorized no new run; the subsequent user request
authorizes the native-policy baseline above. The previous launch plan below
is historical, not a currently running pair.

## Latest Execution Update

V8c reactive finished 108/108 at 19:45, but its physical lane was disabled
at 17:05:16 after the eighteenth PREPARE D2H split publication (37 -> 535/37)
failed an overly rigid node-set reconciliation. Only seventeen PREPARE ACKs
were credited; native HiCache and agent evidence remain useful diagnostics,
not a valid working shared-residency baseline.

Fix split ACKs using current native ancestry, original anchor generation and
exact reserved Host destinations, without dropping byte/pool/session/epoch
or replay checks. Also repair busy-writer status publication, test-shape argv
classification, and shell pipeline exit feedback. No new agent guard.

Start only the user's requested repaired predictive diagnostic, 108 roots
single wave, in `qwen35_joint_wait_h2d_predictive_108root_2to4_v8d`.
Retain model artifacts and resource settings. Do not automatically rerun
reactive or claim a strict cross-version throughput comparison against v8c.
Export v8c only with explicit degraded-runtime diagnostics; the default
physical-lane failure rejection stays intact. Preserve traces/patches and
remove archived completed workspaces before restart.

The prior v8c execution plan below is historical and must not override this update.

The repaired v8d ran at `0f04f70` and has ended. All 108 initial groups had two
children, 340 PREPARE operations have completed without disabling the physical
lane, and the semantic worker is ready. JOIN/tool prefetch flags are on;
actual H2D/first-use/lead and final throughput remain to be measured.
395 related tests passed, one skipped. V8c archived workspaces were removed.
Freeze this launched version; do not promote a cross-version comparison.

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

The user authorizes 108 roots in one arrival wave, two to four children
per delegation turn through a new prompt profile with native first-turn
generation bounds of 2-4 (not a post-generation rejection). Running stays 48, Host
stays 200 GB on NUMA node 1, and FULL/Mamba pool settings stay fixed.
Do not change to 128, staggered 64+64 or artificial eviction.
More traffic is not inherently more useful traffic: report first-use,
exposed restore waiting, advance residency, Host churn and recomputation.

The single v6 development pair is complete: both arms finished 84/84. Seven
JOIN loads actually reused FULL without re-parking/native reload; all were
post-EOS and tool loads stayed zero. The observed +4.62% completed throughput
comes with less realized work and is not an isolated KV speedup. Work-only
refits and first-trigger audits are complete with the phase head/encoder frozen.
A separate observed-final-body path avoids requiring NN results or TPS
after a normal no-tool stop.
No extra repetitions are queued.

V7 has ended: reactive 83 completed/1 incomplete, predictive 84 completed.
Only 12/21 roots had multiple rounds; every round still had one child.
Native H2D CUDA-event time was less than one second and submit-to-ACK roughly
10 seconds, so H2D volume alone cannot establish an oracle opportunity.
Current v8c checks the first 108 train tasks in existing order and the new
native_in_graph_2to4 prompt, retaining old profiles. This is a combined
pressure/fanout diagnosis, not a root-count-only comparison.
The initial v8 AUTO attempt at debe99d was stopped: 105/108 initial
responses had no task calls. It is not eligible for the requested 2-4
comparison or training. Move delegation instructions after the general
prompt and use named task plus explicit parallel calls. The installed
XGrammar named-task format permits only one call; use the required grammar
over the task subset only for BeliefKV parallel named-task requests,
retaining all prompt tool definitions. Count remains prompt-selected;
do not reject workflows, invent children, or add a fanout guard.
V8b's repeat-capable grammar still produced one task for all 108 first replies
and was stopped. Bound first-turn native generation to 2-4 using RepeatFormat;
the model chooses count and content. Later rounds remain prompt-driven and
must be audited, not claimed to be guaranteed. Restart in a fresh v8c directory.
Add same-session prior-served-prefix loss separate from new input, complete
bounded eviction probes, session-close timing and terminal native-ancestry
observations. Do not sum shared ancestry as exclusive dead bytes or offload
dead data to crowd useful Host copies. Frozen heads are not assumed
calibrated in the changed regime. JOIN_ALL waits for the full member set;
early restoration is tied to the last observed unfinished member.
Current plan: `configs/migration/qwen35_108root_2to4_v8.json`.
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

1. V8b at `026f650` has stopped with invalid singleton fanout.
   V8c is launched at `c744461` with the first-turn native 2-4 repair in
   `experiments/raw/qwen35_joint_wait_h2d_ab_108root_2to4_v8c`.
   All 108 initial replies and JOIN groups actually have two children.
   Continue auditing later rounds; a configured prompt or first-turn result
   does not prove all later fanout or all workflow outcomes.
2. Observe native/predictive FULL and Mamba transfers, queue/submit/ACK
   intervals, common served-prefix loss, Host eviction attribution and
   terminal cache references/residency. Keep new input separate from
   recomputation and shared ancestry separate from dead exclusive data.
   Small alignment-tail prefix misses are not evidence of useful KV eviction.
   Read terminal observations from opportunities/admission_opportunities.jsonl;
   writer health and live prefix evidence are already verified.
3. Keep both arms frozen and cold-started. Report tool and complete-ALL JOIN
   timing under the new regime without assuming v7 calibration or
   counterfactual trajectory equivalence. Diagnose system faults before
   continuing; do not censor normal long workflows or invent children.
4. Export `memory_opportunity.json` per completed arm, retain raw evidence,
   and remove only unused workspaces. Assess H2D opportunity and useful
   cache loss together; DMA/ACK sums are not a whole-system oracle bound.
5. Busy-writer status snapshots currently update only after a 0.5-second
   empty queue or close. The v8c status file stopped refreshing at 17:18:56
   while raw JSONL kept advancing. Use raw timestamps and payload consistency
   for live diagnosis, not stale zero-error or hit counters. Repair periodic
   status publication after the frozen pair, not between its two arms.
   One raw partial snapshot has 8,083 native H2D payloads, 2.419 TB,
   71.22 s CUDA-event sum and 790.49 s submit-to-ACK sum; no repeated
   submission timestamps or exact payload rows. These are not a completed
   makespan or a counterfactual JCT gain. Audit later singleton groups as
   realized model behavior; do not invent children or cancel workflows.

### Completed V7 Preparation

The steps below record completed preparation and its limits; they do not
authorize another v7 run or a source/model change during v8c.

1. V6 lifecycle audit complete: all seven JOIN FULL targets actually reused;
   no own re-demotion/native reload. Two soft leases expired before eventual
   reuse, so expiration is not a miss. No new tool load means its post-ACK
   GPU behavior remains unverified.
2. Work-interval repair complete: retain legacy clip-then-expand semantics
   for old artifacts; expand signed residuals then clip only in explicitly
   versioned and recalibrated candidates. The isolated repair did not improve
   point MAE or provide earlier v6 replay triggers.
3. Fit only the work head using v6 intrinsic remaining-token labels and real
   100 ms snapshots, keeping phase/encoder/threshold frozen. Compare a small
   log-work quantile head to the deployed head and linear refit.
4. Audit first triggers, tool-round errors, early residence and actual
   recoverable targets. Runtime may explicitly compare center/upper timing;
   legacy defaults stay upper. Do not turn a whole-trajectory uncertainty
   bound into a blanket action veto or zero work into EOS.
5. Commit source and select one measured candidate before one same-config
   v7 live pair. Keep all 84 tasks, pools/running/prompts/budgets; no blind
   rerun, canary, artificial eviction, concurrency increase or repeat queue.
   Models still do not learn offline action net benefit.
6. Keep exposed restore waiting and block-attribution overflow as explicit
   measurement gaps. Host is full in both arms; aggregate hit >95% cannot
   certify low useful recomputation. Preserve both positive and negative runs.

V6 is complete and remains frozen as evidence; work refits do not backfill it.
V6 becomes training replay when added to the fit. Astropy/Sphinx are reused
project-disjoint development sets, not newly sealed evaluation.
v6 directory: `experiments/raw/qwen35_joint_wait_h2d_ab_84root_20261006_v6`.
Launch commit: `573f32c`. Runtime fingerprint:
`c65caec44ecc934cd5cff9d740ec96f19459f48527505c85927bd4ae969fd6b9`.
Candidate plan: `configs/migration/child_semantic_work_live_v7.json`.
V6 findings: `docs/experiments/joint_tool_join_h2d_v6_84root_zh.md`.
V7 development plan: `configs/migration/qwen35_v7_work_development.json`.
Choose unweighted log quantiles using the Astropy selector, center=500 ms
and the explicitly logged EOS protocol window=250 ms. Native EOS evidence
is not model prediction success. Reweighted body features remain a candidate.
165 related CPU tests pass; the next one-pair GPU check must retain all
workflows and actual transfer/reuse/trajectory evidence.
The one v7 pair launched at `5dfdd30`, directory
`experiments/raw/qwen35_joint_wait_h2d_ab_84root_v7`, is complete:
reactive 83 completed/1 incomplete, predictive 84 completed, makespans
5389.94/5209.46 seconds. Twelve JOIN loads reused FULL, three pre-EOS;
all actual task groups were singleton.
Runtime source SHA (445 Python/shell files):
`4257db63689e9a92786f339e706180e56c5370efd421c7bb9d96336be2c466b9`.
This is not an isolated KV speedup or evidence for the new 2-4 regime.
V8c retains the heads as diagnostics and adds direct cache-opportunity
evidence; no additional repetition pair is queued.

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
