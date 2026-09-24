Agent 调度系统研究进展综述

调研截止日期：2026年9月23日

摘要

基于大语言模型的智能体系统将一次模型请求扩展为由多轮推理、工具调用、状态交互和智能体协作构成的持续工作流。由此，服务系统的优化对象从独立请求转向具有因果依赖和跨轮状态的任务过程，调度问题也由批处理与请求排队扩展至工作流执行、异构资源分配和 KV-cache 生命周期管理。近年来，相关研究形成了应用层工作流选择、程序级调度、计算资源隔离、工具等待期状态保留、未来调用预测以及 KV 驻留与请求准入联合优化等技术路线。与此同时，生产负载研究表明，非模型组件可能主导端到端时延，跨轮缓存命中也不必然转化为更高的工作流吞吐。本文系统梳理上述研究进展，比较其工作流信息假设、调度粒度、资源管理对象及评价指标，并总结当前方法在动态性、物理资源耦合和评测可比性方面的局限。

关键词：智能体服务；工作流调度；大语言模型推理；KV-cache；显存管理

一、研究背景

传统大语言模型服务通常将输入请求视为相对独立的推理任务，以首 token 时延、逐 token 时延和请求吞吐作为主要优化目标。智能体应用则由多个模型调用与外部操作串联而成，工具返回可能触发后续推理，推理结果也可能派生新的子任务或等待多个智能体汇合。因此，单次模型调用的服务效率并不能充分代表整个应用的执行效率。系统需要同时理解调用之间的因果关系、跨轮上下文的复用价值，以及 GPU、主机内存和外部工具的资源竞争。

现有负载测量进一步显示，智能体应用的瓶颈具有显著异质性。AgentSysBench 对多类应用进行系统级测量，发现其所考察的十类应用中有五类由非大语言模型组件主导时延；sandbox 工作集在部分场景可达到每个会话数十 GB，应用组件间的时延差异也很大。系统状态还可能在活跃步骤之间闲置数分钟至数小时。[1] 其他工作负载分析指出，工具调用阶段、上下文重用和生成长度会共同改变请求的计算特征；启用上下文缓存后，部分智能体任务会由 prefill 主导转为 decode 主导。[2] 这些结果说明，智能体调度不能仅依据单次请求长度或缓存命中率制定策略，而应围绕完整任务过程识别实际瓶颈。

二、调度粒度由请求转向工作流

一类研究将服务对象从单个模型请求提升为程序、会话或工作流。Agentix 将智能体执行表示为运行时逐步显露的程序，并依据程序服务状态实施优先级调度和抢占，从而减少传统请求级调度对跨轮执行连续性的忽略。[3] SAGA 面向集群环境，将工作流执行、会话亲和性、负载均衡和公平性纳入统一调度过程，并结合 KV 状态复用管理智能体任务。[4] Nalar 则通过携带依赖关系、生产者和消费者等信息的异步状态对象支持动态工作流执行，使逻辑任务状态能够与物理放置相分离。[5]

这类方法的共同特点是以任务过程为单位，而不是将工作流中的模型调用作为互不相关的请求。其主要贡献包括动态依赖追踪、程序级优先级、跨轮局部性以及完成时间公平性。与此同时，不同系统依赖的工作流信息并不相同：有的方法在运行时维护程序结构，有的方法需要应用提供任务或阶段信息，另一些方法则基于历史轨迹推断后续行为。因此，“工作流级调度”并非单一技术，方法间的可移植性取决于元数据接口、执行模型和调度目标。

Pythia 从历史执行轨迹中归纳工作流模式，并根据工作流类型、会话标识和智能体角色等信息预测后续调用、输出长度及提示词构成，再将预测用于前瞻式资源管理。[6] 与之不同，LLM-as-Scheduler 主要在应用层依据查询特征和中间结果动态选择后续工作流路线，以减少不必要的智能体调用。[7] Dyserve 则通过物理计划编译与自适应运行时，联合选择模型、验证策略和服务后端。[8] 这些工作表明，智能体调度既可以改变资源执行顺序，也可以改变工作流本身或其计算计划；比较系统方法时，需要明确其优化边界。

三、计算资源与服务架构调度

智能体请求在 prefill、decode、工具调用和结果汇总阶段呈现不同的资源需求。AgentServe 面向单 GPU 环境，利用 decode 延迟反馈调节 resume-prefill 预算，并通过 CUDA Green Context 对 GPU SM 资源进行隔离，以降低长 prefill 对 decode 的干扰。[9] 该研究表明，即使工作流层面的优先级合理，prefill 与 decode 的计算竞争仍可能限制智能体任务进展。

