# v0.5.20 调度适配边界

2026-10-14最新状态：V54在前夜23:59:09按预约退出并释放所属
资源，155 completed与1个行政删失保留于v326。完成子集JCT
少11.42%仍不是完整或因果验收；长尾的重复空输出工具调用
不能删除。V55在06:00后确认GPU空闲才冷启动，/goal继续active。

v328封存156个完整workspace，含删失现场和未跟踪内容；
2026-10-14 00:53:26逐文件比较通过后39.014→4.227GB归档，
raw的workspace副本清空，trace/结果/计划/删失仍保留。
库存与恢复命令见既有archive README；当时可用48.51GiB，
V55冷部署前重新检查容量及他人占用，当前仍未启动。

v331再归档V40–V48的72份server/opportunity日志，逐文件比较后
21.889→0.684GB，恢复指针保留；最新V49–V54/native参考不动。
末次可用65.53GiB，启动前容量检查改为60GiB。v329注册首个完整
600–900秒自动只读审计，真实身份/源码匹配后分解周转相位，
历史native缺证保持未知；夜间不启动实验或改变调度。
v330调用冻结源码的36组CPU对照证据一致，匹配list/tuple减少
32.1%–66.4%，早期分歧也有退化。v332的2165目标中2139完整
匹配、26较晚分歧/短输入，支持保留候选，仍须V55测实际服务收益。

a843e83将首次服务list/tuple前缀转换范围限制到ACK证据，利用
既有C精确比较，转换失败保持原数值语义，原生权限和复用证明
不变。新launch_telemetry为run_batch的子区间，分析时不能相加；
forward_iter、时钟域、完整请求身份及原生几何共同决定配对。
194项检查和2项子检查通过，v325重新绑定源码与不变引擎
f7e0a895/a8f4ea97。旧CPU微基准不含新观测/转换，实际扰动及
GPU/JCT效果须冷测，仍不启用新增prefill凑批/延迟/mixed chunk。

服务器每日Asia/Shanghai 00:00–06:00由其他使用者占用，23:59
开始释放我们的实验进程和容器。06:00后确认GPU空闲才冷部署V55，
不停止他人任务。V54未收尾则保留trace、结果和行政删失；不改变
guard或预算，当前/goal保持active。本时段约定覆盖旧的立即续跑
计划，运行代码仍保持冻结。

新/goal以短prefill周转和实际服务收益为当前优先项。用户最新
要求暂不启用prefilladder：原生PrefillAdder已使用且没有独立
启用开关，保留既有准入；暂缓新增凑批/延迟策略。v306确认
V53/V29延迟开关均False、最小空闲槽均None、mixed chunk均False、
prefill_decode_interval均0。单槽可准入时Adder仍可能生成单请求
prefill；新增策略应在后续解除约束后单独评估，不能手动绕过
完整依赖或48请求上限。当前先减少重复准入/前缀和候选工作，
解释随窗口变化的同分桶周转差额，再以JCT/吞吐和公平性共同验收。

最新v321第二波完整3600–3900秒服务步数350521/353757，
少0.91%，控制20.241秒；108/118个细工作/邻居配对raw完成
60.326/67.333ms，worker8.112/6.997ms。H2D逐层样本没有
配对，GPU跨度与等待未知。v319前一窗口粗配对raw仍多
10.276ms，细配对无足够支持；不能把固定worker税视为已解释根因。

开发1a9286e增加每16个prefill的独立周转观测，位置分别为
选择/准入、run_batch、WAR及队列发布、迁移规划、结果处理；
记录墙钟/本线程CPU，CUDA事件仅query，不同步。normal用
当前执行流，overlap用forward stream，GPU跨度包含依赖及
发射间隙，不等于kernel耗时。新forward_iter与原生服务记录
直接配对；不持有Req/tensor，也不改变既有调用顺序和调度规则。
144项检查及2项子检查和同派发CPU对照通过，增量2.2–4.2us/
循环，真实GPU扰动仍未知。v322核对patch a8f4ea97/树f7e0a895，
V54冻结45f7afb/6938bd10；V55按新时段在收尾或行政中断后冷测，不启用
新增prefill凑批、延迟或mixed chunk。最终同版本/JCT/质量验收仍缺。

最近V54于2026-10-13 21:33:35 CST从45f7afb冷启动，端口18459。
v308服务统计复用与v310局部性提示已部署；源码/资格哈希、
真实容量和引擎patch6938bd10不变由v312/v314核验。V53已收尾，
155 completed、1 incomplete保留。155个共同完成任务JCT
2666.636/3024.858秒、完成吞吐61.969/61.074 workflow/h只作
历史诊断，轨迹/输出量/引擎版本不同，当前/goal仍active且未验收。

最新v317完整1800–2100秒服务步数365441/347017，多5.31%，
两侧上下文均约23.8k token，新增prefill工作仍不同；控制独占
墙钟25.221秒。细工作/邻居配对只35/22短样本，raw完成
64.738/56.239ms，多8.499ms，粗配对905/815例也多8.926ms。
18个短采样GPU/CPU/本线程跨度P50为36.965/34.546/34.545ms，
依赖等待0.0868ms。不同边界的worker区间不能覆盖这组差额，
decode领先不是短prefill周转或全程目标验收。

开发ea7cff5为FULL压力发布增加候选侧索引，仅在全局叶子
超过候选8倍时使用，原发布顺序和身份/引用/在途检查不变。
299项检查与12组同输入CPU对照保持消费前缀；差距大的几何
CPU下降57.9%–85.3%，普通几何仍额外1.5%–6.2%。首版边际
几何退化61%–115%已保留v315报告及patch。8倍为保守算法
选择，真实触发频率及GPU/JCT收益未知，V54继续冻结，
不为局部CPU优化单独触发重启或启用新增prefill策略。

V54准入回调先于原生prepare_for_extend，因而旧前缀分歧计数
读到的cached_tokens_device=0不是实际HBM命中。开发修复
e4c1931在on_batch_completed首次服务后读取已填充分层信息，
同时验证请求实例、因果key及无retraction；unknown/unserved
单列。414项检查通过，观察修复不改变排序或恢复许可，留待
未来冷部署，不能用V54旧计数否定hint或热改运行代码。

v314的V54完整600–900秒decode steps为369578/380722，
少2.93%，控制独占墙钟35.162秒。105/110个细工作/邻居匹配
短请求raw完成57.845/55.452ms，多2.393ms；worker10.719/
7.094ms仍受overlap截断。59个短样本GPU/CPU/本线程跨度P50
34.598/34.219/34.217ms，依赖等待0.0865ms；native对应跨度
未知。继续消除重复候选维护/规划与服务等待，不把区间边界
改善或历史单轮JCT当同版本因果收益。独立前缀审计中的JOIN5个/
0.0828GB和tool62个/6.967GB为完成前启动且下一请求消费FULL；
68个JOIN完成边界未知，恢复后的消费长尾继续单独优化。

以下V53阶段及候选资格说明为历史证据，最新分析优先V54。

v310候选将局部性排序用的session驻留提示与实际输入reentry
检查分开：提示不比较token内容，明确input_prefix_verified=false，
独立缓存50ms。未知仍完整检查，原生匹配/分配/页与Mamba依赖
不变；正提示不生成恢复anchors，也不能作为老化队首缩短普通
准入配额的完整就绪证据。409项检查及36组匹配输入的实际CPU
int64索引对照通过，完整规划减负4.7%–36.5%；线上分歧、真实
消费及服务收益由V54冷测核验，旧分歧计数不可用，引擎patch没有变化。
v309最新3600–3900秒服务量仍少6.05%。v311细工作/邻居匹配
仅94/92例，raw完成66.841/64.401ms，worker8.943/6.572ms。
前者含结果处理，后者经过overlap截断；两者都不是完整forward
或纯kernel，也不满足全程JCT/吞吐验收。新增prefill策略继续暂缓。

V53最新完整1800–2100秒片段已有357336/347017个decode服务
步数，但上下文和prefill量不同，只作阶段诊断。短单请求原始
launch→处理完成60.276/56.797ms，同分桶worker仍1.17–1.51倍，
控制独占墙钟29.833秒。下一轮v308候选将PREPARE的D2H服务
统计按字节/形态复用；共享滚动历史收到任一方向完成样本后，
同时使H2D/D2H的正结果和未知结果失效。工具剩余窗口、Host
容量及原生页/状态依赖照常实时检查，不跨轮缓存迁移许可。
147项检查及九组同输出CPU对照通过，静态重复下降44%–51%，
频繁更新收益很小，个别无命中组约3.3%变慢。没有关键路径节省
证据，V53继续保持冻结运行；不据此启用凑批或宣布短prefill达标。

状态：2026-10-13。JOIN/tool 预测恢复、FULL-only PREPARE、
执行 handoff 和首次服务复用归因已用于开发实验。模型提供
完成/剩余工作预测，runtime 根据因果状态和真实缓存容量
安排传输；近期改动的稳定吞吐和 JCT 收益仍待验证。

v299在操作候选规划前增加实时副本存在性检查：没有Host有、
GPU缺失的FULL/Mamba才跳过详细closure，有目标或未知继续原生
checkpoint/容量/锁/完整依赖校验。详细审计不走捷径；输入为空
不构造大量逐节点摘要，仍在每次检查读取真实物理变化。662项
检查和同树CPU对照通过，空路径下降77%–91%，正路径额外
6–38us。V53已于18:55:48从02c171a冷启动，v303确认真实GPU
服务、83项字段不变、相同容量和2327次捷径执行，早期跨度样本
仍未知。v305已于19:13:51完成完整忙时窗口，decode steps仍低
11.48%，控制独占墙钟36.154秒；tool_prefetch降至6.389秒，
但55组短prefill实际GPU/CPU/本线程CPU跨度P50仍约41.75/
41.51/41.51ms，同分桶worker仍1.55–1.95倍native。线程CPU
接近墙钟，继续检查图外发射和批次周转；native对应跨度未知，
不将全部差额归因于GDN或其他writer，也不宣称吞吐已达标。
原有低频逐层观测增加线程CPU跨度，用于下一轮区分本线程发射
成本与墙钟空档；不将差额直接归为其他writer线程或可扣除JCT。

V292候选优先解决短prefill前后的串行迁移规划。单卡原生支持
overlap且执行handoff关闭时，forward及WAR barrier提交后再调用
JOIN/tool规划和PREPARE；空闲时也执行一次，nonoverlap与不支持
的adapter保持原顺序。未改变GPU页/状态依赖、引用或分配原语，
83项引擎和385项runtime检查通过；V51以冻结源码在17:35:21
截止，V52于17:43:50从3892fe1冷启动。v295确认实际树/patch、
82项不变字段和物理容量及真实GPU服务，尚无性能收益结论。

迁移后置路径计数包含idle迭代，不是GPU forward数。V297在
完整600–900秒窗口后自动分解实际服务/恢复等待；不足或停止
不伪装完成结果。V293已实测Host短路255/348次，以及110次
完整恢复份额准入。V296封存JOIN/tool提前消费FULL为
1.040/12.826GB，未知及停止后完成保留删失。V51 workspace
无损归档，raw和63 completed、45已到达未完成、48尚未到达保留。

