# Child 首次 KV 物化背压：实现与 CPU 验证

## 2026-11-17 13:10 UTC：匹配公共窗口的活体诊断

当前 pressure 组的 watcher、server、replay 均重新通过 PID/start_ticks
存活检查。独立封存前缀已有 10/18 个测量成员自然结束，供给观测覆盖到
首条到达后 1970.128919 秒；它仍不是完整测量组的性能结果。
本节只与当前 Host200/initial20/mean55 的 **Full + R5、D3 关闭**
对照比较，不使用前述 Host400/mean60 的 Full/Oracle 数据。

两臂的实际 HBM/Host 容量一致。固定公共窗口为到达后
43.758606 至 1287.750239 秒，两臂均已完整经过；供给统计排除超过
1.5 秒的观测间隙，覆盖率分别为 97.7076% 和 97.4799%。

| 公共窗口指标 | Full + R5 | Full + R5 + D3 |
| --- | ---: | ---: |
| ready-paused 时间加权均值 | 4.909404 | 6.213644 |
| continuation ready-paused 时间加权均值 | 2.817137 | 4.247634 |
| active-ready 时间加权均值 | 18.196217 | 18.675895 |
| JOIN-paused 时间加权均值 | 13.766088 | 14.166641 |
| tool-paused 时间加权均值 | 2.295805 | 2.145416 |
| 被物化背压暂缓的首次请求时间加权均值 | 0 | 0.021414 |
| decode 平均 batch | 16.899058 | 17.241263 |
| decode batch 数 | 21230 | 18026 |
| decode 单批平均 worker 区间，毫秒 | 29.243691 | 30.373552 |

D3 公共窗口有 40 条变化后的决策记录实际暂缓 ready child，
涉及 4 个不同首次请求，其中 1 个属于固定测量组。独立 jq 对压缩供给
样本重算的暂缓总量为 25.967907 agent 秒，与时间加权均值一致；
这不是某条 workflow 的 JCT 代价。公共窗口没有观测到
“D3 仍在暂缓请求、但 active-ready 为零”的状态；该逻辑观测也不证明
GPU 没有空转，或任何待准入成员已经通过物理容量检查。

截至封存的整个前缀，共有 11 个首次请求曾在实际候选集合中被暂缓，
其中 2 个属于测量组；0 次观测回退、0 次 progress exception。
最新状态为注册/开始 193/193 个首次请求、198 次许可、5 次撤销。
许可数包含再次许可，不能当作不同 child 数；累计登记至开始等待也
混有普通排队，不能全部计为 D3 造成的阻塞。
公共窗口 D3 控制累计线程 CPU 增量的上下界为 1.638928/1.642530 秒，
封存前缀累计为 3.298391 秒。不要与包含它的同步控制计数直接相加。

这些证据表明当前 D3 在公共窗口主要只改变了少量 child 的首次物化，
没有明显减少观测到的 ready 供给；但积压增加也可能来自服务变慢。
批宽增加、批次数减少及较长上下文必须与完整吞吐、实际重算和 fixed18
完整 JCT 一起审查，不能由此宣布 D3 获益。独立活体 Host counter 也
不与已完成对照的全程总量直接比较。

CPU 只读分析耗时约 9.36 秒，未修改运行源码或四组队列。复用原供给、
原生 decode 和计时分析器，只扫描一次新的压力组 journal/audit 完整
前缀；对照直接复用已完成的 readout。证据和已目视检查的公共窗口图在
`beliefkv-ready-service-terminal-20261117/experiments/reports/terminal_service_integration_20261117/`：

- `capture_materialization_pressure_prefix.py`；
- `materialization_pressure_prefix_r1/evidence.json`；
- `materialization_pressure_prefix_r1/pressure_supply.jsonl.gz`；
- `materialization_pressure_prefix_r1/common_supply.png` 和同名 PDF。

以下更新保留为对应时点的历史记录。

2026-11-17 12:37 UTC 更新：Host200/initial20/mean55 的 **Full + R5、
D3 关闭**对照已完成全部固定 18 条自然 JCT，平均 1851.699339 秒，
2991 个测量请求、362157 个测量输出、fresh drain 和实际服务进程退出
均已重新核验。这不是 Host400/mean60 的 Oracle 对照结果。
D3 开启组现已进入实际 `running_full` 回放，后续仍为 ThunderAgent、
native；完整 D3 增量收益尚未产生。

本次接续曾因完成后分析停止，原失败队列和证据全部保留：
注册没有 Host 容量覆盖时使用 `source_capacity`，而旧需求分析器只比较
可选的 `expected_replay_capacity`，把一致的实际容量误报为不匹配。
`9efca1d2` 修复该缺省读取，并使协调器能在重新验证完整执行和 review 后
复用已完成阶段；47 项相关 CPU 用例全部通过，绑定 9325 个身份。
该提交只修改两份分析/协调脚本及对应测试，`beliefkv/` 与原 e117908e
对照源码逐文件一致，没有加入 CPU 候选的额外服务修改。

