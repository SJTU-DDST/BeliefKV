# Restore Wait Frontiers

The next all-request design must distinguish actual compute-stream waiting
from H2D volume and queue-time movement. The new offline auditor joins
sampled native dependency waits to the actual tool/JOIN successor graph.
A mixed batch is counted once, not once per member request. Missing
invocation or prompt evidence remains unknown.

## Matched Historical Samples

All three arms use the v195 source work. Each sample is a GPU batch, not a
per-request wait. Sampling is every sixteen eligible load-consuming prefills;
the selected batches differ across arms.

| Arm | Batches | Total sampled wait (ms) | First observed FULL (ms) | Later FULL (ms) | Later FULL over 50 ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| Natural native | 52 | 9091.039 | 94.250 | 8996.789 | 36 |
| V202 | 51 | 9444.685 | 204.896 | 9239.789 | 37 |
| V203 | 52 | 8842.618 | 241.683 | 8600.935 | 36 |

Native has 47 pure post-tool batches accounting for 7978.508 ms; one pure
post-JOIN batch accounts for 202.699 ms. V202 has 49 pure post-tool batches
accounting for 9377.608 ms, and one pure post-JOIN batch at 65.397 ms.
V203 has 47 pure post-tool batches at 7668.517 ms, plus two mixed
JOIN/tool batches at 649.724 ms. Do not infer a JOIN population-wide saving
from those sparse samples.

Native's sample roles are 27 root-only, 17 child-only and 8 mixed batches;
V202 has 29/13/9 and V203 32/9/11. The problem is not restricted to a single
parent continuation. The samples support inspecting later-layer progress
and repeated tool-successor restoration; they do not establish an oracle
lead time or an end-to-end speedup from changing admission.

## Short Tool Reentry

Native has 1867 tool successors with tool time at most one second and no
intervening compaction; 1388 have logical Host hits. The same-server
observed result-to-successor-queue gap is 200.278 ms at P50, and 257.163 ms
among the Host-hit subset. In V203, the corresponding medians are
156.648 ms and 264.671 ms (1038 Host-hit successors).

A 100 ms hold covers arrival of only 53 of native's 1388 Host-hit short-tool
successors; 300 ms covers 757, and 500 ms covers 1133. V203 covers
204/567/849 of its 1038 at those respective thresholds.

These are timing coverage counts, not avoided H2D. The native result
observation is not the exact pin acquisition event, and arrival does not
prove that the pin survives capacity pressure or that service follows soon.
The tool duration uses its own client clock only for post-hoc cohort
selection; it is not subtracted from server-clock gaps.

The previously unvalidated 100 ms grace therefore cannot be assumed to
cover typical reentry. A bounded 300 ms comparison is a concrete follow-up
candidate after V204, using the existing single-context, revocable,
JOIN-yielding implementation. Keep all stopping conditions and source work
unchanged. Do not adopt the larger hold without measured JCT, throughput,
allocation pressure, repeated H2D and recomputation evidence.

## Overlap Safety

`CacheController.start_loading` enqueues H2D behind `load_fence_stream`,
which the scheduler sets to the model runner's forward stream. Current
post-launch dispatch therefore fences already submitted work. It can still
overlap later forward submissions. This is a possible reduction of lead
time, not proof that H2D and computation never overlap.

The fence protects allocator reuse while previous GPU work may still access
the pages. Removing it based only on a free-list count is unsafe. A finer
dependency needs allocator/page provenance, or an earlier submission point
with all existing native ownership and result-publication constraints
preserved. The running V204 retains the qualified engine and this fence.

## Instrumentation

`RestoreWaitProbe` now adds relative start/end offsets for each already
sampled compute-stream wait. It reuses the existing event pairs and performs
no new event allocation, synchronization or per-decode-step prediction.
Offsets identify positions within the observed compute-stream span; they
are not absolute DMA or copy-fence timestamps. Legacy samples remain valid
with offsets explicitly unavailable.

The auditor rejects duplicate batch members or dependency layers,
inconsistent sums, partial offsets and impossible chronology. Both native
event and audit files are required; missing input is not zero wait. The output
keeps first-observed FULL distinct from global layer zero and does not
multiply sampled waits by sixteen or allocate mixed-batch stalls to each
request. Between-wait spans contain kernels and CPU launch gaps and are not
exclusive kernel durations.

V204 freezes `6dd7398d4d383df8fdd71261745af2e90c19e9b1`, with engine tree
`1c3bfd7d01c576a9bddbdb1060a2f614beaed187`. It tests unified execution
restore and advisory physical-read deferral against the retained source,
without changing this instrumentation or the native fence.

Detailed local evidence:

- `/home/longhao/experiment/ops/v195_v202_v203_restore_wait_cohorts_strict_20261024.json`
- `/home/longhao/experiment/ops/v195_v203_tool_return_gap_20261024.json`
- `/home/longhao/experiment/ops/v204_qwen3_coder_18project_unified_restore_full_20261024.registration.json`
