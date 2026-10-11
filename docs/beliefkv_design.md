# BeliefKV 当前系统设计

更新日期：2026-10-11

状态：本文是当前算法与系统边界的权威说明。历史版本保存在
`docs/archive/snapshots/beliefkv_design_2026-07-14_zh.md`。

## 1. 研究场景与目标

BeliefKV 面向单 GPU、HBM 受限的动态 Agent 工作流：

- 多个 root workflow 并发运行；
- workflow 在运行时产生工具调用、FRESH subagent、RETURN、JOIN 和 peer message；
- 系统不要求应用预先提供完整 DAG；
- GPU KV、CPU KV 和 raw-token recompute 可以共同参与容量管理；
- SGLang RadixCache/HiCache 仍是物理 KV 与 allocator 的唯一事实源。

主要优化目标是**相同到达流下的成功 workflow 吞吐及 JCT 分布**；
任务正确性、无界饥饿和物理容量安全是不允许交换掉的约束。
GPU service utilization、action throughput、action unlock、reentry/admission
stall、D2H/H2D、recompute 与控制面开销是用于解释因果和判断退化的指标，
不能仅凭 GPU 利用率或预取次数宣称性能收益。

Workflow fairness 只作为有界防饿死和最终 tie-break，不以平均分配 GPU 时间为目标。

### 1.1 当前阶段目标：相对 native 的可验证性能提升

**当前主目标：**在固定 workload、模型、容量及到达流下，通过
agent 调度、共用路径减负、predictive H2D 与 `PREPARE_HOST`，
使 predictive 相对 native 取得可核实的完成吞吐与 JCT 改善。
当前 active /goal 保持该方向，以下三项是同一目标的执行要求：

1. 缩短恢复就绪到首次服务的等待，减少服务前重复加载；同时核对
   缺失页、必要状态、准入等待及其他 workflow 的延迟。
2. 补齐 PREPARE 消费归因，区分压力释放、恢复、Host 驱逐后补传
   与未观察到消费，减少无效备份和反复回收/补传的成本。
3. 提高有用 FULL 预取覆盖，验证 handoff 能否替代需求恢复，
   并以实际首次复用、吞吐、JCT 与重算评价收益。

开发迭代允许在重要优化已提交后主动结束旧采集，并用新目录
冷启动最新 predictive。旧计划与 trace 保留，未结束任务属于
截断观测；只有稳定候选的完整同版本 native 对照用于收益验收。
现有部署脚本的 --development-restart 支持这一流程，旧轮无需
完整 summary/后处理，但部署仍在旧执行进程退出之后进行。

V96 优化同步首次复用证据路径：原生 token 数组直接比较并
定位分歧，列表输入保留原有共享前缀遍历，ACK 时仍形成独立
快照，FULL/Mamba 物理身份证据不变。85 项检查及 48 组 CPU
对照确认记录一致；32K 单 extent 的匹配均值从 1.4742 降至
0.0561 ms。保留列表 8K/16 extent 的 0.7728 至 0.8256 ms
代价和先前列表转换退化证据，不外推 GPU/JCT。
该修改已提交 741d9d4，准备通过 V26 冷启动验证；冻结 V25
不在运行途中更新。ACK 快照展开仍是后续独立成本。

V25 当前分文件审计确认完成边界前启动且复用的 FULL 为
工具 6.922 GB/418 事件、JOIN 274.125 MB/26 事件；
ACK 到首次服务 P90 为约 953/2792 ms。59 个 JOIN 边界
未知，需求 handoff 与原生残余 Host 命中分别报告。继续
降低服务控制成本、JOIN 服务长尾并验证更多有效提前复用，
不能从这些局部结果宣布相对 native 的性能目标完成。
V25 live GIL 采样的 21:48:26-21:48:52 CST 从主性能窗口
排除，371 有效样本与 120 读取错误一并保留。

V24 已在开发阶段主动结束：132 completed、24 主动截断，
未运行 native；旧计划、trace 和诊断窗口保留。V25 于
21:28:52 CST 冷启动冻结 a770180，包含状态发布减负、
prefill 工作量修正与开发重启支持，保持原配置，先 predictive
再同版本冷 native。启动遗留的失效 Unix socket 已自动清理，
21:31:26 CST 健康检查通过，21:32:04 CST 起已执行真实请求。
21:34:22 CST 的启动核对确认 1058 个 prefill batch、1412 条
原生工作量样本，writer_error=null；工作量修正已在实际服务中
生效，尚不证明性能改善。主工作区
v25_launch_after_v24_20261011.json 为权威状态，当前
running_new_pair；CPU 效果与实际服务收益分开验收，主性能
目标仍 active。以下历史 V24 观测保留为开发诊断。

控制成本须包括后台状态发布，不能只依赖逐记录 writer 和
scheduler 的既有计时。V24 实际 GIL 栈采样确认 `_write_status`
有重复扫描待归因驱逐记录的成本；后续版本由单一 writer 在
索引增删和替换时维护分池数量及最新单位累计值，发布及结束
汇总不遍历记录，不增加锁、丢弃观测或缩小归因范围。84 项
检查通过；32768 项 CPU 夹具中发布均值降至 0.132 ms，匹配
更新路径约增加 6%，完整 GPU/JCT 净收益仍待同版本实验证明。
旧 V24 全程保持原代码，诊断采样重叠窗口从主性能比较中排除。

V24 完整 600-2400 秒窗口相对旧 V23 native 的 decode 吞吐
高 5.07%，但 prefill worker 区间仍为 335.226 秒，已埋点独占
墙钟仍为 221.872 秒。版本和运行需求不同，不能替代同版本
native 对照；6.788 GB 预测 FULL 传输同样不能替代提前复用
证据。下一轮同时核对实际 prefill 工作量、后台成本和恢复消费。

prefill 成本比较需依据原生实际分块工作量。旧遥测读取已失效的
`req.extend_input_len` 并默认零，不能将其解释为没有计算；
后续隔离版本改读 CPU 批次 `prefix_lens`/`extend_lens`，
备用为请求 `extend_range`，缺失保留未知。分析只有全批各行
均具备原生长度才按工作量和前缀建立分组，不补造历史数据。
99 项相关检查通过，不增加设备同步；冻结 V24 保持原采集。
测量修正不代表性能优化，继续以同版本 GPU 与 JCT 结果验收。

当前工具等待卸载窗口为 1 秒，PREPARE 和等待页回收共用判断，
以实际释放容量和后续恢复消费验证效果。V24 冻结为 56ff3c2，
包含恢复容量扫描修正及主分支月报提交，已于 19:38:08 CST
在完整 V23 对照及后处理结束后部署，先 predictive 后冷 native；以主工作区
v24_launch_after_v23_20261011.json 为权威登记。
用户汇报与运行代码分别核验，采集修订和当前核验修订单独记录；
未跟踪报告无需移动或忽略，不改变模型、运行代码或规范 patch。
时间候选覆盖增加尚不证明相对原生 LRU 的收益，仍须比较实际
卸载、提前 FULL 复用、繁忙窗口吞吐/JCT 和重算。

V24 实际动作前缀已确认工具等待节点释放 59 次、0.732 GB FULL，
涉及 36 个工具，首次释放后仍等至少 1 秒为 32/36。工具 PREPARE
的 9.122 GB FULL 必须单列，它只是 Host 备份；不能将备份字节
等同 HBM 释放，或把节点事件等同完整 agent 卸载。已知结束边界
的独立工具首次备份资格为 116/140，未关闭边界保持未知。
该时长判据的精确率不证明恢复复用、吞吐或相对 LRU 的净收益。

V24 已于 19:38:08 CST 部署该冻结版本，开始真实 predictive
任务，运行期工具 forecast 已确认 1000 ms 卸载窗口；结束后
仍按同版本冷 native 对照。V23 全部结束，共同成功的 154 个
任务中平均 JCT 少 311.133 秒，但有 68 个任务更慢，且执行需求
不同；600-2400 秒繁忙 decode 吞吐仅高 1.23%，prefill worker
区间仍多 59.684 秒。配对 JCT 需同时报告失败差异和实现出的
请求/工具数量，不能将同项目任务当作相同轨迹。完整目标保持，
继续核对 V24 的服务成本、提前 FULL 复用及冷 native 对照。

V24 600-900 秒窗口已观察到 1152.66 decode token/s、88.30%
GPU 利用率、47.351 加权 batch 和 1.428 GB 预测 FULL 传输。
这些字节尚不代表完成边界前启动并由下一请求复用。已埋点
Python 独占墙钟仍为 40.760 秒，prefill worker 区间为 50.639 秒；
控制成本相对 V23 predictive 的 40.985 秒尚无明显下降。新版本
逐批 graph 标记均为 true，46/47 请求的较长 worker 区间仍
不能直接归因于未回放。旧 V23 native 的跨版本吞吐参照高约
8.49%，尚无 V24 同版本 native 结果。保持版本与实现需求差异，
不把候选增多、ACK 或局部 CPU 减负当作完整目标完成。

