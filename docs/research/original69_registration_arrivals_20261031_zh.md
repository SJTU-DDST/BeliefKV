# 69 原项目负载的注册与持续到达准备

## 负载的实际物化

`composite69_candidates_r1` 已从六次完整自然采集生成，
69 个不同原项目、11441 个请求、1460863 个输出 token、
304 次真实 JOIN。原任务、完整 workflow、原 token 记录及自然尾部保留。
因果图分别在各自原生时钟域编译后合并，没有直接拼接原始时钟流。

模型工作量 P80/P20 为 3.014931、P90/P10 为 4.666483；
生成量 P80/P20 为 3.189953。初始上下文范围 29111 至 241025 token，
模型/工具关键路径代理的 P80/P20 为 3.303622。
这是 CPU 负载证据，不是 serving 压力、稳态或性能验收。
4 门槛的完整项目候选仍单独保留为 49 项、P80/P20 4.061859。

筛选沿用既有规则：保留各项目首次声明的完整原题代表，
仅按固有模型工作量移除中间项目，保留未选的完整项目。
不使用一次 JCT、完成顺序或任何方法的胜负筛选任务。

## 采集后重新审计的明确入口

`scripts/requalify_causal_source.py` 仅处理一个受限场景：
原自然执行及原生 token 完整，旧依赖审计因已确认的回调观测/发布时钟
校验冲突失败。原始 `capture_failed` 状态和失败报告保持原样。

生产入口核验：

- 原始注册与实际 capture plan 一致，原冻结代码和原生树仍可验证；
- driver 正常退出、全部原 workflow 自然结束、原进程实际退出，
  两份新鲜健康 idle 快照及 writer 无积压；
- 旧失败明确为工具耗时与发布时间差冲突，修复后的报告覆盖完全相同
  的原始事件、原生事件和 token 文件完整前缀；
- 实际重新解析原生 token，复验原始生成参数和精确请求人口；
- 当前源码与完整 CPU 报告绑定，包含回调修复和重新审计检查；
- 原声明至少两个真实服务 child 的并行 root JOIN 仍满足，
  时钟冲突有已记录的原始观测值和发布调整量；
- 完整 proof 绑定所有输入，正式消费时重新核验，不以一个成功标志代替。

`register_composite_source.py` 只有收到显式的
`--origin-requalification` 才能消费这个 proof。
没有该声明时，旧 `source_ready()` 继续拒绝失败采集。
中断、未完成、其他失败、被改写的输入、缺失 token、
失效 CPU 资格、guard 终态和仍在运行的进程均不能获得此资格。

## 已审核引擎来源的显式组合

默认仍要求原生来源使用相同引擎。新增显式选项
`--allow-reviewed-native-engine-variants` 仅允许 common11 与
free-extent434 这两个已审核的精确原生树混合，统一 replay 使用 434。

混合来源还必须具有完全相同的声明 serving 字段和实际容量，
均为关闭 Full runtime 的 FULL-only native capture。
物化与重载均重新核对原始 capture plan、server info 和容量 hash。
注册时从真正使用 434 的已资格化来源取得 replay engine，
不宣称两种原生采集的调度或耗时相同，也不继承新 replay 的 GPU 资格。

六个原始来源和 69 个完整 workflow 均已实际通过新物化入口。
新契约仍要求正式来源注册、fresh GPU position/session/stop 和采样检查，
再开始匹配的策略对比。

## 工具时长与预测器保持一致

`composite69_tool_timing_r1.json` 使用相同的 TraceLab 公开样本、
相同的训练/评估分组、观察类别与 seed21。
2332 个目标调用中，2034 个保留自旧负载的调用与旧条目逐项完全相同，
298 个来自新的原项目。旧条目中仅有 116 个随完整项目筛选而不再执行。
其他工具继续保留原 trace 的交付时长。

离线工具预测先验内容 hash 仍为
`949e2b45b7f6c31c39ac4ff497a2a8bd9bcc6c1fdd055a3a94cc847bbc0835da`。
这次没有通过改变旧调用时长或重新训练预测器来改变比较条件。
核验在 `composite69_tool_timing_preservation_r1.json`。

## 到达、测量与未排序控制准备

`original69_initial12_mean70_prepare_r1` 声明：

