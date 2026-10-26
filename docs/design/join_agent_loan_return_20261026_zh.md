# JOIN 解锁后的 Agent 预算归还

本候选在 v233 的已备份阻塞预算出借上增加 JOIN 归还关系。
配置为 `configs/migration/predictive_join_loan_return_20261026.json`，
唯一运行参数差异是
`AGENT_BLOCKED_LENDING=backed_demand_recall`。
原有 idle、backed_demand 和论文 ThunderAgent 保持各自的历史行为。

## 已完成来源与问题证据

v233 执行完整 v230 工作量后，吞吐比 ThunderAgent 高约 4.0%，
平均 JCT 仅改善约 1.1%。72 个 JOIN 后继 native 排队累计为
3414.865 秒，而 v232 Full 为 675.449 秒。
这些请求区间可能重叠，不能相加当作可移除的 workflow JCT。

v233 排队超过 5 秒的 28 个 JOIN 后继累计等待 3398.870 秒。
最多保持 1500ms 的资格快照将其中 3385.608 秒归为 inactive，
约 99.61%；active 为 13.141 秒，未覆盖约 0.122 秒。
最长两个等待为 664.174 和 652.668 秒，快照均归为 inactive。
这不证明所有延迟均由出借造成，也不证明当时物理容量可用。

旧策略可以从阻塞 parent 出借预算给后继计算，但没有记录归还关系。
JOIN 解锁时，这个 parent 仍会与已经保留资格的借用者竞争。
新候选验证这项机制缺口，不据此提前宣称性能收益。

证据：

- `experiments/reports/v234_v233_thunderagent_join_reentries_complete_20261026.json`
- `experiments/reports/v234_v233_join_eligibility_wait_complete_20261026.json`
- `experiments/reports/v234_v233_thunderagent_causal_paths_complete_20261026.json`

## 借用与归还

沿用 v233 的出借前提：真实 pending 目标、非 running/nonpending 的
真实阻塞 owner、完整且已 ACK 的原生 Host 备份，以及原生物理安全检查。
出借仍在每秒维护点最多新增一个准入，最多八个 donor 证明、
每条原生路径最多 64 节点。出借本身不创建 D2H。

一次出借开启该 owner 的借用期。借用期内新准入的 agent 获得有条件
连续执行资格；在出借前已经活跃的无关 ready agent 不加入。
这里记录的是逻辑资格关系，不是物理 KV 页所有权或逐 token 信贷。
同一个 agent 可以受多个尚未恢复 owner 的借用条件约束，
实际容量仍由唯一的 active 状态求和，只计一次。

借用者暂停后释放自己的关系，但尚未恢复 owner 的借用期保持开放；
之后使用释放预算的新准入仍继承这个条件。再次向下出借时保留上游
条件，避免预算经过另一个阻塞点后丢失恢复关联。
owner 正常恢复或终止后关闭借用期，借用者可继续使用普通保留资格。

仅当 owner 的真实 JOIN 后继请求已经提交、尚未取得 active 资格，
且普通空余预算不足时，才检查归还。
EOS 或 speculative restore 单独到达不触发召回。
候选仅包括该借用期中的非 running、非 critical 且无未消费恢复保护
的借用者；原 running 请求不中断，未完成的分块 prefill 也不打断。

归还前先核对全部可归还预算是否足以让 parent 准入。
不足则不暂停部分借用者，继续现有计算，等下一个自然请求完成边界。
足够时归还必要资格并激活 parent；后续 HBM 分配、Host 备份、
H2D/D2H 和原生锁/DMA 依赖仍使用已有路径。
普通工具预取不因此获得驱逐 ready agent 的权限。

这项策略可能在真实 JOIN 恢复时增加必要的切换和后续 H2D，
必须通过完整工作量同时评价 JCT、吞吐和重算，不能只评价父请求排队。

## 验证范围

共享回归通过 1465 项，无跳过，保留 v233 全部 1396 项。
包括真实 Docker 工具 fixture、实际原生 FULL 树和 allocator 的 CPU
检查，以及借用传递、无环身份关系、运行中保护、未消费恢复保护、
预算不足不部分暂停、空余预算优先、终止清理和 baseline 隔离。
原生拷贝/事件在 CPU fixture 中仍使用替身。

