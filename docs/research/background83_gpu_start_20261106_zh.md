# 83 项目持续到达 GPU 对照启动

2026-11-06，America/Los_Angeles。用户明确要求“进行GPU实验”后，解除
2026-11-04 的 GPU 暂停。旧暂停文件按原字节归档，释放记录绑定其 SHA256。
启动时只安排本轮持续压力诊断及匹配 Oracle，不启动旧的已取消重复实验。
用户随后明确的条件后续顺序见本文最后一节。

当前进度：mean60 已完成固定 18 成员，但背景未覆盖完整尾部，未启动
Oracle；mean70 的首次启动在 GPU 分配前因旧 socket 被拒绝。保留两份
结果，使用相同任务的 mean70 接续计划和独立阶段端口继续，详见文末。

## 工作与资源

| 项目 | 本轮声明 |
| --- | --- |
| 来源 | 83 个不同原项目，复用七份完整原始采集 |
| 到达 | 20 个初始 workflow，平均 60 秒泊松到达 |
| 测量 | 原固定 18 成员，保留完整 JCT，全部自然完成后停止背景 |
| 模型 | Qwen3-Coder-30B-A3B-Instruct，BF16，完整 attention |
| HBM | 805198 FULL token，79154282496 字节 |
| Host | 4069011 FULL token，400000057344 字节 |
| 原生执行 | 相同 2048-token chunk、精确 decode graph、running/decode24 |
| NUMA | 相同分配策略，实测页面比例只作诊断 |
| Full / Oracle | 相同 2 秒统一控制、400ms 工具检查，仅切换工作量排序 |
| 保留 | JOIN/EOS 预取、预测工具卸载、prepare_host、连续性和 sibling |
| 关闭 | 工具预取、逐层 handoff、组合成本分母 |

本轮是增加背景人口与持续供给的诊断，原 73 项目、20+70 秒的已完成结果
继续独立保留。CPU 来源和配置依据见
[来源绑定](background83_source_binding_20261106_zh.md)。

服务冻结代码为 `631911fb`，沿用 8188 项与 23 个子测试的完整 CPU 证据。
分析冻结为 `6db3aa39`，含最新结果读器修复。未因本次启动重跑完整 CPU
回归或独立 400GB CUDA 组件；原生引擎保持 `434c35dd6621`。

## 已通过的 GPU 检查

在端口 19016 上执行了 24 个源绑定短请求：

- 24 个请求通过原始 token、session 和原生停止边界检查。
- 原生 position sampling 与真实 GPU 进程检查通过。
- 采样成本覆盖 batch 1、8、24，保留原 sampler，并在其后执行固定 token 回放。
- 实际 Device/Host 容量与上表完全一致。

资格服务完成后，已注册非排序 Full；正式服务初始化与实际重放分别记录状态，
当前不能用注册或启动证明完整性能收益。

## 实际 Full 与接续进程

Full 已实际进入 `running_full`。独立 CPU 读器在
2026-11-06 21:15:57 America/Los_Angeles 完成检查：

- 745 个已完成请求、55527 个输出 token 与原始包和精确输出哈希一致。
- 实际 Device/Host 容量与表中预期值一致。
- GPU 唯一计算进程为注册 server 的 scheduler 272413，服务进程 nice0。
- 实际工作量排序与未来工作决策关闭，保留统一 2 秒控制和既有恢复机制。
- 固定 cohort 观察器和条件 Oracle 协调器都实际存活，前者 nice19。

证据为 `gpu_pair_20261106_r2/live_full_verified_r2.json`。
它只验证已封闭前缀和当时存活状态，不建立完整 JCT、平均吞吐或收益。

初次独立读器将 GPU worker 的 `/proc/.../environ` 缺失字段判为配置关闭，
在发布证明前失败。原读器及 `live_full_reader_failure_r1.json` 保留。
父 server 的实际初始环境包含正确开关；worker 的初始环境区域已无法提供
该字段，沿用已有原生执行观察器对这类可见性缺失的处理。新读器分别记录
父启动配置、源绑定继承与 worker 字段不可见，不将其写成直接观察到 hook
安装。没有修改或重启 GPU 实验，也未改写原始 token/遥测。

## 首个中途压力前缀

