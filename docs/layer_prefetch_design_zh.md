# 自适应分层预取与两批负载

更新：2026-10-18。此文优先解释当前开发方向；此前研究筛选结论保留为历史。
用户于2026-10-18确认GPU已空闲，覆盖此前北京时间2026-10-19 08:00的
占用约定。V82于08:25 PDT发起并通过真实服务核对；发现新的串行化与审计
问题后停止并保留部分数据。V82b于08:50 PDT从`ab951b9`冷启动，
08:55通过真实服务核对，predictive先行、随后相同合同的native。V81不恢复。

## 已有保护的ACK降本

main在同会话原生锁已覆盖本次恢复节点时跳过零新增保护的容量/字节/槽位
计算；仍核对节点/祖先创建身份，保留每个命令的回执、覆盖锁、过期及消费。
budget、保护槽位和角色为null，表示未做新分配检查，不是假造零容量。
新锁及更深锁继续现场预算、原生加锁和祖先锁合并。冻结V82b不热改。

[V1752 runtime回归](../experiments/reports/v1752_covered_restore_full_runtime_cpu_20261018.xml)
1352项通过，[V1748预算/消费审计](../experiments/reports/v1748_covered_restore_metadata_audit_cpu_20261018.xml)
239项通过。[V1751最终CPU基准](../experiments/reports/v1751_covered_restore_budget_final_control_cpu_20261018.json)
对照0d7aed5，24组完整构造替身、每组101次/10次预热；新锁/扩展锁必须
真的调用锁替身，避免缺字段落入异常分支却误称验证。计数仅在独立首遍，
计时不含计数Mock；固定时钟用普通callable。覆盖场景预算读取1→0，
锁及消费/生命周期记录一致，预算展示差异明确单列。64节点/原生预算
同节点覆盖线程CPU P50为49.884→17.115us，祖先覆盖57.670→24.125us；
新/更深锁约384/397us，深512约2.5ms，没有同等改善。保留慢路径，
不外推在线频率、整轮CPU、DMA、GPU吞吐或JCT。

[V1753旧接口差异](../experiments/reports/v1753_legacy_sglang_contract_provenance_20261018.json)
广adapter套件另有两个失败：旧fixture缺ReqState.time_stats，旧测试调用
当前Scheduler不存在的check_memory；在安装版和冻结引擎均复现，测试源
与0d7aed5一致。该套件不是全绿；未改引擎或删除旧测试规避，不混入
上面的相关runtime通过结论。

[V1743完成子集](../experiments/reports/v1743_v82b_completed_workflow_shape_20261018.json)
截至10:51 PDT，151个结果均completed，其中35个root多轮、15个workflow
嵌套，最大深度2，425个child返回、21个嵌套JOIN完成。尚有5个未完成，
不补零轮次或失败；任务正确性未独立评分。native尚未开始，完整验收保持开放。

## Host压力与预测迁移

[V1729状态快照](../experiments/reports/v1729_v82b_host_status_20261018.json)
截至10:26 PDT，Host FULL/Mamba高水位约160/40.05GB，FULL累计驱逐
265.19GB。精确前缀再访问中1173条仍命中device、20条命中Host，
74条涉及3896 token/79.79MB重算。累计驱逐可能重复；未再访问、观察索引
过期以及Mamba命中位置未知不能算安全备份，也不能算已确认损失。
公共已服务输入回退代理另为1079001 token，包含边界及其他因素，
不与前述物理归因相加，不将79.79MB当所有重算上界。

[V1726密封前缀](../experiments/reports/v1726_v82b_host_predictor_question_prefix_20261018_summary.json)
有203个闭合JOIN、160个正FULL计划、95个提前提交，84个提前且被下一请求
确认复用，FULL为3.180GB；tool提前复用6.400GB。111个JOIN曾遇device容量
预算限制，40个曾无Host-backed缺失副本；原因可以重叠。后一个快捷检查
不观察全部缺副本节点，也会在HBM已驻留时返回，不能当Host驱逐人口。
展开闭包中的无Host且无device FULL节点与必要Mamba缺失须另计。

[V1732预测分组](../experiments/reports/v1732_v82b_nested_forecast_final_diagnostics_20261018.json)
以llm_submit时已见的父子链和JOIN轮次分组，内部摘要不混入普通请求。
EOS前最后快照共161个自然最终请求：第1轮128个的剩余token绝对误差P50
44.92，后续33个56.75；depth2仅7个、coordinator仅4个，统计推广未知。
74个首次work触发有56个离EOS超过2s，且末段仍存在剩余工作高估；
不以统一时间偏置解释所有阶段。完成通知的已观察输入未发现丢失。
模型仍只估计阶段和剩余工作，runtime决定目标、预算与传输时机。

[V1731 CPU检查](../experiments/reports/v1731_nested_work_export_cpu_20261018.xml)
94项通过。离线误差审计与样本导出可在关闭机会采样时读取现有成对时钟；
密封前缀长度/哈希继续核对，分组不使用未来结构，不增加在线埋点。
V1733导出19546个实际已交付快照/159个自然请求，已完成按项目隔离的三组
剩余工作头重拟合；phase/encoder不变。候选与本轮运行模型分开，V82b继续
冻结ab951b9，不能将CPU开发集改进称为整轮吞吐或未见项目验证。

[V1735工具时序](../experiments/reports/v1735_v82b_tool_forecast_sealed_prefix_20261018_summary.json)
从同一V1726前缀重新核对现有成对时钟；12597个有末秒预测的工具轮次，
逐轮末秒绝对误差P50/P90为110.22/576.90ms，多工具轮次432个为
163.71/622.98ms。数据支持工具时序仍有可用信息，不能证明所有轮次精确、
实际Host机会存在或已取得吞吐收益。工具侧时钟修复后的V1736共105项CPU
检查通过；不增加在线采样。

