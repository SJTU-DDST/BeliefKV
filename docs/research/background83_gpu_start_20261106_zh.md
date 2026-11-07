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

### 完整共同窗口的延迟审计

`ops/review_background83_mean70_window_20261106.py` 已登记一次纯 CPU
窗口观察，计划位于本轮 `common_window_complete_r1/plan.json`。
观察器 308314 实际为 nice19、CPU0、CUDA hidden；从已观察的原始
workflow start 与冻结到达表取得相同 monotonic 原点，在窗口结束后
再留 30 秒让正常服务日志发布，不修改测量窗口本身。

它复用原有周期服务与局部 Oracle 读器，以及 mean60 已使用的参数化
`window_costs.py`，分别保留各自封闭前缀。窗口成本读器核对实际执行
配置、容量、有效输出 ordinal 连续性，以及 30 秒分箱与同一累计器
的总量一致性；不把批次 worker 区间称作纯 CUDA 时间。它不核对尚未
完成请求的最终回执、不证明测量组完整 JCT，也不触发新 GPU 实验。

2026-11-06 23:25 America/Los_Angeles，窗口观察已实际完成，观察器及其
CPU 子进程均退出；原 Full 服务和固定测量组观察器继续运行。
`common_window_complete_r1/summary.json` 由同目录 `summarize.py`
重新核对并绑定三份读器报告，完整窗口为 55.692771--1638.954850 秒：

| 完整共同窗口指标 | 结果 |
| --- | ---: |
| 实际周期控制 | 768 |
| 控制前存在 ready-paused | 638 / 768，83.072917% |
| 周期 application-ready 逻辑竞争诊断 | 85 |
| 实际排序候选集合中的跨 workflow 逻辑竞争 | 83 |
| 上述实际候选竞争中剩余工作比至少为 2 | 73 |
| 同状态局部 Oracle 改变恢复集合 | 51 |
| 改变集合涉及测量成员的决策 / 不同测量 workflow | 23 / 9 |
| 实际周期授予且压力协调后保留 | 694 / 694 |
| 全部 workflow 输出 / decode 输出 | 549389 / 544889 token |
| 输出 / decode 吞吐 | 346.998142 / 344.155909 token/s |

638/768 按控制点统计，不是时间加权比例。51 次局部集合变化中，
38 次成员数相同、11 次更少、2 次更多；没有仅删除成员的严格子集。
这里的 9 个测量 workflow 是涉及不同候选选择的成员，不是已经获益
的成员，也不代表反事实服务或 JCT 已经重建。

两种竞争计数的差异已经按相同 monotonic 控制时间核对：完整窗口
恰好相同的 768 个边界，仅 2 个边界分类不同。周期读器依据
application-ready 和逻辑预算做较宽的诊断；实际排序候选还经过 runtime
恢复资格、请求状态、工具状态等筛选。这两处周期日志中存在较小的
可见 ready 对象，但它们没有进入实际排序候选，且局部 Oracle 在两处
均未改变集合。精确拒绝守卫没有记录，不将 85 与 83 的差异归为某个
具体实现问题，也不改写原始读器结果。差异键和边界保存在
`summary.json.candidate_population_differences`。

窗口内实际执行 5642730 个 extend token，prefill worker 区间合计
596.953821 秒，约 0.105792 ms/extend token；decode worker 区间合计
985.977774 秒，约 1.809502 ms/有效 decode token，有效 decode 宽度按
worker 时间加权约 13.722341。精确 decode graph 没有配置 padding row。
这些区间包含 worker 与系统执行因素，不是纯 CUDA 时间；worker 区间
覆盖窗口也不证明 GPU 满载。后续应在同一到达表下检查排序是否改变了
decode 宽度、prefill 成本和实际服务，而不能把更少的逻辑入选者直接
等同于更低的实际 decode batch。

成本读器已核对输出 ordinal 连续性、分箱总量和实际执行配置，但仍明确
保留 `completed_request_receipts_reconciled=false`、
`complete_measured_execution_verified=false`、`telemetry_drained=false`。
本轮提供了继续完整配对所需的选择空间证据，暂不更换到达表或增加并发；
背景尾部覆盖、完整平均 JCT 和 Oracle 实际收益仍等待最终审计。
不跨 mean60 与 mean70 的不同到达窗口计算算法收益。

## mean70 Full 完整验收与 Oracle 接续

2026-11-07，America/Los_Angeles。mean70 非排序 Full 的 18 个固定
测量成员均已自然完成，`cohort_termination/completion.json` 核对
2991 个测量请求及 362157 个输出 token，确认没有截断测量成员。
背景按原协议停止，日志取得两份新鲜空闲快照，所有旧 serving/GPU
进程实际退出。冻结 supervisor 的原始中断状态按完成证书归类为预期
背景取消，不改写成另一份运行状态，也不因此重跑 Full。

| 完整测量与验收 | 结果 |
| --- | ---: |
| 完整平均 JCT | 1061.121917 秒 |
| P50 / P95 JCT | 723.312153 / 2775.338936 秒 |
| 最大 JCT | 3369.116674 秒 |
| 最后测量完成相对时间 | 3782.061165 秒 |
| 最后计划背景到达相对时间 | 4598.502355 秒 |
| 未覆盖测量尾部 | 0 秒 |
| 共同窗口输出 / decode 吞吐 | 346.998142 / 344.155909 token/s |

83 个计划成员中，70 个实际到达，59 个自然完成。24 个非测量成员按
协议右截尾，其中包括 13 个尚未提交的背景；因此实际到达而未完成的
背景为 11 个。不能称全部 83 个完成，也不将这些背景的条件完成子集
作为完整 JCT 样本。测量组身份和成员没有根据完成顺序重新选择。

`full_review/workload_decision.json` 的全部 11 项既有检查通过，
`accepted_for_oracle_diagnostic=true`、`failed_checks=[]`：

