# 两个设计点：工作集选择与执行所需的 KV 周转

2026-11-07，Asia/Shanghai，配图再次修订：不再将 D2 简化为通用 D2H/H2D。
现图显式画出条件工具剩余时间、runtime 成本/容量判断、`PREPARE_HOST`、
ACK 后卸载以及最后 child 信号触发的 parent JOIN 预取。
D1 补齐“任务与已获服务进度 → short/mid/long → 短任务权重 →
容量约束下的 JOIN 集合比较 → 真实执行/RETURN → 更新”的因果链。
本次只更新英文可编辑图、图源及其说明，不修改运行时或启动实验。

2026-11-04，北京时间。本文依据当前 main 的实际源码和近期审计收敛论文表述，
更新英文论文图，不修改运行时、冻结配置或实验。图源、代码指纹和实现边界见
[图文件说明](../figures/two_designs/README.md)及同目录 `manifest.json`。
此前三设计点和中文图保留为历史版本，后续仅更新英文图。

## 论文主线

**面向动态 Agent 工作流，联合决定哪些 agent 应获得执行机会，以及如何为它们
周转 KV 容量和恢复上下文，使工作集选择最终转化为实际的工作流推进。**

优化目标仍是完整 workflow JCT 和完成吞吐；请求交互性、长任务等待、重算和控制
成本同时约束设计。不能以逻辑激活、ACK 或迁移字节替代端到端收益。

不再把完成时间预测、缓存生命周期和准入分别包装成三个互相独立的贡献。
预测是选择和卸载的输入，缓存生命周期是容量周转的依据，原生准入是最终执行权威。

## 问题与设计一一对应

| 原有处理方式及其局限 | 对应设计点 | 核心干预 |
| --- | --- | --- |
| 原生请求队列，或仅按 agent 连续性、局部顺序及占用选择工作集，不能完整表达一个 workflow 的剩余工作与真实 ALL-JOIN 分支的互补关系。某些分支获得资格，parent 仍被另一未推进的分支阻塞。 | **D1：工作流完成导向的依赖感知工作集准入**；Dependency-aware Working-set Admission | 以 agent 为执行身份、workflow 为评价身份；结合已观察依赖和条件剩余工作，在原有预算中比较有限可行集合，避免把 fanout 直接变成更多权益。 |
| 逻辑准入、物理 KV 保留/驱逐和需求恢复分离。实际阻塞的 agent 可继续占用 HBM，已选中的请求却缺少恢复容量；授予资格也不等于 native 已能服务。 | **D2：执行需求绑定的 KV 容量周转**；Execution-coupled KV Turnover | 从真实消费者的安全输入、缺页及增长预留出发，寻找安全阻塞 donor；增量备份、ACK 后释放、缺页恢复，再以原生准入和首次服务闭环。 |

这些是具体机制差异，不是宣称全部已有系统都没有 workflow 信息、预取、工作集或
生命周期管理。ThunderAgent 的连续性/working set、既有 decay、原生 HiCache 和
allocator 是继承基础。图中对照为一个明确的失败模式，不冒充任一论文方法的完整复现。
两个标题本身也不构成新颖性证明。

## D1：先回答执行机会给谁

执行身份仍是单个 agent invocation。root 与 descendant 共享 workflow 的评价权重，
依赖关系仅来自真实已观察事件；SPAWN 不等于 parent 必然阻塞，未观察 JOIN 不能
用未来轨迹补入在线图。

R5 可选路径将当前任务元数据和已获服务/委派进度转换为条件剩余**模型工作**
的 short/mid/long 分布。它不是 child RETURN 的墙钟秒数，也不将工具等待、排队、
DMA 和额外重算并入固有工作。后台发布带版本的预测，在线选择不等待模型推理；
无效输入、过期或不支持时取消偏置并回到原参考方案。

对于真实阻塞 ALL-JOIN，组合评价考虑 ready sibling 的互补覆盖；其他候选按单成员
处理。只比较当前可准入的 agent，不等待尚未 ready 的 sibling，不主动驱逐旧 active。
六/八候选和缓存的资源摘要约束搜索规模；0.5ms 是组合子步骤的软预算，不是整个控制
回调的硬上限。