[V1737副本与丢失归因](../experiments/reports/v1737_v82b_host_loss_and_wait_backing_20261018.json)
在同一密封前缀，71条FULL重算涉及49个请求、3893 token/79.73MB；
62条不超过64 token，最大1176 token。163个JOIN和3607个tool等待确实
展开的闭包没有无device且无Host的FULL节点；快捷返回不枚举这些节点，
缺少展开不能算零丢失。较晚V1729状态计数不同，不强行要求两个前缀相等。

[V1740模型开发结论](../experiments/reports/v1740_v82b_work_refit_decision_20261018.json)
同一固定15请求评估集、冻结phase/encoder：直接重训和晚期加权的末段
绝对误差P50为58.21/49.36 token，旧头46.96；正文进度加权为28.98、
P90为66.22（旧头123.53）。但该候选首32-token触发仍6个，其中5个离
RETURN超过2s，中位提前量11.61s而旧头4.55s。第一个候选减少过早触发
同时降低覆盖，未达到联合改善。三者均未部署，全部报告保留。
19546个输入重放的phase分数最大差6.86e-7、center最大差0.00177 token，
证明本次导出与实际输入一致，不证明新头有效或嵌套推广成立。
后续评价优先首触发及消费，不只优化最后一个快照；模型不学习迁移净收益。

[V1741最终密封审计](../experiments/reports/v1741_v82b_nested_forecast_prefix_final_20261018.json)
与V1732的误差、结构分组、触发和通知统计完全一致。早期前缀始终按partial
评价，后来新增的summary不成为其未来标签。[V1742 CPU检查](../experiments/reports/v1742_nested_forecast_prefix_final_cpu_20261018.xml)
105项通过，包括追加日志及后来完成不改变早期未知状态。

## JOIN并行与最新消费诊断

[V1715第二批形状](../experiments/reports/v1715_v82b_second_wave_workflow_shape_20261018.json)
观测全部156个workflow启动、第二批48个均开始；22个root多轮、
9个workflow嵌套，最大深度2，归档86个completed。终态不等于正确性评分。
[V1716真实服务](../experiments/reports/v1716_v82b_second_wave_actual_service_20261018.json)
从固定完整audit尾部核对全部48个第二批root的prefill样本，并绑定实际
driver/scheduler/client的start ticks。不是仅凭计划或状态文件判断在运行。
predictive仍在采集，native尚未开始；完整对照与质量/性能验收仍待完成。

main新增确认JOIN的有界并行，尚未部署到冻结的V82b。此前即使context
配置为2，确认parent的单一选择票据也在等ACK时阻塞另一parent。现在
已有确认表继续拥有各自的票据、ACK及绑定消费身份，选择器可处理另一
合法context；共用原有在途字节/context与原生FULL/Mamba预算。未收到
完整回执不计ACK，部分回执保留占用；取消不提前释放在途物理所有权。
关闭并行或无原生overlap时仍串行；此处V1710阶段的provisional仍串行，
后续V1724实现如下。
满恢复前沿直接返回，不在每个decode步遍历确认队列。

后续在途表补齐RETURN前provisional JOIN的并行，支持语义剩余工作和
已观测正常EOS两种原有信号。第一笔仍在等待ACK时，选择器可处理另一个
合法parent；共用原来的context/在途字节及原生分池容量。选取同一context
仍受在途排斥。RETURN和同批后继client提交将原票据升级到确认态，保留
命令与安全checkpoint身份；部分ACK保留所有权，取消不提前回收在途页。
满前沿不遍历在途表，旧probabilistic票据保持原串行行为。

[V1724最终CPU回归](../experiments/reports/v1724_provisional_join_parallel_final_cpu_20261018.xml)
1352项通过，含两种阶段信号、部分/乱序ACK、RETURN与client提交合并、
取消/compact/timeout、预算限制、无overlap及满前沿不遍历。
[V1725控制基准](../experiments/reports/v1725_provisional_join_parallel_final_control_cpu_20261018.json)
对照1e0bb50，201样本/20预热；同一CPU替身、真实runtime分派和相同
合法预算下，EOS与work两种场景均在RETURN/ACK前从1组/4节点变为
2组/8节点。已满前沿分派P50为16.661→0.643us；完成前信号是受控输入，
不是在线预测精度或机会频率，亦不包含GPU allocator、DMA、复用或JCT。
上述修复只在main，V82b两组继续固定ab951b9。

[V1721最新迁移前缀](../experiments/reports/v1721_v82b_second_wave_prefetch_prefix_20261018_summary.json)
截至10:07 PDT有170个闭合JOIN、133个进入窗口、81个提前提交；
JOIN/tool提前且确认复用FULL为2.995/6.400GB。predictive未结束，
native尚未开始；不能把局部字节或CPU基准当作整轮收益。

[V1710 CPU回归](../experiments/reports/v1710_parallel_confirmed_join_final_cpu_20261018.xml)
1329项通过，覆盖部分/乱序ACK、超时不计完成、取消独立性、槽数与在途
字节限制、无overlap及满前沿无队列遍历。
[V1712 CPU基准](../experiments/reports/v1712_parallel_confirmed_join_final_control_cpu_20261018.json)
对照f06c03c，201样本/20预热。相同可容纳两个context的CPU替身中，
首个ACK前从1组/4节点变为2组/8节点；已满前沿选择P50从2.258降至
0.746us。确认JOIN本身发生在RETURN之后，不能称为完成前预测覆盖增加。
该局部计时也不是整轮CPU、DMA、GPU服务或JCT测量。

