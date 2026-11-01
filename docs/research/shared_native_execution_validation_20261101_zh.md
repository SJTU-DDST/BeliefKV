# 共同原生执行配置验证

2026-11-01，按本地日期命名。服务器的部分记录使用 Asia/Shanghai，
对应的资格检查时间已经进入 2026-11-02。

## 目标与固定条件

将已经测得组件正收益的精确 decode graph 与 MoE 参数一起提供给
Predictive Full 和 ThunderAgent acting-decay，然后比较完整测量 JCT、
共同墙钟窗口吞吐、请求 interactivity 和迁移/重算。
它是共同引擎优化，不作为 Predictive 专有的调度创新。

保留 original56、initial12、平均 70 秒持续到达，以及原固定 18 个测量成员。
仍在所有测量成员完成后停止未测量背景工作，保留每个测量 workflow 的完整 JCT。
不改工具时长、原始 token、任务、依赖、实际 Device/Host 池容量或各方法策略。
Full 保留 JOIN/EOS、预测工具卸载、prepare_host 和 agent 连续性；
工具预取与逐层 handoff 保持关闭。

基准配置：

- Full：`tool_aware_pressure_initial12_mean70_pair_cpu_r1/control.json`。
- ThunderAgent：`thunder_initial12_mean70_prepare_r1/thunderagent_acting_decay.json`。
- 私有分析组：`sustained56_initial12_mean70_r1/analysis_cohort.json`。
- 上述路径都相对于 `experiments/reports/remaining_original_projects_20261030/`。

Full 的已有工作量 bookkeeping 仍在，但控制工件
`remaining_work_ranking_enabled=false`；不重新启用工作量 Oracle 排序。

## 显式配置与资格链

`configs/execution/qwen3_coder_h200_exact_moe_20261101.json` 声明：

- Qwen3-Coder-30B-A3B-Instruct、BF16 权重/KV、FA3、TP=1。
- Device 805,198 token、running/decode 上限 24、prefill chunk 2048、mixed 关闭。
- Decode graph 使用 1..24 精确桶，prefill 保持 breakable。
- MoE 使用既有九键压缩表，不改变大于 24 的参数区间。

`beliefkv/experiments/native_execution.py` 只在共同环境显式提供
`NATIVE_EXECUTION_CONFIG` 和 `NATIVE_EXECUTION_CONFIG_SHA256` 时生效。
配置及 kernel 文件必须在冻结 checkout 内，核对各自哈希。
MoE 目录按 checkout 解析，不引用开发目录中的可变副本。

`prepare_native_execution_comparison.py` 保留每种方法的原配置，
只增加上述两个共同环境字段；私有 cohort 只留在 launcher/分析元数据，
不交给调度器。
已生成声明位于 `useful_token_cost_r1/shared_execution_prepare_r1/`。

新的 position 资格检查验证实际 server-info、GPU worker 加载文件和
24 个 token/session/stop 请求，再执行 1/8/24 采样成本检查。
正式注册拒绝旧默认引擎的 smoke；实际运行和最终结果验证也核对共同配置。
Predictive/ThunderAgent 比较另外核对配置身份，防止 graph 一致但 MoE 不同。
组件收益不能代替完整 workflow 性能或 Host KV 路径的资格证据。

## 第一轮 CPU 与 GPU 入口

`shared_execution_bound_cpu_r1` 通过 6,714 项测试和 23 项子测试，
保留此前 6,620 个 literal 测试身份，绑定 2,433 个源码输入。
源码、原生引擎和外部输入在执行期间保持不变，零失败、错误和 skip。

实现提交为 `f8f9a10c`。操作目录为
`.worktrees/beliefkv-shared-execution-20261101-f8f9a10c`。
原生引擎仍是 `434c35dd66211d0a230717c88bf1452056769a40`。
仅将旧 native CPU 证据的 canonical patch 路径重新绑定到操作目录，
核对补丁字节、实际 tree 及其余状态全部相同。
记录为 `shared_execution_prepare_r1/operator_preparation_r1.json`。

GPU 入口注册：
`ops/workflow_original56_shared_engine_position_20261101_r1.registration.json`。
模型就绪时，实际 BF16、FA3、805,198-token 池、精确 graph 都符合声明；
模型日志也明确从该冻结目录加载了正确 MoE 文件。
随后配置审计拒绝继续：

