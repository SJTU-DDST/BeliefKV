# BeliefKV 实验注意事项

更新日期：2026-10-09。本文是当前实验的执行约束，不是新增 agent
guard、终态门禁或模型动作授权。启动前同时阅读
`docs/implementation_plan.md`；旧诊断脚本和历史计划不能覆盖当前约定。

## PREPARE 热点修订与节点身份

v10 predictive已用 `e985d8c` 冷启动且仍在收尾，最新优化先在
`/tmp/beliefkv-opportunity-20261009` 提交；本轮driver结束前不修改
主仓库或服务源码。旧 `arm_status.txt` 保留失败启动状态，不能据此
断言当前已停止。未部署的新修复不能计入本轮性能结果。

原生统一树creation_time为 `numpy.float64`。所有传入物理规划/
原语的锚点路径必须使用现有 `normalize_native_creation_time`
转换为Python float/int，保留原值，不截断或更换节点代次。
不要只修session快照而遗漏request reentry：此遗漏使v10的9419次
handoff选择全部在规划阶段退出，尚无handoff传输。新CPU复现已
确认旧版0次、新版1次提交；提交替身不代表真实DMA或ACK。
`resident_or_unavailable` 不能全部算缓存命中；须查看新增的
`execution_handoff_no_step` 具体原因，区分锚点缺失、不可观察闭包
及没有可恢复检查点。

每次PREPARE维护集中刷新租约，再按context校验候选。闭包、深度
及前缀长度的复用限于同次同步检查；采样与真实动作分离，实际
enqueue仍重新验证节点、session、epoch、在途操作和容量。
原生驱逐回调保留实时租约检查，过期锁和终态候选照常释放。

CPU对照必须包含实际祖先链、已备份/未备份状态、Host不足与活跃
租约，不能用只有准入排序的旧基准证明PREPARE减负。新基准
`scripts/benchmark_native_prepare_path.py` 覆盖156 workflow、16轮
历史、24/64节点闭包，对比 e985d8c，并核对选择与发布结果。
报告：`experiments/reports/prepare_path_cpu_156_{24,64}_20261009.json`。
CPU减负与358项回归通过均不代表预测传输已提高GPU吞吐。

## 当前迁移策略与实验接续

`perf/opportunity-aware-transfer` 在 `859d138` 上补齐了下一批容量
预算、PREPARE候选筛选和实测服务窗口，不改模型权重、prompt、
阶段阈值或agent guard。ACK锁按native的真实准入名额与剩余池
容量分配，先预留decode增长及下一批input/Mamba；容量不可观测
才回退4把/1 GiB。满decode batch最多一个frontier候选，原生
准入仍是最终约束。真实NO_TOKEN释放实际锁，不能靠软记录制造
“已让出容量”的假象；恢复提升仍保留普通请求轮次和10秒老化。

Host空闲不足时跳过新增PREPARE，不为备份主动淘汰已有Host副本。
按受压池可回收字节与传输量选择候选，已有Host副本的FULL-only
冷节点也可回收。最新启动窗口使用H2D submit到ACK P90、
enqueue到submit P90和100 ms观察间隔，保持本轮500 ms上限。
只有近期有GPU服务的child才使用pre-EOS工作量投影；这是runtime
动作选择，不干预agent执行或取消workflow。

已修复预测终点越界后夹成1 token的边界问题，但v8d的15个
工作量早触发中越界计数为0，不能把它当作已证明的历史根因。
相同快照中13/15次会继续等待，不证明后续预测精度或吞吐。
新CPU准入对照+0.13%，只覆盖无活跃传输/锁的合成输入。
相关回归290 passed/1 skipped；不因通过检查就声称GPU收益。

v10 native已结束，154 completed/2 incomplete；父驱动曾因HTML
导出路径错误停止，predictive当时尚未启动。renderer的 `--run-dir`
必须指向具体arm目录，不能传pair根目录。native HTML已生成。
提交并部署最新runtime后，以 `scripts/resume_semantic_h2d_ab.py`
接续原v10，保留native版本、原始plan与失败日志，仅更新pending
predictive版本。`RESUME_PENDING=1` 跳过已收集native且不覆盖
已有arm目录，不初始化新计划、不重跑native。启动后冻结源码。

