# Architecture

Updated: 2026-09-17.

This page is a concise English entry point. The authoritative system design is
[beliefkv_design.md](beliefkv_design.md), and the
current implementation status is
[architecture_status_zh.md](architecture_status_zh.md).

## System Model

BeliefKV serves concurrent dynamic agent workflows on one HBM-constrained GPU.
It does not require a predefined workflow DAG. Runtime TOOL, SPAWN, RETURN,
JOIN, HANDOFF, and MESSAGE events incrementally construct an RCCG.

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

## P5 Observed Path

The online P5 path is work-conserving and beneficiary-bound:

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

## P6 Predictive Overlay

FrontierBelief predicts action-local demand and causal slack for tool waits,
child/JOIN release, messages, and future KV growth. Prediction runs
asynchronously and may propose only a safe-point-validated action.

The predictive data path now implements `PREPARE_HOST`, full or ancestor-closed
partial `PREFETCH_GPU`, and a bounded `RECLAIM_AND_PREFETCH` transaction using a
commit-ready CPU-shadowed victim. These mechanisms are development canaries,
not a validated throughput result. The current model is not online-eligible:
rare boundary/tool outcomes are no better than majority baselines, while only
token-demand intervals and PREFETCH timing show useful held-out signal.

## Physical Authority

- RCCG owns causal semantics.
- PageIndex mirrors page ownership and generations.
- PhysicalBundle is the closure-complete migration unit.
- SGLang RadixCache/HiCache owns allocation, tensor location, locks, and DMA.
- A residency change becomes visible only after a generation-checked ACK.

## Current Diagram

![BeliefKV joint scheduling](figures/beliefkv_joint_algorithm_overview.svg)

Historical architecture text is preserved under `docs/archive/snapshots/`.
