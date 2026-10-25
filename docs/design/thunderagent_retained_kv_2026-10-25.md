# Retained Agent Eligibility With KV Migration

V215 completes the shared ThunderAgent core with immediate observed-blocking
yields. Its mean JCT is 1139.94s and whole-run throughput is 235.72 output
tokens/s. Against V209's 1091.94s and 230.65 tokens/s, throughput improves but
JCT regresses. Retain this negative result and its complete work/normal tails.

`BELIEFKV_AGENT_ADMISSION=thunderagent_retained` is the next separate candidate.
It continues to inherit the same public reference scheduling core. It changes
the Full extensions, not the source trajectory or sampling.

## Separate Eligibility From Physical Residency

Long-tool and JOIN waits can still back up and offload physical KV. This does
not automatically terminate logical agent eligibility. When its next call
arrives, an eligible agent keeps its reference continuation status even if its
physical state needs restoration. Eligibility is not a native page lock.

The periodic reference controller still handles context growth and acting
capacity. At ordinary blocking events, retain the logical owner while another
eligible query or in-flight call can compute. Avoid replacing a healthy
working set solely because one of its tools has been outstanding for a second.

## Bounded Idle Handover

If there is no eligible pending query or in-flight call, a real paused ready
request can borrow logical budget from known blocked owners. Reuse reference
continuation groups and size ordering for the recipient, and acting/smallest
ordering for donors. Donors must be non-running, noncritical and outside a live
restore lease. Verify that the whole target can fit before pausing any donor.

Short-tool intervals with no known blocked owner do not trigger this path.
This avoids a speculative timeout replacing the reference policy. Known
dynamic JOIN stalls can still release parent budget for a pending child, and
the GPU need not remain idle behind a blocked active set.

Native ACKs, locks and allocation must independently establish physical HBM
availability. Logical lending does not credit freed pages or cancel DMA.

## Recovery And Fairness

A parent can reserve EOS recovery without losing its cross-call eligibility at
lease expiry. A previously inactive parent can warm only with existing free
logical room. Both then follow the same normal agent eligibility policy.
The 1500ms deadline clears the recovery reservation and a still-blocked owner's
temporary critical privilege; it does not by itself pause the agent or evict
its KV. JOIN recovery cannot
evict a ready peer just to gain logical room. Existing native admission consumes
the recovery opportunity for that same agent. Physical native locks and
allocation remain authoritative; logical eligibility does not pin every page.

The recovery reservation is limited to JOIN/EOS. Disable ordinary
execution-frontier restore, frontier cold reclamation, execution handoff and
speculative tool prefetch in this candidate. Reject an inconsistent frontier or
handoff configuration before serving. Ordinary requests keep their agent
eligibility across calls and use native demand restoration if their KV was
offloaded; they create no additional 1500ms recovery consumer. Physical offload,
incremental backup and JOIN/EOS prefetch remain independent of this simplification.
Publish the reservation scope and zero ordinary reservation duration, and verify
both in the experiment's final runtime observation.

The 1800-second starvation escape admits at most one old waiter per control
tick. Aging uses the later of request arrival and the latest pause timestamp.
Otherwise all old waiters can repeatedly replace one another within a single
tick or immediately reactivate the just-paused victim. This is separate from
ordinary request priority aging.

Publish active and blocked-active invocation IDs in the existing one-second
status record. This adds no per-token I/O and allows later transfer/admission
audits to distinguish logical ownership from observed residency. Report actual
retention state and control periods, and reject an experiment whose observed
policy differs from its declared mode.

## Validation

Check reference equivalence without blocking/recovery extensions, blocked
retention during useful peer compute, observed idle JOIN liveness, reservation
expiry, safe native backup/reclaim while eligibility remains, and randomized
boundary transitions. Retain the complete inherited CPU test scope.

Use the same V195 work, capacities, seed and native baseline. Evaluate complete
JCT and throughput against V209 and V215; fewer migrations or a passing
eligibility test alone does not establish a performance gain.
