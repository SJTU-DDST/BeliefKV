# v233 阻塞预算出借的验证记录

日期：2026-10-26。v233 Full 正在重放 v230 完整轨迹，性能结果待定。
冻结 application 为 `c7562c31ec3b71139b4ca9d9f430d51e499e44b2`，
引擎树为 `6437d7deeacc533f0d795ba166dfd5256b3f44b2`。
运行时生效值已读出为 `blocked_lending=backed_demand`，
每秒维护一次，最多八个 donor、每条路径最多 64 节点、最多新增准入一次。
JOIN/EOS 与条件工具预取保留，普通 frontier restore 关闭。

## 为什么继续验证预算出借

v232 完整比较未通过验收：Full 平均 JCT 仅比 ThunderAgent 低 1.73%，
全程输出吞吐低 0.27%。本轮继续使用同一条来源，保留这些结果，
不运行其他 baseline，不改 prompt、工具时长、请求、token 或停止条件。

v232 的请求后继审计覆盖全部 72 个 JOIN 后继和 3474 个工具后继。
Full 的 JOIN native queue-to-first-service 累计为 675.449 秒，
ThunderAgent 为 1537.789 秒；工具后继则分别为 1777.791 和 708.400 秒。
这些请求区间可能重叠、也不一定都在 workflow 关键路径上，
不能相加或除以 workflow 数后宣称 JCT 收益。

Full 最长的两个工具后继队列等待为：

| 工具 | 实际工具轮次 | native 排队至首次服务 |
| --- | ---: | ---: |
| read_file | 5.817ms | 609.656 秒 |
| ls | 4.182ms | 594.053 秒 |

这不是长工具本身造成的等待。进一步将其 server 时间与独立 agent
运行时资格快照对齐，得到第一例全部、第二例约 593.847 秒被归为
逻辑 inactive。该结论限定于快照观察，不能推断所有时间均可消除。

对所有 native 排队超过 5 秒的 Full 请求后继：

| 诊断对象 | 请求数 | 等待累计 | 快照归为 inactive | active | 未覆盖 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 工具后继 | 30 | 1699.112 秒 | 1649.446 秒 | 49.667 秒 | 0 |
| JOIN 后继 | 14 | 674.011 秒 | 666.016 秒 | 7.995 秒 | 0 |

每个快照仅保持到下一快照或最多 1500ms；更长采样间隙与没有 agent
身份字段的记录保留为 unknown。表中没有未覆盖区间，不代表快照之间
不存在短暂状态变化。inactive 证明缺少已观察逻辑资格，不能独立证明
暂停原因、空闲容量、纯 GPU 传输等待或可移除 JCT。
active 区间也可能包含容量、恢复、调度和 CPU 等待。
诊断使用 5 秒阈值筛选，不从完整 JCT 中排除任何请求或 workflow。

原始证据：

- `experiments/reports/v232_full_thunderagent_join_reentries_complete_20261026.json`
- `experiments/reports/v232_full_thunderagent_tool_reentries_complete_20261026.json`
- `experiments/reports/v232_full_tool_successor_eligibility_wait_20261026.json`
- `experiments/reports/v232_full_join_successor_eligibility_wait_20261026.json`

资格区间及后继审计相关回归通过 93 项，报告为
`experiments/reports/v233_agent_admission_wait_audit_cpu_20261026.xml`。
验证包含采样间隙、区间裁剪、未知身份及时间序列错误。
这不是策略性能或 GPU 正确性验证。

## 新增检查的 CPU 成本

基准执行实际出借策略和实际 native CPU radix tree、allocator 的备份
路径观察。graph 阻塞/身份分类作为已验证输入提供；
备份拷贝与事件使用同步替身。初始化、构造策略和拷贝不计入下表，
因此这是新增决策路径的成本，不是整个控制器或真实 GPU 调度停顿。
每个场景有五次预热和一百次测量。

| 路径节点数，含 root | 状态 | 平均 thread CPU |
| --- | --- | ---: |
| 9 | 完整备份，五个 donor 可容纳一个目标 | 0.304ms |
| 33 | 完整备份，五个 donor 可容纳一个目标 | 0.776ms |
| 64 | 完整备份，五个 donor 可容纳一个目标 | 1.388ms |
| 64 | 备份仍在进行，八次检查均拒绝 | 0.101ms |
| 64 | 可验证路径不足，八次检查均拒绝 | 2.217ms |
| 65 | 超过路径上限，八次检查均拒绝 | 1.918ms |

本次所有样本中最大 thread CPU 为 3.638ms。
没有一轮超过八个 donor 检查或新增准入超过一次；
原 running owner 始终保持运行资格。
这些 CPU 测量不建立 live GPU 开销上界，也不证明 JCT 或吞吐收益。

报告：
`experiments/reports/v233_backed_agent_lending_control_cpu_20261026.json`。
基准前后核对了实际 native 引擎树，源码没有改变。
新增脚本和离线审计均在开发目录运行，没有热修改 v233 的冻结执行树。

## 阶段性首次使用观察

`experiments/reports/v233_prefetch_lifecycle_progress_20261026.json.gz`
封存了仍在运行中的独立完整行前缀，不能当作全程统计或完整工作量比较。
该前缀观察到 418 条已 ACK 的 JOIN H2D 命令，合计 66.136GB，
均有真实 allocation 在 materialized service prefix 中复用的证明。
没有观察到它们在首次服务前被原生重新加载。
37 个具有归因与完成边界的 JOIN wait 中，7 个有完成前启动且随后
确认复用的传输，合计 47.306GB；这不意味着所有字节在 JOIN 完成前
已经传完或全部 GPU 等待被隐藏。
工具预取仍没有 H2D 命令。尚未观察到的提交时间保留为 unknown，
不据此单独判断容量拒绝原因。

## 后续验收

v233 还必须完成全部 3674 请求与原始 token、因果事件和停止条件验证，
排空遥测后再比较完整 JCT 与全程吞吐。检查实际 H2D/D2H、旧输入前缀
丢失、重复恢复、资格等待和未使用预取，避免用队列提前或局部 batch
变大替代端到端结果。

性能目标与顺序不变。只有对 ThunderAgent 的明确、可重复收益满足
预先声明的门槛，才继续其他 baseline；否则保留本次结果，依据证据
继续改进。当前没有提前宣称通过目标。

## 完整运行更新

v233 已在 2026-10-26 完成全部 3674 请求与相同 token/停止条件核验，
18 个正常 workflow 和所有长尾保留，遥测排空、所属进程清理完成。
完整平均 JCT 为 1324.729 秒、全程输出吞吐为 272.709 token/s。
相对 v232 ThunderAgent，JCT 改善 1.122%、吞吐提高 3.998%；
相对 v232 Full，JCT 增加 0.623%、吞吐提高 4.283%。
仍未通过双指标与重复性门槛。

decode 平均 batch 从 v232 Full 的 5.035 增至 5.657，
decode worker 区间累计从 1655.770 秒降至 1564.505 秒。
实际 H2D 为 445.022GB，增加的传输与 JCT 必须一并评价。
工具预取 H2D 仍为零；不能把当前吞吐变化归给工具预取。
旧输入前缀丢失代理仍为零。

完整分析与下一步约束见
`docs/research/decode_batch_and_timeout_review_20261026_zh.md`。
本轮没有修改冻结运行树，也没有启动其他 baseline。
