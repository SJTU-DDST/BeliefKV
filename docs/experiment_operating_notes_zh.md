# BeliefKV 实验注意事项

更新日期：2026-10-10。本文是当前实验的执行约束，不是新增 agent
guard、终态门禁或模型动作授权。启动前同时阅读
`docs/implementation_plan.md`；旧诊断脚本和历史计划不能覆盖当前约定。

## 当前执行状态

v13采集、外层driver、HTML导出及workspace清理已结束。已合入
后续498c0ab修订并部署SGLang增量，完整staging patch反向检查通过。
v14已从50b9179按同一156任务、108+48到达、running48、
Host200GB80:20、HBM比例0.9、模型/预测产物/prompt/seed21及预算
冷启动，HTTP/finish-chunk细分计时已启用。
本次只部署已验证修订，不重复native开发参考、不增加agent guard。
后续版本还没有GPU性能收益证据，当前目标继续保持active。

2026-10-10 02:20 CST运行快照中物理动作保持启用，语义worker无
错误，PREPARE为missing_full_prefix；JOIN/tool和需求handoff均有
ACK。读取opportunities/admission_opportunities.jsonl中最近的
admission_runtime_state，不能把该文件任意末行或runtime_audit
当作状态快照。后续代码继续在隔离工作树修改，完整driver、审计、
HTML及cleanup结束前不得向运行服务目录部署。

只读reentry完整段匹配的后续CPU优化已验证输出一致。实际RadixKey
与合成共享祖先路径的完整reentry基准中，4K/32K/96K完整匹配耗时
下降47.4%--58.8%，87项相关CPU检查通过；原生分配及驻留、DMA、
Mamba状态依赖照常校验。该优化尚未部署到v14，CPU下降不能用作
GPU吞吐结论。保存两侧源码SHA256和报告
`experiments/reports/v15_reentry_cpu_benchmark_20261010.json`，以后
不能用更新后的服务目录冒充本次冻结基线重新计算旧结果。

后续闭包观察优化只减少对象/临时分配，继续校验容量、锁及session
引用。对19650ab的40次同输入交错基准，缺失备份、Host满池及
Host-only PREPARE平均耗时下降14.4%--15.4%，机会采样下降
11.2%--12.8%；已全备份PREPARE约持平或下降3.2%。保留20次
离群值样本与复查报告，不能用P50改善掩盖均值退步。历史baseline
须同时加载其observer、physical和runtime，不得混用当前observer。
144项observer/physical及88项runtime检查通过；这不是GPU收益。
报告：`experiments/reports/v15_closure_prepare_cpu_repeat_20261010.json`。

JOIN链路审计读取同request的可选finish-chunk、LLM_END入口和HTTP
计时，历史缺失保持unknown，按child请求去重后再统计。02:53 CST
快照中EOS到finish-chunk的P50为2962ms，finish到回调94ms，
回调到LLM_RESULT0.112ms；不能把客户端积压计入成功预测提前量。
HTTP consumer pause包括解析、回调和线程调度，raw pull还可能
等待服务端生成；二者是整轮流累计量，不是EOS之后的纯CPU/网络
区间。诊断脚本扩展不改变运行中的策略或租约。先定位客户端与
JOIN到提交的开销，再验证有界驻留，不直接延长到十几秒。
报告：`experiments/reports/v14_join_pipeline_http_partial_20261010.json`。
客户端GIL采样有204个有效样本、94次失败；SDK类型转换和增量
工具JSON等栈只用于选择后续检查对象，不能声称已精确归因。

后续SDK消息遍历优化已实现于隔离工作树，对运行时标识的Chat
Completions使用已转换的wire消息经extra_body合并，最终请求
JSON必须和继承路径相同。保留tools、sampling、运行时/session/
deadline元数据及显式extra_body覆盖；未标识请求和Responses
照常转换。54项既有adapter和7项同步/异步、SSE/非流式等协议
检查通过。本机SDK/库版本、源码及最终canonical JSON SHA256
保存在`experiments/reports/v15_client_payload_cpu_20261010.json`。
合成11/67/259消息的完整请求构造均值分别2.711→0.835、
13.032→1.284、48.162→2.752ms。不可把CPU改善当作GPU吞吐
收益或宣称已消除数秒客户端积压；增量工具JSON等热点仍未解决。
该共用路径只随下一轮冻结版本部署，不修改v14的代码指纹。

