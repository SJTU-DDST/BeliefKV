# P6 Schema-v4 Action-Aligned FrontierBelief

日期：2026-08-27

## 1. 结论

本轮没有重新采集 workflow，也没有访问封存的 `test_id`。系统复用 H200 BF16 的
64 个 train workflow 与 16 个 held-out calibration workflow，重新导出与在线 KV
动作对齐的 schema-v4 标签，并完成 LOPO、训练和 held-out calibration。

新模型解决了两个主要问题：

- 不再把固定的 10/100/1000/10000 ms survival 作为主要评价目标；
- 不再用统一 external-wait 驱动 PREPARE 与 PREFETCH，而是分别查询相反方向的
  action-specific probability。

当前 artifact 仍保持 `online_eligible=false`。它只允许进入事件驱动 shadow，
不能直接发出预测性物理动作。

## 2. 动作目标

`configs/p6/h200_bf16_perf_v1/action_target_contract.json` 冻结当前 performance
patch 的 transfer anchor：

| Direction | Bytes | Extents | P95 |
|---|---:|---:|---:|
| D2H | 6,436,945,920 | 4 | 249.775 ms |
| H2D | 6,436,945,920 | 4 | 692.422 ms |

对每个 WAIT_TOOL decision point，根据该上下文的 KV bytes 生成 live-scale
`tau = transfer_p95 + 25 ms guard`：

```text
PREPARE_HOST:
  P(tool release occurs after tau_d2h)

PREFETCH_GPU:
  P(tool release occurs within tau_h2d)
```

旧冻结语义行没有精确 extent morphology，因此当前 tau 是
`byte_scaled_from_current_patch_anchor`。这足以验证 action-aligned learning 和
shadow 控制链，但不足以开放 online action。在线 safe point 最终仍必须使用 live
bundle、extent count 和当前 transfer service curve 重新计算 tau。

`COMMIT_CPU` 不训练成独立分类头。它依赖未来 beneficiary readiness、HBM deficit、
exclusive reclaimable bytes 和 expected saved stall；这些量由 Causal Package Planner
使用局部 demand belief 与当前 allocator/Radix 状态组合，预测器不得绕过物理可行性。

## 3. 模型结构

`WAIT_TOOL` 按以下层次回退：

```text
role / wait_tool / tool_family / backend / command_class
  / active_tool_count / context_bucket / backend_pressure
```

每个真实 tool episode 在 workflow 内等权，避免长工具调用因 decision point 较多而被
过采样。`WAIT_JOIN` 和 `WAIT_CHILD` 不拟合统一 wall-clock delay，而由 RCCG 保留
child dependency，再在 scenario composer 中组合 child 的 LLM demand、tool survival
和 RETURN。

模型只发布局部分布。PREPARE、PREFETCH、COMMIT 的最终选择权仍属于同一个
JointPlan/Causal Package Planner，不新增第二个迁移策略源。

## 4. 数据覆盖

Train action target：

- 10,432 个 WAIT_TOOL decision row；
- PREPARE tau P50/P95：80.88/213.72 ms；
- PREFETCH tau P50/P95：179.92/548.17 ms；
- 主要 command episode：execute 5,301、ls 1,568、read_file 1,419、
  multi_tool 949；
- 只有 apply_patch 为明显稀疏类别，后续按 gap 定向补采。

Calibration action target：

- 2,846 个 WAIT_TOOL decision row；
- PREPARE tau P50/P95：83.15/297.25 ms；
- PREFETCH tau P50/P95：186.21/779.74 ms；
- 没有少于 4 个 episode 的 command class。

右删失只在 censor endpoint 已经跨过 tau 时提供可证明标签；censor 不会被当作工具成功
或 reentry。现有 calibration action rows 均有可判定 endpoint。

## 5. 训练与校准结果

LOPO 以 project-macro operational-tau action Brier 为第一目标，边界、token demand 和
required-head OOD 只作为次级目标。选择结果为：

- boundary max order：2；
- boundary/tool empirical minimum support：2；
- LOPO operational-tau Brier：约 0.0926。

Held-out Astropy/Sphinx calibration：

| Action | Operational-tau Brier | Episode weight |
|---|---:|---:|
| PREPARE_HOST | 0.0641 | 16.0 |
| PREFETCH_GPU | 0.1222 | 16.0 |

这比旧固定 100/1000 ms 的 0.207/0.224 更贴近当前动作尺度，但不能直接横向解释为同一
测试目标上的绝对提升。

required tool head availability 为 99.72%，action-specific OOD fallback 为 0.108%。
support 主要来自 command/family/state backoff：

- role + command backoff：11.19/16；
- role + family backoff：2.53/16；
- role + state backoff：2.23/16；
- unavailable：0.045/16。

因此“available”不再被误写成“exact”。跨项目 exact support 很少是预期现象，正式报告
必须同时展示 support path。

## 6. 当前门禁

允许：

- predictor-only event-driven shadow；
- 记录 predicted package、positive-benefit coverage、worker latency 和 plan freshness；
- 使用 observed P5 作为唯一实际决策源。

禁止：

- predicted PREPARE/COMMIT/PREFETCH 物理 dispatch；
- predictive retraction；
- early dispatch 或 run-to-action。exact incremental boundary 仍不可用；
- 使用本轮结果评价 JCT 或 workflows/hour。

shadow 出现稳定、fresh、正收益 package 后，才依次开放单笔
`PREPARE_HOST -> COMMIT_CPU -> PREFETCH_GPU` canary。若特定 command class 或
JOIN causal state support 不足，只补采 24--32 个预注册 characterization workflow，
不访问 `test_id`。
