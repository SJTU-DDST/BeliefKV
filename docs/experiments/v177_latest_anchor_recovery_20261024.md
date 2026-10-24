# Surviving Prefix Discovery After Host Eviction

## Completed V176

V176 completes the exact V174 workload:24 workflows,35 JOINs,2369 requests,
151307687 input tokens and531903 output tokens. No workflow is excluded and
all normal tails remain. The source is not independently task-graded.

| Metric | V174 native | V176 Full |
| --- | ---: | ---: |
| Mean workflow JCT | 2102.911s | 2037.600s |
| Event makespan | 2892.903s | 2785.140s |
| Whole-run output throughput | 183.865 token/s | 190.979 token/s |
| Actual logical prefill input | 4255360 tokens | 4223186 tokens |
| Previously served common-input reprocessing | 696174 tokens | 639935 tokens |

The observed mean-JCT reduction is3.106%,and output throughput increases3.869%.
These are single-run point estimates. Repeated-work reduction is8.078%;
Full still reprocesses639935 old input tokens across9 requests. The stronger
simple-scheduler comparison and incremental KV-management benefit remain
unproven. No ablation is started.

The completed offline review is
`/home/longhao/experiment/ops/v176_recomputation_review_20261024`.
It validates the full request population and capacities before comparing
actual prefill ranges. Its initial completed-request diagnostic,V3574,
covered1429 requests and showed15.85% less repeated old input. The complete
result supersedes that completion-conditioned estimate.

## Restore Opportunity

The lifecycle audit records313 controlled JOIN restore commands,86.214GB
transferred,and78.741GB with verified FULL reuse. All commands have confirmed
JOIN scope. Across33 observed JOIN wait episodes,none has a positive FULL
plan before JOIN satisfaction. Verified reused payload includes18.372GB
started between JOIN satisfaction and parent client submit,and60.369GB
between client submit and native arrival. These phases are distinct from
pre-JOIN anticipation. Logical native Host-hit spans are not physical DMA.
The21 negative object-identity checks do not independently prove wasted copies.

Tool prefetch issues no H2D command in this capture. Background native PREPARE
adds14.950GB across245 acknowledged batches:8.686GB idle,6.210GB spawn and
0.054GB critical. Of55 recorded cold-capacity checks,52 report a FULL shortfall
and an unbacked-cold rejection. These are overlapping,conditional checks,
not a unique-state census. Host-use median is99.814% in Full.

There is measurable restoration and reusable state,so current evidence does
not establish that the24-root workload has no prefetch space. Actual capacity
pressure and missed discovery must be distinguished before lowering roots.
Retain24 roots/running24 for the isolated repair replay;use the same completed
native source rather than collecting or replaying a complete native again.
Reconsider20 roots afterward if useful-state loss and restore capacity remain
the limiting factors. A changed population needs its own natural native source.

## CPU Reproduction

Native deletion correctly moves a session's component coverage from an
evicted latest leaf to its surviving ancestor. However,
`snapshot_latest_session_leaf_anchors` still stores the deleted leaf and returns
None. Native matching can reuse the surviving prefix while BeliefKV's backup,
restore and residency queries cannot discover it through that snapshot.
This is a concrete CPU failure mode,not attribution of every unobservable
V176 plan to this defect.

V3577 reproduces the failure using the real FULL-only CPU radix tree,
allocator,incremental spawn backup,ACK and Host eviction. An8-token parent
has two4-token extents. After the last Host extent is evicted,the first4
tokens remain Host-only and in the native session index,but the latest-anchor
query returns None. The original failing result is retained.

The repair follows only the deleted latest leaf's ancestor path using the
native component resolver. It then revalidates live object identity,current
session membership and component markers. It caches the validated ancestor
after every component passes,so warm reads do not repeat the walk. Generation,
closed-session,replaced-object and missing-prefix cases still fail closed.
It does not search unrelated historical branches or add periodic scanning.
The CPU integration now produces a valid4-token H2D plan for the surviving
prefix. Ordinary native eviction and allocation policy are unchanged.

## Qualification

V3580 contains681 unique passing CPU cases,retaining all666 previous engine
qualification identities. V3581 contains5839 unique passing application cases,
retaining all5838 previous identities. Neither report has failures,errors or
skips. V3587/V3584 bind the application and native sources;V3588 retains the
case and source qualification receipt.

V3587 refreshes only the patch README's source digest after its documentation
update. Application execution sources and the native tree are identical to
the sources tested by V3581;no serving-code change is certified without tests.

V3580 combines actual native-module cases from V3578 with module-qualified
application cases from V3579. This preserves correct identities across the
two pytest roots;the11 application checks from V3579 replace empty-classname
outside-root entries,not failed or skipped tests. All source reports pass.

The canonical patch SHA256 is
`85c4b8b429bf7eed86ae8cfebac3dfdcf28bb252a9c3291e32ad10077108c458`,
reconstructing tree`58424f5fcc43f9f2d8ec09a38758017e41b92bc7` over94602c9.
The incremental patch applies over the earlier qualified413cc14e tree.
The candidate is
`/home/longhao/experiment/.worktrees/sglang-latest-anchor-recovery-20261024`.

The canonical change was made after V176 terminated. Frozen V174 and V176
sources still pass their original source verification. The fix has no GPU
performance result yet. The follow-up keeps the24-root configuration and
reuses V175's data-plane/sampling evidence only if the existing registration
checks prove those checked modules and the native sampler remain unchanged.
The actual new Full still requires exact token/session/stop receipts and
same-work capacity validation. No complete native replay or ablation is queued.
