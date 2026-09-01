# P6 Event-Driven Risk Shadow64 GPU Gate

日期：2026-09-01

## 结论

提交 `0c86eb6` 修复了普通 agent 因果事件只更新 RCCG、却不触发预测评估的缺口。
相同 64-root H200 高压运行中，`TOOL_START`、`WAIT_CHILD/WAIT_JOIN`、
`RETURN/JOIN_SATISFIED/TOOL_RETURN` 通过独立 RISK_EVAL 路径复用最近 observed seed，
没有恢复全局 full-plan trigger。

同步控制面门槛通过：safe-point capture P95 为 0.314 ms，predictive submit P95
为 0.023 ms；Joint worker 和 Predictive worker 均无失败、积压或遗留事务。
但预测价值门槛未通过：在线只评估出 6 个 PREPARE_HOST package，全部为负收益，
没有 fresh-positive package，因此不能开放 PREPARE_HOST canary。

本轮还发现全局 `transfer_epoch` 会错误地使非破坏性 PREPARE_HOST 证书失效。
运行后已将该门禁收窄为仅约束破坏性的 RECLAIM_AND_PREFETCH；同一冻结 snapshot
离线重放后仍为零正收益，说明 stale 修复不是在制造正例。

## 冻结配置

- 代码：`0c86eb6`
- 运行目录：`experiments/shadow/p6_event_driven_risk_shadow64/20260901T135927Z`
- 模型：Qwen3-Coder-30B-A3B-Instruct BF16
- GPU：NVIDIA H200 NVL
- KV pool：850,000 tokens
- Host pool：96 GiB
- `max_running_requests=32`，CUDA Graph 最大 batch 32
- 64 个预注册 root 同时提交，`native_subagent_2to3`
- observed P5 在线；predictor/risk 只读；所有 predictive physical action 关闭

## 高压与活性

- 64 workflows、192 invocation/context
- 高压阶段保持约 31--32 running、95--96 waiting
- HBM 峰值 99.9998%
- HBM >=80% 的观测窗口约 462.7 秒
- 最大 migratable KV 68.73 GB
- 关闭前没有 command、lease、reservation、obligation 或 transaction
- `SHUTDOWN_ACK` 正常，shutdown cleanup 没有掩盖未解决事务

GPU utilization 的均值为 11.72%，P50/P95/P99 为 0/57/92%。该运行按门槛提前
终止，不作为吞吐或 workflow JCT 结果。

## 控制面结果

| 指标 | 结果 |
| --- | ---: |
| scheduler steps | 5,923 |
| semantic/risk delta submissions | 2,696 |
| event-driven risk submissions | 1,538 |
| full plans | 9，约 0.152%/step |
| safe-point capture P50/P95/P99 | 0.166/0.314/1.385 ms |
| enqueue P95 | 0.028 ms |
| Predictive worker submitted/completed/failed/pending | 8/8/0/0 |
| risk queue wait P95 | 30.40 ms |
| risk compute P50/P95 | 335.70/636.36 ms |
| trigger-to-validation P50/P95 | 841/2,606 ms |

full-plan 慢路径仍为 snapshot build P95 291.98 ms、plan compute P95 602.72 ms，
但只触发 9 次。当前同步 steady path 已不再是首要阻塞；异步 risk 的端到端新鲜度
仍需通过更早的 beneficiary hint 改善，而不是继续压低普通 semantic delta 的开销。

## Predictive 结果

在线 6 个候选全部为 PREPARE_HOST：

- expected benefit P50 为 -4.05 ms，最大值 -3.94 ms；
- expected recourse credit 全部为 0；
- 6/6 证书因全局 `transfer_epoch` 变化被标 stale；
- 在线 candidate-local transfer estimate 将 6/6 标为 shape unsupported。

收窄 transfer epoch 门禁后，对 4 个冻结高压 snapshot 使用同一 morphology-aware
artifact 重放：

- 4/4 为 shape-supported pressure candidate；
- 最大预测 HBM deficit 4.57 GB；
- positive/eligible 仍为 0/0；
- latest feasible D2H start 已落后当前时刻约 913--984 ms；
- 16 个 scenario 中，10 个为 `shadow_completes_after_pressure`，6 个为
  `projected_beneficiary_hbm_block_unavailable`。

因此本轮的真实价值阻塞是：cached observed source plan 直到 beneficiary 已接近或已经
受到 HBM 阻塞时才暴露 projected requirement，PREPARE_HOST 已经错过隐藏 D2H 的窗口。
在线与离线 shape support 不一致也需要统一，但即使使用离线 shape-aware estimate，
当前候选仍没有 recourse。

## 裁决与下一步

已通过：事件驱动 RISK_EVAL、同步 safe-point 开销、worker latest-wins 活性、真实
page delta 补齐、PageIndex 和 shutdown 正确性。

未通过：action freshness、fresh-positive package 和 PREPARE_HOST canary 门槛。

下一步只做两项最小修改：

1. 向 Predictive worker 发布最新 bounded observed seed 的 deferred request hint，
   让 ProjectedReclaimRequirement 在真实 deficit 之前出现；不重建完整 JointPlan。
2. 统一在线 candidate-local transfer estimate 与冻结 replay artifact 的 shape support
   语义。

修改后先重放本轮 4 个冻结 snapshot。只有 projected block deadline 转为未来且出现
正收益 package，才再运行一轮短高压 shadow；在此之前不开放任何 predictive action。
