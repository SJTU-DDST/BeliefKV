# V124b Full 后的组件消融

日期：2026-10-22，北京时间。

最新执行变更：用户取消第3、8、9项。第3项于北京时间19:52中止，
原始退出码143，保留trace及旧监督器stopped_for_investigation状态；
这是行政取消，不是系统性能或正确性故障。第1、2项已完成并保留。
剩余第4--7项使用补齐种子后的V108/EOS配置接续，再跑同版Full/native；
见[最新接续计划](seeded_eos_modules_v126_20261022_zh.md)。
下表及V125冻结登记描述的是原始历史计划，不再自动执行全部九项。

用户要求最新 Full 之后接续剩余消融，以判断各模块增量。V124b 已冻结
在 `65158cc`，本轮不重启 Full、不修改其 workload。V125 使用最新 main
冻结执行；新增运行时开关默认保持原行为，分别关闭后台备份及终态 child
回收，不将紧急容量备份连同后台备份一起关闭。

## 顺序与问题

| 顺序 | 实验名 | 相对 Full 的改动与要回答的问题 |
| --- | --- | --- |
| 1 | schedule_only | 保留同一 JOIN 排序、短工具续接，关闭自定义 KV，检验 KV 的整体增量 |
| 2 | kv_only | 使用原生请求顺序，保留 KV，检验调度整体增量 |
| 3 | no_tool_continuity | 关闭短工具队列年龄继承和有界续接 |
| 4 | no_join_prefetch | 关闭 EOS/JOIN 主动恢复，保留原生需求恢复和备份 |
| 5 | no_background_prepare | 关闭 idle/spawn 增量备份，保留容量紧急备份和工具卸载 |
| 6 | no_tool_duration_model | 关闭工具时长推断及在线更新，保留非预测的关键容量供体路径 |
| 7 | no_child_reclaim | 关闭自定义终态 child 私有后缀回收，保留原生及 workflow 结束回收 |
| 8 | no_join_priority | 关闭 JOIN parent 优先，保留短工具续接 |
| 9 | complete_restore | 使用完整恢复档位，对照按依赖分档的恢复和状态就绪 |

当前 Full 的普通 execution-frontier restore、工具结束前推测预取、ACK 后
额外驻留保护、最后 child 优先及剩余工作量预取已关闭，不重复为它们运行
“关闭”消融。simple JOIN 下原有 scheduler-only 和 JOIN-only 的有效排序
相同，因此只保留一个 schedule_only；它包含最新短工具续接，不能冒充
历史版本的纯 JOIN-only。

全部沿用 36 roots 一次到达、running/decode 上限 8、seed21、256K 上下文、
95% 压缩、180s 工具上限、200GB Host 及 80:20 池比例、同一任务与引擎。
跨轮复用 subagent 的研究 profile 不进入本矩阵。V122 native 仅作历史
描述性参考，不因本次消融重跑。

## 执行与证据

入口为 `scripts/run_module_ablations_after_full.py`。登记绑定 V124b watcher
的 PID/start_ticks；只有 Full 正常收集、driver 和 watcher 全部退出后才
检查 GPU，逐组等待空闲、NUMA 预检及新预约检查。任一源码/输入变化、
运行时开关不符、遥测失败或新暂停都会停止接续。等待 Full 时仅 CPU
读状态，不额外访问 GPU。每组完成后先后处理再启动下一组。

两个新增开关：

- `BELIEFKV_ENABLE_BACKGROUND_PREPARE=0|1`：仅 idle/spawn 备份，默认 1。
- `BELIEFKV_ENABLE_TERMINAL_CHILD_RECLAIM=0|1`：终态 child 回收，默认 1。

运行时必须记录实际有效值；关闭模块仍有实际动作即拒绝该组。所有组
保留原始退出码、guard、完成质量、实际输出/请求/工具工作量、完整 JCT、
忙时吞吐、恢复等待、有效预取与备份覆盖。每组生成独立时间线、JOIN/tool
重入、迁移生命周期、适用时的后台备份审计，汇总到 module_comparison.json。

Full 与消融不是相同动态轨迹的重放，Full 也冻结于较早提交。本次作为
开发阶段消融，不能把较少生成、guard 或轨迹差异当作某模块确定收益；
各 removal 的差值有交互，不可独立相加。若完整恢复档位没有真实
机制差异，明确记录无活跃机会，不据该组差值宣称档位收益。

CPU 资格保留上一轮全部测试；V2784 为聚焦检查，V2785 为首轮完整检查，
V2786 为顺序执行聚焦检查。最终 V2787 为 4029 项通过、2 个 subtest
通过，保留此前全部 3984 项；同一个历史 Docker 沙箱用例显式 deselect，
无 skip。V2788 绑定应用源码 SHA256，既有引擎资格不变。
登记路径为 `../ops/v125_module_ablations_20261022.registration.json`，
以实际 registration/state 为准，不将登记或测试通过当作 GPU 收益。
