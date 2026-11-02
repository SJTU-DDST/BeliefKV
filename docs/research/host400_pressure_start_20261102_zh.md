# 400GB 持续到达压力实验启动记录

2026-11-02 后续用户明确取消均匀 NUMA 分布要求。本文件中的严格驻留和
均匀性启动检查保留为历史证据，不继续作为新实验前置目标。新的主线见
`../design/current_gpu_oracle_goal_20261102_zh.md`。

2026-11-02，用户再次授权 GPU 实验。目标是检验更高在途人口能否增加真实
ready-paused 容量竞争。当前启动序列为完整 Host 组件、位置/采样检查、
单轮非排序 Full。没有注册后继 oracle、learned 或新 baseline。

## 固定配置

- 73 个不同项目的完整原任务及原始 token/依赖，沿用已有工具 overlay。
- 400GB 十进制 Host，实际完整 FULL 池目标为 400000057344 字节；
  CPU 在节点 1，Host 显式 `interleave:0-1`，核对完整页面分布。
- 20 个初始 workflow，后续泊松平均间隔 70 秒，原 arrival seed35 和排列
  保留。固定测量随后 18 个 workflow，余下 35 个为背景。
- HBM 805198 token、running/decode 上限 24、2048 prefill、mixed 关闭。
- Full 维持非排序的 agent 工作集、工具预测卸载、prepare_host、JOIN/EOS
  与 sibling 支持。工具预取和逐层 handoff 关闭。
- 所有测量成员自然完成后才停止背景，并保留完整 JCT。新测量成员、
  到达及 Host 容量与旧 200GB 轮次不同，不能直接计算其“排序收益”。

新到达参数入口没有改变默认工作负载；当前生成器重建后，原定到达表和
私有 cohort 在结构上完全一致。原 f7781302 的生成源码也保留。证据为
`host400_component_lifecycle_cpu_r1/preparation_compatibility.json`。

## 启动前修复与失败保留

400GB 组件原先把周期状态写入独占创建文件，第二次发布便会退出，
并可能遮盖原始分配错误。`e7ac175b` 改为现有的原子状态发布接口，
保留 entry 等一次性记录的独占创建。实际 run 控制流的成功/失败 CPU
检查由 2 失败变为通过；完整回归为 7134 项及 23 个 subtest，保留 7132
旧身份，2473 份源码和 418 个外部输入，零失败/错误/跳过。

首次链路 `workflow_original73_host400_pressure_20261102_r1.chain.json`
在导入 native/deep_gemm 时因 CUDA_HOME 缺失退出。尚未分配 Host 池，
没有完整组件资格。r2 显式使用配置中已有的 CUDA13 工具链，保留 r1。

r2 又在 GPU 独占检查处退出：当前 PyTorch 的 `cuda.init()` 不立即创建
NVML 可见的上下文。独立 1 字节实际 GPU 探针观察到初始化后进程表为空、
分配后唯一 PID 正是探针自身。证据为
`host400_component_lifecycle_cpu_r1/gpu_context_visibility_probe.json`。
这不是 400GB 或模型验证。

组件因此增加极小的实际设备分配与同步，然后仍要求 GPU 进程集合严格
等于自身；额外记录实际/期望 PID。匹配的 GPU 控制流 stand-in 检查由
失败变为通过，旧 CPU 检查身份保留。r1/r2 日志与退出状态不改写，不
将失败启动计为已完成的 GPU 对比。

后续 r3 绑定新的完整 CPU 资格与干净副本，再启动完整组件。r3 的结果
需根据实际进程、组件状态、原生 Host census、24 请求 smoke、采样
检查和 Full 服务回执确认，不能仅凭 watcher 已登记判断已完成验证。

r3 实际完成原生全量分配，但在注册前的严格 NUMA 均匀性检查被拒绝，
映射随后已释放。该轮没有启动模型、没有完整注册资格。之后的独立
CPU 全量观测 `host400_actual_interleave_diagnostic_r1/state.json` 保留了：

- N0 199160172544 字节，N1 200839852032 字节，份额约 49.790%/50.210%。
- NUMA 读数合计 400000024576 字节，比目标少 32768 字节；原 resident
  检查仍拒绝该未注册快照，未据此宣称完全驻留。
- 分配约 144.846 秒，CUDA 未初始化，映射实际释放，来源未改变。

