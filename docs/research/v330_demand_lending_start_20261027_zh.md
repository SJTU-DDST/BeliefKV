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

## V334 运行中机制观察

新增只读 `scripts/audit_collecting_agent_pipeline.py`，复用已有的封存
JSONL、预算对账和 decode 几何统计。V333 的 59 项 CPU 检查通过，
覆盖旧快照未知、采样年龄、预算与身份不一致，以及 token、停止原因
或采样路径变化的拒绝。严格完整运行审计没有放宽。

V334 在 Full 尚未结束时独立核验 1280/3674 个完整回执，全部精确
匹配。预算观察区间 675.657s，覆盖 674.338s，630 个同快照全部
对账。逻辑预算 token·时间中，模型 pending 为 90.594%，已知阻塞
为 5.963%，请求间为 3.443%。阻塞原因分别为 elapsed tool 3.087%、
JOIN 2.018%、predicted long tool 0.857%；有其他已提交但 inactive
请求时的阻塞预算为总 token·时间的 2.987%。

该前缀含 13992 个 decode batch，平均 batch 12.229、worker 区间
加权 12.265。累计 worker 区间 prefill 266.807s、decode 409.715s，
后者区间内输出速率 414.786 token/s；不是全程吞吐、纯 kernel 时间
或完整 workflow JCT，不用作胜出结论。独立文件前缀的终点不同，
不能把区间累计相加后当作精确端到端分解。

这些观察支持继续保留当前冻结策略、完成同计时表的强对照，而不是
在运行中再改变策略。后续快照已记录需求借出和少量实际预测卸载；
旧输入前缀丢失代理量暂为零，Host 到达容量上限并发生回收。
仍须以最终完整审计确认迁移、重算与 JCT/吞吐的共同结果。
