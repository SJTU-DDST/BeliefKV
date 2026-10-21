# BeliefKV 相关工作与竞品对比总表

更新日期：2026-10-21
用途：研究定位、reference policy 设计、原生系统部署和论文 baseline 选择。
范围：合并此前整理的 Agent serving、workflow scheduling 和 KV-cache 工作，并加入截至本日期检索到的最新直接竞品。2026-09-14 增量检索重点覆盖 arXiv 2026 年 7-9 月的新稿与旧表漏项。

当前工程对照入口为 `scripts/run_fresh_internal_comparison.py`：北京时间
2026-10-22 06:00 前只做 CPU 等待；之后核对资格、冻结版本、暂停标记和
空闲资源再运行新 batch8 Full/JOIN-only/工程对齐 native。V2618 的 3537 项
CPU 用例全部通过、无跳过，V2620 绑定源码；这不构成 GPU 性能验收。
原始非零退出码、guard 排除和动态工作量单列；不重写此前 V111 失败状态。
本文竞品调查的历史证据边界保持原样，本次未重新检索外部论文或适配新竞品。

2026-10-21 更新重点是第 6.5-6.6 节的 baseline 筛选、调度消融和适配顺序，重新核对
论文、作者仓库及公开实现的缺项。TokenCake 官方地址已修正并可访问；
其 README 明确预测预取尚未实现。Continuum 公开预览版仍不含论文
估计器。补充 Ask the Tool 的工具进度信号对照，修正 InferCept 的会议
为 ICML 2024。本次继续补入 Pythia，明确 `JOIN-priority-only` 与
`OurScheduler-only` 的区别，并记录近期 PackServe/Lachesis 的适用边界。
第 6.4 节保留 10 月 12 日方案；候选见 6.5，本次实施顺序和消融以 6.6 为准。
其余未重新核验条目保留历史调研日期，不能据此声称已完成当前
Qwen3.5 / SGLang 0.5.20 上的复现。

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
2026-10-21 的后续候选见第 6.5 节；本次请求的调度消融与实施顺序见第 6.6 节。
这些是后续计划，尚不是已完成的适配。

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
| [InferCept](https://proceedings.mlr.press/v235/abhyankar24a.html), ICML 2024 | tool-interrupted augmented LLM serving | intercepted request/context | context length、预计工具执行时间 | Preserve、Swap、Discard | 减少重算与等待期 HBM 浪费 | **高重叠。** 已覆盖工具等待期间 KV 生命周期选择；[作者代码](https://github.com/WukLab/InferCept)公开，原实现基于旧 vLLM，混合模型兼容性需另核对。
| [Continuum](https://arxiv.org/abs/2511.02230), arXiv 2026 v7 | 多轮 Agent program；单 A100/B200 及多 GPU；vLLM | program、tool gap、KV state | 每类工具 finish-within-TTL 分布、KV 大小、reload/recompute、排队和乱序延迟 | 每工具 KV TTL、到期回收、deadlock breaking；与 program-level FCFS 联动 | 更新稿报告平均 JCT 改善超过 8x | **最高优先级且代码公开。** 已覆盖 tool-wait KV 生命周期与 program scheduling 的联动；未做未知 action readiness、object-level joint search 或共享 ownership。论文标题/正文中系统拼写存在 `Continuum/Continnum` 差异，引用时以正式标题为准。
| [Astraea](https://arxiv.org/abs/2512.14142), arXiv 2025 | 多状态 Agent request；单/双 A100；vLLM | session lifecycle、MLFQ queue、KV context | prefill profile、API 类别均值、生成长度 oracle | token-cost demotion、I/O promotion、HRRN+aging；压力下 Preserve/Swap/Discard min-waste | 报告平均 JCT 最高降低 25.5% | **概念高重叠、证据需谨慎。** 生命周期调度和三态 KV policy 已覆盖；结果依赖 generation-length oracle 和重加权 API latency，且未发现公开代码。
| [KVFlow](https://openreview.net/pdf?id=5Iw1nDtYmT), NeurIPS 2025 | 固定或可抽象的多 Agent workflow；SGLang hierarchical radix cache | Agent Step Graph、agent 和 KV node | workflow 执行图、steps-to-execution | node-level eviction、CPU->GPU next-step prefetch、共享 prefix 最小距离 | 单 workflow 最高 1.83x，多 workflow 最高 2.19x | **最高优先级。** 覆盖图感知 future-use eviction/prefetch；对在线展开、循环、未知 consumer 和弱元数据支持有限。代码已公开并已在本地复现。
| [TokenCake](https://arxiv.org/abs/2510.18586v5), 2026-09-28 修订稿 | function-call-heavy MAS；coding/deep research | workflow graph、critical-path agent | workflow 结构、criticality、工具事件和返回预测 | 论文：Temporal offload/predictive upload；Spatial reserved/shared pool | 论文报告 E2E latency 降低 47.06%，有效 GPU 利用率提升 16.9 个百分点 | **最高优先级。** [官方仓库](https://github.com/pku-lemonade/TokenCake)已公开；README 标明 vLLM v0.22.0、事件驱动备份与需求恢复，预测预取仍在开发。公开实现和完整论文策略必须分列。
| [Pythia](https://arxiv.org/abs/2604.25899v2), 2026-05-14 修订稿 | 多 Agent serving；节点缓存与集群调度 | workflow/role 注解、历史执行模式和 prompt composition | 用 profiling 合成执行模式，估计后继、输出长度及可构造的 prompt | Belady-like 替换、提前构造/计算后继缓存、lookahead scheduling，另有集群 autoscaling | 同时优化吞吐和 workflow JCT | **联合设计的重要对照。** 不只是预声明 DAG；支持从历史模式表达动态执行。当前须核实其 profiling/prompt 输入能否真实取得，单卡只适配缓存和本地排序，不把集群伸缩收益移入；本次未核实官方可运行 artifact。
| [PBKV](https://arxiv.org/abs/2605.06472), arXiv 2026 | 动态 Agent workflow | workflow 内未来多步 Agent invocation、cache entry | 历史 workflow + 当前 task context；预测未来数步 Agent | prediction-based reuse score、保守 eviction 和 prefetch | 动态 workflow 相对 LRU 最高 1.85x；静态 workflow 相对 KVFlow 最高 1.26x | **当前最直接的预测竞品。** 已覆盖“预测 next agents/多步 continuation 再管理 KV”；BeliefKV 的 context tree/Markov predictor 本身不再新颖。必须比较弱元数据、校准/OOD、action-criticality 和联合物理执行。
| [CacheScout](https://arxiv.org/abs/2608.14624), arXiv 2026-07 | 无预定义 DAG 的动态 MAS；vLLM 0.11；单机多卡实验 | Agent prefix anchor、在线 Agent transition | 从 prompt-prefix block hash 推断 Agent identity；无需框架标识；在线一阶转移矩阵 | survival-aware eviction、CPU offload、基于可预测度门控的后台 top-1 anchor warmup | cache hit +10-18 pp；mean TTFT -18%-45%；峰值吞吐 +19%-57% | **对原预测路线构成直接否定。** 已覆盖“无图、无接口、在线 Markov + eviction/prefetch”；其局限是一阶 workload-global 转移、偏静态 Agent anchor，未联合 task JCT/fairness、session dynamic suffix 和 execution admission。未发现公开代码。
| [TOPAS](https://arxiv.org/abs/2608.25523), arXiv 2026-08 | 单 A100 80GB；SGLang 0.5.3；已知共同 DAG | post-decision resident-prefix set + admitted-request set | task DAG、Agent 映射、剩余 stage/service 估计、prefix movement cost | 小 Agent 池枚举；大池 greedy add/pair/drop/swap；联合 HBM residency、CPU/GPU movement、admission 和 aging | synthetic mean/p99 JCT 最高 -39.8%/-49.4%；MetaGPT mean -9.8%/-22% | **对 JointPlan 构成最直接竞争。** 已联合最长剩余路径、下游复用、movement、preemption 和公平性；缺口是所有 task 共用已知 DAG，未处理 opaque dynamic workflow、真实异步 PCIe contention/shared ownership。未发现公开代码。
| [CacheWise](https://arxiv.org/abs/2606.16824), arXiv 2026 | 长时闭环 coding agents；vLLM | coding session、prefix/cache entry | tool-call metadata、真实 coding trace 统计 | prefix-aware scheduling、prediction-guided reuse-aware eviction | eviction 降低 2-2.6x；session completion time 最高改善 3.5x | **当前最直接的真实 workload 竞品。** 覆盖 coding-agent trace、工具 metadata 预测和 session JCT；BeliefKV 必须在跨 coding/search/research 泛化、action frontier、PCIe 联合控制或更弱接口上胜出。
| [Ask the Tool, Don't Guess](https://arxiv.org/abs/2609.18849), 2026-09-16 预印本 | 工具等待期间的 KV 管理；HBM 与 HBM+DRAM | 运行中的工具调用、保留/释放/恢复提示 | 工具实际进度、剩余比例或临近完成信号；独立侧通道 | 用当前进度更新 KV 管理时机 | 论文报告工具后 p90 TTFT 相对 LRU 降低约 20.7%/20.8% | **信号路线的重要新对照。** 工具进度与 child 的 completion intent/EOS 不是同一信号；只能在可获得真实进度的工具子集比较。公开可运行 artifact 尚未核实。
| [AugServe](https://arxiv.org/abs/2512.04013v3), 2026-07-10 修订稿 | augmented requests；含单 RTX4090，也含 H800/A100 | 跨工具调用的 request service segment | BERT 预测 output length 和 tool duration；真实 return length | InferCept policy + space-time value-density 排序 + free/reclaimable token budget | v3 摘要报告有效吞吐为 vLLM/InferCept 的 4.7x/3.3x | **高重叠。** 覆盖预测工具行为、KV policy 和运行时 refinement；不建模 next Agent、依赖解锁和 workflow fairness，目标仍偏 TTFT/normalized latency。
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
| [PackServe](https://arxiv.org/abs/2609.33224), 2026-09-27 预印本 | 集群 request packing、prefix locality、token SLO | 白盒模型估计 prefill/decode 干扰，在 SLO 内合并请求与实例 | 64 H20 GPU 评测及生产集群；目标是 GPU-hours | 近期强邻近系统；当前单卡无实例收缩动作，不列第一轮必跑方法。扩展到多副本或 SLO/成本主张时补入，不能用其单卡子策略冒充完整系统。
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
| [Lachesis](https://arxiv.org/abs/2610.08378), 2026-10-06 预印本 | 按 turn/subagent/worker 的状态生命周期在 HBM 与 high-bandwidth flash 间放置并释放 KV segment | 与 child 终态释放的动机相关，但核心约束是 HBF 写寿命，证据为 trace-driven simulation；当前 HBM/DRAM 实机不直接具备该介质和目标，列相关工作及条件扩展。
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
| TokenCake | 2026-10-21 核对：v5 链接的 [官方代码](https://github.com/pku-lemonade/TokenCake)可访问；旧记录的 owner 少了连字符。README 明确预测预取仍在开发 | 分开 `public-artifact` 和补全预测上传后的 `paper-reimplementation`；仓库可访问不等于完整论文功能已经复现 |
| CacheWise | 2026-10-21 核对：[作者仓库](https://github.com/cachewise-project/cachewise-coding-traces)提供 traces、工具时长预测和分析代码 | 可复用预测训练流程；不能把该仓库当作已取得完整 vLLM 调度器实现 |
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
9. TokenCake、ScaleSim、AugServe：逐项核对可运行原生实现。ScaleSim 已在 KVFlow 官方仓库公开；TokenCake 在 2026-10-21 已核实正确官方地址，但预测预取尚未包含在公开实现中。未取得完整机制时仅保留明确命名的 public artifact 或 paper reimplementation/reference，不伪造完整系统级复现。

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
| 第二批 | TokenCake | 工具预测上传 + 图/运行状态驱动空间预留，是否覆盖我们的组合 | 本行原为 10 月 12 日历史计划；10 月 21 日已核实官方仓库，公开实现缺预测预取，按 6.5 分列实现范围 |
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
  2026-10-21 已核实正确地址为
  [pku-lemonade/TokenCake](https://github.com/pku-lemonade/TokenCake)，
  旧 owner 拼写产生的 404 结论已被本次核验取代。

### 6.5 最新 baseline 清单与适配顺序（2026-10-21）

本节针对当前单 GPU、SGLang/HiCache、Qwen3.5 FULL+Mamba、动态多轮
spawn/JOIN 和工具调用场景。它是后续适配计划，不是已经完成的系统对比。
优先级由机制相关性、公开代码完整性和当前硬件适用性共同决定，
不按作者报告的加速倍数排序。

#### 6.5.1 必须保留的基础与简单策略

| 条件 | 实际比较内容 | 要回答的问题 |
|---|---|---|
| Native SGLang/HiCache | 原生准入、缓存替换和需求恢复；写入策略在开发集选择并冻结 | 相对配置合理的原生系统是否有净收益 |
| Reactive common-path | 共用事件接入和物理动作接口，保留 reactive 决策，不启用预测迁移 | 共同接入层增加了多少成本 |
| Event-only | 仅根据已经观测到的无工具 EOS、JOIN satisfied、tool END 恢复；不用剩余工作或历史时长模型 | 复杂预测相对便宜的事件规则是否有增量 |
| Locality/restore-ready scheduling | 在实际可执行请求间按真实 HBM 命中和恢复就绪做有界排序，保留老化 | 收益是否主要由准入和缓存局部性解释 |

Event-only 的 EOS 可能早于 harness JOIN，不能统一记为 RETURN 后需求恢复。
所有方法记录实际提交相对完整 JOIN/全工具结束边界的位置。Locality
条件可与 Event-only 组合，但组合结果单独命名，不归入某篇论文的原实现。
announce/最终报告行为、工具环境和任务 prompt 在各条件保持一致。

#### 6.5.2 已发表或公开预印本候选

| 优先级 | 候选 | 与本系统直接重叠的机制 | 本次核验的实现状态与适配方式 |
|---|---|---|---|
| 第一批 | ThunderAgent | program-aware 准入、工作集管理与工具生命周期 | [官方仓库](https://github.com/ThunderAgent-org/ThunderAgent)明确支持 SGLang/vLLM；优先接入同一 SGLang，保留代理和控制成本，单卡结果不引用集群收益 |
| 第一批 | Continuum | tool-specific KV TTL 与 program-FCFS 联动 | [论文 v7](https://arxiv.org/abs/2511.02230v7)、[公开预览版](https://github.com/Hanchenli/vllm-continuum)可得；README 明确缺论文 estimation，补全估计器后另标 paper reimplementation |
| 第一批 | CONCUR | 根据 cache/拥塞反馈控制活跃 agent 数，减少 thrashing | [论文](https://arxiv.org/abs/2601.22705)可得；本次未核实官方可运行 artifact，按原算法实现统一底座策略，外部到达不变，准入外等待计入 JCT |
| 第一批 | TokenCake | 工具期 offload、预测 upload、关键 agent 的动态容量预留与调度 | [v5](https://arxiv.org/abs/2510.18586v5)、[作者仓库](https://github.com/pku-lemonade/TokenCake)可得；public artifact 是事件备份+需求恢复，预测上传须补全并单列；图/重要度输入需保持真实可得 |
| 第二批，论文主张中重要 | KVFlow | Agent Step Graph 距离驱逐、共享 prefix 和重叠 CPU-to-GPU 预取 | [论文](https://arxiv.org/abs/2507.07400)、[作者仓库](https://github.com/PanZaifeng/KVFlow)可得；仓库同时包含 ScaleSim，锁定对应策略；已知图组按原假设，动态观察图扩展另命名 |
| 第二批，coding 主对照 | CacheWise | tool metadata 的 reuse 预测、prefix-aware 调度和 eviction | [论文](https://arxiv.org/abs/2606.16824)、[数据与预测代码](https://github.com/cachewise-project/cachewise-coding-traces)可得；未核实完整 serving artifact，移植策略时报告复现范围 |
| 第二批，调度主对照 | Agentix，早期名 Autellix | program 累计服务/调用依赖驱动的优先级与抢占，降低 HOL blocking | [NSDI 2026 正式论文](https://www.usenix.org/conference/nsdi26/presentation/luo)、[早期稿](https://arxiv.org/abs/2502.13965)可得；以正式版本核对算法，本次未核实可运行作者仓库，不把普通优先级队列冒充 Agentix |
| 第二批，联合设计主对照 | Pythia | 从 workflow profiling 合成执行模式，联合 speculative cache management 和 lookahead scheduling | [v2 论文](https://arxiv.org/abs/2604.25899v2)、[机制正文](https://arxiv.org/html/2604.25899v2)可得；本次未核实官方可运行 artifact。动态模式学习、prompt composition 和局部策略须保留；集群 autoscaling 另表，不把第三方 inspired-by 仓库视作作者代码 |
| 第二批，预测主对照 | CacheScout | 在线 agent 转移学习、eviction 和主动 prefetch，无预定义图 | [论文](https://arxiv.org/abs/2608.14624)可得；本次未核实官方可运行代码，按论文重实现并保留 prefix-anchor 语义 |
| 第二批，预测主对照 | PBKV | 历史和当前上下文联合预测未来多步 agent 调用，保守 eviction/prefetch | [论文](https://arxiv.org/abs/2605.06472)可得；本次未核实官方可运行代码，不用一阶转移表替代其预测器 |
| 补充工具基线 | InferCept | interception 期间 Preserve/Swap/Discard 的成本选择 | [ICML 2024 正式论文](https://proceedings.mlr.press/v235/abhyankar24a.html)、[作者代码](https://github.com/WukLab/InferCept)可得；提取策略到统一底座，旧 vLLM 原生结果另表 |
| 条件扩展 | AugServe | 工具/输出预测驱动排序和运行时自适应 batching | [论文](https://arxiv.org/abs/2512.04013)可得；本次未核实官方可运行代码，估计器和原排序准则不能省略，适合工具主导组 |
| 信号路线扩展 | Ask the Tool, Don't Guess | 工具运行中显式进度驱动的保留、释放、恢复时机 | [2026-09-16 论文](https://arxiv.org/abs/2609.18849)可得；本次未核实公开可运行 artifact，在有真实进度的工具子集比较，不能把 child EOS 当作工具进度 |

第一轮适配从前三个基础条件、ThunderAgent、Continuum、CONCUR 和
TokenCake 的公开机制开始，再加入 Locality 对照。KVFlow、CacheWise
和 Agentix 分别检验图预取、coding 缓存预测和 workflow 调度，随后
补齐；若论文主张保留动态预测能力，CacheScout/PBKV 不能只留在 related
work。InferCept 的策略结构较直接，可与工具路径适配并行推进；
不要求在改进阶段每个条件都做正式多轮实验。

#### 6.5.3 统一适配和论文实验的边界

1. 统一底座比较保留论文的决策逻辑，独立输出保留/卸载/预取/准入意图；
   只共享物理有效性、引用、容量和 DMA 依赖处理。结果标为
   `method-adapted-sglang`，不能写成已复现作者原系统的性能。
2. 固定模型、实际 HBM/Host 字节容量、任务与到达、harness、工具上限、
   生成配置及观测等级。各方法使用自己的 predictor/TTL 和调度策略；
   不强制经过 BeliefKV 的预测门槛、保护策略或收益判断。
3. 公开仓库未证明支持 Qwen3.5 的 FULL+Mamba。统一适配须保留全部必要
   recurrent-state 依赖与容量，状态成本单列。确实不兼容时，两侧一起
   换共同支持的纯 Attention 模型并另表比较。
4. 动态主组只提供截至决策时已观测的事件与图；预声明图方法另有共同
   已知图组。不提供未来实际 child、工具时长或完整动态 DAG。方法有
   原生 metadata 接口时可公平接入并计费，不刻意剥夺其必要输入。
5. 正式实验冻结版本并做配对重复，报告吞吐、JCT、服务/排队、实际
   prefill/decode/上下文需求及迁移和重算。保留所有 workflow 的干扰，
   不以任务正确率加权系统速度，也不用请求数简单除 JCT 来校正需求。
6. BeliefKV 分别关闭 PREPARE、JOIN 预取、tool 预取、恢复就绪优先级，
   并比较简单 EOS 事件策略，以解释真正的预测和调度增量。hindsight
   oracle 只用于机会分析，不列为可部署系统的主对照。

LMCache、Mooncake、Parrot 和集群 PD-disaggregation 系统仍是基础设施
或邻近工作候选；当前优先覆盖单卡 agent 工作集和迁移/准入决策。
若后续扩展到多 GPU、远端 KV 或更换数据面，再独立增加相应原生系统
对照，不能以单卡策略移植宣称已经验证其集群能力。

<a id="baseline-scheduling-ablations"></a>

### 6.6 本次请求：调度消融、必要外部对照与实施次序

本节是 2026-10-21 联网调研后的方案。用户进一步要求 V109 完整收尾后
先进行内部对比；独立开关与接续器的最终配置通过 V2573 的 3058 项
CPU 检查，V2574 绑定源码，外部适配仍未实施。
不改变 V109 的冻结代码和接续 native。研究假设是“只调整请求顺序可能无法获得完整系统的 JCT/吞吐
收益”，不是预先指定调度消融必须失败。

#### 6.6.1 先完成能分辨因果来源的内部对照

| 条件 | 请求调度 | KV 管理 | 主要检验 |
|---|---|---|---|
| Native | 原生顺序与准入 | 原生 prefix cache、HiCache、需求恢复和回收 | 无新增策略时的性能 |
| JOIN-priority-only | 只对真正 JOIN 解锁且已提交的 parent 做有界优先；沿用相同老化及优先名额限制 | 同 Native | 简单 parent 优先是否已能解释收益，以及是否牺牲吞吐或公平性 |
| OurScheduler-only | 保留当前可独立定义的 JOIN/tool-return、最终 child、workflow 排序、老化和只读 locality 规则 | 同 Native；关闭全部 BeliefKV 主动 KV 动作 | 完整请求调度本身能解释多少收益 |
| KV-only | 原生请求顺序；仍检查真实执行容量和数据依赖 | BeliefKV 备份、卸载、预取及生命周期管理 | KV 机制在没有新增排序时的增量；作为补充因子消融 |
| Full BeliefKV | 当前完整调度 | 当前完整 KV 管理 | 调度与 KV 协同是否超出单独机制 |

“只保留调度”必须关闭 JOIN/tool 预测 H2D、后台及 spawn 增量
PREPARE_HOST、工具预测卸载、关键 parent 容量预留/冷 donor 选择、
语义化 child 终态释放、额外 HBM/Host 驻留租约、confirmed ticket
恢复和需求 handoff。普通原生 session close、引用释放、需求 H2D/D2H
和正确性依赖仍执行；不能关闭 HiCache 或删去必要 Mamba 恢复，制造
一个没有正常缓存能力的对照。

保持同一个可独立运行的 ranking 函数与配额。当前部分“恢复就绪优先”
来自我们实际迁移的 ACK/租约；移除迁移后不能伪造 ACK 或租约来凑齐
排序分支。只读 native HBM 命中/依赖就绪可保留，恢复派生的分支自然
没有候选，并在开关清单中登记。`JOIN-priority-only` 不要求完整 HBM
命中才获得优先级，数据依赖与需求恢复仍由原生处理；不能把它偷偷改成
restore-ready-only 或无限 root 插队。

V110 调试后将简单 JOIN-only 的配额语义明确为：累计四次普通准入后
可提升一个真正解锁、已提交的 parent，即使原生队首已老化 10 秒；
随后重置配额，普通老化请求仍按 FIFO 使用普通名额。旧实现额外要求
队首未老化，会在高压下压掉这一策略，旧调试段不进入完成比较。
仅调度模式还需移除无消费者的工具预测、decode 上下文记账；JOIN-only
不处理 final-report 预测提示，而 OurScheduler-only 保留自己的最终 child
排序元数据与只读首次服务反馈。V111 用最新修复代码重跑四组，
仍按用户要求复用 V109 Full，记录版本差异。
修复后 V2579 为 3067 项全部通过、无跳过，保留原 3058 项身份并新增
9 项；V2580 绑定源码，老化用例按新配额语义更新预期。
V111 首组 JOIN-priority-only 已完成 36/36，43 次有界 JOIN 优先准入，
32 次 aged 队首后排序；无用工具预测、维护和自定义物理动作均为 0。
73 轮动态委派、128 个 child，未观测嵌套。
[V2595 完整比较](../experiments/reports/v2595_v111_join_only_completed_comparison_20261021.json)
的三组共同去长尾窗口 600–2520.937 秒中，Full/JOIN-only/原始 native
吞吐 580.52/592.37/562.09 token/s，受限平均 JCT
1830.28/1704.12/2055.63 秒。JOIN-only 目前胜过 Full，不能再预设
单纯 parent 优先会拖累吞吐。Full 迁移字节更低尚未转化为整体优势；
版本、前端和动态输出差异使这仍是开发比较，工程对齐 native 尚待完成。
V2595 同时保留原始 native 的 3 个 agent-JCT 资格排除和独立分组。

最新 CPU 方案将有效的简单基线用作 Full 排序骨架：
`BELIEFKV_PREFILL_SCHEDULING_POLICY=join_priority` 保留 KV 管理，完整排序
共用 JOIN-only 路径，关闭驻留/恢复/因果排名和容量绕行；workflow
配置中的最后 child/局部性作为后续条件对照。V2613 全部 3493 项通过、
无跳过，V2614 绑定源码，额外 V2615 的 17 项 EOS 检查通过。V2612 的
中立队列 CPU 降本不是 GPU/JCT 收益。新配置见
`configs/migration/next_predictive_baselines_20261022.json`，待冻结接续：
2026-10-22 06:00 北京时间后，fresh batch8/seed21 Full、JOIN-only、
工程对齐 native。以额外 KV 增量为门槛；不得预设复杂 Full 必须胜出，
不得删去负结果或用减少输出、guard 截断制造收益。

V2610 纳入已结束的 OurScheduler-only：共同去长尾吞吐
Full/JOIN-only/OurScheduler-only/native 为 580.52/592.37/576.00/562.09 token/s，
受限平均 JCT 为 1830.28/1704.12/1835.56/2055.63 秒；各组实际工作量有差异。
V2611 的 JOIN 首服等待进一步支持先比较简单排序。新预约前只做 CPU 工作。

此前 OurScheduler-only 于北京时间 2026-10-21 18:46:58 接续；四组仍冻结
`e0420fe`/`53335e6`、running12/seed21/256K。第二组于 19:44 返回退出码 1：
36/36 completed，system JCT 资格 36/36，native-agent JCT 资格 35/36；
一条 `guard_intervened_completion` 未通过全体 native gate，不能记成
GPU 崩溃或未完成 workflow。失败状态保留，全局暂停标记已生效；
不启动 KV-only 或工程对齐 native。
Full 继续复用 V109。后续身份存储优化、独立 JOIN 审计和 Host-hit
口径修正均不热加载。V2593 全部 3110 项 CPU 检查通过、无跳过，保留
V2586 的 3080 项并新增 30 项，V2594 绑定当前源码；不是新的 GPU 验收。
V2592 不要求预取计划，按首次 root 到达的 600–1200 秒匹配真实 JOIN
后继，Full/JOIN-only/native 入队至首服 P50 为
608.90/331.70/12340.70ms。样本不同，不当作事件级因果配对。
逻辑 Host-hit 可包含已驻留 FULL 的 Host 副本，不用其推算物理缺页
或 H2D；实际 controller 分池回执仍是迁移字节的依据。
用户要求将有效的简单 JOIN parent 优先合入 Full，并考虑最后一个 child
优先。main 据真实 JOIN 解锁/等待关系增加有界排序，保留原生准入和
普通名额；这是待 GPU 验证的新实现，不混入当前冻结结果。
最新 main 进一步共用 Full/JOIN-only 的 JOIN 选择、aged FIFO/native
顺序和独立四次普通准入配额，恢复状态不再让普通请求跨类别或 aged
队首抢先。真实容量压力可准确撤销自己的额外驻留锁；保留首次服务归因，
租约记录本身不再排除回收候选，原生正确性依赖保留。另设
`BELIEFKV_PREFETCH_RESIDENCY_PROTECTION=0|1` 独立比较 ACK 后额外保护；
默认有界且可撤销，尚未做 GPU 开关对照。V2600/V2601 为此前策略资格，
不再绑定新候选。V2608 保留原 3148 项身份并扩展至 3456 项，全部通过、
无跳过，另有 2 个 subtest；V2609 绑定源码。测试名称保留，旧恢复
特权断言按新策略明确更新。离线审计分开记录撤销保护、实际回收和复用。
后续比较不能把 Full/对照输出 token 和委派次数差异归为纯 KV 收益，
也不能用“是否超时”代替 JCT、恢复等待、重复加载和重算的验收。

本次内部矩阵选 batch12，以保持与复用的 V109 Full 相同的 batch。
V109 同 seed 配对全程平均 JCT 约降低 20.52%、共同去长尾受限平均 JCT
约降低 13.44%、吞吐约提高 2.65%；历史 batch8 为吞吐约 +11.00%、
全程 JCT 约 -11.16%、去长尾受限平均 JCT 约 -9.77%，但代码、seed、
上下文和工作轨迹不同，不能用来认定 batch8 更优。用户允许择优不
改变对照边界：不混用 batch8 消融和 batch12 Full 证明机制收益。
最新用户约定为先完成当前 batch12 矩阵；若 Full 优势仍不明显，后续
默认 batch8，并保持服务端/请求 seed21。当前四组配置不变；未来
batch8 对照必须与同 batch 的 Full/native 比较，历史不同 seed/code
的 batch8 结果只作开发参考。

现有 `reactive` arm 不是 `OurScheduler-only`：
`scripts/run_qwen35_native_regime_probe.sh` 的非 off 分支仍可打开
PREPARE_HOST、resident-first 和最终阶段优先。实现时增加明确的条件名、
启动后真实配置核验和动作审计，不能仅关闭两个 prefetch 开关就声称
已经移除 KV 管理。先用小型 CPU/短 workload 验证仅调度组确实没有
自定义迁移动作，再安排完整比较。`BELIEFKV_INTERNAL_COMPARISON_MODE`
区分 `full/join_priority_only/scheduler_only/kv_only/native_matched`；
实际运行时状态与物理动作同时核查，不只看环境变量。独立 CPU 资格与
GPU 登记以当前执行计划为准。

如果 `OurScheduler-only` 更快，应如实报告，重新限定 KV 管理的价值。
如果 JCT 更短而吞吐下降，则同时报告其 trade-off、HBM miss/需求
H2D、batch 填充与等待。不能通过人为等待 GPU、剥夺老化或刻意选择
最差优先配额来获得用户预期结论，也不能把各组效果简单相加当作机制收益。

#### 6.6.2 外部方法按要排除的解释安排

| 问题 | 必要方法 | 适配重点 |
|---|---|---|
| 是否只是 program 调度有效 | Agentix/Autellix、ThunderAgent | 前者保留正式论文的优先级/抢占/老化；后者保留 program 工作集与 phase/elapsed-time 管理。两者不等于简单 parent 优先 |
| 是否只是限制活跃 agent 减少 thrashing | CONCUR | 按原控制单元、cache 信号和更新规则实现；准入之外的等待从外部到达计入 JCT，不把 active-agent window 偷换成 max-running |
| 是否只是工具期保留/卸载得当 | Continuum；InferCept 补充 | tool-specific TTL 与 Program-FCFS 联动；或 Preserve/Swap/Discard。完整保留估计器、成本项与超期行为 |
| 是否已被 graph-aware KV 管理覆盖 | KVFlow、TokenCake | 对应 ASG/steps-to-execution，或时间/空间调度和 reserved/shared pool；保留真实可得图与重要度输入 |
| 是否只是工具 metadata 或动态执行预测 | CacheWise、CacheScout、PBKV | 分别保留 coding-tool 预测、在线转移/anchor、多步 workflow 预测；不强制三者改用我们的预测器 |
| 是否已被缓存与 lookahead 联合设计覆盖 | Pythia | 核实历史 profiling、角色和 prompt composition 接口；保留其模式合成和局部 cache/scheduling，单卡不比较 autoscaling |

实施顺序：

1. 当前 V109 完整收尾后，先做内部矩阵，Full 直接复用 V109，
   不重复运行。依次执行 JOIN-priority-only / OurScheduler-only /
   KV-only / 工程对齐 Native。用户明确允许四组直接用最新代码及新增
   CPU 优化；记录与 V109 的应用版本差异，本轮为开发判断，不称为严格
   同版本因果归因。各组对齐前端 token cache，V109 untouched native
   单列，不把这一矩阵当成外部论文方法已复现。
2. 外部第一批优先 CONCUR、ThunderAgent、Continuum，分别覆盖反馈
   准入、program 工作集、TTL+调度。Agentix 同时核对正式算法与代码，
   是调度主张的重要对照，不能被简单 parent 优先替代。
3. 接着完成 KVFlow 和 TokenCake；TokenCake 公开实现与补全预测上传
   的论文重实现分开命名。若完整代码难以取得，不能把缺机制的公开版
   作为唯一证据宣布战胜该论文。
4. coding/动态预测主张需要补齐 CacheWise、CacheScout/PBKV；
   联合 workflow/cache 主张需要补齐 Pythia。它们不是只列 related
   work 就足够的条目，先完成机制和输入兼容性验证再进入主表。

TOPAS 是 joint residency/admission 的重要条件对照，使用共同支持的
已知 DAG 组；ScaleSim 只在 invocation distance 真实可得的场景上线，
不提供未来完整轨迹冒充在线输入。SAGA 是 workflow JCT/fairness 的
强集群对照，当前只能明确命名本地机制适配；多 GPU 时再比较完整系统。
AugServe 和 Ask the Tool 分别适合工具主导/有真实进度的补充组。
PackServe 的集群 packing、Lachesis 的 HBF 写寿命，以及近似
KV pruning/quantization 不进入当前单卡 exact-KV 第一轮主表；
后续相应扩展时再纳入，不能将“当前不直接适配”表述成这些方案无效。

#### 6.6.3 对齐当前条件并分开完整系统收益与 KV 因果收益

- 以启动时最新冻结配置为准。当前约定为 36 个同时到达 LoCoBench
  root、256K/95%、running/decode 有效上限 12、Host 200GB 80:20、
  工具上限 180s，服务端和请求种子均为 21。比较真实 FULL/Mamba
  allocator 容量，不能只比较配置百分比。
- 所有方法使用同一 model、prompt、harness、工具限制、初始任务、
  外部到达和观测等级；内部分配、活跃 agent 数、TTL 和 eviction
  可以按其算法变化，并给予相同的开发调参预算。动态轨迹和生成工作量
  仍须报告，固定种子不等于请求轨迹完全相同。
- 仅调度消融与 Full 对齐前端 token cache 等非核心优化。当前完整
  predictive/native 可以分别启用/关闭前端 cache，但这个差异不能
  计为 KV 创新；保留 untouched native，并另设非核心工程一致的 native
  作为机制对照，避免用不对齐的前端成本解释 KV 因果收益。
- 统一 SGLang 数据面只共享物理正确性接口，各方法独立决策。结果叫
  `method-adapted-sglang`；作者原栈单列，并配它自己的 native。
  本次未核实公开 artifact 不等于证明作者没有代码。未来适配时再锁定
  论文版本、仓库 commit、缺项、算法到代码映射和模型兼容性。
- 先验证混合状态语义。无法保持 Qwen3.5 FULL+Mamba 正确性的原栈，
  在共同支持的纯 Attention 模型上另做双方对比，不删除必要状态，
  也不把模型不同的两次绝对性能相除。
- 主报所有外部到达 workflow 的 JCT、完成/未完成情况、公平性；
  同时按预冻结规则剔除工具长尾后的繁忙吞吐，保留完整长尾结果。
  诊断记录实际输入/输出量、KV miss/重算、需求与提前 H2D、
  ACK→首次消费及同步控制成本，不用传输 GB 或 ACK 数替代净性能。

本次新增/重新核验的一手入口：

- [SGLang HiCache 设计与原生写回/重叠传输](https://docs.sglang.io/docs/advanced_features/hicache_design)。
- [Agentix NSDI 2026](https://www.usenix.org/conference/nsdi26/presentation/luo)；
  [CONCUR](https://arxiv.org/abs/2601.22705)；
  [ThunderAgent 作者代码](https://github.com/ThunderAgent-org/ThunderAgent)。
- [Continuum v7](https://arxiv.org/abs/2511.02230v7) 与
  [预览版 README](https://github.com/Hanchenli/vllm-continuum)；
  [TokenCake README 的实现边界](https://github.com/pku-lemonade/TokenCake)；
  [KVFlow/ScaleSim 作者代码](https://github.com/PanZaifeng/KVFlow)。
- [Pythia v2 正文](https://arxiv.org/html/2604.25899v2)；
  [CacheWise](https://arxiv.org/abs/2606.16824) 与
  [数据/工具时长预测代码](https://github.com/cachewise-project/cachewise-coding-traces)；
  [CacheScout](https://arxiv.org/abs/2608.14624)；
  [PBKV](https://arxiv.org/abs/2605.06472)。
- [PackServe](https://arxiv.org/abs/2609.33224)；
  [Lachesis](https://arxiv.org/abs/2610.08378)。这两项本次只登记场景边界，
  不以它们的论文结果证明当前单卡收益或可复现性。

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
