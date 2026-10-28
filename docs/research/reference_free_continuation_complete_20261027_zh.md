# 完整 continuation 准入实验

本轮 Full 已完成全部工作，平均 JCT 和全程输出吞吐仍没有达到明确、
可重复地优于强 ThunderAgent 的目标。保留本轮结果，不启动其他
baseline 或消融，不把不同配置的历史运行当作重复试验。

实验名称仍为 `reference_free_continuation_20261027_r1`；实际结束时间为
2026-10-28 08:08:53 Asia/Shanghai。应用冻结在 `1f1678b266e1`，
native 树为 `11b84f7c3198`，复用完整 V345 工具衰减版 ThunderAgent。
实际 watcher、server、GPU scheduler、driver 及 CPU 绘图等待器均已
退出，原始退出码为零，两次新鲜空闲遥测、清理和完整工作校验均通过。

## 同工作量比较

保持完整 V230 轨迹：18 个不同项目、3674 个请求、145565966 个输入
token、549671 个输出 token、92 个 child 和 72 次 JOIN。V229 因未满足
并行 JOIN 合同而被拒绝，不能替换成合格来源。V230 的全部 root 使用过
最终返回前的委派提醒，没有 grandchildren；这些限制保持披露。

V311 仍是显式合成工具时间：931 个 execute 使用固定的 TraceLab
类别可见、按公开会话拆分留出的时长样本，其余 2543 个工具保留原始
时间。每种方法使用相同映射，未来实际结束时间不提供给在线策略。
模型、容量、running/decode24、种子21及流式协议均不变。

| 指标 | 本轮 Full | V345 ThunderAgent 衰减版 |
| --- | ---: | ---: |
| 完整平均 JCT / s | 1630.055849 | 1697.583645 |
| Workflow event makespan / s | 2328.717512 | 2355.344551 |
| 全程输出 token/s | 236.040223 | 233.371801 |
| Decode worker 输出 token/s | 351.421309 | 340.158312 |
| Worker 时间加权 decode batch | 8.348794 | 8.108286 |
| Workflow 首输出均值 / s | 135.390411 | 139.028403 |
| 各 workflow 最大后续 ready 输出间隔的均值 / s | 72.794393 | 73.449107 |

平均 JCT 改善 3.97788%，全程输出吞吐改善 1.14342%。17 个 workflow
更快，`game_simulation` 慢 25.059875 秒，完整保留。相对上一轮 EOS
修复 Full，本轮平均 JCT 改善 1.02052%，输出吞吐下降 0.59148%。
不能据此把新配置宣称为吞吐更优的默认配置。

全程输出吞吐受固定总输出及 makespan 决定，不是独立的 decode
kernel 指标；worker 区间也包含运行时和等待。共同 60 秒分桶趋势、
时间加权分布及相同 batch/context 档位比较均保留全部正常尾部，没有
按胜负选时间段或在较早结束的一臂后面补零。

## 真实准入与迁移

283 条成功授权日志覆盖 286 条 program 服务关联，全部通过相同
request/context/epoch/session/clock 的原生服务检查：

- 191 条普通 continuation 关联；
- 91 条 sibling 授权覆盖 94 条关联；
- 一条 JOIN parent 授权关联。

这些是实际逻辑准入及其服务消费，不等于逐项因果收益、GPU kernel
时间或新增独占容量。它们保留既有 agent 连续性及原生分配权限。

| 完整物理传输 | Full / 十进制 GB | ThunderAgent / 十进制 GB |
| --- | ---: | ---: |
| H2D | 2679.432806 | 2809.042010 |
| D2H | 1079.603331 | 2255.862596 |

H2D 下降 4.61400%，D2H 下降 52.14233%。Full 的非标记需求 H2D 为
2631.645757 GB，其中工具后继关联 1992.705245 GB；这些请求 Host
命中与总需求传输严格对账，但不能把每个 Host 命中等同于独立 DMA。

JOIN 标记恢复共 366 条命令、47.787049 GB，全部证明复用。63 个有
预取身份的 JOIN 事件中，55 个完成前有正 FULL 计划，五个提前启动且
证明复用 22.562734 GB。它们是 72 次真实 JOIN 中的被观测子集，不是
JOIN 数变少。授权关联的 7.910523 GB 还包含完成后拷贝，不能替代前述
提前复用量。工具预取实际拷贝仍为零。

