# Environment Setup

Updated: 2026-09-15.

BeliefKV keeps the serving/control environment separate from the Agent workload
environment. Machine-specific GPU settings are frozen in a runtime profile and
must not be reconstructed from an old experiment report.

## 1. Conda Environments

From the repository root:

```bash
conda env create -f environment.yml
conda env create -f environment-agents.yml
conda run -n beliefkv python -m pip install -e ".[dev]"
```

For existing environments:

```bash
conda env update -n beliefkv -f environment.yml
conda env update -n beliefkv-agents -f environment-agents.yml
conda run -n beliefkv python -m pip install -e ".[dev]"
```

The maintained roles are:

| Environment | Python | Role |
| --- | --- | --- |
| `beliefkv` | 3.10 | policy, patched SGLang runtime, replay, tests |
| `beliefkv-agents` | 3.11 | LangGraph/Deep Agents, workload and Docker tools |

Do not install Deep Agents into the control environment merely to make an Agent
test import pass. Run that test in `beliefkv-agents`.

## 2. Pinned SGLang Source

BeliefKV currently targets SGLang 0.5.2rc1 at:

```text
18f91eb639084825717c0e3c3c7273492812ab71
```

Prepare a clean source tree:

```bash
git clone https://github.com/sgl-project/sglang.git third_party/sglang
git -C third_party/sglang checkout 18f91eb639084825717c0e3c3c7273492812ab71
git -C third_party/sglang apply --check \
  "$PWD/patches/sglang-0.5.2rc1-beliefkv-perf-ownership.patch"
git -C third_party/sglang apply \
  "$PWD/patches/sglang-0.5.2rc1-beliefkv-perf-ownership.patch"
```

Install SGLang from its Python project directory so editable extras are parsed
correctly:

```bash
cd third_party/sglang/python
conda run -n beliefkv python -m pip install -e ".[all]"
cd ../../..
conda run -n beliefkv beliefkv check-sglang "$PWD/third_party/sglang"
```

The canonical patch path and expected patched-tree hash are also recorded in
`configs/p6/h200_bf16_v7/frozen_runtime_profile.json`. A source-contract failure
must stop the experiment.

## 3. Current H200 Contract

The current frozen profile is:

```text
configs/p6/h200_bf16_v7/frozen_runtime_profile.json
```

Its relevant values are:

- Qwen3-Coder-30B-A3B-Instruct BF16;
- BF16 KV, TP=1, context limit 262,144;
- 850,000-token GPU KV pool;
- 96 GiB Host KV pool;
- `max_running_requests=32`;
- CUDA Graph batches 1/2/4/8/16/24/32.

The launcher reads these values and rejects CLI attempts to override immutable
capacity/model fields:

```bash
scripts/launch_deepagents_swebench_server.sh \
  --runtime-profile configs/p6/h200_bf16_v7/frozen_runtime_profile.json \
  RUN_DIR/server
```

`RUN_DIR/server/beliefkv_config.json` must be generated first by the experiment
launcher or `scripts/prepare_deepagents_server_config.py`. Prefer the frozen
experiment launcher over invoking the server manually.

## 4. Validation

```bash
conda run --no-capture-output -n beliefkv pytest -q
conda run --no-capture-output -n beliefkv-agents pytest -q \
  tests/test_deepagents_swebench.py tests/test_p6_collection.py
conda run -n beliefkv beliefkv check-sglang "$PWD/third_party/sglang"
bash -n scripts/launch_deepagents_swebench_server.sh
git diff --check
```

Before a GPU run, also verify that the selected GPU and server port are free,
the model path in the profile exists, required SWE-bench images are present,
and no pause sentinel is active.

## 5. Experimental Discipline

- Use a new output directory for every run.
- Use `performance_mode` for throughput comparisons and equivalent
  instrumentation in every arm.
- Predictor, GPU-service, and transfer-service artifacts must match their
  frozen hardware keys.
- `shadow_eligible` does not imply that an artifact may authorize an online
  physical action.
- Do not treat a mechanism gate or timeout-terminated trace as throughput
  evidence.

Current execution order and open gates are maintained in
[`implementation_plan.md`](implementation_plan.md).
