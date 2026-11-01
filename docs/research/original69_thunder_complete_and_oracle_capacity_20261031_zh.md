# Original69 完整 ThunderAgent 对照与 Oracle 容量诊断

## 完整对照与测量口径

本轮继续使用已冻结的 69 个不同原项目、12 初始、平均 70 秒到达、
固定 12 warmup / 18 measured / 39 background。
原题、token、工具映射、到达和测量成员未修改。
总体模型工作量 P80/P20 为 3.014931，固定测量组为 3.866985。
此前的 49 项目、P80/P20 4.061859 仍仅为单独的 CPU 选择备选，
没有替换正在运行的流量。

ThunderAgent acting-decay 的固定 18 个成员全部自然结束。
独立 `verify_execution` 复验精确请求与 token、完整尾部、
两份新鲜 idle 快照、收尾证书和所有权进程实际退出。

| 固定测量组或共同窗口指标 | ThunderAgent acting-decay |
| --- | ---: |
| 完整测量 workflow | 18 |
| 精确测量请求 | 2827 |
| 测量输出 token | 390934 |
| 平均完整 JCT | 889.845843 s |
| P50 完整 JCT | 421.193704 s |
| P95 完整 JCT | 2040.038743 s |
| 最大完整 JCT | 2862.526868 s |
| 共同窗口全部输出吞吐 | 278.298208 token/s |
| 共同窗口 decode 输出吞吐 | 275.887363 token/s |

50 个 workflow 自然结束，19 个非测量 workflow 按既定协议右删失；
不能写成全部 69 个完成。JCT 成员按预先到达序号选择，
没有改成最先完成者，也没有截掉最后一个测量成员的尾部。
共同窗口封存的 440619 个全部输出与最终报告精确一致；
436802 个是 decode 输出，保留 prefill 产生的首 token。

证据目录为
`experiments/reports/remaining_original_projects_20261030/` 下：

- `measured_cohort_original69_initial12_mean70_thunder_decay_r1`；
- `gpu_entry_original69_initial12_mean70_thunder_decay_r1/independent_complete_r1.json`；
- 原 run 的 `cohort_termination/{completion,receipts,telemetry_drain}.json`。

这是新 69 项目的对照，不能直接与旧 56 项目 JCT 作策略收益比较。
Predictive 尚未完成，此时没有新两臂性能结论。

## 必须保留的覆盖与重算局限

最后计划到达在 4174.136284 s，最后测量完成在 4283.243019 s，
新到达比测量尾部早结束 109.106735 s。
这不代表已有背景工作当时全部结束，但既定
`background_arrivals_cover_measured_tails` 检查确实未通过。
保留该失败，不把本轮直接推广为稳态 serving 结果。

最终同上下文已服务前缀丢失代理为 137159 token，
Host block 重算为 198062 token。
两种口径存在潜在重叠，不能相加，也不能继续引用早期的零计数。
它们是完整 baseline 的结果；Predictive 是否减少这些成本，
仍需用本轮相同流量的完整报告确认。

## JOIN 结构与实际服务机会

完整源总体有 304 轮 JOIN，其中 81 轮为多成员：
223 singleton、64 pair、11 triple、6 quadruple。
58/69 个 workflow 有多轮 root JOIN。
固定测量组有 71 轮 JOIN，其中 19 轮为多成员，14/18 有多轮 root JOIN。
单个最大 child 的模型工作占比在测量组中位数约 21.51%、最大约 29.83%；
全部 child 合计中位数约 66.91%。两种比例不能混为一谈。
Child 调用数仍有长尾，测量组中位数 20、最大 60，
因此不能宣称已经全部变成极短 child。

在测量完成前的一份严格封存前缀中：

- 已观测到 40 个真正阻塞的多成员 ALL-JOIN；
- 40 个都有至少两个成员的模型请求同时处于已提交、未完成状态；
- 40 个都出现过至少两个不同 sibling 共用实际 decode 批次；
- 其中属于测量组的 15 个 JOIN 均有上述证据，其余未来组未计入。

共批次本身不证明首次服务同时，也不证明本系统的共同准入有独占收益。
进一步对同一前缀的每个 child 首次请求检查：
40 个组在最后一个成员提交时均有至少两个成员尚未获得原生服务；
这批待服务成员首次服务时间差 P50 为 203.979 ms，
P90 417.872 ms、最大 838.997 ms。
所有成员提交到首次原生服务的 P50 为 2337.993 ms，
P90 5188.095 ms，但该延迟包含传输、入口处理、prefill 和排队，
不能单独归因为工作集控制。
首次原生输出也不等于客户端流式 TTFT。

