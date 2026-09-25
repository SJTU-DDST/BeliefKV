# Qwen3.5 预测式动作证据与上线门禁

日期：2026-09-25。本文记录原生 reactive 训练集和已使用过的校准集的只读诊断；
不改变 GPU 调度，不开启预测式物理传输。测试集仍未用于阈值选择。

## 1. 预测头与整组 JOIN

校准集是与训练项目隔离的 Astropy/Sphinx 66-root 批次，但它**已经用于当前
模型的区间校准**；下列诊断不等于新的独立测试。原有评估结果：
boundary accuracy 0.93445，与多数类基线 0.93444 几乎相同；FINAL 召回
0.00278，tool error 召回 0.00754。child RETURN 剩余时间的
episode-weighted MAE 约 816.6 秒；校准区间均宽约 3238 秒。

新增的 `scripts/diagnose_native_join_groups.py` 使用每个完整 JOIN 的首个
WAIT_JOIN 快照，把尚未返回的 child 边际 P10/P50/P90 按 ALL 取 max，
并和该 JOIN 的实际 parent reentry 比较。仅纳入实测 reentry 等于最后
一个 member RETURN 的 ALL-compatible 事件；数据没有明确 JOIN mode，
因此这不是 ALL mode 的严格证明。64/64 个 JOIN 具有完整提示；平均/中位
绝对误差约 1238.6/1016.4 秒，预测 P50 平均晚于真实时间约 748.7 秒。
边际分位数组合的 P10--P90 覆盖率为 87.5%，平均宽约 3966.9 秒；
**此覆盖率不是校准过的联合 JOIN 区间覆盖率**。64 个事件不是 44,258 个
独立 JOIN 样本，更不能用其调在线 latest-start。

诊断 JSON：`experiments/raw/qwen35_native_reactive_calibration_20260925_v1/`
`evaluation_calibration_diagnostic.json`、
`join_group_diagnostic_calibration.json`。

新增逐 JOIN 临近窗口诊断：距真实 reentry 2 秒时 64/64 个 JOIN 有
WAIT_JOIN 快照，P50 绝对误差约 **44.7 秒**，无一例达到 500 ms；
500 ms 窗口内 61/64 有快照，P50 误差仍约 44.7 秒。窗口按事后真实
reentry 选取，**不是在线模型能识别该窗口的证据**。现有 child-return
边际分布即使接近终点仍明显偏晚，不能直接用于短窗大块 H2D。

逐工具等待 episode 诊断有 17,805 个合格完成事件：首次等待快照
真实剩余时长 P50/P90 约 94/721 ms；模型绝对误差 P50/P90 约
86/360 ms。但不做预测、立即返回的零基线已有 **81.6%** 的
500 ms 命中率。真实剩余至少 2 秒的 197 个 episode，模型误差
P50 约 2.58 秒，500 ms 命中率 0%；至少 10 秒的 11 个事件
误差 P50 约 23.56 秒。因此总体误差小不代表具有可迁移 KV 的
提前量。报告：`join_group_horizon_diagnostic_calibration.json`、
`tool_return_horizon_diagnostic_calibration.json`。

对于自然语言 child 终态（无需 `ChildCompletion` 工具），只读回放
检查了 66 个 workflow 的 231 个 spawn child：229 个出现候选，
其下一关键事件均为 RETURN。64 个合格 JOIN 的最后 child 均有候选，
从候选到 RETURN 的提前量中位约 **180 ms**、P90 约 226 ms；
只有 3 个达到 500 ms。历史原始日志没有 finish reason 和
invalid tool call 计数，此处只是弱信号的事后审计，不是上线保证。
新埋点保留这两个字段；仅单个完整、非内部、非空、没有工具调用或
无效工具调用、且 finish reason 未提示截断的 child 响应产生
`natural_final` provisional intent。已有 JOIN/epoch/session 身份检查和
安全点物理检查不变；晚到的模型 JOIN hint 不再覆盖更新的
provisional/confirmed ticket。信号过期或取消立即撤销 ticket。
信号仍不足以独立隐藏大块 H2D，且当前物理动作资格保持关闭。
只读报告：`child_terminal_signal_diagnostic.json`。

进一步按决策时间顺序进行**首次触发**回放，而不是事后选取临近
RETURN 的快照。预先列出 500 ms、2 s、10 s 三个观察窗口：
64 个 JOIN 的约 44,254 个可评价快照中，整组 child 边际 P10 在
500 ms 和 2 s 门槛下均触发 **0/64**；10 s 门槛触发 45/64，
其中 21 个在真实 JOIN 前超过 10 s，23 个距 JOIN 不足 500 ms，
只有 1 个处于 500 ms--10 s。工具的 17,805 个完成 episode
在 P10≤500 ms 时全部于首个等待快照触发；其中 3,282 个真实
剩余时长超过 500 ms，包括全部 197 个不少于 2 s 的长等待。
**降低门槛并不能得到既有提前量又有选择性的动作。**两个诊断仅
使用当时特征决定首次触发；完成事件限定回放对象、事后衡量提前量。
这是基于决策事件的上界评估，不包含真实 worker 排队、安全点延迟、
PCIe 和中途被取消的等待，也不是未经使用的独立测试。
结果：`join_group_online_trigger_diagnostic_calibration.json`、
`tool_return_online_trigger_diagnostic_calibration.json`。

## 2. PCIe 时延证据

