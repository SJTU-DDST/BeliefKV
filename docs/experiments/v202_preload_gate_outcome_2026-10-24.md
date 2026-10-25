# V202 Ordinary Preload Gate

All eighteen workflows complete on the exact v195 trajectory: 2,054 requests,
116,355,888 prompt tokens and 387,242 output tokens. Normal tails remain.
The sole policy change from v201 removes the four-normal-admission gate on
ordinary preload. Priority fairness credits, one selected consumer, JOIN
precedence, `waiting` cold reclaim and native admission remain unchanged.

| Arm | Mean event-boundary JCT (s) | Whole-run output tokens/s |
| --- | ---: | ---: |
| V195 natural native | 1509.740834 | 201.434306 |
| V201 Full, preload quota 4 | 1428.570736 | 212.223177 |
| V202 Full, preload quota 0 | 1412.136096 | 214.619034 |

V202 improves native mean JCT by 6.4650% and throughput by 6.5454%. Its further
improvement over v201 is approximately 1.15% in JCT and 1.13% in throughput.
These single runs do not establish that small incremental difference against
run-to-run variation. Neither Full meets the working 10% JCT/throughput target.

Ordinary preload selects 51 consumers, with 26 entering a native batch during
the bounded selection, compared with 33/17 in v201. FULL transfer increases
from 58,252,001,280 to 121,114,460,160 bytes. Verified first-service reuse
increases from 38,620,889,088 to 106,445,930,496 bytes. Negative legacy identity
witnesses must not automatically be called wasted transfers.

The source has 38 logical JOINs. The v202 planner audit observes 37 wait
episodes; twelve have verified FULL reuse started before JOIN completion,
totaling 63,451,398,144 bytes. V201 has ten such JOINs and 53,794,701,312 bytes.
Total JOIN verified reuse across all phases is 87,387,930,624 bytes in v202,
versus 110,770,126,848 in v201. Later restoration is not early JOIN prefetch.
Tool-prefetch commands remain zero; no positive tool-prefetch claim follows.

Previously served same-session common-input prefill remains zero on both
sides. Input prefill is 2,730,955 tokens in v202 versus 2,730,948 native.
V202 prefill worker intervals total 436.426 seconds, versus 434.586 in v201;
decode forward counts decrease from 67,660 to 65,962. V202 H2D totals
7,251,112,525,824 bytes and 232.390 summed stream seconds; D2H totals
850,833,113,088 bytes and 20.806 summed stream seconds. Stream times are not
exclusive PCIe occupancy or removable JCT.

Sampled dependency-wait median is 179.561 ms in v202, compared with 79.304 ms
in v201 and 124.821 ms native. The sampled batches differ. Retain this result
alongside the positive end-to-end metrics: increased preload bytes do not
prove less exposed waiting or isolate the cause of the throughput change.

Application commit is `a6eaa48fa66d6ee72886c9fd0541cbb6f80029f7`; qualified
engine tree remains `1c3bfd7d01c576a9bddbdb1060a2f614beaed187`. The complete
prefill audit and strict workflow JCT path audit pass. Two fresh idle telemetry
snapshots precede cleanup, with no pending work, writer backlog, drops,
failures or writer error. All owned processes exit.

Retain quota four as the historical default until repeated matched results
justify adopting zero. V203 is separately declared against v202 because v202
weakly improves both primary metrics. Its selected-queue capacity repair has
its own qualification and frozen source; this is not a broad ablation campaign.

Authoritative receipts are the v202 registration and
`/home/longhao/experiment/ops/v202_recomputation_review_20261024`.
