# 外部 Baseline 复现进展

2026-10-25，America/Los_Angeles。

执行顺序保持 ThunderAgent、KVFlow、TokenCake，然后才继续 Agentix。
AgentX 是共同的发布轨迹负载，不是一条调度 baseline。所有新运行读取相同
18 个完整会话和相同 token 包，不采集替代 native 源，也不启用备用负载。

当前运行统一称为“ThunderAgent 复现”，负载另列为 AgentX。复现对象是
ThunderAgent 在共同 SGLang 数据面上的策略适配，Agentix 仍是独立且暂缓
启用的方法。当前运行不混入我们的 JOIN 优先、预取、备份或短工具续接策略。

最新覆盖登记：V220f 已完成全部请求，但共用重排遥测的 pending 重复登记
使收尾检查失败，仍保留为未验收轮。已停止并清理旧 V224 启动及等待中的
V225/V226，以修复后的 V220g → V224b → V225b → V226b 重新登记。
V220g/V224b 的策略和共同工作量未变；后两项只是 Agentix 的模型及小池
组件验证，尚非正式 baseline。最新共享 CPU 回归 1091 项通过，失败复现
和旧 GPU 工件均保留。详见文末新增收尾记录及 Agentix 适配记录。

## ThunderAgent 的动态记账修复

V219 在正式请求开始前因注册表缺少容量字段停止，字段已补齐并用实际
服务配置核对。V219b 已进入真实 GPU 运行，随后发现流式适配缺口而停止，
自有进程清理完成，所有原始日志和停止原因保留。它不能作为合格性能对照。
观察到的公开 1800 秒强制恢复仍保留在日志中，不能在修复后实验之前将其
归因为 ThunderAgent 本身的性能缺陷。

作者代理层在流式输出中增加 program token 占用。旧移植只在请求完成时
更新，因此原有 1000 个静态状态对照不足以证明这条动态路径正确。

修复在新 program 到达和既有 5 秒控制点读取原生请求已经生成的输出数量。
没有逐 token 新增控制任务，也没有 GPU 同步。原生 retraction 后更新
请求身份，保留原到达时间、program 服务状态及请求完成计数。
实际状态必须声明
`observed_native_outputs_at_arrival_and_five_second_control`，旧实现不能
通过当前复现验收。每轮缓存容量诊断也只在 5 秒控制点更新。

新增对照交错调用作者的流式增长、完成和容量控制函数，共 40,000 次控制
状态比较一致，另保留原 1000 个静态状态对照。该结果验证组件语义，不代表
完整 GPU 性能已经验收。

- `experiments/reports/thunderagent_streaming_arrival_author_fidelity_20261025.json`
- 修复后的正式运行：`experiments/raw/agentx_thunderagent_v219c_20261025`
- 策略文件：`beliefkv/policy/paper_baselines.py`
- 接口文件：`beliefkv/runtime/paper_baseline.py`

V219c 已完成 18 个会话、2229 次请求；输入 189039040 token，输出 2114833
token，与固定 token 包完全一致。完整平均 JCT 为 8818.270519 秒，全程
输出吞吐为 167.521119 token/s，正常长尾全部计入。原生两个新鲜空闲遥测
快照已收集，自有进程清理完成。复核工具重新校验请求回执、完整事件、
生效策略、冻结引擎、最终遥测及关闭记录：
`experiments/reports/thunderagent_v219c_complete_review_20261025.json`。

该轮记录 32 次作者已有的 1800 秒超时恢复，等待完整计入指标。组件对照
与结果完整性检查不能替代这些长等待的原因分析，也不能把它们从 JCT
中扣除。当前仍是单轮结果，未进行同工作量配对比较与重复实验。
发布轨迹的原应用语义正确性和原始 EOS 未被重建；这里验证的是固定
请求内容与长度契约下的结构重放和系统指标。

## KVFlow

论文固定为 arXiv:2507.07400，下载的 PDF SHA256：
`2dc3180f91c34df920a7547f14fe92988527a76a277d2d17ddf6b558205d2f89`。
公开检出仍固定 `7ef897eaedc16626a281ccfe0d453fae80251e8f`，
其中 ScaleSim 的 timestep/holding 实现不冒充论文的 Agent Step Graph。

