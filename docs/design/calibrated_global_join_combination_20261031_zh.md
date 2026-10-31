# 两秒全局 oracle 与 JOIN 组合准入的成本诊断

## 为什么继续使用当前负载

12 初始 workflow、平均 70 秒到达已经有完整匹配对照，不重跑该对照、
不同时改变到达方式，不修改 trace 工具时长。本候选复用全部 56 个原项目、
固定 12 warmup / 18 measured / 26 background、完整 token/依赖和 200 GB Host。
共同吞吐窗口及全部测量成员的完整 JCT 保持一致。

[上一轮完整结果](../research/toolaware_oracle_complete_and_costs_20261031_zh.md)
没有达到 JCT 目标。实际选择已经到达服务，但存在 prefill/decode 换算偏差
与单体排序减少可并行对象的情况。本候选同时调整时间成本与集合选择，
明确是联合机制诊断，不是只切换 remaining-work ranking 的消融。
旧配对及未通过的背景覆盖/前缀丢失检查全部保留。

## 运行路径

使用已有 `WorkflowCombinationController` 的六候选窗口，
仅为观察到的 sibling 扩展到八名，搜索软预算为 0.5 ms。
元数据构造、原生检查、日志和后台成本另计；该预算不是整轮控制的硬上限。
不实现逐 decode 全队列重排，不因短任务判断抢占已有 active。

周期恢复继续使用本入口的工具感知 decay 预算，普通事件补位继续使用
完整未折扣空闲额度和四个授予上限。两者使用相同组合评分，但不混用预算。
两秒窗口仍用于普通准入；必须已提交请求、满足真实依赖且当前 ready。

组合参考 S0 保留 ready JOIN 保护、原类别/上下文稳定顺序和已有 sibling
邻接，再比较窗口内可行子集。原方案入选者无法完整保留或摘要不可用时，
返回 S0。资源检查、Host backing、原生 allocator、锁与 DMA 仍有最终权限。
逻辑折扣和计划暂停不提供物理空闲页。

## 工具预测卸载继续启用

运行时 A→B 是原有预测式工具卸载的一部分，不增加关闭开关。
它不等两秒普通窗口；先比较当前真实 ready-paused 消费者，再检查真实阻塞
供体及完整单供体配对。只有实际安全释放后，才重新核对完整容量并授予 B。

这一入口不能使用“供体尚未释放时的当前空余额度”预先筛掉 B，
否则在最需要供体的时刻会得到空集合。为此工具配对使用相同工作量排序，
而不走普通空余额度组合筛选。多供体全局组合仍未实现，不能宣称所有入口
已经完成统一组合最优化。

工具预取、普通恢复租约、逐层 handoff 和 mixed chunk 保持关闭。
JOIN/EOS 和 prepare_host 保留；仍在等工具的 paused owner 不因预测返回时间
到达而获得资格。真实返回、其他依赖及已登记后继共同决定 ready。

## 成本与评分冻结

v2 oracle 使用已完成对照的 worker 区间代理：

- 输出步长 `0.023478283345973637 s/output token`；
- 输入成本 `0.0000832252503024952 s/input token`；
- 第一次实际观察到正输出进度后，移除本请求的剩余 prefill 成本；
  输入的固有工作记账仍在请求完成时结算。

固有工作量、0.01 人口统计、分层及源 DAG 均不改写。
工具关键路径继续按并行依赖取最大值，不累加并行等待。
这不是最优 JCT 上界或纯 GPU 时间；原生进度通常以 128 token 或完成事件上报。

组合沿用既有 R5 代理：

```text
workflow weight = 1 / (1 + remaining model/tool path seconds / 600)
set score = sum(workflow weight * capped JOIN coverage increment)
            / (0.1 ms + incremental copy/prefill/16-step decode cost)
```

