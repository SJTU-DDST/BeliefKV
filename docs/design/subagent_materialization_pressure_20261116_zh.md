# 基于有效 KV 压力的 Child 首次物化控制

本候选保留 subagent 的原任务、SPAWN、JOIN、请求登记和 workflow 到达时间，
在新 child 首次进入 native prefill 之前实施背压。等待时间属于原 workflow
的完整 JCT。功能默认关闭。`BELIEFKV_SUBAGENT_MATERIALIZATION=pressure_v1`
启用 child 首次物化控制，`pressure_v2` 将新 workflow 的 root 纳入预算。

## 新 workflow 的首次准入

`pressure_v2` 将新 workflow 的首次 root 请求和新 child 放入同一份
有效 KV 预算。两者都为当前输入和生成增长留空间，已启动请求的未分配
后缀和已经进入原生选择的请求先扣除。root 请求在首次 prefill 前接受
实时复核，等待从原 workflow 到达时开始计入完整 JCT。

出现预算竞争时，先从普通候选窗口排除当前预算放不下的请求，再按以下
顺序分配额度。如果没有候选能按预算启动，沿用原进展例外。

1. 优先满足真实阻塞 CALL 或 ALL-JOIN 的 child。同一个可见 ALL-JOIN
   在有界窗口内连续分配物化额度，容量只够部分成员时允许部分推进。
2. 再考虑其他在途 workflow 的首次 child。child 档内沿用 R5 的剩余
   关键路径比较。
3. 余量可以容纳新 workflow 时，在有界 root 候选窗口内按登记先后放行。
   放不下的 child 不会挡住可以使用余量的 root。只剩新 workflow 可以
   推动系统时，使用原进展例外允许一个 root 启动。

已获得原生服务的 workflow 中，普通续接直接使用调度器的恢复路径。
新 root 所需预算按实际已提交输入计算，后续出现的 child 在各自首次
物化时纳入预算。状态记录区分 held root 和 held child，完成后的
验收需同时检查 root 入场等待、已有 workflow 的完成时间、有效 KV
重算、共同窗口吞吐和可执行候选供给。

可用 `prepare_subagent_materialization.py --pressure-policy pressure_v2`
生成匹配对照。首轮构造验证采用 133 个原项目、Host 200 GB、20 个初始
workflow、平均 55 秒到达和固定 18 个测量成员。

## 目的与边界

控制新增 KV 状态的速度，避免过多 child 同时物化后挤掉仍有用的 Host 前缀。
它补充现有工具预测卸载和短 workflow 优先准入：前者管理已有状态的位置，
后者选择有限预算下推进的 workflow，本候选决定何时增加新的状态。

选择延迟首次 prefill，是因为该位置同时保留完整依赖图和原请求身份。
不用延迟 SPAWN 事件，也不用修改客户端、工具时间或 trace。
已有 child 的工具后继、JOIN parent、重试和分块 prefill 不重新作为新任务限流。
第一次收到请求时 delegation 尚未到达的情况，在后续真实 SPAWN/CALL 上补登记；
补登记只针对未分配 KV 的请求，并清除这类请求遗留的待暂停标记，
避免解除物化等待后仍被原候选过滤器排除。
已经分配 KV 或开始输出的请求不再撤回。

只支持当前 FULL-only、write-back Host cache 与 Full reference agent admission。
ThunderAgent、SGLang native 和其他控制臂强制关闭该功能。

## 实际容量口径

当前同构 FULL 池中以 token 槽记账，字节数由真实 `size_per_token` 换算。
完整普查遍历原生 `_node_arena` 的唯一节点，不逐 agent 累加共享前缀：

- 有引用或锁、且仅剩 Host 副本的状态计入有效 Host-only KV。
- 原生允许回收的完整重复备份，以及无引用无锁的 Host-only cache，
  可以计入可回收 Host 容量；物理回收仍由原生路径执行。
- 完整重复备份必须仍登记在原生 `full_host_duplicates` 回收索引中。
  Host-only cache 必须能按叶子到祖先的顺序安全释放；无引用的祖先
  如果被锁定、预取保护、DMA 中或仍有用的后代挡住，不能提供额度。
- DMA 中、Host 锁定或 JOIN 预取服务保护的副本不提供可回收额度。
- Host 池已分配但没有出现在树中的槽位保守保留，不能当作空闲。

