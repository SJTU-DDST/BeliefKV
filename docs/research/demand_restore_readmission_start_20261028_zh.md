# ACK 准入修复的 Position 与 FULL 验证

2026-10-28。独立冻结5cf3bb8b已通过新的position校准，
修复版FULL已进入正式重放，不重复native或ThunderAgent。
本记录尚不提供修复后的完整JCT或吞吐结论。

## 源码与资格

`readmission_position_entry.py/.json`已重新核对全部1815个CPU-manifest
文件、4102个通过身份、四项实际GPU组件及确定性模型I/O证明。
同时核对R2已完整结束、owned进程实际退出，以及正式配置与R1/R2
逐字节相同。新运行只包含ACK后清除遗留满批标志的原生准入重试；
不授予额外容量、agent资格、优先级或恢复TTL。

native仍为729aa0caa7a1。base fa5cb52a加相同累计补丁与已验证的干净
native树完全一致；92项既有native CPU执行对应相同字节。
`native_registration_ack_retry.sources.json`仅将证明中的补丁路径绑定到
5cf3bb8b冻结目录，不把重新打包冒称为重新执行92项或新增GPU证明。

确定性模型I/O与正式数学分开：独立A512/B8输出及logprob差为零，
正常恢复24583token/2416607232bytes，并观察到两个resident decode
在ACK前完成。实际满批重试分支未在该模型fixture中发生，
其证明仍是两个真实native CPU调度用例。旧普通FA3/flashinfer
长greedy等价性失败保持未证明，不能被新position校准覆盖。

## 新的 Position 校准

注册为`ops/demand_prefill_ack_retry_position_20261028_r1.registration.json`，
端口18698，输出
`experiments/raw/demand_prefill_ack_retry_position_20261028_r1`。
实际启动已核对nice0 watcher1035591/server1035866/GPU scheduler1036196。
24次精确token/session请求全部通过，24次均按原生stop结束；
1/8/24位置分派校准的每步wall增量均值分别为
0.009288121/0.003773884/0.010093582ms。原有supervisor已登记FULL，
position watcher1035591及server实际退出、owned清理完成。
这些校准不是纯GPU kernel成本或完整JCT收益。

## 实际 FULL

注册为`ops/demand_prefill_ack_retry_20261028_r1.registration.json`，
输出`experiments/raw/demand_prefill_ack_retry_20261028_r1`。
继续使用完整V230/V311：18个不同项目、3674请求、145565966输入、
549671输出token、72 JOIN、Device805198/Host200GB、
running/decode24、seed21、原始停止和全部正常尾部。

JOIN/EOS、预测工具卸载、prepare_host、agent连续性、sibling共同准入
及8/4 handoff沿用原配置。工具预取、优先窗口、普通frontier restore、
shared profiling和逐decode progress probe均关闭。正式数学继续为
非确定性FA3/flashinfer，确定性模型fixture的参数不进入正式比较。

`readmission_live_start.py`独立核对实际源码、position supervisor
退出、nice0进程/sole GPU、配置、完成回执前缀及ACK重试计数。
完整结束后仍须重新核对全部工作、两次新鲜idle、实际owned退出、
需求恢复ACK/服务/字节、EOS与sibling；计数不等于已获得JCT收益。
其他baseline与消融继续暂缓，整体目标保持active。

启动审计现已实际完成，保存在`readmission_live_start.json`：
nice0 watcher1038516/server1039033/driver1040363及sole GPU1039393
身份和start ticks均验证；pipeline已安装启用，probe/profile/工具
预取/优先窗口/frontier均关闭，8/4 handoff开启。实际正式参数仍是
非确定性FA3/flashinfer及原生采样后位置分派。

封存的230个已完成回执与R1逐请求token、身份及停止条件相同。
当时最后记录快照的ACK原生容量重试计数为0，不能外推为整轮没有
该分支，更不能把后续性能变化提前归因于修复。230是启动前缀，
不是完整3674请求的正确性、JCT、吞吐或修复收益证明。
