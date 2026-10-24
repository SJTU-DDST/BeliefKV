# V177 Host Losses and Replay Comparability

Date: 2026-10-24. CPU-only audit; no new GPU experiment.

## Measured Old-Input Work

All three arms execute the same 2369 requests, 151307687 prompt tokens and
531903 output tokens across 24 workflows. No ordinary tail is excluded.
The following measure counts actual prefill of previously served same-session
common input, excluding initial cold input and previously generated output.
It is a lower bound on repeated work, not the fraction of all input recomputed.

| Metric | V174 natural native | V176 replay Full | V177 replay Full |
| --- | ---: | ---: | ---: |
| Old common input reprocessed | 696174 | 639935 | 779918 |
| Change relative to native | reference | -8.078% | +12.029% |
| Actual logical input prefill | 4255360 | 4223186 | 4363160 |
| Requests with old-input loss | 9 | 9 | 9 |

V177 adds 83744 old-input tokens relative to native and 139983 relative to
V176. Actual total prefill increases 2.533% relative to native, not 12%.
Ten request identities occur in the union of affected requests.

| Resuming parent project | Native | V176 | V177 |
| --- | ---: | ---: | ---: |
| system_automation_hard_062 | 130224 | 4638 | 142512 |
| desktop_media_medium_092 | 0 | 19871 | 95702 |
| data_warehouse_medium_012 | 87166 | 57866 | 21002 |
| mobile_social_medium_022 | 13864 | 74822 | 74822 |
| desktop_media_medium_020 | 110531 | 110531 | 98249 |
| data_lake_medium_050 | 65187 | 85588 | 61012 |
| ml_inference_hard_088 | 2583 | 0 | 0 |
| fintech_trading_medium_066 | 105609 | 105609 | 105609 |
| blockchain_nft_medium_035 | 61848 | 61848 | 61848 |
| game_simulation_medium_033 | 119162 | 119162 | 119162 |

The first two parents add 137874 and 75831 tokens versus V176, respectively.
Improvements elsewhere offset 73722, leaving the 139983-token net increase.
This concentration explains the sign change in the aggregate; it is not
evidence of a uniform degradation across all requests.

## Host Eviction Evidence

The initial request-path association found no prior match-path node IDs for
these cases. That absence is missing attribution, not proof of no eviction.
The follow-up instead uses native `host_block_reaccess_attributed` records:
run-keyed exact namespace/prefix HMACs link the request to the recorded first
and last Host eviction IDs. The auditor validates those identities against
the actual eviction records, clips intervals to uncached old input and unions
overlapping spans rather than summing repeated eviction lifetimes.

For V177, all 779918 measured old-input tokens have associated prior Host
eviction coverage. The latest eviction of those spans occurs after the prior
service and during the observed client JOIN wait. This establishes eviction
of useful-prefix Host copies before JOIN satisfaction and subsequent old-input
recomputation, not the time at which both-tier coverage was finally lost.
Client JOIN timestamps are clock-aligned; they are not proof of the scheduler's
precise event-delivery time.

For the automation parent, V176 still has 137874 Host-hit tokens on resumption;
V177 has zero. Both have 547 device-hit tokens. Of V177's 142512 reprocessed
old tokens, 138957 belong to spans with repeated Host evictions since the
prior reaccess. For desktop092, V176 has 85123 Host-hit tokens and V177 zero;
V177 retains 9844 device-hit tokens and reprocesses 95702 old tokens.
Repeated Host eviction covers 95627 of those tokens.

This evidence does not identify which capacity allocation triggered each
eviction or whether an HBM copy existed at that instant. The final loss can
follow multiple tier changes; a Host-eviction record alone is not proof that
the sole remaining copy was deleted at that moment.

The duplicate-first Host policy is shared with the current native control.
The frozen native and V177 files are byte-identical:

- `unified_cache/unified_tree_core.py`: SHA256
  `df5063dcdd73fe486b77dd915ffc80454f9d0a76f71f81b59d097ce3e088cad4`.
- `unified_cache/components/full.py`: SHA256
  `55ea77fc80bad814f609082437016c8800dba63cb2b68d860c45dd017b8fbc2d`.

`drive_host_eviction` reclaims eligible FULL Host duplicates first under
write-back, then evicts Host leaves. The session-reference policy also
prefers unreferenced sessions before referenced ones. These are not solely
Full-specific advantages. Full's explicit terminal-child private-suffix
cleanup is active: 35 children cumulatively release 73.084GB of device state
and 40.943GB of Host state. Cumulative release is not simultaneously free
capacity and does not protect all live waiting parents.

