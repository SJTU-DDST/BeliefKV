# V177 Completed Pair and Cold-Capacity Direction

Date: 2026-10-24, Asia/Shanghai.

## Completed Comparison

V177 completed at 22:37:23. All 24 workflows and 2369 requests completed
with the same 151307687 input tokens and 531903 output tokens as V174.
Exact request work, dependencies, stopping conditions and capacities passed
the existing checks. No workflow was excluded; all normal tails remain.
Task patches have not been independently graded.

| Metric | V174 native | V176 Full | V177 Full |
| --- | ---: | ---: | ---: |
| Mean workflow JCT | 2102.911s | 2037.600s | 2046.113s |
| Event makespan | 2892.903s | 2785.140s | 2803.206s |
| Whole-run output throughput | 183.865 token/s | 190.979 token/s | 189.748 token/s |
| Actual logical input prefill | 4255360 | 4223186 | 4363160 |
| Previously served common-input reprocessing | 696174 | 639935 | 779918 |

V177 mean JCT is 2.701% lower and throughput 3.200% higher than native.
Twenty workflows finish faster and four slower. Relative to V176, mean JCT
is 0.418% higher and throughput 0.644% lower. These are individual executions,
not a significance test or causal attribution of the small change to the
anchor correctness repair. Preserve that repair and the previous evidence.

V177 actual input prefill increases 2.533% relative to native. Previously
served common input reprocessed by actual prefill increases 12.029%, across
nine requests in each arm. This lower-bound repeated-work measure excludes
previously generated-output prefixes. Prefill worker intervals increase
1.449%; they are not pure CUDA kernel time or directly removable JCT.

The current result does not justify a broad ablation matrix or external
baseline campaign. Custom KV-management benefit beyond a strong scheduling
control remains unproven.

The follow-up ingress audit finds that natural native uses chat conversion,
whereas Full replay sends pretokenized IDs to `/generate`. API-received to
tokenized-request P50 is 47.949ms versus 2.201ms. The cumulative interval
difference is 110.491s across requests, not a subtractable JCT correction.
Exact work remains matched, but the 2.701% JCT and 3.200% throughput point
estimates are not yet an entry-aligned end-to-end KV-management gain.
The HMAC eviction audit also links all 779918 V177 reprocessed old-input
tokens to Host evictions during observed parent JOIN waits. Duplicate-first
Host eviction is already shared with native; explicit child cleanup does not
guarantee retention of every live parent. See
`docs/experiments/v177_host_losses_and_replay_comparability_20261024.md`.

## Capacity and Restore Evidence

V177 has 360 controlled JOIN restore commands, 89.898GB transferred and
68.096GB with verified FULL reuse. Every command has confirmed-JOIN scope.
Thirty-two observed JOIN waits have zero positive FULL plans before JOIN
satisfaction. Tool prefetch submits no H2D command.

Twenty-three distinct recorded JOIN waits submit a partial observed FULL
burst. Twenty-one extent splits cover 32779 tokens. Thus partial restoration
is implemented and exercised; it does not bypass an allocator budget of zero.
Twenty-three post-enqueue tail-capacity checks release no FULL pages.
Critical capacity diagnostics report FULL shortfall in 51 of 52 checks;
50 checks observe unbacked cold-candidate rejection. Counts overlap and are
not unique pages or a census of every HBM state.

Host-use median is 99.819%. Background PREPARE encounters 4519
Host-capacity skips. Native idle/spawn/critical PREPARE acknowledges
9.421/8.230/0.110GB respectively. These figures do not prove that every
unbacked page could have been backed earlier.

V177 samples one prefill batch containing a JOIN parent: batch dependency
wait is 321.745ms, of which 314.543ms follows the first layer. The batch also
contains a tool successor, so this wait is not attributable exclusively to
the parent. It demonstrates remaining dependency waits in a JOIN-containing
batch, not a population estimate. EOS-to-parent-submit P50 is 72.208ms and
P90 113.463ms; EOS-to-first-prefill P50 is 3.017s and includes queueing.

## The 18.63GB Refusal

V176's warehouse-root refusal has 189470 Host-backed missing FULL tokens,
18.626GB at 98304 bytes/token. Free FULL capacity is initially 348 tokens
and then 672; native next-prefill reserve is 4120. The partial-prefetch
budget is therefore zero at both observations.

The recorded bounded scans reject unbacked and session-referenced leaves.
An urgent 344-token backup subsequently frees only 33.817MB. Reconstructing
client events at those timestamps finds 17 WAIT_JOIN invocations, 24 in the
LLM-submitted state and no active tool waits. LLM-submitted includes queued
requests and must not be called the actual running GPU population.

These observations explain why an immediately available tool-wait donor
cannot be assumed at that instant. They do not establish a whole-HBM
impossibility result or reproduce the scheduler's precise event-delivery
state. Safety still requires current owners, locks and copy readiness.