- 模型工作量 P80/P20 为 2.961973，沿用已放宽的 2.931418 门槛；
  不称已经达到 3 或 4。
- 共同窗口状态样本中 ready-paused 占 73.195187%，与周期控制点
  638/768 的 83.072917% 是不同分母，均不是时间加权占比。
- 最大采样 next-reuse union / 总池为 0.594844。它是状态需求诊断，
  不等于原生页分配或传输可行性证明。
- 同会话已服务前缀丢失代理为 0；最终排空快照仍有 22024 个 FULL
  block 重算 token，单独保留。Host 高水位为实际的 400000057344 字节。

完整周期审计记录 1861 次控制，application-ready 逻辑竞争诊断为
156 次；实际排序候选的局部重放记录 152 次竞争和 86 次集合变化，
其中 35 次变化在共同吞吐窗口之后。1817 个实际授予 episode 全部
保留且有精确后续服务，1708 个在下次控制或已记录同 agent 干预前
完成有效 batch。这些数据支持完整 Oracle 对照，仍不证明其反事实
JCT 收益。

同集合的服务顺序读器记录 262 次跨 workflow 选序变化，其中 249 次
各 workflow 均在有限观察区间内获得服务；204 次首服务时间相同，
226 次最短 workflow 已在最早服务波次。仅 23 次最短 workflow 晚于
另一条获得服务，观察到的最大差距为 1950.543759 ms。该读器不覆盖
所有准入路径，也不是可消除 JCT 的上界；Oracle 结果应特别核对改变
集合的成员是否真正获得更早或更连续的后续服务。

Oracle 已由原接续器自动注册并启动初始化，使用：

```text
ops/workflow_original83_host400_mean70_oracle_20261106_r2.registration.json
experiments/raw/workflow_original83_host400_mean70_oracle_20261106_r2/
```

保留原运行身份中的 `20261106`，不因跨过午夜重新命名。服务源码仍是
`631911fb`，原生树仍是 `434c35dd6621`，端口 19018。实际 supervisor
321889、server 322359 已观察为 nice0 且存活；尚在初始化时不能依据
共享协调状态残留的 Full 18/18 计数认定 Oracle 已完成。Oracle 的实际
回放和进度应以自己的注册、driver 和 cohort 观察器为准。

`ops/verify_background83_oracle_live_20261107.py` 实施独立只读检查，
复用已完成 Full 的相同流量/执行条件比较器和原生配置观察器，核对
实际排序开启、保留机制、GPU 归属、实际容量及原始 token/停止回执。
2026-11-07 00:09 America/Los_Angeles 已实际通过，结果为
`gpu_campaign_20261106_r2/live_oracle_verified.json`：

- Oracle 已进入实际回放，driver 323375、GPU scheduler 322450、
  supervisor 321889、server 322359 及协调器均存活、nice0，GPU 无其他
  计算进程；新固定测量组观察器 323377 实际存活、nice19。
- 实际容量仍为 805198 Device FULL token 和 4069011 Host FULL token，
  原生执行配置与已完成 Full 一致。
- 运行时剩余工作排序、未来工作决策及相应恢复排序开启；2 秒窗口、
  JOIN/EOS、active 连续性与预测工具卸载等保留机制不变，组合准入仍
  关闭。工具驱逐预测继续使用观测输入，不使用 Oracle 未来工具时长。
- 已封闭的首批 12 个请求、502 个输出 token 与原始 token 包、输出
  哈希及停止边界一致。它是运行核对，不是完整 Oracle JCT 或收益证明。

没有新增独立 GPU 小实验，也没有改变冻结 serving。Oracle 达标后才
接续 learned；两者都达标后接续 ThunderAgent acting-decay 和 native。
当前尚无完整 Oracle 收益，整体研究 goal 保持未完成。

### Oracle 早期实际选择与服务

`gpu_campaign_20261106_r2/oracle_live_service_r1/oracle_service.json`
由既有 `audit_ranked_oracle_service.py` 对实际运行的 Oracle 封闭前缀
生成。读器逐项重放冻结策略，并按相同控制时间、request/context/
attempt/session 身份关联原生服务，不改变运行中的服务：

| 早期实际作用 | 观察值 |
| --- | ---: |
| 实际 Oracle 选择边界 | 164 |
| 相对同状态非排序 Full 的候选顺序 / 集合变化 | 130 / 11 |
| 新入选 agent episode / 不同请求 attempt | 14 / 14 |
| 精确授予、压力后保留、有后续精确服务 | 14 / 14 / 14 |
| 下次控制或同 agent 已记录干预前有效服务 / 有效 batch 完成 | 12 / 12 |

14 个新入选 episode 中，1 个属于固定测量组的 web CMS 项目，其他为
非测量成员。该前缀的共同窗口和完整测量跨度均未结束；不会按该结果
重新选择测量组，也不将这 1 个 episode 当作测量 JCT 已改善的证据。
实际 Oracle 轨迹的局部非排序参照不是已执行 Full 另一条轨迹的重建，
没有模拟参照的物理分配、压力协调或后续状态。该结果验证实际排序
能够改变集合并被原生服务消费，净 JCT 与吞吐收益继续等待完整配对。
后续完整读器使用自己的完整前缀，不能将两次观察的计数相加。

### Oracle 完整共同窗口与同步控制成本

2026-11-07 00:36 America/Los_Angeles，Oracle 已经过完整共同窗口，
固定 18 个测量成员仍未全部结束。只读观察复用既有参数化
`window_costs.py`、`audit_ranked_oracle_service.py` 和同步控制计时读器，
在 CPU0、nice19、CUDA 隐藏下运行，没有修改冻结 serving 或启动 GPU
小实验。证据位于同一实验的 `oracle_window_costs_r1/`：

