# BeliefKV 当前架构与实现状态

更新日期：2026-10-11。

工具预取隔离跟进修复候选之间的容量阻塞：原先最临近工具结束的
agent先占唯一ticket，随后发现恢复容量不足就结束整个扫描，
后方可恢复的候选必须等下一轮。现在在选择ticket前沿用原生
冷副本回收，容量仍不足或恢复目标消失时继续检查后方候选。
回收可能改变批量恢复范围，因此重新核对传输估计和既有启动
窗口；下发时仍按原路径复核。没有改变预测分位数、500ms配置、
池预算、原生准入或传输资格。4种双候选场景和相关447项检查
通过；实际发生频率、控制成本及吞吐贡献仍待下一GPU实验。

工具选择修复、固定前缀审计及文档已提交为606cc4e。
2026-10-11 10:01:31 CST将尚未启动的V23更新为
606cc4eb2ded154a8b1c79dd7a8a31e26483b1fb，
冻结checkout为beliefkv-v55-tool-capacity-selection-20261011，
等待协调器PID3111032。环境、前序pair、等待进程身份和原生
patch与18ea788登记一致；主checkout仍为干净的3aa27dd，
V23目录尚未创建。仅停止旧waiting coordinator2903493，
V22采集进程未发信号。旧登记及日志保留，回执为
v55_v23_tool_capacity_registration_20261011.json；
实际登记仍在主工作区v23_launch_after_v22_20261011.json。
V22完整pair与后处理结束后自动部署，predictive先、冷native后。

补齐工具窗口离线审计的H2D服务时间比较，保存
v54_v22_tool_service_window_fixedprefix_20261011.json及本地详细产物。
重读与v48完全相同的817177733字节opportunity前缀和同一生命周期
标签；来源SHA256、原有误差和窗口统计均逐项一致。原来的
timely_candidate只表示预测及真实剩余时间均进入启动窗口，
并不保证能覆盖H2D。655条这类快照中，361条真实提前量短于记录
的H2D P90；有空闲容量的100条中56条亦如此。真实提前量不短于
记录P90且有空闲容量的仅44条快照、42个工具轮次，其逐轮最大
观测FULL合计732037120字节，不是实际唯一传输或可实现收益。
快照分类可在同一轮次重叠；P90只含submit→ACK估计，未含计划和
enqueue开销。所有timely快照的真实剩余P50为83.50ms，而记录
H2D P50为87.97ms；不能把总体误差P50为63.57ms当作传输已及时。
例如xarray-7393的child epoch57有80.630MB计划，但真实只剩5.052ms，
记录H2D P90为87.966ms。按v47的原生transfer submit时间，
实际提交晚于计划9.271ms、晚于工具完成4.220ms；
稍后控制记录的17.510ms不能当作原生submit延迟。继续分别优化
预测/交付提前量、物理容量和服务消费；
本次审计不增加runtime门槛，也不改变冻结V22。

终态child容量交接的隔离跟进继续修复回收候选覆盖：JOIN/tool
预取和已提交请求的execution handoff原先先发布每池最多8个候选，
再由native排除恢复目标的完整前缀。目标自身的已有备份节点可能
占满名额，导致后方其它安全冷节点没有入选。现在两个调用点先
生成同一份目标节点集合，在候选发布计数前排除，再交给native
复核。FULL/Mamba各自的8项预算、原有顺序和全部实时资格检查
保持原语义；第16个字典位置即使被排除也继续初始化稀疏池索引，
索引候选同步排除目标，避免已排除节点阻止及时结束扫描。
注册表保留全部节点供后续不同目标复用。包含前8项全属恢复目标、
FULL/Mamba稀疏池和有/无索引的6种回归场景；相关367项检查通过。
尚未测清该限制在V22中的发生频率，也未证明GPU吞吐改善。

候选修复与上述文档已提交为18ea788。2026-10-11 09:42:56 CST
将仍未启动的V23登记更新为18ea788a0ea1933782eb5051e211c866e2e29579，
冻结checkout为beliefkv-v53-restore-target-candidates-20261011，
等待协调器PID2903493。运行配置、前序pair、等待进程身份和原生
patch均与e7bd8fa登记相同，V23目录尚未创建；主checkout继续
保持3aa27dd且干净。仅停止旧waiting coordinator2616411，
V22 coordinator/driver/server/scheduler/client均未发信号。
旧登记保存为v23_launch_after_v22_e7bd8fa_superseded_20261011.json，
当前登记仍为主工作区v23_launch_after_v22_20261011.json；
有界回执为v53_v23_restore_target_registration_20261011.json。
V22完整pair及后处理结束后自动部署，predictive先、冷native后，
真实容量、驻留复用和繁忙窗口收益仍待新版本GPU验证。
该09:42登记现已由上方10:01的606cc4e登记替换。

新增完整2400–3000秒繁忙窗口，局部产物为
v52_v22_busy_control_2400_3000_20261011.json，保存源读取前缀和
边界SHA256。14015个decode batch的平均可见上下文为24406.595
token，worker区间均值33.873ms；已埋点Python独占墙钟累计
55.805秒，其中reentry检查68439次/10.928秒、control drain
6.737秒、opportunity采样4.721秒。后台writer的request_probe
63个实测样本平均thread CPU10.671ms、墙钟11.946ms；
不能按采样间隔外推总耗时或GIL影响。同版本native仍待运行；
候选覆盖修复与已排队writer/驻留优化共同进入下一冻结实验验证。

V21完整pair和后处理已结束：predictive为155个completed、
1个incomplete，native为156个completed。2026-10-11 07:49:16 CST
已自动部署V22的3aa27dd；当前运行predictive，结束后自动冷启动
同版本native。冻结checkout为beliefkv-v35-fragmented-handoff-20261011，
协调器PID1159560，driver PID1166290，scheduler PID1166676，
client PID1168306。登记状态为running_new_pair，原生patch的
expected/actual tree均为63d91509bee139b2d6a2285eb44b193a67a9e008。
保留v22_supersession_08b804f_to_3aa27dd_20261011.json；
实际启动登记位于主工作区experiments/reports/
v22_launch_after_v21_20261011.json。V22取消需求恢复的累计16-extent
截断，保留单批16-extent、
实时容量/pending额度、原有有效期及原生依赖；纳入writer窗口
分析和按epoch绑定的工具轮次审计修正，保留终态child
容量交接、容量结算后parent恢复重试、后台写入CPU证据、
前缀写入减负、首次服务公共前缀比较复用、完整祖先观察、
已老化准入检查减负和同次tool恢复计划复用；
08:20 CST前已观察到真实GPU请求、终态child回收、parent首次服务及
物理动作ACK；native证据writer的dropped/failed均为0，
writer_error为空。已采到下述完整繁忙窗口和局部容量交接证据；
同版本native仍待后续运行，吞吐目标尚未达成。

V22在线局部容量审计保存在
v40_v22_capacity_handoff_live_20261011.json。117个观测JOIN中，
54个已有完整完成边界，63个边界尚未观测；不能将后者当作失败。
50个JOIN在完成前有确认dynamic subagent的设备容量释放；
其中26个随后在JOIN前实际提交FULL，并由parent下一请求确认复用，
合计259522560字节。相关JOIN前累计释放FULL9.558GB和
Mamba5.216GB，并非同时可用或独占parent的容量。140个确认终态
child累计实际释放FULL30.980GB、Mamba9.594GB；内部调用释放
另行统计。54个parent首次服务均有关联child设备释放，服务端观察
最后child RETURN至首个已完成GPU batch的P50/P90为
327.57/1247.41ms，最长16.312秒；这不是RETURN预测误差或kernel
启动延迟。21个parent先经过恢复lookahead再服务；lookahead仍是
选择证据，不等于传输提交。当前无容量重试rearm事件，不能据此
声称真实压力下已验证重试路径。该报告读取仍运行的多路日志，
是局部观察，不是冻结终态或与native的收益对照。

09:10:53 CST生成的V22完整生命周期局部审计位于
v47_v22_prefetch_lifecycle_live_20261011.json；其有界摘要和来源SHA256
保存在v49_v22_capacity_handoff_progress_20261011.json。256个确认动态
child累计释放设备FULL72.814GB、Mamba37.217GB；内部调用不混入
child计数。169个观测JOIN中109个已知完成边界、60个尚未观测；
101个在完成前有关联sibling设备释放，56个随后实际在JOIN前提交
FULL且由parent下一请求证明复用，合计935055360字节。所有JOIN
提前且复用FULL合计1.072GB；需求handoff确认复用113.663GB，继续
单列。111个parent首个完成GPU batch均关联先前child设备释放，
scheduler最后child RETURN观测至该batch的P50/P90为264.40/767.29ms，
最长16.312秒；不是预测误差或CUDA kernel启动时延。仍无容量重试
rearm记录，不能用本快照认定实际压力重试已验证。

该快照定位到驻留交接的一处缺口：django-11141的parent在原生入队
57ms后被选中恢复，下发时有1个frontier额度，约51ms后ACK时额度
已为0。3个handoff extent均未保留下发额度，未加驻留锁，并在ACK
约386ms后观察到设备驻留丢失；约16.29秒后才进入首个GPU worker
区间，该区间只有20.99ms。它的6.922MB FULL未获首次复用证明，
下一请求仍有176.947MB原生FULL Host hit。不能把这个长等待全归为
GPU执行或传输，也不能据此声称新修复已经消除排队。

隔离跟进将单节点和批量需求handoff纳入已有的下发保护额度记录，
ACK沿用同一原生容量来源的已下发槽额度，继续实时核验字节、
其它context的已有保护、页身份及原生依赖。仍是原有3秒需求租约；
首次服务、失效和分配压力均按原路径释放，不额外预留GPU running
slot，不增加准入门槛。未提交的批量尾节点同步清除额度。174项
相关runtime、handoff、终态容量及传输预算检查及187项公共工具/
runtime检查通过，合计361项。该修改尚未部署到冻结V22。
2026-10-11 09:23:57 CST已将等待中的V23登记更新为e7bd8fa，
完整revision为e7bd8faa08d380a33cb88ee98b71e5e3303a1917，
冻结checkout为beliefkv-v50-handoff-issue-protection-20261011，
协调器PID2616411。运行配置、前序pair和canonical原生patch均与
上一登记相同；仅停止旧waiting coordinator2402814，V22进程未发
信号。旧登记保存为v23_launch_after_v22_3644e8b_superseded_20261011.json；
回执为v50_v23_handoff_protection_registration_20261011.json。
该09:23登记现已由上方09:42的18ea788登记替换；GPU效果及同版本
native收益待验证。

工具窗口局部诊断v48_v22_tool_forecast_live_20261011.json由上述完整
生命周期关联和带读取SHA256的opportunity前缀生成。6549个具有最后
1秒forecast的独立工具轮次，其轮次内中位绝对误差的P50/P90为
63.57/572.06ms；不能当作未见项目或全部工具的保证。575个工具轮次
有记录中的及时FULL候选，仅95个同时观测到空闲容量；原因会跨快照
重叠。接下来结合真实释放、驻留和准入解释覆盖，不能直接把约
421.44GB残余原生FULL需求称为全部可替代预取或oracle收益。

完整600–900秒窗口保存在v37_v22_busy_window_600_900_20261011.json，
包含读取前缀SHA256与边界行SHA256。V22 predictive有6440个
decode batch、平均可见上下文23068.33 token，worker decode区间
均值39.187ms；旧V21 predictive/native为5642/7363 batch、
21840.58/23703.38 token和44.189/35.510ms。此处版本和轨迹不同，
只作诊断。V22已埋点Python独占墙钟累计46.428秒，旧predictive
为60.398秒；最大阶段为准入规划6.338秒、reentry5.618秒、
JOIN PREPARE4.724秒、control drain4.149秒、handoff enqueue
3.510秒、opportunity采样3.239秒和execution handoff2.915秒。
不能把累计墙钟直接从JCT扣除。

后台writer的窗口差分首次提供具体CPU定位：Host驱逐重访
request_probe的30个实测样本，平均thread CPU7.271ms、
墙钟8.944ms，是原生writer样本中最大的处理类别；该窗口所有
原生样本累计CPU421.851ms、墙钟632.605ms，opportunity样本
累计CPU73.084ms、墙钟145.430ms。样本不乘64，不推断GIL争用。
针对该类别的索引优化已在隔离工作区完成验证，当前V22保持冻结。

Host驱逐归因索引新增按namespace和不超过终端长度的最大二次幂
前缀路由。完整终端哈希仍负责最终身份匹配；超过32个候选长度时，
单个递增hasher只在匹配路由上复制终端摘要，小集合保留直接路径。
FULL/Mamba共用的终端索引引用分开维护，重访或容量淘汰后及时清除
辅助路由。只有当前请求精确匹配全部待核验前缀时批量释放索引，
部分匹配和其它namespace仍逐条保留原有归因与顺序。
早期两遍哈希方案及单遍逐条清理方案有小集合/全匹配退化，
证据分别保存在v38_eviction_route_writer_cpu_20261011.json和
v39_eviction_route_single_pass_cpu_20261011.json，均未部署。
最终v41_eviction_route_batched_cleanup_cpu_20261011.json包含36组
各60轮交替CPU测量；记录、哈希、计数、历史与待匹配状态一致。
512条前缀分布在32条分支时，1024/8192/32768-token重访均值为
1.046/1.040/1.918→0.508/0.625/1.463ms；全部匹配场景为
7.087/7.333/7.954→6.655/6.847/7.405ms。16条匹配前缀的小集合
没有再出现先前退化；单条驱逐登记因辅助路由增加约2–4微秒。
156项telemetry/lifecycle/writer/window检查通过，CPU夹具不包含
真实scheduler并发、DMA或GPU，不能由此声称吞吐提升。
上述writer代码与证据已提交为fe06e06。V23最初于2026-10-11
08:27:31 CST登记该版本，原登记保存在
v23_launch_after_v22_fe06e06_superseded_20261011.json。
08:46:35 CST登记的73c9ac1已保存在
v23_launch_after_v22_73c9ac1_superseded_20261011.json。
2026-10-11 09:06:17 CST将尚未启动的V23替换为同时包含writer
索引、准入身份绑定和原生恢复单次spec构造的3644e8b，
当时状态waiting_for_previous_pair，协调器PID2402814；
冻结checkout为beliefkv-v46-single-load-spec-20261011，
完整目标revision为3644e8b7f752005d861b5fb29c8648fb1467b898，
新原生patch SHA256为
498a2e8219f0d454776c7b967b87a82e7b49e3e75831389e08a9daabfb29998e。
实际登记位于主工作区experiments/reports/
v23_launch_after_v22_20261011.json；替换回执为
v23_supersession_73c9ac1_to_3644e8b_20261011.json，
此前v23_supersession_fe06e06_to_73c9ac1_20261011.json及所有旧登记、
日志和冻结checkout保留。仅停止旧waiting coordinator2167234；
V22 coordinator/driver/server/scheduler/client均未发送信号。
新patch相对当前部署的差分已通过原生工作区git apply --check，
回执为v23_single_spec_validated_engine_delta_20261011.patch。
该09:06登记已由上方09:23的e7bd8fa登记替换。当时协调器2616411
已捕获V22 coordinator
1159560及driver1166290的进程身份，等待整个pair及后处理完成再
部署；仍为predictive先行、冷native随后，不改变workload或预测
窗口。当前V22源码和运行进程保持冻结，V23尚未启动；性能目标仍
待真正提前复用FULL和同版本繁忙窗口收益共同证明。

新增完整1200–1800秒控制窗口，保存在
v42_v22_control_1200_1800_20261011.json，固定读取前缀和边界行
SHA256。599.617秒快照间隔内，已埋点Python独占墙钟累计82.818秒；
reentry检查为78428次/19.956秒，handoff enqueue为1693次/8.138秒，
准入规划为6793次/6.982秒，control drain和JOIN PREPARE分别为
6.613/6.359秒。同版本native尚未运行，累计阶段耗时不能直接从
JCT扣除，也不能单独证明GPU服务差距的原因。

根据该窗口继续缩减准入身份绑定开销。_request_key直接读取
workflow、invocation和context字段，避免生成器及中间tuple；
原有类型、session、epoch、attempt和generation校验保持一致，
没有增加模型或调度门槛。新增12种无效必要身份输入检查，
相关290项准入、handoff、终态容量、runtime和原生前缀检查通过。
比较脚本分别加载两个revision的compiler和直接request-key函数，
避免baseline误用优化后的绑定；其它继承helper仍共享，明确限制
测量范围。逻辑时钟改用普通lambda，避免MagicMock保留调用历史
导致GC和计时噪声；旧v44结果保留但不用于最终性能判断。
v45_request_binding_plan_light_clock_cpu_20261011.json包含17组
各200轮交替CPU夹具。两次规划的墙钟均值降低6.6%–14.7%，
顺序、计数、提升元数据和原生驻留检查次数均一致；48-request/
4-lease场景为1.216→1.101ms，156-request/16-lease场景为
2.510→2.216ms。该夹具没有真实allocator、DMA或GPU，
不代表端到端吞吐收益。改动与证据已提交为73c9ac1，并纳入上述
V23冻结登记，等待真正GPU运行及同版本native对照。