最新代码支持显式 v7 `selection_objective="benefit"`：容量、槽位、输入/增长和
必要保护作为约束，最大化 workflow/JOIN 收益代理。历史收益/成本比值目标单独保留。
最近审计已发现成本分母会压过正确的工作量排序，因此新图不把该比值画成论文主公式，
也不把取消分母写成已证明 JCT 改善。实际 native 容量和执行检查仍需重新通过。

机制图采用可复核的示例：两个新槽位、等 KV 占用、既有 active 固定；
A 的 short/mid/long 为 `.7/.2/.1`，B 为 `.1/.2/.7`，由实际
`q=pS+.5*pM+.25*pL` 得 `qA=.825, qB=.375`。
选 `{C1,B}` 的代理为 `.825×(1/2)^2+.375=.58125`；
选 `{C1,C2}` 为 `.825`，图上四舍五入为 `.58/.83`。
两组概率是说明用假设，不是实测模型输出或准确率。概率、权重与
selected/unfinished 分数逐一对应，不再用来源不明的任意权重解释选择。
这个条件下会选择后者；不意味着 sibling 永远优先，也不证明完整 JCT 最优。
两名 child 的真实 RETURN 才能解除 parent 的 JOIN。

## D2：再回答选中的执行怎样获得容量和上下文

先绑定已经选择的真实消费者及其 request/context/session/epoch，计算可复用的
安全输入 checkpoint、缺失 FULL 和新增输入/增长预留。预测较长工具等待只能提供
donor 候选和成本判断；物理动作还需当前真实短缺和原生安全权限。
工具路径使用“仍未结束”条件下的剩余生存分布；runtime 将其与必要备份、
消费者加载、donor 后续恢复和控制余量比较，再判断是否卸载。模型不学习净收益。
短等待或支持不足保留原有策略，不因一个点预测而强制往返。

稳定 FULL 按缺失 extent 增量 `PREPARE_HOST`；发出 D2H 不是释放容量。
真实 SPAWN 可提前备份 delegator 的稳定 FULL；真实 WAIT_JOIN 会刷新 parent
安全前缀。二者是备份入口，SPAWN 本身不授予物理驱逐权。
在 Host 副本落定、身份与引用/锁/在途条件仍有效后，才能释放可回收 HBM，
以 allocator 实际 free list 记账，不把预留、备份或逻辑额度提前算作空闲页。
真正 WAIT_JOIN donor 与预测工具等待 donor 使用各自的授权路径。

终态 child 的私有状态，仅在生命周期已允许结束且无引用用途时释放；共享前缀、
活跃状态继续保留。persistent/unknown context 的一次 invocation RETURN 不是
context closure，不能依据未来没有复用来回收。

消费者只恢复下一请求缺失的页，不按 agent 全量搬运。原生需求 H2D 期间，可推进
无依赖的驻留 decode，恢复后重新走正常 native 检查。原生 EOS 可提供 parent
恢复的提前提示，但 EOS 不替代 harness RETURN/JOIN，不给未 ready 请求执行许可。
恢复、准入和首次实际服务必须以同一消费者关联。
新 D2 时间轴区分 child 最后 decode、final EOS、协议返回/真实 JOIN、
parent Host-only 等待、H2D、请求提交及实际服务，不把 EOS 当作 RETURN，
不声称存在固定提前量，也不要求整次传输都在 RETURN 前完成。

当前图明确画 FULL-only 路径。工具/普通投机预取、physical layer handoff 和
mixed chunk 没有重新开启；不把先前 Mamba 图示当作最新默认模型的组成部分。
本轮也没有添加新抢占、强制时间片或无界恢复优先级。

## 实现与证据边界

