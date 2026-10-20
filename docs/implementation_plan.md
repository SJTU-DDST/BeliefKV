# BeliefKV Current Execution Plan

Status date: 2026-10-21 (Asia/Shanghai).

Research-direction exploration is paused at the user's request. Keep the
historical evidence and reusable auditor fixes; do not resume candidate
searches or GPU experiments from old research instructions. One-off sources
and duplicate summaries were removed after a verified archive was created.
See [the consolidation record](archive/research_exploration_20261020.md).
Current engineering contracts below retain precedence.

Development checkout: `/home/longhao/experiment/BeliefKV`, branch `main`.
Make subsequent code changes, validation and commits in this checkout.

Latest qualification is V2355. Fix inherited cold FULL owner coverage before
the next GPU run: FULL session_ref includes descendant references, while
session_ids names direct session leaves only. After demoting a backed suffix,
the old direct-owner-only guard could leave its backed ancestor resident despite
a real capacity shortfall. Resolve complete current ownership through at most
64 subtree nodes, counting zero-reference children against the same budget.
Verify parent/node identity, reference totals, native session-leaf membership,
current generations and waiting-owner authorization. Retain the direct-leaf
fast path and the no-work path when capacity already fits. No global owner
index or per-decode polling is added.

Revalidate before each demotion. Keep Mamba, session references, native locks,
pending transfers and settled Host-copy requirements intact. Emit the actual
owner generations with causal FULL victims; runtime uses the already validated
owner index to invalidate only their stale empty restore observations.

V2350/V2352 use real CPU radix/session/allocator objects: old engine frees
2 of 4 requested FULL units; candidate frees all 4 in single/shared-owner cases.
Combined native/runtime callers invalidate 0 owners with the old runtime and
1/2 with the candidate. Mamba and references remain unchanged, with no D2H.
Target restoration is stubbed; this proves neither online frequency nor GiB,
H2D, throughput or JCT benefit. Check bounded-walk CPU cost and opportunities
declined beyond 64 nodes in the next trace; do not attribute all historical
low prefetch coverage to this limitation.

V2355 reruns all 2494 prior application and 266 prior native cases, adding
7/18 cases: 2501 application and 284 native cases pass. Native validation is
an actual rerun, not the historical hash-only retention described below.
Canonical engine tree is 8a51dfa7b90d600f29439807c8d676c9db12319c.
Refresh V103 from this qualification, preserve the existing watcher and the
2026-10-21T06:00:00+08:00 GPU hold, and keep performance acceptance open.

Latest JOIN source check reuses only the exact ticket already validated by the
pending-client lookup in this same call. A distinct pending object still needs
selected-ticket validation. Keep each node's fresh source check,map/context
membership,epoch,terminal/expiry handling and pending ACK ownership. Ordinary
JOINs retain the complete ticket check;tools retain context/hint guards. No new
cache,timer,configuration or native API.

V2346 profiles qualified dispatch;V2347 replaces only the source guard in9
equal-state CPU cases. Client-bound34/64-node ticket checks fall75->40 and
135->70;complete application dispatch P50 falls1.174->1.071ms and
2.001->1.830ms. Targets,full expectations,bytes and budgets are identical.
Tools/unbound JOINs are essentially unchanged. Retain the64-node candidate
max3.633ms versus2.097ms baseline and all slow samples;no worst-case claim.
Real native ancestry validation,production issuance telemetry,GPU DMA and
online population are not measured. V2349 reruns all2462 prior application
cases plus32 new cases,2494 passing;rehash and retain266 unchanged-engine
cases. V2355 supersedes this historical qualification; preserve the GPU hold.

Latest empty-target change also restricts FULL to the deepest input-compatible
checkpoint and its ancestors on each FULL path. A Host-only output tail alone
cannot trigger detailed recovery planning or a confirmed ticket. Unknown input
limits retain the old conservative behavior;unobservable geometry falls back
to complete planning. No cross-round cache,data discard,ACK or budget change.

V2342 compares15 complete CPU inspection cases with identical restore steps/
units. Output-only32/64-node P50 falls264->54us and452->79us;resident costs
are unchanged. Keep the positive-path regressions:64-node FULL leaf363->407us,
FULL ancestor496->514us and Mamba-only398->405us. Empty A plus actionable EOS B
changes2 planner calls to B only,extra EOS attempts1->0 and keeps1 burst/4 CPU
reservations. This does not establish online population,total CPU or GPU/JCT
benefit. V2344 reruns all2437 preceding cases plus25 new cases,2462 passing;
rehash and retain266 unchanged-engine cases. This policy is retained by V2355;retain
the2026-10-21T06:00:00+08:00 GPU hold.
Preserve the original V2343 run with2461 passing/1 failing. V2345 reproduces
the failed native-check assertion by advancing only the warm hint's50ms TTL.
Use the existing residency_clock in the12-case affected test,with identical
assertions/identities and49/51ms expiry coverage retained. V2343a is the final
source-bound full application run;no production admission timing is changed.

Latest opportunity-conversion fix invalidates only a tracked donor's wait-transfer
observation after the native parked callback reports actual freed KV. The old100ms
empty restore cache and scan deadline could hide newly Host-only data. Reuse the
existing invalidator to clear that owner's tool/semantic observations and rearm a
matching tool hint. Preserve unknown owners,new generations,zero-release events,
transfer/capacity/admission/lifecycle checks,and the existing TTL/cadence.
No new timer,global invalidation,forecast cache or recovery ticket is introduced.
The native shared `causal_waiting_full` demotion bypasses the tracked-owner
callback. Cover its regular-prefetch,tail-capacity and critical-parent callers
after actual FULL release. Reuse the session index already validated during
that native transaction; inspect only its victim nodes,require current Host-only
identity,and deduplicate matching current owners. Decline stale index sources/
revisions,closed sessions,generation changes and replaced contexts without a
global rebuild or per-decode maintenance.
V2336 reproduces old0 versus new1 CPU burst/reservation while90ms of the previous
empty-cache TTL remains,both with a due and not-yet-due scan. This is a control
proof,not GPU DMA or online performance evidence. V2338 reruns all2404 preceding
application cases plus11 focused cases,2415 passing. V2339 compares the old and
new critical-reclaim callers on the shared path with90ms remaining:both reclaim
once and build the owner index once,but the old caller issues0 CPU bursts and
the new caller1 burst/1 reservation. Both due/not-yet-due scans are covered;
this is not online frequency,GPU timing or performance proof.
V2341 reruns all2415 preceding cases plus22 new cases,2437 passing;rehash and
retain266 unchanged-engine cases. This policy is retained by V2355;keep the hold.

Tool timing evidence: V2335 separates10114 matched forecast records from6210
independent declared tool waits.3880 first-forecast-late calls had only8.21ms
median duration. For2320 non-censored calls with pre-end forecasts,last-pre-end
absolute P50-error median is10ms and P90 is166ms. Neither subset establishes
overall accuracy or server delivery order. V2334's659 matched replay candidates
do not establish production cost or complete request-generation identity;do not
add fine-grained evidence-version caches on that basis.

Latest ACK control change reuses the verified ancestry of a successfully acquired
native lock only within one synchronous multi-node ACK callback. Keep deepest-first
registration, all per-node receipt/first-service tracking and live target identity,
generation, residency and lifecycle checks. Do not reuse protection across callbacks,
assume sibling coverage, or create a proof when budget/locking fails.

V2331 compares complete CPU ACK callbacks on equal ledger/allocator-shaped fixtures:
34-node parent reads596->35 and P50 1.239->1.040ms;64-node configured-bound reads
2081->65 and P50 2.584->1.932ms. Single-node cost is unchanged and two-node improvement
is small. V101 had127 tagged H2D ACK callbacks,54 multi-node,max34. Retain all slow
samples and distinguish this low-frequency optimization from online total CPU or
throughput/JCT proof. V2333 reruns all2392 previous application cases plus12 new
isolation/lifecycle/branch/budget cases,2404 passing. Rehash and retain266 unchanged
engine cases. This policy is retained by V2355;keep the GPU hold.

Latest bounded control change scopes restore-protection validation to WAIT_JOIN
inside cold-page eligibility. The old eager computation served no decision for
WAIT_TOOL or rejected states. Tool waits still use live donor demand, hint,
session and terminal checks; keep the previous no-beneficiary early return.
No qualification cache, cadence change or native/ACK relaxation is introduced.

V2327a profiles current native-index-shaped PREPARE. V2328 compares only the
changed guard on identical fixtures: live-demand checks per round57->39 for36
waiting contexts and165->111 for108; with a real beneficiary, terminal checks
110->92 and326->272. Final pool prefixes and24 native node reads per round are
equal. Positive-demand post-forward P50 falls333->303us and772->677us.
No-demand improvements are smaller; retain the36-context candidate max803us
versus340us, all slow samples, and the unrelated older V2320a results unchanged.
Context count is not workload root count. This does not prove worst-case,
online total CPU or throughput/JCT improvement. V2330 reruns all2392 existing
application CPU cases unchanged; rehash and retain266 unchanged-engine cases.
This policy is retained by V2355; keep the2026-10-21T06:00:00+08:00 hold.

Latest confirmed-JOIN eligibility fix: check for recoverable Host-only FULL or
the necessary input-compatible Mamba checkpoint before creating a new recovery
ticket. A proven-empty restore does not enter selection; missing/unobservable
anchors and capacity blockage do not establish emptiness. Share the negative
check within the same RETURN/JOIN_SATISFIED batch. Keep already-issued bursts,
their ACK identity, consumer binding and residency management.

An empty JOIN retains only its one-use reentry identity, with no capacity,
selector participation or polling. At the next client LLM_SUBMIT, recheck live
native residency and allow recovery if eviction occurred after RETURN. Consume
or invalidate that identity through existing lifecycle events; do not let an old
satisfied JOIN authorize later unrelated requests. Do not cache native emptiness.
The earlier bounded EOS fallback remains for unknown/changed plans, capacity
blockage and declined enqueue, rather than known-empty ticket creation.

V2325 compares the previous3d164e3 policy on identical CPU fixtures: empty A
plus actionable EOS B changes two planner calls to B only, with extra EOS retries
1->0 and the same1 burst/4 CPU action reservations. Positive FULL and unknown
cases retain selection. The extra boundary check is not free: empty-case P50
426->418us but mean474->478us; positive-FULL P50 341->374us. All slow samples
are retained. Timing includes mocked planner callbacks, so it cannot prove
full-planner cost, online frequency or throughput/JCT. V2326 reruns all2367
preceding application cases and adds25 eligibility/lifecycle cases,2392 passing
in total. Rehash and retain266 unchanged-engine cases. This policy is retained
by the latest V2355 qualification; keep the2026-10-21T06:00:00+08:00 GPU hold.

Next-run preparation uses `scripts/launch_qualified_after_hold.py`. The old
V102 environment still pins broad report mode,250ms and an obsolete engine;
never launch it just because the shell defaults have changed. Freeze V103 from
the latest source-bound CPU qualification, overriding those stale settings and
preserving36 simultaneous roots,running8,256K/95%,Host200GB/80:20 and the
dynamic nested delegation contract. Freeze the native engine separately rather
than symlinking execution to the mutable development engine.

The independent CPU watcher must not query or preload GPU before
2026-10-21T06:00:00+08:00. Recheck latest qualification, exact user-pause identity,
deadline and idle resources immediately before releasing that hold. Preserve
newer holds or cancellations; restore the original pause if driver launch fails.
Run predictive first and require the existing delegation-shape audit before a
same-engine native arm. This refresh is needed because the old native baseline
predates shared-engine changes; it does not require native on every iteration.
Retain the authorized frontend-cache asymmetry and its attribution limitation.
V2317 binds2355 passing application CPU cases, retaining all2307 prior cases
and adding48 launch-lifecycle checks. Rehash and retain the266 unchanged-engine
cases without claiming a rerun. This qualification does not establish GPU or
end-to-end performance acceptance.

Latest CPU control change coalesces ordinary JOIN/tool PREPARE candidate
publication within one post-forward call. Selectors request publication only
when they reach their existing publication branch; publish the final live native
pool state once before background PREPARE. Standalone calls and critical-reclaim
publication remain immediate. Keep flags local to this call; do not cache
cross-round state or alter source deadlines, native checks, DMA, ACK or admission.
V2313 compares7 native-index-shaped CPU cases,201 samples after10 warmups.
When both sources are due,36 waiting contexts/64 segments fall284->234us P50,
108 contexts641->554us. Final per-pool candidate prefixes are equal, and one-source
cases are unchanged. Context count is not root count; retain36-root workload.
Legacy fixtures without the native leaf/LRU indexes take a compatibility path
and cannot establish the indexed path's cost. GPU acceptance remains open.

V2315 binds2307 passing application CPU cases and retains all2257 preceding
contracts. Native engine/patch hashes are unchanged; retain the266 source-bound
native CPU cases without claiming a rerun. Cover direct/coalesced publication,
not-due and no-work calls, live lock/inflight/Host mutations, immediate critical
publication and clean exception retry. Freeze V2355 or a later qualification
after the2026-10-21T06:00:00+08:00 hold. Keep the full performance goal active.

Latest authorized JOIN policy is `selective_report_or_eos`; the long-repository
launcher now selects it and the spawn-FULL-qualified native engine. Keep pure
`observed_eos` for an ablation and the old broad report mode only for explicit
historical compatibility. EOS does not depend on the optional prediction's byte,
cadence or service-model support gates.

Use existing last-unfinished-child report/token progress, with200ms checks and
at most4 rotated candidates. Cheap input-size filtering precedes live checkpoint
capture; require at least512MiB genuinely missing Host-backed FULL. Predict only
when controller submit-to-ACK plus enqueue cost exceeds the causal EOS-to-native
arrival window. Use the deficit plus one check period, capped at1000ms advance.
Start with a50ms protocol floor; after4 causal completed JOIN reentries use the
P10 of up to64 recent samples, never including later GPU-service queueing.
These are conservative controller-cost proxies, not measured exposed GPU waits.

If timing is unsupported, leave optional reclaim idle and fall back to EOS.
Earn capacity and pass bounded residency/admission checks before forecast DMA;
cancel unconsumed speculative capacity intent on tool/epoch invalidation. Reuse
only the bounded scalar timing decision at enqueue, while recapturing/revalidating
native identity, checkpoint payload, ownership, capacity and transfer dependencies.
Do not introduce a semantic worker, global child text polling or a layer-pause
primitive. Attribute the actual issue trigger so EOS takeover is not credited
to a pre-EOS forecast. Compare useful FULL, repeated loads, control CPU, exposed
recovery wait and trimmed throughput/mean JCT against EOS/native on the next GPU
run; keep the overall performance goal active.

The earlier V2311 bound2257 passing application CPU cases to its sources. The native
engine and canonical patch are unchanged; rehash and retain the266 previously
qualified native CPU cases without claiming a rerun. V2307 compares7 CPU tree
cases: small-parent checks fall from206-667us to10-14us by declining speculative
planning, while a224-node large-deficit case remains1.439-1.469ms and selects
the same64 nodes. No GPU DMA, online population frequency or throughput/JCT
improvement is established. The V2315 qualification above supersedes this source
snapshot; do not launch the superseded V102 candidate.

Previous CPU optimization: action-local FULL/Mamba capture returns as soon as it
encounters a valid pending transfer, since that dependency already rejected the
candidate after a full snapshot. Keep default diagnostic snapshots complete and
all fields on unblocked paths. No cross-call state cache, broader D2H/H2D queue
serialization, or relaxed native identity/owner/lock/capacity/ACK checks.
V2302 compares24 identical CPU path cases with equal decisions/rejection reasons;
64-node leaf blocking costs about229→7.5us, middle blocking239→115us.
These are target-function CPU costs, not total scheduler or GPU/JCT improvement.
V2306 qualifies2141 application/266 engine cases and retains every previous case;
the native patch remains unchanged. V2300 itself was historical window evidence;
the later selective policy above is enabled by the user's explicit authorization.

Latest addition: real SPAWN, including a child delegating to grandchildren,
queues an immediate incremental backup of the parent's published native FULL
path. Dispatch at the first available post-forward safe point without requiring
WAIT_JOIN or pressure. Coalesce siblings by context, skip existing Host extents,
and continue pending increments after settled ACK. Use up to4GiB/64 extents per
batch, retain64MiB Host headroom, and never evict another Host copy to make room.
Other retries use200ms and inspect at most4 queued contexts. Keep native D2H
ownership/locks and critical-restore precedence. The200ms/512MiB background and
64MiB urgent paths remain. Do not prepare Mamba or free HBM at backup issuance.
Later pressure reclaim still requires settled Host data and fresh cold-owner,
identity, lock and transfer checks. A new request/epoch in the same native session
does not cancel the backup; compaction, terminal state or generation change does.
Account spawn D2H separately from predictive H2D. V2296 exports the isolated
engine; V2299 qualifies2028 application/266 engine CPU cases and retains all
previous cases. GPU acceptance must measure actual
large-batch enqueue CPU cost, missing-Host cold-page rejections, later reclaim/use,
and trimmed throughput/mean JCT. Keep the overall goal active.

Latest user instruction supersedes older launch times and workload text in the
long-running goal: do not start any GPU experiment or canary before
2026-10-21T06:00:00+08:00 (Asia/Shanghai). Continue CPU optimization with the
goal active. V101 was administratively stopped at2026-10-20T18:43:11+08:00
(V2277); V102 is frozen and has NOT started. A one-time pause marker and
ops/gpu_hold_until_20261021_0600.json record this hold; it is not a daily rule.
After that deadline, freeze the latest CPU-qualified sources and recheck user
instructions and resources before launching, rather than starting stale V102.
Keep36 simultaneous LoCoBench roots,running8,256K/95%,Host80:20.
Priorities: account for every EOS-backed JOIN restore opportunity and explicit
zero-byte/capacity outcomes; inspect native asynchronous scheduling and move or
remove added control work that blocks overlap; improve causal FULL donors and
background backup coverage. CPU proofs do not establish GPU/JCT improvements.

Latest CPU candidate replaces400ms/1GiB background FULL preparation with
200ms/512MiB under the user's new instruction. Keep64MiB urgent chunks,
one background batch, Host headroom, incremental FULL-only copies and ACK-before-
reclaim. An empty incremental index skips the native scan; overdue rounds do not
accumulate. Audit actual snapshot configuration rather than applying new caps
to old traces.

Observed EOS now bypasses obsolete H2D sample-count/size-support and repeated
start-window prediction gates. Preserve live EOS, parent/checkpoint identity,
capacity/lease budgets, final enqueue revalidation and native data dependencies.
Native asynchronous scheduling is already enabled. Preserve its future-token,
forward/WAR/result/delayed-sample order; on supportedTP1/PP1 overlap only, move
advisory wait-prediction maintenance after forward and behind EOS dispatch plus
its same-safe-point capacity retry. Keep events, ACK/expiry and admission safety
before selection. No new GPU experiment was started.

V2283 independently audits the stopped V101 trace:86 JOIN episodes,62 with a
positive FULL plan before completion,29 early submitted and next-request-reused
(3.202GB FULL).41 saw capacity rejection;4 saw service-size unsupported.
Reasons overlap.21 without any positive FULL plan have no recorded native FULL
Host-hit on their next request. EOS should guarantee an opportunity check;
actual FULL H2D requires a missing reusable Host copy. Service-size rejection
associates with4.155GB next-request native FULL,not proven replaceable bytes or
oracle JCT savings. Keep the overall performance goal unaccepted.

Previous V2289 qualifies2011 application/253 engine CPU identities against its
sources and canonical patch, retaining prior contracts with three documented
policy-dependent test renames. Offline timing analysis now handles EOS records
without service estimates as unknown windows, preserving labels and old measured
records. No GPU experiment is started or automatically authorized by this receipt.

Current priority: fix severe prefetch coverage before enabling demand handoff.
V2267/V2268 confirms V101 execution_handoff=false and zero handoff H2D;
JOIN/tool source volume3.439GB contains1.829GB FULL,while strict pre-completion
and next-request-reused FULL is1.119GB/0.0516% of all FULL H2D.
31/40 JOIN waits observe capacity-budget rejection. Source bytes are not all early.

V102 candidate uses the existing bounded native capacity scan to validate every
session owner of a resident FULL duplicate,including shared and unregistered
waiting owners. Recheck native generation,all-owner waiting state,device/Host
locks,inflight transactions and excluded restore targets before FULL-only demotion.
Keep Mamba device state and session references intact. Missing Host FULL still
requires urgent backup and settled ACK plus fresh owner validation before reclaim.
The owner index follows context-session binding revision;no new periodic tree scan.
When a new EOS capacity demand earns usable capacity,retry JOIN once in the same
post-forward safe point;no repeated planning without progress and no dependency
or timing waiver. Keep execution handoff disabled and the common36-root contract.

V2275 retains1982/211 prior cases and passes1994 application/224 engine CPU cases.
V2276 executes the frozen V101 reclaim method and candidate on the same CPU
allocator:shared waiting and unregistered single-owner cases change0 to4 freed
units;an unapproved owner remains blocked. State/Host/reference identity is retained.
Owner waiting is an explicit controlled predicate,not live GPU eligibility or gain.
Freeze the qualified candidate before stopping V101 under the user's existing
authorization for important coverage fixes;retain partial outcomes administratively
and launch a fresh arm without splicing traces. Validate actual causal FULL victims,
same-safe-point retries,early reused FULL,duplicate reloads,PREPARE coverage and
tail-excluded throughput/JCT. No performance acceptance from these CPU checks.

Latest requirement: check background PREPARE coverage in every subsequent
predictive arm. The driver now emits prepare_background.json offline, separating
idle/urgent FULL and semantic JOIN/tool PREPARE. Measure issued-round bytes and
node distributions, issue gaps, byte/extent-cap hits, sampled cold-unbacked
rejections, Host headroom and directional stream time. New cumulative counters
inside the existing400ms bounded check and1s state snapshot expose skipped/empty
checks, dirty-index size, actual scanned/stale/eligible entries, queue blocking
and Host/byte/node limits. No added per-token logging or whole-tree scan.
Keep the frozen V101 runtime/engine unchanged; diagnostics are a future candidate.
Do not increase an unhit cap from small-batch evidence alone. If safe published
unbacked FULL persists, use measured cadence/scan/queue/Host-budget restrictions
to tune the limiting stage, while preserving urgent priority and ACK ownership.
Acceptance requires fewer unbacked cold-capacity failures and actual recovery/
service benefit, alongside tail-excluded throughput/JCT, not copied bytes alone.
V2254/V2262 seals617.69s:996 background batches/77.81GB,mean74.50MiB,
P5045MiB,max480MiB and10 extents;none hits1GiB/32. D2H stream sum6.70s
has no physical wall anchor and is not exact PCIe utilization or removable JCT.
17/42 capacity checks still reject unbacked cold pages,all with at least one batch
of FULL space in their latest prior Host usage sample. This remains unresolved.
Use V2262 over the initial V2255 Host join: transfer ACKs are not usage samples.
V2258 exports diagnostics only;V2261/V2266 qualifies1982 distinct application cases
(1981 retained-contract suite plus1 snapshot supplement) and211 engine cases.
Shell syntax passes;new counters still await a future GPU run.
The newer V2264 prefix spans1584.82s:2455 background batches/116.36GB,
mean45.20MiB and no cap hits;53/129 capacity checks still observe unbacked
cold pages while prior Host samples have free space(minimum47.80GB).
This lightweight observation does not re-collect controlled first-use or the
Host eviction chain;do not upgrade native restore associations to proof of
consumption or predictive prefetch,nor splice it into V2254 lifecycle evidence.

Current V101 launched17:36:51 Asia/Shanghai from frozen8bdc5dc;first actual
GPU service17:40:00. V2251 verifies972 sources/540 external inputs,incremental
tracking and healthy writer. Preserve the frozen checkout and engine.
V99 native is complete(36/36 completed,91 JOIN,
11 nested JOIN). V100 launched17:12:05 Asia/Shanghai but was administratively
stopped for background D2H submission latency(V2243);all36 outcomes remained
unobserved. Preserve partial traces rather than treating them as natural failures.
V2242 reconciles67 early untagged FULL backup batches/13.94GB;these are not
JOIN/tool predictive H2D. Submit-to-issue-observation wall P50/P90 is230.02/
290.63ms,not pure CPU or removable JCT.

Coalesce consecutive Host destinations only inside the existing native staging
chunk. Keep64-page staging,400ms/1GiB background policy,64MiB urgent chunks,
fragmented/reversed/duplicate write order,locks,ACKs,H2D and Mamba dependencies.
Original/candidate each pass7 actual GPU layouts with bitwise/untouched-slot
checks(V2244/V2250). Idle-device256MiB submit/stream P50 improves51.39/51.40
to3.85/7.75ms;1GiB207.22/207.23 to15.42/30.58ms. These are transfer canaries,
not model contention or throughput/JCT evidence. V2249 retains all previous
cases and passes1969 application/209 engine CPU cases.
Predictive is running using frozen V101. The optimization is also available
to native: use V99 as a development reference and later match this kernel
when isolating agent-policy gains. Full performance acceptance remains open.

Offline lifecycle auditing now reconciles untagged native FULL backup batches,
same-creation capacity releases and later restore/controlled-first-use evidence.
Mixed/split receipts remain ambiguous; Host eviction,new observed generation
or later FULL writer ends the association. Do not assign original Host allocation
continuity,unique consumed bytes or speedup from these associations. The95-case
focused CPU check(V2240) adds no runtime observations or high-frequency work.
V2252/V2253 reconcile90 early FULL backup batches/22.95GB;no capacity-release
or restore credit is observed yet. The live240MiB cohort submit-to-return P50
changes207.52 to3.94ms(23/40 batches),stream54.63 to32.71ms. Equal bytes
do not control model work/pressure,and these are not end-to-end savings.

Latest order override,2026-10-20 Asia/Shanghai: pause V99 predictive and run
native now while completing FULL preparation/capacity fixes. Predictive stopped
16:01:47 with30 completed/6 without terminal evidence; retain its partial trace.
Native launched16:03:18 and first served16:06:10 from unchanged frozen
d2b7139/V95 engine,with the same36 simultaneous roots and restored common
delegation contract. V2211/V2212/V2215 record ownership,source and baseline
flags. Do not combine the old partial arm with a fresh predictive retry.
Keep this order over the historical predictive-first text below.

The old0c965ed continuation was withdrawn16:30:07 before any GPU launch.
The user requires continuous asynchronous FULL synchronization,not merely
eviction-time catch-up. V2231 passes1950 application/205 engine CPU cases.
Freeze this candidate and recheck source,engine,native summary/timeline and
idle resources before a fresh predictive run.

The400ms/1GiB replacement passes1951 application/208 engine CPU cases
(V2237),retaining all V2231 cases. Keep the old candidate frozen and use
the isolated batched engine. CPU metadata/lock tests do not prove real DMA
speed,overlap or a native-relative throughput/JCT improvement.

The next main candidate separates idle and urgent FULL copying. Idle queues
permit one batch up to1GiB/32 extents,with400ms cadence and32-node checks,
preserving one batch of Host space and never evicting another Host copy.
Only capacity-blocked critical restoration after the existing backed-capacity
scan is exhausted permits small64MiB D2H chunks. Reuse native segmentation,
backup locks and ACKs; reclaim before H2D planning and permit subsequent D2H
while parent H2D remains in flight. Retain full-input/decode/Mamba dependencies.
Neither equal directional rates,perfect overlap nor submitted-DMA preemption
is assumed. Referenced waiting agents keep the existing session-safe PREPARE
path; the non-indexed compatibility sweep only handles unreferenced cold FULL data.

The user requested a proportional fourfold increase from100ms/256MiB.
The old continuous-copy queue was withdrawn before GPU launch(V2233).
Keep the nominal2.5GiB/s byte budget,not a guaranteed transfer rate.
Do not wait to fill a batch or accumulate missed background rounds.

Cache commit/split/unevict publishes one coalesced dirty FULL entry. A400ms
post-forward loop consumes at most32 entries and copies only missing extents,
including stable prefixes of running/shared requests under native read locks.
Do not wait for eviction after a100MiB backup grows by900MiB; V2230 tests this
with scaled CPU metadata,not an actual1GiB DMA. Keep Host prefixes and Mamba
boundaries intact; retain native ACK and active-lock protections. No per-token
tree scan or periodic Mamba backup. V2228 identifies the explicit optional
commit observer as added common-path work; do not claim unchanged ordinary
ASTs or zero overhead. V2232 measures0.237us coalesced publication,37.6us for
a32-node metadata round,and0.213us empty-backend dispatch increment.
Actual scan/DMA/SM costs,useful FULL volume and tail-excluded throughput/JCT
remain GPU acceptance items. Freeze CPU-qualified code before predictive
retry; no current performance goal is considered achieved.

Current V99 launched2026-10-20 15:14:15 Asia/Shanghai from frozen
d2b7139. Keep36 simultaneous roots,the restored
native_in_graph_nested_2to4 profile,root-only full source,running8,
256K/95%,Host80:20 and latest confirmed-JOIN capacity handoff.
Run predictive first. A new native arm is conditional on final actual traces
showing every root's initial2–4 fanout,multiple root rounds and nested spawn.
The offline checker runs outside model execution; it neither rejects RETURN
nor adds per-decode work. No historical native is a matched reference.
V2195 rejects stopped V98 using real traces. V2199/V2200 pass1917 current
CPU cases without skips,retaining all1886 previous cases.
Keep the V2196 failed attempt: its50ms hint used a real clock; V2198
reproduces intentional49ms/51ms expiry. Freeze the test clock and retain
expiry-boundary tests; production TTL and sorting are unchanged.
V2201 verifies first actual service15:17:18,972 frozen sources,525 external
inputs and zero writer failures/drops. V2202 attaches a new observer to the
same live driver after an observer-only import failure; no experiment restart
or frozen launcher mutation. V2203 observes36/36 initial root rounds of2;
a second root round is now observed but nesting remains unproven.
V2204's early sealed prefix has3 completed JOIN waits with positive FULL plans,
all capacity-constrained and none submitted before completion. Its25.518MB
reused FULL was issued between client submit and native arrival; keep it
separate from pre-completion prefetch. Useful coverage and performance
acceptance remain open; restored initial delegation is not goal completion.
Preserve the frozen execution checkout while main development continues.

Latest user direction: restore the earlier working delegation prompt/configuration
with minimal changes. The long-repository launcher now defaults to
`native_in_graph_nested_2to4`. V82b frozen `ab951b9` and main have identical
root/coordinator prompt text and initial/JOIN middleware ASTs. Reuse these
unchanged, including the old four-phase/coordinator guidance and initial
task/[2,4] generation settings. Concrete assignments remain model-generated;
add no round-count or natural-RETURN rejection. Keep LoCoBench,36 simultaneous
roots,root-only full source,task-specific descendants,running8,256K/95%,
Host200GB/80:20 and latest runtime fixes.
Explicitly override inherited dynamic FANOUT_PROFILE when freezing a new run.
Check actual traces for initial2–4 fanout,multiple rounds and nested children
before continuing full performance validation. Scripted CPU model tests prove
plumbing only. Cancel native if workload shape fails; after a pass, use fresh
native with the same changed common contract, not V96 as a matched baseline.
V2193 records the five matching definitions and unchanged-source provenance.
V2192 passes25 CPU cases without skips, including the restored profile with
root-only long source in the nested harness. No new GPU run is started by
this source/CPU audit.

V98 was administratively stopped2026-10-20 14:42:31 Asia/Shanghai for the
user's sparse-delegation rejection (V2190/V2191):17/36 completed,19 without
terminal results;3 delegating roots,6 children,3 root JOIN rounds,no multiple
rounds or nested spawn. Sixteen of17 completed workflows never delegated.
Owned resources are released and the native follow-up is cancelled. Preserve
partial evidence; infer neither natural failure nor complete-run performance.

New main candidate preserves critical capacity preparation through a proven
confirmed JOIN ticket into client reentry, stopping at native request arrival.
Match the ticket's old safe checkpoint/current consumer, keep the earlier
original-demand/ticket deadline, and retain expiry, compaction, cancellation,
session/generation and native-arrival invalidation. Do not treat arbitrary
READY/RUNNING_LLM agents as critical or promise first-service coordination.
Move TOOL_START donor checks after all batch ticket/client bindings and coalesce
by context so intermediate metadata ordering cannot discard valid demand.
V2187 compares verified frozen V98 methods across5 graph/ticket sequences:
old0/5 versus candidate5/5 retain capacity; no DMA is issued in this proof.
V2186 passes1886 current CPU cases, including37 new identity/lifecycle cases.
The stopped V98 snapshot is unchanged; actual capacity and performance remain
unverified for this candidate. Do not credit CPU eligibility as useful prefetch.

V98 historical launch:2026-10-20 14:13:44 Asia/Shanghai from frozen34f9362,
with first actual workflow service14:17:23. V2181 verifies971 frozen
sources and507 external inputs, matching actual configuration and zero
writer failures/drops. Predictive only; reuse completed V96 native as a
development reference. Common36-root simultaneous workload, engine and
capacity remain unchanged. Actual useful FULL/consumption and performance
acceptance are pending; keep the goal active.

V97 was administratively interrupted at2026-10-20 13:59:38 Asia/Shanghai
for the user's latest-code restart, with16/36 completed and20 without terminal
results (V2177). Preserve all partial traces and frozen code; this is not a
crash or natural task failure. V2178 finds zero pre-boundary-issued and
successor-reused FULL across6 completed JOIN waits despite positive plans.
Start-window records and ACK counts cannot substitute for that metric.

The V98 candidate passes1849 current CPU cases without skips (V2180).
It ran frozen with the same36-root common contract and completed
V96 native development reference. No performance acceptance yet.

Post-V97 main candidate connects native EOS to critical parent capacity demand.
Price the same safe input checkpoint before free-list constraints; include native
input/decode reserves and bound demand by the existing EOS evidence lifetime.
No report forecast or supported DMA timing is required for capacity preparation;
physical transfers retain their existing independent checks. Distinguish capacity
rejection from no Host restore target. V2175 passes420 relevant current CPU cases;
V2176 reproduces the frozen49fc2a8 omission before the fix, with no DMA credit.
The stopped V97 snapshot is unchanged. Frozen V98 demand still requires WAIT_JOIN
and current EOS evidence. Main's ticket handoff ends before native visibility;
unlock-through-first-service coordination is not claimed complete.

A bounded FULL-only timing fallback uses recent measured larger payloads when
ordinary estimates have no support. Keep raw ACK/queue durations without byte
downscaling, require3 samples, use nearest16 from the existing32/300s pool,
and retain current estimate caching/expiry. V2179 supports18/22 historical
independent waits, about294MB of maximum plans, not proven early DMA.
Only4 actual validation copies exist and1 exceeds estimated P90; do not claim
a calibrated percentile or guaranteed upper bound. No new per-decode scan,
Mamba mixing, larger-target extrapolation or common-workload change.

Latest complete V96 geometry audit V2169 uses the existing common cutoff.
Fifty matched decode cohorts cover60483/52314 batches; their common minimum
weight20769 yields13.947/13.936ms worker means, about0.08% apart. Actual mean
contexts are147846/101414 tokens. This is descriptive matching, not proof of zero
control cost or a causal throughput/JCT adjustment. V2171 coarse short-prefill
work/prefix/neighbor matching still differs5.73%;16-token work bins have no
sufficient common cohort. V2172's sampled post-launch physical CPU mean0.422ms
is not a whole-run cost; selection/run_batch/result differences still confound
work. Keep short-prefill and actual prefetch/consumption acceptance open.

Current V97 candidate implements immediate tool-wait capacity donation and has
passed1829 current CPU cases (V2166). For a live near-term critical parent
restore, another current-session agent in WAIT_TOOL needs no duration model,
minimum wait age or round-trip window to become a FULL backup/reclaim candidate.
Rearm at TOOL_START and once for new/growing critical demand. Reclaim valid
backed cold capacity before more backups; D2H ACK, current waiting state,
native ownership, references, locks and inflight checks still precede release.
Ordinary tool policy is unchanged without critical demand; Mamba PREPARE stays
off. Actual DMA, donor-to-parent consumption and performance remain unverified.
Historical V97 launched2026-10-20 13:32:58 Asia/Shanghai from frozen49fc2a8,
predictive-only against completed V96 native, with the same36-root simultaneous
arrivals/common harness/tasks/engine/capacity contract. It was administratively
stopped13:59:38 for the EOS capacity and smaller-FULL timing fixes.

V96 completed2026-10-20 13:20:32 Asia/Shanghai. V2167 applies the existing
common600–1793.304s cutoff: decode470.235/542.871 token/s remains13.38%
lower; restricted mean1362.907/1192.747s is14.27% higher. Both arms have36
completed outcomes; predictive has35 natural/JCT-qualified tasks, with one
existing-budget finalization retained and qualified separately. Quality is
ungraded. JOIN/tool FULL transferred and reused1.470GB; pre-boundary issuance
and successor reuse1.230GB. The10GB and native-relative goals remain active.

Delegation regression is explicit: the old profile forced initial task/[2,4]
generation and prompted fixed phases/coordinator roles; the dynamic profile
uses auto without initial fanout bounds. V96 predictive has3 delegating roots,
6 JOINs/11 children, versus31 native JOINs; neither arm has nested children.
Do not describe this as lost JOIN events or solely a dataset property. Do not
silently restore fixed phases/roles or RETURN gates. The latest explicit user
direction at the top now restores the old prompt/profile without RETURN gates.
Any future common-harness
change requires a new matched native reference. V2161/V2164 tested the
superseded1-second fallback, not the current immediate policy. Withdraw the
inconclusive estimator micro-optimization rather than crediting its median
improvement while omitting its P99 regression.

V96 historical startup supersedes the older workload below. It launched2026-10-20
11:49:59 Asia/Shanghai from frozen63625de:36 simultaneous roots, predictive
first, then fresh native. All36 offsets are zero and all historical native
reference arguments are cleared. The9 projects and36 distinct original tasks
come from the same LoCoBench-Agent archive; scenarios and435 source files are
hash-verified. Root source lengths are105597–194277 tokens. Only root receives
the complete project; children/grandchildren receive model-selected assignments,
paths and short evidence, then read the shared workspace. Strengthen ongoing
multi-round/nested collaboration without fixed assignments, generated tool calls,
round quotas or RETURN gates. Current CPU qualification passes1784 distinct
runtime/harness cases plus159 native cases; actual nested requests are covered.
First real workflow service is11:53:39. V2159 verifies971 frozen sources and481
external inputs, with no writer failures/drops. This is startup qualification,
not complete-run throughput, useful coverage or quality acceptance.

Keep running8,256K/95% compression,Host200GB/80:20,tool180s and the qualified
V95 engine. FULL PREPARE,online tool timing and borrowable parent headroom stay
enabled; Mamba PREPARE,execution handoff and detailed high-frequency diagnostics
stay off. Treat critical headroom as borrowable demand, not permanently idle HBM.
At safe waiting points back up eligible cold FULL extents, wait for ACK and
native reference/inflight validation, reclaim capacity, then restore and consume
the critical parent state. Do not preempt an executing LLM request.

Target at least10GB JOIN/tool FULL prefetch while separately reporting actual
transfer, pre-completion issuance followed by successor reuse, and native demand
restoration. Do not create useless DMA to meet a byte target. Verify real
backup-to-ACK-to-reclaim-to-parent-consumption and useful-copy losses/recompute.
Keep the goal active until tail-excluded throughput and mean JCT improve against
native with adequate useful coverage; startup/CPU checks are not acceptance.
Historical V91b is not a matched comparison for these changed inputs/arrivals.

V95 was administratively stopped at11:34:55 for the user's new workload.
Preserve partial traces and immutable snapshots; infer no crash/natural failure.
Historical V95 launched2026-10-20 11:23:18 Asia/Shanghai from frozen8164200 and first
served a real workflow at11:26:21. V2155 verifies970 frozen sources and actual
configuration/process identities. Keep the24-root16+8/900s,running8,
256K,Host80:20 contract and historical V91b native reference. Its engine patch
differs: AST review limits source changes to the private BeliefKV PREPARE entry,
one capability method and CPU tests. Native demand/cache/scheduling methods
remain unchanged. Preserve the historical-reference qualification.

Write-back now prepares one currently reclaimable private FULL device leaf
without requiring Host duplicates of resident ancestors. Reconsider capacity
after ACK/native demotion; also recognize an interior device leaf exposed by
Host-only descendants. Preserve generation/ownership/locks/pending-copy checks,
write-through continuity, the input-safe H2D plan and latest Mamba. V2151 passes
1620 distinct runtime cases; V2149 passes159 native cases. Real CPU hybrid-tree
checks cover ACK protection, shared ancestors and leaf/ancestor recovery order;
they do not execute DMA/forward. V2152's complete8-context/64-node CPU P50 is
3.805 to3.423ms. Keep256-node P99 and legacy-engine regressions.

V94 is complete; V2150 retains the existing common600–1537.851s comparison.
Restricted means714.591/813.582s improve12.17%; throughput370.121/404.289
token/s remains8.45% lower. All24 outcomes are completed,23 natural/JCT-eligible;
the mobile-game feature task used existing graph-budget finalization. Quality
is ungraded. Do not declare acceptance from lower mean completion alone.

V2154 addresses prefetch-volume diagnosis using latest V94 and the cited V92.
V94's2318 ordinary prompts all exceed100K (median170769.5); there are32 ordinary
agents, excluding3 internal compaction requests/contexts. V92's69 FULL commands
contain32965 tokens and serve9 successor requests; FULL denotes the attention
KV component, not a complete agent. V92 had14 positive JOIN plans,12 with
capacity rejection and5 with enqueue identity change; reason groups overlap.
Latest V94 has8 JOINs/3 positive plans,924 tool episodes/8 positive plans,
213.873MB controlled FULL and279.183MB total physical H2D. Its Host FULL high
water is52.107/160GB, not a full pool. Native's93.119GB comes from a different
realized path/residency policy and is not isolated removable traffic.
Focus on effective overlapping cold working sets, not total root count:
12/16 first-wave roots finish before V94's second batch, versus15/16 in V92.
Changing arrivals/root count requires a new comparison contract; do not silently
reuse old native as if its workload matched.

Historical V94 launched2026-10-20 10:39:55 Asia/Shanghai and first served a real workflow
at10:43:11. V2140/V2142 verify frozen3484310,970 execution files, live settings
and1603 passing distinct CPU cases. GPU was actually idle and V93's recorded
driver/scheduler had exited. Only predictive is new; retain V91b native and the
24-root16+8/900s,running8,256K,Host80:20/unchanged-engine contract. Keep its
snapshot immutable and validate the distant-report deferral counter, useful
pre-completion recovery, tail-excluded throughput and mean completion latency.
V2143's10:46:29 snapshot verifies264 live distant-report deferrals and295 native
admissions with the same scheduler identity and zero writer failures/drops. This
confirms execution of the new branch, not exclusive CPU savings or JCT acceptance.

V93 completed normally at10:38:45; V2141 preserves the driver's existing exports.
All24 outcomes say completed, but only23 are natural/JCT-eligible. The mobile-game
bug workflow used existing graph-budget finalization; retain and flag it rather
than relabel or remove it. Common600–1357.683s restricted means are667.033/
759.557s (-12.18%), throughput371.302/410.553 token/s (-9.56%). Whole mean JCT
is707.046/829.357s. Quality remains ungraded; frontend/path differences and the
15-second external GIL diagnostic prevent isolated KV attribution or formal clean
pair claims. Seven FULL commands/99.779MB are reused, but only one JOIN/15.299MB
and one tool/14.049MB start before client completion. ACK-to-service P90 is607.476ms,
with no observed prior native reload. PREPARE has978 commands and11 linked restores.
There are7 children, one multi-round root, no nesting and no observed handoff-replan
branch. Do not declare the full goal achieved.

Main now avoids full report-phase capture when even a conservative upper bound
of the current timing rule cannot enter its existing two-window capacity lead.
The bound uses the safe input, at most eight checkpoint paths and current H2D
samples; it is not a prediction of actual future DMA latency or authorization.
Near stages bypass this bound, unknown metadata falls back, new timing evidence
is read each time, and EOS/live native checks remain. V2139 passes1603 distinct
cases with no skips, including the previous1582 plus15 boundary cases and6 old
estimator controls. V2137's64-node distant CPU P50 is519.595 to21.845us;
V2138's saturated histories give518.919 to113.569us. Preserve near-stage about3%
regressions and initial V2134–V2136 results. V2132 is a15-second external V93 GIL
development profile,725 samples/0 errors, not exclusive CPU accounting or a
clean formal performance arm. The candidate is now frozen separately in V94.

The next main-only simplification uses one ordered ancestry dict instead of a
temporary list plus cycle-detection set in residency ordering. Node identity,
namespace, transfer and native admission checks remain; no cross-call cache is
added. V2128 retains120 differential fixtures and10 whole-planner controls with
identical plans/counters/budgets/native reads. The8-request/64-node thread-CPU P50
is559.091 to548.886us, and256 nodes1720.747 to1687.505us. Preserve warm-hint
0.084/0.489us regressions. V2130 passes346 existing CPU cases. V2129 collected
none because of a nonexistent test path; keep its raw XML. This later change
is not in running V93 and establishes no GPU/JCT improvement.

Historical V93 startup: launched2026-10-20 10:01:29 Asia/Shanghai, first actual workflow
GPU service10:04:26. V2126/V2127 verify frozen17478fe,970 execution sources,
live processes/settings and1582 passing distinct CPU cases. The prelaunch GPU
sample is idle with no compute process. Only predictive is newly run, retaining
the V91b native reference. This is startup verification, not performance acceptance.
Keep its snapshot immutable while developing on main.

V92 completed at2026-10-20 09:39:11 Asia/Shanghai from frozen e8010f0.
All24 outcomes are natural completed and JCT-eligible, with zero writer failures
or drops. Quality remains ungraded. V91b native is the completed historical
reference. Keep24 roots,16+8 arrivals/900s,running8,256K,Host80:20 and the engine.
V2121 follows the pre-agreed common600–1402.181s window: restricted mean
completion latency613.936/778.398s (-21.13%), decode throughput366.917/413.726
token/s (-11.31%). Full mean JCT is662.251/829.357s (-20.15%). The full goal
is not achieved. Different frontend switches and realized paths prevent isolated
KV attribution. Physical H2D is1.628/93.119GB. All69 controlled FULL commands/
675.123MB are reused, but only4 independent JOIN events issue before JOIN
completion and reuse483.881MB. All issue after native EOS; tool prefetch is zero.
There are16 dynamic children,5 multi-round roots and no observed nesting.
Do not regenerate the completed comparison or HTML timeline unnecessarily.

The latest fix decouples safe capacity preparation from H2D timing support.
V91b recorded136 unsupported-size checks and no critical-parent reclaim checks;
these are checks, not136 independent missed opportunities. Within the existing
near-stage lead, a live last-child report can arm restore plus native input/decode
capacity even without a supported timing estimate. DMA still requires the original
ticket, timing, ownership, free-capacity and dependency checks. V2106 reproduces
the old/new behavior with zero physical commands. All1118 distinct V92 candidate
CPU cases pass after explicit local-tokenizer rechecks supersede two initial skips;
two passing subtests are not extra cases. See V2107–V2109.
V92 includes this fix and the two control reductions below. Qualification verifies
only two predictive runtime execution files changed; keep the workload/engine/frontend
contract and label the historical live reference accurately. Useful early recovery,
PREPARE cost, tail-excluded throughput and mean completion latency still require
GPU acceptance.

V2111 exactly reconciles completed native prefill geometry to logical uncached input:
4,938,458 predictive versus4,555,421 native extend tokens. Of the383,037 excess,
347,750 (90.79%) belong to the already intervention-marked mobile-game bug workflow.
Keep all24 outcomes in the primary result. This descriptive partition neither
justifies post-hoc task exclusion/counterfactual throughput correction nor attributes
all excess prefill to control work. First-root input work is close; continuation,
child and compaction work differ. FULL/effective-prefix gaps are not per-layer GPU
recomputation or removable JCT. Follow the latest V92 trace before changing arrivals.

The subsequent main-only residency reader removes repeated node/component field
reads and per-node namespace comparison tuples. It adds no cache and retains node,
namespace, transfer and final native validation. V2114 passes597 related CPU cases;
V2113 separately checks120 differential fixture layouts and8 whole-planner controls
with identical plans, counters, budgets and native lookups. The8-request/64-node
thread-CPU P50 is560.634 to534.131us, and16 requests587.058 to555.254us.
Keep the single-request warm-hint0.026us regression. This is not in completed V92 and
does not establish GPU/JCT improvement.

V2112/V2115 seal an initial V92 prefix:38 FULL receipts/296.038MB all have
first-service reuse; one independent JOIN issues before completion and reuses
123.597MB. The35 critical-capacity checks concern two parents and free nothing;
all1935 scanned native leaves are unbacked. Triggering the repaired path is not
successful capacity handoff. Twelve expired lease receipts are still reused,
with no observed before-first-service native reload; expiry alone is not waste
or a reason to extend protection. Next inspect safe waiting-agent backup versus
actually reclaimable leaves, and remove repeated ineffective scanning only with
sound native invalidation. Continue the immutable V92 snapshot absent a confirmed
material bug; these independently sealed prefixes are historical, not a whole-run result.

Reject the next empty-restore ancestry candidate rather than ship branches for a
0.645us64-node diagnostics-off whole-tool-dispatch improvement. V2116 preserves
all12 controls, including positive-restore regressions; V2117's529 passing cases
and180 differential fixtures concern that unshipped candidate. V2118 retains its
exact patch. The empty-restore reader is restored to d9827c5; do not count this as a new
production optimization. Native no-DMA demotion has no uniform invalidation
revision, so do not lengthen the negative cache to an entire waiting episode.

V2119 aligns727 observed tool start/end pairs in16 sealed client files to the35
critical-capacity checks and finds no overlapping client tool wait. This cohort
does not justify lowering the tool-offload threshold, identify rejected leaves
with agents, or establish that HBM contains no cold cache. V2120's already covered
300–600s window has nearly identical7.92 decode batch but452.593 versus481.983
token/s; predictive also executes170395 extra prefill tokens. The600–900s batches
are4.228 versus6.542. Keep realized work and control cost separate. This report
was produced while V92 collected; V2121 now seals completed outcomes and the
existing standard comparison/timeline exports. The last workflow was not restarted.

The next fix addresses a real client-submit handoff inside the final event drain.
The ticket is rebound to the new consumer while the current call holds the old
key. Replan once only for the same confirmed ticket and proven old checkpoint,
through fresh epoch/capacity/native checks. Do not reuse old steps or grant ACK
credit. Native visibility, missing checkpoint, changed generation, cancellation
and a second invalidation still stop. V2122 reproduces the old failure; V2124
pins the old dispatcher and shows0 versus4 new-epoch reservations with no receipts.
Eight ordinary complete-dispatch controls retain records/reservations/native reads;
small timing regressions are retained, not claimed as performance gains.
V2125 passes1582 distinct current-source cases with no skips, including8 new
handoff cases and real local-tokenizer checks. Two passing subtests are not
additional cases; real SWE Docker is unselected. V93 is frozen with this fix and
the residency-reader reduction. Only two runtime execution files differ from V92,
three from the V91b native reference; workload, engine and frontend settings stay.
Predictive launched after an actual idle-resource check. Validate the replan
counter, true first-service reuse and tail-excluded throughput/JCT; keep /goal active.

Main now skips causal/residency ranking only when a committed chunk has exhausted
the current integer input budget, all native inputs are nonempty, DLLM is absent,
and tool-only timing has at least two tagged candidates. Identity, terminal and
native admission checks remain; next-batch input reserve stays4096. Restored budget
resumes ordinary planning. All313 CPU cases pass, including the11 new cases rather
than324 distinct cases. Pinned full-planner controls agree across12 scenes;
8-request/64-node exhausted CPU P50 is560.940 to27.215us, with520 to0 native
lookups. Preserve two available-budget0.77%/0.99% regressions and initial V2090
results. V91b does not contain this change; do not restart a clean GPU pair for
unproven microbenchmark gains. See V2095; online frequency and GPU/JCT remain unknown.
V2096 reconstructs16 peak and9.062 time-weighted active roots;12/16 first-wave
roots end before the900s second arrival. These are not GPU slots. V2097's live
native prefix already has155 H2D batches/91.821GB including Mamba. Do not infer
that both arms lack migration demand from predictive's small H2D, change arrivals
mid-pair, or subtract logical Host hits from physical DMA. Finish the control and
separate residency, hybrid reuse/recomputation and realized-work differences.
Main's subsequent identity binder removes two temporary comparison tuples while
rechecking all live metadata, attempt and normalized session fields. Existing355
CPU tests pass;18 complete planner+selector controls retain identical plans,
selections and counters. Both module references must use the pinned binder for
the baseline. The48-request normal P50 is451.400 to442.968us; preserve three
single-request0.024/0.046/0.089us regressions. V2098–V2100 are CPU evidence only;
the live V91b snapshot contains neither this change nor the exhausted-budget skip.

The confirmed V89 compaction error is CPU-fixed:
V89 predictive finished22 completed and2 TemplateErrors, with no child/JOIN.
Its1612 D2H batches/63.173GB include auxiliary diagnostics and are not a clean
performance control. Native was administratively stopped during initialization;
no native workflow result exists. The auxiliary single task lost its connection
when the owning V89 driver cleaned up the shared server. This operational lifetime
mistake does not establish an engine failure. The next probe must own its server.

Both failed trajectories reproduce the retention binary search passing user-free
assistant/tool fragments to the strict Qwen chat template. Fragment budgeting now
uses estimates; ratio-based compression, completion budgeting and complete-request
preflight keep exact counts. Real model messages are not supplemented with a user.
The saved full counts remain120637/94121;33 relevant CPU checks pass. See V2075.
GPU completion, useful migration and native-relative performance remain unaccepted.

V90b owned its server and completed the original single task naturally, frozen
at7e77e28:974.85s,5 sequential delegation rounds,5 natural child returns,
330 model calls. No nesting or parallel child execution is demonstrated.
JOIN-to-parent-service is155-163ms in all5 cases. V2080 seals this lifecycle;
it is neither migration-pressure qualification, task-quality proof nor a speedup.
V2082 preserves the25s GIL profile,1362 samples and0 errors; these are not CPU
accounting, a native-relative cost decomposition or removable JCT.
The subsequent main-only decoder change reuses immutable wire fields and removes
an intermediate extension dict. Across316 historical events and30 full-control
replays, graph/counters/delivery/exact ACKs match; CPU P50 is50.753 to49.566us.
All345 related checks pass. See V2077–V2079. V90b does not contain this later
change; freeze it separately for the next GPU candidate. No GPU/JCT gain is claimed.

V90b also contains33 identical edit inputs and unchanged workspace digests over
112.60s. Exact arguments were not retained; do not claim the new identical-edit
check proves that episode eliminated. CPU reproduction establishes that identical
search/replacement text was accepted and advanced workspace epoch. The backend now
returns a parameter error without changing the file/epoch, and later valid edits
still work. Natural RETURN is unchanged. A separate identity check removes temporary
dict/generator work while reading metadata each time. V2081's8-request on_launch
P50 is18.728 to14.420us; V2084 binds the installed function body to that proposal.
Keep the mixed single-request0.017us regression. All359 related checks and4 final
feedback rechecks pass; the real SWE Docker case is explicitly unselected.
Queued V91 was cancelled before any GPU launch; freeze the latest fixes anew.

V2064's two single-turn user variants
both select read_file, and V2065's explicit-team version selects ls. No tool is
executed in those probes; a first action does not establish later delegation.
The latest client appends the collaborative mode to the dynamic profile's actual
user task. Work decomposition, timing, fanout, nesting and subsequent rounds remain
model-selected. V2067 captures the full _run_autonomous serializer; V2068 passes2398
CPU checks. V2066 additionally has190 passes and one unselected SWE Docker case skipped.
Next run one original cross-file refactoring task to observe a real child and
first JOIN, without claiming original-task completion, migration consumption or JCT.
Use a fresh, matching24-root run for performance acceptance after qualification.

V2060 pairs34 short single-request prefill samples: post_launch_physical P50 is
0.013ms versus selection4.856ms. Native work remains inside the broad phases.
V2062 preserves the bounded8s GIL profile,24 samples and9 errors; it does not prove
full-run CPU cost, a native-relative bottleneck or a speedup.

V88 predictive launched at2026-10-20 06:00:31 Asia/Shanghai after two idle-resource
checks and the latest CPU qualification. Its first real workflow GPU service was
recorded at06:03:54. Revision a3bc3cc,970 source files, the engine, actual serving settings
and zero writer failures/drops were verified in V2051.
It was administratively interrupted at06:14: the first16 roots submitted974 model
requests and1008 tool calls without a child or JOIN. Ten natural completions remain;
six active and eight unarrived tasks are not natural failures. Native never started.
The stopped prefix records318 D2H batches/22.70GB and no H2D; V2056 observes252
tool waits without a positive FULL plan. One successor's native evidence is unknown.
The one-time launch and live driver state is registered in
`/home/longhao/experiment/ops/v88_24root_after_0600_20261020.state.json`.
The next V89 candidate still runs predictive first, followed by native on the same
frozen execution sources.
Subsequent candidate changes need validation and a new frozen source; keep the live
run immutable. This is not a recurring daily restriction.
V85d predictive finished all12 workflows naturally; its interrupted native trace
stays preserved and will not automatically resume.
The report-only semantic-worker validation for observed/report-service JOINs
is fixed;65 checks pass, with collection code and frozen configuration unchanged.

The completed12-root audit is V1923/V1924. Pre-JOIN reused FULL is2.868GB,
but only85.463MB across2 JOINs starts before model EOS. H2D stream totals1.160s;
long-input frontend conversion totals676.813s, neither an oracle JCT bound.
Before a resumed24-root run, distinguish missing-copy opportunity, safe FULL
capacity, new-context hybrid checkpoint gaps and service consumption. No completed
native control or policy speedup has been established for this workload.

The V89 candidate is24 distinct original tasks on the same six qualified
projects,16+8 waves900s apart and running8. Use native_in_graph_dynamic_nested:
the model chooses delegation timing, fanout, roles and repeated rounds. Do not
enforce four phases, force task generation at milestones, or reject natural
RETURN. All direct-child roles can spawn leaf grandchildren, with depth capped2.
Keep scope, completion notice and delegation guidance after the long source.
Measure observed delegation/JOIN density and useful migration rather than
claiming the prompt guarantees the desired shape.

V2054 captured the actual serialized CPU request: task and the dynamic guidance
were already present. Treat the absent delegation as a workload-qualification
problem, not a proven tool-registration bug. V89 uses repository-specific task
guidance and description; children are told they already have the project snapshot.
Keep automatic choice, tool arguments, parallel calls and unguarded natural RETURN.
The description shrinks7265 to1286 characters and system text574031 to573095;
these are not token counts or performance savings. V2057 preserves the serialized
tool contract and V2058 passes2398 CPU checks. Real model behavior remains unverified.
Scheduler/migration runtime and engine are unchanged. V2059 binds the evidence.

Borrowable parent capacity now prices restoration plus the native input/decode
reserve, retaining safe waiting-state preparation and native reference checks.
V1925 tests exact token reuse across growing source-backed histories:36 cases
from12 workflows, three iterations, mean CPU conversion323.66 to98.61ms.
The main comparison enables the bounded token cache in predictive and keeps it
off in native. Attribute this contribution to frontend encoding, not KV policy.
Keep the native-cache-on matched-frontend ablation available for formal attribution;
do not require an additional development run on every iteration. Our reactive
control defaults to the same frontend as predictive. Freeze per-arm settings and
verify their server-launch snapshots; preserve them on resume and exclude inherited
global cache flags from native.
The current frozen engine is sglang-v88-idle-control-cpu-20261019,
patch2cbb0b46. Preserve V87/130d376d and V86/af328f26, and respect
the explicit SGLANG_SOURCE_CHECKOUT through the launcher.
This is CPU evidence, not an end-to-end result. Keep new-context hybrid checkpoint
gaps separate; FULL matches alone cannot reconstruct a missing Mamba checkpoint.
The one-time hold was released only for this qualified24-root run.
V2052's initial large-map NUMA snapshot spans N0(52.226GB) and N1(149.574GB).
Preferred placement permits fallback; these maps do not identify exact pool
allocations. Observe native placement too. Optional TorchCodec warnings remain
in the log; the launcher's explicit successful freeze_gc after health precedes
real workflow service and handles the internal startup race.

Current goal:

- Continue removing unnecessary repeated PREPARE, prefetch, lifecycle and admission work.
- Preserve current identity, generation, ownership, capacity, ACK and first-consumption checks.
- Fix confirmed shared-path errors and freeze the next candidate; verify actual
  dynamic delegation with an independently owned server before a long comparison.
- Measure useful Host-only recovery, consumption delay, copy loss/recomputation,
  tail-excluded throughput and mean workflow JCT on the new24-root run.
- Stop and fix material experimental failures promptly; CPU improvements alone
  do not establish GPU performance or a gain over native.

V2047/V2048 read each plain native parent field once per node step. Keep key order,
root/None handling, missing keys, cycles and the existing 4096 traversal bound;
recapture topology on every call. Complete records, expectations, unacknowledged
tracking and native lookup counts match in all 32 dispatch scenes; 31 P50s improve
with one retained 1.907us regression. Native-budget 64-node JOIN/tool are 2150.030
to 2097.084us and 2170.149 to 2117.223us. Native-key callback controls preserve
all proof/lifecycle fields, with retained 0.023/0.034/0.084us regressions.
Pin the old static key-path helpers as well as callback methods in the callback
microbenchmark; its new provenance check rejects current-helper reuse. Earlier V2044
changed no helper, so its scope remains valid. All 2398 checks pass in V2049.
See V2050. Commit and republish the same 06:00 qualification, freeze the startup
candidate, and prioritize the new GPU trace's failures, recovery pressure and actual
consumption. Keep the 24-root engine/configuration and pending GPU/JCT acceptance.

V2043/V2044 replace Python per-key length generators with builtin map in issuance
tracking and prefill records, preserving paths, len order, None/errors and all native
proofs. No cross-call length cache is added. All complete dispatch records, expectations,
unacknowledged tracking and native reads match; 29/32 CPU P50s improve, with retained
0.696/2.303/0.448us regressions. Native-budget 64-node JOIN/tool are 2189.146 to
2150.280us and 2202.007 to 2151.020us. All 12 actual native-key callback scenes with
token diagnostics off improve 3.93%-8.35%; retain opt-in controls' three regressions,
including 27.572us/1.05%. Pin baseline tracker class methods to the old revision when
tracker code changes; the new provenance check rejects reuse of current callback/path
methods. Unchanged module constants remain shared, so this fixture cannot cover changed
constants. V2039 changed runtime only and its shared unchanged tracker scope remains valid.
All 2393 checks pass in V2045. Prefill recording has the same transform but no separate
performance measurement. See V2046. Commit main and republish the same 06:00 qualification;
preserve engine/patch/workload and pending GPU/JCT acceptance.

V2039 reuses the native H2D factory's single frozen child and canonical pool bytes
for issuance metadata, removing generic multi-child dictionaries, generators and
sorting. Read the same child's total for the submission budget. Do not extend this
assumption to the general ledger or merged ACKs. Keep zero FULL pools, sidecar total
bytes and every native accounting/capacity check. Complete dispatch now includes
the real on_prefetch_issued identity tracker with token diagnostics off.
All 32 scenes preserve records, full expectations, unacknowledged tracking, targets
and native read counts; CPU P50s improve 4.32%-8.50%. Native-budget 64-node JOIN/tool
are 2367.061 to 2177.665us and 2349.675 to 2194.786us. Depth256 still enqueues only
64 nodes. Retain V2040's no-callback controls and two 1.768/0.979us regressions.
All 2392 checks pass in V2041. The cache/controller remain CPU doubles, no ACK/use
credit is invented, and call counts are not full per-field instrumentation. See V2042.
Commit main and update the same 06:00 qualification, preserving V88/patch/workload,
disabled extra token diagnostics and pending GPU/JCT acceptance.

V2034 makes additional per-target token snapshots/comparisons opt-in with
PREFETCH_PREFIX_DIAGNOSTICS=0 for new runs. Keep native node/allocation identity,
generation, materialized-prefix length, residency, pre-ACK buffering and Mamba
forward proofs. Emit captured/disabled/unavailable explicitly; missing diagnostic
prefixes do not mean missing reuse. Opt-in controls preserve independent snapshots
and previous token comparison. Freeze the setting, forward it to every arm, retain
it on resume and verify the server launch; plans predating the field retain the old
diagnostics-enabled behavior. All 24 CPU scenes preserve native reuse and ACK
lifecycle, excluding only explicit optional diagnostic fields in record comparison.
For 131K/42 targets, issuance CPU P50 is 9.376 to 0.266ms and first service 5.632 to
0.216ms, avoiding 22.544MB of token arrays, not KV transfers. Retain the 0.088us
regression for an already-skipped 262K single target and opt-in controls' 0.001-7.411us
regressions. Preserve V2033's independent-input/extra-retained-array fixture results;
V2034 reads the same native objects and records only scalar snapshot statistics.
All 2302 runtime and 84 report/resume checks pass; V2037 merges their real JUnits
with duplicate rejection (2386 total). See V2038. Commit main and republish the same
06:00 qualification, retaining engine/patch/workload and pending GPU/JCT acceptance.

V2028 uses slots for immutable restore leases and direct epoch/session comparisons,
removing temporary validation tuples. Keep separate expiry, causal, native session
generation, node generation, residency and lock checks for every receipt.
All 96 maintenance/handoff/first-service/runtime scenes preserve complete state,
records, receipts and locks; copy/deepcopy/pickle/replace keep fields. Ninety CPU
P50s improve. One context/42 receipts without diagnostics has a maintenance-cycle
P50 of 115.977 to 105.269us and a waiting scan of 107.928 to 94.763us. Preserve one
0.145us first-service regression and five nanosecond deferred-path regressions.
Retain V2027's slots-only prototype with its 18 regressions; do not attribute all
between-run changes to field comparisons. All 169 focused and 2278 expanded
checks pass (V2029/V2030). V2025/V2026 separately measure the earlier cb93eec
against 51c0f1f: all 32 dispatch P50s improve 1.51%-6.10%, all 48 recurring-cycle
P50s improve 4.81%-22.85%; retain the unchanged empty-socket control's 4.28ns
regression. These are earlier CPU scopes, not additive savings or GPU/JCT gains.
See V2031. Commit main and republish the same 06:00 qualification; preserve the
engine, patch, configuration and full performance acceptance goal.

V2021/V2022 inline H2D pool-name normalization while preserving the per-operation
mapping snapshot before both native accounting callbacks, live unit sizes, arbitrary
nonempty canonical sidecar names and duplicate-name rejection. Do not alias mutable
maps or cache geometry across operations. All 12 scalar H2D/D2H expectations match;
FULL4096 CPU P50 is 6.066 to 5.581us, with a retained 0.099us derived-sidecar regression.
D2H is unchanged control code. All 32 complete JOIN/tool scenes preserve records,
full reservations and native reads; 31 CPU P50s improve, with one 0.809us regression.
For 64 nodes, native-budget JOIN/tool without diagnostics are 1684.783 to 1655.434us
and 1687.816 to 1656.818us. All 2278 checks pass in V2023. Preserve V2019's failed
new assertion and V2020's correction to assert the existing wrapper and cause;
production error handling is unchanged. See V2024. Commit main and update the same
06:00 qualification; V88/patch/configuration remain unchanged. CPU evidence does
not establish allocator/DMA, GPU or end-to-end gains.

V2013/V2016 reduce temporary tuple/iterator work in live node validation and reuse
the creation-time type within one call. Keep every live field, bound, rejection
and scalar output; do not cache observations across calls. All 30 closure scenes
match; mean thread CPU reductions are 1.8%-7.1%. All 32 complete JOIN/tool
dispatch scenes preserve records, full expectations and native reads, with CPU
P50 reductions of 0.8%-3.8%. Use 24-workflow/8-running CPU fixtures, sparse
session leaf markers and native numpy.float64 creation times. For 64 nodes,
native-budget JOIN/tool without diagnostics are 1724.14 to 1691.81us and
1750.80 to 1707.98us. A depth-256 path still enqueues at most 64 commands.
Retain V2014's independent-namespace regressions and V2015's fixture check.
V2016 aligns command IDs and record clocks, compares emitted records and full
reservations, and retains raw samples; do not attribute all measurement changes
to UUIDs. All 2266 checks pass in V2017. See V2018. Keep the engine/patch/config
unchanged and update the existing 06:00 qualification after committing main.
This is CPU evidence; useful migration, consumption, throughput and JCT remain
to be measured on the authorized GPU run.

V2010/V2011 add a bounded 4096-token rejection filter before the existing long route
and exact namespaced HMAC. Reference the short filter by long route, retaining
all terminal/pool/namespace, expiry and recent-overflow semantics. Retain the
original direct path for at most 32 prefixes within 4096 tokens. All 60 long-prefix
and 42 supplementary scenes preserve evidence and state.
CPU P50 for 131072 tokens/64 unrelated candidates is 1.516 to 0.071ms; 187392
tokens/one unrelated candidate is 2.152 to 0.068ms. Retain the long-case maximum
regression of 0.75%/40.767us for 256 full matches and 13.613us for the small 8K
array case. Do not infer GIL or GPU/JCT gains. All 2266 checks pass in V2009.
Preserve V2004's two spawn failures from invoking
pytest.main through stdin; rerun with the real `python -m pytest` entry.
Also preserve V2001's rejected single-stage shared-head regression and V2003's
per-terminal double-index cleanup regression. This is shared background evidence
work, not a new prefetch/admission policy. Keep V88/patch/configuration unchanged,
commit on main and publish the latest qualification to the existing 06:00 waiter.
See V2008; performance acceptance remains pending on the new GPU trace.

V1998 reduces first-service evidence inventory work relative toa346b6a.
Keep context/epoch validation, return immediately for empty inventory, classify
matches/expiry in one pass, publish expired commands before matching commands,
and retain other contexts/future epochs. Do not alter token snapshots, exact
FULL/Mamba evidence or pre-ACK bookkeeping. All17 inventory scenes and32 complete
prefix scenes preserve records and pending state. Inventory CPU P50 improves
8.2%-48.2%; empty1.148 to0.611us,42 unrelated commands7.289 to3.944us.
Use the same read-only prefix buffers in both variants, copy emitted lists for
comparison and verify snapshot contents. Preserve V1994's separate-allocation
results and final regressions of1.971/0.072us; do not infer GPU/JCT gains.
All2258 CPU checks pass in V1996. V1995's16 fixture errors came from importing
installed SGLang before the qualified fixture inserted its path. Prepend the
qualified engine/python to PYTHONPATH before Python starts; retain the failed
record and do not weaken native ownership checks. See V1997 and final V1998.
The rejected byte-copy prototype is absent from production. V1999 binds actual
V85d background samples:22 Host-eviction samples average6.285ms CPU and26 request
probes6.343ms. Profile these paths next while retaining exact hash/namespace,
matching, counters and retirement; no64x extrapolation or GIL/JCT attribution.
V88/patch/configuration remain unchanged; publish this CPU-qualified revision
to the existing06:00 waiter and retain the full performance goal.

V1990 skips legacy boundary/command feature maintenance when online tool timing
has no model worker. Preserve the worker's live inputs; create boundary deques
only when absent and retire completed/ended/discarded tool-run groups.
Replay all12 complete V85d workflows,9428 events. Per-event graph, online
history, actual counters and physical state agree. Online mode avoids9336
legacy classifiers and preserves1351 supported direct timing queries.
These are not the2447 periodic forecasts counted by the live run.
All24 online scenes improve8.8%-12.0% in amortized CPU P50. With profiling off,
the event-weighted replay P50s are23.595 to20.942us, only about25ms per replay;
do not extrapolate this into online or JCT savings. Of24 worker controls, keep
the2 small regressions of0.066/0.034us. All2104 CPU checks pass without skips.
See V1992 and final V1990; V1989 is a fixture check. No native requests/sessions,
live model worker, transfer/capacity handling, service consumption or GPU were
included. V88/patch/configuration stay unchanged; publish the new CPU-qualified
revision to the same06:00 waiter. Next inspect large-prefix restore and use
evidence copying/scanning, retaining the full throughput/JCT goal.

V1987 removes four diagnostic-only poll counters relative toa9b42a2:
physical_planning_after_forward, prediction_maintenance_deferred,
tool_prefetch_scan_deferred and prefetch_lease_maintenance_deferred.
V85d reports1811270/1790449/189619/6392 increments respectively.
Their absence in future traces is not evidence of no waiting or no service.
Keep actual sweeps, transfers, failures, ACKs and first-use evidence.
Skip empty prediction maintenance and empty report/lease callbacks; allocate
pending service groups only when an unobserved command requires them.
Check inventories after prior handlers without moving clocks or caching absence.
All48 recurring-control states agree. For24-root/8-running deferred cycles,
CPU P50 is4.373 to3.351us without predictions and4.831 to3.790us with8 tool
hints; forced-due active-hint cycles are7.597 to6.808us. The48 scenes improve
4.4%-23.4%; retain the unchanged profiled empty-socket control's0.00088us
regression as variation. The2090 CPU checks pass without skips.
See V1988 and final V1987; V1985 is an initial check. No complete native
scheduler, real workers, active transfer consumption, DMA or GPU/JCT was
measured. V88/patch/configuration stay unchanged; publish this revision to
the existing06:00 waiter after commit and keep the performance goal active.

V1981 removes quadratic ordinary merged-ACK scans relative to0c6cbc6.
Reuse register-time disjoint immutable publications and the existing pending-node
union; retain split Host destinations, adjacency/generation and foreign-node
ownership checks. Build merged nodes once and preserve partial/omitted credit
semantics. No new persistent owner index or cache.
Latest V85d has105 tagged ACK groups:65 D2H/386 receipts and40 H2D/172 receipts,
with maxima8/42. All38 ledger and16 complete runtime callback states agree,
including modeled lock receipts, reads, leases and pending budgets.
Full42-receipt JOIN/tool ACK CPU P50 is1948.756 to1402.615us and1964.162
to1411.925us. The2090 main CPU checks pass; V88/patch unchanged.
See V1983. Tool/64-receipt scenes are controls, not observed tool prefetch counts
or an end-to-end gain. Keep the full performance goal active and update the
existing06:00 waiter's qualification after committing this revision.

V1974-V1976 reduce transfer-expectation construction relative to51c0f1f.
For literal single-pool FULL D2H counts, retain strict counts, geometry and
native bytes while removing generic sets/sorting/lists. Construct fixed-schema
H2D/D2H records positionally; preserve every field, Host destination object
and split-ACK check. Native accounting methods still run.
All12 local accounting results agree; FULL D2H CPU P50 is6.952 to5.213us.
Full JOIN/tool submission improves in30/32 controlled scenes by0.6%-2.9%;
retain the2 diagnostic-tool regressions of23.888/36.269us and the separate
PREPARE negative-control fluctuations. The2086 main CPU checks pass without
skips, including17 new malformed-input/metadata cases. V88 and patch unchanged.
See V1978; no new GPU, throughput or JCT evidence. Publish the new main revision
to the existing06:00 waiter after CPU qualification and preserve older snapshots.

V1970 removes repeated PREPARE registration only for an existing record with
the same frozen key object and node generation. Rebuild replaced group indices;
retain fresh/new-epoch/equal-but-distinct-key processing. Construct prefix maps
only when a new registration needs them. Publication/native reclaim still reads
live ownership, settled copies, locks and pending transfers.
All72 local states agree. Repeated64-node registration CPU P50 is76.809 to27.306us,
but26 fresh/other local scenes regress, up to20.99us; retain those and V1969.
All12 complete PREPARE scenes using24 synthetic workflows/running8 and sparse
native-style indices improve4.6%-14.4%, preserving actions, publications, reads,
counters and pending. The2069 main CPU checks pass; V88/patch unchanged.
See V1972 and final V1970. These are not GPU/allocator/DMA/JCT results.
Pause statements in earlier CPU records below describe their historical state;
the current one-time06:00 authorization above takes precedence.

V1967 reduces nonempty restore maintenance relative to4d389de. The latest
complete V85d has172 JOIN lease registrations and up to42 simultaneous
receipts for one context; these are registration populations, not sweep rates.
Use direct summary/pool loops and reuse a consumer match only for consecutive
receipts sharing the same frozen key object within one sweep. Epoch handoff
already supplies one alternative: skip the other JOIN proof query while
retaining native session-generation checks. Read tool survival evidence only
when tool-wait protection expires, not on normal first-service/JOIN release.
Keep every receipt's expiry, identity, node generation, native residency and
lock release. Out-of-timing checks retain42 node validations while eliminating
42 redundant JOIN or tool-history queries in their respective scenes.
For42 receipts/one context with diagnostics off, CPU P50 is119.981 to83.609us
for waiting maintenance,149.138 to88.720us for epoch handoff,46.489 to38.473us
for first service and138.004 to98.945us for the runtime control cycle.
All96 scenes preserve complete state, records, pending and locks. All84
non-deferred scenes improve; retain the5 small deferred regressions and both
intermediate versions/patches. The2058 main and87 native CPU checks pass,
plus41 subtests. Keep V88/tree1db6d884, patch2cbb0b46, workload and GPU pause
unchanged. No model, DMA, real writer I/O or complete native scheduler is
measured; do not extrapolate CPU percentages into throughput/JCT or restored
coverage. See V1968, final V1967 and intermediate V1965/V1966.

V1963 removes empty maintenance calls from recurring runtime control and avoids
rebuilding predictor fd inventories/differences when no worker or fd change exists.
Keep live worker fd reads, immediate removal and all original deadline positions.
Read parent demand after earlier handlers, so new demand is never hidden by a
cached absence. Directly retain the same running-batch reference at post-forward.
V85d's actual sleep_on_idle=false excludes the fd block: report that case
separately. For24 roots/8 running, no pending and deferred maintenance, CPU
P50 is4.801 to4.331us without hints and5.342 to4.901us with8 tool hints.
The sleeper-enabled/no-worker case is6.305 to4.480us, not a default-config gain.
All192 controlled loop scenes agree on visible/context state, pending, hints and
fd registration; P50 reductions range2.7%-29.6%. Two unchanged socket controls
are slightly slower and retained. The2050 main and87 native CPU checks pass,
plus41 subtests. Bind the paused
candidate to V88/tree1db6d884 and patch2cbb0b46; preserve V87 and older sources.
The engine delta is scheduler.py and its hook checks only. Do not enable idle
sleep or extrapolate CPU microseconds to DMA, JCT or throughput. This measures
runtime plus the native fd block, not complete get_next_batch_to_run, real
prediction work, active lease scans or PREPARE transfers. See V1964 and raw
V1963; V1962 only sampled the sleeper-enabled scenes.
GPU pause and the workload/trigger contract remain unchanged.

V1960 shares completion IDs from the existing runtime request scan with the
native scheduler hook, removing its second scan and finished checks. Preserve
the original batch identity and all service observations. A legacy runtime
returning None retains native cleanup; a legacy hook ignores new IDs, so
the performance change requires the new engine too. Across24 scenes and
four runtime/hook pairings, registration, visible/context state and counters
agree. Unfinished tagged8/48-request callback CPU P50 is3.230 to1.901us
and11.384 to5.161us. Counted predicates, outside timing, confirm16 to8
checks for8 requests. The2045 main CPU checks and86 native checks pass,
plus41 subtests, including pre-ACK first-service and lifecycle contracts.
At that stage the launcher/config used V87/tree db73b955 and patch130d376d;
the only engine delta from V86 is scheduler.py and its lifecycle tests.
Keep GPU paused and old sources intact. V85d's1811270 post-forward entries
and141490 recorded prefill/decode worker intervals are distinct populations;
do not use either as an unverified completion-callback count or multiply
microbench gains into JCT/throughput. See V1961 and raw V1960.

V1956-V1958 reduce planning/registration relative to82ef624. Merge pending
checks into complete node validation; compare closure cardinality only after
proving every path member belongs to the node index. Retain root equality,
cycles, Mamba anchors, input boundaries and validation beyond burst limits.
Single-pool FULL/Mamba registration validates scalar geometry without a
temporary sizes dictionary/set, retaining multi-pool accounting, alignment,
ownership, atomic reservation and ACK rules. Registering64 FULL commands
improves172.50 to151.13us. The16 H2D planner scenes improve3.3%-11.0%,
but complete observations improve only0.9%-3.1%. Complete64-node JOIN/tool
dispatch is1791.23 to1739.86us and1793.21 to1752.82us. Of40 scenes,39
improve; the diagnostics-on native-budget64-node tool case adds28.43us/1.33%.
Retain this regression and do not attribute unchanged PREPARE timing noise.
The1994 CPU checks pass without skips, including46 added boundary/atomicity
cases. See V1959. Keep GPU paused; actual tail replanning, native accounting,
registration and live-source costs remain. Do not multiply transfer-path
microseconds by every decode call or infer throughput/JCT gains.

V1950-V1955 reduce action-local ancestry capture relative to48197f2.
Read each live node identity, component/index and key once within the
capture, fuse index length/bounds validation, and avoid a per-node kwargs
dictionary when constructing the immutable summary. Keep live geometry,
ownership, type, lock/reference/session, pending, identity and cycle checks;
never cache their values across captures. Freeze the baseline observer's
helpers and classes too, rather than transplanting an old function into
current globals or importing the current observer from old physical code.
Use a plain fixed clock, avoiding mock call-history allocations in the
closure benchmark. Final V1955 shows3.8%-17.6% lower CPU P50 and4.1%-17.9%
lower means across30 scenes; retain V1950/V1953 and their outliers.
The32 complete
H2D/PREPARE observation scenes improve7.5%-16.8%, with equal plans.
Complete64-node JOIN/tool dispatch improves only3.6%/2.4%. Of40 dispatch
scenes,39 improve and one diagnostics-on legacy-budget tool scene adds
19.94us/0.91%; preserve this regression, dependency timing variation and
the isolated single-node tensor mean outlier in the raw evidence.
The1948 CPU checks pass, including real CPU native radix/reclaim checks.
See V1954 and the bound V1950-V1955 evidence. Keep GPU paused; real tail
planning, accounting, registration and live-source checks remain costs.
Do not claim completion of overhead reduction or throughput/JCT gains.

V1946-V1948 reduce per-node accounting and redundant tail work relative to
00d1114. When the entire selected plan has been submitted and its FULL
quantity equals all missing FULL in the same immutable snapshot, skip tail
indexing/replanning. Retain live-source/pending validation, actual tail work
for65/96-node paths, safe input checkpoints and all native readiness checks.
This is not a completion or admission proof. Merge FULL-only scalar validation
and use fixed-field step/child/action records, retaining metadata and dataclass
copy/serialization. Their shallow H2D instance storage totals544 to256 bytes;
do not treat this as total heap, Host or HBM savings.
Pinned native FULL accounting P50 is7.16 to5.71us. Complete64-node JOIN/tool
CPU dispatch is2170.40 to1836.43us and2138.82 to1846.12us. The40 scenes with
tail support improve2.9%-17.6%;40 without it improve1.0%-9.8%, with equal
targets/pending/budgets and9 unchanged prefill dependency decisions.
The1750 CPU checks pass, including16 real CPU native radix/reclaim checks
rerun with the qualified checkout after the initial missing-env skips.
See V1949. Keep GPU experiments paused. Closure observation, unavoidable
tail planning, registration and live-source validation remain CPU costs;
do not infer throughput/JCT or prediction-accuracy gains from these scenes.

Earlier V1941/V1942 reduce control costs relative to fc9fea7. Skip empty
prediction inventories and expiry cleanup, and probe empty sockets before
allocating the receive loop. Rearm readiness after a drained burst, retaining
immediate arrivals and profiling. Validate child pools in one pass and reuse
validated publication tuples for single-child commands. PREPARE and all H2D
issuers use one lazy random namespace plus the live PID and an unbounded
sequence; cancellation, receipt history and ledger replacement never reset it.
The24-root/8-running empty-inventory recurring P50 is6.44 to5.47us;
registering64 FULL commands is241.22 to165.56us. Complete64-node JOIN/tool
dispatch is2383.75 to2177.13us and2378.83 to2168.00us; all24 complete scenes
improve2.9%-8.9%. The1384 CPU checks pass with identity, physical targets,
budgets and ACK safety retained. The profiled empty-socket case adds about
0.03us. A single64-command dependency scene adds1.61us; V1944's12 independent
key layouts show mean P50 of26.46 to26.65us, so retain the residual variation
without claiming dependency-path savings. See V1943 and the bound raw reports.
At that checkpoint, native operation accounting, closure observation and
quantity-boundary replanning remained costs; V1946-V1948 address the
FULL-only and proven-empty-tail portions without removing all control work.
Keep the GPU pause, engine/workload and policy triggers unchanged.

V1935-V1937 optimize prefetch submission relative to3381a0e. Full-only
operations skip unused auxiliary-pool structures while still reconciling
native indices, counts and bytes. Retain an already exhausted bounded plan
for same-snapshot post-enqueue tail capacity accounting; preserve the original
fallback at the quantity boundary and do not expand tool/EOS planning.
Explicit report-stage complete plans use the same tail path. These snapshots
are not native capacity, admission or readiness permits. The pinned native
accounting helpers show full-only P50 of12.45 to9.24us. Complete16-node
JOIN/tool CPU dispatch with tail reclaim is864.93 to744.56us and913.88
to796.53us. Across24 scenes each, tail-reclaim support shows7.6%-13.9%
and the path without it shows1.9%-13.4% lower P50, with equal physical targets
and pending accounting;9 prefill dependency scenes remain equal.
The1292 CPU checks pass. See V1938; keep GPU paused and do not claim GPU/JCT
or prediction-accuracy gains from these controlled scenes.

V1931-V1933 reduce scheduler/prefetch CPU work relative to e5c660f. Skip
PREPARE capacity observation only when its wait sources and old pressure
candidates are absent; retain stale-candidate retirement and publication.
Reconcile recent ACKs without rebuilding all128 receipts, and simplify
single-node registration while retaining all validation and byte accounting.
The24-root/8-running forced-due empty-source P50 is23.78 to3.21us;
registering64 commands is302.57 to247.45us. Complete64-node JOIN/tool CPU
dispatch improves only about2.3%/2.5%. Missing-ACK bursts of4/16 commands
still cost about0.8-1.5us more; do not claim universal path improvements.
The1161 CPU checks and60 controlled comparison scenes pass, including
unchanged active PREPARE actions, tool rotation and candidate publication.
See V1934 for scope and raw evidence. V85d has1811270 post-forward entries
but558 controlled completions; neither microbench timings nor forced due
frequency establish cumulative cost or GPU/JCT gains. Keep GPU paused.

V1928 reuses an exact full-input count between compression and completion
budgeting. Keep one bounded immutable content snapshot, including argument
order/types, schema and chat template; changed/unsupported inputs recount.
Use the per-arm frontend flag and export its normalized value to the client.
Native stays off by default. Across36 reconstructed long inputs and three
iterations, each side's216 counts equal native; second-count means5.18/0.80ms,
pair means85.93/82.00ms, template renders216/108. Do not infer production hit
frequency or JCT from two deliberately consecutive calls.
V1929 indexes active tools by invocation and uses fitted event counts plus
binary survival lookup. Replay3719 real lifecycle events;36 controlled query
scenes and post-update predictions remain equal. The24/256-active query P50s
are114.83/87.85us and113.22/68.64us. These populations are controlled CPU scenes,
not measured workload frequencies or prediction-accuracy improvements.
The70 client/config and415 predictor/runtime CPU checks pass with overlap.
Keep GPU paused, automatic resume disabled, workload and dynamic delegation unchanged.

V1926 removes duplicate report-stage JOIN planning within one immutable native
snapshot. Keep the complete checkpoint cost separately from the bounded issue
plan, retaining fresh capture after reclaim/split and at enqueue. The synthetic
4/16/64/256-node selection P50 improves about9%-18% with equal targets/budgets;
do not infer scene frequency or JCT. Tool/EOS paths keep bounded planning;
V1935 retains their naturally exhausted result without an extra full scan.
V1927 inspects the tokenizer postprocessor directly rather than exporting and
parsing its whole vocabulary during cache initialization. On36 reconstructed
long inputs, previous-cache/new-cache means are101.15/84.86ms and first cold
requests743.69/229.63ms, with equal token IDs; overall medians are52.90/51.56ms.
The889 related CPU checks pass. Preserve native-cache-off main comparison,
dynamic delegation, unresolved hybrid-checkpoint attribution and the GPU pause.

V85 preparation now uses the user's 262144 total context and a95% rendered
prompt compaction threshold(249036 tokens), with cached local chat-template
counts and a completion budget that fits the remaining total window.
Keep the2048 hard graph-step budget for roots and children; do not raise it
to8192 or inherit the upstream50-turn cap. Python3.10 tool environments address
runtime features missed by AST-only qualification; preserve original source
test failures separately. Tool timing learns only from causally completed calls
in this run, with censoring and explicit unsupported tails. JOIN anticipation
uses the final direct child's notice and observed decode service, online report
length correction and measured full restore cost; retain native EOS fallback.
Critical-parent capacity is borrowable until needed, reclaimed only from native
validated idle duplicates or newly prepared waiting contexts. Do not preempt
active LLM requests or make a permanently idle partition. V1898 passes773 CPU
checks with1 optional sandbox skip; V1902 passes254 transfer/policy checks.
V84 has been interrupted as authorized, with partial native evidence retained.
V85b exposed a zero-time CDF contract error and concurrent first tokenizer
imports; fix both and keep the failed trace. V1912 passes792 CPU checks, with
the optional real sandbox check separately passed in V1904. V85c was rejected
before GPU startup by a stale refused control socket.
V85d now starts on port18490 with the same qualified e317471 code, a verified
engine mapping, one initialized tokenizer, native-consumable CDFs and complete
safe-checkpoint capacity pricing. Run predictive then matched native; GPU
performance and dataset suitability remain pending.
V1914 verifies all8 initial roots submitted and24 served agents have first
prompts115976-158641 tokens, with no workflow errors. V1915's later live prefix
records73.506GB D2H and19.338GB H2D; physical controls remain enabled. Controlled
JOIN ACKs include confirmed demand recovery and are not all predictive hits.
These early checks establish execution and migration activity, not a speedup.

Long-repository workload support is now an opt-in candidate, not an accepted
replacement performance result. V1890 binds12 distinct original LoCoBench-Agent
tasks from6 generated projects: complete source110395-149832 tokens, task mean
130780.67. Supply the immutable source to roots and all child depths, separately
from public task instructions; exclude reference answers and approaches.
V1889 passes295 CPU checks with1 existing optional real-sandbox progress test
skipped. That Python3.9 parsing was an earlier qualification step. The current
Python3.10 image passes dependency preflight and the real tool path; preserve
original project test failures, V1887's incompatible-project failure and V1888's
prior manifest. Task correctness and full interaction density remain open.
The candidate entrypoint is run_qwen35_long_repository_ab.sh:12 roots in8+4
waves900s apart, running8 with a coupled decode upper bound8, context256K,
Host200GB80:20 and report-service/EOS JOIN, online tool timing and FULL PREPARE
policies in predictive. Freeze a tail
threshold4 and warmup600s through initialization, resume and final comparison.
Native results from the old task set are not matched controls. Qualify actual
tool turns/durations, JOIN frequency, useful Host-only restoration, exposed waits
and recomputation before adopting this as the primary workload. Long supplied
material alone does not establish tool-intensive natural histories. V84's
interrupted native trace is retained separately; no performance win is claimed.
See docs/research/long_repository_workload_selection_20261019_zh.md.

V84 predictive has finished; native was intentionally interrupted for this
dataset transition, retaining its partial trace.
Predictive reports112 completed,104 native-agent-JCT eligible and8 workflows
with existing guard intervention. Correctness remains ungraded. V1882 audits
all405 ordinary agents/26350 requests separately from3 internal summaries:
agent-weighted mean prompt23909 tokens(9.12% of262144), median19312,
p9045185. Mean-prompt bucket counts below16K/16-32K/32-64K/64-128K/128-256K
are155/174/61/15/0(K=1024). Root/child/nested-child means are34277/20390/11091.
Only10 agents ever reach128K including generated output; all are roots.
The374 agents in104 eligible workflows remain83.16% below32K on average;
only6 reach128K peaks. Eligibility is not task correctness. Preserve all strata,
and distinguish the41323-token request-weighted input mean from agent means.
This is a full-lifecycle logical-context census, not unique KV occupancy or
busy-window concurrent residency. V1883 passes6 CPU auditor checks.

Correct the earlier queue/batch conflation: max_running_requests24 limits
admission and, in this TP1/PP1 engine, the whole running_batch used for decode.
There is no independently configured decode slice cap. max_queued_requestsNone
limits neither admission slots nor decode size; it leaves the pre-admission
waiting queue unbounded. Do not claim24 is optimal based on the256K ceiling or
that both dimensions were tuned separately. Assess actual running residency,
batch fill, native restore waits and useful-state recomputation before changing
either dimension. Genuine long-task validation must provide long reusable
prefixes around JOIN/tool returns during busy periods. Keep the current workload
as an ordinary-length control and preserve the live pair's frozen configuration.

V1856 binds the clean main9bd42bf execution snapshot, engine53020ce3,
inputs and checks. V1857 verifies actual server/client256K/running24/output16384/
request timeout14400s and the64+48 arrival schedule. Actual FULL capacity is
1797968 tokens/36.822GB after native profiling capped the requested1798992;
native server_info confirms token_capacity1797968 and running24. Physical actions are enabled; semantic
worker and detailed shared profiling are off. Performance acceptance remains
open. State: /home/longhao/experiment/ops/v84_256k_running24_20261019.state.json.

The following V1868-V1872 metrics describe sealed prefixes, not the final run.
V1869 observes 821.06/867.59 decode token/s and 76.04%/85.14% GPU utilization
in V84's complete 600-1200s/1200-1800s single-arm windows. Mean visible decode
context over 600-1800s is 34.47K, not 256K. Same-configuration native is pending.
V1868 seals 71 positive pre-completion FULL plans among 83 observed completed
JOIN waits, with 17 early reused events/0.368GB. Tool waits have 95 positive
plans and 24 early reused events/0.586GB. Capacity rejections still dominate
the observed JOIN submission gap; absent action-local diagnostics do not
establish absence of reclaimable cold state. Short-prefill phases in V1871
overlap and are not pure kernel timings or additive JCT.
V1878 qualifies tool-only completion bookkeeping that saves completed-request
features and EOS while removing unused per-decode token snapshots. Restore
first service, leases, handoffs and legacy progress mode remain. V1877 passes
622 CPU checks with the qualified native allocator and no skips. The updated
sibling-capacity test rejects notice-only transfers and permits a real native
stop with closed input reasoning before child RETURN. V1875's 24-request
callback CPU P50 is 13.20 to 3.17us; no GPU/JCT gain is claimed.
V1867 qualifies a main-only allocator fix: try the existing FULL reclaim that
retains live Mamba in HBM before the prior demotion fallback. The old direct
path reproduces an unexpected synchronous D2H;189 related CPU checks pass
after the fix. Candidate tree03af77d2, patch SHA1529584a. V84 retains the old
53020ce3 engine for both arms; do not switch its native to the new candidate.
Later GPU validation must measure retained Mamba pressure and end-to-end
effects. No throughput/JCT benefit is claimed and the active goal stays open.

The latest user-selected validation uses the model's native256K total context,
superseding the64K and128K candidates. Freeze window262144/keep8192/summary2048
and output16384 in both arms, with model/server context262144. The existing
lifecycle subtracts static prompt/schema, output and4096 safety tokens from the
actual history budget; do not exceed the native model limit or extend RoPE.
Explicitly forward the window, keep and model limit; compare actual manifests,
and keep legacy resume at32K if its old plan omitted the setting. V1855 passes
180 existing CPU checks; V1853/V1854 preserve prior candidates. V84 selects
the first112 tasks in original order, using64+48/3600s
arrivals to reduce the first wave from108 roots. Reduce maximum running requests
from48 to24 in both arms, freeze/restore that cap and verify actual server_info
before starting workflows. Keep Host200GB80:20 and the qualified engine.
Observe actual context growth, shared capacity, useful-prefix loss and
recomputation;256K per independent FULL is5.369GB, not proof24 can fit at once.

V1848 traces the pylint-6386 child failure to a600s client read timeout while
native generation continues through15925 output tokens, below the16384 cap.
New launches explicitly freeze a model request timeout defaulting to14400s
and compare it with both actual client manifests. Tool180s and the workflow
global deadline remain. Legacy resume keeps600s when that field is absent;
do not hot-change V83c or erase its workflow error. V83c was deliberately
stopped at2026-10-18T22:00:59-07:00; V1852 preserves the process ownership,
partial102 completed/1 error census and original traces. The old driver was
terminated before its arm so it cannot launch old native. GPU release was
confirmed. No terminal summary or end-to-end acceptance is invented.

V1840-V1845 provide a complete observed600-2400s V83c prefix, with1275.96
decode token/s and96.04% GPU utilization versus historical native1263.33/95.07%.
Output caps and application versions differ; they are development evidence
only. The restarted256K pair must provide its own common tail-excluded JCT.
Early next-request-verified FULL is2.880GB JOIN and7.599GB tool in the sealed
prefetch prefix; node JOIN ACK-to-service p50/p90 is332/651ms.
Main now skips ordering for tool-only zero/single tagged-candidate queues,
retaining capacity, identity, terminal and native dependency checks.509 CPU
checks pass; its local microsecond saving is not an online speedup. Preserve
V83c's frozen a8212c5 and keep the full performance goal active.

V1838 diagnoses why the late second wave adds little migration: native H2D
before/after3600s is2817.962/5.408GB predictive and2648.204/.293GB native.
Observed later JOINs still have Host-only FULL; observed tool waits have almost
no restoration demand and mostly serve from device. Matched FULL recomputation
is small, with unobserved loss and Mamba locations still unknown. Do not infer
backup loss from Host occupancy alone. The recommended next workload is one
108-root wave, with128 as a later pressure candidate after checking useful-prefix
loss. Do not mutate the current frozen156-task V83c arrival contract mid-run.

V83c is a preserved interrupted GPU validation, frozen at a8212c5, engine53020ce3.
Use the unchanged156-task manifest hash,108+48 arrivals, FULL1798992/Mamba513
and Host200GB80:20. Its planned native arm was not started.
V1836 binds the source, seeds, inherited CPU checks, V1834's394 EOS regressions
and V1835's actual tokenizer controls; populations overlap and are not summed.
The old V83/V83b initialization evidence stays preserved. State:
/home/longhao/experiment/ops/v83c_eos_only_20261019.state.json.
Cold startup and capacity are observed; performance acceptance remains open.

The worker-free EOS body proof must handle the actual Qwen template:
enable_thinking=False places the closing marker in input, not generated output.
Validate its recent open/close pair and reject reopened/unclosed output reasoning.
V1833 passes387 related CPU checks; actual local tokenizer controls confirm both
thinking modes. Preserve V83's pre-GPU manifest initialization failure and V83b's
intentional stop during server initialization, with no workflow performance
claim. The next freeze explicitly selects the same156-task manifest hash as V82b.

The active goal now uses native EOS only for provisional JOIN prefetch. Remove
live remaining-work inference and semantic text delivery, while retaining tool
timing, incremental FULL-only PREPARE, residency, native dependencies and bounded
restore admission. A notice alone cannot authorize H2D. Child completion checks
run at native finish, including tools, visible body, epoch/session and direct
JOIN ownership. V1829 passes 1997 related CPU checks. The shared output cap for
subsequent predictive/native runs is 16384; keep tool180s/workflow14400s.

Performance acceptance must exclude workload tails from primary metrics.
Freeze an active-workflow threshold of48 and warmup600s. After the last arrival,
find each arm's first depletion below48 and use the earlier boundary for both.
Report restricted completion latency and completion counts through that cutoff;
failures and unfinished tasks remain in the population. Preserve full JCT,
outcomes and the tail separately. V1827 gives a common V82b boundary4780.310s:
decode1003.208/1164.776 token/s and restricted mean2167.451/1930.898s,
predictive/native. Removing tails does not establish a win.

V1828 quantifies V82b's busy-window utilization variation: stddev19.319/9.972,
below80% samples30.649%/6.674%. Sampled per-prefill control CPU and utilization
correlate -0.301/-0.104 in10s bins. Treat this as association with load/geometry
confounders, not exclusive idle attribution. Next freeze latest main and run
predictive first, followed by one native arm with the same increased output cap.
Compare short-prefill/turnover cost, demand H2D, restore consumption and the
tail-excluded performance metrics. Keep the goal active until GPU evidence
establishes an improvement; no canary or new guard is required.

The V82b predictive/native pair is complete. V1822/V1825 bind identical task
sets, physical capacity, application ab951b9 and engine tree53020ce3, all 156
terminal outcomes per arm, final audits, cleanup and both timeline exports.
Predictive has 156 completed/148 strict eligible; native has 153 completed/
3 incomplete/145 strict eligible. Both original exit=1 statuses and eight
workflows with graph-budget intervention per arm remain (10/9 completion
events; event counts are not workflow counts). Native's three incomplete
roots reach 8192 output tokens with valid parsers, satisfied dependencies and
unexpired deadlines. The old cap is historical; apply the new shared cap above
and preserve ungraded quality.

Common system-eligible JCT is 2591.814/2105.767s on 153 tasks; common strict
JCT is 2386.965/1937.292s on 141 tasks (predictive/native), both about 23% worse
for predictive. Full durations are 12185.490/8204.871s. The complete 600-2400s
window also favors native, so a tool/model tail does not explain all the gap.
All realized trajectories differ; preserve workload drift and all exclusions.
Early next-request-confirmed FULL reuse is 91 JOIN waits/3.6645888GB and
89 tool waits/6.40032768GB, with post-boundary recovery kept separate.
The collection/comparison objective is complete; throughput/JCT improvement
and adaptive layer-prefix/tail execution have not been established.

The newer run-only candidate `beliefkv-v83b-main-control-candidate-20261018`
freezes bd3e58f. V1824 binds 35 local modules and the 48 native action identities:
1999 test items plus 27 subtests pass with no skips, including the actual pinned
semantic worker and cached pytest sandbox image. Nine model/seed files, external
encoder inputs and tree53020ce3 match inherited sources. It is not registered,
not launched, and has no launch waiter. The older b698a34 candidate below is
historical qualification evidence. Next GPU work should verify the combined
main fixes against the measured short-prefill/turnover and restore-consumption
gaps; CPU savings cannot be credited to frozen V82b or treated as performance
acceptance. Continue development on main; preserve the frozen pair and reports.

Main additionally skips prefill dependency parsing when no restore/admission
work exists, and uses the existing transaction owner index before scanning
unrelated steps. Live request fields, legacy sessions, target state readiness
and the native FULL pipeline remain checked. V1819 passes 1038 related CPU
tests; V1818 preserves callback targets/counts and physical state in nine cases.
The empty-path P50 is 2.771 to 0.217us and the unrelated-64-node path is 6.403
to 1.495us against 2ed2220. This microsecond saving is not an explanation for
the measured millisecond short-prefill gap. It is outside both frozen V82b and
the b698a34 candidate below; refreeze current main before any later cold run.

The run-only candidate `beliefkv-v83-main-control-candidate-20261018` now freezes
committed main at b698a34. V1813 binds 33 local test modules and native action
fences: 1978 unique test items plus 27 native subtests pass. The two original
V1809 skips are resolved by the exact identities in V1810/V1812, using the real
pinned semantic artifact and an existing pytest image. Preserve V1811's missing
pytest failure in the initially selected SymPy image. Nine model/seed files
match V82b byte-for-byte; external encoder files and tree53020ce3 are also bound.
This is CPU qualification, not a registered or launched experiment. Recheck
digests before a future cold launch; keep developing on main and preserve V82b.

V1807 counts 53522 physical dispatches in the complete 600-2400s busy window,
versus 13374006 over the whole run; the latter is not a busy-path cost estimate.
V1808's selected-method CPU overlay gives two admission calls at
2.447ms frozen/2.426ms main, with unchanged queue and completed-batch state.
It shares current helpers and excludes the native allocator, so it cannot
explain or resolve the measured online selection/run_batch difference.
Keep the short-prefill/turnover diagnosis open. Finish the current native and
its audits/HTML, then use main's common-eligible JCT reporter before choosing
the next GPU observation; the prepared candidate has no launch waiter.

Main now removes the second closure observation for a fitting JOIN/tool plan
selected in the same synchronous dispatch, only after an explicitly empty tuple
from the event drain and with unchanged cache/target identity. Cached and
previous-call tool plans are recaptured. A closure reused across reclamation is
marked for enqueue refresh; causal/timing/current budget and native per-node
allocation, ancestry, generation and transaction checks remain.
V1801 caught two reclaim-boundary failures before this qualification.
Final V1803 passes 1062 runtime CPU tests; V1805 passes 48 native action tests
and 27 subtests. V1806 binds final source and validation artifacts.

V1799 found no positive-dispatch improvement between frozen V82b and the prior
main. V1804 compares this change against 4c452ad with 31 alternating CPU samples:
diagnostics-off/native-budget JOIN/tool P50 at depth4 changes from
605.870/555.325us to 444.178/418.017us; depth64 changes from
3324.834/3358.770us to 2605.831/2615.925us. All 24 dispatch cases preserve
physical targets and pending credit, with one rather than two session snapshots
on stable paths. Selection-only costs still vary. Scalar native enqueue,
pending actions and synthetic frequencies do not establish DMA, reuse or JCT.
Keep both V82b arms frozen at ab951b9; finish the matched native collection,
compare common eligible tasks with main's updated reporter, then qualify a
future cold GPU run for main's combined fixes. Performance acceptance stays open.

At13:26 PDT, V1797 verifies native second-wave first prefill and positive decode
for all48 root requests, matching live actor start ticks and CUDA scheduler2390962.
All156 roots have arrived; the terminal prefix is93 completed/2 incomplete,
with93 system-valid/92 strict-valid and no error. V1798 binds both incomplete
roots (django-10554/11206) to8192 native output tokens and client finish_reason
length, completed child/JOIN dependencies and unexpired workflow deadlines.
pytest-6202 is completed but has one original graph-budget finalization.
Preserve shared frozen budgets and these outcomes; do not restart for output
truncation or label the partial comparison complete.

Main now shares result-pipe and process-exit readiness in both predictor workers.
It removes repeated idle is_alive/waitpid checks without adding a result-delivery
timer; exit wins over queued results, semantic exit is reaped with join(timeout=0),
and startup/inference timeouts remain. V1790 passes 1178 related runtime/report
CPU checks. V1784/V1789 use actual spawned processes/queues and preserve inputs,
replies and lifecycle; V1796 binds final source, test and benchmark hashes.
Empty-poll P50 improves; ready-result costs vary, including
an earlier semantic mean regression in V1785. No whole-run/GPU benefit is proven.
Keep this and earlier main fixes outside both frozen V82b arms.

The comparison reporter now adds common system/native-agent eligible task
intersections and JCT statistics without dropping the original completed cohort.
Unknown flags/correctness remain unknown, exclusions stay visible, and duplicate
identities/invalid completed durations are rejected. V1781 has 106 passing report
checks; V1782 is only a predictive membership self-check (156 system, 148 strict),
not a native comparison. Use the updated main reporter after native completes;
do not rewrite collection provenance or imply that intervention-filtered results
are an unbiased causal KV-policy gain.

V1787/V1788 cover both arms' complete 600-2400s windows: decode throughput is
1102.72/1263.33 token/s and GPU utilization 84.385/95.071% (predictive/native).
Sustained batch48 graph/context bins are near native; batch47 and short-prefill
turnover remain slower. V1792's 16-token work/prefix matching gives a 1.402
short-prefill worker-interval ratio, 1.617 after neighbor matching with a different
matched population. Prioritize turnover/restore/short-prefill control attribution.
These are descriptive windows, not exclusive forward timing or final paired JCT.

V1793/V1795 use existing turnover probes, not new online instrumentation.
For220/196 proven decode47-prefill1-decode48 neighbors, run_batch return to next
run_batch entry averages7.689/2.787ms, with post-launch physical wall2.284/0.00267ms.
Treat missing phases as unknown and overlap by union, not additive cost. Validate
existing main empty-restore/budget fixes on this path next; worker polling alone
does not resolve the observed selection/run_batch/turnover differences.

V1774 seals the completed predictive inputs. V1780 binds all 40354 native
lifetimes and 18902 distinct successor requests/18904 independent waits, without
missing service sequence numbers or open/unbound requests. Final Host FULL/Mamba
high water is 160/40.05GB; cumulative FULL eviction 379.93GB is not distinct useful
loss. Exact reaccess confirms 95 FULL recompute records, 5720 tokens/117.15MB;
unvisited/expired attribution and Mamba locations remain unknown. None of the
177 JOIN/3776 tool expanded last-pre-completion closures shows unbacked FULL;
shortcut observations cannot establish that population's backing.

For 1788 tool no-input-plan/later-Host-hit successors, completion-to-client-submit
P50 is 11.317ms, client-to-native 191.625ms, and queue-to-first-launch 14.060s.
Other request first-launch counts across those windows have medians 0/1/105.
For 68 JOIN capacity-refusal/later-Host-hit requests the values are 14.055ms/
100.747ms/6.768s and 0/1/43. These do not establish displaced eligible parents
or a benefit from service-slot reservation. Keep restoring/residency/near-term
admission coordination ahead of an unvalidated reservation policy. V1779 has
126 passing offline audit checks, including distinct waits sharing a successor;
wait cohorts and deduplicated request populations remain separate.

V1777 re-evaluates the completed run without relabeling sealed live prefixes:
174 natural final requests have work-error snapshots among 466 natural child
returns. Last-pre-EOS absolute error P50 is 45.621 tokens for 139 first-round
requests and 56.750 for 35 later-round requests. There are only 7 depth2/
5 coordinator cases. Of 228 first phase crossings, 173 are natural-final and
55 are other requests; 59 of 78 first estimated-work triggers precede native
EOS by more than 2s.
No missing notice inputs are observed. Do not treat structural graph support
as model calibration. Require role/depth/round diagnostics, first-trigger timing,
coverage and false alarms before adopting a work-head candidate. Continue the
same matched native, then evaluate existing main-only runtime fixes in a future
freeze; no online scans or hot model/code changes are added by this audit.

Current V82b stage: matched native is running. Predictive finishes with 156
completed/system-valid workflows, 148 strict native-agent-eligible workflows,
12185.49s duration, 466 dynamic children, 39 multi-round roots and 17 nested
workflows/depth 2. Eight workflows have graph-budget finalization; the original
native gate returns 1 and is not reinterpreted as a pass. Correctness is ungraded.
Native cold-starts at 12:19:45 PDT; V1773 verifies actual task prefill/decode and
positive completion at 12:27, scheduler 2390962, frozen ab951b9/tree 53020ce3,
matching tasks/arrivals/delegation/shared client settings and fixed pools.
Native policy admission is off and native overlap remains on. Full comparison
is pending; subsequent main-only CPU changes stay outside both frozen arms.

The work-window score probe is rejected. On the same 19546 delivered inputs/
159 natural final requests, phase/encoder remain frozen; selector coverage is
at least the baseline 8 triggers. For the reused 15-request development evaluation,
baseline 6/candidate 10 first triggers are all premature for 32 remaining tokens;
median RETURN lead worsens from 4.552s to 5.997s. Five CPU evaluator tests pass.
The new artifact remains offline at child_semantic_work_window_score_v82b_20261018_v1;
there is no nonfinal false-alarm, unseen-project or GPU acceptance claim.

Main skips the full waiting-queue identity fingerprint when the residency scan
width is zero: an empty target list is independent of queue contents. Positive
widths retain full fingerprints, rotation and live rechecks. V1770 passes 573
CPU cases using the qualified engine path, including a 512-entry non-iterable
queue and refreshed queue selection across 0/1-slot transitions. This is a
local work-elimination result, not measured whole-run control or GPU speedup.
Frozen V82b remains unchanged.

V1769 reconstructs40000 closed native request lifetimes from V1754's sealed
prefix; all18663 next-request targets bind. No service sequence is missing.
The2 unbound and1 unclosed requests start after every target launch.
For1788 tool no-input-plan/later-Host-hit requests, queue-to-first-launch P50
is14.060s;88.80% of waiter-time overlaps at least47 launched-unfinished requests,
63.40% overlaps48. Early-and-reused FULL cohorts have JOIN91/tool89 requests
with queue P50 of57.11/86.61ms;68 JOIN capacity-refusal/later-Host-hit requests
have6.768s P50. These are different cohorts, not causal policy gains.
Prioritize validation of restore/residency/near-term admission coordination.
Observed lifetimes are not allocator/running slots or CUDA execution; do not
infer a slot reservation or preemption policy directly from them.
V1768 passes102 offline audit cases. No online instrumentation or frozen
V82b source changes are introduced; the full matched pair remains pending.

At11:47 PDT the matched V82b pair still has155 completed predictive workflows.
The remaining django-13112 has22 same-command executions totaling2701.66s, all
exit137 with7 output characters. This is repeated tool work, not one hung call
or measured H2D overhead. V1763 reproduces missing timeout diagnostics through
the actual /bin/sh wrapper. Main preserves GNU timeout's --verbose signal
diagnostic without changing command limits or execution/termination policy.
V1765 passes181 CPU cases; one explicit-image integration case is skipped.
The actual container supports --verbose. Model retry reduction remains unproven;
neither frozen V82b arm is hot-patched. Native was pending at that observation;
its current verified service is recorded above in V1773.

Main now excludes historical-only Mamba misses from empty restore planning when
FULL has no Host-only extent and the deepest safe input checkpoint is resident or
absent. Necessary state/FULL restores and unknown geometry retain authoritative
validation; historical Mamba counts remain null rather than falsely zero.
V1761 passes1382 related CPU cases. V1760's depth64 CPU fixture changes historical
empty-plan P50 from450.036 to52.439us, necessary Mamba restore472.132 to491.368us,
and FULL restore478.299 to480.717us. Preserve regressions and GPU acceptance.
This main-only change is not deployed into frozen V82b.

V1756 uses V1754's11:15-11:16 PDT sealed prefix. Of3616 tool waits whose last
pre-completion plan has no reusable input restore step,3539 expanded closures
have no Host-only FULL then.1788 successor requests later show native Host hits
almost entirely within the original safe cap. Plan-to-service P50 is14.731s,
native-arrival-to-service14.114s, and completion-to-client-submit11.317ms.
Later Host-hit counters are neither earlier restore opportunities nor physical
DMA attribution. Next inspect queue-time residency and eligible consumption;
do not extend unverified output prefixes or infer a Host-capacity cause.
At11:37 PDT155 workflows are completed;django-13112 still executes tools.
Matched native and full performance/quality acceptance remain pending.

Main removes fresh capacity/slot/byte planning when an ACK is already covered by
the same-session native lock. No new protection is allocated; covered records keep
receipt/owner/expiry/use identity and explicitly omit an allocation budget/slot role.
New/deeper locks retain live budget and native lock checks. V1752 passes1352 runtime
cases; V1748 passes239 budget/lifecycle-audit cases. V1751's24 complete CPU fixtures
preserve physical/lock/lifecycle outputs; native-budget/depth64 same-node coverage
P50 is49.884 to17.115us, ancestor coverage57.670 to24.125us. New/deeper paths do not
show comparable gains; this is not online frequency or GPU performance evidence.
V1753 records two legacy adapter-interface failures reproducing on the frozen engine;
do not call that broad suite green or alter frozen collection to hide them.
V82b stays on ab951b9 for both arms. Full pair and performance acceptance remain open.

At10:51 PDT V1743 binds151 completed results:35 multi-round roots,15 nested
workflows, depth2,425 reported child returns and21 nested JOIN completions in that
completed subset. Five unfinished workflows are not zero-round or error results.
Predictive is still running; matched native and end-to-end acceptance are pending.

V1729 freezes an independent10:26 PDT Host-status observation. Both pools reached
capacity, but cumulative FULL retirement265.19GB is not distinct useful loss.
Exact-prefix reaccess attributes3896 FULL tokens/79.79MB to recomputation;
pending/expired records and Mamba hit locations remain unknown. V1726 observes
203 closed JOIN waits,160 positive FULL plans,95 early submissions and84 early
reuse events/3.180GB. Overlapping reasons include111 HBM-capacity refusals and40
empty Host-backed missing-copy observations; the latter also includes device
residency and does not identify Host-eviction causality.

V1732 adds causal role/depth/root-round grouping. Among161 natural final requests,
last-pre-EOS work MAE P50 is44.92 tokens for128 first-round requests and56.75 for33
later-round requests. Only7 depth2 and4 coordinator requests are observed; keep
generalization unknown. Of74 first estimated-work triggers,56 remain more than2s
before native EOS. Notice-input alignment has no observed missing inputs.
V1731 passes94 CPU cases for sealed analysis, sampling-off paired clocks and sample
export. V1733 exports19546 delivered snapshots from159 natural requests.
V1735 repairs the corresponding tool-audit clock path:12597 rounds with last-second
forecasts have round-weighted absolute error P50/P90 of110.22/576.90ms;
432 multi-tool rounds yield163.71/622.98ms. This is selected trace diagnostics,
not all-tool or independent generalization proof. V1736 passes105 CPU cases.
V1737 finds no unbacked FULL nodes in163 JOIN/3607 tool expanded observations;
shortcut/expired evidence remains unknown. Of71 exact FULL recompute records,
62 cover at most64 tokens, maximum1176. Do not infer Host-loss causality from
empty-plan counts.
The fixed-project work-only fits are complete and remain undeployed. V1740 retains
all three: on the same15 final requests, last-snapshot MAE P50 changes46.96 to
58.21/direct,49.36/late-weighted and28.98/body-progress-weighted tokens. The last
candidate's first32-token triggers remain6, but median RETURN lead grows4.55s to
11.61s. Preserve phase/encoder and running V82b artifacts. Require first-trigger
timing and coverage alongside point errors; GPU acceptance stays open.
V1741 reproduces the same sealed-prefix statistics with the final auditor.
A later terminal summary never upgrades an earlier prefix to a complete run or
labels its unobserved RETURNs as false positives. V1742 passes105 CPU cases.

At09:55 PDT V82b's second48-root wave is in actual GPU service: V1715 observes
all156 workflow starts,22 roots with multiple rounds and9 nested workflows/depth2;
86 terminal results are completed, with correctness not independently graded.
V1716 binds actual prefill samples for all48 second-wave roots and live driver/
scheduler/client start ticks. Continue the same frozen predictive then native pair.
Native has not started; keep full performance, quality and completion acceptance open.

Latest main-only CPU change also parallelizes pre-completion provisional JOINs.
An in-flight ticket map retains independent ACK ownership while selecting another
legal context; RETURN and coalesced next-client submission preserve the original
commands/checkpoint. Cancellation never releases in-flight native ownership early.
Disabled parallelism/no native overlap and legacy probabilistic tickets stay serial.
V1724 passes1352 CPU cases. V1725 compares1e0bb50: controlled EOS/work signals
both select2 contexts/8 nodes rather than1/4 before RETURN or ACK. Full-frontier
dispatch P50 changes16.661 to0.643us. No online frequency, DMA, reuse or JCT gain
is established. Frozen V82b remains unchanged.

The prior main-only CPU change allows two confirmed JOIN parents to restore without
serializing on the first burst's ACK. The confirmation map retains independent
ownership/consumer identities; shared live byte/context and native pool budgets
remain authoritative. Disabled parallelism/no native overlap stays serial, and
provisional support was added afterward in V1724 above. A saturated frontier
returns without scanning confirmed waiters. V1710 passes1329 CPU cases; V1712's
controlled selector changes1 context/4 nodes to2/8 before ACK, with saturated
selector P50 2.258 to0.746us. This is not whole-run control, DMA or JCT evidence.
Frozen V82b remains ab951b9 on both arms; do not hot-deploy this change.

V1711's09:40 PDT prefix observes128 closed JOINs,114 start windows and71 early
submissions; early-and-reused JOIN/tool FULL is2.634/6.400GB. V1713 partitions
ACK-to-service using ordered actual boundaries. In12 JOIN episodes over5s,
native-arrival-to-service totals148.77s of213.16s observed intervals, not removable
JCT. Two64.53/47.81s tool-to-next-submit gaps contain paired internal-summary
requests lasting64.41/47.75s. Do not attribute them to ordinary client handoff.
Next inspect actual JOIN admission eligibility/leases and summary-induced lost
consumption; keep the second wave and full matched performance/quality pending.
V1714 matches actual commands in all12 long-ACK JOINs to pre-completion lease
expiry. Unlocking does not prove eviction. Improve early timing and actual
post-arrival readiness/cost before extending residency or claiming removable waits.
V1721's10:07 PDT sealed prefix observes170 closed JOINs,133 recorded start windows
and81 pre-completion submissions; early-and-reused JOIN/tool FULL is2.995/6.400GB.
The predictive arm is still collecting and matched native has not started.

Current GPU collection is V82b: application ab951b9, predictive then matched native,
launched at08:50 PDT and actual workflow GPU service verified at08:55. V1693
binds actual capacities/settings, frozen app/engine/artifacts, arrivals and shared
delegation contract. Three sampling-off audit paths succeed on sealed live prefixes.
physical_disabled=false and overlap support are verified; no performance or quality
acceptance yet. V82's interrupted prefix has9/108 roots entering a second round
and no nested spawn. Four rounds/depth2 remain configured targets, not results.
JOIN ACK-to-service P90 is still19.94s in that partial prefix; retain it as a next
optimization priority alongside useful FULL coverage and low control cost.

CPU work during V82b fixes a row-accounting opportunity loss: native prefill
allocates request rows before joining the decode running list. With47 running
and one occupied prefill row, the old budget incorrectly suppresses rowless
cache restoration. Count trusted occupied rows in both the bounded restore
frontier and FULL growth reserves; do not bypass native model-request admission.
V1699 executes the frozen native row allocator on CPU; V1700 passes1314 cases.
V1698's controlled dispatch changes from0 to2 pending contexts without ACKs.
The new once-per-second row-count fields add no tree scan. This is not deployed
to frozen V82b, and its online frequency/performance is not yet established.
V1701's latest sealed prefix has56 closed JOINs:51 observed start windows,
24 pre-completion submissions,15 early reuse events/0.187GB FULL. Tool has
54 start/submissions and50 early reuse events/4.391GB. Reasons overlap; do not
attribute the entire conversion gap to row accounting. Keep the matched pair
running and continue CPU diagnosis of JOIN ticket selection and consumption.

At09:28 PDT, V1704's newer live shape prefix observes37 completed workflows
among108 released roots;17 have multiple root rounds (14 two,2 three,1 four).
Five actually use coordinators/depth2, with10 grandchildren and2 completed
nested JOINs. The second48-root arrival is still pending. These observations
supersede the early prefix's lack of nesting without claiming full compliance.
Main also disables detailed tail-capacity candidate diagnostics when opportunity
sampling is off, retaining actual reclaim/receipt/reuse records. V1703 passes371
focused CPU cases; frozen V82b remains unchanged. Keep GPU throughput/JCT and
quality acceptance pending the complete matched pair.

The user's 2026-10-18 "GPU is free" instruction supersedes
the prior October 19 08:00 Asia/Shanghai reservation. V82 was launched at
08:25 PDT from frozen application `44c802f` and qualified engine tree `53020ce3`.
Run predictive first, then native on the same 156-task nested 108+48/3600s
contract. Fix device capacities to 1,798,992 FULL tokens and 513 Mamba slots;
retain 200GB Host/80:20 and existing limits. Verify actual service/settings,
early FULL reuse, ACK-to-service, useful-prefix loss and quality/performance.
Continue CPU control/conversion work on main without modifying frozen V82.
Actual layer mode is native_all_layers. Startup is not performance acceptance.
Run: experiments/raw/qwen35_nested_prefetch_two_wave_156root_20261018_v82.

V82b candidate supersedes V82's partial collection without hot-editing it.
Release the single tool selection ticket while retaining independent in-flight
ACK reconciliation so two legal tool contexts can overlap. Keep capacity,
causal/session checks, per-node receipts and same-context exclusion. Reuse the
pending-node index for unrelated native ACKs. Preserve a small paired clock in
runtime state so sampling-off JOIN/tool reports cannot block the next native arm.
V1686: 1114 CPU checks pass. V1687: 64-pending/32-node ACK P50 55.42 to 4.83us;
two fitting tool contexts select 8 rather than 4 nodes before any ACK. No online
coverage, DMA throughput or JCT gain is established. Freeze this candidate for
V82b, preserving all V82 partial evidence and the matched nested task contract.

Latest CPU phase (V1676-V1678), relative to `52656d3`: filter unbound/unserviced
stages before graph validation, preserve observed no-tool EOS outside running
batches, and reject predictions beyond the configured lead before checkpoint
planning. Merge duplicate per-node source/context authorization immediately
before registration. Copy flat scalar budget records without recursive copying.
When fresh planning extends beyond the original frontier after resident ancestors,
trim only that tail and retain safe originally selected targets; never transfer
new targets or treat a partial prefix as executable.

1343 cases plus two subtests pass. The 156-JOIN/48-running scan P50 changes from
286.68 to 130.19 us. With an in-memory writer, 64-node JOIN/tool dispatch P50
changes from 4.422/4.332 to 3.516/3.707 ms; the all-running 48-stage scan is
about 4.6 us slower. Report that limitation. These fixtures are not online
frequencies, real DMA, whole-run control cost, RETURN accuracy or JCT evidence.
Next GPU acceptance remains early FULL reuse, ACK-to-service, repeated demand
loads, useful KV loss/recomputation and end-to-end quality/performance on the
shared nested 108+48 task contract. The one-second candidate lead and reservation
remain unchanged; engine layer prefix/tail is still unimplemented.

Current CPU phase (V1670/V1671) supersedes historical research-only goals.
Against `7f1509e`, remove repeated registration work and retain safe originally
selected JOIN/tool targets after final capacity/residency changes. Node ownership
is indexed for pending commands; fixed-age expiry stops at the first live entry;
confirmed JOIN authorization precedes unrelated visible-request traversal.
Final enqueue still drains events, recaptures the checkpoint and checks native
allocation. It may retain fitting whole FULL extents and defer Mamba, never add
targets, skip a missing ancestor, grant admission, or claim partial-layer loading.

Validation: 1316 CPU cases plus two subtests pass. Twenty-four positive dispatch
cases, 101 timed samples each, preserve targets, pending bytes and budgets on a
stable tree. Native-budget/no-diagnostic 64-extent JOIN/tool P50 changes from
4.291/4.025 ms to 3.734/3.528 ms; four-extent changes are small. These CPU fixtures
do not establish online opportunity frequencies, DMA, reuse or end-to-end gains.
See `docs/layer_prefetch_design_zh.md` for the evidence and remaining boundaries.

Next acceptance stays on the 108+48 nested workload: count actually early FULL
and next-request reuse, outstanding recovery and ACK-to-service waits, control
cost, useful-prefix loss/recomputation, quality and all workflow outcomes. Keep
predictive first and a matching native reference for the changed task contract.
Actual layer mode remains `native_all_layers`; engine prefix/tail pause/resume
is unimplemented. No GPU launch before 2026-10-19 08:00 Asia/Shanghai.

Current steering: idea research first, with an OSDI-level systems contribution
as the selection target. Do not continue GPU experiments or automatically
restart V81 during this research phase. This does not pause the overall goal.
V1456 preserves the stop request and exact owned process identities; V1457
verifies supervisor/client/server/driver/scheduler absent and no GPU compute
processes at22:30. State is stopped_by_user. Preserve frozen app87ce506,
engine53020ce3, registration/startup/NUMA and partial traces. No complete
V81 workload or performance result exists; V1452 must not be fabricated.

Research deliverable precedes implementation: a concrete workload phenomenon,
the existing abstraction/algorithm that fails on it, a defensible new mechanism,
strong simple and published baselines, and a CPU/trace falsification plan.
Require a model-general systems argument, natural task demand and material
end-to-end headroom. Existing workflow scheduling, graph orchestration, async
writeback and ordinary GPU overlap cannot be renamed as new contributions.
Agentix(NSDI26), Murakkab(OSDI26), Parrot(OSDI24) and NanoFlow(OSDI25) are
newly verified primary baselines, alongside TokenCake/CONCUR/SAGA.

V1458 audits the latest complete V79b using existing CPU-only tooling:
44.332M actual extend tokens across41046 prefill batches,21.133M(47.669%)
associated with compaction and its first successor. This is a cost object,
not an avoidable-work count, GPU-time fraction or speedup estimate.
Do not reinstate the summary-prompt prototype as paper novelty; prior work and
the mathematical conditions for state/compute reuse must be checked first.
Screening4.64 records the research-first scope and initial exclusion matrix.

V1453 adds complete3600-4200s and4200-4800s windows: V79b FULL H2D totals0.894GB
while D2H totals404.086GB. Queue pressure falls sharply after the earlier
restore spike. Different realized trajectories and observation overhead remain;
this is not performance evidence for a new cache mechanism. Qualify persistent
useful FULL recovery and native exposed stalls in V81 before implementation.
V79b finished at22:21:43:156 completed,145 native-JCT eligible,11 guard-assisted
completions including3 also missing semantic completion. Preserve exit1,
every task and original eligibility; official quality is not yet scored.
V1454 raw all-terminal mean1910.675s versus1665.374s native is not a matched
quality result. Native H2D879.088/13.610GB and differing realized work do not
prove an observation-overhead cause or a new policy benefit. Screening4.63
records complete collection, phase evidence and remaining acceptance work.

Next workload candidate: shorten the Poisson mean interval from15s to10s,
rate0.1 root/s, retaining the156 distinct tasks, exact task order and seed21.
The profile is configs/migration/qwen35_native_poisson_156root_mean10s_2026-10-16.json.
V1446 binds the reference/profile and the generated arrival table; the existing
client loader verifies all156 identities and its digest. That candidate now
was partially exercised by stopped V81; V79b retained its original15s input
through complete collection. The10s stream remains a future workload candidate.

First qualify this offered load with native and the same device/Host budgets,
running48, context/output limits and task behavior. Measure useful FULL writes
and later restores, cache eviction/recomputation, queue/service delay and time
spent under migration pressure. Historical native has1.935TB D2H but only13.610GB
H2D, including3.952GB FULL; increasing backup volume alone does not meet the need.
Do not use old15s native as a matched10s performance control. No new predictor,
PREPARE or policy is mixed into workload qualification.

This is one development candidate, not an established optimum or a rate sweep.
The final scheduled arrival moves from2477.769s to1651.846s; a finite compressed
trace may have a longer drain fraction. If sustained pressure is still absent,
extend the supply of distinct real tasks before further shrinking the interval.
Screening4.62 records exact arrival counts and interpretation constraints.

Current steering supersedes the hybrid-state candidate below. Withdraw
actual-successor-prefix Host admission/retirement as the research direction;
do not substitute another Mamba-only policy or restore the withdrawn
branch-admission/complete-state-budget/parent-child-handoff agenda.

A previously proposed screening object is migration blocking and interference:
can capacity writeback for one agent continuation delay unrelated runnable
requests, and does substantial exposed cost remain in a correctly configured
native baseline? First distinguish existing async-DMA engineering from a new
systems mechanism. CachedAttention already has async saving/write buffering;
LMCache has async retrieval and allocator-budget backpressure; KVBM has
event-driven block lifecycles. Simple async conversion is not a contribution.
Screening4.61 records primary sources, candidate boundaries and falsification.
This is one unselected phenomenon; current research remains open to other
model-general systems problems under the stronger requirements in4.64.

V1431-V1441 seal verified-window geometry and selection/transfer association
through3600s. Positive FULL Host samples show expensive capacity-recovery
intervals, but they are from the diagnostic arm, not a causal estimate of
native headroom. Next audit native exposed stalls and physical recovery
identities before implementing a policy. Do not extrapolate sampled CPU,
transfer-stream sums or diagnostic churn into JCT or promised speedup.
V1443-V1445 exact adjacent-forward accounting finds2.831/2.852ms from
prefill run return to the next run entry in the47->1->48 cohort; this is
neither GPU idle nor a measurement of the earlier expensive selection.
An isolated mechanism is justified only if a strong async baseline still
leaves material cost at equal memory, tasks, quality and recomputation.
The study must establish applicability to pure Attention, not merely claim
that a FULL code path exists in a hybrid model.

V80 automatic continuation is cancelled before GPU at21:53:57 PDT.
V1442 preserves the original waiting state, exact PID/start_ticks, registration
and launcher hash. Driver/GPU were not started; no performance result exists.
Retain its implementation and CPU qualification for an independently justified
future control. V79b remains frozen and running. This is not a global pause.
The V80-waiting and hybrid-candidate statements below are historical snapshots.

V79b now has sealed service/compatibility evidence through3000s. Native H2D
changes from0.421GB at1800-2400s to821.767GB at2400-3000s, versus7.190GB in the
historical native latter window. Do not extrapolate early zero-restore windows.
V1421 retains211.325GB of aggregate Mamba writes as unassignable; no proportional
role attribution or global useless-state claim is allowed. V1410/V1411/V1420/V1421
bind the new windows. Diagnostic overhead and changed realized work remain open.

The independent V80 no-Host native engineering control is registered at21:25 PDT,
not GPU started. V1423 passes301 cases, including actual shell argument
capture for fixed FULL1798992/Mamba513 device limits, no-Host telemetry, NUMA,
and rejection of mixed policy/capacity/arrival configurations. V1412 preserves
an initial test-field-name failure; V1413/V1422's301 passes are intermediate and
overlap V1423. The final source preserves legacy no-Host admission-only launch;
only the new no-Host experiment selector is native-only. No receipt is overwritten.
V1414-V1416 bind a clean separate application3700fa4 and unchanged53020ce3 engine.
Supervisor3176720/start_ticks638138015 is actually live at nice0, waiting for
complete V79b and actual resource release. Preserve full156 tasks, arrivals, session,
HBM and task limits; Host allocation0 is the intentional variable under the same
200GB available Host budget. Disable all extra diagnostics/policies. Verify
actual service, zero Host allocation, physical HBM and NUMA before accepting
startup. This control may reveal useful-recompute cost; retain all outcomes.
Screening4.59/4.60 records the scope. The actual-successor cache policy remains
unimplemented and novelty/performance goals remain unachieved.

V79b now has three complete600s compatibility windows through1800s.
V1405/V1403/V1409 associate100.511/122.726/117.124GB Mamba writes with previously
observed output-tail checkpoints. For95.296/110.170/104.825GB of write units,
successor inputs appeared inside the window before submit and all observed
comparisons were incompatible. Input enqueue time is not asynchronous result
availability; historical node association is not physical-generation reuse or
safe discard. V1407 passes57 CPU cases; prior48/51/56-case receipts overlap.

The next systems candidate is actual-successor-prefix feedback for hybrid-state
Host admission/retirement, preserving usable input anchors and native reference
rules. It is not implemented/deployed and is not yet a defensible paper
contribution. First qualify native without Host offload, the existing native
path cap and a reasonable hybrid adaptation of capacity-conditioned admission.
Per-path furthest-two retains the best compatible checkpoint in3992/3993,
4829/4833 and4729/4736 observations, so simple alternatives must remain strong.
These selections are not native-cap execution or a global cache simulation.

Keep V79b frozen; next inspect later pressure and exposed writeback cost, then
prepare only an isolated candidate. Require full task quality, useful throughput,
JCT and recomputation evidence. Do not call byte reduction or repairing our own
control overhead novelty, and do not force application work to manufacture gains.
Screening4.56/4.57 and V1401 bind these necessary conditions and prior-work limits.
V77 official grading is now complete:90 resolved,65 unresolved,one empty patch,
no evaluator errors/incomplete; measurement_complete remains false. V1406 and
screening4.58 preserve all156 IDs, original labels and the finished worker state.

Current continuation is V79b, not the stopped original V79. The original had
actual service/probe observations but failed NUMA qualification because the
observer resolved ops relative to a .worktrees source location. V1386 preserves
the failure and partial data. Main now resolves operations helpers from the
run-root ancestry; the frozen application/engine/helper are unchanged.

V1387/V1388 register the external observer retry in fresh output, preserving
the exact original arrival-file reference and every setting except RUN_ROOT.
V1393 passes56 CPU cases, including configured environment derivation; the
initial55-case receipt V1392 is retained and overlapping. V1394 binds sources
and receipts. Supervisor2048213 runs at nice0; driver2048545 launched at20:17 PDT.
At20:20:30 PDT V1389/V1391 verify actual probe/service, matching capacity and
NUMA; scheduler2048787 is running, and frozen source is revalidated. Retain the
original failed state and never append partial V79 traces into V79b. This is
operational repair, not performance evidence or a newly selected paper mechanism.
Screening4.55 records this supersession of earlier V79-started snapshots.

V78b completed collection at20:05 PDT:155 completed,one incomplete,all156 runner
finishes and146 explicitly native-JCT-eligible tasks. V1385's final raw runner
mean is1764.123s versus1666.324s native,5.87% slower. Preserve this negative
result and the distinct result.duration mean1763.135/1665.374s. Native H2D
is339.650/13.610GB; predictive/handoff transfers are zero.

V1383 measures observed intervening logical contexts by serialized first-service
order, not backdated queue timestamps. All rapid same-context continuations
remain in the cohort when epochs advance; the original empty equal-epoch cohort
is preserved in V1381. V1382 passes20 CPU cases. Rapid positive FULL Host
counts match V1356 exactly:569/3229 V78b,1328/3243 V76,7/3475 native.
Prompt-length/competition cells still differ, but these are historical
associations, not physical LRU distance, deduplicated working sets or causal
cache-policy evidence. Do not tune a capacity threshold from these bins.

V79 automatically launched driver2032542 at20:06 PDT after verified complete
V78b collection and resource release. Retain its frozen candidate and validate
actual continuation-probe service, capacity and NUMA through V1370/V1372.
Next identify real prefix/checkpoint compatibility and restoration provenance
before selecting a cache policy; the systems contribution remains unchosen.
Screening4.53/4.54 and V1384 preserve sources, corrected cohorts and full results.
Older waiting/insufficient6000s coverage notes below are historical snapshots.

2026-10-16 V79 is now independently registered, still waiting, not GPU started.
V1367/V1368 bind clean candidatec40d2f6, unchanged engine53020ce3, exact native
tasks/arrivals/resources and257 CPU cases. V1369 binds supervisor1906740/
start_ticks637518375; actual ps confirms nice0 and waiting_for_complete_v78b.
V79 source/environment passes revalidation at19:45 PDT. V78b's three processes
and original V77 grading worker remain live; no frozen sources or gates changed.

V1377 retains all156 tasks at5400s: V78b143 completed/one incomplete, native140
completed, runner-capped means1708.632/1613.809s. Native's fully observed runner
mean is1666.324s, so the V78b final raw runner mean cannot outperform it. This is
not a quality-matched metric or the result.duration mean; do not mix them.
V1375 covers5400-6000s service with small weighted batches6.687/9.209 and only
0.148/0GB H2D. The6000s task audit correctly refuses insufficient arrival/finish
coverage; V1376 is unwritten. Keep5400s task evidence and continue full collection
rather than stopping a slower experiment. Screening4.51/4.52 and V1378 preserve
registration, the raw lower bound and their limits. Overall novelty, quality and
performance acceptance remain open.

2026-10-16 V79 full native diagnostic is CPU prepared, not yet GPU registered.
V1374 passes257 cases, including the existing203 probe/auditor cases,39 driver
cases and15 lifecycle-window cases. Earlier V1366/V1373 pass253/256 cases;
overlapping receipts are not cumulative. A relocated-helper check catches the
main-directory derivation problem before freezing. The driver now retains the
authoritative experiment base when executed from its isolated worktree.

Freeze a separate candidate with the exact V75 engine53020ce3, task manifest,
156-task Poisson arrival table, pool geometry, tool180s/root14400s/output8192,
write-back32 and unchanged native policy. Enable only checkpoint-path and exact
generated-prefix compatibility diagnostics; no prefetch, PREPARE, native-close
GC, path cap, frontier batching or additional ordering. Startup must verify the
actual probe event, server configuration, NUMA and physical capacity, not env alone.
Start only after actual V78b supervisor/driver exit, complete clean156-task
collection, verified frozen source and resource release. Preserve native-gate
exit1 and original task labels when collection is complete; never call that
performance acceptance. Registration and actual GPU startup remain separate.

V1365 at4800s retains all156 tasks: V78b132 completed/one incomplete versus
native131 completed, runner-capped means1643.987/1533.602s. V1364's4200-4800s
H2D is1.000/1.143/1.286GB across V78b/V76/native, with different remaining
work and batches. The overall goal remains unachieved. Screening4.49/4.50
defines what the next diagnostic can prove and its cross-session limitations.

2026-10-16 at19:14:33 PDT the original V75/V76 grading queue completed normally.
Predictive has98 resolved,56 unresolved and2 empty patches; native has95/59/2,
V76 has101/54/1, each retaining all156 tasks and no evaluator errors/incomplete.
Frozen correctly_completed_ids counts are95/90/97 respectively. All three still
have measurement_complete=false and null useful completion throughput; do not
relax labels, remove tasks or substitute resolved counts for quality-matched
performance. V77 grading automatically starts worker1604042/start_ticks637354434,
confirmed live. V1363 seals the new summary and denominator checks. Earlier
running/waiting descriptions below are historical snapshots; screening4.48 is
the current grading status. The full research/performance objective remains open.

2026-10-16 latest V78b evidence takes priority over old V77 snapshots. V1353's
2400-3000s native-order/V76/native H2D is275.811/637.786/7.190GB despite
disabled extra ordering and prefetch. Full48, context-matched graph cohorts
remain close; short-prefill selection CPU is concentrated in positive Host
requests. V1357 has15 positive samples averaging94.150ms, versus4.551ms for77
known-zero samples. This identifies an association, not exclusive function cost.

The new read-only lifecycle auditor requires complete runtime-state coverage,
retains exact event windows separately from preceding snapshot deltas, and passes
15 CPU cases. V1359 records no new waiting demotion in this interval on either
arm, with prior cumulative counts457/516 preserved. Do not rewrite a supposed
contemporaneous demotion bug without actual victim evidence. Earlier actions,
ordinary eviction, reference release and terminal reclamation remain possible
contributors; observed accounting does not establish physical-copy generations.

At4200s V1361 keeps all156 due tasks:120/115/124 recorded completions, with
1542.452/1704.592/1426.191s capped runner means. The one V78b incomplete,
pylint-7080, has final finish_reason=length and an unexpired workflow deadline;
retain the outcome, output8192 and all task denominators. Frozen V78b source/env
and live experiment/grading processes pass revalidation at19:13 PDT.
V1360's complete3600-4200s window then has only2.246/5.197/1.108GB H2D.
Native-order decode1311.798token/s exceeds native1212.677 with a larger
weighted batch43.413 versus38.553; this does not establish workflow gain or
permit ignoring the earlier restore burst.

Continue isolating useful native work and compatibility before selecting a new
policy. Existing path caps, Marconi, EfficientAgent and suffix replay must remain
strong comparators; do not present generic sparse checkpoint admission, common
write-back engineering, or repairing our own overhead as the paper contribution.
Screening4.45-4.47 records current necessary conditions and falsified explanations.
Full quality-matched performance and novelty requirements remain unachieved.

2026-10-16 main integration adds exact generated-prefix compatibility to the
default-off checkpoint-path diagnostic. Compare the prior actual input plus
generated output with the next same-scope input, then classify observed
checkpoint boundaries. Keep residency, physical-copy reuse, forward consumption
and safe discard separate. Scheduler-side output snapshots are opt-in; comparison
uses the existing bounded asynchronous history. All203 application CPU cases
pass; V1352 binds the current source and existing receipts.

Qualify the existing native Mamba path cap as a simple comparator, not a new
mechanism. V1351 records seven passing CPU cases and explicitly separates the
HEAD tree from actual patched source53020ce3. The native cap retains FULL and
Host copies and protects tail/forks/locks, but session_ref alone is not protected.
Do not assume a small cap preserves the next required input anchor. The initial
fixture failures remain in V1347; no cap policy or new diagnostic is deployed.

V1346's complete native audit has734072267 effective hits out of775765383 prompt
tokens. FULL match beyond the hybrid boundary is3086927 tokens,7.404% of uncached
input; this is candidate work, not removable JCT. The newly archived V1343/V1344
window covers1200-1800s and the1800s task cohort only, not the live run endpoint.
Screening4.42-4.44 records these limits. Preserve frozen experiments and grading;
the systems contribution and quality-matched performance goal remain unachieved.

2026-10-16 V1334 narrows write-admission evidence. Of16642 native Mamba copies
written in0-6000s,16298 are single-node assignable; only369/23.760GB are prior
safe anchors superseded by an observed new anchor. Another14888 were not
previously observed as safe anchors. Treat them as unknown, not unused output
states or safe discard candidates. Multi-node identity and future coverage stay
separate; the latter is never an online feature or action label.

An opt-in completion-path diagnostic now records node creation identity, prefix
role and scalar references, without tensor reads or cache/scheduler changes.
BELIEFKV_CHECKPOINT_PATH_SHADOW defaults to0 and is not deployed in V78b or
registered for GPU work. V1338 passes187 CPU cases; V1339 binds the candidate
and original fixture failure receipt. Sampling is synchronous at completion,
writing remains asynchronous, and actual overhead is unmeasured. Next establish
write-time state, exact compatibility and consumption before selecting a policy;
compare a reasonable Marconi/EfficientAgent composition first.

V1335's complete0-600s window has V78b/V76/native decode950.070/1001.397/932.722
token/s with weighted batches24.185/25.683/23.655 and H2D0.566/0.758/0GB.
This lightly queued phase does not isolate later restore/ordering pressure.
V1336 retains all42 due tasks:7/5/7 completed, capped runner means282.809/
287.924/272.993s. Continue frozen V78b and official grading; do not infer a
quality-matched gain or replace the run from early rates. Source/environment
and live supervisor/driver/scheduler were revalidated at18:19 PDT.
V1340's next complete600-1200s window has decode1286.643/1323.358/1212.875
token/s with weighted batches46.575/46.514/44.270 and H2D4.744/4.171/0GB.
V1341 keeps all75 due tasks at1200s:22/22/28 completed and514.824/528.719/
484.254s capped runner means. Extra ordering is not necessary for the observed
early restores, but their full cause and later scale remain unisolated.
V1342 confirms comparable context lengths and full Host Mamba pools; reported
resident_pressure still cannot establish free HBM. The source already skips
parent-first demotion when the oldest unlocked component is unreferenced.
Do not blindly implement that existing condition again. Screening4.40/4.41
records the evidence and remaining conditions.

2026-10-16 V77 is fully collected: 156 runner finishes, 155 completed and one
incomplete, no workflow exceptions, and 148 native-JCT-eligible tasks. Preserve
all guard/semantic labels and the failed native gate. Its all-terminal raw mean
is1814.634s versus1665.374s native; official quality is still pending. D2H falls
from1935.037 to1672.269GB, while H2D rises from13.610 to184.587GB. This is not a
causal or quality-matched gain.

The frozen harness correctly returns1 when its native gate fails; the V77
supervisor incorrectly conflates that with incomplete collection. An independent
recovery checks all156 results/finishes, clean drained telemetry, verified source/
startup and actual process exit, then runs the original postprocessing. V1321
preserves original exit1 and failed states, with full HTML/comparison. V1324
passes33 CPU cases, including18 recovery cases. V1322/V1323 resume the original
frozen V78 through a separate recovered state; supervisor230068 is live at17:49
PDT, but the legacy NUMA observer rejects the reactive arm and the frozen watcher
then terminates that attempt. Preserve all failed/partial artifacts. V1326/V1327
start a fresh V78b at17:57 PDT with application56f2c5e, engine53020ce3, arrival
stream and policy unchanged; only RUN_ROOT and the read-only NUMA observer differ.
V1332 passes39 CPU cases. V1328 at18:00 PDT verifies actual workflow service,
positive native ordering, zero extra ordering/prefetch and matched arrivals/
capacity; V1330 records actual NUMA. Scheduler244015 is live and frozen source/
environment checks pass again. This is startup qualification, not performance
acceptance. Official grading queues remain unchanged. Screening4.39 records
the observer failure and fresh retry.

V1319/V1320 bound completed-read sources using physical per-pool copy units,
without assuming node IDs establish generations. Native writes806.130GB FULL
and1071.564GB Mamba in0-6000s, with only3.952/9.658GB completed H2D in the entire
observed prefix. At most0.490%/0.901% of those distinct write copies can source
these reads, conservatively allowing earlier reads and duplicate CoW targets.
This is neither a hit rate, an online-safe discard set, nor removable JCT.
Marconi and EfficientAgent already cover the obvious checkpoint/Host admission
ideas. First identify online-recognizable boundaries and compare their reasonable
composition before selecting a new mechanism. Continue the V78 ordering ablation;
do not substitute a new GPU candidate or restore the withdrawn three-mechanism
agenda. Screening4.37-4.39 records the evidence and limits.
Native official grading now covers95 resolved,59 unresolved and2 empty patches,
with no evaluator error/incomplete. Preserve all156 tasks; the frozen scorer
has90 correctly_completed_ids and measurement_complete=false. The actual prior
queue worker231698 is grading V75 predictive; V77 quality continues waiting.

2026-10-16 V1310 narrows the sampled restore transition: all 11 positive-FULL-
Host V77 short-prefill selection intervals contain D2H acknowledged before
selection ends, totaling 43 batches/590 nodes/6.372GB. Their H2D ACKs all
follow selection. Native capacity write-back explicitly blocks; temporal
coincidence still does not assign exclusive function costs or removable JCT.

An independent, opt-in common-engine candidate is now CPU qualified at
98b2670/tree c8424eb3, based on exact V75/V76 tree 53020ce3. It coalesces
native-order victims across branches and newly exposed parents, with no
full-heap copy and no source release before ACK. The default remains off.
V1314 passes 196 engine CPU cases, including 34 new cases and real hybrid
mapping. V1315's 32 real-CPU FULL fixtures retain identical victims and
capacity while submissions change from chain 135 to frontier 32. These are
simulated-DMA counts, not measured GPU or workflow gains. V1316 verifies the
incremental patch reconstructs the exact candidate tree; it does not replace
the selected canonical patch or register GPU work. Preserve initial fixture
failure receipts and do not sum overlapping suites.

Continue the registered V77 -> V78 sequence without altering either frozen
candidate. V1317 at 6000s retains all 156 tasks: V77/native 149/150 recorded
completions and 1769.327/1654.199s capped runner sojourn. Official quality
and final performance remain unproved. If frontier batching is selected
after the independent ordering ablation, apply the common optimization to
both native and the proposed policy. It is an engineering baseline change,
not a selected paper mechanism. Screening 4.35/4.36 records the evidence.

2026-10-16 latest necessary-condition evidence: V1301/V1306 associate sampled
short-prefill selection cost with exact native Host counters. In V77, 11/98
positive FULL Host samples account for 79.07% of sampled selection-thread CPU:
158.750ms mean versus 5.314ms for 87 known-zero samples. V76's positive group
accounts for 86.41%. Native-close has no workflow controller, so extra ordering
alone cannot explain this concentration. Comparable full-48 decode/context/
replay cohorts remain close. Next inspect demand restore, capacity eviction
and synchronous paths; request association is not function-level attribution,
an extrapolated cost or removable JCT. Reported resident_pressure excludes
evictable cached pages and cannot establish available HBM capacity.

V1302/V1304 cover 3600-4200s: V77/native H2D drops to 2.763/1.108GB, while
their decode rates are 1307.888/1212.677 token/s with different backlog and
weighted batch sizes 46.77/38.55. V1307 keeps all 156 tasks at 4200s:
116/124 completed, capped runner sojourn 1522.430/1426.191s. The later raw
rate does not establish workflow benefit. V76's official grading now covers
101 resolved, 54 unresolved and one empty patch. Eleven original native-JCT
ineligibility labels remain; the frozen scorer correctly leaves complete
useful-throughput measurement false. Native grading is actually running,
with V77 and V78 queues unchanged. V1308 binds these receipts and verified
process incarnations. Fifty-three offline CPU cases pass across the two
new auditors and their existing geometry/phase helpers. Do not modify the
frozen GPU candidates or treat these engineering observations as a new paper
mechanism. Screening 4.33/4.34 records the limits and next attribution step.

2026-10-16 V78 is now registered as the independent native-order ablation.
Freeze application56f2c5e in the separate V1278 checkout, retaining the complete
53020ce3 engine and V76 workload, PREPARE, tool timing and terminal lifecycle.
Disable extra ordering/custom capacity bypass, keeping prefetch/handoff off;
do not combine FULL-only, native close GC or ReplaySSM. V1280 passes324 CPU
cases, including40 driver,20 ordering and151 telemetry checks. V1281/V1282
bind source, plan, receipts and helpers. Supervisor3412628/start_ticks636206265
is actually live and waiting for full successful V77, exact process exit and
resource release. This registration has no V78 GPU service or speedup evidence.
Require actual prefill_ordering_mode=native and positive native ordering counts;
configured priority booleans do not establish effective ordering. Keep V77
and both official quality queues frozen. The latest2400-3000s V77/native
window has174.643/7.190GB H2D and1093.70/1304.05 decode token/s; rapid
continuation positive FULL Host fractions are9.86%/0.20%. Extra workflow
ordering is absent in V77, so it cannot be the sole explanation. At3000s,
all156 tasks retain their denominator, with74/83 recorded completions and
1032.496/1027.185s capped runner sojourn, not final or quality-matched JCT.
Keep V78 to isolate ordering's contribution, and also inspect terminal
reclamation, hybrid-prefix recovery and realized working sets. Do not infer
GC causality merely from this historical single-run difference.
V76's1473 positive Host samples
all wait at least10s, matching a candidate aging boundary, not proven causality.
Screening4.31/4.32 records registration and current evidence.

2026-10-16 latest evidence: V77 remains frozen and running. Its first600s
has102.134GB D2H versus139.944GB native, no H2D on either side, and946.62/
932.72 decode token/s. All42 due workflows remain in the completion cohort:
9/7 recorded completions and271.664/272.993s capped runner sojourn. These are
early development observations, not quality-matched final or causal gains.
The later complete600-1200s V1277 window has168.700/226.189GB D2H yet
1211.45/1212.88 decode token/s for V77/native. Both still have no H2D;
lower traffic is not a substitute for observed system performance.
V76's complete all156 raw duration mean is1974.421s versus1665.374s native;
official quality grading is running. V1275 adds the missing zero/unknown-Host
denominator: among served requests queued in2400-3000s within1s of their
previous completion, positive FULL Host fractions are40.95%/0.20%/6.12% for
V76/native/predictive.85 CPU cases pass; existing positive-Host service-window
fields exactly match V1256. Queue cohorts and service cohorts are distinct.
Prioritize a future isolated native-order comparison with the same53020ce3
engine, PREPARE/lifecycle retained and prefetch/handoff off. Do not combine
FULL-only, native close GC or ReplaySSM. No new GPU run is registered; keep
V77 and both quality queues unchanged. V1276 binds evidence including V1277;
screening4.29/4.30 records the next falsification. Earlier main integration,
ReplaySSM source correction and recovery provenance remain in4.26-4.28.
Timestamped entries below retain their historical observation scope.

2026-10-16 14:50 PDT: V1256/V1257 extend the same complete2400-3000s cohort
with immediately preceding request completion and safe-input checkpoint evidence.
All1473/34 V76/native positive Host requests associate with the previous
checkpoint on the current effective matched path; existing V1246 row fields match.
Positive-Host completion-to-next-queue medians are0.400/547.129s; queue-to-service
observations15.574/6.387s. Requests queued within1s account for389.613/0.935GB
reported FULL demand. Isolate rapid continuation residency and admission before
attributing this to input-format loss. Missing checkpoint generation and page
evidence prevent a physical reuse/waste or removable-JCT claim. V1255 passes63
offline cases; after closed-cohort coverage correction V1258 passes66, not129.
V1259 compares7000s without fabricating native log time: its complete planned
cohort covers later horizons. V76/native raw runner capped sojourn1963.595/1666.324s
already rules out reversing this raw all-task mean by finishing the last workflow;
quality-matched JCT is still unknown. Keep frozen V76/V77 and quality queues;
155 runner finishes are observed, the last workflow remains active. See4.24/4.25.

2026-10-16 14:22 PDT: independent ordering candidate b698416 is now on main.
BELIEFKV_NATIVE_PREFILL_ORDER=1 is read once at startup; default0 retains
workflow ordering. Native mode skips extra priorities/aging and custom capacity
bypass, retaining identity/terminal checks, native PrefillAdder, PREPARE and
lifecycle callbacks. Low-frequency state reports prefill_ordering_mode; existing
priority booleans remain configuration. V1250 passes599 CPU cases, including20
new mode checks. Four initial failures reproduce on unchanged main; two test
fixtures now explicitly enable the callback they measure, without broadening
production dispatch. V1252 revalidates frozen apps/engines and29 registered hashes.
No GPU registration or launch selection changed. Keep the53020ce3 engine for a
future isolated ordering comparison; do not mix in waiting-full-only or close GC.
V1251's full6000s cohort has149/150 V76/native recorded completions and capped
sojourn means1943.412/1654.351s; quality remains ungraded. Finish V76/V77 and
existing quality collection before selecting the next GPU candidate. See4.22.

2026-10-16 local-main integration: preserve the two completed auditors, their
tests and sealed V1241-V1246 evidence on main. V1247 passes all46 CPU cases.
The all-due cohort keeps unfinished and failed tasks in the denominator:
at3600s V76/native recorded completions are80/105 out of156. These are runner
completion observations, not quality-matched JCT. In2400-3000s V76/native
demand H2D is637.786/7.190GB; matched full48 decode intervals are close, while
47-request intervals neighboring prefill differ. Isolate ordering, lifecycle
and request turnover before assigning cause or selecting another GPU candidate.
Native-prefill-order ablation remains unimplemented; no experiment registration
or frozen source changes are part of this integration. See screening4.21.

2026-10-16 13:35 PDT: V1233/V1234 seal V76 waiting-state demotions and following
native requests.516 records span142 context epochs;112 have same-node H2D and
following match-path associations, with no observed reload/repark sequence before
that service. JOIN release-to-service observation P50 is366.300s, tool2.773s;
missing boundaries and node generation remain unknown. These are not reuse proofs.
V1235's complete1200-1800s window has V76/native decode1339.85/1333.46token/s,
weighted batch about47.5 and prefill worker totals120.821/123.336s. Different
realized work prevents a causal or complete quality-matched result. V1238 control
self-wall totals32.778/49.376s for V76/V75-predictive are not removable JCT.
The separate-pool FULL eviction path cascaded264 live Mamba releases. Independent
engine candidate de4118f/tree35cf6411 preserves state when no Mamba deficit remains,
retaining native joint-deficit and later state-pressure backup/reclaim paths.
V1237 covers236 regressions/27 subtests; V1239 covers13 final candidate checks,
including later native backup and reclamation.237 unique testcase identities across
the overlapping suites pass. V1240 binds the incremental waiting-full-only patch,
CPU evidence and unchanged V75/V76/V77 application/engine/29 registered file hashes.
This is an engineering candidate, not novelty or GPU gain; no new launch selection
or experiment is registered. Complete frozen V76, then V77 and official quality
before selecting the next GPU candidate or a paper mechanism. See screening4.20.

2026-10-16 12:52 PDT: V1228 seals complete first600s control prefixes with the
existing read-only monitor/control functions. V76/V75-predictive Python self
wall totals15.043/25.377s over actual snapshot intervals600.617/600.036s.
Different work is observed; this is not thread CPU, exposed stall or net JCT.
Remaining V76 phases include control_drain3.343s, maintenance3.059s,
event_apply2.671s and join_prepare1.693s. V1229 observes1001.40/932.72 decode
token/s for V76/native, with weighted batch25.68/23.66 and different input/
generation. V76 has5 demand H2D batches/0.758GB and1.781GB PREPARE; native has
no H2D in this window. Keep source frozen and inspect later pressured windows,
state-movement attribution and complete quality rather than treating this early
positive ordering as a result. Screening4.19 records remaining uncertainty.

2026-10-16 12:41 PDT: the full V75 pair and postprocessing are complete,
driver exit0 and no observer errors. Predictive/native outcomes are155/153
completed and1/3 incomplete. All-task raw duration means1806.897/1665.374s
include incomplete/guard labels; completed throughput is70.462/80.506wf/h.
Official quality is ungraded and these are not quality-matched JCT gains.
V1226 binds full summaries/comparison/transfers and three native failures:
the last two root responses arelength, deadlines unexpired; retain8192 and labels.
V76 launched12:34:09 and actual startup V1166 verified12:37:09, scheduler1102179.
Keep its frozen no-prefetch configuration and the queued V77 baseline unchanged.
V1223/V1225 now register an independent V77 official-quality queue;17 CPU cases
and actual quality-venv composition pass. Supervisor1121673/start_ticks634983330
is live and waits for full V77 plus the original grading series, including any
future live drivers/workers. Use the unchanged frozen evaluator/exporter/scorer,
all156 patches/outcomes and one worker/1 core/8GiB/no GPU. Do not relax
measurement_complete for incomplete or guard-labeled workflows. Future V1222
inputs and V1224 environment are not grading results. See V1227 and research
screening4.17/4.18; selected novelty and quality-matched performance remain open.

2026-10-16 12:20 PDT: V77 native-close is frozen at application52a2b8e and
engine tree76cf97d2. The independent native-only driver passes34 new CPU cases;
V1214 binds the existing103 engine/151 telemetry/58 launcher checks and sources.
V1215 registers the same156 tasks, Poisson table and38 matched configuration
fields, batch32, Host80:20 and unchanged model/prompt/tool code. The complete
frozen plan is archived as V1220 with the registered raw-plan digest. Enable
native session-close reclamation with native admission; leave workflow control,
PREPARE/prefetch/priorities/semantic/output-ID collection off. Keep existing
announcements and termination labels. This is a strong simple engineering
baseline, not a new paper mechanism or a prefetch-only causal ablation.
Supervisor1076769/start_ticks634866646 is actually live and waiting for complete
V76. V75 native is still running. Start only after successful complete V76
postprocessing, exact predecessor process exit and resource/port release.
Verify actual close-GC activation, service, arrivals, capacities, NUMA and native
configuration after launch. V1216/V1217/V1219 are future startup/completion/
placement outputs. V77 GPU has not started. Existing one-core/8GiB CPU quality
grading may overlap this development run; record it and do not claim matched
formal resources. Grade all156 V77 tasks separately after its complete run,
without editing the frozen V75/V76 quality queue. Compare complete quality,
all-task outcomes/JCT, useful throughput and costs before selecting a mechanism.
Research screening4.16 records the falsification criteria; the broad goal is active.

2026-10-16 11:56 PDT: qualify the simple native-close baseline launch path.
Use explicit native_session_close flavor with its combined patch over pinned
HEAD94602c9; the isolated V1210 engine reconstructs exactly tree76cf97d2,
already covered by the103-case V1205 suite.58 launcher/NUMA/provenance cases
pass, plus actual-import CPU checks for disabled/enabled close reclamation.
Reject a missing source checkout or a feature/patch mismatch before server exec.
V1211/V1212 bind source/patch/evidence; no new GPU run is registered. Future single-arm
collection still needs a separate frozen application, matched plan and registration;
do not use unsupported ARM_ORDER=native in the existing pair driver.
Keep the current V75/V76/quality order and registered helpers unchanged.
V1209's latest complete3000-4200s window shows1293.30/1273.97 decode token/s,
306.329/421.349GB D2H and38.957/62.568s D2H stream time, predictive/native.
All H2D stream time is only0.222/0.234s. Input work and queues differ; these
numbers motivate lifecycle/write-back controls, not causal savings or novelty.
V75 native and its waiters remain actually live; full quality/JCT are pending.
Continue systems research and strong-simple-baseline falsification before
extending control paths or selecting a new paper mechanism.

2026-10-16 local-main integration: earlier V76/audit/output-token changes are
already on main. Preserve the independent native session-close GC candidate as
patches/sglang-v0.5.20-native-session-close-gc.patch on top of the complete V75
engine tree53020ce3; candidate f1dc97d/tree76cf97d2. It is default-off and uses
explicit close, existing reference/identity checks and bounded native safe-point
retries, without a workflow controller or speculative transfer. This is a simple
engineering baseline, not the selected new paper mechanism.103 engine and151
application CPU checks pass; V1204's initial fixture failure remains preserved.
V1207 measures200 randomized close calls per mode: thread-CPU P50 is0.011349/
0.068110ms for native/GC, with32 private FULL tokens freed only by GC. Different
work is performed, so do not interpret the difference as an isolated speedup or
GPU/JCT result. V1208 binds patch, source, test and benchmark hashes. The
canonical staging patch, V75/V76 sources and registered helpers remain unchanged.
Current launchers still require94602c9 plus the canonical patch; this candidate
needs separate launch preparation and qualification before any GPU registration.
No additional GPU run is registered; quality and end-to-end acceptance remain open.

2026-10-16 11:13 PDT: V1195/V1198/V1200 compare two complete V75 windows
against its same-batch native. Full48 decode intervals are near parity; output
rate ordering changes with prefill demand, so do not infer causal prefetch gain
or removable JCT from Python self-wall sums. Continue frozen V75/V76 and quality.
Add default-off BELIEFKV_RECORD_OUTPUT_TOKEN_IDS for a future independent
candidate: immutable generated-ID evidence once per completed request, no
per-decode collection or prompt IDs.151 CPU cases pass (V1197,11 new cases).
Bind tokenizer/model, identities and causal availability before any draft replay.
AgentSpec/AgSpec and ReplaySSM already cover generic corpus speculation/state
replay. Native has relevant entry points, while current BeliefKV admission
rejects speculation; keep that check until full integration is justified and
verified. No speculative GPU run is registered. Research screening4.12/4.13
records the current gap and baseline constraints; the broad goal remains active.

2026-10-16 10:49 PDT: V75 predictive is complete (155 completed/1 incomplete).
The all156 raw workflow-duration mean is1806.897s;11 workflows carry existing
guard-intervention labels and official quality remains ungraded. The incomplete
pytest-7490 ends with finish_reason=length, not a tool/workflow timeout.
Native actual service was verified at10:37:24 with the same application, engine
and batch32; keep its frozen run, queued V76 and quality dependencies unchanged.
V1187 binds complete outcomes and transfer scope. V1193 seals470 input sources:
recorded decode/prefill worker intervals total6411.942/1226.440s, all with graph
replay; native H2D stream time totals3.899s, not removable JCT.19 CPU checks pass.
Declared topology contains367 depth1 children,200 single-waiter JOINs and29
multi-round workflows;708 internal calls are separate. Dependency topology does
not establish equal tokens/tensors or collective reuse. V1188 archives verified
TokenDance/SAGA primary sources; compare existing sharing/PIC/retention methods
before claiming novelty. Prioritize measured control and model computation,
keep prefetch as an ablation, and require exact input/state dependencies and
quality parity for any new reuse candidate. See research screening4.10/4.11.
The selected systems contribution and native-relative gains remain unproven.

2026-10-16 10:20 PDT: V1179 verifies the latest V75 reuse prefix. Only22 of768
large FULL gaps are ordinary continuations (24,741 tokens); two client identities
and one service identity remain unknown. Gaps/proxies are not avoidable FLOPs.
The official-quality queue is now registered (V1185/V1186), with live supervisor
3806721/start_ticks634129312 waiting for all recorded V75/V76 processes to exit.
It then validates complete156-task summaries and grades V76 reactive, V75 native
and V75 predictive, one CPU worker/1 core/8GiB/no GPU.29 CPU cases pass in V1180.
Keep the registered ops helper, core/exporter/scorer and GPU sources frozen;
retain error/incomplete/empty patches and independently audit official reports.
V1181-V1184 are future outputs, not completed grading. No old daily reservation
rule is restored. Research selection and quality-matched performance remain open.

2026-10-16 10:04 PDT: V1175/V1176 audit the current V75 prefix and complete
V73 native with sealed input hashes.27/89 load-consuming samples pair exactly;
10/8 show the pre-forward Mamba fence. Native's42 short samples have0.075ms
median and0.616ms maximum total layer waits. Mixed-request composition alone
does not exclude COW source-slot dependencies. Keep native waits; do not add a
pipeline rewrite or dedicated GPU run without evidence of material exposure.
V1177 verifies all156-per-arm quality inputs, official test specs and156 cached
instance images; it invokes prepare only and launches no grading worker.
Actual correctness remains ungraded. V1178 binds reused sources, execution
overrides and corrected old status text; see research screening4.8.
Continue the frozen V75 pair and queued V76; select the next systems mechanism
from remaining measured cost under the existing quality/all-task acceptance.

2026-10-16 09:40 PDT evidence update: V1171 separates actual terminal releases
from ordinary retired context checkpoints. Dynamic-child/internal-summary/root
FULL releases are12.887/236.873/4.097GB, cumulative rather than simultaneous.
Child scheduler-observation-to-release P50 is0.573ms; faster GC itself is not
supported as the main remaining bottleneck.87 CPU checks pass in V1172.
The terminal_context_cache_sample name does not establish invocation RETURN;
use schema2 checkpoint/epoch/session matching, not the superseded V1170 cohort.
Compare lifecycle and Host-write proposals against verified Lachesis and
EfficientAgent mechanisms (research screening4.6/4.7, sources V1173/V1174).
Next evaluate V75's matched-batch native and queued V76 before extending
control paths. Keep frozen applications/engine/helpers unchanged, retain all
quality/fairness/tail requirements, and do not claim the selected contribution
or end-to-end gains established. The broad systems goal remains active.

2026-10-16 09:10 PDT next candidate: V76 b2adf481 is frozen, using the unchanged
V75 engine53020ce3/batch32. It disables JOIN/tool H2D anticipation, semantic
content prediction/streaming and opportunity census; retains tool timing for
PREPARE/offload, terminal reclamation, causal/resident admission and native
demand recovery.343 distinct CPU cases pass (340 regression plus3 effective-env
checks). V1164/V1165 bind qualification and registration. Supervisor3035460
waits for the full V75 pair and postprocessing to end and resources to be free,
then runs reactive only on the identical156-task Poisson schedule and budgets.
Use V75's same-batch native as the historical development reference, not formal
paired evidence. Keep both candidates/engine/helpers frozen. V76 has no GPU
results yet; quantify net control/work/quality/JCT/throughput before selection.
Canonical evidence: research screening4.5 and ops/v76_no_prefetch_20261016.state.json.

Current execution, 2026-10-16 08:45 PDT: select systems mechanisms in the
serving/runtime execution, physical-state and transfer paths under matched
application behavior. Harness events are inputs, not the selected optimization.
V75 closed windows 600-900s/900-1200s confirm509/536 coalesced batches with
zero enqueue failures. Full48 decode is near historical native, but short
prefill geometry and CPU/control costs still need attribution; window variation
precludes a single slowdown factor. V1150-V1158 and3 new CPU audit checks are
documented in research screening4.4. Keep V75 source/engine/helper frozen;
compare against its same-cap native arm before accepting net gains. Prioritize
actual CPU dispatch, state-copy/dependency and control costs, then choose a
mechanism with demonstrated prior-art differences. The withdrawn three-mechanism
agenda and harness prototypes are not reinstated; overall goal remains active.

2026-10-16 08:23 PDT: V75 is running the frozen 2ad32d0 application with engine
tree53020ce3, predictive first then native, both with write-back batch cap32.
Cold launch08:15:55 and actual predictive GPU/source/arrival/capacity verification
08:18:46 are recorded in V1140-V1142. The final candidate has230 CPU tests and27
subtests. Keep156 V73 tasks/Poisson table and all model/application/resource
settings unchanged. Inspect attempted/eligible/issued coalescing and real DMA,
cache/recompute/control costs before interpreting JCT. After an initial period
without FULL attempts, V1149 now confirms202 multi-node D2H ACKs/865 nodes,
matching native coalescing counts with zero enqueue failures. This proves
execution, not net speedup or quality/JCT gains.
State:ops/v75_batched_writeback_20261016.state.json;source/engine/helper frozen.
V73 all156-per-arm official-quality inputs are exported in V1146/V1147 and bound
by V1148; grading has not started. No qwen_prefix or V74 launch. Goal remains
active; earlier no-new-GPU statements below are historical.

2026-10-16 07:58 PDT update: V1135 matches actual prefill geometry and client
stages. Only 29 of native's 775 large FULL-match gaps are ordinary continuations.
V1136 targets the observed 125,158 singleton write-back D2H batches with an
independent, default-off ancestor-chain backup candidate. It preserves native
device FULL ordering, required state, Host reclaim and ACK-before-free.
90 CPU tests and 27 subtests pass, including real CPU cache/tree plus simulated
DMA. The first wrong-installed-package failures are retained; the final run
asserts candidate imports. Next validate real batching opportunities, overhead,
GPU transfers and cache effects before any throughput/quality claim. This is
shared-path engineering, not established novelty; match successful common
fixes in both baselines. No GPU launch has been registered and V73 is frozen.
See the canonical research screening and V1136 candidate receipt.

Current steering, 2026-10-16 07:26 PDT: focus the paper on serving/runtime
infrastructure under unchanged application behavior. Harness summary, evidence
feedback and agent-decision prototypes are optional engineering controls, not
the selected contribution. Keep qwen_prefix off; no GPU run is queued for it.
First establish exposed costs in native, then compare against existing session,
incremental-input and hybrid-prefix-cache methods before choosing a mechanism.
V1134 audits 37,612 native and 40,186 predictive requests with 7 CPU checks.
Conversation-conversion P50 is 51.39/53.42ms; the FULL-match/effective-prefix gap
accounts for 7.26%/7.27% of uncached tokens, not attainable speedup. Only 775/797
requests have a gap of at least 512 tokens. See the current research screening
for scope, prior art and limits. V73 has now ended in both arms, each with
154 completed/2 incomplete, and all owned run processes exited. V74 remains
cancelled before launch; no replacement GPU experiment is registered.
The overall performance/quality goal is active. Earlier native-live observations
and historical three-mechanism commitments below do not override this paragraph.

V1125 completes the current non-scheduling opportunity audit; see
[the research screening](research/non_scheduling_directions_20261016_zh.md).
Actual native batch geometry confirms33.58% of V73 predictive prefill extends
belong to summaries and14.78% to first ordinary requests after compaction.
These are measured work shares, not attainable speedups or exclusively waste.
Evaluate context processing, version-dependent evidence reuse and generation
acceleration against the relevant strong existing methods before selecting a
new candidate. The read-only auditor has8 CPU passes. V73 native continues;
V74 remains cancelled and no replacement GPU experiment is registered.

Latest steering, 2026-10-16 06:04 PDT: withdraw executable-branch admission,
complete-state budgets and continuation handoff as the selected research agenda.
Keep the prototype and its evidence as unproven alternatives. FULL/Mamba pool
imbalance must first be addressed as a configuration/capacity issue, not a novelty
argument. Quantify repeated tool execution, context summarization/reprocessing,
and model inference from the latest traces before selecting another design.
Investigate beyond scheduling; retain matched native, quality and all-task JCT/
useful-throughput acceptance. The broad goal remains active.
V74 was cancelled while waiting, before any GPU arm launched; preserve its frozen
plan, application, engine and helper. V73 native continues unchanged. Receipt:
`experiments/reports/v74_cancelled_before_gpu_20261016.json`.

The V1121/V74 launch and three-mechanism plan below are historical and superseded.

V1121 freezes independent candidate ace2283 and engine tree ad3e62df for V74.
Its156 ordered tasks and Poisson table digest exactly match V73. Supervisor
1182383/start_ticks632519352 is confirmed live and waits for the entire V73
driver3179209 to exit and the GPU to become idle, then runs workflow_progress
followed by same-application native. See ops/v74_workflow_progress_20261016.state.json
and the V1121 registration. No V74 GPU work or performance result exists yet.
Keep this source, engine and newly registered helper frozen as well.

The latest authorized mainline is specified in
[the research argument](research/workflow_progress_and_state_20261016_zh.md):
dynamic executable-branch admission, complete next-step FULL/Mamba/request
resource budgets, and identity-bound child-to-parent continuation handoff.
The goal remains active; novelty and end-to-end gains are unproven.
The previous exposure/prefetch plan below is supporting evidence and optional
mechanism work, not a substitute for these three mechanisms.

1. Keep V73, its engine and registered helpers frozen. Use V1112 rather than
   V1111 for JOIN counts. Do not claim dynamic nested behavior from depth-one traces.
2. Validate the new independent progress mode at native prefill boundaries.
   Account missing and evictable resident source state, shared physical identity,
   private active/tracking Mamba slots, rows, running growth and input budgets.
   Unknowns retain native attempts; final allocation/dependencies remain native.
3. Validate the natural hierarchical application with real framework callbacks,
   two JOIN levels, private-state isolation, cancellation and epoch invalidation.
   Apply identical prompts/tools/limits to all arms; do not force useless delegation.
4. Measure the complete control cycle against FIFO/count/vector controls.
   The development vendor engine predates the V73 engine; create a fresh engine
   candidate from the latter, then add only the new scheduling hook. Never launch
   the old vendor snapshot as if it included the latest service fixes.
5. Run the new policy first, followed by matched native on the new application
   and one frozen Poisson table. Compare quality, all-task JCT/completions, tails,
   batch filling, physical waiting occupancy, repeated recovery and CPU costs.
   Add reasonable yield-on-wait, vector best-fit and program/graph-order controls
   before claiming contribution beyond TokenCake/CONCUR/SAGA.

Current progress: the independent mode and hierarchical application are implemented.
V1119 has728 application/runtime passes with1 unrun Docker case; the copied latest
engine has165 passes and14 subtests. V1120 replays all156 V73 predictive traces with
equal logical states at batch sizes1/32. These are CPU results, not quality/JCT gains.
The controller now discovers demands lazily up to the next native batch frontier.
V1118's bound-source fixture costs0.222/0.567/1.580ms at47 running and8/48/156
queued requests; cold empty batches still cost0.759/5.921/9.533ms. Reduce or
amortize repeated verified-prefix work if GPU evidence shows this cost matters.
The new sglang-v1116 engine preserves the entire V73 parent engine and adds only
the new scheduling hook/tests; the old development vendor was not used as the base.
Prepare via scripts/run_workflow_progress_ab.py using the latest156-item manifest.
Policies fifo/count/graph/vector/joint are mechanism controls, not prior-paper replicas.
V73 predictive collected154 completed/2 incomplete in8201.434s; both incompletes end
with length/repetitive output, not scheduler failure. Native scheduler686242 and
client687742 are actually running. Preserve that frozen same-arrival comparison.

The goal was reset on2026-10-16 PDT and remains active: use dynamic workflow
events on the native service path to lower mean workflow JCT and raise useful
completion throughput, preserving quality, completion, fairness, tails and
useful-KV recomputation. Prioritize exposed recovery waits, short-prefill/control
cost and recovery/lifecycle/admission coordination. Treat pre-completion
prediction as an optional extension; widespread subsecond RETURN accuracy and
more transferred bytes are not prerequisites or acceptance criteria.

Latest steering on2026-10-16 PDT: if later copies are already hidden, saving
only a few milliseconds of startup cannot justify an expensive prefetch path.
First-layer readiness alone does not guarantee later layers are hidden, but
neither does it establish a useful opportunity. Prioritize shared service/
control cost, repeated restoration and useless KV occupancy. Both completion-
triggered and pre-completion restoration require measured net benefit.

V1109 corrects the all-first-layer assumption for hybrid Qwen: pre-forward
Mamba checkpoint COW waits on the shared layer38 event, after9/10 FULL layers
and all30 Mamba layers of the current missing-data batch. Direct Host-to-request
state restoration can avoid that D2D COW and retain layer-wise execution.
Layer indices start at0; the frontier covers only data needing transfer in
that batch. Partial early copies can still reduce residual waiting without
making the whole pre-forward dependency ready.
Classify these routes before sizing early restoration;90percent is not the
fraction of an entire agent context. The existing on_launch wait probe already
covers the COW fence; V1105's short cohort did not sample that route.
Complete V72 has23/323 barrier samples;historical native6/597;live V73 prefix
5/7,too few for a new-workload majority claim. First COW wait P50 is7.320/
2.856/0.115ms,respectively;D2D copy cost itself is outside those wait events.
V72's23 samples contribute about79percent of sampled dependency waits.
V1110 finds22/23 V72 barriers in multi-request batches,20 with both Host-hit
and no-Host-hit first-service request records;all six native barriers are
such mixed batches. Inspect whether source-specific dependencies can avoid
coupling a resident COW to another request's restoration. General traces do
not prove every source is independent;do not remove the native fence.
Receipts:v1109_mamba_cow_restore_frontier_20261016.json and
v1110_mamba_barrier_batch_coupling_20261016.json under experiments/reports.

Execute the following stage sequence:

1. Audit residual exposure after native layer overlap using existing traces.
   Separate forward-stream layer waits from pre-admission planning,load queue,
   capacity/state preparation,client work and summaries. Bucket by actual
   extend tokens,missing FULL,necessary Mamba and concurrency. Keep absent
   evidence unknown. Report independent JOIN/tool episodes and actual consumers.
2. Reduce shared short-prefill and high-frequency control work, repeated
   restoration and useless KV occupancy. Use existing phase timings and CPU
   equivalence fixtures before adding runtime work. Preserve native ownership,
   state dependencies, action ordering and pressure-reclaim eligibility.
3. Measure the usable completion-to-submit/admission window and its exposed
   restoration cost. Only if evidence supports it, reuse the existing per-layer
   pipeline with safe-checkpoint continuity, actual capacity, bounded residency
   and near-term admission. Do not add fixed sleeps or assume a complete copy.
   Attribute pre-completion prediction, post-completion early restoration and
   post-arrival demand restoration separately. Keep long-tool-wait prediction
   as an independent possible input to cold-KV management.
4. Select one promising change only after identifying operational exposure.
   Validate its isolated net effect with a minimal same-code ablation: actual
   dependency/first-service delay,CPU work,residency,eviction/recomputation,
   mean JCT and useful completions. Shrink paths without demonstrated benefit.
   Do not fund extra scans or restore volume with unmeasured stall estimates.

V1105 completes the first bounded CPU audit over five disjoint V72/V58 sealed
windows. Of9329/10445 observed prefills,4282/4986 are single-request,extend<512.
Pair load-consuming probes directly with services instead of intersecting
independent turnover sampling. Short-prefill layer-wait samples are46/79:
V72 P50/P90/max0.096/0.267/1.356ms;historical native0.091/0.105/0.782ms.
No native short sample exceeds1ms. This currently gives little evidence for
large extra forward-stream overlap gains. Timing includes event overhead,
sampling is sparse/window-concentrated,and earlier recovery/state/admission
cost is not ruled out. Do not extrapolate to all batches or JCT,or infer the
tool module's net effect from these different-version/demand arms.
CPU preparation may already overlap transfers;reassess exposure after reducing
that CPU cost instead of treating the current small waits as a hardware limit.
Receipt:experiments/reports/v1105_short_prefill_existing_layer_restore_space_20261016.json.
V1104 independently aggregates41 exact tool episodes:ACK-to-service P50/P90
1.117/68.304s. V1103's20.560s P50 is node-weighted. Two44/72s client phases
include internal model summaries;existing context_summary_started lease
invalidation must be checked before proposing duplicate fixes.

V1106 resolves native cache counters and lease stages for those41 episodes:
17 still require6.968GB native FULL;24 require none. Among14 ACK-to-service
gaps over5s,13 lose339 node protections before tool completion through
service_window_expired;the other releases five at summary start. Eleven
long-gap requests still restore4.137GB FULL and hit Host Mamba. Release is
not proof of eviction,and native bytes are not all proven repeated copies.
Xarray6744/epoch30 reuses187.433MB early FULL with zero native FULL restore,
yet waits10.629s after arrival;queued snapshots retain a state deficit and
low pool headroom. Do not label the whole gap H2D or pure priority waiting.
Receipt:experiments/reports/v1106_v72_tool_reentry_residency_and_native_restore_20261016.json.

The frozen tool path requires a live WAIT_TOOL forecast and does not recreate
an expired restore merely on TOOL_END. A development prototype adds completion-
driven restoration and next-consumer binding, default off through
BELIEFKV_TOOL_COMPLETION_RESTORE. Its17 initial CPU checks do not qualify late
ACK/epoch handling, full correctness, control cost or GPU benefit. It is absent
from frozen V73 and is not a default next experiment. Establish removable
exposure and an affordable implementation before further rollout. Keep native
capacity checks, summary invalidation and bounded consumption; do not globally
extend old leases. Confirmed JOIN restoration already exists.
Development diagnostics now label fits_current_free_lists as selected-step
capacity and record selected_restore_step_count;first-extent fit is not
whole-checkpoint/input/decode admission proof. V1107 passes three existing
opportunity/sampling CPU checks. No migration,ordering,budget or sampling
cadence change,and the development-only schema is not in frozen V72/V73.

Inspect live V73 and completed V72_workflow_work/f7aec98 before older traces.
V72 ended at2026-10-16 02:52:20 PDT with driver exit0,155 completed and one
incomplete out of156,client duration8278.345s. Task quality remains ungraded.
Use V58 for historical diagnosis;the online workload requires V73's same-table
native arm. Formal acceptance adds matched repetitions and uncertainty.
Keep V72/V73 source,engine and registered launch helpers frozen.
V72 cold-started at2026-10-16 00:27:06 PDT,port18476,on independent engine
tree791dd37f. V1080/V1081 bind registration/launch;state is
ops/v72_workflow_work_20261016.state.json. Keep the agreed156-workflow
108+48 arrivals and budgets for the frozen V72 only.
V1082/V1083 verify actual GPU service,source/settings,identical pools and
NUMA. The00:31 PDT snapshot has108 work accounts,47719 charged output
tokens,84 work-induced head changes and83 admissions,with healthy migration
and no observer errors. This is activation,not measured performance gain.
The first120-300s window has219279/246021 request steps,-10.87%,and17.632s
control self-wall. Short-prefill37/51 samples select at5.392/2.734ms CPU,
run_batch41.563/35.072ms and return-to-result11.087/6.013ms. Keep this
negative evidence;inspect later fixed windows and full quality/JCT outcomes.
The600-900s window yields330045/386707 steps,-14.65%,and36.832s control
self-wall;actual work-priority activation has not established a service gain.

The next development workload uses one Poisson root-arrival process:
156 unchanged tasks in the same order,rate1/15 roots/s,independent seed21.
Freeze the exact table and digest in ab_plan;both arms read it,with no
completion-driven replenishment or hidden client-worker backlog. Log scheduled,
submitted and actual worker-start offsets. V1091 passes837 fresh CPU checks;
V1092 adds10 disjoint harness-config checks. The initial80 are included in837.
V1093 retimed historical durations predict116/126 peak roots and last arrival
at2477.769s;this does not prove real cache pressure or a performance gain.
Run predictive first,then one same-table native baseline:old V58 has different
external arrivals and cannot measure this workload's native-relative speedup.
No task reordering,rate sweep or repetitions during this development iteration.
V72 finished and owned resources exited before the V73 cold start.
V1095 freezes6e85b0a with the unchanged791dd37f engine;V1096 registers V73
supervisor2907350/start_ticks631065999 on port18477. Authoritative state:
ops/v73_online_root_20261016.state.json. Actual V73 start was02:52:38 PDT;
at03:26 PDT supervisor/driver identities match, migration is enabled and no
observer errors are recorded. The same-table native arm is still pending;
startup and a healthy observer do not establish relative performance.
Observe actual FULL/Mamba headroom,Host usage,native H2D,cold reclaim,recomputation
and first-service stalls;retain all failures and full task-quality denominators.
Keep native per-layer H2D/prefill overlap and its Mamba state-COW dependencies;
do not add a second pipeline engine. Page/state capacity is reserved before DMA.
Owner-grouped cold-candidate maintenance and sparse ordered publication pass
CPU equivalence;whole-dispatch CPU changes remain about0.1%-9.3%,not GPU/JCT.

Evaluate prefetch against residual stalls after native layer overlap,not total
H2D duration. Completion-triggered restoration may start the same pipeline
before client submission/native admission without predicting future RETURN;
report it separately from pre-completion prediction. Short prefills can still
stall on later layers;early-layer readiness is not complete overlap or capacity.
V1103 bounds latest V72 receipts:tool535 node commands,4.890GB FULL transferred,
1.941GB confirmed first-service reuse,ACK-to-service P50/P90 20.560/68.305s.
The interval includes pre-completion and admission time;unconfirmed bytes are
not all waste. No isolated same-code tool-prefetch net effect is established.
Keep V72/V73 source,registered helpers and experiment settings frozen.

V1084 audits external arrivals:V72 and historical V58 share the root
timestamps/task order,but the first108 tasks span seven repositories with47
Django tasks and all48 second-wave tasks are Django. Treat two bursts as
a controlled development stress test,not sufficient online-load generality.
For formal evaluation,freeze a shared root-arrival schedule for each pair,
assign tasks independently by randomization/stratification,and vary offered
load,continuous arrival timing,burstiness and two-wave gap/batch sizes.
Keep live internal agent/tool/spawn/JOIN behavior;no fixed demand replay.
Repeating one fixed mapping does not remove mixture/time confounding.
Report warmup,measurement and drain/tool tails explicitly,all failures and
quality,and repeated matched uncertainty. Real LLM request timestamps may
test arrival shape but cannot be labeled real multi-agent workflow traces.
Current V72 and budgets remain frozen;Poisson is now implemented and CPU-checked,
with GPU validation in the running V73 development pair. Broader formal
evaluation remains pending.

V71 failed at00:12:53 PDT with loading_check KeyError:364,driver exit1.
V1075 preserves83 completed,13 connection-interruption errors and60 missing
outcomes,including48 not-yet-arrived workflows. No full-run JCT acceptance.
Its three sealed windows retain-8.31%/-2.64%/+3.45% request-step changes
against V58;third-window394946/381775 and23.176s control self-wall do not
establish stability. A FULL-only prefetch can overlap a same-anchor native
state load,replacing the in-flight record before two ACKs consume it.
Reproduce this on exact native methods;decline duplicates before locks/
preparation and retry only additional restores requiring that live anchor.
Preserve native layer-fence admission for FULL needing no extra restore.
Do not infer the unrecorded second live transfer's Mamba bytes.

Broaden the search with workflow ordering based on completed request work.
BELIEFKV_WORKFLOW_ATTAINED_WORK=1 aggregates completed request output tokens
across the whole root workflow,not queue time,unfinished output,future
RETURN or known total length. Prefer lower attained work only within equal
causal/residency/restore-cost groups. Preserve native discovery order,
ten-second aging,critical JOIN,recovery consumption and actual admission
dependencies. Record actual work-induced head changes and native admissions;
no new tree walks or per-event trace output. Check long-workflow costs.
Combine verified-root and tool-marked child semantic-history reductions.
V1072's18 complete-plan CPU cases preserve native inspection counts/order;
48 candidates add32-40us and156 add90-116us. V1079 binds590 fresh frozen
application identities,158 native identities and27 successful subtests.
GPU policy activation,full-run performance,quality,fairness,tails and
recomputation remain required. Keep prefetch auxiliary;the goal is unmet.

V1067 audits full V70:155 completed/one incomplete,exit0,migration enabled.
153 completed pairs mean2005.764/2306.855s,-13.05%,95 faster/58 slower;
P95 is4424.256/4987.354s. The144 explicitly eligible pairs mean1820.834/
2237.344s,-18.62%,but request/tool counts are24.10%/24.03% lower.
Early verified FULL is59/34 JOIN/tool events,1.271/2.485GB,combined0.192%
of same-run native FULL H2D. First5400s terminal counts138/132 are not
independently correct completions. V70 quality is ungraded;do not reuse V68
quality or attribute historical tail/demand differences to transfer modules.

V1064 rejects raw tool median routing:positive-FULL last-second P90 worsens
1054.8->1132.1ms in V70 and756.7->1473.2ms in V68,without fewer first windows
over10s early,despite better overall quantiles. Keep existing model/routing.
Devef2b782 skips native child-semantic decode history only for declared roots
confirmed by the graph;keep child/unknown paths,tool token features and first
reuse. V1065 passes381/skips one unchanged process integration test requiring
the absent old pinned encoder. V1066 complete-callback CPU fixtures improve
34.47%/12.52% for all/half roots,but child/unknown negatives cost8.78%/5.40%
more,about6.0/3.7us. The67d7685 tool-marker reduction keeps terminal facts;
V1069's48-child callbacks with0/24/48 tool-marked requests change about
-1.64%/-11.49%/-29.20%. Both reductions enter V72,not V70/V71.
V1078 resolves the old fixture skip with the fixed production encoder:
590 checks pass with no skips. Callback CPU is not end-to-end evidence.

Historical V70 cold-started at2026-10-15 21:10:19 PDT,port18474,supervisor3447145/
start_ticks629347135,driver3452941/start_ticks629408220 and scheduler3454060/
start_ticks629409521. V1043/V1045 bind registration/launch;V1046 verifies
actual GPU service,source,dedbe1a0 engine and unchanged physical pools.
V1047 records NUMA preferred. Authoritative state:
ops/v70_terminal_root_20261015.state.json. Keep156 workflows,108+48 arrivals,
running48,context131072,output8192,tools180s,Host200GB/80:20,HBM ratio.9,
prefill4096/interval0 and PREPARE_HOST/JOIN/tool predictive migration.
V1044 superseded only the unlaunched V69 waiter,preserving its evidence.
V68 fully exited and two free-resource observations passed before V70.

The current GPU candidate includes acae8a1 resident-consumption quota,
e284084 same-dispatch first-attempt PREPARE closure reuse and364fa23
post-WORKFLOW_END root retirement. Require explicit ROOT RETURN provenance
and actual workflow end;keep native private-suffix eligibility,shared
prefixes,session/generation,locks and pending-transfer checks. Retry native
deferred anchors only. Root releases stay separate from child handoff/H2D.
V1040/V1041 each pass1109 checks;V1042 binds the qualified source.
Allocator releases and PREPARE reuse are observed,not performance acceptance.
V1048 records first-three-window request steps236154/246021,
371594/386707 and366491/381775,still4.01%/3.91%/4.00% behind V58.
Control exclusive wall is15.321/30.649/27.014s. New earlier-resident-quota
inspection/order/admission counters remain zero;investigate activation
without crediting legacy resident-priority events to the new quota.

The a4108c8 follow-up permits a missing/expired aged-head discovery hint only
on the existing reduced-quota/native-frontier path. A candidate still needs
a fresh warm hint and two actual native reentry checks;compare missing FULL+
necessary-Mamba bytes,next-input work and causal rank before reordering.
Keep ordinary turns,native capacity/dependencies and the old four-turn path.
V1054/V1056 each pass361 checks;V1057 retains759 unchanged identities and
replaces the two modules with361 frozen passes,1120 unique total,not1120 fresh.
V1055 retains real native-matcher CPU costs:0.121-0.126ms at32k/eight nodes,
0.347-0.351ms at98k/24 nodes and3.36-3.45ms at1024 nodes. No fresh candidate
adds no native reads but7-11us;input mismatch still pays two checks.
V1058 registers frozen0bcf78f as V71_aged_head,supervisor610069/
start_ticks629926832,ops/v71_aged_head_20261015.state.json. Require full156
V70 summary,exit0,healthy migration,old process exit and two free-resource
observations. Preserve V70;no GPU benefit or later quality is yet verified.

V1062 joins all five complete V70 windows to sampled native queue metrics.
At3600-3900s,request steps are19.74% lower;mean queue38.43/43.26 and running
40.96/44.21. At4800-5100s,steps are1.43% lower and empty-queue/below48-running
time fractions are96.29%/92.96%,mean queue0.051/0.122. Inspect tool/client
supply there. Sample holding and a6.99s maximum gap in the fourth window
limit attribution;below48 with queued requests can reflect prefill,capacity
or dependencies. Control self-wall24.598/14.875s is not removable JCT.

Broaden the search under the same end-to-end goal. Prioritize a native service
path with sparse event-driven workflow information,whole-workflow bounded
service/cache locality,terminal private-KV release and tool/JOIN-to-submit
supply. Separate backlogged and empty-queue windows before choosing a change.
Current resident/final-report admission is not full workflow attained-service
scheduling;the old workflow_fairness policy is not wired into this native path.
Keep prefetch as an auxiliary with real Host-only opportunities. Do not retain
a feature at the expense of quality,fairness,tails or recomputation,and keep
the workload,budgets,no-new-guard and no-new-PrefillAdder constraints.

V1049 seals the V70 first-wave prefix:53/34 early next-request FULL reuse
JOIN/tool events,1.074/2.485GB,13/one unknown completion boundaries.
Last-ACK-to-service P90 is15.927/96.278s. V1050 partitions81/44 valid
intervals:tool pre-completion P90 is84.757s and after-native-arrival P90
11.960s;JOIN after-arrival P90 is10.643s. Quantiles do not add.
Address premature restoration and later service waiting separately.
At21:59 PDT,82 completed/one incomplete outcomes are written. Django11087
ends on length with34845 repetitive characters and no semantic completion;
children/JOIN finished,no timeout or guard intervention. Preserve it and
the output budget.

V68 completed at21:10:00 PDT,driver exit0,153 completed/3 incomplete/no
errors and migration enabled. The three length-ended roots,django11206,
django13964 andpytest6197,lack semantic completion;children/JOIN ended
without guard intervention. V1048 audits151 both-completed pairs:
mean2156.276/2302.910s,but P95 is5603.914/4999.253s and worsens.
The143 explicitly eligible pairs yield1946.816/2199.727s mean,-11.50%,
76 faster/67 slower. Keep the eight additional pairs and all156 outcomes;
all eight exclusions have guard_intervened_completion,five also missing
semantic completion. Historical versions,realized demand and native tool tails
still prevent causal prefetch or stable acceptance claims.

V1063 now verifies all156 official V68 task outcomes against frozen patches,
source results,individual reports and CPU container receipts:93 resolved/
63 unresolved,no empty patches or infrastructure errors. V58/V906 remains
90 resolved/62 unresolved/four empty. Paired74 both resolved,19 current-only,
16 native-only and47 neither. Preserve the original incomplete/JCT-ineligible
status of resolved django11206. V68 quality is no longer awaiting report audit;
this single historical comparison does not establish noninferiority,causal
policy gains or V70/V71 quality. Retain the one-CPU/8GiB resource overlap.

V1051 audits realized demand in those same cohorts. The151 pairs use
35768/40988 LLM requests and35386/40666 tool calls,-12.74%/-12.98%.
The143 eligible pairs use29085/37084 requests and28771/36796 tools,
-21.57%/-21.81%. The11.50% JCT difference cannot be credited to scheduling.
Fourteen pairs with both call counts within10% and equal delegation rounds
have1535.346/1483.330s mean JCT,5 faster/nine slower. This post-hoc rough
subset does not match token demand,tool time or quality and proves no causal
slowdown. Keep all outcomes. Distinguish ordering,exposed restore waits,
KV lifecycle and realized execution demand;lower mean need not worsen tails.

V1052/V1053 use the complete V68 lifecycle/offload evidence. Early verified
FULL reuse is65/34 JOIN/tool events,1.649/3.185GB,0.081%/0.156% of same-run
native FULL H2D;combined0.236% is byte scale,not speedup or replacement.
PREPARE has1163 node ACKs/12.017GB FULL,337 associated with later restore,
28.98%;unassociated copies are not automatically waste. Tool demotion has
2246 node events/176 tools,cumulative18.871/28.782GB FULL/Mamba;
first actions remain open at least1s for162/176 tools,92.05%,not benefit.
No same-code module ablation identifies separate or combined net speedup.
V58's final single-workflow tail is6351.045s,43.87% of collection,GPU0.332%.
The full-run83.64% completion-throughput difference is tail-confounded.
Both arms end132 workflows in the first5400s;outputs differ+1.54%.
Terminal counts are not independently correct completions. All108 first-wave
3600s restricted time differs-2.75%,90/89 natural completions;versions,
demand and quality remain limitations. Keep full outcomes and fixed-window/
all-identity restricted views;do not silently remove the tail or sum module gains.

V1037 exploratory terminal samples mixed context-epoch advancement with
workflow completion. Correct V1038 requires ROOT RETURN and WORKFLOW_END:
117 roots observed at least10s after end sum19.07/19.90GB device FULL/Mamba
and5.70/6.37GB Host FULL/Mamba across different observation times.
Shared ancestry and native discard eligibility remain unresolved;these are
not simultaneous,exclusive or proven reclaimable capacity.

Historical V68 cold-started at2026-10-15 18:51:42 PDT,port18472,supervisor1944353/
start_ticks628571885 and driver1946108. V997/V999 bind registration/launch;
ops/v68_partial_restore_20261015.state.json is authoritative. V996 binds
1832 unique passes/two skips;708 fresh checks pass in the frozen checkout.
V1000/V1001 verify actual GPU service,configuration and identical physical
pool capacities;scheduler1946507/start_ticks628577888 serves with physical
migration enabled and no observer/writer errors. Keep the same dedbe1a0 engine and agreed workload,
including PREPARE_HOST; no task-quality or performance acceptance is claimed.
The candidate includes qualified multi-child semantic control coalescing
and a reduced ordinary-admission quota for a partly restored demand with
fresh strictly lower FULL+necessary-Mamba restore cost,no greater next-chunk
input and no weaker causal priority. Native input/capacity/dependencies
remain authoritative. Ordinary admissions are preserved;inspect an aged
head only when comparison can change the order. V994 adds8-12us in CPU
fixtures and at most one native read;real native-tree cost remains a risk.
Measure partial-priority consumption,ACK-to-service,reloads,batch filling,
control CPU and actual workflow JCT before accepting this hypothesis.

Latest V68 windows120-300/600-900/1800-2100s yield233896/246021,
376399/386707 and392816/381775 request steps versus V58,with
14.094/27.311/21.897s control exclusive wall. Third-window short-prefill
66/64 samples have40.346/40.419ms run_batch and7.477/4.461ms return-to-result.
V1023 scores all108 first-wave identities at the same1800s per-workflow
horizon:1623.358/1612.092s capped time and34/32 natural completions.
The metric still trails native;it is not full mean JCT or formal acceptance.
Pending/ineligible outcomes count the full horizon;all V68 identities have
passed it. This comparison does not join independent quality evidence;
V68 quality is unverified,while V906 retains the native quality audit.

All five fixed V68 windows are now collected. The4800-5100s window yields
378536/330775 request steps,+14.44%,with10.500s control exclusive wall.
Short-prefill36/73 samples have5.056/4.959ms selection thread CPU,
38.473/37.723ms run_batch and11.685/8.514ms return-to-result.
V1033 scores all108 first-wave identities at a fully observed3600s horizon:
2264.675/2328.684s capped completion time,-2.75%,90/89 natural completions.
Forty-eight pairs improve,52 worsen and8 tie/both cap. Keep the earlier
1800s result and trailing windows;this is not full JCT,stable throughput,
causal prefetch gain,matched repeated acceptance or independent task quality.

V1032 is the newer sealed prefix,ending20:15:11 PDT:62/34 early verified
FULL JOIN/tool events,1.614/3.185GB;18 JOIN/five tool boundaries unknown.
V1034 partitions the same independent ACK intervals. All-action P90 remains
15.840/20.919s. In the outcome-conditioned early-FULL cohort,arrival-to-service
P90 is454.923/273.754ms;before-completion dominates51/62 and24/34 events.
Eight tool events without early FULL reuse still wait13.907s median after
arrival and all have later native FULL hits. Separate early prediction and
post-restore consumption failures;do not claim causal gain or full Mamba
readiness from this selected cohort. Per-file seals are not atomic;
V1032 plan snapshots are not the V1024 explicit-dispatch funnel.

The full156 V68 official quality evaluator is running on CPU,supervisor3204505/
start_ticks629116499,ops/quality_eval/run_v68_quality_cpu_20261015.py.
State:quality_v68_cpu_20261015.state.json. The genuine full summary has
been exported as V1035 and environment V1036 is bound;worker3450470/
start_ticks629378270 is live. One CPU worker,one-core quota,
8GiB,no GPU,cached pinned official images/harness;all outcomes stay in the
denominator. Record possible overlap with later GPU development,not a formal
matched-resource performance trial. Registration or worker exit0 is not a
verified complete quality result.

Historical V1024 independently sealed prefix audit,ending at19:40:41 PDT:
107/153 explicit pre-completion positive FULL JOIN/tool events,76/42
pre-completion submissions and57/34 early next-request reuse events,
1.367/3.185GB FULL. Twenty-nine JOIN starts lack a pre-completion submit;
12 JOIN/one tool completion boundaries remain unknown. Independent-event
last-ACK-to-service P90 is16.156/20.919s,with184.704s maximum for tools.
Twenty-two FULL commands have later native reload associations based on
legacy batch-pool presence,not reconciled per-request receipts. Prefixes
are sealed per file,not atomically across files. Do not claim full-run gain.
At19:51 PDT,88 outcomes are written:87 completed and one incomplete.
The django11206 root ends with two length results,repetitive final text
and no semantic completion;no guard intervened and both children/JOIN ended.
Preserve it without automatically increasing output or changing live harness.
At19:55 PDT,90 are completed and two incomplete. New pytest6197 also
ends on length with repetitive text,no semantic completion or guard action;
children/JOIN finished. Keep117 repeat-tool observations/165 feedback records
separate from physical-migration failures.

V1004's503.998-525.815s native stack diagnosis is outside fixed windows;
941 main-thread samples include50 local closure captures,not CPU duration.
V1005 seals18/42 explicit positive pre-completion FULL JOIN/tool plans,
13/14 submissions and9/11 early next-request reuse events,74.14MB/1.093GB.
Eighty-nine JOIN and one tool boundaries remain unknown. ACK-to-service
P90 remains11.656/20.339s. Of1140 zero/unobserved-positive tool waits,
later native FULL Host hits total385.50GB;this is not an early opportunity.
V1006's four observed large cases have zero FULL missing at their last
pre-boundary snapshot,yet0.65-0.81GB native hits and13.31-14.63s subsequent
arrival-to-service waits. Sparse snapshots cannot date the eviction.

Historical Devacae8a1 was frozen as c27ca37,qualified by V1013 with1850 unique
passes/two skips and726 fresh frozen checks. Earlier locality turns use only
existing fresh hints,then validate both candidate/head native reentry and
cost/work dominance. Quota follows imminent native slots and eligible
residents,with ordinary turns preserved. Separate locality accounting lets
restore/final-stage priority credit accumulate;decline spends no quota.
The V1008 initial extra-discovery variant is rejected:all-cold controls added
nine native reads and128-134us. Final V1010 adds no native discovery on
negative controls,two validation reads on eligible turns and69-78us complete
CPU planning cost;cached all-cold controls still add42-51us. GPU benefit is
unproven;do not hot-edit or interrupt V68 for these CPU observations.
V1025 uses the actual current-engine RadixKey and native reentry method in
complete CPU admission planning,with synthetic ancestry/index metadata and
no DMA. Eligible32k/8-depth and98k/24-depth turns add0.133-0.404ms/two
fresh reads;32k/256-depth and1024-depth add0.974/3.472ms. Input-mismatch
controls still inspect the head,adding0.095-1.943ms without reordering.
All-cold controls add no native reads but about0.018ms. This strengthens
the fixture evidence without measuring live deep-tree frequency or GPU gain.
V1044 replaced this unlaunched candidate;the change is now in V70.
Judge service waits,control CPU and full JCT.
The V68 fourth3600-3900s window yields372397/388781 request steps,
about4.2% behind,with17.930s control exclusive wall. Short-prefill53/75
samples have7.539/4.027ms selection thread CPU,45.464/39.102ms run_batch
and8.745/4.853ms return-to-result;different samples are not removable JCT.

Deve284084 reuses only the best PREPARE selection within one synchronous
dispatch and its first native attempt. Nonempty/unknown control delivery or
an earlier native attempt forces recapture. Keep native session,node,prefix,
Host-capacity checks,final causal validation and per-extent ledger/ACK.
V1031 passes730 checks against the same real engine on CPU fixtures.
V1028/V1030 preserve complete-dispatch transfer selection/publication.
Final16/256-depth eligible means improve0.147/1.405ms;64-depth median
improves4.926 to4.562ms,but a37.840ms outlier raises the mean4.933 to5.218ms.
Control-delivery negatives add0.039-0.068ms with no extra native reads.
Retain both cost and outlier evidence;this is not GPU/JCT acceptance.
V68 was not hot-edited. This PREPARE change later passed V1042 qualification
with completed-root retirement and entered V70;it is no longer Dev-only.
V1018 preserves a registration bug:the imported generic register function
spawned its old defining-module watcher,which exited before GPU launch.
No driver/raw collection was created;its old observer state was overwritten.
Retain the failure and avoid inventing its unretained exit status.
Historical V1019 registers V69_cached_resident,waiter2567402/start_ticks628810592,
port18473,explicit ops/start_v69_cached_resident_registered_20261015.py.
Its PID/argv were verified;V1044 later stopped it before GPU launch.
The complete156 V68 results,exit0,healthy migration,old process exit and
two free-resource polls were verified before the actual V70 launch.

V998 supersedes only the unlaunched V985/6815795 waiter,retaining its source,
registration and helper. V1002 explicitly interrupts V67 after all five
fixed windows:135 completed,21 without terminal outcomes,driver exit-15.
Preserve raw evidence and administrative censoring;do not synthesize full
JCT or label missing outcomes failures. Old processes/containers exited
and two free-GPU observations passed before V68 launch.
V67's4800-5100s window yields329589/330775 request steps and11.211s
control exclusive wall;short-prefill33/73 samples have36.584/37.723ms
run_batch and7.523/8.514ms return-to-result versus historical V58.
V991 partitions separate failure modes:one JOIN still waits14.136s after
native arrival with182.19MB native FULL remaining;one tool ACK precedes
completion by109.922s despite7.84ms client-submit delay,then waits14.507s
after native arrival. Another tool's18.626s client interval includes
summarization. Do not confuse prediction timing,summary work and admission
waiting,or equate one lease exit with release of all covering locks.

Historical V67:
V67 cold-started at2026-10-15 17:20:43 PDT,port18471,supervisor546742/
start_ticks628002818 and driver597989. V972/V974 bind registration/launch;
ops/v67_aged_warm_direct_20261015.state.json is authoritative. Two free-GPU
observations passed before launch. V975/V976 verify actual first GPU service,
e8dcba7 source,dedbe1a0 engine,actual configuration and unchanged physical pool
capacities. Scheduler598387/start_ticks628031986 serves with migration enabled
and no observer errors; NUMA remains preferred. Same frozen
V966/V967-qualified candidate,engine,model and workload; predictive only.
V67 windows120-300/600-900/1800-2100s yield235209/246021,
363328/386707 and387939/381775 request steps versus historical V58.
Control exclusive wall is14.934/29.368/21.373s. Early windows still trail;
third-window short-prefill56/64 samples have38.608/40.419ms run_batch
and7.673/4.461ms return-to-result. No stable full-run gain is established.
V989 uses all108 matched first-wave identities at a fixed1800s per-workflow
horizon: capped completion times1554.608/1612.092s and43/32 verified
natural completions. Pending/errors/ineligible outcomes count the full horizon,
and every predictive workflow has passed it. This is not full mean JCT,
global throughput or causal attribution; task quality remains unverified.
V990 independently seals97/155 explicit pre-completion positive FULL JOIN/
tool events,66/38 pre-completion submissions,52/36 early next-request reuse
events and1.634/3.704GB FULL. Sixteen JOIN and one tool boundaries remain
unknown. Last-ACK-to-service P90 is15.393/2.232s; the tool maximum124.766s
and14 prefetched-FULL commands later natively reloaded remain unresolved.

V978 replays the exact V977 prefixes without failure:9764 of19635 locally
emitted semantic frames are definite intermediate multi-member ALL JOIN
bodies,49.7 percent. This precedes asynchronous coalescing and is not saved
datagrams or CPU time. Dev0c2bbb2 coalesces these body controls, retaining
first/final frames, first negative tool evidence, every child notice and
complete local content. Normal periodic delivery resumes for the last member;
unknown membership keeps the original path. Frozen6815795 uses the same
dedbe1a0 engine; V983 freshly passes251 checks/two subtests with two skips.
V984 binds1733 unique passes/two skips. Historical V985 registered V68_join_body,
waiter1297304/start_ticks628272250,port18472; state is
ops/v68_join_body_20261015.state.json. V998 explicitly superseded it before
GPU launch;its frozen source remains unchanged. Do not attribute the latest
combined V68_partial_restore result to this unlaunched semantic-only candidate.
V973 explicitly interrupts V66 after four complete fixed windows including
3600-3900s:96 completed,60 without terminal results,original driver exit-15.
Retain raw evidence and censored identities; no complete summary or full-run
JCT is synthesized. Verified predecessor processes/containers exited and
GPU resources were released. V968's original waiter was explicitly superseded
before GPU launch; retain its original registration/helpers.
Historical V66 cold-started at2026-10-15 16:10:48 PDT, port18470, supervisor3804658/
start_ticks627607837 and driver3804888. V956/V957 bind registration/launch;
ops/v66_restore_cost_after_tail_20261015.state.json is authoritative.
V958/V959 verify actual first GPU service,2374925 source,dedbe1a0 engine,
settings and unchanged physical pool capacities; scheduler3805292 serves
with physical migration enabled and no observer errors. NUMA is preferred,
not a hard all-Host-memory binding.
The unlaunched V950 waiter was superseded to prioritize the latest qualified
candidate within limited GPU availability. V955 explicitly censors V65 after
all five fixed windows:152 completed,one incomplete,three without terminal
results. Preserve every raw source,result,driver exit-15 and censored identity;
do not synthesize a complete summary or full-run JCT. Verified old processes/
containers exited and GPU resources were released. V949 binds1615 unique
passes/two skips; V948 freshly passes592 checks in the frozen checkout
against the unchanged dedbe1a0 engine. Includes73a4a33 missing-FULL+Mamba
byte ordering within existing non-aged causal groups, plus the previously
checked250ms tool planning slack. Keep V65 model/harness/workload/budgets.
Unknown pool geometry retains old ordering; ordering grants no DMA or
execution authorization. V947 adds no native inspections and48/156-queue
CPU plans are near flat, not GPU benefit. Registration is not launch.
Historical V65 cold-started at2026-10-15 14:00:16 PDT (2026-10-16 05:00:16
Asia/Shanghai), supervisor2241585,driver2241737,port18469. V920 binds1455
unique passing checks/two skips; V921/V922/V923 bind release, registration
and actual launch. V924/V925 verify actual GPU service,614069b source,
dedbe1a0 engine,configuration and unchanged V64 physical pool capacities.
Scheduler2241980 served with physical migration enabled and no
telemetry writer error. FULL branch-retirement counts increased;
these are not freed-byte, prefetch-use or benefit evidence. The observer
collected all five fixed windows; end-to-end acceptance remains open.
Keep156 workflows,108+48 arrivals,running48,131072 context,8192 output,
180s tools,200GB Host80:20,HBM Mamba/FULL=.9 and NUMA preferred.
V64 was intentionally interrupted after155 completed in V916; retain
django__django-12273 as censored with no terminal result and all five
fixed windows. Do not invent a complete summary or full-run JCT comparison.

V954 reuses the exact V944 prefixes for three selected large tool restores.
Native-arrival-to-service is12.891-13.816s; observations still show zero
missing FULL after arrival, then two show loss. Native batches including
the served Host leaf take42.008-55.048ms submit-to-ACK and may include
other requests. Sparse observations only bracket loss, not its cause or
removable JCT. V66 tests missing-byte ordering; no ordinary ready-request
residency lock or blanket lease extension has been added.

Latest V66 windows120-300/600-900/1800-2100s yield233264/246021,
382124/386707 and396669/381775 request steps. Control exclusive wall
is15.226/26.239/19.861s. Third-window short-prefill58/64 samples have
37.014/40.419ms run_batch and6.854/6.962ms selection thread CPU.
Early windows still trail; historical V58 differs in version/trajectory.
At3600-3900s,request steps are336089/388781 and control exclusive wall21.922s.
Decode-batch counts9237/9266 are close but mean batch is36.385/41.958.
Short-prefill49/75 samples show49.424/39.102ms run_batch,6.319/4.027ms
selection thread CPU and10.544/4.853ms return-to-result. Batch filling and
turnover remain open; the third window does not establish a second-wave
or full-run advantage.
V960's opportunity-inclusive pre-completion positive FULL JOIN/tool plans
are46/118, with21/30 early next-request reuse events and0.315/2.738GB.
The explicit tool-plan count is108;67 JOIN boundaries remain unknown.
ACK-to-service P90 remains17.738/18.780s. All11 V962 JOIN starts without
pre-completion submissions lack free-list capacity after input/decode
reserves; no waiting victim was published, and native scans mostly find
unbacked or session-referenced nodes. Later close labels are not exclusive
causes; evictable totals do not prove safe cold capacity.

V961 reconstructs causal state at client submission:6646 multi-member
ALL JOIN background requests retain background classification. Of6626
served requests, median arrival-to-service is11.808s, versus11.980s
foreground and12.549s stragglers. This is not actual later server ordering
or a cross-role causal comparison. Do not promote every child on that basis.
Dev de1f02f instead adds one bounded warm-input bypass after the existing
four ordinary admissions when the head is aged. Candidate FULL must already
reside on device, restore bytes must be lower, next prefill input no greater,
and causal priority no weaker than the head. Existing restore/parent/final
promotions stay ahead. Native prefix/capacity/dependency checks remain
authoritative; capacity reclamation remains a separate open issue.
V963 passes646 checks/two subtests. V964's full CPU fixtures add about
0.13-0.15ms on eligible48/156-queue paths, up to nine inspections; retain
the two initial fixture failures and do not infer GPU/JCT benefit.
Frozen e8dcba7 uses the same dedbe1a0 engine. V966 freshly passes900
checks/two subtests; V967 binds1731 unique passes/two skips. The old V968
waiter431581/start_ticks627925431 was explicitly superseded before GPU launch.
Its original state retains superseded_before_gpu_launch. V972 registration
and V973 explicit censoring/release authorize the actual V974 launch.
V67 has no measured GPU benefit yet; historical V66 source/model/engine,
registered helpers and original driver-failure exit record remain untouched.

V65's first three windows are collected. At600-900s, request steps370969/
386707 exceed V64's365786 but retain different versions/realized work.
Control exclusive wall is28.203s versus V64's33.956s. Matched full48
worker intervals approach native; short-prefill63/62 samples run_batch
37.750/37.587ms and selection thread CPU18.432/17.141ms. At120-300s,
steps219559/246021 remain behind. These are not stable end-to-end gains.
V928's20s out-of-window GIL sample retains102 read errors and285 main
samples; sample counts are not CPU duration or removable control cost.
V929's independently fixed prefix has13/40 explicit pre-completion positive
FULL JOIN/tool plans,10/4 pre-completion submissions,8/4 early next-request
reuse events and0.109/0.323GB FULL;92 JOIN boundaries remain unknown.
V932's separate prefix has85 internal summaries:82 nonempty stop results,
three pending,zero empty length. The48.641s median model span includes
queue/service; it is not client idle or saved JCT. Content/task quality is open.

The separate Dev candidate adds250ms runtime planning slack only for live
tool H2D. Report planning_slack_ms independently from prediction uncertainty;
preserve the existing2s maximum lead, all capacity/identity/dependency/enqueue
checks and the existing scan/model cadence. V930 observes725 additional
timing candidates in V64,658 within1s, and13 in V65,all within1s.
Most lack observed free capacity; no achievable prefetch or benefit follows.
Do not widen JOIN's already-premature signals. V931/V933 pass448 related
checks and V934 binds the source/evidence. Not GPU-deployed or a replacement
for full qualification; do not hot-edit or restart the live V65 for this alone.

Latest V935 fixed prefixes show63/182 explicit pre-completion positive FULL
JOIN/tool plans,52/31 pre-completion submissions,41/30 early next-request
reuse events and1.079/3.211GB FULL.47 JOIN/three tool boundaries are unknown.
V937 includes opportunity snapshots and counts188 positive tool plans; keep
that denominator separate. JOIN ACK-to-service P90 remains15.376s; early
FULL events have2.275s median ACK-to-completion and10.619s P90 native-arrival
to service. Repeated same-parent restores without reuse require exact
prediction/lease/eviction/admission diagnosis, not longer leases everywhere.
At1800-2100s, steps389293/381775 and control exclusive wall21.663s;
short-prefill80/64 samples run_batch36.566/40.419ms and selection thread
CPU5.575/6.962ms. Full48/context-matched ratios are1.005-1.008. Mixed
windows still do not prove complete JCT or stable benefit. V938 groups by
same JOIN/session/node generation/FULL length:21 of39 repeat restores
have confirmed first-service reuse. Reject a blanket one-restore limit;
registered-target identity is not an exact page-content/allocation fingerprint.

V939 binds the selected child request to exact native output totals. Of51
first estimated-work triggers,45 lead native EOS by over2s; median actual/
predicted remaining work is188/44.64 tokens, signed error-136.96.
The last pre-native-EOS snapshot instead overestimates by16.93 tokens
(median absolute33.78). Client notice features align. A global seconds
shift cannot correct both directions; retain phase/work/rate separation.

beff01e adds sealed delivered-input exports, baseline reproduction and
per-role notice coverage;43 CPU checks pass. V940 binds5869 snapshots/
63 natural requests,5359 notice-active. Work-only train/calibration/evaluation
have35/18/6 project-disjoint workflows; calibration contains1488 active
notice snapshots. Baseline work reproduces within0.0009 tokens.
Both workload waves contain only the existing seven project families, not
Astropy/Sphinx. These new roles are development work-head holdouts; the
frozen phase/encoder previously saw their families, not a sealed new test.
On six evaluation requests, unweighted refit changes last-snapshot median
absolute error 47.737->30.703 tokens but P90 114.993->180.378; late4 gives
40.235/88.032 with a worse selector pinball score. Original-progress replay
does not establish stable subsecond triggers. Neither candidate is selected
for GPU; retain both results. Fits use CPU only and do not change live V65.

V65's second-wave3600-3900s window is complete:312147/388781 request
steps,23.217s control exclusive wall. Short-prefill48/75 samples have
58.237/39.102ms mean run_batch and7.669/4.027ms selection thread CPU.
Most full48/context-matched decode worker groups remain close, with one
at1.114x; bucketed short-prefill differences remain. Earlier close groups
do not close the control-cost workstream. Retain trajectory/configuration
limits and continue the fifth fixed window and full-run observation.
Two-thread CPU work refits overlap part of this second-wave window;
retain this resource intervention, not a formal matched comparison.
V941 samples native stacks only at4129-4150s:377 of927 main samples
include forward_extend, with GDN/Triton/CUTLASS and breakable graph eager
calls. This is neither recapture evidence nor CPU/CUDA duration.
Native unwinding can briefly pause threads; retain the diagnostic interval.

The fifth4800-5100s window has386637/330775 request steps,15.954s
control exclusive wall; short-prefill65/73 samples run_batch40.410/
37.723ms, selection thread CPU7.016/4.959ms. V943's exact adjacent-forward
second-wave48/75 samples have mean return-to-result gaps13.707/4.853ms,
including return-to-next-launch10.426/2.977ms; these are not exclusive
function costs or removable JCT. Mixed windows retain trajectory limits.
V942's fixed108 first-wave identities at4200s have94/92 eligible finishes
and2243.551/2405.262s mean restricted time to eligible root completion,
about6.72% lower. This event-clock censoring statistic is not final mean
result.duration_seconds; V65 quality and matched-version acceptance remain open.
V944/V945 include opportunity snapshots:138/198 positive pre-completion
FULL JOIN/tool plans,103/35 actual early FULL submissions,86/32 next-request
reuse events and2.376/3.232GB FULL;22 JOIN boundaries remain unknown.
Selected large later-native-restore episodes had zero FULL missing in
waiting snapshots, only missing state; their later demand is not proof
of earlier prefetchable pages. At15:51 PDT V65 has147 completed,
one incomplete and8 pending. django-11087 ended length without semantic
completion; retain its failure and the8192 output budget.

V64's1800-2100s window has392903/381775 request steps,23.081s control
exclusive wall and39.240/40.419ms short-prefill run_batch. Mixed windows,
different contexts and work leave end-to-end acceptance open.
V883 sealed prefixes confirm early next-request FULL reuse on39 JOIN/
118 tool events,0.811/6.754GB;23 JOIN completion boundaries stay unknown.
V884 audits31 JOIN starts without pre-boundary submit:29 positive FULL
plans and2 recorded slot changes. Reclaim often rejects session-referenced
leaves; aggregate native evictable tokens are not safely reclaimable tokens.
Tool ACK residence is decomposed per event: median pre-completion/client/
delivery/native-to-service intervals250.8/14.8/195.3/160.8ms; tails differ.

The next independent candidate retires obsolete FULL branch session coverage
after a tagged BeliefKV continuation registers its latest path. Cached data,
Host copies, other sessions and active locks remain native-owned. Real CPU
radix/allocator checks demonstrate that an obsolete backed branch becomes
reclaimable. V887-V891 merge1420 unique passing identities and two skips;
retain V889's fixture-only missing-load_fence_stream failure and replace
those six identities with V891. CPU Host-copy stand-ins do not establish
GPU applicability, DMA performance, recomputation or benefit.
Combine this repair with the checked semantic-delivery and single-leaf
PREPARE work in current frozen V65; none was hot-edited into V64.
Tagged native also changes, so formal controls need this same new engine.

V894 freezes candidate2d1d75e with engine treededbe1a0 and canonical patch
SHA256640ab9281cbcb4b95732ff005854a73b4e8901b250240dc51c32c304d797d35c.
V895's original V65_full_latest waiter1770086 was identity-checked and
stopped before GPU launch in V907; preserve its registration/state and absent
run directory. The latest tail contains empty length-limited summaries.
A shared summary-model factory now explicitly disables summary thinking,
retaining2048 summary output,8192 agent output, prompts, retry/fallback,
guards and eligibility. Root/in-graph/planned-child and peer paths use it.
The first184 checks did not cover final identity tagging. V917's two failing
checks expose dropped thinking defaults;5be126e merges model extra_body defaults
with per-call overrides. V918 passes247/skips one; final V920 binds1455 unique
passes/two skips. Preserve the failures and V912 registration: that intermediate
waiter exited on nvidia-smi timeout before GPU launch, with no run directory.
Final V65 includes tagged-summary and retryable observation repairs. Actual
summary quality, failure reduction and GPU benefit remain unverified.
V64's3600-3900s window has325909/388781 steps, mean decode batch30.95/
41.96, contexts16207/17278 and20.070s control exclusive wall. Short-prefill
63/75 samples run_batch47.227/39.102ms; service cost remains an open issue,
and different batch sizes do not explain away every performance gap.

All five V64 windows are now collected. At4800-5100s, steps279568/330775,
mean decode batch21.01/30.92, contexts23335/23139 and control exclusive
wall13.453s. Short-prefill44/73 samples run_batch38.967/37.723ms and
selection thread CPU7.151/4.959ms. Full JCT/quality acceptance remains open.
V903/V904 samples native stacks for20s after5110s:955 main-thread samples,
427 launch and341 result. Model execution/graph replay remain in launch;
thread CPU is not all extra control, and _execute_body_capture is a replay
wrapper, not evidence of repeated capture. Sampling can briefly pause threads;
exclude that diagnostic interval from unperturbed fixed-window claims.

V899 links24 selected tool residence tails to their exact completed calls.
All five client gaps over5s contain internal summaries:265.575s aggregate
summary call span within266.292s aggregate gap. These spans include model
queue/service and callbacks, not plain client idle or tool-return error.
A180.098s test call initially predicts a conditional median of669.5ms.
V901 tests causal shape/project/role history on4383 independent single-execute
waits,997 roots and3386 children. Ten checks pass. Overall last-second median
error improves, but1195 positive-FULL-plan waits worsen P90 from720.6 to
772.8ms and over10s premature windows from25 to35. Keep it offline; do not
select a predictor from the favorable pooled median. V65 stays registered
with unchanged runtime/model/source. Replay windows are not feasible H2D
actions or end-to-end benefit.

V905 seals the later V64 prefix:189 JOIN waits,three unknown boundaries,
83 early-started next-request FULL reuse events/1.648GB;18432 tool waits,
120 such events/6.836GB. Explicit wait_prefetch_plan rows show150/1175
pre-completion positive plans and100/149 pre-completion submits. These
denominators differ from155/1234 classifications including opportunity
snapshots. JOIN/tool final-ACK-to-service P90 is10.332/27.895s, retaining
pre-completion residence, summaries and service waits as distinct causes.

V906 verifies V58's complete156 quality denominator:90 resolved,62
unresolved,four empty patches. All152 nonempty-patch reports agree with the
official summary; no missing nonempty reports or grading infrastructure
errors.152 container receipts retain1 CPU/8GiB/runc/no GPU. Source workflow
outcomes are154 completed,one incomplete,one error; grading completion is
separate. All90 resolved workflows are completed and JCT eligible. The
all156 resolved fraction is57.69%. V936 now verifies all156 V63 task reports:
93 resolved,63 unresolved,no empty patches,missing reports or infrastructure
errors;59.62% of156. Paired counts are74 both,19 V63-only,16 native-only
and47 neither. This historical single run does not establish noninferiority
or policy causality. Resolved pytest-8399 remains JCT-ineligible.

The first two V64 windows are complete. Steps228359/246021 at120-300s;
365786/386707 at600-900s,2.6% above V63 but5.4% below historical native.
Contexts24319/23674 differ. Matched full48 worker ratios are0.997-1.002,
yet control exclusive wall remains33.956s; short-prefill selection/run_batch
20.720/39.403ms compare with17.794/37.587ms. V879 binds the evidence.
V872's sealed prefixes confirm JOIN3/21.26MB and tool16/408.99MB early
next-request FULL reuse, with91 JOIN boundaries still unknown.
V874/V876 active/GIL samples were collected outside fixed windows; preserve
155/121 read errors and avoid converting sample shares into CPU/GPU savings.
Dev PREPARE now reuses a validated single-leaf root-first ancestry, following
the existing H2D pattern. Generic multi-leaf handling, tail proof and enqueue
checks remain. V877 passes443 checks; V878's depth64 complete CPU plan is
0.510/0.486ms with identical output. Small local repair, not deployed in V64
and not a reason to restart the live GPU collection. All workstreams stay open.
V882 supplements16 real native CPU radix/allocator/tail-reclaim checks over
the qualified engine,459 unique passing checks total. Preserve the initial16
optional skips without an engine path; the configured rerun passed all16.

V870 binds completed V63:156 completed,145 native-agent-JCT eligible,
8002.294s and physical migration enabled throughout. Outer driver exit0 does
not erase the inner client's exit1 for eleven eligibility exclusions. On143
explicitly eligible historical pairs, means2000.953/2240.077s differ10.67%,
but LLM requests31073/36896 and different harness/engine prevent attribution.
Pre-completion next-request FULL reuse: JOIN75 events/1.542GB and tool140/
8.700GB. ACK-to-service P90 remains12.552/19.516s. All workstreams stay open.

Independent official CPU quality grading has completed for V58/V63 and
V906/V936 verify every task. Retain V864's one worker,1 CPU,8GiB,no GPU
and overlap with V64/V65; this does not validate V65 quality or the full goal.
Separate Dev repair V871 suppresses only semantic control frames after a
generation's first tool-negative marker, retaining local snapshots, cadence
and following final-report generations. Old code fails two reproductions;
239 checks pass and two skip. V866's29390/140532 historical prefix frames
show applicability, not saved CPU or JCT. This repair is not in frozen V64.

Current facts override later historical scheduling notes. V62 collected156
workflows in7653.886s:154 completed/two incomplete,148 natural terminal and
143 agent-JCT-eligible. Both incomplete12754/14500 final responses hit length;
retain8192 output cap. Eleven completed workflows retain finalization
intervention. V62's frozen HTML import failure was repaired offline, with
the original driver failure retained and no physical migration disablement.
V823 supplements, without rewriting, the152 outcome-completed pairs with141
explicitly eligible pairs: mean2053.910/2172.270s,5.45% lower than historical
native. Realized LLM requests are also11.25% fewer30483/34346. Different harness/
engine and absent independent quality checks leave comparison_eligible=false.

V63's first attempt failed before GPU use because TCP preflight missed a
stale Unix control socket. PreserveV817/V818 and the original directory.
After connection refusal verified it stale, the samea297462 startedV63_retry1
at2026-10-16 00:16:43 Asia/Shanghai. V821/V822 bind recovery and launch;
V824/V826 verify actual service, source/configuration and matching capacity.
NUMA remains preferred, with large mapsN0=41.52GB/N1=160.44GB, not hard bind.
Actual supervisor/driver/scheduler3157362/3157516/3157952 are recorded in
ops/v63_latest_capacity_retry1_20261015.state.json. Observer3208346 uses frozen
analyzers for120-300/600-900/1800-2100/3600-3900/4800-5100s. The first window
has228469/246021 request steps, short-prefill selection CPU4.999/2.734ms and
run_batch37.354/34.842ms. Real deadline coalescing and indexed reclaim execute,
but no measured net benefit is established; all three workstreams stay open.
In600-900s, steps356509/386707 exceed V62's346953 by2.8%, and control
exclusive wall falls33.710 to31.284s. Mean context23794/23674 is close,
yet decode worker intervals34.078/31.212ms remain different. Continue
actual CUDA-graph, batch-geometry and adjacent-phase attribution.

The1800-2100s window has398898/381775 request steps. Short-prefill59/64
samples have run_batch wall38.669/40.419ms and thread CPU37.977/39.234ms;
control exclusive wall is21.409s. Full48 matched graph cohorts differ by
about1.0%-2.3%, while47-decode to one-prefill intervals are shorter here.
Do not infer a fixed decode-kernel regression or overall policy success.
V839 binds these windows and V834's independently sealed live prefixes:
JOIN/tool early-started and next-request-reused FULL1.070/8.686GB across
38/139 waits. Tool's last submit is1777.130s after first service; capacity
and weak conversion remain unresolved.

The cold-output-tail hypothesis is now reproduced and repaired in development,
not deployed in frozenV63. Real CPU radix/session/allocator checks show that
an unbacked2-token FULL output leaf can block reclaim of32 backed input tokens.
PREPARE may back up a proven current private idle device leaf's missing FULL
tail; already-backed tails join cold candidates with live owner revalidation.
Native reclaim frees34 FULL tokens while preserving live input Mamba, and
H2D still restores only the32-token safe input checkpoint. No speculative
Mamba backup. V838/V840 pass839 unique checks. Host metadata/copies are
stand-ins; actual tree, allocation, reference tracking, reclaim and reentry
are native CPU paths. Applicability is not quantified because historical
waiting rows lack device-leaf topology. Validate that and GPU benefit on a
later cold candidate; do not hot-editV63 or restart solely for a CPU pass.

V841/V842 partition the same691624950-byte V63 opportunity prefix. Tool
FULL capacity refusals affect1635 recorded waits in600-1200s; positive FULL
plans almost disappear after1800s, with zero missing Host-backed FULL in the
tool opportunity samples after2400s. These are distinct mechanisms. Native
full-token usage subtracts both free and evictable cache, so19% usage does
not mean81% physical allocator headroom. Keep these capacity views separate.

The3600-3900s window has364157/388781 request steps and10094/9266 decode
batches: mean requests per batch36.08/41.96. Short-prefill70/75 samples have
run_batch wall38.973/39.102ms; control exclusive wall remains18.291s.
V844 fixes all108 original first-wave identities, keeping unfinished and
ineligible outcomes censored. At3000s, eligible completions84/78 and restricted
root-completion time2118.599/2166.941s favor predictive by2.23%. This is not
complete result.duration_seconds mean JCT, matched trajectories or quality.

The next cold candidate is5229d98/V855. It inherits V849/24a83c9's996
checks and FULL session-path scope correction, and adds32 relevant checks
for factual read-result feedback. V852 observes309 same-input grep calls
in one child; different serialized output hashes do not prove changed visible
content. The old feedback covered execute only. grep/read_file/glob/ls now
retain physical execution and append history for identical visible text and
outcome within the same conversation/workspace epoch. Scripted-model checks
use a real LangChain agent and preserve earlier messages and observation bounds.
No new guard, suppression or forced RETURN. Actual loop and quality gains
remain unverified; total distinct passing checks1028.

V859 verifies the old queue was still unlaunched, stops only launcher238495
and observer275192, and preserves V845 plus original state snapshots.
V64_latest is registered inV857 with launcher493245 and observer494540,
V63 has completed and released GPU0; V858 binds the actual cold V64 launch.
Keep the156-workflow setup and V58 development reference. The shared harness
change can alter trajectories, JOIN timing and pressure: formal native controls
must use the same harness and resources, and gains cannot be assigned to
scheduling from the historical comparison. V63 and V64 source remain frozen.

The fifth4800-5100s window is complete:343212/330775 request steps,
mean context23055/23153 and worker interval22.367/22.231ms. Short-prefill
58/73 samples have run_batch wall38.142/37.723ms and selection CPU
5.318/4.959ms; control exclusive wall is14.569s. All five windows have
mixed outcomes. At01:46 Asia/Shanghai,132 results are completed and130
eligible, with physical migration enabled; full-run acceptance stays open.

V819/V820 bind a recent sealed prefix: JOIN/tool early verified FULL reuse
1.570/8.594GB. Tool's last submit is1835.983s after first GPU service. Among
zero-size pre-completion FULL plans,1809 later tool requests restore449.164GB
native FULL and wait12.684s median after arrival. Those later bytes do not
prove the same pages could be prefetched earlier. Same-event ACK partitions
show arrival-to-service P90 0.388/0.988s while ACK-to-service remains6.846/
7.101s; early residence must not be mislabeled pure GPU queueing. Investigate
capacity/reserve, cold-candidate registration and admission using these
separate boundaries; this prefix is not a complete-run oracle bound.
The live indexed scan's235 duplicate entries with zero candidate leaves was
diagnostic motivation, not proof of tail eligibility or permission to discard
unbacked output. The subsequent repair preserves it with incremental backup.

The free-GPU instruction supersedes the19:51 reservation. The identity-checked
old waiter4030423 has exited and must not restart. V61/824cd44 has finished:
155 completed, one incomplete,7541.539s. At approximately582s, a valid confirmed
JOIN ACK reached the runtime before native registration of its next consumer;
the same-epoch session bridge was missing and physical migration was disabled.
Retain this run as degraded_diagnostic with comparison_eligible=false.
Preserve django__django-12125's incomplete outcome and all failure evidence.

Repair a63d524 requires a live confirmed client-bound ticket, matching session/
generation and actual native anchors for the same-epoch registration gap.
Explicit COMPACT/ADVANCE invalidates credit for pending old-prefix transfers
without cancelling submitted DMA or weakening structural receipt validation.
All714 related checks pass; retain the original failing reproduction.
V62/cd53d19 cold-started at2026-10-15 22:01:11 Asia/Shanghai with all latest
code available at launch, this repair and the unchangedaca4c6a8 engine. V788/V789 bind launch and
actual service; V790 records NUMA placement. Workload, pool capacities,
running48/context131072/prefill4096 and disabled new batching remain unchanged.
It has passed the original failure stage with continuing JOIN/tool ACKs.

Observe ops/v62_join_ack_20261015.state.json and the window observer1639353
in ops/v62_join_ack_observer_20261015.state.json. The supervisor stops only
its owned experiment group if authoritative physical_disabled becomes true,
avoiding a long degraded GPU run; no workflow completion guard is added.
Compare120-300,600-900,1800-2100 and3600-3900s using the unchanged analyzers and
corrected native.clock_v2/cache. Historical native has an older harness and
is a development reference only. JCT, throughput, quality and useful FULL
consumption remain unverified. Offline exporters must import their matching
checkout package; the development CLI now inserts its own root. Keep running
sources frozen. Subsequent old candidate,19:51 and deployment statements below
are historical and do not override the current state.

V799 binds the latest V62 sealed-prefix audits, adjacent CPU evidence and a
separate tool-scan candidate. V794 retains each complete-line prefix and hash.
Verified FULL reuse is1.306/8.951GB for JOIN/tool commands; independent
pre-completion started-and-reused events are40/132 with0.943/8.594GB.
Positive FULL-plan event counts are113/1174; command counts208/1140 have a
different denominator. Native FULL H2D in that prefix remains about1.159TB.
Independent last-ACK-to-first-service P90 is6.264/7.101s over64/150 events.
Next diagnose the capacity chain: a recorded reclaim freed3155 tokens, leaving
3267 free, yet a4145-token input/decode reserve blocked a328-token target.
Separate immediate execution needs from future input reservation and revocable
restoration before changing this budget; do not infer that no cold KV existed.
Residency/first-service tails and incomplete conversion remain open.

In1800-2100s, decode request steps395596/381775 are about3.6% above historical
native;600-900s remains346953/386707. The latter64 exact adjacent short-prefill
samples measure9.410ms to the successor launch observer,8.458ms scheduler-thread
CPU:2.435 predecessor results,2.614 physical planning,2.925 successor selection,
0.036 WAR publication and0.448 between phases. V795 validates neighbor identities. No GPU-idle or
removable-JCT inference is justified, and native lacks the same CPU endpoints.
Keep V791's12s49Hz nonblocking stack sample with543 samples/58 errors as
diagnostic evidence, not a CPU-time allocation.

The new undeployed tool scanner sleeps until the earliest possible entry when
no live forecast can yet enter the window. Near entry it may wake before the
old100ms cadence; new forecasts, verified D2H and pending tickets still wake
immediately, while eligible capacity failures retain the old retry cadence.
V797 has225 passing related checks; preserve V796's initial failures.
V798 compares complete CPU maintenance/dispatch fixtures: stable far waits
reduce scans30 to1 and156-context thread CPU16.3%-17.4%; refreshed/eligible
controls are near-flat, including the worst0.44% regression. This omits model
inference, writer I/O, native allocation, DMA and GPU service. Keep V62 frozen
and running; this conditional saving does not justify an immediate restart.
Full JCT, throughput, quality, fairness, tails and recomputation remain unproven.

At23:31 Asia/Shanghai, V62 has129 written completed outcomes, of which127
qualify as natural/JCT outcomes. Preserve graph-budget-intervened results for
django__django-11490 and mwaskom__seaborn-3069; do not count them as natural.
The run remains physically enabled and incomplete. In4800-5100s, observed
decode request steps356829/330775 exceed historical native by about7.9%,
while worker means23.205/22.231ms remain slower. The70/73 short-prefill samples
show selection CPU6.259/4.959ms, run_batch35.181/36.381ms and post-launch
0.255/0.002ms. Control exclusive wall12.054s is not removable workflow JCT.

V809 binds separate engine tree64641f62,157 passing checks and48 CPU cases
inV807. Only the reclaim production method and two test files extend the
running engine. Use native settled FULL Host duplicates when that index is
smaller than device leaves; retain the native leaf path for dense indexes.
No per-decode index maintenance or weakened live refs/locks/DMA checks.
Sparse cases both reduce CPU50.4%-83.0% and recover capacity missed by the
fresh bounded baseline; they do different valid work. Empty-index CPU falls
74.4%-95.6%. Retain dense-index worst+1.37us and already-enough worst+0.25us.
PreserveV805's initial dense-index regression and all initial failed receipts;
V803's mistakenly broad masked-CUDA suite was interrupted, not qualified.
This candidate ande62e1e7's tool-scan reduction remain undeployed. Keep V62
frozen and do not restart a healthy full run for conditional CPU gains.
Input/decode reservation, useful FULL conversion and end-to-end acceptance
remain open; no GPU, DMA, JCT or throughput benefit is established here.

V810/V814 inspect the same1116125886-byte V62 prefix, SHA2566f1bdc44...
06b1e424. Tool/JOIN counts2889/106 identify waits with no FULL allowance
after reservation. Across recorded reclaim diagnoses,493 wait identities
have a selected payload fitting without reserve and2674 have a payload
that itself exceeds free space; categories may overlap over time. These
are not successful opportunities, a complete census or permission to remove
input/decode reservation. Native rejection checks include109917 unbacked
and257591 session-referenced records, with only23 published-waiter checks.

Post-forward dispatch now refreshes the native running-batch reference before
JOIN/tool planning. Missing batch observations preserve the previous state.
The original reproduced six restore slots despite an actual full48 batch.
KeepV811's three failures/one pass;V812 has406 passing related checks.
No adder reread, input-reserve change, KV scan or weakened check is added.
V813 has26 passing offline audit checks. At23:46 Asia/Shanghai,146 outcomes
include145 completed/one incomplete and142 natural/JCT-qualified results.
django__django-14500 ends with finish_reason=length; retain8192 output cap.
Three other completions retain their existing finalization intervention.
This repair remains outside V62. Candidatea297462 now freezes622ae92 runtime
with engine64641f62 and its matching canonical patch. V815 binds589 unique
passing checks:406 runtime,157 engine and26 offline-audit identities.
It is not launched or GPU-qualified. The development default staging patch
is older and must not silently replace this engine.

The two development optimizations now form independent candidate45f46b7,
directly extending V61/9e8a574. Only dispatch, its tests and that candidate's
canonical engine patch change. Engine treeaca4c6a8 changes only the reclamation
production method and its two test files relative to the frozen engine.
V779 binds89 sources and the complete engine tree. All1838 unique checks plus
2 subtests pass, covering1691 inherited runtime and143 engine identities.
The initial Docker check used a cached Django image lacking pytest; preserve
that failure and replace only its identity with the passing cached pytest-image
check. No new registration/deployment is performed. Keep original V61, engine
and19:51 waiter unchanged; earlier conditional CPU timings are not measured
GPU, mean-JCT or completion-throughput benefits for this combined candidate.

V773 adds recorded multi-reference FULL volume to the exact same V59 prefix
as V685, with measured bytes/token and all prior audit fields unchanged.
Only four listed checks have multiple references. The three still-short
snapshots list20.48/471.04/757.76KB, or0.0141%/0.7869%/0.9898% of their
individual deficits. This is not a global cache census or reclaimable-volume
upper bound; other owners' waiting states are unknown. Three real CPU radix
cases show that FULL session_ref counts branch ancestry coverage, not distinct
sessions. One session with two branches can produce2 and release to0.
Keep shared reclamation checks unchanged. All16 audit checks pass; V774 binds
the evidence and source semantics. Continue the existing capacity/registration
and control-cost repairs without changing frozen V61 or its19:51 registration.

The new development engine defers native leaf census until known waiting
victims and their safely exposed ancestors leave a real capacity deficit.
Only the reclaim method and two engine test files differ from the frozen tree;
the separate overlay reproduces the entire development tree. All143 unique
checks pass, including80 real CPU radix/allocator cases. V765 compares60
identical CPU scenes: sufficient known victims reduce thread CPU76.9%-82.9%;
worst no-waiter/invalid/partial controls regress1.74%/1.45%/2.87%.
Preserve V764's earlier nested-function regressions as well. This is not a
whole-scheduler, GPU or JCT result and does not replace V61's registration/
capacity repairs. V767 binds sources, receipts and the separate overlay;
the default staging patch, frozen V61/9e8a574 and19:51 registration are intact.

Development dispatch now skips calls already deferred by existing retry
deadlines. An existing tool ticket always reaches its handler; each PREPARE
deadline is read at its original position, after preceding handlers can rearm
or cross it. No deadline, event/ACK handling or observation configuration changes.
V771 binds557 final checks and12 complete CPU-cycle cases with equal actions,
counters, deadlines, native reads, publication and observations. High-frequency
1/3ms scenes improve0.2%-19.5% thread CPU;3ms profiling-enabled24/156-workflow
scenes improve11.2%/5.2%. The60ms scenes are near-flat. Parent timing counts
match; child counts now omit actual skipped no-ops, so per-child means are not
directly comparable. No trace-frequency weighting or GPU/JCT claim, and this
development change is not in registered V61 or its frozen engine.

Independent quality export now retains all156 tasks in both V59/V58 inputs,
including native's154 completed, one error and one incomplete. Export requires
the saved patch, frozen task identity and matching hashes; empty patches remain
in the denominator. Preserve original terminal outcomes and report officially
resolved partial patches separately from correctly completed workflows.
The existing JCT-eligibility and comparable-rate completeness rules remain.
All23 checks pass (V754); V755/V756 retain the inputs, with source bindings
and semantics in V757. V758/V759 bind the pinned official harness and156
cached images and start CPU grading: one worker, one CPU,8GiB, no GPU device
requests. Stop admitting tasks at19:13 and stop/clean owned containers at19:45,
preserving completed logs before V61. Unattempted/interrupted grading is
missing evaluation evidence, not a model failure. No complete official score
or performance gain is established; raw outcomes and frozen V61 stay unchanged.
The CPU job has now exited after its19:13 admission cutoff. V59 admitted116
containers and produced115 official reports:80 resolved and35 unresolved.
One Docker API read timeout and40 unattempted tasks remain missing evidence.
The unchanged official summary combines these as41 errors; V775 separates
their meanings. V58 is still ungraded. This nonrandom partial cohort cannot
support a complete quality score or cross-arm comparison. No evaluator worker
or owned running container remains; frozen V61 and19:51 stay unchanged.

Development diagnostics now retain predecessor result processing, successor
selection and successor launch-observer endpoints for exact adjacent forwards,
including absolute scheduler-thread CPU clocks. Complete nonoverlapping phases
and their gaps are additive; absent/ambiguous identities or clock overlap stay
unknown. Native service-row identity validation is still required.
The new endpoint is inside the launch observer, after run_batch entry;
retain the separate actual-entry wall timestamp and do not subtract this
new full interval from V743's entry-bounded interval.
There are215 unique passing checks plus12 subtests after replacing the21 old probe
cases with24 final cases. V752's three wrong-engine failures are preserved;
V753 explicitly uses the frozen SGLANG_SOURCE_CHECKOUT and passes all24.
V751 compares enabled old/current probes on the same frozen overlap-loop AST:
added thread CPU is1.921/0.378/0.026us per iteration at prefill periods1/5/16.
This excludes writer JSON and real CUDA/GPU cost, proves no throughput gain,
and cannot recover missing old-trace clocks. Keep this future diagnostic
change outside registeredV61/9e8a574; its19:51 launch and original source,
engine/workload remain fixed. First inspect V61's actual residual and FULL
consumption, then cold-qualify the diagnostics only if the residual persists.

Development selection now retains permitted tagged positions during its full
identity-validation pass and fills only those positions from the same plan.
Native untagged positions, rejections, session/attempt checks and duplicate-ID
failures stay unchanged. V746 passes254 admission/runtime/prediction/benchmark
checks, replacing the overlapping55 V744 checks. V745 checks four queue sizes
and matching/mixed/changed/stale cases. V747 explicitly loads the old3d5f5c4
selector in the baseline arm:20 complete CPU-cycle cases retain equal graph,
plan, selection, counters and timing counts, with no physical actions.
Observed thread-CPU means improve only0.7-19.3us (0.07%-2.96%); near-flat
cases remain near-flat and this does not explain the millisecond residual.
Keep this small change in development. RegisteredV61/9e8a574, the engine,
workload and19:51 launch stay fixed; inspect its actual turnover and next
consumer FULL before further cold changes. GPU/JCT/throughput acceptance
remains open.

V743 accounts for per-sample measured interval unions in the same four
V59/V58 windows. In the decode47 -> short-prefill1 -> decode48 subset,
the mean residual after the previous telemetry-observer entry and before
the next run_batch entry is2.614-3.310ms predictive versus1.201-1.579ms
native, after excluding measured physical/barrier intervals. Therefore
physical planning alone cannot explain the remaining turnover gap.
The residual mixes telemetry tail, sampling, ingest, next maintenance/
selection and waits; it is not measured thread CPU or removable JCT.
New audit19 checks and inherited24 checks pass; replace overlapping
V741/V742 identities rather than adding them. Compare V61's residual and
actual next-consumer FULL against this baseline. If the gap persists,
separate those remaining stages in a future cold diagnostic candidate.
This is offline evidence only: retain the frozen V61/9e8a574, engine,
four registered analyzers, workload and19:51 deadline. Full turnover,
quality/fairness, mean-JCT and completion-throughput acceptance stays open.

V736 cold-qualifies9e8a574 directly on73518: only runtime and JOIN tests
change, with89 source bindings and the same engine. All1691 unique checks
plus2 subtests pass across36 inherited modules, covering1662 old identities.
The single initial Docker skip is replaced by a passing check using an
existing cached image; preserve both receipts without adding duplicate counts.
V734 empty-dispatch CPU delta ranges from-0.03175 to+0.00758us/call.
This is near-flat idle cost, not active-event, GPU or JCT evidence.

Development2d78ab1 keeps a separately bound submitted consumer when an old
parent request retires on epoch advancement. Only explicit prefix continuity,
complete JOIN, known prior input and the same native session/generation permit
continuation. Target epoch matches the graph; old checkpoint bytes remain
bounded by their original input. Compaction, cancellation, expiry and native
visibility invalidate continuation. Completed restores retain consumer identity
for residency checks while another JOIN can progress. Three additional audit
cases retain immediate-consumer and post-submit attribution without changing
registered analysis sources. Internal visibility does not establish HTTP
arrival order; use actual submit/arrival/first-service FULL evidence.

V737-V739 now register this candidate for the still-unstarted V61, superseding
the exact73518 waiter and retaining its frozen records. The user deadline stays
2026-10-15 19:51 Asia/Shanghai, followed by exclusive-resource checks. New waiter
PID4030423 is live; isolated execution remains at exactV59 until cold launch.
Model/workload/engine/budgets and disabled new prefill batching settings stay
fixed. The separate V721 adapter coalescing patch is not included. Do not count
post-completion recovery as pre-RETURN prediction or declare turnover,
FULL-consumption, quality/fairness or mean-JCT/throughput acceptance before GPU evidence.

V718-V720 audit all156 V59 and156 V58-native client sources. There are
786/749 internal summary requests, with71/77 empty length responses.
Their request spans include queueing, generation and transport, not pure
GPU computation or client idle. Two-empty-response compactions occur in
10/14 episodes, implying the unchanged source-history fallback. Generic
raw tool rounds are broader than prior WAIT_TOOL opportunity cohorts.
Use JOIN_WAIT for parent identity; exclude preliminaryV716/V717 JOIN
conclusions. The audit has17 passing checks and binds every raw source.

The summary-related compaction-to-submit P50 is80.17/11.80ms. Development
V721 coalesces CONTEXT_COMPACT and LLM_SUBMIT into one ordered control
batch while retaining the compaction ACK barrier, local session rotation
and delivery-failure degradation. All108 related checks plus2 subtests pass.
This removes one logical delivery, not a demonstrated GPU/JCT cost.
LLM_SUBMIT is now timestamped before that ACK; assess actual HTTP/native
arrival, complete turnover and FULL consumption instead of this moved
phase boundary. Keep scheduledV61/73518e2 and19:51 unchanged. The new
development patch requires its own cold qualification; do not silently
alter the frozen run, summary budget/thinking, model or workload. Shared
summary changes require matching native treatment and quality validation.
The original short-prefill and mean-JCT/throughput acceptance remains open.

Development48838cd repairs normal-mode confirmed JOIN restoration.
RETURN/complete JOIN can create a ticket without a time forecast; an
existing burst keeps its command/ACK identities. Concurrent confirmed
JOINs retain separate targets and rotate past temporarily unplannable
ones. Parent submission, epoch/session changes and physical revalidation
still bind issuance. Earlier semantic/EOS triggering remains available;
the alternative trigger policies have not yet been compared.
V706 cold-qualifies isolated73518e2 directly on43b6310:89 sources,
36 modules,1662 checks plus2 subtests, all1645 inherited identities covered.
V704's no-candidate control adds0.081-0.108us per dispatch, not a speedup.
V701 seals all193 V59 JOIN windows: completion-to-client-submit P50/P90
24.00/94.31ms and completion-to-native-arrival130.51/341.47ms.
Count actual reused FULL separately before completion, between completion
and client submit, between submit and native arrival, and after native
arrival. Missing or unordered boundaries remain unknown; window length
is not guaranteed DMA overlap or isolated JCT saving.
V707/V708 now register V61 with73518e2 and supersede only the exact owned,
unstarted V60 wait process. Retain all V60 frozen files and qualifications.
Use isolatedV709 execution, initially at exactV59 and then fast-forwarded
to the candidate: Main has newer document commits, so its old fast-forward
assumption no longer holds. Do not modify Main or its local documents.
The user moved the start to2026-10-15 19:51 Asia/Shanghai. V713-V715
stop the old15:00 waiter and register the revised one-time deadline.
Wait until that deadline and confirm exclusive resources
before starting predictive only with the historicalV58 reference. The new
wait process is live; actual service and full JCT/throughput remain pending.

V695 cold-qualifies isolated43b6310 in the V691 worktree: two capacity
repairs plus a test-identity-only commit extend9c79eee. Production AST
diff is restricted to two runtime methods and one physical function;
88 sources and the unchanged engine are bound. All1645 checks across
35 modules plus2 subtests pass, covering all1620 inherited identities.
Replace the64 renamed-module checks with their targeted final recheck,
without double counting. Existing cached Docker validation passed.
This does not deploy the candidate or replace registeredV60/9c79eee.
V693 partitions85 JOIN and267 tool restore events in completeV59.
Outcome-conditioned early-FULL cohorts have post-native-arrival P90
292.48/337.52ms, versus10604.85/10222.71ms in all restore events.
JOIN pre-completion residence still has2286.62ms P50 in that successful
cohort. Do not infer causal admission success from selected survivors;
next distinguish early restoration, residency loss and incomplete
checkpoints while retaining the original turnover/JCT/throughput goal.

V679 seals the complete V59 tool-window audit; last-second conditional
absolute error P50/P90 is69.06/542.66ms over25321 rounds. V685 binds
the same1457985716-byte opportunity prefix:10099 plan snapshots retain
an unsatisfied FULL reclaim target,10096 without a published waiting
candidate. These observations can repeat attempts and do not prove a
safe cold victim existed; no native zero-byte budget was recorded.
Development2e37ed2 repairs the independently reproduced zero-byte probe
boundary. Developmentc7bd6ee retains already-backed safe input leaf/state
identities when new Host backup cannot fit, without a second closure scan.
Native publication/reclaim still validates ownership, locks and inflight state.
V688 passes881 checks across10 modules. V689's unchanged-action control
costs0.0195ms/iteration at the original cadence,0.0463ms with forced probes;
retain the added CPU cost. V690 binds the receipts and source versions.
Neither repair changes qualified9c79eee, frozenV60, the reservation or workload.
V695 now qualifies them separately as43b6310; actual candidate/reclaim/
consumption coverage and unresolved turnover/full-JCT acceptance remain open.

V678 cold-qualifies isolated candidate9c79eee, directly extending d925278
with only three additional production-file changes: key equality, same-call
prefill state checks and compact event wire. AST and87 source bindings retain
the original harness/models/budgets and f7b1942b/5262707a engine.
All1620 unique checks across35 modules plus2 subtests pass; the sole initial
Docker skip was rerun against an existing cached image and passed. Retain both
receipts and count the repaired identity once. All967 inherited identities
remain covered.
V676 remeasures the exact candidate's equality fast path: complete CPU
admission improves8.5%-10.9%; rebuilt identities range from0.22% slower to
3.46% faster. Reuse source-identical V667/V671/V672/V656 evidence without
rerunning unchanged scenes or adding component percentages. This candidate
does not replace the existing frozen V60 reservation or claim deployment,
GPU turnover, FULL consumption, quality or mean-JCT/throughput acceptance.

Developmentf29ccb4 omits only top-level None fields from control wire events.
V670 has215 passing checks plus2 subtests. V671 seals four V59 prefixes of
128 events each: bytes fall23.6%-24.1%, combined encode/server CPU3.9%-4.3%,
with identical graph/counters/exact ACKs/timing counts. V672's all-fields
control costs2.2%-2.7% more CPU; retain it and avoid universal speedup claims.
Raw wire batching is unavailable; per-record CPU replay does not establish
socket waiting, online coalescing or GPU/JCT gains. Authoritative logs and
decoded event contents remain complete.
V669 profiles actual cadence/backoff on a156-context/64-node CPU fixture.
Closure capture dominates even without new actions; do not drop native
checks or extend retry times without evidence. The original service-turnover,
FULL-consumption and JCT/throughput goals remain open. V60/d925278 and
the reservation stay unchanged; V678 now qualifies this extension, not deployment.

Development24054ca removes redundant causal-classification terminal reads
and skips final-stage queue indexing when no stage exists. Final authorization
and live native identity checks remain. V664 has231 passing runtime/admission/
prediction checks; V668 adds12 passing benchmark timing/control checks.
Use V667's equal-parent-timing comparison against60275db: complete CPU local
rounds improve9.8%-10.4%, message updates7.6%-8.7%, stable queues3.1%-5.7%.
Event cases save about0.07-0.12ms per iteration; active-stage/disabled-priority
controls are near-flat at0.7%-1.7%. Plans, live selection, full graphs, counters
and timing counts match. V665/V666 omit baseline parent wrappers and must not
support performance claims; retain their receipts and source revisions.
This excludes native residency inspection, neural inference, sockets and DMA.
V678 now qualifies this extension for cold observation; V60/d925278 remain untouched.
Continue toward actual turnover, useful FULL consumption and complete JCT/
throughput acceptance; do not substitute these CPU gains for the full goal.

V662 splits the same sealed V59/V58 prefixes at adjacent run_batch entries.
Across four windows, decode/short-prefill/decode return-to-next-entry P50
is4.9-6.0ms predictive versus2.3-2.6ms native; next-entry-to-result is
about1.3-1.6ms. The second-wave previous-completion-observer-to-entry
P50 is3.595/1.392ms, or3.715/1.297ms for47->1->48 neighbors.
Continue isolating telemetry tail, sampling, deferred physical planning and
next-iteration receive/maintenance/selection before changing another path.
An observer timestamp is not all result work completed, and scheduler entry
is not CUDA launch. Quantiles are not additive or removable JCT bounds.
The24 analyzer checks pass; V60, d925278 and registered observers are unchanged.
Actual engine trees91daa419/5262707a differ only in scheduler ordering and
tests; model/attention/cache/worker and telemetry source equality does not
control realized cache pressure, prefixes or trajectories. Keep the original
GPU, quality, mean-JCT and completion-throughput acceptance open.

V658 binds7386610's immutable-key identity equality fast path. Complete CPU
event/maintenance/planning/live-selection fixtures improve7.5%-8.5%,
about0.07-0.11ms per canonical-key iteration; all graph/plans/selection match.
Rebuilt-key controls improve1.0%-3.4%, while isolated foreign-type controls
cost6.9%-9.5% more and remain explicit. The66 contract checks overlap the
1118 passing integration checks; one real encoder worker check lacks its
local pinned artifact. V678 subsequently cold-qualifies this extension and
does not replace V60 or d925278. Continue addressing the remaining complete
forward/turnover gap; CPU equality savings are not GPU/JCT acceptance.

V651 cold-qualifies the isolated scheduling candidate d925278:80 bound
sources, unchanged f7b1942b/5262707a engine,967 unique checks and2 subtests
passed. Fifteen missing-engine-fixture failures were corrected by linking
the existing engine and rerunning only those15; retain the first receipt.
The candidate preserves the original harness and exporter; feedback5bc57d0
and clock/reference repairbc3820e are separate commits. It does not replace
the existing frozen V60 reservation/registration or claim GPU/JCT acceptance.
V652 completes both historical clock_v2 timelines and the corrected native
comparison cache. Clock-independent demand/JCT/bytes and paired JCT match
the originals. Use the new artifact paths for offline full-window diagnosis.
Native95.376% H2D before1800s and96.847% before the second wave establish
nonuniform migration demand, not a counterfactual benefit or a workload change.

The complete V59 event audit separates implementation loss from workload fit:
69/162 JOIN and216/2529 tool episodes with positive pre-completion FULL plans
achieved early verified reuse, totaling11.398GB. Continue CPU qualification
and lower control/turnover cost before attributing losses to the workload.
V648 adds514 passing previously uncovered cold-path checks; combined with
V646 this is1266 passes plus2 passing subtests and1 explicit-Docker skip,
not a cold deployment or GPU/JCT acceptance.
Nested child delegation is an investigated hypothesis, not an enabled feature
or a change to frozen V60. Current raw child specs lack task middleware.
Any later depth-two harness must preserve direct-parent JOIN/RETURN identity,
stage reset after new work, scoped cancellation, reference safety and the
same hierarchy/quality contract in native. Grandchild completion does not
make root runnable. Require measured queue/cache/JCT benefit, not ACK volume.

The user now explicitly authorizes historical analysis and CPU validation during
the reservation; GPU experiments remain forbidden until2026-10-15 15:00 +08:00.
V643-V647 compare current development with frozen V60/5391a6b: local-rank
event/maintenance/admission CPU improves20.9%-24.3%, while message controls
cost2.0%-3.0% more. Profiled complete tool retries improve23.4%-69.1%.
Adaptive empty-socket polling improves idle18.6% and sparse6.4%, with busy
controls0.4%-0.5% slower. Exact graph/plans, transaction credit and real
socket delivery/ACKs match. GC and parent timing remain enabled.
V646 has752 passing tests plus2 passing subtests and1 explicit-Docker skip.
CPU evidence is not cold qualification or GPU/JCT acceptance; preserve V60.

V636 targets a measured repeated lookup: V59 ran2856427 tool-prefetch calls,
2743143 deferred to the existing retry time. Development code maintains action
counts on registration/retirement and uses O(1) reads; the original retry deadline
now precedes pending inspection after ticket/ACK handling. Accepted hints still
rearm immediately. Five retirement sites share one helper; partial ACK remains
pending. Regressions pass in V646 under the new user authorization. Preserve
frozen V60 and check action/graph equivalence plus thread CPU before qualification.
The old84.916s self wall sum is not a promised saving. Sampling64 of writer
processing does not sample HotpathTiming, which still measures every call.

V635 quarantines old full-timeline timing evidence: the naive local winter epoch
shifted V59's origin by one hour and collapsed all cached V58 native worker
intervals to zero despite468433 decode/42403 prefill batches. Independent V614
windows and original JCT/bytes remain separate evidence. The development loader
now records an aware anchor/capture zone, checks native/client clocks, excludes
pre-monitor service, and rejects obsolete caches; a new cache path preserves old
artifacts. CPU regressions pass; complete traces have not been regenerated.
Regenerate into clock_v2 paths before optimizing from full-band results.
Frozen V60 remains unchanged; its fixed-window observer already passes both zones.
Development launch/resume now carries explicit native-reference timeline/cache
paths, freezes their digests and verifies them on resume. Inherit only for the
same reference; switching references or collecting fresh native clears stale
process environment values. Integration CPU regressions pass.

V634 addresses the V58 child execution loop as a development harness change.
The same command completed76 times with stdout `1\n`; there was no compaction,
and child message count grew2->222. Successful nonempty results were not retained
for repeated-result feedback. Append factual result counts/cost to tool history,
keep source evidence separate, respect observation budgets, and clarify delegated
deliverables/checkpoints. Existing tool availability and termination remain.
The nine feedback cases and related CPU regressions pass; frozen V60
does not include this candidate. After permitted validation, cold-deploy with
matched native harness changes and measure recurrence, natural return and quality.
This is not yet evidence that the live loop or end-to-end performance is fixed.
The real-agent-graph regression also covers the observation budget outside
feedback/status handling and stable earlier messages. A scripted fake model's
final return tests message delivery, not real-model termination.

One-time user reservation: no GPU experiments before
2026-10-15 15:00 Asia/Shanghai (2026-10-15 00:00 PDT). V630 stops
the original V60 launch/startup waiters; V632 waits for this absolute time,
then rechecks the frozen source and exclusive resources before attempting
the existing V60 plan. The user's latest instruction allows CPU optimization,
tests and small CPU comparisons. Do not reinstate the revoked daily rule.
V59 collected156 client completions in8743.634s; its GPU service has exited.

Next cold observation V60 uses the V615-qualified observation cache change;
V620/V621 retain the frozen source and actual registration. Wait for V59's
exact driver and complete postprocessing, exclusive GPU capacity and45GiB
free disk. Keep predictive-only development with cached V58 native reference,
unchanged engine, workload, pools, budgets and disabled new batching knobs.
Recheck all72 source hashes and932 existing passing checks without rerunning
unchanged tests. At16:05 PDT V59 had153 client completions and3 unknown
terminal outcomes with actual model service. Measure live cache frequency,
replenished turnover, useful FULL consumption and full JCT before claiming gain.

V618 archives the six exact V56 client server logs as unique contents:
8.960GB becomes0.215GB after full tar comparison and closed-source rechecks.
Canonical logs and the failed V595 duplication receipt remain. V619 finalizes
the V58 native single-workflow tail:6351.045s,99.551% tool coverage,
0.332% GPU utilization and an empty queue.76 identical successful commands
precede a final workflow-deadline cancellation. Keep all outcomes and budgets;
this idle tail cannot establish predictive migration performance.

Continue the original mean-workflow-JCT and completion-throughput goal through
short-prefill turnover, lower control cost and useful FULL prefetch consumption.
Preserve existing PrefillAdder; new delay, minimum-slot delay, mixed chunk
and interval changes remain disabled.

Latest V614 binds all four corrected V59 windows. Replenished3600-3900s
short-prefill turnover is52.818/43.778ms (+20.65%), and decode service
is364026/388781 (-6.37%). Selection and return handoff still differ by
about2.9ms in median. The original stage and final goals remain unachieved.
The corrected observer completed normally; keep the original live run.
At15:36 PDT,156 workspaces arrived,130 client completions and26 unknown
terminal outcomes were observed. Full quality/JCT/recompute are pending.

Candidate3c14655 retains validated causal ranks across observation-only
STRUCTURED_ACTION/TOOL_WAIT_OBSERVATION/CALL_CENSORED batches with
unchanged sessions. All graph validation, versions, watermarks, timing,
aging and physical residency checks remain. V615 binds72 sources and932
passing checks; six CPU event/maintenance/admission scenarios save52%-55%,
with two state-changing tool controls within about0.7%. This is next-cold
observation qualification, not a live frequency, GPU or JCT claim.

V611/V612/V616 test causal project/command-shape duration history, including
actual failed tool results and excluding cancellation. Across6607 single
execute child waits, central timing improves but new142-171s overestimation
appears. An initial-P90-overrun variant does not reduce the first >10s-early
window count. Reject both online candidates; V616 reproduces the initial
summary with the final evaluator source. Continue completion-stage/work
prediction, measured native admission/consumption and control-cost work.
V609/V610 retain exact independent waits: JOIN queue tails and tool
pre-completion timing tails require different fixes, not a global ETA shift
or lease increase. Keep quality/fairness/recompute and repeated controls.

Earlier V606 adds the complete1800-2100s window:69/64 short samples,
turnover P50 50.935/51.788ms (-1.65%) and decode384610/381775
(+0.74%). All three development windows are close to the stage ranges.
Keep the live run; inspect remaining selection/handoff costs, FULL consumption
and restore waits. The replenished-load result is now retained in V614.

V59 is now executing the frozen d1c3f14/5262707a candidate. V600 verifies
actual GPU service,65 source hashes and fixed scheduling settings;108 first-wave
workspaces arrived. V604 retains complete120-300/600-900s development windows:
short-prefill P50 gaps+9.35%/+7.58%, decode service gaps-3.39%/-3.65%.
These early ranges are close to the stage targets but compare different revisions
and realized requests; later windows, useful FULL consumption, quality and full/
repeated JCT-throughput acceptance remain open. Residual handoff P50 gaps
are approximately2.8-3.3ms; inspect maintenance/admission before the next launch.

V601's observer failed because it interpreted V59's PDT wall strings as Shanghai.
It exited after three explicit failures; the live workload was not restarted.
V602 binds per-arm wall interpretation to native wall/monotonic and client metric
anchors, with16.681/17.760ms first-monitor residuals. The offline analyzer now
supports independent arm timezones;22 checks pass. V603 observes the corrected
windows from the development checkout with exact source hashes. Retain the
original observer source and failure state; never rewrite raw timestamps to fit.

V58 has completed all collection and postprocessing. Its154 common completed
workflows have mean JCT2912.078/2310.910s (+26.01% predictive). Native retains
one output-limit incomplete and one14400s deadline error with an unsatisfied JOIN.
Raw return interventions affect24 predictive/20 native workflows and7/9 roots;
benchmark quality remains ungraded. V596 retains all outcomes and realized demand.
The original performance, quality and repeated-control goal remains unachieved.

The qualified V59 candidate inherits bff29cb and moves deferred JOIN/tool
prefetch/PREPARE planning behind previous result processing and current sampling.
WAR, native load fences, reference protection and normal-loop placement are retained.
V594 binds65 sources and engine tree5262707a:725 CPU checks and12 subtests pass.
V592 compares both loop ASTs with the same probe; CPU differences are approximately
-0.08% to+0.25%, with unchanged dispatch counts. This establishes ordering and
limited loop CPU compatibility, not GPU overlap, useful reuse or JCT benefit.
Cold-observe predictive first with the frozen156-workflow/108+48 workload,
running48 and all existing tool/output/deadline, Host, HBM and prefill settings.
Use the completed V58 native only as a development reference; final acceptance
still requires same-version repeated controls, quality/fairness/recompute checks.

V593 archives24.012GB of stopped V32/V49 mirrors and V54/V55 logs into0.613GB
after full comparison. V595's additional V56 mirror-prefix check failed before
source removal; V618 later archives the exact unique contents without claiming
duplication. Space was independently rechecked
at roughly76GiB after V58 cleanup. Do not attribute all recovered space to archival.
The entries below retain the diagnostic history; current candidate status is V594.

Latest V585/V587 partitions handoff gaps with exact neighboring forward
boundaries; all239/252 predictive/native short samples reconcile. At600-900s
mean handoff gaps are11.523/4.376ms, a7.147ms descriptive difference.
Own-return-to-post-planning-end and prior-completion-to-next-launch mean
differences are3.012/1.812ms. Composite boundaries are not exclusive
function CPU durations or directly removable JCT.
V586 rejects the root-only semantic-observation candidate: all-child callbacks
regress24.1%, all-root improve only7.0%; observed predictive root step shares
are0.3%,8.5%,20.9%,23.1%. The candidate and added tests were removed;
all64 V582-qualified sources match again. Keep bff29cb as the qualified candidate.
Next inspect overlap ordering of physical planning, previous result delivery
and current sampling, preserving native read/free/H2D dependencies, then
maintenance/admission before the next launch. Native still has154 completed,
one incomplete and one live pylint workflow repeating whole-repository lint.
Keep180s tools and existing budgets; no guard or running-source change.

Latest development candidate bff29cb merges ancestry validation/missing-copy
counting and avoids temporary leaf-count lists for internal nodes. V581 retains
3456 capture and14580 ordered-plan/error comparisons; all six108-workflow
scans preserve decisions, counters, native reads and observations. Effective
scan CPU falls3.0%-4.7% relative to the prior candidate; unsupported service
falls only0.3%. Resident negative controls bypass the change and are not attributed.
V582 binds64 sources and27 modules:1402 pass, one explicit-Docker-image test
is skipped. Observer and harness are newly included in the source inventory.
Intervened natural returns are counted and root intervention no longer becomes
natural_terminal; replay retains24 V58 workflows/26 returns without rewriting raw.
V583 seals the committed candidate and unchanged ebdbc183/91daa419 engine.
Keep it out of live V58; CPU/quality-accounting qualification does not satisfy
online turnover, useful FULL, quality/JCT or repeated throughput acceptance.
Native remains live: at2026-10-14 11:45 PDT,153 completed,1 incomplete,
two not yet terminal. Continue the full goal and retain this observation condition.

V58 tail audit separates shrinking batches from idle GPU time. At6000-8100s
GPU sample means remain94.7%-96.4%; the last single workflow's183.842s
averages46.8% with zero queued requests. Root LLM/tool/other partition
that window into87.724/90.326/5.793s. Its identical check runs208 times
without workspace changes; outputs differ only in hexadecimal object addresses.
Do not attribute this tail to180s tool timeouts or exclusively to restoration.
Raw natural-return records retain26 guard interventions across24 workflows,
including7 roots, while the summary reports zero because it only counts
semantic-completion interventions. Correct "all naturally completed" to
"156 client-recorded completed", retain raw evidence and unknown task quality.
Reports: v58_tail_diagnosis_20261014.json and
v58_tail_repetition_and_terminal_20261014.json. This is an audit/doc update;
the live native source, existing budgets and guard configuration stay frozen.

Latest V579 binds all four complete matched windows. The phase observer
finished normally; the verified driver/native scheduler remain live.
Turnover P50 gaps are+9.06%,+16.11%,+7.07%,+6.19%; decode service
gaps are-9.49%,-5.54%,+2.30%,-1.04%. The last window is46.489/
43.778ms and384729/388781 decode steps. Retain realized-demand and
observation qualifications; final quality/JCT/recompute and repetitions
are pending. Do not restart the completed observer or mark the goal done.

V577 retains the matched1800-2100s window:72/64 short samples,
turnover P50 55.449/51.788ms (+7.07%), decode390566/381775 (+2.30%).
Run_batch42.918/36.780ms still requires shape-matched CPU/GPU attribution;
do not extend the first two windows' close launch spans to all phases.
V576 retains native pytest-dev__pytest-10051 as incomplete: the final two
root requests each emit8192 tokens with finish_reason=length, including
the existing reasoning retry. The deadline did not expire, both children
returned and no guard/cancellation intervened. Preserve current budgets,
workspace evidence and all-workflow quality counts alongside paired JCT.

V58 predictive records156/156 completed workflows in8367.726s; native
is live from2026-10-15 00:33 with independently verified PID3865597/
start_ticks616567785. Same-version120-300 and600-900s short turnover
P50 gaps are+9.06%/+16.11%; decode service gaps are-9.49%/-5.54%.
Run_batch medians are already close, but selection/result handoff costs
remain. Realized demand differs; do not declare stage or final acceptance.

V567 seals319 independent complete-line prefixes across both predictive
waves. Confirmed early FULL reuse reaches JOIN81 events/1.599GB and
tool210/11.244GB;9 JOIN completions remain unknown. V568 observes own/
shared protection at native arrival for52/99 early JOIN waits. All40 waits
without observed pre-completion release confirm reuse, versus27/45 with
all early commands released beforehand. This is observation, not eviction
or causal gain. V570 finds54 starts without submit,53 zero-FULL-budget
refusals after action-local reclaim; a bounded scan is not a global census.

V571 separates84 first action-selected work forecasts: work/actual P50
0.373, predicted/actual wall-rate0.929, signed wall error-2.674s.
V572's causal cross-project token-bias correction gives absolute P50
2.606s versus2.674s, leaves500ms hits at7/84 and raises >1s-late
forecasts from4 to34. Keep it offline; a global shift is not qualified.
V573 records H2D estimate/window/uncertainty P50 of71.891/1900.228/
1732.596ms. Next inspect all delivered forecast frames, conditional work
bias and rolling timing with physical feasibility kept unknown when absent.
Continue restored-parent consumption/capacity and measured control costs;
complete native and repeated quality/JCT/throughput acceptance unchanged.

V561/V562 inspect exact sealed V58 JOIN episodes. Of28 starts without an early
submit,27 have post-start action-local reclaim diagnostics: free FULL remains
within the roughly4144-token input/decode reserve, the FULL target is unmet
and no waiting-agent candidate was published;11 episodes record a bounded
native scan truncation. This is not a first-extent size refusal or a complete cold-KV census.
Start-to-completion P50 is472.891ms; native arrival-to-service is12792.362ms.
For11 early JOIN ACK-to-service tails above10s,10 are dominated by the
post-native-arrival phase, with zero confirmed FULL reuse. Unknown reuse is
not counted as waste. Continue admission/consumption and capacity diagnosis.

The next candidate selects a single-leaf reusable checkpoint in one scan of
its validated root-first ancestry while validating the complete suffix.
V563/V565 retain1241 checks across26 modules and58 source bindings.
V564 compares30600 complete plans/errors against frozen V58 and the prior
qualified candidate. Incremental single-leaf CPU falls2.5%-7.2% for depths
16-300; effective complete108-workflow scans fall2.8%-6.1%. Depth4 regresses
0.8%-2.2%; multi-leaf variation and unaffected resident controls are retained.
Keep this qualified CPU candidate out of the live V58 pair. The goal still
requires same-version online turnover, useful FULL, quality/JCT and repeated
completion-throughput evidence; CPU qualification does not satisfy acceptance.

The newest complete3600-3900s window has56/65 short-single samples:
run_batch P50 35.190/50.226ms, turnover46.489/58.199ms, return gap
6.710/4.419ms and384729/373544 decode request steps. Instrumented
self-wall19.733s/300s now ranks control-drain2.718s and opportunity
sampling2.031s first. Keep the historical-reference/demand qualification;
fresh V58 native is pending.
V559 archives old V27 client-side server snapshots and V28 logs after complete
comparison:11.997GB to287.790MB,23:17:19-23:18:29. Results/events,
retained incomplete-workspace evidence and current V58 remain intact.
V558 retains the initial overly strict workspace check without cleanup.
Archival followed the second-wave window; lightweight disk inventories ran
within it, so this is still development evidence, not uncontaminated causality.

V556 retains the earlier complete1800-2100s window:72/67 short samples,
run_batch P50 42.918/49.622ms, turnover55.449/58.001ms, return gap
8.812/4.222ms and390566/357282 decode request steps. Instrumented
self-wall29.432s/300s is led by tool-prefetch5.237s and residency3.331s.
The V56 historical reference and differing demand do not isolate policy gain.
V555 seals223 complete-line prefixes from22:41:53 to22:42:22. Early
next-request FULL reuse is32 JOIN events/0.690GB and181 tool events/
10.186GB.72 JOIN episodes enter a start window,44 submit before completion;
of28 without submit,27 record a FULL capacity refusal and25 had an earlier
free-fit snapshot. Memberships overlap; old snapshots are not current capacity.
Prioritize actual dispatch capacity/reclaim and consumption tails: JOIN/tool
last-ACK-to-service P90 is20.575/13.838s.43 JOIN and one tool boundary
remain unknown, not failed predictions. Full JCT/quality acceptance is pending.

V58 cold-deployed9a54233/ebdbc183/91daa419 at2026-10-14 22:02:57 CST.
V539 verified58 qualified sources and ready service at22:06:10; scheduler
1630621 has served full48-request decode batches. Predictive runs first,
then native on the same shared engine and frozen108+48 arrivals/pools/budgets.
V545 observes large-map NUMA0/1 about52.860/149.102GB, not exact pool
identities or bandwidth. V551 registers four complete phase windows:
120-300,600-900,1800-2100,3600-3900s.
The one-off hold expired at21:30, but the original Python lacked pidfd_open.
V544 preserves that failure and the verified identity-bound recovery; no
workflow was dispatched during the hold. The daily restriction remains revoked.

The development candidate reuses the validated single-FULL-leaf ancestry
order instead of rebuilding per-node depth/provenance maps and sorting it.
Multiple leaves, validation and native enqueue checks remain unchanged.
V543 retains448 pre-hold checks. V541 compares14580 complete outcomes,
including312 positive plans. Single-leaf planner thread CPU falls10.5%-23.2%;
complete108-workflow scans exercising this path fall2.2%-4.8%. Multiple-leaf
fixtures are about0.2%-2.0% slower; the resident negative control does not
execute the changed planner, so its variation is not attributed to this change.
V547 binds1237 checks,58 qualified sources and the unchanged engine for a
subsequent cold observation. Active V58 stays frozen without this candidate.

Latest V549/V550 retain the complete120-300s window:45/38 short-single
samples have run_batch P50 34.606/45.972ms, complete turnover43.793/
52.239ms, selection4.834/1.768ms and run-return gap6.879/4.287ms.
Decode request steps222673/229951 and instrumented self-wall16.253s/180s
retain differing actual demand. Control drain2.776s and admission2.203s
lead the observed costs. V56 native is a cross-version development reference;
prioritize current event/admission costs and await matched-version evidence.
V548 losslessly archives24 stopped V50-V52 logs,10.258GB to295.657MB.
It completed22:15:45 between the early and600-900s windows; V53-V58 raw
evidence remains directly readable. Final JCT/throughput acceptance is open.

The latest complete600-900s V58 window has66/65 short samples:
run_batch P50 36.906/42.150ms, full turnover51.504/50.291ms,
run-return gap9.362/4.273ms and365271/377059 decode request steps.
Instrumented self-wall34.103s/300s now ranks tool-prefetch5.338s first,
then JOIN prepare3.370s and prefetch2.807s. Do not reuse the early
event/admission ranking as a whole-run attribution.
V552/V553 retain1744 main-thread active samples,194 read errors and one
damaged-text exclusion.597 samples end in CUDA stream synchronize; these
include real dependency waiting and are not removable CPU/JCT estimates.
Preserve native dependencies while inspecting preparation/control work.
Fresh same-version native and final repeated acceptance are still pending.

V57 naturally finished all156 workflows and postprocessing at2026-10-14
18:47:37 CST:156 completed, zero incomplete. Frozen0fd9d13/
3c13ad50/572109fe is retained; launcher3811053 and scheduler3811711
are gone, with HTML and workspace cleanup complete. V535 reports paired
mean JCT-5.78% across155 shared completions and completion rate+7.26%
against V56 historical native, with11.73% less output work and5.93%
lower token rate. This is not an isolated policy gain. Early next-request
FULL reuse is69 JOIN events/1.587GB and155 tool events/10.690GB;
JOIN last-ACK-to-service P90 remains19.201s.

The next qualified cold candidate removes GDN normal-EXTEND device-mask
nonzero and four repeated FlashInfer checkpoint metadata readbacks. Use the
existing shape-matched immutable CPU snapshot for logical rows; physical
slot values stay on the current device translation. Missing/mismatched
snapshots and other modes preserve the original fallback. V527 passes437
native tests plus53 subtests; V532 passes68 launch checks. V533/V534 bind
patch ebdbc183 and engine91daa419. V529 preserves complete CPU metadata
equality and about20–37us added short-single CPU preparation, not GPU gain.
V536/V537 register frozen9a54233 for V58 with58 qualified source hashes
and resource watcher1528612. At registration on2026-10-14 19:02 CST,
another user's compute job occupied the GPU; no V58 directory or workflow
had been created. After exclusive GPU
capacity returns, revalidate V57 completion, sources and engine, then cold
run predictive first and native on the same shared engine.
Do not change workload, tool180s, output/deadline budgets or deferred batching.
The launcher permits tracked/untracked docs edits while rejecting execution
source changes; retain the user's working documentation.
Continue bounded CPU diagnosis of physical closure and restore-plan construction
identified in V523, without caching stale physical state or dropping enqueue
validation. Actual first consumption, useful FULL coverage and recomputation
remain open; a registered watcher is not an initialized or accepted experiment.
Actual wait/launch status is recorded in
`experiments/reports/v58_resource_wait_gdn_prefill_20261014.json`.

V56 completed both arms and postprocessing naturally at16:01:22.
V515 retains155 completed/1 incomplete per arm and154 shared completions.
Paired mean JCT is2468.847/2667.417s (-7.44%) and completion rate+2.35%;
predictive realized output work is8.31% lower and output-token rate6.16%
lower. These observations do not isolate migration-policy gains. Retain
different incomplete identities, quality/fairness/recomputation outcomes
and pending repeated matched controls. V56 native is a development reference.

V516/V517 retain the V57 600–900s development window.
Across56/65 short samples, run_batch P50 is44.522/42.150ms,
launch-to-result56.896/50.291ms and run-return-to-result-start9.757/
4.273ms. Tool-prefetch self-wall is7.327s and all instrumented self-wall
36.918s/300s; decode request steps are351776/377059. Actual prefill work
and the public engine differ; separate medians are not causal accounting.
Prioritize remaining control work and consumption rather than assuming
the qualified logical caches have already improved online throughput.

V530/V531 add the complete3600–3900s window. Short-prefill launch-to-result
P50 is60.271/58.199ms and run-return-to-result-start7.613/4.419ms.
Decode request steps336229/373544 differ despite8581/8583 batches.
V522 records206 inclusive GDN mask samples among1831 active main-thread
samples,205 at the leaf; retain109 sampling errors. Active stacks and
independent phase medians do not measure removable CPU or CUDA savings.
Keep current physical checks, prediction refresh and admission constraints.

The deployed candidate updates wait membership from tracked graph deltas,
invalidating only changed member groups and native bindings. Tool insertion
order and JOIN sorting remain unchanged; external graph changes, bulk end
and mirror failure retain complete rebuilding. Reuse the tool census's
affected-invocation set. No physical state is cached or enqueue check skipped.
V507 replays384505 events and45223 proxy bindings from156 sealed V56 files:
769010 ordered queries equal the old code and uncached oracle. Combined
event/binding/query thread CPU is15.639/10.921s, approximately30.2% lower.
Proxy generation, message boundaries and per-event query frequency are not
native delivery or JCT evidence. V508 preserves18.4%/24.5% slower full-rebuild
fixtures at156/512 workflows. V502's empty-binding initial replay is invalid
evidence and retained. V509 passes841 checks; V510 binds58 source files.
The change is deployed with all earlier qualified fixes in V57.
Keep final acceptance open.

The records below preserve earlier qualification and observation states;
older unstarted-V57 and running-V56 descriptions do not override the current
deployment and completed-pair evidence above.

The latest cold candidate invalidates service caches only for the ACK's
direction and any direction actually evicted from the shared 256-row FIFO.
Supported and unsupported opposite-direction entries remain cached when
their evidence is unchanged. Estimator math, history order, legacy fallback,
300s FULL supplement expiry and physical enqueue validation stay intact.
V491 compares 7395 sealed V56 wait queries after 71158 earlier receipts:
all outputs match dda9004; recomputations fall from4656 to4372. Combined
query/receipt thread CPU is996.62/971.04ms in this replay, not online call
frequency, GPU gain or JCT savings. V493 passes803 checks; V492 preserves
one initial old-test assertion requiring unconditional invalidation.
V494 binds56 source files and the unchanged400+29 native qualification.
Include this bounded change with the device-slot and subset-timing fixes
in the unstarted V57. V495/V496 now register frozen d58e982 and live
waiter2985446; the old waiter1922608 and docs observer2133577 were
verified and terminated before dispatch, with their records preserved.
Do not hot-edit V56, launch an extra experiment for this small change, or
enable deferred batching. Post-launch turnover and final acceptance remain
unproven. Broad zero-copy and a general operator framework are deferred.

V497–V499 add the complete same-version3600–3900s second-wave reference.
Across57/65 short samples, run_batch P50 is46.018/50.226ms and complete
launch-to-result55.051/58.199ms. Run-return-to-result-start remains
6.017/4.419ms, selection4.646/2.334ms and post-launch planning0.364/
0.002ms. Decode service is355734/373544 (-4.77%) with2.363M/2.270M
prefill tokens. Twelve matched groups remain sparse; these frozen V56
results do not evaluate d58e982 or prove GPU/JCT gains. Prioritize the
remaining selection/maintenance and useful-service differences rather
than treating the earlier5.18ms gap as universal. Older V57 registrations
below are superseded byV495/V496.

The user revoked the daily00:00–06:00 experiment restriction on2026-10-14.
Remove23:59 automatic stopping and the old06:00 resume registration.
Cold launches and existing runs now depend on actual resource availability,
without a daily blocked interval. Historical cutoff evidence stays intact.

V56 predictive finished naturally; same-version native is running on
frozen661ed45, patchb8e7942c, enginec916d585. V451 is the
actual registration; V450 is a preserved argparse failure before dispatch.
V453 validates all50 source hashes, HTTP service and fixed capacities.
Final predictive has155 completed and1 incomplete across all156 terminal
records, duration9325.121s,29 multi-spawn workflows and191 JOINs. HTML
is complete. Native startup at13:18:38 verifies scheduler1616628 and
unchanged48 slots/context131072/disabled new batching. Preserve budgets. V55
finished postprocessing at10:20:55 with154 completed and2 incomplete;
retain the two root output truncations and all156 terminal records.

The deployed immutable prefill-only CPU mask/seqlen snapshot is distinct
from decode completion metadata. Normal EXTEND with matching shapes uses
CPU-known logical rows and current device-translated physical slots.
Fallbacks and stream/state boundaries remain intact. V447/V448 qualify
176 complete geometries,383 tests plus29 subtests and unchanged50 runtime
sources/793 prior checks. V445 measures5.6–9.2us added median CPU;
GPU/phase/JCT gains remain unproven.

V455 does not show a stable improvement:600–900s short-single run_batch
wall/thread-CPU P50 is42.163/41.480ms against V55 predictive40.815/
40.223ms. Most of15 matched cohorts have only1–2 samples. V456 compares
368701 versus historical-native380722 decode steps with different prefill
work and topology/version; it is diagnosis, not native acceptance.
Measured Python self-wall is34.305s/300s, including7.166s tool_prefetch.
Keep complete forward phases, clipped worker intervals and pure kernel
time separate.

Latest complete second-wave evidence isV469/V470,3600–3900s. Decode
request steps are355734 versus historical-native353757, with different
prefill work and topology/version. Short-single run_batch wall/thread-CPU
P50 is46.018/45.010ms versus V55 predictive46.756/45.799ms; the matched
cohorts remain sparse. Python self-wall is16.520s/300s; control_drain,
opportunity_sampling,event_apply andjoin_prepare lead the current costs.
Do not continue ranking tool_prefetch first from the earlier window.

V471/V472 seal independent live prefixes and report the positive-plan/
free-list-fitting/actual-early-FULL/confirmed-reuse funnel as161/161/78/69
for JOIN and1993/277/191/171 for tools. Confirmed early FULL is1.502GB/
10.881GB;3 JOIN completions stay unknown. Nine JOIN and8 tool >10s tails
are dominated by post-native-arrival waiting, with only20KB/zero confirmed
reuse. Three JOIN and8 tool tails instead precede completion;4 tool tails
are client-submit dominated. A shared ETA shift or global lease extension
does not address these distinct paths. V473's15 service-progress
rejections across14 JOINs have observed service ages262–395ms, with no
already-overtaken selected endpoint; this association is not runtime replay.

V474 binds the incomplete Django-11138 root's8192-token length truncation,
repeated prose, returned children, satisfied JOIN and unexpired deadline.
Its earlier test-analyst graph hard-budget finalization is a separate event.
Retain both facts and frozen caps.

V482 seals the final predictive traces: confirmed early FULL remains
69 JOIN waits/1.502GB and171 tool waits/10.881GB. The isolated candidate
fixes a measured selection-to-enqueue gap: reclaim shrinks a supported
FULL+Mamba burst to a small FULL-only prefix, whose direct service estimate
is unsupported. Only a strictly reduced per-pool payload at the same
key/checkpoint may use the current larger selected payload's cost as a
planning allowance. Direct estimates, latest forecast, EOS/tool and native
identity/prefix/capacity/transaction checks remain authoritative.
V479/V484 cost replay preserves all708 direct estimates and adds support
for617 queries across16 JOIN/5 tool waits; this is not action replay because
the log omits native leaf generation. V480 passes781 runtime checks.
V481 records+0.162us common supported-window median CPU and+7.306us on
the new fallback. V483/V485 bind the isolated source; GPU gains are unknown.

V488 now provides the same-version600–900s phase reference. All-short
run_batch P50 is42.163/42.150ms, but launch-to-result-completion is
55.989/50.291ms (+11.33%) and run-return-to-result-start9.458/4.273ms.
The68/65 populations and15 matched geometry groups are sparse; separate
medians are not causal per-request accounting. Next prioritize post-launch
planning and next-iteration maintenance in that interval while retaining
normal native dependencies. Decode steps are368701/377059 (-2.22%);
actual demand/NUMA placement still differ. Final acceptance stays pending.

Next actions:

1. Finish the existing V56 predictive→native pair with identical engine,
   probes and configuration. The registered read-only observer captures
   native placement/startup and the complete600–900s phase comparison.
   It neither launches a workload nor profiles GPU execution.
2. Isolated candidateV463 is exported and qualified byV466/V467 as
   patch3c13ad50/tree572109fe; keep it for the next cold observation.
   It removes prefill per-request slot.item by
   stacking current device virtual slots; mask/seqlen use explicit
   nonblocking uploads. Physical translation, allocation/references,
   checkpoint/state and stream barriers stay unchanged.400 native tests
   plus29 subtests pass, with both the initial empty-fixture rejection
   and final checks retained. V462's CPU medians improve1–7us, but the
   48-request lazy mean regresses19.1us. Cold GPU benefit is unknown.
   Arrange predictive-first cold observation after the complete V56 pair
   and postprocessing; deployment must not alter either existing V56 arm.
   The corresponding V57 launch state is authoritative for registration.
   V486/V487 supersede the unstarted0bd5664 waiter with frozen d7980a4,
   engine3c13ad50/572109fe and54 source bindings. Launcher1922608 waits
   for all V56 postprocessing; the prior registration/checkout is retained.
   No new run directory exists and no V56 workflow was cancelled.
   V465 separately records the revoked non-execution launch wait; all49
   other prior qualified sources and the live V56 engine remain unchanged.
3. Use V488 to measure and reduce run-return-to-result-processing work.
   Separate post-launch planning, next-iteration maintenance and normal
   overlap dependencies; do not infer removable CPU from independent medians.
   V459's
   894 active samples/85 read errors locate32 prefill scalar-readback
   leaves and275 decode-result synchronization leaves; these are call
   paths, not removable CPU or justification to delete data dependencies.
4. Prioritize the161-to78 JOIN submission gap and post-arrival tails using
   exact request/checkpoint and timing evidence. Continue tool/JOIN
   coverage, prepare consumption and admission/residency analysis without
   counting ACKs or demand handoff as early useful FULL.
   Preserve repeated matching controls for final JCT/throughput attribution.

Existing PrefillAdder stays enabled as before. New PrefillDelayer,
minimum-slot delays, mixed chunk and interval changes stay disabled.
No canary, new completion guard, hot source edit or budget change.
The revoked daily reservation imposes no stopping or launch delay.
Final quality/fairness/recomputation and
mean-JCT/completion-throughput acceptance remains pending.

The following paragraphs retain earlier evidence; their collection-time
running states do not override the status and next actions above.

Earlier V430 independently seals live V55 prefixes at08:45:32–08:46:22.
Confirmed next-request early FULL reuse is73 JOIN waits/1.381GB and124
tool waits/8.680GB;12 JOIN completions remain unknown. V431's observed
pre-completion positive/fitting/actual-early/confirmed-reuse funnel is
146/146/83/73 for JOIN and1729/229/147/124 for tools. Fitting snapshots
are neither transfer reservations nor proven timing opportunities.
Six JOIN and11 tool >10s tails are native-queue dominated with zero
confirmed FULL reuse;11 other tool tails are completion dominated with
62.62s median latest-ACK lead and0.0273/1.219GB reuse.

V432 binds the collection's actual256 mixed/64 H2D histories. V433/V434
evaluate61,229 transfers using only ACKs completed before each submit.
Replacing the mixed history with32 samples per direction/shape reduces
support from60,778 to59,860 and raises overall median absolute error from
0.326 to0.412ms, so that replacement is rejected. The71 FULL-only H2Ds
have24.11ms original median absolute error for controller submit-to-ACK;
this is neither child RETURN nor exposed GPU-stall accuracy.

The development candidate preserves existing typed/legacy estimates.
Only unsupported FULL-only H2D queries may consult an additional32
completed same-shape receipts aged at most300s, retaining minimum3 and
the original4x size range. Expiry also invalidates cached fallback results.
V435 exercises the actual receipt/estimator/cache path on61,297 queries:
all60,778 previously supported estimates remain identical;18 independent
tool waits/22 rejection records gain timing support. These are not newly
executed actions or demonstrated scheduling gains. V434's supplementary
policy is exploratory, not a separately held-out policy validation.
474 focused plus319 cross-module checks pass. V436's cached prediction
median CPU increment is about0.02–0.16us; GPU/JCT effects remain untested.

V438/V439 subsequently observe153 completed,2 incomplete and1 unknown
terminal outcome at09:37:44. Django-11740 and Django-12273 both end on
the root's8192-token length-truncated repetitive response, each with all4
children returned, both JOINs satisfied and deadline unexpired. Each also
has an earlier child hitting the existing2048 graph hard budget at step2017.
Preserve these distinct facts and
the failure; do not change frozen caps or attribute this to H2D. Stratify
future child timing labels with the guard audit, since a RETURN count alone
does not establish an unguarded natural terminal.
V449 supersedes the earlier unstarted V56 c319339 registration. The next
frozen replacement includes this supplement and prefill metadata preparation
with the existing predictive→native order. Main must remain an ancestor of
the new frozen target until cold deployment. Continue JOIN timing,
native-queue/consumption and too-early
tool investigations; do not enable new prefill batching/delay or infer net
benefit from additional support. Final same-version repeated JCT/throughput
and quality/fairness/recomputation acceptance remains pending.

The earlier V56 candidate added a shared single-request Mamba mapping
fast path to the qualified JOIN refresh. V418/V421 identify316 main-thread
samples at the original ping-pong mapping assignment, but retain196 read
errors and3.85s sampling lag; this is call-path evidence, not CPU accounting
or native causality. A nonnegative integer singleton row now uses a
shape-preserving slice for both mappings; other indices retain native
advanced indexing. V423 verifies identical allocation, generations and
mapping outputs across six CPU cases. Single-request mean thread CPU falls
about3–7us; the actual CUDA synchronization/prefill effect is unverified.
V426 binds43 prior sources and b1921753/c9d1fbd;363 native checks plus29
subtests pass. The live V55 engine and runtime remain frozen.

After V55 naturally finishes, run the updated V56 predictive first, then one matching
native arm to obtain the missing full short-prefill phase reference.
The previous predictive-only waiter was superseded before experiment
creation; V417 records no added workflow censoring. Preserve workload,
budgets, existing phase probes and disabled new prefill delay/batching.
V420/V427's complete3600–3900s window has347412 versus historical
native353757 decode request steps(-1.79%), with different realized demand;
53 short-prefill phase medians are4.356/46.756/2.609ms for selection/run/
result. This does not establish same-version performance acceptance.
V419 losslessly archives20.278GB of superseded server traces to0.636GB,
preserving current V49–V55 and V29 sources and all outcomes/censoring.

Current next cold candidate fixes provisional JOIN endpoints becoming
detached from accepted rolling forecasts. V408/V409 associate55 timing
rejections across16 independent waits:49 already had newer fresh,
high-score, announced forecasts and48 had higher center endpoints.
Association does not reconstruct runtime state or prove physical capacity.
Selection, uncertainty and final enqueue now share the latest causal
request/epoch-bound work projection; retain live upper fallback and reject
stale, weak, unannounced, tool or unavailable-service evidence.
Post-drain timing failure immediately clears the JOIN ticket.
950 tests plus2 subtests pass; V412 binds43 sources and the unchanged
engine. Equal four-node simulated dispatch costs0.789→0.833ms mean
thread CPU, about44us more; this correctness fix is not a CPU speedup.

Finish frozen V55, then cold-deploy the qualified candidate. Do not hot-edit
V55 or enable new prefill delay/batching. V413's complete2100–2400s window
observes380533 versus historical native374684 decode request steps(+1.56%)
and2.115M versus2.206M prefill tokens. Instrumented self-wall is19.150s;
short-prefill clipped worker ratios still range1.19–1.57. V415's80 phase
samples show selection/run/result wall medians4.283/50.355/3.525ms;
launch telemetry is only0.072ms and native equivalent phases remain unknown.
Workload/topology/
version confounding remains. At08:03 all77 recorded first-wave results
were completed, with the second wave pending. Continue admission/consumption
and short-prefill investigation; mean JCT and completion-throughput
acceptance still requires matching controls.

Latest V407 uses V55's complete600–900s window:384119 versus historical
native380722 decode request steps(+0.89%), with1.588M versus1.891M
realized prefill extend tokens. Version/topology and demand differences
preclude a causal gain. Consecutive full48 decode ratios are0.991–1.010;
short-prefill clipped worker ratios remain1.350–1.497.64 current phase
samples show selection/run/result wall medians20.637/40.815/3.825ms;
launch telemetry is0.177ms and compute-stream span37.694ms. Native
equivalent phases are unknown. Instrumented self-wall is32.561s/300s,
led by tool_prefetch6.124s; this is not removable CPU or JCT.

V406's independently sealed live prefixes confirm early FULL consumption
in10 JOIN events(0.172GB) and67 tool events(5.170GB). Preserve68/3
unknown JOIN/tool completions. Completed JOIN starts/submissions are40/17,
tools78/77. First distinguish absent actual service from expired/exhausted
work forecasts and submission-state changes in the16 JOIN
service_progress_unavailable events; reasons overlap. Then reduce the
largest planning/turnover scopes and verify effective residency/admission
consumption. Keep V55 frozen for the second arrival wave and final outcomes.
Do not enable new batching/delay settings or infer final JCT acceptance.

d65ed0f fixes nested worker rebinding: SGLang's default automatic NUMA
configuration replaced the outer preferred policy with membind=1. The06:58
attempt failed during Host initialization; V394 preserves exactPID1965823,
the generated wrapper and zero-workflow failure. V393 exercises actual
upstream CPU spawning: parent preferred becomes worker bind with automatic
binding, and stays preferred when disabled; CPU affinity is identical.
The launcher now owns NUMA placement without automatic rebinding. V396
binds41 sources,126 startup checks pass, and core runtime/engine stay fixed.

V55 restarted at07:08:25 and became healthy at07:11:24. V398 verifies all41
source hashes, the qualified engine, HTTP200 and actual200.050GB Host.
V392 observes148.310GB FULL onNUMA1,11.690GB FULL plus40.050GB Mamba
onNUMA0. Fallback works; this is not an all-local Host topology. The server
retains48 slots, interval0 and disabled mixed chunk/new prefill delay.
At07:23 the first108 directories existed and four workflows had completed;
there were no evidence drops/writer errors. Keep execution frozen for the
complete600–900s window and eventual quality/JCT/throughput assessment.
V400/V401 stop56 verified old idle sandboxes and losslessly archive217 old
workspaces,26.510→2.368GB. Current traces/models/results are retained.

Current cold startup uses9dff40b: CPU stays onNUMA1, memory prefersNUMA1
with fallback, and extra startup reserve returns to8GiB. Keep200GB/80:20,
the device pools, workload, predictions and scheduling settings fixed.
Freeze the policy in new plans; legacy frozen-arm resumption keeps bind.
V390 binds41 sources with all27 prior core hashes unchanged and126 passing
startup-chain checks. The obsolete literal-handoff assertion also fails on
b7b56dd; its correction checks the existing configurable behavior.

The third06:28 attempt allocated200.050GB Host and attempted health prefill,
but produced no completed health response or workflow clients. Exact-PID
kernel evidence identifies aNUMA1 memory-policy OOM whileNUMA0 had about
85GiB free. V387 preserves this initialized-server startup independently;
V385/V386 diagnose thread state and kernel reclaim samples, not scheduler
overhead or JCT. The06:00/06:18 preflight failures remain inV379/V382.
The temporary6/5GiB reserve did not solve runtime memory pressure.

Next, cold-start during permitted hours and verify pool census, actualNUMA
page placement, healthy service, and the first complete600–900s window.
V29 remains historical development diagnosis; final native controls must
match the new topology policy and report placement. No new prefill delay,
batching or guard is enabled. Mean JCT and throughput acceptance is pending.

V55 triggered at06:00:01 and verified deploymentfe05cac/a8f4ea97/
f7e0a895, then exited before service or workflow arrival: NUMA1 estimated
192.36GiB while the200GB Host plus8GiB reserve required194.26GiB.
V379 preserves the startup-only failure; do not manufacture156 errors or
censored workflows.2e5f58d reduces only the extra startup reserve8→6GiB;
actual200GB,80:20 and NUMA1 stay fixed. Live preflight now passes;
actual local pinned allocation remains to be observed after cold restart.
V380 binds28 sources, retaining all27 prior core hashes/checks; no new
targeted test for this guard configuration. Reservation management validates
actual launcher termination and receipts, and repeats the same Host preflight
before spawning. Wait for unavailable resources without creating failed runs;
then verify real pool census, healthy service and actual complete windows.

V377 links all31 fully released early-tool waits through exact invocation/
tool_run_id and sealed clocks. Each has one active call at first H2D submit;
13 roots/18 children all lack a completed same-input prior.22 are test-suite
commands;15 children have project-class support and18 have input neighbors,
but mixed short-call history does not establish current long-call timing.
20 callback error outcomes are offline labels, not causal prediction inputs.
30 whole rounds contain one observed call; one contains two, so preserve
callback/whole-round labels and unobserved actual execution starts.
Do not expand root repeat support or shift ETA based on this cohort.
Later command-shape/history-support improvements require independent-project
tail validation; V55 execution,180s tool cap and other budgets remain frozen.

6bd0dd3 reuses JOIN parent identity projections for unchanged RCCG and
context/session revisions. Return, completion, epoch, rebind and abort
invalidate those revisions; causal failure explicitly discards the cache.
All physical ancestry/input/pending/capacity/transfer checks remain live.
V374 verifies six same-input CPU cases with identical projection, complete
pressure publications, actions, observation records and native read counts.
Repeated projection reduces thread CPU83.6%–84.8%; complete PREPARE
reduces3.27%–4.78%. Reset projections regress17.8%–21.7%; two backed
complete cases regress1.68%/2.02%, while the unbacked case improves6.12%.
Retain these regressions: real cache reuse and system benefit remain unknown.
804 checks plus two subtests pass; V375 binds27 sources to the unchanged
qualified engine. Include this cold candidate in V55 after06:00 resources
permit; new prefill delay/batching options and workload/budgets stay frozen.
The first complete600–900s window must measure actual turnover, controls
and consumption; same-version JCT/throughput final acceptance is still pending.

V368/V370 losslessly archive297 legacy workspaces and V27/V32 server logs;
all files pass GNU tar comparison before raw cleanup. Available disk recovers
to77.30GiB at completion. V29 native and latest V49-V54 remain directly
readable; preserve original outcomes and administrative censoring. V367 stops
only217 verified idle legacy sandbox containers, retaining their layers.
V55 resumes no earlier than2026-10-14 06:00 when GPU, port and disk permit;
the21 execution/analysis source hashes qualified by V362 remain unchanged.

V369 extends protection observations to all273 sealed early-FULL waits.
Of84 JOIN waits,36 release every early command's protection before completion;
25 still confirm0.678GB reuse from0.934GB transferred. Of189 tool waits,
31 have that release pattern and confirm only0.0478GB from3.437GB;
their latest-ACK-to-completion median is89.245s. The41 JOIN/158 tool
waits with no observed pre-completion release confirm0.916/0.916GB
and11.094/11.149GB respectively. This transfer-selected population
does not prove a global lease extension would reproduce that reuse or
describe all unissued opportunities; three tool shared-lock associations
remain unknown. Prioritize effective tool-tail restoration timing and
jointly verify complete inputs, residence and admission consumption.
Retain JOIN reuse after release as counterevidence to equating unlock with
eviction. V55 policy remains frozen; real throughput/JCT acceptance is pending.

V364 links the existing18 native-queue-dominated tails (7 JOIN,11 tools)
to protection and exact next-request admission observations. All140 early
FULL commands begin with3s protection and release for service_window_expired
before the completion boundary. No native locks remain observed at arrival;
no matching restore-ready priority-admission receipt is present. Unlocking
does not prove eviction, and missing admission receipts do not prove missing
planning or priority attempts.15 waits have in-queue samples with positive
Host-backed FULL gaps; three tool waits are unsampled. Those sparse hints
do not verify input or establish readiness throughout the queue interval;
legacy reload pool presence is not exact page/component evidence.
Do not treat these18 selected waits as a population estimate or extend all
leases. Extending only post-arrival protection cannot fix protection already
lost before completion. Prioritize effective restoration timing, then jointly
verify complete input restoration, bounded residence and admission consumption.
Keep V55 execution unchanged and cold-start only after06:00 with resources.
Real throughput/JCT and same-version final acceptance remain outstanding.

Commit3a47942 maintains the existing global WAIT_TOOL/family census from
RCCG deltas instead of rescanning all retained invocations after every version
change. Include changed-context members and completed children's return targets:
a caller can resume WAIT_TOOL without appearing in awakened_invocations.
Keep full rebuilding after a version discontinuity and discard both membership
and counts after causal failure. Predictor values and the known local/global
training contract remain unchanged. Decode text is already filtered before
RCCG, so it is not the graph-version rescan cause.

V358 measures complete event/feature cycles, preserving about3.5us additional
CPU when there is no query. Its93.66% improvement at7644 retained invocations
is a large synthetic geometry. V361 uses all156 latest sealed V54 client
prefixes:390742 events,12211 batches/comparisons, zero application failures
and at most1235 retained invocations. Features equal the full-state oracle.
Combined event-plus-one-query thread CPU averages240.62/206.22us (-14.30%);
event maintenance rises181.96/196.76us while feature time falls58.66/9.46us.
These are separate measured intervals summed without overlap. Offline
timestamp sorting,32-event batches and one query per batch differ from live
socket/query cadence; native registration, cache, model and GPU are excluded.
Keep the administratively censored task, and do not invent completion/JCT.
781 checks plus two subchecks pass. V362 binds21 sources, unchanged prior
CPU method bodies and f7e0a895/a8f4ea97. Register the updated V55 cold candidate
for06:00+ when resources are available. Validate real phases and total control
cost, useful FULL, quality/fairness and JCT; the final goal remains unproven.

V355's initial PREPARE profile lacks native FULL-leaf/Mamba-LRU indices and
overstates fallback publication scans. V356 supplies those index shapes and
shifts attribution toward ancestry observation and wait validation. Both are
cProfile-distorted CPU stand-ins, not evidence of live native hotspots.

Commit6186577 avoids a shared-ancestor state table for a single FULL leaf in
the ordering hint. Full ancestry, node identity/cycle, namespace, pending and
live residency checks stay intact; multiple branches retain the shared table.
V351 uses the qualified engine RadixKey and48 identical CPU fixtures spanning
1/8 leaves,4/8/256/1024 depths, mixed/Host/device/pending/short/foreign inputs.
Outputs match. One-leaf non-namespace thread CPU improves median13.13%;
multi-leaf median regresses1.89% and worst9.87%. Preserve these regressions;
online leaf/depth frequency and actual gains remain unmeasured. Hints are still
input-unverified ordering evidence, not restoration authority.
769 checks and two subchecks pass. V353 binds19 sources plus both CPU candidates
and unchanged f7e0a895/a8f4ea97. Update the V55 frozen target and registration
for a permitted cold launch; do not deploy before06:00 or while another GPU
job is active. Real phase/service/consumption and same-version JCT acceptance
remain outstanding despite the local CPU improvements.

Commit978bdc9 removes eager temporary deque allocation from every semantic
decode callback. Allocate once for a new request; retain both empty and
nonempty histories and the existing progress/marker/EOS behavior. V347 compares
1200 identical-input cycles against frozen2c488a9 with156 workflows,64 tracked
parent handoffs and48 serviced requests. Completed-batch mean wall time is
111.822/91.067us (-18.56%); admission changes about+0.06%, with identical order
and observation state. This local CPU result is not whole-prefill or GPU/JCT gain.
V348 counts allocations separately:960 versus48 for48 requests over20 batches,
or zero new allocations when history already exists.534 checks and two subchecks
pass against the existing candidate engine. V349 binds14 sources and these
CPU/allocation receipts to unchanged f7e0a895/a8f4ea97. Include the qualified
fix in the next V55 cold target; keep workload, budgets and disabled batching.
Start after06:00 only when the GPU is available, then validate real turnover
phases, admission waiting, useful FULL consumption and full quality/JCT outcomes.

V344 exactly replays all 18 recorded V54 tail curves using linked roles,
commands, elapsed time and effective counts inferred from all curve points.
The timing export uses calls active within the target invocation; runtime
uses the global WAIT_TOOL invocation census. Effective counts are 1-6 although
each selected invocation has one active call. Correcting this mismatch alone
predicts earlier in 16 cases: residual P50 changes from 1.022s to 0.665s
against actual 152.462s. Do not deploy a count-only timing change as a tail fix.
The operational head uses coarse tool names, not observed command class,
shape or completed history. Fitted leads are 50ms-5s and curve knots extend
to131.072s; these selected forecasts are within5s, so horizon extrapolation
does not by itself explain their errors. Exact input replay also means the
0481982 metadata fix does not fully explain this selected cohort.

V345 audits all23995 sealed tool rounds, retaining one unknown completion.
22183 have pre-completion forecasts;22099 have last-second forecasts with
round-weighted absolute-error P50/P90 of73.55/586.58ms. These are conditional
last-second development errors, not initial-window or unseen-project accuracy.
Of17302 first predicted500ms entries,14923 actually lie within500ms and2379
are early. In7397 rounds with common supported generic-curve/residual-quantile
snapshots, median absolute error is451.31/232.78ms. The quantile alternative,
however, has134 first500ms entries over10s early versus22 for the curve, and
980 curve-supported snapshots lack unexpired quantiles. Do not swap heads
based only on the median or fill missing timing support with zero.
Among569 positive-FULL rounds missing recorded windows, only7 recorded
snapshots also have free capacity. Snapshot categories overlap and share pages.
Keep V55 execution frozen and prioritize its phase/admission/consumption
measurements. A future timing candidate must separate local parallelism from
global pressure, evaluate causal command structure and surviving-history
features, and test tail/coverage alongside median errors. Offline scripts run
nice19 with idle I/O and CUDA hidden; no runtime policy or model flag changed.

V339 separates the latest V54 tail by independent waits. Among 84 early FULL
JOINs, latest ACK to completion P50 is 2.448s; among 189 tools it is 0.235s.
For tool tails above the existing 10s maximum lease, 18 are dominated by
pre-completion waiting (2.330GB, no next-request-confirmed reuse), 11 by
native queue waiting (0.870GB, 0.0478GB confirmed), and two by client
postprocessing. Preserve these distinct causes; do not apply a global ETA shift.

V340's direct context/epoch lookup misses legacy callback fields and must not
be interpreted as zero active tools. V341 verifies exact invocation/run IDs,
sealed prefixes and the same monotonic-to-wall bridge as V334: 18 single-tool
episodes, 17 operational curves, one project prior, no same-input history.
One predicts 0.626s remaining after 8.98s while 156.27s actually remain.
Callback wall time includes backend waiting; actual execution starts remain
unobserved. A 347.85s callback alone does not prove the fixed 180s execution
limit failed. Keep budgets and workload unchanged.

Commit 0481982 fixes intermediate metadata in multi-event tool refreshes.
Two cases previously submitted unknown fields and deduplicated away the final
surviving command. Submit once per context after metadata updates; preserve
the singleton path, predictor math and physical checks. 534 checks and two
subchecks pass; V342 binds 14 sources and unchanged f7e0a895/a8f4ea97.
Real batch frequency and incremental CPU cost are not established, and this
fix is not proven to explain the selected long-tool errors. Include it in
the qualified V55 cold candidate, then verify phases and actual consumption.

V334 seals the latest V54 full prefixes; V335 qualifies the actual reservation
cutoff at 23:59:01.870697 CST. There are 185 known JOINs and 23994 known tool
waits with pre-cutoff first service, plus one unknown completion per source.
Early, next-request-confirmed FULL reuse is 73 JOINs/1.771GB and
160 tools/11.142GB. Sampled fitting positive-FULL events are 161/346;
88/186 remain without early confirmed reuse. Fitting snapshots do not prove
capacity at the trigger. Completion-to-client-submit P50 is 18.92/15.31ms,
whereas native-arrival-to-service P90 is 13.45/12.85s and JOIN last-ACK-to-service
P90 remains 17.34s. Investigate actual admission, capacity and timing jointly.

V336 rules out treating zero-work validation as the principal cause: of 110
zero-work start records, 97 lacked free-list capacity; 94 next observed
no-plan records reject FULL/Mamba budgets (72/22). Preserve the existing
endpoint, EOS and allocation checks. Commit 8fec5be adds only bounded evidence
to formerly silent enqueue rejections, preserving the pre-drain JOIN identity
and reporting causal/transfer/identity/plan/prefix/slot changes from already
read state. No additional physical query, allocation or scheduling change.
267 checks and two subchecks pass; V337 binds 12 source hashes and the unchanged
f7e0a895/a8f4ea97 engine. The prior CPU benchmark excludes these failure-only
records. Cold-test this observation candidate in V55 after 06:00; real phase
cost, rejection attribution and end-to-end gains remain unverified.

V54 actually stopped for the reservation at 2026-10-13 23:59:09 CST:
all owned processes and containers exited; the GPU recheck was 14MiB/0%.
V326 retains 155 completed and one administratively censored django-11211
without fabricating summary.json. The common completed subset has mean JCT
2682.129/3028.016s (-11.42%); this is not the all-arrival mean or causal
acceptance. Two exact command hashes repeat 19/15 times with empty output,
exit=1 and cumulative 2282.641/901.920s, so the tool tail remains visible.

V328 records verified compression of all 156 V54 workspaces, including
untracked files and the censored task, at 2026-10-14 00:53:26 CST.
Full tar comparison passed before cleanup: 39.014GB allocated sources became
a 4.227GB archive, leaving no raw workspaces. Traces, results, frozen plan
and censoring remain. The existing archive README contains the restore command.
Available disk afterward was 52,086,583,296 bytes (48.51GiB); recheck capacity
and GPU availability at the permitted cold launch. V55 has not started.

V331 also fully compared and archived 72 server/opportunity JSONL files from
stopped V40-V48: 21.889GB sources became 0.684GB archives. Per-run restore
pointers and the existing archive inventory preserve access; client traces,
results, plans and censoring remain. Keep V49-V54 and native references direct.
The last available-space snapshot is 65.53GiB. Require at least 60GiB before
launch based on recent workspace/trace allocation, instead of the old 20GiB
check; do not change workload or stop another user's job.

V329's independent cron audit is registered for the first complete 600-900s
V55 window after permitted startup. Check collection PID identity, candidate
commit and script/patch hashes before reading sealed prefixes. Run once with
nice19, idle I/O and CUDA hidden; preserve interrupted or incomplete output.
Missing historical-native phases remain unknown. This audit does not alter
scheduling and does not itself prove final acceptance.

V330 benchmarks exact frozen first-service methods on 36 same-input cases
with equal evidence. Matched list/tuple inputs reduce CPU by 32.1-66.4%;
early divergence can regress sharply, and mid-prefix divergence has conversion
cost. V332 observes 2165 pre-cutoff V54 targets: 2139 complete matches,
26 later divergence/short inputs, no divergence within 1024 tokens, prefix
P50 19465 tokens. Keep the qualified candidate unchanged. These fixtures
exclude writer, physical allocation, nested on_launch and GPU/JCT effects.

Candidate a843e83 converts list/tuple input only through the longest needed
ACK prefix and uses the existing exact C comparison at first service.
Nonintegral/overflowing input keeps the original numeric fallback.
Physical allocation, namespace and component-use evidence remain unchanged.
launch_telemetry is sampled inside run_batch; never add their costs.
The analyzer pairs clock domain, forward_iter, ordered request identity and
native work geometry, preserving missing/ambiguous/shutdown evidence.
194 checks plus two subchecks pass; V325 binds the revised source and the
unchanged f7e0a895/a8f4ea97 engine. The old probe microbenchmark excludes the
new nested span and prefix conversion; actual cost and gains remain pending.
Freeze the updated V55 target for deployment after 06:00 with a free GPU.
First verify real turnover records and their phase/GPU coverage, then compare
complete 600-900s windows and actual useful FULL consumption. Historical V29
has no new phase counterparts; full acceptance still needs same-version
repeated controls. Keep workload, budgets and disabled batching unchanged.

Historical server reservation, introduced2026-10-13 and revoked2026-10-14:
its00:00-06:00 block,23:59 cutoff and06:00 resume are no longer active.
The reservation cron is removed and the old morning resume disarmed.
Preserve raw traces and administrative censoring from its actual V54 cutoff.
Cold launches still require available resources and frozen source; they have
no fixed clock restriction. The goal stays active and workload, model budgets
and disabled batching stay fixed.

Latest V321 covers V54's post-replenishment 3600-3900s window:
350521/353757 decode steps (-0.91%), contexts 18109/18077 tokens,
extend work 2.215/2.271M tokens and 20.241s exclusive instrumented control.
Fine work/neighbor matching supports 108/118 short samples: raw completion
60.326/67.333ms (-7.008ms), yet clipped worker is 8.112/6.997ms.
No paired restore-wait probes exist here; GPU spans and dependency waits
remain unknown. V319's 3000-3300s window remains -3.78% service, 16.141s
control and +10.276ms raw completion in 101/97 broad neighbor-matched
samples; fine neighbor cohorts lack support. Preserve the changing direction
and do not turn interval boundaries or a fixed excess into GPU/JCT proof.

Development commit 1a9286e adds one-in-16 prefill turnover observations:
selection/admission, run_batch, WAR/queue publication, post-launch physical
planning and result processing have wall/own-thread CPU spans. Nonblocking
CUDA events enclose compute-stream work and dispatch/dependency gaps, not
exclusive kernels. Normal mode uses its actual current stream; overlap uses
the worker forward stream. Native and predictive share the instrumentation,
with no Req/tensor ownership, policy changes or new prefill delay strategies.
gpu_service_sample now includes forward_iter for exact sample pairing.
144 checks and two subchecks pass; same-dispatch CPU fixtures add 2.2-4.2us
per scheduler iteration, excluding actual CUDA-event cost. V322 binds patch
a8f4ea97 to engine tree f7e0a895. Real GPU correctness/perturbation and
same-version native counterparts remain pending. Keep V54 frozen at
45f7afb/6938bd10, then cold-test V55 during allowed hours after natural
postprocessing or a recorded administrative cutoff and process exit.
No workload or budget changes.

Last run: V54 cold-started at 21:33:35 CST on port 18459 from 45f7afb,
after all 156 V53 outcomes and HTML/workspace postprocessing completed.
V53 retains 155 completed and one incomplete over 9004.454s. Among the 155
common completed tasks, mean JCT is 2666.636/3024.858s (-11.84%); 88 favor
predictive and 67 favor native. All-terminal means of 2672.147/3039.851s
include the incomplete and are not successful-task JCT. Completion throughput
is 61.969/61.074 workflows/hour, with different output volume (7.781/9.450M
tokens), trajectories and engine versions. Historical diagnosis does not pass
quality-controlled or causal acceptance. The goal remains active.

The newer V317 window covers 1800-2100s: 365441/347017 decode request
steps (+5.31%), with similar visible context (23804/23808 tokens) but different
extend work (2.157/1.985M tokens). Exclusive instrumented control wall is
25.221s. Fine work/prefix/neighbor matching supports only 35/22 short samples:
raw completion 64.738/56.239ms (+8.499ms); broader neighbor matching over
905/815 samples is also +8.926ms. Clipped worker is 9.032/6.007ms.
Eighteen short probes show GPU/CPU/own-thread P50 of 36.965/34.546/34.545ms
and dependency wait 0.0868ms. The short-prefill objective remains unmet;
retain this unfavorable result alongside the earlier favorable window.

Development commit ea7cff5 chooses candidate-side FULL indexing only when
global device leaves exceed waiting candidate count by 8x. It preserves
native-consumed order, exclusions, identity, in-flight and reference checks.
299 checks and 12 same-input CPU cases pass with identical consumed prefixes.
Disparate geometries reduce CPU by 57.9-85.3%; ordinary geometries still add
1.5-6.2%. The rejected equality-based crossover added 61-115% in marginal
cases; keep its V315 reports and patch. The conservative 8x CPU selection
rule is not an action gate or proven optimum. Actual frequency and GPU/JCT
effects remain unmeasured; stage for future cold verification, without
interrupting V54 solely for this limited optimization.

V314 seals V54's complete 600-900s window: 369578/380722 decode request
steps (-2.93%) and 35.162s instrumented exclusive control wall. All 1009/1039
short single-request samples replay graphs. Fine 16-token/prefix/neighbor
matching supports only 105/110 samples: raw launch-to-processed-completion
is 57.845/55.452ms (+2.393ms), while clipped worker is 10.719/7.094ms.
Sparse exact-token cohorts stay unknown. Across 59 short sampled requests,
GPU/CPU/own-thread layer-span P50 is 34.598/34.219/34.217ms, while dependency
wait P50 is 0.0865ms. Native counterpart spans are absent; do not infer exact
kernel costs, writer interference or whole-run improvement.

V312/V314 verify qualified source, actual hint use, identical V53 capacities,
unchanged engine patch 6938bd10 and health 200. V54's native-prefix feedback
is unusable: admission reads initialized zero cached_tokens_device before
prepare_for_extend populates the native tier breakdown. Development fix
e4c1931 observes first service instead, validating instance, causal key,
computed breakdown and absence of retraction. Missing evidence remains unknown;
abort/close stays unserved. 414 checks pass. Keep this observation-only fix
staged for future cold deployment; do not hot-edit or restart V54 for it.

At independently sealed prefixes ending by 21:59:01, V54 confirms early
next-request FULL consumption for JOIN 5/0.0828GB and tool 62/6.967GB;
68 JOIN completion boundaries remain unknown. JOIN/tool ACK-to-first-service
P90 remains 22.290/51.223s. Continue reducing redundant candidate maintenance
and planning, and trace capacity/dispatch/consumption gaps. Match actual work
and retain incomplete outcomes; useful prefetch and complete mean JCT remain
part of acceptance. V54 keeps all workload and prefill settings frozen.

The following V53 window and candidate qualification notes are historical.
Both V308 service-estimate reuse and V310 locality hints are deployed in V54.

V310 qualifies session-residency hints for same-causal-group locality ordering.
Hints explicitly leave input token contents unverified, use a separate 50ms
cache, and never replace restore-anchor or complete-ready quota checks.
Unknown hints fall back to native complete reentry inspection; native prefix
matching, allocation and data dependencies remain authoritative. Add aggregate
native-prefix feedback is deferred to first service by the staged V313 fix,
without extra per-request I/O or gates.
409 checks pass. Across 36 matching-input CPU int64-index cases with Host,
mixed and device placement, complete planning CPU falls 4.7-36.5%; this does
not establish online hint accuracy, useful consumption, GPU or JCT gains.
V309 seals the latest 3600-3900s window: 332347/353757 service steps (-6.05%)
and 23.423s instrumented exclusive control wall. V311's 16-token/prefix/
neighbor matching covers only 94/92 samples: raw launch-to-completion means
66.841/64.401ms, while clipped worker means are 8.943/6.572ms. Exact-token
cohorts lack five samples; retain unknown support. Do not claim a complete
forward ratio or whole-run acceptance. V53 has finished; V54 cold-tests
the qualified candidate with frozen source. New batching,
delay and mixed chunk remain deferred. The observed Django-11885 incomplete
ends on finish_reason=length after repetitive text, with returned children and
an unexpired deadline; preserve it without a new guard or budget change.

V299 qualifies live, read-only empty-restore hints for operational JOIN/tool
and semantic-target checks. A negative hint skips detailed closure allocation;
positive or unknown evidence retains complete planning and native enqueue
validation. Detailed opportunity audit remains on the full path. 662 checks
pass; identical CPU fixtures reduce empty-path cost by 77-91% but add 6-38us
for positive targets. V53 cold-started at 18:55:48 from 02c171a. V303 verifies
83 unchanged plan fields, identical physical capacity, actual GPU service and
2327 shortcut calls; no sampled thread spans were yet observed. Cold V53 must
evaluate actual control cost, short-prefill
execution and useful prefetch, not infer GPU speedup from this microbenchmark.
Sampled layer probes add thread CPU spans to distinguish own CPU from wall time;
their difference does not identify GIL stalls. V298 records 461 GIL samples and
119 errors after the busy window; background writer interference remains
unquantified, so no writer suppression has been added.
V52 stopped at 18:51:35 with 81 completed and 75 arrived unfinished, all retained.
Its 156 workspaces were fully compared and archived from 38.773 to 4.147GB;
75 owned containers were removed. V304 seals pre-cutoff early-consumed FULL
at JOIN 43/1.279GB and tool 133/7.800GB, retaining 56 unknown JOIN boundaries
and excluding seven late tool completions and six late first services.
V305 completed the fully covered 600-900s audit at 19:13:51. V53 has
337028/380722 decode request steps (-11.48%) and 36.154s instrumented
exclusive control wall, versus V52's 41.287s. Tool-prefetch self wall is
6.389s versus 8.692s; these different live trajectories do not yield a causal
speedup. Matched short-prefill worker cohorts remain 1.55-1.95x native.
Among 55 short samples, GPU/CPU/own-thread CPU span P50 is
41.748/41.506/41.505ms; actual span has not materially improved.
The measured thread consumes most observed wall, but the native counterpart
was not measured, so neither GIL attribution nor the full excess GDN cost is
established. Keep V53 running while qualifying a substantive dispatch/turnover
fix; no throughput or full-JCT acceptance has passed.

V307 seals V53's complete 1800-2100s window: 357336/347017 decode
request steps (+2.97%), with different visible contexts (23212/23808 tokens)
and extend work (2.230/1.985M tokens). Exclusive instrumented control wall
is 29.833s. Short requests all replay graphs; raw launch-to-processed completion
is 60.276/56.797ms and matched short worker cohorts remain 1.17-1.51x.
Do not turn this favorable interval into a whole-run or causal success.
V308 is a development-only D2H service-statistics cache, keyed by bytes/shape.
Every completed H2D or D2H sample invalidates both estimates because either
direction can evict evidence from the shared rolling history. Unsupported
results are invalidated too; tool residuals, Host capacity and physical checks
stay live. 147 checks and nine identical-output CPU cases pass. Repeated
static cases reduce rank CPU by 44-51%; frequent receipts largely remove the
benefit and one no-hit case is 3.3% slower. Actual repetition and critical-path
savings are unmeasured. Keep V53 running; stage this modest reduction for a
later cold deployment, without enabling any new batching/delay strategy.

## Current Goal Revision

With fixed tasks, arrivals, model and physical capacities, reduce mean
workflow JCT and improve completion throughput relative to native by combining
low-cost control, workflow-critical admission, tool-wait-aware KV retention
and incremental offload, terminal-child reclamation, and useful anticipatory
restoration. Independently graded solution quality is required before calling
completion throughput successful-task throughput. Keep tail latency, ordinary
request aging, recomputation and resource costs in the acceptance record.
Decode token/s is diagnostic, not a prerequisite for a valid mean-JCT benefit.

The goal interface returned no active goal on October 13; the user explicitly
requested a next-stage goal, which has now been created and is active. Prioritize
explaining and removing the roughly 10-14ms matched decode-to-short-prefill
turnover excess. Separately measure GPU work, own-thread CPU dispatch, native
PrefillAdder admission/prefix inspection, causal events, migration planning and
result processing. Interim targets are a matched short-prefill turnover gap
within 10% and a busy service-volume gap within 5%. These are evaluation targets,
not runtime gates or proof of success. Final acceptance still requires quality-
controlled mean-JCT and completion-throughput improvements.

The user's subsequent instruction defers enabling prefilladder. Native
PrefillAdder is already the admission implementation; keep that existing path.
Defer new prefill batching/delay strategies: V306 confirms actual V53/V29
enable_prefill_delayer=False, min_free_slots_delay=None,
enable_mixed_chunk=False and prefill_decode_interval=0. Shared absence does not
explain the relative deficit. This latest instruction takes precedence over the
stored goal's future batching candidates; the status API cannot edit objective
text, so the active execution contract records the deferral here. Do not pause
the whole goal or falsely complete it to replace its wording.

Development runs predictive first against the fixed V29 reference; repeated
same-version comparisons and ablations belong to final validation. If shared
native code or prefill settings later change, obtain an appropriate common-
configuration native comparison instead of treating old V29 as an isolation
control. No new completion guards or canary stage. Keep the running source
frozen; qualify and commit important fixes before cold deployment.

Trace-analysis priority, updated on October 13: inspect the latest V54 live
prefixes and its complete busy windows first; V53 is the latest full trace.
Use explicit scope/cutoff and retain unknown boundaries. Historical sequence:
V49 completed, and V50 was stopped at
16:22:48 CST, retaining 133 completed and 23 unfinished workflows.
V51 cold-started from 0990b73 at 16:40:12 CST. V53's complete 600–900s window
is the latest busy-window evidence; V292's post-forward migration candidate
has passed qualification and cold-started in V52 at 17:43:50 CST from 3892fe1.
V51's 17:35:21 cutoff retains 63 completed, 45 arrived unfinished and 48
not-yet-arrived workflows. V295 verifies unchanged capacity and 82 plan fields,
real GPU service and the deployed route. Its dispatch counter includes idle
iterations and is not a forward count. V297 automatically waits for full
600–900s coverage, then generates busy-window and sampled-dependency reports;
interrupted or insufficient coverage cannot supply a completed-window result.
V297 completed at 18:08:18: V52 has 335062/380722 decode steps (-11.99%)
and 41.287s instrumented exclusive control wall; matched short-prefill worker
cohorts are 1.70–1.94x native. Among 60 short samples, worker P50 is10.947ms
while observed GPU/CPU layer-span P50 remains41.990/41.783ms. Moving the
planning boundary did not establish a throughput or complete-forward gain.
Continue reducing actual control work and locating eager GDN dispatch cost.
Both live-resolved backends match: FlashInferGDN prefill, TritonGDN decode
and breakable prefill graphs. V52 has been administratively stopped; keep V53
frozen while preparing further candidates.
Inspect the newest live run with explicitly bounded snapshots when diagnosing
an active failure, and label those as partial rather than final outcomes.
Use the fixed V29 native as a qualified comparison reference. Reopen older
predictive traces only for a specific historical comparison or reproduction;
they do not substitute for checking the latest implementation's real behavior.
Bind each analysis to the run, frozen revision, observation scope and input
hashes, preserving incomplete workflows and unknown evidence.

1. Resolve short-prefill overhead as the first performance issue. V49 has finished full collection;
   V50 cold-started at 14:20:50 CST on October 13 from 02fd87c, including
   both 82b42d5 Host-reclaim and 3b66033 final-admission fixes. This measures
   their combined effect, not an isolated Host fix.
   Check real satisfied-eviction scans, final-stage binding/selection counters,
   prefill turnover, complete JCT and throughput; CPU evidence alone does not
   complete this step.
   V287 rules out missing graph replay in V50's matched short prefills.
   Native prefix reuse, breakable graphs and eager GDN dispatch are shared.
   V51 has 344425/380722 decode steps (-9.53%) and 37.814s instrumented
   exclusive Python wall in 600–900s; short-prefill cohorts remain 1.98–2.23x.
   V290's 54 short samples have 0.0863ms median GPU dependency wait and
   42.086/41.927ms median observed GPU/CPU layer spans. Neither spans nor
   clipped worker intervals isolate full forward or kernels.
   V292 moves waiting JOIN/tool migration planning and PREPARE after native
   forward submission and its WAR barrier on the qualified single-rank overlap
   path with demand handoff disabled. Other paths keep the previous order.
   83 engine and 385 runtime checks pass; cold V52 must measure actual
   launch/service and early-consumed FULL. Changed worker clipping or reduced
   planning cost alone does not establish throughput/JCT improvement.
   V51's bounded 192-step profiler ran at 17:03:20–17:03:30 after this window;
   profiler and export overhead remain diagnostic perturbations, not gain evidence.
   V293 confirms the capacity/consumption candidates were live: 255/348 Host
   early exits and 110 complete-checkpoint quota admissions. V296 seals
   pre-cutoff early-consumed FULL at JOIN28/1.040GB and tool150/12.826GB,
   retaining 26 unknown JOIN boundaries and excluding eight late tool
   completions and five late first services. The 108 workspaces are compared
   and archived (20.934 to 2.651GB); raw traces and administrative censoring remain.
2. Prioritize workflow critical-path queueing next. Existing causal ranks
   already recognize the sole remaining JOIN child; resident-first, final
   priority and restored-parent consumption also exist. Current ordering is
   primarily class/depth and bounded promotions. V271 now uses live semantic
   remaining work or a positive report budget to select among explicitly bound
   last-child reports; it also repairs a fresh notice and next-submit arriving
   in one batch. This is not general output-length scheduling: the tool-timing-only
   config still bypasses legacy local-demand predictions. V274 confirms actual
   deployment; measure coverage, then integrate restore cost and investigate
   non-final critical-child queueing. Preserve native batch filling, request
   aging and bounded quotas.
   V275 now uses the full latest V49 trace: paired last-child queue improves
   76.518s/workflow but after-first-service time grows 98.051s and JOIN wait
   grows 21.708s. Investigate generated work and interleaved service/turnover
   together; the latter interval is not isolated scheduler CPU. V276 confirms
   V50 still has 316670/380722 decode steps and 39.753s instrumented exclusive
   Python wall in 600–900s. Preserve its cutoff and cold-deploy V51 after the
   qualified capacity and restored-consumption fixes; never hot-edit live code.
3. Coordinate each waiting agent's memory lifecycle. Rank short-wait HBM
   retention versus incremental FULL backup and reclaim using remaining tool
   time, complete round-trip cost and actual pressure. Supply freed capacity
   to an imminently executable request, then retain restored pages through
   consumption. Existing terminal-child reclaim and survival-conditioned tool
   prediction remain foundations, not missing features. Never PREPARE mutable
   Mamba; restore only the necessary valid checkpoint state.
4. Improve anticipatory coverage at real opportunities. At the same wait-event
   identity, follow safe checkpoint -> missing pages -> actual capacity ->
   submission -> completion boundary -> next-request reuse. Test timing,
   capacity and admission coordination together without claiming a positive
   plan proves a feasible transfer. Partial overlap can help even when full
   ACK cannot precede RETURN, but partial pages never authorize execution.
5. Attribute stable gains after candidate improvement. Report all submitted
   workflows, mean/P50/P95 JCT, completion curves, failed/unfinished outcomes,
   generated work and resource costs. Compare native with shared optimizations,
   workflow scheduling/reclaim, added PREPARE and added JOIN/tool anticipation
   as controlled factors in final repeated live experiments. Do not subtract
   transfer-stream time from JCT or credit shared engine fixes only to prediction.

Commit 6597555 fixes native allocation groups hiding unreserved static free
lists and complete restored checkpoints always waiting for four ordinary
admissions behind an aged head. Pending FULL frees and reserved Mamba slots
remain excluded. Only a fresh complete checkpoint and valid next-prefill
frontier use the dynamic 1–3-admission quota; at least one ordinary turn
remains. Partial/unknown dependencies and unavailable capacity keep the old
quota. Same-call headroom reuse does not span mutations. 456 checks, four
production allocator CPU cases and seven prior/current queue cases pass.
V277/V279 bind behavior evidence, not GPU or JCT improvement.

V50 now confirms 17690 satisfied-capacity early returns in 27418 FULL Host
eviction calls. Short prefill still has roughly 2.29–2.61 times native worker
intervals, so diagnosis must continue. Existing low-frequency layer-wait
sampling adds CPU enqueue/span and GPU observed-layer span without more CUDA
events or synchronization; spans include dispatch gaps, not isolated kernels.
110 relevant checks pass. Cold V51 keeps the workload, limits and handoff0.
V50 unfinished tasks retain administrative censoring after the development
restart; stopped-run completed-only JCT cannot establish improvement.

V284 pairs 99 actual dependency samples in the sealed V50 busy window.
Among 50 short single-request prefills, GPU wait P50/max is 0.0889/0.3795ms
versus 15.2809ms worker P50. Unknown old layer-span fields stay unknown;
this does not establish isolated kernel or scheduler attribution. V285
qualifies early consumed FULL before cutoff: JOIN66/1.454GB,
tool241/14.897GB; eight JOIN completions remain unknown, two shutdown tool
completions and two late first services are excluded. V281 preserves the
administrative outcomes and verified 156-workspace archive (38.962 to
4.204GB), removing only the run's 23 stopped containers. Raw traces remain.

V271 is deployed in the frozen V50 code; performance remains unproven.
Notice recovery is limited to the exact fresh same-batch notice/request and
the currently valid sole unfinished JOIN child. Later tool/RETURN events do
not create a live final stage. Candidate work uses a matching recent forecast
minus observed output; exhausted centers use a supported upper endpoint,
then a positive notice budget, otherwise unknown. Best-at-head and unknown
ties preserve current queue order. Restore-consumption priority and the
existing four-normal-admission quotas/aging remain. No new physical planning
or scheduler-side model inference is added.

186 checks pass and one skips. The six-case baseline reproduction uses real
runtime/graph/queue fixtures and keeps physical action count zero; original
030b7df reproduces the binding and ordering defects. The tracked
`scripts/qualify_final_report_admission.py` binds source/test/JUnit hashes in
`experiments/reports/v271_final_admission_qualification_20261013.json`.
Cumulative candidate/binding counters establish production frequency in V50;
they are not unique events or admitted-request counts.
V49 completed with unchanged execution source. Per the latest user instruction,
V50's cold deployment includes this candidate. Broader critical-child queueing
and coordinated tool retention/offload remain unfinished.

V270 reconstructs both complete V29 traces. Mean JCT improves 391.198s:
root JOIN wait 268.933s, root LLM 108.731s, root tools 18.935s, offset by
5.401s more other/out-of-trace time. Last-returning child queue-to-first-worker
service improves 194.434s and root queueing 22.403s: 55.43% of the observed
mean difference is in these intervals, not an isolated causal policy effect.
Critical-child requests/output are 24184/3.887M -> 24270/3.945M; root output
falls 3.383M -> 2.845M. Both waves improve, but top-ten improvements account
for 71.19% of the net paired difference and all ten generate less output.
All tasks remain included. The script verifies interval reconciliation,
request/tool pairing, all output counts and exact input hashes. See
`experiments/reports/v270_v29_jct_sources_20261013.json` and its full-task CSV.

Commit 82b42d5 is qualified by 113 checks against the exact reconstructed
engine; the original implementation reproduces all three no-scan regressions.
The original V50 queue registered at 12:58:57 CST on October 13,
coordinator PID1250265, was safely stopped at 13:58:20 while V49 remained
live and V50 had not started. Its state/log are preserved under
`v50_launch_after_v49_20261013.superseded_host_only.*`.
The replacement target checkout is `beliefkv-v271-v50-latest-20261013`.
After complete V49 outcomes, HTML/workspace postprocessing and process exit,
V50 cold-started from 02fd87c at 14:20:50 CST, running predictive only.
108+48 arrivals, Host80:20, tool180s, handoff0 and fixed V29 native remain.
V50 has no complete performance result yet. Main's
`experiments/reports/v50_launch_after_v49_20261013.json` is the replacement
live receipt; `v50_latest_code_retarget_20261013.json` preserves the change.

V274 independently validates deployment at 14:44:10: unchanged 82 plan
fields, 27 configuration fields and physical capacities; qualified runtime
and canonical patch/tree match Main and the frozen checkout. HTTP200 and
494 real GPU samples accompany actual remaining-report-work admission,
handoff=false and enabled JOIN/tool, PREPARE, residency and priority.
Recorded semantic/native drops and writer failures are zero. FULL Host
eviction calls are still zero, so its no-scan effect is not measured yet.
The recent 30 runtime snapshots contain 10 actual next-prefill budgets
and 20 capacity-unavailable fallbacks. The latest fallback and the separately
timestamped actual 4145-token/one-Mamba reserve are both preserved;
neither establishes continuous capacity visibility or action feasibility.
Investigate allocator intermediate states without relaxing native dependencies.

V49 collected all 156: 155 completed, one incomplete, duration 9662.691s.
Completion throughput is 57.748 versus fixed native 61.074 workflow/h.
The 155 paired completions average 2962.757 versus 3039.510s; 76 improve,
79 regress, and P95 worsens to 8097.639 versus 7342.445s. The incomplete
is explicit; this completed-only mean cannot establish goal success.
Early next-request-consumed FULL is JOIN 67/1.237GB and tool 182/12.073GB,
with no demand handoff. Native H2D is 3.099TB; explicitly Host-eviction-linked
recompute is 62791 versus 15879 tokens. Do not claim a recomputation win.

V272 reconciles all 94892 V29 requests, output totals and original V270 hashes.
Critical queue seconds per workflow split into already-sole-child 413.311
to 211.632 and still-parallel 493.145 to 500.390. Predictive sole-child requests
without a valid report-stage notice contribute 208.456s versus just 3.177s from 162 announced
reports. About 84.07% of critical queue time comes from requests waiting
at least ten seconds. Broader tool-return/sole-child admission is therefore
a more substantial candidate than report-only tuning. Client roles do not
prove control delivery/rank or a causal aging effect; summed concurrent
request waits cannot be subtracted from JCT.

V273 repairs native/trace-only completion-stage trace loss. Original 02fd87c
loses all three nonqueued modes; 58 checks pass after the fix and queued
predictive behavior matches. Commit fe7b54b is followup-only, not hot-deployed
in V50. Native V29's 301 successful announcement calls have zero stage records,
so its old report phases remain unknown rather than "unannounced". Use the
repair for later same-version collection without adding control authorization,
prompt changes, model calls or high-frequency semantic telemetry.

Current bottleneck work (V265-V268): disabling demand handoff reduced
instrumented exclusive Python wall from 55.537 to 39.679s in the complete
600-900s window, but V49 still produced 318714/380722 decode request steps.
Matched full-48 consecutive decode is close to native; short single-request
prefill remains about 2.25-2.43x. Investigate batch turnover and cache
maintenance outside the existing runtime scopes; do not equate worker
intervals with kernels or removed CPU time with JCT improvement.

A later 24s GIL profile retains 2355 samples and all 520 errors. FULL Host
eviction appears in 381 samples. Write-back duplicate reclaim can satisfy
the request before FullComponent still builds a heap over every Host leaf.
The fix skips that heap only when the existing tracker already meets the
target; otherwise preserve native ordering, locks and frees. Four cumulative
counters enter the existing once-per-second Host record, not per-call logs.
The old trace cannot identify the already-satisfied fraction of those samples.
CPU cases with 4096/32768 synthetic leaves reduce that branch's P50 from
2.508/19.647ms to 0.0037/0.0057ms, with GC disabled only during timed calls.
Necessary eviction outputs match; this is not GPU/JCT evidence.

V49 finished with frozen execution code and the qualified fix is deployed
in V50. The old followup engine lagged the canonical patch; preserve
it and qualify against a new engine rebuilt from the complete current patch.
This shared cache optimization can help native too: fixed V29 remains a
development reference, while final attribution needs same-version evaluation.
Continue evaluating early FULL consumption, service waiting, complete JCT,
tail/fairness and recomputation. The performance goal remains unmet.

Retain V29 as a positive complete JCT observation. Both same-version arms
completed all 156 workflows: mean JCT 3039.851 -> 2648.654s (-12.869%),
P50 2531.277 -> 2191.880s (-13.408%), P95 7334.121 -> 6490.692s
(-11.500%); 87 paired tasks improved and 69 regressed. Paired-delta median
is -79.823s, distinct from the difference between arm medians. Full-run
completed-request output token/s fell 3.836%, with 5.970% less generated
work. V264 rechecks all tasks and source hashes. Do not conflate lower
decode throughput with absence of JCT benefit. Verify repeatable complete
JCT, task quality, tail/fairness and resource cost; separate anticipation
from admission/recovery contributions. Stopped completed-only cohorts
cannot establish complete JCT improvement.

V49 cold startup began at 11:31:21 CST from 69beb35, testing native demand
recovery as one factor:
`EXECUTION_HANDOFF=0` delegates submitted requests to native prefill's complete
FULL/Mamba dependencies, while JOIN/tool anticipation, PREPARE, residency
protection and admission priority remain enabled. V260 initializes the real
plan and checks resume propagation; 80 previous plan fields remain unchanged
outside the recovery policy and source revision. Runtime/engine sources match.
Freeze the explicit flag and verify the actual runtime before measuring benefit.
V48 maintenance is complete. Assess busy throughput, control cost, native H2D/recompute,
early confirmed FULL consumption and restore-to-service waiting. Goal remains
unmet; disabling demand handoff is a diagnostic policy change, not a proven gain.
V262 independently validates deployment at 11:36:56: HTTP200, 591 real
GPU service samples, actual handoff=false, retained prediction/PREPARE/
residency-priority flags, unchanged 80 plan fields, 27 configuration fields
and capacities. Recorded semantic/native telemetry drops and writer failures
are zero. The sampled next batch reserves 4145 FULL tokens and one Mamba
slot; startup does not establish throughput or JCT gain.

V48 stopped at 11:18:44.461620: 84 completed, 72 created unfinished, all
156 arrivals present. Keep the unfinished administratively censored.
V261/V263 qualify early next-request FULL reuse as 41 JOINs/1.060GB
and 164 tools/9.994GB; exclude five post-cutoff tool completions and seven
first services from terminal training, with 34 JOIN boundaries unknown.
All 156 workspaces passed full tar comparison and another comparison
after the audit exited: 38.691GB source became 4.418GB archive. Only
72 owned stopped containers were removed; raw/results/plan remain.

V258 checks V257's exact 399660508-byte audit prefix: 101 unique probe/batch
pairs, with two ambiguous overlap candidates retained as unknown. Among 61
short single-request prefills, CUDA layer wait P50/P90/max is
0.0838/0.1019/0.4421ms; worker interval P50 is 17.301ms. Preserve native
dependencies: these samples do not explain the main short-prefill gap or
establish whole-run fence overhead. V259 retains a later nonblocking GIL
profile with 1783 samples and all 447 errors; ranking/reentry remains visible.
Sampling is not CPU accounting, kernel attribution or removable JCT.

Previous development run V48 cold-started at 10:14:57 CST on October 13
from b8616d1. Canonical patch 3698ce2f and actual engine tree 6d6af094
remain unchanged. Retain 108+48 arrivals, running 48, Host 200GB/80:20,
HBM Mamba/FULL 0.9, tool 180s, output 8192 and maximum lead 2000ms.
Development runs predictive only against fixed V29 native. V253 independently
validates deployment at 10:20:19: HTTP 200, 613 real GPU-service samples,
enabled predictors/physical actions, matching source/engine, 27 frozen
fields and pool capacities. The sampled next batch reserves 4144 FULL
tokens and three Mamba slots. Startup does not establish performance benefit.

V257 seals V48's complete 600-900s window: 323764/380722 decode request
steps, 14.961% below fixed native and essentially unchanged from V47's
323881. Exclusive instrumented Python wall remains 55.537s/300.767s:
tool/reentry/handoff/admission 8.377/5.696/5.287/5.437s. Coarse matched
full-48 consecutive decode is close to native; short single-request
prefill remains about 2.89-3.70x. These intervals include controls/overlap,
not isolated kernels or removable JCT. Next consolidate redundant tool
scans, reentry/physical planning and turnover work while retaining causal
checks; separately verify early reuse and demand restoration. Goal unmet.

V256 independently seals prefixes at 10:34:26-10:34:39: early confirmed
next-request FULL reuse is six JOINs/43.540MB and 45 tools/3.643GB.
Demand handoff's 43.894GB stays separate. Pre-completion positive-FULL/
start/submit is 25/25/11 and 430/50/49. Twelve JOIN events record unavailable
service progress; reasons overlap and do not prove endpoint exhaustion.
The final observed state records three reclaimed-plan reuses, five same-tool
overruns, 390 residual extensions and 233 unsupported-tail checks, not
independent tasks or proven avoided transfers. Keep 82 pending JOIN
boundaries unknown; do not add overlapping prefixes or infer throughput gain.

Commit e7b0d7a reuses the complete selected plan only after successful
cold reclaim and fresh pool/reserve/protection-budget checks. Final
enqueue still drains causal events and recaptures the whole target.
b8616d1 restores the original fallback before missing-pool indexing.
V255 binds 472 checks; V254's 48 CPU cases match actionable outputs.
Successful-branch thread CPU falls 84.1-95.5%; fallback adds at most
9.09us. Partial/probe/insufficient paths refresh. No GPU gain is proven.

Commit 0902199 retains survival evidence for the exact same tool invocation.
After a speculative restore expires while that tool is still running,
combine the latest forecast with the first forecast conditioned on survival.
Refresh cannot erase this observed overrun; an unsupported old tail remains
unknown rather than authorizing repeated near-term speculation. Latest
identity and native checks still authorize actions. Clear on real invocation,
revision, session or context changes; keep existing residency expiration.
V248/V249 reconstruct one V46 invocation with eight bursts/944.251MB and
expired protection without reuse proof. Seven later bursts fall outside
the old survival-conditioned two-second lead. This is mechanism evidence,
not avoided traffic, population accuracy or measured GPU improvement.

V47 stopped at 10:00:46.068105: 80 completed and 76 created unfinished,
all 156 arrivals present. Keep the latter administratively censored.
V251/V252 qualify pre-cutoff early next-request FULL reuse as 34 JOINs/
468.521MB and 158 tools/10.213GB; 145.072GB demand handoff remains separate.
Known pre-cutoff completions are 107/13287. Exclude three post-cutoff
tool completions and four first services from terminal training.
Sampled fitting positive-FULL events are 99/271, with 65/113 lacking
early confirmed consumption; snapshots do not prove trigger capacity.
Event last-ACK-to-service P90 remains 11.087s/17.620s for JOIN/tool.
Do not combine denominators into a queue-delay claim. All 156 workspaces
passed full tar comparison before cleanup at 10:07:05: 38.760GB source
became 4.346GB archive. Only 76 owned stopped containers were removed.
Raw traces, outcomes, manifest and frozen plan remain available.

V243 seals V47's complete600-900s window:323881/380722 decode request
steps,3.75% above V46 but still14.93% below fixed native. Exclusive Python
wall is55.197s; turnover46/47 and short-prefill gaps remain. V244 retains
897 nonblocking GIL stacks and241 sampling errors; closure has51 leaf
samples,remaining_quantile only one. Prioritize repeated physical planning
and control work rather than unsupported probability-math optimization.
V246's independent earlier prefixes are superseded by V251/V252; do not
add overlapping reports. Final dispatch positive-FULL/start/submit is
93/92/52 and 1967/186/180; the tool funnel contains three post-cutoff
boundaries, so training must use V252 qualification. V242 derives V46's gap evidence without raw
rereads:42of48sampled-fitting JOIN gaps have no submission;26tool events
transfer6.007GB early without next-request reuse proof. Inspect trigger
capacity and repeated restores after pre-completion protection expiration.

Retain V47's slot-bounded probing, measured PREPARE budget and V239
deployment evidence. V48 must validate actual source, capacity and service
separately. Next inspect real tool-overrun/retry counters, JOIN submission
gaps and first-service consumption, then the complete 600-900s busy window.
The throughput/JCT goal remains active and unmet.

V46 stopped at 08:47:12.309023: 69 completed, 1 incomplete, 38 created unfinished
and 48 unarrived; the latter 86 remain administratively censored. The existing
django-11206 incomplete has two final root length terminations, no guard
completion or system error; preserve the output cap and V240 evidence.
At 08:50:28, 108 workspaces passed full tar comparison before 20.961 GB source
became a 2.234 GB archive; only 38 owned stopped containers were removed.
V238/V241 pre-cutoff early confirmed FULL reuse is 42 JOINs/1.951 GB and
150 tools/11.536 GB; 130.613 GB demand handoff remains separate.
Positive-FULL/start/submit is 87/83/47 and 1342/181/175. Exclude six
post-cutoff tool completions and four first services from training.
The plan-to-submit gap and below-native busy performance remain active work.
V231 seals V46's complete600-900s window:312178/380722 decode request
steps,18.004% below fixed native. Exclusive instrumented Python wall
is55.041s:tool/admission/handoff/reentry6.689/5.699/5.517/5.516s.
Coarse full48 decode is close to native; turnover46/47 and short prefill
remain slower. These intervals include controls/overlap, not kernel time
or removable JCT. Reuse V199 native summary. The goal remains unmet.

V232 independently seals file prefixes at08:14:56-08:15:13 CST.
Pre-completion positive-FULL/start/submit isJOIN37/36/11,tool762/81/77.
True early next-request FULL reuse is8JOINs/181.924MB and73tools/6.623GB;
65.572GB confirmed demand handoff stays separate. JOIN arrival-to-service
P90 is457.583ms, while12ACK events' last-ACK-to-service P90 is8.584s.
Do not combine denominators or overlapping prefixes.623PREPARE ACK
commands have113later-restore associations; this does not prove net benefit.
Sampled fitting positive FULL is526.684MB/9.052GB forJOIN/tool, not
trigger-time capacity. Continue reducing the plan-to-submit gap and costs.

Commit db68f89, qualified byV233 with344 checks, is cold-deployed inV47.
With measured D2H/H2D queue and ACK P90 evidence, PREPARE uses their
round trip plus100ms observation cadence instead of compulsory2s restore
lead. Missing evidence retains fallback; slow restoration may need more
time. Scope is only the selected first extent, not the entire checkpoint.
V46 did not deploy it. Complete native execution dependencies and no Mamba
PREPARE stay unchanged. No runtime benefit is established for this change.

V45 stopped administratively at 07:43:41.038655 CST: 87 completed and
69 created unfinished, all 156 arrivals present. Keep the unfinished
administratively censored. All 156 workspaces passed full tar comparison
before cleanup at 07:49:31; 38.815GB source became a 4.057GB archive.
Only 69 owned stopped containers were removed; raw/results/plan remain.
V227/V229 prove pre-cutoff early next-request FULL consumption for
48 JOINs/1.342GB and 139 tools/8.774GB. Demand handoff's 134.226GB
confirmed FULL stays separate. Known pre-cutoff completions are 126/14846;
exclude two post-cutoff tool completions and five first services from
terminal training labels. Early confirmed bytes are unchanged.
Sampled fitting positive FULL events are 114/210, with 66/71 lacking early
confirmed consumption; snapshots do not establish trigger-time capacity.
JOIN arrival-to-service P90 328.469ms and 62 ACK events' P90 8.738s
have different denominators. All 336 later same-node/pool D2H records
have preceding Host eviction; do not infer redundant intact-copy backup.

V224 refines actual single-request prefill geometry. For work below512
tokens, 970/1039 batches average218.72/227.09 tokens; scheduler/worker
intervals are21.403/7.059ms and run_batch-to-result70.494/53.222ms.
Matched16-token work bins,2048-token prefix bins,CUDA flags and neighboring
phases retain a2.26-3.53x short-prefill gap;4096-token cohorts are0.971-1.009x.
Retain per-batch rows for reuse. Equal backend settings do not isolate
kernel cost; continue attributing controls, restore dependencies and turnover.

Commit7bc7fbe allows cold WAIT_JOIN FULL backup during the report stage
unless this parent's actual restore ticket is live. Noncontinuing joins,
reentry,session changes and native mutation checks remain restrictive;
Mamba PREPARE remains disabled and partial restoration never grants execution.
All467 related checks pass. V46 cold-deploys with c8fa8c7's two qualified
changes; V225/V228 bind qualified and running source hashes.
Measure real reclaim,early consumption and busy throughput before
claiming improvement. V45 was not hot-edited. V226 retains769 GIL
stacks and196 errors across second-wave arrival; shares are not wall costs.

V219 seals V45 600-900s: 337096/380722 decode request steps,
11.459% below fixed native; 7.962% above V44 is observed, not causal.
Visible context is 22.722k/23.240k; worker decode interval
34.812/32.467ms. Coarse matched single-request prefill remains about
2.65-3.32x native. Instrumented exclusive Python wall is 54.565s:
tool/reentry/admission 9.073/5.664/5.048s; handoff self/inclusive
5.241/16.193s. Intervals include controls/overlap, not isolated kernels
or removable JCT. Reuse V199 native summary; retain local analysis
overlap and do not reread native raw for repeated development comparisons.

V222 independent sealed prefix: known JOIN/tool boundaries 33/3924,
unknown 76/3. Dispatch pre-completion positive-FULL/start/submit is
28/27/9 and 611/54/51. Proven early next-request FULL consumption is
five JOIN events/54.354MB and 47 tool events/3.572GB. Demand handoff
confirmed FULL 46.501GB stays separate; all node commands/ACK reuse
are not early event coverage. Seven JOIN and eleven tool commands
lose FULL protection before completion; later queueing cannot explain
earlier expiration. JOIN arrival-to-service P90 327.615ms uses a
different denominator from nine ACK events' last-ACK-to-service
P90 4.021s. Never add overlapping prefixes or different reason groups.

V220 capacity prefix: 1385 tool and 24 JOIN events record zero
post-reserve FULL budget. Of nineteen in-flight tail-reclaim checks,
only one releases 1806 FULL tokens. Preserve partial-overlap support
without granting execution readiness; splitting cannot create capacity.
Inspect settled waiting copies separately from unbacked/shared/locked
native pages, and qualify actual reclaim-to-submit changes.

Qualified changes now cold-deployed in V46: static closure goes directly through the
existing static geometry validator, with fresh residency/ownership
checks every call. PREPARE registers settled safe-input copies even
while other extents need backup, reusing the fresh action-local closure.
Publication/native reclaim still validate identity, refs, transfers and
protected restores. All 702 related checks pass. V221's 30 CPU cases
match output; four-level static observations reduce thread CPU about
12%. V223's nine probes preserve PREPARE selection and native reads;
partial-copy registration adds about 4.2-23.5us at depths16/64.
Retain this cost and do not claim real reclaim frequency or GPU gains.
Commit evidence promptly and keep Main execution unchanged while V46 runs.

V44 stopped administratively at 06:24:32: 34 completed, 74 created
unfinished and 48 unarrived; the latter 122 remain censored. All 108
workspaces passed full tar comparison before cleanup at 06:29:09,
20.932GB allocated source to 2.249GB archive. Only 74 owned stopped
containers were removed; preserve raw/results/plan. V216 seals known
pre-completion positive-FULL/start/submit: JOIN 63/62/18 and tool
1652/203/194, with 47/3 unknown boundaries. Proven early next-request
FULL consumption: ten JOINs/120.177MB and 182 tools/10.756GB.
Demand handoff confirmed FULL 141.648GB remains separate.
JOIN ACK-to-service P90 13.700s is not arrival-to-service P90 494.102ms;
do not combine denominators, add overlapping prefixes or train from
administrative stop outcomes. The performance goal stays active.

V44 cold-started at 05:44:21 CST on October 13
from f55e136/3698ce2f, engine tree 6d6af094. It deploys committed exact
H2D-statistics reuse and live Tensor closure lengths. V207 verifies HTTP200,
1851 real GPU-service samples, enabled predictors/physical actions,
matching sources/engine, all 27 frozen fields and pool capacities.
Observed reserve is 4144 FULL tokens and 45 Mamba slots following the
imminent batch. Keep running sources frozen and fixed V29 native for
development. The complete busy window below remains below native.
V209 qualifies historical-checkout verification for development restarts,
with 68 checks. The initial launch failed before workload creation;
preserve its log and do not count it as a workflow failure.

V212 seals V44 600-900s: 312235/380722 decode request steps, about
18% below fixed native, with visible context 22.716k/23.240k and worker
decode intervals 37.655/32.467ms. Exclusive instrumented Python wall
is 58.511s: tool/reentry/admission 7.984/6.057/5.647s; handoff
self/inclusive 6.030/18.657s. Full48 coarse matched decode is close
to native; request turnover/prefill intervals remain slower. These
include controls/overlap, not isolated kernels or removable JCT.
Reuse V199 native geometry; analysis after 06:02:44 overlaps later
collection and is not part of the primary completed window.

V211 independent sealed prefix has 23/3846 known JOIN/tool boundaries
and 88/2 unknown. Known pre-completion positive-FULL/start/submit:
20/19/5 and 517/55/52. Proven early next-request FULL consumption
is zero JOINs and 49 tools/2.461GB. Demand handoff confirmed FULL
reuse 41.942GB stays separate. Six JOIN ACK/service events have
ACK-to-service P50/P90 7.677/12.565s, versus native arrival-to-service
212/425ms across 23 known JOIN events. Do not explain pre-completion
expiration with later admission or merge different denominators.
Inspect repeated lease refresh within an iteration; no new change is
deployed. Preserve deadline/identity/residency checks at mutations and
native execution dependencies; no cross-iteration physical cache.

V214/V215 qualify moving the tool path's additional global lease refresh
after eligible-candidate discovery. Deferred scans, in-flight transfers
and empty candidate lists avoid the repeated walk. Scheduler/event/batch/
abort maintenance remains; eligible planning refreshes live leases before
budgeting and stops on native lock-release failure. All 406 focused checks
and 12 output-equal CPU fixtures pass. At 156 contexts with 4-48 leases,
maintenance-plus-scan thread CPU falls 1.6-34.3%; no-lease cases are
essentially unchanged, with a retained 0.35% slowdown. Absolute costs
remain in V214. No throughput extrapolation; V44 was not hot-edited.
V45 cold-deploys this committed change; measure live controls and consumption.
V213 preserves 779 nonblocking GIL stacks and 214 errors. Closure/reentry/
lease-validity leaf samples are 56/26/10; these identify further inspection
work, not wall costs, GPU stalls or removable JCT.

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