`gpu_pair_20261106_r2/pressure_prefix_r1/` 的只读 CPU 分析分别封闭了
周期服务和局部 Oracle 输入。两份 journal 前缀不同，计数不相加。

| 周期服务读器 | 已观察结果 |
| --- | ---: |
| 实际周期控制 | 405 |
| 存在 ready-paused 的控制 | 386 |
| 跨 workflow、各自可放下而不能共同放下的逻辑竞争 | 59 |
| 实际授予且后续有精确原生服务的 episode | 344 / 344 |
| 下一次控制或同 agent 干预前完成有效 batch | 301 / 344 |

后续服务按相同 request/context/attempt/session 身份核对。未能在限定窗口内
完成的 episode 不直接算作失败或可删除等待；其中 4 个窗口尚未闭合。
共同吞吐窗口内的已观察部分有 374 次控制，均有 ready-paused，
其中 58 次竞争、182 次有剩余额度但没有单体可放下。观察截止相对时间
823.535 秒，固定共同窗口为 47.737--1404.818 秒，尚不完整。
这些比例按控制点计，不是时间加权占比或物理页利用率。

另一读器重放了 407 个实际同状态选择：60 次逻辑竞争，Oracle 改变
36 个恢复集合，共同窗口已观察部分为 35 次改变。36 次中，23 次成员数
相同、12 次更少、1 次更多；30 次选择的完整逻辑预算更大。没有只有删除
而无新成员的严格子集，全部保留实际入选的 protected JOIN 且满足记录预算。
15 次差异集合涉及测量成员，覆盖 4 个测量 workflow；这不是 4 个已经受益
的 workflow，也没有反事实服务或 JCT 预测。准入成员数不等于 decode 宽度。

独立保存的原生遥测快照中，Host 高水位为 327875395584 字节，同会话
已服务前缀丢失代理为 0，FULL block 重算为 **12155 token**。该快照晚于
最初查询到 6054 token 的时刻，也不与两份读器前缀原子对齐。两项重算口径
继续分列，不称零重算，不把 Host 高水位当作有效活跃 KV 利用率。

结果支持继续当前完整配对，无需现在增加并发、再次修改到达表或换负载。
它尚不证明稳态、完整背景覆盖、Oracle 性能收益或全部准入入口的选择空间。
`oracle_choices.json`、`periodic_service.json`、`native_telemetry_observation.json`
及 `summary.json` 保留各自哈希和范围；`summarize_prefix.py` 可复现汇总。

## 完整共同窗口的中途审计

`gpu_pair_20261106_r2/common_window_complete_r1/` 已独立封闭完整
47.737--1404.818 秒共同窗口。测量成员的尾部仍在执行，以下不涉及部分
JCT，也不是 Oracle 的实际性能结果。

| 窗口内观察 | 结果 |
| --- | ---: |
| 实际周期控制 | 658 |
| 控制前存在 ready-paused 候选 | 607 / 658 |
| 跨 workflow 逻辑容量竞争 | 87 |
| 相同状态下局部 Oracle 改变恢复集合 | 52 |
| 实际周期授予且压力协调后保留 | 664 / 664 |
| 全部 workflow 的有效输出 | 461020 token |
| 其中 decode 输出 | 457131 token |
| 输出 / decode 吞吐 | 339.714235 / 336.848528 token/s |

前两份选择读器使用各自封闭前缀，但共同窗口的周期控制和竞争计数一致；
窗口外计数不相加、不替代最终完整执行审计。607/658 是按控制点计的比例，
不是时间加权的 ready-paused 占比。52 次集合改变保持原记录候选、预算、
buffer 和 protected JOIN；没有模拟改变后的服务、压力协调或 JCT。

`window_costs.py` 复用源绑定的有效 token 成本读器，逐项核对已观察输出序号，
并将 30 秒分箱之和与同一累计器核对。窗口内 prefill 执行 5276859 个
extend token，worker 区间约 564.174 秒；decode worker 区间约 792.972 秒，
有效 decode 宽度按 worker 时间加权约 15.426。它们是 worker 区间和
实际输出口径，不是纯 CUDA 时间、物理 HBM 利用率或新增输入全部需要重算
的证明。精确 decode graph 在该窗口未产生 padding row。

读器明确保留 `completed_request_receipts_reconciled=false`、
`complete_measured_execution_verified=false` 和 `telemetry_drained=false`；
封闭窗口不能代替测量组自然完成后的回执、排空和真实退出。当前证据支持
完成已登记的同负载配对，不支持中途更换到达表或宣称调度收益。