`04812ec` 首次接续发现Mamba D2H的before-enqueue回调NameError：
D2H记录函数错误引用H2D函数内的 `pool_name`。该次已停止，失败
现场保留，不纳入性能结果；修复后重新冷启动predictive，native
不重跑。启动检查须同时核对真实传输与回调异常日志，不能只看
`physical_disabled=false` 或FULL-only ACK。CPU传输样例必须含
真实Mamba `pool_transfers`，并核对enum/string池名与Host目标索引。
修复后145项相关CPU检查通过。这是修复物理通道缺陷，不增加agent
guard或额外GPU测试。

## 新恢复路径的验证口径

流水handoff修订位于独立分支 `perf/pipeline-execution-handoff`。
用户最新授权v10在native结束后直接用最新优化运行predictive。
native采集和服务收尾前保持原runtime与引擎；仅暂停父驱动防止
旧predictive自动启动，native子进程继续运行。收尾后由
`scripts/resume_predictive_after_native.py` 部署已提交源码和精确
引擎增量，再恢复原驱动；不停止native、不重复native实验。
保留原始plan，两侧源码/补丁分别记入 `arm_revisions` 并进入
对比报告。模型、task、到达表、容量和预算保持一致。
这是用户指定的跨版本开发对照，不能宣称已隔离全部策略因果收益。

一次H2D burst最多16个node、一次原生submit、每node独立command
和ACK。多个ACK不等于多次独立DMA，更不等于吞吐收益。FULL-only
准入使用原生逐层依赖；Mamba在deferred COW前必须等待其自身
传输finish event，不能仅检查load已提交或复用的layer-ring event。
空闲容量只够前缀时可以分段恢复；不强制迁出热KV补齐整条路径。

请求可能在软件账本ACK回调前获得服务。首次复用记录只有在真实
ACK与issue时的allocation身份、context、node、pool字节匹配后
才可发布；`first_service_before_ack=true`不是负传输时间或协议失败。
ACK延迟不能让已经服务过的动作再次加驻留锁。暂存证据超时或失效
须清理，不可把没有验证ACK的首次服务当作预测命中。

因果分类只在同一scheduler轮内共享；驻留、老化与提升预算保持
实时检查。深层FULL锁可覆盖祖先，最新预算依本文顶部的实际
准入容量决定；4把/1 GiB仅为旧版本和观测不可用时的回退。
软跟踪记录数量不等于实际锁数量。仍保留普通请求老化与实际容量
压力下让出保护，不能靠长期pin或无限parent插队提高表面复用。

旧workspace清理可显式加 `--cleanup-clean-planned-children`：
父workflow必须completed、sandbox清理完成、root patch已归档且
artifact无错误；planned child还须已有报告且git无修改、无未跟踪
文件。仅删除workspace，保留trace、report、patch、模型及失败证据。
有未归档child改动的workspace保留，不以日期早就盲删。

## 三策略对比与后续到达

最新执行约定：完成共用路径减负后，进行一对native/predictive，
固定t=0到达108任务、t=3600秒到达48个不同train任务，共156个。
使用新108+48 manifest，首108与旧实验相同，第二波为Django train，
两侧均Host80:20；不能对照旧auto v9。第二波项目构成与首波不同，
需如实分段报告。此约定替代本文历史108单波/不得新增实验的限制，
不授权canary、额外开发重复或人为缩小缓存以制造压力。

启动前提交源码，冻结模型、canonical引擎补丁、task顺序和到达表；
通常两侧间不得热改；本轮按上述最新授权在native收尾后切换，
每侧独立冻结并记录版本。driver逐侧核对指纹，独立冷启动，终态后自动
导出HTML与对比，仅删除有完整归档的completed workspace。
保留当前running/上下文/工具与workflow预算及宽松guard配置。
正式统计仍需多轮配对；本次一对只能提供开发证据。

