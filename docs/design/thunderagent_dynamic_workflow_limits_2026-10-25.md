# ThunderAgent Limits In The Current Dynamic Workflow

Keep ThunderAgent's working-set continuity as the scheduling foundation. Its
paper explicitly models Acting/Reasoning phases, idle caching and optional
acting-token decay. Tool awareness and reduced KV churn are existing strengths.
The next candidate must preserve them while addressing measured limitations.

## Evidence And Scope

Pinned author source: ThunderAgent-org/ThunderAgent,
`7ddc8610270e56d3b109eed8796b3a4360fc67c9`.
Paper: arXiv:2602.13692, Sections 4.2-4.3.
V209 is the declared single-backend public-code adaptation with acting weight
one, exact token ingress and native HiCache. Do not generalize its behavior to
every paper configuration or equate the adapter with a full multi-node system.

`/home/longhao/experiment/ops/v3836_thunder_limits_20261025.json` binds V209/V215
native launch timestamps, exact receipts and reconstructed JCT paths.
Both execute the same 2054 requests and 387242 output tokens from V195; all 18
workflows and normal tails are included. This is one descriptive pair, not a
causal ablation or a repeatability claim.

| Observed Quantity | V209 ThunderAgent | V215 Full |
| --- | ---: | ---: |
| Mean complete workflow JCT | 1091.94s | 1139.94s |
| Whole-run output tokens/s | 230.65 | 235.72 |
| JOIN successor submit-to-launch mean | 16.93s | 0.43s |
| JOIN successor submit-to-launch P90 | 40.78s | 1.42s |
| Tool successor submit-to-launch mean | 0.78s | 1.25s |
| Tool successor launch-to-completion mean | 4.75s | 6.06s |
| Actual H2D bytes / summed measured stream time | 204.95GB / 7.49s | 756.23GB / 25.44s |

Launch-to-completion includes computation and interleaving. Submit-to-launch
includes ingress, queueing, admission and restoration. Neither isolates one
subsystem. Stream time omits some CPU/dependency delays and is not an oracle
bound on attainable JCT improvement.

## Problems To Address

1. Dependency progress is absent from the public program identity. Its
   Acting/Reasoning state and context ordering do not express which ready call
   resumes an observed parent or blocks a real JOIN. Preserve immediate JOIN
   reentry when safe capacity becomes available. Do not grant every parent
   indefinite global priority or evict ready peers merely for speculation.

2. Logical capacity and physical residency serve different purposes. A blocked
   agent can retain its continuation eligibility while its KV is backed up and
   offloaded. Conversely, logical eligibility does not prove native capacity or
   transfer completion. Avoid turning every one-second tool/JOIN boundary into
   a working-set replacement. Lend blocked budgets only at a real progress
   obstruction in the first retained candidate.

3. Context size alone is not restoration cost. The paper's shortest-first
   eviction argument targets recomputation waste; in our two-tier backend,
   settled Host copies, missing dirty tails and transfer dependencies change
   the cost of switching. Prefer incremental backup at genuine blocking
   boundaries and EOS restoration using native state. A new cost ranking needs
   a counterexample and matched measurement before changing the author core.

4. Filling more active slots can improve makespan while slowing each workflow.
   V215 starts useful paths earlier but lengthens their execution span; the
   tool-successor regression is concrete evidence to watch. Admission changes
   must improve complete JCT as well as throughput, including their effects on
   previously resident agents. Do not optimize transferred bytes or batch
   occupancy as independent objectives.

5. Coarse periodic control can miss a dependency-ready opportunity. Keep
   five-second global working-set maintenance, but reuse existing RETURN,
   JOIN/EOS and completion events for safe handover and native demand recovery.
   This is not a reason to add a per-token global scan.

6. Workflow fairness is a candidate, not an established cause. Per-agent
   admission can in principle favor workflows with more runnable branches,
   but all 38 JOINs in this source have one member. It cannot validate fanout
   fairness or sibling-slack scheduling. Any later workflow service accounting
   should operate at admission boundaries and avoid undoing resident continuity.

## Next Candidate And Acceptance

`thunderagent_retained` retains blocked eligibility while useful eligible work
exists, preserves JOIN/EOS restoration, removes ordinary frontier prefetch and
its extra 1500ms execution binding, and uses normal eligibility after JOIN
restoration. A recovery timer expiry is not an eviction command.

Keep the V195 work, seed, capacities and running/decode limits. Reuse the
captured native baseline. Check complete JCT/throughput, JOIN successor waits,
tool continuation waits/spans, native H2D, actual first use and recomputation.
No next candidate has a GPU result yet; the target of clearly improving both
metrics over the strongest applicable baseline remains unmet.
