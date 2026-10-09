# BeliefKV 当前架构与实现状态

更新日期：2026-10-10。

## 当前目标、v14 最终结果与 v15 对照

当前目标是在固定workload、模型、容量及到达表下，使predictive
相对native取得可核实的性能提升。除预测传输外，优先减少调度/
控制开销，利用agent依赖、恢复就绪与HBM局部性改善实际执行。
以下三项并入同一目标，不能以动作数量代替验收：

1. 缩短恢复就绪到首次服务的等待，减少首次服务前的原生重复加载；
   同时检查缺失页、必要Mamba状态与其他workflow的排队代价。
2. 补齐PREPARE消费归因，区分压力释放、原生/受控H2D、Host驱逐后
   重新备份和未观察到恢复；据此减少没有迁移需求的备份。
3. 提高有用FULL预取覆盖，验证已修复handoff是否真正替代需求
   恢复，并以实际复用、完成吞吐、JCT及重算量评价收益。

当前active /goal继续采用上述方向，并落实以下执行约定：
FULL PREPARE的收益评估须区分增量备份与Host驱逐后补传；减少
冷副本反复回收/补传的控制和带宽成本属于当前目标。PREPARE只
提前备份尚未拥有有效Host副本的FULL
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

当前目标仍未达成。v15已从主目录冻结提交
`feb5ee01a9f1340a694dcba442c439d08e4bd274`启动，完整引擎patch
SHA256为`dbde39b7f37977ecacd72dddf78b3da94a56fa6a0879afb1798ae55ab7f1fe63`。
既有driver依次执行predictive_h2d、native，各自冷启动服务与缓存；
156任务、108+48/3600s到达、running48、Host200GB80:20、
HBM Mamba/FULL0.9、context131072、completion8192、
graph2048/reserve32、workflow14400s、2--4 child、seed21及模型
产物均冻结。共用客户端优化在两侧生效，后续开发仅在隔离工作树，
两侧及完整后处理结束前不合入或部署新代码。

2026-10-10 05:40 CST的v15来源快照：原生2358批/731.314 GB，
JOIN/tool72命令/1.384 GB，需求handoff4692节点命令、1524批/
36.050 GB，未知受控来源为0。稍后消费快照的handoff FULL传输/
确认复用19.283/19.203 GB，ACK到首次launch P50为22.62 ms；
JOIN为0.06738/0.01513 GB、17.604秒，tool为0.22223/0.18760 GB、
1.244秒。316次PREPARE ACK中49次关联恢复，54次压力迁出中51次
关联先前PREPARE。此时尚无后续同node/pool D2H关联，不能提前
宣称已消除Host回收后补传；来源与消费快照截止时刻不同，不强行
对齐计数。handoff不能计为提前预测收益，以上均非完整吞吐结果。

首次服务审计已在隔离工作树修正为schema_version=2：原生
LLM_SUBMIT是请求到达，不能作为GPU服务开始。现在按request、
workflow、invocation、context、epoch匹配最早的
gpu_service_sample.service_start_ts_ms，分别报告原生到达、
预取first-launch receipt和worker服务区间；缺失服务采样保留
unknown，不回退到到达时间。worker区间也不是独立CUDA kernel
耗时。冻结报告保持不变，旧文档将到达称作首次服务的字段由新
报告取代。v14正确的parent提交到worker服务P50/P90为
777.09/11950.68 ms，原生到达到worker服务为137.74/11162.52 ms；
旧467 ms实际是提交到原生到达。
05:50 CST的v15快照为27节点命令、18个不同child请求：EOS到
finish P50为397.39 ms，JOIN到parent提交58.57 ms，提交到原生
到达173.05 ms，原生到达到worker服务492.47 ms，提交到worker
服务2739.31 ms、P90约17.26秒。各区间分位数不可相加。
一条django-10914请求旧口径为1.028秒，真实提交到worker服务
约16.059秒；其租约在提交前到期，FULL未确认复用。继续分别检查
客户端积压、准入及传输等待，不能仅延长投机驻留。
修正报告：
`experiments/reports/v14_join_pipeline_first_service_v2_20261010.json`、
`experiments/reports/v15_join_pipeline_first_service_v2_partial_20261010_0550.json`。

下一版只读reentry检查复用同一次调用内多session叶的匹配祖先
状态，每次新调用重新读取实时物理驻留。单叶不创建memo字典，
namespace、前缀、检查点、pending DMA、节点有效性及64层上限
均保留。61项相关检查通过，含跨调用驻留变化和共享祖先。
7组、每组200次实际RadixKey的合成CPU比较输出相同：8叶
4K/32K/96K路径P50分别89.045→32.115、360.005→70.177、
931.728→140.567微秒，下降63.9%/80.5%/84.9%；单叶慢0.3%--2.9%，
首段分歧4.642→4.968微秒。保留这些退步，不推导GPU吞吐收益。
新审计12项检查通过；两项修订均未部署到v15。
报告：`experiments/reports/v16_shared_reentry_cpu_20261010.json`。
首次祖先复用包的完整patch SHA256为
`32c915af99003b65f4cfa952a24e9716f7a3962cefde5ffe3dffddb6af3f63ca`；
候选反向与冻结引擎增量正向检查通过，增量保存在
`experiments/reports/v16_engine_delta_20261010.patch`。该中间包已由
下述最终候选包取代，仍须等待v15完整对照结束后再部署。

下一版PREPARE排序仅在存在有效工具等待预测时查询D2H服务历史，
JOIN候选省去不参与排序或拒绝判断的传输耗时估计。容量、回收收益、
工具短等待窗口及FULL-only范围不变，41项相关检查通过。CPU基准
两侧使用相同156条真实传输种子，5种配置的选择/发布均一致；
缺少备份配置的均值下降4.51%，已全备份无租约配置却慢17.58%，
因此尚不能宣称稳定的整条PREPARE路径加速或GPU收益。保留全部
结果：`experiments/reports/v16_prepare_timing_skip_cpu_20261010.json`。
该修订仅在隔离工作树，v15运行代码不变。

