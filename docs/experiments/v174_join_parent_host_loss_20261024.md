# JOIN parent Host loss diagnosis

## Evidence

V3562 refines the five completed requests already sealed by V3552/V3553.
All five are root continuations after an actual satisfied JOIN. Their 453334
missing previously served common-input tokens are fully covered by exact
namespaced token-prefix Host-eviction attribution. Intersecting each evicted
radix interval with the missing common input, then taking the interval union,
removes overlap and excludes new input and output tails.

Every attributed interval's last Host eviction occurs after that parent's
preceding native request completed and before its continuation entered the
native queue. The native and captured prompt identities agree on session,
generation, context and cache namespace. The native prefix-reuse probes agree
with the independently captured common-input loss. This evidence supports Host
state loss during the parent continuation gap rather than attributing these
cases to a new session or changed prompt.

| Parent project | Missing common-input tokens | Device hit | Host hit |
| --- | ---: | ---: | ---: |
| Fintech trading 066 | 105719 | 498 | 0 |
| Web dashboard 039 | 79248 | 499 | 0 |
| Web social 001 | 138004 | 499 | 0 |
| Mobile utility 023 | 60136 | 499 | 58524 |
| Blockchain NFT 035 | 70227 | 498 | 0 |

This is approximately 44.56 GB of FULL state at the measured 98304 bytes per
token. It is not physical DMA volume or a GPU-time/JCT saving. The missing
tokens are about 13.0% of V3553's observed completed cohort prefill input work;
that ratio is also not a time fraction. V171 remains an interrupted diagnostic
source and is not promoted to a qualified comparison.

## V174 corroboration

The new V174 trajectory reproduces this issue before any observed graph or
semantic finalization. V3563 seals the live inputs and service records; V3565
retains 1343 completed requests with fully observed prefill input work. Five
requests miss 450034 previously served common-input tokens, 12.4447% of that
cohort's 3616280 logical prefill input tokens.

V3566 independently identifies all five as JOIN parents and covers all 450034
lost tokens with exact Host-eviction interval unions. Each interval's last
eviction again falls between the preceding native completion and the next
native queue entry. This is approximately 44.24 GB of FULL state, not physical
DMA or removable JCT. The affected parents are data-warehouse012, data-lake050,
system-automation062, fintech-trading066 and blockchain-NFT035.

V3563 also pairs all 51 observed restore-wait samples with their actual prefill
forward identities. Dependency-wait P50 is121.762ms and P90 is387.756ms;
98.4745% of the summed sampled wait occurs after layer0. Twenty short
single-request prefills have wait P50 187.043ms. Native layer-wise transfer
therefore leaves measured exposed dependency waits in this workload.
Do not multiply these sampled waits by16, confuse worker intervals with
kernel times, or extrapolate an incomplete source into a Full JCT improvement.

## Implications for Full

An H2D-only audit misses these JOIN costs: four parents have no substantial
Host hit, so their lost prefix becomes prefill work. Preloading at EOS cannot
restore a prefix that no longer has a matching physical copy. Spawn backup
and later backup availability must be evaluated together with H2D timing.
A high prefetch rate on the surviving Host-backed subset does not establish
good coverage of all resumed parents.

The native tree already reclaims redundant FULL Host copies before evicting
Host leaves under write-back. Its FULL eviction order also prefers leaves
with no session references. Do not propose those existing mechanisms as a
new repair or assume that all pressure comes from redundant copies.

Keep V174 native and its registered V175/V176 follow-up on their frozen
qualified sources. Before changing policy, check in the resulting Full:

- Whether these classes of resumed parents retain their previously served
  prefixes, including continuations with zero or partial Host hits.
- Whether terminal-child reclamation and bounded resident continuation reduce
  Host turnover enough to preserve parent state.
- Whether spawn/idle PREPARE creates usable backups that survive to EOS, rather
  than only increasing cumulative D2H and subsequent Host churn.
- Whether any reduction in repeated prefill accompanies better complete-workflow
  JCT and same-work output throughput.

If a similar loss remains in Full, evaluate bounded parent-state retention under
real Host allocation pressure. It must permit capacity recovery and account for
shared state; permanent parent pins or a larger unfrozen pool are not validated
fixes. No new retention policy or native engine modification is deployed by this
diagnostic, and no performance benefit is claimed.

The auditor is
`/home/longhao/experiment/ops/audit_v171_join_host_loss_20261024.py`.
V3562 retains its source hash, the earlier report hashes, independently sealed
input prefixes, exact eviction IDs and per-request interval evidence.
The exact V3562 auditor is preserved as
`/home/longhao/experiment/ops/audit_v171_join_host_loss_e5397e521a2d_20261024.py`.
The current driver accepts an explicit refinement path for V3566; each report
retains the source hash actually used.

## Replay semantics and concurrency decision

Keep24 workflows until the qualified Full replay completes. Native prefix loss
establishes pressure and an opportunity; it does not establish that Full has
insufficient capacity or that fewer roots would improve the comparison.
Running/decode24 remains a separate setting.

