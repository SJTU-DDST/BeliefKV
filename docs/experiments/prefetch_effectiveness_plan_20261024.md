# Prefetch Validation and Root Phase Budgets

Status: 2026-10-25 (Asia/Shanghai). GPU performance remains unproven.

## Current Source and Workload

Keep V181's original 18 distinct projects, simultaneous arrival, running/decode
limits of 24, seed 21, 256K context and original pretokenized `/generate` replay.
The current source does not use the new root phase budget. Its live prefix at
2026-10-24T17:47:52Z has 24 JOINs, 16 satisfied JOINs, three returned roots with
no nondelegating return, and zero nested JOINs. All 1,380 completed native
requests have prompt and prefill geometry; old common-input reprocessing is
zero in that prefix. This does not prove zero later losses or safe cold capacity.
Preserve normal tails and the incomplete live-prefix status.

V181 subsequently completed all 18 workflows and 2,071 requests, with 36 JOINs,
one nested JOIN and no root missing its required delegation. All 18 workflows
pass the declared source qualification; seven recorded return deferrals remain
visible. This source is ready for bounded GPU replay qualification, not an
independent task-correctness grade or a Full performance result.

The first follow-up uses the repaired Full with ordinary frontier restoration
disabled. Review its completed result before deciding whether to test the
selected-frontier candidate on the same source or recollect a better-shaped
workload. No additional complete native replay is required.

## Root Soft Budget

`qwen3_coder_18project_root_phase_20261024.json` is an optional future workload.
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
