# P6 自然 PREPARE_HOST Canary：无可归因机会

日期：2026-09-12

运行代码：`b66709c`

运行目录：
`experiments/canary/p6_prepare_natural_canary/20260911T162133Z`

## 结论

本轮按预注册边界运行自然单动作 PREPARE canary：关闭 deterministic injection，允许
最多一笔 `PREPARE_HOST`，HBM 超过 80% 后继续观察至少 5 分钟。最终没有发布自然
positive intent，也没有物理命令。该结果是正确的 no-action，不是模型负收益、证书 stale
或迁移失败。

峰值 HBM 为 72,090,255,360 / 83,558,400,000 bytes，即 86.28%；80% 以上持续
413.0 秒，最大 migratable KV 为 68,793,630,720 bytes。尽管系统有大量可迁移 victim，
前 2--4 个 bounded-seed beneficiary 始终没有形成 projected HBM deficit：367 次 probe
为 `capacity_available`，242 次为 `slot_only`，future-growth deficit 始终为 0。

因此本轮证明的是：总体 HBM pressure 和 migratable bytes 不是 PREPARE 的充分条件；
没有明确 beneficiary 时，P6 会在物理化 victim 和运行 FrontierBelief 之前 fail closed。

## 配置

- Qwen3-Coder-30B BF16，H200 NVL，H200 v6 profile；
- KV pool 850K tokens，Host pool 96 GiB，max running 32；
- 64 个预注册 native-subagent root 同时提交；
- observed JointPlan + predictive risk overlay；
- `--enable-shadow-transfers`，PREPARE canary limit=1；
- deterministic micro gate、COMMIT、PREFETCH 和 TransferEngineV2 均关闭。

## 漏斗

| 阶段 | 数量 |
|---|---:|
| bounded beneficiary hint refresh | 609 |
| beneficiary probe: capacity available | 367 |
| beneficiary probe: slot only | 242 |
| action-local victim overlay | 0 |
| predictive worker submission | 0 |
| positive/fresh-positive intent | 0 |
| PREPARE command | 0 |

高压末期的代表性 hint 具有约 406.6 MB future growth；当时全局 HBM 仍有约 11.5 GB
headroom。即使存在约 55--69 GB migratable KV，迁移也不能提前解锁该 beneficiary。
当前 evidence 不能判断 workload 若继续增长到 95%--100% 后是否会出现机会，因此不能将
结果外推为“该 workload 永远没有预测迁移价值”。

## 控制面

- safe-point capture P50/P95/P99：0.227/0.433/1.083 ms；
- action-local overlay early-return P95：0.0028 ms；
- risk-event delta apply P95：0.851 ms；
- risk-event materialization P95：2.083 ms；
- Joint worker 2,880/2,968 completed，88 次 capacity-one coalesce，0 failed、0
  dropped、0 pending；
- Predictive worker 0 submission，因而本轮不能评价真实 risk compute/GIL 干扰。

P95 同步门槛通过；P99 比 1 ms 严格目标高 0.083 ms。scheduler-step P99 和 Radix sync
仍有长尾，但本轮没有 predictive compute，不能归因于 P6 风险评估。

## 正确性

- 无 command、lease、reservation、restore obligation 或 transaction 遗留；
- shutdown ACK 完整，所有 correctness gate 为 true；
- analyzer 状态为 `no_positive_action`，无 orphan intent；
- Host 使用和策略性 D2H/H2D 均为 0。

## 裁决

自然 PREPARE canary 未执行动作，不能报告收益，也不开放 COMMIT/PREFETCH。下一次价值
实验不应把 80% HBM 单独视为有效机会门槛；应预先冻结为“出现
`future_growth_deficit>0`，或 HBM 达到更接近真实 admission deficit 的区间”后再开始
观察窗口。机制门禁已独立通过，不需要重复注入实验。