令 `Fh` 为真实 Host free，`Rh` 为本轮可回收 Host cache，
`Fd` 为真实 HBM free，`G` 为现有需求预留和当前执行宽度对应的最小增长预留，
`U` 为所有已开始执行、在途、已被本轮 native adder 选中或由需求恢复流水线
暂存的请求，尚未分配的输入后缀与原有 buffer。它包含 root、普通后继和
已启动 child，按精确请求身份去重；已分配槽位和已选请求实际持有的前缀
不重复扣除。需求流水线清空 adder 后，尚未分配请求行的成员仍保留这份承诺；
成员必须与当前 live 请求对象及 visible/latest key 一致，取消或过期成员
不提供额外保留，也不为同一请求重复计价。
已经被本轮 native adder 选中或由需求恢复流水线暂存、但尚未收到最终
准入回调的首次 child，同样只在 `U` 中扣除一次。它们保留本次选择的许可，
不再次参与候选排序或占用候选窗口；选择取消后恢复为普通未开始候选，
不能因此获得永久物化豁免。
可用于新 child 的预算为：

```text
B = Fh + Rh + Fd - G - U
```

候选成本是当前已经收到的完整初始输入长度，加沿用的 native agent buffer，
按真实页大小向上取整。同一轮逐个扣减，多个 child 不能各自看到同一份预算。
这不是预测未来输出长度；同样不是整个 child 生命周期 KV 的上界。
共享初始前缀尚未被精确扣除，因此可能保守少放行，需要实际服务证据再改进。

`pressure_v2` 沿用原生的 `SGLANG_CLIP_MAX_NEW_TOKENS_ESTIMATION` 为当前请求
预留生成 KV，默认每个请求最多再预留 4096 token。当前输入加上已生成 token 和
剩余生成预留后，按该请求已分配或已持有的匹配前缀去重。现有 agent buffer
仍作为输入之上的最低预留。Host 需求预留与批次页对齐开销分别扣除。
这覆盖已提交请求的增长，后续请求收到的工具输出和新上下文仍可能带来额外需求。

这套控制不是简单的“Host 使用率超过 90% 就拒绝”：
Host 满但大部分为可回收备份，不应造成新状态停滞；
Host-only 压力很大但 HBM 有真实空闲页时，仍可使用这部分容量。

## 与调度器的关系

待物化请求仍保留为 pending，附加 `first_materialization_held` 状态。
限制同时覆盖首次登记、周期恢复、免费补位、sibling 共同准入、工具替换候选
和超时强制恢复，避免只过滤 native 队列却继续占用 active 逻辑预算。

候选先后复用当前 R5/Oracle 的连续剩余工作权重。
关闭工作量排序的 Full 保留原顺序，不增加新的老化、请求成本密度或 fanout 权重。
简化的全局关键路径 Oracle 不创建 R5 组合选择器；这条组合直接复用
既有 `_order_work_keys`，按含工具等待的剩余关键路径排列首次物化候选。
不能因组合选择器为空而退回登记顺序，也不能在放行前使用会排除 held child
的免费额度或普通窗口过滤。仅复用当前有界候选，不扩大扫描上限。
放行只解除本控制器的限制，实际 active 集合仍由原来的统一选择器决定，
之后继续通过原生输入、槽位、KV、锁与 ACK 检查。

初始 prefill 一旦获原生准入，即保留连续性。
其尚未分配的输入后缀继续记账；已经分配的槽位不重复扣除。
普通恢复请求不经过首次物化门槛。
许可等待普通准入时不构成物理容量租约。在 native 真正尝试一个新 child
之前、前缀匹配和需求 H2D 之前，复核当前空闲量、可回收额度和在途输入；
该次复核可以越过 400 ms 普通检查和 2 秒普查退避。
容量已被其他请求使用时撤销尚未物化的许可；已启动的请求继续执行。
复核使用原生已有的候选推进入口，不修改 native engine 或增加另一套调度时钟。

## 进展出口

仅依赖 Host 释放可能形成循环：parent 等 child，child 因 parent 占用 Host 而不能启动。
因此，资源预算不够、没有正在执行的请求、也没有现成的 root/已有状态请求可推进时，
允许一个候选申请进展例外。选择仍遵守相同工作量顺序，且自身必须满足单个逻辑预算。

