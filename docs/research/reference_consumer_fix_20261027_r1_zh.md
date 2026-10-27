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
