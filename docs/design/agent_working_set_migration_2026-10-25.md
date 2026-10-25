# Agent Working Sets With Native KV Migration

The user's granularity is an agent, represented by one sequential invocation.
A root workflow remains the JCT accounting boundary. This candidate composes
agent admission with existing BeliefKV migration; it is distinct from the
frozen ThunderAgent public-policy arm and from whole-workflow admission.

## Implemented Candidate

`BELIEFKV_AGENT_ADMISSION=working_set` enables the Full-only candidate. It
requires execution-based tool recovery and the measured FULL-only allocator.
Other internal controls and paper arms explicitly disable it.

An active agent retains logical admission across model calls and short tool
intervals. Paused agents' requests remain in the native queue and their waiting
time remains in workflow JCT. Existing JOIN priority, resident-first ordering,
bounded tool continuity and native admission apply inside the eligible set.
Running/decode limits remain 24; the candidate does not change the workload,
the sampling seed, stopping boundaries, or any model output.

The logical budget sums each admitted agent's latest observed context tokens
and a 100-token growth buffer. This deliberately conservative accounting can
double-count shared prefixes; it is not a physical occupancy measurement.
It uses exact completed/arriving request lengths, not future output lengths.
Growth can temporarily exceed the logical budget while requests are in flight.
The controller marks a pause at the call boundary instead of interrupting
generation or pretending that those pages have already been released.

On an observed JOIN wait, the blocked parent yields logical capacity. Existing
spawn backup and incremental PREPARE continue to make its physical KV
recoverable. Host acknowledgement, native locks, sharing and DMA fences remain
required before physical reclamation. A logical pause never proves free HBM.

For tools, maintenance reuses the existing causal long-wait decision. A tool
that has actually remained outstanding for at least one second can also yield
logical admission without any predicted future completion time. This elapsed
fallback does not manufacture a forecast or a physical D2H acknowledgement.
Tool successors recover through the same selected-request path as ordinary
queued requests.

JOIN/EOS and selected-request H2D use one agent recovery reservation, valid for
1500 ms. Reserve the logical working-set opportunity before critical capacity
reclamation. Recheck the agent reservation at enqueue and keep all native
capacity checks. Ordinary prefetch cannot recover an agent outside the active
set. A newly urgent JOIN may supersede an ordinary reservation; this revokes
logical protection only and never cancels already submitted DMA.

Native admission consumes the reservation for that same agent. An unused
reservation expires; blocked warming agents then yield logical capacity.
An expired reservation belonging to an already pending request does not discard
its request or cached state. Existing physical receipts retain ownership until
their actual completion. A reservation is an opportunity within its priority
class, not a guarantee of service within 1500 ms.

## Control And Validation

Arrival, completion, JOIN, tool, cancellation and native requeue update the
state. Maintenance runs at most once per second; intervening callbacks perform
only the deadline guard. Queue filtering uses already registered identities,
then the existing selector still validates current request/session identity.
No per-token history scan, tool text parsing or new model predictor is added.

Synthetic CPU component measurements are in ops `v3804`. At 18 agents, the
mean whole-queue eligibility check was 6.10 us and due maintenance was 6.16 us;
at 128 agents they were 38.55 us and 51.05 us. These are Python thread CPU
components, not measured serving overhead or proof of a JCT improvement.

The first target run, v3801, retained a fixture missing the required event
socket. V3802 passed 150 cases after constructing the real event channel.
V3803/V3805 retain failures from a too-small physical test pool, NumPy creation
timestamps passed directly to a strict native API, and legacy partial observer
inputs. The physical fixture now uses a 512-token allocator and canonical
Python timestamps, and omitted legacy policy declarations continue to mean
disabled; declared or undeclared enabled policies are validated explicitly.
No native lock, type, capacity or ACK rule was relaxed.
V3806 subsequently passed 382 targeted cases, including the real radix
backup/ACK/reclaim/restore cycle with the production growth reserve.
The later next-epoch checkpoint test is included in the inherited qualification.
V3807--V3809 passed the complete 6323-case scope, retaining all 6291 earlier
identities. A subsequent parallel-tool refinement tracks outstanding tool runs
individually so that finishing one sibling cannot erase another's wait or
carry an old tool's start time into a new one. Its final qualification uses
V3811--V3813 rather than overwriting the earlier evidence.
That 6325-case qualification passed. A subsequent reservation-boundary audit
found that a new JOIN claim could replace the owner pointer just after an older
blocked parent's lease expired. Rebalancing now releases every expired blocked
owner, and three fixed-seed randomized event sequences check budget conservation,
running-agent ownership and blocked recovery reservations. Final evidence uses
V3815--V3817; earlier passing and failing evidence remains intact.
The final V3814 component rerun measured means of 6.08 us for the 18-agent
queue gate, 6.65 us for due maintenance, and 0.52 us for an intervening tick.
The fixed-seed tests passed all 6000 generated state transitions before the
final inherited run.

Run the complete inherited CPU scope before freezing a GPU candidate. Retain
the matching V195 source, all normal tails and the completed V209/V211 paper
results. V212 continues on its frozen source. A subsequent candidate must
demonstrate both reduced repeated H2D and reduced end-to-end JCT against the
strongest baseline. Merely moving recovery waits outside the engine queue
does not count as a gain.

## Remaining Joint Work

This implements agent continuity and admission/recovery coupling using the
current physical transfer interfaces. It does not implement a new page-specific
H2D reuse fence or a calibrated layer-deadline start policy. The original
forward-stream reuse fence remains intact. Physical and performance validation,
shared-page-aware budget refinement, and independent matched repetitions remain
separate requirements.

## Stable Eligibility Follow-up

V214 remains frozen with its original ten-second eligibility aging. A live
complete-line prefix recorded 233 capacity switches by 527 registered calls,
so that candidate does not yet establish cross-call continuity in practice.
The follow-up separates ordinary queue ordering age from eligibility eviction.
Keep the active agent across healthy short tools; transfer eligibility on real
JOIN/long-tool/terminal events and growth pressure. A 1800-second bounded
starvation escape can still switch a non-running owner at a call boundary.
JOIN recovery retains its existing higher authority. The longer bound is a
declared candidate choice, not a claim of optimal fairness or measured gain.

Publish the eligibility starvation bound and distinguish critical JOIN,
starvation and growth switch counters. A deterministic 100-call short-tool
case must retain one owner without a capacity switch; separate cases verify
starvation, JOIN yielding and running-call safety. Subsequent full CPU and GPU
qualification are required; preserve the V214 outcome for comparison.