修复后的分析保留所有正常尾部和负面负载诊断：当前需求采样中，
下一次仍需使用的旧前缀 union 最大为 HBM + Host 容量的 1.134330 倍；
背景到达未覆盖整个测量尾部。这些不是接受结果或零重算证明，
按用户要求保留诊断后继续原实验，不重选测量成员或重跑对照。
Host-block 完成审计进一步确认 287 个请求的 14777860 token 丢失前缀
实际再次进入 prefill，其中测量组 34 个请求为 2478348 token，
包含 21 个 continuation 与 13 个 first-call。它们不等于可移除 JCT，
也不能在 D3 开启组完成前视为背压收益。

当前接续入口位于原 campaign 的
`capacity_review_recovery_r1/continued_campaign_r2/plan.json`。
协调器 179543/start_ticks 95661312、监督器 180073/start_ticks 95665823、
server 180292/start_ticks 95666809、回放 181242/start_ticks 95680873、
固定测量观察器 181243/start_ticks 95680915 已实际核实。
新的 CPU readout 181205 跟随同一接续 plan；这些身份仅对应本次更新，
后续仍须重新核实存活与实际服务。
原 `control_review` 的三项已完成分析按摘要复用到恢复目录，
仅重做失败的需求分析和其后汇总；`recovery.json`、
`control_execution_proof.json`、原失败状态、日志和第一次恢复启动的
参数错误均保留。没有覆盖原注册、原始工作、完整 JCT 或失败记录。

2026-11-17 PST 更新：简化 Oracle 已完成原 fixed18，平均 JCT
1276.770573 秒，对照 Full 为 1274.833468 秒，后续默认停用额外工具边界
抢占，普通预测式工具卸载保留。D3 已实际进入
`gpu_campaign_guarded_3600/plan.json` 的原四组顺序执行，CPU readout
跟随同一 plan；当前四组尚未全部完成。后续 CPU 候选又修复首次物化
在没有 R5 组合选择器时丢失 Oracle 排序的问题，273 项相关检查通过，
只纳入实验后的整合清单，不热改当前服务。以下旧 PID 和运行快照保留为
对应时点的历史证据；实时存活必须重新核对。

## 当前两组实验的对照口径

2026-11-17 11:47:45 UTC 重新核对准备配置、注册和实际 runtime 状态：
前述 Oracle 和当前 D3 使用各自的匹配对照，不能跨 Host 容量或到达表
计算设计收益。当前 D3 的底座实际开启 learned R5，不是关闭工作量
排序的 Full；此前仅写 `Full control` 的标签不够明确。

| 验证项 | 对照关系 | Host pool | 到达方式 |
| --- | --- | ---: | --- |
| 已完成简化 Oracle | 非排序 Full 与关键路径 Oracle | 400 GB | 初始 20，平均间隔 60 秒 |
| 当前 D3 | Full + R5 与 Full + R5 + D3 | 200 GB | 初始 20，平均间隔 55 秒 |

两组均为当前已扩展的 133 条原项目 workflow 和固定 18 个测量成员。
共同的原任务不能抵消资源与到达方式的差异，Oracle 的
1274.833468/1276.770573 秒不进入 D3 的 JCT 收益分母。
D3 组继续使用本组关闭物化背压的 control，随后才与 pressure 比较；
对应 ThunderAgent/native 也使用本组的 200 GB 和同一 55 秒到达表。

独立 CPU 快照逐字段比较了两份准备配置，仅发现物化策略开关及其两个
对应说明字段不同，其他字段全部相等。control 的原生状态进一步确认
`workflow_work_source=learned`、`remaining_work_ranking_enabled=true`、
`workflow_shortest_work_first_admission=true` 和组合准入开启，D3 关闭。
这是准备配置的等价性和实际 control 策略证明；pressure 尚未执行，
不能据此宣布其实际容量、服务或收益已经验证。

该快照重新验证原监督器、server 和 replay 的 PID/start_ticks 存活，
测量完成数仍为 17/18。最后一条测量 workflow 的精确完成请求已从旧快照
的 40/369 推进至 58/369，最近一次完成为 root epoch 35；
epoch 36 后继在独立状态采样中 paused，提交约 480.53 秒，仍待服务。
这表明尾部在推进，也存在较长准入等待；不是完整 JCT 结果或单一瓶颈归因。
独立 jq 对同一封存请求前缀复核完成数和最新精确请求身份。

证据位于 CPU 候选 `beliefkv-ready-service-terminal-20261117` 的
`experiments/reports/terminal_service_integration_20261117/`：
`capture_materialization_scope.py` 和 `materialization_scope_live_r1.json`。
其中保存配置/注册/artifact/原工作摘要、JSONL 完整前缀长度与摘要、
有界 runtime journal 尾部摘要及实际 owner 身份；各文件读取截止独立。
没有热改当前四组源码、资源、排序、测量成员或停止协议。

当前简称统一为 **KV 背压（D3）**。`gpu_campaign_r4/plan.json`
的关闭背压对照在 2026-11-16 22:59 PST 因回放客户端 `ReadTimeout`
停在 17/18，四组均未完成，不能作为完整 JCT 结果。
失败、原始数据和冻结 `7da87367` 保留，实际服务进程已退出；
原简化 Oracle 等待队列随前驱失败停止，没有执行 GPU 测量。
用户随后明确调整为：**先执行带 1800 秒执行兜底的简化 Oracle，
再执行修改后的 D3 对照**。少量测量尾部缺少背景覆盖继续报告，
不再作为停止后续实验的理由。