- `window_costs.json`：原生有效输出 ordinal、窗口分箱和批次执行成本。
- `oracle_service.json`：实际 Oracle 选择、精确授予、压力后保留和后续服务。
- `control_costs.json`：共同窗口两端的累积控制计时上下界。
- `summary.json`：绑定上述报告、Full 对照、相同到达表及测量组的配对摘要。

Full 的完整同步控制报告另存为
`completed_service_analysis/full_synchronous_costs.json`。两组均使用
预先固定的 55.692771--1638.954850 秒，吞吐人口包括所有背景 workflow。

| 完整共同窗口指标 | 非排序 Full | Oracle |
| --- | ---: | ---: |
| 全部输出 token | 549389 | 547911 |
| 输出吞吐，token/s | 346.998142 | 346.064627 |
| decode 吞吐，token/s | 344.155909 | 343.236289 |
| 实际 extend token | 5642730 | 5636978 |
| prefill worker 秒 | 596.953821 | 599.440026 |
| prefill worker ms/extend token | 0.105792 | 0.106341 |
| decode worker ms/有效输出 token | 1.809502 | 1.810638 |
| decode 有效宽度，按 worker 时间加权 | 13.722341 | 13.788950 |

输出吞吐变化为 -0.269026%，decode 为 -0.267210%，满足本轮吞吐
保留 98% 的工程门槛；两组都没有配置 decode graph padding row。
该窗口内暂未观察到明显的有效 decode 宽度损失。worker 区间不是纯
CUDA 时间，窗口输出相近也不能代替完整测量组 JCT，最终回执与排空
仍由完整执行审计核对。

实际 Oracle 的同窗口 768 个选择边界中，353 次候选顺序变化，41 次
集合变化；30 次成员数相同、10 次更少、1 次更多，没有严格子集删除。
新增选中 44 个 agent episode，对应 44 个不同请求 attempt，全部有
精确授予、压力后保留及后续精确服务；40 个在下一次控制或已记录的
同 agent 干预前取得有效服务并完成有效 batch。新增 episode 中有
10 个来自固定测量组的 5 个 workflow，全部获得精确后续服务。
这验证选择确实传递到了实际服务；局部参照仍只重放实际 Oracle
状态下的非排序规则，没有重建另一条 Full 的物理分配和后续轨迹。
不能把这 5 个 workflow 直接算作 JCT 已获益的成员。

| 同窗口线程 CPU 计时，秒 | 非排序 Full | Oracle |
| --- | ---: | ---: |
| 工具事件替换检查 | 24.316948 | 22.284454 |
| 周期工作集控制 | 1.467473 | 1.943926--1.944453 |
| 周期恢复选择 | 0.709699 | 0.795395--0.795546 |
| 剩余路径计算 | 2.612844 | 3.088039--3.088048 |

这些是累积计时发布的窗口边界，不是每次函数执行区间的精确切分。
周期控制包含恢复选择等子路径，工具替换包含事件处理、容量观察、
排序、安全检查和动作，不能把各项直接相加，也不能将线程 CPU
变化当作暴露的 GPU 空闲或可消除 JCT。Full 全观察跨度的工具替换
检查为 58.521681 秒，不能与 Oracle 当前未结束的累积值配对。

Full 的同窗口另有 334 个几何和身份一致的 prefill 抽样，其中
selection 线程 CPU 样本合计 25.343489 秒、P90 为 201.869523 ms。
该路径包含原生选批及相关操作，当前 `profile_shared_path=false`，
尚不能归因到某个检查函数；不乘以采样间隔推算全程成本，也不与
上表的包含性计时相加。全观察跨度另保留 1 个缺少精确原生 prefill
服务记录的排除样本，不将其算作零成本。

当前只通过共同窗口吞吐这一部分条件。完整平均 JCT、背景尾部覆盖、
Oracle 最终工作守恒及预测调度收益继续按原接续器验证，未修改门槛、
测量成员、到达表或后续 baseline 的启动条件。

### 共同窗口吞吐图与 CPU 绘图依赖

同一 `oracle_window_costs_r1/` 下新增
`decode_figures_cpu_r3/closed_window_decode.png`、同名 PDF、
`throughput_bins.csv` 和 `provenance.json`。图仅显示上述完整共同窗口的
decode 趋势及按 bin 时长加权的经验 CDF，不包含尚未完成的 Oracle
JCT。所有背景 workflow 都计入，30 秒分箱的最后一箱实际为
23.262079 秒；已核对两组相同边界、逐箱速率及原生 decode 输出总量。
采用既定 Okabe-Ito 配色，实际查看 PNG 后未发现文字或坐标遮挡。
两组整体分布接近，不据局部峰值推断完整 JCT 或 GPU 空闲。

首次 CPU 绘图因 `beliefkv-next` 环境缺少 `matplotlib` 失败，保存在
`decode_figures/failure.json`。仅以二进制 wheel、`--no-deps` 新增
matplotlib 3.10.7、contourpy 1.3.3、cycler 0.12.1、fonttools 4.60.1、
kiwisolver 1.4.9、pyparsing 3.2.5，安装回执为
`plot_dependencies_install.json`。原有 numpy 2.3.5、pillow 12.3.0、
python-dateutil 2.9.0.post0、packaging 26.2 的版本核对不变。

第二次调用因 campaign 导入把冻结分析快照放在 `sys.path` 前端，
误用了不含 `Oracle` 标签的旧配色表，保存在
`decode_figures_cpu_r2/failure.json`。成功调用显式加载当前绘图脚本，
并在 provenance 中记录实际配色及 CDF 函数的来源。上述失败均发生于
CPU 图表导出前，没有新增 GPU 实验、改写运行中的 serving 或替换
已注册计划。

随后用已完成 Full 的真实 cohort 报告实际运行原
`plot_workflow_cohort_comparison.py` CLI，完整成员检查、PNG/PDF 和 CSV
导出均成功，输出为 `plot_pipeline_cpu_smoke/`。这补齐了自动出图入口
的实际绘图依赖验证，不将它称为 Oracle 完整 JCT 验证或新的性能比较。