共用路径计时采用低开销累计inclusive/exclusive值，不能将嵌套
inclusive之和当作全部额外CPU开销。108 workflow/2轮历史的CPU
准入均值10.722→1.365 ms仅是相同合成输入对照，不是GPU加速。
GPU原生逐层加载等待计量在两侧都每16个加载prefill抽样一次，
不CUDA synchronize、不逐调用写盘、跳过graph capture；
读取完成event后输出，保留未完成/无Python等待样本数。
event开销包含在等待值内，不把抽样总量当作全程oracle收益。
native安全load fence存在且不回收热页时可保持decode overlap；
没有适配能力不得删掉必要的依赖等待。

最终重点为全程完成吞吐与JCT、固定窗口服务效率、FULL实际复用、
重复原生恢复、ACK到服务、重算与驻留字节时间。累计传输毫秒和
ACK次数不代表端到端收益；新predictive仍低于native就明确报告。

v8c、v8d、v9是相同108任务和物理容量的live实验，但不是相同实现
与请求轨迹的严格配对：v8c物理通道失效，v8d改变shell反馈，v9是
共用兼容补丁上的原生策略。native输出更多请求/输入、输出总量与
v8d仅差约1%，不能仅用“模型轨迹不同”忽略14.71%的完成吞吐差距；
也不能将它直接解释为某个预测头造成的确定性减速。
历史Host为auto约52.7:47.3，后续80:20不得回写历史结果。

分段使用HTML首个GPU采样点为时间原点，按真实传输submit分配字节；
单批含原生与tagged部分时守恒拆分，batch计时只计算一次。
分段输出token按请求完成落桶，不是逐decode token时序。
worker服务区间不是独立CUDA kernel时间；累计H2D时长和submit到ACK
时长均不是可直接减去的JCT或oracle上界。不能把低并发长尾造成的
全局小batch均值当作高压时吞吐变差的唯一原因。

native前50/60分钟已发生98.820%/99.779%的H2D，但仍有75/62个
活跃workflow、53/40个未完成JOIN。迁移衰减不等于全部child返回，
Host池满不等于有用状态工作集仍满；FULL active比例也不是HBM占用率。
共同旧前缀缺失代理稳定不证明全部重算已被观测，Mamba与过期块级
归因须继续保留未知状态。

108+48为t=0与t=3600秒固定到达，现已授权，见本节顶部。扩展到达须
使用新的隔离train任务和预先冻结的到达表；旧128任务清单仅剩20个
未使用任务，不能靠重复旧task凑齐。各策略不得按完成数各自补任务。
预先报告迁移密集窗口和全部完成JCT/吞吐，有限第二波仍有排空尾段。
增加迁移机会不修复低复用或控制开销；必须同时观察暴露恢复等待、
实际FULL复用、Host驱逐、共同旧前缀损失和其他workflow延迟。
新80:20容量与历史auto不同，不能保证相同到达表保持相同压力。

## 当前池配置与调度协同

原生v9及自动HTML均已结束，108/108完成；其运行期间保持源码
指纹隔离，导出后才合入独立工作树修复并恢复主目录导出器。
后续仍不得热更新活跃实验的runtime/metrics或third_party源码。

下一版Mamba只保留本context当前安全输入检查点的session引用，
不是删除所有历史物理状态。共享、锁定或在途状态按原生引用释放；
FULL-only祖先传输不携带历史Mamba，需求恢复默认不省略必需状态。
工具完成或ALL JOIN解锁可给未过期原生锁最多3秒提交宽限；
真实提交后仍不超过ACK后10秒。过期不复活，不按ETA长期pin。
恢复与收尾共享准入预算；10秒老化避免普通请求被持续插队。
异步FIFO控制事件不意味着session退休RPC也已异步。
按最新用户要求，后续Host默认FULL:Mamba=80:20，HBM的
Mamba/FULL=0.9不变。显式auto必须传给launcher，不能靠unset
落回80:20而错误宣称跟随Device。80:20不是已证明最优；
启动后读取两池实际census，旧auto/70:30容量校准不能直接复用。

