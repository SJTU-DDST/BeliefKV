# Qwen3.5 原生物理机会压力扫描（进行中）

日期：2026-09-28。此处仅使用训练项目，不选择密封测试集的策略参数。
扫描目的是找出适度压力下 HBM 有可迁移余量、Host 有有效副本及
传输余量、而有用 KV 驱逐后重算很少的主评估区间；优先检验闲置
HBM，冷页替换单列。root 数本身不是压力或收益标签。当前服务
的早期扫描只做 native reactive 和 admission 机会观察；
后续确认 JOIN canary 单独打开了预测式物理 H2D。

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

独立冷启动 v3 已自行完成：
`experiments/raw/qwen35_native_regime_selective_180g_35_65_6root_float64_v3/`。
6/6 workflow 自然完成且具备 native-agent JCT 测量资格；
机会 writer 的 1,733 条候选与 680 条 census 完整关闭，
原生遥测无丢失或写入错误。Host FULL 高水位
1,443,893/3,076,172 tokens（约 46.9%），Mamba 为
1,569/1,818 slots（约 86.3%），均无 Host 驱逐。
FULL 原生 D2H/H2D 为 1,443,942/0 tokens，Mamba 为
1,569/12 slots；这些都不是预测动作。

修复后 `fits_current_free_lists` 出现于 533 条 JOIN wait、
42 条 TOOL wait、2 条 admission 观察；另外 185 条 JOIN wait
因空槽不足拒绝。所有可行动观察的物理目标只有共享 Mamba
节点 28 和 49，FULL 所需恢复量均为 0；这不是 577 笔独立
传输。对 xarray-7393 parent，节点 28 可行动采样距实际
JOIN 满足约 76 至 260 秒；尚未证明如此长的提前驻留有净收益，
更没有真实 predictive H2D ACK 或首次服务复用证据。v2/v3
的 `successful_workflows=0` 都来自六条自然语言终态均不满足
现有自报告结构化协议；测量资格不等于官方任务正确性。
因此 6-root 可以作为无 Host 驱逐的**小规模共享 Mamba 机会**
诊断档，不能单独作为 FULL 预取和正确完成吞吐 A/B 的主场景。
下一步需对共享节点去重、验明 first-use 与真实节省的等待，并
在训练项目另找 FULL 有效 Host 副本而低重算的负载，同时接入
独立正确性评分；不可把不合格标签改称“成功”。

已对 v3 的 `official_eval_inputs/preds.json` 使用隔离环境的
SWE-bench 4.1.0 官方 Docker harness、冻结的 Verified 本地
数据（revision `91aa3ed51b709be6457e12d00300a6a596d4c6a3`）
及六个已有的实例镜像独立评分：6 条提交中 5 条有非空补丁并
实际评测，xarray-7233、xarray-7393 解决任务；pylint-4551、
pylint-4661、pylint-6386 未解决；xarray-7229 为空补丁，
不计作通过。评测错误为 0，未重建或删除镜像。完整报告保存在
`experiments/raw/qwen35_native_regime_selective_180g_35_65_6root_float64_v3/official_eval_inputs/`，
run ID `bkv_native6_float64_v3`；输入 SHA256 为
`0f0894b2c2fd9f5e8183bc4b186f09ec3274ffa06fc0291257c80cbe2401a504`，
报告 SHA256 为
`d5d1234b8ec3b8df685fb9c09114da60611940bdd12042f5dcbf644baec5cbfa`。
导出时的 manifest 仍是未评分输入的不可变记录，不回写其
`official_correctness_evaluated=false` 字段。此结果说明自报告
结构化门禁的 0/6 不等于真实任务正确数，但 2/6 样本过少，
也不能据此宣称政策吞吐收益。

## 200 GB / 25:75 / selective / 8-root：Host Mamba 容量边界

首次启动在 VLM 图像 warmup 的 CUDA 图像搬运处 OOM，client 未
启动，不能作为负载结果。随后在同一计划配置下独立启动
`experiments/raw/qwen35_native_regime_selective_200g_25_75_8root_text_v2/`，
服务端显式跳过图像 warmup，仅对真实文本请求进行 native reactive
和只读机会采样。6/8 workflow 有完整结果时 Mamba Host 达
2330/2330 slots，FULL 高水位 1,831,480/2,441,407 tokens。
此后出现 54 次 Mamba Host 驱逐和 8 次 FULL Host 驱逐，遂中断
剩余两个 client workflow，并由包装脚本关闭服务端。原生遥测与
机会 writer 均完整关闭，无记录丢失或写入错误；被中断的工作流
不能用于正确完成吞吐或完整 JCT。8 次 FULL 驱逐涉及 8,145
tokens，Mamba 驱逐 54 slots；停止时尚无已确认的驱逐后复访，
不能推断所有被驱逐内容此后是否会被重算。

