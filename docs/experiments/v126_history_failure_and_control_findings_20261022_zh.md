# V126 历史工具调用故障与控制路径诊断

日期：2026-10-22，北京时间。

V126的no_join_prefetch在21:01左右结束，driver原始退出码1，36项中
35项完成且测量有效。监督器保留stopped_for_investigation并停止后续组；
不能以35项完成均值代替整组JCT，也不把应用协议错误归因于预取。

## 确认的故障

失败任务为
locobench__python_game_simulation_medium_033_feature_implementation_expert_01。
模型一次返回两个合法task和一个参数JSON非法的task。合法调用执行并返回，
但LangChain在下一次请求中仍将AIMessage.invalid_tool_calls序列化为
assistant.tool_calls，SGLang严格解析历史arguments时返回400。
原始trajectory保留该非法调用及两个真实工具结果。失败并非输出token上限、
GPU恢复超时或最后那个重复Git调用的workflow造成；后者最终自然完成。

main修复只作用于发给模型的历史副本：非法调用的原始名称、ID、参数以
明确的“未执行”文本保留，有效调用和真实结果不变；原始graph与审计
保留invalid_tool_calls，不猜测/修补参数，不创建工具结果，不补发工具，
不强制模型再spawn。正常历史直接返回原request，仅异常历史分配副本。
Full、native、所有消融使用同一修复，不修改运行时KV或调度策略。

V2827使用真实失败trajectory、已安装客户端的序列化函数及原引擎的严格
参数解析函数进行CPU重放：原始5条历史调用中1条复现同一错误，修复后
4条有效调用全部通过，原始graph和真实ToolMessage未修改。正常512条
消息的检查中位约69.5us，P99约74.8us，不创建历史副本；这是独立CPU
测量，不是GPU/JCT收益。V2828保留此前4210项测试并新增11项，4221项
及2个subtest全部通过、无失败或跳过；V2829绑定应用源码，原引擎不变。

V127保留36 roots、running/decode8、seed21和原定V108/EOS配置，
重新运行no_join_prefetch，再执行no_background_prepare、
no_tool_duration_model、no_child_reclaim、full、native_matched。
V126状态、退出码和全部trace保留；V125第3、8、9项仍取消。
登记以../ops/v127_eos_seeded_modules_pair_20261022.registration.json
及对应registration.state.json为准。

## 本轮形成的诊断证据

V2823封存了运行中的JSONL完整前缀及哈希；这是各文件独立截取，
不是原子快照，也不是完整运行的终态分析。

- 工具FULL预取112条，共2.8405GB；61条有物理首次复用证据，约1.5350GB。
  21个提前提交的独立工具等待中，8个确认早启且复用。
- 67个等待在完成前出现正FULL计划，55个等待出现过容量限制记录；
  原因可重叠，不能将差值都认定为单一失败原因。
- 保护到期不等于物理丢失：89条由已有锁覆盖的传输中52条仍被确认复用。
  ACK到首服的命令级P50约4.29s，独立提前传输事件级P50约11.97s；
  两个口径不同，均不能直接称为可减少JCT。
- V2824观察到5238批后台FULL备份，约157.68GB，每批平均30.10MB，
  中位11.80MB。没有批次触及512MiB/32节点限制，dirty索引未显示检查
  上限截断，现有证据不支持继续增大批次或扫描规模。
- 29次带诊断的容量回收中，8次FULL目标未满足，3次记录未备份冷页，
  共4个unbacked拒绝观察；session_referenced拒绝233次。它们是重复
  检查观察，不是独立页/字节统计，不支持绕过共享与执行引用直接回收。

V2825的完整600--1800s窗口中，该消融/native历史参考的decode速率为
533.71/516.94 token/s；uncached输入为2309222/1981726 token。
这是不同轨迹、不同应用版本的描述性参考，不是新同版native或合格加速比。

V2826进一步匹配短单请求prefill：512-token输入分桶、2048-token前缀
分桶及相邻阶段/batch匹配时，47组的worker区间均值约12.21/9.36ms，
比值1.305；launch到结果处理约64.17/48.50ms。16-token分桶仅剩6组，
worker比值约1.445；精确token匹配没有达到每侧5个样本的分桶。
worker区间不是纯GPU计算；阶段采样的CPU/GPU区间重叠，不能相加或
直接从JCT扣除。短prefill成本仍需继续定位，不能以粗平均变快宣布解决。

下一步保留已安排的消融，首先修复协议可靠性并收集完整同版比较；
继续检查短prefill的run_batch路径和恢复到首服的协同，不根据
logical Host hit、保护到期数量或单轮工作量差异扩大预取/保护。