V287证明短prefill两侧都用了breakable graph，而GDN attention
仍在图外执行。V51忙时短请求约1.98–2.23倍，54组样本GPU
依赖等待P50仅0.0863ms，GPU/CPU首末层跨度42.086/41.927ms。
原生worker区间经过overlap截断，不是完整forward；重排CPU工作
也会移动该边界。V289/V291的192步profiler在600–900秒窗口
之后采集，揭示图外发射路径但会放大时间。后续必须以实际GPU
服务和提前消费FULL共同判断重排是否有收益，而非只看worker。

V297的V52完整忙时窗口仍有11.99% decode steps差距和41.287秒
控制独占墙钟。短prefill worker约1.70–1.94倍，GPU/CPU观测
层跨度却仍约42ms；新顺序改善区间标签不等于实际forward变快。
两侧实际FlashInferGDN prefill/TritonGDN decode和breakable
后端一致。继续减少候选维护/规划本身并定位图外发射开销，
V52以18:51:35截止的81 completed、75已到达未完成封存，
当前V53保持冻结，目标active；不把本版列为吞吐已达标。

本阶段不止降低调度CPU开销，还要降低workflow关键路径上的
累计准入等待。v270对V29全部156对任务重建非重叠时间分解：
平均JCT少391.198秒，最后返回child的入队至首次worker服务
等待少194.434秒、root对应等待少22.403秒。最后child输出反增
1.50%，root输出少15.91%；这支持继续研究准入与局部性，也说明
不能用一项聚合token差或预取字节给完整JCT作因果归因。

代码边界：`CausalFrontierScheduler.admission_rank`已有最后阻塞
child/链深度分类；`plan_native_prefill`已有驻留、老化、恢复及
收尾的有界优先。当前tool_timing_only路径跳过
`_submit_local_predictions`，不能将旧length_ranks分支当作已
部署的语义剩余工作调度。v271另在显式、请求绑定的最后child
收尾候选中使用有效语义剩余工作/正报告预算排序；未知工作与
最佳队首保持当前顺序。这不等于对所有请求都已预测剩余长度。
下一步协调关键路径推进与恢复成本，避免新增逐decode全图
扫描。工具卸载、终态child回收与提前恢复应共同服务同一近期
准入计划；不要重做已有基础机制或让部分FULL恢复直接授权执行。
具体目标、正式同版本消融和开发native复用规则见执行计划开头。

v271同时修复同批通知epoch N和下一报告提交epoch N+1的漏绑定：
图先应用全批时，旧completion-intent判断会因当前epoch已前进而
未建阶段。现仅对该批新通知和确切绑定请求尝试恢复仍有效阶段，
避免旧通知在阶段清除后自行复活；后续工具或RETURN保持原有
撤销逻辑。排序只读取已有预测/输出进度，不增加模型推理或
物理检查；恢复消费优先、四次普通准入份额和老化均保持。
186项检查、1项跳过，六组原030b7df对照复现缺陷；CPU资格回执
不证明GPU收益。V49以冻结源码完成；按最新用户要求，V50已于
2026-10-13 14:20:50 CST从02fd87c冷启动，包含82b42d5与
3b66033。该轮评估组合效果，不能
单独归因于Host回收或收尾准入；所有原workload配置保持。

v274核验实际剩余报告工作排序和保留策略、handoff=false、
源码/引擎树及82项不变计划字段，记录494个真实GPU样本与
HTTP200。Host扫描调用当时为0，收益仍待观察。真实下一批
容量预算和不可观测回退交替出现，30个近期状态为10/20；
最近有效预算与最新回退分别存证，不把回退用作容量授权。
继续检查allocator中间态和机会损失，完整依赖及最终原生分配
检查保留，V50不热改。

6597555已在开发工作树修复原生分配组导致的容量不可观测：
读取未计入待释放FULL/已预留Mamba的剩余free-list，保持组和
迭代器不变，最终原生分配仍决定动作。另仅对新鲜确认完整
驻留的checkpoint和有效下一批名额，采用动态恢复消费间隔，
至少保留一次普通准入；未知/部分依赖保持旧规则。456项检查、
四组真实CPU allocator与七组原/新runtime资格通过，收益未证明。

最新V50忙时decode仍少16.82%，短prefill约2.29–2.61倍；
不能继续把差距全归因于恢复依赖。既有低频CUDA等待事件增加
CPU/GPU首末跨度，经110项检查，不额外创建事件或同步，
用于冷测发射/批次周转；跨度不是完整forward或纯kernel。
Host跳过建堆已实测17690/27418次。按用户授权已封存V50，
16:22:48截止133 completed、23未完成；将冷启同workload的V51。
所有未完成保留行政删失，V49完整
结果优先，性能目标仍active。

V284的50组短prefill实际GPU依赖等待P50为0.0889ms，worker
P50为15.2809ms，不能用恢复fence解释整个差距；旧span字段
未知。V285排除停止后完成/服务，确认截止前JOIN/tool提前消费
FULL分别1.454/14.897GB。V281将156个workspace全量比较归档，
raw和删失结果保留，下轮只采用冷部署。

v272复核V29全94,892请求：唯一child关键排队每workflow
413.311→211.632秒，多child阶段493.145→500.390秒。
predictive唯一child无收尾通知请求15425个，贡献208.456秒，
162个已通知报告仅3.177秒。下一步准入应覆盖工具返回后的
持续调查和关键路径推进，而非只扩大最终报告份额；实际控制
交付、排名和老化贡献仍需独立验证。

native旧trace成功通知301次却无stage事件，阶段保留未知。
v273补齐非queued模式的有效本地通知，不新增控制授权；
58项检查通过，queued predictive行为不变。fe7b54b只在开发
工作树，后续正式native使用，V50继续冻结。

v265–v268新增原生Host回收减负：write-back重复FULL副本回收
已经满足请求时，`FullComponent.drive_host_eviction`直接返回，
避免仍遍历所有Host叶节点并建堆。容量不足时保留原来的session
优先级、锁和逐叶回收；不改变淘汰目标或FULL/Mamba准入依赖。
四个累计计数随每秒Host采样输出，用于识别真实跳过/建堆频率。
第二波GIL样本中381/2,355涉及此路径，另有520次采样错误；
不知道其中多少已满足，不能据此断言节省16.2%墙钟。

V49关闭需求handoff后控制墙钟下降28.55%，600–900秒decode
仍低于固定native16.287%；满批连续decode同粗几何接近，短prefill
约2.25–2.43倍。继续定位CPU缓存维护和批次切换，V49完整
JCT已采集。新修复用完整canonical patch核验后在V50冷部署；
旧开发引擎不是当前补丁的精确副本，禁止直接复制它进行部署。

V29完整同版本对照已观察平均/P50/P95 JCT下降12.869%/13.408%/
11.500%，87个任务更快、69个更慢，全部156任务保留。v264复核
原汇总与SHA256；实际输出少5.970%，全程完成请求输出token/s低
3.836%，不能因此否定JCT结果或据此分离提前预取的贡献。关键路径
调度与迁移消费需以完整JCT及其他请求代价评价；忙时decode和
Python区间继续诊断成本，已停轮次不比较completed-only JCT。

V49于11:31:21 CST从69beb35发起冷启动，以`EXECUTION_HANDOFF=0`
单独检验需求恢复成本：
原生prefill按完整数据依赖批量恢复已提交请求；JOIN/tool提前预取、
PREPARE、恢复驻留和优先准入保持。v260确认真实初始化与继续启动
均保留该开关，原计划80项字段及runtime/引擎保持；收益尚未实测。
部分恢复不能授权不完整输入执行，原生逐层FULL依赖和必要Mamba
状态检查继续生效。v258封存前缀配对101个等待采样，两个歧义
保留未知；61个短prefill逐层等待P50/最大仅0.0838/0.4421ms，
对应worker P50为17.301ms，不能将主要差距归因到传输fence。
v259保留1,783个较晚GIL样本与447次错误，继续验证重复规划成本。
v262于11:36:56独立核验HTTP200、591个真实服务样本、实际
handoff=false及保留开关，80项计划字段、27项配置及容量保持；
已记录遥测无drop/writer失败。样本预留4,145 FULL token和1个
Mamba名额。源码不热改，启动不作为性能收益证据。

V48于11:18:44.461620行政停止，84 completed、72未完成，全156
到达；未完成保留删失。v261/v263截止前提前FULL消费41 JOIN/
1.060GB和164 tool/9.994GB，34个JOIN边界未知，停止后5个工具
完成/7个首次服务不作训练终态。156个workspace全量比较、审计
退出后再次比较并归档38.691→4.418GB，仅清理72个该轮已停容器。

上一轮V48于2026-10-13 10:14:57 CST从b8616d1冷启动，
canonical patch3698ce2f/actual tree6d6af094保持。v253于10:20:19
核验HTTP200、613个真实服务样本、预测器/物理动作、源码/引擎、
27项配置及池容量一致；样本下一批预留4,144 FULL token和3个
Mamba名额。冻结workload、固定V29 native开发对照和源码不热改保持。
近期准入名额约束探测、实测PREPARE预算与原生最终校验继续生效。

v257封存V48完整600–900秒：decode request steps323,764/380,722，
少14.961%，与V47基本持平。Python独占墙钟55.537秒，tool/
reentry/handoff/admission为8.377/5.696/5.287/5.437秒。
满48连续decode同粗几何接近native，短单请求prefill仍约
2.89–3.70倍；这些区间含控制/overlap，不是纯kernel耗时。
继续合并重复扫描、物理规划和切换工作，不能用CPU分支改善
代替真实服务收益，目标未达标。

v256独立前缀提前FULL消费JOIN6个/43.540MB、tool45个/3.643GB，
43.894GB handoff仍是需求恢复。完成前正FULL/启动/提交为
25/25/11和430/50/49；服务进度/容量原因交叠，82个待完成JOIN
边界未知。不同前缀不累加，新修复实际执行不等于吞吐改善。

e7b0d7a复用成功冷回收后的完整选择计划，要求最新两池、预留及
保护字节足够；b8616d1补不可观测回退。最终入队仍排空事件并
重捕获目标，partial/probe/不足仍刷新。v255绑定472项检查，
v254的48组CPU输出相同，成功分支减负84.1%–95.5%，回退最多
增加9.09us。CPU结果不证明真实回收频率或GPU收益。

0902199为同一工具调用保留首次实际恢复的预测；驻留到期且工具
仍运行时条件化生存时长，与最新预测取更长剩余时间。旧尾部无
支持则保持未知，刷新不清除已观察偏差；最新状态授权动作，
工具/revision/session变化清除证据，驻留期与执行依赖不放宽。
v248/v249仅证明一个V46调用的8次恢复/944.251MB均保护到期
而未证明消费，7次后续恢复超出生存条件化的两秒窗口。

V47于10:00:46.068105停止：80 completed、76未完成，全156到达，
未完成保留行政删失。v251/v252截止前提前FULL消费JOIN34个/
468.521MB、tool158个/10.213GB，handoff145.072GB仍算需求恢复。
停止后3个工具完成/4个首次服务不作终态训练。采样可容纳99/271
个事件中65/113未证明提前消费，不能当触发容量。事件最后ACK到
服务P90仍为11.087/17.620秒。156个workspace于10:07:05全量
比较后38.760→4.346GB归档，raw/结果/冻结计划保留。

