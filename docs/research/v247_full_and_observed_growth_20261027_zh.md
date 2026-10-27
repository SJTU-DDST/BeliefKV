# V247 Full 与运行中上下文计费

V247 Full 已完整结束，ThunderAgent 正在相同冻结 engine 上执行。
当前尚未通过完整 JCT 与全程输出吞吐共同改善的验收，也没有重复实验
证据。以下区分已完成的 GPU 结果和后来独立实现的 CPU 修补。

## 完整工作与结果

V230 原轨迹保持不变：18 个不同项目，3674 个请求，145565966 个输入
token、549671 个输出 token；72 次 JOIN，包含 20 次双 child JOIN。
保留全部正常长尾、180 秒工具及所有 root 的最终委派提醒。运行上限
与 decode 上限均为 24，seed21；Device 为 805198 token，Host 为
200000077824 字节。未重新运行完整 native。

Full 使用 `thunderagent_retained`、`backed_demand_recall`、`cold_fit`，
普通 execution-frontier restore 与工具 loan-return 均关闭。两个 arm
共同启用 free-page fence，engine tree 为
`11b84f7c31986782be3fbe699bae2574ef092389`。

| 完整 workflow 指标 | V247 Full | 原自然 native |
| --- | ---: | ---: |
| 平均 JCT，秒 | 1423.068672 | 1750.365344 |
| 全程输出 token/s | 286.592505 | 261.680193 |

相对原自然 native，平均 JCT 降低 18.6988%，全程吞吐提高 9.5201%。
完整 workflow makespan 为 1917.953161 秒。这不是与本轮共同 engine
ThunderAgent 的最终比较；不能使用历史 ThunderAgent 代替正在执行的
共同开关对照。

原生停止条件、逐请求准确 token 和完整请求集合均通过。结束后出现
两个新发布的零 pending / 零 writer queue 快照；没有 dropped、
failed 或 writer error，旧 Full 的 owned cleanup 无剩余活跃进程。
证据保存在本轮 `full/replay/summary.json`、`full/telemetry_drain.json`、
`full/owned_process_cleanup.json` 和 `causal_comparison.json`。

## 迁移与执行观察

十进制 GB，按实际 ACK 核对：

| 项目 | V247 Full |
| --- | ---: |
| 全部 H2D | 733.581902 |
| tagged JOIN 恢复 | 126.934843 |
| native demand H2D | 606.647058 |
| 全部 D2H | 259.676307 |
| JOIN 后继的需求 Host-hit 关联量 | 402.362106 |
| 工具后继的需求 Host-hit 关联量 | 182.591521 |

native demand H2D 与全部请求 Host-hit token 乘实际每 token 98304 字节
精确相等。工具后继中 53 个请求有 Host hit，其中 37 个工具轮次不超过
200ms，关联 149.744910 GB。557.026050 GB 需求 Host hit 位于上次请求
完成后曾采样为 inactive 的区间；这不证明某次暂停直接造成对应 DMA。
旧输入前缀丢失代理量为零，不能据此认定所有初始 prefill 都是重算。

799 条 JOIN 节点命令中，798 条有首次 FULL 复用证据，合计
126.927864 GB；另有 6.98 MB 的 v2 身份检查不成立，不能据此断言物理
KV 未被使用。命令数量不是独立 JOIN 次数。66 个有 wait 观察的 JOIN
中，7 个存在完成前启动且确认首次复用的恢复，合计 22.825009 GB；
其余受控恢复不能全部计为完成前预取，也不证明全部传输时间被隐藏。
工具预取实际发行零条，普通 execution handoff 也为零。

16 次实际 JOIN 逻辑归还中，11 次只有一个合法候选；5 次存在多候选，
`cold_fit` 均改变了相对最大候选优先的单对象选择。选中对象分别处于
真实阻塞或两次请求之间，未选 pending/ready 档。逻辑预算不等于释放
的物理页，不能将多归还的预算直接换算成节省的 H2D。

