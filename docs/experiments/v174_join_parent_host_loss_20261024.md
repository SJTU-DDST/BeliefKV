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