v243封存V47完整600–900秒decode request steps为323,881/380,722，
较V46多3.75%仍少于native14.93%，Python独占墙钟55.197秒。
切换及短prefill差距继续查证；非kernel或可扣除JCT。v244保留
897个GIL栈、241次错误，closure51个叶样本，概率分位数仅1个。
v246旧前缀已由v251/v252最终截止资格替代，重叠范围不累加。
最终dispatch正FULL/启动/提交93/92/52和1,967/186/180；
tool包含3个停止后完成边界，训练按v252资格，不以ACK替代覆盖。
v242复用V46截止证据：42个可容纳JOIN缺口没有提交，26个tool
事件提前传6.007GB未证明消费；检查触发容量与重复恢复。

V48继续验证同一工具重复恢复、JOIN启动到提交缺口与首次服务
消费，并检查完整繁忙窗口；目标仍active，不能用启动证明收益。

V46于08:47:12.309023行政停止，69 completed、1 incomplete、
38已创建未完成、48未到达，后86个行政删失。既有incomplete的
root连续length终止，无guard干预完成；v240保留证据，上限不改。
108个workspace于08:50:28全量比较后20.961→2.234GB归档。
v238/v241截止前提前FULL消费为JOIN42个/1.951GB、tool150个/
11.536GB，130.613GB handoff确认复用仍算需求恢复；正FULL/
启动/提交87/83/47和1,342/181/175，缺口与吞吐继续检验。

v231封存V46完整600–900秒，decode request steps为312,178/
380,722，少18.004%，Python独占墙钟55.041秒，仍未达标。
满48请求同粗几何decode接近native，短prefill/请求切换较慢；
worker区间含控制与overlap，不能视为kernel成本或可扣除JCT。

v232独立密封前缀：JOIN/tool完成前正FULL/启动/提交为37/36/11
和762/81/77；提前且下一请求确认消费FULL为JOIN8个/181.924MB、
tool73个/6.623GB，handoff确认65.572GB单列需求恢复。
JOIN arrival到服务P90 457.583ms与12个ACK事件最后ACK到服务
P90 8.584秒分母不同。623个PREPARE ACK命令中113个关联后续
恢复，尚不证明净收益；容量与计划到提交缺口仍是执行目标。

db68f89/v233的344项检查通过，已随9e017a1冷部署V47：PREPARE只在
首个extent有完整排队和传输估计时，以两向ACK P90、排队P90
及100ms周期替代固定最大提前量恢复预算；缺证据保持回退，慢
H2D也可延长预算。此估计不保证整个checkpoint及时完成，不
授权部分ACK执行，Mamba不备份。V46未部署，实际收益未证实。

V45于07:43:41.038655行政停止，87 completed、69已创建未完成，
全156任务已到达，未完成单列行政删失。156个workspace于
07:49:31全量比较后38.815→4.057GB归档，raw/结果/计划保留。
v227/v229截止前真正提前且下一请求消费FULL为JOIN48个/1.342GB、
tool139个/8.774GB；handoff确认FULL复用134.226GB单列需求恢复。
停止后2个工具完成边界/5个首次服务不提供终态训练标签；采样
可容纳的正FULL计划114/210个中66/71未证明提前消费，不代表
触发时容量。JOIN arrival到服务P90 328.469ms与62个ACK事件
P90 8.738秒分母不同。336次后续同节点/池D2H均有此前Host驱逐，
没有证明完整副本被重复覆盖传输。

v224细化短prefill几何：小于512 token的单请求区间均值为
21.403/7.059ms，run_batch入口到结果处理70.494/53.222ms；
匹配16-token工作量、2,048-token前缀、CUDA标志及相邻阶段后
仍有2.26–3.53倍差距，4,096-token组约0.971–1.009倍。
区间仍含调度/overlap；逐批数据已保存，不能归为纯kernel慢。

7bc7fbe将JOIN PREPARE保护与实际恢复ticket对齐：报告通知
不再禁止冷WAIT_JOIN parent补FULL Host副本；有效恢复窗口、
非继续JOIN、reentry和session失效仍拒绝，实际入队仍核验原生
身份、锁、引用和在途传输。467项检查通过，Mamba PREPARE及
完整执行依赖约定不变。与c8fa8c7两项资格改动已在V46冷部署，
v225/v228绑定源码资格，V45未热改。v226的769个GIL栈/196次
错误跨第二波到达，保留
原始压缩栈，不将样本份额称为GPU stall或可移除JCT。

V45的600–900秒decode request steps为337,096/固定native
380,722，少11.459%；比V44多7.962%不构成因果加速。
Python独占墙钟54.565秒，worker decode区间34.812/32.467ms，
同粗几何单请求prefill仍约2.65–3.32倍native。含控制/overlap的
区间不是纯kernel，也不能直接扣除JCT；本地分析重叠范围保留，
固定native复用v199摘要。

v222独立密封前缀：已知JOIN/tool边界33/3,924个、未知76/3个；
dispatch完成前正FULL/启动/提交28/27/9和611/54/51。
真正提前且下一请求消费FULL为JOIN5个/54.354MB、tool47个/
3.572GB，handoff确认FULL复用46.501GB继续单列需求恢复。
7个JOIN/11个tool命令完成前保护过期，后来准入不能解释此前
过期；JOIN native arrival到服务P90 327.615ms与9个ACK事件
最后ACK到服务P90 4.021秒分母不同。前缀与命令/事件口径保持独立。

v220容量前缀：1,385个tool/24个JOIN事件预留后FULL预算为零；
19次在途尾部回收仅1次释放1,806 FULL token。部分传输只是缩短
已传部分的恢复等待，不能授权执行或把不安全页当可迁移容量。
V46已冷部署资格通过的改动：PREPARE有剩余备份时仍登记同次closure
中安全输入的已有Host副本，实际发布/回收仍校验身份、锁、引用、
在途传输和恢复保护；静态closure直达原有静态几何校验，动态状态
逐次读取。702项检查通过，v221的30组输出一致，静态四层CPU
减负约12%；v223的9组PREPARE选择/原生读取保持，部分副本在
深层登记额外4.2–23.5微秒。保留额外成本，不称GPU或回收收益。
V45未热改，V46核验源码一致；继续检验候选到实际提前复用的覆盖。

V44于06:24:32停止，34 completed、74已创建未完成、48未到达，
后122个任务保留行政删失。108个workspace于06:29:09全量tar
比较后20.932→2.249GB归档，仅清理74个该轮已停容器，raw/
结果/计划保留。v216已知完成前正FULL/启动/提交为63/62/18和
1,652/203/194，未知JOIN/tool边界47/3个。提前FULL消费为
JOIN10个/120.177MB、tool182个/10.756GB，handoff的
141.648GB确认复用仍算需求恢复。JOIN ACK到服务P90 13.700秒
不等于native arrival到服务P90 494.102ms，不累加重叠前缀。
部分ACK仍不授权执行，性能目标active。

V44于2026-10-13 05:44:21 CST从f55e136/3698ce2f冷部署
H2D统计复用和实时Tensor长度减负，actual tree6d6af094。
v207于05:48:06核验HTTP200、独立尾部1,851个真实GPU服务
样本、预测器/物理动作启用、源码/引擎、27项冻结配置及池容量
一致。FULL预留4,144 token/Mamba45份，按下一批名额动态变化。
运行源码冻结，下述V44完整繁忙窗口仍未达标，目标active。
v209修复开发重启的历史工作树核验，首个启动在创建workload前
失败，不是workflow error；68项检查通过。

v212封存V44完整600–900秒：312,235/380,722 decode request
steps，较固定native少约18%；worker decode区间37.655/
32.467ms，可见上下文22.716k/23.240k。Python独占墙钟58.511秒，
tool/reentry/admission为7.984/6.057/5.647秒，handoff独占/
含嵌套6.030/18.657秒。满48请求同粗几何decode接近native，
prefill及请求更替仍较慢；不能将worker区间视作纯kernel。
v211独立密封前缀已知JOIN/tool边界23/3,846个、未知88/2个，
完成前正FULL/启动/提交20/19/5和517/55/52。提前且下一请求
消费FULL为JOIN0、tool49个/2.461GB，41.942GB handoff确认
复用单列需求恢复。JOIN ACK到服务P90 12.565秒、native
arrival到服务P90 425ms，分母不同，不合并成准入成本。
继续检查重复驻留维护的调用位置，暂未部署新减负；实际操作
和状态变化仍校验原生身份、容量、过期及完整依赖。

已验证的下一冷部署改动仅减少工具扫描重复租约维护：无候选、
延后或等待在途传输时省略额外遍历；scheduler/事件/batch/abort
维护保持，真正规划恢复容量前刷新，锁释放失败停止物理动作。
v214/v215的406项检查及12组等输出CPU夹具通过，156上下文/
4–48租约的维护加扫描线程CPU减负1.6%–34.3%，无租约组
基本持平且保留0.35%退化。V44未热改，V45冷部署，吞吐仍待验证。
v213采样779个栈、214次错误；closure/reentry/租约失效检查
叶样本56/26/10个，不当作墙钟或GPU stall，继续完整前缀减负。

V43于05:27:59行政停止，78 completed、30已创建未完成、
48未到达，后78个任务行政删失。108个workspace于05:32:08
全量tar比较后20.973→2.501GB归档，只删30个已停容器，
raw/结果/计划保留。v206最终完成前正FULL/启动/提交为
JOIN92/86/43、tool1,483/203/198，保留14/6个未知边界。
提前且下一请求消费FULL为JOIN37个/753.193MB、tool180个/
12.127GB，handoff继续单列，重叠前缀不累加。

v204确认未提前消费的10个JOIN中9个自然RETURN少估剩余工作
57–303 token，触发到EOS约3.2–14.2秒；1个后续工具轮次的
EOS到JOIN177.49秒不能作为报告生成时间。已消费37个自然RETURN
的EOS到JOIN P50/P90为88.846/277.805ms。v208的因果残差pilot
排除同项目donor，标签仅在实际RETURN后可见，76个自然终态
末次pre-EOS请求token MAE80.936→110.818、P50 70.917→105.738，
采样2秒自然触发44→15。负面结果不部署，不扩租约；继续阶段/
内容/服务进度条件下的工作预测，工具轮次和容量/消费分别归因。
v210归档helper供审计，已完成行政脚本不得原样重新运行。

V43完整600–900秒decode request steps为331,539/固定native
380,722，少12.918%；Python独占墙钟53.979秒。v199不是因果收益。
v200沿用V42封存事件：未证明提前FULL消费的27个tool ACK/
服务事件中22个完成前过期、3个晚传、2个总结失效，10个类似
JOIN均提前过期。后来排队不能解释此前过期；缺少submitted记录
的JOIN/tool各有一个实际提前消费证据，不能等同DMA失败。
时机、完整恢复与消费仍一起优化，不统一增加预测偏移或驻留期。

V44冷部署精确字节/形态H2D统计复用，每个有效完成回执
失效且存储有界；closure读实时Tensor形状，不缓存驻留。
588项检查通过。v202的144估计组/12 closure输出一致，
Tensor线程CPU减负6.8%–10.6%，4/16次含冷查询重复组减负
68.3%–74.2%/90.6%–93.2%；保留单次/range最多4.12%/3.0%退化。
v201的769个栈和209次错误不测墙钟，估计器仅2个含调用样本，
不能称主要瓶颈。运行执行源码冻结，部分ACK仍不授权执行，目标active。

