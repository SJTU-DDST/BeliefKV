# 以当前阻塞状态和容量缺口选择归还对象

这是基于完整 v230 同工作量结果的独立 CPU 候选，入口为
`configs/migration/predictive_cold_fit_recall_20261026.json`。
尚未登记或运行 GPU；预约截至北京时间 2026-10-27 11:00。
原默认 `largest`、v232/v233/v235 结果和冻结树均保留。

## 新核对的迁移来源

`scripts/audit_agent_restore_churn.py` 在四个完整 arm 上重新核对请求、
停止条件、遥测排空和实际拷贝。在 FULL-only 的实际 Host 几何下，
每个 arm 的全部 native demand H2D 字节恰好等于请求 Host-hit token
之和乘每 token 98304 字节，受控 JOIN 拷贝独立计数，没有混加。

| 十进制 GB | ThunderAgent | Full v232 | Full v233 | Full v235 |
| --- | ---: | ---: | ---: | ---: |
| 全部 H2D | 216.496 | 341.792 | 445.022 | 647.027 |
| 受控 JOIN H2D | 0 | 202.837 | 112.621 | 111.931 |
| native demand H2D | 216.496 | 138.955 | 332.401 | 535.097 |
| JOIN 后继的 Host-hit 关联量 | 176.753 | 106.776 | 298.047 | 366.740 |
| 工具后继的 Host-hit 关联量 | 20.686 | 18.825 | 15.298 | 147.889 |
| 不超过 200ms 工具后继的关联量 | 18.087 | 18.778 | 15.281 | 145.205 |

v235 的短工具后继中，有 28 个实际需要 Host 恢复；v233 为六个。
现有预取生命周期报告中，v235 JOIN 的 111.931GB 中约 109.747GB
有实际首次 FULL 复用证据，剩余部分保持 unknown，没有把它判为浪费。
因此不能把全部新增 H2D 归因于“预取未被使用”，也不能只继续提前拷贝
而忽略保留资格和恢复之后的迁移。

资格采样把 v235 约 501.735GB 需求恢复关联到曾观察为 inactive 的
同 agent 区间；约 10.935GB 的区间保持 sampled-active，另有首次调用
或观测缺口。采样无法排除两次快照之间的切换，不证明具体驱逐原因。
每请求 Host-hit 分类是聚合字节核对后的关联，不是对单条 DMA 的分配。
报告为 `experiments/reports/v242_agent_restore_churn_complete_cpu_20261026.json`。

旧召回对合法 borrower 按 token 预算从大到小排序。即使只缺较少资格，
也可能先暂停一个远大于缺口的 agent。历史快照里有“约缺 2.5 万，
观察到约 9.5 万预算移出”的区间，但未记录原时刻全部安全候选，
不能宣称另一个更小对象当时一定可选，更不能据此估算可节省的 JCT。

## 选择规则

显式 `AGENT_RECALL_SELECTION=cold_fit`，默认仍为 `largest`。
仅在 `thunderagent_retained` 的 `backed_demand_recall` 中使用。
同原方案一样，只有真实提交的 JOIN 后继等记录中的 owner 才可归还，
EOS 或 speculative restore 不单独获得召回权。

先保留原有合法候选集合：确实属于该借用期、仍 active、非 in-flight、
非 critical、无尚未过期的恢复保护。先核对所有合法预算足够，再修改
任何 agent 状态；不足时不执行部分暂停。

在这些候选中分四档：

1. 已知阻塞且没有 pending 模型请求。
2. 两次模型请求之间、没有 pending 请求。
3. 普通 pending/ready agent。
4. 已经 pending/ready，且是另一个真实阻塞 JOIN 的唯一剩余 child。

第四档使用现有 `_join_by_invocation` 索引及实际 WAIT_JOIN 状态查询，
只在真实归还时检查，不逐 decode 步扫描 DAG。它是最后备选，仍允许
在没有其他足够预算时归还，避免变成无限保护。等待工具且无可运行
模型工作的 child 不因为其父在 JOIN 而占据这一档。

每档按预算升序：若一个 agent 能覆盖剩余缺口，选择其中最小者；
否则先取该档最大者缩小缺口，再查找能单独收尾的对象。低档容量用尽
才进入下一档。这是低成本贪心规则，不声称全局最优或总能最少迁移。
它没有字节级共享页计费、未来工具结束时间或输出长度 oracle。

实际暂停、借用关系归还及 parent 激活成功后，记录一条
`agent_loan_recall_decision`，包含当时完整合法候选、状态档位、缺口、
选中集合及归还预算。日志明确是逻辑资格，不能当作已释放物理页。
无在途中断、额外 D2H、普通 execution-frontier 恢复或 ready-peer
工具预取驱逐权限。原生分配、Host ACK、锁和 DMA 仍决定物理动作。
JOIN 先于工具归还；现有工具归还开关仍保持关闭。

## 控制成本和验收

`benchmark_agent_recall_selection_cpu.py` 执行真实 policy 归还逻辑，
包含 decision 字典构造和 append callback；每档预热五次、测量两百次。
1/4/16/64 个候选的单对象归还平均 thread CPU 分别约为
0.0195/0.0273/0.0499/0.1255ms；原 `largest` 为
0.0140/0.0186/0.0330/0.0843ms。预算不足时保留原快速拒绝路径。
不包括 fixture 构造、真实 native cache、后台序列化或 GPU 时间。
这仅是罕见归还点的 Python 成本，不是完整控制开销上界或性能收益。
最终来源绑定报告为
`experiments/reports/v242b_cold_fit_recall_control_final_cpu_20261026.json`；
较早的测量也保留。

真实 runtime constructor 已在隐藏 CUDA 下确认 Full 使用 `cold_fit`，
ThunderAgent 不启用 Full agent 策略且不继承该选择；两者共用相同
free-page H2D fence。CPU 数值替身与选择测试不构成 GPU 数值资格。
共享报告 `v242_cold_fit_recall_shared_cpu_20261026.xml` 通过 2123 项
及四个子测试，无失败/跳过，完整保留上一轮 2083 项。
最终源码与各报告绑定于
`v242_cold_fit_recall_sources_cpu_20261026.json`。

GPU 预约结束后仍先完成共同 engine 的真实 writer/拷贝及 session 模型
检查，之后以原 v230 请求、token、工具时间、容量和 seed21 比较。
同时看完整平均 JCT、全程吞吐、实际 H2D/D2H、重算、JOIN/工具等待，
以及是否将损失转移到别的 workflow。只有前两项取得明确可重复收益
才启动其他 baseline。不能预先承诺消除上述 145.205GB。
