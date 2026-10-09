# BeliefKV 当前架构与实现状态

更新日期：2026-10-09。

## 当前目标与 v13 修订

当前目标是在固定workload、模型、容量及到达表下，使predictive
相对native取得可核实的性能提升。除预测传输外，优先减少调度/
控制开销，利用agent依赖、恢复就绪与HBM局部性改善实际执行。
以下三项并入同一目标，不能以动作数量代替验收：

1. 缩短恢复就绪到首次服务的等待，减少首次服务前的原生重复加载；
   同时检查缺失页、必要Mamba状态与其他workflow的排队代价。
2. 补齐PREPARE消费归因，区分压力释放、原生/受控H2D、后续D2H
   覆盖和未观察到恢复；据此减少没有迁移需求的备份。
3. 提高有用FULL预取覆盖，验证已修复handoff是否真正替代需求
   恢复，并以实际复用、完成吞吐、JCT及重算量评价收益。

补充的执行约定：PREPARE只提前备份尚未拥有有效Host副本的FULL
前缀。原生build_backup_spec已跳过备份完成的FULL节点，radix节点
分裂会拆分并保留已有Host索引；后续新增前缀只复制未备份节点。
Host副本若已被驱逐则必须重新备份，不能复用已失效的索引。
投机PREPARE不再携带Mamba；固定前缀的Mamba检查点是版本化快照，
运行请求的状态继续更新。真正驱逐/恢复所需的状态保存仍由原生
write-back和恢复依赖处理，不能把关闭提前备份解释为删除恢复状态。

旧审计的superseded_node_pools只表示同节点、同池又出现了D2H，
不证明字节被覆盖或重复传输；节点分裂、Host重分配及旧合并批次
的分池身份均不完整。新报告将其改名为later_node_pool_d2h，
保留“恢复关联不等于forward复用”的口径。三项验收标准属于当前
active目标。旧合并批次中，未打标签操作的pool数量先扣除已知
child receipt，避免将其他操作的FULL数量套到Mamba节点上。
补查v10：35066次后续同node/pool D2H之前，均观察到对应Host FULL
驱逐。证据支持“备份被回收后补传”，不能解释为覆盖仍有效的
Host副本，也不能跨radix分裂推导精确重复字节。修正旧合并批次的
分池归因没有改变本轮计数。补查报告：
`experiments/reports/v10_prepare_full_incrementality_20261009.json`。

v10 native H2D transfer-stream累计100.235秒，实验窗口10345.487秒。
它不是全部恢复等待或oracle上界；相比之下，predictive的JOIN
PREPARE exclusive CPU累计1480.695秒。因此同时检查agent准入、
批次填充、冷KV回收和控制路径，而不是只扩大预取提前量。

已实现后续修订：

- PREPARE压力按下一批最多8个实际准入候选、running上限及运行
  请求的页增长计算。没有等待请求、没有运行请求时跳过扫描；
  没有下一请求时不因Mamba空闲slot低而持续备份。
- 当前context没有可备份步骤或Host容量不足时，下一次观察延后
  1000 ms。新epoch/session身份立即重新观察；实际enqueue仍重新
  校验。它只降低检查频率，不取消child或改变自然语言RETURN。
- FULL可回收叶节点与Mamba可独立迁出的状态分别发布候选；原生
  限制前8项之前先按池筛选，避免FULL候选被不可回收祖先遮住。
  原生分配器仍逐次检查Host副本、引用、锁、代次和在途DMA。
- 一个请求NO_TOKEN且尚未加入batch时，只有原生预算仍允许更多
  准入，才尝试后续候选，每轮最多绕过8个未老化的tagged请求；
  不改容量预算，10秒老化仍有效。记录prefill_capacity_bypassed。
- 原生合并ACK额外发布经过数量/字节核对的逐操作pool receipt，
  包含未打BeliefKV标签的操作。原有受控动作ACK授权保持独立。
  消费报告按同节点、同池的最后D2H关联后续H2D，并分开报告
  handoff；恢复证据不等同于模型forward的首次复用。

补查v10：35713次PREPARE均有ACK，115次关联后续恢复，其中98次
关联原生H2D、29次关联受控H2D，二者可重叠；35066次的节点/池
又出现在后续D2H中，不能称为已证实的覆盖。旧记录缺少原生逐操作pool receipt，关联
使用legacy_batch_pool_presence，不能将35598次未观察到恢复全部
定为浪费。结果保存在
`experiments/reports/v10_prepare_restore_attribution_20261009.json`，
不覆盖v10冻结报告。

CPU对照使用156 workflow、24节点闭包、16轮历史，与2abb957比较：
已备份无租约/四租约的JOIN PREPARE下降2.67%/11.82%，未备份/
Host不足下降15.49%/56.58%。未保护的候选集合、备份选择及采样
目标一致；活跃恢复租约由原生validator排除。报告为
`experiments/reports/prepare_demand_scan_cpu_156_24_20261009.json`。
主仓库223项检查及随后引擎/脚本73项检查通过，不代表GPU收益。

后续冷启动沿用v11 predictive配置：108任务t=0、48任务t=3600，running=48、
Host 200 GB FULL:Mamba=80:20、HBM Mamba/FULL=0.9、同一模型、
预测产物、prompt、seed=21和预算。复用已完成v10 native作为开发
参考，报告源码/引擎版本和实际轨迹差异。出现实现故障时停止该轮、
保留证据、修复后冷启动；正式结论仍要求交替顺序的多轮对照。
v11的冻结版本为3d750f1，其PREPARE仍可能携带Mamba；上述FULL-only
修订在独立工作树中实现，须在后续冷启动版本中核验。
v11于2026-10-09 21:34:34因handoff回收计数的KeyError退出：
局部tracker为空，而原生累加函数假设FULL键已存在。客户端已停止，
该轮只能用于故障前的机制分析，不能作为完整吞吐对照。
后续版本修复稀疏计数累加，并在实际FULL叶驱逐前保存仍被session
引用的未备份Mamba检查点，避免FULL-only PREPARE使必要状态丢失。
采集脚本在服务端提前退出时终止客户端进程组，避免继续生成连接
失败记录；该监督不干预正常agent轨迹。
v11故障前部分审计：4747个handoff节点动作、944次PREPARE；
5066个有首次服务记录的恢复动作ACK到服务P50为77.42ms，
P90仍为3636.41ms。864个动作确认FULL复用，4202个未确认复用，
3个无首次服务记录。这是逐节点动作口径，不能将未确认数直接
解释为全部传输浪费，也不能用于完整实验的吞吐结论。
修复已提交为064ff55；v12按相同配置冷启动，FULL-only PREPARE
及实际驱逐时的必要Mamba保存须单独核验。
进一步检查发现复用判定混用了缓存来源与当前物化驻留：
cached_tokens_device/host是原生来源统计，不能代替执行前缀中的
分配身份。v11的4202个旧口径未复用动作均满足服务前缀覆盖及目标
节点在服务路径上，但旧记录不能重建全部分配身份，不能直接改判
为复用。v12于2026-10-09 22:08主动停止以修正该测量问题。
proof_version=2以同一ACK确认的FULL分配、同一节点版本和服务
物化前缀覆盖确认复用；保留原生分层统计，不改变调度行为。
下一轮为v13，继续相同workload、模型、预测产物和容量采集；
该次修复仅改变复用计量，旧部分轨迹只作机制分析。
v13已以aa93dde冷启动采集。独立工作树进一步修正离线重复恢复
审计：合并批次内按逐操作receipt保留未打标签的原生加载，区分
后续加载FULL或Mamba及与预取池的交集。纯原生批次缺少逐操作
receipt时仍标记legacy_batch_pool_presence，不能把关联数当成
精确重复字节。报告保留FULL复用proof版本，旧记录不补判。
该审计修订不改正在运行的v13调度代码或冻结报告。

