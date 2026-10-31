# 组合 Oracle 完整结果、容量修正与背景候选

## 已完成的比较

2026-10-31 19:35 UTC，冻结 de7/native434 的上下文成本与 JOIN 组合
oracle 完成固定 18 条测量 workflow。其注册为
`ops/workflow_original56_initial12_mean70_calibrated_combination_20261031_r1.registration.json`。
沿用完整原题、56 个不同项目、12 初始/平均 70 秒泊松到达、同一工具时长、
200 GB Host、805198 Device token、running24、chunk2048、两秒普通窗口。

| 指标 | 关闭工作排序的 Predictive | 组合 Oracle |
| --- | ---: | ---: |
| 固定 18 条平均 JCT | 1052.994585 s | 1073.185391 s |
| P50 JCT | 639.699137 s | 640.159964 s |
| P95 JCT | 2736.140634 s | 2819.557450 s |
| 共同窗口全部输出吞吐 | 301.891902 token/s | 300.650162 token/s |

平均 JCT 回退 1.917465%，11 条加快、7 条放慢；
共同窗口吞吐下降 0.411319%。该 oracle 不推广为默认配置。
相对已完成 ThunderAgent acting-decay 的平均 JCT 仍降低 8.712750%，
但当前最好的 Predictive 控制相对 ThunderAgent 降低 10.430219%。
不能将 Predictive 原有收益写成 oracle 排序或组合选择的贡献。

两侧测量工作精确相同：2819 个请求、362072 输出 token。
各有 52 个 workflow 自然结束、4 个非测量 workflow 右删失，
并非全部 56 条完成。完整收据、新鲜 idle drain 与实际所有权进程退出
在下一候选注册之前再次独立核验。所有旧 GPU/收尾/结果观察进程均已退出。

证据：

- `calibrated_combination_initial12_mean70_comparison_r1/comparison.json`；
- 同目录 `combination_choices_and_periodic_service.json`；
- 同目录 `figures/common_window_throughput.{png,pdf,csv}` 与
  `figures/paired_complete_JCT.{png,pdf}`，两张 PNG 已目视核对；
- `gpu_entry_capacity_initial12_mean70_r2/prior_complete_and_native_binding.json`。

共同吞吐窗口保持 55.692771–1638.954850 秒，包含所有到达对象及所有阶段
的真实输出。没有用最后一条完成时间替换分母，也没有删除测量长尾。

## 组合选择实际覆盖

`calibrated_combination_initial12_mean70_prepare_r4/summary_coverage_complete_r1.json`
绑定完整选择审计的同一日志范围：

- 6003 次组合入口，其中 5104 次为空参考集合快路径；
- 646 次没有可比较候选；
- 169 次进入选择函数但在子集枚举前返回；
- 真正枚举子集 26 次，4 次改变局部集合；
- 76 次参考集合资源摘要不支持；
- 150 次参考候选摘要失败，其中 149 次缺失前缀路径摘要、1 次无 checkpoint。

包含可选候选时，缺失 closure 为 326 次、无 checkpoint 为 110 次，
均为重复观察数，不能当作唯一 agent 数。
4 次变化只有 2 个新选入 agent episode；其中一个周期准入 episode
保留了资格并获得同 attempt 的真实服务和有界窗口内的新计算完成。
另外两次变化缩减了原参考集合，不能把每次变化都算作新服务。

此审计不覆盖全部 oracle 影响：即时 A→B 使用工作排序后做实际单供体配对，
工作量还参与压力选择。不能据组合只改变 4 次就认定全系统只受影响 4 次。
已有日志记录的组合元数据及选择 CPU 总计约 0.515152 s，
不含所有其他控制成本，也不是平均 JCT 增加 20.190806 s 的因果解释。

旧 closure 失败没有原生细分 reason，无法区分超过 64 节点、在途传输、
节点创建时间变化或布局不支持。新候选只增加诊断，保持相同安全回退；
不在缺少历史证据时宣称某一种原因解释了全部失败。

## 已验证的下一候选

容量摘要修正与前缀拒绝诊断已提交为 `98c1a971`、`7633f764`，
实际冻结为干净 `7633f764`。设计见
[组合设计](../design/calibrated_global_join_combination_20261031_zh.md)。

新增容量仅来自最多 32 个无引用、无锁、无 DMA、具有完整 Host 副本的
原生 device leaf，排除候选前缀与重复节点。没有物理分配、迁移或回收授权。
任务、工具、到达、固定测量组和五档成本不变，383 个剩余路径与 r4 精确一致。