控制成本分析还可使用现有服务日志的周期 graph 状态，独立于
逐 batch 审计。V23 600-900 秒的两侧 180/197 个日志样本均为
true，仍有相近上下文的 worker 区间差距；未 capture 46/47
请求的专用 bucket 不能作为未回放 graph 的结论。周期样本
已由启动配置确认每 40 步记录一次，不外推到全部批次，也不
回填旧审计的未知字段。V24 共用路径减负已进入实际运行，
仍须由真实 GPU 对照确认，继续保持采集配置。
磁盘维护重叠窗口仅用于诊断；维护后的晚期 batch 占用差异
同样不单独证明预取收益或退化，保留两侧完整结果。

恢复目标自身的节点必须在 FULL 叶和 Mamba LRU 候选扫描的
八节点额度之前排除，避免受保护节点占满额度而遗漏后面的
安全冷副本。保留原生 Host 副本、引用、锁、在途依赖与实际
allocator 空闲量检查；Mamba 单池回收仍保留 FULL。四个真实
hybrid allocator 场景在修复前失败，修复后共 95 项相关检查
通过，没有新增 D2H；它们证明容量选择的修正，不证明 GPU
收益或所有容量不足事件的原因。V23 继续使用冻结版本。

新增同版本 1800-2100 秒窗口 predictive decode 吞吐高约
9.32%，H2D 为 28.065/204.488 GB；紧接着 2100-2400 秒
却低约 1.97%。可见上下文及 batch 相近，仍有不同执行需求，
不能将有利窗口或减少的迁移量直接作为隔离的预测策略收益。
完整 native 对照和主性能目标尚未完成。

驱逐归因的后续排序视图在索引增删时维护，减少每个请求对
同一 namespace 长度集合的过滤和排序；受限 probe 保留原
驱逐最近顺序，完整前缀哈希和归因范围不变。大索引 CPU
夹具的非匹配/部分匹配查询显著减负，但插入与过期增加约
8-9 微秒，实际负载与 GPU 收益待测。同版本 3600-3900 秒
窗口吞吐几乎相同，H2D 两侧均不足 0.15 GB；不能用平均
worker 区间较短替代迁移收益或宣称主目标达标。

工具卸载的后续规则已从 2 秒降到 1 秒：未来 1 秒内结束概率
不超过 10%，无 CDF 时残余 P10 至少为 1 秒。一次工具预取
扫描内复用未发生回收的容量预算；后续 CDF 查询用二分定位
已排序时间结点，保持插值和概率输出。32 条真实预测的查询
夹具中，完整剩余时间查询线程 CPU 降低 14.27%，不是预测
精度或 GPU 性能提升证据。上述修改已进入 V24，V23 全程
保持冻结。V23 同版本 2700-3000 秒曾观察到 predictive 吞吐
低约 4.01%，完整性能目标尚未达到。

V23 同版本 native 已覆盖 600-900 和 900-1200 秒窗口。
predictive decode 吞吐分别低约 8.6%/2.2%，首窗口时间加权
batch 几乎相同，可见上下文也相近；完整 pair 尚未结束。
该不利证据必须保留，不能再用跨版本窗口改善代替同版本收益。
46/47 请求的较长 worker 区间尚未归因；原生允许 padding 到
48 回放 graph，不能仅凭 batch 大小认定 eager。后续共用服务
遥测保存引擎完成结果的 `cuda_graph_replayed`，旧 trace 保持未知；
即使回放，worker 区间也不等于纯 kernel 时间。后续 reentry
优化仅复用一次读取中已失败的共享前缀，并减少重复属性读取；
不跨调用持有输入/驻留状态。27 类 CPU 夹具返回值一致、
共享失败前缀线程 CPU 降低 6.76%-38.27%，不代表 GPU 吞吐
或 JCT 改善。继续以繁忙窗口、实际提前 FULL 复用及恢复等待验收。

V22 完整同版本动态对照仍未达到该目标：两臂均完成 155 个
workflow，predictive 完成吞吐低 17.56%，154 个配对 completed
workflow 的平均 JCT 高 17.82%。该对照的执行轨迹不同，不能
单独识别策略因果效果；此前繁忙窗口的吞吐退化同样保留。
后续 V23 已于 2026-10-11 14:01:40 CST 以 a6dd627 启动，
保持冻结配置；其预取覆盖、控制成本及实际服务改善仍须验证。
600-900 秒完整窗口为 1143.43 decode token/s、86.03% GPU
利用率、47.35 时间加权 batch；V22 predictive/native 对应
1019.20/1219.52 token/s、76.68%/89.81%。跨版本、不同轨迹
只作诊断，不能替代 V23 同版本 native 对照。V23 predictive
已结束，156 个 workflow 中 154 个 completed、2 个 incomplete，
耗时 8984.588 秒；native 服务/客户端已于 16:38:39/16:41:22 CST
启动，完整比较尚不可用。两个 incomplete 没有 deadline 过期或
child 取消，所有 JOIN 满足；最终 root 响应连续 length 截断，
不能将其直接归因于调度或将长分析正文当作完整任务报告。
已核对每次最后调用为 8192 token 的原响应及关闭 thinking 的
恢复响应，恢复正文仍重复并 length 截断；django-12273 的
hard-budget 收尾属于 test-analyst child，不能说成 root 被 guard
终止。终态分类保持 incomplete，生成退化根因仍需调查。
14:41:13 的 V23 审计确认 JOIN/tool 完成前启动并由下一请求
复用 FULL 为 0.515/6.132 GB，需求 handoff 的 93.742 GB
继续单列。独立等待最后 ACK 到服务 P90 为 1772/919 ms，
仍有 9.383/91.976 秒最大值；12 个 JOIN FULL 命令在完成前
过期。此观测不能证明触发长尾已解决或同版本吞吐目标达到。

下一轮 V24 等待登记已于 2026-10-11 17:58:19 CST 更新，
冻结修订为 ddeda0b，包含空提示准入减负、无工作采样调用减负、
按 ledger 扩大 JOIN/tool 批量前缀、下一批恢复额度修正、
1 秒工具卸载窗口、工具扫描内准入预算复用、后台驱逐前缀
范围跳过、本次读取的失败共享前缀复用、graph 回放记录及
工具 CDF 二分查询。运行配置与 V23 相同。这份历史登记随后由
19:05:27 的 56ff3c2 登记取代，V24 已按约在完整 V23 后处理后
启动 predictive；后续仍为同版本冷 native。尚无 V24 成对 GPU
收益证据；登记不代表主性能目标完成。

FULL 的固定前缀可复用有效 Host 副本。PREPARE 只复制缺失
extent，新增前缀增量备份；radix 分裂保留原生 Host 索引映射。
JOIN/tool 预取批量规模由现有 physical ledger 剩余槽限制，
再按字节、分池可用与可回收容量及下一轮 prefill 预留裁剪连续
祖先前缀，不另设独立的 16 节点策略门槛。原生接口在逐节点
reservation 和容量校验后一次提交成功前缀，Mamba 仅在需要的
checkpoint 恢复；整批 H2D 时机仍由服务估计与当前预测决定。
需求 handoff 与只读机会采样保持现有范围。697 项检查和 27 个
原生子测试验证此语义，尚无实际 DMA/吞吐证明，冻结 V23 不变。
若已有副本被 Host 驱逐，后续必须重新备份该段。后续同节点、
同池 D2H 仅为关联证据，不能据此认定覆盖有效副本或精确重复
字节。V10 的 35066 次关联均有中间 FULL Host 驱逐，支持
回收后补传；详细归因见架构状态页与固定审计报告。

投机 PREPARE 不携带 Mamba。运行请求的状态持续更新，固定
前缀的检查点则是版本化快照；真正卸载与恢复时，仍由原生
write-back 和恢复依赖保存、加载必要状态。关闭提前备份不能
删除恢复所需状态，也不能将全部 Mamba 驱逐视作无成本。

预测器负责从已观察的 child 通知、正文及 decode 进度估计阶段
和剩余工作，工具头估计工具返回；runtime 决定传输对象、物理
范围与时机。未来首次服务时刻不进入预测输入，模型不学习
离线 trace 中不可识别的预取净收益。准确率、真实传输提前量、
ACK 后消费与端到端收益分别评价。

最新 V82 运行期读取中，100 个 JOIN 与 390 个工具等待完成前
启动并由下一请求复用 FULL，分别为 1.479/6.257 GB；需求
handoff 的 97.598 GB 保持单列。独立等待最后 ACK 到服务
P90 为 1452/913 ms，最大值仍为 12.496/91.976 秒。工具轮次
关联的 480.284 GB 残余 FULL Host-hit 不是可预取容量上界，
不能将只读计划关联当作实际迁移机会。继续检验等待长尾、
批量恢复覆盖和 V23 同版本 native，当前性能目标尚未达到。