## 启动前的路径绑定失败

首次 entry 在 GPU 分配前被拒绝：原生 CPU 证据将 canonical patch 路径绑定到
旧冻结目录。新执行目录中的补丁字节、原生 tree、commit 和全部其他状态字段
逐项一致；只重新绑定路径，没有改动原生实现、放宽验证或重跑组件。

`gpu_pair_20261106_r1` 保留失败输出和原 operator；有效启动使用
`gpu_pair_20261106_r2`，含新的 `native_sources.json` 与路径比较证据。
两次 entry 的 operator 都按当时哈希保留。

## 执行入口

```text
ops/workflow_original83_host400_mean60_position_20261106_r1.registration.json
ops/workflow_original83_host400_mean60_full_20261106_r1.registration.json
ops/workflow_original83_host400_mean60_full_cohort_20261106_r1.registration.json
ops/workflow_original83_host400_mean60_matched_oracle_20261106_r1.plan.json
ops/run_background83_matched_oracle_20261106.py
```

原始 GPU 结果位于：

```text
experiments/raw/workflow_original83_host400_mean60_position_20261106_r1/
experiments/raw/workflow_original83_host400_mean60_full_20261106_r1/
```

Oracle 条件接续已在实际 Full 和固定 cohort 观察器存活后注册。它仅在测量成员
全部完成、原始回执、fresh drain、真实退出、负载供给及可改变集合的竞争
审计通过后执行。复用本轮短 GPU 检查，重新核对资源和新暂停；没有自动扩展
到参数扫描、旧重复实验或其他 baseline。

验收仍是完整测量组平均 JCT、固定共同窗口吞吐，以及选择到实际服务的传递。
单对结果不建立重复性或完成当前 goal。

## 条件后续顺序

2026-11-06，用户补充：如果当前窗口合适，而且 Oracle 短优先与预测短优先
调度均有可观提升，接下来在同一 workload 上运行 ThunderAgent 和
SGLang native 对照。这是后续实验的条件授权，不改动正在执行的冻结代码、
当前到达表或已登记的 Oracle 接续器。

执行顺序为：

1. 完成非排序 Full 及既有负载审计，确认完整测量、背景覆盖、候选竞争和
   有效状态容量等已声明条件；不能只依据共同窗口内存在候选就认定合适。
2. 完成同条件 Oracle 短优先对照，检查完整平均 JCT、共同窗口吞吐，
   以及改变选择后是否实际改变后续服务。
3. 在同一 workload 上验证预测短优先。相对同配置非排序 Full，分别报告
   Oracle 的排序收益和预测方案的实际收益；预测开销计入结果，
   不能用 Oracle 收益替代预测方案实测。
4. 两种短优先均满足收益目标后，运行 ThunderAgent reproduction，
   仅使用 acting-decay 版本，保留其原有控制和准入策略。
5. 运行 SGLang native 匹配回放，使用相同共享数据面和资源配置，
   关闭 Full 与论文调度策略。它是新的同 workload 系统对照，
   不重新采集自然轨迹，也不使用旧采集的不同到达条件来替代。

以上阶段固定同一组 83 个原项目、完整 token 与依赖、工具时长、
20 初始加平均 60 秒到达表，以及预先选定的 18 个测量成员。
同时固定模型、精度、实际 Device/Host 容量、running/decode24、
chunk2048、共同执行配置和 NUMA 放置策略。算法自身的控制周期和策略
按各自声明保留，不给 baseline 注入 Full 的 JOIN 或工具预测优先权。

所有方法都保留 18 个成员的完整 JCT，全部自然完成后停止非测量背景；
吞吐在同一到达表确定的墙钟窗口中包含背景输出，并展示 decode 吞吐的
时间趋势或分布。完整平均 JCT 使用两组均值的相对变化，另列长尾，
不以最先完成的成员、局部请求或最后一条完成时间替代。

首次写入上述顺序时，已登记的 GPU 接续只有 Oracle；预测、ThunderAgent
和 native 的运行注册尚未创建。条件满足后按上述顺序执行，沿用必要的输入、
实际资源及停止协议检查，避免为未改变的部分新增独立 GPU 小实验。

