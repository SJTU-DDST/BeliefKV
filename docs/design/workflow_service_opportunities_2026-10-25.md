# Workflow Service Opportunities With Agent Continuity

## Evidence And Scope

ThunderAgent has fairness-related queue ordering and a 1800-second paused-program
escape. It does not have a shared root-workflow service account in the pinned
public implementation. Our single-backend projection treats one sequential
invocation as the program. Do not generalize its results to the full distributed
paper system or claim ThunderAgent ignores tools.

Pinned author commit: `7ddc8610270e56d3b109eed8796b3a4360fc67c9`.
See `ThunderAgent/scheduler/router.py`, new-program queue checks and
`_wait_for_resume`. Preserve the V209 reference arm.

The completed exact-work pair contains 18 workflows, 2054 requests and 387242
output tokens in each arm. These are complete-population descriptive results,
including all normal tails:

| Quantity | V209 ThunderAgent | V216 Retained Full |
| --- | ---: | ---: |
| First submit to first launch, mean | 336.978s | 359.179s |
| First submit to first launch, P90 | 1001.953s | 908.854s |
| First submit to first launch, maximum | 1087.145s | 1245.113s |
| Per-workflow longest subsequent ready gap, median | 25.963s | 19.498s |
| Per-workflow longest subsequent ready gap, P90 | 310.681s | 58.270s |
| Complete mean JCT | 1091.940s | 1032.716s |
| Whole-run output tokens per second | 230.646 | 231.954 |

Full improves ongoing service tails, but initial mean and worst wait regress.
One throughput observation only 0.57% above the reference cannot establish
repeatable throughput noninferiority. The old source has singleton JOINs and
unhealthy tests; it cannot validate sibling scheduling or normal long-tool
behavior.

Evidence: `../../../ops/workflow_service_comparison_20261025.json`.
The reusable audit is `scripts/audit_workflow_service.py`; it reads complete
request receipts, checked native launches, drained telemetry and sealed JSONL
prefixes. The experiment's separate exact-work audit is still necessary.

## Metrics

Report equal-weight workflow distributions, not request-weighted distributions:

1. Workflow first service: first submitted model request to first actual native
   worker launch of the root or any descendant. Report arrival-to-first-launch
   separately, because client preparation precedes the first submit.
2. Ready service gaps: union all intervals with submitted incomplete model
   requests, subtract unioned worker service, and report each workflow's longest
   gap after initial service. Include the distribution of all gaps as secondary
   detail. Parallel siblings count once.
3. Ready stall fraction: unserved submitted-ready time divided by submitted-ready
   time. Tool/JOIN-only periods with no submitted model work are excluded.
4. Complete JCT, whole-run throughput, repeated recovery and recomputation remain
   required. An early prefill chunk followed by a long stall is not success.

Worker intervals include internal dependency waits. These metrics do not measure
exclusive GPU allocation, user token TTFT/TBT, or establish that every observed
wait could have been avoided.

## Separate Online Candidate

`AGENT_ADMISSION=thunderagent_workflow` is explicitly separate from both the
paper arm and `thunderagent_retained`. It retains the agent as ownership identity,
the original token-capacity accounting, JOIN/EOS restoration, incremental backup
and native physical authority. It adds no ordinary frontier recovery reservation.

The policy aggregates all descendant readiness and batch submissions under the
root workflow. Initial opportunities target 30 seconds; subsequent ready gaps
target 15 seconds. These are experimental intervention thresholds, not guaranteed
SLOs, observed performance, or calibrated optimal settings.

At most once per five seconds the policy may hand over eligibility to one overdue
workflow. It first verifies sufficient logical capacity from a complete safe donor
set. Donors cannot be in flight, critical JOIN owners, or holders of live restore
reservations. An owner must have held eligibility at least 15 seconds. Prefer
known blocked owners, then inactive-between-calls owners, then ready peers whose
workflow has no service debt. Do not immediately readmit a donor ahead of the
selected overdue workflow.

If a periodic probe finds an overdue waiter but cannot hand over safely, remember
the deferred opportunity and retry at an actual request-completion boundary.
A successful handover starts the normal five-second cooldown. Do not repeatedly
miss short safe boundaries simply because they fall between maintenance ticks.

This handover changes logical eligibility only. Native admission, row/page
allocation, valid Host copies, locks, dirty-tail backup and DMA guards still
decide physical actions. Insufficient safe capacity defers the opportunity.
Running decode is never preempted to satisfy a time target.

On resumption, one representative per root competes in each pass before taking
additional branches. Within a root, preserve continuation/new/acting categories
and observed context ordering. Existing JOIN priority remains ahead of an
overdue ordinary prefill. Ordinary overdue promotion applies within the eligible
set, so a newly admitted cold root is not indefinitely bypassed by hot arrivals.

## Sibling Opportunities

Only a real observed blocking ALL-JOIN establishes a sibling cohort. When one
sibling is eligible and a ready sibling can fit existing free logical capacity,
admit it without waiting for the next five-second maintenance round. No donor
eviction, extra capacity credit, or restore lease is created for this promotion.
An overdue different workflow blocks the extra cohort promotion.

Do not wait for an unsubmitted or slow sibling, equalize finish timestamps, or
infer similar remaining work from similar context size. A single useful ready
child proceeds immediately. The possible benefit is reduced queueing of the
last child and overlapping genuine tool waits, not a synchronization barrier.
Measure start spread, JOIN last-child waiting and complete workflow JCT on the
new model-selected multi-child source.

## Control Cost And Acceptance

Decode observation only touches this batch's identities and root accounts. It
does not scan or sort the waiting population, parse text or invoke a predictor.
Admission and event boundaries reuse current request identities; global
opportunity/resumption selection runs at five-second cadence.

Compare the separately frozen candidate with reference ThunderAgent and retained
Full on identical complete tokens, dependencies, real tool durations, capacities
and seeds. Reuse the matching natural native source. The new workload must first
qualify actual test execution and parallel delegation. Keep every result and
normal tail; reject an unhealthy source as a population rather than quietly
deselecting its slow workflows.

Acceptance requires repeated improvement in first-service and ready-gap tails,
complete mean JCT improvement, and whole-run throughput at least as high as the
reference with uncertainty reported. A CPU microbenchmark or one 0.57% throughput
gain is insufficient. Also inspect extra H2D, old-input recomputation, critical
parent delay and stalls caused by logical eligibility with no physical capacity.

## CPU Qualification

`experiments/reports/workflow_service_environment_application2_cpu_20261025.xml`
records 6498 passing tests and two passing subtests, with no failures, errors or
skips. All 6497 identities from the immediately preceding qualification remain.
The status-only follow-up records 58 passes in
`experiments/reports/workflow_service_declared_status_cpu_20261025.xml`.
Native engine sources were not edited in this turn.

`experiments/reports/workflow_service_control2_cpu_20261025.json` records about
11-15 microseconds per policy-only 24-request batch observation across
36/128/512-agent populations. Due control calls measure about15/38/132
microseconds, respectively. These are local CPU microbenchmarks, not full
adapter cost, adversarial donor search, GPU overlap or performance acceptance.
The reusable audit reproduces the prior V209/V216 service statistics in
`experiments/reports/v209_workflow_service_reusable_audit_20261025.json` and
`experiments/reports/v216_workflow_service_reusable_audit_20261025.json`.
