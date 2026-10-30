# Workflow 分档实验结果与 Mixed Chunk 验证

## 当前结论

保留原 2 秒控制周期 Full。剩余工作量分档及 500ms 补位组合未取得完整
JCT、全程输出吞吐同时改善，暂不推广，也不因此启动其他 baseline。
原 Full 与 ThunderAgent acting-decay 同时开启原生 mixed chunk 的配对已完成。
FULL 相对该轮 Thunder 的 JCT/吞吐改善 3.2445%/2.4997%，但相对原 mixed-off
FULL 的 JCT/吞吐均回退，保留 mixed-off。这是共同 serving 参数验证，
不将其包装为 predictive 调度创新，整体明确/重复获益目标仍未达到。

## 数据与开发验证

用户允许在开发阶段放宽项目、任务隔离，并在不足时增加 native 采集。
已完成两批独立 native 执行：8 条不同任务及 6 条匹配当前执行提示的任务。
现有划分为 34 条训练、22 条校准、18 条开发评估 workflow。
其中训练含历史 V174 的 24 条、八任务采集中的 4 条及匹配采集的 6 条；
校准含历史 V171 的 18 条及八任务采集的另外 4 条。

此划分允许同项目、同任务的不同完整执行，不能声称未见项目泛化。
同次执行的快照不拆散到训练与评估，原始事件文件哈希标识执行。
当前评估已经参与多轮开发决策，因此是开发评估，后续正式结论需要新的
独立执行。此时不再为满足严格项目隔离而额外消耗 GPU 收集数据。

## 完整结果

三个结果均保留相同的 18 个不同项目、3,674 请求、145,565,966 输入 token、
549,671 输出 token、原生停止条件及全部正常长尾。到达为 10 条初始加
8 条均值 120 秒的泊松到达，种子 21；chunk 2048，mixed 关闭，
running/decode 上限 24，Device 805,198 token、Host 200GB。

| 方法 | 平均 JCT / 秒 | Makespan / 秒 | 全程输出 token/s |
| --- | ---: | ---: | ---: |
| ThunderAgent acting-decay | 1504.072304 | 2305.899187 | 238.375989 |
| 原 2 秒 Full | 1439.457324 | 2253.756840 | 243.890996 |
| Workflow 分档加补位 Full | 1432.302431 | 2274.218978 | 241.696602 |

新组合相对原 Full 的 JCT 改善 0.4971%，吞吐下降 0.8997%。
这是不同配置各一次执行，不是重复试验或严格模块归因。
完整结果、fresh drain、实际 owned exit 和图表已核对；保留负结果。

结果入口：

- `experiments/reports/workflow_work_training_20261029/workflow_dispatch_r1/review_worker_r1/`
- `candidate_vs_original.json`：与原 Full 的完整比较。
- `complete_dispatch_service.json`：159 次 grant 对应 159 个不同请求尝试，
  全部找到 grant 之后的实际原生服务及完成；P50 150.15ms，P90 707.62ms，
  最大 17.04 秒。这些不是独占因果效果或可消除 JCT。
- `complete_periodic_selection.json`：1,132 次完整控制中仅 19 次局部集合变化。

## Prefill 与 Decode 间隔证据

新的只读审计识别同一逻辑请求两次实际正 decode 进展之间的 prefill，
并排除该请求已在中间 prefill/mixed 批次得到服务的情况。
只取 worker 区间并集，保留前后无 decode 边界的覆盖计数。

| 方法 | 有间隔的 prefill 段数 | 总 prefill worker 秒 | 位于上述间隔的 prefill worker 秒 |
| --- | ---: | ---: | ---: |
| ThunderAgent | 2688 | 434.144858 | 349.784501 |
| 原 Full | 2854 | 386.068754 | 305.164732 |
| 新组合 Full | 2869 | 384.886960 | 304.991335 |

最长 decode 边界间隔约 20 秒。两个首服务等待约 17 秒的请求，其窗口
分别包含约 15.4–15.6 秒 prefill worker 区间；不能据此称其一直驻留、
一直已准入、纯等待 KV 或存在同等可消除 JCT。D2H 的 submit→ACK 包络也
不是暴露拷贝时延。Mixed chunk 可能增加算力、容量及控制成本，需要实测。

## 新配对的边界

`configs/experiments/mixed_chunk_20261029/` 从原 2 秒 Full 和当前完整
ThunderAgent 配置派生，仅将共同 `ENABLE_MIXED_CHUNK` 从 0 改为 1。
原 Full 不开启 workflow 分档或 500ms 补位；ThunderAgent 仍采用作者的
5 秒完整控制及 1 秒 acting 衰减半衰期。工具预取、普通 frontier 预取、
逐层物理 handoff 保持关闭；JOIN/EOS、预测工具卸载、prepare_host、
sibling 偏好及 demand pipeline 保留原有各方法边界。

