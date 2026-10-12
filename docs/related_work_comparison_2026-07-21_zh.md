# BeliefKV 相关工作与竞品对比总表

更新日期：2026-10-12
用途：研究定位、reference policy 设计、原生系统部署和论文 baseline 选择。
范围：合并此前整理的 Agent serving、workflow scheduling 和 KV-cache 工作，并加入截至本日期检索到的最新直接竞品。2026-09-14 增量检索重点覆盖 arXiv 2026 年 7-9 月的新稿与旧表漏项。

2026-10-12 更新重点是第 6.4 节的可执行对照方案，以及 Continuum、
KVFlow 和 TokenCake 的公开代码核验。其余论文状态主要保留 9 月调研
记录，不能据此声称已完成当前 Qwen3.5 / SGLang 0.5.20 上的复现。

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
| Continuum | arXiv v7；公开 [vLLM artifact](https://github.com/Hanchenli/vllm-continuum)，但 README 明确不含论文 estimation；包含 SWE-bench 运行说明 | 区分 public-artifact 与补全估计器后的 paper reimplementation；不能直接把仓库默认策略称为完整论文复现 |
| KVFlow / ScaleSim | [官方仓库](https://github.com/PanZaifeng/KVFlow)可访问；当前仓库同时含两项工作，须锁定 commit 与实际启用机制 | 从官方实现提取对应 eviction/prefetch；原生和统一底座适配分别命名，不把 ScaleSim 的额外功能归入 KVFlow |
| TokenCake | 最新 arXiv v5（2026-09-28）声明 [公开代码](https://github.com/pkulemonade/TokenCake)，但 2026-10-12 页面和 GitHub API 均返回 404 | 先按论文核对算法；代码可用性待核实，不能宣称已获得可运行官方 artifact |
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

策略对照应使用相同 SGLang/HiCache 物理执行接口，各策略独立输出决策；
不能强制竞品经过 BeliefKV 的 JointPlan、预测触发、优先级或额外门槛。
共同部分限定为真实容量、页/状态有效性、引用和传输依赖等正确性契约，
并保留独立 native 对照测量适配层成本。当前原生路径的具体实施见第 6.4 节。

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
2. Continuum：公开 vLLM artifact，优先复现单卡 tool-gap TTL 与 Program-FCFS；公开仓库未包含论文估计器，需单列该差异，数据面不同的影响单独消融。
3. KVFlow：代码公开、SGLang 路径已掌握；在其能表达的 common-denominator workflow 上优先
   尝试原生对比，不要求其直接执行完整动态 nested workflow。
4. ThunderAgent：代码公开，支持 vLLM/SGLang；单 GPU 只比较本地 program-aware scheduler，不夸大全局队列收益。
5. Agentix：使用正式 NSDI 实现或作者配置，主要比较 program scheduling/JCT。
6. CacheScout、TOPAS：先做忠实 same-data-plane policy；截至检索日无公开代码，不能声称原生系统胜负。
7. PBKV、CacheWise：优先寻找作者代码；若未公开，先做忠实 reference policy，并明确不能声称原生系统胜负。
8. AgentServe：llama.cpp + Green Context 数据面不同，先隔离 phase policy，再视硬件/代码可用性部署。
9. TokenCake、ScaleSim、AugServe：逐项核对可运行原生实现。ScaleSim 已在 KVFlow 官方仓库公开；TokenCake 最新论文给出的仓库地址本次返回 404。未取得代码时仅保留明确命名的 paper reimplementation/reference，不伪造系统级复现。

### 6.4 当前 baseline 复现与 BeliefKV 对照方案（2026-10-12）

本节是后续实施方案。本轮只维护文档；不将旧 P6 的 baseline manifest
或历史部署视为当前模型、引擎和 workload 的复现，也不改变正在采集的
V32 运行源。当前相对 native 的吞吐/JCT 优化目标继续保持。

#### 6.4.1 分开回答策略效果和完整系统效果

| 对照层 | 固定什么 | 可以改变什么 | 结果名称与解释 |
|---|---|---|---|
| 主表：统一底座策略 | GPU/CPU/NUMA、模型权重与精度、SGLang 版本、实际池容量、harness、任务和到达流 | 论文原有 eviction、保留/换出、prefetch、准入与并发控制决策 | `method-adapted-sglang`；说明策略在同一执行底座的效果 |
| 补充：作者原实现 | 尽量统一硬件、模型、任务和工具环境；独立隔离安装作者依赖 | 作者引擎、数据结构、kernel 与原有控制路径 | `method-original-stack`；完整系统对照，不能把底座差异全归为算法 |
| 复现核对 | 作者支持的模型和具有对应机制的 workload | 原实现开关及论文配置 | 检查机制与趋势；不要求重现不同硬件上的绝对倍数 |

每个原实现同时运行该引擎自身的 native 对照，报告其相对本底的改善。
该改善不能直接换算为对当前 SGLang 的加速；原生系统间的绝对性能
另表报告，说明版本、容量和功能差异。论文报告数字不进入实测主表。

统一适配只共用物理动作执行器和必要正确性检查，不共用 BeliefKV
的预测头、latest-start 选择、restore-ready 排序或预取收益判断。
baseline 本身提出的动作也要计入其规划、代理、传输及保护成本。
原生缓存有效性和数据依赖继续由原生 allocator/cache 管理。

#### 6.4.2 候选与优先级

| 顺序 | 候选 | 要检验的问题 | 复现边界 |
|---|---|---|---|
| 第一批 | Native SGLang/HiCache + reactive common-path | 相对原生是否有净收益；共同事件/传输适配成本有多大 | native 不运行 BeliefKV 策略；common-path 只接入共同接口，独立标出开销 |
| 第一批 | CONCUR | 只控制活跃并发是否已足以解释收益 | 按论文拥塞反馈实现，保留同一外部到达流；排队等待计入 JCT |
| 第一批 | ThunderAgent | program/phase 工作集管理是否足够 | 官方代码支持 SGLang；先做单 GPU 本地策略，保留代理成本，不引用集群收益 |
| 第一批 | CacheScout | 无预定义图的在线转移学习是否已能有效 eviction/prefetch | 按论文实现在线转移、缓存价值与预取；本次未核实可运行官方 artifact，标为 paper reimplementation |
| 第二批 | KVFlow | 已知依赖/Step Graph 的距离驱逐和预取有多强 | 官方仓库可访问；保留固定 prompt/shared-prefix 语义，不强行扩成整 agent 传输 |
| 第二批 | Continuum | 工具 TTL + program FCFS 是否已能解释工具等待收益 | 公开 artifact 与补全论文估计器的版本分列；估计器使用统一离线训练划分 |
| 第二批 | PBKV | 历史+当前上下文预测未来 agent 调用，相比我们的事件/内容信号如何 | 按论文保留多步调用预测及保守缓存策略；未核实官方 artifact，不用简化 Markov 策略冒充 |
| 第二批 | TokenCake | 工具预测上传 + 图/运行状态驱动空间预留，是否覆盖我们的组合 | 先核对 v5 的图和预测输入；官方链接本次 404，先明确 paper reimplementation 范围 |
| 条件扩展 | TOPAS / ScaleSim / Agentix | joint residency-admission、显式距离及 program 调度的额外解释力 | 按真实输入与单卡适用性增加；不先把全部相关工作都移植 |

第一批加 BeliefKV 构成六个系统/策略条件。第二批完成后再扩大主表，
避免只和 native 比较，也避免同时移植所有框架拖慢开发。可组合的强策略
另设 `composed-reference`，在开发集选择组合，不能冠以任一论文原系统名。

#### 6.4.3 模型与推理框架

主表优先保留当前 Qwen3.5-35B-A3B BF16、SGLang 0.5.20。
各条件锁定权重/tokenizer/chat template、采样、context/completion
预算、chunked prefill、运行请求上限及实际 FULL/Mamba 池容量。
共享容量以 allocator 普查的可用 token/slot/bytes 为准，不能只比较
`mem_fraction_static` 或 max-running 数字。原方法的内部保留/共享
分区属于策略，可以在相同总容量内调整。

不支持混合模型的论文方法，统一底座适配保留其 FULL 决策，对必要
recurrent state 采用同一原生有效性/恢复处理，并明确这项扩展。
不删除状态依赖、不只给 BeliefKV 额外可用容量；Mamba 字节、恢复
和重算单列。若该方法核心假设无法这样保持，则不放入混合模型主表。

原实现兼容性不足时，另选共同支持的纯 Attention 模型；优先核对
Qwen2.5-32B-Instruct BF16，再确定共同可运行的 context 配置。
BeliefKV 也运行相同模型、精度和 workload，双方一起换模型。
两套模型分别成表，不将原实现的小模型结果与 BeliefKV 大模型结果
直接相除，也不把两套结果合成一个性能倍数。

#### 6.4.4 动态 workflow 和已知图分别比较

| 实验组 | 信息规则 | 方法与结果边界 |
|---|---|---|
| 动态主组 | 多轮 spawn、每轮 2-4 child、真实工具；只暴露截至当前的 spawn/RETURN/JOIN、工具和 decode/通知事件 | native、CONCUR、ThunderAgent、CacheScout、Continuum、PBKV 与 BeliefKV；仅在对应实现完成后纳入 |
| 已知图组 | 固定角色和调用结构，包含分支/汇合；所有方法可读取相同预声明图 | KVFlow、TokenCake、TOPAS 等在输入假设成立时比较；BeliefKV 也得到图，仍不读取未来实际时长 |
| 在线图适配组 | 只提供已经展开的 parent/child 和当前 JOIN 成员；未知未来节点不补造 | `KVFlow-observed` 等明确作为扩展，不能冒充原论文标准实现 |
| 诊断 oracle | 显式标记 hindsight 信息，只用于机会分析 | 不作为可部署 baseline，也不是实际端到端收益上界的自动证明 |

KVFlow 的 Step Graph 可以表达条件分支和同步，不能简单描述成只支持
固定线性链；它所需要的预声明图/距离与动态新建 child 的在线图仍有差别。
不因方法需要图就把 hindsight 的完整动态轨迹偷偷提供给它，也不在
不满足输入条件的任务上把崩溃或回退称为算法性能落后。

已知图组优先使用作者公开的代表性应用/生成规则，并保留固定角色前缀
的复用机会；工具主导方法同时在共同的单 agent 多轮工具应用上比较。
每一组内所有方法执行相同应用，另列静态前缀与增长 suffix 的 token
占比。不能只在我们刻意增加多轮 spawn 的 workload 上给出总排名，
也不能为了复现固定角色前缀策略，把每个新建 child 强行视为从未出现
过的角色。角色/前缀识别按原方法执行，识别失败率单列。

所有条件使用相同 harness、prompt、工具 schema、通知机制和初始任务。
`announce_completion_intent` 等可观察信号对全部方法开放；增加通知
造成的生成/调用成本属于共同 harness 成本。利用信号产生的服务优先级、
预取和驻留策略是各方法的决策，另用开关消融，不能隐藏在公共 executor。
对通知关闭的敏感性只在后续冻结的独立组中比较，不能中途改变采集配置。

#### 6.4.5 workload、调参与统计

开发主候选沿用当前 108+48、第二波 +3600 秒、running=48、Host
200 GB FULL:Mamba=80:20、HBM recurrent/FULL ratio=0.9、
context=131072、completion=8192、工具 180 秒、workflow 14400 秒。
这是现有开发配置，不证明它是最优压力档。另选择一个有实测迁移机会的
中压力档，以及低压力检查；依照驱逐、需求恢复、重算和可回收容量分类，
不只按 root 数命名。工具/CPU/sandbox 限额及外部到达流对所有方法一致。
并发控制 baseline 可以改变内部活跃数量，其排队时间仍从外部到达计时。

当前 156 个任务反复参与开发，不作为未见测试。正式比较重新冻结
未参与模型拟合、校准及策略调参的任务，优先项目隔离；训练、校准、
评测划分及所有方法可见 trace 一并登记。需要学习的论文方法在同一
离线划分重训，保留自身预测目标，不要求其学习动作净收益。
每项策略含 native 获得相同的开发调参预算，允许 native 的 write
policy 等正常调优，主配置和各自调优结果分列，评测集不参与选参数。

开发仍先运行 predictive 或新适配条件，在输入/底座未变时复用固定
历史 native；只作迭代筛选。正式对照按当前四轮重复目标进行：每轮
共享任务、到达流和 seed 块，均衡轮换各方法运行顺序，冷启动同等状态。
统计每轮结果、均值、波动及 run/block 层面的置信区间；不能把同一轮
上千个 request 当成独立重复。同 seed 不保证动态模型轨迹相同，不用
固定需求回放替代真实 workflow，也不选择事后最有利的轨迹。
四轮不足以支持稳定结论时报告不确定性；增加重复须对比较组一致实施。

原生/策略/工作负载三者配置不变时开发可复用 native；原生执行路径、
模型、容量、harness、工具配置或 workload 改变后刷新本底。
正式运行同时限制本地开发、归档和评测的 CPU/磁盘干扰，记录重叠维护。
目前不是启动上述对照；第二批兼容性失败也不阻塞第一批完成。

#### 6.4.6 复现检查、指标和交付物

每个方法登记论文版本、官方/重实现来源、源码 commit、算法到代码
对应表、metadata 接口、FULL/Mamba 扩展、参数和不支持项。
先用相同的短完整 workload 检查其实际决策与论文示例、容量及恢复行为，
再运行完整开发 workload；不采用 canary 限制正常调度动作。
只有固定源、功能和输入假设成立的版本进入正式四轮比较；
不能把关闭 offload、缺少预测器或静默回退的运行标为完整复现。

主结果报告全部到达任务的 workflow 完成吞吐、质量合格吞吐、JCT
均值/分位数及公平性。harness `completed` 与 SWE-bench `resolved`
分别报告，输出提交不等于修复正确。保留 error、incomplete、
未完成和行政截断，不仅统计两侧都已完成的任务。
繁忙窗口按预先冻结的时间段报告 decode 吞吐、GPU service 与 batch；
工具长尾、GPU 空转和模型轨迹差异另列，不能从汇总中悄悄删去。

机制解释至少包含：需求 H2D 与恢复阻塞、重算、HBM/Host 驱逐、
控制面同步/异步成本、ACK 到首次服务及重复加载；按独立 JOIN/tool
事件区分可恢复 FULL、完成前已提交 FULL、下一请求确认消费 FULL。
Mamba、需求 handoff 和仅 ACK 未证实消费的字节分开，不能用迁移字节
或 ACK 数量替代吞吐收益。BeliefKV 分别去掉 PREPARE、JOIN/tool
预测预取及恢复就绪优先级，解释相对 strongest baseline 的增量来源。

交付物沿用现有冻结计划、native 容量普查、对照分析和 timeline HTML
工具，但当前 arm 名与策略开关只支持有限条件，实施时需显式扩展。
旧 `predictive_joint_baseline.py`/P6 manifest 使用旧 SGLang 和模型，
仅复用校验设计，不能直接拿来启动当前竞品。
实现时分别提交每项适配及其短运行证据，再冻结正式比较矩阵；
复用模型文件和只读数据，按独立环境/工作树安装作者依赖，避免复制
整个 workspace 或保留多份重复 raw。按实际开发整轮时长估算 GPU
预算，不承诺未经运行核实的完成日期。

本次核验的一手资料：

- KVFlow paper 与 [官方 KVFlow/ScaleSim 仓库](https://github.com/PanZaifeng/KVFlow)：
  [arXiv:2507.07400](https://arxiv.org/abs/2507.07400)。
- ThunderAgent [官方仓库](https://github.com/ThunderAgent-org/ThunderAgent)：
  明确提供 `program_id` 接入及 SGLang/vLLM 支持。
- CONCUR：[arXiv:2601.22705](https://arxiv.org/abs/2601.22705)。
- Continuum：[arXiv:2511.02230v7](https://arxiv.org/abs/2511.02230v7) 与
  [public artifact README](https://github.com/Hanchenli/vllm-continuum)；
  README 明确缺少 paper estimation。
- CacheScout：[arXiv:2608.14624v1](https://arxiv.org/abs/2608.14624v1)；
  PBKV：[arXiv:2605.06472v1](https://arxiv.org/abs/2605.06472v1)。
- TokenCake：[arXiv:2510.18586v5](https://arxiv.org/abs/2510.18586v5)；
  论文所列仓库 `pkulemonade/TokenCake` 本次页面/API 均返回 404，
  这只说明本次不能取得公开 artifact，不断言作者不存在代码。

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
