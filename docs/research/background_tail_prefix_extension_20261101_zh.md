# 保留测量组的背景前缀扩展

2026-11-01。本文是已有完整原任务 trace 的 CPU 准备，
不是新的 GPU 运行或 workload 验收。当前共同引擎 Full/ThunderAgent
对比的冻结代码、original56 到达表和测量组继续保持原样。

## 已确认的缺口

原 original56 / initial12 / mean70 完整比较中：

| 方法 | 最后计划到达 | 最后测量完成 | 超出背景提交时段 |
| --- | ---: | ---: | ---: |
| Full | 3259.8586 s | 4415.8519 s | 1155.9933 s |
| ThunderAgent acting-decay | 3259.8586 s | 4753.3683 s | 1493.5097 s |

原 original69 的 ThunderAgent 最后计划到达为 4174.1363 s，
测量完成为 4283.2430 s，仍超出 109.1067 s。
这些都是旧完整报告，不能把当前仍在运行的共同引擎 Full 当成完成结果。

原始证据位于 `experiments/reports/remaining_original_projects_20261030/`：

- `measured_cohort_complete_toolaware_initial12_mean70_control_r1/cohort.json`
- `measured_cohort_complete_thunder_initial12_mean70_r1/cohort.json`
- `measured_cohort_original69_initial12_mean70_thunder_decay_r1/cohort.json`

## 候选 A：69 项追加四项

从已有 81 个完整原项目 inventory 中，保留原 69 个项目，
只从尚未使用的项目中按固有模型工作量选择最小的四个：

| 新增原项目 | 初始上下文 | 模型工作量代理 |
| --- | ---: | ---: |
| python_data_analytics_easy_010 | 35,766 | 15,894.43 |
| python_api_rest_easy_006 | 36,610 | 16,023.78 |
| python_web_ecommerce_expert_000 | 227,150 | 16,240.34 |
| javascript_desktop_productivity_easy_055 | 35,115 | 16,578.19 |

73 个不同原项目的 P80/P20 为 **2.934261**，P90/P10 为 **4.542611**。
它不达到原 3 或 4 的门槛，文件中保留这两个 false。
采用的最低值来自用户已接受的完整 original56 人口：**2.931418**。
没有重新要求 56 项达到旧门槛，也没有把门槛进一步放宽。
工作量代理仍为 `.01 * 必要新增输入 + 实际生成输出`，不是源 JCT。
工具关键路径和实际运行压力仍需后续单独核对。

原任务、原始 workflow 行、项目、自然尾部均保留。
新增项目同时包含短上下文与 227K 长上下文，未将所有新增任务都选成小 KV。
没有用方法胜负、JCT 或完成顺序挑选项目。

准备目录为
`experiments/reports/remaining_original_projects_20261030/useful_token_cost_r1/background_tail_original73_prepare_r1/`：

- `population_plan.json`：原始项目行和新增项目，明确不是可执行 source。
- `arrivals.json`：原 69 个事件的完整不变前缀，加上四个新到达。
- `analysis_cohort.json`：保留原 12 个预热、固定 18 个测量成员和相同吞吐窗口；
  背景人口及其绑定身份随扩展更新。
- `preparation.json`：输入/输出 hash、门槛来源、剩余资格工作和禁止自动 GPU 后继的声明。

## 到达契约与测量保持

`22b3f9b9 feat: preserve fixed workflow arrivals when extending background traffic`
新增显式 `prefix_extended_poisson` 契约：

- 先完整校验原 Poisson/warm/permuted 计划。
- 保留全部原 workflow ID、顺序、到达时间和其原声明。
- 按同一 seed35、平均 70 秒的原随机流继续取样，追加新项目。
- 拒绝重复身份、改写旧时间、换 rate、重排前缀和执行人口缺失。
- 新 cohort 重新绑定完整扩展到达表，但测量成员、共同窗口与完整 JCT 收尾规则不变。

新的末次计划到达为 **4376.4982 s**，比原 69 项延长 **202.3619 s**。
当前候选不会替代正在运行的 original56 比较，也没有注册新的 GPU 任务。
到达校验、旧到达形式和测量组回归共 **156 项通过**，
见 `useful_token_cost_r1/background_tail_extension_cpu_r1.xml`。

## 不能据此宣称解决了压力问题

第一个新增到达为 4291.6632 s，
比旧 69 项 ThunderAgent 的测量完成 4283.2430 s 还晚约 **8.4202 s**。
按那个旧完成时点，四个新增项目都不会实际提交。
因此，这个候选延长了已声明的开放到达过程，
**不能据此声称它增加了那次旧运行的实际 ready 工作或迁移压力。**

尚未提交的未来事件使计划覆盖字段变为 true，也不是稳态或持续计算的证明。
正式验证还必须看真实的背景提交、可执行候选、服务间隔、
缺 KV 等待、实际 HBM/Host 状态、前缀损失与完整测量 JCT。
若实际供给仍不足，需要调整后续背景分配/人口或补充自然原任务，
不能只靠在完成之后附加任务来宣布 workload 合格。

新人口的可执行因果图物化、来源注册、旧工具条目与先验保留、
完整 CPU 源码绑定及 fresh GPU 资格仍未完成。
本轮没有复制或重写原 token，也未更改运行中策略或恢复工作量排序。
当前 Full/ThunderAgent 对比仍按原固定测量组完成、实际退出和匹配引擎继续。

## 候选 B：56 项追加十七项

为增加旧 original56 测量尾部之前能够实际到达的人口，
同一准备程序还生成
`useful_token_cost_r1/background_tail_original56_plus17_prepare_r1/`。
它保留当前 56 个原项目、全部 56 个到达前缀和当前固定 18 个测量成员，
从已有 25 个增补项目中按同一固有工作量规则选取最小的 17 个完整项目。
总数同为 73，但项目集合、旧人口和测量成员继承关系与候选 A 不同。

其 P80/P20 也为 2.934261，高于已接受的 2.931418。
这是该分位数下的巧合，不表示两个人口相同。
所有原 original56 的长上下文任务仍保留；
新增项目的工具时间映射及原条目保留还未物化。

第一个新增到达为 **3413.7150 s**，末次仍为 **4376.4982 s**。
按旧 56 项完整报告的完成时点，17 个新增项目均已到达，
所以它与候选 A 的“所有新增事件晚于旧测量完成”有不同的供给含义。
但末次到达仍早于旧 Full 完成约 39.35 s、
早于旧 ThunderAgent 完成约 376.87 s，不能声称覆盖保证已经成立。
新增背景也可能改变两种方法的完成时间，旧时间不是新实验的完成预测。

两个目录均只保存 CPU 准备，没有登记 GPU 后继。
保留两个候选是为了明确到达覆盖与实际可执行人口的区别，
不根据尚未完成的新 ThunderAgent 结果选择或宣布负载获胜。
