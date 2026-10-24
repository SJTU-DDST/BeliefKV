# Prefetch Validation and Root Phase Budgets

Status: 2026-10-25 (Asia/Shanghai). GPU performance remains unproven.

## Current Source and Workload

Keep V181's original 18 distinct projects, simultaneous arrival, running/decode
limits of 24, seed 21, 256K context and original pretokenized `/generate` replay.
The current source does not use the new root phase budget. V181 completed all
18 workflows and 2,071 requests, with 36 JOINs,
one nested JOIN and no root missing its required delegation. All 18 workflows
pass the declared source qualification; seven recorded return deferrals remain
visible. This source is ready for bounded GPU replay qualification, not an
independent task-correctness grade or a Full performance result.

V185 and V186 completed on the identical V181 request trajectory. The latter
enables the selected ordinary frontier and repairs critical queue donor
selection. Neither establishes the performance target:

| Arm | Mean Workflow JCT (s) | Whole-Run Output Tokens/s |
| --- | --- | --- |
| V181 native capture | 1591.305 | 204.878 |
| V185 Full | 1538.514 | 211.922 |
| V186 Full | 1523.872 | 212.811 |

Each arm serves 2,071 requests and 384,371 output tokens, with all 18 normal
workflow tails retained. V186 lowers mean JCT by 4.24% and increases throughput
by 3.87% against native in this single comparison. Neither side reprocesses
previously served same-session common input; Full's additional first-context
input prefill is 0.223%. These observations do not isolate any module's benefit.

V186 issued 18 ordinary frontier commands, transferring 6,537,609,216 bytes;
6,134,956,032 bytes have verified first-use identity. JOINs have 28 positive
pre-completion plans and 11 episodes with pre-completion submission, while 17
start-window episodes encountered capacity budgets without early submission.
Tool prefetch issued no commands: most inspected waits lacked a Host-backed
missing copy, while two positive-plan episodes remained before their start
window. Do not describe post-JOIN demand recovery as early prefetch.

The next source enables the existing root phase budget with the same 18
projects and running/decode limits. All 36 V181 JOINs have one member, and 12
of its 53 root work phases exceed 24 model calls (maximum 81); this motivates
testing earlier model-selected delegation without a fixed DAG or fanout.
The changed workload requires its own native capture. No additional complete
native replay is required.

V187 completed the root-budget source: all 18 workflows qualify, with 1,963
requests, 394,899 output tokens, 30 JOINs and two nested spawns. Root calls total
863 and child calls total 1,100. All observed root JOIN fanouts remain one.
The budget did not increase JOIN density relative to V181; preserve this
workload result rather than claiming the prompt change achieved that goal.
Its native event-boundary mean JCT is 1625.304 seconds and whole-run output
throughput is 197.717 tokens/s. Previously served same-session common-input
prefill is zero; Host FULL occupancy P50 is 99.904%. Different source tokens
and trajectories preclude interpreting V181 versus V187 as a policy speedup.

V188 stopped before starting a GPU server because the calibration caller did
not pass the explicitly selected frontier setting to the shared environment
validator. The comparison environment itself already accepts that explicit
candidate and disables both tool and ordinary prefetch in native mode.
Pass the registered candidate setting through the calibration caller. CPU
regressions exercise the actual calibration watch path through native environment
construction for both frontier settings, and stop before server launch. Preserve
the rejected V188 and its stopped V189 audit; qualify updated main and register
their replacements against the completed V187 source.

## Root Soft Budget

`qwen3_coder_18project_root_phase_20261024.json` defines V187's optional root budget.
Remind at 16 direct root model calls and use 24 as a soft phase target. A real
successful JOIN with an actually served child starts a new phase; nested-child
work and summary requests do not consume the root counter. Each threshold adds
one persisted notice to an existing tool result, without another graph node or
per-step system-prompt rewrite. Do not append an obsolete notice when that very
tool result completes the phase's JOIN.

Keep assignments, fanout, subsequent rounds and nesting model-selected. Complete
pending commands, edits, JOINs and necessary validation. Do not cap the workflow's
total calls or treat incomplete work as success. Shorter compute can increase
the migration fraction, but cannot create Host-only state, cold capacity or
prefetch lead by itself. Use a matching new native source if this option is used.

