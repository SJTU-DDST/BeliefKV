# KVFlow 完整同工作量对照

2026-10-28。KVFlow已完成全部18个不同项目workflow、3674个模型请求、
145565966输入token与549671输出token。原始停止条件、全部正常长尾、
两个新鲜idle遥测快照以及实际owned清理均由独立complete_review.py
核验。没有重新采集native或重跑ThunderAgent。

## 完整结果

| 系统 | 平均JCT/s | 全程输出token/s | decode worker token/s |
| --- | ---: | ---: | ---: |
| 最新FULL（ACK修复版本） | 1617.896789 | 238.405436 | 343.098284 |
| KVFlow observed graph | 1739.626663 | 237.259279 | 339.050842 |
| ThunderAgent acting decay V345 | 1697.583645 | 233.371801 | 340.158312 |

FULL相对KVFlow的平均JCT降低6.99747%，全程输出吞吐提高0.48308%；
相对ThunderAgent分别为4.69413%与2.15692%。吞吐优势仍小，不能据此
认定已经在所有baseline上取得明显提升或达到统计显著。

KVFlow的服务间隔指标更好，必须保留：workflow首次输出均值110.024s，
FULL132.160s；各workflow最大ready输出间隔的均值52.058s，
FULL83.808s，ThunderAgent73.449s。它们与完整JCT是不同的评价维度，
这里不将更快结束解释为所有交互指标更优。

KVFlow平均decode batch6.077、worker时间加权宽度8.720；FULL为
5.876和8.369。较宽batch没有自动转化为更高的完整worker输出率。
这些worker区间包含运行时与依赖等待，不是纯kernel执行时间。

## 对比边界

所有臂使用完整V230请求、token、依赖、V311固定工具时间、KV容量、
running/decode24和seed21，无质量排除。V311是基于公开TraceLab的
可观察类别合成工具时间，不是自然工具执行时长。源轨迹有72次JOIN，
其中20次双child、52次singleton，没有嵌套；18个root均使用过已声明
的最终返回委派提醒。

KVFlow是仅使用已观察runtime图和已完成同context输入前缀的移植臂，
不宣称拥有论文应用预先声明的完整图。启动前实际重跑了当前session
prefix GPU资格；完整运行核验3674个原生radix-session注册。

KVFlow与ThunderAgent使用common11b84f7c3198；FULL使用已审核的
demand-pipeline729aa0caa7a1。因此这里是完整系统对比，不能作为单模块
消融。FULL中最新ACK重试分支未激活，不能把差值归因于该修复。
FULL有原配置R1/R2重复，但KVFlow及复用V345各只有一次完整运行。
TokenCake与Agentix正式结果仍待完成，整体目标保持active。

## 工件

- [完整审计](../../experiments/reports/kvflow_radix_session_20261028/complete_review.json)
- [审计入口](../../experiments/reports/kvflow_radix_session_20261028/complete_review.py)
- [60秒吞吐趋势](../../experiments/figures/kvflow_locobench_v230_v311_20261028_r1/decode_timeline.png)
- [时间加权分布](../../experiments/figures/kvflow_locobench_v230_v311_20261028_r1/decode_distributions.png)

两张PNG已实际打开检查。保留所有warmup、idle、正常尾部和末尾实际
partial bin，无停止后的补零；PDF、CSV和内容绑定gzip证据与图同目录。
