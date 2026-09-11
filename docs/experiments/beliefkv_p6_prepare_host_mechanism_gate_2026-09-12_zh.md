# P6 PREPARE_HOST 单动作机制门禁

日期：2026-09-12

运行代码：`6f9a9f7`

运行目录：
`experiments/micro/p6_prepare_mechanism_gate/20260911T160635Z`

## 结论

确定性单动作 `PREPARE_HOST` 机制门禁通过。注入的测试 intent 在一次尝试内完成
safe-point 重物化、JointPlan commit、command queue、真实 D2H、ACK 和 transaction
terminal。传输 766,083,072 bytes、2 个 extent，五段 ID、bytes 和 extent count
全部守恒，无 orphan command、lease、reservation 或 transaction。

本轮只证明预测动作的数据面和事务路径可执行，不证明 FrontierBeliefModel 产生了自然
正收益动作。注入证据已明确标记为 `injected_mechanism_gate`；修正后的分析结果为
`completed_mechanism_gate`、`mechanism_action_available=true`、
`natural_action_available=false`。

## 修复

前一轮 `20260911T154545Z` 暴露了确定性配置缺陷：server config 将
`shadow_enabled` 固定为 false，而 `PREPARE_HOST` 使用 SHADOW command lane。命令能
入队，但 dispatcher 在 `allow_shadow=false` 下不会取出，直到 shutdown 才产生显式
`CANCELLED` ACK。

修复包括：

1. launcher config 新增显式 `--enable-shadow-transfers`；predictor-only shadow 默认仍
   不拥有物理迁移权限。
2. 任意正数 PREPARE canary limit 和 deterministic micro gate 均要求该权限，配置错误
   启动前 fail-fast。
3. safe-point validation 和 dispatcher 增加 `shadow_disabled` 双重拒绝，禁止静默悬挂
   SHADOW command。
4. 物理提交前失败允许最多 16 次有界重试；command 一旦排队便保持严格单动作语义。
5. gate 优先选择满足最小 private KV 要求的较小 victim，并记录物理提交分段 CPU 时间。

提交为 `32a7317` 和 `6f9a9f7`。

## 事务链路

| 阶段 | 结果 |
|---|---:|
| intent published -> materialized | 135.81 ms |
| safe-point validation wall / thread CPU | 4.284 / 2.217 ms |
| queue -> dispatcher | 0.238 ms |
| D2H submit-to-complete | 147.081 ms |
| actual bytes / extents | 766,083,072 / 2 |
| estimated D2H P90 | 459.075 ms |
| publish -> transaction terminal | 331.08 ms |

safe-point CPU 分段为：causal guard 1.111 ms、physical rematerialization
0.758 ms、beneficiary/action 0.081 ms、transaction certificate 0.141 ms、transfer/timing
0.020 ms。总 thread CPU 低于 5 ms 物理动作门槛。

估计传输时间约为实测的 3.12 倍，说明当前 artifact 对这笔低碎片、2-extent D2H 偏
保守；该误差不会破坏本轮机制正确性，但在自然策略价值评估中必须继续使用区间并记录
shape support。

## 控制面与正确性

- safe-point capture P50/P95/P99：0.293/0.526/0.829 ms；P99 通过 1 ms 门槛，
  P95 比 0.5 ms 严格目标高 0.026 ms。
- predictive worker 6/6 完成，0 failed、0 dropped、0 pending。
- 唯一自然 risk evaluation 选择 observed baseline；fresh-positive 为 0。
- scheduler shutdown ACK 完整，所有 correctness gate 为 true。
- workload 停止后 action outcome 因未等待真实 beneficiary 消费而记为 censored；D2H
  transaction 本身已 completed。这是主动缩短机制实验的观测边界，不是迁移失败。

CPU 回归结果为 183 passed、2 deselected、8 subtests；另两项完整导入测试仍因环境缺少
`CUDA_HOME` 而失败，与本轮路径无关。预测事务 analyzer 回归为 5 passed。

## 裁决

`PREPARE_HOST` 的机制正确性和 5 ms 物理提交门槛已经关闭。下一步只运行自然单动作
canary：关闭 deterministic injection，保持 limit=1，并等待模型产生 fresh、timely、
positive package。只有该 shadow 被真实 beneficiary 消费并能归因 saved stall，才开放
预测 `COMMIT_CPU`；`PREFETCH_GPU` 继续关闭。