### 同负载后续配置的 CPU 准备

`gpu_pair_20261106_r2/conditional_followups_r1/prepare.py` 已执行，
产出 `learned.json`、`native.json`、`evaluation.json` 和 `review.json`。
使用同一冻结 `631911fb` 的环境生成器及真实 runtime constructor，
learned 与 ThunderAgent 配置均通过；native 的检查确认不构造 Full
runtime、不启用自定义 admission socket，并关闭 Full 迁移、工作量排序
及 prepare_host。native 的配置检查不代表 GPU 服务已经启动。

预测配置沿用此前已执行的条件树模型
`fe07eabc1c2ee72230efdb2bff190276ffc372d45dfaeae41228d97a90bfc3d6`。
其预测参数与已评价导出逐项相同，只保留旧的无成本分母组合 profile；
没有用本轮结果重新选模型或声称新的准确率提升。相对当前非排序 Full，
环境只改变已声明的模型来源、模型路径/哈希和组合选择开关，流量、
资源、统一控制与工具检查参数保持一致。旧模板中仍指向早期模型的
qualification 元数据已在派生配置中改为实际模型和对应评价证据。

当前 Oracle 使用直接全局剩余路径排序，learned 使用条件概率与 JOIN
组合选择，因此二者的差距不能全部归于预测误差。后续结果需要分别相对
相同非排序 Full 报告完整 JCT 与共同窗口吞吐。组合成本沿用旧对照标定，
明确不是本轮 83 项目的新成本标定。

三份配置的来源、到达表、工具 overlay、私有测量组和 Host 分配策略
已逐项相同；native 固定测量组停止协议也通过声明检查。CPU 检查未分配
新的 GPU/400GB 池、未重跑全量回归，也未注册后续 GPU。正式运行仍需绑定
实际派生配置，并满足前述完整负载、Oracle 和预测收益条件。

native 结果比较器另外补齐共享执行条件检查，复用 ThunderAgent 比较器的
`matching_serving_traffic`。旧比较器虽然检查流量并在最终比较时核对实际
容量，但遗漏 `native_execution_configuration`、声明容量实验及 Host
分配配置；可能接受不同执行配置或不同 Host 策略的两份声明。四项拒绝
用例在修复前失败、修复后通过。最终相关 62 项 CPU 检查全部通过，
报告为本目录 `native_comparison_before.xml` 和 `native_comparison_after.xml`。
页面数量的实测差异仍是诊断，不要求两组 NUMA 页面比例相等。

该修改仅用于未来 native 结果审计，同时将共享检查 helper 纳入结果溯源；
没有改动运行中的服务、Oracle 接续器或任何项目测试。当前运行及其
8188 项冻结证据保持原样，不能把这 62 项称为重跑整套 serving 回归。

### 预测方案的条件接续

已登记独立的 CPU 接续器：

```text
ops/run_background83_conditional_learned_20261106.py
ops/workflow_original83_host400_mean60_conditional_learned_20261106_r1.plan.json
```

进程 `289613` 在 2026-11-06 22:09 America/Los_Angeles 经实际进程检查
存活，为 nice0、CUDA hidden；状态为 `waiting_complete_promising_oracle`。
预测 GPU 注册尚未创建，ThunderAgent/native 也未排入 GPU 执行。
该进程不修改原 Oracle 接续器，后者及当前服务继续使用原冻结来源。

流程先等待 Oracle 接续器真实退出并发布完整配对，再核对比较输入、
原有 Full 负载接受检查、两臂完整测量请求和实际退出。重新从完成证书
计算 18 个完整 JCT 的均值，核对它们与比较报告一致。沿用既有约 1%
平均 JCT 改善、98% 共同窗口吞吐及背景尾部覆盖工程门槛；不会降低
既有 workload 条件，也不将工程门槛等同于统计显著性。

通过后只接续一次准备好的 learned R5，复用本轮 24 请求和采样 GPU
证据，重新检查暂停与实际资源；不重复独立 400GB 或 position 小实验。
预测臂同样在固定 18 个成员自然完成后停止背景、检查 fresh drain
和实际退出，再执行已有 learned 比较器。达标时标记 baseline 就绪，
未自动扩大到新的参数扫描或不变配置复验。

