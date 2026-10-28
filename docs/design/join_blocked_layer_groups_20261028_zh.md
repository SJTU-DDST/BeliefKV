# JOIN 额度交接与独立传输分组

2026-10-28。此候选将两个实现改动合并声明：
补齐阻塞 agent 的逻辑额度交接，以及按不同层数组合 D2H/H2D。
工具预取和普通执行前沿预取继续关闭，保留 JOIN/EOS 预取、预测卸载、
prepare_host 与 agent 连续性。它是调度与迁移整合，不将额度交接单独包装
为新的工作集调度算法。

配置：`configs/migration/predictive_join_blocked_layer_groups_20261028.json`。
保留 V230 的完整工作量、V311 工具时长、18 个不同项目、running/decode24、
容量、seed21、流式指标和全部正常长尾。原 grouped FULL 已完成，
结果见 `../research/join_blocked_layer_groups_result_20261028_zh.md`。
本页后续启动记录保留原时点含义。最新实现另使用
`predictive_sibling_service_capacity_20261028.json`，没有新 GPU 性能结果。

## 2026-10-28 后续实现

普通工具卸载和逐层 handoff 已共用实际消费者输入预算。
JOIN 逻辑 grant 现在记录对应 donor，并由 `JoinPhysicalRecovery` 在实际
原生准入或可验证 EOS 的恢复入口推进：核对完整身份、实际缺失、增长、
Host 空间和 native 锁，先回收已有备份页，再尝试未备份私有路径的 handoff，
否则使用原生备份与 ACK 后回收。已具有完整逻辑资格的 parent 也可建立此
物理计划。工具 donor 不绕过原动态预测成本检查。

计划最多保存 24 个 parent；每次最多推进八名 donor、64 个物理节点，
失败有 200ms 退避。真实 parent 首个 native forward 消费计划，不因计划结束
撤销正常 agent 资格。`join_planned_physical_release_tokens` 只记录实际释放，
`join_planned_reserved_handoff_tokens` 单独记录共享预留，不能提前计入 free。
它仍是分阶段、重新验证的计划，不是在逻辑授权时一次性锁住所有物理容量。

新原生 `873970a45a250045e823dcc1134a48d70de49ec2` /
tree `cdd58a4acbb5038207d3a5020c54d7cbcef983b9` 增加两项接口修正：

- idle 接收 `max_batch_bytes` 与独立 `reserve_host_bytes`，在需求余量外按实际
  整页预算备份，不再要求先凑足 512MiB；旧引擎缺少接口时继续保守回退。
- handoff 可与路径独立的已发起 ACK 共存，检查最多 64 个 ACK、256 个祖先
  节点。未知或相关路径继续拒绝，原生 Host/Device 锁、生产者事件、
  未提交队列限制和单事务保护继续保留。

补丁 `patches/sglang-sibling-capacity-runtime-20261028.patch`，
SHA256 `5e4038068f793a46b5bdabbbc83d0d4ccf6f854fc0d7be00ea51a72a827bc997`。
这棵树的 GPU/模型验证仍待完成，不能使用旧 grouped 数值证明替代。
多 donor 可以组合普通 native 回收及 ACK 后备份；尚不支持将多个 donor
组成一个逐层事务。全部 8/4 层组仍同步提交，未引入后台异步提交线程。

最终 CPU 资格为 3580 项及四个子测试通过，保留旧 3524 项并新增 56 项，
原生相关检查为 92 项。实际 FULL、默认及 acting-decay ThunderAgent
构造器隔离通过。`experiments/reports/sibling_service_capacity_20261028/`
的 `final_cpu_review.json` 复核源码、精确 native 树与补丁、开发失败、
工作量配置和局部成本。两名小 donor 的真实分配器释放与 H2D/ACK 链路、
JOIN donor/有原生 EOS 权限的入口及独立 ACK 共存均有 CPU 替身检查，
不替代新树的实际 GPU、非强制模型或完整性能验证。

## JOIN 的交接链路

此前 reference FULL 的额外准入只能使用已经空出的完整逻辑预算。
新开关 `AGENT_JOIN_BLOCKED_HANDOFF=1` 在两个实际入口尝试交接：
已观察 EOS 的 parent 恢复申请，以及真实 JOIN 后继请求的注册/就绪事件。
没有 EOS 或实际后继请求时，不为了等待中的 parent 提前占用新预算。