V43于2026-10-13 04:28:33 CST从50df863/3698ce2f冷启动，
actual tree 6d6af094，配置和固定V29 native保持。v197于04:34:14
核验HTTP200、尾部575个真实GPU服务样本、预测器/物理动作启用、
源码/引擎匹配、27项配置及各池容量一致。FULL预留4,145 token，
Mamba按下一批名额动态变化，该样本一份。V43已停止且完整
窗口仍未达标，CPU资格检查不作为性能收益。
V42在04:24:59停止，69 completed、39已创建未完成和48未到达，
后87个任务单列行政删失。108个workspace在04:28:01全量tar
比较后20.957→2.137GB归档，只删39个已停容器；最终审计退出
后才清理workspace，raw/计划保留，维护先于V43。见v196。
最终提前且下一请求消费FULL：JOIN37个/767.529MB、tool
153个/10.163GB；完成前正FULL/启动/提交86/83/46和
1,451/178/176，保留28/6个未知边界，handoff单列需求。
v198拆分同事件：47个JOIN ACK/服务事件的ACK到完成P90为
10.065秒，native arrival到服务P90为457.752ms。提前FULL消费
JOIN/tool的arrival到服务P90为436.449/858.707ms。分位数不相加，
不要把整个ACK间隔当准入等待；早驻留有容量代价，收益仍待验证。

v195在隔离引擎减少RadixKey当前长度和正常namespace检查的
重复调用，Host FULL索引读取当前Tensor形状；不缓存跨调用
驻留或token匹配。257项原生检查、61个子检查及45项报告检查
通过；27种CPU int64索引fixture输出一致。成功匹配减负约
4.3%–19.7%，部分不匹配仅约0.2%。新patch 3698ce2f/tree
6d6af094已在V43冷验证，V42未热改，完整FULL/Mamba/在途依赖保持，
部分ACK不授权执行，不将CPU减负当GPU/JCT收益。

v192的30秒非阻塞GIL采样为1,033个记录栈、286次错误。
selection/launch/result调用路线为340/404/164个样本；
selection中reentry/长度/前缀叶样本79/38/27。完整检查的
重复计算是下一候选；单次共享祖先复用已存在，不能描述为
缺失功能。采样不是墙钟、GPU stall或可减少JCT；保持运行
源码冻结、当前身份及完整FULL/Mamba/在途依赖实时验证。

V42完整600–900秒仍比固定V29 native少14.97% decode request
steps，可见上下文23.254k/23.240k，worker decode区间均值
36.122/32.467ms。已埋点Python独占墙钟55.787秒低于V41的
60.585秒，不同轨迹不能把差异全部归因于改动或扣作JCT。
满48同粗几何CUDA graph接近native，46/47后接prefill区间
仍长约9%–18%；部分单请求prefill后接decode同粗几何区间
为native的2.8–3.2倍。见v190；这些是包含控制/overlap的
worker区间，尚不能拆成kernel或具体函数成本。下一步优先
拆分prefill准备、恢复依赖及结果处理，并继续实际消费优化。
V41整队已耗尽skip只出现一次。V42冷部署用当前老化
FIFO前沿省略无动作handoff规划；后方新请求不能插入该前沿。
同一调用读取一次最多512个tagged身份，未尝试的恢复就绪、
存活parent交接、rearm和绑定变化保留原路径，普通准入与完整
依赖保持。347项最终检查通过，选定CPU fixture在156/512请求
下少约47%–49%；young浅树队列多0.15–0.21ms/次。v186绑定
源码，实际频率和GPU收益尚待冷验证，不改变页、ACK或allocator。

v182同封存前缀的工具最后一秒事件中位绝对误差P50/P90为
72.214/517.558ms；257个错过记录窗口的事件中只有13个记录
足够空闲容量，61个候选过早。类别和计划字节不能当唯一机会、
可实现迁移字节或oracle收益，模型与runtime职责继续分离。

上一轮V42 predictive于2026-10-13 03:27:54 CST从028bb6b/cf0243b6
冷启动，实际engine tree仍为c7c27e0d。配置和固定V29 native
保持。v188在03:32:26核验HTTP200、GC初始化、尾部520个
GPU服务样本、物理动作启用、源码/引擎匹配、27项配置和
各池容量一致。03:34:07早期快照中新skip尚未命中，v189在
03:36:50记录387次老化前沿skip、395次handoff选择，分母
不同不计算命中率，不能推断GPU收益。
新老化FIFO前沿skip仍需实测频率、控制成本、提前FULL消费及
完整繁忙窗口收益。
Mamba预留仍等于下一批请求名额，运行源码冻结，目标active。

v191为03:47:10–03:47:24封存的V42独立前缀：完成前正FULL/
启动/提交为JOIN24/23/11、tool578/54/53，保留84个JOIN和2个
tool未知边界。提前且下一请求消费FULL为JOIN6个/90.276MB、
tool50个/3.117GB；最后ACK到首次服务P90为11.263/2.653秒。
这是部分采集，不以原始数量和更长V41比较；handoff仍单列需求。

V41于03:24:35行政停止，21 completed、87已创建未完成和
48未到达，135个任务保持行政删失。108个workspace于03:27:39
全量tar比较后20.861→2.296GB归档清理，只删87个已停容器；
最终审计退出后才归档，raw/计划保留，维护先于V42。v187最终
完成前正FULL/启动/提交为JOIN47/47/18、tool1,021/151/144；
63个JOIN和3个tool边界未知。提前启动且下一请求消费FULL为
JOIN8个/82.801MB、tool134个/8.932GB，最后ACK到首次服务
P90为6.153/2.698秒。残余原生FULL Host-hit 0.832GB/0.884TB
不证明可实现预取量，需求handoff继续单列。

V40于02:46:16行政停止，62 completed、46已创建未完成和
48未到达，94个任务单列行政删失。108个workspace于02:51:17
全量tar比较后20.943→2.117GB归档清理，仅删除该轮46个
已停容器，raw/计划保留。初次清理被仍引用raw的只读审计阻止，
未删workspace；审计结束后再次全量比较才清理，维护早于V41。
v180最终完成前正FULL/启动/提交为JOIN80/79/41、
tool1,394/170/165，31个JOIN和7个tool边界未知。完成前启动且
下一请求消费FULL为JOIN34个/500.408MB、tool150个/10.422GB；
最后ACK到服务P90为9.042/15.321秒。需求handoff继续单列，
不把残余原生Host-hit或重叠前缀直接当oracle空间。

V40完整600–900秒decode request steps为327,142/380,722，
比固定V29 native少14.07%，已埋点Python独占墙钟60.013秒，
reentry/admission/tool为9.003/8.080/7.355秒。满48同几何
CUDA graph接近native，仍需减少同步控制和恢复间隙。见v177。
v176早期前缀确认提前且下一请求消费FULL为JOIN63.406MB、
tool560.599MB；95个JOIN边界未知，handoff继续单列需求恢复。
v178使用qualified报告保留路径、867个GIL样本和246次采样
错误，不能把样本比例当墙钟或可移除JCT。

V41在全部候选已尝试时停止重复handoff规划，范围和
原prefill相同，最多512个tagged身份。必须逐个重验当前请求
字段；新身份或已从attempt集合移除的parent不能被跳过。
当前running-batch观察仍更新，普通准入排序和完整依赖检查
继续执行，不改变ACK、传输页或allocator语义。v179的真实
inspector/树CPU fixture证明相同handoff动作，48请求/64层
线程成本3.017→0.115ms；真实频率与端到端收益待冷验证。
两批265/337项检查重叠，不相加。V40采集代码没有热改。

上一轮V40 predictive于01:57:38 CST从9f42c24/cf0243b6冷启动，
actual tree仍为c7c27e0d，冻结配置和固定V29 native沿用。真实
服务/容量已在02:04:16核验：HTTP200、GC初始化、尾部565个
GPU服务样本、物理动作启用，容量和27项冻结配置等于V39。
v174绑定最终源码/引擎及独立输入尾部，FULL预留样本4,145
token；Mamba预留按下一批名额动态变化，该样本为一份。
v175早期30条JOIN/6条tool等待者释放也包括需求路径，尚无
wait-prefetch实际冷页回收，不能算新修订的消费收益。
运行执行源码冻结，性能目标仍active。
V39在01:53:51行政停止，31 completed、77已创建未完成与
48未到达，后两类125个任务行政删失。108个workspace于
01:57:11全量tar比较后20.890→2.492GB归档清理，仅删除其
77个已停容器，raw/结果/计划保留，可用157.24GiB。维护早于
V40，v173及现有archive README保存回执和恢复路径。

V40冷部署修订WAIT_JOIN冷候选：child收尾通知本身不等于
parent马上执行，只有同parent实际存活的恢复ticket和节点驻留
租约继续保护。没有进入恢复窗口的等待parent可以发布已备份
FULL候选，原生操作仍重验当前身份、引用、锁、Host副本及
在途状态。FULL-backed不要求Mamba也有备份；活跃且无Host
副本的Mamba留在原生池。READY/终态parent无候选资格。
434项检查通过，v172绑定源码和审计；V39采集期间未热改。

V39完整600–900秒仍比固定V29 native少12.68% decode request
steps，已埋点Python独占墙钟57.799秒不是可减少JCT。v170早期
独立前缀确认JOIN2个/13.804MB、tool9个/250.163MB提前FULL
消费；96个JOIN边界未知。容量审计未观察到wait-prefetch实际
冷页释放，free-list可远低于输入预留，不能用池利用率代替它。
9f42c24提交并核验旧workspace归档后已启动同配置V40冷验证；
实际释放/完整恢复/提前消费和繁忙窗口性能分别报告。见v171。

上一轮 V39 于 2026-10-13 01:19:38 CST 从 aa3e7c1/cf0243b6
冷启动，actual tree 仍为 c7c27e0d，沿用冻结 workload、各池
容量和固定 V29 native。工具窗口外查询依据固定单调分布推导
最早重查时刻，历史支持回退前重查，新 hint 立即失效缓存；
不支持的分布即时查询。二分 quantile 保留原输出，选择时复用
同轮时钟和 fresh reentry observation，最终恢复/准入仍验证
当前身份、容量与完整依赖。部分 ACK 不授权执行。
472 项相关检查通过；冻结 GC 的 CPU fixture 线程成本少约
34.8%，不是 GPU/JCT 收益。v169于01:26:33核验HTTP200、
GC初始化、独立尾部556个真实GPU服务样本以及物理动作启用；
27项冻结配置与各池容量等于V38，FULL/Mamba预留仍为
4,145 token/一份。运行源码冻结、目标 active。

V38 完整 600–900 秒仍比固定 V29 native 少13.06% decode
request steps，Python独占墙钟59.102秒不可直接扣作JCT。
v162前缀提前且下一请求确认复用 FULL为JOIN21.176MB、
tool2.079GB；v166的固定偏移留出验证变差，未部署。
首个传输可早于RETURN 3–11秒，最后预测则可能晚约428ms，
两者分别评价，不以最后预测准确代替首次传输及时。
V38已在01:15:52行政停止：84 completed、72已创建未完成
单列行政删失。156 workspace在01:19:21全量比较后
38.843→4.029GB归档清理，只删除72个该轮已停容器。
raw证据及计划保留，维护早于V39，回执和恢复路径见v168。

V37 已于 00:01:59 CST 行政停止，49 completed、59 个已创建
未完成和 48 个未到达任务，后两类单列行政删失。00:04:30
完成 108 workspace 的全量比较归档，20.933→2.497 GB，
只删除该轮 59 个已停容器，raw/计划保留，结束可用约
168.71 GiB。维护先于 V38，v159 及现有 archive README
保存回执和恢复路径。

