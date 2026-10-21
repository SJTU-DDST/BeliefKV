# 动态多 Agent KV 管理的设计复核与研究方案

日期：2026-10-21，北京时间。

本文回应用户最新的三个设计点和导师意见。此次要求重新开放针对现有主线的
研究论证；旧归档中被撤下的其他研究方向不因此自动恢复。
GPU 预约仍为 2026-10-22 06:00 前禁用。

本文区分已实现机制、历史观测、推导和候选设计。候选尚未获得新颖性或 GPU
性能验收；不把新的术语、组合或评分函数当作已成立的论文贡献。

## 1. 当前判断

目前已有可用系统和有价值的优化问题，但下列三项不能直接作为 OSDI/EuroSys
级别论文的独立新颖性主张：

1. 面向动态 spawn、嵌套 agent 或运行时未知的执行图。
2. 在工具/依赖等待期间卸载状态，随后提前恢复，并优先服务恢复对象。
3. 以 workflow/program JCT 而不是单个请求延迟为调度目标。

最直接的重叠是 TokenCake 的时间、空间和重要度调度，以及 Agentix 的在线
多调用/多线程程序调度。把我们的工作简单描述为上述三类机制的组合，差异不足。
公开 artifact 没有实现论文全部机制，也不能成为论文新颖性的依据。

当前更值得验证的核心问题是：

> 当未来 workflow 结构和运行时长未知，而已观察到的 fork/join 与工具事件会
> 不断改变大块私有上下文的用途时，如何提前准备可安全释放的容量，并在实际
> 恢复边界联合安排数据恢复和执行，减少 workflow JCT，且控制工作足够轻？

这里的候选 insight 是：阻塞期间可以较早执行不改变 HBM 驻留的增量备份；
占用 HBM 的恢复和执行优先权则尽量接近确定的恢复事件再投入。
这种操作代价和信息确定性的区别，是研究假设，不是已证明的新颖性。
仍须击败简单策略和已有方法的忠实适配。

## 2. 与最相关工作的实际重叠

| 工作 | 已覆盖的内容 | 本项目需要验证的差异 |
|---|---|---|
| TokenCake，arXiv:2510.18586v5 | 工具期 offload、预计结束前 upload、reserved/shared 空间管理及重要度调度；还用统一压力快照、pending upload debt 和固定阶段顺序协调迁移与准入 | “时间+空间+优先级”和“统一协调”都不是独有贡献。考察仅用已展开依赖和确定完成事件时，逐 context 的私有 checkpoint、精确可回收容量及准入是否超越按 agent type 分配的策略 |
| Agentix，原 Autellix，NSDI 2026，arXiv:2502.13965 | 动态 agentic 程序、程序级 attained service、在线多线程关键路径跟踪、优先级和抢占；已经关注 JCT | 动态性、最后 child 或程序优先级都不是充分区别。若提出容量交接，必须证明它在同样强的程序调度之上减少迁移/等待，而不只重现程序排序收益 |
| TOPAS，arXiv:2608.25523 | 联合 resident-prefix set 与 admission，包含迁移成本、最长剩余路径和 aging；模型使用已知共同 DAG 等输入 | 候选差异是运行时逐步展开、无需未来共同 DAG，以及变化的私有上下文。不能把已有 joint scheduling 换个名字；静态共同 DAG 子集应按原条件对比 |
| KVFlow，arXiv:2507.07400 | Agent Step Graph、未来使用距离、共享 prefix 驱逐和预取；可以表达分支和同步 | 不能声称它只处理线性流程。需要区分角色共享 prefix 与实际 context 的私有增长，并分开已知图实验和 observed-graph 扩展 |
| PBKV，arXiv:2605.06472 | 融合历史调用图与当前语义预测多步 agent 调用；跨 workflow 汇总复用分数，先回收终态 workflow 的私有缓存，再对活跃缓存评分；预取仅使用空闲 GPU 容量和带宽 | “动态轨迹+私有缓存+生命周期感知”也有直接先例。候选差异是确定恢复事件前对仍活跃但已阻塞的 context 准备安全容量，而不是只在空闲空间预取；同时须击败 TokenCake 等会主动准备容量的方法 |
| Continuum，arXiv:2511.02230；InferCept，ICML 2024，PMLR 235:81-95 | 工具等待期间的状态保留/迁移/重算，工具时长或成本与队列/程序调度联动 | “概率大于 90% 就卸载”本身不足以形成区别；要证明可回收容量和恢复机会的联合决策超越工具 TTL/成本策略 |

