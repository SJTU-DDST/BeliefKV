# BeliefKV JointPlan 快路径与 CUDA Graph 32 门禁报告

日期：2026-08-17  
实现提交：`6ff582efbae010e4081ab5cb255b3c1166a8e68e`  
GPU：NVIDIA H200 NVL 143,771 MiB  
模型：Qwen3-Coder-30B-A3B-Instruct BF16  
KV pool：850,000 tokens；Host pool：96 GiB

## 结论

本轮完成了两项彼此独立的开发门禁：

1. JointPlan 普通进度快路径按预期工作。短 gate 中 47 次 delta 只有 1 次触发完整 PolicyInput 和规划，其余 46 次仅更新 worker mirror；另有 551 次 scheduler tick 被 100 ms 合并门禁直接抑制。
2. `h200_bf16_v5` 成功捕获并执行 batch 31/32 CUDA Graph。相同 32-way 固定 decode 下，稳定吞吐中位数从 graph-16 control 的 661.12 token/s 提升到 5,112.48 token/s，推导 step latency 从 48.40 ms 降到 6.26 ms。

CUDA Graph 32 功能门禁通过，可以将 v5 作为后续 baseline/treatment 的共同 runtime profile。JointPlan 优化只通过低压快路径门禁，尚未通过真实 agent 高压下的 physical-action P95 门槛。

## 输入与产物

- v5 profile：`configs/p6/h200_bf16_v5/frozen_runtime_profile.json`
- v5 profile SHA-256：`7920f79f585b9588b39bd98f8c7c00671ebf0ea57bc38be12f00748119716b4a`
- graph-32 raw：`experiments/raw/h200_bf16_v5_cuda_graph32_gate/20260816T192158Z/server`
- graph-16 control raw：`experiments/raw/h200_bf16_v5_cuda_graph32_gate/20260816T192158Z/control_graph16/server`
- graph-32 请求：32×512-token 和 31×256-token
- graph-16 control：与 graph-32 第一组完全相同的 32×512-token 请求

两臂使用同一 GPU、模型、代码提交、850K KV pool、96 GiB Host pool、max-running=32、prompt 和采样参数；唯一有意差异是 `cuda_graph_max_bs=32` 与 16。两臂均从干净 server/cache 状态启动，并在完成后收到受控 shutdown ACK。

## JointPlan 快路径

实现内容：

- 普通 decode service 与非关键 queue revision 最多每 100 ms 发布一次 apply-only delta。
- pressure crossing、SPAWN、TOOL return、child RETURN、JOIN、transfer ACK、beneficiary deficit 及 restore/retraction revision 触发完整规划。
- apply-only delta 只更新异步 mirror，不构建 PolicyInput、不运行 planner、也不发布可执行计划。
- RCCG 与 consumer snapshot 按 revision 复用；owner delta 不再复制每个 context 的完整 handle closure。
- observed 模式省略 transfer estimate/curve 构造；snapshot ID 改为 revision tuple。
- 低压 admission 继续由 bounded work-conserving seed 决定；只有包含 residency/retraction 的异步计划进入 safe-point 动作局部校验。
- no-action 与 physical-action commit 预算分别为 1 ms 和 5 ms。

短 gate 结果：

| 指标 | 结果 |
|---|---:|
| Scheduler steps | 941 |
| progress coalesced | 551 |
| delta submitted | 47 |
| apply-only | 46 |
| full plan | 1 |
| failed/dropped/superseded | 0 / 0 / 0 |
| Safe-point delta capture P50/P95/P99 | 0.114 / 0.413 / 0.806 ms |
| Snapshot delta apply | 0.038 ms |
| Snapshot materialization | 1.578 ms |
| Plan compute | 2.097 ms |
| Validation | 0.062 ms |

这组数据证明低压快路径避免了每个进度更新都重建计划，但不能与 v4 的 64-root 长跑 P95 直接做性能比值：短 gate 没有 RCCG context、physical bundle 或迁移动作。plan age 为 1,002.6 ms，主要来自结果完成后直到下一 safe point 才被观测；该结果是 seed-only，并未获得在线物理权限。

## CUDA Graph

v5 启动日志：

- 捕获 batch `[1, 2, 4, 8, 16, 24, 32]`；
- graph capture 使用 0.21 GB；
- capture 后 SGLang 报告 3.28 GB 可用，`nvidia-smi` 稳态 free 为 2,922 MiB；
- 高于 1 GiB 安全门槛，因此不调整 850K KV pool。

功能结果：

- 32-way：12/12 稳定日志均为 `cuda graph: True`；
- 31-way：6/6 日志均为 `cuda graph: True`，证明 padding 到 graph 32 生效；
- 63/63 HTTP 请求成功；completion token 总数 24,320；
- 无 OOM、NaN、graph replay failure 或响应 error。

同负载 graph-16 control 结果：

| 指标 | graph 16 | graph 32 | 变化 |
|---|---:|---:|---:|
| 稳定 batch-32 样本 | 12 | 12 | - |
| CUDA Graph | False | True | 命中 graph 32 |
| 吞吐中位数 | 661.12 token/s | 5,112.48 token/s | 7.73× |
| 吞吐范围 | 659.96-692.61 | 4,865.55-5,341.41 | - |
| 推导 step latency | 48.40 ms | 6.26 ms | -87.07% |

step latency 由 `32 / throughput × 1000` 推导，不是 CUDA event 直接测量。该对照只证明当前固定 batch-32 decode 的 graph 收益；agent workload 中的 prefill、batch 波动、工具等待和 JointPlan CPU 开销不包含在这个比值中。

## Gate 判定与后续

| Gate | 结果 |
|---|---|
| 捕获 graph 32 | 通过 |
| capture 后余量 >= 1 GiB | 通过 |
| batch 31/32 graph replay | 通过 |
| 无 OOM/NaN/replay failure | 通过 |
| paired decode throughput/step evidence | 通过 |
| JointPlan no-action capture P95 < 1 ms | 通过 |
| physical-action validation P95 < 5 ms | 未覆盖 |
| 高压 agent trace 下完整 planning/stale rate | 未覆盖 |

后续 baseline 与 treatment 必须共同使用 v5，不能将 v4 treatment 与 v5 baseline 配对。正式 predictive action 前需要在 graph 32 下重新标定 GPU service model 和 decode-contention D2H/H2D transfer curve。下一次高压 agent gate 还需验证 target closure 物化、physical-action validation、stale rate 和 `COMMIT_CPU -> ACK -> beneficiary service`；当前完整规划仍会构建全量 bundle summary 来选择 victim，target-only summary 是剩余优化项。