已补齐 ALL 的 max-plus、ANY 的 min-plus 距离，按最近未来 invocation
合并同一 agent 的距离。固定提示前缀端点向祖先传播最小距离；没有固定
前缀用途的动态后缀为无穷大驱逐优先级。距离接入原生 FULL 驱逐堆，
实际原生分配器验证先回收动态后缀，再回收远距离固定前缀，原生锁仍生效。
原生节点分裂时只更新受影响的所有者路径，驱逐键读取不扫描全部 agent。

传输状态保留 GPU、Host、loading、offloading 四种语义。正在 D2H 的
不可变前缀仍驻留 GPU，可以服务；它不能作为可回收对象。等待 H2D 的
请求则不能被当作已经就绪。

- `beliefkv/policy/kvflow.py`
- `beliefkv/runtime/kvflow_cache.py`

事件、固定前缀边界、已观察图前沿、物理预取及状态准入循环已接通：
`beliefkv/runtime/kvflow_baseline.py`。运行入口明确命名为
`kvflow_observed_graph`，策略名为 `kvflow_paper_observed_graph`。
应用预声明图和只用已观察事件的动态适配仍是不同的信息条件，当前实现
不能作为预声明图版本已复现的证据，驱动私有未来图不会成为在线策略输入。

AgentX 没有原应用的固定提示边界。适配器只比较已完成的同 context 请求
输入与当前已到达输入，以历史公共前缀学习边界；第一次请求不把整个输入
认定为固定提示。epoch 或 namespace 改变会清理旧证据。临时 child 明确关闭
才删除历史，未知或持久 context 在 invocation RETURN 后保留可驱逐缓存。

图距离按事件变更更新，不逐 token 重建。原生节点通知使请求前缀匹配失效，
树未变化时重复准入不重复遍历长输入。准入检查整个实际复用前缀，
固定前缀之外的动态后缀仍在 loading 时也暂缓；独立驻留请求继续执行。
没有预取容量时保留原生需求恢复路径，避免把普通 Host-only 请求永久挡住。

物理维护每 200 ms 一轮，最多 4 个预取在途、16 次前缀路径检查，每条路径
上限 1024 个节点并记录超限。Host-only 目标先于可选备份；同距离候选轮转，
避免 Host 空间不足的前几个目标永久占住检查预算。预取可以请求原生堆
回收可驱逐容量，期间锁住目标已有前缀，原生锁和 D2H ACK 决定实际释放。
每轮最多推进 16 个原生驱逐候选；有 Host 副本才立即 demote，缺失副本的
原生受害节点异步备份，之后再由 ACK 后的轮次回收。后台预取不调用会
全局等待 write-back 的 `cache.evict()`，避免它连带等待无关 D2H。
这些是显式移植参数，不能冒充论文未给出的唯一配置。

新增路径、原生缓存、隔离环境、实验入口及共用 agent 调度共 189 项 CPU 检查通过：
`experiments/reports/kvflow_native_runtime_cpu_20261025.xml`。
它们没有证明真实 GPU 字节搬运或完整运行性能。
图更新、维护轮次及准入规划分别记录累计和最大 wall time，避免只用检查次数
推断控制开销很小；准入规划计时不重复计入其之前触发的维护计时。

V220 在物理检查导入 SGLang 时失败：独立检查进程没有继承登记的
`CUDA_HOME`，DeepGEMM 无法初始化，未提交正式请求。检查入口已采用
登记的 CUDA 13 工具链与库路径，并单独保存生效环境，不继承其他实验的
BeliefKV 策略变量。

V220b 的物理检查通过，正式重放处理到 29 次注册、12 次完成后，被第一笔
主动 D2H 的回执拦下。论文策略发出的命令没有归属到它自己的回执账本，
进入 Full 的账本后被判断成未知命令，runtime 随即禁止物理操作。该轮是
移植错误，不能用于性能比较；原日志与停止记录完整保留。

修复在 `NativePrefixTransfers` 入队前登记独立的物理期望，复用既有
`PhysicalTransactionLedger` 校验方向、字节数、节点拆分与重复回执；
KVFlow/TokenCake 的原生 ACK 交由发起方对账，不进入 Full 的逻辑账本。
原生未标记需求传输保持原路径。CPU 的复制 stand-in 也补上了真实控制器
具有的入队回调和 tagged child receipts，避免再次用缺失回执的假路径
验证集成。

