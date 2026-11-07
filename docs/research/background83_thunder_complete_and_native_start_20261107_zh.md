# 83 项目：五方法完整结果与排序重复验证

2026-11-07，America/Los_Angeles。保持用户最后确认的 83 项目配置，
复用已完成的非排序 Full、Oracle 和 learned R5，已完成 ThunderAgent
与 SGLang 两组基线。本文保留各阶段证据，并在末节记录完整五方法
结果及同配置重复验证接续；首轮结果不建立正式稳态或重复性结论。

## 相同工作与资源

- 83 个不同原项目的完整原始轨迹，20 个初始 workflow，平均 70 秒
  泊松到达；相同固定 18 条测量成员，完整自然尾部。
- Qwen3-Coder-30B-A3B-Instruct、BF16、FULL-only；Device 805198
  token / 79154282496 字节，Host 4069011 token / 400000057344 字节。
- serving `631911fb89b3`，native tree `434c35dd6621`；FA3、
  running/decode24、chunk2048、精确 graph1..24、mixed off。
- 相同原始 token、依赖和工具时间 overlay。共同吞吐窗口为
  55.6927713505--1638.9548502657 秒，包含所有背景输出。
- 各组固定测量成员全部结束后停止背景。NUMA 放置保留诊断，
  不要求跨节点均匀分布或页面计数相等。
- ThunderAgent 保留 invocation program、5 秒控制和 1 秒
  acting-decay 半衰期，不继承 Full 的预测卸载、JOIN/EOS 预取、
  prepare_host 或剩余工作排序。

此前被用户临时切换要求中断的 ThunderAgent 尝试仍保留为行政中断。
本次是新的完整基线；没有把旧部分计时接回，也没有重跑 Full、
Oracle、learned。

## 完整结果

四组固定测量工作一致：2991 个请求、362157 个输出 token，
输入输出身份/哈希和原生停止路径守恒，保留全部正常测量尾部。

| 方法 | 平均 JCT，秒 | P95 JCT，秒 | 平均 JCT 相对 ThunderAgent 降幅 | 共同窗口输出 token/s |
| --- | ---: | ---: | ---: | ---: |
| ThunderAgent acting-decay | 1125.102412 | 3032.987108 | 0 | 328.158558 |
| 非排序 Full | 1061.121917 | 2775.338936 | 5.686638% | 346.998142 |
| Oracle | 1036.098550 | 2720.397967 | 7.910734% | 346.064627 |
| learned R5 | 1049.209646 | 2704.624676 | 6.745410% | 347.541956 |

R5 相比 ThunderAgent，15 条测量 workflow 改善、3 条变差；平均
减少 75.892766 秒，P95 减少 328.362432 秒，共同窗口输出吞吐
提高 5.906717%。三条变差成员为 dashboard、CMS 和 desktop-media；
其中 CMS 回退 192.712370 秒，仍是后续服务顺序检查的重点。

排序自身的比较使用非排序 Full：Oracle 平均 JCT 改善 2.358199%，
R5 改善 1.122611%。整体对 ThunderAgent 的改进不能全部归因于
剩余工作预测或排序，离线 CPU 候选也没有用于这些 GPU 结果。

ThunderAgent 最后测量完成偏移为 4299.885264 秒，背景计划最后
到达为 4598.502355 秒，未覆盖测量尾部为 0 秒。实际到达 79 个，
自然结束 66 个；17 个非测量成员右截尾，其中 4 个未提交。
未用这些条件完成子集替代固定测量组的平均 JCT。

`baseline_restart_20261107_r1/thunderagent_comparison.json` 验证
完整测量工作、两份新鲜空闲快照及实际 owned-process 退出，三个
固定测量性能检查均通过。报告的 `formal_workload_accepted`、
`performance_accepted` 保留为 false：此审计不自动授予正式稳态、
统计显著性或重复性结论。paper 臂也不冒用 Full 专属的
ready-paused/周期授予诊断。

四方法图位于本 campaign 的
`baseline_restart_20261107_r1/four_method_completed_figures/`：
`complete_JCT_and_decode.png`、PDF、CSV 和来源清单均已生成，
PNG 已实际查看。JCT 使用完整固定测量组；decode 趋势与 CDF
使用相同墙钟窗口，CDF 按真实 bin 时长加权，沿用 Okabe-Ito 配色，
没有从单轮构造置信区间。

## 完整服务指标

复用 `audit_measured_request_interactivity.py`；四组均为相同
2991 个完整请求和 18/18 workflow 流式首输出覆盖。
请求 E2E normalized interactivity 为完整请求秒数/token 的 P90
取倒数，包含首次服务等待；workflow TTFT 是从原始到达到任一
descendant 的首个实际流式输出。

| 指标 | ThunderAgent | 非排序 Full | Oracle | learned R5 |
| --- | ---: | ---: | ---: | ---: |
| pooled 请求 E2E normalized interactivity，token/s | 9.833988 | 14.413483 | 14.185364 | 14.899629 |
| 每 workflow 独立计算后等权平均，token/s | 10.906149 | 15.633241 | 15.692796 | 16.157328 |
| workflow 平均 TTFT，秒 | 87.344796 | 74.609520 | 58.784412 | 73.484559 |
| workflow TTFT P90，秒 | 162.206561 | 158.398874 | 91.039642 | 169.310529 |

R5 的平均 TTFT 和请求交互性优于 ThunderAgent，TTFT P90 则更差。
完整平均 JCT 仍是主验收指标；各服务指标独立报告，不能从一种
改善推导所有尾部或所有 workflow 都改善。没有新增 50 token/s SLO。

## 实际 SGLang 回放

ThunderAgent supervisor/server/scheduler/driver/固定测量观察器
均已实际退出后，原 coordinator 自动启动相同 workload 的
`workflow_original83_host400_mean70_native_20261107_r1`，端口 19023。
没有重复资格试验或另启工作负载。

04:17 的 `baseline_restart_20261107_r1/live_native_verified.json`
独立核对：

- 实际唯一 GPU scheduler 为 392898，server 392813、driver
  393784、supervisor 392354；GPU owner 均为 nice0。
- 固定测量观察器 393795 为 nice19；coordinator 370129 存活。
- 实际模型、FA3、MoE 配置文件及 graph 与共享执行声明一致，
  Device/Host 容量精确匹配上述四组。
- `enable_beliefkv_admission=false`，实际无自定义 runtime；
  当前臂使用 native 调度和 KV 路径。
- 首批 15 个已完成请求、636 个输出 token 的源包哈希、身份、
  position sampler 和 native sampler 保留标记匹配。

现场读取器为
`ops/verify_shared_native_execution_live_20261107.py`；它按 native
合同验证实际引擎和原始回放，不要求 Full 的准入快照。证明绑定
源码、注册、独立封闭前缀和实际进程，不是完整 native JCT 或
未经修改的上游 SGLang 证明。

