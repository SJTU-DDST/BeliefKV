# BeliefKV 当前系统设计

更新日期：2026-10-10

状态：本文是当前算法与系统边界的权威说明。历史版本保存在
`docs/archive/snapshots/beliefkv_design_2026-07-14_zh.md`。

## 1. 研究场景与目标

BeliefKV 面向单 GPU、HBM 受限的动态 Agent 工作流：

- 多个 root workflow 并发运行；
- workflow 在运行时产生工具调用、FRESH subagent、RETURN、JOIN 和 peer message；
- 系统不要求应用预先提供完整 DAG；
- GPU KV、CPU KV 和 raw-token recompute 可以共同参与容量管理；
- SGLang RadixCache/HiCache 仍是物理 KV 与 allocator 的唯一事实源。

主要优化目标是**相同到达流下的成功 workflow 吞吐及 JCT 分布**；
任务正确性、无界饥饿和物理容量安全是不允许交换掉的约束。
GPU service utilization、action throughput、action unlock、reentry/admission
stall、D2H/H2D、recompute 与控制面开销是用于解释因果和判断退化的指标，
不能仅凭 GPU 利用率或预取次数宣称性能收益。

Workflow fairness 只作为有界防饿死和最终 tie-break，不以平均分配 GPU 时间为目标。

### 1.1 当前阶段目标：相对 native 的可验证性能提升

**当前主目标：**在固定 workload、模型、容量及到达流下，通过
agent 调度、共用路径减负、predictive H2D 与 `PREPARE_HOST`，
使 predictive 相对 native 取得可核实的完成吞吐与 JCT 改善。
当前 active /goal 保持该方向，以下三项是同一目标的执行要求：

1. 缩短恢复就绪到首次服务的等待，减少服务前重复加载；同时核对
   缺失页、必要状态、准入等待及其他 workflow 的延迟。
2. 补齐 PREPARE 消费归因，区分压力释放、恢复、Host 驱逐后补传
   与未观察到消费，减少无效备份和反复回收/补传的成本。
3. 提高有用 FULL 预取覆盖，验证 handoff 能否替代需求恢复，
   并以实际首次复用、吞吐、JCT 与重算评价收益。

FULL 的固定前缀可复用有效 Host 副本。PREPARE 只复制缺失
extent，新增前缀增量备份；radix 分裂保留原生 Host 索引映射。
若已有副本被 Host 驱逐，后续必须重新备份该段。后续同节点、
同池 D2H 仅为关联证据，不能据此认定覆盖有效副本或精确重复
字节。V10 的 35066 次关联均有中间 FULL Host 驱逐，支持
回收后补传；详细归因见架构状态页与固定审计报告。

投机 PREPARE 不携带 Mamba。运行请求的状态持续更新，固定
前缀的检查点则是版本化快照；真正卸载与恢复时，仍由原生
write-back 和恢复依赖保存、加载必要状态。关闭提前备份不能
删除恢复所需状态，也不能将全部 Mamba 驱逐视作无成本。

预测器负责从已观察的 child 通知、正文及 decode 进度估计阶段
和剩余工作，工具头估计工具返回；runtime 决定传输对象、物理
范围与时机。未来首次服务时刻不进入预测输入，模型不学习
离线 trace 中不可识别的预取净收益。准确率、真实传输提前量、
ACK 后消费与端到端收益分别评价。

滚动预测以独立请求的首次可执行触发评价，保留过早触发、工具
轮次误报和无触发；不能只报告末段快照或正确触发的时间中位数。
V16b的4/8/16/32-token窗口CPU候选未取得足够首次触发精度，
不替换当前产物。工作量倒计时与已观察EOS分别检验，常数偏移
只在独立拟合/检验事件中探索，不给所有滚动预测统一加几秒。
请求body发送到原生登记和登记到首次服务各自测量，不能将入口、
tokenizer、IPC和原生准入都当成H2D耗时或RETURN模型误差。

Qwen3.5纯文本请求复用Jinja模板已经编码的输入token，保留模板
缓存，避免多模态模型声明造成的decode/再encode；真实媒体
输入保持原有processor路径。原生与predictive共用此入口优化
和每请求计时；时间随原生IPC携带，报告body发送、API转换、
tokenizer完成、dispatch、scheduler接收、队列登记与首次服务，
不把入口等待并入child剩余工作标签或仅靠扩大驻留锁掩盖。