V23 完整 predictive 中，4720 次 handoff no_cold_capacity
拒绝后的原生首次 prefill 距关闭 P50/P90 仅 73.051/116.885 ms，
超过 500 ms 的有 48 个。相关 FULL Host-hit 计数不代表拒绝后
可用容量，也不证明可被提前恢复；暂不增加通用重复尝试。
parent 在关联 child 实际释放设备容量后的专门重试继续保留。
依据实际控制计时优化 reentry、事件交付和采样，不以容量拒绝
次数推导服务被阻塞或恢复收益。

工具卸载的现有实现从实际 WAIT_TOOL 开始，根据残余时间判断
是否备份。后续默认以 1000 ms 为窗口：未来 1 秒内返回概率
不超过 0.1，或无 CDF 时残余 P10 至少 1 秒，便可进入长等待
候选；从原 2000 ms 降低，模型置信度条件不变。备份与等待页
回收共用该判断，实际回收仍由原生分配压力及安全副本决定。
接受预测记录窗口，审计同时报告实际策略口径与固定 2000 ms
参考；缺少窗口字段的历史日志继续按旧规则还原。
不是在工具执行前预测调用并主动卸载。JOIN parent
在进入 WAIT_JOIN 后可备份缺失 FULL，再于真实分配短缺时由
原生 allocator 回收已备份的冷页。当前没有每次 spawn 后
无条件 COMMIT_CPU；PREPARE 只形成 Host shadow，不保证
parent 或工具 agent 已经成为 host-only。已有原生 Host 副本
同样可以供回收使用，Mamba 恢复依赖继续由原生路径维护。

V22 审计记录工具/parent 压力回收为 77/49 条节点事件，
涉及 49 次独立工具等待/42 个 parent，累计释放 FULL
1.188/0.733 GB。该 V22 审计当时要求工具残余 P10 至少 2 秒，
或未来 2 秒内返回概率不超过 0.1；独立工具首次卸载的实测
精确率仅 27/49=55.1%，首次 PREPARE 为 143/499=28.7%。
这是已选择动作的精确率，不是全体预测准确率或机会召回；
累计页回收也不是完整 agent 卸载或吞吐收益。后续改进应同时
核对长等待高估、结束事件交付及实际副本/容量消费，不能用
更多备份 ACK 代替这些证据。逐次数据与来源 SHA256 见
`experiments/reports/v70_v22_wait_offload_20261011.json`。

V23 15:25:09 CST 的审计确认该卸载路径在运行：47 条工具
节点回收涉及 24 次独立等待，首次回收仍剩至少 2 秒的是 22 次；
17 次为 execute、7 次为 glob，样本不足以说明整体预测可靠性。
工具 PREPARE 覆盖 172 次独立等待，首次长等待精确率 135/172。
29 条节点回收有有效 P50，绝对误差 P50/P90 为 224/5161 ms；
重复节点不是独立工具样本，RETURN 时间尾部尚未达标。
JOIN 已备份 128 个 parent context，实际压力回收覆盖 47 个
parent。逐次动作及读取前缀哈希保存在
`experiments/reports/v79_v23_wait_offload_live_20261011.json`；
14:35 的历史审计保留为 v75 回执。继续沿用
实际 WAIT_TOOL/WAIT_JOIN、缺失 FULL 备份和原生容量回收语义；
没有加入工具执行前或每次 spawn 后的无条件卸载。

降低窗口的 V79 固定 trace 回放将独立时间候选从 800 扩至
1447，新增 647 个中 574 个实际仍等待至少 1 秒；原先选中的
快照全部保留。该回放没有重演 allocator 或证明实际 Host 副本、
卸载与复用，不能用候选增加代替性能改善。该规则先于
15:48:41 CST 登记为 1f75f55，16:13:07 更新为包含扫描减负
的 bb64e77，16:46:05 再更新为 dd12454；原生 patch 与 workload
保持，冻结 V23 的 2 秒
动作及标签保持原始口径。
14:15:42 已观察到 0.105/1.188 GB JOIN/tool 提前启动并复用
FULL；需求 handoff 复用单列。它们仍须由完整冻结对照评价，
不能把更多 Host 副本或 ACK 视作性能目标完成。

工具完成历史的误差余量不能作为确定的剩余工作。历史中心值仍
有效时使用已完成调用的先验；中心值已过去、三点先验只剩
误差上界时，切回 operational survival CDF，不再把该上界
归一化成概率为 1 的终点。窗口概率与 P10/P50 采用同一分布，
避免一边投机备份、一边判断即将恢复。若两类分布都没有支持，
预测保持未知，由原生需求路径继续执行；不新增动作门槛。
V22 开发回放的原卸载动作中，新逻辑保留 54/77 条，其中
48 条实际还需至少 2 秒；独立工具为 26/29，对比原 27/49。
这是记录动作的重选子集，收益与全机会覆盖仍须由同版本 GPU
对照证明；不能将 89.7% 写成部署后的全体准确率。

H2D 可以只隐藏一部分恢复依赖，无须预计在 RETURN/TOOL_END 前
完成整批传输。服务估计仍用于选择启动时机，触发 horizon 裁剪到
配置最大提前量；整批服务加交付时间超过此上限不再令计划失效。
例如只剩 100 ms、传输估计需 800 ms 时，可先提交有效缺失页，
完成边界后沿用原生逐层依赖继续恢复。最大提前量限制过早驻留，
不是最小提前量；完整提前 ACK、部分重叠、晚于完成边界启动和
首次消费分别报告，不能将完整覆盖 P90 的子集当作收益上限。
真实收益取决于需求路径暴露等待是否下降，以及额外控制、带宽、
驻留和重算代价；预取 ACK/字节不能替代吞吐与 JCT。

容量也不要求整批 checkpoint 一次全部可容纳。JOIN/tool 在有界
冷页回收后按当前分池空闲容量选择连续祖先前缀；Mamba 只随
可容纳的必要 checkpoint 恢复。首个所需祖先都放不下时仍交给
原生需求恢复，不能跳过祖先加载后面的页。提交前交付因果事件、
重新读取容量，并限定为已选择的节点数，按实际选中负载重算
传输时机与字节归因；不把未提交尾部计为提前恢复。剩余尾部
可在后续预取或需求恢复中继续加载，原生依赖、reserve 与驻留
预算保持有效。此路径复用已有 physical fitting-prefix 规划，
不增设传输门槛、策略开关或观测事件。

恢复预算对应下一批请求，不能持久扣除当前 prefill adder 已选的
请求数。后续 decode overlap 继续使用配置的批量上限，并以实时
request rows、running frontier、分池容量和 prefill/decode 预留
决定实际可恢复范围；不将有空闲字节等同于可执行 slot。V79 的
281 项相关检查及固定 a5b4a83 CPU 夹具验证满当前 prefill 后
下一批仍可恢复，真实 rows=0 时仍不恢复。该修复尚未部署到
冻结 V23，live 预算拒绝仍需区分具体因素，GPU 收益尚未验证。

工具预取扫描中，无分配和回收的候选检查可以复用本轮准入
预算，避免为每个工具重复读取分池容量；在尝试回收前使缓存
失效，下一候选重新读取。预算仅在该扫描内有效，实际前缀
规划和提交继续独立复核容量。323 项检查包含回收后额度下降
的回归验证。V81 固定 455a5ef 的九类 CPU 夹具保持策略计数、
检查与命令一致，扫描 CPU 降低 26.78%-46.48%；这不包含
真实原生分配、DMA 或 GPU 性能，仍须后续同版本对照验证。

V72 的 579 项相关检查及固定 fd4a13c CPU 对照确认原先拒绝的
部分前缀可以提交；该对照的分配、closure 与 enqueue 为夹具，
没有测量实际 GPU、预取提前量或 JCT 收益。V22 中观察到容量
阻塞的工具等待后续关联 187.167 GB FULL Host-hit 需求，
不能由此推成全量可预取机会；最终效果仍按真实提前复用、
残余原生恢复、首次服务等待与同版本繁忙窗口性能评价。

性能实验区分运行所需的控制与纯诊断观测。事件应用、准入/恢复
规划、预测输入和物理依赖仍在正常执行路径；机会快照、终态
缓存追踪、详细直方图可以降频或在独立诊断轮开启。后台 JSON
编码及写盘不使 scheduler 内的同步快照构建变成异步，也可能
争用 CPU/GIL。两臂采用一致的最小公共观测，额外诊断成本单独
报告，保留实际传输、首次消费及必要正确性证据。已埋点函数的
独占墙钟既不是纯计时器开销，也不是可直接扣除的 JCT。

后台驱逐归因可用已有排序前缀长度的二分边界跳过不匹配路由
的整段长度；匹配路由仍逐个生成精确终端摘要，完整证据与
原有候选、溢出和容量过期口径保持。V82 的 148 项检查与
36 类 CPU 对照验证记录、哈希和索引状态一致，不匹配分支
处理 CPU 降低 6.58%-18.28%。这未测量并发 GIL、GPU 服务
或端到端收益，不据此减少归因覆盖或宣称性能目标完成。

