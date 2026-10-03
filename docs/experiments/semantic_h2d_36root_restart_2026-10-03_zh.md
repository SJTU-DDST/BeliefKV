# 36-root 修复后重启

本轮回到既定主场景，不延续重叠 64+64 的高压诊断。
只先完整运行 predictive，不自动启动无有效迁移机会的 baseline。
有效机会、首次消费和池压力明确后再决定同配置 reactive 对照。
不使用短 canary，也不人为逐出 KV 来制造传输。

## 固定配置

- Qwen3.5-35B-A3B，patched SGLang 0.5.20 staging，`beliefkv-next`。
- 原 36-root 对照的同一 manifest/选择方式，单波到达，
  `--max-workflows 36 --concurrency 36`；server running=48。
- FULL/Mamba BF16，静态 HBM memory fraction=0.94，
  Mamba/FULL 字节比例=0.9。
- Host 总预算 200 GB，NUMA node 1，比例匹配实际 Device 两池，
  原生 write-back；不改变 Host 原生驱逐规则。
- 14400 秒 workflow 墙钟预算，graph limit=2048、32-step
  FINALIZE reserve；单请求/工具 timeout 不因此扩大。
- `native_in_graph_1to4`，seed=21，completion=8192，
  context=131072，native-reactive guard profile，
  不要求结构化终态。
- 完成通知、有限收尾优先级开启，预测语义观察和 JOIN H2D 开启，
  PREPARE_HOST 关闭；不把 PREPARE 消费作为 H2D 必要条件。
- 阶段头/encoder/阈值冻结，工作头采用
  `child_semantic_work_frozen_phase_20261001_v1` 组合产物。
- 新安全输入 checkpoint、空 finished insertion 引用保留、
  原生压力 Host 保留、稳定 JOIN 预取预算和工具 ID 规范化。

## 观察项

检查真实安全 checkpoint、session/epoch 和 Host 可恢复副本，
将“状态仍在 GPU”与“Host 来源缺失”分开。分别统计模型时机、
容量拒绝、H2D issue/ACK、首次服务和 FULL/Mamba 实际复用。

观察 FULL/Mamba Host high-water、驱逐与可归因重访/重算，
不要把全部 uncached input 当作重算；检查是否依然频繁在首次
服务前重驱逐预取数据。模型评估使用 EOS 前因果快照，
RETURN 与 parent 首次服务单独计时，不以零剩余的 EOS 后
快照冒称准确预测。

有真实 worker、物理账本或遥测故障时停止调查。正常长任务
不因执行超过一小时停止。没有预取机会就报告来源/容量/时机，
不增加并发，不自动重复配置。终态后清理已归档完成 workspace，
保留全部失败现场。

## 产物

目录：
`experiments/raw/qwen35_semantic_h2d_36root_restart_20261003_v1/`。
启动前保存代码 commit、patch/model/manifest SHA256 和实际
运行配置至 `ab_plan.json`；单侧运行明确标为机制观察、非吞吐
对照，并保存按 manifest 原顺序选择的 task ID。此文记录预定
配置，不提前写入结果。

## 完成结果

本轮使用 `81c4be15506ac561d24172d4b964ca39f7b1e145`，单侧
predictive 正常退出，36/36 completed、0 error、0 incomplete。
总耗时 3567.93 秒（59 分 28 秒），completed workflow/hour
为 36.32，完成集合 JCT 中位数 709.18 秒、均值 795.16 秒。
5246 次客户端模型调用、5126 次工具调用，1522408 个 native
完成请求输出 token，426.69 token/s，GPU 采样均值 88.19%。
客户端模型调用与 native 请求观测边界不同，不将差值解释为丢请求。

控制投递、metrics monitor 和 native telemetry 错误均为零；
模型 worker 正常、物理账本未禁用。36 个初始 root 均有安全
输入 checkpoint。无 deadline 到期、无 child 取消、无重复工具
抑制，74 次自然语言终态被接受。

但 `pydata__xarray-6938` 的 general-purpose child 执行到 graph
step 2017，触发已允许的 2048-step/32-step FINALIZE reserve；
触发前约 3282.52 秒、403 次模型调用与 403 次工具调用，最终
workflow completed。这不是 error，也不是 3600 秒截止，
但属于 runtime 干预，不能作为完全自然的剩余工作/RETURN 标签。
聚合 `guard_intervened_completions=0` 没有反映这次提前切换，
不能只凭该计数断言完全没有 guard 干预。

`comparison.json` 的 `status=partial` 是因为没有运行 reactive，
不是 workflow trace 不完整。`successful_workflows=0` 的旧报告
字段源于 36 条 `missing_structured_completion`（另外两条还
标记没有 patch）；completion gate 实际关闭，未阻止自然回复。
未做官方 patch grading，不能把该字段当作 0% 任务正确率，
也不能把 completed throughput 当作正确任务 throughput。

## H2D 与机会