2,324 条轮转机会观察中，有 995 条 `fits_current_free_lists`，
去重 `node_id` 仅 26、49、1932 三个共享 Mamba 节点；它们来自
32 个 session/generation 观察，不是 995 次独立可迁移机会。
可装入观察中 FULL 需求一律为零。原生 FULL D2H/H2D 为
1,840,441/0 tokens，Mamba 为 2,384/26 slots；没有主动预测式
H2D。该配置既没有 FULL H2D 目标，又已耗尽 Mamba Host，
不能作为低 Host churn 的主要 A/B 区间。

本轮旧采样只记录节点 ID，不记录创建时间；对共享节点的去重只是
同一次服务生命周期内的近似，不能替代物理 identity/epoch 证明。
后续机会日志补记 H2D/PREPARE 的 node 与叶节点创建时间，供
新运行的保守去重与 stale 判断使用，不追溯改写本轮原始数据。
如将该配置用于 P5/P6 配对实验，两臂必须采用相同 warmup 设置，
并验证实际 CUDA graph/运行契约一致。

## 200 GB / 25:75 / write-back / 16-root：有 FULL 机会，但 Host 容量越界

在同一训练清单的前 16 项上独立冷启动，running=48，
原生 `write_back`，原始数据位于
`experiments/raw/qwen35_native_regime_writeback_200g_25_75_16root_v1/`。
13/16 个 workflow 写出结果后，Host FULL 到达 100%，Mamba
高水位约 97.8%，出现持续 Host 驱逐；中断 client，包装脚本正常
关闭服务，遥测 writer 无写入失败且机会日志完整关闭。此轮没有
完整 workflow 结果与正式任务正确性评分，不得报告成功任务吞吐。

终态 Host 驱逐合计 FULL 234,277 tokens、Mamba 540 slots；
两池原生 D2H ACK 分别为 2,674,910 tokens、2,352 slots，
H2D ACK 分别为 83,718 tokens、1,110 slots。FULL 重算归因
暂为零，但仍有 881 个 FULL 被驱逐块没有后续请求探针结论；
Mamba 再访问位置不可区分，不能将零已确认重算解释为低重算。
上述 H2D 均是原生迁移，**不是预测性 H2D**。

6,113 条有界 H2D 机会抽样中，2,811 条瞬时 free-list 可容纳、
452 条 free-list 不足；前者去重 node ID/创建时间后仅 19 个
物理节点，其中 17 个节点存在 Host-backed、缺 Device 的 FULL
KV。抽样不是全量机会分母，也不证明迁移发生在有利的时间窗口。
这表明 `write_back` 能提供先前 `write_through_selective` 试采
缺少的 FULL 物理目标，但 16-root 全程不满足稳定 Host 余量门槛。
下一档应在相同训练任务集合中降低同时活跃的数量，并分别检查
阶段性可装入 FULL 目标、Host 水位及 Host eviction 到后续 miss；
不能把该轮的短暂机会直接当成完整 A/B 场景。

已在隔离 SGLang worktree 中使单节点预测 H2D 原语接受真实
`write_back` 模式，同时保留 session/epoch、祖先闭包、无 Device
回收和原生 ACK 校验；并未放行 write-back PREPARE。这项改动有
CPU 回归，但本轮服务启动早于改动，不是物理预测闭环证据。

## 200 GB / 25:75 / write-back / 12-root：Host 稳定但缺少 H2D 目标

同一训练清单前 12 项独立冷启动，running=48，
`experiments/raw/qwen35_native_regime_writeback_200g_25_75_12root_v1/`
留下 12/12 workflow 结果，其中 11 条符合 native-agent JCT 测量条件；
独立任务评分尚缺。Host FULL/Mamba 高水位分别约为
25.64/85.32 GB，两个池均未发生 Host 驱逐。3,018 条有界
H2D 机会观察中，2,387 条为 `already_device_resident`，
631 条没有有效 session/anchor，没有一条 Host-backed、
缺 Device 且可装入的目标。逐请求 Host 命中和原生 H2D ACK
均为零。这轮支持 Host 稳定一侧的边界，但不符合主场景的
物理机会门槛；`successful_workflows=0` 使用旧结构化自报门禁，
不能代替独立任务评分。

