# 需求恢复流水线 R2 完整重复结果

2026-10-28。R2已完成全部3674请求、18个不同项目workflow、
145565966输入及549671输出token，保留全部正常尾部和原始停止。
两次新鲜idle、driver退出零及实际owned清理均已独立核对。
R1/R2执行相同01510ed5应用、729aa0caa7a1引擎及配置；ACK准入重试
修复不在这两轮中。V345对照复用原完整结果，没有额外运行native或ThunderAgent。

## 完整指标

| 系统 | 平均JCT/s | 全程输出token/s | decode worker token/s |
| --- | ---: | ---: | ---: |
| FULL R1 | 1613.659476 | 239.544631 | 343.922082 |
| FULL R2 | 1625.203855 | 238.306858 | 343.713900 |
| 准入前卸载FULL | 1631.194643 | 238.499008 | 346.325953 |
| V345工具衰减ThunderAgent | 1697.583645 | 233.371801 | 340.158312 |

R1/R2相对同一V345，JCT改善分别为4.94374%/4.26370%，全程吞吐改善
2.64506%/2.11468%。两轮方向一致，但幅度仍有限，对照只有一次完整运行，
尚不能据此宣称超过所有baseline或某个模块的独立收益。

R2相对R1的JCT多11.544379秒：8个workflow更快、10个更慢，全部保留。
相对准入前卸载，JCT快5.990789秒、约0.36726%，但全程吞吐低0.08057%。
这个小的负结果保留，不将需求流水线描述为稳定优于准入前卸载。
workflow TTFT均值137.664484秒，对照139.028403秒；最大post-first
ready-output gap的workflow均值73.548356秒，对照73.449107秒，
后者也未改善。没有因此新增老化或请求优先级。

## 原始24.98秒问题

固定原来的2973个关键工具后继、467561输出token，按18个workflow
等权，首次服务到完成相对准入前卸载的增量为：

| 固定历史请求分量/s | 旧分组版 | FULL R1 | FULL R2 |
| --- | ---: | ---: | ---: |
| 自身prefill成员区间 | -0.687349 | -2.968272 | -3.483396 |
| 自身decode成员区间 | +14.601518 | +9.017642 | +12.834296 |
| 自身成员区间之外 | +11.067325 | -3.882696 | -2.241741 |
| 首次服务到完成 | +24.981494 | +2.166674 | +7.109160 |

固定历史cohort的首次服务前等待在R2仍多54.245928秒，不应忽略。
R2实际关键链与准入前卸载的18条链全部相同，完整JCT分解为：
首次服务前等待-7.479293秒，首次服务到完成+1.543954秒，其他事件
净-0.055449秒，合计-5.990789秒。这与R1中web_blog关键链改变的情形分开。

这些worker区间包含运行时和依赖等待。历史增量控制CPU仍未知，
不能将上述区间或重复差值当作独占CPU、纯kernel或可消除传输时间。

## 机制核对

465次staged native demand、720个成员关联均验证精确ACK及后续实际服务：
24169711token/2375979270144bytes。完整未标记H2D对账为：

```text
2542215856128 untagged H2D bytes
= 166236585984 native request Host-hit bytes
+             0 handoff consumer bytes
+ 2375979270144 staged native demand bytes
```

未解释余量为零，不将需求恢复重标为预测预取或增加原生Host命中计数。
R2实际H2D/D2H为2701.306135/1263.321121GB。8/4 handoff虽开启，
本轮没有真实handoff，不能把本轮收益归因于逐层交接。

63个已观察JOIN预取episode中，22个完成前启动并确认复用，约90.764476GB；
工具预取为零。54个final-EOS触发全部观察到最终RETURN，四个历史工具
反例继续通过原分类器。它们与源轨迹72个JOIN属于不同分母。

1049条sibling服务日志、1080个请求尝试均验证实际批次和完成session。
31条多成员服务日志对应30个独立native forward，不能称为30个独立JOIN。
241个grant/program关联中239个有后续服务证明，2个身份仍未证明，
未改写为成功。

## 证据与后续

完整入口、原字节归档和固定cohort核对保存于
`experiments/reports/prefill_pipeline_20261028/repeat_r2_complete/`、
`repeat_r2_paths.json.gz`及`repeat_r2_service_interleaving.json.gz`。
`repeat_result_index.py`将完整指标、机制、实际关键链和固定cohort分别封存。
R1/R2趋势、分布及geometry图位于
`experiments/figures/demand_prefill_pipeline_20261028_r2/`，三幅已实际打开核对。
两轮FULL与完整V345的时间趋势及分布图另保存在
`experiments/figures/demand_prefill_pipeline_20261028_repeat_vs_thunder/`，
两幅均已实际打开核对。通用绘图入口最初以`engine_tree`不同拒绝合图，
该拒绝被独立复现并保存；没有放宽通用matcher。专用入口只接受已审计的
729aa0caa7a1/11b84f7c3198配对，其余工作量、容量、停止、工具时间与
seed仍须完全一致，图标题明确披露不同引擎。

ACK准入修复的独立GPU/模型证明见
[修复记录](demand_restore_readmission_retry_20261028_zh.md)，不属于R2性能。
其他baseline与消融继续暂缓；完整目标保持active，尚无所有baseline上的
明确优势，也未将两次正收益自动升级为最终验收。