同时验证并撤回仅在RadixKey中复用原始长度的候选优化。
v43_reentry_length_reuse_cpu_20261011.json记录15组各15轮、
每轮100次完整reentry检查。256层单leaf前缀约435→437微秒；
1024层单leaf约1703→1666微秒，8-leaf约1800→1793微秒，
没有稳定的整体改善。扩充deep-prefix夹具保留，候选原生代码
已撤回，canonical patch SHA256仍为03033a9e9d28fe217f9085752fd4db4618cda0430312238dd0e130579557aa83。
继续按真实控制窗口、已结束child释放、实际FULL提前复用和
恢复后首次消费检查收益；不由孤立小函数计时扩大原生修改。

隔离工作区继续消除每个预取extent的重复原生传输构造。原先
prefetch_gpu_session_node先构造并核验一次FULL/Mamba spec，
load_back持锁后再构造一次；FULL hook会重复拼接Host索引。
现在将原有单节点、仅必要Mamba的范围检查移到持锁后的唯一
构造位置，保留session/完整祖先核验、容量、无额外设备驱逐、
原生逐层依赖及每节点ACK。已知spec构造失败会释放所取得的
设备与Host锁；普通native需求恢复仍可按原语义恢复多节点。
104项原生shadow/handoff/latest-state检查及25个子用例通过。

scripts/benchmark_native_prefetch_load_cpu.py比较完整原生
prefetch burst、load_back以及真实FULL/Mamba/tree spec函数，
夹具中的allocator、锁计数、enqueue、commit为确定性CPU替身，
不包含runtime物理账本、DMA或GPU。v46_native_prefetch_single_spec_cpu_20261011.json
包含24组各15轮、每轮30次交替测量；有效提交的传输索引、
回调、锁计数和结果完全一致，spec构造次数由每extent两次减为
一次。thread CPU中位耗时降低9.9%–25.7%；64层/16-extent/
4096-token且含最终Mamba的burst为678.15→545.86微秒，
256层同配置为1081.81→951.68微秒。该收益不能直接外推到
实测handoff enqueue的全部8.138秒或端到端吞吐。
新canonical patch已在隔离原生工作区验证expected/actual tree
均为286ef96cc4d5954b9e20a5e3cc96f7306f426acd；代码、夹具和证据
已提交为3644e8b，并纳入上述V23冻结登记。当前V22继续使用
既有patch，真实GPU效果及同版本native收益尚待验证。

闭合V21的predictive/native输出吞吐为738.64/831.87 token/s，
GPU平均利用率为72.04%/80.15%。600–2400秒窗口的decode前平均
可见上下文为23679.53/23850.39 token，worker decode区间均值为
41.49/36.19ms；相近上下文仍存在服务差距。保留
v35_v21_busy_context_control_600_2400_20261011.json及原始来源哈希。
worker区间并非kernel时间，整侧轨迹与完成数也不同，不能把差距
全部归给单一函数；V22继续验证控制减负、完整恢复和实际提前复用。

隔离工作区修正工具轮次审计中的晚到观测关联。原先following请求
必须晚于first_observation；若控制观测在真正下一请求提交后到达，
就会跳过该请求并合并下一轮工具，误差混入一次模型生成时间。
已核对V21 xarray-4687的child epoch20：实际只调用一次7.59ms的
read_file，旧审计却计为两次工具，并选取epoch22而非epoch21。
现在以origin epoch绑定直接following请求，按两请求之间的真实
工具START/END证明整轮完成，额外采样工具身份单独报告；
完成后观测与完成后传输不能变成提前预测证据。66项生命周期、
工具forecast与传输窗口检查通过。闭合V21证据已重算：252个工具
事件的完成边界/下一请求关联修正，248个旧multi轮次实际是single。
668个事件的首次观测已晚于工具完成，16个有轮次外工具身份。
回执为v33_v21_wait_round_attribution_20261011.json，原审计保留。
真正提前且复用的FULL仍为JOIN1.419GB、tool2.530GB，不能把
归因修订当成模型精度或吞吐提升；修正后的工具残余原生FULL
为585.285GB，是观测需求而非可实现的预取或oracle收益。
本项是离线归因修订，V21采集时保留原策略版本。
修正后的审计脚本已随3aa27dd进入当前V22冻结后处理，使新一轮
使用正确轮次边界；这不改变模型、传输或准入策略。

隔离工作区补齐后台证据写入线程的独立计时。Native与opportunity
writer按记录类别每64条采样一次，同时记录thread_time_ns CPU
时间和perf_counter_ns墙钟时间；native按stream及event/internal
event分别采样，避免将大批次序列化与小条目混在一个类别。
记录处理以外的队列等待和状态发布不计入样本。Native每秒在
原audit流追加累计快照，opportunity随原admission_runtime_state
附带消费端快照，最终status也保存计数；实际事件、前缀归因、
ACK刷新、处理顺序与调度策略保持原语义。新数据用于区分
JSON处理、前缀核验和其他写入工作，不能仅凭CPU/墙钟差异
断言GIL争用或将样本乘64视为已测全量成本。
243项writer/telemetry/runtime/lifecycle检查及19项计时/对照摘要
检查通过，两组有重叠。可复现脚本
scripts/benchmark_writer_timing_cpu.py的16组1000轮交替测量，
逐条JSON证据相同；1170/8543/20002字节夹具的平均新增成本
分别为0.498/0.595/0.844微秒。保留
v32_writer_timing_cpu_20261011.json；该CPU夹具不包含实际
队列、文件I/O、前缀处理或并行scheduler，不是GPU收益证据。
本项计时已随3aa27dd部署到V22并开始采集；V21没有该计时。

隔离工作区补齐scripts/analyze_busy_context_control.py的writer窗口
分析：原生writer快照随decode证据读取，opportunity writer随控制
快照读取，均按各自snapshot_ts_ms取累计差值，不能沿用可能在
队列中延迟的producer时间。报告实际样本数、thread CPU和墙钟、
边界行SHA256及快照滞后；不乘64估算全量成本，不把累计最大值
当作窗口最大值，未采集或窗口不完整明确标为不可用。12项窗口/
writer检查通过，V21真实600–900秒重算的decode、控制计时和
cohort结果与原证据完全相同，三份writer证据均为未采集。保存
v34_busy_writer_analysis_validation_20261011.json。此项是离线分析，
已随3aa27dd进入V22登记；V22采到完整窗口后运行，
用于定位残余服务成本，不是CPU归因或GPU性能改善证据。

结束的V21 predictive工具触发窗口诊断已按修正后的轮次重算，
脚本为scripts/audit_tool_forecast_windows.py，当前紧凑证据为
v33_v21_tool_forecast_epoch_bound_20261011.json；v31旧口径及
原始审计保留。完整逐轮结果保存在原实验目录，摘要固定原始
证据与详细结果SHA256。
审计按实际接收时刻重建NativeToolWaitHint的存活条件化分布，
以已证明的整个工具请求轮次结束作为标签，剔除过期、完成后及
无存活时长支持的预测。11818个单工具轮次中，11104个有最后
一秒内预测；各轮先汇总该区间的绝对误差，再跨轮统计，P50/P90
为52.42/424.42ms，带符号误差P50为+6.42ms。这是固定轨迹、
真实结束附近的条件观测，不能当作全程或未见项目准确率。
10132个进入预测500ms窗口的单工具轮次中，8405个首次触发
确在实际结束前500ms内，1727个更早。多工具轮次单列：
147个真实multi轮次中94个有首次触发，其中62个在实际结束前
500ms内、32个更早，实际提前量P50/P90为236.47/6299.04ms。
旧的4741.60ms中位数混入跨轮模型生成时间，不能据此统一加
几秒偏移；剩余长尾仍需按真实工具轮次检查。

正FULL计划中，381个独立轮次在记录的实际与预测触发窗口均
就绪，逐轮最大计划字节合计33.011GB；其中记录的空闲池容量
足够的仅61个轮次、1.692GB。150个轮次在实际窗口内但预测
尚未到窗口，容量足够子集为25个、1.404GB。这些是被runtime
选中采样的计划，分类可跨快照重叠，字节可共享页面，既不是
可实现的独占容量，也不证明实际传输、驻留、准入或性能收益。
继续用实际child容量释放、完整前缀恢复与首次消费验证覆盖，
将多工具轮次时机误差另行追踪。本项仅增加离线诊断，没有修改
V21或当前V22的运行策略；127项相关审计/tool检查通过。

继续降低后台前缀证据写入的Python工作量。原生整数token列表
直接转换为array，已有array在只读重访探测中复用；已服务前缀
仍保存独立快照，兼容原有整数规范化。公共前缀用数组比较，
存在分歧时按范围检查并精确定位首个不同token。Host驱逐
身份哈希、重算代理、会话隔离、记录顺序和归因计数不变。
167项telemetry/lifecycle/终态容量检查通过。可复现脚本
scripts/benchmark_native_prefix_writer_cpu.py隔离各版本的写入方法；
24组各100轮交替CPU测量的记录、哈希、计数、历史和待匹配
状态一致。32768-token列表的保存及追加探测均值为
8.719→1.966ms，后段分歧为6.804→1.613ms；
16条驱逐前缀的列表重访为3.329→1.397ms。已有array的
重访为0.640→0.669ms，没有改善证据。
保存v30_native_prefix_writer_cpu_20261011.json。结束的V21
predictive有44790条已服务前缀探测，合计951763303个公共
token位置，P50为21261 token；原始证据SHA256保留。
这些计数不等于额外KV复用或节省的JCT，CPU测量没有并行
调度线程，不能据此断言GIL争用就是GPU性能差距的根因。
本项修改已随3aa27dd部署到当前V22，V21使用旧版本。

隔离工作区继续减少准入排序的无效观察：已经进入10秒等待老化
次序的请求，驻留信息不会影响其到达顺序，排序扫描不再读取它
的reentry闭包。被选中执行handoff或parent恢复时仍读取真实
checkpoint；年轻请求的resident-first与恢复消费优先级不变。
270项handoff/runtime/终态容量/tool检查通过。17组100轮交替
CPU基准的计划、策略计数和提升身份一致；所有少读的对象都是
已老化请求。48请求全老化夹具的原生读取从2344次降到0，
两次规划均值1.110→0.689ms；156请求为2.504→1.965ms。
读取计数包含10轮预热，原生inspector是确定性stub；未老化
场景的时间差异不能当作收益。这项修订已部署到当前V22，未进入
旧V21。证据为v28_aged_residency_inspection_cpu_20261011.json。

继续降低预取首次服务证据的同步成本。在同一个请求的首次服务
记录中复用已核验的公共输入前缀，减少Python逐token重复比较；
后续快照以原生array确认共享部分相同后才跳过它，否则重新核验。
短前缀、输入分歧和cache namespace继续独立记录，实际FULL
复用仍要求原生节点、创建身份、设备value和物化请求前缀一致。
移除首次服务祖先遍历的256节点截断，保留环路检测；合法300
节点路径上的较早祖先可以被正确记录为复用。这项修订只减少
证据采集工作并修复漏记，不改变预取或准入决策。

151项telemetry/lifecycle/终态容量检查通过。12组100轮交替CPU
测量逐条输出及消费的command一致。16-extent、32768-token
完整/分歧输入夹具的记录均值分别为11.619→2.127ms和
7.611→1.307ms；单extent完整输入为1.283→1.287ms，没有
速度提升证据。保存v29_prefetch_use_shared_prefix_cpu_20261011.json。
其中V21已结束predictive的原始来源SHA256保留：16821条首次
服务command记录关联4285个请求，3001个请求有多个目标前缀；
逐token核验的已匹配前缀合计262613488 token，其中199168503
位置在同一请求中被重复比较。这些不是额外复用字节或可从JCT
扣除的时间。修订已随3aa27dd部署到当前V22，
V21及其旧证据保留原版本。

补充可复现的繁忙窗口几何/控制审计，脚本为
scripts/analyze_busy_context_control.py，证据为
v21_busy_context_control_600_900_20261011.json。两侧600–900秒
decode前可见平均上下文为21840.58/23703.38 token，predictive
没有因平均上下文更长而变慢。在同一22528-token分箱中，
batch48的worker区间均值为23.015/21.983ms，batch47为
87.449/74.284ms；20480分箱的batch47为101.013/68.980ms。
这些是不同轨迹的诊断分组，不能作为相同需求的因果比较。
predictive的300.253秒控制快照差分包含60.398秒已埋点的Python
独占墙钟，其中reentry11.090秒、JOIN PREPARE7.681秒、
control drain6.210秒、准入规划6.064秒。优先检查槽位空出时的
准入与恢复开销，但不能把这些累计值直接从JCT扣除，也不能把
全部reentry时间当作本次可消除成本。脚本保留完整观测窗口、
原始读取前缀SHA256和控制边界行SHA256；计数按窗口内完成
的batch统计，5642/7363与此前包含相交区间的5643/7364口径不同。

## 终态 child 容量交接与完整前缀恢复

四步联动已在 V21 中部署：child 收尾时规划 parent 的安全
checkpoint；真实 RETURN 后回收无引用、无共享依赖且传输已结算
的私有后缀；使用原生 allocator 的实际空闲 FULL/Mamba 容量
恢复 parent；JOIN 满足、请求提交且依赖就绪后有界优先准入。
早返回 sibling 的容量可支持 RETURN 前预取，最后 child 的释放
只支持返回后的需求恢复或 parent 后续增长。容量没有独占预留，
也不要求复用 child 原来的页号，GPU running slot 仍由原生管理。

隔离工作区进一步修复容量释放后的恢复重试缺口。原先 parent
handoff 因 no_cold_capacity 关闭后会一直保留在已尝试集合中；
随后终态 child 的 DMA/锁结算并实际释放设备容量，也不能重新
选取该 parent。现在只有关联 child 确实释放设备容量、JOIN 满足
且 parent 当前请求/session/epoch 仍有效时，才重新允许此前因
容量不足失败的恢复。没有新容量、只有 Host 释放、旧请求或其他
失败不重复尝试。该事件记为 parent_capacity_handoff_restore_rearmed，
仍是需求恢复，不能记入 RETURN 前预测 H2D。

恢复驻留记录新增 protection_reason 和 protected_by_command_id，
区分已有锁覆盖、下一批名额已占用、无名额、字节预算不足与原生
闭包不可观测。审计按这些原因分别报告 FULL 传输及实际复用，
并按相同 workflow/context/JOIN/request 关联重试后的 parent
服务，不能把 native_locked=false 全部视作失去保护或浪费。
431 项终态回收、handoff、运行时、物理传输、tool 与审计检查
通过；V21保留旧版本，这项修订已进入当前运行的V22。

隔离工作区取消需求handoff的累计16-extent截断，保留每批最多16
个extent。V21有81个请求在16个ACK后关闭，全部在关联首次服务
之前；达到上限的P50仅39.85ms、最大291.90ms，尚未到原有2秒
有效期。68个请求首次服务仍记录FULL Host命中，合计1080888
token；对应22.136GB只是原生请求缓存记账代理，可能包含合并
或已经恢复的前缀，不能当作独占残余物理H2D或可节省字节。
保存v35_v21_handoff_extent_limit_20261011.json及逐请求证据。
新版本可在有效期内重新观察剩余安全checkpoint，按当前容量和
pending额度继续分批恢复；有效期不续长，原生依赖、页身份、
ACK校验与失效回退不变，非overlap路径也不再把第16个extent
当作恢复结束。205项handoff/终态容量/runtime检查通过。
此项已部署到V22的3aa27dd，正在采集GPU证据；需求恢复不计入RETURN
前预取，不能由这些检查或Host命中记账宣称吞吐/JCT改善。