## PREPARE 热点与 Handoff 身份修复

本次修订基于 `e985d8c`，在独立工作树
`/tmp/beliefkv-opportunity-20261009` 完成。v10的采集、HTML导出、
对比和workspace清理均已结束，修订代码用于后续实验。
v10保持本轮冻结版本 `e985d8c`，不能将新修复计入其性能结果。

v10最终累计CPU计时显示，JOIN PREPARE exclusive区间为
1480.695秒，机会采样为338.871秒。handoff选择9419次，实际发出
0次，全部以 `resident_or_unavailable` 收场。该名称混合了已驻留
与观察/规划失败，不能解释为9419次都已驻留，也不是GPU时间。
最终证据：v10目录的 `comparison.json` 与 `native_policy_comparison.json`。
早期快照保留在
`experiments/reports/qwen35_v10_predictive_hotpath_partial_20261009.json`。

查明并修复一个可复现的身份类型缺陷：原生统一树的creation_time
来自 `numpy.float64`，request reentry路径直接传给只接受Python
float/int的恢复规划器。session快照路径已做转换，该路径遗漏了。
现在以已有的 `normalize_native_creation_time` 统一转换，保留数值、
节点代次、session和epoch校验。相同Host-only输入的真实CPU闭包/
恢复规划对照中，旧版选择1次、提交0次，新版选择1次、提交1次。
提交使用替身，没有真实DMA/ACK，不能作为复用或吞吐证据。
新增 `execution_handoff_no_step` 明确记录失败原因和blocked_detail。

PREPARE维护改为每次集中刷新租约，再按context身份校验一次；
只在单次同步检查中复用结果，不跨scheduler轮缓存驻留状态。
已备份节点登记复用刚读取的闭包，路径深度与累计前缀计算避免
逐节点回溯到根。机会采样的D2H/H2D观察共享同一份闭包，真正
提交动作时仍重新读取并由native验证，不把采样结果当作授权。
新增候选维护、候选发布和已备份节点登记的exclusive计时。

156 workflow、24节点祖先链、16轮历史、30次CPU测量中，已有
Host副本且无租约的JOIN PREPARE均值26.305→15.075 ms，下降
42.69%；四个活跃租约时176.056→12.658 ms，下降92.81%。
实际备份候选及Host空闲不足样例下降3.74%/4.17%；机会采样下降
30.07%–35.76%。四种样例的备份选择、压力候选发布与采样目标一致。
报告：`experiments/reports/prepare_path_cpu_156_24_20261009.json`；
64节点压力样例中，已备份无租约/四租约的PREPARE下降48.25%/
93.54%，未备份/Host不足样例下降11.30%/11.41%，采样下降
36.23%–43.27%；另存 `prepare_path_cpu_156_64_20261009.json`。
相关CPU回归358 passed。以上不证明GPU吞吐已经超过native。

后续仍须确认修复后的handoff能转成实际FULL复用，减少需求恢复
暴露等待，并计入ACK到服务、驻留和其他请求延迟。

v10 predictive为156 completed，native为154 completed/2 incomplete；
完成吞吐46.054/53.589 workflow/h，predictive低14.06%，GPU利用率
63.681%/73.629%。实际输出量只多2.06%，不能仅用轨迹差异解释
性能差距；单对跨版本live实验仍不能隔离策略因果效果。
PREPARE_HOST为35713次/144.536 GB，等待agent压力释放60次，
其中59次可关联此前PREPARE ACK；这只计特定释放路径，不能把
其余备份全部算作浪费。预测H2D为106次/7.374 GB，其中FULL
1.321 GB、确认首次复用0.720 GB；ACK到首次服务P50为3.245秒，
31个目标随后又在首次服务前发生原生加载。新的handoff实际消费、
PREPARE后续消费、恢复就绪准入和重复恢复仍是优先验证项。
原生逐层依赖等待抽样均值两侧约0.375/0.377 ms，不代表全部
恢复等待或oracle上界；单纯增加传输量不足以证明吞吐收益。

服务端在全部workflow完成后收到SIGTERM，随后再次执行终态缓存
采样，访问已被close设为None的writer而报AttributeError。这是
退出生命周期缺陷，客户端和父driver均正常结束；保留原始堆栈，
不将本次数据误报为采集中断。close现在清空终态观察任务，关闭
writer后跳过采样；CPU回归覆盖close后的scheduler迭代、重复close、
最终记录与writer drain，相关89项通过。

## 迁移机会与实际准入协同

基于 `859d138` 的剩余优化已在 `perf/opportunity-aware-transfer`
实现，模型权重、阶段阈值、harness及prompt保持不变。runtime根据
实际迁移与准入条件选择动作，不要求离线模型预测净收益。

ACK后驻留锁不再无条件限制为4把/1 GiB：读取native的running、
请求pool剩余行数和下一批prefill名额，预留运行请求的页增长、
下一批input及Mamba slot，只将剩余FULL/Mamba空闲字节用于新保护。
满decode batch最多保留一个frontier恢复候选，不能替代原生准入。
容量不可观测时回退旧4把/1 GiB。恢复就绪较多时，每1至4次普通
准入可提升一次恢复请求；10秒老化仍有效。真实NO_TOKEN优先释放
非当前请求、尚未就绪的实际锁，软跟踪记录不能充当可释放锁。

JOIN/tool PREPARE每次轮转最多8个候选，优先备份能释放受压池
空间且传输较少的对象；Host空闲不足时不为备份驱逐已有Host副本。
有实测D2H样本时，过滤剩余工具窗口不足完成备份与恢复的对象。
已有FULL Host副本、没有Mamba的节点也登记为可回收冷副本。
传输范围仍为有效缺失FULL前缀与必要的最新Mamba检查点。

JOIN/tool预取启动窗口由实测H2D submit到ACK P90、enqueue到submit
P90及100 ms观察间隔构成，保持冻结的500 ms提前上限。
未观测到child近期服务时，不沿用旧生成速率提前占用HBM。
预测中心已被实际生成超过时改用仍有效的上界；上界也被超过则
等待新forecast或EOS，不再把越界夹成“仅剩1 token”。
forecast日志记录实际生成进度、服务间隔和有效统计量。

旧v8d诊断中，15个工作量触发的RETURN提前量P50为9.472秒，
EOS/RETURN有符号误差P50为-5.047/-9.094秒；其中心与上界越界
计数均为0，不能将上述边界缺陷解释为这些早触发的根因。
在相同历史快照中，实测服务窗口会使13/15次继续等待；这不证明
推迟后的触发精度或GPU收益。1094次预测ACK到首次服务P50为
8.167秒，FULL确认复用71次，旧记录中1060次租约没有实际native锁。
报告：`experiments/reports/native_transfer_policy_v8d_20261009.json`。

相同156 workflow/16轮历史CPU输入，基于 `859d138` 的两次准入
规划均值3.597→3.602 ms，增加0.13%，队列顺序相同；样例没有活跃
物理传输和驻留锁，不代表PREPARE全路径成本或GPU吞吐。
报告：`experiments/reports/opportunity_policy_cpu_156_16_20261009.json`。
相关回归290 passed、1 skipped；引擎patch未改。