新 Oracle 恢复旧版关键路径短优先，见
[简化 Oracle](../../../.worktrees/beliefkv-simple-path-oracle-20261117/docs/design/simple_path_oracle_20261117_zh.md)。
新计划为该工作树的
`experiments/reports/simple_path_oracle_20261117/ready_service_guard_3600/gpu_queue/plan.json`，
冻结 `61a55c95`，已登记
`workflow_original133_host400_mean60_simple_path_oracle_20261117_r4`。
协调器 100459/start_ticks 93939092、监督器 100973/start_ticks 93943521、
回放 102676/start_ticks 93967486、固定测量观察器 102687/start_ticks 93967589
均实际存活；服务已进入 `running_full`，本次独立检查已有 21 个 workflow 到达，
46 个模型请求登记、26 个完成，尚无完整 fixed18 性能结果。
Oracle 使用已有匹配 Full 的
Host400/mean60 配置；D3 仍使用 Host200/mean55，不能跨配置充作同组对照。
以下带日期的排队状态和执行顺序保留为历史记录。

本轮实验结束后，按用户要求将 D3、简化 Oracle 及必要修复线性整合到
同一主开发分支，并完成工作区整理；执行时点与检查见
[GPU 后线性整合](post_gpu_linear_integration_20261117_zh.md)。

用户要求保持 subagent 原任务不变，在有效 KV 接近容量上限时，
通过系统准入延后新增 child 的状态。候选独立实现且默认关闭。
以下按日期保留最初 CPU 准备和排队记录；当前四组尚未完成，
性能结论仍需等待完整配对，尚未声明 D3 带来 JCT 或重算改善。

## 17/18 停止原因与后续修复

实际回放参数仍为 `--request-timeout-s 3600.0`。
`full/replay/execution_failure.json` 明确记录 `ReadTimeout`，
回放日志显示在流式 HTTP 响应读取期间超时，随后触发本组清理，
固定测量观察器和后继队列均保留失败。
`owned_process_cleanup.json` 的剩余进程为空，并已核对实际 PID 退出；
没有将 17 个完成者改成新的测量组，也没有续跑这个部分负载。

按用户最新要求，**客户端超时保持 3600 秒**；
通过独立的 1800 秒连续 ready 无服务兜底处理长等待：
普通排序无老化加分；每次只提升一个最早超时的精确请求，
复用逻辑超时准入并在普通原生选批中优先。
工具/JOIN 阻塞不计入连续 ready 等待，实际同 workflow GPU 服务才重置时钟。
原生页、锁、DMA、真实 JOIN 和首次物化权限保留；
允许单个超时请求临时突破逻辑工作集预算，不伪造物理空闲容量。
这是防饥饿机会，不是无条件立即执行的保证。

858 项相关 CPU 回归全部通过，无跳过；加上未变的继承证据，
当前绑定报告有 9,320 个有效身份，并非重跑整套测试。
旧“3601 秒必须无效”测试因读取上限改为 14400 秒而明确退役，
原报告保留，不把旧阈值检查冒充新源码通过。
首次准备器错误把新增 CLI 参数当作已存在参数替换，已在登记前停止；
其失败记录及此前 CPU 构造产物保留，修复为显式追加后重新准备。

其后 `r2` 在模型启动后发现实际回放 CLI 仍只接受 3600 秒，
于任何 workflow 提交前因参数校验退出。该遗漏已通过共享超时上限与
9 项真实 CLI 入参检查修复，325 项相关 CPU 检查通过。
`r3` 在启动阶段收到用户保持 3600 秒的指示，已在回放之前取消并核验实际退出；
没有对旧注册或运行中的 Python 参数作隐式修改。实际 `r4` 驱动 argv
明确为 `--request-timeout-s 3600.0`，不是 14400 秒。
最新组合绑定共 9,329 个身份，包含新执行的 CLI/HTTP 检查及未变的继承证据。

实际原生容量为 HBM 805,198 FULL token、
Host 400,000,057,344 bytes；独立运行状态确认组合选择器关闭、
`remaining_model_tool_path_then_greedy_fit` 生效、兜底为 1800 秒，
工具预取关闭。观察时原生 writer 无失败、丢记录或错误。
这些是运行配置和早期服务证据，不是平均 JCT 改善或持续利用率结果。

修改后的 D3 已完成 635 个不同身份的定向执行、组合绑定 9,319 个身份。
`guarded_3600/prepare_and_wait.py` 的等待进程
102562/start_ticks 93965391 实际存活，等待本轮 Oracle 的完整 fixed18、
新鲜排空、退出证书和实际进程退出，之后才登记新 D3 四组。
准备保持 Host200/initial20/mean55；Full 控制和 pressure 只差物化开关，
两组均启用同一 1800 秒兜底，所有回放客户端使用 3600 秒。
少量背景未覆盖的测量尾部作为诊断保留。

大 root 等待的四次“逻辑预算足够”边界也已完成局部选择审计：
一次 `strict_reference_exceeds_prefix_budget`，
一次 `native_resources_unavailable_reference_fallback`，
另外两次完成了实际组合选择但目标未进入 6/8 候选窗口。
后二者没有必须预留的 JOIN 成员，不能把四次全部归因于 JOIN 预留。
精确重放与原记录一致；这仍只是原控制状态下的局部审计，
不证明另一种选择能物理装入或改善后续 JCT。

