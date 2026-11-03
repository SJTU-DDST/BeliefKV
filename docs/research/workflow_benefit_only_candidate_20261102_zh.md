# 无成本分母的 workflow 组合准入候选

2026-11-02，America/Los_Angeles。继续当前完整 JCT 目标，将此前只读
审计中的无分母目标接入真实选择器，并完成冻结 CPU 资格验证。
GPU 对比已由实际资源等待进程排队，尚未开始模型运行，没有新 JCT 结果。

## 本次改变

原选择器最大化：

```text
sum(workflow_weight * JOIN_coverage_gain) / (epsilon + request_batch_cost)
```

新候选最大化相同的分子：

```text
sum(workflow_weight * JOIN_coverage_gain)
```

`CombinationCosts.selection_objective` 的默认值仍为 `benefit_per_cost`。
只有显式 v7 profile 能启用 `benefit`；旧 v6 携带新目标、或者 v7 携带旧
目标都被实际 runtime 构造器拒绝。新日志的 `score` 是实际使用的收益，
原比值保留为 `cost_normalized_score`，不再参与选择。

HBM/逻辑额度、Host 与原生分配权限、完整池规划、输入和增长预留、
槽位、共享 extent 去重、required JOIN、active 连续性、acting decay、
六/八候选、0.5ms 搜索预算和原有相对同分门槛均保留。
没有附加成本同分规则、强制最大 agent 数或新的抢占。

本次使用此前已跑过两轮的 horizon64 预测器，模型参数、分档权重、
初始分支、输入合同、支持范围及 worker 全部相同。连续权重和 Qwen
语义候选没有混入这次 GPU 配置。旧成本数值和 64 步视界也保留，
仅用于原有诊断与资源支持检查。

## 已完成的验证

- 主分支实现提交：`05958618`，`perf: prioritize workflow admission benefit without request cost density`。
- 从原 GPU 服务版本 `d94fe0de` 单独 cherry-pick，冻结提交为
  `225db65e25c69cfc24ebe6967194dc2a24574691`。
- 冻结目录为
  `/home/longhao/experiment/.worktrees/beliefkv-benefit-only-host400-20261102`。
- 相比原服务版本，`beliefkv` 中恰好只改变
  `policy/workflow_combination.py` 和 `runtime/workflow_combination.py`。
- 冻结 CPU 报告 1179 项全部通过，无失败/跳过；完整保留原来的
  1051 项检查，来源文件通过现有资格工具绑定。
- 实际构造入口通过，完整比较检查器识别显式 v7 收益目标。
- 新旧模型去掉 `admission_cost_profile` 后逐项完全相同；
  成本字段去掉 `selection_objective` 后完全相同。

部署选择器还与两份既有独立离线审计做了真实记录核对：

| 已完成的原实验 | 完整可比搜索 | 旧模式精确复现 | 新模式匹配无分母审计 | 改变集合 |
| --- | ---: | --- | --- | ---: |
| horizon64 首轮 | 530 | 是 | 是 | 38 |
| 同配置确认轮 | 508 | 是 | 是 | 37 |

核对实际 JSONL 封闭前缀、时间、入口、成本身份、候选和 required JOIN，
未完成搜索不进入无界目标替换。这里不是新策略的服务或 JCT 反事实；
在线 cutoff 的安全行为另由 CPU 测试覆盖。

保留了最初两项测试 fixture 失败、未声明 native 路径导致的一项 skip，
以及资源等待器错误使用只创建文件 API 的失败。前两项修复测试设置；
等待器改用既有原子状态更新接口，初始配置与资格后配置分别保存，
没有覆盖不可变证据。上述早期记录不计入最终 1179 项成功报告。

## GPU 比较与当前状态

资源等待器已经实际启动并核对 PID/start ticks：

```text
PID: 780699
start_ticks: 786153447
nice: 0
status: waiting_idle_GPU
```