新资格将“均匀”明确为节点页数差最多占总页数 1%，并在配置及证明中
记录上限和真实比例。GPU 组件保留分配前后原始快照，允许未注册阶段
的 resident 观察不充当最终资格；注册后仍必须完整驻留、节点受控、
拷贝正确和完整释放。边界与失败清理的 81 项聚焦 CPU 检查已通过，
新的完整保留回归与 r4 重新冻结随后进行。未修改任务、到达或调度策略。

后续 r4 使用完整 7143 项及 23 个 subtest、2474 份源码和 433 个外部输入
的保留资格。实际全量分配约 143.312 秒，CUDA 注册约 21.465 秒，记录
24 个连续注册区间；随后首尾检查在跨区间的 128 字节 D2H 处报
`invalid argument`。所有注册已注销，映射已释放，原始日志和失败状态保留。
它证明注册调用完成，但未完成拷贝/最终页面检查，不能当作完整组件资格。

该错误来自检查代码的探针偏移：在每个新注册区间前 64 字节开始一笔
128 字节拷贝，会跨越两个独立注册区间。原生注册实现明确要求单笔传输
保持在一个区间内。独立 8192 字节真实 GPU 探针验证，四个区间内首尾
拷贝均正确，跨界拷贝复现同一错误，随后正常拷贝仍正确，注销完成；
证据为 `host400_registration_boundary_probe_r1.json`。

`a472aedb` 将探针改为每段的起点与终点前 128 字节，仍检查全部注册
区间、完整覆盖和实际双向字节；新增检查要求每笔探针完全落在某一段内。
相关 50 项 CPU 检查通过。完整保留回归和 r5 新冻结随后执行；r1--r4
均保留为失败启动，未更改原生引擎、工作负载、工具时长或调度配置。

完整保留回归随后通过 7147 项及 23 个 subtest，保留全部 7143 个旧身份、
2474 份源码和 443 个外部输入；零失败、错误或跳过，来源/native 在执行中
未变化。干净副本 `beliefkv-host400-copy-boundaries-a472aedb` 已逐文件核对
实际 CPU 执行字节，证据为 `host400_copy_boundary_bound_cpu_r1/` 下的
`evidence.json`、`sources.json` 和 `clean_freeze_r1.json`。

2026-11-02 11:26:25 UTC，r5 链路已登记并启动实际 waiter 303749；
仅这个记录不能证明组件或 Full 已开始。组件、模型位置/采样和固定测量组
observer 仍由后续实际状态及 GPU 进程验证。

11:30:32 UTC，r5 完整组件实际结束并通过：400000057344 字节分配约
142.984 秒，24 段注册约 20.505 秒，每段首尾 H2D/D2H 合计验证
12288 字节。注册前后节点 0/1 均为 200000028672 字节，页面总数完整，
策略和映射未变；全部注销、映射释放和输入字节不变已确认。链路随后
确认组件实际退出，再次观测 GPU 空闲后启动模型资格。证据为
`host400_native_registration_component_r5/execution/state.json`、
原始 maps/numa_maps 和 `operator_result.json`。

随后 r5 正式模型实际完成权重、图捕获和 400GB Host 分配/注册，健康检查
成功，但在 11:35:28 UTC 的启动 NUMA 审计报
`the complete Host pool must be resident`，未进入 24 请求 smoke。旧入口在
这个错误发生前没有保存原始 maps/numa_maps，因此不能推断差额的大小或
原因，也不能用完整组件的成功替代正式模型资格。

清理先记录 `owned replay processes remain after cleanup`，当时唯一剩余
worker 仍在退出；该失败保留。之后单独确认所有 watcher/server/worker
实际消失、GPU 进程表为空，再允许重试，未修改原失败状态。

`11702399` 让正式 placement 观察先保留每次原始快照和 SHA256，只有
“驻留不足”可最多重读三次，间隔 100ms；最终仍必须完整覆盖原字节数，
且原有节点、策略、页面大小和进程身份检查不变。持续不足仍失败，错误
策略不重试，更换 PID 身份不允许接续。84 项相关 CPU 检查通过；完整
保留回归、新冻结和 r6 模型资格仍待实际结果。这不是放宽完整驻留门槛，
也不能提前认定 r5 是瞬时采样问题。