2026-10-10 06:13 CST的v15消费快照中，JOIN/tool分别为53/76个
命令，FULL传输/确认首次复用为0.455/0.319 GB与0.336/0.293 GB，
ACK到first-launch P50为1157/1026 ms、P90为20426/14958 ms。
需求handoff为11391个节点命令，FULL传输/确认复用50.244/50.116 GB，
其24 ms的P50不能并入提前预测指标。553次PREPARE ACK中151次
关联后续恢复；324次后续同node/pool D2H均观察到中间Host驱逐。
该阶段已出现回收后补传，不能沿用早期“暂无关联”的快照作结论。
消费报告：`experiments/reports/v15_prefetch_sources_partial_20261010_0613.json`。

129个JOIN/tool动作中，37个ACK登记时的保护槽位预算为0，43个
未获得原生锁；41个未锁定动作仍有正字节预算。登记到实际服务
之间记录28次失去驻留、12次租约到期。9个生成工作量触发的JOIN
命令中，8个在native EOS前2.0--5.6秒提交且FULL未确认复用，只有
提前220 ms的1个确认复用。这些是动作级开发证据，不是独立样本
的预测准确率；继续分别处理提前过早与恢复后的准入等待。

下一版预取在原生before-enqueue处重新核对保护槽位并记录该次
发起预算。ACK登记可延续这次已批准的槽位额度，但仍受实时字节
预算及其他context已占额度约束；新预取不能凭旧额度启动，实际
准入仍由原生决定。首次服务、超时、物理失败及取消清理额度，
不改变既有租约时长，也不复活过期锁。容量不可观测时不沿用旧的
扩张额度。相关套件198项通过，补齐争用和ACK前服务用例后的
策略文件46项通过。v15未记录发起预算，无法断言37个案例均能被
新逻辑挽回；该修订尚未部署，吞吐验收继续等待冻结双侧完整结果。

下一版在线语义审计新增运行中快照与首次触发分解。未完成请求
保留unresolved，只有已确认工具轮次/后续请求/终止事件才归入
非自然终态；按不同child请求计数，不把多个节点命令算独立预测。
触发只关联此前已交付且数值匹配的forecast，分别报告快照年龄、
已推进token、实际采用的工作量/TPS、native EOS和child RETURN。
实时文件按固定字节长度读取，跨文件不是原子快照；只有末尾未写
完整行可跳过，中间坏行继续报错。

2026-10-10 06:30 CST生成的v15局部审计：5个estimated-work
请求均提前EOS超过2秒。触发时预计剩余1.14--4.10 token，
实际52--122 token，快照年龄299--803 ms。工作低估按当时
TPS解释2.60--4.73秒误差，实际剩余速率差解释-0.74--0.69秒；
该代数分解不是实测GPU排队时间。102个自然终态请求的最后
EOS前快照有符号/绝对工作误差P50均约46.50 token，真实剩余
P50为8。负误差早触发与末端正误差并存，不能统一加偏置解决。
报告：`experiments/reports/v15_semantic_trigger_causal_partial_20261010.json`。

同一快照的交付时刻回放中，500 ms进度扣减规则触发9个请求，
1个落在EOS前0--500 ms、6个提前超过2秒；直接保留旧快照中心
触发3个，0个落在0--500 ms、2个仍提前超过2秒。回放不含连续
调度tick、目标存活及物理准入，不能直接等同实际动作；该简单
替代没有充分改进证据，未作为新策略部署。下一版触发日志直接
保存forecast进度/年龄、推进量、采用的剩余token及TPS，减少
后续匹配歧义；充足H2D样本确认改为短路判断，样本门槛不变，
不再逐decode步完整扫描传输历史。阶段头和工作artifact未改动。

下一版需求handoff在选择新目标前先观察当前running与原生槽位。
等待队列为空或槽位为零时省去全队列因果排序、驻留扫描和新候选
选择；已有ticket继续处理ACK及剩余extent，槽位恢复后照常选择。
74项相关CPU检查通过，包含零槽位后恢复及已有传输继续推进。
156-workflow/16轮历史、每配置200次的同输入合成基准中，四种
配置候选一致；零槽位均值2.717→0.021 ms，空队列0.0253→
0.00327 ms。有槽位路径未改变，微小计时差不作为加速证据；
本轮尚无零槽位路径频率或GPU收益证据。报告：
`experiments/reports/v16_handoff_frontier_cpu_20261010.json`。
CPU基准已固定脚本所在工作树的导入路径，并记录实际模块路径与
源码SHA256；首次误导入主目录的结果已由正确版本重新生成。
既有两次准入调用模式也保留，50次比较队列一致。该修订仍只在
隔离工作树，v15两侧及后处理继续冻结。

下一版JOIN/tool PREPARE在Host FULL空间不足以容纳输入前缀时，
先用轻量祖先观察统计安全输入检查点路径上的缺失FULL，复用
已有Host副本并去重共享祖先，排除生成输出尾部。只有可确认
缺失量超过当前Host空闲时才省去完整候选闭包；无法观察时继续
原检查，实际issue仍重新构建并校验完整闭包。新增计数为
`prepare_prefix_budget_rejected_early`，不增加传输、不改备份范围。
326项相关检查通过。同输入156-workflow/24节点、每配置60次的
CPU基准使用相同156条真实传输种子，并在两侧同等清除探测退避，
测量实际探测成本；六种配置选择/发布及终态采样输出一致。
缺少备份且Host满池的JOIN PREPARE均值2.008→0.713 ms，下降
64.49%；已备份且Host满池慢3.02%，缺少备份但Host有空间慢
0.79%。保留退步，不推导GPU或端到端吞吐收益。报告：
`experiments/reports/v16_prepare_prefix_budget_cpu_20261010.json`。
该修订只在隔离工作树，v15两侧及完整后处理继续冻结。