[V1711更新前缀](../experiments/reports/v1711_v82b_prefetch_live_prefix_20261018_summary.json)
观测128个闭合JOIN：114个进入窗口、71个提前提交，JOIN/tool提前且被
下一请求确认复用FULL为2.634/6.400GB。该前缀仍在第二批到达之前，
不是完整对照；不能用节点命令量替代独立事件或吞吐。

[V1713等待分段](../experiments/reports/v1713_v82b_ack_wait_stage_partition_20261018.json)
从同一密封报告将89个JOIN与93个tool的最后ACK→首次服务逐段切分，
每行各段和复现原区间；使用相同分位算法。12个JOIN超过5s，其到达
原生队列→服务累计148.77s，占这组213.16s观察间隔的69.79%，不是
独占GPU时间或可省JCT。两例tool后继提交前的64.53/47.81s空档中，
都有配对的内部context-summary模型请求，span为64.41/47.75s；不将
这些必要模型服务归为普通客户端移交。接下来沿真实准入资格、驻留与
摘要失效继续核查；当前GPU对照仍使用ab951b9，不热改应用或引擎。

[V1714逐命令保护证据](../experiments/reports/v1714_v82b_join_tail_lease_evidence_20261018.json)
核对这12个超过5s的JOIN，全部有实际完成前保护到期。到期释放锁，
不证明数据已驱逐，也不能把到期与后续服务差当可省JCT。优先改善过早
预测与到达后的真实恢复/准入协同；直接把驻留延长到十几秒会重新引入
HBM占用代价，尚无支持这样修改的收益证据。

## 在途恢复与审计修复

V82运行期间，在主目录main完成两项修复，V82原执行源码保持冻结：

- 原有单一tool ticket在等待ACK时直接返回，两个并行context的配置无法
  用于两个tool。现在将在途tool票据单独保留并独立核对ACK，选择器可以
  继续选择另一context；同一context仍禁止重复恢复，实际槽数、在途字节、
  FULL/Mamba容量、因果重检及原生依赖保持有效。无overlap或关闭并行时
  继续串行。JOIN票据的并行扩展尚未在本次修改中实现。
- 无tag的原生ACK用已有在途节点集合核对冲突，不再为每笔在途事务重新
  建集合。部分ACK和失效context仍保留整笔所有权，直到完整核对或退休。

同时修复低观测配置的报表阻断：额外机会采样关闭后没有safe_point_census，
旧JOIN/tool审计却强制依赖它。已有每秒runtime状态现在保存带采样跨度的
wall/monotonic锚点；四个审计路径接受该锚点。没有机会样本仍保持未知，
不恢复重型机会扫描，也不制造机会频率或未来RETURN证据。

[V1686回归](../experiments/reports/v1686_prefetch_conversion_final_cpu_20261018.xml)
1114项通过，覆盖两tool并行、容量限制、无overlap、乱序ACK、超时不计
完成、原生冲突、已有预取语义和无机会采样时的审计。
[V1687基准](../experiments/reports/v1687_parallel_prefetch_control_final_cpu_20261018.json)
以44c802f为基线，101样本/20预热；64笔在途、32个原生ACK节点的
单次核对P50为55.42→4.83us。受控两context选择从1组/4节点变为
2组/8节点，均未伪造ACK或复用。其他空路径与JOIN扫描没有同等改善，
不能外推整轮控制成本、DMA吞吐或JCT。V82b用于实际验证新代码。

## 当前观测与待验证项

运行期间新增CPU修复尚未部署到V82b：原生prefill在加入decode列表前
已分配request row，47 running加1在途prefill会让旧预算错误归零。
cache H2D不占request row，现用已有池容量/空闲行恢复有界前沿，同时
按全部占用行预留FULL增长；原生模型请求仍等待真实空闲行。
[V1699原生方法验证](../experiments/reports/v1699_native_inflight_row_allocator_cpu_20261018.json)
用冻结引擎实际行分配方法重现；新预算不改变alloc_rows拒绝/释放条件。
[V1700 CPU回归](../experiments/reports/v1700_inflight_prefill_final_cpu_20261018.xml)
1314项通过，受控两个context从0提交变为2，但没有GPU收益结论。
每秒状态增加行数解释字段，不引入新树扫描；V82b缺该字段，实际频率未知。
[V1702预算计时](../experiments/reports/v1702_inflight_prefill_budget_final_cpu_20261018.json)
使用201样本/20预热，与f06c03c对照；三种行占用下预算查询P50增加
0.24–0.32us，47 running/48占用时为3.220→3.535us。这是机会修复
带来的小额新增CPU成本，不将其描述为控制路径降本或端到端收益。

[V1701最新密封前缀](../experiments/reports/v1701_v82b_prefetch_live_prefix_20261018_summary.json)
56个已闭合JOIN中51个进入过窗口、24个在RETURN前提交、15个确认提前
复用；tool的54个窗口均提交、50个确认提前复用。提前且复用FULL为
0.187/4.391GB；JOIN仍需优化计划转提交与恢复后消费。观测理由可重叠，
不能把未转换全部归于行预算，也不能将原生Host命中字节全部算作可预取。
当前V82b继续从ab951b9执行匹配对照，新的CPU修复留待后续冻结验证。