隔离后续实现将既有采样条件提前到 scheduler_step 的调用位置，
仅在 writer、诊断条件及 deadline 满足时进入对应计时函数，
semantic worker 不存在时跳过 poll。helper 自身检查保留，
不改变记录或 poll 时序，不将观察条件变成传输门槛。24 组
固定 e317b68 的 CPU 夹具验证等价输出与微秒级空闲维护减负；
未测量真实 allocator、DMA 或 GPU，尚未部署到冻结 V23。
V23 窗口已埋点独占墙钟累计 40.985 秒仍包含 reentry、准入、
备份和恢复的实际工作，不能由该维护夹具推导全路径吞吐改善。

准入没有输出长度提示时，不扫描全队列统计提示覆盖。有提示
时仍校验实时身份、时效与 invocation revision；只有同一
class/depth 组的全部成员有有效提示时，才生成长度排序键。
提示失效后回到既有因果及到达排序，驻留、老化、恢复优先级
继续独立更新。当前 tool_timing_only 实验不产生该长度提示，
该优化用于减少共享控制工作，不替代 child/tool 时间预测。
24 组固定 f9b2f96 的完整 CPU 周期确认等价计划及驻留读取，
未测量真实原生分配、DMA、GPU 或 JCT，尚未部署到 V23。

准入的纯因果 class/depth 可跨无变化的 scheduler 迭代复用。
签名包含 semantic revision、graph version、原生等待队列顺序、
request 对象与完整身份；签名变化重算。缓存不保存完整准入计划
或未来调度时间，不延长驻留/预测有效期。分池容量、当前物理
驻留、老化及优先配额仍实时判断；命中后仍提交已变化的预测输入。
V69 完整 scheduler/准入 CPU 夹具验证等价输出与正常路径减负，
已提交为 b854a91 并登记到仍等待的 V23；实际 GPU 吞吐/JCT
仍需由下一轮同版本实验判定，冻结 V22 及实验配置保持不变。

工具的已完成同输入、失败重复和项目命令历史头输出残余时间
分布。该分布必须跨worker/runtime保留，以预测观察时间为起点
按工具仍在执行条件滚动更新，并扣除IPC年龄。未使用这些历史
特征的通用 operational CDF不能覆盖特定历史头；无特定历史
时才沿用通用路径。支持耗尽表示未知，不能因预测中位数已过
就宣告TOOL_END。在线历史时长与冻结误差余量是输入，未来
工具结束只作评价标签；物理Host副本、分池容量和准入仍由
runtime独立核验，不把同trace时间改善当作真实预取收益。

滚动预测以独立请求的首次可执行触发评价，保留过早触发、工具
轮次误报和无触发；不能只报告末段快照或正确触发的时间中位数。
V16b的4/8/16/32-token窗口CPU候选未取得足够首次触发精度，
不替换当前产物。工作量倒计时与已观察EOS分别检验，常数偏移
只在独立拟合/检验事件中探索，不给所有滚动预测统一加几秒。
请求body发送到原生登记和登记到首次服务各自测量，不能将入口、
tokenizer、IPC和原生准入都当成H2D耗时或RETURN模型误差。

Qwen3.5纯文本请求复用Jinja模板已经编码的输入token，保留模板
缓存，避免多模态模型声明造成的decode/再encode；真实媒体
输入保持原有processor路径。原生与predictive共用此入口优化
和每请求计时；时间随原生IPC携带，报告body发送、API转换、
tokenizer完成、dispatch、scheduler接收、队列登记与首次服务，
不把入口等待并入child剩余工作标签或仅靠扩大驻留锁掩盖。

动作必须有真实迁移需求：PREPARE 的候选需要未来卸载机会，
H2D 需要有效 Host-backed 缺失前缀及必要当前检查点。按池核对
Device/Host 余量，不用总体显存占用代替可行动容量，不为制造
机会主动驱逐有效 Host 副本。已有完成副本、未锁定冷等待 KV
可在真实分配压力下由原生回收；不抢正在执行的热 KV。
预取保护及准入提升有界，并计入新输入、decode 增长和状态
预留、普通请求老化及其他 workflow 尾延迟。

真实 child RETURN 后，结束上下文的无引用私有后缀可直接交还
原生 allocator，无须额外 D2H。已提交且 JOIN 满足的 parent
可在四次普通准入额度后使用下一批恢复 slot：原候选中没有
该类 parent 时，有界检查最多八个关联 parent，选取至多一个
安全 checkpoint、实际恢复 extent 适配当前 free lists 的对象。
数据依赖和首次消费前驻留仍由原生核验，其他可服务请求继续
填充 batch。这个返回后选取与需求恢复不计为预测 H2D；较早
sibling 的真实释放供最终 child 返回前预取使用时，仍按独立
完成边界、实际 submit/ACK 和 FULL 复用证明计算预测覆盖。

容量交接的验收还包含同版本忙碌窗口的服务成本。V22 已观察到
child 私有容量释放与 JOIN 前 FULL 复用，但 600–900 秒窗口
predictive/native 吞吐仍为 1019.20/1219.52 token/s，时间加权
decode batch 接近 47。完整 batch 的相近上下文桶 worker 区间
接近，补入请求阶段的区间则更长；该窗口 predictive Python
独占墙钟计数累计约 46.4 秒。因此应同时核验恢复规划、前缀
检查、控制交付与 prefill 路径，不能只增加回收或预取次数。
worker 墙钟包含调度与 overlap，不等于 kernel 时间；请求轨迹
未固定，也不能将局部计数直接解释为可消除的端到端耗时。
具体证据见 v64_v22_busy_context_600_900_20261011.json 和
v64_v22_busy_window_same_version_20261011.json，性能目标仍有效。

同版本 2400–2700 秒完整窗口出现局部改善：predictive/native
吞吐 1139.97/1082.35 token/s，GPU 利用率 86.29%/79.59%，
可见上下文均值接近，时间加权 batch 仍约 47。然而 native 此时
全部 H2D 仅 1.663 GB，predictive 为 25.225 GB，迁移压力和较早
窗口不同；该窗口不能证明预测迁移或 child 容量交接带来的
因果收益。predictive 的 Python 独占墙钟仍累计 31.495 秒，
其中完整 reentry 观察 7.526 秒。保留 V66 窗口报告与前段退化，
等待完整 pair 和已登记 V23 的同版本对照，不只选有利阶段验收。

补充到达后的完整窗口也必须纳入验收。V22 在 3600–3900 秒的
predictive/native 吞吐为 894.99/1015.65 token/s，4200–4500 秒
为 621.67/881.84 token/s。后者时间加权 decode batch 接近 47，
平均可见上下文为 22665.93/23807.54 token，H2D transfer-stream
累计均约 6.52 秒。相近上下文桶的 batch=48 worker 墙钟接近，
batch=47 及全窗口 prefill 在 predictive 中更长；桶匹配只能用于
定位服务路径，不能消除动态轨迹差异或替代相同需求的因果验证。

该补充负载窗口的 Python 独占墙钟累计 32.381 秒，其中机会
采样、reentry 检查、control drain、JOIN PREPARE、handoff
enqueue 约为 4.259/4.092/4.068/4.021/3.047 秒。减负应分别
处理同步诊断与必要控制/恢复规划，并验证下一轮已登记的正常
前缀、身份、load 结构及预测/归因优化。保留 V68 两份完整窗口
报告和不利阶段，不把此前局部优势当作性能目标已达到。

减负候选须覆盖当前实际的正常输入形态。有效 latest-anchor
快照每池只返回一个叶子；多叶共享树的规划加速不能替代单叶
完整恢复观察的验证。V65 候选虽在多叶规划中加速，单叶完整
路径没有稳定改善，已撤销生产修改并保存可重现的候选补丁。
后续依据实际热路径和同版本 GPU 对照选择部署，不能用未使用
夹具的局部 CPU 加速宣称当前吞吐改善。

若最后child的收尾通知先于sibling RETURN，保留该通知及其
下一请求绑定，在sibling真正RETURN、JOIN只剩一个child时
立即建立恢复阶段，并接续已经观察到的decode进度。检查同一
JOIN与当前context epoch，沿用通知原有效期，不因RETURN
续期；工具调用和后续请求失效的通知不复活。该联动只是提前
规划，H2D仍由真实缺失页、分池容量、近期准入和剩余工作触发，
不释放未结束child的页面，也不替未提交parent独占容量。

终态诊断只跟踪仍存在且creation_time匹配的旧锚点。直接查找
已删除或替换的节点后保留最后一条证据，并停止其后续轮询；
祖先不可观测等未知继续保留。不把旧锚点消失当作独占物理
释放量或触发缓存迁移，容量交接仍以原生allocator回执为准。

已提交请求的 execution handoff 按实际输入规划需求恢复；
child/tool 返回前的预测 H2D 单独计数。前者已有物理 ACK 与
FULL 首次复用证据，不能计作提前预测收益。完整 JointPlan、
主动 COMMIT 与 running retraction 的缺口仍单列为后续工作。