下一版语义维护的文本表只保留尚未提交worker的快照。提交后
移除待处理帧，forecast、decode进度、请求身份和EOS证据继续保留；
缺少传输目标或可用服务进度时仍重试，新文本按原间隔合并并提交。
超出原1500 ms有效期的待处理帧释放缓冲位。128帧上限现在约束
待提交缓冲；`semantic_unchanged_frame_skipped`改为计收到的重复
已提交快照，不再计每个调度步重复扫描。模型、阶段阈值和触发
规则不变。171项相关检查通过，1项因隔离目录缺少固定encoder
产物跳过；另验证提交后的高分回复仍创建final-stage候选。
六种CPU配置、每配置400次，模型输入和接受的forecast一致。
12/48/96个已提交快照且无新文本时，维护均值下降91.38%/97.78%/
98.82%；48个为0.10810→0.00240 ms。目标暂不可用的重试样例
下降56.73%，持续新文本慢0.30%，空表慢3.29%但绝对差仅
0.0463微秒。两侧使用相同关闭计时的包装；stub worker分数为
0.25，基准不验证高分触发、神经推理、真实IPC或GPU吞吐。报告：
`experiments/reports/v16_semantic_pending_cpu_20261010.json`。
修改只在隔离工作树，v15两侧及完整后处理继续冻结。

下一版reentry检查在同一次只读调用中绑定tree/root/节点查询，
只保存最优检查点的必要字段，选择结束后才构造返回字典。每次
调用重新读取token与物理驻留，不新增跨调用缓存；保留共享祖先、
namespace、pending DMA、页对齐、64层上限、首个同长度检查点
及非可恢复尾部之前的有效检查点。76项相关检查通过。与改动前
的同输入完整路径比较，11组、每组500次的六种匹配样例P50 CPU
耗时下降1.9%--6.7%；首段分歧下降10.1%，其余分歧/短请求/页
对齐下降1.4%--3.1%。长数组字节比较在先行六种完整匹配样例中
均变慢，未采用。报告：
`experiments/reports/v16_reentry_result_cpu_20261010.json`。
这些合成CPU结果不能推导native相对GPU吞吐收益。

当前隔离候选已打包，完整patch SHA256为
`66aa563627fb8882808e290ff0ccde10535bc7d787a66eba6848baa8bcf203d8`，
相对冻结v15引擎的精确差量为
`experiments/reports/v16_engine_followup_delta_20261010.patch`，SHA256为
`dced5a847a7416c8d8abdfa591914cd743c822c1f41735d0ce852d144f58deeb`。
完整候选反向检查、冻结引擎差量正向检查和候选差量反向检查均
通过。清单为`experiments/reports/v16_engine_followup_manifest_20261010.json`。
本包包括已有的隔离运行时优化，尚未部署；v15仍使用原冻结
引擎、运行时和模型产物，双侧及后处理全部完成后再评价并部署。

v14采集、审计、HTML导出和workspace清理
已全部结束，156个workflow中154个completed、2个incomplete、
0个error。采集窗口10977.317秒，完成吞吐50.504 workflow/h，
输出700.235 token/s，GPU利用率均值65.316%。相较v13，采集
窗口缩短9.81%、完成吞吐提高10.87%；相较历史v10 native，
完成吞吐仍低5.76%。这只是开发对照：v10尚未采用本次共用
客户端优化，不能据此宣称新版predictive已获得相对native收益。
完整对比确认三轮任务集与物理池容量一致。

v14最终H2D：原生12182批/3532.278 GB；JOIN/tool提前恢复
642个命令/10.901 GB；需求handoff14667个节点命令、4681批/
125.505 GB；未知受控来源为0。FULL消费如下：

| 来源 | FULL传输GB | 确认FULL首次复用GB | ACK到首次服务P50 |
| --- | ---: | ---: | ---: |
| JOIN | 0.981 | 0.620 | 4584 ms |
| tool | 3.610 | 2.639 | 2813 ms |
| demand handoff | 70.517 | 70.128 | 25 ms |

首次服务前后续原生加载关联分别为18、3、19个目标，legacy分池
证据仍不能证明精确重复字节。1130次PREPARE ACK中206次关联
后续恢复；703次后续同node/pool D2H均有中间Host驱逐。优化
对象是备份回收后的重复补传和未消费备份，不是覆盖有效FULL。
52次压力迁出中48次关联先前PREPARE ACK。

JOIN最终审计105个节点命令对应74个不同child请求；22个命令
在原生EOS前提交，83个在EOS后提交。去重请求的EOS到客户端
finish P50为1242 ms、JOIN到parent提交628 ms、提交到原生到达
467 ms；新worker口径的提交到首次服务P50为777 ms。关闭HTTP
与JOIN到提交的重叠P50为406 ms。上述重叠
不能直接作为JCT节省。下一轮实测SDK请求/流式/工具碎片优化及
异步终态关闭能否缩短此链路，同时检查提前恢复的驻留和准入。
最终runtime为final=true、physical_disabled=false、语义worker
无错误。两个incomplete为django-11087和django-11555：前者无
语义完成且出现空reasoning重试；后者末两次root响应以length
结束并持续调查。客户端exit=1由未全部满足native JCT口径引起，
不构成已证实的实现故障。已删除154个workspace，保留2个现场；
采集后约222 GiB可用。