例外不是物理容量证书，必要时仍会等待或由 native 执行原有恢复行为。
它在普通两秒窗口期间保留，避免尚未服务就被下一轮检查撤销。
该分支等待工具时不继续开放无关分支；如果它自身等待真实 JOIN/CALL 子依赖，
则允许沿已观察到的阻塞依赖继续推进一层，避免嵌套死锁。
终止/取消后回收对应状态。

代价是极高压力下 sibling 可能分批推进，以及进展例外可能增加 KV 压力。
需要分别记录例外次数和缺口，不能声称有限容量下无条件零重算。

## 控制成本

- 无待物化 child，或只剩已开始的 prefill 时，不做普查。
- 真实空闲页足以容纳所有当前初始预算时，只读取标量空闲量。
- 普通检查沿用 400 ms 兜底；持续高压的完整普查对齐现有完整控制周期，
  当前为 2 秒。退避期间保持已有承诺和新请求的 hold，不使用旧普查授权新 child。
- 真实请求完成、终止和传输完成 ACK 提前唤醒检查。
- 即将开始的新 child 额外进行一次当前容量复核；被 held 的 child、
  普通后继和已开始的分块/解码请求不因此每轮普查。
- 通过复核后仍被 native `NO_TOKEN` 拒绝的首次请求，复用 400 ms 退避。
  退避期间不重复扫描；真实完成、释放和 ACK 立即唤醒，普通输入批次的
  `OTHER` 拒绝不施加该退避。
- 至多普查 8,192 个树节点、排序当前窗口内 64 个候选。
  超界或几何不可观测时明确回退原调度，不伪造 Host-only 数量。
- 记录累计线程 CPU、完整普查次数、退避次数；只有决策改变才写详细日志。

`benchmark_subagent_materialization_cpu.py` 衡量标量节点替身的普查成本，
不代表真实 allocator、传输、完整控制或 GPU JCT。
实际原生 CPU 树的备份、拆分、ACK 和卸载计量由独立用例验证。

## 实验准备与验收

`prepare_subagent_materialization.py` 从最新 Full 配置生成 `control.json`
和 `pressure.json`，两者只改变本策略开关。可显式指定相同的较小 Host 池，
不额外要求两 NUMA 节点平均放置。

修复后的重复验证使用 `run_materialization_campaign.py --pair-only`，
只安排 control、pressure 两组。默认入口继续安排原四组；
阶段顺序仅接受完整四组或上述完整双组，不能通过部分列表或隐藏条目
混入其他方法。双组完成后生成本配对比较，不再请求四组基线报告。
当前四组冻结实验不热改；已有基线只在工作、资源和对应执行路径均匹配时复用，
不能将旧服务结果直接当作修复版 D3 收益。

最新联合筛查采用已完成的 133 项目、20 初始、平均 60 秒到达、Host 400 GB
轨迹，比较不同容量及同一随机流的到达间隔。首次匹配压力候选选择
**Host 200 GB、平均 55 秒到达**，固定 18 条测量成员与原 workflow 顺序保留。
`prepare_subagent_materialization.py --mean-interval-s 55 --host-gb 200
--include-baselines` 生成 Full 控制、Full＋物化背压、ThunderAgent acting-decay、
SGLang native 四份相同资源与到达配置。不为每个候选容量分别注册 GPU。

选择依据是既有完整测量生存期的唯一可复用旧 KV 需求：
固定旧轨迹时，200/224/240/256 GB Host 对应的超出 HBM＋Host 容量时间比例约为
18.64%/3.11%/1.04%/0%。200 GB 的峰值需求缺口约 41.26 GB；
160 GB 的超出比例则达到 45.56%，首轮不再进一步压低。
它们是每 60 秒采样的逻辑前缀并集与假设容量的对照，
不是实际 Host-only 占用、丢失字节、重算量或新到达下的需求预测。

55 秒相对 60 秒提高到达强度 9.09%。保持原实测 JCT 不变的覆盖筛查中，
55 秒背景到达末尾仍晚于最慢测量完成约 381.90 秒，50 秒则提前约 151.76 秒。
这不保证新策略下的尾部覆盖。现有有限 133 条轨迹的静态覆盖下界为
51.42 秒；若以后继续缩短，应先补足不同原项目的背景，
不能把背景停止后的排空当作稳定压力期间的调度证据。

