# 控制开销优化后的 Host400 实验

2026-11-03，Asia/Shanghai。用户要求控制开销优化完成后使用新代码实验。
原无分母候选尚未注册 GPU，现已明确撤下，改为运行已经独立冻结并通过
CPU 验证的控制优化版本。旧资格、STOP、等待器及观察器的终态全部保留。

## 本轮改动

服务版本为 `6f1c648b88b12402208b6bf59c881da54876b0c4`，位于：

```text
/home/longhao/experiment/.worktrees/beliefkv-tool-checks-host400-20261103
```

相对旧候选 `225db65e`，仅两个 runtime 模块改变：

- `reference_tool_replacement.py`：在读取完整原生前缀前，检查原八个 donor
  窗口是否可能补足 consumer 的逻辑额度。缺口非正时保留物理需求路径。
- `predicted_tool_offload.py`：同一次同步需求检查复用刚观察的 headroom；
  replacement 的 demand 路径也使用该复用方式。

保留原候选的 benefit-only 组合评分和相同预测器 artifact，不将主工作区
中的其他预测器训练改动混入已资格验证的服务版本。模型、native 引擎、
Host 分配及传输组件、position replay 和采样沿用原有匹配证据。

## 保持的实验条件

73 个不同原项目，20 个初始 workflow，随后使用同一份平均 70 秒的泊松
到达表。Host 400GB，Device FULL pool 805198 tokens，running/decode24，
prefill2048，mixed off。固定 18 个测量成员保留自然完整 JCT；
测量组共 2991 请求、362157 输出 token，完成后停止非测量背景。

任务、token、依赖、工具时长、预测 artifact、到达槽位和测量成员不变。
保留 JOIN/EOS 预取、工具预测卸载、prepare_host、agent 连续性和 sibling
支持；工具预取、逐层 handoff 关闭。NUMA 只诊断，不设均衡启动门槛。

## 验证与归因

服务版本已通过独立来源绑定的 1346 项 CPU 检查，保留旧候选的全部
1179 项身份。启动器、取消规则、连续两个新鲜空闲观察、真实旧进程退出、
固定服务来源及联合改变归因的开发回归另有 71 项通过；在干净 operator
上再次执行这组必要检查，作为实际启动输入。

新实验相对旧 ratio 结果同时改变组合评分和同步控制实现，相对非排序 Full
还包含预测及组合准入。所有结果均明确记录联合改变，不能把总体收益当作
短 workflow 排序的独立收益。旧的两次 ratio、Full 和 Thunder acting-decay
完整结果同时保留，不择优选择参考轮次，也不重跑未改动的对照。

验收使用固定成员完整平均 JCT、同一墙钟窗口吞吐和实际准入到服务证据。
CPU 审计同时记录 replacement 耗时及
`tool_event_replacement_logical_window_skips`，区分共同窗口与全程累计值。
CPU 微基准省时不能直接折算为 GPU 空闲或 JCT 改善；尚无本轮 GPU 结果。
若以后单独评估排序贡献，排序和非排序两臂须共享本轮控制优化。

## 运行入口

```text
scripts/start_workflow_control_optimized_candidate.py
scripts/watch_workflow_benefit_results.py --control-optimized

experiments/reports/remaining_original_projects_20261030/useful_token_cost_r1/
  r5_host400_control_optimized_r1/
  synchronous_control_audit_20261103_r1/
    replacement_prefilter_frozen_r1/
    control_optimized_operator_r1/
```

新注册名为
`workflow_original73_host400_control_optimized_20261103_r1`。资源等待器须
核对旧实验未注册且原两个进程真实退出，并观察连续两个新鲜 GPU 空闲状态；
新预约或 STOP 会终止等待，不占用或结束其他用户的任务。
资源等待器、实际模型运行和完整性能结果分别记录，不将排队称为已运行。

## 实际排队记录

2026-11-03 17:40 Asia/Shanghai 核对：

- 原等待器 `780699/786153447` 和原结果观察器 `793352/786333804`
  均已真实退出，旧 GPU 注册不存在，原终态原样保留。
- 新 operator 冻结为 `8c0e7e18`，干净目录实际通过 71 项必要检查，
  并复核全部服务来源和资格输入；实际服务构造器接受配置。
- 结果观察器单独冻结为 `c6b66fef`，73 项相关检查通过。它从资格文件
  识别服务目录，不依赖刚 fork 后可能暂时为空的 `/proc` 命令行。
  该观察路径修正没有改变已排队服务版本或原 operator。
- 新资源等待器 PID `829500`、start ticks `786882494`，nice0，实际存活。
- 新结果观察器 PID `830552`、start ticks `786906378`，nice19、
  CUDA hidden，实际存活。
- 新状态为 `waiting_idle_GPU`，结果观察器为 `waiting_resource_waiter`。
  GPU 上有三个其他任务，17:40:13 的利用率为 93%，本轮尚未注册 GPU。
  早前一次空闲观察不算连续两个空闲状态。

`queued_start_review_r1.json` 另行复核实际 PID/start ticks、两类优先级、
干净服务/operator/analysis 版本和全部资格 SHA256。自动后续只包括这次
实验的固定 18 成员停止与六项 CPU 分析，不注册下一轮 GPU 调参。

CPU 分析会同时保留旧 Full、两轮 ratio 和 Thunder 结果，检查完整
2991 请求/362157 输出 token、自然完成、尾部覆盖与原生排空，再报告
共同窗口吞吐、实际准入到服务及同步控制开销。目标尚未达到，
排队和 CPU 资格不代表已经获得性能收益。
