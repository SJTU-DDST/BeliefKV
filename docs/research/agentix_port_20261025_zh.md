# Agentix 论文策略适配记录

2026-10-25。当前完成的是调度核心与执行记账组件，尚未启用 Agentix
runtime arm，尚无 AgentX 上的 Agentix JCT 或吞吐结果。

## 原始设计与身份映射

依据正式 NSDI 2026 论文《Agentix: An Efficient Serving Engine for LLM
Agents as General Programs》4.2 节和 Algorithm 1。固定本地文本：
`/home/longhao/experiment/ops/paper_baselines_20261024/papers/agentix_nsdi2026.txt`。
原文 PDF：`https://www.usenix.org/system/files/nsdi26-luo.pdf`。

Agentix 为程序维护已经完成的模型执行服务量。PLAS 累加已完成调用的
执行时间；ATLAS 为并发程序维护最长已观察服务路径的标量。每个新调用
继承程序当时的标量，完成后按自己的继承值与执行时间更新。这个过程不
需要未来 DAG，也不能使用 trace 中的原 API 延迟冒充实际模型执行时间。

本项目中 program identity 对应 root workflow；parent、所有层级的 child
以及辅助流的请求共享该程序身份，不能把每个 child 注册为全新的程序来
获得 Q1 优先级。请求仍是论文调度的基本执行对象。

论文同时使用离散的 FCFS 队列、时间片、基于程序和调用等待/服务比的
防饥饿、多 decode 步调度和聚合 KV 拷贝，明确考虑迁移与上下文切换成本。
只实现程序服务量排序不能作为完整复现，也不能将 agent 身份替换当作创新。

## 已实现的调度核心

`beliefkv/policy/agentix.py`：

- 新请求继承已完成的程序服务量，再按显式给定的区间进入 FCFS 队列。
- 时间片只消耗真实参与的模型执行区间；排队和外部工具等待不消耗时间片。
- 在完整调度时处理时间片耗尽和防饥饿，再按队列顺序进行容量检查；如
  Algorithm 1，遇到第一个不能容纳的请求即停止扫描。
- 完整调度间隔按已完成的 decode 步计数；期间保留已有选择，并允许已经
  在 GPU 的排队工作补入空位。所有先前选择都结束时允许提前重新选择，
  避免只因没有积累到 N 步而让 GPU 空闲。
- 选择结果只表达执行意图。只有实际原生准入更新 running 集合，真实
  forward 提交才创建执行记账记录。
- GPU 执行记录未结清时，不允许暂停、取消或完成对应调用；不能让异步
  流水线将上一批的执行时间计入下一批的请求。

队列区间、各队列时间片、beta 和 N 都是必填参数，没有将测试 fixture
中的毫秒数作为正式实验默认值。正式比较前必须单独登记参数。

论文伪代码未完全规定的一些边界处理明确如下：

- program waiting 累加已完成调用的真实队列等待时间。
- aging 清零调用的防饥饿计数器，但保留生命周期内的实际执行量用于
  PLAS/ATLAS 完成记账。这样遵循论文关于累计已完成服务的定义，避免
  Algorithm 1 中的计数器重置被解释为抹除已经获得的 GPU 服务。
- 最低队列时间片耗尽后，在该队列尾部重新排队并更新其时间片。
- 已经位于 Q1 的调用不做没有队列效果的重复提升。
- 服务量为零且存在正等待时允许提升；零等待不因 0/0 而获得提升。

这些是公开的适配选择，不宣称有未提供的作者代码参数或逐行实现等价。

## 执行时间账户

`beliefkv/runtime/agentix_executor.py`：

每次实际 worker forward 的请求组合绑定一条唯一记录，CUDA 事件在明确的
计算流上包围该 forward。异步查询完成事件后，才给该次 forward 的真实
参与者记账；一个共享批次的区间给每个参与调用计入一次，不按 batch size
相除。调度期间新增或移除别的请求不能改写已提交记录的归属。

区间包含该 forward 在计算流上的依赖等待和 host dispatch 间隙，不是排他
CUDA kernel 时间，也不是 HTTP 响应时长。原生接线必须在安装该 forward
的流依赖之后开始记录，不能把其他请求的旧 forward 或初始外部等待计入。

未完成记录有固定数量上限，满时明确失败，不能丢掉样本再估算服务量。
完成、暂停和取消前需结清相关记录。只使用 event query，不为了记账
调用 synchronize；输出结果的原生正确性同步仍由引擎负责。