`ValueError('native GPU worker did not inherit the frozen MoE configuration')`

未提交正式 workload，也未进行 token/session smoke。
该尝试保留 `stopped_for_investigation`，所有 owned 进程已退出，
`owned_process_cleanup.json` 的 `remaining_processes=[]`。
不把它标成已完成资格或性能测量。

## 取证假设修复

旧审计把 `/proc/<pid>/environ` 中缺少变量当成了未继承环境的证据。
固定原生源码在 scheduler 入口调用 `setproctitle`。
CPU 复现中，子进程的 `os.environ` 仍有配置值，
而 `/proc/<pid>/environ` 已不再列出该变量。
复现记录为 `shared_execution_prepare_r1/proc_environment_visibility_reproducer_r1.json`。
它不是已退出 GPU worker 的原始环境快照，不能冒充该快照。

修复后，必须有实际原生 loader 的完整文件路径记录、该文件字节哈希，
以及匹配的实际 server-info。若 `/proc` 仍显示变量，其值也必须正确；
若未显示，则明确记录 `GPU_worker_environment_verified=false`，
不再把不可见性当成错误继承。缺日志、错路径、错哈希和错 graph 仍拒绝。

此修改没有改变 CUDA 图、kernel 参数、模型计算或 agent/KV 策略。
70 项针对性检查通过；随后完整集合重新绑定到修正后的源码。
下一次 GPU 尝试使用新目录，不覆盖上述失败。

## 第二轮资格与实际 Full

修复提交为 `45747eb9 fix: accept native loader evidence when proc environment is hidden`。
`shared_execution_bound_cpu_r2` 在一次执行中通过 6,715 项测试和 23 项子测试，
保留前一轮的所有 literal 测试身份及 2,433 个源码输入；
源码、原生引擎和外部输入在执行期间保持不变，零失败、错误和 skip。
`shared_execution_prepare_r1/operator_preparation_r2.json` 记录干净操作目录与
只改变 canonical patch 路径的原生证据重新绑定。

`workflow_original56_shared_engine_position_20261101_r2` 随后通过新的
24 个精确 token/session/stop 请求，24 次均观察到原生 stop，
并完成新的 1/8/24 采样检查。
实际 BF16/FA3、TP=1、805,198-token Device 池、running24、
chunk2048、精确 1..24 图和冻结 MoE 文件均通过核对。
此处 `GPU_worker_environment_verified=false` 表示 `/proc` 不显示变量；
实际 loader 日志和文件哈希均已核实，不将此字段改写为 true。

Full 注册为
`ops/workflow_original56_shared_engine_full_20261101_r1.registration.json`，
实际执行源码仍冻结为 `45747eb9`，没有热更新。
2026-11-01 09:05 America/Los_Angeles 的现场检查确认
supervisor、server、GPU scheduler、driver 与固定测量组 observer 均存活，
server 已处于实际请求回放阶段。NVML 的 GPU 进程对应本轮 scheduler。
这些是运行状态证据，尚非完整性能结果。

固定 18 个测量成员的预期工作为 2,819 个请求、362,072 个输出 token。
独立 observer 只有在所有测量成员完成后才停止背景工作，
随后校验实际回执、两个新鲜空闲 telemetry snapshot 和 owned 进程退出。
ThunderAgent acting-decay 使用相同执行配置顺序比较；
此前默认引擎的结果不替代新引擎下的对照。

顺序协调器的声明为
`ops/workflow_original56_shared_engine_pair_20261101_r1.plan.json`。
其准备阶段重新核对干净操作源码、6,715 项绑定 CPU 证据、
共同执行配置、成功的 r2 smoke，以及实际 ThunderAgent runtime 构造。
2026-11-01 09:15 America/Los_Angeles 的进程检查确认协调器已存活，
状态为 `waiting_complete_full_fixed18`，尚未注册或启动新的 GPU 臂。
它在 Full 的完整测量、回执、drain 和真实退出均通过后，
重新检查 hold、资源、端口和输入哈希，再直接注册同配置的
ThunderAgent acting-decay。新对照复用共同数据面的成功 r2 资格，
不重复默认引擎的旧对照，也不使用要求全人口完成的旧
`--after-registration` 依赖逻辑。

