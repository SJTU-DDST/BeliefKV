# Original69 对照复用、Oracle 标定与新增 19 原题采集

更新于 2026-10-31 PDT。持续到达比较继续采用 **12 初始 workflow、
平均 70 秒到达**。这一选择依据 ready 供给不足及原有匹配控制，
没有分别运行 8/12 再选择胜幅更大的结果。
新 19 项目只补采缺失的原项目，已有完整 trace 和对照不重跑。

## 已完成的 ThunderAgent 对照

两臂固定到达序号的 18 个测量成员均完整结束，
精确执行 2827 请求、390934 输出 token，容量、工具映射、
到达表和测量窗口相同。收据、新鲜 idle 排空和实际退出已独立复验。

| 指标 | ThunderAgent acting-decay | Predictive 非排序控制 | 改善 |
| --- | ---: | ---: | ---: |
| 平均完整 JCT | 889.845843 s | 813.056404 s | 8.629522% |
| P95 完整 JCT | 2040.038743 s | 1887.050689 s | 7.499272% |
| 共同墙钟窗口全部输出 | 278.298208 token/s | 283.958674 token/s | 2.033957% |
| 共同墙钟窗口 decode 输出 | 275.887363 token/s | 281.514985 token/s | 2.039826% |

18 个测量成员均数值改善。这是非排序 Predictive 的整体结果，
不是 Oracle、R5 组合评分或容量规划的独立收益，也不是统计显著性证明。
ThunderAgent 最后测量完成比最后背景到达晚 109.106735 s；
Full 共同窗口 ready-paused 状态样本仅 245/1547，约 15.84%。
既定 workload 和正式性能验收仍未全部通过，原失败不作豁免。

详细结论与已查看的配对 JCT、吞吐趋势图，沿用
`original69_thunder_complete_and_oracle_capacity_20261031_zh.md` 及报告目录
`predictive_thunder_original69_initial12_mean70_comparison_r1/`，
没有增加一次相同配置的 ThunderAgent GPU 实验。

## 匹配控制的成本重新标定

旧 Original69 full-batch 准备明确沿用了 Original56 的成本。
现从已完成的 Original69 非排序控制重新封存完整 worker/传输日志，
再次验证测量收据、JCT 尾部、排空和退出，得到：

- batch/context 加权 decode 每步代理为 **19.300289736 ms**，
  与原完整测量 review 独立重算一致；
- substantial prefill 的全局中位成本为 **0.072633137 ms/token**；
- 保留原先五个上下文区间、单请求且新增输入至少 256 token、
  每区间至少 30 样本的规则。

| 原生 prefix 加新增输入的上下文区间 | 样本数 | worker ms/新增 token 中位数 |
| --- | ---: | ---: |
| ≤8192 | 632 | 0.034570143 |
| ≤32768 | 3482 | 0.057825710 |
| ≤65536 | 1120 | 0.102327584 |
| ≤131072 | 932 | 0.171311023 |
| >131072 | 301 | 0.308541785 |

这些是可能包含停顿及提交开销的 worker 成本代理，不是纯 GPU 时间。
非测量背景按既定协议右删失，不能称 69 个 workflow 全部完成。
原始任务、工具时长、请求、到达、测量组及关闭的功能都没有因此改变。

证据位于 `original69_completed_control_costs_r1/`。
CPU-only 的 `original69_planned_capacity_oracle_prepare_r1/`
绑定新标定，实际构造确认 `plan_device_capacity=true`、
`decode_cost_basis=full_batch`，成本身份为
`d4b2047786dce011fc285c9f680a533f22e47cd7fedcac9b68b8ca69e4673492`。
69 个初始剩余路径在严格容量参考与规划版本间逐一相同。
完整 FULL 池几何只作为逻辑规划上界，原生分配、Host、锁和 DMA 权限不变。
本准备没有登记 Oracle GPU 后继，不能称已获得新 Oracle JCT 收益。

## 新增原项目与工具边界