| 阶段 | 逻辑资格 | 物理 HBM |
| --- | --- | --- |
| 确认 parent 与实际 JOIN 身份 | 不变 | 不变 |
| 完整预算仍不足，选择真实阻塞 donor | 仅形成计划 | 不变 |
| 再验证后暂停 donor、授予 parent 资格 | 一次性交接完整额度 | 不增加任何空闲页 |
| 原生增量备份、ACK 后安全回收与 H2D | parent 已有资格 | 实际 native 分配及 DMA 检查 |
| parent 原生准入、执行及后续请求 | 延续普通 agent 资格 | 保持原生增长、锁和压力规则 |

新逻辑位于 `beliefkv/runtime/join_blocked_handoff.py`，由
`ReferenceThunderAdmission` 的事件和 `claim_restore()` 路径调用。
原有 JOIN 物理恢复仍走 `_reclaim_wait_prefetch_capacity()`、
原生缓存回收和正常 prefill 准入。逻辑 donor 与最终物理回收对象分别核算，
不会将预算释放当作 HBM 页已经可用。

允许 donor 的必要条件：

- 当前 active、已完成模型调用、没有 pending 请求或暂停标记；
- context/session key 和图状态仍一致，确实等待 JOIN 或工具；
- 工具 donor 仍须通过已有长工具预测；短工具、缺少预测者不提供此交接额度；
- 不涉及其他正在恢复的 JOIN、critical demand 或受预取保护的 owner；
- 不暂停 ready、运行中或等待模型的 agent。

先刷新实际 streaming 长度，按完整上下文与原有 100-token buffer 核算。
单个 fitting donor 优先，否则在有界候选集中累积；只有完整 parent 放得下、
所有 donor 再验证仍有效才一起提交，不做部分释放。
最多检查 64 个 donor，事件上的 ready-parent 窗口为 24；失败后保留 200ms
重试间隔，在真正的请求/阻塞状态变化处重新唤醒检查。没有新后台定时器、
逐 token 全队列扫描、普通恢复 TTL 或 donor-return 关系账本。

### 防止五秒控制撤销交接

reference 五秒控制原先也会考虑恢复仍阻塞的 paused ACTING program。
这可能重新接纳刚暂停的 donor，再因完整预算超限把正在恢复的 parent 暂停。

新开关下，给原 working-set 核心提供可选 `resume_eligible` 条件：
尚未提交新请求且仍被在线图确认阻塞的 paused agent 暂不进入恢复候选。
工具/child/JOIN 真正返回、图状态就绪后，重新参与普通竞争；已经 pending
的真实请求也不被该条件阻挡。active agent 没有因此自动被驱逐。

该条件默认为空，paper ThunderAgent 及旧 FULL 使用原有选择规则。
仍使用相同核心类型和继承的 attach、tick、completed、abort、
streaming-accounting 方法，不伪造 ACTING/pending 状态或折扣完整容量。
这不是无限 parent 优先；真实增长压力与原生容量仍可限制 parent。

### 可审计的后续服务

成功事件 `agent_join_blocked_capacity_admitted` 记录 parent、donor、
完整身份、预算前后值，并显式记录 `released_device_tokens=0` 和
`released_host_tokens=0`。JOIN grant 审计器现在识别该事件，校验：

```text
交接前剩余额度 + donor 完整额度 - parent 完整需求 = 交接后剩余额度
```

之后仍须用原生 session/epoch、实际拷贝和首次服务证据验证恢复与执行。
资格授予本身不计为已执行、已释放显存或 JCT 收益。

## 不同的 D2H 与 H2D 粒度

新 native 分支 `beliefkv-layer-groups-20261028` 已提交
`1f5fac9e9f7ad1d97f89244cdda4d9f311d365c4`，树为
`2ce5e5389979a718c19f80abb9cc9b381b8cff0e`。
旧 `0d407f981c51` 引擎与所有历史结果保留。

候选参数：