面向多实例或集群的研究则关注服务时间预测、请求路由、扩缩容和异构资源放置。SwarmX 利用工作负载特征和服务时间预测改善智能体请求的路由与资源调度。[10] Agora 对智能体工作流中的 CPU、GPU 与主机侧状态进行协同分析，并研究显存超额复用和后续智能体状态预取等机制。[11] 这类方法强调，工具执行和控制逻辑可能与模型推理共同构成关键路径；仅优化 GPU 内部批处理或 KV-cache 并不能保证端到端时延下降。

总体而言，单卡与集群系统面临的资源管理问题并不相同。单卡环境的核心矛盾通常是有限 HBM 下的计算、KV 驻留和数据迁移竞争；集群环境还需处理实例路由、负载不均衡和分布式状态迁移。因此，集群系统的吞吐提升或路由收益不能直接推导为单 GPU 场景中的效果。

四、KV-cache 生命周期管理

（一）工具调用间隔中的状态保留

工具调用会使模型推理暂时中断，但工具返回后可能需要继续使用此前生成的上下文。InferCept 研究工具中断条件下的上下文保留、换出与恢复，旨在减少工具返回后的重复计算。[12] AugServe 进一步结合输出长度和工具执行时长预测，对增强型请求进行动态调度，并依据运行时资源状态分配服务预算。[13]

Continuum 将工具调用间隔视为 KV-cache 生命周期管理的重要决策窗口，根据工具时长分布、KV 重载或重算代价以及潜在排队时延设置缓存存活时间，并与程序级调度协同。[14] Astraea 则根据请求的阶段状态、I/O 与计算特征及内存压力进行分层调度，并在保留、交换和丢弃上下文之间作出选择。[15] 这些研究共同表明，工具等待期间的 KV 管理已从简单的“保留或释放”扩展为对等待时间不确定性、恢复成本和系统拥塞的联合权衡。

（二）未来调用预测与前瞻式缓存管理

KVFlow 使用多智能体工作流图描述调用关系，并依据未来执行距离管理 KV 节点的淘汰和预取。[16] TokenCake 同时考虑时间维度上的 KV offload 与 upload，以及空间维度上的显存预留和共享池管理。[17] ScaleSim 将未来 invocation distance 作为缓存价值的重要信息，支持基于距离的淘汰与主动预取。[18] 这些方法主要利用显式工作流结构或距离信号提升未来调用的缓存命中。

针对动态工作流，PBKV 根据历史工作流及当前任务上下文预测后续智能体调用，并据此估计缓存重用价值。[19] CacheWise 聚焦长时编码智能体，利用工具调用元数据预测会话的缓存重用行为，并结合前缀感知调度和缓存淘汰策略。[20] CacheScout 则在线学习智能体之间的转移关系，不依赖预定义工作流图或离线模型，再将预测结果用于 KV 淘汰和预取。[21] Pythia 采用基于历史 profiling 的工作流预测，说明即使没有完整静态 DAG，重复出现的工作流模式仍可用于前瞻式资源管理。[6]

上述研究覆盖了显式执行距离、历史轨迹预测、工具元数据预测和在线转移学习等不同信息来源。它们的差异不只在预测算法，也在于是否需要预先知道工作流结构、是否需要应用提供智能体标识，以及预测对象是固定前缀、完整会话状态还是后续模型调用。相关实验通常在特定工作流和运行环境下进行，报告结果不能脱离其工作负载和元数据假设进行直接比较。

（三）KV 驻留与请求准入联合优化

TOPAS 将缓存驻留决策与请求准入统一建模，在共享 KV 容量约束下选择需要保留的智能体前缀及需要执行的请求。其决策考虑任务剩余服务路径、下游前缀复用、迁移和抢占成本，并通过老化机制缓解任务饥饿。[22] 该工作的重要意义在于揭示，缓存局部性与工作流进展并非总是一致：保留当前可复用前缀可能挤占执行其他请求所需的显存，而优先执行当前请求也可能造成后续重复 prefill。其评估主要基于已知的共同工作流结构，因此结论需要结合这一信息假设理解。

其他系统从不同侧面处理活跃工作集和并发度。ThunderAgent 依据程序阶段和等待状态管理长期运行的智能体工作集。[23] CONCUR 根据系统拥塞和缓存反馈调整活跃智能体数量，以避免过度并发引起显存压力和缓存抖动。[24] 这表明，KV 驻留、请求准入和活跃任务数具有直接耦合关系，孤立评价任一策略可能无法解释端到端性能变化。

