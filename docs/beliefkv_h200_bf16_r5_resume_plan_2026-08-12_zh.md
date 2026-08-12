# BeliefKV H200 BF16 重基线与 R5 恢复计划

日期：2026-08-12

状态：当前 H200 服务器上的快速执行计划。旧 R5 v9 性能比较暂停，但 R0--R5 代码不回滚。
完成 H200/BF16 环境、KV pool、硬件服务模型和 FrontierBelief 更新后，从 R5 canary/A-B 入口恢复。

## 1. 固定范围

- 主模型固定为 Qwen3-Coder-30B-A3B-Instruct BF16，并冻结 model/tokenizer revision；
- SGLang 继续使用 BeliefKV 当前固定 commit 和 patch；
- KV dtype 必须显式固定并记录实际值，不能只保留 `auto`；
- 模型上下文上限为 262,144，正式 workload 主要覆盖 64K、128K、192K；
- 继续复用 SWE-bench 任务源与 repository 划分，同时覆盖 `natural` 和
  `parallel_analysis_2to3`；
- 不接入 Qwen3.6，不处理 Mamba cache，不恢复 morphology，不新增预测目标和动作。

旧 RTX 6000 Ada/FP8 R5 结果和硬件 artifact 只作为历史证据，不能与 H200/BF16 拼接。旧语义
数据若能恢复只能进入 train；正式 calibration/test 必须全部来自 H200/BF16。

## 2. 最短执行链

```text
H0 冻结 H200/BF16 环境
  -> H1 一次性确定并冻结 KV pool
  -> H2 重采 GPU 与 transfer service model
  -> H3 采集 BF16 pilot，决定增量校准或完整重训
  -> H4 冻结新 artifacts 并做短 shadow/canary
  -> H5 以新 manifest 恢复 R5 A-B/B-A/A-B
```

H0--H4 只重建环境相关证据，不重新评价已通过的 P5 restore、JointPlan 和 retraction 架构。

## 3. H0：环境与配置冻结

新建 `configs/p6/h200_bf16_v1/`，不修改历史 `predictive_joint_v9/`。目录包含：

```text
environment_manifest.json
collection_plan.json
workload_manifests/
service_calibration_plan.json
baseline_manifest.json
ab_run_plan.json
```

环境 manifest 至少记录 H200 UUID/显存、driver/CUDA、CPU/NUMA/PCIe affinity、pinned Host、模型与
tokenizer revision、weight/KV dtype、SGLang/BeliefKV commit、attention backend、page size、
`context_length=262144`、prefill chunk、Host pool、HBM safety margin、Docker image 和 workload digest。

必须修改 `scripts/run_p6_collection_batch.py` 的 FP8 默认值。正式采集应要求显式 `--model`，并从
`/get_server_info` 校验 server model identity，避免只改变显示字符串却连接错误模型。

通过条件：BF16 server 能完成 health、单请求 prefill/decode、HiCache 初始化和 clean shutdown；配置
中没有旧 FP8 artifact 或 RTX hardware key。

## 4. H1：KV Pool 一次性定容

KV pool 是环境配置，不是本项目的研究变量。不再搜索“最佳 pool”，也不为了制造
pressure 反复缩小 pool。在 BF16 模型、CUDA graph、attention backend 和 Host backend 完成初始化后，
使用以下唯一规则定容：

```text
hbm_safety_margin = 1 GiB
kv_pool_bytes = floor_to_allocator_pages(
    total_hbm_bytes - model_and_runtime_reserved_bytes - hbm_safety_margin
)
```

实现上可通过 SGLang 的 static-memory/KV-pool 参数达到该预算。启动后以 runtime allocator 和
`nvidia-smi` 的稳定态数据交叉检查，只要静态剩余 HBM 不低于 1 GiB 即通过。如首次
启动 OOM，只按固定 1 GiB 步长回退一次，随后冻结；不做多轮容量搜索。

唯一的功能 smoke 是验证单个 192K context 及预留 completion 能完成；若不能，则说明
当前模型/运行时配置与正式 workload 不兼容，而不是继续调 pool 的信号。

collection contract 记录 total/reserved/free HBM、`hbm_safety_margin_bytes`、KV dtype、
KV bytes/token、`max_total_num_tokens`、实际 KV pool bytes 和 Host pool bytes。Host pool 至少容纳
最大 planned transfer 加一笔 restore reserve，否则在 workflow 启动前失败。

R5 需要的 KV pressure 由冻结前的 workload concurrency/arrival profile 产生。如果 smoke 压力不足，
只调整 workload 并发度或到达率，不改变已冻结的 KV pool；不得根据 A/B 结果调参。

## 5. H2：重采 H200 硬件服务模型

### 5.1 GPU prefill/decode

复用：

```text
run_queue_service_calibration.py
export_gpu_service_calibration.py
train_gpu_service_curve.py
```

最小矩阵覆盖 64K/128K/192K prefill 和 decode，running width 1/2/4，warm/cold prefix 与实际 prefill
chunk；每个有效 phase bucket 满足现有 cross-calibration gate。artifact 必须绑定 BF16、KV dtype、
H200、SGLang commit 和 pool profile。FrontierBelief 仍不得读取 GPU 时间、batch size 或排队时间。

