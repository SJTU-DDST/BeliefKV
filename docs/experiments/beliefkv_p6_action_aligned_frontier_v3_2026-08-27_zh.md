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

## 7. 固定 Trace GPU Shadow

运行目录：

```text
experiments/shadow/p6_action_aligned_shadow_rerun/20260827T124236Z
```

固定输入为四个 project-distinct train workflow，运行
`native_subagent_2to3`，每个 workflow 在首个自然 JOIN 后受控停止。预测器和 risk
shadow 开启，predictive overlay、PREFETCH canary 和所有预测性物理动作关闭。

### 7.1 Agent 与系统正确性

| 指标 | 结果 |
|---|---:|
| Workflow | 4/4 semantic gate completed |
| FRESH child | 8 |
| Invocation RETURN | 12/12 |
| JOIN_SATISFIED | 4/4 |
| LLM request/result | 296/296 |
| Tool start/end | 439/439 |
| 运行时长 | 669.20 s |

最终 `no_pending_transactions=true`、
`shutdown_cleanup_did_not_mask_unresolved_transactions=true`，queue、command、lease、
reservation、restore obligation 和 retraction transaction 均为空。停止脚本在收到
shutdown ACK 后等待外层 launcher 超时，但 GPU worker 和模型进程已经退出；这不影响
运行时终态或预测聚合。

### 7.2 预测结果

| 指标 | 结果 |
|---|---:|
| Eligibility checked/enqueued | 769/661 |
| Worker completed/failed/dropped/pending | 661/0/0/0 |
| Risk result | 495 |
| PREPARE_HOST candidate evaluation | 1,869 |
| Positive-benefit candidate | 0 |
| Eligible candidate | 0 |
| Selected action | 495 observed baseline |

所有候选的 expected benefit 均为负，P50 为 -33.52 ms，最大值为 -18.13 ms。
这不是因为 trace 没有 WAIT_JOIN：四个 parent 都进入了 JOIN_WAIT；根因是 resident KV
最高仅约 11%，预测 future HBM overflow 为 0，提前 D2H 没有 future-pressure recourse
收益，只有传输与 Host residency 成本。

Action timing 在 1,191/1,869 个候选上可计算；其 live
`D2H p95 + guard` 的 P50/P95/P99 为 79.08/157.91/246.69 ms。714 个候选的
causal-slack probability 低于 0.9，另有 678 个 timing unavailable。WAIT_JOIN 的
dependency timing 主要使用 RCCG structural support，WAIT_TOOL 的 218 次支持仍为
backoff。当前 aggregate 尚不能把 678 个 unavailable 精确拆到缺失 child scenario、
已变化 invocation state 或 unsupported wait 类型，因此不能据此盲目补采数据。

### 7.3 上线阻塞

1. **没有压力机会**：当前短 trace 只验证 agent 语义和 predictor worker，不能验证
   PREPARE/COMMIT/PREFETCH 收益。
2. **Transfer artifact 不匹配**：runtime profile 仍引用旧的 transfer service artifact，
   其 provenance 已标记为 superseded/recalibration required；1,869/1,869 个候选均
   `shape_unsupported`。action-target anchor 可用于训练 operational tau，但不能替代
   online live-shape service model。
3. **后台计划过慢**：planning P50/P95/P99 为 527/895/1,196 ms；1,869 个 action
   certificate 中 915 个 stale，stale rate 48.96%。worker 与 observed path 隔离且没有
   backlog，但该延迟会错过短工具窗口。

因此本轮不开放任何 canary。正确顺序是：先重建当前 performance patch 的
shape-aware D2H/H2D artifact；按事件、context epoch 和 action package 去重后台评估；
补充 timing-unavailable 的细分归因；再对预注册的持续 backlog/high-pressure trace
运行一次 shadow。只有自然出现 fresh、正收益 package 时，才开放单笔
`PREPARE_HOST`，随后验证 beneficiary/COMMIT 和 latest-start `PREFETCH_GPU`。