reactive和predictive共同启用resident-first及既有有界收尾优先级；
只有predictive启用execution handoff，原生策略baseline关闭这些
调度修改。队列换入依赖实际请求输入匹配，不依赖旧动作头的
eligibility或净收益标签；只恢复可复用缺失extent和必要检查点。
先执行同因果层级的HBM就绪请求，不影响老化保护或JOIN恢复提升。
每次一个候选、2秒规划窗口、最多16个node；首次服务释放ACK锁。
容量不足只回收Host ACK完成且未锁定的冷副本，不为制造机会
卸载热KV，也不将D2H在途字节当作已释放HBM。
不同节点D2H/H2D允许重叠，但须用真实submit/ACK判定隐藏开销。
source=execution_handoff必须单独统计，不能把已提交后、首次GPU
服务前的恢复混入RETURN/TOOL_END前的预测精度或提前量指标。
不能通过扩大传输范围提高“预取占比”；必须同时核对实际FULL
复用、原生H2D下降、重复加载、重算、其他workflow延迟与端到端吞吐。

## 原生策略基线与有界恢复

用户已要求同workload原生SGLang对照，启用
`NATIVE_POLICY_BASELINE=1 AB_MODE=off`。关闭BeliefKV admission/
socket、PREPARE、预测H2D、收尾优先级；保留共用的harness通知、
session/NUMA及2–4生成兼容补丁与只读遥测。明确报告为原生策略
基线，不宣称未修改上游SGLang。清除继承的预测artifact与遥测环境，
客户端预检须实测session=true、BeliefKV admission=false。

新恢复保护不得变成长时间锁住全部冷KV：按本文顶部的实际准入
容量限制锁数和闭包字节；预测期lead+1秒，真实下一请求提交后才可延长到ACK后最多
10秒。真实NO_TOKEN优先让出，首次GPU服务释放；解锁必须重放
原始receipt且只执行一次。失败保留证据并停用物理通道，不报成功。
批量Mamba复用只在同节点/代次/device对象、请求COW源/目的身份
和真实forward完成均吻合时计数，不把未知当浪费或无需迁移。
JOIN采用更保守的已观测速率仍不证明亚秒预测有效；不替换或
修改旧模型eligibility。GPU实验启动后冻结可执行源码和模型。

## v8 故障与修复约束

v8c reactive 在17:05:16发生真实账本失效，不能因为108个workflow
都completed就说物理策略完整有效。Radix拆分在D2H在途时是合法的；
发布节点集合可以扩展，但必须由真实native祖先链、anchor代次、
原Host目标索引及守恒的pool/字节证明，不能仅允许任意节点超集。
不要删除 `physical_disabled` 检查或手改状态。

持续忙碌时必须发布有时间戳的状态快照，而不是只等待队列空闲。
全量测试的管道过滤器、`--exclude-tag` 等参数不是测试目标；
管道的末端退出0不代表上游测试或timeout成功。新执行反馈采用
bash pipefail，但不加语义无进展guard或缩短现有预算。

修复后的v8d predictive与失效的v8c reactive属于开发诊断，
不是同配置严格配对。保留全部异常和轨迹，不宣称独立吞吐加速，
不自动排额外reactive或canary。当前108-root、2–4 prompt、
模型/池/running参数保持不变。

## 场景与配置