v10 native已收尾，154 completed/2 incomplete，采集窗口
10345.487秒。父驱动曾因HTML导出传错arm目录而停止，predictive
当时未启动；目录错误已修复，native HTML已生成。新
`scripts/resume_semantic_h2d_ab.py` 保留native源码/补丁、原始计划、
task与到达表，只更新尚未启动的predictive版本后接续；不重跑native。
接续前仅清理完整归档的completed workspace，保留未归档改动和
失败证据。此轮仍是跨版本单对开发实验，GPU收益待实测。

首次以 `04812ec` 接续时，在真实Mamba PREPARE的before-enqueue
回调发现 `shadow_expectation_from_native_op` 引用了H2D函数内部
的 `pool_name`，导致NameError、Mamba备份被拒绝并反复输出异常；
FULL-only备份仍能完成，`physical_disabled=false`未反映这一故障。
已停止该次predictive并保留现场，不纳入性能对照。修复D2H函数
自身的enum/string池名解析，现有CPU样例补上真实Mamba
`pool_transfers`及Host目标索引，145项相关CPU回归通过。
修复提交 `e985d8c` 后冷启动的predictive已正常完成，退出状态0。
旧 `arm_status.txt` 的早期非零项是失败启动残留；本次状态应结合
summary、最终driver状态和进程判断。已完成native不重跑，模型
与原始workload配置保持冻结。

## Handoff 流水恢复与准入减负

当前优化在独立分支 `perf/pipeline-execution-handoff` 中完成，
实现提交为 `e5751e6`，基于 `e5f1f0b`。按用户最新要求，
v10 native维持原版至收尾，随后直接部署最新提交运行predictive，
不重复native。保留原始 `ab_plan.native_frozen.json`，
`arm_revisions` 分别记录两侧源码和引擎补丁；workload、到达表、
模型和pool配置保持一致。这是跨版本开发对照，尚无GPU收益结论。

推荐优化的完成范围：批量恢复、依赖准入和同轮因果分类复用已完成；
下一批容量预算、PREPARE筛选与服务窗口选择已由本文顶部修订补齐。
剩余工作预测头尚未重训，预测精度和端到端收益仍需实测验证。
不将CPU减负或ACK数量等同于完整优化方案已取得GPU收益。

旧handoff逐node发出H2D，再等完整软件ACK推进，50 ms检查间隔
还会继续拖延下一步。新路径一次规划最多16个缺失FULL node，
按祖先到检查点顺序入队，合并一次原生submit；每node仍有独立
command、容量账本和ACK。只在当前可复用检查点携带必要Mamba，
不扩大到历史状态或生成尾段。空闲容量不足时先恢复能放下的
连续前缀，剩余部分由后续handoff或原生需求恢复处理。

FULL-only恢复可以在原生逐层load fence下进入prefill，不等待
软件账本ACK。Mamba的deferred COW早于模型逐层等待，必须先确认
该次传输自身finish event完成；不拿复用的layer-ring event作证明，
也不CUDA synchronize。缺少原生依赖支持时沿用ACK等待。
50 ms回退只用于重新选择候选；活跃ticket的真实ACK后立即推进。
无关原生ACK不重置候选扫描节流，避免传输越多CPU扫描越频繁。

同一scheduler轮内，handoff和正式准入共享因果分类；请求顺序、
身份、图版本或语义revision变化则失效。驻留、老化、预测有效期
及提升预算仍实时检查。同次物理观察中FULL/Mamba锚点相同则只
遍历一次祖先链，提交前仍由native验证代次、祖先、在途操作和容量。
真实ACK按最深node先注册保护，一把锁覆盖FULL祖先，不为每段
重复加锁；该版本采用4把实际原生锁/1 GiB，最新容量预算见本文顶部。

原生流水可能先服务请求、后交付软件ACK。因此在issue时保存实际
allocation身份，首次服务证据暂存，验证ACK且身份/字节吻合后才
发布复用记录，并标记 `first_service_before_ack`。已服务动作不在
ACK后补锁；超时、因果镜像丢失或物理失败清理暂存证据，不能算命中。

相同CPU输入对照 `e5f1f0b`：156 workflow、16轮保留历史、80次
测量、每轮连续两次准入规划，平均5.591→5.180 ms，减少7.35%，
队列顺序一致。它不包含物理handoff入队/观察，也不是GPU加速。
报告为 `experiments/reports/pipeline_handoff_cpu_156_16_20261009.json`。
主仓库相关回归319 passed，独立patched engine104 passed及
16 subtests passed。后续验收仍看完成吞吐/JCT、暴露恢复等待、
FULL实际复用与重复原生恢复，不能用合并submit次数代替收益。

## 共用路径减负与新对照

按最新要求，先降低并测清reactive/predictive共用路径成本，再进行
同配置native/predictive对照。本次不新增agent guard、canary或开发
重复实验，不修改模型权重、fanout与自然语言RETURN契约。

准入规划只分类当前最多512个候选，不再每次遍历全部workflow的
历史invocation及后代。JOIN最后成员/等待者和活跃工具统计按图
版本缓存；没有恢复租约时，batch完成回调跳过逐请求身份解析。
相同CPU输入与旧commit `7055901` 的对照保持队列顺序不变：
108 workflow、2轮保留历史、80次测量时，准入均值10.722→1.365 ms，
降87.3%；开启profiling为1.387 ms。156 workflow、16轮历史压力样例
为120.137→2.522 ms。两者都是合成CPU对照，不是实测GPU加速。
结果保存在 `experiments/reports/native_shared_path_cpu_*.json`。

新增累计inclusive/exclusive Python计时，分别记录控制事件交付、
图更新、维护、准入、语义更新、PREPARE/H2D、handoff和候选检查；
每秒随已有状态发布，不逐调用写盘，不同步CUDA。exclusive用于
避免嵌套重复计费，仍不是CPU周期或全部GPU空闲时间。
cache-mode、session radix和原生load fence均存在时，预测H2D
使用不回收Device页的原生异步加载，不再为工具/JOIN预取drain
无关decode；缺少适配能力时沿用原安全点处理。

对恢复和执行脱节，保留latest Mamba、缺失FULL extent、真实ACK
锁、有界恢复就绪优先级、提交宽限与execution handoff，重点验证
首次复用、ACK到服务、再次原生加载及驻留成本，而不扩大恢复范围。
native/predictive均新增低频只读GPU等待计量：每16个实际消费
加载依赖的prefill抽样一次，在计算流的原生逐层等待前后记录CUDA
event，完成后异步读取，跳过CUDA graph捕获。记录包含event成本，
是抽样batch依赖等待，不是全部H2D耗时、逐请求排队或oracle JCT。

本次已授权冻结的配置为108任务t=0到达，48个不重复train任务
t=3600秒到达，共156个。manifest保留历史首108任务顺序，新增任务
来自train Django；第二波项目构成不同须单独报告，不能宣称IID稳态。
两侧共享到达表、Host200 GB/FULL:Mamba=80:20、HBM Mamba/FULL=0.9、
running48、context131072/completion8192、graph2048/FINALIZE reserve32、
workflow14400秒和tool600秒。先native后predictive，独立冷启动，
固定源码/补丁/模型指纹；没有旧auto对照替代新native。
native关闭BeliefKV控制与调度，保留相同harness和只读遥测。
实验结束自动导出HTML、分段对比和全程完成吞吐/JCT；仅清理已有
完整归档的completed workspace，保留trace、patch和错误证据。
这是单对开发实验，不是正式多轮统计结论；吞吐未提升须明确报告。

manifest：`configs/migration/qwen35_native_predictive_replenished_108plus48_2026-10-09.json`。
主仓库定向回归257 passed；实际patched引擎源码回归99 passed、
16 subtests passed。GPU性能验收待本次对照，不以测试通过代替收益。

