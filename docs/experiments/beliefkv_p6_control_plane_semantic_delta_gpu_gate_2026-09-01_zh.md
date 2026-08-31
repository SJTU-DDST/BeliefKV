# P6 Semantic-Delta Control-Plane GPU Gate

日期：2026-09-01

代码提交：`deac101`

运行目录：
`experiments/shadow/p6_control_plane_semantic_delta/20260831T155240Z`

## 结论

P6 控制面的同步开销门槛已经通过。真实 H200 运行形成 64 workflow、192
invocation/context、32 running 和 96 waiting；575 次 JointShadow publication
全部完成，零失败、零 pending、零 worker backlog。safe-point capture
P50/P95/P99 为 0.167/0.336/0.488 ms，完整规划只发生 1 次，占 1,513 个
scheduler step 的 0.066%。

本轮不证明预测策略收益或物理动作性能。短运行的 KV pool 峰值为 22.82/83.56 GB，
即 27.31%，没有触发在线 predictive risk 或 transfer。candidate-local risk
门槛使用同一冻结高压 snapshot 的 16 次 CPU replay 验证，planning P95 为
85.23 ms；predictive physical action 继续关闭。

## 修复

1. 普通 RCCG 事件只发布 `SEMANTIC_DELTA`，不再复制 PageIndex、allocator、
   fairness、transfer telemetry 和完整 control state。
2. semantic publication 保持 page/topology/telemetry cursor 不变；下一次
   `JOINT_REPLAN` 从旧 cursor 一次性补齐物理状态。
3. 最近一次完整 runnable、fairness、control、capability 和 state stamp 作为只读
   worker mirror 证书复用。
4. PageIndex full capture 对 mutation journal 只扫描一次，不再由
   `changes_since()`、page projection 和 context projection 重复扫描。
5. 先前完成的 changed-invocation `FrontierFeatureSource` 仍由 worker
   candidate-locally materialize，scheduler 不重建 192 个完整特征。

## 门槛

| 门槛 | 结果 | 证据 | 裁决 |
|---|---:|---|---|
| safe-point capture P95 < 1 ms | 0.336 ms | 实际 GPU | 通过 |
| predictive submit P95 < 1 ms | 0.014 ms | 冻结 CPU capacity-one gate | 通过 |
| delta apply P95 < 5 ms | 0.040 ms semantic；1.814 ms/384 pages | CPU replay | 通过 |
| compact observed seed P95 < 10 ms | 5.98 ms | 4,096 pages/192 runnable CPU gate | 通过 |
| predictive risk P95 50--100 ms | 85.23 ms | 16 个冻结高压 candidate-local replay | 通过 |
| full plan / scheduler step < 1% | 1/1,513 = 0.066% | 实际 GPU | 通过 |

实际 GPU 中唯一完整 plan 的 snapshot build 为 1.247 ms，其中 delta apply
0.129 ms、materialization 1.118 ms；plan compute 为 2.170 ms。574 次
semantic-only publication 的 enqueue 与 full publication 合并统计，P95 为
0.030 ms。所有 575 次 publication 均由 worker 完成，未出现 coalesced backlog、
failed、dropped 或 superseded result。

## 正确性

- 所有非用户取消的 restore obligation 均满足；
- 所有在线动作都有 source JointPlan ID；
- 无 pending command、lease、reservation、transaction；
- shutdown cleanup 未掩盖 unresolved transaction；
- final summary 与 shutdown ACK 完整；
- PageIndex 未再次触发一致性断言。

定向 CPU 回归为 186 passed、8 subtests passed。另两项 SGLang import 测试仍因基础
`beliefkv` 环境缺少 `CUDA_HOME` 而失败，与本次改动无关。新增回归覆盖：
semantic delta 不推进物理/telemetry cursor，以及 PageIndex journal 单次扫描。

## 运行边界

本轮在约两分钟后主动停止，只用于控制面 characterization，不进入 workflow JCT
或任务正确性数据。GPU 显存接近满载主要来自 BF16 权重、850K KV pool 和 graph，
不是 KV pool 已使用率；不能据此声称形成了 HBM admission pressure。

BeliefKV scheduler 已完成事务化 shutdown 并生成 ACK。SGLang HTTP 父进程未自行
回收两个已退出 child，确认 GPU allocation 和事务均释放后执行了进程清理。当前无
残留模型或 workload 进程。

## 下一步

控制面不再是 P6 第一阻塞项。后续恢复 beneficiary-bound value 验证：只评估
`1 beneficiary x 2 victims`，先用 frozen high-pressure trace 检查
projected HBM deficit 与 positive package；在 fresh positive package 出现前不开放
PREPARE_HOST canary。