离线容量审计新增sibling_release_windows，以独立workflow/
parent context/JOIN关联真实设备容量释放和完整JOIN边界。
只有释放发生在实际H2D submit之前、submit发生在完整JOIN之前、
且既有审计已证明下一请求复用FULL，才计入“sibling释放之后的
提前复用FULL”。仅Host释放、最后child返回后的释放、内部summary
调用、不同JOIN和没有复用证明的命令分别排除；未知JOIN终态不填0。
记录每轮释放提前量及分池实际回收量；这只是时序与复用关联，
不能证明页号交接、独占容量或native相对收益。本项仅修改隔离
工作区的离线审计，不修改当前V22运行策略或冻结后处理脚本。
66项生命周期检查通过。重算闭合V21：187个观测JOIN中159个
在完整JOIN前已有sibling实际设备释放，其中86个JOIN随后发生
提前提交并由下一请求复用的FULL，合计1.207GB。这些JOIN前
设备释放累计为FULL41.998GB、Mamba32.452GB，不是同时空闲
容量；首次sibling释放到JOIN的P50约649秒，不能从此假设容量
会一直留给parent。旧版所有既有汇总指标保持一致。保存
v36_v21_sibling_capacity_windows_20261011.json及
v36_capacity_handoff_deployment_and_audit_20261011.json。
V22在08:02 CST前已至少有5个真实需求恢复超过16个extent，最大23；
只能证明原截断已解除，不能计入提前预取或吞吐收益。

隔离工作区补齐物理状态变化后的短期观测失效。关联child确实释放
设备容量时，立即清除该parent的语义目标/工具恢复观测；带命令
归因且通过原生ledger验证的PREPARE完成后，清除当前相同或下一
epoch的等待对象观测，并允许相关tool重新扫描。避免Host副本
就绪或设备容量释放后仍使用100ms旧否定结果；不保证每次能节省
100ms。未归因原生ACK、无效回执、旧session、仅Host释放或未
释放容量不触发刷新，其他对象继续使用原有缓存。预取仍经过
实时页、容量、时间与驻留检查。这项变更只有事件发生时的局部
字典操作，没有新增逐decode扫描；261项终态/工具/runtime/
handoff检查通过，现已进入更新后的V22冻结登记87370d3。

工具预取进一步复用同一次dispatch中刚读取的恢复计划，避免
选取候选后立即重复完整观察。缓存命中、前次选定的ticket仍重新
读取；排空因果事件后仍检查当前checkpoint、页集合、时间、容量，
原生逐节点预留和ledger记账不变。267项runtime/tool/终态/handoff
检查通过，覆盖排空期间工具返回与checkpoint变化时取消提交。
100轮交替CPU测量中，4节点/300节点完整dispatch均值分别由
0.858/9.117ms降至0.683/6.672ms；计划与记账一致，300节点新版
仍有19.507ms最大值，不能声称消除了长尾。保留
v27_tool_dispatch_cpu_20261011.json。这项修订已随3aa27dd部署到
当前V22，不属于旧V21。

同版本V21的600–900秒窗口两侧都已完整观测，predictive/native
decode吞吐为891.68/1165.71 token/s，GPU利用率69.26%/87.60%，
时间加权decode batch约47.18/47.36。predictive吞吐低23.51%，
worker decode区间均值44.19ms，native为35.50ms；该区间不是
CUDA kernel时间。这说明当前容量交接证据不能代替服务性能验收，
也不能仅用batch大小解释差距。原始比较及SHA256保存在
v21_busy_window_same_version_evidence_20261011.json。native整侧
现已完成，这个局部窗口仍不代表因果速度提升。
当前V22验证实时池计数、深前缀完整观察和控制减负，
同时继续检查实际提前FULL复用与parent服务，性能目标保持进行中。

后续检查确认完整前缀规划存在不合理的固定祖先数量限制：
observer、PREPARE/H2D 规划和原生 session 恢复分别以64或256
个节点拒绝合法路径。V21 局部采样中，1643个不同的tool等待
context/epoch/JOIN身份出现该拒绝；包括准入采样在内，4792个
不同request的prompt长度为10938–48936 token，远未达到
131072上下文上限。18574条相关记录中9846条仅来自终态诊断，
其余也包含重复采样；这些数量不是可实现预取机会或收益。
证据保存在 v21_prefix_bound_rejections_live_20261011.json。

新修订对物理动作检查完整祖先路径，保留环路、节点创建身份、
session、原生锁与在途DMA检查；同一次原生reentry调用继续
复用共享前缀状态。单次传输仍至多16个extent，字节/容量/时间
预算不变。终态私有页回收的max_nodes仍是可重试操作额度，
只读终态诊断的64节点采样预算也保留，两者不限制合法恢复。
超过64个session共享的合法叶节点不再因数量被拒绝，仍不允许
错误类型、空身份或空session集合。

494项BeliefKV检查和186项原生CPU检查（另18项subtest）通过，
包括65/300节点路径、安全输入checkpoint裁剪、仅恢复必要
Mamba、16步传输额度、真实allocator回收与共享页保留。
规范patch正向建树/反向应用核验通过，SHA256为
`03033a9e9d28fe217f9085752fd4db4618cda0430312238dd0e130579557aa83`，
预期和实际引擎tree同为`63d91509bee139b2d6a2285eb44b193a67a9e008`。
原生CPU基准短路径开销变化约-0.7至+4.9微秒；新增支持的
65/128/300节点完整观察约111–615微秒，不能把旧版快速拒绝
与完整观察的耗时比较当成性能改进。保留v22_complete_prefix_
reentry_cpu_20261011.json和v22_deep_prefix_reentry_cpu_20261011.json。

该修订与容量结算后的parent恢复重试已随3aa27dd部署到V22，
先predictive后同版本冷native。V21保留原源码/patch；当前重点检查深前缀的实际
提前FULL复用、parent服务等待、驻留原因和繁忙窗口吞吐。
四步容量交接已实现，但native相对吞吐目标仍未达成。

继续降低深前缀观察的控制成本。一次节点闭包读取只对新遇到的
原生node/component类做类型检查；锁和session引用计数在当前
节点内一起校验，减少重复函数调用。节点身份、创建时间、长度、
引用、锁和在途DMA仍逐节点读取；没有跨读取复用驻留或容量。
新增检查确认中途切换到未知节点/组件类型仍会拒绝，相同原生
类名的合法替代类型仍可观察。240项observer、physical和终态
容量交接检查通过。

以629eaa4为对照的100轮交替CPU测量中，300节点完整前缀的
闭包、H2D规划和FULL-only PREPARE规划均值分别从
1.536/1.995/1.920ms降到1.453/1.925/1.810ms；完整观测与
16-extent计划一致。4节点闭包仅从33.57微秒降至32.91微秒，
H2D/PREPARE均值没有稳定改善，不能宣称所有路径或GPU吞吐
提高。保留v25_checkpoint4_count_reuse_cpu_20261011.json和
v25_checkpoint300_count_reuse_cpu_20261011.json；最初仅复用
类型检查的64节点测量改善很小，不作为主要收益证据。

上述运行时修订与完整前缀覆盖、容量结算重试、实时几何计数和
稀疏控制计时先登记为V22版本658f639。2026-10-11 04:47 CST
将仍等待中的目标更新为已验证ed974d2，另纳入同次规划排序复用、
物理进展后的观测刷新与burst归因。当时PID3439480，状态为
waiting_for_previous_pair；旧658f639及629eaa4登记完整保留。
回执为v22_supersession_658f639_to_ed974d2_20261011.json。
已逐项确认实验环境相同，排队版本间的原生patch SHA256不变。
V21继续冻结，完成同版本pair后自动部署；先predictive、再冷启动
native。登记后的目标checkout不再编辑，文档及回执在隔离工作区
提交。下一轮仍以真实提前FULL复用、恢复消费和繁忙窗口吞吐/JCT
验收，不能把等待中的登记当作已完成GPU验证。

完成后的predictive审计更新到v25_v21_capacity_handoff_progress_
20261011.json，保留最终原始审计路径及SHA256。V21 predictive
155个completed、1个incomplete，约11375.87秒；同版本冷native
现已完成，闭合pair结果见本文开头。354个真实动态child累计释放设备
FULL115.495GB/Mamba120.858GB；188个parent首次服务中185个
可关联到先前child设备释放。最后child的scheduler RETURN观测到
parent首个观测完成GPU batch的P50/P90为209.57/493.56ms，
原生入队到该batch为168.29/500.65ms，最大仍约18.906秒。
独立完成边界前启动并复用的FULL为JOIN1.419GB（91事件）、
tool2.530GB（84事件）；99.782GB需求handoff复用另列。
只有23条JOIN节点动作先于server EOS提交，不能把这些边界前
动作全部解释为模型尚未结束时的预测。累积释放不等于同时空闲
或独占交接，完成batch也不是kernel启动时间；predictive单侧证据仍
不能证明native相对吞吐提高或等待中的V22修复已在GPU上有效。

隔离工作区进一步复用同一次H2D/PREPARE规划的root-first顺序，
安全checkpoint选择与传输步骤不再重复排序相同祖先集合。节点
身份、完整祖先、输入裁剪、必要Mamba和物理预算检查保持原有
语义。207项physical、execution handoff及终态容量检查通过。
以658f639为对照的100轮交替CPU测量中，300节点H2D/PREPARE
规划均值为1.760→1.732ms和1.664→1.647ms，改善约1.6%/1.1%；
4节点为87.01→85.98微秒和66.28→65.13微秒。观察与传输计划
一致，无代码改动的其他阶段差异属于噪声。保存
v26_checkpoint300_root_order_cpu_20261011.json及
v26_checkpoint4_root_order_cpu_20261011.json。这项小幅CPU减负
不证明吞吐收益，不属于冻结V21，现已进入更新后的87370d3 V22。

新的独立等待事件审计进一步明确覆盖缺口，证据保存在
v25_v21_prefetch_gap_attribution_20261011.json。工具等待中
1105个事件曾观察到正FULL恢复计划，关联后续原生FULL Host
命中256.066GB；9593个事件只观察到零计划，关联327.839GB；
12个事件没有计划观测，不能把缺少计划当作没有恢复需求。
4433个独立工具等待事件曾出现node ancestry exceeds bound，
关联原生FULL Host命中298.122GB，且没有完成边界前的FULL
复用。固定深度限制的修复已包含在等待部署的V22中。
另464个事件出现no_cold_capacity，关联122.589GB残余原生
FULL。这些原因会在同一事件中交叠，不能相加或当作独占根因；
相关需求字节不是全部可实现的提前预取量或oracle收益。

审计现在同时保留规划原因、blocked_detail和no_live_detail，
按独立JOIN/工具等待事件关联原生残余FULL字节和提前复用。
重复采样同一原因只计一次事件；正计划、仅零计划和未观测计划
三类互斥，未知原生字节继续单列。53项生命周期审计检查通过。
这项归因修订本身只改变离线证据，不改变调度策略。

进一步按同一等待中明确观测到的最大planned_nodes数量分组，
保留空数组与未观测的区别。55项生命周期审计检查通过；证据为
v26_v21_burst_size_evidence_20261011.json，含原始审计路径与SHA256。
186个JOIN中165个最大计划为1–15节点、21个仅空计划，没有16节点
计划；工具等待中190个达到16节点，关联63.680GB后续原生FULL，
仅3个有提前FULL复用。4746个工具等待出现深前缀拒绝，关联
298.122GB残余FULL，仍无提前复用；其修复已排入V22。
JOIN/tool的16节点限制是单批额度，ACK后可继续下一批；需求
handoff的整张ticket额度另列。当前证据不支持先扩张JOIN burst。
分组互斥，拒绝原因仍可交叠；残余字节不是可实现的预取量或收益。

较晚局部采样中，完成边界前启动并由下一请求复用的FULL为
JOIN 1.405GB、tool 2.530GB；约99.764GB handoff复用仍是需求
恢复。节点动作ACK到首次服务的JOIN/tool P90分别约1.473/
0.922秒，不能继续用早期16秒P90描述当前主体分布；最大值仍为
24.730/12.354秒。已能对齐完成边界的FULL租约失效证据中，
JOIN有10个发生在完成边界前、1个在等待原生请求到达时，tool
有1个在完成边界前。它们说明部分长尾与过早触发有关，不能将
所有长尾都归为parent准入。338个真实child的回收与175个parent
服务中172个先前设备释放关联保留；累积释放不是独占预留。
同版本native对照尚未完成，整体性能目标仍在验证。

进一步降低容量交接的共享控制成本：空闲页/槽和Host用量观察
现在只读取经过当前池绑定、类型和几何校验的实时free lists，
不再顺带遍历整套FULL/Mamba张量计算字节数。后续修订也已将
动作前节点闭包改为读取同一套经过校验的池几何，而不再做整池
张量字节census；仍检查实际节点的FULL长度、祖先、锁、共享
引用及在途传输。公开完整设备容量census继续读取实际张量。
没有跨迭代缓存空闲容量或取消session、引用、DMA与allocator
检查。这些修订在隔离worktree中完成，计划一起进入下一轮V22。

原生MHATokenToKVPool计数代码配合10层FULL、30层Mamba的
小型CPU张量基准，空闲容量/Host用量观察均值从101.5/100.8
微秒降为6.50/5.58微秒，实时变化的free list结果一致。这是单个计数
路径的CPU证据，不是恢复全过程或GPU吞吐提升。完整PREPARE、
采样和终态观察基准的动作、候选发布与观测结果一致；小型
张量夹具下仍有墙钟长尾，不把其平均时间作为性能结论。
保存v23_static_pool_counters_cpu_final_20261011.json与完整
路径基准。最近296项observer、telemetry、runtime和policy
检查通过；此前267项终态/handoff/physical检查也通过，两组
存在重叠。

最新闭包修订以f57527f为对照，在相同四extent安全checkpoint上
交替执行100轮CPU基准，每轮每阶段50次。节点闭包、缺失FULL
恢复和FULL-only PREPARE规划均值分别从150.07/223.84/193.93
微秒降至40.31/103.96/76.42微秒，结果完全一致；这些路径的
整池张量census调用从15750次降至0。空闲列表观察的约0.7%
差异属于噪声。证据为v24_checkpoint_observation_cpu_final_
20261011.json；这是规划CPU成本变化，不是GPU传输或吞吐收益。

事件通道新增每64次接收尝试/已接收消息一次的分阶段计时，
分别观察socket接收、JSON解析、RuntimeEvent构造、ACK编码
及发送。沿用现有计时器和聚合发布，不逐事件写日志；重复消息
仍使用相同ACK缓存，事件顺序、拒绝行为和ACK字节不变。
该采样比例发布为control_timing_sample_every，样本累计时间
不能直接冒充全量时间。337项observer/physical/终态/handoff/
原生传输检查及132项事件通道/计时/runtime检查通过，两组有重叠。
CPU socket夹具中862字节单事件包的采样平均增量约4.03微秒；
25746字节32事件包的-0.65微秒差异属于噪声。该夹具不包含真实
socket系统调用或GPU。保存v24_control_channel_profile_cpu_
20261011.json，用下一轮真实分阶段结果定位控制成本。

冻结V21的历史局部计时快照仅读取日志末尾128KiB：运行约
7287秒时，control_drain/reentry_inspection/opportunity_sampling/
join_prepare/admission_plan的Python独占墙钟累计分别为
129.19/106.15/102.44/84.37/65.85秒。这些不是可直接消除的JCT
或CUDA耗时，证据为v21_control_profile_snapshot_20261011.json。
当时136个workflow completed、1个incomplete、19个尚无终态。
django-12125的incomplete发生在未耗尽deadline、没有取消或guard
干预的情况下；最后两轮输出均为length终止，保留未完成分类。
这些局部证据没有改变当前pair或下一轮的冻结实验参数。

最终predictive计时保存为v21_control_profile_final_20261011.json，
仍只读取末尾128KiB并保留最终state的规范JSON哈希。埋点覆盖的
Python独占墙钟累计1117.376秒；control_drain/opportunity_sampling/
reentry_inspection/join_prepare/semantic_updates分别为168.853/
124.516/107.695/101.487/86.923秒。scheduler maintenance及多项
轻量路径各调用约251.75万次，不能把调用多直接等同于重复恢复。
独占计时排除嵌套重复，仍不等于CPU利用率、GPU空转或可删去JCT。
queued V22的池几何减负、完整前缀覆盖、分阶段事件计时及观测刷新
未计入V21结果。下一轮要同时检验真实提前FULL复用、消费与同版本
繁忙窗口吞吐，不能从累计CPU时间推导收益。

较晚的V21局部审计保存于v21_terminal_capacity_progress_
summary_20261011.json，保留原审计路径与SHA256。214个真实
动态child累计释放设备FULL 57.445GB、Mamba 32.388GB；
内部总结调用的释放量独立统计。102个parent首次服务中99个
能关联先前设备释放，入队到首个观测完成batch的P50/P90为
196.80/492.49ms，最大18.906秒。容量释放未独占预留，也不能
把累计释放量当作同时可用空间。56个独立JOIN、74个tool事件
确认完成边界前启动并被下一请求复用，FULL分别749.179MB、
2.228GB；80.025GB需求handoff FULL复用仍单列。