## mean70 完整 Oracle 通过与预测调度接续

2026-11-07，America/Los_Angeles。Oracle 的固定 18 个测量成员均已
自然完成；`oracle_comparison.json` 的平均 JCT、共同窗口输出吞吐和
背景尾部覆盖三项检查全部通过，`fixed_cohort_performance_target_met`
为 true。原接续器因此启动预先声明的 learned R5，没有更换 workload、
到达表、测量成员、资源或 serving 版本。

| 完整测量结果 | 非排序 Full | Oracle |
| --- | ---: | ---: |
| 固定测量 workflow | 18 | 18 |
| 测量请求 / 输出 token | 2991 / 362157 | 2991 / 362157 |
| 平均 JCT，秒 | 1061.121917 | 1036.098550 |
| P50 JCT，秒 | 723.312153 | 629.258360 |
| P95 JCT，秒 | 2775.338936 | 2720.397967 |
| 最大 JCT，秒 | 3369.116674 | 3239.762056 |
| 共同窗口输出吞吐，token/s | 346.998142 | 346.064627 |
| 最后测量完成相对时间，秒 | 3782.061165 | 3652.706547 |

完整平均 JCT 降低 25.023366 秒，即 **2.358199%**，11 个成员改善、
7 个成员变差。共同窗口输出吞吐变化 -0.269026%，两组背景计划都
覆盖完整测量尾部，未覆盖区间均为 0。比较只改变剩余工作排序，
不是跨 mean60/mean70 的比较，也没有加入新的控制开销修复。

`complete_oracle_verified.json` 另行核对比较器及完成证书绑定、两组
相同的固定成员与原始 token 工作量、实际容量、测量 JCT 算术和两份
新鲜健康空闲快照，并按 pid/start_ticks 复查旧 serving 的全部清理
身份均已退出。冻结 supervisor 保留的原始中断状态明确归类为测量组
完成后的背景取消，不作为失败重跑，也没有改写原始状态。

Oracle 的 83 个计划成员中，67 个实际到达，58 个自然完成，25 个
非测量成员右截尾，其中 16 个尚未提交。因此实际已到达而未完成的
背景为 9 个，不能称 83 个全部完成。完整测量工作没有被截断。
两组最终的同会话前缀丢失代理均为 0；FULL block 重算仍分别为
22024、23650 token，独立保留，不称零重算。实际 Host 容量均为
400000057344 字节，Device FULL 容量均为 805198 token。

完整 `completed_service_analysis/oracle_service.json` 记录 1796 个
实际选择边界、82 次集合变化、88 个新增 agent episode / 不同请求
attempt。88 个均精确授予、压力后保留并获得精确后续服务，82 个在
下一次控制或已记录同 agent 干预前取得有效服务及有效 batch 完成。
共同窗口仍是 768 个边界、41 次集合变化；完整报告与早期前缀不能
相加。局部非排序参照没有模拟另一条 Full 的物理分配和后续状态。

完整 Oracle 同步计时位于
`completed_service_analysis/oracle_synchronous_costs.json`。其最后
累积工具替换检查线程 CPU 为 56.033346 秒，Full 为 58.521681 秒；
观察跨度、控制次数及执行状态已经不同，不能把差值当作独立的控制
代码优化收益。两组没有新增控制代码差异，包含性计时也不能相加或
直接扣除 JCT。

需保留结果边界：20 个完整预热成员的均值反而回退 1.885891%，其中
15 个变差；背景只有 20 个共同完成成员，其 JCT 仍是条件完成子集
统计。不能将测量组的 2.358199% 外推为所有 workflow 均改善、正式
稳态或统计显著性，也尚未证明重复性。当前 Oracle 是特定排序规则
使用未来信息的诊断，不是全局最优 JCT 上界。

自动生成的完整 JCT ECDF、共同窗口 decode 趋势和时间加权分布位于
`completed_service_analysis/oracle_figures/`，PNG 已实际查看，保留
单次运行、固定测量组与背景人口说明。

learned 已注册于端口 19019，运行目录为
`experiments/raw/workflow_original83_host400_mean70_learned_20261106_r2/`，
serving 仍为 `631911fb`、原生树仍为 `434c35dd6621`，预测模型 SHA256
仍为 `fe07eabc1c2ee72230efdb2bff190276ffc372d45dfaeae41228d97a90bfc3d6`。
初始化阶段的 campaign 18/18 仍属于 Oracle，learned 进度须使用其
自己的 driver 和 cohort 观察器。预测调度达到相同门槛后，才接续
ThunderAgent acting-decay 和 native。learned 的 R5 组合选择与直接
Oracle 排序也有算法差异，两者差距不能全部归因于预测误差。

### learned 实际回放与预测器核对

2026-11-07 01:25 America/Los_Angeles，
`ops/verify_background83_learned_live_20261107.py` 的独立现场检查通过，
结果为 `gpu_campaign_20261106_r2/live_learned_verified.json`：

- supervisor 340996、server 341462、driver 342639、唯一 GPU scheduler
  341549 均实际存活、nice0；独立固定测量组观察器 342646 存活、nice19。
- 实际 Device 为 805198 FULL token，Host 为 4069011 FULL token /
  400000057344 字节，原生执行配置与完整 Full 相同。
- 实际策略为 R5 组合准入，普通协调边界 2000 ms、搜索软预算
  500000 ns；JOIN/EOS 权利与已有 active agent 连续性保留，
  没有新增请求抢占。原有工具预测卸载、原生需求恢复等共同机制不变。
- 预测模型实际 SHA 与声明的 `fe07eabc...bfc3d6` 完全一致，
  `future_trace_used_online=false`、`future_information_used=false`、
  `scheduler_waits_for_inference=false`，预测器 error 为空且有实际推理。