动作必须有真实迁移需求：PREPARE 的候选需要未来卸载机会，
H2D 需要有效 Host-backed 缺失前缀及必要当前检查点。按池核对
Device/Host 余量，不用总体显存占用代替可行动容量，不为制造
机会主动驱逐有效 Host 副本。已有完成副本、未锁定冷等待 KV
可在真实分配压力下由原生回收；不抢正在执行的热 KV。
预取保护及准入提升有界，并计入新输入、decode 增长和状态
预留、普通请求老化及其他 workflow 尾延迟。

真实 child RETURN 后，结束上下文的无引用私有后缀可直接交还
原生 allocator，无须额外 D2H。已提交且 JOIN 满足的 parent
可在四次普通准入额度后使用下一批恢复 slot：原候选中没有
该类 parent 时，有界检查最多八个关联 parent，选取至多一个
安全 checkpoint、实际恢复 extent 适配当前 free lists 的对象。
数据依赖和首次消费前驻留仍由原生核验，其他可服务请求继续
填充 batch。这个返回后选取与需求恢复不计为预测 H2D；较早
sibling 的真实释放供最终 child 返回前预取使用时，仍按独立
完成边界、实际 submit/ACK 和 FULL 复用证明计算预测覆盖。

已提交请求的 execution handoff 按实际输入规划需求恢复；
child/tool 返回前的预测 H2D 单独计数。前者已有物理 ACK 与
FULL 首次复用证据，不能计作提前预测收益。完整 JointPlan、
主动 COMMIT 与 running retraction 的缺口仍单列为后续工作。

child→parent 容量交接由权威 RETURN 与原生物理引用共同驱动。
通知阶段复用现有 parent 安全 checkpoint/缺失页规划；RETURN
后，在 scheduler 内释放已结束上下文的 child session 引用，并直接
回收无共享引用、无设备/Host 锁、无在途 DMA 的私有终态叶及
经逐节点验证的私有祖先，不为无用后缀新增 D2H。闭合 generation
来源处理 close/RETURN 顺序，session 重开失效旧来源。FULL、
Mamba 和 parent 新输入/decode 增长分别核算，不能按 child
完整上下文推算释放量，也不要求 parent 使用相同物理页号。
Deep Agents 的 persistent 标记用于任务内部多轮执行；完成回调
自动声明 context_retired，复用现有 RETURN 后关闭 session 的
生命周期，不要求模型输出新增字段。同一 context 有其他活跃
invocation 时保留；原始客户端事件时间与 scheduler 交付观察
墙钟分别记录，容量交接时延用同一墙钟计算。

实际释放容量进入原生 allocator，可供既有预取与需求恢复使用。
JOIN 解锁 parent 的缺失 checkpoint 恢复只在近期可准入候选内
有界优先；JOIN 满足、下一请求已提交且必要数据驻留后，再与
恢复页租约和普通请求老化共同决定优先准入。不存在未提交
parent 长期独占容量或模型预测许可释放运行中 child 的路径。
较早 sibling 释放可支持真正边界前 H2D；最后 child 释放后才
启动的恢复属于需求 handoff。终态分池释放与 parent 首次观测
完成 batch 独立记录，不能把容量关联当成预测复用或 JCT 改善。

V15 历史比较配置为 156 个任务，108 个在 t=0 到达、48 个在
3600 秒后到达，running=48、NUMA1 Host200GB/FULL:Mamba80:20、
HBM Mamba/FULL=0.9、context131072、completion8192、
graph2048/reserve32、workflow14400s，seed21/temperature0。
首轮约束为 2--4 child，后续轮次仍由 prompt 与模型执行决定。
native 关闭 BeliefKV 控制与优先策略，保留双方一致的协议兼容、
客户端优化及遥测；它不是未修改的上游 wheel。

V15 从冻结提交 `feb5ee01a9f1340a694dcba442c439d08e4bd274`
运行，其最终比较与高负载退化见架构状态页。当前推进 V19b
终态容量交接：运行中的主目录、引擎与预测产物保持冻结，
新修订先在隔离分支提交，再按已授权的快速开发节奏停旧采集、
保存截断证据并冷启动新版。仍需完成稳定同版本 native 对照，
目标尚未达成。completed 是运行终态，任务正确性另行评价。
正式实验多轮取平均，固定需求 GPU 回放不是主线前置条件。
实际实现、最终数据与未完成项以架构状态页和执行计划为准。

