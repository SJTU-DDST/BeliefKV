# Agentix、外部等待与有状态 Workflow 调度

日期：2026-10-22，北京时间。本文区分原论文、历史观测、已实现修复和研究假设。
北京时间 06:00 前不运行 GPU 实验。主对照继续使用简单 JOIN 优先，
不因这份研究直接启用尚未验收的新排序。

## 1. Program 与 Workflow 没有可用来声称创新的定义差异

Agentix 正式 NSDI 2026 论文将程序建模为运行时展开的 DAG，覆盖多线程
LLM 调用、工具、通用代码和人工中断。没有完整先验图、动态分叉、同步、
多轮调用和 program JCT 都已在其问题范围内。

其 PLAS 使用已完成调用的累计 LLM executor 服务量；ATLAS 对多线程
维护最长已观察服务路径标量，新调用继承程序当时的值，完成时更新。
原文理论递推依赖父调用，实际机制用该标量近似，不能把它描述为已知
未来 DAG 或精确剩余关键路径。队列等待单列用于防饥饿；工具阻塞墙钟
不直接加进这个 LLM 服务标量。这不等于它忽略工具。

论文另有离散 MLFQ、调用抢占、按程序等待/服务比提升、multi-step
调度和聚合 swap；还将长外部中断作为 process table 的可观察状态。
因此，“加入多级队列”“避免频繁换入换出”“先完成程序”均不是我们的
独有贡献。完整 Agentix baseline 必须保留这些行为，不能拿一个 LAS
排序器或 JOIN-only 冒充。此次未找到并核验可直接运行的正式作者 artifact。

## 2. 可以检验的差异位于资源代价，而非程序名称

Agentix 认识 swap 开销，但其优先级主体是已获得的 LLM 服务，而不是某次
continuation 的当前有效 checkpoint、暴露恢复等待和实际容量缺口。
我们可研究的条件是：同一程序历史优先级下，不同分支/恢复阶段的私有状态
大小、Host 有效副本、可回收容量和必要依赖存在显著差异。

例如两个程序都在同一服务档位，A 的必要状态尚未就绪，B 已驻留；
或者 A/B 都曾获得相同模型服务，A 正等待外部工具，B 的已就绪分支
是一个实际 JOIN 唯一剩余成员。历史服务量本身不足以区分这些当前状态。
这只是构造情形，不是证明 Agentix 原系统在该情形必然较差。

研究问题应收敛为：

> 在同样的 program JCT、公平预算和事件信息下，能否根据当前恢复的
> 真实依赖与切换成本，选择一个可执行的 continuation 集合，并协调
> 它的容量与执行，使收益超过控制工作、额外迁移及局部性偏置的代价？

外部工具等待还能改变“先服务谁”的价值：尽早启动独立外部阶段可能
与其他 GPU 服务重叠；反之，某 sibling 的长期外部等待可能掩盖另一
分支的小幅延迟。但仅知道工具正在运行并不能证明它还会阻塞多久，
也不能从事后工具结束时间生成在线 slack。

不能把这些都称为现有工作的空白。TokenCake 已协调时间、空间与重要度；
ThunderAgent 已做 program 工作集/资源感知调度；InferCept 已将中断成本
纳入管理。真正需要验证的是同信息、同底座下的增量，而不是机制名称。

## 3. 调度算法的取舍

以下均回到原论文或作者材料核对；“吸收”表示机制选择，不表示已经
移植整个系统，也不声称其原应用域的最优性能够直接转移到 GPU batch。