begin/end/poll 各自累计调度线程 CPU 时间；不逐步写日志。正式运行还需
测量真实引擎接线后的总控制开销，三个小探针不能证明整体开销足够低。

## 已有证据

- `experiments/reports/agentix_executor_and_mlfq_cpu_v2_20261025.xml`：
  64 项通过，包含既有 PLAS/ATLAS 服务继承测试。
- `experiments/raw/agentix_executor_cuda_v218_20261025/summary.json`：
  H200 上三个实际矩阵 forward，请求组合分别为 A、A+B、B；输出结果、
  按请求累加和 workflow ATLAS 更新均核验通过。CUDA 计时记账没有主动
  同步，执行记录全部结清后才读取矩阵结果。该进程正常退出。
- CUDA 检查只验证事件接口和执行记录绑定。首个区间包含冷启动开销，
  这些毫秒数不能当作 LLM profile、Agentix 吞吐或未来队列参数。
- 现有 `RUNTIME_ARMS` 和 adapter factory 尚未加入 Agentix；完整物理能力
  不足时，正式实验入口仍拒绝以 Agentix 名义启动。

## 尚需完成

1. 原生 LLM worker forward 接线和真实排队/准入/暂停生命周期账户。
2. 在已排空 GPU 流水线的安全点选择性暂停运行中请求；保存已计算的
   prompt 与生成前缀，保持 output IDs、采样位置和请求身份不变。
3. 聚合缺失的 D2H 后再按 ACK 释放实际独占 HBM；共享页不能算作已释放
   空间，不能以会触发整段重算的原生丢弃路径冒充论文的 KV swapping。
4. 被暂停请求的 Host 副本保护、原生容量准入、真实 H2D 和恢复队列接线。
5. CPU 原生生命周期检查、实际 LLM 抢占恢复 GPU 校验，然后在同一完整
   AgentX 人口、300/10 秒空闲规则下运行正式对照，保留全部正常长尾。

这些必需项仍未完成，因此不能将本次组件通过登记为 Agentix 复现完成，
也不能用它开始宣称相对 Agentix 的性能提升。
# 最新执行顺序与未完成项

2026-10-25 用户要求先启动 ThunderAgent、KVFlow、TokenCake，再继续
Agentix 适配。当前 Agentix 未注册为可运行 arm。

ThunderAgent 已完成，KVFlow V220f 正在运行，TokenCake V224 已冻结并
排队，Agentix 的 CPU 适配现已恢复。

此前 `agentix_swap.py` 的暂停叶 Host 锁不能保证完整前缀的 Host 所有权：
原生 Host 副本回收检查祖先节点自身锁，节点分裂也不继承 Host 锁。
现改为复用原生前缀组件的逐段所有权，暂停时保护完整已计算前缀，分裂时
继承新增祖先的引用。增量 D2H 之前先保护旧的已备份部分，ACK 后交给
完整前缀引用；只有实际原生 consumer 的节点回执、FULL 锁和分配量满足
恢复条件才释放暂停所有权。共享页仍只按原生分配器的实际增量记作释放。

暂停请求采用显式释放生命周期，不设任意的超时；恢复、取消和关闭释放
其 Host 引用。该类引用不进入到期堆，与已有有限 deadline 租约共同存在时，
堆压缩和到期回收也不误释放暂停前缀。保护只在抢占/恢复及原生分裂事件处
维护，不逐 decode 扫描全部暂停请求。

CPU 证据：
`experiments/reports/agentix_host_ownership_shared_cpu_retry_20261025.xml`，
1409 项通过，覆盖完整前缀回收压力、暂停后分裂、增量备份、真实 consumer
交接及已有公共策略。`qualify_agentix_swap.py` 已增加对应的 GPU 分裂/回收
检查和源码绑定，但本次尚未执行。正式原生 executor、抢占/恢复队列仍未
接线；不能将上述 CPU 检查或三次矩阵 CUDA 接口检查当作完整复现完成。

## 原生 worker 绑定与取消准备

`AgentixWorkerExecution` 现提供显式、仅作用于指定 worker 实例的绑定。
调度器绑定检查单卡单 rank、普通生成、无 speculative/diffusion/pdmux，
并选用原生 overlap forward stream 或非 overlap schedule stream。实际调用
worker 时再验证当前计算流和每个请求的 root workflow 身份，按该次原生
请求集合冻结执行记录；同一批对象后续复用不能改变旧记录的归属。