收益判断以历史已完成比较执行检查：完整正例保持通过，缺成员、
条件完成子集、非有限 JCT 和失败背景覆盖均拒绝；另核对 learned
字段重命名后判断不变。这是接续逻辑检查，不是新的 serving 回归、
预测精度或 GPU 性能证据。接续器源码 SHA256：
`39ecd3c252448592409e996c87aa9f91c6c14c4947d3225eed95b07d26627611`。

## 已观察的尾部覆盖失败

本轮全部 83 个 workflow 到达后，测量组仍为 17/18 完成。只读 CPU 证据
`gpu_pair_20261106_r2/coverage_failure_r1/coverage_prefix.json` 进一步确认：
最后一条未完成测量 workflow 自己的事件已经晚于最后计划到达至少
**335.206012 秒**。这不是仅凭缺少结束事件推断；相同 workflow 的后续
执行事件直接证明当前到达表无法覆盖其完整尾部。

原固定到达表的最后计划到达为 **3941.573447 秒**。证据保存了封闭事件
前缀、全部到达与固定测量声明哈希，明确保留 `mean_JCT=null` 和
`complete_measured_JCT_verified=false`。当前 GPU 运行继续收齐最后一条
正常尾部，再由原观察器生成完整结果和正式拒绝检查。

共同窗口内存在 87 次竞争和 52 次局部集合改变的结果仍保留，但不能用
它们抵消尾部覆盖失败。原 Oracle 接续器仍执行原 workload 门槛；
该门槛不通过就不会启动 Oracle，后续预测及 baseline 也不据此运行。
不会因为已经写好接续流程而放宽本轮的条件。

### 同任务的 70 秒候选

`coverage_failure_r1/prepare_mean70.py` 已使用既有结构化到达 API 生成
相邻目录 `background83_mean70/`：

| 项目 | mean60 当前运行 | mean70 CPU 候选 |
| --- | ---: | ---: |
| 原项目 / 初始 / 测量 / 背景 | 83 / 20 / 18 / 45 | 完全相同 |
| 平均到达间隔 | 60 秒 | 70 秒 |
| 固定共同窗口起点 | 47.736661 秒 | 55.692771 秒 |
| 固定共同窗口终点 | 1404.818443 秒 | 1638.954850 秒 |
| 最后计划到达 | 3941.573447 秒 | 4598.502355 秒 |

沿用 seed35、原 73 成员的 permutation148 与原背景身份替换，保留追加
10 个不同项目的顺序。通过先重建同种子的 73 成员前缀、再继续同一泊松
随机流生成 83 个到达，逐项核对所有时间为原值的 7/6；没有手工移动
单条测量任务、重排成员或按完成快慢重选测量组。

Full、Oracle、learned、ThunderAgent 和 native 的配置只更新到达表及
私有 cohort 引用，其余字段保留；配对声明再次通过既有检查。模型参数、
工具时长、token、依赖、Host400、执行配置和控制参数没有变化，未重复
构造相同环境、全量 CPU 回归或独立 GPU/400GB 检查。

这是降低到达强度并延长背景供给的候选，尚未注册 GPU，也未证明新的
供给、竞争或尾部覆盖会通过。当前完整 JCT 尚不可用，静态覆盖筛查保持
null；不把未完成任务的部分 JCT 当作新配置的预测性能。下一步先完成
当前完整审计，再用同条件的新 Full 确认候选，禁止跨两个到达表计算
Oracle 或预测收益。

## mean60 完整结果与阶段终止

固定 18 个测量成员已全部自然完成，最终结果替代上述 17/18 的中途状态，
但不改写任何原始文件。完成证书与 `full_review/` 独立核对通过：

| 完整测量与窗口指标 | 实测值 |
| --- | ---: |
| 测量成员 / 精确请求 / 输出 token | 18 / 2991 / 362157 |
| 平均 JCT | 1234.884343 秒 |
| JCT P50 / P95 | 848.429621 / 3257.429430 秒 |
| 最大 JCT | 4458.037792 秒 |
| 最后测量完成相对时间 | 4811.990213 秒 |
| 最后背景计划到达相对时间 | 3941.573447 秒 |
| 未覆盖尾部 | 870.416766 秒 |
| 共同窗口输出 / decode 吞吐 | 339.714235 / 336.848528 token/s |

