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
