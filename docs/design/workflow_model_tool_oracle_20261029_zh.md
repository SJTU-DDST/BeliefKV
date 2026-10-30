# Workflow 模型与工具联合 Oracle

本轮按用户要求先给排序器固定 trace 中的全部未来模型 token、工具时长与依赖关系，检验当前恢复排序的收益空间。它是明确使用未来信息的 oracle 实验，不是可部署预测器，也不是整个系统最优 JCT 的数学上界。

## 排序分数

每个 workflow 使用同一份固定重放 DAG。模型节点的成本为：

```
(必要新增输入 token * 0.01 + 输出 token) * 每个等价 token 的模型时间
```

换算系数在实验前固定，取已完成原始 2 秒 Full 对照的 decode `request_step_ms / request_steps / 1000`。这是跨上下文、batch 的平均成本代理，不声称知道新调度下未来 GPU 的精确执行时间。输入权重保留已有模型的 0.01，不用本轮性能调参。

工具节点使用本次实际重放的 V311 分类 TraceLab 时间，包括未被替换的原始工具时间。其余固定事件间隔也保留原 DAG 值。沿串行依赖求和，在分支汇合处取最大值；最终 workflow end 的剩余路径秒数就是排序分数。不会把同时执行的两个工具时间相加。

已完成事件和请求清零。已启动工具按实际观测的开始时间扣减经过时间。模型的部分输出沿用现有每 128 token 与完成时的进度通知，必要输入在请求完成时计费，与原预测标签一致。该口径没有逐 chunk 的真实 prefill 已完成进度。

保留模型总剩余工作量、模型单独路径和外部延迟单独路径，方便审计。单独两条最长路径之和未必等于联合最长路径，因为最大值可能来自不同分支。

## 调度边界

以已完成 learned workflow dispatch 的配置为匹配基础：2 秒完整控制周期、500 ms 有界空闲容量补位。仅将原来的三档模型预测换成联合剩余路径连续值。周期恢复仍先保留 ThunderAgent 原类别，再使用 oracle 分数、上下文大小和入队序号；补位仍保留真实 JOIN 优先。不新增 active agent 抢占，不改变 decay、原生容量、sibling 安全约束或停止条件。

未来 DAG 只供 oracle 计算排序分数，不向运行图提前发布 SPAWN/JOIN，不提前获得资格或驱逐授权。普通 learned 模式和 paper arm 禁止继承 oracle；状态、实验配置和注册都明确记录未来信息与不可部署属性。

工具预取、普通预取、逐层 handoff、mixed chunk 仍关闭。JOIN/EOS 预取、prepare_host、工具预测卸载、agent 连续性、sibling 与原生需求恢复保留。工具卸载预测器本身不读 oracle 工具时长；这轮检验的是联合排序，不能归因为完美卸载预测。

## 数据与验收

保持 V230 全部 18 个不同项目、3674 个模型请求、145565966 个输入 token、549671 个输出 token、72 次 JOIN、92 个 child；V311 工具时间、10 个初始 workflow 加 8 个平均 120 秒到达、种子 21、KV 容量、running/decode 24 与原 native 停止全部一致。

先跑实际构造器、控制隔离、串并行工具与进度守恒的定向测试，再保留已有 5141 个 CPU 测试及 6 个 subtest 的完整来源绑定回归。冻结代码后重新验证 24 个真实 token/session/native-stop 请求与 1/8/24 sampler，再只跑一个完整 oracle Full。复用已完成 ThunderAgent decay、原始 Full 和 learned dispatch，无须额外 native 采集或重复性能对照。

完整结束、两份新鲜空闲 telemetry 及实际进程退出后，比较完整平均 JCT、全程输出吞吐、decode 时间分布、控制 CPU、迁移和重算。检查排序是否实际改变选择、工具是否真的影响分数，并验证全部 oracle 模型工作被实际消费、所有 workflow 剩余量为零。保留正常长尾、失败尝试和负结果。

此前离线 `analyze.py` 只在 learned 实验已发生的局部候选集合上替换剩余模型工作量，不能模拟随后容量与轨迹，也没有工具路径；它不替代本轮 GPU 实验。

## 本轮完整 GPU 结果

实现冻结于 `e718d34e`。实际注册为
`/home/longhao/experiment/ops/workflow_model_tool_oracle_20261029_r1.registration.json`，
完整日志位于 `experiments/raw/workflow_model_tool_oracle_20261029_r1/full`。
代码、CPU 绑定和只读审计分别提交，保留此前无工具的离线诊断与浮点测试断言失败记录。

当前来源绑定 CPU 回归实际通过 5178 个测试及 6 个 subtest，保留全部 5141 个旧测试身份，无失败或跳过。新 GPU 校验通过 24 个精确 token/session/native-stop 请求，全部 24 个 native stop，sampler 1/8/24。正式实验随后完成全部 18/3674 工作、145565966 个输入 token、549671 个输出 token、72 次 JOIN、92 个 child，原停止条件及所有正常尾部保留。