- 已封闭的 1362 个完成请求、121789 个输出 token 的输入输出哈希、
  请求/上下文身份、position sampler 与原生停止边界匹配原始 token
  包。已有 1185 条实际 R5 选择日志；它们不是 1185 次集合改变或
  1185 次独立竞争。

早先一次内联 CPU 检查误将模型路径作为期望 SHA 参数，导致检查器
拒绝；运行中的模型摘要实际匹配声明。修正后的检查器分别验证模型
文件哈希与声明摘要，并把先前参数错误保留在现场证明中，没有改写
运行中的预测器、注册或 GPU 轨迹。

该证明是现场执行和已完成请求前缀的一致性检查，不是完整 learned
JCT 或收益验证。当时 learned 自己的测量组为 0/18、实际到达 25，
未借用 Oracle 的完成计数；继续按原协议等待全部固定成员自然完成。

### learned 封闭共同窗口与实际选择前缀

`learned_window_costs_r1/summary.json` 使用同一个
55.6927713505--1638.9548502657 秒共同墙钟窗口，人口包括背景 workflow。
窗口已封闭，但 learned 固定测量组尚未全部完成，不能以窗口吞吐代替
完整 JCT 验证。

| 共同窗口指标 | 非排序 Full | learned R5 |
| --- | ---: | ---: |
| 输出吞吐，token/s | 346.998142 | 347.541956 |
| decode 吞吐，token/s | 344.155909 | 344.697828 |
| worker 时间加权有效 decode 宽度 | 13.722341 | 13.954142 |
| 实际 prefill extend token | 5642730 | 5644389 |
| prefill worker 时间，秒 | 596.953821 | 597.733392 |
| decode worker 时间 / 输出，ms | 1.809502 | 1.806265 |

共同窗口输出吞吐变化为 +0.156720%。learned 当前使用
`r5_frozen_batch_cost_proxy_capacity_v7`、`selection_objective=benefit` 和
`decode_cost_basis=full_batch`，准入收益没有再除以成本。该策略与
Oracle 的直接剩余路径排序还存在组合算法及目标差异，不能把最终差距
全部算作工作量预测误差。

同窗口 R5 组合选择累计线程 CPU 为 0.929761 秒，周期工作集控制
1.125773 秒，工具替换为 17.619789--17.819308 秒；另一个线程上的
预测 worker 为 9.056665--9.083413 秒。包含性计时存在重叠，worker
区间也不是纯 CUDA 时间，不能相加或视为可直接消除的 JCT。

`learned_live_service_r1/choices_corrected_live.json` 覆盖比共同窗口
更长的独立前缀：4614 条选择日志中，381 条进入可重放的搜索路径，
其中 346 次完成枚举、11 次真正触发软截止、24 次为零枚举提前返回。
最初摘要误将全部 35 次不完整调用称为截止，原文件保存在
`summary_initial_deadline_count.json`，当前摘要绑定其 SHA 并明确修正。

该前缀有 43 次集合变化、44 个新增请求 attempt，均有精确授予和后续
原生服务；28 个周期选择中有 25 个、16 个免费补位中有 11 个在下一次
控制或已记录同 agent 干预前完成有效服务。40 次改变保持相同数量，
3 次数量减少，没有严格子集式收缩。测量组涉及 6 个 workflow 的
10 个新增 episode。这些次数不是完整运行结果、全系统竞争覆盖率，
也不能转成反事实 JCT。

### CPU 选择审计器修复与最终审计接续

旧冻结 CPU 读取器误拒绝运行时合法的初始化记录：
`metadata_unavailable_reference_fallback` 同时包含空 `selected` 和空
`reference`。问题属于分析器，不是 GPU 调度故障。提交 `eb0b94ad`
仅修复读取器和测试：未搜索记录必须使用已知回退原因，并保留有效、
无重复且顺序完全相同的 reference；改变或重排、残留搜索字段仍拒绝。
此类记录保持 `search_replayed=false`，不授予新增服务或集合改变证据。

174 个不同相关 CPU 用例通过；最初因缺少原生路径环境变量跳过的
checkpoint 用例另行通过，未以跳过代替验证。初始真实失败调用没有
保存读取边界，`failure_reproduction.json` 是后续明确封闭的复现，
不是原始失败边界。修复后对该相同封闭复现的 3231 条记录重放通过：
277 次搜索、2954 次原样 reference 回退，见 `sealed_failure_after.json`。

原冻结分析计划及失败仍保留。独立的 CPU-only 最终观察器使用修复后
读取器，计划、验证和状态位于
`completed_service_analysis/learned_service_repaired/`。它等待完整
`learned_comparison.json` 后审计全部选择，即使性能门槛未通过也执行；
没有修改 campaign、注册、冻结服务代码或 GPU 启动条件。

另有完整 Oracle 的同状态目标诊断
`completed_service_analysis/oracle_rank_objectives.json`：相对实际含工具
关键路径，改成纯模型关键路径会改变 23 次集合，其中 8 次涉及 4 个
测量 workflow；改成模型总工作量会改变 19 次，其中 5 次涉及 3 个
测量 workflow。它保持当时的候选、预算和 JOIN 权利，只重放逻辑
选择，没有模拟后续物理执行，不能据此估算替代目标的 JCT 收益。

## mean70 完整预测调度通过与 baseline 接续

2026-11-07 02:21 America/Los_Angeles，
`learned_comparison.json` 已通过完整固定测量组平均 JCT、共同窗口输出
吞吐和背景尾部覆盖检查。Full、Oracle、learned 的各 18 个测量成员
均自然完成相同的 2991 个请求、362157 个输出 token；没有截去正常
长尾。三组分别通过原定 workload 的全部 11 项诊断，门槛未修改。