该严格快照版本完整回归随后通过 7151 项及 23 个 subtest，保留全部
7147 个旧身份、2475 份源码和 453 个外部输入，零失败/错误/跳过；
证据为 `host400_residency_retry_bound_cpu_r1/`。尚未据此运行 r6 GPU。

正常位置验证结束也会走相同的清理入口，因此还需处理已观察到的 72 秒
退出期限不足。`9739dad9` 根据实际原生 census，仅在 Host 池超过
200000077824 字节时，将 SIGKILL 后等待上限由 60 秒扩展到 180 秒，
SIGTERM 仍为 12 秒。继续核对实际 PID/start ticks 和 GPU 资源，不将
已发信号视为已退出；期限后仍有 owner 则继续拒绝后继。较小池的期限
不变。五项聚焦检查通过，包括慢退出、持续存活和 PID 复用；完整绑定
转为 `host400_residency_retry_bound_cpu_r2/`，r6 只使用新的干净冻结版本。

最终完整绑定通过 7153 项及 23 个 subtest，保留 7151 个旧身份，
2476 份源码和 459 个外部输入；零失败/错误/跳过，来源/native 不变。
`host400_residency_retry_bound_cpu_r2/clean_freeze_r1.json` 已逐文件核对
`beliefkv-host400-teardown-9739dad9`。11:55:51 UTC，r6 链路实际启动，
完整组件随后实际占用 GPU；此登记仍不等于 Full 已开始。

r6 完整组件实际通过；随后正式模型启动，但三次 NUMA 原始读数分别比
97656264 个基础页少 130、25、3 页，最终差额为 12KiB，严格门槛仍拒绝。
三次 maps/numa_maps 和检查记录均保留于 r6 位置验证目录的
`server/host_numa_observation_325724_7791375772689625/`。这轮清理约
190 秒，实际 owner 退出，未再出现旧清理错误。

后续 `f45feaa0` 曾将严格观察扩至 30 秒/最多 16 次，并为大池退出留
300 秒上限；CPU 绑定完成 7155 项及 23 个 subtest、2476 份源码、
476 个外部输入。用户随即取消均匀 NUMA 分布要求，r7 GPU 链路未登记。
这些严格观察改动不再是新实验主线；新配置应将 NUMA 观察改为诊断，
复用已有完整组件证据，优先验证调度竞争与 oracle 收益。

## 验收范围

先观察 ready-paused 的时间占比、跨 workflow 真实竞争、授予到实际服务、
HBM 有用状态、Host 前缀丢失与实际额外重算，并核对完整背景覆盖。
GPU 利用率和缓存池物理占用保留为诊断，不代替有效 KV 或调度机会。

如果更高并发下竞争仍不足，才缩短平均间隔，例如先从 70 改为 60 秒，
并为后续匹配控制重新冻结同一到达表和吞吐窗口。先验证 oracle 的实际
集合/服务/JCT 价值，再推进 R5 learned；当前初版预测的早期精度风险
仍见 `../design/r5_conditional_distribution_implementation_20261102_zh.md`。

## NUMA 诊断模式与新压力控制

`cfe9754a` 将页面分布验收与池容量处理分开。显式
`HOST_NUMA_PLACEMENT_CHECK=diagnostic` 保留原分配策略，只做一次尽力观察，
不要求两节点平衡，不等待基础页计数相等。预检改为检查所选节点的合计
内存预算；实际 400000057344 字节容量、原生注册、传输及 token 检查保留。
历史严格启动失败不改判。

本次仅运行受影响的启动、容量、回放和测量组停止检查：597 项全部通过，
零失败、错误或跳过，绑定 2160 份当前源码输入。复用旧全量回归及 r5/r6
实际组件证据，未重跑全量组件。证据目录为
`useful_token_cost_r1/host400_diagnostic_pressure_r1/`。

新位置校验登记为
`ops/workflow_original73_host400_diagnostic_position_20261102_r1.registration.json`，
冻结 `cfe9754a`，原生引擎保持 `434c35dd66211d0a230717c88bf1452056769a40`。
实际 census 验证 805198 Device token、4069011 Host token，24 个原始
token/session/stop 请求通过。单次 NUMA 计数少 1073152 字节，作为诊断
保存，流程照常进入校验服务释放。此处尚不代表 Full 已服务、完整测量组
完成或 oracle 获益。