后续流式优化省去SDK类型对象→dict的逐帧往返，沿用SDK解码、
错误和响应关闭，以及LangChain最终工具参数解析。运行时标识的
普通Chat同步/异步流使用新路径；headers、结构化/非流式响应、
未标识请求及自定义client沿用原资源。71项流式/请求/adapter检查
通过，包含usage、finish、错误和提前关闭。30次合成交错样本中，
67/515/395帧的同步CPU均值9.419→2.034/70.291→13.497/
63.662→18.140ms；异步9.544→2.168/69.862→16.692/
65.006→18.705ms。请求JSON和最终结果一致，记录wall/thread CPU、
版本及源码/输出SHA256。报告：
`experiments/reports/v15_client_stream_cpu_20261010.json`。
它不包含网络/GPU/callback成本，不能宣称已解决实际JOIN消费延迟；
完整driver结束后再部署到下一轮。

增量工具参数优化仅跳过无法形成对象的单帧前缀解析，保留原始
tool_call_chunks及等价invalid_tool_calls；对象前缀、非标准字段、
最终合并参数和工具执行沿用原语义。89项相关检查通过。
60次交错CPU基准中工具路径均值下降39.4%--98.4%，512帧普通正文
同步13.330→13.444 ms、异步14.747→13.533 ms。保留首轮30次
异步正文均值退步的报告，不能仅选取有利样本；复查中GC保持
启用且计入时间，单独记录暂停，不修改生产GC。异步普通正文
P50约11.366→11.414 ms。报告：
`experiments/reports/v15_tool_fragment_cpu_20261010.json`及
`experiments/reports/v15_tool_fragment_cpu_repeat_20261010.json`。
这些合成CPU结果不能代替GPU吞吐验证，仍须等待v14完整driver、
审计、HTML和cleanup退出后部署。

终态session关闭的后续版本使用独立共享线程池，最多32个worker。
RETURN立即禁止该context复用，关闭RPC在锁外执行；不复用长期
占用的workflow线程池，不让child future或parent请求等待HTTP。
workflow收尾必须等待所属关闭任务并保留同身份失败重试，完成
后才关闭audit；真实compaction仍同步关闭旧session。三种arm
使用同一harness，manifest记录关闭模式及worker上限。
HTTP200仅确认原生派发，并没有scheduler引用释放ACK；也不能
证明物理回收。新审计分别记录排队、HTTP和JOIN到parent提交的
重叠，保留旧遥测缺失为unknown。04:03 CST快照中67个去重
child请求的JOIN到提交P50为695ms，关闭HTTP重叠P50为419ms；
这不是可直接从JCT扣除的收益。225项相关检查通过、1项跳过，
9项JOIN审计检查通过。后续修订仍等待v14完整driver退出后部署。
报告：
`experiments/reports/v14_join_pipeline_session_close_partial_20261010.json`。

v13最终审计应读取独立的final报告及来源v2口径。提前JOIN/tool为
760次/11.824 GB，需求handoff为18008次/158.846 GB；旧动作报告
的PREFETCH_GPU合计不得用于预测覆盖。ACK到服务须按来源分别
报告，不能用handoff的大量样本掩盖JOIN的7.06秒中位等待。
PREPARE后续同节点D2H有中间Host驱逐时，应记录回收后补传；
有效FULL副本只补缺失段，投机Mamba PREPARE继续关闭，实际
驱逐所需的活跃Mamba保存与恢复依赖仍保留。

v13清理已移除154个归档workspace，保留2个现场及所有冻结证据。
下文v13冻结、待部署等文字属于当时的执行记录。新版本须使用新
目录与新的源码/patch指纹，不能把后续修订计入v13结果。

## v11 至 v13 调度与消费归因

