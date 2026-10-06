# Architecture

Updated: 2026-10-06.

This page is a concise English entry point. The authoritative system design is
[beliefkv_design.md](beliefkv_design.md), and the
current implementation status is
[architecture_status_zh.md](architecture_status_zh.md).

## Current Mainline

Qwen3.5-35B-A3B BF16 and SGLang 0.5.20 run with the Agent workload in
the shared `beliefkv-next` environment. FULL and Mamba are managed together;
native UnifiedRadixCache and allocators remain the physical authority.
The latest completed v6 pair used 84 roots in one arrival wave, running=48,
and a 200 GB Host pool on NUMA node 1. Runtime, prompts, weights and launch
arguments stayed frozen at launch commit `573f32c`. Both arms completed 84/84.
Seven JOIN H2Ds were actually reused without re-parking or native reload,
but all followed EOS; tool H2D remained zero. Observed completed throughput
increased 4.62% with less realized demand, not an isolated speedup.
Work-only refits keep the phase encoder/head frozen.
Repeated live pairs are for later formal evaluation;
canaries and fixed-demand GPU replay are not prerequisites.

## System Model

BeliefKV serves concurrent dynamic agent workflows on one HBM-constrained GPU.
It does not require a predefined workflow DAG. Runtime TOOL, SPAWN, RETURN,
JOIN, HANDOFF, and MESSAGE events incrementally construct an RCCG.

The diagram below is the target joint architecture, not a claim that every
legacy JointPlan/COMMIT/retraction path has been migrated.

```text
Agent runtime events             SGLang physical state
        |                               |
        v                               v
      RCCG                       PageIndex / Radix
        \                               /
         +---------- JointPlan --------+
                       |
            execution + admission + KV
                       |
           tickets / transfer commands
                       |
             SGLang batch and HiCache
```

## Observed Path Contract

The target observed path is work-conserving and beneficiary-bound:

1. select factually runnable requests from the RCCG frontier;
2. produce a bounded execution and admission seed;
3. detect an actual startup/growth HBM deficit;
4. bind a deferred beneficiary to movable PARKED/DEAD physical bundles;
5. issue reactive `COMMIT_CPU` or `DROP`;
6. admit the beneficiary only after authoritative reclaim ACK;
7. complete the transaction after the beneficiary receives GPU service.

Requests remain in SGLang's visible waiting queue. BeliefKV emits short-lived
admission tickets; SGLang remains the allocator and batch-construction
authority.

## Current Predictive Path

Delivered-text semantics, completion notices and observed decode progress
support a frozen child phase/remaining-work model. A separate tool-event model
predicts residual tool time. The models do not learn offline counterfactual
net benefit or authorize physical actions. Runtime checks the causal frontier,
safe-input checkpoint, Host copy, FULL/Mamba capacity and transfer service
cost before issuing bounded native `PREPARE_HOST` or predictive H2D.
Valid native D2H copies also qualify; prior PREPARE consumption is not required.

Both experimental arms share notices, bounded final-report priority,
waiting-state preparation and real-pressure demotion. Only predictive enables
early loads. This is not an untouched native baseline.
v6 had seven JOIN H2D ACKs totaling 0.533 GB, all with verified FULL first
use; six Mamba forward uses were verified. All followed native EOS;
no pre-EOS load was produced.
Neither broad subsecond RETURN accuracy nor end-to-end throughput benefit
has been demonstrated.

v5 exposed opposing temporal/residency policies: clipped P50 countdown
selected tool prefetch while conditional CDF selected long-wait parking.
Every tool load was ACKed, pressure-demoted again and reactively reloaded
before first service. These six actions cannot be called useful simply
because they completed. The conflict and missing post-ACK short residency
are now addressed in CPU-tested code. V6 verified the JOIN lifecycle but
provided no new tool-transfer sample. Residual
P50 is inverted from the same survival-conditioned CDF. The post-ACK soft
lease excludes BeliefKV parking until service or explicit invalidation; it is
not an allocator pin, reservation or guaranteed-reuse certificate.
Both arms had 113 children/joins and zero child cancellations; the predictive
arm's one incomplete root was an 8192-token, length-ended repetition.
Completed throughput was -8.95%; paired mean JCT was -8.84%, with differing
realized work. See the [v5 report](experiments/joint_tool_join_h2d_v5_84root_zh.md).

v4's lower average utilization is dominated by an isolated Django workflow
tail containing two 600-second whole-suite tool timeouts. Pipeline failures
can also be misreported as success. A smaller nonempty-demand utilization gap
remains; function-level CPU causality is not recoverable from the collected
logs. See the updated [v4 report](experiments/joint_tool_join_h2d_v4_zh.md).
Scheduler/worker service spans must not be called CUDA kernel time.

Full legacy JointPlan, COMMIT and selective running retraction remain
unvalidated in the new architecture. Their old physical enable path stays
fail-closed; it is distinct from the bounded native actions already observed.

## Physical Authority

- RCCG owns causal semantics.
- PageIndex mirrors page ownership and generations.
- PhysicalBundle is the closure-complete migration unit.
- SGLang RadixCache/HiCache owns allocation, tensor location, locks, and DMA.
- A residency change becomes visible only after a generation-checked ACK.

## Current Diagram

![BeliefKV joint scheduling](figures/beliefkv_joint_algorithm_overview.svg)

Historical architecture text is preserved under `docs/archive/snapshots/`.
The version immediately preceding this update is also available in Git at
`c219604:docs/architecture.md`.
