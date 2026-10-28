# 原生需求恢复流水线的完整 FULL 结果

2026-10-28。冻结应用 `01510ed528c2` / native `729aa0caa7a1` 已完成
全部工作。平均 JCT 为1613.659476秒，全程输出239.544631 token/s；
相对完整 V345 工具衰减 ThunderAgent 改善4.94374%和2.64506%。
相对准入前卸载 FULL 改善1.07499%和0.43842%。收益为正但幅度有限，
单轮仍未建立明确、可重复、超过所有 baseline 的目标。

## 完整比较

| 完整系统 | 平均 JCT/s | 全程输出 token/s | decode worker token/s |
| --- | ---: | ---: | ---: |
| 本轮需求恢复流水线 FULL | 1613.659476 | 239.544631 | 343.922082 |
| 准入前卸载 FULL | 1631.194643 | 238.499008 | 346.325953 |
| 上一轮 FULL-only 索引版 | 1651.727199 | 235.457746 | 348.574006 |
| 复用 V345 工具衰减 ThunderAgent | 1697.583645 | 233.371801 | 340.158312 |

本轮和两个主要对照均完成18个不同项目、3674请求、145565966输入和
549671输出 token，保留72 JOIN、固定 V311 工具时间、全部正常尾部、
原生停止、流式测量、Device805198/Host200GB、running/decode24及seed21。
两次新鲜 idle 快照、完整回执和实际 owned 进程退出均通过。
没有额外采集 native 或重跑 ThunderAgent。

本轮 native 为 `729aa0caa7a1`，准入前卸载与 V345 为 `11b84f7c3198`；
索引版为 `cdd58a4acbb5`。它们是明确披露引擎与组合策略差异的系统
比较，不是单模块消融。工具预取、优先窗口、普通恢复绑定、shared
profiling 和逐 decode progress probe 均关闭。正式数学仍为原来的
非确定性 FA3/flashinfer，确定性模型 fixture 的参数未进入性能实验。

本轮18个 workflow 的 JCT 均低于 V345，但 game_simulation 仅快
0.015756秒，不能把这种极小差异解释为稳定收益。相对准入前卸载版，
13个更快、5个更慢；fintech_trading +86.989850秒、ml_inference
+83.186084秒、game_simulation +76.244366秒等回退全部保留。

workflow TTFT均值134.703074秒，对照139.028403秒；每 workflow 的
最大 post-first ready-output gap 均值65.694464秒，对照73.449107秒。
这些沿用完整流式边界和 workflow 等权统计，不授予新的在线老化规则。

## 原来的24.98秒回退如何变化

首先区分两个口径。新运行的 web_blog 实际关键请求链改变，旧链107个
请求变为117个；其余17个 workflow 的链相同。当前实际关键链包含2983
个工具后继，原先为2973个。因此不能直接把两组关键链之差当作同请求
服务成本变化。

`fixed_cohort_review.py/.json` 使用原来准入前卸载版的2973个工具后继、
467561个输出 token，逐项核对本轮同一批请求：

| 固定历史工具后继，相对准入前卸载/s | 旧分组版 | 本轮 |
| --- | ---: | ---: |
| 自身 prefill 成员区间 | -0.687349 | -2.968272 |
| 自身 decode 成员区间 | +14.601518 | +9.017642 |
| 自身成员区间之外的服务间隔 | +11.067325 | -3.882696 |
| 首次服务到完成 | +24.981494 | +2.166674 |

原先的大部分服务后回退已经恢复。固定这批历史请求后，首次服务前等待
仍比准入前卸载多47.703373秒，说明不能单靠优先这些工具后继来解释
全局收益；这些请求并非当前完整 JCT 的全部实际关键链。

按本轮自己的实际关键链进行完整 JCT 分解，相对准入前卸载：
首次服务前等待 -12.007540秒，首次服务到完成 -6.152920秒，其他事件
净增加约0.625293秒，合计 JCT -17.535167秒。服务后分量内部为自身
prefill -4.340934秒、decode +6.924525秒、成员区间之外 -8.736511秒。
该分解保留改变的实际阻塞链，不筛选同链 workflow。

这些 worker 区间仍包含运行时和传输依赖，不能当作纯 kernel 或独占
控制CPU。历史增量控制CPU仍未知；恢复回退和整轮收益不能独立归因于
某一行新代码，也不能把搬到 waiting queue 的时间单独算作收益。

`batch_cost_review.py/.json`进一步保留两侧全部实际 worker：
prefill累计425.946088秒降至373.421013秒，decode累计1576.540814秒
增至1587.560174秒。按实际batch、2048-token上下文档和graph状态匹配，
每侧每档至少32批的共同档位中，decode均值16.734878ms增至16.877008ms，
约+0.84930%；对V345的相同匹配则约-0.17472%。这提示仍应核对拷贝
并行期间的decode干扰与批次组成，不能认定是普遍控制路径退化。

