# Sibling FULL 完整结果

冻结 525c7cc66e1f 的 `reference_join_siblings_20261027_r1` 已完整结束。
独立检查确认全部 18 个不同项目、3674 请求、145565966 输入 token、
549671 输出 token、原生停止、依赖、V311 工具时间、容量、种子及流式
协议与完整 V345 工具衰减 ThunderAgent 一致。全部普通长尾保留，
两个新鲜 idle 快照和真实 owned 退出均验证通过。

## 完整比较

| 指标 | Sibling FULL | V345 工具衰减 ThunderAgent |
| --- | ---: | ---: |
| 平均 workflow JCT/秒 | 1643.381962 | 1697.583645 |
| makespan/秒 | 2298.870580 | 2355.344551 |
| 全程输出 token/s | 239.104804 | 233.371801 |
| decode worker 输出 token/s | 345.889836 | 340.158312 |
| worker 区间加权 batch | 8.104224 | 8.108286 |
| 实际 H2D/十进制 GB | 2606.499496 | 2809.042010 |
| 实际 D2H/十进制 GB | 1213.624615 | 2255.862596 |
| workflow 最大 post-first ready gap 的均值/秒 | 89.947849 | 73.449107 |

平均 JCT 改善 3.19287%，全程输出吞吐改善 2.45660%。16 个 workflow
更快，desktop_media 慢 5.695610 秒，game_simulation 慢 19.749224 秒。
持续服务间隔仍变差。全程输出吞吐由固定输出量和 makespan 决定，
worker 区间含运行时/等待，不能作为纯 kernel 吞吐。

这是一次改变配置的完整比较，不是同配置独立重复，也没有达到明确、
可重复双指标优势。继续复用 V345，其他 baseline/消融保持延期。

## 实际机制

完整授权日志有 112 次新授权、120 个请求：5 次 parent free-JOIN，
107 次 sibling 授权。全部 120 个都匹配完成的原生 session/context/
clock 和后续服务；授权关联的实际 FULL 复用为 20.918992896 GB，
包含 JOIN 完成后的复制，不是提前隐藏时间。

66 个观察到的 JOIN ticket 中，55 个在完成前有正 FULL 计划，11 个
提前提交，9 个确认提前传输并复用 38.132023296 GB。全部受控 H2D
65.540063232 GB；工具预取没有实际复制。额外预测工具卸载仅释放
159 tokens，即 15630336 字节，不能把本轮整体收益归因于该路径。

23 个采样 load-consuming batch 合计等待 2976.735 ms，对照 18 个为
2662.835 ms；样本组不同，不能据此推断全量层等待变化或按间隔扩张。
462 个排队至少 1 秒的工具后继有约 92.38% 的累计等待落在 inactive
采样持有区间，仍不是精确转换日志、独占阻塞原因或可删除的 JCT。

审计发现一类真实错误：模型有时只输出工具闭合标记，旧代码却将其
认成最终回答 EOS，导致过早恢复。dashboard 的例子早于实际 JOIN
完成约 124.8 秒，完成后的原生排队约 3.02 秒。这解释了该案例的大部分
ACK 到服务间隔，不支持把这 127.6 秒全部算作调度公平性缺陷。
main 的修复已通过 CPU 共享验证，尚未在本轮冻结代码执行。

## 图表与证据

`experiments/reports/reference_join_siblings_20261027/complete_review.py/.json`
验证完整来源、工作量、停止、图表输入摘要、idle 和真实退出。
完整传输、JCT 路径、采样等待、工具再入队和授权服务另存于同目录。

原绘图等待器错误地使用缺少 matplotlib 的 serving Python，原失败状态
及日志保持不变；第一次手工重试也保留其空输出目录，不覆盖失败记录。
随后使用已有 `ops/cpu_plotting_20261027/bin/python` 完成 CPU 绘图，
没有安装/修改 serving 环境，没有重新执行 GPU。

最终图表在
`experiments/figures/reference_join_siblings_20261027_r1_cpu_retry/`，
三组 PNG/PDF 均已检查；使用相同 60 秒窗口、时间加权分布、实际尾部
宽度、完整普通长尾，证明输入压缩为 `evidence.json.gz`。
后续绘图必须显式使用这个专用绘图 Python。

本轮没有逐层容量分配或 A 卸载一层、B 接管一层的能力。
