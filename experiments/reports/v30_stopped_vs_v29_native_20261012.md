# V30 Stopped Predictive vs V29 Native

Observed on 2026-10-12 in Asia/Shanghai. Development comparison only.

V30 was administratively stopped at 12:49:22.089 CST. The cutoff receipt
preserves 80 completed workflows, one incomplete workflow, and 75 created
but unfinished workflows out of 156. The unfinished workflows are censored.
Do not compare completed-only V30 JCT or its stop time with full-run V29 JCT
or makespan. V29 native eventually completed all 156 workflows.

V30 used commit fca7ad3; V29 native used 92e47d7. Both used the same engine
patch 1c3c7fc4 and physical capacities. V30 uses a 2s prefetch lead. This is
a fixed historical native reference, with different realized live demand
and overlapping development analysis; it is not an isolated policy effect.
The FULL-only reclaim and EOS evidence lifetime followups were not deployed
to V30.

## Common Fully Observed Window

Use 0-3600s from each arm's first GPU-monitor sample. V30's origin is
11:48:06.529 CST; V29 native's origin is 09:08:07.476 CST.
The generated busy report also retains a 3600-3780s diagnostic window.
V30 does not cover that trailing window: exclude its rates from comparison.

| Metric | V30 predictive | V29 native |
| --- | ---: | ---: |
| Actual decode token/s | 1118.33 | 1158.58 |
| GPU utilization | 85.50% | 87.73% |
| Decode time-weighted batch | 43.79 | 46.11 |
| All H2D, FULL plus Mamba | 2.626 TB | 2.867 TB |
| All D2H, including PREPARE | 1.371 TB | 1.926 TB |
| H2D stream time, cumulative | 88.38s | 99.01s |
| H2D submit-to-ACK, cumulative | 677.96s | 511.95s |
| D2H stream time, cumulative | 114.66s | 249.69s |

Decode throughput is 3.474% lower; H2D and D2H bytes decrease by 8.405%
and 28.831%. Submit-to-ACK sums increase by 32.426%. Transfer sums are
neither exposed critical-path waiting nor quantities removable from JCT.

| Window, seconds | V30 decode token/s | Native decode token/s | Change |
| --- | ---: | ---: | ---: |
| 300-600 | 1144.48 | 1140.88 | +0.31% |
| 600-1200 | 1051.25 | 1238.46 | -15.12% |
| 1200-1800 | 1099.06 | 1076.21 | +2.12% |
| 1800-2400 | 1232.03 | 1198.00 | +2.84% |
| 2400-3000 | 1190.08 | 1232.27 | -3.42% |
| 3000-3600 | 1084.44 | 1152.59 | -5.91% |

The 600-1200s deficit occurs with weighted batches 47.34/47.43 and GPU
utilization 81.94%/92.54%. It cannot be explained solely by smaller batches.
Late drain, context differences and local analysis prevent isolated causal
attribution; aggregate migration reductions do not establish a speedup.

## Stopped-Run Prefetch Lifecycle

The following counts cover the preserved V30 input prefixes, not a full
workload or the entire V29 run.

| Source | Independent early FULL reuse events | Confirmed early reused FULL |
| --- | ---: | ---: |
| JOIN | 26 | 0.512061440 GB |
| Tool | 22 | 0.992419840 GB |

Combined early reused FULL is 1.504481280 GB. No JOIN/tool transfer appears
in the partial trailing window, so the bytes are covered by the common
3600s transfer window. They are about 0.115% of its 1.310 TB FULL H2D.
Demand handoff confirms 105.734 GB FULL reuse and remains separate.

Of 121 JOINs with observed completion, 101 have a positive FULL plan before
completion, 96 enter the start window, and 29 submit before completion.
For tools the corresponding counts are 10293, 845, 29 and 28. Keep the
56 JOINs and two tool episodes with unobserved completion boundaries unknown.
Capacity-budget observations occur in 85 JOINs and 1799 tool episodes;
47 JOINs record unavailable service progress and 66 record a busy admission
budget. Reasons overlap and are not mutually exclusive loss attribution.

JOIN ACK-to-first-service P50/P90 is 3.934/18.207s. Tool P50/P90 is
0.930/39.959s. Last child RETURN observation to first parent service
P50/P90 is 0.330/0.791s across 122 observed parent services.
PREPARE has 1078 ACK commands and 208 observed subsequent restores;
absence of a restore in this censored run is not proof of waste.

The last preserved timing snapshot contains 407.341s of instrumented
exclusive Python wall intervals across 3680.755s. The largest phases
are reentry inspection 64.688s, tool prefetch 36.828s, control drain
31.630s and admission planning 31.419s. Native has no matching control
scope, so this is not a measured net overhead or removable JCT.

The incomplete workflow is pydata__xarray-6744. Its final root response
has finish_reason=length, 8192 output tokens, and repeated text; both
children returned and its JOIN was satisfied. The cutoff receipt observed
this result before the administrative stop.

## Evidence

- v30_stopped_vs_v29_native_busy_20261012.json
- v30_stopped_prefetch_lifecycle_20261012_summary.json
- v30_stopped_prefetch_lifecycle_20261012.json.gz
- v130_v30_development_stop_20261012.json
- v129_v30_v29_native_300_600_20261012.json

V30 has not demonstrated the desired native-relative performance gain.
Continue reducing control costs and converting valid positive plans into
timely, actually consumed restoration; validate the existing followups
in a fresh predictive development run against the fixed native reference.