## v8c / v8d / v9 回溯对比

三轮均为同一108任务、单波到达，running=48，Device FULL/Mamba
36.843/33.096 GB，Host FULL/Mamba 105.358/94.652 GB。历史Host为
auto（约52.7:47.3），不是下文新默认80:20。v9是共用兼容补丁上的
原生FCFS/HiCache策略，不是未修改的上游wheel。

| 指标 | v8c reactive | v8d predictive | v9 native |
| --- | ---: | ---: | ---: |
| completed / incomplete | 108 / 0 | 107 / 1 | 108 / 0 |
| 运行窗口（秒） | 9675.252 | 9394.633 | 8087.675 |
| 完成吞吐（workflow/小时） | 40.185 | 41.002 | 48.073 |
| 输出吞吐（token/秒） | 546.680 | 619.778 | 713.107 |
| GPU平均利用率 | 55.33% | 66.07% | 74.80% |
| completed平均JCT（秒） | 3697.628 | 4125.444 | 4011.883 |
| completed P95 JCT（秒） | 6924.241 | 7996.907 | 6920.779 |
| 原生H2D（TB，十进制） | 2.473 | 2.649 | 2.992 |
| 多轮spawn workflow数 | 11 | 17 | 20 |

v8d相对v9完成吞吐低14.71%、输出吞吐低13.09%、运行窗口长16.16%。
两轮输出总量为5,822,589/5,767,374 token，仅相差0.96%；native的
输入token、请求及工具调用反而更多。不能以“native工作少”解释全部
差距。但completed平均JCT仅高2.83%，P50反而低2.30%，不能说每个
workflow都更慢。v8c物理通道失效、v8d改变shell反馈、v9改变调度，
且三轮均为live生成；这些数值是回溯诊断，不是隔离变量的因果加速。
completed只是终态记录，不等于独立任务正确性评测通过。

当前证据支持三类问题，不能全部归因于预测数学模型或轨迹分歧：

1. 预取未转成足够的有效FULL复用。v8d传FULL 7.544 GB，确认复用
   0.668 GB；ACK到首次服务P50 8.167秒，旧策略仅1.5秒软租约，
   724次失去原生驻留、168个目标随后再次需求恢复。未证明的Mamba
   字节仍属未知，不能把全部未验证传输当浪费。
2. 服务/控制路径存在额外耗时。v8d/native工具结束到同invocation
   下一请求提交P50为157.991/93.003 ms；prefill worker区间累计
   1743.657/1226.401秒，单batch均值56.682/37.697 ms。30–40分钟
   的decode时间加权batch为47.088/47.057，GPU为58.31%/67.03%，
   prefill区间为106.938/69.483秒；高压段不是单纯batch变小。
   这些是worker/客户端区间，不是kernel或单函数CPU计时。旧版
   同步控制交付、1114次工具drain、438651次native动作拒绝是
   候选开销来源，不能未经profiling就把额外517秒分摊给某个函数。
3. 单波负载有很长的低迁移尾段。native全部H2D的98.820%在前50分钟、
   99.779%在前60分钟发生；v8c/v8d前50分钟比例为99.490%/99.676%。
   native H2D CUDA transfer-stream累计88.625秒，submit到ACK累计
   731.751秒；两者都不是可直接从JCT扣除的暴露等待或oracle上界。
   有大量传输字节不代表存在同等比例的端到端可隐藏时间。

native在50/60分钟仍有75/62个活跃workflow，53/40个有未完成JOIN；
不能说此时child已全部结束。30–40、40–50、50–60、60–70分钟的
原生H2D分别为706.851、229.795、28.684、2.687 GB，队列均值为
63.213、24.767、2.732、0.539。旧输入共同前缀缺失代理在这些窗口
约0.14%–0.15%，未出现足以解释迁移消失的大幅FULL重算升高；
归因索引到期和Mamba位置不完整，仍不能证明重算上限。
FULL active token下降不是完整HBM占用率，Host FULL近满也可能是
保留冷缓存，不能据此宣称所有池一直处于有用数据高压。

候选后续配置是固定t=0到达108任务、t=3600秒再到达48个不同任务，
将原生当时62个活跃root补至约110个；64个第二波保留为更高压力候选。
三侧必须使用同一绝对到达表，不能按各自完成数动态补任务。
这可能延长迁移密集期，但不修复低复用/控制开销，也不保证收益；
新Host80:20的压力不能照搬历史auto结果。此前提出的108+48现已
授权并完成独立train manifest及到达支持，见本文顶部。
有限第二波仍有排空尾段，预先约定窗口
指标与全部workflow完成指标，不能事后挑选有利区间。

机器可复核结果为
`experiments/reports/qwen35_v8_v9_policy_comparison_20261009.json`，
由 `scripts/compare_native_policy_runs.py` 按真实ACK分离原生/预测
字节并守恒，按HTML首个GPU样本对齐时间。7个聚合测试通过。

## 当前恢复协同与池配置

原生v9已结束，108/108 workflow completed，运行窗口8087.675秒；
原生HTML导出完成后，独立工作树中的检查点、恢复保护和事件
交付修复已合入主目录，并以增量方式部署到本机patched SGLang。
实验运行期间维持了启动源码指纹。以下新策略尚未跑GPU对照，
不能据此声称吞吐提升或RETURN预测精度改善。

Mamba只保留本context最近一次完成请求的可复用安全输入检查点
引用；旧状态通过原生session引用接口降为普通缓存，不强制删除
其他context共享、锁定或在途状态。PREPARE/H2D按FULL祖先路径
补缺失前缀，只在选定检查点携带必要Mamba；组件mask在构建传输、
分配和提交之前生效。原生需求恢复默认仍携带必需状态。
同context/epoch/checkpoint的在途预测恢复不重复发射。
这不是“全局只允许一个Mamba物理slot”，也不是禁用Mamba Host。

工具实际返回或ALL JOIN真正解锁后，已锁定且未过期的恢复租约
可跨越最多3秒提交空档；真实下一请求提交后总时限仍为ACK后
最多10秒。预测变化不延长、到期不复活，首次服务和真实NO_TOKEN
释放。恢复与收尾优先级共享每4次普通准入最多1次提升，覆盖
有界512候选，普通请求等候10秒后按老化顺序获得执行机会。
不按root身份无限插队。新增就绪、提交、优先准入与队列耗时遥测。
排队控制sink中TOOL_END/RETURN/JOIN_SATISFIED/LLM_SUBMIT异步FIFO
交付，callback不再逐次等控制ACK；session退休RPC仍同步，
不能将全部工具返回到提交开销宣称已消除。

按用户指定，后续实验Host默认FULL:Mamba=80:20，不沿用75:25
建议。200 GB名义预算中约160 GB FULL、40 GB Mamba，当前模型
每slot约64.39 MB，可容纳约621份状态；实际容量以启动census为准。
结束child的无用状态引用释放后由原生缓存回收，不能删除其他
context共享状态。显式Host auto仍沿用Device字节比例；HBM的
Mamba/FULL=0.9不改。80:20是新实验配置，不是已测出的最优比例。
workflow语义仍决定预取对象和时机，native有效前缀、缺失
page/extent和必要状态决定物理范围，两种粒度互补。

新增调度前execution handoff：在batch选择前按因果优先级挑选
已经提交、尚未服务的下一请求，读取真实输入与session安全检查点，
提前恢复缺失FULL祖先和当前必要状态，不依赖旧动作收益预测头。
同因果层级中完整HBM检查点优先，其次部分HBM命中；未观测
不能当作命中。JOIN/收尾恢复优先级及10秒老化仍保留。
reactive与predictive共同启用resident-first，只有predictive开启
调度前H2D；原生策略基线关闭两者。

