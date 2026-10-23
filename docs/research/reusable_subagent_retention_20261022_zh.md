# 跨轮复用 subagent 的选择性状态保留

日期：2026-10-22，北京时间。原论证中的持久会话、分层保留和边界预测
现已接入 main 的独立可选 profile，CPU 正确性已验证；GPU 收益与概率
校准仍待新 workload 验证。V124b Full 及后续 V125 消融继续使用原
workload；不在矩阵中间更换 prompt、会话语义或 root 数量。

2026-10-23 更新：原实验矩阵已结束。下一场景采用模型自主跨轮复用，
配置已对齐当前 V108/EOS 策略，关闭短工具连续性与普通前沿恢复；
显式关闭标签、结构化未解决项和相同会话重放的修复见
[最新准备记录](../experiments/reusable_workload_transition_20261023_zh.md)。
该记录的现行配置和 10 月 23 日晚 GPU 禁用时段优先于下文历史计划。

## 已落地的范围

实现及验证说明见
[跨轮复用实现记录](../experiments/reusable_subagents_implementation_20261022_zh.md)。
新 profile 为 `native_in_graph_reusable_nested`。原 profile 的任务语义
不变；默认 `BELIEFKV_SUBAGENT_RETENTION=off`，不构造休眠状态控制器。

- `ReusableSubagentRegistry` 在 workflow 内保存稳定 handle、私有 graph
  state 和 transcript。`task(..., agent_id=...)` 创建新的 invocation 和 JOIN，
  不复活旧 DONE；同 handle 仅允许一个 writer，关闭和 workflow 结束清理。
- RETURN 不关闭可复用会话。原生 radix session 在普通 resume 时继续使用；
  物理状态丢失后仍重放同一私有历史，原生 generation 可重新建立。压缩
  关闭旧 session；resume 的第一条请求即可压缩，不提前推进 create epoch。
- 原生安全点管理休眠状态：短暂、可撤销的 HBM 宽限；缺失 FULL 增量备份
  和必要 Mamba checkpoint 的异步备份；ACK 后释放独占 HBM；低价值状态
  通过原生终态后缀接口释放物理 KV，但逻辑 handle 仍可再次使用。
- 仅检查最新 path，按物理 extent identity 对预算去重，不保存全部旧
  checkpoint。保留预算对最新路径作保守核算；与活跃上下文已共享的
  页面没有精确计算其边际容量价值，不能声称这已是最优容量分配。
- 有界 scope hashing、返回结果未解决项、角色、实际复用次数以及后续
  真实委派的范围相似性只在语义边界更新。先用预算 MRU，足够正负样本后
  使用收缩的在线特征分桶概率和价值排序。正标签延迟到新 invocation
  实际提交模型调用；执行前取消与未完成 workflow 不记负例。

选择性与 LRU 初始 dormant Host 预算均为 32GiB，可撤销 HBM 预算 2GiB，
宽限最多 1s。这些是独立 profile 的初始参数，尚未 GPU 调优。价值模型
当前显式采用重算速度和传输速度的冷启动先验，并对完整 H2D 作保守成本
扣除；它还不是基于实测暴露等待的 JCT 模型，也不声称接近 oracle。
概率校准、项目留出和 regret 对比仍是后续工作。

四组对照使用完全相同的 API、prompt、任务和逻辑会话能力：
`all` 保持原生可撤销软缓存，不强迫全部返回状态立即 D2H；`drop`
释放物理 KV；`lru` 在同预算内保留近期状态；`selective` 执行分层选择。
新配置单独保存于 `configs/migration/reusable_subagent_retention_20261022.json`，
36 roots、running/decode8、seed21、256K 与 95% 压缩，待当前 V125 完成后验证。

## 研究判断

这个方向可实施，能形成比“EOS 前搬多少 KV”更独立的问题：
**一次 agent 执行返回，不代表它积累的私有会话状态永久失去用途；
有限的 HBM/Host 应保留哪些可能再次激活的会话，并在何时让出容量？**