V37 完整 600–900 秒窗口仍低于固定 V29 native：decode request
steps 337,529/380,722，差 11.35%，上下文约 23.57k/23.24k。
worker decode 区间均值 35.003/32.467 ms，Python 独占墙钟
56.834 秒，不可直接扣作 JCT。v155 的早期独立前缀确认
JOIN 2 个/13.332 MB、tool 11 个/641.495 MB 提前 FULL 消费；
94 个 JOIN 边界未知，handoff 仍单列需求恢复。见 v155–v157。

v156 的容量拒绝中，原生 bounded scan 重复检查同一 set
前缀。新修订保存 FULL 叶快照的剩余扫描队列，跨调用推进；
每次最多校验 64 个非目标候选、选择 8 个，实际事务仍有界。
目标前缀排除不占校验额度，Mamba 单独短缺跳过 FULL 扫描；
新 snapshot 只在确有 FULL 缺口且旧队列耗尽时创建。
当前叶、Host backing、引用、锁、创建身份和在途检查仍由
原生操作执行，动态快照不能证明单时刻完整冷容量。

runtime 在首段 native enqueue 后、pending 和因果等待仍有效
时尝试一次冷容量衔接，目标是同一 safe input checkpoint
未提交的 FULL 尾部，不能使用包含旧输出的 closure 总量。
尾部按实时字节预算和页对齐缩小，再加 input/decode/Mamba
预留交给原生 reclaimer，整个目标 closure 排除。没有同步
DMA 等待、尾部 enqueue 或准入授权，后续沿现有 ACK 重规划；
不是连续在途维护，部分 ACK 仍须补齐原 checkpoint 才能执行。
冷候选校验仅刷新所属 context 的保护租约，减少重复全局维护。
647 runtime 与 124 原生检查通过，规范 patch cf0243b6 与
独立 tree c7c27e0d 一致，v158 保存验证。

V37 采集源保持 c48b3dd/838d2ed8 冻结，v154 回执继续保留。
新修订提交后已封存行政停止和核验 workspace 归档，并同配置
冷启动 V38 predictive，固定 V29 native；重点观察
实际扫描覆盖、尾部恢复/首次消费与繁忙窗口成本，目标 active。

V36 已于 23:08:23 CST 行政停止：39 completed，
69 个已创建与 48 个未到达任务行政删失，不能算作 agent 失败。
108 个 workspace 在 23:10:45 全量核验后归档，
20.905→2.408 GB；仅清理该轮 69 个已停容器，原始证据保留，
可用空间约 173.83 GiB。v153 保存停止/归档回执。
29eb3d2 的容量修订随后经 V37 冷启动部署，继续固定 V29 native。

V36 的完整 600–900 秒仍为 322,277/380,722 decode steps，
比固定 V29 native 少 15.35%；Python 独占墙钟 61.904 秒，
不是可扣除 JCT。V150 已知完成 JOIN 正 FULL/窗口/提交
22/22/2，工具 496/10/10；提前且下一请求确认消费 FULL 为
JOIN 12.145 MB、tool 383.304 MB。44.706 GB handoff FULL
是需求恢复；85 个 JOIN 边界未知，V148 与 V150 前缀重叠。

V151 中 192 条重复 JOIN 启动决策随后无恢复计划；
37 条成功冷页回收有 34 条未满足 FULL 加输入预留目标。
原逻辑以 16,384-token 总输入额度预留，未计入当前原生
4,096-token chunk 约束。V37 改为原生已观察的联合预算，
保留 decode/Mamba 余量和最终原生分配；已耗尽的当前 chunk
不能归零下一批预留。部分 ACK 仍不授权服务，V36 不热改。
Enum 检查的 V149 CPU 改进幅度不稳定，不作 GPU 收益结论。
512 项相关检查通过。V152 同一封存 free-list 下有 67 条所选
FULL 加新预留可容纳，另 49 条有部分额度；这些重复预算记录
不是已执行 H2D，也不证明完整 checkpoint 或实际首次消费。

V36 predictive 曾于 22:27:27 CST 从 9d2bf8e/838d2ed8
冷启动，包含工具同一扫描 P50 复用与需求/提前恢复名额分离。
22:31:42 核验 HTTP 200、2,256 个实际 GPU 服务样本及物理动作启用，
配置和 FULL/Mamba 各池容量均等于 V35。固定 V29 native，
运行源码冻结；v147 保存部署回执，启动不证明吞吐收益。

V35 已于 22:21:14 CST 行政停止，冻结源 e673af5/838d2ed8；
39 completed、69 个已创建未完成和 48 个未到达，后两类按
行政删失保留，不能统计为 agent 失败。准备同配置 V36
predictive，继续固定 V29 native，性能目标仍 active。
V35 的 108 个 workspace 在 22:25:23 CST 全量 tar 比较后
20.934→2.332 GB 无损归档，清理其 69 个已停容器；原始证据保留，
可用空间约 178.23 GiB。归档结束后再冷启动，恢复路径见 v146。

V35 完整 600–900 秒 decode steps 为 319,567/380,722，
Python 独占墙钟 55.245 秒；可见上下文接近，不能仅以轨迹
不同解释服务差距，也不能把控制墙钟直接扣作 JCT。
早期完成 JOIN 正 FULL/窗口/提前提交为 30/27/0，
工具 744/6/6，确认提前工具 FULL 266.445 MB；
80.218 GB handoff FULL 单列需求恢复。证据见 v145。

v146 封存锁生命周期显示 153 条 JOIN 名额拒绝中，152 条
只有需求恢复锁，涉及 44 个 JOIN。计划字节均在记录额度内，
但仅一条符合实际 free-list，其余不能跳过冷页回收。
修订仅在原生容量可观测时分别核算需求与提前恢复名额，
避免 demand handoff 独占满批时唯一的提前恢复位置。
两类共享实时字节预算及原生物理分配，保留输入/decode 预留；
选择、提交前与 ACK 后保护口径一致。容量不可观测仍按原合并
额度处理。403+120 项相关检查通过，V36 才验证实际消费和吞吐。

V36 减少同一扫描内工具 P50 的重复计算，不改变 CDF 或
启动额度；回收后和最终提交前仍重新判断。301 项检查通过，
窗口外的 156-context CPU fixture 为 236.278→128.941 ms，
候选检查均 9,360 次。这不是 GPU/预测精度结果，尚未部署到
V35，V36 冷启动纳入；证据为 v144_tool_scan_center_reuse_cpu_final。

V34 在 20:15:59 CST 因 opportunity writer 无法编码 NumPy bool
退出，冻结源为 0b45f4b/3e23c770。六个 completed、102 个已创建
及 48 个未到达任务故障删失；不是 GPU OOM 或 agent 自发失败。
writer 修订仅转换 NumPy 标量，保留严格非有限/数组拒绝，
119 项检查通过。V34 的早期部署证据保留，运行状态改为失败。

真实原生树复现部分 FULL 首段驻留锁阻止尾段再次分段。修订仅
放行未修改的驻留祖先锁；目标/下游与 Host-only 祖先锁、身份、
在途状态继续检查。130 项原生回归通过，新 patch 838d2ed8/
tree d8abddb5，v142 保存故障和验证。部分 FULL 不生成 Mamba
checkpoint，也不授权服务；仍在每批前回收、ACK 后重规划，
没有 DMA 在途持续回收。V34 六条 split decline 的具体条件
尚不明确，不能仅据这个回归替它们归因。

600–900 秒 complete window 的 predictive/native decode steps 为
336,482/380,722，上下文均约 23.25k；控制独占墙钟 55.894 秒，
不是可扣除 JCT。已知 JOIN 正 FULL/启动/提交为 32/27/2，
tool 566/3/3，完成边界未知者保持未知。v141 故障前审计仍显示
服务路径和触发覆盖问题。先归档故障 workspace，再保持全部
冻结参数启动 V35 predictive，继续固定 V29 native，目标未达成。

V34 的 108 个 workspace 已于 21:29:17 CST 核验归档，
20.841→2.113 GB。仅清理其 102 个停止容器，raw/结果/故障
状态保留，可用磁盘约 182.77 GiB；不与下一轮服务重叠。
归档回执为 v142_v34_workspace_archive，恢复指针保留在 raw。

最新 V33 在 18:40:59 CST 行政停止，106 completed、1 个
非法历史工具参数 error、49 个任务行政截断。下一版本
采用原生 Host-only FULL 分段：预算不足时，动作时将节点
拆为页对齐的连续 prefix 与保留原身份/Mamba checkpoint 的
suffix；保留所有共享 session refs 和 Host indices。规划
不分配、不拆树、不 DMA，最终重新捕获节点身份后才提交。
该部分 FULL 可以供后续完整恢复使用，不是新 Mamba checkpoint。

重启前旧 trace 归档已于 19:10:29 CST 完成，15 轮大日志
154.156→3.788 GB，实际磁盘回收 125.153 GiB。逐文件比较、
SHA256 与恢复命令见 v138_superseded_trace_inventory/archive；
V29 native、近期证据、训练/模型和结果均保留。该维护期间
GPU collector 已停止，不与下一轮服务重叠。

若 native 额度为零但已有实时有效的已备份冷等待 FULL
候选，模型时机通过后允许有界回收探查；之后按真实 free-list
重新规划，可能恢复部分前缀。失败重试复用观察周期，保留
完整目标排除、输入/decode 预留和驻留租约，不驱逐活跃/
共享页，不增加同步 D2H。采用具体预取缺口驱动的释放，
既有增量 FULL-only PREPARE 仍负责未备份等待对象；进一步
提前异步备份必须检查 Host/PCIe 竞争与快速返回导致的反向迁移。
新容量诊断来自实际动作扫描，默认 demand handoff 不开启。
624 runtime 与 123 引擎回归通过，最新 patch 3e23c770。
V33 的 4 个正预算首段拒绝事件在记录窗口内，但不代表
真实 RETURN、最终复用或吞吐收益；详见 v137 审计与验证。

部分恢复诊断进一步记录原 suffix/新 fragment 身份、本批外的
Host-backed FULL 和观测 Host-only Mamba。仅遍历已捕获节点，
不新增原生树扫描；closure 中的输出不能全部当下一输入需求。
旧 Mamba missing 只计 FULL 已驻留的独立恢复状态，零值不能
作为完整就绪。树 split、部分 ACK、原生依赖就绪与首次消费
分别判定；只有实际服务消费可确证复用。当前在批次前做有界
回收、ACK 后重规划余量，未实现传输在途持续回收。H2D 本身
不释放容量；尾部能否补齐、驻留成本和恢复等待由 V34 验证。
90 项相关检查通过，未宣称吞吐收益，见 v139 验证回执。

V32 已完整结束，153 completed、3 个 root length 截断。
用户约定继续保留 8192-token 单次预算和当前 harness。
完整 JOIN/tool 正 FULL 计划/窗口/提交为 151/146/47 与
1,680/37/36，提前确认 FULL 为 0.810/1.184 GB。
600–1200/1200–1800/1800–2400 秒相对固定 native 的
decode 吞吐为 -9.24%/+1.43%/-5.77%，未达性能目标。
完整审计为 v133_v32_complete_prefetch/busy 和
v134_v32_complete_development；优先追踪最新实验。