`scripts/fit_native_pcie_service.py` 对 train 的真实传输按方向分头拟合，
只在 calibration 上验证。保留三种**不混淆**的时延：提交至 ACK、
真实 DMA start-to-complete、提交至真实 DMA start 的排队。训练批次
H2D/D2H 的提交至 ACK 观测分别为 20,403/1,266,970 次，校准批次为
8,919/55,150 次。校准的 H2D/D2H ACK 绝对误差 P95 分别约 24.7/8.2 ms，
相对误差 P95 约 0.579/1.476；D2H 训练残差 P90/P95 覆盖仅
0.672/0.842。两批次 `start_ts_ms` 覆盖均为零；
`transfer_stream_elapsed_ms` 是无墙钟起点的设备 stream 观测，不能据此
拆出可用 DMA service 和排队时延。两方向 service/queue 都标记 unavailable。
PCIe 行缺乏可靠 workflow 归属，每个 split 实际只有一个 run 评价组，
几十万命令不能当成几十万个独立组。模型验收失败，未供在线 latest-start
使用；脚本以退出码 2 明确表示门禁拒绝。

结果：`experiments/raw/qwen35_native_reactive_calibration_20260925_v1/`
`native_pcie_service_diagnostic.json`。下一轮必须在原生传输路径采集与同一
时钟域对应的 DMA start/end 边界和提交边界、实际 extent/页形态、并发
负载及可验证的作用域归属；不能把 submit-to-ACK 或 stream elapsed
直接重命名为 DMA 服务时间。须按 workflow/episode 分组验证物理误差。

## 3. 动作级证据与缺口

旧 `action_targets.py` 的 v4 工具标签继续供旧 Qwen3 BF16 artifact 使用，
不把其中 `kv_bytes_per_token=98304` 和旧 transfer anchors 套用到 Qwen3.5
FULL/Mamba。`export_p6_action_targets.py --native-reactive-only` 导出独立的
reactive 观测：JOIN parent reentry、child 恢复、READY 至 LLM submit
的时间边界，以及能证明的资源快照、同期 native transfer。每行明确区分
估计、实测和未知。无新版可验证的混合池物理时延与 KV residency 时，
估计 H2D 耗时、latest-start、可用 KV 字节、物理动作资格、物理收益
全部为 null；旧 v4 训练读取器自动跳过这些诊断行。

校准批次的时间边界合格决策行：JOIN 44,258、child 恢复 106、
准入 20,547；去重后分别为 **64、53、18,940 个事件**。此前
`join_wait` 下一边界标签错误地把所有 JOIN 行排除；已改为同一 JOIN
完整 member RETURN 与 reentry 的资格，不把未来标签混入当前特征。
训练批次对应的独立 JOIN/child 恢复/准入事件分别为
123/51/32,774；其时间边界合格决策行分别为
86,290/102/37,224。两批次结果见各自目录下的
`native_action_observations_report.json`。这些是边界观测，**没有一例能
从未执行的 reactive 轨迹证明 PREPARE_HOST、COMMIT_CPU、PREFETCH_GPU
的反事实收益**。当前模型继续保持 `action_target_count=0`、
`predictive_action_eligible=false`、`online_eligible=false`；
不得将本诊断报告冒充动作拟合/校准产物。

## 4. 验证顺序

1. 先取得物理起止/排队、作用域、FULL/Mamba residency 和 KV
   字节/页形态的可复验遥测，并在 train 项目拟合、独立项目校准；
   按方向、负载及物理形态报告误差，不借用旧 BF16 anchor。
2. 以完整 JOIN 为单位报告时间误差和预取提前量，控制误报率；
   DONE/RETURN 确定性通知、child READY 与 scheduler 准入应各自
   单独报告命中/过时率。提升关键 boundary 与 child-return 时机，
   避免把宽区间解释成精确预测。
3. 在训练数据真实具备动作身份、可用 KV、物理 transfer/ACK、
   first service、useful/wasted 结果之后，才拟合动作头；
   使用项目隔离的校准与独立测试检查收益，censor/intervention
   轨迹不得视为自然结果。
4. 通过前述门禁后，再先跑只读 shadow（safe-point 新鲜度、语义
   intent 到可执行动作的转化、时间开销），之后做有界 GPU canary
   验证 D2H/H2D 和首次服务以及吞吐，对比同配置 reactive。
   **本次证据未通过第 1--3 步，因此不运行物理 canary。**

## 5. 下一轮预测与在线适应

采用分层时机而非要求同一回归头精确预报长程 JOIN：工具的可观测
完成通知、child 终态或 RETURN、父 JOIN 条件、scheduler 准入分别
作为独立时钟起点。长程概率只用于预算有限的候选排序和部分
PREPARE_HOST，须有可卸载概率、可重用 KV、物理成本及浪费上限；
临近的结构化或自然终态只缩小触发窗口，确认 RETURN 后可继续按
真实 ACK 完成迁移。需要更早隐藏大块 H2D，必须收集 child 进度
或外部工具可靠的**前置完成信号**，量化其提前量、误报和调度延迟，
不能把平均返回时间当作精确截止时间。

预测调度改变服务顺序、工具等待和缓存命中，reactive 轨迹上拟合
的剩余时间分布会偏移。先为每个决策保存策略版本、动作倾向、
可观测完成/取消和延迟标签；按 workflow 分组监测各压力档位、
工具类型、child 状态下的误差、覆盖和动作净收益。线上更新仅以
已完成且非干预/非截尾的标签滚动校准，保留独立测试项目和
reactive 对照；阶段性更新需重新经过误报、安全和物理收益门禁，
不能由自选的 predictive 轨迹直接声称无偏增益。

