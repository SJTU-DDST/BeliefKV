# Qwen3.5 原生物理机会压力扫描（进行中）

日期：2026-09-28。此处仅使用训练项目，不选择密封测试集的策略参数。
扫描目的是找出 HBM 有空位或可安全腾挪冷 KV、Host 有有效副本及
传输余量、而有用 KV 驱逐后重算较少的主评估区间；root 数本身不是
压力或收益标签。当前服务只做 native reactive 和 admission 机会观察，
没有启用预测式物理动作。

## 180 GB / 70:30 / 12-root：超出候选区间

- Qwen3.5-35B-A3B BF16、SGLang v0.5.20、单卡、running=48；
  180 GB Host pool 绑定 NUMA 1，FULL/Mamba=70:30，原生
  `write_through`、session radix、control mirror 和 admission 观察开启。
  训练清单为 `configs/migration/qwen35_native_reactive_overlapped_128root_workload_2026-09-23.json`
  的前 12 项，12 个 root 同批提交。服务端、client 和机会日志分别位于
  `experiments/raw/qwen35_native_regime_180g_v1/`；数据保留。
- 中途观测到 Host FULL 高水位约 11.56%，Host Mamba 达 100%。
  服务端关闭后的归因状态记录 FULL Host 驱逐 12 次、900 tokens，
  Mamba Host 驱逐 561 次、561 slots；374 个 Mamba 被驱逐前缀
  在后续请求中再次出现。Mamba 记录的
  `outcome=mamba_prefix_revisited_hit_location_unknown` **不证明重算**，
  也不能证明重新从 Host 读取；该区间不满足低 Host churn 的候选条件。
- admission writer 完整关闭：1429 条候选观察，0 条溢出/失败；
  其中 1263 条 JOIN wait 的理由为 `no_host_backed_step`，
  40 条 TOOL wait 为 `no_host_backed_step` 且 PREPARE 判为
  `no_observable_shadow_closure`。观察是每秒轮转抽样，并非机会总分母。
  服务端原生传输计数为 FULL D2H 712157 tokens、FULL H2D 0，
  Mamba D2H 1400 slots、Mamba H2D 12 slots；这些计数含该服务
  生命周期中的全部请求，不能当作预测式 PREPARE/H2D 的收益。
- 发现 Mamba 满池后停止 client，并以主进程 SIGTERM 正常关闭服务，
  使两个 writer 写出完成状态。client 被中断，**不能**用此轮评价
  workflow 完成率、正确性、JCT 或训练标签。已有 FULL 重算归因
  为 0 不等于 Mamba 无重算。

## 180 GB / 35:65 / 6-root：同样不能作为主场景

保持上述模型、引擎、NUMA、running 与 write-through 配置，换用
Host FULL/Mamba=35:65 和同一训练清单的前 6 项。
物理容量实测 FULL 63.00 GB、Mamba 117.06 GB。几分钟后 Host
Mamba 达 100%，FULL 高水位仅约 34.7%；到正常关闭服务时记录
164 次 Mamba Host 驱逐和 2 次 Mamba 前缀再访问，仍无法从当前
遥测断言这两次是否重算。FULL Host 驱逐 1 次、61 tokens。
1484 条有界机会观察中，1086 条 JOIN wait 记录为
`no_host_backed_step`；writer 完整关闭，无队列错误。本档 client
也是主动中断，不能用于 JCT/正确性比较。原始数据在
`experiments/raw/qwen35_native_regime_180g_35_65_v1/`。

## 180 GB / 35:65 / selective / 6-root：Host 稳定，但机会尚未成立

同一训练项目的 6-root 独立冷启动试采使用
`write_through_selective`；原始数据在
`experiments/raw/qwen35_native_regime_selective_180g_35_65_v1/`。
6/6 workflow 有终态，6/6 具备 system-JCT 遥测资格，control delivery
未降级，原生遥测与机会 writer 均无丢失/错误。Host FULL/Mamba
高水位分别约 37.3%/70.6%，两池均无 Host eviction。native
FULL D2H/H2D 为 1,146,299/0 tokens，Mamba D2H/H2D 为
1,284/10 slots；**全部是原生行为，不是预测性传输**。
安全点机会观察以 JOIN `no_host_backed_step` 为主；存在 TOOL wait
的部分 PREPARE 候选，但尚未验证后续实际卸载消费，也未证明
可用的 Host-backed JOIN H2D 目标。只有闲置 HBM 且没有可恢复
Host KV，不满足主场景的动作机会门槛。

