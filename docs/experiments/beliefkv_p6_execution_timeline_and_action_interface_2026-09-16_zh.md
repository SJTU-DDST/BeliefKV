# P6 执行时间线与 action-timing 接口修复

日期：2026-09-16

## 结论

本轮从已有高压 baseline 与 predictive 运行生成了两份独立、可缩放的执行时间线，
并针对预测准确率审计中暴露的问题修改了 FrontierBelief 到 JointPlan 的接口。

当前 predictive 结果不能证明吞吐提升。时间线反而显示其可执行集合更小、GPU 利用率更
低、DMA 与 GPU busy 的重叠更少。现阶段核心问题仍是 prediction-to-action
utilization，而不是缺少预测字段。

## 时间线产物

- `experiments/ab/p6_h200_high_pressure_v2/20260916_current_comparison/baseline_execution_timeline.html`
- `experiments/ab/p6_h200_high_pressure_v2/20260916_current_comparison/predictive_execution_timeline.html`
- 同目录下的 `.json` 保存完整可复算数据。

HTML 支持缩放、平移和 hover，分别显示：

- GPU utilization；
- decode 推断窗口与 prefill 观测点；
- D2H/H2D submit-to-complete 区间；
- predictive planning、intent、commit、restore/retraction；
- TOOL/JOIN 活跃数量；
- HBM pressure；
- SGLang running/waiting queue。

传输区间来自精确 telemetry。旧 trace 没有 CUDA-event 级 prefill/decode duration，因此
decode 窗口由 SGLang decode interval、batch size 和 throughput 推断，prefill 只显示
时间点，未伪造持续时间。

## 流水线结果

| 指标 | Baseline | Predictive |
| --- | ---: | ---: |
| 运行时长 | 15,185.95 s | 23,664.63 s |
| GPU utilization mean | 8.79% | 7.98% |
| GPU busy sample fraction | 22.02% | 29.15% |
| mean running requests | 25.74 | 14.73 |
| mean waiting requests | 29.49 | 21.57 |
| transfer count | 12,560 | 13,786 |
| transfer submit-to-complete 总时长 | 3,491.86 s | 4,029.61 s |
| DMA 与 GPU busy 重叠 | 18.79% | 13.06% |
| D2H 与 GPU busy 重叠 | 11.32% | 8.95% |
| H2D 与 GPU busy 重叠 | 20.62% | 14.42% |
| predictive intent / commit | 0 / 0 | 6 / 1 |

`DMA 与 GPU busy 重叠`表示潜在可隐藏比例，不代表传输没有干扰计算。decode 推断窗口
覆盖低利用率间隔，因此其 81%–85% overlap 不能替代 GPU busy overlap。

当前最明显的可优化区间是：

1. WAIT_TOOL/WAIT_JOIN 已知长等待开始后，D2H 仍大量暴露在 GPU idle 区间；
2. reentry 前 H2D 没有稳定按 latest-start 发起；
3. predictive arm 的 mean running 明显下降，预测排序没有维持 work-conserving batch；
4. 单笔 predictive PREPARE 没有形成可测 beneficiary saved stall。

两次运行不是严格正式 A/B：baseline 最终发生 zero-token scheduler crash，predictive
存在 terminal protocol failure，且两臂时长不同。时间线用于定位流水线空洞，不作为
最终吞吐结论。

## 预测准确率重新解释

原始 94.96% boundary accuracy 等于 majority baseline；模型在 calibration 上对
`final` 和 `spawn` 的 recall 都是 0。tool terminal accuracy 79.46%，也略低于
79.63% 的 majority baseline。因此这两个数不能作为“下一个 agent 预测准确”的证据；
当前模型没有直接预测全局 next-agent identity。

新的动作专属 held-out calibration 结果：

| 动作 | Availability | Brier skill | P/R @ online threshold |
| --- | ---: | ---: | ---: |
| PREPARE_HOST, threshold 0.9 | 99.72% | +3.72% | 96.01% / 75.10% |
| PREFETCH_GPU, threshold 0.5 | 99.72% | +15.80% | 72.30% / 14.72% |
| PREFETCH_GPU, old threshold 0.9 | 99.72% | +15.80% | 69.09% / 3.05% |

PREPARE 只有弱概率增益；PREFETCH 有一定排序信号，但召回仍低。`test_id` 继续封存，
这些数字只来自 16 个 calibration workflow。

## 代码修改

1. 新增 `ActionTimingPrediction`，由 FrontierBelief 直接发布：
   - action；
   - live operational tau；
   - favorable probability；
   - support；
   - Brier skill 和 balanced accuracy。
2. PREPARE 与 PREFETCH 分别校准，不再共享一个 action-agnostic survival map。
3. calibration 直接优化 action-facing Brier loss。
4. ScenarioRiskPlanner 不再自行拼接通用 wait 字段；skill 不优于常数先验时 fail closed。
5. PREPARE 保持 0.9 timing gate，PREFETCH 改为独立 0.5 gate。
6. 评估增加 majority baseline、macro/per-class recall、climatology Brier、Brier skill、
   balanced accuracy 和线上 0.9 threshold 指标。
7. LOPO 后续按相对 constant-prevalence baseline 的 Brier regret 选型，不再被高类别
   不平衡下的表面准确率主导。

新 artifact：

- `experiments/models/frontier_belief_h200_bf16_v4_action_timing_calibrated.json`
- `experiments/models/frontier_belief_h200_bf16_v4_action_timing_calibration_metrics.json`

artifact 仍为 `online_eligible=false`。下一步只应先用于 shadow/replay，验证它是否增加
及时、正收益且可物理化的 package；不能仅凭 calibration 指标直接宣称线上收益。