新版原生运行时已补齐可实时观测的部分训练特征：同一
`observed_boundary_action` 定义的最近边界、工具后端/命令类别、
同时等待工具数和家族压力、以及活跃 LLM 批次的生成 token。
对于已被接受且仍存活的工具/JOIN 提示，运行时在等待 2 秒后
可于 worker 空闲时有限速地重估存活时间；身份或状态变化仍使
旧结果失效。2026-09-25 补充逐 context 的 tool hint 和逐 JOIN
的 parent hint：同一批 JOIN 最多提交 8 个目标；单进程 worker
仍只有一个在途批次，但待处理任务按身份合并，同类最多缓存
8 个，tool 与 JOIN 交替服务，并为 demand 保留间歇机会。
空闲时每 500 ms 最多补扫 8 个无 hint 等待目标，游标轮转覆盖
超过单批上限的并发等待。每个结果仍需通过 workflow、
session/epoch、invocation revision 和 JOIN child 集合的新鲜度检查；
旧结果只由同身份的新版本取代，其他 context 不会被覆盖。
这修复的是预测**覆盖**，并不能单独修复概率头对短窗口
无判别力或 JOIN 时间误差大的问题。这些修改尚无独立负载验证，
`online_eligible=false` 和物理动作资格保持关闭。

复现脚本：`scripts/diagnose_native_join_groups.py`、
`scripts/diagnose_native_tool_returns.py`、
`scripts/diagnose_native_child_terminal_signals.py`、
`scripts/fit_native_pcie_service.py`、
`scripts/export_p6_action_targets.py --native-reactive-only`。

## 6. 完成通知条件下的短窗预测（2026-09-25）

`scripts/audit_native_return_cues.py` 在同一训练/校准 trace 中揭示：
仅凭 `ready` 且边界历史以 `final_answer` 结尾，首次出现时仍有
训练集 140/428、校准集 79/224 个 child 距真正 RETURN 超过
2 秒；不得将历史最终回答当作确定性结束。必须用 runtime 的
非内部、单条完整模型回复且不含工具调用的**当前完成通知**，
并绑定 child、JOIN、context epoch 和 parent session。

`scripts/fit_native_completion_lead.py` 仅用七个 train 项目的
428 个已完成 child 通知拟合独立的完成通知→RETURN 短窗头：
P10/P50/P90 分别约 145/186/246 ms。对项目隔离的 Astropy/Sphinx
64 个完整 JOIN 的最后 child，P50 **绝对误差中位 21.6 ms**，
P90 55.9 ms，63/64 在 500 ms 内，P10--P90 覆盖 84.4%。
这比通用 child 时长头在事后选出的 JOIN 前 2 秒快照上
约 44.7 秒的 P50 误差显著准确，但**两种指标的触发时刻不同**，
不得说成早期 JOIN 预测达到 21.6 ms。扩大审计至所有已派发
child 后，训练组有 439 次信号，428 次属于合格 JOIN，11 次
对应不合格 JOIN 的实际 RETURN；校准组为 229 次，其中
224 次属于合格 JOIN，5 次实际 RETURN 但 JOIN 不合格。
两组均未观察到信号后继续调用工具或取消，但分别有 4、2 个
派发 child 未产生信号；旧 trace 未完整记录 finish reason，
样本有限，不能据此证明在线零误报。
训练组最后 child 通知至 RETURN 的中位提前量仅 172 ms；
校准组约 180 ms，64 次仅 3 次达到 500 ms。该头主要用于
短时间内的部分 KV 或晚期重排，不能单靠它隐藏大块 H2D。
runtime 的 `read_only_join_completion_forecast` 在信号晚到、
身份失效、JOIN 结束或 P90 窗口耗尽时拒绝提示；
`join_intent_delivery_le_*ms` 记录实际控制链投递年龄。
物理动作资格保持关闭，尚未在新代码 GPU trace 上验证投递年龄。

作为更早候选的 `TOOL_END` 提供较长提前量：若下一轮恰为 child
RETURN，训练/校准组中位提前约 73/34 秒，但训练组仅
380/21,031、校准组仅 196/10,574 次 `TOOL_END` 满足该条件。
`scripts/pilot_native_preterminal_classifier.py` 用事件当时可见特征
训练，项目留出组中得分最高 1% 的精度仅 7.6%，隔离校准组
仅 6.6%；不足以驱动预测性 H2D，未接入线上。
另一个 child RETURN 树回归试验在隔离校准组首次 JOIN
快照的中位绝对误差仍约 1066 秒，近 2 秒快照约 48 秒；
`scripts/pilot_native_return_model.py` 仅留作阴性对照。
要提前数秒且保持高精度，需采集更早的可靠完成阶段信号，
并在新测试项目和实际调度下验证其领先量、误报率及控制链延迟。

为寻找更早边界，可在独立诊断 workload 使用
`scripts/run_deepagents_swebench.py --stream-completion-shadow`。
该选项将模型响应切换到流式，并将 child 首个非空正文片段记为
脱敏的 `beliefkv_child_first_content_shadow` 事件；事件只写
trace，不进入调度控制通道。`scripts/audit_native_stream_shadow.py`
用于统计误报、距离 RETURN 的时间和最后 child 覆盖率。
**默认关闭**：流式模式会改变工具调用与服务路径，不可与既有
非流式训练/校准批次混合，首正文也不能直接证明 child 即将结束。