V33 于 2026-10-12 17:12:32 CST 冷启动 e0fe3f2，只跑
predictive。17:19:38 核验健康 200、7,899 个实际 GPU
服务样本及 physical_disabled=false，实际物理容量等于
V32。保持 8192-token 预算和 harness 行为，固定 native
参照不变。v134_v33_deployment_validation 保存启动回执；
17:36 的完整 JOIN 正计划/窗口/提交为 40/36/5，完整
工具为 777/8/8；确认提前 FULL 仅工具 7 个/0.478 GB，
JOIN 尚未确认。600–1200 秒 decode 吞吐比固定 native
低 13.10%，两侧 batch 约 47；reentry 独占墙钟为
22.608 秒/600 秒，不能当作可移除 JCT。最新证据为
v135_v33_early_prefetch/busy/development，性能目标未完成。

V33 加入新暴露冷祖先的容量回收：已备份 FULL
叶释放后，沿实际变成 device leaf 的父节点继续，重新
检查因果身份、共享/锁/在途状态及完整目标前缀排除。
保持 live Mamba 独立驻留，使用实际 allocator 释放量，
需求满足或检查 64 节点后停止。补充具体容量预算记录，
不重新读取 headroom，不取消原来的事件去重。102 引擎
与 470 runtime 检查通过，patch 06d491ae 与实际树一致。
后续重点为 V32 测得的 87.615 秒 reentry 检查及恢复消费
等待；322.214 秒 Python 独占墙钟不是可移除的 JCT。

后续独立修订扩大的是原生 FULL 候选的有效检查范围：
前八个未备份、有引用或在途叶节点不再遮住后面的可用
节点，扫描仍受 max_nodes=64 限制，有效候选最多八个。
最终逐节点核验、实际 allocator、目标前缀排除和 live
Mamba 保留不变。真实第九节点回归在旧版失败，108
引擎和 470 runtime 检查通过；单独 patch 943d7165 已合并
到 3e23c770，待下一冷启动部署，V33 未热改，未宣称 GPU 性能收益。

以下为已结束 V32 的冻结启动记录：当轮冻结在
8495c89，已部署 4f4567f 的满批无空请求行缓存恢复修复；
引擎 patch 维持 7b4832ef，单跑 predictive，固定使用完整
V29 native 作开发参照。客户端于 13:55:27 CST 启动；
13:57:38 确认实际 GPU 服务、健康 200 和物理动作启用。
实际池容量与 V31 一致，仍待完整预取覆盖/吞吐/JCT 验证。
V31 已于 13:50:40.618 CST 行政停止并保留全部轨迹。

最新执行证据优先于历史诊断。V132 封存的 V31 已完成
JOIN/tool 正计划、进入窗口、提交事件为 70/67/18 与
1,239/34/34，确认提前 FULL 消费为 0.682/1.538 GB。
JOIN/tool 恢复后首次服务等待 P90 仍为 11.033/13.397 秒。
其完整 600–2400 秒各窗口吞吐仍落后固定 native；两侧
加权 batch 接近 47，继续检查机会转换、准入和控制路径。
模型继续预测完成/工作，runtime 决定传输；不能把
285.854 秒已埋点独占 Python 墙钟当作可移除的 JCT。
下述预算修复已在 V32 predictive-only 冷重启部署，
保留 V31 行政截断和全部轨迹，性能目标仍未达成。

独立 followup 4f4567f 将预取额度与当下 request-row
可用性区分：满批 decode 占满行时，允许一个上下文的
缓存恢复与当前服务重叠；恢复不分配 request row。
FULL/Mamba、新输入和 decode 增长预留、实际数据依赖、
身份/锁及原生执行准入继续保持。JOIN/tool 旧版回归均
无法提交，修复后提交且未分配请求行；380 项相关检查
通过。修订未进入 V31，已进入最新 V32；CPU 回归及
启动验证不构成 GPU 性能结论。

V29 的主要预取损失发生在提交之前：同口径的 JOIN
启动/提交从 V27 的 129/92 变为 147/25，工具从 504/484
变为 17/17；已发出正 FULL 命令 48/73 条均确认复用。
下一批原生预留改变了 fit/reclaim 口径；容量与临近
EOS 的有效期/重规划可同时阻塞。旧日志缺少精确零计划
原因，不能给各实现问题编造独立损失百分比。以 V128
完整事件证据和 V131 封存前缀继续验证实际机会转化。

## 2026-09-22 的 Native reactive 数据采集通道

Qwen3.5/v0.5.20 训练 trace 可以先在 predictor 和 BeliefKV admission
均关闭的 native HiCache (`write_back` 或 `write_through`) 通道采集。
该通道不调用 BeliefKV 的物理 ledger、COMMIT 或 Join H2D ticket，
因此预测路径尚缺的跨 epoch ACK/共享 owner 证书不是采集阻塞项。
使用独立的 `frozen_native_reactive_v0520` 计划，禁止把旧
`frozen_p5_observed` 批次直接改名冒充同一策略数据。
采集脚本必须核验 `0.5.20`、精确模型路径/BF16、Host cache、
物理动作关闭、关键模型文件哈希及 server context，并记录
FULL/MAMBA 混合容量未标定；workflow 没有任意 2 小时截断。
root 在 JOIN 后可再次动态 SPAWN，后续 child/JOIN 有各自身份与
context epoch；已有局部回归，尚非目标 GPU train 数据。
原始 trace 成功不等于旧 rc1 exporter 的 `formal_training_eligible`，
不能直接作为完整 P6 训练集使用。

已冻结 train-only 计划为
`configs/migration/qwen35_native_reactive_train_plan_2026-09-22.json`。
`HICACHE_SIZE_GB` 是原生统一 cache 的**总预算**，按 FULL/MAMBA
设备池字节占比分割，默认总计 **200 decimal GB**（186.26 GiB），
不是各池 200 GB，也不是 200 GiB。启动脚本默认在 node1 上进行
NUMA CPU/内存绑定；预检用该节点 MemFree、干净的 inactive file
cache、保守折算的 active file cache 与 SReclaimable 估算可用量，
检查总池预算及额外 8 GiB 余量。MemFree 单独不足以判定失败，
估算通过也不能证明 pinned Host 内存已实际分配或 NUMA 落点正确。
本轮 200 GB 总池短 GPU 启动/单请求通过：`numastat -p` 测得 scheduler
进程 node1 约 193.9 GB、node0 约 18 MB；服务报告
`hicache_size=200`、device `max_total_num_tokens=1,645,444`。
持续负载、Host restore 和 FULL/MAMBA 分池动作仍待验收。
采集前在相同新环境启动 patched v0.5.20 服务和独立 native 遥测目录
（每批使用全新目录），例如：

```bash
# 服务端终端
RUN_DIR=/path/to/new-native-reactive-run
mkdir -p "$RUN_DIR/server"
HICACHE_SIZE_GB=200 HOST_NUMA_NODE=1 HICACHE_WRITE_POLICY=write_back \
  BELIEFKV_NATIVE_TELEMETRY_DIR="$RUN_DIR/server" \
  SGLANG_SOURCE_CHECKOUT=/home/longhao/experiment/BeliefKV/third_party/sglang-v0.5.20 \
  bash scripts/launch_qwen35_native_v0520.sh
```

```bash
# 采集端终端（与服务端相同的 RUN_DIR）
RUN_DIR=/path/to/new-native-reactive-run
/home/longhao/miniconda3/envs/beliefkv-next/bin/python \
  scripts/run_p6_collection_batch.py \
  --collection-plan configs/migration/qwen35_native_reactive_train_plan_2026-09-22.json \
  --batch-id p6-013-train-mixed-r0 \
  --native-telemetry-dir "$RUN_DIR/server" \
  --model Qwen3.5-35B-A3B \
  --expected-model-path /srv/ai/models/Qwen/Qwen3.5-35B-A3B
```

`server/` 与默认 `workloads/` 必须同属 `RUN_DIR`；目录须预先存在、
可写且无旧遥测。writer 在空闲时周期生成的零记录
`native_telemetry_status.json` 可通过采集器预检；任一遥测流已有
记录、status 已有记录或 `workloads/` 已存在则拒绝，不能通过删除
运行中服务的文件规避门禁。
未设置 `BELIEFKV_NATIVE_TELEMETRY_DIR` 的 wheel/native 服务没有此
专用遥测，不能满足 `--native-telemetry-dir` 的采集门禁。
2026-09-22 在目标 GPU 上完成的是此前 4 GiB
HiCache 服务就绪，采集器的真实 server identity/capacity 校验通过，
单次 chat 返回 HTTP 200；随后已停止临时服务。通用计划生成器
仍只生成旧 P5 策略，native train 计划由专用冻结脚本从原 split
派生并强制核对源计划 SHA-256。真实 train batch 尚未执行；
200 GB 总 Host pool 尚未完成高压/restore 验收；
运行前仍需核对 Docker image lock、日志、宿主 GPU 和工具环境。
取消的是全 workflow 的 2 小时 activation 截止，不是单次模型
调用的默认 `--request-timeout 7200` 安全超时。采集契约先提供
`raw_trace_eligible`；opt-in native scheduler 记录逐请求 submit/result、
GPU batch 中逐请求 token 变化及 scheduler/worker 服务区间，不把
共享 batch 区间冒充独立 request 的 CUDA kernel 耗时。原生 HiCache
ACK 只有方向、node IDs 与 pool token 数量，**缺少 DMA bytes 和
duration**，不可训练 PCIe 服务曲线或冒充旧 rc1 传输遥测。
native reactive P6 导出路径现有 train-only、按 head 的局部
`formal_local_training_eligible` 门禁：精确 request 对齐、token 守恒、
遥测完整性决定各 demand/action/wait 标签资格；PCIe service 标签
明确不合格，完整 `formal_training_eligible` 仍为 false。专用导出
CLI `scripts/export_native_reactive_p6_dataset.py` 要求 `run_dir`、
`--output-dir` 和 `--split-manifest`；只有局部训练资格通过才返回成功。
4 GiB GPU 单请求烟测写出唯一 submit/result 与 11 条服务样本，
未运行正式 train batch，不能将该代码路径或原始 trace 视作已有
正式数据集。目标模型 calibration/test 数据、模型训练/校准和 GPU
大 pool 高压 A/B 仍缺失。

## JOIN 与 pre-admission 协同

- 工具等待的 H2D source 仍需要同一 `WAIT_TOOL`、context/session 与
  revision；JOIN source 独立使用 JOIN ID/mode/成员及 child
  `remaining_to_return_ms`。ALL=max、ANY=min 的边际分位数只是提示，
  不是联合覆盖保证。JOIN H2D 现在采用有界三阶段 ticket：
  (1) calibrated JOIN P10 进入 1s 观察窗口时，尝试一个 action-local
  CPU-backed node；(2) child 模型输出唯一 `ChildCompletion` 后发送
  provisional 意图，只有 ALL 的最后待返回 child 或 ANY 的有效成员
  可提前触发；该信号不证明工具成功或 JOIN 满足；(3) 确认
  RETURN/JOIN_SATISFIED 后，parent READY 且 context/epoch/session、
  JOIN ID/mode/成员不变时，短期 reentry ticket 可继续预取。最多
  两个 node，一笔 H2D ACK 之前不发下一笔；native ACK 排空和
  overlap 安全检查后派发，不占 running slot，也不修改 native 准入。
  取消、超时或身份变化清除 ticket；晚到的 provisional 信号只丢弃。
  1s 是有界试验窗口，尚非目标模型标定的 latest-start/收益判据；
  若旧 epoch 动作的 ACK 晚于 context advance，账本仍 fail closed，
  不能给新 epoch 记账，跨 epoch 接力仍需完善。
