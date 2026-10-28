# Reference FULL 的 ALL-JOIN 共同准入

本改动补齐 reference-core FULL 的一个既有系统要求：在实际阻塞的
ALL-JOIN 中，允许已提交、ready 的 child 使用空闲逻辑预算，并在资源
允许时共同获得实际 native 服务。2026-10-28 已增加可容纳子集、
共同选批及容量释放后的有界重试。它不改变 ThunderAgent paper arm，
不将上述整合作为独立的新调度算法贡献。

最新待验证配置为
`configs/migration/predictive_sibling_service_capacity_20261028.json`。
保留原工作量、V311 工具时间、容量和种子；新原生接口使用单独的精确树。
旧 `predictive_reference_join_siblings_20261027.json` 及其冻结实验保持原样。
parent 的 free-JOIN 授权仍使用独立开关。

## 2026-10-28 实现更新

`join_sibling_service.py` 承接实际选批和服务观测，
`ReferenceThunderAdmission` 仍继承原 reference 的 tick、完成及压力控制。

- 逻辑容量不足以容纳所有 inactive ready 成员时，按实际请求到达时间选择
  放得下的子集。跳过过大的成员，不借用 ready peer 的预算。
  每名获资格者仍按完整上下文加原有 100-token buffer 计费。
- 实际 prefill 中一次偏好一个已观察 ALL-JOIN，最多四名成员。
  原先选出的队首及组前其他请求保持在前；同组可容纳成员移到 anchor 之后。
  检查当前槽位、已有选入请求、输入/chunk 限制、缺失 KV、实际空闲页及增长
  预留。既有 JOIN parent、已老化请求和驻留优先仍受保护。
- 如果最少两页输入、槽位或空闲页已经不足，先快速返回，不读取组/前缀。
  其他检查最多观察队列前 64 项、组前 8 项及四个组成员；
  不等待未提交或工具执行中的 sibling。
- 退休、真实请求完成释放资格以及 reference 控制轮次变化会标记等待组。
  每个 prefill 最多重试八组，一次释放通知只完整轮转一次，不逐 decode
  token 重扫失败组。native requeue 清理旧 attempt 的服务身份。
- 资格授权、native 准入和实际 forward 返回分别记账。
  `agent_join_siblings_native_service` 只在已准入成员的首个 native forward
  返回后记录，含完整 key、请求 ID、入队至选批和入队至 forward 返回两种
  区间。二者均不冒充 kernel 开始时间。plan opportunity 计数含重复计划，
  不能直接用作独立共同服务比例的分母；仍须审计完整 native trace。

没有添加新的全局老化、普通请求恢复绑定、额外抢占或工具预取。
单个可执行成员继续沿原生路径推进；物理容量不足时原生检查仍可拒绝。
新逻辑尚无 GPU 性能证据。共享 CPU、局部成本及保留的开发失败位于
`experiments/reports/sibling_service_capacity_20261028/`。

### 当前 CPU 资格

最终单次共享回归通过 3580 项及四个子测试，无失败、错误或跳过，
保留前次 3524 项全部身份，新增 56 项。原生相关检查另有 92 项通过。
`qualified_shared_cpu_r2.*` 绑定当前应用源码与独立 native 精确树；
`final_cpu_review.json` 独立核对全部源哈希、旧覆盖保留、三个实际构造器、
工作/时长/容量/seed 环境不变及开发失败留存。没有重跑 native 或 ThunderAgent。

局部成本 `control_cost_cpu_r4.json` 使用三个 program、两个 sibling、
512-token 的真实 native CPU 树/分配器及 copy/event 替身。每种情况预热
50 次、测量 500 次：

| 路径 | thread CPU P50 / us | P99 / us |
| --- | ---: | ---: |
| 关闭共同选批 | 13.0385 | 15.088 |
| 两名 sibling 可共同选批 | 88.143 | 95.949 |
| 输入预算只够一个请求，快速返回 | 15.8435 | 18.262 |