V21与旧V20 native的600–2400秒诊断窗口中，decode输出吞吐
为942.09/1059.67 token/s、GPU利用率72.84%/82.44%，没有
改善证据。这是跨版本诊断，不替代V21同版本native对照。
保持当前pair冻结，以新版本同配置实验确认完整前缀覆盖、
恢复驻留原因、容量结算重试和计数减负是否改善真实服务。

V21 局部审计保存在 v21_terminal_capacity_live_20261011.json：
126 个真实 child 累计释放设备 FULL 26.165 GB、Mamba 8.821 GB；
44 个 parent 的入队到首个观测完成 GPU batch P50/P90 为
240.07/750.15 ms，最大 18.906 秒。21 个 JOIN 与 29 个 tool
事件确认完成边界前启动并被下一请求复用，FULL 分别为
221.245/830.730 MB；48.571 GB handoff FULL 复用单列需求恢复。
这些是采集中局部数据，累计释放量不是同时空闲容量或吞吐收益。

django-13023 的一个 parent 在 handoff 选取后仍等待约17.73秒。
两个已恢复节点的 ACK 后182/96ms便记录失去驻留，约17.5秒后
首次服务未复用这两份恢复分配。这是需要定位的驻留/准入问题，
旧日志不能判断未获锁的具体原因，不能用 RETURN 预测误差解释
全部等待。新原因记录用于下一轮确认这一点，未因此放大保护
预算或锁住所有 parent。V21 及其同版本 native 保持冻结，
后续修订计划在完整 pair 后由启动器自动部署并验证。

磁盘清理核验两次旧启动的scheduler已退出、无活跃进程引用、
没有workflow终态产物；只删除165个git无改动workspace，
保留51个有改动或无法确认状态的目录及所有日志/manifest。
净可用空间增加33.840GB，清理后约89GiB可用。回执保存在
stopped_pristine_workspace_cleanup_20261011.json；当前实验
和数据证据保留。

## V21 部署与 V20 完整对照

V20 两臂及 summary、HTML、完成 workspace 清理和最终对照已结束。
predictive 为 154 completed、2 incomplete，耗时 10690.00 秒；
native 为 155 completed、1 incomplete，耗时 10003.74 秒。
完成吞吐为 51.862/55.779 workflow/h，predictive 低 7.02%。
154 个共同完成任务的平均 JCT 为 predictive 3098.94 秒、
native 3487.53 秒，前者低 11.14%；轨迹与 native 初始化差异
仍影响这个比较，不能据此宣称已达成吞吐和可归因 JCT 目标。

2026-10-11 01:21:58 CST，自动启动器核验 V20 完整产物后，
已部署 597688c 和下述新 patch，启动独立 V21 predictive。
部署引擎预期/实际 Git tree 同为
`e6cc9d6322c0b9031243acd1228037003751cadd`。继续使用 156 个任务、
108+48 两波、running=48、Host 200 GB/80:20、HBM 0.9、
180 秒工具上限、500 ms 提前量及相同冻结预测产物；后续
native 将同版本冷启动。启动状态见
`experiments/reports/v21_launch_after_v20_20261011.json`。

本次部署进一步消除机会性容量回收的同步 Mamba 备份等待：
`reclaim_beliefkv_handoff_capacity` 遇到尚无 Host 副本的活跃
checkpoint 时，异步提交必要状态备份，保留原生设备读锁，
本次不把在途副本算作可回收容量。原生 ACK 释放锁后，后续
恢复规划才能回收设备 FULL/Mamba；真正分配与驱逐仍保持原生
同步依赖。不能仅删除等待而沿用不加读锁的 write-back 路径。

74 项相关原生 CPU 检查通过；真实 hybrid allocator 验证在途
页和槽保持占用、ACK 后解锁且后续回收可返回容量。仅传输被
替换为 CPU 夹具，不包含 GPU 或端到端性能证据。该等待点是
代码确认的问题，尚不能归因为下述忙时多出的 40.20 秒。
已部署规范 patch SHA256 为
`98fc5af9c40121d6f7ba1824f1fd5b586d0b141d5b12370a2d9243c1b1b5e8f1`；
完整预期/实际 Git tree 与反向应用检查通过，V20 的冻结代码未改动。

`launch_semantic_h2d_after_pair.py` 支持在完整 pair 后自动部署
已提交的隔离修订，再启动独立 V21：等待原 driver/server/
scheduler 的同一进程身份退出、两侧 summary/HTML/cleanup 和
最终对照完成，核验冻结计划后 fast-forward 与精确引擎增量。
复用原 workload、到达流和模型配置，先 predictive、后同版本
冷 native；状态文件记录目标提交与新目录，不改写 V20 计划。
本次部署已按上述顺序完成；后续源码变化不进入运行中的冻结 pair。

终态 child 到 parent 的容量交接已在冻结版 v20 predictive 中
触发；按真实 spawn 身份重算，371 个 child 累计释放设备
FULL 119.545 GB、Mamba 125.816 GB。192 个独立 JOIN 的
parent 入队到实际首次服务 P50/P90 为 143.10/499.16 ms，
最大仍为 16.838 秒。JOIN 边界前启动并由下一请求复用的
FULL 为 1.507 GB；76.993 GB 需求 handoff 复用继续单列。
这些是关联和复用证据，不是释放空间被 parent 独占的证明。

V21 已包含 sibling RETURN 后激活最后 child 的既有
收尾通知、真实 Mamba 短缺时回收无引用冷备份，以及恢复闭包
内驻留祖先的保护。最后 child 结束后才启动的恢复仍记作需求
恢复，不能算 RETURN 前预取。V20 两臂的 b019d24、部署引擎
c58cc891 和预测产物保持冻结；V21 已使用上述新提交和 patch。

完成 batch 回调进一步复用同一请求内的 rid 和 visible key，
省去重复字典查询；lease 和收尾阶段的元数据仍独立核验，
不跨调度迭代缓存身份。相对 befd5e0 的交替顺序 CPU 基准，
普通回调均值 39.884→35.132 微秒，带语义进度和 16 个待交接
parent 时 145.502→131.500 微秒，分别降低 11.9%/9.6%。
队列顺序与回调后 runtime 状态一致；这不是实测 GPU 吞吐收益。
报告保留为 v20_batch_visible_lookup_{plain,semantic}_cpu_20261011.json。

新增 compare_native_policy_runs.py --busy-window，允许在 native
尚未结束时比较已完整观测的相同忙时窗口，不计算部分 JCT。
按完成 decode batch 的 token delta 统计输出，另列请求结束时
输出长度；worker 墙钟区间按窗口裁剪，不把它当 CUDA kernel
时间。迁移按真实 submit 窗口统计已完成回执，各物理 batch 的
时间只计一次；JOIN/tool、需求 handoff 与原生字节分别归因。
恢复等待单列为采样 CUDA 层依赖，不外推成全部请求等待。

v20_busy_windows_transfers_live_20261011.json 的 600–2400 秒
窗口中，native/predictive 的 decode batch 为 47.309/47.325，
实际 decode 输出为 1059.67/1036.79 token/s，predictive 低
2.16%；GPU 利用率为 82.44%/79.46%。提交请求 11216/11232、
未缓存输入约 10.44/10.32 M token，prefill worker 区间累计
272.95/313.15 秒，差约 40.20 秒。

同窗口总 H2D 为 2.337/2.328 TB，transfer-stream 累计
71.34/67.39 秒；predictive 的 FULL H2D 为 1.298 TB，
其中原生 1.242 TB、需求 handoff 55.969 GB、JOIN/tool
来源 839.434 MB。来源标记不单独证明完成边界前的提前复用。
D2H stream 累计为 140.03/65.45 秒；采样层恢复等待 P95
为 0.174/0.145 ms，464/418 个 batch 合计 86.55/75.49 ms。
这些测量未显示 DMA 或采样层依赖增大，不能据此解释额外的
prefill 墙钟区间；继续核查 CPU 恢复、准入和 worker 路径。

完整窗口复核保存于 v20_completed_busy_windows_20261011.json。
600–2400、2400–4200、4200–6000 秒的 predictive decode
吞吐相对 native 分别为 -2.16%、+0.16%、+2.64%，不能把首段
劣势外推为每个阶段均落后。后二段的 batch、未缓存输入和迁移
需求不同；特别是 4200–6000 秒未缓存输入为 native 8.50 M、
predictive 9.49 M，prefill worker 墙钟为 250.17/383.92 秒，
不能将这个差值全部归为控制开销。优先核验相近 batch/输入的
首段成本，再以 V21 的同版本 native 完成性能结论。

更新审计 v20_capacity_handoff_followup_audit_20261011.json 保留
独立 JOIN/tool 分母。171 个 JOIN 有 FULL 计划，其中 114 个
在至少一个快照中仅缺 Mamba 容量，对应每事件最大 FULL 计划
合计 2.374 GB；这不是已实现预取或可相加的同时容量。工具
10159 个等待事件中只有 1 个确认边界前 FULL 复用。V21 须验证
已部署的历史残余分布路由、状态容量回收和 sibling 通知联动，
不能通过增加 ACK 数量或把需求 handoff 改称预取来扩大覆盖。

本窗口晚于 native 的 331 秒 GC 初始化补做；全程 JCT 仍须
注明初始化差异。native 保留与 predictive 同任务的
django-11087 incomplete：root 最后两次 finish_reason=length，
最终正文 34998 字符，无 deadline、abort 或 guard 终态。
保留为模型终态截断，未因此重启 native 或加入 agent guard。
V21 两臂在 health 就绪后、提交 workload 前统一执行 GC 初始化。
predictive 的 server/gc_freeze_initialization.txt 已记录成功；
早期运行时 physical_disabled=false，工具及语义 worker 正常，
真实工具 forecast 已出现 same_input_completed 和
completed_project_command 的条件历史残余分布。尚未启动的
native 仍需独立核验初始化回执；这些启动检查不是性能达标证据。
新一轮已启动，实际忙时吞吐和可归因配对 JCT 改善尚未达到
active 目标。后续开发在独立 worktree 完成，不修改运行中源码。

## V20 parent 恢复与控制开销优化

V19b 的实际终态回收已工作，但 Host-backed parent 仍需等原准入
队列老化后才进入 handoff。诊断快照中的19个原生FULL Host-hit
parent均等待超过1秒，等待中位数11.52秒；一个parent入队后
等待19.18秒，直到恢复被选中后才在103.79ms内观测到完成batch。
这说明恢复选取太晚，不能将全部等待归因于传输或RETURN预测。

修订1375285在四次普通准入额度满足且存在下一批准入slot时，
若原恢复候选中没有已解锁parent，就从已提交、身份仍有效且
JOIN满足的parent中有界检查最多8个。只有安全checkpoint的
实际恢复extent适配当前free lists时，才将其中1个纳入下一批
恢复候选；没有容量、checkpoint或slot时继续原候选服务。
原生页/状态/DMA依赖、恢复后驻留及有界准入仍有效，不等待
这个parent而空置可用GPU服务，也不独占child原来的页号。
全满的传输ledger不消耗一次恢复尝试。

增加parent_capacity_handoff_restore_lookahead记录，并按相同
workflow/context/JOIN/request关联其选取到首个完成GPU batch
的间隔与选取前排队。选取记录不等于submit或ACK，RETURN后
handoff继续与JOIN/tool边界前的预测恢复分开。328项相关恢复、
调度、容量、普通请求额度与审计检查通过；原生patch保持不变。

V19b于2026-10-10 19:07:25 CST请求停采，19:11:58已核验旧
client/server/scheduler退出、GPU及18454端口释放。80个终态
均completed；156个任务均已到达，其余76个按运行截断保存，
不记作模型incomplete。native未启动，不构成完整性能对照。
最终审计有540个child终态、804次含重试的回收记录，累计释放
190.834GB设备FULL和97.614GB设备Mamba；112个parent的
入队到首个完成batch P50/P90为191.50ms/13.990秒。
独立边界前启动且复用的FULL为JOIN1.234GB、tool0.238GB，
共1.472GB；71.884GB需求handoff复用单列。329次PREPARE中
133次有后续恢复关联，该关联不等同最终forward消费。

4个工作量触发的完整JOIN仍提前约4.15秒；前2个拟合的常数
修正约4.94秒，在后2个事件中仅1个进入500ms误差范围，样本
不足且不能作为在线偏移。95个已观测无工具EOS触发的RETURN
端点误差中位数58.56ms、97.89%在500ms内；这是协议终态线索
的选中事件评价，不是内容模型整体预测精度或数秒提前量。

保留停止记录与三份stopped审计，清理已经完成且归档的80个
root workspace，保留trace、补丁及中断workspace。V20在冻结
提交b019d24上启动，2026-10-10 19:26 CST服务就绪，仍用156
任务、108+48/3600秒、running48、Host200GB80:20、HBM0.9、
工具180秒、lead500ms和原预测产物，先predictive再同版本冷
native。运行中的主目录、引擎和预测产物继续冻结。

已保存prefetch_lifecycle_early_snapshot.json：78个终态上下文（含内部总结请求）、
79次含重试的回收累计释放设备FULL14.797GB、Mamba8.306GB。
23个有终态容量关联的parent，入队到首个完成batch的P50/P90
为198.53/441.96ms，最大16.923秒。7个真实restore lookahead
均关联到后续服务；选取前排队P50/P90为54.83/211.81ms，
选取到首个完成batch为201.32/319.64ms。这是早期关联子集，
不能代替全部JOIN评价，也不能作为native相对节省。
独立完成边界前启动并复用的FULL为JOIN101.888MB、tool3.154MB；
20.198GB已复用需求handoff继续单列。pytest-7571的16.923秒
parent尾部在完成边界附近记录no_restore_plan，没有restore
lookahead。no_restore_plan也可能表示目标已驻留，不能据此
断言checkpoint不存在。原生提交记录有8558个FULL命中token、
8512个Mamba branching token；仍需拆解准入、容量和数据依赖。

后续隔离优化将至少2048个token的同格式int64完整前缀比较
改为不复制数组的memcmp，保留GIL避免Python数组在比较时
改变大小。短段、list及不同array格式继续原数值比较；
namespace、salt、limit、offset、bigram边界和页对齐保持原语义。
指定隔离源码后，127项前缀等价、真实CPU回收/引用、恢复及
ACK检查通过；仅GPU测试启动辅助模块被替换。整次reentry
inspection的交替顺序CPU基准中，32K--98K token、1/8叶匹配
案例中位耗时降低19.5%--29.4%，4K基本不变。结果保存在
v20_followup_reentry_cpu_20261010.json；它未测DMA或GPU吞吐。
仅radix_cache.py及前缀测试与部署引擎不同，隔离patch SHA256：
`67fcbe012a9d3adbd1b5d6cca1510f55ad36b844aae402d968518c3999547617`。
反向应用检查通过；此优化尚未部署，V20仍使用c58cc891。
active吞吐/JCT目标仍未达标。

隔离分支进一步修正D2H与GPU可读性的混淆：只读reentry检查
原先把在途D2H和H2D都当成GPU未就绪，使已完整驻留的FULL
前缀和Mamba checkpoint在备份期间失去恢复就绪判定。现在独立
传播pending_load；D2H不阻断已驻留源数据的读取，H2D及尚未
完成的Host-only副本继续等待原生依赖。祖先、共享叶缓存、实际
输入、session身份与锁/回收权限保持原语义。修复前5个新增检查
有3个复现失败、2个正确拒绝未完成Host副本；修复后132项相关
原生检查通过。该误判尚不能解释pytest-7571的16.923秒长尾。

包含此修正的整次inspection CPU基准对32K--98K、1/8叶匹配
中位耗时降低19.9%--27.0%，4K变化在约1.4%以内；
报告为v20_followup_reentry_d2h_ready_cpu_20261010.json。新隔离
patch反向应用检查通过，SHA256为
`ec65735303567fa11d00eeb85b9cd0257965a414df55766eacebb7250fbf313e`。
相对部署版仅radix_cache.py、unified_radix_cache.py及两份对应
原生测试变化；该修正未部署，V20两臂继续冻结在b019d24/c58cc891。

2026-10-10 20:11 CST容量快照保存在
prefetch_lifecycle_capacity_snapshot_20261010_2011.json：388个
终态上下文（含内部总结请求）、589次含重试的回收记录，累计实际释放设备FULL
124.303GB、Mamba61.878GB，这不是峰值空闲容量。91个容量关联
parent中90个在首次观测服务前有真实设备释放，入队到首个完成
batch P50/P90为214.84/485.33ms，最大16.923秒。36个恢复提前
选取全部关联到后续服务，选取到batch P50/P90为136.30/291.75ms、
最大446.55ms。旧JOIN汇总按context epoch重复归因，把恢复
执行后的工具请求混入首次服务，114条/P90为12.16秒的口径失效；
修正与重算见下方，不能用该错误分母评价容量交接。
独立边界前启动且复用的FULL为JOIN1.053GB、tool3.154MB；
69.057GB需求handoff复用继续单列。