### 5.2 D2H/H2D

复用 `run_restore_micro_gate.py`、现有 transfer micro runner 和 `export_transfer_service_model.py`。
不重做 morphology 矩阵，只校准在线保留的 `bytes + extent_count + contention`：

- 64K、128K、192K context 对应的真实 KV bytes；
- D2H/H2D；
- idle 与一档真实 decode contention；
- 实际 HiCache bundle/page count，不人为制造极端碎片；
- 每个正式支持 bucket 至少 3 次重复。

hardware key 绑定 GPU、driver/CUDA、SGLang、KV dtype、pinned Host、NUMA、page size 和 Host backend；
旧 artifact 必须继续被拒绝。

### 5.3 Tool wait

不做独立 tool 微基准。tool survival 从 H3 workflow 重采，并记录 CPU、磁盘、Docker 和 Host 并发；
旧机器 tool wait 只能作为 train prior。

## 6. H3：BF16 数据与 FrontierBelief

### 6.1 Pilot

先运行 12--16 个 workflow，覆盖至少 4 个 repository，并同时包含 natural 与 parallel fan-out。pilot
属于 train/audit，不进入最终 test，用于检查标签完整性、旧模型 calibration、token interval、tool
survival、OOD/backoff，以及 BF16 对输出长度、工具选择和 fan-out 的影响。

若能恢复旧服务器资产，只迁移 Frontier artifact、processed semantic dataset、split manifest 和
SHA-256，不迁移全部 raw trace。旧 artifact 不可恢复时，pilot 只验证管线并直接进入完整重训。

### 6.2 分支

**增量校准**：旧 artifact 可用且 BF16 pilot 未明显失校。

- 旧 FP8 rows 只进入 train；
- pilot BF16 rows 加入 train；
- 另采 BF16 calibration/test，各至少 16 个 workflow 且 repository 隔离；
- 重新 fit/recalibrate，生成 H200/BF16 artifact。

**完整重训**：旧 artifact 缺失或 action/token/OOD 明显漂移。当前服务器按此分支准备：

```text
总计 96 个 BF16 workflow
train:       64
calibration: 16
test_id:     16
```

三组严格按 repository 隔离，每组同时覆盖 natural/parallel，尽量各占一半。先收 train+calibration，
冻结模型后才解封 test。失败样本只能按预先分配任务补采，不能挑选成功轨迹替换难例。

### 6.3 训练契约

复用 `characterize_p6_coverage.py`、`train_frontier_belief.py`、
`calibrate_frontier_belief.py` 和 `evaluate_frontier_belief.py`。保持：

- load-coupled GPU time/batch features 被 schema 禁止；
- RCCG 根据已观测 child 集合确定性组合 JOIN，不训练完整 DAG；
- calibration/test repository 不进入 fit；
- artifact 记录 dataset/split digest、model/tokenizer revision 和 BF16 环境；
- test 在模型、pool 和阈值冻结后只运行一次。

## 7. H4：在线恢复短门禁

冻结三个新 artifact：

```text
frontier_belief_qwen3coder30b_bf16_h200_v1.json
gpu_service_qwen3coder30b_bf16_h200_v1.json
transfer_service_qwen3coder30b_bf16_h200_v1.json
```

随后只运行：4-workflow predictor shadow sanity、一笔 PREPARE_HOST canary、一笔 Frontier-Aware
Retraction canary 和 clean shutdown。检查 exact/backoff/OOD、planning latency、transfer ACK、动作
ledger、replacement service、victim restore 以及零 orphan 状态。

没有自然动作时记录 `no_positive_action`，仍可继续 R5；不能注入负收益动作或使用旧硬件 artifact。

## 8. H5：恢复 R5

新建 H200/BF16 baseline manifest 和 run plan，不能修改或复用 v9 source hash：

```text
A: P5 observed JointPlan
B: P5 + predictive PREPARE_HOST + Frontier-Aware Retraction
顺序: A-B / B-A / A-B
```

六次运行共享 model revision、三个 artifacts、exact KV/Host pool、workload、arrival 和 fan-out profile。
旧 FP8/RTX 结果不进入 aggregate。主指标保持 successful workflows/hour，同时报告 task success、
tool/JOIN throughput、GPU busy、running count、admission wait、transfer stall、prediction outcome 和
victim restore cost。

## 9. 立即执行与禁止项

1. 修改 FP8 默认值，加入 1 GiB HBM safety-margin 记录与检查；
2. 生成 environment manifest，一次性冻结 KV pool 并完成 192K BF16 smoke；
3. 采集 GPU/transfer service artifacts；
4. 运行 12--16 workflow pilot；
5. 走增量分支或 96-workflow 重训；
6. 冻结三个 artifacts，运行两笔 canary；
7. 恢复六次 R5 A/B。

禁止继续 v9 FP8 A/B、迁移全部旧 raw trace、重跑 morphology、扩展 Qwen3.6/Mamba/peer workload、
修改 Frontier 模型结构，以及在 test 解封后调整 pool 或阈值。
