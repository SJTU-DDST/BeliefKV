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

短窗头可以通过同时设置 `BELIEFKV_COMPLETION_LEAD_ARTIFACT`
和 `BELIEFKV_COMPLETION_LEAD_SHA256` 显式加载经 SHA-256
固定的诊断 JSON；缺任一项、哈希变化或非只读诊断状态均拒绝。
它仅提供只读剩余时间三分位，不改变物理动作资格或默认配置。
截至本次 pilot，尚无带该头的 GPU 控制链投递延迟分布，
更早且可靠的 JOIN 终态信号仍未找到。