[V1704更新的workload形状](../experiments/reports/v1704_v82b_live_workflow_shape_20261018.json)
于09:28 PDT观测108个root、37个completed、17个多轮root（14个两轮、
2个三轮、1个四轮），5个coordinator产生10个grandchild，嵌套JOIN已
完成2个，最大深度2。早期无嵌套不再代表当前前缀；第二批48个root仍待到达。
main另修复部分入队后尾部容量回收的诊断开关遗漏：关闭机会采样时不
构建候选细诊断，保留实际回收、预算、在途命令和首次复用。
[V1703相关CPU检查](../experiments/reports/v1703_tail_reclaim_diagnostics_cpu_20261018.xml)
371项通过；诊断开关不改变有界安全尾部、实际入队或取消检查。该修改
同样尚未部署到V82b，不能将其计为本轮的性能收益。

[V1693启动回执](../experiments/reports/v1693_v82b_actual_service_and_clock_20261018.json)
核对V82b的真实工作流GPU服务、固定物理容量、委派合同、到达表、冻结
应用/引擎/模型产物，physical_disabled=false、overlap支持、两个context
和各开关均符合计划。在未开启机会采样的新trace前缀上，JOIN传输、
JOIN候选、tool传输三个审计已成功执行；这不是完整对照或性能验收。

V82停止前的[V1690迁移审计](../experiments/reports/v1690_v82_partial_native_h2d_sources_20261018.json)
和[V1691生命周期审计](../experiments/reports/v1691_v82_partial_prefetch_lifecycle_20261018.json)
记录JOIN/tool所传FULL为0.995/14.963GB，下一请求确认复用0.521/12.891GB；
不能在缺少旧成对时钟时将它们都算作完成边界前的预取收益。
JOIN/tool的ACK→首次服务P90为19.943/1.866s，后续仍须重点检查JOIN
恢复后的准入等待、重复加载及有用状态丢失。
[V1692实际形状](../experiments/reports/v1692_v82_partial_workflow_shape_20261018.json)
仅覆盖第一批108个root的未完成前缀：9个进入第二轮，尚无coordinator/
嵌套spawn。代码与合同支持深度2不等于模型已执行嵌套或四轮；本轮继续
如实统计，不能将配置目标作为workload已经达标的结果。

## 当前 GPU 对照

V82使用冻结应用`44c802f`、合格引擎tree `53020ce3`，开发继续在主目录
`main`。156 root按108+48、t=0/3600s到达，共享四轮root目标、
每轮2–4 child、最大深度2的委派合同。两侧固定FULL 1,798,992 token、
Mamba 513 slot；Host 200GB、80:20，工具180s、workflow14400s。
predictive开启JOIN/tool H2D、FULL-only PREPARE及恢复/准入协同，
需求恢复走原生路径；native关闭BeliefKV策略。详细CPU诊断关闭但真实
submit、ACK和首次复用仍记录。实际层模式为`native_all_layers`。

V82目录保留为部分实验，不作为完整对照。V82b使用同一工作负载、容量、
模型产物和原生引擎，应用包含上述修复；两侧均从新的固定源码冷启动。
V82b目录为
`experiments/raw/qwen35_nested_prefetch_two_wave_156root_20261018_v82b`，
精确应用提交以该目录ab_plan.json为准。启动与端到端性能分别核验。

## 上一轮控制路径与预取目标

开发检出固定为 `/home/longhao/experiment/BeliefKV` 的 `main`；上一CPU阶段
`/goal` 基于 `52656d3`，完成高候选规模的重复工作削减和安全预取机会
修复，验收仍限定 CPU。下方 V1670/V1671 是上一轮结果，不代表新一轮
整体控制成本或 GPU 吞吐已经达标。

本轮实现：

- 先检查请求绑定、running membership 与最近服务，再核对完整 JOIN
  图并计算语义速率。没有服务的阶段不反复做图遍历；已观测正常、无工具
  EOS 保留离开 running batch 后的机会，仍受因果与有效期核对。
- 最大提前量先于物理 checkpoint 规划，替换独立的2秒筛选常量。
  当前候选仍为1000ms。超过提前量时不读取完整缓存路径；已在窗口内
  仍保留实际传输估计、分池预算、reclaim 后重检和最终重捕获。
- 每个原生节点冻结实际 payload 后，提交前核对一次 context epoch、
  session 和 JOIN/tool 来源，再注册事务。删除重复因果核对，保留
  payload/容量/在途冲突、原生分配、状态 fence 和 ACK 证据。
- 冻结的预算仅含不可变标量，事件使用独立浅拷贝，删除逐节点递归深拷贝；
  每条记录保留原字段，修改一条记录不影响另一条或原预算。
- 原选1、2，若1已驻留，fresh plan 可能变为2、3。旧代码因3未被原选而
  连2一起拒绝；现在确认原选前驱后截断新增尾部，只发送2。原选范围内
  插入未知节点、创建时间变化、缺失祖先仍拒绝。末端 checkpoint 的
  Mamba 若不在原选 payload 中继续延后，不随 fresh plan 扩展而加入。

`final_stage_service_filter_skipped` 包含尚未完整核对图的阶段，不能称为
活跃 JOIN 人口或机会频率。超出提前量时也不生成完整物理计划，不能用
计划行减少来声称 Host-only 机会减少。实际 submit、ACK、首次复用与
有用 KV 丢失的验收口径保持不变。

[V1676 CPU 回归](../experiments/reports/v1676_prefetch_frontier_final_cpu_20261018.xml)
共1343项和2个subtest通过，覆盖离开 running batch 的 EOS、工具终态/
context 在逐节点提交途中变化、已驻留前驱/扩展尾部、必要状态延后、
错误身份、输入/decode 预留、回执和日志预算独立性。

