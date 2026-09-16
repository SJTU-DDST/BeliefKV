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
4. 候选器检查前四个 target。固定 5% HBM 单笔上限已经删除：优先按实时 free HBM 选择完整
   prefetch；完整 context 放不下时，选择 ancestor-closed partial prefix，而不是静默跳过大 context。
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
- safe-point rematerialization 严格满足实时 HBM 容量证书；
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

## 容量资助路径

固定 5% 上限删除后的在线开发 gate 表明，首批真实 PREFETCH target 出现在约 99.8% HBM
占用时。此时 free-HBM-only byte budget 近似为零，即使 partial prefix 已支持，也无法生成可执行
候选。同期一笔真实 `PREPARE_HOST` 已完成，说明传输机制本身可用，缺口在于 reclaim 与 prefetch
没有形成同一个 causal package。

当前代码新增受限的 `RECLAIM_AND_PREFETCH`：

1. 只选择已经拥有完整 CPU shadow、可执行零拷贝 `COMMIT_CPU` 的 parked victim；不为 GPU-only
   victim隐式增加另一套迁移协议。
2. 候选仍限制为 `1 target x 1 victim`，partial target 必须是 ancestor-closed prefix。
3. safe point 同时重新物化 victim 与 target，并验证 victim exclusive reclaim、target copy bytes、
   context epoch、generation 与 HBM 资源证书。
4. 数据面严格执行 `COMMIT_CPU ACK -> partial H2D enqueue -> H2D ACK -> service lease`。H2D
   不得在 victim ACK 前入队；实际 reclaim 小于证书、deadline 已过或 H2D enqueue 失败时显式终止。
5. COMMIT 后失败保留 victim 的 CPU copy，不产生 restore debt；shutdown 通过现有显式 terminal
   cancellation 清理 staged transaction。

该节点已通过候选、JointPlan、worker 和 staged ACK 控制面测试，但尚未通过真实 GPU 在线闭环，
因此不能据此声明 PREFETCH 吞吐收益。下一次短高压 gate 只需要验证第一笔自然产生的：

```text
RECLAIM_AND_PREFETCH
-> victim COMMIT_CPU ACK
-> target partial H2D ACK
-> PrefetchServiceLease
-> target first GPU service
```

若没有 commit-ready victim，则继续由现有 `PREPARE_HOST` 建立 CPU shadow，后续事件再形成交换
候选；不会为了制造正例提前驱逐 GPU KV。

## Funded-prefetch 在线 gate

运行目录：
`experiments/shadow/p6_funded_prefetch_gate/20260916T161833Z/predictive`

本轮在高压后达到预注册的提前停止条件并受控关闭。运行时共完成 83 次 predictive eligibility，
风险规划选择 12 个 `PREPARE_HOST`，发布 11 个 semantic intent；11 个 intent 全部被 safe point
拒绝。拒绝原因均包含：

- `morphology_slack_expired`；
- `transfer_cannot_finish_before_beneficiary_block`；
- 其中 5 个还包含 `transfer_cannot_finish_before_low_window`。

典型候选的 beneficiary 约 21 ms 后发生预测阻塞，而 D2H P95 约为 838 ms；同时单 victim
只能回收约 67 MB，预测缺口约为 416 MB。该候选即使不存在 worker delivery 延迟也无法完成，
因此此前的 `expected_benefit_ms > 0` 属于价值模型假阳性，不是可以通过放宽 freshness 门禁执行的
机会。代码现在在发布 intent 前同时要求：

1. `predicted_block_time > D2H_p95 + commit_guard`；
2. `victim_reclaim_bytes >= predicted_deficit_bytes`。

本轮 `prefetch_target_count` 为 0 的另一个原因是 target 发现语义错误：bounded admission hint
指向的是当前 deferred beneficiary，该 context 通常已经 GPU resident，不能作为 H2D target。现在
target 从可见 parked invocation 中选择 CPU-resident、GPU-missing 的 context；target 与 victim 必须
不同。即使 target 尚未生成 native request，也允许形成 context-level funded prefetch，后续仍由
reentry epoch、latest-start 和 safe-point rematerialization 约束动作。

关闭时所有 correctness gate 均通过：无 pending transaction、command、lease 或 reservation，且
shutdown cleanup 没有掩盖未解决事务。该轮证明在线漏斗和拒绝保护正确，但没有产生实际
`PREFETCH_GPU`，因此仍不能声明吞吐收益或线上 prefetch recall。
