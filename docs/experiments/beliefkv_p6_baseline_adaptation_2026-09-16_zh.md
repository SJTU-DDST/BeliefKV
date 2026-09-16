# P6 Baseline Trace 快速适配

日期：2026-09-16

## 1. 目标与证据边界

为了快速检查当前 predictor 是否因旧 runtime/harness 数据分布而失效，本轮直接复用
2026-09-15 的 40-root observed baseline 作为 development adaptation 数据。

该 baseline 与拟合任务重叠，因此新模型只能用于当前 workload 的机制开发：

- 不能用于 test 或泛化结论；
- 不能把同 workload 的后续 GPU 提升称为无偏 A/B 收益；
- test_id 保持封存；
- 独立的 16-workflow calibration 继续作为模型质量回归门禁。

## 2. 数据导出

输入：

`experiments/ab/p6_h200_high_pressure_v2/20260915_40roots_f548210_pair4/baseline`

输出：

`experiments/processed/h200_bf16_baseline_adaptation_v1`

导出结果：

- 40 个 workflow，22 completed、18 error；
- 31,984 个可训练 frontier decision point；
- 7,728 个有效 external-wait survival 标签；
- 8,672 个 schema-v4 action target；
- reentry 归因覆盖率 100%；
- exact incremental boundary 仍为 0%。

当前 performance trace 没有可匹配的逐请求 GPU service identity，因此 request-level
remaining-decode/unlock-hazard 计数为 0。训练仍保留原 64-train 的 token demand 数据，
baseline 只提供逐目标有效的 semantic/wait/action evidence；intervention 后标签继续 censor。

## 3. 训练协议

新入口：

`scripts/train_frontier_baseline_adaptation.py`

协议：

1. 正式 64-train 提供基础 demand/semantic 分布；
2. 当前 40-workflow baseline 提供 current-patch adaptation；
3. 超参数固定为原 train-only LOPO 结果，不在 baseline 上重新选择；
4. 原 16-workflow calibration 用于概率校准和模型回归；
5. artifact 强制标记 `development_only=true`、`online_eligible=false`。

产物：

- `experiments/models/frontier_belief_h200_bf16_v5_baseline_adapted_uncalibrated.json`
- `experiments/models/frontier_belief_h200_bf16_v5_baseline_adapted_calibrated.json`
- `experiments/models/frontier_belief_h200_bf16_v5_baseline_adapted_metrics.json`

合并后共有 115,696 个 decision point。baseline 的 40 个任务是既有 train 任务的新 rollout，
因此增加当前实现下的重复观测，但不增加任务或项目多样性。

## 4. Held-out Calibration

| 指标 | v4 | v5 baseline-adapted |
| --- | ---: | ---: |
| PREPARE Brier skill | 3.72% | 3.20% |
| PREPARE recall @ 0.9 | 75.10% | 77.47% |
| PREPARE precision @ 0.9 | 96.01% | 95.95% |
| PREFETCH Brier skill | 15.80% | 15.80% |
| PREFETCH recall @ 0.5 | 14.72% | 20.34% |
| OOD fallback | 0.108% | 0.108% |

v5 改善了 PREFETCH 的低阈值 recall 和 PREPARE 高阈值 recall，没有破坏 held-out
calibration，但 PREPARE 的概率 skill 略有下降。boundary 的 spawn/final recall 和 tool-error
recall 仍为 0，不能用于吞吐关键排序。

## 5. 同 Snapshot Replay

在上一轮 predictive run 的 17 个高压 snapshot 上，以相同 64 particles 重放 v3/v5：

| 指标 | v3 | v5 |
| --- | ---: | ---: |
| evaluated PREPARE candidate | 6 | 6 |
| positive benefit | 0 | 0 |
| eligible | 0 | 0 |
| 最大预测 HBM overflow | 3.00 GB | 3.56 GB |
| eligible recourse scenario | 0 | 0 |

v5 明显提高了多个候选的 causal-slack probability，但没有改变收益和动作选择。所有候选的
latest start 已错过约 253--4,363 ms；20/24 scenario 为
`shadow_completes_after_pressure`，另有 4 个没有 beneficiary HBM block。

## 6. 裁决

直接使用当前 baseline 适配模型是可行的快速开发手段，但本轮已经证明“重新训练”不是当前
零收益的主因。真正阻塞是 beneficiary-bound package 形成过晚：模型能提高等待裕量概率，
JointPlan 却在 D2H 已无法隐藏后才构造动作。

因此不启动预期为零动作的长 GPU run。下一步先修改 prediction-to-action 接口，使
`TOOL_START/WAIT_JOIN + 可见 deferred beneficiary + future growth deficit` 在 pressure 前
形成 package，再用同一 replay 检查 `validation < latest_start`。development GPU 计划已冻结在
`configs/p6/predictive_joint_h200_high_pressure_v3/ab_plan.json`，仅允许单笔 PREPARE_HOST。