全体 83 个已到达，81 个自然完成，2 个非测量成员按协议截尾；不称全体
83 个完成。测量组完整工作、fresh drain 和所有注册进程真实退出均通过。
原 supervisor 的 `stopped_for_investigation` 来自测量组完成后主动停止
背景回放，完成证书明确保留这一停止协议，不将其当作模型执行失败。

负载唯一失败项为 `background_arrivals_cover_measured_tails`。模型工作量
P80/P20 为 2.961973，高于已放宽的 2.931418；窗口样本中 ready-paused
占 86.488610%，最大采样 next-reuse union / 总池为 0.641684，
同上下文已服务前缀丢失代理为 0。这些是各自审计口径，不代表零重算、
时间加权供给比例或稳态已成立。完整周期审计记录 2359 次控制、360 次
跨 workflow 逻辑竞争，2437 个实际授予均保留且有精确后续服务，
2225 个在限定窗口内完成有效 batch。

原 mean60 Oracle 和 learned 接续器均已按门槛退出，未注册对应 GPU 实验。
因此没有本轮 Oracle、预测调度或 baseline 的完整收益可报告。

## mean70 校准与条件五臂接续

继续同一组 83 个项目、20 个初始 workflow、固定 18 个测量成员和相同
工具时长，只将同随机流的平均到达间隔改为 70 秒。共同窗口为
55.692771--1638.954850 秒，最后背景到达为 4598.502355 秒。

静态沿用 mean60 的完整 JCT，mean70 仍会有约 272.5 秒覆盖缺口；
它不是新的性能预测。尝试 mean70 的另一项依据是：其前 73 个到达
与历史已完成 mean70 的前缀逐项相同，历史测量结束为 3835.586319 秒，
新增背景的首条到达为 3987.687611 秒。历史服务源码与当前不同，
只能支持降低流入、延长供给的校准选择，不能证明本轮必然通过。
因此先跑当前源码的匹配 Full，不直接用历史结果计算排序收益。

首次 mean70 计划 `gpu_campaign_20261106_r1` 在正式服务启动前被拒绝：
端口 19016 对应的 `/tmp/bkv-causal-19016.sock` 属于此前已退出实验，
launcher 按既有规则保留该文件。没有启动 server、driver 或 GPU 服务，
失败注册、状态、源码、目录和 socket 均保留。

修正后的接续入口为：

```text
ops/run_background83_mean70_recovery_20261106.py
ops/workflow_original83_host400_mean70_campaign_20261106_r2.plan.json
```

它复用相同 `631911fb` 服务、`434c35dd6621` 原生树及既有 24 请求、
batch 1/8/24 资格证据，不增加独立 GPU 小实验。各阶段配置与已准备文件
相同，仅使用独立的运行目录、注册和端口，避免上个按 cohort 截尾的
实验留下 socket 时阻止下一阶段。

| 阶段 | 端口 | 接续条件 |
| --- | ---: | --- |
| 非排序 Full | 19017 | 已实际进入回放，等待固定测量组完成 |
| Oracle | 19018 | Full 完整负载验收通过 |
| learned R5 | 19019 | Oracle 完整比较通过收益门槛 |
| ThunderAgent acting-decay | 19020 | learned 完整比较也通过收益门槛 |
| SGLang native | 19021 | ThunderAgent 完整测量、审计及真实退出 |

2026-11-06 22:53 America/Los_Angeles 实际检查：协调器 301105、
Full supervisor 301503、server 301978 存活且 nice0，状态为
`starting_full`。这不等于已开始测量或已经获得性能结果；需再核对实际
KV 容量、有效策略、精确回执和 cohort 观察器。

随后独立 nice19、CUDA hidden 读器
`ops/verify_background83_mean70_live_20261106.py` 已完成实际运行核对，
证据为本轮目录的 `live_full_verified.json`：

- 实际进入 `running_full`，server 301978、driver 303146、
  唯一 GPU scheduler 302063、协调器 301105 均存活且 nice0。
- 固定测量组观察器 303158 实际存活、nice19，等待全部固定成员自然结束。
- 原生执行配置和实际 Device/Host 容量与本表声明逐项相同。
- 运行时短任务排序与未来工作决策均关闭，保留 2 秒窗口、JOIN/EOS
  恢复、active 连续性及预测工具卸载；工具预取、逐层 handoff 仍关闭。