## 运行中压力与大请求等待

2026-11-16 22:31 PST 保存关闭 D3 的 control 快照；
该时刻有 113 个 workflow 已到达、15/18 个测量成员完成。
诊断只读原日志，在 CPU0、nice19、隐藏 CUDA 环境执行。
没有修改运行源码、工具时长、任务、到达或测量停止条件。
后续实际停止器观察已推进到 16/18，以下数值仍只对应保存的原快照。

该点有 20 个 ready-paused，分属 18 个 workflow；
其中 11 个是同 context 已有模型调用的续接，9 个是首次调用。
同时有 22 个 active-ready、22 个 JOIN-paused、5 个 tool-paused。
这是逻辑状态的点采样，不是时间加权均值，也不证明这些候选都能物理装下。

Host FULL 池高水位为 2,034,506 单位、200,000,077,824 bytes，
达到本轮实际容量。原生 Host-block 遥测已累计标记 7,496,002 个 FULL
重算单位；同 session 已服务前缀缺失代理为 6,426,353 token。
两种口径不能相加，也不能把所有未缓存输入视为重算；
完整结果仍须按实际 prefill 输入区间核对旧前缀重新执行，并区分测量与背景。
快照 writer 无错误、失败或丢记录。

测量成员 `python_desktop_development_expert_021` 当时仅完成
40/369 次原生模型请求。其 root 当前 context epoch 32 的同一请求、
attempt、session generation，在保存前缀内连续出现在 938 次周期
ready-paused 观测中，覆盖 1,938.748 秒；这些周期没有记录恢复该 root。
这确认它仍进入周期候选链路，不能将此长等待直接解释为 paused 漏标。

它每次需要完整逻辑预算 248,445 token，记录的恢复预算中位为
13,057 token。938 次中有 934 次预算不足，4 次预算足够；
这 4 次另有 17--24 个 ready peer 能通过同一逻辑容量检查。
因此，大部分观测存在直接的逻辑额度缺口；4 次未选中仍须区分
短优先、候选窗口和物理限制，不能据此宣布全部等待的唯一原因或反事实收益。
本筛查不覆盖所有事件准入、实际原生分配和中间状态。
更多 ready 积压也不能直接当作更多可改变实际服务的调度竞争。

完整证据保留在 D3 候选工作树的
`experiments/reports/subagent_materialization_20261116/live_control_pressure_r1/`：
`snapshot.json`、`native_status.json`、`runtime_state.json`、
`latest_periodic_decision.json`、`waiting_capacity.json` 及压缩逐周期样本。
保留原始请求/状态、来源前缀长度与 SHA-256、独立观测截止和未来工作量
仅供离线诊断的边界；尚无开启 D3 的同窗收益，继续执行原四组及后继 Oracle。

## 注册空档桥接

2026-11-16 复核发现原四组协调器在 FastWait 返回后才执行注册、
冻结源码及 CPU preflight，500 ms 检查不能等同于已取得 CUDA 占用。
已增加独立辅助提交 `37f5a477`，保留原协调器 PID 4011027 及所有计划、
注册和服务源码。辅助进程仅在原协调器写出当前阶段的 owner.json、
确认进入注册且 GPU 仍空闲后，复用原计划已绑定的 CUDA 桥接脚本。
原监督器的实际 GPU 桥接或模型进程出现后立即释放临时 child。

辅助工作树为
`/home/longhao/experiment/.worktrees/beliefkv-materialization-readout-20261116`，
设计及证据边界见其
`docs/research/materialization_registration_gap_20261116_zh.md`。
新增 15 项、原队列 16 项及既有快速等待 15 项 CPU 检查通过。
初次在分析工作树运行原队列用例时缺少实际准备路径的失败保留，
回到原工作树后完整通过；未改变准备配置或测试断言。

辅助 PID 4031908、start_ticks 88801987 实际存活，
状态为 `waiting_original_registration_control`、`CUDA_bridge_spawned=false`。
状态和转换日志位于辅助工作树的
`experiments/reports/subagent_materialization_20261116/runtime_readout_preparation_r1/registration_bridge_wait_r1/`。
截至观察时仍仅其他用户的原进程占用 GPU，未启动新设计或修复 Oracle。
这是对注册空档的补充，不构成系统级 FIFO 或排他预约；
首次 CUDA 初始化仍需时间，实际交接尚待 GPU 空闲后验证。

## 独立运行验收补充

2026-11-16 新增独立分析提交 `62c0cba3`，位于
`/home/longhao/experiment/.worktrees/beliefkv-materialization-readout-20261116`。
不改变现有物化或后继严格 Oracle 的排队源码与配置。
完整说明为该工作树的
`docs/research/materialization_runtime_readout_20261116_zh.md`。