04:20 状态为实际回放、23 个已到达、0/18 测量成员完成，error
为空。CPU 服务指标接续 386508 已完成 ThunderAgent 审计，继续
等待 native 完整对照，随后自动汇总五方法服务指标；原 coordinator
继续生成完整 native 对照及五方法 JCT/decode 图。
SGLang 完整结果和约 1% 排序收益的重复性验证仍未完成，目标保持 active。

## R5 与 Oracle 收益差距

本节复核已有完整运行及封闭日志，不新增 GPU 实验。两者均相对同配置
非排序 Full 计算平均 JCT 改善：Oracle 为 2.358199%，R5 为
1.122611%，差 1.235588 个百分点，等价于 R5 平均 JCT 多
13.111096 秒。不能直接将这个差值全部归为预测误差。

### 选择目标仍有差异

Oracle 直接按含模型和工具的剩余关键路径秒数排序；R5 按剩余模型
总工作量的条件分布生成权重，再选择 JOIN 覆盖收益之和较大的集合。
当前实际执行使用 capacity-v7 的 `benefit` 目标，没有将收益除以
请求成本。其在线权重仍为三档概率乘以 `1, 0.5, 0.25` 后求和；
连续工作量读出器尚未用于本轮 serving。

在相同 Oracle 历史状态中，仅将含工具路径改为真实总模型工作量，
会改变 19 个集合，其中 5 个涉及测量成员。这个 CPU 对照证明目标
差异确实可能改变选择，不能据此估计其反事实 JCT。

### 主要实测损失表现为服务等待

`completed_service_analysis/complete_cohort_jct_paths.json` 复核三组
相同的 2991 请求、362157 输出和全部 18 条完整 JCT。实际最后完成
依赖链的平均差值为：

| R5 减 Oracle | 平均秒数 |
| --- | ---: |
| 请求提交至首次 native launch | +17.249654 |
| 首次 launch 至 native complete | -4.080137 |
| 其他事件及 dispatch | -0.058420 |
| 完整 JCT | +13.111096 |

launch 至 complete 包含交错和暂停，不是纯 GPU 时间。这里是实际
路径分解，不是控制开销或某项模块的因果归因。

CMS 的完整 JCT 多 182.568793 秒，首次 native launch 晚
178.716244 秒；desktop-media 分别多 73.992911 和 79.852145 秒。
其他成员有正有负，不能从这两条直接推导可消除的系统平均 JCT。

`completed_service_analysis/cms_combination_scores.json` 保留了
7 个包含 CMS 的完整搜索容量竞争边界。其中 6 次仍在等待首个
请求，CMS 的逻辑需求为 211505 token，真实剩余模型工作为
8664.99。示例边界的逻辑余量为 216267 token：

| 候选集合 | 真实剩余模型工作 | 当前收益 |
| --- | ---: | ---: |
| CMS 单成员 | 8664.99 | 0.507176 |
| 两个其他 workflow | 32352.86、28421.00 | 0.306349 + 0.346011 = 0.652359 |

CMS 单独具有规划可行性，但两个较长 workflow 的累计收益更大。
这 7 个边界替换为连续模型工作真值后，所选集合均不变。因此这个
具体问题不只来自预测误差或档内分辨率，也来自集合目标。规划可行
仍不等于立即获得原生服务；这些边界没有覆盖 CMS 的全部准入路径。

### 预测与缓存延迟的证据边界

同候选、同资源、同组合目标的完整搜索 CPU 对照为：

| 权重来源 | 124 个竞争边界的成对排序正确率 | 502 次完整搜索改变集合数 |
| --- | ---: | ---: |
| 实际缓存预测 | 83.440860% | 0 |
| 同模型的即时预测代理 | 84.865591% | 4 |
| 真实连续模型工作量 | 100% | 15 |

即时代理使用已发布观测重建输入，不等于精确 runtime 接收状态，
也没有模拟 worker 队列。连续真值 100% 由排序标签定义，不是模型
准确率。候选人口包含开发项目，不能称为项目隔离测试。
当前证据不支持为消除缓存滞后新增同步推理；连续数值和集合选择
仍值得改进，但 CPU 集合变化不是 JCT 收益。

已提交的 `workflow_shortest_frontier.py` 仅是独立 CPU 候选：
先保留一个预测最短且规划可行的 workflow，保留必选 JOIN，
同组 ready sibling 整体放得下时共同保留，再用原组合搜索补满。
同 502 个状态中，124 个竞争边界包含真实最短 workflow 的次数
从 96 增至 106，改变 16 个集合，8 个涉及测量成员；其中 7 个
减少 agent 数量，存在并行度损失风险。候选没有接入 serving，
没有 GPU 或完整 JCT 结果，后续不能将其效果混入本轮五方法比较。

证据入口为同 campaign 的
`completed_service_analysis/learned_prediction_weights.json`、
`oracle_rank_objectives.json`、`shortest_frontier_candidate_cpu.json`。
详细原始分析见 `background83_gpu_start_20261106_zh.md` 的对应章节。
当前先完成已运行的 SGLang 对照，复用全部已完成方法，不自动扩展
GPU 队列或重新运行排序三组。

## SGLang 已结束的共同吞吐窗口

04:43 左右，既有 CPU 读取器对仍在运行的同一个 native 回放封闭
55.6927713505--1638.9548502657 秒窗口。证据位于
`baseline_restart_20261107_r1/closed_common_window_r1/native_window_costs.json`。
观察到的实际原生服务已覆盖至 1673.063549 秒；输出 ordinal 连续、
逐 bin token 守恒，遥测丢失和失败记录均为 0。此时采样的 writer
队列深度为 2，不是最终空闲排空证明。

| 方法 | 同窗口输出 token/s | 同窗口 decode token/s | 输出相对 native 提升 |
| --- | ---: | ---: | ---: |
| SGLang native | 314.858169 | 312.238262 | 0 |
| ThunderAgent acting-decay | 328.158558 | 325.470437 | 4.224247% |
| 非排序 Full | 346.998142 | 344.155909 | 10.207762% |
| Oracle | 346.064627 | 343.236289 | 9.911274% |
| learned R5 | 347.541956 | 344.697828 | 10.380479% |

新绘图器 `ops/plot_background83_native_window_20261107.py` 在读取前绑定
输入文件哈希，核对五组相同、连续的 bin 边界及输出/decode token
守恒，保留完整窗口的全部背景输出。图位于同目录
`five_method_figures/closed_window_throughput.{png,pdf}`，另有 CSV
和比较清单；PNG 已实际查看。CDF 按真实 bin 时长加权，方法颜色
沿用 Okabe-Ito，没有单轮置信区间或 native 部分 JCT 估计。

