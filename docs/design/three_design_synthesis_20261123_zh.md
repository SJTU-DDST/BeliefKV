# MigraGate 的三个设计点

更新于 2026-11-25。MigraGate 在 spawn 和 join 之间准备与恢复父 KV，在工具等待期间为就绪请求腾出 HBM，并在多个子任务同时到达时协调新 KV 的物化和已有 KV 的恢复。

主评测在单张 H200、SGLang 和固定 200 GB Host KV 上，以泊松到达回放 133 条原项目
轨迹，固定模型 token 和父子依赖。调度器在线读取已发生的生命周期事件。论文统一报告
固定 38 条工作流从到达到完成的 JCT、实际重算和共同窗口吞吐。
[论文实验执行手册](../implementation_plan.md#paper-evaluation-runbook)

## 1. 跨 spawn 与 join 的父 KV 备份与恢复

父 agent 在 `SPAWN` 后可能继续推理，到 `WAIT_JOIN` 才阻塞。等到就绪请求需要 HBM
才备份，会让它等待 D2H。等到父请求提交才回载，则可能让父 agent 在依赖满足后
继续等待 H2D。

- **KVTether** 用动态 fork 与消息生命周期判断各级缓存保留哪些 KV。**ConServe** 在推理
  步之间异步准备可能被抢占的离线任务的副本。父子委派事件还可以提供更具体的备份时机。
  [KVTether §2.4、§6–§8](https://arxiv.org/html/2609.39819v1) ·
  [ConServe](https://proceedings.mlr.press/v306/qiao26i.html)
- **Nalar** 支持在线展开的依赖，并从组件历史平均耗时估计依赖就绪时间，提前恢复 KV。
  估早会延长父 KV 的 HBM 驻留，估晚会留下父请求的传输等待。**KVFlow** 按已声明
  步骤图推测下一步并预取前缀，高并发时传输可能赶不上后继。
  [Nalar §5.2、§6.2.1、附录 A](https://arxiv.org/html/2601.05109v2) ·
  [KVFlow §3–§4](https://arxiv.org/html/2507.07400v1)
- **SGLang RFC #27574** 也提出在 child 关闭阶段预取父 KV。其跨 worker、共享 L3
  原型实现有界 Pin，主动 Prefetch 和 Demote 列在路线图中。
  [SGLang RFC #27574](https://github.com/sgl-project/sglang/issues/27574)

1. MigraGate 在已验证的 `SPAWN` 或 `WAIT_JOIN` 事件上用 `PREPARE_HOST` 异步补写稳定
   前缀中缺少有效 Host 副本的页。D2H ACK 确认副本后，父 agent 仍可使用设备侧 KV。
2. 就绪请求确实缺 HBM 时，回收路径复核父 agent 的阻塞状态、页归属、引用、锁和在途传输，
   再释放已有确认副本的可回收设备页。
3. 最后一个未返回 child 的有效 final EOS 到达后，MigraGate 在容量允许时从同一份 Host 副本
   回载父上下文的缺页，让 H2D 与 child 结果交付和父请求提交重叠。父请求在真实 JOIN
   满足且通过原生检查后执行。

分别比较事件触发备份与按需备份、final EOS 与 JOIN 确认、JOIN 确认与父请求提交，
测量实际暴露的 D2H/H2D 等待及首次 GPU 服务。
[备份与回载运行时](../../beliefkv/runtime/sglang_v0520_runtime.py) ·
[JOIN 恢复](../../beliefkv/runtime/join_physical_recovery.py) ·
[机制消融](../implementation_plan.md#待补的机制实验)

## 2. 基于工具剩余等待预测的按需 KV 卸载

工具阻塞期间，agent 保留的 KV 会与就绪请求竞争 HBM。短调用很快就需要原来的 KV，
卸载会增加回载开销。长调用有机会腾出空间给别的请求，而且等待窗口可能足以覆盖迁移时间。

- **ThunderAgent** 的 *acting decay* 按已等待时长降低 program 的逻辑保留权重，物理
  回收由引擎完成。已等待同样久的工具调用仍可能有不同的返回风险。**Continuum**
  根据工具间隔分布和恢复代价选择保留 TTL。
  [ThunderAgent §4.3、附录 A.4 与 G.2](https://arxiv.org/html/2602.13692v3) ·
  [Continuum](https://arxiv.org/abs/2511.02230)
- **TokenCake** 将预计等待、迁移成本和受益请求纳入卸载判断。其公开实现复用 Host 副本并
  补写缺页，后继请求到达时按需恢复。**Nalar** 也比较预计空闲时间与往返迁移时间。
  等待时间的点估计会掩盖工具提前返回的风险。**TripleKV** 用工具返回预测提前将 KV
  从 SSD 传至 Host，Host 到 HBM 由请求按需触发。
  [TokenCake §4、§6.3、§7.5](https://arxiv.org/html/2510.18586v5) ·
  [TokenCake 公开实现](https://github.com/pku-lemonade/TokenCake) ·
  [Nalar 附录 A](https://arxiv.org/html/2601.05109v2) ·
  [TripleKV §4、§6](https://arxiv.org/html/2611.25112v1)

MigraGate 在同一调度流程中保留预测提前让位和周期回收两个入口，复用增量备份及原生页释放检查。

- **预测提前让位。** 当就绪请求 B 确实缺 HBM 时，结合离线先验、本轮已完成调用和本次
  已等待时长，估计工具阻塞的 A 剩余等待能覆盖这次迁移成本的概率。成本包含尚缺的 D2H、
  B 所需的 H2D、A 的回载和控制余量。满足概率门槛且能形成可执行的 A→B 配对时，在
  原生准入边界提前尝试。若还需备份，则在 ACK 后根据当时的工具状态、就绪请求和容量
  缺口重新判断。只有实际释放的页才供新请求使用。
- **周期回收。** MigraGate 的工作集检查采用 2 秒周期，沿用 ThunderAgent 的 *acting decay*
  思路，按已等待时长调整工具阻塞 agent 的逻辑权重，协调届时仍存在的压力。预测证据不足、
  当前配对不成立或备份未完成时，周期控制仍会继续。物理释放由引擎依据当前容量和页状态执行。

同一原生规划边界先尝试预测替换，再执行到期的工作集检查。若周期回收先满足 B，
A 的 ACK 到达后会重新检查真实缺口和有效候选。消融对比同一套 2 秒周期控制在启用
和关闭预测提前让位时的差异，固定 `PREPARE_HOST`、JOIN 恢复和写回上限。将实际
页释放与 B 的首次 GPU 服务关联，并记录 A 的回载、重复搬运和控制 CPU。
[事件与周期的交接](event_tool_replacement_window2s_20261031_zh.md) ·
[迁移成本和条件概率](adaptive_tool_offload_and_timing_20261027_zh.md) ·
[交接测试](../../tests/test_reference_tool_replacement.py)

## 3. 两级 KV 预算与依赖感知的 agent 准入

已入场的 workflow 仍会分多轮创建 child。新 root 或 child 的首次 prefill 会消耗 HBM，
引发的写回也可能挤掉另一个在途 agent 唯一的 Host 副本，使它恢复时重算旧前缀。
按需回载同样会造成这类挤压。[驱逐归因](../../experiments/reports/d3_reclaim_r5_20261118/host200_arrival_rate_80_65_55_r1/progress_zh.md#当前-55-秒驱逐归因)

- **MARS** 支持动态 child，在单卡 vLLM 上按 HBM 工作集预算控制 root 入场。动态 child
  继承资格，再由引擎通过分块 prefill 和回收应对压力。两级缓存中的 Host-only KV
  还需要独立保护。
  [MARS §4.3、§5.2、§7.2、§7.6](https://arxiv.org/html/2604.26963v3)
- **MORI** 在 SGLang HiCache 上管理 HBM 和 Host 两级驻留。它在 AgentX 闭环回放中把
  child 作为独立 program 准入。论文报告因 Host 副本丢失而重算的可复用前缀比例中位数为
  1.3%。新 program 按上下文大小排序。短而冷的 child 仍可能需要大量首次 prefill，
  正阻塞父任务的 child 也参与这套长度排序。超出驻留预算而进入 Waiting 的旧 program
  可能失去 KV，并在恢复时重算。
  [MORI §4.3、§5、§6](https://arxiv.org/html/2606.00866v2)
- **TokenCake** 用预声明 DAG 和在线反馈为关键 agent 类型划分 HBM 份额。同一类型的实例
  共享配额，即使各自父任务的阻塞状态不同。**EfficientAgent** 按 Host 容量筛选重填写回，
  优先延伸仍有副本的前缀。动态实例何时可以开始首次 prefill 仍需单独决策。
  [TokenCake §3.2、§5、§9](https://arxiv.org/html/2510.18586v5) ·
  [EfficientAgent §4、§5.5](https://arxiv.org/html/2609.33762v1)

MigraGate 在每个新 root 和 child 首次 native prefill 前重新核算 HBM 与 Host 的有效余量，
保护仍有用的 Host-only KV，计入可安全回收的副本，扣除在途承诺和增长预留。压力下，
从放得下的候选中优先推进实际阻塞 CALL 或 ALL-JOIN 的 child。恢复已有 KV 时也检查
空间压力，按实际 HBM 缺口限制写回，并在容量紧张时分段加载长前缀。
[D3 实现](subagent_materialization_pressure_20261116_zh.md)

55 秒档单独的配对实验中，首次物化门控和配套迁移保护使固定 38 条的平均 JCT 从
2,752.02 秒降至 1,757.79 秒。保持迁移保护一致的 root-only 对照用于检验额外 child
门控的收益。[D3 配对结果](../../experiments/reports/d3_reclaim_r5_20261118/host200_arrival_rate_80_65_55_r1/progress_zh.md#55-秒-d3-配对收尾)