绑定包围原有 `forward_batch_generation`，不替换 model runner、采样器或
延迟采样闭包。原生 forward 抛错时仍提交结束事件，保留已经发生的执行
区间并拒绝新 launch；只有事件结清后才能取消调用或卸载绑定。恢复
worker 原方法时保留原有实例覆盖，也不会覆盖后来安装的其他 hook。
控制 CPU 计时只统计绑定逻辑与 executor helpers，不将原生模型 dispatch
时间算成控制开销；这是计时边界检查，不是实际 LLM 开销测量。

同时修复暂停准备的取消路径：取消只释放控制器持有的 Host 引用，
不释放 Device 页、不修改请求行，也不撤销原生 D2H 锁。因此不再要求
请求仍未 abort、tokens 未变化或全局流水线为空。真正 prepare/commit
及已执行调用的取消仍保留各自的 GPU drain 和执行记账要求。

CPU 报告：
`experiments/reports/agentix_worker_and_abort_native_shared_cpu_20261025.xml`，
148 项通过。包含对固定 SGLang `TpModelWorker` 原方法体的普通/延迟采样
调用检查，model runner 为 CPU stand-in；也包含原生树上的增量备份取消、
Host 引用释放与 DMA/GPU 锁保留。该绑定尚未安装到正式 native LLM
调度器，真实 GPU hook、抢占/恢复队列和实验参数仍待完成，正式 arm
继续关闭。V220f 和 V224 的冻结源码、引擎与登记均保持不变。

## 原生 adapter 的 CPU 集成

新增 `beliefkv/runtime/agentix_baseline.py`，将原生 worker 绑定、call 生命周期、
实际容量探测、批次选择、聚合备份和选择性重入队组装到独立 adapter。
共用 runtime 新增显式 `attach_native_scheduler` 接口；尚未在独立引擎构造
路径中安装该调用。adapter 在 scheduler 绑定之前拒绝接收请求，仍未加入
正式 factory 或 `RUNTIME_ARMS`，没有可用于比较的 Agentix GPU 结果。

生命周期使用 root workflow 作为 program。native 完成结果到达后，同一
请求可能仍有已提交的下一次 overlap forward，因此延迟完成和取消记账，
直到该请求的真实执行记录全部结清。同一 program 的新调用暂缓继承，
保留其原生到达时间，等此前已报告完成的调用结清服务量后再进入对应
队列；其他 program 不因此等待。不把请求的重入队视为新的程序或调用，
不重置其时间片、历史执行量或排队年龄。显式 workflow 结束取消剩余
未完成调用，已收到的真实完成事实仍按原先结果结清，等待 GPU 后再退休。

选择钩子保留原生安全检查，只在实际 forward/result 流水线排空后进行
完整重排和 checkpoint。时间片耗尽后按论文队列选择目标，对实际未被
选中的运行请求合并提交 D2H；原生 ACK 确认后才释放请求行与独占 FULL
页，调用原生 `ScheduleBatch.filter_batch` 保留其余计算状态，再通过
原生队列回调重新登记同一 request。恢复时消费真实 consumer 的前缀、
锁和分配，不重新建立服务账户。

多 decode 步之间保留当前选择；只有生命周期变化才检查 GPU 已驻留的
补位请求，普通保留轮不扫描整个等待队列。控制器单独累计线程 CPU
时间。CPU stand-in 的计数与计时不证明真实引擎控制开销足够低。

`beliefkv/runtime/agentix_capacity.py` 从共同原生树匹配已经到达的输入和
已经生成的输出，以节点身份合并共享 FULL 前缀，另计独占输入和显式
decode 增长。先完成全部匹配，再解析路径，避免后续 match 引起的节点
分裂让先前的容量视图遗漏共享祖先。增长预留必须至少覆盖声明的多步
调度间隔加两步 overlap，不读取 sampler 的记录最终输出长度。仅在 Host
的前缀可以参与逻辑容量计算，但不能当作已驻留补位对象；最终准入、
分配和 H2D 依赖仍由原生引擎决定。

新增集成报告：
`experiments/reports/agentix_native_adapter_all_baselines_cpu_20261025.xml`，
604 项通过，覆盖 Agentix、AgentX 入口、其他论文 baseline、Full 原生树
生命周期和逐步恢复。执行和复制使用 CPU stand-in，原生 cache、allocator
与 batch 过滤为真实代码；初次共享前缀 fixture 失败报告另行保留。