oracle 实际消费的必要新增输入为 4117249 token，输出为 549671 token；18 个 workflow 的模型工作余量全部归零，14966 个源图事件匹配，未出现未绑定核心事件或预测器错误。两份新鲜空闲 telemetry、原进程退出与空闲 GPU 均已核对。完整只读分析七项动作全部返回 0；196 次空闲补位均有精确请求/attempt/session 后续服务及完成凭据。三张配对 PNG 已打开检查。

| 方法 | 平均 JCT 秒 | Makespan 秒 | 全程输出 token/s | decode 输出/worker 秒 |
| --- | ---: | ---: | ---: | ---: |
| ThunderAgent decay | 1504.072304 | 2305.899187 | 238.375989 | 339.095798 |
| 原始 2 秒 Full | 1439.457324 | 2253.756840 | 243.890996 | 346.470902 |
| learned 分档及 500 ms 补位 | 1432.302431 | 2274.218978 | 241.696602 | 原完整结果保留 |
| 模型与工具联合 oracle | 1428.715608 | 2275.114080 | 241.601511 | 346.563302 |

oracle 相对原始 Full 的平均 JCT 降低 **0.7462%**，仅 10.741716 秒；全程吞吐下降 **0.9387%**，makespan 增加 21.357240 秒。相对更匹配的 learned dispatch，JCT 仅降低 **0.2504%**，吞吐下降 **0.0393%**。相对 ThunderAgent 的 5.0102% JCT 与 1.3531% 吞吐改善包含已有 Full 设计的收益，不能全归于 oracle。decode worker 输出率相对原始 Full 只增 0.0267%，没有明显计算效率改善。

1132 次周期决策中，507 次有候选、331 次有多个候选。局部复现发现 34 次相对原始类别/上下文顺序改变选中集合；去掉工具及固定外部间隔后，有 10 次集合选择不同。这里固定的是当次候选、类别、sibling 关系与剩余逻辑预算，不模拟后续物理容量或完整反事实 JCT。补位记录中有 3970 次顺序变化，但全部实际补位只有 196 次，顺序变化不等于执行集合变化。

## 控制成本与结论边界

本实现每次被查询时遍历该 workflow 的 DAG。15293 次路径计算遍历 16029800 个节点，累计 **28.452437 秒线程 CPU**；观测更新另用 0.292330 秒。周期完整控制累计 CPU 2.704793 秒，P50 0.144596 ms、P99 14.754170 ms。空闲补位评分/排序累计 CPU **26.517127 秒**，对照 learned dispatch 为 0.091388 秒。路径计算已包含在这些控制路径中，不能重复相加，更不能直接从 JCT 中减去 28 秒。

因此，本轮是“具有真实未来模型/工具信息、带实测控制成本的当前恢复策略”结果，不能当作零成本 oracle 或所有 agent 调度策略的最优上界。它尚不支持“仅继续提高剩余工作量预测准确率就能得到明显收益”。保留原始 Full 为默认；oracle 永远保持显式、不可部署的实验模式。

需要优先排除的已确认实现成本是 DAG 的重复计算，适合缓存或增量更新。另一个需要独立验证的问题是：保留原始恢复类别和 active 连续性时，预测到底能在多少有真实容量竞争的场合改变实际执行集合。当前数据只支持这个方向的审计，不证明取消连续性或强行抢占会更优。

原生 ACK 统计显示，oracle H2D 为 2775.832855 GB，对照原始 Full 为 2659.224748 GB，增加 **4.3850%**；D2H 为 869.316035 GB，对照 939.598381 GB，减少 **7.4800%**。较少 D2H 不代表整个迁移更好，也不能将 ACK 字节直接解释为关键路径等待或可消除的 JCT。

完整证据入口：

- `experiments/reports/workflow_resume_oracle_20261029/complete_review_r1/review.json`
- `experiments/reports/workflow_resume_oracle_20261029/complete_review_r1/oracle_choices.json`
- `experiments/reports/workflow_resume_oracle_20261029/complete_review_r1/free_dispatch.json`
- `experiments/reports/workflow_resume_oracle_20261029/complete_native_acks_r1.json`
- `experiments/reports/workflow_resume_oracle_20261029/complete_review_r1/pair/`：完整配对曲线、分布及几何。
- `experiments/reports/workflow_resume_oracle_20261029/complete_review_r1/incremental_plots/`：原始 Full、oracle 和 ThunderAgent 的同工作量曲线与分布。

这是每种方法一次完整执行的开发诊断，不是重复实验的显著性证明；不启动其他 baseline，不依据局部观测筛选任务，也不把这轮标为全局性能目标完成。
