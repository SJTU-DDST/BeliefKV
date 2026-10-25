# Selected Consumer Preload During Scheduling Cooldown

The current dispatcher requires four ordinary admissions before it may preload
even an already selected next consumer. This is separate from the existing
ordering rule that bounds restored-request priority.

`BELIEFKV_FRONTIER_RESTORE_ORDINARY_QUOTA` keeps the historical default of four.
The explicit zero candidate removes only the preload start gate. It does not
spend scheduling credit, bypass native admission, change queue order directly,
or expand cold donor permissions. One live consumer, JOIN/EOS precedence,
identity revalidation, request slots, decode/input reserves, pending-transfer
checks and existing probe/reclaim backoffs remain in force.

The CPU reproduction issues the same valid next-consumer transfer with zero
scheduling credit, while retaining that credit at zero. Repeated calls keep one
consumer and do not enqueue duplicate DMA. Critical and capacity guards remain
effective.

## Cost Evidence

`experiments/reports/v3705_frontier_preload_quota_cpu_20261024.json` measures
the full CPU planning, capture, validation, enqueue and ledger paths with scalar
native stand-ins. It retains 101 samples after twenty warmups for each case.

For four and 64 extents, positive planning takes approximately 369 us and
2063 us median CPU time. New-context skips take 15-19 us, resident skips
34-41 us, and the constructed no-capacity probes 169-474 us. A pending selected
consumer exits in about 5-7 us.

The old start gate exits in about 1.5-2 us and submits nothing in these
zero-credit positive cases. Removing it therefore has a real CPU cost. The
existing pacing and backoffs matter; this is not a claim that every new probe
is free or that transfer bytes translate directly into JCT savings.

Keep the default unchanged until a successful matched GPU run establishes
useful first-service coverage and a net JCT/throughput benefit. Neither these
stand-ins nor the interrupted v197/v199 runs establish that benefit.