服务端和机会 writer 都已关闭且未报告写入错误；只清理了此轮
55 个可重建的 `workspace` 目录，保留轨迹、结果、日志、
原生迁移和 Host 遥测。下一档在同配置、同清单下测试
14-root，分别核对可装入的 FULL/Mamba 物理节点、Host
驱逐后重算及传输 ACK，不能仅凭采样次数判断收益。

14-root 首次启动在正式模型加载前被 staging 补丁指纹检查拒绝，
`...14root_v1/` 仅含失败日志，没有 workload 数据。为了保留
旧环境清单和旧补丁的可复现性，`...14root_v2/` 显式使用基于
同一上游 commit 的 `confirmed_join_canary` 完整补丁；此轮
**没有打开** canary 动作开关，仍是原生响应式加只读机会采样。
终态 14/14 workflow 具有完整测量轨迹，Host FULL/Mamba
高水位分别为 1,720,879/2,441,407 tokens 与 1,640/2,330 slots，
两池均无 Host 驱逐；原生 H2D ACK 计入 FULL 21,080 tokens
及 Mamba 346 slots，全部是 native reactive。5,020 条有界
机会采样中有 1,338 条瞬时 free-list 可容纳，按 node ID/创建
时间去重仅 14 个物理节点，其中 13 个存在缺 Device 的 FULL KV；
这些快照关联 13 个 workflow，以 JOIN 等待期间观察为主，
不能当作 1,338 次独立预取机会，也不证明确认 JOIN 之后目标
仍存在或首次服务会消费该节点。writer 无错误，但独立任务
正确性评分和物理预取消费链尚缺；因此它是可行动负载**候选**，
不是正式 A/B 已通过的证据。两轮 12/14-root 的补丁指纹不同，
正式 A/B 必须用同一指纹、任务与到达流重采两臂，
不能拿它们直接作吞吐对照。

## 200 GB / 25:75 / write-back / 14-root：确认 JOIN canary v1

原始数据位于
`experiments/raw/qwen35_native_regime_confirmed_join_200g_25_75_14root_v1/`。
与上一轮只读 14-root 扫描使用相同训练任务、到达方式、Host 容量和
running 上限，但显式打开确认 JOIN 后最多单节点的 H2D canary。
14/14 workflow 自然结束且具备测量资格；旧结构化自报规则仍给出
`successful_workflows=0`，独立任务正确性尚未评分。服务端和机会
writer 均正常关闭，无丢失或写入错误；Host FULL/Mamba 高水位为
1,995,026/2,441,407 tokens 和 1,676/2,330 slots，两池均无驱逐。

本轮 `physical_action_ack.jsonl` 为 0 行；原生 FULL/Mamba H2D ACK
分别为 36,243 tokens 和 407 slots，**都不能计作预测式 H2D**。
4,527 条有界机会观察中，JOIN wait 有 574 条瞬时可装入、72 条
容量不足、2,825 条目标已在 Device；这些是重复安全点快照，
不是独立动作数。已确认部分 JOIN 满足前仍有可装入目标，而 parent
在满足后数百毫秒即再次提交请求；仅凭 v1 的只读样本和零 ACK
不能区分 ticket 未建立、overlap 下迟迟未抵达动作安全点，或临界
时刻目标已变更。此轮代码没有持久化这些拒绝原因。

下一轮 `...14root_v2/` 在相同工作负载和物理配置上加载了
确认 ticket 与无 H2D step 原因审计，以及每个确认 ticket/节点预算
至多一次的 overlap safe-point drain。只有其独立物理动作 ACK
与后续首次服务 KV 消费闭合，才能认为预测性 H2D 物理链成立。
两轮已完成实验仅删除可重建的 workflow `workspace`，保留结果、
补丁、原始事件、传输及 Host 遥测供复核。

## 200 GB / 25:75 / write-back / 14-root：确认 JOIN canary v2

`...14root_v2/` 已自然结束：14/14 workflow 有完整测量轨迹，
机会 writer 完成且无错误，原生 writer 无丢失或错误；Host FULL/Mamba
均无驱逐。确认 JOIN ticket 14 个，overlap drain 请求与完成各 8 次，
`physical_action_ack.jsonl` 有 7 次经物理账本确认的
`PREFETCH_GPU` ACK，合计 539,996,160 字节（FULL 89,272,320，
Mamba 450,723,840）。这些动作在
`transfer_telemetry.jsonl` 均可按 child command ID 找到对应的
原生 H2D ACK。相比 v1 的零预测性 ACK，动作下发链已得到物理验证。