容量不足时只回收真实Host ACK已完成、未锁定的冷等待agent副本
或无引用缓存副本，不迁出正在执行的热KV，不计入在途D2H的
预期释放量。D2H/H2D分方向限制在途动作，允许不同节点的备份
与恢复重叠；实际是否隐藏DMA仍须GPU遥测证明。每次只选一个
短期beneficiary，规划窗口2秒、最多16个node；ACK后驻留保护
原先采用4把实际原生锁/1 GiB，现按本文顶部的实际准入容量分配；
队列换入租约最多3秒，首次服务释放。
新路径的FULL走原生逐层依赖，必要Mamba或旧适配器等待完成；
只跳过尚不能安全准入的对应请求，不停止其他可执行请求；失效和窗口
耗尽回到原生需求恢复，不持续重新选择同一请求。
动作source=execution_handoff单独记录，已提交之后的H2D不能
冒充RETURN/TOOL_END之前的预测命中。验收看FULL实际复用、
重复恢复、原生H2D减少量、排队与吞吐，而不是提前传输字节越多越好。

检查点/恢复保护的先前回归为539 passed、16 subtests passed。
本次主仓库定向回归346 passed，patched engine回归104 passed、
16 subtests passed，共450个测试及16个子测试。canonical补丁对
固定上游应用和当前引擎反向校验通过，真实引擎/runtime导入通过；
迁移字节减少和端到端吞吐仍未验证。

## 当前修复与原生策略对照

按用户要求，2026-10-08 23:07启动同一manifest前108个任务、单波
到达的原生SGLang FCFS/HiCache策略实验，启动commit `f188443`。目录为
`experiments/raw/qwen35_native_policy_108root_2to4_20261008_v9/native`。
关闭BeliefKV admission、控制socket、PREPARE、预测H2D及收尾优先级；
保留相同harness、通知、自然语言返回、首轮2–4原生生成约束、
session/NUMA兼容补丁和只读遥测。这是原生策略基线，
不是未打补丁的上游wheel，也不是只关闭H2D的BeliefKV reactive。
running48、Host200 GB/NUMA1、Device Mamba/FULL字节比例0.9、
Host跟随Device字节比例、context131072、completion8192均不变。

本次代码修复：ACK后取得原生树锁receipt，最多4个租约、1 GiB
祖先闭包；预测期仍仅lead+1秒。只有同身份的下一请求真实提交后，
已锁定租约才延长至ACK后最多10秒；首次服务、失效、到期或真实
NO_TOKEN压力释放。恢复请求与收尾请求共享每4个正常准入后最多
1次提升的预算，不能靠长期pin或插队全部请求制造收益。
原生receipt解锁失败保留证据、停止物理动作，且不盲目重试解锁。
工具ETA变化不再撤销已ACK且仍属于同一等待episode的恢复。

Mamba证明改为native COW源/目的对象与request对应关系，
在非speculative extend forward完成后确认，支持多请求batch。
旧14次仅是singleton证明下界，不是实际Mamba复用总数；
v8d原生遥测另有10149次Mamba Host hit，不能据此关闭状态恢复。
JOIN时间投影改用500ms/2s/5s已观测墙钟速率中的保守值，避免
将短decode burst当未来服务份额。尚未重新拟合剩余工作头，
不声称RETURN精度已改善或亚秒级目标已达到。
相关CPU回归325 passed/1 skipped，staging补丁反向校验通过。
启动后冻结源码、prompt及权重；启动健康不等于性能收益。

服务端预检实测session=true、BeliefKV admission=false、socket=null、
FCFS、priority=false。CUDA graph覆盖batch48；Device FULL/Mamba为
36.843/33.096 GB，Host为105.358/94.652 GB。初始核查108个workflow
均已开始，58个已观测首轮派发组全部双child，遥测dropped/failed=0。
以上为启动快照。该轮现已108/108完成；最新回溯比较见本文顶部，
尚无函数级开销或轨迹匹配的因果归因，不把运行窗口当作策略收益证明。

v8d新增只读forecast核对：135条请求首次跨阶段阈值，其中101条
是child自然RETURN的最终请求，34条不是最终请求。101条最终请求
首次跨阈值的剩余token有符号误差P50=-134.02、绝对误差P50=172.77；
最后一个native EOS前快照为+40.96/45.05 token，真实剩余P50=9。
早期低估与末段高估同时存在，不能用全局平移修复；
EOS后快照的零剩余标签不能代替EOS前精度。这是同轮开发诊断，
不是独立验证、完整JOIN墙钟误差或已上线新模型。

## 离线 HTML 时间轴

v8c reactive 与 v8d predictive 已通过
`scripts/render_p6_execution_timeline.py` 导出到
`experiments/reports/qwen35_v8_timelines_20261008/`，文件分别为
`v8c_reactive_execution_timeline.html` 和
`v8d_predictive_execution_timeline.html`。HTML 内嵌压缩数据，可独立
打开；原始聚合数据另存 `.json.gz`，不复制 raw 遥测。
新版离线适配直接读取 scheduler/worker 服务区间、控制器传输 ACK、
client 工具/JOIN 事件及 FULL/Mamba 分池观测；无 Device occupancy
证据时显示未观测，不能将 FULL active use 当作两池占用率。
submit-to-ACK 重叠不等于 DMA 被完全隐藏，PREPARE ACK 不等于消费。
v8c 的物理通道失效已在 HTML 中注明，不作为公平加速基线。

原生v9的 `native_v9_execution_timeline.html` 已在相同目录生成，
覆盖108个workflow，并保存同名 `.json.gz` sidecar。冻结采集期间，
新版导出器及策略在 `/tmp/beliefkv-policy-20261009` 独立开发，
自动导出从该工作树读取日志，未改运行配置或源指纹。导出完成后
已恢复主目录离线导出器，不再依赖运行中的导出watcher。

## v8 Predictive 最终结论

v8d于2026-10-07 23:47结束，107 completed/1 incomplete，耗时
9394.63秒，完成吞吐41.00 workflow/小时；JCT P50/mean
3727.62/4125.44秒，GPU平均66.07%。物理通道全程未禁用，
receipt failure/遥测丢失/写入错误为0，旧D2H split问题未复现。
GPU在v8d结束后已释放。当前新增实验以本页顶部计划为准；
下面“最新诊断与启动”是历史记录。

真正预测H2D为1094次/65.623 GB：JOIN 46次/3.306 GB，工具1048次/
62.317 GB；原生H2D仍8596批/2.649 TB。所有预测ACK均有首次服务
记录，但FULL目标复用只有71次（JOIN22、tool49），确认复用
0.668 GB/所传FULL 7.544 GB。Mamba只确认14次/0.901 GB；
当前证明要求单请求prefill batch，其余57.178 GB是未验证，
不能全算浪费。预取占总H2D约2.42%，不能用ACK数量称成功。

核心时机问题：19次estimated_work/EOS前JOIN预取，实际提前量
P50 12.968秒，0次落在RETURN前1秒。27次observed_no_tool_eos
P50 0.899秒，9次落在前500ms、12次在前1秒；这不是模型预测成功。
例如django-11400预测剩24.28token/322.94ms，后续输出计数从570
增至793，约15.56秒才到EOS；剩余工作和实际服务节奏均需核查。
Tool有685/1048次在TOOL_END前1秒，但FULL复用仅49/1048。