（四）KV 表示与跨智能体复用

除精确缓存驻留管理外，部分研究直接改变 KV-cache 的表示或保留粒度。AgentKV 根据 think、act、tool 等智能体阶段的差异进行阶段感知的 KV 评分与淘汰，探索以有限缓存预算保留更有价值的上下文。[25] TokenDance 面向同步轮次和 all-gather 型多智能体结构，通过集体 KV 收集与差分存储降低兄弟智能体间的上下文冗余。[26] IntentKV 与 UltraQuant 分别研究跨轮上下文裁剪和低比特 KV 表示。[27][28]

上述方法与精确的 GPU/CPU 缓存迁移并不等价。裁剪、量化或跨上下文 KV 复用可能影响数值精度、上下文语义或下游任务质量，因而需要将质量指标与系统性能联合报告。对于工作流调度研究，缓存压缩适合作为互补机制或独立比较对象，不能将其带来的容量收益直接归因于调度策略。

五、评价目标与实验方法

智能体服务的评价目标正在由单请求交互时延扩展至会话和工作流级性能。SMetric 基于生产智能体轨迹提出以会话为中心的调度，在集群负载均衡与跨轮 KV 局部性之间进行折衷，并以 session TPS 和延迟共同评价。[29] 这说明，智能体通常以完整模型响应作为下一步行动输入，逐 token 延迟的重要性可能低于传统面向人类实时阅读的聊天服务；但这并不意味着首 token 时延、单轮延迟或尾延迟可以忽略。

AgentServeSim 将智能体程序作为仿真单元，通过前序任务完成事件因果地释放后续调用，并分别建模 KV 保留和请求派发策略；其仿真结果使用真实 vLLM 部署进行校准。[30] 这一方法为策略筛选和反事实分析提供了工具，但仿真精度不能取代真实 GPU 上的端到端验证。实验还应分别报告工作流完成时间、有效完成吞吐、资源利用率、KV 命中与重算、CPU-GPU 迁移时间、工具等待和任务质量，从而避免以单一缓存指标或局部时延替代整体系统收益。

当前文献的硬件、模型、工作流类型、并发度和基线配置差异较大。不同论文中的性能倍数通常对应不同指标和实验负载，不具备直接横向比较条件。较为可靠的评估应在相同工作负载、模型与服务数据面下复现代表性策略，并明确区分真实在线信息、离线预测、人工提供的工作流元数据和 hindsight oracle。

六、研究趋势与现存问题

现有研究呈现出由请求级走向工作流级、由被动缓存走向预测式生命周期管理、由单独缓存策略走向驻留与执行联合优化，以及由逐 token 指标走向任务完成效率评价的趋势。与此同时，方法仍面临若干共性问题。

首先，工作流信息假设差异显著。部分方法依赖已知 DAG，部分需要应用提供工作流、会话或智能体身份标识，另一些则从提示词指纹或历史转移中学习。动态分支、循环和子任务派生使预测误差具有明显的任务相关性，单一全局转移模型未必能充分表达每个会话的状态。

其次，预测收益受物理执行约束。预取和换出均需消耗带宽、占用主机内存并与推理并行；如果预测动作未能在受益请求恢复前完成，缓存价值可能无法兑现，甚至增加队列和传输争用。因此，调度方法需要将传输服务时间、队列状态、动作取消及恢复成本纳入端到端分析。

再次，缓存命中并非充分的系统目标。缓存局部性策略可能将请求集中到少数实例或推迟当前可执行任务；增加并发也可能造成显存压力和重算增加。有效的系统评价应检验缓存收益是否真正降低完整工作流完成时间并提高成功任务吞吐。

最后，非模型组件和任务质量需要纳入统一评价。工具、sandbox、CPU 编排及控制通道可能成为关键路径；压缩或跨上下文复用还可能引入质量损失。若未区分这些因素，局部缓存或调度改进可能被错误解释为端到端服务能力提升。

结论

Agent 调度系统已从传统 LLM 请求调度发展为涵盖应用工作流、服务程序、GPU 计算资源及 KV-cache 生命周期的多层系统问题。现有方法分别在动态程序调度、工具等待期缓存保留、未来调用预测、缓存驻留与准入联合决策以及 KV 压缩复用方面取得进展。后续研究需要更加重视不同方法的信息接口与工作负载假设，建立统一且可复现的端到端评价，并在真实系统中量化预测误差、数据迁移和非模型组件对任务完成效率的共同影响。