2026-10-07 用户批准下一轮 **108-root 单波到达、每轮2–4 child**，
server running=48、Host 200 GB/NUMA node 1。当时Host跟随Device，
后续比例以本文顶部80:20约定为准，不回写历史manifest。
此前64-root配置仅作历史对照；不得自动扩到128或重叠64+64。
当前pair属于live压力探索，不因固定seed就声明轨迹相同。
历史开发阶段只做一对108-root；最新156-root固定两波见本文顶部。
正式阶段再多轮配对取平均和报告
方差。固定需求GPU回放不作为主线或前置要求，见
`docs/experiments/pressure84_and_fair_comparison_2026-10-05_zh.md`。
v7已结束，reactive83 completed/1 incomplete、predictive84 completed。
108-root/2–4属于新的联合负载诊断，不排额外重复，
不能仅凭脚本默认值自动重跑或当作已冻结的理想正式负载。

两侧共享native_in_graph_2to4 prompt和首轮命名task/显式并行选择。
本机XGrammar的命名task只允许一个调用，BeliefKV ingress改用
task子集required约束允许重复；完整工具prompt不变。
v8b仍108条首轮全单task，已停止，不纳入合格对照。
v8c首轮使用RepeatFormat范围2–4，模型自主选数和内容，不在
生成后拒绝/补造child。后续轮次保持prompt驱动，须核对实测。
首个v8 auto尝试也已停止；v8c使用新目录冷启动。
专用委派prompt须位于通用“先读文件”要求之后，不能只修改次数
而不检查真正的生成约束。实际fanout必须按trace核对；
单个child结束不满足JOIN_ALL，只有最后未完成成员可作为恢复信号。
冻结旧预测头用于新regime诊断，不假定校准有效。
新增旧输入共同前缀损失代理、完整有界Host归因、终态路径驻留/
引用观察及session close耗时。共享祖先不当作独占死字节，
不把close ACK当物理释放，不盲目D2H死child挤占Host。

v6中7个JOIN H2D全部首次复用，但仍在EOS后启动。下一轮先用
相同因果快照比较工作中心/区间与首次触发，不通过手改eligibility、
去掉物理容量检查或新Agent guard制造动作。仅在显式版本的新
work artifact中修正区间投影并重新校准，旧artifact不默默换语义。
Runtime的 `SEMANTIC_WORK_STATISTIC=upper|center` 必须写入launch
manifest与动作日志，旧默认upper。模型只预测事件/工作，动作由
runtime结合真实Host-only副本、可用容量、节点预算与短租约选择。
将v6加入训练后只能报告训练回放，项目隔离的旧开发集合也不能
重新称为密封验证。修复后最多启动一个同配置v7 pair，先提交源码，
运行中继续冻结源码、prompt、权重和参数。
`EOS_PROTOCOL_WINDOW_MS` 默认50，v7显式250；它是正常stop后
cache动作的有效性窗口，不是agent返回门禁。非空正文/无工具/
非internal证据独立于模型，日志标记observed_no_tool_eos，
不能把这个路径增加的ACK写成模型预测精度提升。

场景目标是存在真实可迁移状态与可用 HBM
空间、且有用 KV 丢弃后重算较少的负载。server running=48 是
请求执行上限，不是 root 数，也不代替 root 并发配置。

不得因预测 H2D 为零继续增加已授权的并发或改变到达方式。先区分
checkpoint 不存在、session 引用丢失、Host 无副本、目标仍在 GPU、
缺物理空间、预测太早/太晚及传输发射故障。数据仍在 GPU 时，
不发 H2D 是正确行为，不得人为驱逐或清空 HBM 制造事件。
调整并发、到达方式、Host 大小或池比例须先说明并取得用户确认。
历史上2026-10-04从36-root改为64-root，同时加强多轮spawn
prompt并启用选择性JOIN parent PREPARE_HOST，见
`docs/experiments/join_prepare_h2d_64root_2026-10-04_zh.md`。
该64-root配置不是当前默认。

2026-10-01 的 64+64 仅为高压机制诊断。两池满、频繁 Host 驱逐
和预取后再次淘汰不符合当前主场景，不能将该批次的 ACK 或
完成速率当作当前策略收益。

reactive/predictive 对照必须使用相同 task 集合、到达方式、
模型/采样参数、runtime prompt、通知、收尾优先级、deadline、
物理池和原生缓存规则。预测推理和预测动作是实验变量。
两侧重新冷启动，禁止将不同源码版本或不同配置结果拼成对照。
没有有效迁移机会时先报告原因，不重复同配置来积累无效 ACK。