核心联合管理问题：ACK到首次GPU服务P50 8.167秒（JOIN10.759秒、
tool8.002秒），而策略租约只有1500ms且不是allocator pin。
1060个租约中724次native_residency_lost、244次prediction_window_left、
80次到期、12次在首次服务释放；168个目标又被native H2D恢复。
issue到submit P50 2.33ms，旧发射延迟已不再是主要问题。
不能只延长全体租约：须联合恢复预算、准入及有界驻留，并区分
需求已就绪、预测变化与native LRU。源码检查点见runtime的
`_prefetch_lease_invalid_reason` / `_register_prefetch_service_lease`。

PREPARE ACK11885次/40.047 GB，全部FULL；自定义parent pressure
demotion为0，现有prepare_consumption为空。不能据此说全部备份
无用（native eviction也可消费），但尚无净收益/原生消费归因。
Host两池均满，FULL/Mamba累计驱逐1.084/1.151 TB；输入token
命中95.28%，旧输入缺失代理0.152%。FULL确认驱逐后重算35882
token，但28103条索引到期、Mamba位置未知，不能当完整重算上限。

Workload仍91个仅一轮、16个两轮、1个四轮；后续11个单child组。
incomplete是pytest-6197 root反复输出同段分析后finish_reason=length，
不是结构化终态门禁、deadline或child取消；它的JOIN已满足。
v8c物理通道失效且v8d改变pipefail等反馈，不能将表面吞吐+2.03%
作为公平加速。相对旧R平均JCT反而+11.57%，输出token+10.08%。
下一步优先修正JOIN工作/时间投影及恢复后的准入/驻留协同，
补batched Mamba和native PREPARE消费证据，再补同版本reactive。

## 最新诊断与启动

v8c reactive 已于2026-10-07 19:45结束，108/108 completed，
原生遥测无丢失/写入错误，所有119个JOIN满足、232个child返回。
但它不是完整工作的PREPARE baseline：17:05:16第18次D2H的
Radix节点37在途拆为535/37，账本只允许原发布集合37，
错误触发 `child publication does not match native ACK`。
随后物理动作全程禁用，仅17个PREPARE获得ACK，原生HiCache仍运行。
原生迁移/缓存和agent轨迹可作诊断，不可据此声称完整策略对照有效。

总耗时9675.25秒，吞吐40.19 completed workflow/小时，平均JCT
3697.63秒、P50 3628.62秒，GPU平均利用率55.33%。最后一个
django-11734耗时9642.11秒，反复全量测试构成长CPU长尾；workspace
初始及最终HEAD均为要求的base commit，不支持模型“HEAD更新”的说法。
H2D 8382批/2.473 TB，CUDA-event累计73.14秒、submit→ACK累计
825.29秒；D2H 2.234 TB。ACK等待不是可直接减去的oracle JCT。
11/108多轮且仅两轮，后续6个单child组；不补造或取消这些模型输出。

修复保留失败关闭机制，仅用native树祖先关系、原anchor代次及
原始Host目标索引确认D2H拆分后的发布集合，并仍校验字节、
pool、session/epoch和重放。忙碌writer改为定时发布状态；
测试形态不再把管道过滤器/选项参数当作测试标签，shell采用
bash pipefail反馈上游失败。不新增agent guard或短预算。

已结束的运行是全新目录
`experiments/raw/qwen35_joint_wait_h2d_predictive_108root_2to4_v8d`，
修复后的108-root单波predictive机制诊断，保留v7头、500 ms/
250 ms窗口、running48、Host200 GB/NUMA1和0.9池比例。
不自动重跑reactive，不将v8c和v8d解释为公平配对或预测独立加速。
严格比较须后续补同修复版本reactive。以下v8c启动记录作为历史证据保留。

v8d启动commit `0f04f70`；首轮108条实际全部双task，检查时
340次PREPARE issued/ACK均已完成、physical_disabled=false、
receipt failure为0。语义worker ready，JOIN/tool prefetch均启用，
模型SHA与v7冻结产物一致。相关回归395 passed/1 skipped。
旧reactive的108个已归档workspace已清理，trace/patch保留。
v8d期间再次冻结代码、prompt、模型及参数，不把启动健康当收益证明。

本文是当前实现事实的权威入口，不是逐日开发日志。当前主线已是
Qwen3.5-35B-A3B BF16 / SGLang 0.5.20，不再以旧 Qwen3/SGLang
0.5.2rc1 的 P6 能力描述代替新版事实。迁移前关键节点为
`checkpoint/pre-sglang-model-upgrade-2026-09-22`；本页重整前的
详细开发记录保存在 Git 的 `c219604:docs/architecture_status_zh.md`，
更早记录保存在 `docs/archive/snapshots/architecture_status_zh.md`。
历史实验目录、trace、模型和失败证据没有因本页重整而删除。

## 1. 当前结论

1. 新架构的动态 root/child、工具、JOIN、上下文和原生 session
   身份链已接入。FULL/Mamba 必须共同管理，物理容量由 native
   allocator 和 UnifiedRadixCache 决定。
2. 有界 PREPARE_HOST 与 JOIN H2D 原生事务已实际运行，不再是
   默认关闭且无 GPU 证据的阶段。v6有7个JOIN H2D ACK/
   0.533 GB，全部FULL首次复用，6个Mamba forward复用确认；
   工具H2D为0。ACK本身不是收益。
3. 工具时间模型已独立上线到开发配置，不更改旧模型产物
   `online_eligible` / `predictive_action_eligible` 标志。
   v5工具H2D已下发6次，但都在ACK后被自身pressure parking
   再回收，后续仍需native H2D。代码已统一条件时间分布并加入
   ACK后的有界策略租约。v6的7个JOIN目标没有自身再次回收/
   原生重载；工具没有新动作，不能声称该链路已获GPU验证。
4. 尚未证明端到端吞吐净收益或普遍亚秒级RETURN预测。
   v6 predictive完成吞吐高4.62%、平均JCT低8.01%，但LLM/
   工具/输入/输出量都更少，84条请求序列均不同。所有动作仍在
   native EOS后启动，不能认定语义预测或预取的独立净收益。
5. v6的84-root单波对照已结束，两侧84 completed，measurement
   valid均84、guard干预计数为0、serving writer无故障。
   每侧另有8个workflow触发允许的2048步提前32步FINALIZE；
   guard计数不包括它，不能声称全部无干预。
   工作头CPU拟合/回放及独立观测EOS路径修复已完成；
   v7已结束，reactive83 completed/1 incomplete，predictive84 completed；
   12个JOIN H2D全部FULL复用，其中3个在EOS前启动，工具H2D仍0。
   当前v8c改为用户授权的108-root单波/每轮2–4 child，
   保留500 ms center/250 ms协议窗口，不排额外重复。
   正式阶段再多轮平均，不要求固定需求回放。
   首次v8尝试因首轮auto绕过委派已停止，不用于2–4对照。
   本机命名task约束只允许一次调用；可重复语法修复后的v8b
   仍108条全单child，也已停止。v8c首轮原生生成范围2–4，
   不拒绝回复/补造child；后续轮次保持prompt驱动并审计实测。
   v8c在 `c744461` 启动；108个root首轮已全部实际派发双child，
   不是只修改配置。v8c与v8d均已结束，结论见本页顶部。

## 2. 当前配置