但“语义预测 + 缓存淘汰”本身不足以证明 OSDI/EuroSys 级新颖性。
应验证动态创建的具体 agent 实例、不断增长的私有历史和不确定的跨轮
再次调用，是否使已有状态预测、生命周期与替换策略发生可重复的失败。
若所有休眠状态都能低成本放入 Host，选择性保存不会自动胜过全部软缓存。
若长期复用率很低，立即释放也可能是更好的策略。

不能将“给同一种角色再发一次 task”当作复用成功。复用必须是相同
handle 的原有私有消息历史继续追加；是否命中 KV 只改变执行代价，
不改变给模型的逻辑输入。所有对照都提供相同的持久会话能力。

## 现有工作边界

以下核查的是原论文，不能将未实现的区别宣称为已有贡献。

| 工作 | 与本想法直接重叠的部分 | 本项目需要额外验证的差异 |
| --- | --- | --- |
| KVFlow | 按 agent 工作流步骤及未来使用信息调整状态优先级和预取 | 运行中创建的实例能否在返回后再次使用其不断增长的私有历史 |
| CacheScout | agent/state 转移图、基于 Markov 的复用概率、保留与多层缓存 | 论文重点为稳定 agent anchor 前缀；应比较具体实例完整历史的再次激活，不能仅换一个概率评分器 |
| KVTether | ACTIVE/SUSPENDED/INACTIVE 生命周期、对仍活跃 agent 的历史保留与 MRU 替换 | 已完成 activation 与仍可再次调用的 context 是否需要不同的所有权与预算语义 |

原文：

- KVFlow：`https://arxiv.org/html/2507.07400`
- CacheScout：`https://arxiv.org/html/2608.14624`
- KVTether：`https://arxiv.org/html/2609.39819`

CacheScout 已把语义相关的状态/转移信息转为未来复用预测；KVTether
也已管理 agent 状态生命周期。因此真实的论文问题应是：**面对模型
运行中决定的再次委派，如何在不保留全部物理引用、不依赖已知未来 DAG
的前提下，让会话可恢复，并用有限状态预算降低完整 workflow 的成本？**
即便这样表述，仍须通过实现、强基线和真实收益证明区别有价值。

## 当前 trace 的证据

可重现审计：

- 脚本：`scripts/audit_subagent_retention_capacity.py`
- 首轮：`experiments/reports/v2790_subagent_retention_capacity_20261022.json`
- 含容量动作及同时间并存核算的更新：
  `experiments/reports/v2792_subagent_retention_pressure_20261022.json`
- 最终同前缀重放：
  `experiments/reports/v2794_subagent_retention_replay_20261022.json`；
  终态重试按真实释放时间累计，不提前计入可回收私有页。
- CPU 用例：V2789/V2791/V2793，最终五项通过，含真实解析路径、
  释放时间与日志继续追加后的同前缀重放。

每个 JSONL 独立封存到首次读取时的完整行，记录长度和 SHA256。
使用 runtime 的 wall/monotonic 同步点对齐；共同窗口取各服务器流与
metrics 均有证据的交集。它不是跨文件原子快照，也不是缓存替换模拟。
V124b 是运行中片段，不能代替最终 Full 结果。
本表使用封存至北京时间 2026-10-22 17:12:31 的同一组 V124b 前缀，
V2794 绑定 V2792 的长度和 SHA256，不读取随后增长的记录。