随后工作流终态检查为60 completed、1 incomplete，无error。
incomplete为django-11087：两个child自然RETURN、JOIN满足，
root最后两次输出finish_reason=length，末次正文36832字符且
重复推理；workflow deadline未超时，guard干预为0。保存该
模型终态截断，不把它解释为终态容量回收故障；当前采集继续。

JOIN归因现按workflow/context/JOIN ID保持事件身份，等待epoch
与parent恢复后的epoch观测合并。首次服务只关联完整JOIN后
该parent实际提交的第一条请求；只有恢复后观测也可关联此请求，
缺失完整JOIN边界时保持未知。工具等待仍按自身context epoch
区分，不合并不同spawn轮的JOIN。原始日志未变，不修改GPU调度
来获得这次统计修正。3个新增反例在旧代码中复现错配/重复，
修复后39项生命周期审计检查通过。

v20_join_admission_diagnostics_20261010.json的修正快照覆盖110个
已匹配下一请求的独立JOIN，入队到实际首次服务P50/P90为
169.94/456.97ms，最大16.838秒；110条都关联到容量交接记录，
其中26个JOIN包含多个epoch的观测。django-13023真正的JOIN后
请求等待221.21ms，旧口径额外关联了下一次read_file返回请求的
19.968秒等待。pytest-7571仍有真实长尾，不能通过去重消除。
独立边界前启动且复用的JOIN FULL为1.173GB。此快照比上述
20:11快照更晚，不能把采样数量变化全部归因于统计修复。

后续容量快照prefetch_lifecycle_capacity_snapshot_20261010_2040.json
覆盖568个终态上下文（含内部总结请求）、837次含重试的回收，累计释放设备FULL
202.940GB、Mamba120.987GB；这不是峰值容量或parent独占额度。
118个parent观测到首个完成GPU batch，其中117个此前关联真实
设备释放；39次恢复lookahead均关联后续服务，选取到完成batch
P50/P90为136.97/286.34ms。按独立JOIN及实际服务开始重算，
118个请求入队等待P50/P90为169.11/457.87ms，最大16.838秒。
JOIN边界前启动且由下一请求复用的FULL为1.210GB，tool为
3.154MB；71.236GB需求handoff复用继续单列。tool早期覆盖低、
JOIN长尾及六条完成前到期的FULL驻留仍须优化，不能由这次
容量交接关联声称已超过native。原生遥测failed/dropped均为0。

准入规划CPU剖析进一步发现同一次调用先解析队列身份，编译
计划时又重复解析；156请求/200次规划的profile中，后者累计
约占23%。现在复用本次已绑定的不可变key，正式选择仍重新
核验实时请求/session/epoch/attempt；修订号、重复ID、候选界限
与原生容量权威继续保留。196项准入、运行时、容量及审计检查
通过。整次规划基准以各版本自己的planner/compiler源码比较，
两次连续规划的顺序、累计计数、原生读取次数和优先级元数据
完全一致；156请求的平均CPU墙钟区间降低17.1%--27.6%，
8请求降低2.5%--8.8%。报告为
v20_prefill_identity_reuse_cpu_20261010.json，不包含DMA/GPU/JCT。
该优化仍在隔离分支，V20继续冻结运行；第二批48个任务已经
到达，2026-10-10 20:58 CST终态检查为105 completed、
1 incomplete，无error；native臂尚未开始。

V20实时计时还记录130962次batch_completed回调累计47.14秒
独占CPU墙钟。只要有待交接parent，旧回调就对每个batch内
请求重新解析完整身份；恢复租约也对无关context重复解析。
隔离修订先用已绑定visible key筛选parent context、用当前
租约context筛选可能的消费者，只有相关请求才进入完整实时
身份核验。epoch/session/generation检查、真实锁回执、终态、
语义token进度和已有context记录继续由原回调处理。

242项恢复、终态、租约、运行时和准入检查通过。沿用整段
CPU基准，基线22e3710和新代码的队列顺序、context计数、
语义key/token历史、扫描位置、工具标记、handoff集合及累计
计数一致。48请求/16个等待parent/300轮的整段回调平均为
0.247→0.042ms（降低83.0%）；同时逐步更新语义token时为
0.355→0.134ms（降低62.1%）。无handoff/租约/语义记录时
0.0352→0.0356ms，增加约0.4微秒，不能宣称所有路径均变快。
报告为v20_batch_context_filter_cpu_20261010.json、
v20_batch_context_filter_semantic_cpu_20261010.json及
v20_batch_context_filter_empty_cpu_20261010.json；没有DMA、
GPU吞吐或JCT测量。该修订未部署，不改变当前冻结对照。

终态容量交接的隔离跟进补齐通知与sibling RETURN的事件联动：
child在其它sibling未完成时发送的收尾通知已被保存并绑定下一
请求，但此前只在新语义结果或EOS到达时重新建立最后child的
恢复阶段。现在处理真实RETURN后，只检查该RETURN关联的JOIN；
当只剩一个child时，复用其仍有效、同JOIN和当前context epoch
的通知，接续已观测decode token及速率。保留通知原有有效期，
不因sibling返回延长。工具调用、下一轮请求、旧JOIN及过期
通知不复活；现有有效阶段不重复建立。没有下一请求时先保存
阶段，随后沿用原提交绑定逻辑，不为未提交parent预留容量。

这条联动不直接授权传输：原批量缺失FULL/必要Mamba规划、
原生分池free lists、新输入/decode预留、近期准入及滚动启动
时机仍决定是否H2D；未结束child的页继续保留。新增
final_stage_resumed_after_sibling_return计数及阶段记录字段。
242项终态、收尾、批量预取、恢复、准入与归因检查通过，包括
通知/提交/RETURN同批及利用sibling释放后在最后child RETURN
前提交parent预取的调度回归；后者为mock传输，非GPU收益证据。
此次修订仍在隔离分支，当前V20及随后native继续同版本冻结。

保存v20_terminal_capacity_snapshot_20261010_2115.json的后续
采集中审计：869个终态上下文（含内部总结请求）、1241次含重试的回收，累计实际
释放设备FULL338.281GB、Mamba200.830GB。170个parent完成batch
观察中169个关联此前设备释放；54次恢复lookahead均关联服务，
选取到完成batch P50/P90为152.75/321.15ms。168个独立JOIN
请求入队到实际首次服务P50/P90为163.05/505.54ms，最长仍为
16.838秒。88个JOIN有边界前启动且下一请求复用的FULL，共
1.438GB；tool为3.154MB，76.934GB需求handoff复用单列。
这些是在冻结代码下的部分结果，不能归因于尚未部署的通知
联动，也不证明native相对吞吐/JCT改善。

工具侧运行计数包含20次进入预取窗口、18次无冷容量退出。
需求handoff中4306个请求以no_cold_capacity关闭，但其中
4个关联parent在约100--125ms后即获得首个完成batch，未见
关闭后再收到关联child释放的案例。因此当前证据不支持把
这些parent等待解释成永久不重试；本轮没有增加周期性重试
机制。工具预取早期覆盖与真实JOIN长尾仍是待解决问题。

工具时间头的隔离跟进确认并修正了一条路由错误：模型使用已完成
同输入命令、失败重复命令或项目命令历史得到残余时间分布，worker
同时附带未使用这些历史特征的通用 operational CDF；runtime
优先读取后者，覆盖了历史头。相同9750ms已等待时间、历史时长
10秒/15秒的两个请求，历史P50分别为250/5250ms，旧路径却都
得到约41.56秒。新hint保留历史分布及support detail，以预测观察
时刻为起点按工具仍未结束的条件滚动更新；IPC交付延迟计入
分布年龄。历史支持耗尽后返回未知，不把已过中位数当作RETURN；
无特定历史的请求继续使用原通用CDF，旧reply格式仍可读取。
记录residual_duration_mass、residual_support_detail及独立timing
policy，模型产物和物理容量/准入权限没有变化。

v20_tool_history_routing_20261010_v2.json按独立工具等权诊断
11635个有可核对在线历史的工具、33053个预测快照。最后1秒
内各工具绝对误差中位数的总体中位数为313.43→24.68ms；
其中实际时长至少1秒的1574个工具，1538个有末秒快照，
对应误差为296.95→127.94ms。首次进入预测500ms窗口且实际
仍有0--500ms提前量的长工具为7→838个。项目命令、成功重复、
失败重复支持分别列出；该诊断只使用观察时已记录的历史和冻结
误差余量，未来TOOL_END只作评价标签，并排除未关闭的并行工具。
这是同trace路由诊断，不是项目隔离校准、真实Host-only机会或
GPU收益；短命令占总体样本多数，不能将这些计数称为预取次数。
432项worker、工具时机、物理恢复、终态容量、批量传输、运行时
和生命周期审计检查通过。修改仍在隔离分支；V20预测臂及随后
native保持冻结，待两臂结束后再部署和检验实际早期FULL复用、
繁忙时吞吐、配对JCT及其他请求代价。

终态只读采样的隔离跟进修正旧锚点的生命周期：原生Python
node_by_id直接查_node_arena，已删除节点会抛出KeyError，
原采样把它记成暂时不可观测并持续轮询到60秒到期。现在只将
节点直接查找的KeyError、None及creation_time替换记为旧锚点
消失，先保留该次gone_or_replaced_anchors记录，再移出后续
采样；所有锚点消失时关闭watch并计数。祖先观察的KeyError、
深度不可观测及其他未知仍保留观察，不据此推断空间已经释放。
该修正不发起D2H、DROP或修改物理引用。五个回归在旧路径
复现持续轮询，修复后437项相关运行时/恢复/预测/审计检查通过。
V20正在运行的代码不变，该跟进仍待对照完成后部署。

2026-10-10 21:47 CST采集中终态为147 completed、2 incomplete，
无error。新增incomplete为django-13112：两个child自然返回、
JOIN满足，root最后输出finish_reason=length、21676字符，
报告反复列举测试结果；deadline未过期，guard干预为0。
保存截断终态，不把该记录计入正常完成吞吐或配对完整JCT。

pytest-7571的真实16.838秒parent等待进一步核对：从该请求
原生登记到实际首个服务，记录446个GPU服务区间，其中339个
decode累计13.264秒、batch为45--48；107个prefill累计3.574秒、
batch为1--3。这些是scheduler/worker区间而非CUDA kernel时间，
说明等待期间GPU仍在服务其他请求，不能把整段称为复制阻塞
或空闲。13秒后该parent才进入admission_candidate物理检查，
当时440个Host-backed缺失FULL token、仅1356个device free
token，较早checkpoint已有8558 FULL/8512 Mamba branching
token的原生命中。未记录各次未准入的逐请求理由，仍不能唯一
归因于slot、容量或依赖，也不据此扩大恢复预算或抢占热请求。

压力候选发布的隔离跟进减少无用祖先查询：原生回收每池只读取
前八个候选，但某一池不足八个时，旧发布路径仍逐节点检查
全部注册祖先。现在先保留原顺序扫描十六条，仍未填满时利用
原生FULL设备叶集合和Mamba驻留LRU的实时索引，跳过已经
不可能补齐剩余池的节点。fallback及各池前八个顺序保持一致，
creation_time、Host副本、引用、锁及在途传输继续实时核验。
缺少原生索引时保留原扫描，不跨发布缓存物理驻留判断，也不
改变终态回收、迁移、驱逐或parent准入策略。

443项相关运行时、恢复、终态、工具和归因检查通过。以207643f
为基线、156个workflow/每个24条祖先/150轮交替顺序的CPU
基准中，双池充足发布均值0.16851→0.10122ms，节点读取
192→24；FULL稀疏为3.19760→0.55181ms，Mamba稀疏为
2.95728→0.57657ms，读取均为3744→25。每个workflow仅一
条候选且双池充足时，P50为0.023336→0.023963ms，增加
0.627微秒；均值含一次0.282676ms离群，不能声称短路径均
变快。保留v20_pressure_publication_native_index_cpu_20261010_v3.json
及v20_pressure_publication_native_index_flat_cpu_20261010_v3.json。
早期立即建立索引的版本已被此次延迟建立索引版本替代。
这些是候选发布CPU夹具，未测GPU吞吐或JCT；修改尚未部署。

实时共享路径计时保存于v20_shared_path_cpu_snapshot_20261010_2209.json。
采集9720.138秒时，埋点覆盖的独占Python墙钟累计为控制交付
139.501秒、机会采样115.718秒、reentry检查100.152秒、
batch回调91.747秒、JOIN PREPARE87.136秒、准入规划
76.811秒、语义更新58.985秒、维护54.643秒。这些不能当作
GPU空闲或可直接扣除的JCT。closure_unobservable仍需补齐
具体观察失败原因后再判断是否修改祖先深度，不能将只读终态
诊断的深度失败数量直接当成丢失预取机会。

2026-10-10 22:16 CST终态检查为153 completed、2 incomplete、
无error；剩余pydata__xarray-6992。native尚未开始，V20继续
冻结在b019d24/c58cc891，最新隔离优化待两臂完成后部署。

机会观察的后续隔离修改把capture原有失败原因保留到
closure_unobservable的blocked_detail及wait_prefetch_plan，
区分祖先深度超限、锚点替换、闭包不一致及在途D2H/H2D。
不新增拒绝条件或扫描深度。H2D与PREPARE在同一scheduler
安全点共享已经失败的闭包，PREPARE无需重新查session锚点
和扫描相同祖先；后续采样重新读取，不缓存失败作为未来授权。
失败原因变化即使在500ms日志去重窗口内也保留新记录。

489项物理恢复、运行时、终态容量、handoff、批量预取、工具
及生命周期审计检查通过。相对50aef3d、156个等待parent、
150轮交替顺序、每轮实际观察16个parent的CPU夹具中，65层
祖先超限的采样均值13.778→7.102ms，24层在途D2H为
6.248→3.356ms，在途H2D为6.239→3.349ms；session及
节点查找均减半。正常24层观察均值4.949→4.899ms，读取
数量未变，不将约1%的时差当成确定收益。新失败详情以外的
全部物理机会、PREPARE状态和身份记录逐轮一致，未发起迁移。
保留v20_failed_closure_sampling_cpu_20261010.json；新增
--failed-sampling-only可复现这些CPU测量，尚无GPU吞吐证据。

2026-10-10 22:28 CST确认predictive已产生最终summary：
154 completed、2 incomplete、0 error，旧client及scheduler
退出；驱动正在收尾审计和导出，随后同版本冷启动native。
v20_terminal_capacity_snapshot_20261010_2226.json使用修正后
独立事件口径：1108个终态上下文（含内部总结请求）、1534次含重试的回收，累计
实际释放设备FULL456.008GB、Mamba343.645GB；不是峰值
空间或parent独占额度。192个独立JOIN请求入队到实际首次
服务P50/P90为143.10/499.16ms，最大16.838秒。54次恢复
lookahead均关联后续服务，选取到完成batch P50/P90为
152.75/321.15ms。93个JOIN边界前启动且下一请求复用的
FULL共1.507GB，tool为3.154MB；76.993GB需求handoff
复用单列。当前结果来自冻结代码，不归因于未部署的采样优化
或sibling通知联动，也不证明已超过native。

native于2026-10-10 22:31:29 CST服务就绪，健康检查通过并
开始相同到达流。predictive已导出timelines/predictive_h2d.html，
清理154个完成workspace，保留2个incomplete的workspace及
全部trace/结果；短时可用磁盘74→106GiB，native克隆新任务
后余量继续变化。运行源码及产物仍冻结，新修订在隔离分支。

启动核对发现native的post-warmup freeze_gc在HTTP监听前
遭连接拒绝，predictive原初始化则成功。2026-10-10 22:37:00
CST补做同一POST，返回200；时间比native服务就绪晚331秒，
workflow已经开始。原始失败保留，另存native/gc_freeze_correction.json。
这是初始化条件差异，不是模型或调度版本变化；本轮全程JCT
需注明它，完成补做后的繁忙窗口单列，不能当作完全一致的
冷启动对照。HTTP响应证明tokenizer接受并交付原生消息，不是
scheduler/detokenizer的独立GC状态ACK。隔离launcher已将
相同初始化移至health成功之后、启动workflow client之前，
两臂都执行；保存gc_freeze_initialization.txt，不改GPU服务策略。
已用实际端点200及bash语法检查验证，下一轮再检验完整对照。