真实 GPU 检查已验证 13 个命令、9 个合并 ACK 经实际 runtime 完成对账，
并保留全部字节、共享前缀和分配器检查：
`experiments/raw/paper_prefix_runtime_ack_v221b_20261025/summary.json`。
共用调度、Full 物理路径和各论文基线共 1036 项 CPU 回归通过：
`experiments/reports/paper_baselines_owned_native_ack_cpu_20261025.xml`。

V220c 已越过首个回执故障点，末次 runtime 快照记录 201 个论文策略命令
正常对账，随后暴露原生分配交接缺口而停止。正式请求的 15 条原生回执
保留，不能算完整性能结果。失败时申请 4096 槽位，自由槽位 2398，
等待 ACK 后可驱逐槽位却有 86016。

已用真实原生 CPU 树和分配器复现同一问题：申请 4 槽位，原生先因
异步 D2H 的路径锁找不到叶子，随后按原有 write-back 流程等待 ACK；
页面此时已经解锁，但没有重新走回收，最终在“自由 2 + 可驱逐 4”的
情况下仍报 OOM。修复只在实际分配缺口、且原生已完成该备份等待时，
再次调用原生分配回收入口补齐剩余缺口。不启用 Full 控制标志，不放松
任何锁，不给后台预取增加全局同步。transfer owner 关闭时恢复原入口。

新回归共 1041 项通过：
`experiments/reports/paper_baselines_async_allocation_retry_cpu_20261025.xml`。
V221c 在独立 TokenCake 引擎的真实 GPU 上也验证该分配情形，并重验
15 个命令、11 个 ACK、各层字节和共享容量：
`experiments/raw/paper_prefix_allocation_retry_v221c_20261025/summary.json`。

替代运行 V220d 冻结为 `3d8319b8cff8793608f73fd4ca7506713b841bbd`，
路径 `experiments/raw/agentx_kvflow_v220d_20261025`。supervisor PID 2962084，
start ticks 713735645。该轮已经失败并清理，末次快照 97 次登记、77 次完成，
2175 个论文策略传输命令完成。分配重试实际成功 3 次、回收 12288 槽位；
新的故障是多次独立预取过快轮转原生三个 producer 事件槽，触发
`Producer finish event should be ready before being reused`。它不能作为
性能结果，完整失败记录保留。

修复在分配与登记回执前非阻塞检查三个原生 finish event；有任何槽位
尚忙就延后可选预取，保留原生需求加载和 overlap 的事件槽。一个维护轮次
的多个目标共同提交为一个 producer，不把预取并发改成单目标；中途失败
仍提交此前成功入队的传输。新增 1062 项共用 CPU 回归通过：
`experiments/reports/paper_baselines_producer_backpressure_cpu_20261025.xml`。
GPU 检查新增真实在途事件的退让、四个 owner 共同提交与独立回执、
各层字节和唯一容量验证。V220e 已通过该检查：25 个命令、18 个合并
ACK、D2H 50 token、H2D 34 token，并已启动完整模型。
冻结应用为 `d8612e402dab5a170cb712ce9e7d26e6f8f9916d`，路径
`experiments/raw/agentx_kvflow_v220e_20261025`，supervisor PID 2973195、
start ticks 713960879。参考引擎另有 85 项 CPU 检查通过：
`experiments/reports/kvflow_producer_backpressure_reference_cpu_20261025.xml`。
工作量、容量和种子保持相同，完整结果仍待完成。

V220e 随后因原生 chunk 分配不足停止，进程已退出并清理。末次快照为
70 次登记、55 次完成、1189 个论文传输命令；申请 4096 槽位时只剩
1156 自由槽位且没有可驱逐页。此前的 8 次 D2H ACK 后分配重试成功，
事件槽断言也未再出现。这轮不能作为完整性能结果。

