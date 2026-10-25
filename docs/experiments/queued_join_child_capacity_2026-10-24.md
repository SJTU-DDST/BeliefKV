# Deferred Queued JOIN Child Capacity

The proposed last-child relaxation was withdrawn before a GPU registration.
A ready Host-backed child is not intrinsically cold: freeing its HBM can
create another H2D without improving the execution order. CPU-safe release
alone does not justify that exchange. The implementation retains its original
last-child protection. The next candidate uses `waiting` donors for ordinary
execution restore and merges tool successors into that selection path.

During an ordinary selected-consumer recovery, scheduling importance must not
give every unstarted last child an unconditional device-residency exemption.
The previous queued-donor validator rejected a
request solely because it was the last unfinished member of a parent's JOIN,
even when it was an unstarted queue tail with a settled Host copy.

The native CPU reproduction has one free token, a four-token backed child
prefix and a locked 59-token active request. The selected consumer needs five
free tokens. The legacy validator rejects that child and releases nothing.
Removing only the last-child veto releases four tokens, preserves the Host
copy and session anchors, leaves the active lock intact, and fits the consumer.
No D2H or H2D is created by reclamation.

The child remains queued in `RUNNING_LLM`; its parent remains blocked on its
JOIN. This reclaims an immutable cache copy before service, not an active
request's mutable state. Scheduling priority is unchanged. The actual queue
head, selected consumer and ready JOIN parents remain excluded; native
allocation, lock, Host-copy, session-generation and DMA guards remain required.
The override exists only inside the live ordinary selection's owner-validator
scope and is revalidated at each native owner check. Critical-parent recovery
retains its original last-child dependency protection.

The first relaxation's focused regression passed ninety cases. The corrected legacy reproduction
is `v3726_queued_join_child_valid_reproduction_cpu_20261024.xml`: the available
capacity case fails and six protection cases pass. The repaired path passes
all seven new cases. Preserve the earlier v3725 report, which included an
incomplete ready-JOIN fixture, but do not use its aggregate as the reproduction
basis. These passes did not establish a scheduling or migration benefit.

The first global relaxation passed the focused checks but failed two inherited
critical-parent dependency cases in v3728. Preserve that failure. The corrected
candidate scoped the relaxation to a live ordinary selected consumer, but was
not fully qualified and was withdrawn.

This removes a causal check from the donor path. It adds no queue traversal,
predictor, worker, timeout or residency privilege. Further native reclamation
and subsequent restoration still have real costs. The CPU result establishes
available capacity and preserved guards, not net workflow performance.

V203 finished with the original last-child protection intact. Its larger
native extent scan did not establish an improvement over v202. No v204
registration was started from the withdrawn prototype. Retain the failed CPU
reports and the historical experimental configuration.