这一批 `native_agent_jct_eligible_workflows=0`、
`successful_workflows=0`：六条有非空自然语言终态的轨迹仍因
`missing_semantic_completion` 被现行资格判定排除；任务正确性
也没有通过官方检查证实。不能将它用于合格完成吞吐/JCT A/B，
更不能把非空文本直接当作任务正确。需分别核对自然终态的
测量资格和任务正确性契约，之后才选匹配 A/B 的主配置。
采集代码现已将通过终态检查的非空自然语言返回视作 native-agent
JCT 合格，但不因此标记任务正确；旧结果仍保留原始判定，
下轮试采验证新规则，不直接改写历史摘要。

下一步在训练项目上继续独立冷启动的有界压力/到达扫描，
同时审计 JOIN parent 是否已有 Device KV、Host 副本是否有效、
是否确有后续消费，以及 PREPARE 的 shadow 是否在后续卸载中复用。
仅在 Host 不抖动、有可迁移 HBM 余量、有真实动作目标、
且有用 KV 丢失后重算较少时冻结主配置；否则报告机会不足，
不要单纯增加并发去制造高压。

## 180 GB / 35:65 / selective / 8-root：Mamba 先于 HBM 满载

沿用同一训练清单的前 8 项，独立冷启动并自然结束；原始数据在
`experiments/raw/qwen35_native_regime_selective_180g_35_65_8root_v1/`。
8/8 workflow 完成并具备 native-agent JCT 资格，writer 完整关闭，
没有服务端监控错误。任务正确性尚无官方判定，不能报告成功任务吞吐。
FULL Host 高水位 1,535,599/3,076,172 tokens（约 49.9%），
Mamba Host 1,818/1,818 slots（100%），发生 24 次 Mamba Host 驱逐。
这些被驱逐前缀没有已观测的后续再访问；不能推断发生或未发生
Mamba 重算。FULL 没有 Host 驱逐，观测到的有效 HBM resident
峰值约 26.0%，不能等同于全部物理 HBM 占用。

2,225 条轮转抽样的 H2D 观察中，JOIN wait 有 1,304 条
`no_host_backed_step` 和 256 条 `already_device_resident`；此轮
仍使用旧版只读字段，`no_host_backed_step` **不能**精确区分
完全没有 Host 副本与已有副本但被祖先闭包挡住。已有 1,560
条 JOIN wait 和 40 条 TOOL wait 的 Host PREPARE 容量可容纳观察，
它们不是独立请求数，也没有真正触发 PREPARE。FULL native D2H/H2D
为 1,537,858/0 tokens，Mamba 为 1,842/22 slots，不是预测动作。
下一轮使用新增的 Host-backed 缺 Device、祖先阻塞及 unbacked
节点字段区分物理机会。当前 35:65/8-root 的 Mamba 已无余量，
不可选为主场景；用同一 8-root 到达流试 25:75 分配，独立冷启动，
观察是否同时保住 FULL 和 Mamba 余量。

## 180 GB / 25:75 / selective / 8-root：同样超出 Host 容量边界

同一 8 个训练任务，独立冷启动；
`experiments/raw/qwen35_native_regime_selective_180g_25_75_8root_v1/`。
试采过程中 Mamba Host 仍达 2,097/2,097 slots（100%），
FULL 达到 1,870,078/2,197,266 tokens（约 85.1%）。
原生归因记录 Mamba 驱逐 210 slots、FULL 驱逐 448 tokens；
一个被驱逐的 Mamba 前缀后来再次出现，命中位置不可观测，
不能断言发生重算。因容量不再满足主场景门槛，
主动中断余下 workflow，并正常关闭服务端和两个 writer；
此轮不能用于完整 workflow JCT/正确性或配对吞吐比较。
原生 FULL D2H/H2D 为 1,888,475/0 tokens，Mamba 为
2,307/30 slots，不是预测动作。