新的 CPU 复现使用真实 `Req`、`PrefillAdder` 和原生分配器：9 个空闲槽位
被一个 8-token 预取占用后，已选 chunk 的 4-token 分配失败。修复保护
已准入请求所有尚未分配的当前输入及一轮 decode 增长，不读记录的最终
输出长度；维护仍是每 200 ms 一次，只检查已准入或原生活跃请求。
当可选备份的路径锁会影响这项容量时暂缓备份；原生 adder 已选 chunk 后
队列规划仅更新依赖，不再启动物理维护。需求恢复和原生锁规则保持有效。
1102 项共用回归通过：
`experiments/reports/paper_baselines_native_growth_and_handoff_cpu_retry_20261025.xml`。
新的 GPU 检查已证明真实 chunk 分配及完成后的预取恢复，V220f 已启动。
该轮检查 29 个命令、21 个原生 ACK，D2H 66 token、H2D 42 token，
并保留此前各层字节、共享页与 producer 检查。冻结应用：
`db74aabe312a82abae12e4205c1ec55459a19534`。运行：
`experiments/raw/agentx_kvflow_v220f_20261025`，supervisor PID 2983546、
start ticks 714165321。参考引擎另有 90 项 CPU 检查通过：
`experiments/reports/kvflow_native_growth_reference_cpu_20261025.xml`。
完整性能结果仍待完成。

## TokenCake

公开源码固定 `1901ebf31fdf63590148e61d5c2f65e9fb7ec010`。
请求评分、类型评分、整数容量划分、卸载门槛、时长预测和 EWMA 更新，
分别与未改动的作者纯函数对照 5000 组状态，均一致。校验器在运行前检查
文件 SHA256，仅执行指定定义，不导入作者 serving 引擎。

- `beliefkv/policy/tokencake.py`
- `scripts/qualify_tokencake_components.py`
- `experiments/reports/tokencake_author_component_fidelity_20261025.json`

共享/关键容量窗口也已补齐，位于
`beliefkv/policy/tokencake_capacity.py`。在另 5000 个状态中，可用容量与
作者 `_update_available` 一致；普通及借用两种路径合计 10000 次消费顺序、
10000 次准入边界检查一致。覆盖已准入请求的生成增长、共享池优先、自己的
关键预留、空闲预留借用、等待关键类型保护及配置变动后的已接受增长债务。
报告为 `experiments/reports/tokencake_capacity_author_fidelity_20261025.json`；
它还重复覆盖了前述六类纯策略，而不是替代或改写旧报告。

`beliefkv/runtime/tokencake_capacity.py` 接入真实原生 FULL 树和分配器的
容量组件。共享前缀按原生节点代际去重，保留首次归属；分裂只拆分容量
区间，不展开逐 token CPU 条目，也不读取 GPU 索引到 CPU。新 checkpoint
把同一请求已占的私有页改归缓存前缀，避免双计。空闲缓存属于可回收容量，
无关 DMA 的原生锁仍减少可用容量。

SGLang 的候选准入早于批次私有页分配，因此该组件另记 pending private
slots。准入阶段扣除预留并保留后续增长，实际批次分配后整体转为已分配；
取消准入只撤销预留，不伪造物理释放。

`beliefkv/runtime/tokencake_baseline.py` 现已把空间调度接到实际 runtime
回调：只使用已到达请求、已观察父子关系和 JOIN，缺失的重要性、未来深度
及进度保持中性值，也可接受显式应用元数据。请求评分、类型历史、500 步
容量划分、500 分带内的活跃共享前缀偏好接入同一准入过程。

原生引擎另建于
`/home/longhao/experiment/.worktrees/sglang-tokencake-runtime-20261025`。
每个候选在 `init_next_round_input` 和原生 adder 前重新核算容量；原生
admitted 回调记入尚未分配的私有槽位；实际批次选定后整体核销这些槽位，
再提交真实分配量。不能用队列开始时的一次性筛选代替逐候选 guard。
500 步窗口按原生 scheduler step 推进，包含纯 decode；同一轮后续的
prefill 规划复用已准备的评分与等待集合，不重复推进步数或调整分区。

decode 压力回收每释放一个对象就重新选择下一个；在分带内比较可释放量、
Host 已保存部分以外的重算代价和接近完成状态。整批原生 decode 压力是
显式后端适配，不宣称等同于 vLLM 逐个 growth 请求的全部控制流程。
逻辑归属在真实物理释放之前撤销，最后一个请求的原生 OOM abort 也覆盖。
重新入队必须观察到这个交接，保留最初到达时间和一次历史样本；
物理回收之后记录实际可用容量及完成 epoch。仍有活跃请求时，可用容量
未增加且没有请求完成，就延后重新准入，避免同一对象立即恢复、再次回收。
实际容量增加或完成后允许重试，所有活跃请求结束时也不会永久挡住队列。
运行请求的未知/持久 context 不因 invocation RETURN 被强制清空。
公共 runtime 也新增独立的论文策略 ACK 回调，不受 Full 自己的 physical
ledger 是否启用影响。原生控制步、队列规划和逐候选 guard 分别记录累计
及最大 wall time。