[V1677 控制基准](../experiments/reports/v1677_prefetch_control_final_cpu_20261018.json)
对照同一 `52656d3`，201样本、20次预热；计时用普通请求对象，
访问/完整图检查计数在计时外收集。扫描 P50 如下（us）：

| JOIN阶段数 / running | 基线 | 本轮 |
| --- | --- | --- |
| 48 / 48 | 99.00 | 103.56 |
| 156 / 48 | 286.68 | 130.19 |
| 512 / 48 | 992.81 | 227.09 |
| 156 / 0 | 266.62 | 39.17 |

156/48与512/48的完整图检查分别从156/512次降为48次；156/0为0次。
全running的48阶段场景约慢4.6us，不能称所有扫描都更快。空路径与
预算查询没有同等幅度变化，不将它们的局部波动归为新收益。

[V1678 选择/提交基准](../experiments/reports/v1678_prefetch_dispatch_selection_final_cpu_20261018.json)
每组101样本、10次预热、交替顺序；24组正提交保持物理目标、在途字节和
预算一致，另6组固定速率/剩余工作的 JOIN 选择保持结果与无物理动作一致。
64节点、原生预算下，保留内存 writer 的 JOIN/tool 提交 P50 分别为
4.422→3.516ms、4.332→3.707ms，约降低20.5%/14.4%；无 writer 则为
3.732→3.338ms、3.509→3.334ms，约降低10.6%/5.0%。
剩余1500ms但提前量1000ms、64节点的拒绝路径为596.64→11.93us，
本轮不读取 session/节点；已在窗口内仍读取真实目标并保留最终重捕获。

这些缓存树/索引与 native enqueue 仍是 CPU 替身；writer 只写内存，
不含真实 I/O、后台 GIL 竞争、GPU allocator 或 DMA。选择场景的固定
速率不作预测精度或真实机会频率。不能累乘上述比例得到整轮控制成本、
吞吐、JCT 或 oracle 收益。V1673/V1675是进一步删减前的中间记录，
最终代码与计时以V1676–V1678的源码hash为准。

下一阶段继续检查真正完成前提交且被下一请求消费的 FULL、ACK→服务、
重复需求加载、嵌套压力下有用 KV 丢失/重算及质量。完整前缀捕获、
逐节点 payload 与原生依赖仍有成本，不直接删除这些正确性检查。
真实按层prefix/tail仍未接入，当前为`native_all_layers`；原GPU占用约定
已由用户最新授权覆盖，本轮GPU状态以上方V82记录为准。

## 有效恢复的 CPU 阶段验收

本轮 `/goal` 以 `7f1509e` 为基线：删除有效 JOIN/tool 候选到提交路径中
可证实的重复工作，修复最终检查丢掉仍安全的 Host-only 恢复目标；用
正确性场景和正目标 CPU 基准验收。端到端吞吐、平均 JCT 与真实按层
暂停/续传另待 GPU，不作为本阶段已经完成的事项。

已完成的控制路径修改：

- 物理事务维护在途节点集合；注册不再重扫全部命令的节点。冲突仍拒绝，
  部分 ACK 不释放整笔占用，完成、取消、失效回执和超时统一退休索引。
- 全部事务使用相同超时长度和单调注册时钟，按插入顺序检查超时，
  遇首个未过期命令即停止。乱序 ACK 移除中间命令不破坏该次序。
- 已确认 JOIN 的 parent 先使用实时因果凭据，不为每个恢复节点先扫描
  无关可见请求；没有该凭据时仍走原有等待/可见身份检查。

预取最终检查曾要求原选批次完全一致：少一个节点、Mamba mask 变化或
仅最后一个节点放不下都可能放弃全部。现在 drain 后重捕获，允许：

- 容量收缩时发送仍放得下的原选完整前缀，输入和 decode 预留不变。
- 原选节点已驻留时，在新 closure 证实 FULL/原需状态就绪后跳过它。
- Mamba 槽不足时保留可传输的 FULL，状态延后；不随容量增长扩大原选
  Mamba payload。原选 FULL 已驻留且原未选 Mamba 时，不追加状态恢复。
- 最后节点只剩部分容量时，最终入队不拆树，只保留前面的完整节点。
  checkpoint 的 Mamba 标记从原计划去掉时也按同一身份核对。
  首段也放不下则本次不提交。

替换 context/session/generation、节点/leaf 创建时间、祖先次序或在
原选范围内插入未选目标仍拒绝；后续增加的末端范围截断见上节。
原生逐节点分配、FULL/state 依赖及执行资格不变。
这不是 agent 终态门禁，也不是仅恢复部分 KV 就允许执行。尚未从旧 trace
测得这些变化的在线发生频率，不能把受控案例比例称为机会覆盖提升。

CPU 回归覆盖旧/原生预算、容量收缩、Mamba 变化、已有驻留、会话关闭、
generation 失效、回执/取消/超时、预测时机、原生恢复及嵌套委派；
1316 项和2个 subtest 全部通过，见
[V1670 日志](../experiments/reports/v1670_prefetch_dispatch_correctness_cpu_20261018.xml)。
旧回收测试补齐诊断开/关两种模式，不恢复已经删除的默认细诊断。

[V1671 完整提交基准](../experiments/reports/v1671_active_prefetch_dispatch_final_cpu_20261018.json)
包含 JOIN/tool × 4/16/64 extents × 旧/原生预算 × 诊断开/关，共24组。
每组10次预热、101个交替顺序样本，以线程 CPU 计时；重置、结果核对、
cProfile 在计时外。稳定树下物理目标、在途字节和预算全部相同。原生
下一批预算且无诊断时，P50 如下（ms）：