新增约定：投机PREPARE只传未备份的FULL前缀，不携带Mamba。
有效Host FULL副本由原生保留并随radix分裂拆分；副本被Host
驱逐后才需再次备份。同node/pool后续D2H只能报告为关联，不能
称为已证实的覆盖，也不能据此计算重复字节或浪费量。
Mamba运行态会变化，但已有前缀检查点是版本化快照；真正压力
驱逐时需要的Mamba write-back及恢复时的状态依赖继续保留。
v11冻结3d750f1，仍允许Mamba PREPARE；FULL-only修订用于后续
冷启动，不得计入v11的性能解释。
v10补查的35066次后续同node/pool D2H均有中间Host FULL驱逐，
应分析备份回收与重复补传，不能称为覆盖有效前缀。
PREPARE选择须比较Host空闲与可复用检查点路径上的全部缺失FULL，
不能只因1-token祖先放得下就启动容量无法完成的前缀备份。已有效
备份部分与检查点后的生成输出不占新增预算；实际仍逐段补缺。
`prepare_required_checkpoint_full_tokens`记录完整缺失预算，
`prepare_checkpoint_no_host_capacity`记录单段可放下但检查点不可
完成的拒绝。该预算不是Host容量预留，不阻止原生write-back回收
冗余Host FULL，不保证消除所有重复补传。相关修订仅用于v13结束
后的冷启动版本，须同时报告有效消费和迁移机会是否减少。
后续冻结计划须记录完整缺失前缀预算、有界恢复优先准入及HTTP/
finish-chunk计时状态；不得把这些修订写回v13旧plan。

v11因空回收tracker的FULL键缺失于2026-10-09 21:34:34退出。
实现故障不能靠客户端重试继续采集：停止该轮，保留故障前遥测，
修复并提交后以新目录冷启动。回收结果允许只包含实际释放的池，
不能假定FULL/Mamba计数已初始化。FULL-only PREPARE须保留实际
FULL叶驱逐前对活跃未备份Mamba检查点的原生保存和ACK依赖。
采集客户端使用独立进程组，服务端提前退出后立即终止该组；
不要等待剩余到达批次后继续向失效服务端发送请求。
中止采集的目录缺少整体summary时，可用
`summarize_semantic_h2d_ab.py --cleanup-arm <arm> --cleanup-stopped-collection`
清理已归档workspace：核验记录中的scheduler已退出，并使用各任务
result和model.patch；不伪造整体summary。尚未归档的workspace保留，
child目录仍须有child_reports且git工作树干净才能清理。
FULL复用proof_version=2核验ACK时的分配身份和实际物化服务前缀；
cached_tokens_device/host是来源统计，不能单独用作驻留/复用条件。
v11/v12的旧口径记录保留，不能仅凭前缀覆盖就补判分配未变化，
也不能将旧口径的未复用数全部称为浪费。v12因该测量问题主动
停止，v13以修复口径冷启动，workload和容量保持一致。
离线重复恢复审计须保留合并批次中未打标签的原生操作，按池报告
与已预取FULL/Mamba的交集，并保留receipt/legacy证据级别。
同节点后续Mamba加载不能算作FULL重复恢复；批次池计数不能推导
精确节点字节。FULL复用proof版本须随报告保留，避免跨口径比较。

当前验收目标包括恢复就绪到服务、PREPARE消费及有用FULL覆盖三项，
最终看相同任务/到达表/模型/容量下相对native的完成吞吐和JCT；
H2D stream累计时间、ACK数和备份字节均不能独立证明收益。

FULL-only PREPARE后，FULL冷叶候选允许必要Mamba尚未备份；不能因此
在候选发布时提前传Mamba。实际容量短缺触发FULL回收时，先保存
仍被session引用的Mamba，ACK后重新校验节点、FULL副本、锁、引用
及DMA才释放HBM。保存失败保留有用状态，无引用状态交给原生回收。
handoff与等待parent回收均遵守这一规则，不能继续依赖已关闭的
Mamba PREPARE提供副本。该后续修订不修改正在运行的v13代码。

