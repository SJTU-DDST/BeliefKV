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
