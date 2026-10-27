# V378：按缺口选择阻塞预算的 FULL

V378 于 2026-10-27 23:15:35 Asia/Shanghai 注册，随后进入实际
GPU 重放。该轮只运行 FULL，复用完整 V345 工具衰减版 ThunderAgent；
没有再次执行 native 或 ThunderAgent。

## 冻结条件

- 应用提交：`83d7c3c2eb34d974a6475591ba7c88eeb7c60413`。
- 注册：`ops/v378_locobench_fitting_lending_20261027.registration.json`。
- 原生树：`11b84f7c31986782be3fbe699bae2574ef092389`，与 V345/V364 相同。
- 配置：`configs/migration/predictive_fitting_lending_20261027.json`。
- 端口：18657；运行目录：`experiments/raw/locobench_fitting_lending_20261027_v378/full/`。
- 实际进程：watcher 114948、server 115402、GPU scheduler 115750、
  driver 116684；PID/start ticks、父子关系、实际 argv 和 GPU context
  已独立核验，服务进程 nice 均为 0。

公共条件与 V345 一致：18 个不同项目、3674 次模型请求、
145565966 输入 token、549671 输出 token、72 次 JOIN、V311 类别
可见合成工具时间表、相同 KV 容量、running/decode24、种子 21、
原生停止、position 重放及流式计时。V379 逐字段核验匹配条件。
原始项目源码与测试、source、请求内容、工具输出和全部正常尾部不变。

## 实际策略

本轮启用 `AGENT_LENDING_SELECTION=fit`。在现有最多 8 个 blocked
owner 中，使用当前 observed-demand proof，选出足够覆盖所选请求
逻辑缺口的最小预算集合；同预算优先较少 owner。没有增加普通预取
的 ready-peer 驱逐权限，也不把逻辑预算当成已经释放的 Device 页。

保留 JOIN/EOS、工具预测卸载、prepare_host、agent 连续性和原生
物理准入。该提交还包含已由 V370 CPU 检查通过、但未在 V364 运行的
旧压力标记复核修复。因此 V378 与 V364 是不同完整配置，不能作为
同一策略的重复实验或仅对 fitting 模块的严格消融。

V376b 的 3003 项共享 CPU 检查及 4 个子测试通过；V377b 绑定实际
FULL/default ThunderAgent/工具衰减构造器及全部源文件。早先 V376
共享检查通过但后续资格脚本环境转换遗漏的记录仍保留。
原生源码证明 V378 仅重绑定 canonical patch 路径，其余状态及
实际树与既有证明相同。

## 首批独立证据

`experiments/reports/v379_v378_live_start_and_selection_20261027.json`
核验实际进程与策略，以及首批 16 个真实 fitting 选择。独立枚举
其日志记录的合格 donor 集合，全部满足最小足够预算和较少 owner
的目标。这不独立证明 native 物理可回收页数或迁移节省。

首个真实选择中，缺口为 74986 token，三个通过 proof 的候选预算为
116776、112882 和 80276 token，实际选择 80276。保留另外两个
owner 的逻辑资格，不将差值直接换算成 H2D 或 JCT 收益。

`experiments/reports/v381_v378_collecting_exact_work_20261027.json`
封存 405 条已完成回执，均匹配完整 V330 原工作与停止条件；
这是独立封存的运行中前缀，不是完整工作量或性能验收。

## 完成后的处理

CPU 后处理进程 117226 已实际核验存活，nice19、隐藏 CUDA。它只在
本轮完整成功及实际 owned 进程退出之后生成匹配 V378/V345 图表，
不会启动新的 GPU 实验；中断时保留部分证据并停止。

- 后处理：`ops/plot_v378_after_completion_20261027.py`。
- 状态：`ops/v380_fitting_lending_decode_plots_20261027.state.json`。
- 输出：`experiments/figures/decode_diagnostics_20261027_v380/`。

完整平均 JCT、makespan、工具后继 H2D、控制成本和重复性能验收
仍然待定。目标保持未完成，其他 baseline 继续等待明确且可重复的
同工作量改善。

机制与 CPU 证据见[设计记录](../design/fitting_blocked_agent_lending_20261027_zh.md)。