| 指标 | 最新 V124b 片段 | 已结束 V123 的观测窗口 | 已结束 V122 Full 的观测窗口 |
| --- | --- | --- | --- |
| 共同服务窗口 | 2493.06s | 2953.69s | 3380.87s |
| 返回的动态 child | 108 | 128 | 122 |
| child 最后已完成请求输入 P50/P90 | 36,689 / 71,114 token | 40,488 / 69,051 | 41,068 / 70,041 |
| Host FULL 占用 P50 / 最大 | 98.38 / 139.93GB | 97.49 / 156.31GB | 106.28 / 151.89GB |
| Host FULL 容量 | 160.00GB | 160.00GB | 160.00GB |
| FULL 容量动作未满足目标 | 89 / 145 | 40 / 107 | 46 / 120 |
| 动作前空闲 FULL P50 | 2664 token | 1718 token | 1085 token |
| 相同实例有多个 invocation | 0 | 0 | 0 |
| 同 session 历史前缀缺失代理占比 | 0.0508% | 0.0478% | 0.0462% |
| 单请求缺失代理 >=4096 token | 0 | 0 | 0 |

GB 为十进制。设备 FULL 池约 36.82GB、设备 Mamba 池约 33.10GB。
Host Mamba 池约 40.05GB，三个窗口的占用 P50 都已到容量。这是固定分池
配置和已有 checkpoint 留存的事实，不将某一模型状态槽不足当作创新。
FULL 的 89/145 是容量动作样本，不能外推成全程失败率。

客户端 `resident_pressure`/`num_used_tokens` 是活跃 token 指标，**不包含
全部可驱逐 HBM 页的完整人口**。V124b 活跃指标 P50 约 37%，仍可同时
出现上述 55MB 左右的即时自由容量，不能由该指标断言 HBM 大部分空闲。
Host 占用、Host 淘汰字节也都不等于有用工作集大小。

历史 V122 另有 67 次未备份冷页检查；这些检查对应的先前 Host 样本
全部至少有一批空间，Host FULL 自由字节最低约 32.76GB、中位约
67.07GB。原始来源为 `v122/full/prepare_background.json`，样本年龄约
2--993ms。这表明有些容量问题来自备份/引用与可回收性，不是 Host 满。

重算代理只统计相同 session 已服务输入的公共前缀缺失，排除了首次输入
和新增长；仍包含页对齐及混合 checkpoint 效应，不是 GPU 内核重算时间，
也不证明每个缺失可避免。当前 trace 没有真实“返回后再次调用”的标签，
所以不能用上述较低缺失率推断新机制无用，或预告它一定能降低 JCT。

### 休眠保留的容量核算

在 V124b 片段中，若每个返回 child 都保留到所属 workflow 结束，最多
同时存在 63 个休眠 child。其已服务输入按完全不共享核算的 FULL
峰值约 57.27GB；每个会话仅留一个必要 Mamba checkpoint 的场景还需
约 4.06GB。FULL 核算包括可共享前缀，且不包含未服务的后续输出，
不是实际新增私有页人口。

按真实终态回收记录，已释放 FULL 副本的去重集合未知；对每个 child
取 HBM/Host 两侧释放量的最大值与和，得到**仅已回收部分**的并集界。
它不覆盖该会话全部历史。与同时间 Host 样本相加，FULL 峰值场景
约 143.38--144.89GB；若按上述完全不共享的输入核算则为 193.87GB。
两种场景差距很大，不能把 193.87GB 当作已发生的容量需求。

保留策略真正需要新增的是最新可复用 checkpoint 的**独占缺失页**及
必要状态，公共页只计一次，旧的不可复用 checkpoint 不因 handle 仍在
就永久保留。应在新会话 API 中测量这些字节，不能靠逻辑 token 长度代替。

## 推荐的系统设计

### 一、把会话生命周期与执行依赖分开

原有非复用 profile 的 `deepagents_adapter._declare_task_group` 为每次 tool_call_id 创建新的
child invocation/context；`_complete_task_events` 在 RETURN 上设置
`context_retired=True`。`CausalGraph._on_reactivate` 拒绝 terminal invocation。
`NativeAdmissionRuntime._retire_terminal_child` 则安全回收无引用私有后缀。
因此仅修改 prompt 无法实现真实旧 subagent 再次执行。

需要以下状态：