| 机制 | 当前入口 | 图中的表达 |
| --- | --- | --- |
| 观测依赖、真实 JOIN 和执行资格 | `beliefkv/control/causal_graph.py`；`runtime/reference_thunder_admission.py` | P 与 C1/C2、真实 ALL、选中 agent |
| 条件工作分布及失效 | `runtime/workflow_work_worker.py`；`runtime/workflow_size_admission.py` | S/M/L 概率、短任务权重、真实服务后的更新 |
| 有界集合与 v7 分子目标 | `policy/workflow_combination.py`；`runtime/workflow_combination.py` | 可行集合比较，R5 candidate |
| 消费者绑定的工具期容量借用 | `runtime/predicted_tool_offload.py`；`runtime/reference_tool_replacement.py` | 工具剩余生存曲线、成本与真实需求、增量 PREPARE_HOST、ACK 后卸载 |
| parent 备份与 JOIN 预取 | `runtime/sglang_v0520_runtime.py` 的 spawn/join prepare、`dispatch_join_prefetch`；`runtime/join_physical_recovery.py` | P 备份/Host-only/缺页 H2D、最后 child EOS 提示、真实 JOIN 后服务 |
| 恢复与已有 decode 推进 | `runtime/demand_prefill_pipeline.py` | C1 缺页恢复、C2 热页、native 准入和实际 GPU 服务 |

R5 及 v7 是显式候选，不是当前所有 Full 实验的默认，也未建立稳定的新增 JCT 收益。
已实现 D2 的各路径覆盖取决于冻结配置，不能把整套系统结果归因于其中单个机制。
近期代码和审计优先于旧文档中的“尚未实现”历史状态：

- [条件分布实现](r5_conditional_distribution_implementation_20261102_zh.md)。
- [成本分母审计](../research/workflow_cost_denominator_audit_20261103_zh.md)。
- [当前瓶颈及逻辑选择到实际服务的缺口](../research/current_bottlenecks_20261103_zh.md)。
- [Host400 完整与 learned 结果](../research/host400_thunder_complete_and_learned_R5_20261102_zh.md)。

本组图不展示性能柱状图，不引入“已明显优于 native”或某一机制已独立获益的结论。

## 图与绘图参考

根据用户进一步要求，本套图现含系统总览、D1 专图和 D2 专图。D1 用已观察的
ALL-JOIN、已获服务进度、剩余工作概率、短任务权重和集合分数展示选择依据；
D2 沿用相同身份，展开工具预测卸载、parent 备份、ACK 后释放、
JOIN 提前 H2D 与真实 JOIN 后服务。
三张图沿用同一组 P/C1/C2/T；总览与 D2 专图各只有一组 HBM/Host，
D1 不额外画物理池，避免按阶段重复存储组件。
HBM 中 T/P 的源页、备份与恢复页展示连续动作，不声称占用同时存在或复用同一页号。
灰色宽箭头明确表示 allocator 容量复用；黄色曲线箭头表示真实 KV 数据迁移。
黑色圆圈只编号执行步骤。C1 请求的虚线页表示缺失前缀，C2 的实心页表示驻留，
真实 RETURN 接到 ALL 后才把 P 标成 ready。
用户现授权重新选择科研配色，因此本版采用 Paul Tol 的有名色值，
控制层蓝 `#4477AA`、执行协同青绿 `#117788`、HBM 浅紫 `#EEBBDD`、
Host 暖黄 `#EEDD88`。背景按明确比例与白色混合，不冒称 JANUS 原始取样或
Tol 原色。原色、来源和派生配方均记录在 `paper_palette.json`。
页号与细 owner 色条跨复制保持一致，线型和标签补充颜色语义；不声称组合后的
整套语义色盘已经通过所有色觉条件的可访问性验证。图只含英文标签。

联网查询并读取了 `plbbl/academic-diagram-skill` 当前可取的 `SKILL.md`；
该文件最新提交为 `73429d91f269e70bab68a709b29bd61cc82481f4`，
时间 `2026-09-05T04:03:58Z`，查询日期为 2026-11-04。
这是适用于可编辑科研架构/机制图的通用 skill，不将其声称为系统领域专用或网上最新。
参考的是先核对研究内容、区分架构与机制、原生矢量交付和逐条连线复查的方法。
本地 `scientific-slides` skill 用于可编辑 PPT 和实际渲染检查。

外部来源：

- https://github.com/plbbl/academic-diagram-skill
- https://github.com/plbbl/academic-diagram-skill/blob/73429d91f269e70bab68a709b29bd61cc82481f4/SKILL.md
- https://sronpersonalpages.nl/~pault/
- https://github.com/nsgrantham/disco
- https://github.com/K-Dense-AI/scientific-agent-skills/blob/main/skills/scientific-visualization/references/color_palettes.md

没有安装外部 skill、改动运行时或启动 GPU；不使用 worktree。
