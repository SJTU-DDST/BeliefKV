# 56原项目的真实决策阶段与供给观察

## 目的与边界

固定共同吞吐窗口从首个测量成员到达到最后测量成员到达。18个测量成员的
完整JCT会包含此后的实际执行。两者不能混同，也不能因为晚期存在竞争，
就把晚期墙钟窗口替换成共同吞吐窗口。

新增离线审计保留每次真实周期决策的准入前候选、可装入成员数、可用预算、
跨workflow集合竞争和压力处理后的授予。通过可选`--cohort`按以下范围汇总：

- 原固定共同吞吐窗口。
- 首个测量成员到达到最后测量成员自然完成的执行观察范围。
- 共同吞吐窗口之后、测量成员仍在执行的部分。

后两个范围依赖实际完成时间，只是机制诊断，不是可跨方案替代的吞吐
窗口。范围存在重叠，不能把全部行相加。测量组未完整结束时终点为null，
不能把当前最后一个已完成成员当作测量期结束。早于范围开始的前缀，
ready比例为null，而非0或已完成观察。

这里仍只统计真实周期恢复入口；首次注册、免费续调、sibling和JOIN
专用入口没有被冒充为完整覆盖。逻辑可装入不证明物理页和DMA可行，
授予与后续服务仍使用原有同attempt及干预边界检查。
原25%供给、实际竞争、有用前缀、背景覆盖及完整测量组验收均未改变。

## 已观察结果

| 到达间隔 | 固定窗口实际控制/有ready-paused | 固定窗口实际竞争 | 测量执行范围实际竞争 | 固定窗口后实际竞争 |
| --- | ---: | ---: | ---: | ---: |
| 90秒，18项完整完成 | 909 / 55，6.05% | 0 | 5 | 5 |
| 60秒，失败校准，14/18项完成 | 602 / 155，25.75% | 2 | 118，未完整观察 | 116，未完整观察 |
| 70秒，约880.947秒运行前缀 | 231 / 20，8.66% | 0 | 0，未完整观察 | 尚未开始 |

90秒测量执行范围为530.928631--4504.394432秒，1,946次周期控制中303次
有ready-paused，约15.57%。固定窗口内完成1,938个测量模型请求，随后
完成881个，合计2,819个。这里只数完整请求，不将请求数当作输出token、
模型工作量或GPU执行时间。

60秒固定窗口后的968次控制中，949次有ready-paused。它的竞争确实在
后半程增加，不能再把较早约1,851秒前缀的15次竞争误写成全程结果。
但该轮已经出现有用KV超出HBM+Host容量、额外prefill及背景耗尽，仍不能
作为合格oracle控制。最终测量JCT保持null，原失效证书和部分结果保留。

70秒结果是早期前缀，不代表共同窗口完整供给、尾部覆盖或最终压力。
另一次GPU对齐观察覆盖到523.337830秒：共同窗口已观察110.393339秒，
时间加权GPU利用率99.704566%，无零值采样；截至538.791196秒的独立
供给前缀中，1,267次原生前缀探针的旧前缀损失代理为0。10个定期/末事件
有用状态观察点的最后可复用旧前缀并集为共同容量的41.36%。
这些不同前缀独立封存，不能宣称原子快照或整个实验已通过。

## 实现与验证

`scripts/audit_periodic_admission_decisions.py`新增只读`control_observations`
及可选`observation_phases`，原统计、授予到有效服务规则和在线代码不变。
新增回归检查固定半开边界、完整与未完整测量成员、尚未开始的范围、
篡改成员、时钟、重复或外来workflow，以及保留诊断与验收的区别。

nice19、CUDA隐藏、OMP/OpenBLAS各1线程的70项定向回归通过：
periodic、supply、workload review和measured-cohort stop四个测试模块。
新代码不是当前`f6f6b9d5`冻结GPU源码的一部分，运行中的观察器仍使用
原已资格版本；完成后应再用新读者补生成相同条件的阶段审查。

证据目录：

- `experiments/reports/remaining_original_projects_20261030/decision_phase_original56_r1/`：
  `mean90.json`、`mean60.json`、`mean70_live.json`。
- `idle_supply_original56_mean70_r1/report.json`及同目录图。
- `mean70_live_r1/supply.json`、`demand.json`、`periodic_decisions.json`。
- `decision_phase_cpu_r1.xml`。

阶段审查入口例如：

```bash
python scripts/audit_periodic_admission_decisions.py \
  --registration /home/longhao/experiment/ops/workflow_sustained_original56_mean70_full_20261030_r1.registration.json \
  --cohort experiments/reports/remaining_original_projects_20261030/sustained56_mean70_r1/analysis_cohort.json \
  --output experiments/reports/remaining_original_projects_20261030/decision_phase_original56_r2/mean70.json
```

该入口本身不证明最终退出或完整原生工作；后续oracle登记仍必须核对
固定测量完成证书、新鲜排空、实际owned退出和精确测量请求守恒。
