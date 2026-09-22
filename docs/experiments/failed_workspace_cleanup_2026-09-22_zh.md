# 2026-09-22 失败实验 workspace 清理

仅从以下三个已停止、返回码为 1 的旧运行中删除
`workloads/workflows/*/workspace` 实体目录，每个运行 64 份，共 192 份：

- `experiments/ab/p6_h200_high_pressure_v3/20260920_v58_pair/baseline_attempt0`
- `experiments/ab/p6_h200_high_pressure_v3/20260920_v58_pair/baseline_attempt1`
- `experiments/ab/p6_h200_high_pressure_v3/20260921_v60_predictive/predictive`

这些是各轮 SWE-bench 工作树副本，不是模型权重、训练集或
当前迁移的 SGLang checkout。保留了 `workloads` 中除实体 workspace
以外的文件，包括 `workspace.json`、workflow 事件、sandbox audit，
以及各轮的 server 日志、telemetry、run contract 和结果。
未清理用于 A/B 对比的 v58/v59 或其余轮次，也未触及 baseline_attempt2
及 v60 retry1。清理后上述三轮目录分别为约 265 MiB、128 MiB、
3.1 MiB，磁盘可用空间从约 470 GiB 增至 501 GiB。

这些失败轮次的工作树内容现在无法用于逐文件复查；原始事件和统计
仍可用于诊断，但不能把这三轮作为有效的 A/B 吞吐结果。