主指标为完成吞吐、JCT 和 completion curve；同时报告实际
工作量、任务正确性、GPU 服务、缓存命中、重算、按来源分池
传输、驻留字节时间与公平性。CPU wall 区间、传输累计时间或
更多 ACK 不自动构成端到端节省。若计算、Host thrash 或工具
长尾主导，需报告收益边界，并降低缺乏后续消费的动作成本。

### 1.2 可证伪的研究假设与对照

- **HBM 有可行动余量**：工具等待或 JOIN 提供足够 lead，且未来
  卸载会消费提前备份、未来服务会消费提前恢复的 KV 时，
  `PREPARE_HOST`/predictive H2D 相比同到达流 native 可缩短同步
  D2H/H2D 停顿；计入无效备份、提前驻留字节时间及 PCIe 干扰。
- **有冷 KV 可置换**：当 free-list 不足但可安全收回更冷页时，
  在有界 lease 下预取关键路径 parent 或下一 agent；将 victim
  后续 miss、其它 workflow JCT 和尾延迟纳入成本。缺乏冷页时
  放弃替换。联动执行顺序/回收的 handoff 另作消融，不用它掩盖
  PREPARE/H2D 自身是否有净效益。
- **过载、计算或 Host 容量主导**：若增加 H2D/驻留无法转化为
  真实消费或节省阻塞，应回退到响应式路径，并报告机会缺失；
  不将此压力档的 speculative H2D 数量视作成功。

各压力档必须由训练工作负载中的**物理状态**划分：runnable backlog、
可回收/锁定的 FULL 与 Mamba 字节、Host 有效副本和驱逐后 miss、
GPU 服务饱和度、传输队列及重叠余量。根数只用于重复配置，不用作
压力标签；同一模型、Host/NUMA、HBM/graph、runtime 和到达流
下比较 P5、PREPARE、H2D、闲置容量使用、冷页替换与可选联合 handoff。
分档门槛与动作预算只在训练项目冻结，项目隔离测试不回调参数。

与已有 Agent-aware offload/predictive upload（TokenCake,
arXiv:2510.18586）及 next-step KV prefetch（KVFlow,
arXiv:2507.07400）相比，不能把「提前迁移」本身作为新颖性主张。
待验证的区别是**在线因果 frontier 和 FULL/Mamba 物理余量共同
决定哪笔 KV 值得提前备份/恢复、是否替换冷页、何时准入执行**，
在缺乏可靠精确时钟或有用物理机会时有界地放弃动作。联合
handoff 是可选增强，不能作为尚未验证的当前收益。

## 2. 当前核心设计

![BeliefKV 请求与 KV 联合调度](figures/beliefkv_joint_algorithm_overview.svg)

本节及第3-4节描述目标联合架构的合同，不是新版全部已上线的
能力清单。当前执行通过native causal admission和有界物理动作
接入SGLang；完整JointPlan、COMMIT与running retraction的迁移
缺口见第7节。图中的旧P5/P6路径同样不能代替新版实现证据。

BeliefKV 使用两个相互正交的状态视图：

- RCCG 描述 Agent 的因果和执行关系；
- PageIndex/Radix 描述 KV 页的物理共享、驻留、锁和 generation。

目标架构在 JointPlan 中结合二者：

```text
runtime events -> RCCG causal frontier
                          \
                           -> JointPlan -> admission tickets -> GPU batch
                          /
Radix/PageIndex -> physical bundles
```

JointPlan 同时决定：

- 哪些 runnable request 优先获得 GPU service；
- 哪些 waiting request 获得当前 batch epoch 的 admission ticket；
- 哪些 KV 保留、迁移、恢复或丢弃；
- admission 是否依赖某笔 reclaim/restore ACK；
- 是否需要 selective running retraction。

## 3. P5：Observed-State JointPlan 目标合同

### 3.1 请求调度

P5 只调度已经由 RCCG 证明 runnable 的 invocation。正常排序综合：

1. starvation floor；
2. JOIN straggler 和下游 action unlock；
3. causal MaxWeight utility；
4. resident-ready bytes 与 startup cost；
5. workflow fairness tie-break。

系统不限制每轮每个 workflow 只能选择一个请求。同一 workflow 的多个 child 可以在资源
允许时共同进入 batch。

请求始终保留在 SGLang visible waiting queue。BeliefKV 生成短期、单 epoch 有效的
AdmissionTicket；没有 ticket 或等待 restore 的请求在本轮被跳过，但不能阻塞后续可运行请求。