最终源码资格 `combination_unreferenced_capacity_bound_cpu_r2`：
6318 项及 23 子项通过，保留全部先前 6314 个 literal identity，
2259 个源码输入及原生状态在执行中不变，无失败、错误或跳过。
`calibrated_combination_initial12_mean70_prepare_r5/clean_freeze_entry_r2.json`
再次核对绑定输入及实际 runtime 的容量 flag、成本身份。

启动包装器首次在创建位置注册之前因从 causal comparison 读取
`reference_registration` 而退出。保留
`gpu_entry_capacity_initial12_mean70_r1/pre_registration_failure.json`、
原脚本副本及已写入的独立核验；当时没有 GPU 动作或部分实验。
修正为读取并核对前一轮 position 资格中的 reference。
新 native 证据只改变 canonical patch 路径和派生 state hash，
原生树、补丁 bytes 和 6318 项绑定的 plain native 状态均相同。

正式位置资格注册为
`ops/workflow_original56_initial12_mean70_capacity_combination_position_20261031_r1.registration.json`，
比较注册为同前缀去掉 `_position` 的 r1，端口 18940。
24 次 token/session 检查全部通过，24 次均为原生 stop；
batch 1/8/24 采样验证完成，正式服务已实际运行。
既有 Predictive 与 ThunderAgent 对照均复用，此时没有新 JCT 或收益结论。

`calibrated_combination_initial12_mean70_prepare_r5/live_oracle_r1.json`
独立验证 675 个完成前缀收据的 prompt/output 哈希与源 trace 一致，
339 条组合记录使用新的容量摘要和正确成本身份。
唯一 GPU scheduler 为 3224778，GPU 服务所有者均为 nice0，
实际 Device/Host 容量与完整控制相同。两秒普通窗口、即时 A→B、
JOIN/EOS 保留，工具预取和逐层 handoff 关闭。

CPU 收尾观察器 3225729、结果观察器 3225737 实际存活，均为 nice19、
CUDA 隐藏、OMP/OpenBLAS 单线程，输入 pins 已核验。
注册分别为比较前缀的 `_measured_completion_20261031_r1` 和
`_results_20261031_r1`；`observers_verified_r1.json` 保存实际进程证据。
结果仍在固定 18 条全部自然完成、收据/排空/实际退出后生成，
不登记自动 GPU 后续。

### 新诊断已确认的局部限制

`live_combination_choices_r1.json` 封存早期 635 次组合入口，
`native_reasons_prefix_r1.json` 使用完全相同范围：
8 次候选摘要失败均明确报 `node ancestry exceeds bound`，
其中 2 次涉及参考候选。这是新观察到的 8 次重复事件，
不能倒推旧完整运行的 149 次缺失 closure 都是同一个原因。

该前缀中新增无引用容量下界仅在 2 次观察为正，最大 104 token，
不能将它当成实际回收量、完整运行的容量供给或 JCT 收益。
一次组合变化没有新选入成员，也不是一次新服务。
这些证据说明后续应检查超过 64 节点的有效前缀如何低成本参与比较，
并继续区分摘要不足与真正的容量不足。不得直接取消物理安全条件、
扩大无限扫描或为制造选择次数强制迁移。

## 尚未解决的工作负载要求

本轮测量末次完成 4514.537096 秒，背景末次到达 3259.858610 秒，
未覆盖 1254.678486 秒；同上下文旧前缀丢失代理为 1444930 token。
workload 仍被背景覆盖和旧前缀保留检查拒绝。规则未因性能结果而放宽，
P80/P20 按用户最新授权使用 2.9 门槛，实际仍为 2.931418。
这些结果不是已通过验收的稳态 serving 结果。

CPU 复用既有 LoCoBench JavaScript 清单筛出 26 个未使用、完整上下文
不超过 65536 token 的不同项目。排除一个清单已有源码诊断的项目后，
保留 25 个原题，合计初始上下文 898697 token；未修改源码、测试或任务。
报告为 `background_extension_original26_inventory_r1.json`，
文件名保留初筛数量，实际通过数量明确为 25。

沿用到达 seed35、初始 12、平均 70 秒，保留原 56 个到达前缀，
继续原随机序列的 25 个槽位可将末次到达延至 4999.366843 秒。
这是未绑定自然 trace workflow ID 的规划槽位，不是可执行的新负载，
不能据此认定新一轮长尾或 Host 压力一定合格。

