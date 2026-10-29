# ACK 准入修复 FULL 的完整结果

2026-10-28。冻结应用5cf3bb8b/native729aa0caa7a1完成全部18个不同项目、
3674请求、145565966输入和549671输出token；原始停止、全部正常尾部、
两次新鲜idle和实际owned进程退出均已验证。
R1/R2保持原01510ed5冻结状态，本轮是包含修复的独立运行。

## 完整比较

| 完整系统 | 平均JCT/s | 全程输出token/s | decode worker token/s |
| --- | ---: | ---: | ---: |
| FULL R1 | 1613.659476 | 239.544631 | 343.922082 |
| FULL R2 | 1625.203855 | 238.306858 | 343.713900 |
| 本次ACK修复FULL | 1617.896789 | 238.405436 | 343.098284 |
| 准入前卸载FULL | 1631.194643 | 238.499008 | 346.325953 |
| V345工具衰减ThunderAgent | 1697.583645 | 233.371801 | 340.158312 |

本次相对V345的JCT/全程吞吐改善4.69413%/2.15692%。
两个同源码重复R1/R2分别改善4.94374%/2.64506%和4.26370%/2.11468%。
完整工作上的正收益已重复出现，但幅度仍有限，V345对照只有一次完整运行，
且使用明确披露的不同native引擎11b84f7c3198，不能宣称统计显著性、
单模块收益或超过所有baseline。

相对准入前卸载，本次JCT快13.297855秒、约0.81522%，全程吞吐低0.03923%。
相对R1，JCT多4.237312秒、吞吐低0.47557%；相对R2，JCT少7.307066秒、
吞吐高0.04137%。这些正负结果全部保留，不选择最优运行作为唯一结果。

## 修复没有触发

447次staged demand中，445次走到完整ACK后的ready分支，另外两次走
原生准入回退。全部445条ready日志的`retry_native_capacity_check=false`，
末次记录计数也为0。修复的原生容量重试本轮没有实际发生，不能用它
解释相对R1/R2或ThunderAgent的性能变化。它仍是两个真实native CPU
用例证明的正确性修复，不是本轮实测性能创新点。

## 原始24.98秒问题

固定原来的2973个历史工具后继、467561输出token，相对准入前卸载：
prefill成员区间-2.853132秒、decode+13.425704秒、成员之外-20.425701秒，
首次服务到完成合计-9.853128秒。首次服务前等待仍多59.143803秒。
这些固定历史请求与每轮实际关键路径分别报告，不能加到完整JCT上。

按本轮实际关键路径，首次服务前等待+1.350547秒、服务到完成
-15.237101秒，其余事件净约+0.588699秒，总JCT-13.297855秒。
worker区间包含运行时和传输依赖，历史新增独占控制CPU仍未知。
不能将上述分解或不同运行之间的差值当作纯kernel、DMA或控制CPU。

workflow TTFT均值132.160374秒，对照139.028403秒；每workflow最大
post-first ready-output gap均值83.807889秒，对照73.449107秒。
后者明显回退，故没有声称workflow交互性或公平性全面改善，也没有删尾。

## 机制证据

447次staged native demand、716个成员关联全部验证精确ACK及后续服务，
共23142336token/2274984198144bytes，完整未标记H2D对账为：

```text
2493235101696 untagged H2D bytes
= 213463203840 native request Host-hit bytes
+   4787699712 handoff consumer bytes
+ 2274984198144 staged native demand bytes
```

未解释余量为零，不修改原生Host命中计数。实际H2D/D2H为
2715.928855/1247.064883GB。八次8/4 handoff恢复4.787700GB，
只占总H2D的小部分；一项JOIN物理计划释放44467token，原生释放日志
与计数对账通过。累计释放不等于同时空闲或准入容量信用。

64个已观察JOIN预取episode中，23个完成前启动并确认复用，
约97.620984GB；源轨迹仍为72个JOIN。54个final-EOS触发全部观察到
最终RETURN，四个历史工具反例均通过原分类器，工具预取为零。

1056条sibling日志、1090个请求尝试全部验证实际批次与完成session；
34条多成员日志对应33个独立native forward。237个grant/program关联
中235个验证后续服务，1个未观察到后续服务、1个身份未证明，均保留未知。

## 证据与下一步

完整结果、机制原字节归档和索引在
`experiments/reports/prefill_pipeline_20261028/readmission_complete/`；
实际关键路径与服务分解另保存为`readmission_paths.json.gz`、
`readmission_service_interleaving.json.gz`。完整趋势、分布与geometry图在
`experiments/figures/demand_prefill_ack_retry_20261028_r1/`，三幅已实际打开核对。

不再为未触发的ACK分支重复同配置FULL。下一步转入剩余baseline的本地
适配与必要资格检查；正式比较仍使用同一完整V230/V311，不重采native、
不重跑V345、不筛选获胜trace。当前KVFlow旧入口显式拒绝非空native
session，而本负载的真实native回执包含session，不能直接沿用AgentX
的sessionless入口。TokenCake也需检查同入口行为，Agentix的正式入口
仍关闭，不能把有界qualification模式作为正式baseline。
这些适配尚未完成，整体目标保持active，尚未证明超过所有baseline。
