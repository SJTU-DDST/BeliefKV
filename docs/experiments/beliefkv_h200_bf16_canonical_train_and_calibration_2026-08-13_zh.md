# H200 BF16 Canonical Train 与 Calibration 准备

日期：2026-08-13

## 1. 结论

H200 BF16 的 64-workflow train 采集已转换为可训练的 canonical dataset。三条 recovery
结果按同一 instance 替换原始失败结果，每个预冻结 instance 恰好保留一条 trajectory；没有按
模型输出筛选任务，也没有混入 calibration/test 项目。

当前首个 FrontierBeliefModel 已完成 train-project LOPO 超参数选择与 fit，但仍是
`uncalibrated`，`online_eligible=false`、`predictive_action_eligible=false`。下一步只采集并使用
Astropy/Sphinx calibration split；`test_id` 继续封存。

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

## 5. 后续门禁

1. 在 clean Git source 上启动 `h200_bf16_v4` server，运行两个 calibration shard；
2. exporter 对 recovery/guard 使用相同 target-level censor，不以 task correctness 筛选轨迹；
3. calibration loader 只接收 calibration split，并验证 train/calibration project 不重叠、runtime
   environment digest 一致；
4. calibration 后报告概率校准、interval coverage、OOD/backoff 和各 target 支持度；
5. 在满足门槛前继续保持 online/predictive action disabled；
6. `test_id` 只在模型、校准参数和在线门槛全部冻结后打开一次。

本阶段不使用 calibration JCT 判断 BeliefKV 性能，也不使用本轮训练证明 KV migration 收益。
