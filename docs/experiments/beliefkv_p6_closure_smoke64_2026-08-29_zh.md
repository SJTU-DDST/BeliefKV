# P6 64-root Closure Smoke

日期：2026-08-29

## 结论

提交 6b56736 的 closure-local prediction 修复已通过真实高基数 GPU
验证：运行时包含 64 个 workflow、192 个 invocation/context，
closure_prediction_incomplete=0，并产生了真实 PREPARE_HOST 候选、动作时序和
新鲜 action certificate。

本轮没有预测收益证据，也不应进入 canary。高压区间的 258 个候选中有 54 个证书
在验证时仍新鲜，但正收益和 fresh-positive 均为 0。全程 6,012 个已评估候选的
最大 expected benefit 为 -4.19 ms，全部命中
insufficient_expected_benefit 和 insufficient_recourse_after_stall。因此当前分支
是“候选 fresh、收益全负”，下一步应修正 recourse/value 语义，而不是先优化完整
planner 或放宽风险阈值。

## 配置

- GPU/model：H200 NVL，Qwen3-Coder-30B BF16。
- runtime profile：h200_bf16_v6，850K KV token pool，96 GiB Host pool，
  CUDA Graph 32，max_running_requests=32。
- workload：冻结 64-root native_subagent_2to3 manifest，all-roots eager，
  client concurrency 64。
- predictor：schema-v4 predictor-only shadow。
- predictive physical action、overlay 和 PREPARE canary：全部关闭。
- 停止规则：HBM 达到 80% 后获得至少 20 个非 skipped risk result 即停止。

运行目录：

experiments/shadow/p6_closure_smoke64/20260829T095958Z

停止监控在第 23 个高压结果时触发；取消和服务器 drain 期间 worker 继续完成已提交
任务，最终保留 45 个 HBM >= 80% 的结果。这不是重复实验。

## Closure Gate

| 指标 | 结果 |
|---|---:|
| workflow / invocation / context | 64 / 192 / 192 |
| evaluated risk result | 1,003 |
| HBM >= 80% risk result | 45 |
| HBM 峰值 | 90.88% |
| closure_prediction_incomplete | 0 |
| 全程 candidate / timing available | 6,012 / 6,012 |
| 高压 candidate / timing available | 258 / 258 |
| 全程 fresh / stale certificate | 4,045 / 1,967 |
| 高压 fresh / stale certificate | 54 / 204 |
| positive / fresh-positive / eligible | 0 / 0 / 0 |

PageIndex 未出现 assertion；无 OOM、CUDA replay 或 allocator consistency error。
predictive worker 完成 1,016 个任务，failed/dropped/pending 均为 0。

## 控制面开销

| 路径 | P50 | P95 | P99 |
|---|---:|---:|---:|
| safe-point delta capture | 4.77 ms | 23.68 ms | 31.11 ms |
| snapshot build | 493.87 ms | 1,431.33 ms | 1,971.74 ms |
| observed plan compute | 607.15 ms | 1,637.29 ms | 2,215.90 ms |
| observed validation | 56.60 ms | 203.55 ms | 326.82 ms |
| predictive planning（全程） | 1,330.10 ms | 2,714.89 ms | 3,637.18 ms |
| predictive trigger-to-validation（全程） | 1,639.29 ms | 3,451.88 ms | 5,216.82 ms |
| predictive planning（高压） | 2,951.82 ms | 4,296.61 ms | 4,983.06 ms |
| predictive trigger-to-validation（高压） | 3,976.31 ms | 7,036.22 ms | 25,167.79 ms |

修复后 safe point 为全部 active invocation 冻结 lightweight feature 的路径没有出现
新的数量级退化：capture P95 从上一轮约 25.67 ms 变为 23.68 ms。但它仍远高于
正式目标 1 ms，完整 snapshot/scenario 路径也仍过慢。因为本轮不是 stale-only
分支，暂不先做 compact snapshot 性能重构。

## 零收益根因

全程候选统计为：

- expected benefit P50/P95/max：-29.61/-9.78/-4.19 ms；
- causal-slack probability P50/P95/max：1.0/1.0/1.0；
- future-HBM feasibility probability P50/P95/max：1.0/1.0/1.0；
- insufficient_expected_benefit：6,012；
- insufficient_recourse_after_stall：6,012；
- cvar_risk_budget：5,105；
- shape unsupported：72。

当前 PREPARE recourse 只有在有限 horizon 内预测到 HBM 超过 100%，且 shadow 在
pressure 前完成、pressure 早于 parent reentry、parent 足以覆盖 deficit、快照近似
的 reactive victim 正好也是该 parent 时，才计入 reactive D2H credit。实际 HBM
达到 80%-90% 并不自动满足这些条件。

更关键的是，当前 PREPARE package 只有 victim，没有绑定明确的 beneficiary。运行
结束时同时存在 32 running 和 95 waiting；此时 waiting 可能受 running slot 限制，
不能仅凭 HBM watermark 断言迁出 parked KV 会立即解锁 GPU work。强行给予收益会
把普通高占用误判为 causal replacement opportunity。

正确的下一版价值语义是：

1. 从 observed execution/admission/reclaim seed 选择真实 blocked beneficiary；
2. package 同时携带 beneficiary startup/growth demand 与 victim reclaim envelope；
3. pressure deadline 由 beneficiary 的确定性容量 deficit 或校准后的 pressure-arrival
   scenario 给出，而不是只等待全局 HBM 超过 100%；
4. 比较 baseline reactive D2H + beneficiary admission/service delay，与 proactive
   shadow + pressure-time commit 两条路径；
5. 没有明确 beneficiary 或有限 pressure evidence 时，PREPARE 继续保持负收益。

## 随后修复

本轮后增加了两个不改变策略语义的小修复：

- 候选只从 closure-complete BeliefScope 中产生，移入 OTHER 的 invocation 不再
  产生 local_prediction_missing 的无效 package；
- performance aggregate 记录已计算的
  prepare_recourse_failure_counts 和 expected_recourse_credit_ms，不复制
  scenario 明细。

这两项只减少无意义评估并补全下一轮价值归因，不开放任何预测动作。

## 终止限制

提前停止时客户端取消较慢，最终请求队列仍记录 32 running / 95 waiting；因此本轮
不能用于 JCT、completion rate 或 request-drain liveness 结论。服务器侧
SHUTDOWN_ACK 已完成，queued/inflight command、transaction、lease、reservation 和
obligation 全部清零，shutdown_cleanup_did_not_mask_unresolved_transactions=true。
该边界足以支撑 closure、risk value 和控制面开销结论。