25 个候选还需实际 root/child 工具环境资格和自然采集，工作量与 child
形状尚未知。筛选只依据原题、未使用项目和完整上下文，未使用策略胜负
或完成顺序。初次准备因 tokenizer 字段实际为路径而拒绝，校正读取 schema
后核对原 archive、metadata、scenario hash 与现有 tokenizer 一致。
尚未启动这些候选的 GPU 采集，也没有改变当前实验的流量或工具时长。

## 后续容量对照与资源 Oracle

CapacityV2 已完成相同固定 18 条，精确工作仍为 2819 请求、
362072 输出 token；52 条自然结束、4 条非测量背景删失。
独立复验通过完整收据、两份新鲜 idle 快照和实际进程退出。

| 指标 | ThunderAgent acting-decay | Predictive 控制 | CapacityV2 oracle |
| --- | ---: | ---: | ---: |
| 平均完整 JCT / s | 1175.613671 | 1052.994585 | 1047.018559 |
| P95 完整 JCT / s | 3056.289093 | 2736.140634 | 2682.556302 |
| 共同窗口全部输出 / token/s | 292.354630 | 301.891902 | 301.550202 |

Predictive 相对 ThunderAgent 的平均 JCT 改善 10.430219%、
共同窗口吞吐增加 3.262227%。CapacityV2 相对 Predictive 只改善
0.567527%，吞吐下降 0.113186%，仍不是明确的 oracle 增量收益。
固定工作、长尾和背景覆盖失败项均保留，不重复运行这两个已完成控制。

新 resource-combination 候选使用深前缀摘要和可行替代集合，
服务冻结为 `db82e94d`、原生树为 `434c35dd`，端口 18942。
`calibrated_combination_initial12_mean70_prepare_r7/live_combination_choices_r2.json`
封存 872 次入口、21 次搜索函数调用、5 次集合变化、2 个新选入成员。
这两个成员均获得周期授予、保留资格并在同 attempt 获得实际服务和
有界窗口内的有效完成。这证明选择可以影响服务，不是完整 JCT 收益。

当前继续 12 初始 / 平均 70 秒，而非更换为 8 初始，依据是既有
ready-paused 供给、容量竞争及可复用的匹配控制。旧 8/70 服务代码不同，
不能将它与本轮当作单独的到达方式消融，也不按方法胜负筛选到达表。
工具时长映射不变，即时 A→B 预测卸载、prepare_host、JOIN/EOS 保留，
工具预取和逐层 handoff 关闭。固定 18 条仍须自然完成后才能收尾。

## 旧前缀丢失对应实际 Prefill

只读审计 `scripts/audit_previous_prefix_prefill.py` 将缺失的已服务 FULL
前缀区间，与同一个原生请求、attempt、session 的实际 prefill 输入区间
逐一对应。重复或重叠区间取并集；身份、几何或观测不完整的情况保持未知。

CapacityV2 的完整证据位于
`capacity_combination_initial12_mean70_comparison_r1/previous_prefix_prefill_work_r1.json`：
8870 次 probe 中有 12 次正丢失，合计 995562 token；全部 12 次均在实际
native prefill 区间内得到完整对应，分布于 493 个 prefill batch，
未观察到重复区间。因此这些 token 确实再次进入 prefill，
不再只是缓存计数代理。

涉及 batch 的完整 worker 区间合计约 122.710 s，只是受影响 batch 的
区间总量，不是这些 token 独占的 GPU 时间，也不是可直接消除的 JCT。
Host 驱逐原因及可避免比例仍未确定。

原生 `drive_host_eviction()` 已有两遍 FULL Host 重复副本回收，
Full 的 `HostDemandCapacity.ensure()` 也已有最多 64 个候选的安全回收。
现有配置没有关闭原生重复副本回收。126 次 demand 检查及 78 次
安全镜像不足，是重复检查计数，不能直接归因于缺少 Host 回收实现，
也不能据此放宽锁、DMA 或唯一有效副本保护。

## 25 个完整原项目的补采准备

25 个项目已从同一 archive 独立物化，462 个源码/测试文件及完整上下文
均核对原始 hash，未修改任务、源码、测试或包版本。
原标签为 5 easy、9 medium、7 hard、4 expert，均为 code comprehension；
后续自然轨迹的工作量和 child 形状尚未确定。

依赖准备结果为 3 个完整安装、9 个 npm ETARGET、5 个缺少 manifest、
7 个 ML/GUI 超出有界准备范围、1 个本地 workspace 绑定问题。
保留全部失败及原版本，不以替代模块伪造应用可执行。
用户要求解决正常工具执行，不能将这些安装结果等同于 runtime 故障。

