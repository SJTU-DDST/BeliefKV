# P6 wait-shadow v47/v48 延迟复验

日期：2026-09-19
运行：

- `experiments/shadow/p6_predictive_wait_shadow_v47/predictive`
- `experiments/shadow/p6_predictive_wait_shadow_v48/predictive`

代码提交：`62d3dac`、`38c389b`

## 结论

v47 发现旧 `publish-to-validation` 指标使用预测输入的 observation 时间戳，并不代表 intent
真正发布后的排队时间。`62d3dac` 将源观测时间与实际发布时间拆分，并让 PREPARE 使用最多
4 个本地 invocation prediction，成功发布后不再等待完整 reentry closure。

v48 的修正指标证明 intent 发布后的提交链已经足够快，并自然完成 39 笔 predictive
`PREPARE_HOST -> D2H dispatch -> ACK`。剩余瓶颈位于发布前的物理 preview：源观测到验证
P95 仍为 750.16 ms。`38c389b` 将 bounded shadow preview 从为每个 GPU 页构造 descendant
closure，改为一次 memoized private-subtree 遍历后选择最大合法子树；最终 safe-point 物理
验证不变。

## v48 延迟

| 指标 | P50 | P95 | P99 |
| --- | ---: | ---: | ---: |
| 真正 publish-to-validation | 0.231 ms | 0.395 ms | 0.485 ms |
| same-safe-point validation | 0.487 ms | 0.761 ms | 1.036 ms |
| local prediction | 1.314 ms | 7.603 ms | 11.567 ms |
| source observation-to-validation | 199.84 ms | 750.16 ms | 938.44 ms |

v48 共观察到 39 笔 PREPARE/D2H transaction，均完成 dispatch 和 ACK，无 predictive worker
failure。该结果证明预测行为能够进入真实传输路径，但尚不能证明动作足够早或带来吞吐收益。

## 修复与验证

- `62d3dac`：拆分 source/publish 时间戳，增加端到端指标，PREPARE 使用局部预测快路径；
- `38c389b`：线性化 bounded shadow preview；
- 局部 bundle/runtime 回归：222 passed，2 deselected，8 subtests passed；
- 2 个 deselected 测试依赖当前 shell 未配置的 `CUDA_HOME/deep_gemm`，与本次逻辑无关。

## 下一门槛

同一冻结配置运行 v49 短高压 gate：

- true publish-to-validation P95 < 50 ms；
- source-observation-to-validation 明显低于 v48 的 750.16 ms，目标 < 50 ms；
- predictive PREPARE/D2H 能自然产生；
- worker failure、orphan transaction 和 shutdown masking 为 0。

若端到端指标仍高，则停止 GPU，改为先用 physical summary 对候选排序、只为最终候选构造
一次 preview，不继续延长实验。
