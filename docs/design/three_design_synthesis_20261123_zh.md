# MigraGate 的三个设计点

更新于 2026-11-27。分段写回和回载以 `a7b698e2` 为基线，新增按需回载后的 Host 页归还机制，已通过 CPU 验证。三档主结果均来自历史 `a7b698e2` 的 `full_d3` 臂。固定配置为一张 H200、200 GB Host KV 和写回上限 1。133 条工作流构成到达负载，论文指标使用固定 38 条。历史实验标签 `D3` 同时涉及首次物化与迁移保护，复用结果时按生效配置归因。[实验手册](../implementation_plan.md#paper-evaluation-runbook) · [数据与审计入口](../../paper/icml2027/README.md)

## 1. 跨 Spawn 与 Join 的父 KV 迁移

**Parent KV Migration Across Spawn and Join.** 父 agent 委派任务后仍可继续推理。`SPAWN` 或 `WAIT_JOIN` 触发 `PREPARE_HOST`，只异步补写稳定前缀中缺少有效 Host 副本的页。副本确认后，父任务仍可使用 HBM。待父任务阻塞且就绪请求需要空间，再复核页归属、锁和在途传输，释放已备份的设备页。

对于等待全部成员的 JOIN，最后一个未返回 child 的有效 final EOS 确定了将恢复的父上下文。引擎在容量允许时提前回载父 KV，与 harness 交付结果、组装后继请求的时间重叠。固定 38 条在 80、65、55 秒三档的定向 JOIN 分别匹配 148/148、150/150、145/145，EOS 到父请求进入原生队列的中位数分别为 236、252、245 ms。实际隐藏的传输时间由 H2D 启动、确认和首次服务另行测量。[审计脚本](../../paper/icml2027/audit_join_targets.py) · [JOIN 物理恢复](../../beliefkv/runtime/join_physical_recovery.py)

## 2. 两级 KV 压力下的依赖感知准入

**Dependency-Aware Agent Admission under Two-Tier KV Pressure.** 在每个新 root 或 child 第一次 native prefill 前检查两级可用容量。扣除已有承诺和运行中上下文的增长预留，保护仍有用的 Host-only 前缀。多个候选单独放得下、合在一起放不下时，优先推进父任务正通过 CALL 或 ALL-JOIN 等待的 child。资格随当前阻塞关系和实际余量更新。[首次物化与排序](subagent_materialization_pressure_20261116_zh.md)

MARS 让动态 child 继承 root 的准入资格，随后接受引擎分配检查。MORI 按 program 管理两级缓存，按上下文大小排列新到达的候选。论文在首次分配的决策点比较这两种策略与实时父子依赖。[MARS §4.3](https://arxiv.org/html/2604.26963v3) · [MORI §4](https://arxiv.org/html/2606.00866v2)

## 3. 两级容量压力下的分段 KV 迁移

**Capacity-Aware KV Migration in Segments.** 大段回载会触发 HBM 驱逐，随之而来的写回可能挤掉第三个 agent 唯一的 Host 副本。`a7b698e2` 在 55 秒档共同窗口记录了 175,652 个实际执行的 Host 丢失重算 token，其中 125,785 个可追溯到需求 H2D。[同版本逐请求驱逐归因](../../experiments/reports/d3_reclaim_r5_20261118/host200_arrival_rate_80_65_55_r1/mean55_full_d3_a7b698e2/candidate_common_window_causes.json)

- **写回。** 当前缺口由原生申请量减去已腾出的设备槽位得到，并向上取整到页。可直接释放已备份设备页时优先利用已有副本。需要拆开较大设备叶时，只写回叶节点后缀。Host 容量允许时可留出最多按一个原生输入 chunk 计的额外周转空间，降低后续小申请反复写回的开销。
- **回载。** 已准入且会话有效的长前缀，首段要求 Host 命中至少为两个输入 chunk，并检查驻留 decode 等条件。先预留一个输入 chunk 和运行批每个请求一页。常规分段首段至多为 Host 命中的一半，并受扣除预留后的可用量和页对齐限制。锁住 Host 后缀，提交当前连续前缀的 H2D。原生 ACK 后核实身份、进度和容量，再推进剩余段。余量充足时使用整段原生恢复。
- **历史版本。** 按 `git show a7b698e2:beliefkv/runtime/subagent_materialization.py` 和同提交的 `demand_restore_chunk.py`、`demand_restore_capacity.py` 核对旧实现。55 秒档固定 38 条平均 JCT 为 `a7b698e2` 1,670.09 秒、`4482e9e7` 1,681.12 秒。独立消融应固定首次物化、增长预留、父 KV 策略与写回上限，只切换分段机制，测量恢复引发的实际重算及固定组 JCT。[修订版对照](../../experiments/reports/d3_reclaim_r5_20261118/host200_arrival_rate_80_65_55_r1/progress_zh.md#d3-物化检查开销)

### 回载确认后归还 Host 页

55 秒档固定 38 条完整生命周期中，517,296 / 8,344,615 个 prefill token 是 Host 丢失重算。其中 371,762 个同时具有明确的需求回载标签和唯一 Host 副本驱逐记录，涉及 29 个触发请求。25 次可匹配具体回退记录的检查关联到 329,960 个 token。分段限制单次分配规模，但写回仍需要先占用 Host 槽位，普通回载完成后仍保留 Host 镜像。旧的合计余量检查及在途恢复、批次和服务保护回退均可让原生整段回载触发驱逐。逐笔交换的可行性需要当时的 donor、锁和容量证据。[归因脚本](../../experiments/reports/d3_reclaim_r5_20261118/host200_arrival_rate_80_65_55_r1/lifetime_host_loss_agent/audit_lifetime_host_loss.py) · [需求回载实例](../../experiments/reports/d3_reclaim_r5_20261118/host200_arrival_rate_80_65_55_r1/progress_zh.md#需求恢复)

Penelope 使用预留的 Host 缓冲写回 A，B 的 H2D 完成后将 B 的原 Host 页纳入缓冲。当前实现使用两个原生分配池的实际空闲页启动。对已选中的 B，若常规回载需要计入第三方唯一 Host 副本，且真实空闲量足以完成首段并保留运行批余量，则分段推进。每次原生 H2D ACK 发布后，核对请求身份和 radix 范围，对 HBM 副本仍受保护且没有 Host 锁或预取租约的本段页，立即归还原 Host 槽位。下一段写回按当时的实际空闲量重新检查。最后一段筹足容量后直接进入原生回载及 prefill，仍通过原生 ACK 归还 Host 页。[Penelope §3.3](https://arxiv.org/html/2611.00391v1#S3.SS3) · [分段回载](../../beliefkv/runtime/demand_restore_chunk.py) · [容量选择](../../beliefkv/runtime/subagent_materialization.py)

一轮写回 \(a\) 个槽位、回载 \(b\) 个并归还 \(r\) 个时，HBM 空闲量净增 \(a-b\)，Host 净增 \(r-a\)。等量交换要维持两级余量，需满足 \(a=b=r\)。当两边的实际空闲槽位均为零、可迁移 KV 均只有唯一副本时，必须先有页可用才能启动首段。此时系统沿用原有容量处理，仍可能发生重算。

同容量 CPU 对照从 HBM 无空闲、Host 空闲 7 槽开始，使用实际 radix tree、分配器与 ACK 发布逻辑。原路径整段回载 B 的 12 槽，丢弃第三方 12 槽唯一 Host KV。新路径按 4、4、4 槽回载，前两段在 ACK 后归还的 Host 槽位被下一段写回复用，第三方 KV 得以保留。Host 无空闲时也可由可用 HBM 槽启动。后续在固定 38 条测量重算、需求回载等待、D2H 流量、decode 吞吐及 JCT。[原生 CPU 对照](../../tests/test_demand_restore_chunk_scheduler.py)

工具调用剩余时长预测和两秒周期回收属于系统运行配置，按相同配置执行上述消融。[工具等待策略](adaptive_tool_offload_and_timing_20261027_zh.md) · [事件与周期处理](event_tool_replacement_window2s_20261031_zh.md)
