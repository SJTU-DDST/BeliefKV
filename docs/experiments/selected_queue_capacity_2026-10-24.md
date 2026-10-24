# Selected Queue Capacity Candidate

The matching workload is the v187 natural native capture: 18 simultaneous
workflows from 18 original projects, running/decode cap 24, seed 21, and 256K
context. Root direct-work phases warn after 16 calls and target 24 calls, with
reset only after an actual completed JOIN. Child invocations warn at 24 calls
and target 32. These are soft budgets; no truncation or fixed delegation graph
is introduced. This capture has 30 JOINs and two nested spawns.

V193 recovered 68,764,434,432 FULL bytes before completion with verified use
across 11 JOIN episodes. Ordinary execution-frontier restore transferred
32,741,818,368 FULL bytes across 14 targets; eight targets have evidence of
some use, totaling 24,911,216,640 bytes. Four targets were selected while the
bounded selection was retained; that counter is not the total use count.

V193 mean JCT was 1528.754866 seconds versus native 1625.303836 seconds
(5.9404% lower). Whole-run output throughput was 205.913031 versus
197.717444 tokens/s (4.1451% higher). Each side has 1,963 requests,
120,629,483 prompt tokens and 394,899 output tokens, retaining normal tails.
These results do not meet the working 10% JCT/throughput targets and have not
established benefit over the strongest scheduling control.

## Capacity Transaction

The historical `waiting` policy cannot use a queued unstarted owner as an
ordinary donor even though background prepare may already have backed its KV.
The separately declared `selected_queue` policy adds bounded authority:

- Only the actual selected consumer can initiate the transaction.
- Inspect at most 16 queue-tail positions and 16 native physical candidates.
- Keep the raw queue head, selected request and ready JOIN dependencies.
- Revalidate request allocation, generation, active execution, output, session,
  context epoch and queue membership at native owner checks.
- Keep native Host-copy, locks and pending-DMA checks; no new D2H is issued.
- End authority at transaction exit, selection expiry or new JOIN/EOS evidence.
- Require free capacity for the complete safe input checkpoint and native
  reserve before ordinary enqueue. A small fitting extent alone is insufficient.
- Native admission still decides request rows, full demand and compute budgets.

The transaction probes at most once per 200 ms, keeps one selected consumer
for at most 1500 ms after enqueue, and yields to JOIN/EOS. The existing v193
configuration and frozen engine/application trees remain historical evidence.

## Tool Evidence

V193 issued zero tool-prefetch commands despite enabling the policy. Its
counters include 414 no-target checks, 355 outside-window checks, and 240
unsupported conditional-tail checks. These counters can revisit episodes and
are not an exclusive partition. Fourteen tool-prepare node commands were issued.
The native tool-round P50 was 1.429945 ms and P90 130.461028 ms. These data do
not prove a benefit for speculative tool prefetch. Tools often finish before
their successor's long queue wait; selected-request restore can cover those
successors without treating the short tool itself as a long prefetch window.

Compare the candidate against the same v187 source with unchanged captured
tokens, dependencies, stopping conditions, capacity and seeds. Do not collect
another complete native replay. Transfer bytes alone are not saved JCT.
