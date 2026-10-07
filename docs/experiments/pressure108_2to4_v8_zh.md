# 108-root / 2–4 child 压力与机会诊断

2026-10-07用户授权108个root单波、每轮同一响应派发2–4个child，
观察H2D预算及重算。本轮同时改变root与fanout，是联合负载诊断，
不隔离其中单项收益。只做一个reactive→predictive pair，不排
重复/canary，不自动扩128。配置见
`configs/migration/qwen35_108root_2to4_v8.json`。

## 配置

取既有128-task训练manifest前108项，保留顺序，不按完成结果选题。
模型Qwen3.5-35B-A3B BF16、SGLang0.5.20、running48、Host200 GB/
NUMA1、Device Mamba/FULL0.9与Host匹配比例不变。Context131072、
completion8192、workflow14400秒、graph2048/32步reserve、seed21不变。
两侧共享prompt、通知、收尾优先级、PREPARE、真实压力回收和诊断；
预测侧才提前恢复，每侧冷启动。模型权重沿用v7并冻结。

新profile `native_in_graph_2to4` 保留历史1–4 profile。
每轮要求同一assistant响应发出2–4个互补task调用，等待ALL后
整合并继续复核/验证。首轮auto，不改变工具schema，避免命名task
选择只生成单调用。无补造child、取消workflow或格式门禁；
实际fanout以trace为准，不将prompt要求当作实测保证。

## 缓存诊断

H2D/D2H批次字节、CUDA-event区间、submit→ACK和排队分账；
native/predictive、FULL/Mamba分账。ACK不是纯DMA或关键路径，
H2D字节不代表完整oracle收益，分配前等待和重算另关注。

异步比较同context/native session中先前已完成生成的输入前缀
与当前输入，分开新输入与已服务但未命中的共同前缀。它是
recompute proxy，不是逐层kernel FLOPs，也不独自证明驱逐原因。
新session/compaction重置范围，不把有意改写prompt算成缓存丢弃。
Host块归因检查完整32768-entry有界索引，不再只取最近1024长度，
仍报告索引到期/缺身份。Mamba逐节点hit location仍有缺口，
不伪造精确重算slot计数。

结束child独占无用数据应可释放，不应盲目D2H挤占Host活数据。
记录session retire开始、ACK、失败、耗时；关闭引用不等于释放缓存。
Server在RETURN捕获native叶子与祖先，随后有界观察GPU/Host、
锁、session refs、节点消失/代次变化。共享祖先可能仍属于parent、
其他child或未来可复用前缀，不能相加当独占死字节，不能直接DROP。
该诊断不改变native驱逐/释放策略。

## 预测与验收

更高root与更多child改变GPU服务间隔、工具CPU并发、任务大小和
回流开销。旧工具CDF、phase/encoder/work在新regime只作冻结诊断，
不假定校准依然有效，不改eligibility。结束后按fanout、unfinished
成员数和压力评价工具、child、完整JOIN误差，再决定重拟合/校准。

JOIN_ALL真实完成取完整成员集最大RETURN。现有提前恢复要求只剩
一个未完成成员，不用首个child通知提前唤醒parent，不把各child
边际区间组合冒充校准JOIN区间。EOS前模型与EOS后观测继续分账，
未来GPU排队只作标签，不塞进在线输入。

每侧结束自动导出 `memory_opportunity.json`，汇总迁移服务预算、
旧前缀损失/新输入、Host归因、终态残留及真实fanout，保留全部
需求和异常。随后清理不用workspace，保留trace、patch、summary、
失败证据；磁盘目前约102 GiB空闲，两侧顺序运行。系统故障及时
停止核对，不提前终止正常长任务以改善吞吐。
