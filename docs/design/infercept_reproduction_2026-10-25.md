# InferCept Paper Reproduction Components

The pinned source is WukLab/InferCept at
`3a1a4dac37cb569e767bc4a95ef8ed0995a13b99`. The local paper is
`ops/paper_baselines_20261024/papers/infercept_icml2024.pdf`.
This is a paper reimplementation in progress, not a completed author-code
performance reproduction.

The public scheduler's `_get_max_ragged_batch` explicitly retains a TODO and
fixed 512/999-token choices. Its partial-swap path uses remaining API duration
ordering. These are not substituted for the paper's profiled optimization and
min-waste policy.

## Budget Solver

Section 4.1 gives a forward-derived total transfer limit N and the constraints
on incoming tokens i, outgoing tokens o, new query tokens q, free GPU tokens G
and free Host tokens H:

```text
i + o <= N
o <= H + i
i + q <= G + o
```

`beliefkv.policy.infercept.iteration_budget` maximizes q subject to measured
query demand and available swap inventories. Among solutions with equal q,
it maximizes incoming FCFS restore work, then outgoing work. This lexicographic
tie break is a declared implementation choice because the paper does not
specify one. Transfers use complete native pages while new query work remains
token granular.

For page size P, round transfer inventories and N/H down to pages. Maximum
query work is `q = min(query_demand, G + P * min(out_pages, N_pages, H_pages))`.
With `slack = floor((G-q)/P)`, the maximum incoming pages are
`min(in_pages, N_pages, out_pages+slack, floor((N_pages+slack)/2))`.
The maximum outgoing pages then follow from the remaining transfer and Host
budgets. This avoids an iterative search in the future serving loop.

The plan assumes move semantics: incoming KV eventually frees its former Host
slots. Existing HiCache restore can retain Host duplicates. Its adapter must
implement safe post-ACK Host release before taking this credit; planning alone
must never free or promise native memory. Aggregate feasibility also does not
prove a transfer can start when both tiers have zero free pages. Scratch
capacity or actual native capacity release remains necessary.

Small-state tests exhaustively compare every result to an independent integer
enumeration, including page sizes 1/2/3/16. Three fixed seeds check another
3000 larger budgets and all constraints.

## Resume Queues And Duration

Section 4.3's query and swap queues retain each sequential agent's original
arrival across tool returns and retracted attempts. New or discarded requests
consume the query budget in FCFS chunks; resumed Host requests consume the
independent swap-in budget in FCFS chunks. Partly restored requests do not
consume query work until their swap ACKs complete. Planning is advisory and
does not advance either queue.

`ResumeQueues` exposes separate acknowledgement of actual query work and
actual completed swap chunks. Completed calls retain original agent age.
Only invocation retirement discards it. Invalid phase/count acknowledgements do
not advance the component. The physical adapter must deduplicate native receipts
and bind them to the current request before acknowledging work; this arithmetic
queue component does not establish receipt identity or session generation.

Section 4.4's unprofiled interception estimate is observed elapsed time from
tool start, rather than a future tool finish or BeliefKV's long-tool predictor.
The existing min-waste action component remains separate from these queues.

## Remaining Runtime Work

Measure the model forward curve, saturation point and transfer bandwidth on
the shared model/hardware without reading future replay dependencies. Bind the
profile to model, KV bytes per token and native engine sources. Fill recompute
chunks according to saturation minus actual running queries.

Implement native partial backup, settled reclamation, partial Host moves,
discard and query resumption with generation/lock/DMA correctness. Physical
capacity, native row/batch admission, replay receipts and page ownership remain
authoritative. Isolate the arm from Full migration/priorities; do not enable it
until the complete inherited CPU scope and native path are qualified.

No GPU result, overlap gain, fairness improvement or full reproduction is
claimed from these components. Preserve the frozen V215 candidate while this
independent baseline work proceeds.