600 秒沿用既有控制器默认归一化，不根据这次 GPU 结果调参。
JOIN coverage 使用当前未完成成员、已有 active-ready 成员和新增成员的
平方覆盖增量，每 workflow 封顶，避免 fanout 增加评价身份。
恢复成本按缺失 extent 去重，decode 成本按现有 batch 的增量查表。
候选资源摘要无法支持比较时保留原生回退。

关闭 remaining-work ranking 的显式配置不读取未来路径构造组合效用，
直接使用参考集合。新比较读者验证源请求、图、工具、到达、资源、窗口及
目标阈值一致，并拒绝将此候选标成 ranking-only。
不能对测量组身份增加权重或按完成结果重新选择测量成员。

## 已完成的 CPU 验证

`calibrated_combination_initial12_mean70_prepare_r3` 是最新 CPU 配置：
实际构造入口启用全局组合与两秒窗口。
保留 r1/r2 准备记录；r2 补充准确的联合变化和既有对照复用声明，
r3 使 qualification sidecar 指向实际 v2 kind、路径和 hash。
三份准备都没有 GPU 结果。

定向测试覆盖普通窗口、ready JOIN、真实 native 选批候选、
零空余额度下 A→B 排序、工具阻塞排除、关闭排序后的未来信息隔离，
以及流量/资源/固有工作/成本声明不匹配的拒绝。
初次定向 r1 的三个夹具失败保留；修正模拟时钟/前缀证据，并让空候选
跳过不必要的组合调用，随后独立定向检查通过。

源码绑定共享检查：
`experiments/reports/remaining_original_projects_20261030/calibrated_combination_initial12_mean70_bound_cpu_r1/evidence.json`。
6212 项及 23 子项通过，无失败、错误或跳过；6135 个先前 literal identity
全部保留，2249 个源码输入及原生树在执行中不变。

完整旧对照事件回放验证 9310 个已完成请求、52 个关闭 workflow；
固定测量组的 2819 请求、362072 输出完整守恒。
其余背景仍按原协议删失，不表示全部 56 个完成。
383 次 call-boundary 路径检查累计约 855.978 ms CPU，
不是实际在线控制成本或 GPU/JCT 收益。

## 组合搜索的 CPU 优化

后续检查只调整枚举的计算方式，不改变评分、参考集合、JOIN 保护、
逻辑/物理预算或子集顺序：

- 用 bit count 在构造成员列表前排除超槽位集合；
- 对整数 token 预算增量计算子集容量，先排除逻辑上放不下的集合；
- 缓存同一轮不变的已有 decode batch 成本；
- 保留原八个 mask 一次的截止时间检查、原回退及原枚举计数。

浮点预算仍使用旧的逐集合求和，避免整数优化改变其加法顺序。
基于组合枚举的独立参考检查覆盖六/八候选、保留 JOIN 权利、共享
extent、原始评分及资源拒绝。四项新测试和完整运行时定向检查通过。

新增 CPU 微基准使用当前已冻结成本表与固定随机快照，
分别测量完整搜索和 0.5 ms 搜索；它不构造真实 native 元数据或测量 GPU。
两轮的每个候选宽度各有 300 次完整搜索，新旧选择、评分和枚举计数精确一致。
首轮 unrestricted 的 after 测量与定向 pytest 重叠，保留原始结果；
另做 CPU0 顺序复测，避免只公开较有利的一轮。

| CPU0 顺序复测 | 优化前 | 优化后 |
| --- | ---: | ---: |
| 六候选完整搜索 P50 | 391.265 μs | 363.773 μs |
| 八候选完整搜索 P50 | 1037.861 μs | 848.844 μs |
| 六候选 0.5 ms 搜索触及截止比例 | 37.33% | 36.33% |
| 八候选 0.5 ms 搜索触及截止比例 | 82.67% | 62.00% |

中位完整搜索分别缩短约 7.03% / 18.21%，六候选 P99 没有改善；
八候选有预算搜索的 P99 仍约 585 μs，高于 500 μs。
这是有界搜索计算和完成度的改进，不是总控制开销、JCT 或 GPU 收益；
快照/元数据构造、日志和后台成本仍需端到端验收。
有时间预算时，因为能检查更多子集，实际选中集合可能与旧版本不同。