终态对象分类的后续审计纠正了此前把所有非root调用都称为child
的问题。保留原报告，新增v20_terminal_capacity_scope_20261010_v2.json；
按客户端invocation_create的runtime_internal和relation_type分类，
不用ID命名推断身份。本轮1108个回收上下文包含371个真实spawn
subagent及737个内部上下文总结请求；371与workload摘要一致，
无未知类别。真实child的797次含重试回收累计释放设备FULL
119.545GB、Mamba125.816GB，Host FULL48.573GB、Mamba50.739GB；
内部总结分别释放设备FULL336.464GB、Mamba217.828GB。历史快照
的456.008GB/343.645GB是所有终态上下文总量，不能全部归为
child到JOIN parent的交接，也不是峰值容量或节省时延。

回收日志新增invocation_scope，审计分别报告terminal_contexts、
terminal_children和by_invocation_scope；只有确认的动态subagent
释放关联JOIN parent。内部总结的合法终态回收继续执行，不新增
迁移门槛或改变调度；缺元数据的历史对象保持unknown。80项
终态容量和生命周期检查通过，包括带有总结字样的真实spawn、
无特殊命名的内部调用和内部回收不产生parent交接。此修订
在隔离分支提交，当前native继续使用冻结主目录版本。

工具历史路由的物理机会交叉审计保留为
v20_tool_history_physical_opportunities_20261010_v3.json。完整trace中
11944个工具、34075条预测匹配已完成历史头；持续至少1秒的
工具在最后1秒内按事件等权的中位绝对误差为generic295.675ms、
history125.244ms。此为固定trace的路由诊断，非未见项目校准。

仅用相同workflow/invocation/context/epoch、相同活跃工具、已经
接受且未过期的预测，关联当时的正FULL物理规划。582个工具
有可匹配规划，history在154个工具进入记录的整批启动窗口；
13个当时free-list可直接容纳，11个的实际工具结束提前量在
0--500ms，逐事件最大FULL规划量合计377.917MB。其余不满足
直接空闲容量的记录仍可能需要原有冷页回收，不能全判不可用。
500ms宽松诊断和实际记录的latest-start窗口分别统计，缺少
窗口的记录不补猜。未来TOOL_END只用于偏差评价，不决定
是否入窗；5项审计检查通过。规划字节可能跨事件共享页，
不是可保证的预取量、ACK、复用或吞吐收益；queued历史路由
与sibling容量释放需在下一轮GPU实验共同验证，当前代码不变。

容量交接的隔离跟进修正了一条实际恢复阻塞路径：FULL free-list
充足、Mamba槽位不足时，原handoff仅允许session_ref=1的等待
agent状态回收，遗漏session_ref=0的冷备份；只扫FULL叶也会
遗漏内部节点上的无引用Mamba。现在仅在真实Mamba短缺时，
检查原生未加锁Mamba LRU的前8项，释放已有Host副本、无引用、
无在途传输的设备状态。只释放Mamba，保留FULL数据及其活跃
引用、Host状态和原生恢复依赖；已有等待agent回收仍按原因果
状态验证。真实终态child私有页继续走无需D2H的直接回收路径。

需求handoff现在与JOIN/tool一致，将本次恢复闭包内所有节点
排除出冷回收候选，包括已驻留祖先，避免为恢复parent反而
回收它马上要用的前缀。两个新增短缺案例在旧实现中复现失败；
修复后184项runtime/终态/批量预取/工具/归因检查及72项原生
CPU检查通过。后者含真实FULL/Mamba allocator的叶与内部节点
槽位释放，确认FULL free-list不变、Mamba free-list实际增加且
不新增D2H；只有GPU测试启动辅助模块替换为unittest.TestCase，
没有替换树、引用、分配器或回收方法。

保留v20_restore_pool_shortfalls_20261010.json。192个独立JOIN中，
171个有可观测非空恢复规划的准确容量快照，114个曾出现仅
Mamba短缺，逐事件最大FULL规划量合计2.374GB；167个曾仅
FULL短缺、125个曾双池短缺、166个曾双池充足。同一事件可以
跨多个条件，不能相加作互斥分母。工具10159个观测事件中仅
194个有完整容量快照，没有观测到Mamba短缺不能推出其从不
发生。快照不是实际拒绝原因、冷候选可回收证明或可保证收益。

隔离规范patch正向临时index及反向应用校验通过，SHA256为
`93d2b9e31c23ec466c48aaab6cf1b8129e259a96fc484b59dc3a894b87375480`。
本次修订仍未部署，V20 native继续使用b019d24/c58cc891。
成对采集结束后部署，再验证sibling释放支撑的真正RETURN前
FULL复用、最后child结束后的需求恢复，以及繁忙吞吐/配对JCT。
不能用已回收字节或槽位测试替代GPU收益，完整active目标仍未达成。

## V19b child 上下文结束的集成修正

V19诊断快照已观察到child自然RETURN，但终态回收和parent消费记录
均为0，因Deep Agents task把child声明为persistent=True。停止后的
完整审计最终记录1次回收、0次parent消费，不能将快照0次当作
最终总数。这个
标记保留任务内部多轮上下文，不能据此推断RETURN后还会复用
同一child上下文。适配器原本已在RETURN后retire原生session，
因此现在由任务完成回调自动携带context_retired=True，runtime
按这个权威生命周期信号处理终态；不增加模型工具或输出门槛，
不改变运行中child的persistent特征，也不要求重新训练预测器。
同一context仍有其他活跃invocation时继续保留。

另补齐RETURN和parent下一次LLM_SUBMIT同批处理时的checkpoint
关联：允许相同session/generation的一次epoch推进，随后仍按
实际下一请求身份和驻留核验准入。容量交接的RETURN交付与
首个观测完成batch改用同一scheduler墙钟；客户端原始event
时间单独保留，不能直接与服务器墙钟相减。

498项运行时、harness、恢复与生命周期检查通过；原生
116项allocator/引用/传输验证仍适用于未变更的规范引擎patch。
V19在2026-10-10 17:47 CST停止，0个root终态，156个计划任务
按截断或尚未到达保存，旧client/server/scheduler及GPU已退出。
保留集成失败的trace与停止审计。下一轮V19b仍按同配置先
predictive、再稳定同版本native；先确认真实容量回收及parent
消费记录，再讨论忙时吞吐与JCT。active目标继续。

V19b于2026-10-10 17:54 CST冷启动，冻结版本220896e。
早期快照已确认27个真实child RETURN均有终态回收记录，实际
释放166016个FULL token（3.400GB）和71个Mamba slot
（4.572GB），无待重试依赖；27次回收平均1.51ms、最大2.80ms。
第一个JOIN解锁parent已观测到首个完成GPU batch：scheduler
收到最后一个child RETURN到该batch约105.96ms，原生入队到
该batch约86.01ms。该记录关联两个child释放的3873个FULL
token和6个Mamba slot，但不证明parent独占这些物理页。

这批回收没有额外D2H，提前规划与返回后容量回收/准入路径已经
在真实Deep Agents工作流中触发。遥测failed/dropped均为0，
语义worker无错误，负载已进入Host/GPU迁移阶段。样本仍为
采集中部分结果，不能由一次parent服务或回收字节推断吞吐
提升。实时审计保存在V19b/predictive_h2d/prefetch_lifecycle_live.json。
主工作区、引擎和预测产物继续冻结；状态更新在隔离工作区提交，
本轮先predictive再同版本冷native的自动顺序保持不变。

## V19 终态 child 到 parent 的容量交接

已实现定向终态回收与 parent 恢复/准入联动。收尾通知继续使用
既有安全 checkpoint、缺失 FULL extent 和必要 Mamba 的规划；
通知及模型 EOS 不触发释放，只有真实 RETURN 且上下文已结束
的 child 才在 scheduler 内释放 session 引用并检查私有叶节点。无共享
引用、设备/Host 锁或在途 D2H/H2D 的终态后缀直接交还原生
allocator，不先做 D2H；共享祖先和仍有依赖的页保留。

close_session 可能先于控制 RETURN 到达，因此闭合 session
保留有界的 generation/leaf 来源，重开 session 会失效旧来源。
工具结束和 RETURN 同批交付也能完成回收。依赖尚未结算的
节点按原生锁/传输状态重试；不借预测状态释放运行中 child。
FULL 和 Mamba 各自核算实际释放量，不把完整 child 上下文
大小当成可用容量。Python tree core 实现回收，其他不支持
该接口的 core 保留原生缓存行为。

较早 sibling 的释放可用于最后一个 child 返回前的既有预取；
最后一个 child 返回后恢复仍记作需求 handoff。实际容量释放
使恢复立即可重试；在下一批真实准入候选内，有界偏向已解锁
parent 的缺失 checkpoint 恢复。parent 已提交、JOIN 满足且
FULL/必要 Mamba 已驻留后，再进入现有有界优先准入；普通
请求老化和四次普通准入额度继续有效。没有独占容量预留或
复用同一物理页号的要求，GPU running slot 仍由原生管理。

新增 terminal_child_cache_reclaimed 和
parent_capacity_handoff_first_service 审计，分别记录真实分池
allocator 释放、关联 JOIN、parent 请求等待与首次观测到的
已完成 GPU batch。该 batch 回调不是精确 kernel 开始时刻，
也不是释放容量被 parent 独占使用的证明。未知池字节大小
保留为未知单位；预测 H2D ACK/FULL 复用与需求恢复继续分列。

440项相关运行时、恢复、物理回执与审计检查及116项原生
会话/树/allocator/恢复检查通过，包括真实 CPU FULL+Mamba
allocator、共享前缀、两种 close/RETURN 顺序、锁与 DMA
重试、session 重开、普通请求老化及一次性消费。
规范引擎 patch SHA256：
`c58cc89194a66fe74d2a0e842db4972e5d1d059a2f4870f109f36f223c8ee4ae`。

V18于2026-10-10 17:25 CST停止，旧client/server/scheduler
全部退出，GPU及18454端口已释放。69个终态均completed，
其余87个任务按运行中断或尚未到达保存，不计为模型incomplete。
独立边界审计确认JOIN687.86MB、tool52.24MB提前且复用FULL，
共740.11MB；需求handoff确认复用52.138GB另列。JOIN最后
ACK到服务P50/P90为327.78/940.52ms，但最大仍18.96秒，
8条FULL保护在完成前到期。这是旧版截断观察，不是新容量
交接的性能证据。停止记录与三份审计位于V18原目录。
已清理69个完成并归档的root workspace；trace、回执、
补丁及中断中的workspace保留，磁盘空闲约161GiB。

下一轮冷启动 V19 predictive，再运行稳定同版本 native。
保持156任务、108+48/3600秒、running48、Host200GB80:20、
HBM0.9、工具180秒、lead500ms及预测产物不变。实现验证
尚不证明忙时吞吐或 paired JCT 提升，active 目标继续。

## V18 请求入口修订与启动计划

V17于2026-10-10 16:21:53 CST停止开发采集，已核验旧client/
server/scheduler退出和GPU释放。38个终态结果均completed；
其余118个计划任务是运行中断或尚未到达，不计为模型incomplete。
保留原计划、trace、回执及停止审计，不作完整native对照。
停止审计确认独立提前且复用FULL为JOIN419.66MB、tool62.38MB，
合计482.04MB；需求handoff复用47.095GB另列。JOIN最后ACK到
服务P90为16.73秒，18条FULL锁在完成前到期，仍有明确优化缺口。

隔离修订限定Qwen3.5的纯文本Jinja入口：复用模板已有token IDs，
保留模板缓存，省去decode及tokenizer再次encode；有image/video/
audio/modalities的请求仍走原有媒体入口。真实tokenizer的33个
代表性输入（17--71199 token）与旧入口完全一致，覆盖工具历史、
文本parts、关闭thinking及继续assistant报告。冷缓存线程CPU
均值33.136→16.357ms，热缓存15.256→0.951ms；这些是依据终态
trajectory重构的CPU输入，不是原始HTTP逐请求对照或GPU收益。
报告：`experiments/reports/v18_chat_prompt_encoding_cpu_20261010.json`。

双方带因果身份的请求共用入口计时，经过原生pickle与request
stats复制保留转换开始/完成、tokenizer完成、dispatch开始、
scheduler handler接收和原生队列登记时间。审计按独立JOIN/tool
事件拆分body sent→API received、转换、tokenizer、dispatch/
IPC和scheduler登记；缺边界及逆序区间保留未知。dispatch时间
在序列化/发送前采集，区间不能直接当成纯IPC或隔离tokenizer成本。
没有逐decode步骤新增计时、没有改变agent终态协议或工具180秒配置。

89项原生入口/身份/会话/恢复/调度/回执检查及79项遥测/生命周期
审计检查通过。规范引擎patch SHA256为
`f41f2d118b549a5d7b74fe77b154691f6409cb9804493605a17e8a44ceb31468`。
冷启动V18仍用156任务、108+48/3600秒、running48、Host200GB80:20、
HBM0.9、lead500ms和同一预测产物；先predictive，再稳定同版本
native。当前尚无V18吞吐/JCT收益证据，active目标继续。

## V17 快速迭代与当前部署

用户已要求收集到可优化问题后及时停止开发采集，部署已提交修订并冷启动；
不再为等待每一轮native对照而延迟验证。V16b于2026-10-10 15:28 CST
主动中止，15:31 CST核验旧client/server/scheduler全部退出及GPU释放。
80个已输出结果的workflow全部completed，另外76个计划任务没有终态，
属于开发中断截断，不算模型incomplete；native侧尚未启动。
旧计划、trace及停止审计保留，不能作为完整吞吐/JCT对照。

V17部署四项已提交修订：持久预测结果poll及HTTP阶段计时；多extent
消费查找共享队列扫描；有效恢复锁跨客户端提交保留至既有ACK+10秒
边界；冷副本发布仅收集原生实际读取的各池前8项。规范引擎patch及
模型不变。保持156任务、108+48/3600秒到达、running48、Host200GB
80:20、HBM比例0.9、工具180秒及原有图/上下文/工作流配置。
先predictive；候选稳定后完成同版本冷启动native对照，仍以繁忙窗口
服务吞吐、配对JCT及真实提前FULL复用验收，active目标尚未完成。

V16b停止审计确认独立JOIN/tool提前且复用FULL分别612.52/296.45MB，
合计908.96MB；需求handoff确认复用47.947GB单列。JOIN最后ACK到
首次服务P90仍15.95秒，需要继续测清请求入口和原生准入。

新增有符号偏差审计，按独立完整ALL JOIN区分estimated_work与
observed_no_tool_eos，并以前半事件拟合、后半事件检验常数校正。
11个工作量触发的预测RETURN误差P50为-4619.51ms（负数表示早），
其中生成终点误差P50为-2813.37ms；89个EOS触发的RETURN误差P50
仅-322.83ms。拟合工作量常数+2557.72ms，后半6事件绝对误差P50
4788.18→2230.46ms，500ms命中率0→16.7%，仍未达到目标。
EOS校正+367.72ms使后半45事件500ms命中率68.9%→82.2%，但绝对
误差P50反而217.42→296.21ms。样本仅覆盖已触发且最终匹配的JOIN，
不是全量预测或项目隔离校准；本版不部署统一几秒偏移。
原始明细为V16b的join_candidate_windows_stopped.json。
下文冻结/未部署为历史记录，不覆盖本节快速迭代约定。

### V17 运行诊断与短完成窗口验证

16:00 CST的运行审计中，工具请求body发送完成到原生队列登记
P50/P90为684/4702ms，原生登记到首次worker服务为4398/19400ms；
前一段包含serving入口、模板、tokenizer及IPC，尚不能单独归因到
tokenizer。JOIN已有10条带锁FULL在child真正完成前到期，说明
跨客户端提交保护无法修正提前过多的预测。该快照有大量未完成
事件，不能当作最终吞吐或同版本native比较。

隔离CPU验证改为预测剩余4/8/16/32 token的短窗口，并按独立请求
的首次触发评估。V16b的6949个因果快照来自85个请求/83个workflow；
新工作标签按workflow划分47训练、19阈值选择、17个pydata检验。
四个窗口均未在选择集找到精度至少0.8且至少3个首次触发的阈值，
因此没有部署。现有32-token倒计时在17个检验请求触发15个，仅
1个首次触发落在真实32-token窗口；全部触发的RETURN提前量
P50为5975ms。不能用只计算正确触发的747ms中位数代替该误差。
这不是全流程未见项目检验：既有阶段头/编码器见过pydata；
分类器输出也不是校准概率。明细：
`experiments/models/live_completion_windows_v16b_pydata_20261010/report.json`。

下一项具体修订是核验Qwen3.5纯文本入口的重复encode/decode/encode，
先用真实tokenizer验证输入token一致与CPU成本，保持媒体路径及
运行中引擎不变；再补充入口与原生队列边界，定位驻留消费等待。

## V16 当前执行目标与实现

