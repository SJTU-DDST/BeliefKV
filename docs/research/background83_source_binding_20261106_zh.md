# 83 项目候选的来源绑定与实验准备

2026-11-06，America/Los_Angeles。本轮完成 CPU 来源注册、完整源码绑定和三臂配置
检查，没有启动或注册 GPU 实验、GPU 资格测试或自动恢复任务。用户的 GPU 暂停
仍有效；该候选尚无新的竞争、JCT 或吞吐结果。

## 当前完成项

83 个不同原项目的完整轨迹已注册为组合来源：

```text
/home/longhao/experiment/ops/
  workflow_original83_background_tail_source_20261106_r1.registration.json
```

注册类型为 `composed_complete_original_workflows`，不是一次新的 native capture。
七份原始采集的完成、真实退出、fresh drain、token 和源码绑定均已核对。
原 73 条完整任务与追加的 10 条背景任务继续沿用此前的选择；没有复制任务、
改写 token、内部依赖、项目源码、项目测试或工具时间。

非排序 Full、Oracle、ThunderAgent acting-decay 三份配置已接到该来源：

```text
background83_mean60/source_bound_configs_r1/
  full.json
  oracle.json
  thunderagent_acting_decay.json
  matrix_review_attempt_r1.json
  preparation_review_r2.json
```

它们仍明确声明 `GPU_experiment_registered=false`、`automatic_resume_allowed=false`。
来源注册完成不等于 GPU 实验注册或性能接受。

## 实验契约

| 项目 | 声明与检查 |
| --- | --- |
| 人口与到达 | 83 个不同项目，20 个初始 workflow，平均 60 秒泊松到达 |
| 测量人群 | 原 18 个按到达位置预先选定的成员，身份不交给调度器 |
| JCT | 保留完整正常尾部，全部测量成员自然完成后停止背景 |
| 资源 | 比较配置均声明 400 GB Host、running/decode 上限 24 |
| 模型与执行 | 同一模型、原生执行配置、采样设置、prefill 配置及传输修复 |
| NUMA | 同一放置策略，页面分布继续只作诊断 |
| Full / Oracle | 相同统一 2 秒控制和进度维护，仅改变剩余工作量排序 |
| Full KV 机制 | 保留 JOIN/EOS 预取、预测工具卸载、prepare_host、连续性与 sibling |
| 关闭项 | 工具预取与逐层 handoff 均关闭 |
| ThunderAgent | 只准备 acting-decay 版本，保留其 5 秒控制；不注入 Full 策略 |

三臂均通过当前冻结代码的实际 CPU runtime constructor 检查。
Full/Oracle 通过现有 `verify_ranking_only_pair`；到达、私有 cohort、工具 overlay、
模型和资源字段逐项核对。它们的模型/工具轨迹相同，排序对照的工作量 artifact
只在 `remaining_work_ranking_enabled` 上不同。

来源注册保留了原始采集时的资源元数据；未来比较实际采用上表的 400 GB 配置。
CPU 构造没有新分配 400 GB Host 或 GPU 池，不证明实际页面放置、原生物理准入、
迁移成本或稳态。原 73 项目、20+70 秒的已完成结果继续单独保留，不与本候选混算。

## 完整 CPU 绑定

最终冻结代码为 `631911fb`：

```text
/home/longhao/experiment/.worktrees/beliefkv-background83-operator-631911fb
```

`shared_bound_cpu_r3` 完整执行通过：

- 8188 个唯一测试身份，另有 23 个子测试，JUnit 合计 8211。
- 原 7132 个测试身份全部保留，无失败、错误或跳过。
- 2622 个源码输入、原生引擎和外部输入在执行前后保持一致。
- 所有此前绑定的源码路径保留。
- CUDA 显式隐藏，固定 CPU0、nice19，计算库线程数为 1。

测试包含 native CPU radix、页分配、Host 索引和锁的检查；传输和 device event
使用 CPU stand-in。它不构成新的 GPU 正确性、数值等价或性能证明。

前两轮尝试都保留：

1. `shared_bound_cpu_r1`：8181 项通过、7 项失败。五项 handoff 测试的旧 budget
   mock 不接受新增 `headroom` 关键字；两项缺少忽略目录中的历史报告 fixture。
2. `shared_bound_cpu_r2`：8188 项与 23 个子测试全部通过，但绑定器发现两个旧
   testcase ID 因新增 Host 容量参数而改名，拒绝发布完整绑定。
3. `shared_bound_cpu_r3`：修复 fixture 接口与稳定 ID 后，完整验证和绑定均通过。

`e827e702` 只更新我们自己的 handoff 测试 mock 签名，保留全部容量和锁断言。
`631911fb` 恢复清理测试原有的 `[False]`、`[True]` 标签，同时保留新增的
200/400 GB 场景及其断言。没有改动运行时算法或 LoCoBench 项目的测试。
历史报告 fixture 按原文件哈希绑定到验证快照，没有生成替代数据。

## background25 的当前绑定

第一轮来源检查中，其他六份原始采集通过；background25 的旧重资格证明要求
整套旧 CPU 源码与当前执行目录逐字节一致，因此在新的验证快照中拒绝。
这不是原始轨迹或实际工具执行失败。

三个因果分析源码仍与该证明完全一致。使用当前已通过的完整 CPU 证据，
通过已有 `requalify_causal_source.py` 生成新的
`background83_mean60/source_requalification_r1.json`：

- 同一原始自然完成执行：25 个 workflow、3249 个请求。
- 重新核对原 token、事件前缀、实际退出、fresh drain 和自然返回。
- 保留原先一个 callback 发布时钟与实际观察时钟的冲突及其正确分析。
- 原 `capture_failed` 状态、原失败审计和旧重资格证明全部保留。
- 原始工具时间、token 和任务执行均未改写，也没有重跑 native。

新组合来源显式绑定这份新证明，没有放宽旧证明的源码检查，也没有修改来源
验证器来绕过失败。

## 证据入口与后续边界

本轮产物位于此前候选目录，未增加新的主实验版本：

```text
experiments/reports/remaining_original_projects_20261030/useful_token_cost_r1/
  host400_registered_copy_pressure_r1/capacity_review_correction_r1/
    arrival_pressure_followup_r1/background83_mean60/
      shared_CPU_binding_plan_r1.json
      shared_CPU_binding_plan_r2.json
      shared_CPU_binding_plan_r3.json
      shared_bound_cpu_r1/
      shared_bound_cpu_r2/
      shared_bound_cpu_r3/
      retained_fixture_repair_cpu.xml
      retained_identity_repair_cpu.xml
      origin_completion_review_r1.json
      source_requalification_r1.json
      baseline_prepare_r1/
      source_bound_configs_r1/
```

矩阵第一次汇总误用了只接受全绿报告的 helper 去读取首轮失败报告的测试身份；
相应失败说明保留。最终 `preparation_review_r2.json` 从原 XML 枚举身份，
只对最终通过报告执行严格的通过检查，并核对所有其他首轮身份仍保留。
这不修改测试结果或资格条件。

当前已补齐“完整 CPU 证据、新来源注册和三臂配置”的准备缺口。接下来仍需
用户解除 GPU 暂停，再完成必要的来源绑定运行检查和实际配对。新负载的
ready 供给、可改变集合的竞争、Host 状态损失、完整 JCT 和共同窗口吞吐
尚未验证；保留 `formal_workload_accepted=false`、`performance_accepted=false`。
旧 73 项目约 1.90% 的单对收益不能当作新负载结果或重复性证明。