| 完整测量结果 | 非排序 Full | Oracle | learned R5 |
| --- | ---: | ---: | ---: |
| 平均 JCT，秒 | 1061.121917 | 1036.098550 | 1049.209646 |
| 相对非排序 Full 平均 JCT 降幅 | 0 | 2.358199% | 1.122611% |
| P50 JCT，秒 | 723.312153 | 629.258360 | 718.769721 |
| P95 JCT，秒 | 2775.338936 | 2720.397967 | 2704.624676 |
| 最大 JCT，秒 | 3369.116674 | 3239.762056 | 3264.697334 |
| 共同窗口输出吞吐，token/s | 346.998142 | 346.064627 | 347.541956 |
| 最后测量完成相对时间，秒 | 3782.061165 | 3652.706547 | 3677.641825 |

learned 的平均 JCT 减少 11.912270 秒、共同窗口输出吞吐变化
+0.156720%，达到此前至少 1% JCT 改善且吞吐不低于对照 98% 的
工程门槛。9 个测量成员改善、9 个变差。20 个完整预热成员平均 JCT
回退 0.754319%，6 个改善、14 个变差；21 个共同完成的背景成员是
条件完成子集，其均值变化不能代表全部背景任务。

learned 实际到达 69 个、自然完成 59 个，24 个非测量成员右截尾，
其中 14 个未提交、10 个到达但未完成。背景计划覆盖完整测量尾部，
未覆盖区间为 0。原始 supervisor 保留因背景取消而生成的
`stopped_for_investigation`，完成证书、精确测量工作、两份新鲜空闲
快照和实际 owned-process 退出由独立 cohort 协议核对，不改写原始
状态。它是单轮有界人口的正收益结果，不是重复性、统计显著性或
正式稳态的证明。

### 完整 R5 选择到实际服务

修复后的最终 CPU 审计完成，输出为
`completed_service_analysis/learned_service_repaired/choices.json`：

- 7358 条组合选择日志，546 条搜索路径重放，61 次集合变化。
- 56 次改变保持相同数量、4 次数量减少、1 次增加；这些是同状态
  逻辑参照的集合几何，不等于替代执行的实际 decode 宽度。
- 63 个新增 agent episode / 不同请求 attempt 全部有精确授予和
  后续原生服务；42 个周期选择均在压力协调后保留，21 个免费补位
  均有直接授予。没有把独立工具替换授予算作 R5 选择收益。
- 其中 38 个周期选择、16 个免费补位在下一次控制或已记录同 agent
  干预前完成有效服务。上述有界完成不是全部可消除 JCT。
- 全部选择元数据及选择计时累计线程 CPU 为 1.752212 秒；不是全程
  控制成本，也不能直接解释完整 JCT 差值。

原冻结读取器的 `learned_service` 失败及日志继续保留；修复后的
独立观察器明确报告完整测量执行已验证、退出码 0。原分析接续器
没有因此停止，完整 learned JCT、共同窗口 decode 趋势和时间加权
分布图已生成于 `completed_service_analysis/learned_figures/`，
PNG 已实际查看。

### 相同 workload 的对照顺序

两个排序门槛通过后，原 campaign 已注册并开始启动
`workflow_original83_host400_mean70_thunderagent_acting_decay_20261106_r2`，
端口 19020，serving 仍为 `631911fb`、native 树仍为 `434c35dd6621`。
02:26 已实际进入 replay，固定测量组观察器和顺序接续器均存活。
`live_thunder_verified.json` 独立验证首批 9 个精确请求、355 个输出
token，唯一 GPU scheduler 为 360817，supervisor/server/driver 均为
nice0，cohort 观察器为 nice19。实际 Device 为 805198 FULL token，
Host 为 4069011 FULL token / 400000057344 字节，与 Full 和声明一致。
原始任务、token 来源、依赖、工具 overlay、到达表和原生执行配置
亦与 Full 一致。

现场日志中的 paper 策略为
`thunderagent_public_single_backend_acting_decay`，program 身份为
`invocation_id`，控制周期 5000 ms、衰减半衰期 1 秒；Full
`agent_admission` 为空，prepare_host、预测工具卸载、工具预取和逐层
handoff 均关闭，`future_information_used=false`。这是实际启动和
已完成请求前缀验证，尚无完整 ThunderAgent JCT 或比较收益。

接着按既定条件运行端口 19021 的 `native_matched`。所有方法复用
相同的 83 个原项目、20 个初始 workflow、平均 70 秒持续到达表、
固定 18 个测量成员、原始 token/依赖/工具时间、实际 Device/Host
容量及原生执行配置。ThunderAgent 保留原 paper acting-decay 和
5 秒控制周期，不继承 Full 的 JOIN/EOS、预测卸载或 prepare_host。
SGLang 对照是相同 token 回放和共享执行平面的 native 策略臂，
关闭 Full 的调度迁移策略，不是重新采集自然轨迹或声称未修改上游。
两组均在固定测量组全部完成后停止背景，保留完整测量 JCT。

### 完整 learned 的预测误差与固定目标诊断

ThunderAgent 运行期间，以 nice19、CPU0、CUDA 隐藏的方式运行
`audit_learned_combination_prediction.py`，读取已经结束的 learned
日志，输出 `completed_service_analysis/learned_prediction_weights.json`。
提交 `7fd86b8f` 让该 CPU 审计器接受实际 capacity-v7/benefit 配置，
仍拒绝 v6/v7 与目标不匹配以及非 full-batch 成本。23 个相关 CPU
测试通过；服务代码和五组 GPU 配置没有变化。

7358 条记录中，546 条可重放搜索路径包含 502 次完整枚举、11 次真正
软截止和 33 次零枚举提前返回。预测对照只使用 502 次完整枚举，
不扩大原有搜索截止。其历史计数名 `metadata_unavailable=6812`
实际表示全部未重放搜索的记录，包括无竞争早返，不能解释为 6812 次
预测或候选元数据失败；原始计数及完整原因日志保留。

在其中 124 个“普通候选分别可规划容纳、但不能同时容纳”的跨 workflow
容量竞争边界上，按边界等权计算成对排序正确率；资源、成本目标、
候选和受保护 JOIN 均保持当时状态：