原始吞吐/JCT必须和实际工作量差异一起报告。固定seed甚至
temperature=0都不是逐token、工具结果或workflow路径相同的证明。
不要筛掉轨迹分歧任务，也不要把总耗时除以token数作为“公平”
JCT。正式live重复按run/pair统计，不把同轮共享资源的workflow
当作独立整轮吞吐重复；确定性内核变更必须两側共同验证。

## 长程任务预算

3600 秒 workflow deadline 于 2026-09-28 02:30:12 的 `f3700f4`
引入，当时 `run_qwen35_native_regime_probe.sh` 默认只有 8 root，
用于小规模原生缓存诊断。后续扩成完整 workload 时未同步调整，
是实验配置遗漏，不是模型、SGLang 或长程任务本身的一小时限制。
2026-10-01 20:09:11 的 `4ee4199` 恢复 14400 秒默认值。

当前完整实验显式使用 14400 秒（4 小时），可通过
`ACTIVATION_WALL_CLOCK_SECONDS` 覆盖。任务超过一小时可以正常，
不能按这一时长判定循环或错误。四小时仍是人为预算，达到该
预算要记为截断，不能视为自然完成或证明任务不可完成。

workflow deadline 是 root 与全部 descendants 共用的绝对墙钟
预算，排队、工具、总结及多轮执行均计入；不是每次 LLM 调用的
600 秒 timeout，也不是 sandbox 单条命令 timeout。
graph limit=2048，允许提前 32 步 FINALIZE，不得回退为 512。

workflow deadline、模型请求timeout和工具命令timeout是三个
边界。v4的django-16938两次全量测试各达到约600秒、返回137/
Killed，不能归为GPU等待或把它们当成工具自然完成。检查默认
工具上限及实际命令、超时反馈，不为了改善makespan而统一缩短
所有正常长工具。实验期间冻结配置，修复须后续同时用于两侧。
`python ... | tail ...` 的上游失败可能被末端退出0掩盖，
不能仅凭该退出码或“Command succeeded”判定测试通过。

分类错误必须检查 deadline audit，不能只看异常类。
64+64 批次的 14 个错误中，11 个在模型提交前报
`ActivationDeadlineExceeded`；django-10554 和 xarray-6721
分别只剩约 688 ms、154 ms 时提交请求，随后 deadline abort，
表现为 `APITimeoutError`。13 个均属 deadline 相关截断；
另一个 django-11149 是缺失 tool-call ID 的协议校验错误。
不能把前两条仅按异常名字归为独立网络故障。

## Runtime 与模型边界

完整实验使用 `--native-reactive-guard-profile`，不启用重复工具、
无进展或 soft budget 的语义干预，不添加短 canary 或 child
取消门禁。保留允许的 graph hard limit 收尾；显式的预算到期
和真实执行故障单独记录，不掩盖为模型自然 RETURN。

自然语言 child 回复是有效终态，不要求结构化 completion。
自然语言与真正空响应必须区分，尤其不能把拒绝工具后的空白
响应补成成功结果。空响应的 parsing/serving 问题不能直接归咎
于模型。工具 ID 可在响应边界规范化，但保留工具名/参数，
未知工具收到普通错误反馈，不执行虚构工具、不终止整个 workflow。

任务路径须与 sandbox `/workspace` 的实际仓库根一致，不能将
host checkout 路径提供给模型。模型重复读文件时先核对路径、
返回内容和 prompt，不能直接加强 guard。

允许 root 动态多轮 spawn，积极提示而不固定每轮 child 数。
不要为制造 JOIN 或迁移而伪造轮次。child 通知和有限收尾优先级
必须与 predictive 解耦，并在两侧启用，不用预测得分决定 child
是否可以 RETURN。