独立现场取证为
`useful_token_cost_r1/shared_execution_full_live_verified_r1.json`。
它重新核对冻结源码、实际 HTTP server-info、GPU worker 的进程归属、
MoE 文件加载和精确 graph，并校验独立封存前缀中的 **3,204 个完成请求**：
prompt/output 哈希、真实输出序列和 agent/context 身份均与原 trace 相同。
取证时固定测量组完成 **7/18**；这不是完整测量结果。

实际快照确认工作量排序与组合准入关闭，工具预取、逐层 handoff 关闭，
JOIN/EOS、预测工具卸载与需求恢复流水线保留。
旧 Oracle bookkeeping 对象仍存在，其宽泛元数据仍声明
`future_information_used=true`、`deployable=false`；
不将这些字段静默改写为可部署。
相邻的实际决策开关明确为
`workflow_oracle_future_work_used_for_decisions=false`、
`workflow_oracle_remaining_work_ranking_enabled=false`，
工具压力路径也声明不使用未来工具时长。
本次是非排序控制的执行配置验证，不是新的 Oracle 调度收益。

## 旧引擎下的完整请求 Interactivity

`audit_measured_request_interactivity.py` 在完整测量工作、退出和 drain 校验后，
读取固定成员的全部 2,819 个请求。
指标为完整 HTTP 请求秒数除以输出 token 数，再对其 P90 取倒数；
包含 TTFT，零输出请求单独报告为无定义。

| 已完成方法 | 全请求 inverse P90，token/s | 各 workflow 同定义结果的等权平均 | Workflow TTFT P90 |
| --- | ---: | ---: | ---: |
| 默认引擎 retained Full | 16.5341 | 20.1768 | 67.0957 s |
| 默认引擎 ThunderAgent acting-decay | 10.3635 | 14.0341 | 74.3257 s |
| 默认引擎 native SGLang | 9.6808 | 17.8532 | 57.7503 s |

三组 workflow TTFT 均有 18/18 实际流式首输出覆盖，
不以请求提交时间代替首 token。
证据位于 `useful_token_cost_r1/default_full_interactivity_r1.json` 和
`default_thunder_interactivity_r1.json`，
native 补充为 `default_native_interactivity_r1.json`。
Native 的 workflow TTFT P90 更低；Full 的完整请求 interactivity 更高。
不把一个指标的改善说成全部服务指标占优。
这是旧已完成比较的新增分析，不是共同引擎配置的新性能证据；
每种方法只有一次完整运行，不声明统计显著性。

## 完整 Token 成本的共同口径

`805f9113 perf: audit complete-cohort token costs and normalized interactivity`
使成本分析支持 Full 与 ThunderAgent，并在读取 worker 成本前校验固定测量
完成证书、新鲜 drain 和真实进程退出。18 项针对性检查全部通过，
报告为 `useful_token_cost_r1/completed_metrics_cpu_r1.xml`。
运行中的 GPU 冻结源码没有因此改变。

对旧已完成比较的重新核对保留了原 `full_accounting_r1.json`，
另写 `default_full_complete_accounting_r2.json` 与
`default_thunder_complete_accounting_r1.json`，
native 补充为 `default_native_complete_accounting_r1.json`：

| 共同窗口成本 | 默认引擎 Full | 默认引擎 ThunderAgent acting-decay | 默认引擎 native SGLang |
| --- | ---: | ---: | ---: |
| 全部 worker ms/实际输出 token | 3.308463 | 3.407996 | 3.365475 |
| Decode worker ms/decode 输出 token | 2.381707 | 2.429254 | 2.329806 |
| Prefill worker ms/实际 extend 输入 token | 0.102644 | 0.105686 | 0.113102 |
| Decode worker 时间加权有效宽度 | 8.7082 | 8.3410 | 9.5244 |
| 窗口内实际输出，包含背景 | 477,974 | 462,874 | 470,329 |

三组完整测量请求仍均为 2,819 个、362,072 个输出；
窗口内输出量不要求相同，因为它本身就是固定墙钟吞吐的观测结果。
所有已完成请求的真实输出 ordinal 均与回执一致，
同时保留被截断背景请求已经生成的输出。
Worker 包含计算、传输依赖和主机提交，不能把表中的差值直接归因为
kernel 优化，也不能把普通 extend 输入全部称为重算。
新共同引擎两臂结束后使用同一分析程序，不用组件微测代替完整结果。

