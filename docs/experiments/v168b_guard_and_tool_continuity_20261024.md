# V168b guard audit and short-tool continuation bounds

## Source and scope

The completed native capture is
`experiments/raw/qwen3_coder_24project_native_source_20261024_v168b/native_capture`.
V3533 rejects the complete 24-workflow source because four workflows required
guard intervention. The other 20 workflows are separated below for diagnostics
only. This is not a new accepted cohort, a tail filter, or a Full/native
performance comparison. No formal Full replay is registered on this source.

Inputs:

| File | SHA256 |
| --- | --- |
| V3533 source selection report | `b7cab912a1b7db396a690b53110e7ef6941f361bcaf8c5bf0f10deab8e4f51a9` |
| V3523 native tool episode report | `39c2e8708ff8308defcdcf847ecb6bc685e6552fbb137a6a22aa26888c5e8e87` |
| Native server `runtime_events.sglang.jsonl` | `aaca185e66e6a2fd8c43dd32552464c4af580c698dea81abcfe7fd642f0bfab6` |
| Native server `request_tokens.jsonl` | `eb1878618db5b0bda1dcedd9fc9ff46b4ba2570d2f1cf861041d76fdb54d3876` |

The first two files are under `experiments/reports/` with the prefixes
`v3533_v168b_guarded_source_formal_replay_not_scheduled_20261024` and
`v3523_v168b_tool_reentry_residency_question_20261024`. Per-workflow results
were checked against the hashes retained in V3533.

## Four graph guards

All four interventions have `reason=graph_step_hard_limit_low`,
`graph_step=2017`, hard limit 2048, and reserve 32. Each activation has already
made 403 model calls and 403 tool calls when the next before-model hook
finalizes it. Graph steps count middleware/model/tool nodes, not LLM requests;
this graph advances about five steps per model/tool cycle. The guard-assisted
final response is an additional model call and does not establish natural
termination or task correctness.

| Project/task suffix | Guarded agent | Work before the repeated ending | Final consecutive completion-marker tool calls | Observed suffix time, lower bound |
| --- | --- | --- | ---: | ---: |
| `python_blockchain_nft_medium_035_feature_implementation_expert_01` | implementation child `d9a01fd300bc155a` | Governance/NFT airdrop implementation and tests | 362, alternating `write_todos` and `announce_completion_intent` | 2738.50 s |
| `python_data_lake_medium_050_cross_file_refactoring_hard_01` | root | Error-handling Strategy refactoring, imports and compilation checks | 333, completed todos and `execute` printing completion claims with `echo` | 3356.53 s |
| `python_desktop_media_medium_020_integration_testing_hard_01` | root | Plugin keyboard shortcut integration test implementation and validation attempts | 365, all `write_todos` marking completion | 3238.06 s |
| `python_web_cms_hard_074_integration_testing_expert_01` | root | Premium subscription integration test, repeated checks of the test file | 119, completed todos and completion `echo` commands | 937.85 s |

Each instance ID prepends `locobench__` to the suffix above. Inspect its
`sandbox_audit.jsonl`, `runtime_events.deepagents.jsonl`, and `trajectory.json`.
The NFT child is absent from the root-only trajectory; use its invocation ID
in runtime events and `child_stream_content.jsonl`.

Suffix counts exclude the guard-assisted final message. A root suffix includes
only completed todos and stdout-only completion `echo` calls; its start is the
first model result in the final consecutive suffix. The child suffix includes
only todo and completion-intent calls. Durations run from that result to the
guard event in the same client monotonic clock, so exclude the first call's
generation time. They describe repetitive output, not proof the earlier edits
or tests were correct.

The manifest explicitly sets `enforce_semantic_guard=false` and
`enforce_soft_graph_budget=false`. Detectors observe patterns early but do not
end the activation. For example, the NFT child reports no observable progress
at call 45; the desktop root does so at call 43. At finalization their
consecutive-no-progress counts are 363 and 365. The CMS root's counter is only
9 despite its long marker suffix: distinct command text/output can create
fresh shell/execution evidence keys. Exact repeated-input runs and that
counter alone do not establish a healthy trajectory.