- `--beliefkv-admission-prefetch` 必须与 admission predictor 和
  action-eligible 的 pinned artifact 同时存在。仅对被选中的 READY
  session request 启动有界 lease：先核验 native running slot，
  再抓 action-local CPU-backed node，单 node 原生 H2D 入队；
  ACK 之前 request 留在 waiting。每笔最多两个 node，失败回归
  native 准入。`init_next_round_input` 和 `PrefillAdder.add_one_req`
  仍负责最终 prefix match、FULL/MAMBA 物理门禁和 running 成员资格。
- 此设计不是把等待 H2D 的 request 直接标为 running；也不是对全部
  waiting 请求无差别预取。当前没有经 Qwen3.5 校准的动作 artifact，
  因此线上只执行原本 admission-only，JOIN 物理派发默认关闭，
  物理收益未验证。
这不是完整 P6 预测调度。
目标配置为 Qwen3.5-35B-A3B BF16、单机、统一 FULL/MAMBA tree、HiCache
cache mode。其他 cache backend、TP/PP、disaggregation 和 speculative
仍需独立验收。

## 固定 v0.5.20 的原生能力

以固定 checkout `94602c9c` 的源码和测试为准，不把后续版本或 RFC
当成已有功能：

- FULL KV 与 MAMBA state 属于同一 request/context 的恢复依赖，但并非
  相同的资源单位：FULL 用 token/page，MAMBA 用 slot/sidecar，device
  共享底层字节预算，Host 为独立 pool。对 context 的物理动作须同时
  验收所有必需 pool 的容量、锁、generation 与 ACK，只有整个闭包可用
  才允许发放 reentry credit；成本、容量、传输字节和失败原因仍按 pool
  分开记账。原生 `HostPoolGroup` 对分池 Host 分配支持失败回滚；
  BeliefKV 的跨池 ownership/动作证书尚未完成，不能把单个 FULL
  命中视为整个 context 可恢复。
- `UnifiedTreeCore` 原生按 token/page 匹配、分裂 radix node 和共享前缀；
  BeliefKV 不重建第二棵共享前缀树，也不能把共享祖先算作一个 agent
  的独占 reclaim 字节。
- `--enable-session-radix-cache` 可给 FULL/MAMBA 维护 session 引用；
  原生驱逐会优先保留这些节点，但这是软优先级而不是 pin。
  必须明确传递 session ID、处理 session generation，并在 context
  结束时关闭 session，否则既不能保证工具等待期保活，又可能造成 Host 污染。
  agent 客户端现有显式 opt-in 的 context/epoch session 桥：工具等待期
  复用同一 session，RETURN/CANCEL 关闭，WORKFLOW_END 清理遗漏 child，
  epoch 变化先关闭旧引用；默认没有接入实验 runner，不能用它宣称
  工具 KV 已在新环境保活。关闭接口同步调用原生 `/close_session`，
  失败保留可重试引用，不隐式继续使用过期 session。
- v0.5.20 **没有**根据 agent 的 `TOOL_START/RETURN` 自动保活或恢复 KV。
  已有 `_prefetch_kvcache` 只针对 storage -> Host；`init_load_back`
  在请求准入时从 Host -> GPU。二者都不等于 BeliefKV 预测的、请求
  reentry 前的 `PREFETCH_GPU`。工具时间/依赖预测和 PREPARE/PREFETCH
  动作时机仍由 BeliefKV 决策。

## 保留的决策

- RCCG 与 Action frontier 保有 agent 因果关系、JOIN 依赖和可见请求资格；
  FrontierBelief 输出 future demand、reentry 和 latest-start 的概率分布。
- P5/P6 JointPlan 保有吞吐优先的执行顺序、beneficiary/victim 选择，以及
  `PREPARE_HOST`、`COMMIT_CPU`、`PREFETCH_GPU` 的价值和 deadline 判断。
  预测决策必须绑定 request/context/epoch、目标 node closure 和生成版本。
- 准入计划只对 *tagged* 请求下发语义顺序/许可。同一安全点中，native
  `policy.calc_priority` 先形成队列；BeliefKV 在 tagged 位置重排有身份
  校验的候选，未授权或过期的 tagged 请求跳过并继续扫描下一候选，
  untagged 请求保持其原生相对位置。当前 staging 已接入
  `--enable-beliefkv-admission` 的在线 plan producer。可选
  `--beliefkv-event-socket-path` 在安全点接收 agent 因果事件，复用
  `CausalFrontierScheduler` 排列与 live context/epoch 匹配的 ready
  invocation；没有事件或没有匹配时沿用 native order。另有可选、
  经 artifact 门禁的 next-output demand 排序（见下节），不授权物理动作。
  事件应用失败
  废弃 RCCG mirror，退回 native order；普通事件不深拷贝整个 RCCG。
  waiting queue 中由事件确认已终止的 tagged 请求在安全点被精确移除，
  通知 tokenizer 并释放原生 handle。首 512 个 tagged 候选受有界
  排序，其余请求保留在队列，等待下轮原生调度机会。
  `compile_native_prefill_plan` 将语义排序在安全点绑定到
  request/context/epoch/attempt 和 native session ID/generation；
  session 在授权与应用之间变化时拒绝 tagged 候选，原生请求不受影响。

## 预测 demand 的 admission 边界

- staging scheduler 将 `--beliefkv-admission-predictor-path` 与
  `--beliefkv-admission-predictor-sha256` 交给 `NativeAdmissionRuntime`；
  预测模式还要求 event socket。启动时必须核对 artifact 文件的精确
  SHA-256、`calibration_status=calibrated`、`online_eligible=true`，
  并核对其中 semantic source contract 的模型 `config.json` 及
  声明的权重索引、tokenizer 文件哈希和
  `sglang_version=0.5.20`。旧 Qwen3-Coder/rc1 的 schema-v5 或
  development-only artifact 不满足新模型准入资格；当前迁移目录
  尚无经 Qwen3.5 标定且可在线使用的 predictor artifact。
- 启用合格 artifact 后，单独的 spawn 进程加载 FrontierBelief，
  每批最多 8 个当前可见且 ready 的 tagged 候选；最多一批在途，
  等待批只保留最新值，安全点仅非阻塞 poll。只使用有 support、
  非 OOD 的 `next_output_tokens` P50；失败禁用本地 worker。
  返回值必须在短有效期内与 live request/context/epoch/attempt、
  session ID/generation 及 invocation revision 一致；未齐备 hint
  时不根据局部预测或 request ID 排序。
- 预测值只在**同一已知因果排序类且该类所有候选均有有效 hint**时
  用于短输出优先的 tagged admission 排序。未配置合格 artifact 时，
  仍只有 observed-causal/native 顺序；即使启用预测，SGLang
  `PrefillAdder` 仍独占实际 FULL/MAMBA 资源验收，不能把 hint
  当容量证明或 `PREPARE_HOST/PREFETCH_GPU` 授权。
- 该 worker 现在也接受 `TOOL_START` 后的工具剩余时间请求，产出
  P10/P50/P90；须有已完成请求留下的 context/session 绑定、live
  `WAIT_TOOL` 和模型中非 OOD 且有 support 的 tool-wait head。
  返回时重验 workflow/context/epoch、invocation revision、模型
  SHA 和有效期；无效或过期结果不用于调度。工具推断可覆盖等待中的
  admission 任务，而新的 admission 请求不能抹掉已经在途的工具
  结果。scheduler idle poller 监听 worker 结果 fd，以便结果到达时
  被唤醒。当前工具预测只为只读候选检查提供信号，不派发物理命令；
  admission-only 晋升并不证明 tool-wait head 的动作时机准确率。
- `scripts/promote_qwen35_admission_predictor.py` 提供有证据才晋升的
  admission-only 路径：检查同一 Qwen3.5/v0.5.20 模型/运行时哈希、
  相互隔离的 train/calibration/test_id 数据、冻结拆分以及重放
  的 demand 可用率、误差和区间覆盖。输出仍明确标记
  `predictive_action_eligible=false`；目前缺少目标数据与正式 artifact，
  脚本不能将旧模型数据转换成新模型的预测动作资格。

## 交还给上游的机制

- 不移植 rc1 的 `HiRadixCache.write_backup_batch`、`load_back(force=...)`、
  `_evict_backuped`、逐 token allocator reserve 或 `TreeNode.value` 映射。
  新版 cache 管理 FULL token 与 MAMBA slot，两者共享设备字节 buffer；
  老的单值 `kv_bytes_per_token` ticket 无法当成物理容量证书。
- `PrefillAdder.add_one_req` 负责原生 tile/chunk、FULL+MAMBA 资源门禁和
  原生 Host load-back。BeliefKV 的候选选择在 `Req.init_next_round_input`
  与 `add_one_req` *之前*完成，拒绝候选不会在该 prefill 循环中触发
  prefix match 或 H2D；上游入队阶段独立的 storage-prefetch 路径不在
  此保证内，需要关闭或单独验证。
  native 接受后，BeliefKV 只观察结果，不事后否决已提交的请求。
- `UnifiedRadixCache`、`HostPoolGroup` 和 `HybridCacheController` 保有
  node split、共享 ownership、锁、Host/device 分配、实际 DMA 与 ACK。
  不实现第二套通用 PCIe 队列，也不把 router KV events 当完整 ownership 流。
  物理命令只允许调用经认证的 action-local native API。

## 尚需实现的物理契约

1. 将 native pool 的共享字节占用、FULL/token、MAMBA/slot，以及 node
   本地锁和 split 后的 closure 映射进 BeliefKV 的有界 physical view。
   当前只读 observer 已暴露 FULL/MAMBA 的 session 引用计数及叶
   标记数，但没有原子 revision、全部共享 owner 或独占 reclaim 证明，
   不能据此授权迁移；session 引用也不等同于锁。
   原生 session tracker 新增按 session ID/generation 有界查询 FULL/
   MAMBA leaf anchor 的接口，runtime 可在请求已完成但仍处于
   `WAIT_TOOL` 时保留 context/session 绑定并查询 anchor；换 session、
   终止、取消或 epoch 变化使查询失效。此查询依赖实际启用
   session radix cache；默认 runner 尚未开启该能力。leaf anchor
   是候选来源，不是节点独占所有权或 DMA 授权。
   工具预测结果通过后，安全点最多读取一个 session 的 FULL/MAMBA
   祖先闭包（默认上限 64 个 node），计算尚未在 Host 备份的 FULL
   token/MAMBA node；有 pending transfer、祖先缺失、anchor generation
   不一致时放弃检查。候选仅保留只读快照，随后过期或 session 失效
   即清理；没有原子 ownership/可迁移字节证明，不能视为 PREPARE
   证书。调用动作前必须重新读取 live closure。
   本地根优先步骤选择器从 FULL session leaf 的祖先路径选一个
   尚未备份的 node；每次只建议部分 KV，不把 MAMBA-exclusive leaf
   当作 FULL 路径的来源。工具 hint、上下文和 closure 在动作安全点
   重新读取。步骤只是给 native 的候选 ID，不是可执行动作。
