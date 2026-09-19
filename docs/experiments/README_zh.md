# BeliefKV 实验报告索引

更新日期：2026-09-19

本目录保存单次实验、修复和 characterization 的不可变证据。报告描述的是当时的代码、
硬件和配置，不自动代表当前系统能力；当前结论以
[`../architecture_status_zh.md`](../architecture_status_zh.md) 为准。

## 当前关键证据

| 报告 | 用途 |
| --- | --- |
| [Prepare/Prefetch utility fix](beliefkv_p6_prepare_prefetch_utility_fix_2026-09-19_zh.md) | v50 中期根因与 beneficiary-bound/refresh 修复 |
| [Wait-shadow latency v47/v48](beliefkv_p6_wait_shadow_latency_v47_v48_2026-09-19_zh.md) | 校正 publish 指标并定位 bounded physical preview 瓶颈 |
| [Wait-shadow validation latency v46](beliefkv_p6_wait_shadow_validation_latency_v46_2026-09-19_zh.md) | PREPARE 迟到发布/验证根因与 same-safe-point 修复 |
| [Natural PREPARE no-opportunity](beliefkv_p6_natural_prepare_canary_no_opportunity_2026-09-12_zh.md) | 区分高 HBM 与真实 beneficiary opportunity |
| [PREPARE_HOST mechanism gate](beliefkv_p6_prepare_host_mechanism_gate_2026-09-12_zh.md) | 单笔 predictive D2H 全链路正确性 |
| [Direct predictive pipeline](beliefkv_p6_direct_pipeline_high_pressure_2026-09-11_zh.md) | timely-positive package 与物理门禁状态 |
| [Future-growth top-4](beliefkv_p6_future_growth_top4_shadow64_2026-09-10_zh.md) | beneficiary/value 路径的正收益候选 |
| [Action-local overlay](beliefkv_p6_action_local_overlay_shadow64_2026-09-07_zh.md) | candidate-local physical evidence |
| [Performance/transfer gate](beliefkv_extreme_performance_p3_p4_gpu_gate_2026-08-26_zh.md) | 控制面和 bundle transfer 基线 |

## 阅读顺序

1. 先读当前架构状态，确认某报告是否仍有效。
2. 再读同一主题日期最新的报告。
3. 若新报告明确撤回旧结论，以新报告为准，但旧报告仍保留。
4. 原始 artifact 路径不存在时，只能引用报告中的审计结论，不能补造数据。

## 报告分类

- `2026-09`：H200 BF16 beneficiary-bound predictive path 和 PREPARE gate；
- `2026-08-12` 以后：H200 BF16 数据采集、调度与性能结果；
- 更早报告：RTX 6000 Ada/FP8 或早期 P1-P5 机制证据；
- 名称含 `oracle`：当前只作诊断，Oracle 路线已暂停；
- 名称含 `morphology`：保留 transfer-cost 研究历史，不是当前独立策略；
- 名称含 `smoke`、`micro`、`gate`：机制或局部门禁，不是吞吐结论。

## 新报告要求

每次实验新建一个文件，至少记录：

- Git commit、runtime profile、workload manifest 和 artifact key；
- 是否为 natural、shadow、canary、diagnostic 或正式 A/B；
- workflow completion/censor/timeout 口径；
- allocator、Radix、transaction 和 shutdown correctness；
- GPU/HBM/Host/transfer/control-plane 指标；
- 可以支持的结论和明确不能支持的结论；
- 原始结果目录。

实验报告不得被用来存放未来实施计划；计划只维护在
[`../implementation_plan.md`](../implementation_plan.md)。
