# BeliefKV 跨服务器迁移指南

更新日期：2026-09-15。本文只说明迁移流程；模型、容量和 artifact 的当前值以冻结
runtime profile 为准。

本仓库只提交代码、测试、配置、冻结 manifest 和实验报告。模型权重、数据集、容器镜像、训练
artifact 与 raw trace 不进入 Git；迁移时必须单独处理。冻结 manifest 是历史证据，其中的绝对路径和
哈希不能原地修改。

## 1. 固定代码版本

在旧服务器记录并推送当前提交：

```bash
cd /path/to/BeliefKV
git status --short --branch
git rev-parse HEAD
git push origin main
```

在新服务器检出同一提交：

```bash
git clone git@github.com:SJTU-DDST/BeliefKV.git
cd BeliefKV
export BELIEFKV_ROOT="$PWD"
git rev-parse HEAD
```

## 2. 迁移 Git 之外的 artifact

以下目录当前均被 `.gitignore` 排除。用 `rsync` 从旧服务器传输需要保留的内容；raw 实验可按需
归档，不要执行 `git add -f`。

```bash
# 在新服务器的仓库根目录执行，并替换 OLD_HOST/OLD_ROOT/MODEL_ROOT。
rsync -a --info=progress2 OLD_HOST:OLD_ROOT/experiments/models/ experiments/models/
rsync -a --info=progress2 OLD_HOST:OLD_ROOT/workloads/raw/ workloads/raw/
rsync -a --info=progress2 OLD_HOST:OLD_ROOT/workloads/frozen/ workloads/frozen/
rsync -a --info=progress2 OLD_HOST:OLD_ROOT/workloads/sources/ workloads/sources/
rsync -a --info=progress2 OLD_HOST:OLD_ROOT/artifacts/ artifacts/
rsync -a --info=progress2 \
  OLD_HOST:MODEL_ROOT/Qwen3-Coder-30B-A3B-Instruct/ \
  MODEL_ROOT/Qwen3-Coder-30B-A3B-Instruct/
```

迁移后校验文件数量、总大小及关键 artifact 哈希。旧的冻结 profile 和 manifest
是实验凭证，不得原地修改；在新服务器上复制一份并冻结新的 profile。旧 raw trace
只在需要复查历史结论或重建数据集时迁移。

## 3. 重建软件环境

```bash
conda env create -f environment.yml
conda env create -f environment-agents.yml
conda run -n beliefkv python -m pip install -e ".[dev]"

git clone --branch v0.5.2rc1 https://github.com/sgl-project/sglang.git \
  third_party/sglang
test "$(git -C third_party/sglang rev-parse HEAD)" = \
  18f91eb639084825717c0e3c3c7273492812ab71
git -C third_party/sglang apply \
  "$PWD/patches/sglang-0.5.2rc1-beliefkv-perf-ownership.patch"
cd third_party/sglang/python
conda run -n beliefkv python -m pip install -e ".[all]"
cd ../../..
conda run -n beliefkv beliefkv check-sglang "$PWD/third_party/sglang"
```

SWE-bench 工具容器不会随 Git 迁移。应在新服务器重新拉取或从可信的离线镜像导入，并核对
任务 manifest 所引用的 image tag。`beliefkv-swe` 仅在执行官方 patch correctness harness 时需要。

## 4. 配置机器相关路径

- `MODEL_PATH`：新服务器上的 Qwen 模型目录。
- `BELIEFKV_AGENT_PYTHON`：`beliefkv-agents` 环境的 Python；Conda 环境为同级目录时可自动发现。
- `CUDA_VISIBLE_DEVICES`：实验使用的单卡编号。
- `QWEN_CODE_ROOT`、`NODE_ROOT`：仅在运行 Qwen Code 兼容实验时设置。
- runtime profile：更换 GPU、模型 dtype、SGLang/CUDA 或 Host NUMA 后必须新建并重新
  标定 GPU/transfer service artifact；不能只改路径后沿用硬件 key。

示例：

```bash
export BELIEFKV_ROOT="$PWD"
export BELIEFKV_AGENT_PYTHON="$(conda run -n beliefkv-agents which python | tail -1)"
export MODEL_PATH=/new/model/root/Qwen3-Coder-30B-A3B-Instruct
```

## 5. 迁移验收

```bash
git diff --check
conda run --no-capture-output -n beliefkv pytest -q
conda run --no-capture-output -n beliefkv-agents \
  pytest -q tests/test_deepagents_swebench.py tests/test_p6_collection.py
conda run -n beliefkv beliefkv check-sglang "$PWD/third_party/sglang"
bash -n scripts/launch_deepagents_swebench_server.sh
test ! -e /tmp/beliefkv-experiments.paused
nvidia-smi
```

CPU 回归、补丁兼容检查、profile preflight 和模型启动 smoke 均通过后，再冻结新服务器上的
实验配置。正式实验必须使用新的输出目录，不能续写或覆盖旧服务器的 raw run。当前 H200
参考合同为 `configs/p6/h200_bf16_v7/frozen_runtime_profile.json`，但迁往另一台机器时仍需
重新验证其 hardware key 和容量。