## FULL-Only EOS Repair

The old EOS byte calculation indexed both `kv` and `mamba` entries unconditionally.
Even `0 * entries["mamba"].host_pool.size_per_token` indexed the nonexistent state
pool on Qwen3-Coder. The caught KeyError silently skipped a valid FULL-only plan.
Confirmed JOIN recovery could still work, hiding the missing pre-JOIN path.

Skip pools whose required count is zero. Continue rejecting a plan if a pool
with positive demand is missing. Regression coverage includes FULL-only, hybrid,
missing-required-state, and actual pre-JOIN native command submission. Historical
V177's 105 EOS candidate inspections, 88 capacity preparations, 17 no-Host-target
observations and zero latest-start events are consistent with this defect;
they do not quantify its possible JCT effect.

## Selected Ordinary Consumer

`qwen3_coder_18project_selected_frontier_20261024.json` changes the ordinary
frontier flag while retaining the V181 workload and speculative tool policy.
The two mechanisms are independent. Restore only a currently executable
candidate in the existing scheduling frontier and only when its selected
transfer fits current free lists. Do not perform foreground donor reclamation.

Hold at most one selected consumer for 1,500 ms. Within its existing causal
class, keep it ahead of a later residency reorder. Never advance it over a ready
JOIN, a higher causal class or an aged head. EOS, identity change and expiry
revoke the selection. Actual inclusion in a native batch consumes it; retain
the existing ordinary-admission quota before selecting another consumer. This
is scheduling continuity, not a new native correctness lock or proof of use.
When the current free-byte budget is zero, skip ordinary queue scanning and
prefix planning even if request rows remain available.

## Critical Queue Capacity

V185's live capacity records repeatedly showed 16-17 free request rows but zero
queued donor candidates. The old donor collector skipped that many queue entries,
so a shorter waiting queue was protected in its entirety. Request-row headroom
is an admission ceiling; it does not establish that every waiting request will
enter the next batch under current KV pressure.

During an existing critical parent recovery, retain the actual queue head,
ready JOIN parents and sole children blocking another JOIN. Permit the remaining
unstarted queue tails to reach the existing native FULL donor checks. Revalidate
queue membership, liveness, session/epoch, output, row and KV allocation on each
owner check. Native reclaim continues to require a settled Host copy and to
reject locks and pending DMA; session anchors and transcripts remain available.

CPU regressions reproduce the surplus-row failure and cover original native
reclaim, Host-hit preservation and lock/DMA rejection. This repair does not
prove useful donors existed in every V185 window or establish GPU performance.
V185 remains frozen on its prior code and must finish before the repaired
configuration is compared on the identical V181 source.

## Measurement and Acceptance

Report the three paths separately:

| Path | Advance Boundary | Required Evidence |
| --- | --- | --- |
| JOIN | JOIN satisfaction and parent submission | Observed child EOS, live parent/session, enqueue and ACK times, exact first-use nodes |
| Tool | Actual tool completion | Predicted wait information available then, enqueue time, expiry/revocation, successor first use |
| Ordinary queued request | First native GPU service | Actual selected request/session, earlier enqueue, native batch selection, exact first-use nodes |

Keep post-JOIN demand restoration separate from pre-JOIN transfers. Count HBM
hits, no Host copy, capacity failures and missing estimate support separately.
Report verified reused bytes, bytes lost before first service, repeated H2D and
actual old-input prefill. DMA-stream elapsed time is not exposed dependency wait.
Missing sampled waits are unobserved, not zero.

Primary targets are at least 10% lower mean workflow JCT and 10% higher whole-run
output-token throughput with identical tokens, dependencies and stopping.
Retain all normal tails, failures and negative candidates. Follow an initial
gain with repetition and the strongest simple scheduling control, then module
ablations. JCT divided by generated tokens is not the primary matched-work metric.
The user-selected chat-capture versus `/generate` replay entry difference remains
a measurement limitation; do not subtract it as a JCT correction.
