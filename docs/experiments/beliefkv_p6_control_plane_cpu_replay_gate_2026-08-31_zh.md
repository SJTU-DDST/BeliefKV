# P6 Control-Plane CPU/Replay Gate

日期：2026-08-31

冻结输入：
`experiments/shadow/p6_beneficiary_shadow64/20260829T153721Z`

## 结论

P6 接入后绕过 Performance-First 路径造成的控制面回归已修复。既定的六项
CPU/replay 门槛全部通过，可以恢复后续 GPU characterization；本报告不评价 JCT、
GPU utilization 或预测策略收益，也不开放 predictive physical action。

## 修复内容

1. worker RCCG/data-consumer mirror 对已在 safe point 提交的 delta 使用
   `atomic=False`；任一 apply/version 错误立即丢弃 mirror 并 fail closed。
2. publication 分为 `SEMANTIC_DELTA`、`JOINT_REPLAN` 和 `RISK_EVAL`。普通
   TOOL/SPAWN/RETURN/JOIN 只更新 mirror，不再默认触发完整规划。
3. predictor 开启时继续构建 compact physical summary；只为 observed seed 选出的
   一个 beneficiary 和最多两个 victim 物化 physical bundle 与 transfer estimate。
4. eligibility probe 完整移入 predictive worker；scheduler submit 只执行 capacity-one
   latest-wins 入队。
5. frontier feature 改为 changed-invocation delta；全局 tool pressure 使用增量计数，
   candidate-local worker 在评估前刷新相关 feature。
6. seed-only/no-action plan 不再执行全量在线 validation；当前 native state 会同步重编译
   execution/admission seed，只有物理动作携带 action-local read set 进入验证。

另外，高压 full-plan watchdog 从 5 秒调为 30 秒。pressure crossing、transfer ACK、
beneficiary/reclaim 和 restore/retraction revision 仍会立即触发，不以延长 watchdog
掩盖状态变化。

## 门槛结果

| 门槛 | 结果 | 裁决 |
|---|---:|---|
| safe-point capture P95 < 1 ms | 0.181 ms | 通过 |
| predictive submit P95 < 1 ms | 0.014 ms | 通过 |
| RCCG delta apply P95 < 5 ms | 0.040 ms | 通过 |
| 384-page mirror apply P95 < 5 ms | 1.854 ms | 通过 |
| compact observed seed P95 < 10 ms | 5.943 ms | 通过 |
| predictive risk P95 50--100 ms | 88.201 ms | 通过 |
| full plan / scheduler step < 1% | 8 / 5,599 = 0.143% | 通过 |

192-request safe-point 微基准包含 400 次 apply-only publication 和一次 initial full plan。
capture P50/P95/P99 为 0.106/0.181/1.221 ms。P99 仍超过 1 ms，主要来自同进程 worker
抢占；既定门槛是 P95，因此如实记录而不判定失败。

192-invocation RCCG 微基准共 400 个 TOOL_START/TOOL_END delta：non-atomic mirror
apply P50/P95/P99 为 0.035/0.040/0.044 ms；changed-invocation feature 为
0.008/0.009/0.010 ms。predictive capacity-one submit 的 2,000 个样本为
0.013/0.014/0.017 ms。

compact observed seed 使用 4,096 page、192 runnable、100 次运行；wall-clock
P50/P95/P99 为 5.852/5.943/8.186 ms。该结果包含 bounded work-conserving seed，
不包含 candidate-local risk evaluation。

冻结高压 snapshot 的 16 次 candidate-local replay 中，bundle 数降至 20--36；
planning P50/P95/max 为 42.008/88.201/88.201 ms，scenario risk P50/P95/max 为
9.769/55.440/55.440 ms。eligibility P95 为 1.598 ms，但它已经位于 predictive
worker，不属于同步 submit 门槛。

full-plan 比例使用冻结 trace 的 2,618 个 resource snapshot 重放新门禁：一次 initial、
一次 pressure state crossing、六次 30 秒高压 watchdog，共 8 次。该轮没有 transfer、
restore obligation 或 residency transaction；若后续 GPU 运行出现这些事件，它们会
增加必要的事件驱动 full plan，必须重新报告实测比例。

## 验证

- 控制面/预测定向回归：101 passed。
- frontier feature/model 回归：36 passed。
- SGLang safe-point 定向回归：3 passed。
- `py_compile` 与 `git diff --check` 通过。

## 边界与下一步

冻结 replay 仍为 0 positive package，拒绝原因是 projected beneficiary HBM block
不可用；本轮按要求没有追逐正例、调整风险阈值或开放 canary。下一步只能进行一次
相同配置的短 GPU control-plane gate，验证同进程 GIL 干扰、真实 page delta、worker
backlog 和 full-plan 比例。通过前 predictive physical actions 保持关闭。