接续只登记一轮非排序 Full，继续使用原 20 初始/平均 70 秒、固定 18
测量成员及原始轨迹和工具 overlay；测量组停止观察器先等待真实服务再
启动。oracle 配置准备与 GPU 登记分开，必须先检查真实竞争。后续比较
采用当前目标的约 1% 改善作为继续验证依据；旧配置中的 5% 提升门槛仍
保留为历史元数据，不应据此误报当前目标通过或失败。

位置校验的实际释放从 20:44:07 到 20:47:15（Asia/Shanghai），约 188
秒，所有 owner 实际退出；采样检查随后通过，20:47:54 完成非排序 Full
登记。新控制保持冻结 `cfe9754a`，不因之后的 CPU 观察器修复重启。
CPU-only oracle 准备验证了同一 73 workflow/11796 请求、global 排序开启、
组合准入关闭、2 秒合并窗口、JOIN 绕过窗口及 active 连续性；没有登记
oracle GPU 后继，尚无竞争或收益结论。

上述实际退出时间也暴露了收尾观察器的另一处旧上限：它只等待监督进程
100 秒，短于当前原生清理的 12 秒 TERM＋300 秒 KILL 有界期限。仅对实际
Host census 超过旧 200GB 的池，将测量组完成后的监督退出等待改为 340
秒；client 退出、原始 receipt、两个新 idle snapshot、PID 身份和实际退出
验证不变。46 项测量组停止/完整 JCT 检查通过。先前等待正式 Full 的
bootstrap 已在尚未登记观察器时安全停止，将以修复版本启动新的 CPU
观察器；不修改正在运行的服务源码或测量 JCT。

修复提交为 `8cc0633c`；新的固定 18 成员 CPU 观察器 PID 349507 已实际
运行，正式 Full 的 scheduler PID 347393、监督进程 PID 346708、client
PID 348856 均保持原服务冻结版本。独立存活、祖先关系、实际配置、池容量、
原始 token/hash 与 native sampler 核对通过，前缀共 114 个 receipt/
5661 个输出 token。见 `host400_diagnostic_pressure_r1/live_verification_early.json`。

早期只读观察分别封存，时间范围并不相同，不能混成一个原子快照：

- `supply_early.json`：观察至 150.76 秒，21 到达、零完成；共同窗口内
  87 个状态均存在 ready-paused，平均 8.47 个，5 个采样状态有跨 workflow
  逻辑竞争；没有 Host block 驱逐或已归因的 Host block 重算。
- `control_opportunities_early.json`：70 次实际控制中 47 次非空，4 次跨
  workflow 逻辑集合竞争，3 次 oracle 集合改变；共同窗口部分对应
  35/3/2 次控制/竞争/集合改变。44 次排序改变不能当成 44 次有效优化。
- `periodic_service_early.json`：稍晚 103 次控制，10 次逻辑竞争；37 次
  实际授予均保留并获得精确后续服务，其中 22 次在下一控制或已记录
  同 agent 干预前新服务，20 次在该边界前完成有效 batch。4 个观察窗口
  尚未关闭，不作为失败；其余边界外服务不能算作边界内进展。
- `idle_supply_early/report.json`：共同窗口已观察 55.69–197.79 秒，GPU
  时间加权利用率 98.99%、零值样本时间为 0；fresh 状态覆盖完整，持续有
  ready-paused，时间加权均值 8.19。池物理驻留仍只是诊断，不等于有效 KV。

这些证据说明新压力配置早期已形成候选供给及改变集合的机会；尚未证明
完整背景覆盖、稳态、全程重算、oracle 物理替代可行性或完整 JCT 改善。
继续本轮固定测量，不因早期数字再改到达表或重启服务。

### 首次诊断压力运行提前失败

20:57:23（Asia/Shanghai），原生 scheduler 在
`staged_write_back.cuh:96` 的 `cudaMemcpyAsync` 报 `invalid argument`：
相邻 Host 页被合并为一次 D2H，跨越独立的 CUDA Host 注册区间。随后
runtime socket 关闭，client 的 `ConnectionRefusedError` 是后续错误，
不是原始根因。监督进程最终 `stopped_for_investigation`，观察器同步
保留失败；23 个 workflow 已到达，固定测量成员尚未完成。所有实际
owner 均已退出。该轮只有早期机制证据，不是完整 JCT/吞吐对照。