这还不能登记为完整复现。接下来必须完成独立引擎构造接线、真实 LLM
计时与选择性抢占/恢复、Host 紧张时的回压及进展、native decode 压力
处理，以及独立于 fixture 的正式参数声明。目前不允许以原生丢弃后重算
替代论文的 checkpoint swapping；遇到这种回退或 Host 备份失败会明确
使运行不合格，而不是继续生成一份策略不符的性能结果。V220f 和 V224
的冻结内容未变，最终同工作量 JCT/吞吐验收仍未完成。

## 有限 Host 容量与独立引擎启动

新增 `AgentixHostBacking`。这是有限 Host swap 空间下的显式移植选择，
不宣称是论文另行给出的算法或新的 Full 策略。保留原有论文队列次序；
资源门槛按已观察的 live 调用前缀并集、独占输入和 decode 增长计算，
为推进一个调用保留配置的最大 context 容量。未开始的请求不能无限增加
必须保留的 checkpoint；已完成调用的普通 KV 仍交给原生缓存管理。
该适配要求 Host 至少容纳两个配置的最大 context，HBM 至少容纳一个。

若已有 live 状态的增长触及正常 backing 门槛，只让论文队列首先选中的
一个 live 调用继续推进，保留至该次调用完成，再释放其 live backing
额度。这个身份是请求，不是 agent 会话，不跨工具轮次维持资格，也不
使用最终输出长度或未来 DAG。正常资源充足时仍按原有多步、FCFS 队列和
时间片执行。该模式及其次数单独记录，真实资源下的进展仍需 GPU 验证。

原生备份返回失败时，记录缺失 Host 槽位与实际可用槽位。明确由容量
拒绝造成的失败会保留原生运行批次、GPU 分配、队列位置和时间片，不
丢弃 KV、不假装已经抢占；容量、DMA 或完成/取消状态变化后再重试。
状态未变化时跳过全队列扫描和重复 D2H。Host 明明可容纳却无法提交的
失败仍报错，不能把未知物理故障当成正常回压。native decode 容量压力
的完整 GPU 验证仍待完成，不允许用原生未 checkpoint 的重算回退补齐。

独立引擎：
`/home/longhao/experiment/.worktrees/sglang-agentix-runtime-20261025`。
在相同基础引擎上只新增 cache attach 后的 scheduler attach 调用，不修改
V220f 或 V224 的冻结源码。tree：
`c489dbb85265efce25622a52abfd698e9199688d`。规范 patch：
`/home/longhao/experiment/ops/sglang-agentix-runtime-20261025.patch`，SHA256：
`15fe59ebe16721e5ee07244d0d25381b0187b91b1afa57f8fd06281fe051cd09`。
规范 patch 与工作树已核验一致。

新增资格验证专用入口 `agentix_atlas_native_qualification`，要求显式
`BELIEFKV_AGENTIX_QUALIFICATION_ONLY=1` 和配置文件。固定声明位于
`configs/baselines/agentix_native_qualification_20261025.json`：最多四次请求、
running=1，以小时间片触发原生抢占。该参数只用于有界组件验证，不能作为
正式实验配置，也不能接受完整重放。正式 `RUNTIME_ARMS` 仍无 Agentix。
有界 LLM 验证 supervisor 已在后续 V225 冻结并排队，详情见下节；
实际 GPU 验证结果仍待产生。

最新 CPU 报告：
`experiments/reports/agentix_isolated_host_backing_all_baselines_cpu_20261025.xml`，
628 项通过，使用上述独立引擎。包含原始 scheduler 初始化方法体通过真实
factory 绑定 adapter 的顺序、请求额度、有限 backing 门槛、Host 回压后
状态变化重试，以及全部现有共用检查。执行和复制仍为 CPU stand-in，
不能替代真实 LLM 抢占、各层 KV 字节恢复、压力进展和控制开销测量。
正式 Agentix baseline 仍未注册，最终性能目标未验收。

## 有界原生 GPU 验证已排队

`scripts/qualify_agentix_runtime.py` 已冻结为 V225：
`experiments/raw/agentix_native_runtime_v225_20261025`。应用 commit：
`b093658c194f88fac09901c106adf18992f23f59`；引擎 tree 和规范 patch
仍为上节的独立 Agentix 版本。supervisor PID 3034622，start ticks
`715243559`，端口 18634。已核实进程存活、普通 CPU 优先级和等待状态，
尚无 GPU 子进程。等待的是 V224 的真实 supervisor 退出、终态工件及
自有进程全部清理，并重新核验 GPU、Host、端口和用户 GPU hold。
不能将启动阶段暂时空闲的 GPU 当作前驱已结束。