分析器在原完成证书、精确测量工作及实际退出的基础上，
分别统计 held 首次 child、已有模型调用的续接 ready-paused、
时间加权候选供给、实际旧前缀 prefill 区间、native decode 批宽及窗口控制 CPU。
不将不同截尾的背景总量直接比较，不把 worker 区间当作 GPU 利用率。
49 项相关 CPU 检查通过；复用已完成 Host400/60 秒 Full 的实际核对保留
18 条、2,991 请求、362,157 输出，平均 JCT 为 1274.833468 秒。
公共窗口时间加权 ready-paused 为 3.5691，其中已有续接为 1.8776；
全测量生存期对应 3.3734 / 2.4238。覆盖率分别为 97.9640% / 98.1907%，
观测缺口保持未知。这不是 Host200/55 秒新运行的供给或收益预测。
初版分析器误把 JCT 汇总分布当作逐成员值，其无效平均值已保留并明确排除；
最终 r2 从逐 workflow JCT 计算并严格核对原完成证书，原实验结果未修改。

新的 CPU 读取进程 PID 4027078、start_ticks 88690765，
实际状态为 `waiting_completed_control`。它使用隐藏 CUDA、CPU0、nice19，
等待当前 `gpu_campaign_r2` 的 control/pressure 完成及真实退出后自动分析；
不创建 GPU 服务，不阻塞后继两组 baseline 或严格 Oracle。
状态和结果位于该分析工作树的
`experiments/reports/subagent_materialization_20261116/gpu_campaign_runtime_readout_r1/`。
截至该次观察 GPU 仍被其他用户占用，没有新设计或严格 Oracle 的 GPU 结果，
后继 Oracle 至少降低 2% 平均完整 JCT 的目标保持未完成。

## 设计目的与执行顺序

本设计的核心是让 agent runtime 感知 HBM 与 Host 的有效 KV 容量压力，
约束新 subagent 首次 KV 物化，避免盲目扩展已物化工作集，
把随后仍要使用的 JOIN-paused、tool-paused 等状态挤出有效缓存后再重算。
它应在正常高压运行中产生价值；完全无工作时的防死锁出口只是异常兜底，
不是策略设计的主要场景，也不能代替吞吐与重算验收。
此前进度说明过分强调防死锁，不能据此改变本设计的目标。

按用户最新指示，先保持原队列执行，验证已经排队的修复后 Oracle，
再进行本设计的匹配 GPU 实验。
保留的原队列为
`workflow_original133_host400_mean60_ready_capacity_oracle_20261115_r1`，
使用冻结 `d6d9aa1e`、133 原项目、20 初始、400 GB Host、平均 60 秒到达、
固定 18 测量成员。2026-11-16 已完成全部测量成员的自然尾部，
并通过新鲜排空、完整请求工作及实际服务进程退出检查。
其注册、冻结源码、到达表和容量均未替换；负收益保留，详见后文。
后述 200 GB／55 秒四组配置仅在这轮 Oracle 结果验证之后排队，
不把本候选插入原队列验证之前。

## 实现入口

- 候选工作树：
  `/home/longhao/experiment/.worktrees/beliefkv-subagent-materialization-pressure-20261116`。
- 实现提交：`469be6eaefa20433b00fc54c4f09efa55e8da06a`，
  `perf: defer first child KV materialization under memory pressure`。
- 服务实现修复提交：`440a69171727f0e5f6566313c12176b67b4310c0`，
  `fix: clear stale pause marks before child materialization waits`。
- 基础：已通过 CPU 验证的严格剩余工作量优先候选 `b07de6a5`。
- 设计文档：
  [完整设计](../../../.worktrees/beliefkv-subagent-materialization-pressure-20261116/docs/design/subagent_materialization_pressure_20261116_zh.md)。
- 实现：候选中的 `beliefkv/runtime/subagent_materialization.py`。
- 配置：`BELIEFKV_SUBAGENT_MATERIALIZATION=pressure_v1`；默认 `off`。

SPAWN、JOIN、客户端请求登记和 workflow 原到达时间保持原样。
仅在新 child 首次 native prefill 前等待，延迟计入完整 JCT。
已经开始 prefill 的请求保留连续性，已有状态的续接和恢复不重新限流。
后到的真实 SPAWN/CALL 会补登记尚未开始的首请求。

容量使用真实空闲 Host、可回收 Host cache、真实空闲 HBM，
扣除已有需求预留以及初始 prefill 尚未分配的后缀。
有引用的 Host-only 状态、DMA/锁定状态、JOIN 服务保护和未归入树的 Host 占用
不提供可回收额度。共享树节点只计一次，实际回收和准入保留原生权限。
候选顺序复用 R5/Oracle 的剩余 workflow 工作量，不另加老化或请求成本密度。

限制覆盖登记、周期恢复、免费补位、sibling、工具替换候选及超时强制恢复。
没有已有可执行工作时提供单分支进展例外，允许沿实际嵌套 JOIN/CALL 推进，
避免 parent 等 child、child 等内存的循环。例外和容量缺口单独计数。

## CPU 证据

证据根位于候选工作树下：
`experiments/reports/subagent_materialization_20261116/`。

- `bound_cpu_r2/evidence.json`：实际通过 9,267 个唯一用例及 23 个 subtest，
  保留全部 9,219 个前驱身份，新增 48 个；失败、错误、跳过均为零。
- 最终完整执行 233.20 秒；21 条警告保留在 `bound_cpu_r2/pytest.log`。
- 2,783 份源码、365 项继承外部输入及本轮 5 项配置/模型/到达/测量绑定检查通过，
  前后源码、原生引擎和输入保持一致。两组输入绑定存在重叠，不能相加当唯一数量。
