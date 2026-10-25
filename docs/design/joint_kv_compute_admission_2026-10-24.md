# Joint KV Recovery and Compute Admission

Status: combined design proposal, awaiting the requested paper-baseline
reproductions. Only the unrelated batch-dependency repair below has been
implemented. V205 has completed; retain all completed and running frozen trees.

The user has requested that the two descriptions be implemented as one design:
the layer-deadline model and dependency-specific copy fences refine the same
bounded execution plan, capacity reservation and admission opportunity described
below. They are not separate scheduling policies. Reproduce the declared paper
baselines before implementing this combined proposal. The prerequisite batch
dependency repair already committed before this request remains a separate fix.

## Objective

Reduce exposed recovery stalls and avoid repeated transfers across all eligible
requests, while improving workflow JCT and output throughput on identical work.
Tool successors and ordinary queued requests use the same recovery admission
path. JOIN/EOS remains an earlier, dependency-backed trigger.

Zero GPU stall is not a sufficient objective: withholding a request for its
entire transfer can move its wait into the queue without reducing completion
time. Measure submit-to-service, service-time dependency waits and workflow JCT
together. Prefetched bytes alone are not a success metric.

V204 already unified tool and ordinary execution recovery. Its mean JCT was
1415.542276 seconds, against V202's 1412.136096 seconds on the same work.
Therefore unifying triggers has not established an additional performance
benefit. A new proposal must change the coupling of capacity, transfer and
actual admission, rather than relabeling the existing path.

## One Bounded Execution Plan

Use the existing workflow priority classes, JOIN precedence, bounded short-tool
continuity and aging. Preserve running and decode limits as separate settings.

Maintain one selected recovery consumer initially. Selection is a commitment to
the next feasible admission opportunity within its priority class, rather than
a prediction that a queue entry might eventually run. Higher-priority JOIN
arrivals may supersede it; record the displaced transfer and its later use.
Do not preempt an active model request or bypass native admission.

For each scheduling boundary:

1. Reuse the selected batch and existing residency observations. Inspect only a
   small bounded candidate set, not all contexts or token histories.
2. Fill current compute from eligible resident requests. Independently select a
   nonresident request using workflow priority, age and recoverable capacity.
3. Reserve the selected request's complete missing checkpoint and native input
   and decode headroom. Associate the reservation, transfer and admission
   opportunity with the same request identity and generation.
4. Submit its necessary H2D work while current compute proceeds. Consume actual
   readiness at admission; the stream waits only on this batch's dependencies.
5. Give the selected request its reserved opportunity when feasible. A streamable
   partially loaded prefix may enter with its real remaining dependencies. An
   unrelated resident batch may proceed without inheriting those dependencies.
6. Release the short reservation on admission, cancellation, supersession or
   bounded expiry. Keep in-flight DMA ownership until its real completion.

One consumer limits speculative occupancy and stale plans. Increasing the
lookahead requires evidence that one consumer leaves useful bandwidth idle.
Do not reserve an arbitrary large fixed HBM partition before measuring its
cost to active decode.

The two descriptions share one state machine:

| State | Evidence required to advance | Expiry or invalidation |
| --- | --- | --- |
| Selected | Current queue identity, priority class and generation | Cancel, changed identity or a more urgent JOIN |
| Capacity reserved | Actual complete checkpoint and native headroom fit | Native capacity or ownership change |
| Copying | Native allocation and real source/destination dependencies validated | Cancel future work; retain ownership of in-flight DMA |
| Eligible | Actual layer dependencies allow the chosen compute start | Revalidate at native admission |
| Admitted | Native admission grants this same request its opportunity | Hand capacity and locks to the real running request |

JOIN/EOS may enter this same process before HTTP reentry when the dependency
and session checkpoint are already known. Tool return and ordinary ready
requests enter through queue selection. Do not create separate competing
tool, queue and layer-prefetch reservations. Refresh the plan on existing
queue, EOS, transfer-ACK and release events; avoid a new per-token scan.

## Capacity Is Part of Admission

With the current token allocator, allocating a missing FULL prefix reserves its
slots across layers. Copying the first layers early does not reduce the final
capacity obligation. A valid plan must satisfy:

```
active_native_reserve
  + selected_checkpoint_reservation
  + speculative_reservation
  <= usable_KV_capacity
```

Capacity released by the selected request's own retirement or by another
request is usable only after the native ownership and GPU fences permit reuse.
An anticipated tool call or a free request row is not a released KV page.

Prefer safe Host-backed duplicates belonging to actually blocked invocations,
such as a parent waiting for JOIN or an invocation already waiting for a long
tool. Existing PREPARE can make these duplicates available without synchronous
D2H on the recovery path. Preserve last-child, request-lock, host-lock and DMA
guards. Completion must be known before a child is classified as terminal.

An unstarted ready request is not automatically cold. Keep the `waiting` donor
policy. Do not evict an equally eligible resident ordinary request merely to
restore another ordinary request. Such a switch needs a distinct, demonstrated
workflow urgency or fairness reason, and its eventual return transfer counts
against the benefit.

If no safe capacity or no overlapping compute exists, accept native recovery
waiting. No scheduling rule can guarantee hidden transfers for an all-cold
working set that exceeds HBM.

The first paper comparison changes the reference for this design. On the same
V195 work, V209's single-backend ThunderAgent adaptation completed with mean JCT
1091.940321 seconds and 230.646022 output tokens/s, versus V204 Full's
1415.542276 seconds and 214.032350 output tokens/s. Both retained all normal
tails and the same request/token population. These are single-run observations.