双方分别进行新源绑定的 24 请求 token/session/native-stop GPU 检查、
实际 1/8/24 sampler 测量，然后顺序执行完整 ThunderAgent、Full。
Mixed 小样本先等待两次新鲜 idle telemetry，再清理 owned 进程。
检查必须观察到 prefill 批次内 decode 成员产生正输出，并使所有模式的
输出序号和每条 exact/native-sampled 回执总数守恒；正式注册重算并封存
这些 journal，不能只靠开关或旧 mixed-off smoke 放行。

新的排队入口复用已验证的顺序控制及完整比较 reader，失败即停止，
不自动重跑或继续下一臂。每次 GPU 启动重查预约、实际资源和前驱退出；
不重采 native 性能基线，不改变工作量、工具时间或正常尾部。
完整 mixed 性能仍待验证；最新 GPU 资格状态见后文。

## 当前 CPU 验证

`experiments/reports/mixed_chunk_20261029/cpu_r1/evidence.json` 记录一次实际
源绑定验证：5,064 个测试及 6 个子测试通过，无失败、跳过或错误；保留
上一轮全部 5,008 个测试身份，绑定 2,016 个输入文件，原生源码保持不变。
独立启动前检查同时确认原 Full、ThunderAgent 和新组合均完成相同工作，
且 owned 进程实际退出，两个原生树及 patch 字节可严格重绑定。
这些 CPU 和历史完整性检查不构成新 mixed GPU 或性能结论。

## R1 小样本失败与记账修复

冻结 `db974231` 的 R1 检查完成全部 24 个 exact/session/native-stop 请求，
实际观察到 286 个含 decode 成员的 prefill 批次及其中 422 个 decode 输出。
GPU 检查仍然失败：24 条已完成请求的在途重叠描述缺少输出起止序号，
其中 8 条位于 prefill、16 条位于 decode。原有 telemetry 在 `_completed`
处提前跳过，因此只留下默认 delta=0，没有写入终态输出序号。

独立只读 `failure_review_r1.json` 将这些描述绑定到先前的同请求原生完成，
已记录输出总量与实际回执相同。这个诊断不补写原日志、不改变原失败状态，
也不把旧小样本升级为通过。原队列、position watcher、server 及 GPU 进程均
实际退出，fresh drain 有效，正式 ThunderAgent 和 Full 均未启动。

修复将已完成判断移到输出观察之后，继续读取实际 `output_ids` 数量，
为零新增输出写入准确序号，仍只产生一次完成事件；异常新增输出也不会被
跳过或伪造成零。两个原代码回归测试先实际失败，修复后相关 265 项通过。
源绑定完整 CPU 及新 R2 GPU 检查仍待执行，失败 R1 保留。
独立重试使用显式 `--attempt 2` 及全新路径，不重启原有失败执行。

修复后 `cpu_r2/evidence.json` 已实际通过 5,066 个测试及 6 个子测试，
保留 R1 全部 5,064 个测试身份，无失败、错误、跳过；2,016 个源绑定输入及
原生源码在执行期间未变化。双方采用相同修复后的 telemetry；该次 CPU
通过仍不等于 R2 GPU、完整性能或总体目标通过。

## R2 GPU 资格与正式执行

修复提交 `e3a7e5f1` 的 R2 实际通过 24 条 exact token/session/native-stop
请求，全部原生停止。473 个真实 forward 的 1,043 个输出 token 守恒；
285 个 prefill 批次含 422 个 decode 成员观察，产生 415 个真实 decode
输出。正式注册入口重新读取原始 journal 并通过守恒检查，未放松门槛。
当前原生 1/8/24 sampler 测量通过，小样本 fresh drain、模型/position
进程实际退出已独立核对。完整证据是 `position_complete_review_r2.json`。

`ops/mixed_chunk_thunder_20261029_r2.registration.json` 已实际启动完整
ThunderAgent acting-decay，使用同一冻结提交和 common 原生树；queue_r2
在其完整成功、fresh drain 和实际 owned exit 后才启动 Full 的新资格检查
及完整执行。后续两臂现均完整结束，结果见下文；总体目标仍未通过。
R1 的失败和独立诊断保留，不与 R2 合并为一次成功执行。

`live_start_r2.json` 独立核对实际 mixed=true/chunk2048/running24/seed21、
paper 运行配置、nice0 的 watcher/server/driver/scheduler、唯一 owned GPU
上下文和最初 51 条 exact 完成回执。这些是启动前缀，不能当作完整工作量、
JCT 或吞吐结论。

## 完整回放前缀与后续审计

