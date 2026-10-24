# Child Context Size and Population Sizing

Status: historical sizing and interrupted V178 GPU calibration, updated
2026-10-25 (Asia/Shanghai). No accepted Full result exists for this population.

## Interrupted Calibration

V178 used 18 distinct projects and running/decode limits of 24. It was stopped
after four naturally completed roots had never delegated. V179's waiting
qualification queue was stopped too; the planned V180 Full did not launch.
Terminal states and the original process identities confirm GPU release.

The stopped prefix contains 1,085 completed native requests. Exact token prompts
and prefill geometry cover every one; previously served comparable common-input
reprocessing totals zero. Actual input prefill is 2,264,619 tokens, which includes
initial cold input and new history. Host use P50 is 99.843%; its last sampled use
is 199.678/200.000 GB. The last HBM sample has 2,411 free and 30,594
allocator-evictable tokens, approximately 0.237 and 3.008 GB respectively.
Allocator evictability does not prove those bytes are suitable cold donors.
Recorded H2D is 6.365 TB and D2H is 734.843 GB; their stream times are not exposed
critical-path wait or an upper bound on JCT benefit.

Retain 18 roots for the corrected calibration. There is clear migration pressure
but no observed old common-input reprocessing in this prefix. Full's ability to
release cold capacity and restore a long parent remains untested; reduce to 16
if persistent restoration-capacity failures or useful-state losses establish
that 18 is excessive.

The prompt already contained an explicit MUST-delegate requirement. The failure
was the absence of a runtime pre-return check, not missing prompt text. The
root-only check now defers an undelegated final answer, records its reminder,
and allows at most two model-generated required task attempts. A real satisfied
JOIN is required; declaration alone, rejected tasks and cancellations do not
qualify. The model still selects assignments, roles, fanout, later rounds and
nesting. All contract deferrals remain visible, safety failures are rejected,
and the source monitor checks each newly completed root promptly.

Evidence:

- `/home/longhao/experiment/ops/v178_bounded_child_stopped_prefix_20261024.json`
- `/home/longhao/experiment/ops/v178_stopped_nondelegating_roots_20261024.json`
- `/home/longhao/experiment/ops/v178_qwen3_coder_18project_bounded_children_source_20261024.registration.state.json`
- `/home/longhao/experiment/ops/v179_qwen3_coder_18project_bounded_children_check_20261024.registration.state.json`

## Measured Contexts

Source: completed V174 natural native capture, 24 distinct LoCoBench-Agent
projects, Qwen3-Coder-30B-A3B-Instruct. Bind each client request to its actual
server prompt length and completed output. Count ordinary model calls within
each invocation; do not sum generated tokens as if they were context length.

The BF16 FULL KV geometry is:

```
48 layers * 2 (K, V) * 4 KV heads * 128 dimensions * 2 bytes
= 98,304 bytes/token = 96 KiB/token
```

Twenty of the 35 children reached call 32; none ended at exactly call 32.
Use those same 20 children for every row below to avoid changing cohorts.

| Checkpoint | Context Tokens P50 | Logical FULL KV P50 | Logical FULL KV P90 |
| --- | ---: | ---: | ---: |
| Call 1 | 6,658.5 | 0.610 GiB | 0.618 GiB |
| Call 16 | 16,879 | 1.545 GiB | 2.113 GiB |
| Call 24 | 22,648.5 | 2.074 GiB | 2.661 GiB |
| Call 32 | 27,792 | 2.544 GiB | 3.235 GiB |
| Actual final call | 34,602 | 3.168 GiB | 3.832 GiB |

At call 32, the observed range is 19,842--43,801 tokens, or
1.817--4.010 GiB. The same-cohort median is approximately 19.7% smaller than
at natural completion. A new narrow assignment can return earlier and grow
less; this is historical sizing evidence, not a prediction for the new prompt.

These are complete logical context estimates, not allocator-exact HBM usage.
Shared prefixes count repeatedly, a final generated token may not yet have
its own KV entry, and tool results after a tool-producing response enter KV
only with a following prefill. Children start with their own instructions and
delegated description, not a copy of the root's full repository.

## Capacity Cross-Check

The measured FULL pools contain 79.154 GB of HBM and 200.000 GB of Host KV
capacity. These are decimal GB; the child table uses binary GiB.

The following sensitivity calculation keeps the original V174 request and
return times and root growth. For child requests after call 32, cap logical
size at the smaller of actual size and the child's call-32 context. It does
not remove calls, simulate earlier completion, reproduce a different
scheduler, or predict the new workload.

Populations are prefixes of the predeclared distinct-project order, not
selections based on measured outcomes.

| Roots | Initial Root Logical KV | Original Live-Context Peak | Size-Clipped Live-Context Peak |
| --- | ---: | ---: | ---: |
| 16 | 135.959 GB | 187.225 GB | 186.779 GB |
| 18 | 160.712 GB | 221.009 GB | 220.450 GB |
| 20 | 189.833 GB | 258.158 GB | 258.094 GB |
| 24 | 222.065 GB | 304.065 GB | 304.001 GB |

The peak is dominated by long root/parent contexts, not child growth after
call 32. These logical sums omit obsolete cached branches and duplicate
Host/HBM copies and do not deduplicate common prefixes. They cannot prove
physical capacity shortage, available cold donors or beneficial prefetch.
V177's measured 99.819% median Host occupancy and useful-parent losses are
the independent reason to avoid increasing concurrency immediately.

## Next Experiment Decision

- Use 18 distinct-project roots as the initial capacity calibration candidate.
  Keep running and decode limits at 24; these are separate settings.
- Retain the 24-call reminder and approximately 32-call soft return target.
  Complete pending tools, nested JOINs and necessary validation. Do not
  truncate model output or forcibly declare a child successful.
- Require at least one substantive delegation in the root prompt. Encourage
  useful subsequent rounds and independent nested subproblems; do not impose
  a fixed execution DAG.
- Measure actual blocked-parent HBM release, available Host-only parent KV,
  cold recoverable capacity during restoration, and old common-input
  reprocessing separately from initial cold prefill.
- If the new workload has insufficient real recovery pressure, increase to
  20 roots. If useful parent state is repeatedly lost from both tiers or
  restoration lacks recoverable capacity, reduce to 16 rather than adding
  concurrency. Do not select a final population from logical sums alone.
- Collect the matching natural native source after a population/prompt change;
  reuse it as the baseline without another complete native replay. The user
  selected the original pretokenized `/generate` path; do not add chat-entry
  alignment or raw chat-request capture. Ordinary next-batch restoration
  remains a later bounded candidate.

CPU verification: after the user's original-entry decision, 340 retained
child-budget, nesting, context-lifecycle, loop-recovery, adapter,
source-qualification and original replay/native-receipt tests pass. The
replay backend, native plugin and driver match their pre-alignment code.
The shell launcher passes `bash -n`; `git diff --check` passes.

Evidence:

- `/home/longhao/experiment/ops/v174_child_context_and_pressure_20261024.json`
- `/home/longhao/experiment/ops/audit_v174_child_work_budget_20261024.py`
- `/home/longhao/experiment/ops/original_replay_bounded_child_cpu_20261024.xml`
- `docs/experiments/v177_completed_pair_and_cold_capacity_20261024.md`
- `docs/experiments/v177_host_losses_and_replay_comparability_20261024.md`