新 `BELIEFKV_HOST_REGISTERED_COPY_SPLIT=1` 是共同数据面修复，不是
调度创新：从真实 Host buffer 的注册元数据缓存 K/V 联合 token 边界，
只在跨边界时分组调用原生 staged backup。区间内继续合并；原生 stream、
staging、复制顺序、controller ACK 和页锁不变，不改 native 引擎。该开关
必须同样应用于 Full、oracle 和 ThunderAgent，原任务、工具、到达与容量
不变。

`registered_copy_gpu_r2.json` 用 3000041472 字节的实际 Native MHA Host
小池、三个注册区间复现错误并验证修复：48 层 K/V、16 token 双向检查
各 1572864 字节，实际 hook 将一笔跨界备份分为 3 个原生提交，完整注销，
native 和输入均未变化。68 项相关 CPU 检查通过。首个 GPU 负控制曾遗留
线程 CUDA last error，导致其后的校验失败，保留为 `registered_copy_gpu_r1`；
r2 明确清理负控制错误再验证修复，不将首个结果算作通过。

修复后的新压力证据归属
`useful_token_cost_r1/host400_registered_copy_pressure_r1/`，复用原始完整
400GB 分配/注册证据，仅补受影响的实际复制检查与相关 CPU 检查，不再
重跑均匀 NUMA 资格或全量 7000 余项回归。只有该轮完整测量结束后，才能
形成匹配控制并继续 oracle 的性能验证。

修复提交 `cd56c385`，新的受影响路径资格共 650 项通过，源码与 native
在执行期间不变。修复后的登记为
`ops/workflow_original73_host400_registered_copy_position_20261102_r1.registration.json`，
实际 watcher PID 357755 启动；其后继仅为匹配的非排序 Full，不是 oracle。
旧失败服务遗留的 Unix socket 已在确认所有 owner 退出、无 listener 且
连接被拒后保留改名，不覆盖失败日志。新配置与旧配置的唯一环境变化为
共同数据面的 `BELIEFKV_HOST_REGISTERED_COPY_SPLIT=1`，任务、token、依赖、
工具 overlay、到达表、测量成员及 400GB/805198 token 容量保持不变。

### 当前目标的配对分析

新分析器增加两个显式选项，默认历史行为不变：
`--allow-legacy-control-declaration` 只容许旧非排序控制的“尚未配对”字段
为 false，oracle 仍须明确声明配对；任务、工具、到达、资源、完整原始
工作量和仅排序开关差异继续逐项验证，不重写任何注册配置。
`--minimum-jct-gain .01` 明确采用用户重设的约 1% 目标，并在结果中同时
保存实际门槛与旧配置 5% 门槛。吞吐非劣门槛、完整测量组和背景覆盖
检查不受该选项影响。

37 项分析检查及 11 个 subtest 通过，包含 1.5% 改善在不同门槛下的判断、
吞吐回退仍拒绝、非法目标拒绝，以及旧声明兼容不能放宽资源匹配。
这只是分析路径资格，不代表已运行 oracle 或得到任何完整性能收益；
正在启动的服务与冻结代码不变。

### 修复后的实际大池运行前缀

位置 token/session/stop、采样及实际清理通过后，Full 冻结于
`cd56c385` 开始回放，实际 scheduler PID 361249、监督进程 PID 360581、
client PID 362669；固定测量观察器 PID 362672 实际运行，使用同一冻结
版本中已修复的大池退出等待。

21:36:21（Asia/Shanghai，对应服务端 America/Los_Angeles 日志 05:36:21），
实际大池出现边界拆分日志。随后封存的 `boundary_use_and_progress.json`
包含 3228 个新正进展服务 batch，Host 高水位 217532694528 字节，Host
驱逐及已归因的 Host block 重算为零，既有前缀损失 proxy 为零，telemetry
无丢弃/错误。它证明大池已实际使用修复并继续工作，不代表后续完整
运行已通过，也不把两个重算口径相加。