以上是机制与输入假设的比较，不是已经完成原系统复现，也不是证明这些工作
不能适配动态 fork/join。尤其不能给强 baseline 提供比我们更弱的事件信息，
再把信息差当作算法收益。

因此，当前尚不存在已经验证的“本质差异”。应特别检验同一 agent role 下
多个私有 context 的大小、恢复边界与可回收状态差异：如果仅将 TokenCake
改为逐 context、接入相同 EOS 和增量 writeback 就能获得相同效果，机制贡献
仍应收缩，不能把它描述为原方法原则上无法解决的问题。

## 3. 三个现有设计点需要修正的地方

### 3.1 EOS 是有用信号，但 P50 170ms 不是恢复保证

应区分以下边界：

- 模型结束一个 response；
- 确认该 response 不再调用工具并结束当前 child；
- 当前 JOIN 的最后一个未完成成员结束；
- parent 客户端提交、服务端进入队列、首次 GPU 服务。

任意 EOS 不等于最后一个 child RETURN；任意 child RETURN 也不等于 JOIN
解锁。预取必须保留实际 JOIN 成员、context epoch、session generation 和终态
检查，不能仅依赖 token ID。

170ms 必须注明原始测量版本、样本、起止边界和分位数，不能直接沿用为当前
workload 常量。当前 EOS-only 配置的协议窗口是 50ms；历史 V109 的在线
EOS-to-arrival 样本计数为 0，其 online P10 字段不能作为该轮实测窗口。
缺少样本应保留为未知，不能解释为窗口为零。

同样，spawn 本身不一定使 parent 阻塞；异步 API 允许 parent 继续工作。
spawn 事件可以触发不破坏驻留的增量备份，但释放执行引用和卸载必须依据
真实 WAIT_JOIN/WAIT_TOOL 或其他已验证的安全点，而不是依据 parent 身份。

此外，不能故意保留客户端低效率来扩大窗口。应在较快的客户端/网络/模板
路径下评估 P10/P50/P90、窗口内实际恢复量，以及仍暴露给执行的依赖等待。

分层 H2D 与计算重叠需要逐层满足依赖，不只检查第一层。对理想的单串行
传输流，令第 i 层传输时间为 t_i、计算时间为 c_i、执行前提前量为 W，
无逐层等待至少需要：

    对每个 i：sum(t_1 ... t_i) <= W + sum(c_1 ... c_(i-1))

因此，该简化模型所需的提前量为：

    W_required = max_i max(0, sum(t_1 ... t_i) - sum(c_1 ... c_(i-1)))

这只是解释性推导，未包含真实排队、共享资源、混合状态复制和其他请求。
真实策略应使用实际依赖与少量在线成本统计，不求一个昂贵的在线 oracle。
耗时估计用于选择提前恢复的范围，不应成为 EOS 正机会的硬拒绝门槛。
例如两层传输分别 2/8ms、第一层计算 1ms、提前量 2ms 时，首层已经就绪，
第二层仍会暴露 7ms 等待。这是解释性例子，不是当前模型的测量值。

当前冻结引擎的 `copy_mamba_state` 会在 prefill 前的状态复制路径等待
最后一个 Mamba 层的 transfer counter。不能把纯 attention 的首层流水线
推论不加核查地套用到该混合模型。

还须分清：

- 分层复制可以减少“何时能读取数据”的等待；
- 该原生 FULL token allocator 的槽位对应跨层存储；只提前复制部分层，
  不自动意味着只需相同比例的 HBM 容量。

### 3.2 当前备份已经增量，但“备份所有未备份页”仍是全覆盖策略