从同一公开原始归档中补充 19 个尚未采集的编号家族，
其中 12 个 TypeScript、7 个 JavaScript；
原难度为 6 easy、4 medium、4 hard、5 expert，均保留完整原题。
784 个原源码/测试文件和完整上下文重新对照归档，未修复项目测试、
接口或任务目标，未缩减题目要求，也未填充工具时间。
新项目实际工作量、短 child、多成员 JOIN 是否足够仍需自然 trace 证明。

工具资格分两次完成并明确组装：

1. `remaining_node_original19_tools_r1` 有 18 个通过；
   networking_099 因原项目要求 Node `<19`，实际 Node20 不兼容而失败。
2. 为该项目提供 Node18.20.8；实际 root/child 的 shell、共享工作区、
   非零退出、原实现文件 SHA 读取及私有 TypeScript 编译/执行均通过。
   其余 18 份已通过的原证据未重跑，R1 失败保留。

TypeScript5.7.2/ts-node10.9.2 工具镜像：
`sha256:d575c45c64ab3e213cc41620b11d5cab81d9047cacac0d5178b1f706615c174b`。
仅 networking_099 的兼容镜像：
`sha256:86fa0df8c243609d376a2e7ec7f8e98db9936bd3f9cf28e5985ccbee76bf3355`。
工具检查不宣称应用依赖全部安装或项目测试通过。

完整 CPU R1 有四项因隔离 checkout 缺少未入库的旧测试数据/
参考源码而失败；原 XML 与 FileNotFoundError 保留。
接入原文件并固定其哈希后，完整 R2 单次执行通过
**6532 项与 23 个子项**，保留全部既有测试身份、
2391 个源码输入，无失败、错误、跳过，源码、外部输入及原生引擎未变。
没有修改测试断言或项目源码来通过资格。

## 实际 GPU 状态与后续边界

采集操作源冻结为 `185145f1`；同一原题声明已以 `ab18b3e9` 纳入 main。
注册为：
`ops/workflow_remaining_node19_source_20261031_r1.registration.json`。

实际采集为 **19 总项目、12 个在途 root、running/decode24**，
同一 Qwen3-Coder、原始采样 seed21、child4/8、root8/16 软预算。
这是 source collection 的客户端窗口，不是正式持续到达实验的并发上限。
正式比较仍使用统一开放到达表，测量成员全部完成后即可停止非测量背景。

最新独立启动证明 `remaining_node_original19_gpu_entry_r1/live_start_r1.json`
记录 `recording_native`、新鲜 CUDA sampling 检查通过、唯一 GPU
scheduler3537497，以及 nice0 watcher3536692/driver3537131。
在该封存时点，363 份 prompt、349 份 output 已捕获，writer 无错误。
这是前缀证据，不能称完整 19 项完成或 workload 已符合条件。
实际 Device805198、Host2034506 token/200GB，FULL-only。

CPU 收尾观察器3538676 已实际运行于 nice19/CUDA hidden，
等待 19 个原题自然完成、精确 token/依赖、排空和实际退出后，
审计完整工作量、JOIN/child 原生服务及工具执行记录。
它不会启动新的 GPU 实验。收尾文件位于
`remaining_node_original19_finish_r1/`；注册和日志均已固定。

后续复用已有 81 项目源与新增原题，按固有模型工作量和工具关键路径
构成更长的固定背景流量，报告 P80/P20≥3 与≥4备选，
保留完整原题、不同项目和正常尾部。先检查 ready-paused 容量竞争、
实际服务、Host 有用状态丢失及双方背景覆盖，再做匹配 Oracle 诊断。
只有明确的增量 Oracle 收益后才进入学习预测器阶段。
JOIN/EOS、prepare_host、预测工具卸载保留；工具预取、
普通恢复 lease、逐层 handoff、mixed chunk 仍关闭。

本轮操作代码的逐字归档为
`remaining_node_original19_operator_archive_r1/`；
这是审查快照，正在执行的操作路径和源码不热修改。
