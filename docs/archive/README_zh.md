# BeliefKV 历史文档索引

更新日期：2026-09-15

本目录中的内容只用于追溯，不代表当前实现、实验门禁或论文主张。当前入口见
`docs/README_zh.md`。

## snapshots

重写前的完整入口文档：

| 文件 | 覆盖时间 | 原因 |
| --- | --- | --- |
| `architecture_status_zh.md` | 至 2026-09-12 | 逐日日志超过 2,000 行，已由精简状态页替代 |
| `beliefkv_design_2026-07-14_zh.md` | 至 2026-08-27 | 混合当前设计、历史路线和实验过程 |
| `implementation_plan.md` | 2026-07-14 | 阶段状态已过时 |
| `architecture.md` | 2026-07-14 | 环境和验证状态已过时 |
| `beliefkv_jointplan_visual_zh.md` | 2026-07-29 | 图对应旧 P5D 阶段 |
| `README_pre_cleanup_2026-09-15.md` | 至 2026-09-12 | 根 README 混合旧 FP8、R5/v9 和历史训练状态 |

`snapshots/figures/` 保存这些旧入口文档使用的 P5D 和阶段状态图；当前图保留在
`docs/figures/`。

## plans

| 文件 | 生命周期 |
| --- | --- |
| `beliefkv_hicache_joint_control_improvement_plan_2026-07-18_zh.md` | 已完成/被后续 P5-P6 取代 |
| `beliefkv_p6_predictive_joint_execution_plan_2026-08-11_zh.md` | 被当前 `implementation_plan.md` 取代 |
| `beliefkv_h200_bf16_r5_resume_plan_2026-08-12_zh.md` | H200 迁移与重基线已完成 |
| `beliefkv_perfect_future_oracle_v2_execution_plan_2026-08-17_zh.md` | Oracle 已暂停 |
| `beliefkv_gpu_native_subagent_oracle_plan_2026-08-20_zh.md` | GPU-first Oracle 已暂停 |
| `beliefkv_extreme_performance_execution_plan_2026-08-26_zh.md` | 独立性能阶段已完成 |

## research

`technical_archive_2026-07-10.md` 是最早期技术讨论，包含已经否定的 flat
next-action predictor、静态工作流假设和旧元数据设计。

## 使用规则

- 可以从归档中恢复设计理由、失败原因和术语来源；
- 不得从归档复制运行参数到新实验；
- 不得用归档中的“计划实现”描述当前“已经实现”；
- 归档中的相对链接可能指向当时的目录结构，当前路径以主文档索引为准。