| 项目 | 当前主线 |
| --- | --- |
| Conda | `beliefkv-next`，agent/serving/实验共用 |
| 模型 | Qwen3.5-35B-A3B，权重与FULL KV均BF16 |
| Serving | SGLang 0.5.20，单GPU/TP=1，pinned upstream `94602c9` + staging patch |
| Device | FULL 1,798,995 token，约36.843 GB；Mamba 513单位，约33.096 GB |
| Host | 200.010 GB，NUMA node 1；FULL约105.358 GB、Mamba约94.652 GB |
| 池比例 | Device `mamba-full-memory-ratio=0.9`；Host匹配实际Device字节比例 |
| GPU执行 | running=48，chunked prefill=4096，CUDA graph覆盖decode batch 48 |
| 当前负载 | v8c：manifest前108个root单波，与每轮2–4 child联合压力诊断 |
| 生成 | context=131072，completion=8192，temperature=0，seed=21 |
| 预算 | workflow=14400秒，graph=2048，允许提前32步FINALIZE |
| Harness | native_in_graph_2to4，同轮2–4调用、鼓励多轮，自然语言RETURN有效 |
| 当前预测窗口 | 实际动作目标为RETURN/TOOL_END前0-1000 ms，仍独立审计真实提前量 |
| 最近运行代码 | v8c `c744461`；v8 `debe99d` 和v8b `026f650` 已停止 |
| 当前v8c | 沿用v7 log-work头/工具CDF；新并发与fanout下仅诊断，不假定校准有效 |

以上GB为十进制；Mamba单位是完整状态/检查点，不是FULL的一个token。
池usage、物理occupancy、可驱逐容量和free-list不得混用。
旧70:30、30:70、180 GB、36-root和64+64配置属于历史实验，不是
当前默认值。默认未启用SSD KV层、KV FP8或新的SGLang版本迁移。

## 3. 控制与预测

### Runtime 与因果图

Deep Agents允许root在自己的持续对话中派发并等待child。
`native_dynamic_1to4` 的外部bootstrap planner不保证JOIN前后
prefix延续，不用于当前主实验。RCCG根据真实事件更新，不要求
应用预先提供完整DAG，不根据自然语言猜测身份。

请求绑定workflow/invocation/context/epoch/request/attempt与
native session/generation。普通LLM轮次推进epoch但不必关闭
session；成功压缩、终态和显式失效释放旧引用。
summary保留调用child的callback祖先链，不再回落到root。
foreground CALL和JOIN的依赖必须都满足后才能唤醒parent。
并行工具按稳定tool_run_id跟踪，首个TOOL_END不提前唤醒agent。

没有重复工具或无进展语义强制终止、completion格式门禁或
固定child数guard。graph安全收尾、真正空响应的一次恢复和
实际执行故障独立记录，不能都声称为自然RETURN。

### JOIN 阶段与剩余工作

线上冻结MiniLM encoder及阶段头，使用已送达的有界正文、
通知、历史工具/轮次和因果有效decode进度。预测当前生成轮
是否为最终报告，以及条件剩余token工作；不是parent首次GPU
服务时间，不学习离线不可识别的预取净收益。

语义推理在独立CPU process；每个正文frame只提交一次，只为
真实Host-only恢复目标的关键child推理。100 ms正文快照与
Linux monotonic clock domain证据已接入；仅同域才取消100 ms
回溯，旧trace不回填新时钟证明。

native `<tool_call>` token提前使该轮终态信号失效，announce
调用轮与其后的最终报告轮分开。前EOS时机采用已有工作上界；
无工具EOS保留50 ms以内的协议窗口。模型的阶段和长度预测
仍不够准确，不能将协议窗口预取都称为准确的语义时间预测。
旧MLP和短token CDF候选没有稳定改善，不作为默认模型。
v6暴露106.25 token的固定上界投影下限；新产物先扩张signed
residual再截断，旧产物保留原语义。当前只改善条件工作头，
用v6真实100 ms观测补训练，并比较log-work分位数校准。
Runtime增加显式center/upper时机选择，默认upper保持旧行为；
候选不等于已部署，零剩余估计不等于native EOS。
正常stop且有非空正文、无工具标记的native观测另建立仅H2D阶段，
短报告不再因NN forecast缺失或TPS不足而完全漏掉协议窗口。
空白/reasoning-only、length、abort和internal不形成此证据，
不改变agent返回行为。日志独立标记observed_no_tool_eos，
不计作模型预测成功。v7工作开发结果见
`docs/experiments/conditional_work_v7_development_zh.md`。

### 工具等待

独立加载 `qwen35_native_event_horizons_20260928_calibrated.json`，
验证权重SHA、训练源manifest、当前模型revision及0.5.20身份。
模型给出残余时间和事件CDF，runtime决定备份、回收、加载。

v4的CDF>=0.8二次否决导致目标候选全部未准入；`1aba1be`改为
配置的剩余P50窗口，H2D准入中CDF保留诊断。但长等待/pressure
parking仍使用条件CDF<=0.1；v5证实其与P50倒计时同时支持相反
驻留决策。新代码从同一CDF按仍未结束的条件逆算P50；
不再把过期点预测clipping到0当作完成信号，不增加CDF>=0.8否决。
经验证H2D ACK建立最多lead+1000 ms的策略租约（当前2秒），
从自身冷回收候选中排除该node，覆盖下一epoch及native session
接力；首次GPU服务、预测变化、终态、过期或原生失驻留显式释放。
它不是全局pin、容量预留或复用证明，native最终回收权保留。
短工具和同一等待有界降频，
时间hint接受不立即扫描物理ancestry，动作选择时才做有界
inspection/cache，enqueue前仍重新验证。
工具/语义两个worker FD都进入idle poller，有等待时有界唤醒。

## 4. 物理数据面

| 能力 | 当前事实 |
| --- | --- |
| Action-local FULL/Mamba闭包与free-list | 已接入；只读机会不是容量预留 |
| PREPARE_HOST | 原生D2H shadow，ACK前不宣称Host副本有效 |
| 等待态KV回收 | 真实allocator短缺时，dead/cold优先，独占/未锁定/备份已settle才回收 |
| JOIN H2D | WAIT_JOIN且关键child有效，真实Host-only输入、容量与服务证据下发 |
| 工具H2D | v5六个ACK均ACK后再回收；v6为0，不能证明修复后实际收益 |
| 提交队列 | 纯预测load_queue安全点立即启动；不抢混合原生队列producer |
| 同步 | 保留native stream fence、layer event、producer/consumer及同步ACK |
| 首次消费 | FULL前缀/node/value证明；Mamba单请求COW forward证明或明确未验证 |
| 传输模型 | pool shape/相近size的服务估计，enqueue-to-submit与submit-to-ACK分账 |
| Host eviction归因 | FULL精确prefix/区间reaccess；Mamba hit-location仍不完整 |
| 完整COMMIT/JointPlan/handoff | 旧算法不能直接外推，新架构完整联合执行闭环仍未验收 |
| Running retraction | 无完整新版selective release适配，不开放旧全套物理开关 |

H2D源可以来自native D2H或PREPARE，不要求PREPARE先被消费。
恢复必须对应下一输入可复用的安全checkpoint，不使用生成输出
末端。身份、epoch、session或工具/RETURN事件变化撤销旧intent。
partial backup/多node预算不等于完整context已恢复。

旧 `--enable-beliefkv` 全套物理路径仍fail closed；现在可用的是
独立native admission和有界动作路径。这个区别不能简写成
“新版完全没有H2D”，也不能反向宣称完整旧checkpoint算法已迁移。

## 5. 已验证证据

| 开发实验 | 已观察的主要事实 |
| --- | --- |
| v2，64-root | 两側完成64/63；预测H2D为0；summary错归parent阻断WAIT_JOIN |
| v3，64-root | 10个ACK/814.94 MB，8个RETURN前启动，2个迟发 |
| v4，64-root pair | 两側64/64；6个ACK/491.09 MB，全部FULL复用，工具H2D为0 |
| v5，84-root pair | 84/83 completed；17个ACK，JOIN11个FULL复用，工具6个自我回收；吞吐-8.95% |
| v6，84-root pair | 84/84 completed；7个JOIN ACK，全部FULL复用，无再次回收；单轮吞吐+4.62%，需求混杂 |