- 12 个初始 workflow，之后平均 70 秒的泊松间隔，arrival seed35；
- 12 个预热、固定到达序号的 18 个测量、39 个背景；
- 全部测量成员自然结束后停止非测量背景，保留完整 JCT；
- 固定共同吞吐窗口为首个至末个测量到达之间，包含全部背景输出；
- permutation seed47 是预声明 47 至 99 范围中首个满足静态组间模型工作、
  上下文和外部路径均值平衡条件的种子，不使用 GPU JCT 或策略胜负；
- 最后计划到达约 4174.136284 秒，背景是否覆盖实际测量尾部仍须实验验证。

这种排列是静态条件化诊断，不宣称未条件化随机流量或稳态已经成立。
保留源负载的异构，不通过压缩真实外部时间来维持压力。

Full 控制从已完成且关闭工作量排序的 Predictive 配置派生，
保留两秒普通窗口、500 ms 补位、即时预测卸载、prepare_host、JOIN/EOS、
正常 agent 驻留与 sibling 策略；工具预取和逐层 handoff 继续关闭。
新负载的 oracle 数据仅维护相同的进度记账，工作量排序关闭，
尚未开启完整批次成本这一新目标，也不宣称旧成本已在新负载校准。
Full 和 ThunderAgent acting-decay 的实际 CPU runtime 构造均已通过。

## 验证状态

定向最新 231 项通过。初次两项失败来自测试 fixture 缺少 child 的 JOIN
归属；另一次四项失败来自调用旧 native 检查时漏传源码环境变量。
修正 fixture/调用参数后的证据保留在同一 CPU 准备目录，
没有放宽生产条件或产生额外 GPU 尝试。

完整共享资格
`background25_registration_contract_bound_cpu_r1/evidence.json`
通过 6451 项及 23 子项，保留原有 6400 项、2269 个源码输入和先前路径，
执行中源码、外部输入及原生状态均未改变，无失败、错误或跳过。
它绑定新注册实现、六来源物化负载、原始采集证据和工具时长映射。

上述记录保留了准备时点的状态。后续实际重新审计与来源注册、
ThunderAgent 的 GPU 资格和正式启动已完成，更新如下。
完整测量及新负载验收仍未完成，没有将 CPU 构造或工作量比例当作新 JCT 收益。

## 匹配比较已启动

从干净的 `9ba90576` 操作目录重新核验了 background25 原始执行、
实际退出、排空、原始 token 和时钟纠正证据，生成独立的
`background_extension_source25_timing_requalification_r1/source_requalification.json`。
旧 `capture_failed` 状态、旧失败报告及全部原始事件保持原样。
随后六个来源共同注册为
`ops/workflow_sustained_original69_source_20261031_r1.registration.json`。

本次选择 12 初始、平均 70 秒的持续到达，不额外测试 8 初始。
旧 56 项目记录中，12＋70 的 ready-paused 供给与容量竞争已足以支持
一次完整 Predictive / ThunderAgent 比较；8＋70 的旧记录使用过不同服务实现，
不能把其供给差异全归因于到达方式。
选择 12 的依据是维持在途人口及后半程供给，不使用新 baseline 的输赢选表。
69 项目增加背景覆盖是否实际达标，仍须用完整测量检查。

ThunderAgent acting-decay 先运行；其间只做 CPU oracle 开发。
两侧使用相同 69 个完整原任务、token、工具映射、到达表、固定测量组、
模型、引擎 `434c35dd`、805198 Device token、2034506 Host token、
200 GB Host、running24、2048 prefill、mixed-off。
不重跑旧 56 项目控制；69 项目需要自己的匹配 Predictive 控制。

本轮 ThunderAgent 已通过新 24 请求的 token/session/native-stop 检查，
随后通过 batch 1/8/24 采样检查，正式注册为
`ops/workflow_original69_initial12_mean70_thunder_decay_20261031_r1.registration.json`。
独立 `gpu_entry_original69_initial12_mean70_thunder_decay_r1/live_verified_r1.json`
核验实际 `running_thunderagent_acting_decay`、562 份精确完成收据、
唯一 GPU scheduler 3426210 和 nice0 服务所有者。
真实 runtime 保留五秒控制、一秒工具衰减 half-life、invocation program 身份，
未来信息关闭；Full 的预测卸载、prepare_host、JOIN/EOS、工具预取、
工作量排序、sibling 扩展和逐层 handoff 均未混入 paper 臂。

