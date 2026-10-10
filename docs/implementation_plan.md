# BeliefKV Current Execution Plan

Status date: 2026-10-11.

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
lifecycle checks pass. This revision is not part of the running frozen V21.

Invalidate only the affected parent's semantic/tool restore observations after
linked terminal-child device release. On ledger-validated completed PREPARE,
invalidate the current matching or next-epoch context observation and rescan its
active tool wait. This removes stale100ms negative observations after physical
progress; it does not guarantee a100ms saving or authorize prefetch. Preserve
unrelated caches and all current page/capacity/time/residency checks. Untagged
native ACKs, invalid receipts, stale sessions, Host-only release and no-capacity
retries do not trigger invalidation. There is no new decode-loop scan. The261
terminal/tool/runtime/handoff checks pass. This revision is now included in the
updated frozened974d2 V22 target; waiting registration is not GPU validation.

Reuse only a tool restore plan freshly read during the current dispatch. Refresh
plans from the short cache or an earlier selection. Preserve the post-event-drain
checkpoint, extent, timing and capacity checks and native per-node reservations.
The 267 scoped runtime/tool/terminal/handoff checks pass, including tool completion
and checkpoint replacement during the drain. Keep
v27_tool_dispatch_cpu_20261011.json: 100 alternating CPU samples retain ordered
bursts and ledger reservations; four/300-node dispatch means change from
0.858/9.117 to 0.683/6.672 ms. The optimized 300-node maximum remains 19.507 ms.
This is a CPU planning result, not GPU throughput or end-to-end savings. Register
the committed change for the next pair while keeping the running V21 frozen.

Keep v21_busy_window_same_version_evidence_20261011.json with the original
comparison SHA256. Both V21 arms completely observe 600–900 s: predictive/native
decode throughput is 891.68/1165.71 token/s, GPU utilization 69.26%/87.60% and
time-weighted decode batch 47.18/47.36. Predictive is 23.51% slower in this window.
The mean worker decode intervals 44.19/35.50 ms are not CUDA kernel timings.
Capacity handoff associations do not establish service performance improvement,
and similar batch sizes do not explain the measured gap. Native collection is
still running; do not label the window a final JCT or causal speedup. Validate
queued live-pool geometry, complete-prefix coverage and control reductions with
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

Schedule this committed revision together with the capacity-settlement retry in
V22, replacing only the still-waiting launch registration while preserving its
superseded state. Keep the live V21 pair frozen. After its complete artifacts,
deploy exactly this revision and patch, run predictive first and cold native
second, and measure actual early FULL reuse, parent service wait, protection
reasons and busy-window throughput. The broad performance objective stays active.

The validated deep-prefix observation reduction is registered in frozen V22
revision ed974d2, preserving superseded registrations and receipts. Within one closure
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

The waiting coordinator was updated at2026-10-11 04:47 CST; PID3439480 is
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
incomplete in11375.87 seconds; same-version cold native is collecting.
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
and is now included in the updated registereded974d2 V22 checkout.

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
