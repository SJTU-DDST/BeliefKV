# P6 wait-shadow PREPARE 发布到验证延迟审计

日期：2026-09-19  
运行：`experiments/shadow/p6_predictive_wait_shadow_v46/predictive`  
修复提交：`994de39`

## 结论

v46 证明缺少及时 PREPARE 的首要原因不是工具窗口普遍过短，而是
`TOOL_START -> intent publish -> safe-point validation` 控制链晚于真实工具生命周期。
本轮受控停止时，同类拒绝从观察中的 427 次增长到 444 次。实验已正常 shutdown：
所有在线事务、command、lease 和 reservation 均清空，4 个 restore obligation 均以
`gpu_service_resumed` 满足。

## 延迟分解

444 个 `invocation_state_changed` PREPARE 拒绝均可关联到唯一工具调用：

| 指标 | P50 | P95 |
| --- | ---: | ---: |
| 对应工具总时长 | 449.33 ms | 1,571.71 ms |
| TOOL_START 到 intent publish | 375.50 ms | 1,295.89 ms |
| intent publish 到 safe-point validation | 991.98 ms | 2,283.03 ms |
| TOOL_START 到 validation | 1,478.83 ms | 3,094.94 ms |

其中：

- 259/444 个 intent 在对应 `TOOL_END` 之后才发布；
- 199 个工具调用总时长超过 500 ms；
- 这 199 个长工具中，63 个 intent 在工具结束后发布；
- 其余 136 个虽然在工具执行时发布，但只有 16 个发布时还剩超过 500 ms；
- 136 个中有 134 个在 validation 前结束，只有 2 个及时验证。

因此固定 250 ms control lead 不是主要修复点。旧路径即使正确预测了长工具，也会先消耗
数百毫秒形成 intent，再等待下一轮 JointPlan reuse/refresh，错过动作窗口。

## 代码修复

`994de39` 保留所有因果和物理门禁，只删除重复等待：

1. child `TOOL_START` 清除 reentry poll/signature，立即进行事件对齐预测；
2. 新 wait-shadow intent 发布后，在当前 physical safe point 复用 observed decision；
3. 使用缓存 preview，并继续执行 live causal certificate、bundle、transfer envelope 和
   commit budget 校验；
4. 验证成功后在同一 safe point 进入 residency command queue；
5. `TOOL_END/REACTIVATE/RETURN/CANCEL` 到达时撤销尚未提交的对应 intent；
6. 新增 `wait_shadow_publish_to_validation_ms`、same-safe-point validation wall/CPU
   指标和显式 audit event。

该修复没有让预测器提前驱逐 GPU KV，也没有放宽 invocation state、context epoch、physical
generation、Host capacity、PCIe busy 或事务互斥条件。

## CPU 验证

- 定向回归：4 passed；
- adapter + predictive 相关回归：270 passed，8 subtests passed；
- 2 个既有测试因当前 shell 缺少 `CUDA_HOME/deep_gemm` 无法 import SGLang，与本次修改无关；
- `compileall` 与 `git diff --check` 通过。

## 下一门槛

下一次短 GPU gate 只需验证：

- wait-shadow publish-to-validation P95 显著低于旧值 2,283 ms，目标先定为 <50 ms；
- TOOL_START-to-publish P95 不再受 100 ms poll 限制；
- `wait_shadow_cancelled_on_reentry` 只清理尚未提交动作；
- worker failure、orphan transaction 和 shutdown masking 均为 0；
- 至少一笔 PREPARE 在真实工具窗口结束前完成验证并进入 D2H queue。

在该 gate 前，不能把 `994de39` 表述为 GPU 性能已经通过。