检查使用真实原生 radix、分配器、`Req` 和 `PrefillAdder`，覆盖同批竞争、
共享页去重、Host-only 需求加载、retraction、abort、重新准入及其他公共
baseline/agent 调度路径。报告：
`experiments/reports/tokencake_native_admission_cpu_20261025.xml`。
此前的空间集成合计 260 项检查及 19 个原生 scheduler subtest 通过。
当前公共生命周期和 ACK 修复已纳入上述 1036 项回归，另有 15 项原生
调度 hook 检查通过。CPU 的 DMA stand-in 不能作为完整 baseline 性能
证据，TokenCake 仍不注册为正式运行 arm。

新引擎完整补丁：
`/home/longhao/experiment/ops/sglang-tokencake-runtime-20261025.patch`，
SHA256 `f65aded5cb99f73aa1834742da164d397bc8ffa36cf886f53efca28f625a9638`，
实际树 `04fe085312a8578a581a8fb47ac7d0b915e3b404`。
它没有替换任何已经运行或冻结的参考引擎。
`scripts/qualify_agentx_data_plane.py` 保留原先 15 次有界模型探测的语义，
增加冻结代码、显式前序运行、资源等待及独立报告。V222 登记于
`experiments/raw/tokencake_engine_data_plane_v222_20261025`。它已在 V220c
退出并清理后完成全部 15 次模型探测，验证精确 token、命名空间隔离、
驻留命中和压力后的 Host 恢复。最新物理组件随后由 V221c 在同一
`04fe085312a8578a581a8fb47ac7d0b915e3b404` 原生树上重新检查通过。
这些证据不能作为 TokenCake 完整算法已执行或性能已验收的证据；
V221c 的源码绑定也早于本次 reservation 与 producer 修复。

生成预算必须独立显式声明。控制器不读重放 sampler 的 `max_new_tokens`：
该字段在当前重放中等于记录的最终输出长度。组件只用单独的生成预算及
已经观察到的输出/分配量，避免把未来长度作为在线容量信息。正式运行的
预算配置及其信息条件仍需在完整 baseline 注册时明确；测试中的 8 token 只是
接口 fixture，不能作为性能配置。

论文的 first-fit 工作量检查以及式 (3)/(4) 的关键等待容量保护、
半缺口渐进恢复预算已实现。新增原生 `restore_prefix` 将一份实际 grant
映射到最多同样数量的新 token 槽位：按已观察 token key 验证整条前缀，
必要时拆分 Host-only 节点，只恢复本轮缺失范围。原生 CPU 生命周期中，
8 token 分别按 4、2、1、1 恢复，实际分配量及 ACK 总量都为 8；在途批次
不重复分配，完整 Host 源租约随分裂保留。这里是 token 范围渐进恢复，
不是只传部分层却分配完整 token 范围。

新增组件、原生树、迁移、隔离与共用外部 baseline 回归共 163 项通过：
`experiments/reports/tokencake_native_components_cpu_20261025.xml`。
共享组件随后已通过参考引擎上的真实 GPU 字节与 ACK 对账检查。旧报告
不能继承为独立 TokenCake 引擎的资格；后者已有 V222 与 V221c 的独立
检查。新增的论文版截止时间与实际渐进上传目前仅通过 CPU 生命周期验证，
还没有完整论文策略的 GPU 执行证据。

公开版的 `release_deadline` 释放 Host 保护，不提交预测 H2D；论文版的预测
上传必须另外实现。二者不会混成同一条已完成 baseline。

公共版生命周期已接到已观察的生成完成和工具开始/结束事件。并行工具
等最后一个成员结束才关闭等待；优先关联实际 `LLM_RESULT.request_id`，
不会因为下一请求提前登记而把工具归给错误的生成。时长只用显式估计、
历史 EWMA 和默认值，不读重放的真实剩余时长。已完成前缀按收益选择
增量备份，Host 旧前缀在新增 D2H、节点拆分和 ACK 期间受保护，压力下
允许原生 Host LRU 回收无关副本。

