# 工具替换的前置容量检查

2026-11-03，Asia/Shanghai。在同步控制审计之后，实现两项控制路径优化，
并完成 CPU 回归与局部成本比较。当前还没有该改动的 GPU/JCT 结果。
已经排队的无成本分母实验继续使用原冻结版本，没有混入本次修改。

## 改动与安全边界

第一，在为 consumer 读取完整原生前缀之前，检查原有 donor 窗口内是否
存在可能满足其逻辑额度的单个 donor。设：

```text
D = consumer.tokens + buffer - policy.remaining_capacity()
C_i = donor.tokens + buffer，限当前 active donor；其他 donor 的额度为 0
```

只有 `D > max(C_i)` 才提前跳过。仍按原 cursor 检查至多八个 donor，
不扩大窗口或修改顺序。这是原来 `permits` 条件的必要条件，不是新的
准入评分或物理容量授权。`D <= 0` 时保留原路径，因为可能仅缺物理页。

检查在每个 consumer 前读取当前额度，没有跨事件缓存失败结论。
容量释放后，下次事件或原有检查可以立即重新考虑该请求；没有新增
等待窗口。通过前置检查的候选继续经过原有预测、身份、请求状态、
锁、Host 副本、ACK、实际释放和最终准入检查。

第二，`ReferenceToolReplacement.demand` 和普通
`DemandAwareToolOffload._demand` 将刚读取的 headroom 直接传给同一次
`_current_residency_budget`，避免重复读取 free lists。
复用只发生在同一次同步需求检查内部，不跨释放、传输或事件处理。

新增计数为 `tool_event_replacement_logical_window_skips`。
候选排序、JOIN/EOS、工具预测卸载、prepare_host、acting decay、
agent 连续性和 sibling 规则没有调整；工具预取与逐层 handoff 保持关闭。
hint rearm、shadow 构造顺序和跨调用缓存仍是后续分析项。

## CPU 验证

主工作区的最终相关回归为 411 项通过，无失败或跳过。包括原有真实
native CPU radix/allocator 路径，传输队列、事件和 GPU 操作仍使用
CPU stand-in，不能写成新的 GPU 资格或数值等价证据。

五项新增检查覆盖：

- 必然无法由单个 donor 满足的消费者不再读取原生前缀，现有 agent、
  Device KV 和传输状态保持不变。
- 每次重新读取逻辑容量，容量释放后可以通过；纯物理缺口不会误过滤。
- 保留原来的八个 donor 窗口、cursor 轮转和 active 额度口径。
- 替换和普通卸载两条路径都复用同一次 headroom；重复观察会被测试拒绝。

原有回归继续覆盖成功替换、竞争 consumer 的顺序、首次 prefill、
备份 ACK 后即时重试、工具提前返回、共享或锁定副本、原生容量检查、
新恢复对象的连续性和两秒普通准入窗口绕过。

首次新增测试的三项 fixture 失败保存在 r1 报告：一个缺少 acting 字段，
两个没有先设置原生预算，因而没有真正进入待验证分支。修复 fixture 后，
104 项通过。随后扩展到 411 项时发现两个已有遍历顺序断言失败；
相同两项在干净修改前版本 `7d49ac62` 也失败，证据单独保留。

这两个旧断言现改为核对正确候选、被排除目标、每个选中对象确实经过
原生身份查找、无重复查找以及原工作量上界；不要求已经使用索引的
实现仍遍历全部无关节点。测试名称与参数身份保留，未删除失败用例。

### 独立冻结验证

主工作区实现提交为 `0f457f8e`。从原无分母服务版本 `225db65e` 创建
独立目录，仅 cherry-pick 本次提交，冻结版本为
`6f1c648b88b12402208b6bf59c881da54876b0c4`：

```text
/home/longhao/experiment/.worktrees/beliefkv-tool-checks-host400-20261103
```