- 已封闭的首批 3 个请求、173 个输出 token 与原始 token 包和输出哈希
  一致。这仅是启动前缀证据，不是完整 JCT、负载验收或收益。

两种短优先都分别相对同条件非排序 Full，要求完整平均 JCT 改善至少 1%、
共同窗口输出吞吐至少为对照的 98%，同时通过完整性与背景覆盖等检查。
这沿用此前工程门槛，不是统计显著性或重复性结论。任一门槛失败就保留
结果并停止该接续，不能为了启动 baseline 而降门槛。

各臂沿用相同固定成员、共同墙钟窗口、工具与 token、实际 HBM/Host、
chunk2048 和 running/decode24，测量组自然完成后停止背景。
ThunderAgent 保留原 5 秒 acting-decay 策略；native 关闭自定义调度、
Full 迁移和 prepare_host。native 最终比较使用含 `96bb556f` 修复的
审计器，逐项检查共享执行配置、声明容量及 Host 分配策略，观察到的
NUMA 页面比例仍只作诊断。五臂接续全部完成也不自动将整个研究 goal
标记完成。

## mean70 早期竞争与结果分析接续

`gpu_campaign_20261106_r2/early_pressure_r1/` 保存两份独立封闭前缀，
均由 nice19、CUDA hidden 的既有读器生成，未改变服务或 GPU 配置。
周期服务观察到相对时间 427.209574 秒，共同窗口尚未完整覆盖：

| 观察 | 早期结果 |
| --- | ---: |
| 实际周期控制 | 214 |
| 存在 ready-paused 的周期控制 | 194 |
| 共同窗口已观察部分的控制 / 存在 ready-paused | 180 / 180 |
| 跨 workflow、各自可放下而不能全部放下的逻辑竞争 | 39 |
| 实际授予 episode | 138 |
| 有精确后续原生服务的 episode | 134 |
| 下次控制或已记录干预前获得有效新服务 | 116 |
| 同一有限窗口内完成有效 batch | 115 |

单独的局部 Oracle 读器重放 215 次相同状态选择，记录 39 次逻辑竞争，
其中 33 次候选剩余工作量之比至少为 2。Oracle 会改变 25 次恢复集合：
19 次成员数不变、6 次更少；没有仅删除成员而不引入新成员的严格子集。
6 次集合差异涉及固定测量组，覆盖 2 个测量 workflow。两个读器的封闭
边界不同，不相加，也不将逻辑可放下视作已证明物理 KV 分配可行。

这证明当前前缀存在实际选择空间，支持继续已登记完整对照；它不证明
Oracle 已经实际执行、2 个 workflow 已获益、decode 宽度不变、完整
背景覆盖或平均 JCT 改善。180/180 也是按控制点的前缀比例，不是完整
窗口时间加权供给率。测量组尚未完成，当前不报告部分平均 JCT。

CPU 结果分析接续器已注册且实际存活：

```text
ops/analyze_background83_mean70_20261106.py
gpu_campaign_20261106_r2/completed_service_analysis/plan.json
```

实际观察进程为 305263，nice19、CPU0、CUDA hidden。它只等待本轮完整
结果，不修改、暂停、重启或追加 GPU 服务：

1. Full 结束后，用现有读器汇总局部 Oracle 选择空间及实际服务顺序。
2. Oracle 完整比较发布后，关联改变的集合、同边界精确授予、压力协调后
   是否保留，以及下次控制或同 agent 干预前的实际新服务。
3. learned 完整比较发布后，按原冻结成本、候选及实际截止重放组合选择，
   区分排序或集合变化与后续服务消费。
4. 每份完整比较发布后，生成固定测量组完整 JCT ECDF、共同窗口全部
   workflow 的 decode 吞吐趋势和按 bin 实际时长加权的吞吐分布。
   最后 native 比较完成后再绘制五方法同图，不提前填充缺失方法。

绘图器新增 `Oracle`、`Learned R5`、`SGLang native` 的明确方法名和稳定
Okabe-Ito / 中性灰颜色，保留所有旧标签。相关 7 项已有 CPU 检查通过，
报告为 `gpu_campaign_20261106_r2/plot_labels_cpu.xml`。只验证绘图数据
约束，不属于新 serving 回归或 GPU 性能实验。后续图均沿用同一固定
cohort、完整尾部和共同窗口，不从单次运行推导置信区间。