child→parent 容量交接由权威 RETURN 与原生物理引用共同驱动。
通知阶段复用现有 parent 安全 checkpoint/缺失页规划；RETURN
后，在 scheduler 内释放已结束上下文的 child session 引用，并直接
回收无共享引用、无设备/Host 锁、无在途 DMA 的私有终态叶及
经逐节点验证的私有祖先，不为无用后缀新增 D2H。闭合 generation
来源处理 close/RETURN 顺序，session 重开失效旧来源。FULL、
Mamba 和 parent 新输入/decode 增长分别核算，不能按 child
完整上下文推算释放量，也不要求 parent 使用相同物理页号。
Deep Agents 的 persistent 标记用于任务内部多轮执行；完成回调
自动声明 context_retired，复用现有 RETURN 后关闭 session 的
生命周期，不要求模型输出新增字段。同一 context 有其他活跃
invocation 时保留；原始客户端事件时间与 scheduler 交付观察
墙钟分别记录，容量交接时延用同一墙钟计算。

终态 close 与 RETURN 的来源必须覆盖该 session 全部已索引有效叶，
不能因历史分支超过动作快照额度而退回仅最新锚点。完整来源只在
终态关闭/退休读取，后续仍按每次原生回收节点额度分批推进；
generation、节点创建身份、共享引用、锁与在途 DMA 继续逐节点
核验。这样不会丢失历史私有分支的重试来源，也不为终态后缀
新增 D2H 或同步等待全部回收，不扩大预测恢复到整个历史上下文。

终态回收对象按明确的调用身份区分：动态spawn subagent、内部
上下文总结调用及缺失身份的unknown分别记录分池释放量。
runtime_internal总结请求仍可正常释放无用缓存，但不将其回收
算作JOIN child到parent的交接。日志保留invocation_scope；
历史审计优先读取invocation_create元数据，不通过ID命名推断。
所有终态上下文的累计释放、真实child释放和parent服务关联
分别报告，不能把累计字节当成独占容量、预取复用或时延收益。

预测改善与物理机会的交叉评价只使用在机会快照前已交付的
预测，同一workflow/invocation/context/epoch及准确活跃工具
身份关联，保持原hint失效时间。当时的缺失FULL页、free-list
容量及已记录整批latest-start窗口用于诊断；工具未来返回
时间只作为评估标签，缺失窗口不补猜。瞬时容量与规划字节
不等于准入承诺或实际预取收益，仍需传输回执与下一请求消费。

实际释放容量进入原生 allocator，可供既有预取与需求恢复使用。
JOIN 解锁 parent 的缺失 checkpoint 恢复只在近期可准入候选内
有界优先；JOIN 满足、下一请求已提交且必要数据驻留后，再与
恢复页租约和普通请求老化共同决定优先准入。不存在未提交
parent 长期独占容量或模型预测许可释放运行中 child 的路径。
较早 sibling 释放可支持真正边界前 H2D；最后 child 释放后才
启动的恢复属于需求 handoff。终态分池释放与 parent 首次观测
完成 batch 独立记录，不能把容量关联当成预测复用或 JCT 改善。

机会性恢复规划不为补齐活跃 Mamba 的 Host 副本同步等待全部
write ACK。异步备份使用原生读锁保护设备源，ACK 前不回收该
FULL 叶或 Mamba 槽，也不把预分配的 Host 索引视为已完成副本；
ACK 后由后续真实恢复需求再检查容量。无法立即腾出容量时，
已有原生准入继续执行，不额外等待这个候选。真正驱逐所需的
原生同步依赖保留。该处理只是减少规划自身的等待，不增加预测
模型权限、不改变终态 child 回收条件或提前预取的归因边界。

恢复容量回收按FULL/Mamba各池的实际短缺执行。Mamba短缺时，
可从原生未加锁LRU有界选取已有Host副本的无引用状态，只释放
设备Mamba，不连带驱逐仍有用途的FULL前缀；内部节点也可作为
状态候选。仍被等待agent引用的冷状态使用既有因果验证。恢复
闭包内所有节点，包括已驻留祖先，均排除出回收候选；释放后
重新读取原生free-list决定实际可提交的缺失页和必要状态。
容量审计仅将准确、可观测、非空规划的快照分为FULL短缺、
Mamba短缺、双池短缺及均充足；同一事件的不同快照可以跨类。
这些条件不能替代实际传输、下一请求消费或拒绝原因证据。

同一完成 batch 内复用已绑定的请求身份以减少控制查询，lease、
收尾阶段及下一请求的物理可读性仍按实时元数据核验；本次
查询结果不跨迭代作为未来准入或回收授权。终态容量交接与
恢复选择的 CPU 成本单独测量，不能将 CPU 夹具改善换算为
GPU 吞吐。

忙时比较按相同到达流的固定时间窗口进行。decode 工作按完成
batch 的 token delta 归属，LLM 最终长度按请求完成另列。
FULL/Mamba 迁移按真实 submit 归属已完成物理回执；混合
batch 的字节可按来源分拆，但时间只计一次。已采样 GPU 层
依赖与 scheduler/worker 墙钟区间分别报告，不把后者当成
kernel 时间，不用 transfer-stream 累计或 submit→ACK 推算
可直接扣除的 JCT。只有事件边界和下一请求实际复用共同确认，
才将 JOIN/tool 来源的传输列为真正提前消费的 FULL。

V15 历史比较配置为 156 个任务，108 个在 t=0 到达、48 个在
3600 秒后到达，running=48、NUMA1 Host200GB/FULL:Mamba80:20、
HBM Mamba/FULL=0.9、context131072、completion8192、
graph2048/reserve32、workflow14400s，seed21/temperature0。
首轮约束为 2--4 child，后续轮次仍由 prompt 与模型执行决定。
native 关闭 BeliefKV 控制与优先策略，保留双方一致的协议兼容、
客户端优化及遥测；它不是未修改的上游 wheel。

V15 从冻结提交 `feb5ee01a9f1340a694dcba442c439d08e4bd274`
运行，其最终比较与高负载退化见架构状态页。当前推进 V20
终态容量交接与已解锁 parent 的恢复选取：运行中的主目录、
引擎与预测产物保持冻结，
新修订先在隔离分支提交；当前V20两臂完成后再部署新版并按
predictive-first、同版本冷启动native进行验证。仍需完成稳定对照，
目标尚未达成。completed 是运行终态，任务正确性另行评价。
正式实验多轮取平均，固定需求 GPU 回放不是主线前置条件。
实际实现、最终数据与未完成项以架构状态页和执行计划为准。

安全 checkpoint 检查继续逐段核验实际请求 token 前缀，不以
context 身份代替内容匹配。后续控制减负仅为长 int64 段增加
不复制数组的精确比较，保持 namespace、salt、有效长度、
bigram 边界和页对齐语义，并在比较时保留 GIL；短段及其它
容器继续原路径。CPU 基准与真实 allocator/引用检查单独报告，
只有部署后的同配置 GPU 对照才可证明吞吐与 JCT 收益。

只读恢复就绪检查区分D2H备份和H2D恢复：FULL连续前缀及必要
Mamba状态都已驻留时，正在复制到Host不会使GPU源数据不可读。
Host-only组件和在途H2D仍须满足原生数据依赖。此区分只修正
检查结果及由其驱动的候选顺序，不解除原生锁、提前发布Host
副本或授予回收权限。no_restore_plan既可能表示缺乏可用恢复
计划，也可能表示已驻留；不能单凭该原因推断checkpoint缺失。

一次准入规划内复用已经绑定的不可变请求key，避免在编译
顺序时再次解析同一身份；正式选择请求仍核验实时session、
generation、context epoch及attempt，不跨规划调用缓存身份授权。

GPU batch完成回调先按context筛选容量交接和恢复租约的可能
消费者。parent匹配使用本次可见请求已经绑定的context，随后
仍核验完整实时身份；租约匹配使用当前租约context集合，再
核验实际请求的session/generation/epoch。无关child decode
继续正常记录服务和语义进度，相关消费者仍通过原生锁回执
释放驻留保护，不跨batch缓存一次身份核验作为后续授权。

等待parent/长工具请求的压力候选发布保留原序fallback及FULL、
Mamba各池前八个。先扫描十六条；只有仍需寻找稀疏池候选时，
才利用原生设备叶集合和Mamba驻留LRU过滤后续节点查询。
每次发布重新读取索引，并继续核验实际身份、副本、引用、锁及
在途传输；缺少索引时沿用逐节点扫描。索引只减少无用查询，
不改变child终态回收、parent容量交接或原生驱逐的授权与顺序。
CPU发布基准和部署后繁忙窗口吞吐分别评价，短路径微小开销
也计入结果；不以CPU夹具代替GPU/JCT收益。

原生与predictive采用相同服务初始化：health成功后、workflow
提交前，显式执行原生GC冻结。跳过模型warmup时，启动线程
可能在HTTP监听前发出同一操作；不能仅由服务进程就绪推断
初始化成功。运行中补做的时点作为实验条件保留，受影响的
全程JCT与完整冷启动对照区分，不修改模型或调度参数掩盖差异。

闭包不可观测时保留原始具体原因，区分深度、锚点身份及在途
传输，不把所有失败解释成checkpoint缺失。同一scheduler
安全点的H2D闭包失败可直接用于PREPARE只读机会采样，避免
再次扫描；下一次采样重新读取物理状态。日志详情及去重只
服务归因，不增加动作门槛或以旧失败替代当前恢复授权。