- `control` 和 `pressure` 两臂都通过生产环境 preflight、实际 runtime 构造和 cache attach。
  挂载构造使用容量替身，不声称执行了 GPU 或完整原生 allocator。
- 新用例另外执行真实原生 CPU 树上的拆分、备份 ACK、卸载和计量，
  并覆盖实际 native 候选过滤、后缀预留、重试、取消、窗口退避和嵌套 ALL-JOIN。
- 保留开发用例 `focused_cpu_r2.xml` 的两次失败：
  容量准备 fixture 缺少已有 FULL-only/NUMA 声明；补齐 fixture 后
  `focused_cpu_r3.xml` 的 47 个用例通过，随后新增 JOIN 用例纳入上述完整验证。
- 初版 `bound_cpu_r1` 也完整通过，但最终复核补充了迟到 SPAWN 遗留待暂停标记的断言。
  `pause_mark_before.xml` 使用原 `469be6ea` 登记方法实际复现失败；
  `440a6917` 清除未分配 KV 请求的旧标记，最终以 `bound_cpu_r2` 为准。

`census_cpu_r3.json` 的标量节点替身微基准中，空闲容量快速检查中位约 3.2--3.4 us，
1,024/8,192 节点完整普查分别约 0.396/3.078 ms。
该微基准绑定 `469be6ea`；原始 Git blob 哈希验证通过，
最终暂停标记修复没有改变容量观察类或普查函数的 AST，未为无关改动重复测量。
这不是整个控制器、真实 allocator、传输或 GPU JCT 的成本。
持续高压下完整普查采用现有 2 秒控制周期兜底，
普通标量检查为 400 ms，真实完成/ACK 提前唤醒；不在每个 decode tick 普查。
超出 8,192 节点或不可观测时显式回退原调度，不把未知占用记成零。

## 匹配实验准备

`host200_prepared_r3/{control,pressure}.json` 保留：

- 133 个原项目、20 初始 workflow、平均 60 秒到达、同一固定 18 条测量成员。
- 相同模型、HBM、运行/decode 上限、工具、输入输出、依赖和剩余工作量预测器。
- 两臂均使用 200 GB Host，沿用现有 NUMA 放置诊断，不增加平均分布要求。
- 两臂仅切换首次物化策略。不同 Host 容量的历史结果不作为匹配控制。

初始 20 个 root 的已知输入合计 1,609,748 token，按当前 FULL KV 单位约 158.245 GB。
200 GB Host 与现有 HBM 共 2,839,704 个槽位。
这只是初始输入的容量参照，不是同时驻留、无重算或吞吐预测。
依据为原始 `workload_assessment.json`，SHA256
`39df3e5dabc56ed5914f6938295c6fa155ab79aa0febdd8f04df6be3aba91fd4`，
及不变到达表 SHA256
`faed8d3737785a32641d0293ecb90eb199d5bcc3ee4480ba60c89b5e1f5f1695`。

GPU 验收需同时看完整 JCT、共同墙钟吞吐、有用前缀丢失、实际额外 prefill、
首次物化等待、JOIN 推进、进展例外及累计控制 CPU。
新 root 和已开始 agent 的后续增长仍可能施加压力，本候选不保证任意负载零重算。
只有减少重算的收益超过新增排队和控制成本，才值得启用。
本轮没有修改当前排队服务的冻结源码或 GPU 占用策略。

## 后续：联合选择 Host 容量与到达间隔

最新准备提交为候选工作树的 `ad044fa5`，
`perf: prepare evidence-based child KV pressure workloads`。
首次物化服务实现仍为前述 `440a6917`；本轮增加离线分析、到达配置和对照准备，
没有改服务运行时，也没有启动本候选的 GPU 实验。
原 `host200_prepared_r3` 的 60 秒间隔两臂保留作历史准备；
原队列 Oracle 验证后，本设计的首轮候选采用 `host200_mean55_prepared_r3`；
原 r2 原生对照的固定测量停止合同缺口和修复证据保留，见后文。

采用现有完整 133 原项目、20 初始、固定 18 测量成员，
选择 **Host 200 GB、平均 55 秒持续泊松到达** 作为第一轮压力候选，
不为各个容量单独跑 GPU。模型、HBM、运行/decode 24、prefill 2048、
原任务、输入输出、依赖、已配置工具时长、workflow 顺序和随机抽样流保持一致。
测量成员仍按同一到达序号选取，保留完整 JCT，全部测量结束后停止背景。

容量依据来自已完成 Host 400 GB、60 秒间隔的封存轨迹。
每 60 秒采样的“仍被后继复用的唯一旧前缀”峰值为 3,259,387 token，
约 320.41 GB；包含待输入的需求峰值约 347.12 GB。固定旧服务轨迹时：

| 候选 Host | 可复用旧 KV 超出 HBM＋Host 的时间比例 | 峰值需求缺口 |
|---|---:|---:|
| 160 GB | 45.56% | 81.26 GB |
| 200 GB | 18.64% | 41.26 GB |
| 224 GB | 3.11% | 17.26 GB |
| 240 GB | 1.04% | 1.26 GB |
| 256 GB | 0% | 0 GB |

200 GB 接近公共窗口旧 KV 需求的 P80，低于约 223.28 GB 的 P95 Host 需求；
可形成重复压力，同时避免首轮直接压到 160 GB 的更大缺口。
这是逻辑前缀并集与假设容量的离线对照，不是实际 Host-only 占用，
也不是新压力策略下的重算、物理分配或 JCT 预测。