2. staging native D2H/H2D 入口现可传递可选 command ID，controller
   在真正提交子操作时记录 anchor、pool token counts 和总 bytes；
   合并 ACK 同步且 tree finish 后，只有全部子 receipt 与 ACK
   的 node IDs、pool totals 和总 bytes 对账，才输出 tagged
   `child_commits`。未标记 native 子操作只参加合并账目；
   split 后 node ID 属于原子操作的 published closure，不是新 command。
   `load()` 成功或 ACK 数量都不是 DMA 完成/动作证书。分配失败、
   未提交的 H2D 或对账不符均不得获得子 receipt。
3. staging scheduler 在 admission 启用时已将
   `UnifiedRadixCache.on_hicache_transfer_commit` 接到
   `NativeAdmissionRuntime.on_native_transfer_commit`；runtime 持有
   有界 `PhysicalTransactionLedger`。`register_physical_action`
   只接受与 live causal context/epoch 和可见非终止请求匹配的预期
   动作，**仅注册对账期望，不派发迁移**。ledger 按预期 node
   closure、方向、context epoch/session generation 和冻结的 FULL/MAMBA 每 token
   字节数核对 native child receipt，只有整笔 children 对账才记录
   completion；过期、未知/重复、缺失或不匹配的 receipt 不给部分
   credit，对账错误会禁用后续物理 credit。PREPARE 的精确 pool
   token 数量及含 sidecar 的 DMA 字节数现可从 native 子操作在入队
   前取得并登记；账本输入仍不能自行证明共享 owner、独占 reclaim、
   锁与原子 generation。scheduler 尚未启用预测动作策略。
4. PREPARE 与 PREFETCH 的单 node native 入队前预留和 ACK 对账已接线，
   但尚未取得动作策略资格，因此不开放在线预测性派发。
   只有真实 beneficiary deficit 才能授权 COMMIT；
   SELECTIVE RETRACTION 需另行验证 overlap drain/TP 一致性。
   下一步仍需为 COMMIT 的全部共享 owner/独占 reclaim 与 predictive
   action 建立独立资格，覆盖 child/parent reentry 及收益与 latest-start
   判断。异常发生在 native
   已接收操作之后时不能释放可能被 DMA 持有的 Host slot，只能等待
   ACK/到期并报告失败。已接线的账本不等于在线 P6 物理调度。

staging 的 `UnifiedRadixCache.prepare_host_shadow` 已有单 node 原语：
仅在 cache mode、`write_through`、session radix cache 启用，且
FULL leaf 的 generation/创建代数和祖先 Host 连续性仍成立时，
向原生 controller 提交带 command ID 的 D2H。Host 空间不足则拒绝，
不得为影子备份驱逐其他 Host KV；MAMBA-only 子操作若同处 FULL leaf
祖先路径可提交。该方法**保留 GPU KV**，返回 `issued` 而非完成凭证。
controller 现在有可选的入队前 callback：在主/辅助 Host slot 确定后、
DMA 入队前将真实 `CacheOperation` 交给 runtime，冻结单 child 的
FULL/MAMBA token 数量与含 derived sidecar 的 DMA 字节数并注册账本；
拒绝时释放本次分配的 slot，不排队。runtime 的
`issue_shadow_backup_step` 在同一安全点重新检查 live `WAIT_TOOL`、
session、node，并调用该 callback；native 明确拒绝入队时才撤销账本
预留，返回的 command ID 是 issued 标识，不是 ACK 或正收益凭证。
**scheduler 尚未调用该事务入口**：当前 Qwen3.5 预测 artifact 只有
admission-only 资格，不能据此授权 PREPARE；还需要收益/时序和
物理资格，再在 safe point 接入动作选择。
更没有 beneficiary-bound COMMIT_CPU 或提前 H2D 执行闭环。

staging 的 `UnifiedRadixCache.prefetch_gpu_session_node` 新增单 node H2D
入口：重验 FULL session leaf、context 对应的 node creation、祖先在
GPU、无 pending transfer，并要求 native FULL load-back 只涉及该
node；MAMBA-only（零 FULL token）仍允许。与响应式
`load_back` 不同，此入口遇到 FULL 或辅助设备池不足时直接退出，
不能通过 `evict_for_alloc` 或 side-pool reclaim 抢占其他请求。
controller 仅在实际设备索引及 sidecar 解析完毕、操作入队前调用
BeliefKV callback，拒绝时只释放本次分配的设备槽；runtime 以原生
pool 几何和真实操作内容登记 `PREFETCH_GPU` 预期，native 明确未
入队才撤销预留。**回调成功不等于 H2D ACK，也不等于首次服务**。
该入口可为有动作资格的 JOIN ticket 和 admission lease 读取一个
FULL session leaf 路径；尚不提供动作收益、shared owner/reclaim
证明、完整 execution-KV JointPlan 或跨 epoch ACK 接力。scheduler
只在安全点尝试 JOIN ticket 派发，默认动作门禁仍保持关闭。

新环境默认仍加载预安装 wheel；使用 staging 源码启动时必须显式传
`SGLANG_SOURCE_CHECKOUT` 给 `scripts/launch_qwen35_native_v0520.sh`，
脚本校验实际加载路径和固定 checkout。带源码启动但不启用 BeliefKV
也只验证原生禁用路径，不能代替物理动作 gate。
staging 单测同样需将 checkout 的 `python/` 和 BeliefKV 根目录显式
加入 `PYTHONPATH`；直接运行环境中的 `pytest` 可能导入 wheel，
报缺少新增 session anchor 方法，并不代表 staging 源码失败。
`scripts/launch_qwen35_native_v0520.sh` 默认仍为 `write_back` 且
不开 session radix cache；显式设置 `HICACHE_WRITE_POLICY=write_through`
与 `ENABLE_SESSION_RADIX_CACHE=1`（还需 `HICACHE_SIZE_GB>0`）才满足
单 node 原语的运行前提。该配置本身不会启用 BeliefKV 物理动作，
也不自动启用 agent-native-session 桥；从 write_back 切换后的服务率
与 Host 驱逐行为须独立测量，不能复用旧 A/B 基线。

安全点顺序固定为 native chunk abort -> HiCache ACK 排空 ->
BeliefKV 状态同步/决策 -> native prefill admission -> GPU batch。
现阶段只有单机单 rank、非 disaggregation、无 speculative、unified cache
的 admission-only 模式可显式开启。**目前已验证的排序来自 observed
causal frontier；代码中的异步 demand worker 需要新 artifact 门禁，
尚不能作为已验证的 Qwen3.5 在线预测结果；没有预测 D2H/H2D。**旧 BF16
GPU/transfer 服务率不能作为 Qwen3.5 FULL/MAMBA 的物理容量或
latest-start 证书。下一步采集隔离的新模型数据，生成/校准
action-eligible artifact 并验证 stale/OOD 回退和 admission GPU gate；
仍须完成动作价值/时序、物理 owner/closure、COMMIT 与完整 JointPlan、
跨 epoch ACK 接力及 D2H/H2D 归因，重做容量/服务率标定与
冻结 baseline/P6 高压 A/B。不能用新版原生 smoke 替代这些 gate，
也不能宣称 P6 完成。

## 收尾阶段与 JOIN 预取实验开关

`native_dynamic_1to4` child 完成工具工作后可调用
`announce_completion_intent(estimated_final_report_tokens=...)`，再自然生成
最终报告。回调只传 child/JOIN/context epoch 和有界长度估计，不传报告
正文；无结构化终态的自然语言返回仍被接受。只有 `JOIN_ALL` 最后一个
未完成 child 的通知才进入短期候选。下一次 LLM 提交绑定请求身份，
再次调用工具、epoch 变化、取消、RETURN 或超时均撤销候选。

兼容的 Qwen3.5 admission 启动现在默认设置
`BELIEFKV_ENABLE_FINAL_STAGE_PREFETCH=1`：需同时开启
`--enable-beliefkv-admission`、`--beliefkv-event-socket-path`、
patched SGLang 源码和 session radix cache；不要求 admission
predictor artifact。独立构造 runtime 时仍默认关闭。
今后的 reactive/predictive 配对均启用相同的 child 收尾工具、
admission、事件 socket 与阶段开关；reactive 臂只关闭学习型
predictor/额外预测动作。阶段通知、收尾优先级及阶段 H2D 是
两臂共同机制，只有额外学习型动作的边际收益归入 predictive。
这类 reactive 是 stage-enabled admission reactive，不是旧的纯
native reactive：后者不构造 admission runtime，仅设置环境变量
不会产生阶段 H2D。旧 confirmed JOIN canary 与此开关互斥，
不作为正式 A/B 臂。实验清单应记录实际开关值和动作类型。
收尾请求在 native
waiting 队列中最多提升一次排序，随后需有四次普通准入才能再次提升；
不抢占运行中的请求、不保证持续 decode 服务。阶段通知**不直接派发**
H2D：安全点观察最终请求的实际输出 token 增长和服务速率，结合有界
报告长度估计和至少三次 native 响应式 H2D ACK 的实际字节/提交至 ACK
时延样本，滚动计算 latest
start。若缺 Host 副本、FULL/Mamba 设备空位、服务证据或测量样本，
则不发起预测传输；即使历史 H2D 慢，预期驻留也限制在约 2 秒内。
每次操作仍经 session/node/容量重验和原生 ACK 账本，一个阶段最多
派发两个 node。首次模型调用和样本不足时会自然退回响应式路径，
不能将此代码路径当作真实吞吐收益或时间预测精度的实验结论。

通知覆盖不靠阻断最终回复：每个 child 的提示词要求完成工具工作后
调用一次 `announce_completion_intent`，短报告估计也接受。若模型
直接自然返回，adapter 在 child RETURN 前写入独立的
`terminal_fallback` 事件，覆盖率审计可见，但该事件太晚且
`beliefkv_child_completion_intent=false`，**不得**拿来签发提前 H2D。
主动通知后再次使用工具会撤销旧阶段；未再次通知就返回时同样
记录 terminal fallback。模型是否主动遵守通知仍须实测，兜底
事件绝不计作“有效早期通知”。
通知工具的请求和随后的最终请求分属相邻 context epoch。若服务端先
观察到最终请求，只允许同一 child、同一未完成 JOIN、恰好相邻 epoch
且已登记当前真实请求的 stage 重绑定；更旧的通知继续失效。
服务端记录 `child_final_stage_accepted`，须分别统计 child 工具
通知、trace stage 与服务端接受，不能用任何一个代替另一个。
无门禁时模型可能直接自然返回；这类 child 只有终态兜底，无法
保证得到可用于提前 H2D 的窗口。

训练侧将通知后的最终请求分成请求提交前、提交至首次 GPU 服务、
首次服务至结果、结果至 RETURN；以首次服务作为剩余时间标签
起点仅适用于此后更新的预测。首次服务前的预取仍需要另估
notice-to-first-service 窗口；首次服务后也可能存在交错的无服务
间隙。为跨负载泛化，另保留按 scheduler/worker 服务区间累计的
剩余工作标签和无服务墙钟间隙，不能用训练标签里的未来服务进度
作为在线输入。客户端单调时钟与服务器墙钟不能直接相减，
跨时钟组合的首次服务到 RETURN 仅报告可验证下界。
通知到首次服务的跨时钟组合也只是下界；其遗漏客户端请求
提交到服务端接收的网络间隙，不能当成准确的 H2D 提前量。