同步路径使用 O(K) bounded seed，复杂语义规划在异步 worker 中运行。计划必须在 scheduler
safe point 重新验证，SGLang PrefillAdder 和 allocator 保留最终否决权。

### 3.2 Residency 分类

| 类别 | 含义 | 默认处理 |
| --- | --- | --- |
| `PINNED` | running、engine lock、active reader 或事务占用 | 不迁移 |
| `IMMINENT` | 已 ready 或即将 reentry | 保留或优先恢复 |
| `PARKED` | WAIT_TOOL/CHILD/JOIN/MESSAGE | 可作为 victim |
| `DEAD_UNOWNED` | 无未来 owner | 直接释放 |

共享页使用所有 owner 中最强的保护等级。逻辑 context 不能直接作为迁移单位；系统必须先解析
为包含共享页和 ancestor closure 的 PhysicalBundle。

### 3.3 响应式 HBM 释放合同

真正释放 HBM 的 `COMMIT_CPU` 由已经可观测的 beneficiary HBM deficit 触发：

```text
deferred beneficiary
  -> startup + restore + growth deficit
  -> select PARKED/DEAD physical victims
  -> COMMIT_CPU(victim) or DROP(victim)
  -> completed reclaim ACK
  -> ADMIT(beneficiary)
  -> first real GPU service
```

高 HBM 使用率本身不是迁移理由。系统要求存在明确 beneficiary，并且估计的 saved admission
stall 大于 D2H、未来 restore/recompute 和干扰成本。

### 3.4 Restore 与活性

Running retraction 使用 durable RestoreTransaction：

```text
WAIT_FEASIBILITY -> WAIT_FUNDING -> READY_TO_COMMIT
 -> H2D_QUEUED/ADOPTED -> H2D_ACKED
 -> ADMISSION_RESERVED -> ADMITTED
 -> SERVICE_GRACE -> SATISFIED
```

普通 waiting prefix 不得升级为全局 restore barrier。显式 H2D 不可行时回退到 SGLang
native demand-load；Host copy 不可用时允许从 raw tokens 重算。Restore 只有在请求重新获得
至少一个真实 GPU service quantum 后才完成。

## 4. 物理 KV 数据面

- SGLang/Radix/HiCache：真实 KV、allocator、DMA 和 lock 的 owner。
- PageIndex：按 `(page_id, allocation_generation)` 维护物理镜像。
- PhysicalBundle：共享页和 closure 完整的迁移单位。
- Transfer transaction：D2H/H2D/DROP 的提交、ACK 和终态。
- JointPlan：只表达语义动作和依赖，不直接修改 tensor。
每个物理动作都必须在 safe point 检查：

- context/request epoch；
- Radix ownership、ancestor closure 和 allocation generation；
- engine lock、semantic pin、active reader；
- HBM/Host 实际容量；
- transfer capability 和 in-flight conflict；
- beneficiary、deadline 和动作证书。

## 5. 新版事件与内容驱动的预测旁路

v8c reactive原生迁移数据已完整采集，但D2H在途split ACK的
旧节点集合校验错误禁用了PREPARE通道，不能证明完整策略baseline。
修复按native拓扑/代次与原Host目标索引验证发布节点扩展，不放松
字节、pool、session/epoch和重放约束。后续v8d是修复版本的108-root
predictive开发诊断，沿用冻结预测头；不将跨版本轨迹当作公平对照，
模型仍只预测事件/工作量，runtime决定动作。

2026-10-07当前v8c诊断使用108-root单波及每轮2–4 native task
调用，保持running48与池配置。这是新压力/fanout组合，不自动
外推v7校准。JOIN恢复要求完整ALL成员集只剩一个未完成child，
不能用首个child通知提前唤醒parent。工具CPU并发、GPU无服务
间隔及任务大小变化须按新trace分别评价。
新的旧输入前缀未命中代理排除新输入，Host块归因完整探测原有
有界索引；终态leaf ancestry/锁/refs及close ACK时间独立记录。
关闭引用不等于释放缓存，祖先可能仍共享，不盲目D2H无用child
或DROP共享活页。H2D计时预算不是包括调度/重算的完整oracle上限。
首次v8因auto首轮绕过委派已停止。v8b后置专用委派prompt，
首轮命名task且显式并行；仅在BeliefKV ingress对task子集使用
可重复required约束，避免本机命名工具语法硬性限制为一个调用。
完整工具prefix不变，但v8b仍108个首轮全部单调用，已停止。
v8c首轮通过RepeatFormat在生成时限定2–4，模型选择数和内容；
后续轮次保持prompt驱动并记录实际次数。不在输出后拒绝回复，
不补造child，也不把首轮约束当成所有后续轮次均2–4的证据。

