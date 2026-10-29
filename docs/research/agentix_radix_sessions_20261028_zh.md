# Agentix 完整输入原生Session适配

2026-10-28。当前TokenCake使用冻结22caa011运行；本次Agentix改动只在
CUDA-hidden、nice19的CPU路径验证，没有进入该运行或启动Agentix GPU。
Agentix仍只暴露原来的四请求资格入口，正式baseline保持关闭。

## 实现边界

原Agentix swap拒绝所有非空session_id，而当前LoCoBench token重放
实际使用完整输入的原生radix session。现在仅在明确
full_prompt_replay_guaranteed=true、session缓存开启、原生generation
当前且未关闭时接受这种请求。保留token/namespace原样，不重构对话。
embedding、positional override、原生conversation session及session
params仍在准入账户或缓存匹配变更前被拒绝。

该检查接入adapter注册、容量匹配、容量消费、checkpoint准备和最终
ACK释放/恢复验证。原来的请求对象、输入/输出数组、sampler、attempt、
context和namespace验证均保留；只取消“所有session一律不支持”的旧
限制。暂停Host所有权仍由原来完整路径lease保护，不引入TTL、JOIN
优先级、agent连续资格或新的victim选择。

CPU使用真实Req、原生PrefillAdder、cache、allocator和实际消费者锁：

- 新session首次调用可完成ACK后换出与需求恢复；Host pin交给真实native
  consumer后释放，native DMA锁继续保护未完成H2D。
- 两个session共享prefix的checkpoint只保存一次，各自仍持有独立Host
  所有权和session/generation。
- 既有原生session anchors在checkpoint/demotion前后完全相同。
- 缺完整输入承诺、已关闭或过期generation等在原生变更前拒绝；已准备
  checkpoint也在释放Device前重新检查generation。
- native-session parent/child调用仍共享root workflow服务账户；未把
  child变成新的ATLAS program。

CPU夹具中，首次未完成调用尚无finished-history anchor是原生语义。
初始测试错误地要求其已有anchor，已改为验证真实暂停Host lease和完整
路径，并另测已有anchor的保持。另一个夹具以128生成预算要求64槽池
准入，原生正确拒绝；fixture独立预算改为8。没有放宽生产安全条件或
改变正式workload。原始失败XML均保留在TokenCake等待阶段的证据目录。

## CPU证据

独立目录`experiments/reports/agentix_radix_sessions_20261028/`保存
run_cpu.py、bound_cpu.json/XML/sources和review_cpu.py/cpu_review.json。
一次实际执行752项全部通过，无失败或跳过；全部来源执行前后相同。
原生仍为原Agentix独立c489dbb8树，copy/executor事件使用CPU stand-in。

原CPU入口随后因字面ID保留门槛拒绝，没有抹掉这个结果：旧690项的
字面ID保留686项，另外四项历史测试已扩展为八个observed/inferred
confidence参数用例。独立review只接受这四个明确映射，并重新核验
全部752项、源码与原生状态；不能称为“690个字面ID全部保留”或将它
与另一引擎的812项相加成一次执行。

## 后续限制

此次不提供真实GPU/session swap、原生LLM preemption/resumption、
有限Host/decode压力模型进展或完整性能证明。旧GPU入口的plain请求
不自动覆盖新session路径；必须扩展并执行它们，声明正式参数，再
登记同完整工作量的Agentix。TokenCake完成前不启动另一GPU作业。
当前TokenCake与完成的KVFlow/V345保持各自冻结来源，整体目标仍active。

## 原生准入消费者与压力验证入口

旧GPU swap和pressure夹具先手动load_back，再手工增加consumer锁。
新入口统一使用真实PrefillAdder发起需求恢复并取得锁，再通过原生
alloc_for_extend分配请求行和末token位置，最后由Agentix消费Host pin。
字节夹具不执行这个末token的forward，因此消费后归还该未计算位置；
恢复前已计算的完整KV、输入、输出与sampler逐项保持。没有把手工
lock或fixture行分配描述为整模型执行。

plain路径保留，同时增加显式`--native-radix-sessions`。小池压力检查
覆盖同一组Host耗尽、拒绝、不提前释放Device、容量释放后重试及六个
消费者完整恢复；session generation随每个请求核验。压力夹具的生成
预算明确取64-token上下文减去prompt长度，避免旧夹具将64生成预算
叠加到64上下文。正式workload和基线参数没有因此改变。

native_consumer_cpu一次执行762项全部通过，无失败/跳过，严格保留
此前752个字面ID，1879份来源前后一致，原生仍为c489。此前84项定向
执行单独保存为native_consumers_r1_cpu.xml，不与762相加。
校验器新增真实原生准入/分配回执要求；plain证据不能冒充session
证据，缺一消费者或手工锁报告均拒绝。GPU复制和executor仍为CPU
stand-in。新入口尚未完成真实GPU或整模型资格，Agentix仍未正式启用。

queue_components.py为四场景顺序入口：plain/session各自执行swap和
有限Host压力检查，仅在完整TokenCake实际退出、精确工作量核验、
两次新鲜idle快照和owned cleanup之后，检查GPU资源并启动。
四项独立CPU边界检查通过，证明前序仍存活时不查询GPU也不启动
子进程，cleanup文件不能替代实际退出，来源变化会拒绝运行。
该4项报告不与762项相加。冻结并登记后仍只是待执行的小池GPU资格，
没有自动启用Agentix正式baseline或整模型性能实验。
