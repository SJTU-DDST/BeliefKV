# P6 PREPARE Bounded Commit GPU Gate

日期：2026-09-11

## 结论

本轮证明 bounded live rematerialization 有效，但旧 5 ms wall-clock 后验回滚门禁使
canary 仍未产生物理命令。84 次在线 risk evaluation 产生 35 个 selected PREPARE，
42 个 positive intent 在 certificate fresh 且 validation 早于 latest-start 时到达；
20 次 safe-point 物化成功，全部因 wall time 超过 5 ms 回滚。

相较上一轮，physical commit P95 从 244.78 ms 降至 19.03 ms，说明全 closure 枚举已被
消除。剩余 wall time 不能再直接解释为 19 ms 的有效 CPU 工作：本轮没有记录 thread CPU
time，且高压运行同时存在 scheduler、observed worker 和 workload CPU 竞争。实验后已将
预算拆成 thread CPU 和 deadline 两个约束，下一轮将直接测量两者。

## 冻结配置

- 运行目录：
  `experiments/canary/p6_prepare_host_physical_commit_fast/20260911T112324Z`
- GPU/模型：H200 NVL GPU0，Qwen3-Coder-30B BF16
- Runtime profile：`h200_bf16_v6`
- HBM KV pool：850K tokens；Host KV pool：96 GiB
- CUDA Graph：max batch 32；`max_running_requests=32`
- Workload：冻结 64-root `native_subagent_2to3`，all-roots eager
- 策略：P5 observed JointPlan + P6 predictive overlay
- 权限：单笔 `PREPARE_HOST`；COMMIT/PREFETCH/retraction 关闭

本轮在达到 32 个以上 closure-complete candidate 且物理预算失败原因明确后受控停止，
不用于 workflow JCT。最终 shutdown acknowledged，command、lease、reservation、
obligation 和 transaction 全部归零。

## 在线漏斗

| 指标 | 结果 |
| --- | ---: |
| Predictive worker submitted | 100 |
| worker failed / dropped / pending | 0 / 0 / 0 |
| risk evaluation | 84 |
| selected PREPARE | 35 |
| certificate fresh / stale | 72 / 13 |
| fresh positive before latest-start | 42 |
| published intent / publish rejected | 34 / 1 |
| successful live materialization | 20 |
| rejected safe-point validation | 14 |
| queued predictive command | 0 |

运行时提前发出了 20 条 `predictive_semantic_intent_committed`，随后 caller 才执行预算
回滚。因此这些事件只能解释为旧语义下的 materialized，不能视为真实 JointPlan commit。

## 控制面

| 路径 | P50 | P95 | P99 |
| --- | ---: | ---: | ---: |
| safe-point delta capture | 0.227 ms | 0.664 ms | 1.004 ms |
| action-local overlay capture | 0.003 ms | 0.494 ms | 0.781 ms |
| eligibility | 0.220 ms | 0.320 ms | 0.401 ms |
| belief compose | 14.20 ms | 32.68 ms | 38.79 ms |
| scenario risk | 11.91 ms | 21.28 ms | 45.03 ms |
| predictive total | 29.09 ms | 55.89 ms | 84.31 ms |
| predictive safe-point commit | 11.19 ms | 19.03 ms | 20.59 ms |
| trigger to validation | 448 ms | 1739 ms | 2485 ms |

只有 3 次 observed full plan。Predictive worker 无积压，主要异步路径维持在百毫秒以下；
本轮阻塞项是物理动作预算语义和提交阶段 CPU/wall time 的归因。

## 实验后修改

1. `predictive_semantic_intent_committed` 延迟到预算和 latest-start 检查通过后发出；物化
   与 commit 分开计数。
2. 5 ms 门禁约束当前线程实际 CPU time。wall time 继续记录；若完成时越过动作 deadline，
   无论 CPU time 多小都回滚。
3. shape-aware transfer estimate 使用按完整查询条件缓存；新 telemetry 或 warm-start
   载入立即使缓存失效。
4. live RCCG 的 communication evidence 使用 `(source, target)` 直接索引，不再为每次
   safe-point validation 重建全图 edge map。

## 下一门槛

下一次仍为短 64-root 单笔 PREPARE gate，要求：

- `validation_cpu_ms <= 5 ms`；
- validation 完成早于 latest-start；
- 至少一笔 `commit -> queue -> D2H -> ACK -> terminal -> outcome`；
- ID、actual bytes、extent count 守恒；
- worker failure/pending 为 0，无 orphan transaction。

本轮未通过物理闭环门槛，因此不生成 KV 时间线。
