# v197 Allocation Failure and Bounded Recovery

v197 is an invalid performance run. The server failed after ten completed
requests while allocating 4096 FULL tokens. Its error reported 2411 free
tokens and 119925 evictable tokens. The owned GPU processes were cleaned up.
Keep the failed run and its original logs.

The CPU reproduction in `tests/test_prepare_ack_allocation.py` does not enable
short-tool residency. With four free tokens and a pending background backup
of a 32-token cold prefix, an eight-token allocation fails on the original
engine. The diagnostic then reports 36 available-plus-evictable tokens.
Completing the same ACK before allocation makes the request succeed.

The native eviction walk skips the prefix while its D2H read lock is held.
The existing write-back completion fence releases that lock after the walk.
The allocator then attempts allocation without revisiting the newly evictable
prefix. More workflow concurrency is not needed to explain this failure.

The first retry implementation in v199 was also invalid: it inspected pending
writes after the first eviction walk. A different victim can synchronize both
its own write-back and the earlier PREPARE inside that walk. Its pending queue
is already empty when the walk ends, although newly unlocked cold leaves were
absent from its original heap. A second CPU reproduction retains a 32-token
pending prefix and a separate one-token victim. The old v199 engine frees the
one token, reports another 32 evictable tokens, and still fails an eight-token
allocation with five free tokens.

## Fix Scope

For caches with `_beliefkv_incremental_full_enabled is True`, check whether
eviction quotas remain unsatisfied after the existing completion fence and make
at most one additional native eviction pass. Reuse the original tracker and
allocator targets, so a satisfied request does not evict more data. The trigger
uses the actual remaining work rather than the timing of a pending-write probe.

The fix preserves native request locks and all host-copy and DMA checks. It
adds neither a new polling loop nor a new completion fence. Other native caches
do not perform the extra pass.

The fixed engine is a separate development tree. Historical engine snapshots,
the v195 natural native capture and the failed v197/v199 Full runs remain immutable.

## Source Compatibility

`beliefkv/experiments/prepare_ack_engine.py` admits a changed engine only when:

- The original source explicitly disabled the custom runtime and PREPARE.
- Exactly one production file differs from its recorded engine tree.
- The added statements have the exact custom flag and one-pass retry structure.
- Removing only those statements restores the complete original cache-file AST.

All other production files, including model kernels and sampling, must be
identical. The GPU calibration and controlled replay record both engine trees
and the compatibility evidence, and must use the same fixed engine. Arbitrary
engine changes are rejected.

The failed runs do not establish the short-tool candidate's performance.
After complete CPU qualification and a new source-specific GPU check, rerun
the same v195 tokens, dependencies, capacities and seeds. No additional full
native replay is required for this gated custom-path fix.