继续解决V15繁忙阶段的predictive吞吐/JCT劣势，而不以native末尾
工具长尾造成的全程优势作为完成证据。当前active目标同时包括：
降低控制路径成本；提高真正提前且被下一请求消费的FULL覆盖；
缩短恢复到服务的等待；减少重复加载及无消费的PREPARE。

JOIN/tool已取消固定两节点限制，使用下一请求安全输入checkpoint
中的缺失前缀，祖先优先、每批最多16个extent；按FULL/Mamba实际
容量、预留prefill空间、驻留字节预算及整批H2D启动窗口截取。
每节点保留独立预留和ACK，尾部拒绝不撤销成功前缀。Mamba只在
选中的恢复checkpoint确有缺失状态时搬运；投机PREPARE仍只传FULL。
不足的HBM只从既有有界冷副本回收，整个恢复目标闭包排除在外。
动作前重新检查因果身份、物理计划及child实际服务进度，不能用
旧的单节点时机授权后来扩大的一批传输。

批量ACK的驻留锁合并到最深前缀：先取得新锁，再释放祖先锁；
保留每节点消费回执，预算及恢复优先级按独立请求目标计数。
分配压力下整组释放，避免已合并记录仍虚占保护额度。
既有控制成本修订一并进入本版：一次准入规划复用等待排名，
按context索引就绪恢复，去除重复驻留检查，终态观察用标量序列化，
FULL PREPARE合批提交。CPU改善不等于GPU或端到端收益。

新审计按独立JOIN/工具等待事件报告最大观测FULL计划、提前submit
且确认被该事件下一请求复用的FULL、边界前ACK且复用的FULL、
下一请求原生Host-hit及单列的需求handoff、客户端提交空档、
请求到首次worker服务和ACK到服务。计划是有界观测快照，不是
oracle分母；Host-hit与handoff未证明分配互斥时不能相加；
缺身份、时钟、分池字节或服务证据保留未知，batch依赖等待不按
请求重复累计，也不直接换算为JCT节省。

V16已从冻结提交`1c0a3ea`启动，并于2026-10-10 13:55 CST因
确认的实现错误中止；不作为完成的性能对照。django-11239的
一个child在同一epoch14请求中连续执行两条grep，第二条工具
开始仅改变invocation revision，却误使恢复租约以
`wait_episode_changed`释放：ACK后约14ms失去保护，约16.5秒
后的首次服务未复用该FULL。现已按不可变checkpoint的epoch/
session/代际判断有效性，同请求工具进度不再撤销保护；有效期、
实际容量、终态与物理驻留检查保持原有边界。

工具审计改用同epoch原始请求与下一请求之间的全部工具START/END，
包含采样中未出现的连续工具；缺任一END或请求边界时完成时间为
未知。新增客户端submit→原生请求到达、客户端submit→首次服务
字段；前者包含请求准备、传输与serving前端，不直接认定为客户端
开销。故障请求的三段间隔为工具完成→客户端提交60.97ms、
客户端提交→原生到达6210.00ms、原生到达→worker服务10244.46ms。
不能把这些时间统称H2D耗时，或仅靠延长驻留掩盖等待。
249项相关检查通过，新增等待分解后19项审计检查再次通过。
原有404项runtime/物理/策略/审计和96项原生CPU检查为上一修订。
V16的108个workspace及107个遗留容器已清理，tracked变更和全部
untracked文件（含ignored文件）先归档，trace、回执、原计划及
停止审计保留；108条中只有1条已输出终态，不能把中断视为incomplete。

下一轮独立命名V16b，冻结新提交及同一规范引擎包，先predictive再冷启动native：
156任务、108+48/3600秒到达、running48、Host200GB80:20、
HBM Mamba/FULL0.9、工具180秒，其余已建立配置保持一致。
重点检验600--2400秒及补充到达后的繁忙窗口、配对JCT分布、
控制独占耗时、提前FULL实际复用、原生残余恢复与重算。
V16停止审计确认JOIN/tool提前且实际复用FULL共127.96MB；
handoff确认复用15.403GB另列需求恢复，不能算提前覆盖或吞吐收益。
JOIN/tool仍存在较长恢复后等待，当前无完成的V16 GPU性能对照，
不宣称上述性能问题已解决。诊断：
`experiments/reports/v16_stopped_diagnostic_20261010.md`。下文为V15
结论和此前开发记录，旧的“未部署/冻结”以记录当时状态理解。

### V16b 冻结期间的隔离优化

V16b主目录冻结于`1a09ada`，规范引擎patch SHA256为
`68109d51ceb77f92fcbc2d17b52cfe76717b4ad26c47fd3642923b43ed0ca615`。
后续改动只在`perf/v16-control-wait-20261010`隔离分支提交，未部署到
本轮任何一侧。14:33 CST的运行快照未出现物理路径禁用或语义worker
错误；实验未完成，不能报告native相对收益。

原生预测worker复用结果管道的poll监听器，避免推理进行中每次空
轮询构建selector；保留超时、退出、序列/种类校验及pending合并。
真实spawn进程和有界队列基准覆盖admission/tool/JOIN结果：
空结果轮询线程CPU均值9.11→3.25微秒（降低64.3%），墙钟均值
9.80→3.92微秒；结果已就绪时91.53→92.48微秒，无改善。闲置且
没有in-flight任务的路径基本不变。输入、输出和生命周期相同。
这是CPU微基准，不是线上控制成本或GPU吞吐收益。

成功LLM请求新增HTTP hook开始、body发送开始/完成及响应头时间，
经现有异步诊断writer记录，不写prompt正文。独立事件审计将
客户端submit→HTTP hook、hook→body sent、body sent→原生到达
分别列出。body sent只是传输写入完成；最后一段仍含服务入口、
校验、模板/tokenizer和IPC，不能当作独立tokenizer耗时。缺回执、
多次尝试和逆序时钟保留未知；56项相关检查通过。基准报告为
`experiments/reports/v16_native_predictor_poll_cpu_20261010.json`。

多extent恢复租约在同一次刷新中按context临时分组可见请求，共享
一次队列扫描；仍逐回执匹配workflow、invocation、session/代际、
epoch及下一请求身份。已有等待session证明时，物理登记省去无用
的可见请求扫描；没有跨调用缓存请求或驻留。250项相关检查通过。
156 workflow、16个回执的CPU夹具中，无下一consumer/已有consumer
的线程CPU均值232.13→67.88/342.54→193.74微秒，下降70.8%/
43.4%，可见队列扫描16→1。单回执基本不变，已demand-ready的
16回执约慢0.58微秒（1.6%），退步与全部原始分位数一并保留。
登记与消费身份、输出和有效性一致。报告
`experiments/reports/v16_restore_lease_matching_cpu_20261010.json`
包含实际隔离导入路径和源码SHA256；原生enqueue使用stub，不含
GPU服务、DMA或吞吐证据。本修订未部署到V16b任一侧。

驻留消费继续拆分客户端提交和原生可见请求。下一版在收到同一
workflow/invocation/context的下一epoch外部`LLM_SUBMIT`时，只为
仍有效、未过期且持有原生锁的恢复记录保留既有ACK后最多10秒
保护，记录`prefetch_client_submitted`；不建立可见请求或准入，
`demand_ready`仍须实际原生请求与session/代际匹配。未取得锁、
内部摘要、旧请求、未完成工具、过期或失效身份均不延长。
就绪状态再出现也不缩短已提交请求的保护。385项runtime/物理/
工具/策略/审计检查通过；本修订仍在隔离分支，尚无GPU收益证据。

审计先前只保留登记/释放，漏掉了中间的就绪/提交记录；现将
`prefetch_reentry_ready`、客户端提交与原生可见提交全部保留，
并按完整事件的完成→客户端提交→原生到达→首次服务定位带锁
FULL记录的到期阶段。缺失或逆序边界保持未知，到期不等于复用
失败。V16b未完成快照确认提前FULL复用JOIN437.84MB、工具
296.45MB；16个带锁FULL到期记录中14个发生在真正完成前，
1个在完成到提交间、1个在提交到原生到达间。后一工具案例ACK后
152ms完成、209ms提交、4.64秒原生可见，原锁3.24秒释放；
这不能解释全部低覆盖，10秒上限也不保证其后准入及时消费。
上述记录是阶段关联，不是隔离的因果或完整性能结论。快照为
`experiments/reports/v16b_prefetch_lifecycle_snapshot_20261010.json`。

冷副本发布只收集原生实际读取的各池前8个候选；FULL和Mamba
分别收集，只有两池均达到原生读取上限才停止扫描。候选不足时
继续查找，仍检查节点身份、Host副本、锁、session引用和进行中的
传输；原生回收前的因果/物理再验证保持不变。272项相关检查通过，
循环成本修订后68项策略检查再次通过。156 workflow、每个32节点
的CPU夹具中，充足候选的查找4992→256次/发布，线程CPU均值
5.036→0.235ms，下降95.3%；FULL或Mamba候选稀疏时查找次数不变，
线程CPU分别下降14.5%/19.3%。原生读取的各池前缀、顺序及备用
前缀完全一致；该改动不增加回收范围，也不直接证明GPU收益。
完整分位数、导入路径和源码指纹为
`experiments/reports/v16_pressure_publication_cpu_20261010.json`。
修订仍仅位于隔离分支，V16b两侧继续使用冻结版本。

## 当前目标与 v15 最终对照

当前目标是在固定workload、模型、容量及到达表下，使predictive
相对native取得可核实的性能提升。除预测传输外，优先减少调度/
控制开销，利用agent依赖、恢复就绪与HBM局部性改善实际执行。
后续新实验统一先predictive_h2d、再native；与reactive对照时
同样先predictive，以便发现实现故障后及时停止、修复、冷启动。
启动脚本和计划生成器默认顺序一致；正式多轮也先predictive，
报告每轮及均值/波动和固定顺序的局限。历史冻结计划及续跑顺序
保留。V15双侧及审计、HTML、workspace清理均已完成，工作代码的
冻结现已解除；原始V15配置与产物继续作为历史证据保留。
下文带时间的“运行中/冻结/尚未部署”属于当时的修订记录。
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

当前目标仍未达成。v15双侧均从主目录冻结提交
`feb5ee01a9f1340a694dcba442c439d08e4bd274`启动，完整引擎patch
SHA256为`dbde39b7f37977ecacd72dddf78b3da94a56fa6a0879afb1798ae55ab7f1fe63`。
既有driver依次执行predictive_h2d、native，各自冷启动服务与缓存；
156任务、108+48/3600s到达、running48、Host200GB80:20、
HBM Mamba/FULL0.9、context131072、completion8192、
graph2048/reserve32、workflow14400s、2--4 child、seed21及模型
产物在采集期间均冻结，工具命令上限为600秒。共用客户端优化在
两侧生效；后续隔离修订没有进入V15任何一侧。

V15最终两侧均156/156 completed，无error/incomplete，任务正确性
未独立评测。native/predictive采集时间为13410.271/10857.481秒，
完成吞吐41.878/51.725 workflow/h，predictive分别缩短19.04%、
提高23.51%；但平均JCT为3295.114/3671.538秒，P50为
2670.676/3119.630秒，P95为7487.913/7781.572秒，均变差。
95/156任务的predictive JCT更高，61个更低。两侧请求序列均有
观测差异；同seed不等于同token轨迹，不能据此声称独立KV策略收益。

全程GPU均值61.582%/74.476%、输出620.789/795.773 token/s主要
受native收尾工具长尾影响。600--2400秒两侧decode batch均约47.3，
native/predictive GPU为82.328%/75.900%，输出1089.888/961.813
token/s；4200--6000秒同样是native更高。native最后pylint-6528
耗时13362.885秒，predictive为5573.747秒；前者重复执行exit=31、
约155秒的命令，10200--12600秒GPU均值仅0.11%--0.16%。
180秒工具上限不会截断这些155秒调用，不能把本轮总时长优势
解释为已提升高负载执行效率，也不新增重复调用guard。

JOIN/tool提前恢复共176次、6.059 GB，其中FULL仅1.230 GB、
确认复用1.036 GB。需求handoff为15132节点命令、118.265 GB，
其中FULL传输/复用67.205/67.047 GB，必须单列为需求恢复。
predictive侧原生H2D为3.664 TB，native为3.268 TB；提前FULL
仅相当于predictive原生FULL H2D的0.069%。JOIN/tool的ACK到服务
P50为0.755/1.035秒，P90仍17.686/15.326秒。PREPARE为846次、
8.361 GB FULL，196次关联后续恢复；62个压力回收事件中58个关联
先前PREPARE，715次后续同node/pool D2H前均有Host驱逐。旧前缀缺失代理
为0.15318%/0.15376%，尚无明确重算改善。

V14/V15去重JOIN相关child样本为74/75个：原生生成完成到客户端
result的P50为1375.965/254.628 ms，JOIN到parent提交为
627.861/25.357 ms，parent提交到首次worker服务为777.090/
352.471 ms。但服务端到达后的排队P90为11.163/14.512秒。
客户端请求/流式转换、工具碎片处理和异步session关闭已减少积压，
没有更换预测权重。提前RETURN 0--500 ms的动作从23/105变为
57/99，但V15的87/99 JOIN动作发生在原生EOS之后，仅12次使用
estimated-work并早于EOS。因此这是客户端交付与动作时机改善，
不能宣布语义剩余工作或自然RETURN预测误差已大幅收敛。

下一轮优先验证已提交的控制路径减负、发起到ACK的驻留保护和
缺失FULL前缀合批，再检查高负载阶段的GPU利用率、JCT、真实FULL
消费及原生恢复量。完整分析见
`experiments/reports/v15_final_analysis_20261010.md`；原始对照与HTML
保留在`experiments/raw/qwen35_native_predictive_replenished_108plus48_20261010_v15`。
V15完整后处理结束后，引擎仅应用已核验的精确增量，规范完整patch
SHA256为`c6f9183ac7b95b6710d5988fb1d5adf500ebc5a859225c879d5cef41193d18ba`；
应用前核对旧包、应用后反向核对新包，修改的Python文件编译通过。
部署记录已更新在`v16_engine_followup_manifest_20261010.json`。
隔离期间已提交的后续修订随本次合入工作代码，不改写V15结果；
尚未启动下一轮GPU实验。

本轮注释补充继续作为同一active目标的执行要求：FULL必须保留
有效Host副本，只传安全输入前缀中缺失的extent；Mamba不参与
投机PREPARE，必要检查点仍由原生驱逐写回和恢复依赖管理。
隔离下一版已将JOIN/长工具等待的缺失FULL前缀改为祖先优先、
每次最多8节点的批量排队，统一启动D2H；单节点直接使用既有
原生写入入口。每节点独立预留和ACK，后续节点被拒绝时仍提交
前面已成功入队的节点，不回收其他Host副本，也不把ACK视为
实际消费。动作前只重新捕获一次原生闭包。候选日志保留首个
extent的估计，并携带全部实际入队command_id；审计关联各节点
回执，不能将首节点估计乘以批量大小或把需求handoff记为预测。
相关CPU验证及提交次数报告为
`experiments/reports/v16_prepare_prefix_burst_cpu_20261010.json`；
251项runtime/物理/审计检查和94项原生检查通过。CPU夹具中
4/8/16缺失extent的提交次数为4→1、8→1、16→2，逐节点回执
与FULL字节相同，Mamba分配为0；保留全部计时及离群数据，
不能把均值差异当作真实DMA或吞吐收益。当前引擎包清单为
`experiments/reports/v16_engine_followup_manifest_20261010.json`，
规范patch与精确冻结引擎增量均通过应用/反向核验。
报告不包含CUDA传输、调度轮询等待或端到端吞吐。本项尚未部署，
仍待完整冻结对照后验证PREPARE消费、重复补传与native相对收益。

隔离下一版继续降低恢复准入控制成本：同一次规划内只计算一次
候选等待排名；恢复租约按context临时分组，随后仍校验workflow、
invocation、session/generation、epoch与请求身份。没有就绪恢复时
跳过租约匹配扫描；下一次规划重新构建索引，不缓存物理驻留，
普通请求老化、恢复优先额度、过期顺序和JOIN同期限优先均保留。
260项相关检查通过。同输入CPU基准固定前版`81b824e`，每配置
300次交替运行、每次规划两遍，排序、累计计数与原生检查次数
完全一致。156候选、0/4/16/48租约时，平均wall耗时分别为
3.737→3.488、3.938→3.648、4.133→3.600、4.797→3.758 ms；
下降6.66%/7.37%/12.89%/21.65%。8候选、4租约慢1.63%，
完整分布和离群值保留。报告为
`experiments/reports/v16_prefill_restore_order_cpu_20261010.json`。
夹具使用真实因果图与请求身份处理，原生驻留为确定性模拟，
不含DMA、CUDA或端到端吞吐。本项尚未部署，V15继续完整冻结。