| 方向 | 参数 | 初始候选 |
| --- | --- | ---: |
| D2H | `BELIEFKV_LAYER_HANDOFF_D2H_LAYERS` | 8 层 |
| H2D | `BELIEFKV_LAYER_HANDOFF_H2D_LAYERS` | 4 层 |

两方向独立配置，允许不整除，例如 5/3、3/5。复用现有 HiCache
`transfer_hicache_all_layer` JIT，通过层指针表的切片一次复制整个层组；
没有用 Python 循环调用单层接口来假装合并。
`layer_first` 与 `page_first` 使用各自正确的 token stride。

每个 H2D 组等待覆盖其目标层范围的最后一个 D2H 组事件；D2H 同流有序，
因此跨越多个 donor 组时也不会提前覆盖。前面组就绪后，计算仍按每层
消费就绪事件，不需要等待最后一组完成。默认 1/1 保留旧粒度。
原有 donor/consumer token 数不同的交接仍保留，消费者使用 donor
预留 extent 的前缀，多余尾部只能在真实 D2H ACK 后释放。

在当前 48 层模型上，8/4 将 D2H/H2D 接口调用从 48+48 减到 6+12，
层完成事件也从 96 个减到 18 个，二者均减少 81.25%。
这不是整段提交时间或 JCT 减少 81.25%；索引、锁、发布和其他 CUDA 事件仍有成本。
较大的 D2H 组也会增加首组提前量，8/4 尚不是经过性能选择的最优值。

原生补丁：
`patches/sglang-layer-handoff-groups-runtime-20261028.patch`，
SHA256 `8b8a8d35404d9f1c035a57645684c6758f15c3e48ee6ef83551b3e5815754612`。
应用包装器、启动器及 replay 引擎白名单绑定该精确树，旧引擎不能冒充
支持分组。新的模型 qualifier 还要求 GPU component 的两方向分组与配置一致，
不能用旧 1/1 的 component 证据验证 8/4。

## 实现前审计与尚存边界（2026-10-28）

以下五项保留本轮修改前的源码审计，当前处理范围及剩余限制见前节。
它们分别属于预算口径、
模块衔接和机制覆盖限制，不能全部解释为正确性错误，也尚不能精确
归因各自造成的 workflow JCT 损失。本节仅记录问题和后续验收方向，
没有修改运行中的冻结应用、原生引擎、配置或实验。

### 1. 普通工具卸载仍使用整批输入预留判定需求

[`DemandAwareToolOffload._demand()`](../../beliefkv/runtime/predicted_tool_offload.py)
计算 `needed = missing_full_tokens + cost[1] + reserve`；
而 [`native_residency_budget()`](../../beliefkv/runtime/native_transfer_policy.py)
的 `reserve_full_tokens` 已包含尚未选定的下一批输入上限及增长预算。
因此在当前消费者已经明确、尚未选入其他 chunk 的场景中，需求判断仍
可能同时保留整批输入上限和该消费者的实际输入增量。

例如缺失历史 KV 1024 token、输入增量 1、增长预留 32、未选输入上限
4096、实际空闲 2048：单独执行该消费者所需为 1057，当前公式得到
5153，进而判定还缺 3105。这个算例说明可能高估压力，尚不是实际
多卸载了 3105 token 的运行证据，也不能忽略已经选入的其他 chunk。

`try_handoff()` 已在已知空选批状态下修正其内部预留，但普通 offloader
的前置需求判断没有统一采用该口径。后续应共用实际消费者预算，保留
未知批次和已有选入成员时的保守余量，并统计由预算差异改变的决策、
实际额外 D2H/H2D、batch 形状与服务等待。

### 2. 逻辑 donor 与物理恢复计划尚未绑定

[`JoinBlockedHandoff.claim()`](../../beliefkv/runtime/join_blocked_handoff.py)
按完整上下文加 buffer 选择逻辑 donor、暂停 donor 并激活 parent，
不在同一计划中证明所选 donor 有多少可回收的独占 Device 页、
Host 目的空间或已完成副本，以及这些资源是否足以恢复 parent。
大量上下文已在 Host 或仍有共享依赖时，交出逻辑额度不等于交出同量 HBM。

