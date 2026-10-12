# BeliefKV Current Execution Plan

Status date: 2026-10-13.

Current development run is V44, cold-started at 05:44:21 CST on October 13
from f55e136/3698ce2f, engine tree 6d6af094. It deploys committed exact
H2D-statistics reuse and live Tensor closure lengths. V207 verifies HTTP200,
1851 real GPU-service samples, enabled predictors/physical actions,
matching sources/engine, all 27 frozen fields and pool capacities.
Observed reserve is 4144 FULL tokens and 45 Mamba slots following the
imminent batch. Keep running sources frozen and fixed V29 native for
development. The complete busy-window outcome remains unverified.
V209 qualifies historical-checkout verification for development restarts,
with 68 checks. The initial launch failed before workload creation;
preserve its log and do not count it as a workflow failure.

V43 stopped administratively at 05:27:59: 78 completed, 30 created
unfinished and 48 unarrived; the latter 78 are censoring. All 108
workspaces passed full tar comparison before cleanup at 05:32:08,
20.973GB allocated source to 2.501GB archive; only 30 owned stopped
containers were removed. Preserve raw/results/plan. V206 seals known
pre-completion positive-FULL/start/submit: JOIN 92/86/43, tool
1483/203/198, with 14/6 unknown boundaries. Proven early next-request
FULL consumption is 37 JOINs/753.193MB and 180 tools/12.127GB.
Keep demand handoff separate and never add overlapping prefixes.

V204 separates nine natural JOIN work underestimates of 57-303 tokens
from one later tool round with EOS-to-JOIN 177.49s. Consumed natural
JOIN EOS-to-boundary P50/P90 is 88.846/277.805ms. V208 uses labels
only after actual RETURN and excludes same-project donors: across 76
natural last-pre-EOS requests, a simple bucketed median residual
worsens token MAE 80.936->110.818 and P50 70.917->105.738.
Sampled 2s natural triggers fall 44->15. Do not deploy this pilot.
Continue phase/content/service-conditioned work prediction, separately
evaluate tool-round classification, capacity-to-submit and consumption.
No capacity/enqueue replay or throughput proof is established.
V210 archives 11 temporary helper sources; never rerun completed
administrative helpers unchanged.

V199 seals the complete V43 600-900s window: 331539/380722 decode
request steps versus fixed V29 native, 12.918% lower, at 23.334k/23.240k
visible context. Worker decode intervals average 35.340/32.467ms.
Instrumented exclusive Python wall is 53.979s: tool/reentry/admission
7.888/5.749/5.009s, handoff self/inclusive 5.259/16.266s.
Trajectories differ; none of these is a removable JCT or causal gain.

V200 reuses V42 sealed evidence. Of 27 tool ACK/service events without
proven early FULL consumption, three are late FULL, 22 expire before
completion and two invalidate on summary. None of the 22 expiration cases
has runtime-observed demand then. Their later arrival-to-service P50
11.98s cannot explain the earlier expiration. Ten analogous JOIN events
all expire before completion, with last FULL-submit lead P50 6.926s
versus 1.435s for consumed JOINs. Missing submitted-plan records are
not proof of no DMA: retain one proven early consumption in each source.
Investigate live forecast timing and capacity alongside post-arrival
admission; do not apply one offset or blindly extend leases.

The cold-deployed qualified changes reuse exact size/shape H2D statistics,
including unknown support, bounded to 256 keys and invalidated on each
valid completed H2D/D2H receipt. Closure reads live Tensor index shapes.
588 focused checks pass. V202 preserves all outputs across 144 estimate
groups and 12 closures. Tensor closure thread CPU is 6.8-10.6% lower;
4/16-query groups including the cold query are 68.3-74.2%/90.6-93.2%
lower. Retain single-query/range slowdowns up to 4.12%/3.0%.
V201 retains 769 nonblocking GIL samples and 209 sampling errors;
the estimator appears in only two inclusive samples. Do not label it a
major measured system bottleneck or extrapolate fixture reuse to live
frequency. V43 was not hot-edited; V44 keeps execution frozen.
CPU qualification does not satisfy the active performance goal.

V43 cold-started at 04:28:33 CST on October 13 from 50df863/3698ce2f,
with engine tree 6d6af094 and the qualified prefix-cost change below.
Keep the frozen workload/capacities and fixed V29 native. At 04:34:14,
V197 verifies HTTP200, 575 real GPU-service samples in the independent
tail, enabled physical actions/predictors, matching source/engine, all
27 frozen fields and pool capacities. Observed reserve is 4145 FULL
tokens/one Mamba slot, with Mamba following imminent request slots.
V43 has stopped and the complete window above remains below native;
the performance goal remains active.

V42 stopped administratively at 04:24:59: 69 completed, 39 created
unfinished and 48 unarrived; retain the latter 87 as censoring.
All 108 workspaces passed full archive comparison before cleanup at
04:28:01: 20.957GB allocated source to 2.137GB archive. Only 39 owned
stopped containers were removed. The process check initially caught the
read-only audit; no workspace was removed until it exited and tar
comparison completed. Preserve raw/plan; maintenance precedes V43.
V196 sealed positive-FULL/start/submit is JOIN 86/83/46 and tool
1451/178/176, preserving 28/6 unknown boundaries. Early started,
next-request consumed FULL is 37 JOINs/767.529MB and 153 tools/10.163GB.
V198 decomposes the same events: over 47 JOIN ACK/service events,
ACK-to-completion P90 is 10.065s, native arrival-to-service P90 457.752ms.
The early-FULL cohorts have arrival-to-service P90 436.449/858.707ms
for JOIN/tool. Quantiles are not additive; a long ACK interval is not
all admission waiting. Early residency still costs capacity. No native
relative GPU/JCT gain is established. Keep demand handoff separate.

V195 qualifies isolated-engine prefix-cost reduction: compute live RadixKey
length directly, avoid a compatibility helper for matching namespaces, and
read live Host FULL tensor shape. No cross-call token/residency cache.
257 native checks, 61 subtests and 45 reporting checks pass. All 27 real
CPU int64-index fixtures preserve outputs; matching cases reduce thread
CPU about 4.3-19.7%, some mismatches about 0.2%. V194/V195 bind evidence.
Patch 3698ce2f/tree 6d6af094 is now cold-deployed in V43. V42 execution
was frozen. CPU reduction does not prove GPU/JCT gains; partial ACK still
does not authorize execution and full data dependencies remain live.

V192 records a 30s nonblocking GIL profile with 1033 stack samples and
286 sampling errors. Caller routes selection/launch/result have 340/404/164
samples; selection leaf counts for reentry/length/prefix are 79/38/27.
Investigate complete prefix inspection cost without adding cross-call stale
residency. Shared ancestry reuse already exists within each inspection.
Retain compressed raw stacks and the qualified report. Samples do not
measure wall time, GPU stalls or removable JCT; running sources stay frozen.

V42's complete 600-900s window remains below fixed V29 native:
323732/380722 decode request steps, 14.97% lower at 23.254k/23.240k
visible context. Worker decode interval means are 36.122/32.467ms.
Instrumented exclusive Python wall is 55.787s versus V41's 60.585s;
different trajectories preclude attributing all change to the patch.
Reentry/admission/tool cost 7.086/5.199/7.744s, with handoff self/total
5.491/17.339s. Full48 matched CUDA graph cohorts are close; 46/47
intervals followed by prefill are about 9-18% longer. Several matched
single-request prefill intervals followed by decode are 2.8-3.2x native.
These are scheduler/worker intervals including control and overlap,
not isolated kernels, specific function attribution or removable JCT.
V190 reuses V184's verified fixed-native summary without rereading raw.
Prioritize separating prefill preparation, restore dependencies and
result processing, alongside capacity and next-request consumption.
V41 was 10.20% lower and its whole-queue skip appeared only once.

V42 cold-validates an exhausted aged FIFO handoff frontier despite new tail
requests. Read bounded tagged identities once in the same call; retain
the original path for unattempted restore-ready and live parent handoff
targets, rearmed requests, changed bindings and a wider frontier.
Ordinary admission and native dependencies stay live; partial ACK is
not execution readiness. The final 347 focused checks pass. Selected
real-inspector fixtures reduce thread CPU about 47-49% at 156/512
requests. Young depth8 queues add about 0.15-0.21ms per eligible probe;
depth64 has no consistent change. V186 binds sources and qualified
evidence. The initial unrefined candidate was not deployed. The validated
code was committed before stop/archive and cold restart; no hot editing.

V182 uses exactly V180's sealed prefixes: 9562 rounds with last-second
forecasts have per-round median absolute-error P50/P90 72.214/517.558ms.
257 rounds miss a recorded start window; only 13 also record free
capacity. Another 61 have an early candidate outside the actual window.
Snapshot categories overlap; per-round maximum planned bytes do not
establish unique achievable transfers, an oracle bound or heldout accuracy.
Continue capacity, complete restoration and next-request consumption work.

Previous development run was predictive-only V42, cold-started at
03:27:54 CST on October 13 from 028bb6b/cf0243b6. Engine tree remains
c7c27e0d. It deploys the already-attempted aged FIFO handoff frontier
shortcut with fixed configuration and V29 native reused. At 03:32:26,
v188 verifies HTTP200, frozen GC initialization, 520 real GPU-service
samples in the independent tail, enabled physical actions, matching
source/engine, all 27 frozen fields and actual capacities. The observed
reserve is 4145 FULL tokens/one Mamba slot, following imminent request
slots. The 03:34:07 early snapshot has no new frontier skip yet;
v189 records 387 aged-frontier skips and 395 selected handoffs at
03:36:50. These have different denominators and do not establish
a hit rate or a GPU performance gain.
Partial ACK is not execution readiness. Measure the
execution_handoff_aged_frontier_already_attempted counter, synchronous
control cost, early FULL consumption and a complete busy window.
Freeze V42 execution sources. The performance goal remains active.

V191 independently seals V42 prefixes at 03:47:10-03:47:24. JOIN
positive-FULL/start/submit is 24/23/11 over 27 known completion
boundaries, retaining 84 unknown; tool is 578/54/53 over 4037 known,
retaining two unknown. Early started and next-request consumed FULL
is six JOINs/90.276MB and 50 tools/3.117GB. Last-ACK to first-service
P90 is 11.263s/2.653s. This partial prefix cannot be compared with
V41's longer run by raw counts; unknown boundaries are not misses.
Demand handoff remains separate from predictive consumption.

V41 stopped administratively at 03:24:35: 21 completed, 87 created
unfinished and 48 unarrived. Preserve the latter 135 as censoring.
All 108 workspaces passed full archive comparison before removal at
03:27:39: 20.861GB allocated source to 2.296GB archive. Remove only
87 stopped owned containers; retain raw, results and frozen plan.
The final audit exited before archive cleanup, which precedes V42.
See v187 and the existing archive README for verification and restore.
The strict stopped-run funnel has JOIN positive-FULL/start/submit
47/47/18 over 49 known boundaries, with 63 unknown; tool 1021/151/144
over 5481 known boundaries, with three unknown. Pre-completion started
and next-request consumed FULL is eight JOINs/82.801MB and
134 tools/8.932GB. Last-ACK to first-service P90 is 6.153s/2.698s.
Residual native FULL Host-hit is 0.832GB/0.884TB, not an oracle bound
or proof of pre-boundary feasibility. Keep demand handoff separate.

V40 stopped administratively at 02:46:16: 62 completed, 46 created
unfinished and 48 unarrived. Preserve the latter 94 as censoring.
All 108 workspaces passed full archive comparison before removal at
02:51:17, reducing 20.943GB allocated source to 2.117GB archive.
Remove only 46 stopped owned containers and preserve raw/plan/results.
The initial cleanup stopped because a read-only audit still referenced
raw; no workspace was removed. Repeat full comparison after audit exit.
Maintenance precedes V41. See v180 and the existing archive README.

V180's stopped-run sealed_dispatch_funnel has JOIN positive-FULL/start/
submit counts 80/79/41 over 91 known boundaries; keep 31 unknown.
Tool counts are 1394/170/165 over 10343 known boundaries; keep seven
unknown. Pre-completion started and next-request consumed FULL is
34 JOINs/500.408MB and 150 tools/10.422GB. Last-ACK to first-service
P90 remains 9.042s/15.321s. Residual native FULL Host-hit is
1.669GB/1.238TB, not an oracle upper bound or proof of pre-boundary
opportunities. Keep demand handoff separate; do not add V176's prefix.

V40's complete 600-900s window remains below fixed V29 native:
327142/380722 decode request steps, 14.07% lower at 23.49k/23.24k
visible context. Instrumented exclusive Python wall is 60.013s;
reentry/admission/tool cost 9.003/8.080/7.355s, not removable JCT.
Full48 matched CUDA graph cohorts are close. V176's independent early
prefix confirms early next-request FULL consumption: four JOINs/63.406MB
and eleven tools/560.599MB. Completed-boundary positive-FULL/start/submit
funnels are JOIN 12/12/6, tool 136/14/13; retain 95 unknown JOIN boundaries.
V178's qualified nonblocking GIL profile records 867 samples and 246
sampling errors. Sample shares are not wall time or removable stalls.

V41 cold-validates exhaustion-aware demand handoff: avoid repeated full queue
planning when all bounded tagged identities were already attempted.
Recheck live request fields, preserve the current running batch, and keep
new/changed/rearmed requests on the original complete restoration path.
Ordinary native admission still plans against current dependencies;
partial ACK does not authorize service. All 265/337 checks in two
overlapping batches pass. V179's selected exhausted-queue CPU fixture
uses the real inspector and RadixKey; at 48 requests/depth64, thread cost
falls from 3.017 to 0.115ms with equal handoff actions/bindings.
Measure real skip frequency, early FULL consumption and busy throughput
in the verified cold deployment. Do not infer GPU gains from the
selected CPU case. V41 remains frozen and the goal remains active.

Previous development run V40 cold-started at
01:57:38 CST from 9f42c24/cf0243b6 with unchanged engine tree c7c27e0d.
Deploy report-phase waiting-parent cold FULL candidacy; preserve
108+48 arrivals, running48, Host200GB 80:20, HBM Mamba/FULL0.9,
180s tools and fixed V29 native. At 02:04:16, v174 verifies HTTP200,
GC initialization, 565 actual GPU-service samples in the observed tail,
physical_disabled=false, unchanged capacities and 27 frozen fields,
and validated source/engine identity. Mamba reserve follows imminent
request slots; the observed budget has one slot and 4145 FULL tokens.
Do not require a fixed one-slot reserve when the next batch has more
requests. Freeze running execution sources; goal active.
V175's early capacity prefix records 30 JOIN/28627 FULL-token and
six tool/7894 FULL-token waiting-agent demotions, including demand
admission paths. No actual wait-prefetch or tail reclaim is observed
in this prefix; these counts do not prove early consumption or gains.
V39 stopped at 01:53:51: 31 completed, 77 created unfinished and
48 unarrived, with the latter 125 administratively censored.
All 108 workspaces passed full archive comparison before cleanup
at 01:57:11: 20.890GB to 2.492GB. Remove only 77 stopped owned
containers; preserve raw/plan/results. Maintenance precedes V40.
See v173, raw/ARCHIVED.md and the existing archive README.

V39's complete 600-900s window remains below fixed V29 native:
332453/380722 decode request steps, 12.68% lower at about
22.62k/23.24k visible-context tokens. Instrumented exclusive Python
wall is 57.799s; tool/reentry/admission costs are 7.282/8.462/8.297s,
not removable JCT. Full48 CUDA graph cohorts are close, while some
46/47 cohorts adjacent to prefill remain slower. See v171.
V170's independent early prefix confirms pre-completion started,
next-request consumed FULL: two JOINs/13.804MB and nine tools/250.163MB.
Completed-boundary positive-FULL/window/submit counts are JOIN 13/13/3,
tool 81/10/10; preserve 96 unknown JOIN boundaries. Capacity refusals
affect 15 JOIN and 498 tool-wait identities, with no observed actual
wait-prefetch reclaim in this prefix. These are not failed workflows.

V40 cold deployment removes report-stage-only exclusion of a waiting
parent from cold FULL candidates. Protect an actual live restore ticket
for that parent and existing node residency leases; preserve current
identity, references, Host backing, in-flight and native allocator checks.
READY and terminal parents remain invalid. Backed FULL can be reclaimed
without evicting a referenced unbacked Mamba state. All 434 focused checks
pass; v172 binds final source and audit hashes. Commit 9f42c24 preceded
V39's administrative stop and verified workspace archive. V40 uses
unchanged workload, patch and fixed V29 native. V39 was not hot-edited;
do not infer GPU gains from candidate fixtures.
Measure actual FULL release, complete restoration, early next-request
consumption and busy-window costs. The goal remains active.

V39 predictive cold-started aa3e7c1/cf0243b6 at 01:19:38 CST,
deploying bounded survival probes, indexed quantiles and fresh
observation reuse. Deployed tree remains c7c27e0d; keep frozen
108+48 arrivals, running48, Host200GB 80:20, HBM Mamba/FULL0.9,
180s tools and fixed V29 native. At 01:26:33, v169 confirms HTTP200,
GC initialization, 556 actual GPU-service samples in the observed tail,
physical_disabled=false, unchanged capacities and 27 configuration
fields, and 4145-token/one-Mamba reserves. Independent input tails,
source and process identities are preserved. Startup does not prove
performance gains. Running sources frozen, goal active.

V38 stopped administratively at 01:15:52 CST: 84 completed and
72 created unfinished, with all second-wave tasks already arrived.
Preserve those 72 as administrative censoring. At 01:19:21, all
156 workspaces passed full archive comparison before removal:
38.843GB allocated source to 4.029GB archive. Remove only its
72 stopped owned containers; preserve raw evidence, results, manifest
and frozen plan. Maintenance precedes V39; free space was 161.38GiB.
See v168 receipts, raw/ARCHIVED.md and the existing archive README.

V38 cold-started 71d4d44/cf0243b6 at 00:05:13 CST on October 13;
v160 verifies physical actions, actual GPU service, unchanged frozen
capacities, 4145-token/one-Mamba reserves and deployed tree c7c27e0d.
Its complete 600-900s window still trails fixed V29 native:
331016/380722 decode request steps, 13.06% lower at about
22.88k/23.24k visible-context tokens. Mean worker intervals are
35.372/32.467ms. Instrumented exclusive Python wall is 59.102s;
tool/reentry/admission self costs are 9.550/8.940/7.785s, not removable
JCT. Thirty-eight post-enqueue tail passes cost about 14ms exclusively;
actual tail completion or net performance benefit is unproven. See v163.

V162's independently sealed prefix confirms pre-completion started,
next-request consumed FULL: two JOIN events/21.176MB and thirty-five
tools/2.079GB. Completed-boundary positive-FULL/window/submit funnels
are JOIN 24/24/10 and tool 359/37/35; keep 83 JOIN boundaries unknown.
V166 uses exactly those prefixes for 28 natural ALL JOINs, eighteen
with late-work triggers: final rolling RETURN signed error P50 is
428ms late, absolute P50 541ms. A -499.5ms offset fit on the first nine
events worsens the next nine's absolute P50 from 548.8 to 642.6ms;
500ms hits stay 4/9. Do not deploy a uniform seconds offset.
Initial transfer and final rolling prediction differ: ten submitted
JOINs often transfer 3-11s early and can exhaust a 3s lease before
completion. Expiry is not proof that all bytes were wasted, nor solely
parent admission delay. Keep both timing measurements separate.

V39 is cold-deployed with unchanged workload and physical patch.
Reduce repeated tool survival queries using the earliest possible
window-entry time of a fixed monotone conditional distribution, capped
before historical-support fallback. New hint identity forces a query;
unordered/non-finite distributions do not defer. Preserve live identity,
current survival, capacity and safe-checkpoint checks at actual enqueue.
Binary timing-curve quantiles preserve first crossings, roundoff dips,
plateaus and unsupported tails; reuse a scan's live-check clock and the
existing fresh reentry observation without changing execution readiness.
Partial ACK never authorizes execution.

472 focused checks pass. V164 CPU fixtures preserve every query output:
quantile CPU is about 36.1% lower, combined survival lookup 10.4% lower.
V167's selected 156-context out-of-window 10s fixture defers 98.49% of
repeated survival queries; thread CPU is 84.122 to 54.908ms.
Both arms share current numerical queries and selected curves are not
workload frequencies; these are not GPU throughput or JCT gains.
V168's final-source unfrozen single run is only 4.55% lower and contains
a 24ms outlier. With collect/freeze initialization matching the server,
thread CPU is 85.579 to 55.760ms, 34.84% lower, with 15600 checks per arm.
Preserve both timings and GC callbacks; do not retrospectively assign the
unobserved outlier to GC or claim a stable end-to-end speedup.
V165's deeper admission fixtures do not show stable savings.
V38 collection remains frozen at 71d4d44; aa3e7c1 was cold-deployed
in predictive-only V39 after the cutoff and fully verified archive.
Keep fixed V29 native, verify real consumed FULL and busy-window cost.
Goal active.

V37 stopped administratively at 00:01:59 CST: 49 completed,
59 created unfinished and 48 unarrived; preserve the latter 107
as administrative censoring. At 00:04:30, all 108 workspaces passed
full archive comparison before removal, 20.933GB to 2.497GB.
Remove only its 59 stopped owned containers; raw evidence and plan
remain. Maintenance precedes V38; available space is 168.71GiB.
See v159, the raw run's ARCHIVED.md and the existing archive README.

V37's complete 600-900s window still trails fixed V29 native:
337529/380722 decode request steps, 11.35% lower with about
23.57k/23.24k visible-context tokens. Mean worker decode intervals are
35.003/32.467ms. Instrumented exclusive Python wall is 56.834s;
reentry/admission/tool self costs are 8.678/7.792/6.948s, not removable
JCT. The separate V155 early prefix confirms two JOIN events/13.332MB
and eleven tools/641.495MB of pre-completion started, next-request
consumed FULL. Completed-boundary positive-FULL/window/submit funnels
are JOIN 11/11/4 and tool 164/12/12; keep 94 unknown JOIN boundaries
unknown. Different prefixes and trajectories do not prove causal gains.
See v155-v157.

V156 finds repeated FULL capacity refusals with truncated scans for
1299 tool-wait identities and 17 JOIN identities. Actual wait reclaims
are JOIN seven records/19631 FULL tokens and tool four/10968; seventeen
tool extent splits contain 4791 tokens. Rechecking the same set prefix
does not establish absence of other cold KV. Continue a bounded leaf
snapshot across reclaim calls, validating at most 64 non-target
candidates and selecting at most eight per pass. Revalidate current
leaf membership, identity, backing, references and in-flight state for
actual victims. Target exclusions do not consume the validation quota;
Mamba-only shortages skip FULL scans. This is not an atomic cache census.

After a partial native enqueue, one bounded cold-capacity pass can
prepare the unsubmitted safe-input FULL tail while a command remains
pending and the causal wait is live. Derive the tail from the original
safe input plan for the same checkpoint, not closure bytes that can
include unusable generated output. Respect the live shared byte budget,
page alignment and input/decode/Mamba reserves; exclude the full target
closure. Do not wait for DMA, enqueue the tail or authorize service in
this pass. Existing ACK-time replanning remains responsible for the
next burst; this is not continuous in-flight maintenance. Partial ACK
alone never makes a checkpoint executable. Restrict lease refresh in
cold-candidate validation to its own context. Runtime 647 and native 124
checks pass; canonical patch cf0243b6 matches independent tree c7c27e0d.
See v158_cold_scan_and_tail_validation.

V37 collection remains frozen at c48b3dd/838d2ed8. Commit 71d4d44
was deployed after preserving its cutoff and comparing the workspace
archive, using a cold V38 predictive run with the same frozen workload
and fixed V29 native. V160 confirms engine/source identity, actual
service, capacity and physical actions. Next measure early consumed
FULL, tail completion and busy-window service/control cost. The goal
remains active; V154's V37 receipt stays historical deployment evidence.

V36 was administratively stopped at 23:08:23 CST: 39 completed,
69 created unfinished and 48 unarrived workflows; the latter 117
are administrative censoring, not agent failures. At 23:10:45, all
108 workspaces passed full archive comparison before removal,
20.905GB to 2.408GB. Remove only its 69 stopped owned containers;
preserve raw evidence. Available space is 173.83GiB; see v153 and
raw/ARCHIVED.md. Commit 29eb3d2 is deployed in the same-configuration
V37 predictive cold start, keeping fixed V29 native and the goal active.

V36's first complete 600-900s window still trails fixed V29 native:
322277/380722 decode request steps, 15.35% lower at 23.79k/23.24k
visible-context tokens. Full 48-request decode cohorts are similar;
47-request intervals followed by prefill remain longer. Instrumented
exclusive Python wall is 61.904s, not removable JCT; reentry/tool/admission
self costs are 9.751/9.556/8.596s. The V150 sealed completed-boundary
JOIN positive-FULL/start/submit funnel is 22/22/2, tool 496/10/10.
Early started and next-request consumed FULL is one JOIN/12.145MB,
nine tools/383.304MB. Keep 44.706GB demand-handoff FULL separate
and 85 unobserved JOIN completion boundaries unknown. V148 is an
earlier overlapping prefix and must not be added to V150.

V151 follows 192 repeated JOIN starts by no_restore_plan; these are
decisions, not independent JOIN events. Of 37 successful reclaim
receipts, 34 miss FULL plus input reserves, 32 without published
waiting victims. A truncated bounded scan does not prove no cold KV.
V37's cold-start correction bounds input reserve by the
observed native chunk/input compute limits instead of reserving 16384
tokens when chunked prefill is bounded at 4096. Retain decode pages,
Mamba reserve, actual allocator/identity checks and shared protection.
A consumed current chunk must not zero the next-batch reservation.
V36 remains frozen. V149's Enum allocation reduction preserves CPU
plans, counters and native inspections, with 103 existing checks
passing; mixed fixture timings do not prove stable GPU savings.
512 capacity/shared-path checks pass. V152's identical sealed free-list
comparison finds 67 previously rejected decisions fitting the selected
FULL plus revised reserve, and 49 with positive partial allowance.
These repeated budget counterfactuals do not prove live native identity,
split feasibility, DMA, complete checkpoint readiness or consumption.

V36 predictive-only cold-started 9d2bf8e/838d2ed8 at 22:27:27 CST,
including same-scan tool P50 reuse and separate restore frontiers.
At 22:31:42, validation confirms HTTP200, 2256 GPU-service samples,
physical_disabled=false and unchanged frozen configuration and actual
FULL/Mamba device/Host capacities versus V35. Keep fixed V29 native
and freeze the running source. Low-pressure startup does not prove
early consumed FULL or throughput gains; the goal remains active.
See v147_v36_deployment_validation. Continuous reclamation during
in-flight DMA remains unimplemented.

V35 was administratively stopped at 22:21:14 CST on October 12:
39 completed, 69 created unfinished and 48 unarrived workflows.
The latter 117 are censored for the development restart, not autonomous
agent failures. Frozen source is e673af5/838d2ed8; preserve all raw
evidence. Prepare the same-configuration V36 predictive cold start
using fixed V29 native; the performance objective remains active.
See v146_v35_development_stop for outcomes and process identities.
All 108 workspaces passed full tar comparison before removal at 22:25:23
CST, 20.934GB to 2.332GB; only its 69 stopped owned containers were removed.
Available space is 178.23GiB. Maintenance precedes V36; restore pointers
remain in raw/ARCHIVED.md, the archive README and v146_workspace_archive.

The complete V35 600-900s window contains 319567/380722 predictive/native
decode request steps, 16.06% lower with similar visible contexts.
Instrumented exclusive Python wall is 55.245s, not removable JCT;
reentry and admission self costs are 9.438s and 8.381s.
Early completed-boundary JOIN positive-FULL/start/early-submit is
30/27/0; tool is 744/6/6. Confirmed early tool FULL is 266.445MB.
Keep 80.218GB demand handoff FULL separate; see v145 audits.

The 22:13 sealed lease reconstruction observes 153 JOIN budget refusals,
152 with only demand locks occupying the common frontier, across 44
JOINs. All plans fit recorded byte allowances, but only one fits actual
free lists without reclamation. Periodic state budgets are not exact
refusal-time slot records. Actual partial splits are JOIN3/tool23;
splits do not prove complete dependency readiness or consumption.
Evidence: v146_v35_restore_frontier.

Separate bounded demand and speculative cache frontiers only with
validated native capacity. A full decode batch can overlap one
speculative restoration with the next demand restoration, with a shared
live byte budget and unchanged native admission, input/decode reserves,
identity checks and lease release. Keep legacy unobservable capacity
combined. Apply the same role accounting at selection, enqueue and ACK
protection. 403 transfer/runtime and 120 handoff/lifecycle checks pass.
Deploy together with same-scan tool P50 reuse in V36; measure real
early consumed FULL, reclamation costs and busy-window service first.

V36 includes same-scan P50 reuse across tool
sorting, filtering and window checks, with fresh post-reclaim and enqueue
validation. 301 focused checks pass. A 156-context long-wait CPU fixture
uses 236.278/128.941ms across 3000 ticks with 9360 candidate checks on each
variant. This is outside-window CPU cost, not GPU throughput or prediction
accuracy. It was not deployed to V35; V36 includes it at its cold start.
Evidence: v144_tool_scan_center_reuse_cpu_final.

V34 failed at 20:15:59 CST on October 12: the opportunity writer rejected
a NumPy boolean, and the scheduler propagated its background error.
This is not GPU OOM. Six workflows completed; 102 created and 48 unarrived
tasks are runtime-failure censored, not administrative stops or agent
failures. Frozen source remains 0b45f4b/3e23c770; preserve the earlier
deployment receipt and all raw evidence. See v142_v34_runtime_failure.

The writer now converts NumPy bool/integer/floating scalars through JSON's
default callback, retaining strict nonfinite and array rejection.
119 related checks pass. A real hybrid radix/allocator regression reproduces
protected resident ancestors blocking subsequent Host-only tail splits.
Allow those unchanged resident ancestors while retaining target/descendant
locks, Host-only ancestor locks, identity and in-flight checks.
130 native checks pass; patch 838d2ed8 matches tree d8abddb5.
See v142_failure_and_partial_tail_validation. Partial FULL alone still
cannot execute; continuous reclamation during in-flight DMA remains absent.

The complete pre-fault 600-900s window has 336482/380722 predictive/native
decode request steps with similar 23.25k-token contexts. Instrumented
exclusive Python wall is 55.894s/300s, not removable JCT. Completed-boundary
JOIN positive-FULL/start/submit is 32/27/2; tool is 566/3/3.
Confirmed early FULL is JOIN one event/7.66MB and tool two/82.08MB;
83 unknown JOIN boundaries remain unknown. The six native split declines
are not proven to share one predicate. See v141 fault-prefix audits.
The failed workspaces were archived before the same-configuration V35
predictive-only cold start using fixed V29 native. Continue with lower planning/inspection
costs and better tool opportunity conversion; the performance goal is active.

V34's 108 workspaces passed full tar comparison and were archived at
21:29:17 CST, 20.841GB to 2.113GB. Only its 102 stopped containers were
removed; traces, outcomes, manifest, frozen plan and failure state remain.
Available disk is about 182.77GiB. v142_v34_workspace_archive and raw
ARCHIVED.md retain the restore path. Maintenance precedes the new collector.

V33 stopped at 18:40:59 CST for the next capacity correction:
106 completed, one xarray-6721 invalid historical tool-argument
error and 49 administratively censored workflows. Its 156
workspaces were losslessly archived after tar comparison
(38.898 to 4.352GB); raw evidence and frozen configuration remain.
User-requested large superseded V10-V26 trace archiving precedes
the next predictive cold restart. Keep V29 native and recent
V27-V33 evidence, all outcomes, summaries, models and HTML.

This archival step finished at 19:10:29 CST with no GPU collector
running: 3814 large JSONL/log files from V10/12-15/17-26, 154.156GB
logical sources and 3.788GB archives. Every archive passed GNU tar
comparison before source removal. Actual filesystem free space
increased by 125.153GiB to 185.697GiB; do not substitute the
logical size difference for reclaimed space because of hard links.
Per-run TRACE_ARCHIVED.md and archive README retain restore paths;
v138_superseded_trace_inventory/archive record hashes and receipts.
Archival completed before the next predictive cold restart.

Capacity audit v137 observes six positive-FULL-budget first-extent
refusals across four independent V33 tool episodes, all inside
their recorded start window. Their remaining FULL allowances are
318/161/5304/234 tokens. More refusals have zero allowance after
reserves: 4752 tool records across 2746 waits and 740 JOIN records
across 99 waits; categories overlap. V32 lacks detailed budgets
and cannot retrospectively establish the first-extent fraction.
These are sampled proposals, not observed early reuse or benefits.

Deploy page-aligned partial Host-only FULL radix extents at action
time, retaining the original suffix Mamba checkpoint and all
session/index bookkeeping. Planning does not split, allocate or
submit DMA. A published live Host-backed waiting FULL victim
permits a timed bounded reclaim probe even when native accounting
gives zero allowance; preserve target exclusions and input/decode
reserves, then replan against actual free lists. Retry failed
probes at the existing observation cadence, not per child.
Do not synchronously offload unbacked agents or premaintain a
fixed free pool. Existing FULL-only incremental PREPARE remains;
evaluate more proactive asynchronous backup separately with
action-local diagnostics, transfer contention and return churn.
624 runtime and 123 engine checks pass; canonical patch 3e23c770
matches tree 0b3deb75. V34 predictive failed on its background writer; rerun with the
same frozen parameters and cached V29 native. Measure real
probe/partial submission, independent early consumed FULL and
busy-window service performance. The performance goal stays active.

Record partial restoration as separate stages: native tree split, submitted
burst, ACK, native dependency readiness and first-service consumption.
The v139 fields report captured Host-backed FULL left outside the burst,
Host-only Mamba and unbacked nodes. The captured closure can include output
beyond the next reusable input, so its remainder is not automatically that
request's missing prefix. Legacy Mamba missing counts only states whose
FULL is resident; zero does not mean all required states are ready.
Use the already captured nodes, with no additional native tree scan.
Bounded reclaim precedes each burst; replan after ACK or let native demand
recovery finish the tail. Continuous cold reclamation during in-flight DMA
is not implemented. H2D occupies space and its ACK cannot guarantee tail
capacity. The 90 focused checks establish telemetry and path contracts,
not reduced delay. V34 must assess tail readiness, residence cost, real
consumption and busy-window throughput against the fixed native.

The future baseline reproduction plan is recorded in section 6.4 of
[the related-work comparison](related_work_comparison_2026-07-21_zh.md).
Use common-model/native-executor policy comparisons as the main table,
and original-stack reproductions separately. Keep online dynamic and
predeclared-graph workloads distinct. The first batch covers native,
reactive common-path, CONCUR, ThunderAgent, CacheScout and BeliefKV;
then add graph/TTL/multi-step prediction methods with explicit fidelity
and model-support records. Formal comparisons retain four repeated
blocks and held-out tasks. This is a future execution proposal, not a
completed reproduction; the native-relative optimization objective
and predictive-only development cadence remain unchanged.

V32 completed collection and postprocessing at 16:14:15 CST on
October 12: 153 completed and three incomplete workflows. Each
incomplete root ends with finish_reason=length, with no child
cancellation or expired workflow deadline. Keep the user's
8192-token output budget and current harness behavior unchanged.

Complete V32 JOIN positive-FULL/start/submit counts are 151/146/47
out of 186 completed waits; tool counts are 1680/37/36 out of
23878. Independent early FULL reuse is JOIN 46/0.810GB and tool
32/1.184GB; 147.387GB of demand handoff FULL remains separate.
Capacity reasons occur in 124/2853 completed episodes and overlap
other reasons. ACK-to-first-service P90 remains 13.322/18.202s.

The 600-1200/1200-1800/1800-2400s decode windows differ from the
cached fixed native by -9.24%/+1.43%/-5.77%, with batches near
47. In 600-2400s, instrumented exclusive Python wall is 322.214s;
reentry inspection contributes 87.615s, admission 32.719s and
tool prefetch 29.605s. Whole-run completion/JCT improvements
do not establish causal benefits. Preserve different realized
demand, local analysis and archive maintenance overlap.
Evidence: v133_v32_complete_prefetch/busy and
v134_v32_complete_development.

V33 cold-started e0fe3f2 at 17:12:32 CST on October 12,
predictive only against the unchanged fixed native. Its 17:19:38
deployment receipt confirms HTTP 200, 7899 real GPU service samples,
physical_disabled=false and physical pool capacities equal to V32.
Keep output8192 and the current harness continuation behavior.
Evidence: v134_v33_deployment_validation. The 17:36 sealed prefix
observes 43 complete JOINs with positive-plan/window/submit counts
40/36/5 and 4595 complete tool waits with 777/8/8. Independent
early FULL reuse is seven tools/0.478GB; no JOIN FULL reuse is
confirmed yet. Unknown completion boundaries stay unknown.
The complete 600-1200s window remains 13.10% below cached native,
1076.217 versus 1238.463 decode tokens/s, with batches near 47.
Instrumented exclusive Python wall is 116.330s, including reentry
22.608s; these are not removable JCT. The 17:53 outcome snapshot
has 34 completed, 74 created unfinished and 48 unarrived, with no
observed error/incomplete. Retain different realized demand and
local analysis/profiling overlap. Evidence: v135_v33_early
prefetch/busy/development; the performance goal remains unmet.

Deploy newly exposed cold FULL ancestor reclamation: after each
actual leaf demotion, revalidate the ancestor's native identity,
device-leaf membership, causal wait, sharing, pending transfers
and target-prefix exclusion. Stop at actual pool targets or a
64-node transaction bound. Retain live independent Mamba state;
do not synchronously drain a backup. Add deduplicated capacity
reason/budget records using existing observations. Real allocator
and related engine checks pass 102 cases; runtime checks pass 470.
Canonical patch 06d491ae exactly matches tree 27f9d9ae.
These checks do not complete the service-throughput/JCT goal.

Followup fixes the native FULL reclaim candidate scan: inspect up
to max_nodes nonexcluded leaves, default 64, to select at most eight
settled backed unreferenced candidates. Ineligible leaves no longer
consume that candidate limit. Revalidate live state before actual
reclaim; allocator capacity and target-prefix exclusions remain
authoritative. The real allocator fixture previously misses the
ninth usable victim. Related 108 engine and 470 runtime checks pass.
The standalone scan patch 943d7165 matches tree bcaae4ea; it is now
included with partial extents in 3e23c770 for the next cold restart.
V33 stayed frozen before its administrative stop. Evidence:
v135_cold_native_candidate_scan_validation. No measured GPU
benefit or workload-wide missed fraction is inferred from the fixture.

The V32 tool timing audit finds event-weighted last-second absolute
error P50/P90 113.904/643.405ms among 1680 positive-FULL-plan
rounds. These are tool, not child RETURN, metrics on fixed trace
observations. Of 377 events with a missed recorded start window,
only three have a simultaneous fitting free-list observation.
Keep overlapping timing/capacity categories and sampled coverage;
do not equate per-round maximum plan bytes to achievable benefit.

During the next collection, focus on measured reentry/admission costs,
tool positive plans that never reach their start window and
ACK-to-first-service delay. Preserve workload, pools, model,
lead, output budget and tool180s parameters for attribution.
V31's 108 stopped workspaces were losslessly compressed after
tar comparison; raw evidence and restore pointers remain.

Historical collection records follow. V31 was administratively
stopped at 13:50:40.618 CST with 63 observed completed workflows,
45 created unfinished and 48 unarrived tasks censored separately.
No spontaneous error/incomplete result was observed before signals.
Verified principal process groups and 45 run-owned containers exited;
raw evidence, frozen plans and all workspace files remain intact.
After old Host memory release, the unchanged 200 GB NUMA preflight
passed with 222.75 GiB estimated available capacity.

The completed V32 cold-started 8495c89 at 13:52:45 CST, containing the
4f4567f full-batch rowless restoration fix; its client began at
13:55:27 CST. The 13:57:38 validation observes healthy HTTP 200,
4209 real GPU service samples and physical_disabled=false.
Actual pool capacities equal V31 and the engine patch stays
7b4832ef. Keep the frozen workload/model/lead configuration and
predictive-only development cadence against the fixed cached native.
Measure conversion from positive plans into early consumed FULL,
ACK-to-first-service waiting and complete busy-window service
throughput. Startup validation and prior CPU checks do not
complete the throughput/JCT goal. Receipts:
v132_v31_development_stop and v132_v32_deployment_validation.
The V31 collection observations below are historical pre-stop snapshots.

Prioritize the newest predictive collection rather than continuing to
diagnose V29 as though it were current. V132 seals V31 at
13:39:02-13:39:32 CST: completed JOIN positive-plan/start/submit
events are 70/67/18 out of 75, and tool events are 1239/34/34 out
of 6110. Confirmed independent early FULL reuse is JOIN 15/0.682 GB
and tool 30/1.538 GB. Keep 41 JOIN and three tool completion
boundaries unknown. JOIN/tool ACK-to-first-service P90 remains
11.033/13.397s; demand handoff remains separate.

The complete V31 600-1200/1200-1800/1800-2400s decode windows
remain 20.42%/7.04%/10.52% below the fixed native, with weighted
batches near 47 on both sides. Instrumented exclusive Python wall
in 600-2400s is 285.854s: reentry inspection 50.273s, join_prepare
24.219s, admission_plan 22.437s, tool_prefetch 22.349s and
handoff_enqueue 21.301s. These are not removable JCT or isolated
policy costs; retain development overlap and different live demand.
The independent outcome snapshot has 55 completed, 53 created
unfinished and 48 unarrived workflows, with no observed error or
incomplete result. Pending tasks are not failures.

Deploy the already-validated 4f4567f rowless cache-restoration fix
at the next predictive-only cold development restart; preserve
V31 traces, frozen configuration and administrative censoring.
Its 380 relevant checks are complete. Cache restoration may
overlap a full decode batch without allocating a request row;
actual execution admission and physical pool reserves stay native.
Continue measuring real early FULL consumption and busy-window
performance before declaring this performance goal complete.
Latest evidence is v132_v31_latest_prefetch/busy/development.
Reuse the cached fixed native summary without rescanning its raw.

V131 seals V31 inputs at 13:12:21-13:12:33 CST. Of 29 observed
complete JOINs, 26 have pre-boundary positive FULL plans, 24 start
and 3 submit; 2,511 complete tool waits have 383/5/5. Capacity-budget
truncation appears in 23/782 episodes, and busy admission in 20 JOINs.
Reasons overlap; preserve 80 unknown JOIN boundaries and pending reuse.
Verified early FULL reuse is 1 JOIN/8.991MB and 4 tools/347.853MB.
Do not compare collecting-prefix totals directly with complete V27/V29.

Followup 4f4567f permits one cache-restore context while a full decode
batch occupies all request rows: restore does not allocate a request row.
Retain physical pool, byte, input/decode-growth reserves and native
execution admission. JOIN/tool regressions fail before the change and
submit after it without allocating a request row; insufficient capacity
and non-full batches with no rows remain rejected. Related 380 cases
pass. Keep V31 frozen at 5c1a646; deploy at a subsequent cold restart,
and do not infer V29's loss fraction from this reproduced dependency.

The sealed capacity snapshots contain 354 zero-slot native budgets out
of 808 native-budget snapshots, not independent rejections or a live
allocator census. The complete V31 300-600s development window has
310,198 versus 344,185 decode request steps, mean worker intervals
39.418/36.991ms and 47.533s of instrumented Python exclusive wall time.
Reuse the fixed native summary and its provenance without rescanning
raw logs. Local analysis overlaps; this is not causal speedup, isolated
kernel time or removable JCT. Continue reducing admission/prepare/
handoff/prefix-check costs and validating useful early opportunity
conversion. Preserve v131 evidence and update the existing documents.

Frozen-source verification now allows JSON/JSON.gz/Markdown output
under experiments/reports, as well as the existing reporting paths.
Keep report Python helpers, model artifacts and engine patches inside
the execution-source comparison and retain frozen-plan artifact hashes.
Related 54 cases pass. This prevents offline V30 report commits from
misclassifying V31 as changed execution; serving remains frozen at
5c1a646 and this is not an online runtime optimization.

V30 was administratively stopped at 12:49:22 CST. Preserve its cutoff
receipt: 80 completed, one incomplete, and 75 censored unfinished tasks.
The complete common 0-3600s window shows decode throughput 1118.33 versus
1158.58 token/s for the fixed V29 native reference, a 3.474% deficit.
Retain v30_stopped_vs_v29_native_20261012.md and the busy/lifecycle evidence.
Exclude the partially covered 3600-3780s diagnostic window, completed-only
JCT and final workflow throughput from performance claims. V30's frozen
fca7ad3 source did not include the FULL-only reclaim/EOS lifetime followups.
The historical instructions below to stop or keep collecting V30 are
superseded by the preserved stop receipt. Validate the followups in a fresh
predictive development run; the native-relative performance goal remains open.

V130 reuses the sealed V129 inputs to examine valid native EOS.
For 49 complete natural ALL JOINs, EOS-to-RETURN P50/P90 is
98.900/273.410ms; 7 exceed the old 250ms evidence age. Among
34 startup-without-submit JOINs, 5 report unavailable progress
after EOS+250ms and before the JOIN boundary. This matched
population differs from the lifecycle audit's 48 completed JOINs.
Capacity still overlaps; do not assign all 34 losses to expiry.

Keep normal EOS/body proof separate from the protocol-only fallback.
The evidence window derives from existing restore lead and semantic
identity lifetime, reaching 2s with the current lead. Anchor stage
expiry to the actual EOS timestamp, without renewal on recreation.
Require a live nonterminal child, no tool evidence, and intact
request/session/context identity at selection and final enqueue.
Non-EOS forecasts retain service/progress checks. Related 318 cases
pass, including delayed 300/750/1999ms batch enqueue and rejection
for expiry, tools and changed identity. Retain v130_v30_sealed_join_eos.

V31 now cold-starts from 5c1a646/7b4832ef with both fixes, predictive
only against fixed V29 native. The client starts at 12:55:22 CST;
health returns 200 and actual runtime records confirm a 2,000ms EOS
evidence window, physical_disabled=false and normal 48-request service.
Keep 108+48 arrivals, Host 200GB 80:20, HBM state/FULL 0.9, tool180s
and workflow14400s. Full coverage/throughput/JCT remain unverified.

V30 was administratively stopped at 12:49:22 CST after identity checks.
The independent-file cutoff snapshot observes 80 completed, 1 incomplete
and 75 censored workflows; preserve its written outcomes, frozen plan
and raw traces. Remove only the 75 containers whose /workspace binds
belong to V30. Archive all 156 stopped workspaces after GNU tar comparison:
38,681,817,088 allocated source bytes become 4,174,974,847 archive bytes.
Maintenance 12:56:26-12:59:24 CST overlaps early V31 service; qualify it
and local development beginning 12:41 CST in overlapping comparisons.

The first V31 attempt exits at NUMA preflight before model/workflow
service: old Host-pool capacity is not yet reclaimed after process exit.
Retain startup_attempts/attempt_1 and the original failed launch receipt.
Retry the same frozen plan only after the existing 200GB Host preflight
passes, without shrinking capacity or skipping the check. Retain the
separate retry state and deployment verification. CPU checks and normal
startup are not GPU coverage/throughput/JCT acceptance; the goal stays active.

V129 seals independently capped V30 JSONL prefixes read at
12:11:56-12:12:14 CST. Among 48 JOINs with observed completion,
38 record pre-boundary capacity-budget truncation, 38 enter the
start window, and 4 submit. Unavailable service progress occurs in
23 and busy admission budget in 31; reasons overlap. Among 3,794
complete tool episodes, 1,268 record capacity truncation and 12/11
enter/submit. Keep 69 unknown JOIN boundaries unknown. Verified
early FULL reuse is 2 JOIN events/17.224MB and 7 tool events/403.825MB.
This is a collecting prefix, not a completed-run rate comparison.
Retain v129_v30_sealed_prefetch detail/summary and per-input hashes.

The followup removes a reproduced FULL-only reclaim dependency.
For an eligible backed cold leaf in a FULL/Mamba tree, reclaim FULL
without first backing up the live unbacked Mamba state. Preserve its
device value, references and LRU; a simultaneous state-slot shortfall
still follows asynchronous backup and ACK. Unsupported component
combinations retain the existing path. Recheck locks, session sharing,
identity, pending DMA and target exclusions. The real CPU allocator
regression fails on the old unnecessary D2H, then passes with FULL
capacity returned and state reused after FULL restoration.
Related 157 native cases plus 27 subtests and 182 runtime/audit cases
pass. Canonical forward-tree/reverse validation matches SHA256
7b4832ef4e998d36aff6c91847194c50f3cc869bf3296d01bca43de212f5a162.
This does not isolate the dependency's share of V29/V30 losses or
prove GPU throughput/JCT benefit. Keep collecting V30 frozen;
validate the followup in a later predictive-only development round.

Retain v129_v30_v29_native_300_600 as a complete developmental window.
Observed steps are 345,167/344,185 and mean decode worker intervals
35.414/36.991ms, but matching 48-request/context buckets still show
about 2.1%-2.9% higher predictive intervals. Worker wall intervals
are not isolated kernels; coarse cohorts and differing demand are
not causal speedup. Python exclusive timing sums to 42.598s over
300s; continue reducing control costs without subtracting these
totals directly from JCT. Qualify local analysis beginning 11:52:47
CST and overlapping this window. Keep V30 on fca7ad3/1c3c7fc4 and
the fixed V29 native reference; the full performance goal stays active.

V128 compares completed V27/V29 lifecycle evidence. Early-started,
next-request FULL reuse drops from 90 to 25 JOIN events and from 479
to 17 tool events, or 9.581GB to 1.330GB combined. Model artifacts,
500ms lead, workload and physical settings match; code/engine patches
change. JOIN/tool waits increase from 194/18,476 to 197/20,269.
All 48 JOIN and 73 tool positive-FULL node commands issued by V29
have verified reuse; investigate selection/reclaim/enqueue losses,
rather than treating post-transfer failure as the primary cause.

Strictly before completion, tool startup events fall from 504 to 17
and FULL-only startup snapshots from 377 to 3. Of 1,239 V29 tool
events with a positive before_start_window plan, 293 subsequently
have a zero plan without detail, resembling budget truncation.
Old logs lack no_step_reason, so this is not a directly confirmed
capacity rejection count. JOIN startups increase from 129 to 147,
but pre-boundary submission falls from 92 to 25; 36 of the 122
unsubmitted startups subsequently report unavailable service progress.
These reason groups overlap and are not exhaustive causal partitions.

V117 now subtracts native next-prefill reserves in fit checks and
reclaim targets; do not equate old fit snapshots with new available
capacity. Validate queued V121/V126 timing alongside partial FULL
recovery and first consumption. Keep v128_v27_v29_prefetch_drop/funnel
evidence and input hashes. The offline analysis starts 11:35 CST;
qualify overlapping native windows. V29 native completes during this
analysis: 156/156 completed in 9,195.415s. V30 starts predictive-only
at 11:45:12 CST from fca7ad3, with V29 native as its fixed reference.
Commit this offline evidence/docs only in followup; keep collecting
V30 frozen. The throughput/JCT and attributable FULL goal stays active.

Current development cadence follows the October 12 user update. V127
commits cddd60d: default to a predictive-only cold collection against
a fixed historical native reference, rather than collecting native on
every iteration. Schedule V30 against completed V29 native, retaining
V126's 2,000ms lead and all workload/physical settings. Let the already
collecting V29 native finish. This supersedes the per-iteration fresh
native requirement in historical entries below.

Record the selected reference in the frozen predictive plan and carry
it across subsequent development runs. Produce native_reference_comparison
with version/configuration differences and reuse completed native metrics
when their bins match, avoiding repeated raw scans. Related 61 cases pass.
Use this comparison to screen improvements; refresh native after changes
to its service path, workload, capacity or harness/tool configuration,
and retain repeated paired runs for formal performance acceptance.
The throughput/JCT and attributable early FULL objective stays active.
Local V127 development begins 11:14 CST; qualify overlapping V29 windows.

V126 commits ce56b8f: use the live semantic remaining-work interval in
JOIN startup timing, converting the selected work endpoint minus the
progress-adjusted lower bound with the current decode rate. Initial
selection, after-reclaim planning and final enqueue share this rule;
an existing ticket keeps its selected endpoint. New tool evidence,
lost service or a narrowed interval can change the final decision.
Prediction remains about work/completion; runtime decides migration.

Of V29's latest positive before_start_window observations, 108 JOINs
have actual remaining P50 553ms and signed overestimate P50/P90
778/1,518ms. For 1,239 tool events these are 310ms and 9/331ms.
These errors pair a recorded prediction with retrospective completion;
future boundaries are never online inputs. Keep v126_v29_trigger_gap.

Test the next V30 pair with a 2,000ms maximum residency lead instead
of 500ms, rather than subtracting one constant from every prediction.
JOIN uses its own live work interval; tool timing retains queued V121
conditional P50-P10. Measured transfer costs, current capacity and
near-term admission still apply, and a partial early overlap is allowed.
Related 365 cases pass; actual DMA, first consumption and GPU/JCT
improvement await the new pair.

Capacity is a distinct constraint: fitting observed snapshots in the
actual last 500ms/2s occur in 38/61 JOINs and 39/67 tool events.
Do not treat positive plans as continuously available transfer volume.
Include V126 with V120-V125 in the unstarted immutable V30 candidate.
Keep V29 execution/configuration frozen, predictive first then cold
same-version native. Local development begins 10:43 CST; qualify
overlapping native windows. The performance objective stays active.

V125 commits 23ad8a5: separate strictly pre-completion physical plan
snapshots from post-boundary native demand. Signed timestamps refer to the
complete JOIN/all-tool END, not model error; exactly-at-boundary observations
are reactive. Reuse completed lifecycle identities and stream the opportunity
log once without adding scheduler telemetry or rescanning transfers/GPU logs.
Related 68 audit cases pass. Keep v125_v29_wait_plan_timing event evidence.

Complete V29 contains 164/197 JOIN events with pre-completion positive FULL
plans (3.506GB summed maximum snapshots), but only 25 early reuse events
(0.675GB). Tool waits have 1,416/20,269 positive pre-completion plans
(113.891GB maximum snapshots); only 95 events have an explicitly fitting
snapshot (2.542GB), and 17 yield early verified reuse (0.654GB).
Of the positive tool group, 1,239 record before_start_window before completion;
reasons overlap. These snapshots are not transferable volume, a speedup
upper bound, or proof that all plans could finish before the boundary.

There are 16,553 tool episodes with only zero pre-completion plans and
316.676GB subsequent native FULL Host hits, plus 60 episodes with positive
plans only at/after completion. Do not treat later evictions or demand
restoration as available early Host-only checkpoints. Keep 107.091GB
handoff FULL separate. Validate queued V121/V126 timing together with
V120-V124 CPU changes before accepting coverage or throughput improvements.

Continue both reducing control cost and improving causal event exploitation:
JOIN progress/terminal sibling capacity, long-tool parking, actual missing
pages, timely restoration and admission/first consumption. Completion/work
prediction stays in the model; runtime decides transfer feasibility. V30
retains the queued runtime changes and includes V125 attribution and V126 timing;
collecting V29 remains frozen. Native is incomplete; its first 300-600s
window's +6.93% predictive decode is not the full throughput/JCT result or
an early-prefetch attribution. Local analysis begins 10:27 CST; qualify
overlapping windows. The successful-workflow performance goal stays active.

V124 commits bc33cb8: lazily reuse waiting-context candidates by graph and
native-session binding revisions for JOIN PREPARE, tool refresh and unhinted
tool scans. Keep context ordering for parents and session insertion order for
tool refresh. Register/requeue/abort/terminal cleanup and graph discard
invalidate membership; physical headroom, closure and enqueue remain live.
Do not extend physical observation lifetime or change the transfer policy.

Related 407 cases pass. Eight complete-path CPU fixtures preserve PREPARE
actions, tool refresh rotation and pressure-node publication. Combined
maintenance/PREPARE thread CPU falls 6.56%/4.86% for 156 workflows with
unbacked/backed inputs and 1.96% when the graph changes every step. The latter
maintenance subphase increases 9.51%, or 3.97us; the 24-workflow combined
saving is only 0.23%. Reject eager construction of both classes, retaining
its first-attempt evidence. Keep v124_wait_candidates_cpu and first_attempt.
Actual checkpoint planning/headroom helpers run, but model submission and
native enqueue are stubbed; no allocator/DMA/GPU/JCT improvement is proven.

Complete V29 attribution separates 18,775 tool events with only zero FULL
plan observations and 351.592GB subsequent native FULL Host hits from 1,494
positive-plan events and 377.437GB residual hits. Check observation timing,
available checkpoints and subsequent residency before treating those bytes
as anticipatory opportunity. The 1,292 before_start_window episodes overlap
other reasons; validate queued V121 timing in real service. Early verified
FULL remains 675.144MB JOIN plus 654.356MB tool; 107.091GB handoff FULL is
demand restoration. Both control reduction and useful early coverage remain
required for the active successful-workflow throughput/JCT objective.

Local development starts 09:53:25 CST; qualify overlapping V29 native
windows. Include V124 with V120-V123 in the next immutable cold candidate,
predictive first then same-version native. Leave collecting V29 unchanged.

V123 commits 941156c: classify exact and compatible transfer support in one
scan, skipping known foreign pool shapes before size-distance calculation.
Preserve unknown and legacy-tuple support, minimum support, encounter order
and every estimate field. Related 408 cases pass. All 120 comparisons on
fixed recorded histories return identical outputs. Four actual 256-sample
histories reduce equally weighted thread CPU about 17.1%-19.2%; the legacy
fixture improves about 1.6%. Individual unknown/FULL queries regress by up
to 15.6%/8.6%; do not claim universal savings or GPU/JCT improvement. Keep
both the initial and final v123_transfer_estimate_cpu measurements.

V29 predictive completes all 156 workflows in 8991.395s; same-version native
is collecting. The first complete 300-600s window yields 1219.89/1140.88
decode token/s (+6.93%), 87.315%/84.712% GPU utilization and
565.544/634.388GB total H2D (-10.85%). Only 4.321MB FULL carries JOIN/tool
tags; 17.003GB handoff FULL remains demand restoration. This does not
attribute the throughput difference to anticipatory prefetch. Matched
single-request prefill with actual extend 0-511 tokens remains 1.476-1.921
times slower; matched graph decode at batch 48 is 1.0035-1.0241 times.
Exclusive profiled Python wall totals 41.441s/300s, not removable JCT.
Keep v123_v29_same_version_pipeline and busy reports; await full successful
workflow throughput/JCT before accepting the native-performance objective.

Local V123 development begins at 09:19:18 CST, after native's first
300-600s window. Qualify later overlapping windows. Keep implementation
and reports in followup; only docs may enter collecting main. Include V123
with V120-V122 in the next immutable cold-start candidate, predictive first
then same-version native. Preserve V29 execution and frozen configuration.
The full performance objective remains active.

V122 commits7749e32: reuse identities already parsed in the same synchronous
admission call, and read the identity once when constructing reentry anchors.
Live bindings, invocation state and context epoch remain checked. Resident
ordering needs no physical probe for a singleton unaged causal group; selected
handoff still inspects its checkpoint. Cache pure group cardinalities with the
existing versioned ranks, subtract aging only in a local copy, and preserve scan
rotation. Keep the50ms physical observation expiry and fresh enqueue validation.

Related558 unique cases pass. Twelve alternating CPU fixtures use the actual
native inspector/RadixKey,60ms clock steps and changing mixed residency; complete
queue order agrees, with only singleton-group probes skipped. Two-call48/156
request plans reduce mean thread CPU about0.9%-3.1%; a four-candidate mixed group
improves17.45%, singleton queues61%-92%. The512-request case increases about0.15%
and is effectively flat. Keepv122_reentry_reuse_cpu_final. This is measured local
CPU work, not allocator/DMA/GPU throughput or JCT evidence. Include this revision
with V120/V121 in the frozen waiting V30, after complete V29 predictive/native
and postprocessing. Do not restart V29 for this modest CPU optimization. Qualify
V29 windows overlapping local development from08:07:42 CST to the registration
receipt's local_development_ended_at. The full performance objective stays active.

V121 commits19a83b1: include survival-conditioned tool remaining P50-P10
in the service/enqueue/observation start window, capped by the existing500ms
residence allowance. Initial selection, post-reclaim planning and final
enqueue use the same calculation. Preserve causal drain, fresh physical
prefix validation, native FULL/Mamba reserve and transactions. JOIN does not
inherit the tool margin. Record prediction_early_ms separately from physical
service time. The actual observation interval constant is100ms; the previous
approximately165ms horizon is an observed combined value.

Related596 checks pass. Sixteen same-input CPU fixtures change formerly
rejected300/380ms tool opportunities with P10=100ms into four-node bursts
when free/reclaimable, one continuous FULL extent when only partial capacity
fits, and zero when unavailable. Point forecasts and already actionable80ms
cases retain their payload. Window thread-CPU P50 increases from about25 to
45us; this is added computation, not a performance saving. Native allocator,
headroom and enqueue are mocked. No DMA/ACK/early reuse/GPU/JCT gain is proven.
Keepv121_tool_prefetch_window_cpu. Freeze V30 with V120/V121 and queue it
after the complete V29 predictive/native pair and postprocessing; predictive
first, then same-version cold native. Do not change collecting V29. Verify
actual reclaim, early FULL reuse and service throughput before accepting
the timing policy; the full native-performance goal remains active.

Losslessly archive the76 remaining censored V16b workspaces to3,202,382,046
bytes; GNU tar comparison passes before source removal. Remove76 containers
verified by their old /workspace mounts. Source allocation24,619,732,992
bytes yields about19.95GiB net reclamation; keep traces, outcomes, plan and
restore pointer. Maintenance07:45:42-07:47:55 CST overlaps V29 and must be
qualified in performance windows. Free space at verification60,490,625,024
bytes includes concurrent experiment writes. Keepv121_v16b_workspace_archive.

V120 removes a duplicate JOIN plan capture within one dispatch. Return the
new stage plan directly; enqueue still drains causal events and refreshes the
physical prefix, capacity and timing, with native per-node validation and
transaction reservations unchanged. Record empty plans after reclaim and
retain no_step_reason in independent wait attribution. Related checks pass:
301 runtime/restore checks and 110 audit/batch checks, with overlap. Eight CPU
cases preserve selected plans and ticket state while captures fall from three
to two. Mean thread CPU falls about24%-35%; depth64/four leaves is6.334 to
4.676ms. Decision time is fixed, read-only closure/planning are real, enqueue
revalidation is mocked. No allocator/DMA/GPU or throughput/JCT evidence.
Retain v120_join_dispatch_cpu; the collecting V29 remains frozen.

The07:20:48-07:21:24 CST sealed-prefix audit observes95 independent JOINs
with start-window records;13 submit21 node commands. Twelve events start
early and reuse376.996MB FULL. Fifteen tool events reuse639.918MB early;
95.894GB handoff FULL reuse remains demand recovery. Per-event last-ACK-
to-service P90 is541.163ms for JOIN and1565.680ms for tool.22 JOIN completion
boundaries remain unknown. Later planning reasons overlap; frozen V29 lacks
empty-plan details, so do not invent exclusive rejection causes. Keep the
compressed event/action and JOIN-plan rows, input lengths/SHA256 and final
runtime-state row in v120_v29_sealed_prefetch_lifecycle. Its independent
prefixes are not atomic.721.439GB next-request native tool FULL Host hits are
not an oracle denominator. Of1407 positive-FULL-plan tool events,1228 record
before_start_window; investigate the intersection of forecast residuals,
actual start horizon, live capacity and admission before selecting a policy
change. Qualify windows overlapping this audit/CPU development. The full
same-version native performance goal remains active.

V120's same-prefix tool-window analysis selects only recorded online fields:
positive FULL plans rejected before_start_window with forecast0-500ms.
Sixteen independent events fit the current free lists, totaling537.293MB;
forecast remaining P50 is388.938ms versus an actual start horizon P50 of
167.668ms. True lead P50 is484.391ms: nine within0-500ms, five500-1000ms,
two over1s. Three already have early reuse at another observation. The
reclaim-unverified group has1207 events and99.433GB planned;887 have true lead
0-500ms and51 are observed after their completion boundary. Keep
v120_tool_start_window_evidence. This is observed candidate analysis, not
closed-loop policy/DMA/reuse or gain. Capacity groups can overlap within one
episode and must not be added. Verify reclaim, protection and service
consumption together before treating the existing500ms allowance as usable.

V119 retains V29's complete300-600s pipeline window against historical
V27 native as cross-version development evidence. Matched48-request
CUDA-graph decode mean ratios are0.9963-1.0028. Single-request small
prefill with0-511 actual extend tokens remains12.8-14.7 versus6.0-7.4ms;
the groups followed by48-request decode are1.49-1.87 times slower.
All9,951 service samples carry raw launch; mean launch-to-processed
completion50.855ms and clipped-start-minus-launch20.707ms are pipeline
observations, not kernel or removable scheduling cost. Historical native
has no raw-launch field. Exclusive profiled Python wall totals41.441s/300s,
not thread CPU or removable JCT. Preserve exact prefix hashes/boundaries.

The06:54:01-06:54:20 CST independent fixed-prefix lifecycle audit observes
113 JOIN and3,816 tool events.48 JOINs record a start-window candidate,
but none submit predictive H2D. Eight tool events start early and reuse
436.859MB FULL;69.231GB handoff FULL reuse remains demand recovery.
Tool last-ACK-to-service P90 is658.250ms.63 unobserved JOIN completion
boundaries remain unknown;537.037GB next-request native FULL Host hits
are not an anticipatory denominator. Keep compressed event/action detail,
all input lengths/SHA256 and the terminal runtime-state row. Cross-file
prefixes are not atomic. Retain V29 frozen; investigate candidate-to-submit
loss with actual timing, capacity and target evidence before selecting a
fix. No new implementation or GPU performance gain is claimed by V119.

V118 executes the authorized development restart. Stop V28 at06:27:24
CST with112 completed and44 administratively censored workflows.
The verified controller/client/server exit; remove only the44 containers
whose workspace mounts belong to V28. Preserve the frozen plan, traces,
written outcomes and stop receipt. Retain the superseded V29 waiting-pair
registration/log; its historical receipt no longer describes current state.

V29 cold-deploys92e47d7 at06:28:01 CST, including V113/V115/V117.
At06:34:36 verify the actual server/scheduler/client identities, health200,
submitted requests, GPU service samples and raw launch_monotonic_ms.
Deferred observation call sites and JOIN/tool/PREPARE flags are active;
writer error/dropped/failed records are zero. Engine tree matches patch
1c3c7fc4; frozen plans differ only in code_commit and patch hash.
Keep predictive first, then same-version cold native, with unchanged
arrivals/workload/model settings. Preserve
v118_v29_cold_restart_verification_20261012.json and the immutable target.

Losslessly archive all156 stopped V28 workspaces to4,289,396,535 bytes,
compare with GNU tar before removal, and keep the raw restore pointer.
Source allocation38,987,358,208 bytes gives about32.3GiB net file reduction.
Archive maintenance at06:29:52-06:33:04 CST overlaps V29 loading/initial
service; qualify those windows. Full same-version busy throughput/JCT,
attributable early FULL reuse and residual native recovery remain active
acceptance criteria. Deployment and cleanup are not performance gains.

V117 makes wait planning, cold reclaim and enqueue use the same native
prefill/decode reserve. Subtract the reserve from the direct-fit verdict
and include it in the desired free capacity for bounded cold reclaim.
Preserve target exclusions, prefix continuity and deferred-Mamba loads.
The430 related checks pass. Eight CPU fixture variants compare4a644d2:
with20 FULL/1 Mamba free,8/1 reserved and16/1 to restore, the old
direct-fit verdict leads to zero commands; revised reclaim targets24/2,
submits four nodes and keeps8/1 reserved. Already-fitting variants are
unchanged. Keepv117_wait_prefetch_reserve_consistency_20261012.json;
no allocator/DMA, GPU throughput or JCT gain is measured.

V116 captures223 independent V28 JSONL prefixes at06:00:29-06:01:01
CST, retaining complete lengths/SHA256 and the terminal control row.
Eight JOIN and eight tool events start early and reuse269.496/182.006MB
FULL;96.821GB handoff FULL reuse remains demand recovery. Last-ACK-to-
service P90 is738.282/808.372ms. Tool next-request native FULL Host
hits total619.726GB, not a proven anticipatory denominator or oracle.
The recorded control prefix has462 changed-plan rejects, without each
reject's detailed cause; do not assign all of them to V117. Keep unknown
completion boundaries and cross-file non-atomic scope. Replace only the
still-waiting V29 registration to include V117, retaining its prior974b3db
target/state/log. The full same-version performance goal stays active.

V115 moves periodic opportunity, terminal-cache and runtime-state
observations to batch-result processing. Native overlap submits the next
forward first; idle selection still samples the current waiting queue.
Keep causal event handling, prediction polling, transfers, leases and
admission in their existing phase, with unchanged observation cadence
and records. V28's complete300-600s window records293 opportunity
samples costing1.975s; this cumulative wall time is not a JCT saving.
The242 relevant runtime/engine checks pass, as do105 telemetry and20
analysis checks. All24 CPU cases preserve observations, policy counters,
pending state and semantic polling cadence. Per-cycle total thread-CPU
deltas range -0.417 to +0.897us; retain the regressions. The dispatch
boundary is simulated, with no GPU or measured overlap/throughput.
The isolated engine content tree matches the new canonical patch.

Export existing batch.launch_ts as launch_monotonic_ms without a clock
read or GPU synchronization. It marks scheduler.run_batch entry before
telemetry/worker dispatch, not CUDA submission or kernel start. Preserve
the clipped v1 service interval and distinguish full/partial/missing raw
coverage in the existing analyzer; never substitute legacy service starts.
Keep V28 frozen. Revalidate and replace only V29's still-waiting
registration with a new immutable V113-V115 target, retaining old state,
logs and checkout. Full same-version throughput/JCT and attributable
early-FULL reuse remain the active acceptance criteria.

V114 diagnoses V28's complete 300-600s window against prior V27 native.
Only code_commit differs in the frozen plans; the engine patch matches.
Retain 7680/8130 decode and 2207/2275 prefill batches with actual geometry.
Small-prefill cohorts followed by 48-request decode still average
9.15-11.60/5.73-7.00ms. Exported service_start_monotonic_ms is the maximum
of launch and previous processed completion, not raw launch. Contiguous
neighbor boundaries follow from this clipping and cannot split kernel,
admission or result-processing cost. Keep that limitation explicit in
the existing analyzer; preserve prior geometry/control evidence. The 17
existing analyzer checks pass. V28 stays frozen, V29 remains waiting,
and the full same-version throughput/JCT and early-FULL goal is unproven.

V113 commits 11c8258. Coalesce only already queued control messages,
without a batching timer. Preserve original handler/transaction boundaries,
FIFO, event timestamps, per-submission completion, partial-prefix ACKs,
idempotent retries, flush/close and the existing 64-event/60000-byte limits.
The 73 related checks and two subtests pass. V27's 156 predictive result
summaries have a median per-workflow reentry queue P95 of112.025ms and
ACK P50 of17.306ms; these are not pooled event quantiles. Five alternating
CPU trials per case preserve original batches. With simulated20ms ACKs,
four pending batches use5→2 messages and101.149→40.751ms;32 use33→2
and670.342→43.277ms. Large text requires four messages; retain the
7-9us single-pending cost and receiver codec overhead. Fixture results
are not GPU/JCT gains. Cold-deploy with the prior isolated optimizations;
keep the full same-version throughput, JCT, early-FULL and residual-native
objective active. Do not modify collecting execution source.

At05:01:25 CST, V28 cold-deploys141d3df and starts its frozen pair.
V27 native completes156 workflows over8279.264s; the prior coordinator
and server/scheduler are gone. Retain collecting V28 unchanged and
queue V113 for a new V29 cold pair, predictive first then same-version
native. The V28 registration is no longer a replaceable waiting target.

At05:09:32 CST, V29 waiting coordinator1002829/start ticks592315475
is live, waiting for the verified V28 coordinator883082. Its clean
frozen target isd928da4, descended from main141d3df with unchanged
canonical engine patch. Preservev113_v29_queue_registration_20261012.json;
do not advance that registered target. Completed V27 still misses the
goal:155 paired-completed mean JCT2969.228/2483.133s, with100 slower
predictive workflows. In600-2400s, completed-request output rates are
1130.054/1201.674token/s and prefill worker intervals320.683/286.702s.
Actual trajectories/demand differ; later native also overlaps maintenance.
These are diagnostic evidence, not exact control-cost attribution.

V112 commits fa6f6c8. First-service prefetch and background same-session
prefix audits compare long q-arrays without temporary token slices, using
native RadixKey's GIL-retaining memory comparison. Retain exact token
divergence, short/different-type array behavior, immutable snapshots and
all physical FULL/Mamba reuse checks. The 122 related checks pass.
Thirty-two first-service and 36 writer CPU cases preserve records/state,
with 100 alternating trials each. Matching 32K/128K, 16-command means
fall 0.472/1.198 to 0.341/0.657ms; the 32K array writer falls 0.099 to
0.069ms. Keep short-array costs and the 8K/16-command mean regression.
The complete V27 600-900s window records 4218 prefix comparisons across
1040 requests, median 14080 tokens. Geometry is not a measured CPU/JCT
saving. Include V112 in the cold V28 target; keep live V27 frozen and
the full throughput, JCT, early-FULL and residual-native objective active.

The 76 stopped V19b workspaces now have a lossless 2.81GiB archive.
GNU tar compared the original files before removing uncompressed copies;
raw traces, outcomes and models remain available with a restore pointer.
Archive/CPU maintenance after 04:08 CST overlaps later V27 native windows.
Preserve this qualification when reporting late-window performance.

V111 commits efcda87. In the same-version V27 600-900s window,
all 2030/2159 Host-hit records repeat three identical identity paths.
Read an identical integer anchor once within that first-service
observation; refresh on the next observation. Preserve record fields,
node identity and separate physical reuse proof. The 104 related
checks pass. Twenty CPU on_launch cases, 100 alternating trials each,
preserve records and service state. Same-anchor means at depths
24/64/185 fall from 0.071/0.134/0.337 to 0.045/0.071/0.133ms.
Retain small distinct/missing-anchor overhead and no-hit mean outliers.
This does not explain all small-prefill delay or prove GPU/JCT benefit.
Add V111 to V28's frozen cold target without changing live V27;
the full throughput, JCT and attributable early-FULL goal stays active.

V108 obtains complete same-version V27 windows300-600 and600-900s.
Both arms have actual native prefill geometry; matched cohorts number
13 and12. In600-900s, actual extend is1.633/1.736 million tokens while
worker intervals total48.00/37.99s. Single-request cohorts below512
extend tokens have predictive/native mean ratios1.90-2.72. Matched
48-request decode is nearly equal;47-request bins remain slower.
All observed decode flags indicate CUDA graph replay. These intervals
include overlap/result processing, not just kernels. Prioritize actual
admission/restore costs; do not claim an identical-demand slowdown or
subtract worker/control time from JCT.

V110 commits49fb704. Native selection rechecks current fields and
reuses unchanged planned identities, retaining changed-attempt/session/
epoch and stale-plan handling. The179 relevant checks pass. All16
complete-selector CPU cases preserve selection and rejection reasons,
with1000 alternating trials each. Matching156/512-request means fall
0.463/1.258 to0.304/0.815ms; identity allocations fall156/512 to zero.
Queue V110 together with V101/V104/V106/V107 in cold V28, predictive
first after V27's same-version native. This local saving does not explain
the full remaining worker difference or establish GPU/JCT improvement.
V109's list conversion is reverted: current native input storage is
already array, and some list-divergence cases regress. Retain its report.

V107 commits61f415d in the isolated branch. Pressure maintenance
deletes only rejected nodes in place and reuses the current scan's
key verdict for adjacent extents. Refresh leases first; do not cache
liveness across scans, conflate attempt/epoch identities or reorder
candidates. The294 related checks pass. Existing PREPARE fixtures run
100 alternating trials per case. With156 workflows and backed24/64-depth
ancestry, no-lease means fall5.840 to4.622 and20.769 to12.181ms;
selection, publication and node-read counts stay equal. Empty candidate
paths are nearly unchanged. Host-only sampling means increase9%-13%
although the sampling code is unchanged; retain these negative results.
No GPU or JCT benefit is proved.

At02:43 CST, V27 predictive is terminal with155 completed and1 incomplete,
over9812.319s. The incomplete remains django__django-12325; terminal
telemetry has zero loss, failure or backlog. HTML/workspace archival
is complete. Same-version native starts its server at02:36:34 CST
and is serving with live handles. Keep V27df3dd3c frozen; cold-deploy
V101/V104/V106/V107 together in V28. Replace the waiting target when
syncing new main docs, preserving main's fast-forward ancestry and
the previous queue receipt. The full performance goal remains active.

V106 commits793e8ba in the isolated branch. Restore a fitting missing FULL
checkpoint extent without its necessary Mamba state when the state slot
or combined byte payload does not fit. A state-only step still waits.
Apply actual free-list limits after bounded reclaim, preserving ancestor
continuity and prefill reserves. Fresh enqueue validation retains the
selected FULL-only mask even if state capacity grows; timing and receipts
use that actual payload. Do not treat a FULL ACK as complete state
readiness. Keep deferred_mamba_slots in plan evidence. The511 related
checks and45 native checks pass, with27 native subtests. Eight CPU fixture
cases show12 to16 FULL units restored when16 fit but no Mamba slot does.
These are functional/plumbing results, not allocator/DMA or throughput
evidence.

Retain319 fixed source-prefix byte lengths/SHA256 from02:02:05-02:03:12
CST; repeated reads verify the prefix, without atomic cross-file claims.
Observed early-and-reused FULL is1.553GB/90 JOIN events and8.028GB/479
tool events. Last-ACK-to-service P50/P90 is355.32/1253.18 and
415.98/1017.78ms. Keep124.344GB handoff FULL reuse separate as demand
restoration. Mamba-only shortfall occurs in108 JOIN/16 tool events,
whose per-event maximum FULL snapshots sum2.005/0.530GB. Conditions
overlap; these are not proven extra opportunities or an oracle limit.
Investigate23 JOIN FULL commands/303.022MB expiring before completion.
Use the V106 sealed summary as reproducible evidence; V105 is an
unsealed temporary CLI diagnostic.

Separate02:06 CST reads observe153 completed and1 incomplete, with two
tasks unfinished and the four original handles alive. The incomplete
django__django-12325 has a length-truncated repetitive root response,
two natural child returns and a satisfied JOIN, with no cancellation
or workflow deadline expiry. Continue V27df3dd3c through its same-version
native, then cold-deploy the isolated V101/V104/V106 changes, predictive
first under the same frozen settings. Verify actual early reuse and
work-normalized service costs before claiming the full performance goal.

V104 commits0b26516 in the isolated branch. Handoff and admission share
resident scan targets within a scheduler cycle; queue order, request
identity and graph/semantic changes invalidate the selection. Each new
cycle rotates again. Keep physical observations, aging and restore
priority live; all-aged queues keep their arrival order without another
residency sort. The286 relevant checks pass. Six100-trial alternating CPU
cases use the actual native inspector and RadixKey on synthetic Host-only
ancestry. At156 requests, inspections including warmup fall2432 to1632;
two-plan means fall2.358 to1.977,4.141 to3.153 and11.810 to8.301ms for
32K/8-depth,96K/24-depth and32K/256-depth. All-aged means fall0.839 to0.754ms;
four-request means rise0.291 to0.294ms. Preserve initial and final receipts.
Mixed-residency second-call sampling changes intentionally; only Host-only
and aged fixture orders are proved equal. No allocator/DMA/GPU or JCT
benefit is established. Do not interrupt V27 for this local change alone.

V103 confirms the old native prefill attribution limit: all7303 V27
prefill batches in600-1800s have native geometry, with7,254,415 actual
extend tokens. All7248 older-V23-native batches have unknown work, leaving
zero matched cohorts. Submitted uncached prompts are not actual batch
work;214.60/167.29s worker intervals do not prove a same-work service-path
slowdown. Obtain actual geometry from the subsequent same-version native.
Separate01:38:22 CST reads observe133 completed, four live run handles and
zero telemetry failures/loss with an empty writer queue. Keep V27df3dd3c
frozen and the full performance goal active.

V102 adds complete, profiling-free V27/older-V23-native windows600-1800,
2400-3000,3600-3900s with decode differences-2.79%,+9.05%,-1.99%.
In the busy window, prefill worker wall is214.60/167.25s and submitted
uncached prompt work7.254/6.812million tokens; service-work attribution
is not established. Separate01:00:44 CST reads observe88 completed and
zero telemetry loss/failure. Preserve the same-version comparison.
The driver archives and cleans each terminal arm before the next arm;
keep the37GiB active workspaces until their archival conditions hold.

V101: commit68e656c stops scanning a pool index once its native-consumed
pressure-candidate prefix is full. The other pool retains candidate order,
live lock/residency checks and pending-transfer exclusion. The118 relevant
checks pass; six100-trial alternating CPU cases retain identical native
prefixes and node lookups. With156 candidates,4096 unrelated FULL leaves
and sparse Mamba, publication means fall0.423741 to0.051102ms; the existing
24-depth cases show no mean regression. This is a local CPU improvement,
not GPU/JCT evidence. Keep V27 frozen; this small change alone does not
justify another censored run.

V100 preserves complete V27 windows300-600,600-900,900-1200s:
decode1142.94,1126.97,1011.38token/s, GPU83.45%,85.22%,82.06% and weighted
batch about47. Instrumented exclusive Python wall in the latter windows
is42.668/41.613s; reentry remains5.970/6.370s and candidate publication
1.238/1.159s. Same-version native is still required.

The00:22:42-00:23:03 CST fixed, separately sealed prefixes prove advance
FULL first reuse for428 tools/7.608GB and35 JOINs/410.952MB. Preserve46
unknown JOIN boundaries. Last-ACK-to-service P50/P90 among39 JOIN and472
tool events is348.69/1555.99ms and414.78/996.50ms. Investigate19 JOIN
FULL commands/251.843MB expiring before completion without a blanket TTL
increase or fixed-seconds prediction shift. Residual native tool FULL
Host hits500.795GB are not proven advance opportunities or an oracle bound.
Keep99.690GB demand-handoff FULL reuse separate. Of697 PREPARE ACKs,156
link to an observed restore and541 are not yet linked; neither pending
observation nor an unlinked backup proves waste.

Retain V100 CPU/control, source-prefix seals, profile failure and successful
raw stacks. Full lifecycle details are losslessly compressed and the
uncompressed SHA256 is pinned in the summary. Continue reducing measured
hotpaths and examining independent wait opportunities while V27 collects
its frozen predictive/native pair. The full performance goal remains active.

V99: compare interrupted V26 predictive with the latest completed V23
native using complete monitor-aligned windows300-600,600-2400 and
4200-4800s, excluding V26 stack profiling. Decode throughput is
1065.18/1246.94,1086.56/1113.47 and922.63/1102.59 token/s:
predictive remains14.58%,2.42% and16.32% lower. Both weighted decode
batches remain about47. In600-2400s, total H2D is1.723/2.391TB, but
prefill worker wall is336.57/271.87s. Keep5.661GB JOIN/tool tagged FULL
separate from66.866GB demand-handoff FULL; source tags alone do not
prove anticipation and reuse.

Workload, arrivals, capacities and model artifacts match, while code,
engine patch and realized traces differ. Retain50 administratively
censored V26 tasks; do not compare its106-completion subset with native's
full156-workflow JCT. The rough result does not meet the performance goal.
V27 continues frozen collection before its same-version native.

The existing body_progress_v2 candidate keeps phase scores identical in
the sealed V26 replay. Median absolute token error on46 natural pre-EOS
last snapshots falls39.04 to30.19; CPU batch P50 is116.70/116.45ms.
However,500ms first triggers rise5 to7 with zero0-500ms hits in either
arm; all7 candidate triggers lead by more than2s. Retain development
evidence without deployment. No project-disjoint validation, allocator
replay or candidate delivery-latency simulation establishes useful H2D.

V98: commit8f8ce45 replaces strict background record serialization with
ujson5.12.1, retaining decoded fields, ASCII escapes, nonfinite rejection,
queue and ACK/use flush semantics. Identity/HMAC serialization is unchanged.
The236 relevant checks pass. All259 real records decode identically;
60 alternating CPU trials reduce combined encoding means from2.916485
to1.267997ms,56.5%. This does not measure filesystem, queue or GPU/JCT
savings. Cold-deployed with553fa7c in V27 frozen df3dd3c.

V26 was administratively stopped at2026-10-11 23:43:32 CST after106
written completed outcomes;50 started unfinished tasks are censored,
zero tasks are unarrived and native never started. All old processes
exited and GPU allocations were verified released at23:44:42 CST.
Retain plans, raw evidence and50 partial workspaces; remove106 archived
workspaces and50 containers proven by V26 bind mounts. Available disk
recovered from about52 to73GiB before the new initial workspaces.

V27 cold-started at23:45:30 CST, predictive first then same-version cold
native. Plans differ only in code_commit; V7 and the canonical engine patch
are unchanged. Coordinator/server/scheduler/client are1060398/1060657/
1061045/1062524. The23:48:59 CST independently sealed prefixes verify599
real GPU service samples, health200, writer_error=null and zero dropped/
failed records. Disk after new workspaces is53.35GiB. Main
v27_launch_after_v26_20261011.json is authoritative,running_new_pair.
Retain V98 stop/cleanup/start receipts. Busy throughput/JCT and early
FULL consumption still require validation; the full objective stays active.

Reject deployment of the latest128-token work-only candidate5c98ca9.
Reconstruct8404 of8406 sealed V26 inputs, keeping2 missing-content records.
Phase scores are unchanged, but46 natural pre-EOS last snapshots worsen
median absolute token error39.04 to43.19;4-item CPU batch P50 rises129.85 to191.43ms.
The500ms sampled replay yields5 triggers for each model and zero0-500ms
hits. Host/capacity and extra candidate latency were not replayed.
Retain V7. Original training has10310 notice-active snapshots/200 requests,
but selector/interval/evaluation303/356/445 snapshots all lack notice-active
examples. Next, collect project-disjoint announced final-report calibration
coverage, retain phase stability and compare conditional remaining work
and causal rolling lead. Do not apply a blanket fixed-seconds shift.

The20s nonblocking GIL profile has470 valid samples and101 read errors:
390 main-thread,80 background-thread samples and50 standard iterencode
leaf frames. Retain raw evidence. Conservatively exclude2026-10-11
23:22:25-23:22:54 CST from primary performance windows; the exact start
was not separately logged. Do not extrapolate samples to total CPU/JCT.
V98 reports remain evidence, not completion of the active performance goal.
The V97 and earlier deployment entries below describe historical states.

V97: commit553fa7c checks existing native locks before capturing a restore
closure. A same-key exact target or descendant lock covers the FULL prefix;
new/deeper locks still capture the complete closure, check the live budget,
acquire the descendant before releasing ancestors and retain every receipt.
The220 relevant checks pass. All12 CPU cases,60 alternating trials each,
retain identical records, locks and counters. At depth64/512, same-node
coverage means fall from0.5520/2.5895 to0.0674/0.0619ms. Preserve extension
costs0.4337/2.5862 to0.4442/2.6267ms. No GPU, allocator, admission or
end-to-end savings are measured. This revision is not deployed in V26.

Complete V26 300-600/600-900s windows show1065.18/1077.11 decode token/s,
GPU77.64%/80.57%, weighted batch47.205/47.304 and prefill worker intervals
44.319/49.667s. Instrumented exclusive Python wall remains46.049/44.013s;
the latter does not improve on the old V25 reference43.732s. The600-900s
native geometry records1,571,106 extend tokens, mean decode worker36.777ms
and mean request context22,649 tokens. Different revisions and realized
demand remain diagnostic references; same-version V26 native has not begun.

The22:43:30 CST separately sealed lifecycle audit confirms advance-started,
next-request-reused FULL for307 tools/4.738GB and20 JOINs/152.248MB.
Total tool/JOIN FULL first reuse is5.355/0.158GB; ACK-to-service P90 remains
736.88/2955.47ms. Keep65 unknown JOIN completion boundaries,73.119GB
demand-handoff reuse and370.335GB residual tool native Host hits separate.
Retain V97 CPU/window/lifecycle receipts. Continue frozen V26 collection,
inspect JOIN tails and shared costs; the development-restart option remains
available for important committed fixes and the full objective stays active.

The same sealed prefix contains8 nonreused JOIN FULL commands across3
JOINs, actually started1.578-15.157s before complete JOIN. All have observed
roughly1.5s residency expiry; corresponding native-arrival-to-service is
only116-427ms, with one client-to-native interval866ms. Do not attribute
these tails entirely to parent admission. Inspect underestimated remaining
child work and repeated early recovery together; expiry is an association,
not a demonstrated sole cause. Retain the V97 JOIN-miss diagnostic receipt,
and avoid an arbitrary fixed-seconds adjustment to the forecasts.

V96: optimize the synchronous first-service proof for native token arrays
using array comparisons and divergence search; retain the list shared-prefix
walk, independent ACK snapshots and FULL/Mamba allocation identity checks.
Commit741d9d4 passes85 telemetry checks. All48 alternating CPU cases retain
identical records/consumed commands. Matching32K single/16-extent means fall
from1.4742/1.9879 to0.0561/0.4277ms. Retain the8K/16-extent list cost
0.7728 to0.8256ms and the unsuccessful eager-list-conversion report.
ACK prefix materialization remains separate. Do not extrapolate CPU fixtures
to throughput or JCT. V25 was administratively stopped at22:13:58 CST:
52 written completed outcomes,56 started unfinished tasks censored and48
unarrived tasks kept separate; native never started. All old processes/GPU
allocations were verified absent at22:15:24 CST. Preserve frozen sources,
raw evidence and56 partial workspaces; remove52 archived workspaces and56
containers whose mounts belong to V25. Post-stop drain/reset/exit is an
administrative effect and primary performance windows end before the stop.

V26 cold-deployed frozen2d25850 at22:15:26 CST, including741d9d4.
Plans differ only in code_commit; configuration, artifacts and canonical
engine patch match V25. Coordinator/server/scheduler/client are3845301/
3845543/3845905/3849212. Health passed at22:18:10 CST; native submissions
began at22:18:53 CST. Startup prefix verification at22:20:36 CST records901
submissions,695 prefill batches and930 native_batch geometry samples, with
writer_error=null and zero dropped/failed records. Available disk is72.3GiB.
Main v26_launch_after_v25_20261011.json is authoritative,running_new_pair,
predictive first and cold same-version native afterwards. Retain V96 stop/
cleanup/start receipts; actual busy-window/JCT validation remains pending.
V95 and earlier deployment entries below are historical, not current status.

The complete V25 600-900s diagnostic window has43.732s instrumented
exclusive Python wall,36.456ms mean decode worker,23295 mean context tokens
and51.824s prefill worker intervals. Old V24/V23-native references have
different source and realized demand. Exclude21:48:26-21:48:52 CST, or
1015.164-1041.164s from the first GPU sample, from primary windows because
of live profiling. Retain371 valid GIL samples/120 read errors;7 include
first-service proof and10 key-path materialization, while status publication
has zero. Sample frequencies are not total CPU, GPU stall or removable JCT.

The21:59:28 CST separately sealed lifecycle audit verifies early-started,
next-request-reused FULL for418 tools/6.922GB and26 JOINs/274.125MB.
Tool/JOIN total FULL reuse is7.536/0.291GB; ACK-to-service P90 remains
952.55/2792.28ms. Preserve59 unknown JOIN boundaries, keep84.081GB demand
handoff separate, and do not treat477.752GB residual tool native Host hits
as proven advance opportunity or oracle benefit. Continue shared-cost,
JOIN-tail and useful-FULL coverage work; the full objective stays active.

V95: V24 was intentionally interrupted at 21:18:54 CST after 132
completed outcomes; 24 unfinished tasks are administratively censored,
not model failures. Native never started. Preserve the frozen plan,
raw traces, outcomes and diagnostic windows; all old serving processes
exited and GPU allocations were released. Remove 132 already archived
root workspaces and the 24 V24-mounted containers, retaining unfinished
workspaces. Free space reached about 104GiB after cleanup and was about
85GiB at 21:36:57 CST after V25 created its initial workspaces.

V25 deployed frozen a770180 at 21:28:52 CST under coordinator 3075855,
with exactly the V24 environment, predictive first and cold native.
It includes incremental status totals, prefill-work measurement and
development restart support. Its first setup attempt hit an inactive
control socket before any serving/workflow collection and is archived.
The launcher now removes a confirmed inactive Unix socket; 10 related
checks pass. Health passed at 21:31:26 CST and real submissions began
around 21:32:04 CST. The 21:34:22 CST startup verification observed 1377
native submissions, 1058 prefill batches and 1412 native-geometry samples,
with writer_error=null. Separate file prefixes are sealed, not an atomic
snapshot. The canonical patch still matches the engine tree.
Main v25_launch_after_v24_20261011.json is authoritative; retain
experiments/reports/v95_v25_development_start_20261011.json as evidence.
Keep V95 stop/cleanup receipts and the full objective active; actual
GPU/JCT and early FULL consumption still need validation.

Development iterations may stop an ongoing run once an important fix is
identified, verified and committed, preserving its frozen plan, raw trace,
complete windows, written outcomes and interruption receipt. Do not wait
for the old native arm merely to deploy a useful revision. The existing
launcher now accepts --development-restart, records interrupted_development
and waits for old process identities to exit before starting a fresh
predictive-first pair. Partial work is censored, not model failure or a
completed performance comparison. Retain a complete same-version native
comparison for the stable candidate and final benefit claims.

The 21:08:15 CST V25 full-pair waiting registration and coordinator
2951765 are superseded and cancelled. Retain the original state and
cancellation receipts, user report commits/template and editable tmp/.
The current deployment is the a770180 cold run above. V94 and earlier
entries below describe historical observations, not current run state.

V94: a 20-second live V24 GIL stack sample contains _write_status in
34 of 411 valid samples, with 89 read errors. Status publication scans up
to 32768 pending eviction records every second and is omitted from the
existing writer/control timing. Maintain per-pool pending counts/latest
units on insertion, replacement, expiry and reaccess; publish these totals
without traversing records. The 84 telemetry checks pass, preserving all
evidence. Across 80 alternating CPU trials, full status publication at
32768 entries falls from 15.296 to 0.132 ms. Retain mutation costs:
single eviction 0.0480 to 0.0492 ms, 512 matches 7.262 to 7.698 ms.
These are CPU fixtures, not GPU/JCT improvement. Keep frozen V24 intact.

V93: the complete V24 predictive 600-2400s window gives 1169.916 decode
token/s, GPU 87.73%, weighted batch 47.398 and prefill worker intervals
335.226s. Instrumented exclusive wall remains 221.872s, including 50.889s
reentry. The 3.79%/5.07% gains against V23 predictive/native are
cross-revision references with different realized demand; same-version
V24 native remains pending. The 6.788GB predictive FULL transfer still
requires event-boundary/first-use validation. Retain V93 window/control
and V94 profile/benchmark evidence and keep the full objective active.

Exclude the live profiling interval 20:45:34-20:46:00 CST, or
3825.305-3851.305s from the first GPU sample, from primary performance
windows. GIL stack frequencies are not total CPU, GPU stall or removable
JCT. Actual engine frames use main third_party/sglang-v0.5.20.
The clean follow-up is now registered as V25 above. Its same-version
GPU/JCT comparison must establish the net effect of these changes.

The isolated follow-up fixes prefill work measurement. The old
req.extend_input_len defaults to zero in the current engine: V24's first
200 prefill batches/285 request samples report zero without proving zero
work. Read prepared CPU batch prefix_lens/extend_lens, then the native
request extend_range, recording source and retaining missing values as
unknown. No CUDA tensor reads or synchronization are added. Offline work
cohorts require native geometry for every batch row; legacy defaults and
partial attribution remain unknown. The 99 relevant checks pass. Keep both
V24 arms frozen; use this correction in the next revision to compare actual
prefill work before optimizing its cost. Measurement is not a speedup.

V92: the live V24 action audit confirms948 tool PREPARE node commands/
211 bursts/9.122GB FULL and zero speculative Mamba. First backups for
116/140 known independent tools have at least1000ms remaining.
Tool-wait demotions release0.732GB FULL in59 native-node events across
36 tools;32/36 remain open at least1000ms after their first release.
Accepted forecasts record1000ms throughout. These are waiting-time
qualification rates, not RETURN timestamp accuracy, recall or net benefit.
Sources have separate EOF/prefix seals, not an atomic complete-run
snapshot. Preserve unclosed boundaries and await subsequent consumption/
same-version native performance. Keep the full objective active.

V91: the complete V24 predictive600-900s window gives1152.66 decode
token/s, GPU88.30% and weighted batch47.351. Native/handoff/predictive
H2D are651.143/21.761/2.652GB; predictive FULL is1.428GB, without an
independent early-start/reuse audit in this window report. Prefill worker
intervals remain50.639s. Instrumented exclusive Python wall is40.760s,
including reentry5.888s, admission3.855s andhandoff_enqueue3.592s.
Against V23 predictive's40.985s, aggregate control cost has not clearly
fallen; revision and realized demand differ, so this is not isolated
optimization attribution.

All observed V24 per-batch decode flags aretrue; periodic logs also
have181 true samples. Longer46/47 worker intervals remain and cannot
be attributed to missing exact capture buckets. The V23 predictive/
native reference window gives1143.43/1250.53 token/s: V24 is0.81%
above the old predictive and7.83% below the old native. This is explicitly
a cross-revision development comparison; V24 same-version native is
still pending. Preserve both V91 reports, revisions, prefix seals and
unfavorable evidence. Keep the full performance objective active.

V90: V23 and postprocessing are complete. Native has156 completed in
10375.010s; predictive has154 completed/2 incomplete in8984.588s.
Both HTML timelines exist. Extend the existing comparison script with
same-instance completed-pair JCT, per-task deltas and realized request/
tool counts, retaining unmatched completions and failure differences.
Across154 common successes, mean predictive/native JCT is2660.539/
2971.673s, mean delta-311.133s and median paired delta-204.284s.
Predictive is faster for86 and slower for68; paired-delta P95 is still
+2854.574s. Different demand and tools prevent isolated migration credit.

The complete600-2400s window precedes maintenance and gives1127.156/
1113.470 decode token/s (+1.23%), weighted batch47.378/47.343 and
GPU84.12%/84.99%. H2D is2.019/2.391TB, but predictive prefill worker
intervals are331.559/271.875s, +59.684s, with10.903/10.576 million
uncached prompt tokens. Preserve this remaining cost and actual early FULL
coverage as V24 priorities. Transfer totals are not removable JCT.

V24 deployed frozen56ff3c2 at19:38:08 CST. Patch826993c1 matches
engine tree6e1dc30 exactly. Health passes and actual workload requests
have started. Coordinator/server/scheduler/client are1418070/1495239/
1495619/1497799, with start identities in the V90 receipt; old V23
collection processes are absent. Keep the156-task108+48 arrivals,
running48,tool180s,workflow14400s,Host200GB80:20,HBM0.9 and500ms lead.
Live forecasts explicitly record1000ms tool offload; per-batch graph
flags are present. One observed runtime state hasJOIN/tool ACK25/483
andphysical_disabled=false, not proof of lead/reuse or throughput.
The authoritative registration is nowrunning_new_pair; cold same-version
native follows. Preserve the full active performance objective.

V89 confirms committed1f75f55's one-second tool-offload rule is already
included in the V24 registration below for both PREPARE and waiting-page
reclamation. Frozen V23 retained two seconds and has finished. Use the
V90 receipt and authoritative main launch state for current V24 status.

Analyze existing periodic decode graph logs separately from per-batch
audit flags. In600-900s, all180/197 predictive/native log samples report
true; batch46/47 contribute5/44 and2/35 samples. Trace flags from the
completed result throughreport_decode_stats. V90 fixes dictionary-format
startup configuration parsing and confirmsdecode_log_interval=40.
Keep the original V89 null field/report and the V90 graph report with
identical prefixes and samples. Preserve prefix
hashes and second-resolution boundary exclusions, and keep legacy audit
flags unknown. Some context-matched46/47 worker cohorts remain2%-15%
slower, so absent graph replay is not established as the cause. Predictive
instrumented exclusive wall is40.985s, led by reentry5.641s,
admission4.131s,join_prepare3.719s andcontrol_drain3.570s. No kernel or
removable JCT attribution follows. The15 analysis checks pass. Keep
v89_v23_context_control_graph_600_900_20261011.json and validate queued
CPU reductions with actual V24 GPU/same-version-native measurements.

Keep V89's4200-4500s advantage2.36% and4500-4800s deficit1.54% as
diagnostics only: native overlaps disk maintenance17:52:00-18:04:35 CST.
After those bounds,5100-5400/5400-5700s deficits are3.91%/17.78%, with
weighted batches41.728/47.428 and32.480/46.898. Different demand and
late occupancy prevent isolated policy attribution. Both V89 busy-window
reports record maintenance context. Do not expand instrumentation or add
speculative runtime changes before testing the queued V24 optimizations.
The complete native pair and throughput/JCT objective remain pending.

At19:05:27 CST, waiting V24 was updated to frozen56ff3c2 in
beliefkv-v88-reclaim-exclusions-20261011, including the one-second
tool-offload policy and excluded-node reclaim correction. Coordinator
1418070 has PPID1/start588699021; old1263690 is absent. Preserve its
d73e292 state/log underv24_launch_after_v23_d73e292_superseded_20261011_v88.
Environment and wait identities match exactly. The target includes the
user'sbe627b3 monthly-report commit; preserve the untracked template.
Main execution and the live engine still match frozen V23. Wait for the
complete pair and postprocessing, then predictive followed by cold
same-version native. No V24 GPU arm has started. Keep authoritative
main state andv88_v24_reclaim_exclusions_registration_20261011.json.
The18:42 registration below is superseded; the performance goal remains active.

V88 excludes the restoring checkpoint's own nodes before spending the
existing eight-candidate quota in both generic FULL-leaf and Mamba-LRU
capacity scans. Otherwise eight protected entries can hide a safe cold
duplicate. Preserve Host-copy, reference, lock, pending-transfer and actual
allocator checks; Mamba-only reclamation retains FULL. Four real CPU
hybrid-allocator regressions with8/16 excluded entries fail before the fix
and pass after it;95 scoped native checks pass. Target values remain intact
and no new D2H is submitted. The canonical patch matches the isolated tree
and passes reverse application, SHA256
826993c171b8bdc771828358f90a34acb5e9d3e6111bf135bc397d3627acfa65.
This committed correction is included in still-waiting V24 with the existing
one-second tool-offload policy. Preserve the frozen V23 pair and do not
attribute all no_cold_capacity events or a GPU gain to these fixtures.
Keepv88_reclaim_excluded_scan_validation_20261011.json.

Additional complete same-version V23 windows give predictive/native
1203.647/1101.067 decode token/s at1800-2100s and1162.577/1185.957
at2100-2400s. Preserve both the9.32% advantage and1.97% deficit.
The first window has nearly equal weighted batch47.413/47.353,
visible context23168/23503 tokens and H2D28.065/204.488GB.
Predictive instrumented exclusive wall is36.490s, including11.302s
reentry,3.535s control_drain and2.766s opportunity sampling. These
windows precede documented disk maintenance, but different live demand
and unknown graph replay prevent isolated prefetch causality. Keep both
V88 window reports; wait for the complete native pair and retain the
active throughput/JCT goal.

At18:42:32 CST, waiting V24 was updated to frozen
d73e292c6781e2dd6732dd5302c784302f79fa62 in
beliefkv-v87-eviction-index-20261011. This includes the one-second
tool-offload policy, subsequent CPU reductions and reporting-aware
execution verification. Coordinator1263690 has PPID1/start588561543;
old506123 is absent, with itsddeda0b state/log archived under
v24_launch_after_v23_ddeda0b_superseded_20261011_v87.
Environment and wait identities match exactly. Main is ate99c1a2,
but execution code still matches frozen V23a6dd627 and the live engine
matches its patch. Preserve the user's monthly-report commit and untracked
PPTX without local ignores or relocation. Untracked docs do not block
follow-up deployment; tracked edits and untracked execution files still
prevent overwrite. Record collection and verification revisions separately.
The37 existing launcher/postprocessing/resume checks pass. Keep authoritative
main state andv87_v24_eviction_index_registration_20261011.json.
Wait for the complete V23 pair and postprocessing, then predictive followed
by cold same-version native. No V24 GPU arm has started; the performance
goal remains active. The17:58 registration below is superseded history.

V87 maintains sorted namespace prefix lengths on eviction-index mutation,
then bisects the eligible range per request. Keep eviction-recency
selection for truncated probes, original hashes/output/coverage and the
existing32768-entry bound. This derived view is maintained by the single
background writer; no physical policy or sampling change. The82 existing
checks pass; the new short-overflow/continuing-probe case passes after
correcting its last simulated cache length. Against6c8f347, all42 CPU
cases preserve records, hashes, counters, history and pending state over
25 alternating measurements plus10 warmups. Derived sorted state also
matches live lengths. The4096/16384-entry fixtures reduce unmatched
query median thread CPU by71.30%/74.13% and partial matches by37.11%/
33.18%, while bounded insertion/expiry costs7.8/9.1 microseconds more.
Keep all results and validate actual weighted writer/GPU/JCT effects.
Committed and included in still-waiting V24; preserve frozen V23.

The complete same-version V23 window at3600-3900s has predictive/native
1055.21/1055.75 decode token/s, GPU77.01%/75.08% and weighted batch
43.603/43.963. H2D is only0.073/0.142GB. Shorter predictive mean
decode worker intervals (30.14/33.48ms) do not imply throughput or
prefetch gains. Instrumented predictive exclusive wall is30.672s,
including6.328s reentry,4.347s control_drain,3.032s admission and
3.014s opportunity sampling. Measured writer CPU samples are275.7/
381.7ms; do not extrapolate by sampling interval. This window ends
before this continuation's disk-maintenance bounds. Graph flags remain
unknown; the completed native pair and full performance goal are pending.

V86 reclaims about35.73GiB through verified immutable-log deduplication
(7.22GiB) and compression of two obsolete failed repeat exports
(28.51GiB net). The493.6MB archive passes full tar/source comparison
before removal of16 large JSONL originals; both manifests and restore
pointers remain. Preserve original training/calibration raw traces,
usable exports, models andV22/V23. Current free space is about86GiB.
Keep the archive inventory and both V86 cleanup receipts. Record the
conservative17:52:00-18:04:35 CST maintenance interval when analyzing
live native performance; idle priority does not prove zero interference.

At17:58:19 CST, still-waiting V24 was updated to frozen
ddeda0b8d788a974895a7ab7e7f9c010a2eeec84 in
beliefkv-v86-tool-survival-20261011. Coordinator506123 has
PPID1/start588296246; old121691 is absent and itsdbcd95c state/log
are archived. Environment, engine patch and wait identities exactly match
the old registration. Main stays clean ata6dd627; its engine matches
the V23 frozen patch and native server/client identities are unchanged.
Wait for the complete V23 pair and postprocessing, then predictive followed
by cold same-version native. No V24 GPU arm has started. Keep authoritative
main state andv86_v24_tool_survival_registration_20261011.json.
The17:30 registration below is superseded history; retain its receipt.

V86 replaces linear CDF time-knot lookup with bisect while preserving
interpolation, boundaries and tails. Leave quantile inversion, model and
the one-second offload rule unchanged. The first32 distinct recorded
forecasts have18 knots each;1911 query positions and29 additional
duplicate/boundary cases preserve exact outputs. Over31 alternating
samples, CDF thread CPU falls26.50% and the complete cached native
survival-query bundle falls14.27% (10.899 to9.344 microseconds).
The49 existing prediction checks pass. Keep the V86 CPU report and
validation receipt; these exclude accuracy, allocator, DMA, GPU and JCT.
Committed asddeda0b and registered for still-waiting V24; preserve live V23.

The additional same-version V23 window at2700-3000s has predictive/
native1016.18/1058.67 decode token/s, GPU81.55%/84.70% and weighted
batch41.543/42.938. Preserve the4.01% deficit. H2D is only0.388/0.876GB,
so this is not a strong migration-pressure acceptance window. Predictive's
instrumented21.492s exclusive wall includes3.446s control_drain,
3.210s tool_prefetch and2.452s opportunity sampling. Sparse socket/
JSON samples do not identify total cost or GIL/GPU causality. Native
remains live; require the completed same-version pair and keep the goal active.

At17:30:54 CST, waiting V24 was updated to frozen
dbcd95cfdd198acb0b29815d1b594d2a1ab9ed82 in
beliefkv-v85-reentry-graph-20261011, including failed-prefix reentry
reduction and the shared graph-result marker. Coordinator121691 has
PPID1/start588131709; old3444664 is absent and itsdd12454 state/log
are archived. The environment exactly matches the previous registration.
Main stays clean ata6dd627; its frozen engine and running V23 coordinator/
native server/client remain intact. Wait for the complete V23 pair and
postprocessing, then predictive followed by cold same-version native.
No V24 GPU arm has started. Keep authoritative main state
v24_launch_after_v23_20261011.json and the V85 registration receipt.

V85 adds the completed engine result's can_run_cuda_graph flag to the
existing shared native/predictive service records as cuda_graph_replayed.
No new sampling loop, device synchronization or action policy. Group
worker intervals by batch, context bin and flag; legacy flags remain
unknown. V23's7223/7896 decode batches in600-900s are all unknown.
The90 telemetry/window and20 native scheduler-hook checks pass; the
canonical patch matches the isolated source and passes reverse-apply
validation. Keep the V85 legacy-cohort report and receipt. Use this to
diagnose slow batches in the next GPU pair, without claiming a GPU fix.

V84 now has same-version V23 busy-window evidence. Predictive/native
decode throughput is1143.43/1250.53 token/s at600-900s and
1059.913/1083.420 at900-1200s, a predictive deficit of8.6%/2.2%.
The first window has nearly equal weighted batch47.353/47.395 and
visible context23016.65/23524.59 tokens. Preserve these unfavorable
results; live trajectories and instrumented40.985s exclusive wall do
not establish isolated CPU causality or completed-pair JCT. Slow46/47
worker intervals do not prove eager fallback: native graph replay can
pad them to48, and the old telemetry has no replay flag.

The isolated reentry follow-up memoizes a failed shared ancestor within
one inspection and reuses local node/key/page-size values. Preserve
identity, cycle, namespace, safe-input and transfer checks; no cross-call
failure cache. The139 native and7 materialization checks pass. All27
CPU fixtures preserve outputs over31 alternating samples of80 calls.
Shared failed-prefix thread CPU falls6.76%-38.27%; complete matches
are nearly unchanged or faster, with two small regressions of0.092/
0.015 microseconds. Retain both rejected candidate reports and the
accepted prefix-locals report, with actual thread CPU distinguished
from the historical perf-counter wall fields. Canonical patch matches
the isolated source exactly. No DMA or GPU performance is measured.
Preserve frozen V23 and require the subsequent same-version GPU pair.

The V83 diagnosis of the completed V23 predictive arm confirms that each
incomplete root's final original and thinking-disabled recovery responses
used 8192 output tokens and finished with length; the recovery text is
repetitive analysis, not a finished report. The only hard-budget finalization
in django-12273 belongs to its test-analyst child, not the root; django-11740
has none. Deadlines did not expire, children were not cancelled and JOINs
were satisfied. Retain incomplete classification and investigate generation
degeneration without claiming a scheduling or recovery failure.

All4720 no_cold_capacity handoff closures map to independent requests with
later native prefill service. Close-to-first-prefill P50/P90 is73.051/116.885ms,
max1654.871ms;770 exceed100ms and48 exceed500ms. Their1.390TB of native
FULL Host-hit counters are neither a feasible early-recovery denominator nor
proof that free capacity appeared after rejection. Do not add general retry
polling from these counts; preserve rearming after linked child device release.
The measured exclusive wall totals include113.822s of reentry inspection,
90.732s control_drain and74.104s opportunity sampling. They are not JCT
savings; select subsequent reductions from actual path evidence. Keep
v83_v23_terminal_and_handoff_diagnostics_20261011.json. At that diagnosis
V24 targeted dd12454; the latest registration above supersedes it. Native
remains live and the full performance goal remains unmet.

The V82 follow-up skips unmatched power-of-two prefix ranges in the background
eviction writer using the existing sorted lengths and binary boundaries.
Keep exact terminal hashes, cross-pool/namespace attribution, expiry, overflow
accounting and output order. The82 telemetry/writer and66 lifecycle audit
checks pass. Against7d8b91c,36 CPU cases with25 alternating measurements
and10 warmups preserve all records, hashes, counters, history and pending
indexes. Unrelated-branch CPU falls6.58%-18.28%; partial-branch CPU falls
1.60%-3.62%; complete matches are nearly unchanged. The alternative that
directly enumerates matching routes has weaker gains and some regressions,
so retain the range-skipping version. These fixtures exclude concurrent
GIL effects, DMA, GPU throughput and JCT. Keep both V82 writer CPU reports
and the validation receipt. Commit dd12454 is registered for waiting V24.

The V82 V23 live lifecycle audit links100 JOIN and390 tool waits to
pre-boundary-started, next-request-reused FULL of1.479/6.257GB.
Demand handoff reuse of97.598GB remains separate. Independent-wait
last-ACK-to-service P50/P90 is328/1452ms for JOIN and433/913ms for tools;
maxima remain12.496/91.976s. No JOIN/tool prefetched FULL in this read
has a confirmed native reload before first service. JOIN pre-completion
protection expirations cover0.170GB. Remaining tool-associated FULL Host
hits total480.284GB, including85.362GB across258 waits with at least16
observed nodes; association is not a feasible early-recovery denominator.
Keepv82_v23_prefetch_lifecycle_live_20261011.json. Sequential reads of
append-only logs are not an atomic snapshot, ablation or completed-pair
performance evidence. V23 predictive has ended with 154 completed and
2 incomplete workflows out of 156, in 8984.588s. Its final telemetry has
no dropped/failed records, writer error or pending requests/batches.
The native server/client started at 16:38:39/16:41:22 CST. Both incomplete
roots have unexpired deadlines, no child cancellations and satisfied JOINs;
their last two root responses ended with length. Inspect finalization and
truncated output before attributing this to scheduling. The comparison is
still partial and ineligible. Preserve V23; the full goal remains unmet.

The isolated follow-up now reuses the admission-slot budget within one tool
prefetch scan and invalidates it before capacity reclamation. The next
candidate rereads changed capacity; native prefix planning and enqueue
continue to validate current capacity independently. The323 related checks
pass, including a reclaim that changes available slots from2 to0 before
the second candidate. No prediction, timing or physical policy changes.
Against455a5ef, nine CPU cases cover8/32/64 tool waits and no frontier,
no target or selection of the last candidate. Each has eight alternating
trials of120 prepared scans. Admission-check capacity reads per960 scans
fall from7680/30720/61440 to960, with matching policy counters, inspection
sequences and nonempty commands. Scan CPU falls26.78%-46.48% and wall
26.63%-46.43%; native pools, service and enqueue are stubs, so DMA,
GPU throughput and JCT are unmeasured. Keep
v81_tool_prefetch_scan_capacity_cpu_20261011.json and its validation receipt.
Committed asbb64e77 and registered for waiting V24. Preserve the frozen
V23 pair and require the same-version GPU comparison.

At 16:46:05 CST, waiting V24 was updated to committed
dd1245477187e3a7ce925822d971117c76798307 in the frozen
beliefkv-v82-eviction-ranges-20261011 checkout. Coordinator3444664
has PPID1 and start identity587862813; old waiting coordinator3283000 is
absent. Itsbb64e77 state and log are archived. V23 coordinator, native server and client
remain live; main stays at frozen a6dd627. The environment, native patch,
arrival flow and model artifacts exactly match the superseded registration.
Only the waiting target code changes. After the complete V23 pair and
postprocessing, deploy the one-second tool-offload policy, scan reduction
and unmatched eviction-prefix range skipping,
run predictive then cold same-version native. Keep authoritative main state
v24_launch_after_v23_20261011.json and receipt
v82_v24_eviction_ranges_registration_20261011.json. No V24 GPU arm or performance
proof exists yet; the16:13 bb64e77,15:48 1f75f55 and15:29 f61fc05 registrations are historical
and retain their own receipts.

Reduce the subsequent tool-offload horizon from 2000ms to 1000ms.
The return-within-window probability remains at most0.1, or the residual
P10 must be at least1000ms when no CDF is available. PREPARE selection
and waiting-page reclamation use the same predicate; preserve native
allocation, references, Host-copy dependencies and H2D timing. Record
the horizon with each accepted forecast. Audit that policy horizon alongside
the fixed2000ms reference; legacy records without the field retain2000ms.
Frozen V23 retains its original policy; include the reduction in waiting V24.

The SHA256-verified V79 prefixes contain55472 accepted forecasts and
52773 matched live snapshots for a single completed tool. Timing-qualified
independent candidates grow800 to1447; all1843 baseline-selected snapshots
remain selected. Of647 added tools,574 actually have at least1000ms left,
including476 in the1000-2000ms range. First-candidate precision against
the new horizon is1319/1447=91.2%. These are timing candidates, not
physical opportunities, actual demotions or performance evidence. The291
existing related checks pass; CDF and quantile-only audit cases preserve
legacy2000ms scoring and apply the recorded1000ms horizon. Keep
v80_tool_offload_one_second_replay_20261011.json. No model refitting
or live V23 policy change is involved.

At 15:29:16 CST, V24 was registered for committed
f61fc055620e22187c1b17b483cd8271d5b9220e in the frozen
beliefkv-v79-restore-budget-20261011 checkout. At 15:29:33, coordinator
2684310 has PPID 1 and start identity 587401910; it waits for the entire V23
coordinator 1083828, including both arms and postprocessing. V23 server
and client remain live. The new environment matches V23 except RUN_ROOT:
156 tasks, 108+48 arrivals, running 48, Host 80:20, tool 180s, workflow 14400s,
maximum lead 500ms and unchanged model artifacts. Main stays at a6dd627
with the frozen V23 patch; no V24 GPU arm has started. Keep authoritative
main state v24_launch_after_v23_20261011.json and the receipt
v79_v24_restore_budget_registration_20261011.json. After the complete pair,
HTML, workspace cleanup and performance export, run predictive first and
cold same-version native. Require early reused FULL, exposed recovery waits
and busy-window throughput/JCT evidence; the full goal remains unmet.

The isolated follow-up fixes a stale next-batch restore limit. Subtracting
the current PrefillAdder selections can cache zero after a chunked prefill
fills max_prefill_bs, then block subsequent decode-overlap recovery. Retain
the configured positive limits; live request rows, running frontier, FULL/
Mamba capacity and input/decode reserves still constrain the actual budget.
The 281 related checks pass. The baseline-a5b4a83 CPU fixtures change the
next restore slot from 0 to 1 with running48, rows6 and a filled one-request
prefill; rows0 stays0 and the ordinary two-slot case is unchanged.
Keep v79_next_batch_restore_budget_validation_20261011.json. Live busy
counters do not separate row and prefill-limit factors; do not attribute every
refusal to this defect. DMA, throughput and JCT remain unverified. This repair
and the existing validated reductions are committed as f61fc05. The V24
registration above will deploy them only after the complete V23 pair.

At 15:25:09 CST, the V23 tool audit has232 PREPARE bursts across172
independent waits with9.942GB FULL. Actual demotion is47 node events,
24 independent waits and14 contexts, releasing0.436GB FULL and2.318GB
Mamba. First-action long-wait precision is22/24=91.7% for demotion and
135/172=78.5% for backup; this is selected-action precision, not recall or
RETURN-time accuracy. Calls are17 execute and7 glob. The29 node decisions
with valid P50 have absolute-error P50/P90 of224/5161ms; repeated nodes
are not independent tools. JOIN backup covers128 parent contexts, while
demotion covers47 parents across49 epochs with0.604GB FULL and2.447GB
Mamba. Keep v79_v23_wait_offload_live_20261011.json. There is no
unconditional pre-tool or post-spawn offload; preserve the frozen pair.

The V23 complete2700-3000s window has21.492s instrumented exclusive
Python wall; control_drain/tool_prefetch/opportunity_sampling are
3.446/3.210/2.452s, with29.730ms mean decode worker interval. Keep
v79_v23_control_2700_3000_20261011.json. Work and context differ from
the early window; no same-version native gain is established. Completed
V20 server/client snapshots were SHA256-verified and hardlinked in four
pairs, freeing7.54GiB with all paths and data retained. Active V23 was
untouched. Keep v79_v20_log_deduplication_20261011.json.

At 14:41:13 CST, the V23 lifecycle read has 40 JOIN and 380 tool waits
with pre-boundary-started, next-request-reused FULL of 0.515/6.132 GB.
Demand handoff FULL reuse of 93.742 GB remains separate. Independent-wait
last-ACK-to-service P50/P90 is 361/1772 ms for JOIN and 431/919 ms for
tools; maxima remain 9.383/91.976 s. All observed next-tool requests have
native-arrival-to-service P50/P90 of 7.519/14.862 s, so selected prefetch
latencies do not describe the whole queue. Twelve JOIN FULL commands
expire before completion. Keep v76_v23_prefetch_lifecycle_live_20261011.json.
This live read is not an atomic snapshot, ablation or native speedup proof.

The same audit has 258 independent tool waits with at least 16 observed
planned nodes, associated with 85.362 GB residual next-request FULL Host hits.
Those associations do not establish truncation, feasible early bytes or gains
from a larger burst.

The isolated follow-up removes the independent 16-node limit in JOIN/tool
planning and native prefetch submission. Plan up to the current physical-ledger
slots (64 by default), then crop the continuous prefix by byte, pool capacity
and prefill reserve. Preserve per-node reservations/ACKs, necessary checkpoint
state, causal revalidation and whole-burst transfer timing. Demand handoff
and read-only opportunity sampling retain their existing 16-node scope.
The 697 related checks and 27 native subtests pass: complete 40-node recovery,
80-node plans clipped to 64 ledger slots, a 23-slot limit and a 20-ancestor
prefix under the byte budget. Native 64-node submission and reservation
rejection after 20 nodes both start one load burst. The native patch matches
the isolated source tree; the live V23 engine still matches its frozen patch.
Keep v78_wait_prefetch_node_budget_validation_20261011.json. This is CPU
semantic validation, not DMA, pre-boundary reuse, throughput or JCT evidence.
Continue verifying useful FULL coverage and exposed first-service recovery
waits in the next same-version GPU pair. Frozen V23 remains unchanged.

The isolated follow-up avoids empty-hint queue statistics in plan_native_prefill.
Validate hint identity, expiry and invocation revision only when hints exist;
count group coverage only when there are valid hints, and materialize length
sort keys only for fully hinted class/depth groups. Preserve causal rank,
aging, residency, restore priority and prediction submission semantics.
V23 is tool_timing_only and does not generate these admission length hints.
The 365 existing related checks pass, including full-to-partial hint expiry
ordering. Keep v77_native_prefill_empty_hints_validation_20261011.json and
v77_native_prefill_empty_hints_cpu_20261011.json. The 24 alternating complete
scheduler/admission CPU cases use real graphs, live identity checks and changing
stub residency; plans, inspection traces and non-cache counters agree.
Average wall falls 6.18%-11.89% and thread CPU 6.26%-11.89% against f9b2f96.
Native allocator, DMA, GPU throughput and JCT are excluded. Preserve frozen
V23 and the full same-version performance goal. Committed as 3b7e950.

V23 wait-offload evidence was updated at 2026-10-11 14:35:10 CST.
Tool PREPARE has 82 burst decisions, 456 node ACKs and 3.811 GB FULL
across 58 independent waits. Actual HBM demotion has 19 node events,
7 contexts and 8 independent tool waits, releasing 0.145 GB FULL and
0.773 GB Mamba cumulatively. Seven of eight first demotions still have at
least 2 seconds remaining; seven calls are glob and one is execute, so this
is not overall prediction accuracy. First-tool PREPARE precision is38/58.
Only one demotion has a valid P50 point estimate; no representative RETURN
time-error distribution is established. JOIN PREPARE covers103 parents,
with105 bursts,168 ACKs and2.142GB FULL; actual demotion covers42 parents,
releasing 0.549 GB FULL and 2.318 GB Mamba. Preserve backup/demotion and
node/agent distinctions. Keep v75_v23_wait_offload_live_20261011.json and
the prior v73 read. There is no unconditional pre-tool or post-spawn offload.
WAIT_TOOL/WAIT_JOIN backups enable native reclamation at real shortfalls.
Sequential live-file reads seal observed prefixes, not an atomic snapshot.

The complete V23 predictive600-900s service window has1143.43 decode token/s,
86.03% GPU utilization,47.35 weighted decode batch and34.61ms mean worker
wall interval. V22 predictive/native are1019.20/1219.52token/s and
76.68%/89.81%. Keepv74_v23_busy_window_600_900_20261011.json. This
cross-version diagnostic does not replace the pending same-version V23 native.
Keepv74_v23_control_600_900_20261011.json:40.985s instrumented exclusive
Python wall in299.703s, including reentry5.641s,admission self4.131s,
JOIN PREPARE3.719s,handoff enqueue3.143s and opportunity sampling2.520s.
These sums are not removable JCT or isolated CPU cycles.

The isolated follow-up skips decorated no-op polling/sampling calls in
scheduler_step, using existing worker,writer,diagnostic and due-time conditions.
Helpers retain their own checks. Sampling cadence,prediction inputs,physical
actions,thresholds,native patch and frozen V23 configuration are unchanged.
The342 related checks pass; one existing worker test skips because the local
pinned encoder artifact is absent. Keepv74_scheduler_sampling_validation_20261011.json
andv74_scheduler_sampling_cpu_20261011.json. The24 alternating CPU cases use
real causal graphs,profiling and1ms logical cycles with stub sinks/idle workers;
policy rows,counters and semantic cadence match. Full dormant maintenance
CPU falls31.1%-34.4% with a worker and51.0%-55.6% without, at microsecond
scale. Allocator,DMA,GPU throughput and JCT are excluded. Committed asf9b2f96;
preserve the started V23 pair and continue measured reentry/admission/prepare/
recovery reductions. No subsequent GPU pair is registered by this update.

The earlier14:15:42 lifecycle read has 9 JOIN and 87 tool waits with early-started,
next-request-reused FULL of 0.105/1.188 GB. The 20.646 GB handoff FULL
reuse remains demand recovery. Independent-wait last-ACK-to-service
P50/P90 is 533/1641 ms for JOIN and 420/819 ms for tools; JOIN has only
10 paired events, and the tool maximum remains 17.257 seconds.
Keep v73_v23_prefetch_lifecycle_live_20261011.json. These sequential
live-log reads are not atomic snapshots, a code ablation or a native
throughput comparison. Keep the started pair frozen and the goal active.

V23 predictive started at2026-10-11 14:01:40CST after the complete V22
pair, HTML export, workspace cleanup and performance comparison. At14:05:25
server1088402 and client1090250 are live; health succeeds and client requests
are submitted. Main and V23 ab_plan are pinned toa6dd627. The native patch,
156 tasks,108+48 arrivals,running48,Host80:20,tool180s and maximum lead500ms
match the registered frozen configuration. Preserve this running pair.

The complete V22 arms both have155 completed and1 incomplete. Predictive/
native wall duration is11779.026/9710.655s; completed throughput is
47.372/57.463 workflow/h, predictive down17.56%. The154 paired completed
workflows have mean JCT3428.161/2909.736s, predictive up17.82%.
Full-run output throughput is747.283/886.549token/s and GPU utilization
67.066%/82.096%. Dynamic traces differ and task correctness is not independently
graded; isolated_policy_effect_verified=false. Keepcomparison.json,
native_policy_comparison.json and both arm HTML exports. The performance goal
is unmet. Require actual early FULL reuse, exposed recovery wait and busy-window
throughput/JCT evidence from V23; CPU fixtures and extra ACKs do not suffice.

V23 registration at2026-10-11 13:55:19CST now targets committed
a6dd627587827f376bed7022d15e538100c0c775 in the frozen
beliefkv-v72-prefix-capacity-20261011 checkout. At13:55:51, new waiting
coordinator1083828 has PPID1 and start identity586838240; old waiting
coordinator1024560 is absent. V22 native and collection processes remain
live:153 completed,1 incomplete,2 unfinished. Workload,observations,native
patch,maximum lead500ms and previous wait identities match. Keep
v72_v23_prefix_capacity_registration_20261011.json; main state
v23_launch_after_v22_20261011.json is authoritative. After the complete
V22 pair and postprocessing, run predictive then cold same-version native.
GPU throughput/JCT and early reused FULL remain unverified for this code.

Use a fitting continuous prefix after bounded cold reclaim for JOIN/tool
prefetch. Previously the refreshed plan included global evictable capacity
and required the entire burst to fit, even when some FULL ancestors were
already loadable. Reuse the existing physical fitting-prefix planner and
leave the unavailable tail to a later predictive or native load. Revalidate
after causal event delivery with the selected node count, so newly freed
capacity cannot inflate the time estimate or submitted-byte attribution.
Keep native allocation, dependency and reserve checks. Native patch,
workload, observations and maximum lead500ms are unchanged.

579 related checks pass. Keep the baseline-fd4a13c CPU comparison in
v72_wait_prefetch_capacity_cpu_20261011.json and validation receipt in
v72_wait_prefetch_capacity_validation_20261011.json. Both JOIN/tool fixtures
need16 FULL units and one Mamba slot for four nodes. Free FULL10/Mamba0
changes0 to2 submitted nodes; FULL16/Mamba0 changes0 to3. Full capacity
keeps4 nodes; a first ancestor that cannot fit keeps0. These use mock native
enqueue and do not establish pre-boundary physical transfer or GPU benefit.

The completed V22 audit observes no_cold_capacity in54 JOIN and695 tool
waits; next-request FULL Host-hit associations are0.485/187.167GB.
Do not treat those bytes as feasible early recovery or an oracle saving.
The new code still requires same-version early reuse, exposed recovery
wait and busy-window throughput/JCT validation. Register it only while
V23 is genuinely waiting; preserve a started pair.

V22 native django__django-11885 is incomplete after two length-terminated
root results with repetitive text. Both children returned, JOIN was satisfied,
the deadline did not expire, no guard intervened and runtime delivery did
not fail. Retain it as an incomplete result, not a GPU policy benefit.
These changes are included in the page-top V23 registration; V22 stays frozen.

The following13:32 registration is superseded by a6dd627 and is historical.
Registered V23 at2026-10-11 13:32:18CST for committed
60c55021e097750f2faeef175d206c906279eabf in the frozen
beliefkv-v71-tool-overrun-20261011 checkout. At13:32:35, coordinator1024560
has PPID1 and start identity586700081; V22 native and collection processes
remain live, with151/156 workflows completed. Replace only waiting773052.
Native patch, workload, observations, maximum lead500ms and wait identities
are unchanged. Keepv71_v23_tool_overrun_registration_20261011.json.
The mainv23_launch_after_v22_20261011.json is authoritative; archive prior
b854a91 registration. After the complete V22 pair/postprocessing, run predictive
then cold same-version native. GPU performance remains unverified.

Correct the completed-history overrun path before the next pair. The final
surviving point of a three-point historical prior is an error margin, not a
certain completion. When its center has passed, use the existing operational
survival CDF for probabilities and quantiles; retain history while the center
still has support. Add no policy switch or timing threshold. Preserve the
frozen model, workload, native patch, observations and live V22 collection.

494 related checks pass. In the same V22 development replay,364 PREPARE
decisions retain only the upper point, with just15 actual long waits;
23 demotions in that condition have just1 actual long wait. Re-evaluating the
old77 demotions selects54, of which48 remain open at least2s; independent
first-tool precision is26/29 versus27/49 originally. Re-evaluating587 backups
selects215 with168 actual long waits; independent first-tool precision is
129/165 versus143/499 originally. Keep selection subsets distinct from
opportunity recall, actual actions and end-to-end throughput.

On all71 recorded demotions with valid P50, absolute error P50/P90 changes
719/2828 to303/1713ms without changing that error denominator. Across18251
tools with accepted active snapshots, per-tool near-end median absolute-error
P90 falls713 to606ms, while P50 remains101-102ms. First500ms triggers with
actual0-500ms lead increase11281 to11305; triggers over1s early fall609 to606.
This is snapshot timing, not physical prefetch or GPU benefit. Keep the
source-hashedv71_v22_tool_prepare_probability_20261011.json,
v71_v22_tool_history_overrun_replay_20261011.json and
v71_tool_history_overrun_validation_20261011.json. Register the committed
fix for the genuinely waiting V23; continue the full performance objective.

Keep the completed V22 waiting-agent audit in
v70_v22_wait_offload_20261011.json. Tool PREPARE has587 burst decisions,
3095 acknowledged node commands and27.719GB FULL; pressure demotion has
77 node events,31 contexts and49 independent tools, releasing1.188GB FULL
and4.185GB Mamba cumulatively. JOIN PREPARE has293 decisions,891 ACKs
and6.725GB FULL;49 demotions across42 parents release0.733GB FULL and
2.125GB Mamba. PREPARE is a Host shadow, not HBM demotion or whole-agent
offload. Mamba release preserves required native write-back dependencies;
speculative PREPARE does not back up Mamba.

The V22 long-wait predicate targeted at least2000ms remaining. First-action
precision per independent tool is143/499=28.7% for PREPARE and27/49=55.1%
for demotion; per-node demotion precision is49/77=63.6%. For71 demotions
with valid P50 predictions, absolute error P50/P90 is719/2828ms. Four
demotions follow the observed harness TOOL_END by15-119ms; retain clock
pairing uncertainty and inspect event delivery as well as forecast overestimation.
The1810 tools with total duration at least2000ms are descriptive, not a feasible
offload denominator. Current policy starts after actual WAIT_TOOL or WAIT_JOIN;
there is no unconditional pre-tool or post-spawn COMMIT_CPU. Treat precision,
coverage, backup/demotion and subsequent consumption as separate evidence.
This audit does not change the frozen workload; the current V23 revision
is the latest page-top registration and authoritative main state. The
same-version busy-window performance objective remains active.

Reuse pure causal admission ranks across unchanged scheduler iterations instead
of clearing them every GPU step. Preserve the existing signature of semantic
revision, graph version, queue order, request object and full current identity.
Graph, queue or identity changes still recompute classification. Residency,
capacity, hint expiry, aging, restoration/final-stage quotas and predictor inputs
remain live; do not cache complete plans or add a policy switch.

The349 related runtime/admission/handoff/terminal/tool/burst checks pass,
including changing residency and age across iterations, hint expiry, new decode
inputs, graph events and queue/object/identity invalidation. The old scheduler
reproduces four classification calls across two unchanged cycles; the new one
performs two while native reads and ordering still update. Keep
v69_causal_reuse_admission_cpu_20261011.json:24 complete scheduler/admission
CPU cases,61 alternating samples each,48/156 requests,1/4 retained rounds,
one/two planning calls,graph changes every1/4/16cycles,51ms logical progress.
Native observations/plans/non-cache counters agree. Graph changes every4/16
cycles reduce mean wall16.8%-31.6%/20.8%-39.3%; every-cycle invalidation is
approximately unchanged at0.5%-2.1% variation. Residency is a changing stub;
actual allocator,DMA,GPU throughput and JCT are excluded.
Committed asb854a91 and registered at2026-10-11 12:56:00CST for the
still-waiting V23 inbeliefkv-v69-causal-ranks-20261011. Coordinator773052
is reverified at12:56:41 with PPID1 and start identity586482350; V22 native
server/client remain live. Replace only waiting coordinator384991.
Workload,observations,native patch,maximum lead500ms and wait identities
match the previous26633b4 target. Keepv69_v23_causal_rank_registration_
20261011.json; archive the old registration asv23_launch_after_v22_26633b4_
superseded_20261011.json. The mainv23_launch_after_v22_20261011.json
remains authoritative. After complete V22 pair/postprocessing, run predictive
then cold same-version native. Earlier registrations below are historical;
actual GPU benefit and the full performance objective remain unproven.

Keep the complete replenishment-window regression in the acceptance evidence:
v68_v22_replenished_busy_windows_20261011.json and
v68_v22_busy_context_4200_4500_20261011.json. At3600-3900s,
predictive/native decode throughput is894.99/1015.65token/s and GPU
utilization62.35%/72.99%; at4200-4500s,621.67/881.84token/s and
51.54%/68.99%. In the latter window, weighted decode batch47.06/47.27,
visible mean context22665.93/23807.54tokens and H2D transfer-stream
6.521/6.523s do not support a longer-context or transfer-time-only explanation.
For batch48 in the22528-24575token bucket, worker means23.06/22.98ms;
batch47 means172.17/121.38ms. Window-wide prefill means45.32/33.41ms.
Worker intervals include scheduler/overlap work; binned geometry does not
match exact demand or prove a kernel-level cause.

The predictive control snapshots cover300.151s and record32.381s exclusive
Python wall time. Opportunity sampling4.259s, reentry inspection4.092s,
control drain4.068s, JOIN PREPARE4.021s and handoff enqueue3.047s lead the
measured phases. Keep writer samples on their own clocks without extrapolating
to whole-population cost or subtracting counters from JCT. Demand handoff FULL
9.562GB and predictive-source FULL0.150GB remain separate.

Verify the queued V23 normal-prefix, identity, native-load construction,
tool-prediction and exact-writer reductions in the actual same-version GPU pair.
Preserve live V22 and frozen V23 workload, observations and maximum lead500ms.
The earlier2400-2700s local advantage does not satisfy the performance goal.
Later formal performance pairs may use matched minimal common observations
and separate diagnostic pairs; required control, prediction inputs, physical
dependencies and transfer/first-use evidence remain part of normal execution.

Allow partial pre-boundary JOIN/tool H2D overlap. Remove the rejection when
estimated submit-to-ACK P90 plus enqueue P90 exceeds maximum lead; continue
clipping the start horizon to that configured lead. A 100ms remaining interval
may start an 800ms transfer, while native capacity, identity, dependencies and
residence remain authoritative. The 296 transfer-policy, tool, batch and runtime
checks pass. Committed as 26633b4 and registered for the still-waiting V23,
preserving the live V22 pair and its workload/observation configuration.
The 42 tool rounds covering recorded P90 with free capacity are a complete-hide
diagnostic subset, not the count of all potentially useful partial overlaps.

At 2026-10-11 12:26:36 CST, update V23 to
26633b4d2875f26cadb1706b47e182a84164a0b0 in the frozen
beliefkv-v67-partial-h2d-20261011 checkout. Coordinator 384991 is reverified
at12:27:33 with PPID1 and start identity586305867. Native patch, maximum
lead500ms, workload and observation configuration match the previous e82ad88
target. Stop only old waiting coordinator3756458, preserving V22 server/client
and the clean3aa27dd main checkout. Keep
v67_v23_partial_overlap_registration_20261011.json and archive the superseded
state asv23_launch_after_v22_e82ad88_superseded_20261011.json. The main
v23_launch_after_v22_20261011.json remains authoritative. Prior e82ad88
registration descriptions below are historical. Wait for complete V22 pair
and postprocessing, then predictive followed by cold same-version native.

Keep v67_partial_prefetch_v21_costs_20261011.json. Corrected V21 pre-boundary
reused FULL is JOIN 1.419GB/91 events and tool 2.530GB/84 events. Next-request
residual native FULL Host-hit byte proxies are 0.499/585.285GB; they do not
identify all bytes as physically prefetchable. Tool ancestry-bound evidence
covers 4748 overlapping rounds and 299.286GB of that cache-accounted proxy.
Do not infer a theoretical prefetch ceiling from the observed small coverage.

The 1117.376s V21 scheduler-thread exclusive wall profile includes required
control/planning and diagnostic sampling, not just logging. Opportunity and
terminal sampling are 124.516/43.949s; their collection and record construction
run synchronously, while the opportunity JSON/file writer is asynchronous
and was not timed in V21. For later formal performance runs, reduce pure
diagnostics and use a matched minimal common observation setup; keep causal
prediction inputs, transfer/first-use proofs and required correctness evidence.
Use a separate diagnostic pair for detailed profiling. Do not change frozen
arms or subtract these counters from JCT.

Reject the V65 shared-ancestry candidate for the current normal latest-anchor
path. Each valid latest snapshot has one leaf per pool; a four-leaf CPU fixture
does not represent that path. Across64 case/phase combinations with21 alternating
samples, planning outputs match;1024-depth four-leaf H2D/PREPARE means fall
1372.6/1219.1 to827.7/663.8us, but complete observations improve only3.8%/0.5%.
Complete single-leaf observations do not improve consistently: H2D6103.6 to6025.9us,
PREPARE5673.0 to5780.4us. Keepv65_checkpoint_ancestry_cpu_20261011.json and the
source-hashedv65_checkpoint_ancestry_candidate_20261011.patch. The benchmark can
load this rejected patch in memory; production physical.py is restored exactly
to7432972. The496 related checks passed before reversion. Leave the V23e82ad88
target, live V22 pair and frozen workload untouched. Prioritize measured
normal-path costs and the actual queued GPU comparison over unused-fixture gains.

The requested four-step child-to-parent capacity handoff is implemented in the
runtime and native allocator. Reuse final-stage planning of the safe parent
checkpoint; retire only authoritatively finished child contexts; directly discard
settled private suffixes without D2H; use actual separate FULL/Mamba free lists
and next-input/decode reserves; coordinate bounded ready-parent admission and
residence until first consumption. Shared ancestors, active references and
inflight transfers remain protected. Settled child device release may rearm a
capacity-blocked parent restore. Add no new switch or frozen-workload change.

Keep the complete V22 predictive audit and compact source-hashed evidence in
v63_v22_terminal_capacity_complete_20261011.json and
v63_child_parent_capacity_handoff_complete_20261011.json. The arm ends with
155 completed/1 incomplete, 367 dynamic children and 31 multi-round workflows.
Confirmed children cumulatively release device FULL 117.868 GB and Mamba
111.973 GB, excluding internal calls. Of 188 observed JOINs, 172 have pre-JOIN
sibling device release and 91 subsequently submit before JOIN and prove
following-parent FULL reuse, 1.356 GB. All 190 parent first completed GPU batches
link to prior child release; scheduler-observed last RETURN to that batch
P50/P90 is 222.46/689.11 ms, maximum 16.312 s. This is not prediction error,
kernel-start latency, simultaneous free capacity or exclusive parent ownership.
Restore after the last child's RETURN remains demand recovery. No actual
capacity-retry rearm is observed. Wait for the same-version native comparison
before any throughput/JCT claim. V23 is queued at the 11:41 e82ad88 target.

Keep the now-complete V22 busy-window evidence in
v64_v22_busy_window_same_version_20261011.json and
v64_v22_busy_context_600_900_20261011.json. Native has covered all three
requested windows, although the complete arm and pair are still running.
At600-900s, predictive/native decode throughput is1019.20/1219.52 token/s,
GPU utilization76.68%/89.81%, and time-weighted decode batch47.27/47.38.
At1200-1500s, throughput is1042.79/1197.59 token/s. Terminal recycling
and useful pre-boundary restoration do not establish a native-relative gain.

Inspect replacement-prefill and control-path costs using the measured geometry:
mean visible decode context23068/24068 tokens; batch48 in the22528-24575
mean-context bucket has worker mean22.07/21.90ms, while batch47 in that
bucket has86.79/71.38ms. Window-wide prefill mean is28.81/18.51ms.
These worker intervals include scheduler/overlap work and are not CUDA
kernel timings or matched-demand causal estimates. Predictive's exclusive
Python wall counters total46.428s over the preceding300.344s control
snapshots: admission6.338s, reentry5.618s, JOIN PREPARE4.724s,
control drain4.149s and handoff enqueue3.510s lead the measured phases.
Do not extrapolate writer samples or subtract these counters from JCT.
Validate the already queued V23 cost reductions and complete terminal-anchor
recycling on the GPU pair. Preserve the running V22 native and current V23
registration; keep the performance goal active until actual improvements
are verified.

Retain the later complete 2400-2700s window in
v66_v22_busy_window_2400_2700_20261011.json and
v66_v22_busy_context_2400_2700_20261011.json. Predictive/native decode
throughput is 1139.97/1082.35 token/s, a local 5.32% advantage; GPU utilization
is 86.29%/79.59%, weighted decode batch 47.42/47.31, and visible context
24084/23891 tokens. Total H2D is 25.225/1.663 GB, with transfer-stream
0.964/0.066s. This later low-migration native window does not establish
attributable handoff/prefetch gains or negate the early busy-window regression.
Predictive exclusive Python wall remains 31.495s, including reentry 7.526s,
control drain 3.408s, tool PREPARE 2.370s and opportunity sampling 2.354s.
Keep the source-hashed interpretation and capacity handoff evidence in
v66_child_parent_handoff_status_20261011.json. Preserve both unfavorable and
favorable windows, the live V22 pair and queued V23 e82ad88; validate actual
single-leaf normal-path costs and complete-pair JCT before accepting the goal.

Fix the demonstrated terminal-history coverage loss before the next V23 launch.
More than 64 indexed component anchors previously forced latest-only provenance
in both direct retirement and close-before-RETURN, then released all references
and lost the historical private branches from retries. A real CPU allocator
fixture with 65 branches owns 66 FULL tokens; the old code frees only one and
returns no retry anchors. Ordinary eviction can still reclaim the remainder;
the affected real V22 child count is not established.

Terminal close/retirement now request all valid indexed anchors. Ordinary action
snapshot defaults and per-call reclamation/retry budgets remain unchanged.
Add no whole-tree scan or D2H. Twelve new 65/128-branch, close-order, shared-parent
and historical lock/H2D cases fail before the fix and pass afterwards.
Native 164, related BeliefKV 457 and native materialization 7 checks pass, 628 total.
Keep v63_terminal_anchor_recycling_fix_20261011.json. New canonical patch:
4af68951beefdf759901d0143ca747c6893aa1ffc7124a3738158512851f7e60;
expected/actual tree a49cf2d2f643afaaf188f693cfee0c08373bba81.
The queued native delta touches only session_ref_tracker.py, unified_radix_cache.py
and the terminal-capacity test; forward deployed transition and reverse checks
pass. Committed as e82ad88 and registered as the next waiting V23 target;
preserve the frozen V22 pair and all workload/model settings.
Allocator coverage is not a GPU speedup.

Reduce repeated request-identity allocations after a CPU profile of108 waiting
requests with16 retained rounds identifies_request_key cost. Admission planning,
terminal queue checks, reentry reads and lease first-service processing may reuse
the existing runtime.visible identity only after checking every current request,
workflow, invocation, context, epoch, attempt, session and generation field.
Changed fields still produce a new identity or reject malformed input. Add no
cross-call metadata/residency cache or scheduling guard.

The328 related admission/runtime/handoff/terminal/tool checks pass, including14
changed/invalid-field cases and ordinary/streaming session normalization.
Keepv61_bound_identity_admission_cpu_20261011.json with pinned2252bbc planner,
identity-parser and frontier sources. Four synthetic admission cases,61 alternating
samples each, reduce mean wall10.6%-15.0%. Two plans over156 requests are2.830
to2.427ms; over108 with resident ordering, parent handoffs and semantic progress,
2.237 to1.902ms. Queue order, native inspection counts and completed-batch state
are equal. Resident inspection is a stub; physical restoration, IPC, DMA and GPU
are excluded. Commit and include the change in the still-waiting V23, preserving
the frozen V22 pair; CPU improvements do not prove native-relative performance.

Retain the four-step terminal-child capacity handoff and reduce complete-prefix
inspection cost. Within each RadixKey.is_prefix_at call, reuse logical lengths
while preserving namespaces, bigram boundaries, limit/offset/page semantics and
current token-buffer reads. Add no cross-call residency cache.
Keepv60_reentry_prefix_lengths_cpu_20261011.json:15 synthetic complete-reentry
cases with actual RadixKey,31 alternating samples and100 calls per sample all
return equal observations. Median wall reductions are1.6%-9.1%;256-depth one/eight
leaves are441.17/488.04 to410.25/450.44us, and1024-depth are1685.72/1815.14
to1532.07/1698.89us. The perf_counter measurements andp50_cpu_us labels are
CPU-fixture wall intervals, not thread CPU, CUDA or measured GPU throughput.

Actual patched native checks:131 passed. Related BeliefKV checks:304 passed
across the initial297 successes and the corrected7 materialization cases;
explicitly use the isolated native source for that module because this worktree's
submodule is uninitialized. Keepv60_prefix_native_delta_validation_20261011.json.
The queued d6d6470 native delta touches onlyradix_cache.py and its prefix test;
the read-only deployed-V22-to-new-patch git apply --check passes and the live
canonical engine tree remains unchanged. New patch SHA256:
fb6aecfc1f72f4ad82e8ca83a8379980ba036883573dd59d9875458ba4989100.
Committed ascc2fac4 and registered in the next waiting V23 target, preserving
the frozen V22 pair and configuration. Capacity release, equal outputs and CPU
cost reductions do not complete the native-relative performance goal.

Keepv58_v22_child_parent_capacity_and_summary_gap_20261011.json with the
10:29:41 CST lifecycle-audit SHA256 and independent client-event SHA256.
The partial live V22 audit has364 confirmed dynamic children with cumulative
device FULL116.806GB/Mamba109.783GB release, excluding internal calls.
Of188 observed JOINs,169 have pre-completion sibling device release and89
subsequently submit before JOIN and prove following-parent FULL reuse,1.332GB.
All187 parent first completed GPU batches link to child release; scheduler
last-child RETURN to that batch P50/P90 is223.28/691.96ms, maximum16.312s.
This is neither prediction error nor kernel-start latency. Total early-and-reused
JOIN FULL is1.617GB and tool FULL3.643GB; demand handoff reuse128.325GB remains
separate. No real capacity retry rearm is observed. Cumulative release is not
simultaneously free or exclusive parent capacity; same-version native is pending.

Trace xarray-4356's184s ACK-to-service tail: the tool lasts423.61ms, then two
foreground context-summary model requests take97.61/85.14s, both length with
empty visible output. The summary phase spans182.95s and changes the37120-token
target to a next input sharing only8252 tokens; FULL is not reused.
Release speculative next-consumer residence when this same parent has an observed
active blocking context-summarizer CALL. ACK during the call does not acquire a
new lock. Keep the parent's data/session/epoch and already submitted demand
protection; failed summary must leave the parent resumable. Do not change summary
execution, prediction, admission or workload settings or extend leases.
The486 related checks pass. Include the follow-up in the next waiting frozen
target; V22 remains unchanged. This does not remove summary work or prove GPU benefit.

The residence fix and capacity evidence are committed asd6d6470; prefix-length
reuse iscc2fac4, bound-identity reuse is2aa19f2 and full terminal-branch
provenance is e82ad88. At2026-10-11 11:41:08 CST, register the waiting V23
target as e82ad88446db6eb663c92fac47727c01308e5bb6 in
beliefkv-v63-terminal-anchors-20261011; coordinator3756458.
At11:41:33 CST, verify its reparenting to PPID1 with unchanged start identity
586033093. Configuration and captured previous-pair identities match2aa19f2;
the native delta is limited to the verified two files and terminal-capacity test.
Stop only waiting coordinator3378541; retain its registration as
v23_launch_after_v22_2aa19f2_superseded_20261011.json and its log.
Keepv63_v23_terminal_anchors_registration_20261011.json as the bounded receipt,
including the original helper-child identity and the subsequent PPID1 observation.
Main remains clean at3aa27dd, V23 has no run directory, and V22 collection processes
are not signalled. Deploy after the complete V22 pair and postprocessing, then
predictive followed by cold same-version native. The performance goal remains active.

Reduce operational tool-curve cost after the CPU profile identifies repeated
dense coefficient scans at18 knots. Select coefficients for this curve's feature
names once, retaining original coefficient order, nonfinite terms, sigmoid,
calibration and isotonic projection. Do not cache predictions across requests,
change quantiles or alter transfer policy. Twenty-four mapping/object, elapsed,
cancellation and nonfinite-coefficient cases match dense point predictions
exactly and round-trip unchanged;603 relevant checks pass.
Keepv57_tool_curve_cpu_20261011.json: baseline is58f2097's already optimized
tool-only entry, shared35MB calibrated parameters and each revision's pinned
curve function. Complete worker mean wall is1.283→0.841ms for one input and
15.211→9.477ms for16, reducing34.5%/37.7%; full predict and nonempty hints
are exactly equal. Inputs remain a synthetic elapsed matrix. This reduces CPU
work, not proven GPU stalls or JCT.
The change is committed as6793959. At2026-10-11 10:34:17 CST, update the waiting
V23 target to6793959e6b37a03ceda2a47ed489a87cab6ccda7 in
beliefkv-v57-tool-curve-fast-20261011; coordinator3297034, now with PPID1.
Configuration and captured previous-pair identities match58f2097; the canonical
engine patch is unchanged. Main remains clean at3aa27dd and V23 has no run directory.
Stop only old waiting coordinator3255311; preserve its registration as
v23_launch_after_v22_58f2097_superseded_20261011.json and retain its log. Keep
v57_v23_tool_curve_registration_20261011.json as the bounded receipt; the main
v23_launch_after_v22_20261011.json remains current. V22 collection processes are
not signalled. Deploy after its complete pair and postprocessing, then run
predictive followed by cold same-version native. GPU performance remains unproven.
This10:34 registration was superseded by later revisions; the current11:41
target is e82ad88.

Terminal-child to parent capacity handoff is already in the frozen V22: retire
confirmed finished contexts, release session references, and directly discard
settled unreferenced private suffixes through the native allocator without D2H.
Keep shared ancestors and retry pending native dependencies. Track FULL/Mamba
release separately; earlier sibling release can support restoration while the
final child still generates. Restoration that needs the last child's post-RETURN
capacity remains demand handoff. Use native free lists rather than fixed page
identity, preserve next-input/decode reserves, and apply existing bounded ready-
parent admission and residence until first service. The latest partial v58 GPU evidence
above establishes observed release and following-request reuse, not native-relative
performance improvement.

Remove the first tool query's unconditional50ms delay while retaining unchanged-
wait adaptive150/500/1000ms throttling. The worker now uses a tool-only prediction
entry that retains residual wait, prompt-growth support, calibration, OOD and
operational timing, but skips unused boundary, decode, next-output and child-
completion heads. Preserve full predict call order/output and fallback for custom
models. Five feature contracts, successful/failed repeated commands, project
history, overrun, missing support and operational-curve parity are covered;565
relevant model/worker/runtime/terminal/handoff/lifecycle checks pass.
Keepv56_tool_wait_model_cpu_20261011.json: actual35MB calibrated artifact,
606cc4e old predict and current tool-only entry share unchanged loaded components.
Complete predict_tool_wait_batch mean wall is1.571→1.243ms for one input and
19.743→15.322ms for16, reducing20.9%/22.4%; nonempty replies are exactly equal.
The synthetic elapsed matrix is not a workload-frequency sample; exclude IPC,
queueing, DMA and GPU and do not extrapolate throughput. Include this committed
change in the not-yet-started V23; preserve the running V22 pair.

Commit the code, verification and documents as58f2097. At2026-10-11 10:21:26 CST,
update the still-waiting V23 target to58f2097c7a0c6c098d6a6d81abaadb69663e2522
inbeliefkv-v56-tool-wait-fast-20261011; coordinator3255311. Configuration,
previous pair, captured wait-process identities and canonical engine patch match
the606cc4e registration. Main remains clean at3aa27dd and V23 has no run directory.
Stop only old waiting coordinator3111032; preserve its registration as
v23_launch_after_v22_606cc4e_superseded_20261011.json and its log. Keep the bounded
v56_v23_tool_wait_registration_20261011.json receipt; the main
v23_launch_after_v22_20261011.json remains current. V22 collection processes are
not signalled. Deploy after its complete pair and postprocessing, then run
predictive followed by cold same-version native. GPU performance remains unproven.
This10:21 registration was superseded by later revisions; the current target is
the11:41 e82ad88 registration above.

Fix capacity blocking between tool-prefetch candidates in the isolated follow-up.
Previously the nearest-ending tool took the sole ticket before reclaim was checked;
when capacity remained insufficient, the dispatch ended and a later restorable
candidate waited for another scan. Perform the existing cold-duplicate reclaim
before selecting that ticket, continue on insufficient capacity or a vanished
target, and re-estimate service/window after reclaim changes the bounded payload.
Retain dispatch-time revalidation, forecast quantiles,500ms configuration, pool
budgets and native dependencies. Four two-candidate cases and447 relevant checks
pass. Actual frequency, control cost and throughput benefit require the next GPU
pair; these checks do not demonstrate the goal.

Commit the tool selection fix, fixed-prefix audit and documents as606cc4e.
At2026-10-11 10:01:31 CST, register the not-yet-started V23 target as
606cc4eb2ded154a8b1c79dd7a8a31e26483b1fb in
beliefkv-v55-tool-capacity-selection-20261011; waiting coordinator3111032.
Environment, previous pair, captured wait identities and canonical engine patch
match18ea788. Main stays clean at3aa27dd and the V23 directory is absent.
Stop only waiting coordinator2903493; preserve its registration/log and the
v55_v23_tool_capacity_registration_20261011.json receipt. V22 collection processes
are not signalled. The main v23_launch_after_v22_20261011.json is current.
Deploy after the complete V22 pair and postprocessing, predictive first and cold
same-version native second.
This10:01 registration was superseded by later revisions; the current target is
the11:41 e82ad88 registration above.

Keepv54_v22_tool_service_window_fixedprefix_20261011.json and its local detailed
artifact. Re-read exactly the same817177733-byte opportunity prefix and lifecycle
labels asv48; input SHA256, original forecast errors and window categories match.
Add recorded-H2D-P90 service slack without turning it into a runtime gate. Of655
timely-window snapshots,361 have actual lead below recorded H2D P90. Among100
snapshots with observed free capacity,56 are below it. Only44 snapshots/42 tool
rounds have free capacity and actual lead at least that estimate; their summed
per-round maximum observed FULL is732037120 bytes, neither unique transfers nor
achievable savings. Categories overlap across snapshots. Service P90 excludes
planning/enqueue time and is not actual completion. Timely-plan actual lead P50
is83.50ms versus87.97ms median recorded H2D P90. For xarray-7393 child epoch57,
an80.630MB plan has5.052ms true lead against87.966ms recorded H2D P90.
Usev47 native transfer submit:9.271ms after the plan and4.220ms after completion.
The later control-record timestamp's17.510ms gap is not native submit latency.
Improve forecast/notification lead,
capacity and first consumption separately; the selected last-second prediction
error alone does not prove usable anticipation. V22 remains frozen.

Continue terminal-child capacity handoff by fixing candidate coverage before
bounded native reclaim. JOIN/tool anticipation and queued execution handoff
previously published eight candidates per pool before native excluded the target
prefix. The target's own backed nodes could fill that quota and omit eligible cold
nodes later in the registry. Compute the complete target-node tuple once, exclude
it before publication counters and pass the same tuple to native reclaim. Preserve
per-pool limits, ordering, identity, live references and transfer checks. Initialize
the sparse pool index at dictionary position16 even when that entry is excluded;
remove target IDs from both pool index sets and retain the underlying registry for
later targets. Six indexed/unindexed FULL/Mamba regression cases and367 related
checks pass. The actual V22 frequency and GPU throughput contribution of this
limitation remain unmeasured. Include the fix in the next frozen pair.

Commit the candidate fix as18ea788. At2026-10-11 09:42:56 CST, replace the
still-waiting V23 target with18ea788a0ea1933782eb5051e211c866e2e29579 in
beliefkv-v53-restore-target-candidates-20261011; coordinator2903493.
Environment, previous pair, captured wait identities and canonical engine patch
equal the prior e7bd8fa registration. Main remains clean at3aa27dd; V23 has not
created its run directory. Stop only waiting coordinator2616411 and preserve its
registration asv23_launch_after_v22_e7bd8fa_superseded_20261011.json and its log.
The main v23_launch_after_v22_20261011.json is the current registration;
retainv53_v23_restore_target_registration_20261011.json as the bounded receipt.
V22 processes are not signalled. Deploy after its complete pair and postprocessing,
then run predictive followed by cold same-version native. Capacity, residence,
useful early FULL reuse and busy-window performance still require GPU validation.
This09:42 registration is superseded by the10:01 606cc4e registration above.

Keep the complete2400–3000s V22 predictive window in the local
v52_v22_busy_control_2400_3000_20261011.json artifact with read-prefix and boundary
hashes. It has14015 decode batches,24406.595 mean visible context tokens and
33.873ms mean worker interval. Instrumented Python self wall totals55.805s:
reentry68439 calls/10.928s, control drain6.737s and opportunity sampling4.721s.
The63 measured eviction request_probe writer samples average10.671ms thread CPU
and11.946ms wall; do not extrapolate total/GIL cost from the sampling interval.
Same-version native is pending. Validate the queued writer, residence and candidate
coverage changes by actual early reuse and matched busy-window performance.

The V21 pair and postprocessing have completed: predictive has 155 completed
and one incomplete workflow; native has 156 completed. V22 revision3aa27dd
was automatically deployed at 2026-10-11 07:49:16 CST. Predictive is running,
followed automatically by cold same-version native. Frozen checkout:
beliefkv-v35-fragmented-handoff-20261011; coordinator1159560, driver1166290,
scheduler1166676 and client1168306. Registration is running_new_pair;
canonical native expected/actual tree matches
63d91509bee139b2d6a2285eb44b193a67a9e008. Keep
v22_supersession_08b804f_to_3aa27dd_20261011.json and the deployed registration
in the main workspace's experiments/reports/v22_launch_after_v21_20261011.json.
The deployed revision removes the cumulative16-extent demand-restore
cutoff, retaining16 extents per burst, live capacity/pending limits, original
expiry and native dependencies. It adds writer-window analysis and retains
the epoch-bounded tool-round audit below,
retaining terminal-child capacity handoff, capacity-settlement parent retries,
background writer CPU evidence, prefix writer reduction, shared first-service
input-prefix evidence, complete ancestor observation, aged-order inspection
reduction and same-dispatch tool plan reuse.
Before08:20 CST, real GPU requests, terminal-child allocator release, parent first
service and physical ACKs are present. Native evidence has zero dropped/failed
records and no writer error. Complete busy-window diagnostics and partial capacity
handoff evidence are now available below; same-version native is still pending
and the performance objective remains active.

Keep v40_v22_capacity_handoff_live_20261011.json as a partial live audit.
Of117 observed JOINs,54 have a known completion boundary and63 remain unobserved,
not failed. Fifty have confirmed dynamic-child device release before completion;
26 subsequently submit FULL before JOIN and prove reuse by the parent's following
request, totaling259522560 bytes. Their cumulative pre-JOIN release is FULL9.558GB
and Mamba5.216GB, neither simultaneously free nor exclusively assigned capacity.
The140 confirmed terminal children cumulatively release FULL30.980GB and
Mamba9.594GB; internal-call release is reported separately. All54 parent first
services link to prior child device release. Scheduler-observed last-child RETURN
to first completed GPU batch has P50/P90 of327.57/1247.41ms and a16.312s maximum,
not a RETURN prediction error or kernel-start latency. Twenty-one parents have
prior restore-lookahead selection, which is not transfer submission. No capacity
restore rearm is observed yet; the real-pressure retry path is not verified by this
snapshot. Multiple source logs remain live; this is neither a frozen final result
nor a native-relative saving.

Keep the09:10:53 CST partial full lifecycle audit in
v47_v22_prefetch_lifecycle_live_20261011.json and its bounded, SHA256-linked
v49_v22_capacity_handoff_progress_20261011.json summary. The256 confirmed dynamic
children cumulatively release device FULL72.814GB and Mamba37.217GB, excluding
internal calls. Of169 observed JOINs,109 have completion evidence and60 remain
unobserved. There are101 with pre-JOIN sibling device release and56 with actual
subsequent pre-JOIN FULL submission and following-parent reuse, totaling935055360
bytes. All early-and-reused JOIN FULL totals1.072GB; the113.663GB verified
execution-handoff FULL remains demand recovery. All111 linked parent completed
GPU batches have prior child device release; scheduler last-child RETURN to
completed batch P50/P90 is264.40/767.29ms, maximum16.312s. No capacity retry rearm
is observed. Do not treat cumulative release as simultaneously available or
exclusively assigned capacity, or the completed batch as kernel start.

Fix a traced handoff residence gap in the isolated follow-up: django-11141 is
selected with one frontier allowance57ms after native queue registration.
Its three extents ACK about51ms after submission with zero remaining frontier
slots and no recorded issue allowance. They obtain no residence lock and lose
native residency about386ms later; first worker interval starts16.29s after
registration and lasts20.99ms. FULL6.922MB is not proven reused, and the next
request still has176.947MB native FULL Host hits. This proves the gap, not that
the new fix removes all of the queue tail.

Record the existing issue protection allowance for single-node and batched demand
handoff, including rollback of unsubmitted tail receipts. Reuse the existing
same-capacity-source slot allowance at ACK while byte capacity, other-context
ownership, native page/dependency checks, the original3-second expiry and
allocation-pressure release stay live. Do not reserve a GPU running slot or
introduce another admission gate. The174 focused runtime/transfer/handoff/
terminal-capacity checks and187 common tool/runtime checks pass,361 in total.
At2026-10-11 09:23:57 CST, register this committed e7bd8fa follow-up for V23
without mutating V22. Frozen checkout:
beliefkv-v50-handoff-issue-protection-20261011; full revision
e7bd8faa08d380a33cb88ee98b71e5e3303a1917; waiting coordinator2616411.
Configuration, previous pair and canonical engine patch match the prior
registration. Only old waiting coordinator2402814 was stopped; no V22 process
was signalled. Preserve the previous registration as
v23_launch_after_v22_3644e8b_superseded_20261011.json and the receipt
v50_v23_handoff_protection_registration_20261011.json. Wait for the complete
V22 pair and postprocessing, then deploy, predictive first and cold native second.
Validate actual residence and first use in that matched pair; the new fix has
not run on GPU.

Keep v48_v22_tool_forecast_live_20261011.json and its detailed local artifact.
For6549 independent tool rounds with last-second accepted forecasts, the
round-weighted median absolute error P50/P90 is63.57/572.06ms. These selected
live traces do not establish heldout accuracy. Of575 rounds with a recorded
timely FULL candidate,95 also have observed free capacity. Snapshot categories
overlap. Use actual capacity settlement, residence and admission to explain
coverage;421.44GB residual next-request native FULL demand is not entirely
replaceable prefetch or an oracle JCT saving.

Keep v37_v22_busy_window_600_900_20261011.json with input-prefix and boundary-line
hashes. The complete V22 predictive600–900s window has6440 decode batches,
23068.33 mean visible context tokens and39.187ms mean worker decode interval;
old V21 predictive/native have5642/7363 batches,21840.58/23703.38 tokens and
44.189/35.510ms. These versions and trajectories differ, so this is diagnostic.
Instrumented V22 Python self wall totals46.428s versus60.398s in old predictive.
Largest measured V22 phases are admission planning6.338s, reentry5.618s,
JOIN PREPARE4.724s, control drain4.149s, handoff enqueue3.510s, opportunity
sampling3.239s and execution handoff2.915s. Cumulative wall is not removable JCT.

Background-writer window differences identify the next CPU target:
Host-eviction request_probe has30 measured samples averaging7.271ms thread CPU
and8.944ms wall. All native samples total421.851ms CPU/632.605ms wall;
opportunity samples total73.084ms CPU/145.430ms wall. Do not multiply by64
or infer GIL contention. Continue exact-routing index optimization for this
writer category in the isolated follow-up while preserving the frozen V22 pair.

The validated Host-eviction index routes by namespace and the largest power-of-two
prefix no longer than each terminal. The existing complete terminal hash still
proves identity. Above32 candidate lengths, advance one hasher and snapshot only
terminals on matching routes; small sets retain direct hashing. Keep separate
terminal references across FULL/Mamba, remove auxiliary routes after reaccess or
bounded-index expiration, and batch-clear indexes only when the current request
exactly matches every pending prefix. Partial matches and other namespaces retain
their original evidence and ordering.

Keep the undeployed two-pass and single-pass/per-entry-cleanup prototypes in
v38_eviction_route_writer_cpu_20261011.json and
v39_eviction_route_single_pass_cpu_20261011.json; they regress small/all-matching
cases. The final v41_eviction_route_batched_cleanup_cpu_20261011.json has36 groups
of60 alternating samples with equal records, hashes, counters, history and pending
state. For512 prefixes across32 branches,1024/8192/32768-token probe wall means
change1.046/1.040/1.918→0.508/0.625/1.463ms; all-matching cases change
7.087/7.333/7.954→6.655/6.847/7.405ms. The16-prefix matching sets no longer show
the prior regression; single eviction registration adds about2–4 microseconds for
the auxiliary route. The156 telemetry/lifecycle/writer/window checks pass.
These fixtures exclude real scheduler concurrency, DMA and GPU, and do not
establish throughput improvement. Writer code and evidence are committed asfe06e06.
The initial2026-10-11 08:27:31 CST V23 registration is preserved in
v23_launch_after_v22_fe06e06_superseded_20261011.json.
Preserve the08:46:35 CST revision73c9ac1 registration in
v23_launch_after_v22_73c9ac1_superseded_20261011.json.
At2026-10-11 09:06:17 CST, replace the not-yet-started registration with the
combined writer-index, admission-binding and single-native-spec revision3644e8b.
That registration was waiting_for_previous_pair with coordinator2402814.
Its frozen checkout isbeliefkv-v46-single-load-spec-20261011, target revision
3644e8b7f752005d861b5fb29c8648fb1467b898 and new canonical patch SHA256 is
498a2e8219f0d454776c7b967b87a82e7b49e3e75831389e08a9daabfb29998e.
Keep the live registration in the main workspace's
experiments/reports/v23_launch_after_v22_20261011.json. Preserve the old
registration, logs and checkout, plus
v23_supersession_73c9ac1_to_3644e8b_20261011.json. Keep the earlier
v23_supersession_fe06e06_to_73c9ac1_20261011.json as history.
Only the old waiting coordinator2167234 was signalled; no V22 coordinator,
driver, server, scheduler or client was signalled. The engine delta passes
git apply --check against the live frozen engine; retain
v23_single_spec_validated_engine_delta_20261011.patch. Captured wait identities
are V22 coordinator1159560 and driver1166290. Deploy only after the full pair and
postprocessing complete, then run predictive first and cold native second,
preserving workload and prediction windows. V22 source and processes remain
frozen; the09:06 registration is superseded by the09:23 e7bd8fa registration
above, which is in turn superseded by the09:42 18ea788 registration. V23 is not
yet running. Keep the objective active until useful early FULL
reuse and same-version busy-window performance benefit are both demonstrated.

Keep the complete1200–1800s control evidence in
v42_v22_control_1200_1800_20261011.json with exact read-prefix and boundary hashes.
The599.617s snapshot interval has82.818s of instrumented Python self wall:
reentry inspection78428 calls/19.956s, handoff enqueue1693/8.138s,
admission planning6793/6.982s, control drain6.613s and JOIN PREPARE6.359s.
Same-version native has not run. Do not subtract cumulative phase wall from JCT
or use this evidence alone to assign the GPU service gap.

Reduce admission request-key binding allocations without changing identity
validation or scheduling decisions. Read workflow/invocation/context directly;
keep exact types, session consistency, epoch, attempt and generation checks.
The12 additional invalid-required-field cases and relevant290 admission,
handoff, terminal-capacity, runtime and native-prefix checks pass.
The benchmark now loads each revision's compiler and direct request-key function;
other inherited helpers remain shared and the scope states that limitation.
Use a plain lambda for logical monotonic time so MagicMock does not retain
iteration histories and introduce GC noise. Keep oldv44 as nonfinal evidence.
The finalv45_request_binding_plan_light_clock_cpu_20261011.json contains17
groups of200 alternating iterations with identical plans, counters, promotion
metadata and native inspection counts. Mean wall for two planning calls falls
6.6%–14.7%;48 requests/4 leases changes1.216→1.101ms and156 requests/16 leases
changes2.510→2.216ms. These fixtures exclude real allocator, DMA and GPU.
Code and evidence are committed as73c9ac1 and included in the V23 frozen
registration above. Actual GPU execution and same-version native comparison
remain pending before any service-performance claim.

Reject the RadixKey raw-length reuse candidate after whole-inspector measurement.
Keepv43_reentry_length_reuse_cpu_20261011.json:15 groups of15 samples with100
iterations each show no stable broad improvement;256-node/one-leaf prefixes
change about435→437 microseconds,1024-node/one-leaf1703→1666 and eight-leaf
1800→1793. Preserve the expanded deep-prefix fixtures but withdraw the engine
candidate. Canonical patch SHA256 remains
03033a9e9d28fe217f9085752fd4db4618cda0430312238dd0e130579557aa83.
Continue using real control windows, settled terminal-child release, useful
early FULL restoration and first consumption, rather than isolated tiny-function
timings, to choose the next engine change.

Eliminate duplicate transfer-spec construction per predictive extent in the
isolated follow-up. Previously prefetch_gpu_session_node builds and checks
FULL/Mamba transfers before load_back builds them again under native locks,
including a second FULL Host-index concatenation. Move the existing single-node/
necessary-Mamba checks to the one locked construction. Keep live session and
complete ancestry checks, capacity, no additional device reclamation, native
layer dependencies and individual ACKs. Release acquired device/Host locks on
the previously handled spec failures; normal native demand restoration still
accepts its multi-node specs. The104 native shadow/handoff/latest-state checks
and25 subtests pass.

Keep scripts/benchmark_native_prefetch_load_cpu.py and
v46_native_prefetch_single_spec_cpu_20261011.json. These exercise whole native
prefetch bursts and load_back with the exact FULL/Mamba/tree spec builders;
allocator, lock accounting, enqueue and commit are deterministic CPU stubs.
They exclude runtime physical-ledger work, DMA and GPU. The24 groups of15
alternating samples,30 iterations each, preserve transfer indices, callbacks,
lock counts and outcomes. Spec construction falls from two calls per extent
to one. Median thread CPU falls9.9%–25.7%;64-node/16-extent/4096-token bursts
with final Mamba change678.15→545.86 microseconds, and256-node bursts change
1081.81→951.68. Do not extrapolate this to all8.138s of observed handoff enqueue
or to end-to-end throughput. The isolated canonical patch's expected/actual
tree matches286ef96cc4d5954b9e20a5e3cc96f7306f426acd. Code, fixtures and
evidence are committed as3644e8b and included in the V23 frozen registration.
The live V22 remains on its existing patch. Actual GPU effect and same-version
native-relative performance benefit remain unproven.

Closed V21 predictive/native output rates are738.64/831.87 token/s and mean
GPU utilization72.04%/80.15%. In600–2400s, mean visible decode contexts are
23679.53/23850.39 tokens, while mean worker decode intervals are41.49/36.19ms.
Retain v35_v21_busy_context_control_600_2400_20261011.json with source hashes.
Worker intervals are not kernel time, and whole-arm trajectories/outcomes differ.
Validate V22 control reductions, complete restoration and actual early use before
attributing the prior service gap or claiming improvement.

Fix delayed tool-observation attribution in the isolated follow-up. Requiring
following submission to be later than first_observation can skip the real next
request and fold another model/tool round into the completion label. Original
V21 xarray-4687 child epoch20 has one7.59ms read_file; the old audit counts two
tools and selects epoch22 instead of21. Bind following to the origin request
epoch, prove the bounded round from its intervening START/END events, and report
sampled tool IDs outside that round separately. Post-completion observations
and transfers remain post-completion evidence, never early predictions.
The66 lifecycle/tool-forecast/transfer-window checks pass. Closed V21 evidence
has been recomputed:252 tool completion/next-request links change, including
248 old multi-call labels that are single-call rounds.668 events are first
observed after tool completion;16 contain sampled IDs outside the bounded
round. Keep v33_v21_wait_round_attribution_20261011.json and all old audits.
Verified early-and-reused FULL remains JOIN1.419GB and tool2.530GB. Corrected
tool residual native FULL is585.285GB of observed demand, not achievable
prefetch or oracle savings. These corrections are not prediction or performance
improvements. This is an
offline evidence correction, absent from V21 and V22 runtime policies.
The corrected audit script is deployed with3aa27dd for V22 postprocessing;
the model, transfer and admission policies remain the same as queued 87370d3.

Add independent background-writer CPU evidence in the isolated follow-up.
Sample every 64th record per category with thread_time_ns and perf_counter_ns.
For native evidence, distinguish stream and event/internal-event categories.
Measure processing only, excluding queue waiting and status publication. Append
native cumulative snapshots to the existing audit stream each second, attach
opportunity snapshots when consuming existing admission_runtime_state rows,
and retain final status counters. Preserve authoritative event ordering, prefix
attribution, ACK flushes and scheduling policy. Use this evidence to separate
JSON processing, prefix checks and other writer work; CPU/wall differences alone
do not prove GIL contention. Measured samples are not full-run cost estimates.
The 243 scoped writer/telemetry/runtime/lifecycle checks and 19 timing/comparison
summary checks pass, with overlap. Keep scripts/benchmark_writer_timing_cpu.py
and v32_writer_timing_cpu_20261011.json. Its 16 groups of 1000 alternating samples
preserve every encoded JSON record. Mean per-record added costs for
1170/8543/20002-byte fixtures are 0.498/0.595/0.844 microseconds. This excludes
actual queue, disk, prefix processing and concurrent scheduler work; it is not
GPU performance evidence. This instrumentation is registered in the frozen
3aa27dd revision and collecting in V22; V21 did not collect these timings.

Extend scripts/analyze_busy_context_control.py with background-writer window
analysis, consuming native snapshots during the decode scan and opportunity
snapshots during the control scan. Difference on each writer's snapshot_ts_ms,
not its potentially delayed producer timestamp. Retain measured sample counts,
thread CPU/wall totals, exact boundary-row hashes and snapshot lag. Do not
multiply samples by64, use cumulative maxima as window maxima or report absent/
incomplete collection as zero cost. The12 window/writer checks pass. Recompute
V21's real600–900s window: all original decode geometry, control and cohort
metrics remain identical; all three writer profiles are explicitly uncollected.
Keep v34_busy_writer_analysis_validation_20261011.json. This offline analysis
is now registered with3aa27dd. Run after V22 collects
complete windows; this preparation is neither measured writer attribution nor
GPU performance improvement.

Keep scripts/audit_tool_forecast_windows.py and the corrected compact
v33_v21_tool_forecast_epoch_bound_20261011.json. Preserve v31 and the original
audit as historical evidence. Store detailed per-round evidence with the closed
V21 predictive output and pin its SHA256 in the summary.
Reconstruct the recorded NativeToolWaitHint at actual acceptance time, using
survival-conditioned mass/CDF/quantiles and proven whole-tool-round completion.
Exclude expired, post-completion and exhausted-support forecasts. Of 11818
single-call rounds, 11104 have observations in the actual last second. Compute
each round's median absolute error in that interval before aggregating across
rounds: P50/P90 are 52.42/424.42ms; median signed error is +6.42ms. This is selected,
fixed-trace near-completion evidence, not overall or heldout prediction accuracy.
Of 10132 single-call rounds entering the predicted 500ms window, 8405 first enter
within the actual last 500ms and 1727 earlier. Report multi-call rounds separately:
of147 real multi-call rounds,94 have a first entry,62 inside the actual last
500ms and32 earlier. Median/P90 actual lead is236.47/6299.04ms. The old4741.60ms
median included another model round and cannot justify a blanket seconds-scale
bias shift. Investigate the remaining real multi-call tails separately.

Recorded positive FULL plans show 381 independent rounds inside both actual and
predicted runtime start windows, totaling 33.011GB of per-round maximum planned
FULL; only 61 rounds and 1.692GB also report enough current free-list capacity.
There are 150 rounds in the actual window while the forecast remains outside;
their capacity-fitting subset is 25 rounds and 1.404GB. These runtime-selected
plan snapshots can overlap categories and share pages. They are not unique
achievable bytes, transfer completion, residence, admission or speedup. Validate
terminal-child release, complete-prefix coverage and actual next-request use;
track multi-tool timing separately. This offline diagnostic changes neither
V21 nor the deployed V22 policy. The 127 scoped audit/tool checks
pass.

Reduce background prefix-evidence writer work using direct native integer-array
conversion and exact array comparisons. Keep independent served-input snapshots,
the original integer-normalization compatibility, session isolation, keyed
eviction identities, record ordering, recompute proxies and attribution counts.
Locate a divergent token exactly within the first unequal range; never infer
equality from a sampled prefix. The167 telemetry/lifecycle/terminal-capacity
checks pass. Preserve scripts/benchmark_native_prefix_writer_cpu.py, which
isolates each revision's writer methods, and
v30_native_prefix_writer_cpu_20261011.json. Its24 groups of100 alternating CPU
samples preserve records, hashes, counters, history and pending attribution.
For32768-token lists, served-snapshot-plus-appended-probe wall means change
8.719→1.966ms; late divergence6.804→1.613ms. A16-prefix eviction reaccess
list changes3.329→1.397ms; an existing array changes0.640→0.669ms without
speedup evidence. The closed V21 predictive output has44790 context probes
comparing951763303 common token positions, with21261-token median. Retain its
source SHA256. These are neither extra useful KV bytes nor avoided JCT. This
fixture excludes concurrent scheduler work and does not establish GIL contention
as the cause of the GPU performance gap. The validated reduction is deployed
in V22 revision3aa27dd; V21 retained its original version.

The isolated follow-up skips resident-order reentry inspection for requests
already in the ten-second arrival-order aging class. Their residency cannot
change the sorting rank. Selected execution handoff and parent restoration
still inspect the actual checkpoint; younger resident-first ordering and
restore-consumption priority retain their existing behavior. The270 scoped
handoff/runtime/terminal/tool checks pass. Keep
v28_aged_residency_inspection_cpu_20261011.json:17 fixture groups,100 alternating
samples plus10 warmups preserve both plans, policy counters and promotion
identity. All omitted native inspections belong to already-aged requests.
For fully aged48/156-request queues, two-plan wall means change from
1.110/2.504 to0.689/1.965ms;48-request native reads change2344→0.
This uses a deterministic native-inspector stub, includes warmups in read counts,
and is not GPU throughput proof. Unaged timing differences are noise rather
than evidence of savings. This validated revision is deployed in
V22 revision3aa27dd; V21 retained its original version.

Reduce synchronous first-service prefetch evidence work by retaining the
matching input prefix only within the current request's recording call.
Subsequent extent snapshots still compare their shared prefix using native
array equality and inspect any new or divergent suffix. Shorter prefixes,
divergent inputs and cache namespaces remain independently reported.
Actual FULL reuse still requires live node/creation/value identity and the
materialized request prefix. Remove the256-node ancestor traversal truncation,
retain cycle detection and verify an earlier ancestor on a valid300-node path.
This changes evidence collection, not prefetch or admission decisions.

The151 telemetry/lifecycle/terminal-capacity checks pass. Keep
v29_prefetch_use_shared_prefix_cpu_20261011.json:12 groups of100 alternating CPU
samples preserve every evidence record and consumed command. For16 extents
and32768 tokens, complete/divergent-input means change11.619→2.127ms and
7.611→1.307ms. Single-extent complete-input means1.283→1.287ms do not establish
a speedup. The finished V21 predictive source has16821 first-service command
records for4285 requests,3001 with multiple target prefixes. Reported matching
prefixes total262613488 tokens, including199168503 repeated matching positions
within the same request. Preserve the source SHA256. These counts are neither
additional verified reuse bytes nor removable JCT; the fixture excludes writer,
allocator, DMA and GPU work. The validated follow-up is deployed in
V22 revision3aa27dd; V21 and its evidence retain their version.

Keep the reproducible scripts/analyze_busy_context_control.py and
v21_busy_context_control_600_900_20261011.json with source-prefix and boundary-row
SHA256. The fully observed600–900s window has visible decode context means
21840.58/23703.38 tokens for predictive/native. Longer average contexts do not
explain the predictive gap. Within the22528-token mean-context bin, batch48
worker means are23.015/21.983ms and batch47 means87.449/74.284ms; batch47 in the
20480-token bin has101.013/68.980ms. These cohorts are diagnostic rather than
identical-demand causal evidence. The300.253s predictive control snapshot delta
contains60.398s instrumented exclusive Python wall time, including reentry11.090s,
JOIN PREPARE7.681s, control drain6.210s and admission planning6.064s. Investigate
admission/recovery work when slots open before attributing the gap to kernels.
These sums cannot be removed directly from JCT; not all reentry cost is avoidable.
This script counts5642/7363 batches completed within the half-open window;
the earlier5643/7364 counts include clipped intersecting intervals.

## Child-to-Parent Capacity and Complete-Prefix Recovery

The four-step path is deployed in V21: plan the parent's safe checkpoint during
child finalization, retire only settled unreferenced private suffixes after real
RETURN, restore using actual native FULL/Mamba free lists, and give the submitted
JOIN-unlocked dependency-ready parent bounded admission priority. Earlier sibling
release can support pre-RETURN H2D. Last-child release supports demand restoration
or parent growth. Do not claim exclusive capacity ownership or reuse of page IDs;
native allocation and running-slot management remain authoritative.

The isolated follow-up fixes a missed retry when a parent's handoff closes for
no_cold_capacity and linked child pages are freed later after DMA/lock settlement.
Remove that current parent's one-shot attempt only on actual linked device release,
after verifying JOIN completion and visible request/session/epoch identity.
Keep retries disabled without new capacity, for Host-only release, stale requests
and other failure reasons. Record parent_capacity_handoff_restore_rearmed as
demand restoration, separate from early prediction.

Add protection_reason and protected_by_command_id to residency registration.
Distinguish an existing covering lock from occupied/absent frontier slots, byte
budget exhaustion and an unobservable native closure. Partition FULL transfer and
reuse by those reasons; link rearmed requests to observed parent service using the
same workflow/context/JOIN/request and causal time order. Do not infer a miss
from native_locked=false. The 431 scoped terminal/handoff/runtime/physical/tool/
lifecycle checks pass. V21 retained its frozen revision; the fix is deployed in V22.

Remove the demand handoff's cumulative16-extent cutoff, retaining16 extents per
burst. Closed V21 has81 requests stopped after16 ACKs, all before linked first
service; cutoff elapsed P50/max are39.85/291.90ms, well before the original
two-second lifetime.68 first services still record FULL Host hits totaling
1080888 tokens. The22.136GB byte proxy is native request cache accounting and
may include coalesced or already-restored ranges; it is not exclusively
attributed residual physical H2D or achievable savings.
Keep v35_v21_handoff_extent_limit_20261011.json with all request rows.
Within the unchanged expiry, inspect remaining safe checkpoint pages and
continue bursts fitting current native capacity and pending-action slots.
Do not renew the deadline or relax page/session identity, native pipeline
dependencies, receipt verification or ordinary fallback. Nonoverlap deferral
must also follow the live fragmented restoration beyond16 extents.
The205 handoff/terminal-capacity/runtime checks pass. This is deployed in
V22 revision3aa27dd and collecting GPU evidence. Demand restoration remains separate
from pre-RETURN prefetch; tests and Host-hit proxies are not throughput/JCT proof.

Add offline sibling_release_windows to the existing terminal-capacity audit.
Link actual dynamic-child device release and the observed complete JOIN by
workflow/parent context/JOIN. Count early FULL only if its actual submission is
after a linked device release and before complete JOIN, and the existing wait
audit proves FULL reuse by the following request. Host-only release, post-JOIN
release, internal summary calls, other JOINs and missing reuse proof cannot
qualify. Missing completion leaves the window unknown. Report release lead,
actual per-pool capacity and qualified command bytes; temporal association is
not exclusive page handoff or native-relative savings. This isolated offline
audit does not mutate V22's frozen runtime or postprocessing.
The66 lifecycle checks pass. Recompute closed V21:159 of187 observed JOINs
have prior sibling device release;86 then submit proven early-and-reused FULL,
totaling1.207GB. Cumulative pre-JOIN device release is FULL41.998GB and
Mamba32.452GB, not simultaneously free capacity. First sibling release precedes
JOIN by a649s median, so do not assume that capacity remains available. All
previous summary metrics are unchanged. Keep
v36_v21_sibling_capacity_windows_20261011.json and
v36_capacity_handoff_deployment_and_audit_20261011.json.
Before08:02 CST, at least five real V22 demand restorations exceed16 extents, reaching23.
This proves cutoff removal, not anticipation or service-performance improvement.

Invalidate only the affected parent's semantic/tool restore observations after
linked terminal-child device release. On ledger-validated completed PREPARE,
invalidate the current matching or next-epoch context observation and rescan its
active tool wait. This removes stale100ms negative observations after physical
progress; it does not guarantee a100ms saving or authorize prefetch. Preserve
unrelated caches and all current page/capacity/time/residency checks. Untagged
native ACKs, invalid receipts, stale sessions, Host-only release and no-capacity
retries do not trigger invalidation. There is no new decode-loop scan. The261
terminal/tool/runtime/handoff checks pass. This revision is now included in the
deployed V22 revision3aa27dd; deployment alone is not performance acceptance.

Reuse only a tool restore plan freshly read during the current dispatch. Refresh
plans from the short cache or an earlier selection. Preserve the post-event-drain
checkpoint, extent, timing and capacity checks and native per-node reservations.
The 267 scoped runtime/tool/terminal/handoff checks pass, including tool completion
and checkpoint replacement during the drain. Keep
v27_tool_dispatch_cpu_20261011.json: 100 alternating CPU samples retain ordered
bursts and ledger reservations; four/300-node dispatch means change from
0.858/9.117 to 0.683/6.672 ms. The optimized 300-node maximum remains 19.507 ms.
This is a CPU planning result, not GPU throughput or end-to-end savings. The
committed change is deployed in V22 revision3aa27dd; V21 retained its version.

Keep v21_busy_window_same_version_evidence_20261011.json with the original
comparison SHA256. Both V21 arms completely observe 600–900 s: predictive/native
decode throughput is 891.68/1165.71 token/s, GPU utilization 69.26%/87.60% and
time-weighted decode batch 47.18/47.36. Predictive is 23.51% slower in this window.
The mean worker decode intervals 44.19/35.50 ms are not CUDA kernel timings.
Capacity handoff associations do not establish service performance improvement,
and similar batch sizes do not explain the measured gap. Native collection is
now complete; this window still cannot establish causal speedup. Validate
deployed V22 live-pool geometry, complete-prefix coverage and control reductions with
actual early FULL reuse and parent service in the next same-version pair.
The broad performance goal remains active.

Complete valid checkpoint ancestry must remain observable. The next revision
removes artificial 64/256-node rejection from action closure, PREPARE/H2D
planning, native session primitives and reentry inspection. Retain cycle and
live identity checks, session ownership, native locks, DMA dependencies and
the single-call shared-prefix memo. Restore at most 16 extents per burst under
the existing byte/capacity/time budgets. The terminal-reclaim max_nodes is a
retryable operation allowance, and explicit 64-node terminal diagnostic sampling
is read-only; neither should become an eligibility cap. Accept valid shared
leaves with more than 64 sessions while retaining malformed identity checks.

Keep v21_prefix_bound_rejections_live_20261011.json as partial observed evidence.
There are 1643 different tool-wait context/epoch/JOIN identities with a rejected
plan and 4792 distinct affected requests with 10938–48936 prompt tokens, below
the 131072 context limit. Of 18574 records, 9846 are terminal-only diagnostics;
the remainder still contains repeated sampling. Do not treat these as independent
realizable prefetch opportunities or a measured speedup.

494 BeliefKV checks and 186 native CPU checks plus 18 subtests pass, covering
65/300-node paths, safe-input clipping, necessary state only, 16-step bursts,
cycles/stale identities, real allocator release and shared-prefix preservation.
Forward-tree and reverse-apply validation gives canonical patch SHA256
`03033a9e9d28fe217f9085752fd4db4618cda0430312238dd0e130579557aa83`
and exact expected/actual engine tree
`63d91509bee139b2d6a2285eb44b193a67a9e008`.
Keep v22_complete_prefix_reentry_cpu_20261011.json and
v22_deep_prefix_reentry_cpu_20261011.json. Short-path median costs change by
about -0.7 to +4.9 microseconds; newly supported complete 65/128/300-node
observations take 111–615 microseconds. Old fast rejection and new complete
inspection perform different work and must not be reported as a speedup.

This committed revision and the capacity-settlement retry are deployed in V22
with3aa27dd after the completed V21 pair. Preserve superseded registration
states and the old V21 artifacts. Run predictive first and cold native
second, and measure actual early FULL reuse, parent service wait, protection
reasons and busy-window throughput. The broad performance objective stays active.

The validated deep-prefix observation reduction is registered in frozen V22
revision 87370d3, preserving superseded registrations and receipts. Within one closure
read, validate newly encountered native node/component classes once and validate
the current node's lock/reference counters together. Continue reading identity,
creation time, lengths, locks, references and pending DMA for every node.
Do not cache physical residency or capacity across calls. The 240 scoped
observer/physical/terminal-capacity checks pass, including changed ancestor types.
The canonical native patch SHA256 and engine tree remain unchanged.

Keep v25_checkpoint4_count_reuse_cpu_20261011.json and
v25_checkpoint300_count_reuse_cpu_20261011.json. In 100 alternating CPU samples
against 629eaa4, complete 300-node closure/H2D/FULL-only PREPARE planning means
change from 1.536/1.995/1.920 to 1.453/1.925/1.810 ms with identical complete
observations and 16-extent plans. Four-node closure changes from 33.57 to 32.91
microseconds, while full planning means do not show a stable improvement.
Do not treat the initial type-only run, fixture CPU reductions or their sum as
proof of GPU throughput or an end-to-end saving. Preserve the frozen V21 pair
and validate actual useful early FULL coverage, consumption and busy-window
throughput/JCT under the next predictive-first, same-version cold-native pair.

An earlier registration at2026-10-11 04:47 CST used PID3439480 in
waiting_for_previous_pair with target checkout beliefkv-v26-validated-20261011,
revisioned974d2. Keep v22_supersession_658f639_to_ed974d2_20261011.json and the
preserved658f639 and629eaa4 registrations. The environment and canonical native
patch SHA256 are identical to the previous registration. The updated target adds
single-plan root ordering reuse, physical-progress observation invalidation and
independent burst attribution. Do not edit the registered detached checkout.
Commit documentation and receipts only in the editable follow-up worktree.
The live V21 source, engine and processes remain frozen; after its full pair
and postprocessing complete, the coordinator deploys exactly the registered
revision and runs predictive first, then cold native. Waiting registration is
not completed GPU validation or achievement of the broad performance goal.

Update v25_v21_capacity_handoff_progress_20261011.json with the final predictive
source audit path and SHA256. V21 predictive completes155 workflows with1
incomplete in11375.87 seconds; same-version cold native has completed.
Its354 confirmed dynamic children release cumulative device FULL115.495GB and
Mamba120.858GB. Of188 observed parent services,185 follow
linked device release. Last child scheduler RETURN observation to first observed
completed parent batch P50/P90 is209.57/493.56ms; request queue to that batch is
168.29/500.65ms, with an18.906s maximum. Independent early-and-reused FULL is
1.419GB from91 JOIN events and2.530GB from84 tool events;99.782GB reused demand
handoff FULL stays separate. Only23 JOIN node commands precede server EOS.
These are predictive-arm lifecycle associations, not simultaneous free capacity,
exclusive page ownership, exact kernel-start times or native-relative savings.
The queued V22 changes are not deployed in this snapshot.

Reuse the current H2D/PREPARE plan's root-first node order for checkpoint
selection and transfer-step generation, preserving full ancestry, identity,
input clipping, necessary Mamba and physical budget checks. The207 existing
physical/execution-handoff/terminal-capacity checks pass. Keep
v26_checkpoint300_root_order_cpu_20261011.json and
v26_checkpoint4_root_order_cpu_20261011.json. Against658f639,100 alternating
CPU samples preserve observations and plans:300-node H2D/PREPARE planning
means change1.760→1.732ms and1.664→1.647ms;four-node means change
87.01→85.98 and66.28→65.13 microseconds. Unchanged phases remain noise.
This modest CPU reduction is not throughput proof. It is not part of frozen V21
and is now included in the updated registered 87370d3 V22 checkout.

The latest independent-wait evidence makes complete-prefix coverage the next
runtime validation priority. Keep v25_v21_prefetch_gap_attribution_20261011.json:
4433 tool waits observed node ancestry exceeds bound and are associated with
298.122 GB of remaining native FULL Host hits and no verified early FULL reuse.
The already queued V22 removes this restriction. There are 1105 tool waits with
positive FULL plans and 256.066 GB residual native demand, 9593 with only observed
zero plans and 327.839 GB, and 12 without a plan observation. Zero or absent plans
must not be interpreted as absence of physical demand.

Track planning reasons, blocked_detail and no_live_detail once per independent
wait per reason, with residual native FULL bytes and known/unknown evidence.
Evidence categories partition events; reason groups overlap. The 464 waits with
no_cold_capacity associate with 122.589 GB and may overlap depth rejections.
Neither byte association proves all demand could have been moved early or is an
oracle speedup. The 53 lifecycle-audit checks pass, including repeated samples,
unknown demand and overlapping reasons. This revision changes offline attribution
only; it does not change the scheduling policy or the frozen V21 sources.

Partition independent waits by their largest explicit planned_nodes observation,
keeping empty arrays separate from absent node evidence. The55 lifecycle-audit
checks pass. Keep v26_v21_burst_size_evidence_20261011.json with source SHA256.
Of186 JOINs,165 observe1–15 nodes and21 only empty plans; none observes16 nodes.
Of11927 tool waits,190 observe16 or more nodes, associating63.680GB residual native
FULL and only3 events with early FULL reuse. The4746 ancestry-rejected waits
associate298.122GB and no early reuse. Validate the queued complete-prefix fix
first. JOIN/tool16-node budgets apply per burst and permit later ACKed bursts;
the separate demand-handoff lifetime limit must not be described as a prediction
cap. Burst partitions do not overlap; rejection reasons still do. Neither a
full-sized burst nor associated residual demand proves a larger early transfer
would fit or improve performance.

The later partial V21 snapshot has 1.405/2.530 GB independent early-reused JOIN/tool
FULL; 99.764 GB demand-handoff reuse stays separate. Node-command ACK-to-first-
service P90 is 1.473/0.922 s, not the earlier 16 s, while maxima remain 24.730/12.354 s.
Known-boundary FULL lease expirations include 10 JOIN commands before completion,
one awaiting native ingress, and one tool command before completion. Diagnose
premature prediction separately from post-return admission; do not attribute every
long delay to lack of parent priority. There are 338 confirmed children and 175
parent services, 172 after linked device release. Validate useful pre-boundary
coverage, demand-restoration consumption and busy-window performance in the
same-version pair before marking the full performance goal achieved.

Include the isolated live-counter reduction in the next V22 registration.
Headroom and Host usage now validate current pool ownership and geometry and
sample live free lists without a device tensor-byte census. Full capacity census
retains its tensor inspection. Action node closure now uses the same validated
current pool geometry rather than an irrelevant whole-pool tensor census.
Keep live node lengths, full ancestry, session ownership, reference/lock and DMA
checks unchanged. Do not cache free lists across native allocation.

Keep v23_static_pool_counters_cpu_final_20261011.json. Alternating CPU measurements
use the native MHATokenToKVPool counter with ten FULL and thirty Mamba tensor
layers: mean headroom/Host usage cost changes from 101.5/100.8 to 6.50/5.58 microseconds,
with identical changing live counters. This is a counter-path improvement only,
not a restoration or GPU throughput result. PREPARE, sampling and terminal
fixtures preserve selections, publications and observations; retain their wall
outliers instead of treating noisy fixture means as an end-to-end gain.
The latest 296 observer/telemetry/runtime/policy checks pass; the earlier 267
terminal/handoff/physical checks also pass, with overlapping tests.

Include the final closure reduction and sparse control profiling in V22's
still-waiting registration, preserving the old registration and receipt.
Keep v24_checkpoint_observation_cpu_final_20261011.json: against f57527f,
100 alternating CPU samples with 50 calls per phase preserve changing live
counters and identical four-extent H2D and FULL-only PREPARE plans.
Mean closure/H2D/PREPARE planning costs change from150.07/223.84/193.93
to40.31/103.96/76.42 microseconds, with15750→0 tensor census calls.
Headroom and Host-usage differences are noise. This is not GPU throughput proof.

Sample control socket receive every64 attempts and JSON/Event decoding,
ACK encoding/send every64 received packets through the existing timing collector.
Preserve wire schema, event order, rejection and cached duplicate ACK bytes.
Publish control_timing_sample_every; do not extrapolate sampled totals into
full-run CPU time or JCT. Keep v24_control_channel_profile_cpu_20261011.json:
single-event862-byte CPU fixture packets add about4.03 microseconds on average;
32-event25746-byte packet differences are noise. The fixture excludes real
socket syscalls and GPU. The337 observer/physical/terminal/handoff/native-transfer
checks and132 channel/timing/runtime checks pass, with overlapping tests.

Preserve v21_control_profile_snapshot_20261011.json, extracted with a128KiB
tail budget. At7287 seconds the exclusive Python wall totals for control_drain,
reentry_inspection, opportunity_sampling, join_prepare and admission_plan are
129.19/106.15/102.44/84.37/65.85 seconds. These are not removable JCT or CUDA
idle. At this partial observation136 workflows completed,1 was incomplete and19
had no result. Preserve django-12125's length-truncated incomplete result;
the deadline was unexpired and no cancellation/guard intervention occurred.
Use the next frozen same-version pair to measure actual restoration consumption
and native-relative busy-window performance; the broad goal remains active.

Keep v21_control_profile_final_20261011.json with the128KiB source-read budget
and compact final-state hash. Completed V21 predictive has1117.376 seconds of
instrumented exclusive Python wall time. Control drain, opportunity sampling,
reentry inspection, JOIN PREPARE and semantic updates account for168.853,
124.516,107.695,101.487 and86.923 seconds respectively. Maintenance and several
light paths each run about2.518 million times; call counts alone do not prove
redundant restoration. These intervals are not CUDA idle or directly removable
JCT. The queued V22 reductions and sparse channel stages are absent from this
V21 profile. Validate actual pre-boundary FULL coverage and consumption together
with same-version native-relative busy-window throughput/JCT. Retain the older
profile snapshot as historical partial evidence.

Preserve v21_terminal_capacity_progress_summary_20261011.json with source audit
path and SHA256. Its 214 confirmed dynamic children release cumulative device
FULL57.445GB and Mamba32.388GB; internal summary invocations remain separate.
Of102 observed parent services,99 follow linked device release. Queue-to-first
observed completed batch P50/P90 is196.80/492.49ms,max18.906s. This does not prove
exclusive capacity reservation or a throughput win. Independent early-and-reused
FULL is749.179MB from56 JOINs and2.228GB from74 tool events;80.025GB reused demand
handoff FULL remains a separate category.

The600–2400s V21 versus prior V20 native diagnostic window has942.09/1059.67
decode token/s and72.84%/82.44% GPU utilization. It does not demonstrate progress
and is not V21's matched baseline. Keep v21_busy_window_vs_v20_native_diagnostic_
20261011.json, preserve the live V21 pair, then verify all corrections under the
next identical predictive-first/cold-native pair before making performance claims.

Disk cleanup removed165 pristine workspaces from two verified stopped
zero-result starts, retained51 dirty/unavailable workspaces and preserved logs
and manifests. No results or active paths were fabricated or removed. Net free
space increased33.840GB,leaving approximately89GiB. Keep the exact directory
receipt in stopped_pristine_workspace_cleanup_20261011.json.

Keep v21_terminal_capacity_live_20261011.json as a partial audit:126 real children
release cumulative26.165GB device FULL and8.821GB Mamba. Forty-four parent
requests have240.07/750.15ms P50/P90 queue-to-first-observed-completed-batch time,
with18.906s maximum. Early verified FULL is221.245MB from21 independent JOINs
and830.730MB from29 tool events;48.571GB demand-handoff reuse stays separate.
Cumulative release is neither simultaneous free capacity nor a throughput win.

One django-13023 parent waits17.73s after handoff selection. Both restored nodes
lose residency182/96ms after their ACKs and their allocations are not reused at
first service roughly17.5s later. Old records cannot identify why protection was
unavailable; validate the new reasons before expanding residency budgets. Preserve
the V21 same-version pair. Schedule deployment of the committed follow-up after
that pair completes, then validate the unchanged workload and performance goal.

## V21 Deployment and Completed V20 Evidence

The V20 pair, both summaries, HTML exports, completed-workspace cleanup and
final comparison are complete. Predictive collected 154 completed / 2 incomplete
in 10690.00 s; native collected 155 completed / 1 incomplete in 10003.74 s.
Completed throughput is 51.862 / 55.779 workflows/h, or -7.02% for predictive.
For 154 commonly completed tasks, mean JCT is 3098.94 / 3487.53 s, or -11.14%.
Different trajectories and native GC startup still affect the JCT comparison;
neither that mean nor a tool tail completes the active performance objective.

At 2026-10-11 01:21:58 CST the coordinator verified all previous outputs,
deployed 597688c and the patch below, and started fresh V21 predictive.
The deployed expected/actual engine tree is
`e6cc9d6322c0b9031243acd1228037003751cadd`. Preserve 156 tasks, 108+48
arrivals, running=48, Host 200 GB/80:20, HBM 0.9, 180 s tools, 500 ms lead
and the same predictor artifacts. Cold native follows on the same revision.
Develop subsequent changes in a separate worktree, leaving this pair frozen.

Opportunistic capacity reclaim now submits an unbacked live Mamba checkpoint
asynchronously instead of draining pending write ACKs. Keep native device read
locks until ACK; do not count pending Host state as freed capacity or demote
either pool in that call. A later restoration attempt may reclaim after native
ACK processing. Actual native allocation/eviction retains its synchronous
dependency. Merely removing the wait from unlocked write-back is unsafe.

74 scoped native CPU checks pass, including real hybrid allocators and native
write-ACK unlock processing with transport replaced by a fixture. This confirms
capacity/lock semantics, not GPU throughput or the cause of the 40.20 s busy
prefill gap. The deployed canonical patch has SHA256
`98fc5af9c40121d6f7ba1824f1fd5b586d0b141d5b12370a2d9243c1b1b5e8f1`;
reverse application and exact expected/actual tree verification pass. Deployment
after the completed V20 pair is done; measure V21 reuse and busy-window/paired
JCT results before claiming an improvement.

launch_semantic_h2d_after_pair.py performed the previous-process, output,
frozen-plan and source checks before fast-forwarding main and applying the exact
engine delta. Preserve its separate launch state and the V20 frozen plan.
V20 native is diagnostic context, not V21's matched baseline.

Terminal private-suffix retirement and parent restore/admission are implemented.
The frozen predictive arm contains 371 real spawn children; their cumulative
device releases are 119.545 GB FULL and 125.816 GB Mamba. Independent JOIN
requests have 143.10/499.16 ms P50/P90 queue-to-first-service time, with a
16.838 s maximum. Keep 1.507 GB early reused JOIN FULL separate from 76.993 GB
demand-handoff reuse and from cumulative allocator release.

The deployed follow-ups activate an existing final-child report notice on sibling
RETURN, reclaim settled unreferenced state under an actual Mamba shortfall,
and exclude the full target closure from reclamation. The completed V20 pair
preserved its frozen b019d24/c58cc891 comparison. Earlier sibling
release can support pre-RETURN prediction; last-child release can only support
post-RETURN demand restoration or subsequent parent growth.

Reuse rid and visible identity within each request's completed-batch callback.
Preserve full metadata checks for lease consumption and final-stage updates.
Relative to befd5e0, alternating CPU fixtures report 39.884→35.132 microseconds
for plain callbacks and 145.502→131.500 with semantic progress and 16 pending
parent handoffs. Queue order and callback runtime state match. Preserve both
v20_batch_visible_lookup_*_cpu_20261011.json reports; these are not GPU results.

The live comparator now supports repeated --busy-window START:END without
requiring a terminal summary. Keep decode token deltas separate from completed
request lengths and explicitly report the complete service horizon. Attribute
completed physical receipts and measured batch durations to submission windows;
partition H2D bytes into JOIN/tool, demand handoff and native sources, timing
each physical batch once. Report sampled layer waits without extrapolation.
Receipt source metadata remains authoritative; only missing legacy sources
require the opportunity-log fallback scan.

Preserve v20_busy_windows_transfers_live_20261011.json. In 600–2400 s,
native/predictive decode output is 1059.67/1036.79 token/s, despite nearly
identical 47.309/47.325 weighted decode batches and input demand. Prefill
worker wall intervals are 272.95/313.15 s. Total H2D is 2.337/2.328 TB and
transfer-stream time is 71.34/67.39 s; sampled layer-wait P95 is 0.174/0.145 ms.
These observations do not establish DMA as the cause of the extra 40.20 s
prefill wall time. Inspect CPU restoration, admission and worker intervals
next; avoid claiming a kernel-time breakdown from wall intervals.

The completed recheck is v20_completed_busy_windows_20261011.json. Predictive
decode output relative to native is -2.16%, +0.16%, +2.64% in 600–2400,
2400–4200 and 4200–6000 s. Later windows have different batches, input and
restoration demand. In 4200–6000 s uncached input is native 8.50 M versus
predictive 9.49 M and prefill worker wall time is 250.17 versus 383.92 s.
Do not call the entire difference control overhead. First verify the cost gap
under similar batch/input conditions, then use V21's matched native arm.

Keep v20_capacity_handoff_followup_audit_20261011.json. Of 171 independent
JOINs with an observed FULL plan, 114 ever encounter only a Mamba shortfall;
their per-event maximum FULL plans total 2.374 GB, not achieved reuse or
simultaneous capacity. Only one of 10159 tool-wait events has proven pre-boundary
FULL reuse. Verify deployed history-head routing, Mamba reclaim and sibling
notice activation on real V21 transfers. Keep submitted demand handoff separate.

The busy window follows the native GC correction, but full-run JCT still
carries that initialization asymmetry. Native django-11087 ends incomplete
with two root finish_reason=length responses and 34998 visible characters;
no deadline expiry, abort or guard completion occurs. The same task is
incomplete in predictive. Preserve this model truncation in the completed pair.
V21 starts predictive first and cold native second with GC initialization after
health readiness and before each workload. Predictive's
server/gc_freeze_initialization.txt confirms success. Early runtime evidence has
physical_disabled=false, healthy tool/semantic workers and actual
same_input_completed / completed_project_command conditional residual
forecasts. Inspect the future cold-native receipt separately. These startup
checks do not prove throughput or usable pre-boundary restoration.
The full performance goal remains active.

## V20 Parent Restoration and Control Cost

V19b terminal reclamation works, but Host-backed parents can wait for the
original residency/admission queue to age before handoff starts. In a diagnostic
snapshot,19 native FULL Host-hit parents all waited over1s, with11.52s
median queue wait. One waited19.18s before first observed service, but only
103.79ms from restoration selection to that service observation.

Revision1375285 considers one submitted JOIN-unlocked parent for the next
available restoration slot after four ordinary admissions. Keep a parent already
in that frontier; otherwise inspect at most eight linked parents and select only
a safe request checkpoint whose restoration extents fit the current free lists.
Unavailable slots, checkpoint or capacity preserve ordinary service. Native
page/state/DMA dependencies, bounded leases, normal admission quota and batch
filling remain authoritative. A full transfer ledger must not consume the
one-shot restore attempt.

Audit parent_capacity_handoff_restore_lookahead against the same workflow,
context,JOIN and request's first observed completed GPU batch. Report selection
to service and queue wait before selection, without treating selection as
submission/ACK or counting this post-RETURN demand work as early predictive H2D.
328 scoped runtime/restoration/capacity/admission/audit checks pass. The engine
patch and predictor artifacts are unchanged.

V19b stop requested19:07:25 CST,2026-10-10; old processes,GPU and port18454
were verified released at19:11:58. Preserve80 completed outcomes and76
interrupted tasks; all156 arrived. No native arm started, so no completed
performance pair exists. Final stopped evidence includes540 terminal contexts,
804 reclamation records including retries,190.834GB device FULL and97.614GB
device Mamba released.112 linked parent services have queue-wait P50/P90
191.50ms/13.990s. Independently early reused FULL is1.234GB JOIN plus
0.238GB tool;71.884GB demand-handoff FULL reuse remains separate.
329 PREPARE commands include133 later-restore associations, not forward proof.

Keep the4 early estimated-work triggers and their bias as failures. A constant
correction fitted on2 events leaves only1 of2 later events within500ms, so
do not change the runtime offset or frozen artifact from that sample.95 selected
observed-no-tool-EOS triggers have58.56ms median RETURN endpoint error and
97.89% within500ms; these are protocol-cue events, not overall semantic
forecast accuracy or a multi-second predictive window.

Retain the stop record, three stopped audits, traces and patches; clean the80
archived completed root workspaces, keeping interrupted workspaces. V20 runs on
frozen revision b019d24; the server was ready at19:26 CST,2026-10-10.
Predictive precedes cold native on that same revision. Keep156 tasks,108+48
at3600s,running48,Host200GB80:20,HBM0.9,180s tools and500ms lead.
Keep the deployed c58cc891 engine patch and predictors unchanged between arms.

Preserve prefetch_lifecycle_early_snapshot.json. It contains78 terminal contexts,
79 reclamation records including retries,14.797GB device FULL and8.306GB device
Mamba released.23 linked parents have queue-wait P50/P90 of198.53/441.96ms,
with16.923s maximum.7 restore lookaheads all link to observed service: selection
queue-wait P50/P90 is54.83/211.81ms, selection-to-batch is201.32/319.64ms.
This early linked subset is not all JOINs or a native-relative latency saving.
Independently early reused FULL is101.888MB JOIN plus3.154MB tool;20.198GB
reused demand-handoff FULL stays separate. The pytest-7571 parent with16.923s
queue wait has no restore selection and a no_restore_plan observation near the
completion boundary. That reason can also mean already resident; it does not
establish an absent checkpoint. Native submit telemetry reports8558 FULL-hit
tokens and8512 Mamba branching tokens. Continue inspecting native capacity,
admission and data dependencies; terminal reclaim alone does not explain this tail.

The isolated follow-up compares long, same-format int64 token segments directly
without copying slices. Hold the GIL during memcmp so Python buffers cannot resize.
Use it only for segments of at least2048 tokens; retain numeric comparison for
short segments, lists and different array formats. Preserve namespace,salt,
limit,offset,bigram and page-alignment rules.127 scoped native checks pass with
the isolated source and only the GPU test-launch helper replaced for CPU fixtures.
The existing whole-inspection CPU benchmark alternates variant order and verifies
identical observations.32K--98K,1/8-leaf matching fixtures improve median CPU cost
by19.5%--29.4%;4K is essentially unchanged. Preserve
experiments/reports/v20_followup_reentry_cpu_20261010.json; this is synthetic CPU
evidence, not physical restoration or end-to-end throughput evidence.
Only radix_cache.py and its prefix test differ from the deployed engine.
The new isolated canonical patch passes reverse-application checking and has SHA256
`67fcbe012a9d3adbd1b5d6cca1510f55ad36b844aae402d968518c3999547617`.
Commit this follow-up now and deploy after preserving V20's same-version pair
or a concrete stopped-run failure. Validate actual reuse and busy-window/paired
JCT performance before closing the full active goal.

The isolated follow-up now separates GPU source readiness from outbound backup
completion in inspect_beliefkv_reentry. Propagate pending_load independently
through each ancestor and the per-call shared-prefix cache. A fully resident
FULL chain and device Mamba checkpoint remain readable during D2H; inbound H2D
and unsettled Host-only data still require native completion. Keep all input,
session,reference,allocator and eviction semantics. Before this fix,3 of5
new cases reproduce the false negative and2 correctly reject unsettled Host
copies; after it,132 scoped native checks pass. This code-level defect is not
yet established as the cause of the pytest-7571 outlier.

The whole-inspection CPU benchmark including both queued changes reports
19.9%--27.0% lower matching cost for32K--98K and1/8 leaves, with4K
within about1.4%. Preserve v20_followup_reentry_d2h_ready_cpu_20261010.json;
this remains synthetic CPU evidence. The canonical isolated patch passes
reverse-application checking and has SHA256
`ec65735303567fa11d00eeb85b9cd0257965a414df55766eacebb7250fbf313e`.
Compared with deployed sources,only radix_cache.py,unified_radix_cache.py and
their two corresponding test files differ. Commit promptly, but preserve the
live V20 b019d24/c58cc891 pair before deploying these follow-ups.

Preserve the20:11 CST,2026-10-10 partial capacity snapshot as
prefetch_lifecycle_capacity_snapshot_20261010_2011.json.388 terminal
contexts and589 reclamation records including retries release124.303GB
device FULL and61.878GB device Mamba cumulatively, not peak free capacity.
91 linked parent services include90 with prior actual device release;
queue-to-completed-batch P50/P90 is214.84/485.33ms,max16.923s.
All36 restore lookaheads link to observed service, with selection-to-batch
P50/P90 of136.30/291.75ms,max446.55ms. The older114-row JOIN aggregate
and its12.16s P90 are invalid: grouping by context epoch repeats JOINs and
attributes later tool requests to them. See the corrected identity below.
Independently early reused FULL
is1.053GB JOIN plus3.154MB tool;69.057GB demand-handoff FULL reuse stays separate.

A subsequent outcome scan has60 completed,1 incomplete and no error.
django-11087 has two natural child RETURNs,a satisfied JOIN,and root's last
two model responses ending with finish_reason=length; the last has36832
characters of repeated reasoning. No workflow deadline expiry or guard
intervention occurred. Preserve this model-truncation outcome and continue
the stable pair; it is not evidence that terminal reclamation failed.

Keep one JOIN episode per workflow/context/JOIN ID across the parent's epoch
advance. Combine wait and post-resumption plans, then identify the parent's
first actual request after the complete JOIN regardless of the observation's
epoch. An unobserved complete JOIN leaves request/service attribution unknown.
Keep tool episodes at their own context epochs and different spawn JOIN IDs
separate.3 new cases reproduce old duplicate/wrong-next-request behavior;
39 lifecycle audit checks pass after the fix. Do not alter raw logs or claim
runtime acceleration from this measurement correction.

The later corrected v20_join_admission_diagnostics_20261010.json snapshot
has110 matched independent JOIN requests,all linked to capacity handoff.
Queue-to-actual-first-service P50/P90 is169.94/456.97ms,max16.838s;
26 JOINs have observations spanning multiple epochs. django-13023's actual
post-JOIN request waits221.21ms; the old extra group incorrectly uses the
subsequent read_file request's19.968s wait. The pytest-7571 tail remains
real and unresolved. Independently early reused JOIN FULL is1.173GB.
This snapshot is later than20:11; changes in sample counts are not all
deduplication. Apply this same corrected offline auditor to both final arms
after preserving their frozen runtime versions.

The subsequent prefetch_lifecycle_capacity_snapshot_20261010_2040.json
contains568 terminal contexts and837 reclamation records including retries.
Actual cumulative device release is202.940GB FULL and120.987GB Mamba,
not peak capacity or a parent reservation.118 parents have completed-batch
service observations;117 link to prior actual device release. All39 restore
lookaheads link to subsequent service,selection-to-completed-batch P50/P90
136.97/286.34ms. Corrected independent JOIN queue-to-actual-service P50/P90
is169.11/457.87ms,max16.838s. Early started and next-request-reused FULL
is1.210GB JOIN plus3.154MB tool;71.236GB reused demand-handoff FULL remains
separate. Preserve low early-tool coverage,the real JOIN tail and six FULL
leases expired before completion as unresolved risks; these observations
do not establish native-relative throughput improvement. Native telemetry
failed/dropped records remain zero.

Profiling the156-request CPU fixture finds repeated identity parsing at plan
compilation accounts for about23% of cumulative plan cost. Reuse immutable
keys already bound within that plan call. Formal native selection must still
recheck live request,session,generation,epoch and attempt; retain revisions,
duplicate-ID and candidate-bound checks.196 scoped admission/runtime/capacity/
audit checks pass. The whole-planning benchmark loads each variant's exact
planner and compiler source and alternates their order; paired plans,cumulative
counts,native inspection counts and priority metadata remain identical.
156-request mean CPU wall intervals decrease17.1%--27.6%;8-request cases
decrease2.5%--8.8%. Preserve v20_prefill_identity_reuse_cpu_20261010.json;
it does not measure physical DMA,GPU throughput or paired JCT.
Commit this runtime follow-up in isolation and deploy between frozen runs.
The second48-task wave has arrived; at2026-10-10 20:58 CST the outcome scan
is105 completed,1 incomplete,no error. The native arm has not started.
Continue the live predictive then cold-native pair.

Live profiling records130962 batch_completed calls with47.14s exclusive
Python wall time. When any parent handoff is active,the old callback parses
every batch request's full identity; restore leases similarly inspect unrelated
contexts. Filter parent candidates using their already-bound visible context
and lease candidates using current lease contexts before full live identity
checks. Preserve session/generation/epoch validation,exact native lock
receipts,terminal handling,semantic token progress and context accounting.
242 scoped restoration,terminal,lease,runtime and admission checks pass.

The whole-callback benchmark compares22e3710 and the isolated revision,
rotating order and checking queue order,context accounting,semantic keys,
token histories,scan positions,tool markers,handoff sets and cumulative
counters.48 requests with16 waiting parents and300 iterations average
0.247→0.042ms (83.0% reduction); with live semantic-token progress this is
0.355→0.134ms (62.1%). With no tracked parent/lease/semantic progress,
0.0352→0.0356ms adds about0.4us; do not claim every path gets faster.
Preserve v20_batch_context_filter_cpu_20261010.json,
v20_batch_context_filter_semantic_cpu_20261010.json and
v20_batch_context_filter_empty_cpu_20261010.json. These contain no DMA,
GPU throughput or JCT evidence. Commit and deploy between frozen pairs.

Complete the sibling-RETURN/earlier-notice linkage in the isolated follow-up.
When a child announced before other siblings finished,its notice and following
request were retained but the last-child restoration stage previously required
a new semantic reply or EOS. After a real RETURN,inspect only its associated
JOINs. If one child remains,reuse that child's current same-JOIN/context-epoch
notice and observed decode tokens/rate without renewing the original expiry.
Do not revive notices invalidated by tools,new requests,another JOIN or expiry;
retain a live existing stage. A report not yet submitted uses the existing
request binding path. Native private-suffix reclamation and the existing FULL/
required-Mamba,capacity,reserve,admission and latest-start rules still control
physical transfer; running child pages and unsubmitted parent capacity are
not claimed by this stage observation.

Record final_stage_resumed_after_sibling_return and the corresponding stage
field.242 scoped terminal/report/batch-transfer/handoff/admission/audit checks
pass,including one-batch notice/submit/RETURN ordering and a mocked early
parent submission after sibling release while the last child is still live.
The mocked transfer proves dispatch ordering,not physical DMA or performance.
Commit promptly in isolation; deploy after the frozen V20 predictive/native pair.

Preserve experiments/reports/v20_terminal_capacity_snapshot_20261010_2115.json.
This later partial audit has869 terminal contexts,1241 reclaim records including
retries,and338.281GB FULL/200.830GB Mamba actual cumulative device release.
169 of170 parent completed-batch observations link to earlier device release;
all54 lookaheads link to service,with152.75/321.15ms selection-to-completed-batch
P50/P90.168 independent JOIN requests have163.05/505.54ms queue-to-actual-service
P50/P90 and16.838s maximum.88 JOINs have early-started,next-request-reused FULL
totaling1.438GB;tool early FULL remains3.154MB. Keep76.934GB reused demand-handoff
FULL separate. These are frozen-code observations,not effects of the undeployed
notice follow-up or native-relative throughput/JCT gains.

Current tool counters include20 window entries and18 cold-capacity exits.
4306 demand-handoff requests close for no_cold_capacity; only4 link to a
parent service,and those complete a first batch about100--125ms later.
No linked child release was observed between these closes and services,
so do not treat permanent retry suppression as the demonstrated parent-tail
cause or add periodic retries on that assumption. Early tool coverage and
the actual long JOIN wait remain unresolved.

Preserve the completed-command history residual head through the worker/runtime
boundary. The former hint preferred an operational CDF that lacked the history
inputs, replacing same-input, failed-repeat and project-command predictions.
At9750ms elapsed,10000/15000ms prior durations should produce250/5250ms
residual P50; the former route produced about41560ms for both. Carry the
specific residual probability mass and support detail in the hint; condition on
survival since the original observation, including IPC latency. Exhausted
support becomes unknown rather than a zero-time completion. Generic history-free
curves and legacy reply formats remain supported. Record the selected timing
policy and mass without changing the frozen predictor or physical authority.

Preserve experiments/reports/v20_tool_history_routing_20261010_v2.json:
11635 independent matched tools and33053 observations. Equal-weight per-tool
last-second median absolute error is313.43→24.68ms; for actual durations at
least1000ms it is296.95→127.94ms,with1538 comparable tools of1574 matched.
First predicted500ms-window entries with actual0--500ms lead increase7→838
in this long-tool subset. Use online-recorded history and frozen error margins,
with future TOOL_END only an evaluation label; exclude ambiguous parallel tools
including unclosed intervals. This fixed-trace routing audit is not heldout
calibration,physical Host-only opportunity coverage or GPU speedup. Retain
support-specific groups and do not count short-command forecasts as transfers.
432 scoped worker/tool/physical/terminal/batch/runtime/lifecycle checks pass.
Commit this follow-up promptly in isolation. Preserve both V20 arms,then deploy
the queued changes between pairs and validate predictive-first/native on the
same version. Continue checking actual capacity,early FULL reuse,busy-window
throughput,paired JCT and costs to other requests.

Stop read-only terminal diagnostics when their exact anchors are known gone.
Python node_by_id reads _node_arena and raises KeyError for a deleted node;
the old observation route treated this as temporary unavailability and polled
until its60s expiry. Record direct lookup KeyError,None or changed creation time
as gone/replaced,retain the last evidence record,and prune only those anchors.
Close the watch when none remain. Ancestry observer KeyError,bounded-depth
failures and other unknowns retain observation; an absent anchor is not proof
of allocator capacity release. This change issues no D2H or DROP. Five focused
regressions fail on the former behavior;437 scoped runtime/physical/prediction/
terminal/audit checks pass after the fix. Commit and deploy between frozen pairs.

At21:47 CST on2026-10-10,the collecting arm has147 completed,2 incomplete,
zero errors. The additional incomplete is django-13112:both children return
naturally,JOIN is satisfied,and the root ends with finish_reason=length and
21676 output characters while repeating its report. Deadline expiry and guard
interventions are zero. Preserve the truncated terminal and exclude it from
normal completion throughput/complete paired JCT.

The real pytest-7571 parent tail spans446 observed GPU service intervals
between native registration and first service:339 decode intervals total
13.264s with45--48 requests,and107 prefill intervals total3.574s with1--3.
These are scheduler/worker intervals,not CUDA kernel times. Other requests
are served throughout; do not label all16.838s as copying or GPU idleness.
Its first admission-candidate physical inspection is about13s after registration,
with440 Host-backed missing FULL tokens and1356 device-free tokens. Native
submit observes8558 FULL/8512 Mamba branching hit tokens. Per-request rejection
reasons are absent,so slot,capacity and dependency causes are not uniquely
identified. Retain this gap without enlarging budgets or preempting hot requests
on speculative attribution.

Use live native pool indexes to reduce sparse pressure-candidate publication.
The old publisher walks all registered ancestors when one pool cannot supply
its first eight candidates. Retain the initial sixteen-entry scan and original
order; only then intersect registered candidates with native evictable FULL
leaves and the resident Mamba LRU. Skip irrelevant node lookups once fallback
is full, stopping after possible remaining pool candidates are exhausted.
Keep each pool's consumed prefix, fallback, creation time, locks, references,
Host-copy validity and pending-transfer checks. Missing indexes retain the
original scan. Rebuild from live indexes each publication; do not cache physical
authority across calls or change eviction/restoration/admission policy.

443 scoped runtime/transfer/terminal/handoff/tool/physical checks pass.
Preserve v20_pressure_publication_native_index_cpu_20261010_v3.json and
v20_pressure_publication_native_index_flat_cpu_20261010_v3.json. Against207643f,
156 workflows with24 ancestors and150 alternating iterations show mean
publication cost0.16851→0.10122ms with both pools available,3.19760→0.55181ms
with sparse FULL,and2.95728→0.57657ms with sparse Mamba. Native-consumed
prefixes match every iteration; node reads fall192→24 or3744→25. The shallow
dense case P50 increases0.023336→0.023963ms; its mean includes one0.282676ms
outlier. Do not claim every path improves. The eager-index preliminary version
is superseded by the lazy-index v3. These fixtures exclude DMA,GPU throughput
and JCT. Commit promptly in isolation and deploy only between frozen pairs.

Preserve v20_shared_path_cpu_snapshot_20261010_2209.json. At9720.138s,
instrumented exclusive Python wall totals are control drain139.501s,
opportunity sampling115.718s,reentry inspection100.152s,batch callback91.747s,
JOIN PREPARE87.136s,admission planning76.811s,semantic updates58.985s and
maintenance54.643s. These are not GPU idle or directly removable JCT.
closure_unobservable action samples lack detailed capture failure reasons;
terminal diagnostic depth failures do not prove lost actionable opportunities.
Propagate cheap failure detail before changing observation depth bounds.

At22:16 CST on2026-10-10,the collecting arm has153 completed,2 incomplete,
zero errors,with pydata__xarray-6992 unfinished. Native has not started.
Keep V20 atb019d24/c58cc891 for both arms; all latest isolated changes remain
queued for the next predictive-first same-version native pair.

Preserve existing capture failure reasons in closure_unobservable.blocked_detail
and wait_prefetch_plan. Distinguish ancestry bounds,replaced anchors,conflicting
closures and pending D2H/H2D without adding rejection conditions or enlarging
the capture bound. At one scheduler safe point,a failed H2D closure is also
the PREPARE closure failure; reuse it instead of rereading the same session and
ancestry. Read afresh on later samples. Include changed failure detail in the
500ms log deduplication fingerprint,without changing transfer authority.

489 scoped physical/runtime/terminal/handoff/batch/tool/lifecycle checks pass.
Preserve v20_failed_closure_sampling_cpu_20261010.json,baseline50aef3d,
156 waiting parents,150 alternating iterations,16 observed parents per call.
Ancestry-bound sampling mean13.778→7.102ms,pending D2H6.248→3.356ms,
pending H2D6.239→3.349ms; session/node lookups halve. Normal24-layer
sampling4.949→4.899ms retains the same reads; do not claim its roughly1%
difference as a stable gain. All physical opportunity,PREPARE and identity
fields apart from newly added failure detail match every iteration; no native
transfer is issued. Reproduce with --failed-sampling-only. Commit in isolation,
retain frozen V20,then validate GPU performance after both arms finish.

At22:28 CST on2026-10-10,predictive has a final summary with154 completed,
2 incomplete and zero errors; old client and scheduler have exited. Driver
audits/export are running before the frozen native cold start. The corrected
v20_terminal_capacity_snapshot_20261010_2226.json has1108 terminal contexts,
1534 reclaim records including retries,and cumulative actual device releases
456.008GB FULL/343.645GB Mamba.192 independent JOIN requests have native
queue→actual first-service P50/P90143.10/499.16ms,max16.838s. All54 restore
lookaheads link to service,selection→completed-batch P50/P90152.75/321.15ms.
93 JOINs have H2D submitted before completion and next-request FULL reuse,total1.507GB;
tool early reuse is3.154MB,while76.993GB demand handoff stays separate.
These frozen-code results neither measure queued follow-ups nor establish
native-relative throughput/JCT improvement. Keep the performance goal active.

Native is healthy and serving from2026-10-10 22:31:29 CST. Predictive HTML
was exported;154 completed workspaces were removed,2 incomplete workspaces
and all traces/results retained. Free disk briefly rose74→106GiB before
native workspace creation. Main code,engine and model artifacts remain frozen.

Retain the native startup GC asymmetry. With skipped warmup,the engine's
post-warmup /freeze_gc request can precede HTTP listening; it failed on native
but succeeded on predictive. The same standard POST was accepted at22:37:00
CST,331s after native readiness and after workflows began. Preserve
native/gc_freeze_correction.json and the original failure. HTTP success confirms
tokenizer acceptance/dispatch,not an independently observed scheduler or
detokenizer GC acknowledgement. Report post-correction busy windows separately;
full-run paired JCT retains this initialization difference and is not a fully
matched cold-start result. The isolated launcher now explicitly POSTs /freeze_gc
after health succeeds and before launching either workload client,saving
gc_freeze_initialization.txt. Actual endpoint200 and bash syntax validate this
setup change; do not change the active pair's code. Validate the next pair with
both arms initialized before workload submission.

Correct terminal-reclamation scope without changing valid reclamation behavior.
Earlier counts include internal context-summarization calls. Retain original
reports and add v20_terminal_capacity_scope_20261010_v2.json. Explicit client
invocation_create metadata confirms371 spawn subagents and737 runtime_internal
calls among1108 terminal contexts,with no unknown identities. Actual cumulative
subagent release is119.545GB device FULL/125.816GB device Mamba and48.573GB
Host FULL/50.739GB Host Mamba. Internal calls account for336.464GB device
FULL/217.828GB device Mamba; aggregate456.008GB/343.645GB must not be
called child-to-parent handoff or peak free capacity.

Publish invocation_scope in reclamation records. Audit terminal_contexts,
confirmed terminal_children and per-scope release totals separately. Link only
confirmed dynamic subagents to JOIN parent service; preserve unknown scope when
creation metadata is absent and never infer scope from ID spelling. Legitimate
internal retirement still releases allocator capacity and does not create a
JOIN handoff. Eighty focused capacity/lifecycle checks pass. Commit in isolation
while frozen V20 native continues; deploy between complete paired runs.

Intersect completed-history tool forecasts with actual observed FULL plans.
Preserve v20_tool_history_physical_opportunities_20261010_v3.json. Full-trace
routing matches11944 independent tools/34075 forecasts. For tools lasting at
least1s,the tool-weighted last-second median absolute error is295.675ms generic
versus125.244ms history; this is a fixed-trace routing diagnostic,not holdout
calibration. Match only an already accepted,unexpired forecast for the same
workflow/invocation/context/epoch and exact active tool at the physical snapshot.
Later TOOL_END evaluates lead without selecting the timing decision.

582 tools have matched positive FULL plans. History enters the recorded
whole-batch start window for154 tools;13 fit observed free lists directly,
11 also have actual0--500ms lead,total377.917MB of per-event maximum FULL
plans. Other records may require existing cold-page reclamation rather than
being impossible. Report the separate permissive500ms diagnostic; missing
latest-start horizons stay unknown. Free-list fit is not future admission or
capacity reservation,and maxima can share pages across events. Five audit
checks pass. Keep these observed opportunities separate from actual migration,
reuse or speedup; validate queued history routing with sibling release and
parent restoration in the next GPU pair,without changing the live native arm.

Fix the isolated capacity-handoff omission under a real Mamba-only shortfall.
The previous path requires one waiting-agent session reference and scans FULL
leaves, missing unreferenced backed Mamba on leaves and interior nodes. Inspect
at most eight unlocked native Mamba LRU entries only when its free list is
insufficient. Demote settled, Host-backed, zero-reference Mamba independently;
preserve FULL values and references, Host state and native load dependencies.
Keep existing waiting-agent causal validation and direct no-D2H terminal-child
discard. Exclude every node in the upcoming restoration closure, including
resident ancestors, from demand-handoff reclamation just as JOIN/tool already do.

Two added shortage cases fail against the old path.184 scoped runtime/terminal/
batch/tool/audit checks and72 native CPU checks pass after correction. Real hybrid
allocator cases cover both leaf and interior Mamba: one slot actually returns,
FULL capacity and values remain unchanged, Host state remains and no D2H runs.
Only the GPU test-launch helper is substituted with unittest.TestCase for CPU
fixtures; the native tree, references, allocator and reclamation methods run.
The canonical isolated patch passes reverse application and forward checking
against an upstream-HEAD temporary index; SHA256:
`93d2b9e31c23ec466c48aaab6cf1b8129e259a96fc484b59dc3a894b87375480`.

Preserve v20_restore_pool_shortfalls_20261010.json. Of192 independent JOINs,
171 have exact observable free-list snapshots for nonempty restore plans.
114 ever show Mamba-only shortage, with2.374GB summed per-event maximum
planned FULL;167 ever show FULL-only,125 both,and166 neither. Conditions
overlap across time and are not exclusive event counts. Only194 of10159 tool
episodes have complete capacity observations; zero observed Mamba shortage
does not establish its absence. Snapshots do not prove actual rejection causes,
available cold victims or achievable extra transfers/reuse. Keep V20 native
frozen atb019d24/c58cc891; deploy after the pair ends, then verify early sibling-
enabled FULL reuse, post-final-child demand restoration and busy-window/paired
JCT performance. The full goal remains active.

## V19b Harness Context Retirement Integration

The V19 diagnostic snapshot contained authoritative child RETURNs but zero
terminal-reclaim/parent-handoff records. Its stopped audit ultimately contains
one reclamation and zero parent services; distinguish this total from the live
diagnostic snapshot. Deep Agents task children are marked persistent=True for
multi-round execution, while their existing RETURN path already retires the native
session. Preserve this running-context feature and automatically emit
context_retired=True from the task completion callback. Accept that authoritative
lifetime signal for terminal reclamation; retain a context shared by another live
invocation. No model-side tool, report-format requirement or forecast retraining
is added.

Preserve the parent checkpoint/capacity link when child RETURN and the parent's
next LLM_SUBMIT share an event batch and advance the same session by one epoch.
Keep native generation/residency/admission checks. Use scheduler wall time for
RETURN-delivery-to-first-observed-completed-batch intervals; retain the original
client event timestamp separately rather than subtracting incompatible clocks.

498 harness/runtime/physical/audit checks pass. The116 prior native allocator/
reference/transfer checks cover the unchanged canonical engine patch. V19 stopped
at17:47 CST,2026-10-10 with0 root outcomes and156 interrupted/not-yet-arrived
planned tasks. All old processes/GPU exited; preserve the integration-failure
traces and stopped audit. Cold-start V19b predictive first with unchanged settings,
verify actual terminal releases and parent consumption, then complete a stable
same-version native comparison before closing the active throughput/JCT goal.

V19b cold-started at17:54 CST,2026-10-10 on frozen revision220896e.
Its early snapshot confirms27 actual child RETURNs and27 terminal reclamations:
166016 FULL tokens(3.400GB) and71 Mamba slots(4.572GB) returned to the
native allocators without additional D2H. Reclamation wall time is1.51ms
mean,2.80ms maximum across those27 calls. The first JOIN-unlocked parent
has a completed GPU-batch observation105.96ms after scheduler RETURN
delivery and86.01ms after native queue registration. Its two linked child
releases total3873 FULL tokens and6 Mamba slots, without exclusive-page
ownership or a native-relative performance claim.

The live workload has begun Host/GPU transfers; telemetry has zero failed or
dropped records and the semantic worker has no error. Keep collecting on the
unchanged deployed revision, then cold-start native automatically. Update
prefetch_lifecycle_live.json as partial evidence; preserve the distinction
between sibling-enabled pre-boundary H2D and last-child post-RETURN demand
handoff. Documentation observations are committed only in the isolated worktree
until both frozen arms finish. Busy-window throughput and paired JCT remain
unvalidated; the goal remains active.

## V19 Terminal Child Capacity Handoff

Implement authoritative RETURN-based retirement of ended child contexts.
Reuse existing final-stage parent checkpoint/extent planning; a completion notice
or EOS alone cannot release running child data. Release session references in the
native scheduler and discard only proven child-path leaves without session/device/
Host references or pending writes/loads. Walk proven ancestors only after their
last private branch is removed; preserve shared prefixes. No extra D2H is needed
for dead suffixes. Account actual FULL tokens and Mamba slots independently.

Retain bounded closed-generation leaf provenance for close-before-RETURN, and
invalidate it when a session reopens. Handle TOOL_END and RETURN in one event
batch without losing the terminal session identity. Retry unsettled native
dependencies with bounded maintenance work; unsupported tree cores retain KV.
Parent bookkeeping survives its next-request epoch transition, then clears at
first observed service, context compaction, terminal state or workflow end.

Use actual freed capacity via the native allocator. Retry demand restoration
immediately after device capacity release. Within the imminent native admission
frontier, prefer a JOIN-unlocked parent after the shared ordinary-admission quota;
once its submitted request's required checkpoint is resident, apply bounded
ready-parent priority. Preserve aging, ordinary admissions, restoration leases,
new-input/decode/state reserves and native running-slot decisions. Do not reserve
physical pages exclusively for a parent or manufacture a GPU slot.

Keep early-sibling-enabled anticipatory restoration separate from last-child
post-RETURN demand handoff. Audit actual per-pool terminal releases and parent
JOIN/service linkage using terminal_child_cache_reclaimed and
parent_capacity_handoff_first_service. The service record is the first observed
completed GPU batch, not exact kernel start or proof of exclusive capacity reuse.
Unknown byte sizes and reversed/missing clock boundaries remain unknown.

440 scoped runtime/physical/lifecycle checks and116 native session/tree/allocator/
restoration checks pass, including real CPU FULL+Mamba pools, shared references,
close/RETURN races, pending DMA, reopening, imminent-frontier bounds, aged quotas
and one-shot parent service linkage. Canonical engine patch SHA256:
`c58cc89194a66fe74d2a0e842db4972e5d1d059a2f4870f109f36f223c8ee4ae`.

V18 stopped at17:25 CST on2026-10-10; all old client/server/scheduler processes
exited and GPU/port18454 release was verified. Preserve69 completed results and87
interrupted/not-yet-arrived tasks, the plan, traces, receipts and three stopped
audits. Independently early consumed FULL is687.86MB JOIN+52.24MB tool;
52.138GB verified demand handoff is separate. JOIN last-ACK-to-service P50/P90
is327.78/940.52ms, but max18.96s and8 pre-completion FULL expirations remain.
These are censored old-version observations, not validation of the new handoff.
Clean69 archived completed root workspaces; retain evidence and interrupted
workspaces. Deployment engine matches all35 tested patched files exactly.

Cold-start V19
predictive first. Keep156 tasks,108+48 at3600s,running48,Host200GB80:20,
HBM0.9,hard180s tools,lead500ms and unchanged semantic artifact. Verify real
terminal releases and parent consumption before a stable same-version native
comparison. Do not mark the active throughput/JCT goal achieved from these tests
or from CPU, ACK, transfer-byte or tool-tail evidence.

## V18 Text Ingress Revision

Stop V17 for the authorized development iteration at16:21:53 CST,2026-10-10.
Verify all old client/server/scheduler processes exit and the GPU is released.
Preserve38 completed terminal results and118 interrupted/not-yet-arrived planned
tasks as censored evidence; native never started. No completed pair exists.
The stopped audit verifies419.66MB JOIN and62.38MB tool independently early
and consumed FULL;47.095GB demand handoff is separate. JOIN last-ACK-to-service
P90 is16.73s, with18 FULL locks expiring before completion.

Qwen3.5 text-only Jinja inputs reuse encoded prompt IDs, retaining template
caching and avoiding decode/re-encode. Media inputs and other VLMs retain their
existing routing. Real-tokenizer/native-method validation covers33 representative
trajectory histories/special cases,17--71199 tokens, with exact token equality.
Cold thread CPU mean33.136->16.357ms; warm15.256->0.951ms. This is a CPU
conversion benchmark, not exact HTTP replay or an end-to-end performance result.
Retain the source fingerprints and complete rows in
`experiments/reports/v18_chat_prompt_encoding_cpu_20261010.json`.

Propagate opt-in request ingress timestamps through native pickle/request stats.
Audit body-sent-to-API, conversion, tokenizer completion, dispatch-to-scheduler
handler and handler-to-native-registration intervals per independent JOIN/tool
event. Dispatch is stamped before serialization/send; do not claim pure IPC or
isolated tokenizer time from these broader intervals. No per-decode work is added.
89 native checks and79 telemetry/lifecycle checks pass; preserve missing/reversed
boundaries as unknown. Canonical patch SHA256:
`f41f2d118b549a5d7b74fe77b154691f6409cb9804493605a17e8a44ceb31468`.

Cold-start V18 predictive first with the established156-task108+48/3600s plan,
running48, Host200GB80:20, HBM0.9, hard180s tools, lead500ms and unchanged
semantic artifact. Check ingress waits, lock expiry, native residual recovery,
first-service reuse and busy-window service. Continue rapid development only
for actionable improvements; finish a stable same-version native comparison
before marking the active goal achieved.

## V17 Rapid Development Iteration

The user now authorizes stopping a development collection after actionable
issues have been collected, committing/deploying fixes and promptly cold-starting
the next predictive revision. A confirmed failure is no longer the only reason
for a development restart. Preserve the stopped plan, traces, receipts and
censor reason. Never mutate a running arm. Once a candidate is stable, complete
a same-version native comparison before claiming busy-window or paired-JCT gains.
This current cadence supersedes the historical frozen-pair deployment notes below.

V16b stopped at15:28 CST on2026-10-10 for iteration; old client/server/scheduler
and GPU release were verified at15:31. Its80 terminal results are all completed;
76 planned tasks have no terminal result and are interrupted/censored, not model
incomplete. Native did not start. Deploy ccaccd2,447f294,74efbcf and a423989 in
V17 with the unchanged canonical engine patch and established156-task108+48/
3600s, running48, Host200GB80:20, HBM0.9 and hard180s-tool configuration.

The stopped lifecycle audit verifies908.96MB independently early and consumed
JOIN/tool FULL, with47.947GB demand handoff reuse kept separate. JOIN last-ACK
to first-service P90 remains15.95s. Inspect the new HTTP intervals and native
admission, restore residency, repeated loads and actual early FULL consumption.

Audit signed endpoint error, predicted minus actual, separately against native
EOS and child RETURN. The11 matched work triggers have RETURN error P50
-4619.51ms versus-322.83ms for89 EOS triggers. Fit an additive bias only on the
earlier independent events and inspect later events: work correction+2557.72ms
reduces heldout absolute P50 from4788.18 to2230.46ms, but only1/6 events is
within500ms. EOS correction+367.72ms improves500ms coverage68.9%->82.2%
while worsening absolute P50. This is selected-trigger development evidence,
not project-isolated calibration; do not deploy one several-second offset for
both signals. Retain full stopped candidate-window rows and keep the goal active.

### Live Completion Windows And Request Ingress

The V16b CPU experiment uses6949 causal live forecasts from85 requests and83
workflows. Hold out17 pydata workflows for new work labels, with47 training and
19 selector workflows. The existing phase/encoder already saw that project,
so this is not a completely project-unseen pipeline. No4/8/16/32-token window
obtains a selector first-trigger precision of0.8 with at least3 triggers.
Keep all boosters/report for development evidence, but do not deploy them.
On the17 held-out natural requests, the existing32-token countdown triggers15,
only1 within the actual token horizon; all-trigger RETURN lead P50 is5975ms.
The747ms median conditioned on correct triggers conceals early false triggers.

V17's early snapshot puts tool body-sent-to-native-queue-registration P50/P90
at684/4702ms and registration-to-worker-service at4398/19400ms. This is not an
isolated tokenizer measurement. Ten locked JOIN FULL receipts expire before
completion. Prioritize exact token-equality/CPU validation of Qwen3.5 text-only
redundant encoding and additional ingress boundaries in an isolated engine;
then cold-start committed changes. Preserve V17's frozen running files.

## V16 Execution And Acceptance

The existing active goal remains open: improve native-relative busy-window
service and JCT with useful early FULL restoration and lower control cost.
Native's repeated-tool tail, total ACKs, and transferred bytes do not establish
completion. Preserve independent workflows and trajectory limits in reporting.

Implemented JOIN/tool restoration now plans the missing safe input prefix,
ancestor first, up to 16 extents per burst. Truncate by actual pool capacity,
next-prefill reserve, residency bytes, and whole-burst H2D start timing.
Retain separate node reservations/ACKs and partial-success submission. Restore
only the required checkpoint Mamba state; speculative PREPARE remains FULL-only.
Reclaim only the existing bounded cold backups, excluding the entire target
closure. Revalidate causal identity, the physical plan, and live child service
progress immediately before enqueue.

Acquire a deeper prefix lock before relinquishing ancestor locks; keep all
node-use receipts. Count residency slots and restore promotion by target,
not by extent. Allocation pressure releases the whole protection group.
Deploy with the already committed shared-path rank reuse, per-context restore
lookup, duplicate scan removal, scalar terminal serialization, and FULL
PREPARE batching.

Audit independent JOIN/tool wait episodes, not commands: maximum observed FULL
plan, actual pre-boundary submit with verified next-request FULL reuse,
pre-boundary ACK with reuse, residual native Host-hit, separately reported demand
handoff, client submission delay, and request/ACK-to-first-service waits.
Bounded snapshots are not an oracle denominator. Keep missing evidence unknown;
do not add Host-hit and handoff bytes without disjoint-allocation proof or sum
one sampled batch dependency wait across its requests.

V16 ran from frozen commit `1c0a3ea` and was stopped at 13:55 CST on
2026-10-10 for a confirmed lease fault. Two sequential grep calls from the
same child request changed invocation revision but not epoch/checkpoint.
Protection was incorrectly released about14ms after ACK, before the next
request consumed it. The fix retains protection across same-request tool
progress; session/generation, epoch, terminal, native residency, expiry and
capacity still determine validity. The stopped arm is diagnostic evidence,
not a valid completed performance comparison.

Tool event attribution now requires the original same-epoch request, the
following request, and all intervening tool START/END records, including
sequential calls absent from sampled active sets. Missing evidence stays
unknown. Separate client-submit-to-native-arrival from native-arrival-to-worker
service; the former includes preparation, transport and serving frontend work.
In the fault, tool-completion-to-client-submit was60.97ms, client-to-native
arrival6210.00ms, and arrival-to-worker-service10244.46ms. Do not attribute all
of this to transfer or hide it by simply extending leases.

Validation: 249 related checks pass;19 audit checks pass after the new wait
decomposition. The previous revision passed404 runtime/physical/policy/audit
checks and96 native CPU checks. Archived tracked changes and all untracked
files, including ignored files, before removing108 stopped workspaces and107
matching containers; retain V16 traces and physical evidence. Interruptions
without terminal results are not completed or model-generated incomplete
outcomes. Restart under a distinct V16b root and freeze the new commit with
the unchanged canonical engine patch for predictive_h2d -> cold native:
156 tasks,
108+48 arrivals at 3600 seconds, running48, Host200GB80:20, HBM ratio0.9,
hard180s tools, and the established model artifacts/remaining parameters.
Inspect busy-window GPU/output rates, paired JCT, control exclusive wall cost,
useful early FULL coverage, residual restoration and recomputation. Stop only
for an actual implementation failure, retaining its evidence before a fix and
cold restart. The stopped arm verified127.96MB early JOIN/tool FULL reuse;
15.403GB verified handoff FULL remains demand restoration. No completed V16
native-relative GPU improvement has been established. Diagnostic:
`experiments/reports/v16_stopped_diagnostic_20261010.md`. Historical
freeze/deployment notes below retain their original observation-time meaning.

### Isolated Follow-up During V16b

V16b freezes main at `1a09ada` with canonical engine patch SHA256
`68109d51ceb77f92fcbc2d17b52cfe76717b4ad26c47fd3642923b43ed0ca615`.
Develop and commit follow-ups only on `perf/v16-control-wait-20261010`;
deploy after the frozen pair, unless a confirmed fault requires a cold restart.
The14:33 CST snapshot is healthy; no completed native-relative result exists.

Reuse a persistent result-pipe poller for in-flight native predictor inference.
Keep failure, timeout, pending coalescing and result-identity semantics. A real
spawn-process/bounded-queue benchmark checks admission/tool/JOIN replies and
lifecycle equality. Empty in-flight polling mean thread CPU falls9.11->3.25us
(64.3%), wall9.80->3.92us. Ready-result CPU91.53->92.48us shows no improvement;
idle-without-task is effectively unchanged. These are microbenchmark results,
not measured busy-window GPU or end-to-end savings.

Record successful HTTP request boundaries through existing stream diagnostics:
hook start, body-send start/complete and response headers. Audit independent
events as client-submit->hook, hook->body-sent and body-sent->native-arrival.
The last interval still includes ingress, validation, template/tokenization
and IPC. Do not fabricate missing transport receipts or combine ambiguous
attempts. Keep request/SSE content unchanged;56 related checks pass. Report:
`experiments/reports/v16_native_predictor_poll_cpu_20261010.json`.
Use the new boundaries in the next frozen pair to identify the actual costly
request stage before changing frontend or scheduling policy.

Share one visible-request scan across extent receipts during one lease refresh,
grouped lazily by context. Retain exact workflow/invocation/session/generation/
epoch and next-request checks; do not cache visibility across calls. Skip the
physical-registration visible scan when the existing waiting-session proof
already establishes admissibility.250 related checks pass. With156 workflows
and16 receipts, mean thread CPU falls232.13->67.88us without a next consumer
and342.54->193.74us with one (70.8%/43.4%); queue scans fall16->1.
The single-receipt path is effectively unchanged; already-ready16-receipt
refresh is0.58us slower (1.6%). Keep the regression and full distributions.
Outputs and lease identity/validity agree. Report:
`experiments/reports/v16_restore_lease_matching_cpu_20261010.json`.
Native enqueue is stubbed; this is not GPU, DMA or throughput evidence.
Keep this change isolated from both frozen V16b arms.

Distinguish an observed next-epoch external client LLM_SUBMIT from native request
visibility. For an existing valid, unexpired native lock, keep protection only
within the established ACK+10s maximum and record `prefetch_client_submitted`.
Do not create a visible request or grant admission; native demand readiness
still requires an actual request and exact session/generation matching.
Internal summaries, old requests, pending tools and invalid/expired/unlocked
restores do not extend protection. A later READY observation must not shorten
this already submitted protection.385 related runtime/physical/tool/policy/
audit checks pass. Keep this change isolated; GPU benefit is unverified.

The lifecycle audit previously omitted intermediate reentry-ready/submission
records; retain them and locate locked FULL receipt expiry along complete
event->client submit->native arrival->first service. Missing/unordered evidence
remains unknown; expiry is not a reuse miss. An incomplete V16b snapshot verifies
437.84MB JOIN and296.45MB tool early FULL reuse. Of16 locked FULL expirations,
14 precede completion,1 lies before client submit and1 before native arrival.
The last tool case completes152ms and submits209ms after ACK, reaches native
visibility at4.64s, but releases its original lock at3.24s. This does not explain
all missed opportunities, and ACK+10s does not guarantee timely later admission.
Preserve this limited evidence in
`experiments/reports/v16b_prefetch_lifecycle_snapshot_20261010.json`.
Next inspect early prediction/delivery, bounded reclaim opportunities and native
admission separately; do not uniformly lengthen speculative leases.

Cold-candidate publication preserves the first eight candidates actually read
by each native pool and the fallback prefix. Stop scanning only after both pool
prefixes are complete; a sparse pool must not hide a later eligible node.
Keep live node identity, Host copy, lock/session-reference and pending-transfer
checks, plus native validation at reclamation.272 related checks pass, with68
policy checks repeated after fixing the sparse-case loop overhead. In a156
workflow/32-node CPU fixture, sufficient candidates reduce node lookups from
4992 to256 per publication and mean thread CPU5.036->0.235ms (95.3%).
Sparse FULL/Mamba scenarios retain the lookup count and reduce mean thread CPU
14.5%/19.3%. Native-consumed prefixes and their order agree. Full distributions,
imported paths and source fingerprints are in
`experiments/reports/v16_pressure_publication_cpu_20261010.json`.
This changes neither the native reclaim limit nor the frozen V16b arms, and
establishes no GPU/throughput improvement.

## Current Objective And V15 Comparison

Keep the fixed workload, model, capacity and arrival schedule. Continue until
predictive has a verifiable performance improvement relative to native.
Agent scheduling, shared-path CPU cost, predictive H2D and PREPARE belong to
the same objective. New experiments run predictive_h2d first, then native;
reactive comparisons also run predictive first. The launcher and plan generator
default to this order, including formal repetitions. On an actual predictive
implementation fault, stop, retain evidence, fix and cold-start before proceeding
to the baseline. Preserve historical frozen plans and resume order; do not
modify historical V15 artifacts. Both arms, audits, HTML exports and cleanup
are now complete, so the working-code freeze is released. Later dated entries
retain the status at their original observation time. The three explicit
priorities are:

- Restore-to-service: shorten ACK-to-first-service and reduce repeated native
  loads before service, while accounting for missing pages and other workflows.
- PREPARE consumption: attribute pressure parking, native/controlled reloads,
  Host eviction followed by re-backup and unobserved restoration, then reduce
  unnecessary backups.
- Useful FULL coverage: validate real handoff FULL reuse and replacement of
  demand restoration, alongside throughput, JCT and recomputation.

Additional execution requirements:
- Treat missing FULL extents and re-backup after Host eviction separately.
  Reduce cold-copy reclaim/re-backup control and bandwidth costs within the
  existing native-relative performance objective.
- Speculative PREPARE copies only missing FULL prefix extents. Reuse valid Host
  copies, including their native redistribution on radix splits; re-copy an
  extent only after its Host copy is lost. Native already skips backed FULL.
- Remove Mamba from speculative PREPARE. Native eviction write-back and
  required-state restoration remain responsible for resumable checkpoints.
- A later same-node/pool D2H is an association, not proof of overwritten or
  duplicate bytes. Rename that audit field to later_node_pool_d2h and state the
  unresolved allocation/split identity limits.
- Complete missing FULL input prefixes in a bounded ancestor-first burst,
  retaining per-node reservations and ACK identity. Submit earlier queued nodes
  if a later node declines. One missing extent uses the direct native write.
  Measure actual consumption, repeated restoration and CPU cost; batching or
  ACK counts alone do not establish native-relative performance gains.

These are concrete execution requirements of the existing active /goal;
the native-relative throughput direction is unchanged. For legacy merged transfers, subtract
known child pool receipts before attributing the remaining untagged nodes.
V10 re-audit finds intervening same-node FULL Host eviction before all 35066
later D2H associations. This supports reclaim followed by re-backup, not
overwriting valid Host extents; it cannot establish exact duplicate bytes
across splits. The legacy residual correction did not change these counts.
Report: experiments/reports/v10_prepare_full_incrementality_20261009.json.

Current status:
- V15 final: native and predictive both complete156/156 without errors or
  incomplete outcomes; correctness is not independently graded. Collection
  durations are13410.271/10857.481s, completed workflow rates41.878/51.725
  per hour. Predictive makespan improves19.04%, but mean/P50/P95 JCT worsen
  11.42%/16.81%/3.92%;95/156 paired tasks are slower. Native's repeated155s
  exit31 commands on pylint-6528 dominate the final tool tail. In600--2400s,
  similar47.3 decode batches produce native/predictive GPU82.328%/75.900%
  and1089.888/961.813 output tokens/s. The objective remains unmet.
  Keep all workflows and the live-trajectory limits; do not isolate a favorable
  subset or claim a causal KV-policy speedup.
- Client latency is improved, without changing predictor weights. V14/V15
  unique JOIN-associated child samples74/75 have native-done-to-client-result
  P501375.965/254.628ms and JOIN-to-parent-submit627.861/25.357ms.
  Actual parent arrival-to-service P90 remains11.163/14.512s. The0--500ms
  RETURN lead count rises23/105->57/99, but87/99 V15 JOIN commands occur
  after native EOS; only12 use estimated work before EOS. Report client
  delivery, real pre-EOS prediction and admission waiting separately.
- Anticipation is176 commands/6.059GB, including1.230GB FULL with1.036GB
  verified reuse. Demand handoff is15132 node commands/118.265GB; never
  count it as prediction. Predictive native H2D3.664TB exceeds native3.268TB.
  Instrumented exclusive Python wall is1283.355s versus113.504s of summed
  H2D transfer-stream time; neither is an additive critical-path saving.
  Prioritize the committed control-cost, residency and FULL-burst changes,
  then check busy-window service and JCT on the next same-version pair.
  Full analysis:experiments/reports/v15_final_analysis_20261010.md.
- All future experiments use a hard180s sandbox command execution limit.
  Normalize both launcher/config values and model-specified timeouts to180s;
  retain effective/requested values in audit and freeze the value in new
  comparison plans. This supersedes the earlier explicit-extension advice.
  Native/reactive/predictive use the same limit; the14400s workflow deadline
  and600s LLM request timeout remain separate budgets.
- A read-only final V15 tool-duration audit uses execute_elapsed_ms separately from
  the per-workflow sandbox lock wait. Native/predictive exit-zero maxima are
  96.902/115.295s, with no naturally completed command above180s; both
  historical V15 arms retain600s.
  Pylint-6528 repeats one command 64 times at median 155s with exit 31, so 180s
  would not truncate that tail. Keep12/16 timeout/SIGKILL candidates separate
  from natural completion labels and do not convert their shortened durations
  directly to JCT savings. Report:
  v15_tool_timeout_distribution_final_20261010.json.
  The next pair must use the same180s limit and corresponding censoring labels,
  without adding repeat guards or changing the 14400s workflow deadline.
- The following isolated-development and dated observations were collected
  while V15 remained frozen; their "not deployed/live" statements describe
  that earlier state. V15 driver/server/client processes have exited, both
  HTMLs are available and312 workspaces were removed with none retained.
  After that completion, the live engine received only the validated exact
  follow-up delta. Old/full and candidate/full reverse checks pass, changed
  Python files compile, and the deployment manifest records the application.
  The isolated committed follow-ups are integrated with this status update;
  no new GPU comparison has been started or claimed as verified.
- The isolated admission planner computes waiting ranks once per call and
  groups demand-ready leases by context for candidate lookup. Keep workflow,
  invocation, session/generation, epoch and request/source checks after lookup.
  Skip matching without any ready restore; rebuild the index on each plan.
  Preserve native reentry reads, aging, bounded restore promotion and expiry/JOIN
  tie ordering. All260 related checks pass. Against pinned81b824e,300
  alternating iterations per case with two planning calls have equal plans,
  cumulative counters and native read counts. Queue156 with0/4/16/48 leases
  has mean wall3.737->3.488,3.938->3.648,4.133->3.600,4.797->3.758ms,
  reductions6.66%/7.37%/12.89%/21.65%. Queue8/4 leases regresses1.63%;
  retain every timing sample and outlier in
  v16_prefill_restore_order_cpu_20261010.json. This fixture uses real causal
  state and identity processing with deterministic stub-native residency.
  It excludes DMA, CUDA and end-to-end throughput. Commit only in isolation;
  the complete V15 freeze remains required before deployment.
- The isolated JOIN/tool PREPARE queues at most eight missing FULL extents from
  one safe input path before one controller submission. Existing valid Host
  copies and generation beyond the reusable checkpoint are excluded. Only this
  burst's pending ancestors may authorize its descendants; foreign writes and
  loads retain native exclusion. A fresh action-point closure is captured once.
  Partial decline preserves successful prior submissions and cancels only
  registered-but-unsubmitted commands. Candidate estimates describe the first
  selected extent; explicit burst IDs connect separate receipts without
  multiplying those estimates or granting use credit. Preserve
  v16_prepare_prefix_burst_cpu_20261010.json, the canonical engine patch and
  exact frozen-engine delta. The current package manifest is
  v16_engine_followup_manifest_20261010.json; forward/reverse application checks
  pass. All251 runtime/physical/audit and94 native CPU checks pass. For4/8/16
  extents, actual controller submissions change4->1,8->1,16->2 with equal
  per-node receipts and FULL bytes, and zero Mamba allocation. Retain raw
  timing distributions and outliers, not a blanket CPU acceleration claim.
  This CPU fixture executes native write/commit/
  ACK and receipt merging with CPU tensors and fake completions; it is not
  GPU/JCT evidence. Keep the complete V15 freeze before deploying.
- V15 was collected from frozen main commit
  feb5ee01a9f1340a694dcba442c439d08e4bd274 and full engine patch SHA256
  dbde39b7f37977ecacd72dddf78b3da94a56fa6a0879afb1798ae55ab7f1fe63.
  The existing driver runs predictive_h2d then native with fresh servers and
  caches. Keep156 tasks,108+48/3600s arrivals, running48, Host200GB80:20,
  HBM ratio0.9, context131072/completion8192, graph2048/reserve32,
  workflow14400s, native_in_graph_2to4, seed21/temperature0 and artifacts
  unchanged across both arms. Main and engine remained frozen through exports
  and cleanup; all subsequent development stayed in the isolated worktree.
- V15 predictive has completed156/156 workflows with no errors or incomplete
  outcomes:10857.481s,51.725 completed workflows/hour, JCT P503119.630s,
  mean GPU utilization74.476% and795.773 output tokens/s. Task correctness
  is not independently graded. JOIN/tool have99/77 ACKs and FULL sent/reused
  0.893/0.743GB and0.338/0.293GB; demand handoff has15132 ACKs and FULL
  67.205/67.047GB, which is not anticipation. Forecast age P50 is137.325ms
  and mean inference28.603ms. These are the earlier single-arm observations;
  the final native comparison and its limitations are recorded above.
  At2026-10-10 10:50 CST, native has150/156 completed with no error or
  incomplete outcomes and live service; main and the real engine are unchanged.
- The completed predictive input audit uses the exact pinned tokenizer.
  Right truncation at256 MiniLM tokens can drop the newest suffix of the
  1024-character window. Last client windows truncate502/24014 requests and
  336/370 no-tool normal-stop rounds(90.81%); the latter drop54 tokens at P50.
  These are encoder tokens, not Qwen decode/KV tokens, and the windows are
  not guaranteed to have been consumed before native EOS. Train/inference
  share this policy; representation limits are not demonstrated error causes.
  Preservev15_semantic_encoder_window_20261010.json. A CPU comparison of
  inexpensive suffix sequence features is complete. Restore the original18650
  work observations/embeddings using the pinned cached-source manifest;
  structural refit reproduces the V7 numerical weights and selection loss.
  Adding64 hashed sequence features over the latest32 words/symbols regresses
  Astropy selector log pinball0.146075→0.153486 and Sphinx's31 last-request
  snapshot absolute-token-error P5026.631→44.179. Keep phase, encoder and
  threshold frozen; reject candidate deployment. Sphinx's nine workflows are
  reused development data and have no notified snapshots in this cohort, so
  they cannot validate notice-driven H2D triggering. Preserve
  child_semantic_suffix_cached_samples_20261010.json and the isolated
  child_semantic_work_suffix_candidate_20261010/report.json. No V15 labels
  enter selection, no second encoder pass is introduced, and the frozen
  native-relative comparison remains the next throughput check.
- The isolated terminal-cache sampler now directly serializes scalar node
  observations instead of recursively copying dataclasses. Preserve the last
  observation of a shared ancestor, node order, native read counts, sampling
  frequency and unavailable/gone-anchor handling; no cross-anchor physical
  state cache is added. All164 runtime/observer checks pass. Against782c283,
  identical four-watch/24-level fixtures,200 alternating iterations per case,
  give mean wall2.666→0.911,3.379→1.641 and6.808→5.110ms for1/2/8
  anchors:65.83%,51.44% and24.93% reductions, with similar thread CPU
  changes. Outputs excluding timestamps and native read counts are equal;
  imported isolated source paths are pinned. The writer is an in-memory sink,
  so this excludes disk writes, GPU work and end-to-end performance.
  Preservev16_terminal_scalar_serialization_cpu_20261010.json, commit only
  in isolation and deploy after both V15 arms and full postprocessing.
- Within one resident-first admission plan, skip rotating positions already
  covered by the fixed first eight. Preserve scan order, rotating coverage,
  fresh observations on the next call and native read counts; add no persistent
  residency cache. All201 related admission/runtime/policy checks pass. The
  pinned13491b2 CPU fixtures retain16 rounds and two planning calls, with300
  iterations and mixed stub-native residency. Queue8 mean wall falls
  0.562451→0.442924ms(21.25%); queue156 falls4.160591→4.130053ms(0.73%),
  with P503.767605→3.719760ms and large wall outliers retained. Every call's
  ordering equals its corresponding baseline call, and native inspection
  counts are identical. A later call can discover new residency and legitimately
  reorder requests. Preservev16_resident_scan_cpu_queue8_20261010.json and
  v16_resident_scan_cpu_queue156_20261010.json. These are CPU fixture results;
  the large queue has no demonstrated robust gain and V15 remains frozen.
- The complete isolated schema2 JOIN audit covers99 node commands and75
  unique child requests:95 start before RETURN,57 within0--500ms, median
  submit lead309.66ms and82 ACK before JOIN. On unique children, native
  completion-to-client-result P50/P90 is254.63/1074.24ms, JOIN-to-parent-submit
  P5025.36ms, parent-submit-to-arrival P50127.72ms and arrival-to-worker
  P50/P90160.30/14512.15ms. Submit-to-worker P50/P90 is352.47/14930.20ms.
  These percentiles are not additive, worker intervals are not CUDA kernel
  measurements, and node commands are not independent child events. Preserve
  v15_join_pipeline_first_service_v2_final_20261010.json without overwriting
  the frozen arm's reports. Separate client completion delay, early work
  underprediction and actual admission waiting before adjusting leases.
- The isolated lifecycle audit now groups positive-FULL commands by initial
  observed native lock and last observed release reason. Missing evidence is
  unknown; Mamba-only commands stay excluded. Report confirmed reuse, misses,
  unknown results, known FULL bytes and node/pool reload associations separately;
  a lease expiry is not a reuse miss or an isolated cause. All11 audit checks
  pass and every pre-existing V15 summary field equals the frozen report.
  V15 has174 JOIN/tool FULL commands:134 reused,31 unprotected/residency-lost
  misses and9 protected/expired misses. Six unprotected/expired JOIN commands
  were reused. The9 protected misses include3 observed-EOS commands and6
  estimated-work commands across5 children. The former start15--23ms after
  native EOS, with2.76--3.43s of subsequent protocol/RETURN delay and13.3--15.0s
  of arrival-to-service waiting; the latter start2.0--5.2s before EOS, retaining
  a real early-work estimation issue. Do not treat all9 as prediction failures
  or solve them with blanket longer leases. PREPARE has846 ACKs,196 later
  restoration associations and715 later same-node/pool D2H associations, all
  with intervening Host FULL eviction. Preserve the compact
  v15_prefetch_lifecycle_residency_final_20261010.json; detailed evidence remains
  in the frozen arm's logs. The CLI --summary-only avoids duplicated event rows.
  Issue-to-ACK protection is already fixed in isolation but V15 lacks the issue
  budget telemetry to prove how many unprotected misses it would recover.
- The PREPARE lifetime audit now separates restore associations before and
  after observed Host eviction, without changing latest-writer counts.
  All14 audit checks pass; every pre-existing summary field is unchanged.
  All196 V15 restore-associated commands have observations before eviction,
  totaling697 node/pool associations;650 commands have no observed restore.
  All846 FULL prepared commands have a subsequent observed eviction, with
  ACK-to-first-published-node-eviction P50/P9047.935/2378.784s. Stop this
  interval at the next same-node/pool D2H writer; it is not the lifetime of
  the entire prefix or proof of allocation continuity. There are86 repeated
  source/context/epoch/node/creation groups,141 additional prepares and a
  maximum14 commands per identity; missing identities are excluded. Splits
  and shared prefixes still prevent exact duplicate-byte or wait-episode
  claims. Inspect real pressure/reclaim opportunities before adding arbitrary
  Host leases or preparation cooldowns. Preserve the compact report
  v15_prepare_host_lifetime_final_20261010.json and frozen raw evidence;
  commit this audit in isolation without changing live scheduling.
- Isolated PREPARE candidate records now include the actual issued command ID.
  Link candidate estimates to ACK/reclaim/restoration by that ID; never infer
  legacy command identity from a shared node or nearby timestamp. All123
  runtime/audit checks pass, and previous summary values remain unchanged.
  V15 has127 candidate records with direct pressured-byte reclaim potential
  and719 without it. The latter account for6.457 of8.361GB estimated selected
  transfer bytes(77.23%). An ancestor backup can be necessary for a later
  exclusive checkpoint, so zero direct reclaim does not establish waste;
  these estimates are neither DMA bytes nor actual reclaimed capacity.
  All846 V15 candidate records lack command IDs, so preserve aggregate
  observations without retroactive per-command ACK credit. Extend the existing
  v15_prepare_host_lifetime_final_20261010.json instead of duplicating it.
  Inspect full-prefix completion and actual consumption before selecting a
  filtering/batching policy. At2026-10-10 10:07 CST, native has94/156 completed,
  no error/incomplete outcomes and live scheduler/client processes. Keep V15
  frozen and deploy this attribution field only in the next comparison.
- The isolated semantic worker now reuses a nonblocking poll listener to check
  the result pipe before Queue.get_nowait, avoiding selector construction on
  empty polls. Retain per-tick result, process-exit and timeout checks, input
  freshness and batch dispatch. Relevant checks pass38 with one old-artifact
  integration skip. Against4d6e6ea, real spawned CPU processes and bounded queues
  with deterministic four-reply batches preserve inputs, replies and active
  state. On20000 alternating idle/inflight-empty polls, mean wall falls
  8.666/8.560 to3.371/3.283us(61.10%/61.64%). On400 result-ready polls,
  mean rises49.280 to51.129us(3.75%); retain this cost. The benchmark excludes
  neural inference, DMA and GPU stalls, so it cannot establish JCT savings.
  Preservev16_semantic_worker_poll_cpu_20261010.json and commit in isolation.
  At2026-10-10 09:42 CST, native has77 completed and no error/incomplete
  outcomes; keep the full V15 freeze through both arms and postprocessing.
- At2026-10-10 05:40 CST, the partial V15 H2D source snapshot has native
  2358 batches/731.314GB, JOIN/tool72 commands/1.384GB, demand handoff
  4692 node commands/1524 batches/36.050GB and no unknown controlled source.
  The slightly later consumption snapshot has handoff FULL sent/reused
  19.283/19.203GB and ACK-to-first-launch P5022.62ms; JOIN0.06738/0.01513GB
  and17.604s; tool0.22223/0.18760GB and1.244s. PREPARE has316 ACKs and
  49 restoration associations;54 pressure demotions include51 prior PREPARE
  associations. Snapshot cutoffs differ. No later same-node/pool D2H is
  observed yet; do not claim eliminated churn or native-relative throughput.
- Correct the JOIN audit in isolation before interpreting restore waits.
  Native LLM_SUBMIT is server arrival, not first GPU service. Schema2 uses
  the earliest matching gpu_service_sample.service_start_ts_ms with
  request/workflow/invocation/context/epoch identity and keeps arrival,
  prefetch first-launch receipt and worker service separate. Missing service
  remains unknown; worker intervals are not isolated CUDA kernel time.
  Preserve frozen reports and generate separately named corrected reports.
  V14 client-submit-to-worker P50/P90 is777.09/11950.68ms; old467ms was
  submit-to-arrival. At05:50 CST, V15 has27 node commands/18 child requests,
  unique-child EOS-to-finish P50397.39ms, JOIN-to-submit58.57ms,
  submit-to-arrival173.05ms, arrival-to-worker492.47ms and submit-to-worker
  2739.31ms/P90 about17.26s. Percentile components are not additive.
  A django10914 request changes from1.028s under the old arrival metric to
  16.059s to worker service, with its lease already expired before submission.
  Distinguish client backlog, admission and transfer waits before changing
  residency or priority. Twelve audit checks pass; no live policy is changed.
- The isolated next-revision reentry probe shares matched ancestor state only
  inside one read-only invocation. Each new call refreshes physical residency;
  preserve namespace, pending DMA, checkpoint selection, stale-node validation
  and the64-node ancestry bound. Avoid a memo dictionary on the single-leaf
  path. Sixty-one related checks pass. Seven samples of200 iterations with
  actual RadixKey give equal outputs and8-leaf4K/32K/96K CPU P50 reductions
  of63.9/80.5/84.9%; single leaves regress0.3--2.9% and first divergence
  goes4.642→4.968us. Preserve regressions and do not infer GPU throughput.
  Report:v16_shared_reentry_cpu_20261010.json. Initial candidate patch SHA256
  is32c915af99003b65f4cfa952a24e9716f7a3962cefde5ffe3dffddb6af3f63ca.
  Candidate reverse-check and frozen-live delta forward-check pass.
  The final package below supersedes this intermediate package. Commit the
  isolated package promptly; deploy only after both V15 arms and full
  postprocessing finish.
- The isolated PREPARE ranker now reads D2H service history only for a live
  tool-wait hint. JOIN candidates skip the unused estimate; capacity, reclaim
  rank and tool short-window rejection are unchanged. Forty-one related
  checks pass. With identical156-sample real service seeds, all five CPU
  configurations preserve selection/publication. Missing-backup mean falls
  4.51%, but the fully backed/no-lease case regresses17.58%; this does not
  establish stable full-path acceleration or GPU throughput improvement.
  Preserve all cases in v16_prepare_timing_skip_cpu_20261010.json. Commit
  in isolation and leave both live V15 arms unchanged.
- At06:13 CST, V15 has53 JOIN/76 tool commands. FULL sent/reused is
  0.455/0.319GB and0.336/0.293GB; ACK-to-first-launch P50 is1157/1026ms,
  P9020426/14958ms. Demand handoff11391 node commands send/reuse
  50.244/50.116GB FULL with P5024ms; do not credit it as anticipation.
  PREPARE has553 ACKs/151 restore associations. All324 later same-node/
  pool D2H associations have intervening Host eviction. This supersedes the
  early snapshot's absence of churn observations, not the frozen run.
  Report:v15_prefetch_sources_partial_20261010_0613.json.
- Of129 JOIN/tool actions,37 register at zero protection slots,43 obtain no
  native lock and41 unlocked cases still have positive byte capacity.
  Release observations include28 residency losses and12 expirations.
  Of nine estimated-work JOIN commands, eight submit2.0--5.6s before EOS
  without verified FULL reuse; the one220ms-ahead command is reused.
  These correlated node actions do not define independent forecast accuracy.
- The isolated next revision rechecks speculative protection slots at native
  before-enqueue and records the issued allowance. Completed ACKs may carry
  that bounded slot allowance across a transient slot reduction, while live
  bytes and other protected contexts still limit the lock. New issues and
  actual admission never use old allowances. Preserve lease duration,
  first-service/expiry/failure cleanup and fallback when capacity is unknown.
  The relevant suite passes198 checks; after two more boundary cases the
  policy file passes46. V15 lacks issue-budget evidence, so do not claim
  all37 zero-slot cases would be recovered. Commit in isolation; evaluate
  throughput only after both frozen V15 arms and exports finish.
- The next online semantic audit supports fixed byte snapshots of live files.
  Keep unfinished requests unresolved rather than counting them as false
  terminal classifications. Match only previously delivered forecasts and
  group estimated-work first triggers by child request. Separate forecast
  age, progressed tokens, intrinsic work error, remaining rate variation,
  native EOS and client RETURN; the rate decomposition is not GPU queue time.
  Only incomplete trailing records may be ignored during live collection.
- The06:30 CST V15 partial audit has five estimated-work requests, all over
  two seconds before EOS. Projected work is1.14--4.10 tokens versus52--122
  actual tokens, with299--803ms observation ages. Underestimated work
  accounts for2.60--4.73s at the trigger rate; the remaining rate gap is
  -0.74--0.69s. Across102 natural final requests, last pre-EOS point error
  has P50 signed/absolute46.50 tokens, with actual remaining P50 eight.
  Do not repair early underprediction and late overprediction using a
  single bias, a longer lease or a blanket priority increase.
- Delivery-time replay at500ms gives progress-countdown nine first
  triggers, one in0--500ms before EOS and six over2s early. Holding the
  old snapshot center gives three, zero and two respectively. This replay
  omits continuous scheduler ticks, target lifetime and physical admission;
  the simple alternative is not deployed. Preserve coverage and early
  triggers together. Report:v15_semantic_trigger_causal_partial_20261010.json.
  Next-revision trigger telemetry includes forecast progress/age, advanced
  tokens, projected remaining work and actual TPS. Short-circuit sufficient
  H2D evidence without scanning full history every decode; retain the
  three-sample requirement and separate evidence sources. Phase/work
  artifacts and both live arms remain unchanged.
- The isolated demand handoff checks current running/native slots before new
  frontier planning. Skip empty queues and zero-slot ranking/residency scans;
  existing tickets still process ACKs and remaining extents. Resume ordinary
  selection when capacity returns. Seventy-four relevant CPU checks pass.
  Four156-workflow/16-round CPU fixtures preserve selected targets across
  200 iterations. Zero-slot mean is2.717→0.021ms; empty queue0.0253→0.00327ms.
  Positive-slot differences are small and not evidence of acceleration.
  V15 zero-slot frequency and GPU benefit remain unmeasured. Keep this
  revision isolated until both frozen arms and postprocessing finish.
  Report:v16_handoff_frontier_cpu_20261010.json. The benchmark pins and
  verifies the actual imported worktree and records its source SHA256;
  the initial wrong-worktree result was regenerated. The existing two-call
  admission benchmark also preserves queue order in50 iterations.
- The isolated JOIN/tool PREPARE first reads a lightweight reusable-input
  prefix budget when Host FULL free space is below the input length. Count
  only missing FULL on the current safe checkpoint paths, deduplicate shared
  ancestors and exclude the generated tail. Skip full closure construction
  only when the observed missing prefix exceeds current free tokens; unknown
  observations retain full validation. Actual issue still rebuilds and checks
  the complete closure. Record prepare_prefix_budget_rejected_early; transfer
  scope and authorization are unchanged. The relevant suite passes326 checks.
  Six156-workflow/24-node configurations with60 iterations and identical156
  real transfer samples preserve selection/publication and terminal outputs.
  Clear probe backoff equally on both sides to measure actual probe costs.
  Missing-backup/Host-full PREPARE mean falls2.008→0.713ms, or64.49%;
  backed/Host-full regresses3.02% and missing-backup/Host-free regresses0.79%.
  Preserve these regressions and do not infer GPU or end-to-end throughput.
  Report:v16_prepare_prefix_budget_cpu_20261010.json. Commit in isolation;
  both frozen V15 arms and complete postprocessing remain unchanged.
- The isolated semantic text buffer retains only unsubmitted snapshots.
  Remove a frame after submission while preserving its forecast, completed
  decode progress, request identity and EOS evidence. Missing transfer
  targets or prior service still retry; new text coalesces at the original
  interval. Release pending frames beyond the existing1500ms validity bound.
  The128-frame limit now bounds pending text; semantic_unchanged_frame_skipped
  counts received duplicate submitted snapshots rather than repeated ticks.
  Keep model, phase threshold and H2D triggering unchanged. Related checks:
  171 pass, one skipped because the isolated pinned encoder artifact is absent.
  A high-score reply still creates a final-stage candidate after frame removal.
  Six CPU configurations/400 iterations preserve submitted model inputs and
  accepted forecasts. With12/48/96 submitted snapshots and no new text,
  mean reductions are91.38/97.78/98.82%;48 changes0.10810→0.00240ms.
  Target-retry mean falls56.73%; fresh text regresses0.30%, and idle regresses
  3.29% by only0.0463us. Match disabled timing wrappers on both sides.
  Worker replies score0.25; this benchmark does not validate high-score
  triggers, neural inference, real IPC or GPU throughput. Preserve all cases:
  v16_semantic_pending_cpu_20261010.json. Commit only in isolation until
  both V15 arms and full postprocessing finish.
- The isolated reentry probe binds tree/root/node lookup inside one read-only
  call and assembles the result once after selecting the best checkpoint.
  Refresh tokens and physical state on every call; retain shared ancestry,
  namespace, pending DMA, page alignment, the64-node limit, first-tie selection
  and valid checkpoints before a nonresumable tail. Seventy-six related checks
  pass. Eleven CPU samples/500 iterations with equal complete-path outputs
  reduce the six matching cases by1.9--6.7% at P50, first divergence by10.1%,
  and other divergence/short-request/page cases by1.4--3.1%. The preliminary
  raw-byte comparison regresses all six full matching cases and is rejected.
  These synthetic CPU figures do not establish GPU throughput improvement.
  Report:v16_reentry_result_cpu_20261010.json.
- The isolated physical capture reuses a Mamba anchor already observed on the
  FULL ancestry during this call. Validate its creation time, refresh physical
  state on each new call and retain native issue dependency checks. All344
  related checks pass, including reversed leaf order and state/version changes.
  Against ed02b21,156-workflow/24-node CPU fixtures with60 iterations preserve
  selections, pressure publications and every opportunity field except clocks.
  Mamba-ancestor sampling means fall29.47--30.16%; missing-backup/Host-space
  PREPARE falls37.10%, Host-only39.98%. Missing-backup/Host-full PREPARE
  regresses1.12%. The ordinary same-leaf first comparison has one10.60% mean
  regression despite a0.79% P50 improvement;180 iterations change that case
  to a2.81% mean reduction. Do not treat either as stable performance evidence.
  Ordinary sampling repeat means vary from0.30% slower to0.73% faster.
  Preserve all reports:v16_mamba_ancestor_capture_cpu_20261010.json,
  v16_shadow_capture_identity_cpu_20261010.json and its_repeat_cpu report.
  Both variants clear probe backoff equally and verify imported worktree paths.
  This is CPU evidence only; commit in isolation and retain the V15 freeze.
- The isolated JOIN PREPARE publishes pressure candidates once after scanning
  and native issue, retaining publication on Mamba-only and no-parent returns.
  Native PREPARE cannot reclaim Host/HBM and still validates dependencies;
  publication reads post-operation locks/pending state. Within-call maintenance
  indexes by context then checks the complete key, avoiding per-node identity
  hashes without conflating attempts/epochs or caching liveness across calls.
  All348 related checks pass. Against8d1db37,156-workflow/24-node fixtures,
  sixty iterations and the same156 actual transfer seeds preserve actions,
  pressure publication and complete opportunity fields. Both clear probe
  backoff equally. The three backed/device-resident PREPARE means change
  15.463→9.887,14.967→10.151 and13.464→7.766ms, down36.07/32.18/42.32%;
  other cases improve0.82--1.57%. First-case node lookups fall431508→216228.
  Unchanged sampling/terminal timing differences are not acceleration evidence.
  CPU fixtures do not establish live-path frequency or GPU throughput gains.
  Report:v16_prepare_publication_cpu_20261010.json. Keep the V15 freeze.
- At07:35 CST the partial causal semantic audit matches seven estimated-work
  first-trigger requests to prior delivered forecasts: five are over2s before
  native EOS, one within0--500ms and one within500--2000ms. Last pre-EOS
  signed/absolute work-error P50 for158 natural-final requests is46.80 tokens;
  trigger signed-error P50 is-72.82 tokens. Early underprediction and late
  overprediction still coexist. Do not apply a global bias or blanket lease
  extension; evaluate actual Host-only opportunities after the frozen comparison.
  Report:v15_semantic_trigger_causal_partial_20261010_0735.json.
- The partial V15 feature audit identifies303 snapshots from five requests
  with a client completion announcement but online notice_active=false.
  All five announced while two children remained, so the old action-bound
  stage rejected their notices. Tasks are requests-1142 and django11239,
  11095,11292,14349. The offline feature collector retains those announced
  facts, exposing an input mismatch. Client/server delivery is not atomically
  observed; these snapshots are clustered requests, not independent samples
  or proof of available Host-only H2D. Preserve
  v15_notice_input_alignment_partial_20261010.json.
- The isolated next revision stores child announcement history separately
  from transfer stages and binds only the next matching context/epoch/request.
  After graph-batch validation, record and bind in event order, including an
  announcement and the next epoch's submission delivered in the same batch.
  Stage expiry retains the historical feature; tools, native tool tokens,
  compaction, cancellation, RETURN, workflow end and mirror reset retire it.
  A further request cannot inherit it. Keep native H2D eligibility, capacity
  and dependencies unchanged. Forecast records expose the estimated report
  tokens, content character count and prior tool/model rounds without text.
  The related suite passes364 checks with one missing-artifact skip. Against
  8eac264, six unannounced CPU fixtures/400 iterations preserve model inputs
  and accepted forecasts; timing differences are not GPU or throughput gains.
  Report:v16_notice_input_cpu_20261010.json. Commit in isolation, retain the
  V15 freeze and verify input alignment and consumed FULL after the full pair.
- Skip PREPARE checkpoint expansion and path sorting when this captured closure
  has no FULL device extent missing a Host copy. Read current node lengths;
  keep no cross-cycle residency cache and preserve the missing-prefix/Mamba policy.
  The related suite passes289 checks. Against9c5a399, six156-workflow/32-node
  fixtures with180 forced probes preserve selection, publication and sampling.
  Backed device-resident PREPARE means fall2.50--2.71% and sampling5.64--6.03%;
  Host-only means fall9.60/6.09%. Unbacked/Host-sufficient PREPARE regresses
  0.0045%; other small differences and unchanged terminal timings are not
  acceleration evidence. Preservev16_prepare_backed_step_cpu_20261010.json.
  This is CPU evidence only; commit in isolation and keep V15 fully frozen.
- The final isolated engine package has full patch SHA256
  66aa563627fb8882808e290ff0ccde10535bc7d787a66eba6848baa8bcf203d8.
  Its exact frozen-V15 delta isv16_engine_followup_delta_20261010.patch with
  SHA256dced5a847a7416c8d8abdfa591914cd743c822c1f41735d0ce852d144f58deeb.
  Full-candidate reverse, frozen-live delta forward and candidate delta reverse
  checks pass. Preservev16_engine_followup_manifest_20261010.json alongside
  the patch and CPU evidence. The candidate includes existing isolated runtime
  follow-ups and remains undeployed until both V15 arms and full exports finish.
- V14 collection, outer driver, audits, HTML export and cleanup have all
  finished:154 completed, two incomplete, no errors out of156. Duration is
  10977.317s, completed throughput50.504/hour, output700.235 tokens/s and
  mean GPU utilization65.316%. Completed throughput is10.87% above V13
  but5.76% below historical V10 native. All three task sets and physical
  pool capacities match. This remains development evidence, not a same-version
  native-relative performance gain.
- Final V14 source audit: native12182 batches/3532.278GB, anticipation
  JOIN/tool642 commands/10.901GB, demand handoff14667 node commands,
  4681 batches/125.505GB; unknown controlled source0.
  JOIN/tool/handoff FULL transfer is0.981/3.610/70.517GB and verified
  first reuse0.620/2.639/70.128GB; ACK-to-first-service P50 is
  4584/2813/25ms. Native reload associations18/3/19 retain legacy
  pool identity limits; they do not establish exact duplicate bytes.
- Of1130 final PREPARE ACKs,206 have subsequent restoration associations.
  All703 later same-node/pool D2H associations have intervening Host eviction.
  Pressure demotion occurs52 times,48 linked to a prior PREPARE ACK.
  Preserve incrementality and reduce reclaim/re-backup and unconsumed backups.
- Final JOIN105 node commands correspond to74 child requests;22 commands
  precede native EOS and83 follow it. Unique-child EOS-to-finish P50 is
  1242ms, JOIN-to-parent-submit628ms and submit-to-native-arrival467ms.
  Corrected submit-to-worker-service P50 is777ms.
  Child-close HTTP overlaps JOIN-to-submit by406ms at P50; this is not
  a causally isolated or additive JCT saving.
- Final runtime reports final=true, physical_disabled=false and no semantic
  worker error. django11087 has no semantic completion and empty reasoning
  retries; django11555 ends with two length responses and unresolved work.
  Client exit1 reflects native-JCT eligibility for154/156, not an established
  implementation failure. Cleanup removed154 workspaces and retained two.
- The committed follow-up package and exact engine delta are deployed for V15.
  The fresh same-version native arm follows predictive automatically with
  the same shared harness improvements. Direct comparison-script imports
  passed the completed three-run report. Keep both arms frozen.

The following V13 results and timestamped V14 development observations are
historical records. Statements about keeping V14 live unchanged applied during
its collection; no V14 process or postprocessing remains active.

- V13 collection, driver audits, timeline export and workspace cleanup have
  finished. Outcomes: 154 completed, one error, one incomplete out of 156.
  Duration12171.006s and45.551 completed/hour remain behind V10 native's
  10345.487s and53.589/hour. The performance objective remains active.
- Complete source-v2 comparison confirms the same task set and physical pool
  capacity. In the80--90min band both run about47 requests, but V13/native
  GPU utilization is48.56/58.53% and output509/742 tokens/s. The deficit is
  not only the final workflow tail. Instrumented exclusive Python wall totals
  are1734.347s, including JOIN PREPARE398.406s, opportunity sampling188.376s
  and reentry inspection184.171s; these are not isolated GPU idle intervals.
- Source-v2 final H2D: native3839.402GB, JOIN/tool760 commands/11.824GB,
  demand handoff18008 commands/158.846GB, unknown controlled0. Preserve
  frozen action-name reports;18768 PREFETCH_GPU commands are not all anticipation.
- JOIN/tool/handoff FULL bytes sent are0.908/3.833/81.708GB; proof-v2
  first-use bytes are0.453/2.695/47.154GB; ACK-to-service medians are
  7058/3220/815ms. Later native-load associations31/8/599 do not establish
  exact duplicate bytes under legacy pool evidence.
- Of16087 PREPARE ACKs,180 have observed restoration associations.
  All15603 later same-node/pool D2H associations have intervening Host eviction.
  Keep valid FULL copies, budget all missing checkpoint extents and reduce
  reclaim/re-backup churn. Do not restore speculative Mamba PREPARE.
- V13 JOIN127 commands correspond to83 child requests; only19 commands
  precede native EOS. Unique-child EOS-to-client-result P50 is1534ms;
  result-to-RETURN425ms, JOIN-to-parent-submit866ms, submit-to-service861ms.
  These boundaries must remain separate from completion-work prediction error.
- Follow-up code through498c0ab is merged; the engine delta is applied and
  the full staging patch reverse-check passes. All previous focused checks
  remain the validation basis; the package has no GPU performance evidence yet.
- V14 is running from frozen50b9179 with the same156 tasks,108+48 arrivals,
  running48, Host200GB80:20, HBM ratio0.9, heads/prompt/seed21 and budgets.
  The existing full driver enables the existing HTTP/finish-chunk timers.
  Measure real FULL consumption, source-specific restore waits/reloads,
  PREPARE churn, recomputation and native-relative throughput.
- At2026-10-10 02:20 CST, physical actions remain enabled, the semantic worker
  has no error and PREPARE is missing_full_prefix. JOIN/tool and demand-handoff
  ACKs are present. Early instrumented exclusive Python wall is214.68s over
  1119.96s, with JOIN PREPARE33.00s and reentry29.75s; these are not GPU-idle
  attribution or terminal performance results.
- Develop the next reentry optimization in the existing isolated worktree.
  Whole-node checks use a boolean complete-segment comparison instead of an
  LCP search, preserving namespace/salt, offset, limits, bigram and page semantics.
  Native allocation, state dependencies and physical residency validation remain.
  The actual-RadixKey full-reentry CPU benchmark has equal outputs and47.4--58.8%
  lower complete-match cost on4K/32K/96K paths. Related CPU checks:87 passed.
  Keep it out of live V14 until the entire driver/export/cleanup exits.
  This CPU evidence does not establish a GPU throughput improvement.
- Reduce closure-observation allocation cost without removing capacity,
  reference, lock or residency checks. The corrected benchmark loads historical
  observer/physical/runtime together. Against19650ab,156 workflows,64-node
  paths and40 interleaved iterations show equal selection/publication/targets,
  14.4--15.4% lower PREPARE mean in missing-backup/Host-full/Host-only cases
  and11.2--12.8% lower sampling mean. Fully backed PREPARE is approximately
  unchanged or3.2% faster; preserve the earlier outlier-sensitive20-iteration
  report rather than claiming a universal gain. Existing observer/physical144
  and runtime88 checks passed. Keep this change out of live V14.
- Extend the existing JOIN audit with native EOS, delivered finish chunk,
  last raw HTTP read, LLM_END entry, LLM_RESULT, RETURN, JOIN and parent
  submit/service boundaries. Preserve unknowns for legacy or mismatched
  identities and report unique child requests separately from extent commands.
  At02:53 CST the partial snapshot has60 commands/37 child requests;
  45 commands submit after native EOS. Unique-request EOS-to-finish P50 is
  2962ms, finish-to-callback94ms, callback-to-result0.112ms,
  result-to-RETURN103ms, JOIN-to-parent-submit859ms and submit-to-service678ms.
  Do not count client backlog as successful prediction lead.
- Prioritize measured HTTP/framework consumer backlog and JOIN-to-submission
  cost before lengthening speculative residency. One django11400 request
  consumes the finish chunk10.437s after native EOS; its1500ms lease expires
  before parent submission and FULL allocation reuse is unconfirmed.
  HTTP pull and consumer-pause totals cover the entire stream, not only
  completion-to-RETURN. Current triggers already require the last unfinished
  child; do not attribute this case to an unverified multi-child trigger.
  An8s nonblocking GIL profile has204 successful samples and94 failed reads.
  SDK payload transformation, incremental tool JSON and model construction
  are candidate CPU work, not proven attribution of the single-request delay.
- Implement the SDK payload part in the isolated worktree. For native-tagged
  Chat Completions, merge already-converted wire messages through extra_body
  after SDK typed traversal. Keep tools, sampling, runtime/session/deadline
  metadata, response parsing and explicit extra_body overrides identical.
  Untagged Chat and Responses keep their inherited paths. The shared harness
  makes this a common-path optimization, not an extra predictive-only signal.
  With installed openai2.6.1/langchain-openai1.1.9/httpx0.28.1, actual final
  JSON requests match through synchronous/asynchronous and streaming/nonstreaming
  MockTransport. Existing adapter54 and new protocol7 checks passed.
  Thirty interleaved full request-construction samples show means of
  2.711→0.835ms for11 messages,13.032→1.284ms for67,48.162→2.752ms
  for259. Preserve source/body hashes and versions. These synthetic CPU
  gains are not measured V14 client-delay or GPU throughput improvements.
  Incremental tool JSON and stream-model construction remain separate work.
  Do not deploy to V14; wait for the full driver/export/cleanup to exit.
- Eliminate the second measured SDK round trip in the isolated worktree:
  native-tagged ordinary Chat streams pass SDK-decoded data directly to
  inherited LangChain conversion. Keep the SDK SSE decoder, error handling,
  context-manager close and final tool semantics; preserve typed resource
  paths for response headers, structured responses, nonstreaming, untagged
  requests and custom clients. Synchronous and asynchronous checks compare
  chunks, final messages, usage, finish metadata, stream errors and early
  close; the combined stream/payload/adapter suite passes71 checks.
  Thirty interleaved samples for67/515/395 frames reduce synchronous thread
  CPU means9.419→2.034/70.291→13.497/63.662→18.140ms and asynchronous
  means9.544→2.168/69.862→16.692/65.006→18.705ms. The395-frame case
  includes136 tool-argument fragments. Final request JSON and results match.
  Preserve wall/thread CPU, installed versions and source/result hashes.
  These synthetic measurements exclude network, GPU and callback work;
  they do not establish reduced live JOIN delay or throughput improvement.
  Keep live V14 frozen.
- Optimize non-object tool-argument fragments without changing final parsing.
  A prefix that cannot become a JSON object by suffix removal retains the
  same raw tool_call_chunks and invalid_tool_calls without repeated partial
  parsing. Object prefixes, noncanonical fields and merged arguments keep
  inherited semantics. The combined tool/stream/payload/adapter suite passes
  89 checks, including short-prefix combinations and final merge equivalence.
  Sixty interleaved actual SDK SSE CPU samples reduce tool-case means
  39.4--98.4%. Pure-text synchronous mean is13.330→13.444ms and asynchronous
  is14.747→13.533ms; preserve the earlier30-sample report with its asynchronous
  text regression. GC remains enabled, included and separately measured;
  pure-text asynchronous P50 is11.366→11.414ms. No production GC change,
  network/GPU/callback benchmark or measured throughput gain is implied.
  Commit and preserve both reports; deploy only after the full V14 driver
  has exited.
- Remove terminal session HTTP dispatch from the child RETURN critical path.
  Mark the context terminal immediately and submit close work to an independent
  shared pool of up to32 workers; do not hold the session lock across HTTP or
  reuse the workflow executor. Apply the same harness to all three arms.
  Drain a workflow's pending closes before its audit/backend shutdown, preserve
  failed identities and retry during cleanup. Keep synchronous compaction.
  Record enqueue-to-start and HTTP completion separately. Native HTTP200
  confirms dispatch acceptance, not scheduler reference release or physical
  reclamation; correct the former "native reference close ACK" wording.
  At04:03 CST,95 JOIN commands correspond to67 unique child requests.
  JOIN-to-parent-submit P50 is695ms; child HTTP-close overlap with that
  interval is419ms and all matched parent submissions follow HTTP completion.
  The overlap is not a causally isolated JCT saving. EOS-to-finish P50 is
  1444ms and76/95 node commands submit after EOS, so client consumption
  still needs live verification after deployment.
  Session/adapter/harness checks pass225 with one skip; JOIN audit checks
  pass9. Blocked-close concurrency checks verify parent progress, terminal
  no-reuse, deduplication, retry identity and full drain before failure reporting.
  Keep this follow-up revision out of live V14. No GPU gain is established.
- Deduplicate multi-anchor terminal-cache summaries before dictionary conversion.
  Keep the original single-anchor serialization path.
  Keep independent live observations of every anchor, last-observation values,
  output order and all fields. Do not cache live ancestors across action-local
  physical checks. Two existing terminal checks pass, including changing shared
  ancestry. A bounded recent log sample has130 two-anchor and90 one-anchor
  terminal rows; it is not the full-run distribution. Sixty interleaved CPU
  iterations at24/64-node depth reduce final two-anchor means32.9/35.1%;
  eight-anchor synthetic bounds reduce58.3/61.2%. Final single-anchor means
  are approximately unchanged:24-node0.41% slower,64-node0.19% faster.
  Retain the intermediate report with a2.55% single-anchor regression; it
  prompted preservation of the original path. Match output fields/order and
  native read counts, retain source hashes
  and wall/thread CPU. Keep this optimization out of live V14; it is no proof
  of GPU throughput improvement.
- V13 cleanup removed154 archived workspaces and retained two forensic
  workspaces. Preserve summaries, patches, telemetry, final reports and HTML.

V15 Deployment Record (Completed Before Launch):
- V14 is fully finished, including audits, HTML and workspace cleanup;
  deployment can proceed. Final outcomes and performance are recorded above.
- The final source audit has642 JOIN/tool commands/10.901GB,
  14667 demand-handoff node commands/125.505GB and3532.278GB native H2D,
  with no unknown controlled source. Handoff FULL transfer/reuse is
  70.517/70.128GB and ACK-to-service P50 is25ms; JOIN/tool still wait
  4584/2813ms. Do not credit demand handoff as anticipatory prediction.
  PREPARE has1130 ACKs/206 restoration associations; all703 later same-node/
  pool D2H associations have intervening Host eviction. Reduce reclaim and
  re-backup, not valid-copy reuse. Unique-child EOS-to-finish P50 is1242ms,
  JOIN-to-parent-submit628ms, with406ms overlapping close HTTP. These are
  final consumption and overlap evidence, not an isolated JCT/throughput gain.
- Follow-up performance code is committed through edbe4dc, with subsequent
  goal and deployment documentation. Its complete staging patch
  SHA256 is dbde39b7f37977ecacd72dddf78b3da94a56fa6a0879afb1798ae55ab7f1fe63.
  Candidate reverse-check and pristine upstream temporary-index forward-check
  passed. A file comparison over all32 patched paths confirms that live
  Mamba cold-demotion helpers already match the candidate. Only radix_cache,
  unified_radix_cache and two test files differ:149 insertions/one deletion.
  The generated experiments/reports/v15_engine_delta_20261010.patch passes
  a live forward-check and candidate reverse-check; SHA256 is
  d1a1720cace4b06557d728f709a638d0644ab3727d19c11576b3581b7ced38a0.
  This exact delta is deployed and the complete staging patch verified.
- The committed package is running V15 with the existing full AB driver
  in predictive_h2d/native order, fresh server and cache for each arm.
  Keep156 tasks,108+48 arrivals separated by3600s, running48,
  Host200GB80:20, HBM ratio0.9, context131072/completion8192,
  graph2048/reserve32, workflow14400s, fanout native_in_graph_2to4,
  seed21/temperature0, lead500ms and the same model artifacts.
- Shared SDK payload, raw-stream, tool-fragment and deferred-session-close
  improvements apply to native as well as reactive/predictive. A fresh native
  arm with this same package is required for a native-relative gain claim;
  V10 native remains historical development evidence.
- Keep anticipation JOIN/tool, submitted-demand handoff and native H2D
  separate. Evaluate actual FULL reuse, restore-to-service delay/reloads,
  PREPARE reclaim/re-backup, recomputation, throughput and JCT. Same seed does
  not fix model trajectories. Formal paired repetition remains later work.
  Main's ignored experiments/models contains the actual artifact files;
  retain those paths rather than using absent isolated-worktree defaults.

Final source/consumption/pipeline reports are in experiments/reports:
v14_h2d_sources_final_20261010.json,
v14_prefetch_sources_final_20261010.json,
v14_join_pipeline_final_20261010.json.
Complete three-run comparison: v14_native_policy_comparison_20261010.json.
Historical V13 source/consumption/pipeline reports:
v13_h2d_sources_final_20261010.json,
v13_prefetch_sources_final_20261010.json,
v13_join_pipeline_final_20261010.json.
Complete native comparison: v13_native_policy_comparison_20261010.json.
Next-revision CPU comparison: v15_reentry_cpu_benchmark_20261010.json;
reproduce with scripts/benchmark_native_reentry_cpu.py against the frozen live
engine and the isolated candidate engine.
Closure comparison: v15_closure_prepare_cpu_repeat_20261010.json.
Partial JOIN HTTP audit: v14_join_pipeline_http_partial_20261010.json.
Partial client stacks: v14_client_gil_profile_partial_20261010.txt.
Next-revision SDK CPU comparison: v15_client_payload_cpu_20261010.json;
reproduce with scripts/benchmark_child_request_payload_cpu.py using only
MockTransport, with no GPU or serving call.
Next-revision stream CPU comparison: v15_client_stream_cpu_20261010.json;
reproduce with scripts/benchmark_child_stream_cpu.py through the actual SDK
SSE decoder and inherited LangChain conversion/aggregation.
Next-revision tool-fragment comparisons:
v15_tool_fragment_cpu_20261010.json,
v15_tool_fragment_cpu_repeat_20261010.json.
Reproduce with scripts/benchmark_tool_fragment_cpu.py, retaining enabled GC,
equal final request JSON/results and source/version hashes.
Partial terminal-close JOIN audit:
v14_join_pipeline_session_close_partial_20261010.json.
Reproduce with scripts/audit_join_transfer_windows.py; compare HTTP dispatch
timing and interval overlap without treating either as scheduler release.
Terminal sampling CPU comparisons:
v15_terminal_sampling_cpu_final_20261010.json,
v15_terminal_sampling_cpu_depth24_final_20261010.json.
Reproduce with `scripts/benchmark_native_prepare_path.py --terminal-only
--baseline-revision 6b951f5 --depth 24 --iterations 60` (or `--depth 64`).
CPU evidence must not be counted as end-to-end speedup.
The following V11--V13 entries describe the frozen collection history;
references to keeping live V13 unchanged no longer imply a running process.

## V11 Through V13 Revision History

V11 implementation:
- Size PREPARE pressure from up to eight next-prefill candidates, the running
  limit and active decode page growth. Cache occupancy alone is not demand.
- Revisit exhausted or Host-full contexts after one second; new identity/epoch
  is immediately eligible. Native enqueue still revalidates physical state.
- Publish separate FULL-leaf and Mamba-state reclaim indexes before native's
  eight-candidate bound. Retain all native ownership and DMA checks.
- After an unadmitted NO_TOKEN, try up to eight later unaged tagged requests
  only if PrefillAdder's current budget allows more. Preserve ten-second aging.
- Persist reconciled per-operation pool receipts for native and controlled
  transfers. Track the latest node/pool writer and subsequent H2D; restoration
  is not final model-forward credit. Report execution handoff separately.

V10 retrospective: 35713 acknowledged PREPARE operations; 115 have observed
reload associations (98 native, 29 controlled, overlapping), while 35066
have later same-node/pool D2H appearances, not verified byte overwrite. Old logs only support legacy batch-pool
association; do not classify all 35598 without observed reload as waste.
Report: experiments/reports/v10_prepare_restore_attribution_20261009.json.

Against 2abb957, the 156-workflow/24-node CPU benchmark reduces PREPARE by
2.67%/11.82% for backed/no-lease and four-lease cases, 15.49%/56.58% for
unbacked/Host-full cases. Selected backups, unprotected candidate sets and
sampled targets agree. Active restore leases remain excluded by the native
validator. Main regression: 223 passed; later engine/launcher checks: 73 passed.
These are CPU and correctness evidence, not GPU throughput evidence.

Continue cold-start predictive runs with the same 108+48 arrivals, running48, Host200GB80:20,
HBM Mamba/FULL0.9, frozen heads, prompt, seed21 and budgets. Reuse the completed
v10 native as a cross-revision development reference. Monitor actual runtime
failures and physical disablement; stop, retain evidence, fix and cold-start
on an implementation fault. Keep v10 frozen and quantify realized trajectory
differences. Final claims still require repeated fixed-configuration pairs,
with predictive first for new runs and the fixed-order limitation reported.
V11 froze 3d750f1 and still permits Mamba PREPARE. The FULL-only change is
developed in /tmp/beliefkv-full-prepare-20261009 for the next cold-start revision.
V11 stopped after the scheduler crashed at 2026-10-09 21:34:34 CST:
handoff passed a sparse eviction tracker to an accumulator that assumed
preinitialized FULL/Mamba keys. Fix sparse accumulation, preserve live unbacked
Mamba at actual FULL-leaf eviction, commit and cold-start V12 with the same
workload, arrival schedule, model and capacity. V11 is partial mechanism
evidence, not a terminal throughput comparison.
V12 stopped at 2026-10-09 22:08 CST to fix FULL reuse attribution.
Native cached_tokens_device/host account cache origin, while acknowledged
H2D extents can already belong to the materialized service prefix. Proof v2
requires the same acknowledged FULL allocation, node generation, ancestry
and materialized prefix coverage; keep native tier accounting unchanged.
All 4202 old V11 negative outcomes cover the materialized prefix and service
ancestry, but allocation identity cannot be fully recovered from those logs.
Keep the originals, cold-start V13, and do not reinterpret the old counts
as verified waste or as verified reuse.
V13 is the next collection. Freeze the corrected telemetry with the existing
FULL-only PREPARE and scheduling revisions; retain the workload, model,
prediction artifacts, capacity, prompt and arrival schedule.
V13 started from aa93dde. The follow-up offline audit retains native operations
inside mixed tagged batches, reports per-pool overlap and reuse proof versions,
and separates reconciled operation receipts from legacy batch-pool associations.
Pure native batches without receipts remain ambiguous; a later node/pool load
does not establish repeated allocation or duplicate bytes. Keep live V13 frozen.

The follow-up limits tool H2D candidate scans to the existing 100ms observation
cadence. New accepted forecasts and completed action tickets request an immediate
rescan; in-flight ACK handling and ticket invalidation remain per iteration.
Live native validation still precedes enqueue. The original 125 checks and two
timing/refresh cases pass. In a CPU-only fixture with 156 long tool waits and
5000 calls at 2ms ticks, candidate checks fall from 780000 to 15600; cumulative
CPU time falls from 14.136s to 0.297s (97.90%). This does not establish GPU benefit.
Report: experiments/reports/tool_candidate_scan_cpu_156_20261009.json.
Commit in the follow-up worktree; deploy only after V13's frozen driver exits.

The live V13 contains a concrete ACK -> native residency loss -> demand reload
chain: three extents of the same request lose residency 76--102ms after ACK.
Their leases are unlocked; an approximately 367MB ancestor prefix exceeds the
free-list-only lock budget. This is actual residency loss, not just proof-v2
metadata ambiguity. Legacy pure-native batches do not establish exact duplicate
bytes.
The follow-up uses native free+evictable capacity only to bound locks on already
restored demand-handoff data, after retaining input/decode/Mamba reserves.
Speculative JOIN/tool budgets remain free-list-only, and physical restore still
requires real free allocations or a successful cold reclaim. New handoffs inspect
only the next native admission slots (up to 16). Extents of one request share a
slot; each closure remains conservatively charged. A real NO_TOKEN releases that
demand request's handoff locks together so overlapping locks do not obstruct
native fallback. First-service, invalidation and three-second expiry still apply.
Keep V13 frozen and validate the revision in a later cold-start collection.

Separate native child completion, client LLM_RESULT, RETURN, complete JOIN,
parent submission and first service. Match the exact request and runtime
identity; report both command-weighted and unique-child summaries.
The 76-action live V13 snapshot shows a 4788.94ms median native-to-client
result delay and 1026.04ms result-to-RETURN delay. These intervals do not
identify a function-level cause or establish prediction-head error.
Report: experiments/reports/v13_join_completion_pipeline_partial_20261009_2320.json.
Deliver LLM_RESULT through the existing ordered asynchronous callback path.
Keep workflow-end delivery checking and measurement degradation on transport
failure, including mixed root RETURN/WORKFLOW_END batches that previously
matched the asynchronous RETURN condition. Record result queue/ACK timings separately. This removes callback
ACK waiting; enable the existing HTTP-stream and finish-chunk diagnostics in
the next cold-start run to investigate earlier stream-consumption delay.
Keep the live V13 code frozen.

Coalesce redundant tool-stream semantic frames: send the first tool negative
immediately, retain changed body observations and the finish chunk, and skip
pure argument chunks with unchanged body. A live snapshot has 63072 identical
tool frames among 131187 semantic frames, not a measured GPU cost estimate.
Retain the tool-negative request identity until its normal cleanup; late body
frames must not resurrect that request's RETURN prediction. Agent/tool execution
is unchanged. The next launcher enables existing HTTP-stream and finish-chunk
timers for both policies, without changing the live V13 configuration.
The coalescing/runtime/ordered-delivery group passes 155 CPU checks and shell
syntax validation. The prior result-delivery group passes 65 checks. GPU
performance remains to be measured after deploying a frozen cold-start revision.

Use H2D source audit schema v2: predictive_* contains JOIN/tool anticipation
only; execution_handoff_* is submitted demand restoration; unknown_controlled_*
holds tagged payload without a known source; native_* is the untagged remainder.
controlled_* provides the total controlled payload. Match issued command source
with ACK fallback for older logs. Conserve child-receipt bytes and pool units;
keep mixed-source batch intervals intact instead of apportioning time by bytes.
Forty focused CPU checks pass, including downstream memory-budget classification.
The live partial V13 snapshot has JOIN/tool 9.927GB, handoff134.073GB,
native3486.746GB and 16.466MB unclassified tagged payload. This is not terminal
throughput evidence. Report: experiments/reports/v13_h2d_sources_partial_20261009_2356.json.
Keep the frozen collection/report unchanged; apply the audit in the follow-up.

Service-completion telemetry now indexes the current batch by request_id once
instead of searching the batch separately for every sample. Preserve request
filtering/reordering and output-token attribution. The CPU fixture compares full
service records and token maps, using 16/48 requests with and without removals.
At 48 requests the median interval falls 116.04 -> 43.04us; with removals,
102.39 -> 33.53us. Fifty-two existing telemetry/timeline/restore-probe checks pass.
Report: experiments/reports/native_service_lookup_cpu_48_20261010.json.
The fixture excludes writer, DMA and GPU, and does not establish throughput.
Deploy only after the frozen V13 driver exits.

FULL-only PREPARE reclaim correction:
- Publish exclusive, unlocked FULL-backed leaves even if their resident Mamba
  lacks a Host copy. Other resident components still require valid backups.
- At actual allocator shortfall, handoff and waiting-parent FULL reclamation
  save a live unbacked Mamba through native write-back, then recheck identity,
  FULL Host copy, references, locks and DMA after ACK before demotion.
- Do not save unreferenced Mamba or restore speculative Mamba PREPARE. Declined
  state preservation leaves useful data resident and retains native fallback.
- Runtime checks: 150 passed. Engine checks: 71 passed plus 16 subtests. This
  proves candidate/reclaim behavior, not recovered-opportunity count or GPU gain.
- Keep V13 frozen; deploy with the other follow-up commits after its driver exits.

Restore-ready admission with an aged head:
- Preserve ordinary age ordering but remove the permanent veto after a head
  has waited ten seconds. Allow one submitted, demand-ready restore only after
  four ordinary admissions when an aged head is present.
- Keep the existing dynamic quota for unaged heads, native capacity checks and
  actual residency checks. Failed admission does not spend the ordinary quota.
- Record bounded aged-head bypass attempts and admissions separately.
- 191 related CPU checks passed, including three consecutive 4:1 admission
  cycles and rejected admission. Validate service/reload reduction and costs
  to other workflows in the next cold start; do not claim a GPU improvement yet.

Lifecycle source reporting:
- Separate JOIN/tool anticipation from submitted-demand execution handoff.
  Count FULL-bearing commands and use reconciled physical ACK pool bytes for
  FULL transferred/reused bytes. Report absent legacy pool-byte evidence.
- 17 related audit checks passed. The V13 partial JOIN/tool/handoff
  ACK-to-service P50 is 7861.71/3220.14/815.37ms; the aggregate1047.01ms
  masks JOIN waiting. Verified FULL bytes are0.424/0.880GB for JOIN and
  2.695/3.833GB for tool; these are proof-v2 node-command evidence.
- Report: experiments/reports/v13_prefetch_sources_partial_20261010_0034.json.
  Preserve frozen-driver reports and regenerate follow-up versions separately.

PREPARE checkpoint budget:
- The partial V13 has 9723/13435 PREPARE commands of at most64 FULL tokens.
  A shared one-token node has7471 re-backups and no observed restore association.
  Native write-back prioritizes reclaiming redundant Host FULL copies; these
  observations do not prove overwrite of a still-valid Host copy.
- Require free Host space for all currently missing FULL extents on the
  reusable checkpoint paths, rather than only the next ancestor. Count existing
  Host copies and generated output after the checkpoint as zero extra budget.
- Still submit one missing extent through the existing native path. This is a
  read-only selection budget, not an atomic Host reservation or residency promise.
  Record required checkpoint tokens and capacity rejections separately.
- 214 related CPU checks pass. Evaluate fewer repeated node commands, real
  consumption, CPU cost and any lost useful opportunities in the next cold start.
  Keep V13 frozen until its driver exits.
  Subsequent frozen plans record the complete-missing-prefix budget, bounded
  restore-ready admission and enabled HTTP/finish-chunk timers.

Completed-run comparison source correction:
- Reuse the existing H2D source audit and preloaded ACK index. Add
  `h2d_sources` and per-band `h2d_source_*` counters for JOIN/tool anticipation,
  submitted-demand handoff, unknown controlled payload and native remainder.
- Preserve legacy action-name `transfers`; its PREFETCH_GPU total is not
  predictive coverage. Receipt bytes and pool units partition a physical batch,
  while category batch counts may overlap. Native milestones use the receipt
  remainder. Do not apportion batch time by byte ratio.
- 26 related CPU checks pass. Recomputed complete V10 native evidence retains
  3202.074GB native H2D and zero anticipation/handoff bytes; report:
  experiments/reports/v10_native_source_comparison_20261010.json.
- V13 django-12273 is incomplete after root output reaches8192 tokens with
  finish_reason=length, repeated analysis and no completion declaration.
  Both children returned and JOIN satisfied; no workflow deadline fired.
  Preserve the outcome and evidence without adding a new guard.
- Keep the entire frozen V13 driver unchanged until its exports and cleanup
  finish. Deploy these follow-up revisions with the prepared engine delta.

## Current PREPARE Cost And Handoff Fix

Implemented in `/tmp/beliefkv-opportunity-20261009`, based on `e985d8c`.
V10 collection, exports and its driver have finished. Use the revision for
subsequent runs; v10 results still describe the frozen e985d8c arm.

The final v10 state has 1480.695 s exclusive JOIN PREPARE CPU wall time and
338.871 s opportunity sampling, plus 9419 selected handoffs and zero handoff
issues. These intervals are not GPU time or throughput. Final evidence is
the v10 comparison.json/native_policy_comparison.json; retain the earlier
`experiments/reports/qwen35_v10_predictive_hotpath_partial_20261009.json`.

Completed:
- Normalize native numpy.float64 node timestamps at the request-reentry
  boundary, as session snapshots already do. Preserve numeric identity
  and native generation/session checks. A real CPU observer/planner fixture
  with Host-only input reproduces old selected=1/issued=0 and new 1/1;
  enqueue is stubbed, so it grants no DMA, ACK or reuse credit.
- Refresh leases once per pressure-candidate maintenance pass, validate
  repeated context identities once, and retain live native eviction checks.
- Reuse a freshly captured closure to register backed nodes and calculate
  ancestry depths/prefix lengths without repeated node-to-root walks.
- Reuse the H2D observation's closure for D2H opportunity sampling. Actual
  actions still refresh physical evidence; no cross-cycle residency cache.
- Record specific handoff no-step diagnostics and nested maintenance,
  publication and backed-registration timing.
- Clear terminal cache watches during close and stop sampling when the
  opportunity writer has closed. Cover an additional scheduler iteration,
  repeated close and drained final evidence in CPU regression (89 passed).
  V10's shutdown-only AttributeError followed all workflow completions;
  the client and driver exited normally. Preserve the original exception.

CPU checks: 358 passed. In 156-workflow/24-node/16-history-round fixtures,
30 measurements against e985d8c preserve selected backups, published pressure
nodes and sampled targets. JOIN PREPARE means are 26.305 -> 15.075 ms when
backed, 176.056 -> 12.658 ms with four live leases. Unbacked/Host-full cases
reduce 3.74%/4.17%; sampling reduces 30.07%-35.76%. Native enqueue is stubbed.
At depth 64, backed/no-lease and four-lease PREPARE reduce 48.25%/93.54%;
unbacked/Host-full reduce 11.30%/11.41%, sampling reduces 36.23%-43.27%.
Reports: `experiments/reports/prepare_path_cpu_156_{24,64}_20261009.json`.

Next measured checks remain useful FULL reuse, exposed native restoration
wait, ACK-to-service, repeated demand loads and completed-workflow throughput
relative to native. Do not claim GPU benefit from these CPU results.
The finished v10 uses e985d8c and cannot validate the new fix.

V10 final: predictive 156 completed, native 154 completed/2 incomplete.
Completed throughput is 46.054 vs 53.589 workflows/hour (-14.06%),
GPU utilization 63.681% vs 73.629%, output volume +2.06%. This is a
cross-revision single live pair, not isolated policy attribution.
PREPARE is 35713 operations/144.536 GB but the waiting-agent pressure
demotion path has only 60 events (59 linked to a prior PREPARE ACK).
That path is not complete backup-consumption accounting. Predictive H2D
is 106 operations/7.374 GB, including 1.321 GB FULL and 0.720 GB confirmed
FULL first-service reuse. ACK-to-service P50 is 3.245 s; 31 targets undergo
demand loading before first service. Prioritize actual handoff consumption,
unused-backup accounting, ready-restore admission and repeated restoration.
Both native layer-dependency wait probes average roughly 0.375/0.377 ms;
sampled waits cannot be treated as all restoration wait or an oracle bound.

## Opportunity-Aware Transfer Revision

The remaining runtime optimizations are implemented in
`perf/opportunity-aware-transfer`, based on `859d138`. Preserve model weights,
phase thresholds, harness and prompt. Runtime chooses transfer actions from
forecast and observed capacity; the offline predictor need not learn net benefit.

Completed:
- Budget ACK locks from native running count, request-pool rows and next-prefill
  limits. Reserve decode page growth, input space and new Mamba slots first.
  A full decode batch allows at most one frontier restore, without overriding
  native admission. Missing capacity observations retain four locks/one GiB.
- Promote ready restores after one to four ordinary admissions according to
  next-batch demand, retaining ten-second aging. On real NO_TOKEN, release an
  actual speculative lock before a ready restore or the current request's lock.
- Rotate through at most eight JOIN/tool PREPARE candidates. Rank pressured-pool
  relief before copied bytes, respect Host free lists and observed D2H timing.
  Register already backed FULL-only checkpoints for idle duplicate reclamation.
- Derive latest-start from measured H2D submit-to-ACK P90, enqueue-to-submit P90
  and 100 ms observation spacing, within the frozen 500 ms lead cap. Pre-EOS JOIN
  decisions require recent child service; do not extrapolate descheduled progress.
- When generated work overtakes the predicted center, use a still-live upper
  bound or wait for a new forecast/EOS. Log actual progress and the effective
  statistic. Do not interpret endpoint overrun as one remaining token.
- Fix the timeline renderer's arm path and resume only the unstarted predictive
  arm, retaining native artifacts and all non-revision frozen configuration.

Evidence: related regression 290 passed/1 skipped. No engine patch change.
Synthetic 156-workflow/16-round admission comparison with `859d138` is
3.597 -> 3.602 ms (+0.13%), with identical queue order. This fixture has no
active physical transfers/locks; it is not whole-path cost or GPU throughput.
Report: `experiments/reports/opportunity_policy_cpu_156_16_20261009.json`.

Historical v8d has 15 work-based trigger snapshots, RETURN lead P50 9.472 s,
EOS/RETURN signed error P50 -5.047/-9.094 s. None overtook the center/upper;
the endpoint-clamp bug is not a demonstrated cause of those early triggers.
The adaptive policy would wait at 13/15 original snapshots, without proving
later trigger accuracy, reuse or speedup. ACK-to-service P50 is 8.167 s;
71 FULL uses are confirmed and historical leases held no actual native locks.
Report: `experiments/reports/native_transfer_policy_v8d_20261009.json`.

V10 native is finished: 154 completed/2 incomplete, 10345.487 s window.
Its parent driver previously failed at the HTML export path before predictive
started. Native HTML is now generated; archived completed workspaces were
cleaned and `scripts/resume_semantic_h2d_ab.py` resumed the existing v10
directory. The predictive arm finished on e985d8c after the callback fix
below. Preserve both frozen arm revisions and the original plan.
Do not repeat native or rewrite the completed collection's model/configuration.
Evaluate throughput/JCT, exposed waits, reuse, repeated loads and residency
costs. Further remaining-work-head tuning depends on new measured errors;
subsecond RETURN accuracy and GPU benefit are not yet established.

The first `04812ec` predictive start exposed a Mamba D2H callback failure:
`shadow_expectation_from_native_op` referenced `pool_name`, scoped inside the
H2D function. Native rejected these backups and repeatedly logged NameError
while FULL-only backups still completed and `physical_disabled` stayed false.
Stop and retain that failed start; it is not a performance arm. Use D2H-local
enum/string pool-name normalization, cover real Mamba `pool_transfers` and
Host destination identity in the existing CPU fixture; 145 related CPU checks
passed. The fixed e985d8c predictive arm was cold-started and has completed
with driver exit code 0. Old arm_status.txt entries belong to the failed start;
inspect current processes and output rather than treating that file as
the current status. Do not repeat collected native.

## Pipelined Handoff Revision

Implemented in the isolated `perf/pipeline-execution-handoff` branch based
on `e5f1f0b`, implementation commit `e5751e6`. The latest user instruction
supersedes the same-revision pair freeze: keep native unchanged until its
wrapper finishes, then deploy the latest committed revision for predictive.
Do not repeat native. Keep tasks, arrivals, pools and model artifacts fixed.
Archive the native plan and report per-arm code and engine patch revisions.
This is a cross-revision development comparison; GPU benefit is pending.

- Plan up to 16 root-first missing FULL extents, enqueue exact independent
  commands and submit the burst once. Transfer state only at the reusable
  input checkpoint. Use a fitting prefix when current free capacity is partial.
- Admit FULL through native layer load fences instead of a software ledger
  ACK barrier. Pending Mamba must complete its own transfer event before
  deferred COW. Legacy adapters keep their completion wait.
- Keep the 50 ms backoff for new-beneficiary selection, advance a live ticket
  immediately after its ACK, and do not let unrelated native ACKs cause scans.
- Share causal classification within a scheduler cycle, invalidated by graph,
  semantic revision and ordered request identities. Recompute residency,
  aging, promotion budgets and hint expiry. Deduplicate identical component
  leaf observations within one physical capture; native enqueue revalidates.
- Register the deepest ACK lock before ancestors, sharing its FULL closure
  protection. This revision used four locks/one GiB; the current native-capacity
  budget is described above.
- Capture native allocation identities at issuance, buffer service observed
  before software ACK, and publish reuse only after matching verified credit.
  Do not acquire a late residency lock after service. Discard provisional
  evidence on expiry, mirror loss or physical failure.

CPU comparison against `e5f1f0b`, 156 synthetic workflows with 16 retained
rounds and 80 measurements: two consecutive admission plans average
5.591 -> 5.180 ms, 7.35% lower with identical order. This excludes physical
inspection/enqueue and is not a GPU throughput result. Evidence:
`experiments/reports/pipeline_handoff_cpu_156_16_20261009.json`.
Related repository regression: 319 passed; independent engine: 104 passed
and 16 subtests. Use the committed canonical staging patch for deployment.

`scripts/resume_predictive_after_native.py` waits for the native wrapper
to finish while only its parent pair driver is stopped. It verifies the
original plan, fast-forwards the repository, applies the exact engine delta,
records both revisions, and resumes the existing driver. It adds no agent
guard, canary, model authorization or additional experiment.

Evaluate the latest predictive revision against the current native arm. Assess
completed-workflow throughput and JCT together with exposed restoration
wait, useful FULL reuse, repeated demand loads, recompute and resident byte-time.
Do not refit the predictor, add canaries or enlarge transfers to count success.
Dynamic next-batch budgets, PREPARE candidate refinement and service-aware
trigger selection are now implemented above. Further remaining-work model
tuning and GPU benefit remain unverified. Do not substitute CPU results for
the GPU comparison.

## Shared-Path Cost And Matched Replenishment

The user now authorizes shared-path cost reduction followed by one live
native/predictive pair on a replenished workload. No canary, replay or
development repetitions. Preserve natural-language RETURN and current guards.

Implemented:
- Classify only bounded queued requests, cache JOIN waiter/tool counts per
  graph version, and skip lease identity parsing when no leases exist.
- Aggregate inclusive/exclusive Python wall timings without per-call I/O
  or CUDA synchronization. Keep the timers enabled for the predictive arm.
- Dispatch native no-reclaim, fenced cache-mode H2D without draining
  unrelated overlap decode. Legacy adapters retain their safe point.
- Preserve actual ACK residency locks, restore-ready admission, latest
  state and missing FULL extents; assess reuse and repeated loads, not
  speculative byte volume.
- Sample one in 16 load-consuming prefills in BOTH arms with compute-stream
  CUDA events around native per-layer dependencies. Read only completed
  events; skip graph capture and bound outstanding samples. Event overhead
  remains in measurements; these are batch stalls, not per-request queue
  delays or an end-to-end oracle bound.
- Automatically render each completed arm's HTML and a native-policy
  comparison; use correct native baseline labels and a frozen arrival table.

CPU check against `7055901`: 108 synthetic workflows with two retained
rounds, 80 iterations, admission mean 10.722 -> 1.365 ms (87.3% less),
profiled 1.387 ms; identical queue order. The history-heavy 156/16-round
case is 120.137 -> 2.522 ms. Neither is a GPU performance result.
Verification: 257 repository tests, 99 patched-engine tests and 16 subtests.

Freeze and collect: 108 roots at t=0 plus 48 disjoint train Django tasks
at fixed t=3600 s, native first then predictive, fresh server/cache per arm.
Manifest:
`configs/migration/qwen35_native_predictive_replenished_108plus48_2026-10-09.json`.
Keep running48, Host200 GB/80:20, Device Mamba/FULL0.9, context131072,
completion8192, graph2048/reserve32, workflow14400 s and tool600 s.
Replenishment is intentionally not IID: report second-wave composition.
Commit before collection and verify code/artifact fingerprints before
each arm. Report fixed 0-3600, 3600-7200 and remaining windows plus all
arrival completion/JCT, demand differences, recompute, useful FULL reuse,
resident byte-time, repeated restoration and sampled exposed GPU waits.
Do not substitute historical Host-auto v9 for this matched native run.
Only remove completed, archived workspaces between arms; retain trace,
model patches and failure evidence. A failure to beat native is a failure
of the current policy on this workload, not a migration-count success.
GPU throughput benefit is pending; this is one development pair.

## Native Comparison And Arrival Proposal

Retrospective comparison completed with
`scripts/compare_native_policy_runs.py`; seven aggregation tests passed.
Report: `experiments/reports/qwen35_v8_v9_policy_comparison_20261009.json`.
The first 108 tasks and physical pool capacities match across v8c, v8d and v9.
Historical Host is auto (105.358/94.652 GB), not the new 80:20 setting.

V8c/v8d/v9 completion throughput is 40.185/41.002/48.073 workflows/hour;
output throughput is 546.680/619.778/713.107 tokens/s. V8d loses 14.71%
completion throughput to native although output work differs by only 0.96%.
Its completed mean JCT is only 2.83% worse and P50 is 2.30% better; do not
claim that every workflow is slower. V8c's physical failure and harness/
policy changes make these live runs diagnostic, not a controlled speedup.

Measured priorities for the next performance evaluation:
1. Verify that selective latest-state restoration, actual ACK locks and
   restore-ready/resident-first admission convert missing FULL extents into
   first-service reuse and reduce repeated native restores. These fixes
   have CPU verification but no measured GPU benefit yet.
2. Separate reentry/control and prefill scheduler/worker costs. V8d/native
   tool-end to next-submit P50 is 157.991/93.003 ms; prefill intervals total
   1743.657/1226.401 s. In the 30-40 minute band decode batches are both
   about 47, yet GPU utilization differs by 8.72 percentage points.
   Existing timings cannot attribute the difference to one CPU function or
   distinguish all synchronization/worker costs; do not invent that breakdown.
3. Evaluate exposed restoration stalls rather than H2D volume alone.
   Native transfer-stream H2D totals 88.625 s and submit-to-ACK totals
   731.751 s, both overlapping service and neither an oracle JCT bound.

Native H2D is 98.820% finished by 50 minutes and 99.779% by 60 minutes,
while 75/62 workflows and 53/40 unfinished JOINs remain. Waiting queue mean
falls from 63.213 at 30-40 minutes to 2.732 at 50-60 minutes and 0.539 at
60-70 minutes. FULL old-prefix loss proxies remain about 0.14%-0.15%;
this is not a complete FULL/Mamba recompute bound or Device occupancy census.

Initially proposed and now authorized above: 108 arrivals at t=0 and 48
new train tasks at fixed t=3600 s, with 64 as a higher-pressure alternative.
This can renew the working set but cannot fix ineffective preload, remove
runtime overhead or guarantee throughput gain; native benefits from renewed
load too. The new 80:20 Host ratio requires fresh capacity/pressure evidence.
All policies must share a predeclared task/arrival table and measurement
windows plus full-drain outcomes, not policy-dependent completion triggers.
The existing 128-task manifest has only 20 unused tasks after the first 108.
The expanded disjoint train manifest and explicit runner support are now
implemented. Do not duplicate tasks or reuse the old 64+64 launcher constraint.

## Current Handoff And Host Configuration

Native v9 and its offline HTML export have completed: 108/108 workflows,
8087.675 s collection window. The isolated latest-state/restore-ready patch
has now been integrated and incrementally applied to the active checkout.
The frozen run was not hot-edited; the native HTML renderer is restored.

Implemented for the next revision:
- Keep only this context's latest reusable Mamba input-checkpoint session
  reference. Release older references through native ownership hooks;
  shared, locked and in-flight physical states remain native-managed.
- Transfer missing FULL ancestors without historical Mamba. Include state
  only at the selected safe checkpoint, before allocation and commit.
  Native demand restore and backup defaults keep their necessary state.
- Suppress duplicate in-flight restores of the same context/epoch/checkpoint.
- Bridge actual tool completion or satisfied ALL JOIN to submission with
  at most 3 s on an unexpired native lock; a matching submitted request
  retains the ACK+10 s ceiling. No expiry revival or ETA-driven extension.
- Select restore-ready requests across at most 512 candidates; restore and
  final-stage promotion share one per four ordinary admissions. Aged
  requests waiting 10 s retain their turn across causal classes.
- Make queued TOOL_END/RETURN/JOIN_SATISFIED/LLM_SUBMIT callbacks nonblocking
  while retaining FIFO delivery and explicit flush. Session retirement
  RPC remains synchronous. Record ready-to-submit and ACK-to-service phases.

Added for the next experiment:
- Default Host FULL:Mamba=80:20 in the probe, collection runner and launcher.
  Explicit `auto` still follows Device proportions. Device bytes=0.9 is unchanged.
- Resident-first ordering within causal classes, after ordinary aging and
  before existing bounded restore/final promotions. Both A/B sides share it.
- Predictive execution handoff before native batch admission. Choose one
  submitted, executable beneficiary; compare its actual input to live session
  checkpoints read-only, then load missing FULL extents/current required state.
  No offline net-benefit action head is required for an observed queued request.
- Reclaim only settled, backed, unlocked idle duplicates during a real load
  shortfall; do not evict running/hot or shared live state. Recheck real free
  capacity after native release, never credit an unfinished D2H.
- Allow independent pending D2H and one H2D burst within the ledger budget,
  so directions can overlap on distinct nodes. Keep the native allocator/ACK
  as physical authority; never credit capacity from an unfinished transfer.
- Bound each request's handoff planning to 2 s and 16 nodes, with no repeated
  ticket after expiry. FULL uses native layer dependencies; unavailable state
  or legacy completion waits affect only the loading request. Other work continues.
  ACK locks now share the capacity budget described above; queued restores
  hold at most 3 s.
- Record `execution_handoff` separately from pre-RETURN/pre-TOOL_END loads,
  including selected request, checkpoint, freed victim units, issue and ACK.

No predictor refit or new GPU performance claim is part of these fixes.
Do not add canaries, agent guards or artificial cache pressure. The single
matched replenishment pair above supersedes the former no-new-run restriction.
Future comparisons must share revised harness/cache/resident-first rules.
Use FULL useful reuse, repeated restores, native demand H2D reduction,
queue/service timing and throughput rather than maximizing preload bytes.
Host 80:20 is user-selected, not a proven optimum; size against live necessary
checkpoints rather than historical backups. At 200 GB it has approximately
621 current-model state slots. Capture a fresh capacity census for this ratio.

The earlier checkpoint/restore-ready regression had 539 tests and 16 subtests.
Current verification: 346 repository tests plus 104 patched-engine tests and
16 subtests passed. The canonical patch applies to the pinned upstream temporary
index and reverse-checks against the active engine; real engine/runtime imports
passed. GPU reuse, transfer reduction and throughput are pending.

## Completed Native-Policy Baseline

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
These are startup observations. Final collection has 108 completed workflows;
its HTML is available. The retrospective throughput/pressure diagnosis is now
at the top of this plan; function-level overhead attribution remains pending.

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

Offline HTML export completed after the frozen experiment.
V8c reactive, v8d predictive and native v9 timelines are in
`experiments/reports/qwen35_v8_timelines_20261008/`, with embedded gzip data
and `.json.gz` sidecars. The renderer now consumes native v0.5.20 service,
transfer and pool telemetry without copying raw traces. The independent
`beliefkv-native-timeline-v9` job waited for PID 423778, then produced
`native_v9_execution_timeline.html` including all terminal outcomes.
It ran from `/tmp/beliefkv-policy-20261009` while collection rechecked
source fingerprints. The watcher is done; the renderer is now restored
in the main checkout.

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
run predictive first in each new pair, retain all failures/censors and show
every pair plus mean and variation. Report the fixed-order limitation.
The unit for whole-run throughput uncertainty is a run
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