最终报告：
`experiments/reports/v14_native_policy_comparison_20261010.json`、
`experiments/reports/v14_h2d_sources_final_20261010.json`、
`experiments/reports/v14_prefetch_sources_final_20261010.json`、
`experiments/reports/v14_join_pipeline_final_20261010.json`。
v14 HTML：
`experiments/raw/qwen35_native_predictive_replenished_108plus48_20261010_v14/timelines/predictive_h2d.html`。
以下v13结果及带具体采样时间的v14段落是历史记录，不代表仍在运行。

v13的采集、审计、HTML导出和workspace清理
均已完成，156个workflow中154个completed、1个error、1个incomplete。
采集窗口12171.006秒，完成吞吐45.551 workflow/h；v10 native为
10345.487秒、53.589 workflow/h。同任务与容量下，v13窗口长17.65%，
完成吞吐低15.00%。两轮是实际生成轨迹，源码与遥测版本亦有差异；
上述为开发对照，不能作为轨迹相同的因果性能结论。
完整来源v2对比确认任务集与物理池容量一致。80--90分钟两轮
running均约47，v13/native的GPU利用率为48.56%/58.53%，输出
约509/742 token/s，排队均值65.67/44.46。因此差距不限于最后
一个workflow的长尾。v13累计已埋点exclusive Python wall区间
1734.347秒，其中JOIN PREPARE398.406秒、机会采样188.376秒、
reentry检查184.171秒；它们不是GPU空闲或可直接扣除的JCT。
继续验证批次服务、恢复等待和共用控制成本，不能仅归因于轨迹。
完整报告：
`experiments/reports/v13_native_policy_comparison_20261010.json`。

v13最终H2D来源v2审计：原生3839.402 GB，JOIN/tool提前恢复
760次/11.824 GB，提交后的需求handoff 18008次/158.846 GB，
未知受控来源为0。旧冻结报告中的18768次PREFETCH_GPU包含
handoff，不可全部称为预测H2D。分来源FULL消费如下：

| 来源 | FULL传输GB | 确认FULL首次复用GB | ACK到首次服务P50 |
| --- | ---: | ---: | ---: |
| JOIN | 0.908 | 0.453 | 7058 ms |
| tool | 3.833 | 2.695 | 3220 ms |
| demand handoff | 81.708 | 47.154 | 815 ms |

FULL复用使用proof_version=2。首次服务前后续原生加载关联分别
为31、8、599个目标；legacy分池关联仍不足以证明精确重复字节。
16087次PREPARE ACK中180次关联后续恢复，15603次后续同节点/
同池D2H均有中间Host驱逐。需要减少不被消费的备份与回收后补传，
不能以“后续覆盖”否定有效FULL副本的增量复用。

JOIN最终审计127个节点命令对应83个不同child请求，仅19个命令
在原生EOS前提交。去重请求的EOS到客户端结果P50为1534 ms，
结果到RETURN为425 ms，JOIN到parent提交866 ms，parent提交到
首次服务861 ms。v13未启用HTTP/finish-chunk细分计时，不能将
EOS到客户端的差距直接归因于某一解析或网络函数。

后续修订已合入主目录，SGLang增量已部署并通过完整staging patch
反向适配检查。v14已从50b9179冷启动，同一156任务、108+48到达、
running48、Host200GB80:20、HBM Mamba/FULL0.9及原模型/预测头/
prompt/seed21均保持冻结，验证
工具候选100ms节流、FIFO异步LLM_RESULT、工具负证据与帧合并、
按batch服务索引、恢复驻留保护、有界恢复准入、实际FULL冷回收
时保存必要Mamba及完整缺失检查点Host预算。投机PREPARE仍仅
传缺失FULL段；完整路径预算不是Host容量预留。本轮启用已有
HTTP/finish-chunk计时，继续以消费、等待、重算和吞吐验收。
这组修订尚无GPU性能收益证据。

2026-10-10 02:20 CST运行快照：physical_disabled=false，语义worker
错误为空，PREPARE池范围为missing_full_prefix；已有JOIN/tool及需求
handoff ACK，不能将后者并入预测H2D覆盖。运行1119.96秒时已埋点
exclusive Python wall为214.68秒，JOIN PREPARE33.00秒、reentry
检查29.75秒。该早期快照不是完整实验结果，也不能直接解释GPU空闲。
本轮driver、导出及清理结束前，不向服务目录部署后续代码。

后续reentry CPU优化在既有隔离工作树开发。原只读检查用match_at
计算最长公共前缀，却只判断整段节点是否匹配；现用is_prefix_at
一次比较所需完整段，保留namespace/salt、offset、limit、bigram
边界与页对齐语义。原生匹配/分裂/分配、FULL/Mamba依赖、DMA及
session有效性检查均沿用现有逻辑，不缓存物理驻留结果。
使用实际RadixKey和相同合成radix祖先路径比较完整reentry，两侧
输出一致；完整匹配4K/32K/96K路径耗时下降47.4%--58.8%，32K
单叶为102.07→53.66微秒，8叶为711.06→355.06微秒。前缀分歧、
短请求与页对齐样例同样改善。87项相关CPU检查通过，旧Mamba
保存测试夹具同步加载其已拆分的辅助方法。这只是CPU及正确性
证据，不代表GPU吞吐收益；该修订未计入运行中的v14。
基准脚本：`scripts/benchmark_native_reentry_cpu.py`。
报告：`experiments/reports/v15_reentry_cpu_benchmark_20261010.json`。