这只是同一入口的小夹具成本，不含异步 writer、DMA、GPU、最大候选窗口
或整控制器。成功选批仍有约 75us 的局部增量，须在完整实验检查是否值得；
不能据此声称已经改善 JCT。早期未做快速拒绝时的输入不足路径约 89us，
相应报告和 profiler 结果均保留，后续采用严格源绑定的 R4。

多 donor 原生夹具验证两个 32-token 的已有备份 donor 合计释放 64 个真实
槽位，再恢复 68-token 消费者并完成 ACK；逐层预留路径则始终保持 free
不变。错误的整组拒绝断言、缺少夹具事件/接口及 NumPy 时间戳未按原生
接口规范化造成的开发失败均留档，没有通过放宽 native 检查解决。

## 共同准入规则

在请求注册、JOIN 事件及有界释放重试中检查资格；实际偏好在 native
prefill 选批入口实施。不新增 decode 全局扫描或定时器。
每组仍限制 2--24 个已观察成员，超过此界限沿原有 reference 路径推进。

- parent 必须确实处于该 JOIN 的 WAIT_JOIN，属于同一 workflow，仍是
  未释放的 waiter，没有独立工具或 foreground child 阻塞。
- 只考虑当前已提交、pending、inactive、未标记暂停的 ready child。
  request、visible key、workflow、context 和 epoch 必须仍一致。
- 当前符合条件的 inactive child 可以选择可容纳子集；每个被选成员必须
  完整放得下。已经 active 的成员保持原有资格，不为了凑组暂停其他 agent。
- 使用完整、未衰减的上下文加每个 program 原有 100-token buffer；
  不使用 ACTING 折扣容量、未来输出长度或实际物理共享的未经证明折扣。
- 没有提交请求的 sibling 不阻挡 ready 成员，不预留未知上下文，不等待
  全员到齐。ANY、推断 JOIN、后台 SPAWN、已经满足/释放的 JOIN 不授权。
- 不暂停其他 ready agent，不借出/召回预算，不增加恢复 TTL。
  获得资格后仍遵循原 reference 的增长、压力、完成和退休规则。

逻辑共同准入不保证同一个 GPU batch 开始，也不保证两个 child 同时结束。
真实 native 物理容量、Host checkpoint、锁和 DMA 仍决定实际恢复/准入。
CPU 原生组件检查专门验证逻辑允许两个 child、物理仅够恢复一个时，
第二个恢复仍被拒绝，Host 副本和 ACK 所有权保持正确。

## 实现前审计与改进目标（保留）

以下记录本轮修改前的状态；当前实现及剩余边界见前文。
实现前共同准入只补齐逻辑执行资格，尚未与实际共同服务形成完整协同。
这是调度覆盖和性能上的缺口，不是已经证明的 KV 正确性错误；
当时以下改进尚未实施；现在仍没有新的性能收益证据。

1. **缺少 sibling 共同选批。**`ReferenceThunderAdmission._coadmit_join()`
   授予 active 资格后，`order_ready()`、`selected()` 和 `admitted()`
   没有 sibling 成组调度逻辑。多个成员即使都已获资格，仍可能分别排队，
   不能据此保证同批开始或在后续多轮请求中及时共同推进。
2. **额外授权要求当前合格成员整体可容纳。**一次检查收集的所有 inactive、
   ready 成员只要合计预算放不下，本次额外授权就全部放弃；已经 active
   的成员保持不变。例如一个成员正在执行、另三个待准入，而容量只能
   再容纳一个时，当前路径不会选择其中一个来扩大共同服务宽度。
   这项保守规则应评估为按实际资源选择可容纳子集，而非强制全组等待。
3. **容量释放后的组级重试不完整。**共同准入检查主要由请求注册、
   本组 JOIN 创建/等待和成员终态触发。无关 workflow 释放预算或其他
   成员完成一次模型调用后，未必立即重新检查之前因容量不足而等待的组；
   普通周期恢复和 continuation 授权不等于重新执行本组共同准入决策。