提早返回的请求可以消费仍在 D2H 的 GPU 源页，容量只给精确匹配且尚未
记账的共享 extent 计入候选局部额度；无关 DMA 不获额度，ACK 不重复
增加空闲容量。原生 reset 会撤销旧 Host 所有权并重新绑定空间与时间
组件；存在活跃容量声明时拒绝静默 reset。公共生命周期的 5000 次状态
转换及非空保留/释放对照已在当前源码重新执行：
`experiments/reports/tokencake_lifecycle_author_fidelity_v3_20261025.json`。
V2 原报告保留；V3 再次通过 5000 次生命周期转换与 32 个实际非空
保留/释放场景，以及原有策略和容量对照。
正式生成预算仍待声明，不能使用测试 fixture 或记录的最终输出长度。

独立的 `tokencake_paper_observed_metadata` adapter 已接通预测截止时间、
实际观测 decode 速率的 first-fit 检查、式 (3)/(4) 的真实 token 范围上传、
Host 源保留及实际 consumer 的原生容量交接。预测上传单独计作 reservation，
不计作活跃请求；共享 extent 保留原容量归属，节点分裂继承 reservation。
只有精确匹配 workflow/context/epoch 且真正持有原生路径锁的准入请求才能
消费它。过期、提前工具结束、在途 D2H/H2D、显式退休、abort 及压力撤销
均有 CPU 覆盖。第一次出现有效 decode 速率时撤销旧的“无速率”退避，
之后不逐 sample 扫描全部生命周期。优先级与截止紧迫性的归一化排序、
压力撤销及重试退避是论文未完全规定处的显式移植选择。仍不注册正式 arm，
六场景 GPU 验证随后已补齐，正式生成预算和完整 arm 仍需单独声明。

V223 冻结应用为 `69a1f4d90dcfbf22b822217dd2946dd775a452f0`，
独立引擎仍为 `04fe085312a8578a581a8fb47ac7d0b915e3b404`。报告：
`experiments/raw/tokencake_native_lifecycle_v223_20261025/qualification/summary.json`。
它使用真实原生请求、`PrefillAdder`、两层 GPU/Host 池和 runtime ACK，
六种场景全部通过：完整预测上传、D2H 中提前返回、H2D 中提前返回、
到期、显式退休、压力撤销。完整上传实际按 4/2/1/1 分配与复制；
H2D 中返回时先等待已发副本，再由原生需求恢复精确补足剩余 4 token。
所有恢复层的 K/V 字节一致，最终容量与 Host 引用全部释放。

另修复 consumer 锁证明：原生请求默认非空的 `DecLockRefParams()` 仍表示
没有取得锁，交接现在同时要求回执节点等于真实 consumer 的 `last_node`，
且没有跳过 FULL。空回执、其他节点和跳过 FULL 均不能消费 reservation。
GPU 六场景报告已重新与当前 runtime 源码校验一致。该验证使用独立的小池
fixture 和可控策略时钟，不代表完整模型、正式生成预算或性能结果。

正式论文入口现已独立登记为 `tokencake_paper_observed`，对应
`tokencake_paper_observed_metadata`，公开版和仅空间组件继续分开。正式配置：
`configs/baselines/tokencake_agentx_20261025.json`。独立生成预算固定 16384，
context 262144、running/decode 24、prefill 16384、chunk 4096；
容量单位映射到共同的 native page=1，公开版 128-block 检查窗口对应 128
token。生成预算来自共同配置的 `MAX_COMPLETION_TOKENS`，不参与裁剪
重放输出，也不读取记录的最终长度；未知未来图字段保持中性值。

启动入口要求七项参数全部显式给出，核验它们与共同 native 配置一致，
并冻结完整声明。实际 server info 还会检查 context、running、prefill、
chunk 和 page，检查失败不会提交测量请求。每次完整运行在资源可用后，
先执行当前冻结源码的原生 prefix 检查，再执行六个论文生命周期场景；
V223 的旧源码证据不直接继承给新增的完整入口。