参考文献

[1] From LLM Inference to Agentic Workloads: Characterization and Implications for Serving Systems. arXiv:2608.15127, 2026.

[2] Agentic AI Workload Characteristics. arXiv:2605.26297, 2026.

[3] Agentix: An Efficient Serving Engine for LLM Agents as General Programs. NSDI, 2026. Earlier arXiv version: Autellix, arXiv:2502.13965.

[4] SAGA: Workflow-Atomic Scheduling for AI Agent Inference on GPU Clusters. arXiv:2605.00528, 2026.

[5] Nalar: A Serving Framework for Agent Workflows. arXiv:2601.05109, 2026.

[6] Pythia: Toward Predictability-Driven Agent-Native LLM Serving. arXiv:2604.25899, 2026. Earlier version: Pythia: Exploiting Workflow Predictability for Efficient Agent-Native LLM Serving.

[7] LLM-as-Scheduler: Agentic Workflow Dynamic Scheduling. ACL, 2026.

[8] Serving Agentic Workflows with a Physical-Plan Compiler and Adaptive Runtime (Dyserve). arXiv:2607.02942, 2026.

[9] AgentServe: Algorithm-System Co-Design for Efficient Agentic AI Serving on a Consumer-Grade GPU. arXiv:2603.10342, 2026.

[10] SwarmX: Agentic Scheduling for Efficient and Low-Latency Agentic Systems. arXiv:2606.21401, 2026.

[11] Architectural Implications of Agentic AI Workflows. arXiv:2608.04458, 2026.

[12] Abhyankar et al. InferCept: Efficient Intercept Support for Augmented Large Language Model Inference. ICML, 2024.

[13] AugServe: Adaptive Request Scheduling for Augmented Large Language Model Inference Serving. arXiv:2512.04013, 2026.

[14] Continuum: Efficient and Robust Multi-Turn LLM Agent Scheduling with KV Cache Time-to-Live. arXiv:2511.02230, version 7, 2026.

[15] Astraea: A State-Aware Scheduling Engine for LLM-Powered Agents. arXiv:2512.14142, 2025.

[16] Pan et al. KVFlow: Efficient Prefix Caching for Accelerating LLM-Based Multi-Agent Workflows. NeurIPS, 2025.

[17] Bian et al. TokenCake: A KV-Cache-centric Serving Framework for LLM-based Multi-Agent Applications. arXiv:2510.18586, 2025/2026.

[18] ScaleSim: Serving Large-Scale Multi-Agent Simulation with Invocation Distance-Based Memory Management. arXiv:2601.21473, 2026.

[19] Efficient Serving for Dynamic Agent Workflows with Prediction-based KV-Cache Management. arXiv:2605.06472, 2026.

[20] Tiwari et al. CacheWise: Understanding Workloads and Optimizing KVCache Management for Efficiently Serving LLM Coding Agents. arXiv:2606.16824, 2026.

[21] Zhang et al. Learning Agent Execution for KV-Cache Management in Agentic Serving (CacheScout). arXiv:2608.14624, 2026.

[22] Ni et al. TOPAS: Workflow-Aware Prefix-State Scheduling for Multi-Agent LLM Serving. arXiv:2608.25523, 2026.

[23] Kang et al. ThunderAgent: A Simple, Fast and Program-Aware Agentic Inference System. arXiv:2602.13692, version 3, 2026.

[24] Chen et al. CONCUR: High-Throughput Agentic Batch Inference of LLM via Congestion-Based Concurrency Control. arXiv:2601.22705, 2026.

[25] AgentKV: Phase-Aware KV Eviction for Agentic LLMs. arXiv:2609.14872, 2026.

[26] Bian et al. TokenDance: Scaling Multi-Agent LLM Serving via Collective KV Cache Sharing. arXiv:2604.03143, 2026.

[27] IntentKV: Cross-Turn Intent-Aware KV Cache Pruning for Agent Inference. arXiv:2606.09916, 2026.

[28] UltraQuant: 4-bit KV Caching for Context-Heavy Agents. arXiv:2606.20474, version 3, 2026.

[29] Wang et al. SMetric: Rethink LLM Scheduling for Serving Agents with Balanced Session-centric Scheduling. arXiv:2607.08565, version 2, 2026.

[30] Rajib et al. AgentServeSim: Serving-System Simulation and Policy Search for LLM Agent Programs. arXiv:2606.09613, version 3, 2026.
