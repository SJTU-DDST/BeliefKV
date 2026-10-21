# 动态多 Agent 的系统层研究筛选与最新 Trace 证据

2026-10-21 用户重新要求论证当前三个机制及 OSDI/EuroSys 定位，见
[最新设计复核](design_review_osdi_eurosys_20261021_zh.md)。以下记录仍保留
各自的历史证据和撤下结论，不作为自动恢复全部旧方向的指令。

**2026-10-20 整理状态（北京时间）。**方向探索已按用户要求暂停。
本文保留此前候选、否定证据和观测边界；下文的“下一步”“goal active”
及旧实验安排均属于历史记录，不构成恢复探索或启动实验的指令。
当前工程状态和实验合同以 [architecture_status_zh.md](../architecture_status_zh.md)
和 [implementation_plan.md](../implementation_plan.md) 为准。

61 个一次性研究脚本及 50 个配套测试已在核对归档后从 scripts/tests
移除。本文中的旧脚本名称继续作为历史来源身份保留；需要复核时按
[整理记录](../archive/research_exploration_20261020.md)查找压缩包和依赖，
不能将旧路径视为当前可运行入口。尚未形成通过完整论证的新主机制。

**历史研究摘要（截至 2026-10-18）。**

4.134/V1643–V1645补自然原始日志：完整root+4 child与配对DB按
消息ID匹配90请求，2未链接完整响应/1 null保留。四Explore为另一模型，
仍是一轮分叉；最后回执到parent API提交119.689秒不当GPU排队，其后
120.852秒API区间另计。尾部新用户请求/中断不当单任务完整JCT。新增
Scepsy多模型联合GPU分配强对照，直接异构角色分配不立项；API时钟不当
物理成本。5项CPU及8来源/90身份核对通过，无runtime/任务/配置/GPU变化。
转查有显式动态依赖与运行事件的自然执行，主机制未选，/goal仍active。

4.133/V1637–V1642普查固定公开库739份自然trace：根请求59204、
child733，19份显式分支共70 child，其中4个async_launched。独立固定
生成器显示标记取parent工具回执时间、child请求另行归零且不递归构建
child链接；不能据显式深度1或replay统一等待推原始DAG/重叠。62对观测
声明模型不同；8对同模型的条件代理hash LCP为0，不当原生KV结论。
739路径/6分片/43留存blob与源核对通过，7项扫描器CPU测试已通过。
不采用合成回放证明新问题；转查保留真实输入/绝对时间/依赖的自然记录。
撤下候选区别继续跳过，无runtime/任务/配置/GPU变化，/goal仍active。

4.132/V1635–V1636筛选真实token的层/专家级异速：QLLM已有专家队列/
部分状态保存/优先抢占，AMoE已有层队列异步重批，BatchGen已有sequence
coroutine及模块重组/迁移。原文/部署范围分列，不将这些直接变体立项。
既有worker中位decode11.345ms、首dispatch等待1238.721ms只是不同人口
尺度，不当可省时间或JCT界。8项身份/绑定/人口/blob核对，无新测试/
runtime/任务/配置/GPU变化；转向寻找强组合之后的实际成本，/goal仍active。

4.131/V1633–V1634将调用间隙接上精确输入/实际Host回收：spawn后
Host FULL计数量2.421GB，工具593.447GB；匹配相邻旧输入的回收区间中，
cache边界缺口仅20 token，不当全程重算上界。统一年龄/多保留parent
直接变体下调。冻结源码还确认：llm_submit时间为入队，cache字段在首次
服务采集，旧“登记Host”字段须按此解释，不能作入队在线特征。7项CPU、
48来源/分区谓词通过；无GPU/runtime/任务/配置变化，研究/goal仍active。

4.130/V1629–V1632补入KVTether消息生命周期/共享状态/MRU强对照。
V79b闭合39516个同invocation再请求间隔；固定1547017对象对后，仅换
计龄锚点有4.311%严格排序反转；“更老但后续需求更晚”的对象中85.714%
此前请求spawn。它是事后调用排序，不是物理KV驱逐或收益，不把阶段换
MRU/LRU立为新机制。7项CPU与28来源/分区谓词通过，无raw重扫或GPU；
下一步查强阶段/图感知对照后的真实容量损失，完整研究/goal保持active。

4.129/V1628闭合状态保留的源合同：native streaming session交接同一
KV记录并保留请求映射行，保存状态不等于释放准入容量；不据局部路径宣称
抢占组合bug或性能收益。Pie已有显式KV句柄/共享与队列，资源过载实现
边界另列，和Agentix一起作强对照，不为直接状态保存变体立项。8份冻结
blob核对相同，作者PDF与文本已归档。撤下候选的区别继续跳过，无GPU、
runtime/任务/配置变化；没有新主机制，完整研究/goal保持active。

4.128/V1627核对短输入等待的在途请求：18054个所选路径/登记零Host/
uncached≤512请求，92.14%的等待对应47–48个已dispatch未完成请求；
最终输出>512仅占全请求7.90%，却占这组在途配对暴露37.31%。76.91%暴露
来自所选路径请求，合格任务分组模式近似；均不当物理槽或抢占收益。
冻结native已有priority preemption，Agentix已有程序抢占及GPU候选超额保留。
Speculative Actions/TomasuLLM已覆盖直接跨工具推测，不恢复共享workspace线。
4项CPU含24几何、25分区谓词与4份SHA通过，无raw重扫/runtime/任务/配置/GPU
变化；未选完整主机制，/goal保持active。

4.127/V1625–V1626重建156份封存client源、40718调用和201 JOIN的观测
调用轮次路径。首服排队/其余模型区间/选中工具为29.84%/64.73%/4.76%；
77.285%的路径排队对应登记Host hit为零，不证明服务时驻留。仅去首服排队、
固定其余时长时7/201 JOIN换控制前驱，不当可实现加速或oracle界，也不证明
剩余失衡全为固有工作。8项CPU、624项路径检查及7份SHA通过；两份冻结源
确认索引复制不是整段KV，native已可移交session record。未改runtime/任务/
配置或启GPU；仍无通过完整验收的主机制，/goal保持active。

4.126/V1624补入Stream2LLM、HEAR及Semantic Parallelism强对照：
流式prefill/LCP失效、跨层提示协议和专家亲和性不能直接立项；分离式prefill/
多卡通信结果不外推本地单卡。固定AIPerf/AgentX源码确认哈希合成prompt与
部分未来信息参与离线链重建，不作语义/质量或在线已知依赖证据。未下载
corpus或运行代码；既有40718请求/44331657输入的3项一致性核对、13份SHA
通过。未改runtime/任务/配置或启GPU；没有保留主机制，完整/goal保持active。

4.125/V1623跳过已撤下候选的区别问题，另筛选workflow资源计量。
VTC已有按client汇总与自定义请求成本，DLPM已有prefix-aware公平调度；
FairCache作者仓库已有多资源/共享成本设计，冻结queue确认client计量与
prefix credits，不将局部源码当完整设计验证。把root作为client是合理
强对照适配，不单独立项。Agentix关键路径service不等于总资源账单。
没有新增性能证据、测试或trace重扫；未改runtime/任务/配置或启GPU，
主机制仍未选，完整/goal保持active。

4.124/V1622核查取消与观测成本：532普通invocation均记录RETURN，
40718请求没有跨过owner/workflow终态，native abort为0，取消不选主线。
重读3.581GB audit及7118个writer快照，确认40–50分钟归因记录增加且样本
处理更贵；相同埋点配置不等于相同观测工作量。14637个样本实测CPU5.033秒
不是全轮CPU或JCT，不乘64外推。3项CPU和冻结源核验通过，未改runtime/
任务/配置或启GPU；主机制仍未选，完整/goal保持active。

4.123/V1621筛查传输期间的回收：区分整批ACK回收、逐块Host完成回收、
gather完成回收。相对第二种，仅提前到gather的额外在途容量受staging
容量约束；正常FULL staged路径的一对缓冲为条件上限1.25MiB，不作延迟
上界。Range已有completion-owned banks，LayerKV已有分层分配/卸载。
5项CPU及14种有限几何、42408状态的闭式计数核验通过，6份源码匹配冻结tree；
不选这些直接primitive为主机制。未改runtime/任务/配置或启GPU，
完整/goal保持active。

4.122/V1620检查未登记需求的必要性：40–50分钟恢复高峰前，20–30/
30–40分钟已登记请求均值已为71.781/95.044，对应H2D为0/0.421GB；
高峰均值119.918、等待JOIN组60.201，不是此前完全看不到压力。
CONCUR已含工具等待状态与准入保护，普通反馈不能直接立项。派生重放
40718请求、532普通family、201 JOIN，60项独立窗口积分/计数和11份hash
核验通过；没有物理容量、策略收益或因果证明。未改runtime/任务/配置或
启GPU，主机制仍未选，完整/goal保持active。

4.121/V1619补齐付费验证预算的CPU筛选及5份强对照原文。48×9×8的
既有首层路由中，width32/budget64的简单路径贪心为331个额外位置，整数
规划证得最优342；其他5个付费设置未证明最优，不据此排除联合空间。
EcoSpec已有训练路由预测器，D-cut/ECHO已有跨请求预算，ASPIRE/DScale已有
混合执行或ragged graph机制；不以这些直接组合立项。3项CPU检查含24个
穷举对照通过，8个真实首层几何设置完成；不是接受率、GPU或workflow收益。
未改runtime/任务/配置或启GPU，主机制仍未选，完整/goal保持active。

4.120/V1618继续遵守跳过已撤下候选差异讨论的决定。另固定公开GPU工具
事故及KREX原文：计时区域互斥与跨命令显存保留须分别核查，但不能由此
直接恢复旧显存借还主线。现有隔离、换页、弹性KV及合理组合尚未被排除；
事故也未给出对象级生命周期或动态父子依赖的额外成本。8处原文与3份旧
来源hash核验完成，只做CPU来源筛选，不作性能验证。未执行下载的脚本、
修改运行时/任务/配置或启动GPU，主机制仍未选，/goal保持active。

4.119/V1615–V1617继续筛选计算侧：并发48的容量证据仍受4.74约束，
不重复用低聚合占用推导64/96可行。LayerSkip/PipeDec/FASER已有分层状态
复用、增量树计算或frontier执行，单独保留未完成验证不立项。SpecFit原文
明确route atlas预先获取在请求计时外，而conversion/selector已经计时；
在线预测器训练未完成，强对照须保留该信息边界。2项CPU与17个有限策略点
验证两层信息反例，不是性能结果；
仍无主机制，未改runtime/任务/配置或启GPU，/goal保持active。

4.118/V1613–V1614筛选动态共享布局：40718个完成请求的路径探针全部可用，
40048个普通请求属于532个context。观测到98个跨普通context共享锚点，全部
跨workflow出现，未观测到workflow独占的多agent共享锚点；不是不存在未记录
共享的证明。已知前缀逐请求计数占输入20.3404%，不是物理读或性能比例。
6项CPU及52165次成员关系独立复核通过；补入ChunkAttention，并重核Hydragen/
FlashInfer/GraniKV。直接动态共享布局不选主机制，未改runtime/任务/配置或启
GPU；完整研究目标保持active。

4.117/V1609–V1612更正JOIN成本归因：201次join_satisfied均缺invocation，
通过先前join_wait找到全部后继；旧审计漏记201项，40718请求其余元数据
不变。JOIN后继占未命中输入0.7528%、登记Host命中FULL0.3960%；普通工具
后续接占Host命中FULL99.2399%。这些不是可省时间。新增GraniKV、
RelayCaching、Sutradhara原文强对照，不将粒度、输出KV转接或已知prefix
提前prefill直接立为新主线。13项CPU通过，未改runtime/任务/配置或启GPU；
继续从主要工具续接路径找具体执行/状态访问代价，OSDI机制仍未选定。

4.116/V1607–V1608完成目标Qwen3.5首层真实权重CPU筛选。48个受控任务
前缀中，独立零新增tile方案和联合方案均保留16个额外位置；独立方案已达到
该固定几何问题的上界，未显示联合收益。432个位置的logit/top8及48份输入
hash独立复核通过。首层局部机会不作完整推测验证、原生路由或GPU收益；
不推进以填充padding为主的实现。未改runtime/配置/任务或启动GPU，
完整研究目标继续active，OSDI主机制仍未选定。

4.115/V1605–V1606筛选MoE计算侧预算：FASER已有层内token提前退出且
报告MoE适配，SpecFit已有专家增量成本的草稿子树选择，不能作为本项目新
primitive。CPU反例表明剩余padding不可跨专家/层直接合并；按候选内容停止
后独立重抽bonus会使二元均匀目标变为3/4、1/4。同位置随机数耦合的3528
个有限精确案例均恢复目标分布，但不是新采样算法或原生实现验证。
仅保留跨请求、按实际MoE边际成本分配完整验证前缀的待证伪问题；缺实际
路由/延迟及强对照残差，未选实现候选。3项CPU检查通过，无GPU或运行时/
配置/任务改变，完整研究目标继续active。

最新4.114/V1600–V1604定位到首轮委派已发生变化：V75两侧156个root初始
语义指纹、记录输入摘要、原生输入长度和输出上限全匹配，但140项委派描述、
其中20项角色组合不同；首轮工具名/数量仍全相同。不能据此认定batch数值
或KV错误为原因。V79b中93.57%调用请求工作工具或spawn，覆盖83.44%原生
生成token，整次调用按工具阶段启用稳定执行仍覆盖大部分工作，不立项。
MarginGate/UNISON补入直接相关工作，分别限定token验证与session驻留范围。
7项CPU检查、627个封存前缀及468项首轮决定/原生契约复现通过；未改配置、
runtime、任务或启GPU，完整OSDI候选仍未选定，/goal保持active。

最新4.113/V1597–V1599用2篇完整QASPER论文、各3个自然问题，筛选共享KV
联合选集。0.5B CPU模型在99%观测分组注意力权重约束下，1-token页相对
简单max/mean聚合仅再缩小2.65%–2.98%并集，却增加3.48%–4.22%逻辑读取；
16-token页增量仅0.53%–1.08%。不是99%准确率、真实HBM流量或动态fork
实测。该直接贪心变体不进实现队列；普通多query强对照继续保留，不能据
此否定所有联合算法。5项CPU检查、32个策略结果和输入复现核对通过，
未改runtime/任务/配置或启GPU，完整OSDI候选仍未选定，/goal保持active。

最新4.112/V1596按用户要求跳过已撤下候选的区别讨论。另筛选同机工具与
Host KV的DRAM竞争：V1391只有200.050GB池容量和单时点驻留，缺工具RSS、
系统内存压力及故障时序，暂不立项。新增MARS v2强对照，已覆盖CPU工具
压力与GPU/KV压力联合控制；不将普通跨资源准入重新作为新机制。未修改
runtime、配置或任务，未执行新基准或启动GPU；/goal继续active。

最新4.110–4.111/V1591–V1595完成真实留存历史片段与冷参考筛选。0.5B CPU
模型改用4个不同workflow的2048-token片段后，计参考的公平单目标组仍少
13.095%/7.165%，但冷参考也需传输时单目标比独立压缩多约74%–85%字节，
示例需4–7个等成本目标摊销。14400次张量恢复逐位一致，不是Qwen3.5服务收益。
真实V79b的1432次额外跨agent正文复现中，995次先前观察者均已终态，437次
仍有未终态先前者；这不是HBM驻留或实际输入消费证明。保留可压缩性观察，
将当前负载上的残差恢复主机制撤出实施队列，不接入CPU codec或启动GPU。
DeltaLog已有状态更新日志/checkpoint关键机制，同样不重新立项。新增10项
CPU检查与原8项codec检查通过，/goal保持active，完整OSDI候选仍未选定。
4.107–4.108的Host扫描与page_size判断保持：1是合法native默认，不是
extra_buffer硬限制；逻辑页与DMA区间分别验证，不直接改参数。

最新4.106/V1578–V1579拆开传输不对称与测量资格：D2H/H2D字节仅3.279倍，
批次却24.386倍，stream时间17.579倍。纯FULL D2H占297.544秒/67.25%；
冻结源码的正常staged分支在page_size=1时逐K/V页仅10KiB，低于128KiB
批拷贝阈值，仍缺内部调用实测。候选为合并连续物理写回、异步回收与必要的
增量FULL提前写回；通用引擎修复与workflow增益分开验证。最后五项均是有效
系统测量，仅因guard介入排除于原生无干预子集，其中django-11265已解题；
更正此前“不合格”泛称，全部到达和资源历史保留。完成CPU审计与公开源码
筛选，BrowseComp-Plus多researcher候选尚未运行，未改运行时或启动GPU。

最新4.105/V1576–V1577进一步区分生成与恢复控制：204个相邻decode片段中，
选批全落在前一decode worker记录内；完整47→48正Host/零Host组的提交
间隔P50为159.120/58.706ms，当前prefill worker仅7.260/8.635ms。不能
把82.946%记录占比当kernel分解；跨workflow提交耦合成立，CUDA暴露量未知。
8项CPU检查通过，通用异步写回仍是强工程对照，未改运行时或启GPU。

最新4.104/V1575先汇总native真实时间成本：原生请求平均8.509秒，派发前
2.932秒；40–50分钟出现恢复/写回慢路径，70分钟后并行度下降。最后五个
workflow全属原native-agent JCT排除集合，仍是有效系统测量，见4.106。仅CPU汇总/
画图，模型服务、反复上下文处理和迁移干扰是成本对象，尚不据此选择机制。

最新4.103/V1574直接筛选分叉/汇合与压缩问题：当前376个child首请求均短于
此前root普通请求，输入P50为5530.5/8930 token；结合独立task上下文源码，
不能当成继承长parent历史的KV fork。不是零共享证明，首请求有效缓存P50
仍为3584 token。新增AgentKV、在线压缩研究、PolyKV、KVComm强对照，保留
“不同消费者压缩集合与物理共享”作待证伪问题；固定集合共享页已可达到并集
下界，不能把引用计数本身当创新。未选新机制、改harness或启GPU。

最新4.102/V1571–V1573按源事件前缀冻结历史信号：当前回收时普通再访问信息
覆盖重复区间609.918GB/99.830%，但此时D2H可能已付出；上一次回收之前
已有同类信息的相邻重叠区间为544.229GB/89.078%，不回填后续访问或较大
区间。这是信息覆盖，不是可省字节/命中率/策略回放。16项CPU通过。
不把这批重复FULL周转单独立为workflow主创新；先要求强通用历史策略与
已有workflow方法仍失效的具体案例。GPU停止，整体研究/性能目标未完成。

最新4.101/V1568–V1570将重复区间子集与真实状态丢失分开：7884个重复身份
的最大区间合计66.759GB、观测保留包络峰值66.116GB，但不证明全部工作集
可放入160GB Host。冻结源码与启动Git tree逐字节一致；按7067个快照区间
保守配对，至少565.370GB回收只释放Host副本、有效FULL仍在HBM。不能把
Host驱逐量当历史丢失或可减JCT。18项新CPU通过，GPU停止，主机制未选。

最新4.100/V1565–V1567补齐精确前缀区间的重复回收：FULL累计下界610.957GB，
其中99.689%的区间量只有一个已归因的再访问context，但不证明私有所有权。
已匹配最新区间587.589GB中重算代理仅0.218GB；全部回收量55.785%仍因索引
过期/未匹配而未知。不能把重复回收、未读源下界和可省字节相加，也不以此
实施parent保留机制。22项CPU通过、真实计数闭合，GPU继续停止，主机制未选。

最新4.99/V1564补齐FULL Host物理对账：初始为空，完成D2H 72699039槽，
驱逐64888979槽，最终保留7810060槽，残差为零；全部完成H2D为29851444槽。
因此至少35037535槽、717.569GB的已回收副本不能全部对应不同的完成恢复源。
这是具体成本对象，既不是已识别的无用副本，也不是可避免传输/JCT或动态
fork/join的新颖性证明。FULL-only D2H stream累计297.544秒不能直接扣JCT。
异速仍不立项；GPU停止，未修改运行时代码或重启实验，主机制仍待筛选。

最新4.98按用户问题进一步收紧异速路线：比较对象须允许细粒度暂缓、相同的
关键请求推测能力，以及暂缓时保留KV；异速须证明在关键路径与有效吞吐之间
提供额外的执行收益，目前尚无此证据，不作为核心方向继续实施。
KV研究回到阻塞但仍有后继的上下文、共享页的真实使用者、fork/join的工作集
切换与上下文变换四类影响。PagedAttention、原生session引用、SAGA、
Lachesis、EfficientAgent、TokenDance等继续作强对照；问题存在不等于新颖性。
本轮只有分析/文档更新，没有新运行时代码、测试或GPU实验。

最新4.97/V1559–V1563进一步核对候选与强组合：保留AdaServe作者PDF及
TAPER v1原始HTML/hash，后者已有可替换utility接口，不能只比固定宽度。
原175个双成员JOIN中，两边最后普通调用的decode span重叠仅7组；
原正确完成任务103组中为5组。下调“给sole更多草稿”作为独立主创新的
优先级，也不据此建设多分支终段加速器；广义依赖执行仍是问题，未证明无效。
15项最终CPU通过，原40718请求和全部质量/执行统计复现。前两条问题线继续，
共享状态线仍撤下；没有新GPU/引擎实现，具体机制仍未选。

最新4.96/V1554–V1558关联40718个请求的提交时依赖、原生首次dispatch与
25848个成员服务分区。11719个sole中2883来自原本单成员JOIN，8836在
sibling终态后提交。正确完成任务中4337个普通sole请求来自86个workflow，
输出P50为78 token、decode worker暴露P50为2.205秒，生成对象并非只存在于
失败轨迹；但高等待与后段低等待阶段差异明显。11项最终CPU检查通过，
尚无草稿接受率、强对照反例或推测提速证据，不据此实现新引擎或启动GPU。
共享状态线继续撤下，前两条研究及4.95候选继续，主机制未选。

当前决策（2026-10-17，4.95）：按用户要求放弃“共享环境交错导致验证失效、
重复调查/额外推理”第三条研究线，不再补做相关工具正文、文件版本和并发写入
审计。已收集产物保留为历史证据，不解释为冲突或收益。保留系统放大的分支
失衡、动态分叉的跨资源反馈两条问题线；新增待筛选的系统设计候选是动态依赖
下的异速批执行，研究怎样改变关键续接的实际推进速度及批内执行形态。
TAPER、AdaServe、DAS、Pie及它们与Agentix等的合理组合均是必要对照；
单独加优先级、设置不同草稿长度或启用推测解码不构成新意。尚未选定主机制。
GPU继续停止，未修改冻结运行时、harness、模型或workload。
以下4.94及更早“保留三条”的文字是当时记录，由本段及4.95覆盖。

更新：2026-10-17。当前以完整V79b为最新trace开展OSDI目标的CPU研究；
V81已停止、GPU已释放，无自动接续。混合状态Host准入及三个旧机制均未
恢复为主线。最新4.81按用户要求回到动态subagent并发workflow的实际差异，
以真实JOIN成员划分分支等待；4.78–4.80保留为此前工程/表示筛选。
独立官方CPU质量评分已结束并独立复核，见4.82；不再列作运行中。
新主机制、质量匹配性能与论文差异均尚未验收。

最新4.83/V1500–V1503继续三条问题线的CPU取证：25848个成员请求全部完成
服务区间划分，单剩成员阶段首次服务前等待占累计原生请求时长32.586%；
最后成员的观测工作量也约为先返回成员的2倍。另104/175组在双成员仍活跃时
使用显式写工具，28组两方都写。等待、任务工作差异及共享状态都需继续拆分，
没有将这些比例当作收益、冲突或在线slack。新增9项CPU检查通过，GPU仍停止。

最新4.84/V1505–V1509补齐第二条反馈假设及第三条已有能力/可识别性核对。
38687个完整工具循环中，1秒计数未显示普遍过度聚集；100ms同伴返回与排队
的全程关联在同阶段内多消失或反转。9366个已关联execute循环中，184个
压缩后首请求占tool-ready→提交累计间隔96.563%，不能当客户端空等。
Atomix v2与固定AgentFork README纳入强对照，工具参数hash不足以识别文件冲突；
新增7项CPU通过，三条方向均保留、GPU继续停止，主机制尚未选定。

最新4.85/V1511–V1513完成空结果成本归因：552个stop与23个length后继对均
唯一关联既有EmptyReasoningRecovery日志；首轮原生输出分别占全轮0.636%/
2.515%，不是速度收益。移除这些首轮的描述计数后，慢/快成员工作比仍约2倍。
等semantic digest但native输入均多2 token，不能视为相同前向。新增4项CPU
通过，未改thinking/重试/guard/上限或GPU；后续回到在线JOIN状态与执行组合。

最新4.86/V1514–V1517完成提交时JOIN状态与真实batch关联：40718请求全部闭合，
184635个decode批同时包含提交时sole/parallel标签；4744个保留decode集合的
短prefill片段，其中间请求提交时为parallel、同伴包含sole。这是已观测组合，
不是可安全延迟或混批收益证明。161个请求在client未收结果时变为唯一成员，
不等于GPU正在decode。6项CPU通过；Niyama动态chunk/QoS及Cortex阶段池/
workflow slack作为已有能力，三条问题线继续保留，GPU及运行配置未改。

最新4.87/V1518–V1524沿同一JOIN验证共同服务与原生排队状态。双成员都未返回的
完整请求中，较晚成员61.851%的decode worker暴露时间与sibling同批；不同批的
393768条记录中306577条为sibling已登记、未获首次worker，其余87191条没有
已登记请求，未见已获首服的sibling被移出同批。175组双方平均首服等待之比P50
仅1.008；全生命周期约2倍工作量不能直接证明前半段受系统不公平拖慢。
下一步聚焦反复准入等待的具体原因，仍须超过程序调度/混批强对照；17项CPU
通过，三条问题线、GPU停止与最终目标未验收状态不变。

最新4.88/V1525–V1530补齐40718个请求的首次dispatch与生命周期。冻结埋点将
worker起点设为max(dispatch,上一批完成)，不能等同真实GPU开始或纯队列。
单剩成员8836个完整请求的登记→首次dispatch P50为1.424秒，dispatch→串行
起点P50为35.937ms；等待主体仍在dispatch前。按请求累计加权，该组63.926%的
dispatch前等待发生在已有48个已dispatch未完成请求时，47–48合计89.861%。
这不是精确槽/容量或可减JCT证明。最终18项CPU通过，旧观测及源码快照保留，
三条方向和强对照继续，GPU/运行时/workload未改，主机制与最终目标仍未验收。

最新4.94/V1546–V1549补齐强对照输入/实现边界。固定TokenCake作者仓库
1901ebf并验证6份源码Git blob；其实现已有图/进度评分、JOIN优先级继承、
动态保留及cache亲和，不能省略。SAGA v2明示动态图重推断及新任务估计限制，
不等于不支持动态；Agentix和CONCUR边界也保留。全40718请求按提交前事件
重放，28731有本地JOIN、11719为sole，670个summary；未来关键路径/剩余工作
不回填，信息应平等提供给强对照。7项CPU通过，GPU/运行时未改，仍未选新机制。

## 1. 本次调整

最新决策覆盖下文历史候选：用户明确否定“根据真实后继token前缀决定混合
状态Host写入/保留”作为主线。撤下该候选，不继续为Mamba checkpoint设计
专用控制器。4.61的模型通用迁移阻塞及跨请求干扰保留为待证伪现象，
当前按4.64开放研究筛选；不预设一次异步工程修复就有足够新颖性，
也不恢复更早撤下的三个机制。
V80未启动的自动接续已取消，V1442保留证据；V79b已完成，V81已停止。

最新用户约束：重点是 serving/runtime 系统，目标投稿系统领域会议。当前目标是
在相同 agent 任务和行为下，通过系统执行、物理缓存/状态和请求处理取得收益。
SPAWN/JOIN、工具开始/结束及会话续接可以提供系统信息，但不能靠改变模型决策、
摘要内容、输出上限或有效任务量证明系统性能。应用层工程修复应匹配放进各对照臂。

V1133 的 qwen_prefix 摘要原型保留在独立 worktree，默认关闭。它对 68 个历史
root 首次摘要证明了实际输入 token 前缀和 checkpoint 位置条件，不证明物理驻留、
cache hit、任务质量或 GPU/JCT 收益。262 项 CPU 检查通过、1 项 Docker 检查未运行。
这个原型降为工程对照，暂不安排 GPU；下文摘要格式/证据反馈方向是此前的候选分析，
不再是已选择的论文主线。当前也不承诺另一套未经证实的三机制。

撤下“动态执行准入、完整状态预算、父子容量交接”作为既定研究主线的地位。
FULL 有余量而 Mamba 缺槽首先是池配置/容量问题，不能把它包装成新研究问题。
“减少无法立即消费的状态”未被证明是具有足够规模的独立问题，不继续据此扩展控制器。
既有实现、CPU 验证和研究草稿保留为历史候选。

V74 在等待 V73 时已取消，未启动任何 GPU 实验；冻结计划、候选、引擎和 helper
均保留。V73 两侧现已结束且进程退出，源码、引擎和原始结果继续冻结。取消凭据：
`experiments/reports/v74_cancelled_before_gpu_20261016.json`。

总目标仍是匹配资源、有效任务和质量下优于 native 的平均 workflow JCT 与有效
完成吞吐。V75/V76/V77/V78b和V79b均已收集结束，V79b官方质量评分也已
结束并完成4.82复核。V77关闭回收与V78b原生顺序均是已完成的历史对照，不能继续列为
待运行。核心论文机制尚未选定，不把工程减负视为新贡献。

研究范围进一步明确：harness 提供已有的 SPAWN/JOIN、工具和会话身份事件，
主实现及待验证机制放在 serving/runtime 的执行、数据布局、缓存和传输路径。
新设计必须指出实际少做了哪些计算、搬运或同步，以及多 agent 工作流信息
为何比普通请求信息更有用；普通工程修复与 workflow 信息的额外收益分别验收。

## 2. 数据与口径

最新完整trace是V79b，输入/执行复核见4.63、4.67和4.78；历史比较保留
完整V78b/V77/V76及V75 native。V79b独立官方CPU评分已结束，原始156个
终态、145项JCT资格、11项guard标签和收集exit1全部保留。早期“当前运行”
段落只保留其当时观察，不替代最新状态。
V76全156项原始duration均值1974.421s，native为1665.374s；前者156 completed，
后者153 completed/3 incomplete，guard和质量差异保留，不能改称质量匹配JCT。
下列V73数据继续作为已完成成本分析的历史依据，不代替最新运行的性能结论。

主证据是完整 V73 predictive 的 156 个 workflow，154 completed、2 incomplete；
任务正确率尚未独立评分。两个 incomplete 保留，不加 token 上限或重标成功。
V1125 同时读取的 native 前缀在当时仅有 101 个终态，不能用该旧前缀总量与完整
predictive 宣称性能差异。V1134 的新系统审计已覆盖 native 完整服务事件。
最终两侧各 154 completed/2 incomplete；native/predictive 客户端总时长
7125.628/8201.434 秒，全部 156 项原始 workflow duration 均值为
1905.792/1912.261 秒。前者是完整运行时长，后者不是仅成功任务的 JCT，也不是
质量验收；两侧任务正确率尚未独立评分。本轮不能宣布系统取得稳定收益。

- `experiments/reports/v1125_v73_predictive_agent_work_20261016.json`
- `experiments/reports/v1125_v73_native_agent_work_prefix_20261016.json`
- `experiments/reports/v1122_v73_predictive_context_summary_20261016.json`
- `experiments/reports/v1125_redundancy_auditor_cpu_20261016.xml`

只读审计器为开发分支 `scripts/audit_agent_work_redundancy.py`。每个输入保存实际
读取字节数及 SHA256，活动 JSONL 只读观察长度内的完整行。8 项 CPU 检查覆盖
并发工具身份、完成前不可命中、工作区 epoch、部分行、摘要归因和 batch geometry。
不新增 GPU 探针、不改变客户端、模型、工具或引擎。

## 3. 最值得关注的实际成本

| 项目 | 完整 V73 predictive 观察 | 含义与限制 |
| --- | --- | --- |
| 内部摘要 | 123 个 workflow、693 次；请求墙钟 P50/P90 为 27.88/46.49 秒 | 包含排队、生成和传输，693 次时长之和不是端到端收益 |
| 摘要输入计算 | 14,933,804 个 native prefill extend token，占 33.58% | 已由实际 batch geometry 核对，不只是提示词字符数 |
| 摘要后首个正式请求 | 6,570,836 个 extend token，占 14.78% | 其中有必要的新输入；不能全称为无用重算 |
| 摘要与后继请求合计 | 21,504,640 / 44,470,352 个 extend token，即 48.36% | 是成本集中区，不是可实现的加速百分比 |
| 摘要输出 | 640,523 / 8,156,973 token，即 7.85% | 已关闭 thinking 的专用摘要请求仍有这部分生成 |
| root 同结果重复 | 92 个 workflow 中 2,432 次，运行时观察到相同 workspace epoch | 不是全环境等价证明，不能据此直接安全缓存 shell |
| 发起这些重复的模型请求 | 2,418 次、398,377 输出 token，占全部输出 4.88% | 工具结果缓存本身省不掉这些已经发生的生成 |
| 同 agent 相同参数重复 | 12,793 次，涉及 152 个 workflow | 期间可能修改状态，包含写工具，不是缓存命中数 |
| 不同 agent 相同参数 | 2,407 次，所涉工具墙钟合计约 307.60 秒 | 有些是通知或写工具，且并行重叠，不构成较大工具缓存收益的证明 |

实际 prefill 核对覆盖 40,688 个批次，所有保留样本都有已知 extend geometry。
mixed prefill 若含 decode 行，其一 token 也按原生 geometry 记录；不按 token 比例
分配 GPU 时间。两类摘要相关 token 总量与登记处 uncached token 核算吻合。

当前 native 前缀也观察到摘要占已记录 extend token 的约 29.72%，摘要后首请求
另占约 12.65%。这说明上下文成本不是只有 predictive 新控制器才存在的现象；
它不是两侧完整性能或质量对比。前缀有 3,587 个 token 尚不能匹配已封存客户端
身份，保留 unknown，不能填零或强行归因。

## 4. 系统侧最新测量及已有能力

V1134 新审计器只读完整行、保存实际字节数和 SHA256，不新增服务探针或 GPU 任务。
7 项 CPU 检查覆盖身份去重、未知/非法采时、两级缓存核算、重叠区间、完整行截断、
小对齐差距与首次/续接请求分组。结果：

- `experiments/reports/v1134_v73_native_system_costs_prefix_20261016.json`
- `experiments/reports/v1134_v73_predictive_system_costs_20261016.json`
- `experiments/reports/v1134_native_system_costs_cpu_20261016.xml`

native 报告名中的 prefix 指固定文件观察范围；本次已在 native 退出后核验它覆盖
全部已写服务事件，不能与 V1125 的未完成客户端前缀混淆。审计器位于开发 worktree
`scripts/audit_native_system_costs.py`，不改变 V73 的源码、引擎或 helper。

| 系统观察 | native | predictive | 解释 |
| --- | ---: | ---: | --- |
| 实际出现原生首次服务事件的请求 | 37,612 | 40,186 | 未服务/未进入该事件的请求不在分母 |
| 对话转换 P50/P90 | 51.39/75.71ms | 53.42/78.74ms | 包含模板处理及其内部 tokenization，不是独占 CPU |
| 对话转换区间累计 | 1937.44s | 2158.30s | 与 GPU/其他请求可重叠，不能从运行时长扣除 |
| HTTP 收到至原生入队 P50 | 84.54ms | 87.75ms | 不含原生排队至服务，不能称 H2D 等待 |
| 完整输入 token P50 | 20,943 | 21,009.5 | 前端处理的整段输入 |
| 原生 uncached 输入 token P50 | 379 | 376 | 与完整输入的两个中位数不能逐请求相减 |
| FULL 匹配超出实际两级复用边界的正差额 | 2,991,043 token | 3,233,014 token | 占 uncached token 7.26%/7.27%，不是提速百分比 |
| 正差额 P50/P90 | 36/60 token | 35/60 token | 大部分是小差距，不能全部解释成大块状态丢失 |
| 差额至少 512 token | 775 请求/2,422,421 token | 797 请求/2,619,981 token | 大差距须单独核查来源和实际计算时间 |
| 续接请求中差额至少 512 token | 347 请求/1,271,924 token | 399 请求/1,534,106 token | 首次出现与后续出现按 context 分列，含 compaction 后续接 |

FULL 匹配与可复用混合状态边界不同是引擎确有的行为：
`unified_cache/components/mamba.py::finalize_match_result_in_tree_core`
从 FULL 最深匹配计算新 branching checkpoint 目标，而实际请求从具有必要状态的
有效边界续算。目标位置不等于已有可恢复状态；checkpoint 对齐、历史改写、数据
驻留和缓存保护均须检查，不能只由差额判定是 Mamba 驱逐或策略缺陷。
Marconi [R6] 已研究混合模型精确状态命中、checkpoint admission 与按计算节省/
内存占用决定 eviction；“混合缓存有不同边界”或“增加 checkpoint”不单列为新意。

前端全量处理是具体系统对象，但单独优化的端到端价值尚未成立。native 转换
累计平均每个 workflow 约 12.42 秒，相对全部原始 workflow duration 之和约
0.65%；这是工作量的量级比较，不是全局收益上界，因为请求相互排队和并行。
同样，1937 秒也不能解释成 GPU 被它阻塞了 1937 秒。须进一步测它是否真正限制
请求供给、关键串行续接或与 GPU 并发时的 CPU 路径，才决定投入。

本地冻结引擎已具备两种容易混淆的能力：

1. 当前应用的 top-level `session_id` 配合 `enable_session_radix_cache` 提供 radix
   引用生命周期与 eviction 偏好；`handle_generate_request` 仍按本次完整输入
   构造普通请求，没有自动拼接历史。
2. 原生 `Session.create_req` 已有追加、分叉和 replace/offset 路径；streaming
   session 可在连续轮次保留 KV/状态。追加路径和当前 radix session 不是同一种
   接口，但“支持有状态会话”本身已存在，不能当作本项目创新。

此外，TokTier [R7] 已有保持参考 token IDs 一致的 session 增量 tokenization，
Delta Tokenizer [R8] 已有精确增量、CPU chunk 并行和在 SGLang 的评估。
要比较的是它们在实际 Qwen chat template、历史改写、分叉与当前负载中的适用性，
不能因为主干没有直接启用便声称此前未解决。未经匹配测试，不断言它们无法支持
某种动态 workflow。前端增量化目前作为性能候选/工程对照，未选为论文贡献。

### 4.1 V1135：大差距主要发生在首次/压缩后输入

完整服务记录与客户端身份、压缩事件逐请求连接，全部 37,612/40,186 个原生
请求均匹配到客户端身份。实际 batch geometry 分别核算出 41,220,157/
44,470,352 个 extend token，与输入计数一致；FULL 差额对应的 extend 位置
分别为 2,991,043/3,233,014 token。没有按 token 比例分配 GPU 时间。

| 至少 512 token 的差额来源 | native 请求/差额 token | predictive 请求/差额 token |
| --- | ---: | ---: |
| root 首次请求 | 110 / 417,725 | 106 / 409,688 |
| child 首次请求 | 318 / 732,772 | 292 / 676,187 |
| root 压缩后首请求 | 76 / 440,734 | 111 / 646,919 |
| child 压缩后首请求 | 242 / 793,912 | 259 / 853,903 |
| root 普通续接 | 10 / 15,706 | 11 / 9,717 |
| child 普通续接 | 19 / 21,572 | 18 / 23,567 |

native 775 个大差额中，746 个发生在首次或压缩后输入；普通续接仅 29 个、
37,278 token。不能据此把长期 agent 状态频繁丢失当作主要瓶颈。首次/压缩后
仍可能有公共前缀和缺失 recurrent checkpoint，应先与 Marconi 类 admission
比较，不能由差额直接判定可以跳过计算。

原生同 session 的旧输入前缀缺失代理合计 1,161,267 token，不是逐层实际 FLOPs。
Host FULL 驱逐后被请求归因为重算的仅 5,527 token；这是已观测 HMAC 精确前缀
匹配的驱逐记录范围，不是所有形式重算的总量。缺旧输入探针时保持 unknown，
首次/新 session 的样本不填零。

### 4.2 V1135/V1136：逐叶 write-back 的工程候选

native 实际配置是 `hicache_write_policy=write_back`。当前 FULL 驱逐路径对
每个未备份叶节点执行 backup、阻塞式 writing_check、再 demote。controller
已有 CacheOperation.merge_ops，但这里每次 write 立即 start_writing 并等待 ACK。

| native D2H | FULL-only | FULL+Mamba |
| --- | ---: | ---: |
| payload 批次 | 105,872 | 19,286 |
| 每批节点数 | 全部 1 | 全部 1 |
| FULL token P50 | 172 | 127 |
| 总字节，十进制 | 1.122 TB | 1.399 TB |
| transfer-stream 区间累计 | 305.407s | 79.468s |
| submit-to-ACK 累计 | 474.456s | 196.258s |

合计 125,158 批，FULL-only 中位量约 3.52 MB，不是 agent 整段大小。
两类时长均不能从 JCT 直接扣除；尚无逐次 scheduler 阻塞或 GPU 暴露空闲的
完整计时。同锚点反复 D2H 的节点有 7,021 个，但节点可 split、锚点又可覆盖
其他页，不能把重复锚点字节全认作相同数据或把未关联 H2D 的备份全认作浪费。

V1135 证据：

- `experiments/reports/v1135_v73_native_hybrid_reuse_20261016.json`
- `experiments/reports/v1135_v73_predictive_hybrid_reuse_20261016.json`
- `experiments/reports/v1135_native_hybrid_reuse_cpu_20261016.xml`：14 项 CPU 检查。

独立候选 `.worktrees/sglang-v1136-batched-writeback-20261016` 从 V73 完整引擎树
`791dd37f412514eaab6dbfeb08d18b18a6fd97d3` 建立 baseline snapshot
`d65ad382f4862b27dce55d67b9465ce002f96212`，仅改 3 个生产文件。
V73 原工作区/index 树及注册源码未更改。候选仅批量提交当前 FULL 分配需要的
连续原生设备驱逐节点：

1. 沿叶向上；若其他原生 heap victim 排在父节点之前，结束批次。多个设备 child、
   活跃/Host 锁及在途传输也结束链，不跨共享分支回收。
2. 按实际 FULL 缺口及节点预算止步。批内可使用原生 Host 回收；已排入节点沿用
   原生 pending 保护，不以“必须有空闲 Host”阻止高压下的有效合并。
3. 保留必要 Mamba 和原生 controller 合并；整批 ACK 后再释放。部分成功或后续
   入队异常会先完成已入队部分，未完成 DMA 不计可用容量。
4. 将原生游标推进至最后实际释放的节点。未知 planner、SWA/其他组件、buffer
   mode 保持原路径；Host 分配失败继续既有回收/失败处理。

`SGLANG_HICACHE_WRITE_BACK_BATCH_NODES` 默认 1，保留单节点路径；CPU 夹具用
32，尚未为 GPU 选值。开关与 predictive 解耦。设备 FULL victim 顺序受约束，
Host 回收结果与 pending 占用仍可能变化，须测命中、重算和完成率，不能只看批次数。

候选 90 项 CPU 测试及 27 个 subtest 通过。真实 CPU cache/tree 接线使用原生
CacheOperation 合并和模拟 DMA：相同 3 节点由 3 次提交合为 1 次，释放容量、
顺序、Host 索引对应数据及 ACK 前源页一致；另验证原生 pending 回收保护。
这些不验证真实 GPU DMA、PCIe 时延、吞吐或质量。最初未固定 PYTHONPATH 的
5 项失败来自旧安装包，原记录保留；最终显式固定并断言候选 Python import 根。

- `experiments/reports/v1136_batched_writeback_candidate_20261016.json`
- `experiments/reports/v1136_batched_writeback_cpu_20261016.xml`
- `experiments/reports/v1136_batched_writeback_installed_engine_failure_20261016.xml`

这是共用路径的工程候选，**不是已成立的论文创新**。批处理/同步合并有已有方法，
本地 controller 也已有合并 API。后续先测独立 GPU 效果，把有效共用修复匹配
放进 native/新策略，再判断动态 workflow 信息的额外系统收益。未登记新 GPU；
研究问题、新颖性、质量和端到端验收仍开放。

### 4.3 V75：同配置 predictive/native 实机验证已启动

V1137/V1138 在新的应用/引擎中增加低频批量计数，包括实际上限、尝试次数、
无连续原生节点、计划节点数、入队失败及成功合并。它们仅随既有每秒 Host
采样和状态文件发布，没有逐 decode 日志。应用测试 140 项、引擎 90 项及
27 个 subtest 通过，绑定记录 V1139；先前 V1136 原型和 CPU 记录保留。

V75 于 2026-10-16 08:15:55 PDT 冷启动，08:18:46 已核验真实 predictive GPU
服务、源码/引擎、实际 batch=32、相同到达表与物理池容量。应用 commit
`2ad32d097dae169ff437ecb9f66098a2dc6ffea4`，引擎完整树
`53020ce3efef3ed362e9af0db77147b39070f064`，二者及已登记新 helper 冻结。

156 个任务和顺序、Poisson 均值15秒/root、seed21、running48、context131072、
output8192、工具180s、activation14400s、Host200GB80:20、HBM Mamba/FULL0.9、
prefill4096 均沿用 V73；模型、prompt、harness、预测头和策略参数不改变。
predictive 后 native，两侧都 cap=32，开发仅一轮，不扫率/改顺序或重复。
Host 继续 preferred NUMA；无 qwen_prefix、V74 或新的 guard/canary。

- 资格：`experiments/reports/v1139_batched_writeback_gpu_candidate_20261016.json`
- 登记/启动：V1140/V1141；实际 predictive 启动核验：V1142；placement：V1144。
- 状态：`ops/v75_batched_writeback_20261016.state.json`
- 结果目录：`experiments/raw/qwen35_native_predictive_online_poisson_156root_20261016_v75_batched_writeback`

早期已有 D2H，但 FULL 合并的 attempts=0。这只能证明当时没有进入此合并路径，
不能把所有 D2H 当作 FULL 分配触发，也不能称候选已合并或发生传输失败。
继续按真实回收来源、实际容量和批次证据判断；active token usage 也不等于
allocator free list。吞吐、JCT、重算、Host 回收与公平性仍未验收。

V1149 随后的密封片段已确认真实合并：394 次尝试中成功 202 批、865 个节点，
共回收 359,210 个 FULL token；192 次无连续原生节点，入队失败和部分入队均为 0。
同一读取片段的传输日志有 202 个不含预测标记的多节点 D2H ACK、合计 865 个
节点，与上述内部合并数一致；这些多节点 payload 不再仅是 CPU 模拟或开关状态。
完整行范围、SHA256、状态快照、首批 ACK 和当时实际存活 PID 身份保存在
`experiments/reports/v1149_v75_early_gpu_coalescing_20261016.json`。
这是机制执行证据，不能据此把所有字节算成有用、推导净吞吐或质量/JCT 改善。

V73 两侧质量输入各 156 项已导出，原始两项 incomplete 均保留；输入核对
任务集合、patch 和来源哈希，尚未执行官方评测，也未启动额外评测容器：

- `experiments/reports/v1146_v73_native_quality_inputs_20261016`
- `experiments/reports/v1147_v73_predictive_quality_inputs_20261016`
- `experiments/reports/v1148_v73_quality_inputs_basis_20261016.json`

### 4.4 V75：已封闭窗口的系统路径核对

V1150/V1154 复用既有 batch-geometry 分析器，读取 V75 predictive 与历史
V73 native；V1151 保留 V73 predictive 的相同窗口。原点为各臂 GPU monitor
首行，均按 America/Los_Angeles 解析，已覆盖 `[600,900)` 与 `[900,1200)` 秒。
这不是相同模型轨迹的隔离消融；V75 native 尚未运行，历史 native 缺少共用合并
改进，不能用其宣称最终收益。

| 600–900s 已观测项目 | V75 predictive | V73 predictive | V73 native |
| --- | ---: | ---: | ---: |
| 未带预测标记的 FULL-only D2H 批次/节点 | 985/2,937 | 3,884/3,884 | 3,009/3,009 |
| 上述数据量 | 25.994 GB | 29.542 GB | 36.900 GB |
| 全部 FULL-only D2H transfer-stream 累计 | 8.645s | 9.692s | 10.826s |
| 已埋点 Python self 墙钟累计 | 17.024s | 19.513s | 未采集同口径 |
| 实际 decode 请求步数 | 354,358 | 373,210 | 376,582 |
| 同会话旧输入缺失代理 token | 70,083 | 80,665 | 70,283 |

stream 行包括带预测标记的 FULL-only D2H，不能与上一行未标记 bytes 混成带宽。
低频快照分别记录成功合并 509 批/2,464 节点与 536 批/2,910 节点，入队失败及
部分入队均为 0；第一个窗口实际未标记多节点 ACK 为 507 批。快照与 ACK
选窗边界不同，不要求逐窗口相等。轨迹和流量不同，批次数下降不是隔离提速。

满 48 请求、相近上下文的 decode worker 均值比例 V75/V73 native 在两个窗口为
0.993–1.022 与 0.986–0.994；单请求且 actual extend<512 的短 prefill 分桶比例为
1.19–2.14 与 0.76–1.18。分桶仍有不同实际 token 与邻接 batch，不能将第一个
窗口推广成全程固定差距，或把第二个窗口当成因果加速。
V1152/V1153/V1158 配对现有 prefill phase 样本；第二窗口 V75/native 的短
prefill 主线程 run_batch CPU 均值为 39.57/33.23ms（79/73 样本），CUDA event
包围区间为 31.33/25.92ms。event 包含发射间隙、依赖和可能的并发工作，不是
纯 kernel 时间。所配样本未记录层恢复等待，不证明全路径等待为零，也不能将
差额直接归给某个函数。V75 第二窗口 Python self 累计仍为 22.191 秒。

Host FULL 占用采样 P50 为 20.44%/37.43%，Mamba 满池；FULL Host 回收建堆
增量为 0。Mamba 触发 Host 回收分别释放 125,879/148,876 个 FULL token。
该现象先归池配置和原生回收诊断，不恢复完整状态预算研究主张。旧输入缺失
代理不是逐层重算；缺少 FULL reaccess 归因保持 unknown，不能填成总重算为零。

补充只读分析器为开发 worktree 的 `scripts/audit_transfer_control_window.py`：
复用完整行封存和 pool 字节核对，记录输入/代码 SHA256、窗口及快照滞后。
3 项 CPU 检查覆盖乱序快照、边界、覆盖不足/计数重置和两种重算口径。
V1155/V1156 为第一窗口传输/Host/重算报告，V1157 为第二窗口，均在
`experiments/reports/`。未修改 V75 应用、引擎、helper 或 GPU 探针。
合并真实发生，净收益仍未成立；接下来先核对短请求执行组合中的 CPU 发射、
状态复制/恢复依赖和控制成本，再选择可证明已有方法差异的系统机制。

### 4.5 V76：无预取、保留备份与工作流运行时的独立候选

按最新讨论，下一轮先隔离预取及其专用预测/观测成本。候选
`.worktrees/beliefkv-v1160-no-prefetch-20261016` 已冻结为 `b2adf481`，从 V75 的
`2ad32d0` 派生；物理引擎与 canonical patch 均未改变，仍为树 `53020ce3`、
批量 write-back 上限 32。当前 GPU 仍运行 V75，不热改或中断它的对照。

| 路径 | V76 reactive 配置 |
| --- | --- |
| JOIN/tool 完成前的预测 H2D | 关闭 |
| child 内容预测 worker、正文内容上送 | 关闭 |
| H2D 机会枚举和诊断扫描 | 关闭 |
| 工具时长模型 | 保留，供长等待 PREPARE/卸载判断 |
| PREPARE_HOST、冷页候选、终态 child 回收 | 保留 |
| 因果/驻留优先和有界收尾/parent 优先 | 保留 |
| 原生需求 H2D、引用保护和逐层恢复依赖 | 保留 |

已关闭的预取不再调用 JOIN/tool 预取分发器；仍有在途 ticket 时保留原处理入口。
PREPARE 的选择/ACK/消费归因和运行时状态继续记录。新的
`BELIEFKV_H2D_OPPORTUNITY_SAMPLING=0` 只去掉枚举，不关闭整个观测通道。
原生恢复使用原生引用和锁，已有 HBM 驻留准入不依赖预取租约；没有额外构造
一套未验证的需求恢复租约。关闭不能用 `PREFETCH_LEAD_MS=0` 代替。

原 reactive 启动会继续上送正文，候选已修正。实际启动环境检查将 GPU launcher
替换成环境捕获进程，执行真实 shell/env 配置，验证 reactive 保留工具模型/
PREPARE/驻留而移除语义模型，predictive 保持原配置，native 清除继承的语义配置；
没有启动模型或 GPU canary。340 项回归及 3 项新增环境检查通过，去重 343 项。
最初新增用例字段名拼写失败保留在 V1160 XML，不混作成功；资格为 V1164。

V76 于 09:09 PDT 已登记并启动等待进程 `3035460/start_ticks633722974`。
等待 V75 supervisor/driver 的实际身份退出、完整 native 后处理结束且资源可用，
再自动启动单臂 reactive；不追加新 native。使用同 156 项任务/顺序、Poisson
均值15秒/root、seed21 和到达表摘要 `00e1d626…`，其余模型/应用/预算保持匹配。
参考的是 V75 中同样启用 batch=32 的 native；单次历史对比仍不是正式配对重复。

- 资格：`experiments/reports/v1164_no_prefetch_candidate_20261016.json`
- 登记：`experiments/reports/v1165_v76_no_prefetch_registration_20261016.json`
- 权威状态：`ops/v76_no_prefetch_20261016.state.json`
- 结果：`experiments/raw/qwen35_native_reactive_online_poisson_156root_20261016_v76_no_prefetch`

V1163/V1167/V1168 另封存 V75 的 2400–2700s 窗口：满48 decode 分桶的 worker
均值比例 V75/V73 native 为 0.981–1.003，实际 decode 请求步数为
358,173/333,265；短 prefill 分桶比例仍为 1.009–1.514。Python self 累计
23.347s，其中 H2D 机会扫描 2.391s。V75/native 短 prefill phase 样本主线程
selection CPU 均值为 16.28/28.99ms，run_batch 为 39.15/34.00ms；说明不能
将全部差额归给新增准入。传输/回收量与任务轨迹不同，不能由该窗口隔离合并、
终态回收或预取的因果收益。V76 尚无 GPU 服务、吞吐/JCT 或质量结果。
该候选是选择系统主机制所需的工程消融，不因“去掉预取”就宣称论文创新成立。

### 4.6 V75：终态回收与普通 checkpoint 失效必须分开

V1171 在 09:37:56 PDT 完成只读前缀审计，覆盖机会日志的前 957,692,155 字节
和 156 份客户端完整行前缀；各输入均记录实际读取长度与 SHA256。分析器为
`scripts/audit_terminal_cache_lifetime.py`，复用既有物理释放和 parent 关联核算。
V1172 的 87 项 CPU 检查包含原有生命周期用例，以及新作用域、时钟、epoch/
session 精确匹配、节点身份复用和活动文件截断检查。没有增加 GPU 探针或改动
运行中的源码。V1170 是首次审计，普通 epoch 样本的名称仍有歧义；其诊断样本
结论由 V1171/schema2 替代，原报告保留。

| 明确作用域 | 有回收记录的 context/session 数 | 累计设备 FULL 释放 | 累计设备 Mamba 释放 | 首次正释放距调度端终态观察 P50/P90 |
| --- | ---: | ---: | ---: | ---: |
| 真实 dynamic subagent | 330 | 12.887 GB | 60.976 GB | 0.573/0.987 ms |
| runtime_internal，主要是上下文摘要 | 506 | 236.873 GB | 128.134 GB | 0.759/1.104 ms |
| 已结束 workflow 的 root | 125 | 4.097 GB | 20.991 GB | 0.760/1.440 ms |

以上是原生 allocator 实际释放的累计量，不是同时占用、避免的 D2H、parent
独占容量或 JCT 收益；没有把 Mamba slot 与 FULL token 相加。该前缀未记录正
释放 retry，节点 `(id, creation_time)` 无重复释放。最后记录的 pending 数不是
完整实时 backlog：没有释放的 retry 可以不写日志。观察时刻是调度端墙钟，
没有减去客户端 monotonic 事件时间，也不包含 RETURN 消息在到达调度端前的延迟。

两个需要固化的口径修正：

1. `terminal_child_reclaim_calls` 包括内部摘要，不能直接称为返回 child 数。
   本次非 root 的 FULL 释放大部分来自内部摘要。只看汇总计数会误判动态 fork/
   join 生命周期优化的覆盖和来源。
2. `_terminal(key)` 也会在 context epoch 改变时成立，因此
   `terminal_context_cache_sample` 不一定对应 invocation RETURN。35,751 条
   dynamic-subagent 角色样本包含 22,692 个旧 checkpoint key，却只涉及 345 个
   invocation。与真实终态回收匹配同 workflow/invocation/context/epoch/session
   的仅 348 条、159 个 checkpoint，其中 189 条发生在调度端终态观察后。
   普通旧 checkpoint 仍可包含后续会复用的共享前缀，不能称为“已结束 agent
   的无用 KV”。匹配的终态样本也有共享祖先，仍不能全部按可回收字节求和。

166 个已有 parent 首次服务记录的 RETURN 观察→首个完成 GPU batch 的 P50/
P90 为 268.79/7157.13 ms，请求可见→服务为 153.62/7098.16 ms。它们是已经
发生的关联，不是 GC、恢复或优先级各自的因果收益。结合亚毫秒的回收，现有
证据不支持继续把“更快执行终态释放”当成主要剩余瓶颈；较长的续接尾部仍需
按实际容量、依赖、排队和执行拆开。诊断也有成本，但 V1163 的 300 秒窗口里，
已计时 `terminal_sampling` self 仅累计 0.586 秒；初始观察包含于事件路径，
不能把所有控制成本归给这个采样器。

原生客户端本身也通过 executor 关闭 session；关闭引用与实际丢弃私有缓存是
两件事，不能声称 native 从不关闭 session。下一步先使用 V75 同 batch native
和 V76 隔离额外控制/状态策略的净效果，再判断这些累计释放实际改变了哪些
备份、重算和服务等待。容量释放的存在本身不证明有论文级收益。

### 4.7 追加已有工作约束：生命周期与 Host 写入准入

本轮核对落盘的版本化 arXiv PDF/摘要页，不把搜索结果的题名或相似编号直接
当作已读论文。原始来源与摘要、正文抽取的 SHA256 保存在
`experiments/reports/v1173_related_work_primary_20261016/`；绑定见 V1174。

- Lachesis [R9] 已利用 harness 的 segment 身份、spawn 记录和关闭边界，按
  生命周期选择 HBM/HBF，并在边界到达后释放对应 block；worker 的生命周期
  排名也是其已有机制。故“识别已结束 child、即时 GC、按生命周期选 tier”
  不能独立主张新颖性。其评估是 HBF 耐久性的 trace-driven 模拟，不能将该
  结果当作当前 HBM/DRAM 机器的 JCT 或传输收益。
- EfficientAgent [R10] 已建立 agent 的 Host reuse working set/stack-distance
  估计，并在容量不足时拒绝大块 refill、保留已缓存前缀的增量写入，容量充足
  时恢复完整准入。“少做无消费备份”或“一律过滤 Host 写入”不能再单独作为
  新机制。若研究 PREPARE/写回，必须与这种容量感知准入合理适配后比较。

本轮检索中，候选题名对应的 `2607.11686` 和 `2607.14785` 实际分别是机器人/
宇宙网论文，已排除，不作为 EfficientAgent 或 CacheScout 的证据。后者尚未
匹配到可核对的原始来源，不据此断言它具有或缺少某种能力。正式论证以本节
已经核对的版本为准。

当前更强的对照约束是：保留共用修复的 native、使用同一生命周期信息的简单
回收、合理适配的 Host 容量感知写入准入，以及将来选出的系统机制。前两项
工程修复和后两项研究差异分别归因。若简单方法已覆盖全部剩余收益，应撤销
该候选，而不是追加控制概念；核心系统机制与质量匹配的端到端收益仍未成立。

### 4.8 最新恢复依赖审计与质量评测前置检查

V1175/V1176 复用既有只读分析器，先分析 V75 predictive 的封存前缀，再分析
完整 V73 native。分别读取并哈希 3,074,590,066/3,564,209,575 字节的服务审计
前缀，另绑定首次服务事件和传输记录；第二步重新核对已封存事件 SHA256。
V75 实际引擎源码一并绑定。V1178 保存执行参数、原工具副本和来源哈希。
复用旧工具时，V1175 的前两条 limitations 残留了旧 V73 运行状态；这两条文字
由 V1178 的 corrected_scope 更正，量测值和原文件保留。当前 live 是 V75，
V73 native 已完整结束；本次跨版本诊断不能作为批量写回或新策略的因果消融。

| 已观测项目 | V75 predictive 前缀 | 完整 V73 native |
| --- | ---: | ---: |
| 完成的 prefill batch | 36,628 | 38,206 |
| 配对的恢复依赖样本 | 27 | 89 |
| 第一次等待出现在最后 Mamba 层的样本 | 10 | 8 |
| 上述前置等待 P50 | 3.513 ms | 0.018 ms |
| 上述样本全部层等待累计 | 37.172 ms | 15.429 ms |
| 单请求且实际 extend<512 的短样本 | 7 | 42 |
| 短样本全部层等待 P50/最大值 | 0.093/3.420 ms | 0.075/0.616 ms |

探针只采每 16 个 load-consuming prefill 中的一个，表中样本数不能除以所有
prefill 当作真实发生率，也不能把等待累计外推为整轮可移除时间。Mamba 整份
COW 确实会在有本批 H2D consumer 时等待最后 Mamba 层；当前模型对应 layer38，
其前面经过该传输批次 9/10 的 FULL 层和全部 Mamba 层。它约束的是本批缺失
数据，不是整个 agent 的 KV。

V75 10 个屏障样本中有 6 个混合 Host-hit 与无 Host-hit 请求；native 的 8 个
全部混合。此关联仍没有给出每个 COW 源物理槽与全部在途 H2D 目标的排除证明，
不能直接移除等待。当前 native 的短样本等待很小，未证明重做恢复流水线有足够
额外收益；保留此依赖作为正确性条件，暂不为它增加新控制或专门 GPU 实验。
继续从 V75 同 batch native 和 V76 的真实服务/控制成本选择主要剩余对象。

质量方面，V1177 用固定官方 harness `f7bbbb2`、固定 dataset Arrow 和全部
156 项任务，核对两侧导出、原始 summary/patch 哈希以及官方 test spec 的缓存
镜像身份，156 个镜像均可用。两侧仍各 154 completed/2 incomplete、155 个非空
补丁；非空补丁不等于正确。仅执行 `prepare`，没有调用 worker/supervisor，
没有启动评测容器，当前正确率仍未评分。该前置检查去掉旧一次性预约截止的
内存配置，不登记新的运行任务；正式评分仍需独立记录资源重叠与原始失败。
不得把过去因为未尝试而计入的 evaluator error 当作模型失败。

### 4.9 V75 缺失前缀复核与质量自动接续

V1179 复用主线只读混合前缀分析器，读取 V75 最新前缀。39,216 个首次服务请求
中 2 个尚不能连接到较早封存的客户端身份；40,093 个 prefill batch 中另有
1 个样本缺原生身份，均保持 unknown。实际 extend 总量 44,036,091 token，
其中 FULL 匹配超出有效复用边界的位置共 3,183,465 token。这不是可消除计算量。
768 个至少 512 token 的大差额中，746 个是首次请求或 compaction 后请求；
普通续接仅 22 个、24,741 token。相同 session 的旧输入缺失代理合计
1,201,683 token，P50 为 31 token，不等于逐层重算或全部由驱逐造成。
该前缀继续不支持“普通续接大规模丢失可复用状态”作为主要收益来源。

为补齐质量验收，新增 `scripts/run_official_quality_after_series.py`，在独立的
ops 副本冻结并于 10:17 PDT 登记为 V1185。V1180 的 29 项 CPU 检查包括
6 项新队列检查和 23 项既有导出/评分检查；官方评分 venv 的 CLI 接线也已执行。
真实等待进程为 `3806721/start_ticks634129312`，10:20 已核验存活，
`grading_started=false`，尚无评分容器或正确率结果。

它等待 V75 supervisor/driver 与 V76 supervisor 的已登记实际进程身份退出，
保留 PID 启动时间校验；随后验证三侧完整 156 项 summary，导出全部补丁，
再次核对官方 harness、dataset、镜像与源码。评分固定按最新数据优先：
V76 reactive、V75 native、V75 predictive，单 worker、1 核、8 GiB、无 GPU。
原始 error/incomplete/空补丁不丢弃；evaluator error 与模型 unresolved 分列，
worker 退出 0 不自动表示 156 项报告完备。旧一次性预约截止不启用，也不恢复
每日停实验规则。之后若与新的 GPU 开发重叠，须另记录，不能称匹配资源正式实验。

- 实际状态：`ops/quality_eval/quality_v75_v76_cpu_20261016.state.json`。
- 登记/绑定：V1185/V1186；ops helper 与登记的 core/exporter/scorer 冻结。
- 未来导出：V1181/V1182/V1183，未来环境核对 V1184；当前尚未生成。

这补齐的是质量执行链。已有 V75/V76 的模型服务、策略、引擎和启动 helper
保持冻结；系统论文机制、任务正确率和相对 native 的端到端收益仍待证明。

### 4.10 V75 predictive 完整结果与真实成本量级

predictive 已结束，156 项全部有终态：155 completed、1 incomplete。全程
7919.168 秒，全部 156 项原始 workflow duration 均值 1806.897 秒；只取
completed 的均值为 1783.130 秒，两个分母不可混用。154 项补丁非空，尚未
官方评分。11 个 completed workflow 有既有 guard 介入记录，原生 agent JCT
eligible 为 144；保留全部任务与这些标签，不能把它们全称为未干预自然完成。
唯一 incomplete 是 pytest-7490，最后 root 响应为 `finish_reason=length`，
context epoch120，未触发 workflow deadline；不增加输出上限或重标成功。
完整结果、逐项来源 SHA256 和终态依据见 V1187。

V75 native 于 10:37:24 PDT 核验真实 GPU 服务，scheduler3831770/
start_ticks634234241，使用同一应用2ad32d0、引擎53020ce3和 batch32；
`enable_beliefkv_admission=false`，到达表与物理容量已核对。V1143 为实际启动
凭据，不是仅有计划。V76 和官方质量队列仍等待既定进程顺序，不改冻结源码。

| 完整 predictive 观察 | 数量/字节 | transfer-stream 区间累计 | submit→ACK 累计 |
| --- | ---: | ---: | ---: |
| 原生需求 H2D | 352 批 / 115.203 GB | 3.899s | 20.663s |
| JOIN/tool 标记 H2D | 204 批 / 12.156 GB，492 个节点命令 | 0.726s | 8.431s |
| D2H | 35,082 批 / 1.749 TB | 213.984s | 431.229s |

这些均不是可直接从 JCT 扣除的时间或 oracle 上界；区间可能与计算、其他
请求和控制路径重叠。FULL 分配写回合并成功 8863 批/50662 节点、19731247
token，入队失败及部分批次均为 0。该工程机制真实执行，但不能与旧 V73
流量不同的总量相除宣称因果提速。

按独立等待事件，JOIN 完成前启动且下一请求确认复用的 FULL 为93次/2.443GB，
tool 为13次/0.599GB。节点命令口径下 JOIN FULL 总复用2.663GB，包含更晚
启动的命令，不能全算提前预取。JOIN 末 ACK→首次服务 P50/P90 为2.190/
11.416秒（115个有关联 ACK 的事件），tool 为0.680/84.954秒（15个）。
不能与按357/135个节点命令统计的长尾混用；一次大 burst 会重复贡献多个
节点命令。工具正 FULL 计划只有47个独立事件，不能将全部22,588个工具等待
都称为可立即预取机会，也不能因没有预取计划就推断后来没有需求恢复。

V1193 进一步以原生 batch 核对完整计算阶段，所有39,919个客户端请求身份均
进入角色分类。共40,932个 prefill 和461,524个 decode batch，记录的 worker
区间累计分别为1226.440/6411.942秒；没有缺失采时，二者全部记录 graph replay。
decode 约占两类累计 worker 区间的84%，这不是 CUDA kernel 独占时间比例。
仅含摘要的3752个 prefill batch累计395.493秒；含摘要的混合批次累计26.014秒，
混合时间保持整批，不按 token 或角色分摊。摘要输入15,276,527 token，
压缩后首个正式请求另有6,503,522 token，合计占45,174,567个 extend token约
48.21%，不能称为48.21%的时间或可消除工作。

由此调整筛选顺序：继续完整 V75/V76 消融，首先量化高频运行时控制和真实
生成/上下文计算；预取保持消融对象，不再仅以提高 RETURN 精度为主要收益假设。
任何新执行复用方案须指出少做了哪些实际模型计算，并验证混合状态正确性和
已有方法差异。D2H 仍有可测成本，但应先与已有批处理、生命周期回收和
EfficientAgent 类写入准入比较，不能把修复共用路径直接当作 workflow 创新。

- V1187：完整 predictive、终态和传输/预取口径绑定。
- V1190：首轮依赖图审计；V1193在同一完整输入上保留拓扑并补齐阶段成本。
- V1192：19 项 CPU 检查，覆盖身份、嵌套/内部调用、缺失证据、跨 workflow
  同名、重复等待、循环、混合批次不分摊、非法采时及跨阶段重复服务身份。
- 运行源码、引擎、GPU helper 和官方质量 helper/依赖均未修改。

### 4.11 跨 agent 共享的已有工作与当前负载必要条件

V1188 封存 TokenDance [R11] 和 SAGA [R12] 的原始摘要页、PDF、正文及 SHA256。
TokenDance 第4.1–4.4节已包括共享块接口、兼容请求的 collective PIC、
Master-Mirror 差异存储及融合恢复。其当前实现基于 CacheBlend，第6.6节的
等价对象是同一 PIC 路径；不能解释为与原始模型逐 token 精确等价，也不能
直接外推为 Qwen3.5 recurrent 状态可安全跨前缀复用。通用跨 agent 共享、
差异存储或再加一套逐层恢复不自动具有新颖性。

V1193 的实际依赖图有367个 dynamic child，全部为深度1；708个内部调用
单列，不当作嵌套 subagent。200个 JOIN 的声明等待者均为1个；没有观察到
同一非空生产者集合对应多个声明消费者。156个 workflow中127个一轮JOIN，
20个两轮、7个三轮、1个五轮、1个七轮；多轮共29个（18.59%）。
因此不能把当前结构直接视作 TokenDance 的 All-Gather 场景，也不支持以
“新增多个 child”本身推导群体共享收益。

该审计只核对调用依赖，不记录等价 token/KV，不排除多个 agent 重读相同
文件、工具文本或公共前缀。后续若选择计算/状态共享方向，必须先补齐实际
渲染 token、可见源数据、因果/位置依赖及已有 native 命中证据；没有这些条件
就不实现近似 recurrent 状态复用，也不人为制造相同输出来提高共享率。
SAGA 的 AEG、缓存保留和公平调度仍是对照边界，不能靠换名称绕开。

### 4.12 V75 同配置 native 的两个完整窗口

V1195/V1198 复用已验证的 worker/控制分析器，改用 V75 当前 native；
应用2ad32d0、引擎53020ce3、batch32相同，不再用旧 V73 解释共用修复收益。
两个窗口均已完整观察，时钟按 America/Los_Angeles 校验；native 全程仍未结束。
V1200 汇总相同窗口的实际 decode token delta、监控和传输。

| 指标 | 600–900s predictive/native | 900–1200s predictive/native |
| --- | ---: | ---: |
| 实际 decode token/s | 1175.06 / 1237.65 | 1314.06 / 1188.10 |
| GPU 利用率 | 91.38% / 94.05% | 93.78% / 94.66% |
| prefill worker 区间累计 | 75.961 / 71.606s | 56.640 / 76.413s |
| 提交时 uncached 输入 token | 2,141,241 / 2,052,828 | 2,221,941 / 2,624,996 |
| predictive 已埋点 Python self 墙钟累计 | 17.024s | 22.191s |

满48、相近上下文且 graph replay 的 decode 均值比例为1.003–1.005和
0.999–1.007。短单请求 prefill 的均值比例随窗口、工作量和邻接执行变化，
中位数仍有差距；不能把均值下降当作隔离提速，也不能将某一窗口倍数推广为
固定内核差距。worker 区间包含排队、依赖和结果处理；self 墙钟也不是可直接
扣除的 GPU stall。

当前 native 在这两个窗口未记录已完成 H2D payload，而 predictive 有恢复和
PREPARE。后者的 H2D 不能直接称为替代 native 的恢复等待，仍需结合容量、
有用重算和完整 JCT/质量判断。第二窗口多出的 decode 服务同时对应约20秒
更少的 prefill worker 区间与更少 uncached 输入；不是已证明的预取收益。
保留 V76 无预取消融以核对净控制/状态效果，不据此新增高频控制或改动冻结实验。

### 4.13 生成加速的必要证据与原生能力边界

通用 agent 草稿不能直接作为新主机制：AgSpec [R4] 已有检索共享语料；
AgentSpec [R13] 已有共享 n-gram、批次预算和工具/提示词边界复用。
ReplaySSM [R14] 的状态回放与 TreeWY [R15] 的 GDN 推测验证须分开比较。
旧 V1199 中标为 ReplaySSM 的 HTML 实际属于 TreeWY；V1260 补存正确来源，
保留原归档及摘要。其 kernel/模型加速比均不能解释当前 workflow 收益。

本地原生引擎已有 `NGRAMWorker`、共享 corpus/外部语料 API、
`commit_mamba_states_after_verify` 和 ReplaySSM 入口。BeliefKV admission
初始化在 `scheduler.py` 明确拒绝 non-NONE speculation；它们的组合尚未验证。
不能删掉这一正确性约束就宣称支持，也不能把简单开启原生选项当作论文贡献。

现有 trace 的计数、正文和解析后的工具参数不足以还原实际生成 token。
补充默认关闭的 `BELIEFKV_RECORD_OUTPUT_TOKEN_IDS=1`，仅在原生请求完成时，
把不可变 `req.output_ids` 快照附在既有 `llm_result.attributes.output_token_evidence`
中；ready/status 明确记录开关。没有逐 decode 上送或额外 worker，没有记录
prompt token。native/admission 两条观测路径使用同一实现，不改变模型/工具/
终态行为。V1197 的151项CPU检查通过，包括11项新增检查：默认关闭不复制、
两模式、0/3/8192 token、后续列表修改及重复完成、非法配置。

这些是引擎保留的生成 IDs，可包含 reasoning、工具序列化和特殊 token；
一次 LLM 请求完成不等于 child RETURN。后续因果重放必须绑定模型/tokenizer、
同一时钟及请求身份，草稿源只能使用当时已可见的数据。仅有完成序列时，应先
保守地限制为目标请求提交前已完成的源，不利用未来 response 或本轮完整输出
构造草稿。离线 token 一致仍不证明真实接受率、Mamba 回滚正确性或净提速。

普通 ReplaySSM decode 与 speculative verification 是两个入口；admission
拒绝 non-NONE speculation 本身不等于拒绝普通 buffered decode。当前
extra_buffer 策略与普通 decode 入口仍有独立兼容性约束，见4.27。

V75/V76 的已冻结候选未加入此采集，当前旧 trace 的缺口仍存在；没有补造
历史 token、登记新 GPU 任务或改变已排队实验。它是下一独立候选的数据能力，
尚无实机开销/推测接受率结果。下一机制仍须以实际减少的模型工作、控制/恢复
成本、资源预算和完整任务质量证明价值。

### 4.14 原生 session 关闭回收的简单对照与主线保存

为检验终态生命周期收益是否需要完整工作流控制器，独立引擎候选
`f1dc97d` 在显式 session close 后复用现有安全回收检查，仅丢弃已经没有
活跃引用、共享依赖、锁和在途传输的私有节点。受阻节点保留身份与generation，
在原生HiCache完成/锁释放后的安全点有界重试；锁释放不直接丢页，晚结束
请求也在cache清理后排队。此项不依赖RCCG、预测、PREPARE或parent优先级。

这是简单工程对照，用于证伪复杂控制器的必要性，不主张新的生命周期贡献。
`SGLANG_NATIVE_SESSION_CLOSE_RECLAIM` 默认关闭。独立增量补丁以完整V75
tree53020ce3为底，得到tree76cf97d2；V1208绑定提交、patch和验证哈希。
规范staging patch及冻结V75/V76未修改，独立论文素材未纳入此次系统提交。

V1205有103项真实CPU radix/allocator及生命周期检查通过；V1206有151项应用
检查通过。V1204的首次14项检查中1项因夹具缺少swa_evicted_seqlen失败，补齐
夹具后通过，原XML保留。V1207按随机顺序测每模式200次关闭，构建与插入不计时：
native/回收的线程CPU P50为0.011349/0.068110ms。回收每次释放32个私有FULL
token，并验证活跃parent前缀仍可用；原生保留私有缓存。两者工作量不同，
这只能量化该小夹具的额外关闭成本，不能推出GPU吞吐/JCT或全程控制成本。

当前CPU资格限单rank HiCache的FULL或FULL+Mamba；没有多GPU、非HiCache及
其他缓存模式的重试证明。候选HEAD与现有launcher固定的94602c9不一致，
后续需要独立组合补丁和启动资格，当前未登记/启动新GPU实验。是否保留此
简单基线及复杂控制器，仍由同任务质量、完整JCT/吞吐和真实状态成本决定。

### 4.15 最新 V75 窗口与简单对照的启动资格

V1209继续读取同一V75两臂的3000–4200s，GPU监控、服务和客户端时钟已核对，
窗口两侧均完整观测。其动态来源尚未逐文件封存前缀，属于开发观察，不作
正式性能验收。完整native仍在运行，不能用已结束子集计算最终平均JCT。

| 指标 | predictive | native |
| --- | ---: | ---: |
| decode token/s | 1293.30 | 1273.97 |
| GPU利用率 | 96.25% | 96.87% |
| 提交的uncached输入token | 9,084,046 | 8,953,101 |
| prefill worker累计区间 | 253.865s | 252.253s |
| D2H实际字节 | 306.329GB | 421.349GB |
| D2H transfer-stream累计 | 38.957s | 62.568s |
| 全部H2D transfer-stream累计 | 0.222s | 0.234s |

这个窗口显示需要分开测写回、生命周期和控制成本；它并不隔离任何策略。
两侧running、waiting和真实生成工作不同，不能把约1.5%的decode差距当作
预取、回收或批量写回的因果收益；流时间也不是可直接扣除的JCT。

V1211把上一节的简单关闭回收候选准备为显式launcher选择：
`native_session_close`使用完整组合补丁，保留94602c9的HEAD约束和反向patch
检查。新独立检出的实际tree精确等于76cf97d2，未改V75/V76共用引擎。
58项启动、NUMA和来源CPU检查通过；直接导入新检出的禁用/启用两模式也
验证了0/2个私有FULL token释放与活跃parent保留。既有103项引擎检查仍
绑定相同源码树；不把源码相同误写成新GPU验证。
V1210的原56项检查保留；补充缺少源码/开关与补丁不匹配的静默未启用风险
后，V1212的最终58项全部通过，来源摘要以V1211为准。

尚未登记新GPU单臂实验。现有pair driver不接受单独native，后续需要独立
应用检出、同表任务/到达/预算计划和启动登记。这个对照用于判断完整工作流
控制器是否必要，不新增已成立研究贡献；最终仍以全部任务质量、平均JCT、
有效吞吐和系统成本选择下一机制。

### 4.16 V77 独立原生关闭回收对照及可证伪条件

V77用于检验一个具体问题：终态生命周期管理的效果，有多少可以由显式session
关闭触发的原生私有页回收取得，是否需要完整工作流控制器。不是把通用关闭
回收重新命名为论文贡献；已有方法边界仍按4.7和4.14执行。

独立候选应用52a2b8e、引擎tree76cf97d2已由V1214/V1215冻结登记，任务、prompt、
模型调用及工具执行代码相对V75保持一致。native入口无admission控制器，
仅启用关闭回收，保留batch32、Host200GB/80:20、HBM ratio0.9、原生逐层恢复、
通知与终态规则；关闭PREPARE、预取、语义模型、额外优先级和生成IDs采集。
实际V75计划的38项任务/到达/资源字段相同，156项Poisson表摘要不变。

新单臂驱动的34项CPU检查覆盖同表/预算、来源冻结、真实进程等待、实际开关
缺失/失配，以及后处理保留所有任务和失败标签。原103项引擎、151项应用观测、
58项启动检查与源码也随资格绑定，不把跨检出的同树验证称为GPU结果。
监督1076769/start_ticks634866646在12:20实测存活，等待完整V76成功结束和
资源释放。V75 native继续原运行，V76和质量队列顺序未改；当前V77尚未启动GPU。

验收先确认真实关闭/释放发生、私有FULL/Mamba容量和pending重试，再比较D2H、
Host回收、原生恢复、有用重算及请求服务。独立单臂会生成完整HTML与历史native
开发对照，保留全156项和终态标签；不能仅用释放字节或完成子集JCT称收益。

可证伪判断：

1. 若native-close在质量匹配后取得控制器的大部分效果，生命周期释放用这个
   简单基线解释，不宣称复杂控制器或动态DAG机制是必要条件。
2. 若V76与native-close仍有差距，需沿实际服务、模型工作、驻留/写回及公平性
   分解，不把差距自动归给工作流感知。V75与V76同时改变预取、语义采集与扫描，
   也不能把二者差值当作单一H2D算子的隔离收益。
3. 若三者均接近native，缩减相应工程范围，继续寻找有规模的系统成本与已验证
   现有方法差异；不扩大控制器或制造无用分支、异常循环以维持研究假设。

原V75/V76官方CPU评测可能在V77运行时占用单核/8GiB，计划明确记录这一限制；
本轮不作为正式匹配资源的重复实验。V77完整结果后单独补充全任务官方质量
评测，当前未登记该新质量队列。V1216/V1217/V1219是未来启动、完成和NUMA
证据，尚未生成。最终研究与质量匹配的JCT/有效吞吐目标仍保持未完成。

### 4.17 V75 完整配对结果与下一步归因

V1226绑定完整两臂摘要、原定比较、物理传输和native失败轨迹。V75在12:34:06
以AB driver退出0结束，observer_errors为空；全部156项都保留，native客户端
exit1并不代表缺少任务收集。相同应用2ad32d0、引擎53020ce3、batch32、任务/
Poisson表和容量，仍不能约束实际模型轨迹相同。

| 指标 | predictive | native |
| --- | ---: | ---: |
| completed / incomplete | 155 / 1 | 153 / 3 |
| 收集时长 | 7919.168s | 6841.743s |
| 全156项原始duration均值 | 1806.897s | 1665.374s |
| completed-only原始duration均值 | 1783.130s | 1608.445s |
| completed workflow/h | 70.462 | 80.506 |
| 显式native JCT可用workflow | 144 | 141 |
| 有既有guard介入标签的workflow | 11 | 13 |
| 客户端LLM请求 | 39,211 | 36,166 |
| 客户端工具调用 | 38,927 | 35,896 |
| 需求原生H2D | 115.203GB | 13.610GB |
| 预测H2D | 12.156GB | 0 |
| D2H | 1.749TB | 1.935TB |

predictive原始耗时均值高8.50%，完成吞吐低12.48%。152个共同completed任务
中，predictive较快78个、native较快74个，但前者原始均值仍高169.377s；
其中仅132个两侧都显式JCT可用。官方质量未评分，不能用完成子集或guard
终态宣称平均JCT收益，也不能把完整结果的劣势改写成先前局部窗口的优势。

native的pytest-6197、django-13089、django-11179末两次root响应均为length，
deadline均未过期，错误字段为空；11179还记录一次既有guard介入。输出8192
配置和原终态保持。质量评分后仍按全部任务及失败/guard标签分别报告。

需求H2D差额需要拆分策略引发的状态变动和实际轨迹，不能把predictive的
115.203GB当作可替代native的13.610GB。全部H2D流累计约4.624/0.661s，
D2H为213.984/276.723s，均非可直接扣除的JCT。native和predictive的同session
旧前缀丢失代理为1.148M/1.223M token；有限历史代理不是精确有用重算证明。

继续原定V76消融和V77简单对照，先量化控制成本、实际工作、恢复/写回与服务
延迟。V76于12:34:09接续；V1166在12:37:09确认真实首服、同到达/同池及无预取，
仍保留PREPARE、工具时长、生命周期/驻留/收尾调度。当前没有V76净收益结论。
不以本轮完整负结果扩展高频控制，也不据局部相近worker区间忽略真实额外工作。

### 4.18 V77 全任务官方质量队列

V1223/V1225登记独立V77队列，复用APP52a2b8e中的冻结series及原CORE、exporter、
scorer，不改原V75/V76队列。新薄封装仅选择V77、独立run ID和输出路径，等待
准确监督身份并追踪后来启动的driver/worker，避免先前监督退出但worker仍活跃
时并发第二个grader。17项CPU检查与实际quality venv中的组合检查通过；
V1227绑定源码/检查/登记摘要。监督1121673/start_ticks634983330已实际存活。

等待完整V77和旧评分队列退出后，导出并评分全156项，单worker/1核/8GiB，
官方测试超时仍1800s、无GPU，保持已有scorer的measurement_complete条件。
完整官方正确率评分和原生JCT测量有效是两个条件；有incomplete或guard标签时
不能为了产生有效吞吐数字而放宽后者。V1222输入、V1224环境和正式评分仍为
未来输出，当前grading_started=false。新系统贡献及质量匹配性能目标仍未完成。

### 4.19 V76 首个完整 600 秒窗口

V1228复用既有monitor_window/control_profile，按真实reactive臂读取，无新运行时
采集。报告绑定分析模块摘要、方法、时区及控制文件前缀/边界行摘要。V76与
V75 predictive的已埋点Python self墙钟累计分别15.043/25.377s，实际counter
快照跨度600.617/600.036s。剩余V76最大几项为control_drain3.343s、
scheduler_maintenance3.059s、event_apply2.671s、join_prepare1.693s。

关闭预取/语义/机会扫描的配置已由V1166确认；较少的控制支出与这一变化一致。
但事件数、forward数和实际工作不同，上述值不是线程CPU、GPU stall，也不是
隔离的省时比例。不能把self墙钟差直接从完整JCT中扣除。

V1229使用既有支持任意臂名的service分析器读取同样0–600s：

| 指标 | V76 reactive | V75 native |
| --- | ---: | ---: |
| decode token/s | 1001.40 | 932.72 |
| decode时间加权batch | 25.68 | 23.66 |
| 提交uncached输入token | 3,745,230 | 3,650,788 |
| prefill worker累计区间 | 119.633s | 123.824s |
| 需求H2D | 5批 / 0.758GB | 无 |
| D2H | 120.021GB | 139.944GB |
| 其中PREPARE | 1.781GB FULL | 无 |

较大的batch和不同生成需求也能改变速率；早期段还不能反映完整压力、长尾和
质量。V76仍有需求恢复而native此段没有，后续要关联PREPARE、回收、真实缺失
前缀和实际请求，不能单凭字节差就归咎策略或宣称有用迁移。
本轮证据足以继续当前消融，尚不足以扩展高频控制或形成新的论文主张。后续
优先V76有压力窗口及完整结果，再与V77简单对照和全任务官方质量合并判断。

### 4.20 V76 等待状态回收、恢复关联与独立分池候选

V1233封存截至13:23 PDT的逐文件完整行前缀，按workflow/invocation/context/epoch
关联定向回收、此前PREPARE ACK、下一较高epoch原生请求和同节点H2D。新增
`audit_wait_state_recovery.py`及既有生命周期审计的94项CPU检查通过（V1232）。
V76没有旧审计器要求的safe_point_census；首次调用旧工具时长审计因此拒绝。
新审计只用服务端wall时钟，不伪造配对时钟，不报告工具预测准确率。

| 观察 | JOIN等待 | 工具等待 |
| --- | ---: | ---: |
| 含定向回收的context/epoch | 129 | 13 |
| 原生节点回收记录 | 489 | 27 |
| 后继首次服务已观察 | 100 | 13 |
| 同节点H2D与后继匹配路径可关联 | 100 | 12 |
| 首次回收到服务观测间隔P50 | 366.300s | 2.773s |
| FULL与Mamba一起释放的记录 | 238 | 26 |
| 只有FULL释放的记录 | 251 | 1 |

这142个context/epoch不是独立工具或JOIN事件的完整机会分母。112个已关联轮次中，
未观察到首次服务前的“定向回收→同节点恢复ACK→再次定向回收”序列；未服务的
29个轮次，以及已服务但缺时间证据的1个轮次保持未知。demotion和原生H2D没有
节点creation identity；即便同node ID出现在后继匹配路径，也不是组件forward
复用证明。原生未标记驱逐、节点拆分及其他epoch的活动不能据此排除。
长JOIN间隔支持这些parent确实长期等待，不能仅凭新增H2D断言卸载是错误的。

PREPARE为510个JOIN节点命令/4.431GB FULL、578个工具节点命令/7.106GB FULL，
均有ACK，Mamba字节为0。整个封存transfer前缀有613批H2D/254.923GB，
流时间累计7.939s；与回收可关联的112整批共17.946GB/1.067s。整批包含其他
状态与页，不能计作回收agent的独占字节、避免的传输或可扣除JCT。V1234以相同
前缀摘要复核数量、运行开关和引擎源码；逐文件封存不是跨文件原子快照。

V1235/V1238核对另一个完整1200–1800s窗口：

| 指标 | V76无预取 | V75 native | V75 predictive |
| --- | ---: | ---: | ---: |
| decode token/s | 1339.85 | 1333.46 | 1329.42 |
| decode时间加权batch | 47.501 | 47.494 | 47.491 |
| decode worker平均区间 | 28.314ms | 28.300ms | 29.130ms |
| prefill worker累计区间 | 120.821s | 123.336s | 111.090s |
| GPU利用率采样均值 | 96.58% | 97.21% | 93.96% |
| 已埋点Python self墙钟累计 | 32.778s | 无工作流控制器 | 49.376s |

两控制counter实际跨度599.609/599.759s；V76剩余最大项为JOIN PREPARE5.903s、
residency_hint4.147s、event_apply3.669s、control_drain3.284s。工作量、排队和
请求组成仍不同，短prefill也未在这里逐请求匹配；这些数值不证明隔离提速、
线程CPU节省或完整质量匹配JCT收益。它们支持先完成当前消融，不恢复高频预取。

具体工程问题有源码依据：V76为static_separate_full_mamba，516个定向回收均释放
正FULL；预取与handoff关闭，原生FULL等待候选路径的全节点demote会级联释放Mamba。
仅为FULL容量触发时，丢弃独立池中的仍有效状态不是释放FULL的必要条件。
新独立候选de4118f/tree35cf6411基于V75完整树53020ce3，在没有剩余Mamba缺额时
使用已有FULL-only原语，保留原Mamba对象/引用/LRU；同时缺Mamba则保持原生级联。
之后真正出现Mamba压力时，仍由原生备份、ACK和状态回收处理。

V1237的236项回归及27个subtest、V1239的13项最终候选检查通过；后者含新增
真实CPU池压力下保留状态的原生备份回收，两个集合去重有237个用例身份。
CPU检查验证FULL恢复后hybrid checkpoint再次匹配、联合/状态缺额、活跃/共享/
在途/过期身份保护，不是GPU DMA、模型数值或性能验证。增量补丁为
`patches/sglang-v0.5.20-waiting-full-only.patch`，只应用于完整53020ce3树。
它是减少自引状态搬运的工程候选，不是新论文贡献；尚未选择进launcher或登记GPU。

V1240绑定上述来源与CPU证据，重新核验V75/V76/V77应用干净、两引擎树仍为
53020ce3/76cf97d2，29份已登记文件指纹匹配。V76继续原版本，V77及质量接续
顺序不变。最终仍需完整质量/JCT/有效吞吐及相关工作差异，研究目标未完成。

### 4.21 V76 后续窗口与主线审计整合

本次将已完成的两个只读审计器、测试及V1241–V1246报告纳入主线；V1247对
两个新测试文件及既有比较器共46项CPU回归全部通过。没有将未实现的原生
prefill顺序消融混入本次提交，也没有登记新GPU实验或改动冻结运行版本。

`audit_online_completion_windows.py`直接读取收集器记录的相对到达/完成时间，
保留时点前全部应到任务，包括未提交、未完成、失败和结果未知者。终点为
run_one返回后的workflow_finished，包含setup和产物收集，不等于result.duration_seconds。
失败/未完成任务在完成口径下持续累计到观察时点；各均值是有限观察期的逗留面积，
不是最终JCT、Kaplan-Meier RMST或质量匹配吞吐。V1242的同表队列结果为：

| 观察时点 | 应到任务 | V76 completed | V75 native | V75 predictive |
| --- | ---: | ---: | ---: | ---: |
| 600s | 42 | 5 | 7 | 7 |
| 1800s | 111 | 40 | 49 | 46 |
| 3600s | 156 | 80 | 105 | 101 |

3600s的completed口径全队列平均截断逗留为1476.103/1267.947/1273.804s，
顺序同上。已记录completed均显式标为JCT可用，但官方任务质量仍未评分。
这些是单轮开发观察，不能由不同实现轨迹直接分摊策略因果收益。

V1243封存完整2400–3000s服务窗口：

| 指标 | V76 | V75 native | V75 predictive |
| --- | ---: | ---: | ---: |
| decode token/s | 1009.645 | 1304.050 | 1245.198 |
| decode时间加权batch | 47.188 | 47.490 | 47.438 |
| H2D总量 | 637.786GB | 7.190GB | 102.896GB |
| 其中FULL | 448.096GB | 2.811GB | 66.838GB |
| D2H总量 | 725.583GB | 224.260GB | 263.761GB |
| D2H传输流累计 | 132.184s | 34.727s | 38.457s |

V76该段没有PREPARE，迁移属于原生恢复/写回。传输流累计不能直接当作可减JCT。
V1244按真实上下文分桶后，满48请求、20480上下文桶的decode worker均值为
21.244/21.557/21.889ms；47请求紧邻单请求prefill的同桶区间为
89.469/51.555/56.953ms。worker区间包含周转/依赖和重叠，不是纯kernel；
这将差距定位到请求周转附近，但尚不能归因到某个排序或回收函数。

`audit_native_host_demand.py`按首次服务匹配路径的observed_ts_ms归窗，并以准确
workflow/invocation创建记录区分root和child。V1246的V76正Host FULL命中为
1473请求、145个context、94个workflow；child贡献约366.834GB，占报告需求约82%。
队列到服务观测最短10.075s、P50为15.574s。cached_tokens_host合计是重复需求，
不是独占页数、实际组件传输或forward消费证明；队列间隔包含准入/恢复/控制。
大量需求分布于多个agent，不能仅按单个工具长尾解释，也不能断言10s老化是原因。

下一步需独立隔离排序、生命周期与原生周转因素。原生prefill顺序消融仍是待实现
方案；已有FULL-only CPU候选只覆盖特定等待回收路径，尚未证明能解决后续大量
原生迁移。native在同工作负载没有同规模新增迁移，因此消除V76自身成本属于
工程修复，不能作为论文创新。完整V76/V77及质量证据继续按既定顺序收集。

### 4.22 原生顺序独立消融与 V76 6000 秒完成窗口

V1251复用上一节审计器，封存完整3600/5400/6000s全到达队列。到6000s均已到达
156项；结果如下，不能删除未完成、失败或guard标签再比较：

| 6000s观察 | V76 | V75 native | V75 predictive |
| --- | ---: | ---: | ---: |
| runner finished | 149 | 151 | 144 |
| recorded completed | 149 | 150 | 144 |
| 显式JCT可用completed | 143 | 141 | 141 |
| completed口径全队列截断平均逗留 | 1943.412s | 1654.351s | 1748.971s |

native已观测1项incomplete，另5项未结束；V76有7项未结束，predictive为12项。
上表终点和逗留语义沿用4.21，显式JCT标签不是任务正确率。5400s时V76/native
完成143/140项，而截断逗留仍为1907.026/1613.809s，说明单一时点完成数会改变
相对顺序，不能代替整个到达队列的时延/完成曲线。官方质量和最终结果仍待收集。

为隔离排序因素，在独立应用检出实现b698416并快进合入main，基线为eed5945；
不改引擎、agent输入、工具执行或终态。新开关BELIEFKV_NATIVE_PREFILL_ORDER
默认0，启动时设1后保留调度器传入的原生顺序，跳过因果/长度、驻留、已完成工作、
老化、已恢复及收尾优先级，并关闭该候选遍历中的自定义容量绕行。
标记请求仍需实时身份/会话/attempt、可见性及终态检查，未标记请求位置保持；
512个标记候选上限、semantic revision以及原生PrefillAdder准入和逐层恢复仍在。

该开关不关闭PREPARE、工具时长预测、事件、生命周期和完成回调，也不改变原生
物理分配器。每次规划清除旧promotion标记，原生模式不记录错误的排序hint消费；
低频状态新增prefill_ordering_mode，其他优先级布尔量继续表示原配置。
未来针对V76的无预取排序对照须保持handoff/预测H2D关闭，并继续使用53020ce3
引擎；若同时加入FULL-only候选或native-close，会混合排序与回收因素。
这里隔离的是整套候选排序/绕行边界，不是每个启发式的独立因果贡献。

新增20项用例覆盖默认路径、原生/未标记位置、启动配置、跳过排序工作、容量观测、
过期身份/终态/候选上限、准入计数、PREPARE/完成回调和实际模式记录。
V1248首次回归有4项旧用例失败；V1249在未修改main eed5945复现同样4项，原因
是测试假定关闭预取仍调用预取处理器。仅在两个测试夹具中显式开启被测回调，
未扩大生产派发。V1250对6模块共599项CPU回归全部通过，不覆盖GPU DMA或性能。
首次失败和原main复现回执均保留，不用最终通过覆盖原始证据。

V1252绑定干净候选、源码及三份CPU回执，核对V75/V76/V77应用与两份引擎
来源未变、29份已登记文件SHA256匹配；14:22 PDT的真实V76调度/驱动、V77及
两个质量等待进程仍在。没有登记新GPU实验或更改接续顺序。
先完整收集V76/V77与任务质量，再决定下一候选。排序消融用于排查自引成本，
尚未证明它能消除后续迁移，更不是新的论文贡献；原系统研究目标保持未验收。

### 4.23 重复 context 的 Host 需求集中度

V1253直接重分析V1246绑定的同一2400–3000s窗口行，不重读动态raw。
新增repeated_context_demand按workflow/context隔离并按服务观测排序，
将窗口内首个正Host观测与后续正观测分开。V1254对Host、完成窗口和比较器的
50项离线回归通过，其中4项新增检查覆盖排序、命名空间、缺失身份和空分母。

| 正Host需求观察 | V76 | V75 native | V75 predictive |
| --- | ---: | ---: | ---: |
| 正命中请求 / context | 1473 / 145 | 34 / 32 | 273 / 111 |
| 窗口内多次正命中的context | 137 | 2 | 75 |
| 同context后续正命中次数 | 1328 | 2 | 162 |
| 后续报告FULL需求 | 417.048GB | 0.262GB | 40.988GB |
| 占该段报告FULL需求 | 93.07% | 9.32% | 62.63% |

V76相邻正观测间隔P50为21.645s，1328次中1074次属于child、254次属于root。
这说明新增需求的主要规模落在同context后续访问，而不只是首次遇到冷context，
支持下一步优先隔离排序、缓存生命周期及请求周转。它不能证明排序已经是原因；
native只有2个重复观测，不能从其间隔分位数推导普遍行为。

窗口边界左删失，“首个”不等于context生命周期首次；两个正观测间可能有HBM
命中请求，不能称连续模型请求都恢复。context epoch及token前缀可变化，计量
没有物理page/generation证明，不把417.048GB全部算作重复搬运、可避免流量或
可减JCT。间隔包含生成、工具、准入和恢复，不是工具时长预测标签。
当前仍先完成V76/V77和质量收集，原生顺序仅CPU资格，不恢复高频预取、不把
修复自身缓存周转作为论文贡献。生产运行源码和已登记helper均保持冻结。

### 4.24 安全输入 checkpoint 与快速续接的 Host 需求

V1256重新封存原生事件前缀，在同一2400–3000s完整窗口，对每个正Host请求
关联立即前一同workflow/context请求及其完成事件。要求request ID和invocation
匹配、上一完成不晚于当前入队；前一安全checkpoint必须在旧输入内，并出现在
当前有效匹配前缀的节点路径上。中间存在未完成请求时不跳过它寻找更早“命中”。
V1257核验新旧窗口每个请求的既有V1246字段完全一致，并绑定来源与CPU回执。

| 正Host样本指标 | V76 | V75 native | V75 predictive |
| --- | ---: | ---: | ---: |
| 关联上一安全checkpoint / 正Host请求 | 1473 / 1473 | 34 / 34 | 273 / 273 |
| 上一完成→当前入队P50 | 0.400s | 547.129s | 0.505s |
| 当前入队→首服观测P50 | 15.574s | 6.387s | 10.898s |
| 1s内续接的正Host请求 | 1273 | 7 | 199 |
| 这些请求的报告FULL需求 | 389.613GB | 0.935GB | 47.737GB |

V76大部分正Host需求发生在上一轮结束后很快入队、且旧安全checkpoint仍在当前
匹配路径上的续接中。输入格式改变本身不足以解释这个队列；排序、等待期间驻留
变化和原生请求周转应优先隔离。这个结论针对Host命中队列，不能外推全请求
快速续接的miss率，native正Host样本也不能代表其全部请求的闲置分布。

上一完成到当前入队包含客户端交付、真实工具/JOIN和应用工作；入队到服务观测
包含准入、恢复和控制。不能相减两个分布中位数分摊时间，也不把15.574s当H2D。
checkpoint完成记录只含node ID及prefix长度，缺creation identity；当前路径观测
不能证明同一物理page、组件forward复用或全部Host需求都可避免。关联也不排除
checkpoint之后的输入变化，更不能据此量化重算。

V1255对3个离线模块共63项回归通过，新增13项检查覆盖epoch推进、安全前缀、
缺失完成、错ID/不同invocation、时间重叠、缺路径、越界及中间未完成请求。
本轮没有改变运行中应用、引擎、工具或GPU登记。V76已观测155个runner结束，
最后一个仍有真实模型服务；V77和官方质量接续继续等待。原生顺序开关保持
独立CPU资格，完整质量/JCT和原生相对收益仍待验收。该项是工程归因证据，
不是新选定的论文机制；既有相关工作约束不变。

### 4.25 已结束队列的更晚时点覆盖与 V76 原始均值下界

固定观察窗口审计此前要求最后一条到达/结束事件时间不早于比较时点，导致native
全部计划任务已结束后，仍拒绝与V76更晚时点比较。现仅在全部计划任务的真实
runner结束都已落在观测前缀内时允许后续时点，保留实际日志时间及完整队列标记；
不补造心跳或无限时间。未完成计划任务和超出观测的结束仍不能充当完整性证明。
失败/未知结果在completed口径中继续累计到观察时点，未从分母删除。

V1258的66项离线回归通过，含3项新增覆盖/失败标签检查；与V1255的63项重叠。
V1259重新封存同表6000/7000s完成窗口，6000s数据与V1251一致。7000s观察为：

| 全156项队列指标 | V76 | V75 native | V75 predictive |
| --- | ---: | ---: | ---: |
| runner finished | 154 | 156 | 153 |
| recorded completed | 154 | 153 | 152 |
| runner口径截断平均逗留 | 1963.595s | 1666.324s | 1798.399s |
| completed口径截断平均逗留 | 1963.595s | 1679.550s | 1799.569s |

native在7000s前全部已结束，原始runner均值此后不变。V76仍有2项未结束，
其runner截断平均逗留为最终同口径均值的下界，已经比native高297.271s。
因此最后任务结束不能让本轮原始全队列runner均值反超native；不能只等待长尾
结束后期待这个原始比较逆转。当前实时已观察155项结束，与7000s的154项不同。

这个下界严格限于含setup/收集和失败终点的runner口径；native3项incomplete、
guard标签和任务质量差异均保留，不能把结论改称质量匹配JCT或策略因果差距。
completed口径对失败继续计龄，也不能套用runner均值在结束后不变的性质。
V76/V77与质量接续继续冻结；原生顺序消融及已有回收对照仍用于后续隔离原因，
本轮没有新GPU登记或已成立的论文贡献。

### 4.26 原始来源身份更正

V1260保存原始HTML、实际标题和SHA256：ReplaySSM是2610.13778v1，
标题为“ReplaySSM: Cache SSM Inputs, Not State”；2608.20961v1实际是
“TreeWY: Speculative Verification for Gated DeltaNet Hybrids”。
旧V1199错误的文件名、README和指纹原样保留，以本节及R14/R15更正当前引用，
不再把TreeWY的内容称为已核对ReplaySSM。

同批来源包括Hydragen [R16]和Semantic Parallelism [R17]；Sem-MoE是后者
的框架名。共享前缀attention分解已有强方法，不能把一般共享计算作为新意。
专家并行通信方法还需区分当前TP1/EP1、无all-to-all的配置。此次只完成来源
身份和方法边界核对，没有复现论文性能，也没有确立新的workflow系统贡献。

### 4.27 普通 ReplaySSM decode 的预算修复候选

当前引擎普通decode的d/k/g ring按持久Mamba槽分配，原预算路径仅在
spec-verify启用时计入ring。独立候选91a9359/treef231ed24补齐普通decode
每槽ring、padding和剩余内存核算；联合legacy verify scratch仅在主状态侧
计入ring，保持spec专用scratch独立。关闭该功能时保留原有容量推导。

V1261和V1265保留原始复现失败；V1263的39项CPU检查及8个subtest通过，
包含真实CPU MambaPool的GDN/KDA、FP32/BF16、不同ring长度、自动/显式/
radix关闭容量和legacy verify scratch。V1264绑定提交、代码、测试和增量patch，
在临时Git index中从完整53020ce3精确重建候选树，反向应用检查也通过。

该patch已纳入主线保存，未被launcher选用，也未启用新的GPU候选。当前实验
enable_linear_replayssm=false，因此该预算遗漏不是V76退化的原因；当前
extra_buffer与普通ReplaySSM还不兼容，保留原生拒绝检查。CPU预算资格不覆盖
GPU峰值、数值正确性或性能。这是工程修复，不是新论文机制。

### 4.28 V76 后处理恢复与 V77 真实接续

V76全部156个workflow已完成收集，recorded completed为156，保留13个既有
guard介入标签，官方质量仍未完成。原driver因最终历史比较器硬编码当前臂名
predictive_h2d而退出2；65503eb改为接受真实当前臂，同时要求正确native引用
和单一当前运行目录。恢复后补齐原缺失的native_reference_comparison.json，
没有把reactive改名，也没有改写原driver_failed/exit2状态。

V1267–V1269记录完整任务/终态、同表/容量、遥测、旧进程退出和冻结来源核验，
新wrapper仅向原V77 helper提供带失败来源的已验证收集/报告完成凭据。
V1270的117项CPU检查通过；与较早72项有重叠，不相加。V77应用52a2b8e、
引擎76cf97d2及原启动检查不变，没有加入本轮ReplaySSM或FULL-only候选。

V77于2026-10-16 15:20:49 PDT接续，V1216在15:23:51核验真实首服，
scheduler2646481/start_ticks635952682，native close GC启用、原生准入、
同156项到达表与物理池，V1219记录NUMA放置。此前“V77等待”是历史状态。
官方质量队列已开始评分，尚无完整质量结果；完整性能和系统贡献继续待验。

### 4.29 V77 首个完整 600 秒窗口

V1271沿用现有service-window分析器，V1272核对正Host需求，V1273按全部应到任务
统计完成窗口。V77与V75 native的物理池/应用/到达配置匹配，但实际模型工作不同，
且V77与单核官方质量评分重叠；不是正式重复对照或GC因果验收。

| 0–600s观察 | V77 native-close | V75 native |
| --- | ---: | ---: |
| decode token/s | 946.62 | 932.72 |
| 时间加权decode batch | 23.08 | 23.66 |
| prefill worker累计 | 117.517s | 123.824s |
| 入队uncached输入token | 3,642,214 | 3,650,788 |
| D2H | 102.134GB | 139.944GB |
| H2D | 0 | 0 |
| 600s全部应到任务 | 42 | 42 |
| recorded completed | 9 | 7 |
| runner截断平均逗留 | 271.664s | 272.993s |

V77实际关闭回收已生效，但释放量和本窗口D2H差额不能直接充当省下的关键路径
等待。没有H2D只描述本早期窗口，不能用来否定后续压力，也不能说已经超过native。
服务窗口沿各臂首GPU采样计时，任务完成窗口沿runner到达时钟计时，各保留原始
起点，不把两个600s窗口拼成相同的逐请求关键路径区间。
旧native窗口复用V1229，避免重新分析同一已封存报告；V1276绑定文件。
V1271现有CLI没有逐文件前缀摘要，保留为开发观察，不能称正式来源完整性验收；
V1272的Host和V1273的到达/结果核验各有独立封存凭据。继续冻结V77与质量队列。

随后V1277补齐最新600–1200s窗口，三侧均完整覆盖：

| 600–1200s观察 | V77 native-close | V75 native | V76 reactive |
| --- | ---: | ---: | ---: |
| decode token/s | 1211.45 | 1212.88 | 1323.36 |
| 时间加权decode batch | 42.75 | 44.27 | 46.51 |
| prefill worker累计 | 144.692s | 148.019s | 123.035s |
| D2H | 168.700GB | 226.189GB | 182.522GB |
| H2D | 0 | 0 | 4.171GB |

V77/native的D2H流时间累计20.904/32.441s，decode几乎持平，实际输入/生成/
batch不同。这一结果不支持仅凭D2H差额宣称吞吐收益，也不能把11.537s直接
扣成JCT。V76该局部窗口输出较多并未避免后来整轮原始耗时落后，进一步说明
局部吞吐不能替代全队列完成、质量和最终性能。V1277同样保留现有CLI的来源
前缀限制，未修改任何运行中代码或登记新的GPU任务。

### 4.30 快速续接的完整已服务请求分母

此前V1256只包含正FULL Host样本，不能判断全部快速续接中有多大比例需要Host。
现补充served_request_queue_cohort：native的llm_submit在第一次launch才发出，
ts_ms仍为早先入队时刻。本新队列按入队窗口计入所有已观测服务的请求，包括零/
未知FULL Host计数；未服务请求不在其中，首次服务可在入队窗口以后。
它与原按first-service观测时刻选择的正Host窗口是不同队列，不能共用分母。

V1275重分析同一2400–3000s入队窗口：

| 已服务请求队列 | V76 reactive | V75 native | V75 predictive |
| --- | ---: | ---: | ---: |
| 全部请求 | 3,902 | 4,373 | 4,427 |
| 正FULL Host计数请求 | 1,536 | 34 | 274 |
| 正计数比例 | 39.36% | 0.78% | 6.19% |
| 前次完成后1s内入队请求 | 3,243 | 3,475 | 3,249 |
| 其中正FULL Host计数 | 1,328 | 7 | 199 |
| 快速续接中的正计数比例 | 40.95% | 0.20% | 6.12% |
| 快速续接报告FULL需求 | 408.623GB | 0.935GB | 47.737GB |

三侧本队列的Host计数均有记录；未知仍在代码中独立保留。有效续接要求立即前一
同workflow/context/invocation请求已完成，中间有未完成请求不可跳过。V1274对
Host/完成窗口/比较器85项CPU检查通过，其中13项新增；V1275旧服务窗口的
summary/rows/角色/工作流/context/重复需求/checkpoint字段与V1256逐项一致。
原1473项服务窗口与新1536项入队队列的区别正是窗口口径，不是篡改旧结果。

这补强了“快速续接仍大量落到Host”的规模证据，不能单靠客户端长等待解释，
但仍未证明相同物理页、每字节可避免或额外排序的因果贡献。V76全156项原始
duration均值1974.421s，native1665.374s，质量仍在评分，不能只用预取关闭后的
CPU减负宣布目标完成。下一独立GPU判断优先使用已有native-order候选隔离排序/
自定义容量绕行，其余PREPARE/生命周期、53020ce3引擎与工作负载保持。
不叠加FULL-only、关闭回收或ReplaySSM，避免无法归因；当前没有登记新GPU。
如果原生顺序下该比例仍高，继续核对实际准入和状态回收，不能先把排序写成根因。

### 4.31 V78 原生顺序独立候选已登记等待

已有默认关闭的原生顺序候选现补齐真实启动入口。应用56f2c5e冻结在独立
beliefkv-v1278-native-order-gpu-20261016，复用精确53020ce3引擎和batch32。
V78计划相对V76只改变有效排序/自定义容量绕行及其版本/来源元数据，保留
156项任务、Poisson15s/root、seed21、模型/工具/prompt、PREPARE、工具时长、
终态回收、全部资源和原生需求恢复，预取/handoff仍关闭。
源码相对V76有两份生产文件差异：既有原生顺序分支，以及默认关闭输出IDs/
可选关闭回收计数的遥测变化；不声称逐字节单因素或严格因果对照。

V1280对7模块324项CPU检查通过，含40项新驱动、20项原生顺序、5项无预取、
151项遥测、31项比较器、26项接续环境及51项后处理检查。既有599项顺序资格
通过runtime源码指纹复用，不能与重叠套件相加。V1281绑定应用/引擎/CPU来源，
V1282冻结计划及环境，V1285记录真实接续监督3412628/start_ticks636206265，
2026-10-16 16:03 PDT启动后实测存活。当前状态waiting_for_complete_v77，
尚无V78 GPU服务。原V77及官方质量队列继续，不修改已登记helper或运行代码。
完整计划另存V1287，字节及摘要与V1282登记的raw/ab_plan.json完全一致。

新入口只在完整V77成功收尾、原监督/driver真实退出、GPU/资源与端口释放后
冷启动reactive单臂，用同配置V75 native作历史开发参考。启动核验实际首服、
prefill_ordering_mode=native、正执行计数、同表/容量/NUMA和无预测H2D，
旧resident_first等字段保留为配置。结束后保留全156项、失败/guard标签、HTML、
传输与历史比较；质量仍需官方评分。V1283/V1284/V1286是未来凭据，未生成。
不叠加FULL-only、native-close或ReplaySSM。该项用于排除自身工程损失，
不是选定论文贡献，也不以CPU通过或接续进程存活代替端到端性能。

### 4.32 V77 至 2400 秒及 V76 恢复突增边界

V1288新读V77的1200–1800/1800–2400s完整服务窗口，V1289复用V1243的
历史封存结果作比较基础，V1291另封存1800–2400s的Host与续接队列。
V1292使用全应到任务的到达/终态凭据，不把未完成任务从分母删除。

| 1800–2400s服务观察 | V77 native-close | V75 native | V76 reactive |
| --- | ---: | ---: | ---: |
| decode token/s | 1300.05 | 1296.69 | 1317.23 |
| 时间加权decode batch | 47.46 | 47.43 | 47.47 |
| prefill worker累计 | 118.737s | 108.095s | 121.316s |
| D2H | 169.214GB | 206.565GB | 174.662GB |
| H2D | 0 | 0 | 6.089GB |

1800s的111项应到任务，V77/native/V76完成49/49/40项；2400s的149项完成
64/64/54项。2400s的runner截断平均逗留为732.122/744.667/828.759s，
不是最终JCT或质量匹配收益，且不能与GPU服务窗口拼成相同关键路径。
V77与native本窗口均无正FULL Host服务样本；V76有33项，其中入队队列的
1s快速续接仅1/3756项为正计数。下一2400–3000s旧V76窗口却有1473个正
Host服务样本，说明该现象具有明显阶段性，不能用全程平均或早期吞吐解释。

进一步复核V1275的正Host服务样本：V76的1473/1473项入队→首服观测均不少于
10s，native为0/34项；V76中1s内客户端续接的1273项仍全部排队超过10s，
P50为15.601s。冻结V76 runtime的waiting_rank确有10s老化分支，当前原生
顺序候选会跳过这组额外排名。这是需要GPU消融验证的关联，不能因时间接近
便宣称该阈值已被证明造成缓存失效，亦不能将所有报告Host字节认作浪费。
若V78的额外恢复和排队不下降，应否定这一解释并继续查准入/实际工作集。

随后V1293/V1294/V1295补齐V77的2400–3000s完整窗口，出现重要反证：

| 2400–3000s观察 | V77 native-close | V75 native | V76 reactive |
| --- | ---: | ---: | ---: |
| decode token/s | 1093.70 | 1304.05 | 1009.65 |
| H2D | 174.643GB | 7.190GB | 637.786GB |
| 正FULL Host服务样本 | 416 | 34 | 1473 |
| 1s续接队列中正FULL Host请求 | 332/3367 | 7/3475 | 1328/3243 |
| 该队列正计数比例 | 9.86% | 0.20% | 40.95% |

V77明确没有额外工作流排序，却也出现显著恢复与吞吐下降，因此额外排序不是
该现象的必要条件，不能把10s关联升级成唯一根因。V78仍用于测量排序/容量
绕行的贡献，同时需要沿终态回收、有效FULL/Mamba前缀、实际准入和工作集核对。
单轮轨迹/资源不同，当前也不能直接宣称GC导致全部退化。V77的416个正Host
样本均可关联立即前一完成请求的安全输入checkpoint到当前匹配路径；这不是
逐页/代际复用或“节点被删除”的证明，不能先按某种缓存故障命名。

V1297在runner日志真实覆盖3000s后保留全部156项，V77/native/V76完成
74/83/66项，runner截断平均逗留1032.496/1027.185/1148.225s。V77先前
2400s的小幅均值优势未延续，不等待长尾后再以早期窗口宣称成功。此前同数值
时点的首次任务审计被覆盖检查拒绝，V1296没有生成；没有放宽规则或改造日志。
质量仍在评分，完整JCT/有效吞吐与新的系统贡献继续未验收。

### 4.33 恢复压力、实际上下文与准入阶段成本

V1301新增只读审计，复用已有geometry读取器，按经过验证的native/client时钟
连接2400–3000s完整服务窗口、既有客户端压力和Host采样。V1300的30项检查
覆盖新审计器及已有geometry分析；V1298的初始7项与之重叠，不能再相加。
首次V1299把同流eviction事件误计为未知occupancy样本，原件保留，由V1301
严格筛host_pool_usage更正；geometry及已知占用值没有变化。

| 2400–3000s观察 | V77 native-close | V75 native | V76 reactive |
| --- | ---: | ---: | ---: |
| 观察到的decode平均上下文token | 22105 | 22545 | 21356 |
| 48请求、22528-token桶worker均值 | 21.678ms | 22.268ms | 21.877ms |
| 47请求、22528-token桶worker均值 | 103.411ms | 50.630ms | 120.778ms |
| 客户端排队计数采样P50 | 59 | 51 | 76 |
| 活跃FULL占用比采样P50 | 0.458 | 0.464 | 0.436 |
| Host FULL占用比采样P50 | 0.999900 | 0.999894 | 0.999936 |
| Host Mamba占用比采样P50 | 1.0 | 1.0 | 1.0 |

表中decode分桶均保留实际graph=true。满48区间接近，47等换批区间明显不同；
worker区间含调度/结果处理，不能认作47请求kernel本身慢一倍。上下文是可见
batch/request统计，不是去重后的物理工作集；分桶也不是相同请求或相同计算。
原生metrics明确used=total-available-evictable，当前客户端resident_pressure
由num_used_tokens/FULL容量计算，排除可驱逐缓存。表中约46%不代表HBM有54%
空闲，亦不提供Mamba总驻留。不能据此将恢复增加归为“容量明明足够”的策略错误。
V77边界采样间实际关闭175个session、释放1589469个设备FULL token及359个
设备Mamba槽，但这些累计值不证明它们与后续恢复为同一物理页，也不能证明GC
是恢复突增原因。

V1303先复用既有prefill阶段分析，V1306进一步逐请求连接原生llm_submit
Host计数与同clock domain/forward/request的短prefill采样。短请求定义为
单请求、实际native extend<512 token；不是按旧缺省字段猜测。V1305的23项
检查覆盖7项精确关联用例及16项已有phase分析，与V1300合计53个不同CPU用例。

| 短prefill采样及选批线程CPU | V77 native-close | V75 native | V76 reactive |
| --- | ---: | ---: | ---: |
| 所有短prefill样本 | 98 | 124 | 112 |
| 正FULL Host组样本 | 11 | 0 | 37 |
| 正FULL Host组选批CPU均值 | 158.750ms | 未采到 | 89.180ms |
| 已知FULL/Mamba均零组样本 | 87 | 124 | 75 |
| 已知零组的选批CPU均值 | 5.314ms | 3.694ms | 6.919ms |
| 正FULL Host组占采样选批CPU | 79.07% | 未采到 | 86.41% |

关联按完整request ID，不用时间最近样本替代；零FULL但缺Mamba观测的情况单列。
正Host少量样本集中了大部分采样选批CPU，因此下一步优先分解原生需求恢复、
为其分配容量时的驱逐/写回与同步路径，而非仅增加预取扫描或继续查controller。
这仍只是关联：采样选批包括全部native batch准备，不提供其中各函数独占时间；
线程CPU可含底层驱动工作，不能直接称全为Python算法或拷贝等待。
一轮16步采样不外推为全程成本，亦不能把158ms当每次H2D可节省的JCT。
V77零Host组的run_batch线程CPU仍为54.398ms，native为39.538ms，说明恢复
关联尚未解释全部forward差距。须继续核对实际prefill geometry和执行路径，
不能将这两组不同请求的均值差直接归因关闭回收。

### 4.34 V77后续窗口与首份官方质量结果

V1302新读V77的3000–3600/3600–4200s完整服务窗口；V1304另读同配置历史
V75 native/V76的3600–4200s，未启动新的native GPU。V77 H2D从前一窗口
174.643GB降为3000–3600s的4.267GB及3600–4200s的2.763GB。突增是阶段性
现象，不能将其外推到整段稳定高压。

| 3600–4200s服务观察 | V77 native-close | V75 native | V76 reactive |
| --- | ---: | ---: | ---: |
| decode token/s | 1307.888 | 1212.677 | 1268.590 |
| decode加权batch | 46.765 | 38.553 | 46.293 |
| 排队计数均值 | 9.382 | 0.674 | 15.531 |
| H2D | 2.763GB | 1.108GB | 5.197GB |
| D2H | 154.155GB | 207.155GB | 166.705GB |

该窗口V77原始decode较高，但native已接近无排队，剩余工作和有效并发不同。
不能因此宣称GC加速或掩盖前段落后。V1307使用runner到达/完成时钟保留全部
156项：3600s三臂完成89/105/80项，runner截断逗留1313.327/1267.947/
1476.103s；4200s完成116/124/115项，截断逗留1522.430/1426.191/
1704.592s。完成样本、资格和未知项单列，均非最终或质量匹配JCT。

原单worker官方评分队列已完成V76 reactive，156项严格分成101 resolved、
54 unresolved、1 empty patch，无evaluator error/incomplete。空patch为
django-12858，不能遗漏后以155作分母。101/156约64.74%是官方正确率，
native和predictive评分尚未齐，不能宣称质量匹配。
所有156项原始outcome为completed，其中11项原生JCT资格为false，保留既有
guard/语义终态原因；97项同时正确且原生JCT合格。冻结scorer要求完整原生JCT
资格，故维持measurement_complete=false及有效完成吞吐null，不更改标签或
剔除这些任务。评分完成与性能测量完整是两个条件。

V1308绑定V1298–V1307输入及官方摘要/原始summary摘要，并于16:48 PDT核对
真实进程incarnation：V77监督/driver仍活跃，V78仍等待完整V77，官方worker
正在评分V75 native，V77质量等待器继续。V77/V78及质量helper保持冻结。
本次变更只含离线审计、测试和文档；研究目标未完成，具体论文机制仍未选定。
继续独立V78消融，并依据上述关联分解原生恢复的实际CPU/同步成本；不得把
工程修复、池配置或一轮局部速率当作新的系统贡献。

### 4.35 选批中的同步写回与独立跨分支合并候选

V1310新增只读物理receipt对齐。来源为V1306已精确配对的短prefill样本、
原压力/时钟来源及stat封存的transfer_telemetry前缀。按direction/command ID
去重检查，不用相近时间的另一次传输替代目标；只报告选批内提交及是否在其
结束前ACK，未知/未完成receipt不充当成功。V1309的5项CPU检查通过。

| 正FULL Host短prefill样本的选批区间 | V77 native-close | V76 reactive |
| --- | ---: | ---: |
| 样本数 | 11 | 37 |
| 有D2H提交的样本 | 11 | 37 |
| D2H批次，均在选批结束前ACK | 43 | 108 |
| 记录的D2H节点数 | 590 | 1375 |
| D2H字节 | 6.372GB | 15.792GB |
| D2H transfer-stream累计 | 1.440s | 2.842s |
| H2D批次 | 11 | 37 |
| H2D字节 | 5.614GB | 15.809GB |
| 选批结束前完整H2D ACK | 0 | 0 |

原生`UnifiedRadixCache._evict_components`在需要write-back的设备驱逐上调用
`writing_check(write_back=True)`，后者对finish_event执行synchronize，再释放
容量；恢复自身则通过controller.load排队。表中支持进一步检查“为恢复先腾
空间”的同步链，而非把整个长尾都称H2D传输慢。H2D已提交而ACK尚未返回，
不能改称尚未传输。时间对齐没有函数栈，仍不证明每批由哪次分配调用触发；
stream含依赖和dispatch间隙，三种时间不能相加、相减或外推成可减JCT。

现有V75/V76批处理只把满足原生优先次序的一条祖先链合并；遇到另一分支更早
的victim就停止。为减少多分支树上的重复提交/阻塞，建立独立CPU工程候选：

- 应用主目录仅保存增量patch和证据；引擎独立检出为
  `sglang-v1311-frontier-writeback-cpu-20261016`。
- 基线snapshot0fa4118的完整tree为已使用的53020ce3，不叠加native-close、
  waiting-FULL-only或ReplaySSM。候选commit98b2670、treec8424eb3。
- `SGLANG_HICACHE_WRITE_BACK_BATCH_FRONTIER`默认关闭，节点预算仍由原参数控制。
  开启时，规划器按原堆优先级合并不同分支及虚拟暴露的父节点。只查看二叉堆
  需要的头部，不额外复制整个原生堆；不在规划阶段改变实际节点、锁或堆。
- 仍使用原生backup、CacheOperation合并与ACK。成功入队的前缀统一ACK后才
  demote；无Host容量、部分成功、异常及ACK失败均保留原保护。修复批量释放后
  每个分支父节点的堆入口，避免只保留最后一个分支。

V1314最终196项引擎CPU检查通过，其中34项新用例。真实CPU cache/allocator
按2种缺额、8种优先级seed与single/chain/frontier三模式比较，实际设备victim
前缀顺序、释放容量和Host页映射一致；真实FULL/Mamba夹具另检验两池映射。
补充部分Host容量、enqueue异常、ACK失败及4096候选中只读所需堆头的检查。
初始V1311的19项失败来自未初始化的假游标及重复复用模拟controller；
V1312剩余两项是测试在后续victim未backup/ACK前直接demote。修复夹具后，
未放宽生产保护或改判失败；原记录保留，V1313/V1314不按重叠用例累加。

V1315另运行32组真实CPU FULL树夹具，三模式都释放192节点/384token、顺序
相同，模拟物理提交single/chain/frontier为192/135/32。这不是实际trace中的
可合并次数，更不是吞吐改善比例；小CPU池和模拟DMA不提供GPU/PCIe/JCT证明。
并批时同时保护多个Host目标，会改变暂存/Host回收机会；multi-rank和实机
影响未验收。V1316绑定源码/CPU/patch，并用临时index从53020ce3精确重建
c8424eb3；增量patch未替换规范staging patch，也没有注册新GPU。

该候选属于共用路径工程优化。若在独立V78后选择实机验证，native和新策略
必须采用同一共用引擎；不能把修复基线或减少同步本身冒充workflow独有创新。
现阶段既没有选定新论文机制，也没有恢复旧三机制为既定贡献。

### 4.36 最新V77任务窗口及运行边界

V1317新读runner实际日志覆盖的6000s窗口，保留全部156项。V77/native/V76
记录completed为149/150/149，runner已结束149/151/149项，native另1项
incomplete；原生JCT合格completed分别147/141/143。runner截断逗留均值
1769.327/1654.199/1943.412s，资格与失败分开，不是最终或质量匹配JCT。
V77在更晚任务窗口仍落后于native，不能由恢复后段变少或局部decode较高宣布
关闭回收取得净收益，亦不能仅因负结果停止完整收集。

V1318绑定本轮所有新输入及限制，17:17 PDT逐一核验真实process incarnation：
V77 scheduler2646481、监督2645733和driver2646017活跃；V78监督3412628
仍等待完整V77；原官方评分监督3806721/worker3802150正在评分V75 native，
V77质量等待器1121673继续。分别从两份冻结检出执行verify_source成功，
原app、engine、plan、helper和CPU凭据均保持。下一GPU仍按原V78登记，
不得在等待期间偷偷替换为跨分支候选。研究及完整性能/质量目标继续未验收。

### 4.37 写回与完整恢复读取之间的数量边界

V1319/V1320新增独立只读审计，分别选取前3000/6000s提交、已完成的D2H，
再把同一臂封存前缀内的全部已完成H2D都算作可能读取，即使读取早于写入、
位于cohort之外，或是同一Host源向cache和request CoW的重复目标复制。
按direction/command ID拒绝重复receipt，验证时间、pool单位数与实际字节
完全一致。8项专用检查及7项既有依赖检查通过；来源、时钟和完整行前缀摘要
保存在报告中。表中GB均为十进制，读取范围是各臂完整观测前缀，并非6000s窗。

| 臂 | 前6000s FULL写回GB | 前6000s Mamba写回GB | 全部FULL读取GB | 全部Mamba读取GB | FULL来源份数上界 | Mamba来源份数上界 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| V75 native | 806.130 | 1071.564 | 3.952 | 9.658 | 0.490% | 0.901% |
| V77 native-close | 599.404 | 956.694 | 123.417 | 61.170 | 20.590% | 6.394% |
| V76 reactive | 1258.966 | 1185.919 | 775.320 | 334.952 | 61.584% | 28.244% |
| V75 predictive | 581.635 | 1017.284 | 73.851 | 53.507 | 12.697% | 5.260% |

边界的对象是已完成物理写副本：设某池cohort写入W份、全部读取目标为R份，
最多min(W,R)份不同写副本能作为这些读取的来源。每次Host读只使用一个确定
版本，重复目标只能放宽这一上界。规范Mamba源码BACKUP_HOST复制完整状态，
LOAD_BACK可从同一Host源复制到cache和request两个目标，因此按目标计数尤其
保守。它不需要假定node ID等于物理generation，也没有给具体checkpoint贴上
“未复用”标签。FULL单位20480字节，Mamba单位64389120字节。

这些比例不是cache hit rate、逐页复用率或在线可安全丢弃集合。正在传输的
部分层、未完成读取与封存之后的未来读取不计；低比例也不能否定释放HBM、
保护未来恢复的价值。流时间含依赖/dispatch，写入GB不能直接换算可减JCT。
V75/V76/V77的实际模型路径与工作集不同，不能由表中差额宣称策略因果收益。

文献重新核对收紧了新颖性边界：Marconi [R6]已明确报告其测量中仅0.4%的
SSM缓存状态被复用，并按输入共享/最终输出前缀做checkpoint准入；EfficientAgent
[R10]已有Host reuse-working-set估计与容量条件写入准入。因此“大量Mamba写后
未读”“少备份”“只留有用checkpoint”均不足以成为新贡献。当前源码也已经保留
安全输入session leaf，不能把这一既有功能再当新机制实现。

后续先回答两个可证伪问题：哪些写入在当时已有可识别的失效/续接依据，以及
Marconi式checkpoint准入与EfficientAgent式Host准入的合理组合能否拿到全部收益。
若简单组合已足够，作为共用工程对照收尾；若仍有可观的动态fork/join独有成本，
再据精确身份、消费和关键路径证据选择机制。暂不添加新的GPU候选，不把减少
传输量本身当论文验收，也不恢复已撤回的旧三机制为既定方向。

### 4.38 V77完整终态、native门槛与V78接续恢复

2026-10-16 17:40 PDT附近，V77最后一个django-16938结束，runner的submitted/
started/finished均为156。最终summary为155 completed、1 incomplete，无workflow
异常；原始均值1814.634s，对照native1665.374s，采集时长8276.154/6841.743s。
这仍是包含全部终态的原始耗时，不是官方质量匹配JCT。两侧实际多轮workflow为
31/30个。完整V77的D2H/H2D为1672.269/184.587GB，native为1935.037/13.610GB；
少写约262.768GB没有转成该口径性能优势，不能证明关闭回收已成功。

唯一incomplete为django-12708：children和JOIN均完整，root仍在解释修复与测试
矛盾，既有graph-budget finalization/guard介入；最后正文未形成语义完成。
deadline未到期，无取消，不将该任务改记自然RETURN或从分母移除。V77中native
JCT合格共148项，另7个completed同样保留既有guard/语义终态不合格标签。

原`run_deepagents_swebench.py --gate native`在合格数不等于156时返回exit1。
冻结V77监督将此当作采集失败，在执行后处理前停止；原V78随后按原规则停止。
这是“完整采集”和“native验收通过”的接线混淆，不能靠放宽论文/质量门槛恢复。
V1324的33项CPU检查含18项恢复用例、8项读写审计及7项依赖检查，保留不同
终态/资格，拒绝缺任务、重复finish、异常、缺startup、未知退出和未排空遥测。

独立`resume_v78_after_complete_v77_20261016.py`逐项核对156份result与summary、
全部runner结束、真实进程退出、已冻结源码和startup、遥测零drop/failed及队列
排空，再调用原V77 postprocess。V1321封存原始失败state、exit1、全部标签及
独立恢复来源；原失败state不覆盖。完整HTML和同任务/同物理容量比较均已生成，
原V1217完成凭据只表示完整开发采集和质量未评分，native_gate_passed仍为false。

V1322/V1323登记独立接续，17:49 PDT启动监督230068/start_ticks636843838，
driver230265/start_ticks636843929真实存活。新动态state为
`ops/v78_native_order_recovered_20261016.state.json`；旧停止state原样保留。
仍由原冻结V78 helper验证原56f2c5e应用、53020ce3引擎、参数、来源与资源，
只替换等待的已验证collection状态和监督输出路径。没有修改运行源码、策略、
工具、到达表、质量门槛或V77/V78冻结文件。该次尝试随后因独立观测器参数错误
被原监督终止，未生成V1283；下节记录新目录重试，不能将旧尝试视为完整实验。

### 4.39 NUMA观测器的reactive兼容与V78b新目录重试

V78实际原生排序已出现，但首服验证调用旧`observe_v55_numa_placement.py`时，
argparse的choices只含native/predictive_h2d，拒绝reactive并返回2。冻结监督
按原错误分支停止其driver。17:52后的旧监督/driver均已退出，V1325封存失败
state/log及关联V1322来源；原V78部分raw不删除、不覆盖、不冒充全量完成。
这属于观测/启动接线缺陷，不是GPU策略错误或新颖性证明。

独立`observe_owned_scheduler_numa.py`要求显式run-root，支持三种实际臂名，
继续验证同用户scheduler PID/start_ticks、容量census与读取前后进程存活，
记录原始/proc NUMA文本摘要和实际驻留字节。它不迁移内存，也不把large map
当作准确pool身份或带宽证明。旧已登记observer文件保持不变。

V1332最终39项CPU检查通过，含6项新observer/重试检查及前述33项。先前V1331
的同39项通过记录保留；首次prepare的Path类型接线在注册前修正，未启动GPU，
最终凭据只绑定修正后的源码，不把两个重叠suite相加。

V1326登记新run目录`qwen35_native_reactive_online_poisson_156root_20261016_v78b_native_order`。
原应用56f2c5e、引擎53020ce3、原V78计划的全部字段和环境逐项一致，唯一允许的
环境差异为RUN_ROOT。独立入口继续调用原冻结helper的资源、启动、监控和后处理；
只把其只读NUMA观测命令定向到新增observer，注册并校验两层helper及新observer
的来源摘要。它没有跳过startup、弱化容量/实际排序条件或修改runtime/harness。

V1327于17:57 PDT启动监督243510/start_ticks636892226，新state是
`ops/v78b_native_order_20261016.state.json`。V1328于18:00:31 PDT核验真实首服、
正native排序计数9、全部到达表/物理容量和PREPARE/工具时长保留、预取关闭；
V1330完成实际NUMA观察。18:01再次验证冻结来源与环境，native排序累计370，
额外排序/预取计数均0，physical_disabled=false。scheduler244015/
start_ticks636893629及driver243802/start_ticks636892336真实存活。该证据
证明启动配置，不能外推完整性能或因果收益；V1329仍留给完整结束。原官方
质量队列继续，原始负结果与研究目标均未改判。

原质量队列于17:50 PDT完成V75 native官方评分，原摘要
`v1182_v75_native_quality_inputs_20261016/official_quality_summary.json`保留
全156项：95 resolved、59 unresolved、2空patch，无evaluator error/incomplete。
空patch为django-11490/13028。冻结评分器的correctly_completed_ids为90项，
measurement_complete=false、有效完成吞吐为null；原3个native incomplete及
resolved但未正常完成任务均保留。V76为101 resolved、54 unresolved、1空patch，
不能仅比较resolved个数宣称性能或质量匹配JCT优势。真实worker231698/
start_ticks636850048正在评分V75 predictive，V77质量等待器1121673继续。

### 4.40 写回角色的可识别范围与默认关闭的路径观测

V1334在V1320同一封存transfer前缀与0–6000s写入cohort上，关联原生请求事件。
只使用D2H submit之前严格更早的事件；新请求开始、空/缺失snapshot会把先前
锚点的当前状态改为unknown。只有单节点且Mamba单位为1的receipt才赋角色，
多节点/分池身份不能确定的部分单列。V1333的26项CPU检查含11项新用例，
覆盖未来信息、同刻事件、共享锚点、新请求、未知snapshot、冲突及重复证据。

| 0–6000s Mamba写副本 | V75 native | V77 native-close | V76 reactive | V75 predictive |
| --- | ---: | ---: | ---: | ---: |
| 总份数 | 16642 | 14858 | 18418 | 15799 |
| 可关联单节点份数 | 16298 | 13842 | 14202 | 15158 |
| 最后观测仍为当前安全锚点 | 1014 | 37 | 173 | 174 |
| 先前安全锚点、当前状态未知 | 27 | 38 | 164 | 43 |
| 先前安全锚点已被观测到的新锚点替代 | 369 | 24 | 345 | 419 |
| 此前未被观测为安全锚点 | 14888 | 13743 | 13520 | 14522 |
| 无法分配单节点角色的份数 | 344 | 1016 | 4216 | 641 |

即使允许使用未来snapshot，native/V77的可关联写副本中，也只有1410/99份曾被
观测为安全锚点。该额外覆盖数明确不作为在线输入或动作标签。native已知被替代
的369份只占总Mamba写入约2.22%，约23.760GB；不能靠这一小类解释1071.564GB
写回。剩余大类是“当前证据不知道”，不是“无用输出状态”或“可以安全丢弃”。
逻辑node/prefix关联不补造物理generation、真实session_ref或未来消费证明。

为辨明未知写回，新增独立诊断模块`checkpoint_path_probe.py`，由
`BELIEFKV_CHECKPOINT_PATH_SHADOW=1`显式开启，默认0。仅在模型请求结束、
cache_finished_req已处理后，从req.last_node沿实际缓存路径读取至多256个节点；
记录Mamba状态的node/creation_time、相对本次输入的prefix位置、引用计数、
树分叉数量及设备/Host存在性。输入前缀、恰好输入边界和生成输出尾部分开，
不把边界状态直接当可复用。它不读取tensor内容、不分配或迁移KV、不改引用。
这是scheduler线程的同步完成时采样，序列化继续由原后台writer处理；每次
另记wall/thread CPU，实机开销仍须计入，不能由CPU资格宣称无额外成本。

V1338最终187项CPU检查通过，包括原151项遥测、新10项路径观测及前述26项，
用例重叠不相加。V1337初始两项失败是新夹具遗漏原生launch descriptor的
mamba_forward_candidates；只修夹具，未放宽生产路径。V1339绑定源码、初始/
最终检查及18:19 PDT实际进程验证。新观测未部署V78b、未登记新GPU实验。
它只弥补归因缺口，不是新缓存策略或已选定的论文机制。下一步须连接实际写前
状态、精确prefix兼容与后续消费，再对比Marconi/EfficientAgent的合理组合。

### 4.41 V78b首1200s的完整窗口

V1335新封存0–600s实际服务窗口，与V76/native按各自已验证GPU时钟比较：

| 首600s | V78b native-order | V76 reactive | V75 native |
| --- | ---: | ---: | ---: |
| Decode token/s | 950.070 | 1001.397 | 932.722 |
| 时间加权decode batch | 24.185 | 25.683 | 23.655 |
| Prefill worker累计s | 122.009 | 119.633 | 123.824 |
| D2H GB | 116.879 | 120.021 | 139.944 |
| H2D GB | 0.566 | 0.758 | 0 |
| PREPARE_HOST GB | 1.155 | 1.781 | 0 |

这时实际running仅约24，恢复只有4/5/0批，不能回答后续高压段的恢复与排序
根因，也不能由decode略高于native宣布加速。V1336保留全部42个应到任务，
分别记录完成7/5/7项，runner截断逗留282.809/287.924/272.993s；尚未完成
任务都在分母内，非最终或质量匹配JCT。V78b相对V76完成更多，但未优于native。

18:19 PDT验证V78b监督243510、driver243802及scheduler244015真实存活；
冻结source/env检查通过，native排序计数9035，额外排序/预取计数0，
physical_disabled=false。原官方worker231698仍评分V75 predictive，
V77等待器1121673继续。当前不热改实验，也不据早期结果注册替代GPU候选。

随后V1340补齐600–1200s窗口，V78b/V76/native的decode为1286.643/1323.358/
1212.875token/s，时间加权batch为46.575/46.514/44.270；prefill worker累计
134.826/123.035/148.019s。H2D为4.744/4.171/0GB，D2H为187.955/182.522/
226.189GB；V78b含3.738GB PREPARE。排序关闭仍出现恢复，说明额外排序不是
这一阶段恢复存在的必要条件，不能由此推断后续大量恢复的全部原因。

V1341保留1200s前全部75个应到任务，完成22/22/28项，runner截断逗留均值
514.824/528.719/484.254s。V78b与V76相比该原始口径有所改善，仍落后native。
V1342核对实际平均decode context为21577/22014/22068 token；running中位数
47/47/45，queue中位数5/5/0，三侧Host Mamba占用样本均达满池，Host FULL
中位占用约30.56%/27.30%/42.59%。resident_pressure仍排除可驱逐缓存，不能
把约0.43的活跃占用当作HBM实际空闲比例。

源码核查也排除了一个过于简单的解释：`_evict_backed_join_parent`在最老无锁
候选session_ref为0时已直接返回，不能未经实际victim证据就宣称它无视所有
无引用缓存而优先牺牲parent。后续继续核对真实驱逐对象、prefix兼容与消费，
同时观察后续压力窗口；这些数据还未建立最终JCT、质量或新机制收益。

### 4.42 生成前缀精确兼容性诊断的主线整合

在4.40默认关闭的路径观测上补齐下一请求的实际token比较。请求完成时只在开关
开启后快照其生成输出，复用同一次checkpoint路径采样；既有后台writer在最多
1024个context历史中保存实际输入、生成输出及路径证据。下一同作用域请求到来
后，先比较旧输入，再比较旧生成输出的连续前缀；对实际观测checkpoint分别记录
exact_prefix_compatible、token_prefix_diverges、new_input_ends_before_checkpoint
或outside_observed_transcript。历史容量淘汰、会话/命名空间变化和epoch倒退
不补造兼容性结论。新事件不写入原始token ID，默认关闭时不快照生成输出。

V1345的203项CPU用例通过，包含16项新增兼容性、边界、作用域及容量用例；
主线整合时同套203项再次通过，两次不相加。V1352绑定当前四份源码/测试与原始
XML和独立原生资格，未覆盖V1339的旧源码哈希。同步完成时采样/输出快照与
后台比较的成本仍需实机计量；当前冻结V78b未部署该诊断。

精确前缀不符不能独立识别序列化、模板或工具轮次的原因；前缀兼容不证明状态
仍驻留、被forward消费或曾作为物理传输来源，也不授予全局安全丢弃权限。
生成末token可能尚无缓存，结论只涉及观测到的checkpoint位置。当前完成的是
系统归因能力，不是新缓存机制或性能验收。

### 4.43 原生Mamba路径上限是必须保留的简单对照

当前引擎已有mamba_max_states_per_path，默认-1。其驱逐路径优先移除浅层、
符合条件的设备Mamba状态，保留FULL及已有Host副本；尾节点、树分叉、锁定
状态和设备叶节点受到保护，故它是软上限。源码没有单独保护session_ref，
小上限可能移除下一请求需要的输入checkpoint，不能假设cap=2天然合适。
当前实验配置不变，也不把这一原生参数或少存SSM状态本身当作新贡献。

V1347首次直接运行既有测试模块为3失败、4通过、5跳过，失败来自fake tree
未初始化已存在的host eviction observer；CUDA夹具在CPU环境跳过。独立脚本
qualify_native_mamba_path_cap_cpu.py仅把该fake构造字段补为生产默认None，
选择原TestMambaPathStateCap，保留全部原断言与驱逐条件，7项CPU用例及2项
子测试通过。V1348/V1349保留首次资格，V1350/V1351补齐准确来源标识：
引擎HEAD树0fa78f29657df0caa5520eca15bde377d19b9411，实际规范patch树
53020ce3efef3ed362e9af0db77147b39070f064。两者不能混为同一部署源码。
这只验证既有CPU策略/CLI，不证明GPU开销、读写减少、重算或JCT收益。

### 4.44 完整native机会规模与本次封存窗口

V1346审计完整V75 native请求事件文件61695327字节，SHA256为
a2cc76a6d3130d20325ed204068129118d3da7da8fcb6ac9f4abaac7868445cd。
36786个请求共有775765383输入token，734072267有效缓存命中，41693116未命中。
FULL匹配超出有效混合前缀3086927 token，占未命中的7.404%；正差距17278项的
P50仅36 token，差距至少512 token的774项共2505259 token。因此不能由巨大
写回字节推导出同等规模的可恢复计算或JCT空间。

首次观测context请求1140项占14984073未命中token，普通续接35646项占
26709043；“首次观测”包含root、child、内部摘要等，不能全部称摘要。前端
chat转换含模板tokenization的P50为51.815ms，累计/并集1918.133s，是墙钟
跨度，非独占CPU、暴露GPU空闲或可减JCT。既有旧输入缺失代理约0.157%，
未覆盖旧生成输出，不能据此声称全量无重算；新诊断用于补齐这一证据缺口。

本次代码整合一并封存V1343的1200–1800s窗口，V78b/V76/native的decode为
1317.33/1339.85/1333.46 token/s，加权batch为47.519/47.501/47.494，
decode worker均值28.330/28.314/28.300ms；H2D为4.128/7.093/0GB。
V1344保留1800s时全部111个应到任务，完成46/40/49项，runner截断逗留均值
664.747/684.125/613.734s。该历史截面不是当前运行终点、完整JCT或质量匹配
收益；它没有建立优于native的结论。继续以serving/runtime系统证据筛选机制，
整体研究目标保持未验收。

### 4.45 V78b后续压力窗口排除了哪些简单解释

V1353补齐1800–3600s服务窗口。2400–3000s有：

| 600s完整窗口 | V78b原生排序 | V76 reactive | V75 native |
| --- | ---: | ---: | ---: |
| Decode token/s | 1201.168 | 1009.645 | 1304.050 |
| H2D GB | 275.811 | 637.786 | 7.190 |
| FULL H2D GB | 190.044 | 448.096 | 2.811 |
| D2H GB | 437.555 | 725.583 | 224.260 |
| PREPARE GB | 0.171 | 0 | 0 |
| 平均decode context token | 22239 | 21356 | 22545 |
| 采样queue P50 | 63 | 76 | 51 |

V1355保持真实graph标记、batch及2048-token上下文桶。V78b满48请求的四个
匹配graph桶与native的worker均值比为1.001以内至1.009；47请求的22528-token
桶则约1.508，不能解释为同一个纯CUDA kernel慢了50.8%。worker区间含重叠/
调度周转。三侧Host FULL与Mamba均接近满池，reported resident_pressure仍
排除可驱逐缓存，不证明HBM还有约一半可随意使用。

V1356将真实请求ID、前后完成和匹配路径连接：V78b正FULL Host首次服务请求
666项，504 child/162 root，合计190.044GB报告需求；均关联上一安全输入
checkpoint节点。该报告没有creation identity，不升级为物理页或forward复用。
同一context窗口内后续正Host观测548次，占报告字节84.78%，不等于重复搬运
同一批页。按入队窗口、完成后1s内续接的已服务请求，V78b/V76/native正Host
为569/3229、1328/3243、7/3475。两种队列/服务分母仍严格分开。

V1357的一比十六采样短单请求prefill中，V78b正FULL Host15项选批线程CPU
均值94.150ms、累计1412.245ms；零Host77项为4.551ms、累计350.412ms。
正组占该采样选批CPU80.12%。V76正组37项为89.180ms、占86.41%；native
124项零Host为3.694ms。恢复与慢选批的关联继续存在，额外排序并非必要条件。
不能由不同请求组差值分摊函数、外推全部请求或宣布可减JCT。与4.35同步写回
证据一起看，下一步必须同时核对恢复为何增加及原生容量回收路径，不能只扩预取。

V1360补齐随后3600–4200s的完整窗口，V78b/V76/native H2D降至
2.246/5.197/1.108GB，D2H141.925/166.705/207.155GB；decode为1311.798/
1268.590/1212.677token/s，但加权batch为43.413/46.293/38.553。高恢复不是
整段运行的恒定现象，后段原始速率也受剩余工作/排队和batch影响。应核对真实
活跃工作集及其反馈，而不能只挑恢复多的窗口或以之后速率较高宣称最终加速。

### 4.46 等待卸载的窗口反证与不可提前选定的机制

新增只读audit_runtime_lifecycle_window.py使用已验证服务时钟、实际机会事件
和前后runtime状态。V1358的15项CPU用例通过，覆盖精确半开事件窗、不同快照
边界、缺失覆盖、计数回退、非法单位和异步落盘乱序。V1359在2400–3000s记录
V78b/V76的parent_pressure_demoted事件均0，累计计数也都无增量；但窗口前已
累计457/516次，不能排除那些动作后来造成影响。两侧普通原生驱逐和终态回收
仍发生，“该窗口0次”不能写成“系统没有迁移或回收”。

因此目前的判断是：

1. “额外排序或预测H2D是恢复突增的唯一原因”不成立，V78b两者关闭仍有大量
   恢复；这不证明原生顺序完全无益，V78b比V76的多个原始窗口确有改善。
2. “压力窗口中大量新PREPARE/定向卸载直接导致恢复”缺乏支持，V78b本窗口仅
   0.171GB PREPARE且无新增定向卸载。此前动作、引用保留/释放、回收和实际
   工作集仍待隔离，不直接按0计数给旧动作免责，也不把单轮相关性写成因果。
3. “用更大的预取覆盖就能解决差距”仍无依据。须先解释普通快速续接为何失去
   驻留及恢复的控制/写回成本，同时证明native自身存在可减少的有效工作。

文献约束继续收紧：Marconi[R6]已覆盖混合模型checkpoint准入/驱逐；
EfficientAgent[R10]已有Host工作集与写入决策；原生路径软上限也是简单对照。
本轮补核SuffixReplay[R18]原始摘要，其少量SSM锚点与有界后缀重放直接约束
“少存状态再重算”这一方向。未复现其实现、质量或当前负载收益，不能照搬论文
TTFT提升，也不能宣称生成前缀兼容性诊断已经构成新贡献。

下一独立诊断应连接真实生成前缀、下一实际输入、必要checkpoint及其后续消费。
先完成这条必要条件，再在相同引擎/有效任务下比较已有准入和保留机制。当前
没有据此登记小cap策略或替换冻结V78b，也不恢复已撤下三机制为选定研究路线。

### 4.47 V78b最新任务截面与incomplete归因

V1354保留3000/3600s全部156个应到任务。3600s的V78b/V76/native recorded
completed为93/80/105，runner截断平均逗留1354.919/1476.103/1267.947s。
V1361进一步覆盖4200s，三侧recorded completed为120/115/124，另有V78b的
1项incomplete；全156项runner截断逗留1542.452/1704.592/1426.191s。
V78b若仅按completed终点累计，其截断逗留为1557.706s；失败任务继续累计到
窗口终点。原生JCT合格完成数分别120/114/123，guard/语义标签保留。以上均非
最终或质量匹配JCT，不能因完成数接近就称性能达标。

该incomplete为pylint-dev__pylint-7080，runner原始duration1722.240s。
2个child均已返回、JOIN满足；root最后请求
beliefkv:01a14776-f396-7551-a473-0bc85e345a03的llm_result.finish_reason为length，
输出正文32216字符、无工具调用，最终无语义完成且patch为空。原始文本反复讨论
同一ignore-paths判断，不能按自然任务完成计；workflow_deadline.expired=false，
无截止时间强停。保留8192输出上限和原始标签，不新增guard或增大上限。

19:13 PDT再次核对V78b监督243510、driver243802、scheduler244015真实存活；
冻结source/env验证通过。原质量worker231698仍评分V75 predictive，V77等待器
1121673和原监督3806721继续，未放宽质量门槛。新审计只推进归因与研究筛选，
完整性能、质量和已有方法差异仍不足，目标继续保持未验收。

### 4.48 V75/V76三侧官方评分已完整收尾

19:14:33 PDT原队列完成最后一臂V75 predictive评分，监督和worker正常退出。
V1363绑定三份原始official_quality_summary，检查结果类别互斥且其并集精确
覆盖原156项instance_ids；没有修改冻结评分器、任务结果、输出限制或资格标签。

| 官方评分 | V75 predictive | V75 native | V76 reactive |
| --- | ---: | ---: | ---: |
| Resolved | 98 | 95 | 101 |
| Unresolved | 56 | 59 | 54 |
| Empty patch | 2 | 2 | 1 |
| Evaluator incomplete/error | 0/0 | 0/0 | 0/0 |
| 冻结correctly_completed_ids | 95 | 90 | 97 |
| Measurement complete | false | false | false |
| 有效完成吞吐 | null | null | null |

predictive的空patch为django-11087/12304。resolved与correctly_completed
不相等，原predictive的1项incomplete、native的3项及各臂guard/语义终态资格
继续保留，不能以官方评分完成为由重标或从分母删除。评分器给出的正确完成
子集JCT分位数也不能当全156任务的质量匹配JCT。当前单轮resolved多3项不证明
质量等价，更不能覆盖V75 predictive原始全任务均值1806.897s落后native
1665.374s这一负结果；正式收益仍须按原完整口径验收。

V77原等待器1121673已自动接续其官方CPU评分，worker1604042/
start_ticks637354434于19:14:40启动，后续ps确认真实存活。原GPU V78b仍冻结运行，
不因评分队列切换热改资源参数或注册新候选。本次是补齐质量证据与状态更新，
不宣布研究机制已成立或目标完成。

### 4.49 V78b的4800s截面

V1364补齐4200–4800s的实际服务窗口。V78b/V76/native H2D分别为
1.000/1.143/1.286GB，D2H123.031/146.531/180.360GB，decode为1082.763/
1132.570/1056.228token/s，加权batch为24.513/30.259/25.881，queue均值
均低于0.1。这是有需求阶段之后的低排队窗口，不能拿其速率宣称高压段胜出。

V1365保留4800s前全156项：V78b/V76/native recorded completed为132/132/131，
V78b另有1项原incomplete。runner截断逗留均值1643.987/1832.021/1533.602s；
只有completed才使用结束端点的口径为1663.087/1832.021/1533.602s。
原生JCT合格完成数132/129/130，guard/语义标签照旧。V78b完成数略多并未
推翻其原始逗留仍落后native，完整性能与质量匹配证据继续待验收。

### 4.50 下一独立全量native前缀诊断的必要性与资格

当前证据缺口是：一次生成后实际留下哪些checkpoint，旧生成输出在下一实际
输入中保留多少，以及对应状态是否仍在缓存路径。已有旧输入探针排除了新增
输入，却不覆盖旧生成输出；正Host请求的匹配路径只有当前创建身份，上一安全
checkpoint事件也不证明逐页/物理副本世代。不能直接将数TB写回标成可丢弃。

V79因此准备为完整156任务的native诊断，不做新的策略A/B。沿用V75的完整
53020ce3引擎、同任务和Poisson到达、running48、Host200GB80:20、HBM ratio0.9、
write-back32、工具180s、root14400s及输出8192；模型、prompt、harness与工具
代码不改。仅设置BELIEFKV_CHECKPOINT_PATH_SHADOW=1，原生策略保持，
prefetch/PREPARE、额外排序、native-close GC、路径cap和frontier合并均不启用。
新观测的同步路径/快照和后台比较开销仍须计量，不能称无成本。

研究判断按实际数据推进：统计输入前缀/边界/生成尾部checkpoint的精确兼容性；
将重复观测的逻辑checkpoint与不同物理写副本分开；区分普通续接与首次fork、
换会话及compaction。后两类没有相同会话历史时保持未知，不能由普通续接结果
推导全部大缺口。兼容性不是驻留、forward消费或安全丢弃证明；即使存在可过滤
状态，也须与原生路径软上限、Marconi/EfficientAgent及后缀重放的合理方法比较，
并证明native本身可减少的成本，才选择新机制。当前仍未选定论文贡献。

新启动器start_v79_checkpoint_diagnostic_20261016.py只允许独立冻结候选，
核对前轮真实进程退出、完整156项及干净遥测，再等待GPU/资源释放。原生门槛
失败不重标为成功；完整采集的exit1保留在独立证据中。启动后还必须看到实际
generation_continuation_prefix_probe、原生服务参数、同到达/容量与真实NUMA，
不能仅凭环境变量认为观测已运行。最终V1374的257项CPU检查通过，包含203项
既有观测/审计、39项新驱动和15项生命周期用例；先前253/256项重叠不相加。
加载worktree脚本的用例在登记前覆盖了根目录解析问题。当前只是CPU准备完成，
独立检出、登记和实际GPU启动仍要分别封存，原V78b及质量队列不改。

### 4.51 V79独立诊断已冻结登记并真实等待

候选c40d2f6在独立beliefkv-v1366-checkpoint-diagnostic-gpu-20261016检出，
只通过ignored symlink复用原完整引擎53020ce3，未修改运行引擎。V1367/V1368
绑定clean应用、规范patch、257项最终CPU凭据、前轮进程/登记、原生任务到达、
容量和helper。V75实际完整采集也通过新完整性判定的CPU只读核对：153 completed、
3 incomplete、141项原生JCT合格、client exit1原样保留，159项输入绑定，不将
这一核对当作新GPU或质量/性能验收。

V1369于19:42 PDT启动监督1906740/start_ticks637518375；随后ps确认nice0、
真实argv及waiting_for_complete_v78b。未启动V79 driver，也未生成实际服务/
placement/结束凭据V1370–V1372。19:45再次核对V79来源与环境通过，V78b监督
243510、driver243802、scheduler244015仍真实存活，V77评分worker1604042继续。
这构成已执行的登记与可验证等待，不是新观测已采到数据、性能提高或论文机制
成立的证明。后续只有前轮完整收集、实际退出和资源释放后才能冷启动。

### 4.52 V78b原始runner均值的单调负结果

V1377在已覆盖的5400s任务时点保留全部156项，V78b/V76/native recorded
completed为143/143/140，V78b另有1项原incomplete。runner截断逗留均值为
1708.632/1907.026/1613.809s；只有completed使用终点的口径为
1731.578/1907.026/1613.809s，原生JCT合格完成为143/139/135。完成数略多
不能代替总体逗留，失败和未完成任务仍在分母中。

以同一runner终点减计划到达为口径，V1259已封存native全156个runner实际结束，
7000s观察的1666.324260906s即其最终原始均值，本轮只读复核一致。V78b在5400s
的1708.632188036s已是最终原始runner均值的下界，所以该指标后续无法反超。
这不是和result.duration均值1665.374s相减，也不是质量匹配JCT或推断全部新
机制无效；原V78b继续完整运行，不因原始负结果提前停止或改变任务。

V1375服务窗口5400–6000s的V78b/V76/native H2D为0.148/0/0GB，D2H为
59.298/79.175/98.018GB；decode687.300/777.105/778.060token/s，加权batch
6.687/8.402/9.209、queue均值均低于0.03，属于不同剩余工作的小batch阶段。
不能由GPU利用率接近98%推断整体吞吐或公平性改善。

同数值6000s的任务审计因到达/finish日志尚未覆盖而拒绝，V1376没有写出。
后续检查该日志最新finish约5921.662s，GPU服务覆盖不能替代任务时钟证据；
采用V1377的5400s可验证截面，不补心跳或放宽审计。V1378封存当前登记/真实
等待、这一原始指标负结果和限制。总目标继续未验收。

### 4.53 快速续接之间的可观测上下文竞争

新增只读审计器按native事件写出顺序统计：同一逻辑context上一请求完成后，
到下一请求首次服务登记前，有多少不同的其他逻辑context开始服务。不能按
llm_submit的ts_ms排序，因为该事件到首次服务才写出，时间却回填为较早入队时刻。
窗口成员仍按入队时间定义，与V1356的已服务请求cohort一致；尚未服务的请求
不在此cohort，服务本身可能发生在窗口结束后。

首版V1381将快速cohort限定为同epoch，三侧均为空集。真实窗口内可关联前驱的
3814/3812/4264次续接均推进epoch，不能将其等同compaction、失效或新会话。
修正schema2保留全部同逻辑context续接，再单列同epoch和变化；V1381原始结果
及V1380的18项CPU凭据保留，V1382最终20项通过，重叠检查不相加。
V1383保留准确读取前缀及源码hash；V1384绑定修正原因、CPU和独立分母复核。

2400–3000s中，上一完成到下一入队不超过1s的cohort如下：

| 观测 | V78b | V76 | Native |
| --- | ---: | ---: | ---: |
| 快速续接请求 | 3229 | 3243 | 3475 |
| 报告正FULL Host请求 | 569 | 1328 | 7 |
| 正FULL Host占比 | 17.62% | 40.95% | 0.20% |
| 其他逻辑context首服数P50 | 66 | 82 | 56 |
| 其他逻辑context首服数P90 | 75 | 92 | 65 |

三侧快速cohort的分母、正Host数和报告FULL字节与独立V1356逐项一致，
竞争身份均已知。原生排序减少了观测到的竞争数量，但仍不能将V78b剩余恢复
直接归因于这一数量：例如提示16384–20479 token、竞争48–63个context时，
V78b为62/270正Host，native为2/495；提示20480–24575、同竞争桶则为66/325
和1/440。两侧相同粗桶仍存在差异，需要进一步连接实际驻留/驱逐和前缀变化。

这些不是物理页LRU距离、去重工作集、逐层访问或暴露恢复时间。decode访问、
共享前缀、实际驱逐、namespace/session和物理副本世代未由此计数证明；同一
逻辑context和epoch也不证明相同物理副本。历史单轮、workflow内相关请求及
粗分桶仍有混杂，不能把64等桶边界直接设成新准入阈值或宣布策略因果。
下一步使用V79的实际输入/生成前缀和checkpoint观测补齐兼容性，之后再判断
需要何种物理来源证据；目前没有从该关联直接选定新的缓存策略或论文机制。

### 4.54 V78b完整结果及V79自动接续

V1329确认V78b于20:05 PDT完整收集，HTML和同容量native比较已生成。
V1385利用三个cohort所有已结束runner，在9000s时点核对全156项：

| 最终观测 | V78b | V76 | Native |
| --- | ---: | ---: | ---: |
| Runner结束 | 156 | 156 | 156 |
| Recorded completed | 155 | 156 | 153 |
| Incomplete | 1 | 0 | 3 |
| 明确原生JCT合格完成 | 146 | 145 | 141 |
| 最终runner平均逗留/s | 1764.123 | 1975.416 | 1666.324 |

V78b比native的原始runner均值慢5.869%，确认4.52的下界负结果。其
result.duration全终态均值为1763.135s，native1665.374s，两种时钟端点不混用。
只有completed/合格completed才停止累计的9000s截断指标另列于V1385，不能当作
最终runner均值；失败及不合格任务继续留在分母。V1379后来已验证6000s真实任务
覆盖，原V1376的拒绝和未写出状态保持，不补造早期覆盖。

完整同池比较中，V78b/native原生H2D为339.650/13.610GB，其中FULL
223.492/3.952GB、Mamba116.158/9.658GB；两侧预测和handoff传输均为0。
V78b多轮spawn为28个workflow，native为30个，实际模型/工具工作仍不同。
原始完成速率75.589/80.506个每小时也不是质量匹配有效吞吐；V78b官方质量
未评分，原incomplete和全部JCT/guard资格保留，不以多完成2项宣称性能胜出。

原监督、driver及scheduler实际退出后，V79原登记监督1906740于20:06:01 PDT
自动启动独立driver2032542/start_ticks637662487。只启用已登记诊断，应用
c40d2f6和引擎53020ce3保持冻结；驱动存活不替代实际首服/前缀探针及NUMA
凭据V1370/V1372。V77官方CPU评分继续原队列，未因本轮结束重启或修改评分器。
当前完整性能、质量匹配和已有方法差异证据仍不足，研究目标继续未验收。

### 4.55 V79观测器导入故障及同配置V79b重试

原V79已产生实际请求和generation_continuation_prefix_probe，但20:09 PDT
NUMA观测子进程发生ModuleNotFoundError: server_reservation。观测脚本按自身
路径parents[2]/ops导入，在冻结.worktrees目录中得到不存在的.worktrees/ops，
而真实操作目录为experiment/ops。原监督按失败路径停止driver与GPU服务，
状态stopped_for_diagnosis；不是模型完成、NUMA容量不足或cache策略失败。
V1370/V1372未形成资格，原部分数据及失败state/log原样保留，不能充当完整实验。

主线观测器改为从显式run-root的祖先定位真实ops。新的独立重试入口加载原
冻结V79 helper，只重定向外部NUMA observer和新输出/凭据位置；原应用c40d2f6、
完整引擎53020ce3、所有任务/资源/模型/prompt/工具及诊断开关保持。原始注册输入
继续逐项验hash，重试额外绑定observer、入口、CPU、失败和新plan，不热改冻结源。
首次prepare还发现按新RUN派生的arrival文件路径发生变化，严格拒绝且未写登记；
现保留原冻结arrival文件引用，仅RUN_ROOT变化，新plan内容必须与原plan完全一致。

V1393最终56项CPU检查通过，含worktree外部helper定位、原失败分类、只替换
observer命令、配置生成保留原arrival路径、完整前轮及原启动条件；V1392初始
55项重叠凭据仍保留，不累计。V1386/V1387/V1388绑定故障、原注册来源及实际
监督，V1394记录修复资格。原进程确认退出后，V79b监督2048213/start_ticks
637731046以nice0在20:17:27 PDT启动，driver2048545/start_ticks637731177运行。
V1389/V1391于20:20:30确认真实首服、新probe、NUMA和与原native完全相同的
FULL/Mamba容量，scheduler2048787/start_ticks637732486实际运行，冻结来源
再次核验通过。Host大映射驻留约159.574GB在N1、42.386GB在N0，声明策略为
preferred；这些映射不能直接逐池归因或当带宽证据。原V77评分继续，未重新启动。

这次修复只恢复已登记的证据采集，不产生性能/质量收益，也不是新的缓存机制。
V1395压缩保存V78b完整native比较，与V1385全队列审计一起维护负结果；下一步
依据正常独立诊断的真实兼容性和物理来源选择研究机制，完整总目标仍未验收。

### 4.56 实际后继前缀与生成尾部写回

V1396/V1402/V1408给出V79b与原native的三个完整服务窗口，时钟、池单位及
实际诊断启动均已验证。V1405/V1403/V1409由最终审计源码独立封存读取前缀，
V1407的57项CPU检查覆盖未知路径、token计数、身份冲突、聚合写入、同刻/未来
信息及跨context兼容反例；先前48/51/56项重叠凭据保留、不累加。

| 观测 | 0–600s | 600–1200s | 1200–1800s |
| --- | ---: | ---: | ---: |
| 已观测同会话续接 | 3993 | 4833 | 4736 |
| 上一输入最后1个token处分歧 | 3968 | 4796 | 4713 |
| 保留正生成前缀的续接 | 25 | 37 | 23 |
| 全部Mamba D2H/GB | 106.950 | 133.672 | 129.680 |
| 关联上次观测生成尾部的D2H/GB | 100.511 | 122.726 | 117.124 |
| 写前已出现后继输入且已观测比较仅不兼容的副本/GB | 95.296 | 110.170 | 104.825 |
| 上述后继输入出现到写提交P50/s | 33.036 | 17.297 | 12.742 |

第一项只包含已有历史并出现实际后继的请求，不是所有完成请求。前两个窗口
的写角色均可关联单节点；最新窗口另有7份/0.451GB聚合Mamba副本不能逐节点
归因。角色来自严格早于D2H submit的完成快照，receipt没有creation/物理副本
generation，所以这是历史关联。输入出现到写提交的提前量也不是后台比较完成
或动作可用时间：探针ts_ms在输入入队时记录，实际比较随后由writer执行。
先前窗口的比较不计入本窗口时序分区，未知仍保留；不能由此宣布在线安全丢弃。

共有4/11/3个生成尾部checkpoint观测仍精确兼容，不能统一删除所有decode末态。
三个窗口中严格输入前缀状态的兼容观测为50989/30167/22918，均保持精确兼容；
输入边界本身仍有3/8/15次不兼容。其原因尚未从chat template、thinking、
工具消息重编码和session行为逐项分出，不将最后1个token分歧直接归因某一个。

离线路径选择给出必须保留的简单对照：

| 保留集合中仍有兼容checkpoint的路径 | 0–600s | 600–1200s | 1200–1800s |
| --- | ---: | ---: | ---: |
| 最远一个checkpoint | 2426/3993 | 2888/4833 | 2848/4736 |
| 最远两个checkpoint | 3992/3993 | 4829/4833 | 4729/4736 |
| 最近的严格输入前缀checkpoint | 3993/3993 | 4833/4833 | 4736/4736 |

严格输入方案保留最远兼容点的数量为3988/3993、4822/4833、4733/4736；
少数更远的输出或边界状态可兼容，不能把表中“能恢复某个前缀”当零重算。
选择仅使用上一请求长度和已观测位置，后继兼容性只作离线标签；没有模拟全局
缓存、真实容量、共享引用、Host准入或执行变化。原生path cap保护tail/fork/
locked/device leaf，保留已有Host副本；它沿当前tail的祖先链执行，不是上述
两点选择，也不证明会清掉别的旧分支。该来源已在当前冻结引擎mamba.py核对。

三个窗口两侧原生H2D均0。全D2H stream累计为13.832/34.233/34.938s，
不是可减JCT；原native相应为13.466/32.441/34.777s。最新窗口满48附近的
加权batch为47.463/47.494、decode worker均值28.464/28.300ms，不能据此
断言总体性能等价或诊断没有开销。首窗口同步路径探针累计374.587ms，仅覆盖
path walk，不含输出快照、队列、后台前缀比较和序列化。

### 4.57 候选研究问题与已有方法边界

当前有证据支持的问题是：在后继实际token前缀已不再延续某个生成分支时，
为何还要自动将其大份混合状态写入Host，怎样保住仍有价值的输入checkpoint，
又不增加已解决的高频控制工作？候选依赖实际请求转移，不依赖RETURN报秒数，
不改变模型prompt、工具工作或任务完成标准。它仍只是待验证的系统候选。

Marconi [R6]第4.1节已覆盖基于历史分叉的prefill准入，并将末次decode状态作为
input-and-output续接缓存点；其文字明确以对话通常追加而非从中间分叉为依据。
当前前缀分歧提示这个适用条件需要检查，但不证明Marconi的合理适配一定失败。
少存checkpoint、保留fork或只缓存两份本身不能成为新颖性。
EfficientAgent [R10]第4节/附录D已有工作集估计与容量条件写入，且警告在容量
足够时固定拒写会损害恢复。必须按实际FULL/Mamba大小合理适配后比较，不得
只对照原生全写或把其方法误称为一律关闭迁移。
SuffixReplay [R18]用近似后缀重放替代状态checkpoint，属于另一条强相关路线；
其质量和本任务成本不能直接照搬。本文同时将R18的正式标题校正到原始摘要。

下一步先完成强工程对照：原生不启用Host迁移、已有path cap和容量条件准入。
若关闭迁移或简单保留已拿到全部收益，就按工程配置收尾，不能把更复杂的策略
包装成论文机制。继续原V79b观察后段压力，不更换运行源或因局部负/正结果截断。

只有简单对照仍留下明确空间时，才在独立候选中实现按实际后继反馈的分量级
Host准入/回收：在请求转移时更新受影响分支，保留可用输入锚点；在原生回收点
核对活跃/共享引用及在途传输，再决定是否备份低复用Mamba状态。未知仍走原生，
FULL和必要状态分别核算；回收后缺缓存须由正常重算恢复。CPU夹具先证明共享、
引用、异常和恢复语义，再检验少写是否减少真实阻塞、是否增加有用重算。

当前现象也可能发生在单agent，尚非动态fork/join独有。需要用自然协作的共享、
分叉和终态语义证明增量，不能强制无用spawn或改变任务工作来创造优势。最终
必须超过正确配置native及合理已有方法，交付完整质量、平均JCT、有效吞吐、
长尾、公平性和重算证据。目前只是规模与时序必要条件成立，净收益和新颖性未成立。

### 4.58 V77官方评分结束

原CPU监督于20:38 PDT正常完成，worker_returncode=0，监督和worker随后实际
退出，无需重启。V1406封存原state、评分和156项分母验证；V77为90 resolved、
65 unresolved、1空patch（django-11138），evaluator incomplete/error均0。
原native非完成django-12708保留，correctly_completed_ids为90，measurement_complete
仍false、有效吞吐仍null。与同156项V75 native的95 resolved比较，80项双方
resolved、10项仅V77、15项仅native；单轮差异不作显著性或质量等价声明。
不能因两侧correctly_completed数量均90就假定是同一个质量匹配子集。
原V77平均duration1814.634s仍落后native1665.374s；终态回收未取得已验证收益。
原V79b仍继续独立诊断，未改任务标签或评分器，总目标继续未验收。

### 4.59 后段恢复反例与无Host原生强对照

V1410/V1411和V1420/V1421分别封存V79b的1800–2400s、2400–3000s，
沿用已核验时钟、启动及原生池单位，没有热改原诊断的应用或引擎。

| 窗口 | V79b H2D/GB | 历史native H2D/GB | V79b D2H/GB | decode token/s，V79b/native |
| --- | ---: | ---: | ---: | ---: |
| 1800–2400s | 0.421 | 0 | 220.165 | 1312.552 / 1296.692 |
| 2400–3000s | 821.767 | 7.190 | 923.520 | 938.623 / 1304.050 |

后段两侧平均running仍约46.9，decode时间加权batch47.182/47.490；
worker均值43.676/28.993ms，不是相同kernel耗时或纯计算退化。
此前1800s两侧H2D为0的结论仍适用于原窗口，但不能外推全程。相同任务和
到达不保证相同模型路径、物理工作集或观测成本，当前没有把差额分配给具体原因。
原诊断继续收集，不因负性能中止；它本来就不是新缓存策略性能实验。

1800–2400s的4930次续接有4897次在末输入token分歧，生成尾部关联写回
106.049GB；写前已观测后继且仅不兼容为97.485GB。2400–3000s相应为
3386/3365次、55.375GB和52.477GB，但该段另有3282份/211.325GB聚合
Mamba副本无法逐节点归因，不能沿用前段比例推断它们也是输出尾部。原生
Mamba H2D在该段245.580GB，也不能继续把所有先前写回当无恢复需求。
后段最远两点离线选择保住最远兼容checkpoint3378/3386，严格输入点能保住
某个兼容点3386/3386，却只保住最远兼容点3377/3386。反例和额外重算风险保留。

先实现能证伪复杂方案必要性的强对照。V80只取消原生Host缓存分配和迁移，
不改任务、prompt、调用规则、输出8192、工具180s、workflow14400s、156项
Poisson到达、原生排序及session引用语义。Host实际分配0、可用预算仍200GB；
显式固定FULL1798992 token及Mamba513槽，启动时核对与原生相同的物理
HBM69,939,384,320字节。保留CPU node1及内存preferred1，禁用额外诊断、
PREPARE、handoff、预取、关闭回收和path cap；原生未命中走正常重算。
现有引擎registry原本就支持UnifiedRadixCache不挂HiCache，新增实现只拆开
启动器和观测对Host的绑定，未修改原生引擎或发明另一套驱逐策略。

最终V1423的301项CPU检查通过，涵盖Host缺失/部分挂接拒绝、同HBM字节、
实际shell固定容量参数、NUMA、遥测关闭和不匹配启动拒绝。V1412最初因测试
断言读取不存在的顶层字段失败，生产路径无该错误；已改读实际嵌套字段，
V1413/V1422中间301项通过保留，最终保留已有无Host admission-only启动；
新实验入口才限定native。四套凭据重叠，不相加，不覆盖失败记录。
V80在21:25登记前仅完成CPU准备；独立冻结、绑定来源后等待V79b完整终态、
原进程及GPU实际释放。只有实际服务、零Host分配、同HBM和NUMA核验才算启动。
最终保留全部任务、质量/JCT资格和有用重算。若简单关闭Host就获得全部收益，
按工程对照解释；否则再检验原生path cap、容量条件写入及后继反馈的增量。

### 4.60 V80无Host对照登记与实际等待

21:25 PDT，V1414/V1415绑定独立应用3700fa465641531e2defd0c366f61fc6ce64e525、
完整原引擎53020ce3efef3ed362e9af0db77147b39070f064、V1423及同一156项任务/
Poisson表。新引擎检出基于原94602c9并应用完全相同canonical patch，没有
修改V79b的应用、引擎或已登记helper。来源、环境和输入绑定已再次验证。

V1416记录真实监督3176720/start_ticks638138015；ps确认nice0且state为
waiting_for_complete_v79b。原V79b的2048213/2048545/2048787仍真实存活，
并非仅由state推断运行。V80尚无driver、GPU服务、性能或质量结果。
自动接续要求前轮全156项summary/result/finish、有效生命周期及排空遥测，
保留原始exit1/incomplete/guard；随后核对实际GPU/进程释放，才启动新独立输出。
V1417/V1418是未来真实启动/NUMA凭据，不提前生成或把等待写成实验已开始。

真实聚合回执可见node_commits/tagged_child_commits为空，node_ids也不等于
Mamba状态清单，不能按节点数量均摊上述211.325GB。暂保留未知，不为提高机会
统计而修改归因契约；额外细粒度观测是否值得加入，需先看无Host强对照的规模。

### 4.61 撤下混合状态候选；改查模型通用的迁移阻塞与干扰

用户认为后继前缀驱动的混合状态Host准入不可靠、新意不足且依赖Mamba。
本轮撤下它的研究主线地位，保留此前诊断、CPU实现和反例，不继续堆叠控制器。
V80仍是有价值的工程控制实现，但此次自动接续是为旧候选准备；已在
21:53:57 PDT核验精确进程身份、仍在等待且无driver/子进程后取消。
V1442保存完整原state、登记和冻结launcher哈希；driver/GPU均未启动。
ops状态改为cancelled_before_gpu，原V79b继续冻结，不设置全局停实验。
未来只有新的具体问题需要该对照时再独立登记，不自动恢复旧队列。

**新筛选问题。** 一次agent续接在分配恢复空间时，是否因冷KV写回而拖住
其他已经可执行请求？如果是，能否在相同HBM/Host容量与缓存复用条件下，
让这部分迁移在不阻塞无关请求的情况下完成？这里研究的是物理页回收、
数据依赖及共享传输路径，不修改agent决策，不要求准确预测RETURN。
它应在纯Attention模型中也成立；混合模型运行一条FULL路径不足以证明泛化。

V1431/V1432用已验证时钟比较真实worker几何，不要求Host占用流，支持未来
无Host对照。脚本audit_native_window_geometry.py及既有依赖的最终34项CPU
检查见V1430，不与先前重叠凭据累加。新脚本只读已存在日志，不增加运行探针。
V1434–V1441沿用既有prefill/恢复/传输审计器，全部报告保留原始读取范围和hash。

| 观察 | V79b 2400–3000s | 同段历史native |
| --- | ---: | ---: |
| 原生H2D | 821.767GB | 7.190GB |
| 相近上下文、满48且graph=true的worker比值 | 约0.981–1.007 | 参考 |
| 正FULL Host短prefill采样/选批线程CPU均值 | 57 / 87.956ms | 本采样无正组 |
| 零Host短prefill采样/选批线程CPU均值 | 36 / 3.867ms | 124 / 3.694ms |
| 正Host样本选批内D2H | 174批 / 25.394GB | 不可匹配正组 |
| 正Host样本选批内H2D | 57批 / 24.323GB | 不可匹配正组 |

57个正组样本全部有D2H提交，174批D2H均在选批结束前ACK；57批H2D的ACK
全部晚于选批结束。D2H记录stream累计4.441s，选批墙钟累计5.438s，二者
不能相除宣称因果比例或相加作为成本。3000–3600s正组仅1个样本：
选批线程CPU53.267ms、3批D2H/347MB在选批结束前ACK。恢复高峰有阶段性；
不能把57个采样按采样率外推，也不能由诊断臂反推native必有巨大瓶颈。

V1443/V1444继续按同一clock-domain、精确forward_iter前后邻居核对，
V1445核算已有CPU阶段的区间并集。2400–3000s的
decode47→prefill1→decode48有55/75个样本，prefill run_batch返回至下一
run_batch入口均值为2.831/2.852ms；该边界不是CUDA kernel结束或GPU空闲。
它也发生在当前选批之后，不能用来解释或扣除前述87.956ms选批线程CPU。
目前没有证据把全部选批等待称为GPU暴露停顿。这是新候选的必要反例，
不是异步方案收益的证明。两个最初的直接脚本入口因scripts模块路径失败，
没有产生报告；改用python -m后生成独立V1443/V1444，未改源码或服务环境。

**函数口径更正。** V79b冻结应用的third_party symlink实际指向
sglang-v1138-batched-writeback-gpu-20261016，完整树53020ce3。
主目录旧脏third_party不是运行来源。冻结引擎_try_write_back_chain已经
合并入队再start_writing；之后writing_check(write_back=True)仍等待
finish_event.synchronize，ACK后才demote。批处理不是缺失能力，
同步存在也不证明所有174批都由同一个函数或目标请求触发。

**与已有方法的边界。** 本轮重新核对下列原始论文/官方文档：

| 已有能力 | 对新筛选的约束 |
| --- | --- |
| CachedAttention最新版摘要及AttentionStore v1 §3.2.2 [R19]：异步保存、写缓冲及逐层加载 | 换成异步DMA、增加buffer或减少一次等待本身不是新意 |
| LMCache官方async loading文档 [R20]：异步取回、事件同步与分配预算反压 | 非阻塞API或页生命周期不是独立贡献；该页标明旧进程内模式已deprecated |
| KVBM官方组件文档 [R21]：跨层块管理与事件生命周期 | 不能把统一页状态机或接入NIXL称为新机制 |
| TokenCake原始摘要/作者实现 [R22]：agent感知空间管理、工具期卸载及预测上传 | 不能把工具/SPAWN事件加异步卸载简单组合后宣称首次提出 |
| CONCUR原始摘要及v1 [R23]：反馈调节agent准入、缓解中段抖动 | 单纯降低并发或限制工作集应作为对照，不是本项目新机制 |

尚未证明这些方法在目标条件下留下同一缺口。候选只有在“已有异步机制、
合理并发与固定内存下仍有显著跨请求阻塞”成立时才值得实现，不能把文献未逐项
报告当前指标当作未解决的证明。SAGA/R12已有workflow执行图、保留和公平机制，
也必须保留为相关对照，不把优先parent改称新研究问题。

**接下来的具体工作与停止条件。**

1. 先在正确native中核算选批等待是否暴露到GPU供给、影响哪些其他请求，
   区分调用线程等待、已有GPU计算覆盖、copy带宽干扰及必要恢复依赖。
   现有日志缺少直接等待时保持未知，不把stream秒数当可去除停顿。
2. 用相同页身份/版本连接写回、回收、后续恢复和重算，判断是不可避免的真实
   工作集，还是可以改变时机而避免同步的写回。诊断额外成本和轨迹变化需单列；
   只在诊断臂出现的退化不能作为优于native的论文收益。
3. 若规模足够，独立验证事件驱动的回收完成处理，使仍可执行的batch继续前进；
   源页必须保护到DMA完成，目的容量不可提前虚报，不能直接删synchronize。
   这一步先归为共用工程对照；workflow阶段信息能否再减少暴露写回及抖动，
   必须独立消融，不能混在基础异步实现中。
4. 若简单强对照已覆盖全部收益，或正确native只剩很小空间，停止把本候选升为
   论文主线。只有相同任务/质量/资源下的JCT、有效吞吐和重算都支持，且证明
   纯Attention适用性及已有方法差异，才进入新机制的实机开发。

当前状态是完成候选撤回、队列取消、最新证据整理和文献边界筛选，
不是新机制实现、证明存在新颖性或已优于native。性能总目标继续未验收。

### 4.62 提高在线到达率，验证有效双向FULL迁移机会

用户指出当前负载未提供足够迁移机会，建议缩短任务到达间隔。采用一个开发
候选：Poisson均值从15秒/root改为10秒/root，到达率从1/15增至0.1，即提高
50%。保留156个不同任务、原始任务顺序、seed21及相同指数随机数，实现原
到达时间轴的2/3缩放。不改变running48、Host200GB80:20、实际HBM容量、
工具180s、workflow14400s、输出8192及agent行为，不混入新预测/回收策略。
10秒是初次验证值，尚未证明最优，不进行多率扫描或重复实机开发。

V1446绑定原plan/profile/manifest、原始native比较与到达日志的实际sha256，
生成experiments/plans/qwen35_native_poisson_156root_mean10s_20261016/workflow_arrivals.json。
原生load_workflow_arrivals已验证全部156项、顺序及digest；最大缩放浮点差
约1.36e-12秒。状态prepared_not_launched，尚无GPU监督或新结果。
V79b原15秒输入、应用、引擎及helper继续冻结。

| 到达项 | 原15秒 | 新10秒候选 |
| --- | ---: | ---: |
| 平均到达率，root/s | 0.066667 | 0.1 |
| seed21实际平均间隔，秒 | 15.883 | 10.589 |
| 600s前已到达 | 42 | 58 |
| 1200s前已到达 | 75 | 111 |
| 1800s前已到达 | 111 | 156 |
| 最后计划到达，秒 | 2477.769 | 1651.846 |

标称Poisson均值与这156次抽样的实测均值分开。原native日志中156次started/
finished完整，峰值活跃workflow为91；该历史值不外推新轮并发，更不当作物理
工作集。更快到达可能增加同时存活的agent及上下文，也可能只增加队列等待，
迁移量不能按1.5倍推算。

需要纠正“两个方向都很少”的笼统表述：V75 native完整结果D2H为
1935.037GB，其中FULL832.695GB；H2D仅13.610GB，其中FULL3.952GB。
写回多、后续读回少，不是可利用双向迁移充足的证据。原native归因FULL
eviction重算10148 token是已观测范围，不是全部重算；缺失/未知保持原口径。
V79b的2400–3000s恢复爆发也不能代表无额外诊断native的持续工作集。

下一次GPU负载资格先使用native原生策略，不为增加机会而强制驱逐热KV、
重复搬迁终态数据或提前开启新控制器。检查：

1. 多个连续时间窗是否存在真实FULL写回及后续需求恢复，恢复是否被下一请求
   实际使用；按agent工具/JOIN阶段分列，不把Mamba备份GB或已结束child垃圾
   回收充当有用FULL迁移。
2. 实际HBM/Host缓存压力、可回收量、Host驱逐、有用数据重算及首次服务等待；
   活跃token gauge不代表全部物理驻留，排队更多也不自动等于工作集更多。
3. GPU服务与完整任务质量、全部终态、JCT、长尾；纯计算饱和或频繁有用KV
   重算不能仅因H2D增加便宣布理想负载。旧15秒native只作上下文参考，新设计
   的相对性能必须匹配10秒到达表。

有限156项更快到达会把供给时段由约41.3分钟缩至27.5分钟，可能增加收尾
阶段占比。它用于提高瞬时重叠，不保证延长高压时间。若仍缺持续迁移，
优先增加来自同类真实任务的不同root、延长随机供给时段，再考虑降低间隔；
不靠继续压到极高到达率制造短暂抖动，也不声称当前只需调率就一定有效。

### 4.63 V81登记接续；后续窗口仍是写回多、有效恢复少

V81独立入口start_v81_native_arrival_pressure_20261016.py复用原生实验、
完整收尾和实际NUMA检查；只把已有10秒到达表接入原策略。应用87ce506、
完整引擎树53020ce3在独立worktree冻结，不改V79b原源码、引擎或helper。
新引擎仍基于94602c9加完全相同canonical patch，不混入未验证跨分支合并。
V1447/V1448绑定来源、配置、实际156项到达表加载、旧manifest和物理池目标。
这是参数及loader验证，没有为单一到达率改动新增策略测试或GPU证明。

22:18 PDT V1449登记监督3592377/start_ticks638458271，真实ps为nice0；
当时waiting_for_complete_v79b，未提前占用GPU。V79b于22:21:43完整收尾，
原监督/driver实际退出后，V81于22:21:51自动启动driver3595930。
22:24:43 V1450/V1451核验真实首服、实际156项到达表、完整同池及NUMA，
scheduler3596344/start_ticks638478865真实运行。三进程均nice0，
V1455再次核验冻结来源、登记/启动/NUMA凭据和实际进程身份。
V1452仍预留完整收尾，不能把首服当负载合适、质量或性能验收。
准备时最后的pytest-6202已自然完成，不因收尾较慢提前终止。

V81保持Host200GB80:20、HBM FULL1798992 token/Mamba513槽，完整池字节与
原native一致；保留session、NUMA preferred1、running48及所有任务行为。
禁用额外前缀诊断、预测头、PREPARE、预取、额外准入和终态回收。
原native server_info的max_total_tokens/max_mamba_cache_size为null，表示原
启动未显式传参数，不表示实际池无界；原census才是物理容量参考。
新轮显式传同容量参数，首服时同时核对参数及完整census，不能仅凭env验收。
新到达率的性能对照不能直接使用原15秒native作为匹配控制。

等待期间V1453使用既有比较器补齐两个完整时间窗，继续优先最新V79b轨迹：

| 项目 | V79b 3600–4200s | native同窗 | V79b 4200–4800s | native同窗 |
| --- | ---: | ---: | ---: | ---: |
| D2H/GB | 221.375 | 207.155 | 182.711 | 180.360 |
| H2D/GB | 3.048 | 1.108 | 1.581 | 1.286 |
| 其中FULL H2D/GB | 0.344 | 0.078 | 0.551 | 0.127 |
| 平均running请求 | 44.89 | 38.37 | 28.07 | 25.73 |
| 平均waiting请求 | 8.21 | 0.67 | 0.066 | 0.058 |
| decode token/s | 1278.67 | 1212.68 | 1089.84 | 1056.23 |

后20分钟V79b仍写回约404.086GB，FULL恢复却仅0.894GB，说明早期恢复峰值
不能代表全程持续双向机会。后段较高原始token/s伴随较大batch和不同剩余工作，
不称因果加速或JCT收益。GPU采样利用率、传输stream秒数及worker区间均保持
各自语义，不据此推算oracle或可节省停顿。

V1454补齐完整V79b与预先选定的历史native比较，保留全部156项：

| 完整采集项 | V79b诊断 | 历史native |
| --- | ---: | ---: |
| completed / incomplete | 156 / 0 | 153 / 3 |
| 全终态原始duration均值/秒 | 1910.675 | 1665.374 |
| 客户端采集时长/秒 | 7219.416 | 6841.743 |
| 原生D2H/GB | 2882.386 | 1935.037 |
| 原生H2D/GB | 879.088 | 13.610 |
| 其中FULL H2D/GB | 611.358 | 3.952 |
| 已归因FULL eviction重算/token | 10644 | 10148 |

V79b原生JCT资格仅145项，11项guard介入完成，其中3项另缺semantic completion；
原始exit1、分母156和所有标签保留。遥测writer_error/dropped/failed均无错误，
队列和pending均排空，官方任务正确率尚未评分。表中均值不是质量匹配JCT，
不同轨迹、诊断和已实现工作量仍未隔离；迁移大幅增多不能直接归因于某项策略
或宣称出现了论文收益。V1454第一次调用缺少明确timeline参数而拒绝、未写出；
补齐两侧已有密封timeline后成功，原native数据及缓存未覆盖。

接下来查看V81连续窗口的有用FULL迁移、物理工作集、
重算和native暴露等待。若更快到达只提高排队/无用备份，不判作理想workload；
若已有异步强对照覆盖收益，不把共用工程修复升级成论文新贡献。
研究问题、新颖性、纯Attention泛化和同质量性能目标继续未验收。

### 4.64 研究先于GPU：按OSDI目标重新筛选

用户明确要求先想idea，再占用GPU验证，且研究应朝OSDI创新程度推进。
V81提前进入负载实验的次序已纠正：V1456封存停止前状态及精确owned进程身份，
22:29发送终止信号，22:30的V1457确认监督、driver、server、scheduler、client
均退出，nvidia-smi无计算进程。保留冻结源码、注册、首服及部分trace，
不自动重启、不另排GPU、不把部分结果当完整负载/性能结论。研究目标继续，
没有将整体目标置为paused或把旧的三个机制恢复为指定研究主线。

OSDI是选题和论证目标，不是当前候选已达标或保证录用。当前异步写回、恢复
优先级和到达率调参都只作为工程能力/测量对象；“workflow感知”也不是充分的
新意。新候选必须具体回答：

1. 一个真实的动态多agent案例发生了什么，哪个原有抽象或机制使它昂贵；
   不能从一个坏配置、我们的控制开销或人为强制行为推出新问题。
2. 系统凭借什么新信息/执行语义改变了可实现的设计空间，究竟少做了哪些
   必要计算以外的工作；不是为已有策略增加几个优先级和常量。
3. 哪个强简单方案、哪个已发表系统最接近，以及一个它们处理不好的具体
   反例。文献没有报告当前指标不等于没有能力解决它。
4. 收益是否来自有规模的成本对象，而非被原生重叠覆盖的少量传输；候选应
   对纯Attention成立，不以Mamba独有行为或修改任务目标作为前提。
5. 先用CPU/已有trace验证必要条件和失败条件，再决定是否值得实现、启动
   GPU。最终仍需同任务、质量、资源、JCT、有效吞吐和公平/长尾的完整证据。

本轮重新核对原始USENIX页面，扩展强基线而非仅比较native：

| 工作与已核对范围 | 对候选创新的约束 |
| --- | --- |
| Parrot，OSDI24，Semantic Variable API及数据流依赖 [R24] | 暴露LLM调用之间的依赖不能单列为新执行抽象 |
| Agentix，NSDI26，程序上下文及LLM调用抢占/优先机制 [R25] | 将workflow作为单位、缩短程序排队不自动构成新贡献 |
| Murakkab，OSDI26，声明式workflow与跨模型/硬件配置 [R26] | DAG暴露加跨层编排本身已存在；需说明新问题/约束 |
| NanoFlow，OSDI25，设备内异构操作重叠 [R27] | 普通GPU计算/内存/通信流水化不能仅凭agent负载重命名 |

以上按本次原始摘要所述范围筛选，不据摘要声称这些系统不能处理某种动态
控制流、状态迁移或失败。具体候选成立前，仍需通读相应机制与artifact。
原TokenCake、CONCUR、SAGA及CachedAttention等约束继续，不能遗漏新强对照。

**CPU证据更新。** V1458使用现有审计器读取完整V79b，保留实际文件前缀hash：

| 真实工作量 | 完整V79b |
| --- | ---: |
| 有已知extend geometry的prefill批次 | 41046 |
| root + child + summary实际extend token | 44331657 |
| summary请求extend token | 14785177 |
| compaction后首个普通请求extend token | 6347474 |
| 两类compaction关联token合计占比 | 47.669% |
| root相同已观测epoch、同结果重复工具 | 1539次，84个workflow |
| 发出这些重复的请求输出token | 316344 |

“compaction后首请求”是root/child中的子集，不重复加进总extend分母。
47.669%是工作量分布，不是可避免计算、GPU时间比例或JCT上界。后继输入中
有必要的新内容，Transformer不同上下文下的KV也不能直接视为可复用。
普通摘要prompt格式修复、增量tokenization或工具缓存都有强已有方法，
这份数据不恢复旧harness原型为论文机制。先区分可复用计算的真实条件、
不可避免重算及现有方法覆盖范围，再决定是否形成新的系统问题。
原直接文件入口缺scripts模块路径，未生成报告；改用python -m成功，
没有更改GPU服务、模型或原始trace。

当前交付阶段是候选研究论证：每个候选应有具体案例、核心insight、机制草图、
相关工作差异、收益量级和可证伪条件。目前不宣布新的核心创新成立。
只有该论证支持后才安排独立实现/实验，不用密集GPU试跑替代idea筛选。

### 4.65 OSDI选题筛选：不能用大成本或机制拼装代替新意

用户进一步明确idea目标应达到可以投稿OSDI的创新程度。当前没有经过论证的
新主创新；不把“workflow感知”、另加三个控制机制或相对旧native的局部改进
视为该目标已实现。GPU保持停止，不注册新候选，不自动恢复V81。

**本次明确降低优先级的提法。** 以下是对独立贡献的约束，不是声称相关领域
所有问题都已解决，也不根据作者加速比推算当前系统收益。

| 提法 | 本次核对的强已有方法 | 当前判断 |
| --- | --- | --- |
| 异步摘要、提前构造变换后的KV、用工具窗口掩盖变换 | SmoothAgent [R1] 原文包括segment-decomposable transform接口及lookahead KV | 单独提出这些能力不够；尚无它处理不好的具体反例 |
| 分块/并行摘要、增量记忆避免反复处理旧历史 | PCC [R28]、Akashic [R29] 的原文机制 | 可以是强对照；不能将摘要成本大直接解释成新颖性 |
| 按活跃工作集决定Host写入、减少无效备份 | EfficientAgent [R10] 原始摘要与artifact | 当前关闭/收紧写入只能作为工程/已知策略比较 |
| 仓库或工具文本作为推测解码草稿 | AgSpec [R4]、AgentSpec [R13] 原始摘要 | 普通检索草稿和agent级接受率调整不足以单列贡献 |
| 联合sandbox与KV分叉、共享执行环境并作一致性检查 | AgentFork [R30]已有同branch ID的sandbox/KV lifecycle；DeltaBox [R31]、Atomix [R32]分别处理环境C/R与工具事务 | “把二者放在一起”不是充分差异；当前trace也没有自然回溯树证据 |

AgentFork为开源实现，不称其经过同行评审；README明确完整GPU forward over HTTP、
多GPU和GPU/microVM同机实验未运行，不能用其微基准作为完整系统性能证据。
后三者的能力边界按本次实际读取的README/原始摘要及DeltaBox正文描述，
不据此断言已实现任意跨状态全局优化。
PCC和Akashic同样按预印本版本处理。新候选若使用这些机制，应把合理组合放进
强基线，而不是只与不具备这些能力的旧引擎比较。

**CPU证伪：压缩是否经常发生在马上结束之前。** 新只读审计器
`scripts/audit_compaction_followthrough.py`依据INVOCATION_CREATE中的真实parent
关联内部summary，按同一invocation的context_compact、普通LLM请求、下一次
context_compact及RETURN/CANCEL建立区间，不根据名称或最近时间猜测。
右删失、非唯一summary、缺native token字段保留未知。4项CPU用例覆盖交错身份、
连续压缩分段、未知/删失和native测量缺失。未改harness、引擎或GPU行为。

完整V79b报告为
`experiments/reports/v1459_v79b_compaction_followthrough_20261016.json`，
封存156个workflow源和native事件文件的实际读取范围/hash：

| 压缩后观察 | 完整V79b |
| --- | ---: |
| 压缩区间/唯一已完成summary关联 | 670/670 |
| 结束于下一次压缩/同invocation RETURN | 400/270 |
| 到下一边界的普通模型请求P50/P90 | 28/49轮 |
| 到下一边界墙钟P50 | 189.833秒 |
| 仅1轮请求后RETURN | 13次，占全部压缩1.940% |
| 不超过3轮请求后RETURN | 65次，占9.701% |
| 不超过5轮请求后RETURN | 89次，占13.284% |
| 仅1轮后RETURN的summary+首后继登记uncached token | 413564，占全轮extend token0.933% |
| 不超过5轮后RETURN的相同登记量 | 2820093，占6.361% |

后两项是已观察工作量的量级对照，不是可移除计算或oracle上界。跳过压缩可能
触及context硬上限，增加后续attention/驻留成本，改变模型行为或损害任务质量；
真实未压缩反事实未执行，不能把这些token全部当作节省。请求/工具墙钟重叠，
不能由区间时长相加推出可减JCT。原guard/终态/质量资格未重写。

本次结论是降低“临近结束时跳过压缩”作为主创新的优先级：大量压缩之后
仍有持续执行，不能以少数最后一轮案例解释全轮成本。它不否定压缩执行本身
仍可能昂贵，也不证明任意更智能时机策略无收益。

**下一候选的研究投入条件。** 继续优先解释模型计算而非少量已重叠传输：
V1458的44.332M extend中21.133M关联压缩/首后继，另有7.491M生成token。
这些是成本对象，不先宣称存在大规模可复用计算。下一份候选论证必须回答：

1. 选定一个自然案例，写出旧、新请求的实际token序列/依赖，标明哪些计算
   可以安全复用、哪些因上下文改变必然失效。禁止任意拼接不同上下文的KV。
2. 给出一个强已有系统及其合理组合仍需付出代价的具体反例，不能只列关键词
   或把尚未阅读/复现的能力当不存在。主机制须对纯Attention也成立。
3. 说明改变的系统抽象、数据布局或执行方式如何消除这项代价；若只剩阈值、
   普通cache policy或给控制器加priority，按工程改进收尾。
4. 先用CPU/历史trace检验必要条件；新workload只能引入有现实依据的需求，
   不强制无用spawn/回溯来制造收益。当前376个child均为深度1，201个JOIN
   各仅一个声明waiter，不能据此声称已有多消费方/回溯共享机会。
5. 反事实收益须扣除额外计算、常驻内存和控制成本；最终使用相同任务、质量、
   资源、到达流及强基线，比较有效吞吐、平均JCT、长尾和所有终态。

本节完成的是候选排除及研究标准收紧，尚未选定主机制，也不构成整体目标完成。
更早自动goal文本内的分支准入/完整状态预算/容量交接已撤回，不因旧文本恢复。

### 4.66 跨轮计算复用：因果有效性与多轮改写的候选问题

上一轮完成了新的证伪证据，属于研究进展；整体性能/论文目标仍未实现。
继续CPU与文献研究，V81保持stopped_by_user、automatic_restart=false。
本轮没有恢复已撤下的三个机制，也没有改动模型、harness或serving引擎。

**首先限定可消除计算。** “保留文本相同”不推出“其KV仍与新上下文等价”。
本轮核对PatchKV [R33]、SUR [R34]和BCR [R35]的原始版本：

| 已有工作 | 实际核对的机制/范围 | 对新候选的约束 |
| --- | --- | --- |
| PatchKV | 将相邻上下文分成有效prefix、改写span、对齐suffix；选择局部及非局部块修复，其他块按精度tag恢复，并修正RoPE | 改写后选择性恢复、融合反量化/位置修正已是直接强对照 |
| SUR | 按query相关的连贯区域分配修复预算，逐层集中执行所选位置；§3.1、附录7.1/14明确仍读取新旧混合状态 | “修复不保证等价”和分散修复集中执行均不是本项目新发现 |
| BCR | 跨上下文复用有质量成本；按相同预算比较行/块/整调用的选择，并提供三种repair layout | 不能假定给整个请求加一个准入分数就能控制质量或胜过细粒度修复 |
| ACL26 cache reuse分析 [R36] | 在LLM judge中检验候选交互及selection consistency | 单次答案accuracy接近不能代替多agent决策行为验证；但该任务结果不直接外推coding agent |

读取的是对应原文的机制/实验范围，不将论文未报告当前指标视为它们不能适配。
尤其SUR已分析fresh token读取stale history产生的误差，不能把“误差会传播”
本身包装成新insight。PatchKV的对象是2609.26219，不与同名weight-space
compensation论文2609.39329混淆。

**一个可重现的正确性反例。** 仅用于排除任意KV拼接的普遍正确性，
不是Qwen实测、任务质量实验或加速估计。取一个带residual的最小causal
attention层，令Q/K投影为零、V投影为恒等，于是
`h[i] = x[i] + mean(x[0:i+1])`。两次输入为`[1,2,3]`和`[1,4,3]`，
最后一个token及其位置都未改变：

| 量 | 旧上下文 | 新上下文完整计算 |
| --- | ---: | ---: |
| 第一层末位置输出，即可供下一层V投影使用的输入 | 5 | 17/3 |
| 第二层末位置输出 | 17/2 | 187/18 |

若只重算末位置，第二层仍读取中间位置旧的一层状态7/2，而正确值应为13/2，
末位置输出变成169/18，与完整计算相差1。新计算过某个位置，不代表它已脱离
旧历史；第三层的缓存会继续继承这个差异。这里没有位置移动，RoPE修正不能
解释或消除差异。上述数值已用Python Fraction作CPU精确算术检查。
真实Transformer的norm、MLP及不同投影会改变数值，本例不预测其实际误差。

复现核心算术（CPU标准库，不加载模型）：

```python
from fractions import Fraction

def layer(x):
    return [v + sum(x[:i + 1]) / (i + 1) for i, v in enumerate(x)]

old = layer(list(map(Fraction, (1, 2, 3))))
new = layer(list(map(Fraction, (1, 4, 3))))
assert (old[2], new[2]) == (Fraction(5), Fraction(17, 3))
assert layer(new)[2] - layer([old[0], old[1], new[2]])[2] == 1
```

**自然多轮改写的必要条件。** 扩展只读审计器按workflow+invocation分组，
以真实context_compact计数，不以普通context_epoch推进代替改写。V1460保留
与V1459相同的输入文件边界/hash，增加链统计，不覆盖V1459：

| 完整V79b观察 | 数量 |
| --- | ---: |
| 至少一次压缩的invocation | 270 |
| 仅一次压缩 | 146 |
| 至少两次压缩 | 124，合计524次 |
| 至少四次压缩 | 55，合计361次 |
| 单invocation压缩次数最大值 | 19 |
| 多次压缩组的summary登记uncached token | 11488391 |
| 同组压缩后首请求登记uncached token | 4964154 |

报告为`experiments/reports/v1460_v79b_compaction_chains_20261016.json`。
新增2项链分组用例，加原4项审计用例与19项已有工作量审计，共25项CPU通过。
这是自然重复改写的证据，**不是**KV近似误差、suffix精确重叠、缓存物理驻留或
可消除计算的证据；当前native没有启用本文讨论的近似复用。
两类token合计16.453M也不能直接换算成GPU时间或JCT收益。

**一个明确但尚未选定的候选研究问题。**
当同一agent连续多轮改写上下文、后续状态又基于之前的部分修复状态生成时，
runtime能否以低于完整重建的成本管理这些版本，并在完整任务上维持质量？
需要验证的是跨版本的执行成本和质量，不是再次提出单次suffix repair。

候选实现若值得推进，至少应解决以下具体问题：

1. 状态目录记录它对应的实际token版本及计算来源，区分精确prefix、选择性
   修复状态和继承近似历史的新状态。不能将“本轮做过forward”当成精确标志。
2. 在已知相同token前缀处使用现有精确复用；需要完整恢复时，从真正有效的
   prefix重建当前实际输入。恢复当前KV不会撤销先前错误工具操作，也不能
   证明先前输出等同dense执行，必须另测完整任务质量。
3. 若试图跨多轮分配修复/重建工作，必须提出比“每次用固定预算”和
   “每N次改写完整重建”更有根据的机制，并计入来源维护、selector、Host/HBM、
   错误后的补救及重建成本。异步重建本身已有SmoothAgent等强对照。

上述来源记录和普通fallback首先是正确性/工程要求，并不独立构成OSDI贡献。
尚缺的核心证据是：多版本执行存在单次方法或简单组合处理不好的实质代价，
以及新机制能低成本消除这项代价。本轮只保留这个可检验问题，不宣布已选主线。

继续研究前的证伪顺序：

1. 核对实际token对齐，区分“harness重新编码导致全部失配”与有长共享suffix
   的改写。已知摘要XML格式失配先作为工程基线，不计入新的理论收益。
2. 阅读并合理适配PatchKV/SUR/BCR及现有cache编辑接口，比较每次全量重建、
   每次固定repair、周期性重建、始终从精确来源复用；不能假设已有方案没有
   多轮能力。固定任务、输入及等量工作只用于机制测量，最终仍须闭环任务。
3. 若多轮质量/成本没有明显新问题，或周期性重建已以很小成本覆盖，就淘汰
   候选。若需要很重的误差预测/逐token元数据才有收益，同样降低优先级。
4. 只有确认模型通用的新机制和足够收益空间，才安排独立GPU验证；当前不启动。

### 4.67 实际执行成本收敛：当前不优先实现跨版本近似修复

4.66的多轮链统计提供了自然改写证据，但不自动证明足够大的系统收益。
本轮继续CPU证伪，将摘要请求、压缩后首普通请求和其他请求的实际prefill
区间分开。只扩展离线审计，未改harness、模型、引擎或GPU实验状态。

**新增V1461。** `audit_agent_work_redundancy.py`按真实request_id标记阶段，
保留chunk/re-execution的原生extend geometry。混合批次按完整stage set记录，
不按请求数/token数拆分worker墙钟；未知身份仍为unknown。新增2项测试覆盖
混合区间不能重复分摊、未知请求不能假充压缩后首请求；两套审计共27项CPU通过。
报告为`experiments/reports/v1461_v79b_compaction_stage_service_20261016.json`。
其来源文件范围/hash、requests_by_role、context_compaction及原有service统计
与V1458逐项相等，只增加阶段分解，原报告未覆盖。

| 记录阶段集合 | prefill批次 | worker区间合计/秒 |
| --- | ---: | ---: |
| ordinary | 35207 | 639.704 |
| post_compaction | 1681 | 145.939 |
| summary | 3679 | 392.888 |
| ordinary + post_compaction | 183 | 13.310 |
| ordinary + summary | 279 | 21.554 |
| post_compaction + summary | 14 | 1.516 |
| ordinary + post_compaction + summary | 3 | 0.370 |
| 全部prefill | 41046 | 1215.281 |
| 全部decode | 367661 | 5910.805 |

ordinary/post_compaction/summary实际extend分别为23.199M/6.347M/14.785M。
包含post_compaction的1881批worker区间合计161.135秒，其中145.939秒只有
该阶段的已记录请求行；含它的全部区间占prefill+decode区间合计7126.086秒的
2.261%。摘要及首后继虽占47.669%的extend token，却不能据此说占近半GPU时间。
摘要token不能全部算到4.66的“保留尾部跨版本修复”机制名下。

上述区间包含launch、排队/依赖和传输影响，不是独占CUDA kernel时间。
2.261%仅描述记录对象的规模，不是JCT改善上界；队列放大、并行重叠和工作集
变化尚未建立反事实。也不能把161秒全算成可移除计算，或认为它严格限制其他
自然长上下文工作负载。这一结果降低的是当前候选在当前配置上的实现优先级。

**token对齐证据仍缺失。** 本轮直接按V1460的670个首后继request_id连接完整
`server/eviction_attribution.jsonl`的38840个generation_continuation_prefix_probe，
目标中匹配0/670；这表示未知，不是测得suffix重叠为0。
实际读取253422304字节，SHA256为
`0faabe5226cea99af3b144f1a8e9df282448368c9fbe0e2c75d387c7729b7512`；
V1460报告SHA256为
`0461e19bd97b5acd2b9fae4e536a5d5ffd01523dce13a52a277b03061c5d86a1`。
按这些ID对完整行流式查找即可复核。冻结V79b应用c40d2f6的
`v0520_native_telemetry.py::_write_context_prefix`按workflow/context/namespace/
session_id/session_generation限定历史，且只记录前缀比较结果，不落盘完整
token IDs或suffix指纹。不能用其他普通续接的命中或缺探针推导压缩前后对齐。
旧V1133虽有68个root首次摘要的三哈希重建，但属于V73和不同样本，不冒充
最新V79b全部670次改写的token证据。

**本轮决策。** 暂不开发/启用跨版本近似KV修复。候选保留为研究问题，不升为
新主线，不为了得到对齐数据单独启动GPU。选择性修复已有PatchKV/SUR/BCR，
当前候选仍缺token重叠、新机制超过强简单方案及完整任务质量/性能证据；
已测执行规模进一步降低了为它引入复杂控制的理由。

下一项研究应从较大实际计算或新的自然应用需求中提出具体反例，继续检验
模型通用性和已有方法差异。生成执行是本轮最大的已记录worker对象，但区间
占比本身同样不证明存在可消除计算；普通推测解码的收益属于必须纳入的强基线。
不通过减少有效任务/强制额外行为获取性能，不恢复已撤下的Mamba专用或三个
旧机制，不把修复自身开销称新意。GPU保持停止，整体目标仍未完成。

### 4.68 生成阶段筛选：普通草稿已有强基线，完整前缀合并机会未成立

4.67提供了新的实际成本分解，属于研究进展；本轮继续CPU研究，不启动GPU。
目标仍是同任务、质量、资源下的JCT和有效吞吐，而非仅找到一个可实现的小功能。
新主机制仍未选定，未恢复已撤下的Mamba专用或三个旧机制。

**先澄清已有能力。** 本轮重新核对AgentSpec [R13]、AgSpec v2 [R4]：
前者已有semantic-aware n-gram和受观测接受率影响的动态验证预算；后者§3.1
明确将最近N个调用输出保存在会话语料，并在同一session的sequential calls/
agents之间共享，另有workspace/global语料及在线草稿长度调整。
不能把“让parent利用child文本”“根据agent类型调草稿长度”单列为新贡献。
Hydragen [R16]已有shared-prefix attention；TAPER [R37]已有同请求内动态分支
及其内存/重放/调度管理，不能仅以“并行分支+共享prefix”声称新颖性。
这些是已核对能力边界，不据摘要断言它们不能适配某个新的具体机制。

**一个与草稿不同、但尚未形成贡献的候选。** 若多个独立采样分支的完整原生
token前缀、模型/adapter/位置及影响forward的参数相同，可研究共享这一步
模型计算，再让各逻辑请求分别采样，token分歧后分叉执行。不能复制一条完整
随机答案给多个样本；独立采样关系必须保留。相同输出片段不满足完整状态条件。
数学层面的同状态共享不保证不同实际batch数值路径的逐bit复现；实际质量、
状态管理成本及相对trie/beam等强简单实现的新意仍须另证。

该候选先检验一个必要条件：独立分支是否在生成很短前缀后就分散。若如此，
合并元数据、调度和状态管理的成本很难有足够工作可摊销，不先开发GPU路径。

**公开样本CPU筛选V1463。** 原官方Hub/viewer的直接TLS读取失败，V1462未产出
统计结果。随后经公开镜像，固定OpenR1-Math-220k [R38]提交
`e4e141ec9dea9f8326f4d347be56105859b2bd68`，下载第一个default Parquet分片
213636225字节，完整验证其SHA256等于所捕获镜像元数据的LFS SHA256
`ccc3a95e4875dc4cff9bec2f0fb1c93e4c9cbb24fb24853fd0b96c416e1a263c`。
仅在内存解析前100个物理行，不保留整分片；元数据和完整样本约3.6MiB。
该验证保证本次读取与捕获的镜像元数据一致，不称已独立证明镜像真实性。
它也不假定Parquet行序等同viewer API行序。

新增只读`audit_parallel_generation_prefixes.py`保存来源、reader版本和哈希，
使用排序相邻LCP求字符prefix树边数，保留样本multiplicity，不对答案正确与否、
finish_reason或reasoning_complete另作筛选。只删除精确开头`<think>\n`的对照
单列；不假设这个标记属于真实生成token。没有生成新答案或下载模型权重。

| 固定样本观察 | 原文 | 删除固定开头标记后 |
| --- | ---: | ---: |
| 问题/回答 | 100/212 | 100/212 |
| 回答字符总量 | 3453820 | 3452124 |
| 前缀树可合并的重复字符 | 3920 | 3024 |
| 占字符总量 | 0.1135% | 0.0876% |
| 两两LCP P50/P90 | 14/81.5字符 | 6/73.5字符 |
| 最大两两LCP | 221字符 | 213字符 |

94题有2条回答、6题有4条，均无完全重复答案。本次没有排除任何已取样行；
100题有reasoning_complete字段，finish_reason仅8题提供，其余保持未知。
报告为`experiments/reports/v1463_openr1_parallel_prefix_screen_mirror_20261016.json`。
8项CPU用例覆盖树边计数/内部子串不能共享、独立样本multiplicity、固定标记、
未知/截断拒绝及Parquet原序提取；保留样本重新计算与报告、文件哈希均一致。

这是公开数学长推理的便利样本，不是当前Qwen coding workload。源数据发布方
的整理/筛选与采样设置不是本项目控制变量。字符树不是原生token树，也不是
forward次数、FLOPs、推测接受率或可得加速的上界；实际并发/模型状态未验证。
本次只是在假设同题各回答可同时合并的有利条件下，发现2–4分支很早分散。
因此降低这个具体合并候选的优先级，不外推为所有并行推理都无收益。

**最新V79b的请求入口筛选V1464/V1465。** 当前ready明确
`record_output_token_ids=false`，完整native结果没有output_token_evidence，
所以不能用解析后正文/工具参数重建全部原生生成序列来报接受率。
本轮按callback的prompt_semantic_sha256另作保守筛选，随后加原生长度检查：

| 完整V79b观测 | 数量 |
| --- | ---: |
| llm_submit/有语义指纹 | 40718/40718 |
| 不同语义指纹 | 40142 |
| 重复组/其中请求 | 576/1152 |
| 重复组中client请求区间重叠对 | 0 |
| 跨invocation/跨workflow重复组 | 1/0 |
| 组内原生输入长度不同 | 575 |
| 首请求stop/length且callback正文、工具调用均空 | 575 |

所有原生长度已知。原生长度相同仍不证明token/执行状态相同；不同语义指纹
也可能渲染为相同token，因此本表不作“所有完全相同输入为零”的穷尽结论。
重叠区间定义是client submit/result，包含排队和交付，并非GPU同时执行。
V1465同时保留request身份、原生输入/输出长度及callback字段，可逐项复核。

抽查django-10097同一指纹请求：原生32780/32782 token，首回调可见正文0、
第二次1868字符。实际冻结应用的EmptyReasoningRecoveryMiddleware在空白终态
后用相同消息、enable_thinking=false重试；语义digest没有完整覆盖该extra_body
模板开关。因此不能把576组当作可直接复用的同状态forward。当前native缓存
依旧使用真实token，新增检查仅修正离线解释，不是发现已有原生错误复用。
575个首空白回调请求合计生成236081 token；这是共用恢复成本，不是并发合并
机会、全部可避免工作或论文新贡献，完整质量/恢复策略仍须独立评估。

本轮源码修改仅限审计，新增/相关38项CPU检查通过。V1464保留初始语义筛选，
V1465增加原生合同检查，不覆盖旧报告。CLI只打印重复统计摘要，完整组保留
报告，避免大量逐请求输出。GPU保持停止，既有实验源码和raw未改。

**选题结果。** 不据上述数据实现新的草稿共享或完整前缀合并控制器。
前者尚无超过AgSpec/AgentSpec的机制差异；后者既缺当前原生等价状态的并发
证据，也未在这批自然2–4样本中显示有规模的持续前缀共享。继续寻找真正改变
计算或状态执行方式的系统问题；生成阶段成本大只是测量事实，不是已发现
冗余或新机制。整体目标继续，OSDI新颖性与端到端性能仍未实现。

### 4.69 OSDI候选论证：跨运行时物理显存交接，先检验简单方案

用户明确要求按可投稿OSDI的创新程度想idea。本节把这一要求落实为选题标准，
不据会议目标宣称当前已经找到主贡献。保持CPU研究；没有启动GPU、安装新的
GPU库、修改运行引擎，或为候选恢复自动队列。

**选题需要回答的五个问题。**

1. 真实应用在什么条件下损失了计算、容量、带宽或完成时间？必须有自然任务的
   执行路径及资源证据，不能只给一个可能发生的极端例子。
2. 现有系统的哪一项抽象或机制导致这个损失？一个未启用的优化、配置问题、
   自引开销或把已有控制器放到agent负载上，均不足以作为论文问题。
3. 新机制利用什么以前的方法没有表达或没有有效使用的信息？必须指出状态、
   有效性、安全条件和实现位置，不能只说“感知workflow”。
4. 最强的简单组合和相关系统为何仍不能取得相同收益？不能只胜过未适配native。
5. 在保持任务、质量、资源、到达流及全部终态时，收益是否足以覆盖系统新增
   成本？迁移GB、worker区间和CPU累计时间均不能直接当作可减JCT。

这是本项目的研究筛选标准，不是声称OSDI存在固定加速百分比或这些条件足以
保证录用。旧预取、固定优先级和专用Mamba策略不因换一个标题重新成为主线。

**候选问题：LLM服务与有状态GPU工具之间，怎样安全交接物理显存？**

考虑一个自然应用：工具保留模型或执行状态，等待LLM产生下一步动作；LLM
执行时也需要KV和临时空间。此时“agent暂时不运行”不等于“工具状态无用”，
而“KV页可复用”也不等于“另一运行时已经可以分配这部分物理显存”。
研究对象是两个运行时真实占有的内存及其使用依赖，不是再次预测RETURN时刻。
该问题在纯Attention模型上同样可以提出，但是否有规模与新意仍未成立。

V1466只读核对最新完整V79b的冻结应用及其实际symlink引擎。FULL和Mamba
设备池合计69,939,384,320字节；normal FULL池持有torch.zeros分配的tensor，
TokenToKVPoolAllocator.free把索引还给池。已有post-capture VMM backing
也被明确登记，不能声称原生没有VMM。上述源码只证明“回收索引”和“交回物理
分配”不是同一操作，不证明本轮发生过GPU工具争用或这69.939GB都可安全借出。
权重使用BF16，不能按FP8假设推算可借容量；startup可用内存也不是最终空闲量。

V1467进一步核对公开PARTNR固定提交
ddfff19f4b6c098a31edea4d19e7b75db72433c2，保留5个源码文件共78,563字节及
SHA256，没有下载场景数据或执行应用：

| 源码事实 | 证据范围 |
| --- | --- |
| EvaluationRunner的episode循环保留env_interface，交替step与规划 | 是保留状态的调用路径，不是每轮重建环境 |
| EnvironmentInterface支持CPU/CUDA，并在self.device创建成员状态tensor | 证明有GPU配置能力，不证明某次运行选了CUDA |
| FindObjectTool内部调用self.llm.generate | 是工具到LLM的真实控制流，不是已经观测到资源循环 |
| NnSkillPolicy提供to(device)，本地LLM路径使用model.device | 必须比较简单设备迁移；不能假设没有现成offload |

该证据比“多模型README”更具体，但仍没有同GPU物理驻留、工具显存规模、
迁移安全性、allocation失败或关键路径等待。当前SWE-bench trace也不能补出
这些信息。PARTNR可能使用远端LLM、不同GPU或CPU环境，且当前硬件是否支持
其渲染路径尚未核验；不能直接指定它为新主workload。

**强相关工作首先限制了什么。**

| 相关工作/能力 | 本轮核验范围 | 对候选的限制 |
| --- | --- | --- |
| Prism v3与kvcached [R40] | 论文及官方README | 弹性物理KV、虚实地址解耦与多运行时共享本身已有，不能重做一层池作为贡献 |
| Pythia v2 [R41] | 正文§3–4 | workflow预测、预热、lookahead与阶段资源调整已有；“按agent阶段调整配额”不够 |
| MORI v1 [R42] | 正文§2–4 | 相对闲置度排序、两级放置及容量准入已有，不能把WAIT_TOOL卸载重新包装 |
| CrossPool [R43] | 原始摘要 | 权重/KV分池与流水线已有；不能据“统一管理模型状态”泛称新颖性 |
| ThunderAgent及Murakkab [R26,R44] | 原始论文/会议页面 | 程序感知与跨层编排是必要对照，不能仅由跨组件声称新执行抽象 |
| Ghost [R45] | 作者页面确认标题和链接，PDF读取403 | 尚未完成机制比较，不能宣称其不支持所需状态隔离/回收 |

CrossPool正确编号为2606.24506。此前研究交接笔记里的2606.22515没有被本轮
作为有效来源，也不据该错误编号作机制判断。kvcached当前README列出
SGLang0.5.20/hybrid支持；这是上游能力描述，未在本地冻结patched引擎验证。

**普通offload可以解决的情况不算新问题。**

设C为设备容量，W为共同不可移出的占用，S为等待工具的状态，B为LLM回调的
工作集。若W+S+B>C而W+max(S,B)<=C，状态错峰确有潜在容量收益。
但这一不等式本身只描述常见超额驻留：简单CPU offload、整服务sleep、
kvcached配额hook或固定callback预留都可能解决，不能据它宣布新机制。
若S无法迁移且本就必须常驻，增加控制器也不能凭空腾出容量。实际数据移动、
重建和同步成本必须另算，不能由容量关系推导吞吐收益。

因此仅在进一步证据表明下列约束有实际规模时，才研究新的运行时协议：
工具状态存在活跃别名或在途CUDA操作，不能由一个字节配额安全撤回；
工具内部还可发出LLM调用，跨组件等待使固定生命周期或单一callback预留产生
可测损失；现有独立池/内置offload组合没有表达这些使用依赖，且补齐普通hook
仍不能获得相同效果。这些目前全部是待检验条件，不是已有方法已失败的结论。

**有条件的机制草案，不是已选实现。**

若候选通过上述比较，系统侧需要表达每块状态的所属运行时、真实使用引用、
完成事件、迁移方法及恢复所需容量。借还过程应在安全点确认旧用户停止访问、
完成必要保存后再交出物理页；使用方恢复所需状态及数据依赖后才继续执行。
工具失败、取消和嵌套回调还需避免引用失效、永久占用和无法推进。实现必须
落在allocator、数据迁移与运行时接口，不能让LLM猜测这些正确性条件。

这些要素已有OS/虚拟内存/资源管理基础，仅把它们列成三项不产生新颖性。
真正需要证明的是一个现有强方案不能覆盖、且新机制在自然负载上以更小成本
解决的具体状态交接问题。任意深度递归和不可撤销占用不能靠一个有限设备的
协议无条件保证前进；所需的资源边界与普通准入控制也必须如实比较。

**下一步CPU论证与停止条件。**

先检查真实应用的GPU对象创建、使用、等待和释放路径，区分共享权重、会话
私有状态和可立即释放临时空间；拿到自然调用记录后再建立物理内存时间线。
分别对照最佳静态切分、kvcached加工具起止配额hook、callback容量预留、
工具自带CPU offload/设备迁移和整运行时sleep/park；另完成Ghost全文比较，
再讨论是否需要新协议。不得人为分配无用tensor或制造额外回调来做正例。

若只有独立模型常驻，或简单hook/预留已拿到主要收益，就把候选降为工程适配。
若当前应用没有真实GPU工具工作集，不能用压缩SWE-bench到达间隔替代它的证据。
没有自然驻留证据、剩余的机制差异和有规模的端到端成本对象之前，不写新控制器，
不启动GPU微基准，更不把这个候选写成已经达到OSDI程度的主贡献。

本轮结论：提出了更具体、模型通用的跨运行时问题，并补齐一条自然应用的源码
路径；还未证明其必要性、新颖性或性能。旧KV迁移方向不被强行续命，新的整体
性能目标保持active，主论文机制继续未选定。

- `experiments/reports/v1466_gpu_tool_memory_contract_research_20261017.json`
- `experiments/reports/v1467_partnr_state_lifetime_source_screen_20261017.json`

### 4.70 PARTNR候选继续筛选：调用路径不足以支持新显存协议

上一轮4.69完成自然源码路径和强对照要求，是研究进展；本轮继续用同一固定
提交检验候选，不启动GPU。结论是降低跨运行时显存交接的实现优先级。该结论
针对当前证据是否足以选主线，不是证明所有GPU工具都没有容量问题。

V1468保留6个新源码/配置，共29,776字节及SHA256，重新核验V1467的5个
来源不变；仅用PyYAML读取基础配置并计算一个tensor的声明形状，没有导入
PARTNR、合成完整Hydra配置、加载权重或执行CUDA。

**新证据如何改变判断。**

| 核查项 | 结果 | 对选题的影响 |
| --- | --- | --- |
| 所查multi-agent示例 | 选择oracle_rearrange_agent等oracle动作工具 | 不支持“大型学习策略必须跟随每个workflow常驻”的推断 |
| 基础配置的recurrent hidden state | 1×(2×2)×512=2048元素；若FP32为8192字节 | 单个该tensor不足以证明大规模会话私有状态；不是环境总量上界 |
| 本地LLM放置 | llama.yaml声明hf，模型路径是占位符；加载使用device_map=auto | 不能当成已评估的同GPU共置部署 |
| 既有to(device) | 基类移动_cur_skill_step，NN子类还移动模型等成员 | 有简单迁移入口，但不等于完整保存/迁移所有环境状态 |
| 新建的状态成员 | reset仍分别创建hidden state、prev_actions、masks | 必须按实际对象核算，不能只调to(cpu)就声称释放全部私有状态 |
| 生成结果生命周期 | 开启output_scores和return_dict_in_generate，结果挂在self.response_raw | 先检查正常使用和引用释放，不能把额外保留直接当成新协议需求 |

8192字节是基础配置下单个tensor的条件计算，不是实测驻留。场景几何、纹理、
观测、模型权重、其他tensor和allocator reserve均不在该计数内，命令行和
Hydra组合也可能改变形状；不能从此表反向宣布仿真环境很小。

HF wrapper在生成后调用empty_cache；PyTorch官方语义只释放未占用的缓存，
不能由该调用推断活跃对象已经释放[R48]。本轮没有枚举实际返回tensor或其
消费者，不宣布response_raw泄漏、无用或造成了OOM；若后续确有生命周期
冗余，应在各臂匹配修复，不能用它支撑论文收益。

**抽象本身还需要更强的相关工作约束。**

GPU多任务vision论文[R47]的§3.2讨论每应用虚拟内存、按需物理分配与透明
撤回，§3.3讨论compound AI组件间同步。跨组件显存和使用依赖不是全新的
问题表述。该文是愿景论文，不等于已有完整实现，也不能仅凭它否定所有具体
机制；但新候选必须指出更具体的未解决对象。Ghost全文第二种URL仍403，
保留未完成，不据不可访问推断其缺少能力。

据此先把候选状态对象映射到必要的普通对照：

| 对象 | 首先比较什么 | 当前剩余机制差异 |
| --- | --- | --- |
| 共享且不变的模型权重 | 静态切分、弹性物理池、模型sleep/park | 未证明 |
| 已知成员的小型私有状态 | 完整枚举并打包迁移，核算普通hook成本 | 未证明 |
| 持有的生成结果 | 核实消费者与正常引用释放 | 未证明 |
| 仿真后端或其他不透明状态 | 真实规模、分配API、可安全保存与恢复路径 | 未建立事实前提 |
| 工具内再次调用LLM | callback预留、普通准入及生命周期配额hook | 未证明有规模的损失 |

这份表没有宣称简单方案已经覆盖全部收益。它说明当前还没有一个对象同时
满足“真实的大成本”与“强简单方案仍有缺口”。在这种证据状态下实现借还
控制器，会先增加复杂度，再寻找需要它的workload，不符合当前研究顺序。

**当前决策与下一步。**

将4.69的候选降为低优先级，不指定PARTNR为新主workload，不为它安排GPU
资格实验，不实现新控制器。保留自然控制流证据；以后只有出现真实物理驻留
与等待记录，且普通迁移/回调预留不能拿到主要收益时，才重新提高优先级。
这个决定不恢复旧三机制、Mamba准入、输出上限或预取时刻优化作为论文主线。

后续选题继续要求明确减少哪些计算、搬运或同步，以及动态agent信息为什么
不可由普通请求/生命周期信息替代。优先筛查现有trace或公开真实应用能提供
资源成本证据的对象，不把一次调用关系、可能的资源循环、API缺少一个字段
或抽象愿景当成已观测系统瓶颈。整体性能目标仍未完成，GPU继续停止。

- `experiments/reports/v1468_partnr_memory_baseline_screen_20261017.json`

### 4.71 最新前端供给核查：有重复工作，但未证明容量瓶颈或新机制

4.70降低显存交接候选优先级后，本轮回到最新完整V79b及历史V75 native，
检验“长上下文反复进入前端，会不会使短续接请求无法及时供给GPU”。
只运行CPU审计，没有启动GPU、改动服务端或恢复任何旧控制器。

V1469沿用已有audit_native_system_costs核对完整V79b的40,718个原生请求。
实际读取170,564,044字节，SHA256与V1458/V1461一致，无不完整尾行。
全部请求有完整入站阶段计时。V1471/V1472再以V1454已验证时钟与采集范围，
分别按600秒、10秒窗口计算区间累计、并集和最大重叠，保留最后不足整窗的部分。
原生llm_submit在首次服务处写出、ts_ms却是更早的入队时刻，不能用该字段
作为前端到达。本审计使用api_received_ts_ms；未服务或未记录的请求不在分母。

| 观察 | 最新V79b诊断 | 历史V75 native |
| --- | ---: | ---: |
| 已观测首次服务的请求 | 40,718 | 36,786 |
| 对话转换墙钟P50 | 51.818ms | 51.815ms |
| 对话转换墙钟均值 | 52.042ms | 52.143ms |
| 入站至原生入队P50 | 85.398ms | 84.881ms |
| 转换区间累计/并集 | 2119.030s | 1918.133s |
| 转换最大观测重叠 | 1 | 1 |
| 全采集范围转换区间覆盖 | 29.352% | 28.036% |
| 最繁忙600s窗口的转换覆盖 | 42.048% | 38.748% |
| 最繁忙10s窗口的转换覆盖 | 53.134% | 53.105% |

这些百分比描述记录到的墙钟区间，不是CPU利用率、GPU空闲或可消除运行时间。
采集范围包含末尾无请求的部分；窗口数据已单独保留，不只用全程均值判断。
两轮的10秒窗口均未达到80%覆盖，但更短突发、API计时开始前的等待、CPU共享
干扰和未服务需求仍未被排除。worker区间也包含CPU提交和依赖，不能用其高
覆盖率证明GPU持续忙碌，或把前端区间都视为隐藏。

**累计成本差额主要来自请求数，不是观测到的单请求处理变慢。**

V79b比native多3932个请求，转换累计增加200.897s。用native平均时长分解：
请求数项为+205.026s，均值变化项为-4.129s。两项和等于总差额；这是描述性
算术分解，不是对相同prompt的因果比较，也不解释为什么多出这些请求。
不能仅由V79b的转换累计更大便判定前端实现退化。

V79b中39,516个同逻辑context续接请求的转换累计为2075.686s，确有重复处理
对象；所有转换累计平均每个workflow为13.584s。这个工作量比较既不是每个
workflow都能省13.584s，也不是通过排队放大后的收益上界。
V79b有11项guard介入且官方质量未评分，历史native有3个incomplete；
本轮不把两个原始全终态均值解释为质量匹配性能结论。

**已有方法与选题决定。**

TokTier当前原始摘要已明确CPU/GPU session增量tokenization及按类型分派，
Delta Tokenizer v1已研究精确增量与segment并行[R7,R8]。原生Session追加/
分叉接口在第4节也已核验；普通有状态输入和跳过未变前缀不能单列为新贡献。
本轮没有比较它们在本地Qwen模板的完整能力，更不能由native未启用推断缺口。

当前不以“前端反复处理整个输入”选定OSDI主线，也不为其重写session协议。
数据支持它是一个可优化成本对象，尚不支持它是本负载的主要容量瓶颈或足够
大的独立收益来源。若后续出现真实短调用高到达率或较大模板处理成本，应先
用上述强方法检验，再提出超出其表达能力的机制。

新增只读脚本audit_frontend_window_load.py按现有来源封存习惯保存原始前缀、
参考报告、时钟和源码hash，输出不覆盖旧报告。17项相关CPU检查通过，覆盖
重叠并集、跨窗裁剪、最后部分窗口、到达/入队区别、缺失/非法计时和重复身份。
V1471/V1472的全局统计与原始前缀完全一致，改变的只有窗口分辨率。
当前主机制仍未选定，整体性能与质量目标未完成，GPU保持停止。

- `experiments/reports/v1469_v79b_frontend_costs_20261017.json`
- `experiments/reports/v1470_frontend_window_load_cpu_20261017.xml`
- `experiments/reports/v1471_v79b_native_frontend_window_load_20261017.json`
- `experiments/reports/v1472_v79b_native_frontend_bursts_20261017.json`

### 4.72 MoE生成执行筛选：参数规模可核实，相关方法需区分部署边界

上一轮前端窗口审计产生了新trace证据，降低了普通session重写的优先级。
本轮转到生成执行，检查是否能利用多agent信息减少专家权重访问；仍不启动
GPU、不加载模型tensor、不改运行引擎或路由。

V1473解析本地Qwen3.5-35B-A3B的config、index及13个safetensors分片的JSON头。
配置/index共191007字节，权重头前缀共33928字节；合计只读224935字节，
没有读权重payload或导入ML库。精确匹配主干language_model层，排除MTP、
vision、共享expert和其他权重，不能用index总量充当运行时实际加载量。

| 主干routed-expert元数据 | 核查结果 |
| --- | --- |
| 层数/每层专家/每token top-k | 40 / 256 / 8 |
| gate_up_proj | 40份BF16，[256,1024,2048] |
| down_proj | 40份BF16，[256,2048,512] |
| 主干routed权重总计 | 64,424,509,440字节，64.425GB |
| 单层单expert参数 | 6,291,456字节 |
| 若每层执行top8，一个token选中参数的逻辑体积 | 2,013,265,920字节 |

80个目标tensor均核对shape乘dtype字节数与header的data_offsets差额相等，
全部40层各有两个分量。封存的是小元数据及头前缀hash，不是完整权重内容
完整性验证，也不是对内核实际读了多少字节的测量。

**需要避免的两个错误推导。**

第一，64.425GB是routed参数集合，不是每次decode都完整读取或传输64.425GB。
选中参数体积也不是实际HBM事务，更不是Host到GPU流量。cache、内核tile、
数值格式、执行路径和重复访问都影响实际成本。不能拿它除带宽直接估算JCT。

第二，批越大、选中的专家越多，不自动意味着吞吐越差。仅作解释，若每token
独立均匀选k个不同专家，单层期望并集为E×(1−(1−k/E)^B)。同一假设下：

| 示例batch token数 | 单层期望专家并集 | 全层选中参数GB | 每输出token对应GB |
| --- | ---: | ---: | ---: |
| 1 | 8.000 | 2.013 | 2.013 |
| 20 | 120.333 | 30.283 | 1.514 |
| 48 | 200.230 | 50.389 | 1.050 |

这是假设算例，不是Qwen真实路由。批总量增加和每token摊销下降可以同时成立；
不能用平均batch代入这个非线性公式推算整轮流量，也不能据此选择新调度策略。

**现有trace的识别边界。**

V79b真实startup字典用ast.literal_eval解析：BF16、无量化、TP1/EP1、
running48、enable_eplb=false、expert_distribution_recorder_mode=None、
enable_return_routed_experts=false、speculative_algorithm=None。
冻结引擎的内置capture在相应flag关闭时返回None，distribution recorder走
no-op。因此当前已登记来源没有可用于按token/层/agent分析的真实路由队列。
不能由任务文本、parent/child身份或总batch猜测专家重叠率，更不能据此编造
“同workflow更相似”的训练标签。

V1461记录367661个decode批、worker区间约5910.805s。该区间含CPU提交和
依赖，不是expert kernel独占时间，不能把生成对象较大当作权重带宽瓶颈证明。
本轮没有启用路由采集、复用旧trace缺失字段或新建GPU排队任务。

**相关工作收敛了哪些普通方向。**

Semantic Parallelism/Sem-MoE已有语义分组与模型/数据协同[R17]；其通信目标
要与当前单卡区分。ELDR用prefill签名将请求分配到PD分离的不同decode worker，
不改变内核或batching[R49]。MoE-Gen则面向离线单卡，以多个attention小批
积累expert批并管理offload[R50]。亲和性分组和模块级分批已有机制，但不能
将多worker放置直接当作当前单GPU执行方案的完整对照。

本轮最初把ELDR误写成单卡dataflow runtime；正式v2题名及§4–5已经核对，
上述范围与V1473已更正。MoE-Gen题名也按原始v1更正。该更正不影响本地
权重头及startup数据，但撤回“ELDR已直接覆盖单卡局部执行”的推断。

若要继续，必须证明一种实际的跨轮/分叉行为，使直接路由签名和上述强方法的
合理适配仍留下有规模损失，而workflow信息提供了额外可用信息。在线模块级
执行与离线攒批的区别可以继续论证，不能仅据ELDR否定，也不能直接宣称新颖。
当前没有这样的反例或实测亲和性，暂不实现新的专家调度器，也不为参数规模开GPU。
已有native的批量/精度/推测路径优化也应作为公平工程对照，不能把简单调参
获得的收益写成新机制。最终仍须保持任务、全部终态、质量和资源相同。

一个可继续筛查的具体问题是：已经入队且数据依赖就绪的在线请求，能否在
attention与expert模块采用不同执行粒度，同时约束活跃KV、关键请求延迟和
重复状态恢复？模块分批本身已有MoE-Gen，设备内分层流水线还需对照NanoFlow
[R27]；有界等待的普通适配、原生动态批及合理running上限都是必要强对照。
仅把离线版本加timeout，或提高并发上限，不足以成为新机制。必须先找到它们
在自然动态执行中的具体失效，说明agent信息能提供什么额外的、当时已可知
的信息；当前尚未建立，不安排实现或GPU资格。

本轮进展是把新的GPU内部成本对象与PCIe迁移分开，核实其元数据量级并识别
直接相关工作；尚未选定主论文机制或取得性能收益，整体目标继续。

- `experiments/reports/v1473_qwen35_moe_weight_scope_20261017.json`

### 4.73 在线模块执行的必要条件：拆批不产生新token，供给不等于可执行状态

上一轮4.72补齐权重元数据与路由记录能力，并更正相关工作适用范围。本轮继续
分析同一候选，不换成实现任务或启动GPU。V1475重新读取V79b及V75 native的
完整已登记请求，V1476封存启动配置、理论条件及相关工作比较。

**先约束“扩大expert batch”这一收益来源。**

固定一层、纯autoregressive decode、无推测验证或已知多token prefill时，
每个已准入请求最多提供一个当前frontier token。下一token仍依赖当前完整
模型执行与采样。将相同的A个请求拆成多个attention小批，不会让该层expert
模块凭空得到超过A个不同输入token。

这里必须区分模块输入和路由展开：k=8时，总routed条目最多8A，每个expert
最多收到A个token。当前相同48请求集合的对应上限为48/384，不是把384个
路由条目算作384个独立序列token。

这不是新的系统机制，也不否定所有拆批收益。较小attention批和相同expert
token集合仍可能改善硬件重叠，须对照NanoFlow。已知prefill或推测验证可产生
每请求多个token，更多准入请求也可扩大集合；这些分别需要现有混批/推测
路径和实际状态容量的强对照，不能从纯decode条件外推全系统上限。

**最新trace中，到底有多少额外请求供给？**

新只读脚本audit_registered_request_supply.py按唯一request_id配对原生入队
与完成。全部40718/36786个已记录请求均闭合；写出顺序不是入队顺序，先配对
再排序时间端点，按已验证采集时钟裁剪并保留最后部分窗口。11项CPU检查通过。
未服务或未记录请求不在分母，不能填成零需求。

| 已注册未完成需求，包含排队 | V79b诊断 | V75 native |
| --- | ---: | ---: |
| 时间加权均值 | 47.990 | 42.095 |
| 峰值 | 136 | 117 |
| 至少48个的时间 | 3367.903s / 46.651% | 2734.827s / 39.973% |
| 至少64个的时间 | 2557.300s / 35.423% | 1793.123s / 26.209% |
| 至少96个的时间 | 974.413s / 13.497% | 450.247s / 6.581% |

供给主要集中中段。V79b在2400–3000s需求均值119.918、峰值136，而4200–
4800s均值28.485、峰值40；4800s之后更低。历史native在2400–3000s均值
97.452、峰值117，4200–4800s峰值32。不能用一个中段峰值代表全程持续供给，
压缩同一有限任务流的到达时间也不自动延长稳定高负载。

上述需求包含尚未prefill、需要恢复或仍在准入队列的请求，不是某层已经就绪
的expert token数。它既不能证明running64/96可行，也不能当作潜在加速比例。
状态共享、物理驻留、预留新token、临时buffer和逐层数据依赖仍待核算。

**强简单配置必须先排除。**

两轮真实startup字典均用ast.literal_eval核查：running48、FCFS、
chunked_prefill_size4096、max_prefill_tokens16384、mixed_chunk=false、
overlap未禁用、无推测。V79b来源沿用V1473，native的server.log SHA256为
7325687f0477cdd9436921155567bb2dbd3310775cf18616a75c1dda6903c6be，均为第8行。
当前没有更改任何开关或容量。
原生overlap调度标志也不等同于NanoFlow的GPU内算子重叠或模块分批，不能由
该flag推断两者具有相同执行能力。

如果新设计的收益仅来自多容纳一些请求，必须先比较相同物理池及完整内存预算
下的原生容量自适应/更高running上限。平均active token占用不能证明峰值可放下，
也不能靠扩大池制造优势。现有prefill/decode混批也是候选对照，但本地Qwen
混合模型/backend的完整兼容性尚未验证；不能由一个flag存在就宣布可直接开启。
这不恢复用户撤下的prefill延迟策略，也不在本轮新建GPU配置。

**相关工作没有留下一个可以直接命名的新贡献。**

MoE-Gen已做attention/expert模块分批和状态offload[R50]；其离线权重迁移
收益不能直接搬到当前resident-weight场景。NanoFlow不只讨论离线，它已有
在线评估和Mixtral评估[R27]。因此“在线+MoE+不同模块批量”本身还不够。
其合理适配、普通动态批和有界等待版本都须作为强方法，而不是只对照native48。

当前更具体的研究要求是：在自然到达和依赖中，指出这些方法仍无法有效处理的
真实计算/状态交接对象，证明agent信息提供了额外的、当时已可知的信息。
若只是一般请求分批、配置调优或已有流水线适配，应作为工程对照收尾。
若扩大expert批依赖额外KV交换，还须计入新增搬运、延迟和重算；不能把模块
小激活tensor误当成整个请求状态已经变小。

本轮决定：暂不实现在线模块引擎，不将更宽batch或在线适配认作论文主机制。
这不证明单卡执行方向无收益；它排除了一个没有额外token/状态来源的收益
解释，并给出了实际供给阶段和必要强对照。整体目标继续，GPU仍停止。

- `experiments/reports/v1474_registered_request_supply_cpu_20261017.xml`
- `experiments/reports/v1475_v79b_native_registered_request_supply_20261017.json`
- `experiments/reports/v1476_online_module_execution_scope_20261017.json`

### 4.74 容量核查：legacy resident_pressure不是真实物理驻留或空闲量

本轮核对V79b实际冻结引擎及客户端，V1477封存源码与两轮原始metrics/census。
它进一步约束4.73的原生更高并发对照，不能据低占用均值直接判定64/96可行。

在本模型的hybrid统计路径，num_used_tokens取FULL池的
U=C_sched−(available+evictable)，不包含Mamba池。客户端仅保留四个原生
gauge，并把U除以pool_tokens写成resident_pressure。这个legacy名字容易
让人误读；它不是物理resident字节，也不是free比例。即使容量单位匹配，
C−U也只能给出available与evictable之和，不能区分立即空闲和需要回收的缓存。
token_usage又取FULL/Mamba使用比例的最大值并保留两位小数，不能混用分母。

| 采样观察，均非精确阻塞时长 | V79b | V75 native |
| --- | ---: | ---: |
| 采集范围内有效样本 | 6655 | 6334 |
| 报告有队列的样本 | 3068 | 2545 |
| 其中报告running=47 | 2307 | 1880 |
| 其中报告running=48 | 222 | 199 |
| running48且有队列时，U/C_census中位数 | 45.005% | 46.315% |

最后一行不证明还有约55%空闲HBM，也不包含Mamba即时可用性。running=48
样本较少也不能当作配置上限很少起作用：prefill统计报告已有running请求，
新prefill序列另记，decode则报告当批；gauge还可能停留在上一报告。没有
同一准入时刻的已占请求槽/新prefill集合，不能由47推断恰有一个槽位可用。

**原生有多重约束，而不是一个使用率阈值。**

Scheduler.get_num_allocatable_reqs综合PP预算与请求池可用槽，PP1下真实
running48已用完该预算。之后PrefillAdder还要计算已有请求的输出工作预留、
候选extend/输出/page开销，并在保护候选前缀后复核。Mamba状态、共享引用、
在途恢复和临时buffer另有约束；本轮static-separate census也不能直接套用
共享allocator的所有分支。源码估算clip的默认值4096不等于实际输出上限，
环境可覆盖，不能当作已观测的有效值；本轮没有改任何输出或工具限制。

已有metrics没有保存分别的FULL可用/可驱逐量、Mamba可用/可驱逐量和当时
输出预留比率。在列明的7份artifact中，目标键搜索无匹配；这是有边界的
来源核查，不是宣布所有别名或所有文件都没有容量信息。
因此不继续用聚合ratio生成“64可准入”的模拟，也不拿后来首次服务的cache
命中或前缀保护状态回填更早的决策点。改变并发还可能改变缓存及内核工作，
静态trace不是这个反事实的完整性能执行器。

**未来测量可用现有接口，不新增高频控制。**

冻结引擎已导出kv_available_tokens、kv_evictable_tokens、
mamba_available_tokens、mamba_evictable_tokens及new_token_ratio。
未来可从同一次metrics响应保留它们；这里Mamba单位是状态槽，不能加到FULL
token数。它们仍可能是各报告持有的值，不是原子准入快照，足够做总体筛查，
不足以单独证明具体候选可放行。本轮不改运行中或冻结的采集器，也未启GPU。

结论是48配置确实构成一个约束，但没有证明它是唯一瓶颈或64/96在同预算可行。
原生更高并发的资格仍开放，容量分析在当前记录的可识别边界处收敛；不再通过
相同聚合指标的更多统计伪造缺失状态。该项是研究测量与正确性约束，不作创新。

- `experiments/reports/v1477_native_capacity_metric_scope_20261017.json`

### 4.75 补齐最新完整V79b的官方质量：CPU评分已启动，尚无完整结果

V79b保留156 completed、145项原生JCT合格和11项guard介入，原始收集exit1、
缺semantic completion及全部标签未改。此前尚无该轮官方评分；这使有效
完成吞吐和质量匹配比较都未验收。本轮独立补齐CPU评分，不再启动模型/GPU。

V1478用既有exporter导出全部156个原始patch，未按JCT资格筛选。V1479的
35项相关CPU检查通过，覆盖导出身份、完整分母、原始失败/资格标签和官方
报告连接。新薄wrapper复用已固定的官方harness及CPU core，V1480登记源码、
计划、workload、原始summary/manifest和导出hash，V1481记录环境及缓存镜像。
未修改既有评分器的measurement_complete规则。

2026-10-17 01:40:05 PDT启动监督3725471/start_ticks639666857，实际worker
3725549/start_ticks639667652。状态running_official_cpu_grading，首个任务
django__django-10097已实际进入测试容器；容器HostConfig核验1CPU、8GiB、
cpu_shares128、runtime=runc、DeviceRequests=[]、Privileged=false、
无Devices。worker及容器均不请求GPU，没有拉取新的镜像或修改源patch。
这些是启动/资源凭据，不是任务通过或全轮质量结论。

评分单worker逐任务进行，沿用每task1800秒的官方CPU评测超时，与agent工具
180秒上限分开。本轮没有恢复旧的每日/历史截止规则，也不创建GPU接续。
后续须检查真实worker/容器及逐任务报告，完成后用原scorer核验全部156项
partition，再与已有V75 native官方结果比较。即使patch通过，也不改写其
原始guard/终态资格；完整官方评分不自动使measurement_complete=true。
研究主机制、性能和质量匹配目标均仍未完成。

- `scripts/run_v79b_official_quality_cpu.py`
- `experiments/reports/v1478_v79b_quality_inputs_20261017`
- `experiments/reports/v1479_v79b_quality_cpu_checks_20261017.xml`
- `experiments/reports/v1480_v79b_quality_registration_20261017.json`
- `experiments/reports/v1481_v79b_quality_environment_20261017.json`
- `experiments/reports/v1482_v79b_quality_supervisor_20261017.json`
- `/home/longhao/experiment/ops/quality_eval/quality_v79b_cpu_20261017.state.json`

### 4.76 OSDI选题边界：原生重叠有缺口，不等于存在新的论文机制

本轮继续用户要求的研究论证，不启动GPU、不移植重叠策略、不改变workload。
V1483核查V79b实际引擎及历史native的启动参数，保存9份源码hash和定位。
冻结引擎HEAD与登记源码不同：已有staged补丁构成登记tree
53020ce3efef3ed362e9af0db77147b39070f064；与该tree的工作区diff为空。
本轮只读，不能由上游HEAD或staged状态误判实验源码被改动。

**需要分别判断三种重叠。**

| 对象 | 冻结源码与真实启动 | 本轮能得出的结论 |
| --- | --- | --- |
| 原生overlap schedule | 两侧disable_overlap_schedule=false | 调度/forward路径开启，不代表任意GPU算子都重叠 |
| 模型内部双流 | Qwen路径包含Q/K norm、GDN投影、shared/routed expert的条件双流路径 | 已有局部能力；缺逐点激活及kernel计时，不断言本轮全部运行或有多大收益 |
| 通用SBO/TBO | 两侧显式关闭；Qwen3.5层循环未调用model_forward_maybe_tbo，通用strategy没有该混合层case | 当前未获得这套跨层计划，不能据flag存在认定已有完整支持 |

SBO还包含dispatch/combine与down-GEMM的后端专用hook，不能由名称将其等同于
通用attention/expert分批。TBO在moe_a2a_backend=none时要求DP attention，
而本轮enable_dp_attention=false；直接只打开flag会被配置验证拒绝。
parallel_hook里的另一处TBO assert仅属于DWDP分支，不是对全部TP1/Qwen的
普遍禁止。这些是本地固定版本的能力边界，不是对所有上游版本的判断。

**这为什么还不能成为主创新。**

NanoFlow[R27]已有按操作拆分nano-batch及设备重叠，并非只研究离线；MoE-Gen
[R50]已有attention/expert模块分批。当前源码尚未移植某能力，只证明有一个
工程适配对象，不能证明已有抽象在动态agent场景中存在未解决问题。
同样，原生已有局部双流也不能证明所有模块执行方向均无空间。

4.73的固定48请求frontier限制继续成立：把相同集合拆开不产生额外token。
4.74的容量未知继续保留：不能用legacy resident_pressure认定更多请求能放下。
因此本轮没有把“在线MoE+动态agent+流水线”选为主机制，也没有注册GPU微基准。

按用户的OSDI目标，下一份具体提案必须给出一个自然执行反例，明确强已有方法
及其合理适配仍必须支付的计算、数据访问或状态维护代价，再说明新机制如何改变
这一代价及其正确性边界。贡献不能只由相对旧native的提升、额外几个控制器、
当前模型缺少一段移植或人为增加无用工作构成。纯Attention上的适用范围也须明确；
模型专用加速可以作为工程对照，不能自动证明动态agent系统贡献。

当前更值得继续分析的成本对象仍是生成执行；它是研究优先级，不是已证明的
冗余工作或选定的专家调度方案。真实路由、kernel瓶颈和自然workflow提供的额外
可用信息尚无证据，不以新名字填补这些空白。主机制未选、整体目标继续；
已有V79b官方CPU质量作业保持原worker进行，不因本轮研究改动而重启。

- `experiments/reports/v1483_native_overlap_capability_scope_20261017.json`

### 4.77 补齐显存共享强对照：真实GPU工具存在，普通弹性池仍不足以成为主线

本轮以CPU文献和固定源码补齐4.69–4.70候选的比较边界，不复活原先撤下的
三机制，不运行GPU、训练脚本或新benchmark。V1484固定三个作者仓库commit，
保存12份原始文件及一份PDF文本，共1268105字节/hash，未下载模型或数据集。

**此前缺少的低层能力已有更完整的原始资料。**

作者GVM仓库中的`assets/GVM_paper.pdf`已成功读取，正文实际题名为
GVM: OS-Level GPU Virtualization for Mixing Interactive and Batch Workloads
[R52]。它与此前作者页面上的Ghost标题不同；尚未核实版本对应关系，不能将
这份PDF冒称为此前无法访问的Ghost全文或推断会议录用。

本轮核对GVM的§3–7：它不只暴露monitor API，还包含驱动内物理占用限额、
UVM分配与透明换页、主动后台回收和双向传输重叠，以及compute priority/
timeslice/抢占。§7.3–7.4还评估动态调整资源限额的policy。因此不能以
“已有方法只能静态分区”或“缺少跨进程回收”论证新颖性。本文与当前模型、
硬件和agent负载未做性能复现，不沿用其加速数字。

新核对的JointServe v1[R51]直接研究agentic inference中模型参数与KV的
联合HBM管理：工具意图/复用信号、保留集合frontier及SGLang的plan、
copy readiness与KV迁移hook均已有。它与任意用户训练进程的范围不同，
但这种范围差异本身仍不是新机制；
还须比较GVM、Prism/kvcached及显式工具边界配额/park等合理组合。
原有“调用者和工具轮流用GPU”的描述继续不足以选为OSDI主线。
初稿误记的扩展题名和typed residency术语已按保存的v1原文更正；该机制
不能归给此论文，后续比较使用实际joint residency、依赖及执行接口。

**真实应用来源比此前PARTNR控制流证据更明确，但仍不等于有规模收益。**

| 固定作者源码 | 确认可知的事实 | 保持未知 |
| --- | --- | --- |
| MLAgentBench [R53] environment/execute_script | 将device传给工具，并以CUDA_VISIBLE_DEVICES启动脚本、等待完成 | 主LLM是否同卡、实际争用和时间/容量收益 |
| MLAgentBench CIFAR-10 baseline | 选择CUDA、model/data上设备、backward与SGD路径 | 不同agent改写后的实际模型/峰值 |
| SWE-Serve [R54] GPU环境和一个task | 任务与compose均声明1张GPU，agent环境具GPU能力 | 在线agent是否实际执行GPU工具及同卡部署 |
| SWE-Serve所查task verifier | environment_mode=separate，独立运行pytest/score | 不能把独立终评GPU活动算为在线LLM/工具重叠 |

尤其不能由MLAgentBench名字或CUDA调用推出大块显存需求。AST只读解析所查
CIFAR-10网络的5个声明层，参数为456+2416+48120+10164+850=62006，
若FP32仅248024字节。这不包括activation、gradient、momentum、workspace或
allocator预留，不是总驻留/峰值；它既不能证明总量很小，也不能支持大规模
交接收益。SWE-Serve README声明41个GPU任务也不是本地运行或全roster资格。

本轮将“GPU工具有现实依据”与“新机制成立”分开：前者获得更直接的固定源码，
后者仍须识别同设备的自然成本、简单弹性控制之后的剩余损失、具体正确性条件
和端到端收益空间。若只需在工具边界调用既有API，按工程组合对照处理。
当前没有注册新workload、控制器或GPU任务，主机制继续未选。
这也不将通用显存共享再次改名为原先的父子KV容量交接。

既有V79b官方CPU质量作业保持同一监督3725471/worker3725549；
2026-10-17 02:04 PDT按PID/start_ticks核验均live，实际评分推进至django-12050，
02:05进度记录为django-12193。这是运行中观察，不是完整质量结论；
不重写原guard/JCT资格，也不因此重启评分或GPU。

- `experiments/reports/v1484_gpu_tool_source_scope_20261017.json`
- `experiments/reports/v1484_gpu_tool_sources_20261017`

### 4.78 最新续接计算审计：补齐旧前缀仍不能省掉本批请求的输入处理

4.77补齐显存共享强对照后，本轮回到最新V79b，核对普通续接重复输入是否
提供足以改变系统执行方式的成本对象。没有启动GPU或修改harness/engine。

**先复用既有真实batch审计。** V1485使用未改的audit_native_hybrid_reuse，
关联40718个原生请求，全部有client身份，实际41046个prefill batch和44331657
个extend token与独立V1458完全一致。FULL匹配超过实际复用边界至少512 token
的请求为839个，其中首次412、压缩后387、普通续接40；这是特定匹配边界差额，
不能当作全部cache miss或可消除的混合状态重算。

**不能仅由少量token推出少量或大量时间。** 新只读审计器
`audit_continuation_input_partition.py`针对原生请求和同session输入探针，
用唯一request及workflow/context/epoch连接，令P为当前输入长度、H为本轮
device+Host缓存命中、C为与已完成前次输入的精确公共前缀。按计数恒等式拆分：

```
旧公共前缀缺口 M = max(0, C - H)
补齐这类缺口后仍未覆盖的输入 R = P - max(C, H)
当前未命中输入 P - H = M + R
```

H包含Host命中，不代表全部数据已经在HBM或没有恢复等待。C只覆盖前次输入，
不覆盖所有生成输出；R也不自动等于从未生成过的语义内容。该拆分只回答
“修复所观测旧输入前缀缺口，是否足以覆盖本次全部输入”，不是执行模拟或
oracle性能上界；完美覆盖不保证checkpoint可行，R=0也不证明logits可复用。

V1489按V1485记录的字节边界/hash读取两份原始源，不混入后续append：

| 最新V79b计数 | 同session有探针的观察 |
| --- | ---: |
| 原生请求/有探针/无探针 | 40718 / 38840 / 1878 |
| 逻辑公共前缀C累计 | 804990802 token |
| 缺口M累计；P50/P90/P99 | 1258367；32/57/63 token |
| 当前未命中P−H累计 | 21777902 token |
| 补齐这类前缀后R累计；P50/P90 | 20519535；327/1291 token |
| 有缺口且R>0 | 38065次 |
| 无缺口且R>0 | 775次 |
| R=0 | 0次；R最小为3 token |

缺口占已观测C的0.1563%，占同一有探针子集当前未命中输入的5.7782%；
这些均为token计数比例，不是计算、时间或JCT比例。1878个无探针请求保持
unknown，没有补成零丢失。所有38840次即使将H提升到max(H,C)，仍有正的
未覆盖输入。因此仅修这类旧输入缺口，不能直接删掉这些请求的prefill；
是否更好凑批或改善kernel执行是另一问题，需要强已有方法及真实成本证据。
也不能由该结果否定所有跨agent计算共享或所有生成输出复用。

20项CPU检查通过，覆盖Host命中、cache超出前次输入、未知探针、错误身份/
计数、字节封存、缺文件拒绝及R=0仍不宣称免forward。早期V1487结果与最终
V1489分解完全相等；V1487的原审计器快照另存，V1489绑定新增缺文件检查后的
当前源码。原始run、注册运行helper和评分器未改。

**补齐Agentix正文，收紧JCT与控制成本比较。** V1488保存USENIX prepub PDF/
派生文本及hash，本轮核对§3.2、§4.1–4.3、§5和附录A.1。Agentix已经面向
事先未知的动态agent DAG，以PLAS/ATLAS使用程序级、观测关键路径attained
service，并包括抢占、程序级防饥饿、多步调度和聚合连续KV交换。它还明确观察
程序内较高前缀复用，不能把当前高命中当作其未考虑的现象。
这些能力应作为强对照，不把动态graph、少做逐decode控制或普通bulk swap
重新命名为主贡献。其附录将下一步预测/推测列为后续机会，不能据此认定已实现
全部预测，也不能据“future work”直接宣称任意新预测机制有新颖性。

本轮改变的是选题投入顺序：普通旧输入前缀修复进一步降优先级，当前仍未建立
新的可消除生成计算或强对照之外的执行机制。不恢复旧候选或启动GPU；
V79b官方CPU评分保持原worker继续，完整质量与最终研究目标尚未完成。

- `experiments/reports/v1485_v79b_native_input_reuse_20261017.json`
- `experiments/reports/v1486_continuation_input_partition_cpu_20261017.xml`
- `experiments/reports/v1487_v79b_continuation_input_partition_20261017.json`
- `experiments/reports/v1487_continuation_input_partition_auditor_20261017.py`
- `experiments/reports/v1488_agentix_execution_scope_20261017.json`
- `experiments/reports/v1489_v79b_continuation_input_partition_20261017.json`

### 4.79 短prefill的执行机会：原生已有混批路径，需先作为强工程对照

4.78确认补齐旧输入前缀不足以免去本批续接prefill。本轮进一步问：是否存在
较多短prefill插在持续decode之间，能够通过合批减少独立执行？仅核对CPU
源码/trace及原始论文，不启用混批、延迟凑批或GPU，不改PrefillAdder配置。

**冻结引擎已有能力。** V1492绑定实际登记tree与5份源码hash，工作区相对该
tree的diff为空。Scheduler已有enable_mixed_chunk控制的路径，将新extend
batch与running decode合并；mix_with_running把普通decode表示为长度1的
extend。当前两侧真实启动mixed=false、chunk4096、running48、无推测。
因此不能把“支持短prefill+decode混合”本身作为原生缺失或新贡献。

源码路径存在还不等于本配置已经有正确、高效的混批实现。它检查logprob、
input_embeds、beam及running状态；本轮trace不能补齐所有准入条件。
HybridLinearAttnBackend将非DECODE路径转入forward_extend，GDN也因此使用
extend路径，不能假定保留全部专用decode优化。noncontiguous state pool的
gather/scatter只按条件存在，不把它冒充当前已测开销。需要在之后获准的GPU
阶段资格验证，当前不静默切flag或变更物理池。

**最新完整trace的机会规模。** 新只读audit_prefill_decode_interleaving按已封存
runtime_audit读取，每批验证完整request集合、原生extend geometry及服务序号；
序号缺口打断相邻关系，重复/乱序拒绝，原decode cohort变化单列。
12项CPU检查通过，覆盖请求集合保留、上限、缺失记录及零extend等边界。

| V79b实际观察 | 数量 |
| --- | ---: |
| prefill / decode batch | 41046 / 367661 |
| 实际prefill extend token | 44331657，与V1485一致 |
| 单请求、正extend且不超过512 token的prefill | 19828 |
| decode→上述短prefill→decode | 18177 |
| 原decode请求全在后一个decode继续，且合并请求数不超48 | 12633 |
| 其中47 decode→1 prefill→48 decode | 6842 |
| 上述12633个中间prefill的extend P50/P90 | 220 / 441 token |
| 对应worker区间P50/P90 | 7.309 / 16.187 ms |
| 对应worker区间累计 | 111.707秒 |

12633个片段中的新prefill请求也全部进入后一个decode。另5544个相邻片段的
原decode集合没有完整保留，不能合入同一观察。512只是透明的描述分桶边界，
不是新增运行门槛。Decode的logical token字段统计本轮无推测decode的输入行，
不是唯一新生成token数。

这些数据证明有一批值得检验的执行组合，不证明当时所有请求ready、数据已恢复、
物理容量可放下或后端正确。111.707秒是完整worker区间的合计，不能当作空闲、
可消除CUDA时间、JCT节省或oracle收益上界；合批仍要完成原输入工作，也可能
增加decode耗时。当前trace缺expert路由，不能声称已经减少了多少权重读取。

**已有机制比较。** Orca[R55] §3的selective batching已将请求相关attention与
可合并的token-wise操作分开；Sarathi-Serve[R56] §4已用chunked prefills与
混合batch利用decode执行中的余量，并讨论token budget和chunk开销。
因此“把attention分别做、再合并MLP/MoE”或给混批加普通预算仍是强已有方法/
合理适配，不能只加agent标签就称新机制。两篇论文的吞吐数字均未移用。

本轮决定是将本地兼容混批及上述合理适配保留为必要强工程对照；没有选择它作
OSDI主线，没有实现新控制器或注册GPU。主研究仍需指出这些机制之后剩下的
自然代价及动态agent额外可用的信息。V79b官方CPU评分继续同一worker，
质量与性能匹配目标尚未完成。

- `experiments/reports/v1490_prefill_decode_interleaving_cpu_20261017.xml`
- `experiments/reports/v1491_v79b_prefill_decode_interleaving_20261017.json`
- `experiments/reports/v1492_mixed_execution_capability_scope_20261017.json`

### 4.80 替代恢复表示：HCache已有核心机制，当前完整hidden比FULL KV更大

本轮以CPU metadata检验另一个系统问题：能否用更便宜的中间状态代替KV备份，
恢复时只补少量计算，降低迁移与容量成本？这不是RETURN预测，也不恢复旧三
机制。没有捕获hidden tensor、读取权重payload、导入ML库或启动GPU。

**直接相关机制已有。** HCache[R57]保存逐层中间hidden states，恢复时投影出
KV，从而避开完整的attention/FFN前缀计算。§4还包含传输与重建流水线、按层/
token组合hidden/KV/重算路线，以及生产与恢复顺序不同的存储布局和两阶段保存。
因此“中间激活恢复”“混合恢复”或“让重建与迁移重叠”本身不能选为新主机制。
其§3的基本尺寸分析针对MHA；§7已讨论MQA/GQA及低秩表示，不能把GQA问题
本身称为新发现，也不能由该讨论直接认定当前固定checkpoint已有高效实现。

**先核对当前模型尺寸，避免套用MHA结论。** V1493读取本地固定config及V79b
原生池计量，用实际head_dim=256，而不是hidden_size/num_attention_heads=128。
在hidden和KV都用BF16、TP1、相同FULL层和token集合的条件下：

| 项目 | 每FULL层、每token | 全部10个FULL层、每token |
| --- | ---: | ---: |
| 原生K+V：2×2 KV heads×256 head_dim×2 bytes | 2048 bytes | 20480 bytes |
| 完整hidden：2048 hidden_size×2 bytes | 4096 bytes | 40960 bytes |

前者与实际native FULL池的20480 bytes_per_unit完全一致。后者只是待存表示的
尺寸计算，未测hidden运行时dtype或分配；其值为KV的2倍。在同精度下，完整
hidden更小的条件是hidden_size < 2×num_key_value_heads×head_dim，本模型
不满足。不要由head数量比例替代明确的维度与实际池计量。

V79b记录的FULL H2D为29851444 token单位、611357573120字节。若对相同token
和10个FULL层逐一改传完整BF16 hidden，条件体积为1222715146240字节，
尚未计重建和生产时snapshot。这不是已发生迁移、时间预测或所有格式的收益
上界；Mamba/linear state、不同层子集、量化和其他压缩均未混入比较。
已有trace也没有hidden值，不能补造压缩率、矩阵秩或精确重建结果。

本轮降低“直接用完整hidden替换当前FULL KV”的实现优先级。它没有同精度
尺寸优势，还需要额外计算/保存；不为此移植HCache或开GPU。这并不证明部分层
重算、其他表示或整个恢复方向均无空间。后续若提出不同方案，须给出其实际
表示、正确性、成本和超出HCache等强对照的具体差异，不能仅换一个格式名字。
当前新主机制继续未选，V79b官方CPU评分仍用原worker推进。

- `experiments/reports/v1493_recovery_representation_scope_20261017.json`
- `experiments/reports/v1493_hcache_eurosys25_20261017.pdf`

### 4.81 回到应用差异：动态分叉后，究竟新增了什么系统问题

历史范围说明：本节记录当时确认的三条问题线；4.95按用户最新指令撤下第三条。
下文共享环境取证仅作为历史，不能继续据此安排研究或实验。

用户最新指导是从当前动态subagent并发workflow的实际问题找idea，不继续从
通用LLM优化清单挑选机制再套入应用。HCache表示筛选到此收束；本轮不移植
新kernel、不改变harness/到达流、不启动GPU，也不恢复三个已撤下的机制。
下列是问题筛选与新CPU证据，不是已选定的论文设计。

**用户确认的后续研究范围。** 下列三个问题都继续探索。用户提出的按agent
紧急程度利用等待余量、分块与混批的想法保留为候选机制，与三条问题线建立
联系；它不替代其中任何一条，也不将研究范围收窄为调度。

| 问题线 | 下一步要取得的证据 | 如何解释结果 |
| --- | --- | --- |
| 分支进度失衡中有多少由系统造成 | 对同一JOIN的成员对齐实际请求/工具序列、输入输出工作量、可观测排队与服务边界、上下文重建和恢复记录；先区分任务本来更长与系统额外代价 | 固有任务差异不算系统损失；若只改变完成顺序，要同时报告其他workflow及长尾代价，不能直接称吞吐提升 |
| 动态分叉是否形成跨资源反馈 | 关联SPAWN后的模型请求、工具开始/结束和后继请求提交，检查相关突发、积压及关键分支受影响的具体区间；缺少CPU或服务计量时保留未知 | 时序相关只证明候选链条，不能认定工具竞争或GPU调度造成了下游拥塞；小规模现象不强行扩成主线 |
| 并发执行是否增加实际工作量 | 沿真实工具输入输出、共享环境版本、读写依赖及后续验证使用，检查失效验证、重复调查或重复推理的自然案例 | 相同命令、文本相似、版本改变或轨迹不同都不是可安全消除工作的充分条件；需要正确性依据与实际成本 |

优先顺序以现有证据为依据：先深化第一条，第二条沿同一批事件检查，
第三条先确认实际共享与冲突条件。三条都保留，后续证据可以改变投入顺序。
每条方向的阶段产物包含自然案例、成本分解、最接近已有方法及其合理组合、
仍未覆盖的具体代价和可证伪解释。它们是研究判断依据，不是新增agent guard
或实验运行门槛，也不预先承诺三个方向都能形成论文机制。

等待余量与混批候选优先检验第一、二条：能否从当时已知的依赖、服务进度和
应用约束识别可延迟分支，再选择更有效的执行组合。事后sibling返回时间不能
直接作为在线余量；还需核算关键请求额外延迟、分块/控制成本、KV驻留和公平性。
普通混批、程序级优先调度及二者组合是必要对照。若第三条发现了可避免的
额外工作，允许机制落在执行、缓存或工具状态管理中，不强行套入混批方案。

**新的观察单位是同一JOIN的真实分支。** V1494/V1495新增只读
`scripts/audit_dynamic_workflow_phases.py`，按join_create成员、join_wait、
各成员RETURN/CANCEL和join_satisfied关联，不把其他仍活跃child算进该JOIN。
最后一个成员及其内部summary请求参与区间划分；模型、工具分别取区间并集，
重叠单列。6项CPU检查覆盖成员作用域、summary归属、重叠/裁剪、已返回成员、
缺失终态和并列最后返回。

| 完整V79b的原始观察 | 结果 |
| --- | ---: |
| workflow / 普通及内部模型请求 | 156 / 40718 |
| 动态child / 嵌套深度 | 376 / 全部深度1 |
| JOIN / 真实成员数 | 201 / 175组为2、26组为1 |
| JOIN等待P50/P90 | 635.859 / 1975.931秒 |
| 双成员JOIN中，只有最后一个成员未返回的时段P50/P90 | 312.608 / 1150.751秒 |
| 该时段占各组JOIN等待比例P50/P90 | 50.819% / 84.898% |
| 该时段内最后成员新提交的普通模型请求P50/P90 | 29 / 114次 |
| 该时段内最后成员新提交的内部summary请求P50 | 1次 |
| 所有该时段合计的模型客户端区间占比 | 约95.915% |
| 工具区间 / 其他及未观测占比 | 约3.353% / 0.732% |

模型区间包括排队、真实服务和交付，95.915%绝非GPU独占时间或可省比例。
summary的约6.882%是模型区间的子集，不能再相加。区间和跨workflow重叠，
312秒也不是某个调度器能直接拿走的JCT。最后成员身份依赖未来RETURN，
这是离线诊断，不是假设在线能准确预测它。全部原始guard/质量标签保留。

这份证据改变研究优先级：当前许多JOIN的失衡持续几十轮模型/工具交替，
不是只剩一段报告，也不是工具服务区间占主导。优先区分固有分支工作量、
模型排队/执行及并发引入的额外工作，不能继续仅围绕最后数百毫秒的传输窗口。
当前仍不能由此认定工具CPU竞争导致主要长尾。

**应比较三个应用模型，而不只比较请求数量。**

| 应用差异 | 值得查证的问题 | 不能直接当作新意的已有能力 |
| --- | --- | --- |
| 相对单agent，运行中的程序会创建多个独立续接流；相对固定DAG，后续分叉和成本未知 | root到达相同，分叉后的实际模型/工具需求是否发生相关突发和跨资源反馈；是否产生可避免的额外工作 | Agentix[R25]已支持未知动态DAG，不能把动态本身当发现 |
| JOIN把分支完成时间通过屏障耦合，而各分支会在模型、工具及summary之间转换 | 最后成员的慢来自任务本来更多、引擎等待、上下文重建还是其他workflow干扰；什么系统信息能提前识别可减少的部分 | Agentix的ATLAS及SAGA[R12]已有程序/工作流层调度，不能仅给最后child提优先级 |
| 同一workflow的多个分支可能观察、使用或修改共享环境 | 不同交错是否改变工具结果、引出失效验证/重复推理，并使总工作量反过来影响系统；先确认实际冲突与依赖 | AgentFork[R30]、DeltaBox[R31]、Atomix[R32]为强对照；仅联合sandbox与KV或增加事务接口不足 |

第三行目前是研究假设，不是已发现的数据竞争或确定收益。冻结V79b采用
native_in_graph_2to4；实现允许非只读profile的child使用工具/patch路径，
但能力存在不证明本轮有实际冲突。必须用真实读写、结果版本和后续使用证明，
不能只凭相同工具参数、时间相关或生成文本相似认定额外工作可消除。
同样，当前376个child全部深度1，不能以嵌套、回溯或多消费者问题解释已有数据。

**当前最具体的研究问题。** 动态生成的并行分支是否在共享推理/工具资源下，
产生了能由系统减少的进度失衡或额外工作，而这些代价不能由现有程序调度与
普通缓存解释？重点不是“动态agent更复杂”，而是找出一个旧执行/资源抽象
导致实际多做计算、重复验证、搬运或阻塞的自然案例。

下一步先沿同一完整V79b的175个双成员JOIN追踪：将模型请求的排队/服务
边界、实际输入/输出工作量、summary和工具依赖对齐；缺少首服/服务边界的
请求保留未知，不按token数分配batch GPU时间。再区分同一问题在单agent、
固定并行和动态分叉下的来源，核对Agentix、SAGA、CONCUR及Murakkab的合理
实现/组合。Murakkab[R26]已有跨层编排，不能只由“跨资源”声称差异。
只有发现有规模且强对照仍处理不好的具体代价，才选择系统机制或改变workload。

若长分支主要是任务固有的更多工作，单纯改变顺序只能重新分配JCT，不能
自动增加吞吐；若只剩原生可覆盖的排队/缓存策略，应作为工程对照收尾。
新workload可以引入自然嵌套或协作需求，但要有实际任务依据，不能通过强制
无用spawn、冲突或低效工具制造新颖性。当前没有恢复分支准入/完整状态预算/
父子容量交接为默认方案，也没有启动新的GPU队列。

- `experiments/reports/v1494_dynamic_workflow_phases_cpu_20261017.xml`
- `experiments/reports/v1495_v79b_dynamic_workflow_phases_20261017.json`

### 4.82 V79b官方CPU质量评分完整结束

原监督/worker于2026-10-17 03:03:59 PDT自然结束，未重启评分或GPU。
V1496独立调用冻结的原score_run，要求与原监督生成的summary全字段一致；
154份非空任务报告全部为匹配的singleton instance及boolean resolved，
无多余报告。全156任务严格分为96 resolved、58 unresolved、2 empty patch，
没有evaluator error/incomplete。

其中94项同时满足原始correctly_completed/JCT资格。原始156 completed、
145项JCT资格、11项guard-assisted、其他semantic completion标签及collection
exit1全部保留。原scorer的measurement_complete仍为false，有效完成吞吐
仍为null；评分结束不将本轮变成完整质量匹配性能实验，也不与其他轨迹直接
作因果加速比较。旧节“评分继续”保留为当时截面，以本节及顶部为当前状态。

- `experiments/reports/v1478_v79b_quality_inputs_20261017/official_quality_summary.json`
- `experiments/reports/v1496_v79b_official_quality_final_audit_20261017.json`

### 4.83 分支服务与共享状态：先把等待、观测工作和并发写入分开

按用户确认的三条问题线继续CPU研究，没有更改harness、运行时、模型、
workload或GPU安排。新增两个只读审计器：
`scripts/audit_join_branch_service.py`与
`scripts/audit_join_workspace_overlap.py`。分别7项与2项CPU用例通过。
所有源都验证既有V1495/V1458封存前缀的字节数/hash，服务记录复现
41046个prefill、367661个decode，无序号缺口。

**第一条：模型客户端区间中确有大块等待，但不是全部。** 选择175个双成员
JOIN的成员及其runtime-internal summary，共25848个模型请求；用真实身份
连接原生入队、完成与批次worker区间。只将完整位于阶段内的请求划入
both_members_live或last_member_only，跨边界的161个请求单列，避免把后半段
身份回填到请求开始。客户端和server时间戳不直接相减。

原生入队wall时间用首次worker的wall/monotonic对映射到server monotonic；
本批请求的wall/monotonic duration差最大绝对值为0.007004ms。
首次服务前等待包括准入、恢复和控制工作，worker区间也包含依赖等待；
批间空档未被识别为可避免等待。各阶段分位数不能相加。

| 每请求观察 | 两成员均未返回 | 只有最后成员未返回 |
| --- | ---: | ---: |
| 完整请求数 | 16851 | 8836 |
| 客户端请求时长P50 | 6.471秒 | 6.231秒 |
| 原生入队至首worker区间P50 | 2.598秒 | 1.460秒 |
| 参与的worker区间并集P50 | 2.062秒 | 2.140秒 |
| 首末worker之间未覆盖空档P50 | 0.440秒 | 0.456秒 |
| 首次服务前等待占同组累计原生请求时长 | 43.223% | 32.586% |
| worker区间并集占同一分母 | 45.651% | 54.421% |
| worker之间空档占同一分母 | 11.126% | 12.993% |

后一个阶段8836个请求包含8642个普通请求和194个summary。只看普通请求时，
首次worker前等待P50为1.477秒，累计比例34.448%。不能将32.586%当作可减JCT：
请求之间、workflow之间共享资源，准入和恢复有真实约束，worker也非纯CUDA。
两阶段所处的全局负载和请求工作量不同，表格不证明关键分支受到特殊饥饿，
也不证明不存在饥饿。

按175组配对全部观测成员工作，最后返回方/先返回方的中位数比例为：请求
2.034、原生输出token 2.127、实际prefill extend token 2.094。前者含内部摘要，
输出含任何reasoning；这些工作尚未分成固有任务需求与并发引入的额外工作。
因此“慢的原因全是排队”与“全是任务不同”都未成立，下一步应联合分析二者。

165组在单剩阶段有完整请求，另外10组没有，不补零进该条件分布。前165组
的首次服务前等待累计P50为52.314秒，占各自完整单剩时段的比例P50为20.351%；
这些组内的所选客户端请求没有重叠。仍不把52秒当作可以直接省掉的时间，
也不将313秒事后tail当成在线可用预算。

**修正审计的完成边界。** V1498首轮将完成后的尾随descriptor算进last service，
使大量请求被标为时间逆序；该初稿不作为性能证据。冻结native的overlap路径
可能在一个请求已记录完成后，仍输出包含它的已launch批次descriptor。
V1500将worker区间裁剪到原生完成边界，尾随样本另存；25848个请求现全部
闭合，区间之和均等于同请求原生时长。尾随记录不证明完成后仍有有用服务、
GPU浪费或新runtime bug；新增CPU用例覆盖裁剪与尾随保留。

**第三条：共享可变活动确实存在，冲突和额外工作仍待查。** 按JOIN真实
直接成员选择父等待至首个RETURN内完整的工具调用，排除parent、无关child、
summary及跨边界调用。175组共16670次工具调用，其中104组有显式工作区写
工具，28组两个成员都使用过写工具。779次显式写调用包括224次edit_file、
555次write_file；514次观察到前后workspace digest不同，265次相同。
这不要求两个写操作在毫秒级时间上相交，也不证明它们写同一个文件。
execute的隐含副作用没有从工具名或command hash推断。

自然案例django__django-10097的JOIN abe8d7e29fb30f59中，两成员均有显式写
调用，共4次，3次前后digest改变。先/后返回方的普通及内部请求为19/106，
输出token为3177/21203，extend token为9264/86236；该workflow属于原官方
评分中正确且JCT合格的集合。此例证明当前真实任务具有并发成员写共享环境的
条件，不证明工作量差来自冲突。不得由其质量通过推定写入无冲突，或反过来
把106轮自动判成重复工作。

**当前研究决策。** 保留首次服务前等待为第一条方向的成本对象，比较时必须
加入程序级调度/合理混批强对照。第三条从“只有实现能力”推进为“实际活动条件
已观察”，下一步检查文件/结果版本及后继使用，先找真实失效或重复验证。
本轮所用工具事件提供参数hash和全局workspace digest，尚未建立文件级读写
依赖；暂不实现snapshot/事务控制器或声称新颖性。第二条跨资源反馈继续保留，
本轮没有完成其时序因果检验，也不能拿两阶段排队比例变化代替该检验。
GPU继续停止，整体研究及性能/质量目标未验收。

- `experiments/reports/v1499_join_branch_service_cpu_20261017.xml`
- `experiments/reports/v1500_v79b_join_branch_service_20261017.json`
- `experiments/reports/v1501_join_workspace_overlap_cpu_20261017.xml`
- `experiments/reports/v1502_v79b_join_workspace_overlap_20261017.json`
- `experiments/reports/v1503_v79b_join_service_derived_20261017.json`

### 4.84 检验反馈假设，并收紧共享状态方向的已有方法边界

继续使用冻结V79b及既有源hash，没有GPU、runtime/harness或workload修改。
本节推进第二条研究线，并检查第三条能由现有记录识别什么。它不取消三条
用户确认的方向，也不将旧三个机制或新的反馈控制器预先选为主线。

**第二条的完整循环证据。** 新只读
`scripts/audit_tool_return_feedback.py`按同invocation的模型结果、工具组和下一次
模型提交关联。要求实际工具数量/名称与模型结构化结果相符、全部工具闭合，
工具组ready取最后一个tool_end。含task/JOIN的201组、38组工具记录不全或
不匹配等情况明确排除，不把某一个工具提前返回当整组ready。4项CPU通过，
覆盖并行工具、缺失组、invocation作用域及只计算已返回的其他workflow事件。

| 观察 | 完整V79b |
| --- | ---: |
| 可核对的完整模型→工具组→下一请求循环 | 38687 |
| 包含execute的循环 | 16336 |
| 其中下一请求能关联V1500原生计时 | 9366 |
| execute循环的工具组span P50/P90 | 375.289 / 1708.520ms |
| execute循环tool-ready→下一请求提交P50/P90 | 14.604 / 45.362ms |
| 同一间隔P99 | 27223.882ms |
| 下一请求原生入队→首次worker P50 | 2205.806ms |

循环使用已观测顺序及数量/名称检查，没有声称存在额外的原生工具→模型parent
ID。execute可能只是短shell命令，span不是纯CPU service。计时匹配仅覆盖原
双成员JOIN请求集合，不把未匹配请求补成零。工具ready也不等于引擎已经拿到
可执行prompt，下一次提交前仍可能发生上下文处理。

**首先排除“每秒六个返回就是风暴”的解释。** 在下一请求提交时，只统计过去
1000ms内其他workflow的已核对工具组ready事件，中位数为6；这是事件率现象，
本身不是异常突发或资源反馈证明。按600秒阶段保留空计数窗口，另用100ms与
1000ms宽度检查计数方差/均值。新只读
`scripts/audit_tool_ready_dispersion.py`的3项CPU检查覆盖零窗口、部分末窗口
及阶段边界；未将这些窗口设置为runtime常量或门禁。

在已核对循环的观察范围中，1000ms计数的阶段方差/均值为0.688–1.118，
100ms为0.933–1.447；完整计数窗口包含38686个ready，末尾部分窗口1个事件
单列。这不是独立Poisson过程的接受检验，也不能排除更细时标的相关性，
但当前没有支持普遍强烈秒级工具返回风暴。

100ms内有其他workflow工具组ready的匹配请求，全程首次worker前等待P50为
2501.092ms，没有同伴ready的为1775.757ms。但分到相同600秒阶段后，这个
差异多消失或反转：例如1200–1800秒阶段为2827.362/2887.506ms，
1800–2400秒为5452.792/5501.039ms。两组请求尺寸与全局压力仍可能不同，
不把分层当因果证明；它已足以说明全程相关不能用来选定反馈控制器。
计数阶段以首个originating model result为起点，peer分层沿原报告的首个
workflow_start起点；二者起点和覆盖时间均保留，不把编号直接当同一个窗口。
peer集合限于事后核对的完整循环，不声称等同在线控制器的完整事件流。

**长tool-ready→提交间隔主要集中在压缩桥接的请求。** 在9366个有原生关联
的execute循环中，184个下一请求带有已观测first_after_compaction标记：
其间隔P50为29.531秒；其余9182个的P50/P99为13.764/123.942ms。
这184个循环占该间隔累计时长96.563%。标记不把整个间隔归因于summary，
但不能再把它整体叫作客户端闲置或通知交付延迟，也不能把该比例称为收益。
后续若比较紧急程度/混批，需要把内部summary及变换后的首次prefill纳入实际
依赖路径；不恢复旧摘要prompt原型为论文主线。

**第三条的已有方法及可识别性。** V1505保存Atomix v2原始HTML及AgentFork
README固定commit 73f7d537f505489a57ca0820a9a350e2b848931f/hash。
Atomix[R32]已包含有作用域的事务effect、快照版本/读写集合、读—模型计划—写
之间的版本验证、stale工作拒绝/重算及commit frontier；普通事务包装、版本
检查或把长等待移出锁均不能选为新机制。AgentFork[R30]已有sandbox与KV共同
分支身份，其所查README明确单winner、尚无multi-winner merge；这只是一条
能力比较边界，不证明普通合并、Atomix或合理组合处理不了协作分支。
两者均未在本配置复现，不移用其性能或宣称一个开源限制等同整个领域缺口。

现有callback工具事件只保留输入JSON长度/hash及全局workspace digest，
所查HTTP hook不保留完整body，child stream只有截断内容/工具标记，最终
trajectory来自root返回消息。进一步使用原serializer，对全部保留root工具
参数建立精确(name, SHA256)字典：11918个调用、8508个不同键；779个双成员
活跃阶段的child显式写仅有1个匹配。该匹配是保留明文的精确检索，不是hash
猜测，不能据此恢复其他参数、判定环境等价或允许缓存写操作。
因此本轮仍未取得文件级冲突、验证失效或额外推理的证据，不从514个digest
改变直接选择snapshot/事务设计；也不把观测字段不足包装成创新。

**研究决策。** 第二条保留但降低“秒级工具风暴是主要瓶颈”的投入优先级；
继续将全局阶段性压力与真正跨资源反馈区分。第一条后续需把summary/压缩
桥接纳入完整路径，再检验在线可识别的分支余量及执行组合；第三条需具体
文件/结果版本和后继使用案例，以及超出强简单方案的成本。GPU保持停止，
整体主机制与同质量性能目标尚未完成，不将本轮必要条件审计当作目标达成。

**第一条的下一个可识别成本对象。** 循环中另外590个结果未满足正tool_calls
结果条件：552次stop/零工具/零可见正文后，同invocation仍提交下一请求；
另有23次length/零工具/零正文，以及15次length/一工具。V1510保存分类及
实际相邻request ID。零可见正文不等于没有reasoning或原生生成，也不能由
继续请求就判成重试bug、guard或并发引入的工作。下一步先连接实际token/
service成本及分支归属，核算其对观测工作量差的贡献；不改输出上限或终态
路径，若发现共用路径缺陷则匹配放入对照，不作为论文机制。该成本归因现已在
4.85完成，不再列为未执行的下一步。

- `experiments/reports/v1505_shared_state_scope_20261017.json`
- `experiments/reports/v1506_tool_return_feedback_cpu_20261017.xml`
- `experiments/reports/v1507_v79b_tool_return_feedback_1000ms_20261017.json`
- `experiments/reports/v1508_tool_ready_dispersion_cpu_20261017.xml`
- `experiments/reports/v1509_v79b_tool_ready_dispersion_20261017.json`
- `experiments/reports/v1510_v79b_excluded_cycle_observation_20261017.json`

### 4.85 空结果恢复成本：闭合一条解释，回到动态分支本身

完成4.84/V1510提出的成本核算。新只读
`scripts/audit_empty_result_recovery.py`连接同invocation的空结果/后继请求、
原生队列/输出计量、已有分支service记录及sandbox审计。4项CPU用例覆盖
invocation作用域、工具/无效工具排除、唯一marker归属，以及相同semantic
digest不等于相同native输入。156份事件与156份sandbox源均验证原hash。
没有重启GPU、改变模型设置、guard、上限或harness/runtime。

**来源已明确为既有共用恢复路径。** 冻结harness的
EmptyReasoningRecoveryMiddleware在单个AIMessage无可见正文、无有效/无效
工具且finish_reason为stop/length时，对原ModelRequest重试一次，覆盖
extra_body.chat_template_kwargs.enable_thinking=False。全部575个后继对均有
唯一位于前一结果和后一次提交之间的retry marker，575个恢复结果标记均为
true，实际后继也都有可见正文或工具。关联使用同client边界和唯一所有权，
不凭scope名称伪造request ID，不将恢复成功等同任务正确。

| 首轮空可见结果 | stop | length |
| --- | ---: | ---: |
| 请求数 | 552 | 23 |
| root / child | 263 / 289 | 11 / 12 |
| 首轮原生输出token合计 | 47665 | 188416 |
| 首轮原生输出token P50 | 25 | 8192 |
| 占全轮7490631输出token | 0.636% | 2.515% |
| 首轮原生入队→完成P50 | 1.084秒 | 164.418秒 |
| 首轮结果→后继提交P50 | 9.073ms | 5.235ms |

23个length例均达到原8192-token上限，分布在17个workflow；552个stop例分布
在97个workflow。两类共有101个workflow，存在交集。原生输出包含任何reasoning，
没有独立reasoning token测量，不能按空正文认定没有生成，也不将这些token
全部判成无用。队列/worker区间包含共享服务和真实等待，不是可减GPU/JCT。
后继请求仍执行任务工作，不能把两次请求之和直接算成可消除的恢复开销。

**相同digest不是相同前向的自然案例。** 575对prompt_semantic_sha256均相同，
而后继native输入均多2 token。冻结digest字段不包含extra_body和
chat_template_kwargs，恢复路径会覆盖thinking设置。数据与源码共同说明，
不得由该digest相同就推断native token、渲染契约或模型设置相同，也不能据此
合并两次采样。旧观测按原口径保留；精确执行复用仍须实际token/完整契约证据。

**直接计数不能用这条路径解释典型2倍分支差距。** 原生分支service集合能关联
232个首轮：先返回方86、后返回方146；其余343个没有该双成员JOIN service
归属，不补标签。按175组配对，仅从观测中移除这些首轮ID后，请求数、输出、
实际extend的last/first中位数仍为2.027/2.133/2.094。原值约为
2.034/2.127/2.094。这只是描述性重核算，没有执行取消恢复的反事实，也没有
改变后续模型路径、排队或质量。少数length例可能影响个别长尾，仍应分列，
但不能把575次恢复当作典型工作量失衡的主要直接解释。

**下一步研究决策。** 关闭“552次空结果就是并发引入的重复工作”这条未经证实
的归因，保留恢复成本作为共用对照口径，不据此改thinking、输出上限或guard。
第一条回到动态分支本身：按当时已知的JOIN成员/RETURN状态识别本地阻塞路径，
核对请求参与的真实batch和上下文处理，检验用户提出的紧急程度/等待余量混批
候选。只有剩一个未返回成员是在线可观察的结构信息；其他分支能延迟多久仍
未知，不能由并行存在或事后返回差直接赋值。已有程序调度、普通混批及其
合理组合继续作强对照，不提前选定新的论文机制。第二、三条问题线均保留，
整体性能/质量/创新目标仍未验收，GPU继续停止。

- `experiments/reports/v1511_empty_result_recovery_cpu_20261017.xml`
- `experiments/reports/v1512_v79b_empty_result_recovery_20261017.json`
- `experiments/reports/v1513_empty_recovery_interpretation_20261017.json`

### 4.86 提交时JOIN状态与batch：有结构信息，尚无可安全延迟的余量

本轮继续用户确认的三条问题线；紧急程度、分块和插缝混批只作为候选机制。
新只读`scripts/audit_join_submission_batching.py`连接V1495封存的156份client
事件与V1458的原生事件/worker记录，按时间戳及sequence重放JOIN创建、等待、
RETURN/CANCEL。每次llm_submit只使用当时已观测状态；内部summary继承其声明
parent的标签。全量读取核对原始字节/hash，没有修改冻结应用、引擎或workload。

`sole_member`仅表示一个尚未满足、已有waiter的本地JOIN只剩一个未完成成员；
它包括原来只有一个成员的26个JOIN，不等于全局关键路径或更高用户价值。
`parallel_member`仅表示同组仍有多个未完成成员，不意味着存在正的可延迟余量。
这些是client可以附带的信息，没有证明当时原生engine已接收或更新该状态。
batch始终使用提交时标签，不用事后最后返回者身份回填。

| 完整V79b的观察 | 结果 | 解释边界 |
| --- | ---: | --- |
| 已归类/未闭合client模型请求 | 40718 / 0 | 包含root、child及内部summary |
| 提交时root / parallel / sole | 11987 / 17012 / 11719 | sole包含单成员JOIN，不与双成员完整last-only请求数混用 |
| 原生prefill / decode批 | 41046 / 367661 | 复现此前审计总数及44331657个extend token |
| 同批出现sole与parallel提交标签的decode批 | 184635（50.219%） | 证明标签共存，不是优先级倒置或可取消的工作 |
| 保留前后decode集合、请求数不超48的单请求prefill片段 | 20212；其中≤512 token为12633 | 只核对观测请求集合，不证明更早ready、容量或混批兼容 |
| 上述中间prefill提交时为parallel、decode同伴含sole | 7253；其中短prefill为4744 | 标签固定在提交时，不能当作当前在线slack |
| 这两组中间worker区间合计 | 103.814 / 41.970秒 | 共享batch墙钟，不是可省GPU或JCT上界 |
| client请求未收结果期间由parallel变为sole | 161（155 child、6 summary） | 可能仍在排队、服务或交付，不认定GPU正在decode |
| 上一行状态变化→client结果P50/P90 | 4.372 / 23.002秒 | 同client时钟，不与server时间直接相减 |

worker分类使用原生completion同一monotonic时钟，排除已无preterminal区间的
尾随descriptor，但保留原始batch几何。没有unknown请求分类，原有只读
interleaving observer在同一原始流复现12633个短片段；没有用未来batch
反推调度时可用容量。512及48是既有描述口径，不是新runtime常量或延迟策略。
6项CPU用例覆盖未来RETURN不改提交标签、summary继承、无waiter的JOIN、
无关/已满足成员、完整batch区间以及已完成descriptor。

**进一步收紧已有方法边界。** 本轮保存作者原始PDF及hash：

- Niyama: Breaking the Silos of LLM Inference Serving [R58]，作者PDF标明
  arXiv:2503.22562v1。§3.2–3.3把应用TTFT/TBT/TTLT目标映射为deadline，
  用当前decode的下一token余量选择动态prefill chunk并构建mixed batch；
  §3.4另有混合优先级、eager relegation与selective preemption。因此普通
  “不同紧急程度共享engine并插入prefill”已有直接强对照。未发现足够依据将
  这份PDF概括为已实现动态JOIN控制或bi-level concurrency控制。
- Cortex的实际题名为Workflow-Aware Resource Pooling and Scheduling for
  Agentic Serving [R59]，作者PDF标明arXiv:2510.14126v1，共3页。§2描述
  每workflow stage的专属pool、SLO slack/预计服务时间优先级、局部性路由、
  准入和兼容stage借用空闲engine，并展示NL2SQL的KV footprint示例。§3是
  malleability、speculation和agentic state的后续方向。它不是只有愿景、没有
  原型或示例，也不是已测量的单GPU在线动态JOIN/chunk控制器。

以上范围约束新颖性论证；未复现其实现，不能仅因部署或评估对象不同，就断言
它们的合理适配不能解决候选问题。Agentix/SAGA的程序级调度、Niyama及
Orca/Sarathi的执行组合，以及它们的合理组合，都继续作为强对照。

**下一步判断。** 第一条继续拆分等待与实际工作，并检验当时可用的依赖、
服务进度和应用约束能否识别有效延迟预算；不能把parallel直接标成低优先级，
也不能把sole提优先级本身当作创新。161个运行中结构变化提示需要评估信息
更新路径，但不是新控制协议的收益证据。第二条保留模型/工具/summary反馈，
4.84的阶段混杂结果只降低“普遍秒级工具风暴”的优先级。第三条仍需实际
文件/结果版本及后继使用来证明可避免工作，现有779个写事件不能补出缺失依赖。
三条方向都要求自然案例、可量测成本与强对照剩余差异，再选择系统机制。
没有开启mixed、PrefillAdder、延迟凑批或GPU，也没有更改任务语义或恢复行为。

- `experiments/reports/v1514_join_submission_batching_cpu_20261017.xml`
- `experiments/reports/v1515_v79b_join_submission_batching_20261017.json`
- `experiments/reports/v1516_niyama_author_20261017.pdf`
- `experiments/reports/v1516_cortex_author_20261017.pdf`
- `experiments/reports/v1517_join_batching_research_interpretation_20261017.json`

### 4.87 同一JOIN共同服务：优先查反复准入，尚无decode不公平的主要证据

新增只读`scripts/audit_join_branch_coservice.py`，基于V1500封存的25848个成员
请求及相同两份原生source。先按实际JOIN和成员关联同一decode批，再按原生
完成时刻裁掉尾随descriptor，worker区间取并集。anchor请求必须在client双成员
均未返回时完整提交并收回；同批peer可以是跨越首次RETURN的请求，单独计数，
不将其整段工作混进anchor分母。first/last仍是离线标签，不作为在线紧急程度。

4.88进一步核对了计时定义：这里的首次worker是埋点的串行统计边界，不是真实
CUDA开始；“等待首次worker”可能已经dispatch，不能全当仍在waiting_queue。
本节原计数保留，首次dispatch及其与串行起点的区别以4.88为准。

**先比较共同阶段，避免把晚结束必然累积的工作直接解释成原因。**

| 双方都未返回时的完整请求 | 最终先返回成员 | 最终后返回成员 |
| --- | ---: | ---: |
| 模型请求 / 其中summary | 8514 / 96 | 8337 / 94 |
| native输出token | 1220189 | 1150180 |
| 实际extend token | 7906200 | 7931210 |
| 首次worker前等待累计 | 30813.295秒 | 31871.523秒 |
| 每请求平均首次worker前等待 | 3.619秒 | 3.823秒 |
| retained decode记录 | 1211675 | 1141843 |
| 有同JOIN preterminal sibling的记录 | 774941 | 748075 |
| 同批worker暴露 / anchor暴露累计 | 59.797% | 61.851% |
| 上一比例的逐JOIN P50 | 62.681% | 68.407% |

175组按组配对，后/先成员的每请求平均首服等待比P50为1.008，完整请求数比
为1.093、输出比为0.886。另161个boundary请求均属于最终后返回成员，完整
输出共73106 token；其首次RETURN前生成多少没有在本审计中拆分，不能全放入
共同阶段。以上比较受完整请求选择、实际任务和到达时序影响，不能证明固有
工作相等、系统完全公平或没有并发额外工作。它说明4.83的全生命周期约2倍
观测工作量本身不足以解释失衡成因。

**把“没有同批peer”进一步拆开。** 使用V1500已核对的同server队列→结果时长、
首次worker等待及本次原生completion重建登记/首服/完成时间。仅在batch没有
有效peer时查同JOIN另一成员的原生区间，不减client与server时钟。

| anchor没有同批有效sibling的记录 | 先返回成员 | 后返回成员 |
| --- | ---: | ---: |
| 合计 | 436734 | 393768 |
| sibling已登记、尚未获首次worker | 349317（79.984%） | 306577（77.857%） |
| sibling没有已登记且未完成的模型请求 | 87417 | 87191 |
| sibling已获首次worker但没有同批记录 | 0 | 0 |
| 多个重叠原生请求、无法唯一归类 | 0 | 0 |

没有已登记请求并不区分工具、client或context工作；已经登记也不证明页面、
状态、running额度和prefill预算都可准入。后返回成员的306577条等待peer记录
中，原始batch=48为231425条、=47为62604条，合计95.907%；最小为43。
这是保留尾随descriptor的原始batch几何，不是活跃请求数或allocator容量
快照，不能据47推定还能准入一员，也不能据48宣称HBM或GPU完全饱和。
原生状态分桶只划分record计数，不把交叠worker区间当互斥GPU时间。

**交叉核验。** 41046个prefill、367661个decode及零缺失序号与V1500一致；
V1523去掉新增batch尺寸分桶后完全复现V1521聚合。双方输出token减请求数
各自等于anchor decode记录数。先方共同服务记录减26866个boundary-peer记录
等于后方748075条；没有将其他JOIN、原生已完成成员或last-only请求混成共同
服务。旧V1519/V1521结果及其hash一致的源码快照均保留，后续增加观测未覆盖
旧产物。当前17项CPU通过（含原service审计7项，不把各轮重复通过相加）。

**研究决策。** 当前共同阶段不支持“已获服务的慢分支长期被从decode批中挤出”
作为主要解释，下一步优先追查反复首次准入等待，以及更多实际轮次如何积累
等待。这个等待是否可减少、受running额度/容量/恢复/准入策略哪一项限制，仍
缺同次决策证据；不直接扩大上限、改变调度或将排队总量当可省JCT。
Agentix[R25]的§3.1已明确闭环调用到达反馈，§4.2已有程序级等待/优先级及
多步调度；SAGA、Niyama及其合理组合继续作强对照。普通“有闭环”“多轮等待”
或“JOIN重要”不够成为新主张。

三条方向都继续：第一条深化准入/实际工作因果；第二条寻找超出普通闭环的
具体工具/summary反馈；第三条仍需实际版本和读写依赖证明额外工作。当前
没有选定主机制，也没有修改harness、恢复、输出上限、guard、PrefillAdder、
mixed/延迟策略或启动GPU。最终质量、性能和论文差异验收均仍开放。

- `experiments/reports/v1518_join_branch_coservice_cpu_20261017.xml`
- `experiments/reports/v1519_v79b_join_branch_coservice_20261017.json`
- `experiments/reports/v1520_join_branch_coservice_pending_cpu_20261017.xml`
- `experiments/reports/v1521_v79b_join_branch_coservice_pending_20261017.json`
- `experiments/reports/v1522_join_branch_coservice_batch_size_cpu_20261017.xml`
- `experiments/reports/v1523_v79b_join_branch_coservice_batch_size_20261017.json`
- `experiments/reports/v1524_join_branch_coservice_interpretation_20261017.json`

### 4.88 首次dispatch与并发：等待主体仍在发出前，不能把统计区间当队列

新增只读`scripts/audit_native_started_occupancy.py`，读取相同封存原生event/
audit，重建全40718个请求的登记、首次dispatch、首次串行worker、首次decode
及原生完成。原25848个成员请求的时长与V1500逐项复现；新派生表保存全量
native边界，后续CPU时序分析可以复用，不必重复扫描3.58GB原始audit。

**先修正归因边界。** 冻结`v0520_native_telemetry.py`的on_completed使用：

```text
service_start = max(run_batch_entry, previous_sample_completion)
```

它保留了run_batch入口的`launch_monotonic_ms`，但worker区间起点是串行统计
边界。本次408707个批次全部核对这一关系，408536个起点晚于dispatch。
因此登记→首次worker包括登记→首次dispatch和dispatch→串行起点，后一部分
可以与前批处理重叠，不是纯控制开销、GPU idle或尚未准入。4.87的“sibling
等待首次worker”计数仍正确描述原边界，但不能直接改称未dispatch请求。

| 请求组 | 请求数 | 登记→首次dispatch P50/P90 | dispatch→串行起点P50 | 原登记→首次worker P50 |
| --- | ---: | ---: | ---: | ---: |
| 全体 | 40718 | 1.239 / 8.147秒 | 36.246ms | 1.279秒 |
| 双成员均未返回的完整请求 | 16851 | 2.561 / 9.885秒 | 36.329ms | 2.598秒 |
| 单剩成员阶段的完整请求 | 8836 | 1.424 / 7.497秒 | 35.937ms | 1.460秒 |

分位数不相加；逐请求两段相加严格复现原值。全体dispatch→串行起点累计
1564.896请求秒，占原登记→首次worker累计的1.294%。这部分不能从运行时长
或JCT扣除，也不能据此把整个首服前等待当成埋点假象。登记→首次dispatch
累计仍有119383.433请求秒，包含真实排队、准入及dispatch之前的准备工作，
不是已定位函数的可消除成本。

**再按请求生命周期统计同一时刻的并发。** 首次dispatch→原生完成称为
“已dispatch未完成”，另保留串行worker开始和decode开始两种视图；三者
全轮最大值均为48。只在待测请求尚未dispatch的等待段计算下表，因此待测
请求不会把自己计进已dispatch集合。时间按同时等待的请求数加权，非独占
GPU时间、墙钟占比或JCT比例。

| dispatch前累计等待的条件 | 全体请求 | 双成员完整请求 | 单剩成员完整请求 |
| --- | ---: | ---: | ---: |
| 当时已有48个已dispatch未完成请求 | 63.120% | 62.115% | 63.926% |
| 当时已有47–48个已dispatch未完成请求 | 89.454% | 89.092% | 89.861% |

这些生命周期计数比raw batch几何更直接，仍不等于allocator的精确槽占用：
prefill分块可以释放request row，候选可能在dispatch前已取得资源，完成处理
也可能晚于或早于实际资源释放。低于48不证明有可用页/状态/准入预算；达到48
也不证明唯一阻塞原因就是并发设置。1668个请求有多次prefill记录，没有观察到
decode开始后再次prefill；所有请求都有decode记录。完整server.log对冻结
retraction警告字面模式匹配为0，不将这些观测扩大成任意重算/中断均不存在。

**冻结代码与启动条件。** scheduler/policy hash与V1477一致。启动记录为
FCFS、max_running_requests=48、TP1/PP1、mixed=false、priority_scheduling=false，
prefill_delayer=false、min_free_slots_delay=None、prefill_decode_interval=0，
未把主动延迟凑批当成本轮排队原因。get_num_allocatable_reqs同时检查微批预算
和request pool；PrefillAdder还检查FULL预算、前缀保护与必要状态等。没有
逐候选容量快照，仍不能用事后命中或这些生命周期回填当时物理可准入状态。

SGLang官方调优文档[R60]也将max-running与显存/并发调优分别讨论；它不证明
当前混合模型的64/96请求可行。以后获准实机时，合理调优的native应作强对照，
不能只调大本方并发就声称workflow新机制收益。本轮没有登记或启动该实验。

**研究决策。** 第一条继续查dispatch前的服务分配与实际工作量，不能主要围绕
几十毫秒串行统计差额扩控制器；也不能把普通并发调优当OSDI贡献。后续候选
需要说明workflow信息如何在相同资源下改善执行选择，并超过Agentix/SAGA、
动态混批及其合理组合。第二条保留具体工具/summary反馈，第三条保留版本/
读写依赖导致的额外工作，不能用本轮并发计数替代两者的因果证据。

最终18项CPU检查通过，包括已dispatch但尚未进入串行区间的反例；同时覆盖
时钟、尾随descriptor、重入、并发等待加权和边界同刻事件。V1527曾因观察器
缩进错误在收集时失败，已修正；当前证据用V1528，不覆盖或重标该历史产物。
V1526初版及hash一致的源码/测试快照保留。无GPU、harness、模型、guard、
输出上限、恢复、物理池或运行时策略变更，主机制与最终性能/质量尚未验收。

- `experiments/reports/v1525_native_started_occupancy_cpu_20261017.xml`
- `experiments/reports/v1526_v79b_native_started_occupancy_20261017.json`
- `experiments/reports/v1528_native_dispatch_occupancy_cpu_20261017.xml`
- `experiments/reports/v1529_v79b_native_dispatch_occupancy_20261017.json`
- `experiments/reports/v1530_native_dispatch_interpretation_20261017.json`

### 4.89 竞争与职责：FIFO次序成立，先解释child实际做了什么

新增只读`scripts/audit_workflow_dispatch_competition.py`复用V1529的40718个
dispatch生命周期、V1495封存client事件、V1458的156份root trajectory以及
V1500成员工作表。没有重新扫描3.58GB worker audit。按提交当时状态重放
11719个sole、17012个parallel及11987个root标签；内部summary继承声明parent。
标签固定在提交时，不假定原生engine收到，也不作为已知剩余工作/可延迟余量。

**未找到后到请求插队，或少数workflow大量占并发的解释。**

- 检查严格早于目标dispatch、但登记晚于目标的请求，在登记差大于0/1/100ms
  三个描述口径下均为0。相同批次同刻dispatch不人为排序。这是重建的原生
  FIFO dispatch次序，不证明没有HOL等待、公平性问题或新策略收益。
- 单剩成员8836个请求的累计dispatch前等待仍复现25374.615请求秒。其等待
  期间，已dispatch未完成请求来自的workflow数量，按等待请求加权P50为43；
  每个workflow在这些等待区间内最多有2个此类请求。不能把这一条件统计
  直接称为任意时刻的全轮峰值或真实GPU占用。
- 该组等待期间，目标workflow没有已dispatch未完成请求；这是本地JOIN仅
  剩一个成员的预期结构，不单独构成不公平证明。其他workflow的请求对暴露
  按提交标签为parallel45.149%、root29.966%、sole24.885%。请求对暴露还
  乘上同时活跃的其他请求数，不等于GPU资源、等待时间或JCT比例；parallel
  仍不代表可以安全推迟。

因此，后续改顺序的候选需要证明相对FIFO及程序感知强对照的净收益，不能当作
修复已观测的插队bug。当前数据也不支持为“极少数超大fanout workflow独占”
建控制器；自然更深工作负载仍可另行论证，不能反过来假设本轮已经具有该问题。

**恢复实际委派职责，先解释观测工作量。** child创建事件保留agent_definition、
description_sha256和description_chars；本轮对同workflow、同agent_type及
完全相同hash/字符数的root task参数进行精确匹配。350个双成员JOIN child全部
找到原始文本，本轮没有重复调用身份歧义；这一方法本身不承诺在重复相同
描述时能确定唯一tool_call_id。原生请求经声明ancestry关联member，全部
25848请求/4224718输出/26526484 extend与V1500一致。

| 声明角色 | paired child数 | 最后返回数 | 模型请求P50 | 输出token P50 | 实际extend P50 |
| --- | ---: | ---: | ---: | ---: | ---: |
| repository-explorer | 147 | 46 | 43 | 5111 | 27524 |
| test-analyst | 168 | 114 | 62.5 | 9095 | 64281.5 |
| implementation-agent | 16 | 6 | 42 | 6192.5 | 25712 |
| general-purpose | 19 | 9 | 22 | 3406 | 18530 |

这些是实际观测工作，不是固有必要工作量或预测准确率。项目、阶段与任务内容
不同，不能由表中差异推断角色造成慢、系统公平或test-analyst应一律提高优先级。
尤其同一角色内部跨度很大：test-analyst请求数2–426、explorer为3–423。

**一条具体的后续调查线索。** django-10097第二组JOIN的任务文本分别要求
实现URLValidator修复，以及编写并运行验证脚本。实现child有19个模型请求/
3177输出，验证child有106个/21203输出。验证child的105次工具调用中有
74次execute、5次edit_file、5次write_file等；31个error为28个command_failed
和3个write_file tool_error。该workflow仍在原官方resolved/correctly_completed
集合中，原measurement_complete=false不改。

它证明长分支确实执行了更多工具/推理工作，也有具体错误路径值得检查；
error可能是正常失败复现，不自动算浪费。现有计数尚不能判定错误由环境版本、
错误任务假设、工具准备、模型探索或并发交错哪一项造成。任务描述恢复也不
补出完整child命令、读依赖、文件版本及后继使用，不把这条线索直接称为冲突。

**研究决策。** 第一条从纯服务分配继续推进到职责、工具结果和实际工作成本，
把资源等待与任务/探索路径分开。第二条继续具体工具/summary反馈；第三条
利用新恢复的任务目标，寻找实际版本/依赖与额外工作的自然证据。单纯按固定
角色分类、预测平均成本或分池仍须对照Cortex[R59]的阶段隔离及合理适配；
Agentix/SAGA和动态混批的强对照继续，不能把已有结构标签本身当作OSDI贡献。
当前不选择新的优先级或控制器。

16项CPU检查通过：严格登记/dispatch次序、同批时间相等、同/跨workflow区别、
等待与请求对单位、同刻释放、summary继承、精确描述及封存JSON读取均有覆盖。
旧质量/guard/完成标签保留，未改harness、模型、输出上限、恢复、物理池、
runtime或GPU配置，三条方向与最终目标保持开放。

- `experiments/reports/v1531_workflow_dispatch_competition_cpu_20261017.xml`
- `experiments/reports/v1532_v79b_workflow_dispatch_competition_20261017.json`
- `experiments/reports/v1533_workflow_competition_interpretation_20261017.json`

### 4.90 工具正文与错误路径：先纠正摘要身份，再判断重复工作

只读`scripts/audit_tool_content_recovery.py`复用4.89封存的156份client事件、
156份trajectory及V1529原生请求表，未重扫worker audit、运行工具或启动GPU。
本轮先完成内容可识别性核验，避免把回调封装与实际工具正文混为一谈。

**纠正的是内容解释，不是所有旧重复指标。** 冻结应用`_finish_ordinary_tool`
对output调用`_json_stats`，后者使用`json.dumps(..., default=str)`。output为
ToolMessage时，其表示包含tool_call_id；同样正文、不同call ID可产生不同hash。
因此V1458的跨agent相同callback摘要2次/11.362ms不能估计全部相同正文。
输入摘要重复、root的beliefkv_source_result_sha256及其原有边界不受此问题影响；
不能连带否定历史输入重复计数，也不把root source摘要当成callback摘要。

**只用保留的真实正文，逐调用重建验证。** root trajectory的正文先构造raw/
ToolMessage候选，必须同时复现该原调用记录的序列化字符数和完整SHA256才能
成为见证。之后用真实见证正文与待核对call ID重建，仍要求长度与hash完全一致。
普通成功调用仅检索同输入摘要的见证；错误调用另允许同tool、匹配表示长度的
已见证正文。没有逆推hash或补造命令/输出；长短、截断或无法匹配的正文保持未知。
见证可能来自更晚调用或另一workflow，这只是离线恢复观测，不是在线可用信息。

| 覆盖口径 | 数量 |
| --- | ---: |
| 完整工具调用 | 39,574 |
| root保留且有唯一普通callback的结果 | 11,537 |
| root原调用正文直接重建通过 | 10,000 |
| 经见证检索后确认的root正文 | 11,419 |
| child工具调用 / 已确认正文 | 28,037 / 2,319 |
| 全部已确认正文 / 全部调用 | 13,738 / 39,574（34.715%） |
| error调用 / 已确认正文 | 6,060 / 3,172 |

child覆盖仅8.271%，因此不能由未检出推断没有重复或冲突。10000个root直接
通过外，1537个正文未直接通过；其中部分通过其他真实见证确认，仍不能把最终
trajectory中的反馈/截断正文无条件当成callback当时的值。

**相同正文机会确实被旧callback计数低估，但成本仍需正确解释。** 在已确认
子集内，要求同workflow/tool/输入/正文/状态，且同值前调用结束不晚于本次开始：

| 后续调用口径 | 调用数 | workflow数 | 所涉工具墙钟累计 |
| --- | ---: | ---: | ---: |
| 同invocation存在已完成前调用 | 2,916 | 116 | 1,100.010秒 |
| 跨invocation存在已完成前调用 | 911 | 143 | 37.567秒 |

两行可以重叠，不相加。跨invocation主要是644次read_file、128次grep，另有
59次execute、53次ls、25次glob及2次edit_file。另1245个同workflow/tool/
输入/正文组合确有不同callback摘要。上述不是全量内容机会、在线cache hit、
安全去重、可省工具时间或可省JCT；相同结果不提供完整读依赖/环境版本/纯度。

V79b原输入重复为2406次/161.446秒，root观测epoch不变的source正文重复为
1539次/697.981秒，二者仍按V1458原口径保留。§2的307.60秒是历史V73输入
重复统计，不能继续称作最新V79b；新正文恢复也不替换原epoch/source指标。

**错误后确有后继推理，不自动算作浪费或并发放大。** 38687个完整普通工具
循环中6026个含error；按同invocation、完整工具数量/名称及原生request ID，
各自唯一关联一个紧接请求，共输出1071849 token。test-analyst为2171个请求/
374217输出；这些可能用于必要复现、解释失败或继续调查，不是可避免工作上界。
已确认正文有769次提及断言/失败测试、572次import错误、63次已有文件、9次
语法错误，标签仅是词面观察，分类不互斥。902次正文仅有exit code 1，无解释
输出；另96次报告.git/index.lock所在文件系统只读，不能称为并发锁竞争。

4.89的django-10097验证成员31次error中仅3份正文确认，且都无解释输出，
命令参数仍未恢复；其31个紧接请求共4895输出/18187 uncached输入token。
root的9个error可见真实测试失败、语法、settings/import等路径，但不能把root
原因套给未知child，也不能据此证明共享写冲突。原resolved/正确完成及
measurement_complete=false保持。

**三条问题线的推进。** 第一条结合已委派职责、错误/结果后继与dispatch/上下文
处理，解释观测工作而非只重排请求；第二条保留工具→后继/summary的实际依赖，
不由错误token数声称资源拥塞；第三条用已确认内容/参数寻找具体路径、版本、
读写及后续使用，覆盖未知处继续保留未知。当前尚未证明并发使必要工作变多，
也未选择新主机制。相同正文恢复和普通工具缓存本身不是新增论文贡献。

11项CPU检查通过，覆盖call身份与内容区别、严格长度/hash、错误候选边界、
特殊字符call ID、并发重叠不算已完成前调用及错误组的唯一后继归因。
未改harness/恢复/模型/guard/输出上限/workload/物理池/调度或启动GPU。

- `experiments/reports/v1534_tool_content_recovery_cpu_20261017.xml`
- `experiments/reports/v1535_v79b_tool_content_recovery_20261017.json`
- `experiments/reports/v1536_tool_content_interpretation_20261017.json`

### 4.91 同路径创建：已找到跨agent活动，尚无并发冲突成本证明

只读`scripts/audit_file_creation_provenance.py`沿V1535的63次已确认“文件
已存在”错误，检查先前报告成功的write_file。原V1535不改；本轮额外允许
成功write_file匹配其他输入下实际保留的同tool正文，但仍须逐调用严格复现
原序列化长度和SHA256。root见证还须核对原输入digest和file_path。没有按
期望路径拼造成功正文或猜测child命令；成功正文的见证可来自更晚调用，仅
恢复离线观测，不代表当时在线可用。

829次报告成功的write_file中，132/133份保留root成功正文与原调用匹配，
另39份child成功正文可严格恢复，共171份；658份未知。成功正文报告该路径
写入成功，但child未保留的physical_execution不从root见证补出。相同路径
仅按同workflow内规范/workspace字符串匹配，不解析shell、不解决软链/别名，
也不补出删除、重建、文件版本或连续存活。该覆盖不足以给出完整文件来源。

| 文件已存在错误的先前同路径报告成功关系 | 全部错误 | 其中child错误 |
| --- | ---: | ---: |
| 自身invocation | 34 | 3 |
| 另一invocation | 3 | 1 |
| 没有已确认的先前成功 | 26 | 15 |
| 合计 | 63 | 19 |

“没有已确认”不等于原仓库已有或没有其他agent写入；未知成功和execute副作用
仍可能改变文件。“先前同路径成功”也不能证明该文件持续存在到错误时刻。
本地后端write_file是创建接口，已有路径会报错；不能把每次错误都叫系统
并发竞争，或据此让工具静默覆盖/拒绝模型调用。

**3个跨invocation案例均在先前写入者RETURN之后。**

| workflow / 失败方 | 路径 | 先前写入者RETURN到失败开始 | 紧接输出token |
| --- | --- | ---: | ---: |
| django-13128 / general-purpose child | test_temporal_subtraction.py | 23.264秒 | 34 |
| django-13128 / root | test_temporal_subtraction.py | 1,378.072秒 | 38 |
| xarray-4966 / root | test_unsigned_false.py | 988.378秒 | 52 |

第一行的写入者确为同JOIN的test-analyst sibling。它已返回，后一个child才
尝试创建同路径；不是观测到的同时写入。后两行为parent在child返回之后
创建同路径。期间是否删除/重建及相同内容仍未知，不能补成文件因果链。
两项任务均在原resolved/正确完成集合内，measurement_complete=false保留。

全部63次错误的工具墙钟累计94.924ms，紧接的63个唯一模型请求共12781输出/
54947 uncached输入token。3个跨invocation错误的工具墙钟累计2.611ms，
紧接3个请求共124输出/2890 uncached输入token。这些不是全恢复链成本、
可避免工作或收益上界，后继请求可能继续必要任务，不能按整段输出称浪费。
pytest-10081的一个child在367.861秒跨度内创建test_repro.py失败9次，紧接
输出累计306 token；来源未知，不能说是sibling创建、完全同参数重试或可省
367.861秒JCT。

**研究决策。** 同名创建错误这个子假设暂降优先级，不能由此建设大规模
并发workspace隔离机制，或把普通创建/编辑语义改成论文贡献。第三条仍保留，
后续要找真实文件版本→验证→版本改变→后继使用/重算的链条，而不是只找
两个agent操作同路径。投入优先回到第一条的服务/上下文成本及第二条包含
工具和summary的实际依赖。当前没有证明并发增加必要工作，也没有关闭该问题。

8项CPU检查覆盖实际见证跨输入恢复、原root输入/正文、精确路径与workflow、
自身/他方区分、重叠/后发生不算先前成功、错误组后继去重及重复归因拒绝。
未改变GPU、运行时、harness、恢复、模型、guard、输出上限或workload。

- `experiments/reports/v1537_file_creation_provenance_cpu_20261017.xml`
- `experiments/reports/v1538_v79b_file_creation_provenance_20261017.json`
- `experiments/reports/v1539_file_creation_interpretation_20261017.json`

### 4.92 多轮准入：累计等待主要随调用次数增长，不直接证明逐调用不公平

只读`scripts/audit_join_wait_multiplicity.py`复用V1500的25848个成员请求、
V1529的40718个原生dispatch生命周期、V1495的真实JOIN及V1507完整工具
循环，不重新扫描worker或client原始大文件。严格核对请求、invocation、
workflow、输出与原生duration，继续区分dispatch和串行worker边界。

**先把“调用更多”与“每次更慢”分开。** 对175组配对成员，用全观测生命周期
计数，最后/先返回方的比值如下；身份为离线标签，不是在线关键分支预测。

| 同JOIN后/先返回方比值 | 各组比值P50 |
| --- | ---: |
| 模型请求数 | 2.034 |
| 累计首次dispatch前等待 | 2.059 |
| 平均每请求首次dispatch前等待 | 1.095 |
| 累计dispatch后至原生完成时长 | 2.019 |
| 输出token | 2.127 |
| 实际prefill extend token | 2.094 |

152组后返回方请求更多，149组累计排队更多，114组平均逐请求排队更长；
52组请求更多但平均排队并不更长。约2倍累计排队不能直接叫约2倍不公平，
也不能反过来把更多实际调用认定为固有必要工作。全生命周期均值受任务、
阶段与负载构成影响，和4.87共同阶段的平均首worker等待比不是相同口径。

另做对称代数分解，保持有符号差：
`Q_l-Q_f=(n_l-n_f)*(q_l+q_f)/2+(q_l-q_f)*(n_l+n_f)/2`。
175组累计差为27066438.141ms，调用次数项33175859.829ms，均值项
-6109421.688ms。这是精确账目恒等式，不是因果贡献或可减JCT；负项不能截零，
各项分位数不相加。少数较大样本可主导总和，不与逐组中位数混用。

**完整工具续接的时序覆盖补齐。** 原V1507已有38687个完整命名工具组，只有
24839个next请求关联成员原生时序；本轮用全请求表，原有循环全部核对起始/
后继native request ID和同invocation/workflow，新增13848个循环的计时覆盖，
没有补造新循环或把未观测当零。

| 完整循环的单项观察 | 全部38,687组 | 单剩成员完整请求8,536组 |
| --- | ---: | ---: |
| 工具组跨度P50 | 66.742ms | 77.225ms |
| tool-ready到下次提交P50 | 16.899ms | 22.188ms |
| 后继登记到首次dispatch P50 | 1.299秒 | 1.468秒 |
| 同一等待P90 | 8.203秒 | 7.523秒 |
| 排队长于工具组的循环 | 26,740（69.119%） | 5,987（70.138%） |

不同项的中位数不是同一条路径，也不能相加。client工具/bridge与server
排队只比较经过时间，不直接相减绝对时间戳。ready→提交仍可包含内部summary，
不叫纯client空等；dispatch后仍含交错和数据依赖，不是独占GPU执行。

**负载阶段不能忽略。** 在2400–3000秒，3385组的工具跨度P50为50.243ms、
后继排队P50为13.021秒，3372组排队更长；4200秒后五个600秒阶段的排队
P50分别为5.262/5.820/5.529/5.684/5.648ms。这些是分阶段中位数，末段仍按
原循环队列保留，不拼成一个全段值。工具时长全程分桶也非单调关系：

| 工具组跨度描述桶 | 循环数 | 后继排队P50 |
| --- | ---: | ---: |
| 0–10ms | 10,128 | 30.298ms |
| 10–100ms | 13,497 | 2.053秒 |
| 100–1000ms | 12,709 | 1.688秒 |
| 超过1000ms | 2,353 | 2.541秒 |

不能因全程中位数就固定保留运行槽、所有工具返回都提优先级，或声称越短工具
越受排队惩罚。工具类型、任务工作量、到达和负载阶段都混在这些条件统计中；
本轮没有选择任何新阈值或改变准入配置。

**上下文工作仍需纳入第一/二条，而非把长bridge归空等。** 全25848成员请求
中390个summary和390个压缩后首请求占3.018%，实际extend分别8703066和
3574854 token，合计占46.286%；其余25068请求为14248564。summary的
extend P50为21901.5、压缩后首请求8728。它们是已发生计算，不证明无用或
允许跳过摘要；worker共享区间不作独占GPU成本。普通上下文优化仍受既有强
对照约束，不恢复被用户撤下的harness/Mamba专用主线。

**已有方法边界再次核对。** Agentix[R25] §4.2.1已经用程序/最长观测路径的
attained service为未知动态DAG排序，§4.2.2有抢占、anti-starvation和
multi-step/bulk swap；附录A.3讨论并行短调用与长调用的干扰。故“多轮工具
后反复排队”“程序级元数据”“最后child重要”或普通续接队列，本身不构成
新颖性。论文已有能力不等于已在本模型复现，但合理适配必须作为强对照。

**研究决策。** 第一条下一步要解释高负载dispatch前的实际running/容量/
prefill约束，以及上下文工作；不能仅改最终child的优先级便宣布解决。
第二条继续找超出普通闭环到达的具体资源反馈，不由本轮等待量认定工具风暴。
第三条继续版本/验证失效链，同名创建子假设保持降优先级。当前证据支持
反复准入是成本对象，尚未支持独立于原生调优、程序调度、普通混批及其合理
组合的新机制，不能拿理想零排队作为可实现收益。

7项CPU检查通过，覆盖对称分解的正负/零项、原生duration守恒、成员重复/
身份、工具循环双原生端点与作用域、重复归因及描述桶边界。无GPU、运行时、
harness、恢复、模型、guard、输出上限、物理池、workload或调度策略变更。

- `experiments/reports/v1540_join_wait_multiplicity_cpu_20261017.xml`
- `experiments/reports/v1541_v79b_join_wait_multiplicity_20261017.json`
- `experiments/reports/v1542_join_wait_multiplicity_interpretation_20261017.json`

### 4.93 已服请求的空档：大部分对应其他请求prefill，仍须强普通混批对照

**先收束无法从旧trace补出的准入状态。** 复核4.74/V1477及冻结scheduler
hash后，逐候选FULL/Mamba可用量、可驱逐量及输出预留仍缺失；不重新用
resident_pressure推导空闲HBM、唯一拒绝原因或64请求资格。4.92的高负载
准入问题继续保留，但本轮不重复聚合统计冒充缺失的决策帧。

本轮转向可识别的服务交错，新增只读
`scripts/audit_prefill_decode_gap_exposure.py`。以V1495封存client事件重放
提交时JOIN/角色标签，复用V1529生命周期和V1500请求空档；为取得此前没有
保留的完整中间批次，单次顺序扫描3.58GB worker audit并核对原hash。
408707个连续序号、41046个prefill和367661个decode均复现，串行worker
起点仍是max(dispatch,前批完成)，不是独立CUDA计时。

**选取严格的观测片段。** 同一peer出现在前后decode批中，中间有一个或多个
其他prefill；peer不在中间prefill集合中，且后decode的worker起点尚未原生
完成。只累计中间prefill worker区间，不把中间CPU/未观测间隔补进去。
每个成员请求都与V1500的between_sampled_workers逐一核对，选中暴露量
不得超过已封存空档。保留peer不等于所有活跃或已ready请求。

| 成员请求的已记录服务间空档 | 全25848个请求 | 单剩成员8836个请求 |
| --- | ---: | ---: |
| 有所选prefill暴露的请求 | 25,671 | 8,732 |
| 累计服务间空档 | 26,908.521请求秒 | 10,252.468请求秒 |
| 其中所选其他prefill区间 | 26,787.248请求秒 | 10,209.233请求秒 |
| 对应该空档分母的比例 | 99.549% | 99.578% |

这里99.55%仅指这些请求的已记录服务间空档，不包括首次准入排队，更不是
全轮时间、GPU空闲或可省JCT。worker可能含依赖、host与observer作用，原生
overlap已开启，不能把串行观察器的区间直接当成独占CUDA阻塞。

**具体是哪类prefill。** 41046个prefill中40843个位于上述片段，200个没有
保留的未完成decode peer，3个缺前后anchor。34391个有peer的连续prefill
片段中，保留peer数P50为46、最大47。按prefill实际参与请求的角色整体分类，
混合角色不按token比例切分其worker时间：

| 中间prefill类别 | 批次数 | worker区间累计 |
| --- | ---: | ---: |
| 普通child | 25,207 | 522.444秒 |
| 普通root | 10,275 | 219.247秒 |
| 全部为内部summary | 3,653 | 389.864秒 |
| 混合角色或其他 | 1,708 | 68.397秒 |
| 合计 | 40,843 | 1,199.952秒 |

summary占所选prefill worker的32.490%，不是全部运行成本或无用工作。
按整批extend计，1–512 token的20452批为222.106秒，513–4096的20391批为
977.846秒；短批占18.510%。这与4.79单请求短prefill/整组保留的严格子集
不是同一分母，本轮没有把请求长度与chunk大小混为一谈。

全部prefill/decode串行worker分别1215.281/5910.805秒，前者占这类已记录
worker总量17.054%；该比例不是GPU计算份额或加速上界。另按peer数加权的
请求暴露累计42778.192请求秒，其中99.259%来自其他workflow的prefill。
这个量乘了peer数，不能与1199.952秒或JCT相加；角色、标签和workflow关系
是不同边际统计，不凭它们推断联合因果或安全可延迟对象。

**一个可复核片段。** native-service-000149699与000149718两个decode之间
有18个prefill批，worker合计2021.720ms，33个peer前后仍有decode记录；
它们的提交标签为root12、parallel7、sole14。18批中10批为summary，另有
普通child3、普通root1及混合4。sole只表示提交时本地JOIN状态，原来单成员
也算；不能称14个全局关键请求，也不能断言插入的summary不重要。

**已有分块不等于已经混批或有界decode间隔。** 对server.log中的原始
server_args用ast.literal_eval解析，确认chunked_prefill_size=4096、
prefill_decode_interval=0、enable_mixed_chunk=false、prefill_delayer=false、
dynamic_chunking=false、max_running_requests=48、beliefkv_admission=false。
冻结scheduler在cadence为0时不会arm强制decode间隔；有new prefill就先选
prefill，另有现成mixed分支。它解释了为何分块后仍会连续出现多个prefill，
但不证明另一个配置对当前混合模型一定兼容或更快。

**研究决策。** 现在能把已获服务后的一大类空档定位到具体prefill，而非把
它留作泛化“调度开销”。但常规chunk/cadence/mixed及程序调度是必要强对照：
Niyama[R58]已有动态分块、混批与QoS决策，Sarathi[R56]也不能省略。仅把这
些能力用于当前模型或按JOIN标签调优，不足以成为新主机制。下一步需要
说明动态依赖/进度信息相对这些合理组合究竟多提供了什么有效执行选择；
工具/summary反馈和版本/验证失效两线仍保留，不从一般prefill干扰宣称新意。

7项CPU检查覆盖summary角色、多个prefill、终态尾随descriptor、自身不能作
干扰peer、无anchor/丢序号、逐请求空档上限和混合角色/工作桶。没有改动
GPU、运行时、harness、恢复、模型、guard、上限、池、workload、cadence、
混批或PrefillAdder/延迟设置；三条问题线与最终目标继续，主机制未选定。

- `experiments/reports/v1543_prefill_decode_gap_exposure_cpu_20261017.xml`
- `experiments/reports/v1544_v79b_prefill_decode_gap_exposure_20261017.json`
- `experiments/reports/v1545_prefill_decode_gap_interpretation_20261017.json`

### 4.94 强对照已有更多能力：先固定可用输入，不能靠缺元数据形成差异

本轮针对4.93之后的具体问题核对：动态依赖/进度究竟提供了什么强已有方法
拿不到的有效选择？这仍是研究问题，不先把普通priority/chunk/reservation
套回新主线。没有运行外部实现或启动GPU。

**版本与实际实现分开。** CONCUR固定arXiv v1 HTML；SAGA继续用V1188封存
v2 PDF；Agentix沿V1488 USENIX prepublication。另固定TokenCake作者仓库
`1901ebf31fdf63590148e61d5c2f65e9fb7ec010`，完整tree6069路径、truncated=false，
保留README、protocol、scheduling、config、core scheduler及offload policy；
6份原字节的Git blob SHA均与该tree对应，另记SHA256。该仓库的vLLM迁移与
原论文分别比较，不由README确认会议状态或移植性能。

| 强对照 | 已核对的具体能力 | 公平比较与剩余边界 |
| --- | --- | --- |
| Agentix | 未知动态程序、程序/观测关键路径累计服务、并行调用与anti-starvation | 不能把“不预测完整未来DAG”当新意；实际executor service不可静默换成调用数或dispatch后墙钟 |
| SAGA v2 | 显式/推断AEG、复用预测、剩余工作与deadline紧急程度 | §1.5明示动态图每epoch重推断和新agent时长估计限制；不能说全部动态场景不支持，也不能用事后剩余步数补输入 |
| CONCUR v1 | agent级admit/pause/resume，按KV使用和hit率反馈调节并发 | 运行中计算保留，在边界控制；普通反馈窗口或工作集限制已有，详细batch策略仍可合理组合 |
| TokenCake固定作者实现 | 图/进度/等待评分、JOIN分数继承、动态容量保留、空闲借用及cache affinity | critical_path、near_completion、remaining_depth由调用方提供；部分FullAttentionSpec专用预检查不等于全系统不支持hybrid |

TokenCake的request_score实际使用importance、图度数、进度、年龄及若干调用方
hint；begin_step在同application/JOIN组传播最高live分数。capacity plan与
generation commitment也有实际代码，而非仅README声明。它同时保留原生
chunk路径及decode时prefill预算接口。因此JOIN提优先级、额外reservations、
缓存就绪偏好或一般batch预算，本身都不能作为我们独有机制。
README另将当前迁移的需求恢复与论文predictive upload分开；本轮未验证其
完整offload路径，也没有将作者开发测量或论文加速比外推本模型。

**最新trace中哪些输入可以平等提供？** 新增只读
`scripts/audit_workflow_policy_inputs.py`，在每个llm_submit只读取此前的
client事件，复用既有JoinState，summary沿已声明parent继承。全40718请求
与V1529精确核对workflow/invocation，且重放label与原观察器完全一致。

| 提交时已观测输入 | 当前覆盖 |
| --- | ---: |
| 请求 / workflow | 40,718 / 156 |
| 有正在等待的本地JOIN | 28,731 |
| JOIN还剩两个成员 / 一个成员的请求 | 17,012 / 11,719 |
| 内部summary / 已观测压缩后首普通请求 | 670 / 670 |
| 请求所属声明spawn深度0 / 1 | 11,987 / 28,731 |
| 请求提交时观测到的最大活跃dynamic child数 | 2 |

最后一行以请求提交时刻为条件，不直接称任意时刻峰值。elapsed、已创建/
已终态child、已完成模型调用数及本地JOIN成员数也均可从过去重建，但不证明
冻结engine已经收到。角色不是应用importance，本地sole包含原本单成员JOIN，
不是全局关键路径；观察调用数不是剩余GPU工作，更不是ATLAS的executor service。

尚未给出的输入明确不填：完整未来DAG、实际最后返回者、剩余call/depth/
GPU-seconds、全局关键性、安全可延迟时间或应用workflow deadline。不能从
全trace看见它们后，回填给自己的策略；也不能把未知当已知零，然后宣称先前
方案失效。若评估oracle，另列未来信息；当前ledger不包含这些oracle字段。

**研究决策。** 后续合理适配要给各方法同样可用的JOIN/tool/summary事件，
不靠削弱baseline接口证明workflow信息价值。动态图和时长估计难题真实存在，
但Agentix等非预知方法已经是必要对照；SAGA/TokenCake仍可接收部分显式hint。
下一份主机制提案必须指出：给足这些信息、加入普通chunk/mixed/程序调度后，
哪个具体执行选择仍然不同，能少支付什么自然成本，以及正确性/公平边界。
不能只换权重、加一个agent标签，或把已撤下三机制恢复成既定方向。

第一/二条继续找超出程序排序与普通batch控制的依赖/反馈损失；第三条继续真实
版本→验证→后继使用的额外工作证据。当前没有复现这些完整系统、声明本地兼容、
选择新策略或取得质量匹配性能收益，整体目标仍开放。

7项CPU检查通过，覆盖未来RETURN不重标过去、未来SPAWN不填过去图、
summary ancestry、此前完成计数、compaction消费、嵌套声明及缺失祖先/结果
拒绝。嵌套仅为观察器unit fixture，不是新workload或本轮深度2证据。
无harness、恢复、模型、guard、cap、pool、workload、prefill/GPU改动。

- `experiments/reports/v1546_concur_v1_20261017.html`
- `experiments/reports/v1546_tokencake_tree_20261017.json`
- `experiments/reports/v1547_workflow_policy_inputs_cpu_20261017.xml`
- `experiments/reports/v1548_v79b_workflow_policy_inputs_20261017.json`
- `experiments/reports/v1549_prior_policy_inputs_scope_20261017.json`

### 4.95 撤下共享状态线，探索动态依赖下的异速批执行

**范围调整。** 用户明确放弃第三条共享环境/验证失效研究线。本轮到此停止
文件版本、并发编辑、工具正文和后继重复推理的深挖，不因已经写出审计器而
继续投入。V1550–V1553及先前产物保留，但不升级为研究主线，也不声称已证明
竞争或可移除工作。第一条系统放大的分支失衡、第二条跨资源反馈继续保留。
没有启动GPU、改变harness、模型、恢复、workload或冻结引擎。

**新候选的问题。** 动态subagent的模型请求处于不断变化的程序依赖中：
一个调用结束后可能进入工具、创建更多child，或者使parent恢复执行。
同批请求的token推进速度相同，不意味着它们对workflow完成时间的贡献相同。
当请求已经进入同一批，改变准入优先级也未必使关键调用每秒推进更多token。
值得研究的是：能否利用当时已知的依赖，改变批内各调用的实际推进速度，
同时保留其他workflow的批处理效率，而不是仅重新排列请求。

这不是“唯一剩下的child必然最重要”的结论。本地sole不等于全局关键路径；
另一个长分支可能是固有任务更多，未来分叉/工具/模型工作也仍未知。当前
候选只能从已经观测的依赖、服务和生成进度取得输入，不能回填真实最后
返回者、未来剩余token/call、完整DAG或固定可延迟时间。

**现有证据支持研究对象，不支持收益承诺。**

| 已完成的本地证据 | 对新候选的含义与限制 |
| --- | --- |
| V1519/V1521：后返回成员61.851%的both-live decode worker暴露与sibling同批；175组平均首服等待比P50为1.008 | 没有把“decode持续偏爱早返回者”作为已知根因；值得区分谁先服务与服务后实际推进速度 |
| V1541：后/先返回方调用数比P50为2.034，平均逐调用dispatch前等待比为1.095 | 单纯平衡首服等待不能抹去实际观察到的长串行链；不能由此说更多调用都是必要工作 |
| V1495：最后单成员区间内仍提交普通模型请求P50为29轮 | 新候选应作用于持续续接，不再围绕最后一次RETURN前的H2D时刻 |
| V1548：提交时11719个请求已知本地JOIN只剩一员，17012个仍有两员 | 有事件时点可用的依赖输入，但没有天然的全局重要性、安全slack或未来服务预算 |
| V1458：367661个decode批及7490631个输出token；V79b ready明确record_output_token_ids=false | 生成执行是可研究成本对象；现有计数/可见正文不能支持精确草稿接受率或推测提速重放 |

**需要改变的系统执行方式。** 一个具体候选是将同批调用的推进量变为不同：
有合适草稿且值得加速的续接，通过目标模型验证一次推进多个token；其他
调用采用普通推进或较浅验证。动态分叉、工具等待及JOIN状态变化后重新选择
加速对象。目标是减少阻塞程序后继的顺序目标模型执行轮次，并约束额外验证
对同批其他请求的影响。这里的“多个token”是有条件的接受结果，不能预设
固定接受长度，更不能跨未知工具结果执行下一轮调用。

真正的研究难点是执行决策的耦合：增加某请求的验证量会改变整批耗时；
一个分支多推进一些token，可能仍不改变JOIN完成；工具或summary又会改变
当前依赖。计算预算、实际接受收益和阻塞关系要联合处理。仅将一个JOIN标签
接到已有优先分数或草稿长度上，仍只是合理组合，不据此认定OSDI创新。

**本轮原始文献核对及候选排除。**

| 已有方法 | 对候选的直接约束 |
| --- | --- |
| TAPER [R37] | 已研究额外分支对同批串行阶段的影响及按余量调节宽度；“支线插缝”和动态宽度本身不是空白 |
| AdaServe [R62] | 已有按请求SLO选择不同验证树/推进速度；“同批异速”本身不是新机制 |
| DAS [R63] | 已通过差异化推测预算加速决定RL rollout makespan的长轨迹；“给拖尾者更多草稿”不独立成贡献 |
| Pie [R64] | 已提供可编程请求控制与多种解码执行支持；可配置执行接口不能单列为我们的新抽象 |
| FlashAgents [R61] | 已将上游生成与下游prefill重叠；仅提前处理child报告不是新方向 |

TAPER本轮按原文更正题名为Regulating Branch Parallelism in LLM Serving；
此前R37的Efficient Intra-Request Parallelism不是本次所读v1题名。FlashAgents
原文PDF访问遇到浏览器验证；本轮机制核对依据MLSys官方演示资料及原始PDF
检索摘要，不声称读完其完整正文。AdaServe核对作者EuroSys26 PDF，
DAS核对arXiv v1，Pie核对作者SOSP25 PDF，不混用论文加速比。

这组文献之后，尚待建立的差异只可能是：动态多轮程序中，当前依赖与生成
执行方式的联合决策，能避免强方法及其合理组合仍会支付的具体成本。
必须同样提供可用JOIN/tool事件，并比较Agentix/TokenCake加AdaServe、
TAPER式宽度控制以及agent草稿策略。不能由某篇论文没实验当前模型，就声称
它不能合理适配；不能用“图动态变化”替代机制证据。

**下一阶段CPU研究任务。** 先选已有真实JOIN案例，写出当时可见的依赖和
请求执行选项，区分首次dispatch、prefill、decode、tool和summary。检查是否
存在“请求都在执行但需要不同推进速度”的具体选择，以及普通程序排序、
混批和按请求加速的合理组合为何仍不足。缺少接受率、物理服务曲线或精确
token时保留条件分析，不能从输出计数模拟真实推测接受。只有找到这种反例、
有规模的适用请求和可实现的执行机制，才将候选提升为主线。

若最终只得到现成程序优先级加现成推测解码，或收益仅来自尾部小batch、
提前结束少数workflow而损害有效吞吐，则降低此候选优先级。若没有可用草稿
或所处负载没有额外验证余量，也应放弃该具体执行路线。当前没有新增代码、
GPU资格或性能结论，不以“同批异速”已经选定替代尚未完成的研究判断。

### 4.96 提交时依赖与实际执行：正确完成任务也有生成对象，但瓶颈分阶段

本轮继续4.95的CPU必要条件筛选，不恢复共享环境第三条，不把旧goal中的
三个已撤下机制当实施要求。新增只读
`scripts/audit_dependency_execution_profile.py`，关联封存V1548输入、
V1529原生生命周期和V1500成员服务，核对请求身份、同一原生时钟、两级
来源hash及持续时间恒等式。没有重扫raw、改冻结运行码或启动GPU。

**先纠正sole的含义。** 40718个请求全部一一关联；其中root11987，
parallel17012，sole11719。sole里2883来自本来就只有一员的JOIN，
另8836是在sibling终态后提交；终态可为RETURN/CANCEL，不当全局关键性。
后一组按类型分为8442个普通调用、194个summary、200个压缩后首调用。
详细worker分区只覆盖原来的25848个双成员请求；其他请求保持未知，不补零。
按依赖、依赖/类型、阶段/依赖/类型分组的总量均精确回到40718。

| sibling终态后的普通请求 | 全原始任务 | 原正确完成/JCT合格任务子集 |
| --- | ---: | ---: |
| 请求 / 涉及workflow | 8442 / 147 | 4337 / 86 |
| 输出token P50 / P90 | 79 / 440.8 | 78 / 371 |
| 首次dispatch前等待P50 | 1.459秒 | 2.924秒 |
| decode worker暴露P50 | 2.086秒 | 2.205秒 |
| prefill worker暴露P50 | 13.371ms | 14.637ms |
| 服务间空档P50 | 442.346ms | 524.342ms |
| 观察输出至少128 token的请求 | 2502 | 1135 |

不同列是事后质量分组，不是配对因果比较。各项分位数不相加；worker是
共享串行worker区间，不是某请求独占CUDA、可移除时间或JCT收益。16/128
只作输出长度描述，不设为调度阈值。长度与接受率都不是这里的在线已知量。

**质量筛选改变了案例选择。** 最初按生成暴露取出的前三名django-12193、
pytest-6202、django-12406均官方unresolved，不能依靠它们宣称有效完成收益。
最终V1557另接已完成的原官方评分，保留全156项、96 resolved、94项原正确
完成与JCT合格、measurement_complete=false，不重评或改变原guard/资格。
质量仅用于事后分组，不写入提交时依赖或候选策略。

正确完成的django-13128也有自然案例：同一个JOIN在sibling终态后仍发生
245个普通模型调用、39143个输出token；这些请求累计dispatch前等待约
4.238秒，累计共享decode worker暴露约752.485秒。这证明继续生成这一
研究对象不是仅由失败轨迹组成，但没有证明草稿可接受、并发下净加速或已有
方法处理不好。不能从累计请求暴露减出全局GPU/JCT。

**不能用全程中位数选择固定执行策略。** 同样限定原正确完成任务的普通
sole请求，按原生单调时钟相对首请求的窗口观察：

| 时间窗口 | 请求 | dispatch前等待P50 | decode worker暴露P50 |
| --- | ---: | ---: | ---: |
| 2400–3000秒 | 483 | 13.158秒 | 3.506秒 |
| 4200–4800秒 | 387 | 5.440ms | 1.708秒 |

这不是两个可直接比较的反事实或完整压力分类。它说明待处理的成本对象
随阶段变化：前者不能只靠加快已经执行中的生成解释，后者也不能只靠重排
首服队列解释。两阶段是否有加速余量、可调整并行度或能接受的草稿仍未知。

**研究决定。** 继续生成对象在正确完成任务中有规模，4.95候选保留；但没有
将“给sole请求更多草稿”选为主机制。下一份论证必须给出一个当时信息可用、
且普通程序调度加AdaServe/TAPER/agent草稿等合理组合仍需支付的具体成本。
目前未取得这一强对照反例。不能因新审计器有数据就先接新控制器，也不把
两种成本同时存在当作已经建立的创新。

初版V1554的9项CPU和V1555完整结果保留，源码/测试匹配快照已保存。
最终新增质量子集与来源链核对后，V1556的11项CPU通过；两轮测试不相加。
检查覆盖生命周期/服务分量守恒、单成员与sibling终态区分、summary归属、
未知分区不补零、身份/缺失/重复拒绝、事后标签不重标输入、质量分组不改
在线行及完整原评分要求。观察器正确不等于推测引擎兼容或性能通过。

- `experiments/reports/v1556_dependency_execution_profile_cpu_20261017.xml`
- `experiments/reports/v1557_v79b_dependency_execution_profile_20261017.json`
- `experiments/reports/v1558_dependency_execution_interpretation_20261017.json`

### 4.97 执行能力已有，先下调直接组合和多分支终段方案

本轮不再重复证明生成有成本，而是检查4.95–4.96的候选是否需要新的执行
能力。保留AdaServe作者EuroSys26 PDF、派生text及TAPER v1原始HTML，
版本、字节和hash见V1563；没有运行其实现，也没有为此启动GPU。

**强组合不能被故意简化。** AdaServe原文§3–4已有按请求构建不同验证树、
在硬件预算内兼顾请求TPOT与期望接受量的执行方式。TAPER §3/Algorithm1
已有可替换utility及按边际收益/时延成本选择额外分支的接口。因此，比较
“JOIN状态→程序优先级→差异化验证预算”时，需要给合理组合相同的已知事件，
不能只比均匀草稿或固定分支数。如何产生有效图条件预算仍是研究问题；
这里不声称现有论文已经实现所有动态图目标，也不证明任意新控制器可约化。

一个ALL-JOIN只有两员都结束才完成：对完成集合的指示函数，空集与两个单员
集合的值均为0，双员集合的值为1。这只能说明workflow与可加的逐请求收益
可能不同，并非新发现、真实成本证据或足以成立的系统机制；已有工作流控制器
及可编程utility也必须纳入比较。不能用这条恒等事实宣称联合规划已具新意。

**新CPU必要条件：当前是否常见两边同时进行最后一次生成？** 在既有审计器
中加入终段关联：对每个原双成员JOIN，以client结果时间选出各direct member
唯一的最后普通模型调用，排除internal summary；核对同member、request ID、
结果不晚于终态和原生时钟。分别计算client调用区间及服务器first-decode
到result区间的交集；不跨client/server时钟直接相减。
本节“普通”指非internal调用，包含压缩后首调用；不同于4.96按request_kind
排除压缩后首调用后的ordinary子集。

| 最后普通模型调用的观察 | 全部双成员JOIN | 原正确完成任务中的JOIN |
| --- | ---: | ---: |
| JOIN数量 | 175 | 103 |
| client调用区间有交集 | 10 | 8 |
| 原生decode span有交集 | 7 | 5 |
| 无法确定decode span的组 | 0 | 0 |
| 两成员终态差P50 | 312.608秒 | 344.776秒 |

这是离线选出的最后调用，不假设在线知道它是最终报告。decode span含交错
空档，交集不等于同批、可用验证预算或收益；未重叠也不排除更早分支阶段
的依赖优化。175组的先/后成员最后结果到终态P50为8.477/9.442ms，
仅为client结果事件到terminal事件，不混作流式最后chunk窗口或parent续接时延。

**研究决策。** 将“给唯一剩余child更多草稿”下调为工程对照候选，不作为
独立OSDI主创新继续推进；也不从7/175的终段重叠建设专用联合收尾机制。
保留广义依赖执行问题，但它仍须给出强方法及合理组合之后的自然成本和
不同执行选择。前两条系统放大失衡、跨资源反馈继续，第三条共享状态不恢复。
没有因为已有观察器而继续安排本候选的接受率GPU实验。

V1562完整复现V1557所有原分组、执行及质量计数，另保存175对最终request ID；
V1560源码/测试快照匹配旧报告绑定。最终15项CPU检查通过，新增检查覆盖
summary排除、终态桥接、client重叠不等于原生decode重叠、未知不补零，以及
缺失/歧义/跨成员/越过终态拒绝。论文与本地证据的对照不是算法等价证明、
完整移植或性能验收，整体目标保持开放。

- `experiments/reports/v1559_adaserve_eurosys26_20261017.pdf`
- `experiments/reports/v1559_taper_v1_20261017.html`
- `experiments/reports/v1561_dependency_execution_profile_cpu_20261017.xml`
- `experiments/reports/v1562_v79b_dependency_execution_profile_20261017.json`
- `experiments/reports/v1563_execution_candidate_scope_20261017.json`

### 4.98 异速相对暂缓的必要优势，以及动态workflow真正改变的KV需求

**异速路线的进一步判断。** 用户指出，异速很难说明优于推迟非关键agent。
当前同意不将其作为核心实现方向。公平比较至少允许暂缓策略按较细时间粒度
分配服务，向它提供相同已知依赖，并给关键请求相同的推测解码能力；暂停不
预设必须驱逐KV。否则，新方法可能只是获得了更强的解码器或更有利的保留规则。

只调整普通decode调用的执行频率，可以由交错执行/暂缓形成不同平均速度，
不构成新的物理收益。推测验证确实能改变每次目标模型调用的推进量，但这是
AdaServe等已有能力。异速若要成立，须在核算草稿、整批执行、控制和状态成本
后证明：相近的关键路径完成时间下有更多有效工作完成，或相近的有效吞吐下
关键路径更短。只证明关键请求比均匀大batch更快，不足以回答用户的比较。

潜在优势依赖有额外可用计算资源、合适的接受率，以及保留其他请求时很小的
边际成本；本模型/负载尚无这种证据。保留非关键分支进度可能提前启动工具，
但细粒度暂缓同样可以给它服务，故也不能直接作为异速独有收益。
4.96的生成成本与4.97的终段重叠均未证明上述条件。保留通用推测方法作为
未来强工程对照，不据此增加本项目引擎改造或GPU队列。

**KV的物理对象与agent执行图应分开。** 对精确前缀缓存，同模型/位置/掩码等
执行契约成立时，一段历史可由多个后继共享；实际空间按不同物理页核算，
不能按各agent逻辑上下文长度直接相加。相反，读到相同文本并不保证拥有相同
KV，前面的上下文不同会影响后续层状态。JOIN一般传递结果文本，不能直接
拼接各child的KV来得到parent状态。这里不恢复已撤下的共享workspace研究。

| 动态执行事件 | 对KV的实际影响 | 应查的系统成本 |
| --- | --- | --- |
| parent进入WAIT_JOIN、child继续执行或再次分叉 | parent暂时不发模型请求，但其历史仍可能用于后继；只有实际输入相同的前缀才共享 | 无请求的存活历史是否占用了稀缺层级，以及保留/移出/重建的实际成本；不能把每次SPAWN都当parent停顿 |
| 多个child在共享前缀后各自增长 | 共有页与私有页的使用者、边际空间和最后使用时间不同 | 真实物理重复、对共有前缀与私有尾部的错误保留或回收；引用计数本身已有 |
| child终态、JOIN满足 | 某些私有生成状态可能不再需要，parent历史即将再次被请求；共有页和仍有消费者的状态继续存活 | 是否备份了无后继的私有状态，却丢失后续实际可复用的历史；不一律将child全部KV称无用 |
| 结果汇入、summary或历史重写 | 结果文本成为parent的新输入；append与改写对旧前缀有效性的影响不同 | 区分容量驱逐、合法上下文失效和新输入计算；不能把所有uncached token称为可避免重算 |

相对普通多轮对话，动态分叉把这些现象耦合起来：多份存活历史具有共享关系，
下一次使用受其他分支约束，分支终态又会改变可回收集合和需要保留的对象。
单个现象也可能出现在单agent，不能以“发生在agent”或“图未知”认定新问题。
嵌套可能增强这种耦合，但当前V79b所有child仍为深度1，不补造嵌套证据。

**已有能力必须正面纳入。** PagedAttention [R65]已有跨请求共享；
官方Session-Aware Radix Cache [R66]已有session软引用及Host/device的驱逐
偏好，close移除引用而非立即删除物理数据。本地冻结引擎也有session tracker，
不能把新增身份标签、close或引用计数当空白。SAGA/R12的workflow保留、
Lachesis/R9的生命周期分层、EfficientAgent/R10的Host工作集/写入准入和
TokenDance/R11的collective共享继续构成对照。未在同一模型复现不代表没有能力。

**当前值得保留的KV研究问题。** 动态分叉与汇合是否产生了可观的物理重复、
无效备份，或真正仍可复用的历史被丢弃后的重算，而且这些损失在原生共享/
session生命周期及已有分层/准入方法合理组合之后仍存在？研究对象是具体
字节和恢复/计算成本，不先指定新的预取时刻、保护配额或三个旧机制。

现有证据还不能承诺这条问题有足够收益。4.78/V1489的38840个同session探针
中，所观测旧输入公共前缀缺口P50/P99仅32/63 token；H已包含Host命中。
它不证明H2D很少，也不覆盖全部生成输出或1878个无探针请求；但不能再仅凭
parent曾等待，就假设有大块可避免重算。迁移字节、逻辑重复文本与原生实际
损失分别核对，不能用更多迁移或更复杂控制器代替研究证据。

本轮只完成比较标准和问题范围的调整，没有新增运行时代码、CPU测试、GPU
实验或性能结果；主机制与整体质量/性能目标仍未验收。

### 4.99 FULL Host写入与回收：已有具体成本，尚未归因动态fork/join

本轮继续回答异速相对暂缓的独立价值以及动态workflow对KV的影响，复用既有
CPU审计器读取最新完整V79b，不新增策略、引擎观测、测试或GPU运行。
4.98对异速的判断不变：若只是调整普通decode参与频率，细粒度暂缓即可表达；
若使用差异化推测能力，强对照应得到同样能力。未证实额外执行收益。

**物理计数已对齐。** 封存transfer与host_pool完整行前缀，核对唯一完成
receipt和每池物理字节。冻结引擎的`FullComponent.evict_component`按实际
`len(cd.host_value)`计数并清空Host引用；`evict_host`先释放再回调observer，
不是候选扫描次数。初始Host FULL为空、结束无pending batch/request、
writer错误或丢记录。单位为20480字节/token槽：

| FULL项目 | token槽 | 十进制GB |
| --- | ---: | ---: |
| 完成D2H写入 | 72699039 | 1488.876 |
| 完成H2D读取 | 29851444 | 611.358 |
| Host回收 | 64888979 | 1328.926 |
| 最终保留 | 7810060 | 159.950 |

`写入-回收=最终保留`精确成立。把全部H2D读取，包括重复源和请求CoW目的
副本，都宽松地视为不同Host源，仍有至少`64888979-29851444=35037535`槽，
即717568716800字节的已回收副本，不可能全部作为不同的已完成恢复源。
这是按物理copy-lifetime计量的保守数量下界，没有按node anchor推断重复页，
也不需要假设全部模型forward已被记录。后者对全无identity的batch仍有覆盖
缺口，故不发布`写入-所记录KV生成量`的重复传输下界。

**不能直接改称浪费或收益。** 本下界不识别具体哪些副本未读，不证明在线可
提前判断，也不否定备份的容量释放/安全价值。结束时仍保留的159.950GB不算
已回收下界。FULL-only写入24669批、1072.189GB，transfer-stream累计297.544秒；
含Mamba批次另累计144.887秒，不能全算FULL。所有stream累计均不等于暴露
GPU等待或可减JCT。完整性能/原始质量及measurement_complete状态均未改。

**问题范围与差异要求。** 这提供了独立于Mamba的真实成本对象，但一般Host
工作集溢出也会产生同样现象；EfficientAgent/R10已经处理工作集与容量条件
写入，原生session/R66和SAGA/R12也已有保留/回收能力。因此不把“少写Host”
或“child结束就关闭session”作为新主张。后续应先区分：

1. 写入后无后继的分支私有状态，与仍有消费者的公共历史；
2. 一般并发容量不足造成的回收，与JOIN等待/分支终态改变使用关系的部分；
3. 强原生生命周期和已有写入准入合理组合后仍有的损失，以及其暴露成本。

只有这些差异与足够大的代价同时成立，才值得设计新机制；不能由717.569GB
直接推断OSDI新颖性或承诺吞吐收益。当前仍保留分支失衡与跨资源反馈问题，
不恢复共享workspace线、混合状态写入旧论点或旧三个机制。GPU继续停止。

凭证：`experiments/reports/v1564_v79b_full_host_retired_copy_balance_20261017.json`。
该文件保存原始前缀/状态文件hash、冻结实现与复用审计器hash、计数及限制。

### 4.100 精确FULL区间重复回收与再访问：收窄历史丢失假设

4.99的总量下界不能定位写入来源，本轮改用V79b既有的精确前缀驱逐诊断，
没有新增GPU观测。新增CPU审计器`audit_full_host_copy_churn.py`，连接全部
40718个原生请求与V1548提交时角色，重放FULL/Mamba共同竞争的32768项
观测索引。真实驱逐ID、episode计数、物理字节、最终pending与过期计数、
原生Host池计数及再访问区间全部对齐；不按回执anchor或node ID推断同一页。

**确有重复物化区间的回收。** 对固定namespace、前缀长度L和完整前缀HMAC，
每次FULL回收范围为`[L-units,L)`；同一身份各范围的并集长度为`max(units)`。
因此`sum(units)-max(units)`计量至少重复回收过的区间量，不依赖模型forward
计数。67021个精确身份中7884个重复回收，累计下界29831895槽，即610.957GB，
占全部FULL回收量45.974%。不同endpoint不合并，可能还有未计入的重叠；
这不证明同时分配了重复页，也不证明有限Host容量下可以省掉这些传输。

重复下界中99.689%只有一个已归因再访问context；一个workflow内多个context
的观测仅6个精确身份，跨workflow183个，其余另有144个未归因身份。
“仅观察一个”不等于没有其他消费者，更不能据此把它们称为child私有尾部。
当前记录仍未提供每次FULL写入的全部物理页和最初创建者，终态污染的完整
因果链尚未建立。

**已匹配部分以恢复命中为主。** 72754条FULL再访问归因来自2685个请求。
这些episode的最新回收区间为587.589GB，其中HBM命中159.215GB、
Host命中428.156GB、重算代理0.218GB，占匹配最新区间0.0371%。
普通root的重算代理只有12.268MB；这里不是JOIN后所有请求的损失上界，
也不是独占计算或可减JCT。原生计数区间给出的是请求cache边界归因，
不等于逐层实际重算工作。

全部回收episode按真实累计单位守恒，重复回收不被漏掉：

| FULL回收episode的后续观测 | 十进制GB | 解释 |
| --- | ---: | --- |
| 发生精确匹配再访问 | 587.590 | episode可能多次回收，故略大于最新区间 |
| 索引容量过期 | 422.994 | 后续行为未知，不计作未使用 |
| 观测结束仍pending | 318.342 | 未匹配，不证明没有有效消费者 |
| 合计 | 1328.926 | 与物理Host回收精确一致，残差0 |

未知部分占55.785%，所以不能把0.218GB推广成全程重算上界，也不能把
未知部分全部判为无用。4.99的717.569GB未读源数量下界与本轮610.957GB
重复区间下界可能重叠，含义不同，不能相加。

**压力来源另行核对。** Host回收callback按请求组件分组，FULL触发释放
FULL 1205.228GB，Mamba触发释放FULL 123.699GB，后者占9.308%。触发组件
只是这次回收请求的来源，不证明全局池一定满、比例最优或动态JOIN因果。
没有将Mamba配置问题包装成研究机制。

**对下一步的影响。** 当前证据确认了大量真实缓存周转，但削弱了“动态
分叉主要制造大量不可恢复历史丢失，应优先保护等待parent”的直接主张。
在未知部分得到更强证据前不宣称该主张已被完全否定。EfficientAgent/R10
已明确研究工作集超过Host后miss-driven churn、容量条件写入与增量去重，
所以重复回收本身不能作为已有方案缺失。先区分普通容量周转与动态依赖
改变消费者/使用时机后的剩余代价，再选择机制；不凭GB量重启预取、设置
新保护配额或恢复旧三个机制。分支失衡和跨资源反馈继续，第三条仍撤下。

22项CPU检查通过，覆盖重叠区间去重、namespace/endpoint隔离、双池索引
过期、重复episode守恒、scope/字节/时间/Host边界矛盾和最终pending一致性。
它们验证审计器，不是引擎性能实验。新增报告封存原始前缀与审计源码hash，
原始156项质量、终态和measurement_complete均未改。GPU继续停止。

凭证：

- `experiments/reports/v1565_full_host_copy_churn_cpu_20261017.xml`
- `experiments/reports/v1566_v79b_full_host_copy_churn_20261017.json`
- `experiments/reports/v1567_full_host_churn_research_scope_20261017.json`

### 4.101 重复区间容量与原生Host副本回收：不能等同于历史丢失

继续检查4.100的周转究竟是反复使用的数据本身超过Host容量，还是需要更强的
通用准入/保留对照。新增CPU审计`audit_full_host_retention_capacity.py`，
复用V1566/V1564封存的驱逐与Host快照，不修改运行时、workload或GPU。

**子集容量画像。** 7884个重复精确身份按各自最大FULL区间计费，合计
3259738槽、66.759GB，小于Host FULL容量160.000GB。若从首次记录回收前
保留到最后回收/已归因再访问，所观测时间包络峰值为66.116GB。至少两次
不同请求再访问的6923个身份，最大区间合计58.992GB。不同endpoint没有合并，
可能重复计费共有坐标，但这些都只是事后选出的重复区间子集，未覆盖早期
写入至首次回收的驻留、必要祖先、其他首次使用备份及未观测消费者。
因此不称为完整working set、oracle可行计划或在线保留容量保证。

相邻重复回收之间可找到已归因再访问的72498对，重叠区间累计586.550GB，
相当于4.100重复区间下界的96.005%。它确认大量周转之间有真实前缀再访问，
但不证明读的是上一次物理Host副本，也不保证保留该副本不会挤出其他有效数据。
普通历史复用信息必须作为对照，不能把所有这些机会都归于动态JOIN预测。

**回收并不都意味着KV失去有效副本。** 本轮读取V1394启动资格登记的引擎
tree `53020ce3efef3ed362e9af0db77147b39070f064`，逐字节核对保留的scheduler、
unified_tree_core与FullComponent；scheduler hash同时匹配native ready。
已运行的write-back路径在FULL Host压力下先执行`_reclaim_full_host_duplicates`：
要求FULL device/Host值都存在、无在途DMA和Host锁；释放时只移除Host值。
这不是本轮新增策略，也不能把它当作原生缺失能力。

FullComponent的`already_satisfied`表示上述预处理已满足释放请求，无需建立
Host驱逐堆。7067对相邻快照与其间全部38311次FULL回调数量精确相符，其中
23925次为这种无heap调用、14386次建立heap。每个区间把最小的回调释放量
分配给无heap次数，得到27605968槽、565.370GB的保守下界，占全部FULL
Host回收量42.543%。374个全部无heap的区间可直接确认405.013GB；其余按
最保守配对。建立heap的调用也可能先释放部分重复副本，未计入该下界。

这些事件发生时HBM仍保留有效FULL，所以不能计为“有用历史全部丢失”。
后续HBM驱逐仍可能重新触发D2H，保留Host副本也会减少其他Host-only数据
可用容量，因此还不能称为浪费、可省传输或性能收益。此565.370GB与前两轮
610.957GB重复区间、717.569GB完成读取源数量下界可能重叠，不能相加。

**选题影响。** 不再由Host驱逐总量推断应保护等待parent或放大池；先区分
副本层级选择、普通复用历史与动态依赖带来的额外信息。EfficientAgent/R10
的工作集/准入、原生session及副本回收都应得到合理对照，不以本轮画像
实施固定Host保护预算。动态fork/join的独立差异、暴露成本和同质量收益
仍未证明。保留前两条问题线，第三条及旧三个机制继续撤下。

18项新CPU检查通过，覆盖区间最大容量、相同边界释放顺序、较晚再访问、
缺失/重复请求身份、混合快照最保守分配、全无heap与全heap及不完整计数。
它们验证审计与来源语义，不是GPU性能测试；原质量/终态资格保持。GPU停止。

凭证：

- `experiments/reports/v1568_full_host_retention_capacity_cpu_20261017.xml`
- `experiments/reports/v1569_v79b_full_host_retention_capacity_20261017.json`
- `experiments/reports/v1570_full_host_retention_research_scope_20261017.json`

### 4.102 普通历史信息在何时已可用：收束重复FULL周转的独立主张

4.101说明了真实周转和Host副本回收，本轮直接检验workflow信息是否是识别
这些机会的必要条件。新增CPU审计`audit_full_host_past_reuse.py`，重放原
封存前缀；每个精确身份只使用此前出现且源时间严格更早的回收/再访问。
相同时间不计已知，后续事件不能更新早先决定。全部142351次FULL回收、
72754次再访问和物理/重算计数复现；没有修改GPU或运行时。

**必须区分两个时点。** 在当前回收候选出现时，Host副本可能已完成D2H，
此时识别重复不能省回已经发生的传输。进一步把信号冻结到上一次回收之前，
只计两次相邻回收区间的重叠量；不能用当前的新访问或以前更大的区间回填。

| 使用此前发生的普通事件 | 当前回收时覆盖的重复区间 | 上一次回收时已可用的相邻重叠区间 |
| --- | ---: | ---: |
| 曾回收过同一精确身份 | 610.957GB / 100% | 544.229GB / 89.078% |
| 曾有精确前缀再访问 | 609.918GB / 99.830% | 544.229GB / 89.078% |
| 曾有正Host命中 | 573.871GB / 93.930% | 517.178GB / 84.651% |
| 曾有正HBM命中 | 428.636GB / 70.158% | 369.846GB / 60.536% |

分母均为4.100的610.957GB已观测重复区间下界，不是全部D2H、有效预取或
工作流时间。普通再访问信号按原候选序列逐步标记7730个身份，最大区间
记账65.689GB；不代表改变驱逐选择后仍能满足全部Host需求，也没有模拟其他
victim、后续miss、祖先和准入。保留过去副本可能挤出更有用数据。
89.078%因此不是预测准确率、可避免传输比例或端到端收益。

审计目录在本轮保留全部已观测历史，未设ghost预算，元数据成本和源事件
到runtime的及时交付尚未测量。未观测/索引过期的访问依然是未知，不当负例。
测试对较晚访问、较大区间、相同时间及不同namespace明确检查不回填。

**强对照与选题判断。** ARC/R67已有recency/frequency自适应与非驻留历史，
不能把通用对照限制成不保留历史的裸LRU；本文只核对原文机制，没有实现/
复现ARC，也不把均匀page缓存直接当当前变长前缀区间的完整实现。
EfficientAgent/R10、原生session及SAGA等的合理适配继续需要相同可用信息。
当前审计既不证明这些方法已取得本项目的性能目标，也不证明它们无法适配。

现在不将“识别并减少这批重复FULL周转”单独作为必须依赖动态workflow的
主创新。它仍可能有工程收益；论文本身需要指出强历史对照和已有workflow
方法仍处理不好的具体情形。未被上述前一时点信号覆盖的66.728GB，也不能
整体算作workflow收益空间，尚有首次复用、其他普通特征和观测边界。
后续候选先给出带实际代价的反例，停止仅由更多回收总量推出新机制。
前两条问题线继续，第三条及旧三个机制仍撤下，主论文机制未选。

16项新CPU检查通过，覆盖冻结决定的前缀不变性、前后时点区分、较小旧副本、
不同命中层级、同时间/namespace隔离、未知访问和矛盾输入。它们是审计验证，
不是cache policy或GPU性能回放；原质量/终态资格及measurement_complete不变。

凭证：

- `experiments/reports/v1571_full_host_past_reuse_cpu_20261017.xml`
- `experiments/reports/v1572_v79b_full_host_past_reuse_20261017.json`
- `experiments/reports/v1573_full_host_past_reuse_research_scope_20261017.json`

### 4.103 从分叉语义到压缩视图：新问题须超过已有组合

当前状态由4.113补充：共同选集的直接贪心原型已完成CPU筛选，增量与读取/
选集成本尚不支持进入实现队列。本节的静态并集例子不作立项或吞吐证据。

本轮停止围绕重复FULL周转追加同类总量审计，直接核对自然动态workflow中
哪些KV问题已有方法处理、哪些还需要真实案例。只做一手文献、保留源码和
既有报告交叉核对，没有新增策略、单位测试、harness改动或GPU运行。

**当前SPAWN并不等于原样继承parent的模型状态。** 保留app构造独立
subagent prompt；当前环境的DeepAgents task中间件排除parent的messages，
以任务description构造新的HumanMessage。该已安装依赖本轮未独立证明与
收集时逐字节相同，因此另以封存policy/native身份交叉核对，不只看源码。
全部40718个请求唯一匹配，376个child的首次普通请求均有此前root普通请求：

| 输入长度一致性核对 | child首请求 | 此前最近root普通请求 |
| --- | ---: | ---: |
| P50 token | 5530.5 | 8930 |
| P90 token | 5567 | 15707.5 |
| 最大token | 5788 | 37244 |
| child更短的配对 | 376/376 | 不作具体LCP或所有权证明 |

子/父长度比P50为0.61949。长度只能排除完整原生输入前缀的原样包含，不能
判断语义历史、实际公共前缀或物理页。child首请求有效缓存P50为3584 token，
不能称为没有共享；它也不说明缓存来自该parent。现有全部child仍深度1，
201组JOIN各有一个声明waiter，没有producer set的多个声明消费者。
增加root压力不会自动产生长历史继承、嵌套或多消费者的应用关系。

**新增相关工作必须正面纳入。**

| 具体问题 | 已核对的一手已有能力 | 本轮判断 |
| --- | --- | --- |
| 阶段切换使最近query不足以代表后续读取 | AgentKV/R68的多类别query代表与跨turn物理压缩 | 仅加阶段标签或维护query buffer不足以构成新意 |
| 压缩发生时未来query尚不可见 | 在线压缩研究/R69的延后压缩与后续query代理 | 不把“未来未知”单独当未解决问题；其大模型serving数字是模拟 |
| 多agent是否能共享压缩表示 | PolyKV/R70的单一量化源与独立DynamicCache接入 | “共享+压缩”已有主张，须比较真实分配而非只比较pool |
| 不同prefix下重复文本的KV不能精确拼接 | KVComm/R71的anchor与位置/上下文偏移估计 | 近似复用已有，不把child结果文本当原生KV直接可拼 |
| 同批在途prefix重复、JOIN输入位置未稳定 | FlashAgents/R61官方演示的临时prefix树与位置稳定约束 | 增量prefill/提前加载本身已有；本轮仍未取得完整OpenReview正文 |

R69明确压缩Qwen3.5的FULL-attention部分、保留其他混合状态，不能笼统以
Mamba/GDN为由排除；其27B并非本项目35B-A3B。R70的压缩pool与各agent
解量化后的tensor不是同一个计量对象，需计入展开、私有尾部及workspace；
本轮没有测得其实际分配，既不沿用其内存比例，也不据伪代码断言实现错误。
R71固定Ye等人的2510.12872v1，不混用同名方案或不同版本的加速数字。

AgentKV顶层repo固定b5280b7，可读取gitmodules和engine gitlink
254c57bc；该MiniSGLang子模块的公开tree请求本轮返回HTTP404，未运行其
实现。这是复现可达性边界，不是方法不存在、功能不足或无法适配的证据。
TokenDance/R11的collective共享、原生共享/COW及已有workflow方法继续作强
对照，不能只因为当前未移植就削弱它们。

**一个可具体证伪、尚未选定的系统问题。** 多个自然fork的消费者需要同一
长历史，但各自压缩保留集合不同，单agent减少KV是否反而破坏整体共享？
设公共不可变prefix有N个等大KV单位，4个agent各保留一半并各自物化，
总量为2N，比未压缩共享一份的N还大。这是分析例子，不是当前trace测量。

然而，对固定且值兼容的保留集合S_i，每agent逻辑视图引用共有物理页，
存储只需`|union S_i|`。经典共享页/引用计数即可达到这个理想页粒度下界；
它不等于当前native已支持任意逐head稀疏布局，但单独补视图/引用不是新
论文机制。真正待验证的残差只能是：在相同任务质量约束下联合选择S_i，
或已有共享视图之外仍存在显著的物化/压缩成本。TokenDance、AgentKV、
PolyKV及合理组合均应得到相同输入与资源。

下一步只有在自然应用提供真实长历史fork、保留集合/实际分配证据，并且
强组合仍有带成本的失效时，才继续设计。若共享视图即可消除、集合并集接近
原始全量、或峰值/暴露成本没有下降，就下调该候选。不能凭当前字节量承诺
收益，也不把任务质量或压缩存储大小当作workflow JCT。

**workload的两种语义分开。** 当前独立brief委派保留为有效应用。若后续
需要原生历史fork，应从AgentFork/R30等真实继承式接口选自然任务，同时
让native启用同样fork/COW，不把harness适配当贡献；不复制无用长prompt、
强制嵌套或增加无效child来制造共享。本轮未注册或启动这种新负载。
原两个问题线继续，第三条与旧三个机制仍撤下，主机制和总体目标未完成。

凭证：`experiments/reports/v1574_kv_workload_semantics_and_prior_scope_20261017.json`。
保存三份源报告hash、源码观察边界、文献版本、公开artifact可达性和反例假设；
没有新增测试或性能结果，原质量/终态资格与GPU状态不变。

### 4.104 Native全局与请求时间线：优先从真实时间成本筛选问题

按用户最新要求，暂停继续扩展KV回收总量或文献清单，把最新完整V79b
native策略诊断的负载、服务、排队、迁移和workflow尾部对齐。
完整分阶段表、来源哈希和图见
[V1575报告](../../experiments/reports/v1575_v79b_native_timeline_bottlenecks_20261017_zh.md)。
不使用V1454中另一个历史native字段替代V79b diagnostic字段。

156个root按Poisson到达，最后提交在41.296分钟；runner终点119.651分钟。
10–40分钟decode时间加权batch约47，GPU约97%，排队P50由0.515增至5.293秒。
40–50分钟batch仍47.182、排队P50却为12.994秒，H2D821.767GB、D2H923.520GB；
按实际decode记录输出938.623token/s，后一个窗口为1292.228，下降约27.36%。
这是不同阶段的实测差异，不是同轨迹加速上限。70分钟后队列基本为空，
batch从28逐步降到2.86，但多数窗口GPU仍97%–98%，不能称为等工具的空闲。

40718个请求均值分解：原生登记至首次dispatch2.932秒，后至串行worker
起点0.038秒，再至完成5.538秒，合计8.509秒。前端receipt至登记另为
0.090秒。这些均值可核对相加，不把独立P50拼成单个请求。
decode/prefill记录worker累计5910.805/1215.281秒，前者占82.946%；
记录外52.966秒不是CUDA空闲证明。尚不能分解为Attention/MoE/状态层kernel。
670次摘要及首后继占47.669%实际prefill token，但新摘要和改写prefix
不能直接当作可免重算。普通工具P50/P99仅62.092/3951.010ms。

4.61中的具体慢路径继续成立：40–50分钟57个正FULL Host短prefill样本
选批平均95.398ms，36个零Host样本4.016ms；正组全有选批内D2H、174批全在
选批结束前ACK，H2D全晚于选批结束。冻结engine已批量写回并保留同步完成。
这说明应分析恢复空间回收和服务供给，不能把H2D stream25.168秒当全程
恢复等待，也不能把选择区间全算为GPU stall或可省Python开销。普通异步
写回与合理并发须作为工程强对照，尚不构成workflow独有论文创新。

175个双成员JOIN的最后成员阶段P50为312.608秒，普通调用数P50为29；
95.915%处于模型客户端区间，工具3.353%。典型分支约两倍工作量，不能全
归为调度不公或只等最终报告。继续从反复模型服务/上下文处理及压力干扰
找实际问题，不恢复已撤下的第三条或旧三机制，也不直接选定异速/预取。

本次新增尾部核对：最后五个workflow全部属于原11个native-agent JCT排除案例。
它们各自最后600秒模型区间占86.57%–92.22%，工具4.74%–11.88%。
原145个native-agent JCT子集最后于6452.679秒完成，之后726.371秒仅
原子集排除任务存活。4.106确认这五项system_jct_eligible均为true；
排除原因是guard介入，不能据此称为无效系统测量或删除其终态时延。
全156项/原145项/原94个合格且resolved的duration均值分别为
1910.675/1677.260/1648.279秒。这些是原始分层描述，不删资源竞争历史、
不改变测量资格，measurement_complete=false及96 resolved/58 unresolved/
2 empty保留。不能从尾部删失构造对native的性能收益。

`scripts/summarize_v79b_native_timeline.py`完成来源/计数、同请求token总量、
到达生命周期、资格集合和最后五个workflow请求/工具闭合检查，输出独立
JSON及matplotlib PNG。本轮无运行时/harness/workload改动或GPU实验，
未新增单位测试；研究机制与总体性能目标未完成。

### 4.105 恢复选批被记入前一decode：从相邻请求证明提交耦合

本轮沿4.104的恢复高峰继续核查，未改运行时。新增
`scripts/audit_restore_decode_dispatch.py`读取已封存1962096278字节的
V79b原生audit前缀并校验SHA256，按同clock-domain的forward_iter -1/0/+1
关联现有选批和传输样本，再用全40718个原生请求生命周期验证保留同伴。
2400–3000秒93个短prefill样本中92个为decode→prefill→decode，
3000–3600秒114个中112个符合；3个非交替片段保留排除原因。
本轮没有按时间最近匹配或把缺失记录填零。

**对4.104的必要解释补充。** 204个有效片段的选批区间全部落在前一个
记录decode worker区间内，差额只有不到0.000001ms的浮点舍入。
记录worker的终点是原生结果观察器入口，overlap loop会在处理此前结果前
先选取并提交本次prefill。因此，5910.805秒/82.946%仍是正确的记录标签
统计，却不能解释成生成kernel耗时或直接指导Attention/MoE优化。
把选批时间再加在该decode区间上也会重复计数。

为避免仅比较不同batch大小，另保留“此前47个请求完整留在随后48个请求中，
新增1个是当前prefill请求”的严格集合：

| 2400–3000秒严格集合 | 正FULL Host | 已知零Host |
| --- | ---: | ---: |
| 样本数 | 35 | 20 |
| 前后decode dispatch间隔P50 | 159.120ms | 58.706ms |
| 当前选批P50 | 95.235ms | 2.337ms |
| 前一个记录decode worker P50 | 137.961ms | 39.989ms |
| 当前记录prefill worker P50 | 7.260ms | 8.635ms |
| 前decode平均上下文长度的P50 | 23004.553token | 21686.234token |

这些P50不可相加减作配对成本。按前一decode平均上下文再做2048-token
描述分桶，20480–22528区间正/零组10/11项，提交间隔P50为161.650/60.727ms；
22528–24576区间24/5项，为156.526/57.307ms。后一个600秒窗口的82个严格
零Host样本为61.890ms。上下文分桶支持现象不只来自batch人数差异，但
缺失KV、请求内容、时间和其他资源状态仍不相同，不构成反事实加速证明。

全部57个正Host有效片段的已有请求中，中位46个来自其他workflow，
涉及其他workflow数中位42；具体身份来自已有request ID而非文本相似。
因此成本不限于待恢复请求自身，已建立跨workflow提交耦合的观测对象。
代表片段forward108927中，选批95.235ms，2批336.364MB D2H在选批结束前
ACK，D2H stream累计78.069ms；382.915MB H2D stream11.098ms且ACK较晚。
原有47个请求继续出现在下一decode中，前后dispatch间隔166.282ms，
当前prefill记录区间7.260ms。该例不是GPU idle量或整体收益上限。

**没有补造GPU因果边界。** 冻结L2传输从scheduler stream事件之后启动，
而scheduler的WAR barrier可能只等共享metadata读取完成，并非总等整个
forward结束。不能据D2H等待就宣称此前GPU计算已经全部结束。
当前缺少前一forward的真实GPU完成时间及逐选批CPU独占阶段分解；
仍不能区分有多少copy/控制被覆盖、有多少延迟了有效计算。
V1577保存6份冻结运行源码与测试hash，scheduler哈希匹配实际ready回执。

**诊断成本也没有被排除。** `_emit`向有界队列非阻塞投递，编码和落盘由
后台writer执行；但快照和回调仍在scheduler线程。1/64 writer计时不是总
诊断成本，也不能据线程CPU直接量化GIL或GPU停顿。本轮没有无诊断、同轨迹
反事实，不能将V79b中所有退化外推为未加诊断native的性能缺陷。

**研究决策。** R19原始AttentionStore/CachedAttention已提出异步KV保存、
write buffer和逐层加载重叠；R10 EfficientAgent已有Host复用工作集、
容量条件写入及程序语义。两份原始来源本轮复核。因此，当前新增证据把
问题从“单次H2D能否提前”具体化为“恢复如何影响其他已有请求的提交”，但
不支持把改成异步写回或增加缓存预算直接立为动态workflow核心创新。
先以合理异步/Host准入为强工程对照，研究其后仍有多少动态依赖带来的
额外状态成本；没有这一残差前不重构旧三机制、不恢复共享workspace线、
不占用GPU或声称已获得OSDI级机制。

凭证：
`experiments/reports/v1576_v79b_restore_decode_dispatch_20261017.json`、
`experiments/reports/v1577_v79b_restore_dispatch_interpretation_20261017.json`。
8项CPU检查覆盖精确邻居/时钟、重复身份、已终态同伴、部分重叠和非法次序；
真实前缀读取、来源hash及额外workflow归属核对通过。原始质量和JCT资格
不变，整体研究与性能目标仍未完成。

### 4.106 迁移吞吐损失：小页写回、提交阻塞与有效工作负载

本轮按用户问题新增CPU审计器`scripts/audit_native_transfer_asymmetry.py`，
读取同一V79b全部完成传输、原始summary、最后五项sandbox audit及官方评分，
校验command ID唯一、双池字节守恒与完整参考总量。不启动GPU或修改运行时。
凭证为
[V1578传输与尾部审计](../../experiments/reports/v1578_v79b_transfer_asymmetry_and_tail_20261017.json)、
[V1579设计与负载筛选](../../experiments/reports/v1579_migration_design_workload_screen_20261017.json)。

**D2H并非只多传3.3倍字节。**

| 完成传输 | D2H | H2D |
| --- | ---: | ---: |
| 总字节，十进制GB | 2882.386 | 879.088 |
| 批数 | 44845 | 1839 |
| 平均每批MB | 64.274 | 478.025 |
| stream累计秒 | 442.431 | 25.168 |
| 字节/stream时间，GB/s | 6.515 | 34.929 |

字节比3.279、批次比24.386、stream时间比17.579。单批P50反而是
D2H4.098ms/H2D13.151ms，所以“D2H每批都更慢”也不成立。纯FULL D2H
24669批/1072.189GB/297.544秒，占全部D2H stream约67.25%；混合池
D2H为1810.197GB/144.887秒。不能将慢写回全部归因于Mamba状态。
在256–512MB这个粗分桶里，混合池D2H/H2D的stream P50仍为
50.787/11.340ms，但pool构成、资源竞争和实际前缀不配对，不能直接作因果差值。

这些速率是有效负载除以事件区间，并非PCIe单方向峰值。区间可能含布局转换、
copy、两事件之间的主机提交空档和并发争用；stream、submit→ACK、worker和
JCT相互重叠，不相加或直接从JCT扣除。诊断本身也仍有调度线程成本。

**更具体的源码线索是内部小拷贝，而不只是外部batch数量。** 实际启动为
`page_size=1/kv_cache_dtype=bfloat16/hicache_io_backend=kernel/
hicache_mem_layout=page_first`。10个FULL层、2个KV head、head_dim256，
每个Host K页或V页只有10240字节。冻结引擎
`pool_host/mha.py:439`的正常staged路径先在GPU重排，再由
`kernels/jit/csrc/kvcacheio/staged_write_back.cuh:64`逐tensor、逐页
调用`cudaMemcpyAsync`；batch-copy尝试的单页阈值为128KiB，并且当前
先建立指针向量再检查阈值。H2D走不同的逐层kernel，不是这一循环的反向执行。

当前trace没有内部branch/copy计数。若全部FULL都使用该正常staged分支，
72699039个槽对应145398078次K/V页copy；这是条件推导，不能写成实测。
64页staging chunk在源/Host目标都连续时可从128次小copy合并为2次；
这只是操作数例子，不是64倍吞吐承诺。现有32-node外部批量写回不会自动
合并内部逐页copy；不能把外部batch优化视为此问题已解决。

**设计应先改善必要迁移的执行，再测workflow信息的额外收益。**

1. 保留逻辑page和精确KV值，按源staging与Host目标的共同连续区间合并FULL
   写回；碎片化区间保留正确的scatter路径。评估chunk及物理布局，避免仅
   增大外部batch仍触发相同的小copy。该项是通用传输路径修复。
2. 将未完成D2H及其待释放页显式留在pending状态，保持引用保护，仅在真实
   完成后增加可用容量；尚待恢复的新请求等待容量与数据依赖，已有驻留请求
   可继续提交。不能删除同步后提前复用源页，也不能无限推迟待恢复请求。
   该项是通用异步回收基线，先测是否减小4.105的跨workflow提交间隙。
3. 在已有WAIT_TOOL/JOIN/终态事件处，选择确有回收需要的增量FULL提前写回；
   已有有效Host副本不重写，确认失去用途的child私有状态可直接释放。
   PREPARE及生命周期路径已有相关能力，不把它们重新声称为新机制。
   Mamba等可变状态继续按必要版本与依赖恢复，不能复制仍在变化的状态充数。

完整实验至少分原native、含通用传输/异步修复的native、额外workflow策略
三个层次；同资源、任务语义、质量标准和到达流。固定decode前后请求的
159.120/58.706ms关联差异支持诊断价值，但不是可减100ms的逐次证明。
主指标为持续压力段输出吞吐、已有驻留请求的提交间隙、平均JCT、有效恢复/
重复迁移及额外控制成本。CachedAttention/AttentionStore已研究异步保存，
EfficientAgent已有Host工作集和语义准入；单纯合并copy或异步化不足以构成
动态agent论文贡献。整体研究机制和可达加速仍未确定。

**最后五个任务是guard介入，不是系统数据无效。** 五项原字段均为
`completed/natural_terminal=true/system_jct_eligible=true`，
`native_agent_jct_eligible=false`，唯一排除原因是
`guard_intervened_completion`。每项有一个child在graph_step2017触发
2048步预算的32步收尾预留；LangGraph配置的recursion_limit9999是另一项，
不能混为一谈。日志记有403次model call，guard移除10个普通工具、保留0个，
插入强制completion指令。未触发14400秒上限，也没有输出token耗尽证据。

| Workflow | 触发child角色 | 触发时elapsed秒 | 官方解题结果 |
| --- | --- | ---: | --- |
| django-12193 | repository-explorer | 4587.141 | unresolved |
| django-12708 | general-purpose | 3497.796 | unresolved |
| django-12406 | test-analyst | 3541.159 | unresolved |
| django-11265 | general-purpose | 4861.625 | resolved |
| pytest-6202 | test-analyst | 4918.743 | unresolved |

`natural_terminal`只说明最后响应/终止形态，不否认之前的干预。用于分析无
guard自然轨迹或训练终态标签时分层合理；把这五项称为“不合格任务”并从
系统吞吐、全体到达时延或资源历史删除不合理。完整固定预算实验保留其实际
终态时间，另外报告guard比例及正确性；若声称无界自然完成，则将介入作为
干预/删失处理。原145/94子集只作附加描述，不是无偏全体收益。原始flag、
评分、measurement_complete=false及退出码均不改，本轮未修改guard配置。

**候选负载采用固定语料多轮检索，尚未证明迁移压力。**
已冻结BrowseComp-Plus提交`046949032b0328319cc9a02663a759ec601d9402`的
README与BM25 searcher；本地Lucene/Pyserini支持search与get_document。
任务、语料和答案/证据评测可固定，避免把实时网页变化混入系统比较。
并行结构参考OpenDeepResearch提交
`1b7d2e80db9faa586165c60e09096dbbfd483a64`：supervisor动态调用
ConductResearch，多researcher通过asyncio.gather执行、独立累积工具结果，
返回后汇总并可继续调查。该参考仓库已archive，只作冻结结构参考；
其异常分支含`is_token_limit_exceeded(...) or True`，不可原样吞掉异常后
当成功。优先复用当前有埋点的parent/child harness接固定本地检索。
这是基于公开任务的多agent变体，不冒充原benchmark已验证的原生配置。

原BrowseComp-Plus主评测对工具内容有长度限制，OpenDeepResearch也会压缩；
不能由多researcher直接推断有长上下文或频繁迁移，更不能为制造压力任意
塞长文档或重复调用。候选须在native上实测连续多个窗口的有用Host→HBM
恢复，以及按共享页去重、分池的可复用工作集超过HBM而仍能由两级容量承载。
仅D2H备份多或大量丢失后重算不算满足。固定seed在线随机到达应覆盖预定
测量时段；只缩短间隔会抬高峰值，未必消除低压尾部，应保证足够真实任务
持续到达。现有V79b的40–50分钟仍是已经观察到的迁移高压案例。

公开源码hash和commit、来源路径及未执行状态记录于V1579。本轮没有下载
语料/索引、运行候选负载或GPU实验，也未新增运行时单元测试。CPU审计的
传输总量、字段、输入及源码hash核对通过；设计收益与论文新颖性保持待证。

### 4.107 OSDI候选筛选：用成本模型和强对照淘汰直接变体

用户明确要求继续寻找系统idea、逐项做初步可行性论证，低可行性则换下一项，
并设立/goal。已建立active目标：至少一个保留候选须同时有具体成本证据、
机制与正确性边界、CPU可行性验证、强对照及可证伪实验设计；没有候选通过时
继续寻找，不能把完成一轮筛选当成目标完成。上一轮有新增CPU审计、来源核对
及改变候选优先级的结果，属于实际进展，不是等待GPU或无进展阻塞。

**候选一：将attention留在Host，避免整轮恢复。** 新增
`scripts/audit_host_attention_candidate.py`，使用全部40718个已完成原生请求，
按唯一request ID、明确的decode/prefill计数和原始workflow身份核对。每项
恰为一次prefill首token加D次单token decode；假设整次prefill只读一遍旧
Host前缀，之后每个decode独立扫描该部分。V表示原Host命中FULL字节，
条件模型为Host扫描服务量`V*(1+D)/B_host`，一次恢复为`V/B_restore`。
比较只给出`1+D <= B_host/B_restore`这个字节服务量条件。

| V79b集合 | 请求数 | 输出P50 | 原FULL Host输入GB | 条件逻辑扫描TB | 字节加权扫描倍数 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 所有Host命中请求 | 2074 | 79 | 611.358 | 87.424 | 142.999 |
| 其中uncached输入小于512 | 1304 | 74 | 410.722 | 52.235 | 127.179 |
| 其中官方resolved任务 | 1208 | 78 | 341.280 | 47.442 | 139.012 |

假设Host有效扫描带宽为恢复带宽16倍，只有4项/0.220%的Host输入字节满足
该条件；即使是假设32倍，也只有18项/0.982%。这些是假设比值，不是当前
机器带宽。短prefill仍跟着几十次历史访问，不能从“输入增量小”直接选择
整轮Host执行。按此具体理由下调候选，不为它移植运行时或开启GPU。

这不是NEO的性能反证。NEO/R72已经将部分attention移至CPU并与GPU非对称
批次重叠、按负载选择执行位置，须作为直接强对照。CPU缓存、跨请求公共
prefix合批可能减少物理读取；增加GPU可用容量也可能带来收益。表中逻辑量
没有模拟这些能力，未计attention FLOPs、新KV、query/result流量或混合状态。
V与完成H2D的FULL总量闭合，不建立逐传输唯一消费关系，也不证明无读放大。
未来长度仅作事后筛选，不供在线调度器使用。

**另外三项继续收紧。**

| 候选 | 本轮核对的直接已有能力 | 决策 |
| --- | --- | --- |
| 精确压缩KV以减少迁移 | SplitZip/R73的逐位精确保真、GPU编码与传输路径 | 可作工程基线，不作为独立新主机制；当前trace没有KV值，不能杜撰压缩率 |
| GPU自主推进以隔离CPU控制停顿 | Blink/R74的GPU常驻批处理、调度及KV管理，配合SmartNIC入口 | 不把一般GPU控制平面重新作为主idea；部署设备不同本身不足以证明差异 |
| 压缩不同分支的共有历史而不复制 | Zipage/R75明确讨论压缩破坏共享及目标页不足时的抢占；DiffKV/R76已有GPU并行compaction | 静态共享view或普通压缩allocator不足以选定主线，需要额外的实际转换/执行成本 |

最后一项还有一个必须保留的反例：若parent仍需要完整prefix，它也是消费者，
所有保留集合的并集仍是整个prefix。只给child剪枝并不能据此保证整体物理
存储小于原生共享一份的大小。若将parent原始状态放入Host，则必须重新计入
该层容量、后续恢复及child动态保留集合的成本，不能漏掉parent后再宣布收益。
这不是撤销所有共享压缩研究，而是继续要求超出固定集合共享页下界的具体问题。
当前没有候选通过完整立项条件，也没有宣称达到OSDI新颖性。

**公开样本筛选未被用来制造共享证据。** 固定
`callanjfox/kv-cache-tester`提交`94f6046dfd9c0c8bc0cdb24a5aa6579bac8669bf`，
按文件序号先选0001–0003再读取。三项合计469个root请求和14个child请求，
最大观察深度1；root输入P50分别126004、43773、126011 token。
三项`hash_id_scope`都是local，不能横跨context比较编号。第三项两个
child使用Haiku而parent使用Opus，首输入9526/9473 token也短于当时
parent的16564/103140。它们支持“真实应用可有更长context”，不支持当前
所需的同模型长历史KV继承、迁移量或本项目任务质量结论。样本非总体统计。
没有执行其合成文本回放，更没有用回放替代用户要求的在线随机到达实验。

后续继续找会改变实际状态量或迁移/执行工作的动态分支反例，并同时给
原生共享、合理Host策略和上述强实现相同信息与资源。若剩下的只有已有
机制的组合或参数不足，停止该候选而不是扩大其实现。新workload须先
证明自然需求及数据身份，不能强制无用fork、复制长prompt来制造现象。

本轮8项新CPU检查通过，覆盖字节加权、零机会、重复身份、未知计数和不同
decode语义；真实报告所有计数/输入hash校验通过。证据见
[V1580](../../experiments/reports/v1580_v79b_host_attention_candidate_20261017.json)
及[V1583](../../experiments/reports/v1583_research_screen_and_page_scope_20261017.json)。
文献以arXiv Atom题名/版本与原文交叉核对；初始错误编号下载未作证据。
未修改harness、模型、运行时、guard或启动GPU，/goal保持active。

### 4.108 page_size=1：合法默认与实际DMA粒度分别判断

针对用户对10KiB页的疑问，核对实际server.log、冻结native启动链及engine
源码。`launch_qwen35_native_v0520.sh`和native regime probe未传
`--page-size`，引擎`arg_groups/overrides.py:_page_size_default`的一般
非MUSA默认是1。本轮实际为FA3、BF16、extra_buffer、track_interval256、
chunked_prefill4096、无推测解码、kernel/page_first HiCache。它不是Qwen3.5
永远只能用1的证明，也不能仅据小copy现象直接判断配置错误。

`no_buffer`确实要求page_size=1，但本轮未用该模式。extra_buffer上层
校验要求track_interval能被page_size整除，Mamba chunk与page也须相容。
16和64满足当前256/4096的整除条件；FA3源码有page_size>1的页表路径。
这些仅是上层支持线索，不是所有kernel、缓存命中、恢复与生成正确性已验收。
本轮相关校验/FA3文件与冻结53020ce3树一致，未用另一个引擎解释原始实验。

page_size的单位是token。固定当前模型10个FULL层、2个KV head、head_dim256、
BF16，每个K或V的全层token跨度是10240 bytes：

| page_size | 单K或V页 | 是否达到128KiB尺寸门槛 | 当前64页staging的K+V总量 |
| --- | ---: | --- | ---: |
| 1 | 10KiB | 否 | 1.25MiB |
| 16 | 160KiB | 是 | 20MiB |
| 64 | 640KiB | 是 | 80MiB |

这是源码条件下的几何计算，不是GPU结果。达到尺寸门槛仍需要相应CUDA/
driver/API支持，不能据表宣称一定执行batch-copy。增大页会同时改变分配/
前缀匹配边界、尾部padding、物理布局和staging容量，可能影响cache复用。
反过来，保留1-token逻辑页也可以合并源/目标连续的DMA区间；“细粒度管理”
不要求“每个细页各发一次copy”。连续性必须由物理索引验证，不能假定整棵
radix树连续。当前trace未记录所有内部copy分支及索引，不能补造其比例。

研究判断：page_size=1是合法默认，与逐页小copy路径的组合可能不优，应列为
强native工程对照，不上升为OSDI机制。后续确需GPU验证时，在相同资源与任务
下比较合法的1/16/64，并单列page_size=1加DMA连续区间合并，检查吞吐、
写回成本、cache复用和内存占用。没有仅凭阈值把当前配置改为16/64，也没有
启动参数测试。V1583保存配置、源码hash及条件几何；/goal继续研究筛选。

### 4.109 跨上下文精确残差：可压缩性成立，系统收益与主创新未成立

**筛选顺序和研究问题。** 先查“独立分支对同一历史保留不同稀疏集合”的
执行成本。FlashInfer/R77已有可编程稀疏页表、共享/组合KV格式及不搬动KV
的执行方式；Hydragen等共享attention继续作对照。因此普通任意mask共享、
固定集合引用计数与共享attention分解不独立立项，没有为直接变体写原型。

随后检查一个不同问题：不同因果历史中的相同内容，不能直接互换KV，但
其真实张量是否仍存在可利用的精确表示相关性？若成立，可保存一个参考值
及目标逐位残差，恢复时得到目标本来的KV，不能拿参考KV代替目标KV计算。
这区分了计算复用与物理表示共享，不预设它是新贡献或当前负载适配。

**CPU实现与来源。** 新增`screen_cross_context_kv_codec.py`和
`audit_cross_context_codec_scope.py`，固定Qwen2.5-Coder-0.5B-Instruct的
`ea3f2471cf1b1f0db85067f1ef93848e38e88c25`版本。实际988097824-byte权重
SHA256与固定版本镜像LFS元数据一致；不加载35B CPU模型，不使用GPU。
BF16、eager attention、单CPU线程，读取所有24层的实际K/V张量。

V1584先用三份冻结引擎代码、64-token不同指令历史、每份256个相同正文
token做控制。V1586进一步使用V1553中已通过原始callback表示校验的真实
工具正文，预先按时间取最早三份跨同workflow不同agent出现且足够长的正文。
三份都来自xarray-7229，不是跨任务代表样本。每份取256 token，分别放在
四个512-token不同指令历史后；另测增加8个前置token的位置偏移。
前置历史由重复指令构造，**没有重建实际agent对话，也不是当前模型的KV**。

每个layer/K/V独立比较Zstd(level3)原始word、按byte分面及BF16符号/尾数/
指数重排。参考编码加入可逆XOR和uint16模差，均允许回退独立编码。每块
包含10-byte头，整组计入首个已计算context的参考副本，不能用未来张量。
没有浮点减法的可逆性假设，没有逆RoPE。增加保留head/channel而打乱参考
token顺序的对照，检验收益是否需要实际位置对应。

| V1586计入参考副本 | 独立无损压缩 | 对齐参考+残差 | 少占容量 | 打乱参考后少占容量 |
| --- | ---: | ---: | ---: | ---: |
| 同位置，参考+3个目标 | 27.841008 MB | 22.029745 MB | 20.873% | 0% |
| 偏移8 token，参考+1个目标 | 13.910311 MB | 12.612674 MB | 9.329% | 0% |
| 同位置，排除第0层 | 27.191904 MB | 21.866929 MB | 19.583% | 0% |
| 偏移，排除第0层 | 13.585783 MB | 12.364413 MB | 8.990% | 0% |

随后V1588增加可逆bit-plane重排，双方都可选用该codec，不能只增强残差
一侧。计参考后的独立/残差容量分别为27.323177/21.903778MB（同位置）
和13.665028/12.486707MB（偏移），额外节省收敛为19.834%/8.623%。
排除第0层后仍为18.494%/8.261%。打乱参考的微小节省仅0.0027%/0.0019%；
新报告14400次张量恢复逐位一致，8项检查重新覆盖新增layout。V1586原报告
不改，后续结论以更强V1588基线为准；它仍不是全部最强codec的复现。

V1590进一步对齐参考摊销：两侧均为每份正文1个参考+1个目标，同位置组
独立/残差为13.669285/11.797242MB，节省13.695%；增加8个前置token组
仍为8.623%。排除第0层后分别为12.814%/8.261%。此前19.834%与8.623%
包含不同副本数，不能把差距全部归因于位置；新增前置token也改变因果
历史，所以本实验始终不隔离纯RoPE效应。

同位置与偏移组各自计参考，不能相加。第一层排除项避免将最初投影中与
历史无关的值当作全部发现。Zstd+字段重排不是SplitZip GPU kernel复现，
最短codec选择仍是乐观搜索；参考ID、检索、对齐、生命周期、GPU编码、
消费者解码及额外驻留成本尚未建模。10800次张量codec恢复逐位一致，
8项CPU检查覆盖全部65536种BF16 bit pattern、NaN payload、模回绕、
错误引用、回退与跨workflow误合并；不据此宣称模型最终输出逐位一致。

**当前真实需求覆盖不够支持直接实施。** 对V79b的39574项工具观察，
19559项有已验证正文。按同workflow、相同正文、不同invocation分组后，
1066组涉及154个workflow；每个invocation相同正文只计一次，额外正文为
2512379 UTF8 bytes。用实际Qwen3.5 tokenizer独立编码这些正文，额外
743436 token，其中read_file为639442。全部已验证正文出现量为5002736
token。这不是所有请求历史的覆盖分母，不能外推到未知正文。

若仅按20480 bytes/FULL token换算，743436 token对应15.225569280GB，
但这既不是实际物理KV容量，也不是迁移量或可省字节上下界：chat渲染边界、
确切token位置、已共享prefix、后续驻留及实际消费均没有证明。第一个不同
agent再观察同正文的间隔P50为525.467秒、P90为2258.905秒；它也不是已测
参考生命周期。这削弱了“参考已经在GPU、多个目标马上同时使用”的假设。
不能把压缩比例直接乘全程TB传输，不能为获得相关性强迫所有child读整份文件。

**拟议机制与正确性边界，仅用于下一轮证伪，不是实现状态。**

1. 依据相同模型/格式和真实token片段建立候选参考索引，但不同因果历史仍
   各自计算目标KV；只复用表示。逐块比较独立压缩与参考残差，差则回退。
   不要求agent改输出、跳工具或修改任务语义。
2. 参考必须已计算、不可变且可定位。先限定单级引用，显式记录依赖计数；
   child结束不能释放仍被引用的参考。将参考占用、重新独立编码/解绑成本、
   两级容量与传输同时计入，不能把结束child的参考永远视为免费。
3. 原生attention消费前恢复目标原始bits。若目标设备没有参考，比较
   `传参考+传残差+解码`与独立恢复；单目标不划算时直接回退。CPU解码后
   再传完整KV不会自动减少H2D，不能据当前Python原型宣称传输收益；
   GPU解码/分层消费仅在成本与兼容性验证后才考虑。

**强对照及明确放弃条件。** SplitZip/R73及强独立无损codec是直接基线；
KVComm/R71等跨上下文近似复用与ResidualKV/R78的量化残差须按相同质量
约束分层比较，不将“近似”视为已失效。MobiLoRA/R79已经有同prompt、
不同adapter的anchor/delta池、浮点误差有界压缩和上下文感知回收；
XORCache/R80已经有跨line可逆XOR、相似值选择及可恢复性不变量。
因此“anchor+delta+引用计数”本身没有新颖性，不因它们应用域不同排除适配。
此前临时提及LoRA论文但尚未核实时撤回了依据；本轮后续已取得并读到
MobiLoRA正式ACL原文，只有核实后的机制用于上述判断。

后续先CPU检验更长且多样的因果历史、位置和更强独立codec，随后要求实际
目标模型张量与在线参考可用证据。若相关性只在短控制历史中存在，或相同
信息下通用参考选择已取得全部收益，则放弃主创新；若额外参考搬运、长时间
保留和解码抵消节省，则放弃系统实施。自然多agent长文档/代码评审可以作为
候选负载，但须先证明实际内容重叠、状态同时存活和真实需要，不强制复制
无用长输入、不调整任务质量来制造数据，未选择或运行新负载。

本轮保留的是可压缩性观察，**不是通过OSDI立项或GPU实验门槛的系统**。
当前负载下未证明足够收益，因此不接入运行时，不为Python codec占用GPU。
下一步优先测成本覆盖/参考可用性；证据仍弱就换候选。/goal保持active，
不因新增原型、来源和测试而标完成。

报告为[V1584](../../experiments/reports/v1584_cross_context_kv_codec_cpu_20261017.json)、
[V1586](../../experiments/reports/v1586_observed_content_kv_codec_cpu_20261017.json)、
[V1587](../../experiments/reports/v1587_cross_context_codec_scope_20261017.json)；
增强基线与更新核对见
[V1588](../../experiments/reports/v1588_bitplane_control_kv_codec_cpu_20261017.json)、
[V1589](../../experiments/reports/v1589_bitplane_control_codec_scope_20261017.json)；
公平单目标分组见
[V1590](../../experiments/reports/v1590_matched_reference_codec_scope_20261017.json)。
原始文献与固定权重元数据归档于
`experiments/reports/v1585_codec_and_shared_layout_sources_20261017/`；
本轮arXiv Atom请求429，身份以固定版本摘要页、原始正文及正式出版PDF核对，
不声称该次Atom身份检查成功。未改冻结轨迹、运行时、harness、guard或配置。

### 4.110 真实历史与冷参考：容量缩小不等于恢复收益

**本轮是上轮的实质进展，不是等待GPU。** V1591–V1593替换掉重复短指令，
使用真实留存消息，并对参考传输收费；V1595将跨agent正文相等与invocation
生命周期关联。新证据改变实施决策：保留相关性观察，当前负载不以跨上下文
残差恢复作为独立系统主机制，不为它启动GPU或继续扩大原型。整体/goal
仍须寻找至少一个通过研究门槛的候选，不能把本轮筛选当完成。

**先核查实际输入证据缺口。** 冻结`deepagents_adapter.py`的`llm_submit`
只输出message_count、prompt_chars及含model contract的semantic hash；
冻结`v0520_native_telemetry.py`虽在内部接收input_ids，写出的context
probe仅含公共前缀计数等字段。当前已登记trace没有完整原生输入token序列。
工具正文hash匹配、后来有模型请求或最终trajectory保留消息，均不能独立
证明某段正文在当时完整进入目标请求、位于哪个token区间或形成哪些KV页。
不把这个缺口当零机会，也不通过猜测补造实际GPU状态。

新增`build_kv_codec_history_controls.py`，按最早workflow_start及最早足够长
的留存assistant边界，选择4个不同workflow；排除正文样本所属xarray-7229。
实际为xarray-7233、xarray-7393、pylint-4551及pylint-4661。将实际留存
root消息按0.5B模型模板渲染，固定取2048-token后缀，再与原三份256-token
正文组合。保留原始消息hash、选取边界、token ID/hash及执行脚本快照；
V1593从密封trajectory重新渲染，4项逐token一致。

这些是实际对话材料构造的控制输入，不是原始native prompt回放。原系统/
工具schema、middleware状态、compaction前历史及原始位置仍未还原；
2048长度、不同材料与模板变化同时存在，不将结果差异只归因于长度。

| V1592：包含参考、独立基线含bit-plane | 独立压缩MB | 参考+残差MB | 少占容量 |
| --- | ---: | ---: | ---: |
| 1参考+3目标，同位置 | 27.233666 | 22.400358 | 17.748% |
| 公平1参考+1目标，同位置 | 13.616661 | 11.833538 | 13.095% |
| 公平1参考+1目标，多8个前置token | 13.628293 | 12.651844 | 7.165% |

排除第0层后的公平两组仍为12.197%/6.770%。打乱参考token对应后，全部层
两组仅少0.0050%/0.0067%；14400次codec恢复逐位相同。说明小模型的
跨上下文可压缩性并未在这个更强控制中消失，但不能据此宣称本地35B模型、
完整原始对话或端到端速度改善，也没有复现SplitZip/MobiLoRA GPU kernels。

**给不在目标tier的参考收费。** 前表包含仍需保留的参考副本，适合描述整组
存储。若只有一个目标要恢复、参考本身不执行且目标设备无参考，必须另传
参考。V1593逐layer/K/V仅对实际选用残差的块收参考费；独立回退块不收。

| 样本目标 | 独立目标MB | 冷参考+目标MB | 比独立多传 | 等成本目标字节持平数 |
| --- | ---: | ---: | ---: | ---: |
| context_1 | 6.809611 | 11.833538 | 73.777% | 4 |
| context_2 | 6.809515 | 12.136215 | 78.224% | 5 |
| context_3 | 6.807490 | 12.044705 | 76.933% | 5 |
| 增加8个前置token | 6.821243 | 12.651844 | 85.477% | 7 |

这是重复同一采样字节轮廓、共享一次冷参考的假设摊销数，不是实际并发、
其他正文的预测或端到端盈亏阈值。额外元数据、解码、驻留及传输调度尚未
收费，所以即使字节持平也不证明执行收益。四项单冷目标均应回退独立恢复；
比原始未压缩FULL目标也多传约25%–34%，未计Mamba，不外推整次请求。
参考已经在目标设备或参考也确实要执行，是不同条件，不能用此表全部否定。

**真实动态workflow是否自然提供活跃参考？** 新增
`audit_shared_body_lifetimes.py`，完整核对156份密封事件源的1202个invocation，
全部有已观察return/cancel。每个正文、每个invocation只取第一次观察；
参考须严格早于目标，选择任意一个先前观察者，不能只看第一份或回填未来。

| 额外不同context观察时的先前观察者 | 次数 | 独立正文token出现量 |
| --- | ---: | ---: |
| 全部已终态 | 995 | 513250 |
| 至少一个尚未终态 | 437 | 230186 |
| 生命周期未知/首次同时出现 | 0 | 0 |
| 合计 | 1432 | 743436 |

次数、UTF8字节和token均与V1593的额外正文出现量闭合。约69%的出现量没有
仍未终态的先前观察者，因此不能把参考保留当作总是顺带发生。这里**终态
不等于物理驱逐，未终态不等于KV仍完整驻留**；来源可能已压缩/卸载，也
可能结束后仍留原生缓存。表只是程序生命周期条件，不是可用HBM参考命中率、
已计算KV或预取收益上下界。不能将这张表与小模型压缩率相乘造可省GB。

**研究决策。** 当前证据同时存在表示相关性、冷参考额外成本与有限的自然
共同活跃支持。加上4.109的强已有方法，尚不足以为当前负载选择OSDI系统
主线，停止为该候选接入运行时。保留CPU原型和反例；只有另有自然工作负载
证明同一模型/相同内容的多个目标在同一tier真正重叠，且强独立/通用参考
codec后的净收益与机制差异仍在，才重新打开。不强制无用重复阅读或延长
agent生命周期来制造摊销，也不因没有原始输入而擅自启动GPU补采。

新增3项历史选择、3项冷参考收费、4项生命周期CPU检查，原8项codec检查
保持通过。历史工具初版误按Transformers BatchEncoding字典长度判断token数，
已主动终止、修正并拒绝非平坦返回；该失败进程没有结果用于证据。
后续显式return_dict=False，词表长度只取一次，避免每token重复查询。
这些是研究工具修正，不是serving优化或模型guard。

证据：[V1591](../../experiments/reports/v1591_retained_history_codec_controls_20261017.json)、
[V1592](../../experiments/reports/v1592_retained_history_kv_codec_cpu_20261017.json)、
[V1593](../../experiments/reports/v1593_retained_history_codec_scope_20261017.json)、
[V1595](../../experiments/reports/v1595_v79b_shared_body_lifetimes_20261017.json)。
执行版本快照保存在同目录的V1591/V1592 `.auditor.py`，与报告源码hash匹配。
未修改冻结实验、模型、任务语义、runtime、guard或启动GPU。

### 4.111 状态更新日志候选：已有DeltaLog关键机制，不重复实现

本轮还筛选了“用小的状态更新记录代替反复保存完整Gated DeltaNet状态”：
其自然理由是线性attention递推有低秩更新，完整状态重复写入可能更贵。
读取固定v1的DeltaLog/R81后，该关键primitive已有直接对照：它保存低秩
update log与稀疏checkpoint，按需物化/恢复，覆盖推测解码rollback和
prefix-cache恢复，并讨论重物化间隔与实现。没有将代数等价泛称为浮点
逐位恢复，也不把其原文加速结果当本项目实测。

该文的GPU状态日志不等于本项目Host分层迁移已被复现；但仅将已有日志
搬到Host或绑定agent事件，不足以成为新主机制。此直接变体低新颖性，
因此不再写原型、不占GPU、不恢复已撤下的Mamba专属Host准入主线。
后续仍须找新的可测系统问题，而不是更换同一已有primitive的应用标签。

正式标题、arXiv版本与原文归档于
`experiments/reports/v1594_state_log_sources_20261017/`，hash记入V1593。
本轮继续取得了新来源、CPU结果和改变行动的否定证据；/goal保持active，
没有通过完整立项条件的候选，不能标complete或因研究困难标blocked。

### 4.112 跳过已撤下候选，筛选Host内存竞争与跨资源已有控制

按用户最新要求，不再继续跨上下文残差候选的区别论证，4.110的撤出决定
保持。下面是独立的研究筛选，不恢复其实现，也不将旧CPU结果改称系统收益。

**另一项待核查问题。** 同机CPU工具与Host KV可能使用同一DRAM资源，
动态分叉可能同时增加工具进程和存活历史。只有实际发生内存竞争，并且存在
超出合理资源隔离/缓存配置的额外代价时，才值得考虑相应系统机制。
当前不能从两类分配共存直接推出竞争或正反馈。

**已有证据与缺口。** 本轮重读V79b的V1391原始NUMA观察、实际容量、GPU
采样表头及冻结Host池源码，不运行新工具负载或GPU：

| 已核对对象 | 能得出的结论 | 不能得出的结论 |
| --- | --- | --- |
| 原生容量普查 | Host FULL为160000020480 bytes，Mamba为40050032640 bytes，合计200050053120 bytes | 全机可用DRAM不足或工具内存被挤出 |
| 单时点scheduler NUMA观察 | N1/N0驻留分别为160767922176/42947014656 bytes | 工具RSS、持续内存压力、远端访问带宽或运行期page fault |
| GPU采样 | 表头仅有GPU显存、利用率与功耗字段 | Host内存或CPU工具争用时序 |
| 冻结`HostKVCache` | 构造时按预算分配`kv_buffer`；普通`free`/`clear`更新逻辑空闲槽 | 逻辑槽释放就已归还物理DRAM给工具，或`VmLck=0`证明CUDA页未固定 |

V1391本来就声明不是带宽或pinned-page证明，不能改变其范围。该观察没有
工具内存和系统压力的同步序列；缺失证据不等于已经证明竞争不存在。当前
不以降低主存容量、制造内存压力或加大无用工具任务来补造这一问题。

**补齐直接强对照。** MARS/R82的固定v2正文已有CPU压力与GPU KV压力反馈、
阶段感知准入、进度反馈队列和KV预算下的执行/驻留协调。因此“动态agent
在CPU工具与GPU推理之间交替，联合控制两类压力”本身不能作为新主张。
文中的pinned session是其GPU驻留策略术语，不能据此认定其实现了CUDA
Host页注册/弹性回收；同样，也不能只因部署或存储层不同就宣称新颖性。
本轮没有复现MARS或沿用其速度数字。

**研究决定。** Host DRAM竞争暂不立项，不为缺少瓶颈证据的问题增加新
控制器；跨资源反馈研究继续，但必须给出MARS、Agentix、Murakkab及合理
组合仍未处理的具体成本。当前没有保留候选通过全部研究门槛，/goal保持
active。筛选只涉及文档和来源归档，无运行时、任务语义、实验配置或GPU
操作，也不为文档变更新增单位测试。

凭证：
[V1596筛选记录](../../experiments/reports/v1596_host_tool_memory_screen_20261017.json)。
MARS v2原文保存于同目录`v1596_host_tool_memory_screen_sources_20261017/`。
记录包含V1391、观察器、冻结Host池源码及论文原文的SHA256，便于按原始
输入核查；不是新测量、策略回放或服务加速结果。

### 4.113 共享KV联合选择：有并集缩小，但不等于少读或新多agent机制

本轮继续active研究目标，沿4.103尚未验证的“共同决定保留集合”做CPU
筛选。区别于已撤下的跨上下文残差编码：这里使用确实相同的token前缀，
研究多个reader的近似保留集合，不继续前一候选的区别讨论或运行时接线。

**先固定问题与强对照。** 设当前reader的保留集合为`S_i`，共享存储量由
`|union S_i|`决定，但逻辑读取工作仍随`sum |S_i|`变化。两者都不自动等于
实测HBM流量。普通共享页已消除复制，新的选择若缩小并集，却让每个reader
保留更多页，可能增加执行工作。当前reader与观测权重相同时，这一静态选择
问题也出现在普通多query服务中，不能只添加agent标签就宣称新颖性。

新增核对KVzip/R83正式PDF§2–3及附录：已有可重复使用的query-agnostic
压缩和重建评分，未来query不可见并非空白。EchoPress/R85本轮只核对原始
摘要中的虚拟重建范围，不称复现。LoSA/R84原文已有query局部选择的并集
膨胀与访问局部性机制，但针对块扩散模型；不能直接等同本项目自回归、多
请求或跨turn实现。FlashInfer、TokenDance、PolyKV及合理组合仍是强对照。

**自然输入与模型边界。** 从QASPER/R86官方v0.3归档按原始顺序选择前两篇
满足条件的完整论文：各有3个不同且有可回答标注的问题，全部完整输入不超过
4096 token，不截正文或按实验效果选问题。论文ID为1811.00383、1605.06083。
构造公共system与论文、末尾不同问题的受控输入后，逐token核对实际公共
前缀为3140/3061 token；它们不是V79b原始输入，也不是实际动态fork证据。

固定`Qwen2.5-Coder-0.5B-Instruct`原revision，权重SHA256与已保存LFS元数据
一致。CPU/BF16/eager、单线程，对全部24层及2个KV head取问题末尾最多32
个位置的实际attention，按GQA组内query head及位置平均。本文99%仅指
保留这些**已观测平均权重的99%**，不是每个未来query、单个head的保证，
更不是任务准确率。

**对照与计费。** 比较各reader独立top-weight、全体权重mean/max聚合，以及
缺额覆盖贪心；每个方法都满足相同的观测分组权重约束。以三个简单方法中
并集最小、同并集时逻辑读取最少者作强简单对照，贪心也保留这一fallback。
按当前FULL布局，任何层/head仍需要的token都按整份FULL存储计费，末页按
真实宽度计数；没有把逐head理想稀疏大小冒充原生allocator节省。

| 权重约束/逻辑页 | 独立并集，两篇 | 简单聚合并集，两篇 | 贪心并集，两篇 | 相对简单法再缩小 | 逻辑读取增加 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 90% / 1 token | 2985 / 2730 | 2051 / 1897 | 1810 / 1594 | 11.75%–15.97% | 7.65%–9.15% |
| 99% / 1 token | 3139 / 3060 | 2945 / 2852 | 2867 / 2767 | 2.65%–2.98% | 3.48%–4.22% |
| 90% / 16 token | 3092 / 2965 | 2308 / 2181 | 2196 / 2005 | 4.85%–8.07% | 2.56%–2.89% |
| 99% / 16 token | 3140 / 3061 | 3028 / 2965 | 3012 / 2933 | 0.53%–1.08% | 0.45%–0.70% |

表内是选集的逻辑token单位，并非实际HBM分配、带宽或迁移数据。1-token/
99%原型CPU贪心选集为1.72–1.85秒，简单max约42–44毫秒；不是GPU实现
开销下界，但不能部署原样CPU路径。若parent或未知reader仍需完整来源，
保留在其他层级的full master同样计费；在双方均需同一master的条件下，
99%/1-token两层逻辑容量之和额外仅减少1.28%–1.44%，不把不同层级容量
当作等价成本，也没有测得真实master驻留。

**没有过度否定。** 原型不是最优算法。按每个reader的分数背包松弛计算
并集下界后，99%/1-token代理约束仍容许约13.1%–13.3%的额外空间上界。
因此不能将2.65%–2.98%的启发式结果当成所有联合算法的极限。权重保留也
不是质量证明；KVzip、LoSA等专用系统未本地复现，不宣称已胜过它们。

**研究决定与下一步边界。** 保存实际权重及容量/读取tradeoff，直接贪心
变体不进入当前动态agent系统实现队列。V79b已封存的376个child首输入均
短于此前root输入，仍不支持把本受控多query实验当作自然长历史fork。
仅扩大样本、优化这个贪心常数或给reader改名不足以立项；后续必须取得
自然动态消费关系、未来query质量和超出强普通方法的具体系统残差。
其他算法与问题未被全盘否定，但本轮没有保留候选通过完整OSDI研究门槛。

5项CPU检查覆盖共享选择、所有reader约束、末页/跨层计费、token前缀和
分数下界；32个策略结果用保存的实测权重复算，完整输入与固定模型来源
复现核对通过。无runtime、harness、任务语义、配置或GPU改动，/goal继续
active，不因筛选测试通过宣称研究目标完成。

凭证：
[V1598实测权重筛选](../../experiments/reports/v1598_shared_kv_selection_cpu_20261017.json)、
[V1599范围与核验](../../experiments/reports/v1599_shared_kv_selection_scope_20261017.json)。
源码快照、问题/输入hash与压缩权重数组同目录保存；
`v1597_shared_selection_sources_20261017/sources.json`固定原始来源与SHA256。

### 4.114 首轮分叉已变：先核查决策差异，再筛选控制阶段稳定执行

上一轮实际权重筛选取得否定证据，本轮继续完整研究目标，先验证另一类
系统问题：服务执行的数值路径是否会改变agent控制决定，进而改变后续工作。
温度零不作为确定性证明，也不预设观测到的差异必然来自batch、cache或GPU。
这里不恢复用户已放弃的共享workspace竞争/额外调查研究线。

**从工具执行之前的决定取证。** 新审计器只定位每个root的首个普通模型
调用、其callback结果和首轮实际声明的子任务。描述身份由原`description`
UTF8内容直接SHA256产生，不包含tool call ID；生成的request/child ID不作
决定比较。原生请求则按精确request/workflow/invocation身份关联。

V75同轮native/predictive及最新完整V79b native各156个root、468项均闭合。
各对照的初始语义指纹、prompt字符数、message数量、seed、原生输入token
数和输出上限均匹配；已记录的模型、stream和上下文配置也匹配。三侧计划
温度均0，客户端请求seed均21、输出上限8192；启动日志的deterministic
模式均关闭。服务端`random_seed`则分别为916353286、363282933、381702714，
并未固定；不能把相同请求seed解释成全部随机状态相同，也没有证明这些
服务端seed差异是温度零输出变化的原因。

| 描述性比较 | 初始指纹/输入摘要/原生长度匹配 | 委派描述有差异 | 角色组合有差异 | 工具名及数量有差异 |
| --- | ---: | ---: | ---: | ---: |
| V75 native / predictive | 156/156 | 140 | 20 | 0 |
| V75 native / V79b native | 156/156 | 143 | 23 | 0 |
| V75 predictive / V79b native | 156/156 | 137 | 18 | 0 |

每项都是在该workflow尚未执行工具时产生的第一次委派。因而不能把这些
初始差异解释成该workflow先前工具结果不同；但跨轮native不是同配置重复
实验，也不能据此给出方差或数值因果结论。描述不同不自动证明任务含义、
必要工作量或质量不同；角色组合不同则是已观测的结构化选择差异。

**没有越过身份与因果边界。** 相同语义指纹和长度不能证明实际token向量、
所有模板参数、权重或中间状态相同。指纹已知不完整覆盖extra_body中的模板
开关；本轮保留这个既有限制。完整forward状态和首个分歧token没有留存，
不能将140项差异认定为数值误差、恢复错误、调度收益或额外工作。627份封存
前缀复核均强制耗尽生成器再验证hash，避免早停解析跳过末尾SHA检查。

**对一个新候选做CPU必要条件筛选。** 若只让“会发工具或spawn的整次模型
请求”使用稳定执行，是否能用较少代价稳定控制？本轮给这个方案一个有利的
事后分类条件，不要求在线预知输出，统计完整V79b的40718次调用并与原生
output token逐ID对齐：

| 事后输出类别 | 模型调用数 |
| --- | ---: |
| 请求spawn | 201 |
| 请求工作工具 | 37900 |
| 其他结构化动作 | 840 |
| 可见文本、无结构化动作 | 532 |
| 内部摘要 | 670 |
| 无可见文本或结构化动作 | 575 |

工作工具只取明确的`execute/read_file/grep/glob/ls/write_file/edit_file/
apply_patch`和`task`。前两类38101次，占93.573%；对应6250514个原生生成
token，占83.444%。这是请求的结构化动作，不声称全部已实际执行或产生
外部副作用；原生token也包含reasoning、序列化及特殊token。

结果不支持将**整次调用按工具阶段启用稳定执行**作为低覆盖成本的新性能
机制。未选中的摘要和文本仍可能影响后续控制，计数反而有利于该候选。
这不是验证器耗时、最小验证工作或speedup上界，更不限制token级margin
验证；本轮不为这一直接变体开发运行时。少数首轮root稳定，也不等于整个
动态workflow的后续决定稳定。

**已有工作与原生能力。** MarginGate/R87已有按logit margin触发稀疏验证和
修复的直接机制，不把“只验证风险token”重新提出为新主张；未复现其性能，
不据论文数字估算本系统收益。UNISON/R88已有session hazard与KV驻留协同，
但session生存/驻留不能等同完整workflow剩余GPU工作或任意DAG调度；没有
据此否定所有hazard调度算法。

冻结engine四份源码与实际launch tree `53020ce3`逐字节一致，已有
`enable_deterministic_inference`分支。FA3、环境/MoE设置及GDN prefill有
相关处理；GDN明确拒绝deterministic模式下的FlashInfer prefill，要求Triton。
这些只是源码能力和约束，未验证Qwen3.5全路径确定性、兼容性或成本。
本轮没有开启该开关，不能用更慢的deterministic native替换原native后就
宣称达到原吞吐目标。未来若研究该对照，双方条件和原fast native仍需保留。

**研究决定。** 保存初始控制差异和整次调用覆盖数据，暂不选择这一稳定执行
变体，也不将单轮JCT差直接归因于某个机制。正式多轮实验的既有约定不变，
开发阶段仍先用具体成本和系统反例筛选；不新增重复GPU实验或配置任务。
新的OSDI主机制仍须有动态workflow特有的可测成本、超出合理强对照的方案、
正确性边界及CPU初验，本轮没有将这些缺口改称已完成，/goal保持active。

凭证：
[V1602初始原生契约](../../experiments/reports/v1602_initial_decision_native_contract_20261017.json)、
[V1603控制调用覆盖](../../experiments/reports/v1603_control_sensitive_decode_20261017.json)、
[V1604来源与复现](../../experiments/reports/v1604_initial_decision_research_scope_20261017.json)。
7项CPU检查通过，raw、任务、harness、runtime、质量标签、实验配置和GPU均
未改变。V1600保留初版结果，执行快照与后续V1602扩展版本分别保存。
初始误指的论文编号未作为证据；R87/R88按实际下载的原文标题固定身份。

### 4.115 MoE验证预算：剩余tile容量不等于可免费推进的分支

本轮继续研究目标，先排除重做旧工程的路线。4.6已有终态观察后亚毫秒级
回收证据，不能恢复“更快释放终态child”主线。V1603中201次生成spawn的
调用合计50524个输出token，仅占全程0.6745%；它只是直接生成工作覆盖，
不是开叉流水线或间接批处理收益的JCT上界。普通流式工具/子请求启动还须
与Conveyor、FlashAgents等比较，本轮没有重构harness。

**计算侧待证伪问题。** 目标MoE每层的工作随所有并发请求的联合路由变化，
多个agent的验证成本未必可按请求独立相加。是否能在验证过程中，根据当层
实际已知路由，给有价值的请求分配更多可完整验证的token前缀，同时保留
其他已准入请求的基本进度？这不要求首先准确预测child RETURN秒数。
但普通MoE多请求也有非加性成本，动态workflow标签本身不是机制差异。

**先收紧已有能力。**

| 一手对照 | 实际核对内容 | 本候选仍须证明什么 |
| --- | --- | --- |
| FASER/R89，§4.1–4.3、6.4.2 | 请求级自适应草稿长度、验证层内token提前退出、draft/verify交叠，且已有MoE适配 | 不能声称现有方法只在完整forward后丢弃token或只支持dense |
| SpecFit/R90，§3–4 | 以每层专家集合的边际成本选择ancestor-closed子树，保留EAGLE候选和目标验证 | 原文聚焦offloaded MoE；迁移到resident场景仍有HBM/算子成本，不能设为零或省略合理适配 |
| 耦合解码/R91及经典speculative sampling/R92 | 共享随机性、目标分布与正确验证已有直接工作 | 同位置随机数、普通rejection或prefix提交不作为独立新创新 |

不把FASER的中间logit拒绝预测器当作已经实现任意MoE成本预算，也不因其
有通用控制接口就声称所有新控制问题已被解决。判断标准仍是给强方法同样
图状态、草稿和资源后，是否还存在具体、可测、可消除的代价。

**冻结源码的CPU资格。** 只提取实际engine tree `53020ce3`中的
`get_default_config` AST，在CPU以BF16、256专家、top8调用其fallback，
48/96/384个输入token对应BLOCK_SIZE_M为16/16/64。这不是已观测的tuned
配置，也不证明实际使用该Triton路径。源码的对齐按每个expert分别补齐，
不能把所有padding当一个可任意填充的全局空池。

三个具体CPU检查改变了接下来的实现选择：

1. **专家容量不可互换。** 最小例子中，一个expert的4-row tile只占1行，
   剩余3行；新增token若路由到另一个expert，tile数仍从1变2。存在padding
   不证明该新增token没有边际MoE成本，Attention、路由和状态工作也未消失。
2. **层内局部机会不等于完整验证。** 两层、两个候选的精确例子中，第一层
   可零新增tile容纳候选A，第二层可容纳B；两层的可行候选交集为空。
   不能把各层剩余量相加后声称有一个能提交的额外token。这不是目标模型
   的实测路由，也不排除付出一定代价的预算方案。
3. **朴素内容相关停止可能改变采样分布。** 令目标和草稿均为A/B各1/2；
   A便宜则验证并接受，B昂贵则停止并重新独立从目标抽bonus，最终A/B为
   3/4、1/4。即使前一个位置已正常提交，这个下一位置反例仍成立。
   这不是对FASER、SpecFit或现有论文正确性的反例。

将每个提交位置的随机数预先确定，停止/回退时继续使用同一个数，可以在
这个有限分类模型中恢复目标分布。对分母8的21种正三元分布，枚举全部
target/draft组合与8种候选gate，3528项Fraction精确检查均通过。该原则已有
R91等工作，不称为新算法；没有证明原生seed逐bit兼容、GPU数值一致或目标
模型的完整speculative sampler。随机性只在同一请求的draft/target之间
对齐，不能跨agent复制样本；本例也未证明多请求间随机关系保持。当前
workload温度零，反例不诊断现有系统
的采样bug；它约束的是拟议方法将来可宣称的正确性范围。

**如果继续，机制必须具体到以下位置。** 这是研究草案，尚未实现：

- 当层路由产生后，以整批新增expert/tile工作、已支付的低层计算及剩余层
  为代价，联合决定各请求继续验证的连续前缀；不能只换一个agent优先分数。
- 只提交全部目标层已完成且通过目标选择的前缀。丢弃位置的临时KV和混合
  状态必须回滚到实际提交边界；不能仅截短FULL索引却保留更后面的GDN状态。
  普通checkpoint/日志已有方法继续作基线，不以增加引用标签作为创新。
- workflow输入只使用当前可见的JOIN、等待与服务进度。强对照得到同样
  信息；比较联合非加性预算是否比独立分配真正改善已提交工作及workflow
  完成，而不是只偏爱少数root或提高草稿长度。

**下一步实验必须可证伪。** 先取得真实路由、实际选中的kernel配置和受控
候选的边际延迟，再计入attention、状态、选择器与rollback成本。CPU
fallback和小例子不替代这一证据。固定资源下比较原fast native、相同合法
推测框架、FASER式自适应/提前退出、SpecFit的合理resident适配及相同图信息
的简单预算方法。若接口适配已消除残差、所有层的可提交增量极小，或额外
控制/状态开销抵消收益，则停止这个候选；不以人为加大无用fanout制造机会。
正式端到端仍保持原在线到达、任务/质量/全部终态与多轮要求。

**当前决定。** 仅保留这个明确的待证伪研究问题，不选实现主线、不启动GPU，
也不宣称OSDI创新或性能提升成立。V79b没有实际expert route记录和推测
验证数据，不能由batch48和权重体积编造收益；该缺口不表示所有CPU研究
被阻塞。完整目标仍未达到，/goal保持active。

3项CPU检查及3528项有限精确概率案例通过；未加载模型权重或GPU库。
凭证：
[V1606约束筛选](../../experiments/reports/v1606_verifier_constraints_cpu_20261017.json)。
实际来源及hash见`v1605_verifier_budget_sources_20261017/sources.json`；
FASER全文PDF/文本、SpecFit原文及原作者耦合资料均归档。最初猜测的FASER
编号2603.24785无关，未作证据；采用已核对标题的2604.20503v1。

### 4.116 目标模型首层路由：局部padding机会没有显示联合分配优势

按用户要求跳过已撤下残差候选的区别讨论。本轮只推进4.115的CPU证伪：
不是继续为已放弃候选做实现，也没有开启新GPU实验。

**实际执行边界。** 使用本地`Qwen3.5-35B-A3B`检查点和已安装Transformers的
`Qwen3_5MoeDecoderLayer`，只执行第0层的输入归一化、GDN、残差、输出
归一化与router。层对象先建在meta设备上，仅加载这些计算所需的
65.321MiB权重及2234个embedding行、8.727MiB。所有routed/shared expert
权重保持meta，hook在专家计算之前停止。强制使用库内PyTorch CPU fallback，
进程断言CUDA未初始化；这不是原SGLang fused kernel的数值等价验证。
12个微批的计时合计5.462秒、进程峰值RSS约1369.43MiB，均不是GPU成本。

输入按最新完整V79b manifest顺序取48个真实任务，经冻结任务prompt builder
构造后截取最多256个token；每个末尾9个位置作为1个基础位置加8个候选位置。
它们是受控的teacher-forced任务文本，不是原native完整聊天prompt、在线
decode frontier或真实draft，不能据此计算接受率。模型config/index、任务
manifest、prompt源码、选中tensor/embedding行和路由artifact均记录hash；
没有声称校验了完整检查点。V80/V81不替代V79b的最新完整实验资格。

**固定几何问题的结果。** 保存的路由形状为`48×9×8`、logit形状为
`48×9×256`。先用保存logit重做softmax/top8得到同样路由，再由独立脚本
逐位置核对top-k成员关系、前缀长度、tile计数和固定宽度对照。

| 假设tile行数 | 基础tile数/活跃专家 | 独立方案额外位置 | 联合方案额外位置 | 全48请求各加1位置新增tile |
| --- | --- | --- | --- | --- |
| 16 | 186/186 | 16 | 16 | 41 |
| 32 | 186/186 | 16 | 16 | 41 |
| 64 | 186/186 | 16 | 16 | 41 |

12/48个输入可延伸至少1个位置，最长3个位置。三种宽度下，逐请求求出的
最长零新增tile前缀合并后仍然可行，因此其总和16既是独立方案结果，也是
该固定base、零新增tile、连续前缀问题的上界；联合算法无法在这批数据上
再增加可行位置。这个证明只针对已知单层路由和该预算，不否定有额外预算、
其他输入或其他目标下的联合算法，也不把16个位置称为16个已接受生成token。

**筛选决定。** 不将“联合填充专家padding”推进为当前实现主线。局部存在
空行还不足以超过简单独立方案，且Attention/GDN、router、后续层、拒绝、
回滚和控制成本尚未计入。若以后重新考虑付费的联合预算，仍须先证明
FASER式自适应和SpecFit合理resident适配之外的残差，以及动态workflow
带来的额外约束；不能用普通MoE现象加agent标签宣称创新。本轮不为补足
这些缺口直接加载完整模型、占用GPU或制造无用fanout。

3项探针CPU检查通过；独立复核432个路由位置、48份token hash及三组完整
几何结果通过。代码、执行前快照和artifact：
[V1607首层探针](../../experiments/reports/v1607_qwen35_first_layer_routes_cpu_20261017.json)、
[V1608独立复核](../../experiments/reports/v1608_first_layer_route_artifact_audit_20261017.json)。
没有修改runtime、harness、任务、配置、原trace/质量/资格或启动GPU；
完整研究目标未达到，/goal保持active，继续筛选其他系统设计。

### 4.117 重新归因JOIN后继：小范围通信与主要工具续接须分开

上一轮在真实首层路由上排除直接联合padding变体，属于有证据的推进。
本轮继续CPU研究，按当前冻结V79b和一手原文重新约束候选，不恢复已撤下
残差压缩、共享workspace或混合状态专用策略。

**发现并修正观察器错误。** V79b客户端201个`join_satisfied`事件的
`invocation_id`全部为null，真正的等待parent须经`join_id`关联此前
`join_wait`。旧`audit_native_hybrid_reuse.read_requests`直接将null放入
joined集合，导致`follows_observed_join`全漏记。它是审计标签错误，
不是发现了runtime没有满足JOIN或没有执行parent。

新增独立因果审计，按已记录事件sequence消费JOIN状态；内部summary不消费
parent的JOIN标记，压缩重叠用单独flag保留，未知waiter不补造。随后修正
旧通用审计并保留修改前源码，其SHA与V1485记录完全相同。V1612对原156个
来源分别执行旧/新函数，全部40718个request ID及其他元数据完全一致，
只有201项JOIN标记由false改为true；独立审计的201项ID集合也精确相等。
旧报告不覆盖，旧总输入/缓存/原生prefill计数不因该标签问题而失效。

**闭合后的范围。** 全40718请求共登记44331657个未命中输入token，
与既有原生extend总量一致；这是合计闭合，不是每层执行或时间分摊。
每个登记Host命中FULL token按冻结池的20480字节计：

| 全量保留的请求集合 | 调用数 | 未命中输入token | 登记Host命中FULL |
| --- | ---: | ---: | ---: |
| 全部模型调用 | 40718 | 44331657 | 611.358GB |
| 全部JOIN后首普通请求，含压缩重叠 | 201 | 333740 | 2.421GB |
| 普通工具后续接，不含更高优先分类 | 38034 | 21448003 | 606.710GB |
| 内部summary | 670 | 14785177 | 0.003912GB |
| 压缩后首普通请求 | 670 | 6347474 | 0GB |

表中JOIN集合与压缩后集合有2项重叠，不能相加；互斥stage分类中的
`first_after_join`为199项。普通工具后续接占全部调用93.4083%、未命中
输入48.3808%、登记Host命中FULL99.2399%；不是“所有长工具”子集，也不
证明每次Host命中唯一消费某次物理H2D。不能与ACK字节再次相加。
JOIN后继未命中输入占0.7528%，仍包含child报告之外的内容，不能全算可
直接转接的KV。376个child最后普通调用均在completed RETURN前、无工具
动作，合计原生输出226526 token，占全程3.0241%；含reasoning/序列化，
不等于可见报告token，更不等于可复用目标状态。

JOIN满足到parent提交的客户端间隔P50/P90为23.525/70.085ms，P99为
470.241ms，最大35.190秒。最大值可含summary等客户端工作，这些不是GPU
排队或纯idle；当前不能沿用旧版本“普遍有几秒空档”作为固定恢复窗口。
也不能将本轮token/字节比例当作关键路径、平均JCT或系统收益的上界。

**原文强对照进一步约束直接候选。**

| 一手来源 | 已核对机制 | 本轮决定 |
| --- | --- | --- |
| GraniKV/R93，§3.2–3.5、附录B.10 | 连续共享prefix池、细粒度私有suffix和attention路径选择；准入fallback留在paged池，不在运行中promotion | 作为布局强对照；不把存储布局等同D2H/H2D优化，也不把补普通promotion当创新 |
| RelayCaching/R94，§3–4 | 已有上游decode KV用于下游prefill，并做层范围及token级context rectification | 直接输出KV转接不作新主线；其近似与质量结果不证明当前coding任务原生状态等价 |
| Sutradhara/R95，§4及评估设置 | tool-independent prompt splitting/prefill、流式工具派发、语义cache hints；baseline已开启prefix caching | 已知prefix等待期提前计算属于强已有能力，不与任意未来stdout流式计算混为一谈 |

R7/TokTier的精确session增量分词此前已在4.71核对，不重新包装为新候选。
GraniKV并非完整迁移控制器；RelayCaching不等于跨context逐位KV兼容；
Sutradhara不等于工具输出尚未产生就能计算其KV。这些边界同时约束评价，
不能通过削弱已有方法的合理适配产生差异。

**下一步筛选收窄到真实主要路径。** 普通工具续接反复访问长历史是当前
规模证据最充分的对象。可继续核查并发不同context对共同prefix的实际
状态访问，以及布局/驻留变化是否产生强共享执行方法仍未消除的代价；
但目前既没有完成这种归因，也没有选定新算法。必须先给native共享、
FlashInfer/cascade、GraniKV合理适配及工作流强方法同样的信息与资源，
再判断是否有额外可减少的读、计算、搬运或同步。不因“占99%”就恢复
工具预取主线，也不据论文future-work条目直接立项。

13项相关CPU检查通过，156个封存来源和全部请求修复前后复核闭合。
原始质量/guard/资格分层完整保留在新审计中，没有只保留成功任务。
原文HTML、题名/版本、来源hash和执行前源码快照均保存：
[V1609阶段成本](../../experiments/reports/v1609_v79b_continuation_stage_exposure_20261017.json)、
[V1611候选筛选](../../experiments/reports/v1611_continuation_candidate_screen_20261017.json)、
[V1612标签修复复核](../../experiments/reports/v1612_join_stage_label_repair_20261017.json)。
未修改runtime、harness、任务、配置或启动GPU；完整研究目标未达到，
/goal保持active。

### 4.118 工具续接的共享历史：已知公共前缀不自动形成动态workflow新机制

本轮沿4.117提出的下一项具体假设继续：多个agent是否在工具续接时反复
访问相同长历史，使通用prefix cache节省存储却仍有额外物理读取。先做
低成本的共享身份筛选，不读取3.58GB逐batch日志来预设性能收益。

**来源与边界。** 读取V1529封存的同一170564044字节原生事件，关联V1548
全部40718个request及workflow/invocation身份。冻结引擎tree source与
`53020ce3`逐字节相等。源码确认checkpoint探针在请求完成、cache插入后
沿`last_node`访问radix路径，且只输出当时仍有device/Host状态的Mamba
checkpoint；本轮仅借这些锚点识别共同逻辑prefix，没有提出Mamba专用策略。
缺失锚点不代表FULL不共享，完成时存在也不证明decode时在HBM。

每个锚点用`node_id + creation_time`区分代次，并核对绝对prefix长度
始终一致。源码split会继承creation counter，所以只比较creation_time
会错误合并不同节点；当前检查和CPU反例覆盖这一点。仅保留落在输入内
的锚点，排除18599次generated-tail锚点观察。所有原终态/质量/资格均保留。

| 全量离线身份统计 | 结果 |
| --- | ---: |
| 原生请求；完成路径探针observed | 40718；40718 |
| 普通请求；实际普通context | 40048；532 |
| 普通context/epoch组合 | 40048 |
| 全角色输入锚点代次 | 42771 |
| 跨普通context共享锚点代次 | 98 |
| 其中被至少两个workflow使用 | 98 |
| 仅同一workflow内多个普通context共享 | 0 |
| 每普通请求最长已知共享前缀P50/P90/max | 4096/8320/8320 token |

532个context包含156个root和376个child；请求epoch变化不代表新增agent，
也不能把40048个context/epoch组合称为40048次摘要或上下文改写。
摘要的`summary_root`/`summary_child`均排除于普通owner统计，避免把内部
summary当作又一个任务分支。跨workflow分类使用整轮观察，是事后描述，
不能将未来被其他workflow访问的信息回填为在线hotness或准入信号。

普通请求输入合计838280878 token，按每请求最长已知共享前缀计
170509478 token，占20.3404%。该数没有按decode步数加权，也没有观察
CUDA load、L2命中、物理page alias或实际同批消费；既不是20.34%的冗余
HBM读，更不是吞吐/JCT可省比例。同理，0个workflow独占锚点只约束当前
稀疏记录，不能证明所有任务历史都私有、没有更短共享或没有有用的同批复用。

**独立复核与强对照。** V1614从已保存共享锚点的request列表重新构造
owner/workflow集合及每请求最长prefix，52165次成员关系、98个代次及
40048个普通请求均闭合。原文核对进一步限定“动态共享”候选：

- Hydragen/R16的§3.3已有树形、分组和层级共享，不能称其只支持一个固定
  全局prefix。
- FlashInfer/R77的§3.1.1与相关工作已有不搬KV的组合格式、多层多prefix
  及统一page table执行，不能把“构造view避免gather复制”直接立为新机制。
- ChunkAttention/R96已有运行时prefix-tree的加入/结束/增长，以及chunk-first
  与sequence-first的两阶段计算来利用共享KV局部性。
- GraniKV/R93继续作物理布局及后端选择强对照；其准入fallback或未在线
  promotion本身，不足以证明一个新的动态布局方案有额外净收益。

FlashInfer归档正文的HTML页标题实际为`1Introduction`，首次独立身份检查
按完整论文题名拒绝；最终使用此前封存的versioned identity页和正文各自
SHA双重核对，未把章节标题当论文身份或放宽为按文件名认定。旧源码及
失败前快照保留，最终执行快照另存。

**筛选决定。** 当前不建设workflow专用共享布局或prefix局部性内核：
已知共享范围没有给出超出上述强方法的具体成本。该决定不否定原生服务
可能从已有共享attention优化获益，也不以稀疏身份记录宣布物理瓶颈已经
测清。后续候选仍须指出在公平强对照下额外少做的计算、状态存储、搬运
或同步；若需要新workload，先证明自然任务需求，不能复制长prompt或
强制无用fork制造共享。

6项CPU检查通过；独立成员/前缀复核通过。证据：
[V1613路径共享形状](../../experiments/reports/v1613_v79b_prefix_sharing_shape_20261017.json)、
[V1614范围与原文核对](../../experiments/reports/v1614_prefix_sharing_scope_20261017.json)。
ChunkAttention官方PDF/派生文本及所有来源hash已存，原有正文不重复下载。
未改runtime、harness、任务、配置或启动GPU；完整研究目标未达到，
/goal保持active。

### 4.119 在线验证预算：已有状态复用与未来路由信息必须分开

本轮先重核4.74，避免重复走向容易通过的容量模拟。V79b实际48-request上限
和静态分池没有改变；69.939GB设备池分别为36.843GB FULL与33.096GB Mamba，
不代表其中多少当时可用。原生观察器只采集Host用量，实际static headroom
函数用于另一条runtime路径。既有记录仍缺原生准入时刻的free/evictable、
输出预留与候选保护，因此不以低`resident_pressure`断言64/96可行。
更高并发属于未来强基线资格，不是本轮新机制，也没有启动GPU调参。

**新筛选的直接变体：保留未完成验证。** 对比原文后，不把这个primitive
推进为独立贡献：

| 一手方法 | 已有明确能力 | 不应混淆的边界 |
| --- | --- | --- |
| LayerSkip/R97，§4.3 | 自草稿与剩余层验证之间复用早期层hidden/KV与exit query | 论文含early-exit训练；不称本地未训练目标可直接获得其接受率或加速 |
| PipeDec/R98，§3.2–3.4 | 在pipeline中推进动态预测树，增量复用tree-specific KV与传递激活 | v1实际题名为PipeDec；不外推为当前单GPU、resident MoE部署已验证 |
| FASER/R89，§4.2–4.3 | 按层提前退出、按已提交prefix推进frontier chunk、重置与draft/verify交叠 | 已有MoE适配；不能把“有frontier”或保留一段计算直接当新意 |

上述能力不等于已经实现任意online MoE预算控制，但对新设计必须提供合理
组合对照。仅将pipeline层切得更细、将缓存加上agent标签，仍不足以立项。
本轮未写新的target执行器或改变当前模型/草稿/采样方式。

**进一步核对SpecFit的信息来源。** R90的§3.3、§4.6与附录实现协议明确：
atlas来自独立target query，预先获取在请求计时外；但atlas conversion、
selection、target verification、commit与必要同步都已计入请求时间，
不能将这些已计时费用一并说成被排除。部署可替换为route predictor，
预测器训练留作future work。不能将论文测得的selector结果称为免费、
已部署的在线全层路由预测。原文同时说明route预测错误影响效率、
目标仍执行真实所选expert，
因此也不能将这项边界误写成目标模型准确率有错。
resident适配仍须保留实际HBM/算子成本，不能因为没有Host专家offload就把
成本设零。可把atlas结果列作明确信息更强的参照，不与在线方案混报。

**CPU反例只约束信息使用，不宣称性能。** 新增两个layer、两个候选A/B、
两个可能世界的精确小例子。每层基本进度都含2个expert-0 token，
tile宽4；已知第一层A/B均路由expert0。第二层世界W0为A→0、B→1，
W1恰好相反，当前观察完全相同。

- 预先知道第二层route的参照，在W0选A、W1选B，均不新增expert tile。
- 只按当前观察提前单选A/B、选A概率为p时，两世界成功率为p与1−p，
  最差情况不超过1/2；17个Fraction精确策略点及解析式均闭合。
- 一个具体逐层策略先推进A/B到下一router，再选便宜候选，可以在两个
  世界都达到相同tile数；但它比预知路线多处理1个候选的早期位置。
  相同tile数不能据此把Attention/router/状态工作或保留开销设为零。

这个有限例子不是对所有online/学习策略的不可能性结论，不是最优信息
获取成本下界，也不是对SpecFit/FASER的性能反证。它不测接受率、Qwen
实际路由、逻辑位置到GPU FLOPs/时间的转换或workflow JCT。即便可以缓存
这些早期结果，其正确性、失效条件和净收益还需要完整验证。

**保留的只是更具体的待证伪问题。** 能否在按需取得目标实际route时，
扣除已经付出的低层计算、丢弃位置、保留状态与控制开销，仍比公平的
FASER式控制、SpecFit合理适配及简单联合预算产生更多完整验证的有用进度？
当前没有该问题的端到端证据，也尚未证明动态workflow提供了额外收益，
所以没有选定实现主线。4.116已排除的直接padding填充不恢复，4.118的
公共prefix身份计数也不混入MoE收益。

2项CPU检查及17个有限策略点通过；新原文题名/版本、既有FASER/SpecFit
hash、容量边界及执行前脚本快照均绑定到
[V1617信息与计时约束筛选](../../experiments/reports/v1617_causal_verification_information_timing_20261017.json)。
V1616的初始筛选及旧源码快照保留，V1617补齐预先获取与已计时转换的边界。
LayerSkip原始PDF/文本与PipeDec原始HTML存于V1615目录。未加载新权重、
修改runtime/harness/任务/配置或启动GPU；完整研究目标未达到，
/goal保持active。

### 4.120 GPU工具区域与显存生命周期：真实事故不足以排除已有组合

按用户决定，已撤下跨上下文残差候选的区别讨论继续跳过，不恢复实现。
本节记录另一条线索的快速筛选，不将4.69–4.70已降低优先级的普通显存借还
换名立项；保持CPU研究，不占用GPU。

**新证据是什么。** 固定公开KernelBench-Multi仓库提交
`7bb3f95c8a86ee410bc58b3f6f80fed7204cc7ec`，保存SPEC、DEVLOG、命令包装器、
串行sweep驱动及commit元数据。DEVLOG的2026-07-25条目报告四个并发
GPU编程会话发生OOM，并将原因归于跨命令锁区间保留的显存分配；后续
误杀和端口冲突也分别记载。驱动目前串行执行完整agent会话。[R100]

这是开发者公开事故记录，不是本项目重放的原始资源trace。尤其所保存的
命令包装器声称按进程生命周期持锁；事故说明尚未给出每个活跃GPU对象的
创建、使用、等待、释放及锁所有权。因此不能仅凭文字把“哪个对象如何
跨过锁边界”认作已独立复现。约74GB的描述也不当当前任务工作集测量。

**与已有能力的边界。** 本轮保存并核对KREX的2609.30057v1原始PDF与
派生文本，实际题名为KREX: Concurrent Kernel Benchmarking on Shared
GPUs via Region-Granular Exclusivity，不沿用未经原文确认的扩展题名。[R99]

| 已有能力 | 原文确认范围 | 对本候选的限制 |
| --- | --- | --- |
| KREX区域互斥 | §4.2阻止新提交、排空已有工作并保护CPU提交线程 | 计时区域内排他执行及排空协议不是新机制 |
| KREX context复用 | §4.3保留context和库缓存，命令退出时释放其分配的资源 | 不能把context持久化等同跨命令保留任意活跃tensor；也不能推断它必然支持本事故 |
| KREX部署范围 | §7将multi-GPU candidates列为后续扩展 | 多加一个多GPU锁不足以独立立项；范围不同不自动证明新研究缺口 |
| GVM及已有弹性KV | 4.77已有物理占用限额、换页与动态资源管理证据 | 不能说现有方法仅管计算互斥、不管持久显存；需比较合理组合 |

JointServe、Prism/kvcached与GVM的既有筛选结论继续有效。特别需要比较：
完整会话串行与正常清理；区域互斥加物理配额/换页或弹性KV；在受支持的
分配路径上先恢复必要状态、再在保护区内warmup及计时；分设备放置或
工具自带普通offload。这些是应当资格验证的强组合，尚未在本地部署，
不能反向宣称它们已经解决全部案例。

**当前不选主线的原因。** 公开事故支持“命令互斥不等于对象生命周期管理”
这一具体提醒，但没有同时建立以下证据：动态父子依赖相对独立会话的额外
成本；本地LLM与工具的联合驻留/计算时间线；IPC、collective或symmetric
memory中实际不受支持的对象；强简单组合之后仍有规模的端到端损失。
普通配额、换页、warmup与排他区域的连接不能先当作OSDI主贡献。

因此不实现新控制器、不注册新workload，也不为补证启动GPU。以后仅在
自然应用取得对象级因果证据和强组合残差时重开。后续继续从主要工具续接
路径找真实计算、搬运或同步成本，不由来源范围差异直接挑机制。

[V1618筛选记录](../../experiments/reports/v1618_gpu_tool_region_contract_screen_20261018.json)
绑定8处原文位置、3份旧来源hash、下载URL与固定版本，以及本次执行源码
快照。这里只完成CPU来源/身份核查，没有CPU性能模型、工具执行或GPU
收益结果；未改runtime/harness/任务/配置。完整研究目标尚未达到，
/goal保持active。

### 4.121 付费验证预算：局部联合空间不能绕过已有跨请求方法

4.116已排除直接“零新增tile填充”主线，本轮没有恢复它。新筛选只问：
允许增加明确的expert tile预算时，跨请求的联合前缀选择相对简单边际成本
策略有多少局部空间？使用此前保存的48×9×8目标模型首层实际路由，不重跑
模型、不读取新权重，也不启动GPU。

**先补齐比原先更强的一手对照。**

| 固定原文 | 直接已有能力 | 当前比较边界 |
| --- | --- | --- |
| EcoSpec v1/R101 | 训练轻量全层expert预测器，用已选路径的expert集合计算边际代价 | SpecFit将预测器训练留作future work，不代表全领域尚无训练预测器；EcoSpec的集合代价不等于实际tile/延迟 |
| D-cut v1/R102 | 批内跨请求剪枝，结合部署cost table选择验证深度 | 原文评估包含Qwen3.5-35B-A3B，但TP4、8K上下文等与本地TP1长多轮任务不同；不能省略它作为直接对照 |
| ECHO v1/R103 | 将batch视为统一super-tree，在全局预算下调整深度与宽度 | 不是只做单请求固定长度；加入workflow权重本身仍不足以立项 |
| ASPIRE v1/R104 | 同一forward混合draft/verify，按请求在线选择验证，带batch cost模型 | 其full/sparse attention机制不等于本地GDN已适配，也不能忽略其异步执行能力 |
| DScale v1/R105 | 可变前缀预算、path-aware tile及固定地址workspace接入捕获graph | 可变验证长度与CUDA Graph相容本身已有；不直接外推其dense模型结果 |

本轮原文题名和版本逐份严格核对。最初取得的2605.15864v1和2602.17234v1
实际是另外两篇论文，作为编号不符的来源保留并明确排除，未用于判断D-cut/
ECHO能力。后续使用2607.14647v1与2604.09603v1的正确原文。DScale归档HTML
题名将Block-Diffusion与Speculative连写，保存实际title而非由文件名推断。

**CPU几何问题与正确性。** 每个request固定保留首个frontier位置，在其后
8个已知位置中选一个连续前缀。目标为最大化额外位置数，约束所有request
合并后的各expert token数向上取整为tile，新增tile之和不超过预算。
一个整数规划同时选择前缀长度和expert tile数；每个可行解另按原始路由
重新计数，避免把独立请求的padding当作可无条件相加。简单对照分别是
逐位置贪心和一次看完整候选前缀的边际成本贪心；两者均使用同样的已知路由。
它们不是已引用论文的完整复现。

3项CPU检查通过，其中24个小实例逐项与完整穷举比对；另覆盖“各自单独
免费、合并却超过padding”的反例和非法重复top-k输入。下面是完整受控
首层artifact的8个设置，每次整数规划限时8秒、单线程；限时可行解不称最优：

| tile宽度 | 新增tile预算 | 逐位置贪心 | 路径贪心 | 整数规划可行解 | 已证明上界 | 已证最优 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 16 | 0 | 16 | 16 | 16 | 16 | 是 |
| 16 | 16 | 51 | 59 | 60 | 141 | 否 |
| 16 | 32 | 92 | 98 | 75 | 213 | 否 |
| 16 | 64 | 190 | 213 | 233 | 294 | 否 |
| 32 | 0 | 16 | 16 | 16 | 16 | 是 |
| 32 | 16 | 51 | 61 | 63 | 114 | 否 |
| 32 | 32 | 104 | 114 | 125 | 212 | 否 |
| 32 | 64 | 304 | 331 | 342 | 342 | 是 |

基准集合含186个base tile；预算64并非“免费”。width32/budget64中，简单
路径贪心与该数学问题最优解相差11个位置，但不能把这一例外推到所有设置。
width16/budget32的限时规划可行解反而低于贪心，只反映求解进度，不能据此
说联合方案不如贪心。其余5个付费设置的上界不紧，局部联合空间仍未排除。
Python参考贪心两种合计约38–329ms，整数规划部分达到8秒限时；这些只是
该CPU原型成本，不是优化实现的速度下界，也不支持直接放入decode热路径。

所有route是此前CPU forward之后已知的信息，额外位置来自teacher-forced
任务文本，不是已接受draft。目标未扣Attention/GDN状态、真正FFN执行、
全层route取得、丢弃计算、候选接受与控制开销；固定16/32也不是实测tuned
kernel配置。即使局部位置数增加，也不能推出token吞吐或workflow JCT改善。
该artifact不是原native的真实batch，动态依赖的额外作用仍缺证据。

**本轮决定。** 不以“跨请求预算+路由预测+graph接入”这一直接组合选主线，
不训练新predictor或实现验证引擎。新强对照并没有证明所有净收益预算方法
无效；它们使下一方案必须指出给足同样信息、合理组合之后仍存在的具体
成本与机制差异。4.119的在线代价问题保留为待证伪问题，不再以“没有在线
路由预测器”或“没有批内预算”作为其差异。已撤下的残差/共享workspace
方向均不恢复，继续只做CPU研究。

[V1619报告](../../experiments/reports/v1619_joint_verifier_paid_budget_cpu_20261018.json)
绑定全部8组前缀、原始route hash、来源题名/版本、超时状态、可行解/上界、
CPU依赖版本和执行前源码快照。没有runtime、harness、任务、配置或GPU
变化；OSDI主机制仍未选定，完整/goal保持active。

### 4.122 待恢复需求与拥塞：普通可见压力已早于主要H2D高峰

本轮筛选“动态分叉与等待中的agent会产生未来恢复需求，是否需要增加一个
专门的预测准入机制”。先检查它相对普通已登记请求、既有agent级反馈与
程序调度，是否确有新的信息缺口，不先实现控制器。

新脚本从已封存派生报告对齐40718个native/client请求、532个普通调用
family及201个JOIN等待区间。重新构造native登记→完成、登记→首次dispatch
的区间积分，并以JOIN wait→satisfied统计等待组数；后者不是可立即运行的
请求或实际HBM占用。first ordinary model submit只计某family第一次普通
模型提交，不把它等同external root到达或invocation创建。

| 分钟 | 已登记请求均值 | 首次dispatch前排队均值 | 等待JOIN组均值 | 原生H2D/GB |
| --- | ---: | ---: | ---: | ---: |
| 20–30 | 71.781 | 24.163 | 40.332 | 0 |
| 30–40 | 95.044 | 47.455 | 47.351 | 0.421 |
| 40–50 | 119.918 | 72.602 | 60.201 | 821.767 |
| 50–60 | 87.732 | 40.071 | 46.714 | 50.692 |

40–50分钟全部600秒的已登记请求数均至少96，而20–30分钟已有571.519秒
至少64。40分钟之前，458/532个普通family已有首次提交；40–50分钟新增
7个root、32个child的首次普通提交，此后还有35个child首次提交。这些
是回溯的需求时间线，不是任何时点已知的完整未来图，也不是安全准入上限。

结果削弱的是“必须预测尚未登记的需求才能提前看到压力”这一必要性理由：
主要恢复高峰前，普通队列已经明显积压。但它不证明具体root/agent限流
能够减少迁移、改善JCT，或说明所有动态需求预测都无价值；状态体积、
共享/保护页、实际可回收容量及调度后的执行变化仍不能从这些计数得到。
4.74的物理容量资格边界保持，不用136个登记请求推可运行136个。

**强已有方法不只观察decode中的请求。** 重读已有原文确认，CONCUR v1
§4.1明确保留工具等待agent的逻辑KV关联，并通过高压时限制新准入来保护
这部分状态[R23]；不能把“统计暂时等待的agent”当作缺失能力。其物理驻留
效果和本地兼容性仍未验证。Agentix已讨论闭环到达与高并发交换放大[R25]；
4.94固定的TokenCake实现已有图/容量保留及idle borrowing。先按相同过去
事件提供合理组合，不能由baseline缺一个hint制造优势。

据此不把普通“未登记恢复需求计数+拥塞反馈”选为主机制，不新增准入策略
或GPU任务。跨资源反馈问题继续开放，但下一份候选须给出这些信号/强组合
之后仍存在的具体成本对象，不以拥塞曲线本身代替机制差异。

[V1620派生筛选](../../experiments/reports/v1620_v79b_latent_workflow_demand_screen_20261018.json)
保留12个完整/末尾部分分析窗口。为避免把采集收尾空档混入末窗，分析截到
7179.043秒，7219.416秒采集范围另重现V1475全部13窗；不静默丢弃末尾。
独立逐区间求和与family计数共60项通过，11份来源hash/快照及3份旧primary
hash核对通过。本轮只重放既有派生报告，没有重扫raw JSONL或测GPU，也未
修改runtime/harness/模型/任务/配置；主机制尚未选定，完整/goal保持active。

### 4.123 传输期间的回收：先区分整批屏障与单块数据依赖

本轮继续查4.105的跨workflow恢复提交耦合，具体检验“页被传输持有，
是否可以更早释放源页，从而避免恢复前的容量等待”。没有把通用异步写回
重新选为主线，没有更改冻结引擎或运行GPU。

**冻结实现已经具备什么。** 6份源文件逐字节匹配资格tree
`53020ce3efef3ed362e9af0db77147b39070f064`：正常MHA page-first写回有
GPU K/V staging；wrapper按64页chunk迭代，单write stream中先gather，
再提交该chunk的Host copy。L2TransferEngine仅暴露整次写入的完成event，
write-back回收路径在完成后demote。H2D另有逐层ready回调，不是完全没有
流水线。4.106的内部小copy仍是另一个成本，不能由生命周期分析视为修复。

以下三个对照必须分开，否则会夸大“提前释放”的独立空间：

| 对照 | 何时回收原始页 | 仍须保护什么 |
| --- | --- | --- |
| 整批屏障 | 全部写入ACK后 | 尚未完成的源读取和整批引用 |
| 逐块Host完成 | 该chunk写入Host完成后 | 其他未完成chunk及活跃读者 |
| 逐块gather完成 | 该chunk已有独立GPU snapshot后 | snapshot的bank直到DMA结束；Host仍不能提前宣称ready |

第二种相对第一种可能渐进释放一大批页，但这属于必须纳入的普通异步/
分块回收基线。只比较第三种相对第二种时，每个“源页已释放、Host尚未ready”
的chunk必须仍独占一个staging bank；因而额外提前回收的在途字节不超过
这些bank容量。这是该协议的结构界，不是所有GPU传输机制的不可能性结论。

V1578登记的正常FULL staged几何为64页、每页K或V合计10240字节，
一对K/V staging容量为`64 * 10240 * 2 = 1310720`字节，即1.25MiB。
本轮没有内部branch计数，这仍是源码条件界，不证明全部真实FULL传输用了
该分支；不涵盖Mamba、额外banks或其他后端。它不是JCT、延迟或物理空闲
容量上界。增加bank也必须计入相同HBM预算，不能把快照当免费空间。

**CPU正确性筛选。** 新有限模型包含gather提交/完成、DMA提交/完成、
活跃读者结束和源页回收。14种chunk/bank/初始读者几何共42408个可达状态、
175414条合法转移都保持：活跃读者的源不复用；snapshot完成前源不复用；
DMA完成前bank不复用；提前回收chunk数不超过bank数。另用按bank占用组合
得到的独立闭式计数逐项核验全部可达状态数量。

5项CPU检查通过，另保留三个错误捷径的toy反例：gather尚未读完就改写源；
只有GPU snapshot时便宣布Host ready；DMA尚未读完就覆盖bank。它们不是
冻结引擎已发生的race，也不验证CUDA内存可见性、跨rank恢复或真实copy时序。

**更强的已有机制约束。** 新冻结Range v1实际题名为Range: Decoupling
KV-Cache Reuse Granularity from Storage Work，§3.3–3.5已有物理byte stripes、
completion-owned GPU transfer banks、逐层交付与ownership/recovery[R106]。
它研究SSD/GDS路径，不将其性能外推Host DRAM，但不能将bank由完成事件
管理或逻辑对象与传输粒度解耦作为缺失primitive。LayerKV v1已有layer-wise
KV allocation/management/offloading及scheduler[R107]；其请求/模型范围
与当前多轮混合模型分开，仍需作为合理适配对照。

因此不以“释放更早”“多加bank”“逐层分配”或这些primitive的直接连接选
OSDI主机制。保留源依赖与Host ready分离的正确性边界，以及通用分块异步
回收作为工程强对照；动态workflow在这些对照之后的额外成本仍未建立。
不为本筛选实现控制器、分配器或安排GPU。

[V1621协议与条件界](../../experiments/reports/v1621_transfer_retirement_contract_cpu_20261018.json)
绑定冻结源码、原文题名/版本、14组状态计数、反例和执行快照；11份hash及
快照复核通过。不加载ML库、权重或修改runtime/harness/模型/任务/配置，
整体研究目标尚未达到，/goal保持active。

### 4.124 取消机会与观测成本：不把缺失场景或诊断自身当新瓶颈

本轮筛选动态workflow的另一个可能成本：parent结束或分支取消后，推理是否
继续消耗GPU。实际重读156份封存client event文件及完整native audit前缀，
以独立invocation ancestry重建40718个请求的普通owner，再核对native登记、
首次dispatch、完成与owner/workflow终态。

**当前取消场景缺失。** 532个普通invocation均有RETURN记录，cancel为0；
40718个请求中，登记、首次dispatch或完成晚于owner终态的各为0，晚于
workflow_end完成也为0；deadline expired、deadline abort和native abort
审计记录均为0。1202个RETURN还包含670个内部summary，不当作1202个agent。
RETURN只是事件形态，不表示全部任务无guard、全部正确或每个输出均被消费；
原质量、终态、measurement_complete及资格字段保持。

因此不把取消/孤儿推理作为当前主机制，不制造无用分支再取消以展示收益。
这不否定自然any-of/quorum或失败恢复负载；当前没有相应成本证据。
终态后的缓存写入也不能仅凭时间就称为浪费，共享前缀、未来复用和在途
读者保护另有条件，本轮没有把全部post-RETURN状态操作归成孤儿。

**需要单列的观察是诊断工作本身。** `runtime_audit.jsonl`完整前缀为
3580718049字节/418506行，其中7118条后台writer累积计时快照。冻结应用
`c40d2f6072be7689c12bc92b5b8686abe350bb60`的3份源码逐字节核对：
调度线程构造记录并`put_nowait`；独立Thread执行序列化、写文件及prefix/
eviction归因。后台异步不代表零开销，也不代表所有相关工作都移出调度线程。

下表按实际writer处理快照的累积差分统计；对应近似分钟段，真实端点偏差
均显式保留，最大0.926秒。eviction列是诊断内部记录，不是独立物理驱逐数。

| 近似分钟 | host_block_eviction记录 | request_probe记录 | probe已测样本CPU均值/ms | 全类别实际样本CPU累计/s |
| --- | ---: | ---: | ---: | ---: |
| 20–30 | 8002 | 4973 | 0.774 | 0.282 |
| 30–40 | 11961 | 5147 | 1.460 | 0.415 |
| 40–50 | 81029 | 3521 | 4.095 | 1.425 |
| 50–60 | 15141 | 4054 | 3.099 | 0.569 |

最终writer统计包含14637个实际处理样本，样本CPU合计5.033秒、样本墙钟
5.893秒。每类从第1条开始每64条采样，是确定性systematic sampling：
不能乘64后称为实测全轮CPU，不能假设iid或给iid置信区间；样本墙钟也不是
GPU停顿。queue waiting、status发布及调度线程的记录生产不在这些计时内。
CPU和墙钟差也不能全归为GIL。此处没有估计这5.033秒对应多少吞吐/JCT。

这带来的是**基线资格要求**：诊断复杂度随缓存事件数和prefix匹配活动变化，
同样配置不保证固定加性开销。不能把4.104–4.106的高压慢路径全部解释为
无观测运行时的迁移代价；但这些样本也不足以宣布诊断解释了全部慢路径。
尤其另一调度线程的thread CPU不能直接记作writer线程CPU。未来候选验证需
将详细归因成本与最少测量、真正调度/迁移成本分开，并在同到达/质量标准下
资格比较；本轮没有注册、排队或启动这类GPU对照。

[V1622审计](../../experiments/reports/v1622_v79b_cancellation_observer_scope_20261018.json)
保存取消边界、原始hash、13个处理窗口（含最后部分窗）、采样累计差分和
执行快照。3项CPU检查覆盖差分应使用样本sum/count、新类别首条采样及
退化counter拒绝。收尾pending/queue/dropped/failed均0，仅作collector完整性
核查，不重写原测量资格。取消不选主线，观测开销只作工程强基线资格；
无新OSDI机制、runtime/harness/任务/配置或GPU变化，完整/goal保持active。

### 4.125 Workflow资源计量：分叉身份不是新的公平调度机制

按用户要求，跳过已经撤下的跨context残差候选与普通KV复用方法的区别问题，
不恢复该候选或其实现。另对“一个workflow派生多个agent，是否需要新的
资源计量机制”做一手来源筛查；当前没有将公平性确定为性能根因。

**先区分已有的两种service。** Agentix原文§4.2.1对单线程程序累计
model executor执行时间，对多线程程序采用最长已观测关键路径服务值[R25]。
它不是所有child的总资源消费；不能把关键路径值、请求dispatch后墙钟或
重叠worker区间之和直接当GPU独占服务。指标不同也不自动构成新系统问题。

VTC原文§3.1、§4.2及附录B.2已有按client汇总多个请求、单调请求成本
`h(np,nq)`及profiled成本函数[R108]。这不是任意向量资源、非单调追溯credits
或真实batch边际GPU费用的完整定理，但已覆盖普通“按输入/输出工作计量”
的直接变体。V1622已重建root和请求owner，给同一root全部后代相同client
身份是合理基线适配；不能通过不给基线root身份制造spawn新份额的优势。

DLPM v1 §3已按extend而非总输入token计服务量，结合deficit预算与LPM保留
局部性[R109]，不能将“命中prefix不重复计费+公平性”单独当新机制。进一步
固定FairCache作者仓库提交`4fa5650273e439237b163aaa0d4aa852dcd4463e`：
README明确提出计算/内存/I/O、多租户共享prefill成本分摊和公平缓存管理；
实际queue含client计量、hit/miss权重和prefix创建者credits，scheduler按
开关选用该queue[R110]。本轮未取得完整论文证明或审计全部资源路径，
不将作者声明和局部代码等同完整部署、正确性或本地性能验证。

因此不选“workflow计费”“所有child归root”“缓存命中成本折扣”或这些
直接组合为OSDI主机制。不认为这些已有方法已经解决所有动态workflow问题：
仍需在提供同样root身份、过去状态和合理缓存成本的强对照后，给出具体的
额外工作、干扰或等待；单有一种不同的计量定义不够。现有队列/分支审计
不是反事实公平收益，不能从多child或资源计数直接推导JCT改善。

[V1623来源筛选](../../experiments/reports/v1623_workflow_resource_accounting_screen_20261018.md)
保存官方VTC、固定DLPM版本、FairCache分支metadata及3份固定commit源文件，
并绑定旧Agentix/V1622报告；共9份SHA核对。未执行下载代码、重扫raw、
构造toy性能或新增测试；runtime/harness/模型/任务/配置与GPU保持不变。
当前只有强对照补全与直接变体否定，没有新收益结果；完整/goal保持active。

### 4.126 主要工具续接路径：补齐流式执行、协议与自然trace的强对照

4.125补齐计量来源后，本轮沿4.117的主要工具续接继续筛选，没有恢复撤下
候选或占GPU。先复核V1609的40718请求、互斥stage请求和未命中输入计数，
三项谓词通过；总输入44331657，其中普通工具后继21448003、summary
14785177、压缩后首请求6347474。它们不是全部可消除计算；4.78/V1489已
确认补齐所观测旧输入prefix缺口后，38840个有探针请求仍有正的残余输入。

**三种看似直接的新候选已有重要能力对照。**

| 本轮检验的直接想法 | 一手原文实际能力 | 保留的范围限制 |
| --- | --- | --- |
| 工具/外部结果边产生边prefill | Stream2LLM v1的append/update、LCP失效和多请求下swap/recompute选择[R111] | P/D分离的crawler/ANNS结果不是本地混合agent JCT；当前没有stdout各片段可用时刻 |
| 新增workflow intent与引擎反馈协议 | HEAR v1已有description/control/state/outcome、消息强度与acceptance/completion，并实例化缓存协调[R112] | 协议不等于每种优化策略最优或本地已部署，但不能把相同接口/完成语义当新primitive |
| 按agent文本/历史专家亲和性分组 | Semantic Parallelism v5已有token/request专家预测和模型数据协同放置/调度[R113] | 原文主要处理多卡EP及Attention-DP/TP通信，不证明单卡weight-read/tile收益；本地真实全层代价仍未知 |

因此不为这些直接变体新增控制器、流式工具接口或GPU任务，也不由单卡
部署差异直接宣布新颖性。Stream2LLM归档HTML的title是首图caption，按正文
题名/abstract核对；一次错误编号2602.09833v1实际为统计学论文，重命名为
excluded并排除证据，正确Semantic Parallelism为2503.04398v5。

**新的自然负载来源也须先核资格。** AgentX/Weka可提供长历史与父子回放
形状，但固定AIPerf `f7257c900f3d38a3ca1f7ba7f40aca05ffe0123e`和AgentX
`104c7c8086666385fbf3962911b55f68bf08a04a`后，源码/文档明确了[R114]：
prompt由hash_ids合成；显式subagent和LCP推断chain不同；部分链重建使用
“未来更长状态是否再用”，worker分组还看完整重叠区间。离线重建允许读
未来，不是loader bug，但不能将这些标签默认为在线调度已知事实。
同trace的root/child共用local hash namespace，不将跨trace同整数当共享。
输入长度cap可能删除parent锚点、改变background/JOIN关系，不能只当减压。
固定时间、think-time-only和back-to-back也须作为不同负载分别登记。

这些只允许未来把它当缓存访问形状的辅助强对照，不能替代真实任务文本、
语义预测、任务质量或V79b最新应用证据。直接HF metadata获取TLS失败，
未下载/重放corpus，没有独立核验其全量统计，不把文档报告数称为实测。
下载的代码均未执行或import，原质量/guard/资格字段完整保留。

[V1624来源与负载资格](../../experiments/reports/v1624_continuation_candidates_and_trace_qualification_20261018.md)
绑定13份来源/旧报告及上述计数谓词。没有CPU性能模型或新单测，更没有
新GPU收益。下一保留候选仍须给这些强能力之后的自然成本、不同机制、
CPU可行性及同质量可证伪实验；完整/goal仍未验收，保持active。

### 4.127 调用轮次路径：主要成本不只在恢复，也不都由首服排队解释

本轮从4.126的文献筛选回到最新完整V79b，增加CPU路径重建，不恢复旧候选、
修改任务或启动GPU。先核对冻结engine：`unified_radix_cache.py`中finished
request的`to(int64,copy=True)`复制的是页索引，不能按整段KV计；原生
`StreamingSession.SessionSlot`已有同一KV record的detach/save/restore。
两份文件Git blob均匹配资格tree `53020ce3efef3ed362e9af0db77147b39070f064`，
不将普通session交接或索引维护重新包装为新状态机制。

**重建范围。** 新`audit_workflow_path_sensitivity.py`消费156份client封存源、
248758677字节，并逐份读尽/hash核对；不重扫3.581GB native audit。关联
全部40718请求、532普通invocation、670内部summary、201 all-of JOIN。
内部summary归普通owner；同轮parent工具和JOIN允许并发，不按callback
发出顺序串行。201842节点、242213边的实际时刻重建差为0，只作已观测
时间守恒，不证明完整隐含依赖、物理可行性或反事实模型准确。

| 跨workflow选中路径成分 | 累计秒 | 占累计路径 |
| --- | ---: | ---: |
| Native首服dispatch前排队 | 88903.723 | 29.84% |
| Client模型区间扣除首服排队 | 192835.454 | 64.73% |
| 路径选中的工具操作 | 14193.843 | 4.76% |
| 其他client/未知间隔 | 1993.578 | 0.67% |

表中不是服务器全局或独占GPU时间。其余模型区间仍含frontend、恢复、
服务交错和诊断干扰。选中路径含32204请求，其他8514请求仍属原任务所需
工作，不能丢弃。原94项正确完成且JCT合格子集的排队占约33.2%；
全156项、96 resolved/58 unresolved/2 empty、11 guard、原exit1及
measurement_complete=false保持，质量只用于事后分组。

**登记输入不是全部排队的解释。** 路径中30681个登记Host hit为零的请求，
累计首服排队68708.963秒，占77.285%；19008个登记uncached不超过512的
请求排队54392.151秒，占61.181%。两种分组不能相加，512仅作描述。
登记无Host hit不是服务时HBM驻留或没有容量问题的证明，但下一候选必须
解释大量短续接上的等待，不能只以H2D字节覆盖度代表系统收益。

**固定时长敏感性不能当oracle。** 仅将native首服前排队置零，保留其他
全部操作持续时间、工具开始滞后和轮次间隔时，7/201 JOIN换了控制前驱，
7个workflow改变所选模型路径；原合格子集为5/115 JOIN。数学路径差累计
88423.575秒不作实际可减JCT或一般上界。GPU容量/竞争、驻留、工具竞争、
诊断和模型路径都未按新的时序重算。194个JOIN未换前驱也不证明固有工作
主导全部失衡，首服后的竞争仍留在固定模型区间中。

据此继续要求新机制解释反复首服等待或首服后具体成本，而非直接实现更高
续接优先级。Agentix/TokenCake/CONCUR、普通session、已有混批及合理组合
获得同等信息与资源；4.124的观测成本资格仍保留。不指定只研究调度，
也不把这份图重建当作OSDI机制已成立。

[V1626主报告](../../experiments/reports/v1626_v79b_workflow_path_sensitivity_20261018.json)/
[方法和边界](../../experiments/reports/v1626_workflow_path_interpretation_zh.md)
保留全部源、请求、原质量及执行源码快照。8项CPU覆盖并发/路径切换/
summary/非法图；624个独立路径谓词和7份SHA、156份旧源绑定与输入分组
闭合。V1625保留，V1626将旧`tool_union_ms`更名为selected_tool_operation，
不把单条路径选中的工具时长误作全部工具union，路径/时长完全相同。
没有GPU或runtime/harness/任务/配置变化，完整研究目标保持active。

### 4.128 短输入等待时的在途请求：有长调用暴露，但不能只给续接插队

本轮沿4.127的短输入首服等待继续CPU筛选。新只读
`audit_request_reentry_incumbents.py`复用V1529全40718请求、V1626路径与
原完整官方质量；没有重扫raw或改运行配置。三个派生来源与旧绑定相等，
全部任务和原quality/guard/exit/measurement_complete继续保留。

**先限定单位。** 已dispatch未完成仍是生命周期，不是实际slot/GPU占用。
等待请求时长按pending数累计；在途“配对暴露”按
`pending_count * incumbent_class_count * interval_ms`累计，两者不同。
输出长度、所选路径与质量均为事后标签，不是在线已知量。

短输入集合为所选路径中登记Host hit=0、uncached≤512的18054请求，
累计首服前等待41395.826秒；其中92.14%的等待对应47–48个在途请求。
原94项合格任务中同组8140请求、23876.954秒、92.26%，等待者筛选没有
删除其他任务的在途竞争。登记短输入不表示HBM驻留、可准入或短服务，
其中905个请求事后仍输出>512 token，占5.01%。

在这组等待期间，最终输出>512的在途请求占配对暴露37.31%，而其全轮
请求数为3216/40718=7.90%；17–128和129–512分别占30.13%/32.54%。
所选路径上的在途请求占76.91%，内部summary占7.88%；这几个维度不能
相加。原合格任务等待者的对应值37.14%/76.67%/7.81%，不是只有失败
等待者才出现。长调用有持续存在的证据，不证明它们造成全部等待，也
不意味着抢占能省相同比例的GPU、JCT或独占时间。

**强已有能力不能削弱。** 冻结scheduler/policy/batch三文件均与资格tree
匹配。native已有按priority差和token预算抢占、release和reset_for_retract；
当前trace未启用该flag。本地路径不能由offload_kv默认值推出每次整份
D2H：直接backup条件是decode disaggregation；后面还涉及缓存释放/回收，
实际保留或重算量未知。Agentix §4.2.2已有程序级MLFQ、多步调度、GPU候选
超额保留与合并交换[R25]，不能把简单续接优先、抢占或多留候选当新机制。
本轮没有启用这些能力，未测实际切换成本或证明更大并发可容纳。

另筛查预测工具结果并提前执行后继：Speculative Actions v2已包含API
response预测、后续调用预启动、多步扩展及可逆/不可见前提[R115]；
TomasuLLM v1已有工具action/observation推测、COW和按序验证提交[R116]。
原文范围与本地共享GPU成本分开，不外推其lossless/速度，也不把“先验证
再提交”重新立项；不恢复停止的共享workspace研究线或实现sandbox。

[V1627数据](../../experiments/reports/v1627_v79b_request_reentry_incumbents_20261018.json)/
[单位与强对照](../../experiments/reports/v1627_reentry_incumbents_scope_zh.md)
保留5个嵌套视图、原质量、输入源和执行快照。4项CPU含24个有限几何的
独立逐对交集对照，25个实际分区谓词与4份SHA、3份冻结blob通过。
下一机制仍须指出强普通方法之后的具体成本，并核算被延迟工作和切换；
当前只有成本定位和直接候选筛除，未达完整/goal验收，无GPU或runtime/
harness/任务/配置变化，保持active。

### 4.129 状态保存与执行准入：补齐原生和可编程系统的强基线

已撤下的跨上下文恢复候选及其区别讨论继续跳过。本轮只闭合4.128留下
的原生状态保留合同，避免把已有机制包装成新上下文抽象。

冻结源码表明`Req.detach_kv`交出原记录，`SessionSlot`保存/交回同一记录；
`ReqKvInfo`里的`req_pool_idx`仍指向实际请求映射行。`release_kv_cache`
在cache已接管记录后提前返回，不替该slot归还映射行；后续同会话请求
复用原行。`get_num_allocatable_reqs`又受行空闲量约束。因此保存状态、
释放执行容量和回收KV页不是同一个动作。这里只证明所有权合同，不把
它当当前trace中的物理容量瓶颈。

`release_session`才有相应私有页/行/状态和树引用释放，仍在途时原生
session close会延迟清理。本轮没有执行priority preemption与streaming
session的组合，不能由局部源片段宣布兼容、无损抢占或bug。V79b的普通
radix session身份不是流式slot，priority scheduling也关闭；普通抢占的
实际保留/重算仍未测，不推断整份丢失或整份D2H。

R64/Pie作者SOSP25 PDF重新归档并核对§4.1/5.2/8：已有显式KV页资源、
虚拟句柄、跨inferlet共享和命令队列；其资源过载实现按FCFS终止较新
inferlet，丰富准入/抢占/swapping列在后续方向。既不夸大其已实现能力，
也不忽略Agentix已有程序抢占/候选保留/交换，单独状态句柄/保存不立项。

[V1628源合同与文献边界](../../experiments/reports/v1628_preemption_state_contract_screen_zh.md)
记录8份冻结blob与2份作者资料SHA，无新性能测量或源码镜像测试。
没有改runtime/harness/任务/配置或重扫raw、启动GPU；原质量/资格不变。
若继续此区域，须拿出强普通方法之后的实际切换/重算成本及受损工作，
不能把在途配对暴露当可省JCT。未选新主机制，完整研究/goal保持active。

### 4.130 等待年龄的方向：生命周期已有强方法，阶段混合仍需物理证据

继续跳过已撤下候选的区别讨论。重读4.99–4.102确认，不将普通历史已能
识别的重复FULL周转重新当workflow独有收益；4.114也已筛查整调用稳定
执行，不重启其直接变体。新核对KVTether v1/2026-09-30 [R117]：
已有消息编辑/派生/一次性/保留、共享状态聚合和失效suffix处理；在prefill
完成进入decode后转Suspended，按MRU先驱逐最近suspend的条目。
其PD写回锚点与随机prompt/固定输出长度、记录工具等待的评测边界保留，
不把原文性能当本地真实语义/质量收益。

新`audit_reentry_age_order.py`复用五份封存派生来源，逐请求/作用域/输出量
对齐，分类器与计时源的raw前缀/祖先相同，不重扫raw。40718请求分为670
内部摘要、532普通流末调用和39516个闭合再请求间隔。间隔并不证明旧KV
仍有效或可恢复，压缩/终态/共享引用及实际容量尚未重建。

按首次已观测decode计龄时，工具类再请求面对的严格比较对象中72.15%
更老但需求更晚，spawn类18.86%；按native完成计龄为93.26%/34.64%。
两种锚点还改变比较对象数，不能把百分比差全归因于时钟。V1631独立
固定1547017对双方均已完成的对象，严格方向反转仅66691对/4.311%。
其中1434962个“更老但需求更晚”的对象有1229965个此前请求spawn，
占85.714%。类别均为事后标签；比例按调用对象对计，不当驱逐错误、
字节/等待/JCT占比或预测准确率。native首decode也不是实测D2H完成。

这使下一问题从改统一时钟转向：长依赖屏障与短工具等待混在同一idle
类别，是否真正在物理容量竞争中造成额外工作。不能由排序证据认定
KVTether性能差或直接新建phase policy；TokenCake图评分、Agentix、
MORI和KVTether合理适配须同样获得已知事件。先关联实际有效FULL页、
admission/victim与容量，强普通阶段/图方法后仍有损失才设计主机制。

[V1632方法、边界与文献](../../experiments/reports/v1632_reentry_age_and_lifecycle_baseline_screen_zh.md)/
[V1630间隔](../../experiments/reports/v1630_v79b_reentry_age_order_20261018.json)/
[V1631固定对象控制](../../experiments/reports/v1631_v79b_reentry_age_order_controls_20261018.json)。
7项CPU含30组逐对交集和24组固定对象控制，28实际来源/分区谓词通过。
原156项/质量/guard/measurement_complete/exit不改，无runtime/harness/
任务/配置/GPU变化；没有通过完整立项的主机制，研究/goal继续active。

### 4.131 排序接上物理前缀：parent假设降级，修正cache计数观测时点

本轮完成4.130要求的物理交集筛选，未建立新缓存策略或启动GPU。先读取
V79b实际应用快照c40d2f6的telemetry，与原资格hash及commit blob相同；
当前main文件不同，未用它解释旧行为。

**时点修正具有优先效力。** 原生`register_request`保存入队时刻，
`before_forward`首次看到请求才发`llm_submit`并采集cache字段，时间戳
回填入队时刻。因此V1626/V1627等处的`registered_host_hit_tokens`和
“登记Host”应读作带入队时间的首次服务cache观察，不能当入队已知在线
输入或从入队开始驻留的证明。历史JSON及数字保留；V1630/V1631排序没有
使用cache计数，不受这一命名/解释修正影响。

新`audit_reentry_physical_prefixes.py`各耗尽一次封存native170564044B及
前缀证据253422304B，hash相同。40718请求、38840精确旧输入探针、
142351 FULL/21020 Mamba真实Host释放与72754 FULL归因全部复现。
39516闭合间隙中38840为相邻原请求，676无探针；后者670带下一次compaction
标记、6无标记，均不作零损失。不同prefix endpoint在同请求内按坐标并集
去重，再交前次输入公共prefix，不以node ID推断同页。

spawn后201请求中75有正Host计数，FULL等价量2.421GB/0.396%；
工具37900请求中1951有正Host计数，593.447GB/97.071%。在已匹配且确认
属于相邻旧输入的回收坐标并集中，587.350GB等价量仅20 token落在cache
计数之外，spawn子集为0。它不是全程缺口/逐层重算上界；未匹配/过期/
无probe部分保持未知，混合状态匹配边界也不当FULL丢页原因。

匹配旧输入最新回收在前次完成之前、调用间隙、下一次登记之后的各自并集
等价量为584.292/1.805/1.253GB，类别可重叠，且前次完成之前未细分服务
阶段。spawn子集中在调用间隙最新回收仅4 token，未落在本次cache缺口。
最新prompt+output逻辑毛包络峰值13.460GB，不当实际驻留或安全空闲容量；
仍不能排除parent间接挤占。

**研究决定。** 长等待对象主导年龄比较，并未证明主导物理损失；下调统一
年龄/阶段切MRU-LRU/多保留parent直接变体。incoming admission所有权、
全部resident页和共享引用不全，不做虚构victim回放。KVTether/MORI、
TokenCake/Agentix和普通历史合理适配仍是强基线；继续寻找其后的额外
工作/迁移成本，不把本轮否定证据当目标完成。

[V1634源合同、结果与边界](../../experiments/reports/v1634_physical_reentry_evidence_and_clock_scope_zh.md)/
[V1633物理交集](../../experiments/reports/v1633_v79b_reentry_physical_prefixes_20261018.json)。
7项CPU含100个独立坐标集合，48实际来源/分区谓词通过；原质量/guard/
measurement_complete/exit保持，无runtime/harness/任务/配置/GPU变化，
尚无通过完整立项门槛的主机制，完整研究/goal保持active。

### 4.132 层与专家级异速：直接执行原语已有，不能仅靠更细优先级立项

本轮从4.131已降级的缓存年龄线转查执行候选：在真正必需的token上，
允许关键agent以层/专家粒度推进，同时组合其他agent的工作。没有虚构
draft、未来route或增加分支，和旧推测验证预算筛选分开。

新核对三份原始全文及版本：QLLM/R118已有逐专家队列、KV/hidden/路由及
部分计算保留、抢占与自定义策略；AMoE/R119已有逐层µ-queue、依赖汇合、
异步重批与defragging；BatchGen/R120已有序列coroutine的yield/combine/
partition/migrate及Host/GPU状态管理。BatchGen还评估受SCT/TPOT限制的
批推理，不能说它完全不考虑延迟；其主BCT目标与在线动态agent仍分开。
也不将QLLM的HF原型或AMoE分布式EP收益直接外推本地fast native。

复读已封存V1544/V1529，不重扫raw：decode worker P50/P90为
11.345/25.301ms，prefill15.590/98.170ms；全部40718请求入队到首launch
1238.721/8147.397ms，首launch到序列worker36.246/45.490ms。worker是
含提交/依赖/观测的区间，不是CUDA；批次分位不按到达加权，不将这些
不同人口统计相减/求比当可省JCT。长尾最大值保留，未归因。

在相同候选、容量、状态保留与优先级下，仅细化当前一个不可抢占区间时，
该次额外等待受其剩余部分约束；这不是整个queue/多在途batch或JCT界。
粗细方案若有不同重算/迁移成本，须另计，不能都归于粒度。
两份冻结native policy/batch与资格树相同，普通抢占路径已存在，但本轮
未启用/验证恢复容量或性能。秒级等待仍须查准入/资源及真实竞争。

[V1636执行候选、原文范围与核对](../../experiments/reports/v1636_fine_grain_execution_candidate_screen_zh.md)
归档三PDF/文本与SHA，完成8项身份/报告绑定/人口/blob核对，不新增单位
测试或性能结果。撤下“专家队列+agent优先级”和普通真实token跨层重组
直接变体的实施优先级；若继续执行方向，须证明Agentix/TokenCake获得
同样信息并结合上述强执行能力之后仍有具体额外代价。
不把未评测agent、接口缺失或future-work条目当领域空白。无runtime/
harness/任务/配置/GPU变化，主机制尚未选，完整研究/goal保持active。

### 4.133 公开自然 Trace 普查：先确认动态依赖和观测语义

继续遵守用户跳过撤下候选差异讨论的决定。本轮先固定作者公开库
`callanjfox/kv-cache-tester@94f6046` 的完整树，再以读取内容前固定的24个
等距位置筛查。样本2057请求无显式child，随后明确扩展同一仓库739份的
六分片普查，保留原计划与样本，不做按结果挑选。全量59937请求，其中
root59204、child733；19份trace显式包含70 child，66 completed、
4 async_launched。只保留43份raw/59.623MB，全库657.249MB不落盘。

最大显式深度1及连续3个标记不当原始DAG深度/fanout。独立固定生成器
`agentic-coding-analysis@15e88f7` 源码确认：child链接来自parent工具
回执、child相对时间以其首模型请求归零、包装不保留绝对起点，且child
处理不递归构建链接。该源版本不是已证实的corpus生成版本，边界另列。
replay在pending_subagents非空时统一等待，不能将其施加的barrier当
原程序all-of JOIN或系统损失；4个async记录没有可恢复绝对重叠时长。

标记前parent与child首请求的声明模型关系为62不同/8相同；后者代理
hash LCP全为0。local family共享ID按固定README作条件比较，不跨trace；
生成器用规范化JSON/GPT-4代理tokenizer/带salt链hash，并非原生KV等价。
完整形状无非相邻复现也不否定短前缀复用。保留输入缩短/模型改变/状态
原值，不推物理容量、收益、质量或生产代表性。

[V1642结果与证据边界](../../experiments/reports/v1642_public_dynamic_trace_evidence_screen_zh.md)/
[V1640全量](../../experiments/reports/v1640_public_trace_catalog_shapes_20261018.json)/
[V1641来源核对](../../experiments/reports/v1641_public_agent_trace_provenance_20261018.json)。
扫描器7项CPU测试已通过，739路径/6分片/43留存blob与固定来源核对通过。
不采用合成回放证明新机制，下一步找保留真实输入/绝对时间/Task与等待边
的自然记录，先证额外系统成本，不制造任务。未改runtime/harness/任务/
配置或启GPU，没有通过完整验收的新主机制，完整研究/goal保持active。

### 4.134 自然原始事件：区分 API 身份、未提交间隔与异构模型问题

按固定作者树，在读内容前选择完整root+4 child JSONL，后按同树追加
唯一配对API DB。8份来源共15.847MB均验Git blob/大小；SQLite只读、不
执行下载代码。93行中92完整响应，90精确message.id匹配（root6/child84），
2未链接完整响应及1 null保留。302条root progress不当额外模型请求，
spawn按tool_use_id/agentId配对而非完成顺序。proxy秒级时钟不报亚秒收益。

四个Explore为Haiku、root为Opus，观察到同一消息一轮四分支，无child
再spawn或async回执，不将单样例泛化为生产。child报告131.908–164.129秒，
API窗口外51.020–79.872秒只是混合间隔，不当CPU工具或GPU独占时间。
最后child回执到parent下次API119.689秒，该请求含四回执；它是尚未提交
的间隔，不当GPU排队，也不猜限流/退避原因。该API的120.852秒另列。
尾部有新用户请求、null和中断，整段跨度不作单个完成任务JCT。

Scepsy [R121]已针对多模型agent workflow构造Aggregate LLM Pipeline，
联合fractional GPU/TP/replica分配；直接异构角色分配不立项。保留其
聚合profile、长工具范围限制及16GPU/RAG/beam评估边界，不外推本地
Qwen3.5或声称所有在线缓存/模型迁移已解决。本自然样例没有物理模型/
KV/服务数据，也不能反证聚合假设。下一步换有显式动态依赖与运行事件
的自然记录，先证强组合之后的额外系统成本。

[V1645事件与方向筛查](../../experiments/reports/v1645_natural_event_and_multimodel_direction_screen_zh.md)/
[V1644原始对齐](../../experiments/reports/v1644_natural_agent_event_sample_20261018.json)。
5项CPU涵盖时区/身份/progress/未知保留/区间并集/乱序回执，8来源/90身份
核对通过。未改runtime/harness/任务/配置或启GPU，没有通过完整立项
门槛的新主机制，完整研究/goal保持active。

## 5. 既有候选一：降低上下文摘要和重建的计算量

这是目前最有规模证据的性能对象。当前 `_summary_input` 把历史消息转成 XML，
放进一个独立摘要 prompt；摘要请求登记的 prefix hit P50 只有 192 token，
uncached P50 为 21,451 token。摘要后普通请求的 uncached P50 为 8,556 token。
原始历史已经在 agent 请求中计算过，换成另一种输入后需要重新处理。

具体探索：

1. 验证摘要请求能否保留原对话的真实 token 前缀，在末尾附加总结任务。必须核对
   system/tool schema、chat template、thinking 和 tool_choice 的实际渲染，不能
   仅因消息内容相似就宣称 KV 可复用。输入改变后的摘要保真度必须独立检查。
2. 将已确定的工具事实和原始日志作为可恢复记录，只对新增决策和未决事项生成
   增量总结；衡量是否真正减少摘要生成和再次输入的 token，而不是把日志隐藏后
   迫使模型多次回读。摘要阈值不能只调大来推迟成本。
3. 分开测摘要本身、压缩后首次请求、后续 decode 与重复调用。上下文缩短也可能
   带来信息损失或更多后续生成，必须核对完整任务和失败。

已有工作边界：SmoothAgent [R1] 已覆盖上下文变换的提前计算，且明确包括
summarization 与 subagent isolation/流式指令预构建；不能声称它只研究单 agent。
TokenPilot [R2] 已覆盖稳定 prefix、工具输出精简及可恢复原始内容。因此
“异步摘要”“缓存友好的 prompt”或“给工具输出一个引用”本身不能算新贡献。
这些应先作为强工程对照。若它们已拿到全部收益，就不围绕它们包装新论文。

## 6. 既有候选二：让父子 Agent 复用有版本依据的调查与验证结果

历史候选：4.95已按用户要求停止共享状态/验证失效方向的继续探索。

直观问题是：child 已经读取某段代码或完成一项验证，parent 或 sibling 又为同一
问题产生新调用；另一方面 parent 编辑后，旧结论可能已经过时。需要区分“已经
知道且仍成立”“已经知道但版本过期”和“从未确认”，不能只记一段自由文本报告。

例如 child 在版本 v0 完成 `pytest tests/test_a.py`。parent 修改了其他文件时，
是否仍可用这条证据取决于测试实际依赖及环境；若修改了被测实现，旧通过结果
只能作为 v0 的历史事实，不能被总结成当前版本通过。相同命令不是充分条件。

候选实现是记录 `(工具参数、实际依赖版本、结果、原始日志引用)`，让 sibling/
parent 能取得同一份记录，必要依赖改变时使结论失效。对确定的文件读取可先用
文件内容版本；任意 shell、网络、时间、随机性和未知读写集仍执行原工具。
不能通过拒绝重复调用、强制 RETURN 或把历史结果冒充本次执行来获得收益。

研究问题是这些记录能否同时减少有效工具执行、重复模型生成和摘要输入，并在
动态编辑、JOIN 与 compaction 后仍保持正确。必须实际追踪结论使用和失效，
不能把当前V79b的1539次root同source结果、观测epoch不变的重复直接解释为
摘要丢失所致：目前没有因果证据。该指标也不证明完整依赖或环境未变。

已有工作边界：TVCache [R3] 已有状态感知工具结果缓存、工具调用历史的最长前缀
匹配和并发共享。候选需要与它的合理适配以及简单版本缓存比较。不同调用历史、
共享且持续修改的 workspace、依赖级失效以及模型上下文中的事实复用，是需要
验证的差别，不是“此前无人解决”的结论。数据库增量维护/MVCC 等也是必要对照。

这条路线更贴近动态多agent。最新完整V79b的跨agent同输入后续工具为2406次、
墙钟累计161.446秒，不能全部复用；307.60秒属于§2的历史V73。4.90进一步确认
部分正文相同的911次/37.567秒，child正文覆盖仍低，这些不能当全部机会或收益。
单做工具缓存不优先；只有同时减少实测重复推理或上下文计算，并超过已有方法
的合理适配，才值得升为主线。

## 7. 既有候选三：利用当前代码与已产生的结果加速模型生成

本轮共输出 8,156,973 token，其中 child 4,469,761、root 3,046,689、摘要 640,523。
可以测试从当前 repository、已可见工具结果及已完成 child 的文本中取草稿，
交给目标模型验证；不依赖预测 RETURN。草稿只能使用当时已存在的信息。

优先复用成熟推测解码实现，核对当前混合模型的验证、拒绝回滚和 Mamba 状态成本。
离线字符串重复只能说明有候选，不能代替目标模型接受率和实际服务成本。还需
与普通 n-gram/后缀草稿及 engine 可用的推测方式比较，不能手写简化内核后比较。
batch=48 的当前并发、长上下文、验证额外计算和状态占用均可能使收益消失。

已有工作边界：AgSpec [R4] 已针对 coding-agent pipeline 做检索草稿、共享语料与
agent 相关草稿长度控制；其生成吞吐结果不能直接当本项目 JCT 收益。SpecReason
[R5] 已研究小模型草稿/大模型检查的推理加速。普通检索草稿、按 agent 区分接受率
或换小模型本身都不能算新意。本方向目前是独立性能候选，非已成立论文贡献。

## 8. 当前系统研究的执行顺序

4.107–4.108补充当前/goal验收：先寻找并逐项论证具体系统候选，低收益、
不可实现或被强已有方案覆盖则换下一项。至少一个保留候选须具备自然成本、
机制与正确性边界、CPU验证和强对照实验设计；目前尚未通过，不以8项测试、
文献列表或页大小调优替代完成。全部GPU继续停止，不自动接续旧候选。

研究范围是系统，具体主机制仍待证据选择。4.95覆盖先前三条问题线的安排：

1. 先深化分支失衡：沿同一JOIN分开固有任务工作、实际观测工作、首次服务前
   等待、worker与上下文处理。4.83和4.85的约2倍工作比并未解释因果；4.86
   补齐提交时结构标签和batch组合，4.87将共同阶段的不同批现象定位到首次
   串行区间之前或尚无登记请求。4.88区分真正的首次dispatch，单剩成员P50
   仍有1.424秒，累计等待多发生于接近既有并发上限的生命周期集合。下一步查
   dispatch前具体约束，不将生命周期、raw batch或已登记当作容量/ready证据。
   检验紧急程度与混批候选时，对照合理调优native、程序级调度、普通
   动态混批及二者组合，同时核算长尾、公平性、额外控制与驻留成本。
   4.89已核对FIFO dispatch及等待期间的多workflow竞争，未见后到插队或
   少数workflow超大并发。350个委派任务已恢复，下一步沿具体职责、工具结果、
   错误路径及上下文处理解释实际工作差异，不由角色平均值直接设优先级。
   4.90纠正callback摘要含call ID的内容解释，补齐6026个错误循环的唯一后继；
   继续查具体工作路径，不能把107万后继输出token全当额外或可省计算。
   4.92将约2倍累计等待拆为调用次数和均值；后/先平均逐调用等待P50为
   1.095倍，仍受全生命周期阶段混杂。继续实际高负载约束和上下文处理，
   不由代数分解认定固有工作或不公平。
   4.93收束缺逐候选容量数据的旧trace边界，另将服务后空档99.549%定位为
   中间其他prefill的worker暴露；不当CUDA、总JCT或准入原因。
   4.94补齐全请求提交时可用输入和TokenCake实际JOIN继承/图进度策略；
   给强对照同样信息，仍须找具体未覆盖选择，不用未来关键路径标签。
2. 继续跨资源反馈：4.84已关联38687个完整循环，普遍秒级工具风暴解释降优先级，
   不撤下整条方向。把工具、summary与后继请求纳入实际依赖，按阶段、过去
   可观测信息与服务边界检验因果，不把返回密度相关或长bridge全算工具竞争。
   普通闭环调用到达已在Agentix中讨论，需要进一步指出具体阶段反馈的代价。
   4.92补齐全部38687个循环的原生时序，高负载排队与尾部差数个数量级；
   分开阶段、工具和summary，不按全程中位数直接设置续接槽或普遍提优先级。
   4.93观测到多prefill连续片段及summary成本，继续核对是否存在强普通混批/
   程序调度组合之外的具体反馈，不把一般prefill/decode干扰当新贡献。
3. 共享状态交错/失效验证研究线按用户要求停止；保留历史产物，不继续恢复
   工具正文或追查文件竞争。新增筛选动态依赖下的异速批执行：先证明一个
   改变实际推进速度的选择，超出程序排序、普通混批、TAPER、AdaServe、
   agent草稿策略及合理组合。V79b未记录输出token IDs，不能伪造接受率或
   GPU服务曲线；先做案例、必要条件与正确性论证，不直接接新控制器或开GPU。
   4.96已补提交时依赖与实际执行/原质量分组；正确完成任务存在持续生成，
   但案例不能只取最重的失败轨迹，分阶段成本也不能替代强方法反例。
   4.97下调sole加大草稿的直接变体；TAPER可替换utility和合理组合纳入对照，
   终段重叠7/175（原正确完成5/103）不支撑专用多分支终段机制，不因此开GPU。
   4.98进一步要求异速相对细粒度暂缓及相同推测能力证明额外收益，目前
   不作为核心实现路线。KV问题从物理共享、存活历史、汇合切换和有效性出发，
   与原生session及已有方法比较，不恢复共享workspace线或旧三个机制。
   4.99–4.100已确认真实FULL周转与610.957GB重复回收区间下界，但匹配部分
   只有0.218GB重算代理且55.785%回收量未知。先检验普通工作集/写入准入
   仍未覆盖的动态依赖差异，不按大字节直接建设parent保护或预取机制。
   4.101再确认重复区间子集最大容量66.759GB，以及至少565.370GB Host
   回收仅释放仍有HBM副本的冗余层。子集画像不是完整保留方案，回收量不
   当历史丢失；须与通用复用准入/副本策略比较后再选动态workflow机制。
   4.102将普通历史信号冻结到前一次回收，覆盖544.229GB/89.078%的相邻
   重复区间；仅为信息覆盖，不是策略或节省回放。阶段性收束重复FULL周转
   作为独立主创新，下一候选先给强历史/已有workflow对照仍失效的带成本案例。
   4.103区分当前brief委派和自然历史fork，补入阶段压缩/共享压缩/非prefix
   通信及FlashAgents强对照；压缩视图相互作用仅待证伪，固定集合的共享页
   下界不算新机制。未验证自然需求与强组合残差前不改harness或开启GPU。
   4.113已用完整自然论文/多问题及实测权重筛选直接共同选集变体；简单
   聚合吃掉大部分增量，贪心还增加逻辑读取与选集成本，不进实现队列。
   保留未来质量和其他算法的开放边界，不用受控多query冒充动态fork证据。
4. 保留已收敛的工程/表示筛选及强对照，不重复当作主创新。旧输入prefix修复、
   session/tokenization、摘要和普通操作重叠继续受已有方法约束；参数字节、
   worker区间与注册队列分别不等于HBM流量、纯CUDA或可准入供给。只修改本方
   缺失配置或自身开销的改进，匹配放入native后再看workflow信息的额外收益。
5. V75/V76/V77/V78b/V79b均已收集结束，V79b官方CPU评分也已结束并复核，
   不再重启评分；原156项、145项JCT资格、11项guard、exit1及
   measurement_complete=false保留。GPU保持停止，无自动接续。找到具有
   自然成本、明确系统位置、强对照剩余差异及同质量收益预期的机制后，再按
   用户后续授权安排独立候选验证。最终验收平均workflow JCT、有效完成吞吐、
   质量、全部终态、长尾和资源成本。

这不是恢复旧的“可消费状态”论证，也不要求先预设三项创新。若当前负载只提供
很小增量，先判断真实应用是否存在有规模的系统需求，再调整 workload；双方
使用相同应用和自然需求，不能强制无用 spawn、重复输出或异常循环创造收益。

### 暂不优先与历史探索

工具执行环境值得保留诊断，但当前 sandbox execute 的锁等待 P50 约 0.00124ms，
不是主要锁竞争瓶颈。execute 墙钟 P50 约 402ms，包含真实子进程工作，尚未分出
可移除启动开销；不能把这些耗时都称为 Docker 或 Python 初始化成本。
先不建设新的工具调度器、常驻解释器池或零拷贝框架。

此前候选的筛选记录，受本节系统优先约束：

1. 已完成：取消旧候选自动启动，审计最新完整 trace，并用真实 batch 核对输入计算。
2. 摘要格式原型的 CPU token 条件验证已完成，但暂不继续其 GPU 实验。其工程
   改进须以 SmoothAgent/TokenPilot 所涵盖的强对照约束，不自动计为论文贡献。
3. 既有候选二相关共享状态、失效验证和重复调查研究已按4.95停止，保留旧
   证据，不列作待办。
4. 对既有候选三先测可验证草稿与混合模型的真实成本，不能照搬其他模型/较小 batch
   的论文加速比。使用正确任务，避免由异常重复循环制造接受率。
5. 选定后再做一个独立候选。native 加相同 harness 工程修复、强简单方法与新机制
   必须分列；不拿自身早期低效实现或未评分任务数量证明创新。

新 workload 可以增加真实协作编辑/独立验证和自然层级，但双方必须使用同一应用，
不能强制无用重复、重复事实或无效 spawn 来创造收益。最终以官方任务正确率、
全部到达任务的 JCT/完成曲线、长尾和有效吞吐共同判断。

## 参考资料

以下均按实际阅读的预印本版本比较，不视为其已获特定会议录用。

- [R1] SmoothAgent，arXiv:2607.00151v1，第 3.4/3.5 节。
  `https://arxiv.org/html/2607.00151v1`
- [R2] TokenPilot，arXiv:2606.17016v1，第 3 节。
  `https://arxiv.org/html/2606.17016v1`
- [R3] TVCache，arXiv:2602.10986v1，第 2.3/3 节。
  `https://arxiv.org/html/2602.10986v1`
- [R4] AgSpec: Pushing the Limits of Retrieval-Based Speculative Decoding in
  Coding Agent Pipelines，
  arXiv:2610.01108。早期核对v1，本轮另核对v2 §3.1的跨调用/agent会话语料、
  workspace/global corpus及在线长度调整；不混用版本实验数字。
  `https://arxiv.org/html/2610.01108v2`
- [R5] SpecReason，arXiv:2504.07891。
  `https://arxiv.org/abs/2504.07891`
- [R6] Marconi: Prefix Caching for the Era of Hybrid LLMs，MLSys 2025；
  核对作者论文与 artifact，不能把混合状态 checkpoint admission/eviction 本身
  当作未解决问题。`https://arxiv.org/abs/2411.19379`
  `https://github.com/ruipeterpan/marconi`
- [R7] TokTier，早期核对题名Exact Stateful Tokenization for Agentic LLM
  Serving；2026-10-17再核对当前原始摘要题名Exact Session-Incremental
  Tokenization for CPU/GPU LLM Serving。作者实现与论文版本/实验口径不混用。
  `https://arxiv.org/abs/2607.29678`
  `https://github.com/asu-idi/toktier`
- [R8] Delta Tokenizer: Reference-Identical Tokenization of Session Deltas and
  Segment-Parallelism for Long Prompts on Multicore CPUs，2026-10-14 提交；
  早期核对摘要，本轮另核对v1正文的精确增量与segment并行机制；未复现实验
  或完整实现。`https://arxiv.org/html/2610.19801v1`
  `https://arxiv.org/abs/2610.19801`
- [R9] Lachesis: Lifetime-Aware KV Cache Placement for Agent Serving across
  HBM and High-Bandwidth Flash，arXiv:2610.08378v1，2026-10-06；
  本次核对原始摘要和 PDF 第 4/5 节，区分 HBF 模拟与实机性能。
  `https://arxiv.org/abs/2610.08378v1`
- [R10] EfficientAgent: What Makes KV Cache Offloading Work for Concurrent
  Agents?，arXiv:2609.33762v1，2026-09-27；本次核对原始摘要和 PDF
  第 3/4 节的 working-set 估计、容量条件写入规则与去重边界。
  `https://arxiv.org/abs/2609.33762v1`
- [R11] TokenDance: Scaling Multi-Agent LLM Serving via Collective KV Cache
  Sharing，arXiv:2604.03143v1；本次核对第2.2、4.1–4.4、6.6节，保留其
  PIC 等价对象和模型适用性限制。`https://arxiv.org/abs/2604.03143`
- [R12] SAGA: Workflow-Atomic Scheduling for AI Agent Inference on GPU
  Clusters，arXiv:2605.00528v2；本次重新核对第3–6节的 AEG、概率保留、
  session-affinity batching 和 agent 级公平。`https://arxiv.org/abs/2605.00528v2`
- [R13] AgentSpec: Speculative Decoding for Batch Inference of LLM Agents，
  arXiv:2608.24004v1；本次核对第3.2–3.4节。
  `https://arxiv.org/html/2608.24004v1`
- [R14] ReplaySSM: Cache SSM Inputs, Not State，arXiv:2610.13778v1；
  正确来源保存于V1260，替代此前错误编号。
  `https://arxiv.org/html/2610.13778v1`
- [R15] TreeWY: Speculative Verification for Gated DeltaNet Hybrids，
  arXiv:2608.20961v1；旧V1199误标为ReplaySSM的内容实际来自本文。
  `https://arxiv.org/html/2608.20961v1`
- [R16] Hydragen: High-Throughput LLM Inference with Shared Prefixes，
  arXiv:2402.05099v1，原始HTML与标题保存于V1260。
  `https://arxiv.org/html/2402.05099v1`
- [R17] Semantic Parallelism: Redefining Efficient MoE Inference via
  Model-Data Co-Scheduling，arXiv:2503.04398v5；框架名Sem-MoE。
  `https://arxiv.org/html/2503.04398v5`
- [R18] Just Let Linear States Forget the Distant Past: Prefix Caching via
  Suffix Replay for Hybrid LLMs，系统名SuffixReplay，arXiv:2609.33477v1；
  本轮再次核对原始摘要及正式标题，未复现完整实现、质量或当前系统收益。
  `https://arxiv.org/abs/2609.33477`
- [R19] CachedAttention: Cost-Efficient Large Language Model Serving for
  Multi-turn Conversations。arXiv当前摘要与v1名称AttentionStore分别核对，
  本次机制核对v1 §3.2.2，不混用不同版本实验数字。
  `https://arxiv.org/abs/2403.19708`
  `https://arxiv.org/html/2403.19708v1`
- [R20] LMCache官方Async Loading文档。本页明确指旧进程内模式，
  已deprecated；不能当作最新MP实现的完整能力清单。
  `https://docs.lmcache.ai/kv_cache/async_loading.html`
- [R21] NVIDIA Dynamo KVBM官方overview与组件文档；按各自标明版本阅读，
  不由接口描述推断目标负载的性能。
  `https://docs.nvidia.com/dynamo/v1.4.0/knowledge-base/modular-components/kvbm/overview`
  `https://docs.nvidia.com/dynamo/v-0-8-1/components/kvbm/design-deep-dive`
- [R22] TokenCake原始摘要与作者仓库；V1546另固定1901ebf并核对6份实际源码
  的Git blob，区分原论文和vLLM迁移；不据仓库标题认定会议录用或本地复现结果。
  `https://arxiv.org/abs/2510.18586`
  `https://github.com/pku-lemonade/TokenCake`
- [R23] CONCUR原始摘要与v1正文，二者标题存在版本差异；
  本次仅比较反馈准入机制，不沿用跨论文加速比。
  `https://arxiv.org/abs/2601.22705`
  `https://arxiv.org/html/2601.22705v1`
- [R24] Parrot: Efficient Serving of LLM-based Applications with Semantic
  Variable，OSDI24；本轮核对原始摘要及API目标，未声称完整复现。
  `https://www.usenix.org/conference/osdi24/presentation/lin-chaofan`
- [R25] Agentix: An Efficient Serving Engine for LLM Agents as General Programs，
  NSDI26；早期核对USENIX摘要，本轮V1488另保存prepub全文/hash并核对§3.2、
  §4.1–4.3、§5和附录A.1，未复现其性能或认定当前引擎可直接移植。
  V1524补核§3.1闭环到达与§4.2程序级等待，不把这两者本身作为缺失能力。
  `https://www.usenix.org/conference/nsdi26/presentation/luo`
  `https://www.usenix.org/system/files/conference/nsdi26/nsdi26spring_luo_prepub.pdf`
- [R26] Murakkab: Resource-Efficient Agentic Workflow Orchestration in Cloud
  Platforms，OSDI26；本轮核对USENIX原始摘要，按其跨层编排范围比较。
  `https://www.usenix.org/conference/osdi26/presentation/chaudhry`
- [R27] NanoFlow: Towards Optimal Large Language Model Serving Throughput，
  OSDI25；早期核对原始摘要，本轮另核对正文的nano-batch执行、§6.3在线
  与§6.6跨模型评估，不将其加速数字用于本项目估算。
  `https://www.usenix.org/conference/osdi25/presentation/zhu-kan`
  `https://www.usenix.org/system/files/osdi25-zhu-kan.pdf`
- [R28] Parallel Context Compaction for Long-Horizon LLM Agent Serving，
  arXiv:2605.23296v1；核对原文分块并行及任务/缓存感知方案，不混用摘要加速
  与端到端任务收益。`https://arxiv.org/html/2605.23296v1`
- [R29] Akashic: A Low-Overhead LLM Inference Service with MemAttention，
  arXiv:2607.05708v1；核对原文chunk-granular memory maintenance、
  cross-chunk inference及物理共访问布局，不称其研究了prefix KV reuse。
  `https://arxiv.org/html/2607.05708v1`
- [R30] AgentFork作者仓库README，早期及V1505再次核对sandbox与KV fork；
  后者固定commit 73f7d537f505489a57ca0820a9a350e2b848931f，另核对单winner及
  multi-winner merge限制。是开源能力对照，不据README认定会议录用或当前项目性能。
  `https://github.com/sachinkesiraju/agentfork`
- [R31] DeltaBox: Scaling Stateful AI Agents with Millisecond-Level Sandbox
  Checkpoint/Rollback，arXiv:2605.22781v1；核对DeltaFS动态overlay及DeltaCR
  incremental dump/frozen template fork机制。
  `https://arxiv.org/html/2605.22781v1`
- [R32] Atomix: Timely, Transactional Tool Use for Reliable Agentic Workflows，
  早期核对arXiv:2602.14849v1，V1505另保存v2 HTML并核对工具effect事务、
  read/plan/write版本验证、frontier及scope/version边界，
  不认定已复现完整能力。错误编号2605.22663实际是Therm-FM，不作为本项目来源。
  `https://arxiv.org/abs/2602.14849`
- [R33] PatchKV: Efficient KV Cache Recovery for Dynamically Edited LLM
  Contexts，arXiv:2609.26219v1；核对原文相邻版本模型、dirty set及恢复路径。
  `https://arxiv.org/html/2609.26219v1`
- [R34] Budgeted KV-Cache Repair for Evolving Contexts，arXiv:2610.17338v1；
  核对§3、§4、附录7.1与14的混合状态语义/误差及评估单位。
  `https://arxiv.org/html/2610.17338v1`
- [R35] Budgeted Cache Repair for Cross-Context KV-Cache Reuse，
  arXiv:2610.02233v2（2026-10-08）；核对原文reuse质量成本、预算单位和
  三种repair layout，摘要入口指向v2，不混用v1。
  `https://arxiv.org/html/2610.02233v2`
- [R36] When KV Cache Reuse Fails in Multi-Agent Systems: Cross-Candidate
  Interaction is Crucial for LLM Judges，ACL2026长文327；本次核对作者原始摘要，
  不将judge选择一致性结果直接当成coding任务质量。
  `https://aclanthology.org/2026.acl-long.327/`
- [R37] TAPER: Regulating Branch Parallelism in LLM Serving，
  arXiv:2605.06914v1；4.95更正此前题名，核对额外分支影响、逐步宽度控制与
  共享前缀下的计算/内存解耦边界。不据其应用范围断言不可适配多轮agent。
  `https://arxiv.org/html/2605.06914v1`
- [R38] OpenR1-Math-220k官方数据卡，样本含同问题的多条推理回答；
  本轮镜像来源、固定提交、LFS哈希与派生样本另记V1463，不冒充当前coding数据。
  `https://huggingface.co/datasets/open-r1/OpenR1-Math-220k`
- [R39] Hugging Face Dataset Viewer官方rows/parquet文档；
  实际成功采集来自固定提交Parquet，不将失败的viewer请求称为数据证据。
  `https://huggingface.co/docs/dataset-viewer/rows`
  `https://huggingface.co/docs/dataset-viewer/parquet`
- [R40] Prism: Cost-Efficient Multi-LLM Serving via GPU Memory Ballooning，
  arXiv:2505.04021v3；以及kvcached官方README，读取日期2026-10-17。
  上游兼容性不等于本地冻结引擎验证，不沿用其性能数字作为本项目预期。
  `https://arxiv.org/html/2505.04021v3`
  `https://github.com/ovg-project/kvcached`
- [R41] Pythia: Exploiting Workflow Predictability for Efficient Agent-Native
  LLM Serving，arXiv:2604.25899v2，核对§3–4。
  `https://arxiv.org/html/2604.25899v2`
- [R42] Idleness is Relative: Exploiting Tool-Call Idle Windows for Offloading
  in Agentic Systems with MORI，arXiv:2606.00866v1，核对§2–4。
  `https://arxiv.org/html/2606.00866v1`
- [R43] CrossPool: Efficient Multi-LLM Serving for Cold MoE Models through
  KV-Cache and Weight Disaggregation，arXiv:2606.24506；本轮核对原始摘要，
  未声称复现。`https://arxiv.org/abs/2606.24506`
- [R44] ThunderAgent: A Simple, Fast and Program-Aware Agentic Inference
  System，arXiv:2602.13692；按程序/工具生命周期管理范围设强对照。
  `https://arxiv.org/abs/2602.13692`
- [R45] Breaking the Tradeoff: Elastic and Isolated GPU Sharing with Ghost。
  作者页面确认标题及来源；PDF本轮403，不据二手摘要推断机制缺口。
  `https://web.cs.ucla.edu/~easonliu/`
  `https://web.cs.ucla.edu/~harryxu/papers/GVM.pdf`
- [R46] PARTNR官方仓库，固定提交
  ddfff19f4b6c098a31edea4d19e7b75db72433c2；5个源码文件及SHA256见V1467。
  `https://github.com/facebookresearch/partnr-planner/tree/ddfff19f4b6c098a31edea4d19e7b75db72433c2`
- [R47] Towards efficient and practical GPU multitasking in the era of LLM，
  arXiv:2508.08448v1，核对§3.2–3.3；这是vision论文，不称为完整实现。
  `https://arxiv.org/html/2508.08448v1`
- [R48] PyTorch官方empty_cache API与v2.9.0源码docstring，读取日期2026-10-17；
  stable页面返回重定向，语义核对实际依据tagged源码。未据此认定PARTNR运行
  版本，或测算当前应用可回收字节。
  `https://docs.pytorch.org/docs/stable/generated/torch.cuda.memory.empty_cache.html`
  `https://raw.githubusercontent.com/pytorch/pytorch/v2.9.0/torch/cuda/memory.py`
- [R49] ELDR: Expert-Locality-Aware Decode Routing for PD-Disaggregated
  MoE Serving，arXiv:2607.00466v2；核对正文§4–5的签名与多worker放置，
  明确不改变kernels/batching；未复现性能。`https://arxiv.org/html/2607.00466v2`
- [R50] MoE-Gen: High-Throughput MoE Inference on a Single GPU with
  Module-Based Batching，arXiv:2503.09716v1；核对§4的模块分批与离线offload
  范围，不将其吞吐数字用于agent收益估算。`https://arxiv.org/html/2503.09716v1`
- [R51] JointServe: Serving Multi-Model Agents on Memory-Constrained GPUs，
  arXiv:2610.19822v1；核对正文及附录中的joint residency、工具意图、
  依赖/资源处理，未复现性能。`https://arxiv.org/html/2610.19822v1`
- [R52] GVM: OS-Level GPU Virtualization for Mixing Interactive and Batch
  Workloads，作者仓库a7d68031d50b5b59625fbccd2ae65a700bcb9fb5中的PDF及README；
  读到的实际题名与R45的Ghost不同，未确认两者版本关系或会议状态。
  `https://github.com/ovg-project/GVM/tree/a7d68031d50b5b59625fbccd2ae65a700bcb9fb5`
- [R53] MLAgentBench官方仓库，固定commit
  5d71205cc20a8e95d43aa7cb7120e89ca3323e31。核对environment、执行工具和
  CIFAR-10训练源，未执行或测量显存。`https://github.com/snap-stanford/MLAgentBench`
- [R54] SWE-Serve官方仓库，固定commit
  84d8fd92739a4a76ceddba56e5a70605921c4ad2。核对README、GPU compose/环境及
  一个task/verifier；源码固定不等于benchmark release资格，未执行任务。
  `https://github.com/NVIDIA/swe-serve`
- [R55] Orca: A Distributed Serving System for Transformer-Based Generative
  Models，OSDI2022；本轮保存原始PDF/hash并核对§3的selective batching，
  未将其性能外推当前模型。`https://www.usenix.org/system/files/osdi22-yu.pdf`
- [R56] Taming Throughput-Latency Tradeoff in LLM Inference with Sarathi-Serve，
  OSDI2024；核对§4及§5.4的混批、chunk和预算边界，未复现当前负载收益。
  `https://www.usenix.org/system/files/osdi24-agrawal.pdf`
- [R57] Fast State Restoration in LLM Serving with HCache，EuroSys2025，
  DOI10.1145/3689031.3696072；保存作者PDF/hash并核对§3–5及§7，
  比较逐层hidden恢复、混合路线与MQA/GQA边界，不外推其性能。
  `https://chenyoumin1993.github.io/papers/eurosys25-hcache.pdf`
- [R58] Niyama: Breaking the Silos of LLM Inference Serving，
  作者PDF标明arXiv:2503.22562v1；核对§3.2–3.4及§3.6的QoS/deadline、
  dynamic chunk/mixed batch、混合优先级与过载处理，未复现性能。
  `https://www.microsoft.com/en-us/research/wp-content/uploads/2025/04/niyama.pdf`
- [R59] Cortex: Workflow-Aware Resource Pooling and Scheduling for Agentic
  Serving，作者PDF标明arXiv:2510.14126v1，共3页；核对§2阶段pool/
  workflow slack与NL2SQL示例，区分§3后续方向，未外推为单GPU控制器。
  `https://www.cs.columbia.edu/~kkaffes/papers/cortex-agentic-serving-saa25.pdf`
- [R60] SGLang官方Hyperparameter Tuning，2026-10-17核对max-running、
  memory及调度调优边界；当前实验以冻结source和启动记录为准，不把文档
  建议当作当前Qwen混合模型更高并发的实机容量资格。
  `https://docs.sglang.ai/advanced_features/hyperparameter_tuning.html`
- [R61] FlashAgents: Accelerating Multi-Agent LLM Systems via Streaming
  Prefill Overlap；4.95读取MLSys2026官方演示资料及原始PDF检索摘要，
  PDF open触发浏览器验证，不称完整正文已读取。
  `https://mlsys.org/media/mlsys-2026/Slides/3760_dJT5ZOY.pdf`
  `https://openreview.net/pdf?id=m14PPUfgEc`
- [R62] AdaServe: Accelerating Multi-SLO LLM Serving with SLO-Customized
  Speculative Decoding；4.95核对作者EuroSys26 PDF的按请求验证预算与执行，
  不将其请求SLO或加速比当作当前workflow的现成输入/收益。
  `https://www.cs.cmu.edu/~zhihaoj2/papers/AdaServe_EuroSys26.pdf`
- [R63] Beat the long tail: Distribution-Aware Speculative Decoding for
  RL Training，DAS，arXiv:2511.13841v1；4.95核对长轨迹预算、接受和rollout
  边界，不把静态RL batch与在线动态多轮workflow认作完全同一问题。
  `https://arxiv.org/html/2511.13841v1`
- [R64] Pie: A Programmable Serving System for Emerging LLM Applications，
  SOSP2025；4.95核对作者PDF的可编程执行与按请求解码接口，不声称本地复现。
  `https://www.yecl.org/publications/gim2025sosp.pdf`
- [R65] Efficient Memory Management for Large Language Model Serving with
  PagedAttention，SOSP2023，arXiv:2309.06180v1；4.98重核原始摘要的跨请求
  共享范围，不沿用其模型加速数字。
  `https://arxiv.org/abs/2309.06180`
- [R66] SGLang官方Session-Aware Radix Cache文档；2026-10-17读取main文档，
  非固定release承诺。核对session软引用、close与Host/device驱逐范围，
  本地配置是否启用仍按冻结源码和启动记录判断。
  `https://raw.githubusercontent.com/sgl-project/sglang/main/docs/docs/advanced_features/session_radix_cache.mdx`
- [R67] ARC: A Self-Tuning, Low Overhead Replacement Cache，FAST2003；
  4.102读取USENIX摘要与原始PDF，核对第III/IV节的recency/frequency与
  非驻留历史目录，未实现或复现当前变长前缀/两级KV系统。
  `https://www.usenix.org/conference/fast-03/arc-self-tuning-low-overhead-replacement-cache`
  `https://www.usenix.org/legacy/events/fast03/tech/full_papers/megiddo/megiddo.pdf`
- [R68] AgentKV: Phase-Aware KV Eviction for Agentic LLMs，
  arXiv:2609.14872v1；4.103核对§3.3–3.4与附录E，固定顶层repo
  b5280b7及engine gitlink254c57bc，后者公开API本轮404，未复现实验。
  `https://arxiv.org/html/2609.14872v1`
  `https://github.com/LiuTaowen-Tony/agentkv/tree/b5280b7c42bd90962712c19f8e0047c89ca40d6d`
- [R69] Practical Online KV Cache Compaction for LLM Agents: An Empirical Study，
  arXiv:2608.00902v1；4.103核对§3–5与附录A.7，明确任务实验与serving
  simulation不同，并保留FULL层/混合状态边界，未移植或外推速度数字。
  `https://arxiv.org/html/2608.00902v1`
- [R70] PolyKV: A Shared Asymmetrically-Compressed KV Cache Pool for Multi-Agent
  LLM Inference，arXiv:2604.24971v1；4.103核对§3.1–3.3、§4和§5.3，
  区分共享pool与各DynamicCache接入后的实际分配，未运行其artifact。
  `https://arxiv.org/html/2604.24971v1`
- [R71] KVComm: Online Cross-context KV-cache Communication for Efficient
  LLM-based Multi-agent Systems，Ye等，arXiv:2510.12872v1；4.103核对
  §3.1–3.4的位置和上下文偏移近似，不混用同名paper或宣称原生状态等价。
  `https://arxiv.org/html/2510.12872v1`
- [R72] NEO: Saving GPU Memory Crisis with CPU Offloading for Online LLM
  Inference，arXiv:2411.01142v1；4.107核对非对称CPU/GPU批次、逐层交换
  与负载感知调度，不以本地独立扫描模型冒充该实现。
  `https://arxiv.org/html/2411.01142v1`
- [R73] SplitZip: Ultra Fast Lossless KV Compression for Disaggregated LLM
  Serving，arXiv:2605.01708v3；4.107核对精确保真、固定长指数编码与
  GPU codec路径，不将其异机传输收益外推为本地HiCache收益。
  `https://arxiv.org/html/2605.01708v3`
- [R74] Blink: CPU-Free LLM Inference by Delegating the Serving Stack to GPU
  and SmartNIC，arXiv:2604.07609v1；4.107核对GPU常驻调度/批处理/
  KV管理与SmartNIC入口，未移植或复现其性能。
  `https://arxiv.org/html/2604.07609v1`
- [R75] Zipage: Maintain High Request Concurrency for LLM Reasoning through
  Compressed PagedAttention，arXiv:2603.08743v1；4.107核对压缩目标页、
  共享prefix引用和容量不足时抢占，不将其压缩质量视为本项目验收。
  `https://arxiv.org/html/2603.08743v1`
- [R76] DiffKV: Differentiated Memory Management for Large Language Models
  with Parallel KV Compaction，SOSP2025，DOI10.1145/3731569.3764810；
  4.107保存作者PDF并核对§5–6的GPU页管理、compaction与内核布局。
  `https://ipads.se.sjtu.edu.cn/zh/publications/sosp25-zhang.pdf`
- [R77] FlashInfer: Efficient and Customizable Attention Engine for LLM Inference
  Serving，arXiv:2501.01005v1；4.109核对可编程稀疏/共享组合KV格式和
  不搬动数据的执行能力，不把它限定为只支持单一连续prefix。
  `https://arxiv.org/html/2501.01005v1`
- [R78] ResidualKV: Residual-Based KV Cache Compression for Efficient Long-Context
  Inference，arXiv:2602.08005v2；4.109读取v2标题及全局参考、pre-RoPE、
  residual quantization与重建机制。v1题名DeltaKV，不混用版本或当作本地复现。
  `https://arxiv.org/html/2602.08005v2`
- [R79] MobiLoRA: Accelerating LoRA-based LLM Inference on Mobile Devices，
  ACL2025长文1140，正式PDF§3.1–3.3；已有prefix/adapter参考选择、
  anchor/delta池、浮点误差有界压缩及系统上下文回收，不沿用其加速比。
  `https://aclanthology.org/2025.acl-long.1140/`
  `https://aclanthology.org/2025.acl-long.1140.pdf`
- [R80] The XOR Cache: A Catalyst for Compression，ISCA2025，
  DOI10.1145/3695053.3730995；4.109读取作者PDF§2–4的跨line可逆XOR、
  相似值参考选择及minimum sharer可恢复性约束，不声称是GPU KV实现。
  `https://jsm.ece.wisc.edu/docs/pan-isca2025.pdf`
- [R81] DeltaLog: Deferred Materialization of Recurrent States for Linear Attention
  Decoding，arXiv:2608.15533v1；4.111核对Gated DeltaNet update log、
  checkpoint/物化、rollback及prefix-cache恢复，不推断本地Host层实现
  或逐位浮点等价。`https://arxiv.org/html/2608.15533v1`
- [R82] MARS: Efficient, Adaptive Co-Scheduling for Heterogeneous Agentic Systems，
  arXiv:2604.26963v2（2026-06-15）；4.112保存原文/hash并核对CPU/GPU压力
  反馈、准入、进度队列与KV预算机制。不把pinned session当Host固定页，
  未复现性能或确认任何会议录用。`https://arxiv.org/html/2604.26963v2`
- [R83] KVzip: Query-Agnostic KV Cache Compression with Context Reconstruction，
  NeurIPS2025正式PDF；4.113核对§2–3与附录的重建评分、多query复用、
  chunked scoring及开销边界，未本地复现或沿用其速度/质量数字。
  `https://papers.nips.cc/paper_files/paper/2025/file/f4eaa4b8f2d08edb3f0af990d56134ea-Paper-Conference.pdf`
- [R84] LoSA: Locality Aware Sparse Attention for Block-Wise Diffusion Language
  Models，arXiv:2604.12056v1；4.113核对并集膨胀和局部性机制，明确其
  block-DLM范围，不推断为当前AR多请求的现成实现。
  `https://arxiv.org/html/2604.12056v1`
- [R85] EchoPress: Query-Agnostic KV Cache Pruning via Virtual Context
  Reconstruction，arXiv:2610.00412v1；4.113仅核对原始摘要/身份，不称
  本地复现或完整实现对照。`https://arxiv.org/abs/2610.00412`
- [R86] QASPER官方数据说明与v0.3 train/dev归档；4.113固定实际归档SHA256，
  按源顺序取完整论文和自然问题，受控prefix构造不当原生agent轨迹。
  `https://allenai.org/data/qasper`
  `https://qasper-dataset.s3.us-west-2.amazonaws.com/qasper-train-dev-v0.3.tgz`
- [R87] MarginGate: Sparse Margin-Triggered Verification for Batch-Invariant LLM
  Inference，arXiv:2605.30218v1；4.114核对实际原文标题与margin触发验证/
  修复范围，未复现或外推加速比。误指2602.00953未采纳。
  `https://arxiv.org/html/2605.30218v1`
- [R88] UNISON: A Co-Designed Near-Memory Scheduler of Session KV Residency for
  LLM Agents，arXiv:2609.09643v1；4.114核对session hazard/驻留范围，不把
  它当成完整workflow剩余工作预测或所有DAG调度的实现。
  `https://arxiv.org/html/2609.09643v1`
- [R89] FASER: Fine-Grained Phase Management for Speculative Decoding in Dynamic
  LLM Serving，arXiv:2604.20503v1；4.115保存PDF并核对§4.1–4.3及6.4.2，
  不忽略其MoE适配，不沿用论文速度数字。`https://arxiv.org/pdf/2604.20503v1`
- [R90] SpecFit: Not All Draft Tokens Cost the Same in Offloaded MoE Inference，
  arXiv:2610.15417v1；4.115核对实际原文的专家边际成本、ancestor-closed
  选择与offload边界，未复现其策略。`https://arxiv.org/html/2610.15417v1`
- [R91] Coupling Without Communication and Drafter-Invariant Speculative
  Decoding，NeurIPS2025；4.115核对原作者Google Research页面的采样耦合
  范围，未声称本地复现论文算法。
  `https://research.google/pubs/coupling-without-communication-and-drafter-invariant-speculative-decoding-2/`
- [R92] Fast Inference from Transformers via Speculative Decoding，ICML2023，
  PMLR202；4.115核对经典无改模型/目标分布的原始摘要，有限停止反例只针对
  本文朴素规则，不推断已有算法有错。
  `https://proceedings.mlr.press/v202/leviathan23a.html`
- [R93] GraniKV: Asymmetric Granularity KV-Cache Paging for Multi-Agent Systems
  with Long Shared Prefix，arXiv:2608.15584v1；4.117保存原HTML并核对§3、
  limitations及附录B.10。布局/attention结果不外推成本地迁移收益，
  也不因其没有在线promotion就宣称新机制成立。
  `https://arxiv.org/html/2608.15584v1`
- [R94] RelayCaching: Accelerating LLM Collaboration via Decoding KV Cache
  Reuse，arXiv:2603.13289v1；4.117保存原HTML并核对decode-to-prefill、
  layer/token rectification及其近似边界，未复现或外推coding质量。
  `https://arxiv.org/html/2603.13289v1`
- [R95] Sutradhara: An Intelligent Orchestrator-Engine Co-design for Tool-based
  Agentic Inference，arXiv:2601.12967v3；4.117保存原HTML并核对§4、baseline
  prefix caching及FTR/E2E边界，不冒充完整移植或本地收益。
  `https://arxiv.org/html/2601.12967v3`
- [R96] ChunkAttention: Efficient Self-Attention with Prefix-Aware KV Cache
  and Two-Phase Partition，ACL2024长文623；4.118保存官方PDF/派生文本，
  核对§3.1–3.2的动态prefix-tree与两阶段局部性计算，未复现本地模型收益。
  `https://aclanthology.org/2024.acl-long.623/`
  `https://aclanthology.org/2024.acl-long.623.pdf`
- [R97] LayerSkip: Enabling Early Exit Inference and Self-Speculative Decoding，
  arXiv:2404.16710v1；4.119保存原始PDF/文本并核对§4.3的hidden/KV复用
  及训练边界，未套用本地未训练目标的接受率或速度。
  `https://arxiv.org/pdf/2404.16710v1`
- [R98] PipeDec: Low-Latency Pipeline-based Inference with Dynamic Speculative
  Decoding towards Large-scale Models，arXiv:2504.04104v1；4.119保存实际题名
  原HTML并核对§3.2–3.4的增量prediction-tree KV/activation与pipeline部署，
  不按第三方或其他版本名称混用来源，未外推为本地单GPU收益。
  `https://arxiv.org/html/2504.04104v1`
- [R99] KREX: Concurrent Kernel Benchmarking on Shared GPUs via Region-Granular
  Exclusivity，arXiv:2609.30057v1，PDF标注2026-09-24；4.120保存PDF/文本，
  核对§4.2–4.3和§7的区域保护、命令退出清理及multi-GPU扩展边界，
  未部署或外推其速度。`https://arxiv.org/pdf/2609.30057v1`
- [R100] KernelBench-Multi官方SPEC、DEVLOG及两个驱动脚本；4.120固定仓库
  提交7bb3f95c8a86ee410bc58b3f6f80fed7204cc7ec，读取2026-10-18。
  2026-07-25事故按开发者报告记录，不冒充原始分配trace或本地复现。
  `https://github.com/Infatoshi/kernelbench.com/tree/7bb3f95c8a86ee410bc58b3f6f80fed7204cc7ec/benchmarks/multi`
- [R101] Less Experts, Faster Decoding: Cost-Aware Speculative Decoding for
  Mixture-of-Experts，EcoSpec，arXiv:2607.12696v1；4.121核对§4及附录的
  已训练predictor、expert buffer与边际路径代价，不外推其速度或准确率。
  `https://arxiv.org/html/2607.12696v1`
- [R102] D-cut: Adaptive Verification Depth Pruning for Batched Speculative
  Decoding，arXiv:2607.14647v1；4.121核对§3与附录的跨请求预算、runtime
  profiling、Qwen3.5实验配置及因果范围，未本地复现。
  `https://arxiv.org/html/2607.14647v1`
- [R103] ECHO: Elastic Speculative Decoding with Sparse Gating for
  High-Concurrency Scenarios，arXiv:2604.09603v1；4.121核对§3–4的统一
  super-tree及全局预算，不误称只按请求独立决策。
  `https://arxiv.org/html/2604.09603v1`
- [R104] ASPIRE: Asynchronous Batched Self-Speculative Decoding for Long-Context
  LLM Inference，arXiv:2609.17943v1；4.121核对§4的mixed forward、
  在线cost-aware选择及refresh，不外推为本地GDN已兼容。
  `https://arxiv.org/html/2609.17943v1`
- [R105] DScale: Scaling Block-Diffusion Speculative Decoding with Adaptive
  Verification，arXiv:2609.37532v1；4.121核对§III的path-aware tile、预算及
  fixed-address graph；归档HTML title有DiffusionSpeculative连写，未据此
  改成错误的Dynamic Verification扩展题名。未复现或沿用其性能。
  `https://arxiv.org/html/2609.37532v1`
- [R106] Range: Decoupling KV-Cache Reuse Granularity from Storage Work，
  arXiv:2610.21241v1；4.123保存实际题名原HTML，核对§3.2–3.5的range
  ticket、byte stripes、completion-owned banks及逐层交付；SSD/GDS范围
  不等于当前Host DRAM直接实现，未复现或外推性能。
  `https://arxiv.org/html/2610.21241v1`
- [R107] LayerKV: Optimizing Large Language Model Serving with Layer-wise
  KV Cache Management，arXiv:2410.00428v1；4.123保存原HTML并核对分层
  分配、管理、卸载及SLO scheduler，不忽略其已有分层primitive，也不外推
  为当前Qwen混合模型已资格兼容。`https://arxiv.org/html/2410.00428v1`
- [R108] Fairness in Serving Large Language Models，OSDI2024；4.125保存官方
  PDF/文本，核对§3.1、§4.2及附录B.2的client、单调请求成本和profiled成本。
  不把该范围扩大为所有batch共享资源成本定理。
  `https://www.usenix.org/system/files/osdi24-sheng.pdf`
- [R109] Locality-aware Fair Scheduling in LLM Serving，arXiv:2501.14312v1；
  4.125保存固定HTML，核对§3的extend-token成本及DLPM/D2LPM局部性和公平
  预算；不冒充物理迁移/显存驻留成本模型。`https://arxiv.org/html/2501.14312v1`
- [R110] FairCache作者仓库，读取2026-10-18；固定提交
  `4fa5650273e439237b163aaa0d4aa852dcd4463e`的README、scheduler和CPIReqQueue。
  多资源/共享归因的完整设计按作者说明，局部代码只确认client计量与credits；
  未核完整论文、全部资源路径或本地适配，不按未来期号推定已出版。
  `https://github.com/icloud-ecnu/FairCache/tree/4fa5650273e439237b163aaa0d4aa852dcd4463e`
- [R111] Stream2LLM: Overlap Context Streaming and Prefill for Reduced
  Time-to-First-Token，arXiv:2604.16395v1；4.126按正文核题名，检查§2.4/3的
  append/update、LCP与资源选择。分离式prefill结果不当闭环多agent JCT。
  `https://arxiv.org/html/2604.16395v1`
- [R112] Can Agent Harnesses and Inference Engines Hear Each Other? The HEAR
  Protocol for Agentic LLM Serving，arXiv:2610.06597v1；4.126核对§3–4的消息
  强度、operation生命周期和缓存协调，不忽略已有跨层协议能力，也不当
  所有策略已被解决。`https://arxiv.org/html/2610.06597v1`
- [R113] Semantic Parallelism: Redefining Efficient MoE Inference via
  Model-Data Co-Scheduling，arXiv:2503.04398v5；4.126核对§3的预测与EP/DP/TP
  调度，不将多卡通信结果外推本地单卡，不误用2602.09833统计学论文。
  `https://arxiv.org/html/2503.04398v5`
- [R114] AIPerf Weka作者教程和固定源码，读取2026-10-18；AIPerf提交
  `f7257c900f3d38a3ca1f7ba7f40aca05ffe0123e`，AgentX harness提交
  `104c7c8086666385fbf3962911b55f68bf08a04a`。核对合成prompt、显式/推断拓扑、
  namespace与时间/过滤规则；未下载corpus或执行loader，不作任务质量证明。
  `https://docs.nvidia.com/aiperf/latest/tutorials/datasets-inputs/replay-weka-agentic-coding-traces.md`
  `https://github.com/ai-dynamo/aiperf/tree/f7257c900f3d38a3ca1f7ba7f40aca05ffe0123e/src/aiperf/dataset/loader`
  `https://github.com/SemiAnalysisAI/agentx-harness/tree/104c7c8086666385fbf3962911b55f68bf08a04a`
- [R115] Speculative Actions: A Lossless Framework for Faster Agentic Systems，
  作者ICLR2026 PDF，正文arXiv:2510.04371v2/2026-04-23；4.128核对§2、
  Algorithm1及多步/副作用边界。作者的并发、独立延迟和可逆前提不直接
  成立于本地高压GPU或任意工具，不移用速度/准确率。
  `https://www.cs.columbia.edu/~kkaffes/papers/speculative-actions-iclr26.pdf`
- [R116] TomasuLLM: Out-of-Order Speculative Execution for LLM Agents，
  arXiv:2609.38201v1；4.128核对正文intro的action/observation drafting、
  COW依赖/效果验证与有序提交；仅补直接对照，不恢复共享workspace线，
  不作本地质量/并发部署或速度证明。`https://arxiv.org/html/2609.38201v1`
- [R117] Capture the lifecycle: KV Cache management in ReAct Agents with
  KVTether，arXiv:2609.39819v1，2026-09-30；4.130核对§4–8/Algorithm1，
  保留消息级生命周期、共享聚合、prefill后Suspended/MRU与PD/shape replay
  边界，不把本地调用排序当实际驱逐或原文性能反证。
  `https://arxiv.org/html/2609.39819v1`
- [R118] Priority-Aware Preemptive Scheduling for Mixed-Priority Workloads in
  MoE Inference，QLLM，EuroMLSys2025/arXiv:2503.09304v1；4.132核对§3/
  Algorithm1及HF原型评估，不移用其SLO/速度结果。
  `https://arxiv.org/html/2503.09304v1`
- [R119] Toward Cost-Efficient Serving of Mixture-of-Experts with Asynchrony，
  AMoE，arXiv:2505.08944v2，2025-05-29；4.132核对§3.2–3.4的µ-queues、
  依赖追踪、异步重批与defragging，保留分布式EP/原型范围。
  `https://arxiv.org/abs/2505.08944v2`
- [R120] BatchGen: An Architecture for Scalable and Efficient Batch Inference，
  OSDI2026；4.132核对官方正文§4–5/6.3/7的sequence coroutine、状态管理及
  BCT/SCT/TPOT边界，不把未在本地适配当新颖性依据。
  `https://www.usenix.org/conference/osdi26/presentation/xu-tairan`
  `https://www.usenix.org/system/files/osdi26-xu-tairan.pdf`
- [R121] Scepsy: Serving Agentic Workflows Using Aggregate LLM Pipelines，
  arXiv:2604.15186v1；4.134核对§3–5的聚合profile、联合fractional GPU/
  TP/replica分配及discussion长工具范围。16GPU/RAG/beam结果不作本地
  性能证据，也不将异构模型直接分配或聚合假设的未检验残差当新机制。
  `https://arxiv.org/html/2604.15186v1`