The evidence establishes repeated completion-tool generation without a natural
stop. It does not prove why model decoding chooses another tool call. Keep
these interventions visible; do not silently force a successful report or
change source eligibility.

## Priority contract

Full keeps short-tool continuation enabled with workflow ordering and
resident-first. It promotes only an executable same-context/session successor,
after all tools finish within one second and the successor reaches the queue
within one second of native completion. Residency must already be proven by
the resident-first observation: a nonempty device checkpoint, no missing FULL
tokens, and no missing Mamba slots. Unknown and Host-only state do not receive
this locality boost.

The extra continuity preference stays below every executable satisfied-JOIN
parent in the inspected queue, even while JOIN's bounded priority quota is
replenishing. It cannot newly boost an earlier tool successor ahead of a later
ready JOIN. It can still reorder ordinary requests after the last ready
parent. Unsatisfied, stale or nonexecutable JOIN state does not create an
unconditional barrier. Existing causal classes, stage/prefetch priority, JOIN
fairness and aged-request budgets remain in effect.

The implementation reuses graph/key checks and existing residency observations.
There is no new prefix lookup, restore plan, transfer, KV lease, or per-decode
tracking. This preference does not reserve a running slot or guarantee
consecutive execution.

## Four-request bound

Four includes the ordinary initial admission and at most three successors.
After 1024 generated tokens have completed in a segment, no more continuations
are granted. This is not an active request's output limit or a GPU-time cap.
An ordinary admission replenishes bounded workflow credit; sibling agents
share it and cannot inherit one another's queue age.

An offline bound audit joins V3523's served episodes with native completion
timestamps/output counts and captured session identities. A structural link
requires a whole tool round no longer than 1000 ms, server completion-to-queue
gap in [0,1000] ms, adjacent context epochs, the same context/session/generation,
and no compaction or delegation in the tool round. There are 81 long-tool
rounds, 30 other completion-to-queue exclusions, and one unserved episode.

The 20 naturally eligible workflows have 1917 structural links in 131 chains.
Their request-chain length is P50 10, P90 37, maximum 77; preceding output is
P50 62 and P90 446 tokens. The four guarded workflows have 1615 links, with a
maximum chain of 338 requests, and must not determine the preferred burst size.

Partition each native chain into segments using a candidate request limit and
the same 1024 completed-token limit. The next request after either bound
starts another ordinary segment. Counts are:

| Request limit, including initial request | Structural continuation opportunities in eligible 20 | In guarded four |
| ---: | ---: | ---: |
| 2 | 971 | 812 |
| 3 | 1279 | 1079 |
| 4 | 1427 | 1164 |
| 8 | 1630 | 1231 |

These counts ignore admission-time residency, competing JOINs, causal classes,
shared workflow credit and changes in queue timing. They are neither actual
priority grants nor saved migrations/JCT. Increasing four to eight creates
203 additional structural opportunities in the eligible diagnostics, while
doubling the request burst ceiling. Retain four as a conservative initial
limit; the trace does not establish an optimal limit or a GPU benefit.

## CPU validation

- V3535: 177 focused policy/internal-control cases pass, including JOIN boost
  credits 0 through 4, ordering after a parent, a parent later in the queue,
  and unsatisfied JOIN state. Residency-reader call counts do not increase.
- V3537: all 5790 required cases pass, retaining every previous 5776 identity
  and adding 14 cases. V3538 seals the source hashes. The qualified native
  engine is first in PYTHONPATH; the real local tokenizer and cached CPU
  sandbox image are explicitly selected, with GPU visibility disabled.
- The actual Full constructor CPU preflight verifies workflow ordering,
  short continuity enabled, EOS JOIN, cold prepare and 1500 ms tool-prefetch
  priority. No GPU experiment is started.
- V3539: with 24 candidates and a ready successor, advisory inspection,
  planning and selection P50 is 252.158 microseconds disabled and 274.654
  enabled, a 22.496 microsecond difference. Without a ready successor it is
  253.450 versus 256.195 microseconds. Both configurations make the same
  number of residency-hint calls. These are alternating CPU samples using
  warm residency stand-ins, without the allocator or DMA; they do not measure
  GPU throughput or JCT.