排队入口增加显式前序运行，核验相同请求计划、token 包和容量，并固定
前序 registration 摘要。只有 supervisor 实际退出、终态及清理完成、
自有子进程全部退出才放行，随后仍重新检查资源。新的正式入口与共用
回归 1344 项通过：
`experiments/reports/tokencake_formal_baseline_shared_cpu_20261025.xml`。

完整运行 V224 已冻结并排在 V220f 之后，路径：
`experiments/raw/agentx_tokencake_v224_20261025`。supervisor PID 2990251、
start ticks 714294139，应用 `5dd06b6fe9d56303d68f2caa4147127741c910f5`，
引擎 `04fe085312a8578a581a8fb47ac7d0b915e3b404`。它保留同一 18 会话、
2229 请求和 2114833 输出 token。当前真实进程仍在等待前序；尚未占用
GPU、尚无 TokenCake 完整性能结果。后续 Agentix 开发不改动这些冻结源码。

## 共用原生物理组件

`beliefkv/runtime/paper_prefix_transfers.py` 支持没有 session ID 的已观察
缓存节点，检查 workflow/context/epoch、节点代际、namespace 和前缀长度。
备份只传缺失后缀，原生 ACK 前不释放 HBM；恢复按真实完整分配容量检查，
拒绝不足容量，不驱逐另一请求来隐藏失败。部分入队失败仍启动此前成功
的原生作业。原生分配器、锁、ACK 与流依赖保留。

Host 租约分别锁住完整前缀的每段，节点分裂时继承新前缀的引用；到期按
最小堆释放，不逐 decode 扫描全部租约，提前释放留下的堆项也有界。
Host 保护不锁 HBM，原生 H2D 自己的源引用不会被租约到期误释放。
不需要 Host 租约时不安装额外的节点通知。

该组件既有真实原生树和 Device 分配器的 CPU 检查，也已完成参考引擎
上的两层 GPU/Host 真实拷贝与 runtime 回执对账。二者都不等同于完整
模型性能验收；V217c 的旧数据面报告仍不能替代新增组件检查。

新增 `scripts/qualify_paper_prefix_transfers.py`，用原生两层 GPU/Host 小池
检查初次 D2H、传输中分裂、仅 4 token 增量备份、完整 Host 租约分裂继承、
共享运行 peer 保护、ACK 前不释放、无关 D2H 在途时的非阻塞原生堆回收、
释放页写坏后 H2D 恢复及租约到期后原生在途源引用。后续版本还加入
6、3、2、1 的渐进恢复、各层字节一致性、原生 GPU 上的共享容量记账和
批次分配前后 private slots 交接。入口校验原生树、补丁和组件源码 SHA256。
当前还覆盖增量 D2H 前的 Host 旧前缀保护、精确忙碌共享页容量交接，
以及真正的论文 adapter 到 runtime 的 ACK 分发、备份 ACK 后的原生
分配重试。V221c 报告与 V220d 自己的
`native_prefix_qualification/summary.json` 保留为此前源码的证据；
本次新增 producer 退让和批次合并已在 V220e 的参考引擎检查通过。
独立 TokenCake 引擎与完整论文生命周期仍需各自验证。V220 至 V220d
的失败均保留。
任何后续主分支修改都不改变该运行的冻结内容。

KVFlow 的实验 supervisor 必须先等资源，再通过这项新增物理检查，
才允许启动完整模型和共同发布轨迹。失败保留独立报告并停止启动。
接下来完成 V220f 和排队的 TokenCake 完整运行，进行完整同工作量比较。
Agentix CPU 适配已经恢复。完整路径 Host 所有权及分裂继承现已实现并
通过 1409 项共用回归，真实 GPU swap 验证、原生 LLM 执行记账和
抢占/恢复调度接线仍待完成，正式 arm 继续关闭。

Agentix 随后补齐显式原生 worker 绑定组件及取消暂停准备时的 Host 引用
清理。148 项针对性 CPU 回归通过，包含固定 worker 原方法体与原生树
生命周期；不代表真实 LLM/GPU hook 已验证。详细边界和未完成项见
`docs/research/agentix_port_20261025_zh.md`。本次开发未修改运行中的 V220f
或排队的 V224 冻结内容。