隔离单 workflow 的 `qwen35_completion_stream_pilot_20260925_v1`
实测 6 次首正文通知，3 次最终 RETURN、3 次继续调用 `execute`；
3 个真阳性距 RETURN 的 P50 提前量约 1.89 秒，
**精度只有 50%**，不能开启预取。尝试在诊断提示词中要求
child 最终自然语言答复以特定前缀开头；对应
`qwen35_completion_marker_pilot_20260925_v1` 中 3 个正常
RETURN 都没有输出此前缀，覆盖率为零，已撤销该无效试验代码。
两次 pilot 都只有单个 workflow，且使用不同于正式批次的
流式模式、较小并发和零 Host 池；仅证明该原始信号不够可靠。
另一诊断 `qwen35_ready_handoff_pilot_20260925_v1` 给 child
提供 `ready_to_finish` 工具并提示它在最终自然语言报告前声明；
3 个 child 正常返回却没有一次调用该工具，故撤销这套零覆盖率
的试验代码。这个结果**不能**证明受运行时强制约束的两阶段
完成协议无效，只证明单纯添加工具/提示词不足以建立该协议。
旧校准 trace 中 `write_todos` 仅覆盖 14 个 child，其 58 次
工具结束只有 1 次的下一模型回复直接 RETURN；它也不是可靠
的普适完成阈值。

早期流式试验绕过了 `BeliefKVChatOpenAI` 非流式 `_generate/_agenerate`
入口，没有可靠地向服务端注入 RID 与 invocation 身份；上述 pilot
仅用于观察输出形态，不能验证正式服务条件下的预取误差。
现同步/异步 `_stream/_astream` 都做上下文预检、RID 注入、
活跃请求跟踪和异常 abort；LangChain 隐式进入流式路径时，
由调用作用域的 `ContextVar` 传递 run manager。首个工具调用
片段也以脱敏、只读事件记录。旧的
`qwen35_stream_tool_chunk_{pilot,test}_20260925_v1` 中，等待
2 秒且尚未观察到工具片段的规则在不同项目上仍有误报，
不得以此开启预测传输。

`qwen35_forced_completion_contextbound_20260925_v1` 曾向 child
加入 `finish_work` 并强制每次调用一个工具。41 次 child
请求确实发出了 required tool choice，但没有一次调用
`finish_work`；三个 child 均未正常 RETURN，最终被取消。
该方案改变了任务执行轨迹，不能作为精度样本；强制工具选择、
额外工具及提示词已撤回。仅保留与正常工作负载兼容的流式身份
修复及只读事件。后续验证须同时报告误报率、正常 child RETURN
覆盖率、提前量，以及按完整 JOIN 计的误差。

修复身份后的正常子代理流式诊断
`qwen35_contextbound_stream_normal_20260925_v1` 有两个项目：
Astropy 的四个 child 中仅两个自然 RETURN，首正文后等待
1.5 秒且未出现工具片段的提示在这两个 child 上无误报，
但样本太少，不能据此证明泛化；Sphinx 四个 child 全部取消，
没有可评价的真实终态。流式执行还改变了任务轨迹，
默认保持关闭。

另一只读试验 `scripts/pilot_native_join_progress.py` 只用训练集
已完成 JOIN 的当时可见 child 进展拟合剩余时间，按完整 JOIN
在既有项目隔离校准组验证。首个 WAIT_JOIN 快照中位绝对
误差由原头约 1016 秒降到约 581 秒，但 64/64 个 JOIN
都没有进入模型预测的 2 秒触发窗口。仅改变损失尺度结果
仍约 572 秒；同为 66-root 压力下 Astropy→Sphinx 及
Sphinx→Astropy 的项目互测误差分别约 1284/363 秒，亦均无
2 秒触发。这是离线模型诊断，不构成可上线的精度提升。
训练集 128-root 与校准集 66-root 的负载差异并非唯一原因：
任务尚未给出完成信号时，仅由累积执行量不能倒推出剩余
完成时间。大块 H2D 应继续依赖有容量预留的准入交接或
较早且可验证的阶段边界；不得通过缩小区间、事后选快照
或降低触发门槛虚构准确率。

工具时长有一个可修复的可观测特征缺口：旧 `execute`
调用的 `command_class` 仅退化为工具名。只读的
`scripts/pilot_native_execute_timing.py` 将旧 root trajectory
按工具调用 ID 对齐到已完成工具时长，训练组 5,363 次，
项目隔离校准组 3,052 次。仅用测试命令、Python 临时代码、
Git 等粗分类，校准组调用起点的中位绝对误差由约
332 ms 降至 167 ms；但真实时长至少 2 秒的 122 次中，
误差仍约 2,018 ms（原约 2,261 ms）。child 的命令没有
记录在旧 trajectory 中，不能将这个 root 子集结论外推
至全部 child。新增 TOOL_START 脱敏字段
`observed_command_class` 和 P6 工具行持久化，不存储原命令；
不更改当前线上模型使用的 `command_class`，以免旧 artifact
匹配键变化。需要从新埋点收集完整 child 样本、重训并独立
验证长工具调用后，方可考虑启用。
在完全相同的 3,052 次首个 TOOL_START 决策快照上，当前
已校准 Frontier 工具头的中位绝对误差约 321 ms，分类候选
约 167 ms；64 个 workflow 等权的中位数约 347→126 ms。
这个对照证明新特征在 root `execute` 子集上优于现有头，
**不证明**已能准确预测 2 秒以上调用，亦不证明 JOIN 提前量。
同批 122 次长调用的两者误差中位分别约 2,419 和 2,018 ms，
仍不满足在数百毫秒内安排大块 KV 传输的目标。进一步拆分
单例/模块测试在隔离项目上略改进短调用，却恶化长调用，
因此保持低基数粗分类。

