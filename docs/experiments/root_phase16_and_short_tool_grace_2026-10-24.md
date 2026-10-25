# Shorter Root Phases and Short Tool Residency

The workload calibration keeps eighteen simultaneous, project-disjoint roots,
running/decode limits of 24, seed 21, a 256K context limit, and the child's
24-call warning / 32-call soft target. Roots receive a reminder at eight model
calls and a soft phase target at sixteen. A real completed delegated JOIN resets
the phase. The workflow's total work is not capped, unfinished tasks are not
declared complete, and the model still selects assignments, fanout and nesting.

This is a workload change and requires its own natural native capture, v195.
The capture is the native baseline. It must be reviewed for actual delegation,
normal completion, JOIN density, useful-state loss, and repeated prefill before
Full replay. A shorter budget alone does not establish a system speedup or task
correctness.

The previous v194 selected-queue reclamation candidate increased verified JOIN
prefetch bytes but did not improve the matched result over v193. The new
calibration retains the historical `waiting` reclamation policy.

## Observed Short Tool Gap

Queue-age inheritance does not pin KV. The CPU reproduction in
`tests/test_tool_continuity_native_return_gap.py` uses the real native FULL
radix tree and allocator. Native eviction between model completion and tool
reentry removes the device prefix even when age credit remains valid. A
temporary native pin preserves the prefix and makes the existing resident-only
continuation rule applicable. This is a mechanism reproduction, not a GPU
performance result.

## Optional Candidate

`BELIEFKV_TOOL_CONTINUITY_GRACE_MS` defaults to zero. The separately declared
candidate requests 100 ms. It applies only when both custom KV management and
tool continuity are enabled.

- At most one completed context may retain a native lock.
- Acquisition requires a native tool-opening token, remaining continuation
  credit, a settled resident FULL leaf, and another unfinished request.
- Acquisition uses the latest generation-checked session anchor. Native
  lock receipts remain paired even if a radix prefix splits.
- Ready JOINs, observed JOIN EOS, critical parent demand, allocation pressure,
  decode reserve pressure and an idle GPU revoke protection.
- Expiry, cancellation, context/session changes, causal mirror failure and
  shutdown also revoke protection.
- The successor takes its native admission lock before the optional pin is
  released. Ordinary unrelated admissions do not acquire extra privileges.
- Existing four-request / 1024-output-token continuation budgets and JOIN
  precedence remain in effect.
- The candidate starts no H2D or D2H and introduces no worker thread, text
  decoder or tool-duration model.

The launcher verifies the effective constructor configuration on CPU and the
observed runtime configuration during a controlled experiment.

## CPU Cost Evidence

`experiments/reports/v3678_tool_residency_grace_native_cpu_units_fixed_20261024.json`
measures real CPU native locks and runtime maintenance, with copy-queue
stand-ins. All 201 samples per case are retained after twenty warmups; order
alternates and normal GC remains enabled.

| Operation | Mean CPU time |
| --- | ---: |
| Release and reacquire the native pin | 12.86 us |
| Scheduler maintenance, option disabled | 1.44 us |
| Scheduler maintenance, no pin held | 1.73 us |
| Scheduler maintenance, pin held | 6.56 us |

There are no new transfer operations and the final native lock count is zero.
These small-tree CPU measurements do not establish GPU throughput or full
production-path overhead. The earlier v3677 output mislabeled millisecond
samples as microseconds and must not be used; v3678 corrects the units.

## Acceptance

Use identical captured tokens, causal dependencies, stopping conditions, pool
capacity and seeds when comparing Full with the natural native capture.
Retain normal long tails. Review useful bytes and actual first-service evidence
for JOIN prefetch, tool prefetch and selected-request restore separately; bytes
alone are not an end-to-end benefit.

The ongoing goal is at least a 10% mean JCT reduction and a 10% whole-run output
throughput increase on matched work, followed by the strongest simple scheduling
baseline and repeats. Current evidence has not met that goal. If the new
candidate adds pressure or repeated restore without a measured benefit, retain
the negative result and disable it.