原 60 秒运行完整测量生存期的平均 active-ready/ready-paused 为
16.47/3.38，约 15.42% 时间没有 ready-paused。这个 ready 数包括首次请求，
尚不能证明有足够的已有 KV 恢复候选。下一轮应分别统计：
已有物化状态的 ready-paused、已放行首次请求、被 held 的首次请求、tool-paused、
JOIN-paused 和实际 native decode 宽度。不能把 held child 当作恢复积压。
原进展出口只保证有界推进，不保证足够并行；不能以它作为吞吐不降的证据。

新增 `analyze_materialization_ready_supply.py` 只扫描一次封存 journal，
按精确 request/workflow/invocation/context/epoch 对齐原始请求，
把同一 context 的第一次请求与真正后继分开，不使用可能随重试变化的 steps 代替。
完整测量生存期 2,829 次控制观测平均有 3.897 个 ready-paused，
其中 2.821 个为已有前驱调用的续接；1,939 次同时有至少两个不同 workflow 的续接。
公共窗口对应 658 次观测、平均 2.190 个续接候选、389 次多 workflow 续接并存。
全部请求身份均可对齐。该结果不同于前述按时间加权的 3.38：
这是离散控制观测，不能解释为恢复可行性、实际竞争或新策略下的候选数量保证。
这些续接不经过首次物化门槛，为首轮压力配置提供了已有工作供给依据。

55 秒的名义新增输出约 371.52 token/s，旧运行公共窗口输出约 342.21 token/s；
二者的 1.086 比值不是稳定性证明，但提醒本候选可能属于有限过载诊断。
验收还需报告到达积压的漂移，保留所有排队和完整正常尾部。
该准备不注册 GPU，也不复用资源或到达不同的历史结果作为匹配控制。

完整 CPU 验证和构造检查的最终状态记录在同名证据目录。
GPU 阶段要求 Full＋背压相比相同代码的 Full 控制：
实际执行的旧前缀重算减少，公共墙钟窗口 decode 和总输出吞吐不下降，
完整测量 workflow 平均 JCT 不变差。另报告长任务等待、工具/请求恢复、JOIN、
CPU 开销与进展例外。decode worker 内部 token/s 不能代替墙钟吞吐；
不能把初次正常 prefill 算成重算，或只按瞬时 Host 占用宣布收益。
若 held child 增多时 native 执行宽度下降或空闲增加，必须计入负结果并定位，
不能用成功减小 Host 占用作为通过。

本轮联合协议相关 CPU 回归共 178 项通过，包含原物化、到达、Host 几何、
新配置对齐、外部策略隔离和首次请求/续接区分。
服务运行时源码保持 `440a6917`；本轮新增的是离线诊断及配置准备，
不能把此前 `bound_cpu_r2` 的完整服务资格说成新到达协议的 GPU 资格。
证据目录为 `experiments/reports/subagent_materialization_20261116/` 下的
`workload_pressure_screen_r3`、`continuation_supply_r1`、
`host200_mean55_prepared_r2` 和 `protocol_cpu_r3.xml`。

## 原生对照固定测量停止合同修复

继续保持已排队 Oracle 优先，本节不注册或启动新 GPU 实验。
在等待 Oracle 最后一条测量 workflow 时，实际生产停止器检查发现：
`host200_mean55_prepared_r2/native_matched.json` 没有声明
`comparison_contract.native_replay_baseline=true`，
因此 `native_baseline_declared()` 拒绝其固定测量停止协议。
原 178 项准备测试没有覆盖这条生产合同；保留原证据，不把它算作本路径已通过。

新增回归先实际复现拒绝，再由生成器为 native 显式补齐声明，
ThunderAgent 显式为 false。任务、工具、到达、资源及运行时不改变。
四组新配置位于 `host200_mean55_prepared_r3`，取代 r2 作为下一阶段准备，
旧 r2 不修改。`native_cohort_contract_before_r1.xml` 保留修复前失败；
`native_cohort_contract_after_r1.xml` 为 179 项通过，
`native_cohort_stop_related_r1.xml` 为额外 77 项生产停止器相关回归通过。

`qualify_materialization_protocol.py` 对实际四组 r3 配置分别执行生产环境转换、
runtime preflight、固定测量停止器入口及共同流量/资源检查。
`host200_mean55_protocol_r1.json` 记录全部通过，
并核对前驱完整服务 CPU 证据中的 169 份 runtime/policy/control 源码均未改变。
native 分支明确跳过 Full runtime 构造；其余三组通过各自的生产 preflight。
这是 CPU 协议资格，不是实际 GPU allocator、模型服务或新策略收益验证。