非流式低压 child 诊断
`qwen35_execute_class_child_pilot_20260925_v1` 的四个
workflow 中三个完成、一个不完整；共观测到 83 次完成的
child `execute`。直接套用 root 先验时，按调用统计的
全局中位数→分类候选误差为约 350→506 ms，Sphinx
子集恶化，不能迁移该先验到 child。仅 3 次 child
调用长于 2 秒，没有可验证的长尾收益。新增脱敏
`is_child` 标志并导出到工具行和决策快照，以便后续
单独训练 child 工具头；旧模型仍不消费此字段。

短窗头可以通过同时设置 `BELIEFKV_COMPLETION_LEAD_ARTIFACT`
和 `BELIEFKV_COMPLETION_LEAD_SHA256` 显式加载经 SHA-256
固定的诊断 JSON；缺任一项、哈希变化或非只读诊断状态均拒绝。
它仅提供只读剩余时间三分位，不改变物理动作资格或默认配置。
截至本次 pilot，尚无带该头的 GPU 控制链投递延迟分布，
更早且可靠的 JOIN 终态信号仍未找到。

## 7. 新版工具时长特征契约（2026-09-25）

Frontier artifact schema v7 增加 `tool_feature_contract`。默认 `legacy`
完全保留旧模型的工具特征键；新 Qwen3.5 训练入口显式选择
`observed_command_child_v1`。该契约使用 TOOL_START 时可见的脱敏
`observed_command_class`（`execute` 为粗命令类别，其他工具为工具名）。
工具时长的 root 和 child 各自训练独立的
生存时长分布和回退层次，child 样本不足时不借 root 全局时长先验；
数据缺少 TOOL_START 身份或观测类别时直接拒绝训练。运行时通过
同一事件传递原始旧类别及新类别，按加载的模型契约选择特征；序列化
和旧 artifact 加载已有定向测试。当前改动只修复可观测特征与训练/服务
不一致，**尚未证明 child 的长尾时长或早期 JOIN 窗口改善**。
新数据必须用新埋点重新采集；此前旧批次没有该契约必需的标签，
不能直接按 v7 重训。需要报告项目隔离的 root/child 分层误差、
不少于 2 秒调用的误差、首次触发的提前量及误报，物理动作继续关闭。

在 v7 训练检查中发现 TOOL_START 的跨 invocation 混用：同一 workflow
多个 child 同时 WAIT_TOOL 时，旧导出行只有 `trigger_id`，训练曾将
新工具的类别套到全部等待 child 上。旧训练集 31,863 个工具触发行中
335 行包含多个 WAIT_TOOL，最多污染 344/32,207 个工具等待样本；
这个比例不足以单独解释原有的大误差，但会破坏 root/child 分头的
可验证性。隔离分支增加 `trigger_invocation_id`，新契约只用该调用的
标签拟合工具头和做 terminal 校准；对其他 invocation 的预测特征不
借用触发工具的类别或后端。导出器对每个 invocation 保留原生
parent/child 身份，TOOL_START 当前调用可从事件身份交叉验证；
此前只对部分 JOIN 标签补 child 身份会导致工具预测分头回放失真。
之前正在运行的高压采集保存了原始事件，
必须待完成后**重新导出决策行**，不能把旧导出文件当成已修正数据。
在旧 66-root 校准 trace 上独立重导出预检确认：17,805 条 TOOL_START
决策全部具备触发 invocation ID，所有 invocation 快照都写有
`is_child`；291 条决策同时包含多个 WAIT_TOOL。重导出 manifest
通过本地训练证据完整性校验。预检完成后仅删除这次创建的 14 GB
临时导出，原始校准证据保持不变；该旧 trace 缺少新版命令类别
观测，不能用于新版工具头的正式独立校准。

只读 `scripts/pilot_child_execute_generalization.py` 对运行中的高压
train 已完成的 1,703 次 child `execute` 调用拟合粗类别时长中位，
在项目隔离但压力不同的早期 Astropy/Sphinx pilot 83 次调用上，
全局中位先验到分类先验的逐调用 P50 绝对误差约 181→90 ms，
按 4 个 workflow 等权的 P50 约 164→151 ms。仅 3 次调用超过
2 秒，其误差仍约 2.01→1.65 秒；不是本次 Frontier v7 artifact
的正式精度，训练样本还在增长，不能证明高压泛化、物理动作收益
或 JOIN 长程误差已达标。留存的初步报告为
`experiments/raw/qwen35_execute_class_child_pilot_20260925_v1/`
`high_pressure_child_category_preliminary_20260925.json`。
后续训练尚未完成时的只读留一项目消融（4,309 次已完成 child
`execute`，343 次真实时长 ≥2 秒）发现：仅用全局中位先验的逐调用
P50 误差约 212.5 ms，纯类别先验反而约 236.2 ms；对每个 workflow
等权的 P50 为约 205.7→217.9 ms。类别权重取 0.5 时等权误差
约 192.2 ms，但长调用 P50 仍约 2.22 秒，与全局先验无实质差别。
这说明早期两项目 pilot 的收益不稳健，不应仅根据中位误差将
类别键部署为精确返回时钟；优先等待新版正式 artifact 与隔离校准。
额外尝试把调用输入字节数做长度分桶，在早期隔离项目的 83 次 child
调用上反而使中位误差恶化，因此没有把它加入在线特征。