证据：
`gpu_entry_original69_initial12_mean70_thunder_decay_r1/`
`{tail_progress_common_window_r1,join_service_common_frame_r1,join_first_service_common_frame_r1}.json`。
这些证据约束后续归因：不能仅凭 Full 存在 sibling 共同准入逻辑就声称有收益。

## 旧 Oracle 竞争口径的进一步收紧

旧 56 项目资源组合臂的 364 次完整实际枚举，在相同候选、
required JOIN 和记录资源约束下重新分解为：

| 口径 | 次数 |
| --- | ---: |
| 记录资源摘要下没有任何可行子集 | 224 |
| 至少一个可行、未受保护 workflow | 140 |
| 至少两个不同 workflow 各有可行选择 | 39 |
| 不同可行 workflow 无法全部同时选择 | 16 |
| 其中逻辑预算下界本身强制部分 workflow 等待 | 10 |
| 其余物理或联合约束冲突 | 6 |

这 16 次剩余路径最大/最小比的中位数为 10.907。
Full-batch 评分在其中 13 次选中最短可行 workflow。
此前“137/140 命中最短”中的 140 **不是 140 次真实跨 workflow 容量竞争**；
该宽口径不得继续用于支持机会已经充足的结论。
本次只统计记录摘要能支持的局部集合，不重建未记录的原生树。

进一步关联 224 次无可行子集的回退：
193 次发生在周期恢复、31 次发生在普通补位。
193 次周期回退后实际产生 278 个精确 ready 授予，
全部在压力检查后保留资格；232 个在下一控制或同 agent 干预前获得新服务，
231 个在该界限前完成新的有效批次。
232 个界限内新服务延迟 P50 为 294.398 ms。

因此，“当下摘要无空闲空间”和“该逻辑集合以后不能执行”不同；
R5 规划阶段的物理硬门槛可能限制覆盖。
但后续原生服务不证明选择瞬间已有足够空间，
也不证明所有未选替代集合都能安全或高效执行。
本次没有删除分配、锁、Host 副本、ACK 或原生准入检查，
没有把局部诊断冒充 JCT 改善。

证据在 `combination_full_batch_cost_cpu_r1`：
`cross_workflow_competition_refinement.json`、
`resource_refusal_actual_reference_service_bounded.json`。
后一报告明确更正初稿延迟字段：
278 个“随后新服务”包含界限外服务，只有 232 个属于界限内分布。
初稿保留，不能用它的全体分布冒充有界服务分布。

## 已有直接 Oracle 的 CPU 准备

新增 `original69_direct_global_oracle_prepare_r1`，使用已经实现的直接全局
剩余模型/工具路径排序，作为更容易解释的 oracle 诊断。
它不是新增算法，也不是 v5 组合候选。

与当前实际 Predictive 控制的严格配对检查通过：
两个 artifact 只在 `remaining_work_ranking_enabled` 上不同；
同一 69 项目、11441 请求、工具、到达、测量成员、资源、
两秒窗口、500 ms 补位及模型成本系数保持相同。
实际干净 `9ba90576` 构造确认全局排序开启、组合选择关闭、
JOIN 绕过普通窗口、active 连续性保留。
该开关影响已有的工作量恢复、单消费者选择及压力偏好，
没有引入新的抢占或物理权限。

这份准备复用已经资格化的 6451 项服务源码，
新 artifact 的数据绑定与实际构造另行验证。
没有宣称本轮控制或 workload 已完成，也没有登记 oracle GPU 后继。
完整 Full 后续审查决定下一步，v5 的 6467 项候选与干净冻结仍保留。

## Predictive 接续与分析异常边界

顺序协调器实际核验 ThunderAgent 退出后，完成新 24 请求
position/session/stop 及 batch 1/8/24 采样检查，随后启动本轮 Full 控制。
独立 live proof 核验实际 `running_full`、1645 份精确完成收据、
唯一 GPU scheduler 3455975、nice0 所有者及与 ThunderAgent 相同容量。
固定 18 成员观察器、顺序结果协调器均实际存活。
Full 仍关闭工作量排序，保留 JOIN/EOS、预测工具卸载、prepare_host、
agent 连续性和 sibling 策略；工具预取、逐层 handoff 关闭。

一次只读 `tail_progress_common_window_r2` 审计遇到
`completed request has no exact native receipt`，未生成有效报告。
该次失败没有保存具体缺失 ID 或原始读取前缀，不能严格复原其发表时序。
后续另行封存并复验 9507 个 result 与 9507 个精确 receipt 全部对应，
最终完整测量收据、排空及退出也通过；没有据此重启 GPU 实验，
也没有把失败的 live 分析当成性能结果。
边界记录在 `tail_progress_r2_observation_failure_review.json`。

本轮目标仍未完成。完整新 Predictive JCT、吞吐、压力、重算和尾部覆盖
审查完成后，才决定 Oracle GPU 验证或进一步调整负载。