证据均归入原候选目录：
`calibrated_combination_initial12_mean70_prepare_r3/search_cpu_comparison_r1.json`
和 `search_cpu_{before,after}_{r1,r2}.json`。
源码绑定共享检查
`calibrated_combination_search_prefilter_bound_cpu_r1/evidence.json`
通过 6240 项及 23 子项，保留 6236 个先前 identity 和 2254 个源码输入，
无失败、错误或跳过；执行中源码和原生树不变。
正在运行的 ThunderAgent 仍使用旧冻结源码，不受此优化影响。

## 干净快照与只读选择核验

`bf5c6c58` 已从干净 checkout 冻结，
`calibrated_combination_initial12_mean70_prepare_r3/clean_freeze_entry_r1.json`
重新验证全部 2254 个绑定源码输入、6240 项已有检查、外部配置及报告 hash，
并从该 checkout 调用实际 runtime 构造。它确认组合选择、两秒普通窗口、
预测式工具卸载开启，工具预取和逐层 handoff 关闭。此检查不重新执行旧测试，
不注册或启动 GPU，也不修改运行中的 ThunderAgent。

新增只读 `scripts/audit_workflow_combination_choices.py` 验证组合日志：

- 重建相同候选、预算、成本、JOIN 保护和参考集合 S0；
- 从枚举计数恢复可能的实际截止边界，核验有软截止的返回值，不能用完整搜索
  的最优结果冒充线上在截止前得到的结果；
- 以日志顺序绑定下一次实际周期准入，再以完整请求、attempt、session 身份
  关联压力处理后的资格和有效原生计算；
- 有效计算窗口止于下一次控制或同 agent 的已观察暂停/重授予；
- 元数据不足、缺少 grant、观察窗口未结束或缺少服务，均保留 unknown。

这是相同状态上的局部 S0 比较，不是重建另一整轮控制的未来状态。
普通事件补位的选择也会重放，但其服务关联仍须使用单独的 free-dispatch
审计；即时 A→B 是单供体配对，不计为组合搜索。不能将这里的变化次数、
后续服务或代理评分直接换算为因果 JCT 收益。

分析器及相邻只读检查共 74 项通过，其中实际 runtime 日志适配测试覆盖
周期和普通补位。r1 的两个失败来自测试请求夹具未提供原生 session 身份，
r2 给请求绑定 session 后通过，未放宽分析器的身份检查。
证据为本候选目录的 `choice_audit_cpu_r1.xml` 和 `choice_audit_cpu_r2.xml`。
分析器没有改变 `bf5c6c58` 服务快照、6240 项既有资格或正在运行的观察器。

## 上下文相关的准入 Prefill 成本

继续审计已完成的同一 predictive 对照发现，单请求、extend 至少 256 token
的 prefill worker 成本随上下文长度变化明显。上下文使用真实
`sequence_tokens_before + token_delta`，前者是本批次前缀，不能将它当作
完整输入或要求它大于 extend。

| 本批完成后的上下文范围 | 样本批次数 | worker ms / extend token 中位数 |
| --- | ---: | ---: |
| 小于 8192 | 720 | 0.034491 |
| 8192–32767 | 3448 | 0.059538 |
| 32768–65535 | 1192 | 0.100981 |
| 65536–131071 | 1351 | 0.171672 |
| 至少 131072 | 725 | 0.307888 |

旧统一成本为 0.083225 ms/token。这不是原生 GPU 时间或 JCT 根因证明，
但在组合选择中会低估长上下文补入成本。诊断证据为 r3 目录的
`prefill_context_cost_diagnostic_r1.json`，绑定同一已完成控制的完整
native prefix，不使用运行中的 baseline 或下一轮 oracle 结果拟合。