预测旁路不建立第二个调度器，也不控制agent是否可以RETURN。
当前配置使用冻结MiniLM encoder及phase/work头、独立工具事件
时间模型和实测传输服务估计；运行中不切换权重或重新校准。
旧Qwen3 FrontierBelief schema-v5的数值、动作资格与完整
RECLAIM_AND_PREFETCH机制属于历史参照，原文保存在
`c219604:docs/beliefkv_design.md`，不能作为新版能力证明。

### 5.1 模型输入输出与运行边界

child模型输入为已经送达的有界正文语义、正文字符数、已生成
token数、历史工具/模型轮次、完成通知及其报告长度提示。
native工具标记另用于使当前轮的终态信号失效。语义推理在
独立CPU process中运行，scheduler消费有身份与时间证明的结果。

输出分为 `continue_work`、`completion_notice`、`final_report`
阶段分数，以及条件剩余生成token的下界、中心和上界。
校准区间不自动等于正确的P10/P50/P90，状态单独保存。
模型本身不直接授权物理动作，也不把未来GPU准入时刻作为输入。
runtime依据实际decode进度和已观测服务估计滚动换算近端时机，
前EOS使用已有工作上界；无工具EOS仅保留短协议窗口。

工具模型输入包括当前等待elapsed、角色、工具/backend/command
类别与上下文历史，输出残余时间及事件CDF。当前准入使用配置的
残余P50窗口，H2D准入中CDF保留诊断；长等待回收仍使用条件CDF。
v5证实两个口径可同时将同一目标判为冷victim和预取beneficiary，
导致ACK后再次回收。新代码以同一生存条件CDF逆算残余P50，
并在ACK后建立短期策略租约，排除自身冷回收直到服务/失效。
不是容量预留或全局pin，原生压力驱逐仍允许且须明确归因。
v6的7个JOIN H2D已保留并首次复用，无自身再次回收；
工具H2D为0，不能据此完成工具链路GPU验收。
端到端净收益仍未证明。并行工具依赖未满足时不提前唤醒。
传输模型使用实际FULL/Mamba形态及相近大小的样本，分别估计
enqueue-to-submit和submit-to-ACK，不线性放大固定ACK开销。

runtime才决定是否PREPARE或H2D：检查下一输入可复用的安全
checkpoint、有效Host副本、FULL/Mamba物理容量、当前因果状态、
传输时间及驻留机会成本。源副本可以来自native D2H或PREPARE。
阶段模型、旧模型的eligibility字段与物理动作授权相互独立；
不通过改写false标志开放旧全套迁移路径。

当前实现将语义对象与物理范围进一步解耦：动态workflow的工具和
JOIN事件决定“谁即将恢复”，原生有效输入前缀及缺失page/extent
决定“传多少”。FULL祖先只搬缺失FULL，当前安全检查点才携带
必要Mamba状态；本context历史状态引用可释放，其他共享/锁定状态
仍由原生缓存管理。ACK后的有界原生驻留保护、真实就绪到提交
宽限及恢复优先准入共同连接预取和首次服务，普通请求以共享
提升预算和老化限制保护。原生v9结束并导出HTML后已合入，
尚无新GPU净收益证据；模型不负责选择物理范围或判断净收益。

进一步将短期执行顺序与KV恢复联合：scheduler在batch选择之前
确定已提交、可执行的下一候选，并用真实输入读取安全检查点。
优先服务同因果层级中KV已在HBM的请求，给候选的缺失FULL前缀
与必要当前状态提供提前换入窗口；JOIN解锁parent的既有有界提升
与普通请求老化仍保留。真实容量不足时，只停放已有完成Host副本、
未锁定的冷等待agent或无引用缓存，不撤下正在执行的热KV。
备份与恢复可在不同节点同时在途，HBM目的空间必须在实际分配
时已可用，不能根据尚未完成的D2H预测释放容量。
此execution handoff不依赖模型学习净收益；它补充边界前预测，
不替代或伪装为准确预测RETURN。按来源独立计数，要求FULL实际
复用、原生需求恢复减少及端到端效果形成证据。
后续Host FULL:Mamba默认80:20，Device Mamba/FULL字节比例0.9
不变；该配置须经新的物理容量census核对，不宣称已证明最优。

