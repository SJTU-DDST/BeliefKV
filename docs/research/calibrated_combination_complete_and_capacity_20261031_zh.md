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