The causal replay fixes input/output tokens, stopping conditions, workflow
dependencies and observed external/tool delays. Its request node has zero
recorded service delay and awaits the current backend's native completion.
Subsequent client delivery is measured from that completion. The generate
payload supplies input tokens and session/cache namespaces, not captured
cache hits, eviction decisions, uncached-token counts or H2D commands.
Thus a prefix recomputed during source capture can be a cache hit in Full.
Preserved non-model delays and the frozen trajectory remain experimental
limitations; replay does not model schedule-dependent changes in agent choices.

V3567 passes57 focused existing CPU cases. In particular, changing the source
model service time from100ms to10000ms does not change the evaluated causal
plan when replay service times are held fixed. This checks release semantics,
not GPU speedup or absence of all source-dependent effects.

V3570 audits the now completed V174 native capture:2369 requests across24
workflows and35 JOINs. Actual prefill geometry covers4255360 input tokens.
Nine requests reprocess696174 previously served same-session common-input
tokens,about16.36% of observed input prefill. The geometrical count agrees
with first-service common-input loss. All request identities and observed
prefill coverage agree;there is no additional input prefill above first-service
uncached input in this capture. First requests per context account for2423676
prefill input tokens and are reported separately. The old-input count excludes
previous generated-output prefixes and is not a complete recomputation bound,
CUDA-time fraction or removable JCT.

The remaining comparison must retain every source request and normal workflow
tail and check:

- Actual old-common-input prefill,first-service prefix loss,total input prefill
  and generated-history reprocessing for native and Full.
- Timely verified prefetch reuse,backup survival to restore/consumption,and
  repeated restoration or displacement before first service.
- Complete-workflow JCT and same-work output throughput at unchanged capacity.

If Full preserves these useful prefixes and reduces repeated work while
improving end-to-end performance,retain the24-root workload. If Full also loses
substantial useful state and its backups/prefetches repeatedly displace one
another before use,consider fewer concurrent workflows. Check policy and
lifecycle failures before attributing all losses to workload size. A changed
project-disjoint population needs its own natural native capture;do not compare
it to the old24-root baseline or silently change the running/decode limit.

The CPU-only follow-up auditor
`/home/longhao/experiment/ops/audit_v176_recomputation_20261024.py`
waits for a completed,validated Full registration. It compares actual service
ranges against source token metadata,validates matching request work and
capacities,and then runs the existing prefetch-lifecycle and background-PREPARE
audits. V3568 passes10 focused auditor cases covering different cache outcomes
on identical work,generated-history separation,missing evidence,session
bindings and population/identity failures. It neither schedules GPU work nor
automatically changes concurrency.

## New-model EOS window

V3571 observes all35 native JOIN parent continuations. V3572 pairs their child
terminal completions with parent submits and actual prefill forwards using the
native monotonic clock. All35 completion-to-submit windows have P50=63.382ms
and P90=90.784ms. One child ends its final request with length rather than a
normal no-tool stop;exclude that request from the EOS cohort,without removing
its workflow or work from the experiment. The34 normal EOS cases have
completion-to-submit P50=62.705ms,P90=92.244ms,and minimum33.098ms.
EOS-to-JOIN-satisfied P50 is17.126ms. Do not reuse the older170ms estimate
without identifying the model,run and endpoint.

Two one-request JOIN prefills are covered by the one-in16 restore-wait sample:

| Native parent | EOS to submit | Prefill input | First-layer wait | Later-layer wait |
| --- | ---: | ---: | ---: | ---: |
| Mobile utility023,epoch24 | 68.254ms | 516 tokens | 4.060ms | 156.373ms |
| Mobile utility023,epoch32 | 64.489ms | 600 tokens | 0.016ms | 162.386ms |

These are actual compute-stream waits paired with their native forward
identities,not inferred DMA volume. They establish residual waits after the
first FULL layer in native,not the residual after EOS prefetch or an unbiased
estimate for all35 JOINs. Do not extrapolate the two sampled cases.

The34 normal cases have EOS-to-first-prefill-launch P50=12937.639ms. This
includes frontend processing and native queueing. Full's JOIN priority changes
that queue,so the native slack is not a guaranteed Full prefetch budget. Complete
hiding requires every layer's KV to be ready by its consumption time;first-layer
readiness alone is insufficient. Actual dispatch/capacity delay consumes the
available lead. V176 is now running on its frozen qualified source. Measure
its dispatch lead,verified reuse and sampled residual dependency waits before
claiming complete JOIN H2D hiding or changing the EOS policy.

The qualified native `beliefkv_prefetch_can_admit` path permits pending
FULL-only transfers to use native layer fences. It does not require every FULL
layer's transfer ACK before admitting the parent. Keep admission/capacity delay
separate from compute-stream dependency waits;moving a wait before forward
would not establish that the end-to-end restore cost disappeared.