`host400_registered_copy_pressure_r1/live_verification_after_crossing.json`
独立核对 770 个精确 receipt、原始 token/hash/sampler、实际容量、所有
存活 owner 和观察器。`control_opportunities_after_crossing.json` 封存
198 次控制，其中 29 次跨 workflow 逻辑集合竞争，16 次本地 oracle
集合变化；25 次竞争的剩余工作比至少为 2。另一个独立前缀
`periodic_service_after_crossing.json` 封存 197 次控制、110 次实际准入，
资格均保留，107 次精确后续服务；86 次在下一控制或已记录同 agent
干预前获得新服务，84 次在该边界前完成有效 batch。前缀间并非原子快照，
未知后续服务不算失败。CPU 只读审计使用 nice19、CPU0、单线程、隐藏 CUDA。

匹配 oracle 在 `host400_registered_copy_direct_oracle_prepare_r1/` 重新
完成源图和实际构造核对，保留 global 排序、2 秒窗口、JOIN 绕过窗口、
active 连续性及共同传输修复，组合准入仍关闭。`goal_pair_contract.json`
直接验证原始配置，只有四个工作量路径/哈希字段变化，不修改旧控制的
配对声明；新 artifact 仅切换排序。没有登记自动 oracle GPU 后继。

对旧失败前缀还保留了 `packing_failed_prefix.json`：12 次反事实集合
变化中 7 次成员数不变、5 次减少，均不存在还能直接放入的未选单体，
3 次涉及测量成员。这是需要在 oracle 比较后核对的并行度与短路径
取舍，不是漏装对象、最优组合或 JCT 收益的证明。当前负载早期已具备
足够供给来继续完整对比，不改到达表或在途中调整策略。

后续前缀已出现非零 Host block 重算，不能沿用跨界检查时的零值：
`host_recompute_first_observation.json` 匹配 618–620 三个删除块到同一
GraphQL 项目的新 child 请求（context epoch 0），约 19.9 秒后重访问，
合计 4852 token。Host 高水位约 309GB，池容量驱逐调用/单位仍为零，
同会话已服务前缀损失 proxy 仍为零；块删除计数和池容量驱逐计数并非
同一口径。现有记录没有证明具体删除原因，不据此宣称 Host 满或策略
无重算。固定测量组已有 1 个成员自然完成，服务与观察器继续运行。

### 同配置 oracle 顺序执行

控制组继续推进到 15/18 测量成员完成；Host 高水位达到 400000057344
字节，并已出现容量驱逐，同会话前缀损失 proxy 仍为零。Host block
重算后续为 10995 token，非首次观察的 4852；仍单独报告两种口径。

已登记并确认 PID 373202 存活的顺序执行器：
`ops/workflow_original73_host400_matched_oracle_20261102_r1.plan.json`。
运行源码与 oracle 仍为 `cd56c385`，只读分析器冻结为 `f470525c`；
候选配置和接续证据放在 `host400_registered_copy_pressure_r1/oracle_chain/`。
当时状态为 `waiting_complete_control`，`oracle_GPU_registered=false`。
旧准备记录中的“无自动后继”是历史准备状态，本节登记了新的条件接续。

接续前必须核验固定 18 成员完整自然结束、每个原始 token/stop receipt、
两个新 idle snapshot、实际所有服务 owner 退出、完整工作负载审计通过，
以及共同窗口仍有 oracle 会改变集合的竞争。任何条件不满足，都保留证据
并停止后继。oracle 启动后同样登记固定测量观察器，不等全部背景结束。

由于只改变工作量排序开关，数据面源码、模型、原生引擎、容量、图、
sampler 和 token 数据均与本轮位置校验相同，直接通过现有
`run_causal_comparison.py` 的完整登记检查复用该资格，不再次启动独立
400GB 组件或重复的位置校验；新服务仍核验实际配置/容量并保留完整原始
receipt。该复用在新的 oracle 配置中显式说明，不改写历史 GPU 证据。
GPU owner 继续 nice0，CPU 分析使用 nice19/CPU0/隐藏 CUDA。

配对分析显式采用 1% JCT 目标与原有共同窗口吞吐门槛，保留控制、候选的
完整审计结果。单次达到门槛也仅进入重复验证，不自动进入 R5 或将目标
标为完成。若没有收益，先核对选择是否落到服务、容量组合及执行成本。

额外只读诊断 `tool_path_rank_inversions_failed_prefix.json` 保存旧失败
前缀中 148 个逻辑竞争候选对观察：模型路径与模型/工具总路径排序方向
相反的观察有 4 次、4 个不同请求对。独立最长模型路径和外部路径不是
可直接相加的组成项；频次也不能限定 JCT 影响，现阶段不据此改策略。
