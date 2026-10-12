# V31 Latest Predictive Diagnosis

Observed on October 12, 2026, in Asia/Shanghai. V31 is the latest
predictive collection; V29 native is the fixed development reference.

The prefetch audit seals independent complete JSONL prefixes between
13:39:02 and 13:39:32 CST. These prefixes are not a cross-file atomic
snapshot or a completed workload.

| Completed Wait Funnel | JOIN | Tool |
| --- | ---: | ---: |
| Observed completed waits | 75 | 6,110 |
| Positive FULL plan before completion | 70 | 1,239 |
| Entered start window before completion | 67 | 34 |
| Submitted before completion | 18 | 34 |
| Confirmed independent early FULL reuse | 15 | 30 |
| Confirmed early reused FULL, GB | 0.681574400 | 1.537556480 |
| Unknown completion boundaries | 41 | 3 |

The JOIN dispatch funnel still has 49 starts without submission.
Capacity-budget observations affect 59 completed JOIN and 1,881 tool
events; admission-budget observations affect 47 JOIN events, and
unavailable service progress affects 24. Reasons overlap and must not
be added as exclusive rejection counts. Unknown boundaries remain
unknown. The lifecycle plan categories also include diagnostic
session-opportunity observations, so their positive-plan counts differ
from the dispatch-only funnel; do not silently combine denominators.

JOIN/tool last-ACK-to-first-service P50 is 3.654/0.853 seconds and P90
is 11.033/13.397 seconds. Demand handoff remains separate from early
JOIN/tool prefetch. Positive plans do not prove that all plan bytes
could have fit, started, or produced a performance gain.

| Complete Window, Seconds | V31 Decode Token/s | Native Decode Token/s | Change |
| --- | ---: | ---: | ---: |
| 300-600 | 1028.65 | 1140.88 | -9.84% |
| 600-1200 | 985.63 | 1238.46 | -20.42% |
| 1200-1800 | 1000.50 | 1076.21 | -7.04% |
| 1800-2400 | 1071.94 | 1197.995 | -10.52% |

Weighted decode batches stay near 47 on both sides. GPU utilization
in 600-1200s is 74.84% versus 92.54%. This is not explained solely by
a smaller predictive batch. Different realized demand, local analysis
and the different predictive patch remain explicit confounders; these
windows are development evidence, not a paired causal speedup claim.
Native metrics reuse the already-written cached window report without
rescanning its raw logs.

Instrumented exclusive Python wall in 600-2400s totals 285.854 seconds.
The largest phases are reentry inspection 50.273s, JOIN PREPARE
24.219s, admission planning 22.437s, tool prefetch 22.349s, handoff
enqueue 21.301s and opportunity sampling 20.986s. These intervals are
not GPU kernels, isolated net control overhead or removable JCT.

Of 2,152 native-budget snapshots in the sealed prefix, 1,056 store
zero restore slots. Budgets can be the last observation rather than a
synchronous allocator census. The independently reproduced rowless
restoration issue is fixed in 4f4567f but is absent from V31's frozen
5c1a646 source. Its 380 relevant checks passed; deploy it at the next
predictive-only cold restart while retaining input/decode and physical
pool reserves and native request-row admission.

The latest independent workflow snapshot observes 55 completed,
53 created unfinished and 48 unarrived tasks out of 156. No error
or incomplete result was observed in that snapshot. Preserve
administrative censoring separately if the development run is stopped.
The predictive-versus-native performance goal remains unmet.

Evidence: v132_v31_latest_prefetch_20261012.json.gz and its summary,
v132_v31_latest_busy_20261012.json,
v132_v31_latest_development_20261012.json, and
v131_full_batch_restore_frontier_validation_20261012.json.