这轮的新版机会计数将许多 JOIN wait 标为
`host_backed_step_blocked`：存在 Host-backed、Device 缺失的
Mamba 状态，但无法形成符合 FULL leaf 祖先和父 FULL Device
条件的单节点 H2D。仅凭缺失数量尚不能判明具体约束，
下一轮才会记录 `blocked_detail`。固定 180 GB 下单改
FULL/Mamba 分配没有让这组 8-root 工作流同时保住两池余量；
不能据此推断所有到达模式或选择性 Host 备份策略都不可行。

`write_through` 会自动备份部分 KV；其 native D2H 不能作为选择性
`PREPARE_HOST` 的收益。单 node PREPARE/H2D 原语和只读机会观察
已在源代码中放行 `write_through_selective`，保留原有身份、
容量与 ACK 门禁；上面的 GPU 试采仅验证 native 行为及只读观察，
**没有 GPU 验证主动 predictive 原语**，scheduler 仍未签发真实预测动作。
自动备份与不可观测闭包在观察结果中尚未充分区分。后续仍需验证 FULL/
Mamba 身份、Host 预留、ACK/消费闭环，不能仅更改启动参数或将
已有原生传输重新命名为预测式动作。

## 180 GB / 35:65 / selective / 6-root：准入状态与时间戳类型复查

同一训练清单前 6 项，独立冷启动、自行完成；
`experiments/raw/qwen35_native_regime_selective_180g_35_65_6root_admission_v2/`。
此轮已包含 native waiting 状态修复和 `blocked_detail` 观察，
**不包含**下述创建时间规范化修复。6/6 workflow 有测量资格，
`successful_workflows=0`，不能计算正确完成吞吐；机会 writer 完整
（1,701 条样本、无错误），原生遥测无丢失。Host FULL 高水位
1,429,604/3,076,172 tokens，Mamba 为 1,715/1,818 slots（约 94.3%），
均无 Host 驱逐；Mamba 余量较窄，不能仅据此认定更高并发也适用。
FULL/Mamba 的原生 D2H 为 1,429,924 tokens/1,716 slots，
原生 H2D 为 0 tokens/10 slots；不代表预测动作。

轮转观察中，845 条 `host_backed_step_blocked` 均给出
`other_selector_invariant`，其中 773 条属于 JOIN wait、71 条属于
TOOL wait、1 条属于 admission；另有 261 条 `already_device_resident`
和 595 条 `no_live_session_or_anchors`。这些是重复安全点采样，
不是 845 个独立请求。排查发现 SGLang 节点创建时间使用
`numpy.float64`，BeliefKV 选择器与原生单节点 H2D 命令却只接收
内建 `int/float`；旧只读快照传递原始值，使符合其他门禁的目标
在选择器前置校验处被拒。现仅在只读闭包和 session anchor 边界将
有限、非负的 native `float64` 转为等值的内建 `float`，无效值
继续拒绝；修复有 CPU 回归，但 v2 服务端不含此修改。
下轮独立冷启动需确认是否出现 `fits_current_free_lists`，
随后才可做身份/容量安全的真实 H2D canary 和首次服务归因。

独立冷启动 v3 已在 `bkv-regime-float64-6` 中开始：
`experiments/raw/qwen35_native_regime_selective_180g_35_65_6root_float64_v3/`。
**截至本次运行中观察，尚无终态 summary/writer 状态**；修复后
JOIN wait 已出现 `fits_current_free_lists`，也有 Mamba Device
空槽为零而拒绝的观察。六条 JOIN parent 的候选大多指向同一
共享物理节点 28，部分 tool wait 指向节点 49；不能把重复的
安全点观察视作独立的 H2D 动作。在一条 JOIN 上，可行动采样
距实际 JOIN 满足约 76 至 260 秒，但未签发预测动作，不能
据此断言有收益。v2 的 `successful_workflows=0` 来自六条
自然语言终态均不满足现有自报告结构化协议；6/6 JCT
资格不代表任务已通过官方正确性评估。v3 应自行结束并
复核 writer、Host FULL/Mamba 水位、驱逐及请求归因后再定主场景。