| 方法 | 依据与目标 | 本项目取舍 |
|---|---|---|
| Agentix PLAS/ATLAS | 非先知 program 已服务量、已观察路径，配合 MLFQ/抢占和防饥饿 | 必须作为强参照；吸收 root 共享预算、服务继承、离散优先级 |
| Tiresias DMLFQ | 未知 GPU job 长度时，用离散已服务量减少短任务阻塞并防饥饿 | 吸收低成本分档，避免逐 decode 步连续重评分；不把输出 token 当 executor 时间 |
| SRPT/SJF | 剩余或完整大小已知时优先短任务 | 用作受限 oracle；不能要求真实未来 token、工具数或完整 DAG |
| 多阶段 Gittins | 在给定阶段信息与分布的单服务器模型下优化平均响应时间 | 吸收“当前阶段信息有价值”；现有 GPU 批处理、工具并行、HBM 约束不满足其完整模型，暂不做复杂在线 index |
| HEFT | 以已知 DAG 和任务/通信成本进行异构最早完成时间规划 | 只借鉴通信/计算的联合成本；不把完整未来图假设带入动态 agent |
| Graphene | 已知任务图、资源打包、依赖关系共同影响集群 JCT | 吸收 work-conserving 和资源可行性；不实时扫描整个未知图 |
| Decima | 图表示与学习策略联合做 DAG job 调度 | 需要训练和额外泛化验证，当前先不放进高频控制路径 |
| Varys/Aalo coflow | 用 group completion 和带宽分配减少并行流同步拖延 | 借鉴 JOIN 整组完成目标；HBM 容量、GPU compute 和动态外部阶段不能简化成纯 coflow |
| Delay Scheduling | 在等待公平性和数据局部性之间做有界取舍 | 局部性只在相近等级内考虑，有界让步，不能为热 KV 无限推迟其他程序 |
| DRF | 多资源公平分配 | 可作公平约束；公平本身不是平均 JCT 的优化目标，不能直接当主排序 |
| InferCept | 面向中断的恢复/保存成本与请求服务管理 | 工具卸载应同时看成本、压力和等待；不能只看预测 precision |
| TokenCake | Agent serving 的 time/space/importance 协调 | 直接近邻；需要相同工具、EOS、writeback 信息的对照 |
| ThunderAgent | Program 工作集与资源/程序感知调度 | 直接近邻；需比较程序工作集管理与本项目逐 continuation 有效状态/容量管理的实际增量 |

择优后的结构不是再加一个任意权重总分：程序级服务分档/老化在外层；
当前可执行依赖和有界 JOIN 优先在内层；恢复成本只在相近等级、证据完整
时参与比较。一次执行机会不能被每个 child 重复领取。普通工具恢复保留在
真实准入前沿，当前不重新打开对所有工具的提前预取和优先级特权。

“恢复便宜先做”也不是普遍正确：同档位的热请求可能还要生成很久，冷请求
反而马上完成。缺少剩余工作信息时只能有界试验，并报告相反结果。
可见分支数同样不是剩余工作，原有宽度反例 V2646 继续保留。

## 4. 最新历史 Trace 的独立核查

使用 V2640/V2641 已封存的同一组独立工具/JOIN 后继人口，窗口为首 root
到达后 600–2520.937 秒。每个请求的图特征只来自客户端序号在该提交之前
的事件；工具结束的未来标签、最终完整图和未来 token 不进入快照。
客户端序号保持原始顺序，小幅事件时间倒退不通过事后重排“修正”。
服务端排队与客户端工具时长仅比较各自的持续时间，不减跨时钟的时间戳。

V2678 的结果：

| 历史 arm | 有服务证据的工具轮次 | 排队长于刚结束工具 | 工具 <=200ms 且排队 >=1s | 提交时仍有其他完成依赖在等工具 |
|---|---:|---:|---:|---:|
| Full V109 | 3691 | 3465，93.88% | 2297，62.23% | 11 |
| JOIN-only V111 | 4793 | 4355，90.86% | 2442，50.95% | 44 |
| scheduler-only V111 | 4821 | 4442，92.14% | 2525，52.38% | 30 |
| native V109 | 3630 | 3596，99.06% | 2977，82.01% | 26 |

Full 的工具时长 P50 8.64ms，排队至首次服务 P50 7.12s；JOIN-only
为 9.39ms/2.06s。队列等待不能直接当成可节约 JCT，逻辑 Host-hit
也不是实际 H2D 字节。这些历史运行的输出量和代码不同，表格不是因果消融。

这组人口不支持把“大量并行长工具的 slack 调度”立即设为主机制：
在这些后继提交时刻，另一必需分支仍等工具的样本很少；但这也不是所有
LLM 调度点的全人口，不能据此断言任何工具阶段预测都无用。
更直接的待解问题仍是频繁短工具返回后的反复排队、准入与恢复协同。

## 5. 本轮实际修复和参考实现

审计发现完成前沿候选的实质缺陷：DeepAgents 将并发 child 标为
`background`，即使 root 已经实际 WAIT_JOIN。原候选只统计 foreground，
将这些阻塞分支全部排除。V2677 保留了纯标签口径，不能当完成依赖宽度。

新 `workflow_frontier.py` 从 foreground 开始，只沿已观察到的
WAIT_JOIN/WAIT_CHILD 及 foreground 依赖展开，纳入实际等待的后台 child、
嵌套 child，保持独立后台任务的区别。未确认依赖保留为未知；排序回到
原队列，避免把缺失观测当成小任务。宽度继续按受影响 workflow 的事件
失效缓存，不每 decode 步重建图。