对于实际 ALL-JOIN，给定一次执行安排，解锁时刻为最后一个成员的完成
时刻，即 `T_JOIN = max(T_child_1, ..., T_child_n)`。若其他成员的完成
时间不变，只提前完成非关键成员不会直接提前 JOIN；但提前启动工具、
释放 KV 或请求槽位仍可能间接改善其他分支，不能将其一概视为无用工作。
本设计的目标是减少拖住 JOIN 的分支等待，并让真实外部等待尽早重叠。

后续实现方向：

- 在 native 选批中，对同一实际阻塞 ALL-JOIN 的已提交 ready sibling
  提供有界共同服务偏好；按当前请求槽位、prefill/chunk 输入预算、
  缺失 KV 和增长空间决定宽度，可以为两个或更多成员。
- 当前合格组整体放不下时，允许选择可容纳的子集；首版采用 ready
  等待时间、实际恢复成本等在线信息，不用未来 trace 或把上下文长度
  当成剩余工作量。新选择规则须单独验证和比较。
- 有真实资格或容量释放时，对有界的等待组重新尝试；区分逻辑额度、
  实际 HBM 和 Host/DMA 就绪，不引入逐 decode 步的全局扫描。
- 不等待尚未提交、工具执行中或无法恢复的 sibling；只有一个可执行
  成员时继续执行，不强制同批开始、持续占槽或同时结束。保留跨 workflow
  老化与服务机会，不能让 fanout 大的组获得无限优先或引发额外 KV 抖动。

验收至少分别统计：两个及以上成员同时 ready 的机会数、这些机会中的
共同 native 服务比例及 ready 排队时间、最后完成 child 的 ready 等待、
JOIN 解锁到 parent 首次服务，以及完整 workflow JCT、全程吞吐、额外
迁移和控制成本。以实际同组 ready 成员为条件，不能用新增授权人数
推算总并行度，也不能把非关键 child 的加速直接计为 JOIN/JCT 收益。

JointFullR1 的 95 次 sibling 授权中，93 次新增一个成员、两次新增两个，
全部 97 个新增请求已验证后续服务，两次双成员授权的首次 native 服务
入口时间差均为零。这证明授权被服务消费，尚不能证明完整组的持续共同
推进或独立 JCT 改善；其他事件可能已有 sibling 正在执行、等待工具或
已经完成，不能解释为全轮只有两次并行。完整证据见
[`layer_overlap_regression_and_siblings_20261028_zh.md`](../research/layer_overlap_regression_and_siblings_20261028_zh.md)。
另行开发的 JOIN parent 阻塞额度交接不等于上述 sibling 选批和子集准入
已经完成。本次仅记录缺陷，未修改运行策略或安排新实验。

## 授权前的增长记账修复

实现过程中发现 free-JOIN 和新 sibling 授权共用的额外接纳路径可能使用
上一个参考控制轮次的 program 大小，漏掉正在生成的 token：

1. 三成员夹具的总容量 1202 tokens；第一个 active child 最后记录 302，
   两个 inactive child 各需要 400。第一个又生成 18 tokens 后，真实
   剩余 782，旧额外授权仍把两个都接纳。
2. parent 恢复夹具中，一个小 owner 退休后缓存剩余 304，parent 需要
   302。另一个活跃请求又生成 18 tokens 后真实剩余 284，旧路径仍授权。

两个失败报告分别保存在 `streaming_budget_before_fix.xml` 和
`parent_streaming_budget_before_fix.xml`。冻结 3a23fe10 的 GPU 实验仍使用
旧路径，不能宣称本修复已经在该实验中运行。

当前额外授权在容量检查前调用继承的 `_refresh_streaming_progress()`，
从当前 visible native 请求的实际 input/output 长度更新参考预算。
没有每 token 扫描，没有更改 reference tick、完成或原有直接准入方法。