工具H2D候选按现有100ms观察间隔筛选，新hint和动作ACK立即触发
重查；节流不能延后在途ACK处理、ticket失效或真实enqueue校验。
CPU长期等待样例的扫描降幅不能直接外推为GPU吞吐提升。v13代码
仍冻结aa93dde，后续工作树提交在本轮driver退出后再部署。

需求handoff的驻留保护按原生free+evictable容量计算；真正H2D仍
要求已分配空闲页或实际冷回收，不能将可驱逐容量当成可直接写入
的空间。JOIN/tool投机预取仍只用free-list余量。下一批input、
decode页增长及Mamba slot先预留，同一请求多个extent只占一个
准入名额；真实NO_TOKEN按request释放handoff锁，不能只释放一个
重叠路径锁。闭包字节保守计入，3秒到期、失效和首次服务照常释放。
老化队首不能永久阻止恢复就绪请求消费：至少四次普通准入之后可
安排一次已提交、恢复就绪请求。其余普通请求仍按老化排序，拒绝
准入不消耗配额；有界越过老化队首的实际准入另计，须报告其他
workflow的等待代价。该后续修改与v13冻结版本区分。
v13已观察到同一请求ACK后76--102ms真实驻留丢失并随后原生重载；
不得将这类负证据一概解释为tensor对象变化，也不能以旧合并批次
的pool关联数推导精确重复字节。

JOIN提前量须分离原生生成结束、客户端LLM_RESULT、RETURN、parent
提交与首次服务。核对同一request/invocation/context/epoch，不把
另一轮模型请求或提前的工具reentry拼接到当前JOIN。对同一child
多个extent报告动作口径和去重请求口径，避免重复计为独立样本。
LLM_RESULT可沿既有FIFO异步交付；不得丢失create-before-submit
顺序或workflow结束的交付检查；含WORKFLOW_END的混合batch明确
等待交付，不能因含RETURN而异步放行。失败标记测量，不新增agent guard。
运行中v13尚无finish-chunk/HTTP流计时，数秒的原生EOS到客户端
结果差距不能直接归因于网络、解析或回调。下一轮同时启用已有
`--child-finish-chunk-shadow`与`--stream-http-timing-shadow`定位，
相应child stream记录也须开启；不追改本轮配置或性能归因。

工具流语义帧只需立即交付首个工具负信号，随后保留正文变化和
finish观察；纯参数chunk不应反复广播相同正文。工具负证据按
request保存至正常清理，不能因晚到的正文恢复该请求的RETURN
候选。该规则只处理预测输入和遥测，不拒绝工具、不取消child。
后续native/reactive/predictive launcher同启已有HTTP流及
finish-chunk计时；旧native开发参照的遥测配置差异须如实报告。

H2D来源审计v2将JOIN/tool、提交后的execution_handoff、来源未确认
的tagged传输及native余量分开。旧predictive_*字段曾包含所有
ACKed PREFETCH_GPU，新字段只包含JOIN/tool；受控合计使用
controlled_*，跨版本比较必须说明口径。issued source与ACK对应，
缺失来源不凭动作名补判。混合批次按receipt保持字节/池单位守恒，
时间仍属整个batch，不能按字节占比拆成动作耗时。JOIN/tool来源
只说明提前动作意图，实际提前量、首次复用与性能收益另行验证。
策略对比报告同样读取`h2d_sources`和分段`h2d_source_*`字段，
旧`transfers.PREFETCH_GPU_h2d`包含需求handoff，不能用于声称
预测覆盖。分类的batch次数可重叠，字节和池单位必须守恒；
原生迁移里程碑使用receipt拆分后的native余量。冻结旧报告保留，
修订复算写入独立报告文件。

v13 django-12273的root最后8192-token响应以length结束，重复
分析且没有完成声明；两个child正常返回、JOIN满足、deadline
未触发。保留incomplete及逐请求遥测，不能将模型自然耗尽输出
预算等同于传输故障，不能为改善完成数新增guard或追改本轮预算。
生命周期中的ACK到服务、FULL首次复用及确认字节也须按这三种
来源分开；FULL复用分母只包含携带FULL的命令。缺失旧分池ACK
字节单列，不按总字节比例补算。节点命令不是独立workflow样本。

