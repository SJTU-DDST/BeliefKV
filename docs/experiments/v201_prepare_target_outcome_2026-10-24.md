# V201 Repaired Standard Full

The run completed normally but has not met the working 10% JCT and throughput
targets. Do not begin broad ablations or call individual modules beneficial
from this whole-system comparison.

## Matched Result

| Arm | Mean workflow JCT (s) | Whole-run output tokens/s |
| --- | ---: | ---: |
| V195 natural native | 1509.740834 | 201.434306 |
| V201 standard Full | 1428.570736 | 212.223177 |

Mean JCT decreases 5.3764% and throughput increases 5.3560%. All eighteen
workflows and normal tails are retained. Each arm executes the same 2,054
requests, 116,355,888 prompt tokens and 387,242 output tokens. Both columns use
workflow-start to workflow-end event boundaries. This is one Full run, not a
repetition claim or an independent task-correctness grade.

The eighteen project-disjoint roots keep the 8/16 soft phase budget and
children keep 24/32. Running/decode limits remain 24. Join prefetch is EOS-only.
Tool prefetch, resident-first, stage priority, JOIN-parent priority, short-tool
queue continuity, background incremental preparation and ordinary selected
request restore remain enabled. Cold reclaim uses `waiting`; extra short-tool
KV pins remain disabled. Ordinary preload still requires four normal
admissions. Context, pools, seeds and stopping conditions match v195.

## Work and Transfer Evidence

Actual input prefill is 2,730,948 native versus 2,730,953 Full tokens. Previously
served same-session common-input prefill is zero on both sides, with no
unresolved prefill geometry. The prefill worker interval sum decreases from
461.899 to 434.586 seconds. Decode forward counts decrease from 74,706 to
67,660. These are descriptive observations, not isolated causes of the gain.

Native H2D totals 8,888,613,273,600 bytes and 279.164 summed stream seconds.
Full H2D totals 7,280,515,842,048 bytes and 232.082 summed stream seconds.
Native D2H totals 2,618,596,884,480 bytes and 59.805 summed stream seconds;
Full D2H totals 848,031,842,304 bytes and 20.205 summed stream seconds.
Do not add these overlapping stream totals or treat them as removable JCT.

Sampled dependency wait median is 124.821 ms over 52 native batches and
79.304 ms over 51 Full batches. The samples are different batches; they do not
constitute a paired per-request wait reduction or a full-run stall estimate.

## Prefetch Coverage

All 38 JOINs have observed completion boundaries. Thirty-four have a positive
FULL plan before completion. Ten have verified reuse of FULL transfer started
before completion, totaling 53,794,701,312 bytes. Twenty have an observed
pre-completion capacity-budget rejection. Reason counts may overlap; planning
logs report eight pre-completion submissions, while the native action/use
chain proves ten early-use events. Keep those different evidence sources.

Across all JOIN phases, controlled FULL transfer totals 117,993,799,680 bytes,
of which 110,770,126,848 bytes have verified first-service reuse. Transfers
after JOIN completion must not be credited as anticipatory JOIN prefetch.

Ordinary selected-request restore selects 33 consumers, and seventeen enter a
native batch during the bounded selection. It transfers 58,252,001,280 bytes,
with 38,620,889,088 bytes of verified reuse. The remaining identity witnesses
are negative, not proof that all other bytes were wasted: the recorded proof
version can lose identity after native radix splits.

Tool-prefetch commands are zero. Of 304 observed tool-wait episodes, four have
positive FULL plans before completion and none reaches the selected start
window. A no-Host-backed-missing-copy reason occurs in 270 episodes. Do not
attribute the whole-system speedup to tool prefetch. Short tools and later
request-queue eviction are distinct opportunities.

Background preparation issues 218 batches averaging 33.62 MB, totaling
7,328,464,896 bytes. Spawn preparation adds 3,420,684,288 bytes and critical
preparation adds 194,641,920 bytes. Capacity diagnostics still observe unbacked
cold candidates and Host capacity restrictions. Raising a batch limit alone
does not establish a remedy.

## Integrity and Next Candidate

Application commit is `727d46890176bb1445bc307e096398e7e2491df4`; qualified
engine tree is `1c3bfd7d01c576a9bddbdb1060a2f614beaed187`. The bounded,
custom-path-only PREPARE-ACK allocation retry passed its CPU reproductions.
The application qualification has 6,094 passing cases and the inherited
engine qualification has 701. The v200 bounded GPU check verifies exact
native tokens, sessions and stopping behavior before this Full.

Two fresh idle telemetry snapshots precede cleanup, with zero pending
requests/batches, writer backlog, drops, failures or writer error. All owned
processes exited. The strict workflow JCT path audit passes.

The next separately declared candidate changes only
`FRONTIER_RESTORE_ORDINARY_QUOTA` from four to zero. Ordering fairness credits,
one selected consumer, JOIN precedence, cold-donor authority and native
admission remain unchanged. Reuse the same native capture and GPU sampling
check. The candidate is already CPU-qualified but has no GPU performance claim.
Keep the historical default until useful first-service coverage and net
performance establish a reason to change it.

Authoritative receipts are the v201 registration and
`/home/longhao/experiment/ops/v201_recomputation_review_20261024`, including
`recomputation.json`, `prefetch_lifecycle_summary.json`,
`prepare_background.json` and `full_jct_paths.json`.