native 的前段吞吐较低，后段多个区间能追上或超过 Full。因此不能
把共同窗口均值直接当作已建立的稳态优势。完整固定测量组仍需自然
结束、全部 receipt 核对、新鲜排空及实际 owned-process 退出。
04:47 同一个 scheduler 392898、driver 393784 及观察器继续运行，
47 个已到达，9/18 测量成员完成，coordinator error 为空。

## 当前目标证据与剩余工作

项目数遵循用户最新指令为 83；持续保留原目标的 400GB Host、
20 个初始成员、平均 70 秒到达、固定 18 个测量成员和完整 JCT。
以下只区分已验证事实与缺失证据，不将 CPU 候选替代原目标。

| 要求 | 当前权威证据 | 状态 |
| --- | --- | --- |
| 原始任务、token、依赖及工具时长一致 | 注册、输入哈希及完整测量 receipt 守恒 | 已完成四组，native 仅有窗口及前缀证明 |
| 存在真实 ready-paused 容量竞争 | `full_review/workload_decision.json` 所有检查通过 | 已验证 |
| Oracle 完整平均 JCT 至少约 1% 改善且吞吐不明显下降 | `oracle_comparison.json`，2.358199%，全部三项性能检查通过 | 单轮已验证 |
| 不同选择落实到实际服务 | `oracle_service.json`：88 个新增请求尝试全部精确授予、压力后保留并实际服务，82 个有边界内有效完成 | 已验证本轮周期选择 |
| 推进条件剩余工作预测调度 | `learned_comparison.json`，1.122611%；R5 共 63 个新增 episode 全部有授予及精确后续服务，54 个有边界内有效完成 | 单轮已验证，算法差异另列 |
| 同配置 ThunderAgent acting-decay | `thunderagent_comparison.json`、完整 cohort 及实际退出 | 已完成 |
| 同配置 SGLang native | 进程现场、完整共同窗口；固定 18 成员尚未结束 | 未完成 |
| 约 1% 或更高改善能够重复 | 当前每方法仅一轮，不存在第二组同配置完整结果 | 未完成 |
| NUMA 仅诊断、原有机制保留 | 已运行冻结配置及实际容量，未设置均匀分布门槛 | 已执行 |

ready-paused 的 73.195187% 是共同窗口采样占比，不是时间加权占比。
Oracle 的 88 次 episode 来自周期路径，R5 的 63 次包含 42 次周期
和 21 次免费补位，不能直接用这两个计数比较选择覆盖率。

已使用 CPU-only、nice19、CPU0 完成
`ops/prepare_background83_replication_20261107.py`，输出
`repeatability_preparation_r1/prepared_commands.json`。准备清单逐项核对
原完整 Full、Oracle、learned 的代码、配置、任务、到达表、工具、
容量和模型哈希，只替换注册路径、run-root 和端口；各组仍为
18 个测量成员、2991 请求、362157 输出。复用原冻结 serving
`631911fb89b3`、模型及已有 CPU/GPU 资格，不新增参数或训练。

这是可复查的命令准备，不是重复性实测或已排队的 GPU 接续：
`commands_executed=false`、`GPU_registered_stages=[]`。未来执行仍需
先完成当前 native 的完整核对和实际退出，再检查当前预约、资源、
端口与全部固定输入。新 CPU 短任务候选不用于这些重复命令；
原目标保持 active。

### 重复验证执行器的 CPU 实现

`ops/run_background83_frozen_replication_20261107.py` 已实现冻结
Full、Oracle、learned 的单次顺序重复执行，未启动。其
`--prepare` 只消费上述固定命令和通过的 CPU 检查，生成
`ops/workflow_original83_host400_mean70_frozen_replication_20261107_r1.plan.json`，
分析目录为 `repeatability_execution_r1/`。

`--start` 要求当前五方法 coordinator 完成、native 完整固定测量
比较及背景覆盖通过，且原 coordinator、测量观察器、supervisor、
server、driver 和登记的后代实际退出，再检查预约、资源、全部端口、
socket、注册路径及输入哈希。冻结 supervisor 在主动取消背景后
可能仍记录 driver 中断；该标签不单独决定是否完成，执行器依赖
已验证的测量完成证书、fresh drain 和实际退出证据。

每个重复臂仍由冻结的 `stop_after_measured_cohort.py` 等待相同
18 个测量成员自然完成，然后停止背景并审计。Full 必须通过原
workload 检查；Oracle 和 learned 分别与本次 Full 比较，同时保留
原轮结果。两轮都要求平均 JCT 改善至少 1%、共同窗口吞吐比至少
0.98 及背景覆盖。小收益或负收益会保存结果并停止后续接续，
不自动换种子、重跑或删掉失败结果；两轮通过也不建立统计显著性。

`ops/test_background83_frozen_replication_20261107.py` 在 CPU-only、
nice19、CPU0 下实际通过 24 项检查，0 失败、0 错误、0 跳过。
证据为 `repeatability_preparation_r1/executor_cpu_checks.json`，
绑定执行器 SHA256
`b07d990609043cd9fa10ba29c9f0d71db4bab3f181e2bf7e459343444d787e93`。
覆盖活跃或缺身份的前驱、部分窗口、测量成员/请求/输出缺口、
背景覆盖不足、主动背景取消，以及实际三组命令仅改变运行标识。
这是执行控制逻辑和命令等价的 CPU 检查，不是新 GPU 或重复性结果。

执行计划明确 `commands_executed=false`、
`automatic_start_or_watcher_registered=false`、`GPU_registered_stages=[]`；
没有修改原来的 baseline coordinator、冻结 serving、配置或 GPU 队列。
04:57 实际原 scheduler 392898、driver 393784、cohort observer 393795
及 coordinator 370129 继续存活，native 为 13/18 测量成员完成、
55 个已到达，error 为空。当前仍先完成该运行。

## 五方法完整结果与实际重复接续

05:25，native 固定 18 个测量 workflow 全部自然完成。独立读取
`cohort_termination/completion.json` 确认同样的 2991 请求、
362157 输出、无测量截断、两份新鲜空闲快照及实际 owned-process
退出。随后原 coordinator 完成 `native_comparison.json` 及
`five_method_figures/`，以 `complete_matched_baseline_comparisons`
正常退出；原 supervisor/server/scheduler/driver/cohort observer
及 coordinator 的实际进程均已消失，GPU 当时无 compute owner。

