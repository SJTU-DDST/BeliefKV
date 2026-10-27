# V330 Full 完成与 workflow 尾部

Full 在 2026-10-27 04:41 America/Los_Angeles 收尾。
独立验证报告为
`experiments/reports/v341_v330_full_complete_20261027.json`。
冻结 application、native tree、V311 时间表和全部容量不变。

## 完整工作与指标

- 18 个不同项目，3674 个请求，145565966 输入 token、
  549671 输出 token，72 次 JOIN。
- 完整回执与历史原始工作逐请求匹配，原生停止及 session lifecycle
  检查通过；历史回执只验证工作，不提供匹配的工具计时性能对照。
- 平均 workflow JCT 为 1646.612839s，中位 1653.404019s，
  P90 为 2094.916268s，最大 2310.987541s。
- Workflow event makespan 为 2311.112390s，全程输出吞吐
  237.838282 token/s。
- 无质量排除，全部正常尾部保留。两个新鲜 idle telemetry 快照和
  实际 owned-process 退出均通过检查。
- 全部 3674 个原生请求 probe 的旧输入前缀丢失代理为零；Host eviction
  再访问归因仍记录 7590 个 FULL recomputed units。两个定义不同，
  不能将前者表述为所有重算均为零。

本轮 `thunderagent_acting_decay` 随后实际启动；server 4193558、
GPU scheduler 4193919、driver 2218 的进程身份已检查。PID 小于旧 PID
不是继承旧进程的证据，按实际 start ticks 和 argv 识别。该对照使用
完全相同的 V311 表、工作量、streaming、容量、engine 和 seed21。
完整匹配结果仍待对照结束，不能接受跨时间表的胜出结论。

随后用户预约 2026-10-27 20:00 至 2026-10-28 08:00 Asia/Shanghai。
监督器在 19:53:09 因新 hold 收尾，19:53:15 完成 owned cleanup，
活动进程与 GPU context 已释放。V342 独立验证 stop、预约边界及
847 个中断前完整回执。原始 `stopped_for_investigation` 状态和
`HoldChanged` 保留，归类为用户预约导致的行政中断，不是算法性能
失败或完整强对照。不能继续部分请求冒充完整实验；保留已完成 Full，
未来如需补齐对照，单独注册匹配的工具衰减版 ThunderAgent。

## 尾部现象

以下历史数据只比较是否存在尾部，不能作性能胜负对照：

| 运行 | 工具时间表 | 中位 JCT/s | 最大 JCT/s |
| --- | --- | ---: | ---: |
| V330 Full | V311 可观察类别 | 1653.404 | 2310.988 |
| V295 ThunderAgent optional ACTING decay | V269 混合网络 | 1719.385 | 2324.668 |
| V286 默认 ThunderAgent | V269 混合网络 | 1649.153 | 3137.977 |

历史 ThunderAgent 也有尾部；V295 最慢的三个项目为 blockchain_nft、
mobile_game、api_gateway，与 V330 最慢三个项目相同，但顺序不同。
这支持检查固有工作与服务次序，不能证明尾部全由固定负载造成。

按 V330 实际 workflow-end 时间，最后 448.611676s 只剩三个
workflow，最后 157.981231s 只剩一个。剩余 workflow 数不等于可执行
agent 数，这个统计也没有证明期间 GPU 空闲或 KV 恢复成本为零。
此处不截掉这些时段，不将窗口吞吐替代全程吞吐。

V338 的完整完成路径审计：
`experiments/reports/v338_v330_full_causal_jct_paths_20261027.json`。
最慢 mobile_game 的 JCT 为 2310.987541s，路径含：

- 853.187666s 的 tool-end 事件间隔；
- 530.408928s 的请求提交到首次 native launch；
- 872.630741s 的首次 launch 到 native complete；
- 其余约 54.760s 的提交前与事件处理间隔。

Tool-end 间隔是实测因果路径段，不能直接等同于纯工具 kernel 时间。
提交后等待包括逻辑资格、物理恢复和原生排队，不能全归为一个调度
原因；首次 launch 到完成包含 interleaving、恢复与其他等待，也不是
纯模型计算。路径全部分项重构原始 JCT，没有删掉工具或正常尾部。

固定串行工具等待可以压低模型侧优化的相对 JCT 收益；若尾部等待
来自策略推迟仍可执行的请求，则属于策略本身需要解决的问题。
完整匹配比较需保留逐 workflow 差值，再核对首服务、持续服务间隔、
恢复和请求交错，不能先将慢项目认定为负载异常。

## 迁移与 decode

V339：
`experiments/reports/v339_v330_full_restore_churn_20261027.json`。
全部原生传输 H2D 为 3369.115877GB，其中 tagged 为 145.724670GB；
未 tagged 的 native demand H2D 为 3223.391207GB，与完整请求 Host-hit
字节逐聚合对账。后继请求归因中 tool 为 2686.822711GB、JOIN 为
513.412104GB、其他为 23.156392GB。它们是同一原生 demand 总量的
请求队列归因，不是唯一页数、独立浪费、可提前覆盖空间或节省时间。

V340：
`experiments/reports/v340_v330_full_decode_20261027.json`。
87902 个 decode batch，完整平均 batch 为 6.250711，worker 区间加权为
9.023021；前缀 V335 的 11.035 不能代替完整平均值。Prefill worker
区间累计 440.528123s，decode 为 1539.940081s，均不是纯 kernel 时间。
完整 batch=1 的 worker 区间累计 132.401313s，但不能将它直接归为最后
一个 workflow 的全部时间。

以上证据证明 Full 正常完成并揭示尾部与迁移成本，尚未证明优于本轮
强对照，更未满足可重复或其他 baseline 的完整验收。
全程输出吞吐应称为端到端输出吞吐；固定 token 下与 makespan 互为
倒数，不能当作独立 decode 效率。完整口径见
`docs/research/throughput_metric_contract_20261027_zh.md`。

完整逻辑预算与工具卸载机制现另有
[V349/V350b 审计](v330_complete_capacity_and_offload_audit_20261027_zh.md)。
全程已知阻塞预算占比为 23.586%，同时存在 inactive 已提交请求的
阻塞预算占比为 2.974%，均不是物理 HBM 或可消除等待。预测式提前
释放仅 0.119GB，另有实际容量不足时的工具与 JOIN 驱逐，应分别归因。