这组数据也限定了底层优化方向：native 的旧 decode 单 token 成本
低于 Full，并拥有更宽的实际有效 decode batch；
Full 的旧 prefill 单输入成本更低。
这些是不同实际批次/上下文混合下的观测，不构成对某段控制代码的因果归因。
下一步先看共同引擎在完整运行中的成本变化，
不据此立即增加工作量排序、改变工具时长或重开 handoff。

## 新 Full 的固定窗口诊断

完整测量组仍在执行时，原先预注册的共同窗口已经结束。
`e265ff7f perf: audit fixed execution windows while measured workflows finish`
新增 `scripts/audit_common_execution_window.py`，用于核对这个已经闭合的窗口，
不提前生成完整 JCT 或完成证书。

窗口仍为原点后的 `[55.692771, 1638.954850)` 秒，
共 **1,583.262079 秒**，包含所有测量及背景输出。
两组策略环境只相差共同执行配置的两个字段；
实际模型、BF16 权重/KV、FA3、TP、prefill 捕获、Device/Host 池容量均逐项相同。
Worker 成本按 forward 完成归入窗口，覆盖时间按窗口边界精确裁剪。

`useful_token_cost_r1/shared_execution_full_closed_window_r2.json` 记录：

| 固定窗口指标 | 旧 Full | 新共同引擎 Full | 观测变化 |
| --- | ---: | ---: | ---: |
| 总 worker ms/输出 token | 3.308463 | 3.233532 | 降低 2.2648% |
| Decode worker ms/decode 输出 token | 2.381707 | 2.313357 | 降低 2.8698% |
| Prefill worker ms/实际 extend 输入 token | 0.102644 | 0.102394 | 降低 0.2443% |
| 实际输出，按 native forward 完成统计 | 477,974 | 485,988 | 增加 1.6767% |
| 同口径输出 token/s | 301.8919 | 306.9536 | 增加 1.6767% |
| Decode 图填充行/真实行 | 17.4231% | 0 | 填充消除 |
| Decode worker 时间加权有效宽度 | 8.7082 | 8.0506 | 实际批次混合不同 |

不能将最后一行的变化直接归因于某个调度分支；
新旧运行的上下文和实际输出进度也不完全相同。
Prefill 单输入成本接近，当前观测没有支持 prefill 阶段的大幅加速。
3.99% 组件重加权筛查与这里的 2.26% 总 worker 成本变化是不同证据，
不要求二者相等，也不能用其中任何一个替代完整 JCT。

还有一项额外边界：旧 Full 冻结于 `93161529`，新 Full 冻结于 `45747eb9`。
两份注册共有源码中有 **23 个文件哈希不同**，其中 **6 个 runtime/policy 文件**
涉及此前 Oracle/组合准入扩展。即使排序和组合功能在本轮均关闭，
配置相同也不能单独证明实现与控制成本完全相同。
报告明确列出这些路径，并保持
`isolated_engine_only_attribution_verified=false`。
因此本表是配置匹配的观测成本变化，不作为只改变 graph/MoE 的完整 serving 因果实验。
本轮新 Full 与待执行 ThunderAgent 都冻结于同一 `45747eb9`，
其匹配比较仍按原顺序继续。

初次分析因旧注册缺少新增加的 server 元数据字段而被严格拒绝，
未产生 GPU 失败，也没有修改原始测量。
修正后只允许声明过的四项元数据增加，
另从两组实际 `server_info.json` 与 `native_capacity_census.json` 核对资源及执行条件。
缺失/改变 KV 精度、chunk、Host 容量或 prefill 捕获仍拒绝。
相关 **31 项 CPU 检查全部通过**，保存为
`common_window_metrics_cpu_r3.xml`；较早 CPU 报告和 r1 诊断仍保留。
r2 在更晚的 journal 前缀上得到与 r1 相同的窗口成本值。

分析时固定测量成员完成 **12/18**。
报告显式保留 `candidate_complete_measured_execution_verified=false`、
`candidate_complete_receipt_reconciliation_performed=false` 和 `JCT_gain=null`；
已核对 live 输出 ordinal，但未把它升级成完整回执/退出证明。
最终完整成本分析仍由固定测量组完成后的既有路径执行。

