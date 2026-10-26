# 工具后继的阻塞预算归还候选

当前保留式 agent 调度允许已经阻塞且具备 settled Host 副本的 owner
借出执行资格。原有 JOIN 后继可以在实际提交后收回记录中的借出资格，
工具后继尚无对应路径。本候选只补齐真实工具返回后的需求归还，
不在预测结束时间提前抢占 ready agent。

## 开关与边界

`AGENT_TOOL_LOAN_RETURN=1` 和对应 `BELIEFKV_` 参数显式启用，
默认 0。必须配合 `thunderagent_retained` 与
`AGENT_BLOCKED_LENDING=backed_demand_recall`。原有配置保持关闭；
native、其他消融及论文 baseline 不继承此开关。

只有匹配的全部工具实际结束，且没有仍未完成的前台 child 或 JOIN，
才记录工具返回。记录本身不改变资格；必须等同一 agent 真正提交
后继请求。随后仅在记录中的 borrower 已结束当前请求、没有 running、
JOIN 或恢复保护时，才考虑归还。先确认可归还的完整预算足够，
再执行切换，避免容量不足时先暂停一部分 borrower 却无法推进 owner。
已有空闲预算足够时直接恢复资格，不暂停 borrower。

JOIN 归还先于工具归还。工具路径每次 rebalance 至多成功归还一次；
不会驱逐无关的原有 ready owner。新阻塞事件使旧返回记录失效，
实际 native 准入消费该记录。原生分配、共享前缀、锁和 DMA 权限不变。
工具预取仍不能借此获取普通恢复预留或提前夺回执行资格。

## 验证与限制

定向 CPU 回归通过 494 项，报告为
`experiments/reports/v237_tool_loan_return_audited_cpu_20261026.xml`。
覆盖并行工具最后返回、预测不抢占、实际提交后继、running borrower、
JOIN 和恢复保护、预算不足、无关 ready owner、配置隔离及真实 runtime。
loan auditor 分开验证 JOIN/工具计数、参数、边界和计数单调性，
仍只证明采样中的逻辑关系，不将资格预算当作实际独占可回收页。

依赖状态回归另通过 361 项及四个子测试，报告为
`experiments/reports/v237_tool_loan_return_current_cpu_20261026.xml`。
修复了并行 TOOL_END 错误越过未完成 JOIN/前台 child，以及超时后旧
JOIN 完成影响新 JOIN 的情况。此前 v230 离线事件审计没有发现前一种
状态差异，不能把该修复当作历史性能回撤的已证实原因。

当前仅为 CPU 验证的独立候选，尚未登记或运行 GPU。
额外的资格归还可能增加迁移，不能宣称降低 JCT 或提高吞吐。
尚未解决 inactive lender 的预测工具预取，应与真实冷页、容量和
执行资格的协同另行设计，不通过本开关隐式启用。
