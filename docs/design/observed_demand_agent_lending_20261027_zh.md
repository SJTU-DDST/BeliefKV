# 基于真实阻塞需求的 agent 资格借出

本候选为 `configs/migration/predictive_demand_lending_20261027.json`，
在动态工具成本候选上只改变逻辑预算证明：
`AGENT_BLOCKED_BUDGET_PROOF=observed_demand`。
原有 `predicted_pressure`、`observed_blocked` 和历史结果保留。
CPU 正确性不能证明完整 GPU 性能改善。

## 证据与问题边界

V286 Full 与 V295 optional ACTING decay 使用相同完整工作量、V269
工具计时、容量、engine 和 seed。Full 全程吞吐 189.340 token/s，
衰减对照 236.450 token/s；平均 JCT 分别为 1669.294s、1700.062s。
Full 传输更少，但平均 decode batch 为 3.143，对照为 5.023。
不能从迁移减少直接推断吞吐收益，也不能将该差距归因于尚未运行的
动态物理卸载候选。

完整 V286 Full 的同快照逻辑预算审计为：
`experiments/reports/v286_full_tool_blocking_budget_audit_20261027.json`。
2812 个快照全部对账，原生请求区间 2871.453s，采样覆盖 2869.862s，
未覆盖 1.592s。活跃预算 token·时间中，模型 pending 为 34.787%，
已知阻塞为 61.713%，请求间为 3.500%；平均另有 3.937 个 pending
agent 没有 active 资格。这些是逻辑预算，阻塞包含 JOIN 与工具，
不是实际 HBM 占用、独占冷页或可消除等待的测量。

旧规则实际已有一秒 elapsed-tool 阻塞标记，但借出逻辑预算仍要求完整
已确认 Host 检查点。预测无支持、增量尾部未备份或某段正在传输时，
这个证明可能失败，即使原生按需缓存可以安全处理后继的实际分配。
保留全部资格并不能保护 GPU 的有效并发。

## 新规则

仅在真实已提交的 pending agent 需要逻辑容量时，复用现有借出事务：

1. donor 仍 active、确实阻塞、没有 pending 模型请求或在途计算，
   不是关键恢复对象，且没有有效的恢复、工具预取或驻留保护。
2. 运行时因果图必须证明实际 WAIT_JOIN、WAIT_CHILD 或有实际 active
   tool 的 WAIT_TOOL。后台 SPAWN 本身不提供 parent 阻塞证据。
3. context、epoch、workflow、native session generation 必须有效，
   关闭或替换的 session 不能借出。
4. 工具实际等待至少一秒才允许无预测借出。可信的当前长工具预测
   可以更早触发；不读 trace 中未来的结束时间，也不把已等一秒
   当成未来一定还要等待一秒的预测。
5. 选中的真实 donor 逻辑预算合计足够后才暂停它们，再启用受益
   agent；不选择 ready peer，不修改其正在执行的 batch。

维护周期仍为一秒，每次至多八个 donor 检查、一个准入。新证明使用
O(1) 身份与状态检查，不遍历整个前缀或做完整 Host 检查点扫描；
预取租约检查复用当前已有的小集合。资格借出不分配 Device 页，
不修改 native tree、锁、Host 副本或 DMA，也不授予物理容量 credit。

后继获得逻辑资格后仍须通过原生准入。若物理空间不足，原生按需
write-back、驱逐和 H2D 恢复负责真正取得空间；这仍可能产生等待，
甚至在 Host 压力下造成重算，必须在 GPU 实验中审计。原生需求恢复的
驱逐选择与“普通工具预取不得驱逐 ready agent”的权限不是一回事，
新候选没有扩大普通预取 donor 范围。

## 与预测及预取的组合

`demand_cost` 物理提前卸载仍要求实际容量需求、支持的剩余分布、
90% survival、迁移成本门槛、完整的单页备份与 native 释放条件。
该策略的弃权只禁止主动提前卸载，不再否决上述逻辑需求回退。
JOIN/EOS、工具预测预取、短工具续接、增量备份及共同准入均保持基线
候选配置，不重新引入普通 execution-frontier 预取。

使用 V311 的可观察类别工具计时与独立离线先验；新 Full 必须与新运行
的 ThunderAgent optional decay 使用相同计时文件。V286/V295 的 V269
数据只能解释设计动机，不能作为新候选的匹配性能对照。

## 验证

`tests/test_demand_agent_lending.py` 检验实际 native tree/allocator：
无 Host 副本且无工具预测时可以转移逻辑资格，allocator 空闲页不变；
真实短缺仍然分配失败，只有原生备份 ACK、demotion 才提供后续分配。
覆盖短工具、返回、context/session 变化、在途计算、恢复保护、无真实
等待计算、后台 SPAWN 与实际 JOIN，以及 paper arm 的隔离。
CPU copy、event 和 Host 索引仍是 stand-in。

保留 V323 初始夹具缺少创建事件、V324 租约夹具收尾错误的失败报告；
修复测试事件和使用真实租约类型，不放宽运行时身份或释放检查。
完整 shared CPU 必须保留 V321 全部用例。之后先验证新 Full 的完整
工作量、DMA 回执及正常尾部，再运行相同工作量的最强 ThunderAgent
对照，联合观察完整 JCT、全程吞吐、有效 batch、原生恢复和重算。

V325 的一次完整 shared CPU 执行通过 2916 项及四个 subtest，无失败、
跳过，保留 V321 全部 2890 项并新增 26 项。V326 绑定源码、配置、
时间表以及真实构造检查：Full 为 `observed_demand + demand_cost`，
加载固定 SHA 的 TraceLab 先验；两个 ThunderAgent arm 没有这些扩展。
这是 CPU 资格，尚未证明 GPU 运行或完整性能。
