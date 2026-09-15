# BeliefKV 相关工作与竞品对比总表

更新日期：2026-09-14
用途：研究定位、reference policy 设计、原生系统部署和论文 baseline 选择。
范围：合并此前整理的 Agent serving、workflow scheduling 和 KV-cache 工作，并加入截至本日期检索到的最新直接竞品。2026-09-14 增量检索重点覆盖 arXiv 2026 年 7-9 月的新稿与旧表漏项。

## 1. 使用说明

本文按与 BeliefKV 当前目标的重叠程度分成三层：

- **A：直接竞品**。已经覆盖 Agent/workflow 语义驱动的 KV 驻留、驱逐、预测、offload 或工作集控制，必须进入主要 baseline。
- **B：强邻近系统**。主要优化 workflow/program 调度、prefill/decode 或集群放置，可能解释 BeliefKV 的部分收益。
- **C：机制邻近工作**。提供共享、压缩、分层缓存、部分序推断或 observation/prefill 机制，但不直接解决单卡动态 MAS KV 联合控制。

“元数据”列只描述在线策略实际需要的信息。论文使用 hindsight 或人工构造信息时，不能把该结果直接当作 metadata-free 在线能力。

### 1.1 当前实施决定

`B0-B4` 是 BeliefKV 早期使用的内部对照标签，不是相关论文定义的标准名称。当前 P3-P6
只启用 B0 reactive baseline、SGLang/HiCache 数据面基线和 BeliefKV 内部 O0-O3 joint oracle。
早期 B1-B4 same-data-plane 草图不是原系统复现，现已从维护代码删除：

- B1/ScaleSim-style 需要 invocation distance，动态未 spawn agent 通常没有该输入；
- B2/AugServe-style 需要 output/tool-duration signal；
- B3/ThunderAgent-style 需要真实 program/phase metadata；
- B4/CONCUR-style 只依赖在线拥塞反馈，是后期最容易公平适配的一项。

保留通用 `PolicyInput/PolicyOutput` 和中立 physical trace 是为了 BeliefKV 自身调试、oracle 和
未来离线复放，不表示当前正在搭建完整竞品框架。P8 根据稳定 workload、论文代码和真实
metadata 条件重新实现值得保留的 baseline，不恢复整套早期草图。

## 2. 当前结论

截至 2026-09-14，以下宽泛表述均已被现有工作覆盖，不能单独作为 BeliefKV 的主要创新：

1. 根据 workflow 图或未来调用距离驱逐、offload 和 prefetch KV：KVFlow、TokenCake、ScaleSim。
2. 预测动态 workflow 的未来 Agent 序列，再估计 KV reuse potential：PBKV。
3. 不依赖预定义 workflow 图，在线学习一阶 Agent 转移矩阵并驱动 eviction/prefetch：CacheScout。
4. 在单卡上联合选择 resident Agent prefixes 和 admitted requests，并计入 CPU-GPU movement、preemption、最长剩余路径与 aging：TOPAS。
5. 通过工具间隔分布、重载/重算代价和排队延迟设置 KV TTL，再与 program-level scheduling 联动：Continuum。
6. 根据生命周期状态、API 时长和内存压力联合调度，并在 Preserve/Swap/Discard 中选择：Astraea。
7. 根据 coding-agent tool metadata 预测 idle/reuse，再做 prefix-aware scheduling 和 eviction：CacheWise。
8. 根据工具时长、输出长度和 space-time cost 选择 Preserve/Swap/Discard：InferCept、AugServe。
9. 根据 Reasoning/Acting phase 和等待时间控制 Program 工作集：ThunderAgent。
10. 根据 KV pressure/hit rate 自适应控制 active Agent 数：PACE/CONCUR。
11. 区分 cold/resume prefill 与 decode，并保护 decode 资源：AgentServe。
12. 将 Agent 完整响应时间/TPS 置于面向人类的逐 token 流式体验之前：SMetric。
13. 利用 prompt 的 semantic region、跨轮 intent 或低比特表示缩减 KV：MemDecay、IntentKV、UltraQuant。
14. 对同步 round/all-gather MAS 的兄弟上下文做 collective sharing 和 diff storage：TokenDance。

当前仍可能形成独立贡献的窄边界是：

> 在没有共同已知 DAG、没有显式 Agent/program 标识的单 GPU 动态 MAS 中，从每个 workflow 的运行时因果事件推断 action readiness，而不是只学习 workload-global Agent 转移；联合管理 session history/动态 suffix 的 exact KV、HBM/host residency、受争用 PCIe 队列和执行 admission；对错误预测只执行有预算、可撤销的动作，并以 workflow JCT、公平性和 action-unlock time 为目标。