## 分阶段配对复核

此前80/68个短prefill的run_batch均值约52.56/41.94ms，来自不同样本，
不能直接把差值称为新增控制开销。V2831改用
scripts/analyze_matched_prefill_phases.py，在输入长度、前缀、实际graph状态、
overlap模式及相邻阶段/batch分桶内配对；每项计时独立检查双侧覆盖。
缺失计时、不支持的分桶保留unknown，不补零。V2830的7项CPU测试覆盖
样本组成偏差、分项覆盖不足、邻接缺失、重复身份和非法时长。

V126的600--1800s中，512-token输入分桶及2048-token前缀分桶、每侧至少
3个阶段样本时，只有4组可用。加相邻阶段/batch限制后覆盖17/15个样本，
共同权重15：

| 阶段 | V126墙钟 / ms | 历史native墙钟 / ms | V126线程CPU / ms | 历史native线程CPU / ms |
| --- | ---: | ---: | ---: | ---: |
| selection | 13.388 | 12.774 | 12.147 | 10.836 |
| run_batch | 51.436 | 39.391 | 49.972 | 38.495 |
| launch telemetry | 0.187 | 0.178 | 0.187 | 0.178 |
| post-launch physical | 0.815 | 0.003 | 0.805 | 0.002 |
| result processing | 4.231 | 3.802 | 4.229 | 3.802 |

launch telemetry嵌套在run_batch内，不能重复累加。64-token或16-token
输入分桶均没有满足样本要求的阶段对照，已有恢复等待也不能与这组
样本形成足够的精确配对。这是定位线索，不是精确同工作量、同版本的
性能证明。线程CPU包括原生运行库工作及可能的忙等，不等于Python控制
执行时间；GPU run_batch跨度还包含依赖和提交间隔。

源码核查显示run_batch主要进入原生输入解析、ForwardBatch元数据、
模型forward/采样和结果拷贝，新增post-forward规划在另一阶段。
当前不能将约11.48ms的run_batch线程CPU差直接归因于该阶段之外的
约0.80ms控制时间，也不能因此排除跨批次迁移干扰。继续用已排队的
no_background_prepare与后续同版Full/native检查这条假设，不改正在执行的
冻结策略。

V2832优先复核V127最新已完整覆盖的600--780s窗口：短prefill服务记录
为217/193个，但阶段采样仍没有满足每侧3条的共同分桶，结果显式为unknown。
不能从旧trace的配对结果推断新一轮已经改善或回撤。

V2834补齐每个计时分桶内真实输入和前缀的最小值、均值、最大值，
只统计确实贡献该项计时的样本；保持所有不支持的细分桶。V2833最终8项
CPU检查全部通过，新增用例验证同宽分桶中的实际工作量差异不会被隐藏。
旧V2831/V2832所用分析器源码已按原SHA256归档在
experiments/reports/v2831_matched_prefill_analyzer_source_20261022.py，
不改写旧测量。本次改动只涉及离线分析，V127仍执行冻结的aa1ab19版本。

## 最新 V127 配对结果

V2836优先使用V127已经完整覆盖的600--1800s窗口，继续与历史V122
native比较。短单请求prefill服务记录为1334/1207个；512-token输入、
2048-token前缀及相邻阶段/batch匹配后，阶段计时只有4个共同分桶、
21/15个样本，共同权重14。run_batch线程CPU均值为36.619/36.482ms，
比值1.00374；post-launch physical为0.802/0.002ms。64-token输入分桶
仅剩1组、每侧3个样本，run_batch比值1.07523；16-token分桶没有足够
共同样本。分桶中仍保留实际输入/前缀分布，粗分桶相同不代表工作完全相同。

因此，V126中约1.30倍的run_batch CPU差距没有在这次最新观测中稳定
重现，不能继续将它描述为V127已确认的固定开销。V127没有修改模型
forward或服务端调度实现，客户端非法工具历史修复也不能被解释为GPU
加速的因果来源。轨迹、批次与运行环境差异仍待同版Full/native核查。
额外post-launch控制仍有约0.8ms的采样线索，后续应结合已排队的
no_background_prepare检查迁移与控制的影响，不据此改写冻结实验。

完整证据为experiments/reports/v2836_v127_latest_prefill_phases_600_1800_20261022.json；
输入前缀哈希和观测边界保留在报告中。历史native尚未包含最新种子及
工具历史修复，这些结果仅用于诊断，不构成完整JCT、同版性能或消融收益
的验收结论。V127六组仍须收齐并核对逐workflow工作量与完成资格。