高压调用的 sandbox audit 此前仅记录 `sandbox_execute` 总耗时；
该计时从拿内部执行锁**之前**开始，又缺少容器身份，不能把它解释成
纯命令服务时间，更不能凭时间顺序配对并行 child 的 TOOL_START。
独立分支补充 `container_name`、`lock_wait_ms` 和
`execute_elapsed_ms`，其和必须等于既有 `duration_ms`；待下轮
采集后分别报告内部锁排队和实际子进程执行，仍需调用级身份配对
才能训练从 TOOL_START 到完成的端到端时延头。

在高压批次尚未结束时，仅看已经自然完成的前 11 个 workflow：
37 次 child 的无工具调用非空模型完成通知，其下一个关键事件均为
RETURN；通知到 RETURN 的中位提前量约 237 ms，只有 2 次
达到 500 ms。此处排除了未结束 child，存在完成选择偏差，
不是在线误报率验证；可支持较小分块的晚期迁移候选，不能据此
证明大块 H2D 可完全隐藏，亦不能替代整组 JOIN 首次触发评价。

## 8. 同输入工具时延的因果历史头（进行中）

`scripts/audit_repeated_tool_timing.py` 按项目留出拟合命令类别先验，
并按 workflow 事件真实时间顺序回放；同 workflow、同 invocation、
同工具名、同输入 SHA-256，且前次 TOOL_END 先于本次 TOOL_START，
才允许使用前次耗时。正在运行的高压批次部分样本中，
6,054 次已完成 `execute` 有 953 次具备历史，约占 15.7%。
重复子集的前次耗时绝对误差 P50/P90/P95 为约 11/134/248 ms，
跨项目类别先验在同一子集的 P50 约 303 ms。实际不少于 2 秒的
重复调用有 85 次，前次耗时 P50/P90 误差约 108/338 ms，
类别先验 P50 约 2014 ms；但以前次时长超过 2 秒作判定时，
84 次预测中有 8 次实际短于 2 秒，不能作为无风险动作门禁。
该统计仅包含已完成调用，存在完成选择偏差；训练项目留一回放
不等于独立项目正式验证，也没有覆盖其余 5,101 次无历史调用。
JOIN 的长期时机仍未解决。
旧 66-root Astropy/Sphinx trace 的独立项目回放含 8,295 次
`execute`，其中 4,002 次有同输入历史，P50/P95 误差约
13/123 ms，58 个 workflow 有重复调用，但前 5 个 workflow
贡献约 45% 样本，长于 2 秒的重复调用仅 4 次。因此它支持
短调用的因果特征，却不能证明高压长调用及大块 H2D 时机已达标。
用进行中高压 train 项目的 1,057 次“前次成功”重复调用设残差
P90 约 111 ms，再对上述 Astropy/Sphinx 的 3,519 次前次成功
重复调用逐次评价，覆盖约 93.1%；但仅约 25% 的 52 个含重复
调用 workflow 全部落入此边界。逐调用覆盖不能代替 workflow 级
动作安全性，因此 v8 校准采用每 workflow 最大误差决定保守区间。
旧 66-root trace 已用于原模型校准及此轮分析，不能充当密封测试集。
旧 trace 没有显式 `is_child`，诊断脚本必须用
`--allow-legacy-evaluation-origin` 才按 invocation ID 命名约定推断；
该方式得到 2,143 次有历史的 child `execute`，P50/P95 约
12/116 ms，但实际长于 2 秒的 child 样本为零，不能外推长窗口。

