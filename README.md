# BeliefKV

BeliefKV is a research system for joint Agent request scheduling and KV-cache
management on one HBM-constrained GPU. It discovers workflow structure from
runtime TOOL, SPAWN, RETURN, JOIN, HANDOFF, and MESSAGE events; applications do
not need to submit a complete DAG in advance.

## Current Scope

The current target is:

- NVIDIA H200 NVL, tensor parallelism 1;
- Qwen3-Coder-30B-A3B-Instruct BF16;
- SGLang 0.5.2rc1 at commit
  `18f91eb639084825717c0e3c3c7273492812ab71`;
- LangGraph/Deep Agents and isolated SWE-bench tool containers;
- dynamic root, FRESH subagent, tool-wait, RETURN, and JOIN workflows.

The frozen runtime contract is
[`configs/p6/h200_bf16_v7/frozen_runtime_profile.json`](configs/p6/h200_bf16_v7/frozen_runtime_profile.json).
It uses an 850K-token GPU KV pool, a 96 GiB Host KV pool, at most 32 running
requests, and CUDA Graph batches 1/2/4/8/16/24/32.

## System Status

BeliefKV currently has two policy layers under one `JointPlan` authority:

- **P5 observed path:** work-conserving request admission, beneficiary-bound
  reactive `COMMIT_CPU/DROP`, selective running retraction, and transactional
  restore;
- **P6 predictive overlay:** asynchronous FrontierBelief scenarios and
  safe-point-validated `PREPARE_HOST` intents.

`PREPARE_HOST` creates a CPU shadow while retaining GPU KV. It is predictive
transfer, not predictive eviction. HBM-releasing offload remains reactive.
Predictive `COMMIT_CPU` is documented only as a future optional branch and is
not part of the current performance claim.

The latest code includes versioned physical ownership, visible-but-gated
admission, compact event-driven planning, CUDA Graph batch-32 support, and
generation-checked HiCache transfer transactions. The predictive mechanism has
passed focused correctness gates, but natural end-to-end throughput benefit has
not yet been established.

See the [current architecture status](docs/architecture_status_zh.md) for the
latest verified evidence and blockers.

## Architecture

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

RCCG is the causal view. PageIndex/Radix is the physical KV view. `JointPlan`
combines them, while SGLang remains the final allocator, batch-construction, and
DMA authority.

## Environments

The maintained setup uses two Conda environments:

| Environment | Python | Purpose |
| --- | --- | --- |
| `beliefkv` | 3.10 | control plane, patched SGLang runtime, tests, replay |
| `beliefkv-agents` | 3.11 | LangGraph/Deep Agents workload and Docker tools |

```bash
cd /home/longhao/experiment/BeliefKV
conda env create -f environment.yml
conda env create -f environment-agents.yml
conda run -n beliefkv python -m pip install -e ".[dev]"
```

For an existing installation, use `conda env update -n <name> -f <file>`.
Detailed SGLang patch and environment instructions are in
[`docs/setup.md`](docs/setup.md).

## Validation

Run control-plane tests before changing scheduling, ownership, or transfer code:

```bash
conda run --no-capture-output -n beliefkv pytest -q
```

Run Agent-runtime tests in the separate environment:

```bash
conda run --no-capture-output -n beliefkv-agents pytest -q \
  tests/test_deepagents_swebench.py tests/test_p6_collection.py
```

Validate the pinned SGLang source and launcher before a GPU run:

```bash
conda run -n beliefkv beliefkv check-sglang "$PWD/third_party/sglang"
bash -n scripts/launch_deepagents_swebench_server.sh
```

Formal experiments must read a frozen profile and workload manifest. Do not
copy capacity, artifact, or timeout values from an old experiment report.

## Documentation

Start with the [documentation index](docs/README_zh.md).

| Document | Role |
| --- | --- |
| [Current design](docs/beliefkv_design_2026-07-14_zh.md) | authoritative algorithm and system boundary |
| [Architecture status](docs/architecture_status_zh.md) | implemented, verified, pending, and blocked |
| [Execution plan](docs/implementation_plan.md) | only active near-term work order |
| [JointPlan visual guide](docs/beliefkv_jointplan_visual_zh.md) | concise architecture diagram |
| [Runtime integration](docs/runtime_integration_zh.md) | SGLang and runtime event contract |
| [Experiment reports](docs/experiments/README_zh.md) | immutable evidence for individual runs |
| [Archive](docs/archive/README_zh.md) | superseded plans and former long-form status pages |

## Repository Layout

```text
beliefkv/
  control/       RCCG, controller, and state transitions
  policy/        JointPlan, admission, residency, risk, and service curves
  predictor/     FrontierBelief data contracts and models
  runtime/       SGLang bridge, PageIndex, bundles, and transactions
  experiments/   Agent workload, collection, and dataset logic
configs/         frozen runtime and workload contracts
docs/            current documentation and experiment evidence
patches/         versioned SGLang integration patches
scripts/         launchers, replay, analysis, and collection tools
tests/           correctness and regression tests
```

## Evidence Boundary

Mechanism tests, shadow runs, timeout-terminated workflows, and synthetic
canaries are not end-to-end performance evidence. A performance claim requires
matched workload/model/runtime/instrumentation, clean transaction shutdown,
allocator/Radix consistency, and measured workflow throughput. High HBM usage
alone does not establish a useful KV scheduling opportunity.
