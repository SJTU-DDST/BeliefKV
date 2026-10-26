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

## Workflow Service Metrics And Observed Disparity

The follow-up offline audit merges root and descendant service by root workflow.
Use three separate metrics:

- Workflow time to first service: arrival to the first native `run_batch` launch
  containing any request of that workflow. Also report first submitted model
  request to first launch, excluding client preparation. This is a service
  boundary metric, not token delivery TTFT.
- Ready service gap: time with at least one submitted, incomplete model request
  but no observed worker batch service for the workflow. Merge descendant
  intervals, exclude periods without submitted model work, and report the
  maximum after first service separately from initial admission.
- Ready stall fraction: submitted-ready wall time without observed service
  divided by all submitted-ready wall time. This is an experienced stall
  measure, not a share of exclusive GPU execution or a normalized Jain index.

The worker interval includes its internal computation and dependency waits.
Intra-forward GPU stalls are not identified as no-service gaps. Client transport
and completion callbacks remain in the submitted-ready interval. None of these
metrics replaces JCT, throughput, or normalized resource accounting.

| Workflow-equal-weight statistic | V209 | V215 |
| --- | ---: | ---: |
| Arrival-to-first-service mean | 374.15s | 254.74s |
| Arrival-to-first-service P90 | 1040.59s | 655.33s |
| Arrival-to-first-service maximum | 1125.50s | 979.67s |
| First-submit-to-first-service mean | 336.98s | 217.56s |
| Per-workflow maximum post-start ready gap, median | 25.96s | 50.83s |
| Maximum post-start ready gap over all workflows | 566.75s | 475.10s |

The first mean includes approximately 37.17s before the first request submits;
the earlier 336.98s statistic did not. These boundaries must not be conflated.
In V209, `system_networking027` first launches after1125.50s but its maximum
post-start gap is only1.21s. `web_cms074` starts after45.91s and subsequently
has a566.75s ready gap; `ml_inference088` starts after59.30s and has a460.80s
ready gap. This establishes unequal startup and sustained-progress latency,
not a proof that every delay was avoidable or a resource-share violation.
V209 has no recorded per-agent eligibility timeline sufficient to attribute
each gap to one logical or physical capacity decision.

The audit covers69035/60434 native service records and all18 workflows in both
arms. Detailed36-row CSV, JSON and a0.5s-binned descriptive timeline are under
`../../../ops/thunder_workflow_fairness_tools_20261025.*`.
The author-policy program continuation and1800s escape remain acknowledged;
there is no shared root-workflow service entitlement or measured interval bound.
These new metrics are offline observations, not enabled online SLO enforcement.

## Why The Current Tools Are Short

The natural V195 source, before any replay, records1954 ordinary tool calls.
Read-file/ls account for594/404 calls, with medians1.19/1.44ms.
File edits and writes account for238 calls and use the host-scoped filesystem
backend. Only545 calls use Docker shell execution: median101.12ms, P90
2083.11ms and maximum3716.18ms. Long context and many model rounds do not
therefore imply long individual external operations.

Among85 test-suite-class calls,55 have an error outcome and30 a success outcome.
Nonzero tests can represent expected assertion failures and must not all be
classified as environment errors. Root-trajectory outputs independently show
at least10 collection failures,23 missing-module markers and2 Django-settings
markers; these sets overlap and cover only220 of545 execute outputs.
For example, the CMS subscription tests stop during collection because Django
settings are unset. They do not reach the intended integration test execution.
Successful tests are also bounded by this cohort's3.72s maximum.

All545 audited shell commands use the180s timeout ceiling and none records a
timeout exit code. Source tools have median1.49ms and P90 175.80ms. Matching
all1954 source/replay identities shows replay adds median1.25ms; it does not
shorten source tool execution. The timing covers the actual tool callback,
with separate physical shell durations in sandbox audit.

Retain this as the short-tool workload. A long-tool migration population must
first demonstrate real test collection and execution in each project's working
environment, then collect a new matching natural source. Current system edits
must not silently lengthen tools or substitute altered work into the frozen
V195 comparison. This audit changes no timeout, prompt, workload, or runtime
fairness policy.

## Next Candidate And Acceptance

`thunderagent_retained` retains blocked eligibility while useful eligible work
exists, preserves JOIN/EOS restoration, removes ordinary frontier prefetch and
its extra 1500ms execution binding, and uses normal eligibility after JOIN
restoration. A recovery timer expiry is not an eviction command.

Keep the V195 work, seed, capacities and running/decode limits. Reuse the
captured native baseline. Check complete JCT/throughput, JOIN successor waits,
tool continuation waits/spans, native H2D, actual first use and recomputation.
V216 subsequently completed with mean JCT1032.716s and throughput231.954
output tokens/s. Its first-submit-to-first-launch mean is359.179s, above
V209's336.978s. The target of repeatably improving both JCT/throughput and
workflow service remains unmet. See
`workflow_service_opportunities_2026-10-25.md` for the complete comparison and
the separately declared, CPU-only workflow-opportunity candidate.