另一项进行中高压样本的只读首次触发上界：对前次时长
不少于 2 秒的 154 次 child 重复调用，假设提前 1 秒发出请求、
且控制及 PCIe 延迟均为零，则 153 次实际还剩至少 500 ms；
其中有 17 次当次总时长实际短于 2 秒。该上界尚未按物理 KV
可用性、投递/排队和误报占用 HBM 计费，不据此选取线上 lead。
全量 child 工具调用的项目留出回放中，有历史时用历史、其余
回退粗类别：P50 误差约从 237 降至 125 ms；但实际长 child
调用中大多数仍无成功历史，长调用整体 P50 仍约 2 秒。
另测同 workflow 跨 child 复用相同输入摘要，阶段性重复调用
覆盖约 1,824→2,019，长 child 整体 P50 仍约 2 秒，
重复长 child P95 约 381→452 ms，误报增加；故只保留
离线消融，不扩大线上 invocation 身份作用域。
按真实完成时间在同一项目不同 workflow 中寻找相同命令的
`scripts/pilot_project_level_tool_history.py` 又找到约 245 次此前
无同 invocation 成功历史的 child 调用，但实际长调用仅 1 次，
该次误差约 573 ms；预测为长调用的 2 次中有 1 次误报。
跨任务的镜像版本和工作区状态也未被控制，不采纳项目级在线共享。
项目留出冷启动长调用分类试验（`scripts/pilot_cold_child_tool_long.py`）
仅用工具开始时的粗命令类别和输入长度，约 5,881 次无成功历史
child `execute` 中 480 次真实长于 2 秒；各项目得分最高的
10% 合并精确率约 10.5%，召回 12.9%，与整体长调用发生率
约 8.2% 接近，不适合驱动大块 KV 预取。长调用高度集中
在 pydata/xarray 项目，不应在项目留出评估中偷偷引入项目 ID。
`scripts/audit_tool_sandbox_wall_time.py` 在现有高压 trace
按单调时钟、调用包络及唯一匹配，仅用于诊断地对应了
8,567/8,567 次已完成 `execute` 和 sandbox audit；callback
结束差 P95 约 3.5 ms，工具总时长减 sandbox 时长 P50/P90
约 1.3/4.9 ms。旧 sandbox 时长**包含内部执行锁等待**，
并不等于纯命令执行时长；尚无调用 ID 独立身份交叉证明，
锁等待/实际命令耗时须等待新埋点批次检查。
`scripts/pilot_online_project_tool_prior.py` 按项目与命令粗类维护
过去完成的成功 child 调用，最少 16 次、最近最多 64 次；
除比较基线外不使用项目的未来标签。在当前高压训练批次部分
样本的 411 次有历史支持的冷启动长 child 调用上，项目内
滚动中位数的绝对误差 P50/P90 约 371/840 ms，
项目留出类别先验的同批 P50 约 2163 ms。
以历史长调用占比至少 80% 且中位时长不少于 2 秒为只读筛选，
31 次候选有 30 次真实长调用，但只覆盖所有冷启动长调用约
6.2%；不能把这个筛选视为普遍可用的 JOIN 时间预测。
旧低压四 workflow pilot 缺显式 child 身份，开启标注过的
legacy 推断后，仅有 36 次支持充分的短调用，P50 约
615→35 ms，但没有长调用样本。新高压/独立项目 trace、
真实控制链与 HBM 占用仍是部署前提。
训练采集完成并以新导出器重建原始 dataset 后，用
`scripts/compare_qwen35_tool_timing_models.py` 在相同首次
TOOL_START 快照逐调用对比 v7/v8；按 child/root、是否有成功
同输入历史、真实长于 2 秒的 child 等分组，报告 P50/P90/P95、
500 ms 命中率与 workflow 等权误差。普通首调用必须与
同批次 v7 对照；不得以 v8 重复子集的收益代替全量评估。
同时用既有 `diagnose_native_join_groups.py` 检查整组 JOIN 首次
WAIT_JOIN，而不将工具时长改善冒充 JOIN 改善。

独立 worktree 加入有界 `SameInputToolHistory`，在 TOOL_START 附带
前次已完成时长、年龄和状态；导出器可从旧原始事件按相同因果
规则重建，并核对新 trace 的在线字段。schema v8 的
`observed_command_child_repeat_v2` 必须显式选择，旧 v7 契约保持默认；
只有前次成功的 `execute` 才使用历史先验。训练项目至少 32 次、
8 个 workflow 才拟合残差边界；校准项目按 workflow 的最大绝对
误差给出保守边界，不足 8 个 workflow 则关闭此头回退旧工具模型。
该边界需在未用于训练和调参的项目上评价覆盖率、误报、首次触发、
P95 和 workflow 等权误差。主工作树仍在运行旧代码采集；
高压批次完成后须用新导出器重新导出、显式训练并独立校准，
不能把此 pilot 当作物理预测传输的上线证据。

## 9. 冷启动长工具调用的项目在线先验（待独立验证）

进行中的 128-root 批次只读因果回放补齐了**所有无同输入成功历史**
child 调用（有足够项目历史时使用项目/命令滚动中位数，否则使用项目隔离
的类别模型），而不只汇报被筛中的长调用。部分已完成数据中的
6,166 次 cold child 的逐调用 P50/P90 误差由约 213/1558 ms
降到约 99/575 ms；其中真实时长至少 2 秒的 488 次，
P50 从约 2192 ms 降到 431 ms，500 ms 命中率由 0%
升至 56.6%。**按 workflow 等权**的长调用 P50 却仅由约
2622 ms 降到 2323 ms；项目 mwaskom 的已支持子集退化。
以滚动中位数 ≥2 s 判为长调用，316 次阳性中 109 次是假阳性，
只能作为只读时间估计，不能用于无界 HBM 预留或开启 H2D。
报告由 `scripts/pilot_online_project_tool_prior.py` 生成，样本仅包含
已完成工具调用，训练批次尚在运行，结果有完成选择偏差，也未做
独立压力匹配项目校准。