v6已有7个JOIN H2D ACK与FULL首次复用，全部发生在EOS后；
工具H2D为0。单轮吞吐高4.62%但实际工作量更少。端到端吞吐净收益
仍未证明，不能据此证明普遍准确的RETURN预测或完整JointPlan已迁移。

工作头先修正区间语义：旧产物先非负截断再加绝对token margin，
会在末段保留固定正数下限；新候选先在signed residual上扩张
再截断，并重新按workflow校准，旧产物不默默换语义。
随后利用真实100 ms正文/进度观测比较log-work分位数头。
phase/encoder/phase threshold冻结，未来RETURN和服务只用于标签，
不作为在线输入。Runtime显式选择center/upper时机，旧默认upper；
不是用整段保守区间否决全部近端动作，也不把中心为零当作EOS。
首次触发过早、工具轮次误报、区间覆盖与真实预取提前量须分账，
不能只按整体MAE选模型。
正常native stop、已观察到非空正文且无工具标记时，协议窗口
使用独立观测证据，不强制等到NN forecast或TPS可估计。
该阶段只用于H2D，不改变agent终态或收尾准入优先级；仍验证
完整ALL JOIN关键child、Host安全副本、身份与容量。
空白/reasoning-only、length/abort和internal不能作为此证据。
观测窗口与前EOS模型触发分开统计，协议加载不是预测精度证明。

### 5.2 模型预测与 runtime 决策的分离验收

**事件与剩余工作头**仍单独评估工具 release 与完整 JOIN 的预测：使用项目隔离的
自然事件，按触发阶段、压力桶报告覆盖、误报、条件 P50/P90 误差和删失，
不只在事后成功的长窗口子集计算精度。JOIN 不等同于单个 child RETURN；
有多个未完成 child 时需按实时 blocker 集合组合；只剩最后一个 child 时
可把结构化终态提示作为近端信号，但不能把其发出视为已确认 RETURN。
继续追求有用的亚秒级时间精度，但不能把该阈值当成每次预取获益的必要条件，
也不能用只覆盖少数近终态 JOIN 的条件误差替代整体表现。

**runtime动作决策**判断当前可见状态下某笔迁移是否值得执行，
不是让模型学习离线trace不可识别的反事实净收益。令 `R` 为
JOIN真正满足、parent可提交的时刻，
`C` 为同一物理 KV/epoch 的有效 H2D ACK：完全隐藏传输要求 `C <= R`，
且 `R-C` 不超过受 HBM 机会成本约束的驻留预算；部分完成只计实测可节省的
等待。即便 `C <= R`，若没有被首次服务实际消费或挤掉更高价值 KV，也
不得算 useful。受益必须由 request 级 ACK/extent、实际首次服务及同配置
reactive 对照验证；不能拿 reactive 的长排队窗口当作预测式 H2D 的节省。

以下冷页抢占与handoff为目标合同，不是当前已上线策略：
parent 的关键路径价值以剩余 blocker、下游解锁、当前工作流进度和真实
可回收物理 KV 表示，而不是“所有 parent 永远高优先级”。同一 JointPlan
比较空闲空间、可安全置换的冷页以及其它 runnable 工作；只在可用 Host
副本、closure/owner/epoch 与容量证书成立、预计解锁收益覆盖搬运/置换/
未来 restore 或 recompute 成本时，给 parent 短期驻留租约。租约有
字节/时间预算、到期或状态变化撤销、防饿死界限；ACK 未到不得按已恢复
KV 准入。JOIN 之外的 admission handoff 也按相同规则，在选出下一
beneficiary 后先预留空间、协调 D2H/H2D，再给执行 ticket。

child RETURN 时间拆解为可观测的执行阶段/剩余 GPU 工作、未来 GPU
服务份额、排队和工具执行，不把未来排队时长作为在线特征。训练时可以
用有身份关联的 request 服务事件重建**实际发生的**服务时间和排队时间，
检验“剩余工作/服务需求”头是否跨压力更稳健；工具等待对真实 RETURN
仍有贡献，不能一律从标签中删除。预测调度会改变服务分配，需在
reactive 与 predictive 下分别校准/按压力分层，之后只用历史已确认事件
滚动校正；在线更新必须经过因果时序、漂移及安全回退门禁。
当前条件工作头已使用剩余token标签；跨调度的未来服务份额、
排队分头及在线更新仍未验证，不能声称已消除RETURN墙钟波动。

