# V364：FULL 压力暂停候选

持续优化目标在 V345 完整对照后继续推进。V330 的平均 JCT 和
端到端输出吞吐改善为 3.003%/1.914%，仍不足以满足明显、可重复的
验收条件，因此本轮只验证一个新的 FULL 候选，直接复用 V345。

## 冻结与启动

- 注册时间：2026-10-27 21:39:39 Asia/Shanghai。
- 注册：`ops/v364_locobench_projected_pressure_20261027.registration.json`。
- 运行目录：`experiments/raw/locobench_projected_pressure_20261027_v364/full/`。
- 应用提交：`0127655243099ed9ac45631e7321c7462180776a`。
- 原生树：`11b84f7c31986782be3fbe699bae2574ef092389`。
- 端口：18656。
- 实际进程：supervisor 72569、server 73085、GPU scheduler 73491、
  replay driver 74538。已核对 PID/start ticks、argv、父子关系及 GPU context。

注册前的最新资源检查确认 GPU 利用率为零、无其他计算进程、
无其他 owned 实验、端口空闲。旧预约释放记录及后续用户持续目标
分别保留在共享 hold 中，原记录归档为
`ops/gpu_hold_after_v345_before_projected_20261027.json`。
任何新的预约或暂停仍由监督器检查，本轮未排入其他 GPU arm。

## 实际条件

与 V330/V345 相同：18 个不同项目、3674 个请求、72 次 JOIN、
145565966 输入 token、549671 输出 token、V311 工具时长表、
原生树、Device/Host 容量、running 上限 24、种子 21、
position 重放、原生停止及流式计时。

V367 独立启动审计验证这些公共字段，完整比较 reference environment
后唯一新增项是 `AGENT_PRESSURE_PAUSE_POLICY=projected_release`。
实际运行确认该策略及 `planned_release_grants_capacity=false`。
默认与工具衰减 ThunderAgent 的策略源码均与 V345 相同。

V365 首批封存 129 条完整回执，均与完成的 V330 原始工作及停止条件
一致；105 个预算快照全部同快照对账。这不是完整模型正确性或性能
结论，不用前缀 batch 与完整对照平均值组成加速比。

本轮来自 V358c 的 2942 项共享 CPU 检查及 4 个子测试，
实际构造器和 paper 隔离证据为 V359c。
原生源码状态只重新绑定 canonical patch 绝对位置，V363 验证其他
源码状态与已验证原生树完全相同。

## 完成后的处理

CPU-only 后处理进程 74773 等待本轮完整成功及全部实验进程退出，
再生成 V364 与完成的 V345 的完整时序、时间加权分布和 geometry 图。
若本轮中断则停止后处理并保留部分结果，不重启 GPU 或重跑对照。

- 脚本：`ops/plot_v364_after_completion_20261027.py`。
- 状态：`ops/v366_projected_pressure_decode_plots_20261027.state.json`。
- 计划输出：`experiments/figures/decode_diagnostics_20261027_v366/`。

完整 JCT、makespan、工具 H2D、暂停标记和控制成本继续等待实际结果。
设计与失败记录见[压力暂停预算计划](../design/projected_pressure_release_20261027_zh.md)。