导师对“全量备份”表述的担心有合理依据，但应通过真实策略改进回应。
当前 FULL 只补缺失副本，不是每 200ms 重传全部历史；同时覆盖策略仍较宽。
“增量”和“选择性”是两个不同维度，不能只更换措辞。

实现上，当前是在 scheduler 的 post-forward 安全点按周期检查 dirty index，
然后提交 native 异步 D2H；不是专用 Python 后台线程独立扫描整个 HBM。
异步 DMA 也不等于 CPU 分发、Host 分配、HBM 带宽、PCIe/NUMA 和 Host
容量没有成本。

更关键的是：Host 有副本只是回收的一个条件。页面还可能存在执行引用、
共享 owner、锁或在途 DMA，因此“备份完成”不等于“现在可立即驱逐”。

V109 的离线观测提供一个具体提醒：

- idle 备份 5583 批，约 167.04GB FULL，平均约 29.92MB/批；
- 该轮没有触及 512MiB 字节上限或 32 节点上限；
- 冷容量诊断中 session_referenced 为 443 次，unbacked 为 21 次；
- 这些是可重复的拒绝观测，不是唯一页数，也不涵盖全 HBM。

所以，继续增大批次或备份范围未必解决剩余容量问题。
应维护“具有有效 Host 副本且当前满足安全回收条件的容量”，并说明共享页
在所有有关 owner 同意前为何不能作为 donor。

FULL 的不可变已发布范围可以增量备份。正在演化的 Mamba 状态不能直接套用
同样的 dirty-prefix 规则；仅对有效暂停 checkpoint 使用状态副本。
论文的主机制不能依赖 Mamba 才成立。

### 3.3 工具预测准确率需要转换为决策收益

当前代码主要在已经进入 WAIT_TOOL 后判断剩余等待是否足够长，并在真实
压力下准备/回收。它不是在工具尚未发生前预测模型下一步会调用长工具。
存在关键 parent 容量需求时，当前也允许使用其他已经 WAIT_TOOL 的安全 donor，
不要求其先满足同一个长等待门槛。

“准确率 97%”必须补充：

- 是整体 accuracy、被选中卸载的 precision，还是长工具 recall；
- 按总工具时长还是条件剩余时长标注；
- 无支持、删失、多工具并行、工具超时如何处理；
- 按 episode、实际安全驱逐、字节及释放的 HBM 时间如何计量；
- 在当前任务集上的校准和 coverage，不能直接继承旧 workload 数字。

不能只用固定 1s/90%：相同等待时间下，已经有副本的小 checkpoint 与需要
大块 D2H/H2D 的 context，成本不同；没有竞争需求时，释放容量也未必有收益。
概率模型应成为容量决策的输入，而不是脱离大小、带宽和压力的开关。

历史 V109 记录有 163 次工具等待压力回收，以及 3 条显式 tool PREPARE。
大量备份走 idle/spawn 路径，不能把所有工具期释放都归功于概率预测，
也不能把 3 条显式 PREPARE 误读为只有 3 次工具期管理。

### 3.4 提高优先级不能保证 1.5s 内消费

当前已记录的工具预取 ACK-to-first-service P50 约 1.78s、P90 约 4.04s。
这是命令人口，不是独立工具轮次分布；但已足以说明固定 1.5s 的消费保证
没有得到该轮证据支持。

普通排序无法绕过没有执行槽、必要 checkpoint 未就绪、容量不足或已有长
decode 请求。要声称有界执行，需要相应的准入/抢占模型和真正的时间界；
当前应表述为有界优先机会或目标，不能把 TTL 当成保证。

优先执行某些 workflow 可以降低平均 JCT，却不必提高相同工作量的 decode
吞吐；也可能增加其他 workflow 的等待。若改变 batch 组成、迁移频率或
无效重算，吞吐才可能随之改善。二者需要分别归因，不能从 JCT 改善直接推出。