独立分支新增显式契约 `observed_command_child_project_v3`、schema v9：
跨 workflow 的项目/命令历史只吸收**已经完成且成功**的 child
`execute`，同项目同命令至少 16 次后才输出滚动中位数；每个类别
最多保留 64 次、类别总量上限 256。同 invocation 成功同输入历史
优先，其他冷启动 child 才使用项目先验；两种时长都须有不少于
8 workflow 的校准残差，否则退回原工具时长分布。训练导出器
按全局事件时钟重建相同的因果特征，遇到在线字段与离线回放
不一致则拒绝；独立模型选择不改默认 v7 和正在运行的采集。
`scripts/compare_qwen35_tool_timing_models.py` 增加冷启动项目
先验有/无与长调用分层。待高压批次**完整结束**后以此分支重新
导出原始 trace，在同一触发时刻做 v7/v8/v9 对照，并用新的
项目隔离、高压校准组报告 P50/P90/P95、各项目退化、workflow
等权误差、首次触发时间、误报和真实物理动作转化。JOIN 长程预测
及物理动作门禁仍未达标，不因上述子集收益而解锁。
随着未结束批次增加，长调用筛选的只读训练内 sweep
（历史长调用占比 ≥0.5/0.6/0.7/0.8 且中位 ≥2 s）
分别给出约 65.5%/74.4%/82.4%/96.8% 的精度，
相应召回约 42.7%/24.3%/12.4%/6.1%。这些是同一批
项目内滚动回放，且都遗漏未完成调用，**不能从中选出部署阈值**；
它说明提高精度需牺牲覆盖，最终应在独立项目与真实
PCIe/HBM 代价下决定是否触发部分预取。
为了区分同 workflow 自适应和真正的同项目跨任务适应，
`pilot_online_project_tool_prior.py --exclude-same-workflow-history`
仅从其他已完成 workflow 借用项目先验。仍在进行的批次已有
493 次实际 ≥2 s 的 cold child 调用；对**全部**此类调用，
项目隔离类别先验 P50 约 2200 ms，普通滚动历史约 431 ms，
排除同 workflow 历史后约 480 ms；独立 workflow 等权
P50 分别约 2622/2323/2324 ms。收益不是仅靠同一任务重复，
但少数首次出现项目的 workflow 仍缺信息；也不能把同一
实验的在线回放当作未见项目的正式校准。
按命令类别进一步审计部分高压样本：长 child `python_inline`
404 次中 384 次已有足量项目历史，时长 P50 误差约
2163→374 ms；长 `git` 11 次跨 8 workflow，却无一具备
项目/命令支持，P50 误差约 47.6 秒；`other` 13 次也只有
2 次有支持，P50 误差约 15 秒。少数无支持的极慢命令解释了
长调用 workflow 等权误差为何仍然偏大。原始训练事件只保留
粗命令类别、脱敏哈希及输入长度，不能反向恢复 Git 子命令
或精确脚本形态；若要减少这种冷启动尾部误差，需要下轮
新增**可在线观察且不泄漏参数/路径**的子命令类别，再重新
采集并做项目隔离验证，不能把新特征伪造到旧 trace 中。
同输入/项目时长先验在调用尚未返回、运行时间已超过先验中位数时
不能继续给出零剩余时间：运行时已改为只对仍存活的先验分量
重归一化；超过最高先验时长则退回原有条件生存时长头（无支持
则不提供时机提示）。这修正了晚期虚假的即时返回预测，但不是
早期 JOIN 预测精度提升，仍需按真实决策快照验证准确率和误报。
另补齐了通用/旧版在线 shadow 的 TOOL_START 时长特征透传及
TOOL_END 失效；新版 0.5.20 原有 `_tool_metadata` 路径已透传这些
字段，该兼容修复不能算新版准确率收益。
首次 TOOL_START 模型对照增加同批样本的零剩余时长基线；
必须在长调用和 workflow 等权维度超过该基线，不能仅根据
大量短工具调用的整体中位数宣称准确。
当前 128-root 采集固定于旧 BeliefKV 提交，新契约的项目历史需在
采集结束后从**原始事件按因果时间重建**；新代码下采集的项目隔离
校准组会多出在线项目历史字段。训练/校准应同时保存各自
`beliefkv_source_sha256` 并核对在线与离线字段，不把 SGLang、
模型和硬件一致误称为采集代码完全一致。完成原型筛选后，
正式系统收益实验须在同一已冻结实现中重新采集对照与预测组。
只读 TOOL_START 首触发上界（假设**零控制/PCIe 开销**）中，历史
长调用占比 ≥0.8 的 31 个候选若按滚动中位数提前 1.5 秒触发，
27 个真实剩余 0.5--2 秒、2 个工具已先返回、2 个过早超过
2 秒；覆盖绝大多数长 child 调用的能力仍不足。此时机
**不能**被解释为真实 H2D 有用率，也不推断 JOIN 结束时机。

## 10. 更早的 child 完成提示（仅影子诊断）

旧流式单 workflow pilot 的首正文提示有 3 次误报；仅凭出现正文
不可判定 RETURN。复核原始 `llm_result` 还发现继续调用工具的
响应正文可达 723 字符，故简单的 64 字符门槛也不能当成
确定性完成协议。分支增加两个**只写 trace** 的累计正文门槛
（64、1024 字符）：每个 child 模型请求仅在首次跨过该门槛且
此前没有观察到工具调用片段时发出脱敏通知，并带 request、
context epoch、JOIN 身份；新增审计脚本按门槛分别统计
RETURN 精度、漏检和有效提前量。两种门槛目前均无足量、
压力匹配的独立样本，且模型可能先写很长正文随后才调用
工具；它们不能作为预测性 H2D 的放行依据。当前训练批次
没有启用流式采集，不中途修改其运行方式或将其混入校准。
进行中的高压**非流式** trace 又发现至少 35 次 child 工具调用
响应的最终正文长度达到 1024 字符（同时有 416 条非工具调用
`finish_reason=stop` 的 child 响应达到该长度）。这仅是完整响应
长度，不能推断正文阈值与工具调用片段在流内的先后，因此
1024 字符也不具确定性，必须在独立流式批次测真实首触发误报。