当前后续原生恢复仍可安全拒绝，因此不能将逻辑授权记为物理错误；
限制在于 parent 获资格后仍可能等待，实际回收还可能选择其他对象。
后续应联合规划逻辑额度、可回收物理范围、备份/恢复依赖和首次服务，
逐项记录实际消费。计划中的容量不能提前记入 allocator 空闲页，
不能为了履行计划越过原生锁、Host 源保护或在途 DMA。

### 3. 逐层事务覆盖小于原定容量交接场景

[`try_handoff()`](../../beliefkv/runtime/layer_handoff.py) 只在原生准入前，
对已有逻辑资格的实际已提交消费者尝试；donor 来自工具预测卸载路径，
且一条合格私有路径必须能承接消费者的完整缺失路径。真实 JOIN 阻塞
parent 作为 donor、parent 尚未提交时的 EOS 提前恢复，以及多个冷对象
共同供给容量，尚未直接纳入此逐层事务。这不表示原有 JOIN/EOS 预取不存在。

旧 JointFullR1 只有 13 次交接、107151360 字节，约占全部 H2D 的 0.004%，
消费者长度中位数为 67 token；这是旧 1/1 引擎的完整覆盖证据，不能
当作新 8/4 配置的结果。已有 Host 副本的 donor 直接 demote 本来更便宜，
不应为提高交接次数重复 D2H。后续需评估将已有备份页直接回收、未备份
范围的流水卸载及真实空闲容量组成同一计划，并审计尚未覆盖需求的
实际可用机会；不放宽共享、身份、锁和 DMA 条件来制造机会。

### 4. idle 备份仍以整批上限作为最低预算

[`dispatch_idle_prepare()`](../../beliefkv/runtime/sglang_v0520_runtime.py)
已经修复字节数与完整页取整不一致的问题，但仍要求扣除需求预留后的
`available` 至少达到约 512MiB 的完整批次上限，随后无参数调用原生
`prepare_beliefkv_idle_full()`。因此即使剩余 128MiB、候选只需 4MiB，
当前入口也会整轮跳过；SPAWN 路径则已经可以传递缩小后的批次预算。

这是固定批次接口的保守覆盖限制，与旧 JointFullR1 中造成 10114 次
检查拒绝的取整错误不同。后续应让原生 idle 备份接受实际字节上限，
并检验完整 extent 的适配、需求余量及 ACK 生命周期。应单独统计
满足小节点备份却不足整批上限的机会，不能拿旧 10114 次作为此限制
的频率或 JCT 损失，也不能通过取消必要 Host 需求预留来规避。

### 5. 全部层组仍同步提交，入口仍要求传输排空

当前 grouped 原生
[`submit_layer_handoff()`](../../patches/sglang-layer-handoff-groups-runtime-20261028.patch)
在一次 CPU 调用中提交全部 D2H/H2D 层组后返回，调度器才继续推进
消费者。8/4 将 96 次拷贝调用减至 18 次，但没有将整个提交循环移出
同步路径。`begin()` 同时要求 write/load 及对应 ACK 队列为空，并且
只允许一个交接事务在途；高迁移压力下可能缺少可进入该路径的安全点。

这是提交成本与事务并发覆盖限制，不代表 GPU 必须等待全部拷贝完成。
现有队列排空条件承担正确性保护，后续若改为更细的事务依赖管理，
必须保留页所有权、源引用及完成事件；不能直接删除条件。
旧完整运行 13 次交接的提交相关时间合计约 62.43ms，不能据此解释
整轮平均 JCT 回退约 7.38s；新分组还需完整运行的提前量和暴露等待证据。

### 已修复问题与验收顺序

经验 CDF 被错误插值、Full 工具预测在 1--2s 后失效却不续发、idle
字节预算取整误拒绝，均已有修复，不能继续列作当前未修问题。前两项
修复后已有完整运行，动作增加没有建立明显净收益；取整修复也不能
单凭 CPU 反例宣称已经改善 JCT。详情见
[工具预测修复](tool_prediction_curve_repair_20261027_zh.md)及
[旧联合实验审计](../research/layer_overlap_regression_and_siblings_20261028_zh.md)。

