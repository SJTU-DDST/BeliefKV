# P6 Event-Aligned PREPARE 高压 Shadow Gate

日期：2026-09-16

## 1. 目的

快速验证 PREPARE 事件 latch 修复能否在真实高压 GPU 路径中做到：

1. `TOOL_START/WAIT_CHILD/WAIT_JOIN` 后使用同一 safe point 的最新 beneficiary hint；
2. 进入 action-local physical overlay 和 Predictive Risk worker；
3. 在 `latest_feasible_start` 前形成 fresh positive package；
4. 不执行预测物理动作，不用短 gate 评价吞吐收益。

## 2. 配置

- GPU：单张 NVIDIA H200；
- 模型：Qwen3-Coder-30B-A3B-Instruct BF16；
- runtime profile：`h200_bf16_v7`；
- KV pool：850,000 tokens；
- Host pool：96 GiB；
- workload：冻结 64-root manifest 中前 40 个 root，`native_subagent_2to3`；
- client in-flight：40；SGLang `max_running_requests=32`；
- predictor：baseline-adapted v5，process-isolated risk worker；
- `predictive_risk_shadow_enabled=true`；
- predictive JointPlan overlay、predictive transfer 和 prefetch canary 均关闭。

运行目录：

`experiments/shadow/p6_event_aligned_short/20260916T081602Z/predictive`

达到高压观察条件后人工发送 `SIGINT`，因此 `run_result.json` 中的
`KeyboardInterrupt` 是预注册的提前终止，不是运行失败。

## 3. 压力与触发链

- HBM peak：100%；
- HBM >= 80% 持续：322.56 秒；
- running request：高压阶段保持 31--32；
- PREPARE event latch：697；
- event-aligned hint：696；
- event-aligned risk publish：38；
- action-local overlay victim：1,070 个 victim summary，单次最多两个；
- risk worker：515 submitted / 515 completed / 0 failed / 0 dropped / 0 pending。

event-to-hint 延迟为 P50/P95/P99 `174.34/889.74/3236.16 ms`，最大
`4229.20 ms`。该指标包含“事件发生时尚无 beneficiary，等待后续 bounded seed hint”的
时间，不等同于单次 scheduler capture，但长尾仍需在物理 canary 中继续观测。

## 4. 候选与时序

- risk evaluation：315；
- candidate evaluation：345；
- positive-benefit candidate：38；
- validation 前仍 fresh：319/345，fresh rate 92.46%；
- validation 早于 latest-start 的 positive package：37；
- 最终选择 `PREPARE_HOST`：8；
- 其中 validation 早于 latest-start：7；晚于 latest-start：1。

37 个及时 positive package 的 validation margin：

- 最小：76.09 ms；
- P50：281.17 ms；
- P95：1343.36 ms；
- 最大：1388.44 ms。

8 个被选 PREPARE package 的最大预测收益为 284.47 ms；唯一晚到动作的 margin 为
`-310.14 ms`，证明提交端仍必须保留 latest-start 复验，不能只依赖 worker 选择结果。

## 5. 控制面开销

| 指标 | P50 | P95 | P99 |
| --- | ---: | ---: | ---: |
| safe-point delta capture | 0.211 ms | 0.692 ms | 1.393 ms |
| event-aligned safe-point capture | 0.213 ms | 0.711 ms | 1.057 ms |
| predictive planning | 20.76 ms | 43.81 ms | 59.50 ms |
| belief compose | 3.24 ms | 14.17 ms | 23.26 ms |
| scenario risk | 14.79 ms | 26.29 ms | 33.68 ms |
| trigger-to-validation | 76.28 ms | 673.72 ms | 967.45 ms |

此前数百毫秒的常态 belief composition 已消除，positive-candidate planning P95 低于
100 ms。同步 capture 的 P95/P99 仍略高于严格目标 `0.5/1 ms`，因此性能门槛仅部分通过。

## 6. 安全与动作边界

- predictive overlay 未启用，本轮没有 predictive D2H/H2D/COMMIT；
- 56 条 transfer telemetry 全部是 native `write_back`，`source_joint_plan_id=null`；
- Predictive worker failure：0；
- PageIndex/assertion/OOM：0；
- shutdown acknowledged；
- command、transaction、lease、reservation、restore obligation 均无遗留；
- `shutdown_cleanup_did_not_mask_unresolved_transactions=true`。

## 7. 裁决

1. **事件对齐功能门禁通过。** 修复后的在线路径首次在自然高压 workload 中产生真实
   positive PREPARE package，并有 37 个在 latest-start 前完成验证。
2. **候选价值门禁通过。** 8 次选择 PREPARE，不再是全部停在 closure、mirror 或 cheap
   opportunity probe。
3. **严格控制面门槛部分通过。** planning P95 已合格，但 safe-point P95/P99 与
   trigger-to-validation 长尾仍需控制。
4. **本轮不提供吞吐收益结论。** 预测物理动作明确关闭，不能与 baseline 比较 JCT、tok/s
   或 workflows/hour。

下一步应只开放一笔 `PREPARE_HOST` canary，并要求该动作在提交时仍满足 latest-start，随后
验证 D2H ACK、真实 beneficiary deficit、COMMIT 消费和 saved stall。不得直接开放无限预测
迁移或运行完整 A/B。