以下为采集期间的修订记录，最终双侧结论以上述汇总为准。

v15 predictive侧已完成：156/156 workflow completed，无error或
incomplete；采集10857.481秒，完成吞吐51.725 workflow/h，
JCT P50为3119.630秒，GPU利用率均值74.476%，输出795.773 token/s。
这是运行终态口径，任务正确性未独立评测。JOIN/tool各99/77次
ACK，FULL传输与确认首次复用分别为0.893/0.743 GB、0.338/
0.293 GB；需求handoff为15132次ACK、67.205/67.047 GB FULL，
不能作为提前预测收益。forecast观察年龄P50为137.325 ms，
平均推理28.603 ms。native侧已冷启动并正常服务，完整同版本
对照仍待结束；上述单侧结果不证明目标达成。
2026-10-10 10:50 CST核查native已完成150/156，无error/incomplete，
剩余请求仍正常获得服务；主目录及实时引擎未修改。

12:12 CST的只读工具时延审计中，exit=0命令的native/predictive
P99为4.508/4.076秒，最大96.902/115.295秒；所有自然结束命令
均低于180秒。最后pylint-6528的长尾包含同一命令64次重复、
P50约155秒、exit=31，降低600秒上限至180秒无法处理这组重复。
用户已确定后续所有实验的单条sandbox命令执行上限为180秒，
包含模型显式timeout；超过180的配置与参数统一按180执行，实际
上限写入manifest及sandbox审计。此约定取代此前“默认180、
允许显式延长”的建议，V15历史两侧仍保留600秒。最终报告中
两侧有12/16条timeout/SIGKILL候选，不能作为自然结束标签或
直接换算JCT节省；137本身不能区分超时与OOM。完整时延报告为
`experiments/reports/v15_tool_timeout_distribution_final_20261010.json`，
执行时间剔除sandbox锁等待；workflow14400秒和LLM请求600秒
是独立预算，工具上限不会改变这两项。

完成后的语义输入审计发现，固定encoder以右侧截断处理1024字符
窗口、最多256个MiniLM token，可能丢失最新正文。24014个请求
的末次客户端窗口中502个被截断；370个无工具正常stop轮次中
336个被截断，占90.81%，丢失量P50为54个MiniLM token。
这不是Qwen生成/KV token，窗口也不保证在原生EOS前被模型消费。
训练与在线使用同一截断规则，证据只说明表示存在限制，不直接
证明预测误差原因。报告为
`experiments/reports/v15_semantic_encoder_window_20261010.json`。
隔离CPU比较已完成：原V7的18650个工作快照及embedding可从固定
缓存完整恢复，structural基线重拟合的数值权重和选择损失均复现。
在最近32个词/符号上增加64维有序后缀特征，保持阶段头、encoder
和阈值，未改善条件工作头：Astropy选择集log pinball从0.146075
升到0.153486；Sphinx开发集31个请求末次快照的绝对token误差
P50从26.631升到44.179。该9-workflow开发集被反复使用，不能
称独立验证。其快照均无有效通知，无法验证通知后的H2D触发。
候选不部署，V15不参与选择。重建样本清单为
`configs/migration/child_semantic_suffix_cached_samples_20261010.json`，
比较报告为隔离目录中
`experiments/models/child_semantic_work_suffix_candidate_20261010/report.json`。
本轮仍优先完成冻结对照，再验证已提交控制成本与消费优化。

隔离下一版终态缓存诊断将节点标量记录直接序列化，省去递归
`dataclasses.asdict`。共享祖先仍保留最后一次观测，不缓存跨锚点
或跨采样的物理状态；记录字段、节点顺序、读取次数、采样频率、
不可观察与已消失锚点的处理均不变。164项runtime/observer检查
通过。同输入CPU基准固定前版`782c283`，每次处理4个24层watch，
各配置200次交替运行；1/2/8锚点的平均wall耗时分别为
2.666→0.911、3.379→1.641、6.808→5.110 ms，下降65.83%/
51.44%/24.93%，thread CPU降幅接近。除时间戳外全部输出相同，
原生读取次数相同，并核验实际导入的隔离源码路径。writer为内存
记录，该结果不包含磁盘写入、GPU执行或端到端收益。报告：
`experiments/reports/v16_terminal_scalar_serialization_cpu_20261010.json`。
本项仅在隔离分支提交，V15主目录、引擎与预测产物继续冻结。

隔离下一版准入规划省去固定前8项与轮转扫描重叠的Python检查，
保留轮转覆盖、首次观测顺序、下一次调用的实时驻留刷新及原生
读取次数，不新增持久驻留缓存。201项相关检查通过。同输入CPU
基准固定`13491b2`，保留16轮、每次两次规划、各300次迭代，
以stub原生检查器提供混合驻留。8请求均值0.562451→0.442924 ms，
下降21.25%；156请求4.160591→4.130053 ms，仅下降0.73%，
P50为3.767605→3.719760 ms，保留较大的wall离群值。
每次规划均与对应基线顺序一致，原生读取次数相同；后一次规划
新发现驻留后允许调整顺序。不能据此宣称大队列稳定加速或GPU
吞吐提升。报告为`v16_resident_scan_cpu_queue8_20261010.json`、
`v16_resident_scan_cpu_queue156_20261010.json`，仅在隔离分支提交。

v15完整schema2 JOIN审计为99节点命令、75个不同child请求。
95次在RETURN前发起，其中57次位于提前0--500 ms，提前量
中位数309.66 ms；82次ACK早于JOIN。按不同child去重后，
原生完成到客户端result的P50/P90为254.63/1074.24 ms，
JOIN到parent提交P50为25.36 ms，提交到原生到达127.72 ms，
到达到worker首次服务P50/P90为160.30/14512.15 ms；
提交到worker首次服务为352.47/14930.20 ms。各分位数不可相加，
worker区间不是CUDA kernel计时。报告为
`experiments/reports/v15_join_pipeline_first_service_v2_final_20261010.json`；
冻结侧旧报告不覆盖。后续须区分过早预测、客户端完成积压与
真正准入等待，不能全部通过延长驻留租约处理。

隔离生命周期审计新增FULL驻留分组：记录首次登记时是否持有原生
锁、末次观察到的释放原因、真实复用/未复用/未知、已知FULL字节
及后续加载关联。缺失证据保持未知，纯Mamba动作不进入FULL计数；
登记获得锁不代表一直保护到服务，租约到期也不代表复用失败。
11项审计检查通过，v15全部旧汇总字段与冻结报告一致。
174个JOIN/tool FULL动作中134个确认复用，31个未保护且后来
丢失驻留，9个获得保护但到期后未复用；另6个未保护且到期的
JOIN动作仍成功复用。9个保护后未复用动作包括3个observed-EOS
和6个estimated-work动作；前者发起时EOS已经结束15--23 ms，
后续RETURN仍需2.76--3.43秒，到达后服务等待13.3--15.0秒；
后者对应5个child，提前EOS约2.0--5.2秒，仍需优化剩余工作
低估。上述是关联证据，不能将全部未复用归为预测失败或统一
延长租约。发起到ACK期间槽位变化的保护修复已在隔离分支，
本轮缺少issue-budget遥测，不能声称可恢复全部31次未保护动作。
PREPARE共846次ACK，196次关联后续恢复；715次后续同节点/
同池D2H均有中间Host FULL驱逐，仍须减少回收后的补传成本。
报告为`experiments/reports/v15_prefetch_lifecycle_residency_final_20261010.json`，
使用`--summary-only`保留紧凑汇总；详细证据沿用冻结侧原日志，
不重复提交整份事件明细。主目录与v15配置继续冻结。

PREPARE生命周期审计进一步区分Host驱逐之前和之后的恢复关联，
保留原有latest-writer计数。14项审计检查通过，所有既有汇总
字段保持一致。v15的196次恢复命令关联全部发生在已观察到的
Host驱逐之前，共697个node/pool关联；650次未观察到恢复，
不能据此全部判作浪费。846次FULL备份均观察到后续Host驱逐，
ACK到首个发布节点驱逐的P50为47.935秒、P90为2378.784秒；
该区间截止于同节点/池下一次D2H写入，不代表完整前缀寿命。
相同source/context/epoch/node/creation身份有86组再次PREPARE，
额外141次、单组最多14次；缺失身份不参与重复分组。节点分裂
与共享前缀仍限制物理归因，不能称精确重复字节或同一等待阶段。
后续先核查这些再次备份是否对应真实压力回收机会，避免任意
延长Host驻留或按固定冷却期抑制必要补传。紧凑报告为
`experiments/reports/v15_prepare_host_lifetime_final_20261010.json`，
原始证据仍保留在冻结实验中，尚未改动运行策略。

隔离下一版将PREPARE候选记录绑定实际传输command_id，便于
将候选估计与ACK、压力回收和恢复关联，而不按共享节点或时间
猜测身份。123项runtime/审计检查通过，既有汇总不变。v15的
846次候选中，127次有直接受压字节回收潜力，719次没有；后者
选中节点的估计传输量6.457 GB，占总估计8.361 GB的77.23%。
无直接回收潜力可能是补齐祖先前缀的必要步骤，不能直接判为
无效备份，估计字节也不是DMA或实际回收字节。v15全部候选均
缺少command_id，只报告汇总，不追认其单次ACK身份。现有
`v15_prepare_host_lifetime_final_20261010.json`补充该统计，避免
复制整份报告。下一轮核查完整前缀补齐与后续消费的对应关系，
再决定筛选或合批方式；本轮配置继续冻结。10:07 CST核查native
为94/156 completed，无error/incomplete，调度器及客户端仍运行。

隔离下一版语义worker轮询复用poll监听器检查结果管道，空队列
不再进入每次新建selector的Queue.get_nowait。仍每tick检查进程
退出、启动/推理超时及结果就绪，不降低预测刷新频率，现有输入、
返回值与批次调度不变。38项相关检查通过，1项旧模型集成检查因
隔离目录无其固定artifact而跳过。另以真实spawn进程、有界队列及
确定性4条预测回复作CPU比较，固定4d6e6ea：空闲/推理在途空队列
各20000次交替轮询，平均wall由8.666/8.560微秒降至3.371/
3.283微秒，下降61.10%/61.64%。结果已就绪的400次轮询由
49.280升至51.129微秒，慢3.75%，保留该代价。输入、回复及
active状态相同；此基准不含神经推理、DMA或GPU停顿，不据此
推导JCT节省。报告为
`experiments/reports/v16_semantic_worker_poll_cpu_20261010.json`。
2026-10-10 09:42 CST核查native为77个completed、无error/
incomplete，仍在正常运行；本项仅在隔离分支提交，V15继续冻结。

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

下一版物理机会采样复用同一次闭包中已读取的Mamba祖先锚点，
不再重复遍历该祖先到root的路径；校验creation_time，每次新
调用仍重新读取实时驻留，实际传输保留原生依赖校验。344项
相关检查通过，含跨调用驻留变化、锚点版本变化及反向叶顺序。
与`ed02b21`的同输入156-workflow/24节点、每配置60次CPU比较中，
动作选择、压力候选及完整机会字段一致；祖先锚点配置的机会
采样均值下降29.47%--30.16%，缺少备份且Host有空间的PREPARE
下降37.10%，Host-only配置下降39.98%。Host满池、缺少备份的
PREPARE慢1.12%，保留该结果。普通同叶配置首次比较有一组
PREPARE均值慢10.60%，P50却快0.79%；180次复核中该组均值
快2.81%，不将变化归为确定的加速或退步。普通同叶配置复核的
采样均值变化为慢0.30%至快0.73%。两侧同等清除探测退避，
基准固定并校验实际导入路径；这些CPU结果不能证明GPU吞吐收益。
报告：`experiments/reports/v16_mamba_ancestor_capture_cpu_20261010.json`、
`experiments/reports/v16_shadow_capture_identity_cpu_20261010.json`、
`experiments/reports/v16_shadow_capture_identity_repeat_cpu_20261010.json`。
此修订只在隔离工作树，v15两侧及完整后处理继续冻结。

下一版JOIN PREPARE将压力候选的两次完整发布合并为扫描和
原生发起之后的一次发布，保留仅Mamba压力和无等待parent时的
发布。原生PREPARE不回收Host或HBM，实际传输仍校验依赖；
发布读取操作后的锁和pending状态，避免把正在传输的节点列为
可回收候选。同一次维护按context定位缓存并核对完整key，减少
每节点请求身份哈希；不同attempt/epoch仍分别检查，不跨调用
缓存存活状态。348项相关检查通过。与`8d1db37`比较，
156-workflow/24节点、每配置60次同输入CPU基准使用相同156条
真实传输种子，两侧同等清除探测退避，动作、候选发布和完整
机会字段一致。三个已备份且仍有device驻留的配置中，PREPARE
均值为15.463→9.887、14.967→10.151、13.464→7.766 ms，
下降36.07%、32.18%、42.32%；其他三个配置下降0.82%--1.57%。
首组节点查询由431508降到216228。未修改的采样/终态采样计时
差不作为该改动的加速证据；实际频率与GPU吞吐仍待验证。
报告：`experiments/reports/v16_prepare_publication_cpu_20261010.json`。
此修订只在隔离工作树，不改变v15配置、策略或模型。

2026-10-10 07:35 CST的语义局部审计有7个estimated-work首次
触发请求，均匹配此前已交付的forecast；5个提前EOS超过2秒，
1个在0--500 ms、1个在500--2000 ms。158个自然终态请求的
最后EOS前快照有符号/绝对剩余工作误差P50均约46.80 token，
触发时的有符号误差P50约-72.82 token。提前低估与末端高估
仍并存，不统一加偏置，也不据此延长全部驻留保护；完整对照
结束后再针对有Host-only目标的实际机会评价和优化。
报告：`experiments/reports/v15_semantic_trigger_causal_partial_20261010_0735.json`。

本轮局部输入审计进一步发现，5个请求的303个快照已有客户端
收尾通知，在线输入却为`notice_active=false`，涉及requests-1142、
django-11239、11095、11292、14349。五次通知发出时各有2个未完成
child，旧运行时收尾动作阶段均未接受通知。旧代码从“已成为最后一个
child的预取阶段”读取通知特征，较早通知会在child后来成为最后
成员时丢失；这与离线训练读取已发通知的口径不一致。
报告：`experiments/reports/v15_notice_input_alignment_partial_20261010.json`。
该报告按客户端因果历史和已交付输入比较，时钟使用首个census
偏移，跨文件交付不是原子观测；303个快照也不是303个独立样本，
缺少通知输入本身不证明存在可消费的Host-only机会。

隔离下一版将通知证据按child独立保存，绑定通知后下一请求的
context/epoch/request身份。预取阶段到期仍保留已发通知这一
历史特征，后续工具调用及原生工具token、上下文压缩、取消、
返回、workflow结束和镜像重置清理记录，额外模型请求不继承
旧通知。同一控制batch先完成图验证，再依事件顺序记录通知、
绑定下一请求和清理，覆盖通知与epoch前进同时到达的情况。
模型输入的通知与报告长度提示因此独立于当前H2D资格；
实际加载继续检查最后未完成child、Host-only安全输入、容量和
原生依赖。forecast日志补齐报告长度提示、正文字符数、历史工具
与模型轮数，不记录正文。相关检查364项通过、1项因隔离目录
缺少固定encoder产物跳过。六种未通知CPU配置、每组400次与
`8eac264`比较，输入及forecast一致；小幅计时差不作为性能提升
证据，实际通知路径由生命周期检查覆盖。报告：
`experiments/reports/v16_notice_input_cpu_20261010.json`。
本修订在隔离工作树提交，v15双侧及完整后处理继续冻结。

隔离下一版进一步省去无缺失FULL时的PREPARE检查点展开与路径
排序。该判断直接读取本次已捕获节点的FULL device/Host长度，
不使用跨调度轮次驻留缓存，也不改变缺失前缀或Mamba策略。
289项相关检查通过。以`9c5a399`为基线，156-workflow、32层路径、
每组180次强制探测的六种CPU配置均保留选择、发布和采样结果；
已备份device驻留配置的JOIN PREPARE均值下降2.50%--2.71%，
采样下降5.64%--6.03%，Host-only配置分别下降9.60%/6.09%。
缺少备份且Host足够的PREPARE均值慢0.0045%，其他小幅波动与
未修改的terminal计时不作为加速证据。报告：
`experiments/reports/v16_prepare_backed_step_cpu_20261010.json`。
这仅是CPU控制成本证据，不能推导GPU吞吐收益；v15继续冻结。

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
保留证据、修复后冷启动；正式结论仍要求多轮同配置对照，
新实验统一先predictive、再baseline，并报告固定顺序的局限。
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