不将 JOIN/工具墙钟点预测达到亚秒级设为物理实验的先决条件。
利用确定性frontier、已观察的临近事件和保守时机进行当前单pair
开发A/B；不以canary或固定需求GPU回放作为前置步骤，正式实验
再采用多轮配对取平均。运行中的源码、prompt、权重和参数冻结。
时间模型单独按全体与条件覆盖验收，未获项目隔离验证的头不得作为
无回退的物理门禁。
不基于正在运行的密封留出集调整压力阈值、ETA、模型或准入门禁。

## 6. 未来可选方案：Predictive Eviction

### 6.1 动机和当前缺口

当前主线实现有界native predictive transfer/shadowing，不是
完整predictive eviction或新版完整COMMIT/JointPlan：

```text
PREPARE_HOST: GPU_ONLY -> GPU_AND_CPU_SHADOW
COMMIT_CPU:   GPU_AND_CPU_SHADOW -> CPU_ONLY
```

前者提前消除未来D2H成本，但不释放HBM；上述COMMIT转换是算法合同。
新版当前的等待态回收由真实allocator短缺触发，要求备份已ACK、
独占且未锁定，不等于完整旧COMMIT事务已经迁移。如果竞争工作
中的收益来自预测式提前释放HBM，当前不能声称已覆盖该来源。

### 6.2 可选的两阶段算法

未来可以在不改变现有安全边界的前提下增加 `PREDICTIVE_COMMIT_CPU`：

1. PCIe 有余量且预测等待窗口足够长时执行 `PREPARE_HOST`；
2. CPU shadow 完整后持续观察 projected HBM timeline；
3. 在 `latest_safe_commit_time` 到达时重新评估；
4. 满足风险和收益门槛才解除 GPU residency；
5. 预测失败时使用已有 H2D、native demand-load 或 recompute 恢复。

提交条件至少包括：

```text
shadow_complete
AND victim is still PARKED and unpinned
AND P(wait remains open beyond commit/restore horizon) >= p_min
AND projected_hbm_deficit > 0
AND concrete/projected beneficiary exists
AND expected_saved_stall
    > expected_restore_or_recompute_debt + transfer_interference + risk_margin
AND live causal/physical certificate is fresh
```

### 6.3 与现有 P6 的关系

该方案复用现有 FrontierBelief、beneficiary-bound package、latest-start、PhysicalBundle 和
safe-point transaction，不新增独立 eviction scheduler。建议把它保留为未来可选实验分支，
而不是当前论文主张，直到满足以下门槛：

- 现有 `PREPARE_HOST` 在自然 workload 中出现可归因的 useful shadow；
- trace 中存在“响应式 COMMIT 已经太晚”的 admission stall；
-离线/影子重放显示 predictive commit 相比 reactive commit 有稳定正收益；
- 有界配对机制验证没有显著增加反向 H2D、recompute 或 HBM-time 浪费。

必须报告 useful/wasted commit bytes、提前释放的 HBM-time、beneficiary saved stall、
restore/recompute debt、方向反转率以及最终 workflows/hour。

## 7. 实现边界（旧 P6 路径及新版缺口）

下表以当前Qwen3.5/SGLang 0.5.20的有界native路径为准。
旧Qwen3的完整P5/P6能力属于历史参照，不代表新版已经迁移。
模型输出不是物理授权，也不需要学习离线不可识别的预取净收益。

| 能力 | 当前状态 |
| --- | --- |
| 动态 RCCG 与 FRESH subagent/JOIN | 新版已接入，summary与并行工具归属已修复 |
| Native causal admission | 新版已接入；allocator最终决定准入 |
| FULL/Mamba物理闭包、身份与ACK | 有界单node原生事务与首次消费证明已接入 |
| 预测器 | 冻结语义phase/work + 独立工具残余时间/CDF；runtime独立选动作 |
| Predictive `PREPARE_HOST` | JOIN/长工具已实际运行，备份后真实压力回收；净收益未证明 |
| JOIN `PREFETCH_GPU` | v15有99次ACK，FULL传输/确认首次复用0.893/0.743GB；净收益待完整对照 |
| 工具 `PREFETCH_GPU` | v15有77次ACK，FULL传输/确认首次复用0.338/0.293GB；仍有未保护驻留丢失 |
| 原生D2H副本恢复 | 同样可用，不强制依赖先前PREPARE |
| 新版 execution handoff | v15需求恢复15132次ACK，FULL传输/首次复用67.205/67.047GB；不属于提前预测 |
| 完整COMMIT/JointPlan | 尚未完成新版执行/ownership与收益验收 |
| Running selective retraction | 新版完整适配仍缺失，不开放旧全套物理开关 |
| 新版有界恢复驻留与准入提升 | 已接入；锁保护、准入长尾及其他workflow代价继续审计 |
| 新版 child 工作/服务/排队分解与在线更新 | 待训练侧可识别性验证，尚未上线 |
| Peer multi-agent 专项优化 | 非当前关键路径 |
| Oracle action-space 优化 | 已暂停，仅保留诊断资产 |
| Morphology 独立策略 | 已降级；shape 仅作 transfer cost/OOD 输入 |