`calibrated_combination_initial12_mean70_prepare_r4` 在同一 v2 artifact
中显式加入五档准入成本，支持至 262144 token。按必要输入区间
`[context - missing_input, context]` 分段积分，每个候选只计算一次，
集合枚举复用成本。缺少支持的摘要继续返回原方案，不扩大物理容量。
不启用这张表时保留旧常数成本及原浮点求和，旧实验保持冻结。

这项变化只细化准入的即时 prefill 成本；完整 workflow 的固有模型/工具
剩余路径仍沿用 r3 的全局 prefill 代理，未声称其已成为上下文精确模型或
最优 JCT oracle。任务、token、图、工具、到达、固定测量成员与资源均不变。
r3 的干净快照和未来启动准备只属于 r3，不自动作为 r4 的运行资格。

r4 已通过实际构造，完整旧对照事件回放仍为 9310 个完成请求、52 个自然
结束 workflow，固定测量工作守恒为 2819 请求 / 362072 输出；
383 次 call-boundary 路径查询约 974 ms CPU。这是离线回放成本，
不包含真实原生 partial callback，不是在线控制或 GPU 收益。
包含上下文边界、旧成本兼容、候选缓存和显式证据绑定的完整源码回归通过
6290 项及 23 子项，保留先前全部 6240 个 literal identity，2257 个源码
输入与原生树在执行中不变，无失败、错误或跳过。证据为
`calibrated_combination_context_cost_bound_cpu_r1/evidence.json`。
服务源码已从 `de7b3e9b` 干净冻结，GPU 比较已启动，完整 JCT 尚未产出。

## r4 GPU 启动与观察

正式比较注册为
`ops/workflow_original56_initial12_mean70_calibrated_combination_20261031_r1.registration.json`，
端口 18938，原生树仍为 `434c35dd`。复用已有完整 predictive 控制，
不重跑该控制或刚完成的 ThunderAgent。

首次位置资格注册在创建注册文件和启动 GPU 之前，被原生证据中的旧
operator 补丁绝对路径拒绝。保留 `registration_attempt_r1.json`；
逐字段验证真实 native 树、补丁 bytes 和 6290 项检查绑定的原生状态不变后，
只重绑定路径及派生 state hash。修正证据为 `native_source_path_rebind_r1.json`。
位置资格使用显式 r2 注册，实际比较仍只有一轮。

新的 24 请求 token/session/原生 stop 检查全部通过，24 次均为原生 stop，
batch 1/8/24 的 position 采样检查通过。`live_oracle_r1.json` 独立验证：
实际 `running_full`、176 个完成前缀收据、唯一 GPU scheduler 3184710、
服务所有者 nice0、完整容量不变，以及正确的上下文成本身份。
JOIN/EOS、prepare_host、即时预测卸载与两秒普通窗口保持原配置。

固定测量组收尾观察器 3185277、结果观察器 3185288 实际存活，
nice19、CUDA 隐藏、OMP/OpenBLAS 单线程，输入 pins 已重新验证。
结果将在完整 18 条、收据、排空和实际退出后生成匹配控制对比、
组合选择/周期真实服务审计和明确标注的新图表。未注册自动 GPU 后续。

早期 `live_combination_choices_r1.json` 封存 200 次组合入口观察，
6 条具有可重放搜索状态，尚无改变集合。其余包括无竞争、
参考集合资源摘要不支持和候选摘要不可用；默认 metadata fallback
还包含无参考准入集合的快路径，不能全解释为真实元数据获取失败。
这只是早期前缀，不是完整覆盖率、JCT 或吞吐收益。

## r5：补全无会话引用的冷缓存容量

r4 的资源摘要只合计 allocator 当前空闲页与已登记的、私有阻塞 agent
的可回收副本。原生会话关闭只解除引用，不立即释放缓存页；原生 prefill
还可以回收无会话引用的缓存。因此摘要可能比实际原生准入更保守。