服务遥测减少request查找开销时，必须保留overlap批次过滤/重排后
的同request匹配与token delta计量。CPU样例的微秒差距不等于GPU
服务成本或端到端吞吐；这类修订仍按冻结driver结束后冷启动执行。

PREPARE根据下一批可准入需求与运行请求页增长选择时机。等待队列
为空时不能只因缓存占满或Mamba free低就持续备份。已无备份步骤/
Host不足的context观察延后一秒，新epoch立即重查，enqueue保留
实时验证。FULL与Mamba压力候选须分别筛选；限制前8项之前排除
不属于FULL可迁出叶节点的祖先，不能因此移除原生锁或Host验证。

未加入batch的NO_TOKEN不是必然意味着后续请求均放不下。只有
PrefillAdder预算仍为CONTINUE、当前请求尚未老化时，才有限尝试
后续候选；每轮最多8次。已提交请求和预算耗尽仍结束本轮，保留
被拒请求的Mamba临时状态清理，不强行绕过真实容量限制。

逐操作pool receipt必须与整个ACK的node、pool数量和字节核对。
native无BeliefKV command的操作也记录receipt，不能授予动作信用。
关联PREPARE时使用child的published_node_ids，不能把合并batch的
全部node分配给每一个command。后续D2H覆盖须按node/pool失效旧
关联；Host恢复和实际forward复用分别报告。旧v10没有这一完整
原生receipt，115次恢复关联仅为legacy_batch_pool_presence；
35598次未观察到恢复不能直接称为浪费。

v11冷启动predictive，复用完成的v10 native作开发参考。固定108+48
到达表、running48、Host200GB80:20、HBM比例0.9、模型/预测产物、
prompt及seed21。保留新旧源码、engine patch和实现故障现场；运行
时发现实现故障先停止并修复，不能因正常自然语言RETURN或工具
命令失败新增agent终止门禁。性能正式结论沿用多轮交替对照要求。

## PREPARE 热点修订与节点身份

v10 predictive以 `e985d8c` 完成156个workflow，driver和全部导出已
结束。最新优化在 `/tmp/beliefkv-opportunity-20261009` 验证后用于
后续实验；不得改写已完成实验的源码指纹或将新修复计入旧性能。
旧 `arm_status.txt` 保留失败启动状态，本次最终状态为exit=0。

原生统一树creation_time为 `numpy.float64`。所有传入物理规划/
原语的锚点路径必须使用现有 `normalize_native_creation_time`
转换为Python float/int，保留原值，不截断或更换节点代次。
不要只修session快照而遗漏request reentry：v10的9419次handoff
选择均未实际提交，类型遗漏已在真实Host-only规划中复现。旧版
通用拒绝原因不能逐次识别是否同一缺陷；新CPU复现确认旧版0次、
新版1次提交，提交替身不代表真实DMA或ACK。
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

SIGTERM之后scheduler仍可能完成一轮收尾。runtime.close必须清空
终态缓存观察任务，writer关闭后不得再次采样；最终状态先写入再
drain，并允许重复close。v10的AttributeError发生在全部workflow
结束、Remaining requests=0之后，客户端和driver都正常退出。
保留退出堆栈，不以此认定采集中断，也不删掉完整workflow数据。
本项相关CPU回归89 passed，覆盖关闭后的scheduler迭代。

v10 predictive完成吞吐低于native14.06%，新修复尚无GPU收益证据。
PREPARE 144.536 GB与60次特定等待agent压力释放不能一一等同；
需要补查其他原生消费与未消费备份。预测FULL传输1.321 GB、
确认复用0.720 GB，ACK到服务P50为3.245秒；后续验收必须计入
恢复就绪准入、重复需求恢复、Host churn及其他workflow延迟。

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
不把HTTP关闭完成当scheduler引用释放或物理释放，不盲目D2H
死child挤占Host。

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