后续优先统一实际消费者容量口径，再绑定逻辑资格、物理恢复及首次服务，
随后扩大逐层交接的有效覆盖并降低同步提交成本。验收同时保留实际
机会数、缺口与回收量、额外迁移、首次服务等待、GPU 恢复暴露等待、
完整 workflow JCT/吞吐及控制成本，不将动作量或模型正确性等同净收益。
另有 sibling 共同选批、可容纳子集准入和释放后重试的独立缺口，见
[ALL-JOIN 共同准入设计](reference_join_siblings_20261027_zh.md)。

## CPU 验证

一次最终共享执行通过 3519 项及 4 个子测试，无失败/跳过，保留前次
3481 项全部身份，新增 38 项。原生相关 CPU 检查通过 53 项。
实际构造器确认 FULL 的新交接与 8/4 生效，工具预取关闭；
默认和工具衰减 ThunderAgent 均未继承新开关。

测试覆盖额度不足不部分释放、ready/pending/受保护 donor 拒绝、
交接期间 donor 返回、重复检查退避、五秒控制不撤销交接，以及真实 native
树/分配器上的预算交接、恢复拒绝、Host-backed 回收、H2D、ACK 与
parent 后续候选选择。原生测试覆盖不同方向组、非整除组、正确 stride
与指针范围。CPU copy/event 替身不证明实际 CUDA 安全或模型数值等价。

局部控制成本使用真实 runtime 回调、33 个合成 program、31 条 pending
记录和异步日志，每条路径预热 50 次、测量 500 次：

| 路径 | thread CPU 中位数/微秒 | P99/微秒 |
| --- | ---: | ---: |
| 关闭新交接 | 29.243 | 32.526 |
| 成功额度交接及日志 | 121.328 | 141.781 |
| 本轮没有可用阻塞 donor | 73.528 | 78.802 |
| 200ms 重试退避 | 34.116 | 38.468 |

它不含 writer 线程 CPU、最大 64-donor 窗口、整控制器或 GPU 开销；
不能据此声称控制开销已低于所有场景的迁移收益。

命名证据目录为 `experiments/reports/join_blocked_layer_groups_20261028/`。
最终共享来源绑定为 `qualified_shared_cpu.*`，原生为 `native_final_cpu.xml`，
回调成本为 `control_cost_cpu.py/.json`，总来源检查为 `review_cpu.py/.json`。
保留首次两个测试夹具错误、一次 nested-harness 时间戳乱序、
精确复查、完整重跑以及最初 CPU benchmark 统计字段错误。
没有为通过测试改写 workload、排序实际源事件或丢弃失败证据。

实际 grouped GPU 拷贝已通过下述组件验证，非强制模型输出一致性及完整性能仍需分别验证。
后续 FULL 继续复用 V345，显式披露引擎差异，评价完整 JCT、全程输出、
decode 随时间曲线、迁移量和计算干扰；当前没有新的性能优势结论。

首次真实 GPU component 从冻结应用 `0decf93f`、原生 `1f5fac9e` 启动，
在交接前发现共用夹具仍硬编码原有 2 层/1024 字节，而新的分组夹具使用
17 层，因此主动拒绝。`gpu_components_r1.json` 保留失败，进程 613553
已退出、NVML 已为空。只修复夹具按声明层数检查每层 512 字节，仍保留
128 Device / 257 Host slots 校验；71 项定向 CPU 检查通过。迁移与调度
代码和原生引擎不变，下一次实际 GPU 验证使用新的冻结应用。

## 实际 GPU 组件完成

R2 使用冻结应用 `4675f4ad` 和同一 clean 原生 `1f5fac9e`，
`gpu_components_r2.json` 的正常/延迟尾组两个场景均通过：

- 实际 D2H 8 层、H2D 4 层，17 层原生 K/V 池；
- donor 48 token、consumer 32 token，预留尾部 16 token；
- donor 原有 GPU reader 在覆盖前完成，受保护 resident 的全部 K/V 不变；
- Host donor 与 HBM consumer 的全部层 K/V 与参考逐项一致；
- 延迟最后一组 D2H 时，consumer 第一层读取在最终 donor ACK 前完成；
- 发起后原生 free count 仍为 4，尾部只在真实 ACK 后释放，最后为 20；
- donor 早返回不能读取未完成的 Host checkpoint。