V2678 在 Full 的 3691 个工具后继中找到 990 个多完成分支快照；
原 foreground-only 口径却全部为 1。JOIN-only 对应为 1580/4793。
已纳入的所有服务样本都没有未知完成依赖。此修复只影响独立的
`workflow_completion` 候选；历史 Full 没启用它，不能据此归因历史性能。
它也没有修复“宽度不等于剩余工作”的算法局限。

新 `program_service.py` 是 CPU 参考核：支持 PLAS 的已完成服务求和、
ATLAS 的继承后最长已观察路径更新，以及同服务档位内可选的暴露恢复
成本比较。未知恢复成本保留该档 FIFO，跨档优先级不被热 KV 覆盖。
外部等待和输出 token 不记作 executor 服务。该核尚未连接 GPU executor
计时，不包含 Agentix 的 MLFQ quantum、运行中抢占和 anti-starvation，
因此不登记为完整 Agentix baseline，不自动用于本轮 Full。

CPU 测试覆盖并行完成次序、继承服务、无关程序/外部等待不污染账户、
档位限制、未知成本、阻塞后台成员、嵌套等待、依赖失效及无未来信息快照。
最终 V2680 共 3770 项通过，保留 V2671 的全部 3739 项，新增 31 项；
无 skip，2 个 subtest 通过。与此前相同的一个 Docker 沙箱测试仍明确
deselect，不计通过。V2681 绑定应用源码，原生引擎未修改，复用 V2664
绑定的 467 项通过资格。

V2679 保留正常 GC、101 个样本及全部原始计时。在同一 CPU fixture 中，
48 队列的完成候选/简单 Full/JOIN-only 约为 278.4/261.8/258.5us，
128 队列约为 548.7/516.7/511.6us。候选仍有额外成本，没有将它当作
默认更优。简单 Full/JOIN-only 的完整选择、图和 pending 状态一致，
fixture 不做真实 DMA，也不证明物理 Full 行为与 JOIN-only 相同。
此前 V2675 的绝对时间与本轮不同，不能据跨次测量宣称加速或退化。
最终资格见 V2682；新的 V118 延时启动登记继续等待用户授权时间，
先执行 Full、JOIN-only、工程对齐 native。

## 6. 下一步的验收顺序

1. 先完成当前 Full、JOIN-only、工程对齐 native 三组，保留真实输出、
   guard、未完成和长尾口径；不将候选直接混进主对照。
2. 接入可归因的 executor 服务账户，再忠实补全 Agentix 式分档/抢占/
   老化对照。已有 worker 区间包含 CPU/同步，不能改名成纯 GPU service。
3. 在相同服务优先级基础上测试当前缺失状态/容量/暴露恢复成本的增量；
   使用 native 已有证据，避免为了评分重新做长前缀匹配。
4. 比较纯 program 调度与该调度加 KV 协调；再看工具阶段预测是否还值得
   增加成本。若简单 program 策略解释主要收益，收缩 KV 新颖性主张。

CPU 正确性通过不证明有 GPU 性能提升；当前也没有证据证明这些差异
已经足以构成 OSDI/EuroSys 的独立贡献。

## 7. 工具调用究竟与 Agentix 有什么区别

不能将 Agentix 的 program 描述为静态计算图，也不能说它没有考虑工具。
其 process table 和执行流显式覆盖外部中断；§4.2 的 PLAS/ATLAS 则主要
根据已获得的 LLM 服务设置优先级。工具等待不是该服务量本身。
这是“已经支持工具”和“进一步用当前外部等待改变资源决策”的区别。

例如一个 JOIN 仍有两条必需分支：A 已进入外部等待，B 正等待 LLM；
另一 workflow C 只剩一个已就绪调用。若能够保守确认 A 的等待足以
覆盖 B 的让步，可以先服务 C，而不推迟该 JOIN。但是“分支在等工具”
本身不提供这个保证；外部工具可能马上返回。必须使用线上已观察到的
持续时间或经校准的条件分布，不能拿 trace 的未来结束时间当决策输入。
本项目现有后继提交人口中，这类并行工具阻塞样本很少，尚不适合设为
默认机制。其调度价值也并非由“多 agent”这个名称自动产生。

另一个更贴近当前负载的候选是：两个 continuation 在同一程序服务档，
一个可以直接执行，另一个必须腾出容量、恢复必要状态。原优先级的顺序
可能与实际完成时间的顺序不同。但始终选择热请求会使大状态程序饥饿，
并且缺少剩余生成长度时无法证明热请求更早完成。因此只在同档、
相近等待预算中比较已知的暴露恢复成本，未知代价保留原次序。
这也是待验收候选，不是 Agentix 在所有场景下的缺陷证明。