| 方法 | 完整平均 JCT，秒 | 完整 JCT P95，秒 | 同窗口输出 token/s | 同窗口 decode token/s |
| --- | ---: | ---: | ---: | ---: |
| SGLang native | 1273.285931 | 3451.122271 | 314.858169 | 312.238262 |
| ThunderAgent acting-decay | 1125.102412 | 3032.987108 | 328.158558 | 325.470437 |
| 非排序 Full | 1061.121917 | 2775.338936 | 346.998142 | 344.155909 |
| Oracle | 1036.098550 | 2720.397967 | 346.064627 | 343.236289 |
| learned R5 | 1049.209646 | 2704.624676 | 347.541956 | 344.697828 |

R5 相比 native，平均 JCT 降低 17.598269%，同窗口输出吞吐提高
10.380479%；16 条测量 workflow 改善、2 条变差。CMS 比 native
慢 196.196078 秒，desktop-media 慢 3.059202 秒。不能从平均改善
推断每个成员改善。R5 对 ThunderAgent 的改善仍为平均 JCT
6.745410%、吞吐 5.906717%；排序自身相对非排序 Full 的收益仍为
Oracle 2.358199%、R5 1.122611%，不能混用这些参照。

native 最后测量结束偏移为 4146.391129 秒，背景最后计划到达为
4598.502355 秒，未覆盖测量尾部为 0。实际到达 76 个、自然结束
60 个；23 个非测量成员右截尾，其中 7 个未提交。完整 native
比较的工作守恒、资源相同及全部三项性能检查均通过；正式 workload、
性能和统计显著性 flag 仍保留审计器的保守语义。

完整五方法 `five_method_figures/complete_JCT_and_decode.{png,pdf}`
已生成且 PNG 已实际查看。JCT 使用相同固定 18 成员的完整尾部；
decode 趋势及按时长加权的 CDF 使用共同墙钟窗口的全部背景输出。
方法颜色保持一致，没有从单轮推导置信区间。

### 完整服务指标及绘图恢复

五组服务审计均完成相同的 2991 个请求、18/18 流式 TTFT 覆盖：

| 方法 | pooled E2E NI，token/s | workflow 等权平均 E2E NI，token/s | 平均 workflow TTFT，秒 | workflow TTFT P90，秒 |
| --- | ---: | ---: | ---: | ---: |
| SGLang native | 7.451677 | 10.173902 | 47.782083 | 78.762256 |
| ThunderAgent | 9.833988 | 10.906149 | 87.344796 | 162.206561 |
| 非排序 Full | 14.413483 | 15.633241 | 74.609520 | 158.398874 |
| Oracle | 14.185364 | 15.692796 | 58.784412 | 91.039642 |
| learned R5 | 14.899629 | 16.157328 | 73.484559 | 169.310529 |

R5 的完整 JCT 和请求交互性较好，但 native 的平均及 P90 workflow
TTFT 更低。TTFT 为从到达到任意 descendant 的首次实际流式输出；
NI 包含完整请求等待，不能用其中一种指标替代其他指标。

原 CPU 服务观察器在全部指标审计完成后，绘图因 `KeyError('Oracle')`
停止。源码核对确认同名模块从旧 analysis 快照导入，其 `COLORS`
缺少 Oracle、R5 和 native。原失败状态与全部指标报告保留。
`ops/render_background83_service_metrics_recovery_20261107.py` 只复用
五份已完成报告，按精确文件路径读取当前 canonical palette，
重新验证固定测量人口、输入哈希和 TTFT 覆盖，在新目录
`service_metrics/render_recovery_r1/` 生成汇总 JSON、PNG 和 PDF。
PNG 已实际查看；未重做指标审计或运行 GPU。

### 重复验证实际启动

05:27，在完整 native 对照、背景覆盖及原进程实际退出核对通过后，
执行已准备的 `run_background83_frozen_replication_20261107.py --start`。
新 coordinator 411682 实际存活，注册了重复 Full，端口 19024：

```text
ops/workflow_original83_host400_mean70_repeat_full_20261107_r1.registration.json
ops/workflow_original83_host400_mean70_frozen_replication_20261107_r1.plan.json
repeatability_execution_r1/start.json
```

`start.json` 绑定完整 native 的实际结束证据；旧 baseline 队列与所有
结果保持不变。重复 Full 的 serving watcher 为 412107，
执行 checkout 为 `beliefkv-causal-631911fb89b3-95a962e2c884`；
仍为 serving `631911fb89b3`、相同 native、模型、工具时间、token、
83 项目、到达表、固定 18 成员和容量。这里只启动已声明的重复
Full；后续 Oracle、R5 由执行器逐组检查后接续，不再重复两组基线。
启动不是重复性结果，目标保持 active。

05:33，复用 `verify_shared_execution_live_20261101.py` 对本次
重复 Full 做只读现场核对，输出
`repeatability_execution_r1/live_full_verified.json`。实际唯一 GPU
scheduler 为 412655，server 为 412576，driver 为 413529；
cohort observer 413540 及 coordinator 411682 均存活，优先级符合
GPU owner nice0、观察器 nice19 的要求。实际 Device/Host 容量及
共享模型、引擎、graph 配置相同；运行时为非排序 Full，Oracle
未来工作与组合准入均关闭，预测工具卸载保留，工具预取与逐层
handoff 仍关闭。

首批 12 个已完成请求、502 个输出的原始 token 哈希、身份及
native position sampler 保留标记通过独立核对。这是新重复臂
实际回放及前缀证明，不是完整 JCT。05:34 状态为 21 个已到达、
0/18 测量成员完成，error 为空；只继续同一运行。

### 重复轮的实际选择与服务审计接续

05:38，独立 CPU 观察器
`ops/finish_background83_replication_service_20261107.py` 已登记并实际
运行，PID 415479、nice19、CPU0、CUDA 隐藏、单线程数值库。
固定计划及状态位于
`repeatability_execution_r1/complete_service_analysis/{plan,state}.json`，
绑定当前重复 coordinator、固定 cohort 和全部只读审计源码哈希。

观察器等待完整的 Oracle 或 learned 对照后，再调用原有审计器：

- Oracle 使用 `audit_ranked_oracle_service.py --complete`，复核实际
  周期选择与精确请求授予、压力后保留和有界后续服务。
- learned 使用 `audit_workflow_combination_choices.py --complete
  --policy-kind learned`，同时核对周期与免费补位；独立工具替换
  仍与组合选择分开，不能混用计数人口。
- 两者分别调用 `audit_cohort_jct_paths.py`，用同一原始源图和本轮
  Full 重建完整固定 18 条 JCT，区分提交至实际服务的等待、服务
  后区间和其余事件。路径分解不作为纯 GPU 时间或独占因果归因。

即使完整结果未达到性能门槛，也会审计该结果；若 GPU coordinator
因此未执行后续臂，则明确记录没有该完整比较，不另启 GPU。
这些 CPU 输出是后续证据，不是已取得的重复性收益。原 GPU 执行器、
冻结服务代码、配置及运行顺序保持不变。