1. root 范围稳定的 `agent_handle/context_id` 和私有消息历史；
2. 每轮调用新的 `activation/invocation_id`，本轮 JOIN 仅引用这些新 ID；
3. 活跃执行结束后，context 转为可再次调用的 dormant，invocation 仍为 DONE；
4. 显式关闭、workflow 结束或语义上永久丢弃时才删除会话；
5. 物理 KV 随时可以淘汰；没有 KV 也能从同一 transcript 正确恢复。

保留现有共享前缀、锁和在途传输安全性。逻辑 handle 不能变成长期设备/
Host 引用锁。新 activation 必须重新授权其 cache/session 访问，不能直接
把旧 DONE 改回 READY。普通 resume 保留 session/generation，推进输入
epoch；压缩、重置及原生 session 替换才执行相应的失效/代际处理。
同一 handle 同时只能有一个写入私有历史的 activation。

### 二、让缓存层做三选一

| 动作 | 适用情况 | 与当前路径的关系 |
| --- | --- | --- |
| 暂留 HBM | 已有可信的近期再次执行机会，且容量允许 | 可撤销，不能压住已就绪 parent |
| 增量保留 Host、释放 HBM | 未来可能复用但没有近期执行请求 | 默认候选，复用后台 dirty index 和安全 D2H/ACK |
| 释放物理 KV、仅留 transcript/handle | 复用价值低或容量竞争中不值得保留 | resume 时重算同一输入，语义不变 |

不在每个 child return 上无条件做大 D2H。若已有 Host 副本只补缺失
独占页；若价值不足，直接清理。必要备份在 native 安全点提交，ACK
前不释放仍需保存的源页。parent 恢复和运行请求优先，休眠备份在
传输批次边界让步，不假设能取消已经执行的 DMA。

容量紧张时保留“最可能再用且能省较多实际计算”的状态，而非最长状态。
可用下面的价值近似做分档排序：

`P(再次使用) * (重算代价 - 暴露的恢复代价) - 缺失D2H代价 - 容量占用代价 - 控制成本`

它是排序代理，不是最优平均 JCT 证明。恢复代价应使用暴露依赖等待，
不能把可重叠的整个 H2D stream 时间都算成收益。容量成本按独占 FULL
字节与必要状态分别记账；静态共享前缀只计一次。soft budget 随活跃
需求与 Host 空间变化，原生安全回收始终可撤销策略保护。

### 三、只在语义边界预测真实实例的再次使用

输入只使用决策当时可见的信息：原始委派范围、涉及文件/模块、角色，
child 返回时的未解决项/失败测试/后续建议，parent 的当前阶段，
该实例以往被继续调用的次数和间隔，以及后续新委派与其职责的相似度。
同一角色名称不足以判断复用，不能使用 trace 事后出现的下次调用。

先采用小型 CPU 特征模型，例如任务范围的 bounded hashing/文件集合、
结构化结果标记和校准的 logistic/生存模型。首次 return 更新一次；
parent 新一轮委派或明确关闭时更新相关会话。预测器位于异步准备路径，
不逐 decode token 解析文本、不每轮重算全图，不增加 GPU 推理模型。
新 handle 冷启动用受预算约束的 MRU/LRU；模型不可靠时回退。

正标签是相同 handle 的真实再次调用。显式关闭可结束对应 episode 并记
负例；其他未复用状态在 workflow 正常结束后才记负例。运行中片段和未完成 workflow 是删失样本。当前 workload
没有这种标签，不能拿旧 tool/JOIN 预测器直接声称准确率。
按项目留出、按时间先预测后更新，检查概率校准、字节/重算代价加权
precision/recall 和 policy regret；“97% never reuse”不能成为好预测器。

## API、prompt 与 workload

增加可选 `task(..., agent_id=handle)`，或单独的 `resume_agent(handle, task)`；
新 task 的普通工具结果附上稳定 handle。registry 与私有 LangGraph
checkpoint 存于 root 范围，与 parent 消息通道隔离。handle 清单通过
追加工具消息提供，不能反复改 system prompt 前部而破坏现有前缀。