这是带有限延迟的正确性夹具，正常小拷贝场景没有观察到第一层提前于最终
donor ACK；不因此推算真实工作量中的隐藏时间，也没有将该延迟注入 workload。
进程 616670 已实际退出、NVML context 消失，未启动 FULL 或 LLM 模型服务。

夹具修复后的最新单次共享检查为 3524 项及 4 个子测试全部通过，
保留前次 3519 项身份并新增 5 个 geometry 案例；报告为
`fixture_qualified_shared_cpu.*`。`gpu_review.py/.json` 绑定实际冻结应用、
原生引擎、组件源码与报告、最新 CPU 来源以及进程退出。原 R1 失败保持不变。

## 非强制模型验证

`model_r1/report.json` 使用同一冻结应用 `4675f4ad` 与原生 `1f5fac9e`，
实际发生 D2H 8 层、H2D 4 层的完整模型交接。consumer 恢复与 donor 再次
恢复的自然 greedy 输出均与独立参考完全一致，最大 logprob 差都为 0。
每个请求生成 8 个 token，seed21；没有强制输出录制 token。

该验证使用 49,152-token Device / 8GB Host 的正确性夹具、16K/16K/24K
三个源前缀和受控工具等待，不能替代完整 workload 或证明 JOIN 逻辑额度
交接已经带来 GPU/JCT 收益。server 623498 已退出、GPU 无 compute context，
清理记录无剩余 owned process。`model_review.py/.json` 复核实际报告、
分组组件、冻结来源、当前 CPU 绑定及退出，生成用于后续重放注册的
`native_sources.json`。正式 FULL 前仍需新 grouped 引擎的 24 请求
position/session/native-stop 检查及 running24 的 sampling 开销检查。

## 正式完整对比

`join_blocked_layer_position_20261028_r1` 已通过 24 个请求的精确 token、
session 及 native 停止边界检查；24 个请求均观察到原生 stop。
running24 的 position 采样额外派发开销均值约 0.01036ms。
检查用进程退出后启动 `join_blocked_layer_groups_20261028_r1`，port18677，
冻结应用 `913e3238`、grouped native tree `2ce5e5389979`。

`live_start.py/.json` 独立确认实际 nice0 watcher/server/driver、唯一 owned
GPU scheduler、实际新开关和 8/4 粒度生效、工具预取及优先窗口为零。
复用 V345 工具衰减对照，保留明确的 grouped/common 引擎差异。
两边工具时长 artifact 的存放路径不同，但文件 SHA256、entries、
transformed plan、种子、公开来源、分类及离线 prior 均相同并逐项核对；
不修改已冻结的注册记录。

完整评价前仍须全部 3674 请求、18 个正常长尾完成，原停止条件一致，
两条新鲜 idle telemetry 与实际退出通过。`complete_review.py` 同时核对
精确工作量，生成 decode 时间曲线、分布和相同 batch/context 几何对比，
并保留相对上一轮 joint FULL 和 online-refresh FULL 的退化或改善。
该运行尚无完整性能结果，不能据此宣称逐层分组的独立收益。

### 已提交 parent 的审计边界

首次运行中的 grant 前缀发现，审计器将全部 parent 授权当成 EOS 时对旧
epoch 的授权，要求随后服务的 epoch 必须增加。新增路径还能对已经真实
提交的 JOIN 后继请求授予额度，此时必须绑定相同 request_id 与相同 epoch。
仅为 `agent_join_blocked_capacity_admitted` 补齐该情况，仍保留实际 JOIN、
session/generation、完整回执、native clock 及授权后服务检查。

原代码反例为 1 失败/1 通过，修复后 45 项审计检查通过。同一个已封存的
前缀中，10 个阻塞交接的已验证服务从 4 个变为 9 个；剩下 1 个仍因当时
缺少完成/session 回执而保留未知，不记为成功。`grant_same_prefix_review.*`
固定原有五个 JSONL 的长度与 SHA256，确认只改变这 5 个已提交边界的关联。
原 `grants_prefix_r1.json` 与失败 CPU 报告均保留。此修改只影响独立审计，
正在运行的 `913e3238` 应用与原生引擎均未改动，也不改变实测 JCT。