05:39，实际重复 Full 的 scheduler 412655、driver 413529、
cohort observer 413540、coordinator 411682 及上述 CPU 观察器均
存活。Full 为 25 个已到达、0/18 测量成员完成；两个观察器 error
均为空。当前依然等待本轮 Full 的完整测量结果。

## 旧 73 项目 ThunderAgent 与当前结果的差异

针对用户提出的 1190.93 秒与 1125.10 秒差异，复核两次原始注册、
源图、token 包、工具 overlay、实际 server 参数、NUMA 诊断及完整
固定测量组日志。仅运行 CPU 审计；当前 GPU 重复实验保持不变。
证据保存在本 campaign 的
`baseline_restart_20261107_r1/historical73_comparison_r1/`，
包括 `comparison.json`、`old73_paths.json` 和 `new83_paths.json`。
读取器为 `scripts/audit_thunder_arrival_extension.py`。

### 相同条件与实际区别

- 前 73 个 workflow 的到达身份和时刻逐项相同，测量成员仍为原固定
  18 条。原 11796 个请求的输入输出 token、23592 条 token 包记录、
  请求工作量、依赖及 2358 条 overlay 记录一致；测量子集两轮均为
  2991 个请求、362157 个输出 token。
- 原始图直接相等检查发现三个 `workflow_start.delay_ms` 各增加
  84.971535 ms。两轮实际回放均使用固定外部到达表覆盖该字段，
  因此有效源图相同。保留初始检查失败及这三处差异，不将原始
  JSON 描述成逐字节一致。
- 实际模型、native tree `434c35dd6621`、执行配置、Device/Host
  容量、24 并发、2048 分块、mixed off、采样种子和所有策略参数相同。
  实际 server 参数差异只有端口、socket、对应命令字符串和启动
  时间观测；启动观测并非测量 JCT 的起点。
- ThunderAgent adapter、工作集策略及原生准入模块源码逐字节相同，
  仍为 invocation program、5 秒控制与 1 秒 acting-decay 半衰期。
  serving 提交从 `cd56c385` 变为 `631911fb`，Full 的控制/预测优化
  在 paper 臂关闭；共享遥测增加了 decode 样本的会话身份字段。
  不能将整个 serving 声称为同一源码版本，也没有证据将本次
  65.83 秒改善归因于该遥测变化。
- 两轮使用相同跨 NUMA 分配策略，启动诊断的远端页面比例分别为
  50.119805% 与 50.000000%。这不是早前 200 GB 实验中的
  23.60% 与 2.22% 对照，不能套用其解释。

83 项目首个新增背景在 3987.687611 秒到达。旧 ThunderAgent
最后测量结束于 4507.933780 秒，当前为 4299.885264 秒，因此新增
背景与两轮对应的测量尾部重叠。旧到达表止于 3966.635036 秒；
新表延伸到 4598.502355 秒。这一差别阻止把旧完整 JCT 直接
标作当前 83 项目持续到达负载的匹配结果。

### 改善主要早于新增背景

| 指标 | 旧 73 项目 | 当前 83 项目 |
| --- | ---: | ---: |
| 固定 18 条平均完整 JCT，秒 | 1190.928255 | 1125.102412 |
| 同窗口全部输出，token/s | 324.579870 | 328.158558 |
| 固定 18 条 JCT 相对旧轮改善数量 | 不适用 | 14 |

平均 JCT 减少 65.825843 秒，即 5.527272%。17 条成员在两轮中
都早于首个新增背景结束；它们贡献平均差值中的 54.267592 秒，
占总改善 82.441165%。只有 desktop-development 的测量尾部进入
新增背景区间。因此“新增背景使旧测量组整体变快”不能解释主要差异。
共同吞吐窗口也在新增背景前结束，吞吐已提高 1.102560%。

逐 workflow 的差异包括 fintech-trading 减少 204.128592 秒，
CMS 减少 192.813791 秒，microservice 减少 149.724427 秒；
这三条都在新增背景前结束。另有四条回退，说明不是给全部
workflow 统一减去一个计时偏移。

使用原有完整 JCT 审计器分别重建两轮实际最后完成依赖链，
按当前减旧轮的平均差值分解：

| 观测路径区间 | 当前减旧轮，秒 |
| --- | ---: |
| 请求提交至首次 native launch | -41.153684 |
| 首次 native launch 至 native complete | -26.102469 |
| 工具及其他事件、dispatch 合计 | +1.430310 |
| 完整平均 JCT | -65.825843 |

这证明主要观测差异发生在实际服务等待和服务后的执行区间。
后者包含 batch 交错、暂停和恢复，不是纯 kernel 时间。工具和
其他事件合计没有加速，源工具时长也未重采样。ThunderAgent 的
墙钟周期控制会根据当时可见的完成、请求和 KV 状态选择工作集，
因此相同 token、到达表及随机种子不保证完全相同的服务次序。
这种时序敏感性是合理机制解释，当前两轮证据仍不能将所有
差异唯一归因于控制相位、运行波动或某个共享模块。

### 历史结果的使用范围

当前 83 项目主比较继续使用已完成的 1125.102412 秒，不以较慢的
1190.928255 秒替换。旧结果继续作为 73 项目历史对照和相同到达
前缀的敏感性参考，不当作同一 83 项目配置的重复样本直接平均。
也不因本次分析另启 ThunderAgent 或更改固定测量成员。

若将当前 R5 的 1049.209646 秒除以前一轮基线，会得到
11.899844% 改善，而当前匹配基线下为 6.745410%；这一增幅混入了
两轮 ThunderAgent 本身的差异，不能全部记为设计收益。
当前继续既有同配置 Full/Oracle/R5 重复接续，以检查约 1% 的
排序增益是否重现，不由本次历史比较提前宣布重复性成立。

## 重复 Full 完整结果

06:33，固定 18 条测量 workflow 全部自然结束。最后一条
desktop-development 完成全部 369 个请求和 59093 个输出 token。
原观察器随后核对所有 2991 个测量请求、362157 个输出、原始
token 包及正常尾部，获得两份新鲜空闲遥测快照，并确认所有
登记的服务进程实际退出。完整证书保存在重复 Full 的
`cohort_termination/completion.json`，不是仅凭 supervisor 标签判定。

| 同配置非排序 Full | 首轮 | 重复轮 |
| --- | ---: | ---: |
| 固定 18 条完整平均 JCT，秒 | 1061.121917 | 1052.713786 |
| P95 完整 JCT，秒 | 2775.338936 | 2760.849832 |
| 共同窗口全部输出，token/s | 346.998142 | 345.647766 |
| 共同窗口 decode，token/s | 344.155909 | 342.821954 |