JOIN评估以workflow/context/JOIN ID定义独立事件，不随parent
恢复后的context epoch重复计数。完整JOIN后的首次服务只关联
parent实际提交的第一条请求，即使计划观测晚于该提交；缺少
完整JOIN边界时不推测关联。后续工具请求及不同spawn轮的JOIN
分别评价，GPU服务开始观察与完成batch观察也保持各自口径。

主指标为完成吞吐、JCT 和 completion curve；同时报告实际
工作量、任务正确性、GPU 服务、缓存命中、重算、按来源分池
传输、驻留字节时间与公平性。CPU wall 区间、传输累计时间或
更多 ACK 不自动构成端到端节省。若计算、Host thrash 或工具
长尾主导，需报告收益边界，并降低缺乏后续消费的动作成本。

### 1.2 可证伪的研究假设与对照

- **HBM 有可行动余量**：工具等待或 JOIN 提供足够 lead，且未来
  卸载会消费提前备份、未来服务会消费提前恢复的 KV 时，
  `PREPARE_HOST`/predictive H2D 相比同到达流 native 可缩短同步
  D2H/H2D 停顿；计入无效备份、提前驻留字节时间及 PCIe 干扰。
- **有冷 KV 可置换**：当 free-list 不足但可安全收回更冷页时，
  在有界 lease 下预取关键路径 parent 或下一 agent；将 victim
  后续 miss、其它 workflow JCT 和尾延迟纳入成本。缺乏冷页时
  放弃替换。联动执行顺序/回收的 handoff 另作消融，不用它掩盖
  PREPARE/H2D 自身是否有净效益。
- **过载、计算或 Host 容量主导**：若增加 H2D/驻留无法转化为
  真实消费或节省阻塞，应回退到响应式路径，并报告机会缺失；
  不将此压力档的 speculative H2D 数量视作成功。

各压力档必须由训练工作负载中的**物理状态**划分：runnable backlog、
可回收/锁定的 FULL 与 Mamba 字节、Host 有效副本和驱逐后 miss、
GPU 服务饱和度、传输队列及重叠余量。根数只用于重复配置，不用作
压力标签；同一模型、Host/NUMA、HBM/graph、runtime 和到达流
下比较 P5、PREPARE、H2D、闲置容量使用、冷页替换与可选联合 handoff。
分档门槛与动作预算只在训练项目冻结，项目隔离测试不回调参数。

与已有 Agent-aware offload/predictive upload（TokenCake,
arXiv:2510.18586）及 next-step KV prefetch（KVFlow,
arXiv:2507.07400）相比，不能把「提前迁移」本身作为新颖性主张。
待验证的区别是**在线因果 frontier 和 FULL/Mamba 物理余量共同
决定哪笔 KV 值得提前备份/恢复、是否替换冷页、何时准入执行**，
在缺乏可靠精确时钟或有用物理机会时有界地放弃动作。联合
handoff 是可选增强，不能作为尚未验证的当前收益。

## 2. 当前核心设计

![BeliefKV 请求与 KV 联合调度](figures/beliefkv_joint_algorithm_overview.svg)

本节及第3-4节描述目标联合架构的合同，不是新版全部已上线的
能力清单。当前执行通过native causal admission和有界物理动作
接入SGLang；完整JointPlan、COMMIT与running retraction的迁移
缺口见第7节。图中的旧P5/P6路径同样不能代替新版实现证据。

BeliefKV 使用两个相互正交的状态视图：

- RCCG 描述 Agent 的因果和执行关系；
- PageIndex/Radix 描述 KV 页的物理共享、驻留、锁和 generation。

目标架构在 JointPlan 中结合二者：

```text
runtime events -> RCCG causal frontier
                          \
                           -> JointPlan -> admission tickets -> GPU batch
                          /
Radix/PageIndex -> physical bundles
```

JointPlan 同时决定：

- 哪些 runnable request 优先获得 GPU service；
- 哪些 waiting request 获得当前 batch epoch 的 admission ticket；
- 哪些 KV 保留、迁移、恢复或丢弃；
- admission 是否依赖某笔 reclaim/restore ACK；
- 是否需要 selective running retraction。

## 3. P5：Observed-State JointPlan 目标合同

### 3.1 请求调度

P5 只调度已经由 RCCG 证明 runnable 的 invocation。正常排序综合：

1. starvation floor；
2. JOIN straggler 和下游 action unlock；
3. causal MaxWeight utility；
4. resident-ready bytes 与 startup cost；
5. workflow fairness tie-break。

系统不限制每轮每个 workflow 只能选择一个请求。同一 workflow 的多个 child 可以在资源
允许时共同进入 batch。

请求始终保留在 SGLang visible waiting queue。BeliefKV 生成短期、单 epoch 有效的
AdmissionTicket；没有 ticket 或等待 restore 的请求在本轮被跳过，但不能阻塞后续可运行请求。

同步路径使用 O(K) bounded seed，复杂语义规划在异步 worker 中运行。计划必须在 scheduler
safe point 重新验证，SGLang PrefillAdder 和 allocator 保留最终否决权。

### 3.2 Residency 分类

| 类别 | 含义 | 默认处理 |
| --- | --- | --- |
| `PINNED` | running、engine lock、active reader 或事务占用 | 不迁移 |
| `IMMINENT` | 已 ready 或即将 reentry | 保留或优先恢复 |
| `PARKED` | WAIT_TOOL/CHILD/JOIN/MESSAGE | 可作为 victim |
| `DEAD_UNOWNED` | 无未来 owner | 直接释放 |

共享页使用所有 owner 中最强的保护等级。逻辑 context 不能直接作为迁移单位；系统必须先解析
为包含共享页和 ancestor closure 的 PhysicalBundle。

### 3.3 响应式 HBM 释放合同

真正释放 HBM 的 `COMMIT_CPU` 由已经可观测的 beneficiary HBM deficit 触发：

```text
deferred beneficiary
  -> startup + restore + growth deficit
  -> select PARKED/DEAD physical victims
  -> COMMIT_CPU(victim) or DROP(victim)
  -> completed reclaim ACK
  -> ADMIT(beneficiary)
  -> first real GPU service
```

高 HBM 使用率本身不是迁移理由。系统要求存在明确 beneficiary，并且估计的 saved admission
stall 大于 D2H、未来 restore/recompute 和干扰成本。

### 3.4 Restore 与活性

Running retraction 使用 durable RestoreTransaction：

```text
WAIT_FEASIBILITY -> WAIT_FUNDING -> READY_TO_COMMIT
 -> H2D_QUEUED/ADOPTED -> H2D_ACKED
 -> ADMISSION_RESERVED -> ADMITTED
 -> SERVICE_GRACE -> SATISFIED
```

普通 waiting prefix 不得升级为全局 restore barrier。显式 H2D 不可行时回退到 SGLang
native demand-load；Host copy 不可用时允许从 raw tokens 重算。Restore 只有在请求重新获得
至少一个真实 GPU service quantum 后才完成。

## 4. 物理 KV 数据面

- SGLang/Radix/HiCache：真实 KV、allocator、DMA 和 lock 的 owner。
- PageIndex：按 `(page_id, allocation_generation)` 维护物理镜像。
- PhysicalBundle：共享页和 closure 完整的迁移单位。
- Transfer transaction：D2H/H2D/DROP 的提交、ACK 和终态。
- JointPlan：只表达语义动作和依赖，不直接修改 tensor。
每个物理动作都必须在 safe point 检查：

- context/request epoch；
- Radix ownership、ancestor closure 和 allocation generation；
- engine lock、semantic pin、active reader；
- HBM/Host 实际容量；
- transfer capability 和 in-flight conflict；
- beneficiary、deadline 和动作证书。

## 5. 新版事件与内容驱动的预测旁路

v8c reactive原生迁移数据已完整采集，但D2H在途split ACK的
旧节点集合校验错误禁用了PREPARE通道，不能证明完整策略baseline。
修复按native拓扑/代次与原Host目标索引验证发布节点扩展，不放松
字节、pool、session/epoch和重放约束。后续v8d是修复版本的108-root
predictive开发诊断，沿用冻结预测头；不将跨版本轨迹当作公平对照，
模型仍只预测事件/工作量，runtime决定动作。

2026-10-07当前v8c诊断使用108-root单波及每轮2–4 native task
调用，保持running48与池配置。这是新压力/fanout组合，不自动
外推v7校准。JOIN恢复要求完整ALL成员集只剩一个未完成child，
不能用首个child通知提前唤醒parent。工具CPU并发、GPU无服务
间隔及任务大小变化须按新trace分别评价。
新的旧输入前缀未命中代理排除新输入，Host块归因完整探测原有
有界索引；终态leaf ancestry/锁/refs及close ACK时间独立记录。
关闭引用不等于释放缓存，祖先可能仍共享，不盲目D2H无用child
或DROP共享活页。H2D计时预算不是包括调度/重算的完整oracle上限。
首次v8因auto首轮绕过委派已停止。v8b后置专用委派prompt，
首轮命名task且显式并行；仅在BeliefKV ingress对task子集使用
可重复required约束，避免本机命名工具语法硬性限制为一个调用。
完整工具prefix不变，但v8b仍108个首轮全部单调用，已停止。
v8c首轮通过RepeatFormat在生成时限定2–4，模型选择数和内容；
后续轮次保持prompt驱动并记录实际次数。不在输出后拒绝回复，
不补造child，也不把首轮约束当成所有后续轮次均2–4的证据。

