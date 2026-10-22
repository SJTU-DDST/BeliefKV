# 工具预取独立开关与在线经验 CDF 修复

本修改位于 main，只通过 CPU 资格验证，没有注入 V128 冻结 Full/native
队列，也没有启动竞争的 GPU 实验。工作目标仍未完成。

## 工具卸载与预取分开验证

实际启动脚本曾把 `BELIEFKV_ENABLE_TOOL_PREFETCH` 直接重设为
`ENABLE_TOOL_TIMING`，因此无法通过关闭前者单独测试工具预取。
关闭工具模型的现有消融同时去除了预测卸载依据与工具预取，不能隔离两者。

现在 `run_qwen35_native_regime_probe.sh` 的优先级为：

1. `BELIEFKV_ENABLE_TOOL_PREFETCH`。
2. 显式 `ENABLE_TOOL_PREFETCH`。
3. 未设置时沿用 `ENABLE_TOOL_TIMING`，保持已有默认行为。

设置必须为 0 或 1，在创建运行目录和启动服务前验证。reactive 与 native
仍将最终工具预取关掉。launch configuration 增加工具模型和预取的请求值，
有效状态继续通过 runtime 记录验证，不把请求值当作有效值。

CPU 预设示例：

```python
environment = arm_environment(common, "full", tool_prefetch=False)
validate_runtime("full", observed_state, expected_overrides={"tool_prefetch": False})
```

在当前 speculative 配置下，该预设相对 Full 只改变工具预取开关。
在线工具历史、工具时长 artifact、prepare、冷页回收资格、JOIN EOS、
阶段优先和 resident-first 保留。旧模式列表、历史消融顺序及当前队列不变。
若显式使用 execution 模式，关闭这个源开关也关闭依赖它的 frontier restore。

验证覆盖真实 shell 启动参数和最终环境，并构造完整运行时：
在工具 H2D 关闭时，从已完成工具历史仍生成预测，长等待 agent 仍可进入
冷状态候选；工具结束后资格撤销。测试未声称执行了真实 GPU DMA。

## 修复经验分布被插值

在线工具模型由已完成和删失样本生成 Kaplan–Meier 阶梯分布，但
`NativeToolWaitHint` 曾用校准模型的 `ActionTimingCurve` 解释它。
后者在第一个点前作线性插值、点间作 log 时间插值，会在未观测到完成的
区间人为分配结束概率，改变长等待门槛和预取时机。

CPU 复现：四个历史工具均为 10 秒，当前工具已等待约 1 秒，经验剩余
时长约 9 秒。旧路径算出未来 1 秒结束概率约 11.1%，大于卸载门槛
10%；经验分布在该区间的结束概率为 0。

新增 `EmpiricalActionTimingCurve` 复用已有验证，以二分查找解释阶梯
CDF 和逆分位数。仅 `online_workflow_command`、`online_run_command`
使用该类型；校准模型继续使用原有插值。超过已观测尾部后仍保持未知，
不把生存超时当作必然即将结束。没有新增定时扫描、文本处理或 GPU 同步。

V2920 固定最新 Full 的封存输入，仅在每条预测发出时重算分布门槛：

| 诊断 | 计数 |
| --- | ---: |
| 在线预测记录 | 7349 |
| 原插值语义满足长等待 | 602 |
| 阶梯语义满足长等待 | 862 |
| 判定改变的预测 | 260 |
| 涉及的 context/revision | 104 |
| 各 context/revision 首条预测中新增长等待 | 41 |
| 上一行有唯一活跃工具完成边界 | 40 |
| 上一行实际剩余不少于卸载门槛 | 39 |

预测门槛没有使用未来完成时间；完成边界只作为事后评价标签。
104 个修订不等于额外可安全卸载 104 次，41/40/39 也不是独立留出集
准确率。这里没有重放容量、物理依赖或队列，也没有 GPU/JCT 收益结论。
固定原输入重算时，候选 P50 均与记录的经验 P50 对齐；P10/P90 仍可能
因离散概率边界的浮点表示而不同，不宣称所有分位数逐位一致。

## 验证与证据

V2918：4490 项通过、2 个 subtest 通过，无失败或 skip；明确取消选择
1 个既有的可选真实 Docker progress 测试，没有声称运行它。
完整保留 V2911 的 4444 个 testcase 身份，新增 46 项。

覆盖真实启动器的独立开关及优先级、native/reactive 隔离、运行时在线预测
与冷页资格，以及两级历史、重复时长、删失、年龄、CDF 和分位数边界。
既有校准模型和工具调度测试继续通过，shell 语法与 diff 检查通过。

V2919 将资格报告绑定到当前应用、脚本、测试、配置和补丁源哈希。
测试隐藏 GPU，并显式选择已资格验证的 FULL 扫描连续性候选引擎。
V2920 保留输入前缀、客户端标签输入和参考/候选实现哈希。

GPU 验收仍需在工作量与完成质量可比的条件下，检验暴露恢复等待、完整
workflow JCT、吞吐、重复恢复及备份压力，不能仅按新增卸载资格决定胜负。