该干净目录实际通过 1346 项 CPU 检查，零失败、错误或跳过，来源绑定
工具验证完整保留原来的 1179 项身份，增加五项新检查及原生传输策略
相关检查。相比原服务版本，`beliefkv` 中只有
`runtime/reference_tool_replacement.py`、
`runtime/predicted_tool_offload.py` 两个模块改变。

原排队版本的全部资格输入 SHA256 仍匹配。这里没有重新分配 Host 池、
运行 GPU 模型或增加 GPU 后继，也没有把新的 CPU 资格覆盖到正在等待
的原服务目录。

## 局部成本比较

基线类直接读取已冻结服务目录中的 `ReferenceToolReplacement` 原代码。
两侧使用同一 native CPU tree 和当前共享 runtime/安全路径。
设置、状态断言和调用计数均在计时之外；九个 block 交替顺序，
每个 block 调用 100 次。

| CPU fixture | 原类中位 CPU / us | 新类中位 CPU / us | 降幅 |
| --- | ---: | ---: | ---: |
| 单个 donor 必然无法补足逻辑额度 | 137.014 | 15.713 | 88.53% |
| 逻辑可以放下，但工具预测不支持卸载 | 86.820 | 82.733 | 4.71% |

第一类每十次检查中，`_read_request_reentry` helper 调用由 20 次降为 0，
实际 native `inspect_beliefkv_reentry` 由 10 次降为 0。
helper 的旧 20 次中包含缓存读取，不能全部称为原生完整 token 扫描。
第二类两侧均保留十次实际 native 前缀观察，没有靠跳过必要检查省时。
两类均未授予、未传输或驱逐，也没有抢占现有 hot agent。

初轮报告只记录 helper 调用，旧字段 `prefix_inspections_in_10_calls`
指的是 helper，不是完整原生扫描。保留初轮，并以 r2 的两个明确计数
字段为准；两轮成本方向一致。

这是两个有界失败检查 fixture 的结果，不是完整控制器或真实负载收益。
尤其不能将 88.53% 乘以前轮 118–132 秒：其中有多少轮符合前置过滤
条件、其他候选的成本以及实际 GPU 暴露时间，仍需真实运行计数。
共享安全路径并未完整切换成旧版本，所以这也不是两个完整系统的对比。

## 实验边界

原无分母候选保持 `225db65e`，GPU 资源等待器和结果观察器继续运行。
当前先取得该候选的完整固定 18 成员结果，并与已有完整对照比较。
本次 CPU 优化作为单独准备的后续改动，不注册额外 GPU 实验，也不修改
任务、token、依赖、工具时长、400GB Host、到达表或测量成员。

若后续要隔离短 workflow 排序收益，排序与非排序 Full 应采用同一版
控制优化；否则必须声明为联合改变，不能把控制成本下降归因于排序。

完整平均 JCT 可重复改善约 1% 的目标仍未达到；CPU 微基准不是该目标
的替代验收。后续服务候选只为实际改变的控制代码补必要资格，原模型、
Host 组件和迁移路径的已有证据继续复用。

## 证据

```text
experiments/reports/remaining_original_projects_20261030/useful_token_cost_r1/
  synchronous_control_audit_20261103_r1/
    replacement_prefilter_cpu_r1.xml
    replacement_prefilter_cpu_r2.xml
    replacement_prefilter_cpu_r3.xml
    replacement_unchanged_baseline_r1.xml
    replacement_prefilter_cpu_r4.xml
    replacement_control_benchmark_r1.json
    replacement_control_benchmark_r2.json
    replacement_prefilter_frozen_r1/
      cpu.xml
      sources.json
      binding_result.json
      qualification.json
```

实现为 `beliefkv/runtime/reference_tool_replacement.py`、
`beliefkv/runtime/predicted_tool_offload.py`；新增 CPU 检查为
`tests/test_tool_replacement_control_cost.py`，比较入口为
`scripts/benchmark_tool_replacement_control_cpu.py`。
全部使用 nice19、CUDA hidden 和 OMP/OpenBLAS/MKL 单线程。
