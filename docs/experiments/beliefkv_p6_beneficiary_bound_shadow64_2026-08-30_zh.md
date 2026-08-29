# P6 Beneficiary-Bound 64-root Shadow

日期：2026-08-30

## 结论

本轮关闭了 projected beneficiary 的接口缺口，但没有发现可执行的预测式 KV
机会，因此不开放 PREPARE_HOST canary。

修复前，observed JointPlan 的 `candidate_order_request_ids` 只包含截断后的 16 个
候选，而 bounded seed 将这 16 个请求全部 ADMIT；其余 74--110 个 DEFER 请求不在
该顺序中。risk planner 因此在 1,205 次评估中始终返回
`no_projected_hbm_beneficiary`。提交 f78e33c 让 observed planner 使用同一排序额外
发布一个 seed-excluded waiting request，不引入第二套 beneficiary scheduler。

修复后的真实 64-root 运行中，1,040 次 risk result 全部完成，产生 2,079 个
PREPARE_HOST package，`no_projected_hbm_beneficiary=0`。但 16 个冻结高压快照的
HBM 余量仍为 12.99--15.42 GiB，而 projected beneficiary 的 startup 加 growth
需求只有约 8.3--433.2 MiB。slot 可用时这些请求能够直接 admission；它们当前受
`max_running_requests=32` 限制，不受 HBM deficit 限制。

因此 16,592 个 scenario 全部返回
`projected_beneficiary_hbm_block_unavailable` 是正确的 what-if 结果。不能给高 HBM
watermark 人工收益，也不能把 waiting age 当作 saved stall。

## 配置与停止规则

- GPU/model：H200 NVL，Qwen3-Coder-30B BF16。
- runtime profile：h200_bf16_v6，850K KV token pool，96 GiB Host pool，
  CUDA Graph 32，max running 32。
- workload：冻结 64-root `native_subagent_2to3`，all-roots eager，client in-flight 64。
- predictor：schema-v4 predictor-only shadow。
- predictive overlay、COMMIT、PREFETCH 和 PREPARE canary：全部关闭。
- 停止规则：HBM >=80% 后获得至少 20 个非 skipped risk result，或首个
  fresh-positive package。

有效运行目录：

`experiments/shadow/p6_beneficiary_shadow64/20260829T153721Z`

停止监控在第 21 个高压结果时触发；shutdown drain 期间 worker 完成已提交工作，
最终保留 26 个高压结果。峰值 HBM 为 83.61%。

## 结果

| 指标 | 结果 |
|---|---:|
| workflow / invocation / context | 64 / 192 / 192 |
| risk result | 1,040 |
| candidate / timing available | 2,079 / 2,079 |
| fresh / stale certificate | 1,131 / 948 |
| HBM >=80% result / candidate | 26 / 52 |
| positive / fresh-positive / eligible | 0 / 0 / 0 |
| expected benefit P50 / P95 / max | -32.81 / -32.81 / -1.99 ms |
| expected recourse credit max | 0 ms |
| replay snapshot | 16 |

主要拒绝计数：

- `projected_beneficiary_hbm_block_unavailable`：16,592 个 scenario；
- `insufficient_expected_benefit`：2,079 个 package；
- `insufficient_recourse_after_stall`：2,079 个 package；
- `cvar_risk_budget`：2,069 个 package；
- `shape_unsupported` 和 `insufficient_causal_slack_probability`：各 5 个 package。

shape 和 causal slack 只影响少量 package，不是本轮零收益主因。所有 candidate 使用
同一个 beneficiary、最多两个 victim，预测路径没有提前驱逐 GPU KV。

## 离线 Replay

performance-mode snapshot 保存的是 `frontier_features` 和模型版本，而旧 replay
脚本错误地只接受预计算 `frontier_predictions`。脚本现支持 `--predictor-model`，
复用 `FrontierBeliefModel.load` 和在线 risk observer。16 个快照确定性重放得到：

- 32 个 PREPARE_HOST candidate；
- 32/32 shape-supported；
- 256/256 scenario 为 `projected_beneficiary_hbm_block_unavailable`；
- 0 pressure candidate、0 recourse scenario、0 positive candidate。

这不是模型 OOD 或 replay 数据缺失造成的零收益。

## 控制面边界

| 路径 | P50 | P95 | P99 |
|---|---:|---:|---:|
| safe-point delta capture | 4.62 ms | 24.13 ms | 29.10 ms |
| snapshot build | 485.81 ms | 1,403.85 ms | 1,955.00 ms |
| observed plan compute | 629.67 ms | 1,622.99 ms | 2,230.51 ms |
| observed validation | 73.68 ms | 280.01 ms | 866.09 ms |
| predictive planning | 913.52 ms | 1,836.10 ms | 2,549.30 ms |
| predictive planning（高压） | 1,468.84 ms | 2,149.96 ms | 2,229.44 ms |
| trigger-to-validation（高压） | 2,415.81 ms | 7,909.15 ms | 8,229.61 ms |

`1 beneficiary × 2 victims` 限制降低了动作空间，但没有解决全量 snapshot、Python
对象复制和 GIL 竞争。当前延迟不适合开放完整在线 P6。

## 正确性与裁决

predictive worker 1,040/1,040 完成，failed/dropped/pending 均为 0。无 OOM、
PageIndex、allocator 或 CUDA replay error。shutdown 后 command、transaction、lease、
reservation 和 obligation 全部清零，所有 correctness gate 通过，无残留进程。

本轮不能评价 JCT 或 workflows/hour，因为按 characterization 停止规则提前取消了
workload。科学裁决仅为：beneficiary package 机制已闭环，但当前冻结的 80%--83.6%
窗口主要是 slot pressure，不是 predictive KV opportunity。后续若继续验证该机制，
停止条件必须使用 action-specific projected deficit，而不能继续把固定 80% watermark
等同于 HBM-blocked beneficiary；该变更必须在新运行前预注册，不能回填本轮。