独立 CPU 工具资格
`background_extension_original25_tools_r1/toolchain_live_root_child.json`
验证全部 25 个项目 root/child 的真实工具调用及 Node fs 原源码 hash 读取，
同时公开各项目依赖状态。它不宣称 25 个应用或项目测试成功。
`background_extension_source25_toolchain_r1` 已绑定原题与工具证据，
沿用 child 4/8、root 8/16 软预算和自然尾部，不加固定 DAG 或硬调用截断。

25 项目绑定的 CPU r1 为 6373 项及 23 子项，保留 2262 个源码输入，
执行中源码与原生状态不变，无失败、错误或跳过。
它不能替代后续真实采集中对任务进展、工具失败和正常完成的审计。
已有 56 条加上这 25 条，最多形成 81 个不同原项目；三个依赖完整项目
是这 25 条的子集，不能再次累加。

准备时发现 source-capture 注册器虽接受已审核原生变体，冻结时仍硬编码
应用 common11 补丁。已改为按资格记录的目标原生树选择审核补丁，
并同时核对 canonical tree 匹配及实际 tree 等于资格中的 expected tree。
五项真实隔离 git worktree 检查覆盖 common11、free-extent434、
错误旧补丁、错误 tree 和被修改的审核补丁；相关 161 项定向检查通过。
旧 `fc1b6a9d` 冻结 operator 不包含该修复，不能用于修复后的采集。
新完整 CPU r2 已包含该 launcher 和九项旧前缀区间审计检查，
6387 项及 23 子项全部通过，保留原有 6373 项 literal identity、
2265 个源码输入及全部先前输入路径，绑定 224 个外部输入。
执行中源码、外部输入及原生状态不变，无失败、错误或跳过。
证据为 `background_extension_source25_bound_cpu_r2/evidence.json`。
仍需从修复后的提交干净冻结并复验实际采集环境；
尚未登记补采 GPU，不修改正在运行的资源 Oracle。

## Resource Oracle 的完整结果

2026-10-31 22:45 UTC，resource-combination 的固定 18 条全部自然完成。
三臂独立复验均为 2819 请求、362072 输出 token，实际容量完全相同；
各有 52 条自然结束、4 条非测量背景删失。两份新鲜 idle 快照、
完整收据、原到达序号和实际所有权进程退出均已核验。

| 指标 | ThunderAgent acting-decay | Predictive 控制 | Resource oracle |
| --- | ---: | ---: | ---: |
| 平均完整 JCT / s | 1175.613671 | 1052.994585 | 1071.047245 |
| P95 完整 JCT / s | 3056.289093 | 2736.140634 | 2735.947722 |
| 共同窗口全部输出 / token/s | 292.354630 | 301.891902 | 301.113130 |

Resource oracle 相对 Predictive 平均 JCT 回退 1.714411%，
共同窗口吞吐下降 0.257964%；18 条中 4 条改善、14 条放慢。
它相对 ThunderAgent 的总体平均 JCT 改善为 8.894625%，
共同窗口输出增加 2.995848%。这些是整体差值，不能归因于 oracle，
也不能代替已完成 Predictive 控制的 10.430219% / 3.262227% 收益。
该 oracle 不推广为默认配置，继续保留未排序的 Predictive 控制。

完整组合审计为 6484 次入口、5493 次空参考快路径、
396 次搜索函数调用，其中 29 次在枚举前返回，367 次真正枚举子集。
65 次改变集合：56 次来自周期恢复、9 次来自免费补位；
60 次没有加入新成员、只是缩减原集合，只有 5 次加入参考外成员。
五个新成员均有真实周期授予、压力检查后资格、同 attempt 服务和
有界有效完成。不能再把结果简单解释为“组合路径没触发”，
应进一步审计缩减并行对象是否符合实际批次成本与完整 JCT 目标。
本审计不覆盖全部 A→B 排序和压力暂停影响，也不是其因果收益分解。

完整组合元数据与选择线程 CPU 合计 1.089850 s，
不包含其他控制路径，不能直接解释均值增加的 18.052660 s。
缺失候选摘要为 122 次 `native_checkpoint_unavailable` 重复观察；
原先的深度超限不再出现于这份完整审计。没有取消原生安全边界。

