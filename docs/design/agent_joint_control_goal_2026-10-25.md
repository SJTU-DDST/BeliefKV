# Agent Admission And KV Migration Goal

The user reset the goal on 2026-10-25. The admission unit is one sequential
agent invocation, not a whole workflow and not a single model request.
Compose continued agent eligibility with incremental Host backup, safe early
offload, JOIN/EOS prefetch and selected-consumer recovery. Workflow dependencies
and complete workflow JCT remain the optimization and accounting boundary.

## Evaluation Contract

Use the existing V195 natural native capture: 18 distinct original projects,
18 simultaneous roots, 2054 requests, 116355888 prompt tokens and 387242 output
tokens. Preserve context, Host capacity, running/decode limits, seed, native
sampling, model-selected delegation and causal pretokenized `/generate` replay.
Do not schedule another complete native replay. Count all normal tails, logical
admission waits, recovery waits and client dependency delays in workflow JCT.
Do not truncate outputs, omit unfinished workflows or change the trace to obtain
a favorable result.

As a prospective engineering criterion, target at least 10% lower mean JCT and
10% higher whole-run output-token throughput than each applicable baseline,
including the strongest completed paper adaptation. This percentage is an
engineering acceptance target, not a user-specified threshold or a performance
claim. Investigate regressions at the workflow level as well as aggregate gains.
Seek at least three matched runs of the final candidate and strongest paper
control before claiming repeatable gains; preserve hardware/thermal telemetry,
dispersion, all negative runs and the original natural native measurement.

Completed references include native, scheduler-only, ThunderAgent public
single-backend, Continuum public and CONCUR observed-JOIN. Continue the declared
remaining paper implementations, explicitly separating author artifacts, paper
reimplementations and dynamic-graph compatibility adaptations. Do not silently
substitute our priorities, migration or future replay graph for missing baseline
components. An incomplete adapter is not a defeated baseline.

Verify exact work, workflow completion, fresh telemetry drain and owned-process
cleanup for every result. Independent source-task correctness remains unscored;
performance results do not establish application correctness or publication
readiness.

## Unified Control Invariants

- Active eligibility persists across successive calls and short tool intervals.
  It is not permission to ignore native allocation or claim free physical HBM.
- JOIN waits and credible long tool waits can yield at a safe call boundary.
  Preserve native locks, shared-page ownership, ACKs and GPU reuse fences.
- Back up missing eligible KV incrementally in the background. A copy alone does
  not justify evicting an agent that should execute soon.
- Choose the agent before ordinary recovery. Its capacity reservation and next
  feasible admission opportunity must refer to the same identity and generation.
- Keep one bounded recovery consumer, with JOIN precedence and aging. Revoking a
  logical reservation never cancels or releases native ownership of submitted DMA.
- Prefer useful resident work while another selected agent restores, without
  letting residency indefinitely starve that selected consumer.
- Measure physical switching and recovery consumption. Logical eligibility alone
  is insufficient if native reclamation repeatedly evicts eligible agents.
- Use observed runtime events and bounded maintenance. Avoid per-token graph
  scans, a second tool predictor, and planning transfers for ineligible agents.

## Current Work

V214 is a frozen first candidate using commit `7c57001d`. Preserve its sources,
registration and results. Its initial working-set gate and CPU qualification do
not establish a performance gain.

Audit completed and in-progress runs for H2D ACK bytes, same-session repeated
restores, active-set turnover, effective decode concurrency, JOIN coverage,
recovery-to-first-service loss, Host pressure and measured control cost. Distinguish
logical admission, native residency and actual compute/transfer overlap.

Then repair conflicts supported by evidence, qualify the inherited CPU scope,
and run each new candidate separately on the shared GPU after its predecessor
has fully drained and cleaned up. Page-specific producer fences, shared-prefix
budget deduplication and layer-deadline calibration remain unimplemented
directions until their ownership and measured benefit are established.
