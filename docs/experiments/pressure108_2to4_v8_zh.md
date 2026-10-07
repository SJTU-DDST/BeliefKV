# 108-root / 2–4 child 压力与机会诊断

## 最终 reactive 诊断与后续 predictive

v8c reactive 已在2026-10-07 19:45结束：108 completed、0 error/
incomplete，所有119个JOIN满足、232个child返回。9675.25秒，
40.19 completed workflow/小时，JCT P50/mean为3628.62/3697.63秒，
GPU全程平均55.33%；只有11个workflow两轮spawn，无三轮。
最后django-11734耗时9642.11秒，多次串行全量测试造成CPU长尾。
其Git HEAD与base commit一致，模型“当前HEAD更新”的说法不是事实。

重要失败：17:05:16，第18个PREPARE的原节点37在D2H期间拆分，
ACK正确发布535/37；账本按旧单节点集合比较后禁用所有物理动作。
只有17个PREPARE ACK，reactive虽完整结束，不能当作PREPARE
正常运行的正式baseline。默认汇总拒绝正确，不能删除检查。
诊断导出显式用 `--allow-degraded-runtime`，并标记
`comparison_eligible=false`；原始证据不回填、不“修成正常”。

完整原生H2D为8382批/2.473 TB，CUDA-event 73.14秒，
submit→ACK 825.29秒、enqueue→submit 64.48秒；D2H 2.234 TB，
CUDA-event 378.31秒。大流量不等于大JCT收益，安全点等待和
与计算重叠须分开。原生输入token命中约95.25%，FULL/Mamba Host
均达到满池。FULL已确认驱逐后重算1675 token；大量索引到期及
Mamba逐节点hit未知仍是归因缺口，不能把它当成完整重算上限。

修复采用native祖先链/代次/原Host目标索引确认D2H拆分投影，
继续校验pool、字节、session/epoch和重放。并修复忙碌状态心跳、
全量测试形态分类和shell管道退出码反馈；不增设agent guard。
后续只启动全新目录
`qwen35_joint_wait_h2d_predictive_108root_2to4_v8d` 的108-root预测
开发诊断，资源/权重/窗口不变，不自动重跑reactive或canary。
因源码修复且旧reactive策略失效，两者不能作为严格吞吐对照。
v8d已在 `0f04f70` 提交后启动，108条首轮全双task；启动检查
340次PREPARE issued/ACK、physical_disabled=false、receipt failure=0，
语义worker ready，JOIN/tool prefetch打开。验证395 passed/1 skipped。
旧reactive workspace已清理，v8d代码/参数/模型冻结，收益尚未验证。
以下保留原启动配置及历史截面，不覆盖上述最终诊断。

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
整合并继续复核/验证。首轮指定task并显式允许并行，不改变工具schema。
本机XGrammar的命名task结构仅允许一次调用；v8b ingress仅对
BeliefKV并行task选择使用task子集的required约束，允许重复生成。
但v8b的108个首轮仍全部单调用，已停止。v8c通过原生RepeatFormat
将首轮生成范围设为2–4，模型选择数量和内容，不在输出后拒绝
回复/补造child。后续轮次仍通过prompt和持久化结果提示派发，
实际次数须持续审计；不得声称语法约束已保证所有后续轮次。
专用委派要求位于通用“先读文件”提示之后。无补造child或格式门禁；
实际fanout以trace为准，不将prompt要求当作实测保证。

## 首次尝试已停止

`qwen35_joint_wait_h2d_ab_108root_2to4_v8` 在 `debe99d` 下启动，
首轮108条回复中105条无task、2条单task、1条双task。
早期JOIN组有7个单child、1个双child、1个三child。
因此不是合格的2–4对照；已停止client/server并释放GPU，
保留原始trace、终态和patch，清理该次遗留容器/workspace。
不将这次数据混入后续对照或新regime训练。
修复后v8b在 `026f650` 启动，但首轮108条全部单task，亦已停止，
不用于合格对照或新regime训练。两次都保留trace/patch后清理
workspace和遗留容器。
当前使用全新目录 `qwen35_joint_wait_h2d_ab_108root_2to4_v8c`，
仍只运行一对reactive→predictive，不排额外重复。

v8c已在 `c744461` 提交后冷启动，staging patch和模型SHA以新目录的
`ab_plan.json` 为准。启动后冻结源码/权重/prompt/参数，不能
将已停止v8b的CUDA graph或fanout数据代替新实验结果。
2026-10-07启动核对：108条首轮全部双task、108个双成员JOIN，
服务端记录108次2–4生成约束；prefill/decode CUDA graph成功捕获，
decode覆盖48；writer dropped/failed/error均0。
终态驻留/refs样本实际写在 `opportunities/admission_opportunities.jsonl`；
旧前缀缺失代理也已写入，几十token的小尾部不能直接当作驱逐重算。
后续轮次、完整H2D预算和吞吐结果仍须等新trace，不由首轮推断。

启动后已出现少量后续单child组，继续记录实际成员，不补造/
取消workflow，也不把首轮语法约束当成后续所有轮次的保证。
持续忙碌时状态JSON没有心跳更新：17:18:56后仍是旧快照，
因为writer只在队列空闲0.5秒或关闭时发布状态。原始JSONL仍
推进，当前计数以它和载荷一致性为准；不能引用旧零错误/命中率
作为实时证明。定时发布修复放在整个冻结pair结束后，不中途换代码。

一次原始记录截面：native H2D 8,083批、2.419 TB，CUDA-event
区间累计71.22秒、submit→ACK累计790.49秒；native D2H约1.593 TB。
已核对无重复提交时间/完整载荷，载荷字节等于FULL/Mamba单位数
乘相应尺寸。全部仍是进行中统计，ACK区间包含调度安全点等等待，
不能直接从JCT减去，也不能由它宣称预取独立收益。

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
失败证据；启动前重新检查磁盘，两侧顺序运行。系统故障及时
停止核对，不提前终止正常长任务以改善吞吐。
