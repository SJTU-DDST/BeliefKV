# V195 Shorter Root Phases

## Workload Contract

Eighteen simultaneous roots use eighteen distinct original projects. Running
and decode limits remain 24. The model is Qwen3-Coder-30B-A3B-Instruct, with a
262,144-token context limit, compression at 95%, a 16,384-token output limit,
and seed 21. The FULL Host pool is 200 GB. No subagent reuse is enabled.

Root direct-work phases receive a reminder after eight model calls and a soft
target of sixteen. Only a real completed delegated JOIN resets the phase.
There is no total workflow turn cap. Children retain the 24-call reminder and
32-call soft target. Active tools, JOINs, edits and validation may finish after
the target; unfinished work must not be declared successful.

Every root must perform at least one real delegation. The model chooses the
assignment, fanout, timing, further rounds and nesting. Six roots first
attempted an undelegated final return; the declared contract deferred those
returns and all six subsequently delegated. Preserve this intervention in the
source qualification. No guard-forced completion was needed. Independent task
correctness has not been graded.

## Captured Work

All eighteen workflows completed. The accepted source has 38 JOINs, including
one nested JOIN; eight roots have multiple delegation rounds.

There are 2,054 requests, 116,355,888 prompt tokens and 387,242 output tokens.
Roots issue 836 requests and children issue 1,218. Trace-level root phases
have a median of eleven calls, mean 15.2 and maximum 89. Child invocations have
a median of 32 calls, mean 32.05 and maximum 52. These observed segment lengths
are not hard middleware limits.

The context-length audit gives each agent equal weight after averaging its
served requests. Root mean history is 109,455 tokens, with median 115,578;
child mean history is 16,779 tokens, with median 17,008. Root peak context
median is 127,474 tokens. At 98,304 FULL bytes per token, the respective mean
logical KV sizes are approximately 10.02 GiB and 1.54 GiB. These are logical
context sizes, not unique physical allocations or an HBM residency census.

The previous 16/24-root-phase v187 source had 30 JOINs. The changed prompt
requires this new matching capture; a timing difference between those sources
is not a causal system speedup.

## Metric Boundary

Use the same workflow-start to workflow-end event boundary for native and
predictive comparison. Keep all normal tails.

| Measure | Native event boundary | Native client harness |
| --- | ---: | ---: |
| Mean workflow JCT (s) | 1509.740834 | 1511.238320 |
| Whole-run duration (s) | 1922.423287 | 1928.576320 |
| Whole-run output tokens/s | 201.434306 | 200.791639 |

The harness columns retain surrounding client work. They must not be mixed
with predictive event-boundary measurements. The current working targets of
10% lower mean JCT and 10% higher whole-run throughput correspond to event
metrics of at most 1358.766751 seconds and at least 221.577736 tokens/s.
These targets have not been established by this native capture.

The capture is the native baseline. Subsequent controls use exact captured
tokens, request dependencies, sessions, stopping conditions, capacities and
seeds through the existing pretokenized `/generate` replay. Do not run another
full native replay or require chat-ingress alignment.

## Migration Evidence

The device FULL pool holds 805,198 tokens, or 79,154,282,496 bytes. The Host
FULL pool holds 2,034,506 tokens, or 200,000,077,824 bytes, and approaches that
capacity. Actual prefill of previously served same-session common input is
zero. Total input prefill is 2,730,948 tokens, including 1,769,354 first-context
cold-input tokens. Occupancy alone must not be called expensive recomputation.

Native transfers include 832 H2D operations totaling 8,888,613,273,600 bytes,
with 279.164 seconds of summed stream-event duration. D2H totals
2,618,596,884,480 bytes and 59.805 seconds over 28,760 operations. These sums
are not exclusive PCIe occupancy or a JCT stall estimate.

Fifty-two sampled restore batches have median dependency wait 124.82 ms. Their
summed waits are 9,091.04 ms: 94.25 ms at layer zero and 8,996.79 ms in later
layers. Thirty-six sampled batches wait more than 50 ms in later layers.
Layerwise transfer therefore does not guarantee that all transfer after the
first layer is hidden. Do not extrapolate these sampled batch waits into an
entire-run saving or attribute a batch wait independently to every request.

Tool successors account for approximately 95.8% of logical Host-hit tokens;
JOIN successors account for approximately 3.6%. Tool duration median is
1.493 ms, while tool-successor queue-to-first-service median is 4,307.61 ms.
This distinguishes tool-window opportunities from restoration during the
later request queue. Logical Host-hit tokens are not exact physical H2D bytes.

## Receipts

- Source registration:
  `/home/longhao/experiment/ops/v195_qwen3_coder_18project_root_phase16_source_20261024.registration.json`
- Workload review:
  `/home/longhao/experiment/ops/v195_workload_review_20261024.json`
- Native prefill review:
  `/home/longhao/experiment/ops/v195_native_recomputation_20261024.json`
- Tool and JOIN reentry reviews:
  `/home/longhao/experiment/ops/v195_tool_reentry_review_20261024.json` and
  `/home/longhao/experiment/ops/v195_join_reentry_review_20261024.json`
- Agent context-length review:
  `/home/longhao/experiment/ops/v195_agent_context_lengths_20261024.json`
- First repaired standard Full:
  `/home/longhao/experiment/ops/v201_qwen3_coder_18project_prepare_target_full_20261024.registration.json`

The v197 and v199 interrupted Full runs are invalid performance evidence.
Preserve them and the target-based PREPARE-ACK allocation retry explanation in
`v197_prepare_ack_allocation_failure_2026-10-24.md`.