ACK 后各 context 首次 LLM 请求均进入下一 context epoch；
其中 3 次报告非零 Device 命中，4 次报告为 0。
这仅是 request 级别关联，**不能证明命中的是这 7 笔预取的节点**，
也不能把非零命中、提前驻留或已完成 H2D 直接计作节省的同步等待。
旧服务端未记录 `physical_action_use.jsonl`；当前新增埋点只在账本
确认 H2D 后追踪同 context 当前或下一 epoch 的首次 GPU launch，
核对原节点对象/创建时间、FULL value 未被替换及完整 Device 前缀
覆盖，并区分过期/无后续服务；Mamba 节点级消费仍不可验证。
新埋点的 CPU 测试通过，但 **v2 不能回填这项证据**。正式验收仍需
新进程采集节点级首次复用、正确性评分及同配置 reactive 配对 A/B；
部分 `PREPARE_HOST` 的后续卸载消费也尚未在此轮证明。
另外，v2 只读 PREPARE 有 4,122 条满足 Host 空槽条件的重复快照，
按 context/节点创建时间去重为 70 组，按物理 node ID 去重仅
3 个节点；其中 1 个 node ID 在首次成为候选后出现 native D2H。
这一关联没有备份动作及后续消费回执，不能当成 PREPARE 成功；
选择性策略必须以物理节点去重，避免按快照次数批量传输。
本轮结束后仅删除 14 个可重建的 `workspace`，其它结果保持原样。

## 200 GB / 25:75 / write-back / 14-root：首次复用核验 v3

`...14root_v3/` 保持 v2 的任务、到达方式和物理配置，新增
`physical_action_use.jsonl` 节点级首次 GPU launch 观察。
14/14 workflow 自然结束且具备测量资格，Host FULL/Mamba 均无
驱逐，机会与原生遥测 writer 正常关闭。确认 JOIN ticket 14 个，
drain 请求/完成各 7 次；物理账本记录 6 次 `PREFETCH_GPU` ACK，
首次 GPU launch 的 FULL 节点复用为 **0/6**。Mamba 节点级消费
仍不可验证。此轮没有证明预取缩短等待或改善 workflow JCT；
任务正确性也尚未独立评分。

问题不在于 H2D 没有实际发生。当前 `native_dynamic_1to4` 先用
单独的结构化 planner 请求选择初始 children，再在 JOIN 后把全部
child 报告追加到任务文本，才启动真正的 root agent 对话。
两段请求沿用 root context 身份，却不共享可延续的 prompt 前缀；
例如 flask-5014 的 planner 请求为 777 tokens，JOIN 后 root 的
首次请求为 10,236 tokens 且 Device 前缀命中为 0。六次预取
在 ACK 后均遇到下一 epoch 的首次请求，但六次都没有预取节点
进入该请求的 Device 前缀。这是目标前缀不连续，而非对 JOIN
返回时间的抽象预测误差。

修复分两层：外部 planner 的 bootstrap JOIN 在创建事件上显式标明
`parent_prefix_continuation=false`，调度器保留 JOIN 时间观察但
不为其预取旧 planner KV；新增 `native_in_graph_1to4` 实验配置，
由真实 root agent 在自身对话内选择一至四个 child，而非由外部
planner 预先启动 children。后者是否实际派发、多轮 JOIN 是否
形成有用 Host-backed KV，以及首次服务能否复用，仍需新 GPU
试采验证。两种配置不能互作同任务同配置的 reactive/P6 配对。

## 200 GB / 25:75 / write-back / 8-root：in-graph 试采 v1 启动失败

`...native_in_graph_join_200g_25_75_8root_v1/` 的服务端启动后
`/health` 返回 200，但客户端参数解析拒绝 `native_in_graph_1to4`：
runtime 已支持该配置，`run_deepagents_swebench.py` 的 argparse
仍重复枚举旧 profile。客户端在提交 workflow 前退出，包装脚本
随后终止服务端；本次没有 JOIN、物理动作或可评估 workflow。
已将 CLI 选项与 runtime 的 `SUBAGENT_FANOUT_PROFILES` 共用，
增加解析回归测试；同物理配置试采需用新目录重跑。v1 的空遥测
不能作为 JOIN/H2D 不可行动的证据。