## 8. 进一步搜索后的筛选和实施边界

FastServe 的 NSDI 2026 论文已用多级反馈队列和分布式 KV 管理减少
交互生成延迟；不能把 MLFQ 或抢占本身再包装成新设计。
AgentInfer 官方公开设计也结合有界排队老化、当前计算阶段、HBM 压力
与短工具等待的状态保留。它是实现级近邻，不能用其功能描述替代独立的
性能证据，但足以提醒我们：“工具感知 + 压力阈值”不是可靠的新颖性主张。
TokenCake/ThunderAgent 继续是时间、容量和 program 工作集方面的
直接近邻，需要相同信息和同底座对照。

当前择优顺序：

1. 保留已验证过的简单 JOIN 优先作为默认主排序。按实际阻塞依赖识别
   最后 child，不用 foreground/background 标签猜测关键性。已经修正
   `workflow_completion` 候选的依赖展开；该候选仍不等同剩余工作预测。
2. 先建立 root 共享的真实模型服务账户，再实现 Agentix 风格的离散
   服务档和有界老化，防止每次新 child/新请求重新领取短任务优待。
   已完成的 `program_service.py` 只验证继承语义，没有连接执行器，
   不能登记为完整 Agentix baseline。实际接线时必须说明 batch 内服务
   归属规则，不能用输出 token 或带排队的请求墙钟冒充模型服务。
3. 在相同程序调度上单独增加当前有效 checkpoint、必要恢复依赖和容量
   协调，再比较 JCT、忙时吞吐、重复 H2D、重算和额外 CPU 工作。
   恢复便宜的偏好不得跨档覆盖公平性，也不得重新引入逐 decode 步
   全图扫描和长前缀匹配。
4. 工具阶段 slack 与学习调度保留为条件分支。先证明机会规模和分布
   稳定，再承担预测、训练及在线控制成本。纯离线排序不能重放生成
   轨迹和容量变化，不能被当作真实 JCT 提升。

审计恢复证据时还确认一个测量缺陷：旧 native 的 Mamba 前置等待使用
普通 layer fence，采样器将它记作最后状态层；新专用状态事件则记作
`pre_forward_state`。旧的总等待采样已经包括该等待，不能声称旧 trace
完全漏记 Mamba 等待。现已对齐两个路径的分类，见
`docs/experiments/restore_wait_attribution_20261022_zh.md`。
修正不改变实际 barrier、传输顺序或调度优先级，也不产生 JCT 收益结论。

## 原始来源

- Agentix，NSDI 2026，§2、§4.2，Algorithm 1：
  https://www.usenix.org/system/files/nsdi26-luo.pdf
- Tiresias，NSDI 2019，§4：
  https://www.usenix.org/system/files/nsdi19-gu.pdf
- Scully 等，多阶段 Gittins：
  https://arxiv.org/abs/1805.06865
- HEFT，Topcuoglu 等，TPDS 2002，DOI 10.1109/71.993206。
- Graphene，OSDI 2016，作者论文：
  https://www.microsoft.com/en-us/research/wp-content/uploads/2017/01/graphene_osdi16_cr.pdf
- Decima，SIGCOMM 2019，MIT 作者项目：
  https://web.mit.edu/decima/
- Varys，SIGCOMM 2014，作者论文：
  https://web.eecs.umich.edu/~mosharaf/Readings/Varys.pdf
- Aalo，SIGCOMM 2015，作者论文：
  https://www.mosharaf.com/wp-content/uploads/aalo-sigcomm15.pdf
- Delay Scheduling，EuroSys 2010，作者论文：
  https://cs.stanford.edu/~matei/papers/2010/eurosys_delay_scheduling.pdf
- DRF，NSDI 2011：
  https://www.usenix.org/conference/nsdi11/dominant-resource-fairness-fair-allocation-multiple-resource-types
- InferCept，ICML 2024：
  https://proceedings.mlr.press/v235/abhyankar24a.html
- TokenCake v5，§3–5：
  https://arxiv.org/html/2510.18586v5
- ThunderAgent，作者公开稿：
  https://arxiv.org/html/2602.13692v1
- FastServe，NSDI 2026；早期公开稿始于 2023：
  https://www.usenix.org/conference/nsdi26/presentation/wu-bingyang
- AgentInfer 官方调度设计，2026-08-12 修订版说明：
  https://github.com/openJiuwen-ai/agent-infer/blob/main/design/module/scheduling/progress-ttl-scheduling.md