v4六次动作中，五个是在native EOS后协议窗口触发；唯一前EOS
动作提前约6.59秒，是剩余工作低估。全部早于RETURN不等于全部
满足100-1000 ms或已隐藏同步等待。没有独立patch grading时
只能报告completed-workflow吞吐，不称正确任务吞吐。

v4退化的详细复核写入原有
`docs/experiments/joint_tool_join_h2d_v4_zh.md`，数据在
`experiments/analysis/v4_gpu_root_cause_20261005.json`。
GPU service记录是scheduler/worker墙钟区间，不是kernel时间。
NVML近似积分显示额外约1117秒空闲；主要长尾来自django-16938
两次600秒全量测试。有服务需求时仍存在差距，控制面扫描、
工作量/batch变化和迁移干扰需要分别分析，不能用路径差异一句
带过。现有日志不提供每个CPU函数的独占时间。

v5详细结果见
`docs/experiments/joint_tool_join_h2d_v5_84root_zh.md`。
两侧均有113个child/完整JOIN、29次JOIN后再派发，但每轮child
仍为1。v5十一个JOIN H2D全部在native EOS后启动，RETURN前
提前中位863 ms，6个在1秒内；不能据此声称语义时间预测已准。
工具六次早发3.70-7.11秒，全部ACK后94-1652 ms被回收并再次
native H2D。38次pressure释放均有先前PREPARE ACK，涉及32节点，
包括重复循环，不全部计为收益。
Host两池都达满池，FULL hit约95.5%，块归因大量overflow；
84-root有机会，但不能据此证明有用重算很低或冻结正式负载。
GPU利用率76.50/81.66%，v4式大空转未重现；predictive仍有
约899秒仅两workflow的小batch忙碌尾段，不能以利用率高认定吞吐好。

## 6. 当前阻塞项

1. v6已闭合7个JOIN的ACK到首次服务链，无再次回收/原生重载。
   两个租约过期后仍复用；不把过期视为miss。工具H2D为0，
   仍缺该链路修复后的GPU行为证据。
2. v5只读回放525个采样恢复目标中，新口径有1个near且容量fit，
   旧6次错误早发均不再触发；这不是充分的工具时间精度验证。
   v6的5154次前EOS上界检查全部被过早判断挡住，仍无pre-EOS
   动作。阶段/encoder权重冻结，改进工作头而非改eligibility。
3. 量化exposed restore stall和控制处理开销。
   没有profile的v4不能把差值精确分配到单个函数。
4. harness对SIGKILL/timeout反馈和管道上游失败存在歧义，
   需在后续同配置两侧修复；不在运行中改shell/prompt/timeout。
5. v6多轮workflow为18/13个，但单轮JOIN仍只有1 child；不能靠
   强制取消/返回门禁制造JOIN或“提高自然完成率”。
6. Host块归因长prefix probe有溢出；不能以观测子集证明全量
   重算率很低。Mamba逐节点命中位置仍有缺口。
7. 稳定端到端收益尚未证明。开发单pair用于机制验证，正式
   实验再多轮取平均/报告方差，保留失败/截断，不筛分歧轨迹。

## 7. 权威入口

- 当前设计：`docs/beliefkv_design.md`。
- 当前状态：本文件。
- 执行顺序：`docs/implementation_plan.md`。
- 不可违反的实验约定：`docs/experiment_operating_notes_zh.md`。
- 最近完成的pair：`docs/experiments/joint_tool_join_h2d_v6_84root_zh.md`。
- 最近单pair验收：v6 raw目录的 `development_validation_plan.json`。

修改主线时同步维护上述文件，不以新增实验报告代替更新状态页。
历史报告保留原配置和原始结论，新诊断明确标注为后续复核。
v5期间仅修改文档；当时443个Python/shell运行文件指纹为
`4907f0437b65489812eca98b07035952cadf87cab6a00c6a1241cb63a72c4a55`，
指纹算法见 `beliefkv/experiments/decision_characterization.py`。
Git文档提交的变化不应被误记为v5中途更换运行代码。
v6使用新的运行源码指纹，由新launch记录冻结，不回填v5。
v6当前指纹：
`c65caec44ecc934cd5cff9d740ec96f19459f48527505c85927bd4ae969fd6b9`。
目录为 `experiments/raw/qwen35_joint_wait_h2d_ab_84root_20261006_v6`；
225项相关CPU回归为v6启动时证据。v6终态与生命周期已核对，
正向吞吐观测仍有需求混杂。新候选的拟合/回放不回填v6。

v7目录：`experiments/raw/qwen35_joint_wait_h2d_ab_84root_v7`。
启动commit为 `5dfdd30`，runtime源码445文件指纹：
`4257db63689e9a92786f339e706180e56c5370efd421c7bb9d96336be2c466b9`。
两侧已结束：reactive83 completed/1 incomplete，predictive84 completed。
分别耗时5389.94/5209.46秒；12个预测JOIN H2D，3个EOS前启动，
全部FULL首次复用。旧fanout全为单child，不能视为2–4负载证据。

v8b（已停止）目录：
`experiments/raw/qwen35_joint_wait_h2d_ab_108root_2to4_v8b`。
启动commit `026f650`，staging patch SHA：
`4cf11ec7e8041fe262312d02a9fe47d0777860e768311164e9e592196e0e03ab`。
模型和运行参数见该目录 `ab_plan.json`。虽然CUDA graph捕获成功，
108个首轮全部单task，不是合格2–4数据。当前v8c使用新目录
`experiments/raw/qwen35_joint_wait_h2d_ab_108root_2to4_v8c`，
首轮以原生语法范围2–4生成，不用启动配置代替实际fanout。
v8c启动commit为 `c744461`，staging patch SHA为
`53109f07cce1662afeaefa369ab913f94c0dd6e249e0a8b00f327c56838f7087`。
2026-10-07启动核对：108条首轮全部2个task、108个双成员JOIN；
服务端记录108次2–4生成约束，decode CUDA graph覆盖48。
native遥测dropped/failed/writer error均0，旧前缀探针已实际写入；
终态样本位于 `opportunities/admission_opportunities.jsonl`，不是
`server/runtime_audit.jsonl`。很小的旧前缀缺失不能自动当作驱逐
重算；需要与对齐尾部和块级归因分开，Mamba逐层重算仍未精确量化。
本轮尚未结束，不报告完整H2D预算或吞吐收益。后续只改文档，
保持运行源码、权重、prompt和参数冻结。

启动后的持续忙碌阶段发现监控口径限制：`native_telemetry_status.json`
只在writer队列空闲0.5秒或关闭时更新，因此其17:18:56快照不能
代表后续实时计数；JSONL仍持续写入。直播分析须核对原始记录时间、
队列溢出/处理错误和载荷单位一致性，不能沿用旧快照的零错误/命中数。
原始记录一次后续截面已有8,083个native H2D、约2.419 TB，
CUDA-event区间累计71.22秒、submit→ACK区间累计790.49秒；
提交时间/完整载荷没有重复，字节与FULL/Mamba单位数一致。
这些是进行中的累积量，不是完整makespan或可直接减去的oracle JCT。
首轮全双child后，已出现少量后续单child组；记录实际成员，
不补造child或取消正常workflow，也不声称全程已保证2–4。
忙碌时定时发布状态的修复留到冻结pair结束后，两侧运行代码不混用。
