# P6 Shape-Aware Transfer 与重复评估固定 Trace

日期：2026-08-29

## 结论

本轮通过了 artifact 加载、shape support 和重复评估抑制门禁，但没有形成 HBM 压力，
不能评价预测迁移收益。新 transfer artifact 将 shape-unsupported 从 100% 降至 7.60%；
触发签名压缩使完整 risk result 的业务事件归一化比例由 67.35% 降至 50.16%。

扩大 shape support 后，更多候选进入完整 scenario timeline，单次 planning 延迟没有下降。
因此随后加入 deterministic-infeasible fast reject；该修改保持候选拒绝语义不变，但本轮
GPU 数据采于修改前，不能把它写成已测得的延迟改善。

## Artifact

- 路径：`artifacts/p6/h200_bf16_v6/transfer_service_qwen3coder30b_bf16_h200_perf_v1.json`
- 样本：36 条 bundle terminal record，35 条完成样本，14 个 shape bucket。
- 数据边界：仅 bundle 级 `offload_context/prefetch_context` 的 HiCache submit-to-complete。
- 排除：per-extent callback、native demand-load/write-back 和重复 command record。
- 支持：1 extent 的 4 MB 至 402 MB，以及 4 extents、6.437 GB 的 D2H/H2D。
- OOD：超出 size/extent 邻域的 shape 继续 fail closed。

该 artifact 描述显式 bundle submit-to-complete，不等同于纯 DMA 带宽。当前 telemetry
会包含 allocator、callback 和并发执行影响，这是 JointPlan 所需的保守服务成本边界。

## 固定 Trace

- 运行目录：`experiments/shadow/p6_shape_dedup_fixed/20260828T172421Z`
- 模型：Qwen3-Coder-30B-A3B-Instruct BF16。
- 配置：H200 BF16 v6、850K KV token、96 GiB Host、predictor-only shadow。
- Workload：预注册 w4 `native_subagent_2to3`，首次自然 JOIN 后结束。
- 完成：4/4 workflow、8/8 natural child RETURN、4/4 JOIN_SATISFIED。
- 负载：380 次 LLM、583 次工具调用，运行约 995.7 秒。
- 正确性：0 pending command/lease/obligation/reservation/transaction，受控 shutdown 通过。
- 压力：resident KV 峰值 17.79%，无策略性 D2H/H2D。

## 对比

| 指标 | 旧 fixed trace | 本轮 |
|---|---:|---:|
| eligibility checked | 769 | 995 |
| enqueued / checked | 85.96% | 71.96% |
| unchanged suppression | 13.91% | 27.94% |
| full risk result / 业务事件 | 67.35% | 50.16% |
| certificate stale | 48.96% | 38.67% |
| shape unsupported | 100.00% | 7.60% |
| planning P50 | 527 ms | 600 ms |
| planning P95 | 895 ms | 1,078 ms |
| planning P99 | 1,196 ms | 1,469 ms |

本轮 1,854 个 PREPARE_HOST 候选均无正收益且不可执行。主要原因不是 shape OOD，
而是 workload 没有产生 pressure-time recourse；最大 resident pressure 仅 17.79%。

## 下一门槛

只执行一次预注册 64-root 高压 predictor shadow。该轮不开放预测物理动作，仅验证：

- deterministic fast reject 是否降低完整 scenario evaluation 与 worker backlog；
- 高压时是否出现 action-aligned causal slack 和正 recourse 候选；
- planning freshness 是否足以发布 semantic intent；
- observed P5 路径和 shutdown 守恒不受影响。

若该轮仍未形成 HBM 压力，不继续重复相同 workload，也不据此调低风险门槛。