## Full 完整测量与 ThunderAgent 交接

Full 已完成固定 18 个测量 workflow。
`workflow_original56_shared_engine_full_20261101_r1/cohort_termination/completion.json`
核对全部 **2,819 个请求、362,072 个输出 token**，
具有新鲜 idle drain、完整测量回执和实际 owned 进程退出证明。
固定测量结果：

| 指标 | 新共同引擎 Full |
| --- | ---: |
| 完整平均 JCT | 964.2904 s |
| P50 JCT | 618.0022 s |
| P95 JCT | 2478.7245 s |
| 最大 JCT | 2810.3379 s |
| 共同窗口全部输出 | 306.9536 token/s |
| 共同窗口 decode 输出 | 304.5042 token/s |

旧 Full 同测量组平均 JCT 为 1052.9946 s，
新 Full 的观测平均值低 8.4240%；仍保留前述六个 runtime/policy 文件变化的归因限制。
这不代表对新共同引擎 ThunderAgent 的收益，后者仍在运行。

Full 的新 workload 审计没有通过：

- 最后测量结束为 4102.1994 s，晚于最后计划到达 842.3408 s。
- 同 session 已服务输入前缀损失代理为 647,065 token，涉及 9 次 probe。
- Host 驱逐回访将 1,019,114 个 FULL token 单位归因为重算。

两项损失统计的语义不同，不能相加；均包含背景人口。
后一个是缓存回访归因计数，尚未逐请求/forward 核对为实际额外计算。
还需定位发生时间、测量/背景归属、Host 重复备份与真实独占状态。
共同窗口 1,529 份供给采样中 569 份有 ready-paused，10 份存在集合竞争下界；
这是采样计数，不是时间加权比例或 Oracle 改变执行的次数。
不能把已通过的供给检查升级成所有 workload 条件均已通过。

原顺序协调器在完整 Full 收尾后因资源守卫停止：
`ValueError('fresh GPU, Host, disk, port or socket is unavailable')`。
现场检查确认 GPU 空闲、所有 Full owned 进程消失、TCP 18963 空闲，
Unix socket `/tmp/bkv-causal-18963.sock` 仅有文件残留，
无监听者。旧协调器状态与日志保留，未删除该 socket。

恢复入口使用重新核对为空闲的 **18964**，只改端口；
实际模型、共同 engine 配置、source、工具时长、到达和固定测量组均相同。
ThunderAgent 注册为
`ops/workflow_original56_shared_engine_thunder_decay_20261101_r1.registration.json`，
代码仍冻结为 `45747eb9`，复用成功 r2 的共同数据面资格，不重复旧默认引擎实验。
`workflow_original56_shared_engine_pair_recovery_20261101_r1.plan.json`
保留原失败并绑定新收尾/比较协调器；它不会登记额外 GPU 后继。

独立 `shared_execution_thunder_live_verified_r1.json` 已核对
191 个完成请求、12,692 个输出 token 和实际 GPU scheduler 4070452。
实际 paper 快照确认 invocation program、5 秒控制、1 秒 acting-decay half-life、
`periodic_resume_only` 和未来信息关闭；
Full 预测卸载、需求恢复流水线、工具预取和逐层 handoff 均关闭。
固定 18 个测量成员 observer 与 CPU 比较恢复协调器均已实际存活。
这仍是 ThunderAgent 前缀证据，尚无匹配的完整方法收益。

## 当前证据边界

已有十二宽度组件比较覆盖旧 decode worker 时间的 86.63%，
静态重加权筛查约为 3.99% 总 worker 时间改善。
Full 的完整测量已结束；匹配 ThunderAgent 的完整 JCT、吞吐比较、
请求 interactivity 和最终逐请求 Host 损失分析仍待继续。
保留原 Full/ThunderAgent/native 比较，不把新 Full 与旧默认引擎 baseline
之间的差值作为相同引擎下的方法收益。

背景人口可扩展性的独立 CPU 准备见
`docs/research/background_tail_prefix_extension_20261101_zh.md`。
该准备没有改变本轮 56 项目对照，也尚未证明改善实际压力。