Agentix 的独立 adapter 随后已完成共用 runtime 的 CPU 集成，包含实际共享
前缀容量探测、保留原到达时间的调用生命周期、原生选择钩子、ACK 后选择性
batch 过滤与重入队。604 项共用回归通过：
`experiments/reports/agentix_native_adapter_all_baselines_cpu_20261025.xml`。
实际引擎构造接线、真实 LLM/GPU 抢占恢复、Host 压力下进展和正式参数
仍待验证，正式 factory/arm 保持关闭；不以原生丢弃后重算替代论文 swapping。

独立 Agentix 引擎现已加入启动接线，tree 为
`c489dbb85265efce25622a52abfd698e9199688d`，规范 patch SHA256 为
`15fe59ebe16721e5ee07244d0d25381b0187b91b1afa57f8fd06281fe051cd09`。
显式有限 Host backing 门槛及容量拒绝后的原生批次保持/重试已补齐；
628 项共用 CPU 回归通过：
`experiments/reports/agentix_isolated_host_backing_all_baselines_cpu_20261025.xml`。
这是有限 swap 空间的公开适配选择，仍需真实 GPU 压力进展验证。
仅开放最多四次请求的有界资格验证 factory，正式 `RUNTIME_ARMS` 继续关闭。
尚未启动新的 Agentix GPU 进程或登记完整 Agentix 性能结果。

## 完成后重排遥测缺陷与替换登记

V220f 执行完 18 个完整会话、2229 次请求，所有输入/输出 token 契约
核验通过，物理 runtime 保持启用。其事件边界得到完整平均 JCT
8436.819305 秒、全程输出吞吐 174.683842 token/s。这些仅为诊断指标：
收尾 `pending_request_count=9`，未通过两个新鲜 idle 快照的强制检查。
不能将该轮改写为合格性能对照。原始状态、日志与清理工件全部保留，
诊断复核在
`experiments/reports/kvflow_v220f_drain_incomplete_review_20261025.json`。

最后的 9 条 pending 与 9 次 native retraction 对应。共用遥测
`on_enqueue` 在已经 active 的请求重入队时重复写入 pending；
完成时只移除 active，导致真实请求全部结束后仍留下计数。已先复现，
再改为忽略 active/completed 调用，保留首次到达时间。检查条件保持严格，
不通过清零旧状态或放宽收尾门槛修复。包含两个失败复现及最新 1091 项
通过的 CPU 报告均保留。

V224 在启动阶段、正式轨迹提交前行政停止，V225/V226 在等待中停止，
全部自有进程清理完成。修复后的执行顺序为：

| 任务 | 应用 commit | 前驱 | 范围 |
| --- | --- | --- | --- |
| V220g KVFlow | `bb63a5b66b5cd42689b87757b48f5929e4a75bd1` | V220f 已退出 | 原完整人口与原论文适配策略 |
| V224b TokenCake | `dea4616ee40749363329d16af9d30fb76e12dbb1` | V220g | 原完整人口与显式论文参数 |
| V225b Agentix | `2b80c0d59af9b92ae0e226b7c6cd48c6e00f55ae` | V224b | 四请求真实模型抢占恢复检查 |
| V226b Agentix 压力 | `214f46cad2a6fcc033b21ed90883a6e7a1b8549b` | V225b | 小型原生 GPU/Host 池压力组件 |

V220g 已重新通过源码绑定的真实 GPU prefix 检查并进入完整轨迹运行。
每个后继都要等前驱真实退出和完成清理，不能仅根据 GPU 暂时空闲启动。
V225b/V226b 没有正式 Agentix 性能含义，也不证明完整 LLM 在有限 Host
压力下已经具备进展保证。所有比较仍需完整同工作量结果与配对重复验证。

后续 V227 排在 V226b 之后，验证 Full sessionless 前缀迁移的真实原生
GPU/Host 组件。其监督 PID 3071982/start `715994041`，应用
`e53af6c6f493b66dcdfc7381c0dea9c8be7aa746`，固定使用同一
`c489dbb85265efce25622a52abfd698e9199688d` 引擎，但关闭 Agentix policy。
这项组件检查不计为一轮 Full 或一个新增 baseline。当前队列仍不包含
已合格的正式 Agentix 全人口性能运行或完整 Full 运行；两者继续推进。
已有 V219c/V220g/V224b 的工作量、顺序、源码和信息条件均未修改。