2026-11-03 07:34:57 UTC 的资源检查中，GPU 有三个其他进程，
占用者 PID 为 714844、716522、716597。没有启动我方 GPU server。
等待器在两次连续空闲检查后重新校验来源和全部资格输入，再注册一个
新候选；新预约或显式 STOP 会使其停止，不会结束外部进程。

预定注册文件：

```text
/home/longhao/experiment/ops/
  workflow_original73_host400_benefit_only_20261102_r1.registration.json
  workflow_original73_host400_benefit_only_cohort_20261102_r1.registration.json
```

固定相同 73 原项目、400GB Host、805198 HBM token、20 初始＋mean70
到达、running/decode24、prefill2048、mixed off 和原工具时长。
保留 JOIN/EOS、预测工具卸载、prepare_host、agent 连续性和 sibling，
工具预取与逐层 handoff 关闭。NUMA 只作诊断，不要求均分。

复用已有位置回放、采样和 400GB 注册传输证据，不重跑未改变的
Full、oracle、Thunder 或旧比值对照。完成模型启动后，CPU bootstrap
才会为真实运行登记固定 18 测量成员的结束观察器；现在不能宣称该
观察器已运行。测量成员自然完成后保留完整 JCT，再停止背景并完成
新鲜 idle drain、请求回执和实际进程退出检查。

完整结果仍需比较非排序 Full、旧比值及 Thunder，并核对实际新准入和
后续服务。首轮若获益，还需要确认可重复性。当前目标继续 active。

## 完整结果的 CPU 自动分析

2026-11-03 08:04:35 UTC，独立干净分析目录
`/home/longhao/experiment/.worktrees/beliefkv-benefit-results-7d49ac62`
已启动结果观察器。提交为 `7d49ac62`，70 项相关 CPU 检查全部通过。
实际 PID 为 793352，start ticks 为 `786333804`，nice19、CPU0、
CUDA hidden。其当前状态为 `waiting_resource_waiter`；GPU 等待器
780699 仍在运行，外部三个 GPU 占用者未结束，没有新模型结果。

观察器已绑定完成对照和候选资格输入，待固定 18 成员正常完成、取得
完整复核文件且 GPU 运行进程实际退出后，顺序执行五项 CPU 分析：

- 与非排序 Full 比较完整测量 JCT 和共同窗口吞吐。
- 与旧比值首轮比较，验证除组合目标外的预测器和成本参数完全相同。
- 与旧比值确认轮独立比较，保留两轮，不挑选更有利的参考结果。
- 与相同 workload、资源和 acting-decay 的 ThunderAgent 比较。
- 使用候选自身冻结服务目录的审计器，核对选择、实际准入和后续服务。

结果目录为本实验内的 `final_results_r1/`，包含冻结 `plan.json` 和
`watcher.state.json`。观察器只分析结果，不注册 GPU 后继；原有 workload
失败、完整尾部和实际停止证据全部保留。其启动不代表候选已经获得 JCT
收益，也不修改仍在排队的 `225db65e` 服务版本或 1179 项资格绑定。

## 证据

父目录：

```text
experiments/reports/remaining_original_projects_20261030/useful_token_cost_r1/
  r5_benefit_only_cpu_r1/
    targeted_before_r1.xml
    targeted_after_r1.xml
    targeted_after_r2.xml
    operator_r1.xml
    operator_r2.xml
    deployed_choice_parity_r1.json
  r5_host400_benefit_only_r1/
    frozen_cpu.xml
    frozen_sources.json
    historical_choice_parity.json
    source_compatibility.json
    qualification.json
    resource_waiter_process.json
    resource_waiter.state.json
    serving_ready/qualified_full.json
```

控制入口为 `scripts/start_workflow_benefit_only_candidate.py`，
模型准备入口为 `scripts/prepare_workflow_benefit_only.py`，
历史选择核对为 `scripts/verify_workflow_benefit_replay.py`。