v4的吞吐负结果不能一句归于模型路径差异。已确认最后一个
workflow的两次600秒全量测试造成长段无GPU请求；管道上游错误
可能被tail的成功退出掩盖。CPU inspection和有请求阶段的GPU
利用率差距仍需profile，不能把scheduler墙钟interval当kernel
时间，或把所有uncached input都当重算。
v5的利用率不再低于reactive，但整轮吞吐仍低8.95%；
真实工具预取被相反的pressure策略抵消。模型只预测时间/工作，
runtime必须使预取目标、回收候选与短驻留生命周期保持一致。

## 8. 不变量

- 不要求预定义 DAG，但要求最小运行时因果事件。
- 不根据 prompt 文本猜测 parent/child 身份。
- SGLang allocator 和 Radix 始终具有最终权威。
- 计划字节不能代替 completed ACK 的实际释放字节。
- 预测动作不能绕过 P5 correctness 和 liveness。
- 不把旧策略的 wall-clock RETURN 误当调度不变的纯服务需求；未来服务/
  排队分头必须使用因果可观测的历史输入，并显式报告压力与策略分布差异。
- 正式性能比较必须使用相同 workload、模型、runtime profile 和 instrumentation。

## 9. 当前实验环境

H200 NVL单卡、Qwen3.5-35B-A3B BF16、SGLang 0.5.20，
同一 `beliefkv-next` 环境运行serving与agent实验。
Device为FULL约36.843 GB/Mamba约33.096 GB，
Host为NUMA node 1的约200 GB，FULL:Mamba按80:20分配；
Host和Device不采用相同比例，容量以实际启动census为准。
running=48、context=131072、completion=8192、workflow=14400秒，
graph=2048/预留32步、宽松native-reactive profile、自然语言终态。
当前比较为V15 native/predictive、156任务两波到达(108+48/
3600s)。两侧冷启动服务和缓存，模型、脚本、共用协议与容量冻结；
详情及启动SHA见架构状态页和当前执行计划。

旧Qwen3/0.5.2rc1的冻结基线仍保存在
`configs/p6/h200_bf16_v7/frozen_runtime_profile.json`，不是当前默认。

## 10. 代码入口

- RCCG：`beliefkv/control/causal_graph.py`
- 新版接入/准入：`beliefkv/runtime/sglang_v0520_runtime.py`、
  `beliefkv/runtime/sglang_v0520_admission.py`
- 原生session/物理动作：`beliefkv/runtime/sglang_v0520_sessions.py`、
  `beliefkv/runtime/sglang_v0520_physical.py`
- JOIN与预测：`beliefkv/runtime/sglang_v0520_join_projection.py`、
  `beliefkv/runtime/sglang_v0520_prediction.py`
- 语义phase/work：`beliefkv/predictor/child_semantic_work.py`、
  `beliefkv/runtime/semantic_report_worker.py`
- 工具与传输服务：`beliefkv/runtime/tool_wait_shadow.py`、
  `beliefkv/runtime/native_transfer_service.py`
- 原生遥测：`beliefkv/runtime/v0520_native_telemetry.py`
- 旧算法参照：`beliefkv/policy/joint_scheduler.py`、
  `beliefkv/runtime/sglang_v052rc1.py`、
  `beliefkv/runtime/restore_obligation.py`，不等于新版迁移完成。

## 11. 文档权威顺序

1. `docs/README_zh.md`：统一导航和生命周期。
2. 本文：当前算法与系统设计。
3. `docs/architecture_status_zh.md`：当前代码与实验状态。
4. `docs/implementation_plan.md`：当前执行顺序。
5. `docs/experiments/`：不可变实验依据，不直接代表当前设计。
6. `docs/archive/`：历史计划和旧权威文档快照。