| 完整 extents | JOIN 基线→本轮 | tool 基线→本轮 |
| --- | --- | --- |
| 4 | 0.633→0.625 | 0.564→0.555 |
| 16 | 1.524→1.461 | 1.194→1.144 |
| 64 | 4.291→3.734 | 4.025→3.528 |

小批变化很小，大批约降低12%–13%。fixtures 使用真实应用观察/选择/
注册路径与 CPU 缓存树，native enqueue/索引为替身；FULL=20B/token、
Mamba=80B/slot 只是检查字节一致性的受控几何。没有真实 DMA、GPU
allocator 或 ACK/use credit；不能直接累乘为控制总成本或吞吐/JCT收益。
V1667 初版使用 Mock 时钟，成本混入 Mock 调用开销，不作为最终计时依据。

下一轮候选继续 `native_all_layers`，不伪称已经启用自适应层数。
优先验证真正提前提交且被下一请求消费的 FULL、未预取的需求恢复、
ACK→首次服务和重复加载；同时核对嵌套后的有用 KV 丢失与控制成本。
1316 项 CPU 通过不能代替匹配负载的 native/predictive GPU 对照。

## 嵌套后的容量风险与 CPU 控制成本

增加后代数量时，108+48 个 root 不代表原来相同的物理压力。先保持该候选
负载，下一轮同时检查实际活跃后代、FULL/Mamba 池压力、可恢复 Host-only
前缀及随后有用输入的缺失。终态 child 无用私有状态的回收、仍有 Host
副本的 HBM 驱逐、丢掉全部可恢复副本及随后的重算必须分别解释。
不能把驱逐数或 H2D 字节增加直接称为有效迁移机会增加。

`comparison.json` 新增 `capacity_pressure_watch`，只复用已有计数：
同 session 已服务公共输入的 cache-hit 缺口，及精确 Host 驱逐前缀再访问
时的恢复/重算、未匹配块和探针覆盖。前者不是驱逐因果或逐层重算证据；
两组可重叠，不相加为唯一丢失字节。缺字段、零分母和未匹配事件均保持
未知。原生丢弃/重入队还需检查已有日志和 metrics 的实际覆盖，不能由
上述指标为零就断言不存在。若有用缺口与重入队增大、可恢复机会反而下降，
应调整到达强度或并发，不能继续靠破坏有用状态制造迁移。

本轮按“非必要即删除”改动 CPU 路径：

- 详细共享路径计时、额外机会采样、终态缓存诊断均默认关闭；计时设置
  进入冻结计划、恢复环境与实际 runtime 核对。关闭计时也跳过其 scope
  构造；分析脚本报告“未测量”，不把空计时表报成零成本。
- JOIN 每次扫描只构建一次 running request 集合，避免每个阶段重复遍历
  同一 batch。没有 JOIN/tool 候选时不进入相应物理规划。
- 在途字节及 context/session/generation 计数随真实物理事务注册和退休
  维护；查询不再遍历全部命令，部分 ACK、失效及超时仍保留容量占用语义。
- 同一次入队和回收调用复用已读取的 free-list headroom；完整目标重捕获、
  因果 drain、实际 allocator 检查、原生 fence 和 ACK 仍保留。
- 关闭机会采样时不周期性重复输出不变计划，仍记录状态变化、实际提交、
  ACK 与首次复用；回收不构建详细候选诊断。计划行不再是等间隔样本，
  不可据其行数估计完整机会频率或持续时间。

V1665 CPU 基准与 `31d6849` 比较，计时使用普通请求对象，身份读取计数
在计时之外收集：156 JOIN/48 running 的扫描 P50 803.72→289.64us；
64 条在途命令的组合预算查询 21.47→2.32us；无候选的维护加 post-forward
8.30→2.55us。固定不变计划场景的记录由320条减至1条，变化后仍输出。
这些是受控局部函数成本，不代表在线调用频率、总体控制成本或 GPU/JCT
收益。此前 V1664 的身份读取含计数属性开销，只作访问次数证据，以
[V1665](../experiments/reports/v1665_prefetch_control_uninstrumented_cpu_20261018.json)
的未插桩对象计时为准。

CPU 回归主集合977项、嵌套委派/观察器/语义 worker 补充89项通过，
合计1066项（另有2个 subtest）。补充集合日志见
`experiments/reports/v1666_nested_observer_semantic_cpu_20261018.xml`。
shell 语法与本轮代码 diff 检查通过；未启动 GPU server 或监督进程。

## 多轮与嵌套委派负载

当前新实验默认 `native_in_graph_nested_2to4`。旧 prompt 已要求三轮，
但首轮之后主要依赖提示，所有 child 都没有 task 工具，实际不能嵌套。
本次同时修改 prompt 与真实 harness 工具图：

- root 目标四轮，每轮2–4个并行 child：诊断、候选补丁审查、回归/边界
  验证、整合后跨组件/兼容验证。root 在轮间整合证据并修改候选补丁。
- 每轮提示指定一个 `delegation-coordinator` 处理可分解的 workstream，
  其他 child 负责不重叠的证据。coordinator 自己用原生 task 启动一轮
  2–4个 grandchild，等待其 JOIN 后再综合并向 root RETURN。
- 只有 coordinator 拥有嵌套 task；它的可用角色都是叶子。实际最大
  spawn 深度为2（root=0、child=1、grandchild=2），形成三层 agent。
- 初始 root/coordinator 请求沿用原有 generation-time 的2–4 fanout
  约束；后续轮数通过系统 prompt 和持久写入 tool result 的阶段指令推动。
  不拒绝自然终态，不因未达到轮数而强制重新回答，也不制造重复调查。
