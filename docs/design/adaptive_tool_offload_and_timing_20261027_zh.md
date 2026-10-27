# 工具卸载、预测适配与后台备份

本候选单独声明在
`configs/migration/predictive_adaptive_tool_offload_20261027.json`。
保留 V269、V286、V295 和固定一秒门槛候选的配置与结果。
当前变更尚无整模型 GPU 正确性、控制开销或性能验收。

## 1. 用迁移成本决定卸载门槛

旧的独立物理卸载候选采用
`P(remaining > 1000ms) >= 0.9`。
新候选通过 `BELIEFKV_TOOL_OFFLOAD_POLICY=demand_cost` 启用：

1. 最多每 200ms 检查八个实际等待请求。受益者必须仍在原生队列中、
   尚未分配可变请求 KV，并有原生 session 检查证明的 Host 缺失前缀。
   仅有首次冷 prefill、队列非空或缓存占用高均不足以触发。
2. 必须存在实际容量缺口。目标包括缺失前缀、原生允许的本次输入增量、
   当前原生增长预留。请求已可容纳时停止主动驱逐。
3. 受益者须已获得 agent 资格，或在现有 blocked-budget lending 规则下，
   仅借出本次实际 donor 的预算即可容纳。保留原生物理准入权威。
4. donor 必须仍在真实工具阻塞中，预测与 session/epoch 有效，
   无在途计算、恢复预留或工具预取保护。仅释放独占、已完成 Host
   备份、原生锁允许的 Device 叶节点，不驱逐 ready/排队 peer。
5. 迁移门槛为：

   ```
   H = 本次尚缺 D2H 的 P90（含排队）
       + 受益者缺失前缀 H2D 的 P90（含排队）
       + 本次及此前已卸载 KV 的未来 H2D P90（含排队）
       + 2 * 200ms 控制周期
       + 需要逻辑借出时的一次 1000ms 资格维护预算
   ```

   要求条件分布给出 `P(remaining > H) >= 0.9`。
   已有 Host 副本时 D2H 成本为零，不能仍按完整备份收费；
   受益者自己的恢复也需要时间，不能仅检查 donor 的往返开销；
   逐段释放后累加未来需要恢复的大小，不能把每段都当作一次廉价小恢复。
   尚无同大小、同方向 FULL 传输证据时，显式使用 8GB/s 的工程回退，
   并保留至少一秒门槛。这个带宽是启动假设，不能作为实测结果引用。
6. PREPARE 排队前、事件 drain 后和每次物理释放前重验。
   工具返回、受益者离队、容量缺口消失或预测变短均撤销授权。
   备份入队本身不算释放显存。

这里检验的是“有真实受益者，且预计有足够迁移窗口”，并不证明本次切换
一定改善 JCT。新日志保留受益请求、动态门槛、预测概率、备份与恢复预算，
后续还须关联实际准入、重复 H2D 和暴露等待。

## 2. 当前实际预测路径与离线先验

V286 配置使用 `ONLINE_TOOL_TIMING=1`、`TOOL_TIMING_ARTIFACT=""`，
实际没有使用旧 SWE-bench 离线模型。此前同时提供旧 artifact 和在线开关，
也不会自动得到两者融合。

新增 `ToolTimingPrior`，通过路径与 SHA256 显式装载，
与旧模型 artifact 互斥。新候选采用：

- 同一可观察工具类别的离线 Kaplan-Meier 时长先验；
- 当前 arm 已完成调用形成的 workflow/run 历史，64 次滑动窗口；
- 以八个等效样本为先验强度，在线权重 `n/(n+8)`；
- 对“已经等待了多久”进行条件化，再输出剩余时间分布；
- 每个运行重新开始在线历史；未知 domain 和无支持的尾部弃权；
- 物理卸载额外要求当前幸存尾部有支持，不把无限尾部外推当成证据。

先验的作用是冷启动，在线历史用于场景适配，不应每轮在评测轨迹上重训
再测同一轨迹。若切回自然工具执行，须从独立采集的真实调用准备对应
domain 的先验，并按项目/session、时间划分训练和评测；网络合成先验
不能直接用于原始 shell 命令，更不能宣称恢复了旧 SWE-bench 的准确率。

## 3. 修正合成工具时长的信息契约

旧网络池并非仅 WebSearch，而是 Claude 的 WebFetch + WebSearch：
3260 条相关记录，2944 条缺少内部时长，3 条重复，313 次有效调用。
这些缺失限制了分布的代表性，仍保留在审计中。

V1 把混合分布施加给原始 `execute`，预测器看到的仍是原命令类别，
因此原命令与合成时长之间的对应关系不再成立。旧结果不改写。

V2 明确声明为“可观察工具延迟类别”的合成敏感性负载：

1. 先按原始 provider/session 的固定哈希划分总体，未读取时长做筛选。
   54 个 session 的 222 次调用用于离线拟合，17 个不同 session 的
   91 次调用用于合成评测时长。
2. 对每次调用先按独立固定哈希选择类别，再在该类别的留出样本中
   用另一个哈希抽取时长。改变时长值不改变类别分配。
3. TOOL_START 提供 `timing_domain`、`observed_timing_class`，
   所有方法获得完全相同的类别；`execute` 的原始名称、输出和 token
   保留。样本 ID、所属 public session 和未来时长不会送入在线策略。