重复轮平均 JCT 下降 0.792381%，共同窗口输出下降 0.389160%，
decode 下降 0.387602%。这只是同一非排序对照的两次观测，不能当作
排序收益，也不能由两点建立统计显著性或波动上界。
早前 `full_closed_common_window.json` 的两项吞吐与最终完整
`full_review/cohort.json` 逐项一致；没有移动吞吐窗口。

最后测量完成偏移为 3694.820893 秒，背景到达计划延伸至
4598.502355 秒，测量尾部覆盖通过。实际到达 69 条，自然结束
59 条；24 条非测量成员右截尾，其中 14 条尚未提交。只以原固定
18 条完整成员统计主 JCT，不以背景完成子集替代。

`repeatability_execution_r1/full_review/workload_decision.json`
的 11 项检查全部通过，`accepted_for_oracle_diagnostic=true`。
原始审计还记录：

- 共同窗口 1493 个状态采样中，1042 个存在 ready-paused，
  即约 69.79%；这是采样占比，不是时间加权比例。
- 整轮 1815 次周期控制中，166 次存在跨 workflow 的逻辑集合
  容量竞争，不能视为 166 次已证明所有替代集合均满足原生物理容量。
- 1801 个 ready 授予 episode 全部在压力协调后保留、获得精确请求
  服务；1690 个在下一控制或对应干预前获得有效服务，1673 个在
  同样边界内完成有效 batch。这些是非排序 Full 的服务证据，
  不是 Oracle 的新增选择计数。

06:36，原 coordinator 在上述审计与实际退出通过后，自动注册
`workflow_original83_host400_mean70_repeat_oracle_20261107_r1`，
端口 19025，冻结 serving `631911fb89b3`、native `434c35dd6621`。
实际 supervisor 430549、server 431037 已启动，此时 driver
尚未开始；这不是 Oracle 已完成回放或已取得重复性收益。
后续 Oracle 和 R5 均使用本轮 1052.713786 秒的 Full 作对应对照。

为复用首轮已验证的现场读取器，新增
`scripts/verify_background83_repetition_live.py`。它只将注册、完成
证书、cohort 和 coordinator 绑定到当前重复轮，并显式适配实际
Full 现场证明中的 `actual_retained_runtime/actual_retained_agent`
字段。别名保留原值，原证明文件不变；输出继续绑定原文件、
原读取器及适配器自己的源码哈希。三个实际证据和不变性检查、
七个无效证据或策略冲突检查通过；这些是 CPU 分析检查，不是
新的 GPU 或 serving 资格。实际 Oracle/R5 现场核对尚待开始回放。

06:41，重复 Oracle 已实际进入回放；
`repeatability_execution_r1/live_oracle_verified.json` 复用原 Oracle
读取器完成独立现场核对：

- 唯一实际 GPU scheduler 431116 属于 server 431037，
  supervisor 430549、driver 432067 和固定 cohort 观察器
  432083 均实际存活，优先级分别符合 nice0/nice19 的要求。
- 实际原生执行配置、805198 Device token 和 4069011 Host
  token 与本轮完整 Full 完全一致，原始请求工作及到达表匹配。
- 实际剩余工作 Oracle 排序、决策和恢复的未来工作输入均启用，
  组合准入关闭；预测工具卸载保留，工具预取与逐层 handoff 关闭。
  工具压力卸载仍不使用未来工具时长。
- 首批 12 个完成请求、502 个输出的原始 token、请求身份、
  自校验哈希、position sampler 和原生停止路径匹配。
- 本轮 Full 的完整完成证书、负载验收及实际退出同时通过读取器
  核对；现场证明没有误绑定到首轮 Full。

这是现场前缀和实际运行证明，不是完整 Oracle JCT 或重复性收益。
06:42，21 个 workflow 已到达，0/18 测量成员完成，coordinator
及独立后续服务审计观察器均无错误，继续同一冻结回放。

## 本组结束后的 GPU 暂停要求

2026-11-07，用户要求：“这一轮重复的实验进行完成之后就先不接着
进行 GPU 实验了。”范围为当前已声明的 Full/Oracle/R5 重复组，
仍遵循原有性能门槛决定是否接续 R5。该组正常结束、未达到门槛
或退出后，均不自动追加 GPU 复验、优化候选或 CUDA 资格试验；
必须等待用户新的明确授权。CPU 分析、绘图和文档整理可以继续。

当前冻结执行器本身只包含这三组，没有组后的 GPU 接续。另登记
`scripts/hold_after_frozen_repetition.py`，请求为
`ops/workflow_original83_host400_mean70_frozen_replication_20261107_r1.plan.pause_after.json`。
CPU-only、nice19、CPU0 守卫 435582 已实际存活，绑定 coordinator
411682 的 PID/start_ticks，并逐次核对三个注册中的所有实际 owner。
它等待 coordinator 及全部服务 owner 退出后，才建立共享暂停
标记，并将现有预约记录设为禁止自动恢复、需要用户明确释放。
不改写当前执行器、冻结配置、正常尾部或提前暂停当前实验。

六个 CPU 边界场景验证了 coordinator 存活、子进程仍存活时不得
激活，以及全退出后的激活、新预约覆盖、显式取消、保留已有暂停。
测试位于 `tests/test_post_repetition_gpu_hold.py`。守卫检测到较新
预约变更或请求旁的 `.cancel.json` 后退出，避免随后重新施加旧要求。
登记后已核对当前预约文件哈希未变、暂停标记尚未建立，当前 Oracle
继续执行；“等待结束”的守卫状态不是已经暂停 GPU 的证明。

## 重复 Oracle 的选择至服务前缀

06:57，复用 `audit_ranked_oracle_service.py` 对同一重复 Oracle 做
一次 CPU-only、nice19、CPU0 封闭前缀审计，结果为
`repeatability_execution_r1/live_oracle_service_prefix.json`。读取器
核对实际 frozen policy 的源码哈希及与本轮非排序 Full 的配置差异，
再使用实际候选、JOIN 保护、类别、分组和预算重建本地参考选择。

| 整个封闭前缀，含初始和非测量成员 | 次数 |
| --- | ---: |
| 实际周期选择重放 | 483 |
| 相对同状态非排序参考的准入集合变化 | 32 |
| 新增 agent episode | 38 |
| 获得精确请求授予、压力后保留并有实际服务 | 38 |
| 在下一控制或同 agent 干预前有有效服务与有效 batch 完成 | 35 |

这 38 个 episode 对应 38 个独立请求 attempt，不能算成 38 个
workflow，也不能将 483 次周期选择都称作容量竞争。共同吞吐
窗口内的子集为 448 次选择、31 次集合变化和 37 个新增 attempt；
窗口尚未闭合，该子集不是完整共同窗口结果。