55 秒使到达强度提高 9.09%。固定已测完整 JCT 的覆盖筛查中，
末次背景到达约为 6,194.72 秒，最慢测量完成约为 5,812.82 秒，
剩余约 381.90 秒；50 秒会留下约 151.76 秒的未覆盖尾部。
当前有限 133 条背景流的静态覆盖下界约为 51.42 秒，
不能直接压到 40--45 秒并宣称仍具持续尾部压力。
若新运行变慢，55 秒也可能覆盖不足，应按实际结果报告。

完整源有 20,895 个请求、2,781,537 个输出 token。
55 秒对应后续任务的名义新增输出约 371.52 token/s，
而旧运行公共窗口输出为 342.21 token/s；约 1.086 的比值表明它可能是有限过载诊断，
不能直接称作稳态服务，也不能假设推迟物化会消除长期超载。

### 已有续接供给

新增分析按精确 source request/workflow/invocation/context/epoch 区分首次请求与续接，
不依靠重试可能改变的 `steps`。一次扫描核对完整 journal 的
912,192,675 字节及既有封存 SHA-256；保留 compact 结果供后续复用。

- 完整测量生存期：2,829 次控制观测，平均 ready-paused 3.897 个，
  其中真正续接 2.821 个，首次请求 1.076 个。
- 2,463 次有续接候选；1,939 次同时存在至少两个不同 workflow 的续接。
- 公共窗口：658 次控制观测，平均续接 2.190 个，389 次为多 workflow 续接并存。
- 请求身份全部对齐，未知为零。首次请求包括 root 和 child，不能全部当成被限流 child。

这些续接已有先前模型调用，不再受 child 首次物化限制。
这支持保持当前任务组合继续试验，但它是离散控制观测，
不是时间加权状态、真实容量竞争、物理可恢复证书，或新配置下的供给保证。
前述按时间加权的全测量生存期 active-ready/ready-paused 为 16.47/3.38，
两种口径不能混用。原进展出口只防止停滞，不能保证 decode 批宽。

### 准备和验收

当前 `host200_mean55_prepared_r3` 包含 `control.json`、`pressure.json`、
`thunderagent_acting_decay.json`、`native_matched.json`。
前两组只差本设计开关；后两组明确隔离 Full 策略，使用相同到达、工具、资源和测量成员。
NUMA 继续只诊断，不新增均匀分配要求。

Full＋背压相对同配置 Full 控制必须同时满足：
实际执行的旧前缀重算减少，公共墙钟窗口 decode 与总输出吞吐不下降，
完整 18 条平均 JCT 不变差。
分别报告已物化 ready-paused、放行/held 首次请求、tool/JOIN-paused、
native decode 宽度、held 期间新增空闲、控制 CPU 和进展例外。
不能用 held child 冒充恢复积压，或用 decode worker 内部速率代替墙钟吞吐。
若减少重算却缩小执行集合、降低吞吐，仍为负结果。

初版配置和诊断相关 CPU 回归 **178 项通过**，16 条警告保留，
包含全部原物化测试、到达和 Host 几何、四组配置隔离及续接身份校验。
此前完整 `bound_cpu_r2` 服务资格保留；它不是 55 秒新协议的 GPU 资格。
最新产物均位于候选的 `experiments/reports/subagent_materialization_20261116/`：

- `workload_pressure_screen_r3/screen.json`：容量和到达联合筛查及输入绑定。
- `workload_pressure_screen_r3/figures/capacity_and_supply.{png,pdf}`：已查看的容量与状态曲线。
- `continuation_supply_r1/{supply.json,decisions.jsonl.gz}`：精确续接供给统计与 compact 观测。
- `host200_mean55_prepared_r2/preparation.json`：历史四组准备，native 合同需修复。
- `protocol_cpu_r3.xml`：原 178 项相关 CPU 回归。

## Oracle 完整结果：未达到收益目标

本轮是原队列中的容量累积及保留可放下候选修复 `d6d9aa1e`，
仍保留 30 秒 ready-wait 优先和 2 秒容量累积设置。
它不是后续 `b07de6a5` 严格剩余工作量候选，不把本轮结果归因给后者。

两侧固定测量成员均为 2,991 个请求、362,157 个输出 token，
逐请求的输入输出数量、哈希及原生结束原因全部一致。
两次新鲜空闲快照、完整测量停止证书及实际注册进程退出通过；
背景到达覆盖两侧完整测量尾部。未截去任何测量成员的长尾。

| 指标 | 非排序 Full | 本轮 Oracle | 变化 |
|---|---:|---:|---:|
| 固定 18 条平均完整 JCT | 1274.83 s | 1281.77 s | 回退 0.54% |
| 公共窗口总输出吞吐 | 342.21 token/s | 337.01 token/s | 下降 1.52% |
| 公共窗口 decode 吞吐 | 339.33 token/s | 334.15 token/s | 下降 1.53% |

8 条测量 workflow 改善、10 条变慢，累计 JCT 回退 124.81 秒。
最后完成的 `desktop_development_expert_021` 贡献约 19.18 秒，
其余 17 条合计约 105.63 秒；不能把回退全部归因于最后一条。
本轮只有一次配对运行，不声明统计显著性或可重复性。