`choice_resource_gaps_prefix_r1.json` 封存的前缀包含 6 次参考集合资源拒绝。
在各自原有摘要、候选和 JOIN 保护下枚举全部子集，均无可行集合；
其中包括需求 2057 token（2048 输入加 9 增长预留）、可用摘要只有
1900 token 的小缺口。
这证明当时的摘要挡住了比较，不能证明被漏计的安全缓存一定足以补齐缺口。
旧日志没有记录这些无引用节点，不补造其容量，也不将 6 次都算作已修复机会。

新的显式 profile 为 `r5_frozen_batch_cost_proxy_capacity_v2`，
在原 32 个阻塞节点检查之外最多检查 32 个原生 device leaf。只接受：

- FULL-only 池中仍由原生树持有的同一节点，排除根和重复身份；
- 完整且等长的 HBM/Host 副本，原生 device-evictable leaf 集合中的成员；
- 所有组件的 session、device lock、Host lock 引用均为明确的整数零；
- 无 session ID、无在途 D2H/H2D，也没有待确认的 write-through；
- 不属于本轮候选的匹配前缀 anchor，且不与既有阻塞摘要重复计费。

这里只增加资源下界的可观察范围，不释放节点、不启动备份或传输，
不把逻辑额度当作物理分配权限。原生锁、分配、迁移和 ACK 仍为最终权限。
候选前缀保护同时作用于旧阻塞摘要和新增无引用摘要。
日志分别记录原阻塞容量、受保护 anchor、新增无引用容量和摘要类型。
旧 profile 不启用新增检查，保持原历史路径。

准备目录为 `calibrated_combination_initial12_mean70_prepare_r5`。
新 profile 的身份同时绑定成本和容量摘要版本；旧 de7 operator 明确拒绝它。
任务、图、工具时长、到达、18 个测量成员、容量和五档成本全部不变。
9310 个已完成请求、52 个自然结束 workflow 及固定测量工作
2819 请求 / 362072 输出再次守恒，383 个 call-boundary 剩余路径与 r4 精确相同。
`old_operator_rejection_and_scope_r2.json` 保存显式旧版本拒绝及范围核验；
r1 的外层异常未展开内层 stderr，保留失败并在 r2 补齐证据。

`combination_unreferenced_capacity_bound_cpu_r1/evidence.json`
验证 6314 项及 23 子项通过，保留全部 6290 个先前 literal identity
和全部旧源码路径，绑定 2258 个源码输入；执行中源码及原生状态不变，
无失败、错误或跳过。新增 24 项覆盖引用、锁、DMA、Host 缺口、重复/失效节点、
有界检查、实际构造、候选前缀保护、真实选择变化和旧 profile 隔离。

该候选仅完成 CPU 验证，尚未注册 GPU，也没有 JCT 收益证据。
正在运行的 r4 仍使用冻结 de7/native434，不热更新。须先完成其固定
18 条及完整收尾，再决定是否值得单独验证容量修正。

### 冻结核验与摘要覆盖诊断

初始容量修正已在干净 `017ea089` checkout 中独立核验：
`calibrated_combination_initial12_mean70_prepare_r5/clean_freeze_entry_r1.json`
重新检查 2258 个源码输入、全部绑定外部输入、6314 项测试报告和原生状态，
并实际构造 runtime，确认新容量摘要开启且成本身份一致。
这项核验不查询或占用 GPU，不自动登记后续实验；它只绑定该初始版本，
不自动覆盖后续诊断代码。

对 r4 的同一已封存前缀进一步细分：
`summary_coverage_prefix_r1.json` 与 `live_combination_choices_r3.json`
绑定完全相同的日志范围。4027 次组合入口中，3408 次是空参考集合快路径，
122 次进入选择函数但在枚举前返回，真正枚举子集的只有 18 次，
其中 4 次改变集合。这不是全部 oracle 影响的计数：即时 A→B 的工作排序
和压力暂停选择不在该组合审计范围内。