新增 `agent_join_free_capacity_admitted`、`agent_join_siblings_coadmitted`
日志仅在真实新授权成功时写入异步 opportunity writer，包含 JOIN、
完整当前请求身份、实际上下文、所需完整预算、授权前后剩余量及时间。
它们是逻辑授权证据，不能直接当作物理分配、复制或节省的 H2D。

## 验证

`experiments/reports/reference_join_siblings_20261027/` 保留原始失败、
定向和共享检查。修复及身份日志后的定向检查为 86 项全部通过。
一次共享执行通过 3174 项及 4 个子测试，无失败/跳过，保留前次
3143 项全部身份并新增 31 项。实际 FULL、默认 ThunderAgent、工具衰减
ThunderAgent 构造器均检查隔离；原生 tree/allocator 测试使用明确的
copy/event/Host-index CPU 替身，不能推及 GPU 数值或性能正确性。

`admission_cpu_cost.py/.json` 使用 33 个 program、32 个实际 pending
记录的合成长度夹具和真实异步日志，每条路径预热 50 次、采样 500 次：

| 路径 | thread CPU 中位数/微秒 | P99/微秒 |
| --- | ---: | ---: |
| parent 已有资格 | 2.710 | 3.009 |
| parent 新授权 | 78.703 | 124.958 |
| parent 完整预算不足 | 36.832 | 45.293 |
| 两个 sibling 成组授权 | 68.328 | 93.760 |
| sibling 完整预算不足 | 38.998 | 45.858 |

这是事件回调局部 CPU 成本，不含后台 writer 线程，不是完整控制开销、
GPU 干扰或 JCT 收益。旧 claim-only 8.393 微秒夹具没有这些 pending
记录及新刷新/日志路径，不能继续作为当前实现的开销结论。

CPU 验证时尚未登记该候选的 GPU 实验。完整 free-JOIN 实验只带来 2.5869% 的
平均 JCT 改善和 1.4620% 的全程输出吞吐改善，且持续服务间隔变差；
应保留负面结果。后续若验证本候选，继续使用完整 V345 工具衰减控制，
不得重采一条获胜轨迹、删去普通长尾或启动其他 baseline/消融。

## 实际运行

后续已核对完整 free-JOIN 和 V345 的真实进程退出、预约、空闲 GPU、
端口及 socket，登记 `ops/reference_join_siblings_20261027_r1.registration.json`，
只执行 FULL，继续复用完整 V345。冻结应用为
`525c7cc66e1f80a0c35f1ecf98f080939441e943`，native 树仍为 11b84f7c3198，
端口 18662，socket 为 `/tmp/bkv-causal-18662.sock`。

实际启动的 watcher255889/start ticks730446159、server256436/
730448880、GPU scheduler256898/730450297、driver257923/730463855
均独立确认 live/nice0，实际 GPU PID 与 NVML 一致。真实 runtime policy
为 `full_with_reference_thunderagent_join_and_sibling_admission`，共同准入
及授权前 streaming 刷新均生效，物理控制健康。

`live_start.py/.json` 核对与完整 V345 相同的 13 项工作量、工具时间、
容量、引擎、流式和种子字段。这个最早快照尚无完成回执，不能作为
完成请求正确性、授权消费、完整 JCT 或吞吐的证据。

随后独立的 `collecting_pipeline_after_start.json` 封存并验证 138 个完成
请求与完整 V345 的 token、hash 和原生停止一致。其 reference 预算只有
聚合信息，逐 agent 阻塞 token-time 仍为 unknown；没有用 program 数或
全程均值补齐。该前缀也不能用于与完整对照比较性能。

CPU 绘图等待器256660/start ticks730449328为 nice19，隐藏 CUDA，
只在完整成功且 owned 退出后生成同口径 60 秒曲线、时间加权分布及
geometry 图。它不启动 GPU 工作，状态为
`ops/reference_join_siblings_20261027_r1.plots.state.json`。本轮源、配置
和外部输入保持冻结，没有再排其他 GPU 实验。