审计支持本轮存在“不同选择、精确授予、压力后保留、实际服务”的
链路，但没有重建其他准入入口或全系统反事实压力，也没有证明
参考替代集合均满足原生物理容量，不能据此推算可消除的 JCT。
`complete_measured_execution_verified=false` 保持不变。
06:59，实际 scheduler、driver、固定 cohort 观察器、coordinator、
完整服务审计接续及组后暂停守卫均存活，30 个已到达、4/18 测量
成员完成；仅继续当前冻结回放。

## 重复 Oracle 的完整共同吞吐窗口

07:08，同一 Oracle 已越过预定窗口末端。复用原有源绑定的
`window_costs.py`，以 CPU-only、nice19、CPU0 读取封闭日志，
得到 `repeatability_execution_r1/oracle_closed_common_window.json`。
本次没有新建 GPU 阶段，仍为同一个 scheduler 431116。

两轮均使用回放原点后
`[55.6927713505, 1638.9548502657]` 秒、全部 workflow（包含背景），
53 个相同边界的分箱。逐 bin 输出守恒及实际输出 ordinal 连续性
通过，failed/dropped records 均为零；累计输出除以窗口实际长度，
不是按完成成员数或无权重分箱均值计算。

| 重复轮同一完整墙钟窗口 | 非排序 Full | Oracle |
| --- | ---: | ---: |
| 全部输出 token | 547251 | 546673 |
| 全部输出 token/s | 345.647766 | 345.282697 |
| decode token/s | 342.821954 | 342.458148 |

Oracle 全部输出相对本轮 Full 为 99.894381%，下降 0.105619%；
decode 下降 0.106121%。闭合窗口满足原定吞吐比例不低于 98% 的
数值条件，尚未建立完整 JCT 改善或统计显著性。
worker 服务证据已覆盖至 1669.478835 秒，读取时 writer queue
为零；这是运行中快照，不是最终排空证明。报告继续明确
`complete_measured_execution_verified=false`、
`completed_request_receipts_reconciled=false`，最终仍须核对
2991 条测量回执、正常尾部、窗口计数一致及实际服务退出。

07:10，39 个 workflow 已到达、9/18 测量成员自然结束，
Oracle、coordinator、固定 cohort 观察器、完整服务审计接续和
组后暂停守卫均实际存活。当前仅等待同一冻结 Oracle 的剩余
测量任务；后续仍按原门槛决定 R5，组后 GPU 暂停要求保持不变。

## 重复 Oracle 的完整结果

07:44，coordinator 完成本轮 Oracle 的固定 18 成员、2991 请求和
362157 输出核对，完成证书、新鲜排空及实际 owner 退出检查通过。
两轮均有 59 个 workflow 自然结束、24 个非测量成员被右删失，
其中 14 个尚未到达；不能称为 83 个项目全部执行完成。
Oracle 最后测量成员在 3683.487090 秒完成，固定到达表延续至
4598.502355 秒，覆盖全部测量尾部。

| 相同重复组 | 非排序 Full | Oracle |
| --- | ---: | ---: |
| 固定 18 成员平均完整 JCT / s | 1052.713786 | 1036.998610 |
| P95 JCT / s | 2760.849832 | 2730.249648 |
| 相同窗口全部输出 token/s | 345.647766 | 345.282697 |

Oracle 平均 JCT 改善 1.492825%，12 个成员改善、6 个回退；
吞吐下降 0.105619%，通过原定 JCT 至少改善 1%、吞吐比例不低于
98% 的数值门槛。本轮比较必须使用本轮 Full，不能改用首轮 Full。
`oracle_repeatability.json` 核对首轮 2.358199% 与重复轮
1.492825% 均通过门槛；两次运行仍不能证明统计显著性。
完整 workload 诊断的 11 项检查均通过，正式稳态及 JOIN 形态限制
没有被改写为已通过。

执行器按已有顺序接续本组 R5，仍为冻结 serving `631911fb`，
端口 19026。07:52 的独立现场证明有 99 份精确完成回执和
105 条 R5 选择记录，唯一 GPU scheduler 为 450397，未来轨迹
没有作为线上预测输入。上一项连续预测 CPU 修改未进入该运行。
本组结束后暂停 GPU 的要求及守卫 435582 保持。

## 完整服务审计的冻结读取器修复

07:54 的实际进程核对发现旧 CPU 审计观察器 415479 已退出。
其原状态在 07:18:45 记录为
`stopped_preserve_repeated_service_audits`：后续 R5 开发修改了主目录
`beliefkv/policy/workflow_combination.py`，触发原有指纹拒绝。
当时尚未生成任何完整服务审计；GPU 实验和完成比较没有受到影响。
保留原失败状态、计划及空日志，不将其描述为仍然存活的等待器。

`63815c5e` 的干净分析 worktree 中，15 个已登记读取器和策略源
文件均与原 CPU 计划逐字节一致。新的
`scripts/recover_repetition_service_audits.py` 仅把这 15 个源路径
绑定到冻结分析目录，数据、cohort、注册和父执行器仍核对原哈希。
只允许原观察器实际退出且在服务审计开始前因源变化失败的情况，
拒绝缺失或改变的冻结源码与输入，继续使用相同完整原生服务和
JCT 路径审计。八项 CPU 边界检查通过。
后续结果写入原证据目录下独立的 `frozen_reader_r1`，不覆盖失败记录，
不放宽原生容量或工作量检查，也不创建任何 GPU 阶段。

## 重复 Oracle 的完整选择与路径证据

08:01，修复后的 CPU 观察器 454341 实际启动，nice19、CPU0、
CUDA hidden。其子进程从干净的 `63815c5e` 分析目录执行原读取器；
原 15 份源码的字节哈希全部保持。08:04 已完成 Oracle 的完整
选择与 JCT 路径核对，随后等待同组 R5，未修改 GPU 队列。

`frozen_reader_r1/oracle_choices.json` 重新验证完整测量执行，
不是早先的现场前缀：

| 观察人群 | 周期选择 | 相对本地非排序参考改变集合 | 新增 attempt | 具有精确授予、保留和实际服务 | 边界内有效完成 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 全部记录，含非测量成员及退出前记录 | 1814 | 85 | 96 | 96 | 91 |
| 固定测量观测跨度 | 1776 | 84 | 95 | 95 | 91 |
| 完整共同吞吐窗口 | 771 | 35 | 41 | 41 | 39 |

同一 attempt 的服务匹配使用真实请求、session 和 attempt；
“边界内”以下一完整控制或该 agent 的观测干预为界。
原首轮完整审计为 1796 次选择、82 次集合改变、88 个新增且
实际服务的 attempt，82 个边界内有效完成。两轮均建立了不同
选择能到达实际服务的链路。上述数字不是独立 workflow 数、
全部容量竞争次数、反事实 JCT 或某次改选的独立贡献。

