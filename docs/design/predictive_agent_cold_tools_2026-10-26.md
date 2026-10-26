# Agent continuity with conditional KV warming

Date: 2026-10-26.

This candidate follows the user's latest execution order: audit a complete
natural native source, run predictive Full, then run ThunderAgent. Other
baselines require clear gains over ThunderAgent first. The configuration is
`configs/migration/predictive_agent_cold_tools_20261026.json`.

## Policy

Retain ThunderAgent-based eligibility across an agent's sequential requests.
Logical eligibility and physical HBM residency are separate: a blocked owner can
retain eligibility while its safe KV is backed or offloaded. Lend a blocked
owner's budget when existing eligible compute cannot progress and real ready
work is waiting. Native allocation, growth and lock checks remain authoritative.
This is execution continuity, not unlimited priority for long jobs.

JOIN/EOS warming uses observed runtime dependencies and the existing bounded
restore reservation. First native admission consumes that reservation; its
expiry does not pause the agent or force eviction. Subsequent requests follow
the same normal retained-agent policy. Ordinary execution-frontier warming
remains disabled.

Tool warming is allowed only for an already eligible, non-running agent that
is still waiting on the same tool/context generation. A paused owner is not
reactivated speculatively. Warming can use free pages or settled Host-backed
pages whose owners are genuinely blocked and satisfy the native safety rules.
Both initial and tail recovery are denied the JOIN-specific ready-queue donor
scope. A Host copy alone does not make a queued agent cold. Warmed tool
successors receive the declared 1500 ms priority window below JOIN parents,
and their Host backing is retained through the unused protection window.

SPAWN authorizes incremental backup. Offloading a parent requires actual
blocking, so a background SPAWN cannot evict a parent that can continue.
Long-tool offload uses the existing causal timing estimator. The new workload
starts its online timing history empty; old-workload accuracy is not evidence
for the new workload. Under insufficient confidence, defer prediction rather
than inventing a future tool completion time.

`prepare_host` backs missing dirty extents from blocked or independently proven
unstarted owners. Backing an unstarted request does not authorize its eviction
for ordinary tool warming. Reuse native dirty indices and bounded existing
maintenance; do not add a per-decode graph scan.

Observed blocking ALL-JOIN siblings can gain eligibility together within
available logical capacity. This never pauses a ready peer, overcommits native
capacity, or waits for an unready sibling. The native scheduler can still split
prefill or execute eligible siblings in different batches.

Complete copies continue while capacity, transfer resources and priority allow.
Native layer dependencies permit a partially restored request to start safely;
first-layer arrival alone is not proof that all later copy latency is hidden.
The existing allocator reserves cross-layer token rows, so partial-layer copy
does not imply proportionally smaller HBM allocation.

## Source and task boundary

Each of 18 roots comes from a different original LoCoBench project. The source
uses Qwen3-Coder-30B-A3B-Instruct, shared running/decode24, Host200GB and seed21.
Native capture disables all Full controls and agent eligibility management.
Actual capacities are measured and pinned for replay.

Only tool infrastructure is repaired. Original project source, tests, fixtures
and API defects remain agent work. Tool startup, execution, write permissions,
shared workspaces and root/child environment propagation passed the separate
36-run live sandbox qualification. Test assertions or collection failures do
not by themselves mean a tool is broken or justify pre-fixing the project.

V229 was stopped after a root completed four sequential singleton JOINs but
no required two-child parallel JOIN. Its results are not a qualified source.
The replacement retains automatic initial tool choice. Only an attempted final
return with unmet delegation uses the existing native 2--4 task-call grammar,
with at most two attempts. The model chooses real assignments and specialists.
Disclose these interventions; do not describe every workflow as unprompted
delegation, fabricate a graph, or retroactively waive a failed contract.

## Evaluation

Audit whole tasks and normal tails, actual parallel/multiple/nested JOINs,
tool durations, blocking intervals, native restore gaps and exposed H2D waits.
Occupancy and blocked time cannot establish reclaimable exclusive pages:
the first Full run must also prove actual safe reclamation and first use.
If the source has insufficient opportunities, report that result and justify
any workload change independently of Full winning.

All arms execute the same complete captured requests, tokens, tool delays and
dependencies, with the same seed and capacities. The completed natural capture
is the native reference; another complete native replay is not scheduled.
Check replay overhead with the bounded shared data-plane qualification.

The initial target is at least 10% lower complete mean workflow JCT and at
least 10% higher full-run output throughput than ThunderAgent, repeatable over
three comparisons. This target is not an observed result or a guarantee.
Also report workflow TTFT/service gaps, extra prefill, repeated H2D, unused
warming, native retraction and controller time. Do not remove normal tails or
select a source according to which policy wins.

The first candidate CPU regression passed 600 cases. The combined retained
tools, sibling admission, source and parallel-retry regression passed 783
cases without skips. The final shared source/operator regression passed 1097
cases, including the two explicitly enabled real Docker fixtures, without
skips. Preserve the earlier run with those fixtures skipped as an intermediate
report. GPU correctness, source suitability and performance acceptance remain
pending.

## Queued source review and comparison

`scripts/audit_predictive_source.py` reviews the completed original population,
paired tool/JOIN intervals, exact native cache counters and sampled GPU restore
dependencies. It rejects incomplete or undrained telemetry. A source with
neither native Host-prefix recovery nor previously served input-prefix loss
does not automatically proceed to the Full probe. Prefix-loss counters remain
proxies, and accumulated Host hits remain request accounting, not exclusive
cold pages or avoidable transfer bytes.

The position-replay calibration supervisor performs that review before GPU
allocation, then runs the bounded exact token/session/stop check and sampling
cost calibration. It starts Full followed by ThunderAgent only after these
checks pass and owned native capture processes exit. It does not schedule
another complete native replay or another baseline. Full's final observer
also checks actual sibling admission, retained-tool eligibility and the
declared tool priority window before proceeding to ThunderAgent.

The source review, replay operator, native position-sampling hooks and existing
agent/tool regression passed 1230 shared CPU cases without skips in
`experiments/reports/predictive_source_pipeline_cpu_20261026.xml`. Native output
hooks were checked against the pinned SGLang tree, and the real Docker fixtures
were explicitly enabled. This does not replace the pending GPU qualification.

V230 subsequently completed all 18 eligible workflows, 3674 model requests and
72 JOINs. There were 52 singleton and 20 two-child rounds, no nested delegation,
and a final-return delegation reminder in each root. Child model-call P50 was
29.5, so the 4/8 soft reminders did not impose an eight-call limit. Preserve this
observed workload shape rather than claiming the target duration was achieved.

V231's source audit stopped before GPU allocation on an incorrect divisibility
check. The Device pool allocates an additional reserved page, so allocation
bytes are not the product of allocatable tokens and per-token data size. The
corrected auditor uses observed Host bytes per token and verifies them against
the actual Host census. Its direct review of V230 passed: 1965 requests had
Host prefix hits, accounting for 9,086,312,939,520 bytes; old served-input loss
was zero. Across 70 sampled GPU batches, total dependency-wait P50 was
119.56 ms and later-layer wait P50 was 119.37 ms. These are sampled batch
measurements, not removable JCT. Keep the 180-second tool call and all workflows.

The corrected geometry and shared pipeline regression passed 1232 cases without
skips in
`experiments/reports/predictive_source_host_geometry_integration_cpu_20261026.xml`.
V231b retains V231's failure and replaces its frozen follow-up only.