4. 注册时验证先验 SHA、domain 和留出 session 集合，拒绝重叠或错配。
5. 这仍是合成时间，不代表这些 shell 调用实际执行了网络工具。

准备文件为
`experiments/reports/v311_v230_categorized_network_timing_cpu_20261027.json`
及同名 `.prior.json`。18 workflows、3674 请求、145565966 输入 token、
549671 输出 token、72 JOIN 和全部因果依赖不变。
931 次目标调用的合成时间中位数为 5733ms，P90 为 16339ms，
最大值为 61752ms，不删除正常尾部。

留出集的离线 P10 下界覆盖率分别为 WebFetch 88.89%、WebSearch 89.29%；
P90 上界覆盖率分别为 95.24%、92.86%。这些是有限样本的离线诊断，
不是在线准确率，也不提供 90% 的概率保证。未据此选择获胜参数或 trace。
新时间文件改变了实验负载，不能复用 V286/V295 作为匹配的性能控制。

## 4. prepare_host 与 CPU 控制传输

当前后台路径仍是 200ms 节拍，但使用原生增量 dirty FULL 索引，
有节点数和字节数上限，优先冷 owner，并在恢复/容量关键事务前让步。
不是每 200ms 遍历和复制全部 HBM。
备份可以覆盖已证明尚未开始服务的队列 owner；
这不赋予将其作为普通预取 donor 驱逐的权限。

实际 V286 使用 `kernel + page_first`。原生备份路径调用 GPU gather/copy
kernel，不能仅因为 PCIe D2H 平时空闲就认定它不影响计算。
原生 direct 分支还会准备 CPU 索引，可能出现索引同步和提交成本。

新增 `BELIEFKV_IDLE_PREPARE_IO_BACKEND=direct`，只在 FULL-only、
`layer_first`、无 storage 且原生传输队列空闲时，为一个后台 PREPARE
提交作用域使用原生 direct 路径；作用域退出或异常立即恢复 kernel。
原生锁、分配、stream 依赖和 ACK 仍保留。
正常 H2D 和按需 D2H 继续原来的后端，direct 的 ACK 不混入 kernel
迁移成本估计。布局不兼容时保留原生路径并计数。

当前 `page_first` 不能直接启用该 direct 方式。新增 launcher 的
`HICACHE_MEM_LAYOUT` 开关，默认保持 `page_first`。DMA 跟进实验必须
单独声明所有 arm 同用 `layer_first`，验证数值、ACK、CPU 提交成本、
计算干扰与带宽后，才决定是否采用；本候选暂不启用。

## 5. 增长水位

固定的作者快照中 `BUFFER_PER_PROGRAM=100`，容量检查包含每个 active
program 的 100-token 缓冲，并在周期检查发现超额后处理。
不能把它误报成另有一个固定 90% 的原生物理空闲页水位。
现有复现及我们的组合层已经保留这 100 token。

新增 Full 独立的 `BELIEFKV_AGENT_KV_HIGH_WATERMARK`，默认 1。
例如 0.95 给逻辑 active set 预留额外 5% 池容量，同时保留原有缓冲；
单个请求只能独占才能容纳时允许使用这个软预留，避免被水位永久挡住。
不减小实际 KV pool，不凭空授予物理容量，不修改 paper baseline。
逻辑增长缓冲也不保证原生缓存一直有同等数量的物理空闲页。

本次主候选仍用 1，防止同时改变工具预测、物理驱逐、传输布局和容量
水位后无法归因。后续据实际 decode 增长、回收、重算和 batch 利用率
决定预留量，不能只凭较低占用宣称更优。

## 验证范围

新增 CPU 测试覆盖 session 分割、类别分配不依赖时长、domain 隔离、
离线冷启动与在线适配、错误先验/时间文件拒绝、实际原生树与 allocator
释放、PREPARE/ACK、队列消失和预测变化竞态、工具返回后的停止、后端
恢复与 paper arm 隔离。原生 copy/event/Host 索引仍有 CPU stand-in。

共享回归与 source-bound 构造检查由
`scripts/qualify_adaptive_tool_policy_cpu.py` 执行，必须保留 V307 的全部
用例，并补充核心 runtime、在线预测、后台 prepare 和本候选测试。
CPU 通过不替代真实 DMA、整模型正确性或完整 JCT/吞吐对比。

最终 V321 在一次执行中通过 2890 项用例和四项子测试，无失败/跳过，
保留 V307 的全部 2523 项用例，新增 367 项。
V322 绑定最终源码、配置、时间文件和真实 runtime 构造检查，
确认 Full 启用 `demand_cost` 和固定 SHA 的先验，默认及 optional-decay
ThunderAgent 均禁用这些扩展。
V316 保留两个旧核心测试的失败；在冻结的
`d6a4ce4c7bc11d6fc64b20edf45bb655907141b1` 上独立复现后，
只修正其测试事件顺序，使 parent 先完成 JOIN 再进入工具等待。
V319 保留新增逻辑借出用例的 fixture 失败，修复为有实际竞争计算的场景，
避免被原有 idle lending 提前准入。运行时保护没有为这些测试放宽。
