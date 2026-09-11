# P6 PREPARE Canary 物理提交门禁

日期：2026-09-11

## 结论

本轮 64-root H200 高压运行证明预测价值路径已经稳定产生可执行语义意图，但没有完成
预测式物理动作。157 次在线 risk evaluation 产生 41 个正收益候选、37 个 eligible
`PREPARE_HOST`，其中 18 个在 certificate fresh 且 validation 早于 latest-start 时到达。
然而 37 个 intent 全部在 safe-point live rematerialization 阶段被拒绝，实际
predictive D2H 为 0，因此本轮不能宣称 canary 或 saved-stall gate 通过。

实验有效暴露了两个确定的软件问题：PREPARE safe point 枚举同一 context 的全部
closure，导致物理提交 P95 达到 244.78 ms；risk 阶段使用保守 interference envelope
时，safe point 因没有更新的 live stall 样本再次拒绝同一证书。两项均已在实验后修复。

## 冻结配置

- 运行目录：
  `experiments/canary/p6_prepare_host_direct_pipeline/20260911T095523Z`
- GPU/模型：H200 NVL GPU0，Qwen3-Coder-30B BF16
- Runtime profile：`h200_bf16_v6`
- HBM KV pool：850K tokens
- Host KV pool：96 GiB
- CUDA Graph：max batch 32
- 服务并发：`max_running_requests=32`
- Workload：64-root `native_subagent_2to3`，all-roots eager
- 策略：P5 observed JointPlan + P6 predictive overlay
- Predictive 权限：单笔 `PREPARE_HOST` canary；COMMIT/PREFETCH/retraction 关闭

本轮采用受控停止，不用于 workflow JCT 或 workflows/hour。停止后
`shutdown_state=acknowledged`，running/waiting 和所有 command、lease、reservation、
obligation、transaction 均归零；shutdown cleanup 没有掩盖未完成事务。

## 在线漏斗

| 指标 | 结果 |
| --- | ---: |
| Predictive worker submitted/completed | 163/163 |
| worker failed/dropped/pending | 0/0/0 |
| risk evaluation | 157 |
| positive candidate | 41 |
| eligible / selected PREPARE | 37/37 |
| certificate fresh/stale | 92/48 |
| fresh positive before latest-start | 18 |
| semantic intent published/rejected | 37/37 |
| predictive physical command | 0 |

旧运行把 37 次“safe-point 已拒绝且验证超过预算”也计入
`seed_safe_point_budget_fallback`。该计数不能解释为 37 个本可提交动作仅因预算回退；
实验后已拆分为 successful commit budget fallback 和 rejected validation budget exceeded。

37 次拒绝允许原因重叠，主要包括：缺少 live shape stall 证据 36 次、无法在 beneficiary
block 前完成 22 次、morphology slack 过期 20 次、无法在 low window 前完成 8 次；另有
1 次 bundle envelope 变化和 1 次 lock/busy。

## 控制面

| 路径 | P50 | P95 | P99 |
| --- | ---: | ---: | ---: |
| safe-point delta capture | 0.240 ms | 0.766 ms | 1.283 ms |
| action-local overlay capture | 0.002 ms | 0.571 ms | 0.820 ms |
| eligibility | 0.221 ms | 0.346 ms | 0.403 ms |
| belief compose | 17.60 ms | 29.98 ms | 39.32 ms |
| scenario risk | 11.11 ms | 15.98 ms | 26.64 ms |
| predictive total | 31.30 ms | 45.32 ms | 71.66 ms |
| predictive safe-point commit | 93.02 ms | 244.78 ms | 671.32 ms |
| trigger to validation | 659 ms | 3067 ms | 12118 ms |

同步 semantic/overlay 路径接近目标，predictive compute 也已低于 50 ms P95；本轮明确的
第一瓶颈是物理 safe-point commit。完整 observed full plan 仅 17/3447 次 publication，
但其 compute P95 仍为 423.49 ms，继续作为低频后台技术债，不阻塞单笔 PREPARE gate。

## 实验后修复

1. PREPARE 只请求一个最大 exclusive shadow closure，不再枚举 context 的全部 root
   closure；PREFETCH 仍保留原语义。
2. bundle ancestor validity 在单次 closure 内记忆化，消除链式 Radix 上重复祖先遍历。
   160-extent CPU micro 中，bounded preview 从约 18.6 ms 降到 5.76 ms；旧全枚举路径
   从约 1786.5 ms 降到 371.2 ms。真实候选只有 2--16 extents，预计提交路径低于 5 ms，
   但仍需 GPU 复验。
3. safe point 复用本轮已经构造的 runnable frontier，避免二次全队列扫描。
4. summary shape 使用 copy bytes 和 action count 构造 O(1) live fingerprint，不再逐页生成
   shape bundle。
5. live service curve 缺少 stall 样本时，复用 intent 已认证的 conservative interference
   envelope；只有新 live evidence 超过 envelope 才拒绝。
6. performance mode 保留低频 publish/reject/commit/outcome 和 predictive transfer
   correctness 事件，下一轮可完整验证 commit、queue、transfer、ACK、terminal、outcome
   的 ID/bytes/extent 守恒。

## 下一门槛

下一次只运行短 64-root 单笔 PREPARE gate。必须同时满足：

- 至少一笔 `PREPARE_HOST -> queue -> D2H -> ACK -> terminal`；
- `predictive_safe_point_commit` P95 小于 5 ms，或单样本小于 5 ms；
- canary attribution chain 的 ID、actual bytes 和 extent count 守恒；
- worker failure/pending 为 0，无 orphan transaction；
- beneficiary 后续真实消费 CPU shadow，才能进一步报告 saved stall。

本轮不生成 KV 时间线，因为预测式物理动作闭环没有发生。