JOIN parent 未必接近 workflow 结束：它可能再 spawn、继续工具调查或产生长
输出。HBM 命中的请求也不必然使根 workflow 更快完成。局部性适合作为同级
请求之间的成本项，不能默认压过其他 workflow 的完成与老化。

当前排定的 Full 已显式采用 simple JOIN 排序、关闭额外 ACK 后保护，没有
全面启用“所有工具预取后额外插队”和 resident-first。旧设计叙述应与这一
配置区分。

## 4. 收敛后的三个候选机制

### 4.1 根据可释放容量缺口选择增量备份

目标从“把所有未备份页传到 Host”改为“为近期恢复准备足够的安全 donor”。
不是预留一块长期闲置的 HBM，也不按所有等待 parent 的总大小长期占空间。

为下一小批候选恢复，分池核算：

    capacity_gap =
        max(0, missing_restore_capacity + next_input/decode_reserve
               - current_free_capacity - independently_reclaimable_backed_capacity)

这不是把两份引用同一页的 agent 字节相加，也不是把仍运行 child 的预计释放
计入 actual_free。FULL 和混合状态槽位分别满足原生 allocator 约束。

备份优先级采用直接可解释的事件：

1. 已阻塞在 JOIN、用途确定的 parent 私有缺失页，保持当前 spawn 增量备份。
2. 容量缺口出现时，刚进入工具等待且可安全回收的 donor。
3. 仍缺可回收容量时，低优先级补其他具有近期回收可能的不可变 dirty 页。

没有缺口且已有足够的可释放容量时降低备份强度；Host 接近满池时保留高价值
副本，避免后台备份挤掉近期真正需要的状态。
200ms 只作为已有周期后备，事件维护索引；不能新加每步全树评分或全图搜索。

要报告 dirty bytes、eligible dirty bytes、clean donor bytes、压力缺口、
备份后实际释放/恢复关联和 foreground 干扰，而不只报告 D2H 总字节。

### 4.2 在可靠恢复事件上，恢复真正暴露的依赖

保留 EOS-only 的默认路径；限制为实际 JOIN 状态允许的 child 完成信号。
工具返回时机不确定，继续保留条件生存模型，但按成本和容量使用它。

在线决策使用已验证的缺失 checkpoint 和少量层/状态成本：

- 原生恢复仍是正确性和未完成恢复的执行路径；
- native 已能隐藏的后续层不必一律提前搬完；
- pre-forward 状态依赖或后续传输赶不上计算时，允许提前更多；
- 无可靠模型时，允许安全且有容量的正机会开始，不新加估计器拒绝门槛；
- 不等待额外完整 ACK 才允许原生依赖已经满足的执行；
- 不因为发生 EOS 就提前驱逐仍有活跃引用的 child。

收益按减少的暴露恢复等待和服务启动等待验收。搬运更多或复用率更高均不能
单独证明加速。

### 4.3 按一次实际阻塞—恢复过程协调容量与有界执行

调度单位从“某次预取命令的 TTL”转为“当前已观察到的 continuation”：
记录其依赖、checkpoint、分池容量缺口、实际回收及后继请求身份。
同一事件记录贯穿备份、释放、恢复和首次执行，减少多个控制器重复规划。

记录键至少包括 root、invocation、context epoch 与 JOIN generation。
事件索引维护缺失页、Host 副本有效性、独立可回收容量和实际前驱完成；
epoch/成员变化时失效。它是执行所需的状态，不应发展成逐 token 写详细
日志的新热点。只在 spawn/block/terminal/EOS/admission/ACK 等相关事件更新，
周期检查仅处理脏对象和到期项。

保留已测得有效的简单 JOIN parent 优先作为基础。进一步候选仅包括：

- 一个 JOIN 只剩一个未完成 child 时，将 parent 当前的有界调度机会传递给
  该 child；不推断整个未来 DAG，也不把所有 child 各当作一个高权重 workflow。
- child 确实终态后，回收其无活跃引用的私有页；共享页按 owner/refcount
  处理。实际释放的容量优先承接当前可执行 parent，缺口仍由安全 donor 补齐。
