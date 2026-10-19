# 合并 ACK 去除二次扫描与控制降本

日期：2026-10-20（北京时间）。基线 `0c6cbc6`，仍只进行 CPU 工作。
北京时间 06:00 的新 GPU 验证安排保持有效，尚未启动新的 GPU 实验。

## 来自最新完整轨迹的范围

读取 V85d predictive 的原始 `transfer_telemetry.jsonl`，
SHA256 `e39d4fe8aa6375f2dae24821ee0b018fc474c9cd28980303fff6d4aa03be9be0`。
2657 个完成 ACK 全部含有明确的 tagged-child 字段，不能再将同一行的
`node_commits` 副本重复计数。

| 方向 | 全部 ACK | 含受控回执的 ACK | 受控 child 回执 | 单 ACK 最大受控回执 |
| --- | ---: | ---: | ---: | ---: |
| D2H | 2586 | 65 | 386 | 8 |
| H2D | 71 | 40 | 172 | 42 |

本轮没有观测到 tagged split publication；split 仍是必须保留的正确性
路径。H2D 的 172 条回执对应此前已核实的 JOIN 租约；工具源的完整回调
对照是额外兼容控制，不能作为本轮工具预取数量或频率。

上述只有 105 个受控合并 ACK。它们说明批量回执是实际存在的控制对象，
不说明该路径主导整轮运行。没有记录每个 ACK 当时所有在途命令的人口，
不能按这些分布直接计算实际累计节省或 JCT 收益。

## 根因与修改

旧 ledger 对每条回执遍历所有其他在途命令及其 child publication，
反复构造集合检查交集。64 个单 child 命令的一次合并 ACK，CPU profile
中共有 4160 次 set intersection 和 11676 次函数调用。

`register()` 已在登记时拒绝命令之间重叠的 publication，预期记录为
不可变 dataclass/tuple，且现有 `_pending_nodes` 始终保留整个命令生命周期
的预留节点，包括只收到部分 child ACK 的阶段。普通回执完全等于其
原始预留 publication 时，再遍历其他命令不能得到新的冲突信息。

现在复用这个已有保证，普通回执不再执行上述交叉扫描。对 native D2H
split，仍先验证原始 Host 目的索引、节点代际与邻接关系，再用现有全局
预留节点与该命令自己的节点集合检查新增节点是否侵入其他命令。
没有新增 owner 索引、跨调用缓存或另外一份容量账本。

合并 ACK 的节点集合只创建一次，用于逐条 publication 包含关系及最终
核算；重复 credit 直接检查是否相交。遗漏 expected child 的检查使用
现有全局预留集合与「合并节点减已确认节点」比较，保留部分 ACK、过期、
取消和错误清理下的相同判定。context/session、epoch、池字节、总字节、
重复 receipt 和非法 merged node 类型的验证继续执行。

同一 64-command profile 的函数调用降至 3226，普通路径不再调用 set
intersection。它仍逐条验证和完成全部 64 个命令，不能将减少的调用数
解释成少处理了回执。

## CPU 对照与边界

V1981 冻结并重新绑定旧 event_channel、observer、physical 和 runtime，
保留各源码 SHA256。38 组 ledger 对照覆盖 H2D/D2H、1/8/42/64 个命令、
单个或多个 publication 节点、其他在途命令、同命令两个分批 child ACK，
以及 split ACK。有无旁路命令、pending、partial receipt、完整输出、
预留集合、历史和各池预算均相同。

另有 16 组完整 `on_native_transfer_commit`，覆盖 1/8/42/64 条 JOIN/tool
回执及诊断开关。包含服务样本更新、逐节点租约登记、祖先保护合并和
模拟原生锁回执；完整记录、逐条租约、原生节点读取、保护预算、锁获取/
释放及 ledger 状态相同。锁替身要求释放原始 receipt 对象，不仅比较 ID。

每组 151 个样本、10 次预热，新旧次序交替。构造、登记准备、原生数据
已就绪的模拟、重置、状态序列化、比较和 profiler 均在计时外；固定时钟
使用普通函数。V1980 的单样本只用于验证基准可运行，不能当最终计时。

代表性线程 CPU P50，单位 us：

| 路径 | 回执数 | 基线 | 候选 | 降幅 |
| --- | ---: | ---: | ---: | ---: |
| ledger H2D 单 publication | 8 | 96.235 | 67.948 | 29.4% |
| ledger H2D 单 publication | 42 | 810.257 | 276.099 | 65.9% |
| ledger H2D 单 publication | 64 | 1561.827 | 404.947 | 74.1% |
| 完整 JOIN ACK，关闭诊断 | 8 | 385.004 | 348.266 | 9.5% |
| 完整 JOIN ACK，关闭诊断 | 42 | 1948.756 | 1402.615 | 28.0% |
| 完整 tool ACK，关闭诊断 | 42 | 1964.162 | 1411.925 | 28.1% |
| 完整 JOIN ACK，关闭诊断 | 64 | 3327.493 | 2147.557 | 35.5% |

38 组 ledger、16 组完整回调的 P50 均更低。16 组完整回调降幅
2.1%–35.5%；42 条回执下，诊断开关与 JOIN/tool 四组为 26.6%–28.1%。
64 是扩展压力控制，不是 V85d 实际观测的最大 tagged ACK。

writer、native 锁/controller/allocator 都采用 CPU 替身，不含真实 DMA、
writer I/O、模型或 GPU 调度。这不是整轮控制成本、预取覆盖、吞吐或
JCT 的加速结果。后续仍需检查高频常态维护、闭包、恢复消费与准入成本。

## 检查与执行安排

最终主仓 2090 项 CPU 检查通过，无 skip；早期 284 项是重叠检查，不相加。
新增四组检查明确验证：split 新增祖先与其他在途命令冲突时拒绝、不冲突
时保留旁路命令；同命令或跨命令被 merged ACK 遗漏的 child 均拒绝并
清空 pending。原有原始 Host 目的地、回收、split 身份、重复/部分 ACK、
session generation、原生引用和首次服务检查继续通过。

V88 引擎和完整补丁未改：
树 `1db6d8846499beba8f963db9ba8abc7a832ecda8`，
patch SHA256
`2cbb0b4699e000ffc28bd80f6ee70732fb13b4950e40aa303b9afbc7d5a418ef`。
提交后更新同一个等待进程的合格源码与冻结执行目录，保留旧目录；
24-root、16+8/900s、running8、256K、Host80:20、动态嵌套配置保持。
06:00 后依旧检查 GPU 空闲及当前资格再启动，旧 V85d native 不接续。

## 原始记录

- [V1981 最终对照与原始采样](v1981_merged_ack_control_cpu_20261020.json)。
- [V1982 最终 CPU 检查](v1982_merged_ack_cpu_checks_20261020.xml)。
- [V1980 基准检查](v1980_merged_ack_cpu_fixture_check_20261020.json)，非性能结论。
