# Unified Execution Restore

The next candidate merges tool successors and ordinary ready requests into
the existing selected-consumer execution-restore path. A tool return changes
readiness; it does not automatically justify a separate recovery priority.
JOIN/EOS remains an earlier, dependency-specific opportunity.

## Scheduling and Capacity

Treat these states differently:

| Owner state | Capacity interpretation |
| --- | --- |
| Waiting for required children | Cannot execute until its dependency returns; backed, unlocked pages may be cold outside the parent's recovery window. |
| Waiting for an externally running tool | May be a cold donor when a supported remaining-wait estimate is long; critical parent recovery can use an actually waiting tool owner without that forecast. |
| Ready in the native waiting queue | Executable work, not intrinsically cold. Prefer fitting resident work within the existing causal and aging rules. |
| In a native running/admitted batch or DMA | Native ownership and transfer guards remain authoritative. |

For ordinary recovery, use the historical `waiting` donor policy. Do not
evict a ready request merely to make another ordinary request resident. Keep
the experimental `selected_queue` path frozen for historical comparisons,
and withdraw the unqualified last-child relaxation.

Choose the near-term consumer with the existing workflow order, which
already includes JOIN precedence, resident ordering, aging and bounded
short-tool continuity. Use free capacity or safe backed blocked donors.
Retain a single selected consumer for at most 1.5 seconds, with its existing
same-causal-class priority; do not block native admission waiting for a
speculative ticket. Reconsideration and expiration retain the original
guards. A selected consumer is not an irrevocable right to GPU service.

This balances dependencies and locality. Always serving resident requests
can starve a Host-only parent; restoring every newly ready request can
produce migrations without advancing workflow completion. Neither prefix
length nor transferred bytes alone is the optimization objective.

## Tool Distribution

The v195 natural capture contains 1,954 observed completed tool calls. There
are 1,409 non-shell calls (72.1%); file reads, file edits, directory listing,
todos and similar local operations dominate their count. The overall tool
median is 1.493 ms. Shell execution has 545 calls, a 101.122 ms median and
87 calls longer than one second. Targeted test commands account for 81 of
those long calls. Tool callbacks report errors on 150 shell calls; sandbox
exit codes are retained separately and must not be conflated with callback
status. No independent task correctness score has been established.

The natural native tool successors wait a median 4,307.608 ms from native
queue registration to observed first service. This does not prove that
every restoration was caused by a queue-time eviction; it explains why
short tools can still have Host-hit successors. Residence at tool start
does not guarantee residence after seconds of queueing.

V203's actual runtime reports `tool_predictor_configured=false`,
`online_tool_timing=true`, no disabled predictor, and 1,920 completed-history
updates (completion-intent tools are excluded). Its timing artifact is empty.
The online model groups by backend and observed command shape, uses bounded
recent histories, requires past completion support, and conditions estimates
on elapsed wait. It does not load the old workload's offline model. Old
accuracy numbers are not evidence for this distribution. Start-only offline
causal diagnostics are also not the actual runtime decision sequence.

Keep this online predictor for selective long-tool offload. In execution
restore mode, tool timing refreshes defer physical ancestry inspection until
the actual backup action, whose existing recapture/ownership checks remain.
No new prediction worker, periodic scan or per-decode-step work is added.

## Configuration and Evidence

The pending profile is
`configs/migration/qwen3_coder_18project_unified_execution_restore_20261024.json`.
It sets `TOOL_RESTORE_MODE=execution`, disables speculative tool-return
restoration and its separate priority, enables selected execution restore,
and uses `FRONTIER_COLD_RECLAIM_POLICY=waiting`. Ordinary and post-tool
consumers share the existing bounded selection. Online timing, prepare,
JOIN/EOS, resident ordering and short-tool continuity remain enabled.

The causal comparison launcher previously required `speculative`, even
though the runtime supported `execution`. It now records the explicitly
declared mode in the immutable registration, passes it to both CPU preflight
and the actual watcher, and verifies the effective tool-prefetch switch.
Legacy registrations retain their speculative default.

Reuse the exact v195 source tokens, dependency graph, tool intervals,
stopping conditions, capacities and seeds. Its frozen root warning/target
is 8/16, not the older 16/24. These are optional soft phase controls:
no truncation or total workflow turn cap is applied, and a real JOIN resets
the phase. The observed maximum phase is 89 calls. Even soft targets affect
delegation behavior; they must not be tightened merely to increase migration
as a fraction of runtime. Any later prompt/budget change needs a new natural
source and completion-quality review.

V203 has completed all 2,054 requests and 387,242 output tokens. Its mean
workflow-event-boundary JCT is 1,413.299 s, versus native's 1,509.741 s
(-6.388%); whole-run output throughput is 214.626 versus 201.434 tokens/s
(+6.549%). V202 is 1,412.136 s / 214.619 tokens/s. The enlarged selected-queue
extent scan did not establish an incremental improvement.

V203 transfers and verifies 8.448 GB of speculative tool FULL in 42 commands;
v202 had no such transfers. Ordinary selected
restore transfers 27.925 GB and verifies 24.790 GB. These are physical
reuse observations, not avoided GPU wait or independently isolated speedup.
Cold reclaim's `causal_waiting_full` label does not by itself separate
blocked owners from authorized ready queue tails. Candidate-check counts
must not be presented as actual queued-victim bytes.

Same-session previously served common-input prefill is zero in both native
and v203; total prefill inputs differ by eleven tokens. The strict causal
path audit reconstructs mean JCT, with 547.250 s from request submission to
first native launch and 806.039 s from that launch to native completion on
the observed last-completing paths. Neither interval isolates KV transfer.
Two fresh idle telemetry snapshots precede cleanup, with no lost/failed
records, writer error or owned processes remaining.

Keep full JCT, whole-run throughput, exposed restore wait, repeated H2D and
recomputation as acceptance evidence. The 10% working improvement target
and benefit over the strongest simple scheduling control remain unproven.
This merged policy has CPU qualification only; no GPU registration has been
started for it.

The first inherited qualification, v3732, retained all 6,099 earlier cases
but found four launcher assertion failures (6,107 passes). They expected the
speculative-tool switch to stay enabled in execution mode. The corrected
assertions check its effective disabled state and priority while preserving
the timing, prepare and frontier checks. Keep the failed report. The
corrected qualification is v3735, with source map v3736 and proof v3737.

Detailed local reports:

- `/home/longhao/experiment/ops/v195_tool_mix_20261024.json`
- `/home/longhao/experiment/ops/v195_tool_reentry_review_20261024.json`
- `/home/longhao/experiment/ops/v203_recomputation_review_20261024/finished.json`
- `/home/longhao/experiment/ops/v203_recomputation_review_20261024/prefetch_lifecycle_summary.json`
- `/home/longhao/experiment/ops/v203_recomputation_review_20261024/full_jct_paths.json`