后续闭包观察优化保留所有字段及容量/锁/引用校验，减少每节点的
临时生成器、tuple和对象字典；没有缓存驻留状态或放宽驱逐条件。
修正CPU基准的历史依赖加载，使baseline同时使用其observer、
physical和runtime，避免以当前observer冒充历史版本。对19650ab
的156-workflow、64节点路径、40次交错比较，缺失备份、Host满池及
Host-only PREPARE平均耗时下降14.4%--15.4%，机会采样下降
11.2%--12.8%，选择、发布和采样目标一致。已全备份路径的PREPARE
约持平或下降3.2%；首轮20次样本受离群值影响，不能宣称所有路径
都有固定比例收益。144项observer/physical及88项runtime检查通过。
报告：`experiments/reports/v15_closure_prepare_cpu_repeat_20261010.json`。
该修改仅在隔离工作树，仍须等v14完整driver结束后部署。

2026-10-10 02:53 CST的新增JOIN链路审计仍是运行中快照：60个
节点命令对应37个child请求，45个命令在原生EOS之后提交。按请求
去重，原生EOS到客户端finish-chunk的P50为2962 ms，finish-chunk
到LLM_END回调入口为94 ms，回调入口到LLM_RESULT仅0.112 ms；
结果到RETURN为103 ms，JOIN到parent提交859 ms，提交到服务
678 ms。不可把数秒的客户端消费延迟当作准确预测生成结束的提前量。
django-11400的一条请求中，EOS到finish-chunk为10437 ms，
其后回调入口448.5 ms；HTTP累计consumer pause为13638.7 ms，
raw pull为1253.9 ms。后两项覆盖整轮流，不能相加解释EOS之后
的耗时，也不能分别等同于纯CPU或网络时间。该parent预取锁在
1500 ms到期，首次服务没有确认FULL分配复用。
现有实现已经要求最后一个未完成child；此处未证实多child误触发。
下一步优先减少客户端积压和JOIN到提交的开销，再验证有界驻留
与恢复准入，不能仅把投机租约延长到十几秒。
报告：`experiments/reports/v14_join_pipeline_http_partial_20261010.json`。

本轮客户端8秒非阻塞GIL采样取得204个有效样本，94次采样失败。
热点栈包括SDK消息类型转换、增量工具参数JSON解析与消息对象
构造；它不能精确解释上述单请求延迟或推导GPU空闲占比。后续
优化应核对最终请求/工具语义不变，并保留低开销客户端消费计时。
采样：`experiments/reports/v14_client_gil_profile_partial_20261010.txt`。

SDK消息转换热点的第一项后续优化已实现：仅对带BeliefKV运行时
标识的Chat Completions，将LangChain已转为wire schema的消息经
extra_body合入最终JSON，避免SDK再次逐层遍历完整历史。工具
定义、采样参数、元数据和响应解析仍走原路径；原有extra_body
显式覆盖语义保留，未标识请求与Responses转换不变。该路径在
reactive/predictive/native harness中共用，不构成新的agent guard。
本机版本为openai2.6.1、langchain-openai1.1.9、httpx0.28.1。
实际SDK及MockTransport比较最终请求JSON，同步/异步及SSE/
非流式均一致；54项既有adapter与7项请求协议检查通过。
完整请求转换、SDK构造、JSON编码和响应解析CPU基准，30次交错
样本的平均耗时为：11条消息2.711→0.835 ms，67条13.032→
1.284 ms，259条48.162→2.752 ms，下降69.2%--94.3%。
记录源码SHA256、最终canonical JSON SHA256和安装版本；这些
是合成消息的CPU证据，不能宣称已消除2.96秒消费积压或带来相同
比例的GPU吞吐改善。增量工具JSON及流式对象构造尚待分别优化。
基准：`scripts/benchmark_child_request_payload_cpu.py`；
报告：`experiments/reports/v15_client_payload_cpu_20261010.json`。
仍未部署到v14；完整driver、审计、HTML及清理退出后才能应用。

流式对象往返的后续优化也已实现：带运行时标识的普通Chat流继续
使用SDK的SSE解码、错误处理及关闭机制，将已解码数据直接交给
LangChain，省去每帧构造SDK类型对象后立即model_dump的往返。
同步/异步路径均生效；response headers、结构化响应、非流式、
未标识请求及自定义client沿用原资源。没有替换SSE解析器或
修改最终工具参数，增量工具JSON仍由LangChain原逻辑处理。
71项相关检查通过，涵盖逐帧/最终结果、usage、finish、工具参数、
流内错误与提前停止时响应关闭。30次交错合成CPU对照的67/515/
395帧同步均值为9.419→2.034、70.291→13.497、63.662→18.140 ms；
异步为9.544→2.168、69.862→16.692、65.006→18.705 ms，
下降71.2%--80.8%。395帧包含136段工具参数碎片。最终请求JSON
及输出相同，记录thread CPU与wall clock、安装版本和源码/输出
SHA256。该基准不含网络、GPU及callback工作，不能证明实际
JOIN消费积压已消除或端到端吞吐提升；本项仍仅在隔离工作树。
基准：`scripts/benchmark_child_stream_cpu.py`；
报告：`experiments/reports/v15_client_stream_cpu_20261010.json`。

增量工具参数的后续优化已完成：单个碎片的非对象前缀不可能通过
删除末尾字符变成JSON对象，因此直接保留等价的原始工具碎片及
invalid_tool_calls，省去LangChain反复裁剪和解析。对象前缀、
非标准字段及最终合并参数仍由继承路径解析；未修改工具执行或
普通正文语义。89项相关检查通过，包含短前缀组合、长字符串、
并行工具调用、不同碎片大小和最终结果一致性。
60次交错的实际SDK SSE合成CPU对照中，8/64/1024字符碎片同步
均值为9.813→5.803/17.546→3.527/189.548→3.084 ms，异步为
10.198→6.177/17.021→4.579/189.382→3.258 ms，下降39.4%--98.4%。
512帧纯正文同步13.330→13.444 ms，异步14.747→13.533 ms；
首轮30次异步正文均值受60--80 ms离群值影响出现退步，保留该
报告。复查保留并计入GC，记录其暂停而不修改生产GC设置；
纯正文异步P50约11.366→11.414 ms。该证据不包含网络、GPU
或agent callback，不能推导实际吞吐收益。两份报告：
`experiments/reports/v15_tool_fragment_cpu_20261010.json`、
`experiments/reports/v15_tool_fragment_cpu_repeat_20261010.json`。
该修改仍仅位于隔离工作树，不部署到运行中的v14。

