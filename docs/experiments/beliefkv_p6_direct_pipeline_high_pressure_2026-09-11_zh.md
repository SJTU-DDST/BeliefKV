# P6 Direct Pipeline 高压 Shadow 与控制面优化

日期：2026-09-11

## 结论

本轮 64-root H200 predictor-only 高压实验首次同时满足：真实在线 Risk worker
执行、正收益 PREPARE package、fresh certificate，以及 validation 早于
latest feasible start。机制门槛已经通过，但 55.2% 的 action-specific certificate
仍因真实 invocation revision/state 变化而 stale，不能据此开放多笔预测动作。

实验后完成的粒子缓存和 safe-point probe 优化在冻结 replay 上保持 128 粒子决策完全
一致，并将 belief compose P95 降至 18.46 ms。该部分尚未经过 GPU 复验。

## 冻结配置

- 运行目录：
  `experiments/shadow/p6_direct_pipeline_shadow64/20260911T075009Z`
- GPU/模型：H200 NVL GPU0，Qwen3-Coder-30B BF16
- Runtime profile：`h200_bf16_v6`
- HBM KV pool：850K tokens
- Host KV pool：96 GiB
- CUDA Graph：max batch 32
- 服务并发：`max_running_requests=32`
- Workload：64-root `native_subagent_2to3`，all-roots eager
- 策略：P5 observed JointPlan + predictive risk shadow
- Predictive physical action：关闭
- Frontier particles：128，top-K 4

本轮采用有界停止，没有等待 64 个 workflow 全部自然完成。停止后执行两阶段 server
shutdown；`shutdown_state=acknowledged`，running/waiting 均为 0，command、lease、
reservation、restore obligation 和 transaction 均无遗留。由于 workload 受控中止，
本轮不用于 JCT 或 workflows/hour 结论。

## 在线结果

HBM 峰值为 99.991%，高压条件成立。Predictive worker 58/58 完成，0 failed、
0 dropped、0 pending：

| 指标 | 结果 |
| --- | ---: |
| closure-complete risk evaluation | 58 |
| positive-benefit candidate | 9 |
| eligible candidate | 4 |
| selected PREPARE_HOST | 4 |
| action certificate fresh/stale | 26/32 |
| fresh positive before latest-start | 1 |

32 个 stale certificate 中，24 个来自同一个 invocation 的持续 TOOL_START/TOOL_END
推进。这是真实因果状态变化，不能通过忽略 revision 或放宽 certificate 消除。

## GPU 控制面

| 路径 | P50 | P95 | P99 |
| --- | ---: | ---: | ---: |
| safe-point delta capture | 0.210 ms | 0.750 ms | 1.620 ms |
| action-local overlay capture | 0.002 ms | 0.519 ms | 0.648 ms |
| eligibility | 0.221 ms | 0.352 ms | 0.383 ms |
| belief compose | 24.96 ms | 35.87 ms | 38.47 ms |
| scenario risk | 10.68 ms | 19.29 ms | 62.97 ms |
| predictive compute | 38.70 ms | 51.85 ms | 99.05 ms |
| action validation | 0.131 ms | 0.164 ms | 1.025 ms |
| trigger to validation | 721.7 ms | 2877.8 ms | 9872.5 ms |

预测已经在独立进程执行，58 次评估期间没有 worker backlog。direct forwarding 避免了
scheduler 先消费 Joint result 再提交预测任务的额外一轮延迟。剩余
trigger-to-validation 尾延迟主要是预测完成后等待下一次 scheduler safe point；当前
16K chunked prefill 期间不能在 Python 层安全地提前验证。

完整 observed full plan 为 33/6315 scheduler steps，约 0.52%。其 plan compute P95
仍为 265.5 ms，但不再出现在每个普通 semantic event 上。该路径与 action-local
predictive risk 分开统计。

## 实验后优化

1. `FrontierScenarioComposer` 使用固定 common-random quantile，并以本地采样实际读取的
   invocation、JOIN、communication 和 prediction 字段作为 cache key。时间戳等无关
   revision 不再使 128 个本地粒子全部失效。
2. `PredictiveRiskShadowObserver` 将单槽 belief cache 改为 128-entry LRU，允许动态
   RCCG 在多个近期 semantic state 之间复用粒子。
3. 同一 projection/target 的两个 victim candidate 共享 baseline 与 conservative
   baseline timeline；PREPARE 只计算各自 action delta。
4. 同一 safe point 复用一次 `RuntimeResourceObservation`。最多 4 个 beneficiary probe
   共用 running request 索引、HBM 可用量和 running growth，只保留 chunked beneficiary
   的条件增量按候选计算。

冻结的 12 个高压 snapshot 使用 128 粒子重放：

| 指标 | 结果 |
| --- | ---: |
| selected action | 2 PREPARE / 10 baseline |
| 与优化前动作一致 | 12/12 |
| positive / eligible | 4 / 2 |
| belief compose P50/P95 | 12.19 / 18.46 ms |
| scenario risk P50/P95 | 8.28 / 27.18 ms |
| total planning P50/P95 | 21.91 / 40.33 ms |
| cold-start max planning | 91.22 ms |

64 粒子会将同一输入从 2 个 PREPARE 改为 1 个，因此未采用降粒子数这一优化。

## 门禁与下一步

- 已通过：独立 predictive process、worker 活性、action-local validation、至少一笔
  fresh/timely positive package。
- 尚待 GPU 复验：safe-point 观测复用和公共 beneficiary probe 是否将 capture 恢复到
  P95/P99 小于 0.5/1 ms。
- 仍需控制：真实因果变化导致的 stale，而不是通过放宽 read-set 掩盖。
- 下一次 GPU 运行应直接合并短高压性能复验与单笔 `PREPARE_HOST` canary；若 live
  rematerialization 或 latest-start 门禁失败，立即回退 observed P5。

本轮未生成 KV 时间线，因为实验是 predictor-only、受控提前停止，未形成可归因的在线
物理动作闭环。