保持当前阶段 head、encoder 和阈值稳定，只独立优化条件剩余
工作 head。模型输出阶段置信度和剩余工作，不学习离线 trace
不可识别的预取净收益；Host 副本、容量、时机和收益由 runtime
判断，不修改 eligibility 元数据来绕过物理安全检查。

## 物理证据与统计

恢复目标必须是 parent 下一输入可复用的安全 checkpoint，
不能使用生成输出末端；FULL 前缀和 Mamba 状态须分别验证。
短 decode 的 finished insertion 为空时，不得覆盖已存在的
真实 prefill anchor。没有真实状态时不能补造引用。

原生 D2H 留下的有效 Host 副本也可以 predictive H2D，不要求
先消费 PREPARE_HOST。PREPARE 仅备份可能被卸载的有效状态，
不对全部 agent 无差别备份。预测 H2D 重试预算绑定同一 JOIN
与 parent context/epoch、session generation；阶段刷新或
request attempt 改变不能清零预算，也不能长期 pin 预取数据。

分别报告候选、native issue、ACK、首次服务、实际 FULL/Mamba
复用、censor 与未验证状态。ACK 不是收益，Host 副本存在也不是
数据已被消费。未验证 Mamba 不全部记为浪费，Host eviction
不全部记为有用 KV 丢失，uncached input 不全部记为重算。

持续高负载时 `native_telemetry_status.json` 的更新时间也必须检查。
当前writer只在队列空闲0.5秒或关闭时写状态；v8c已经观察到
状态停在17:18:56、但JSONL继续写入。实时计数和健康检查应使用
原始记录时间、处理/队列错误及载荷一致性，不能将旧快照当作
实时零错误或命中率证据。修复定时状态发布须在冻结pair结束后
共同用于后续两侧，不为监控口径问题中途污染对照。
块归因 probe 溢出时不能以观测子集推断全量重算率。

usage 与物理占用分开：SGLang 的 FULL/Mamba usage 会扣除
可驱逐缓存，物理 free-list 接近零时 usage 仍可能很低。
不能用日志 usage 推断池未填满，也不能用填满推断热 KV 满池。
2026-10-03 的 36-root 观测到 FULL usage 峰值 33.34%、
而 FULL/Mamba 物理占用曾接近/达到 100%。

冷启动时不能要求本轮先发生三次 H2D 才初始化其预测服务估计，
然后通过增加并发绕过这一依赖。缺实测服务模型时先核对已有
证据与初始化，不把被服务样本保护挡住的动作算成预测头失败；
有效输入目标仍驻留 GPU 时，即使估计就绪也不应发 H2D。

native EOS、child RETURN、JOIN satisfied、parent 首次 GPU
服务是不同边界。ACK 到首次服务不是 child RETURN 预测误差；
EOS 之后收到的快照不能声称生成结束前预测准确。
剩余工作头只用因果有效的已生成 token/正文/通知，
不混入未来排队等待；RETURN 时间与实际动作提前量分别评估。

内部 summarization 必须继承真实调用 agent 的 callback 祖先链
和 invocation scope，不能重新构建仅含 handler 的 callback 列表
使 child summary 回落到 root。检查 JOIN 未满足时 parent 是否
仍 WAIT_JOIN，不能用错误唤醒的 READY 状态解释为“无恢复目标”。
foreground 调用和 JOIN 是可同时存在的依赖，完成其一不能绕过
另一个。此项是因果/遥测正确性，不是额外 agent guard。
旧的机会采样只用于回放诊断，实际传输仍重新验证身份、副本及
当前物理容量；采样消失不自动判定为 Host 数据被驱逐。

亚秒级窗口必须同时检查正文采样间隔、推理队列年龄和 decode
进度回溯量。100 ms 正文快照须由已送达的新内容触发，不补造
结尾，也不改变 agent 工具/返回路径。只有双方 Linux boot、
time namespace 与 CLOCK_MONOTONIC 的域标识匹配，才直接使用
正文时刻之前的已完成 GPU 服务；未知时钟和旧 trace 仍保守
回溯 100 ms。不要为了增加短窗口命中而回填旧的时钟域证明。
短 token 区间概率与 RETURN 墙钟误差分别报告，无触发不算收益。

