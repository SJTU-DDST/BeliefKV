# ThunderAgent Admission With BeliefKV Migration

The user explicitly requests using ThunderAgent as the scheduling foundation.
`BELIEFKV_AGENT_ADMISSION=thunderagent` is a separate Full candidate. V209 remains
the isolated author-policy single-backend reference, and V214 remains the
initial agent-working-set candidate.

## Shared Scheduling Core

`ThunderAgentMigration` inherits the exact `ThunderWorkingSet` implementation
used by the reference arm. Program context size, active/paused state, acting
state, request count and queue sequence live in that one inherited record.
There is no second working-set controller estimating the same capacity.

Reuse initial placement, continuation/new/acting resumption groups, within-group
size ordering, the single-backend BFD projection, 100-token growth reserve,
smallest-acting-first capacity relief and nonpreemptive running-call pause marks.
Run the inherited control algorithm every five seconds. Short tools retain
agent eligibility. Our existing JOIN priority, resident-first and bounded short
tool request ordering operate only inside this admitted set.

The separate one-second maintenance checks actual outstanding tools and leases.
It reuses the existing causal long-tool classifier and observed elapsed-time
fallback. It does not run the ThunderAgent placement algorithm every second.
The old ten-second request-aging capacity rotation is absent from this mode.

## Declared Full Extensions

An observed JOIN or long tool wait yields logical capacity at a safe boundary.
Inactive blocked agents are excluded from the inherited resumption input;
active in-flight or warming owners still count toward capacity. This input
compatibility extension uses no future graph or tool completion time.

One recovery consumer binds a real agent/generation to a 1500ms opportunity.
JOIN can supersede an ordinary consumer. Before changing ownership, verify that
free logical capacity plus non-running, unprotected candidates can fit the
whole target. Follow the reference acting-first/smallest-first victim order.
Running output, native locks and submitted DMA cannot be credited as freed.
An impossible handover makes no partial victim changes.

JOIN unblocking can claim a safe opportunity immediately, rather than waiting
for a periodic placement tick. Ordinary recovery only serves eligible agents.
Actual native admission consumes the same agent's recovery opportunity.
Expiry releases an unused blocked warming owner. Native allocation and physical
receipt ownership remain independent and authoritative throughout.

Use a declared 1800-second starvation escape at the periodic boundary. Unlike
the public router's unconditional timeout activation, this Full extension
requires safe logical room and cannot preempt running output. This difference
must remain visible in the experiment specification.

Existing spawn backup, incremental background PREPARE, long-tool offload,
JOIN/EOS recovery and selected-request recovery keep their native capacity,
ACK, shared-page and forward-stream fence checks. Tool successors share the
selected-request recovery path; speculative tool-timer prefetch remains off
for the first matched candidate.

## Evidence Required

Check equivalence to the shared ThunderAgent core without the declared blocking
and recovery extensions. Exercise three fixed seeds and 3000 observed state
transitions. Separately exercise 6000 randomized blocking, reservation, requeue
and cancellation transitions, plus real radix backup/ACK/reclaim/restore.
Retain every inherited CPU test identity and qualify the entire source before
freezing a GPU run.

Keep the V195 work and all normal tails. Compare complete JCT, whole-run output
throughput, physical H2D, recomputation and native control evidence with V209 and
V214. A logical continuity test, a component CPU benchmark or fewer H2D bytes
alone does not establish a JCT/throughput improvement.
