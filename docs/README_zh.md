# BeliefKV 文档导航

更新日期：2026-10-06

当前主线为Qwen3.5-35B-A3B / SGLang 0.5.20，同一
`beliefkv-next` 环境。84-root单波v6开发pair已结束，两侧84 completed，
最近配置running=48、Host 200 GB/NUMA node 1。7个JOIN H2D全部
首次复用，但仍均在EOS后启动；单轮吞吐+4.62%有实际需求混杂。
阶段头冻结的工作预测改进已提交，v7单pair现已启动reactive，
500 ms center/250 ms观测协议窗口；84个首请求已提交，不排额外重复，
正式实验再多轮取平均。
旧Qwen3/0.5.2rc1、canary和旧池配置不作为当前默认。

本页是项目文档的统一入口。文档状态分为：

- **当前**：可以作为代码实现、实验计划和论文表述的依据；
- **参考**：仍有技术价值，但不是当前实现契约；
- **历史**：只用于追溯决策，不得覆盖当前文档；
- **实验凭证**：记录当时的代码和结果，不随当前设计修改。

## 1. 当前权威文档

| 文档 | 用途 |
| --- | --- |
| [当前系统设计](beliefkv_design.md) | 算法、状态机、预测质量边界和未来可选方案 |
| [当前架构状态](architecture_status_zh.md) | 已实现、已验证、未验证和阻塞项 |
| [当前执行计划](implementation_plan.md) | 唯一有效的近期实施顺序 |
| [实验注意事项](experiment_operating_notes_zh.md) | 配置、冻结、统计口径及清理约束 |
| [JointPlan 图解](beliefkv_jointplan_visual_zh.md) | 目标架构参考，不代表新版迁移全部完成 |
| [Architecture](architecture.md) | 英文简要入口 |

发生冲突时按上表顺序解释语义，但“当前实现是否完成”以架构状态页和代码为准。

## 2. 环境与运行

| 文档 | 状态 |
| --- | --- |
| [安装与环境](setup.md) | 当前 |
| [Runtime 接入](runtime_integration_zh.md) | 新版入口与旧接口参考分开，具体符号以代码为准 |
| [服务器迁移](migration_guide_zh.md) | 新版要点与历史迁移记录分开 |
| [README](../README.md) | 项目入口、命令和目录 |

冻结实验参数应从对应 `configs/` profile 读取，不从历史报告复制。

## 3. 研究背景

| 文档 | 状态 |
| --- | --- |
| [相关工作比较](related_work_comparison_2026-07-21_zh.md) | 持续更新 |
| [动态 Agent Workflow 分析](beliefkv_dynamic_agent_workflow_considerations_2026-07-20_zh.md) | 参考 |
| [HiCache/Theta KVPool 启发](hicache_theta_kvpool_implications_2026-07-18_zh.md) | 参考 |
| [ClawTrace 数据分析](clawtrace_dataset_analysis_2026-07-13.md) | 参考 |

## 4. 实验凭证

[`docs/experiments/`](experiments/README_zh.md) 中每个文件对应一次实验、修复或
characterization。它们遵循：

- 保留原始日期和当时结论；
- 后续发现测量错误时，在原报告中明确撤回或降级；
- 不因当前算法变化而重写历史数据；
- 不单独作为“当前系统已经支持某能力”的依据。

当前最相关的报告：

| 报告 | 结论 |
| --- | --- |
| [v5 84-root结果](experiments/joint_tool_join_h2d_v5_84root_zh.md) | 17个预测H2D；JOIN11个复用，工具6个被再回收，完成吞吐-8.95% |
| [v4工具/JOIN恢复](experiments/joint_tool_join_h2d_v4_zh.md) | 六个JOIN H2D及吞吐负结果；补有GPU利用率根因复核 |
| [84-root与对照要求](experiments/pressure84_and_fair_comparison_2026-10-05_zh.md) | 当前单pair；正式多轮平均，回放不是前置要求 |

以下为旧Qwen3/0.5.2rc1 checkpoint参考，不能覆盖新版状态：

| 报告 | 当时结论 |
| --- | --- |
| [PREFETCH recall 与 funded path](experiments/beliefkv_p6_prefetch_recall_enablement_2026-09-16_zh.md) | v6 校准、在线假阳性修复与最新 bounded gate |
| [Natural PREPARE no-opportunity](experiments/beliefkv_p6_natural_prepare_canary_no_opportunity_2026-09-12_zh.md) | 高 HBM 不等于存在 beneficiary |
| [PREPARE_HOST mechanism gate](experiments/beliefkv_p6_prepare_host_mechanism_gate_2026-09-12_zh.md) | 单笔真实 D2H 机制闭环通过 |
| [Direct predictive pipeline](experiments/beliefkv_p6_direct_pipeline_high_pressure_2026-09-11_zh.md) | 出现 timely-positive，但当时物理动作关闭 |
| [Future-growth top-4](experiments/beliefkv_p6_future_growth_top4_shadow64_2026-09-10_zh.md) | beneficiary/value 路径首次产生正收益 |
| [Performance/transfer gate](experiments/beliefkv_extreme_performance_p3_p4_gpu_gate_2026-08-26_zh.md) | 控制面与 bundle transfer 基线 |

## 5. 历史归档

- `docs/archive/snapshots/`：重写前的权威设计、状态、图解和计划全文；
- `docs/archive/plans/`：已经完成、暂停或被替代的执行路线；
- `docs/archive/research/`：已被当前设计取代的早期讨论。

归档文档不得作为当前实现状态。索引见
[archive/README_zh.md](archive/README_zh.md)。

## 6. 已归档路线

以下路线不在当前关键路径：

- 2026-07 HiCache Joint Control P0-P8；
- 2026-08 H200 BF16 R5 恢复计划；
- Perfect-Future Oracle v2；
- GPU-first Oracle；
- Performance-first 独立优化阶段；
- 2026-08-11 P6 旧执行计划；
- morphology 独立策略。

相关代码可以继续用于测试或诊断，但不能据此扩展当前论文主张。

## 7. 文档维护规则

1. 按设计/状态/执行分工修改对应权威页，并同步检查其它当前入口。
2. 模型、框架或实验配置变化时，同步维护环境、runtime及迁移说明。
3. 已实现、GPU已验证、净收益已证明三类事实必须分开。
4. 新实验记录对应报告；旧实验的新诊断追加到原报告并注明日期，
   不能仅新增一份报告而不更新已有状态。
5. 完成或被替代的计划移动到 `docs/archive/plans/`。
6. 不再向状态页追加按日期排列的完整实验报告。
7. 文档中的“已实现”“已验证”“已产生收益”必须分开表述。