新旧前缀区间审计还确认，本轮 14 次正丢失的 1258680 token
全部再次进入实际 native prefill，涉及 622 个 batch、没有重复区间。
其完整 batch worker 区间合计约 153.835 s，不是纯 GPU 时间或
可直接消除的 JCT。对照仅有前缀丢失代理值时，不作精确因果比较。

背景末次到达仍早于测量尾部，原有两个 workload 失败项继续保留。
共同窗口 ready-paused 采样占比为 0.311722，P80/P20 仍为 2.931418。
不因为 oracle 回退换种子筛选输赢，也不继续重复相同控制。

全部证据在 `resource_combination_initial12_mean70_comparison_r1`：
`comparison.json`、`independent_complete_verification_r1.json`、
`combination_choices_and_periodic_service.json`、
`summary_coverage_complete_r1.json`、
`previous_prefix_prefill_work_r1.json`。
decode/全部输出曲线和完整配对 JCT 的两张 PNG 已目视核对。

补采 launcher 修复已提交为 `5102ec03`。单独干净 operator 在相同代码上
提交原始工具资格 manifest 为 `dd7f10a9`，用于让新冻结 checkout 真正携带
该清单；不改变任何原任务或上下文。完整 CPU 资格仍为 6387 项及 23 子项，
源码文件与外部绑定保持不变。采集环境明确为 native_matched，
不构造 Full runtime，不开启工作集调度、预测卸载或工作量 oracle。
初次只读冻结检查错误地期待 Full constructor 回执，保留其 native proof；
更正为原生隔离核验后通过，不存在该次检查触发的 GPU 尝试。

### 组合缩减的容量与评分诊断

`packing_feasibility_complete_r1.json` 用实际冻结的成本、候选、容量和
截止边界重放全部 65 个变化，检查选中集合是否仍可增加一个已就绪候选。
27 次变化存在满足记录资源下界的单成员扩展，均来自严格缩减参考集合；
合计 38 个可扩展对象原本就是参考成员。20 次变化的原参考集合本身可评估，
并非所有缩减都是因为原参考放不下。

一个已记录例子中，原参考为两个成员，新选择只有一个；资源摘要仍允许
把另一成员加入。单成员输入 175 token、增量成本约 10.557 ms，
两成员输入 7120 token、下一 chunk 为 2048、估计成本约 254.057 ms。
局部收益/增量成本比因此偏向单成员。这个例子说明当前目标可能减少
可准入的并行工作，也说明需要保留 prefill 对已有 active 请求的干扰成本，
不能把“人数最大”直接替换为新的最优目标。

这些都是记录摘要内的局部可行性，不是新增原生分配权限、实际 GPU 空闲
或可避免 JCT 的证明。下一候选应先验证不必要的集合缩减与实际服务间隔、
批次效率的联系，再调整评分或有界补齐，不能为增加改变次数强制迁移。
本轮 GPU 服务及历史选择没有被热修改。

### 新原项目采集已实际启动

干净冻结检查 r3 再次验证 `dd7f10a9` 的 2265 个源码输入、
6387 项既有 CPU 资格、外部原题/工具证据、实际 native 隔离环境和
103 项原生引擎检查。原生树严格为 `434c35dd`；这不是新采集完成证明。

确认 resource oracle 的测量、收尾和全部 GPU/观察进程退出后，
新注册为 `ops/workflow_original_background25_source_20261031_r1.registration.json`，
实际服务 checkout 为 `beliefkv-bg25r1-capture-dd7f10a9ab24`，
端口 18944，原生引擎为 `sglang-bg25r1-capture-434c35dd6621`。
fresh hold、空闲资源和 batch 1/8/24 CUDA 原生采样检查均通过。

独立进程检查确认 watcher 3342547、driver 3342776、server 3342791、
唯一 GPU scheduler 3342915，GPU 服务所有者均为 nice0。
实际 census 仍为 Device 805198 token、Host 2034506 token / 200 GB、
FULL-only、无 Mamba，running24。客户端清单为 25 个原项目、
25 个在途 root 窗口，没有未提交的 root backlog。

首轮真实 token 日志检查观察到 492 份 prompt、469 份 output 前缀记录，
native 遥测已进入 prefill/decode，writer 无错误或丢弃。
这些是运行中的前缀数量，不是完整自然 trace、应用正确性、正常 root
完成或新 workload 验收。采集保持真实工具时间，后续仍须检查原题进展、
真实多成员 JOIN、自然尾部和环境类失败，再决定是否与已有 56 条合并。
没有登记采集后的自动 GPU 对比，也没有重跑已完成控制。