| 权重来源 | 成对排序正确率 | 相对实际执行改变的集合数 |
| --- | ---: | ---: |
| 实际缓存预测 | 83.440860% | 0 |
| 相同模型的理想同步预测 | 84.865591% | 4 |
| 真实剩余模型工作量的三档权重 | 82.701613% | 25 |
| 真实剩余模型工作量的连续权重 | 100% | 15 |

正确率人口是 124 个竞争边界、322 个不同 workflow 对和 497 次成对
观察；集合改变计数使用全部 502 次完整搜索，不能混淆这两个分母。
两者都属于固定测量组的候选对只有 14 个边界、15 个不同对和 20 次
观察；在这个较小子集上实际缓存预测的边界等权正确率为 89.285714%。
所有人口包含开发期训练项目，不能称项目隔离测试准确率。

本轮在线权重仍由三档概率形成软权重；没有把连续剩余量预测读出器
接入 serving。真实三档标签存在档内平局，正确率按 0.5 计，
因此其成对正确率可以低于保留软概率差别的模型。连续真值的 100%
由排序标签定义保证，不是一个学习预测器达到的准确率。

同步对照使用客户端已发布事件和粗粒度原生输出重建输入，不等同于
该时刻 runtime 已接收的精确状态，也未模拟在线 worker 队列。
仅 4 次集合差别不支持为了消除缓存滞后而新增同步推理。
连续真值改变 15 次集合说明仍有数值分辨率或预测误差方面的检查
空间，但没有证明替代集合的实际服务或 JCT。它的标签为模型总工作量，
直接 Oracle GPU 臂则按含工具的剩余关键路径排序，二者还有算法和
目标差异，不能把 2.358199% 与 1.122611% 的差距全部归因于模型。

## 2026-11-07 保留 83 项目与基线接续

用户短暂要求切回历史 73 项目时，原 ThunderAgent 运行已被实际停止：
原 campaign、supervisor、server、scheduler、driver 和测量观察器均退出，
证据为 `administrative_switch_to_original73.json`。该轮仅完成约 3/18
测量成员，是行政中断，不能继续拼接计时或作为完整对照。

随后确认当前到达表的前 73 项与历史表完全相同，第 74 项在
3987.687611 秒才到达，而本轮 Full、Oracle、learned 的固定测量组
均在此前完成。用户明确恢复最新 83 项目；没有创建或启动 73 项目
实验。完整 Full、Oracle、learned 均保留复用，不重新运行。

新的基线专用接续器为
`/home/longhao/experiment/ops/run_background83_baselines_20261107.py`，
计划为
`workflow_original83_host400_mean70_baselines_20261107_r1.plan.json`，
证据位于本 campaign 的 `baseline_restart_20261107_r1/`。
只顺序运行 ThunderAgent acting-decay（端口 19022）和 native_matched
（端口 19023），之后生成相同固定测量组的对比和五方法图。仍使用已验证
的 `631911fb` serving、`434c35dd6621` native 和全部相同的 workload、
到达、工具、token、Device/Host 容量及测量窗口，不新增 GPU 资格测试。

02:58 America/Los_Angeles 的独立现场证明
`baseline_restart_20261107_r1/live_thunder_verified.json` 验证唯一实际
GPU scheduler 为 371126，所有 GPU owner 为 nice0，测量观察器为
nice19；9 个已完成请求、355 个输出 token 的原始身份和哈希匹配。
Device 为 805198 token，Host 为 4069011 token / 400000057344 字节。
03:06 的 coordinator 状态为正在等待完整 ThunderAgent，25 个已到达、
0/18 测量成员完成，error 为空。它是启动和前缀证明，尚无新完整
ThunderAgent/SGLang 比较结果；native 需等待前一轮完整测量、排空及
实际 owned-process 清理后接续。

### Oracle 与 R5 差距的完整依赖链

新增只读 `scripts/audit_cohort_jct_paths.py`，复用已有因果路径核算，
只选择固定 18 条完整 workflow 的整个源子图。背景仍在原始执行中
保留，不要求被协议合法取消的背景也完成。缺测量事件、请求、原生
服务样本，或身份、token、时钟、依赖不一致时仍拒绝。35 个相关 CPU
用例通过，覆盖合法背景截尾及测量缺口拒绝；初次集成测试 fixture
缺 JOIN 身份的失败保留，修复的是 fixture，没有放宽原生验证。
首次调用误用了不存在的 `control` raw 目录，改用已完成的 `full`
目录后成功，没有修改 GPU 轨迹。

输出 `completed_service_analysis/complete_cohort_jct_paths.json` 独立
重建三组各 2991 个请求、362157 个输出的全部 18 条 JCT，均与 cohort
报告一致。R5 减 Oracle 的平均 JCT 为 13.111096 秒，其观察到的最后
完成依赖链差值为：

| 完整依赖链分量 | R5 减 Oracle，平均秒数 |
| --- | ---: |
| 请求提交至首次实际 native launch | +17.249654 |
| 首次 launch 至 native complete | -4.080137 |
| 其余事件和请求 dispatch 合计 | -0.058420 |
| 完整 JCT | +13.111096 |

首次 launch 是实际模型执行，不是流式首 token；launch 至 complete
包含请求交错及暂停，不能称为纯 GPU 时间。分量重建的是各方法实际
最后完成依赖链，路径可能不同，不能据此做模块的反事实因果归因。

| Workflow | Oracle JCT，秒 | R5 JCT，秒 | R5 减 Oracle，秒 | 首次 native 服务差，秒 |
| --- | ---: | ---: | ---: | ---: |
| python_web_cms_expert_002 | 583.224698 | 765.793491 | +182.568793 | +178.716244 |
| javascript_desktop_media_medium_056 | 519.936209 | 593.929119 | +73.992911 | +79.852145 |
| python_mobile_game_hard_024 | 262.209093 | 299.732189 | +37.523095 | +38.673837 |