预测旁路不建立第二个调度器，也不控制agent是否可以RETURN。
当前配置使用冻结MiniLM encoder及phase/work头、独立工具事件
时间模型和实测传输服务估计；运行中不切换权重或重新校准。
旧Qwen3 FrontierBelief schema-v5的数值、动作资格与完整
RECLAIM_AND_PREFETCH机制属于历史参照，原文保存在
`c219604:docs/beliefkv_design.md`，不能作为新版能力证明。

### 5.1 模型输入输出与运行边界

child模型输入为已经送达的有界正文语义、正文字符数、已生成
token数、历史工具/模型轮次、完成通知及其报告长度提示。
native工具标记另用于使当前轮的终态信号失效。语义推理在
独立CPU process中运行，scheduler消费有身份与时间证明的结果。

输出分为 `continue_work`、`completion_notice`、`final_report`
阶段分数，以及条件剩余生成token的下界、中心和上界。
校准区间不自动等于正确的P10/P50/P90，状态单独保存。
模型本身不直接授权物理动作，也不把未来GPU准入时刻作为输入。
runtime依据实际decode进度和已观测服务估计滚动换算近端时机，
前EOS使用已有工作上界；无工具EOS仅保留短协议窗口。

工具模型输入包括当前等待elapsed、角色、工具/backend/command
类别与上下文历史，输出残余时间及事件CDF。当前准入使用配置的
残余P50窗口，H2D准入中CDF保留诊断；长等待回收仍使用条件CDF。
v5证实两个口径可同时将同一目标判为冷victim和预取beneficiary，
导致ACK后再次回收。新代码以同一生存条件CDF逆算残余P50，
并在ACK后建立短期策略租约，排除自身冷回收直到服务/失效。
不是容量预留或全局pin，原生压力驱逐仍允许且须明确归因。
v6的7个JOIN H2D已保留并首次复用，无自身再次回收；
工具H2D为0，不能据此完成工具链路GPU验收。
端到端净收益仍未证明。并行工具依赖未满足时不提前唤醒。
传输模型使用实际FULL/Mamba形态及相近大小的样本，分别估计
enqueue-to-submit和submit-to-ACK，不线性放大固定ACK开销。

runtime才决定是否PREPARE或H2D：检查下一输入可复用的安全
checkpoint、有效Host副本、FULL/Mamba物理容量、当前因果状态、
传输时间及驻留机会成本。源副本可以来自native D2H或PREPARE。
阶段模型、旧模型的eligibility字段与物理动作授权相互独立；
不通过改写false标志开放旧全套迁移路径。

当前实现将语义对象与物理范围进一步解耦：动态workflow的工具和
JOIN事件决定“谁即将恢复”，原生有效输入前缀及缺失page/extent
决定“传多少”。FULL祖先只搬缺失FULL，当前安全检查点才携带
必要Mamba状态；本context历史状态引用可释放，其他共享/锁定状态
仍由原生缓存管理。ACK后的有界原生驻留保护、真实就绪到提交
宽限及恢复优先准入共同连接预取和首次服务，普通请求以共享
提升预算和老化限制保护。原生v9结束并导出HTML后已合入，
尚无新GPU净收益证据；模型不负责选择物理范围或判断净收益。

进一步将短期执行顺序与KV恢复联合：scheduler在batch选择之前
确定已提交、可执行的下一候选，并用真实输入读取安全检查点。
优先服务同因果层级中KV已在HBM的请求，给候选的缺失FULL前缀
与必要当前状态提供提前换入窗口；JOIN解锁parent的既有有界提升
与普通请求老化仍保留。真实容量不足时，只停放已有完成Host副本、
未锁定的冷等待agent或无引用缓存，不撤下正在执行的热KV。
备份与恢复可在不同节点同时在途，HBM目的空间必须在实际分配
时已可用，不能根据尚未完成的D2H预测释放容量。
此execution handoff不依赖模型学习净收益；它补充边界前预测，
不替代或伪装为准确预测RETURN。按来源独立计数，要求FULL实际
复用、原生需求恢复减少及端到端效果形成证据。
后续Host FULL:Mamba默认80:20，Device Mamba/FULL字节比例0.9
不变；该配置须经新的物理容量census核对，不宣称已证明最优。

v6已有7个JOIN H2D ACK与FULL首次复用，全部发生在EOS后；
工具H2D为0。单轮吞吐高4.62%但实际工作量更少。端到端吞吐净收益
仍未证明，不能据此证明普遍准确的RETURN预测或完整JointPlan已迁移。

工作头先修正区间语义：旧产物先非负截断再加绝对token margin，
会在末段保留固定正数下限；新候选先在signed residual上扩张
再截断，并重新按workflow校准，旧产物不默默换语义。
随后利用真实100 ms正文/进度观测比较log-work分位数头。
phase/encoder/phase threshold冻结，未来RETURN和服务只用于标签，
不作为在线输入。Runtime显式选择center/upper时机，旧默认upper；
不是用整段保守区间否决全部近端动作，也不把中心为零当作EOS。
首次触发过早、工具轮次误报、区间覆盖与真实预取提前量须分账，
不能只按整体MAE选模型。
正常native stop、已观察到非空正文且无工具标记时，协议窗口
使用独立观测证据，不强制等到NN forecast或TPS可估计。
该阶段只用于H2D，不改变agent终态或收尾准入优先级；仍验证
完整ALL JOIN关键child、Host安全副本、身份与容量。
空白/reasoning-only、length/abort和internal不能作为此证据。
观测窗口与前EOS模型触发分开统计，协议加载不是预测精度证明。

### 5.2 模型预测与 runtime 决策的分离验收

**事件与剩余工作头**仍单独评估工具 release 与完整 JOIN 的预测：使用项目隔离的
自然事件，按触发阶段、压力桶报告覆盖、误报、条件 P50/P90 误差和删失，
不只在事后成功的长窗口子集计算精度。JOIN 不等同于单个 child RETURN；
有多个未完成 child 时需按实时 blocker 集合组合；只剩最后一个 child 时
可把结构化终态提示作为近端信号，但不能把其发出视为已确认 RETURN。
继续追求有用的亚秒级时间精度，但不能把该阈值当成每次预取获益的必要条件，
也不能用只覆盖少数近终态 JOIN 的条件误差替代整体表现。

**runtime动作决策**判断当前可见状态下某笔迁移是否值得执行，
不是让模型学习离线trace不可识别的反事实净收益。令 `R` 为
JOIN真正满足、parent可提交的时刻，
`C` 为同一物理 KV/epoch 的有效 H2D ACK：完全隐藏传输要求 `C <= R`，
且 `R-C` 不超过受 HBM 机会成本约束的驻留预算；部分完成只计实测可节省的
等待。即便 `C <= R`，若没有被首次服务实际消费或挤掉更高价值 KV，也
不得算 useful。受益必须由 request 级 ACK/extent、实际首次服务及同配置
reactive 对照验证；不能拿 reactive 的长排队窗口当作预测式 H2D 的节省。

以下冷页抢占与handoff为目标合同，不是当前已上线策略：
parent 的关键路径价值以剩余 blocker、下游解锁、当前工作流进度和真实
可回收物理 KV 表示，而不是“所有 parent 永远高优先级”。同一 JointPlan
比较空闲空间、可安全置换的冷页以及其它 runnable 工作；只在可用 Host
副本、closure/owner/epoch 与容量证书成立、预计解锁收益覆盖搬运/置换/
未来 restore 或 recompute 成本时，给 parent 短期驻留租约。租约有
字节/时间预算、到期或状态变化撤销、防饿死界限；ACK 未到不得按已恢复
KV 准入。JOIN 之外的 admission handoff 也按相同规则，在选出下一
beneficiary 后先预留空间、协调 D2H/H2D，再给执行 ticket。

child RETURN 时间拆解为可观测的执行阶段/剩余 GPU 工作、未来 GPU
服务份额、排队和工具执行，不把未来排队时长作为在线特征。训练时可以
用有身份关联的 request 服务事件重建**实际发生的**服务时间和排队时间，
检验“剩余工作/服务需求”头是否跨压力更稳健；工具等待对真实 RETURN
仍有贡献，不能一律从标签中删除。预测调度会改变服务分配，需在
reactive 与 predictive 下分别校准/按压力分层，之后只用历史已确认事件
滚动校正；在线更新必须经过因果时序、漂移及安全回退门禁。
当前条件工作头已使用剩余token标签；跨调度的未来服务份额、
排队分头及在线更新仍未验证，不能声称已消除RETURN墙钟波动。