- 轮数和每轮 coordinator 是请求目标，不宣称真实模型已遵从。
  任务有具体 blocker 时保留真实终态及未完成阶段；不删样本。

所有 child 层均保留独立上下文和原生 session、完成通知、终态释放。
grandchild 的 JOIN parent 是 coordinator；coordinator 的 RETURN 才能
完成 root 对它的依赖。前者可为 coordinator KV 提供恢复机会，不能
错误归为直接解锁 root。新增角色属于新的预测分布，沿用的预测头
只能作为开发起点，不能继承旧 profile 的校准结论。

计划、client manifest 和 summary 使用同一份委派合同，并核对两侧。
新增实际 root 轮数分布、嵌套轮数/已满足 JOIN、spawn 深度分布和未知
深度计数。未来实验继续是108+48/3600s，其他池比例、工具/输出/任务时限
不变；native 和 predictive 都启用相同多轮嵌套形状。旧非嵌套 native
仅作历史参考，新负载需要一次匹配的 native。

逻辑机会多不等于有效预取多：仍按每个 JOIN 的直接 parent 检查安全
输入 checkpoint、可恢复 Host-only FULL、实际提前提交与首次复用。
同时报告增加的模型/工具工作、质量、重算、控制成本和高压段吞吐。
不因提高 fanout 或嵌套深度就先宣称端到端收益或论文创新。

CPU 验证：V1661 回归309项通过、1项环境相关跳过。真实 harness/
LangGraph 配合受控假模型，在有/无排队控制通道两种模式下均产生四轮
root JOIN、四轮嵌套 JOIN、16个后代与17个独立 session，通知归属、
祖先等待及终态回收核对通过；取消样例保留3个取消与2个 JOIN_TIMEOUT，
不作为成功 JOIN。假模型按脚本输出，不能证明真实模型已达到轮数或
预测精度，也没有 GPU 性能结论。测试见 `tests/test_nested_delegation.py`，
日志见 `experiments/reports/v1661_nested_delegation_final_cpu_20261018.xml`。

## 目标与已完成部分

主负载恢复到 156 个互不重复任务：108 个 root 在零时到达，48 个在
3600 秒到达。先解决预取机会转化不足和控制成本，再验证端到端效果。
大 JOIN parent 倾向恢复较深、小请求可能只需较浅的提前量，但策略依据
实际缺失字节、状态依赖和服务机会决定，不能只按 parent/tool 身份分类。

已实现、默认关闭的新开关：

- JOIN/tool 可分别设置最低启动提前量；仍受总驻留提前量约束。
- 原生支持 overlap 时，可在实时容量预算内恢复两个不同上下文；同一
  context/session/generation 的重复在途恢复仍被阻止。
  现有单 tool/单预测 JOIN ticket 仍约束各源并发；本轮主要解除跨源串行。
- 预测状态清理和驻留检查可合并执行；图、请求和租约变化触发重检，
  不越过最早租约到期时间，动作发出前仍验证身份与有效期。
- 关闭额外机会采样不再被启动脚本强制覆盖。传输回执与首次复用保留；
  详细共享路径计时按需启用，设置写入实验计划并在恢复实验时还原。
- `layer_prefetch_policy.py` 提供自适应层数的 CPU 决策原型。尚未接入
  GPU 分层暂停/续传，不能把它说成已经部署的分层传输策略。

原有传输代码本来就允许完整传输时间长于提前量；这一行为不是本次新增。
当前实际 native primitive 仍会提交全部层。FULL token extent 的部分恢复
与“只传前若干模型层”是不同机制，不互相冒充。

## 在线决策

每次返回阶段、工具预测或候选资源发生变化时，对候选 checkpoint 决策
一次。没有变化时复用结果，不在每个 decode token 上重新求解。
先筛出有可恢复 Host-only 页和实际迁移预算的少量候选，仅对当前恢复
前沿求解，避免把轻量层数模型再次变成全 agent 的高频扫描。
候选层数是 `k=0..L`，零表示暂不预取；20 不是默认值或经验最优值。

在线信息只包括已观察到的 child/tool 信号、当前排队与空闲容量、实测
传输服务、当前请求工作量桶及必要状态。真实未来 RETURN、下一请求
首次服务时刻和未来带宽只允许用于事后 oracle，不能回填在线输入。

每个模型/缺失前缀大小/新输入桶缓存一份逐层成本表：

- 各模型层 FULL 字节与必要状态字节分别统计，二者不能平均成相同层大小。
- 传输服务来自 transfer stream；enqueue/ACK 轮询延迟不能除以层数当带宽。
- 计算使用逐层 compute 区间或合格工作量桶；Python worker 墙钟不是纯算时。
- 记录 forward 前的依赖，例如 Mamba checkpoint 到请求槽的复制，以及
  native 每层读 KV 前的事件依赖。依赖改变时重建成本表。

对顺序 H2D、顺序 forward、前向状态复制这一局部模型，预先构建两个
成本包络后，深度 k 的完成时间可以精确写为：

```text
F(k, T, Q) = max(T + A[k], Q + B[k])
```

T 为预计下一次需求就绪的提前量，Q 为当前在途 H2D 剩余服务，A/B 是
由逐层传输、计算和前置依赖构建的数组。准备少量 T 的概率情形，
用前缀 CDF 求期望，不需要每次重新模拟所有层。当前原型最多接受
8 个情形，决策为 O(L log S)，固定 S 下随层数线性增长。

评分比较“预计减少的等待”与“增加的资源成本”：

```text
score(k) =
  P(本 checkpoint 会被续接) × E[F(0) - F(k)]
  - 无续接概率 × 提前传输服务
  - 当前链路竞争价格 × 提前传输服务
  - 完整预留页的驻留成本
  - 本次控制成本
```