四个旧工具结束符反例全部通过；55 个实际 final-EOS 触发都有真实
final RETURN，未发现被触发的中间工具请求。

两臂同会话旧输入探针均有 3564 个历史输入请求、140952652 个共同
输入 token，缺失计数为零。它排除了把新增输入算成旧输入重算，
但不是所有 kernel 重算或所有 Device 丢弃的完整测量。

## 等待与工具卸载

各 workflow 的完整因果路径都还原到实测 JCT。均值中的
submit-to-first-launch 从 393.950753 秒降至 326.573639 秒；
first-launch-to-native-complete 为 877.543074 秒，对照 877.326112
秒。这些区间包含交错执行和其他等待，路径也可能改变，不能当作
独立模块的因果分解或纯 GPU 时间。

386 个排队至少一秒的工具后继，采样累计 3397.474063 秒。其中：

- active：197.039982 秒；
- inactive、完整原始逻辑预算可容纳：235.784769 秒；
- inactive、完整原始逻辑预算不足：2958.153267 秒；
- 未知：6.496045 秒。

126 个后继出现过逻辑可容纳的采样区间。采样最多保持 1500ms，
请求等待会互相重叠；上述累计值不能从 JCT 中直接扣除，逻辑预算也
不是同时刻可回收的物理页。28 个 Full 恢复采样批次等待累计
3777.827ms，对照18个批次2662.835ms，不能扩张或当作同批次对照。

独立的完整卸载路径审计分清了：

- 分配压力下 JOIN 阻塞页释放：612 次节点事件，累计125.567631 GB；
- 分配压力下工具等待页释放：189 次节点事件，累计2.006188 GB；
- 额外预测式提前卸载：零事件、零字节。

这不表示工具预测不存在，也不表示没有原生 untagged 驱逐。节点释放
总量不是同时空闲空间、独占唯一字节或整个 agent 的卸载量。

末次记录中，额外预测卸载有3800次未发现合格 Host 恢复等待消费者，
只有一次 donor 检查。后台 idle prepare 共9965次检查，7251次在
Host 空闲预算门槛处、扫描前退出；末次 Host 自由683tokens，保留
5461tokens。计数揭示需要继续检查的入口和容量条件，不能直接认定
存在可安全驱逐的 Host victim，也不能仅放宽预测置信度来制造卸载。

## 后续判断

本轮补齐了实际空闲完整预算下的准入，尚未建立大的传输隐藏收益。
下一步先核对真实请求到达、资格改变和原生恢复之间，是否漏掉了可
提前处理的容量短缺窗口，并检查 Host 备份门槛是否存在有证据的安全
替换机会。不能为增加工具预取而驱逐 ready/排队 agent，也不能把
工作集切换或进一步借出/召回包装为创新。

A逐层卸载、B接管空间并开始计算仍未实现。本轮只使用共同 free-page
fence 和已有逐层加载。要推进容量交接，仍需实际 donor、所有权、
完整最终工作集及逐层依赖的证明；本轮采样不足以断言其端到端收益。

## 证据

- [完整审计及源绑定](../../experiments/reports/reference_free_continuation_20261027/complete_review.json)
- [独立卸载路径](../../experiments/reports/reference_free_continuation_20261027/complete_offload_paths.json)
- [工具等待与预测动作](../../experiments/reports/reference_free_continuation_20261027/complete_wait_offload.json)
- [完整吞吐趋势](../../experiments/figures/reference_free_continuation_20261027_r1/decode_timeline.png)
- [完整分布](../../experiments/figures/reference_free_continuation_20261027_r1/decode_distributions.png)
- [相同几何档位](../../experiments/figures/reference_free_continuation_20261027_r1/decode_geometry.png)

所有三张图均已检查。新增回调 CPU 测量及原始失败保留在同目录，
完整审计 helper 的300项测试通过。汇总器首次把合法的原始 source
依赖日志误判为其他 arm；保留 `complete_review_first_failure.json`，
修正后只对登记的原始 source graph 单独授权，原实验输入及运行时
未改变。最终汇总已执行成功。