固定 18 个测量成员的收尾观察器 3426894 已实际存活、nice19、
CUDA 隐藏、单线程。所有测量成员自然结束后才停止非测量背景，
保留完整尾部、精确收据、两份新鲜 idle 快照及实际所有权进程退出。
这份前缀证明时测量完成数为 0；它不是完整 JCT 或性能结果。

`ops/workflow_original69_initial12_mean70_pair_20261031_r1.plan.json`
绑定此次两臂顺序比较。协调进程 3427176 等待 ThunderAgent 完整收尾后，
重新检查 hold、资源、输入和退出，再用相同冻结源码运行 Predictive 非排序控制。
该控制保留 2 秒普通窗口、即时预测工具卸载、prepare_host、JOIN/EOS、
驻留连续性与 sibling 策略，工具预取及逐层 handoff 仍关闭。
它没有登记新 oracle GPU 后继。

双方结束后，已有严格比较器将生成固定成员的完整平均/P50/P95 JCT、
逐 workflow 差值、共同墙钟窗口输出吞吐及 decode/全部输出时间曲线，
结果目录为 `predictive_thunder_original69_initial12_mean70_comparison_r1`。
背景删失和 workload 失败项保留；没有按最先完成者选成员或截掉长尾。

## Oracle 的独立 CPU 改进

旧组合评分只除以新增成本。在已有 active 批次时，
单个小集合可能有很小的新增 decode 成本，进而压过能共同推进更多 workflow
的可行集合。新增显式 `r5_frozen_batch_cost_proxy_capacity_v5`：

```text
C_incremental(S) = H2D(S) + prefill(S) + max(0, decode(A+S) - decode(A))
C_batch(S)       = C_incremental(S) + decode(A)
                 = H2D(S) + prefill(S) + max(decode(A), decode(A+S))
score(S)         = JOIN/workflow utility(S) / (epsilon + C_batch(S))
```

其中 A 是实际观察的当前批次，decode horizon 沿用原值，没有新增等待时间。
无已有计算时，新旧成本相同。该评分不强制最大基数，也不抢占 active agent，
仍保留 required JOIN、共享 extent 去重、逻辑/物理/输入/增长/槽位约束、
原生准入权限和 0.5 ms 搜索软截止。
旧 profile 默认行为与历史日志保持可复现，v5 与 `full_batch` 成本声明必须匹配。
`--full-batch-cost` 需要同时开启已有的完整容量修复契约。

191 项相关 CPU 检查通过。新实现对完整旧日志的 6484 个入口复核，
其中 396 个进入过搜索函数；364 个为完整实际枚举，29 个是函数早返，
3 个才是真实截止。完整枚举的集合与先前离线目标逐项一致：

| 同一 364 次完整枚举 | 旧增量成本 | 新完整批次成本 |
| --- | ---: | ---: |
| 严格缩小参考集合 | 59 | 41 |
| 新选参考集合外成员次数 | 5 | 11 |
| 相对旧执行选择更大的集合 | 0 | 25 |

这些是局部、重复决策统计，不是已获得服务的唯一 agent 数或 JCT 收益。
`complete_history_differential.json` 初稿把 32 个非完整入口误标为截止；
独立 `complete_history_differential_counts_correction.json` 明确更正为
29 早返＋3 截止，原报告和原操作脚本保留，不重复这一错误解释。

同一 CPU0、nice19 的交替顺序微基准中，新目标六候选完整搜索的各轮
P50 为 407–416 us，旧目标 405–420 us；八候选新目标 951–983 us，
旧目标 935–962 us。它是相同合成快照的纯搜索检查，
不覆盖原生摘要、全部控制路径或 GPU 性能；八候选经常触及软截止的局限仍在。
证据位于 `combination_full_batch_cost_cpu_r1`。

本次 GPU 两臂继续冻结使用 `9ba90576`；CPU 改进不会热修改运行中的服务。
新 oracle 的完整源码资格、新 workload 成本标定、干净冻结与 GPU 收益均待完成。
旧 56 项目 Predictive 相对 ThunderAgent 的 10.430% JCT 改善不归因于此新评分，
也不代替当前 69 项目测量。

## 完整资格与 69 项目候选冻结

