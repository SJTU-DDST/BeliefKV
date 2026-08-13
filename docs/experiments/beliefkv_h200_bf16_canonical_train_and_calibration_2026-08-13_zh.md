# H200 BF16 Canonical Train 与 Formal Calibration

日期：2026-08-13 至 2026-08-14

## 1. 结论

H200 BF16 的 64-workflow train 采集已转换为可训练的 canonical dataset。三条 recovery
结果按同一 instance 替换原始失败结果，每个预冻结 instance 恰好保留一条 trajectory；没有按
模型输出筛选任务，也没有混入 calibration/test 项目。

当前首个 FrontierBeliefModel 已完成 train-project LOPO、fit 和 Astropy/Sphinx held-out
calibration。校准没有重新拟合训练计数，`test_id` 仍然封存。由于工具等待分布重尾、workflow-macro
覆盖仍不均衡，artifact 保持 `online_eligible=false`、`predictive_action_eligible=false`，仅允许
shadow/replay 使用。

## 2. Canonical 合并与资格契约

实现入口：

- `beliefkv/experiments/p6_canonical.py`：读取 result selection manifest、导出多 run canonical
  数据、校验 instance 守恒并生成 coverage；
- `scripts/export_canonical_p6_dataset.py`：正式导出与 coverage-only 重算入口；
- `beliefkv/experiments/p6_dataset.py`：两级资格和 target/horizon 粒度 censor；
- `beliefkv/predictor/structured_frontier.py`：fit 接受 formal-local 行，terminal/JCT 与正式 test
  仍要求 clean evidence。

资格定义如下：

- `formal_local_training_eligible`：允许使用 cutoff 前已经完成的局部 action、token demand、tool
  survival 和 reentry 标签；
- `clean_trajectory_eligible`：只在完整 workflow 没有 runtime intervention 时成立，供完整轨迹、
  terminal outcome 和 JCT 使用；
- 跨越 intervention cutoff 的 label 单独 censor；已经在 cutoff 前结束的 tool/RETURN/JOIN 不因
  同 workflow 后续 guard 而失效。

Canonical dataset：

`experiments/processed/h200_bf16_formal_train_v1_canonical`

- dataset manifest SHA-256：
  `20e0c2213d3a02ea0f00be9d6190cd8775de7f0f8d218db9fdcd8ebf5099cc67`；
- 64 个 instance、64 个 workflow、7 个 train project；
- 96,725 个 decision row，其中 83,712 个至少含一个可训练 target；
- 35,038 个 clean decision row；
- 48,674 个 intervention-affected workflow 中保留下来的局部可训练 row；
- 13,013 个 intervention-affected 且完全被 censor 的 row；
- 58 个可训练 JOIN reentry 全部 closure-complete；
- 9,989 个 tool survival 样本；
- 108 个 RETURN 与 76 个 JOIN runtime transition。

Coverage gate 通过。唯一警告是 exact incremental action boundary 为 0%；当前 runtime 只在完整
AIMessage 返回后解析 action，因此这不阻塞“最终 action 类型 + 剩余 decode demand”模型，但阻塞
early dispatch 和 run-to-action 的性能主张。

## 3. 首个 H200 FrontierBeliefModel

LOPO artifact：

`experiments/models/frontier_belief_h200_bf16_v1_lopo.json`

- SHA-256：`5ffc62d5b20b4e3bdc254f0fe062046b4911fa6348734566a92542a0a3801252`；
- 7 个 train project 逐项目留一；
- 选择 candidate 3：boundary order 4、boundary support 6、empirical/tool support 8；
- project-macro loss：1.2716608842。

未校准模型：

`experiments/models/frontier_belief_h200_bf16_v1_uncalibrated.json`

- SHA-256：`eca2cd59e7d6a828ea5a5cb3f750a914f79e1b8887ded44af6da019242748c53`；
- 83,712 decision point、8,318 local episode、4,469 episode group；
- boundary/remaining decode 71,264，next output 57,399，prompt growth 57,092，tool 10,116，
  join wait 40,434；
- 模型 metadata 绑定 dataset manifest、coverage、LOPO、H200/model/SGLang runtime environment；
- 未使用 calibration 或 test 数据。

## 4. Frozen Calibration Plan

计划目录：

`configs/p6/h200_bf16_formal_calibration_v1`

- plan ID：`h200-bf16-formal-calibration-v1`；
- plan SHA-256：`d64af1952beb6e95e3784ebc1aee22c73a7728a209b16cb6372b88ca0ed5b749`；
- 16 个唯一 calibration instance：Astropy 8、Sphinx 8；
- shard 1：8 个 `parallel_analysis_2to3`，Astropy/Sphinx 各 4；
- shard 2：8 个 `natural`，Astropy/Sphinx 各 4；
- 固定 seed、版本多样化、无结果筛选；
- predictor 与 predictive action 关闭，使用 frozen P5 observed policy；
- `fit_or_model_selection_use=false`，`test_id_accessed=false`；
- 绑定与 train 相同的 `h200_bf16_v4` profile SHA
  `5ece5b5075193856b1cf7fff081378fe1a4040734bd80133838713a9a90cd6ba`。