不将 JOIN/工具墙钟点预测达到亚秒级设为物理实验的先决条件。
利用确定性frontier、已观察的临近事件和保守时机进行当前单pair
开发A/B；不以canary或固定需求GPU回放作为前置步骤，正式实验
再采用多轮配对取平均。运行中的源码、prompt、权重和参数冻结。
时间模型单独按全体与条件覆盖验收，未获项目隔离验证的头不得作为
无回退的物理门禁。
不基于正在运行的密封留出集调整压力阈值、ETA、模型或准入门禁。

## 6. 未来可选方案：Predictive Eviction

### 6.1 动机和当前缺口

当前主线实现有界native predictive transfer/shadowing，不是
完整predictive eviction或新版完整COMMIT/JointPlan：

```text
PREPARE_HOST: GPU_ONLY -> GPU_AND_CPU_SHADOW
COMMIT_CPU:   GPU_AND_CPU_SHADOW -> CPU_ONLY
```

前者提前消除未来D2H成本，但不释放HBM；上述COMMIT转换是算法合同。
新版当前的等待态回收由真实allocator短缺触发，要求备份已ACK、
独占且未锁定，不等于完整旧COMMIT事务已经迁移。如果竞争工作
中的收益来自预测式提前释放HBM，当前不能声称已覆盖该来源。

### 6.2 可选的两阶段算法

未来可以在不改变现有安全边界的前提下增加 `PREDICTIVE_COMMIT_CPU`：

1. PCIe 有余量且预测等待窗口足够长时执行 `PREPARE_HOST`；
2. CPU shadow 完整后持续观察 projected HBM timeline；
3. 在 `latest_safe_commit_time` 到达时重新评估；
4. 满足风险和收益门槛才解除 GPU residency；
5. 预测失败时使用已有 H2D、native demand-load 或 recompute 恢复。

提交条件至少包括：

```text
shadow_complete
AND victim is still PARKED and unpinned
AND P(wait remains open beyond commit/restore horizon) >= p_min
AND projected_hbm_deficit > 0
AND concrete/projected beneficiary exists
AND expected_saved_stall
    > expected_restore_or_recompute_debt + transfer_interference + risk_margin
AND live causal/physical certificate is fresh
```

### 6.3 与现有 P6 的关系

该方案复用现有 FrontierBelief、beneficiary-bound package、latest-start、PhysicalBundle 和
safe-point transaction，不新增独立 eviction scheduler。建议把它保留为未来可选实验分支，
而不是当前论文主张，直到满足以下门槛：

- 现有 `PREPARE_HOST` 在自然 workload 中出现可归因的 useful shadow；
- trace 中存在“响应式 COMMIT 已经太晚”的 admission stall；
-离线/影子重放显示 predictive commit 相比 reactive commit 有稳定正收益；
- 有界配对机制验证没有显著增加反向 H2D、recompute 或 HBM-time 浪费。

必须报告 useful/wasted commit bytes、提前释放的 HBM-time、beneficiary saved stall、
restore/recompute debt、方向反转率以及最终 workflows/hour。

## 7. 实现边界（旧 P6 路径及新版缺口）

下表以当前Qwen3.5/SGLang 0.5.20的有界native路径为准。
旧Qwen3的完整P5/P6能力属于历史参照，不代表新版已经迁移。
模型输出不是物理授权，也不需要学习离线不可识别的预取净收益。

| 能力 | 当前状态 |
| --- | --- |
| 动态 RCCG 与 FRESH subagent/JOIN | 新版已接入，summary与并行工具归属已修复 |
| Native causal admission | 新版已接入；allocator最终决定准入 |
| FULL/Mamba物理闭包、身份与ACK | 有界单node原生事务与首次消费证明已接入 |
| 预测器 | 冻结语义phase/work + 独立工具残余时间/CDF；runtime独立选动作 |
| Predictive `PREPARE_HOST` | WAIT_JOIN/WAIT_TOOL 后 FULL 备份与真实压力回收；V22 工具/parent 回收77/49条节点事件，首次工具卸载长等待精确率55.1%；无无条件 spawn 后 COMMIT，净收益未证明 |
| JOIN `PREFETCH_GPU` | v15有99次ACK，FULL传输/确认首次复用0.893/0.743GB；净收益待完整对照 |
| 工具 `PREFETCH_GPU` | v15有77次ACK，FULL传输/确认首次复用0.338/0.293GB；仍有未保护驻留丢失 |
| 原生D2H副本恢复 | 同样可用，不强制依赖先前PREPARE |
| 新版 execution handoff | v15需求恢复15132次ACK，FULL传输/首次复用67.205/67.047GB；不属于提前预测 |
| 完整COMMIT/JointPlan | 尚未完成新版执行/ownership与收益验收 |
| Running selective retraction | 新版完整适配仍缺失，不开放旧全套物理开关 |
| 新版有界恢复驻留与准入提升 | 已接入；锁保护、准入长尾及其他workflow代价继续审计 |
| 新版 child 工作/服务/排队分解与在线更新 | 待训练侧可识别性验证，尚未上线 |
| Peer multi-agent 专项优化 | 非当前关键路径 |
| Oracle action-space 优化 | 已暂停，仅保留诊断资产 |
| Morphology 独立策略 | 已降级；shape 仅作 transfer cost/OOD 输入 |

v4的吞吐负结果不能一句归于模型路径差异。已确认最后一个
workflow的两次600秒全量测试造成长段无GPU请求；管道上游错误
可能被tail的成功退出掩盖。CPU inspection和有请求阶段的GPU
利用率差距仍需profile，不能把scheduler墙钟interval当kernel
时间，或把所有uncached input都当重算。
v5的利用率不再低于reactive，但整轮吞吐仍低8.95%；
真实工具预取被相反的pressure策略抵消。模型只预测时间/工作，
runtime必须使预取目标、回收候选与短驻留生命周期保持一致。

## 8. 不变量

- 不要求预定义 DAG，但要求最小运行时因果事件。
- 不根据 prompt 文本猜测 parent/child 身份。
- SGLang allocator 和 Radix 始终具有最终权威。
- 计划字节不能代替 completed ACK 的实际释放字节。
- 预测动作不能绕过 P5 correctness 和 liveness。
- 不把旧策略的 wall-clock RETURN 误当调度不变的纯服务需求；未来服务/
  排队分头必须使用因果可观测的历史输入，并显式报告压力与策略分布差异。
- 正式性能比较必须使用相同 workload、模型、runtime profile 和 instrumentation。

## 9. 当前实验环境

H200 NVL单卡、Qwen3.5-35B-A3B BF16、SGLang 0.5.20，
同一 `beliefkv-next` 环境运行serving与agent实验。
Device为FULL约36.843 GB/Mamba约33.096 GB，
Host为NUMA node 1的约200 GB，FULL:Mamba按80:20分配；
Host和Device不采用相同比例，容量以实际启动census为准。
running=48、context=131072、completion=8192、workflow=14400秒，
graph=2048/预留32步、宽松native-reactive profile、自然语言终态。
当前比较为V15 native/predictive、156任务两波到达(108+48/
3600s)。两侧冷启动服务和缓存，模型、脚本、共用协议与容量冻结；
详情及启动SHA见架构状态页和当前执行计划。

旧Qwen3/0.5.2rc1的冻结基线仍保存在
`configs/p6/h200_bf16_v7/frozen_runtime_profile.json`，不是当前默认。

## 10. 代码入口

- RCCG：`beliefkv/control/causal_graph.py`
- 新版接入/准入：`beliefkv/runtime/sglang_v0520_runtime.py`、
  `beliefkv/runtime/sglang_v0520_admission.py`
- 原生session/物理动作：`beliefkv/runtime/sglang_v0520_sessions.py`、
  `beliefkv/runtime/sglang_v0520_physical.py`
- JOIN与预测：`beliefkv/runtime/sglang_v0520_join_projection.py`、
  `beliefkv/runtime/sglang_v0520_prediction.py`
- 语义phase/work：`beliefkv/predictor/child_semantic_work.py`、
  `beliefkv/runtime/semantic_report_worker.py`
- 工具与传输服务：`beliefkv/runtime/tool_wait_shadow.py`、
  `beliefkv/runtime/native_transfer_service.py`
- 原生遥测：`beliefkv/runtime/v0520_native_telemetry.py`
- 旧算法参照：`beliefkv/policy/joint_scheduler.py`、
  `beliefkv/runtime/sglang_v052rc1.py`、
  `beliefkv/runtime/restore_obligation.py`，不等于新版迁移完成。

## 11. 文档权威顺序

1. `docs/README_zh.md`：统一导航和生命周期。
2. 本文：当前算法与系统设计。
3. `docs/architecture_status_zh.md`：当前代码与实验状态。
4. `docs/implementation_plan.md`：当前执行顺序。
5. `docs/experiments/`：不可变实验依据，不直接代表当前设计。
6. `docs/archive/`：历史计划和旧权威文档快照。
