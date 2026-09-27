# Qwen3.5 原生物理机会压力扫描（进行中）

日期：2026-09-28。此处仅使用训练项目，不选择密封测试集的策略参数。
扫描目的是找出 HBM 有空位或可安全腾挪冷 KV、Host 有有效副本及
传输余量、而有用 KV 驱逐后重算较少的主评估区间；root 数本身不是
压力或收益标签。当前服务只做 native reactive 和 admission 机会观察，
没有启用预测式物理动作。

## 180 GB / 70:30 / 12-root：超出候选区间

- Qwen3.5-35B-A3B BF16、SGLang v0.5.20、单卡、running=48；
  180 GB Host pool 绑定 NUMA 1，FULL/Mamba=70:30，原生
  `write_through`、session radix、control mirror 和 admission 观察开启。
  训练清单为 `configs/migration/qwen35_native_reactive_overlapped_128root_workload_2026-09-23.json`
  的前 12 项，12 个 root 同批提交。服务端、client 和机会日志分别位于
  `experiments/raw/qwen35_native_regime_180g_v1/`；数据保留。
- 中途观测到 Host FULL 高水位约 11.56%，Host Mamba 达 100%。
  服务端关闭后的归因状态记录 FULL Host 驱逐 12 次、900 tokens，
  Mamba Host 驱逐 561 次、561 slots；374 个 Mamba 被驱逐前缀
  在后续请求中再次出现。Mamba 记录的
  `outcome=mamba_prefix_revisited_hit_location_unknown` **不证明重算**，
  也不能证明重新从 Host 读取；该区间不满足低 Host churn 的候选条件。
- admission writer 完整关闭：1429 条候选观察，0 条溢出/失败；
  其中 1263 条 JOIN wait 的理由为 `no_host_backed_step`，
  40 条 TOOL wait 为 `no_host_backed_step` 且 PREPARE 判为
  `no_observable_shadow_closure`。观察是每秒轮转抽样，并非机会总分母。
  服务端原生传输计数为 FULL D2H 712157 tokens、FULL H2D 0，
  Mamba D2H 1400 slots、Mamba H2D 12 slots；这些计数含该服务
  生命周期中的全部请求，不能当作预测式 PREPARE/H2D 的收益。
- 发现 Mamba 满池后停止 client，并以主进程 SIGTERM 正常关闭服务，
  使两个 writer 写出完成状态。client 被中断，**不能**用此轮评价
  workflow 完成率、正确性、JCT 或训练标签。已有 FULL 重算归因
  为 0 不等于 Mamba 无重算。

## 180 GB / 35:65 / 6-root：同样不能作为主场景

保持上述模型、引擎、NUMA、running 与 write-through 配置，换用
Host FULL/Mamba=35:65 和同一训练清单的前 6 项。
物理容量实测 FULL 63.00 GB、Mamba 117.06 GB。几分钟后 Host
Mamba 达 100%，FULL 高水位仅约 34.7%；到正常关闭服务时记录
164 次 Mamba Host 驱逐和 2 次 Mamba 前缀再访问，仍无法从当前
遥测断言这两次是否重算。FULL Host 驱逐 1 次、61 tokens。
1484 条有界机会观察中，1086 条 JOIN wait 记录为
`no_host_backed_step`；writer 完整关闭，无队列错误。本档 client
也是主动中断，不能用于 JCT/正确性比较。原始数据在
`experiments/raw/qwen35_native_regime_180g_35_65_v1/`。

下一档不继续单纯提高 root 数或重分配同一 write-through Host
pool。先验证 v0.5.20 的 `write_through_selective` 是否减少自动
备份造成的 Host churn，同时保留足够的有效 Host 副本与真实
predictive H2D 候选。只有同时满足容量、候选和工作流有效性门槛，
才冻结该档进行匹配 A/B。

`write_through` 会自动备份部分 KV；其 native D2H 不能作为选择性
`PREPARE_HOST` 的收益。单 node PREPARE/H2D 原语和只读机会观察
已在下一轮源代码中放行 `write_through_selective`，保留原有身份、
容量与 ACK 门禁；CPU 单测通过，但**尚未在该策略上做 GPU 验证**，
且 scheduler 仍未签发真实预测动作。自动备份与不可观测闭包在
观察结果中尚未充分区分。后续仍需验证 FULL/
Mamba 身份、Host 预留、ACK/消费闭环，不能仅更改启动参数或将
已有原生传输重新命名为预测式动作。