Layerwise H2D reduces dependency lead requirements, while the current FULL
allocator reserves addresses for all layers of each selected token slot.
Copying fewer layers does not proportionally reduce those slots. A shorter
continuous token prefix can reduce actual allocation.

## Population Sizing

Use prefixes of the existing predeclared seed-21 distinct-project order.
No task is selected by JCT, a favorable policy result or desired JOIN count.

| Roots | Initial root logical FULL | Peak latest live-context logical FULL | JOINs in original reference subset |
| --- | ---: | ---: | ---: |
| 16 | 135.959GB | 187.225GB | 21 |
| 18 | 160.712GB | 221.009GB | 25 |
| 20 | 189.833GB | 258.158GB | 30 |
| 24 | 222.065GB | 304.065GB | 35 |

These are subsets of the original native timeline, not predictions of new
model choices or performance. Latest live context means its most recent
input plus completed output until RETURN. The sums count shared system
prefixes per context and omit obsolete branches, duplicate Host/HBM copies,
buffers and transient allocations. They are not physical unique KV counts.

Actual 24-root native decode batches with at least 16 unfinished workflows
have logical sequence-KV P50/P90 73.888/77.899GB, against a 79.154GB device
pool; mean batch size is 9.219. Geometry is available for every counted
batch. This is a batch logical sum, not unique HBM usage or all concurrent
allocations. It warns that fewer roots alone need not create a sufficiently
small active working set.

## Next Experiment Decision

Prefer an 18-root natural-native feasibility capture before more ablations
or external baselines. Keep distinct projects and the declared selection
order. Running/decode limits remain independently specified as 24 initially.
No new GPU experiment is launched by the offline review.

Before accepting this new comparison's performance result, qualify equivalent
request ingress and observation paths. Natural native chat conversion and
pretokenized Full replay are currently different. Preserve the instruction
not to run another full native replay; do not treat historical calibration as
a bound on this new-model difference.

The target has two separate requirements: retained context demand remains
larger than HBM, while the current executing set leaves useful recoverable
capacity, including cold pages with valid Host copies. Host must also have
room to maintain those copies. If 18 roots still saturate these resources,
consider 16; if migration pressure disappears, consider 20. Do not silently
substitute a decode-batch change for a workflow-count change.

More JOINs should arise from bounded independent subproblems, integration
and follow-up needs selected by the model. Preserve dynamic round count,
fanout, nesting and timing. Do not force a DAG, reject natural completion,
duplicate projects or copy the root's complete source into every child.
An existing subset's JOIN count is not guaranteed after a new natural run.
Prefer more useful return/integration opportunities over indiscriminately
widening simultaneous fanout: more concurrent children can saturate the
active working set again. A child should return its bounded deliverable,
leaving the parent to decide whether new evidence warrants another round.

Ordinary continuation prefetch is a conditional CPU candidate, not enabled
by this decision. Its initial scope should be one already-selected next
request, or an equally bounded admission set. Use native matching and
allocation evidence, validate actual free/reclaimable capacity, and couple
the copy to the next admission opportunity. JOIN priority remains above an
ordinary continuation's bounded priority window. On expiry, revoke optional
HBM protection while retaining a valid Host copy where the retention policy
permits it; avoid another compulsory D2H merely to undo prefetch.
The memory feasibility condition is active state plus native input/decode
reserve plus the admitted restore footprint fitting the device pool.
Cold capacity counts only currently safe reclaimable extents with valid
copies, not all bytes outside a sampled decode batch. If actual execution
continues to saturate HBM at18 roots, an explicit active-state admission
budget is a separate candidate with a throughput tradeoff; a root-count
change does not silently introduce it.

Control work should be driven by a selected-request change, dependency
completion or capacity becoming sufficient. Reuse the existing candidate
and native plan. An unchanged decode step should perform only bounded
state/version checks, without a queue-wide prefix match, graph traversal,
transfer-time predictor or GPU-to-CPU synchronization. Benchmark incremental
CPU cost against scheduling-only before enabling the candidate in Full.

More JOINs and lower concurrency do not by themselves establish a win:
report exposed restore waiting, prompt work, timely reuse, repeated H2D,
old-input reprocessing, complete JCT and throughput on matched work. Stop
expanding speculation if copied states again lose residency before service.

## Evidence

The completed CPU-only review is:

`/home/longhao/experiment/ops/v177_recomputation_review_20261024`

Key reports are `decision_evidence_20261024.json`,
`native_live_capacity_and_decode_20261024.json`,
`wait_capacity_review_20261024.json`, `join_eos_window_20261024.json`,
`prefetch_lifecycle_summary.json` and `prepare_background.json`.
The follow-up report is `losses_and_replay_bias_verified_20261024.json`.

The V176 refusal's reconstructed client timeline is
`/home/longhao/experiment/ops/v176_recomputation_review_20261024/join_capacity_timeline_20261024.json`.
Reports retain source digests/sealed prefixes and their interpretation limits.
