# 参考准入下的工具卸载修复轮 R1

本轮已进入真实FULL重放，尚无完整性能结论。只运行FULL，继续复用
V345工具衰减版ThunderAgent。名称使用实际实验的描述与重复编号，
CPU检查和进度观察均归入所属实验，不再各自递增主实验版本。

## 本轮检验内容

冻结代码为`eecf7293e7118cbdee941d56654fce2879df8b5d`。
相对V400b，生产包仅改变两个文件：

- `predicted_tool_offload.py`：reference模式下，未获得活跃资格或
  已有足够物理容量的队首恢复请求，不再挡住后面的有效受益者。
  保留最多8个候选、200ms间隔、预测门槛及逐节点物理重新授权。
- `reference_thunder_admission.py`：每秒状态快照增加实际active、
  paused-pending及marked-for-pause身份，以及未衰减逻辑预算。
  快照不改变策略；参考准入、控制周期、完成及增长记账方法仍直接继承。

两个真实native CPU反例已复现原卸载选择问题。最终共享回归一次
通过3039项及4个子测试，无失败或跳过，保留此前全部3038个身份，
并验证三个实际构造器隔离。证据在
`experiments/reports/v400b_followup/eligibility_shared_cpu.*`。
真实GPU效果和完整JCT/吞吐仍需本轮完成后验证。

## 工作量及实际启动

保持V230全部18个不同项目、3674请求、145565966输入token、
549671输出token、72次JOIN、原生停止及所有正常长尾。工具时间使用
同一V311类别可见合成表，未来样本时间不提供给在线策略。
Device805198 token、Host200000077824 bytes、running/decode24、
种子21及原生树`11b84f7c31986782be3fbe699bae2574ef092389`不变。

- 注册：`ops/reference_consumer_fix_20261027_r1.registration.json`。
- 实际执行目录：`/home/longhao/experiment/.worktrees/beliefkv-causal-eecf7293e711`。
- 端口18660，socket`/tmp/bkv-causal-18660.sock`。
- watcher198769/start ticks729631601。
- server199253/start ticks729634027。
- GPU scheduler199600/start ticks729635483。
- replay driver200694/start ticks729649161。
- 上述服务进程实际nice0，GPU计算PID与owned身份一致。

启动时独立封存179条已完成回执，全部与完整V345的请求、token及停止
一致。共同工作、工具时间、容量、引擎、种子和流式参数保持一致；
已验证前驱V400b完整成功及实际owned退出。真实快照已有新增身份字段，
物理控制健康，没有逻辑借出或召回。回执、状态及遥测是独立非原子前缀，
179条不能代表剩余3495条正确性或完整性能。

启动证据：
`experiments/reports/reference_consumer_fix_20261027_r1/live_start.json`。

## 待检验的排队机制

V400b尾部workflow的一条3秒工具后继等待约194.6秒；其整个等待窗口内
native容量拒绝计数只新增1次，不能把194秒解释成KV复制时间。
旧快照没有逐agent资格，无法确定该请求全程是否inactive。

另一个固定CPU夹具直接执行未改动的参考核心：相同三个program到达和
3秒工具返回，若控制轮落在工具期间，衰减准入可接纳小child，随后
完整容量检查暂停root；root返回时缺乏足够逻辑预算而继续等待。
若控制轮在返回之后，root保持active。该反例没有KV传输，也没有
resident-first，证明一种可能机制，未证明历史194秒的具体原因或频率。
代码与结果为
`experiments/reports/v400b_followup/reference_phase_counterexample.py/.json`。
本轮新增快照用于区分实际资格等待与原生容量/恢复等待。

## 完成后的图表与目标

CPU绘图等待器200332/start ticks729643148，nice19、隐藏CUDA。
它仅在本轮完整成功及实际owned退出后生成与V345匹配的固定60秒时序、
时间加权分布和batch/context图；失败则停止，不启动GPU任务。

- 状态：`ops/reference_consumer_fix_20261027_r1.plots.state.json`。
- 完成后图表目录：`experiments/figures/reference_consumer_fix_20261027_r1/`。

没有后续GPU实验排队。逐层卸载A并复用空间加载B仍未实现；
本轮也未开启sibling特殊共同准入，不能作为全部设计完成的证明。
完整平均JCT和全程输出吞吐明确、可重复改善后，再进入其他baseline。
总体目标保持未完成。

## 收集中已确认的资格等待

本次每秒身份快照已提供此前没有的直接观测。对同一
`beliefkv:01a177aa-05e6-7633-b98d-02added92e81`，epoch38、
129003输入/100输出token：

- R1从native队列登记到首次`run_batch`为78.881921秒；
  完整V345同请求为10.224毫秒。这里首次执行不是CUDA kernel起点。
- 前一轮工具实际持续3015.538毫秒。前一个native请求于
  `1793126804300.4482ms`完成，后继于`1793126807497.9348ms`登记。
  root在后继登记前1412毫秒的快照仍active，前405毫秒的快照已inactive；
  这个状态变化落在两次请求之间，约一秒的采样不能确定精确转换点。
- 后继登记后，快照持续记录该root为paused-pending，直到首次执行附近
  才重新观察到active。1500毫秒最大持有规则将78.882秒全部归到
  inactive采样区间；这不是逐次状态转换日志，不能宣称每一微秒都未准入。
- 该请求服务记账为Host128239、Device547、未缓存217token，
  不能倒推出提交时的驻留位置。含前后诊断范围的全局`native_NO_TOKEN`
  只由167变为168，也不能把这79秒解释成该请求的GPU复制等待。

独立封存的工具后继前缀含1971个已观察首次服务的后继，其中306个
native排队至少一秒。这306个区间合计2238.630秒：
inactive采样持有2069.759秒，占92.4565%；active161.015秒，
占7.1926%；unknown7.856秒。这里按排队时间筛选诊断对象，
没有删除任何workflow或正常长尾，也没有估计可减少的JCT。

这个结果把后续瓶颈检查指向资格选择及返回连续性。它没有证明衰减的
某个选择是全部等待的唯一原因，也没有证明逐层容量交接没有价值；
不能把修改传输实现当作这段资格等待已经得到解决。

新增`audit_request_agent_eligibility.py`绑定真实完成回执、同身份
native提交/结果、首次`run_batch`和五个独立封存前缀。针对身份、
时钟、样本缺失、重复、完整生命周期和unknown的检查，以及现有工具、
资格与预算审计，一次通过110项CPU检查。

通用收集审计曾因reference缺少旧retained的逐agent阻塞预算字段退出。
该CPU失败单独保存在`collecting_pipeline_initial_failure.json`。
修复后明确保留reference预算分解为unknown，实际核验3191/3674条
回执与完整V345的请求、输入输出token、hash及原生停止一致。
其1644个预算快照只有聚合观测，不补造逐agent预算。

证据位于`experiments/reports/reference_consumer_fix_20261027_r1/`：

- `mobile_game_epoch38_eligibility.json`及
  `control_mobile_game_epoch38_eligibility.json`；
- `tool_reentries_collecting.json`及`tool_eligibility_waits_collecting.json`；
- `collecting_pipeline.json`、`collecting_eligibility_cpu.xml`；
- `collecting_review.py/.json`绑定上述报告及分析源码摘要。

这些是收集中诊断，不是完整R1性能结论。运行代码仍冻结为
`eecf7293e711`，尚未实施新调度策略或逐层空间交接，也未安排额外GPU轮次。