- parent 请求已经提交且必要数据依赖满足时，尽快获得一次真实准入机会；
  不能让尚未消费的恢复记录挤占其他本来可执行的工作。
- 后续可比较一个有服务预算的继续执行阶段：在再次阻塞、终止或用完 GPU
  服务预算时让出机会。它不是整个 workflow 一直占 GPU，也不是固定 1.5s 锁。
- root-level aging/公平预算约束所有后代；局部性只在合适的同级候选间使用。
- 保持 work-conserving 和 batch 填充；没有可执行 parent 时继续服务其他请求，
  不能为了预计恢复对象闲置 GPU。

例如，一个待恢复 parent 缺少 3GiB，当前 child 结束后能安全释放 1GiB，
另一个工具等待 agent 具有 2GiB 可回收私有页。若后者尚无 Host 副本，可在
等待期间准备它的不可变增量；child 真正终态时按实际释放量补足容量，随后
恢复和准入 parent。这里的候选收益是提前准备缺口、减少临界点 D2H 和反复
换入换出。它不是要求两个 agent 使用相同物理页，也不是声称任何固定时间
内必能执行；如果 Host 副本与原生 LRU 本已完成这些工作，候选就没有额外收益。

root 公平预算、最后 child 优先和一般联合准入分别已有强先例，单独都不能
包装成新贡献。机制 3 只有在同样强的程序调度上，通过实际容量/状态交接
获得额外 JCT 或吞吐收益，才比一个更复杂的优先级函数更有研究价值。

上述后续阶段尚未作为当前默认实现。最后 child/普通恢复排序的旧版本也不能
直接称为这套新机制已经验证。

对任意相同到达集合，sum(JCT) 等于 unfinished-root 数量随时间的积分。
因此论文应检查是否更早完成根 workflow，而不只是加速某个 parent 请求。
上述在线策略不掌握完整剩余工作，不能据此宣称全局 JCT 最优。

## 5. 能够否定这条研究主张的实验

第一组仍完成已经排定的同配置 Full/JOIN-only/native 对照，建立共同的排序
基础。然后按实际瓶颈加消融，不先叠加全部候选：

| 问题 | 必要对照与证据 |
|---|---|
| 是否仅是 JOIN 优先 | Full 与同排序、同前端的 JOIN-only；普通 FIFO、老化与 native admission 相同 |
| 容量准备是否有增量 | JOIN-only 加选择性增量备份/生命周期管理，与加入 EOS 恢复前后对比；测 clean donor 缺口和恢复阻塞 |
| 是否只是更多备份 | 当前广覆盖增量策略 vs 缺口驱动策略；D2H/Host 占用、未备份拒绝、重算和吞吐同时报告 |
| 是否已被程序调度解释 | Agentix 或其忠实同底座适配；root 共享公平预算的简单版本单列 |
| 是否已被时间/空间联合管理解释 | TokenCake 论文机制；public artifact 与补全预测功能的版本分列，不能只打缺机制的仓库 |
| 是否已被动态状态缓存解释 | PBKV 的多步预测、retired-first 回收及保守预取；保留预测器与同样的动态事件输入，区分预测收益与容量准备/准入收益 |
| 是否只是工具 TTL | Continuum/InferCept；估计器、费用模型和预测输入按对应方法保留 |
| 层流水线是否已足够 | native 分层恢复与 EOS 恢复比较，分层等待和 pre-forward 状态等待分开；加入纯 attention 模型 |

还需要：

- 提供同事件信息的强组合对照，例如 Agentix 加 EOS/增量 writeback，以及
  TokenCake-observed/逐 context 适配；明确它们是扩展，不冒充原论文实现。
- 在同一因果请求/工具 trace 上重放固定工作量，用于隔离系统策略；
  root 外部到达固定，后继到达仍由前驱完成与工具等待决定，不泄露未来信息。
- 真实动态执行单列完成质量、输出 token、工具/委派数量、guard 和失败，
  不把 replay 当作真实端到端质量证据。
- 多种容量/长上下文/工具分布，包含较轻压力、较快客户端和较短窗口；
  不能只展示人为增加 spawn 的压力配置。
