# Segmented Checkpoint Reclaim

The separately declared `selected_queue` candidate must fit a complete safe
input checkpoint plus native reserve before ordinary preload. Its historical
native `max_nodes=16` transaction budget can stop halfway through a large
backed radix prefix. A later attempt can release more of the same prefix,
while intervening native admissions may already consume the first release.

V194 recorded 133 ordinary reclaim transactions that freed capacity, 2,007
complete-capacity rejections and eighteen selected consumers. These counters
do not establish that every extra reclaim was wasted. One trace transaction
released sixteen 4,096-token extents; a later transaction released another
14,028 tokens from the same owner.

The CPU reproduction uses the real FULL-only native radix tree and allocator:
thirty two-token donor segments, one free token, and a locked 67-token active
request. A 52-token capacity target needs 26 segments. The legacy budget
releases sixteen segments and leaves only 33 free tokens, so the consumer
cannot fit. A budget of 64 stops at segment 26 with 53 free tokens. Host state,
session anchors and the active request's lock remain valid. No D2H or H2D is
issued by the reproduction.

Only `selected_queue` now passes `max_nodes=64` to the existing native helper.
The native helper stops when its target is satisfied. The default `waiting`
policy keeps sixteen, the queued donor scope keeps at most sixteen unstarted
requests, and the global probe interval stays 200 ms. No native engine code,
backup policy, scheduling credit or admission authority changes. Native scan
and transaction bounds are separate; an extent may be inspected more than once.
The runtime snapshot records the effective native node limit.

The focused regression passes 86 cases, including consumer/donor identity,
locks, DMA, complete-capacity rejection, source-policy isolation and global
probe backoff. Require qualification of the full inherited application scope
before the candidate is frozen for GPU use.

The native CPU benchmark alternates limits for 101 measured samples after ten
warmups, with setup and cleanup excluded. The sixteen-extent median thread CPU
time is 713.17 us; the 64-limit case, which performs 26 demotions and satisfies
the target, takes 1,253.29 us. This is different work, not a fixed-work speedup.
The owner proof accepts one declared donor and DMA is absent, so these values
are not total runtime cost, GPU overlap or saved JCT.

This repair does not manufacture capacity when the active working set already
fills HBM, and a 64-limit attempt may still fail to fit. Do not claim GPU
benefit before matched comparison. Keep v201 and the running v202 frozen;
both retain the historical `waiting` policy.

Receipts:

- `experiments/reports/v3719_frontier_segment_budget_reproduction_cpu_20261024.xml`
- `experiments/reports/v3720_frontier_segment_budget_focused_cpu_20261024.xml`
- `experiments/reports/v3721_segmented_reclaim_native_cpu_20261024.json`
- `/home/longhao/experiment/ops/benchmark_segmented_reclaim_20261024.py`