Astropy/Sphinx 完整 Git object 已迁入，16 个 base commit 均可解析。16 个 SWE-bench image 已拉取并
锁定为 immutable RepoDigest，总逻辑大小约 17.23 GB。

## 5. Calibration 采集与 Censor 修复

两个预冻结 shard 均在 predictor 和 predictive action 关闭时完成：

- parallel shard：8/8 workflow 自然结束，784 次 LLM、1,700 次工具调用、16 个 child、8 次 JOIN；
- natural shard：8/8 workflow 自然结束，544 次 LLM、1,116 次工具调用、10 个 child、10 次 JOIN；
- 共 16 个 instance、16 个 workflow、2 个 calibration project，未访问 `test_id`；
- 24,394 个 decision row，其中 10,911 个 clean row、11,720 个 intervention 前局部可训练 row、
  1,763 个完全 censor row；
- 15/15 个 eligible JOIN 的因果闭包完整，记录 26 个 RETURN；
- natural/parallel 与 Astropy/Sphinx 均覆盖全部六类训练 target。

运行时 reentry 覆盖此前不足的根因不是事件缺失，而是 `CALL_CENSORED` 使用 runtime root ID，LLM
invocation 使用规范化 ID。现在只对 `CALL_CENSORED` 提供严格、唯一且限时的 identity fallback，并把
`CALL_CENSORED`、`JOIN_TIMEOUT` 记为右删失 reentry endpoint。正常 RETURN/JOIN 仍要求精确 identity。
重导出后：

- parallel：758/758 reentry 有归因，其中 723 observed、35 right-censored；
- natural：526/526 reentry 有归因，其中 500 observed、26 right-censored；
- censor 不作为成功事件参与 terminal 分类或完成等待时间校准；完整结束于 cutoff 前的 tool wait 和
  RETURN/JOIN 仍保留为局部训练证据。

统一 coverage report：

`experiments/processed/h200_bf16_formal_calibration_v1/coverage_report.json`

- SHA-256：`df681dec4b77763308bf56d2e73d14f28c14c1da78848b8c1bb8f502adbd7042`；
- coverage gate 通过，无 blocker；
- 唯一 warning 仍是 `exact_incremental_action_boundary_unavailable`。

## 6. Held-out Calibration 结果

正式 artifact：

`experiments/models/frontier_belief_h200_bf16_v1_calibrated.json`

- SHA-256：`c7dbdfefd1caedbf1ff403fd7891cd0f9595f8bcd7418c2670a1f5c97e2fa5a1`；
- 22,631 个 eligible decision row、1,209 个 episode、2,236 个 local episode；
- boundary temperature 0.90，tool terminal temperature 0.85；
- conformal calibration unit 为 local-episode 最大 nonconformity，目标覆盖率 90%；
- metadata 绑定 calibration dataset、coverage report、H200 runtime environment，并明确
  `test_id_status=sealed_not_evaluated`；
- `training_counts_refit=false`，calibration 没有反向修改 fit 数据或 LOPO 选择。

Held-out 指标：

| 目标 | 结果 |
|---|---:|
| Boundary accuracy / ECE | 94.96% / 0.0023% |
| Tool terminal accuracy / ECE | 86.82% / 10.72% |
| Next-output local-episode interval coverage | 90.15% |
| Prompt-growth local-episode interval coverage | 93.90% |
| Remaining-decode local-episode interval coverage | 90.17% |
| External-wait local-episode interval coverage | 90.13% |

所有 action-specific target 在 calibration 数据中均有支持。40.86% 的 composite OOD 表示“任意一个
预测头不可用”，不能作为所有动作的一票否决；在线接入仍必须按动作检查所需预测头。

当前不能解封预测性动作，原因不是分类精度，而是风险区间仍不够稳定：external-wait 的平均区间宽度
约 3.94e6 ms、MAE 约 4.30e5 ms，workflow-macro coverage 也只有 87.40%；next-output 的
workflow-macro coverage 为 88.47%。这些结果说明不同 calibration workflow 之间仍有明显重尾和
异质性。

## 7. 后续门禁

1. 当前 artifact 只用于 shadow/replay，不执行预测驱动的 offload、restore 或 retraction；
2. 在不访问 `test_id` 的前提下，先改进 tool/external-wait 的分层 survival 与 OOD fallback；
3. 冻结模型、校准参数、动作相关支持门禁和在线策略后，再打开一次 `test_id`；
4. exact incremental boundary 未实现前，不做 early dispatch 或 run-to-action 主张；
5. 本阶段不使用 calibration JCT 判断 BeliefKV 性能，也不使用本轮训练证明 KV migration 收益。