稀疏prefill样本本轮225个、准入前卸载235个：选批线程CPU均值
57.328213/53.472004ms，结果处理51.147408/63.483543ms。
各阶段可能嵌套，且暂存会把部分选批与恢复移动到实际prefill样本之外；
不能把样本相加、倍乘或宣称完整CPU成本下降。

## 恢复与实际服务

完整审计核对466个暂存 native demand 批次、749个成员关联：
24191769 token / 2378147659776 bytes，全部匹配精确原生 ACK，全部
关联到后续实际 prefill 与完成回执。成员计数是 episode/member关联，
不是独立请求去重后的计数，更不是预测预取次数。

完整 native 字节对账如下：

```text
2541578747904 untagged H2D bytes
= 161633304576 native request Host-hit bytes
+   1797783552 layer handoff consumer bytes
+ 2378147659776 staged native demand bytes
```

未解释余量为零；原生请求 Host-hit 语义未改。旧审计在原口径下的余量
仍保留，由新增需求恢复审计明确解释，不能把它重新标为 speculative
prefetch 或重复加回原生 Host 命中。

本轮实际 H2D/D2H 为2777.213829/1154.344157 GB，相对准入前卸载增加
62.526358/21.978907 GB。两个实际8/4 handoff恢复1.797784 GB；它们只占
全部 H2D 的小部分，不能把全部吞吐变化归因于逐层 handoff。

完整日志记录149次 JOIN 物理尝试：53次无安全 donor、81次已无缺失、
3次物理容量已足、4次发备份等 ACK、6次消费者失效、2次实际释放。
两条释放日志共135424 token。558次 Host-only extent 遍历是检查数，
不是558个可恢复机会；累计释放量也不等于同时空闲或消费者分配证明。

1087条 JOIN 预取命令验证复用228.943626 GB；64个已观察预取 JOIN
episode中，29个提前启动并复用168.798585 GB。源轨迹72 JOIN、64个
已观察 episode和57个实际 final-EOS触发属于不同分母。工具预取为零。
全部57个 EOS触发均有最终 RETURN证明，四个已知工具/EOS反例均通过。

20个并行 JOIN 的1050条 sibling日志、1071个请求尝试均绑定实际 native
服务和完成 session，包括21条多成员服务日志，对应20个独立 native
forward。同一批次可服务不同JOIN，`sibling_unique_review`另外去重，
保留旧算子的原始字段不改写。253个 grant/program
关联中250个有服务证明、2个未观察到 grant后的服务、1个身份未证明；
这些未知项不改写为成功或请求未完成。

本轮只产生3个恢复等待采样，累计26.645600ms；准入前卸载27个为
4272.784670ms，V345的18个为2662.834720ms。新路径改变了哪些请求仍
落入 worker 内恢复等待的采样范围，不能直接计算下降百分比、外推全程，
或宣布2.378TB传输全部被隐藏。

## 审计适配与证据

初次 CPU 完整审计发现两个入口问题，记录于 `initial_review_failures.json`：
三个命令误写旧对照目录，已按不变的注册表改正；新版 observer要求
旧准入前策略尚未实现的 sibling preference/subset/retry字段，因而
拒绝旧策略。原始拒绝保留，新 validator未放宽。

完整审计改为每个臂在独立CPU进程使用自身冻结 observer，并核对其
文件路径与注册 source SHA256。`observer_compatibility_review`五项检查
通过，包括三个真实完整臂、旧策略仍被新版 validator拒绝、当前 pipeline
严格设置仍被要求。早期13项 live/complete边界检查保留原source身份，
不伪称其涵盖后来修改后的观察器适配。

`experiments/reports/prefill_pipeline_20261028/` 保存完整
`complete_review`、`complete_demand_h2d_service`、`mechanism_review`、
`complete_jct_paths`、`complete_service_interleaving`、`complete_wait_prefetch`、
`complete_sampled_restore_waits`、`complete_guard_cases`、
`complete_eos_trigger_lifecycle`、`final_index`及固定cohort证据。
大JSON均按原字节gzip封存并核对，原始本地文件保留。

三幅完整趋势、分布和geometry图已实际打开核对，位于
`experiments/figures/demand_prefill_pipeline_20261028_r1/`。保留真实尾
bin、全部正常尾部和引擎差异，不在结束后补零。

本轮结果支持保留需求恢复流水线继续优化；尚未满足明确、可重复、
超过所有baseline的最终目标。其他baseline和消融继续暂缓，本次CPU
审计没有注册下一项GPU实验，整体目标保持active。