启动模型前重新执行源码绑定的真实 prefix 和 Agentix swap GPU 检查。
模型探针从固定发布 token 包选出
`agentx:94fa70f6a3f08f4260afe7713d2de2808f54:request:27`，
输入 110720 token、输出 18 token。前三次请求并行提交到独立 namespace，
以 running=1 和资格验证小时间片触发抢占，第四次复用第一个 context，
检验恢复后续请求和生命周期排空。要求实际 CUDA forward 记账、正的
checkpoint 释放与恢复量、原生选择性抢占、全部 token 契约和两个新鲜
idle 遥测快照；每次失败保留工件，只清理自己的进程。

最新 CPU 报告为
`experiments/reports/agentix_runtime_supervisor_all_baselines_cpu_20261025.xml`：
667 项通过。它验证排队、前驱身份与输入/源码绑定、并发探针和结果门槛，
使用的 CPU stand-in 不能证明真实 GPU 抢占已通过。token 契约也不能证明
模型 KV 数值等价。V225 的 roomy pool 不验证有限 Host 压力和完整人口
性能；后续仍需真实 Host 回压、decode 容量压力进展、正式参数及配对比较。

## 压力组件与遥测修复后的排队版本

新增 `scripts/qualify_agentix_pressure.py`。小池固定为 128 个 Device token
槽位、257 个真实 Host 槽位、64 token context 和两层 FULL KV。六个 live
调用的初始 backing 需求为 192，正常门槛为 193；实际增长两个 token 后
需求到 194，触发 drain。只保留选中的一个 live 调用继续增长到本次完成，
完成后重新开放未开始调用的准入。另用真实 Host allocator 占满剩余槽位，
要求备份拒绝不释放 HBM、不破坏暂停前缀；释放实际 Host 槽位后再备份和
恢复，最终逐一恢复六次暂停的全部各层 K/V、请求内容及 sampler。

CPU 版本使用真实原生树、分配器和 Host 槽位分配，复制和完成事件为同步
替身。共享报告 `agentix_pressure_all_baselines_cpu_20261025.xml` 为 690 项
通过。首次检查中的 dtype 和 write-back 替身协议错误报告保留；它们是
探针错误，不能归因为正式策略或 GPU 的错误。实际 GPU 组件证据仍待产生，
完整模型在 Host/decode 压力下的进展也仍待单独验证。

随后发现共用遥测的实际 bug：V220f 完成全部 2229 次请求，却在收尾时
保留 9 条 pending，恰好对应 9 次 native retraction。已启动请求重新
`on_enqueue` 时又进入 pending，而完成只移除 active。修复后重排不改变
原始到达时间，也不重复登记已启动或已完成的调用。先保留两项 CPU 失败
复现，再完成含遥测、Agentix、其他 baseline 和比较入口的 1091 项回归：
`experiments/reports/native_telemetry_requeue_all_baselines_cpu_20261025.xml`。
没有清零旧状态、放宽 drain 条件或改写旧轮为成功。

V224 在模型启动期间、正式请求提交前被行政停止；V225/V226 在等待时
停止，全部自有进程已清理。保留旧状态和工件，用新快照依次运行：

- KVFlow V220g：应用 `bb63a5b66b5cd42689b87757b48f5929e4a75bd1`。
- TokenCake V224b：应用 `dea4616ee40749363329d16af9d30fb76e12dbb1`。
- Agentix V225b：应用 `2b80c0d59af9b92ae0e226b7c6cd48c6e00f55ae`，
  PID 3047217/start `715454684`，端口 18638。
- 压力组件 V226b：应用 `214f46cad2a6fcc033b21ed90883a6e7a1b8549b`，
  PID 3047528/start `715458321`，端口 18639。

V225b 仍使用原四请求 roomy-pool 探针，源码清单新增遥测模块绑定。V226b
使用小池及显式 fixture drain，不执行 LLM。两者都不是正式 Agentix 性能
配置。后续真实模型压力探针可从同一固定 token 包使用
`agentx:cbbac1ac4e0a88cb6ee971ef166fe6a24222:request:13:25`
（112768 输入、4376 输出），但其池容量、context、运行并发和预期压力
需另行显式声明与核验，不能悄悄替换完整比较的公共容量。
