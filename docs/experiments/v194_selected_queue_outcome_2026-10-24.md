# V194 Selected Queue Outcome

The candidate is not accepted as an improvement over v193.

| Arm | Mean JCT (s) | Whole-run output tokens/s |
| --- | ---: | ---: |
| V187 natural native | 1625.303836 | 197.717444 |
| V193 Full | 1528.754866 | 205.913031 |
| V194 Full | 1542.165385 | 204.716223 |

V194 reduced native mean JCT by 5.1153% and increased native throughput by
3.5398%. Against v193, mean JCT increased 0.8772% and throughput decreased
0.5812%. These are single-run comparisons without a repetition claim.
The working 10% JCT/throughput targets remain unproven.

Each arm executes 1,963 requests, 120,629,483 prompt tokens and 394,899 output
tokens. All 18 workflows and normal tails are retained; no quality exclusions
were applied. Independent task correctness has not been graded.

## Mechanism Evidence

The selected-request path had 18 consumers; 16 reached native selection while
its bounded priority was retained. It transferred 27,108,409,344 FULL bytes,
of which 26,424,999,936 have verified first-use evidence. V193 had 14 targets,
four timely selections, and 24,911,216,640 verified reused FULL bytes. A timely
selection count is not the complete reuse count or saved JCT.

Sixteen of 30 JOIN waits have verified pre-completion FULL reuse in v194,
totaling 88,538,578,944 bytes. V193 had 11, totaling 68,764,434,432 bytes.
V194 issued no tool-prefetch commands. Its observed tool funnel contains two
waits with positive plans, both before the selected start window; 237 waits
have an observed no-Host-backed-missing-copy reason. Reasons can overlap.

Total v194 H2D was 7,603,697,713,152 bytes with 244.682 seconds of summed
stream-event duration; v193 had 7,767,189,258,240 bytes and 249.732 seconds.
These are neither exclusive PCIe wall occupancy nor a saved-JCT estimate.
The modest prefetch share does not cover the bulk of native restoration.

Old served same-session common-input prefill was zero on both sides. V194
actual input prefill exceeded native by 0.2290%; large old-prefix recomputation
does not explain this result. At fixed output work, decode forward counts
were 54,109 native, 55,146 v193, and 56,022 v194. Changed batch formation is a
concrete concern; it is not a causal attribution of the performance change.

## Collection Integrity

Application code was frozen at `7a5667ed2a4b9dc40c8c4aa493a2ed4b7dbf9e1d`;
the engine tree remained `58424f5fcc43f9f2d8ec09a38758017e41b92bc7`.
The inherited CPU scope passed all 5,995 cases, including 35 new cases.

The completed arm observed two fresh idle telemetry snapshots before cleanup,
with zero pending requests/batches, queue backlog, dropped or failed records,
and no writer error. The strict Full JCT path audit passed. All owned GPU
processes exited. Keep original v193 telemetry and its failed strict audit.

## Next Workload Calibration

V187 roots produced 863 requests across 46 trace-level direct-work phases.
Phase length P50 was 16 calls, nearest-rank P90 was 41, and 14 phases exceeded
24 calls. The new optional profile warns at 8 root calls and targets 16, with
child budgets unchanged at 24/32 and resets only after completed JOINs.
The model retains assignment, fanout, round and nesting choices.

Collect a matching natural native trajectory before Full replay. Inspect
actual delegation/JOIN density, completion and useful-state pressure first.
Use the historical `waiting` reclaim policy, retaining CPU optimizations;
do not promote the negative selected-queue candidate or begin broad ablations.

Authoritative receipts:

- `/home/longhao/experiment/ops/v194_qwen3_coder_18project_root_phase_selected_queue_full_20261024.registration.json`
- `/home/longhao/experiment/ops/v194_recomputation_review_20261024/finished.json`
- `/home/longhao/experiment/ops/v194_recomputation_review_20261024/full_jct_paths.json`
- `/home/longhao/experiment/ops/v3665_v187_root_phase_characterization_20261024.json`