| Recorded FULL Host eviction | Native | V176 | V177 |
| --- | ---: | ---: | ---: |
| Events | 75507 | 41784 | 44413 |
| Cumulative bytes | 9.541TB | 5.986TB | 6.279TB |

These totals include repeated lifetimes; they are neither unique lost bytes
nor PCIe traffic. Full evicts less in total while V177 loses more future-used
old input. Fewer aggregate evictions cannot establish better retention of
valuable prefixes. V177 Host occupancy has a 99.819% median, and background
PREPARE records 4519 Host-capacity skips.

The V177 code repair changes surviving-prefix discovery, not Host eviction
ordering. It can change which recovery is issued and subsequent placement
and execution timing. One run of each version does not isolate that timing
effect from run variation. Do not undo a validated discovery correction on
the basis of this aggregate result.

The remaining policy issue is joint management of live-parent Host retention,
incremental backup and HBM reclamation under capacity pressure. Duplicate
reclamation must not be confused with a guarantee that a later HBM eviction
will still find a ready Host copy. Current evidence does not attribute each
loss to that particular sequence, so such attribution remains an open check.

## Replay Comparability

Replay fixes prompt/output token sequences, causal dependencies, stop reasons
and recorded external delay intervals. Requests execute real model forwards,
native cache lookup/eviction, allocation and DMA. Successors are released from
the current run's actual native completion, not the source run's old absolute
completion timestamp. Source recomputation, cache hits and scheduling order
are not replay inputs. The different old-input counts directly demonstrate
that cache outcomes remain independent.

All 2274 source TOOL_START events are preserved in both Full replays with
identical event IDs and attributes. Online tool estimates are regenerated
from these causal runtime signals; discarded diagnostic shadow records do not
establish that a predictor input was omitted.

There is, however, a material request-entry mismatch. The natural native
source uses `/v1/chat/completions` with chat conversion/template processing.
`CausalHTTPBackend.request_payload` sends captured `input_ids` to `/generate`
with `stream=False`. Native's measured received timestamp is set before chat
conversion in `OpenAIServingBase.handle_request`; tokenized time is recorded
when the tokenized engine request is created. This interval includes more
than the tokenizer kernel.

| Ingress stage P50 | Native | V176 | V177 |
| --- | ---: | ---: | ---: |
| API received to tokenized request | 47.949ms | 2.219ms | 2.201ms |
| Client submit to native queue registration | 112.292ms | 72.338ms | 71.147ms |
| Dispatch to native queue registration | 28.795ms | 27.050ms | 26.970ms |

Across 2369 requests, API-received-to-tokenized intervals sum to 120.017s
in native and 9.526s in V177, a difference of 110.491s. These concurrent
intervals must not be subtracted from mean JCT or makespan. They can alter
queue readiness and batch formation, so a simple per-request correction does
not align the experiments.

Replay has corpus/JSON/event and sampling-hook costs. The V175 microbenchmark
measures about 0.004-0.010ms extra per sampling step for sampled batch sizes;
this is not an end-to-end replay error bound. Conversely, bypassing chat
processing and replacing real tools by recorded waits can favor replay.
The trace does not establish the net end-to-end bias or its upper bound.

The V177 observations, 2.701% lower mean JCT and 3.200% higher whole-run
throughput, therefore do not by themselves establish an aligned end-to-end
custom KV-management gain. Exact-work checks still pass. V176 and V177 share
the same replay entry, so this entry mismatch does not itself explain their
difference from each other.

No complete Qwen3-Coder native replay is performed. Registration explicitly
labels the replay-error basis as historical calibration not remeasured on
this source. Do not transfer an old-model calibration into an error bound
for these runs or schedule another full native replay contrary to the user
instruction. Qualify an equivalent request-entry and observation path before
accepting a future natural-native versus replay-Full gain. Keep the current
runs as exact-work diagnostic evidence.

A single native-selected trajectory also limits generality: replay deliberately
removes scheduling-induced semantic workload variation. It is appropriate
for controlled fixed-work analysis, but cannot show how natural model-selected
workflow trajectories vary under each scheduler.

## Evidence

Verified CPU report:

`/home/longhao/experiment/ops/v177_recomputation_review_20261024/losses_and_replay_bias_verified_20261024.json`

Audit source:

`/home/longhao/experiment/ops/audit_v177_losses_and_replay_20261024.py`

The report retains sealed input prefixes, input digests, paired ingress
distributions and individual HMAC/eviction-ID evidence. The earlier
path-only audit is superseded for eviction attribution, not for its measured
request work or ingress data. No serving source or frozen run was changed.