选择器在完整记录中相对同状态的参考选集改变 331 次，
产生 284 次周期新增准入及 4 次免费补位新增准入，全部精确匹配到实际服务；
其中周期 263 次、免费补位 4 次在观察边界内完成了有用批次。
这些是局部选择和后续服务证据，不是相对另一轮 Full 的因果收益。
完整测量生存期内，Full/Oracle 的逻辑跨 workflow 集合竞争分别为 499/512 次，
涉及测量成员为 212/238 次。公共吞吐窗口内分别为 101/97 次，
涉及测量成员为 61/67 次。重复控制次数不等同于独立任务数。
新增准入路径确实影响了服务，仍未转化为净 JCT 收益。
元数据与选择部分累计线程 CPU 为 2.256 秒，
不能当作全部控制成本，也不能据此完成回退归因。

原始证据根为 `.../competitive_window_r1/ready_capacity_accumulation_r1/resumed_queue_r1/`：

- `complete_comparison_r2/comparison.json`：完整工作、排空、实际退出及性能比较。
- `complete_comparison_r2/figures/complete_JCT_and_decode.{png,pdf}`：已查看的结果图。
- `oracle_actual_choices.json`：完整局部选择及精确服务审计。
- `complete_diagnostics_r1.json`：复用 compact 结果的两窗口竞争与尾部拆分。

保留汇总脚本 r1 的类型比较失败：新验证返回 tuple，JSON 缓存读回为 list。
r2 仅将缓存外层数组恢复为 tuple，继续严格比较全部请求身份和五项字段；
未修改服务、工作、结果或重新运行 GPU。

## 原生停止合同与最新资格

`f0564f6b` 修复了原 r2 `native_matched.json` 缺少
`comparison_contract.native_replay_baseline=true` 的问题，
生成新的 `host200_mean55_prepared_r3`，旧 r2 不修改。
修复前实际失败保存在 `native_cohort_contract_before_r1.xml`；
修复后 `native_cohort_contract_after_r1.xml` 为 179 项通过，
`native_cohort_stop_related_r1.xml` 为额外 77 项停止器相关回归通过。
`host200_mean55_protocol_r1.json` 对实际四组配置执行生产环境转换、preflight、
停止器入口与共同流量检查，并确认 169 份 runtime/policy/control 源码仍与前驱相同。
这不是新 Host 池、GPU allocator 或模型的物理资格。

新增队列与协议修改沿用全部未改变的 9,267 个服务用例。
`campaign_bound_cpu_r3/evidence.json` 汇总既有实际通过报告与新增定向报告，
保留 9,267 个前驱身份，共 9,290 个唯一身份，并保留继承执行的 23 个 subtest。
这是报告组合与源码绑定，未声称重新执行整套测试。
`campaign_cpu_r3.xml` 的 16 项队列用例包括真实准备配置、
四组隔离、完整请求比较、精确监督器跳转及多次状态心跳。

## 新设计已实际排队

冻结候选为 `d4716505`；服务实现仍与 `440a6917` 相同，
新增提交仅涉及实验协议、CPU 证据绑定、排队与结果读取。
`gpu_campaign_r2/plan.json` 登记以下顺序：

1. 同代码 Full 控制，首次物化控制关闭。
2. Full 开启 `pressure_v1`，只改变此开关。
3. 同配置 ThunderAgent acting-decay。
4. 同配置 native SGLang。

四组均为 133 个不同原项目、20 初始、平均 55 秒持续泊松到达、
Host 200 GB、相同固定 18 条测量成员与公共吞吐窗口。
保留原任务、工具时长、原生采样、JOIN/EOS 和既有原生安全权限。
复用未变的精确 token 与采样 GPU 检查，启动时仍核验实际 Host/HBM 容量、
原生执行配置、完整测量工作、新鲜排空和真实退出。

等待进程 PID 4011027、start_ticks 88345847 已通过实际进程核对，
状态 `waiting_resources_control`；持久 NVML 每 500 ms 检查，
空闲确认间隔 250 ms。当前 GPU 仍由其他用户占用，未终止其进程。
等待状态文件为候选证据目录下的 `gpu_campaign_r2/state.json`，
尚未创建第一组 GPU 服务注册；后台队列已连续更新超过 100 次心跳。
该状态是本次核对时的观察，不是后续持续存活或 FIFO 独占保证。

保留 CPU 等待进程初次启动 `gpu_campaign_r1` 的失败：
误用只能新建文件的 evidence writer，第二次心跳退出。
`d4716505` 改为复用原子状态更新函数，并补充多次心跳用例；
旧状态、日志和 `CPU_waiter_failure.json` 保留。
该失败没有创建 GPU 注册、执行测量或重复消耗 GPU 实验。
另外保留早期 CPU 绑定时 JSON 路径未转为 `Path` 的错误，
修复后通过完整绑定检查。

本设计尚无 GPU 性能结果。验收仍须同时看到实际旧前缀重算减少、
公共墙钟 decode 与总输出吞吐不下降，以及完整 18 条平均 JCT 不变差。
限制新增状态时，还要核对已有续接供给、held child 等待与真实 decode 批宽，
不能以 Host 占用降低或防死锁出口触发作为收益。