即使采用该表述，当前边界也只是 **CacheScout 的动态在线学习 + TOPAS 的 joint state search +
Continuum 的跨工具 KV 生命周期** 三者交集中的剩余空间，不能仅凭系统组合宣称创新。最终投稿前
必须在 P8 证明：三者的忠实组合仍无法达到 BeliefKV 的效果，且收益来自新的信息边界、问题形式
或可证明/可测的在线控制机制，而不是更多工程动作。该要求是论文主张门槛，不是 P3-P6 的功能实现
前置条件。

## 3. A 类：直接 KV 与工作集管理竞品

| 工作 | 目标场景与部署 | 调度语义/粒度 | 在线所需先验或元数据 | 核心 KV/内存动作 | 主要目标与结果 | 与 BeliefKV 的冲突及缺口 |
|---|---|---|---|---|---|---|
| SGLang RadixAttention / HiCache | 通用 LLM serving；单机或分布式；BeliefKV 当前数据面 | request、radix node、KV page | token prefix、物理 cache state；无 Agent workflow 语义 | LRU/leaf eviction、GPU-CPU-L3 write/load、异步传输、prefix sharing | TTFT、throughput、cache hit | **基础强 baseline，不是 Agent 策略。** BeliefKV 不应重新声明分页、多级缓存、write-through、异步 DMA 或 prefix sharing。
| InferCept, OSDI 2024 | tool-interrupted augmented LLM serving | intercepted request/context | context length、预计工具执行时间 | Preserve、Swap、Discard | 减少重算与等待期 HBM 浪费 | **高重叠。** 已覆盖工具等待期间 KV 生命周期选择；不理解多 Agent 依赖、future owner 和 workflow fairness。
| [Continuum](https://arxiv.org/abs/2511.02230), arXiv 2026 v7 | 多轮 Agent program；单 A100/B200 及多 GPU；vLLM | program、tool gap、KV state | 每类工具 finish-within-TTL 分布、KV 大小、reload/recompute、排队和乱序延迟 | 每工具 KV TTL、到期回收、deadlock breaking；与 program-level FCFS 联动 | 更新稿报告平均 JCT 改善超过 8x | **最高优先级且代码公开。** 已覆盖 tool-wait KV 生命周期与 program scheduling 的联动；未做未知 action readiness、object-level joint search 或共享 ownership。论文标题/正文中系统拼写存在 `Continuum/Continnum` 差异，引用时以正式标题为准。
| [Astraea](https://arxiv.org/abs/2512.14142), arXiv 2025 | 多状态 Agent request；单/双 A100；vLLM | session lifecycle、MLFQ queue、KV context | prefill profile、API 类别均值、生成长度 oracle | token-cost demotion、I/O promotion、HRRN+aging；压力下 Preserve/Swap/Discard min-waste | 报告平均 JCT 最高降低 25.5% | **概念高重叠、证据需谨慎。** 生命周期调度和三态 KV policy 已覆盖；结果依赖 generation-length oracle 和重加权 API latency，且未发现公开代码。
| [KVFlow](https://openreview.net/pdf?id=5Iw1nDtYmT), NeurIPS 2025 | 固定或可抽象的多 Agent workflow；SGLang hierarchical radix cache | Agent Step Graph、agent 和 KV node | workflow 执行图、steps-to-execution | node-level eviction、CPU->GPU next-step prefetch、共享 prefix 最小距离 | 单 workflow 最高 1.83x，多 workflow 最高 2.19x | **最高优先级。** 覆盖图感知 future-use eviction/prefetch；对在线展开、循环、未知 consumer 和弱元数据支持有限。代码已公开并已在本地复现。
| [TokenCake](https://arxiv.org/abs/2510.18586), arXiv 2026 v3 | function-call-heavy MAS；coding/deep research | workflow graph、critical-path agent | workflow 结构、criticality、工具事件和返回预测 | Temporal offload/predictive upload；Spatial reserved/shared pool | E2E latency 降低 47.06%，有效 HBM 利用率提升 16.9% | **最高优先级。** 同时覆盖时域 offload 和关键路径显存预留；动态未知 workflow、预测置信度和细粒度 shared-page ownership 仍是缺口。
| [PBKV](https://arxiv.org/abs/2605.06472), arXiv 2026 | 动态 Agent workflow | workflow 内未来多步 Agent invocation、cache entry | 历史 workflow + 当前 task context；预测未来数步 Agent | prediction-based reuse score、保守 eviction 和 prefetch | 动态 workflow 相对 LRU 最高 1.85x；静态 workflow 相对 KVFlow 最高 1.26x | **当前最直接的预测竞品。** 已覆盖“预测 next agents/多步 continuation 再管理 KV”；BeliefKV 的 context tree/Markov predictor 本身不再新颖。必须比较弱元数据、校准/OOD、action-criticality 和联合物理执行。
| [CacheScout](https://arxiv.org/abs/2608.14624), arXiv 2026-07 | 无预定义 DAG 的动态 MAS；vLLM 0.11；单机多卡实验 | Agent prefix anchor、在线 Agent transition | 从 prompt-prefix block hash 推断 Agent identity；无需框架标识；在线一阶转移矩阵 | survival-aware eviction、CPU offload、基于可预测度门控的后台 top-1 anchor warmup | cache hit +10-18 pp；mean TTFT -18%-45%；峰值吞吐 +19%-57% | **对原预测路线构成直接否定。** 已覆盖“无图、无接口、在线 Markov + eviction/prefetch”；其局限是一阶 workload-global 转移、偏静态 Agent anchor，未联合 task JCT/fairness、session dynamic suffix 和 execution admission。未发现公开代码。
| [TOPAS](https://arxiv.org/abs/2608.25523), arXiv 2026-08 | 单 A100 80GB；SGLang 0.5.3；已知共同 DAG | post-decision resident-prefix set + admitted-request set | task DAG、Agent 映射、剩余 stage/service 估计、prefix movement cost | 小 Agent 池枚举；大池 greedy add/pair/drop/swap；联合 HBM residency、CPU/GPU movement、admission 和 aging | synthetic mean/p99 JCT 最高 -39.8%/-49.4%；MetaGPT mean -9.8%/-22% | **对 JointPlan 构成最直接竞争。** 已联合最长剩余路径、下游复用、movement、preemption 和公平性；缺口是所有 task 共用已知 DAG，未处理 opaque dynamic workflow、真实异步 PCIe contention/shared ownership。未发现公开代码。
| [CacheWise](https://arxiv.org/abs/2606.16824), arXiv 2026 | 长时闭环 coding agents；vLLM | coding session、prefix/cache entry | tool-call metadata、真实 coding trace 统计 | prefix-aware scheduling、prediction-guided reuse-aware eviction | eviction 降低 2-2.6x；session completion time 最高改善 3.5x | **当前最直接的真实 workload 竞品。** 覆盖 coding-agent trace、工具 metadata 预测和 session JCT；BeliefKV 必须在跨 coding/search/research 泛化、action frontier、PCIe 联合控制或更弱接口上胜出。
| [AugServe](https://arxiv.org/abs/2512.04013), ICML 2026 | augmented requests；含单 RTX4090，也含 H800/A100 | 跨工具调用的 request service segment | BERT 预测 output length 和 tool duration；真实 return length | InferCept policy + space-time value-density 排序 + free/reclaimable token budget | 对 vLLM/InferCept 汇总 goodput 6.5x/4.7x | **高重叠。** 覆盖预测工具行为、KV policy 和运行时 refinement；不建模 next Agent、依赖解锁和 workflow fairness，目标仍偏 TTFT/normalized latency。
| [ScaleSim](https://arxiv.org/abs/2601.21473), ICML 2026 | 大规模多 Agent simulation；主实验单 H100，SGLang 0.5.2 | agent-specific memory object | 前端持续提供 invocation distance；不同应用手工定义 seconds/hops | distance eviction、主动 prefetch、共享对象 min-distance、可抢占 H2D queue | 相对 SGLang 最高 1.74x | **高重叠。** 覆盖 future-use prefetch/eviction 和共享对象；不解决如何从 opaque workflow 获得可比较、带置信度的 distance。
| [ThunderAgent](https://arxiv.org/abs/2602.13692), ICML 2026 Spotlight | coding、routing、science Agent serving/RL rollout；主实验 H100 集群，另有 RTX5090 | 长期 Program 工作集 | `program_id`、LLM/tool 关联、Reasoning/Acting、显式 release | periodic pressure check、Pause/Restore、elapsed-time decay、global queue；可结合 HiCache | serving 1.48-3.58x；rollout 1.79-3.92x | **高重叠。** 覆盖 phase-aware KV lifecycle 和工具等待降权；完整 Program 粒度、显式接口、无 action DAG/parent-child、无单卡 object-level PCIe 联合优化。代码公开。
| [PACE/CONCUR](https://arxiv.org/abs/2601.22705), ICML 2026 | 高并发 agentic batch inference | active Agent admission window | KV usage、cache hit、拥塞反馈；不要求未来图 | AIMD 风格增加/收缩 active-Agent window | 防止 middle-phase KV thrashing 与吞吐崩溃 | **高重叠的反馈控制 baseline。** 不决定单个 KV 的 keep/offload/prefetch，不区分未来价值；可解释只靠 admission 即获得的收益。
| [MemDecay](https://arxiv.org/abs/2607.10582), arXiv 2026-07 | 单 Agent/Agent prompt 内的有限 KV budget；Qwen2.5-1.5B/3B | semantic region、token/page | orchestrator 提供 system/plan/retrieval/tool/scratchpad region boundary；attention refresh | region-specific base priority/decay、pin、lowest-score page eviction | system token half-life 显著更长；约 450/1700 token context 上保持关键事实 | **语义分段新竞品。** 证明 orchestrator-known region 可影响 eviction，但属于 loss/quality-sensitive token eviction，不是 exact full-KV offload；实验规模小，不能直接替代 BeliefKV。
| [TokenDance](https://arxiv.org/abs/2604.03143), arXiv 2026 | 同步 round/all-gather MAS | sibling Agent context、master KV 与 diff blocks | round 边界和 collective communication structure | collective KV gathering、block-sparse diff storage/sharing | KV 压缩 11-17x；并发 Agent 2.7x；prefill 最高 1.9x | **特化但直接。** 已覆盖 sibling/collective context 的共享压缩；不适合异步 tool-driven workflow，也不解决 admission、offload 时机和 workflow fairness。
| [Agent Memory Below the Prompt](https://arxiv.org/abs/2603.04428), arXiv 2026 | Apple/edge device 多 Agent | 每 Agent 完整 KV | agent identity、持久 cache handle | Q4 KV 持久化到磁盘、直接 restore、cross-phase injection | Q4 容量约 4x；TTFT 最高 136x | **介质与压缩邻近。** 解决 edge RAM/disk persistence，不做并发 workflow 调度；可作为低层 exact/near-exact restore 对照，而非主策略竞品。

## 4. B 类：Workflow、Program 与计算资源调度系统

| 工作 | 核心抽象 | 核心机制 | 目标/部署 | 对 BeliefKV 的意义 |
|---|---|---|---|---|
| [Agentix/Autellix](https://arxiv.org/abs/2502.13965), NSDI 2026 | runtime-emergent program DAG、process table | 按累计 service/wait time 的 program-aware priority、preemption、data-locality load balance | 动态 Agent program；多 engine | **强调度基线。** 已覆盖无先验完整 DAG 的 program-level scheduling；不负责 KV tiering，但 BeliefKV 的 workflow fairness/admission 收益必须与其区分。
| [Nalar](https://arxiv.org/abs/2601.05109), arXiv 2026 | futures 串联的动态 Agent workflow、managed state | future 携带 dependency/context/creator/consumer/location；global policy + local event control；逻辑状态与物理 placement 解耦 | 动态 Python workflow；分布式 serving | **架构强重叠。** 已覆盖事件驱动 DAG、状态位置和 consumer metadata；若 BeliefKV 修改 Agent 接口暴露相同信息，RCCG/action frontier 本身不新。弱接口推断和单卡物理 KV 控制才可能形成差异。
| [SMetric](https://arxiv.org/abs/2607.08565), arXiv 2026-07 | Agent session、完整 response completion | 首轮负载均衡、后续轮 cache-aware routing；以 session TPS 为中心，弱化人类流式 TTFT/TPOT 权重 | 两份 Bailian 生产 coding-agent trace；大规模集群 | **目标函数关键证据。** 直接支持“Agent 通常等待完整响应才行动”，但不是说 TTFT/TPOT 可完全忽略；其集群 routing 不解决单 GPU HBM/PCIe，BeliefKV 应报告 workflow TPS/JCT 与 action-ready time。
| [AgentServe](https://arxiv.org/abs/2603.10342), arXiv 2026 | cold prefill、resume prefill、short decode | TPOT feedback 调整 resume-prefill budget 和 decode SM reservation；CUDA Green Context | 单消费级 GPU；3B-8B；3-6 concurrent agents | **单卡计算面强邻近。** 已覆盖 observation/resume-prefill admission 和 decode 隔离；不做 KV offload/prefetch。BeliefKV 应将其视为 compute-plane baseline，而非重复创新。
| [AgentServeSim](https://arxiv.org/abs/2606.09613), arXiv 2026 v3 | Program Control Block、因果 successor release、Retention/Dispatch Plane | trace-driven discrete-event simulation；组合 TTL/pressure-aware retention 与 FCFS/PLAS dispatch | vLLM 0.19；B200/RTX PRO 6000 校准；SWE-bench/BFCL | **评测方法强邻近。** 实机 JCT 误差约 5.5%；压力案例中 TTL+FCFS 反而恶化 JCT，而 pressure-aware TTL+PLAS 才改善，说明 retention-only 不足。可用于 P8 搜索和反例生成，不能替代真实物理实验。
| SAGA, HPDC 2026 | Agent Execution Graph、workflow-atomic unit | reuse-aware graph、session-affinity batching + work stealing、Agent Fair Share | 64-GPU cluster；SWE-bench/WebArena | 覆盖 workflow atomicity、KV continuity 和 completion-time fairness；集群方案，不直接解决单卡 PCIe/HBM，但 fairness 主张会与 BeliefKV 冲突。
| [HexAGenT](https://arxiv.org/abs/2605.16637), arXiv 2026 | online-revealed workflow DAG | standalone completion horizon、SLO miss risk；联合 prefill/decode placement 和 queue priority | 异构 A100/H100/H200 PD-disaggregated cluster | 覆盖在线 DAG、criticality 和 workflow SLO；BeliefKV 不能只以“在线展开 DAG + 关键路径调度”作为创新。
| Kairos, arXiv 2025 | 在线分析出的 workflow 与 agent remaining latency | workflow-aware priority + memory-aware dispatcher | public-cloud shared LLM；4xA40 | 覆盖动态 workflow 历史学习、remaining-latency priority 和 memory-aware dispatch；不是 KV residency，且依赖多实例调度。
| [Justitia](https://arxiv.org/abs/2510.17015), arXiv 2026 v2 | task-parallel Agent、memory-centric agent cost | 预测 fair completion order、virtual-time fair queue、selective pampering | shared GPU servers | **fairness 强基线。** 已覆盖 task-parallel Agent 的 completion fairness；BeliefKV 的 bounded-lag fairness 必须对比它或给出不适用边界。
| [GraphFlow](https://arxiv.org/abs/2605.22566), ICML 2026 | operation-level workflow graph | GNN/MLP 生成 task-specific subgraph；`KV_base + sparse DeltaKV`；rare-path recompute | dynamic workflow；近似 KV 表示 | 覆盖动态结构和 operation-level KV，但重点是近似 residual storage，不是 HBM residency/offload；质量与系统吞吐证据仍有限。
| HeraSys, ICML 2026 | concurrent workflow structure | 跨 workflow node merging/reuse、load-aware inter/intra-workflow scheduling | workflow serving | 覆盖结构复用与联合 scheduling；公开资料目前主要是官方摘要，原生复现可行性低。
| Helium, arXiv 2026 | workflow=query plan，LLM call=operator | proactive prompt/KV/output caching、cache-aware operator scheduling | batch/speculative agent workflows | 覆盖 query-plan 级复用和 workflow scheduling；更偏已知 DAG 与 batch workflow，不是 opaque online single-GPU lifecycle。
| [Halo](https://arxiv.org/abs/2509.02121), arXiv 2025 | consolidated query-plan DAG | adaptive batching、KV sharing/migration、CPU-GPU pipeline | batch Agent analytics/workflows | 已联合 DAG consolidation、跨任务 KV reuse 和 placement；适用已知批处理图。BeliefKV 不能把“workflow DAG + KV migration”作为独立主张。
| Ayo, ASPLOS 2025 | primitive-level dataflow graph | 跨 LLM/非 LLM primitive 的 parallelization、pipelining、two-tier scheduling | 端到端 LLM applications | 说明 tool/non-LLM overlap 已有成熟先例；不管理 KV residency，但“统一端到端联合调度”本身不是新贡献。
| Parrot, OSDI 2024 | Semantic Variable/dataflow | 应用暴露变量依赖、跨 request dataflow analysis 和 scheduling | public LLM service | 说明显式语义接口和跨调用数据流优化已有先例；BeliefKV 的 RCCG 价值必须来自动态弱元数据与物理 KV 联合控制。

## 5. C 类：局部机制与算法邻近工作

| 工作 | 机制 | 与 BeliefKV 的关系 |
|---|---|---|
| [RelayCaching](https://arxiv.org/abs/2603.13289), ICML 2026 | 下游 Agent 直接复用上游 decode KV；选择性重算偏差较大的 layer/token | 直接缓解 inter-agent observation prefill，但不是 residency/offload scheduler；如果 BeliefKV讨论 observation prefill，必须纳入。
| [KVCOMM](https://arxiv.org/abs/2510.12872), arXiv 2025 | 用 anchor pool 在线校正跨 context/position 的上游 KV，再供下游 Agent 复用 | 直接竞争 observation prefill；训练-free 且报告超过 70% KV reuse、prefill 最高 7.8x，但属于近似 cross-context reuse，必须单独验证质量，不能与 exact KV 策略混报。
| [KVShareArena](https://arxiv.org/abs/2609.10266), arXiv 2026-09 | 跨 prompt 位置、上下文、checkpoint 和多来源 Agent report 的 KV reuse benchmark | 最新机制评测基准；显示未经修复的 KV reuse 可能劣于不复用，简单 position correction 只适用于较简单情形。适合检验 BeliefKV 是否应保持 exact semantics。
| [LRAgent](https://arxiv.org/abs/2602.01053), ICML 2026 | 多 LoRA Agent 共享 base KV，维护低秩 adapter-dependent cache | 适用于角色由 LoRA 区分的 Agent；与同模型、prompt-role Agent 的通用生命周期正交。
| [ForkKV](https://arxiv.org/abs/2604.06370), arXiv 2026 | multi-LoRA Agent 的 CoW disaggregated KV、DualRadixTree 和 ResidualAttention | 对不同 adapter 的 Agent fork/share 高度相关，但依赖 multi-LoRA 表示结构；不能解释普通同模型 workflow 的 residency 收益。
| [EpiCache](https://arxiv.org/abs/2509.17396), ICML 2026 | block-wise prefill、episodic semantic compression、layer-wise budget | 单会话长上下文、lossy compression；不是并发 workflow 调度。
| [IntentKV](https://arxiv.org/abs/2606.09916), arXiv 2026 | 跨轮 QueryMemory、intent-aware KV pruning | 对长 session history 做 token-level lossy reduction；与 exact object residency 正交，但可能削弱 BeliefKV 的 HBM pressure 机会，必须作为组合/敏感性实验。
| [UltraQuant](https://arxiv.org/abs/2606.20474), arXiv 2026 v3 | context-heavy Agent 的 4-bit KV quantization | AMD CDNA4 上用 Claude Code trace replay 报告高于 BF16/FP8 的 qualified throughput；属于表示层和质量约束分支，可减少但不会消除调度压力。
| [ArborKV](https://arxiv.org/abs/2605.22106), ICML 2026 | Tree-of-Thought active branch/ancestor value、lazy rehydration | 可借鉴分支 survival/value，但目标是 tree reasoning，不是多 workflow 单卡 serving。
| Preble, ICLR 2025 | 分布式 prompt-sharing scheduler，联合 cache reuse 与负载均衡 | 强 prefix-locality baseline；没有 Agent workflow 语义，主要是多副本分布式。
| InfiniGen, OSDI 2024 | 基于 attention 的动态 KV prefetch/selection | 低层长上下文 KV 管理；可与 workflow policy 正交组合。
| [AMPD](https://arxiv.org/abs/2602.14516), ICML 2026 | 多轮请求在 PD-disaggregated serving 中路由 incremental prefill | 多节点 PD 场景；对 observation/resume prefill 有参考，非单卡 KV residency。
| [PPD](https://arxiv.org/abs/2603.13358), ICML 2026 | append-prefill 在 decode node 本地执行，避免 KV transfer | 多节点 PD placement，证明不同 prefill 类型应区别对待；非单卡。
| [Agent-Omit](https://arxiv.org/abs/2602.04284), ICML 2026 | RL 训练 Agent 省略冗余 thought/observation | 改变模型和语义；若 BeliefKV 保持 exact prompt，则正交。不能将 observation 丢弃作为系统独占创新。
| [BPOP](https://arxiv.org/abs/2602.02806), ICML 2026 | 从多条 noisy traces 用 Bayesian/MCMC 推断 latent partial order | 可作为离线 workflow structure learner；成本高、同类 workflow 假设强，不是在线 KV scheduler。
| [Agent JIT Compilation](https://arxiv.org/abs/2605.21470), ICML 2026 | 用 latency distribution 和 Monte Carlo 选择 serial/parallel/hedge plan | 支持使用分布而非点估计，但它优化 agent plan，不管理 KV。
| [PASTE](https://arxiv.org/abs/2603.18897), arXiv 2026 | 预测未来 tool call、在模型生成期间投机执行，再联合调度返回 session | 直接覆盖 tool/LLM overlap，报告 E2E task time 降低 43.5%；不管理 KV residency，但使“预测工具并提前执行”不能成为 BeliefKV 主创新。
| MAPS, ICML 2026 | uncertainty-calibrated output-length upper bound 和 memory-aware placement | 可借鉴校准风险界，不是 Agent workflow 系统。
| [AgentSysBench](https://arxiv.org/abs/2608.15127), arXiv 2026-08 | 10 个 Agent 应用、4,641 requests、64,924 LLM calls、118,274 tool calls 和 178,799 production sessions 的全栈 characterization | 发现 5/10 应用由 non-LLM 组件主导、sandbox 可达 28GB、状态可能闲置数分钟到数小时；证明 KV 优化必须报告全栈贡献，适合作为 workload/trace 设计依据。
| [Agentic AI Workload Characteristics](https://arxiv.org/abs/2605.26297), arXiv 2026 | Agent 阶段、prefix caching 和 token composition characterization | 发现 context caching 可使部分 Agent run 转为 decode-dominated，工具类型从前期 read/explore 向后期 execute/write 迁移；支持 phase-conditioned workload，但不是管理算法。
| Characterizing Agents in Production, ICML 2026 Spotlight | 20 case studies + 306 practitioners 的生产 Agent characterization | 支持弱元数据、短 step 和可靠性需求，但不是 serving 方法；用于 workload motivation。

### 5.1 2026-09 增量论文的证据强度

| 工作 | 证据与复现状态 | 当前使用方式 |
|---|---|---|
| Continuum | arXiv v7；公开 [vLLM artifact](https://github.com/Hanchenli/vllm-continuum)；含真实 Agent benchmark、单卡和多卡 | 优先原生复现，作为 tool-gap lifecycle + program scheduling 强 baseline |
| CacheScout | 完整算法与实机多 workload 实验；截至检索日未发现公开代码 | 忠实 same-data-plane 实现；所有作者结果标记为 paper-reported |
| TOPAS | 8 页 arXiv 稿；单 A100/SGLang 实验；截至检索日未发现公开代码 | 固定 DAG 子集实现 post-decision joint search；不外推到 opaque workflow |
| SMetric | 两份大规模生产 coding-agent trace，外部真实性强；代码/trace 声明将在发表后公开 | 用于目标函数和 workload 证据，目前不能作为可复现 native baseline |
| Astraea | 实机实验，但 service prediction 使用生成长度 oracle，并人工放大长 API 比例；未发现公开代码 | 只做机制 reference/oracle，不直接采信跨 workload 的绝对收益 |
| AgentServeSim | 用真实 vLLM/GPU 校准并报告仿真误差；未发现公开 repo | 用于策略组合筛选与反例，不代替端到端实机结果 |
| AgentSysBench | 覆盖调用数和系统组件较广；论文称将开放代码，但截至检索日未找到链接 | 用于补足全栈指标与 workload taxonomy，不作为调度 baseline |

## 6. P8 延后对比的推荐分层

以下分层只在 BeliefKV P5/P6 功能、workload 和配置冻结后实施。当前阶段只需持续记录中立
runtime/physical trace；不要求运行本节的 B1-B4 或 native system。

### 6.1 L1：Hindsight/Oracle replay

在同一冻结 trace 上给竞品其最有利的未来信息，用于判断 BeliefKV 是否存在独立上界，而不是测试预测器精度：

| ID | Oracle/reference policy | 提供的信息 | 需要回答的问题 |
|---|---|---|---|
| O0 | Full-future joint oracle | 完整未来 request、tool、page demand、实际 DMA/compute cost | BeliefKV 动作空间的理论机会有多大？ |
| O1 | KVFlow/ScaleSim oracle | 真实 next invocation distance/steps | action frontier 是否优于完美 future-distance ranking？ |
| O2 | AugServe oracle | 真实 output length、tool duration、return length | action-unlock 是否独立于 shortest-job/space-time value density？ |
| O3 | ThunderAgent oracle | 完美 Program/Reasoning/Acting phase | 弱元数据推断是否比显式 phase 提供额外价值？ |
| O4 | PBKV oracle | 真实未来 K-step Agent 序列 | predictor 之外的 JointPlan、物理 ownership 和可逆动作是否有价值？ |
| O5 | CacheWise oracle | 真实 coding tool idle/reuse interval | coding workload 收益是否只是工具 metadata predictor？ |
| O6 | PACE oracle | 最优 active-workflow window | 复杂 KV 动作是否优于只控制并发工作集？ |
| O7 | TOPAS oracle | 已知共同 DAG、真实 residual service/critical path 和 movement cost | 联合 residency+admission 是否只是已知图上的 longest-path utility？ |
| O8 | Continuum oracle | 真实 tool gap、queue delay、reload/recompute cost | lifecycle 收益是否只是最优 TTL + program FCFS？ |

若 full BeliefKV 只优于 reactive LRU，却不能优于 O1-O8 中对应的 strongest policy，则不能主张新的算法边界。

### 6.2 L2：Same-data-plane reference policies

所有策略必须经过同一个 `PolicyInput -> JointPlan -> SGLang/HiCache` 执行路径：

| P8 顺序 | Reference policy | 项目内实现状态/限制 |
|---|---|---|
| Core | SGLang LRU + HiCache write-back/write-through/selective + B0 | 当前 correctness 与数据面 baseline |
| First | CONCUR-style congestion admission | 只需 observed feedback；后期最适合在线同数据面对照 |
| First | CacheScout online transition policy | 从 prefix fingerprint 推断 Agent；严格复现一阶 transition、survival eviction 和 predictability-gated warmup |
| First | Continuum TTL + Program-FCFS | 从公开实现提取 policy；在同一 trace 学习 tool-specific TTL，计入 queueing/out-of-order delay |
| Conditional | KVFlow/ScaleSim distance policy | 仅在真实提供 distance 的子集在线；否则为 hindsight oracle |
| Conditional | TOPAS post-decision joint policy | 仅在 workload 原生提供共同 DAG/Agent mapping 时在线；动态 workload 只设 oracle，不补造图 |
| Conditional | AugServe space-time policy | 需复现 predictor 才称在线；否则为 hindsight oracle |
| Conditional | ThunderAgent phase/decay policy | 仅使用 workload 原生提供的 program/phase metadata |
| Conditional | PBKV multi-step prediction policy | 代码/输入可得后再实现；另设 hindsight variant |
| Conditional | CacheWise tool-metadata policy | 使用相同 tool metadata；coding trace 单独报告 |
| Secondary | AgentServe resume-prefill admission | compute-plane ablation，不与 KV residency 混为一个收益 |
| Separate | MemDecay region eviction | lossy/token dropping 实验组，与 exact-semantics 主表分开 |

### 6.3 L3：Native end-to-end systems

原生部署按可复现性和与当前硬件的匹配程度排序：

1. SGLang/HiCache：当前底座和强 baseline。
2. Continuum：公开 vLLM artifact，优先复现单卡 tool-gap TTL 与 Program-FCFS；数据面不同的影响单独消融。
3. KVFlow：代码公开、SGLang 路径已掌握；在其能表达的 common-denominator workflow 上优先
   尝试原生对比，不要求其直接执行完整动态 nested workflow。
4. ThunderAgent：代码公开，支持 vLLM/SGLang；单 GPU 只比较本地 program-aware scheduler，不夸大全局队列收益。
5. Agentix：使用正式 NSDI 实现或作者配置，主要比较 program scheduling/JCT。
6. CacheScout、TOPAS：先做忠实 same-data-plane policy；截至检索日无公开代码，不能声称原生系统胜负。
7. PBKV、CacheWise：优先寻找作者代码；若未公开，先做忠实 reference policy，并明确不能声称原生系统胜负。
8. AgentServe：llama.cpp + Green Context 数据面不同，先隔离 phase policy，再视硬件/代码可用性部署。
9. TokenCake、ScaleSim、AugServe：无可运行原生实现时，保留 reference + hindsight 上界，不伪造系统级复现。

## 7. 指标映射

| 研究问题 | 主指标 | 必须同时报告的诊断指标 |
|---|---|---|
| 单卡 workflow 效率 | workflow JCT p50/p95/p99、completed workflows/min | request TTFT、step latency、time-to-tool-start、time-to-valid-action |
| KV residency | HBM peak/area、cache hit、recompute tokens/time | useful/wasted D2H/H2D bytes、GPU/CPU residency timeline |
| PCIe 调度 | urgent restore stall、copy queue delay | overlap ratio、cancel/retry bytes、inference slowdown during DMA |
| 动态预测 | top-k owner recall、time-to-next-use calibration | ECE/Brier、coverage、OOD rate、prediction-to-action conversion |
| Workflow fairness | slowdown、bounded lag、starvation count | per-root service/JCT、fan-out amplification |
| Action criticality | action-unlock time、critical decode completion | 与 true-distance、true-length、SRPT/value-density oracle 的 gap |
| Exactness | task success、输出一致性 | lossy policy 单独报告 quality/accuracy，不与 exact policies 混合 |

## 8. 最终论文主张的 Go/No-Go 条件

以下条件在 P8 判断最终论文主张，不阻塞 P4-P6 的系统实现、正确性验证和内部 ablation：

1. CacheScout-style online transition、PBKV-style K-step future-agent oracle、TOPAS known-DAG oracle 与 Continuum tool-gap oracle 均不能解释全部收益。
2. 在至少三类真实 workload（coding、search/research、动态 peer/subagent）上，事件驱动 causal baseline 与 full-future oracle 存在稳定、显著的 gap；同时按 AgentSysBench 方法报告 non-LLM 占比，避免挑选 KV 主导的样本。
3. 在线策略实现 oracle gap 的显著部分，并在 OOD 时不劣于 strongest reactive/phase baseline。
4. Joint execution+KV policy 相比 CacheScout eviction/prefetch + TOPAS admission/residency + Continuum TTL/program scheduling 的最强忠实组合仍有收益。
5. 收益来自单卡 HBM/PCIe/prefill contention，而不是旧 SGLang 版本、retry storm、错误 admission 或 baseline 配置缺陷。
6. Action-unlock objective 相对 invocation distance、SRPT/space-time value density 和 TPOT protection 有独立解释力。
7. Metadata-free/weak-metadata 必须量化接口成本和识别误差；若必须提供完整 program/phase/graph，则与 ThunderAgent、KVFlow、ScaleSim 的差异显著收窄。
8. Per-workflow causal readiness 必须在 conditional history 决定 next action 的 workload 上，稳定优于 CacheScout 的 workload-global first-order transition；否则 variable-order/context-tree 只是更复杂的 predictor。
9. 物理动作收益必须在实测 D2H/H2D 带宽、共享 copy-engine 排队、异步 overlap、取消和错误预取浪费都计入后仍成立；TOPAS 已计入 movement cost，静态 PCIe 分数不是创新。

若条件 1、2、4 或 8 不成立，应停止将复杂 predictor 作为核心贡献，保留经过修复的 reactive JointPlan 作为工程系统，并转向更明确的 failure，例如 workflow fairness、PCIe admission stability 或 exact object-level ownership。

## 9. 维护规则

新增论文时必须记录：

- 版本和检索日期；
- venue 是否已正式接收，避免把 arXiv 当成正式发表；
- 在线需要的真实 metadata；
- 论文动作空间与 BeliefKV 动作空间的交集；
- 是否 exact semantics；
- 是否有公开代码、数据集和可运行硬件；
- 应进入 L1、L2 还是 L3，而不是只加入文字 related work。

本表是研究决策文档，不是性能结论。未部署的系统只能描述论文报告结果，不能写成 BeliefKV 已完成的实测对比。
