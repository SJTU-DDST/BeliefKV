# TokenCake 原生 session 资格准备

2026-10-28。当前 KVFlow 正式对照仍在运行，冻结应用6dc4b892与登记
保持不变。本轮只做 CUDA-hidden、nice19 的 CPU 检查和后续 GPU
资格入口准备，没有启动并行 GPU 实验。

## 实际检查范围

原 TokenCake 组件已使用真实 native Req，但没有 session。本轮新增
完整输入、原生 session_id/generation、空 extra_key/cache_salt 的
实际 Req，并通过真实 PrefillAdder 获取消费者锁回执，检查：

- 已缓存 session 前缀的实际准入、完成和容量结清；
- 两个 session 的共享前缀只计一次，同时保留两个原生所有者；
- 工具阻塞期间保存，按4/2/1/1 token分批恢复，并由同 context 后继消费；
- 工具在第一批 H2D 中途返回，消费者先等待，随后由 native demand
  恢复未上传的4 token，原生锁接管后释放可撤销保留与 Host lease。

四个新增用例均通过。生产 TokenCake 的调度、优先级、保留策略、
传输和容量实现没有改动。不能把这项兼容性验证描述为性能优化收益。
CPU 的 copy/event 是 stand-in，不提供真实 DMA 或字节恢复证明。

第一轮两项通过、两项失败：旧测试辅助只设置了简化锁字段，真实 Req
具有单独的 lock_receipt，因此安全检查正确拒绝；另一个断言混淆了
工具阻塞期间 registry 的保留与工具返回后 checkpoint 接管的 lease。
第二轮还有一个错误断言：被移除 checkpoint 对象上的 lease ID 字符串
不必清空。最终检查实际 manager 归属、预留、原生需求 H2D 和 ACK，
没有为测试放宽生产安全条件。全部原 XML 保留。

## GPU 入口扩展

`qualify_tokencake_runtime.py` 保留原六个 plain-token 场景，另外对
full-prompt native radix session 执行相同六项：完整上传、D2H中途返回、
H2D中途返回、过期、退休和压力撤销。原来的字节、独占容量、native
consumer回执、需求回退和pin清理门槛均保留。

新 session 组另外核对实际 session 请求数、三个不同的有效原生
generation，以及 native source anchor/所有权。结果验证器要求两个
组分别完整，不能用 plain-token 结果或顶层成功标记替代 session 证据。
这十二个场景尚未在 GPU 上执行，正式 TokenCake 也尚未启动。

## 合并证据

`experiments/reports/tokencake_radix_session_20261028/` 保存
`run_bound_cpu.py` 和 `bound_cpu.json/.xml/.sources.json`：
同一次执行742项全部通过、无失败或跳过，完整保留此前726个用例，
新增16个，1873个源码文件在执行前后相同。原生为未改动的 clean
common11b84f7c3198；当前 KVFlow 的 frozen tree 不受影响。

同目录 `kvflow_collecting_while_cpu.json` 是独立 live-prefix 审计，
封存2274个对照一致的完成回执；实际 watcher/server/driver 存活，
预算字段未知时保留 null，不计算部分完成 JCT 或整轮性能。
该前缀不能替代完整3674请求、正常尾部和实际退出审计。

后续先冻结当前来源，再等 KVFlow 的实际 supervisor退出、owned清理
和新鲜资源检查后执行十二个组件。组件通过后才继续正式 TokenCake
配置及入口检查；Agentix正式臂仍未启用。保持同一 V230/V311 完整
工作量、不重跑 native/V345、不启动消融，整体目标仍 active。

## 顺序执行入口

`defer_gpu_components.py` 单独登记这十二个小组件，不启动完整模型。
除了前驱正常完成、watcher/server/driver实际退出，还检查每个前驱
arm 的 owned cleanup 边界及进程身份；之后才做两次新鲜资源检查。
CPU waiter 隐藏 CUDA，并在等待和运行时检查共享 reservation/pause。
来源、742项原始XML/manifest及之前证据均做内容校验；组件的实际
PID必须等于报告中的GPU PID，失败和停止都保留结果并清理子进程。

独立 `component_queue_cpu.xml` 的71项检查全部通过、无跳过，
包含顺序门槛、失败前驱拒绝、篡改/重复/失败CPU证据拒绝以及现有
paper启动和TokenCake验证器。这是启动入口的CPU证据，与742项
策略/原生session回归分开，不能相加为一次合并执行。

新增只读前缀 `kvflow_collecting_before_component_queue.json` 封存
3664/3674个完全一致的完成回执；它仍不是完整实验结果。当前GPU
组件尚未执行，任何后续资格或性能结论必须依据实际完整报告。

## 组件完成与正式配置

后续实际执行已完成：冻结应用eca8b38d在KVFlow正常退出和清理之后，
通过全部六个plain场景及六个native-session场景。组件PID1096881
退出0，waiter1096617也已实际退出。独立component_review.py/
gpu_review.json重新检查来源、原始报告、进程身份和cleanup，确认GPU
上下文不存在。此处仍是两层有限KV组件，不代表完整模型或性能。

正式配置为
`configs/baselines/tokencake_locobench_v230_v311_20261028.json`。
除TokenCake独立声明的七个参数外，共同环境与已完成KVFlow相同；
generation预算16384、context262144、running24、prefill16384、
chunk4096、policy block1、eviction window128。旧paper配置被直接
引用；不使用某个请求的录制输出长度作为容量预算或截断规则。

formal_bound_cpu.json/.xml/.sources.json是一次新的759项完整执行，
无失败或跳过，保留所有742项，新增15项顺序入口与2项正式配置检查；
1880个来源文件在执行前后相同。配置检查构造实际runtime并核验
TokenCake参数及Full策略隔离。这里的CPU检查仍使用copy/event stand-in。
register_formal.py在冻结后核验组件、前驱实际退出及当前资源，再登记
单独TokenCake；正式supervisor仍会在模型启动前重跑prefix和十二项
生命周期组件，不继承这次组件的整模型/性能资格。

## 首轮正式启动失败

e182a243正式登记在18703启动模型前通过新的prefix及十二场景检查，
随后实际请求触发 `TokenCake native admission bypassed its capacity guard`。
原始失败、HTTP回执与owned cleanup保留；不得当作完整baseline结果。

原因已从实际冻结原生源码确认：common11b84的scheduler有
on_prefill_candidate_result回调，却没有逐候选调用
allow_native_prefill_candidate。此前测试直接调用adapter/PrefillAdder，
没有执行该scheduler循环，因此组件通过未覆盖这个缺口。另需恢复
TokenCake独立引擎中原有的decode victim与释放前容量结清接线。

接下来基于common创建单独的TokenCake原生变体，只加入这些既有策略
必需的hook，并增加执行真实scheduler候选循环的拒绝/准入回归；
不放宽“未经过容量检查不能准入”的保护。先做原生接线、GPU组件与
有界实际模型检查，再用新登记重试完整工作量。当前TokenCake已退出，
没有运行中的完整TokenCake或Agentix对照。