预测 H2D issue/ACK 为零，原生 H2D 也为零，Host FULL hit
和 Mamba Host hit 为零。9982 条已完成原生 D2H 共传输
208544849920 字节；Host 数据实际生成，但本轮没有恢复消费。
两者不能混同为“没有发生 KV 迁移”。

5715 条 WAIT_JOIN opportunity 中，5482 条为
`no_reusable_input_restore_step`，233 条为
`native_anchor_snapshot_rejected`。有效观察中的缺 Device、
有 Host 的 FULL token/Mamba node 数均为零；同时所有
2329 条已观测安全输入 Mamba state 记录都是 Device 存在、
Host 不存在。该计数是重复的 state 观测，不是独立 checkpoint。

当前拒绝详情合并了 checkpoint 不可用与已驻留，233 条 anchor
快照拒绝也不可观测，因此不能声称全量 JOIN 从未有机会。
可以确认的是：本批没有建立可发射的、可恢复 Host-only 输入
目标，不能将零动作归因于预测分数差或传输 worker 故障。

还发现独立的冷启动依赖：`_roll_final_stage` 在本轮 H2D
样本少于三条时直接返回，本批原生 H2D 为零，所以没有任何
latest-start 决策。该保护不能靠扩大 workload 获得“暖启动”；
应从已有实测传输证据初始化服务估计，再在真实目标下重检。
即使补齐初始化，没有 Host-only 目标时仍不应发射。
本次检查没有修改运行逻辑或启用新的 GPU 实验。

准备备份的容量观察中有 6609 条拟合当前 Host free-list，
其中 WAIT_JOIN 5482 条、WAIT_TOOL 1127 条。它们是重复采样的
可备份状态，不是卸载消费或 H2D 收益。PREPARE_HOST 本轮关闭，
不能评价它的收益；开启 PREPARE 本身也不会使仍在 GPU 的
目标变成需要 H2D 的状态。

## 容量与驱逐

| 项目 | 本轮 |
| --- | ---: |
| FULL Host 峰值 / 容量 | 101.30 / 105.36 GB，96.15% |
| Mamba Host 峰值 / 容量 | 94.65 / 94.65 GB，100% |
| FULL Host 驱逐 | 173 次，1.46 GB |
| Mamba Host 驱逐 | 173 次，11.14 GB |
| 输入 token Device hit | 94.69% |
| 输入 token Host hit | 0% |
| 未缓存输入 | 6172386 token，5.31% |

Host 首次驱逐约在 workload 开始后 2282.29 秒；38 次 JOIN
已有 37 次满足。173 个被驱逐 FULL/Mamba 前缀在随后 220 条
请求归因探测中没有观测到重访；probe 溢出为零，但尚未重访
不等于永久无用，未缓存输入包含新输入，不能全部计为重算。

metrics 的 FULL usage 峰值 33.34% 扣除了 evictable cache，
不是物理占用率。直接 free-list 采样的 FULL 最小只剩 1 token、
Mamba 最小为 0 slot，即两池物理占用曾接近/达到 100%。
这证明有缓存填充与回收活动，不证明热数据撑满或存在有益
H2D 目标。不能因为 33.34% 就认定 HBM 压力不足，也不能因
物理填满就认定严重有用 KV 丢弃或 GPU 计算不是瓶颈。

相较高压诊断 Host churn 显著减少，但批次大小、任务集合和
执行轨迹不同，不能作为受控性能收益。36-root 本批符合正常
完整执行，却未提供预期的 Host 恢复窗口；不增加并发来制造
事件，先处理来源可观测性、实际备份/驱逐对象和服务估计初始化。

## Child 与预测

38 个 child 全部正常返回、38 个 ALL JOIN 满足。34 个
workflow 一轮 spawn，`psf__requests-6028` 和
`pylint-dev__pylint-4551` 各两轮，每轮只有一个 child。
32/38 child 实际提交了阶段通知；允许通知不等于本批全覆盖。

2473 条预测被接收，涉及 361 个 child 请求，平均模型计算
14.11 ms、接收时 observation age 中位数 298.39 ms。
阈值跨越发生在 46 个请求：37 个最终 RETURN 请求，
另外八个是 completion-notice 工具轮、一个是 read_file 轮。
所以最终请求检测为 37/38，但不能将另外九个全部视为普通
调查工具的误报；completion-notice 仍不是最终无工具请求。

38 个终态请求最后 EOS 前快照的条件剩余 token 绝对误差
中位数为 47.00、有符号误差 +28.12，实际剩余中位数 43 token。
剔除 xarray-6938 的受干预 child 后，37 个请求分别为
46.00 / +22.06 token。首次阈值快照的绝对误差中位数为
116.45 token，不能把末段指标当作全程精度。
这批是已见项目开发负载，不是独立密封验证；token 误差也
不是毫秒 RETURN 精度，本轮没有 H2D 提前量或吞吐收益证据。

本轮模型审计保存为 `online_forecast_audit.json`，原始单侧统计
保存在 `comparison.json`。36 个完成且 patch 归档的 workspace
已清理；trace、patch、配置和模型保留，GPU 已释放。
