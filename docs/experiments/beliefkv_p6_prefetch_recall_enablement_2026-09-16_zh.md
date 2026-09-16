# P6 PREFETCH_GPU 召回与执行闭环

日期：2026-09-16

## 目标

快速补齐可执行的预测式 `PREFETCH_GPU`，不重新采集 24--32 个 workflow。现有正式
64-train 提供基础语义分布，最新 40-root observed baseline 只作为 development adaptation，
独立 16-workflow calibration 用于概率校准和阈值选择；test_id 保持封存。

## 修改

1. FrontierBelief 为 PREPARE/PREFETCH 分别保存 action-aligned calibration。
2. PREFETCH 使用 calibration 上的 recall-oriented F2 阈值，不再固定使用 0.5。
3. 风险规划仍要求正 Brier skill，并保留 HBM、transfer、latest-start 和净收益门禁。
4. 候选器检查前四个 target，跳过超过 5% HBM canary 上限的完整 context，最多生成一个
   PREFETCH package。
5. parked context 尚无 native request 时也可生成 PREFETCH intent；H2D ACK 后建立 5 秒
   `PrefetchServiceLease`，在下一次 reentry request 出现时按 context identity 绑定并提升 admission
   优先级。首次 GPU service 前禁止该 context 被反向迁出，首个 service、终态或超时立即释放。
6. development-only v3 高压计划绑定新模型，并只允许一笔 in-flight PREFETCH。
7. development artifact 默认没有物理动作权限；只有显式
   `allow_development_predictor_canary=true` 的 bounded gate 可以覆盖该门禁，正式实验禁止使用。

## Calibration

模型：
`experiments/models/frontier_belief_h200_bf16_v6_prefetch_recall_calibrated.json`

| 指标 | 固定 0.5 | 学习阈值 0.1850 |
| --- | ---: | ---: |
| Recall | 20.34% | 64.67% |
| Precision | 68.93% | 36.17% |
| 正例率 | 17.29% | 17.29% |

PREFETCH Brier skill 为 +15.80%。召回约提升 3.18 倍，代价是 classifier precision 下降；
这在当前分层架构中可接受，因为 classifier 后仍有确定性物理门禁和 scenario net-benefit
过滤。

## 证据边界

- 这是 development-only 校准结果，不是 test 或线上准确率。
- baseline adaptation 与后续同 workload gate 重叠，不能用其声明泛化。
- 当前修改及 CPU 契约证明模型能形成 context-level PREFETCH，runtime 能在 H2D ACK 后保护
  residency 并绑定下一次 reentry request；真实 GPU H2D/ACK/service 闭环及吞吐收益仍需短高压
  GPU gate 测量。
- 若线上 recall 仍低，下一步只补采缺失 tool/command class，而不是继续全局降低阈值。

## 下一门槛

短高压 gate 需要同时满足：

- 至少一笔 fresh、timely、正收益 PREFETCH；
- `H2D -> ACK -> service lease -> first GPU service` 完整；
- worker failure 为 0；
- H2D 后 5 秒内无无收益反向迁移；
- safe-point rematerialization 未绕过 5% HBM 上限；
- 单独报告候选 recall funnel，不能把 64.67% calibration recall 当作线上 recall。

## 第一轮 GPU gate 与时序修复

运行目录：
`experiments/shadow/p6_prefetch_recall_short/20260916T104314Z/predictive`

本轮在发现时序契约问题后受控停止，没有执行预测物理动作：

- HBM peak：38.10%；
- 携带真实 parked victim overlay 的 hint：69；
- predictive eligibility：43；
- semantic intent：2，均为 `PREPARE_HOST`；
- 两个 intent 均因 transfer 无法在 beneficiary/low-window deadline 前完成而被 safe point 拒绝；
- PREFETCH H2D、service lease 和遗留 transaction：0。

该 gate 暴露并修复了三个问题：

1. action probability 的 calibrated threshold 不能直接作为原始 wait CDF quantile。现在先反解
   Platt calibration，再用 raw threshold 计算 PREFETCH 时间窗。
2. context-only PREFETCH 不再携带无关 observed execution order，避免 active request turnover 使
   独立 residency action 失效。
3. service lease 固定到 target invocation 和唯一 reentry context epoch；若 H2D ACK 到达时目标
   epoch 已获得 GPU service或已经越过，直接判为 late prefetch，不再绑定后续请求。

因此第一轮只能证明线上漏斗已到达 action-local physical eligibility，不能作为 PREFETCH 召回率或
吞吐收益证据。修复后的真实 GPU gate 才是下一证据节点。
