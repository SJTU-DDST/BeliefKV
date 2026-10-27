# V330 需求借出与 TraceLab 工具先验

本轮独立注册：
`/home/longhao/experiment/ops/v330_locobench_demand_lending_20261027.registration.json`。
执行 commit 为 `bdd74e9c280bcedcf322aa5e00a78be8aaa1a2e6`，
先运行 Full，完成后运行 `thunderagent_acting_decay`。
首次观察时 supervisor 4170080、server 4170626、GPU scheduler
4171097 均实际存活；当时仍在模型初始化，尚无完整性能结果。

## 改动

Full 使用 `predictive_demand_lending_20261027.json`：
在真实已提交计算需求下，实际 JOIN、child 或长工具阻塞的 agent
可让出逻辑资格，不再把完整 Host 检查点当作逻辑资格释放的必要条件。
context、epoch、native session、在途计算和恢复保护继续验证；
原生分配、备份 ACK、锁及需求恢复仍决定物理容量。
不能把该逻辑交接称作已经释放 HBM。

“parent 在 JOIN_WAIT”是依赖事实，不应被删除；需要修复的是有待执行
计算时，它仍占满逻辑准入预算的情况。后台 SPAWN 后 parent 仍可执行
时不视为冷 donor；parent 实际等待 children 后才可以让位。
先前 V286 61.7% 的统计是工具与 JOIN 共同占用的逻辑预算 token·时间，
不是该时段 parent 仍有同等数量 KV 驻留在 HBM 的证明。

JOIN/EOS、工具预取、动态成本工具卸载、prepare_host、短工具连续性
和容量内 sibling 准入保留。预测弃权不再否决逻辑需求回退，但也不
授权 speculative 物理动作或驱逐 ready peer。

## TraceLab 训练与评测

固定 TraceLab v0.0.1 的 `syfi_coding_trace.jsonl.gz`，只使用有实际
`tool_internal_latency_ms` 的 WebFetch/WebSearch 调用。缺时长记录
保留在审计里，不用相邻记录间隔替代工具时长。

按原始 session 哈希划分，54 个 session、222 次调用拟合类别剩余
时长生存分布，17 个独立 session、91 次调用用于合成评测。
不是在同一批时长上拟合后再抽样评测。在线只加入当前 arm 已完成
的观测，64 次滑动窗口、八个等效离线先验样本。

V331 从原始压缩数据重新执行拟合，先验 SHA256 与实际加载的 V311
完全相同：
`949e2b45b7f6c31c39ac4ff497a2a8bd9bcc6c1fdd055a3a94cc847bbc0835da`。
V331 是 CPU 重现证据，没有替换正在运行的输入文件。
类别内实际时长仍随机抽样，不能把分布预测当成对单次未来抽样的预知。

两组同用 V311 可观察类别时间表，类别在 TOOL_START 提供，抽样结果
与未来结束时刻不交给策略。18 个不同项目、3674 次请求、145565966
输入 token、549671 输出 token、72 JOIN、全部依赖和停止规则不变。
新时间表与 V286/V295 的 V269 不同，不能把旧性能作为匹配对照。

## 验证与封装

V325 一次执行通过 2916 项及四个 subtest，无失败或跳过，保留 V321
全部 2890 项。V326 绑定 1740 个源码、配置及构造检查；Full 加载
`observed_demand + demand_cost` 和 TraceLab 先验，两个 ThunderAgent
arm 不继承这些扩展。Native CPU 复制、事件和 Host 索引仍有替身。

V327 在 GPU 前发现 main 的补丁相对旧 native 基线；重新生成相对
fa5cb52afaea 的补丁后，V328 证明实际树与历史合格树完全相同：
`11b84f7c31986782be3fbe699bae2574ef092389`。
V329 在 GPU 前拒绝将改变了计时与策略的新实验接为 V295 的匹配
延续；保留其 preflight worktree。V330 独立注册，检查实际资源空闲，
不绕过匹配检查或复用旧结果。

完整两组结束前，不接受早期吞吐或较少迁移作为胜出证据。需要检查
精确工作量、DMA 及首次使用、逻辑与真实容量、旧输入重算、控制开销、
完整平均 workflow JCT 和全程吞吐，保留全部正常尾部与负结果。
两项指标获得明确且可重复的改善前，不推进其余 baseline 或消融。

后续已确认进入 `running_full`，driver 4172595 实际存活。运行快照
确认 `physical_disabled=false`、`observed_demand`、无需完整 Host
逻辑证明、零物理容量 credit 和固定 SHA 的 TraceLab 先验。
04:05 America/Los_Angeles 的早期快照记录十次 donor 证明、七次实际
需求借出准入、八个 donor 资格切换；不是独立 agent 或已释放页数。
遥测 `writer_error=null`、`failed_records=0`。

V332 独立封存 748311 字节的回执前缀，其中 228 个完整请求的输入/
输出 token 数、SHA256、原生停止原因均与历史完整原始工作量一致，
全部使用原生 position sampling。使用旧回执只验证工作相同，
不比较两个不同计时表的性能。这不证明剩余请求正确、完整模型数值
等价或最终 JCT/吞吐改善。