CMS 在两组使用相同的实际阻塞分支。其首个请求有 211405 输入 token，
提交到首次 launch 为 Oracle 81.566026 秒、R5 260.300906 秒；首个
请求的 launch 至 complete 反而从 41.592013 秒降到 40.387374 秒。
CMS 整条依赖链排队差为 +242.083269 秒，launch 后差为 -59.574178 秒。
因此这条最大的差值主要表现为初次服务和后续服务前的等候，而不是
首次请求本身计算变慢。CMS 贡献 182.568793 秒，而 18 条净差值合计
236.0 秒；其他 workflow 有正有负，不能将单条等候直接推广为可消除
的全系统 JCT。

### 组合打分与预测误差分开

`completed_service_analysis/cms_combination_scores.json` 由独立 CPU
脚本 `ops/explain_background83_cms_choices_20261107.py` 生成，精确重放
7 个包含 CMS 的完整搜索容量竞争边界，再枚举保留 required JOIN 的
可行子集。它保留原候选、逻辑容量、物理规划、权重和成本；不是新的
在线控制或未来执行模拟。

其中 6 个边界尚在等待 CMS 的首个请求，CMS 的完整逻辑额度为
211505 token、真实剩余模型工作为 8664.99、缓存权重为 0.507176。
6 次都存在包含 CMS 的规划可行集合，但实际选择了其他集合。示例：

- 逻辑余量 216267 token 时，CMS 单独放得下，得分 0.507176。
- 实际选择两个其他 workflow 的 agent，分别消耗 14280 和 115368
  token。对应 workflow 还剩 32352.86 和 28421.00 模型工作单位，
  权重为 0.306349 和 0.346011，合计得分 0.652359。
- 这是多个 workflow 的收益求和超过一个大 KV 短 workflow；不是
  仍在把收益除以请求成本，也不是 CMS 被误判为单独放不下。
- 相同 7 个边界改成真实剩余模型工作量的连续权重，集合全部不变。
  当前权重压缩和预测误差仍值得检查，但单纯换成连续真值不足以
  改变这些具体选择。

这解释了当前组合目标与直接短剩余路径排序之间的一个实际差别。
它不证明任何情况下应牺牲多个对象的并行推进，也不能把首服务差
全部归因于这 7 个周期选择：其他准入入口及更早的执行历史没有被
该局部分析覆盖。

后续改进应优先在 CPU 上比较同候选、同资源、同 JOIN 权利下的
组合目标与直接短剩余量排序，并检查“大 KV 短任务被多个长任务
累计权重压后”的频率。若要量化纯预测损失，应保持同一组合算法和
目标，只替换为对应真值标签。当前 GPU Oracle 按含工具关键路径、
R5 按模型总工作量且使用组合收益，不能称 2.358199% 是当前预测器
同算法的严格上界。现场服务代码不因本次分析而改变；先完成正在
运行的 ThunderAgent 和随后 SGLang 对照。

### 同状态短剩余量排序的选择机会

`scripts/audit_combination_shortest_frontier.py` 继续读取相同封闭前缀，
先精确重放 502 次完整搜索，再保持实际候选窗口、逻辑/物理规划容量、
输入和增长预留、槽位及 required JOIN，比较收益组合与短剩余量的
greedy 代理。排序仍保持已就绪 sibling 相邻，不等待未就绪成员。
它是同状态的有界候选算法比较，不是对运行中直接 Oracle 所有入口
及完整候选人口的逐项复现。未改变 serving 或注册 GPU 实验。

输出为 `completed_service_analysis/shortest_frontier_choices.json`。
124 个普通跨 workflow 容量竞争边界中：

| 同状态选择 | 包含真值最短 workflow 的边界数 | 相对执行集合改变数 | 改变涉及测量 workflow 的边界数 |
| --- | ---: | ---: | ---: |
| 实际缓存权重＋当前组合 | 96/124 | 0 | 0 |
| 真值连续权重＋相同组合 | 110/124 | 15 | 7 |
| 实际缓存权重直接排序 | 107/124 | 20 | 10 |
| 真实剩余模型工作量直接排序 | 124/124 | 29 | 15 |

四组在全部 502 个完整搜索均有规划可行集合，保留 required JOIN。
124/124 是真值排序及可行性筛选的结果，不是学习预测准确率。
普通排序使用当前缓存权重，不新增同步推理或训练模型。原始标签、
配置、模型及选择核心哈希均与前一份预测审计绑定；20 个相关 CPU
用例通过，包含实际 sibling 相邻与未就绪 sibling 不阻塞的检查。

严格限定为单个可准入候选、无待共同准入的其他同 workflow 成员，
且其真实工作量小于所有已选普通 workflow 时，共找到 9 个“短单分支
被多个长 workflow 排后”的边界，涉及 8 个不同 workflow。其中
2 个边界属于测量成员，包括 CMS 首个请求。真值连续权重的原组合
仍在其中 6 次排后；预测直接排序可纳入 6 次，真值排序可纳入全部
9 次。不能称这种情况占主导，也不能按边界次数估算 JCT。

预测直接排序的 20 次集合改变中，8 次 agent 数量减少、1 次增加、
11 次保持不变。它可能更早服务短 workflow，也可能缩小实际并行度；
规划集合数不等于未来 decode 宽度。当前结果足以支持继续评估
短 workflow 的排序目标，但不足以证明直接替换就会获得额外 1% JCT
收益。所有比较保持实际历史，没有模拟后续服务、工具返回、容量
和竞争如何变化。

03:17 America/Los_Angeles，实际 ThunderAgent scheduler、driver、
cohort observer 和 coordinator 仍存活，固定测量组为 4/18、已到达
32 条，error 为空。同一运行继续自然完成，SGLang 尚未接管 GPU。