新增只读 `scripts/audit_mixed_output_prefix.py` 按原生 forward 时间边界
核对输出序号、原生完成事件和客户端 exact/native-sampled 回执。仍在执行、
尚未收到客户端回执、晚于该 forward 边界的事件分别计数；不得把前缀标为
完整守恒。完整模式还要求所有原生完成、客户端完成及输出请求集合一致。

两个独立封存的 Thunder 前缀分别验证 1,033 和 3,144 条共同完成回执。
后者包含 3,146 个已观察请求、479,422 个输出 token、3,622 个混合 prefill
forward 及其中 26,002 个 decode 输出；9 个较晚原生完成和 12 个边界之外
回执保留为覆盖差异。这些计数不是同一时刻的原子状态或完整性能结果。

另外一个独立 decode 间隔前缀观察到 12,250 次持续 decode 前后边界观察，
全部在中间的 prefill 段获得服务，quiet gap 为零。此处是重复服务观察，
不是 12,250 条独立请求。没有把 400.36 秒 prefill worker 区间当作可消除
JCT，也不能直接与旧完整运行的区间总量比较。后续原生状态样本显示
Host FULL 池高水位达到 200GB，已有 467 次 Host-hit 请求及大量真实恢复；
不能把这个较晚状态与上述封存前缀拼成原子观测。

`complete_after_pair.py` 为 CPU 专用的后处理入口，只在原 queue 完整配对
评审完成、queue watcher 实际退出且两臂 exact work/fresh drain/owned exit
重新通过后执行。它顺序检查两臂完整混合输出、decode 间隔及共同 JCT 路径，
再核对 Full 的原生恢复字节/后续服务、JOIN 预取、sibling 和 EOS。
它不启动任何 GPU 工作，不自动重试失败，也不覆盖旧报告。
显式检查工具/普通预取和物理 handoff 仍为关闭；允许的提前恢复来源是
`join_ticket`。相关 55 个 CPU 测试已通过，运行中的冻结 GPU 源码未变化。

## R2 完整配对

两臂保留全部 18 个 workflow、3,674 请求、145,565,966 输入和 549,671 输出
token，原生停止、两次新鲜 idle 状态及实际 owned 进程退出均通过。
三张完整吞吐趋势、分布和 batch/context 图已逐张查看。

| 方法 | Mixed | 平均 JCT / 秒 | Makespan / 秒 | 全程输出 token/s |
| --- | --- | ---: | ---: | ---: |
| 原 ThunderAgent acting-decay | 关 | 1504.072304 | 2305.899187 | 238.375989 |
| 原 2 秒 FULL | 关 | 1439.457324 | 2253.756840 | 243.890996 |
| 本轮 ThunderAgent acting-decay | 开 | 1525.197388 | 2335.372979 | 235.367543 |
| 本轮原 2 秒 FULL | 开 | 1475.712067 | 2278.419409 | 241.251017 |

本轮 FULL 相比本轮 Thunder 的 JCT 降低 3.2445%，输出吞吐提高 2.4997%。
但新 FULL 平均 JCT 比原 FULL 多 36.254743 秒，输出吞吐也降低。控制臂
同样回退。只有各配置一次完整执行，且应用修订包含已披露的遥测修复，
不能将其作为 isolated mixed ablation 或预测调度收益。

完整后处理 `mechanism_review_r2/registration.state.json` 已通过全部九项
CPU 审计，未启动任何新 GPU 工作：

- 两臂全部 549,671 输出均有连续序号并与 exact 原生回执守恒。
- FULL 的 4,128 个 mixed prefill forward 产生 30,522 个 decode 输出，
  Thunder 的 3,986 个产生 27,208 个；持续 decode 的 quiet gap 为零。
- FULL prefill/mixed worker 区间合计 505.348876 秒，普通 decode 区间
  1492.999959 秒。原 FULL prefill 为 386.068754 秒、decode 为
  1575.881254 秒；不能把 mixed 整段同时加在 prefill 和 decode 上。
- 所有 479 个需求恢复 stage、682 个成员均验证原生 ACK 和后续实际服务，
  2,482,415,665,152 个 untagged H2D 字节完整核对，无未解释余量。
- 物理 handoff 为零；提前恢复来源只有 `join_ticket`，工具与普通预取关闭。

FULL 当前 H2D/D2H 为 2724.139893/1006.907130 GB，原 FULL 为
2659.224748/939.598381 GB，迁移并未减少。以上区间是运行时/等待在内的
worker 观测，字节是实际传输量，均不能单独归因内核、控制 CPU 或可消除 JCT。
完整图表和机器可读证据位于本实验的 `queue_r2/complete_review/` 与
`mechanism_review_r2/`，不新增主实验版本。没有下一项 GPU 工作排队。
