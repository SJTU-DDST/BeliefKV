# P6 Bounded-Hint High-Pressure Shadow64 Gate

日期：2026-09-02

## 结论

本轮验证了 bounded observed-seed beneficiary hint 能在真实 64-root 高压运行中持续
触发 Predictive worker，且受控关闭后没有遗留事务。但预测价值门槛没有通过：全程
没有 `fresh-positive` 且在 latest-start 前完成验证的 package，因此没有运行
`PREPARE_HOST` canary。

实验同时暴露了两个实现问题：compact RCCG 缺少可选 execution witness 会使 risk
evaluation 抛出 `KeyError`；仅 `seed_generation` 变化也会重复评估同一动作。二者均已
修复并通过定向回归。修复后对保存的高压 snapshot 重放能够正常生成候选，但该候选
仍为零收益，不能通过修改门禁制造正例。

## 冻结配置

- 运行目录：`experiments/shadow/p6_bounded_hint_shadow64/20260902T105749Z`
- 模型：Qwen3-Coder-30B-A3B-Instruct BF16
- GPU：NVIDIA H200 NVL
- KV pool：850,000 tokens；Host pool：96 GiB
- `max_running_requests=32`，CUDA Graph 最大 batch 32
- 64 个预注册 root 同时提交，`native_subagent_2to3`
- observed P5 在线；predictor/risk 只读；所有 predictive physical action 关闭
- 运行约 41.4 分钟，达到高压后受控停止，不评价 workflow JCT 或吞吐

## 高压与活性

- HBM 峰值：99.9995%
- HBM >=80% 持续约 13.79 分钟
- 停止前队列：32 running / 95 waiting
- Predictive worker：1,199 submitted / 1,199 completed / 475 failed / 0 pending
- shutdown 后无 pending transaction、command、lease、reservation 或 obligation
- `shutdown_cleanup_did_not_mask_unresolved_transactions=true`

失败不是资源泄漏。475 次 worker failure 的主要可复现原因是 compact RCCG 中不存在
source execution plan 的可选 slot witness，`BeliefScopeBuilder` 将其误当成必需节点。
beneficiary 与最多两个 victim 仍保持必需并 fail closed；slot witness 现在仅在 mirror
中存在时加入 scope。

## Hint 与候选漏斗

| 指标 | 数量 |
| --- | ---: |
| hint publication | 2,039 |
| Predictive worker submission | 1,199 |
| `no_beneficiary_hint` | 73 |
| `no_live_victim_bundle` | 807 |
| `unchanged_action_signature` | 324 |
| eligibility checked/evaluated/no-candidate | 1,198 / 479 / 710 |
| 完整 risk result | 5 |
| positive / fresh-positive / timely-positive | 0 / 0 / 0 |

高压阶段有 342 次 risk enqueue，但仅产生 1 个完整 result；主要过滤项为
`no_live_victim_bundle=190`。enqueue 时 physical mirror age 的 P50/P95/P99 为
28.1/1,359/2,682 ms，最大 page revision lag 为 3,594。当前下一项物理视图修复应是
`1 beneficiary x 2 victims` 的 action-local overlay，而不是恢复全局 PageIndex 快照。

hint created-to-published 的 P50/P95/P99 为 12.61/19.97/23.51 ms。现在只有
request/context/epoch/startup/growth 变化才触发 RISK_EVAL；单纯 seed generation 更新
仍同步 mirror revision，但只发布 apply-only refresh。

## 控制面结果

| 指标 | 结果 |
| --- | ---: |
| Joint worker submissions | 4,665 |
| full plans | 1 |
| safe-point capture P50/P95/P99 | 0.213/20.004/26.386 ms |
| risk event materialization P50/P95 | 26.12/60.21 ms |
| risk delta apply P50/P95 | 0.544/22.43 ms |
| predictive submit P95 | 0.035 ms |
| eligibility P95 | 1.666 ms |
| trigger-to-validation P95 | 1,317 ms |

此前低压 gate 的 safe-point P95 `<1 ms` 不能外推到该高压事件路径。主要新增成本是
全局 active invocation 特征与过旧物理 mirror 的候选物化，而不是 enqueue 本身。
因此本轮不能宣称控制面整体已合格。

## 修复后重放

对本轮保存的高压 snapshot 使用修复后代码重放：

- 不再出现 compact RCCG `KeyError`；
- 正常评估 1 个 `PREPARE_HOST` 候选；
- positive/eligible 仍为 0/0；
- 4/4 scenario 为 `projected_beneficiary_hbm_block_unavailable`；
- 最大预测 HBM overflow 约 1.493 GB，但该 beneficiary 在动作窗口内没有形成可归因
  的 admission deficit。

这说明软件失败已被移除，但当前 snapshot 仍不具备 predictive KV opportunity。

## 裁决与下一步

本轮通过：hint 主动发布、worker latest-wins 活性、受控 shutdown、compact RCCG
必需/可选 scope 语义。

本轮未通过：risk 覆盖、候选物理视图新鲜度、同步高压开销、正收益及时 package。

下一步只实现 candidate-local physical overlay，并在 CPU replay/定向测试中要求：

1. `risk_shadow_failed=0`；
2. `no_live_victim_bundle` 明显下降；
3. seed-generation-only refresh 不触发 risk；
4. 不恢复全局 PageIndex 或完整 physical snapshot。

随后最多再运行一次短高压 shadow。只有出现 `fresh-positive` 且
`validation_ts < latest_feasible_start_ts`，才开放单笔 `PREPARE_HOST` canary。

定向验证结果：`192 passed, 2 deselected, 1 warning, 8 subtests passed`；
`py_compile` 与 `git diff --check` 通过。