新 profile 的 prompt 应保留当前任务难度、原始材料只给 root、child
根据职责取源码以及动态嵌套委派的基本方式，并增加以下引导：

> 在检查结果、实现和验证之间，可以进行多轮委派。后续问题仍属于先前
> specialist 的职责范围时，可使用其返回的 agent handle 继续交流，并
> 提供新增证据；独立审查、新职责或需要不同判断时创建新 agent。根据
> 实际进展决定何时继续委派、复用或结束，不要求固定次数或固定 DAG。

先做仓库探索，再按测试结果回访原实现者、再独立验证，是自然的复用
机会。不要硬编码所有 parent 第二轮必须唤醒指定 child，也不要给模型
未来调用表。语义缓存策略不改变工具输出中的 handle 可见性，不把保留
与否告诉 parent，以免策略本身改变委派决策。

还需验证：并行 resume 排斥/排队、取消、跨 root handle 拒绝、session
失效、重算恢复、摘要/压缩、workflow 结束清理及共享页回收。工作区文件
更新作为新证据追加给 agent；已有对话的 KV 有效性由准确 token 前缀
决定，不能因为文件内容变了就任意复用不同 token 的 checkpoint。

## Root 数量选择

**本轮消融固定 36；新复用 workload 也建议先以 36 roots、running/decode8
采集独占字节与复用标签，暂不降到 16。**

理由是目前已有 HBM 容量竞争，却没有明显的大规模同 session 历史丢失；
真实已回收的私有 FULL 只占少量，而逻辑全历史估计明显偏大。尚无证据
说明新的必要活跃工作集一定容纳不下。直接减为 20/16 可能让全部 Host
软缓存都放得下，从而失去比较选择策略的空间。

首轮先核查去重后的 dormant 最新 checkpoint、活跃必要页、实际有用
Host 淘汰及其后续重算。如果在可撤销休眠策略下，36 仍造成持续的有用
活跃前缀丢失/重算，则将同一套策略与基线一起改为 20；只有 20 也无法
容纳必要活跃集时才考虑 16。不能通过降低单个策略的 root 数获得优势，
也不能把 root 数与 running8 混为一谈。

这不是预告 36 一定最佳，而是基于当前缺少独占 dormant 数据、实际重算
证据较弱时的最小改动选择。新 profile 的真实容量核算完成后再冻结
正式对比配置，不凭不共享上界强行选择低并发。

## 验证顺序与退出条件

先完成 V124b/V125 的当前 workload 消融，单独保留此前模块的收益问题。
新方向先实现持久会话的 CPU 正确性及不依赖 KV 的相同 transcript
重算，再采集真实 resume 标签/去重容量，随后比较状态策略：

- 全部立即释放 KV，但保留同样的逻辑会话与 transcript；
- 全部作为普通软缓存保留，容量压力按原生 LRU 回收，不永久 pin 全部；
- 同预算 MRU/LRU，及可复现的生命周期/转移预测强基线；
- 只用结构特征、加语义特征、完整的分层保留策略；
- 事后知道真实再次使用的 oracle，仅用于收益空间上界。

保持同一复用 API、prompt、任务、root 数、容量、随机种子与完成要求。
记录完整平均 JCT、吞吐、质量、实际工作量、关键恢复等待、重算 token、
保留 GB*s、无效备份、重复 H2D，以及边界预测/策略 CPU 成本。
不要将“复用 agent 少说了一遍历史”与“相同历史命中 KV”混为系统收益。

若软缓存 LRU 已保住几乎全部真实复用状态，或选择性策略净收益不足以
覆盖备份和控制成本，则不把这个方向升级为核心贡献。若有明显收益，
还需证明来自动态私有历史的不确定生命周期，而非人为缩小某一模型的
状态池、强制反复调用、改变完成质量或只对比全部 pin/全部丢弃的弱对照。
