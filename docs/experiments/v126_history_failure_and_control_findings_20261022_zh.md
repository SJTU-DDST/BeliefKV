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