报告：
`experiments/reports/v235_join_loan_return_shared_cpu_20261026.xml`。
三份早期 targeted 报告保留，包括旧测试预期、状态方法名和缺少
CUDA_HOME 的设置错误；未将跳过或初始化失败当作通过。

增量逻辑路径每个场景预热五轮、测量两百轮。
1/4/16/64 个可归还 agent 时，平均 thread CPU 为
0.0167/0.0215/0.0366/0.0897ms；全部仍在运行而拒绝召回时为
0.0112/0.0123/0.0166/0.0298ms。
构造 fixture、原生分配和拷贝不在此范围内，
不是 live GPU 控制开销上界或性能收益证明。
报告为 `experiments/reports/v235_join_loan_return_control_cpu_20261026.json`。

下一轮只运行相同 v230 来源上的 Full，不重放完整 native，
不改变模型、种子、依赖、token、工具时长或 Host/Device 容量，
不加入其他 baseline。必须保留所有正常长尾与失败比较，
检查最终借用关系释放、首次真实消费、重复 H2D、旧输入重算及端到端指标。

## 执行登记

v235 已于 2026-10-26 注册并开始 Full 重放，端口 18646，
冻结 application 为 `f26f1998ae5f7e1526798a4cacff5379248eff3d`，
engine tree 为 `6437d7deeacc533f0d795ba166dfd5256b3f44b2`。
注册文件：
`/home/longhao/experiment/ops/v235_locobench_join_loan_return_20261026.registration.json`。
早期运行快照确认新策略生效，physical_disabled=false；
不能据此认定完成工作量、数值正确性或性能验收通过。

engine 证明仅因旧 canonical patch 绝对路径属于旧 prepared application
而未通过第一次预检。核对其余 engine state 字段、patch 内容和
canonical tree 完全相同后，重新绑定到新 prepared 路径。
保留原预检失败与
`experiments/reports/v235_engine_evidence_rebinding_20261026.json`，
没有放宽源代码或 CPU 检查。

## 完整结果

v235 已完整执行 3674 个请求、145565966 输入 token、549671 输出 token，
所有停止条件匹配，18 个 workflow 的普通长尾全部保留。
遥测排空、所属进程退出；性能门槛未通过。

| 指标 | ThunderAgent v232 | Full v233 | Full v235 |
| --- | ---: | ---: | ---: |
| 平均完整 JCT，秒 | 1339.764 | 1324.729 | 1344.922 |
| 全程输出吞吐，token/s | 262.225 | 272.709 | 280.631 |
| 实际平均 decode batch | 4.932 | 5.657 | 6.418 |
| 按 worker 区间加权的 decode batch | 6.057 | 7.331 | 8.369 |
| decode worker 区间累计，秒 | 1652.569 | 1564.505 | 1518.026 |
| 实际 H2D，GB | 216.496 | 445.022 | 647.027 |
| JOIN 后继 native 排队累计，秒 | 1537.789 | 3414.865 | 1361.661 |

v235 有 14 次真实 JOIN 召回、14 次借用者暂停，最终借用关系为空。
相对 ThunderAgent 吞吐提高 7.019%，平均 JCT 反而增加 0.385%；
不能将更宽 batch 或更短 JOIN 排队单独作为成功标准。
平均每个 workflow 的关键路径 queue-to-first-launch 从 634.203 秒降至
443.912 秒，但 launch-to-native-complete 从 626.117 秒增至 821.286 秒。
这些执行跨度包含交错调度与等待，不能当作纯 GPU 计算。

完整逻辑预算审计中，v235 对齐快照的 pending 模型工作占 70.98%，
已知阻塞占 26.59%，两请求间隙占 2.43%。约 3--5% 时间无法精确
对齐，保留为缺口；这些比例不是 HBM 独占页或可驱逐冷页比例。

证据：

- `experiments/reports/v235_full_thunderagent_decode_complete_20261026.json`
- `experiments/reports/v235_full_thunderagent_causal_paths_complete_20261026.json`
- `experiments/reports/v235_full_join_eligibility_wait_complete_20261026.json`
- `experiments/reports/v235_agent_loan_state_complete_20261026.json`
- `experiments/reports/v235_full_agent_budget_composition_complete_20261026.json`
