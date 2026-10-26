# JOIN 释放与 agent 就绪的一致性

这是已有 agent admission 的边界修复。共享 GPU 预约仍截至北京时间
2026-10-27 11:00，本轮没有登记或启动 GPU 实验。

## 已复现的问题

因果图分别跟踪 JOIN、前台 child 和并行工具。JOIN 完成或超时仅解除
其中一项依赖，parent 仍可能是 WAIT_TOOL、WAIT_CHILD，或者已经开始
等待另一个 JOIN。

此前 AgentAdmission 对 JOIN_SATISFIED、JOIN_TIMEOUT 的全部历史 waiter
直接调用 unblock，并设置 critical。真实 runtime 的 CPU 复现确认：
图仍是 WAIT_TOOL 时，资格层已经是 blocked=None、critical=True。
这会使资格控制错误理解当前阻塞状态，并授予尚未就绪的 owner 额外保护。
它不证明 native GPU 已错误执行该请求，也不证明产生了历史迁移损失。

现在只有 waiter 的当前 JOIN 与该事件一致，且图状态为 READY 时，
才能执行原来的解除阻塞和优先级操作。其他依赖继续阻塞；最后一个
工具结束及后续真实请求注册沿用既有路径。正常当前 JOIN 已全部就绪时，
保留原有恢复优先级。此次未新增请求排序、扫描、预测器、资格轮换、
物理驱逐权限或原生 engine 改动。

新增回归跨四种 Full agent-admission 策略，覆盖并行工具、前台 child、
超时后的新 JOIN、已经消费后继的旧 JOIN 通知及正常就绪恢复。
论文 ThunderAgent 的独立 adapter 不使用此类，不修改其 paper policy。
保留最初夹具配置失败、修正夹具后 24 项失败的复现，以及第一次共享
回归漏传 SGLANG_SOURCE_CHECKOUT 所产生的跳过报告。

## 历史发生率与当前性能问题

`v243_agent_join_release_history_cpu_20261026.json` 在四个完整历史 arm
各自的 14966 条 replay runtime 事件上执行串行图投影。每组 72 个显式
JOIN 释放的 waiter 都属于当前 JOIN 且已 READY，未发现本修复会抑制
的事件。记录的相邻时间戳逆序显式排序，没有丢弃事件或普通长尾。
这个离线投影不重建原服务器 ingress batch，也不模拟实际资格变化。
因此，本修复不应被宣称为 v235 吞吐/JCT 差异的已证实原因。

新的完整末完成依赖路径报告为
`experiments/reports/v243_causal_jct_paths_complete_cpu_20261026.json`：

| 路径分量均值，秒 | ThunderAgent | Full v232 | Full v233 | Full v235 |
| --- | ---: | ---: | ---: | ---: |
| 实际提交至首次 native launch | 634.203 | 605.855 | 503.576 | 443.912 |
| 首次 launch 至 native 完成 | 626.117 | 631.245 | 741.686 | 821.286 |
| 完整 workflow JCT | 1339.764 | 1316.524 | 1324.729 | 1344.922 |

每个 arm 保留全部 workflow、相同请求/token 与原工具时间，路径分量
重建实测完整 JCT；并行 sibling 时间没有相加。不同 arm 的最后完成
依赖路径可以不同，因此这是观测分解，不是单模块因果消融。
首次 launch 至完成包括 prefill、decode、交错调度和其他等待，不能
称为纯 GPU 时间。该证据说明仅降低首次服务前排队不足以保证平均 JCT，
后续仍需同时衡量服务后的完成时间、批次宽度及额外 KV 迁移。

## 后续验证

共享回归使用固定的 free-page fence engine 源码，完整保留 v242 的
2123 项 case 身份和出现次数。实际执行通过 2155 项及四个子测试；
合并报告为 `v243c_agent_join_release_shared_cpu_20261026.xml`。
它由一次 2153 项通过的共享执行和两项 Docker 夹具补跑组成，
精确替换相同身份的两个跳过项，原报告均保留。这不是一次 pytest
执行；组装来源与镜像 ID 单列于
`v243_agent_join_release_suite_assembly_cpu_20261026.json`。
源码和结果绑定在
`v243_agent_join_release_sources_cpu_20261026.json`。
CPU 成功不替代预约结束后的真实 writer/copy 与 session 模型检查。
Full 与 ThunderAgent 正式比较使用相同共同 engine 开关、原 v230
完整工作量、容量及 seed21，继续要求完整平均 JCT 与全程吞吐均有
明确可重复收益，再运行其他 baseline。