终态session关闭的后续优化已实现：child RETURN立即标记context
终态并向独立共享线程池提交关闭任务，HTTP不再持有session锁或
阻塞child future及parent下一请求。实验统一使用最多32个关闭
worker，不复用workflow执行池；native/reactive/predictive采用
同一harness路径。workflow收尾等待所属关闭任务，失败保留同一
session身份并重试，全部任务完成后才关闭audit。真实compaction
的同步关闭语义保留。新增排队、HTTP开始/完成计时。
核对SGLang原生实现：/close_session的HTTP200只表示消息派发，
并不等待scheduler释放引用；旧native reference close ACK表述
过强。观察到引用释放也不能等同于物理页回收。
运行中v14的04:03 CST审计包含95个节点命令、67个child请求。
去重后JOIN到parent提交P50为695 ms，child关闭HTTP与该区间的
重叠P50为419 ms；所有匹配parent提交均在该child HTTP完成之后。
这是路径顺序及区间重叠证据，不能将419 ms当作隔离后的JCT收益。
同快照原生EOS到客户端finish P50为1444 ms，76/95个节点动作
在原生EOS后提交，客户端消费积压仍须检验。
225项session/adapter/harness检查通过、1项跳过，另9项JOIN
审计检查通过。并发检查覆盖阻塞HTTP时parent请求仍可继续、
终态身份不可重用、去重、同身份失败重试及报错前完整清理。
本项仍仅在隔离工作树，等待v14完整driver退出后部署；当前没有
GPU吞吐收益证据。快照报告：
`experiments/reports/v14_join_pipeline_session_close_partial_20261010.json`。

终态缓存采样的后续优化只改变多锚点序列化顺序：先按node/creation_time
去重不可变summary，再将唯一节点转为字典；单锚点保持原路径。每个锚点仍独立观察
实时祖先，保留最后一次观察值和原有输出顺序；不在物理动作或
跨叶闭包校验中复用旧驻留信息。现有终态采样检查扩展共享祖先
状态变化场景，2项通过。最近1000条运行日志中的220个终态样本，
130个有两锚点，90个单锚点，不能据此推断整个实验的分布。
60次交错CPU基准中，最终24/64节点双锚点均值下降32.9%/35.1%；
八锚点下降58.3%/61.2%，只是较大的合成边界。最终单锚点约持平，
24节点慢0.41%，64节点快0.19%。中间版本单锚点复查慢2.55%，
因此增加原路径分支；中间报告仍保留。输出字段、顺序及
native观察次数一致，单独记录wall/thread CPU和源码哈希。
该修改仍在隔离工作树，不计入运行中的v14，也没有GPU吞吐
收益证据。报告：
`experiments/reports/v15_terminal_sampling_cpu_final_20261010.json`、
`experiments/reports/v15_terminal_sampling_cpu_depth24_final_20261010.json`。

2026-10-10 04:51 CST运行快照：150个workflow已有终态，其中148个
completed、2个incomplete、0个error，6个未结束。物理动作保持启用，
语义worker无错误，PREPARE仍为missing_full_prefix。两个incomplete
分别为django-11087与django-11555；前者没有语义终态，后者末两次
root响应以length结束且未完成任务。尚无已证实的实现故障，不能因
自然未完成而停止本轮。完整driver及后处理仍在运行。

04:54 CST运行中来源审计：JOIN/tool提前恢复642次/10.901 GB，
需求handoff14667个节点命令/125.505 GB，原生H2D为3532.278 GB，
未知受控来源为0。handoff的FULL传输70.517 GB、确认首次复用
70.128 GB，ACK到服务P50为25 ms；JOIN/tool的FULL分别传输
0.981/3.610 GB、确认复用0.620/2.639 GB，ACK到服务P50仍为
4584/2813 ms。需求恢复的消费明显改善，但不能用handoff替代
提前预测传输的验收。1130次PREPARE ACK中206次关联后续恢复；
702次后续同node/pool D2H均有中间Host驱逐，应继续减少回收后
补传，不能称为覆盖有效FULL。上述为运行中快照，不是吞吐结论。
74个不同child请求的原生EOS到客户端finish P50为1242 ms，
JOIN到parent提交628 ms，其中关闭HTTP区间重叠406 ms；
重叠不等于可直接扣除的JCT收益，部署后的共用路径仍需实测。
报告：`experiments/reports/v14_h2d_sources_partial_20261010_0454.json`、
`experiments/reports/v14_prefetch_sources_partial_20261010_0454.json`、
`experiments/reports/v14_join_pipeline_partial_20261010_0454.json`。

下一版代码已提交至隔离分支edbe4dc，包括reentry、闭包观察、SDK
请求与流式转换、工具碎片解析、异步终态session关闭和终态采样。
完整SGLang patch的SHA256为
`dbde39b7f37977ecacd72dddf78b3da94a56fa6a0879afb1798ae55ab7f1fe63`；
与候选引擎反向检查及对原始upstream临时index的正向检查均通过。
逐文件比较完整patch涉及的32个路径：当前服务目录已包含Mamba
冷回收辅助逻辑，实际差异仅为radix_cache、unified_radix_cache及
两个测试文件，共149行新增、1行删除。已生成
`experiments/reports/v15_engine_delta_20261010.patch`，对服务目录的
正向检查和候选目录的反向检查均通过；其SHA256为
`d1a1720cace4b06557d728f709a638d0644ab3727d19c11576b3581b7ced38a0`。
部署只应用该精确差异，再核对完整staging patch；不能直接叠加
候选引擎的全部未提交diff。
v14完整driver、审计、HTML与workspace清理现已退出，可以统一部署。
直接调用对比脚本的仓库导入路径已修复，三轮实际对比报告生成成功。