94 次参考候选摘要失败中，93 次为 `unobservable_missing_closure`，
1 次为 `native_checkpoint_unavailable`。包含可选候选时，分别为
220 和 82 次重复观察，不能当作唯一 agent 数量。
旧日志没有保存原生 closure 的具体原因，不能据此断言全是路径超过
64 节点，也不能假设取消这个上限就能安全、低成本地比较。

为后续实际验证增加只读诊断：在原有相同拒绝条件下，记录原生 reason、
是否 observable、节点数、64 节点上限、anchor 创建时间是否匹配、
checkpoint 和缺失 FULL token。未增加扫描、重试或放宽在途传输条件。
四项新增测试覆盖过深路径、待加载、待卸载和 anchor 变化，
同时验证原参考准入与检查次数保持不变。

`combination_unreferenced_capacity_bound_cpu_r2/evidence.json`
通过 6318 项及 23 子项，保留全部 6314 个先前 literal identity、
全部旧源码路径与既有外部输入，绑定 2259 个源码输入，执行中源码及
原生状态不变，无失败、错误或跳过。54 项定向检查亦通过。
新诊断与当前运行的 r4 保持隔离，尚无 GPU/JCT 验证。

## r6：支持超过 64 节点的有效前缀

运行中的 capacity r1 新诊断已确认，早期 8 次缺失 closure 都是
`node ancestry exceeds bound`，其中 2 次涉及参考候选。
原生 `inspect_beliefkv_reentry` 本来就能检查完整请求路径；
限制出现在组合选择器构造精确、可去重的逐节点 extent 时。
深路径本身不是原生 KV 不可恢复或候选不可执行的证明。

新增显式 profile `r5_frozen_batch_cost_proxy_capacity_v3`：

- 精确路径仍最多检查 64 节点；只在明确报深度超限时尝试新分支；
- 对同一可执行请求额外调用一次现有原生 checkpoint 读取，强制刷新；
- 验证 FULL-only 布局、有效 checkpoint/缺失量/驻留前缀、一致的可复用长度、
  session 身份、单一 anchor 及当前节点创建时间；
- 将缺失 FULL token 作为该请求独立的聚合 extent，保守地不做跨请求共享去重；
- 使用刷新后的 checkpoint 计算新增输入，并保护旧、新观察的 anchor；
- 无有效新摘要时保留原回退，在途读写、布局错误和节点变化不触发聚合分支。

新分支不调用分配、卸载、备份或迁移接口，不扩展物理权限，不改变
JOIN/EOS、工具预测、普通窗口、原生最终准入或 agent 连续性。
六候选/按 sibling 扩到八的上限与 0.5 ms 搜索软预算保持原值。
原生 checkpoint 读取随自身路径长度增长，不能把额外一次读取说成常数成本；
搜索预算也不代表整个元数据构造一定低于 0.5 ms。

聚合 extent 的身份包含请求、session generation、context/epoch 和 checkpoint。
不同候选即使引用相同底层前缀也分别计费，因而可能过高估计共同恢复成本，
不能将它声称为精确共享页去重。这是支持深路径的保守方案，
共享代价和准入收益仍需实际验证。

新身份同时绑定成本和摘要版本。旧 profile 的行为不变；
旧冻结 `7633f764` 的实际 runtime 构造明确拒绝 v3。
准备目录 `calibrated_combination_initial12_mean70_prepare_r6` 保存新配置、
artifact、构造与历史进度核验。已有成本、任务、工具、到达、测量组与资源
未改变，383 个剩余路径样本与 r5 精确一致；
完整控制历史仍为 9310 完成请求、52 自然结束 workflow，
固定测量工作 2819 请求 / 362072 输出守恒。尚未注册 r6 GPU。

### 验证与 CPU 成本

新增 26 项覆盖实际 runtime 配置隔离、真实选择与日志重放、共享缺失量
保守计费、刷新后几何变化、无效/过期 checkpoint、有限创建时间、
在途传输回退及真实原生深路径。96 项相关定向检查通过。
实际 CPU radix tree 用例含 80 节点、80 token 前缀，其中 64 token 在 Host、
16 token 在 Device，摘要读取前后 allocator 空闲量和原生 IO 队列不变。

