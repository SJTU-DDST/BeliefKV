# P6 FrontierBelief schema-v5 重构与校准

日期：2026-09-17

## 结论

旧 v6 的主要问题不是组件数量，而是训练契约错误：train action-target 只被加载和记录，
没有参与 `FrontierBeliefModel.fit()`；在线动作概率仍来自稀疏层次经验 wait 模型。因此
PREPARE/PREFETCH 的“动作对齐评估”没有对应到动作对齐训练。

schema-v5 已改为：

- 直接拟合 live `tau` 下的 tool release probability；
- 用 pooled conditional model 预测 token demand；
- 用 pooled multinomial model 替换 boundary/tool-terminal 多数类退化；
- 运行时保持纯 Python 小模型，NumPy/SciPy 只用于离线训练；
- schema-v4 artifact 保持只读兼容。

## 数据隔离

- fit：64 workflow、7 project、83,712 decision points；
- action fit：10,432 rows、20,864 known action outcomes；
- model selection：只在 train project 内做 7-fold LOPO；
- calibration：16 workflow，`astropy` 与 `sphinx`；
- `test_id`：未读取、未训练、未调参、未校准。

## Held-out calibration

| Head | schema-v5 | 旧 v6 |
| --- | ---: | ---: |
| PREFETCH Brier | 0.0775 | 0.1204 |
| PREFETCH Brier skill | +45.67% | +15.80% |
| PREFETCH precision/recall @0.5 | 69.91% / 62.80% | 68.93% / 20.34% |
| PREFETCH precision/recall @action threshold | 59.66% / 90.56% | 36.17% / 64.67% |
| PREPARE Brier | 0.0501 | 0.0601 |
| PREPARE Brier skill | +19.21% | +3.20% |
| Remaining decode MAE | 384.54 tokens | 436.67 tokens |
| Next output MAE | 175.84 tokens | 178.64 tokens |
| Prompt growth MAE | 2,002.41 tokens | 2,024.10 tokens |
| Tool terminal error recall | 51.10% | 0% |
| Boundary top-2 accuracy | 99.67% | 未记录 |

Boundary top-1 accuracy 仍接近多数类基线，因为 calibration 中 94.96% 的边界为 TOOL。
系统使用 top-2 scenarios：FINAL/SPAWN 的 top-2 recall 分别为 97.72%/73.76%，不把
argmax 当作确定性 action boundary。

## 性能与边界

- WAIT_TOOL inference：约 0.86 ms/invocation；
- RUNNING_LLM inference：约 0.27 ms/invocation；
- action timing 和 demand 仍在异步、增量 worker 中计算；
- exact incremental boundary 仍为 0%，所以不支持 early dispatch；
- prompt-growth conformal interval 较宽，物理动作仍需 safe-point capacity 与 closure 重验；
- artifact 保持 `online_eligible=false`，直到长高压路径完成真实
  `PREPARE_HOST -> COMMIT_CPU -> PREFETCH_GPU -> first service` 门禁。

正式 artifact：
`experiments/models/frontier_belief_h200_bf16_v7_pooled_action_calibrated.json`。

代码节点：

- `37cbb9c feat(p6): redesign action-aligned frontier prediction`
- `54281c7 feat(p6): pool rare frontier classifications`