下一轮v15使用既有双arm驱动，顺序为predictive_h2d、native，
两侧各冷启动，采用同一最新代码、模型/预测头及156任务108+48到达
配置。SDK请求/流式/工具解析与终态关闭优化均在native、reactive、
predictive的共用harness生效，不能只更新predictive后沿用v10 native
作为收益结论。分别报告JOIN/tool提前恢复、需求handoff、原生H2D，
核对FULL首次复用、ACK到服务、重复恢复、PREPARE回收后补传、
重算、吞吐和JCT。同seed不保证同轨迹；本轮仍为开发对照，正式
实验的多轮平均要求保持不变。

最终报告：
`experiments/reports/v13_h2d_sources_final_20261010.json`、
`experiments/reports/v13_prefetch_sources_final_20261010.json`、
`experiments/reports/v13_join_pipeline_final_20261010.json`。
v13 HTML：
`experiments/raw/qwen35_native_predictive_replenished_108plus48_20261009_v13/timelines/predictive_h2d.html`。
已删除154个归档workspace，保留2个现场。下文“v13保持冻结”
属于该次采集期间的修订记录，不能解释为仍在运行。

## v11 至 v13 修订记录

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

独立工作树补充工具H2D候选扫描节流：按现有100ms观察周期筛选，
接受新hint或处理完本动作ACK后立即重查，在途ACK及ticket失效
仍逐轮处理，真正enqueue继续核验当前状态。125项原有检查及
2项时间窗口/新hint检查通过。156个长期工具等待、5000次2ms
调度调用的CPU对照，候选检查780000→15600，累计14.136→0.297
秒，减少97.90%。它只测传输窗口外的扫描，不代表GPU吞吐。
报告：`experiments/reports/tool_candidate_scan_cpu_156_20261009.json`。
该修改用于后续冷启动，v13保持aa93dde冻结版本。

v13运行中已发现实际恢复后驱逐：同一request/context/epoch的
3个handoff extent在ACK后76--102ms失去原生驻留，随后原生再次
恢复该检查点。对应租约均未获得native lock，完整祖先前缀约
367MB，超过只按free-list计算的保护余量。这不是单纯的复用
口径问题；纯原生合并批次仍不足以推导精确重复字节。
后续handoff保护可使用原生free+evictable容量，预留下一批input、
运行请求页增长及新Mamba slot；只锁已恢复数据，不凭此允许向
已占用页传输。推测性JOIN/tool恢复仍只使用free-list余量。
新handoff候选限制在下一批可准入名额，最多16个；同一请求的
多个extent共享一个名额，字节仍保守按各锁闭包计入。真实NO_TOKEN
会按request一并释放该handoff的锁，避免只解开重叠路径中的一个
锁却没有释放可回收容量。首次服务、身份失效及3秒到期照常释放。
这些修改位于独立工作树，须在v13冻结driver退出后冷启动核验。

JOIN时间审计现已分离同一child请求的原生生成结束、客户端结果
回调、自然RETURN及parent下一请求提交/首次服务；核对invocation、
context和epoch，并同时报告动作口径与去重child请求口径。
v13运行中76次已完成RETURN的动作样本，原生结束到客户端结果
回调P50为4788.94ms，回调到RETURN为1026.04ms，JOIN到parent
提交为1234.89ms。不能将这些区间归因于某一个函数，也不能将
全部提前量解释为剩余工作头的误差。局部报告：
`experiments/reports/v13_join_completion_pipeline_partial_20261009_2320.json`。
后续修订将LLM_RESULT加入已有FIFO异步控制交付，callback无需等
ACK后才发完成hint、执行工具或RETURN；workflow结束仍检查此前
交付，失败仍标记测量降级。含WORKFLOW_END与root RETURN的混合
batch明确等待最终交付，避免被RETURN的异步条件误放行。分别
记录LLM_RESULT排队/ACK分位数。
它消除一段明确的同步等待，不解决回调入口之前全部HTTP/框架
消费延迟；下一轮需启用已有HTTP流及finish-chunk计时来定位。
v13采集保持冻结，不能将尚未运行的修订计入其性能结果。

客户端工具流通知合并：首个tool chunk立即发出负信号；正文变化
仍按原有阈值/100ms观察，结束chunk仍记录，不再为纯参数增量
反复发送相同正文。v13局部计数131187条语义帧中79635条为工具
帧，其中63072条内容/工具标志与前一帧相同；这是消息冗余证据，
不能据此计算实际GPU收益。原生保留同request的工具负证据直至
下一请求/终态清理，晚到的正文或finish帧不能重新激活该请求的
RETURN预测，不取消agent或工具。后续launcher在两种策略下同时
开启已有finish-chunk与HTTP流计时，用于区分消费延迟的来源。
本组155项相关CPU检查及shell语法检查通过；FIFO结果交付组65项
通过。以上均为后续冷启动修订，尚无这一版本的GPU收益证据。

H2D来源审计升级为schema_version=2：predictive只含join_ticket/
tool_wait，execution_handoff单列，缺少明确来源的tagged传输列为
unknown_controlled，未打标签的批次余量列为native。controlled
保留受控传输合计；不再将提交后的handoff称为提前预测。来源优先
匹配issued command，旧记录可使用ACK携带的source。每个批次
按child receipt核对字节及池单位守恒，混合批次时间整体报告，
不按字节比例推导某一类动作的耗时。40项相关CPU检查通过。
v13运行中局部报告显示JOIN/tool 9.927 GB、handoff 134.073 GB、
native 3486.746 GB；采集时仍有16.466 MB tagged数据来源未确认，
不能补判为预测或原生。该结果不是完整实验或吞吐收益结论。
报告：`experiments/reports/v13_h2d_sources_partial_20261009_2356.json`。
脚本修订在独立工作树中提交，当前v13及其冻结报告保持原版本。