GPU利用率低时，先区分无请求/工具长尾、有请求但发射不足、
batch/context变化和实际kernel繁忙。NVML利用率是采样期间
kernel-active占比，不是SM occupancy。`gpu_service_sample`
记录scheduler/worker墙钟interval，包含非kernel开销，不得
累计后当作GPU kernel时间。完整makespan保留长尾，同时报告
completion curve及非空服务阶段；诊断分段不能替代正式指标。

工具等待模型必须在启动计划和运行时状态中明确记录为已加载，
不能仅因存在 tool_wait 代码就声称启用。事件时间预测与 legacy
admission 的动作资格分离，不修改旧产物的 eligibility 标志。
并行工具不能在首个 TOOL_END 时唤醒 agent；使用稳定 tool_run_id
跟踪，时间动作依然绑定真实等待、session/generation/epoch。

预测 load_queue 不能等后续 prefill 才启动并把延迟算成预测误差。
提前 flush 必须保留原生 stream fence 与 layer producer event，
不能抢原生 prefill 的 consumer index。服务估计须区分池形态、
相近大小、enqueue 排队与 submit-to-ACK，不能线性放大固定开销。
窗口现为 1000 ms，但必须仍按真实 submit 到 RETURN/TOOL_END
审计；未来未生成的 token、first GPU service 均不能替代返回标签。

v5的工具路径证实，同一target可同时被P50倒计时判为即将恢复，
又被条件CDF判为长等待victim，导致ACK后再次pressure parking。
后续须统一时间/驻留口径与短租约，而非增加独立模型门禁。
v6代码已采用同一条件CDF逆算P50并加入当前最多2秒的策略租约。
该租约只排除自身pressure parking，不是全局pin或容量预留；
原生驱逐、预测变化、epoch/session失效、首次服务和过期均记录。
EOS后、RETURN前的提前H2D是合法机制证据，但不等于语义头
在生成结束前已经预测准确。Host归因overflow限制重算结论，
不应被扩展为拒收整轮逐请求/child事件数据的额外门禁。

## 版本与文件

启动前提交代码，持久化实际 commit、源码 patch、模型/工作头、
manifest 的 SHA256，以及 root/到达、deadline、guard profile、
NUMA 和实际容量配置。检查最终命令和有效环境，不只看脚本默认值。
同一实验内冻结 prompt、runtime 和模型，修复后另建目录，不能
改写历史配置或回填已结束结果。

每轮结束后只删除正常完成、sandbox 已清理、patch 已归档且
artifact 无错误的旧 workspace。保留 trace、patch、配置、模型
以及失败现场。检查磁盘余量，不以扩大并发或删除在用数据解决
磁盘不足。GPU 实验进行中持续区分系统故障与正常长任务，
真实系统故障及时停止并修复，不以吞掉异常“提高完成率”。

## 最近清理记录

2026-10-06删除24个已被后续导出替代的JSONL及373个已完成、
sandbox已清理且patch已归档的旧workspace，回收约83.7 GiB，
磁盘可用空间由约25 GiB增至108 GiB。逐项清单见
`experiments/analysis/old_artifact_cleanup_20261006_result.json`。
旧v9/native-transfer导出目录只保留manifest及清理标记，不再是
完整dataset；不能仅凭manifest存在就重新选为训练源。

保留当前模型引用的 `dataset_failed_repeat_v10`、所有原始
trace/result/patch、失败及近期workspace、v3/v4/v5证据、模型、
processed标签、环境依赖和Docker镜像。在用模型文件哈希及443个
运行源码文件指纹均未变化。
清理使用低I/O优先级，发生于v5 predictive侧的01:01:59至
01:02:21（Asia/Shanghai）；它是已记录的背景I/O事件，不改写
原始性能指标，不据此新增数据资格门禁。