后续共享 CPU 回归通过 6467 项及 23 子项，保留全部 6451 个原测试身份、
2270 个源码输入和既有输入路径。361 个外部输入包含全部 350 个先前绑定；
执行期间源码、原生引擎和外部输入未变，无失败、错误或跳过。
完整证据为 `combination_full_batch_cost_bound_cpu_r1/evidence.json`。

`original69_full_batch_oracle_prepare_r1` 已实际生成新的 69 项目 v5 候选，
保持相同原题、11441 个完整源请求、工具映射、到达表和固定测量成员。
它通过源 token/DAG 绑定、与非排序控制的严格配对检查以及实际 runtime 构造。
v4 成本参考与 v5 新目标的 69 个初始剩余路径完全一致，
变化仅在组合评分及其声明，不重新定义项目长短或篡改未来工作。
运行中的旧 `9ba90576` 构造明确拒绝 v5，拒绝证据保留在候选 entry 中。

此候选当前明确复用 **已完成 original56 控制** 的成本曲线，
没有宣称 original69 的匹配控制或成本标定已完成。
配置与 entry 均明确要求本轮控制完成、workload 审查、
成本重新标定或显式复用决定，以及新的 GPU position/采样资格；
没有登记 oracle GPU 后继。

源码已从提交 `8e3bbe24` 冻结到
`/home/longhao/experiment/.worktrees/beliefkv-full-batch-operator-8e3bbe24`。
`original69_full_batch_oracle_prepare_r1/clean_freeze_entry_r1.json`
独立复验全部 6467 项报告身份、2270 源码输入、外部绑定、原生状态、
69 项目数据与真实构造。实际成本身份为 `ae3c99d4`，
`full_batch`、未引用副本容量、深前缀和可行替代集合四项契约均成立。
本次冻结只完成 CPU 与数据准备，不构成 GPU 或性能验收。

## 剩余路径权重的只读检查

在相同完整旧日志、相同 full-batch 成本、相同容量和 required JOIN 下，
额外比较了现有 `1/(1+R/600)` 与共同归一化的严格 `1/R` 权重。
R 是已记录的剩余模型/工具路径；统一归一化只保持权重不超过一，
不引入新的时间常数。该检查只使用 364 个完整实际枚举；
29 次搜索函数早返和 3 次真实截止单独排除。

140 次存在可行、未受保护的 workflow 竞争者。
现有权重在其中 137 次选中至少一个最短可行未受保护 workflow，
倒数权重为 139 次。两种权重仅改变 8 次集合，
其中倒数权重 6 次缩小集合，未产生更大的集合；
参考外成员次数反而从 11 降至 10。
因此当前证据不支持继续增强长短权重作为首要修复，
保留 v5 的原权重，不新增 GPU 方案或时间超参数。
这些局部最短成员命中不证明全局 JCT 最优。

证据为
`combination_full_batch_cost_cpu_r1/remaining_path_weight_diagnostic.json`，
所有实际旧决策先按原成本、原截止复现，再比较完整的局部反事实选择；
没有模拟后续服务或 JCT。

ThunderAgent 当前仍正常推进，最新收尾观察器记录到 31 个到达、
12/18 测量完成。
一次较早的遥测读取已记录 Host 接近 200 GB 高水位、
H2D 4752731 token、D2H 4246869 token，表明迁移压力实际存在。
随后封存的
`gpu_entry_original69_initial12_mean70_thunder_decay_r1/live_migration_pressure_r2.json`
记录 H2D 5722124 token、D2H 7269522 token、Host 高水位 199999979520 bytes。
已服务同上下文前缀丢失代理仍为零，但 Host block 重算计数已出现 6146 token，
两者口径不同，不能称为完全零重算；writer 仍健康。
快照保留实际服务/观察器/协调器身份、原生状态完整内容及 hash、
物理池日志完整前缀。高水位不是平均有效占用，copy ACK 量也不是独占等待时间。
这些都是 baseline 前缀观察，不能替代 Predictive 的完整压力、重算及尾部验收。
顺序比较协调器继续等待固定 18 成员完成，整体目标保持未完成。

后续 ThunderAgent 已完整收尾，Predictive 已实际接续运行。
完整指标、覆盖失败、JOIN 服务证据及 Oracle 容量诊断更新在
[完整对照与容量诊断](original69_thunder_complete_and_oracle_capacity_20261031_zh.md)；
上文 live 数字保留为各自前缀，不代替完整结果。