`frozen_reader_r1/oracle_JCT_paths.json` 核对两侧各 2991 条请求、
362157 个输出及 18 条完整依赖路径。Oracle 减 Full 的平均
路径分量如下；两轮都单独使用同组 Full：

| 观测路径分量 / s | 首轮 | 重复轮 |
| --- | ---: | ---: |
| submit 至首次 native launch | -21.348094 | +7.556285 |
| 首次 launch 至 native complete | -4.495295 | -27.052080 |
| 其他事件和 dispatch | +0.820022 | +3.780618 |
| 完整平均 JCT | -25.023366 | -15.715177 |

两轮的路径分量结构并不相同，不能将所有 JCT 改善都归于首次
服务等待减少；launch 至 complete 含交错和暂停，不能称为纯
GPU 执行或迁移耗时。事件间隔按实际观测时钟重建，不代表固定
工具 overlay 被改写。保留这些波动，不将约 1.49% 写成稳定的
确定性收益，也不据此改变预先固定的测量成员。

相同窗口的趋势和时间加权分布图已经生成并目视检查：
`frozen_reader_r1/oracle_figures/complete_JCT_and_decode.png`，
同时保存 PDF、逐 bin CSV 与来源指纹。图中 JCT 为 18 条固定
完整 workflow，吞吐包含全部背景；53 个相同边界的分箱守恒。
图明确标注每个方法单次运行、不绘制置信区间。吞吐趋势整体
接近，与共同窗口 decode 的 342.821954 对 342.458148 token/s
一致，没有通过不同墙钟窗口扩大收益。

## 重复轮的交互指标补充

复用冻结分析目录 `63815c5e` 中的
`audit_measured_request_interactivity.py`，分别核对本轮 Full 和
Oracle 的完整 2991 个请求。两个结果写入
`frozen_reader_r1/full_interactivity.json`、
`frozen_reader_r1/oracle_interactivity.json`，TTFT 均覆盖全部 18 个
固定测量 workflow；不使用 native launch 代替真实流式首输出。

| 完整测量人口 | 非排序 Full | Oracle |
| --- | ---: | ---: |
| pooled E2E normalized interactivity / token/s | 13.559464 | 14.370419 |
| workflow 等权的 normalized interactivity / token/s | 14.933966 | 15.368912 |
| workflow 平均 TTFT / s | 68.974172 | 63.731207 |
| workflow P90 TTFT / s | 153.945700 | 91.063065 |
| 请求 seconds/output-token 的 P99 | 0.310227 | 0.375908 |

Normalized interactivity 定义为完整 HTTP 请求耗时除以输出 token 数的
P90 的倒数，不扣除 TTFT；不是 token/s 的 P90、纯 decode 速度或
50 token/s SLO。workflow 等权行先按每个 workflow 使用同一定义，
再对 18 个 workflow 等权平均。P99 仍有回退，因此不将 JCT、
TTFT 和 P90 指标的改善描述为所有尾部请求都获益。原始时序与
来源指纹保留，没有重跑 GPU。

## 重复 R5 的完整共同窗口

08:18，同一 R5 的原生服务已观察至回放原点后 1727.416365 秒。
沿用原有 `window_costs.py` 读取封闭前缀，结果为
`repeatability_execution_r1/learned_closed_common_window.json`。
固定窗口仍为 `[55.6927713505, 1638.9548502657]` 秒；
三组均使用 53 个相同边界的分箱，输出与 decode token 分别守恒。

| 相同窗口，含所有背景 workflow | 非排序 Full | Oracle | R5 |
| --- | ---: | ---: | ---: |
| 输出 token | 547251 | 546673 | 550343 |
| decode token | 542777 | 542201 | 545851 |
| 输出 token/s | 345.647766 | 345.282697 | 347.600696 |
| decode token/s | 342.821954 | 342.458148 | 344.763515 |

R5 输出吞吐相对本轮 Full 改善 0.565006%，满足吞吐比例不低于
98% 的数值条件。failed/dropped records 为零，读取时 writer queue
为零，输出 ordinal 连续性通过。它仍是闭合窗口诊断：
`complete_measured_execution_verified=false`、
`completed_request_receipts_reconciled=false`，不能据此判断整轮
JCT 或重复性门槛已通过；原窗口报告不在完成后被改写为完整执行证明。

## 完整收尾的待验收项

当前目标按用户后续确认的 83 项目执行，旧 73 项目结果保留为历史。
最终验收继续使用相同 Host400GB、20 初始、mean70、固定 18 测量成员：

| 要求 | 当前证据或待完成项 |
| --- | --- |
| 原始任务、token、依赖、工具 overlay 和到达表一致 | 冻结注册、两轮完整 Oracle/Full 及首轮 R5、ThunderAgent、native 的同工作量核对 |
| 真实 ready-paused 竞争 | 两轮负载诊断及实际周期候选；不以队列总长代替竞争 |
| Oracle 重复至少约 1% JCT 改善，吞吐无明显下降 | 首轮 2.358199%、重复轮 1.492825%；对应同组共同窗口门槛通过 |
| 改选影响准入和后续服务 | 两轮完整精确 attempt 授予、压力后保留、实际服务及有效完成审计 |
| R5 条件分布确实用于执行 | 原冻结模型的现场及首轮完整证据；重复轮运行中 |
| R5 重复完整 JCT、全部原生回执和正常尾部 | 待本轮 18 个固定成员全部自然完成，禁止仅用先完成成员计算 |
| R5 完整改选、服务、JCT 路径与交互指标 | 改选和路径由 454341 接续；交互指标在完成后读取 |
| NUMA 仅作诊断，不强制两个节点均分 | 现有400GB注册及已完成实际执行；不新增内存放置试验 |
| 保留 JOIN/EOS、工具预测卸载、prepare_host、连续性、sibling | 冻结源码及现场启用参数；工具预取和逐层 handoff 仍关闭 |
| 组后停止 GPU，需要用户明确释放 | 守卫已登记并实际存活，当前仍在等待本组结束；待核对实际激活 |

新增 `scripts/finish_ranking_repetition_metrics.py` 只执行 CPU 收尾：
等待本组 coordinator、完整服务审计观察器实际退出，以及暂停记录和
marker 均确认为当前组禁止自动恢复、需要用户明确释放后，才读取本轮
R5 的完整 interactivity。正、负完整结果都保留；如果执行器未生成三组
完整图表，则用相同 cohort 生成，否则核对来源后复用。结束前再核对
暂停状态，任何不完整或预约变更都保留待复核，不自动标记 goal 完成。
当前三组的 GPU 配置和执行器没有修改。25 项相关 CPU 边界检查通过，
包含旧 hold/服务恢复检查，不能将其当作 GPU 或新调度策略性能证明。