概率模型只预测事件、工作量与时间分布；价格、容量、可执行性和是否
迁移由 runtime 决定。价格必须通过真实资源竞争验证，不从未来 oracle
反推参数，也不把本地 score 宣称为系统吞吐的精确目标函数。

## 两项不能忽略的约束

现有 FULL allocator 通常一次分配所有层对应的完整 page。只写入前
若干层不代表只占相同比例的 HBM。因此任何非零层数都必须通过完整
目标页分配预算，输入/decode 增长预留也继续保留。部分传输首先节省的
是提前占用的链路服务，不是线性减少 page 的分配容量。

Qwen3.5 有 40 个模型层、其中 30 个状态层。当前 engine 的
`copy_mamba_state` 在 forward 前等待最后一个 Mamba 层的加载事件。
若必要 Mamba 状态也在 Host，单纯按自然层序传到中间就暂停，可能仍
把大部分恢复留在 forward 前。若 Mamba 已驻留、只缺 FULL，较浅的
提前传输才更容易与后续 forward 重叠。因此层数必须与实际依赖一起算，
不能假设除了第一层以外总能完全 overlap，也不能把模型层数等同于
FULL attention 层数。

## 原生分层执行的接入合同

层数决策器接入前，原生 transfer 需要提供可验证的 prefix/tail 操作：

1. 以稳定的 session、generation、checkpoint、page allocation 身份
   预留目标页，提交前 k 层，并保留 Host 源和目标页所有权。
2. 为部分完成维护明确的层水位；完整 ACK、整页有效性与首次复用记录
   不得提前发布。未提交层的事件不能当作已经 record 的依赖。
3. 请求到达时，先把剩余层及对应事件提交到原生流，再允许 forward
   依据真实依赖开始；不能让请求等待尚未 record 的 CUDA event。
4. Mamba 复制仍遵守其真实前置依赖。若优化恢复顺序，单独验证状态
   一致性和 forward 依赖，不直接移除等待。
5. 暂停的传输不能永久占满有限 producer event ring。传输结束与
   消费者生命周期共同决定事件何时可重用；取消、超时、终态及驱逐
   必须延后释放仍在 CUDA 流上使用的页。
6. 先传 prefix、后传 tail 的字节和时长分别核对；完整 ACK 保留原生
   transaction 语义。需求阶段续传不能全部记成完成边界前的预取。

这些是实现正确性依赖，不是向 agent 添加新的 guard 或通知门禁。
尚未实现的暂停/续传不能通过“预取启动得较晚”冒充。

## CPU 与 GPU 验证

CPU 用独立的逐层离散事件模拟器，核对成本包络与全部可行深度的最优
解；再改变带宽、提前量、FULL 大小及 Mamba 依赖，报告选择深度、
负收益案例、oracle regret 和决策 P50/P90/P99。受控几何与成本不等于
trace 校准结果，更不能用这种 oracle 回收率宣称端到端性能接近 oracle。

本次已完成的 CPU 验证：

- 最终恢复预算、预取、层数和计划核对合并回归 614 项全部通过。
- 5000 次、40 层/3 提前量情形，缓存成本表后的决策 P50/P90/P99 为
  16.69/18.06/21.20 微秒；成本表冷构建 P50 为 86.71 微秒，另行计量。
  此前同算法两次 P50 为 21.07/21.28 微秒，保留 CPU 测量波动。
- 500 个带带宽与提前量误差的受控案例，取得局部模型 oracle score 的
  91.01%，但有 30 个所选动作在实际参数下 score 为负。价格及参数尚未
  由真实逐层数据校准，不能将此 91.01% 当作系统 oracle 收益。
- 512 个 hint 的维护微基准中，100ms 合并将 5000 次 sweep 减至 500 次，
  这段函数墙钟约从 213.69ms 降至 22.69ms；不等于全部控制成本下降89%。

来源绑定与完整案例见
[V1655 CPU 报告](../experiments/reports/v1655_layer_prefetch_cpu_final_20261018.json)，
回归日志见 V1656 XML。没有运行 GPU 或报告新吞吐/JCT。

现有 trace 里的逐层 dependency wait、整批 transfer-stream 时长只能
支持部分参数；依赖等待混合调度、生产者和计算，不足以独立识别每层
传输与 kernel 耗时。缺失参数保留未知，不能用 ACK 时长均分填补。

下一次 GPU 开放后先运行 predictive。恢复两批任务与最新开销优化；
先验证实际开关、容量、重复加载、FULL 提前复用与控制成本，再接入
通过原生依赖验证的分层执行。若新 engine 还未具备 prefix/tail，
必须把实际模式标成 `native_all_layers`，不用“自适应层数”描述该轮。

候选配置：running48、Host200GB/80:20、HBM Mamba/FULL0.9、
context131072、output8192、工具180秒、workflow14400秒；
PREPARE 仅 missing FULL，需求恢复交给 native 逐层路径；
JOIN 最低提前1000ms、tool500ms、总提前量1000ms、
两个恢复上下文、维护间隔100ms，关闭额外机会/终态诊断采样。
这些是开发起点，不是已经测出的最优常量。

新 workload 与当前共享 engine 用一轮新的 native 做参考；开发阶段
不重复每轮跑 native，正式结果再做多轮对比。比较全体任务 JCT、
尾部、完成质量与高压段吞吐，同时单列控制成本、重复恢复、有用重算、
提前且首次复用的 FULL、需求 H2D 与 Mamba。字节多或 ACK 多都不单独
构成系统收益，较短首层等待也不能单独否定整个预取方向。