实际平均 decode batch 为 6.898321，worker 时间加权宽度为 8.404342，
decode worker 区间累计 1491.721819 秒。free-page fence 实际执行
1192 次 boundary event，48 个 load batch 使用提前事件，127 个保留
原 forward fence。事件计数和 worker 区间不直接表示节省的 GPU/JCT。

完整审计：

- `experiments/reports/v255_v247_full_decode_complete_20261027.json`
- `experiments/reports/v255_v247_full_restore_churn_complete_20261027.json`
- `experiments/reports/v255_v247_full_prefetch_lifecycle_complete_20261027.json`
- `experiments/reports/v255_v247_full_jct_paths_complete_20261027.json`

## JCT 回撤的观察边界

历史 V235 Full 的平均 JCT 为 1344.921974 秒，比本轮少约 78.147 秒。
完整实际路径分解中，提交至首次 launch 的平均贡献从 443.912096 秒
增加至 519.360459 秒，差约 75.448 秒；首次 launch 至 native complete
从 821.286481 秒增加至 824.006012 秒，差约 2.720 秒。

这说明本次观察到的回撤主要落在准入/排队区间，不能只继续优化 PCIe
拷贝。后一区间包含交错服务及等待，并非纯 GPU 计算。两轮同时改变
了归还选择与共同 fence，且调度时序会改变后续到达，因此这不是
`cold_fit` 或 fence 的独立因果归因。完整共同对照仍在执行。

## 独立 CPU 计费修补

代码核对发现，论文 ThunderAgent adapter 在新 agent 到达和五秒控制
点读取实际生成进度，Full agent adapter 原来主要在请求注册/完成时
更新上下文。较长 decode 过程中，Full 的逻辑容量可能低估已经生成
的 KV；这是一项实际观测缺口，尚未证明它导致上述 JCT 回撤。

`AgentAdmission` 现在维护 native request 引用，在新 agent 到达及
每五秒的维护点读取 `len(origin_input_ids) + len(output_ids)`，交给
已有 `generated()` 计费。只检查当前有效身份下 active、pending、
非 acting 的请求。完成、退休、取消、重排队和关闭维护对应引用；
过期 attempt 不提供新进度。原 `working_set` 保留请求边界计费，
ThunderAgent 论文 adapter 没有修改。

该更新不读取最终 trace 输出长度，不增加逐 decode 步队列扫描，不
新增资格、驱逐或物理分配权限。运行中的 V247 冻结应用完全不含这项
修补。它仍是定期观察，不能替代 native 实时容量和 allocation 保护，
也不是逐层容量复用；后者仍须遵守显式 A 到 B 交接约束。

定向报告 `v256_observed_growth_targeted_cpu_20261027.xml` 通过 136 项。
相关策略及 native 生命周期报告
`v256_observed_growth_native_policies_cpu_20261027.xml` 通过 248 项，
无失败或跳过；native cache/allocator 使用真实实现，GPU copy/event
仍是 CPU 替身。它不是全部共享回归或新候选的 GPU 性能资格。

`scripts/benchmark_agent_observed_growth_cpu.py` 使用实际 adapter/policy，
每档预热五次、测量一千次。1/24/128 个 live 请求的刷新平均 thread
CPU 成本约为 0.001809/0.022151/0.114105ms。来源与完整分布在
`experiments/reports/v256_agent_observed_growth_control_cpu_20261027.json`。
测量只覆盖刷新 callback；不包含真实 forward、拷贝、全部控制器或
新增 agent 的构造，也不能换算成 JCT 加速。

截至北京时间 2026-10-27 12:13，监督进程 3884554、ThunderAgent
scheduler 3904251 和 driver 3905991 已核验存活。实际配置确认同容量、
seed21、running24、chunk4096 和 session cache；论文策略未继承 Full
预取、prepare_host 或 agent admission。继续完成本轮共同对照，再
根据完整结果决定下一候选；其他 baseline 的性能启动仍受原收益门槛
约束。