Its working-set admission also transferred substantially less H2D. This
requires evaluating the bounded *active context set across successive calls*,
not just the current running count and one next-copy buffer. A completed model
call followed by a short tool should not immediately make its program compete
as an unrelated cold arrival. This is a candidate to incorporate after the
baseline reproductions, with observed JOIN liveness and all external admission
waiting retained in workflow JCT. Do not attribute the measured gain solely to
DMA overlap or adopt the sequential program mapping's dependency-blocking case.

## Transfer Lead Time

Layerwise readiness is a pipeline, not a guarantee of full overlap. For a fixed
copy schedule, let `A[l]` be the time layer `l` becomes ready after H2D submission,
and `C[l]` its compute time. If compute starts `P` after submission, the total
exposed dependency wait in this simplified serial-layer model is:

```
W(P) = max(0, max_l(A[l] - P - sum_{j < l}(C[j])))
```

The model assumes fixed arrivals, sequential compute and no additional resource
interference. It is not a measured oracle. It shows why first-layer readiness
alone is insufficient when later copy arrivals fall behind compute.

Estimate only the lead time needed for the actual batch to run with acceptably
small stalls. Reuse low-frequency byte-throughput and existing layer-wait
observations. Estimation chooses between complete early recovery and a
streamable partial start; it must not reject useful JOIN/EOS transmission merely
because a timing sample is missing.

Use compute already justified by the scheduler as the hiding window. For
example, a 40 ms restore can fit behind 60 ms of eligible work if capacity and
stream dependencies allow it; a 100 ms restore in that window still exposes
about 40 ms under the same simplified assumptions. Do not add unnecessary
compute or grow batches solely to make the reported overlap larger.

H2D for an actual selected consumer takes precedence over speculative distant
work. PREPARE uses the other direction when beneficial, with bounded batches
and existing arbitration. Separate PCIe directions can still share HBM, copy
engines and launch resources; concurrency must be verified in the trace.

The existing forward-stream page-reuse fence remains mandatory. The unrelated
batch-consumer repair below does not remove the H2D producer's broad fence.
A finer fence requires proof that the selected destination pages are either
already safe or have only the recorded page-specific GPU dependencies. Until
that proof exists, retain the current wait and measure how much overlap it
prevents; do not count a Python enqueue as concurrent device execution.

## Avoid Recovery Before Trying to Hide It

Short-tool residency and bounded queue continuity can prevent a transfer that
prefetch would otherwise need to hide. Preserve JOIN precedence, aging and
revocation under native capacity pressure. Retain only the already implemented
bounded number of protected contexts initially.

The observed native short-tool result-to-successor-queue gap is 200.278 ms at
P50, or 257.163 ms in its logical Host-hit subset. A 100 ms hold covers only
53 of 1388 Host-hit arrivals; 300 ms covers 757. These are arrival-coverage
counts, not avoided transfers. A 300 ms candidate needs its own comparison
before adoption; arrival does not establish that protected KV survived or that
the successor could enter compute.

## Implemented Prerequisite: Batch Dependencies

The historical custom speculative producer was returned globally to new
prefill batches even when every selected request's prefix was already resident.
A CPU reproduction retained this unwanted consumer on a resident request.

The candidate adds a bounded actual-prefix check at native batch binding:

- Only the recorded custom producer, FULL-only write-back cache, and known
  request anchors qualify. Native loads without that producer keep their
  existing consumer unchanged.
- Walk at most 256 ancestors across the selected batch, deduplicating shared
  paths. Any pending anchor, split-fragment pending marker, absent value,
  unsupported mode or uncertain identity keeps the existing fence.
- Return no consumer only when all required prefixes are known resident.
- Change no allocation, locking, transfer, ACK or forward-stream reuse fence.
  Count checks and skipped unrelated waits in the existing status snapshot.

Real CPU tree tests cover a separate pending branch, a shared pending prefix
split, ACK clearance, and the conservative write-through fallback. Write-through
does not carry the write-back fragment markers and therefore must not use this
shortcut.

This establishes removal of an unnecessary dependency in the reproducer.
Frequency, actual saved GPU time and JCT benefit remain unmeasured.

## Validation Order

V205 scheduler-only has completed on the exact V195 work. Its mean workflow
JCT is 1458.076082 seconds, versus V204 Full's 1415.542276 seconds. Preserve
that control to distinguish custom KV benefit from workflow scheduling.

Reproduce and qualify the declared paper methods first, keeping public
artifacts, paper reconstructions and dynamic-graph extensions distinct.
V208's 24-request native replay calibration passed; it verifies the shared
data plane and stopping conditions, not paper scheduling or speculative
producer performance. V209 is the separately frozen ThunderAgent comparison.

Before a Full run with the new engine, retain all inherited CPU case identities
and run the bounded real GPU replay check. Do not rerun the full native capture.
Use the same V195 tokens, dependency graph, capacities, seeds and normal tails.

After the paper baselines, compare each concrete change against the preceding
Full, matched scheduler-only and strongest qualified paper baseline. Report:

- Identical request, prompt-token, output-token and workflow completion counts.
- Whole-run token throughput and workflow JCT with all normal tails retained.
- Sampled batch dependency waits, counting mixed batches only once.
- Restore-to-first-use delay, invalidated reservations and unused restored bytes.
- Repeated H2D, old-prefix recomputation and native allocation retries.
- Scheduler CPU cost and any loss of resident decode capacity.

Do not infer aggregate transfer savings from the sampled waits or count
deliberately deferred admission as saved waiting.