原先用例错误预期一次回收全部 80 token，保留两个夹具失败记录并改为
使用原生实际释放的 64 token。`before_native_r3.xml` 才是修正前有效深路径
无法生成摘要的复现。初次定向 r1 中三个失败包含缺少 session 的模拟请求，
r2 修正该夹具后还暴露真实 native 创建时间为 NumPy 标量；复用现有
`normalize_native_creation_time` 后，r3 的 96 项全部通过。
未为使新路径通过而修改原生树、物理回收或传输代码。

源码绑定检查 `combination_deep_prefix_bound_cpu_r1/evidence.json`
通过 6344 项及 23 子项，保留全部 6318 个先前 literal identity 和原输入路径，
绑定 2260 个源码输入；执行中源码及原生状态不变，无失败、错误或跳过。
这不等于 GPU 正确性或 JCT 收益。

独立 CPU 微基准使用真实 native tree/CPU 索引张量及既有 IO stand-in，
固定 CPU0、nice19，每种路径各 300 次，交替新旧测量顺序：

| 80 节点路径 | 旧路径 CPU P50 | 新路径 CPU P50 | 新路径 CPU P99 |
| --- | ---: | ---: | ---: |
| 80 token | 351.663 us | 468.957 us | 485.865 us |
| 81920 token | 429.011 us | 619.299 us | 638.606 us |

证据为 r6 的 `deep_prefix_cpu_probe_r1.json`，保留全部原始样本。
两种路径覆盖不同：旧路径返回无摘要，新路径返回有效聚合结果。
两侧均绕过 native checkpoint 缓存，以包含实际检查成本；
未测量整轮六/八候选控制、GPU kernel、拷贝或 JCT，不据此推导全局收益。
该微基准是独立证据，不冒充前述 6344 项检查的一部分。

提交 `7f75a954` 已在干净 checkout
`/home/longhao/experiment/.worktrees/beliefkv-deep-combination-operator-7f75a954`
独立复核。r6 的 `clean_freeze_entry_r1.json` 验证全部 2260 个源码输入、
绑定外部输入、6344 项报告、原生状态和微基准源文件，
并实际构造 runtime，确认新聚合 flag、无引用容量 flag 和成本身份一致。
该核验没有 GPU 动作，也不自动注册后续实验。
当前 capacity r1 继续使用原冻结 `7633f764`，等待固定 18 条完整收尾。

### 另外保留的资源选择缺口

完整 r4 的同一封存范围额外枚举了 76 次
`reference_resource_summary_unsupported`：
15 次存在满足相同资源下界和 JOIN 保护的非空子集，
其中 3 次可以纳入参考集合之外的成员。
这与早期仅 6 次拒绝中均无可行子集的结论不冲突。
证据为 r4 的 `resource_alternatives_complete_r1.json`。

这些是局部可行性审计，不是已经发生的服务或因果 JCT 收益。
当前 r6 只修正深路径摘要，不同时更改原参考集合资源不支持时的选择规则。
后续应分开验证可行替代集合、真实原生选批与完成时间，保留 JOIN 权利。

## GPU 验收

需要干净冻结源码、当前预约/资源检查、新的 24 请求 position/session/stop
与采样资格。正式运行仍在 18 个固定成员全部自然完成后终止非测量背景，
收集新鲜排空及实际进程退出证据。

除完整平均/P95 JCT 和固定共同窗口吞吐外，核对：

- 各入口的组合次数、改变集合次数、回退原因和全部控制成本；
- 授予是否被压力撤销、是否为同 attempt 的后续真实服务；
- sibling 的实际服务覆盖、Host 前缀丢失及迁移/重算；
- 长 workflow 和背景任务退化，以及背景到达覆盖的原失败项。

不得因某次集合评分更高就宣称 JCT 改善。此文档记录 CPU 候选，
GPU 与性能结果在后续父实验的命名证据目录中保存。