- 同配置比较用于隔离机制；论文还应让 baseline 合理调优，报告吞吐/JCT
  权衡，避免只选择一个使 native 不利的 batch 或并发上限。
- 区分自然委派 workload 与 prompt 引导的受控 fork/join 压力 workload。
  动态决策在结构约束内发生，不等于委派频率代表自然分布。

如果简单 JOIN 加常规增量 writeback 已解释主要收益，或 native 暴露恢复等待
太少以至 KV 控制不能稳定获益，就应承认当前核心机制不足，不通过提高迁移
字节、增大工具延迟或添加复杂优先级来制造论文贡献。

## 6. 当前事实和实施边界

目前最新完整 Full 为 V109；最新完成的内部控制为 V111。
V2610 的共同去长尾窗口为 600–2520.937s：

| 指标 | Full | JOIN-only |
|---|---:|---:|
| decode token/s | 580.52 | 592.37 |
| 共同截止受限平均 JCT，秒 | 1830.28 | 1704.12 |
| 全程输出 token | 1736639 | 1556460 |

版本和真实工作量不同，这不是同轨迹因果结论，但它是目前 Full 尚未证明
增量收益的重要负证据，不能在论文中跳过这个简单 baseline。

本轮只形成设计复核与实施顺序，不将候选写进默认 runtime，不改变 workload、
prompt、工具上限或种子。维持 CPU-qualified simple JOIN、EOS、增量 PREPARE
及关闭额外保护的基本对照；新的调度/选择性备份机制待按上述证据实施和消融。

GPU 时限没有改变。文档提交后应更新冻结登记，以免旧登记因 main 前进而
拒绝启动；CPU/runtime/engine 源码若无变化，可核对原资格映射，不宣称重跑测试。

## 7. 来源

外部原始来源于 2026-10-21 重新检索和阅读：

- S1：Agentix: An Efficient Serving Engine for LLM Agents as General Programs，
  NSDI 2026，USENIX 作者页面及论文；原 Autellix，arXiv:2502.13965。
- S2：TokenCake: A KV-Cache-centric Serving Framework for LLM-based Multi-Agent Applications，
  arXiv:2510.18586v5，作者公开仓库 pku-lemonade/TokenCake。
- S3：TOPAS: Workflow-Aware Prefix-State Scheduling for Multi-Agent LLM Serving，
  arXiv:2608.25523v1。
- S4：KVFlow: Efficient Prefix Caching for Accelerating LLM-Based Multi-Agent Workflows，
  arXiv:2507.07400，作者仓库 PanZaifeng/KVFlow。
- S5：Continuum: Efficient and Robust Multi-Turn LLM Agent Scheduling with KV Cache
  Time-to-Live，arXiv:2511.02230v7。
- S6：InferCept: Efficient Intercept Support for Augmented Large Language Model Inference，
  ICML 2024，PMLR 235:81-95，作者 Abhyankar 等。
- S7：SGLang 官方 Hierarchical Cache 文档，章节介绍 layer-wise loading overlap。
- S8：Efficient Serving for Dynamic Agent Workflows with Prediction-based KV-Cache Management，
  arXiv:2605.06472v1，作者原始论文。

本地实现与证据：

- `beliefkv/runtime/sglang_v0520_runtime.py`：
  `_long_tool_wait`、`dispatch_idle_prepare`、`dispatch_spawn_prepare`、
  `_observe_join_eos_arrival`、`plan_native_prefill`。
- 冻结引擎树 `53335e6` 的 `memory_pool.py::copy_mamba_state`。
- `configs/migration/next_predictive_baselines_20261022.json`。
- `experiments/reports/v2610_v111_latest_four_arm_comparison_20261021.json`。
- V109 Full 的 `prefetch_lifecycle.json`、`prepare_background.json` 和最终
  `admission_runtime_state`。字节采用原始十进制 GB，未按 agent 逻辑大小推断
  唯一物理迁移量；命令与独立 JOIN/tool episode 口径保持区分。