服务完成遥测按batch建立一次request_id索引，替代每个sample再次
线性扫描batch；保留批次过滤/重排后的同request匹配及原有输出
token计量。16/48请求CPU样例同时核对完整服务记录和token归因，
包含移除部分请求的情况；48请求的P50由116.04降至43.04微秒，
移除部分请求时由102.39降至33.53微秒。52项现有相关检查通过。
报告：`experiments/reports/native_service_lookup_cpu_48_20261010.json`。
样例不含writer、DMA或GPU，不能据此推导完整服务成本或吞吐收益。
这一修订仍只用于v13结束后的冷启动版本。

FULL-only PREPARE的回收路径进一步修正：此前普通FULL叶驱逐会在
实际驱逐前保存活跃Mamba，但handoff和等待parent回收要求所有
组件事先已有Host副本，且候选发布阶段就排除了未备份Mamba。
新版本允许FULL已备份、Mamba未备份的独占冷叶成为FULL候选；
仅在真实分配短缺且选中该叶时，按原生write-back保存仍被session
引用的Mamba检查点。ACK后重新检查节点身份、FULL Host副本、
引用、锁和在途DMA，确认恢复状态有效才释放HBM。无引用Mamba
可随原生回收释放；有用状态保存失败则保留该叶并继续原生路径。
该修改不恢复投机Mamba PREPARE，不主动制造压力。150项运行时
检查和71项引擎检查通过，另有16项引擎subtest。它解释了一个
候选遗漏机制，尚不能量化v13中因此丢失的全部迁移机会。
v13保持冻结；该修订在driver退出后部署并以同配置冷启动验证。

恢复就绪准入调整为有界消费：之前队首等待超过10秒后会一直阻止
恢复优先级，即使此前已完成四次普通准入。新版本保留老化请求的
原有排序，并在至少四次普通准入之间允许一次已提交、恢复就绪
请求优先消费；不依赖root身份或未来预测来绕过真实执行条件。
拒绝准入不消耗配额，记录有界越过老化队首的排序与实际准入次数。
191项相关CPU检查通过，含连续三轮四次普通准入/一次恢复准入。
该改变针对ACK到服务和再次丢失，尚无其GPU吞吐或其他workflow
延迟代价的实测结论；同样在v13结束后的冷启动中验证。

生命周期审计新增按join_ticket/tool_wait/execution_handoff分组的
FULL传输、首次复用字节与ACK到服务分布；只统计实际携带FULL的
命令，分池字节使用已核对physical ACK，缺失旧证据单独报告。
17项相关审计检查通过。v13局部样本的JOIN/tool/handoff等待P50
分别为7861.71/3220.14/815.37ms，合并P50为1047.01ms，不能用
合并值证明JOIN及时消费。JOIN确认FULL复用0.424/传输0.880GB，
tool为2.695/3.833GB；这些是节点命令与proof v2字节，非独立
workflow样本或端到端收益。局部报告：
`experiments/reports/v13_prefetch_sources_partial_20261010_0034.json`。
同一局部样本12889次后续同节点D2H之前均有Host驱逐，继续支持
备份回收后补传的解释。冻结driver输出保留，修订报告另行生成。

PREPARE进一步检查完整缺失前缀的预算。v13局部13435次动作中，
9723次只传不超过64个FULL token；同一个1-token共享节点反复
备份7471次，未观察到其后续恢复关联。原生write-back会优先
回收HBM中仍有副本的Host FULL，不能据此指责增量备份覆盖有效
数据。旧选择只检查当前单节点是否放得下，没有要求剩余可复用
检查点前缀也能放入Host。新选择按检查点路径上的全部缺失FULL
计算预算，跳过容量不足的候选；已备份节点与检查点后的生成
输出均不计入。实际仍只传当前缺失段，不预留或锁定整条Host
路径，原生enqueue继续检查容量。这限制无法完成的部分备份，
不保证消除Host驱逐或所有重复补传。214项相关CPU检查通过，
下一轮分别核验重复节点动作、消费、成本和有效迁移机会。
该修改仍在独立工作树，v13保持冻结。
后续ab_plan同步记录完整缺失前缀预算、有界恢复优先准入及已
开启的HTTP/finish-chunk计时，保留实际策略与测量版本。

完整策略对比报告也使用同一来源拆分。新增`h2d_sources`及时间
分段的`h2d_source_*`字段，单列JOIN/tool、需求handoff、未确认
受控来源及原生余量；原有`transfers`保留动作名称口径，不能把
`PREFETCH_GPU_h2d`合计称为预测迁移。混合batch按receipt守恒
字节及池单位，分类batch次数可重叠；不按字节比例分摊传输时间。
ACK索引在同次对比中复用，避免额外遍历ACK日志。26项相关CPU
检查通过，完整v10 native复算仍为3202.074 GB原生H2D，提前
预测与handoff均为0。复算报告：
`experiments/reports/v10_native_source_comparison_20261010.json`。

v13运行中新出现的django-12273 incomplete已定位：两个child均
正常RETURN、JOIN满足且workflow deadline未触发；root最终
输出8192 token，以length结束，约29554字符正文仍重复分析同一
问题，没有完成声明。该项保留incomplete，不通过增加guard或
重判completed处理，也不据此认定H2D实现故障。django-12262的
ReadTimeout仍保留为未完全定位的错误，后续流计时用于补齐证据。

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
